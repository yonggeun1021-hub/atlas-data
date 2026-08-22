# P10-02/P10-03 Opportunity Capture PIT Replay — 2026-07-22 → 2026-08-22

★ Status: this document reports a **replay audit (Atlas WBS P10-02/P10-03,
Shadow Audit)**, not a decision, and is **unrelated to P11 (Real Capital)**.
Nothing in it changes any P0/P5 rule, Stage, trade_proposal, or trading
authority. Every ruleset comparison below is a capital=0 shadow simulation.
All real orders remain Human Approval. See `replay/` for the code and
`evidence/audit/pit_replay/` for the machine-readable ledgers this document
summarizes.

## 0. Canonical sources consulted

Read in full via Notion before any design work began: **CIO Investment
Operating Doctrine**, **Atlas 1개월 운용 감사 — Signal-to-P&L Review**,
**Opportunity Capture Control Loop**.

## 0.1 CIO review history on this PR

- **Round 1 → CHANGES_REQUIRED** (survivorship bias, GATE_BLOCK
  misclassification, fabricated condition shortcuts, entry/signal timestamp
  divergence, un-deduplicated daily-row miss counts).
- **Round 2 → CHANGES_REQUIRED**, round 1's 5 fixes accepted. Found 5 more
  (crypto source-catalog contamination, condition 1/5/6 semantics, episode
  grouping rework, DATA_FAILURE/Miss separation, P11→P10 naming) -- all
  fixed.
- **Round 3 → CHANGES_REQUIRED**, round 2's 5 fixes accepted (crypto
  contamination removed, false GATE_BLOCK removed). Found a **confirmed
  lookahead bug in the core return calculation**, fixed in this revision:

### The confirmed bug and its fix

`hypothetical_entry_at` was being set to `signal_evaluation_at` -- the
trading date a trigger's finalized-close data was computed FROM, which is
frequently the PRIOR trading day relative to when the signal actually
became knowable (given the collector's own T-1 finalization lag). Concrete
case CIO cited: a signal evaluated against 2026-08-19's close but only
knowable on 2026-08-20 was graded as if entry happened AT 2026-08-19's
close -- using a price from before the information that would have
justified buying it even existed. **The previously-reported returns
005930 +9.49%, 000660 +12.73%, BTC +5.36% are retracted; they were
inflated by a day of look-back on the entry price.**

**Fix**: one single, uniform rule now applies to EVERY entry (Miss,
Defense, SIGNAL_MISS, GATE_BLOCK, ACTION_CONVERSION_FAILURE alike -- never
a different rule per category): `hypothetical_entry_at` = the first real
trading date in the committed series **strictly after**
`action_eligible_at` (== `decision_date`), priced at that date's **OPEN**
(never a same-day fill when only a daily bar exists, never any prior
day's price). If no such later trading date exists in the committed
evidence, the entry is `NOT_GRADABLE` -- never silently graded from an
earlier, already-realized price. `signal_evaluation_at` is retained ONLY
as diagnostic metadata; it plays no role in pricing. This invariant
(`hypothetical_entry_at > action_eligible_at`) is enforced structurally
inside `compute_forward_metrics()` itself (raises `AssertionError` if ever
violated), not merely asserted in one call site, and is proven against the
real end-to-end replay output by
`test_pit_replay_end_to_end.py::test_every_ok_graded_entry_has_hypothetical_entry_at_strictly_after_action_eligible_at`.

No "acceptable overstatement" framing survives anywhere in this codebase --
grepped and confirmed absent (`test_replay_forward_metrics.py`).

**Further round-4 fixes, also done:**

| # | Item | Fix |
|---|---|---|
| 5 | `config/universe.json` is the CURRENT watchlist, not a reconstructed historical PIT population | KOREA market's `kpi_population_status` is now `NOT_COMPUTABLE_NO_HISTORICAL_PIT_WATCHLIST_EVIDENCE`; the 6-ticker results are still shown but labeled `CURRENT_WATCHLIST_DIAGNOSTIC_COHORT`, never presented as PIT-eligible |
| 6 | Crypto taxonomy `effective_from` could in principle be backdated relative to real ratification | Directly verified against real `git log`/`git show` history of `config/crypto_breadth_exclusion_taxonomy.json` (all 4 commits) -- **no backdating found**: every record's `effective_from` matches the UTC calendar date of the commit that first introduced it. Now a permanent automated regression (`test_replay_asset_identity.py::EffectiveFromNeverBackdatedVsRealGitHistoryTests`), not just a one-time manual check |
| 7 | Blended 45.9% coverage figure risked being read as one performance KPI across incompatible populations | Tagged `SECONDARY_OPERATIONAL_METRIC_NOT_A_PERFORMANCE_KPI` with an explicit `blended_metric_warning`; every performance-shaped question must use `by_market` |

**Round 3's numbers (`Miss=5`, `Defense=3`, per-ticker returns including
005930 +9.49% / 000660 +12.73% / BTC +5.36%) are superseded and must not
be used.**

## 1. The headline structural finding (unchanged across all rounds)

**This repository's own committed evidence trail begins 2026-08-13.** 22 of
32 audit-window days have zero committed Atlas evidence. Reported via the
`Coverage Gap` KPI block (section 4), never blended into Miss/Defense.

## 2. Priority cases: BTC / 005930 / 000660 (corrected under fixed entry timing)

*(Machine-readable: `evidence/audit/pit_replay/signal_replay_ledger_priority_only.json`,
`opportunity_miss_episodes.json`, `defense_episodes.json`.)*

### BTC (population: dedicated collector, KPI-eligible)

- **1 Miss Episode**: `2026-08-20`, `ACTION_CONVERSION_FAILURE`, corrected
  forward return **+7.30%** (previously mis-reported +5.36% from a
  backdated entry). Real trail: `signal_evaluation_at` (the trigger's own
  finalized-close basis) = 2026-08-19; `action_eligible_at` = 2026-08-20;
  `hypothetical_entry_at` = 2026-08-21 (the first real trading day after
  the signal became knowable), entered at that day's real OPEN.
- **0 Defense Episodes** in this window.

### 005930 (market: KOREA, `CURRENT_WATCHLIST_DIAGNOSTIC_COHORT` -- see below)

- **1 Miss Episode**: `2026-08-19`, `ACTION_CONVERSION_FAILURE`, corrected
  forward return **+5.45%** (was mis-reported +9.49%). `hypothetical_entry_at`
  = 2026-08-20 at real open 257,000.
- **1 Defense Episode**: `2026-08-13`, avoided **−10.00%** (was −7.65%
  under the old, incorrectly-earlier entry timing).

### 000660 (market: KOREA, diagnostic cohort)

- **0 Miss Episodes** under the corrected entry timing (the prior +12.73%
  figure does not survive fixing the entry-price lookahead -- either the
  corrected forward return no longer clears the 5% materiality threshold
  or the entry became `NOT_GRADABLE`; see the raw per-day ledger for the
  exact mechanics).
- **1 Defense Episode**: `2026-08-13`, avoided **−11.50%** (was −5.84%).

**Both 005930 and 000660 results above are `CURRENT_WATCHLIST_DIAGNOSTIC_COHORT`
results, NOT an approved PIT-eligible-universe KPI** -- see section 5.

## 3. Full-population audit, corrected, by market

| Market | Entries | Miss Episodes | Defense Episodes | Auditable Coverage | KPI Status | Population Label |
|---|---|---|---|---|---|---|
| BTC | 32 | 1 | 0 | 9.4% | `OK` | `DEDICATED_COLLECTOR` |
| KOREA | 192 | 1 | 4 | 27.1% | `NOT_COMPUTABLE_NO_HISTORICAL_PIT_WATCHLIST_EVIDENCE` | `CURRENT_WATCHLIST_DIAGNOSTIC_COHORT` |
| CRYPTO | 92 | 2 | 0 | 97.8% | `NOT_COMPUTABLE_MOSTLY_PRE_2026_08_19` | `PIT_RATIFIED_ELIGIBLE_UNIVERSE` |

**108 previously-graded entries became `NOT_GRADABLE` under the corrected
entry-timing logic** (up from 0 under round 3's -- itself already
incorrect -- backdated-entry methodology): 3 each for the 6 KR tickers
(18), 2 each for BTC/ETH/SOL (6), 1 each for 84 further crypto pairs (84).
This is expected and correct: near the end of the audit window
(2026-08-20 → 08-22), there is frequently no committed trading date after
`decision_date` at all, so no honest entry point can be established.

**Only BTC's market carries an `OK` KPI status.** KOREA and CRYPTO are both
`NOT_COMPUTABLE`-flagged for different, real reasons (no historical PIT
watchlist reconstruction possible for KOREA; ratification timing for
CRYPTO) -- neither market's Miss/Defense counts above should be read as an
approved investment-performance number, only BTC's.

## 4. Coverage Gap KPI block (secondary metric only, per market never blended)

*(Machine-readable: `evidence/audit/pit_replay/coverage_gap.json` (overall,
secondary), `by_market.json` (per-market, primary reference).)*

Overall blended auditable coverage: **45.9%** -- tagged
`SECONDARY_OPERATIONAL_METRIC_NOT_A_PERFORMANCE_KPI` in the machine output;
BTC (9.4%) / KOREA (27.1%) / CRYPTO (97.8%) must never be summed or
compared directly as one performance figure, since their population
definitions are fundamentally different (a dedicated single-asset
collector; a current-watchlist diagnostic cohort; a real ratified
universe that is empty for most of the window).

## 5. Korea population status (corrected, round 4)

`config/universe.json`'s 6 tickers are Atlas's **current** watchlist, with
no committed evidence anywhere in this repo reconstructing what the
PIT-investable KR population, or Discovery/Candidate/Ready state, actually
was on 2026-07-22. The official KOREA Opportunity KPI is therefore
`NOT_COMPUTABLE_NO_HISTORICAL_PIT_WATCHLIST_EVIDENCE`. The 6-ticker Miss/
Defense results in sections 2-3 are still reported -- explicitly and only
as a `CURRENT_WATCHLIST_DIAGNOSTIC_COHORT`, never as an approved PIT
Opportunity Capture KPI.

## 6. Crypto taxonomy ratification-timing verification (round 4)

Directly verified via `git log`/`git show` against every one of the 4 real
commits that ever touched `config/crypto_breadth_exclusion_taxonomy.json`:
every `eligible_crypto` record's `effective_from` date matches (never
predates) the UTC calendar date of the commit that first introduced it.
**No backdating found.** This is now `test_replay_asset_identity.py::
EffectiveFromNeverBackdatedVsRealGitHistoryTests`, a permanent regression
rather than a one-time manual check.

## 7. Root-cause distribution (episode-level, corrected)

| Category | Episodes | Notes |
|---|---|---|
| ACTION_CONVERSION_FAILURE | 3 | BTC (1), 005930 (1), and one crypto pair -- all under corrected entry timing |
| SIGNAL_MISS / GATE_BLOCK / etc. | 0 | None survive materiality + corrected grading in this replay of the real evidence |
| DATA_FAILURE | *(excluded -- Coverage Gap, section 4)* | |

## 8. Keep / Change / Kill (re-derived, corrected)

Unchanged recommendations 1-6 from round 3 (P5 trade_proposal=None →
CHANGE; 30-day cadence → CHANGE; P0/P5 invariant → KEEP; evidence
retention gap → CHANGE; position-sizing data source → CHANGE; crypto
taxonomy ratification timing → CHANGE), all still grounded in the
corrected numbers above. **New (round 4)**: Korea historical-population
reconstruction — **CHANGE**. Recommend persisting a dated, committed
Discovery/Candidate/Ready + watchlist snapshot going forward so a future
audit of this kind can establish a genuine PIT KR population instead of
falling back to a current-watchlist diagnostic cohort.

## 9. Hard-constraint verification (re-verified after every round's fixes)

- **Zero lookahead, including the round-4 entry-timing fix**:
  `test/test_replay_lookahead_gate.py` (13 tests) +
  `test_pit_replay_end_to_end.py`'s
  `test_every_ok_graded_entry_has_hypothetical_entry_at_strictly_after_action_eligible_at`
  and `test_entries_with_no_forward_trading_date_are_not_gradable_not_silently_graded`,
  run against the real, round-4-corrected replay output.
- **Determinism**: two independent `run()` calls diff byte-identical
  across every ledger key.
- **Authority booleans unchanged**: re-verified after every fix.
- **No survivorship bias / no source-catalog contamination**: unchanged
  from round 3, re-verified.
- **Untouched Forward Alpha files**: re-verified via `git diff main --stat`
  before pushing this revision.

## 10. Known limitations (unchanged plus round-4 additions)

Only 4 of 7 doc trigger types implemented; no BTC ETF-flow dataset
committed; 22/32 window days have zero committed Atlas evidence; crypto
PIT-eligible universe is empty for all but the final ~4 days of the
window; **new**: KOREA has no reconstructable historical PIT population at
all (current-watchlist diagnostic only); **new**: near the end of the
audit window, most entries cannot be graded at all under the corrected
entry-timing rule because no committed trading date exists after
`decision_date` -- this replay reports that honestly as `NOT_GRADABLE`
rather than approximating an entry price.
