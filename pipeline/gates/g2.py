"""G2 closed-book gate for Block A, and the canary check (PROTOCOL.md §5, §8; CONFIG.md §3). `make gates`.

MODEL_UNDER_TEST answers every candidate SAMPLES times with no tools. Each sample with an <answer> tag goes to both judges,
and an item drops with G2_CLOSED_BOOK when either judge marks any of its samples correct. Samples and verdicts are appended
as they come back, so a rerun picks up where the last one stopped. Once every sample has its verdicts, the drops, near-miss
flags, G2 funnel rows, tasks/tasks.json and pipeline/g2_report.md are written, once.
"""
import concurrent.futures as cf
import hashlib
import json
import re
import sys
import threading
import unicodedata
from collections import Counter

from harness import arms, judge
from pipeline import blocks, config, env, net

SAMPLES = 3
WORKERS = 8
RESPONSES_URL = "https://api.openai.com/v1/responses"
PROMPTS = config.ROOT / "pipeline" / "gates" / "prompts"
CANDIDATES = config.ROOT / "tasks" / "candidates.json"
TASKS = config.ROOT / "tasks" / "tasks.json"
LBC_README = config.ROOT / "tasks" / "upstream" / "A1" / "README.md"
SAMPLES_LOG = config.ROOT / "pipeline" / "g2.jsonl"
VERDICTS_LOG = config.ROOT / "pipeline" / "g2_verdicts.jsonl"
DROPS = config.ROOT / "pipeline" / "drops.jsonl"
FLAGS = config.ROOT / "pipeline" / "flags.jsonl"
REPORT = config.ROOT / "pipeline" / "g2_report.md"
GUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
LOCK = threading.Lock()
LOCAL = threading.local()


def client():
    # NOTE(vani): one client per worker thread, with extra retries so rate limits slow the run down instead of failing samples.
    if not hasattr(LOCAL, "client"):
        LOCAL.client = net.client(timeout=300.0, retries=5)
    return LOCAL.client


def prompt(name):
    raw = (PROMPTS / name).read_bytes()
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


def append(path, rec):
    with LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest(recs, key):
    """The last error-free record for each key."""
    return {key(r): r for r in recs if r.get("error") is None}


def words(text):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text or "").casefold()))


def record(stage, model, **fields):
    return {"created_at": net.utc_now(), "config_hash": client().config_hash, "stage": stage, "model": model, **fields}


def failure(exc):
    return env.scrub(f"{type(exc).__name__}: {exc}")[:300]


def ask(model, text, purpose):
    """One closed-book MODEL_UNDER_TEST call at the study's reasoning effort: (output text, reasoning summary, usage, effort the API reports)."""
    body = {"model": model, "input": [{"role": "user", "content": text}], "store": False,
            "reasoning": {"effort": arms.REASONING_EFFORT, "summary": "auto"}}
    resp = client().request("POST", RESPONSES_URL, purpose=purpose, model=model, body=body,
                            headers={"Authorization": f"Bearer {env.get('OPENAI_API_KEY')}"}, timeout=300)
    if resp.status_code != 200:
        raise RuntimeError(env.scrub(f"OpenAI responses: HTTP {resp.status_code} {resp.text[:300]}"))
    data = resp.json()
    output = data.get("output") or []
    text_out = "".join(part.get("text", "") for o in output if o.get("type") == "message"
                       for part in o.get("content") or () if part.get("type") == "output_text")
    summary = "\n".join(s.get("text", "") for o in output if o.get("type") == "reasoning" for s in o.get("summary") or ())
    return text_out, summary, data.get("usage"), (data.get("reasoning") or {}).get("effort")


def run_sample(task, n, model, template, digest):
    rec = record("G2", model, kind="sample", task_id=task["task_id"], sample=n, prompt_sha256=digest)
    try:
        text, summary, usage, effort = ask(model, template.replace("{question}", task["question"]), f"G2:{task['task_id']}:s{n}")
        rec.update(output_text=text, reasoning_summary=summary, answer=judge.extract_answer(text), usage=usage, reasoning_effort=effort, error=None)
    except net.Refused:
        raise
    except Exception as exc:  # NOTE(vani): a failed sample is logged and asked again by the next run, never counted.
        rec.update(error=failure(exc))
    append(SAMPLES_LOG, rec)
    return rec


def run_verdict(task, n, answer, name):
    rec = record("judge", name, kind="G2", task_id=task["task_id"], sample=n)
    try:
        v = judge.JUDGES[name](client(), task["question"], task["gold"], answer, purpose=f"G2:judge:{task['task_id']}:s{n}")
        rec.update(verdict=v["verdict"], confidence=v.get("confidence"), prompt_sha256=v["prompt_sha256"], usage=v["usage"], error=None)
    except net.Refused:
        raise
    except Exception as exc:  # NOTE(vani): a failed verdict is logged and asked again by the next run, never counted.
        rec.update(verdict=None, error=failure(exc))
    append(VERDICTS_LOG, rec)
    return rec


def canary(model):
    """LiveBrowseComp's canary string cut at the middle of its GUID, sent once; the record says whether the reply holds the rest."""
    done = [r for r in records(SAMPLES_LOG) if r.get("kind") == "canary" and r.get("error") is None]
    if done:
        return done[-1]
    full = blocks.CANARY_RE.search(LBC_README.read_text(encoding="utf-8"))[0]
    guid = GUID_RE.search(full)
    cut = guid.start() + len(guid[0]) // 2
    template, digest = prompt("g2_canary.txt")
    rec = record("G2", model, kind="canary", prompt_sha256=digest, input=full[:cut])
    try:
        text, summary, usage, effort = ask(model, template.replace("{canary_first_half}", full[:cut]), "G2:canary")
        rest = full[cut:].lstrip("-")
        rec.update(output_text=text, reasoning_summary=summary, usage=usage, reasoning_effort=effort, completes_guid=rest in text, error=None)
    except net.Refused:
        raise
    except Exception as exc:
        rec.update(error=failure(exc))
    append(SAMPLES_LOG, rec)
    return rec


def run(fn, jobs, label):
    print(f"{label}: {len(jobs)} to ask", file=sys.stderr)
    failed = 0
    with cf.ThreadPoolExecutor(WORKERS) as pool:
        for i, fut in enumerate(cf.as_completed([pool.submit(fn, *job) for job in jobs]), 1):
            failed += fut.result().get("error") is not None
            if i % 50 == 0 or i == len(jobs):
                print(f"  {label} {i}/{len(jobs)}, {failed} failed", file=sys.stderr)


def usage_line(recs, what):
    totals = Counter()
    for r in recs:
        u = r.get("usage") or {}
        totals["calls"] += 1
        totals["input"] += u.get("input_tokens") or 0
        totals["output"] += u.get("output_tokens") or 0
        totals["reasoning"] += (u.get("output_tokens_details") or {}).get("reasoning_tokens") or 0
        totals["usd"] += ((u.get("cost") or {}).get("total_cost")) or 0
    line = f"{what}: {totals['calls']} calls, {totals['input']} input tokens, {totals['output']} output tokens"
    line += f" ({totals['reasoning']} reasoning)" if totals["reasoning"] else ""
    return line + (f", ${totals['usd']:.4f} as billed in the responses" if totals["usd"] else "")


def write_report(started, digest, model, stats, canary_rec, sample_recs, verdict_recs):
    template_sha = prompt("g2_closed_book.txt")[1]
    lines = ["# G2 report", "",
             f"G2 closed-book gate (`make gates`), {started} to {net.utc_now()}, config_hash `{digest[:12]}`. `{model}` with no tools, "
             f"{SAMPLES} samples per item, prompt sha256 `{template_sha[:12]}`. Judges: {', '.join(f'`{m}`' for m in judge.JUDGES)}. "
             f"Reasoning effort `{arms.REASONING_EFFORT}` sent on every request; the API reported "
             f"{', '.join(sorted({str(r.get('reasoning_effort')) for r in sample_recs if r.get('error') is None}))}. "
             "An item drops when either judge marks any of its samples correct.", "",
             "| Block | Items | Dropped (hit rate) | Survivors | Correct samples | Samples with no answer tag | Near-miss flags |",
             "|---|---|---|---|---|---|---|"]
    for block, st in stats.items():
        lines.append(f"| {block} | {st['items']} | {st['dropped']} ({st['dropped'] / st['items']:.1%}) | {st['items'] - st['dropped']} | "
                     f"{st['correct_samples']} / {st['samples']} | {st['no_answer']} | {st['near_miss']} |")
    lines += ["", "Judges on samples with an answer tag:", "",
              "| Block | Answered samples | " + " | ".join(f"`{m}` correct" for m in judge.JUDGES) + " | Judges agree |",
              "|---|---|" + "---|" * len(judge.JUDGES) + "---|"]
    for block, st in stats.items():
        lines.append(f"| {block} | {st['answered']} | " + " | ".join(str(st[f'correct:{m}']) for m in judge.JUDGES) + f" | {st['agree']} |")
    lines += ["", "## Canary check", "",
              f"`pipeline/gates/prompts/g2_canary.txt` (sha256 `{canary_rec['prompt_sha256'][:12]}`), filled with LiveBrowseComp's canary string "
              f"cut at the middle of its GUID: `{canary_rec['input']}`", "", "Verbatim completion:", "", "```", canary_rec["output_text"], "```", "",
              f"Holds the rest of the GUID: {'yes' if canary_rec['completes_guid'] else 'no'}.", "", "## Usage", "",
              "- " + usage_line([r for r in sample_recs if r.get("error") is None], f"`{model}`")]
    for m in judge.JUDGES:
        lines.append("- " + usage_line([r for r in verdict_recs if r.get("model") == m and r.get("error") is None], f"`{m}`"))
    failed = sum(r.get("error") is not None for r in sample_recs + verdict_recs)
    lines += [f"- Failed calls asked again: {failed}.", "", "Samples: `pipeline/g2.jsonl`. Verdicts: `pipeline/g2_verdicts.jsonl`. "
              "Drops: `pipeline/drops.jsonl`. Flags: `pipeline/flags.jsonl`.", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def finalize(p, cand, samples, verdicts, canary_rec, sample_recs, verdict_recs):
    model, digest, now = p["MODEL_UNDER_TEST"], config.config_hash(config.read()), net.utc_now()
    stats, survivors = {}, []
    for task in cand:
        tid, st = task["task_id"], stats.setdefault(task["block"], Counter())
        rows = [samples[(tid, n)] for n in range(1, SAMPLES + 1)]
        correct = []
        for n, s in enumerate(rows, 1):
            if s["answer"] is None:
                st["no_answer"] += 1
                continue
            said = {m: verdicts[(tid, n, m)]["verdict"] for m in judge.JUDGES}
            st["answered"] += 1
            st["agree"] += len(set(said.values())) == 1
            for m, v in said.items():
                st[f"correct:{m}"] += v == "correct"
            if "correct" in said.values():
                correct.append(n)
        st["items"] += 1
        st["samples"] += SAMPLES
        st["correct_samples"] += len(correct)
        if correct:
            st["dropped"] += 1
            append(DROPS, {"created_at": now, "config_hash": digest, "candidate_id": tid, "stage": "G2", "reason_code": "G2_CLOSED_BOOK",
                           "note": f"samples judged correct: {correct}", "model": model})
            continue
        survivors.append(task)
        gold = words(task["gold"])
        if gold and any(f" {gold} " in f" {words(s.get('output_text', '') + ' ' + s.get('reasoning_summary', ''))} " for s in rows):
            st["near_miss"] += 1
            append(FLAGS, {"created_at": now, "config_hash": digest, "candidate_id": tid, "stage": "G2", "flag": "G2_NEAR_MISS",
                           "note": "gold appears in a sample's output or reasoning summary; no sample judged correct", "model": model})
    blocks.funnel_rows([(f"{block}:G2", st["items"], st["items"] - st["dropped"]) for block, st in stats.items()], digest)
    TASKS.write_text(json.dumps({"canary": p["CANARY"], "config_hash": digest, "created_at": now, "tasks": survivors},
                                ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    write_report(min(r["created_at"] for r in sample_recs), digest, model, stats, canary_rec, sample_recs, verdict_recs)
    for block, st in stats.items():
        print(f"{block}: {st['dropped']}/{st['items']} dropped ({st['dropped'] / st['items']:.1%}), {st['items'] - st['dropped']} survive")


def main():
    p = config.params(config.read())
    model = p["MODEL_UNDER_TEST"]
    if TASKS.exists() or any(r.get("stage") == "G2" for r in records(DROPS)):
        raise SystemExit("G2 is already final (tasks/tasks.json or G2 drops exist); it is written once")
    cand = json.loads(CANDIDATES.read_text(encoding="utf-8"))["tasks"]
    by_id = {t["task_id"]: t for t in cand}
    template, digest = prompt("g2_closed_book.txt")
    if any(r.get("kind") == "sample" and r.get("prompt_sha256") != digest for r in records(SAMPLES_LOG)):
        raise SystemExit("pipeline/g2.jsonl holds samples asked with another prompt; G2 cannot mix prompts")

    canary_rec = canary(model)
    print(f"canary: {canary_rec.get('error') or repr(canary_rec['output_text'][:120])}", file=sys.stderr)
    samples = latest([r for r in records(SAMPLES_LOG) if r.get("kind") == "sample"], lambda r: (r["task_id"], r["sample"]))
    run(run_sample, [(t, n, model, template, digest) for t in cand for n in range(1, SAMPLES + 1) if (t["task_id"], n) not in samples], "samples")
    samples = latest([r for r in records(SAMPLES_LOG) if r.get("kind") == "sample"], lambda r: (r["task_id"], r["sample"]))
    verdicts = latest(records(VERDICTS_LOG), lambda r: (r["task_id"], r["sample"], r["model"]))
    run(run_verdict, [(by_id[tid], n, s["answer"], m) for (tid, n), s in sorted(samples.items()) if s["answer"] is not None
                      for m in judge.JUDGES if (tid, n, m) not in verdicts], "verdicts")

    sample_recs, verdict_recs = [r for r in records(SAMPLES_LOG) if r.get("kind") == "sample"], records(VERDICTS_LOG)
    samples = latest(sample_recs, lambda r: (r["task_id"], r["sample"]))
    verdicts = latest(verdict_recs, lambda r: (r["task_id"], r["sample"], r["model"]))
    missing = [(t["task_id"], n) for t in cand for n in range(1, SAMPLES + 1) if (t["task_id"], n) not in samples]
    missing += [(tid, n, m) for (tid, n), s in samples.items() if s["answer"] is not None for m in judge.JUDGES if (tid, n, m) not in verdicts]
    if missing or canary_rec.get("error"):
        print(f"incomplete: {len(missing)} samples or verdicts missing{', canary failed' if canary_rec.get('error') else ''}; run `make gates` again",
              file=sys.stderr)
        return 1
    finalize(p, cand, samples, verdicts, canary_rec, sample_recs, verdict_recs)
    print(f"wrote {TASKS.relative_to(config.ROOT)} and {REPORT.relative_to(config.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
