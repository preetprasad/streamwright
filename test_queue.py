"""Fast offline test of kit/queue.py with a fake uploader: python test_queue.py"""
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ["DRY_RUN"] = "false"
from kit import config as c, queue  # noqa: E402

queue.BACKOFF = (0, 0, 0)  # retries are due immediately
calls = []  # (clip, outcome)
plan = {}   # clip -> number of upload() calls that must fail first


def fake_upload(meta, path):
    clip = meta["id"]
    n = sum(1 for k, _ in calls if k == clip)
    ok = n >= plan.get(clip, 0)
    calls.append((clip, ok))
    if not ok:
        raise RuntimeError("boom")
    return f"post-{clip}"


queue.uploader = lambda name: SimpleNamespace(enabled=lambda: name == "youtube", upload=fake_upload)


def fresh(tmp):
    c.STATE, c.CLIPS = Path(tmp) / "state", Path(tmp) / "clips"
    c.STATE.mkdir(), c.CLIPS.mkdir()
    calls.clear(), plan.clear()
    os.environ["MAX_POSTS_PER_HOUR"] = "4"


def clip(cid):
    (c.CLIPS / f"{cid}.mp4").write_bytes(b"x")
    (c.CLIPS / f"{cid}.json").write_text(json.dumps({"id": cid}))


def case(fn):
    with tempfile.TemporaryDirectory() as tmp:
        fresh(tmp)
        fn()
    print("PASS", fn.__name__)


def test_retry_then_success():
    clip("a"), plan.update(a=1)
    q = queue.UploadQueue()
    q.add("a")
    q.run_once()
    j = q.jobs["a:youtube"]
    assert j["status"] == "pending" and j["tries"] == 1, j
    q.run_once()
    assert j["status"] == "done" and j["post_id"] == "post-a", j
    q.run_once()
    assert calls == [("a", False), ("a", True)], calls  # exactly one successful post
    assert list(q.jobs) == ["a:youtube"]


def test_restart_no_double_post():
    clip("a"), clip("b")
    q = queue.UploadQueue()
    q.add("a")
    q.run_once()
    q.add("b")  # pending when the "crash" happens
    q2 = queue.UploadQueue()  # restart: state reloaded from queue.json
    assert q2.jobs["a:youtube"]["status"] == "done" and q2.jobs["b:youtube"]["status"] == "pending"
    q2.run_once()
    q2.run_once()
    assert calls == [("a", True), ("b", True)], calls
    q2.add("a")  # dedupe: re-adding a done clip must not resurrect it
    q2.run_once()
    assert len(calls) == 2


def test_hourly_cap():
    os.environ["MAX_POSTS_PER_HOUR"] = "2"
    for k in "abc":
        clip(k)
    q = queue.UploadQueue()
    for k in "abc":
        q.add(k)
    q.run_once()
    q.run_once()
    assert len(calls) == 2 and q.jobs["c:youtube"]["status"] == "pending", calls
    for j in q.jobs.values():  # pretend those posts were over an hour ago
        if j["status"] == "done":
            j["done_at"] -= 3700
    q.run_once()
    assert len(calls) == 3 and q.jobs["c:youtube"]["status"] == "done"


def test_fails_four_times():
    clip("a"), plan.update(a=99)
    q = queue.UploadQueue()
    q.add("a")
    for _ in range(8):
        q.run_once()
    j = q.jobs["a:youtube"]
    assert j["status"] == "failed" and j["tries"] == 4 and len(calls) == 4, (j, calls)
    assert queue.UploadQueue().jobs["a:youtube"]["status"] == "failed"  # persisted, still not retried


for t in (test_retry_then_success, test_restart_no_double_post, test_hourly_cap, test_fails_four_times):
    case(t)
print("QUEUE TESTS PASSED")
