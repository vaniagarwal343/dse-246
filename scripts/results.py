"""Export and verify public DSE-246 results without network access or dependencies.

Run with --private-root and --site-scores to rebuild the sanitized data from the
private completed run. Run without arguments to verify the committed bundle.
"""
import argparse
import collections
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import random
import re
import statistics

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / "published"
ARMS = ("exa", "deepseek")
BLOCKS = ("A1", "A2")
SEED = 20261018
BOOTSTRAP = 10_000
PILOT_TASKS = (
    "A1-008", "A1-010", "A1-015", "A1-022", "A1-024",
    "A1-029", "A1-030", "A1-032", "A1-040", "A1-042",
)
CONFIG_HASH = "fe8cd27b36d3fb4e51123b1fb653b915078d45689c8e8077cddc21882502d2fd"
CODE_COMMIT = "751a020c024eada6b9b721b47fb251ef6776f008"
RECORD_FIELDS = {
    "task_id", "block", "backend", "attempt", "correct", "review_changed",
    "status", "completed_at", "wall_seconds", "retried", "timed_out",
    "cap_hit", "token_ceiling_hit", "no_answer_line", "isolated",
    "max_request_input_tokens", "usage", "calls", "code_commit", "config_hash",
}
USAGE_FIELDS = {"input", "cached_input", "output", "reasoning"}
CALL_FIELDS = {"searches", "fetches", "fetch_failures", "successful_searches", "successful_fetches"}


def json_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def task_scores(records):
    grouped = collections.defaultdict(lambda: {arm: 0 for arm in ARMS})
    for row in records:
        grouped[row["task_id"]][row["backend"]] += int(row["correct"])
    return [
        {"taskId": task_id, "block": task_id[:2], **grouped[task_id]}
        for task_id in sorted(grouped)
    ]


def score_block(tasks):
    n = len(tasks)
    correct = {arm: sum(row[arm] for row in tasks) for arm in ARMS}
    accuracy = {arm: correct[arm] / (2 * n) for arm in ARMS}
    differences = [(row["exa"] - row["deepseek"]) / 2 for row in tasks]
    rng = random.Random(SEED)
    samples = sorted(sum(rng.choice(differences) for _ in range(n)) / n for _ in range(BOOTSTRAP))
    gap = accuracy["exa"] - accuracy["deepseek"]
    return {
        "items": n,
        "attemptsPerArm": 2 * n,
        "correct": correct,
        "accuracy": accuracy,
        "passAtTwo": {arm: sum(row[arm] > 0 for row in tasks) / n for arm in ARMS},
        "halfCorrectRate": {arm: sum(row[arm] == 1 for row in tasks) / n for arm in ARMS},
        "bothCorrect": {arm: sum(row[arm] == 2 for row in tasks) for arm in ARMS},
        "gapPoints": round(100 * gap, 1),
        "gapCi95": [
            round(100 * samples[math.floor(.025 * BOOTSTRAP)], 1),
            round(100 * samples[math.ceil(.975 * BOOTSTRAP) - 1], 1),
        ],
        "verdict": "Inconclusive" if abs(gap) < .12 else ("Exa ahead" if gap > 0 else "DeepSeek ahead"),
    }


def operations(records, arm):
    rows = [row for row in records if row["backend"] == arm]
    n = len(rows)
    totals = {key: sum(row["usage"][key] for row in rows) for key in USAGE_FIELDS}
    calls = {key: sum(row["calls"][key] for row in rows) for key in CALL_FIELDS}
    model = round(((totals["input"] - totals["cached_input"]) * 10
                   + totals["cached_input"] + totals["output"] * 50) / 1_000_000, 6)
    tools = round((calls["successful_searches"] * (7 if arm == "exa" else .75)
                   + calls["successful_fetches"] * (1 if arm == "exa" else .75)) / 1000, 6)
    total = round(model + tools, 6)
    correct = sum(row["correct"] for row in rows)
    return {
        "attempts": n,
        "completed": sum(row["status"] == "completed" for row in rows),
        "retried": sum(row["retried"] for row in rows),
        "timeouts": sum(row["timed_out"] for row in rows),
        "noAnswerLine": sum(row["no_answer_line"] for row in rows),
        "capHitAttempts": sum(row["cap_hit"] for row in rows),
        "ceilingHitAttempts": sum(row["token_ceiling_hit"] for row in rows),
        "searches": calls["searches"],
        "fetches": calls["fetches"],
        "fetchFailures": calls["fetch_failures"],
        "fetchFailureRate": calls["fetch_failures"] / calls["fetches"],
        "successfulSearches": calls["successful_searches"],
        "successfulFetches": calls["successful_fetches"],
        "totalCalls": calls["searches"] + calls["fetches"],
        "isolatedAttempts": sum(row["isolated"] for row in rows),
        "medianSeconds": round(statistics.median(row["wall_seconds"] for row in rows)),
        "modelUsd": model,
        "toolsUsd": tools,
        "totalUsd": total,
        "meanToolCostUsd": tools / n,
        "meanTotalCostUsd": total / n,
        "costPerCorrectUsd": total / correct,
        "inputTokens": totals["input"],
        "cachedInputTokens": totals["cached_input"],
        "outputTokens": totals["output"],
        "reasoningTokens": totals["reasoning"],
        "largestRequestInputTokens": max(row["max_request_input_tokens"] for row in rows),
    }


def summarize(records):
    tasks = task_scores(records)
    nonpilot = [row for row in records if row["task_id"] not in PILOT_TASKS]
    task_spans = collections.defaultdict(list)
    for row in records:
        task_spans[row["task_id"]].append(datetime.fromisoformat(row["completed_at"].replace("Z", "+00:00")))
    spans = [(max(times) - min(times)).total_seconds() / 60 for times in task_spans.values()]
    return {
        "benchmark": "DSE-246",
        "scoring": "reviewed answer key",
        "updated": "2026-10-20",
        "configHash": CONFIG_HASH,
        "resultsCodeCommit": CODE_COMMIT,
        "bootstrapSamples": BOOTSTRAP,
        "bootstrapSeed": SEED,
        "decisionThresholdPoints": 12,
        "firstCompletedAt": min(row["completed_at"] for row in records),
        "lastCompletedAt": max(row["completed_at"] for row in records),
        "interleavingMedianMinutes": round(statistics.median(spans)),
        "interleavingMaxMinutes": round(max(spans)),
        "blocks": {block: score_block([row for row in tasks if row["block"] == block]) for block in BLOCKS},
        "pooled": {
            "items": len(tasks),
            "descriptiveOnly": True,
            "correct": {arm: sum(row[arm] for row in tasks) for arm in ARMS},
            "accuracy": {arm: sum(row[arm] for row in tasks) / (len(tasks) * 2) for arm in ARMS},
        },
        "operations": {arm: operations(records, arm) for arm in ARMS},
        "operationsByBlock": {
            block: {arm: operations([row for row in records if row["block"] == block], arm) for arm in ARMS}
            for block in BLOCKS
        },
        "pilotSensitivity": {
            "removedTaskIds": list(PILOT_TASKS),
            "blocks": {
                block: score_block([row for row in task_scores(nonpilot) if row["block"] == block])
                for block in BLOCKS
            },
            "operations": {arm: operations(nonpilot, arm) for arm in ARMS},
        },
        "review": {
            "changedAttemptGrades": sum(row["review_changed"] for row in records),
            "changedByBlock": {
                block: {
                    arm: sum(row["review_changed"] for row in records if row["block"] == block and row["backend"] == arm)
                    for arm in ARMS
                } for block in BLOCKS
            },
            "calibrationComplete": False,
        },
    }


def export(private_root, site_scores):
    latest = {}
    for row in json_lines(private_root / "results/main_runs.jsonl"):
        latest[(row["task_id"], row["arm"], row["attempt"])] = row
    grades = {
        (row["task_id"], row["arm"], row["attempt"]): row
        for row in json_lines(private_root / "results/grades.jsonl")
    }
    records = []
    for key, row in sorted(latest.items()):
        grade = grades[key]
        trace_path = private_root / "results/main" / key[0] / key[1] / f"attempt_{key[2]}" / "trace.json"
        trace = json.loads(trace_path.read_text())
        successful = [call for call in trace["tool_calls"] if "n" in call and call.get("ok")]
        assert grade["final"] in ("correct", "incorrect")
        assert row["config_hash"] == grade["config_hash"] == CONFIG_HASH
        records.append({
            "task_id": row["task_id"],
            "block": row["block"],
            "backend": row["arm"],
            "attempt": row["attempt"],
            "correct": grade["final"] == "correct",
            "review_changed": grade["final"] != grade.get("final_as_frozen", grade["final"]),
            "status": row["status"],
            "completed_at": row["created_at"],
            "wall_seconds": row["wall_s"],
            "retried": bool(row["retries"]),
            "timed_out": bool(trace.get("timed_out")),
            "cap_hit": row["summary"]["cap_hits"] > 0,
            "token_ceiling_hit": row["summary"]["ceiling_hits"] > 0,
            "no_answer_line": row["status"] == "completed" and row["exact_answer"] is None,
            "isolated": row.get("mcp_catalogs") == [["configured", 2]] and not row.get("foreign_items"),
            "max_request_input_tokens": row["max_request_input_tokens"],
            "usage": {
                "input": row["usage"]["input_tokens"],
                "cached_input": row["usage"].get("cached_input_tokens") or 0,
                "output": row["usage"]["output_tokens"],
                "reasoning": row["usage"].get("reasoning_output_tokens") or 0,
            },
            "calls": {
                "searches": row["summary"]["searches"],
                "fetches": row["summary"]["fetches"],
                "fetch_failures": row["summary"]["fetches_failed"],
                "successful_searches": sum(call["tool"] == "search" for call in successful),
                "successful_fetches": sum(call["tool"] == "fetch" for call in successful),
            },
            "code_commit": row["code_commit"],
            "config_hash": row["config_hash"],
        })
    site_bytes = site_scores.read_bytes()
    site = json.loads(site_bytes)
    assert task_scores(records) == site["tasks"], "Reviewed scores differ from research.site"
    PUBLISHED.mkdir(exist_ok=True)
    (PUBLISHED / "reviewed-scores.json").write_bytes(site_bytes)
    (PUBLISHED / "attempts.jsonl").write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in records))
    write_json(PUBLISHED / "summary.json", summarize(records))


def verify():
    records = json_lines(PUBLISHED / "attempts.jsonl")
    scores = json.loads((PUBLISHED / "reviewed-scores.json").read_text())
    summary = json.loads((PUBLISHED / "summary.json").read_text())
    assert len(records) == 984
    unique = {(row["task_id"], row["backend"], row["attempt"]) for row in records}
    assert len(unique) == len(records), "Duplicate attempt"
    assert len({row["task_id"] for row in records}) == 246
    for row in records:
        assert set(row) == RECORD_FIELDS
        assert set(row["usage"]) == USAGE_FIELDS and set(row["calls"]) == CALL_FIELDS
        assert row["backend"] in ARMS and row["block"] in BLOCKS
        assert re.fullmatch(r"A[12]-\d{3}", row["task_id"])
        assert re.fullmatch(r"[0-9a-f]{7,40}", row["code_commit"])
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", row["completed_at"])
        assert row["task_id"].startswith(row["block"] + "-")
        assert row["attempt"] in (1, 2)
        assert row["config_hash"] == CONFIG_HASH and row["status"] == "completed"
        for field in ("correct", "review_changed", "retried", "timed_out", "cap_hit", "token_ceiling_hit", "no_answer_line", "isolated"):
            assert type(row[field]) is bool
        assert row["wall_seconds"] >= 0
        assert all(type(value) is int and value >= 0 for value in row["usage"].values())
        assert all(type(value) is int and value >= 0 for value in row["calls"].values())
        assert row["usage"]["cached_input"] <= row["usage"]["input"]
        assert row["calls"]["successful_searches"] <= row["calls"]["searches"]
        assert row["calls"]["successful_fetches"] <= row["calls"]["fetches"]
    for task in scores["tasks"]:
        assert set(task) == {"taskId", "block", "exa", "deepseek"}
        assert all((task["taskId"], arm, attempt) in unique for arm in ARMS for attempt in (1, 2))
    assert scores["scoring"] == "reviewed answer key" and scores["configHash"] == CONFIG_HASH
    assert task_scores(records) == scores["tasks"], "Task totals differ from per-attempt verdicts"
    assert summarize(records) == summary, "Summary does not reproduce"
    assert summary["pooled"]["correct"] == {"exa": 297, "deepseek": 234}
    assert summary["blocks"]["A1"]["gapCi95"] == [9.6, 24.8]
    assert summary["blocks"]["A2"]["gapCi95"] == [3.7, 13.2]
    assert summary["review"]["changedAttemptGrades"] == 104
    manifest_path = PUBLISHED / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        for name, digest in manifest["sha256"].items():
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, f"Hash mismatch: {name}"
    print("PASS: 984 unique attempts, 246 tasks, reviewed scores, paired CIs, sensitivity, operations and cost accounting")
    print("PASS: strict public-field allowlists")
    if manifest_path.exists():
        print("PASS: artifact hashes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", type=Path)
    parser.add_argument("--site-scores", type=Path)
    args = parser.parse_args()
    if args.private_root or args.site_scores:
        if not (args.private_root and args.site_scores):
            parser.error("--private-root and --site-scores must be supplied together")
        export(args.private_root, args.site_scores)
    verify()


if __name__ == "__main__":
    main()
