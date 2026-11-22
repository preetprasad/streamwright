"""Per-game thumbnail style pack (state/styles/<slug>.json), researched through Codex with live web search:
python -m kit.game_style [--game NAME] [--refresh] [--get-font]"""
import argparse
import io
import ipaddress
import json
import logging
import re
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlparse

import requests

from . import config as c

log = logging.getLogger("game_style")
FONT_DIR = Path(__file__).parent / "third_party" / "fonts"
NEUTRAL_FONT = "kit/third_party/clipkaro/fonts/Montserrat-ExtraBold.ttf"
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
SOURCES = ("google_fonts", "official_free", "paid", "unknown")
FONT_CAP = 20_000_000
TIMEOUT_RESEARCH = 420
GOOGLE_DIRS = ("ofl", "apache")  # OFL and Apache 2.0 folders of github.com/google/fonts

TYPE_GTA = "heavy display type, thick black outline, hard black drop shadow, white words, one accent word in a gradient"

PROMPT = (
    "Research the visual identity of the video game \"{game}\" on the live web (official site, store page, trailers, "
    "key art, press kit, font identification pages) and fill the JSON. Rules: be factual; write \"unknown\" instead of "
    "inventing a font, URL or licence; every URL must be one you actually saw; list the pages you used in sources. "
    "Colours are #RRGGBB taken from the game's key art and logo.\n"
    "- logo_font: font used in the game's logo or marketing type; logo_font_url: where it is sold or downloaded, or unknown.\n"
    "- closest_free_fonts: 1-3 free fonts that look closest, each {{name, url, licence, source}}; source is google_fonts "
    "(url on fonts.google.com or github.com/google/fonts, OFL/Apache), official_free (the foundry's own free download of "
    "the font), paid or unknown. Prefer Google Fonts.\n"
    "- palette 3-6 colours, accent_gradient 2-3 stops (top to bottom), text_fill and outline for the main thumbnail words "
    "(they must stay readable: strong contrast between them).\n"
    "- type_treatment: one line, e.g. \"heavy condensed, thick black outline, hard drop shadow\".\n"
    "- scenes: exactly 5 thumbnail background scenes in that game's world (iconic places, time of day, mood). Write them "
    "like this example, generic about \"the character\", never naming real trademarked characters, not copying official art: "
    "\"{example}\".\n"
    "- outfit_hint: one sentence on how the streamer's character could be styled to fit the world (no official costumes).\n"
    "- lingo: exactly 6 short thumbnail phrases (2-3 words, upper case) a first-time player of this game would use, e.g. "
    "\"GTA ROOKIE\".\n"
    "- hashtags: 3-6 without the # sign. avoid: things not to show (logos, official characters, ...). sources: URLs consulted.\n"
    "Reply with the JSON only.")


# ---- validation --------------------------------------------------------------------------------------------

def slug(game):
    return re.sub(r"[^a-z0-9]+", "-", game.lower()).strip("-") or "game"


def pack_path(game):
    return c.STATE / "styles" / f"{slug(game)}.json"


def _txt(v, lo, hi, name):
    t = " ".join(str(v).split())
    if not lo <= len(t) <= hi:
        raise ValueError(f"{name} length")
    return t


def _hex(v):
    if not isinstance(v, str) or not HEX.match(v):
        raise ValueError(f"bad hex {v!r}")
    return v.upper()


def _list(v, lo, hi, name):
    if not isinstance(v, list) or len(v) < lo:
        raise ValueError(f"{name} must be a list of >= {lo}")
    return v[:hi]


def validate(p):
    """Raw pack dict -> clean pack dict. Raises ValueError/KeyError/TypeError when unusable."""
    fonts = []
    for f in _list(p["closest_free_fonts"], 0, 4, "closest_free_fonts"):
        url = str(f["url"]).strip()
        if url != "unknown" and not url.startswith("https://"):
            raise ValueError("font url must be https or unknown")
        if f["source"] not in SOURCES:
            raise ValueError("font source")
        fonts.append({"name": _txt(f["name"], 1, 80, "font name"), "url": url,
                      "licence": _txt(f["licence"], 1, 120, "licence"), "source": f["source"]})
    tags = [re.sub(r"\W", "", str(h).lstrip("#")) for h in _list(p["hashtags"], 3, 6, "hashtags")]
    out = {
        "game": _txt(p["game"], 1, 100, "game"),
        "logo_font": _txt(p["logo_font"], 1, 100, "logo_font"),
        "logo_font_url": str(p.get("logo_font_url") or "unknown").strip()[:300],
        "closest_free_fonts": fonts,
        "palette": [_hex(x) for x in _list(p["palette"], 3, 6, "palette")],
        "accent_gradient": [_hex(x) for x in _list(p["accent_gradient"], 2, 3, "accent_gradient")],
        "text_fill": _hex(p["text_fill"]), "outline": _hex(p["outline"]),
        "type_treatment": _txt(p["type_treatment"], 5, 200, "type_treatment"),
        "scenes": [_txt(s, 30, 500, "scene") for s in _list(p["scenes"], 3, 5, "scenes")],
        "outfit_hint": _txt(p["outfit_hint"], 5, 300, "outfit_hint"),
        "lingo": [_txt(s, 3, 28, "lingo").upper() for s in _list(p["lingo"], 3, 6, "lingo")],
        "hashtags": [t for t in tags if t],
        "avoid": [_txt(s, 1, 100, "avoid") for s in _list(p["avoid"], 0, 10, "avoid")],
        "sources": [str(s)[:300] for s in _list(p["sources"], 0, 20, "sources")],
        "font_file": str(p.get("font_file") or NEUTRAL_FONT),
        "origin": str(p.get("origin") or "research"),
        # optional: "stars" draws a wanted-level cue; creator_outfit styles THUMB_LOOK=creator
        "badge": p.get("badge") if p.get("badge") in ("stars",) else "",
        "creator_outfit": _txt(p["creator_outfit"], 5, 200, "creator_outfit") if p.get("creator_outfit") else "",
        # optional, THUMB_LAYOUT=poster: emblem behind the subject and the poster background
        "emblem": _txt(p["emblem"], 5, 300, "emblem") if p.get("emblem") else "",
        "poster_scene": _txt(p["poster_scene"], 5, 400, "poster_scene") if p.get("poster_scene") else "",
    }
    if isinstance(p.get("font_source"), dict):
        out["font_source"] = p["font_source"]
    if len(out["hashtags"]) < 3 or not all(1 <= len(s.split()) <= 4 for s in out["lingo"]):
        raise ValueError("hashtags/lingo")
    return out


def schema():
    st = lambda: {"type": "string"}  # noqa: E731
    arr = lambda it: {"type": "array", "items": it}  # noqa: E731
    font = {"type": "object", "additionalProperties": False, "required": ["name", "url", "licence", "source"],
            "properties": {"name": st(), "url": st(), "licence": st(), "source": {"type": "string", "enum": list(SOURCES)}}}
    props = {"game": st(), "logo_font": st(), "logo_font_url": st(), "closest_free_fonts": arr(font),
             "palette": arr(st()), "accent_gradient": arr(st()), "text_fill": st(), "outline": st(),
             "type_treatment": st(), "scenes": arr(st()), "outfit_hint": st(), "lingo": arr(st()),
             "hashtags": arr(st()), "avoid": arr(st()), "sources": arr(st())}
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


# ---- built-in packs ----------------------------------------------------------------------------------------

def hexs(rgb):
    return "#%02X%02X%02X" % tuple(rgb)


def neutral(game):
    """Current refined look: Montserrat, white words, black outline, gold -> pink accent. No research needed."""
    return validate({
        "game": game, "logo_font": "unknown", "closest_free_fonts": [],
        "palette": ["#1B1B2F", "#FFD666", "#FF7096"], "accent_gradient": ["#FFD666", "#FF7096"],
        "text_fill": "#FFFFFF", "outline": "#000000",
        "type_treatment": "heavy extra-bold sans, thick black outline, soft drop shadow, one gold-to-pink accent word",
        "scenes": [
            "golden-hour light over an open landscape, the character stands relaxed in the foreground with a game controller in one hand, warm rim light and soft bokeh behind",
            "a neon-lit city street at blue hour, the character leans on a railing with a confident easy smile, colourful signs in soft bokeh",
            "a cosy gaming room with warm lamps and glowing screens, the character grins holding a controller up like a trophy, shallow depth of field",
            "a bright sunny outdoor plaza, the character looks around with wide-eyed wonder and a big excited smile, light motion blur on the background",
            "a dramatic sunset skyline, the character looks into the camera, calm and confident, strong warm rim light and film grain",
        ],
        "outfit_hint": "casual streetwear in colours that match the game's palette, no official costumes or logos",
        "lingo": ["FIRST TIME EVER", "COMPLETE NOOB", "I'M SO LOST", "DAY ONE", "WHAT AM I DOING", "STILL A NOOB"],
        "hashtags": ["gaming", "beginner", "firsttime"], "avoid": ["official logos", "official characters"],
        "sources": [], "font_file": NEUTRAL_FONT, "origin": "neutral"})


def seed_gta(game):
    """Grand Theft Auto VI pack from today's hard-coded look (no network)."""
    from . import stream_meta as sm  # lazy: stream_meta imports this module
    thumb = Path(c.s("THUMB_FONT").strip()).expanduser()
    font = str(thumb) if c.s("THUMB_FONT").strip() and thumb.is_file() else sm.IMPACT if Path(sm.IMPACT).is_file() else NEUTRAL_FONT
    return validate({
        "game": game, "logo_font": "Pricedown", "logo_font_url": "https://www.typodermicfonts.com/pricedown/",
        "closest_free_fonts": [{"name": "Pricedown", "url": "https://www.typodermicfonts.com/pricedown/",
                                "licence": "Typodermic desktop EULA, no redistribution", "source": "paid"}],
        "palette": [hexs(x) for x in ((45, 10, 100), (255, 55, 160), (255, 150, 40))],
        "accent_gradient": [hexs(x) for x in sm.SUNSET], "text_fill": "#FFFFFF", "outline": "#000000",
        "type_treatment": TYPE_GTA, "scenes": sm.SCENES, "badge": "stars", "emblem": "giant Roman numerals 'VI' filled with a pink-to-purple sunset gradient, palm-leaf silhouettes inside the strokes, a thin glowing orange edge", "poster_scene": "a dark purple neon night in a Miami-style city, palm tree and art-deco building silhouettes", "creator_outfit": "a bright pastel Vice City-style Hawaiian shirt and over-ear gaming headphones",
        "outfit_hint": "relaxed tropical streetwear: bright open shirt, sunglasses pushed up, no official costumes",
        "lingo": ["GTA ROOKIE", "FIRST TIME EVER", "COMPLETE NOOB", "I'M SO LOST", "WHAT AM I DOING", "STILL A NOOB"],
        "hashtags": [h.lstrip("#") for h in sm.HASHTAGS.split()],
        "avoid": ["Rockstar or GTA logos", "official GTA characters"], "sources": [], "font_file": font, "origin": "seed"})


# ---- fonts -------------------------------------------------------------------------------------------------

def extract_fonts(data, dest):
    """Write the .ttf/.otf files of a zip into dest by base name. Any unsafe member name rejects the whole zip."""
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for i in z.infolist():
            parts = PurePosixPath(i.filename).parts
            if i.filename.startswith(("/", "\\")) or ".." in parts or "\\" in i.filename:
                raise ValueError(f"unsafe zip member {i.filename!r}")
        for i in z.infolist():
            name = PurePosixPath(i.filename).name
            if name.lower().endswith((".ttf", ".otf")) and not i.is_dir():
                if i.file_size > FONT_CAP:
                    raise ValueError("font too big")
                (dest / name).write_bytes(z.read(i))
                out.append(dest / name)
    return out


def pick_font(files):
    """Heaviest upright static face first (thumbnail words want weight)."""
    def score(p):
        n = p.name.lower()
        return (("italic" in n) or ("[" in n), -max((w for k, w in (("black", 5), ("extrabold", 4), ("heavy", 4), ("bold", 3), ("regular", 2)) if k in n), default=1))
    return min(files, key=score) if files else None


def _public(url):
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname or u.hostname == "localhost":
        raise ValueError("font url must be public https")
    try:
        ip = ipaddress.ip_address(u.hostname)
    except ValueError:
        return u
    if not ip.is_global:
        raise ValueError("font url must be public https")
    return u


def fetch(url):
    """GET with a 20 MB cap."""
    _public(url)
    with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "stream-kit"}) as r:
        r.raise_for_status()
        _public(r.url)
        buf = b""
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) > FONT_CAP:
                raise ValueError("download over 20 MB")
    return buf


def google_files(url, name):
    """Font file URLs of a Google Fonts family (github.com/google/fonts, OFL/Apache folders only)."""
    host = (urlparse(url).hostname or "")
    if host not in ("fonts.google.com", "github.com", "raw.githubusercontent.com"):
        raise ValueError("not a Google Fonts url")
    if host == "github.com" and not urlparse(url).path.startswith("/google/fonts"):
        raise ValueError("not a Google Fonts url")
    if host == "raw.githubusercontent.com":
        return [url] if url.lower().endswith((".ttf", ".otf")) and urlparse(url).path.startswith("/google/fonts/") else []
    fam = re.sub(r"[^a-z0-9]", "", name.lower())
    for d in GOOGLE_DIRS:
        r = requests.get(f"https://api.github.com/repos/google/fonts/contents/{d}/{quote(fam)}", timeout=30)
        if r.status_code == 200:
            return [x["download_url"] for x in r.json() if x["name"].lower().endswith((".ttf", ".otf"))]
    return []


def get_font(pack):
    """Download the first closest free font that is Google Fonts or marked official free. Sets font_file."""
    from PIL import ImageFont
    dest = FONT_DIR / slug(pack["game"])
    found = []
    for f in pack["closest_free_fonts"]:
        try:
            if f["source"] == "google_fonts":
                files = []
                for u in google_files(f["url"], f["name"]):
                    (dest).mkdir(parents=True, exist_ok=True)
                    p = dest / Path(unquote(urlparse(u).path)).name
                    p.write_bytes(fetch(u))
                    files.append(p)
            elif f["source"] == "official_free":
                data = fetch(f["url"])
                if data[:2] == b"PK":
                    files = extract_fonts(data, dest)
                elif f["url"].lower().split("?")[0].endswith((".ttf", ".otf")):
                    dest.mkdir(parents=True, exist_ok=True)
                    files = [dest / Path(urlparse(f["url"]).path).name]
                    files[0].write_bytes(data)
                else:
                    continue
            else:
                continue
            best = pick_font(files)
            if best:
                ImageFont.truetype(str(best), 40)  # must load
                found.append((best, f))
                if "[" not in best.name:  # static face: weight is fixed, so it is what we want; variable ones are a fallback
                    break
        except Exception as e:  # network, zip, bad font: try the next candidate
            log.warning("font %s not downloaded: %s", f["name"], c.mask(str(e))[:150])
    if found:
        best, f = found[-1] if "[" not in found[-1][0].name else found[0]
        pack["font_file"] = str(best.relative_to(c.ROOT)) if best.is_relative_to(c.ROOT) else str(best)
        pack["font_source"] = {"name": f["name"], "url": f["url"], "licence": f["licence"], "bytes": best.stat().st_size}
        print(f"Font downloaded: {f['name']} ({f['licence']}) from {f['url']} -> {pack['font_file']}")
        return pack
    if pack["logo_font"].lower() != "unknown":
        print(f"Logo font \"{pack['logo_font']}\" is not a free download; using {Path(pack['font_file']).name}. "
              f"Get it here: {pack.get('logo_font_url', 'unknown')}")
    return pack


# ---- research / load ---------------------------------------------------------------------------------------

def research(game):
    from . import stream_meta as sm
    if not sm.codex_ok():
        raise RuntimeError("codex not logged in with ChatGPT")
    with tempfile.TemporaryDirectory() as d:
        prompt = PROMPT.format(game=game, example=sm.SCENES[0])
        raw = json.loads(sm.run_codex(prompt, d, "read-only", TIMEOUT_RESEARCH, schema(), search=True))
    raw["game"] = game
    return validate(raw)


def save(pack, game):
    p = pack_path(game)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pack, indent=1))


def load(game=None, refresh=False, research_ok=True):
    """The game's style pack; researched (and saved) first when missing or refresh. Never raises."""
    game = game or c.s("STREAM_GAME")
    p = pack_path(game)
    old = None
    if p.exists():
        try:
            old = validate(json.loads(p.read_text()))
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("style pack %s unreadable, rebuilding", p.name)
    if old and not refresh:
        return old
    try:
        if slug(game) == "grand-theft-auto-vi" and not refresh:
            pack = seed_gta(game)
        elif research_ok:
            pack = research(game)
            pack = get_font(pack)
        else:
            return old or neutral(game)
        save(pack, game)
        return pack
    except Exception as e:
        log.warning("style research for %s failed (%s): using %s pack", game, c.mask(str(e))[:150],
                    "the saved" if old else "neutral")
        return old or neutral(game)


def get(game=None):
    """Pack for rendering: never researches, never needs the network."""
    return load(game, research_ok=False)


def main():
    ap = argparse.ArgumentParser(prog="python -m kit.game_style")
    ap.add_argument("--game", default=None, help="game name (default STREAM_GAME)")
    ap.add_argument("--refresh", action="store_true", help="research again even when a pack exists")
    ap.add_argument("--get-font", action="store_true", help="download the closest free font (Google Fonts / official free only)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    game = a.game or c.s("STREAM_GAME")
    t0 = time.time()
    pack = load(game, a.refresh)
    if a.get_font:
        pack = get_font(pack)
        if pack["origin"] != "neutral":
            save(pack, game)
    print(f"{pack_path(game)} ({pack['origin']}, {time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()


def clip_hashtags():
    """Hashtags for clip uploads: HASHTAGS_DEFAULT if set, else the current game's pack (no network)."""
    own = c.s("HASHTAGS_DEFAULT").split()
    return own or ["#" + str(h).lstrip("#") for h in get().get("hashtags", [])[:4]]
