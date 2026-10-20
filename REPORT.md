# DSE-246 reviewed results

Snapshot date: October 20, 2026. This report presents the reviewed answer key used on [research.site](https://research.site/dse-246).

The main run contains 984 completed attempts: 246 tasks × two backends × two attempts. The fixed model is `gpt-6-astra`, with medium reasoning effort, running through Codex CLI `0.162.1`. Exa Search/Contents and DeepSeek Search/Fetch each provide the same two tools, `search` and `fetch`. The results compare the complete retrieval configurations under this harness.

## Accuracy

Accuracy averages the two attempts for each task, then averages tasks within a dataset. Pass@2 is the fraction of tasks answered correctly at least once. “One of two correct” is the fraction answered correctly in exactly one attempt.

| Dataset | Backend | Correct attempts | Accuracy | Pass@2 | One of two correct | Both correct |
|---|---|---:|---:|---:|---:|---:|
| A1 / LiveBrowseComp | Exa | 141 / 250 | 56.4% | 62.4% | 12.0% | 63 / 125 |
| A1 / LiveBrowseComp | DeepSeek | 98 / 250 | 39.2% | 48.0% | 17.6% | 38 / 125 |
| A2 / EvoBrowseComp-EN | Exa | 156 / 242 | 64.5% | 69.4% | 9.9% | 72 / 121 |
| A2 / EvoBrowseComp-EN | DeepSeek | 136 / 242 | 56.2% | 60.3% | 8.3% | 63 / 121 |

| Registered comparison | Exa − DeepSeek | 95% paired bootstrap interval | Decision |
|---|---:|---:|---|
| A1 | +17.2 points | +9.6 to +24.8 | Exa ahead |
| A2 | +8.3 points | +3.7 to +13.2 | Inconclusive |

Intervals use 10,000 paired resamples of tasks, seeded with `20261018`. The registered decision rule calls an absolute gap below 12 percentage points inconclusive. A2's interval excludes zero, but its gap remains below that threshold.

Across both datasets, Exa has 297 / 492 correct attempts (60.4%) and DeepSeek 234 / 492 (47.6%). This pooled summary is descriptive; the registered comparisons are per dataset.

## Pilot-overlap sensitivity

The first ten A1 tasks were used in the staged main-run start. The main analysis includes their 40 scored attempts; the two earlier discarded pilot runs remain excluded. Removing these ten tasks gives:

| Dataset | Tasks | Exa accuracy | DeepSeek accuracy | Difference | 95% paired CI | Decision |
|---|---:|---:|---:|---:|---:|---|
| A1 | 115 | 57.4% | 38.7% | +18.7 points | +10.9 to +27.0 | Exa ahead |
| A2 | 121 | 64.5% | 56.2% | +8.3 points | +3.7 to +13.2 | Inconclusive |

The exact excluded task IDs, pass@2, consistency and operating-cost sensitivity are in [summary.json](published/summary.json).

## Speed and operations

| Metric, both datasets | Exa | DeepSeek |
|---|---:|---:|
| Completed attempts | 492 / 492 | 492 / 492 |
| Attempts needing a retry | 1 | 3 |
| Timeouts | 0 | 0 |
| Missing required answer line | 0 | 0 |
| Attempts reaching the 60-call cap | 74 | 133 |
| Attempts reaching the input-token ceiling | 0 | 0 |
| Search calls | 10,568 | 14,626 |
| Fetch calls | 2,040 | 2,389 |
| Fetch failures | 56 / 2,040 (2.7%) | 576 / 2,389 (24.1%) |
| Clean recorded arm-isolation evidence | 492 / 492 | 492 / 492 |
| Median time per attempt | 87 seconds | 164 seconds |

| Dataset | Exa median | DeepSeek median |
|---|---:|---:|
| A1 | 125 seconds | 189 seconds |
| A2 | 64 seconds | 131 seconds |

Reaching the call cap prevents further tool calls; it does not automatically make the final answer incorrect. Fetch failures count tool responses, not failed whole-task attempts.

Attempts finished between `2026-10-19T19:35:26Z` and `2026-10-20T07:40:33Z`. A task's four attempts finished within three minutes of one another at the median, and 16 minutes at most.

## Estimated cost

| Both datasets, USD | Exa | DeepSeek |
|---|---:|---:|
| Model API equivalent | $462.01 | $791.24 |
| Search + fetch estimate | $75.96 | $12.33 |
| Combined estimate | $537.97 | $803.57 |
| Tool estimate / attempt | $0.154 | $0.025 |
| Combined estimate / attempt | $1.093 | $1.633 |
| Combined estimate / correct attempt | $1.81 | $3.43 |

Exa's tool estimate is higher. DeepSeek's larger model-token estimate reverses the ranking when the two components are combined. The model ran on a ChatGPT subscription, so its API-equivalent cost was not an API bill. Tool estimates exclude credits and promotions, including the DeepSeek launch promotion documented before the run. The estimates do not establish actual account deductions.

The exact rate table, formulas, successful-call counters, retry scope and one staged-call accounting discrepancy are documented in [ACCOUNTING.md](ACCOUNTING.md).

## Task selection and scoring

| Dataset | Upstream items | Previously used/excluded | Eligible pool | Selected | Closed-book drops | Retained |
|---|---:|---:|---:|---:|---:|---:|
| A1 / LiveBrowseComp | 335 | 85 | 250 | 150 | 25 | 125 |
| A2 / EvoBrowseComp-EN | 400 | 0 | 400 | 150 | 29 | 121 |

Selection was evenly spaced over each eligible pool sorted by upstream index. Each selected task received three closed-book attempts from the model under test. A task was dropped if either judge marked any sample correct. These are aging public datasets behind a closed-book filter, rather than a fresh-web task set. Upstream revisions and file hashes are pinned in [CONFIG.md](CONFIG.md).

The harness permits 60 total tool calls per attempt and estimates a 250,000-input-token ceiling per model request. Searches return at most ten results with 400 characters of text each; fetched pages are capped at 8,000 characters. The backend-specific request defaults and subsequent parity changes are recorded in amendments [02](amendments/2026-10-19-02.md) and [05](amendments/2026-10-19-05.md).

The final answer is extracted from the last `Exact Answer:` line. After normalization, exact matches are correct; other answers are evaluated by `jev-1.13.0` and `perplexity/sonar`, blinded and independently. Forty judge disagreements were resolved through an arm-blind sheet, with Claude Fable 5.1 acting as an independent third grader and the owner confirming decisions. The sheet was built with a one-off script, rather than committed code.

Under amendment 05's contested-answer rule, 81 tasks were reviewed. The owner retained 54 answer keys, replaced 13 and accepted an additional answer for 14. Research drafts and a third-grader review informed the owner's decisions. Thirty-one retained cases involved convergence on declining to answer, and cited the review sheet itself as the only source. No task was dropped; 104 attempt grades changed from incorrect to correct.

| Reviewed-key changes | A1 | A2 |
|---|---:|---:|
| Tasks changed | 5 | 22 |
| Answers replaced | 2 | 11 |
| Additional answers accepted | 3 | 11 |
| Additional correct Exa attempts | 10 | 43 |
| Additional correct DeepSeek attempts | 8 | 43 |

The asymmetry between datasets matters when interpreting the scores: most changed answer keys came from A2.

## Limitations and audit status

- This is one model, one reasoning setting and one harness over two public task pools. It does not measure every possible retrieval configuration or general model intelligence.
- Built-in model search and the planned fresh-task block were deferred. The scored arms both use search and fetch.
- The original closed-book filter misses some retained-task answers accepted by the reviewed key. Retention therefore does not establish that every accepted answer was unavailable to the model without browsing.
- The contested-answer rule and several harness adjustments were recorded after the 40 staged attempts, before the remaining 944 attempts. Pilot-exclusion sensitivity is reported above.
- Owner calibration ingestion remains incomplete. Judge agreement against that calibration set is not reported as completed.
- The source guard run passed network, funnel, evidence, plaintext and grading checks. Configuration and model guards stopped while parsing a truncated JSON line in the failed first try of `A1-165 / exa / attempt 1`. That failed retry log remains untouched and excluded from the public records. The public verifier independently checks the completed final-try data, but does not replace those source guards.
- Task salt, complete adjudication rationales with evidence, type/attribution tables and discarded-pilot answer text are not included in this snapshot. Question/answer text and raw transcripts remain private. The reviewed-key-only publication choice and outstanding items are recorded in [amendment 2026-10-20-01](amendments/2026-10-20-01.md).

## Disclosures and provenance

Exa provided $1,000 in API credits for research.site benchmarking. No relationship with DeepSeek or other funding is reported. The same results are reported regardless of outcome.

LiveBrowseComp is MIT-licensed. EvoBrowseComp-EN is CC-BY-NC-SA-4.0; its questions were synthesized by DeepSeek-V3.2 using Google search, not DeepSeek Search. This repository publishes task identifiers and verdicts without redistributing plaintext questions or answers.

The source report was generated at `2026-10-20T14:48:30Z` with code commit `751a020c024eada6b9b721b47fb251ef6776f008`. Configuration section 1 hashes to `fe8cd27b36d3fb4e51123b1fb653b915078d45689c8e8077cddc21882502d2fd`. The frozen task-manifest root hash remains `41815550ad7062bd6b164efb8e8bff2ba33c111adbddaf0f44b41c52f57b9e03`.
