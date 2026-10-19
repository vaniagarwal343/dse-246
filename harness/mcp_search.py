"""The study arms' tools inside the Codex CLI: a stdio MCP server exposing `search` and `fetch` over one backend (CONFIG.md §3).

    python harness/mcp_search.py <arm> <run_dir> <purpose> <prompt_chars>

Every arm gets the same two tools with the same names, descriptions and schemas (harness/arms.py). Each backend call goes
through harness/net.py, so it is logged to results/requests.log, and each tool call adds one line to <run_dir>/tool_calls.jsonl.
After CALL_CAP calls, or once the estimated next model input reaches INPUT_CEILING tokens, every further call is refused with
the forced-answer message, the CLI's nearest equivalent of tool_choice none.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import arms  # noqa: E402
from harness import net as harness_net  # noqa: E402
from pipeline import config, env, net  # noqa: E402

PROTOCOL_VERSION = "2025-06-18"
FORCED = "No more tool calls: {why}. Give your final answer now, in the format the question asks for."
BLOCKED = "BLOCKED_KEY_DOMAIN"
UNAVAILABLE = "This URL is not available."
# NOTE(vani): the CLI reports token usage once per run, not per request, so the server estimates the next model input: the CLI's
# own instructions and tool definitions (BASE_TOKENS: a request with no tool call carried 9,289 to 9,292 input tokens on both
# arms under amendment 2026-10-19-04's config), a token per three characters of the prompt and of every tool argument and result so far, and CALL_ALLOWANCE
# per call for the model's own reasoning and messages.
BASE_TOKENS = 10_000
CALL_ALLOWANCE = 1000
TOOLS = [{"name": t["name"], "description": t["description"], "inputSchema": t["parameters"]} for t in (arms.SEARCH_TOOL, arms.FETCH_TOOL)]


class Blocked(Exception):
    """A URL on harness/arms.py's BLOCKLIST; the message is the rule."""


def drop_blocked(payload, meta):
    """Search results without blocklisted URLs, their backend lengths kept aligned, and one BLOCKED_KEY_DOMAIN row per dropped result."""
    backend = meta.get("backend_chars") or [None] * len(payload)
    rules = [arms.blocked(r["url"]) for r in payload]
    dropped = [{"code": BLOCKED, "rule": rule, "url": r["url"], "title": r["title"]} for r, rule in zip(payload, rules) if rule]
    meta = {**meta, "backend_chars": [b for b, rule in zip(backend, rules) if not rule], "blocked": dropped}
    return [r for r, rule in zip(payload, rules) if not rule], meta


def trace(name, args, payload, meta, error):
    """The trace fields of one call: every search result as returned with its lengths, or the fetch's status, lengths and truncation."""
    extra = {k: meta[k] for k in ("cost_dollars", "request_id", "query_sent", "blocked") if meta.get(k)}
    if name == "search":
        rows = payload or []
        backend = meta.get("backend_chars") or [None] * len(rows)
        return {**extra, "results": [{"url": r["url"], "title": r["title"], "text": r["text"], "returned_chars": len(r["text"]), "backend_chars": b}
                                     for r, b in zip(rows, backend)]}
    page = payload or {}
    status = meta.get("status") or (BLOCKED if (error or "").startswith(BLOCKED) else "error" if error else None)
    return {**extra, "fetch": {"url": page.get("url") or args.get("url"), "title": page.get("title"), "status": status, "source": meta.get("source"),
                               "error": meta.get("error") or error, "returned_chars": len(page.get("text") or ""),
                               "backend_chars": meta.get("backend_chars"), "truncated": meta.get("truncated")}}


class Server:
    def __init__(self, arm, run_dir, purpose, prompt_chars):
        self.arm, self.backend, self.purpose = arm, arms.ARMS[arm], purpose
        self.log_path = Path(run_dir) / "tool_calls.jsonl"
        self.client = harness_net.client(timeout=120.0)
        self.model = config.params(config.read())["MODEL_UNDER_TEST"]
        self.calls, self.chars = 0, prompt_chars

    def estimate(self):
        return BASE_TOKENS + self.chars // 3 + CALL_ALLOWANCE * self.calls

    def log(self, rec):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def call_tool(self, name, args):
        rec = {"created_at": net.utc_now(), "config_hash": self.client.config_hash, "stage": "arm", "model": self.model,
               "arm": self.arm, "tool": name, "arguments": args, "estimated_input_tokens": self.estimate()}
        if self.calls >= arms.CALL_CAP:
            why = f"the limit of {arms.CALL_CAP} tool calls is reached"
        elif self.estimate() >= arms.INPUT_CEILING:
            why = f"the input is at the {arms.INPUT_CEILING:,}-token ceiling"
        else:
            why = None
        if why:
            self.log({**rec, "ok": False, "refused": why})
            return {"content": [{"type": "text", "text": FORCED.format(why=why)}], "isError": True}
        self.calls += 1
        started = time.monotonic()
        try:
            if name not in ("search", "fetch") or self.backend is None:
                raise ValueError(f"unknown tool {name}")
            if name == "fetch" and arms.blocked(args.get("url")):
                raise Blocked(arms.blocked(args.get("url")))
            payload, meta = self.backend[name](self.client, args, f"{self.purpose}:{name}")
            if name == "search":
                payload, meta = drop_blocked(payload, meta)
            elif arms.blocked(payload.get("url")):
                raise Blocked(arms.blocked(payload.get("url")))
            text, ok, error = json.dumps(payload, ensure_ascii=False), True, None
        except Blocked as exc:  # NOTE(vani): the model is told only that the page is unavailable; the trace keeps the rule.
            text, ok, error, payload, meta = json.dumps({"error": UNAVAILABLE}), False, f"{BLOCKED}: {exc}", None, {}
        except Exception as exc:  # NOTE(vani): a failed backend call goes back to the model as that tool's output, and is logged.
            error = env.scrub(f"{type(exc).__name__}: {exc}")[:300]
            text, ok, payload, meta = json.dumps({"error": error}), False, None, {}
        self.chars += len(json.dumps(args, ensure_ascii=False)) + len(text)
        self.log({**rec, "n": self.calls, "ok": ok, "error": error, "elapsed_ms": round(1000 * (time.monotonic() - started)),
                  "returned_chars": len(text), **trace(name, args, payload, meta, error)})
        return {"content": [{"type": "text", "text": text}], "isError": not ok}

    def handle(self, msg):
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            return self.reply(mid, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
                                    "serverInfo": {"name": "search", "version": "1.0"}})
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "ping":
            return self.reply(mid, {})
        if method == "tools/list":
            return self.reply(mid, {"tools": TOOLS})
        if method == "tools/call":
            return self.reply(mid, self.call_tool(params.get("name"), params.get("arguments") or {}))
        if mid is not None:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
        return None

    @staticmethod
    def reply(mid, result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def serve(self):
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                resp = self.handle(json.loads(line))
            except json.JSONDecodeError:
                resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    arm, run_dir, purpose, prompt_chars = sys.argv[1:5]
    Server(arm, run_dir, purpose, int(prompt_chars)).serve()
