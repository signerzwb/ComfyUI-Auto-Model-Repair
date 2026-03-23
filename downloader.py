
import os
import threading
import time
import uuid
import urllib.request
from pathlib import Path


class DownloadManager:
    def __init__(self, service, plugin_dir, config):
        self.service = service
        self.plugin_dir = plugin_dir
        self.config = config
        self.tasks = {}
        self.lock = threading.Lock()
        self.chunk_size = int(config.get("download_chunk_size", 262144))

    def _apply_hf_mirror(self, url, use_hf_mirror):
        if use_hf_mirror and "huggingface.co" in url:
            return url.replace("https://huggingface.co", "https://hf-mirror.com")
        return url

    def create_task(self, payload):
        filename = payload.get("filename")
        url = payload.get("url")
        model_type = payload.get("model_type")

        if not filename or not url or not model_type:
            raise ValueError("missing filename/url/model_type")

        save_dir = self.service.get_primary_folder_path(model_type)
        if not save_dir:
            raise ValueError(f"unknown target folder for model_type: {model_type}")

        os.makedirs(save_dir, exist_ok=True)
        save_path = str(Path(save_dir) / filename)
        effective_url = self._apply_hf_mirror(
            url,
            bool(payload.get("use_hf_mirror", self.config.get("use_hf_mirror_default", False)))
        )

        task_id = str(uuid.uuid4())
        task = {
            "task_id": task_id,
            "filename": filename,
            "source_url": url,
            "effective_url": effective_url,
            "model_type": model_type,
            "node_id": str(payload.get("node_id", "")),
            "widget_index": payload.get("widget_index"),
            "save_dir": save_dir,
            "save_path": save_path,
            "status": "queued",
            "progress": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "speed_bytes": 0,
            "error": "",
            "cancel_requested": False,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

        with self.lock:
            self.tasks[task_id] = task

        threading.Thread(target=self._download_worker, args=(task_id,), daemon=True).start()
        return task

    def cancel_task(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return {"cancelled": False, "reason": "task not found"}

            if task.get("status") != "downloading":
                return {"cancelled": False, "reason": "not downloading"}

            task["cancel_requested"] = True
            task["updated_at"] = time.time()

        return {"cancelled": True, "task_id": task_id}

    def _download_worker(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task["status"] = "downloading"
            task["updated_at"] = time.time()

        tmp_path = task["save_path"] + ".part"
        last_time = time.time()
        last_bytes = 0

        try:
            req = urllib.request.Request(task["effective_url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = resp.headers.get("Content-Length")
                total = int(total) if total and total.isdigit() else 0

                with self.lock:
                    task["total_bytes"] = total

                with open(tmp_path, "wb") as f:
                    while True:
                        if task["cancel_requested"]:
                            raise RuntimeError("cancelled")

                        chunk = resp.read(self.chunk_size)
                        if not chunk:
                            break

                        f.write(chunk)
                        now = time.time()

                        with self.lock:
                            task["downloaded_bytes"] += len(chunk)
                            if task["total_bytes"] > 0:
                                task["progress"] = round(task["downloaded_bytes"] * 100.0 / task["total_bytes"], 2)

                            elapsed = max(now - last_time, 0.001)
                            current_bytes = task["downloaded_bytes"]
                            task["speed_bytes"] = int((current_bytes - last_bytes) / elapsed)
                            task["updated_at"] = now

                        if now - last_time >= 0.5:
                            last_time = now
                            last_bytes = current_bytes

            os.replace(tmp_path, task["save_path"])

            with self.lock:
                task["status"] = "completed"
                task["progress"] = 100.0

        except Exception as e:
            if str(e) == "cancelled":
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                with self.lock:
                    task["status"] = "cancelled"
            else:
                with self.lock:
                    task["status"] = "failed"
                    task["error"] = str(e)

    def list_tasks(self):
        with self.lock:
            tasks = [dict(v) for v in self.tasks.values()]
        return {"tasks": tasks}

    def remove_task(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return {"removed": False}

            if task.get("status") == "downloading":
                return {"removed": False, "reason": "still downloading"}

            self.tasks.pop(task_id, None)

        return {"removed": True}
