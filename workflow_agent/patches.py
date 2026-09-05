"""Validation and persistence for user-approved workflow patch plans."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


ALLOWED_OPERATIONS = {
    "replace_node", "add_node", "remove_optional_node", "connect", "disconnect", "set_widget",
    "set_title", "set_color", "move_node", "create_group", "update_group", "add_note", "add_reroute",
}


def validate_patch_plan(plan: dict[str, Any], workflow: dict[str, Any], known_node_types: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("修复计划必须是对象")
    nodes = {str(item.get("id")) for item in workflow.get("nodes") or [] if isinstance(item, dict)}
    errors = []
    safe_operations = []
    for index, operation in enumerate(plan.get("operations") or []):
        if not isinstance(operation, dict) or operation.get("kind") not in ALLOWED_OPERATIONS:
            errors.append({"index": index, "error": "不支持的修改动作"})
            continue
        kind = operation["kind"]
        node_id = operation.get("node_id")
        if kind not in {"add_node", "create_group", "add_note"} and node_id is not None and str(node_id) not in nodes:
            errors.append({"index": index, "error": "操作引用了不存在的节点"})
            continue
        target_type = operation.get("target_type")
        if target_type and known_node_types and str(target_type) not in known_node_types:
            errors.append({"index": index, "error": "替代节点未在当前 ComfyUI 中注册"})
            continue
        required: dict[str, tuple[str, ...]] = {
            "replace_node": ("node_id", "target_type"),
            "add_node": ("target_type",),
            "remove_optional_node": ("node_id",),
            "connect": ("origin_id", "origin_slot", "target_id", "target_slot"),
            "disconnect": ("link_id",),
            "set_widget": ("node_id", "widget_index", "value"),
            "set_title": ("node_id", "title"),
            "set_color": ("node_id", "color"),
            "move_node": ("node_id", "pos"),
            "create_group": ("title", "pos", "size"),
            "update_group": ("group_id",),
            "add_note": ("text", "pos"),
            "add_reroute": ("origin_id", "origin_slot", "target_id", "target_slot", "pos"),
        }
        missing_fields = [field for field in required[kind] if field not in operation or operation[field] is None]
        if missing_fields:
            errors.append({"index": index, "error": f"{kind} 缺少必要字段：{'、'.join(missing_fields)}"})
            continue
        for field in ("origin_id", "target_id"):
            if field in operation and str(operation[field]) not in nodes:
                errors.append({"index": index, "error": "连线操作引用了不存在的节点"})
                break
        else:
            for field in ("origin_slot", "target_slot", "widget_index"):
                if field in operation and (not isinstance(operation[field], int) or operation[field] < 0):
                    errors.append({"index": index, "error": f"{field} 必须是非负整数"})
                    break
            else:
                for field in ("pos", "size"):
                    value = operation.get(field)
                    if value is not None and (not isinstance(value, (list, tuple)) or len(value) != 2 or not all(isinstance(number, (int, float)) for number in value)):
                        errors.append({"index": index, "error": f"{field} 必须是两个数值组成的坐标或尺寸"})
                        break
                else:
                    safe_operations.append(operation)
                    continue
    return {"valid": not errors, "errors": errors, "operations": safe_operations}


class SnapshotStore:
    """Stores recoverable JSON snapshots outside the workflow itself."""

    def __init__(self, root: Path):
        self.root = root
        self.snapshots = root / "snapshots"
        self.reports = root / "reports"
        self.snapshots.mkdir(parents=True, exist_ok=True)
        self.reports.mkdir(parents=True, exist_ok=True)

    def create(self, workflow: dict[str, Any], label: str = "操作前快照") -> dict[str, Any]:
        identifier = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        record = {"id": identifier, "label": label, "created_at": int(time.time()), "workflow": workflow}
        path = self.snapshots / f"{identifier}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"id": identifier, "label": label, "created_at": record["created_at"]}

    def list(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.snapshots.glob("*.json"), reverse=True)[:40]:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                result.append({key: record.get(key) for key in ("id", "label", "created_at")})
            except Exception:
                continue
        return result

    def get(self, identifier: str) -> dict[str, Any]:
        path = self.snapshots / f"{Path(identifier).name}.json"
        if not path.is_file():
            raise FileNotFoundError("找不到该快照")
        return json.loads(path.read_text(encoding="utf-8"))

    def report(self, data: dict[str, Any]) -> dict[str, Any]:
        identifier = f"report-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        path = self.reports / f"{identifier}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"id": identifier, "filename": path.name}

    def get_report(self, identifier: str) -> dict[str, Any]:
        path = self.reports / f"{Path(identifier).name}.json"
        if not path.is_file():
            raise FileNotFoundError("找不到该迁移报告")
        return json.loads(path.read_text(encoding="utf-8"))
