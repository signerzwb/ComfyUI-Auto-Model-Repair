"""Deterministic, offline analysis for ComfyUI workflow JSON."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable


_MODEL_LOADER_MARKERS = (
    "checkpointloader",
    "loadcheckpoint",
    "unetloader",
    "diffusionmodelloader",
    "vaeloader",
    "cliploader",
    "loraloader",
    "controlnetloader",
)


def _node_id(value: Any) -> str:
    return str(value)


def normalize_links(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize both legacy array links and Workflow 1.0 object links."""
    normalized: list[dict[str, Any]] = []
    for raw in workflow.get("links") or []:
        if isinstance(raw, (list, tuple)) and len(raw) >= 5:
            normalized.append(
                {
                    "id": raw[0],
                    "origin_id": raw[1],
                    "origin_slot": raw[2],
                    "target_id": raw[3],
                    "target_slot": raw[4],
                    "type": raw[5] if len(raw) > 5 else None,
                }
            )
        elif isinstance(raw, dict):
            origin = raw.get("origin_id", raw.get("originId"))
            target = raw.get("target_id", raw.get("targetId"))
            normalized.append(
                {
                    "id": raw.get("id"),
                    "origin_id": origin,
                    "origin_slot": raw.get("origin_slot", raw.get("originSlot")),
                    "target_id": target,
                    "target_slot": raw.get("target_slot", raw.get("targetSlot")),
                    "type": raw.get("type"),
                }
            )
    return normalized


def _finding(
    severity: str,
    code: str,
    title: str,
    detail: str,
    node_ids: Iterable[Any] = (),
    recommendation: str = "",
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "title": title,
        "detail": detail,
        "node_ids": [_node_id(item) for item in node_ids],
        "recommendation": recommendation,
    }


def _rect(node: dict[str, Any]) -> tuple[float, float, float, float] | None:
    pos = node.get("pos")
    size = node.get("size")
    if not isinstance(pos, (list, tuple)) or len(pos) < 2:
        return None
    if not isinstance(size, (list, tuple)) or len(size) < 2:
        return None
    try:
        x, y = float(pos[0]), float(pos[1])
        width, height = max(float(size[0]), 1.0), max(float(size[1]), 1.0)
    except (TypeError, ValueError):
        return None
    return x, y, x + width, y + height


def _overlap_pairs(nodes: list[dict[str, Any]], limit: int = 12) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    rects = [(node, _rect(node)) for node in nodes]
    for index, (left_node, left) in enumerate(rects):
        if left is None:
            continue
        for right_node, right in rects[index + 1 :]:
            if right is None:
                continue
            horizontal = min(left[2], right[2]) - max(left[0], right[0])
            vertical = min(left[3], right[3]) - max(left[1], right[1])
            if horizontal > 8 and vertical > 8:
                pairs.append((_node_id(left_node.get("id")), _node_id(right_node.get("id"))))
                if len(pairs) >= limit:
                    return pairs
    return pairs


def _estimate_depth(node_ids: set[str], links: list[dict[str, Any]]) -> int:
    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree = {node_id: 0 for node_id in node_ids}
    for link in links:
        origin = _node_id(link.get("origin_id"))
        target = _node_id(link.get("target_id"))
        if origin in node_ids and target in node_ids and origin != target:
            outgoing[origin].append(target)
            indegree[target] += 1
    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    depth = {node_id: 0 for node_id in node_ids}
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for target in outgoing[current]:
            depth[target] = max(depth[target], depth[current] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(node_ids):
        return max(depth.values(), default=0) + 1
    return max(depth.values(), default=0) + (1 if node_ids else 0)


def analyze_workflow(
    workflow: dict[str, Any], known_node_types: Iterable[str] | None = None
) -> dict[str, Any]:
    """Return a stable report without calling an external model."""
    if not isinstance(workflow, dict):
        raise ValueError("workflow must be a JSON object")
    nodes = workflow.get("nodes") or []
    if not isinstance(nodes, list):
        raise ValueError("workflow.nodes must be an array")

    findings: list[dict[str, Any]] = []
    links = normalize_links(workflow)
    known = set(known_node_types or [])
    node_by_id: dict[str, dict[str, Any]] = {}
    duplicate_ids: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            findings.append(
                _finding("error", "invalid_node", "节点数据损坏", "工作流包含非对象节点。")
            )
            continue
        identifier = _node_id(node.get("id"))
        if identifier in node_by_id:
            duplicate_ids.append(identifier)
        node_by_id[identifier] = node

    if not nodes:
        findings.append(
            _finding("info", "empty_workflow", "工作流为空", "当前画布还没有可分析的节点。")
        )
    if duplicate_ids:
        findings.append(
            _finding(
                "error",
                "duplicate_node_ids",
                "节点 ID 重复",
                f"发现 {len(duplicate_ids)} 个重复节点 ID，工作流加载或执行可能不稳定。",
                duplicate_ids,
                "重新创建这些节点，确保每个节点 ID 唯一。",
            )
        )

    missing_type = [node.get("id") for node in nodes if isinstance(node, dict) and not node.get("type")]
    if missing_type:
        findings.append(
            _finding(
                "error",
                "missing_node_type",
                "节点类型缺失",
                f"有 {len(missing_type)} 个节点没有类型信息。",
                missing_type,
                "删除损坏节点，或从原始工作流重新导入。",
            )
        )

    if known:
        unknown = [
            node.get("id")
            for node in nodes
            if isinstance(node, dict) and node.get("type") and node.get("type") not in known
        ]
        if unknown:
            findings.append(
                _finding(
                    "error",
                    "unknown_node_types",
                    "缺少自定义节点",
                    f"当前 ComfyUI 不认识 {len(unknown)} 个节点类型。",
                    unknown,
                    "先确认缺失节点的来源和版本，再通过 ComfyUI-Manager 安装。",
                )
            )

    degree = {identifier: 0 for identifier in node_by_id}
    dangling: list[Any] = []
    backward = 0
    very_long = 0
    for link in links:
        origin = _node_id(link.get("origin_id"))
        target = _node_id(link.get("target_id"))
        if origin not in node_by_id or target not in node_by_id:
            dangling.append(link.get("id"))
            continue
        degree[origin] += 1
        degree[target] += 1
        origin_rect = _rect(node_by_id[origin])
        target_rect = _rect(node_by_id[target])
        if origin_rect and target_rect:
            if origin_rect[0] > target_rect[0] + 40:
                backward += 1
            distance = abs(origin_rect[0] - target_rect[0]) + abs(origin_rect[1] - target_rect[1])
            if distance > 2200:
                very_long += 1

    if dangling:
        findings.append(
            _finding(
                "error",
                "dangling_links",
                "存在断开的连线",
                f"发现 {len(dangling)} 条连线指向不存在的节点。",
                recommendation="删除断线，或重新连接对应节点。",
            )
        )

    isolated = [identifier for identifier, count in degree.items() if count == 0]
    if isolated and len(nodes) > 1:
        findings.append(
            _finding(
                "warning",
                "isolated_nodes",
                "存在孤立节点",
                f"有 {len(isolated)} 个节点没有任何连接。它们可能是草稿，也可能是遗漏。",
                isolated[:30],
                "确认用途；不再需要的节点可以移到草稿区或删除。",
            )
        )

    overlaps = _overlap_pairs([node for node in nodes if isinstance(node, dict)])
    if overlaps:
        overlap_nodes = sorted({identifier for pair in overlaps for identifier in pair})
        findings.append(
            _finding(
                "warning",
                "overlapping_nodes",
                "节点发生重叠",
                f"检测到至少 {len(overlaps)} 组节点重叠。",
                overlap_nodes,
                "使用自动排版，随后检查分组和便签位置。",
            )
        )
    if backward >= 3:
        findings.append(
            _finding(
                "info",
                "backward_links",
                "工作流阅读方向不一致",
                f"有 {backward} 条连接从右向左返回，阅读和排错会比较困难。",
                recommendation="按执行方向重新排列为从左到右。",
            )
        )
    if very_long:
        findings.append(
            _finding(
                "info",
                "long_links",
                "存在过长连线",
                f"检测到 {very_long} 条跨越很远的连线。",
                recommendation="考虑重新排版、添加转接点或建立子图。",
            )
        )

    loader_uses: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or "")
        compact_type = node_type.lower().replace("_", "").replace(" ", "")
        if not any(marker in compact_type for marker in _MODEL_LOADER_MARKERS):
            continue
        widgets = node.get("widgets_values") or []
        model_name = str(widgets[0]) if isinstance(widgets, list) and widgets else ""
        if model_name:
            loader_uses[(node_type, model_name)].append(node.get("id"))
    repeated = {key: ids for key, ids in loader_uses.items() if len(ids) > 1}
    for (node_type, model_name), ids in list(repeated.items())[:8]:
        findings.append(
            _finding(
                "warning",
                "duplicate_model_load",
                "模型被重复加载",
                f"{model_name} 由 {len(ids)} 个 {node_type} 节点分别加载，可能增加内存占用。",
                ids,
                "尽量复用同一个模型加载节点的输出。",
            )
        )

    groups = workflow.get("groups") or []
    if len(nodes) >= 14 and not groups:
        findings.append(
            _finding(
                "info",
                "missing_groups",
                "复杂工作流尚未分组",
                f"当前有 {len(nodes)} 个节点，但没有画布分组。",
                recommendation="按模型加载、条件控制、采样和输出建立语义分组。",
            )
        )
    if len(nodes) >= 80:
        findings.append(
            _finding(
                "warning",
                "large_workflow",
                "工作流规模较大",
                f"当前包含 {len(nodes)} 个节点，建议拆出可复用子图。",
                recommendation="把稳定模块转换为子图，并保留清晰的输入输出接口。",
            )
        )

    severity_penalty = {"error": 14, "warning": 6, "info": 2}
    score = max(0, 100 - sum(severity_penalty[item["severity"]] for item in findings))
    return {
        "summary": {
            "node_count": len(nodes),
            "link_count": len(links),
            "group_count": len(groups) if isinstance(groups, list) else 0,
            "estimated_depth": _estimate_depth(set(node_by_id), links),
            "score": score,
        },
        "findings": findings,
    }
