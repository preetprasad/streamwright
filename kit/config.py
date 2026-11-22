"""Settings: .env (or process env) with typed getters and defaults. Blank value = default."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # never overrides variables already set in the environment

DEFAULTS = {
    "OBS_INGEST_PORT": "1935",
    "YOUTUBE_RTMP_URL": "rtmps://a.rtmps.youtube.com/live2",
    "TWITCH_RTMP_URL": "rtmps://ingest.global-contribute.live-video.net/app",
    "BUFFER_MINUTES": "20", "PRE_ROLL": "20", "POST_ROLL": "8",
    "MUSIC_CHECK": "true",
    "CANDIDATE_THRESHOLD": "3.0", "W_AUDIO": "1.0", "W_CHAT": "1.0", "COOLDOWN_SECONDS": "90",
    "JUDGE_MODEL": "qwen3-vl:8b", "JUDGE_MIN_SCORE": "7", "WHISPER_MODEL": "models/ggml-base.en.bin",
    "CROP_MODE": "blur", "CAPTIONS": "true", "DRY_RUN": "true", "MAX_POSTS_PER_HOUR": "4",
    "DELETE_CLIPS_AFTER_UPLOAD": "false", "YOUTUBE_CLIENT_SECRET_FILE": "secrets/client_secret.json",
    "YOUTUBE_PRIVACY": "public", "TIKTOK_REDIRECT_URI": "http://127.0.0.1:8765/callback/",
    "TIKTOK_MODE": "draft", "IG_GRAPH_VERSION": "v27.0", "HASHTAGS_DEFAULT": "",
    "JUDGE_CUE_BONUS": "1", "FACECAM": "auto", "CLAUDE_JUDGE": "off", "CLAUDE_JUDGE_MODEL": "sonnet",
    "CLAUDE_JUDGE_MIN_LOCAL": "5", "CLAUDE_JUDGE_MAX_PER_HOUR": "10", "CLAUDE_JUDGE_TIMEOUT": "120",
    "JUDGE_BYPASS": "false", "KEEP_TMP": "false", "OLLAMA_URL": "http://localhost:11434",
    "STREAM_META": "off", "STREAM_GAME": "Grand Theft Auto VI", "STREAM_GAME_SHORT": "", "STREAMER_NAME": "", "SCENE_KICKER": "",
    "STREAM_GEN": "", "STREAM_THUMB": "", "LINK_YOUTUBE": "", "LINK_TWITCH": "", "LINK_TIKTOK": "",
    "LINK_INSTAGRAM": "", "LINK_DISCORD": "", "LINK_TIP": "", "LIVE_PROMO": "true", "PROMO_MODEL": "sonnet", "LINK_SHORTS": "", "LINK_WEBSITE": "", "FACE_REF": "", "STYLE_REF": "", "THUMB_TYPE": "game", "THUMB_LIVE": "false", "THUMB_LOOK": "hero", "THUMB_LAYOUT": "stake", "THUMB_POSE": "", "THUMB_EXPRESSION": "", "THUMB_FACE_SIDE": "left", "LIKENESS_MAX": "0.40", "LIKENESS_TRIES": "3", "LIKENESS_PYTHON": "python3", "THUMB_FONT": "", "STREAM_SCHEDULE": "", "STREAM_ABOUT": "",
    "CHATBOT_PORT": "8777", "DISCORD_WEBHOOK_URL": "", "DISCORD_WELCOMES": "false", "DISCORD_LIVE_PING": "", "DISCORD_LIVE_COOLDOWN_MIN": "120", "YOUTUBE_CHAT_REPLIES": "true", "YT_POLL_SECONDS": "15", "TWITCH_BOT_NICK": "", "TWITCH_BOT_OAUTH": "",
    "WELCOME_TEMPLATE": "Welcome to the stream, {user}! Glad you're here, sit back and enjoy.",
    "THANKS_TEMPLATE": "Huge thanks {user} for the {amount}! You're awesome. 💛", "CHATBOT_COOLDOWN": "8", "TIMER_MINUTES": "20", "TIMER_MIN_CHAT": "5",
    "EXPLAINER_MODEL": "sonnet", "VOICE_PYTHON": "voice/venv/bin/python",
    "VOICE_DIR": "voice",
    "STREAM_ANGLE": "",  # blank = derived from STREAM_GAME, see angle()
}


def s(key):
    v = os.environ.get(key, "")
    return v if v != "" else DEFAULTS.get(key, "")


def f(key): return float(s(key))
def i(key): return int(float(s(key)))
def b(key): return s(key).strip().lower() in ("1", "true", "yes", "on")


def game_short():
    """Short game label for overlays (STREAM_GAME_SHORT, else STREAM_GAME)."""
    return s("STREAM_GAME_SHORT").strip() or s("STREAM_GAME")


def angle():
    return s("STREAM_ANGLE").strip() or f"first time ever playing {s('STREAM_GAME')}; total beginner"


def path(key):
    p = Path(s(key))
    return p if p.is_absolute() else ROOT / p


# DATA_DIR lets the selftest run in a throwaway folder; default is the project folder.
DATA = Path(os.environ.get("DATA_DIR") or ROOT)
BUFFER = DATA / "buffer" / "live" / "obs"
CLIPS = DATA / "clips"
STATE = DATA / "state"
TMP = STATE / "tmp"
SECRETS = ROOT / "secrets"


def ensure_dirs():
    for d in (BUFFER, CLIPS, TMP, SECRETS):
        d.mkdir(parents=True, exist_ok=True)


def mask(text):
    """Hide every secret-looking env value (keys, tokens, secrets) in a string."""
    for k, v in os.environ.items():
        if v and len(v) >= 4 and any(w in k for w in ("KEY", "SECRET", "TOKEN", "OAUTH", "WEBHOOK")):
            text = text.replace(v, "***")
    return text
