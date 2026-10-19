"""Grading for the main run (PROTOCOL.md §4; amendments/2026-10-19-02.md §3). `make grade`; re-runnable at any time.

For each completed attempt the answer is its last `Exact Answer:` line; none is NO_ANSWER_LINE, graded incorrect. An answer
that matches gold after normalize() is correct without judges. Any other answer goes to both judges, blinded (question, gold
and answer only) and independent. If they agree, that is the verdict; if they disagree, the attempt waits in
results/owner_queue.jsonl until the owner's decision is logged in results/owner_decisions.jsonl. Verdicts are cached by
attempt and answer, so a rerun asks only for what is missing. results/grades.jsonl is rewritten on every run.
"""
import calendar
import datetime as dt
import decimal
import hashlib
import json
import re
import sys
import unicodedata

from harness import judge
from harness import net as harness_net
from pipeline import config, net

RUNS = config.ROOT / "results" / "main"
TASKS = config.ROOT / "tasks" / "tasks.json"
VERDICTS = config.ROOT / "results" / "verdicts.jsonl"
QUEUE = config.ROOT / "results" / "owner_queue.jsonl"
DECISIONS = config.ROOT / "results" / "owner_decisions.jsonl"
GRADES = config.ROOT / "results" / "grades.jsonl"

# NOTE(vani): month names come from the standard library; spelled out here, a gold that is a month name would trip plaintext_guard.
MONTHS = {name.casefold(): n for n, name in enumerate(calendar.month_name) if name}
MONTHS |= {name.casefold(): n for n, name in enumerate(calendar.month_abbr) if name} | {"sept": 9}
EDGE = "\"'“”‘’«»()[]{}<>*_`"
TRAILING = ".,;:!?"
NUMBER_RE = re.compile(r"[+-]?\d+(?:\.\d+)?%?")
THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
# NOTE(vani): a comma between digits with no space ("1,000") is a thousands separator, not a list separator.
LIST_SPLIT_RE = re.compile(r"\s*;\s*|,(?=\s)\s*|(?<!\d),\s*|,(?!\d)\s*|\s+(?:and|&)\s+")
DATE_FORMATS = (
    re.compile(r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"),
    re.compile(r"(?P<y>\d{4})-(?P<m>\d{2})"),
    re.compile(r"(?P<mon>[a-z]+)\.? (?P<d>\d{1,2})(?:st|nd|rd|th)?,? (?P<y>\d{4})"),
    re.compile(r"(?P<d>\d{1,2})(?:st|nd|rd|th)? (?P<mon>[a-z]+)\.?,? (?P<y>\d{4})"),
    re.compile(r"(?P<mon>[a-z]+)\.?,? (?P<y>\d{4})"),
)


def clean(text):
    """NFKC, case folded, inner whitespace collapsed, quotes, brackets and trailing sentence punctuation stripped from the ends."""
    out = " ".join(unicodedata.normalize("NFKC", text or "").casefold().split())
    while out and (out[0] in EDGE or out[-1] in EDGE + TRAILING):
        out = out[1:] if out[0] in EDGE else out[:-1]
        out = out.strip()
    return out


def number(text):
    if not NUMBER_RE.fullmatch(text):
        return None
    pct = text.endswith("%")
    value = decimal.Decimal(text.rstrip("%"))
    canon = format(value.normalize(), "f") if value != 0 else "0"
    return canon + ("%" if pct else "")


def date(text):
    for pattern in DATE_FORMATS:
        m = pattern.fullmatch(text)
        if not m:
            continue
        parts = m.groupdict()
        month = int(parts["m"]) if parts.get("m") else MONTHS.get(parts.get("mon", ""))
        if not month:
            continue
        try:
            if parts.get("d"):
                return dt.date(int(parts["y"]), month, int(parts["d"])).isoformat()
            return dt.date(int(parts["y"]), month, 1).isoformat()[:7]
        except ValueError:
            return None
    return None


def scalar(text):
    return number(text) or date(text) or text


def normalize(text):
    """The comparable form of an answer: a number, an ISO date, a sorted list of items, or the cleaned text."""
    text = THOUSANDS_RE.sub("", clean(text))
    whole = number(text) or date(text)
    if whole:
        return ("value", whole)
    items = [clean(item) for item in LIST_SPLIT_RE.split(text)]
    items = [scalar(item) for item in items if item]
    if len(items) >= 2:
        return ("list", tuple(sorted(items)))
    return ("value", text)


def exact_match(answer, gold):
    return answer is not None and normalize(answer) == normalize(gold)


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def append(path, rec):
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def answer_sha(answer):
    return hashlib.sha256((answer or "").encode("utf-8")).hexdigest()


def traces():
    return sorted(json.loads(p.read_text(encoding="utf-8")) for p in RUNS.glob("*/*/attempt_*/trace.json"))


def main():
    digest = config.config_hash(config.read())
    tasks = {t["task_id"]: t for t in json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]}
    client = harness_net.client(timeout=120.0)
    verdicts = {(v["task_id"], v["arm"], v["attempt"], v["model"], v["answer_sha256"]): v for v in rows(VERDICTS) if v.get("error") is None}
    decisions = {(d["task_id"], d["arm"], d["attempt"], d["answer_sha256"]): d for d in rows(DECISIONS)}
    queued = {(q["task_id"], q["arm"], q["attempt"], q["answer_sha256"]) for q in rows(QUEUE)}
    grades = []
    for tr in traces():
        if tr["status"] != "completed":
            continue
        task = tasks[tr["task_id"]]
        key = (tr["task_id"], tr["arm"], tr["attempt"])
        answer, sha = tr["exact_answer"], answer_sha(tr["exact_answer"])
        out = {"created_at": net.utc_now(), "config_hash": digest, "task_id": tr["task_id"], "block": task["block"], "arm": tr["arm"],
               "attempt": tr["attempt"], "answer": answer, "answer_sha256": sha, "judges": {}, "owner": None}
        if answer is None:
            out.update(method="NO_ANSWER_LINE", final="incorrect")
        elif exact_match(answer, task["gold"]):
            out.update(method="exact", final="correct")
        else:
            for name, fn in judge.JUDGES.items():
                if (*key, name, sha) not in verdicts:
                    rec = {"created_at": net.utc_now(), "config_hash": digest, "stage": "judge", "model": name, "task_id": tr["task_id"],
                           "arm": tr["arm"], "attempt": tr["attempt"], "answer_sha256": sha}
                    try:
                        v = fn(client, task["question"], task["gold"], answer, purpose=f"main:judge:{tr['task_id']}:{tr['arm']}:a{tr['attempt']}")
                        rec.update(verdict=v["verdict"], output=v["output"], prompt_sha256=v["prompt_sha256"], usage=v["usage"], error=None)
                    except judge.JudgeError as exc:
                        rec.update(verdict=None, error=str(exc)[:300])
                    append(VERDICTS, rec)
                    if rec["error"] is None:
                        verdicts[(*key, name, sha)] = rec
                found = verdicts.get((*key, name, sha))
                out["judges"][name] = {"verdict": found["verdict"], "output": found["output"]} if found else None
            said = [j["verdict"] for j in out["judges"].values() if j]
            if len(said) < len(judge.JUDGES):
                out.update(method="judges", final="pending: judge error, rerun")
            elif len(set(said)) == 1:
                out.update(method="judges", final=said[0])
            elif (*key, sha) in decisions:
                out.update(method="owner", final=decisions[(*key, sha)]["verdict"], owner=decisions[(*key, sha)]["verdict"])
            else:
                out.update(method="judges disagree", final="pending: owner")
                if (*key, sha) not in queued:
                    append(QUEUE, {"created_at": net.utc_now(), "config_hash": digest, "task_id": tr["task_id"], "arm": tr["arm"],
                                   "attempt": tr["attempt"], "answer_sha256": sha, "answer": answer, "judges": out["judges"]})
                    queued.add((*key, sha))
        grades.append(out)
    GRADES.write_text("".join(json.dumps(g, ensure_ascii=False) + "\n" for g in grades), encoding="utf-8")
    for arm in sorted({g["arm"] for g in grades}):
        mine = [g for g in grades if g["arm"] == arm]
        print(f"{arm}: {len(mine)} graded, {sum(g['final'] == 'correct' for g in mine)} correct, {sum(g['final'] == 'incorrect' for g in mine)} incorrect, "
              f"{sum(g['final'].startswith('pending') for g in mine)} pending, {sum(g['method'] == 'NO_ANSWER_LINE' for g in mine)} NO_ANSWER_LINE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
