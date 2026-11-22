"""Post clips mined by replay_vod.py, best first, paced, until a deadline.

    .venv/bin/python scripts/post_replay.py [--gap 40] [--until "2026-11-20 20:30"]

YouTube: public Short each round; when the daily API quota runs out, waits for the Pacific-midnight reset.
TikTok: inbox draft each round; when TikTok refuses more pending drafts, retries next round.
Keeps going while replay_vod.py is still adding clips. Record of what went where: state/replay_posted.json.
"""
import argparse
import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kit import config as c  # noqa: E402
from kit.uploaders import tiktok, youtube  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--gap", type=float, default=40, help="minutes between posts")
ap.add_argument("--until", default="2026-11-20 20:30")
a = ap.parse_args()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("post")
ACC, REC = c.STATE / "replay_accepted.jsonl", c.STATE / "replay_posted.json"
APPROVED = c.STATE / "replay_approved.txt"  # one clip id per line
UNTIL = dt.datetime.strptime(a.until, "%Y-%m-%d %H:%M").timestamp()


def next_pacific_midnight():
    now = dt.datetime.now(ZoneInfo("America/Los_Angeles"))
    return (now + dt.timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0).timestamp()


def candidates():
    rows = [json.loads(l) for l in ACC.read_text().splitlines() if l.strip()] if ACC.exists() else []
    ok = set(APPROVED.read_text().split()) if APPROVED.exists() else set()  # reviewed by eye: the judge hallucinates
    rows = [r for r in rows if r["id"] in ok and (c.CLIPS / f"{r['id']}.mp4").exists()]
    return sorted(rows, key=lambda r: -r["score"])


def main():
    done = json.loads(REC.read_text()) if REC.exists() else {}
    yt_wait = tt_wait = 0
    fails = {}
    while time.time() < UNTIL:
        posted = False
        for plat, up in (("youtube", youtube), ("tiktok", tiktok)):
            if time.time() < (yt_wait if plat == "youtube" else tt_wait):
                continue
            todo = [r for r in candidates() if plat not in done.get(r["id"], {})]
            if not todo:
                continue
            r = todo[0]
            mp4 = c.CLIPS / f"{r['id']}.mp4"
            try:
                pid = up.upload(json.loads(mp4.with_suffix(".json").read_text()), mp4)
                done.setdefault(r["id"], {})[plat] = pid
                REC.write_text(json.dumps(done, indent=1))
                log.info("posted %s %s (%.1f) %s -> %s", plat, r["id"], r["score"], r["title"], pid)
                posted = True
            except Exception as e:
                msg = c.mask(str(e))[:300]
                log.error("%s %s failed: %s", plat, r["id"], msg)
                if plat == "youtube" and "quota" in msg.lower():
                    yt_wait = next_pacific_midnight()
                    log.info("youtube quota used up; next try %s", time.ctime(yt_wait))
                    continue
                if plat == "tiktok":
                    tt_wait = time.time() + 3600
                    if "pending_share" in msg:  # inbox full until the owner posts drafts: not this clip's fault
                        continue
                fails[(r["id"], plat)] = fails.get((r["id"], plat), 0) + 1
                if fails[(r["id"], plat)] >= 3:  # give up on this clip for this platform
                    done.setdefault(r["id"], {})[plat] = f"failed: {msg[:120]}"
                    REC.write_text(json.dumps(done, indent=1))
        time.sleep(a.gap * 60 if posted else 300)
    log.info("deadline reached")


main()
