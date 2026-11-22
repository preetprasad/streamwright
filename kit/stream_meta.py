"""Stream title, description, tags, Twitch texts and thumbnail:
python -m kit.stream_meta [--apply] [--frame PATH] [--thumb-only [--text T --accent W] [--plate PATH]]"""
import argparse
import itertools
import json
import logging
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageFont

from . import config as c, game_style, links
from .render import FONT, font, probe
from .third_party.clipkaro import facecam as fc

log = logging.getLogger("stream_meta")
W, H = 1280, 720
YELLOW, WHITE = (255, 230, 0), (255, 255, 255)
CAT_GAMING = "20"
FORCE_SSL = "https://www.googleapis.com/auth/youtube.force-ssl"
HASHTAGS = "#GTA6 #GTAVI #gaming #beginner #firsttime"  # the GTA VI pack's hashtags; other games use their pack's
FIELDS = ("title", "hook", "about", "tags", "thumb_text", "thumb_accent", "twitch_title", "twitch_notification",
          "twitch_bio")
SCHEMA_HINT = ('{"title": "<=70 chars", "hook": "3-4 sentences, honest beginner angle, curiosity, makes people want to watch", '
               '"about": "5-7 sentences: what viewers will see, the vibe, why a first-timer is fun to watch, invite spoiler-free chat tips and backseating", "tags": ["<=15 tags"], '
               '"thumb_text": "2-4 punchy words", "thumb_accent": "one word of thumb_text", '
               '"twitch_title": "<=140 chars", "twitch_notification": "<=140 chars go-live message", '
               '"twitch_bio": "<=200 chars, who I am and the angle; no links"}')
TIMEOUT_TEXT, TIMEOUT_IMAGE = 180, 600

# (title, thumb_text, accent); {game} {goal}. Picked by stream number so reruns of a day are stable.
DAY1 = [
    ("I have NEVER played {game}. Day 1", "FIRST TIME EVER", "EVER"),
    ("Total beginner plays {game} for the first time", "COMPLETE NOOB", "NOOB"),
    ("Can a total newbie survive {game}? Day 1", "I'M SO LOST", "LOST"),
]
DAYN = [
    ("Still a beginner: {game} Day {n}", "STILL A NOOB", "NOOB"),
    ("Learning {game} from zero, Day {n}", "DAY {n} STILL LOST", "LOST"),
    ("First-timer vs the whole of {game}, Day {n}", "WHAT AM I DOING", "DOING"),
    ("Day {n}: did I finally learn how to drive in {game}?", "CAN I DRIVE YET", "DRIVE"),
]
# One scene per day (cycled), always the beginner angle: an original young gamer-type character on the right.
SCENES = [
    "dusk on a neon pastel ocean-front boulevard lined with palms, the character stands beside a sports car he has just 'borrowed' while two police cruisers with flashing red and blue lights close in down the street",
    "blue hour on a busy beachfront strip, the character at the wheel of a convertible glances toward a police helicopter whose searchlight is sweeping the road ahead",
    "golden hour on a rooftop over a sparkling Miami-style skyline, the character looks across at a huge open city full of places he has never been, a jet streaking overhead",
    "a sunny pastel art-deco street, the character in a vintage convertible looks back at a pile-up he may have caused, a fire hydrant spraying water in the background",
    "a neon night market street, the character holds a map the wrong way up while a getaway car idles beside him with its door open",
]
# Day 1 = curiosity/uncertainty (first encounter); later days = growing confidence. Never fake shock or a smirk.
EXPRESSIONS = ("genuine curiosity with mild uncertainty: eyebrows raised, eyes wide with interest, mouth closed or "
               "slightly parted, a believable 'wait, what did I just start?' look; not scared, not smirking, no grin",
               "focused concentration with a hint of growing confidence; not scared, not smirking, no grin")
# THUMB_LOOK=creator: Mythpat-style top-YouTuber photo composite (real-looking face, game-themed outfit, vivid).
STYLE_CREATOR = ("photoreal top-YouTuber thumbnail photo-composite: the creator photographed in crisp studio light, "
                 "cleanly cut out and composited into a vivid, sunny, high-saturation game-world scene; punchy clean "
                 "colours, sharp detail, gentle rim light separating him from the background, shallow depth of field.")
EXPRESSION_CREATOR = ("a natural, confident look with eyebrows slightly raised and a small closed-mouth smile; no wide "
                      "grin, no teeth, no smirk, never scared")
COMPOSITION_STAKE = (
    "Composition: exactly two things in tension and nothing else competing. (1) The subject: {subject}{outfit}, "
    "a close chest-up crop on the RIGHT third, his face large (about one third of the frame height), sharp, and the "
    "best-lit element. (2) ONE unmistakable game-world stake from the scene (the threat or goal) in the LEFT-CENTRE "
    "mid-ground, readable even when the image is shrunk to 168x94 px. He looks toward that stake (inward gaze), not "
    "into the lens. Expression: {expression}. Saturate the subject and the stake, keep the rest muted and softly "
    "blurred. Keep the TOP-LEFT area (about 40% wide, 45% tall) calm and dark for type.\n")
# THUMB_LAYOUT=poster: key-art poster like Mythpat's GTA 6 live thumbnail (big centred subject, game emblem behind).
COMPOSITION_POSTER = (
    "Composition: a bold key-art poster. The subject: {subject}{outfit}, large and slightly right of centre, head and "
    "torso filling about 60% of the frame height, looking straight into the lens with {expression}, {pose}. Directly behind him, partly hidden by his head and shoulders, a huge glowing "
    "emblem: {emblem}. Dark, saturated neon night background with soft silhouettes from the game world, strong "
    "magenta and teal rim light on him. Keep the TOP-LEFT corner calm for a small tag.\n")
EXPRESSION_POSTER = ("a subtle, confident, closed-mouth smile with relaxed, friendly eyes; no teeth showing, "
                     "no wide grin, no smirk")
PLATE_PROMPT = (
    "Use your built-in image generation tool (do NOT draw with code, PIL, SVG or any script) to create ONE wide 16:9 "
    "background plate for a livestream thumbnail, and save the result as plate.png in the current directory.\n"
    "Style: {style}\n"
    "Scene: {scene}.\n"
    # Composition rules from top gaming-thumbnail research (state/research/thumbnail_design.md):
    # 1-3 focal elements, game anchor + stakes, face ~1/3 of frame, inward gaze, earned expression, 168x94 legible.
    "{composition}"
    "Forbidden: {forbid_head}, watermarks, official characters or costumes from {game} or "
    "Marvel, extra fingers, cluttered composition, {forbid}.")

STYLE_PHOTO = ("photoreal cinematic film still, shot on 35mm anamorphic lens, motivated practical lighting (neon signs, "
               "warm golden-hour sun, soft rim light), natural skin texture, warm vibrant but tasteful teal-and-amber "
               "colour grade, aspirational and inviting mood, shallow depth of field, subtle film grain, true-to-life "
               "proportions.")
FORBID_PHOTO = "oversaturated HDR, plastic skin, over-sharpening, cartoon style"
# STYLE_REF set: render the character in the look of that image (e.g. a Marvel Rivals-inspired hero render).
STYLE_HERO = ("stylised hero-shooter key art in the look of the attached STYLE reference (a Marvel Rivals-inspired "
              "hero render): painterly 3D, clean confident shapes, crisp sharp detail, bold warm rim light, rich but "
              "controlled colour, polished splash-art finish, premium game-cinematic lighting with depth and "
              "atmosphere, shallow depth of field. Keep his outfit and silhouette from the style reference.")
# THUMB_LOOK=hero without a STYLE_REF image: the same look described in words, so only his face image is sent
# (sending an outfit image alongside pulled the face away from his likeness).
STYLE_HERO_TEXT = ("stylised hero-shooter key art in a Marvel Rivals-inspired look: painterly 3D, clean confident shapes, "
                   "crisp sharp detail, bold warm rim light, rich but controlled colour, polished splash-art finish, "
                   "premium game-cinematic lighting with depth and atmosphere, shallow depth of field. His face stays "
                   "true to the photos, only the rendering is stylised.")
HERO_OUTFIT = ("an original red hero jacket with angular armour plates on the shoulders, a high collar, silver "
               "buckles and thin cyan glowing accents, over a dark charcoal top with a chevron pattern, and dark "
               "armoured fingerless gloves")
FORBID_HERO = "flat cheap cartoon, chibi, anime, muddy or noisy rendering, warped face, generic AI gloss"


SUBJECT = "an original young gamer-type character (not a real person, not a game character)"
SUBJECT_REF = ("EXACTLY the man in the attached reference photos (front and both three-quarter views of the same "
               "person): keep his real face shape and proportions, eyes, eyebrows, nose, lips, skin tone, beard "
               "pattern, hairline and long dark hair tied back. Do not beautify, slim, age or idealise him; it must "
               "be instantly recognisable as him to his friends. He wears grey-silver rectangular glasses with metal temples (add them even if a photo shows none).")


def face_ref(key="FACE_REF"):
    p = c.s(key).strip()
    # resolve(): codex runs in a temp cwd, so a relative path would not be found there
    return Path(p).expanduser().resolve() if p and Path(p).expanduser().is_file() else None


def paths():
    return c.STATE / "stream_meta.json", c.STATE / "thumbnail.jpg"


# ---- text --------------------------------------------------------------------------------------------------

def cut(t, n):
    t = " ".join(str(t).split())
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


EXPECT = ("- A real first playthrough: no guides, no prior experience, every mistake live\n"
          "- Chat as co-pilot: drop spoiler-free tips, backseat me, vote on what I try next\n"
          "- Learning the game from zero: controls, missions and whatever it throws at me")


def description(day, hook, about):
    """Full long description. Links, schedule and hashtags are inserted here, never by a model."""
    names = {n for n, _ in links.items()}
    live = " and ".join(n for n in ("YouTube", "Twitch") if n in names)
    clips = ", ".join(p for n, p in (("YouTube", "YouTube Shorts"), ("TikTok", "TikTok"), ("Instagram", "Instagram Reels"))
                      if n in names)
    # Each link appears once (in the block below); these lines only say where things happen.
    where = "\n".join(s for s in (f"Live on {live} at the same time, so watch wherever you like." if live else "",
                                  f"The best moments are clipped live to {clips}." if clips else "") if s)
    parts = [cut(hook, 700),
             f"About this stream (Day {day})\n{cut(about, 900)}",
             f"What to expect\n{EXPECT}",
             f"Watch and follow\n{where}" if where else "",
             f"About me\n{c.s('STREAM_ABOUT').strip()}" if c.s("STREAM_ABOUT").strip() else "",
             links.block(),
             f"Schedule: {c.s('STREAM_SCHEDULE').strip()}" if c.s("STREAM_SCHEDULE").strip() else "",
             hashtags()]
    return cut_desc("\n\n".join(p for p in parts if p))


def style():
    """Style pack of STREAM_GAME (no network; research happens in ensure_style)."""
    return game_style.get(c.s("STREAM_GAME"))


def hashtags():
    return " ".join("#" + h for h in style()["hashtags"])


def ensure_style():
    """Research the game's style pack when it is missing (Codex + web search, slow). Never raises."""
    try:
        game_style.load(c.s("STREAM_GAME"))
    except Exception as e:
        log.warning("style pack failed: %s", c.mask(str(e))[:200])


def cut_desc(d):
    return d if len(d) <= 5000 else d[:4999]  # unreachable with the caps above; safety net


def twitch_bio(text):
    """<=300 chars: bio text cut first, links to the other platforms kept whole."""
    return links.fit(text, 300, links.short(exclude=("Twitch",), compact=True), sep=" | ")  # Twitch strips line breaks


def template(day):
    """Deterministic fallback text for stream number `day`."""
    pool = DAY1 if day == 1 else DAYN
    title, thumb, accent = pool[day % len(pool)]
    fmt = {"game": c.s("STREAM_GAME"), "n": day, "goal": c.s("STREAM_GOAL")}
    title, thumb = title.format(**fmt), thumb.format(**fmt)
    game = c.s("STREAM_GAME")
    goal = f" {c.s('STREAM_GOAL')}." if c.s("STREAM_GOAL") else ""
    hook = (f"I have never played {game} in my life, and now I am dropping in live. "
            "No guides, no spoilers, no idea what I am doing. Can a total beginner work it out in real time, "
            "or will the game eat me alive?")
    about = (f"Watch a complete beginner learn {game} from zero: getting lost, making mistakes live "
             f"and learning the hard way.{goal} "
             "Chat is the co-pilot: tell me where to go and what to try next.")
    return build({"title": title, "hook": hook, "about": about, "thumb_text": thumb, "thumb_accent": accent,
                  "tags": [game, *style()["hashtags"], f"first time playing {game}", f"{game} beginner",
                           f"{game} gameplay", f"{game} live", f"{game} first playthrough", "new player",
                           f"noob plays {game}", "livestream"],
                  "twitch_title": title,
                  "twitch_notification": f"Day {day}: a total beginner is live in {game}. Come help me not mess up!",
                  "twitch_bio": f"Total beginner playing {game} live, mistakes included. Chat tells me what to try next."},
                 day)


def build(v, day):
    """Validated fields -> final meta text dict (description, Twitch texts built by code)."""
    return {"title": v["title"], "description": description(day, v["hook"], v["about"]), "tags": v["tags"],
            "thumb_text": v["thumb_text"], "thumb_accent": v["thumb_accent"],
            "twitch_title": cut(v["twitch_title"], 140), "twitch_notification": cut(v["twitch_notification"], 140),
            "twitch_bio": twitch_bio(cut(v["twitch_bio"], 200))}


def clean(v, day):
    """Validate/normalise model output. Raises ValueError/KeyError/TypeError when unusable."""
    title = " ".join(str(v["title"]).split())
    if not 0 < len(title) <= 100:
        raise ValueError("title length")
    hook, about = " ".join(str(v["hook"]).split()), " ".join(str(v["about"]).split())
    if not 40 <= len(hook) <= 900 or not 20 <= len(about) <= 1200:
        raise ValueError("hook/about length")
    tags, total = [], 0
    for t in v["tags"]:
        t = str(t).strip().lstrip("#")
        if t and t.lower() not in (x.lower() for x in tags) and total + len(t) + 1 <= 400 and len(tags) < 15:
            tags.append(t)
            total += len(t) + 1
    if not tags:
        raise ValueError("no tags")
    words = str(v["thumb_text"]).upper().split()
    if not 2 <= len(words) <= 4:
        raise ValueError("thumb_text must be 2-4 words")
    accent = str(v.get("thumb_accent", "")).upper().strip(" .,!?")
    tt = " ".join(str(v.get("twitch_title") or title).split())
    nt = " ".join(str(v.get("twitch_notification") or title).split())
    bio = " ".join(str(v.get("twitch_bio") or "").split())
    if not bio:
        raise ValueError("twitch_bio missing")
    return build({"title": title, "hook": hook, "about": about, "tags": tags, "thumb_text": " ".join(words),
                  "thumb_accent": accent if accent in (w.strip(".,!?") for w in words) else words[-1],
                  "twitch_title": tt, "twitch_notification": nt, "twitch_bio": bio}, day)


def prompt_text(day):
    return (
        f"Write YouTube + Twitch live stream metadata. Game: {c.s('STREAM_GAME')}. Stream number: Day {day}. "
        f"Angle: {c.angle()}. Goal: {c.s('STREAM_GOAL') or '(none)'}. "
        f"Thumbnail text ideas for this game (use or riff on one, 2-4 words): {', '.join(style()['lingo'])}.\n"
        "Voice: funny, honest, curiosity-driven, a bit self-deprecating. No clickbait lies, no fake events, "
        "no real people's names, no all-caps spam, no URLs or hashtags (added separately), and do not describe "
        "specific gameplay events (you do not know what will happen). Lean into the total-beginner angle.\n"
        f"Reply with JSON only: {SCHEMA_HINT}")


def ask_ollama(day):
    body = {"model": c.s("JUDGE_MODEL"), "stream": False, "format": "json", "think": False,
            "messages": [{"role": "user", "content": prompt_text(day)}]}
    r = requests.post(c.s("OLLAMA_URL").rstrip("/") + "/api/chat", json=body, timeout=300)
    r.raise_for_status()
    return clean(json.loads(r.json()["message"]["content"]), day)


# ---- codex (ChatGPT plan, never an API key) ----------------------------------------------------------------

def codex_ok():
    """True when `codex login status` says ChatGPT login. An API-key login is refused with a warning."""
    try:
        out = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=30)
        out = out.stdout + out.stderr
    except (OSError, subprocess.SubprocessError):
        return False
    if "chatgpt" in out.lower():
        return True
    if "api key" in out.lower():
        log.warning("Codex is logged in with an API key: not used (would bill the API). Run `codex login` with ChatGPT.")
    return False


def run_codex(prompt, cwd, sandbox, timeout, schema=None, image=None, search=False):
    """Run `codex exec` in `cwd`; returns the last message text. search=True enables live web search."""
    out = Path(cwd) / "last.txt"
    images = [image] if isinstance(image, (str, Path)) else list(image or [])
    cmd = ["codex"] + (["--search"] if search else []) + ["exec"] + (["-i", *map(str, images)] if images else []) + ["--skip-git-repo-check", "-s", sandbox, "-o", str(out)]
    if schema:
        sf = Path(cwd) / "schema.json"
        sf.write_text(json.dumps(schema))
        cmd += ["--output-schema", str(sf)]
    subprocess.run(cmd + [prompt], cwd=cwd, capture_output=True, text=True, timeout=timeout, check=True,
                   stdin=subprocess.DEVNULL)
    return out.read_text()


def ask_codex(day):
    schema = {"type": "object", "additionalProperties": False, "required": list(FIELDS),
              "properties": {k: ({"type": "array", "items": {"type": "string"}} if k == "tags" else {"type": "string"})
                             for k in FIELDS}}
    with tempfile.TemporaryDirectory() as d:
        return clean(json.loads(run_codex(prompt_text(day), d, "read-only", TIMEOUT_TEXT, schema)), day)


def gen_chain():
    """Text sources to try in order, from STREAM_GEN (blank = auto)."""
    want = c.s("STREAM_GEN").strip().lower()
    if want == "template":
        return ["template"]
    if want == "ollama":
        return ["ollama", "template"]
    if want == "codex" or not want:
        return (["codex"] if codex_ok() else []) + ["ollama", "template"]
    return ["ollama", "template"]


def make_text(day):
    for src in gen_chain():
        if src == "template":
            return template(day), "template"
        try:
            return (ask_codex(day) if src == "codex" else ask_ollama(day)), src
        except Exception as e:  # unreachable, bad JSON, invalid lengths: all fall back the same way
            log.warning("%s text failed (%s), trying next", src, c.mask(str(e))[:200])
    return template(day), "template"


# ---- thumbnail ---------------------------------------------------------------------------------------------

def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def sunset(pk=None):
    """Vertical background gradient from the first three palette colours (GTA VI: purple -> pink -> orange)."""
    stops = np.array([rgb(x) for x in (pk or style())["palette"][:3]], float)
    y = np.linspace(0, 2, H)[:, None]
    i = np.minimum(y.astype(int), 1)
    col = stops[i[:, 0]] * (1 - (y - i)) + stops[i[:, 0] + 1] * (y - i)
    return Image.fromarray(np.repeat(col[:, None, :], W, axis=1).astype("uint8"))


def newest_segment():
    segs = sorted(c.BUFFER.glob("*.ts"), key=lambda p: int(p.stem))
    return segs[-2] if len(segs) > 1 else segs[0] if segs else None  # the newest file may still be written


def grab(seg, out):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "3", "-i", str(seg), "-frames:v", "1", str(out)],
                   check=True, capture_output=True, timeout=60)
    return Image.open(out).convert("RGB")


def find_cam(frame, seg):
    """(x, y, w, h) of the facecam in frame pixels, or None."""
    if c.s("FACECAM") == "off":
        return None
    w, h = frame.size
    if c.s("FACECAM_BOX"):
        x, y, bw, bh = (float(t) for t in c.s("FACECAM_BOX").split(","))
        cam = fc.clamp({"x": x, "y": y, "w": bw, "h": bh}, w, h)
    elif seg:
        try:
            cam = fc.detect(seg, probe(seg)[3], w, h)
            cam = fc.clamp(cam, w, h) if cam else None
        except Exception as e:
            log.warning("facecam detection failed: %s", e)
            return None
    else:
        return None
    return (cam["x"], cam["y"], cam["w"], cam["h"]) if cam and cam["w"] > 8 and cam["h"] > 8 else None


def background(frame_path, pk=None):
    """-> (1280x720 RGB background, facecam crop or None)."""
    frame, seg = None, None
    if frame_path:
        frame = Image.open(frame_path).convert("RGB")
    else:
        seg = newest_segment()
        if seg:
            try:
                with tempfile.TemporaryDirectory(dir=c.STATE) as d:
                    frame = grab(seg, Path(d) / "f.png")
            except Exception as e:
                log.warning("frame grab failed: %s", e)
    if frame is None:
        return sunset(pk), None
    cam = find_cam(frame, seg)
    face = frame.crop((cam[0], cam[1], cam[0] + cam[2], cam[1] + cam[3])) if cam else None
    return ImageOps.fit(frame, (W, H), Image.LANCZOS), face


def game_type():
    """True = the game's style pack (THUMB_TYPE=game, alias gta); False = the clean Montserrat style."""
    return c.s("THUMB_TYPE").strip().lower() != "clean"


def tfont(size, pk=None):
    """Thumbnail font: THUMB_FONT if set (e.g. Pricedown), else the game pack's font_file, else Montserrat."""
    pk = pk or style()
    for p in (c.s("THUMB_FONT").strip(), pk["font_file"] if game_type() else ""):
        q = Path(p).expanduser() if p else None
        if q and not q.is_absolute():
            q = c.ROOT / q
        if q and q.is_file():
            return ImageFont.truetype(str(q), size)
    return font(size)


def layout(words, maxw, maxh, gap=0.98, pk=None):
    """Largest font (<=200) with the words on 1-3 lines inside maxw x maxh -> (size, lines)."""
    for size in range(200, 50, -4):
        f = tfont(size, pk)
        sp = f.getlength(" ")
        for n in (1, 2, 3):  # fewest lines first
            for cuts in itertools.combinations(range(1, len(words)), n - 1):
                idx = (0, *cuts, len(words))
                lines = [words[idx[k]:idx[k + 1]] for k in range(n)]
                width = max(sum(f.getlength(w) for w in ln) + sp * (len(ln) - 1) for ln in lines)
                if width <= maxw and n * size * gap <= maxh:
                    return size, lines
    return 50, [words]


def likeness(paths_):
    """dlib face distances of each image to FACE_REF ({path: {"mean","best"} | None}); {} when the scorer is
    unavailable (needs a Python with face_recognition: LIKENESS_PYTHON, default python3)."""
    ref = face_ref()
    if not ref:
        return {}
    py = c.s("LIKENESS_PYTHON").strip() or "python3"
    try:  # -P: kit/queue.py would otherwise shadow the stdlib queue module
        r = subprocess.run([py, "-P", str(Path(__file__).with_name("likeness_score.py")), str(ref), "--",
                            *map(str, paths_)], capture_output=True, text=True, timeout=300)
        return json.loads(r.stdout.strip().splitlines()[-1])["scores"]
    except Exception as e:
        log.warning("likeness check unavailable (%s)", str(e)[:120])
        return {}


def best_plate(day):
    """plate_from_codex, regenerated until the face matches FACE_REF (mean distance <= LIKENESS_MAX) or
    LIKENESS_TRIES runs are used; keeps the closest plate. Scores go to state/likeness.jsonl."""
    tries, limit = max(1, c.i("LIKENESS_TRIES")), float(c.s("LIKENESS_MAX") or 0.40)
    best = None
    for n in range(tries):
        plate = plate_from_codex(day)
        keep = c.STATE / f"thumb_plate_try{n}.png"
        keep.write_bytes(plate.read_bytes())
        sc = likeness([keep]).get(str(keep))
        with open(c.STATE / "likeness.jsonl", "a") as fh:
            fh.write(json.dumps({"ts": time.time(), "day": day, "try": n, "score": sc}) + "\n")
        if sc is None and not face_ref():
            return plate  # nothing to compare against
        d = sc["mean"] if sc else 9.0
        log.info("likeness try %d: %s", n + 1, f"{d:.3f}" if sc else "no face found")
        if best is None or d < best[0]:
            best = (d, keep)
        if sc and d <= limit:
            break
    out = c.STATE / "thumb_plate.png"
    out.write_bytes(best[1].read_bytes())
    return out


def plate_from_codex(day):
    """Textless 16:9 plate through Codex image generation (ChatGPT plan). Saves state/thumb_plate.png."""
    t0 = time.time()
    pk = style()
    scene = pk["scenes"][(day - 1) % len(pk["scenes"])]
    if c.s("THUMB_LAYOUT").strip().lower() == "poster" and pk.get("poster_scene"):
        scene = pk["poster_scene"]
    with tempfile.TemporaryDirectory() as d:
        creator = c.s("THUMB_LOOK").strip().lower() == "creator"
        poster = c.s("THUMB_LAYOUT").strip().lower() == "poster"
        ref, style_ref = face_ref(), (None if creator else face_ref("STYLE_REF"))
        subject = SUBJECT_REF if ref else SUBJECT
        side = c.s("THUMB_FACE_SIDE").strip().lower()
        if ref and side in ("left", "right"):  # his preferred side: left = his left cheek toward the camera
            subject += (f" Show his {side} side: head turned about 30 degrees so his {side} cheek faces the camera "
                        f"(nose pointing toward the viewer's {side}), matching the {side}-side views in the photos.")
        if ref and style_ref:
            subject += (" The FIRST attached image is his real face (likeness). The SECOND image shows only the "
                        "outfit and rendering style, with no face. His face must come ONLY from the first image: keep "
                        "his exact face shape, nose, eyes, eyebrows, beard and hairline, rendered in the painterly "
                        "style without changing who he is.")
        hero_words = not creator and not style_ref and c.s("THUMB_LOOK").strip().lower() == "hero"
        outfit = "" if style_ref else f" (wearing: {HERO_OUTFIT if hero_words else pk.get('creator_outfit') or pk['outfit_hint']})"
        prompt = PLATE_PROMPT.format(scene=scene, subject=subject, outfit=outfit, game=c.s("STREAM_GAME"),
                                     composition=(COMPOSITION_POSTER if poster else COMPOSITION_STAKE).format(
                                         subject=subject, outfit=outfit, pose=c.s("THUMB_POSE").strip() or
                                         "a playful finger-gun pose aimed at the camera", emblem=pk.get("emblem") or "the game's title "
                                         "numerals", expression=c.s("THUMB_EXPRESSION").strip() or EXPRESSION_POSTER if poster else EXPRESSION_CREATOR
                                         if creator else EXPRESSIONS[0 if day == 1 else 1]),
                                     forbid_head=("any words or letters other than the emblem" if poster else
                                                  "any text, letters, numbers, logos"),
                                     style=STYLE_CREATOR if creator else STYLE_HERO if style_ref else
                                     STYLE_HERO_TEXT if hero_words else STYLE_PHOTO,
                                     forbid=(FORBID_HERO if style_ref or hero_words else FORBID_PHOTO) + ", " + ", ".join(pk["avoid"]))
        run_codex(prompt, d, "workspace-write", TIMEOUT_IMAGE, image=[p for p in (ref, style_ref) if p])
        found = Path(d) / "plate.png"
        if not found.exists():  # Codex keeps generated images under ~/.codex/generated_images
            imgs = [p for p in (Path.home() / ".codex/generated_images").rglob("*")
                    if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and p.stat().st_mtime >= t0]
            if not imgs:
                raise RuntimeError("codex produced no image")
            found = max(imgs, key=lambda p: p.stat().st_mtime)
        out = c.STATE / "thumb_plate.png"
        Image.open(found).convert("RGB").save(out)
    return out


TYPE_H, TYPE_W = 250, 560  # max text block (top-left ~45% wide)


def scrim(img):
    """Gentle dark gradient on the left ~60% for type legibility."""
    x = np.clip(1 - np.linspace(0, 1, W) / 0.62, 0, 1) ** 1.4
    yv = np.clip(1 - (np.linspace(0, 1, H) - 0.45) / 0.4, 0.25, 1)  # strong behind type, light lower down
    img.paste((0, 0, 0), mask=Image.fromarray((yv[:, None] * x[None, :] * 200).astype("uint8")))


def shadowed(img, xy_text, f):
    """Draw [(x, y, text, colour)] with a soft blurred drop shadow, no outline."""
    sh = Image.new("L", img.size, 0)
    sd = ImageDraw.Draw(sh)
    for x, y, t, _ in xy_text:
        sd.text((x + 2, y + 7), t, font=f, fill=170)
    img.paste((0, 0, 0), mask=sh.filter(ImageFilter.GaussianBlur(9)))
    d = ImageDraw.Draw(img)
    for x, y, t, col in xy_text:
        d.text((x, y), t, font=f, fill=col)


IMPACT = "/System/Library/Fonts/Supplemental/Impact.ttf"
SUNSET = ((255, 214, 102), (255, 112, 150), (178, 72, 230))  # Vice City-style gold -> pink -> purple


def sunset_fill(w, h, stops=SUNSET):
    """Vertical gradient image through the 2-3 colour stops, w x h."""
    t, xs = np.linspace(0, 1, h), np.linspace(0, 1, len(stops))
    col = np.stack([np.interp(t, xs, [x[k] for x in stops]) for k in range(3)], 1)
    return Image.fromarray(np.repeat(col[:, None, :], w, 1).astype("uint8"), "RGB")


def gta_text(img, items, f, pk=None):
    """Game-style type: hard black drop shadow, thick outline, filled words, accent word in the pack's gradient."""
    pk = pk or style()
    fill, edge = rgb(pk["text_fill"]), rgb(pk["outline"])
    stops = [rgb(x) for x in pk["accent_gradient"]]
    sw = max(4, f.size // 14)
    d = ImageDraw.Draw(img)
    for x, y, t, _ in items:
        d.text((x + sw, y + sw + 3), t, font=f, fill=(0, 0, 0), stroke_width=sw, stroke_fill=(0, 0, 0))
    for x, y, t, col in items:
        d.text((x, y), t, font=f, fill=fill, stroke_width=sw, stroke_fill=edge)
        if col == YELLOW:  # accent: gradient fill clipped to the glyphs
            m = Image.new("L", img.size, 0)
            ImageDraw.Draw(m).text((x, y), t, font=f, fill=255)
            x0, y0, x1, y1 = m.getbbox()
            img.paste(sunset_fill(x1 - x0, y1 - y0, stops), (x0, y0), m.crop((x0, y0, x1, y1)))


def stars(img, n=5, size=34):
    """Row of gold five-point stars with black edges, top-right (a wanted-level style cue)."""
    import math
    d = ImageDraw.Draw(img)
    x0 = W - 60 - n * (size * 2 + 8)
    for k in range(n):
        cx, cy = x0 + k * (size * 2 + 8) + size, 30 + size
        pts = [(cx + (size if i % 2 == 0 else size * 0.45) * math.sin(math.pi * i / 5),
                cy - (size if i % 2 == 0 else size * 0.45) * math.cos(math.pi * i / 5)) for i in range(10)]
        d.polygon(pts, fill=(255, 205, 40), outline=(0, 0, 0), width=4)


def compose(bg, face, text, accent, day, pk=None):
    """Type over a 1280x720 background in the game's style pack -> state/thumbnail.jpg."""
    pk = pk or style()
    bg = ImageOps.fit(bg.convert("RGB"), (W, H), Image.LANCZOS)
    scrim(bg)
    right = W - 420 if face else int(W * 0.56)  # bottom-right 20%x15% stays free either way
    # Type stays in the top-left block (~45% tall) so the stake in the lower left-centre stays visible.
    size, lines = layout(text.split(), min(right - 80, TYPE_W), TYPE_H, pk=pk) if text.strip() else (60, [])
    f = tfont(size, pk)
    sp, lh = f.getlength(" "), int(size * 0.98)
    y = 135
    items = []
    for ln in lines:
        x = 70.0
        for wd in ln:
            items.append((x, y, wd, YELLOW if wd.strip(".,!?") == accent else WHITE))
            x += f.getlength(wd) + sp
        y += lh
    gta_text(bg, items, f, pk) if game_type() else shadowed(bg, items, f)
    if pk.get("badge") == "stars" and c.s("THUMB_LAYOUT").strip().lower() != "poster":  # HUD cue, top-right
        stars(bg)
    if face:
        fw = 360
        fh = min(int(face.height * fw / face.width), 480)
        bg.paste(face.resize((fw, fh), Image.LANCZOS), (W - fw - 50, 60))
        ImageDraw.Draw(bg).rectangle((W - fw - 50, 60, W - 50, 60 + fh), outline=YELLOW, width=3)
    lab = font(34)
    label = f"DAY {day}"
    bw = int(lab.getlength(label)) + 36
    d = ImageDraw.Draw(bg)
    if game_type():  # solid accent-colour tag with the outline colour as its edge
        edge = rgb(pk["outline"])
        d.rounded_rectangle((70, 62, 70 + bw, 112), 8, fill=rgb(pk["accent_gradient"][len(pk["accent_gradient"]) // 2]),
                            outline=edge, width=3)
        d.text((88, 66), label, font=lab, fill=rgb(pk["text_fill"]), stroke_width=2, stroke_fill=edge)
    else:
        d.rounded_rectangle((70, 62, 70 + bw, 112), 8, outline=YELLOW, width=2)
        d.text((88, 66), label, font=lab, fill=YELLOW)
    if c.s("THUMB_LIVE").strip().lower() in ("1", "true", "yes"):
        # Red LIVE pill right of the DAY tag; only while actually live (YouTube bars misleading thumbnails),
        # so set THUMB_LIVE=false and regenerate for the archive.
        lx = 70 + bw + 16
        lw = int(lab.getlength("LIVE")) + 62
        d.rounded_rectangle((lx, 62, lx + lw, 112), 8, fill=(230, 33, 23), outline=(0, 0, 0), width=3)
        d.ellipse((lx + 16, 79, lx + 32, 95), fill=(255, 255, 255))
        d.text((lx + 42, 66), "LIVE", font=lab, fill=(255, 255, 255))
    out = paths()[1]
    for q in (90, 85, 80, 70, 55):
        bg.save(out, "JPEG", quality=q)
        if out.stat().st_size < 1_900_000:
            break
    # Feed-size check (research: must read at 168x94): saved beside the thumbnail for a quick look.
    bg.resize((168, 94), Image.LANCZOS).save(out.with_name("thumbnail_168.png"))
    return out


def thumbnail(text, accent, day, frame_path=None, plate=None):
    face = None
    if plate:
        bg = Image.open(plate)
    else:
        bg = None
        if c.s("STREAM_THUMB").strip().lower() != "local" and codex_ok():
            try:
                bg = Image.open(best_plate(day))
            except Exception as e:
                log.warning("codex plate failed (%s), using local background", c.mask(str(e))[:200])
        if bg is None:
            bg, face = background(frame_path, style())
    return compose(bg, face, text, accent, day)


# ---- run ---------------------------------------------------------------------------------------------------

def generate(frame_path=None):
    """One generation run: bumps the day counter, writes state/stream_meta.json and the thumbnail."""
    meta_path, _ = paths()
    c.STATE.mkdir(parents=True, exist_ok=True)
    try:
        day = int(json.loads(meta_path.read_text()).get("day", 0)) + 1
    except (OSError, ValueError, AttributeError):
        day = 1
    text, source = make_text(day)
    thumb = thumbnail(text["thumb_text"], text["thumb_accent"], day, frame_path)
    meta = {**text, "thumb": str(thumb), "day": day, "source": source}
    meta_path.write_text(json.dumps(meta, indent=1))
    return meta


def thumb_only(text=None, accent=None, plate=None):
    """Regenerate only the thumbnail for the current day (no day bump)."""
    meta_path, _ = paths()
    c.STATE.mkdir(parents=True, exist_ok=True)
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        meta = {**template(1), "day": 1, "source": "template"}
    if text:
        meta["thumb_text"] = text.upper()
        meta["thumb_accent"] = (accent or text.split()[-1]).upper()
    meta["thumb"] = str(thumbnail(meta["thumb_text"], meta["thumb_accent"], meta["day"], plate=plate))
    meta_path.write_text(json.dumps(meta, indent=1))
    return meta


def apply_youtube(meta):
    """Update the upcoming/active broadcast. Returns True when a broadcast was updated."""
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    from .uploaders import youtube
    if not youtube.enabled():
        return False
    if FORCE_SSL not in (json.loads(youtube.TOKEN.read_text()).get("scopes") or []):
        print("YouTube token lacks the youtube.force-ssl scope: rerun `python -m kit.auth youtube`, then --apply again.")
        return False
    yt = build("youtube", "v3", credentials=youtube.credentials(), cache_discovery=False)
    bc = None
    for status in ("upcoming", "active"):  # broadcastStatus and mine are exclusive filters; both return your own
        items = yt.liveBroadcasts().list(part="id,snippet", broadcastStatus=status, maxResults=5).execute().get("items")
        if items:
            bc = items[0]
            break
    if not bc:
        print("No upcoming or active YouTube broadcast found: create one in YouTube Studio, then --apply again.")
        return False
    vid, old = bc["id"], bc["snippet"]
    snip = {"title": meta["title"], "description": meta["description"], "scheduledStartTime": old["scheduledStartTime"]}
    if old.get("scheduledEndTime"):
        snip["scheduledEndTime"] = old["scheduledEndTime"]
    yt.liveBroadcasts().update(part="snippet", body={"id": vid, "snippet": snip}).execute()
    v = yt.videos().list(part="snippet", id=vid).execute()["items"][0]["snippet"]  # keep the other snippet fields
    v.update(title=meta["title"], description=meta["description"], tags=meta["tags"], categoryId=CAT_GAMING)
    for k in ("thumbnails", "publishedAt", "channelId", "channelTitle", "liveBroadcastContent", "localized"):
        v.pop(k, None)
    yt.videos().update(part="snippet", body={"id": vid, "snippet": v}).execute()
    try:
        yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(meta["thumb"], mimetype="image/jpeg")).execute()
    except HttpError as e:  # thumbnails need a phone-verified channel
        print(f"Title/description/tags set, thumbnail upload failed: {e.status_code} {e.reason}")
        return True
    print(f"YouTube broadcast {vid} updated (title, description, tags, category, thumbnail).")
    return True


def show(meta):
    print(f"Day {meta['day']} ({meta['source']})\nTitle: {meta['title']}\nThumb text: {meta['thumb_text']} "
          f"(accent {meta['thumb_accent']})\nTags: {', '.join(meta['tags'])}\nThumbnail: {meta['thumb']}\n"
          f"Description:\n{meta['description']}\n\nTwitch title: {meta['twitch_title']}\n"
          f"Twitch go-live notification: {meta['twitch_notification']}\nTwitch bio: {meta['twitch_bio']}")


def auto():
    """Kit startup hook: generate, apply when a YouTube token exists. Never raises."""
    try:
        ensure_style()
        meta = generate()
        log.info("stream meta day %d (%s): %s", meta["day"], meta["source"], meta["title"])
        if apply_youtube(meta):
            log.info("stream meta applied to YouTube")
    except Exception as e:
        log.error("stream meta failed: %s", c.mask(str(e))[:200])


def main():
    ap = argparse.ArgumentParser(prog="python -m kit.stream_meta")
    ap.add_argument("--apply", action="store_true", help="push to the upcoming/active YouTube broadcast")
    ap.add_argument("--frame", help="image to use as thumbnail background")
    ap.add_argument("--thumb-only", action="store_true", help="regenerate only the thumbnail, keep the day counter")
    ap.add_argument("--text", help="thumbnail text (with --thumb-only)")
    ap.add_argument("--accent", help="accent word of --text")
    ap.add_argument("--plate", help="compose on this image, no Codex call (with --thumb-only)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ensure_style()
    if a.thumb_only:
        t0 = time.time()
        meta = thumb_only(a.text, a.accent, a.plate)
        print(f"Thumbnail: {meta['thumb']} ({Path(meta['thumb']).stat().st_size} bytes, {time.time() - t0:.0f}s)")
        return
    meta = generate(a.frame)
    show(meta)
    if a.apply and not apply_youtube(meta):
        print("(not applied; paste the title/description above into YouTube Studio)")


if __name__ == "__main__":
    main()
