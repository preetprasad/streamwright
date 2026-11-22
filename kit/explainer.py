"""Countdown tips video from one OBS recording: python -m kit.explainer RECORDING.mkv [--items N] [--no-shorts] [--out DIR]
Transcript (whisper) -> script (claude -p) -> narration (local voice clone) -> long.mp4 + shorts/ -> qa.json. Never uploads."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import config as c, game_style, judge, judge_claude, qa, render

W, H, FPS, GAP, TAIL, MARGIN = 1920, 1080, 30, 0.25, 0.4, 96
CAP = dict(size=66, cw=1400, ch=180, line=78, stroke=6)  # 16:9 caption canvas, bottom centre
CAP_XY = ((W - CAP["cw"]) // 2, 730)
ENC = ["-c:v", "h264_videotoolbox", "-b:v", "10M", "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k",
       "-ar", "48000", "-ac", "2", "-movflags", "+faststart"]
RULES = """Write a YouTube "{n} things to do first in GTA 6" countdown video script from the stream transcript below.
Structure (copy the shape, never any existing video's words): a punchy cold-open hook of 3-5 short lines, a promise line
("{n} things to do first"), a tease ("and number {n}, you can't undo"), then per item "Number k, <tip title>." plus 1-3 short
lines; a subscribe line after item 2; a one-line retention bridge about every 3 items; the teased item LAST; a short outro
CTA (subscribe, live on YouTube and Twitch, Discord in the description). Second person, plain words, 8-14 words per sentence.
HARD RULES: every tip must be grounded in something the streamer did or said in the transcript; cite the transcript range it
comes from as clip start/end seconds (4-25 s long each, inside the recording, no two overlapping). Never invent game
mechanics, prices or Rockstar facts the transcript does not support. Framing tips as first-timer lessons ("I learned this
the hard way") is good. No copyrighted lyrics. No official Rockstar or brand claims.
Reply with ONE JSON object only, no prose, exactly: {{"title": str (<=70 chars), "description": str, "hook": [str],
"promise": str, "tease": str, "items": [{{"n": int, "title": str (<=32 chars), "lines": [str], "callout": str or null
(short label like "TRACKER: PULL IT AT A FENCE"), "clip": {{"start": s, "end": s}}, "short_hook": str}}],
"bridges": {{"<item n it follows>": str}}, "subscribe": str, "outro": [str]}}. Exactly {n} items numbered 1..{n}.
Recording length {dur:.0f} s. Transcript lines are "[start-end] text" in seconds:
"""


def sh(cmd, **kw):
    r = subprocess.run([str(x) for x in cmd], capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError(f"{cmd[0]} failed: {c.mask(r.stderr)[-400:]}")
    return r.stdout


def log(*a): print("[explainer]", *a, file=sys.stderr, flush=True)


# ---- 1-3: transcript and script ------------------------------------------------------------------------------

def transcript(rec, work):
    """Timestamped lines of the mic track (track 2, else track 1 with a warning), trimmed to ~12k words."""
    n = sh(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", rec]).split()
    if len(n) < 2:
        log("WARNING: no audio track 2 (mic only); using track 1 (stream mix), the transcript will include game audio")
    wav = work / "mic.wav"
    sh(["ffmpeg", "-y", "-v", "error", "-i", rec, "-map", f"0:a:{1 if len(n) > 1 else 0}", "-ac", "1", "-ar", "16000", wav])
    words, lines, cur = judge.transcribe("explainer_mic", wav), [], []
    for w in words + [(1e9, 1e9, "")]:
        if cur and (w[0] - cur[-1][1] > 0.7 or len(cur) >= 14):
            lines.append(f"[{cur[0][0]:.1f}-{cur[-1][1]:.1f}] " + " ".join(x[2] for x in cur))
            cur = []
        cur.append(w)
    return lines[:max(1, 12000 // 12)]  # ponytail: ~12 words per line, so the first ~12k words; no smart trimming


def ask(prompt):
    m = c.s("EXPLAINER_MODEL").lower()
    if m not in judge_claude.ALLOWED_MODELS:
        sys.exit(f"EXPLAINER_MODEL must be one of {judge_claude.ALLOWED_MODELS}, got {m!r}")
    r = subprocess.run(["claude", "-p", "--model", m, "--output-format", "json", "--safe-mode", "--tools", "",
                        "--system-prompt", "You write video scripts. Reply with the JSON object only.",
                        "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"],
                       input=prompt, capture_output=True, text=True, timeout=900)
    env = json.loads(r.stdout)
    if r.returncode or env.get("is_error"):
        raise RuntimeError(f"claude failed: {str(env.get('result') or r.stderr)[:300]}")
    txt = env["result"]
    return json.loads(txt[txt.index("{"):txt.rindex("}") + 1])


def validate(s, n, dur):
    e, items = [], s.get("items") or []
    if len(items) != n or [i.get("n") for i in items] != list(range(1, n + 1)):
        e.append(f"need exactly {n} items numbered 1..{n} in order")
    for k in ("title", "description", "promise", "tease", "subscribe"):
        if not isinstance(s.get(k), str) or not s[k].strip():
            e.append(f"{k} must be a non-empty string")
    if len(str(s.get("title"))) > 70:
        e.append("title over 70 chars")
    for k in ("hook", "outro"):
        if not s.get(k) or not all(isinstance(x, str) for x in s[k]):
            e.append(f"{k} must be a non-empty list of strings")
    clips = []
    for i in items:
        k = i.get("n")
        if not i.get("lines") or not isinstance(i.get("title"), str) or len(i["title"]) > 32:
            e.append(f"item {k}: needs lines and a title of at most 32 chars")
        try:
            a, b = float(i["clip"]["start"]), float(i["clip"]["end"])
        except (KeyError, TypeError, ValueError):
            e.append(f"item {k}: clip.start/end missing")
            continue
        if not 0 <= a < b <= dur or not 4 <= b - a <= 25:
            e.append(f"item {k}: clip {a}-{b} must be 4-25 s long and inside 0-{dur:.0f} s")
        clips.append((a, b, k))
    clips.sort()
    e += [f"items {x[2]} and {y[2]} clips overlap" for x, y in zip(clips, clips[1:]) if y[0] < x[1]]
    return e


def write_script(rec, work, n, dur):
    prompt = RULES.format(n=n, dur=dur) + "\n".join(transcript(rec, work))
    s = ask(prompt)
    errs = validate(s, n, dur)
    if errs:
        log("script invalid, retrying once:", errs)
        s = ask(prompt + "\n\nYour previous JSON had these problems, fix them:\n- " + "\n- ".join(errs))
        errs = validate(s, n, dur)
        if errs:
            sys.exit("script still invalid after one retry: " + "; ".join(errs))
    (work / "script.json").write_text(json.dumps(s, indent=1))
    return s


# ---- 4: narration -----------------------------------------------------------------------------------------

def narration(s, shorts, work):
    """{text: (wav, duration, [(start, end, WORD)])} for every distinct line. Cached by lines.txt."""
    texts = list(dict.fromkeys(
        s["hook"] + [s["promise"], s["tease"], s["subscribe"]] + s["outro"] + list(s.get("bridges", {}).values()) +
        [f"Number {i['n']}, {i['title']}." for i in s["items"]] + [x for i in s["items"] for x in i["lines"]] +
        ([i.get("short_hook") or f"GTA 6 tip number {i['n']}:" for i in s["items"]] if shorts else [])))
    texts = [re.sub(r"\s+", " ", t).strip() for t in texts if str(t).strip()]
    d = work / "lines"
    d.mkdir(exist_ok=True)
    lst = d / "lines.txt"
    if not (lst.exists() and lst.read_text() == "\n".join(texts) and len(list(d.glob("*.wav"))) == len(texts)):
        lst.write_text("\n".join(texts))
        log(f"voice: {len(texts)} lines (clone model loads once; ~10-30 s per line)")
        sh([c.s("VOICE_PYTHON"), Path(c.s("VOICE_DIR")) / "say.py", "--lines", lst, d])
    out = {}
    for k, t in enumerate(texts):
        wav = d / f"{k:03d}.wav"
        dur = judge.duration(wav)
        words = [(a, b, w) for a, b, w in judge.transcribe(f"explainer_ln{k}", wav)]
        if not words:  # ponytail: whisper heard nothing, spread the script words evenly over the line
            ws = t.split()
            words = [(dur * j / len(ws), dur * (j + 1) / len(ws), w) for j, w in enumerate(ws)]
        out[t] = (wav, dur, words)
    return out


def join(texts, nar):
    """Lines of one segment -> (wavs, words with segment-relative times, narration seconds)."""
    wavs, words, t = [], [], 0.0
    for x in texts:
        wav, dur, ws = nar[re.sub(r"\s+", " ", x).strip()]
        wavs.append(wav)
        words += [(a + t, b + t, w) for a, b, w in ws]
        t += dur + GAP
    return wavs, words, t


# ---- 5: pictures -----------------------------------------------------------------------------------------

def rgb(h, a=255): return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) + (a,)


def txt(d, xy, s, f, fill=(255, 255, 255, 255), anchor="la", stroke=3):
    if stroke:
        d.text((xy[0] + 4, xy[1] + 4), s, font=f, fill=(0, 0, 0, 200), anchor=anchor)  # hard drop shadow
    d.text(xy, s, font=f, fill=fill, stroke_width=stroke, stroke_fill="black", anchor=anchor)


def fit(path, s, size, maxw):
    f = render.font(size, path)
    while f.getlength(s) > maxw and size > 30:
        size -= 4
        f = render.font(size, path)
    return f


def badge_png(st, n, title, out):
    im = Image.new("RGBA", (1500, 120))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 8, 104, 112), 24, fill=st["accent"], outline=(0, 0, 0, 255), width=4)
    txt(d, (52, 60), str(n), render.font(80), st["dark"], "mm", 0)
    txt(d, (130, 60), title.upper(), fit(render.FONT, title.upper(), 68, 1340), anchor="lm")
    im.save(out)


def pill_png(st, text, out):
    f, text = render.font(38), text.upper()
    w = int(f.getlength(text)) + 56
    im = Image.new("RGBA", (w, 72))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, 71), 20, fill=(10, 4, 30, 190), outline=st["accent"], width=3)
    d.text((28, 36), text, font=f, fill=(255, 255, 255, 255), anchor="lm")
    im.save(out)


def tag_png(out):
    f = render.font(26)
    im = Image.new("RGBA", (int(f.getlength("GTA 6 · STREAM FOOTAGE")) + 8, 40))
    ImageDraw.Draw(im).text((4, 20), "GTA 6 · STREAM FOOTAGE", font=f, fill=(255, 255, 255, 215), anchor="lm", stroke_width=2, stroke_fill="black")
    im.save(out)


def card_png(st, n, title, frame, out):
    """Chapter card: blurred frame, dark gradient, big number + title."""
    im = Image.open(frame).convert("RGB").resize((W, H)).filter(ImageFilter.GaussianBlur(26)).convert("RGBA")
    mask = Image.linear_gradient("L").resize((W, H)).point(lambda v: 130 + v // 2)
    im.paste(Image.new("RGBA", (W, H), st["dark"][:3] + (255,)), mask=mask)
    d = ImageDraw.Draw(im)
    txt(d, (W // 2, 400), f"{n}", render.font(420), st["accent"], "mm")
    txt(d, (W // 2, 740), title.upper(), fit(render.FONT, title.upper(), 110, W - 2 * 160), anchor="mm")
    im.convert("RGB").save(out)


def cap_track(words):
    frames, spans = [], []
    for ch in render.chunks(words, render.FONT, CAP["size"], CAP["cw"], CAP["stroke"]):
        for i, w in enumerate(ch):
            frames.append(render.chunk_img([x[2] for x in ch], i, render.FONT, accent=st_accent, **CAP).tobytes())
            spans.append([w[0], ch[i + 1][0] if i + 1 < len(ch) else w[1] + 0.25, len(frames) - 1])
    for a, b in zip(spans, spans[1:]):
        a[1] = min(a[1], b[0])
    return frames, spans


# ---- 5: render --------------------------------------------------------------------------------------------

def seg(out, src, wavs, track, dur, base, ovs, cap, loud=False):
    """One mp4 piece: looped footage + captions piped as raw RGBA + PNG overlays [(png, 'x:y', enable)] + ducked game audio + narration."""
    cw, ch, cx, cy = cap
    frames, spans = track
    words = bool(spans)
    inputs = ["-stream_loop", "-1", "-i", src]
    if words:
        inputs += ["-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{cw}x{ch}", "-r", str(FPS), "-i", "pipe:0"]
    k = 2 if words else 1
    f = base + (f";[v0][1:v]overlay={cx}:{cy}[v1]" if words else ";[v0]null[v1]")
    last = 1
    for png, xy, en in ovs:
        inputs += ["-loop", "1", "-i", png]
        f += f";[v{last}][{k}:v]overlay={xy}" + (f":enable='{en}'" if en else "") + f"[v{last + 1}]"
        k, last = k + 1, last + 1
    for w in wavs:
        inputs += ["-i", w]
    a = "".join(f"[{k + j}:a]aresample=48000,aformat=channel_layouts=stereo,apad=pad_dur={GAP}[n{j}];" for j in range(len(wavs)))
    f += (f";{a}" + "".join(f"[n{j}]" for j in range(len(wavs))) + f"concat=n={len(wavs)}:v=0:a=1[nar];"
          "[0:a]volume=-20dB[g];[g][nar]amix=inputs=2:normalize=0:duration=longest" +
          (",loudnorm=I=-14:TP=-1.5:LRA=11" if loud else "") + "[a]")
    cmd = ["ffmpeg", "-y", "-v", "error"] + inputs + ["-filter_complex", f, "-map", f"[v{last}]", "-map", "[a]",
                                                      "-t", f"{dur:.2f}"] + ENC + [str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE if words else None, stderr=subprocess.PIPE)
    if words:
        try:
            render.feed(p, frames, spans, dur, cw, ch)
            p.stdin.close()
        except BrokenPipeError:
            pass  # ffmpeg hit -t first: normal
    err = p.stderr.read().decode(errors="replace")
    if p.wait():
        raise RuntimeError(f"ffmpeg failed for {out}: {c.mask(err)[-400:]}")


def still(out, png, dur):
    sh(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-i", png, "-f", "lavfi", "-i",
        "anullsrc=r=48000:cl=stereo", "-t", str(dur)] + ENC + [out])


def cut(rec, clip, out):
    """Footage piece, 1920x1080 30 fps with the stream-mix audio, so it can loop to any narration length."""
    sh(["ffmpeg", "-y", "-v", "error", "-ss", clip["start"], "-t", float(clip["end"]) - float(clip["start"]), "-i", rec,
        "-map", "0:v:0", "-map", "0:a:0?", "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", out])




def build(rec, work, s, nar, shorts):
    global st_accent
    pack = game_style.get()
    ff = c.ROOT / pack.get("font_file", "")
    st = {"font": ff if ff.is_file() else render.FONT, "accent": rgb(pack["accent_gradient"][0]), "dark": rgb(pack["palette"][0])}
    st_accent = st["accent"]
    t, items = work / "tmp", s["items"]
    t.mkdir(exist_ok=True)
    cuts = {}
    for i in items:
        cuts[i["n"]] = t / f"cut{i['n']:02d}.mp4"
        cut(rec, i["clip"], cuts[i["n"]])
    tag_png(t / "tag.png")
    tag = (t / "tag.png", f"W-w-{MARGIN}:H-h-{MARGIN}", None)
    base = f"[0:v]setsar=1,fps={FPS},format=yuv420p[v0]"
    cap = (CAP["cw"], CAP["ch"], *CAP_XY)
    parts = []

    def piece(name, src, texts, ovs):
        wavs, words, dur = join(texts, nar)
        seg(t / f"{name}.mp4", src, wavs, cap_track(words), dur + TAIL, base, [tag] + ovs, cap)
        parts.append(t / f"{name}.mp4")

    piece("hook", cuts[1], s["hook"] + [s["promise"], s["tease"]], [])  # ponytail: first clip as the "strongest"; rank clips if it matters
    for i in items:
        n = i["n"]
        sh(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", cuts[n], "-frames:v", "1", t / f"f{n}.png"])
        card_png(st, n, i["title"], t / f"f{n}.png", t / f"card{n}.png")
        still(t / f"card{n}.mp4", t / f"card{n}.png", 1.0)
        parts.append(t / f"card{n}.mp4")
        badge_png(st, n, i["title"], t / f"badge{n}.png")
        ovs = [(t / f"badge{n}.png", f"{MARGIN}:64", None)]
        if i.get("callout"):
            pill_png(st, i["callout"], t / f"pill{n}.png")
            ovs.append((t / f"pill{n}.png", f"{MARGIN}:620", "between(t,1.0,3.5)"))
        extra = [s["bridges"][str(n)]] if str(n) in s.get("bridges", {}) else []
        piece(f"item{n}", cuts[n], [f"Number {n}, {i['title']}."] + i["lines"] + extra + ([s["subscribe"]] if n == 2 else []), ovs)
    piece("outro", cuts[items[-1]["n"]], s["outro"], [])
    (t / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
    long = work / "long.mp4"
    sh(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", t / "list.txt", "-c:v", "copy", "-af",
        "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", long])
    meta = []
    if shorts:
        (work / "shorts").mkdir(exist_ok=True)
        meta = [short(work, t, s, i, cuts[i["n"]], nar) for i in items]
    return long, meta


def short(work, t, s, i, src, nar):
    n = i["n"]
    wavs, words, dur = join([i.get("short_hook") or f"GTA 6 tip number {n}:"] + i["lines"], nar)
    dur = max(dur + TAIL, 15.5)  # ponytail: Shorts under 15 s are padded with looping footage, not cut shorter
    frames, spans, boxes, top = render.caption_track(f"expl{n}", words, 1300)
    base = f"{render.BLUR_FILL},setsar=1,fps={FPS},format=yuv420p[v0]"
    out = work / "shorts" / f"item_{n:02d}.mp4"
    seg(out, src, wavs, (frames, spans), dur, base, [], (render.CW, render.CH, render.CX, top), loud=True)
    return out, {"caption_boxes": boxes}


def run(rec, n, shorts, out):
    c.ensure_dirs()
    rec = Path(rec).resolve()
    work = Path(out) if out else c.STATE / "explainers" / rec.stem
    work.mkdir(parents=True, exist_ok=True)
    dur = judge.duration(rec)
    s = write_script(rec, work, n, dur)
    nar = narration(s, shorts, work)
    long, shorts_meta = build(rec, work, s, nar, shorts)
    fails = {"long.mp4": qa.check(long, {}, (W, H), (60, 900))}
    for p, m in shorts_meta:
        fails[f"shorts/{p.name}"] = qa.check(p, m, dur=(15, 60))
    (work / "qa.json").write_text(json.dumps(fails, indent=1))
    tags = [h.lstrip("#") for h in game_style.clip_hashtags()] + ["GTA6", "countdown", "tips"]
    (work / "metadata.json").write_text(json.dumps({"title": s["title"], "description": s["description"],
                                                    "tags": list(dict.fromkeys(tags)), "contains_synthetic_media": True}, indent=1))
    return work, fails


def main():
    ap = argparse.ArgumentParser(prog="python -m kit.explainer")
    ap.add_argument("recording")
    ap.add_argument("--items", type=int, default=7)
    ap.add_argument("--no-shorts", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    work, fails = run(a.recording, max(3, min(15, a.items)), not a.no_shorts, a.out)
    print(work)
    print(json.dumps(fails, indent=1))


if __name__ == "__main__":
    main()
