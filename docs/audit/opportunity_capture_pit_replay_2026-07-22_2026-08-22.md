# P10-02/P10-03 Opportunity Capture PIT Replay — 2026-07-22 → 2026-08-22

★ Status: this document reports a **replay audit (Atlas WBS P10-02/P10-03,
Shadow Audit)**, not a decision, and is **unrelated to P11 (Real Capital)**.
Earlier commits on this PR branch used a "P11" label before the phase was
clarified in CIO review round 3 -- git history is not rewritten, but every
doc/output-artifact label from this revision forward says P10-02/P10-03 /
"P10 Audit". Nothing in this document changes any P0/P5 rule, Stage,
trade_proposal, or trading authority. Every ruleset comparison below is a
capital=0 shadow simulation. All real orders remain Human Approval. See
`replay/` for the code and `evidence/audit/pit_replay/` for the
machine-readable ledgers this document summarizes.

## 0. Canonical sources consulted

Read in full via Notion before any design work began: **CIO Investment
Operating Doctrine**, **Atlas 1개월 운용 감사 — Signal-to-P&L Review**,
**Opportunity Capture Control Loop**.

## 0.1 CIO review history on this PR

- **Round 1 → CHANGES_REQUIRED** (5 flaws: crypto survivorship bias in the
  first cut, GATE_BLOCK misclassification, fabricated condition shortcuts,
  entry/signal timestamp divergence, unde-duplicated daily-row miss counts).
- **Round 2 → still CHANGES_REQUIRED**, but round 1's 5 fixes accepted as
  correctly done. Round 2 found 5 FURTHER flaws, all fixed in this
  revision:

| # | Flaw (round 2) | Fix |
|---|---|---|
| 1 | Crypto "632" was a raw Kraken source catalog, not a confirmed investable/PIT-eligible universe -- dumping it all in just traded one bias for a different contamination | Split into `source_coverage_population` (632, coverage-only) vs. the real, ratified `point_in_time_eligible_universe` (`config/crypto_breadth_exclusion_taxonomy.json`, ~87 assets, evaluated per-date against real `effective_from`) |
| 2 | Conditions 1/5/6 still didn't mean what their labels claimed | Condition 1 now distinguishes `PASS_TACTICAL` (price/flow-structure trigger) from `PASS_FUNDAMENTAL` (real thesis/catalyst -- none implemented today); condition 5 renamed `stop_distance_pct` and `condition_5_position_sizing` is honestly always `NOT_EVALUATED` (no portfolio-sizing data exists anywhere); condition 6 split into `condition_6a_price_integrity` / `condition_6b_asset_identity_status` / `condition_6c_pit_availability` |
| 3 | Episode grouping (flat 4-day gap) both wrongly merged unrelated rallies and wrongly split continuous ones | Reworked to group on `(subject, trigger_family, real forward-window overlap)`, not a flat calendar gap; renamed field set (`episode_start_date`/`first_signal_date`/`first_action_eligible_date`/`episode_end_date`/`outcome_window_start`/`outcome_window_end`/`representative_forward_return_pct`); `first_signal_date` is `None` on SIGNAL_MISS/DATA_FAILURE episodes (nothing was actually detected) |
| 4 | DATA_FAILURE entries were counted as Opportunity Misses | Excluded entirely from Miss/Defense KPI numerator AND denominator (applied symmetrically to Defense too); reported instead via a new `Coverage Gap` KPI block (`replay/coverage_gap.py`) |
| 5 | "P11" naming collided with the real WBS (P11 = Real Capital) | Renamed to P10-02/P10-03 throughout docs/output labels (this section, PR title, `wbs_phase` field) |

**Round 2's numbers (`Miss=1,564`, `Defense=1,061`, `GATE_BLOCK=0` on a
632-pair population) are superseded and must not be used** -- see section 3
for the corrected numbers under the real PIT-eligible population.

## 1. The headline structural finding (unchanged across all rounds)

**This repository's own committed evidence trail begins 2026-08-13.**
22 of the 32 audit-window days have zero committed Atlas evidence of any
kind. This is now reported as a first-class `Coverage Gap` KPI (section 4),
not blended into the Opportunity Miss numbers.

## 2. Priority cases: BTC / 005930 / 000660 (by market, corrected)

*(Machine-readable: `evidence/audit/pit_replay/signal_replay_ledger_priority_only.json`,
`opportunity_miss_episodes.json`, `defense_episodes.json`,
`by_market.json`.)*

### BTC (market: BTC)

- 32 entries in the window; **auditable coverage 9.4%** (3/32 days --
  BTC's own dedicated collector only has committed evidence 2026-08-20
  through 08-22).
- **1 Miss Episode**: `2026-08-20 → 2026-08-21`, root cause
  `ACTION_CONVERSION_FAILURE` (not GATE_BLOCK -- round-1's retraction still
  holds), trigger family `PRICE_CONFIRMATION`, representative forward
  return **+5.36%**.
- **0 Defense Episodes** in this window.
- No BTC ETF-flow dataset is committed anywhere in this repo (see Coverage
  Gap, section 4) -- the canonical doc's own "8/8 브리핑 ETF 4거래일 순유입"
  claim could not be independently corroborated or refuted from repo
  evidence.

### 005930 (삼성전자) and 000660 (SK하이닉스) (market: KOREA)

- 192 entries in the window (6 KR tickers × 32 days); **auditable coverage
  27.1%** across the whole KR population.
- **005930**: 1 Miss Episode (`2026-08-20`, `SIGNAL_MISS`, +9.49%); 1
  Defense Episode (`2026-08-13 → 08-19`, avoided **−7.65%**).
- **000660**: 1 Miss Episode (`2026-08-20`, `SIGNAL_MISS`, +12.73%); 1
  Defense Episode (`2026-08-13 → 08-19`, avoided **−5.84%**).
- A third KR Defense Episode exists for `329180` (`2026-08-14 → 08-18`,
  avoided **−6.95%**) -- included here because the full-population audit
  (section 3) explicitly requires the same rules applied to every declared
  KR ticker, not only the two priority names.
- Both 005930 and 000660's `SIGNAL_MISS` classification on 2026-08-20 means
  none of this replay's 4 implemented trigger types fired that day despite
  a real, material forward move -- an engine-coverage gap (see section 6),
  not a rule-gate block.

## 3. Full-population audit (corrected under the real PIT-eligible universe)

- **KR declared universe**: unchanged from round 2 -- 6 tickers, all in
  `config/universe.json`, never contaminated by an ambiguous catalog
  problem (it was always a small, deliberately curated watchlist).
- **Crypto, corrected**: `source_coverage_population` = **632 pairs**
  (pure data-coverage metric, `population.crypto_source_coverage_population`
  in the machine output) vs. the real Opportunity KPI population
  `point_in_time_eligible_universe` = **≤87 assets, monotonically date-gated
  by real `effective_from`** (`config/crypto_breadth_exclusion_taxonomy.json`,
  `approval_status: RATIFIED`). Per that file's own real ratification
  history, this eligible set is **EMPTY for every decision_date before
  2026-08-19**, 3 assets 08-19→08-21, and the full ~86 (BTC excluded to
  avoid double-counting the dedicated BTC subject) only from 08-22 onward.
  **`kpi_population_status = NOT_COMPUTABLE_MOSTLY_PRE_2026_08_19`** for
  the CRYPTO market as a whole -- this is now explicit, not silently
  substituted with the full catalog.
- **Total signal-replay-ledger entries, corrected: 316** (was 20,448 under
  round 2's full-catalog population -- a ~65x reduction, entirely explained
  by using the real ratified universe instead of the raw source catalog).
- **Opportunity Miss Episodes, corrected: 5** (was 1,564 under round 2's
  DATA_FAILURE-inflated, full-catalog population): BTC 1, 005930 1, 000660
  1, ETH/USD 1, SOL/USD 1. Root causes: SIGNAL_MISS 2, ACTION_CONVERSION_FAILURE
  3, **GATE_BLOCK 0**.
- **Defense Episodes, corrected: 3** (was 1,061): 005930, 000660, 329180 --
  every one carrying the structural-zero-capital caveat.
- **DATA_FAILURE is no longer part of either number** -- see section 4.

## 4. Coverage Gap KPI block (new, deliverable per CIO review round 2 flaw 4)

*(Machine-readable: `evidence/audit/pit_replay/coverage_gap.json`,
per-market breakdown in `by_market.json`.)*

| | Overall | BTC | KOREA | CRYPTO |
|---|---|---|---|---|
| Total entries | 316 | 32 | 192 | 92 |
| Auditable entries | 145 | 3 | 52 | 90 |
| **Auditable Coverage** | **45.9%** | **9.4%** | **27.1%** | **97.8%** |
| Unauditable days | 30 of 32 | 29 | 30 | 1 |
| Unauditable subjects (entirely) | none | -- | -- | -- |

Missing evidence types (overall): (1) no committed Atlas evidence of any
kind before 2026-08-13; (2) no BTC ETF-flow dataset committed anywhere;
(3) no committed briefing TEXT output for any date (only raw collector
snapshots). CRYPTO's high auditable-coverage % is itself explained by its
tiny, date-gated real population (section 3) -- it is not evidence that
crypto is better-covered than BTC/KR, it is evidence that its real,
ratified population barely exists yet.

## 5. Root-cause distribution (episode-level, corrected, DATA_FAILURE excluded)

| Category | Episodes | Meaning here |
|---|---|---|
| SIGNAL_MISS | 2 | Live data existed; none of the 4 implemented trigger types fired |
| ACTION_CONVERSION_FAILURE | 3 | A real trigger existed but conditions 1-6 were not ALL real PASS |
| GATE_BLOCK | 0 | Reserved for conditions-1-6-all-real-PASS blocked only by condition 7 -- structurally unreachable today since condition 5 (position sizing) is always NOT_EVALUATED |
| UNIVERSE_MISS | 0 | Every subject scanned came from a declared/ratified population |
| DECISION_LATENCY | 0 | Not observed in this window at the >3-day threshold |
| NO_POSITION_RULE | 0 | No committed-evidence source for a genuinely-observed portfolio-level constraint |
| DATA_FAILURE | *(excluded -- see Coverage Gap, section 4)* | 171 unauditable entries reported separately |

## 6. Keep / Change / Kill (re-derived, corrected)

1. `decision/alpha_review.py`'s unconditional `trade_proposal=None` —
   **CHANGE** (unchanged rationale, now grounded in 3 ACTION_CONVERSION_FAILURE
   + 0 GATE_BLOCK episodes).
2. `decision/alpha_review.py`'s fixed 30-day cadence — **CHANGE** (unchanged).
3. P0/P5 authority invariant — **KEEP** (unchanged, re-verified).
4. Repo evidence retention gap — **CHANGE**, now grounded in the real
   Coverage Gap numbers (45.9% overall auditable coverage, section 4)
   rather than a miss-ledger side-effect.
5. **NEW**: Position sizing data source — **CHANGE**. Even a ratified
   Probe P5 Rule would have nothing real to size against; recommend a
   ratified Portfolio Constitution NAV/headroom feed as a co-requisite.
6. **NEW**: Crypto taxonomy ratification timing — **CHANGE**. The only
   real ratified crypto eligible-universe this repo has was ratified in
   the final 1-4 days of the audit window itself -- a genuinely PIT-honest
   Crypto Opportunity Capture Rate is `NOT_COMPUTABLE` for nearly the
   entire window, which is an operational gap, not a methodology choice.

## 7. Existing vs. proposed ruleset (corrected population, same conclusion)

Both sides still show **0% action conversion** -- but now for a more
complete, honestly-derived reason than round 2: condition 5 (position
sizing) is structurally always `NOT_EVALUATED` (no portfolio data source
exists at all), so `conditions_1_to_6_all_pass` can never be `True` today
regardless of trigger quality. The proposed ruleset's real, demonstrated
value remains diagnostic transparency (per-condition PASS/FAIL/
NOT_EVALUATED/NOT_COMPUTABLE detail across a REAL, ratified population),
not yet a materially higher conversion rate.

## 8. Hard-constraint verification (re-verified after every round's fixes)

- **Zero lookahead**: `test/test_replay_lookahead_gate.py` (13 tests) +
  `test_pit_replay_end_to_end.py`'s signal-anchored-entry sweep, run
  against the real, round-3-corrected replay output.
- **Determinism**: two independent `run()` calls diff byte-identical
  across every ledger key, including the new `coverage_gap`/`by_market`
  tables.
- **Authority booleans unchanged**: re-verified after every fix.
- **No survivorship bias, two senses now proven**: (a) the classifier's
  signature structurally excludes outcome fields; (b) the KPI population
  is never outcome-selected (round 2) AND is never an unclassified raw
  catalog either (round 3) -- `test_replay_no_survivorship_bias.py`.
- **Untouched Forward Alpha files**: re-verified via `git diff main --stat`
  before pushing this revision.

## 9. Known limitations (unchanged plus one new, honestly reported)

Only 4 of 7 doc trigger types implemented; no BTC ETF-flow dataset
committed; 22/32 window days have zero committed Atlas evidence at all.
**New**: the real crypto PIT-eligible universe is empty for all but the
final ~4 days of the window -- any crypto Opportunity Capture Rate
computed over this window is necessarily `NOT_COMPUTABLE`-dominated, and
this replay reports that honestly rather than substituting a larger,
uncertified population.
