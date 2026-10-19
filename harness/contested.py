"""Contested gold after the main run (amendment 2026-10-19-05 §1.8). `make contested ARGS=...`.

    python -m harness.contested find                     pair the incorrect answers, write the arm-blind adjudication sheet
    python -m harness.contested rule <task_id> <cluster> gold_stands|answer_accepted|gold_replaced --reason "..." --sources URL [URL ...]

A task is contested when at least 3 of its 4 attempts, across both arms, converge on one non-gold answer: an incorrect answer
(an attempt graded incorrect that has an Exact Answer line) to which at least two other incorrect attempts of the task are
equivalent. Two answers are equivalent when they match after harness/grade.py's normalize(), or when both judges, shown the
question with one answer as the reference and the other as the candidate, say the candidate is correct. Pairs are cached in
results/contested/pairs.jsonl. Answers that decline to answer count like any other answer; the owner then rules gold_stands.

The sheet (results/contested/sheet.md) shows each contested task's question, frozen gold, the converged answer and how many of
the four attempts gave it, never an arm. The owner adjudicates against primary sources and logs one ruling per task:
- gold_stands: no grade changes.
- answer_accepted: the question admits the converged answer too; its attempts become correct.
- gold_replaced: the frozen gold is wrong; the converged answer's attempts become correct, and attempts graded correct against the
  frozen gold become incorrect.
A ruling is written to results/contested/adjudications.jsonl (published with the results) and, for every attempt whose grade
it changes, as a contested_gold decision in results/owner_decisions.jsonl. No task is dropped; reports give scores with the
gold as frozen and after adjudication.
"""
import argparse
import itertools
import json
import sys

from harness import grade, judge
from harness import net as harness_net
from pipeline import config, net

DIR = config.ROOT / "results" / "contested"
PAIRS = DIR / "pairs.jsonl"
SHEET = DIR / "sheet.md"
CLUSTERS = DIR / "clusters.jsonl"
ADJUDICATIONS = DIR / "adjudications.jsonl"
CONVERGED = 3
RULINGS = ("gold_stands", "answer_accepted", "gold_replaced")


def stamp():
    return {"created_at": net.utc_now(), "config_hash": config.config_hash(config.read()), "code_commit": config.code_commit()}


def by_task():
    out = {}
    for g in grade.rows(grade.GRADES):
        out.setdefault(g["task_id"], []).append(g)
    return out


def equivalent(client, cache, task, a, b):
    """Whether answers a and b (grade rows) are the same answer: normalize() equal, or both judges say so."""
    if grade.normalize(a["answer"]) == grade.normalize(b["answer"]):
        return True
    first, second = sorted((a, b), key=lambda g: g["answer_sha256"])
    key = (task["task_id"], first["answer_sha256"], second["answer_sha256"])
    if key not in cache:
        verdicts = {}
        for name, fn in judge.JUDGES.items():
            try:
                verdicts[name] = fn(client, task["question"], first["answer"], second["answer"], purpose=f"contested:{task['task_id']}")["verdict"]
            except judge.JudgeError as exc:
                raise SystemExit(f"{name} failed on {task['task_id']}: {exc}; rerun `find`") from None
        rec = {**stamp(), "stage": "contested", "task_id": key[0], "reference_sha256": key[1], "candidate_sha256": key[2], "verdicts": verdicts,
               "equivalent": all(v == "correct" for v in verdicts.values())}
        grade.append(PAIRS, rec)
        cache[key] = rec["equivalent"]
    return cache[key]


def find():
    DIR.mkdir(parents=True, exist_ok=True)
    tasks = {t["task_id"]: t for t in json.loads(grade.TASKS.read_text(encoding="utf-8"))["tasks"]}
    cache = {(p["task_id"], p["reference_sha256"], p["candidate_sha256"]): p["equivalent"] for p in grade.rows(PAIRS)}
    client = harness_net.client(timeout=120.0)
    clusters, lines = [], ["# Contested gold: adjudication sheet", "",
                           "PRIVATE. One block per task where at least 3 of 4 attempts converge on one non-gold answer (amendment 2026-10-19-05 §1.8). "
                           "No arm is shown. Adjudicate against primary sources, then log one ruling per task with `make contested ARGS=\"rule ...\"`.", ""]
    for task_id, graded in sorted(by_task().items()):
        if len(graded) < 4 or any(g["final"].startswith("pending") for g in graded):
            continue
        wrong = [g for g in graded if g["final_as_frozen"] == "incorrect" and g["answer"] is not None]
        if len(wrong) < CONVERGED:
            continue
        task = tasks[task_id]
        same = {(a["arm"], a["attempt"], b["arm"], b["attempt"]) for a, b in itertools.combinations(wrong, 2) if equivalent(client, cache, task, a, b)}
        linked = lambda a, b: a is b or (a["arm"], a["attempt"], b["arm"], b["attempt"]) in same or (b["arm"], b["attempt"], a["arm"], a["attempt"]) in same
        best = max(wrong, key=lambda pivot: (sum(linked(pivot, other) for other in wrong), pivot["answer_sha256"]))
        members = [g for g in wrong if linked(best, g)]
        if len(members) < CONVERGED:
            continue
        cluster = f"{task_id}:c1"
        clusters.append({**stamp(), "stage": "contested", "cluster": cluster, "task_id": task_id, "pivot_sha256": best["answer_sha256"],
                         "attempts": [{"arm": g["arm"], "attempt": g["attempt"], "answer_sha256": g["answer_sha256"]} for g in members]})
        lines += [f"## {task_id} (cluster `c1`, {len(members)} of {len(graded)} attempts)", "", task["question"], "",
                  f"**Gold as frozen:** {task['gold']}", "", f"**Converged answer:** {best['answer']}", ""]
        lines += [f"- also given as: {g['answer']}" for g in members if g is not best and g["answer"] != best["answer"]]
        lines.append("")
    CLUSTERS.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in clusters), encoding="utf-8")
    SHEET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(clusters)} contested tasks; sheet at {SHEET}")
    return 0


def rule(task_id, cluster, ruling, reason, sources):
    found = next((c for c in grade.rows(CLUSTERS) if c["cluster"] == f"{task_id}:{cluster}"), None)
    if found is None:
        raise SystemExit(f"no cluster {task_id}:{cluster}; run `find` first")
    if any(a["task_id"] == task_id for a in grade.rows(ADJUDICATIONS)):
        raise SystemExit(f"{task_id} already has a ruling; rulings are one per task and final")
    members = {(a["arm"], a["attempt"]) for a in found["attempts"]}
    changes = []
    for g in by_task()[task_id]:
        if ruling != "gold_stands" and (g["arm"], g["attempt"]) in members:
            changes.append((g, "correct"))
        elif ruling == "gold_replaced" and g["final_as_frozen"] == "correct":
            changes.append((g, "incorrect"))
    rec = {**stamp(), "stage": "contested", "task_id": task_id, "cluster": found["cluster"], "ruling": ruling, "reason": reason, "sources": sources,
           "changed": [{"arm": g["arm"], "attempt": g["attempt"], "to": verdict} for g, verdict in changes]}
    grade.append(ADJUDICATIONS, rec)
    for g, verdict in changes:
        grade.decide(task_id, g["arm"], g["attempt"], verdict, "contested_gold", f"{ruling}: {reason}")
    print(f"{task_id}: {ruling}, {len(changes)} grades change; run `make grade` to apply")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("find")
    r = sub.add_parser("rule")
    r.add_argument("task_id")
    r.add_argument("cluster")
    r.add_argument("ruling", choices=RULINGS)
    r.add_argument("--reason", required=True)
    r.add_argument("--sources", nargs="+", required=True)
    args = ap.parse_args()
    if args.command == "find":
        return find()
    return rule(args.task_id, args.cluster, args.ruling, args.reason, args.sources)


if __name__ == "__main__":
    sys.exit(main())
