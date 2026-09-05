"""A small, read-only DeepSeek agent loop for workflow analysis."""

from __future__ import annotations

import json
import os
import re
from pathlib import PureWindowsPath
from typing import Any, Callable
from urllib.parse import urlparse

import aiohttp


_SECRET_KEY = re.compile(r"(api[_-]?key|token|secret|password|authorization)", re.I)
_SECRET_VALUE = re.compile(r"\b(?:sk|hf)_[A-Za-z0-9_-]{12,}\b|\bsk-[A-Za-z0-9_-]{12,}\b")
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


class AgentResponseFormatError(RuntimeError):
    """The provider answered, but did not honour the JSON-only contract."""


def settings(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve one agent call's configuration without persisting the secret."""
    overrides = overrides or {}
    if not isinstance(overrides, dict):
        raise ValueError("智能体配置格式无效")
    key = str(overrides.get("api_key") or os.environ.get("COMFYUI_WORKFLOW_DOCTOR_API_KEY", os.environ.get("COMFYUI_WORKFLOW_AGENT_API_KEY", ""))).strip()
    base_url = str(overrides.get("base_url") or os.environ.get("COMFYUI_WORKFLOW_DOCTOR_BASE_URL", os.environ.get("COMFYUI_WORKFLOW_AGENT_BASE_URL", "https://api.deepseek.com"))).strip().rstrip("/")
    model = str(overrides.get("model") or os.environ.get("COMFYUI_WORKFLOW_DOCTOR_MODEL", os.environ.get("COMFYUI_WORKFLOW_AGENT_MODEL", "deepseek-v4-flash-vision-exp"))).strip()
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("智能体地址必须是无账号信息的 HTTPS API 根地址")
    if parsed.port not in (None, 443):
        raise ValueError("智能体地址不能使用自定义端口")
    if key and not 12 <= len(key) <= 512:
        raise ValueError("智能体密钥长度无效")
    if not model or len(model) > 200:
        raise ValueError("模型名称无效")
    return {
        "configured": bool(key),
        "api_key": key,
        "base_url": base_url,
        "model": model,
    }


def _scrub(value: Any, key: str = "", depth: int = 0) -> Any:
    if depth > 12:
        return "<omitted>"
    if _SECRET_KEY.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): _scrub(v, str(k), depth + 1) for k, v in list(value.items())[:300]}
    if isinstance(value, list):
        return [_scrub(item, "", depth + 1) for item in value[:500]]
    if isinstance(value, str):
        cleaned = _SECRET_VALUE.sub("<redacted>", value)
        if _WINDOWS_PATH.match(cleaned):
            return f"<local-path>/{PureWindowsPath(cleaned).name}"
        return cleaned[:4000]
    return value


def compact_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    nodes = []
    for raw in (workflow.get("nodes") or [])[:300]:
        if not isinstance(raw, dict):
            continue
        nodes.append(
            _scrub(
                {
                    "id": raw.get("id"),
                    "type": raw.get("type"),
                    "title": raw.get("title"),
                    "pos": raw.get("pos"),
                    "size": raw.get("size"),
                    "mode": raw.get("mode"),
                    "inputs": raw.get("inputs"),
                    "outputs": raw.get("outputs"),
                    "widgets_values": raw.get("widgets_values"),
                }
            )
        )
    return {
        "version": workflow.get("version"),
        "nodes": nodes,
        "links": _scrub((workflow.get("links") or [])[:1200]),
        "groups": _scrub((workflow.get("groups") or [])[:100]),
    }


def _parse_json_result(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    candidates = [text]
    # Providers occasionally add one sentence before/after a valid object.
    # Try every balanced object rather than blindly slicing from the first
    # brace to the last one, which made a malformed answer surface as a raw
    # JSON decoder error in the browser.
    depth, start, quoted, escaped = 0, None, False, False
    for index, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : index + 1])
                start = None
    parsed = None
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                parsed = value
                break
        except json.JSONDecodeError:
            continue
    if parsed is None:
        raise AgentResponseFormatError("智能体没有按要求返回完整 JSON")
    if not isinstance(parsed, dict):
        raise ValueError("AI 返回内容不是 JSON 对象")
    parsed.setdefault("summary", "")
    parsed.setdefault("risk_level", "unknown")
    parsed.setdefault("findings", [])
    parsed.setdefault("recommended_actions", [])
    return parsed


def _format_retry_message() -> dict[str, str]:
    return {
        "role": "user",
        "content": "上一条最终回答不是可解析的 JSON。不要解释、不要使用 Markdown、不要调用工具；请只重写一个完整 JSON 对象，并严格使用系统提示中要求的字段。",
    }


def _completion_payload(
    *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: str, temperature: float, max_tokens: int
) -> dict[str, Any]:
    """Build one compatible, non-thinking tool-call request.

    The selected DeepSeek vision endpoint rejects this tool protocol while
    thinking is enabled.  Keeping tool turns non-thinking also ensures no
    chain-of-thought needs to be retained or sent back in later turns.
    """
    return {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        "thinking": {"type": "disabled"},
        "temperature": temperature,
        "max_tokens": max_tokens,
    }


async def _post_deepseek_json(
    session: aiohttp.ClientSession, url: str, headers: dict[str, str], payload: dict[str, Any]
) -> dict[str, Any]:
    """Send a small provider request, retrying one transient connector failure."""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            async with session.post(url, headers=headers, json=payload) as response:
                raw = await response.text()
                if response.status >= 400:
                    raise RuntimeError(f"DeepSeek API 返回 {response.status}: {raw[:500]}")
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("DeepSeek API 返回了无法解析的响应") from exc
                if not isinstance(data, dict):
                    raise RuntimeError("DeepSeek API 返回格式无效")
                return data
        except RuntimeError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            last_error = exc
            if attempt == 0:
                continue
    raise RuntimeError("无法连接 DeepSeek API。请在“设置”页点击“测试连接”检查网络、地址和模型。") from last_error


async def test_connection(config: dict[str, Any] | None = None) -> dict[str, str]:
    """Validate endpoint, key and selected model without reading a workflow or saving settings."""
    config = settings(config)
    if not config["configured"]:
        raise RuntimeError("请先填写 API Key")
    timeout = aiohttp.ClientTimeout(total=45, connect=15, sock_read=30)
    headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
    payload = {
        "model": config["model"],
        "messages": [{"role": "user", "content": "仅回复：连接成功"}],
        "thinking": {"type": "disabled"},
        "temperature": 0,
        "max_tokens": 16,
    }
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _post_deepseek_json(session, config["base_url"] + "/chat/completions", headers, payload)
    content = str((((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")).strip()
    return {"model": config["model"], "message": content[:120] or "连接成功"}


async def run_analysis_agent(
    workflow: dict[str, Any],
    report: dict[str, Any],
    installed_node_types: list[str] | None = None,
    screenshot: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = settings(config)
    if not config["configured"]:
        raise RuntimeError("尚未配置 COMFYUI_WORKFLOW_DOCTOR_API_KEY")

    compact = compact_workflow(workflow)
    installed = sorted(set(installed_node_types or []))[:2000]
    tool_data: dict[str, Callable[[], Any]] = {
        "inspect_workflow": lambda: compact,
        "inspect_rule_findings": lambda: _scrub(report),
        "inspect_installed_node_types": lambda: installed,
    }
    tools = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
        for name, description in (
            ("inspect_workflow", "读取当前 ComfyUI 工作流的节点、连接和参数。"),
            ("inspect_rule_findings", "读取本地确定性分析器发现的问题。"),
            ("inspect_installed_node_types", "读取本机已经安装的 ComfyUI 节点类型。"),
        )
    ]
    system = (
        "你是 ComfyUI 工作流审查 Agent。先调用工具取得事实，再给建议。"
        "不要声称执行了修改，不要建议绕过许可或安全检查。"
        "最终只输出 JSON 对象，字段为 summary、risk_level、findings、recommended_actions。"
        "findings 每项包含 severity、title、detail、node_ids；recommended_actions 每项包含 kind、title、reason、node_ids。"
        "kind 只能是 layout、workflow_edit、model_search、manual。使用中文，节点 ID 必须来自工具结果。"
    )
    user_text = (
        "请分析当前工作流的结构、兼容性、重复加载、可维护性和排版。"
        "如有截图，再检查可读性和视觉重叠；结构化工作流数据优先于截图。"
    )
    if screenshot:
        user_content: Any = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": screenshot, "detail": "low"}},
        ]
    else:
        user_content = user_text
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]

    timeout = aiohttp.ClientTimeout(total=150, connect=20, sock_read=120)
    headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
    url = config["base_url"] + "/chat/completions"
    format_retries = 0
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for turn in range(6):
            payload = _completion_payload(
                model=config["model"], messages=messages, tools=tools,
                tool_choice="required" if turn == 0 else "auto", temperature=0.2, max_tokens=3000,
            )
            data = await _post_deepseek_json(session, url, headers, payload)
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError("DeepSeek API 没有返回候选结果")
            message = choices[0].get("message") or {}
            assistant_message = {"role": "assistant", "content": message.get("content")}
            if message.get("reasoning_content") is not None:
                assistant_message["reasoning_content"] = message.get("reasoning_content")
            calls = message.get("tool_calls") or []
            if calls:
                assistant_message["tool_calls"] = calls
            messages.append(assistant_message)
            if not calls:
                try:
                    return _parse_json_result(message.get("content") or "")
                except AgentResponseFormatError:
                    if format_retries >= 1:
                        raise RuntimeError("智能体连续两次未返回有效计划，请稍后重试。")
                    format_retries += 1
                    messages.append(_format_retry_message())
                    continue
            for call in calls:
                function = call.get("function") or {}
                name = function.get("name")
                if name not in tool_data:
                    output = {"error": "未知或未授权工具"}
                else:
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                        if arguments:
                            raise ValueError("该只读工具不接受参数")
                        output = tool_data[name]()
                    except Exception as exc:  # tool errors are data, not agent crashes
                        output = {"error": str(exc)}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(output, ensure_ascii=False, separators=(",", ":")),
                    }
                )
    raise RuntimeError("Agent 达到最大工具调用轮数，未能生成最终报告")


async def _run_planner(
    *,
    system: str,
    user_text: str,
    tool_data: dict[str, Any],
    screenshot: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Small constrained tool loop shared by repair and layout planning."""
    config = settings(config)
    if not config["configured"]:
        raise RuntimeError("尚未配置 COMFYUI_WORKFLOW_DOCTOR_API_KEY")
    tools = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
        for name, (description, _value) in tool_data.items()
    ]
    content: Any = user_text
    if screenshot:
        content = [{"type": "text", "text": user_text}, {"type": "image_url", "image_url": {"url": screenshot, "detail": "low"}}]
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    timeout = aiohttp.ClientTimeout(total=180, connect=20, sock_read=135)
    headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
    format_retries = 0
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for turn in range(7):
            data = await _post_deepseek_json(
                session,
                config["base_url"] + "/chat/completions",
                headers,
                _completion_payload(
                    model=config["model"], messages=messages, tools=tools,
                    tool_choice="required" if turn == 0 else "auto", temperature=0.1, max_tokens=4200,
                ),
            )
            message = ((data.get("choices") or [{}])[0]).get("message") or {}
            calls = message.get("tool_calls") or []
            assistant = {"role": "assistant", "content": message.get("content")}
            if calls:
                assistant["tool_calls"] = calls
            messages.append(assistant)
            if not calls:
                try:
                    return _parse_json_result(message.get("content") or "")
                except AgentResponseFormatError:
                    if format_retries >= 1:
                        raise RuntimeError("智能体连续两次未返回有效计划，请稍后重试。")
                    format_retries += 1
                    messages.append(_format_retry_message())
                    continue
            for call in calls:
                name = (call.get("function") or {}).get("name")
                output: Any = {"error": "未知或未授权工具"}
                if name in tool_data:
                    try:
                        arguments = json.loads((call.get("function") or {}).get("arguments") or "{}")
                        if arguments:
                            raise ValueError("该只读工具不接受参数")
                        output = _scrub(tool_data[name][1])
                    except Exception as exc:
                        output = {"error": str(exc)}
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(output, ensure_ascii=False)})
    raise RuntimeError("Agent 达到最大工具调用轮数，未能生成最终计划")


async def run_repair_planner(
    workflow: dict[str, Any], report: dict[str, Any], environment: dict[str, Any], node_resolution: list[dict[str, Any]], model_report: dict[str, Any], config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = {
        "inspect_workflow": ("读取脱敏后的工作流结构。", compact_workflow(workflow)),
        "inspect_diagnostics": ("读取确定性诊断结论。", report),
        "inspect_environment": ("读取本机节点、硬件和版本事实。", environment),
        "inspect_missing_nodes": ("读取 Manager 节点包候选和本地替代候选。", node_resolution),
        "inspect_models": ("读取智能模型需求、依赖包和本地候选。", model_report),
    }
    system = (
        "你是神都猫 ComfyUI 工作流助手的修复规划 Agent。先读取事实工具，再输出严格 JSON。"
        "你只能提出计划，不能声称已安装、下载或修改。对每个缺失节点优先判断安装原包、使用本地替代、重建、绕过或人工判断。"
        "最终 JSON 必须包含 summary、risk_level、findings、recommended_actions、node_actions、model_actions、operations。"
        "node_actions 的 action 只能是 install_exact、replace_local、rebuild_subgraph、bypass_optional、manual_review、unresolved。"
        "operations 只能使用 replace_node、add_node、remove_optional_node、connect、disconnect、set_widget，且只能引用事实中的节点。使用中文。"
    )
    return await _run_planner(system=system, user_text="请为当前工作流生成可解释、可确认、可撤销的修复计划。", tool_data=data, config=config)


async def run_layout_planner(workflow: dict[str, Any], report: dict[str, Any], environment: dict[str, Any], screenshot: str | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    data = {
        "inspect_workflow": ("读取脱敏后的工作流结构。", compact_workflow(workflow)),
        "inspect_diagnostics": ("读取当前结构与可读性问题。", report),
        "inspect_environment": ("读取本机节点事实。", environment),
    }
    system = (
        "你是神都猫 ComfyUI 工作流助手的语义排版 Agent。先读取事实，再只输出 JSON。"
        "根据完整上下文把节点分配到动态语义阶段和泳道；阶段从左到右，组内从上到下，主链在上方。"
        "不要给出坐标，不要建议修改参数或连线。最终 JSON 必须含 summary、risk_level、findings、recommended_actions、groups。"
        "groups 是数组，每项含 node_id、stage_id(input/model/prompt/sampling/decode/output/utility)、stage_title、stage_purpose、lane_id、role、importance、editable。"
    )
    return await _run_planner(system=system, user_text="请理解当前工作流并生成中文分组与泳道计划。", tool_data=data, screenshot=screenshot, config=config)


async def run_model_source_planner(requirement: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn an opaque ComfyUI filename into repository-level search queries."""
    config = settings(config)
    if not config["configured"]:
        raise RuntimeError("请先在“设置”页配置并测试 DeepSeek")
    facts = _scrub({key: requirement.get(key) for key in ("expected", "role", "family", "node_type", "node_title", "widget_name", "upstream_node_types", "downstream_node_types")})
    system = (
        "你是 ComfyUI 模型来源检索 Agent。根据给出的缺失文件及节点上下文，推断它所属模型组件和可能仓库。"
        "不要编造下载链接、不要声称已找到文件。只输出 JSON 对象，字段为 summary、queries、official_repositories、reason。"
        "official_repositories 只在你能明确判断官方仓库时填写，例如 Comfy-Org/MiniMax-H3；否则为空数组。"
        "queries 必须是 1 到 3 个适合模型仓库搜索的短词，不得包含文件扩展名、不得包含完整哈希或本地路径；优先模型系列、组件角色和官方项目名称。使用中文说明。"
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": f"缺失模型事实：{json.dumps(facts, ensure_ascii=False)}"}]
    timeout = aiohttp.ClientTimeout(total=75, connect=20, sock_read=55)
    headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
    for retry in range(2):
        async with aiohttp.ClientSession(timeout=timeout) as session:
            data = await _post_deepseek_json(session, config["base_url"] + "/chat/completions", headers, {
                "model": config["model"], "messages": messages, "thinking": {"type": "disabled"}, "temperature": 0.1, "max_tokens": 500,
            })
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        try:
            parsed = _parse_json_result(content)
            queries = [str(value).strip() for value in parsed.get("queries") or [] if 2 <= len(str(value).strip()) <= 120]
            if not queries:
                raise AgentResponseFormatError("缺少可用查询词")
            repositories = [str(value).strip() for value in parsed.get("official_repositories") or [] if str(value).strip().lower().startswith("comfy-org/")]
            return {"summary": str(parsed.get("summary") or ""), "reason": str(parsed.get("reason") or ""), "queries": list(dict.fromkeys(queries))[:3], "official_repositories": list(dict.fromkeys(repositories))[:3]}
        except AgentResponseFormatError:
            if retry:
                raise RuntimeError("智能体未能生成有效的模型检索词，请稍后重试。")
            messages.append({"role": "assistant", "content": content})
            messages.append(_format_retry_message())
    raise RuntimeError("智能体未能生成模型检索词")
