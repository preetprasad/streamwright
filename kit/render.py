"""Vertical 1080x1920 clip: facecam stack or blur/center, word-by-word captions, loudness normalised."""
import json
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config as c
from .third_party.clipkaro import facecam as fc

FONT = Path(__file__).parent / "third_party/clipkaro/fonts/Montserrat-ExtraBold.ttf"
SAFE = (140, 230, 940, 1436)       # caption safe zone x0, y0, x1, y1 (platform buttons/bars stay clear)
CX, CW, CH, CFPS = 140, 800, 240, 30  # caption canvas: 800x240 at x=140, fed to ffmpeg at 30 fps
SIZE, STROKE, LINE = 78, 6, 92
BLUR_FILL = ("[0:v]split=2[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
             "boxblur=30:3[bg];[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")
YELLOW, WHITE = (255, 230, 0, 255), (255, 255, 255, 255)


@lru_cache(None)
def font(size, path=FONT):
    return ImageFont.truetype(str(path), size)


def wrap(texts, f, cw=CW, stroke=STROKE):
    """Greedy line breaks -> [[word index]]."""
    lines, cur = [], []
    for k in range(len(texts)):
        if cur and f.getlength(" ".join(texts[j] for j in cur + [k])) > cw - 2 * stroke:
            lines.append(cur)
            cur = []
        cur.append(k)
    return lines + [cur]


def chunks(words, path=FONT, size=SIZE, cw=CW, stroke=STROKE):
    """2-4 words per caption; break on a pause > 0.35 s, after punctuation, or when it would need 3 lines."""
    out, cur = [], []
    for w in words:
        if cur and (len(cur) == 4 or w[0] - cur[-1][1] > 0.35 or cur[-1][2][-1] in ".?!,"
                    or len(wrap([x[2] for x in cur + [w]], font(size, path), cw, stroke)) > 2):
            out.append(cur)
            cur = []
        cur.append(w)
    return out + [cur] if cur else out


def chunk_img(texts, active, path=FONT, size=SIZE, cw=CW, ch=CH, line=LINE, stroke=STROKE, accent=YELLOW):
    """Transparent cw x ch image of one chunk with word `active` highlighted (defaults: the 800x240 Short caption)."""
    while True:
        f = font(size, path)
        lines = wrap(texts, f, cw, stroke)
        widths = [f.getlength(" ".join(texts[k] for k in ln)) for ln in lines]
        if max(widths) <= cw - 2 * stroke or size <= 40:  # a very long word shrinks the font
            break
        size -= 4
    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d, y, sp = ImageDraw.Draw(img), (ch - len(lines) * line) // 2, f.getlength(" ")
    for ln, wd in zip(lines, widths):
        x = (cw - wd) / 2
        for k in ln:
            d.text((x, y), texts[k], font=f, fill=accent if k == active else WHITE, stroke_width=stroke, stroke_fill="black")
            x += f.getlength(texts[k]) + sp
        y += line
    return img


def probe(src):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(src)],
                       capture_output=True, text=True, check=True)
    j = json.loads(r.stdout)
    n, _, d = j["streams"][0]["r_frame_rate"].partition("/")
    return j["streams"][0]["width"], j["streams"][0]["height"], float(n) / float(d or 1), float(j["format"]["duration"])


def find_cam(src, w, h, dur):
    if c.s("FACECAM") == "off":
        return None
    if c.s("FACECAM_BOX"):
        x, y, bw, bh = (float(v) for v in c.s("FACECAM_BOX").split(","))
        return fc.clamp({"x": x, "y": y, "w": bw, "h": bh}, w, h)
    cam = fc.detect(src, dur, w, h)
    return fc.clamp(cam, w, h) if cam else None


def caption_track(cid, words, centre):
    """Per-word PNGs (kept in state/tmp) + their active spans. -> (frames bytes, spans, boxes)"""
    top = int(centre - CH / 2)
    frames, spans, boxes = [], [], []
    for ch in chunks(words):
        texts = [w[2] for w in ch]
        for i, w in enumerate(ch):
            img = chunk_img(texts, i)
            img.save(c.TMP / f"{cid}_cap{len(frames)}.png")
            bb = img.getbbox()
            boxes.append([CX + bb[0], top + bb[1], CX + bb[2], top + bb[3]])
            spans.append([w[0], ch[i + 1][0] if i + 1 < len(ch) else w[1] + 0.25, len(frames)])
            frames.append(img.tobytes())
    for a, b in zip(spans, spans[1:]):  # never overlap the next word
        a[1] = min(a[1], b[0])
    return frames, spans, boxes, top


def feed(proc, frames, spans, dur, cw=CW, ch=CH):
    blank, si = bytes(cw * ch * 4), 0
    for n in range(int((dur + 1) * CFPS) + 1):
        t = n / CFPS
        while si < len(spans) and spans[si][1] <= t:
            si += 1
        proc.stdin.write(frames[spans[si][2]] if si < len(spans) and spans[si][0] <= t else blank)


def render(cid, src, verdict, words, window, peak):
    """src: exact-window clip. words: (start, end, text) relative to src. -> path of clips/<id>.mp4"""
    out = c.CLIPS / f"{cid}.mp4"
    w, h, fps, dur = probe(src)
    fps = f"{min(fps, 60):g}"
    cam = find_cam(src, w, h, dur)
    if cam:
        base, centre = fc.filter_chain(cam, fc.geometry(cam, w, h), h, fps), fc.TOP_H + fc.CAP_GAP
    else:
        pre = "[0:v]scale=-2:1920,crop=1080:1920" if c.s("CROP_MODE") == "center" else BLUR_FILL
        base, centre = f"{pre},setsar=1,fps={fps},format=yuv420p[v0]", 1300
    words = [(a, b, t.strip().upper()) for a, b, t in words if t.strip()] if c.b("CAPTIONS") else []
    frames, spans, boxes, top = caption_track(cid, words, centre) if words else ([], [], [], 0)
    chain, last, extra = base, "v0", []
    if spans:
        chain += f";[v0][1:v]overlay={CX}:{top}:shortest=1[v1]"
        last, extra = "v1", ["-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{CW}x{CH}", "-r", str(CFPS), "-i", "pipe:0"]
    cmd = (["ffmpeg", "-y", "-v", "error", "-i", str(src)] + extra +
           ["-filter_complex", chain, "-map", f"[{last}]", "-map", "0:a?", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-c:v", "h264_videotoolbox", "-b:v", "8M", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-ar", "48000", "-movflags", "+faststart", str(out)])
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE if spans else None, stderr=subprocess.PIPE, text=False)
    if spans:
        try:
            feed(p, frames, spans, dur)
            p.stdin.close()
        except BrokenPipeError:
            pass  # ffmpeg stopped early (or failed): its exit status below says which
    err = p.stderr.read().decode(errors="replace")
    if p.wait():
        raise RuntimeError(f"ffmpeg render failed: {c.mask(err)[-300:]}")
    meta = {"id": cid, "title": verdict["title"], "caption": verdict["caption"], "hashtags": verdict["hashtags"],
            "score": verdict.get("clip_score", verdict.get("score")), "reason": verdict["reason"],
            "window": list(window), "peak": peak, "layout": "facecam" if cam else c.s("CROP_MODE"),
            "caption_boxes": boxes}
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    return out
