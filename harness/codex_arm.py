"""One study-arm attempt in the Codex CLI (CONFIG.md §3).

Each attempt gets a fresh empty workdir and its own CODEX_HOME holding only the generated config.toml and a copy of the
owner's ChatGPT login. No OpenAI API key reaches the CLI. The model runs read-only with network access off and Codex's own web
search disabled; its only web path is harness/mcp_search.py, whose backend calls go through harness/net.py. The workdir,
login copy included, is deleted after the attempt; the event stream, stderr and the run record stay in run_dir.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from harness import arms, tokens
from pipeline import config, env, net

CODEX_VERSION = "codex-cli 0.162.1"
TIMEOUT = 3600
AUTH = Path.home() / ".codex" / "auth.json"
UNSET = ("OPENAI_API_KEY", "CODEX_API_KEY")
# NOTE(vani): Codex features that add tools or paths beyond the arm's MCP server (amendment 2026-10-19-04). `apps` alone adds the
# codex_apps catalog of 180 tools, among them ChatGPT's own web search; the shell tools could read any file on this disk.
FEATURES_OFF = ("apps", "plugins", "remote_plugin", "plugin_sharing", "tool_suggest", "skill_search", "skill_mcp_dependency_install",
                "browser_use", "browser_use_external", "browser_use_full_cdp_access", "browser_annotation_api", "in_app_browser", "computer_use",
                "image_generation", "view_image", "shell_tool", "unified_exec", "unified_exec_tty", "shell_snapshot", "multi_agent",
                "goals", "hooks", "sleep_tool")
CATALOG_RE = re.compile(r'MCP binding catalog materialized.*?server_kind="([^"]*)".*?tool_count=(\d+)')
OWN_ITEMS = ("agent_message", "reasoning")
LIMIT_RE = re.compile(r"rate.?limit|usage limit|too many requests|status(=| )429|quota", re.I)
# NOTE(vani): RUST_LOG=info stderr also carries tool-result text, so only ERROR/WARN lines and error events count as limit messages.
LEVEL_RE = re.compile(r"\b(ERROR|WARN)\b")
REQUEST_RE = re.compile(r"event\.kind=response\.completed input_token_count=(\d+)")
CONFIG_TOML = """model = "{model}"
model_reasoning_effort = "{effort}"
approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"

[sandbox_workspace_write]
network_access = false

[mcp_servers.search]
command = {python}
args = {args}
default_tools_approval_mode = "approve"
tool_timeout_sec = 180
startup_timeout_sec = 60

[features]
{features}
"""


def version():
    out = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    return out.splitlines()[0] if out else None


def config_text(arm, run_dir, purpose, prompt_tokens):
    model = config.params(config.read())["MODEL_UNDER_TEST"]
    args = [str(config.ROOT / "harness" / "mcp_search.py"), arm, str(run_dir), purpose, str(prompt_tokens)]
    return CONFIG_TOML.format(model=model, effort=arms.REASONING_EFFORT, python=json.dumps(sys.executable), args=json.dumps(args),
                              features="\n".join(f"{name} = false" for name in FEATURES_OFF))


def template_sha256():
    """sha256 of config.toml with the per-attempt arguments (arm, run_dir, purpose, prompt tokens) as placeholders: the same in every
    attempt of both arms, where the generated file's own hash differs per attempt (amendment 2026-10-19-05)."""
    return hashlib.sha256(config_text("{arm}", "{run_dir}", "{purpose}", "{prompt_tokens}").encode()).hexdigest()


def home(work, arm, run_dir, purpose, prompt):
    """Write CODEX_HOME's config.toml and login copy; returns the config text."""
    text = config_text(arm, run_dir, purpose, tokens.count(prompt))
    (work / "codex-home").mkdir()
    (work / "codex-home" / "config.toml").write_text(text, encoding="utf-8")
    shutil.copyfile(AUTH, work / "codex-home" / "auth.json")
    os.chmod(work / "codex-home" / "auth.json", 0o600)
    return text


def events(stdout):
    out = []
    for line in (stdout or "").split("\n"):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def rate_limits(home_dir):
    """The last rate-limit snapshot (plan window used, credits balance) in the attempt's session log, or None."""
    snap = None
    for path in sorted(home_dir.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"rate_limits"' not in line:
                continue
            try:
                found = [f for f in find_key(json.loads(line), "rate_limits") if f]
            except ValueError:
                continue
            snap = found[-1] if found else snap
    return snap


def find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            yield obj[key]
        for v in obj.values():
            yield from find_key(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from find_key(v, key)


def catalogs(err):
    """Every MCP tool catalog Codex built, from its RUST_LOG=info stderr, as [(server kind, tool count)]; the arm's own is ("configured", 2)."""
    return sorted(set((kind, int(n)) for kind, n in CATALOG_RE.findall(re.sub(r"\x1b\[[0-9;]*m", "", err or ""))))


def foreign(evs):
    """Items other than the model's own messages and calls to the arm's `search` server: any such item breaks the arm's isolation."""
    out = []
    for e in evs:
        it = e.get("item") or {}
        if e.get("type") != "item.completed" or it.get("type") in OWN_ITEMS:
            continue
        if it.get("type") == "mcp_tool_call" and it.get("server") == "search":
            continue
        out.append(f"{it.get('type')}: {it.get('server') or ''}/{it.get('tool') or it.get('command') or ''}"[:200])
    return out


def limit_messages(evs, err):
    """Error events, and ERROR or WARN lines on stderr, that mention a rate limit, usage limit or quota."""
    lines = [l for l in re.sub(r"\x1b\[[0-9;]*m", "", err or "").splitlines() if LEVEL_RE.search(l)]
    texts = [json.dumps(e) for e in evs if e.get("type") in ("error", "turn.failed")] + lines
    return [env.scrub(t)[:300] for t in texts if LIMIT_RE.search(t)]


def run(arm, prompt, run_dir, purpose, timeout=TIMEOUT):
    """One attempt: returns the run record, with the final message in "final_text"."""
    if version() != CODEX_VERSION:
        raise SystemExit(f"codex --version is {version()!r}; CONFIG.md §3 pins {CODEX_VERSION}")
    commit = config.code_commit()
    model = config.params(config.read())["MODEL_UNDER_TEST"]
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="dse-arm-"))
    try:
        toml = home(work, arm, run_dir, purpose, prompt)
        cmd = ["codex", "exec", "-m", model, "--sandbox", "read-only", "-c", 'approval_policy="never"',
               "-c", f'model_reasoning_effort="{arms.REASONING_EFFORT}"', "--strict-config", "--skip-git-repo-check",
               "--cd", str(work), "--json", "-o", str(work / "last_message.txt"), prompt]
        # NOTE(vani): HOME is the empty workdir, so nothing from the owner's home (skills, config) reaches the arm; RUST_LOG=info makes
        # Codex log each MCP catalog it builds, which the record keeps as proof of what the model could call.
        child_env = {k: v for k, v in os.environ.items() if k not in UNSET}
        child_env.update(CODEX_HOME=str(work / "codex-home"), HOME=str(work), RUST_LOG="info")
        created, started = net.utc_now(), time.monotonic()
        try:
            # NOTE(vani): stdin must be closed: `codex exec` appends piped stdin to the prompt and waits for EOF when it is not a TTY.
            proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=timeout, env=child_env, stdin=subprocess.DEVNULL)
            rc, out, err, timed_out = proc.returncode, proc.stdout, proc.stderr, False
        except subprocess.TimeoutExpired as exc:
            out, err = (s.decode(errors="replace") if isinstance(s, bytes) else (s or "") for s in (exc.stdout, exc.stderr))
            rc, timed_out = None, True
        last = work / "last_message.txt"
        final = last.read_text(encoding="utf-8") if rc == 0 and last.exists() else ""
        snapshot = rate_limits(work / "codex-home")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    evs = events(out)
    usage = next((e.get("usage") for e in reversed(evs) if e.get("type") == "turn.completed"), None)
    log = run_dir / "tool_calls.jsonl"
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").split("\n") if line.strip()] if log.exists() else []
    # NOTE(vani): .log, not .jsonl: the CLI's own event stream is not a pipeline record, so the guards leave it alone.
    (run_dir / "codex_stdout.log").write_text(env.scrub(out or ""), encoding="utf-8")
    (run_dir / "stderr.txt").write_text(env.scrub(err or ""), encoding="utf-8")
    record = {"created_at": created, "config_hash": config.config_hash(config.read()), "stage": "arm", "model": model, "arm": arm,
              "purpose": purpose, "code_commit": commit, "codex_version": CODEX_VERSION, "config_toml_sha256": hashlib.sha256(toml.encode()).hexdigest(),
              "config_template_sha256": template_sha256(),
              "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "returncode": rc, "timed_out": timed_out,
              "elapsed_s": round(time.monotonic() - started, 1), "final_text": final, "usage": usage,
              "tool_calls": sum(1 for c in calls if "n" in c), "refused_calls": sum(1 for c in calls if c.get("refused")),
              "web_search_events": sum(1 for e in evs if "web_search" in json.dumps(e)),
              "errors": [env.scrub(json.dumps(e))[:300] for e in evs if e.get("type") in ("error", "turn.failed")],
              "limit_messages": limit_messages(evs, err), "rate_limits": snapshot, "mcp_catalogs": catalogs(err), "foreign_items": foreign(evs),
              "max_request_input_tokens": max((int(n) for n in REQUEST_RE.findall(err or "")), default=None)}
    (run_dir / "attempt.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return record
