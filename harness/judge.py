"""Grading (PROTOCOL.md §4): the answer is the study arm's last `Exact Answer:` line or every other system's last <answer> tag, and
each judge in CONFIG.md's JUDGE_MODELS says whether it is equivalent to gold.

A judge sees only the question, the gold and the extracted answer, never which system answered (blinded), and never the
other judge's verdict (independent).
"""
import hashlib
import json
import re
from pathlib import Path

from pipeline import env

ANSWER_RE = re.compile(r"<answer>(.*?)</answer>", re.S | re.I)
# NOTE(vani): models often bold or bullet the label ("**Exact Answer:** x", "- Exact Answer: x"), so leading markdown and emphasis around the colon are skipped.
EXACT_ANSWER_RE = re.compile(r"^[\s>*_#-]*exact answer[\s*_]*:[\s*_]*(.*?)[\s*_]*$", re.I | re.M)
VERDICT_RE = re.compile(r"<verdict>\s*(correct|incorrect)\s*</verdict>", re.I)
PROMPTS = Path(__file__).resolve().parent / "prompts"
JEV_MODEL = "jev-1.13.0"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
PERPLEXITY_MODEL = "perplexity/sonar"
PERPLEXITY_URL = "https://api.perplexity.ai/v1/agent"


class JudgeError(Exception):
    """A judge call failed or came back without a usable verdict. The message never carries keys or case text."""


def extract_answer(text):
    """The last <answer>…</answer> in text, stripped, or None when there is none (graded incorrect)."""
    found = ANSWER_RE.findall(text or "")
    return found[-1].strip() if found else None


def extract_exact_answer(text):
    """The rest of the last `Exact Answer:` line in a study arm's final text, trimmed, or None when there is none (NO_ANSWER_LINE)."""
    found = EXACT_ANSWER_RE.findall(text or "")
    return found[-1].strip() if found else None


def prompt(name):
    """(text, sha256) of a prompt file under harness/prompts/."""
    raw = (PROMPTS / name).read_bytes()
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


def reply(resp, who):
    if resp.status_code != 200:
        raise JudgeError(env.scrub(f"{who}: HTTP {resp.status_code} {resp.text[:300]}"))
    return resp.json()


def jev(client, question, gold, answer, *, purpose):
    """jev's verdict on one case: {"model", "verdict", "confidence", "prompt_sha256", "usage"}."""
    text, digest = prompt("judge_jev.json")
    body = {"model": JEV_MODEL, "state": {"question": question, "reference_answer": gold, "candidate_answer": answer},
            "questions": {"equivalent": {"type": "choice", **json.loads(text)}}}
    data = reply(client.request("POST", JEV_URL, purpose=purpose, model=JEV_MODEL, body=body,
                                headers={"Authorization": f"Bearer {env.get('TYPESAFE_API_KEY')}"}), "jev")
    choice = (data.get("answers") or {}).get("equivalent") or {}
    if data.get("model") != JEV_MODEL or choice.get("choice") not in ("correct", "incorrect"):
        raise JudgeError(f"jev: unexpected response (model {data.get('model')!r}, choice {choice.get('choice')!r})")
    return {"model": JEV_MODEL, "verdict": choice["choice"], "confidence": choice.get("confidence"), "prompt_sha256": digest,
            "output": {"choice": choice["choice"], "probabilities": choice.get("probabilities"), "confidence": choice.get("confidence")},
            "usage": data.get("usage")}


def perplexity(client, question, gold, answer, *, purpose):
    """perplexity/sonar's verdict on one case, with no tools; a response that shows any search is rejected."""
    instructions, digest = prompt("judge_perplexity.txt")
    case = f"Question:\n{question}\n\nReference answer:\n{gold}\n\nCandidate answer:\n{answer}"
    body = {"model": PERPLEXITY_MODEL, "instructions": instructions, "input": case, "tools": [], "tool_choice": "none",
            "max_output_tokens": 64, "temperature": 0}
    data = reply(client.request("POST", PERPLEXITY_URL, purpose=purpose, model=PERPLEXITY_MODEL, body=body,
                                headers={"Authorization": f"Bearer {env.get('PERPLEXITY_API_KEY')}"}), "perplexity")
    output = data.get("output") or []
    cost = (data.get("usage") or {}).get("cost") or {}
    # NOTE(vani): Perplexity documents no switch that turns sonar's search off, so each response has to show it did not search.
    if any(o.get("type") != "message" for o in output) or data.get("tool_calls_details") or cost.get("tool_calls_cost"):
        raise JudgeError("perplexity: the response shows a search or tool call; verdict rejected")
    text = "".join(part.get("text", "") for o in output for part in o.get("content") or () if part.get("type") == "output_text")
    found = VERDICT_RE.findall(text)
    if not found:
        raise JudgeError("perplexity: no <verdict> tag in the reply")
    return {"model": PERPLEXITY_MODEL, "verdict": found[-1].lower(), "prompt_sha256": digest, "output": text, "usage": data.get("usage")}


JUDGES = {JEV_MODEL: jev, PERPLEXITY_MODEL: perplexity}
