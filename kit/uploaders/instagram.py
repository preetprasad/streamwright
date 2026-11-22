"""Instagram Reels via Graph API resumable upload (rupload.facebook.com)."""
import time

import requests

from .. import config as c, links
from .. import game_style


def enabled():
    return bool(c.s("IG_USER_ID") and c.s("IG_ACCESS_TOKEN"))


def upload(meta, path):
    ver, ig, tok = c.s("IG_GRAPH_VERSION"), c.s("IG_USER_ID"), c.s("IG_ACCESS_TOKEN")
    g = f"https://graph.facebook.com/{ver}"
    caption = links.fit(f"{meta['caption']}\n\n{' '.join(meta['hashtags'] + game_style.clip_hashtags())}", 2200)
    r = requests.post(f"{g}/{ig}/media", timeout=60, data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption, "share_to_feed": "true",
        "access_token": tok})
    j = r.json()
    if "id" not in j:
        raise RuntimeError(f"instagram container: {j.get('error', j)}")
    cid = j["id"]
    size = path.stat().st_size
    with open(path, "rb") as fh:
        r = requests.post(j.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{ver}/{cid}",
                          data=fh, timeout=600, headers={"Authorization": f"OAuth {tok}", "offset": "0",
                                                         "file_size": str(size)})
    if not r.ok or not r.json().get("success"):
        raise RuntimeError(f"instagram upload: {r.text[:200]}")
    for _ in range(40):  # up to 10 min
        st = requests.get(f"{g}/{cid}", params={"fields": "status_code", "access_token": tok}, timeout=30).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"instagram processing: {st}")
        time.sleep(15)
    else:
        raise RuntimeError("instagram processing timeout")
    r = requests.post(f"{g}/{ig}/media_publish", data={"creation_id": cid, "access_token": tok}, timeout=60).json()
    if "id" not in r:
        raise RuntimeError(f"instagram publish: {r.get('error', r)}")
    return r["id"]
