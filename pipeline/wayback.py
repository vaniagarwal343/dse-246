"""Wayback Machine snapshots for evidence URLs (PROTOCOL.md §4).

With WAYBACK_ACCESS_KEY and WAYBACK_SECRET_KEY blank this is anonymous Save Page Now. A save that returns no snapshot
URL is logged to pipeline/wayback_failures.jsonl and yields None.
"""
import json
import re

import httpx

from pipeline import config, env, net

SAVE_URL = "https://web.archive.org/save/"
SNAPSHOT_RE = re.compile(r"^/web/\d{14}")
FAILURES = config.ROOT / "pipeline" / "wayback_failures.jsonl"


def snapshot_of(resp):
    """The snapshot URL a Save Page Now response points to, or None."""
    location = resp.headers.get("content-location", "")
    if SNAPSHOT_RE.match(location):
        return "https://web.archive.org" + location
    if resp.url.host == "web.archive.org" and SNAPSHOT_RE.match(resp.url.path):
        return str(resp.url)
    return None


def submit(client, url, purpose):
    """Ask Save Page Now to capture url. Returns the snapshot URL, or None after logging the failure."""
    headers = {}
    if env.get("WAYBACK_ACCESS_KEY") and env.get("WAYBACK_SECRET_KEY"):
        headers["Authorization"] = f"LOW {env.get('WAYBACK_ACCESS_KEY')}:{env.get('WAYBACK_SECRET_KEY')}"
    try:
        resp = client.request("GET", SAVE_URL + url, purpose=purpose, headers=headers, timeout=180)
    except httpx.HTTPError as exc:
        status, reason = None, type(exc).__name__
    else:
        snapshot = snapshot_of(resp)
        if snapshot:
            return snapshot
        status, reason = resp.status_code, "no snapshot URL in the response"
    FAILURES.parent.mkdir(parents=True, exist_ok=True)
    with FAILURES.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"created_at": net.utc_now(), "config_hash": client.config_hash, "url": url, "purpose": purpose, "status": status, "reason": reason}) + "\n")
    return None
