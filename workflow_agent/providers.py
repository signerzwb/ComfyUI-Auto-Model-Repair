"""Search adapters for public model catalog APIs."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp

from .security import SAFE_MODEL_EXTENSIONS
from .model_intelligence import source_priority


def _safe_extension(filename: str) -> bool:
    return Path(str(filename or "")).suffix.lower() in SAFE_MODEL_EXTENSIONS


def _hf_item(item: dict[str, Any]) -> dict[str, Any]:
    model_id = item.get("id") or item.get("modelId") or ""
    return {
        "provider": "huggingface",
        "id": model_id,
        "name": model_id,
        "type": item.get("pipeline_tag") or "model",
        "downloads": item.get("downloads") or 0,
        "likes": item.get("likes") or 0,
        "updated_at": item.get("lastModified"),
        "gated": bool(item.get("gated")),
        "preview": None,
        "page_url": f"https://huggingface.co/{model_id}",
        "base_model": None,
        "license": None,
    }


def _civitai_item(item: dict[str, Any]) -> dict[str, Any]:
    versions = item.get("modelVersions") or []
    first = versions[0] if versions else {}
    images = first.get("images") or []
    return {
        "provider": "civitai",
        "id": str(item.get("id") or ""),
        "name": item.get("name") or "",
        "type": item.get("type") or "model",
        "downloads": (item.get("stats") or {}).get("downloadCount") or 0,
        "likes": (item.get("stats") or {}).get("thumbsUpCount") or 0,
        "updated_at": item.get("updatedAt"),
        "gated": bool(item.get("allowNoCredit") is False),
        "preview": images[0].get("url") if images else None,
        "page_url": f"https://civitai.com/models/{item.get('id')}",
        "base_model": first.get("baseModel") or item.get("baseModel"),
        "license": item.get("allowCommercialUse"),
    }


def _modelscope_item(item: dict[str, Any]) -> dict[str, Any]:
    model_id = item.get("id") or item.get("Name") or item.get("name") or ""
    metadata = item.get("metadata") or item.get("Metadata") or {}
    return {
        "provider": "modelscope",
        "id": str(model_id),
        "name": str(item.get("name") or item.get("Name") or model_id),
        "type": str(item.get("task") or metadata.get("task") or "model"),
        "downloads": int(item.get("downloads") or item.get("Downloads") or 0),
        "likes": int(item.get("likes") or item.get("Likes") or 0),
        "updated_at": item.get("updated_at") or item.get("UpdatedTime"),
        "gated": bool(item.get("gated") or False),
        "preview": item.get("cover") or item.get("Cover"),
        "page_url": f"https://modelscope.cn/models/{model_id}",
        "base_model": metadata.get("base_model") or metadata.get("BaseModel"),
        "license": metadata.get("license") or item.get("license"),
    }


async def _get_json(session: aiohttp.ClientSession, url: str, **kwargs: Any) -> Any:
    async with session.get(url, **kwargs) as response:
        text = await response.text()
        if response.status >= 400:
            raise RuntimeError(f"模型站点返回 {response.status}: {text[:300]}")
        return await response.json(content_type=None)


async def search_models(query: str, provider: str = "all", limit: int = 12) -> dict[str, Any]:
    query = str(query or "").strip()
    if len(query) < 2:
        raise ValueError("搜索词至少需要两个字符")
    provider = str(provider or "all").lower()
    if provider not in {"all", "huggingface", "civitai", "modelscope"}:
        raise ValueError("不支持的模型来源")
    limit = max(1, min(int(limit), 20))
    timeout = aiohttp.ClientTimeout(total=35, connect=12)
    headers = {"User-Agent": "ComfyUI-Workflow-Agent/0.1"}
    errors: list[dict[str, str]] = []

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async def hf() -> list[dict[str, Any]]:
            token = os.environ.get("HF_TOKEN", "").strip()
            auth = {"Authorization": f"Bearer {token}"} if token else {}
            data = await _get_json(
                session,
                "https://huggingface.co/api/models",
                params={"search": query, "limit": limit, "sort": "downloads", "direction": "-1"},
                headers={**headers, **auth},
            )
            return [_hf_item(item) for item in data if isinstance(item, dict)]

        async def civitai() -> list[dict[str, Any]]:
            token = os.environ.get("CIVITAI_API_TOKEN", "").strip()
            auth = {"Authorization": f"Bearer {token}"} if token else {}
            data = await _get_json(
                session,
                "https://civitai.com/api/v1/models",
                params={"query": query, "limit": limit, "nsfw": "false", "sort": "Most Downloaded"},
                headers={**headers, **auth},
            )
            return [_civitai_item(item) for item in (data.get("items") or []) if isinstance(item, dict)]

        async def modelscope() -> list[dict[str, Any]]:
            token = os.environ.get("MODELSCOPE_TOKEN", "").strip()
            headers_ms = {"Authorization": f"Bearer {token}"} if token else {}
            # ModelScope's public API has changed names across releases.  A failure is
            # reported as an ordinary provider error and never blocks other catalogs.
            data = await _get_json(
                session,
                "https://modelscope.cn/openapi/v1/models",
                params={"name": query, "page_number": 1, "page_size": limit},
                headers={**headers, **headers_ms},
            )
            payload = data.get("data", data) if isinstance(data, dict) else data
            items = payload.get("models", payload.get("items", [])) if isinstance(payload, dict) else payload
            return [_modelscope_item(item) for item in items if isinstance(item, dict)]

        jobs: list[tuple[str, Any]] = []
        if provider in {"all", "huggingface"}:
            jobs.append(("huggingface", hf()))
        if provider in {"all", "civitai"}:
            jobs.append(("civitai", civitai()))
        if provider in {"all", "modelscope"}:
            jobs.append(("modelscope", modelscope()))
        values = await asyncio.gather(*(job for _, job in jobs), return_exceptions=True)
        results: list[dict[str, Any]] = []
        for (name, _), value in zip(jobs, values):
            if isinstance(value, Exception):
                errors.append({"provider": name, "message": str(value)})
            else:
                results.extend(value)
    # A large download count alone does not make a source best for a domestic
    # ComfyUI install.  Keep Comfy-Org's ModelScope releases first, followed
    # by ModelScope, Hugging Face and finally CivitAI as the fallback.
    results.sort(key=lambda item: (source_priority(item)[0], -int(item.get("downloads") or 0), -int(item.get("likes") or 0)))
    return {"results": results[: limit * len(jobs)], "errors": errors}


def _suggested_folder(model_type: str) -> str:
    value = str(model_type or "").lower()
    if "lora" in value:
        return "loras"
    if "textual" in value or "embedding" in value:
        return "embeddings"
    if "control" in value or "adapter" in value:
        return "controlnet"
    if "vae" in value:
        return "vae"
    if "upscal" in value:
        return "upscale_models"
    return "checkpoints"


def _modelscope_folder(path: str, fallback: str = "checkpoints") -> str:
    """Infer a ComfyUI target from an official repository path, not its title."""
    parts = [part.lower() for part in str(path or "").replace("\\", "/").split("/")]
    for folder in ("vae", "loras", "text_encoders", "diffusion_models", "controlnet", "clip_vision", "embeddings", "upscale_models"):
        if folder in parts:
            return folder
    return fallback


def _modelscope_download_url(model_id: str, path: str) -> str:
    """Use ModelScope's documented Hub download API, which also supports Range."""
    query = urlencode({"Revision": "master", "FilePath": path})
    return f"https://www.modelscope.cn/api/v1/models/{quote(model_id, safe='/')}/repo?{query}"


def _modelscope_file_items(payload: Any) -> list[dict[str, Any]]:
    """Normalise the legacy Hub file-tree response across public deployments."""
    if isinstance(payload, dict):
        payload = payload.get("Data", payload.get("data", payload.get("files", payload.get("Files", []))))
    if isinstance(payload, dict):
        payload = payload.get("Files", payload.get("files", payload.get("Items", payload.get("items", []))))
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


async def model_files(provider: str, model_id: str) -> dict[str, Any]:
    provider = str(provider or "").lower()
    model_id = str(model_id or "").strip()
    if not model_id or provider not in {"huggingface", "civitai", "modelscope"}:
        raise ValueError("模型来源或 ID 无效")
    timeout = aiohttp.ClientTimeout(total=35, connect=12)
    base_headers = {"User-Agent": "ComfyUI-Workflow-Agent/0.1"}
    async with aiohttp.ClientSession(timeout=timeout, headers=base_headers) as session:
        if provider == "huggingface":
            token = os.environ.get("HF_TOKEN", "").strip()
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            data = await _get_json(
                session,
                f"https://huggingface.co/api/models/{quote(model_id, safe='/')}",
                headers={**base_headers, **headers},
            )
            revision = data.get("sha") or "main"
            files = []
            for item in data.get("siblings") or []:
                filename = item.get("rfilename") or ""
                lfs = item.get("lfs") or {}
                oid = str(lfs.get("oid") or "")
                files.append(
                    {
                        "name": filename,
                        "size_bytes": lfs.get("size") or item.get("size"),
                        "safe": _safe_extension(filename),
                        "sha256": oid.removeprefix("sha256:") if oid else None,
                        "suggested_folder": "checkpoints",
                        "download_url": (
                            f"https://huggingface.co/{model_id}/resolve/{revision}/{quote(filename, safe='/')}"
                        ),
                    }
                )
            return {"provider": provider, "id": model_id, "files": files}

        if provider == "modelscope":
            token = os.environ.get("MODELSCOPE_TOKEN", "").strip()
            headers_ms = {"Authorization": f"Bearer {token}"} if token else {}
            # Model details do not contain the complete repository tree.  In
            # particular, Comfy-Org keeps VAE assets in nested folders.  The
            # Hub file API is recursive only with this exact capitalisation.
            data = await _get_json(
                session,
                f"https://www.modelscope.cn/api/v1/models/{quote(model_id, safe='/')}/repo/files",
                params={"Revision": "master", "Recursive": "True"},
                headers={**base_headers, **headers_ms},
            )
            files = _modelscope_file_items(data)
            return {
                "provider": provider,
                "id": model_id,
                "files": [
                    {
                        "name": item.get("Path") or item.get("path") or item.get("Name") or item.get("name") or "",
                        "size_bytes": item.get("size") or item.get("Size"),
                        "safe": _safe_extension(item.get("Path") or item.get("path") or item.get("Name") or item.get("name") or ""),
                        "sha256": item.get("sha256") or item.get("Sha256"),
                        "suggested_folder": _modelscope_folder(item.get("Path") or item.get("path") or item.get("Name") or item.get("name") or ""),
                        "download_url": item.get("download_url") or item.get("DownloadUrl") or _modelscope_download_url(model_id, str(item.get("Path") or item.get("path") or item.get("Name") or item.get("name") or "")),
                    }
                    for item in files if isinstance(item, dict)
                ],
            }

        token = os.environ.get("CIVITAI_API_TOKEN", "").strip()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        data = await _get_json(
            session,
            f"https://civitai.com/api/v1/models/{quote(model_id, safe='')}",
            headers={**base_headers, **headers},
        )
        suggested = _suggested_folder(data.get("type") or "")
        files = []
        for version in data.get("modelVersions") or []:
            version_url = version.get("downloadUrl")
            for item in version.get("files") or []:
                filename = item.get("name") or ""
                hashes = item.get("hashes") or {}
                scan_ok = item.get("virusScanResult") not in {"Danger", "Error"}
                scan_ok = scan_ok and item.get("pickleScanResult") not in {"Danger", "Error"}
                files.append(
                    {
                        "name": filename,
                        "version": version.get("name"),
                        "size_bytes": int(float(item.get("sizeKB") or 0) * 1024) or None,
                        "safe": _safe_extension(filename) and scan_ok,
                        "sha256": hashes.get("SHA256") or hashes.get("sha256"),
                        "suggested_folder": suggested,
                        "download_url": item.get("downloadUrl") or version_url,
                    }
                )
        return {"provider": provider, "id": model_id, "files": files}
