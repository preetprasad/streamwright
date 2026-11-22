"""Live chat bot: welcomes first-time chatters, announces paid events, answers !commands (YouTube + Twitch).
Outputs: OBS pages (/overlay alerts, /layout frame, /starting /brb /ending on http://127.0.0.1:CHATBOT_PORT), Discord webhook, Twitch chat replies.
Run: python -m kit.chatbot [--demo]"""
import argparse
import itertools
import html
import json
import logging
import mimetypes
import random
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as c, links

log = logging.getLogger("chatbot")
PAID = {"superchat", "supersticker", "membership", "bits", "sub", "resub", "giftsub", "raid"}
YT_KINDS = {"text_message": "chat", "paid_message": "superchat", "paid_sticker": "supersticker", "membership_item": "membership"}
TIERS = {"1000": "Tier 1", "2000": "Tier 2", "3000": "Tier 3", "Prime": "Prime"}


def msg(platform, author, author_id, text="", kind="chat", amount=""):
    return dict(platform=platform, author=author, author_id=str(author_id or author), text=text, kind=kind, amount=amount, first=False)


# ---- parsing ----
def parse_irc(line):
    """':tags' IRC line -> (tags, command, params). Handles @tag;tag=v :prefix CMD #chan :text."""
    tags = {}
    if line.startswith("@"):
        raw, line = line[1:].split(" ", 1)
        for kv in raw.split(";"):
            k, _, v = kv.partition("=")
            tags[k] = re.sub(r"\\(.)", lambda m: {"s": " ", ":": ";", "n": "\n", "r": "\r"}.get(m[1], m[1]), v)
    prefix = ""
    if line.startswith(":"):
        prefix, line = line[1:].split(" ", 1)
    head, _, text = line.partition(" :")
    parts = head.split()
    return tags, (parts[0] if parts else ""), (parts[1:], text, prefix)


def from_twitch(line):
    """One IRC line -> normalized message or None."""
    tags, cmd, (params, text, prefix) = parse_irc(line)
    name = tags.get("display-name") or prefix.split("!")[0]
    uid = tags.get("user-id") or name
    if cmd == "PRIVMSG":
        bits = tags.get("bits", "")
        return msg("twitch", name, uid, text, "bits" if bits else "chat", f"{bits} bits" if bits else "")
    if cmd == "USERNOTICE":
        mid = tags.get("msg-id", "")
        name = tags.get("display-name") or tags.get("login", "someone")
        if mid == "sub":
            return msg("twitch", name, uid, text, "sub", TIERS.get(tags.get("msg-param-sub-plan"), "Tier 1"))
        if mid == "resub":
            return msg("twitch", name, uid, text, "resub", f"{tags.get('msg-param-cumulative-months', '?')} months")
        if mid in ("subgift", "anonsubgift"):
            who = "Anonymous" if mid == "anonsubgift" else name
            return msg("twitch", who, uid if mid == "subgift" else "anon", f"gifted a sub to {tags.get('msg-param-recipient-display-name', 'a viewer')}", "giftsub", "1 gift sub")
        if mid == "raid":
            n = tags.get("msg-param-viewerCount", "?")
            return dict(msg("twitch", tags.get("msg-param-displayName") or name, uid, "", "raid", f"{n} viewers"),
                        count=n, login=tags.get("msg-param-login") or tags.get("login", ""))
    return None


API_KINDS = {"textMessageEvent": "chat", "superChatEvent": "superchat", "superStickerEvent": "supersticker",
             "newSponsorEvent": "membership", "memberMilestoneChatEvent": "membership", "membershipGiftingEvent": "membership"}


def from_api(item):
    """YouTube Data API liveChatMessage -> normalized message or None."""
    sn, a = item.get("snippet", {}), item.get("authorDetails", {})
    kind = API_KINDS.get(sn.get("type"))
    if not kind:
        return None
    det = sn.get("superChatDetails") or sn.get("superStickerDetails") or {}
    text = sn.get("displayMessage") or ("became a member" if kind == "membership" else "")
    return msg("youtube", a.get("displayName", "?"), a.get("channelId"), text, kind, det.get("amountDisplayString", ""))


def youtube_api(hub, stop):
    """Read live chat through the official API (needs the YouTube login). 5 quota units per poll, so poll every
    YT_POLL_SECONDS (default 15 s, ~1200 units/hour) or slower if YouTube asks. Backlog at start is skipped."""
    def once():
        api = YouTubeChat()._api()
        items = api.liveBroadcasts().list(part="snippet", broadcastStatus="active", broadcastType="all").execute().get("items", [])
        chat_id = items[0]["snippet"].get("liveChatId") if items else None
        if not chat_id:
            raise RuntimeError("no active broadcast")
        token, first = None, True
        while not stop.is_set():
            r = api.liveChatMessages().list(liveChatId=chat_id, part="snippet,authorDetails", pageToken=token).execute()
            token = r.get("nextPageToken")
            if not first:
                for it in r.get("items", []):
                    if m := from_api(it):
                        hub.process(m)
            first = False
            stop.wait(max(c.f("YT_POLL_SECONDS"), r.get("pollingIntervalMillis", 0) / 1000))
    backoff_loop("youtube", once, stop)


def from_ytdl(m):
    """chat-downloader message dict -> normalized message or None (ignored types)."""
    kind = YT_KINDS.get(m.get("message_type"))
    if not kind:
        return None
    a = m.get("author") or {}
    money = m.get("money") or {}
    amount = money.get("text") or (f"{money['amount']} {money.get('currency', '')}".strip() if money.get("amount") else "")
    text = m.get("message") or ""
    if kind == "membership" and not text:
        text = "became a member"
    return msg("youtube", a.get("name", "?"), a.get("id"), text, kind, amount)


# ---- templates / escaping ----
def esc(text):
    """Escape Discord markdown so usernames can't format or ping."""
    return re.sub(r"([\\*_~`|>#\[\]()@:])", r"\\\1", text)


def render(kind, m):
    t = c.s("WELCOME_TEMPLATE" if kind == "welcome" else "THANKS_TEMPLATE")
    discord = c.s("LINK_DISCORD").strip()
    out = t.format_map({"user": m["author"], "amount": m["amount"] or "support", "platform": m["platform"].title(), "discord": discord})
    if discord and "{discord}" not in t and kind == "welcome":
        out += f" Discord: {discord}"
    return re.sub(r"[\r\n]+", " ", out)[:400]


def raid_text(m):
    return f"Thanks for the raid {m['author']} with {m.get('count', '?')} viewers! Go follow them: twitch.tv/{m.get('login') or m['author'].lower()}"


def rotation():
    """Timer messages for chat: tip, discord, then the rest of the links."""
    L = dict(links.items())
    out = [f.format(L[n]) for n, f in (("Tip", "Support the stream: {}"), ("Discord", "Join the Discord: {}")) if n in L]
    rest = links.short(("Tip", "Discord"))
    return out + ([f"Follow along: {rest}"] if rest else [])


def ticker():
    """Overlay ticker lines: 'Tip: streamelements.com/...' for every link that is set."""
    return [f"{n}: {u.split('://', 1)[-1].removeprefix('www.')}" for n, u in links.items(("Website", "YouTube Shorts"))]


def discord_payload(ev):
    m = ev["msg"]
    title = {"raid": "Raid"}.get(m["kind"], m["kind"].title()) if ev["type"] != "welcome" else "New chatter"
    desc = f"**{esc(m['author'])}** on {m['platform'].title()}" + (f": {m['amount']}" if m["amount"] else "")
    if m["text"]:
        desc += "\n> " + esc(m["text"])[:500].replace("\n", " ")
    return {"embeds": [{"title": title, "description": desc[:2000], "color": 0xFFB300 if ev["type"] != "welcome" else 0x5865F2}],
            "allowed_mentions": {"parse": []}}


# ---- core ----
class Hub:
    """Dedupe first-timers, throttle welcomes, fan events out to overlay / Discord / Twitch."""

    def __init__(self, twitch_say=None, post=None, yt_say=None):
        self.events, self.seen, self.lock = deque(maxlen=200), set(), threading.Lock()
        self.ids, self.welcomes = itertools.count(1), deque()
        self.twitch_say, self.post, self.yt_say = twitch_say, post, yt_say
        self.last_say = 0.0
        self.chat, self.cids, self.cd = deque(maxlen=50), itertools.count(1), {}  # layout chat lines, ids, command cooldowns
        self.t0, self.chat_count, self.rot, self.timer = time.time(), 0, 0, {"n": 0, "text": ""}
        self.clip_path = c.STATE / "clip_requests.jsonl"

    def process(self, m):
        key = (m["platform"], m["author_id"])
        with self.lock:
            m["first"] = key not in self.seen and m["kind"] != "raid"
            self.seen.add(key)
            self.chat.append(dict(m, id=next(self.cids)))
        if m["kind"] == "chat":
            self.chat_count += 1
            if m["text"].startswith("!"):
                self.command(m)
        if m["kind"] in PAID:
            self.emit("raid" if m["kind"] == "raid" else "paid", m)
        elif m["first"]:
            if len(self.welcomes) < 20:  # drop beyond 20 so raids can't flood
                self.welcomes.append(m)

    def emit(self, typ, m):
        ev = {"id": next(self.ids), "type": typ, "msg": m, "t": time.time()}
        with self.lock:
            self.events.append(ev)
        if typ != "welcome" or c.b("DISCORD_WELCOMES"):
            if self.post:
                threading.Thread(target=self.post, args=(ev,), daemon=True).start()
        if m["platform"] == "twitch" and self.twitch_say and (typ != "welcome" or m["first"]):
            if typ == "raid":
                self.twitch_say(raid_text(m))
            else:
                self.say(render("welcome" if typ == "welcome" else "thanks", m))
        elif m["platform"] == "youtube" and self.yt_say and typ == "paid":  # YouTube: thanks only (API quota)
            self.yt_say(render("thanks", m))

    def bot(self, text, platform):
        """Bot answer: always a BOT line in /layout chat, plus Twitch chat when the command came from Twitch and we can speak."""
        text = re.sub(r"[\r\n]+", " ", text)[:400]
        with self.lock:
            self.chat.append(dict(msg("bot", "BOT", "bot", text, "bot"), id=next(self.cids)))
        if platform == "twitch" and self.twitch_say:
            self.twitch_say(text)
        elif platform == "youtube" and self.yt_say:
            self.yt_say(text)

    def command(self, m):
        name, now = m["text"].split()[0][1:].lower(), time.time()
        L = dict(links.items())
        if name == "clip":  # global 1/60 s
            if now - self.cd.get("clip", -1e9) < 60:
                return
            self.cd["clip"] = now
            self.clip_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.clip_path, "a") as fh:
                fh.write(json.dumps({"t": int(now), "user": m["author"], "platform": m["platform"]}) + "\n")
            return self.bot("Clip marked!", m["platform"])
        up = int(now - self.t0) // 60
        reply = {"discord": f"Join the Discord: {L.get('Discord')}" if "Discord" in L else "",
                 "tip": f"Support the stream: {L.get('Tip')}" if "Tip" in L else "",
                 "socials": links.short(),
                 "schedule": f"Schedule: {c.s('STREAM_SCHEDULE').strip()}" if c.s("STREAM_SCHEDULE").strip() else "",
                 "uptime": f"Live for {up // 60}h {up % 60}m" if up >= 60 else f"Live for {up}m",
                 "lurk": f"{m['author']} is lurking, enjoy!",
                 "commands": "Commands: !discord !tip !socials !schedule !uptime !lurk !clip"}.get(name)
        key = (name, m["author"] if name == "lurk" else "")  # lurk cooldown per user, the rest per command
        if reply and now - self.cd.get(key, -1e9) >= c.f("CHATBOT_COOLDOWN"):
            self.cd[key] = now
            self.bot(reply, m["platform"])

    def timer_tick(self):
        """Post the next rotating message if enough chat happened since the last one."""
        lines = rotation()
        if not lines or self.chat_count < c.i("TIMER_MIN_CHAT"):
            return None
        text, self.rot, self.chat_count = lines[self.rot % len(lines)], self.rot + 1, 0
        self.timer = {"n": self.timer["n"] + 1, "text": text}
        if self.twitch_say:
            self.twitch_say(text)
        return text

    def timer_loop(self, stop):
        while not stop.wait(c.f("TIMER_MINUTES") * 60):
            self.timer_tick()

    def chat_since(self, n):
        with self.lock:
            return {"last": self.chat[-1]["id"] if self.chat else n, "lines": [x for x in self.chat if x["id"] > n], "timer": self.timer}

    def say(self, text):
        now = time.time()
        if now - self.last_say >= c.f("CHATBOT_COOLDOWN"):
            self.last_say = now
            self.twitch_say(text)

    def welcome_loop(self, stop):
        while not stop.wait(3):  # max 1 welcome per 3 s
            if self.welcomes:
                self.emit("welcome", self.welcomes.popleft())

    def since(self, n):
        with self.lock:
            return {"last": self.events[-1]["id"] if self.events else n, "events": [e for e in self.events if e["id"] > n]}


# ---- outputs ----
def discord_post(ev, url=None):
    url = url or c.s("DISCORD_WEBHOOK_URL").strip()
    if not url:
        return
    body = json.dumps(discord_payload(ev)).encode()
    for _ in range(3):
        req = urllib.request.Request(url, body, {"Content-Type": "application/json", "User-Agent": "gta-stream-kit"})
        try:
            urllib.request.urlopen(req, timeout=10).read()
            return
        except urllib.error.HTTPError as e:
            if e.code != 429:
                return log.warning("discord webhook failed: HTTP %s", e.code)
            try:
                wait = float(json.loads(e.read()).get("retry_after", 1))
            except (ValueError, OSError):
                wait = 1.0
            time.sleep(min(wait, 30))
        except OSError as e:
            return log.warning("discord webhook failed: %s", c.mask(str(e)).replace(url, "***"))


OVERLAY = """<!doctype html><meta charset=utf-8><title>chat overlay</title><style>
html,body{margin:0;background:transparent;overflow:hidden;font-family:-apple-system,"Helvetica Neue",Arial,sans-serif;font-weight:800}
#s{position:fixed;left:32px;bottom:32px;width:560px;display:flex;flex-direction:column-reverse;gap:12px}
.t{color:#fff;border-radius:14px;padding:10px 16px;background:rgba(18,18,28,.82);border-left:6px solid #5865f2;
 box-shadow:0 6px 20px rgba(0,0,0,.4);animation:in .45s cubic-bezier(.2,1.3,.4,1) both;font-size:22px;transition:opacity .5s,transform .5s}
.t.paid,.t.raid{padding:18px 24px;font-size:30px;border-left-width:10px;border-image:linear-gradient(#ffb300,#ff3d71) 1;background:rgba(30,16,24,.9)}
.t.out{opacity:0;transform:translateX(-40px)}
.a{color:#ffd54a;font-size:1.5em;display:block;line-height:1.1}.p{opacity:.65;font-size:.65em;text-transform:uppercase;letter-spacing:.08em}
.x{font-weight:600;font-size:.7em;opacity:.9;display:block;word-break:break-word}
@keyframes in{from{opacity:0;transform:translateX(-60px) scale(.9)}to{opacity:1;transform:none}}</style><div id=s></div><script>
const S=document.getElementById('s');let last=0;
const LBL={superchat:'Super Chat',supersticker:'Super Sticker',membership:'New member',bits:'Bits',sub:'Subscribed',resub:'Resubscribed',giftsub:'Gift sub',raid:'Raid'};
function el(c,t,x){const e=document.createElement('span');e.className=c;e.textContent=t;return e}
function show(ev){const m=ev.msg,d=document.createElement('div');d.className='t '+ev.type;
 if(ev.type=='welcome'){d.append(el('p',m.platform),document.createTextNode(' Welcome, '+m.author+'!'))}
 else{d.append(el('p',m.platform+' - '+(LBL[m.kind]||m.kind)),document.createElement('br'),document.createTextNode(m.author+(m.kind=='raid'?' is raiding!':'')));
  if(m.amount)d.append(el('a',m.amount));if(m.text)d.append(el('x',m.text))}
 S.prepend(d);setTimeout(()=>d.classList.add('out'),ev.type=='welcome'?5000:9000);setTimeout(()=>d.remove(),ev.type=='welcome'?5600:9600);
 while(S.children.length>6)S.lastChild.remove()}
async function poll(){try{const r=await (await fetch('/events?since='+last)).json();
 if(last==0&&r.events.length>3)r.events=r.events.slice(-3);last=r.last;r.events.forEach(show)}catch(e){}setTimeout(poll,1000)}poll();
</script>"""


OV = Path(__file__).parent / "overlays"
SCENES = {"/layout": ("layout.html", ""), "/starting": ("scene.html", "STARTING SOON"), "/brb": ("scene.html", "BE RIGHT BACK"),
          "/ending": ("scene.html", "THANKS FOR WATCHING")}
STATIC = {"/common.css": OV / "common.css", "/common.js": OV / "common.js", "/avatar.png": c.STATE / "avatar" / "avatar.png",
          "/tip_qr.png": c.STATE / "overlay" / "tip_qr.png",
          # Pricedown: Typodermic desktop EULA, served to the local OBS only, never committed or redistributed
          "/pricedown.otf": Path(__file__).parent / "third_party" / "pricedown" / "Pricedown Bl.otf",
          **{f"/scene_{n}.png": c.STATE / "overlay" / f"scene_{n}.png" for n in ("starting", "brb", "ending")}}


def make_qr():
    """state/overlay/tip_qr.png for LINK_TIP (needs segno; skipped without it)."""
    tip, out = c.s("LINK_TIP").strip(), STATIC["/tip_qr.png"]
    try:
        import segno
        if tip:
            out.parent.mkdir(parents=True, exist_ok=True)
            segno.make(tip, error="m").save(str(out), scale=8, border=1)
    except ImportError:
        log.warning("segno missing: no tip QR (pip install segno)")


def page(path, mode):
    """Overlay HTML with the page title and a JSON blob of links/ticker injected server-side."""
    handle = re.search(r"@[\w.]+", c.s("LINK_YOUTUBE"))
    data = {"mode": path[1:], "ticker": ticker(), "handle": handle[0] if handle else "@yourchannel", "schedule": c.s("STREAM_SCHEDULE").strip(), "kicker": c.s("SCENE_KICKER").strip(),
            "qr": STATIC["/tip_qr.png"].exists() and bool(c.s("LINK_TIP").strip()),
            "links": [{"n": n, "u": u.split("://", 1)[-1].removeprefix("www.")} for n, u in links.items(("Website", "YouTube Shorts"))]}
    blob = json.dumps(data).replace("</", "<\\/")
    name = c.s("STREAMER_NAME").strip() or data["handle"].lstrip("@")
    return ((OV / mode[0]).read_text().replace("__TITLE__", mode[1]).replace("__NAME__", html.escape(name.upper()))
            .replace("__GAME__", html.escape(c.game_short().upper())).replace("__DATA__", blob).encode())


def serve(hub, port):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query).get("since", ["0"])[0]
            n = int(q) if q.isdigit() else 0
            if u.path == "/overlay":
                body, ctype = OVERLAY.encode(), "text/html; charset=utf-8"
            elif u.path == "/events":
                body, ctype = json.dumps(hub.since(n)).encode(), "application/json"
            elif u.path == "/chat":
                body, ctype = json.dumps(hub.chat_since(n)).encode(), "application/json"
            elif u.path in SCENES:
                body, ctype = page(u.path, SCENES[u.path]), "text/html; charset=utf-8"
            elif u.path in STATIC and STATIC[u.path].exists():
                body, ctype = STATIC[u.path].read_bytes(), mimetypes.guess_type(u.path)[0] or "application/octet-stream"
            else:
                return self.send_error(404)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---- sources ----
class Twitch:
    """Reads chat over IRC (anonymous unless bot creds set); say() posts as the bot."""

    def __init__(self, channel):
        self.channel, self.sock = channel.lower().lstrip("#"), None
        self.nick, self.oauth = c.s("TWITCH_BOT_NICK").strip().lower(), c.s("TWITCH_BOT_OAUTH").strip()
        self.can_say = bool(self.nick and self.oauth)

    def say(self, text):
        try:
            if self.sock and self.can_say:
                self.sock.sendall(f"PRIVMSG #{self.channel} :{text}\r\n".encode())
        except OSError as e:
            log.warning("twitch send failed: %s", c.mask(str(e)))

    def run(self, hub, stop):
        def once():
            pw = self.oauth if self.oauth.startswith("oauth:") else f"oauth:{self.oauth}"
            nick, pw = (self.nick, pw) if self.can_say else (f"justinfan{random.randint(10000, 99999)}", "SCHMOOPIIE")
            s = socket.create_connection(("irc.chat.twitch.tv", 6667), 10)
            s.settimeout(30)
            s.sendall(f"CAP REQ :twitch.tv/tags twitch.tv/commands\r\nPASS {pw}\r\nNICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
            self.sock, buf = s, b""
            while not stop.is_set():
                try:
                    data = s.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    return
                buf += data
                *lines, buf = buf.split(b"\r\n")
                for ln in lines:
                    ln = ln.decode("utf-8", "replace")
                    if ln.startswith("PING"):
                        s.sendall(b"PONG :tmi.twitch.tv\r\n")
                    elif "NOTICE * :Login authentication failed" in ln:
                        log.error("twitch bot login failed: check TWITCH_BOT_OAUTH")
                        self.can_say = False
                    elif (m := from_twitch(ln)) and m["author"].lower() != self.nick:
                        hub.process(m)
        backoff_loop("twitch", once, stop)


class YouTubeChat:
    """Posts to the active broadcast's live chat as the channel (Data API, youtube.force-ssl). 50 quota units per
    message of 10k/day, so only command replies and paid thanks go here, never welcomes or timers."""

    @staticmethod
    def available():
        from .uploaders import youtube as yt
        return yt.TOKEN.exists() and "youtube.force-ssl" in yt.TOKEN.read_text()

    def __init__(self):
        self.chat_id, self.lock = None, threading.Lock()

    def _api(self):
        from googleapiclient.discovery import build
        from .uploaders import youtube as yt
        return build("youtube", "v3", credentials=yt.credentials(), cache_discovery=False)

    def _post(self, text):
        try:
            with self.lock:
                api = self._api()
                if not self.chat_id:
                    items = api.liveBroadcasts().list(part="snippet", broadcastStatus="active", broadcastType="all").execute().get("items", [])
                    self.chat_id = items[0]["snippet"].get("liveChatId") if items else None
                if not self.chat_id:
                    return log.warning("youtube reply skipped: no active broadcast")
                api.liveChatMessages().insert(part="snippet", body={"snippet": {
                    "liveChatId": self.chat_id, "type": "textMessageEvent", "textMessageDetails": {"messageText": text[:200]}}}).execute()
        except Exception as e:  # noqa: BLE001
            self.chat_id = None  # broadcast may have changed; look it up again next time
            log.warning("youtube reply failed: %s", c.mask(str(e))[:200])

    def say(self, text):
        threading.Thread(target=self._post, args=(text,), daemon=True).start()


def youtube(url, hub, stop):
    def once():
        from chat_downloader import ChatDownloader
        for raw in ChatDownloader().get_chat(url):
            if stop.is_set():
                return
            if m := from_ytdl(raw):
                hub.process(m)
    backoff_loop("youtube", once, stop)


def backoff_loop(name, fn, stop):
    """Run fn forever; on failure or clean end wait 5 s doubling to 120 s. One source never kills another."""
    wait = 5
    while not stop.is_set():
        t0 = time.time()
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log.warning("%s chat dropped: %s", name, c.mask(str(e))[:200])
        wait = 5 if time.time() - t0 > 120 else min(wait * 2, 120)
        stop.wait(wait)


def demo(hub, stop):
    seq = [msg("twitch", "NewViewer", 1, "hi!"), msg("youtube", "Ana", "u1", "hello from Brazil"),
           msg("youtube", "BigFan", "u2", "Love the stream! Keep going", "superchat", "$20.00"),
           msg("twitch", "Bitty", 2, "cheer100 nice", "bits", "100 bits"), msg("twitch", "Subby", 3, "", "sub", "Tier 1"),
           msg("twitch", "Loyal", 4, "year two!", "resub", "24 months"), msg("youtube", "Sticker", "u3", "", "supersticker", "$5.00"),
           msg("twitch", "FriendlyStreamer", 5, "", "raid", "42 viewers"),
           msg("twitch", "Lurky", 6, "!lurk"), msg("youtube", "Ana", "u1", "!tip"), msg("twitch", "Clipper", 7, "!clip"), msg("youtube", "Mem", "u4", "", "membership", "")]
    while not stop.is_set():
        for m in seq:
            if stop.wait(4):
                return
            hub.process(dict(m, author_id=f"{m['author_id']}-{int(time.time())}"))  # unique id so every lap welcomes again


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true", help="fake events, no network")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    stop, tw = threading.Event(), None
    channel = c.s("TWITCH_CHANNEL").strip()
    if channel and not a.demo:
        tw = Twitch(channel)
    post = discord_post if c.s("DISCORD_WEBHOOK_URL").strip() and not a.demo else None  # demo never posts fake events to Discord
    yt_chat = YouTubeChat() if not a.demo and c.b("YOUTUBE_CHAT_REPLIES") and YouTubeChat.available() else None
    hub = Hub(tw.say if tw and tw.can_say else None, post, yt_chat.say if yt_chat else None)
    port = c.i("CHATBOT_PORT")
    make_qr()
    serve(hub, port)
    threading.Thread(target=hub.welcome_loop, args=(stop,), daemon=True).start()
    if c.f("TIMER_MINUTES") > 0:
        threading.Thread(target=hub.timer_loop, args=(stop,), daemon=True).start()
    print(f"OBS Browser Sources (1920x1080): http://127.0.0.1:{port}/overlay (alerts), /layout (frame), /starting?min=5, /brb, /ending")
    if a.demo:
        threading.Thread(target=demo, args=(hub, stop), daemon=True).start()
    else:
        yt = c.s("YOUTUBE_LIVE_URL").strip() or (c.s("LINK_YOUTUBE").rstrip("/") + "/live" if c.s("LINK_YOUTUBE").strip() else "")
        if yt_chat:  # official API when logged in; chat-downloader scraping breaks whenever YouTube changes
            threading.Thread(target=youtube_api, args=(hub, stop), daemon=True).start()
        elif yt:
            threading.Thread(target=youtube, args=(yt, hub, stop), daemon=True).start()
        if tw:
            threading.Thread(target=tw.run, args=(hub, stop), daemon=True).start()
        if not (yt or tw):
            print("no source: set TWITCH_CHANNEL and/or YOUTUBE_LIVE_URL / LINK_YOUTUBE (or use --demo)")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
