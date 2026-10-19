"""Request policy shared by pipeline/net.py and harness/net.py; tests/net_guard.py re-applies it to both request logs."""
import re

# NOTE(vani): pipeline contact with a study backend pre-selects the task set for that backend's index (PROTOCOL.md §0).
# DeepSeek is refused at host level because its chat API can also run server-side search, and no pipeline role uses DeepSeek.
PIPELINE_FORBIDDEN_HOSTS = frozenset({"api.exa.ai", "mcp.exa.ai", "api.deepseek.com"})
# NOTE(vani): provider-hosted search and fetch (Anthropic, Perplexity, Gemini grounding). Only the UNIQ_AGENTS may use it, in G3 (PROTOCOL.md §0).
NATIVE_SEARCH = ("web_search", "web_fetch", "google_search")
# NOTE(vani): before the main run MODEL_UNDER_TEST holds tools only on smoke and dev items; the main run adds its purpose at C6, after the freeze tag.
HARNESS_TOOL_PURPOSES = ("smoke", "dev")


def is_model(model, target):
    # NOTE(vani): resellers prefix the model id (openai/gpt-6-astra, us.openai.gpt-6-astra), so match on the suffix.
    return bool(model) and re.search(rf"(^|[/.]){re.escape(target)}$", model) is not None


def tool_ids(body):
    """Tool identifiers in a JSON request body: the tool's type, `function:<name>` for a caller-run tool, or the only key (Gemini)."""
    ids = []
    for tool in (body or {}).get("tools") or ():
        if not isinstance(tool, dict) or not tool:
            continue
        kind = tool.get("type") or ("function" if "name" in tool else next(iter(tool)))
        ids.append(f"function:{tool.get('name')}" if kind in ("function", "custom") else kind)
    return tuple(ids)


def pipeline_refusal(host, model_under_test, model=None, tools=(), purpose=""):
    """Why the pipeline client must refuse this request, or None if it may go out."""
    if host in PIPELINE_FORBIDDEN_HOSTS:
        return f"{host} is a study backend"
    if host == "api.openai.com" and any(t.startswith("web_search") for t in tools):
        return "OpenAI web_search is the built-in study arm's backend"
    if is_model(model, model_under_test):
        if tools:
            return "MODEL_UNDER_TEST with tools goes through harness/net.py only"
        if not purpose.startswith("G2"):
            return "MODEL_UNDER_TEST outside G2 before the main run"
    if any(t.startswith(NATIVE_SEARCH) for t in tools) and not purpose.startswith("G3"):
        return "native search outside G3; every other role searches through PIPELINE_SEARCH"
    return None


def harness_refusal(host, model_under_test, model=None, tools=(), purpose=""):
    """Why the harness client must refuse this request, or None. Study backends are allowed here."""
    if is_model(model, model_under_test) and tools and not purpose.startswith(HARNESS_TOOL_PURPOSES):
        return "MODEL_UNDER_TEST with tools before the main run is for smoke and dev items only"
    return None
