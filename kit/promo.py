"""Go-live promo: an 8 s vertical "LIVE NOW" video, posted through the streamer's own signed-in Chrome
(Claude in Chrome, run headless by `claude -p --chrome` on the Claude plan, no API keys):
YouTube community post (image), Instagram Reel, TikTok video.

    python -m kit.promo --card     build state/promo/live.png + live.mp4 only (no posting)
    python -m kit.promo            build and post

Called from kit.golive when OBS starts publishing. Instagram web has no Story upload, and Story
link stickers are app-only, so Instagram gets a Reel (it also reaches non-followers).
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config as c, game_style

OUT = c.STATE / "promo"
FONT = Path(__file__).parent / "third_party" / "pricedown" / "Pricedown Bl.otf"
W, H, SECS = 1080, 1920, 8


def title():
    try:
        return json.loads((c.STATE / "stream_meta.json").read_text()).get("title") or ""
    except (OSError, ValueError):
        return ""


def text(d, xy, s, size, fill="#fff", stroke=10):
    d.text(xy, s, font=ImageFont.truetype(str(FONT), size), fill=fill, anchor="mm",
           stroke_width=stroke, stroke_fill="#000")


def card(path=None):
    """1080x1920 PNG: the Starting Soon art cropped tall around the character, plus LIVE NOW text."""
    art = Image.open(c.STATE / "overlay" / "scene_starting.png").convert("RGB")
    cw = round(art.height * W / H)
    x0 = min(max(round(art.width * 0.74 - cw / 2), 0), art.width - cw)  # character sits right of centre
    im = art.crop((x0, 0, x0 + cw, art.height)).resize((W, H), Image.LANCZOS)
    shade = Image.linear_gradient("L").resize((W, H)).point(lambda v: max(0, v - 90) * 2)  # dark lower half
    im = Image.composite(Image.new("RGB", (W, H), (20, 4, 48)), im, shade)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((W / 2 - 150, 1150, W / 2 + 150, 1230), 40, fill="#E8173A")
    d.ellipse((W / 2 - 95, 1172, W / 2 - 59, 1208), fill="#fff")  # Pricedown has no ● glyph
    d.text((W / 2 + 25, 1190), "LIVE", font=ImageFont.truetype(str(FONT), 64), fill="#fff", anchor="mm")
    text(d, (W / 2, 1360), "LIVE NOW", 190, fill="#FFD666", stroke=12)
    for i, line in enumerate(textwrap.wrap(title(), 22)[:2]):
        text(d, (W / 2, 1530 + i * 90), line, 76, stroke=7)
    text(d, (W / 2, 1760), "LINK IN BIO", 70, fill="#FF7096", stroke=7)
    path = Path(path or OUT / "live.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def video():
    """8 s mp4 with a slow push-in and a silent audio track (some apps reject video without audio)."""
    png, mp4 = card(), OUT / "live.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(png), "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=stereo", "-t", str(SECS), "-vf",
                    f"scale={W * 2}:-1,zoompan=z='1+0.0004*on':d={SECS * 30}:s={W}x{H}:fps=30"
                    ":x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2',format=yuv420p",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-c:a", "aac", "-shortest",
                    "-movflags", "+faststart", str(mp4)], check=True)
    return mp4


STEPS = """You post a go-live promo for a streamer who is live right now. Use only the Claude in Chrome tools.
Create one new tab with tabs_create_mcp, do everything in it, close it at the end. Chrome is already signed in.
Never change account settings, never accept or turn on prompts that change settings (press Cancel/Not now),
never post anything except what is below, and post each item once. Files: IMAGE={png} VIDEO={mp4}

1. YouTube community post: open {yt}/posts. Click the "Share an image..." text box, type TEXT_YT.
   Click "Image", upload IMAGE into its hidden file input with file_upload (never click file inputs).
   Put the caret at the end of the text and type one space (the Post button stays disabled until a text edit
   after the upload). Click the enabled "Post" button. Check the new post text appears on the page.
2. Instagram Reel (account must be {ig_user}; stop this step if another account is signed in):
   open https://www.instagram.com/, click Create, then Post, upload VIDEO into the dialog's file input,
   click OK on the "Video posts are now shared as reels" notice, open the crop menu (bottom-left icon),
   pick 9:16, Next, Next, click "Add a caption...", type TEXT_LONG, click Share, wait for "Your reel has been shared".
3. TikTok: open https://www.tiktok.com/tiktokstudio/upload, upload VIDEO into the file input, Cancel any
   "automatic content checks" prompt, dismiss tips, click the Description box, select all (cmd+a) and type
   TEXT_TT, then type each of {tt_tags} separately, press Escape, scroll to and click "Post".

TEXT_YT: 🔴 LIVE NOW: {title} Come hang out 👉 {yt}/live
TEXT_LONG: 🔴 LIVE NOW on YouTube & Twitch: {title} Link in bio 👆

{tags} #gaming #livestream #twitch #youtubegaming
TEXT_TT: 🔴 LIVE NOW on YouTube & Twitch: {title} Search {name} on YouTube or {twitch} on Twitch{space}
Reply with one line per platform: youtube/instagram/tiktok = posted | skipped (reason)."""


def run():
    """Build the video, then let headless Claude (Sonnet, Claude plan) post it through Chrome. -> its report."""
    if not c.b("LIVE_PROMO"):
        return "off (LIVE_PROMO=false)"
    mp4 = video()
    pack_tags = game_style.load(research_ok=False).get("hashtags", [])[:2]
    prompt = STEPS.format(png=OUT / "live.png", mp4=mp4, yt=c.s("LINK_YOUTUBE").rstrip("/"), title=title(),
                          ig_user=c.s("LINK_INSTAGRAM").rstrip("/").rsplit("/", 1)[-1],
                          twitch=c.s("TWITCH_CHANNEL") or c.s("LINK_TWITCH").rstrip("/").rsplit("/", 1)[-1], space=" ",
                          name=c.s("STREAMER_NAME") or "my channel", tags=" ".join("#" + h for h in pack_tags),
                          tt_tags=", ".join(f'"#{h.lower()} "' for h in [*pack_tags[:1], "gaming", "livestream"]))
    r = subprocess.run(["claude", "-p", "--chrome", "--model", c.s("PROMO_MODEL"),
                        "--allowedTools", "mcp__claude-in-chrome__*"],
                       input=prompt, capture_output=True, text=True, timeout=1200, cwd=OUT)
    report = (r.stdout or r.stderr).strip()[-1500:]
    (OUT / "last_report.txt").write_text(report)
    return report


if __name__ == "__main__":
    print(video() if "--card" in sys.argv else run())
