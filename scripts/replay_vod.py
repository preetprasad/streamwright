"""Mine a finished stream recording for Shorts: the live pipeline (detect -> judge -> render -> QA), run offline.

    .venv/bin/python scripts/replay_vod.py "<recording.mkv>" [--threshold 2.0] [--cooldown 45] [--min-score 6]

Recording start time comes from the OBS file name (YYYY-MM-DD HH-MM-SS). Chat is not replayed (audio only).
Accepted clips land in clips/ and are listed in state/replay_accepted.jsonl; nothing is uploaded here.
Moments within 60 s of a clip already in clips/ are skipped, and the script resumes where it stopped.
"""
import argparse
import datetime as dt
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ap = argparse.ArgumentParser()
ap.add_argument("rec")
ap.add_argument("--threshold", default="2.0")
ap.add_argument("--cooldown", default="45")
ap.add_argument("--min-score", default="6")
ap.add_argument("--fresh", action="store_true", help="ignore the resume point (new pass with other settings)")
a = ap.parse_args()
os.environ.update(CANDIDATE_THRESHOLD=a.threshold, COOLDOWN_SECONDS=a.cooldown, JUDGE_MIN_SCORE=a.min_score)

from kit import config as c, detect, judge, qa, render  # noqa: E402  (env must be set first)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("replay")
REC = Path(a.rec)
T0 = dt.datetime.strptime(REC.stem, "%Y-%m-%d %H-%M-%S").timestamp()
OUT = c.STATE / "replay_accepted.jsonl"
DONE = c.STATE / "replay_done.json"


def cut(cid, start, end):
    """Cut [start, end] (epoch s) straight from the recording, first audio track (the one streamed)."""
    out = c.TMP / f"{cid}.mp4"
    judge.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(start - T0, 0):.2f}", "-i", str(REC),
               "-t", f"{end - start:.2f}", "-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "14", "-c:a", "aac", "-b:a", "192k", str(out)])
    return out


judge.cut = cut  # prepare() looks cut up at call time


def rms():
    af = ("aresample=16000,asetnsamples=16000,astats=metadata=1:reset=1,"
          "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-")
    cache = c.STATE / f"replay_rms_{int(T0)}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(REC), "-map", "0:a:0", "-af", af, "-f", "null", "-"],
                         capture_output=True, text=True).stdout
    vals = []
    for line in out.splitlines():
        if "RMS_level=" in line:
            try:
                vals.append(max(float(line.split("=")[1]), -90.0))
            except ValueError:
                vals.append(-90.0)
    cache.write_text(json.dumps(vals))
    return vals


def existing_windows():
    wins = []
    for p in c.CLIPS.glob("*.json"):
        try:
            wins.append(json.loads(p.read_text())["window"])
        except (KeyError, ValueError):
            pass
    return wins


def judged_peaks():
    """Peaks a replay pass already judged (verdicts.jsonl ids are local peak times)."""
    out = set()
    for line in open(c.STATE / "verdicts.jsonl"):
        r = json.loads(line)
        if r.get("replay"):
            out.add(time.mktime(time.strptime(r["id"], "%Y%m%d-%H%M%S")))
    return out


JUDGED = set()


def process(peak):
    cid = time.strftime("%Y%m%d-%H%M%S", time.localtime(peak))
    if any(abs(peak - p) < 20 for p in JUDGED):
        log.info("skip %s: judged in an earlier pass", cid)
        return False
    start, end = peak - c.f("PRE_ROLL"), min(peak + c.f("POST_ROLL"), peak - c.f("PRE_ROLL") + 60)
    if any(w[0] - 60 < peak < w[1] + 60 for w in existing_windows()):
        log.info("skip %s: overlaps an existing clip", cid)
        return False
    try:
        prep = judge.prepare(cid, start, end)
        if prep is None:
            return False
        clip, start, end, words, after = prep
        ctx, cues = judge.context(words, after, [])
        v = judge.local(clip, ctx, cues)
        if v is None:
            return False
        judge.log_record(id=cid, judge="local", replay=True, **v)
        if not judge.accepted(v):
            log.info("reject %s (%.1f) %s", cid, v["clip_score"], v["title"])
            return False
        out = render.render(cid, clip, v, words, (start, end), peak)
        fails = qa.check(out, json.loads(out.with_suffix(".json").read_text()))
        if fails:
            log.warning("QA fail %s: %s", cid, fails)
            return True
        with open(OUT, "a") as fh:
            fh.write(json.dumps({"id": cid, "score": v["clip_score"], "title": v["title"]}) + "\n")
        log.info("ACCEPT %s (%.1f): %s", cid, v["clip_score"], v["title"])
        return True
    except Exception:
        log.exception("processing %s failed", cid)
        return False
    finally:
        for p in c.TMP.glob(f"{cid}*"):
            p.unlink(missing_ok=True)


def main():
    c.ensure_dirs()
    JUDGED.update(judged_peaks())
    vals = rms()
    log.info("recording %s: %d s of audio, start %s", REC.name, len(vals), time.ctime(T0))
    resume = 0 if a.fresh else (json.loads(DONE.read_text()).get(str(int(T0)), 0) if DONE.exists() else 0)
    det = detect.Detector()
    for k, r in enumerate(vals):
        peak = det.step(T0 + k, r)
        if peak is not None and peak - T0 >= resume and process(peak):
            det.last_accept = peak
        if peak is not None:
            state = json.loads(DONE.read_text()) if DONE.exists() else {}
            state[str(int(T0))] = peak - T0
            DONE.write_text(json.dumps(state))
    log.info("replay finished")


main()
