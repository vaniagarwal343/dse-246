"""The study arms' backends and tools. CONFIG.md §1 ARMS names the arms in this freeze; each runs MODEL_UNDER_TEST in the Codex CLI
(harness/codex_arm.py), whose only web path is these two tools served by harness/mcp_search.py.

The settings below are CONFIG.md §3's: reasoning effort, results and characters per search, characters per fetch, the
call cap and the input-token ceiling. Every arm exposes the same two tools with the same names and descriptions.
"""
import re

from pipeline import env

EXA_URL = "https://api.exa.ai"
DEEPSEEK_URL = "https://api.deepseek.com/v1/tools"
REASONING_EFFORT = "medium"
RESULTS = 10
SEARCH_TEXT = 400
PAGE_TEXT = 8000
CALL_CAP = 60
INPUT_CEILING = 250_000
QUERY_CAP = 128
# NOTE(vani): pages that hold the answer key (amendment 2026-10-19-03): either paper by arXiv id on any mirror, anything named
# after either benchmark (the HF dataset pages, the project page, title-slug mirrors), and a GitHub repo hosting LBC records.
BLOCKLIST = (
    ("arXiv 2605.28721 (LiveBrowseComp paper)", re.compile(r"2605\.28721")),
    ("arXiv 2606.13120 (EvoBrowseComp paper)", re.compile(r"2606\.13120")),
    ("named after LiveBrowseComp", re.compile(r"livebrowsecomp", re.I)),
    ("named after EvoBrowseComp", re.compile(r"evobrow(se)?comp", re.I)),
    ("lbc-30 repo (hosts LiveBrowseComp records)", re.compile(r"(github\.com|githubusercontent\.com)/vaniagarwal343/lbc-30\b", re.I)),
)

SEARCH_TOOL = {
    "type": "function", "name": "search", "strict": True,
    "description": "Search the web. Returns a JSON list of results, each with title, url, date and text.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["query"],
                   "properties": {"query": {"type": "string", "description": "Search query, at most 128 characters."}}},
}
FETCH_TOOL = {
    "type": "function", "name": "fetch", "strict": True,
    "description": "Fetch one web page. Returns its title and text.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["url"],
                   "properties": {"url": {"type": "string", "description": "An http or https URL."}}},
}


def blocked(url):
    """The BLOCKLIST rule a URL falls under, or None."""
    return next((rule for rule, pattern in BLOCKLIST if pattern.search(url or "")), None)


class ArmError(Exception):
    """A study-arm request failed; the message carries the provider's error text with keys scrubbed."""


def ok_json(resp, what):
    if resp.status_code == 200:
        return resp.json()
    try:
        message = resp.json().get("error", {}).get("message") or resp.text
    except ValueError:
        message = resp.text
    raise ArmError(env.scrub(f"{what}: HTTP {resp.status_code} {str(message)[:300]}"))


# NOTE(vani): each backend returns (payload, meta). The payload is exactly what the model sees; meta goes only to the trace,
# with each text's length as the backend returned it (Exa cuts to our cap on its side, so its lengths stop there).
def exa_search(client, args, purpose):
    # NOTE(vani): highlights are Exa's query-focused excerpts, the counterpart of DeepSeek's query-focused summary; page text is not.
    body = {"query": args["query"][:QUERY_CAP], "numResults": RESULTS, "contents": {"highlights": {"maxCharacters": SEARCH_TEXT}}}
    data = ok_json(client.request("POST", f"{EXA_URL}/search", purpose=purpose, body=body, headers={"x-api-key": env.get("EXA_API_KEY")}), "Exa search")
    rows = data.get("results", [])[:RESULTS]
    texts = [" ".join(r.get("highlights") or []) for r in rows]
    payload = [{"title": r.get("title"), "url": r.get("url"), "date": r.get("publishedDate"), "text": t[:SEARCH_TEXT]} for r, t in zip(rows, texts)]
    meta = {"backend_chars": [len(t) for t in texts], "query_sent": body["query"], "cost_dollars": (data.get("costDollars") or {}).get("total"),
            "request_id": data.get("requestId")}
    return payload, meta


def exa_fetch(client, args, purpose):
    body = {"urls": [args["url"]], "text": {"maxCharacters": PAGE_TEXT}}
    data = ok_json(client.request("POST", f"{EXA_URL}/contents", purpose=purpose, body=body, headers={"x-api-key": env.get("EXA_API_KEY")}), "Exa contents")
    page = (data.get("results") or [{}])[0]
    status = (data.get("statuses") or [{}])[0]
    text = page.get("text") or ""
    payload = {"title": page.get("title"), "url": page.get("url") or args["url"], "text": text[:PAGE_TEXT]}
    meta = {"status": status.get("status"), "source": status.get("source"), "error": status.get("error"), "backend_chars": len(text),
            "truncated": len(text) >= PAGE_TEXT, "cost_dollars": (data.get("costDollars") or {}).get("total"), "request_id": data.get("requestId")}
    return payload, meta


def deepseek_headers():
    return {"Authorization": f"Bearer {env.get('DEEPSEEK_API_KEY')}"}


def deepseek_search(client, args, purpose):
    # NOTE(vani): DeepSeek rejects queries over 128 characters, so both arms cut at QUERY_CAP and the search tool states it.
    body = {"query": args["query"][:QUERY_CAP], "count": RESULTS}
    data = ok_json(client.request("POST", f"{DEEPSEEK_URL}/search", purpose=purpose, body=body, headers=deepseek_headers()), "DeepSeek search")
    rows = data.get("results", [])[:RESULTS]
    payload = [{"title": r.get("title"), "url": r.get("url"), "date": r.get("date"), "text": (r.get("summary") or "")[:SEARCH_TEXT]} for r in rows]
    return payload, {"backend_chars": [len(r.get("summary") or "") for r in rows], "query_sent": body["query"]}


def deepseek_fetch(client, args, purpose):
    data = ok_json(client.request("POST", f"{DEEPSEEK_URL}/fetch", purpose=purpose, body={"url": args["url"]}, headers=deepseek_headers()), "DeepSeek fetch")
    text = data.get("content") or ""
    payload = {"title": data.get("title"), "url": args["url"], "text": text[:PAGE_TEXT]}
    return payload, {"status": "success", "backend_chars": len(text), "truncated": len(text) > PAGE_TEXT}


# NOTE(vani): None means the provider runs the search itself (the built-in arm, deferred in this freeze); the others are caller-run function tools.
ARMS = {
    "builtin": None,
    "exa": {"search": exa_search, "fetch": exa_fetch},
    "deepseek": {"search": deepseek_search, "fetch": deepseek_fetch},
}

