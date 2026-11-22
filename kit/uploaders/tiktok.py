"""TikTok Content Posting API v2: draft (inbox, video.upload) or direct (video.publish), chunked FILE_UPLOAD."""
import json
import time

import requests

from .. import config as c, links
from .. import game_style

API = "https://open.tiktokapis.com/v2"
TOKEN = c.SECRETS / "tiktok_token.json"
MB = 1 << 20


def enabled():
    return TOKEN.exists() and bool(c.s("TIKTOK_CLIENT_KEY"))


def save_token(d):
    d["expires_at"] = time.time() + d["expires_in"]
    TOKEN.write_text(json.dumps(d))
    TOKEN.chmod(0o600)


def token():
    d = json.loads(TOKEN.read_text())
    if d["expires_at"] - 60 < time.time():  # access token lasts 2 h; refresh token rotates every refresh
        r = requests.post(f"{API}/oauth/token/", data={
            "client_key": c.s("TIKTOK_CLIENT_KEY"), "client_secret": c.s("TIKTOK_CLIENT_SECRET"),
            "grant_type": "refresh_token", "refresh_token": d["refresh_token"]}, timeout=30)
        d = r.json()
        if "access_token" not in d:
            raise RuntimeError(f"tiktok refresh failed: {d.get('error')} {d.get('error_description')}")
        save_token(d)
    return d["access_token"]


def call(path, tok, body):
    r = requests.post(f"{API}{path}", json=body, timeout=60, headers={
        "Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8"})
    j = r.json()
    if j.get("error", {}).get("code") != "ok":
        raise RuntimeError(f"tiktok {path}: {j.get('error')}")
    return j["data"]


def upload(meta, path):
    tok, direct = token(), c.s("TIKTOK_MODE") == "direct"
    size = path.stat().st_size
    chunk = size if size <= 64 * MB else 10 * MB  # last chunk absorbs the remainder (allowed up to 128 MB)
    n = max(size // chunk, 1)
    body = {"source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                            "total_chunk_count": n}}
    if direct:
        info = call("/post/publish/creator_info/query/", tok, {})
        if meta["window"][1] - meta["window"][0] > info.get("max_video_post_duration_sec", 1e9):
            raise RuntimeError("clip longer than this creator may post")
        opts = info["privacy_level_options"]  # unaudited apps: only SELF_ONLY works
        title = f"{meta['title']} {' '.join(meta['hashtags'] + game_style.clip_hashtags())}"
        body["post_info"] = {"title": links.fit(title, 2200),
                             "privacy_level": "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in opts else "SELF_ONLY"}
    data = call("/post/publish/video/init/" if direct else "/post/publish/inbox/video/init/", tok, body)
    with open(path, "rb") as fh:
        for k in range(n):
            first = k * chunk
            last = size - 1 if k == n - 1 else first + chunk - 1
            fh.seek(first)
            r = requests.put(data["upload_url"], data=fh.read(last - first + 1), timeout=300, headers={
                "Content-Type": "video/mp4", "Content-Range": f"bytes {first}-{last}/{size}"})
            r.raise_for_status()
    for _ in range(60):  # up to 5 min
        st = call("/post/publish/status/fetch/", tok, {"publish_id": data["publish_id"]})
        if st["status"] == ("PUBLISH_COMPLETE" if direct else "SEND_TO_USER_INBOX"):
            return data["publish_id"]
        if st["status"] in ("FAILED", "PUBLISH_COMPLETE"):
            raise RuntimeError(f"tiktok status {st['status']}: {st.get('fail_reason')}")
        time.sleep(5)
    raise RuntimeError("tiktok publish status timeout")
