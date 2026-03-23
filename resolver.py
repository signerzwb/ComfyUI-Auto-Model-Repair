import json, logging, re
from dataclasses import asdict
from pathlib import Path
import folder_paths
from .matcher import ModelMatcher
from .workflow_utils import clone_workflow, extract_node_widgets, get_node_identity, iter_workflow_nodes, set_widget_value
logger = logging.getLogger("ComfyUI-Auto-Model-Repair")
MODEL_FILE_EXTENSIONS = [".safetensors",".ckpt",".pt",".pth",".bin"]; INVALID_MODEL_VALUES = {"","none","null","nil","n/a","na","default","auto"}
class ModelResolverService:
    def __init__(self, plugin_dir, config_path):
        self.plugin_dir = plugin_dir; self.config_path = config_path; self.config = self._load_config(); self.matcher = ModelMatcher(candidate_threshold=int(self.config.get("candidate_threshold",78)), max_candidates=int(self.config.get("max_candidates",5)))
    def _load_config(self):
        if not self.config_path.exists(): return {}
        with open(self.config_path, "r", encoding="utf-8") as f: return json.load(f)
    def _looks_like_model_filename(self, value):
        if not isinstance(value, str): return False
        text = value.strip()
        if not text or text.lower() in INVALID_MODEL_VALUES or len(text) > 140 or "\n" in text or "\r" in text: return False
        return any(text.lower().endswith(ext) for ext in MODEL_FILE_EXTENSIONS)
    def normalize_name(self, value): return self.matcher.normalize_filename(value)
    def build_model_index(self):
        ignore_exts = set(self.config.get("ignore_extensions", [])); result = {}
        for folder_name in sorted(set(folder_paths.folder_names_and_paths.keys())):
            files = []
            try:
                for filename in folder_paths.get_filename_list(folder_name):
                    if Path(filename).suffix.lower() in ignore_exts: continue
                    files.append({"filename": filename, "path": self._resolve_full_path(folder_name, filename)})
            except Exception:
                continue
            if files: result[folder_name] = files
        return result
    def _resolve_full_path(self, folder_name, filename):
        try:
            full_path = folder_paths.get_full_path(folder_name, filename)
            if full_path: return str(full_path)
        except Exception: pass
        try:
            for root in folder_paths.get_folder_paths(folder_name):
                candidate = Path(root) / filename
                if candidate.exists(): return str(candidate)
        except Exception: pass
        return filename
    def get_primary_folder_path(self, folder_name):
        try:
            roots = folder_paths.get_folder_paths(folder_name)
            if roots: return str(roots[0])
        except Exception: pass
        return None
    def _folder_candidates(self, folder_name):
        ignore_exts = set(self.config.get("ignore_extensions", [])); out = []
        try: filenames = folder_paths.get_filename_list(folder_name)
        except Exception: return out
        for filename in filenames:
            if Path(filename).suffix.lower() in ignore_exts: continue
            out.append(Path(self._resolve_full_path(folder_name, filename)))
        return out
    def _detect_model_type(self, node_type, node_title, widget_name, widget_value):
        if not self._looks_like_model_filename(widget_value): return None
        widget_rules = self.config.get("widget_name_rules", {}); node_type_rules = self.config.get("node_type_rules", {}); node_title_rules = self.config.get("node_title_rules", {})
        widget_name_l = (widget_name or "").strip().lower(); node_type_l = (node_type or "").strip().lower(); node_title_l = (node_title or "").strip().lower(); value_l = str(widget_value).strip().lower()
        if widget_name_l in widget_rules: return widget_rules[widget_name_l]
        if node_type_l in node_type_rules: return node_type_rules[node_type_l]
        for key, folder_name in node_title_rules.items():
            if key in node_title_l: return folder_name
        if "lora" in value_l: return "loras"
        if "vae" in value_l: return "vae"
        if "clip" in value_l or "t5" in value_l or "text_encoder" in value_l or "umt5" in value_l: return "clip"
        if "diffusion" in value_l: return "diffusion_models"
        if "unet" in value_l or "transformer" in value_l: return "unet"
        if "checkpoint" in node_type_l or "checkpoint" in node_title_l: return "checkpoints"
        return None
    def _exists_in_folder(self, folder_name, expected_filename):
        try: names = folder_paths.get_filename_list(folder_name)
        except Exception: return False
        expected_name = Path(expected_filename).name
        return any(Path(name).name == expected_name for name in names)
    def extract_download_links(self, workflow):
        pattern = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)"); results = []
        def add_from_text(text, node_id="", node_title=""):
            if not isinstance(text, str) or "http" not in text: return
            for filename, url in pattern.findall(text):
                source_subdir = self._infer_subdir_from_url(url); model_type = self._infer_model_type_from_link(filename.strip(), url.strip(), source_subdir)
                results.append({"filename": filename.strip(), "url": url.strip(), "node_id": node_id, "node_title": node_title, "source_subdir": source_subdir, "model_type": model_type, "is_direct": ("/resolve/" in url or url.lower().endswith(tuple(MODEL_FILE_EXTENSIONS)))})
        for node in iter_workflow_nodes(workflow):
            node_id, _, node_title = get_node_identity(node); add_from_text(node.get("title"), node_id, node_title)
            for value in (node.get("widgets_values") or []): add_from_text(value, node_id, node_title)
        add_from_text(workflow.get("extra", {}).get("notes", ""), "workflow", "workflow")
        return results
    def _infer_subdir_from_url(self, url):
        lower = url.lower()
        for marker in ["diffusion_models","loras","text_encoders","vae","clip","unet","checkpoints"]:
            if f"/{marker}/" in lower: return marker
        return ""
    def _infer_model_type_from_link(self, filename, url, source_subdir=""):
        mapping = self.config.get("source_subdir_to_model_type", {})
        if source_subdir and source_subdir in mapping: return mapping[source_subdir]
        value_l = filename.lower()
        if "lora" in value_l: return "loras"
        if "vae" in value_l: return "vae"
        if "clip" in value_l or "t5" in value_l or "text_encoder" in value_l or "umt5" in value_l: return "clip"
        if "diffusion" in value_l: return "diffusion_models"
        if "unet" in value_l: return "unet"
        return ""
    def _build_download_match(self, item, links):
        expected_norm = self.normalize_name(item["expected"]); direct_candidates = []; fuzzy_candidates = []
        for link in links:
            link_filename = link["filename"]
            if not self._looks_like_model_filename(link_filename): continue
            if link.get("model_type") and item["model_type"] and link["model_type"] != item["model_type"]: continue
            link_norm = self.normalize_name(link_filename)
            if link_norm == expected_norm: direct_candidates.append(link)
            else:
                score = self.matcher.score_pair(item["expected"], link_filename)
                if score >= 90:
                    enriched = dict(link); enriched["score"] = score; fuzzy_candidates.append(enriched)
        if direct_candidates:
            chosen = direct_candidates[0]; return {"filename": chosen["filename"], "url": chosen["url"], "source": "workflow_note", "source_subdir": chosen.get("source_subdir",""), "is_direct": bool(chosen.get("is_direct"))}
        if fuzzy_candidates:
            fuzzy_candidates.sort(key=lambda x: -x["score"]); chosen = fuzzy_candidates[0]
            return {"filename": chosen["filename"], "url": chosen["url"], "source": "workflow_note_fuzzy", "source_subdir": chosen.get("source_subdir",""), "is_direct": bool(chosen.get("is_direct")), "score": chosen["score"]}
        return None
    def scan_workflow(self, workflow):
        model_index = self.build_model_index(); note_links = self.extract_download_links(workflow); items = []
        for node in iter_workflow_nodes(workflow):
            node_id, node_type, node_title = get_node_identity(node)
            for widget_info in extract_node_widgets(node):
                widget_name = widget_info["name"]; widget_index = widget_info["index"]; widget_value = widget_info["value"]
                if not isinstance(widget_value, str) or not widget_value.strip(): continue
                model_type = self._detect_model_type(node_type, node_title, widget_name, widget_value)
                if not model_type: continue
                exists = self._exists_in_folder(model_type, widget_value); candidates = []; best = None; download = None
                if not exists:
                    matches = self.matcher.find_best_matches(widget_value, self._folder_candidates(model_type)); candidates = [asdict(m) for m in matches]; best = asdict(matches[0]) if matches else None; download = self._build_download_match({"expected": widget_value, "model_type": model_type}, note_links)
                items.append({"node_id": node_id, "node_type": node_type, "node_title": node_title, "widget_name": widget_name, "widget_index": widget_index, "expected": widget_value, "model_type": model_type, "exists": exists, "best_match": best, "candidates": candidates, "download": download})
        summary = {"total": len(items), "exists": sum(1 for x in items if x["exists"]), "missing": sum(1 for x in items if not x["exists"]), "auto_resolvable": sum(1 for x in items if (not x["exists"]) and x["best_match"] and x["best_match"]["score"] >= int(self.config.get("auto_apply_threshold",92))), "downloadable": sum(1 for x in items if (not x["exists"]) and x.get("download"))}
        return {"summary": summary, "items": items, "note_links": note_links, "model_index": {k: len(v) for k, v in model_index.items()}}
    def resolve_workflow(self, workflow, auto_apply_threshold=None):
        threshold = int(auto_apply_threshold or self.config.get("auto_apply_threshold",92)); new_workflow = clone_workflow(workflow); scan = self.scan_workflow(new_workflow); applied = []; unresolved = []; node_map = {str(node.get("id")): node for node in iter_workflow_nodes(new_workflow)}
        for item in scan["items"]:
            if item["exists"]: continue
            best = item.get("best_match")
            if not best or int(best.get("score",0)) < threshold: unresolved.append(item); continue
            node = node_map.get(str(item["node_id"]))
            if not node or not set_widget_value(node, int(item["widget_index"]), best["filename"]): unresolved.append(item); continue
            applied.append({"node_id": item["node_id"], "widget_name": item["widget_name"], "from": item["expected"], "to": best["filename"], "score": best["score"], "model_type": item["model_type"]})
        return {"workflow": new_workflow, "applied": applied, "unresolved": unresolved, "summary": {"applied_count": len(applied), "unresolved_count": len(unresolved), "threshold": threshold}, "scan": scan}
    def apply_selected_matches(self, workflow, selections):
        new_workflow = clone_workflow(workflow); applied = []; skipped = []; node_map = {str(node.get("id")): node for node in iter_workflow_nodes(new_workflow)}
        for sel in selections:
            node_id = str(sel.get("node_id", "")); widget_index = sel.get("widget_index"); filename = sel.get("filename")
            if not node_id or widget_index is None or not filename: skipped.append({"selection": sel, "reason": "missing node_id/widget_index/filename"}); continue
            node = node_map.get(node_id)
            if not node or not set_widget_value(node, int(widget_index), filename): skipped.append({"selection": sel, "reason": "apply failed"}); continue
            applied.append({"node_id": node_id, "widget_index": int(widget_index), "filename": filename})
        return {"workflow": new_workflow, "applied": applied, "skipped": skipped, "summary": {"applied_count": len(applied), "skipped_count": len(skipped)}}
