"""CONFIG.md §1: canonical text, config_hash, stamp and derived window.

python3 -m pipeline.config           print sha256 of §1
python3 -m pipeline.config --stamp   write it into CONFIG.md's config_hash line
"""
import argparse
import datetime as dt
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "CONFIG.md"

STAMP_RE = re.compile(r"^config_hash: sha256:(\S+)$", re.M)
SECTION1_RE = re.compile(r"^## 1\.[^\n]*\n(.*?)(?=^## |\Z)", re.M | re.S)
PARAM_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s+=\s+(.+?)\s*$")
DERIVED_RE = re.compile(r"W_START = (\d{4}-\d{2}-\d{2}), W_END = (\d{4}-\d{2}-\d{2})")
COUNTS_RE = re.compile(r"^Drafting counts: (.+?) \(", re.M)


def read():
    return CONFIG_PATH.read_text(encoding="utf-8")


def section1(text):
    m = SECTION1_RE.search(text)
    if m is None:
        raise ValueError("CONFIG.md has no '## 1.' section")
    lines = [line.rstrip() for line in m.group(1).splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def config_hash(text):
    return hashlib.sha256(section1(text).encode("utf-8")).hexdigest()


def stamped_hash(text):
    m = STAMP_RE.search(text)
    return m.group(1) if m else None


def params(text):
    """NAME = value lines of §1, with trailing '# ...' comments dropped."""
    out = {}
    for line in section1(text).splitlines():
        m = PARAM_RE.match(line)
        if m:
            out[m.group(1)] = re.split(r"\s+#\s", m.group(2), maxsplit=1)[0].strip()
    return out


def listed(value):
    """['A1', 'A2'] from 'A1, A2' (BLOCKS, ARMS)."""
    return [item.strip() for item in value.split(",") if item.strip()]


def window(p):
    """(W_START, W_END) computed from FREEZE_DATE, both inclusive."""
    w_end = dt.date.fromisoformat(p["FREEZE_DATE"]) - dt.timedelta(days=14)
    return w_end - dt.timedelta(days=90), w_end


def derived(text):
    """(W_START, W_END) as written in §1, or None."""
    m = DERIVED_RE.search(section1(text))
    return (dt.date.fromisoformat(m.group(1)), dt.date.fromisoformat(m.group(2))) if m else None


def shares(spec):
    """[(name, number)] from 'facts 15 / exceptions 15 / ...'."""
    return [(name, int(n)) for name, n in re.findall(r"([A-Za-z][\w-]*) (\d+)", spec)]


def largest_remainder(weights, total):
    """Split total in proportion to [(name, weight)]; leftover units go to the largest remainders, ties in the order given."""
    whole = sum(w for _, w in weights)
    floors = [(name, total * w // whole, total * w % whole) for name, w in weights]
    bumped = set(sorted(range(len(floors)), key=lambda i: -floors[i][2])[: total - sum(f for _, f, _ in floors)])
    return [(name, f + (i in bumped)) for i, (name, f, _) in enumerate(floors)]


def drafting_counts(p):
    """TYPE_QUOTA as counts of the TARGET_B drafted total."""
    drafted = int(re.search(r"(\d+) drafted", p["TARGET_B"]).group(1))
    return largest_remainder(shares(p["TYPE_QUOTA"]), drafted)


def derived_counts(text):
    """The drafting counts as written in §1, or None."""
    m = COUNTS_RE.search(section1(text))
    return shares(m.group(1)) if m else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Print or stamp the sha256 of CONFIG.md §1.")
    ap.add_argument("--stamp", action="store_true", help="write the hash into CONFIG.md's config_hash line")
    args = ap.parse_args(argv)
    text = read()
    digest = config_hash(text)
    if args.stamp:
        new, n = STAMP_RE.subn(f"config_hash: sha256:{digest}", text, count=1)
        if n != 1:
            sys.exit("CONFIG.md has no 'config_hash: sha256:...' line to stamp")
        CONFIG_PATH.write_text(new, encoding="utf-8")
    print(digest)


if __name__ == "__main__":
    main()
