"""YouTube Shorts via Data API v3 videos.insert (resumable)."""
import json

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .. import config as c, links
from .. import game_style

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]
TOKEN = c.SECRETS / "youtube_token.json"


def enabled():
    return TOKEN.exists()


def credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    # refresh with the scopes the token was granted: an old upload-only token must keep working
    scopes = json.loads(TOKEN.read_text()).get("scopes") or SCOPES
    creds = Credentials.from_authorized_user_file(str(TOKEN), scopes)
    if not creds.valid:
        creds.refresh(Request())
        TOKEN.write_text(creds.to_json())
    return creds


def upload(meta, path):
    yt = build("youtube", "v3", credentials=credentials(), cache_discovery=False)
    tags = " ".join(meta["hashtags"] + game_style.clip_hashtags())
    body = {"snippet": {"title": meta["title"][:91] + " #Shorts", "description": links.fit(f"{meta['caption']}\n\n{tags}", 5000),
                        "categoryId": "20"},
            "status": {"privacyStatus": c.s("YOUTUBE_PRIVACY"), "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": bool(meta.get("contains_synthetic_media"))}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(path), mimetype="video/mp4", resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]
