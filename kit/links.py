"""Cross-platform links from config: full block, one-line short form, and caption fitting."""
from . import config as c

PLATFORMS = (("YouTube", "LINK_YOUTUBE"), ("YouTube Shorts", "LINK_SHORTS"), ("Twitch", "LINK_TWITCH"), ("TikTok", "LINK_TIKTOK"),
             ("Instagram", "LINK_INSTAGRAM"), ("Discord", "LINK_DISCORD"), ("Tip", "LINK_TIP"), ("Website", "LINK_WEBSITE"))


def items(exclude=()):
    """[(name, url)] for every link that is set."""
    return [(n, c.s(k).strip()) for n, k in PLATFORMS if c.s(k).strip() and n not in exclude]


def block():
    """Full 'Where to find me' section, '' when no link is set."""
    return "Where to find me\n" + "\n".join(f"{n}: {u}" for n, u in items()) if items() else ""


def short(exclude=(), compact=False):
    """One line: 'YouTube: url | Twitch: url | ...', '' when no link is set.
    compact=True drops scheme and www (youtube.com/@x) for tight limits like the Twitch bio."""
    trim = (lambda u: u.split("://", 1)[-1].removeprefix("www.")) if compact else (lambda u: u)  # noqa: E731
    return " | ".join(f"{trim(u)}" if compact else f"{n}: {u}"
                      for n, u in items(("Website", "YouTube Shorts", *exclude)))  # website and Shorts only in the full block


def fit(text, limit, footer=None, sep="\n\n"):
    """text + sep + footer within `limit` chars: the text is cut first, the footer never."""
    footer = short() if footer is None else footer
    text = text.strip()
    if not footer:
        return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
    room = limit - len(footer) - len(sep)
    if room < 1:  # footer alone is too long: drop whole links from the end, never cut one
        parts = footer.split(" | ")
        while parts and len(" | ".join(parts)) > limit:
            parts.pop()
        return " | ".join(parts)
    if len(text) > room:
        text = text[:room - 1].rstrip() + "…"
    return f"{text}{sep}{footer}"
