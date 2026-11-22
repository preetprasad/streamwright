"""Offline: golive posts once, then the cooldown skips; --force posts again. Promo agent call is faked. No network."""
import os, tempfile
from pathlib import Path
from kit import golive, promo, config as c

sent, calls = [], []
golive.urllib.request.urlopen = lambda req, timeout=0: (sent.append(req.data), type("R", (), {"read": lambda s: b""})())[1]
os.environ["DISCORD_WEBHOOK_URL"] = "https://example.invalid/hook"
os.environ["LIVE_PROMO"] = "false"
golive.STAMP = Path(tempfile.mkdtemp()) / "golive_last"
assert golive.post() == "discord: posted\npromo: off (LIVE_PROMO=false)" and len(sent) == 1
assert golive.post() == "skipped: posted recently" and len(sent) == 1
assert golive.post(force=True).startswith("discord: posted") and len(sent) == 2
assert b'"parse": []' in sent[0]  # no pings unless DISCORD_LIVE_PING is @everyone/@here

# promo on: one headless Claude-in-Chrome run on the configured model, prompt carries the files and links
os.environ["LIVE_PROMO"] = "true"
promo.OUT = Path(tempfile.mkdtemp())
promo.video = lambda: promo.OUT / "live.mp4"
promo.subprocess.run = lambda cmd, **kw: (calls.append((cmd, kw["input"])), type("R", (), {"stdout": "youtube = posted", "stderr": ""})())[1]
assert golive.post(force=True).endswith("promo: youtube = posted")
cmd, prompt = calls[0]
assert cmd[:4] == ["claude", "-p", "--chrome", "--model"] and cmd[4] == c.s("PROMO_MODEL")
assert str(promo.OUT / "live.mp4") in prompt and "{" not in prompt.split("TEXT_YT")[1]
print("ok")
