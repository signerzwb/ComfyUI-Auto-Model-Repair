"""Context-aware model requirements and compatibility scoring."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .analyzer import normalize_links


_MODEL_WORDS = {
    "checkpoint": "checkpoints",
    "lora": "loras",
    "vae": "vae",
    "text_encoder": "text_encoders",
    "clip": "text_encoders",
    "unet": "diffusion_models",
    "diffusion": "diffusion_models",
    "controlnet": "controlnet",
    "clip_vision": "clip_vision",
    "upscale": "upscale_models",
}
_MODEL_EXTENSIONS = {".safetensors", ".sft", ".gguf", ".ckpt", ".pt", ".pth", ".bin"}
_FAMILIES = ("sdxl", "sd3", "sd1.5", "flux", "wan", "ltx", "hunyuan", "qwen", "sana", "zimage")


def source_priority(candidate: dict[str, Any]) -> tuple[int, str]:
    """Prefer locally accessible, officially maintained ComfyUI releases."""
    provider = str(candidate.get("provider") or "").lower()
    identity = str(candidate.get("id") or candidate.get("name") or "").lower()
    if provider == "modelscope" and (identity.startswith("comfy-org/") or "/comfy-org/" in identity):
        return 0, "魔塔 · Comfy-Org 官方发布"
    if provider == "modelscope":
        return 1, "魔塔社区"
    if provider == "huggingface":
        return 2, "Hugging Face"
    if provider == "civitai":
        return 3, "CivitAI"
    return 9, provider or "未知来源"


def _normalize(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def model_family(*values: Any) -> str | None:
    haystack = " ".join(str(value or "").lower() for value in values)
    if "sd 1.5" in haystack or "sd15" in haystack or "sd1.5" in haystack:
        return "sd1.5"
    for family in _FAMILIES:
        if family in haystack:
            return family
    return None


def _role_from_text(*values: Any) -> str | None:
    text = " ".join(str(value or "").lower() for value in values).replace("-", "_")
    for word, role in _MODEL_WORDS.items():
        if word in text:
            return role
    return None


def _is_model_filename(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return Path(value.replace("\\", "/")).suffix.lower() in _MODEL_EXTENSIONS


def _widgets(node: dict[str, Any], schema: dict[str, Any] | None) -> list[tuple[str, Any]]:
    values = node.get("widgets_values") or []
    if not isinstance(values, list):
        return []
    names: list[str] = []
    required = (schema or {}).get("inputs", {}).get("required", {})
    optional = (schema or {}).get("inputs", {}).get("optional", {})
    for source in (required, optional):
        if isinstance(source, dict):
            names.extend(str(name) for name in source)
    return [(names[index] if index < len(names) else f"widget_{index}", value) for index, value in enumerate(values)]


def extract_model_requirements(
    workflow: dict[str, Any], node_catalog: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Extract model references with node context instead of only filename heuristics."""
    node_catalog = node_catalog or {}
    links = normalize_links(workflow)
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    for link in links:
        origin, target = str(link.get("origin_id")), str(link.get("target_id"))
        outgoing[origin].append(target)
        incoming[target].append(origin)
    nodes = {str(node.get("id")): node for node in workflow.get("nodes") or [] if isinstance(node, dict)}
    requirements: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for node_id, node in nodes.items():
        node_type = str(node.get("type") or "")
        schema = node_catalog.get(node_type)
        title = str(node.get("title") or "")
        surrounding = " ".join(
            str(nodes.get(other, {}).get("type") or "") for other in incoming[node_id] + outgoing[node_id]
        )
        for index, (widget_name, value) in enumerate(_widgets(node, schema)):
            role = _role_from_text(widget_name, node_type, title)
            if not (_is_model_filename(value) or role):
                continue
            if not isinstance(value, str) or not value.strip() or value.strip().lower() in {"none", "null", "default", "auto"}:
                continue
            if not _is_model_filename(value) and len(value) > 180:
                continue
            marker = (node_id, index)
            if marker in seen:
                continue
            seen.add(marker)
            expected = value.replace("\\", "/").strip()
            requirements.append(
                {
                    "key": f"{node_id}:{index}",
                    "node_id": node_id,
                    "node_type": node_type,
                    "node_title": title,
                    "widget_index": index,
                    "widget_name": widget_name,
                    "expected": expected,
                    "role": role or _role_from_text(expected) or "unknown",
                    "family": model_family(expected, node_type, title, surrounding),
                    "upstream_node_types": [str(nodes.get(other, {}).get("type") or "") for other in incoming[node_id]],
                    "downstream_node_types": [str(nodes.get(other, {}).get("type") or "") for other in outgoing[node_id]],
                }
            )
    # Embedded workflow metadata is authoritative when present.
    for node in nodes.values():
        for item in (node.get("properties") or {}).get("models", []) or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            requirements.append(
                {
                    "key": f"metadata:{node.get('id')}:{item.get('name')}",
                    "node_id": str(node.get("id")),
                    "node_type": str(node.get("type") or ""),
                    "node_title": str(node.get("title") or ""),
                    "widget_index": None,
                    "widget_name": "models metadata",
                    "expected": str(item.get("name")),
                    "role": str(item.get("directory") or _role_from_text(item.get("name")) or "unknown"),
                    "family": model_family(item.get("name"), item.get("url")),
                    "metadata_url": item.get("url"),
                    "sha256": item.get("hash"),
                    "upstream_node_types": [],
                    "downstream_node_types": [],
                }
            )
    return requirements


def group_dependency_bundles(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Combine related missing files so a workflow does not download only half a model family."""
    bundles: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for requirement in requirements:
        family = requirement.get("family") or "unknown"
        # Connected loader nodes with no known family still stay in their own group.
        key = (family, str(requirement.get("node_id")))
        bundles[key].append(requirement)
    result = []
    for (family, anchor), entries in bundles.items():
        roles = sorted({str(item.get("role") or "unknown") for item in entries})
        result.append(
            {
                "bundle_id": f"{family}:{anchor}",
                "family": family,
                "roles": roles,
                "requirements": entries,
                "complete_hint": "需要确认同一阶段的模型、编码器和 VAE 是否齐全。"
                if {"diffusion_models", "text_encoders", "vae"}.intersection(roles)
                else "单文件或独立模型依赖。",
            }
        )
    return result


def local_model_index() -> dict[str, list[str]]:
    try:
        import folder_paths
    except ImportError:
        return {}
    index: dict[str, list[str]] = {}
    for folder in getattr(folder_paths, "folder_names_and_paths", {}):
        try:
            index[str(folder)] = list(folder_paths.get_filename_list(folder))
        except Exception:
            continue
    return index


def _string_score(left: str, right: str) -> float:
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    overlap = len(set(a) & set(b))
    return overlap / max(len(set(a) | set(b)), 1)


def local_candidates(requirement: dict[str, Any], index: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    index = index if index is not None else local_model_index()
    expected = str(requirement.get("expected") or "")
    role = str(requirement.get("role") or "")
    family = requirement.get("family")
    preferred = role if role in index else None
    pools = [(preferred, index.get(preferred, []))] if preferred else list(index.items())
    candidates = []
    for folder, names in pools:
        for name in names:
            score = _string_score(expected, name)
            candidate_family = model_family(name)
            if family and candidate_family and family != candidate_family:
                score *= 0.35
            if Path(name).name == Path(expected).name:
                score = 1.0
            if score >= 0.28:
                candidates.append(
                    {
                        "filename": name,
                        "folder": folder,
                        "family": candidate_family,
                        "score": round(score * 100),
                        "exact": Path(name).name == Path(expected).name,
                        "source": "local",
                    }
                )
    return sorted(candidates, key=lambda item: (item["exact"], item["score"]), reverse=True)[:12]


def summarize_model_requirements(
    workflow: dict[str, Any], node_catalog: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    requirements = extract_model_requirements(workflow, node_catalog)
    index = local_model_index()
    for item in requirements:
        item["local_candidates"] = local_candidates(item, index)
        item["status"] = "available" if any(candidate["exact"] for candidate in item["local_candidates"]) else "missing"
    return {
        "requirements": requirements,
        "bundles": group_dependency_bundles(requirements),
        "summary": {
            "total": len(requirements),
            "available": sum(1 for item in requirements if item["status"] == "available"),
            "missing": sum(1 for item in requirements if item["status"] == "missing"),
        },
    }


def score_online_candidate(requirement: dict[str, Any], candidate: dict[str, Any], environment: dict[str, Any] | None = None) -> dict[str, Any]:
    """Hard constraints first; Agent explanations are added later, never replace this score."""
    name = str(candidate.get("name") or candidate.get("filename") or "")
    score = _string_score(requirement.get("expected"), name) * 55
    expected_family, candidate_family = requirement.get("family"), model_family(name, candidate.get("base_model"), candidate.get("type"))
    compatible = True
    reasons = []
    if expected_family and candidate_family:
        if expected_family == candidate_family:
            score += 30
            reasons.append("模型家族匹配")
        else:
            compatible = False
            score -= 45
            reasons.append(f"模型家族不匹配：需要 {expected_family}，候选为 {candidate_family}")
    expected_role = str(requirement.get("role") or "")
    candidate_role = _role_from_text(candidate.get("type"), name) or "unknown"
    if expected_role != "unknown" and candidate_role == expected_role:
        score += 15
        reasons.append("模型类型匹配")
    elif expected_role != "unknown" and candidate_role != "unknown":
        compatible = False
        score -= 25
        reasons.append(f"模型类型不匹配：需要 {expected_role}，候选为 {candidate_role}")
    size = int(candidate.get("size_bytes") or 0)
    free_disk = int((environment or {}).get("free_disk_bytes") or 0)
    if size and free_disk and size > free_disk:
        compatible = False
        reasons.append("磁盘空间不足")
    return {
        **candidate,
        "family": candidate_family,
        "compatibility_score": max(0, min(100, round(score))),
        "compatible": compatible,
        "reasons": reasons or ["根据名称和公开元数据匹配，仍需 Agent 复核。"],
    }
