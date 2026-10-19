"""Stage 0 smoke test (`make smoke`).

One MODEL_UNDER_TEST attempt per arm in CONFIG.md §1 ARMS, in the Codex CLI with the arm's MCP tools, whose backend calls go
through harness/net.py (AGENTS.md, C1), and a check that the CLI's sandbox has no network of its own. When BLOCKS includes
Block B it also pulls ten in-window records from every PROTOCOL.md §2 source and makes one Wayback submission. Writes
pipeline/smoke_report.md and exits 1 if any check falls short.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from harness import codex_arm, judge
from pipeline import config, env, net, wayback
from pipeline.sources import SOURCES

N_RECORDS = 10
# NOTE(vani): a throwaway question with a fixed, well-known answer. It is not a candidate and nothing is drafted from it.
QUESTION = ("In what year did the Eiffel Tower open to the public? Use your search tool at least once to confirm it, "
            "then give only the year inside <answer></answer>.")
EXPECTED = "1889"
REPORT = config.ROOT / "pipeline" / "smoke_report.md"
RECORDS = config.ROOT / "pipeline" / "smoke_records.jsonl"
ARM_RECORDS = config.ROOT / "results" / "smoke_arms.jsonl"
ARM_RUNS = config.ROOT / "results" / "smoke"
# NOTE(vani): run under the arms' own sandbox profile, each probe must fail: no HTTP, no DNS.
PROBES = {"curl": ["curl", "-sS", "-m", "10", "-o", "/dev/null", "-w", "HTTP %{http_code}", "https://example.com"],
          "dns": ["python3", "-c", "import socket;print(socket.gethostbyname('example.com'))"]}
BACKENDS = {"builtin": "OpenAI web_search (hosted)", "exa": "api.exa.ai", "deepseek": "api.deepseek.com"}
LOGS = (config.ROOT / "pipeline" / "requests.log", config.ROOT / "results" / "requests.log")


def append(path, rec):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def failure(exc):
    return env.scrub(f"{type(exc).__name__}: {exc}")[:300].replace("|", "/").replace("\n", " ")


def pull_sources(client, w_start, w_end, end):
    rows = []
    for src in SOURCES:
        key = src.__name__.rsplit(".", 1)[-1]
        try:
            records, error = src.pull(client, w_start, end, N_RECORDS, purpose=f"smoke:source:{key}"), None
        except Exception as exc:  # NOTE(vani): one broken source must not hide the others' results.
            records, error = [], failure(exc)
        for rec in records:
            rec["in_window"] = bool(rec.get("date")) and w_start.isoformat() <= rec["date"] <= w_end.isoformat()
            append(RECORDS, {"created_at": net.utc_now(), "config_hash": client.config_hash, "source": src.NAME, **rec})
        rows.append({"source": src.NAME, "records": records, "in_window": sum(r["in_window"] for r in records), "error": error})
        print(f"  {src.NAME}: {rows[-1]['in_window']}/{len(records)} in window" + (f"; {error}" if error else ""), file=sys.stderr)
    return rows


def submit_wayback(client, rows):
    urls = [r["records"][0]["record_url"] for r in rows if r["records"] and r["records"][0].get("record_url")]
    if not urls:
        return None, None
    snapshot = wayback.submit(client, urls[0], purpose="smoke:wayback")
    print(f"  Wayback: {snapshot or 'no snapshot URL (logged)'}", file=sys.stderr)
    return urls[0], snapshot


def run_arms(names, started):
    rows = []
    for arm in names:
        run_dir = ARM_RUNS / started.replace(":", "") / arm
        try:
            rec, error = codex_arm.run(arm, QUESTION, run_dir, f"smoke:arm:{arm}"), None
        except Exception as exc:  # NOTE(vani): one failing arm must not hide the others.
            rec, error = {"final_text": "", "returncode": None, "web_search_events": 0, "usage": None}, failure(exc)
        log = run_dir / "tool_calls.jsonl"
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").split("\n") if line.strip()] if log.exists() else []
        answer = judge.extract_answer(rec["final_text"])
        append(ARM_RECORDS, {**rec, "question": QUESTION, "answer": answer, "error": error, "run_dir": str(run_dir.relative_to(config.ROOT))})
        rows.append({"arm": arm, "rec": rec, "calls": calls, "answer": answer, "error": error})
        print(f"  {arm}: {len(calls)} tool calls, answer {answer!r}, exit {rec['returncode']}" + (f"; {error}" if error else ""), file=sys.stderr)
    return rows


def isolation():
    """Each PROBES command under `codex sandbox` with the arms' config.toml; a probe passes when it fails to reach the network."""
    work = Path(tempfile.mkdtemp(prefix="dse-netcheck-"))
    try:
        codex_arm.home(work, "exa", work / "unused", "smoke:netcheck", "")
        child_env = {k: v for k, v in os.environ.items() if k not in codex_arm.UNSET}
        child_env["CODEX_HOME"] = str(work / "codex-home")
        out = {}
        for name, probe in PROBES.items():
            p = subprocess.run(["codex", "sandbox", "-P", ":read-only", "-C", str(work), "--"] + probe,
                               capture_output=True, text=True, timeout=60, env=child_env, cwd=work)
            text = (p.stdout + p.stderr).strip()
            reached = any(line.strip().startswith("HTTP 2") or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", line.strip()) for line in text.splitlines())
            out[name] = {"pass": p.returncode != 0 and not reached, "returncode": p.returncode, "output": text[-160:].replace("\n", " ")}
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def calls_text(calls):
    counts = {}
    for call in calls:
        label = call["tool"] + ("" if call.get("ok") else " (refused)" if call.get("refused") else " (failed)")
        counts[label] = counts.get(label, 0) + 1
    return ", ".join(f"{label} ×{n}" for label, n in counts.items()) or "none"


def requests_since(path, started):
    if not path.exists():
        return 0
    # NOTE(vani): only smoke purposes count; a gate running at the same time logs to the same files.
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sum(1 for r in rows if r["timestamp"] >= started and r["purpose"].startswith("smoke:"))


def source_lines(window, end, source_rows, wb_url, snapshot):
    w_start, _ = window
    lines = [f"## Sources: {N_RECORDS} in-window records each", "",
             "| Source | Pulled | In window | Dates | First record | Result |",
             "|---|---|---|---|---|---|"]
    for r in source_rows:
        dates = sorted(x["date"] for x in r["records"] if x.get("date"))
        first = f"`{r['records'][0]['record_id'][:60]}`" if r["records"] else "–"
        result = r["error"] or ("ok" if r["in_window"] >= N_RECORDS else f"only {r['in_window']} in window")
        lines.append(f"| {r['source']} | {len(r['records'])} | {r['in_window']} | {f'{dates[0]} to {dates[-1]}' if dates else '–'} | {first} | {result} |")
    lines += ["", "Raw records: `pipeline/smoke_records.jsonl`.", "", "## Wayback: one submission", ""]
    mode = "keyed Save Page Now" if env.get("WAYBACK_ACCESS_KEY") and env.get("WAYBACK_SECRET_KEY") else "anonymous Save Page Now (no Wayback keys in .env)"
    if wb_url is None:
        lines.append("No source returned a record URL, so nothing was submitted.")
    elif snapshot:
        lines.append(f"Submitted {wb_url} through {mode}. Snapshot: {snapshot}")
    else:
        lines.append(f"Submitted {wb_url} through {mode}. No snapshot URL came back; logged to `pipeline/wayback_failures.jsonl`.")
    return lines + [""]


def report(started, digest, model, block_b, window, end, source_rows, wb_url, snapshot, arm_rows, probes):
    """Write pipeline/smoke_report.md and return whether the smoke test passed."""
    broken = [r["arm"] for r in arm_rows if r["error"] or r["rec"]["returncode"] != 0 or r["rec"]["web_search_events"]
              or not any(c.get("ok") for c in r["calls"]) or r["answer"] is None]
    leaky = [name for name, v in probes.items() if not v["pass"]]
    problems = [f"arms failed: {', '.join(broken)}"] * bool(broken) + [f"sandbox reached the network: {', '.join(leaky)}"] * bool(leaky)
    if block_b:
        short = [r["source"] for r in source_rows if r["in_window"] < N_RECORDS]
        problems = [f"sources short: {', '.join(short)}"] * bool(short) + ["no Wayback submission"] * (wb_url is None) + problems
    lines = ["# Smoke report", "", f"Stage 0 (`make smoke`), {started} to {net.utc_now()}, config_hash `{digest[:12]}`."]
    if block_b:
        lines.append(f"Window {window[0]} to {window[1]}. Records exist only through {end}, so every pull covers {window[0]} to {end}.")
    lines += ["", f"**Result: {'PASS' if not problems else 'FAIL'}**" + (f" ({'; '.join(problems)})" if problems else ""), ""]
    if block_b:
        lines += source_lines(window, end, source_rows, wb_url, snapshot)
    else:
        lines += ["Block B is deferred (CONFIG.md §1 `DEFERRED`), so this run pulls no seed records and submits nothing to Wayback.", ""]
    lines += [
        "## Study arms: MODEL_UNDER_TEST in the Codex CLI", "",
        f"Model `{model}` in `{codex_arm.CODEX_VERSION}`, one attempt per arm on the owner's ChatGPT login. The CLI's only web path is "
        "the arm's MCP tools (`harness/mcp_search.py`), whose backend calls go through `harness/net.py`. "
        f"Throwaway question: \"{QUESTION}\" Expected answer: {EXPECTED}.", "",
        "| Arm | Backend | Tool calls | Codex web_search events | Answer | Input / output tokens | Result |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in arm_rows:
        rec, u = r["rec"], r["rec"].get("usage") or {}
        result = r["error"] or ("ok" if r["arm"] not in broken else f"exit {rec['returncode']}, no successful tool call, a Codex web search or no answer")
        lines.append(f"| {r['arm']} | {BACKENDS[r['arm']]} | {calls_text(r['calls'])} | {rec['web_search_events']} | "
                     f"{r['answer'] if r['answer'] is not None else 'no <answer> tag'} | {u.get('input_tokens', '–')} / {u.get('output_tokens', '–')} | {result} |")
    lines += ["", "## Sandbox network isolation", "",
              "Each probe ran under `codex sandbox -P :read-only` with the arms' config.toml, and passes when it cannot reach the network.", "",
              "| Probe | Exit | Output | Result |", "|---|---|---|---|"]
    for name, v in probes.items():
        lines.append(f"| {name} | {v['returncode']} | `{v['output']}` | {'pass' if v['pass'] else 'FAIL'} |")
    lines += ["", f"Records: `results/smoke_arms.jsonl`, `results/smoke/`. Requests this run: {requests_since(LOGS[0], started)} in `pipeline/requests.log`, "
              f"{requests_since(LOGS[1], started)} in `results/requests.log`.", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    return not problems


def main():
    text = config.read()
    p = config.params(text)
    block_b = "B" in config.listed(p["BLOCKS"])
    started = net.utc_now()
    window = end = source_rows = wb_url = snapshot = None
    if block_b:
        client = net.client()
        window = config.window(p)
        end = min(window[1], dt.datetime.now(dt.UTC).date())
        print("sources:", file=sys.stderr)
        source_rows = pull_sources(client, window[0], window[1], end)
        print("wayback:", file=sys.stderr)
        wb_url, snapshot = submit_wayback(client, source_rows)
    print("arms:", file=sys.stderr)
    arm_rows = run_arms(config.listed(p["ARMS"]), started)
    print("sandbox network isolation:", file=sys.stderr)
    probes = isolation()
    print("  " + ", ".join(f"{name} {'pass' if v['pass'] else 'FAIL'}" for name, v in probes.items()), file=sys.stderr)
    passed = report(started, config.config_hash(text), p["MODEL_UNDER_TEST"], block_b, window, end, source_rows, wb_url, snapshot, arm_rows, probes)
    print(f"{'PASS' if passed else 'FAIL'}: wrote {REPORT.relative_to(config.ROOT)}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
