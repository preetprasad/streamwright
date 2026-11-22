"""Quality gate before enqueue; rules from ClipKaro clipper/qa.py (https://github.com/allthingsaman/ClipKaro @
b6d9401ac83fc695c8ce52795c14c25213a866fd, MIT), reimplemented here. Failed clips are kept but never enqueued."""
import asyncio
import json
import re
import subprocess
import tempfile
from pathlib import Path

from . import config as c
from .render import SAFE

LUFS, LUFS_TOL, MAX_TP = -14.0, 3.0, -1.0


def stats(path):
    """Loudness, true peak, black and frozen stretches in one ffmpeg pass."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vf",
                          "blackdetect=d=0.3:pix_th=0.10,freezedetect=n=0.002:d=2", "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    peak = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", err)
    return {"lufs": float(lufs[-1]) if lufs else None,
            "peak": float(peak[-1]) if peak and peak[-1] != "-inf" else None,
            "black": [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)],
            "frozen": re.findall(r"freeze_start: ([\d.]+)", err)}


def songs(path, duration, step=15.0, window=12.0):
    """Titles Shazam recognises in 12 s windows every 15 s. Sends those short snippets to Shazam (free, no key).
    Raises on network or API errors so the caller can fail closed."""
    from shazamio import Shazam
    sh, found = Shazam(), []
    with tempfile.TemporaryDirectory() as tmp:
        t = 0.0
        while t < max(duration - 3, 0.1):
            wav = Path(tmp) / f"{int(t)}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t}", "-t", f"{window}", "-i", str(path),
                            "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
            track = asyncio.run(sh.recognize(str(wav))).get("track")
            if track:
                found.append(f"{track.get('title')} - {track.get('subtitle')} at {t:.0f}s")
            t += step
    return found


def safe_zone_fails(boxes):
    x0, y0, x1, y1 = SAFE
    return [b for b in boxes if b[0] < x0 or b[1] < y0 or b[2] > x1 or b[3] > y1]


def check(path, meta, size=(1080, 1920), dur=(10, 60)):
    """-> list of failure reasons (empty = pass). size (w, h) and dur (min s, max s) default to the Short rules."""
    fail = []
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                                   "-show_entries", "format=duration", "-of", "json", str(path)],
                                  capture_output=True, text=True).stdout)
    kinds = [s["codec_type"] for s in j["streams"]]
    video = next((s for s in j["streams"] if s["codec_type"] == "video"), {})
    d = float(j["format"]["duration"])
    if (video.get("width"), video.get("height")) != tuple(size):
        fail.append(f"size {video.get('width')}x{video.get('height')} is not {size[0]}x{size[1]}")
    if "audio" not in kinds:
        fail.append("no audio")
    if not dur[0] <= d <= dur[1]:
        fail.append(f"duration {d:.1f}s outside {dur[0]}-{dur[1]}s")
    s = stats(path)
    if s["lufs"] is None or abs(s["lufs"] - LUFS) > LUFS_TOL:
        fail.append(f"loudness {s['lufs']} LUFS is more than {LUFS_TOL:.0f} LU from {LUFS:.0f}")
    if s["peak"] is not None and s["peak"] > MAX_TP:
        fail.append(f"true peak {s['peak']} dBTP above {MAX_TP}")
    if s["black"]:
        fail.append(f"black video for {sum(b - a for a, b in s['black']):.1f}s at {s['black'][0][0]:.1f}s")
    if s["frozen"]:
        fail.append(f"frozen picture (2s or more) at {s['frozen'][0]}s")
    out = safe_zone_fails(meta.get("caption_boxes", []))
    if out:
        fail.append(f"{len(out)} caption(s) outside the safe zone, e.g. {out[0]}")
    if c.b("MUSIC_CHECK"):  # copyrighted songs get Content ID claims (yellow dollar) and Twitch/TikTok mutes
        try:
            hit = songs(path, d)
            if hit:
                fail.append(f"copyrighted music: {hit[0]}")
        except Exception as e:  # fail closed: an unchecked clip is never uploaded
            fail.append(f"music check failed ({type(e).__name__}: {e})")
    return fail
