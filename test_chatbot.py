"""Offline test of kit/chatbot.py (no network except a loopback overlay server): python test_chatbot.py"""
import json
import os
import urllib.request

os.environ.update(LINK_DISCORD="https://discord.gg/abc", WELCOME_TEMPLATE="", THANKS_TEMPLATE="", CHATBOT_COOLDOWN="0", STREAMER_NAME="Test Streamer", STREAM_GAME_SHORT="Some Game")
from kit import chatbot as cb, config as c  # noqa: E402

T = "@badge-info=;bits=100;display-name=Bit\\sGuy;user-id=11 :bitguy!bitguy@bitguy.tmi.twitch.tv PRIVMSG #chan :cheer100 hi there"
m = cb.from_twitch(T)
assert (m["kind"], m["amount"], m["author"], m["author_id"], m["text"]) == ("bits", "100 bits", "Bit Guy", "11", "cheer100 hi there"), m
m = cb.from_twitch("@display-name=Al;user-id=1 :al!al@al PRIVMSG #chan :hello")
assert m["kind"] == "chat" and m["amount"] == "" and m["platform"] == "twitch"
un = "@display-name=Sub;user-id=2;msg-id=%s;msg-param-sub-plan=2000;msg-param-cumulative-months=14;msg-param-recipient-display-name=Pal;" \
     "msg-param-viewerCount=42;msg-param-displayName=Raider :tmi.twitch.tv USERNOTICE #chan :msg"
assert cb.from_twitch(un % "sub")["kind"] == "sub" and cb.from_twitch(un % "sub")["amount"] == "Tier 2"
assert cb.from_twitch(un % "resub")["amount"] == "14 months"
g = cb.from_twitch(un % "subgift")
assert g["kind"] == "giftsub" and "Pal" in g["text"]
r = cb.from_twitch(un % "raid")
assert r["kind"] == "raid" and r["amount"] == "42 viewers" and r["author"] == "Raider"
assert cb.from_twitch(un % "ritual") is None and cb.from_twitch(":tmi.twitch.tv 001 x :Welcome") is None

# chat-downloader dicts
sc = cb.from_ytdl({"message_type": "paid_message", "message": "gg", "author": {"name": "Ann", "id": "UC1"}, "money": {"amount": 5, "currency": "USD", "text": "$5.00"}})
assert (sc["kind"], sc["amount"], sc["author_id"], sc["platform"]) == ("superchat", "$5.00", "UC1", "youtube")
assert cb.from_ytdl({"message_type": "paid_sticker", "author": {"name": "B", "id": "2"}, "money": {"amount": 2, "currency": "EUR"}})["amount"] == "2 EUR"
mb = cb.from_ytdl({"message_type": "membership_item", "author": {"name": "C", "id": "3"}})
assert mb["kind"] == "membership" and mb["text"]
assert cb.from_ytdl({"message_type": "text_message", "message": "hi", "author": {"name": "D", "id": "4"}})["kind"] == "chat"
assert cb.from_ytdl({"message_type": "ticker_paid_message_item", "author": {}}) is None

# first-time detection, welcome queue cap, paid events
said = []
hub = cb.Hub(twitch_say=said.append)
hub.process(cb.msg("twitch", "Al", 1, "hi"))
hub.process(cb.msg("twitch", "Al", 1, "again"))
hub.process(cb.msg("youtube", "Al", 1, "other platform, same id"))
assert len(hub.welcomes) == 2
for i in range(100, 160):
    hub.process(cb.msg("twitch", f"u{i}", i))
assert len(hub.welcomes) == 20
hub.process(cb.msg("twitch", "Newbie", 999, "cheer", "bits", "50 bits"))
ev = hub.events[-1]
assert ev["type"] == "paid" and ev["msg"]["first"] and not hub.welcomes.count(ev["msg"]) and said[-1].startswith("Huge thanks Newbie for the 50 bits")
hub.process(cb.msg("twitch", "Raider", 5, "", "raid", "10 viewers"))
assert hub.events[-1]["type"] == "raid" and "Raider" in said[-1]
hub.emit("welcome", hub.welcomes.popleft())
assert "Welcome" in said[-1] and "Discord: https://discord.gg/abc" in said[-1]
assert hub.since(0)["last"] == hub.events[-1]["id"] and hub.since(hub.events[-1]["id"])["events"] == []

# webhook payload + escaping
p = cb.discord_payload({"type": "paid", "msg": cb.msg("twitch", "*_evil_* @everyone `x`", 1, "[a](http://x) @here", "bits", "100 bits")})
assert p["allowed_mentions"] == {"parse": []}
d = p["embeds"][0]["description"]
assert "\\*\\_evil\\_\\*" in d and "\\@everyone" in d and "\\[a\\]" in d and "100 bits" in d, d
assert "*_evil" not in d.replace("\\*\\_", "")

# templates
os.environ.update(WELCOME_TEMPLATE="Hi {user} on {platform}!", THANKS_TEMPLATE="{user}: {amount}")
assert cb.render("welcome", cb.msg("youtube", "Zed", 1)) == "Hi Zed on Youtube! Discord: https://discord.gg/abc"
assert cb.render("thanks", cb.msg("twitch", "Zed", 1, amount="$5")) == "Zed: $5"
os.environ.update(LINK_DISCORD="")
assert "Discord" not in cb.render("welcome", cb.msg("twitch", "Zed", 1))
os.environ.update(THANKS_TEMPLATE="x\r\n/ban {user}")
assert "\n" not in cb.render("thanks", cb.msg("twitch", "Zed", 1))

# commands, cooldown, clip, timer, raid text
import tempfile  # noqa: E402
import socket  # noqa: E402
os.environ.update(LINK_DISCORD="https://discord.gg/abc", LINK_TIP="https://streamelements.com/x/tip", STREAM_SCHEDULE="Mon 8pm", CHATBOT_COOLDOWN="30", TIMER_MIN_CHAT="3")
said = []
hub = cb.Hub(twitch_say=said.append)
hub.clip_path = cb.Path(tempfile.mkdtemp()) / "sub" / "clips.jsonl"
hub.process(cb.msg("twitch", "Al", 1, "!discord"))
hub.process(cb.msg("twitch", "Bo", 2, "!DISCORD"))  # cooldown: ignored
assert said == ["Join the Discord: https://discord.gg/abc"], said
hub.process(cb.msg("youtube", "Yu", "y1", "!tip"))  # youtube: overlay line only
assert said == ["Join the Discord: https://discord.gg/abc"] and hub.chat[-1]["kind"] == "bot" and "streamelements" in hub.chat[-1]["text"]
hub.process(cb.msg("twitch", "Cy", 3, "!lurk"))
assert said[-1] == "Cy is lurking, enjoy!"
hub.process(cb.msg("twitch", "Cy", 3, "!schedule"))
assert said[-1] == "Schedule: Mon 8pm"
hub.process(cb.msg("twitch", "Cy", 3, "!nope"))
assert hub.chat[-1]["text"] == "!nope"
hub.process(cb.msg("twitch", "Cy", 3, "!clip"))
hub.process(cb.msg("twitch", "Di", 4, "!clip"))  # rate-limited
rows = [json.loads(x) for x in hub.clip_path.read_text().splitlines()]
assert len(rows) == 1 and rows[0]["user"] == "Cy" and rows[0]["platform"] == "twitch" and said[-1] == "Clip marked!"
hub.cd["clip"] -= 61
hub.process(cb.msg("twitch", "Di", 4, "!clip"))
assert len(hub.clip_path.read_text().splitlines()) == 2
hub = cb.Hub(twitch_say=said.append)
assert hub.timer_tick() is None  # not enough chat
for i in range(3):
    hub.process(cb.msg("youtube", "V", i, "hi"))
n = len(said)
t1 = hub.timer_tick()
assert t1 and said[-1] == t1 and len(said) == n + 1 and hub.timer["n"] == 1 and hub.timer_tick() is None
r = cb.from_twitch(un % "raid" + "")
assert cb.raid_text(dict(r, login="raider1", count="42")) == "Thanks for the raid Raider with 42 viewers! Go follow them: twitch.tv/raider1"

# overlay + layout servers on a random free port
with socket.socket() as so:
    so.bind(("127.0.0.1", 0))
    port = so.getsockname()[1]
hub.process(cb.msg("twitch", "Z", 9, "", "bits", "1 bits"))
srv = cb.serve(hub, port)
try:
    for path, mark in (("/layout", "TEST STREAMER"), ("/starting", "STARTING SOON"), ("/brb", "BE RIGHT BACK"), ("/ending", "THANKS FOR WATCHING"),
                       ("/common.js", "startChat"), ("/chat?since=0", "lines")):
        rsp = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}")
        assert rsp.status == 200 and mark in rsp.read().decode(), path
    assert "streamelements.com/x/tip" in urllib.request.urlopen(f"http://127.0.0.1:{port}/layout").read().decode()
    html = urllib.request.urlopen(f"http://127.0.0.1:{port}/overlay").read().decode()
    assert "background:transparent" in html and "textContent" in html and "innerHTML" not in html
    j = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/events?since=0").read())
    assert j["events"] and j["events"][0]["msg"]["platform"]
finally:
    srv.shutdown()
print("ok")
