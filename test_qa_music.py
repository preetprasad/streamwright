"""Offline check of the clip music gate: a recognised song or a failed lookup blocks the upload."""
import subprocess
import tempfile
from pathlib import Path

from kit import qa

with tempfile.TemporaryDirectory() as tmp:
    clip = Path(tmp) / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:d=20", "-f", "lavfi",
                    "-i", "anoisesrc=d=20:a=0.05", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(clip)], check=True)
    music = lambda: [f for f in qa.check(clip, {}) if "music" in f]
    qa.songs = lambda p, d: []
    assert music() == [], music()
    qa.songs = lambda p, d: ["Song - Artist at 15s"]
    assert music() == ["copyrighted music: Song - Artist at 15s"], music()
    def offline(p, d): raise ConnectionError("offline")
    qa.songs = offline
    assert music() and "music check failed" in music()[0], music()
print("ok")
