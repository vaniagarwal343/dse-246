"""Owner calibration of the judges on the 40 staged answers (amendments/2026-10-19-02.md §3, 2026-10-19-05), blinded to arm.

    python -m harness.calibration sheet    write results/calibration/owner_sheet_40.md and its key
    python -m harness.calibration ingest   read the owner's Y/N into results/calibration/owner_verdicts.jsonl and report agreement

The sheet shows each staged task's question and gold, then its answers as [NN] codes ordered by the answer's sha256, so neither
a label nor a position names an arm or an attempt, and no judge verdict appears. The key (code to task, arm and attempt) is
results/calibration/owner_sheet_key.jsonl; the owner fills the sheet without opening it. Owner calibration verdicts never
change a grade; only a logged decision does (harness/grade.py).
"""
import json
import re
import sys

from harness import grade, report
from pipeline import config, net

DIR = config.ROOT / "results" / "calibration"
SHEET = DIR / "owner_sheet_40.md"
KEY = DIR / "owner_sheet_key.jsonl"
VERDICTS = DIR / "owner_verdicts.jsonl"
AGREEMENT = DIR / "agreement.md"
LINE_RE = re.compile(r"^- \[(\d{2})\] .*→\s+\*\*([YN?])\*\*\s*$")
MARKED_RE = re.compile(r"→\s+\*\*[YN]\*\*")


def staged():
    tasks = {t["task_id"]: t for t in json.loads(grade.TASKS.read_text(encoding="utf-8"))["tasks"]}
    by_task = {}
    for tr in grade.traces():
        if tr["task_id"] in report.PILOT_TASKS and tr["status"] == "completed":
            by_task.setdefault(tr["task_id"], []).append(tr)
    return tasks, by_task


def sheet():
    if SHEET.exists() and MARKED_RE.search(SHEET.read_text(encoding="utf-8")):
        raise SystemExit(f"{SHEET} already holds owner verdicts; ingest it, and move it aside before writing a new one")
    tasks, by_task = staged()
    stamp = {"config_hash": config.config_hash(config.read()), "code_commit": config.code_commit(), "stage": "calibration"}
    lines = ["# Owner calibration: 40 staged answers (amendments 2026-10-19-02 §3, 2026-10-19-05)", "",
             "PRIVATE. For each answer, replace `?` with `Y` (correct: same answer as the gold, at the specificity the question asks) "
             "or `N` (incorrect, including a hedge between candidates or no answer).",
             "Judge only against the gold below. Answers are coded and ordered so that nothing shows which arm or attempt gave them, "
             "and the judges' verdicts are not here. Do not open owner_sheet_key.jsonl until every answer is marked.", ""]
    keys, code = [], 0
    for task_id in sorted(by_task):
        task = tasks[task_id]
        lines += [f"## {task_id}", "", task["question"], "", f"**Gold:** {task['gold']}", ""]
        for tr in sorted(by_task[task_id], key=lambda t: (grade.answer_sha(t["exact_answer"]), t["arm"], t["attempt"])):
            code += 1
            shown = tr["exact_answer"] if tr["exact_answer"] is not None else "(no Exact Answer line)"
            lines.append(f"- [{code:02d}] {shown}  →  **?**")
            keys.append({"created_at": net.utc_now(), **stamp, "code": f"{code:02d}", "task_id": task_id, "arm": tr["arm"],
                         "attempt": tr["attempt"], "answer_sha256": grade.answer_sha(tr["exact_answer"])})
        lines.append("")
    DIR.mkdir(parents=True, exist_ok=True)
    SHEET.write_text("\n".join(lines), encoding="utf-8")
    KEY.write_text("".join(json.dumps(k) + "\n" for k in keys), encoding="utf-8")
    print(f"wrote {SHEET} ({code} answers) and its key")
    return 0


def rate(pairs):
    return f"{sum(a == b for a, b in pairs)}/{len(pairs)}" if pairs else "n/a"


def ingest():
    marks = {}
    for line in SHEET.read_text(encoding="utf-8").splitlines():
        m = LINE_RE.match(line)
        if m:
            marks[m.group(1)] = m.group(2)
    keys = {k["code"]: k for k in grade.rows(KEY)}
    blank = sorted(code for code in keys if marks.get(code, "?") == "?")
    if blank:
        raise SystemExit(f"unmarked answers: {', '.join(blank)}")
    stamp = {"config_hash": config.config_hash(config.read()), "code_commit": config.code_commit(), "stage": "calibration"}
    owner = {}
    out = []
    for code, k in sorted(keys.items()):
        verdict = "correct" if marks[code] == "Y" else "incorrect"
        owner[(k["task_id"], k["arm"], k["attempt"])] = verdict
        out.append({"created_at": net.utc_now(), **stamp, "code": code, "task_id": k["task_id"], "arm": k["arm"], "attempt": k["attempt"],
                    "answer_sha256": k["answer_sha256"], "verdict": verdict})
    VERDICTS.write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8")
    grades = {(g["task_id"], g["arm"], g["attempt"]): g for g in grade.rows(grade.GRADES)}
    judged = [(key, g) for key, g in grades.items() if key in owner and g["judges"]]
    lines = ["# Judge calibration against the owner", "", f"{len(out)} answers marked by the owner, blinded to arm and to the judges.", "",
             "| Comparison | Agreement |", "|---|---|"]
    for name in sorted({n for _, g in judged for n in g["judges"]}):
        lines.append(f"| {name} vs owner | {rate([(g['judges'][name]['verdict'], owner[key]) for key, g in judged if g['judges'].get(name)])} |")
    names = sorted({n for _, g in judged for n in g["judges"]})
    if len(names) == 2:
        lines.append(f"| {names[0]} vs {names[1]} | {rate([(g['judges'][names[0]]['verdict'], g['judges'][names[1]]['verdict']) for _, g in judged if all(g['judges'].get(n) for n in names)])} |")
    lines.append(f"| final grade vs owner (exact matches and judges) | {rate([(g['final'], owner[key]) for key, g in grades.items() if key in owner])} |")
    lines += ["", "Owner calibration verdicts never change a grade (amendment 2026-10-19-05)."]
    AGREEMENT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command not in ("sheet", "ingest"):
        raise SystemExit("usage: python -m harness.calibration sheet|ingest")
    sys.exit(sheet() if command == "sheet" else ingest())
