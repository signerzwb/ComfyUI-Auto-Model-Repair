"""Safe read-only integration with ComfyUI-Manager's local catalogs."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


def manager_root() -> Path | None:
    configured = os.environ.get("COMFYUI_WORKFLOW_DOCTOR_MANAGER_PATH", "").strip()
    candidates = [Path(configured)] if configured else []
    # custom_nodes/<this-plugin>/workflow_agent/manager.py -> custom_nodes
    custom_nodes = Path(__file__).resolve().parents[2]
    if custom_nodes.name.lower() == "custom_nodes":
        candidates.append(custom_nodes / "ComfyUI-Manager")
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "extension-node-map.json").is_file():
            return candidate
    return None


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def manager_catalog() -> dict[str, Any]:
    root = manager_root()
    if not root:
        return {"available": False, "by_node": {}, "packages": {}}
    try:
        node_map = _read_json(root / "extension-node-map.json")
        node_list = _read_json(root / "custom-node-list.json")
    except Exception as exc:
        return {"available": True, "error": str(exc), "by_node": {}, "packages": {}}
    package_by_file: dict[str, dict[str, Any]] = {}
    packages: dict[str, dict[str, Any]] = {}
    for item in node_list.get("custom_nodes", []) if isinstance(node_list, dict) else []:
        if not isinstance(item, dict):
            continue
        package = {
            "id": str(item.get("id") or ""),
            "title": str(item.get("title") or item.get("id") or ""),
            "reference": str(item.get("reference") or ""),
            "files": [str(value) for value in item.get("files") or []],
            "version": str(item.get("version") or "unknown"),
            "install_type": str(item.get("install_type") or ""),
            "description": str(item.get("description") or "")[:1000],
        }
        if package["id"]:
            packages[package["id"]] = package
        for file_url in package["files"]:
            package_by_file[file_url.rstrip("/").lower()] = package
    by_node: dict[str, list[dict[str, Any]]] = {}
    for source, payload in (node_map.items() if isinstance(node_map, dict) else []):
        if not isinstance(payload, list) or not payload:
            continue
        names = payload[0] if isinstance(payload[0], list) else []
        metadata = payload[1] if len(payload) > 1 and isinstance(payload[1], dict) else {}
        package = package_by_file.get(str(source).rstrip("/").lower())
        candidate = {
            "source": str(source),
            "package_id": package.get("id") if package else None,
            "title": package.get("title") if package else metadata.get("title_aux") or str(source),
            "reference": package.get("reference") if package else str(source),
            "files": package.get("files") if package else [str(source)],
            "version": package.get("version") if package else "unknown",
            "install_type": package.get("install_type") if package else "",
            # Manager's extension map is authoritative even when its package list
            # has not yet caught up.  In that case Manager can still queue the
            # repository as an "unknown" package after its own policy check.
            "manager_payload": package
            or {
                "id": None,
                "files": [str(source)],
                "reference": str(source),
                "version": "unknown",
            },
        }
        for node_name in names:
            by_node.setdefault(str(node_name), []).append(candidate)
    return {"available": True, "by_node": by_node, "packages": packages}


def refresh_manager_catalog() -> dict[str, Any]:
    manager_catalog.cache_clear()
    return manager_catalog()


def package_candidates(node_type: str) -> list[dict[str, Any]]:
    return list(manager_catalog().get("by_node", {}).get(str(node_type), []))


def resolve_missing_node_packages(missing_node_types: list[str]) -> list[dict[str, Any]]:
    results = []
    for node_type in sorted(set(map(str, missing_node_types))):
        candidates = package_candidates(node_type)
        results.append(
            {
                "node_type": node_type,
                "status": "install_exact" if candidates else "unresolved",
                "candidates": candidates[:8],
                "message": "已在 ComfyUI-Manager 本地目录中找到候选节点包。"
                if candidates
                else "本地 Manager 索引中没有找到明确节点包。",
            }
        )
    return results


def manager_install_request(candidate: dict[str, Any]) -> dict[str, Any]:
    """Create, but never execute, a request understood by Manager's queue endpoint."""
    payload = candidate.get("manager_payload") or {}
    files = payload.get("files") or candidate.get("files") or []
    if not payload or not files:
        raise ValueError("候选节点包没有 Manager 安装信息")
    version = payload.get("version") or "unknown"
    return {
        "url": "/manager/queue/install",
        "method": "POST",
        "body": {
            "id": payload.get("id"),
            "version": version,
            "selected_version": "latest" if payload.get("id") and version != "unknown" else "unknown",
            "files": files,
            "repository": payload.get("reference"),
            "channel": "default",
            "mode": "default",
            "skip_post_install": False,
            "ui_id": "workflow-doctor",
            "pip": [],
        },
    }
