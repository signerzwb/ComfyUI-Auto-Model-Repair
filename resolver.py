import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import folder_paths

from .matcher import ModelMatcher
from .workflow_utils import (
    clone_workflow,
    extract_node_widgets,
    get_node_identity,
    iter_workflow_nodes,
    set_widget_value,
)

logger = logging.getLogger("ComfyUI-Auto-Model-Repair")

MODEL_FILE_EXTENSIONS = [".safetensors", ".ckpt", ".pt", ".pth", ".bin"]
INVALID_MODEL_VALUES = {
    "",
    "none",
    "null",
    "nil",
    "n/a",
    "na",
    "default",
    "auto",
}


class ModelResolverService:
    def __init__(self, plugin_dir: Path, config_path: Path):
        self.plugin_dir = plugin_dir
        self.config_path = config_path
        self.config = self._load_config()
        self.matcher = ModelMatcher(
            candidate_threshold=int(self.config.get("candidate_threshold", 78)),
            max_candidates=int(self.config.get("max_candidates", 5)),
        )

    def _load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _looks_like_model_filename(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False

        text = value.strip()
        if not text:
            return False

        lower_text = text.lower()
        if lower_text in INVALID_MODEL_VALUES:
            return False

        if len(text) > 120:
            return False

        if "\n" in text or "\r" in text:
            return False

        if not any(lower_text.endswith(ext) for ext in MODEL_FILE_EXTENSIONS):
            return False

        return True

    def build_model_index(self) -> Dict[str, Any]:
        ignore_exts = set(self.config.get("ignore_extensions", []))
        result: Dict[str, Any] = {}

        folder_names = set(folder_paths.folder_names_and_paths.keys())
        for folder_name in sorted(folder_names):
            files = []
            try:
                for filename in folder_paths.get_filename_list(folder_name):
                    if Path(filename).suffix.lower() in ignore_exts:
                        continue
                    full_path = self._resolve_full_path(folder_name, filename)
                    files.append({
                        "filename": filename,
                        "path": full_path,
                    })
            except Exception:
                continue

            if files:
                result[folder_name] = files

        return result

    def _resolve_full_path(self, folder_name: str, filename: str) -> str:
        try:
            full_path = folder_paths.get_full_path(folder_name, filename)
            if full_path:
                return str(full_path)
        except Exception:
            pass

        try:
            roots = folder_paths.get_folder_paths(folder_name)
            for root in roots:
                candidate = Path(root) / filename
                if candidate.exists():
                    return str(candidate)
        except Exception:
            pass

        return filename

    def _folder_candidates(self, folder_name: str) -> List[Path]:
        ignore_exts = set(self.config.get("ignore_extensions", []))
        out: List[Path] = []
        try:
            filenames = folder_paths.get_filename_list(folder_name)
        except Exception:
            return out

        for filename in filenames:
            if Path(filename).suffix.lower() in ignore_exts:
                continue
            full_path = self._resolve_full_path(folder_name, filename)
            out.append(Path(full_path))
        return out

    def _detect_model_type(
        self,
        node_type: str,
        node_title: str,
        widget_name: str,
        widget_value: Any
    ) -> Optional[str]:
        if not self._looks_like_model_filename(widget_value):
            return None

        widget_rules = self.config.get("widget_name_rules", {})
        node_type_rules = self.config.get("node_type_rules", {})
        node_title_rules = self.config.get("node_title_rules", {})

        widget_name_l = (widget_name or "").strip().lower()
        node_type_l = (node_type or "").strip().lower()
        node_title_l = (node_title or "").strip().lower()
        value_l = str(widget_value).strip().lower()

        if widget_name_l in widget_rules:
            return widget_rules[widget_name_l]

        if node_type_l in node_type_rules:
            return node_type_rules[node_type_l]

        for key, folder_name in node_title_rules.items():
            if key in node_title_l:
                return folder_name

        if "lora" in value_l:
            return "loras"
        if "vae" in value_l:
            return "vae"
        if "clip" in value_l or "t5" in value_l or "text_encoder" in value_l:
            return "clip"
        if "unet" in value_l or "transformer" in value_l:
            return "unet"

        if "lora" in node_type_l or "lora" in node_title_l:
            return "loras"
        if "vae" in node_type_l or "vae" in node_title_l:
            return "vae"
        if "clip" in node_type_l or "clip" in node_title_l:
            return "clip"
        if "unet" in node_type_l or "unet" in node_title_l:
            return "unet"
        if "checkpoint" in node_type_l or "checkpoint" in node_title_l:
            return "checkpoints"

        return None

    def _exists_in_folder(self, folder_name: str, expected_filename: str) -> bool:
        try:
            names = folder_paths.get_filename_list(folder_name)
        except Exception:
            return False

        expected_name = Path(expected_filename).name
        for name in names:
            if Path(name).name == expected_name:
                return True
        return False

    def scan_workflow(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        model_index = self.build_model_index()
        items = []

        for node in iter_workflow_nodes(workflow):
            node_id, node_type, node_title = get_node_identity(node)
            widgets = extract_node_widgets(node)

            for widget_info in widgets:
                widget_name = widget_info["name"]
                widget_index = widget_info["index"]
                widget_value = widget_info["value"]

                if not isinstance(widget_value, str) or not widget_value.strip():
                    continue

                model_type = self._detect_model_type(
                    node_type,
                    node_title,
                    widget_name,
                    widget_value,
                )
                if not model_type:
                    continue

                exists = self._exists_in_folder(model_type, widget_value)
                candidates = []
                best = None

                if not exists:
                    folder_candidates = self._folder_candidates(model_type)
                    matches = self.matcher.find_best_matches(widget_value, folder_candidates)
                    candidates = [asdict(m) for m in matches]
                    if matches:
                        best = asdict(matches[0])

                items.append({
                    "node_id": node_id,
                    "node_type": node_type,
                    "node_title": node_title,
                    "widget_name": widget_name,
                    "widget_index": widget_index,
                    "expected": widget_value,
                    "model_type": model_type,
                    "exists": exists,
                    "best_match": best,
                    "candidates": candidates,
                })

        summary = {
            "total": len(items),
            "exists": sum(1 for x in items if x["exists"]),
            "missing": sum(1 for x in items if not x["exists"]),
            "auto_resolvable": sum(
                1
                for x in items
                if (not x["exists"])
                and x["best_match"]
                and x["best_match"]["score"] >= int(self.config.get("auto_apply_threshold", 92))
            ),
        }

        return {
            "summary": summary,
            "items": items,
            "model_index": {k: len(v) for k, v in model_index.items()},
        }

    def resolve_workflow(self, workflow: Dict[str, Any], auto_apply_threshold: Optional[int] = None) -> Dict[str, Any]:
        threshold = int(auto_apply_threshold or self.config.get("auto_apply_threshold", 92))
        new_workflow = clone_workflow(workflow)
        scan = self.scan_workflow(new_workflow)
        items = scan["items"]
        applied = []
        unresolved = []

        node_map = {str(node.get("id")): node for node in iter_workflow_nodes(new_workflow)}

        for item in items:
            if item["exists"]:
                continue

            best = item.get("best_match")
            if not best or int(best.get("score", 0)) < threshold:
                unresolved.append(item)
                continue

            node = node_map.get(str(item["node_id"]))
            if not node:
                unresolved.append(item)
                continue

            ok = set_widget_value(node, int(item["widget_index"]), best["filename"])
            if not ok:
                unresolved.append(item)
                continue

            applied.append({
                "node_id": item["node_id"],
                "widget_name": item["widget_name"],
                "from": item["expected"],
                "to": best["filename"],
                "score": best["score"],
                "model_type": item["model_type"],
            })

        return {
            "workflow": new_workflow,
            "applied": applied,
            "unresolved": unresolved,
            "summary": {
                "applied_count": len(applied),
                "unresolved_count": len(unresolved),
                "threshold": threshold,
            },
            "scan": scan,
        }

    def apply_selected_matches(self, workflow: Dict[str, Any], selections: List[Dict[str, Any]]) -> Dict[str, Any]:
        new_workflow = clone_workflow(workflow)
        applied = []
        skipped = []

        node_map = {str(node.get("id")): node for node in iter_workflow_nodes(new_workflow)}

        for sel in selections:
            node_id = str(sel.get("node_id", ""))
            widget_index = sel.get("widget_index")
            filename = sel.get("filename")

            if not node_id or widget_index is None or not filename:
                skipped.append({
                    "selection": sel,
                    "reason": "missing node_id/widget_index/filename",
                })
                continue

            node = node_map.get(node_id)
            if not node:
                skipped.append({
                    "selection": sel,
                    "reason": "node not found",
                })
                continue

            ok = set_widget_value(node, int(widget_index), filename)
            if not ok:
                skipped.append({
                    "selection": sel,
                    "reason": "failed to set widget value",
                })
                continue

            applied.append({
                "node_id": node_id,
                "widget_index": int(widget_index),
                "filename": filename,
            })

        return {
            "workflow": new_workflow,
            "applied": applied,
            "skipped": skipped,
            "summary": {
                "applied_count": len(applied),
                "skipped_count": len(skipped),
            },
        }
