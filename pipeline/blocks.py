"""Block A (PROTOCOL.md §8, CONFIG.md §1 and §4): fetch each upstream file at its pinned revision, decrypt it, select N items by even spacing. `make blocks`.

Writes tasks/upstream/<block>/ (the files as published), tasks/candidates.json (plaintext, PRIVATE), one record per block
in pipeline/blocks.jsonl (no plaintext) and each block's pool and select rows in pipeline/funnel.csv. Runs once.
"""
import base64
import csv
import hashlib
import json
import re
import sys

from pipeline import config, net

HF = "https://huggingface.co/datasets"
SPEC_RE = re.compile(r"(?P<n>\d+) items: (?P<repo>[\w.-]+/[\w.-]+)@(?P<rev>[0-9a-f]{40}) (?P<file>\S+) sha256:(?P<sha256>[0-9a-f]{64})$")
CANARY_RE = re.compile(r"BENCHMARK DATA SHOULD NEVER APPEAR AS PLAIN TEXT ONLINE\. canary GUID \S+")
UPSTREAM = config.ROOT / "tasks" / "upstream"
CANDIDATES = config.ROOT / "tasks" / "candidates.json"
MANIFEST = config.ROOT / "pipeline" / "blocks.jsonl"
FUNNEL = config.ROOT / "pipeline" / "funnel.csv"
FUNNEL_HEADER = ["stage", "in_count", "out_count", "timestamp", "config_hash"]


def spec(p, block):
    m = SPEC_RE.search(p[block])
    if m is None:
        raise SystemExit(f"CONFIG.md §1 {block} does not end in '<N> items: <repo>@<revision> <file> sha256:<hex>'")
    return {**m.groupdict(), "n": int(m["n"])}


def excluded(text, block):
    """The idx on CONFIG.md §4's '<block> excluded idx (<count>): ...' line, checked against its count; none if the line is absent."""
    m = re.search(rf"^{block} excluded idx \((\d+)\): ([\d, ]+)$", text, re.M)
    if m is None:
        return set()
    idx = {int(x) for x in m[2].split(",")}
    if len(idx) != int(m[1]):
        raise SystemExit(f"CONFIG.md §4 lists {len(idx)} distinct {block} idx but says {m[1]}")
    return idx


def fetch(client, s, block, name):
    """One file of the dataset at the pinned revision, kept under tasks/upstream/<block>/."""
    resp = client.request("GET", f"{HF}/{s['repo']}/resolve/{s['rev']}/{name}", purpose=f"blocks:{block}:download")
    resp.raise_for_status()
    path = UPSTREAM / block / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return resp.content


def xor(data, key):
    if len(key) < len(data):
        raise SystemExit("an upstream key is shorter than its ciphertext")
    return bytes(a ^ b for a, b in zip(data, key)).decode("utf-8")


def jsonl_rows(raw):
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]


def livebrowsecomp(client, s, block, raw):
    """BrowseComp's XOR scheme: the key is sha256 of the canary string, which the dataset README gives at the same revision."""
    m = CANARY_RE.search(fetch(client, s, block, "README.md").decode("utf-8"))
    if m is None:
        raise SystemExit(f"{block}: no canary string in the README at {s['rev']}")
    digest = hashlib.sha256(m[0].encode("utf-8")).digest()

    def decrypt(ciphertext):
        data = base64.b64decode(ciphertext)
        return xor(data, digest * (len(data) // len(digest) + 1))

    return [{"idx": int(r["idx"]), "question": decrypt(r["problem"]), "gold": decrypt(r["answer"])} for r in jsonl_rows(raw)]


def evobrowsecomp(client, s, block, raw):
    """Each field XORed with its own base64 key (data_decrypt.py at the same revision). The file has no id, so idx is the 0-based line number."""
    return [{"idx": n, "question": xor(base64.b64decode(r["question"]), base64.b64decode(r["question_key"])),
             "gold": xor(base64.b64decode(r["answer"]), base64.b64decode(r["answer_key"]))} for n, r in enumerate(jsonl_rows(raw))]


DECRYPT = {"Forival/LiveBrowseComp": livebrowsecomp, "Krystalan/EvoBrowseComp": evobrowsecomp}


def even(pool, n):
    """CONFIG.md §1 SELECTION: positions floor(k * L / n), k = 0 .. n-1, over the pool sorted by idx."""
    return [pool[k * len(pool) // n] for k in range(n)]


def funnel_rows(rows, digest):
    new = not FUNNEL.exists()
    with FUNNEL.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        if new:
            writer.writerow(FUNNEL_HEADER)
        writer.writerows([*row, net.utc_now(), digest] for row in rows)


def main():
    text = config.read()
    p = config.params(text)
    blocks = [b for b in config.listed(p["BLOCKS"]) if b != "B"]
    if CANDIDATES.exists() or (FUNNEL.exists() and any(f"\n{b}:pool," in FUNNEL.read_text(encoding="utf-8") for b in blocks)):
        raise SystemExit("Block A is already built (tasks/candidates.json or its funnel rows exist); it runs once")
    client = net.client(timeout=120.0)
    created = net.utc_now()
    tasks, rows, manifest = [], [], []
    for block in blocks:
        s = spec(p, block)
        raw = fetch(client, s, block, s["file"])
        if hashlib.sha256(raw).hexdigest() != s["sha256"]:
            raise SystemExit(f"{block}: {s['file']} at {s['rev']} does not match the sha256 that CONFIG.md §1 pins")
        items = {item["idx"]: item for item in DECRYPT[s["repo"]](client, s, block, raw)}
        ids = sorted(items)
        drop = excluded(text, block)
        if len(ids) != len(jsonl_rows(raw)) or not drop <= set(ids):
            raise SystemExit(f"{block}: duplicate idx upstream, or an excluded idx that is not upstream")
        pool = [i for i in ids if i not in drop]
        if len(pool) < s["n"]:
            raise SystemExit(f"{block}: pool of {len(pool)} is smaller than {s['n']}")
        chosen = even(pool, s["n"])
        for i in chosen:
            tasks.append({"task_id": f"{block}-{i:03d}", "block": block, "idx": i, "question": items[i]["question"], "gold": items[i]["gold"],
                          "upstream": f"{s['repo']}@{s['rev']} {s['file']}"})
        rows += [(f"{block}:pool", len(ids), len(pool)), (f"{block}:select", len(pool), len(chosen))]
        manifest.append({"created_at": created, "config_hash": client.config_hash, "model": None, "block": block, "repo": s["repo"],
                         "revision": s["rev"], "file": s["file"], "file_sha256": s["sha256"], "upstream_items": len(ids),
                         "excluded": len(drop), "pool": len(pool), "selected": chosen})
        print(f"{block}: {len(ids)} upstream, {len(drop)} excluded, pool {len(pool)}, selected {len(chosen)} (idx {chosen[0]} .. {chosen[-1]})")
    CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES.write_text(json.dumps({"canary": p["CANARY"], "config_hash": client.config_hash, "created_at": created, "tasks": tasks},
                                     ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with MANIFEST.open("a", encoding="utf-8") as fh:
        fh.writelines(json.dumps(rec) + "\n" for rec in manifest)
    funnel_rows(rows, client.config_hash)
    print(f"wrote {len(tasks)} candidates to {CANDIDATES.relative_to(config.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
