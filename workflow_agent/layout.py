"""Deterministic semantic layout for readable left-to-right ComfyUI workflows."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from .analyzer import normalize_links


_STAGES = (
    ("input", "输入与预处理", "读取图片、视频、音频或建立初始数据。", "#3c78d8"),
    ("model", "模型与资源", "加载本阶段需要的模型、编码器与控制资源。", "#7b61c9"),
    ("prompt", "提示词与条件控制", "构建正负条件、提示词和控制条件。", "#1f9d8b"),
    ("sampling", "采样与生成", "执行主要生成或采样过程。", "#e58b2b"),
    ("decode", "解码与后处理", "解码结果并执行图像、视频或音频处理。", "#c4577a"),
    ("output", "预览与输出", "预览、保存或导出最终结果。", "#499d55"),
    ("utility", "辅助与调试", "承载显示、数学、开关或调试辅助。", "#6d7680"),
)
_STAGE_INDEX = {key: index for index, (key, *_rest) in enumerate(_STAGES)}


def _stage_from_text(text: str) -> str:
    value = text.lower().replace("_", "")
    if any(word in value for word in ("save", "preview", "showtext", "output", "export")):
        return "output"
    if any(word in value for word in ("ksampler", "sampler", "scheduler", "noise", "latent")):
        return "sampling"
    if any(word in value for word in ("decode", "upscale", "detail", "face", "restore", "combine", "blend")):
        return "decode"
    if any(word in value for word in ("cliptext", "prompt", "conditioning", "controlnetapply", "ipadapterapply")):
        return "prompt"
    if any(word in value for word in ("loader", "lora", "checkpoint", "unet", "vae", "clipvision", "controlnet")):
        return "model"
    if any(word in value for word in ("loadimage", "loadvideo", "loadaudio", "input", "empty", "mask", "preprocess", "detect")):
        return "input"
    return "utility"


def _topology(workflow: dict[str, Any]) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, int]]:
    nodes = {str(node.get("id")): node for node in workflow.get("nodes") or [] if isinstance(node, dict)}
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    indegree = {node_id: 0 for node_id in nodes}
    for link in normalize_links(workflow):
        origin, target = str(link.get("origin_id")), str(link.get("target_id"))
        if origin in nodes and target in nodes and origin != target:
            outgoing[origin].append(target)
            incoming[target].append(origin)
            indegree[target] += 1
    queue = deque(node_id for node_id, value in indegree.items() if value == 0)
    depth = {node_id: 0 for node_id in nodes}
    visited = set()
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        for target in outgoing[current]:
            depth[target] = max(depth[target], depth[current] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    # Cyclic or malformed fragments retain a stable local depth.
    for node_id, node in nodes.items():
        if node_id not in visited:
            depth[node_id] = max(depth.get(node_id, 0), int(float((node.get("pos") or [0])[0]) // 420))
    return outgoing, incoming, depth


def semantic_plan(workflow: dict[str, Any], agent_groups: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build semantic group assignments; agent hints may rename but not invalidate graph facts."""
    nodes = [node for node in workflow.get("nodes") or [] if isinstance(node, dict)]
    outgoing, incoming, depth = _topology(workflow)
    hints = {str(item.get("node_id")): item for item in agent_groups or [] if isinstance(item, dict) and item.get("node_id") is not None}
    node_assignments = []
    stage_nodes: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        node_id = str(node.get("id"))
        hint = hints.get(node_id, {})
        inferred = _stage_from_text(f"{node.get('type') or ''} {node.get('title') or ''}")
        stage = str(hint.get("stage_id") or inferred)
        if stage not in _STAGE_INDEX:
            stage = inferred
        lane = str(hint.get("lane_id") or ("main" if len(incoming[node_id]) <= 1 else "branch"))
        stage_nodes[stage].append(node_id)
        node_assignments.append(
            {
                "node_id": node_id,
                "stage_id": stage,
                "lane_id": lane,
                "role": str(hint.get("role") or inferred),
                "importance": str(hint.get("importance") or ("primary" if stage in {"sampling", "output"} else "normal")),
                "editable": bool(hint.get("editable", stage in {"prompt", "sampling"})),
                "topology_depth": depth[node_id],
            }
        )
    groups = []
    for stage, members in stage_nodes.items():
        base = next(item for item in _STAGES if item[0] == stage)
        title = next((str(item.get("stage_title")) for item in hints.values() if item.get("stage_id") == stage and item.get("stage_title")), base[1])
        purpose = next((str(item.get("stage_purpose")) for item in hints.values() if item.get("stage_id") == stage and item.get("stage_purpose")), base[2])
        # A broad functional label is not a spatial layout rule.  Split it at
        # topology bands so one "utility" or "input" group cannot become a
        # kilometre-high column that hides several unrelated operations.
        bands: dict[int, list[str]] = defaultdict(list)
        for node_id in members:
            bands[depth[node_id] // 2].append(node_id)
        multi_band = len(bands) > 1
        for index, (_, band_members) in enumerate(sorted(bands.items()), start=1):
            group_title = f"{title} · {index}" if multi_band else title
            groups.append(
                {
                    "group_id": f"{stage}:{index}:{min(depth[node] for node in band_members)}",
                    "stage_id": stage,
                    "title": group_title,
                    "purpose": purpose,
                    "color": base[3],
                    "node_ids": sorted(band_members, key=lambda node_id: (depth[node_id], node_id)),
                }
            )
    groups.sort(key=lambda item: (min(depth[node] for node in item["node_ids"]), _STAGE_INDEX.get(item["stage_id"], 99), item["group_id"]))
    return {"assignments": node_assignments, "groups": groups}


def build_layout_plan(
    workflow: dict[str, Any], agent_groups: list[dict[str, Any]] | None = None, mode: str = "semantic_rebuild"
) -> dict[str, Any]:
    """Return positions/groups only. It never changes execution connections or widget values."""
    if mode not in {"structure_only", "preserve_groups", "semantic_rebuild", "selection_only"}:
        raise ValueError("不支持的排版模式")
    nodes = [node for node in workflow.get("nodes") or [] if isinstance(node, dict)]
    semantic = semantic_plan(workflow, agent_groups if mode == "semantic_rebuild" else None)
    by_id = {str(node.get("id")): node for node in nodes}
    assignment = {item["node_id"]: item for item in semantic["assignments"]}
    _, incoming, depth = _topology(workflow)
    layers: dict[int, list[str]] = defaultdict(list)
    for node_id in assignment:
        # A valid DAG must always read left to right.  Semantic labels are used
        # only for groups and vertical lanes, never to force a later stage into
        # an earlier/later column.
        layers[depth[node_id]].append(node_id)
    positions: dict[str, list[float]] = {}
    min_x = min((float((node.get("pos") or [0])[0]) for node in nodes), default=0.0)
    min_y = min((float((node.get("pos") or [0, 0])[1]) for node in nodes), default=0.0)
    x = min_x
    column_widths: dict[int, float] = {}
    # `order_by_node` contains all already placed predecessors.  Unlike a
    # stage label, this is a graph fact and therefore keeps an edge moving in
    # the reading direction even when it jumps over one or more columns.
    order_by_node: dict[str, int] = {}
    for level in sorted(layers):
        ids = layers[level]
        def sort_key(node_id: str) -> tuple[float, bool, float, str]:
            parent_order = [order_by_node[parent] for parent in incoming.get(node_id, []) if parent in order_by_node]
            # Barycentric ordering reduces crossing lines.  Nodes with no
            # placed parent keep their original vertical position.
            vertical_hint = (
                sum(parent_order) / len(parent_order)
                if parent_order
                else float((by_id[node_id].get("pos") or [0, 0])[1])
            )
            return (vertical_hint, assignment[node_id]["lane_id"] != "main", float((by_id[node_id].get("pos") or [0, 0])[1]), node_id)

        ids.sort(key=sort_key)
        order_by_node.update({node_id: index for index, node_id in enumerate(ids)})
        width = max(float((by_id[node_id].get("size") or [220])[0]) for node_id in ids)
        column_widths[level] = max(250.0, width)
    for level in sorted(layers):
        y = min_y
        for node_id in layers[level]:
            node = by_id[node_id]
            height = max(70.0, float((node.get("size") or [220, 100])[1]))
            positions[node_id] = [round(x, 1), round(y, 1)]
            y += height + 58.0
        x += column_widths[level] + 150.0
    groups = []
    # "保留现有分组" must not create duplicates in a user's canvas.  It still
    # uses the dependency-aware positions above, but leaves existing groups as
    # authored by the workflow creator.
    if mode == "semantic_rebuild":
        for group in semantic["groups"]:
            rects = []
            for node_id in group["node_ids"]:
                pos = positions[node_id]
                size = by_id[node_id].get("size") or [220, 100]
                rects.append((pos[0], pos[1], pos[0] + float(size[0]), pos[1] + float(size[1])))
            left, top = min(item[0] for item in rects), min(item[1] for item in rects)
            right, bottom = max(item[2] for item in rects), max(item[3] for item in rects)
            description = f"作用：{group['purpose']}\n输入：来自左侧相关阶段。\n输出：交给右侧后续阶段。\n重点参数：绿色标记的可调节点。"
            groups.append(
                {
                    **group,
                    "pos": [round(left - 32, 1), round(top - 42, 1)],
                    "size": [round(right - left + 64, 1), round(bottom - top + 70, 1)],
                    "description": description,
                }
            )
    suggestions = _reroute_suggestions(workflow, positions, by_id, assignment)
    return {
        "mode": mode,
        "positions": positions,
        "groups": groups,
        "semantic": semantic,
        "summary": {"node_count": len(nodes), "group_count": len(groups), "columns": len(layers)},
        "reroute_suggestions": suggestions,
        "warning": "本次计划只修改画布位置、分组和说明，不修改节点参数、连线或执行语义。",
    }


def _reroute_suggestions(
    workflow: dict[str, Any],
    positions: dict[str, list[float]],
    by_id: dict[str, dict[str, Any]],
    assignment: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flag only visually expensive links; insertion remains an explicit user action.

    Reroutes are presentation helpers, so a suggestion must never change a
    workflow.  We only identify long cross-stage links or links spanning a
    dense column gap, with a stable suggested midpoint for a future UI action.
    """
    suggestions = []
    for link in normalize_links(workflow):
        origin_id, target_id = str(link.get("origin_id")), str(link.get("target_id"))
        if origin_id not in positions or target_id not in positions:
            continue
        origin, target = positions[origin_id], positions[target_id]
        source_width = float((by_id[origin_id].get("size") or [220])[0])
        distance = target[0] - (origin[0] + source_width)
        source_stage = assignment[origin_id]["stage_id"]
        target_stage = assignment[target_id]["stage_id"]
        if distance < 560 and source_stage == target_stage:
            continue
        if distance < 800 and abs(target[1] - origin[1]) < 480:
            continue
        suggestions.append(
            {
                "link_id": str(link.get("id") or f"{origin_id}-{target_id}"),
                "origin_id": origin_id,
                "target_id": target_id,
                "reason": f"连接跨越 {source_stage} 到 {target_stage}，可能遮挡中间节点。",
                "suggested_pos": [round(origin[0] + source_width + max(120, distance / 2), 1), round((origin[1] + target[1]) / 2, 1)],
            }
        )
    return suggestions[:20]
