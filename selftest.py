"""End-to-end test, no platform creds, no real network. Runs ~5 minutes (4-minute synthetic stream, real time)."""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / ".selftest"
COOLDOWN = 45
BURSTS = (150, 185, 220)  # seconds into the stream; 185 falls inside the cooldown of 150
CAM = "x=1500:y=700:w=360:h=270"  # fake facecam: a bright box bottom-right
KEY_YT, KEY_TW = "ytsecretkey123", "twsecretkey456"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ffprobe(path, entries, stream="v:0"):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", entries,
                        "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
    return json.loads(r.stdout or "{}")


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        raise SystemExit(f"selftest failed: {msg}")


def stream_cmd(port):
    vol = "+".join(f"between(t,{b},{b + 3})" for b in BURSTS)
    return ["ffmpeg", "-v", "error", "-re", "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30,drawbox={CAM}:color=white:t=fill",
            "-re", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
            "-af", f"volume=0.01,volume='1+40*({vol})':eval=frame", "-t", "240",
            "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "3M", "-g", "60", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-f", "flv", f"rtmp://127.0.0.1:{port}/live/obs"]


def unit_checks():
    from kit import judge
    from kit.third_party.clipkaro import facecam
    check(judge.clip_score(dict.fromkeys(judge.WEIGHTS, 5)) == 10.0, "rubric: all 5s -> 10.0")
    check(judge.clip_score({"hook": 5, "payoff": 5, "emotion": 0, "share": 0, "quote": 0}) == 5.5, "rubric: hook=payoff=5 -> 5.5")
    check(facecam.detector(640, 360) is not None, "YuNet model loads")
    words = [(0, 1, "a"), (1.2, 2, "word"), (2.05, 3, "b")]
    check(judge.snap(1.5, words) == 1.1 and judge.snap(1.15, words) == 1.15, "cut snap: inside a word moves to the gap, in a gap stays")


def main():
    os.environ["DATA_DIR"] = str(DATA)  # kit modules imported in this process must use the test folder too
    unit_checks()
    shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir()
    port = free_port()
    try:
        ollama = requests.get("http://localhost:11434/api/tags", timeout=3).ok
    except requests.RequestException:
        ollama = False
    env = {**os.environ, "DATA_DIR": str(DATA), "OBS_INGEST_PORT": str(port), "DRY_RUN": "true",
           "JUDGE_MIN_SCORE": "0", "JUDGE_BYPASS": "" if ollama else "true", "COOLDOWN_SECONDS": str(COOLDOWN),
           "BUFFER_MINUTES": "5", "KEEP_TMP": "true", "CAPTIONS": "true", "FACECAM_BOX": "1500,700,360,270",
           # YouTube stand-in: a second path on the same mediamtx. Twitch: dead port.
           "YOUTUBE_RTMP_URL": f"rtmp://127.0.0.1:{port}/yt", "YOUTUBE_STREAM_KEY": KEY_YT,
           "TWITCH_RTMP_URL": "rtmp://127.0.0.1:1/app", "TWITCH_STREAM_KEY": KEY_TW,
           "TWITCH_CHANNEL": "", "YOUTUBE_LIVE_URL": "", "IG_USER_ID": "", "IG_ACCESS_TOKEN": ""}
    print(f"ollama up: {ollama}; port {port}")
    log = open(DATA / "kit.log", "w")
    kit = subprocess.Popen([sys.executable, "-m", "kit"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    stream = None
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        stream = subprocess.Popen(stream_cmd(port))
        time.sleep(40)
        segs = list((DATA / "buffer/live/obs").glob("*.ts"))
        check(len(segs) >= 2, f"recorded segments appear in buffer ({len(segs)})")
        yt = ffprobe(f"rtmp://127.0.0.1:{port}/yt/{KEY_YT}", "stream=codec_name", "v:0")
        check(bool(yt.get("streams")), "forward isolation: YouTube stand-in receives data while Twitch dest is dead")
        stream.wait()
        qf = DATA / "state/queue.json"  # wait for the last candidate to finish judge + render + dry-run upload
        for _ in range(60):
            time.sleep(5)
            jobs = json.loads(qf.read_text()).values() if qf.exists() else []
            if len(jobs) >= 6 and all(j["status"] != "pending" for j in jobs):
                break
    finally:
        if stream and stream.poll() is None:
            stream.terminate()
        kit.send_signal(signal.SIGINT)
        try:
            kit.wait(15)
        except subprocess.TimeoutExpired:
            kit.kill()
        log.close()

    clips = sorted((DATA / "clips").glob("*.mp4"))
    check(len(clips) >= 1, f"at least 1 clip rendered ({len(clips)})")
    peaks = []
    for p in clips:
        v = ffprobe(p, "stream=codec_name,width,height")["streams"][0]
        a = ffprobe(p, "stream=codec_name", "a:0")["streams"]
        d = float(ffprobe(p, "format=duration")["format"]["duration"])
        check((v["codec_name"], v["width"], v["height"]) == ("h264", 1080, 1920) and a and 10 <= d <= 60,
              f"{p.name}: h264 1080x1920 + audio, {d:.1f}s")
        peaks.append(json.loads(p.with_suffix(".json").read_text())["peak"])
    check(all(b - a >= COOLDOWN for a, b in zip(peaks, peaks[1:])), f"cooldown respected, peak gaps {[round(b - a) for a, b in zip(peaks, peaks[1:])]}")
    check(len(clips) == 2, "burst at ~185s suppressed by cooldown (2 clips from 3 bursts)")
    q = json.loads((DATA / "state/queue.json").read_text())
    check(len(q) == 3 * len(clips) and all(j["status"] == "dry-run" for j in q.values()), f"queue has dry-run entries ({len(q)})")
    out = (DATA / "kit.log").read_text()
    check(KEY_YT not in out and KEY_TW not in out, "stream keys never appear in kit log")
    conf = DATA / "state/mediamtx.yml"
    check(oct(conf.stat().st_mode)[-3:] == "600", "mediamtx.yml is chmod 600")
    verdicts = [json.loads(l) for l in (DATA / "state/verdicts.jsonl").read_text().splitlines()]
    check(len(verdicts) >= 1, f"verdicts logged ({len(verdicts)})")
    print("judge verdict sample:", json.dumps(verdicts[0]))

    check(all(json.loads(p.with_suffix(".json").read_text())["layout"] == "facecam" for p in clips), "clips used the facecam stack layout")
    # word captions, facecam stack and QA, on a kept tmp clip with fake words
    os.environ["FACECAM_BOX"] = "1500,700,360,270"
    from kit import config, qa, render
    src = next(p for p in (DATA / "state/tmp").glob("*.mp4") if "_ext" not in p.name)
    words = [(1, 1.4, "No"), (1.4, 1.9, "way"), (2.0, 2.5, "that"), (2.5, 3.1, "just"), (3.1, 3.8, "happened!"),
             (5, 5.5, "CONGRATULATIONSSSS"), (5.5, 6, "this"), (6, 6.4, "is"), (6.4, 7, "much"), (7, 7.5, "longer")]
    config.CLIPS.mkdir(exist_ok=True)
    t0 = time.time()
    cap = render.render("captiontest", src, {"title": "t", "caption": "c", "hashtags": [], "score": 1, "reason": ""},
                        words, (0, 28), 0)
    print(f"render time for a 28 s clip with {len(words)} word captions: {time.time() - t0:.1f} s")
    meta = json.loads(cap.with_suffix(".json").read_text())
    check(ffprobe(cap, "stream=width,height")["streams"][0]["height"] == 1920 and meta["layout"] == "facecam", "facecam stack renders 1080x1920")
    pngs = list((DATA / "state/tmp").glob("captiontest_cap*.png"))
    check(len(pngs) == len(words) and meta["caption_boxes"] and not qa.safe_zone_fails(meta["caption_boxes"]),
          f"{len(pngs)} word-caption PNGs exist and lie in the safe zone")
    check(bool(qa.safe_zone_fails([[0, 0, 10, 10]])), "qa safe-zone check rejects an out-of-zone box")
    black = DATA / "black.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30,drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,4,7)'",
                    "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000", "-t", "12", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(black)], check=True)
    why = qa.check(black, {})
    check(any("black" in r for r in why), f"QA rejects a clip with 3 s of black ({why})")
    check(qa.check(clips[0], json.loads(clips[0].with_suffix(".json").read_text())) == [], "QA passes a rendered clip")
    from kit.third_party.gta_highlights.taxonomy import classify_text
    check(any(q.category == "chase" for q in classify_text("cops are chasing us")), "taxonomy: 'cops are chasing us' -> chase")
    shutil.rmtree(DATA, ignore_errors=True)
    print("SELFTEST PASSED")


if __name__ == "__main__":
    main()
