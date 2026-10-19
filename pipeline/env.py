"""Keys from .env, held in memory only. Never print, log or write a value; scrub() strips them from text that might be."""
import re
from functools import cache

from pipeline.config import ROOT

ENV_PATH = ROOT / ".env"
ASSIGN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*(.*?)\s*$")


@cache
def values():
    out = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            m = ASSIGN_RE.match(line)
            if m:
                value = m.group(2)
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                out[m.group(1)] = value
    return out


def get(name):
    """name's value in .env, or '' when it is blank or absent."""
    return values().get(name, "")


def scrub(text):
    """text with every .env value replaced by <redacted>: anywhere for values of 8+ characters, as a whole URL path segment for shorter ones."""
    # NOTE(vani): TheSportsDB's key is a short URL path segment; replacing short values anywhere would mangle unrelated digits.
    for value in values().values():
        if len(value) >= 8:
            text = text.replace(value, "<redacted>")
        elif value:
            text = re.sub(rf"(?<=/){re.escape(value)}(?=/|$)", "<redacted>", text)
    return text
