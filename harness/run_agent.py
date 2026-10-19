"""Main run (C6): every frozen task × CONFIG.md §1 ARMS × 2 attempts on the Codex route (CONFIG.md §3). `make run`; resumable.

    python -m harness.run_agent [--block A1] [--limit 10] [--workers 4]

Each attempt runs harness/codex_arm.py in results/main/<task_id>/<arm>/attempt_<k>/try_<j>/. An infrastructure failure
(nonzero CLI exit, timeout, or a turn that failed on a limit) is retried up to RETRIES times, each try logged. A finished
attempt gets trace.json: every tool call in order with each search result's URL, title, text as returned and lengths, and
each fetch's URL, status, lengths and truncation; cap and ceiling refusals; Codex usage and rate-limit snapshot; the final
message, its Exact Answer line and wall time. An attempt with a trace.json never runs again. A usage or rate limit stops
new attempts, and an attempt it interrupted resumes on the next run with its try count kept.

Every trace and main_runs.jsonl row carries the commit of the code that ran it, the normalized config.toml hash, and the
isolation evidence (mcp_catalogs, foreign_items); the runner refuses to start on uncommitted code. As the attempts outside the
pilot tasks pass 25, 50, 75 and 100 percent, harness/report.py writes results/progress_<pct>.md: operational counts and costs,
no scores (amendment 2026-10-19-05). Jobs are queued task by task, both arms' attempts together, so the arms stay interleaved.
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import subprocess
import sys
import threading
import time

from harness import arms, codex_arm, judge, report
from pipeline import config, net

ATTEMPTS = 2
RETRIES = 3
BACKOFF = (60, 300, 900)
PROMPT = config.ROOT / "harness" / "prompts" / "arm_lbc30.txt"
PROMPT_SHA256 = "cee5eb765b1ad4ef7374bac3fda28703e02fd0cad63ac199235c4a40d96c0c82"
TASKS = config.ROOT / "tasks" / "tasks.json"
RUNS = config.ROOT / "results" / "main"
LOG = config.ROOT / "results" / "main_runs.jsonl"
LOCK = threading.Lock()
STOP = threading.Event()
ROW_FIELDS = ("created_at", "config_hash", "code_commit", "stage", "model", "task_id", "block", "arm", "attempt", "status", "retries",
              "exact_answer", "wall_s", "usage", "summary", "config_template_sha256", "mcp_catalogs", "foreign_items", "max_request_input_tokens")


def preflight():
    """The frozen tasks and the pinned prompt; refuses before the freeze tag, on another config or on another prompt."""
    tags = subprocess.run(["git", "-C", str(config.ROOT), "tag", "--list", "freeze-*"], capture_output=True, text=True).stdout.split()
    if not tags:
        raise SystemExit("no freeze tag: the main run starts only after the freeze (AGENTS.md, C5)")
    config.code_commit()
    data = json.loads(TASKS.read_text(encoding="utf-8"))
    if data["config_hash"] != config.config_hash(config.read()):
        raise SystemExit("tasks/tasks.json was written under another config_hash")
    raw = PROMPT.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROMPT_SHA256:
        raise SystemExit("harness/prompts/arm_lbc30.txt is not the prompt CONFIG.md §3 pins")
    return data["tasks"], raw.decode("utf-8")


def tool_calls(run_dir):
    log = run_dir / "tool_calls.jsonl"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").split("\n") if line.strip()] if log.exists() else []


def summarize(calls):
    done = [c for c in calls if "n" in c]
    searches = [c for c in done if c["tool"] == "search"]
    fetches = [c.get("fetch") or {} for c in done if c["tool"] == "fetch"]
    results = [r for c in searches for r in c.get("results") or []]
    return {"calls": len(done), "searches": len(searches), "fetches": len(fetches), "failed_calls": sum(not c["ok"] for c in done),
            "cap_hits": sum("limit of" in (c.get("refused") or "") for c in calls),
            "ceiling_hits": sum("ceiling" in (c.get("refused") or "") for c in calls),
            "search_results": len(results), "search_chars_backend": sum(r.get("backend_chars") or 0 for r in results),
            "search_chars_returned": sum(r["returned_chars"] for r in results),
            "fetch_chars_backend": sum(f.get("backend_chars") or 0 for f in fetches), "fetch_chars_returned": sum(f.get("returned_chars") or 0 for f in fetches),
            "fetches_truncated": sum(bool(f.get("truncated")) for f in fetches), "fetches_failed": sum(f.get("status") not in ("success", "BLOCKED_KEY_DOMAIN") for f in fetches),
            "blocked_results": sum(len(c.get("blocked") or []) for c in searches), "blocked_fetches": sum(f.get("status") == "BLOCKED_KEY_DOMAIN" for f in fetches),
            "queries_cut": sum(len((c.get("arguments") or {}).get("query") or "") > arms.QUERY_CAP for c in searches)}


def exact_line(text):
    found = list(judge.EXACT_ANSWER_RE.finditer(text or ""))
    return found[-1].group(0).strip() if found else None


def failed_try(rec):
    """Retried: a CLI crash (nonzero exit) or a transport error that ended the turn without a final message. A timeout is not a
    failure: the attempt is complete, has no answer and scores NO_ANSWER_LINE (amendment 2026-10-19-03)."""
    if rec["timed_out"]:
        return False
    return rec["returncode"] != 0 or (not rec["final_text"].strip() and bool(rec["errors"] or rec["limit_messages"]))


def isolation_breach(rec):
    """Why an attempt could reach more than the arm's MCP server, or None: another MCP catalog, a missing own catalog, or a foreign item."""
    if rec["mcp_catalogs"] != [("configured", 2)]:
        return f"MCP catalogs {rec['mcp_catalogs']}, expected only the arm's own (configured, 2 tools)"
    if rec["foreign_items"]:
        return f"items outside the arm's tools: {rec['foreign_items'][:3]}"
    return None


def append(rec):
    with LOCK:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def tries_on_disk(adir):
    dirs = sorted((d for d in adir.glob("try_*") if (d / "attempt.json").exists()), key=lambda d: int(d.name[4:]))
    return [(int(d.name[4:]), d, json.loads((d / "attempt.json").read_text(encoding="utf-8"))) for d in dirs]


def run_attempt(task, arm, k, template):
    adir = RUNS / task["task_id"] / arm / f"attempt_{k}"
    prompt = template.replace("{problem}", task["question"])
    started = time.monotonic()
    for j in range(len(tries_on_disk(adir)), RETRIES + 1):
        if j:
            time.sleep(BACKOFF[j - 1])
        if STOP.is_set():
            return None
        rec = codex_arm.run(arm, prompt, adir / f"try_{j}", f"main:{task['task_id']}:{arm}:a{k}:t{j}")
        breach = isolation_breach(rec)
        if breach:
            STOP.set()
            print(f"STOP: {task['task_id']} {arm} a{k} t{j} broke the arm's isolation, so it is not scored: {breach}", file=sys.stderr)
            return None
        if not failed_try(rec):
            break
        if rec["limit_messages"]:
            STOP.set()
            print(f"STOP: {task['task_id']} {arm} a{k} t{j} hit a limit: {rec['limit_messages'][0]}", file=sys.stderr)
            return None
    found = tries_on_disk(adir)
    last_try, run_dir, rec = found[-1]
    tries = [{"try": j, "infra_failure": failed_try(r), **{f: r[f] for f in ("created_at", "returncode", "timed_out", "elapsed_s", "errors",
                                                                             "limit_messages", "usage", "rate_limits")}} for j, _, r in found]
    status = "failed" if failed_try(rec) else "completed"
    timed_out = rec["timed_out"]
    final = rec["final_text"] if status == "completed" else ""
    calls = tool_calls(run_dir)
    trace = {"created_at": net.utc_now(), "config_hash": config.config_hash(config.read()), "code_commit": rec.get("code_commit"), "stage": "arm", "model": rec["model"],
             "task_id": task["task_id"], "block": task["block"], "arm": arm, "attempt": k, "status": status, "timed_out": timed_out, "retries": last_try,
             "tries": tries, "codex_version": rec["codex_version"], "template_sha256": PROMPT_SHA256,
             "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "final_text": final,
             "exact_answer_line": exact_line(final), "exact_answer": judge.extract_exact_answer(final),
             "wall_s": rec["elapsed_s"], "wall_s_this_run": round(time.monotonic() - started, 1), "usage": rec["usage"],
             "rate_limits": rec["rate_limits"], "summary": summarize(calls), "config_template_sha256": rec.get("config_template_sha256"),
             "mcp_catalogs": rec["mcp_catalogs"], "foreign_items": rec["foreign_items"], "max_request_input_tokens": rec.get("max_request_input_tokens"),
             "tool_calls": calls}
    (adir / "trace.json").write_text(json.dumps(trace, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    append({f: trace[f] for f in ROW_FIELDS})
    with LOCK:
        report.progress()
    s = trace["summary"]
    print(f"  {task['task_id']} {arm} a{k}: {status}, {s['calls']} calls ({s['searches']} search, {s['fetches']} fetch), "
          f"{trace['wall_s']}s, answer {trace['exact_answer']!r}", file=sys.stderr)
    return trace


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--block", default=None, help="only this block")
    ap.add_argument("--limit", type=int, default=None, help="only the first N tasks in task_id order")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    tasks, template = preflight()
    tasks = sorted((t for t in tasks if args.block in (None, t["block"])), key=lambda t: t["task_id"])[: args.limit]
    p = config.params(config.read())
    jobs = [(t, arm, k) for t in tasks for arm in config.listed(p["ARMS"]) for k in range(1, ATTEMPTS + 1)
            if not (RUNS / t["task_id"] / arm / f"attempt_{k}" / "trace.json").exists()]
    print(f"{len(jobs)} attempts to run ({len(tasks)} tasks × {len(config.listed(p['ARMS']))} arms × {ATTEMPTS}), {args.workers} at a time", file=sys.stderr)
    with cf.ThreadPoolExecutor(args.workers) as pool:
        done = [f.result() for f in cf.as_completed([pool.submit(run_attempt, t, arm, k, template) for t, arm, k in jobs])]
    finished = [t for t in done if t]
    print(f"{len(finished)} attempts finished ({sum(t['status'] == 'completed' for t in finished)} completed), "
          f"{len(jobs) - len(finished)} left{' (stopped on a limit)' if STOP.is_set() else ''}")
    return 1 if STOP.is_set() else 0


if __name__ == "__main__":
    sys.exit(main())
