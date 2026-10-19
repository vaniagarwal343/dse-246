"""The single HTTP client for this repo (AGENTS.md, Network policy).

Every request passes a host policy from pipeline/policy.py and is logged as one JSON line: host, path, timestamp,
config_hash and purpose, plus method, model, tools and status. Keys, query strings and bodies are never logged.
`client()` logs to pipeline/requests.log under the pipeline policy; harness/net.py builds the same client under the
harness policy.
"""
import datetime as dt
import json
import threading
import time
from urllib.parse import urlsplit

import httpx

from pipeline import config, env, policy

USER_AGENT = "dse-pipeline/0.1"
RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})
# NOTE(vani): the gate sends requests from worker threads; one lock keeps their log lines whole.
LOG_LOCK = threading.Lock()


class Refused(Exception):
    """The host policy refused a request. Surface it; never route around it with another client."""


def utc_now():
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def retry_delay(resp, attempt):
    try:
        return min(float(resp.headers.get("retry-after", "")), 60.0)
    except ValueError:
        return 5.0 * (attempt + 1)


class Client:
    def __init__(self, refusal, log_path, timeout=60.0, retries=2):
        text = config.read()
        self.config_hash = config.config_hash(text)
        if config.stamped_hash(text) != self.config_hash:
            raise SystemExit("CONFIG.md's config_hash stamp is stale; run `python3 -m pipeline.config --stamp` first")
        self.model_under_test = config.params(text)["MODEL_UNDER_TEST"]
        self.refusal, self.log_path, self.retries = refusal, log_path, retries
        self.http = httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT})

    def request(self, method, url, *, purpose, model=None, body=None, **kwargs):
        """Send one request and return the httpx.Response, retrying 408/429/5xx and transport errors.

        Raises Refused, before anything is sent, when the policy says no. `body` goes out as JSON; other keyword
        arguments (params, headers, timeout) go to httpx.
        """
        if not purpose:
            raise ValueError("every request needs a purpose")
        parts = urlsplit(url)
        tools = policy.tool_ids(body)
        reason = self.refusal(parts.hostname or "", self.model_under_test, model, tools, purpose)
        if reason:
            raise Refused(f"{parts.hostname}: {reason}")
        entry = {"host": parts.hostname, "path": env.scrub(parts.path), "method": method, "purpose": purpose,
                 "config_hash": self.config_hash, "model": model, "tools": list(tools)}
        for attempt in range(self.retries + 1):
            start = time.monotonic()
            try:
                resp = self.http.request(method, url, json=body, **kwargs)
            except httpx.TransportError as exc:
                self._log(entry, error=type(exc).__name__, elapsed_ms=round(1000 * (time.monotonic() - start)))
                if attempt == self.retries:
                    raise
                time.sleep(5.0 * (attempt + 1))
                continue
            self._log(entry, status=resp.status_code, elapsed_ms=round(1000 * (time.monotonic() - start)))
            if resp.status_code not in RETRY_STATUS or attempt == self.retries:
                return resp
            time.sleep(retry_delay(resp, attempt))

    def _log(self, entry, **result):
        line = json.dumps({"timestamp": utc_now(), **entry, **result}) + "\n"
        with LOG_LOCK:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)


def client(**kwargs):
    """The pipeline client: pipeline policy, logged to pipeline/requests.log."""
    return Client(policy.pipeline_refusal, config.ROOT / "pipeline" / "requests.log", **kwargs)
