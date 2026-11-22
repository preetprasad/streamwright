"""mediamtx: RTMP ingest from OBS, rolling mpegts recording, built-in `forward:` to YouTube/Twitch."""
import logging
import os
import sys
import socket
import subprocess
import time

from . import config as c

log = logging.getLogger("relay")
CONF = c.STATE / "mediamtx.yml"


def destinations():
    """(name, 'rtmps://host/app#key') for each platform whose key is set."""
    out = []
    for name in ("YOUTUBE", "TWITCH"):
        key = c.s(f"{name}_STREAM_KEY")
        if key:
            out.append((name.lower(), f"{c.s(name + '_RTMP_URL').rstrip('/')}#{key}"))
    return out


def write_config():
    fwd = "".join(f"      - dest: {d}\n" for _, d in destinations())
    forward = f"    forward:\n{fwd}" if fwd else ""
    text = f"""logLevel: warn
api: no
metrics: no
pprof: no
playback: no
rtsp: no
hls: no
webrtc: no
srt: no
moq: no
rtmp: yes
rtmpAddress: 127.0.0.1:{c.i('OBS_INGEST_PORT')}
pathDefaults:
  recordFormat: mpegts
  recordPath: ./buffer/%path/%s
  recordSegmentDuration: 10s
  recordDeleteAfter: {c.i('BUFFER_MINUTES')}m
paths:
  live/obs:
    record: yes
    runOnReady: {sys.executable} -m kit.golive
{forward}  all_others:
"""
    c.STATE.mkdir(parents=True, exist_ok=True)
    CONF.write_text(text)  # contains stream keys: owner-only, never printed
    os.chmod(CONF, 0o600)


class Relay:
    def start(self):
        write_config()
        # stdout/stderr may echo forward URLs (with keys): mask every line before logging
        self.proc = subprocess.Popen(["mediamtx", str(CONF)], cwd=c.DATA, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True)
        import threading
        threading.Thread(target=self._pump, daemon=True).start()
        port = c.i("OBS_INGEST_PORT")
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError("mediamtx exited; is the port in use? see log above")
                time.sleep(0.2)
        log.info("mediamtx up: OBS -> rtmp://127.0.0.1:%d/live key obs; forwarding to: %s", port,
                 [n for n, _ in destinations()] or "nothing")

    def _pump(self):
        for line in self.proc.stdout:
            log.info("mediamtx: %s", c.mask(line.rstrip()))

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
