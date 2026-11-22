"""Persistent upload queue: one job per (clip, platform), retries, hourly cap, no double posting."""
import importlib
import json
import logging
import os
import threading
import time

from . import config as c

log = logging.getLogger("queue")
PLATFORMS = ("youtube", "tiktok", "instagram")
BACKOFF = (60, 300, 900)  # seconds after failure 1, 2, 3; the 4th failure is final
MAX_TRIES = 4


def uploader(name):
    return importlib.import_module(f"kit.uploaders.{name}")


def enabled_platforms():
    # DRY_RUN needs no creds: every platform is "enabled" so the whole flow can be rehearsed
    return [p for p in PLATFORMS if c.b("DRY_RUN") or uploader(p).enabled()]


class UploadQueue:
    def __init__(self):
        self.file = c.STATE / "queue.json"
        self.lock = threading.Lock()
        self.jobs = json.loads(self.file.read_text()) if self.file.exists() else {}

    def _save(self):  # atomic: write tmp + rename
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.jobs, indent=1))
        os.replace(tmp, self.file)

    def add(self, clip_id):
        with self.lock:
            for p in enabled_platforms():
                self.jobs.setdefault(f"{clip_id}:{p}", {"clip": clip_id, "platform": p, "status": "pending",
                                                        "tries": 0, "next_try": 0, "post_id": None})
            self._save()

    def _posted_last_hour(self, platform):
        return sum(1 for j in self.jobs.values() if j["platform"] == platform
                   and j["status"] in ("done", "dry-run") and j.get("done_at", 0) > time.time() - 3600)

    def run_once(self):
        with self.lock:
            for key, j in list(self.jobs.items()):
                if j["status"] != "pending" or j["next_try"] > time.time():
                    continue
                if self._posted_last_hour(j["platform"]) >= c.i("MAX_POSTS_PER_HOUR"):
                    continue
                self._attempt(key, j)
                self._save()

    def _attempt(self, key, j):
        mp4 = c.CLIPS / f"{j['clip']}.mp4"
        if c.b("DRY_RUN"):
            log.info("would upload %s %s", j["platform"], mp4.name)
            j.update(status="dry-run", done_at=time.time())
            return
        try:
            meta = json.loads(mp4.with_suffix(".json").read_text())
            j.update(post_id=uploader(j["platform"]).upload(meta, mp4), status="done", done_at=time.time())
            log.info("uploaded %s %s -> %s", j["platform"], mp4.name, j["post_id"])
            self._cleanup(j["clip"])
        except Exception as e:
            j["tries"] += 1
            j["error"] = c.mask(str(e))[:300]
            if j["tries"] >= MAX_TRIES:
                j["status"] = "failed"
            else:
                j["next_try"] = time.time() + BACKOFF[j["tries"] - 1]
            log.error("upload %s %s failed (try %d): %s", j["platform"], mp4.name, j["tries"], j["error"])

    def _cleanup(self, clip_id):
        mine = [j for j in self.jobs.values() if j["clip"] == clip_id]
        if c.b("DELETE_CLIPS_AFTER_UPLOAD") and all(j["status"] == "done" for j in mine):
            for ext in (".mp4", ".json"):
                (c.CLIPS / f"{clip_id}{ext}").unlink(missing_ok=True)


def worker(q, stop):
    while not stop.is_set():
        q.run_once()
        stop.wait(10)
