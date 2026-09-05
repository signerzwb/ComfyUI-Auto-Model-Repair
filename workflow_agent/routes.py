"""HTTP API for 神都猫 ComfyUI 工作流助手."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .analyzer import analyze_workflow
from .deepseek import run_analysis_agent, run_layout_planner, run_model_source_planner, run_repair_planner, settings, test_connection
from .downloads import DownloadManager
from .environment import environment_snapshot, node_catalog, refresh_node_catalog
from .layout import build_layout_plan
from .manager import manager_catalog, manager_install_request, package_candidates, refresh_manager_catalog
from .model_intelligence import score_online_candidate, source_priority, summarize_model_requirements
from .patches import SnapshotStore, validate_patch_plan
from .providers import model_files, search_models
from .security import configured_mirror_bases, safe_model_filename, validate_download_url, validate_model_folder
from .substitutions import load_user_rules, resolve_missing_nodes, save_user_rule


_REGISTERED = False
_MAX_REQUEST_BYTES = 8 * 1024 * 1024
_MAX_SCREENSHOT_CHARS = 5 * 1024 * 1024
_PREFIX = "/workflow-doctor/v1"
_COMFY_ORG_REPOSITORIES = {"minimaxh3": "Comfy-Org/MiniMax-H3"}


def _json_response(web, data, status: int = 200):
    return web.Response(text=json.dumps(data, ensure_ascii=False), status=status, content_type="application/json")


async def _payload(request) -> dict:
    if request.content_length and request.content_length > _MAX_REQUEST_BYTES:
        raise ValueError("请求数据过大")
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("请求必须是 JSON 对象")
    return data


def _error(web, exc: Exception, status: int = 400):
    return _json_response(web, {"ok": False, "error": str(exc)}, status=status)


def _doctor_root(folder_paths) -> Path:
    try:
        root = Path(folder_paths.get_user_directory()) / "workflow_doctor"
    except Exception:
        root = Path(__file__).resolve().parents[1] / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _auth_headers(provider: str, url: str) -> dict[str, str]:
    host = (urlparse(url).hostname or "").lower()
    token_name = {"huggingface": "HF_TOKEN", "civitai": "CIVITAI_API_TOKEN", "modelscope": "MODELSCOPE_TOKEN"}.get(provider)
    token = os.environ.get(token_name, "").strip() if token_name else ""
    return {"Authorization": f"Bearer {token}"} if token and host else {}


async def _open_download(session, source_url: str, provider: str, range_start: int):
    current = validate_download_url(source_url)
    for _ in range(6):
        headers = {"User-Agent": "ShenDuMao-Workflow-Doctor/1.0", **_auth_headers(provider, current)}
        if range_start:
            headers["Range"] = f"bytes={range_start}-"
        response = await session.get(current, headers=headers, allow_redirects=False)
        if response.status in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.release()
            if not location:
                raise RuntimeError("下载站点返回了无目标重定向")
            current = validate_download_url(urljoin(current, location))
            continue
        return response
    raise RuntimeError("下载重定向次数过多")


def _mirror_sources(provider: str, source_url: str) -> list[dict[str, str]]:
    values = [{"label": "官方来源", "url": source_url}]
    if provider == "huggingface" and "huggingface.co/" in source_url:
        values.append({"label": "HF 国内镜像", "url": source_url.replace("https://huggingface.co/", "https://hf-mirror.com/", 1)})
    source_path = urlparse(source_url).path
    for base in configured_mirror_bases().get(provider, []):
        candidate = base.rstrip("/") + source_path
        if candidate not in {item["url"] for item in values}:
            values.append({"label": f"已配置镜像（{urlparse(base).hostname}）", "url": candidate})
    return values


def _validated_screenshot(value: object) -> str | None:
    if not value:
        return None
    if not isinstance(value, str) or not value.startswith("data:image/"):
        raise ValueError("截图格式无效")
    if len(value) > _MAX_SCREENSHOT_CHARS:
        raise ValueError("截图过大，请关闭视觉分析后重试")
    return value


def _safe_install_request(candidate: dict) -> dict:
    return {
        "title": candidate.get("title"),
        "reference": candidate.get("reference"),
        "request": manager_install_request(candidate),
        "notice": "仅在用户确认后由前端提交给 ComfyUI-Manager；安装后必须重启并重新验证。",
    }


async def _official_comfy_file(requirement: dict, repositories: list[str]) -> dict | None:
    """Return an official candidate only after its exact file is in the Hub tree."""
    expected = str(requirement.get("expected") or "")
    compact = "".join(char for char in expected.lower() if char.isalnum())
    repository = next((value for value in repositories if str(value).lower().startswith("comfy-org/")), None)
    if not repository:
        repository = next((repo for marker, repo in _COMFY_ORG_REPOSITORIES.items() if marker in compact), None)
    if not repository:
        return None
    filename = safe_model_filename(expected)
    manifest = await model_files("modelscope", repository)
    file = next((item for item in manifest.get("files") or [] if str(item.get("name") or "").rsplit("/", 1)[-1].lower() == filename.lower() and item.get("safe") and item.get("download_url")), None)
    if not file:
        return None
    folder = str(file.get("suggested_folder") or requirement.get("role") or "checkpoints")
    return {
        "provider": "modelscope", "id": repository, "name": repository, "type": "官方 ComfyUI 模型仓库", "downloads": 0, "likes": 0,
        "compatible": True, "compatibility_score": 100, "reasons": ["已定位到 Comfy-Org 官方仓库及对应组件目录。"],
        "source_rank": 0, "source_label": "魔塔 · Comfy-Org 官方发布",
        "direct_file": file,
    }


async def _verified_file_candidate(requirement: dict, candidate: dict) -> dict | None:
    """Keep a model search result only when its repository contains the needed file.

    Repository titles and workflow names are deliberately not treated as proof:
    a single unrelated CivitAI workflow otherwise looked more relevant than the
    actual MiniMax-H3 VAE simply because its title contained both terms.
    """
    try:
        expected = safe_model_filename(str(requirement.get("expected") or "")).lower()
        manifest = await model_files(str(candidate.get("provider") or ""), str(candidate.get("id") or ""))
        file = next((item for item in manifest.get("files") or [] if str(item.get("name") or "").rsplit("/", 1)[-1].lower() == expected and item.get("safe") and item.get("download_url")), None)
        if not file:
            return None
        verified = score_online_candidate(requirement, candidate, environment_snapshot())
        verified["compatible"] = True
        verified["compatibility_score"] = 100
        verified["direct_file"] = file
        verified["reasons"] = list(verified.get("reasons") or []) + ["仓库文件清单已精确命中缺失文件。"]
        return verified
    except Exception:
        # An unavailable catalog is not evidence that a third-party title is
        # correct.  Omit it and leave a transparent provider error below.
        return None


def register_routes() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        import aiohttp
        import folder_paths
        from aiohttp import web
        from server import PromptServer
    except ImportError:
        return
    routes = PromptServer.instance.routes
    download_manager = DownloadManager(folder_paths)

    @routes.get(f"{_PREFIX}/status")
    async def status(_request):
        config = settings()
        return _json_response(web, {"ok": True, "name": "神都猫 ComfyUI 工作流助手", "version": "1.0.0", "ai_configured": config["configured"], "model": config["model"], "providers": ["huggingface", "civitai", "modelscope"], "manager": manager_catalog().get("available", False)})

    @routes.get(f"{_PREFIX}/environment")
    async def environment(request):
        return _json_response(web, {"ok": True, "data": environment_snapshot(include_catalog=request.query.get("catalog") == "1")})

    @routes.post(f"{_PREFIX}/environment/refresh")
    async def refresh_environment(_request):
        refresh_node_catalog()
        refresh_manager_catalog()
        return _json_response(web, {"ok": True, "data": environment_snapshot()})

    @routes.post(f"{_PREFIX}/workflow/analyze")
    async def workflow_analyze(request):
        try:
            data = await _payload(request)
            return _json_response(web, {"ok": True, "data": analyze_workflow(data.get("workflow"), node_catalog())})
        except Exception as exc:
            return _error(web, exc)

    @routes.post(f"{_PREFIX}/workflow/validate")
    async def workflow_validate(request):
        try:
            data = await _payload(request)
            report = analyze_workflow(data.get("workflow"), node_catalog())
            return _json_response(web, {"ok": True, "valid": not any(item["severity"] == "error" for item in report["findings"]), "data": report})
        except Exception as exc:
            return _error(web, exc)

    @routes.post(f"{_PREFIX}/nodes/resolve-packages")
    async def nodes_resolve(request):
        try:
            data = await _payload(request)
            return _json_response(web, {"ok": True, "data": resolve_missing_nodes(data.get("workflow"), node_catalog()), "manager_available": manager_catalog().get("available", False)})
        except Exception as exc:
            return _error(web, exc)

    @routes.post(f"{_PREFIX}/nodes/find-substitutes")
    async def nodes_substitutes(request):
        try:
            data = await _payload(request)
            resolved = resolve_missing_nodes(data.get("workflow"), node_catalog())
            return _json_response(web, {"ok": True, "data": [{"node_type": item["node_type"], "candidates": item["local_substitutes"]} for item in resolved]})
        except Exception as exc:
            return _error(web, exc)

    @routes.get(f"{_PREFIX}/nodes/substitution-rules")
    async def rules_get(_request):
        return _json_response(web, {"ok": True, "data": load_user_rules(_doctor_root(folder_paths))})

    @routes.post(f"{_PREFIX}/nodes/substitution-rules")
    async def rules_post(request):
        try:
            data = await _payload(request)
            return _json_response(web, {"ok": True, "data": save_user_rule(_doctor_root(folder_paths), data.get("rule") or {})})
        except Exception as exc:
            return _error(web, exc)

    @routes.post(f"{_PREFIX}/node-packs/install-plan")
    async def node_pack_plan(request):
        try:
            data = await _payload(request)
            candidates = package_candidates(str(data.get("node_type") or ""))
            if not candidates:
                raise ValueError("没有找到可由 Manager 安装的节点包")
            return _json_response(web, {"ok": True, "data": [_safe_install_request(candidate) for candidate in candidates]})
        except Exception as exc:
            return _error(web, exc)

    @routes.post(f"{_PREFIX}/models/scan")
    async def model_scan(request):
        try:
            data = await _payload(request)
            return _json_response(web, {"ok": True, "data": summarize_model_requirements(data.get("workflow"), node_catalog())})
        except Exception as exc:
            return _error(web, exc)

    @routes.post(f"{_PREFIX}/models/search")
    async def model_search(request):
        try:
            data = await _payload(request)
            result = await search_models(data.get("query"), data.get("provider", "all"), data.get("limit", 12))
            requirement = data.get("requirement") if isinstance(data.get("requirement"), dict) else None
            if requirement:
                result["results"] = [score_online_candidate(requirement, item, environment_snapshot()) for item in result["results"]]
            for item in result["results"]:
                item["source_rank"], item["source_label"] = source_priority(item)
            result["results"].sort(key=lambda item: (not bool(item.get("compatible", True)), int(item.get("source_rank", 9)), -int(item.get("compatibility_score") or 0)))
            return _json_response(web, {"ok": True, "data": result})
        except Exception as exc:
            return _error(web, exc)

    @routes.post(f"{_PREFIX}/models/files")
    async def model_file_list(request):
        try:
            data = await _payload(request)
            result = await model_files(data.get("provider"), data.get("model_id"))
            for item in result.get("files", []):
                if item.get("download_url"):
                    item["sources"] = _mirror_sources(str(data.get("provider") or ""), item["download_url"])
            return _json_response(web, {"ok": True, "data": result})
        except Exception as exc:
            return _error(web, exc)

    @routes.post(f"{_PREFIX}/models/download")
    async def model_download(request):
        part_path = None
        try:
            data = await _payload(request)
            provider = str(data.get("provider") or "").lower()
            if provider not in {"huggingface", "civitai", "modelscope"}:
                raise ValueError("模型来源无效")
            source_url, folder_name = validate_download_url(data.get("download_url")), validate_model_folder(data.get("folder"))
            filename = safe_model_filename(data.get("filename"))
            expected_hash, expected_size = str(data.get("sha256") or "").lower().removeprefix("sha256:"), int(data.get("size_bytes") or 0)
            max_bytes = int(os.environ.get("COMFYUI_WORKFLOW_DOCTOR_MAX_DOWNLOAD_GB", "40")) * 1024**3
            if expected_size and expected_size > max_bytes:
                raise ValueError("模型文件超过当前下载大小限制")
            roots = folder_paths.get_folder_paths(folder_name)
            if not roots:
                raise RuntimeError("ComfyUI 没有配置目标模型目录")
            destination = Path(roots[0]).resolve() / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise FileExistsError(f"文件已存在：{filename}")
            part_path, resume_at = destination.with_name(destination.name + ".part"), 0
            if part_path.exists():
                resume_at = part_path.stat().st_size
            timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=180)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                response = await _open_download(session, source_url, provider, resume_at)
                async with response:
                    if response.status >= 400:
                        raise RuntimeError(f"下载站点返回 {response.status}: {(await response.text())[:300]}")
                    append = resume_at > 0 and response.status == 206
                    if not append:
                        resume_at = 0
                    declared = int(response.headers.get("Content-Length") or 0)
                    if declared and declared + resume_at > max_bytes:
                        raise ValueError("模型文件超过当前下载大小限制")
                    digest, written = hashlib.sha256(), resume_at
                    if append:
                        with part_path.open("rb") as handle:
                            for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                                digest.update(chunk)
                    with part_path.open("ab" if append else "wb") as handle:
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            written += len(chunk)
                            if written > max_bytes:
                                raise ValueError("模型文件超过当前下载大小限制")
                            digest.update(chunk)
                            handle.write(chunk)
            if expected_size and written != expected_size:
                raise RuntimeError(f"文件大小校验失败：期望 {expected_size}，实际 {written}")
            actual_hash = digest.hexdigest()
            if expected_hash and actual_hash != expected_hash:
                part_path.unlink(missing_ok=True)
                raise RuntimeError("SHA256 校验失败，临时文件已删除")
            os.replace(part_path, destination)
            return _json_response(web, {"ok": True, "data": {"filename": filename, "folder": folder_name, "size_bytes": written, "sha256": actual_hash}})
        except Exception as exc:
            return _error(web, exc, status=409 if isinstance(exc, FileExistsError) else 400)

    @routes.post(f"{_PREFIX}/models/downloads/enqueue")
    async def model_download_enqueue(request):
        try:
            data = await _payload(request)
            return _json_response(web, {"ok": True, "data": {"jobs": await download_manager.enqueue(data.get("jobs") or [])}})
        except Exception as exc:
            return _error(web, exc, status=409 if isinstance(exc, FileExistsError) else 400)

    @routes.post(f"{_PREFIX}/models/downloads/status")
    async def model_download_status(request):
        try:
            data = await _payload(request)
            return _json_response(web, {"ok": True, "data": {"jobs": download_manager.status(data.get("job_ids"))}})
        except Exception as exc:
            return _error(web, exc)

    @routes.post(f"{_PREFIX}/models/agent-search")
    async def model_agent_search(request):
        try:
            data = await _payload(request)
            requirement = data.get("requirement") if isinstance(data.get("requirement"), dict) else {}
            plan = await run_model_source_planner(requirement, data.get("agent_config"))
            seen, candidates, errors = set(), [], []
            try:
                official = await _official_comfy_file(requirement, plan.get("official_repositories") or [])
            except Exception as exc:
                official = None
                errors.append({"provider": "modelscope", "message": f"无法验证 Comfy-Org 官方文件清单：{str(exc)[:180]}"})
            if official:
                candidates.append(official)
                seen.add((official["provider"], official["id"]))
            raw_candidates = []
            for query in plan.get("queries", [])[:3]:
                result = await search_models(str(query), "all", 8)
                errors.extend(result.get("errors") or [])
                for item in result.get("results") or []:
                    identity = (str(item.get("provider")), str(item.get("id")))
                    if identity not in seen:
                        seen.add(identity)
                        raw_candidates.append(item)
            verified = await asyncio.gather(*(_verified_file_candidate(requirement, item) for item in raw_candidates), return_exceptions=False)
            candidates.extend(item for item in verified if item is not None)
            for item in candidates:
                item["source_rank"], item["source_label"] = source_priority(item)
            candidates.sort(key=lambda item: (not bool(item.get("compatible", True)), int(item.get("source_rank", 9)), -int(item.get("compatibility_score") or 0)))
            return _json_response(web, {"ok": True, "data": {"plan": plan, "results": candidates[:24], "errors": errors}})
        except Exception as exc:
            return _error(web, exc, status=502 if "DeepSeek" in str(exc) or "连接" in str(exc) else 400)

    @routes.post(f"{_PREFIX}/agent/analyze")
    async def agent_analyze(request):
        try:
            data = await _payload(request)
            report = analyze_workflow(data.get("workflow"), node_catalog())
            result = await run_analysis_agent(data.get("workflow"), report, list(node_catalog()), _validated_screenshot(data.get("screenshot")), data.get("agent_config"))
            return _json_response(web, {"ok": True, "data": result})
        except Exception as exc:
            return _error(web, exc, status=502 if "DeepSeek" in str(exc) else 400)

    @routes.post(f"{_PREFIX}/agent/test")
    async def agent_test(request):
        try:
            data = await _payload(request)
            return _json_response(web, {"ok": True, "data": await test_connection(data.get("agent_config"))})
        except Exception as exc:
            return _error(web, exc, status=502 if "DeepSeek" in str(exc) or "连接" in str(exc) else 400)

    @routes.post(f"{_PREFIX}/agent/plan-repair")
    async def agent_repair(request):
        try:
            data = await _payload(request)
            workflow, catalog = data.get("workflow"), node_catalog()
            result = await run_repair_planner(workflow, analyze_workflow(workflow, catalog), environment_snapshot(), resolve_missing_nodes(workflow, catalog), summarize_model_requirements(workflow, catalog), data.get("agent_config"))
            result["patch_validation"] = validate_patch_plan({"operations": result.get("operations", [])}, workflow, set(catalog))
            return _json_response(web, {"ok": True, "data": result})
        except Exception as exc:
            return _error(web, exc, status=502 if "DeepSeek" in str(exc) else 400)

    @routes.post(f"{_PREFIX}/agent/plan-layout")
    async def agent_layout(request):
        try:
            data = await _payload(request)
            workflow = data.get("workflow")
            agent = await run_layout_planner(workflow, analyze_workflow(workflow, node_catalog()), environment_snapshot(), _validated_screenshot(data.get("screenshot")), data.get("agent_config"))
            return _json_response(web, {"ok": True, "data": {"agent": agent, "layout": build_layout_plan(workflow, agent.get("groups") or [], data.get("mode", "semantic_rebuild"))}})
        except Exception as exc:
            return _error(web, exc, status=502 if "DeepSeek" in str(exc) else 400)

    @routes.post(f"{_PREFIX}/layout/plan")
    async def layout_plan(request):
        try:
            data = await _payload(request)
            return _json_response(web, {"ok": True, "data": build_layout_plan(data.get("workflow"), data.get("groups"), data.get("mode", "semantic_rebuild"))})
        except Exception as exc:
            return _error(web, exc)

    @routes.post(f"{_PREFIX}/patch/validate")
    async def patch_validate(request):
        try:
            data = await _payload(request)
            return _json_response(web, {"ok": True, "data": validate_patch_plan(data.get("plan") or {}, data.get("workflow") or {}, set(node_catalog()))})
        except Exception as exc:
            return _error(web, exc)

    @routes.post(f"{_PREFIX}/snapshots/create")
    async def snapshot_create(request):
        try:
            data = await _payload(request)
            return _json_response(web, {"ok": True, "data": SnapshotStore(_doctor_root(folder_paths)).create(data.get("workflow") or {}, str(data.get("label") or "操作前快照"))})
        except Exception as exc:
            return _error(web, exc)

    @routes.get(f"{_PREFIX}/snapshots")
    async def snapshot_list(_request):
        return _json_response(web, {"ok": True, "data": SnapshotStore(_doctor_root(folder_paths)).list()})

    @routes.get(f"{_PREFIX}/snapshots/{{snapshot_id}}")
    async def snapshot_get(request):
        try:
            return _json_response(web, {"ok": True, "data": SnapshotStore(_doctor_root(folder_paths)).get(request.match_info["snapshot_id"])})
        except Exception as exc:
            return _error(web, exc, status=404)

    @routes.post(f"{_PREFIX}/reports/create")
    async def report_create(request):
        try:
            data = await _payload(request)
            return _json_response(web, {"ok": True, "data": SnapshotStore(_doctor_root(folder_paths)).report(data)})
        except Exception as exc:
            return _error(web, exc)

    @routes.get(f"{_PREFIX}/reports/{{report_id}}")
    async def report_get(request):
        try:
            return _json_response(web, {"ok": True, "data": SnapshotStore(_doctor_root(folder_paths)).get_report(request.match_info["report_id"])})
        except Exception as exc:
            return _error(web, exc, status=404)

    _REGISTERED = True
