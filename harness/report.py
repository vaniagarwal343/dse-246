"""Main-run reports (CONFIG.md §6; amendments/2026-10-19-02.md §2 and §4, 2026-10-19-05). `make report`.

    python -m harness.report [--scores] [--out PATH]

Every report gives, per arm and with and without the pilot tasks: attempts, retries, timeouts, NO_ANSWER_LINE, the rates of
attempts that hit the call cap or the input-token ceiling, the fetch failure rate, isolation evidence, the run's dates and
Codex limit snapshots, and cost split into model tokens and backend fees at amendment 02's list prices. --scores adds CONFIG §6's
analysis from results/grades.jsonl. The runner writes results/progress_<pct>.md without scores (progress()), so no interim
accuracy is seen while the run is under way.
"""
import argparse
import datetime as dt
import json
import math
import random
import statistics
import sys

from harness import arms as arms_config
from pipeline import config, net

RUNS = config.ROOT / "results" / "main"
LOG = config.ROOT / "results" / "main_runs.jsonl"
GRADES = config.ROOT / "results" / "grades.jsonl"
TASKS = config.ROOT / "tasks" / "tasks.json"
# NOTE(vani): the staged attempts of amendment 2026-10-19-02 §6: the first ten A1 tasks in task_id order.
PILOT_TASKS = ("A1-008", "A1-010", "A1-015", "A1-022", "A1-024", "A1-029", "A1-030", "A1-032", "A1-040", "A1-042")
PROGRESS = (25, 50, 75, 100)
ATTEMPTS = 2
# NOTE(vani): list prices per token or per call, amendments/2026-10-19-02.md §2. Backend fees count calls that returned a result.
MODEL_PRICE = {"input": 10 / 1e6, "cached_input": 1 / 1e6, "output": 50 / 1e6}
BACKEND_PRICE = {"exa": {"search": 7 / 1000, "fetch": 1 / 1000}, "deepseek": {"search": 0.75 / 1000, "fetch": 0.75 / 1000}}
BOOTSTRAP = 10_000
INCONCLUSIVE = 0.12


def rows():
    """The latest main_runs.jsonl row per attempt."""
    latest = {}
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                latest[(r["task_id"], r["arm"], r["attempt"])] = r
    return latest


def trace(r):
    path = RUNS / r["task_id"] / r["arm"] / f"attempt_{r['attempt']}" / "trace.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def arms():
    return config.listed(config.params(config.read())["ARMS"])


def frozen_tasks():
    return json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]


def model_cost(usage):
    usage = usage or {}
    cached = usage.get("cached_input_tokens") or 0
    return ((usage.get("input_tokens", 0) - cached) * MODEL_PRICE["input"] + cached * MODEL_PRICE["cached_input"]
            + usage.get("output_tokens", 0) * MODEL_PRICE["output"])


def backend_calls(tr):
    done = [c for c in tr.get("tool_calls") or [] if "n" in c and c.get("ok")]
    return {tool: sum(c["tool"] == tool for c in done) for tool in ("search", "fetch")}


def pct(n, d):
    return f"{n}/{d} ({100 * n / d:.1f}%)" if d else "0/0"


def operational(scoped, label):
    lines = [f"### {label}", "", "| | " + " | ".join(arms()) + " |", "|---|" + "---|" * len(arms())]
    stats = {}
    for arm in arms():
        mine = [r for r in scoped if r["arm"] == arm]
        traces = [trace(r) for r in mine]
        s = [r["summary"] for r in mine]
        calls = {tool: sum(backend_calls(t)[tool] for t in traces) for tool in ("search", "fetch")}
        model = sum(model_cost(r["usage"]) for r in mine)
        backend = sum(calls[tool] * BACKEND_PRICE[arm][tool] for tool in calls)
        isolated = sum(r.get("mcp_catalogs") in ([["configured", 2]], [("configured", 2)]) and not r.get("foreign_items") for r in mine)
        stats[arm] = {
            "attempts": str(len(mine)),
            "completed": pct(sum(r["status"] == "completed" for r in mine), len(mine)),
            "attempts retried": pct(sum(bool(r["retries"]) for r in mine), len(mine)),
            "timeouts": pct(sum(bool(t.get("timed_out")) for t in traces), len(mine)),
            "NO_ANSWER_LINE": pct(sum(r["status"] == "completed" and r["exact_answer"] is None for r in mine), len(mine)),
            "attempts hitting the call cap": pct(sum(x["cap_hits"] > 0 for x in s), len(mine)),
            "attempts hitting the token ceiling": pct(sum(x["ceiling_hits"] > 0 for x in s), len(mine)),
            "fetch failure rate": pct(sum(x["fetches_failed"] for x in s), sum(x["fetches"] for x in s)),
            "tool calls (search / fetch)": f"{sum(x['searches'] for x in s)} / {sum(x['fetches'] for x in s)}",
            "isolation evidence clean": pct(isolated, len(mine)),
            "largest model request (input tokens)": f"{max((r.get('max_request_input_tokens') or 0) for r in mine):,}" if mine else "n/a",
            "attempts with a request over the ceiling": pct(sum((r.get("max_request_input_tokens") or 0) > arms_config.INPUT_CEILING for r in mine), len(mine)),
            "median wall time (s)": f"{statistics.median(r['wall_s'] for r in mine):.0f}" if mine else "n/a",
            "model tokens, list price": f"${model:,.2f}",
            "backend fees, list price": f"${backend:,.2f}",
            "total cost, list price": f"${model + backend:,.2f}",
            "_total": model + backend,
        }
    for name in [k for k in next(iter(stats.values()), {}) if not k.startswith("_")]:
        lines.append(f"| {name} | " + " | ".join(stats[arm][name] for arm in arms()) + " |")
    return lines + [""], stats


def run_window(all_rows):
    stamps = sorted(r["created_at"] for r in all_rows)
    snaps = []
    for r in sorted(all_rows, key=lambda r: r["created_at"]):
        limits = trace(r).get("rate_limits") or {}
        primary = limits.get("primary") or {}
        snaps.append((r["created_at"], primary.get("used_percent"), (limits.get("credits") or {}).get("balance"), primary.get("resets_at")))
    by_task = {}
    for r in all_rows:
        by_task.setdefault(r["task_id"], []).append(dt.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")))
    spans = [(max(v) - min(v)).total_seconds() / 60 for v in by_task.values() if len(v) > 1]
    lines = ["## Dates and Codex limits", ""]
    if stamps:
        lines.append(f"Attempts finished between {stamps[0]} and {stamps[-1]} (UTC).")
    if snaps:
        first, last = snaps[0], snaps[-1]
        resets = sorted({dt.datetime.fromtimestamp(s[3], dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ") for s in snaps if s[3]})
        lines.append(f"Codex weekly window used: {first[1]}% at the first attempt, {last[1]}% at the last; credits balance {first[2]} then {last[2]}.")
        if resets:
            lines.append("Weekly window resets seen: " + ", ".join(resets) + ".")
    if spans:
        lines.append(f"Arms interleaved: all attempts of a task finished within {statistics.median(spans):.0f} minutes of each other at the median, {max(spans):.0f} at most.")
    return lines + [""]


def item_scores(grades, block, tasks, field):
    """task_id -> {arm: mean score} for the tasks of a block where every arm has every attempt graded correct or incorrect."""
    marks = {}
    for g in grades:
        verdict = g.get(field, g["final"])
        if g["block"] == block and g["task_id"] in tasks and verdict in ("correct", "incorrect"):
            marks.setdefault(g["task_id"], {}).setdefault(g["arm"], []).append(verdict == "correct")
    return {t: {a: sum(v) / len(v) for a, v in by_arm.items()} for t, by_arm in marks.items()
            if set(by_arm) == set(arms()) and all(len(v) == ATTEMPTS for v in by_arm.values())}


def scores(grades, tasks, label, field):
    first, second = arms()[:2]
    lines = [f"### {label}", "", f"Difference is {first} minus {second}, in points; 95% CI from {BOOTSTRAP:,} bootstrap resamples of items, "
             f"seeded with RNG_SEED. A gap under {INCONCLUSIVE * 100:.0f} points is inconclusive (CONFIG.md §6).", "",
             f"| Block | Items | {first} accuracy | {second} accuracy | Difference | 95% CI | Verdict | pass@2 ({first} / {second}) | items at 0.5 ({first} / {second}) |",
             "|---|---|---|---|---|---|---|---|---|"]
    seed = int(config.params(config.read())["RNG_SEED"])
    for block in config.listed(config.params(config.read())["BLOCKS"]):
        items = item_scores(grades, block, tasks, field)
        n = len(items)
        if not n:
            lines.append(f"| {block} | 0 | | | | | | | |")
            continue
        acc = {a: sum(v[a] for v in items.values()) / n for a in (first, second)}
        diffs = [v[first] - v[second] for v in items.values()]
        rng = random.Random(seed)
        boot = sorted(sum(rng.choice(diffs) for _ in range(n)) / n for _ in range(BOOTSTRAP))
        low, high = boot[math.floor(0.025 * BOOTSTRAP)], boot[math.ceil(0.975 * BOOTSTRAP) - 1]
        gap = acc[first] - acc[second]
        verdict = "inconclusive" if abs(gap) < INCONCLUSIVE else f"{first if gap > 0 else second} ahead"
        pass2 = {a: sum(v[a] > 0 for v in items.values()) / n for a in (first, second)}
        half = {a: sum(v[a] == 0.5 for v in items.values()) / n for a in (first, second)}
        lines.append(f"| {block} | {n} | {100 * acc[first]:.1f}% | {100 * acc[second]:.1f}% | {100 * gap:+.1f} | [{100 * low:+.1f}, {100 * high:+.1f}] | {verdict} | "
                     f"{100 * pass2[first]:.1f}% / {100 * pass2[second]:.1f}% | {100 * half[first]:.1f}% / {100 * half[second]:.1f}% |")
    return lines + [""]


def build(with_scores=False, title="Main run report"):
    commit = config.code_commit()
    all_rows = list(rows().values())
    every = {t["task_id"] for t in frozen_tasks()}
    scopes = (("All tasks", every), (f"Without the {len(PILOT_TASKS)} pilot tasks", every - set(PILOT_TASKS)))
    expected = len(every) * len(arms()) * ATTEMPTS
    lines = [f"# {title}", "", f"Written {net.utc_now()} under config_hash `{config.config_hash(config.read())[:12]}` by code at commit `{commit[:12]}`. "
             f"{len(all_rows)} of {expected} attempts recorded.", ""]
    lines += run_window(all_rows)
    lines += ["## Operations and cost", ""]
    totals = {}
    for label, tasks in scopes:
        part, stats = operational([r for r in all_rows if r["task_id"] in tasks], label)
        lines += part
        totals[label] = stats
    if with_scores:
        grades = [json.loads(line) for line in GRADES.read_text(encoding="utf-8").splitlines() if line.strip()] if GRADES.exists() else []
        pending = sum(g["final"].startswith("pending") for g in grades)
        lines += ["## Scores", "", f"{len(grades)} grades in results/grades.jsonl, {pending} pending.", ""]
        rulings = sum(g["final"] != g.get("final_as_frozen", g["final"]) for g in grades)
        lines += [f"Each table is given with the gold as frozen and after contested-gold adjudication (amendment 2026-10-19-05 §1.8); "
                  f"{rulings} grades differ between the two.", ""]
        for gold, field in (("gold as frozen", "final_as_frozen"), ("after adjudication", "final")):
            for label, tasks in scopes:
                lines += scores(grades, tasks, f"{label}, {gold}", field)
                correct = {a: sum(g.get(field, g["final"]) == "correct" for g in grades if g["arm"] == a and g["task_id"] in tasks) for a in arms()}
                lines += [f"Cost per correct answer, list price ({label.lower()}, {gold}): "
                          + "; ".join(f"{a} ${totals[label][a]['_total'] / correct[a]:,.2f}" if correct[a] else f"{a} n/a" for a in arms()), ""]
    return "\n".join(lines) + "\n"


def progress():
    """Write results/progress_<pct>.md, without scores, the first time the attempts outside the pilot tasks pass each quarter."""
    outside = [t for t in frozen_tasks() if t["task_id"] not in PILOT_TASKS]
    total = len(outside) * len(arms()) * ATTEMPTS
    done = sum(k[0] not in PILOT_TASKS for k in rows())
    for step in PROGRESS:
        path = config.ROOT / "results" / f"progress_{step}.md"
        if done >= math.ceil(total * step / 100) and not path.exists():
            path.write_text(build(title=f"Main run progress: {step}% of the {total} attempts outside the pilot tasks"), encoding="utf-8")
            print(f"wrote {path.name}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", action="store_true", help="add CONFIG.md §6's analysis from results/grades.jsonl")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    text = build(with_scores=args.scores)
    if args.out:
        (config.ROOT / args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
