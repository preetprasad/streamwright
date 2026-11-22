"""Chat signal: Twitch IRC (anonymous) and optional YouTube chat. Keeps the last 15 minutes."""
import logging
import random
import re
import socket
import threading
import time
from collections import deque

from . import config as c

log = logging.getLogger("chat")
HYPE = re.compile(r"\b(LUL|KEKW|LMAO|OMG|POG|W|CLIP)\b|😂")


def weight(text):
    return 2 if HYPE.search(text) or "no way" in text.lower() else 1


class Chat:
    def __init__(self):
        self.msgs = deque()  # (unix time, text), oldest first
        self.lock = threading.Lock()

    def add(self, text, ts=None):
        now = time.time()
        with self.lock:
            self.msgs.append((ts or now, text))
            while self.msgs and self.msgs[0][0] < now - 900:
                self.msgs.popleft()

    def weighted(self, sec):
        """Hype-weighted message count in [sec, sec+1)."""
        n = 0
        with self.lock:
            for ts, text in reversed(self.msgs):
                if ts < sec:
                    break
                if ts < sec + 1:
                    n += weight(text)
        return n

    def lines(self, a, b, limit=30):
        with self.lock:
            return [t for ts, t in self.msgs if a <= ts <= b][:limit]


def twitch(chat, channel, stop):
    while not stop.is_set():
        try:
            s = socket.create_connection(("irc.chat.twitch.tv", 6667), 10)
            s.settimeout(30)
            s.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\nJOIN #{channel.lower()}\r\n".encode())
            buf = b""
            while not stop.is_set():
                try:
                    data = s.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    break
                buf += data
                *lines, buf = buf.split(b"\r\n")
                for line in lines:
                    line = line.decode("utf-8", "replace")
                    if line.startswith("PING"):
                        s.sendall(b"PONG :tmi.twitch.tv\r\n")
                    elif " PRIVMSG " in line:
                        chat.add(line.split(" PRIVMSG ", 1)[1].split(" :", 1)[-1])
        except OSError as e:
            log.warning("twitch chat dropped: %s", e)
        stop.wait(5)


def youtube(chat, url):
    try:
        from chat_downloader import ChatDownloader
        for m in ChatDownloader().get_chat(url):
            ts = m.get("timestamp")  # microseconds since epoch
            chat.add(m.get("message", ""), ts / 1e6 if ts else None)
    except Exception as e:  # chat is optional: log once, carry on without it
        log.warning("youtube chat unavailable: %s", e)


def start(chat, stop):
    if c.s("TWITCH_CHANNEL"):
        threading.Thread(target=twitch, args=(chat, c.s("TWITCH_CHANNEL"), stop), daemon=True).start()
    if c.s("YOUTUBE_LIVE_URL"):
        threading.Thread(target=youtube, args=(chat, c.s("YOUTUBE_LIVE_URL")), daemon=True).start()
