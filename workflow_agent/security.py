"""Security boundaries shared by network and filesystem features."""

from __future__ import annotations

import re
import json
import os
import unicodedata
from pathlib import Path
from urllib.parse import urlparse


SAFE_MODEL_EXTENSIONS = {".safetensors", ".sft", ".gguf"}
ALLOWED_MODEL_FOLDERS = {
    "checkpoints",
    "loras",
    "vae",
    "text_encoders",
    "diffusion_models",
    "clip_vision",
    "embeddings",
    "controlnet",
    "upscale_models",
}
_TRUSTED_DOWNLOAD_DOMAINS = (
    "huggingface.co",
    "hf.co",
    "xethub.hf.co",
    "civitai.com",
    "civitai.green",
    "modelscope.cn",
    "modelscope.ai",
    "hf-mirror.com",
)
_BAD_FILENAME_CHARS = re.compile(r"[<>:\"/\\|?*\x00-\x1f]+")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def configured_mirror_bases() -> dict[str, list[str]]:
    """Read mirrors from an environment variable without accepting per-request hosts."""
    raw = os.environ.get("COMFYUI_WORKFLOW_DOCTOR_MIRRORS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, list[str]] = {}
    for provider, values in data.items():
        if not isinstance(values, list):
            continue
        safe = []
        for value in values[:10]:
            parsed = urlparse(str(value or ""))
            if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
                safe.append(parsed.geturl().rstrip("/"))
        if safe:
            result[str(provider).lower()] = safe
    return result


def trusted_download_domains() -> set[str]:
    domains = set(_TRUSTED_DOWNLOAD_DOMAINS)
    for bases in configured_mirror_bases().values():
        for base in bases:
            host = (urlparse(base).hostname or "").lower().rstrip(".")
            if host:
                domains.add(host)
    return domains


def validate_download_url(value: str) -> str:
    parsed = urlparse(str(value or ""))
    if parsed.scheme.lower() != "https":
        raise ValueError("只允许 HTTPS 下载地址")
    if parsed.username or parsed.password:
        raise ValueError("下载地址不能包含登录信息")
    if parsed.port not in (None, 443):
        raise ValueError("下载地址不能使用自定义端口")
    host = (parsed.hostname or "").lower().rstrip(".")
    trusted = any(host == domain or host.endswith("." + domain) for domain in trusted_download_domains())
    if not trusted:
        raise ValueError("下载来源不在受信任站点列表中")
    return parsed.geturl()


def safe_model_filename(value: str) -> str:
    name = Path(unicodedata.normalize("NFKC", str(value or ""))).name
    name = _BAD_FILENAME_CHARS.sub("_", name).strip(" .")
    if not name or name.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        raise ValueError("模型文件名无效")
    if len(name) > 180:
        suffix = Path(name).suffix
        name = name[: 180 - len(suffix)] + suffix
    if Path(name).suffix.lower() not in SAFE_MODEL_EXTENSIONS:
        raise ValueError("第一版只允许 safetensors、sft 和 gguf 权重")
    return name


def validate_model_folder(value: str) -> str:
    folder = str(value or "")
    if folder not in ALLOWED_MODEL_FOLDERS:
        raise ValueError("目标模型目录不受支持")
    return folder
