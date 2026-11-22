import json
import logging
import signal
import threading
import time

from . import chat as chatmod, config as c, detect, judge, judge_claude, qa, queue, render, stream_meta
from .relay import Relay

log = logging.getLogger("kit")


def decide(cid, clip, ctx, cues):
    """Local verdict, optionally Claude (shadow = log only, on = decides). -> (verdict or None, judge name)."""
    v = judge.local(clip, ctx, cues)
    if v is None:
        return None, "local"
    judge.log_record(id=cid, judge="local", **v)
    mode = c.s("CLAUDE_JUDGE")
    if mode in ("shadow", "on") and v["clip_score"] >= c.f("CLAUDE_JUDGE_MIN_LOCAL"):
        cv = judge_claude.judge_clip(cid, clip, ctx, cues)
        if cv:
            judge.log_record(id=cid, judge="claude", **cv)
            if mode == "on":
                return cv, "claude"
    return v, "local"


def make_processor(chat, q):
    def process(peak):
        """Cut -> transcribe + snap -> judge -> render -> QA -> enqueue. True when a clip was accepted."""
        cid = time.strftime("%Y%m%d-%H%M%S", time.localtime(peak))
        start = peak - c.f("PRE_ROLL")
        end = min(peak + c.f("POST_ROLL"), start + 60)
        try:
            prep = judge.prepare(cid, start, end)
            if prep is None:
                return False
            clip, start, end, words, after = prep
            ctx, cues = judge.context(words, after, chat.lines(start, end))
            v, by = decide(cid, clip, ctx, cues)
            if v is None or not judge.accepted(v):
                judge.log_record(id=cid, decision="reject", by=by, clip_score=v and v["clip_score"])
                return False
            t0 = time.time()
            out = render.render(cid, clip, v, words, (start, end), peak)
            log.info("render %s took %.1fs", cid, time.time() - t0)
            fails = qa.check(out, json.loads(out.with_suffix(".json").read_text()))
            if fails:
                log.warning("clip %s failed QA, not enqueued: %s", cid, fails)
                judge.log_record(id=cid, decision="qa_fail", reasons=fails)
                return True  # judged good: keeps the cooldown so the same moment is not retried
            judge.log_record(id=cid, decision="accept", by=by, clip_score=v["clip_score"])
            q.add(cid)
            log.info("clip %s accepted (%s %.1f): %s", cid, by, v["clip_score"], v["title"])
            return True
        except Exception:
            log.exception("processing %s failed", cid)
            return False
        finally:
            if not c.b("KEEP_TMP"):
                for p in c.TMP.glob(f"{cid}*"):
                    p.unlink(missing_ok=True)
    return process


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    c.ensure_dirs()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    relay, chat, q = Relay(), chatmod.Chat(), queue.UploadQueue()
    relay.start()
    chatmod.start(chat, stop)
    threads = [threading.Thread(target=detect.run, args=(stop, chat, make_processor(chat, q))),
               threading.Thread(target=queue.worker, args=(q, stop))]
    if c.s("STREAM_META") == "auto":
        threads.append(threading.Thread(target=stream_meta.auto))
    for t in threads:
        t.daemon = True
        t.start()
    log.info("running (DRY_RUN=%s). Ctrl-C to stop.", c.b("DRY_RUN"))
    try:
        stop.wait()
    finally:
        stop.set()
        relay.stop()
        for t in threads:
            t.join(5)
        log.info("stopped")


main()
