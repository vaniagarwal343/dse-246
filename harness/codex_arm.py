"""One study-arm attempt in the Codex CLI (CONFIG.md §3).

Each attempt gets a fresh empty workdir and its own CODEX_HOME holding only the generated config.toml and a copy of the
owner's ChatGPT login. No OpenAI API key reaches the CLI. The model runs read-only with network access off and Codex's own web
search disabled; its only web path is harness/mcp_search.py, whose backend calls go through harness/net.py. The workdir,
login copy included, is deleted after the attempt; the event stream, stderr and the run record stay in run_dir.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from harness import arms
from pipeline import config, env, net

CODEX_VERSION = "codex-cli 0.162.1"
TIMEOUT = 3600
AUTH = Path.home() / ".codex" / "auth.json"
UNSET = ("OPENAI_API_KEY", "CODEX_API_KEY")
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
"""


def version():
    out = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    return out.splitlines()[0] if out else None


def home(work, arm, run_dir, purpose, prompt):
    """Write CODEX_HOME's config.toml and login copy; returns the config text."""
    model = config.params(config.read())["MODEL_UNDER_TEST"]
    args = [str(config.ROOT / "harness" / "mcp_search.py"), arm, str(run_dir), purpose, str(len(prompt))]
    text = CONFIG_TOML.format(model=model, effort=arms.REASONING_EFFORT, python=json.dumps(sys.executable), args=json.dumps(args))
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


def run(arm, prompt, run_dir, purpose, timeout=TIMEOUT):
    """One attempt: returns the run record, with the final message in "final_text"."""
    if version() != CODEX_VERSION:
        raise SystemExit(f"codex --version is {version()!r}; CONFIG.md §3 pins {CODEX_VERSION}")
    model = config.params(config.read())["MODEL_UNDER_TEST"]
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="dse-arm-"))
    try:
        toml = home(work, arm, run_dir, purpose, prompt)
        cmd = ["codex", "exec", "-m", model, "--sandbox", "read-only", "-c", 'approval_policy="never"',
               "-c", f'model_reasoning_effort="{arms.REASONING_EFFORT}"', "--strict-config", "--skip-git-repo-check",
               "--cd", str(work), "--json", "-o", str(work / "last_message.txt"), prompt]
        child_env = {k: v for k, v in os.environ.items() if k not in UNSET}
        child_env["CODEX_HOME"] = str(work / "codex-home")
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
              "purpose": purpose, "codex_version": CODEX_VERSION, "config_toml_sha256": hashlib.sha256(toml.encode()).hexdigest(),
              "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "returncode": rc, "timed_out": timed_out,
              "elapsed_s": round(time.monotonic() - started, 1), "final_text": final, "usage": usage,
              "tool_calls": sum(1 for c in calls if "n" in c), "refused_calls": sum(1 for c in calls if c.get("refused")),
              "web_search_events": sum(1 for e in evs if "web_search" in json.dumps(e))}
    (run_dir / "attempt.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return record
