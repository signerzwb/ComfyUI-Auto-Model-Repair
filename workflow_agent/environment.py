"""Read-only inspection of the live ComfyUI environment."""

from __future__ import annotations

import importlib
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def node_catalog() -> dict[str, dict[str, Any]]:
    """Return static node facts without executing third-party schema functions.

    A live ComfyUI can contain legacy nodes whose INPUT_TYPES() implementation
    raises because its author targets a different API generation. Invoking all
    of them turns one incompatible node into a failure for the whole assistant.
    Workflow JSON already carries live port data, so catalog construction must
    remain static and failure-isolated.
    """
    try:
        import nodes
    except ImportError:
        return {}
    result: dict[str, dict[str, Any]] = {}
    # Do not access values from NODE_CLASS_MAPPINGS.  Some legacy nodes expose
    # metaclass properties that construct a Schema on attribute access.
    for name in getattr(nodes, "NODE_CLASS_MAPPINGS", {}):
        result[str(name)] = {
            "name": str(name),
            "category": "",
            "description": "",
            "inputs": {},
            "outputs": [],
            "output_names": [],
            "schema_source": "workflow_ports",
        }
    return result


def refresh_node_catalog() -> dict[str, dict[str, Any]]:
    node_catalog.cache_clear()
    return node_catalog()


def installed_node_types() -> list[str]:
    return sorted(node_catalog())


def _version(module_name: str, attribute: str = "__version__") -> str | None:
    try:
        module = importlib.import_module(module_name)
        value = getattr(module, attribute, None)
        return str(value) if value else None
    except Exception:
        return None


def _custom_nodes_root() -> Path | None:
    # In a normal install this file lives at custom_nodes/<plugin>/workflow_agent/.
    candidate = Path(__file__).resolve().parents[2]
    return candidate if candidate.name.lower() == "custom_nodes" else None


def auto_model_repair_adapter_status() -> dict[str, Any]:
    """Report an optional legacy-plugin adapter without importing its code.

    The legacy extension exposes no stable public Python API.  Importing it just
    to scan a workflow would run third-party module initialization in our route
    process, so v1 intentionally reuses only its presence/version metadata.
    The independent resolver remains the source of truth.
    """
    root = _custom_nodes_root()
    plugin = root / "ComfyUI-Auto-Model-Repair" if root else None
    if not plugin or not plugin.is_dir():
        return {"available": False, "mode": "standalone", "reason": "未检测到 Auto-Model-Repair。"}
    version = None
    for name in ("pyproject.toml", "__init__.py", "README.md"):
        path = plugin / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[:20_000]
            import re

            match = re.search(r"(?:__version__|version)\s*[=:]\s*[\"']?([0-9][\w.\-]+)", text, re.I)
            if match:
                version = match.group(1)
                break
        except OSError:
            continue
    return {
        "available": True,
        "mode": "metadata_only",
        "version": version,
        "reason": "已检测到旧插件；v1 保持独立解析，仅保留安全的兼容识别，不导入其启发式决策。",
    }


def environment_snapshot(include_catalog: bool = False) -> dict[str, Any]:
    """Provide facts needed by the planner without leaking local paths."""
    catalog = node_catalog()
    data: dict[str, Any] = {
        "python": sys.version.split()[0],
        "comfyui_version": _version("comfyui_version"),
        "node_count": len(catalog),
        "node_types": sorted(catalog) if include_catalog else None,
        "torch": _version("torch"),
        "gpu": [],
        "model_folders": {},
        "free_disk_bytes": None,
        "manager_detected": False,
        "auto_model_repair_detected": False,
        "auto_model_repair_adapter": auto_model_repair_adapter_status(),
    }
    try:
        import torch

        if torch.cuda.is_available():
            for index in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(index)
                data["gpu"].append(
                    {
                        "name": props.name,
                        "vram_bytes": int(props.total_memory),
                        "index": index,
                    }
                )
    except Exception:
        pass
    try:
        import folder_paths

        folders = getattr(folder_paths, "folder_names_and_paths", {})
        for name, value in folders.items():
            roots = value[0] if isinstance(value, tuple) else []
            count = 0
            for root in roots or []:
                try:
                    count += sum(1 for path in Path(root).rglob("*") if path.is_file())
                except OSError:
                    continue
            data["model_folders"][str(name)] = {"file_count": count}
        output_directory = getattr(folder_paths, "get_output_directory", lambda: None)()
        if output_directory:
            data["free_disk_bytes"] = shutil.disk_usage(output_directory).free
    except Exception:
        pass
    custom_root = _custom_nodes_root()
    if custom_root:
        data["manager_detected"] = (custom_root / "ComfyUI-Manager").is_dir()
        data["auto_model_repair_detected"] = (custom_root / "ComfyUI-Auto-Model-Repair").is_dir()
    return data
