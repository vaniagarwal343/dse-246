# Cost and usage accounting

The published figures are **fixed-price estimates from recorded completed attempts**. They are not provider invoices, account-credit deductions or a measured CLI bill.

## Rates and formulas

Prices are fixed to the October 19, 2026 table in [amendment 02](amendments/2026-10-19-02.md#2-list-prices), which records the source URLs and contemporaneous promotion.

| Component | Rate |
|---|---:|
| Model uncached input | $10 / 1 million tokens |
| Model cached input | $1 / 1 million tokens |
| Model output | $50 / 1 million tokens |
| Exa search | $7 / 1,000 recorded result-returning calls |
| Exa fetch | $1 / 1,000 recorded result-returning calls |
| DeepSeek search | $0.75 / 1,000 recorded result-returning calls |
| DeepSeek fetch | $0.75 / 1,000 recorded result-returning calls |

```text
model_usd =
  ((input_tokens - cached_input_tokens) * 10
   + cached_input_tokens * 1
   + output_tokens * 50) / 1,000,000

exa_tools_usd =
  (successful_searches * 7 + successful_fetches * 1) / 1,000

deepseek_tools_usd =
  (successful_searches + successful_fetches) * 0.75 / 1,000

combined_usd = model_usd + tools_usd
per_attempt = combined_usd / completed_attempts
per_correct_attempt = combined_usd / correct_attempts
```

Cached input is already part of the reported input-token total. Reasoning tokens are a subset reported separately by the runtime and are not added again to output tokens. The estimator follows the original report's usage fields and does not independently reconstruct provider billing.

| Recorded main-run usage | Exa | DeepSeek |
|---|---:|---:|
| Input tokens, including cached | 205,990,852 | 408,154,055 |
| Cached input tokens | 182,816,768 | 372,556,928 |
| Output tokens | 949,105 | 1,254,185 |
| Reasoning tokens | 353,938 | 498,424 |
| Searches counted for cost | 10,568 | 14,626 |
| Fetches counted for cost | 1,985 | 1,813 |
| Model estimate, USD | 462.012858 | 791.237448 |
| Tool estimate, USD | 75.961000 | 12.329250 |
| Combined estimate, USD | 537.973858 | 803.566698 |

The tool estimator selects trace call entries containing `n` and a truthy `ok` flag, matching `harness/report.py::backend_calls`. Total calls and fetch-failure metrics use the attempt summary counters. These counters are not interchangeable.

In one staged attempt (`A1-042 / exa / attempt 1`), the summary records one failed fetch among three fetches, while all three fetch call entries carry `ok`. This preserves the historical behavior before the fetch-error parity fix described in [amendment 05 §2.1](amendments/2026-10-19-05.md#2-code). The report's cost calculation therefore includes 1,985 Exa fetches, not `2,040 − 56 = 1,984`. This snapshot reproduces the existing estimator rather than retroactively repricing the run.

## Scope

- One latest completed record per task, backend and attempt: 984 records, 492 per backend.
- Token usage, calls and estimated dollars come from each completed attempt's final try. Earlier failed retry costs, discarded pilots and smoke tests are excluded.
- Wall time is the recorded `wall_s`: elapsed time for the final try. Earlier failed tries and retry backoff are excluded.
- The ten staged A1 tasks are included in the main view. The summary also reports sensitivity excluding them.
- Per-attempt estimates divide by 492 in the pooled view. Cost per correct attempt divides by 297 for Exa and 234 for DeepSeek.
- Prices and denominators stay the same when computing dataset-specific views, with 250 attempts per backend for A1 and 242 for A2.

## Relationship to spending

The model ran through the owner's ChatGPT subscription. Its token estimate is an API equivalent, not an amount charged as a separate API bill. The run's recorded Codex credit draw is also not a dollar figure and is not allocated by backend.

Exa provided $1,000 in API credits. DeepSeek's then-current documentation recorded free Search and Fetch until October 31, 2026 at 16:00 UTC. The published tool estimates ignore those credits and promotions so the rate comparison remains explicit. Actual credit use can differ from these estimates because of promotions, grants and calls outside the completed final-try scope.

## Public data schema

`published/attempts.jsonl` contains only a fixed allowlist:

- Task ID, block, backend, attempt number and reviewed correctness.
- Whether review changed the grade, without answer text or a prior-key score table.
- Completion timestamp/status, recorded wall time, retry and limit flags.
- Input/cached/output/reasoning token counts.
- Total search/fetch counts, fetch failures and logged result-returning call counts.
- Recorded source-code commit and frozen configuration hash.

`published/reviewed-scores.json` aggregates the two attempt verdicts into 0, 1 or 2 correct attempts per task/backend. It is byte-identical to the research.site download used for this release.

`published/summary.json` is derived from those records. The offline verifier recomputes every aggregate and paired bootstrap interval, validates the field allowlists and verifies the artifact hashes. It cannot independently check private answer content or audit omitted logs.

To regenerate the data when both local source repositories are available:

```sh
python3 scripts/results.py \
  --private-root /path/to/private-benchmark \
  --site-scores /path/to/research-site/public/dse-246-results.json
```

The exporter reads only completed run records, corresponding final traces and grades. It never reads `.env`, exports tool arguments/results or includes questions, answers, account balances or transcripts.
