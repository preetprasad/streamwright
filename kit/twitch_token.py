"""One-time Twitch chat token for the chatbot, no client secret needed (implicit grant).

    python -m kit.twitch_token CLIENT_ID [NICK]

Opens Twitch's authorize page; after you click Authorize, Twitch redirects to http://localhost:3000 with the
token in the URL fragment. A tiny page here posts it back and it is written to .env as TWITCH_BOT_OAUTH
(TWITCH_BOT_NICK = NICK or TWITCH_CHANNEL). The token is never printed. The app must list
http://localhost:3000 as an OAuth redirect URL.
"""
import http.server
import re
import secrets
import sys
import threading
import urllib.parse
import webbrowser

from . import config as c

PAGE = b"""<!doctype html><meta charset=utf-8><body style="font:16px sans-serif;background:#111;color:#eee">
<p id=m>Saving...</p><script>
const p=new URLSearchParams(location.hash.slice(1));
fetch('/save',{method:'POST',body:JSON.stringify({t:p.get('access_token'),s:p.get('state'),e:p.get('error_description')})})
.then(r=>r.text()).then(x=>{document.getElementById('m').textContent=x;history.replaceState(null,'','/')});
</script>"""


def set_env(key, val):
    path = c.ROOT / ".env"
    text = path.read_text() if path.exists() else ""
    line = f"{key}={val}"
    text, n = re.subn(rf"^{key}=.*$", lambda _: line, text, flags=re.M)
    if not n:
        text += ("" if text.endswith("\n") or not text else "\n") + line + "\n"
    path.write_text(text)


def main(client_id, nick):
    state, done = secrets.token_urlsafe(16), threading.Event()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)

        def do_POST(self):
            import json
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if d.get("s") != state or not d.get("t"):
                msg = f"Failed: {d.get('e') or 'no token or wrong state'}. Close this tab and run again."
            else:
                set_env("TWITCH_BOT_NICK", nick)
                set_env("TWITCH_BOT_OAUTH", "oauth:" + d["t"])
                msg = "Saved to .env. You can close this tab."
                done.set()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(msg.encode())

    srv = http.server.HTTPServer(("127.0.0.1", 3000), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "https://id.twitch.tv/oauth2/authorize?" + urllib.parse.urlencode({
        "response_type": "token", "client_id": client_id, "redirect_uri": "http://localhost:3000",
        "scope": "chat:read chat:edit", "state": state, "force_verify": "true"})
    print("Open this and click Authorize:\n" + url)
    webbrowser.open(url)
    ok = done.wait(600)
    srv.shutdown()
    print("token saved to .env" if ok else "timed out after 10 min")
    return ok


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(0 if main(sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else c.s("TWITCH_CHANNEL")).strip().lower()) else 1)
