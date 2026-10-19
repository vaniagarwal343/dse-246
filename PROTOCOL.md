# Task Generation Protocol — Astra × {Exa, DeepSeek Search, built-in}

Working spec for the agent (Codex) that pulls seeds, drafts candidate tasks, builds evidence packets, and runs the automated gates. Human verification and freezing are done by the benchmark owner (Vani). This file is internal; reader-facing prose (README, CONFIG.md narrative, site copy) is written by the owner.

## 0. Non-negotiables

The agent MUST NOT:
- Call Exa, DeepSeek Search/Fetch, or Astra-with-tools at any stage. All web access in this pipeline goes through `PIPELINE_SEARCH` (see §1), with one exception: in G3 the `UNIQ_AGENTS` may use their own native search, never a study backend's, and those calls are logged like every other request. `PROBE` gets no such exception. Reason: if candidate discovery runs through a study backend, the set is pre-selected for that backend's index.
- Draft questions with the model under test. `DRAFT_MODEL` must not be Astra. If this Codex session is running Astra, switch the model for the drafting step and log the model ID in every packet.
- Record evidence from a search snippet. Every evidence item comes from a page the agent fetched and saved to disk.
- Write plaintext questions or answers anywhere in the public repo.
- Choose, reorder, or drop candidates for any reason other than the coded gates below. Every drop carries a reason code from §7.
- Verify gold. The agent assembles the evidence packet; the owner verifies.

`MODEL_UNDER_TEST` with tools is called only through `harness/net.py`, only on smoke and dev items, never on a Block A or B candidate before the main run.

Every stage appends to `pipeline/funnel.csv` (stage, in_count, out_count, timestamp, config_hash) and `pipeline/drops.jsonl` (candidate_id, stage, reason_code, note).

## 1. Parameters (frozen in CONFIG before Stage 1 runs)

| Param | Value | Notes |
|---|---|---|
| `FREEZE_DATE` | ____ | Day the task set is frozen and hashed. |
| `W_END` | `FREEZE_DATE − 14 days` | 14-day buffer so search indexes have caught up. Items newer than this measure crawl latency, not retrieval quality. |
| `W_START` | `W_END − 90 days` | 90-day window, same as LiveBrowseComp. |
| `ASTRA_CUTOFF` | ____ | Astra's published training cutoff. Require `W_START ≥ ASTRA_CUTOFF`. If unknown or later than `W_START`, record that the closed-book gate (G2) is the only recency defense. |
| `PIPELINE_SEARCH` | ____ | Serper (Google) or Brave + Jina Reader for fetch. Not a study backend. |
| `DRAFT_MODEL` | ____ | Not Astra. Pin exact model string. |
| `CB_MODELS` | Astra (study setting, no tools) + one non-OpenAI frontier model | For G2. Block A runs Astra alone (§8). |
| `UNIQ_AGENTS` | 3 search-capable systems, none of which is a study arm | e.g. Claude + native search, Gemini + native search, non-Astra GPT via `PIPELINE_SEARCH` scaffold. For G3. |
| `PROBE` | one search-capable system not in the study and not in `UNIQ_AGENTS` | For G4, on the `PIPELINE_SEARCH` scaffold, never native search. |
| `HEAT_MODEL` | ____ | Scores GDELT heat in Stage 1 (§2). |
| `STABILITY_MODEL` | ____ | Runs Stage 2 (§3). Never `DRAFT_MODEL`. |
| `HOP_MODEL` | ____ | Runs G5 (§5). Never `DRAFT_MODEL`. |
| `TAG_MODEL` | ____ | Tags Block A items (§8). Never selects or drops. |
| `JUDGE_MODELS` | ____ | Two judges, blinded and independent. Each judges equivalence to gold only (§4, **Answer**). |
| `RNG_SEED` | ____ | All sampling is seeded and reproducible. |
| `TARGET_B` | 60 survivors from 110 drafted | Adjust only before Stage 1. |
| `TYPE_QUOTA` | facts 15% / exceptions 15% / lists 25% / tables-footnotes 15% / distributed 30% | Applied at drafting (Stage 3), not after gates. Counts are these shares of 110 by largest remainder, ties broken in the order listed. |
| `SALT` | random 32 bytes, private | For hashing. |
| `CANARY` | ____ | BrowseComp-style canary string embedded in public files. |

`DRAFT_MODEL`, `PROBE`, and `UNIQ_AGENTS` are pairwise distinct systems. The drafter never probes.

## 2. Stage 1 — Seed pull (mechanical)

Stage 1 runs once, on `W_END` + 1 day. Pull 3× the drafting target (330 seeds) across the sources below; each source's share is its Seeds column, 330 split evenly by largest remainder with ties broken in table order. `TYPE_QUOTA` applies at drafting (§4). Filter by date field in `[W_START, W_END]`. Log raw count, in-window count, post-filter count per source.

Sources and long-tail filters (LBC Appendix A thresholds where they exist; a uniform news-mention filter elsewhere):

| Source | Seeds | API / date field | Long-tail filter | Primary gold use |
|---|---|---|---|---|
| CVE/NVD | 42 | NVD API, `published` | CVSS ≥ 9.0 OR single-product scope; English description; status not REJECTED | affected product/version, CWE, publish date, CNA references. NOT the CVSS score (can change after publication). |
| USGS earthquakes | 42 | FDSN event API, `time` | significance ≥ 600 OR depth < 10 km OR > 500 km; must have location text | magnitude, depth, place, felt reports, PAGER |
| GDELT | 41 | DOC 2.0 API, `seendate` | `HEAT_MODEL` heat score 2.0–4.0 (geographic reach, entity fame, impact); article > 150 chars; drop paywall/404/bot pages | local/regional events, named entities, quantities |
| RAWG | 41 | games API, `released` | ratings_count ≤ 10 and added ≤ 1 and no Metacritic score | developer/publisher, platform, release specifics |
| TheSportsDB | 41 | events API, `dateEvent` | finished status with scores; non-football bonus; attendance < 50,000 | scores, venue, scorers |
| Federal Register | 41 | FR API, `publication_date` | notices and rules with an explicit exception/exemption clause; news-mention filter | the clause, effective dates, thresholds, exempted parties |
| CourtListener | 41 | opinions API, `date_filed` | district/appellate; news-mention filter | holdings, named parties, dates, cited statutes |
| arXiv | 41 | OAI/API, `published` | news-mention filter; not on HF daily-papers top list | tables, ablation numbers, author affiliations, dataset sizes |

News-mention filter (uniform): run a `PIPELINE_SEARCH` news query on the seed's canonical name; drop if ≥ 3 hits from outlets in `pipeline/major_outlets.txt`. Log the hit count.

Stage 1 drops carry one code: `S1_WINDOW` (date field outside `[W_START, W_END]`), `S1_LONGTAIL` (fails the source's long-tail filter or the news-mention filter), `S1_INCOMPLETE` (lacks a field the filter or the gold needs, such as location text, an English description, or a readable article rather than a paywall/404/bot page).

Sample seeds per source with `RNG_SEED`. Store `pipeline/seeds.jsonl` with: seed_id, source, record_id, record_url, pull_timestamp, raw record JSON, filter scores.

## 3. Stage 2 — Answer-stability filter

Drop any seed whose candidate gold could change over the benchmark's life: standings, cumulative revenue, chart positions, open court cases, CVSS pending analysis, "latest" anything. Gold must be a fact fixed at publication in a primary document. Reason code `S2_UNSTABLE`. `STABILITY_MODEL` applies this filter; it is never `DRAFT_MODEL`.

## 4. Stage 3 — Candidate drafting (`DRAFT_MODEL`)

For each seed, draft at most one candidate. Hard rules:

**Difficulty by evidence requirement, not obfuscation.** Every candidate must require at least one of:
- (a) a second record from an independent primary source (distributed evidence);
- (b) a qualifier below the headline — an exception clause, footnote, unit, effective-date condition;
- (c) exhaustiveness — every X meeting condition Y in the window.
At least one clue must be anchored in-window, and the final answer must be an in-window fact.

**Phrasing.** Clear, bounded research questions. Do not name the answer entity or its aliases in the question. Vague-but-recoverable descriptors are fine; riddles are not.

**Answer.** Exactly one of: number (with unit and tolerance), ISO date, short string (with alias list), or list (set-equality, order-insensitive, per-element aliases). Write the `answer_spec` at draft time:
```json
{"type": "number", "value": 12, "unit": "USD millions", "tolerance_abs": 0.05,
 "accepted_forms": ["12", "12.0", "$12M"], "normalization": "strip_units,strip_commas,float"}
```

The study arms answer in lbc-30's format: their prompt is lbc-30's, byte for byte (CONFIG §3), and the grader takes the last `Exact Answer:` line of the final response, deterministically. That is the last line that begins, after any markdown emphasis or list marker, with `Exact Answer:` in any case; the answer is the rest of the line. A run with no such line is `NO_ANSWER_LINE`, the arms' equivalent of `NO_ANSWER_TAG`: graded incorrect and counted separately. Every other answering system (`CB_MODELS`, `PROBE`, `UNIQ_AGENTS`) ends with its final answer inside `<answer>…</answer>`, and the grader takes the last `<answer>` tag deterministically; a response with none is `NO_ANSWER_TAG`, graded incorrect and counted separately. The closed-book gate's `<answer>` prompt and the arms' prompt are separate, and CONFIG §3 records both by sha256. The answer is normalized by `answer_spec`; Block A items have none, so theirs is only whitespace-trimmed. Each of the `JUDGE_MODELS` only judges whether the normalized answer is equivalent to gold. It sees only the question, the gold and that answer (blinded), never the other judge's verdict (independent). The `JUDGE_MODELS` are calibrated against the owner's verdicts on 30 answers before the main run.

**Citation requirement.** `gold_domains`: the registrable domains of the primary sources. A study answer counts as *supported* only if correct AND cites at least one URL on a gold domain (or its archived copy).

**Evidence packet** (`tasks/private/<task_id>/packet.json`):
```
task_id, type_tag, seed_id, source, draft_model, draft_prompt_sha256, drafted_at,
question, gold, answer_spec, gold_domains,
hops: [{hop_n, claim, evidence_id, kind: structured | read}],
evidence: [{evidence_id, url, fetched_at, archive_path, archive_sha256, wayback_url, passage_locator, in_window: bool}],
must_fail_note: "which clue, if removed, makes the question unanswerable"
```
Every hop carries `kind`. `structured`: the hop's gold is a field in a machine-readable record and is re-verified programmatically at freeze (`make verify`). `read`: the owner verifies it against the archive in Stage 5.

Archive every evidence page as raw HTML + extracted text under `tasks/private/<task_id>/sources/`. Also submit each URL to the Wayback Machine and record the snapshot URL. With no Wayback keys, submissions use anonymous Save Page Now; a save that returns no snapshot URL leaves `wayback_url` null and is logged to `pipeline/wayback_failures.jsonl`.

Stop drafting when 110 candidates exist, in `TYPE_QUOTA` proportions. Candidate order is the drafting order and is never changed.

## 5. Stage 4 — Automated gates (in this order; cheapest and most eliminating first)

**G1 Leak test.** (i) Gold string and all `accepted_forms` absent from the question after normalization. (ii) Front-page test: run the question text and three trivial reformulations through `PIPELINE_SEARCH`; if the gold appears in any top-30 title or snippet, drop. Reason `G1_LEAK`.

**G2 Closed-book.** Each `CB_MODELS` model, no tools, 3 samples, forced short answer, the study's reasoning setting for Astra. Grade with the study grader; a sample is correct when either judge marks it correct. Any correct sample → drop (`G2_CLOSED_BOOK`). Log near-misses where the gold entity appears in reasoning without a correct final answer (`G2_NEAR_MISS`, kept but flagged for owner).

**G4 Difficulty probe.** `PROBE` on the `PIPELINE_SEARCH` scaffold, 3 attempts, under the study's call cap (CONFIG §3), so difficulty is measured under the tool budget the arms get and every call is counted in `pipeline/requests.log`. Drop 3/3 (`G4_TOO_EASY`). Keep 0–2/3. Items at 0/3 may be at most 40% of the block; if more, keep the earliest-drafted 0/3 items up to the cap and drop the rest (`G4_CAP`). This is applied by drafting order, never by content.

**G3 Uniqueness / multi-answer.** Each `UNIQ_AGENTS` system, 3 rollouts = 9 answers. If ≥ 80% converge on one non-gold answer → `G3_CONTESTED`, routed to owner adjudication against the archive (either the gold is wrong or the question admits two answers). If any rollout's answer satisfies every stated constraint and differs from gold → drop (`G3_MULTI`).

**G5 Hop necessity.** Remove the in-window clue named in `must_fail_note`; `HOP_MODEL` (never `DRAFT_MODEL`) re-reads the question and states whether it is still answerable from the remaining clues. If yes → `G5_HOP_NOT_NEEDED`, flagged for owner (owner may rewrite once; a rewritten item re-enters at G1).

All gate runs log: model/system ID, prompt hash, timestamp, raw outputs, grader verdict.

## 6. Stage 5 — Owner verification (human)

For every survivor:
1. Open the archived sources (not the live page). Confirm each `read` hop's claim at the locator; `structured` hops are re-verified programmatically at freeze (`make verify`), so this pass covers read hops only. Confirm gold and `answer_spec`.
2. Confirm the question is answerable, bounded, and has one reading.
3. Second pass ≥ 24 h later, against the archive, not against notes — or an independent verifier who did not see the first pass.
4. Record `verified_by`, `verified_at` (both passes) in the packet. Drop codes: `H_GOLD_WRONG`, `H_AMBIGUOUS`, `H_EVIDENCE_BROKEN`.

Budget: ~12 min per item for the first pass.

## 7. Reason codes

`S1_WINDOW`, `S1_LONGTAIL`, `S1_INCOMPLETE`, `S2_UNSTABLE`, `G1_LEAK`, `G2_CLOSED_BOOK`, `G3_MULTI`, `G3_CONTESTED→resolved/dropped`, `G4_TOO_EASY`, `G4_CAP`, `G5_HOP_NOT_NEEDED`, `H_GOLD_WRONG`, `H_AMBIGUOUS`, `H_EVIDENCE_BROKEN`. Nothing else.

## 8. Block A — ready-made pools

- A1, LiveBrowseComp: pool = all LBC idx 0–334 minus every idx used in LBC-30, Astra-26 block two, the 5-item difficulty pilot, and plumbing pilots, and the two example questions printed in the LBC paper (list them in CONFIG).
- A2, EvoBrowseComp-EN: pool = every item of the English file; an item's idx is its 0-based line number.
- Select 150 per block, evenly spaced over the pool sorted by idx: position ⌊k·L/150⌋ for k = 0…149, where L is the pool size. No content-based selection.
- Gates: G2 only (Astra ×3), then owner spot-check of 20 A1 items against LBC's reference answers. LBC gold is peer-reviewed upstream; log any disagreement.
- Canary check: Astra, no tools, gets LBC's canary string cut at the middle of its GUID; CONFIG records the verbatim completion.
- `TAG_MODEL` tags Block A items; a tag never selects or drops an item.
- Caveat recorded in CONFIG: A1 facts date from ~Feb–May 2026; this block is "aging public pool behind a closed-book gate," not fresh.

## 9. Stage 6 — Freeze and publish

Private repo: `tasks.json` (plaintext), `tasks/private/*`, `pipeline/*`.
Public repo at `FREEZE_DATE`:
- `TASKS.md`: task_id, block, `sha256(question || "\n" || gold || SALT)`. No plaintext. Its root hash, the sha256 of the file, is posted externally on `FREEZE_DATE`.
- `CONFIG.md`: every §1 parameter, filter thresholds, the full funnel with per-gate drop counts, model IDs for every gate, pre-registered predictions and the per-block "inconclusive" threshold, funder disclosure, `CANARY`.
- Block B: encrypted upstream snapshot (BrowseComp-style XOR with password) for later release. Block A ships none, because CONFIG §1 pins its upstream files by revision and sha256.
- Tag the freeze commit. Any later change is a dated amendment; none after seeing scores.

Dev/pilot items (for cap and grader calibration) are tagged `dev` at drafting and are never scored or pooled. They may use in-window records that already exist before Stage 1.

## 10. Funnel expectations (for planning, not targets)

Block B: 330 seeds → ~200 post-filter → 110 drafted → G1 −10% → G2 −12% → G4 −20% → G3 −8% → G5 −5% → human −15% → ~60 frozen.
If survivors fall below 50, draft from the remaining filtered seeds in seed order; do not relax any gate.

## Changelog

- 2026-10-18: Every hop in an evidence packet carries `kind: structured | read` (§4). Structured-hop gold is re-verified programmatically at freeze (`make verify`); the owner's Stage 5 pass covers read hops only (§6).
- 2026-10-18: `DRAFT_MODEL`, `PROBE`, and `UNIQ_AGENTS` are pairwise distinct systems. The drafter never probes (§1).
- 2026-10-18: `MODEL_UNDER_TEST` with tools is called only through `harness/net.py`, only on smoke and dev items, never on a Block A or B candidate before the main run (§0).
- 2026-10-18: Stage 1 runs once, on `W_END` + 1 day (§2). Dev items may use in-window records that already exist before Stage 1 (§9).
- 2026-10-18: TMDB and SEC EDGAR full-text removed from the §2 sources. Reason: no key (`TMDB_API_KEY` and `SEC_USER_AGENT` blank in .env). Per-source seed quotas are now explicit (Seeds column) and rebalanced over the eight remaining sources: 330 split evenly by largest remainder, ties broken in table order.
- 2026-10-18: In G3 the `UNIQ_AGENTS` may use their own native search, never a study backend's; those calls are logged like every other request (§0). `PROBE` runs on the `PIPELINE_SEARCH` scaffold under the study's call cap, not native search (§1, §5 G4).
- 2026-10-18: New model parameters: `HEAT_MODEL` scores GDELT heat (§2), `STABILITY_MODEL` runs Stage 2 (§3), `HOP_MODEL` runs G5 (§5). `STABILITY_MODEL` and `HOP_MODEL` are never `DRAFT_MODEL` (§1).
- 2026-10-18: Grading: final answers are extracted deterministically from the required `<answer>` tag and normalized by `answer_spec`. `JUDGE_MODEL` judges equivalence to gold only and is calibrated against the owner on 30 answers at C2 (§1, §4).
- 2026-10-18: `TYPE_QUOTA` counts use largest remainder, ties broken in the order listed (§1).
- 2026-10-18: Stage 1 reason codes `S1_WINDOW`, `S1_LONGTAIL`, `S1_INCOMPLETE` (§2, §7).
- 2026-10-18: Evidence items carry `wayback_url`. With no Wayback keys, saves use anonymous Save Page Now; a save that returns no snapshot URL leaves `wayback_url` null and is logged (§4).
- 2026-10-19: Scope of the first freeze (CONFIG.md §1). Arms: `MODEL_UNDER_TEST` + Exa Search/Contents and `MODEL_UNDER_TEST` + DeepSeek Search/Fetch. Blocks: A1 (LiveBrowseComp unused pool) and A2 (EvoBrowseComp-EN), 150 items each, evenly spaced over each sorted pool (§8). G2 runs Astra alone, 3 samples; a sample is correct when either judge says so (§5). The arms use lbc-30's prompt byte for byte and are graded from its last `Exact Answer:` line; G2 keeps its own `<answer>` prompt (§4). The arms run in the Codex CLI on the owner's ChatGPT subscription (CONFIG §3): the CLI reaches OpenAI itself, and the arms' backend calls go through `harness/net.py`. `JUDGE_MODELS`: jev and Perplexity sonar with search disabled, blinded and independent (§1, §4). `TAG_MODEL` tags Block A items (§1, §8). `TASKS.md` lists task_id, block and hash, and its root hash is the sha256 of the file (§9). Deferred to a later freeze: the built-in search arm, and Block B (§2–§5, §10) with its sources and models.
