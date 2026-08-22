# Opportunity Capture PIT Replay — 2026-07-22 → 2026-08-22

★ Status: this document reports a **replay audit**, not a decision. Nothing
in it changes any P0/P5 rule, Stage, trade_proposal, or trading authority.
Every ruleset comparison below is a capital=0 shadow simulation. All real
orders remain Human Approval. See `replay/` for the code and
`evidence/audit/pit_replay/` for the machine-readable ledgers this document
summarizes.

## 0. Canonical sources consulted

Read in full via Notion before any design work began (not paraphrased from
memory): **CIO Investment Operating Doctrine**, **Atlas 1개월 운용 감사 —
Signal-to-P&L Review**, **Opportunity Capture Control Loop**.

## 0.1 CIO review response (PR #210, first submission → CHANGES_REQUIRED)

The first submission of this PR received a CIO review verdict of
**CHANGES_REQUIRED** identifying 5 real methodology flaws. All 5 are fixed
in this revision, on the same branch/PR. **The numbers in the first
submission (`GATE_BLOCK=6`, raw `Miss=389`, `Defense=389`,
`conditions-1-6-satisfied=2`) are superseded and must not be used.** This
section maps each flaw to its fix; the rest of this document reflects the
corrected methodology throughout.

| # | Flaw | Fix | Where |
|---|---|---|---|
| 1 | Crypto KPI population was the outcome-selected top/bottom-15 (survivorship bias upstream of the classifier) | KPI population is now the FULL committed breadth catalog (632 real pairs); the outcome-ranked table survives only as an explicitly-labeled `crypto_movers_descriptive_only` field, structurally excluded from ledger construction | `replay/run_pit_replay.py::load_all_series`, `test/test_replay_no_survivorship_bias.py` |
| 2 | `GATE_BLOCK` assigned whenever a trigger existed + condition 7 failed, without checking conditions 1-6 | `GATE_BLOCK` now requires `conditions_1_to_6_all_pass == True` AND condition 7 alone failing; anything less falls through to `ACTION_CONVERSION_FAILURE` | `replay/root_cause.py::classify`, `test/test_replay_root_cause_classifier.py::GateBlockNarrowingTests`, `test/test_pit_replay_end_to_end.py::EndToEndGateBlockNarrowingTests` |
| 3 | Conditions 1/5/6/7 were fabricated shortcuts (count-only, `cond3 and cond4`, hard-coded `True`, an invented sentinel string) | Real `PASS`/`FAIL`/`NOT_EVALUATED`/`NOT_COMPUTABLE` vocabulary; condition 5 does real max-loss arithmetic; condition 6 checks the series' own recorded `integrity_conflicts`; condition 7 checks a real `config/*_policy.json` + `approval_status=="RATIFIED"` file per this repo's own established convention (verified against `config/korea_leadership_policy.json`) | `replay/action_conversion_gate.py`, `test/test_replay_action_conversion_gate.py` |
| 4 | Forward-metrics entry price could diverge from the signal's own evaluation date, silently grading off an unknowable future price | `forward_metrics.compute_forward_metrics()` takes an explicit `entry_date`, always the signal's own `evaluation_date`; every entry carries `signal_evaluation_at`/`action_eligible_at`/`hypothetical_entry_at`/`entry_price_available_at`/`execution_assumption`; a signal-anchored entry whose price wasn't actually live-known is marked `NOT_GRADABLE`, never silently computed | `replay/forward_metrics.py`, `replay/signal_replay_ledger.py`, `test/test_replay_forward_metrics.py::SignalAnchoredEntryAlignmentTests`, `test/test_replay_lookahead_gate.py::test_signal_anchored_entries_never_grade_an_unknowable_price` |
| 5 | 389 "misses" were raw daily rows -- a 5-day rally counted 5 times | New `Opportunity Episode` dedup (`(subject, date-adjacency ≤4 days, same root_cause)`) collapses daily rows into episodes; episode counts, not daily-row counts, are the headline Miss/Defense KPI | `replay/opportunity_episode.py`, `replay/opportunity_miss_ledger.py::build_miss_episodes`, `replay/defense_ledger.py::build_defense_episodes`, `test/test_replay_opportunity_episode.py` |

## 1. The headline structural finding (unchanged by this review)

**This repository's own committed evidence trail begins 2026-08-13.**
22 of the 32 audit-window days have zero committed Atlas evidence of any
kind. Every replay entry in that sub-window is recorded with
`root_cause = DATA_FAILURE`, not fabricated. Two further real data-quality
findings, both handled transparently rather than silently:

- **Cross-snapshot revision (KRX)**: both `000660`'s and `005930`'s
  2026-08-14 closes disagree across committed snapshots (000660: 1,638,000
  vs 1,645,000; 005930: 268,000 vs 274,500 -- a real 2.4% discrepancy).
  Recorded in `PriceSeries.integrity_conflicts`, earliest capture kept as
  canonical. **This is also now a real, structural contributor to the
  corrected Condition-6 (PIT integrity) evaluation** -- see section 3.
- **Collector finalization lag**: BTC/crypto-breadth collectors both
  exclude "today" and finalize only through T-1 (verified from each
  snapshot's own `_manifest.json`). Every signal necessarily evaluates
  against the most recent finalized day; the real lag is recorded per entry
  as `evaluation_lag_days` / `action_eligible_at` vs `signal_evaluation_at`.

## 2. Priority cases: BTC / 005930 / 000660 (corrected episode counts)

*(Machine-readable: `evidence/audit/pit_replay/signal_replay_ledger_priority_only.json`,
`opportunity_miss_episodes.json`, `defense_episodes.json`, both filtered to
these three subjects.)*

**Priority Miss Episodes: 8** (was misleadingly presented as up to 30 raw
daily rows before dedup):

| Subject | Episode window | Root cause | Forward return | Delay (days) | Daily rows deduped |
|---|---|---|---|---|---|
| 000660 | 07-29 → 07-30 | DATA_FAILURE | +19.06% | 1 | 2 |
| 000660 | 08-06 → 08-12 | DATA_FAILURE | +6.56% | 6 | 7 |
| 000660 | 08-20 | SIGNAL_MISS | +12.73% | 0 | 1 |
| 005930 | 07-24 → 07-30 | DATA_FAILURE | +5.21% | 6 | 6 |
| 005930 | 08-06 → 08-12 | DATA_FAILURE | +16.27% | 6 | 6 |
| 005930 | 08-20 | SIGNAL_MISS | +9.49% | 0 | 1 |
| BTC | 08-14 → 08-19 | DATA_FAILURE | +10.01% | 5 | 6 |
| BTC | 08-20 → 08-21 | **ACTION_CONVERSION_FAILURE** | +5.36% | 1 | 2 |

**Priority Defense Episodes: 6:**

| Subject | Episode window | Avoided return | Delay (days) | Daily rows deduped |
|---|---|---|---|---|
| 000660 | 07-22 → 08-05 | −23.44% | 14 | 9 |
| 000660 | 08-13 | −5.84% | 0 | 1 |
| 000660 | 08-19 | −9.75% | 0 | 1 |
| 005930 | 07-22 → 08-02 | −19.96% | 11 | 6 |
| 005930 | 08-13 | −7.65% | 0 | 1 |
| 005930 | 08-19 | −7.82% | 0 | 1 |

**Critical correction from the first submission**: the BTC 2026-08-20
breakout entry was originally reported as `GATE_BLOCK` ("only blocked by
missing P5") -- under the corrected, real per-condition evaluation, its
Condition 2 (independent confirmation) is `FAIL` (only one trigger type,
PRICE_CONFIRMATION, fired that day; no second independent signal type
co-occurred), so `conditions_1_to_6_all_pass = False`, and the entry is
correctly `ACTION_CONVERSION_FAILURE`, not `GATE_BLOCK`. **The claim "BTC/
005930/000660 were only blocked by the missing P5 rule" from the first
submission is retracted.** The real, corrected finding is more nuanced:
zero entries anywhere in the full replay population reach a genuine,
all-six-conditions-real-PASS state today (see section 3) -- the binding
constraints are a mix of (a) this replay's trigger engine only implementing
4 of 7 doc-specified trigger types, making 2-type independent confirmation
rare, and (b) the real KRX 08-14 data revision contaminating the 10-day PIT
-integrity lookback window for KR names across a multi-week stretch.

## 3. Full-population audit (corrected)

- **Crypto KPI population**: now the full committed breadth catalog --
  **all 632 tracked pairs** (`crypto_kpi_population_size` in
  `replay_summary.json`), not a 30-pair outcome-selected sample. The old
  top/bottom-15 table still exists but only as
  `population.crypto_movers_descriptive_only`, explicitly excluded from
  every KPI computation (`test_replay_no_survivorship_bias.py` proves this
  structurally).
- **Total signal-replay-ledger entries**: 20,448 (7 KR/BTC subjects × 32
  days + 632 crypto pairs × 32 days). 1,950 had any real PIT data (9.5% --
  a direct measure of how evidence-starved this window is), 862 had ≥1 real
  detected trigger, 8 had independent (≥2 trigger type) confirmation.
- **GATE_BLOCK, corrected: 0** across the entire real replay population
  (episode-level; was mis-reported as 6 in the first submission). Zero
  entries anywhere satisfy the strict, real, all-six-conditions test. This
  is itself a finding: the honest current state of this engine's trigger
  coverage + this window's real KRX data-revision noise means "fully
  qualified except for P5 ratification" essentially never occurs yet at
  this narrow trigger-type coverage.
- **Opportunity Miss Episodes (headline KPI, deduplicated)**: **1,564**
  (root causes: DATA_FAILURE 1,036, SIGNAL_MISS 315, ACTION_CONVERSION_FAILURE
  213, GATE_BLOCK 0). Deduplication materially changes the picture: raw
  daily miss rows before dedup numbered in the low thousands across the
  full 632-pair population; grouping consecutive same-subject/same-cause
  days into one episode is what produces the 1,564 figure above.
- **Defense Episodes (headline KPI, deduplicated)**: **1,061**, every one
  carrying the structural-zero-capital caveat.
- No episode is simultaneously a miss and a defense for the same subject
  starting on the same day (`test_pit_replay_end_to_end.py`).

## 4. Root-cause distribution (episode-level, full population, corrected)

| Category | Episodes | Meaning here |
|---|---|---|
| DATA_FAILURE | 1,036 | No committed evidence exists for this date/subject (structural — 22/32 days) |
| SIGNAL_MISS | 315 | Live data existed; none of the 4 implemented trigger types fired |
| ACTION_CONVERSION_FAILURE | 213 | A real trigger existed but conditions 1-6 were not ALL real PASS (most commonly: only one trigger type present, or a PIT-integrity conflict in the lookback window) |
| GATE_BLOCK | 0 | Corrected -- see section 3. Reserved for the narrow case of a fully-qualified (all 6 conditions real PASS) candidate blocked only by the unratified P5 Probe Rule; no such episode exists in this window today |
| UNIVERSE_MISS | 0 | Every subject scanned came from the declared universe / full breadth catalog |
| DECISION_LATENCY | 0 | Not observed in this window at the >3-day threshold |
| NO_POSITION_RULE | 0 | This replay has no committed-evidence source for a genuinely-observed portfolio-level "no position" constraint (see `root_cause.py` docstring) -- reserved, not fired here |

## 5. Keep / Change / Kill (re-derived from corrected episode counts)

1. **`decision/alpha_review.py`: `trade_proposal` unconditionally `None`** —
   **CHANGE** (unchanged recommendation, now grounded in corrected counts:
   0 GATE_BLOCK + 213 ACTION_CONVERSION_FAILURE episodes). Recommend the
   CIO doctrine's own prescribed fix: a Probe-specific P5 Rule Slice with a
   real, bounded loss budget -- not removal of the gate. **Additional,
   corrected finding**: even once ratified, this alone would not convert
   the 213 ACTION_CONVERSION_FAILURE episodes into action -- most fail
   Condition 2 or 6, not just Condition 7. The trigger engine itself (only
   4 of 7 doc-specified types implemented) and the PIT-integrity pipeline
   (real KRX revision contamination) both need attention too.
2. **`decision/alpha_review.py`: fixed 30-day review cadence** — **CHANGE**
   (unchanged rationale).
3. **P0/P5 authority invariant** — **KEEP** (unchanged; still verified
   structurally holding after this revision's changes).
4. **Repo evidence retention (no committed evidence before 2026-08-13)** —
   **CHANGE** (unchanged; 1,036 of 1,564 miss episodes are DATA_FAILURE).
5. **NEW, from this review**: **CHANGE** -- the KR PIT-integrity lookback
   window is real-world contaminated by KRX's own 08-14 data revision
   (000660 and 005930 both affected) for an extended stretch. Recommend the
   collector layer persist a revision-aware "as-first-reported" vs
   "as-later-revised" distinction so PIT-integrity checks aren't
   structurally blocked by ordinary data-vendor revisions for weeks at a
   time.

## 6. Existing ruleset vs. proposed ruleset (corrected)

| | Existing | Proposed |
|---|---|---|
| Action Conversion Rate (of entries with ≥1 trigger, n=862) | **0.0%** | **0.0%** |
| Qualified pending only P5 ratification (`conditions_1_to_6_all_pass`) | n/a | **0 / 862 (0.0%)** |

Both the final action-conversion rate AND the "qualified pending
ratification" rate are 0% under the corrected methodology -- a materially
more conservative (and more honest) result than the first submission's
`qualified_pending_gate_ratification_count=2`. **The proposed ruleset's
demonstrated advantage over the existing one, today, is diagnostic
transparency (per-condition PASS/FAIL/NOT_EVALUATED/NOT_COMPUTABLE detail),
not yet a materially higher conversion rate** -- that will only become
visible once the trigger engine covers more of the doc's 7 trigger types
and the PIT-integrity pipeline handles ordinary data revisions gracefully.

## 7. Hard-constraint verification (all re-verified after this revision)

- **Zero lookahead**: `test/test_replay_lookahead_gate.py` (13 tests,
  including a new signal-anchored-entry-alignment sweep) plus
  `test/test_pit_replay_end_to_end.py`'s NOT_GRADABLE-enforcement tests,
  run against the real, corrected replay output.
- **Determinism**: two independent `run()` calls diff byte-identical across
  every ledger key, including the new episode/daily/ungradable tables.
- **Authority booleans unchanged**: unchanged from the first submission --
  re-verified after every fix in this revision.
- **No survivorship bias**: now proven at TWO levels -- (a) the classifier's
  signature structurally excludes outcome fields (unchanged), AND (b) the
  KPI population itself is never selected by outcome
  (`test_replay_no_survivorship_bias.py`, new in this revision, directly
  addressing flaw 1).
- **Untouched Forward Alpha files**: re-verified via `git diff main --stat`
  before pushing this revision -- see the PR description.

## 8. Known limitations (unchanged, still not papered over)

Only 4 of 7 doc trigger types implemented; no BTC ETF-flow dataset
committed; 22/32 window days have zero committed evidence; full per-pair
signal-replay treatment now covers ALL 632 crypto pairs (corrected from the
first submission's 30), removing that limitation entirely.
