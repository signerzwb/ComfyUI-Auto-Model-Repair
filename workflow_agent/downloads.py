"""Resumable, bounded-concurrency model downloads for the local ComfyUI server."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp

from .security import safe_model_filename, validate_download_url, validate_model_folder


_CONTENT_RANGE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.I)


def _content_range(response: Any) -> tuple[int, int, int | None] | None:
    """Parse a range response without assuming its HTTP status code."""
    match = _CONTENT_RANGE.match(str(response.headers.get("Content-Range") or "").strip())
    if not match:
        return None
    start, end, total = match.groups()
    return int(start), int(end), None if total == "*" else int(total)


def _auth_headers(provider: str, url: str) -> dict[str, str]:
    host = (urlparse(url).hostname or "").lower()
    token_name = {"huggingface": "HF_TOKEN", "civitai": "CIVITAI_API_TOKEN", "modelscope": "MODELSCOPE_TOKEN"}.get(provider)
    token = os.environ.get(token_name, "").strip() if token_name else ""
    return {"Authorization": f"Bearer {token}"} if token and host else {}


async def _open_download(session: aiohttp.ClientSession, source_url: str, provider: str, range_start: int):
    current = validate_download_url(source_url)
    for _ in range(6):
        headers = {"User-Agent": "ShenDuMao-Workflow-Doctor/1.0", **_auth_headers(provider, current)}
        if range_start:
            headers["Range"] = f"bytes={range_start}-"
        response = await session.get(current, headers=headers, allow_redirects=False)
        if response.status in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.release()
            if not location:
                raise RuntimeError("下载站点返回了无目标重定向")
            current = validate_download_url(urljoin(current, location))
            continue
        return response
    raise RuntimeError("下载重定向次数过多")


class DownloadManager:
    """A process-local queue. Partial files survive a restart and resume on requeue."""

    def __init__(self, folder_paths: Any, max_concurrency: int = 3):
        self._folder_paths = folder_paths
        self._max_concurrency = max(1, min(int(max_concurrency), 3))
        self._jobs: dict[str, dict[str, Any]] = {}
        self._targets: dict[str, str] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []

    def _public(self, job: dict[str, Any]) -> dict[str, Any]:
        fields = ("id", "filename", "folder", "status", "downloaded_bytes", "total_bytes", "resumed_bytes", "sha256", "error")
        return {field: job.get(field) for field in fields}

    async def enqueue(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(entries, list) or not entries:
            raise ValueError("下载任务不能为空")
        if len(entries) > 100:
            raise ValueError("单次最多提交 100 个下载任务")
        output = []
        for entry in entries:
            provider = str(entry.get("provider") or "").lower()
            if provider not in {"huggingface", "civitai", "modelscope"}:
                raise ValueError("模型来源无效")
            folder = validate_model_folder(entry.get("folder"))
            filename = safe_model_filename(entry.get("filename"))
            roots = self._folder_paths.get_folder_paths(folder)
            if not roots:
                raise RuntimeError("ComfyUI 没有配置目标模型目录")
            destination = Path(roots[0]).resolve() / filename
            key = str(destination).lower()
            existing = self._targets.get(key)
            if existing and existing in self._jobs:
                output.append(self._public(self._jobs[existing]))
                continue
            if destination.exists():
                raise FileExistsError(f"文件已存在：{filename}")
            part = destination.with_name(destination.name + ".part")
            resumed = part.stat().st_size if part.exists() else 0
            job = {
                "id": uuid.uuid4().hex,
                "provider": provider,
                "url": validate_download_url(entry.get("download_url")),
                "filename": filename,
                "folder": folder,
                "destination": destination,
                "part": part,
                "expected_hash": str(entry.get("sha256") or "").lower().removeprefix("sha256:"),
                "expected_size": int(entry.get("size_bytes") or 0),
                "status": "queued",
                "downloaded_bytes": resumed,
                "total_bytes": int(entry.get("size_bytes") or 0),
                "resumed_bytes": resumed,
                "sha256": None,
                "error": None,
            }
            self._jobs[job["id"]] = job
            self._targets[key] = job["id"]
            await self._queue.put(job["id"])
            output.append(self._public(job))
        self._ensure_workers()
        return output

    def _ensure_workers(self) -> None:
        self._workers = [worker for worker in self._workers if not worker.done()]
        while len(self._workers) < self._max_concurrency:
            self._workers.append(asyncio.create_task(self._worker()))

    async def _worker(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                job = self._jobs.get(job_id)
                if job and job["status"] == "queued":
                    await self._download(job)
            finally:
                self._queue.task_done()

    async def _download(self, job: dict[str, Any]) -> None:
        job["status"], job["error"] = "downloading", None
        max_bytes = int(os.environ.get("COMFYUI_WORKFLOW_DOCTOR_MAX_DOWNLOAD_GB", "40")) * 1024**3
        if job["expected_size"] and job["expected_size"] > max_bytes:
            job["status"], job["error"] = "failed", "模型文件超过当前下载大小限制"
            return
        destination, part = job["destination"], job["part"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        resume_at = part.stat().st_size if part.exists() else 0
        job["resumed_bytes"] = job["downloaded_bytes"] = resume_at
        timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=180)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                response = await _open_download(session, job["url"], job["provider"], resume_at)
                async with response:
                    if response.status >= 400:
                        raise RuntimeError(f"下载站点返回 {response.status}: {(await response.text())[:300]}")
                    ranged = _content_range(response)
                    # ModelScope replies 200 plus Content-Range for a valid
                    # resumed request.  Treat the verified byte start as the
                    # source of truth, as ordinary HTTP status alone loses
                    # otherwise valid partial downloads.
                    append = resume_at > 0 and bool(ranged and ranged[0] == resume_at)
                    if not append:
                        resume_at = 0
                        job["resumed_bytes"] = job["downloaded_bytes"] = 0
                    declared = int(response.headers.get("Content-Length") or 0)
                    total = (ranged[2] if ranged and ranged[2] is not None else (resume_at + declared if declared else job["expected_size"]))
                    if total and total > max_bytes:
                        raise ValueError("模型文件超过当前下载大小限制")
                    job["total_bytes"] = total
                    digest, written = hashlib.sha256(), resume_at
                    if append:
                        with part.open("rb") as handle:
                            for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                                digest.update(chunk)
                    with part.open("ab" if append else "wb") as handle:
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            written += len(chunk)
                            if written > max_bytes:
                                raise ValueError("模型文件超过当前下载大小限制")
                            digest.update(chunk)
                            handle.write(chunk)
                            job["downloaded_bytes"] = written
            if job["expected_size"] and written != job["expected_size"]:
                raise RuntimeError(f"文件大小校验失败：期望 {job['expected_size']}，实际 {written}")
            actual_hash = digest.hexdigest()
            if job["expected_hash"] and actual_hash != job["expected_hash"]:
                part.unlink(missing_ok=True)
                raise RuntimeError("SHA256 校验失败，临时文件已删除")
            os.replace(part, destination)
            job["downloaded_bytes"], job["total_bytes"], job["sha256"], job["status"] = written, written, actual_hash, "done"
        except Exception as exc:
            # Keep .part for retry; it is intentionally only removed after a
            # checksum mismatch, where retaining corrupted bytes is unsafe.
            job["status"], job["error"] = "failed", str(exc)[:500]

    def status(self, job_ids: list[str] | None = None) -> list[dict[str, Any]]:
        ids = [str(item) for item in job_ids] if job_ids else list(self._jobs)
        return [self._public(self._jobs[item]) for item in ids if item in self._jobs]
