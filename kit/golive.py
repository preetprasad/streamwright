"""Go-live announcements: Discord post, then the social promo (kit.promo). mediamtx runs this (runOnReady) when OBS starts publishing.

    python -m kit.golive [--force]

One post per stream: skipped if the last one was under DISCORD_LIVE_COOLDOWN_MIN ago (OBS reconnects
re-trigger runOnReady). DISCORD_LIVE_PING (e.g. "@everyone") is prepended and allowed to ping; nothing
else in the message can.
"""
import json
import sys
import time
import urllib.request

from . import config as c

STAMP = c.STATE / "golive_last"


def message():
    meta = {}
    try:
        meta = json.loads((c.STATE / "stream_meta.json").read_text())
    except (OSError, ValueError):
        pass
    title = meta.get("title") or f"{c.s('STREAM_GAME') or 'Stream'} live now"
    where = [f"{n}: <{c.s(k)}>" for n, k in (("YouTube", "LINK_YOUTUBE"), ("Twitch", "LINK_TWITCH")) if c.s(k)]
    ping = c.s("DISCORD_LIVE_PING").strip()
    return "\n".join(x for x in (ping, f"🔴 **LIVE NOW**: {title}", *where) if x)


def post(force=False):
    cool = c.i("DISCORD_LIVE_COOLDOWN_MIN") * 60
    if not force and STAMP.exists() and time.time() - STAMP.stat().st_mtime < cool:
        return "skipped: posted recently"
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.touch()  # before the slow promo, so an OBS reconnect meanwhile can't post twice
    out = discord()
    try:
        from . import promo
        return f"discord: {out}\npromo: {promo.run()}"
    except Exception as e:
        return f"discord: {out}\npromo failed: {c.mask(str(e))}"


def discord():
    url = c.s("DISCORD_WEBHOOK_URL").strip()
    if not url:
        return "no DISCORD_WEBHOOK_URL"
    ping = c.s("DISCORD_LIVE_PING").strip()
    allowed = {"parse": ["everyone"] if ping in ("@everyone", "@here") else []}
    body = json.dumps({"content": message(), "allowed_mentions": allowed}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json", "User-Agent": "gta-stream-kit"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except OSError as e:
        return "failed: " + c.mask(str(e)).replace(url, "***")
    return "posted"


if __name__ == "__main__":
    print(post(force="--force" in sys.argv))
