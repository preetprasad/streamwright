"""Offline test of kit/stream_meta.py + kit/links.py (template text, local thumbnail, no network): python test_stream_meta.py"""
import json
import os
import tempfile
from pathlib import Path

os.environ.update(OLLAMA_URL="http://127.0.0.1:1", FACECAM_BOX="", STREAM_GEN="template", STREAM_THUMB="local",
                  LINK_YOUTUBE="https://www.youtube.com/@examplegaming", LINK_TWITCH="https://www.twitch.tv/examplegaming",
                  LINK_TIKTOK="https://www.tiktok.com/@examplegaming", LINK_INSTAGRAM="https://www.instagram.com/examplegaming",
                  LINK_WEBSITE="https://www.example.com", STREAM_SCHEDULE="Live Mon/Wed/Fri 8pm")
from PIL import Image  # noqa: E402

from kit import config as c, links, stream_meta as sm  # noqa: E402

LINKS = [os.environ[k] for k in ("LINK_YOUTUBE", "LINK_TWITCH", "LINK_TIKTOK", "LINK_INSTAGRAM")]

# links: all four when set, blanks skipped
assert "https://www.example.com" in links.block() and "example.com" not in links.short()
assert all(u in links.block() for u in LINKS) and all(u in links.short() for u in LINKS)
assert "\n" not in links.short()
os.environ["LINK_TIKTOK"] = ""
assert "tiktok" not in links.block() and "tiktok" not in links.short() and "TikTok" not in links.short()
os.environ["LINK_TIKTOK"] = LINKS[2]
# clip captions: 3000-char input never exceeds the limit and keeps the link line intact
for limit in (2200, 5000):
    out = links.fit("x" * 3000, limit)
    assert len(out) <= limit and out.endswith(links.short()), limit
assert links.fit("short", 2200) == "short\n\n" + links.short()
os.environ.update(LINK_YOUTUBE="", LINK_TWITCH="", LINK_TIKTOK="", LINK_INSTAGRAM="", LINK_WEBSITE="", LINK_DISCORD="", LINK_SHORTS="", LINK_TIP="")
assert links.block() == "" and links.short() == "" and links.fit("hi", 10) == "hi"
os.environ.update(LINK_YOUTUBE=LINKS[0], LINK_TWITCH=LINKS[1], LINK_TIKTOK=LINKS[2], LINK_INSTAGRAM=LINKS[3], LINK_WEBSITE="https://www.example.com")

with tempfile.TemporaryDirectory() as tmp:
    c.STATE, c.BUFFER = Path(tmp) / "state", Path(tmp) / "buffer"  # empty buffer: gradient background
    m = sm.generate()
    assert m["source"] == "template" and m["day"] == 1
    assert 0 < len(m["title"]) <= 100, m["title"]
    assert len(m["tags"]) <= 15 and len(",".join(m["tags"])) <= 400
    d = m["description"]
    assert len(d) <= 5000 and all(u in d for u in LINKS) and "Live Mon/Wed/Fri 8pm" in d
    assert d.rstrip().endswith(sm.HASHTAGS) and d.startswith("I have never played")
    assert d.index("#GTA6") < d.index("#GTAVI") < d.index("#gaming")
    for sec in ("About this stream", "Watch and follow", "Where to find me"):
        assert sec in d, sec
    assert len(m["twitch_title"]) <= 140 and len(m["twitch_notification"]) <= 140
    bio = m["twitch_bio"]
    bare = lambda u: u.split('://', 1)[-1].removeprefix('www.')  # bio uses compact links
    assert 0 < len(bio) <= 300 and bare(LINKS[0]) in bio and bare(LINKS[3]) in bio and bare(LINKS[1]) not in bio
    assert m["thumb_accent"] in m["thumb_text"].split()
    thumb = Path(m["thumb"])
    assert thumb.exists() and thumb.stat().st_size < 2_000_000
    assert Image.open(thumb).size == (1280, 720)
    saved = json.loads((c.STATE / "stream_meta.json").read_text())
    assert saved["title"] == m["title"] and saved["twitch_bio"] == bio
    assert sm.generate()["day"] == 2
    assert sm.template(1)["title"] != sm.template(2)["title"]
    # --thumb-only keeps the day; --plate composes on a synthetic image, no Codex call
    plate = Path(tmp) / "plate.png"
    Image.effect_noise((1536, 1024), 80).convert("RGB").save(plate)
    t = sm.thumb_only("NEVER PLAYED GTA", "never", str(plate))
    assert t["day"] == 2 and t["thumb_text"] == "NEVER PLAYED GTA" and t["thumb_accent"] == "NEVER"
    p = Path(t["thumb"])
    assert Image.open(p).size == (1280, 720) and p.stat().st_size < 2_000_000
    # a good model reply is accepted; bad ones raise
    good = {"title": "t" * 70, "hook": "h" * 60, "about": "a" * 40, "tags": ["a"] * 30 + ["#b"],
            "thumb_text": "i am lost", "thumb_accent": "lost", "twitch_title": "w" * 300,
            "twitch_notification": "n" * 300, "twitch_bio": "b" * 280}
    ok = sm.clean(good, 1)
    assert ok["tags"] == ["a", "b"] and "#GTA6" in ok["description"] and ok["thumb_accent"] == "LOST"
    assert len(ok["twitch_title"]) <= 140 and len(ok["twitch_notification"]) <= 140 and len(ok["twitch_bio"]) <= 300
    assert LINKS[0].split("://", 1)[-1].removeprefix("www.") in ok["twitch_bio"] and len(ok["description"]) <= 5000
    for bad in ({**good, "title": "x" * 101}, {**good, "thumb_text": "one"}, {**good, "hook": "short"}):
        try:
            sm.clean(bad, 1)
        except ValueError:
            continue
        raise AssertionError("accepted bad output")
# per-game style packs (no network: codex forced unavailable)
import io, zipfile  # noqa: E402
from kit import game_style as gs  # noqa: E402
sm.codex_ok = lambda: False
MONT = str(c.ROOT / gs.NEUTRAL_FONT)
with tempfile.TemporaryDirectory() as tmp:
    c.STATE = Path(tmp) / "state"
    n = gs.neutral("Some New Game")
    assert gs.validate(n) == n and n["origin"] == "neutral" and len(n["scenes"]) == 5 and len(n["lingo"]) == 6
    assert gs.load("Some New Game") == n and not gs.pack_path("Some New Game").exists()  # research failed: neutral, not saved
    os.environ["THUMB_FONT"] = MONT
    g = gs.seed_gta("Grand Theft Auto VI")
    assert g["font_file"] == MONT and g["accent_gradient"] == [gs.hexs(x) for x in sm.SUNSET] and g["scenes"] == sm.SCENES
    assert "GTA ROOKIE" in g["lingo"] and g["text_fill"] == "#FFFFFF" and g["outline"] == "#000000"
    assert gs.load("Grand Theft Auto VI") == gs.get("Grand Theft Auto VI") and gs.pack_path("Grand Theft Auto VI").exists()
    os.environ["THUMB_FONT"] = ""
    for bad in ("#FFF", "red", "#GGGGGG", "#12345678", 5):
        try:
            gs._hex(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted hex {bad!r}")
    try:
        gs.validate({**n, "palette": ["#FFFFFF", "#000000", "blue"]})
        raise AssertionError("accepted bad palette")
    except ValueError:
        pass
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../evil.ttf", b"x")
    try:
        gs.extract_fonts(evil.getvalue(), Path(tmp) / "fonts")
        raise AssertionError("accepted ../ zip member")
    except ValueError:
        assert not (Path(tmp) / "evil.ttf").exists()
    ok_zip = io.BytesIO()
    with zipfile.ZipFile(ok_zip, "w") as z:
        z.writestr("static/Foo-Bold.ttf", b"x")
        z.writestr("readme.txt", b"x")
    assert [p.name for p in gs.extract_fonts(ok_zip.getvalue(), Path(tmp) / "fonts")] == ["Foo-Bold.ttf"]
    # compose with a pack that has another gradient and colours
    other = {**n, "accent_gradient": ["#00FF00", "#0000FF"], "text_fill": "#FFFF00", "outline": "#220000", "font_file": MONT}
    bg = sm.sunset(other)
    out = sm.compose(bg, None, "NEW GAME HYPE", "HYPE", 3, other)
    assert Image.open(out).size == (1280, 720)
# best_plate: regenerates until the face matches, keeps the closest; no network (codex + scorer faked)
import itertools as _it
from PIL import Image as _I
_seq = _it.count()
_scores = [0.55, 0.47, 0.38]
def _fake_plate(day):
    n = next(_seq); out = sm.c.STATE / "thumb_plate.png"
    _I.new("RGB", (64, 36), (n * 60, 0, 0)).save(out); return out
_orig = (sm.plate_from_codex, sm.likeness, sm.face_ref)
sm.plate_from_codex = _fake_plate
sm.likeness = lambda ps: {str(ps[0]): {"mean": _scores[int(str(ps[0])[-5])], "best": 0.3}}
sm.face_ref = lambda key="FACE_REF": Path("x.jpg")
sm.c.STATE.mkdir(parents=True, exist_ok=True)
os.environ["LIKENESS_TRIES"], os.environ["LIKENESS_MAX"] = "3", "0.40"
_out = sm.best_plate(1)
assert next(_seq) == 3, "stops after the third try (0.38 <= 0.40)"
assert _I.open(_out).getpixel((0, 0))[0] == 120, "keeps the closest plate (third)"
_seq = _it.count(); _scores[:] = [0.50, 0.44, 0.48]
os.environ["LIKENESS_TRIES"] = "3"
_out = sm.best_plate(1)
assert _I.open(_out).getpixel((0, 0))[0] == 60, "none pass: keeps the closest (second)"
sm.plate_from_codex, sm.likeness, sm.face_ref = _orig

print("ok")
