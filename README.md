# DSE-246

GPT-6 Astra with **Exa Search + Contents** versus **DeepSeek Search + Fetch**, on 246 tasks from LiveBrowseComp and EvoBrowseComp-EN. Both backends provide search and fetch through the same tool interface. Each task has two independent attempts per backend.

[Interactive results on research.site](https://research.site/dse-246) · [Results report](REPORT.md) · [Cost accounting](ACCOUNTING.md)

| Reviewed answer key | Tasks | Exa accuracy | DeepSeek accuracy | Exa − DeepSeek | 95% paired CI |
|---|---:|---:|---:|---:|---:|
| A1 / LiveBrowseComp | 125 | 56.4% | 39.2% | +17.2 points | +9.6 to +24.8 |
| A2 / EvoBrowseComp-EN | 121 | 64.5% | 56.2% | +8.3 points | +3.7 to +13.2 |

A1 meets the registered 12-point decision threshold. A2 is inconclusive under that rule. These results compare retrieval backends with one fixed model and harness.

## Data and reproduction

- [Reviewed task scores](published/reviewed-scores.json): 246 task IDs with correct-attempt counts for each backend; identical to the research.site download.
- [Sanitized attempt records](published/attempts.jsonl): 984 reviewed verdicts, timing, usage, operational counters and provenance fields.
- [Aggregate summary](published/summary.json): scores, paired bootstrap intervals, pilot-exclusion sensitivity and cost/operational summaries.
- [Artifact hashes](published/manifest.json): SHA-256 digests for the result bundle and frozen inputs.
- [Offline verifier/exporter](scripts/results.py): regenerates every aggregate from the public attempt records using Python's standard library.

Verify the committed bundle with Python 3.11 or later:

```sh
python3 scripts/results.py
```

No API keys, network calls or private datasets are needed to verify the published aggregates. The public records contain no questions, answer text, prompts, retrieved pages or conversation transcripts.

## Protocol and publication status

The task manifest and configuration were frozen on October 19, 2026: [freeze release](https://github.com/vaniagarwal343/dse-246/releases/tag/freeze-2026-10-19), [CONFIG.md](CONFIG.md), [PROTOCOL.md](PROTOCOL.md), [TASKS.md](TASKS.md). Subsequent changes are recorded in [amendments/](amendments/).

This is a **reviewed-results snapshot**, with limitations documented in the [report](REPORT.md#limitations-and-audit-status) and [publication note](amendments/2026-10-20-01.md). Owner calibration ingestion remains incomplete. Two source guards stop on a truncated failed-retry log; the independently verified public records use completed final tries. This publication does not claim completion of every promised audit artifact or the full C7 checkpoint.

Exa provided $1,000 in API credits. The model ran through a ChatGPT subscription; model dollar figures are estimated API equivalents. Tool dollar figures use fixed list prices and exclude credits and promotions. See [cost accounting](ACCOUNTING.md) for the precise scope.
