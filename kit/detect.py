"""Find strong moments: per-second audio loudness + chat rate, z-scored against a rolling baseline."""
import logging
import statistics
import subprocess
from collections import deque

from . import config as c

log = logging.getLogger("detect")
PEAK_SEARCH = 5  # seconds after the first threshold crossing in which the peak is chosen
WARMUP = 120     # seconds of data needed before anything can fire


def robust_z(x, hist):
    med = statistics.median(hist)
    mad = statistics.median(abs(v - med) for v in hist)
    return (x - med) / (1.4826 * mad + 1.0)  # +1 (dB / msg) floor so flat input can't give infinite z


class Detector:
    def __init__(self, chat_fn=lambda t: 0.0):
        self.chat_fn = chat_fn
        self.audio, self.chat, self.recent = deque(maxlen=600), deque(maxlen=600), deque(maxlen=5)
        self.seen = 0
        self.last_accept = self.lock_until = float("-inf")
        self.pend = None  # [first_t, peak_t, peak_score]

    def step(self, t, rms):
        """Feed one second. Returns the peak time once POST_ROLL seconds after it have been seen."""
        self.recent.append(self.chat_fn(t))
        smooth = sum(self.recent) / len(self.recent)
        warm = self.seen >= WARMUP
        a_z = robust_z(rms, self.audio) if self.audio else 0.0
        c_z = robust_z(smooth, self.chat) if self.chat else 0.0
        score = c.f("W_AUDIO") * a_z + c.f("W_CHAT") * c_z
        self.audio.append(rms)
        self.chat.append(smooth)
        self.seen += 1
        if not warm:
            return None
        if self.pend is None:
            if (score >= c.f("CANDIDATE_THRESHOLD") and t >= self.lock_until
                    and t - self.last_accept >= c.f("COOLDOWN_SECONDS")):
                self.pend = [t, t, score]
            return None
        if t <= self.pend[0] + PEAK_SEARCH and score > self.pend[2]:
            self.pend[1:] = [t, score]
        if t >= max(self.pend[0] + PEAK_SEARCH, self.pend[1] + c.f("POST_ROLL")):
            peak = self.pend[1]
            log.info("candidate at %.0f (score %.1f)", peak, self.pend[2])
            self.pend = None
            self.lock_until = peak + c.f("PRE_ROLL") + c.f("POST_ROLL")  # don't re-fire on the same event
            return peak
        return None


def rms_per_second(path):
    af = ("aresample=16000,asetnsamples=16000,astats=metadata=1:reset=1,"
          "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-")
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-af", af, "-f", "null", "-"],
                         capture_output=True, text=True).stdout
    vals = []
    for line in out.splitlines():
        if "RMS_level=" in line:
            try:
                vals.append(max(float(line.split("=")[1]), -90.0))
            except ValueError:
                vals.append(-90.0)  # "-inf" / nan = digital silence
    return vals


def seg_start(p):
    return int(p.stem)


def segments():
    return sorted(c.BUFFER.glob("*.ts"), key=seg_start)


def run(stop, chat, handle):
    """Poll the buffer; handle(peak) -> True if a clip was accepted (starts the cooldown)."""
    det, done = Detector(chat.weighted), set()
    while not stop.is_set():
        for p in segments()[:-1]:  # the newest file is still being written
            if p.name in done:
                continue
            done.add(p.name)
            for k, rms in enumerate(rms_per_second(p)):
                peak = det.step(seg_start(p) + k, rms)
                if peak is not None and handle(peak):
                    det.last_accept = peak
        stop.wait(5)
