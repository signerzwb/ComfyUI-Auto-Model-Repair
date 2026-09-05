"""Node package resolution and conservative local substitute discovery."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .manager import resolve_missing_node_packages


def _types(items: Any, key: str) -> list[str]:
    result = []
    for item in items or []:
        if isinstance(item, dict) and item.get(key):
            result.append(str(item[key]))
    return result


def _tokens(value: Any) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", str(value or "").lower()) if len(part) > 2}


def _schema_inputs(schema: dict[str, Any]) -> list[str]:
    inputs = schema.get("inputs") or {}
    values = []
    for key in ("required", "optional"):
        group = inputs.get(key) if isinstance(inputs, dict) else {}
        if isinstance(group, dict):
            for item in group.values():
                if isinstance(item, (list, tuple)) and item:
                    values.append(str(item[0]))
                elif isinstance(item, str):
                    values.append(item)
    return values


def _similarity(missing: dict[str, Any], candidate_name: str, schema: dict[str, Any]) -> tuple[int, list[str]]:
    expected_inputs = _types(missing.get("inputs"), "type")
    expected_outputs = _types(missing.get("outputs"), "type")
    candidate_inputs = _schema_inputs(schema)
    candidate_outputs = [str(item) for item in schema.get("outputs") or []]
    score, reasons = 0.0, []
    if expected_outputs:
        overlap = len(set(expected_outputs) & set(candidate_outputs)) / max(len(set(expected_outputs)), 1)
        score += overlap * 48
        if overlap:
            reasons.append("输出端口类型有重合")
    if expected_inputs:
        overlap = len(set(expected_inputs) & set(candidate_inputs)) / max(len(set(expected_inputs)), 1)
        score += overlap * 30
        if overlap:
            reasons.append("输入端口类型有重合")
    missing_words = _tokens(f"{missing.get('type')} {missing.get('title')}")
    candidate_words = _tokens(f"{candidate_name} {schema.get('category')} {schema.get('description')}")
    if missing_words and candidate_words:
        overlap = len(missing_words & candidate_words) / len(missing_words | candidate_words)
        score += overlap * 22
        if overlap:
            reasons.append("名称或类别语义相近")
    return min(100, round(score)), reasons


def local_substitutes(missing: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for name, schema in catalog.items():
        score, reasons = _similarity(missing, name, schema)
        if score < 30:
            continue
        expected_outputs = _types(missing.get("outputs"), "type")
        exact_ports = bool(expected_outputs) and set(expected_outputs) == set(schema.get("outputs") or [])
        candidates.append(
            {
                "node_type": name,
                "confidence": score,
                "port_compatible": exact_ports,
                "category": schema.get("category"),
                "reasons": reasons or ["需要 Agent 根据上下文复核功能是否等价。"],
                "status": "replace_local" if score >= 76 and exact_ports else "manual_review",
            }
        )
    return sorted(candidates, key=lambda item: (item["status"] == "replace_local", item["confidence"]), reverse=True)[:10]


def resolve_missing_nodes(workflow: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    missing_by_type: dict[str, list[dict[str, Any]]] = {}
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or "")
        if node_type and node_type not in catalog:
            missing_by_type.setdefault(node_type, []).append(node)
    package_data = {item["node_type"]: item for item in resolve_missing_node_packages(list(missing_by_type))}
    results = []
    for node_type, nodes in sorted(missing_by_type.items()):
        package = package_data[node_type]
        sample = nodes[0]
        local = local_substitutes(sample, catalog)
        status = package["status"]
        if local and local[0]["status"] == "replace_local" and not package["candidates"]:
            status = "replace_local"
        results.append(
            {
                "node_type": node_type,
                "node_ids": [str(node.get("id")) for node in nodes],
                "count": len(nodes),
                "status": status,
                "package_candidates": package["candidates"],
                "local_substitutes": local,
                "message": package["message"],
                "next_step": "请比较安装原节点与本机替代的行为差异。" if local else "请先确认节点包来源。",
            }
        )
    return results


def user_rules_path(root: Path) -> Path:
    return root / "substitutions.user.json"


def load_user_rules(root: Path) -> list[dict[str, Any]]:
    path = user_rules_path(root)
    if not path.is_file():
        return []
    try:
        import json

        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except Exception:
        return []


def save_user_rule(root: Path, rule: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    if not isinstance(rule, dict) or not rule.get("source_node") or not rule.get("target_node"):
        raise ValueError("替代规则必须包含来源节点和目标节点")
    rules = load_user_rules(root)
    rules.append({key: value for key, value in rule.items() if key in {"source_node", "target_node", "port_map", "widget_map", "note"}})
    path = user_rules_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    return rules
