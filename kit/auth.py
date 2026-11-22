"""One-time OAuth helpers: python -m kit.auth youtube|tiktok"""
import base64
import hashlib
import secrets
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from . import config as c


def youtube():
    from google_auth_oauthlib.flow import InstalledAppFlow
    from .uploaders.youtube import SCOPES, TOKEN
    flow = InstalledAppFlow.from_client_secrets_file(str(c.path("YOUTUBE_CLIENT_SECRET_FILE")), SCOPES)
    creds = flow.run_local_server(port=8090, timeout_seconds=900)  # fixed port, 15 min to finish sign-in
    c.SECRETS.mkdir(exist_ok=True)
    TOKEN.write_text(creds.to_json())
    TOKEN.chmod(0o600)
    print("saved", TOKEN)


def tiktok():
    from .uploaders.tiktok import TOKEN, API, save_token
    redirect = c.s("TIKTOK_REDIRECT_URI")
    u = urllib.parse.urlparse(redirect)
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = hashlib.sha256(verifier.encode()).hexdigest()  # TikTok wants hex, not base64url
    state = secrets.token_urlsafe(16)
    scope = "video.publish" if c.s("TIKTOK_MODE") == "direct" else "video.upload"
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": c.s("TIKTOK_CLIENT_KEY"), "scope": scope, "response_type": "code",
        "redirect_uri": redirect, "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            got.update(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Done, you can close this tab.")

        def log_message(self, *a):
            pass

    server = HTTPServer((u.hostname, u.port), H)
    print("Opening browser to authorize TikTok...")
    webbrowser.open(url)
    server.handle_request()
    if got.get("state") != state or "code" not in got:
        sys.exit(f"authorization failed: {got.get('error_description') or got}")
    r = requests.post(f"{API}/oauth/token/", timeout=30, data={
        "client_key": c.s("TIKTOK_CLIENT_KEY"), "client_secret": c.s("TIKTOK_CLIENT_SECRET"), "code": got["code"],
        "grant_type": "authorization_code", "redirect_uri": redirect, "code_verifier": verifier}).json()
    if "access_token" not in r:
        sys.exit(f"token exchange failed: {r.get('error')} {r.get('error_description')}")
    c.SECRETS.mkdir(exist_ok=True)
    save_token(r)
    print("saved", TOKEN)


if __name__ == "__main__":
    {"youtube": youtube, "tiktok": tiktok}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(
        "usage: python -m kit.auth youtube|tiktok"))()
