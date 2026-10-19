"""The study arms' backends and tools. CONFIG.md §1 ARMS names the arms in this freeze; each runs MODEL_UNDER_TEST in the Codex CLI
(harness/codex_arm.py), whose only web path is these two tools served by harness/mcp_search.py.

The settings below are CONFIG.md §3's: reasoning effort, results and characters per search, characters per fetch, the
call cap and the input-token ceiling. Every arm exposes the same two tools with the same names and descriptions.
"""
from pipeline import env

EXA_URL = "https://api.exa.ai"
DEEPSEEK_URL = "https://api.deepseek.com/v1/tools"
REASONING_EFFORT = "medium"
RESULTS = 10
SEARCH_TEXT = 400
PAGE_TEXT = 8000
CALL_CAP = 60
INPUT_CEILING = 250_000

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


def exa_search(client, args, purpose):
    body = {"query": args["query"], "numResults": RESULTS, "contents": {"text": {"maxCharacters": SEARCH_TEXT}}}
    data = ok_json(client.request("POST", f"{EXA_URL}/search", purpose=purpose, body=body, headers={"x-api-key": env.get("EXA_API_KEY")}), "Exa search")
    return [{"title": r.get("title"), "url": r.get("url"), "date": r.get("publishedDate"), "text": (r.get("text") or "")[:SEARCH_TEXT]}
            for r in data.get("results", [])[:RESULTS]]


def exa_fetch(client, args, purpose):
    body = {"urls": [args["url"]], "text": {"maxCharacters": PAGE_TEXT}}
    data = ok_json(client.request("POST", f"{EXA_URL}/contents", purpose=purpose, body=body, headers={"x-api-key": env.get("EXA_API_KEY")}), "Exa contents")
    page = (data.get("results") or [{}])[0]
    return {"title": page.get("title"), "url": page.get("url") or args["url"], "text": (page.get("text") or "")[:PAGE_TEXT]}


def deepseek_headers():
    return {"Authorization": f"Bearer {env.get('DEEPSEEK_API_KEY')}"}


def deepseek_search(client, args, purpose):
    # NOTE(vani): DeepSeek rejects queries over 128 characters, so every arm's search tool states that cap.
    body = {"query": args["query"][:128], "count": RESULTS}
    data = ok_json(client.request("POST", f"{DEEPSEEK_URL}/search", purpose=purpose, body=body, headers=deepseek_headers()), "DeepSeek search")
    return [{"title": r.get("title"), "url": r.get("url"), "date": r.get("date"), "text": (r.get("summary") or "")[:SEARCH_TEXT]}
            for r in data.get("results", [])[:RESULTS]]


def deepseek_fetch(client, args, purpose):
    data = ok_json(client.request("POST", f"{DEEPSEEK_URL}/fetch", purpose=purpose, body={"url": args["url"]}, headers=deepseek_headers()), "DeepSeek fetch")
    return {"title": data.get("title"), "url": args["url"], "text": (data.get("content") or "")[:PAGE_TEXT]}


# NOTE(vani): None means the provider runs the search itself (the built-in arm, deferred in this freeze); the others are caller-run function tools.
ARMS = {
    "builtin": None,
    "exa": {"search": exa_search, "fetch": exa_fetch},
    "deepseek": {"search": deepseek_search, "fetch": deepseek_fetch},
}

