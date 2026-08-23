# P7-11 Profit Harvesting Baseline Audit

**BASELINE AUDIT ONLY.** This is a diagnostic measurement layer over
PR #210 (P10-02/P10-03 Opportunity Capture PIT Replay)'s real committed
evidence -- it is NOT an operational Harvest Engine and NOT a sell-policy
ratification. No sell threshold, no liquidation rule, no real quantity, no
Trade Proposal, no order generation, and no Harvest action of any kind is
produced anywhere in this package.

## CIO methodology review round 1 (3 defects, fixed together)

An earlier version of this audit built its OFFICIAL population as
`build_miss_episodes ∪ build_defense_episodes` -- but Miss/Defense are
THEMSELVES outcome classifications (materially positive/negative FUTURE
return, `>=5.0%`/`<=-5.0%`). The claim "population fixed before computing
outcomes" was factually wrong: a real, contemporaneous Trigger on 298040
(2026-08-13, PRICE_CONFIRMATION) was silently excluded purely because its
future outcome never became a material Miss/Defense. Two further defects
were found in the same review. All 3 are fixed in this document/code;
see each section below for the specific fix and its dedicated regression.

## Design decision packet (AI 개발 실행 헌장, section 1)

- **투자 목적**: 이 기능은 "수익실현(harvest)" 그 자체를 자동화하지 않는다.
  실제 계약(contemporaneous Trigger)이 존재했던 episode들에 대해, "만약
  진입했다면 그 이후 가격 경로가 어떻게 전개되었는가"를 PIT-safe하게
  측정하는 감사 기반이다. 목적은 향후 P7-11 정책 설계(부분실현/위험회수/
  핵심물량유지/trailing 임계값)를 위한 실증 근거를 축적하는 것이며, 그
  자체로 매도 시점이나 임계값을 확정하지 않는다.
- **입력 정본**: `replay.run_pit_replay.load_all_series`/
  `build_signal_replay_ledger` (PR #210, 무수정 재사용),
  `replay.opportunity_episode.group_into_episodes` (무수정 재사용),
  `replay.signal_replay_ledger.classify_gap` (무수정 재사용),
  `replay.price_series.PriceSeries`. 원본 evidence: BTC Kraken OHLC, KRX
  daily, Crypto breadth Kraken OHLC -- `REPO_HISTORY_STARTS_AT=2026-08-13`
  이전은 구조적으로 `DATA_FAILURE`.
- **의미 계약**: 아래 "진단 카테고리" 절 참조.
- **권한 계약**: 진단(Diagnostic)만 허용. Review/Shadow/Buy/Order 권한
  전혀 없음. `policy_input_packet.json`은 미래 별도 CIO 설계결정의 INPUT일
  뿐 정책 자체가 아니다.
- **모집단** (round-1 defect 1 이후): 실 signal_ledger 316행 중, "당시
  실제로 알 수 있었던 사실"만으로 선별 -- 실제 contemporaneous Trigger
  존재(`entry["triggers"]` 비어있지 않음) AND gradable 진입점 존재
  (`forward_metrics.status=="OK"`, 순전히 데이터 가용성 사실이지 미래
  수익률의 크기/방향이 아님). 316행 → 211 gradable → 21 triggered+gradable
  → PIT episode grouping(`replay.opportunity_episode.group_into_episodes`
  무수정, 그룹핑 키는 subject+trigger_family+root_cause+실제 forward-window
  overlap이며 전부 미래 수익률과 무관) → 11개 PIT episode.
- **실패 정책**: `gain_path.status != "OK"` → `NOT_GRADABLE`. 시장별
  `kpi_population_status`는 PR #210의 정적 분류를 그대로 재사용(Korea/
  Crypto는 `NOT_COMPUTABLE_*`).
- **하류 소비자**: 없음. `evidence/audit/profit_harvest_baseline/`만
  생성하며, 어떤 운영 경로(`decision/`, `clock/`, `shadow/`, `briefing/`)도
  이 출력을 import하지 않는다 (구조적 테스트로 증명).
- **반례**: 사후 최고가 최적화, 미래정보 재주입, 결과기반 모집단 선택,
  미비준 임계값을 computability 판정처럼 사용 -- 전부 금지·테스트로 검증.

## Defect 1 fix -- the official population is now outcome-independent

`harvest_audit/population.py::build_trigger_population_records` selects
rows using ONLY `entry["triggers"]` (real, contemporaneous trigger
existence) and `forward_metrics.status=="OK"` (pure data availability) --
**never** future return magnitude or direction. `build_pit_episodes`
dedups these via PR #210's own unmodified `group_into_episodes` (grouping
key: subject + trigger_family + root_cause + real forward-window overlap
-- `root_cause` itself is PIT-safe, computed via `classify_gap()` from
`had_valid_trigger`/`conditions_1_to_6_all_pass`/`decision_latency_days`/
`gate_available`, never from a return figure).

Real counts (independently reproducible): **316 total rows → 211
forward-metric-gradable → 21 real-trigger+gradable → 11 PIT episodes**.
298040's real 2026-08-13 PRICE_CONFIRMATION trigger now correctly appears
in the official population (`test_profit_harvest_population.py::
RealTriggerGradablePopulationCountsTests::
test_298040_is_present_in_the_official_population_despite_no_material_future_outcome`).

**Post-hoc outcome labels** (attached ONLY after episode membership is
already fixed, via `_classify_outcome_category`, reusing the SAME single
already-ratified `MATERIALITY_THRESHOLD_PCT=5.0`/
`MATERIALITY_DRAWDOWN_THRESHOLD_PCT=-5.0` magnitude twice -- once against
`terminal_return_pct`, once against `peak_to_terminal_giveback_pct` --
never a new invented number):

| `outcome_category` | Meaning |
|---|---|
| `HARVEST_OPPORTUNITY` | Terminal return materially positive (`>=5.0%`) AND a material pullback from the peak actually occurred (`peak_to_terminal_giveback_pct <= -5.0%`) -- a real "should have harvested near the peak" case. |
| `HOLD_BENEFIT` | Terminal return materially positive AND no material pullback from the peak -- holding kept paying off. |
| `DEFENSE` | Terminal return materially negative (`<=-5.0%`). |
| `FLAT_NO_MATERIAL_OUTCOME` | Neither condition met. |
| `NOT_GRADABLE` | No real trading date exists strictly after the episode's action-eligible date. |

**Required outcome-independence proof**
(`test_profit_harvest_population.py::
PopulationMembershipIsOutcomeIndependentTests`): every entry's forward
returns/MFE/MAE across the whole signal ledger are artificially mutated
to absurd values (999%/-999%), and the resulting PIT-episode membership
(subject/start/end/trigger_family/root_cause set) is proven byte-identical
to the unmutated run.

**Reconciliation** (`evidence/audit/profit_harvest_baseline/
reconciliation.json`, `test_profit_harvest_population.py::
ReconciliationTests`): all 21 real-trigger+gradable rows map into exactly
one of the 11 PIT episodes; 0 unreconciled, structurally tested.

**Auxiliary cohort**: the OLD Miss ∪ Defense episode set (8 episodes) is
retained ONLY as `pr210_auxiliary_cohort.json` -- explicitly labeled
`pr210_category` (`MISS`/`DEFENSE`), structurally distinct from
`episode_ledger.json`, kept purely for comparison against PR #210's own
already-outcome-selected KPI framing
(`test_profit_harvest_population.py::
AuxiliaryCohortIsNotTheOfficialPopulationTests`).

## Defect 2 fix -- confirmed giveback never uses the MFE day's own low

The original `max_giveback_after_mfe_pct` included the MFE day's OWN low
(`>= mfe_date`) on the unproven assumption that a daily bar's high must
have occurred before its low. With daily OHLC bars there is NO way to
know that ordering -- if the low happened in the morning and the high in
the afternoon, that low is actually PRE-MFE, not post-MFE giveback.

Fixed (`harvest_audit/gain_path.py`): `max_giveback_after_mfe_confirmed_pct`
only ever starts counting from the FIRST REAL TRADING DAY STRICTLY AFTER
the MFE day. If no such day exists within the window,
`giveback_confirmed_status="NOT_COMPUTABLE_NO_TRADING_DAY_AFTER_MFE"` and
the pct field is `None` -- never fabricated. The identical fix is applied
symmetrically to `breakeven_after_positive_mfe_status`.

**Required regression**
(`test_profit_harvest_gain_path.py::NoGivebackBeforeMfeTests::
test_reproduces_and_fixes_the_low_before_high_same_day_bug`): a synthetic
MFE day constructed with "low occurs in the morning (20), high occurs in
the afternoon (200)" -- the OLD code would have computed a giveback of
`(20-200)/200*100 = -90.0%` (treating the pre-peak morning low as
post-MFE giveback). The fixed code provably never produces that number;
only the next real trading day (strictly after the MFE day) is used.

## Defect 3 fix -- unratified sample-size/horizon values never produce a verdict

The original scenario module used `MIN_SAMPLE_SIZE=5` and the 1/3/5-day
early-exit grid as if they were already-ratified computability criteria,
attaching `NOT_COMPUTABLE_INSUFFICIENT_SAMPLE` below 5 and an implicit
"OK" verdict (with an averaged opportunity-cost figure) above it. Neither
value is ratified -- choosing them IS itself an unratified policy
decision.

Fixed (`harvest_audit/scenario.py`): the grid is labeled
`ANALYTICAL_GRID_UNRATIFIED` everywhere. The real sample count is still
printed as a plain fact, but `aggregate_summary.status` is **ALWAYS**
`NOT_COMPUTABLE_POLICY_PARAMETERS_UNRATIFIED`, regardless of sample size
-- no averaged/aggregate figure of any kind is ever computed. Only the
raw, per-episode `comparisons` (real, already-happened numbers) are
provided for a future, ratified analysis to aggregate however that future
policy design decides.

**Required regression**
(`test_profit_harvest_end_to_end.py::
NoOptimalRecommendedActionableWordsAnywhereTests`): scans every string in
`policy_input_packet.json`, prose included, and proves none of "optimal",
"recommended", or "actionable" ever appears; a companion test proves the
aggregate status is the unratified string even for an artificially
inflated (far-above-5) sample size.

## Reuse boundary

Every episode's underlying trigger detection, action-conversion gate,
root-cause classification, materiality threshold, and episode-
deduplication logic is `replay/`'s own, **completely unmodified**:

- `replay/run_pit_replay.py::load_all_series`/`build_signal_replay_ledger`/
  `market_of`/`PRIORITY_SUBJECTS`
- `replay/opportunity_episode.py::group_into_episodes`
- `replay/signal_replay_ledger.py::classify_gap`
- `replay/opportunity_miss_ledger.py::MATERIALITY_THRESHOLD_PCT`,
  `replay/defense_ledger.py::MATERIALITY_DRAWDOWN_THRESHOLD_PCT` (reused
  as constants, for the post-hoc `outcome_category` label only)
- `replay/coverage_gap.py::build_coverage_gap_report`
- `replay/price_series.py::PriceSeries`

`harvest_audit/` is purely additive: it selects a PIT-safe population,
attaches a genuinely NEW, independently-written path-level measurement
(`harvest_audit/gain_path.py`), and labels episodes post-hoc. It never
re-derives trigger detection, gate evaluation, or episode grouping a
second, possibly-inconsistent way.

## PIT timing contract (B-4)

Every gradable `gain_path` record carries: `signal_evaluation_at`,
`action_eligible_at`, `hypothetical_entry_at`, `entry_price`,
`price_evidence_as_of`, `evaluation_horizon_end`, `market_calendar`,
`time_precision`.

**Invariant** (`harvest_audit/gain_path.py::_validate_gain_path_timing`,
structurally enforced, never merely asserted at one call site):

```
signal_evaluation_at <= action_eligible_at < hypothetical_entry_at <= evaluation_horizon_end
```

- `hypothetical_entry_at` = the first REAL trading date in the committed
  series strictly after `action_eligible_at`, priced at that date's OPEN
  -- never a same-day or prior-day price (no backdated entry).
- Future prices are used ONLY to MEASURE what already-committed history
  shows happened after entry -- never re-injected into any entry/harvest
  judgment (there is no judgment produced by this module at all).
- `market_calendar` is `KRX_WEEKDAY` for Korea, `BTC_24_7`/`CRYPTO_24_7`
  for BTC/Crypto -- this falls out structurally from `PriceSeries` only
  ever storing REAL committed rows.
- `time_precision="DATE_ONLY"` (this repo's evidence is date-granularity).

## Continuous measurements (B-5)

Per gradable episode, `harvest_audit/gain_path.py::compute_gain_path`
computes, from the entry date through a real, endpoint-truncated window of
up to 20 real trading days:

`mfe_pct`/`mfe_date`/`time_to_mfe_days`, `mae_pct`/`mae_date`/
`time_to_mae_days`, `first_positive_return_date`/
`time_to_first_positive_return_days`,
`breakeven_after_positive_mfe_status` (`NO_GIVEBACK_BELOW_BREAKEVEN` /
`RECOVERED` / `NOT_RECOVERED_IN_WINDOW` /
`NOT_COMPUTABLE_NO_TRADING_DAY_AFTER_MFE`) with
`time_to_breakeven_after_positive_mfe_days`, `giveback_confirmed_status` +
`max_giveback_after_mfe_confirmed_pct` (see Defect 2 fix above -- computed
ONLY from real trading days strictly AFTER the MFE date), per-horizon
(1/3/5/10/20) `forward_return_pct`/`mfe_pct`/`mae_pct`/
`mfe_retention_ratio` (each horizon's retention ratio uses ONLY that
horizon's own sub-window MFE, never the full-window/global peak),
`terminal_return_pct`, `peak_to_terminal_giveback_pct`,
`positive_return_duration_days`/`underwater_duration_days`/
`at_breakeven_duration_days`, and an honest `endpoint_coverage` block
(never an arbitrary/interpolated endpoint).

Cross-validated against PR #210's own, separately-written
`replay.forward_metrics.compute_forward_metrics` on real BTC evidence
(`test_profit_harvest_gain_path.py::
CrossValidationAgainstReplayForwardMetricsTests`) -- the two independent
implementations of the same PIT-safe entry-timing rule agree exactly on
the 1/3/5/10-day horizons where they overlap.

## No policy ratification (B-6, see Defect 3 fix above)

Every scenario-comparison record in `policy_input_packet.json` is locked:

```json
{"approval_status": "UNRATIFIED", "scenario_type": "ANALYTICAL_SCENARIO_ONLY",
 "action_authorized": false, "order_authorized": false, "grid_status": "ANALYTICAL_GRID_UNRATIFIED"}
```

The aggregate summary's `status` is ALWAYS
`NOT_COMPUTABLE_POLICY_PARAMETERS_UNRATIFIED`, regardless of sample size
-- no average/"optimal" figure, no "N outperformed" count, is ever
produced from an unratified analytical grid.

## Authority (B-7/B-8 item 15)

Every top-level report carries a hard-coded `authority` block:

```json
{"review_only": true, "action_authorized": false, "order_authorized": false,
 "stage_authorized": false, "buy_authorized": false,
 "production_authorized": false, "trading_authorized": false}
```

No code path anywhere in `harvest_audit/` ever sets any of these to
`true` -- verified structurally
(`test_profit_harvest_end_to_end.py::AuthorityInvariantTests`, which
recursively scans the ENTIRE report for any `*_authorized=true` or
Stage/Buy/Action/Order/Production/Trading-shaped field set to `true`).

## Deliverables

```
evidence/audit/profit_harvest_baseline/
├── episode_ledger.json          -- OFFICIAL population: PIT episodes + gain_path + outcome_category
├── reconciliation.json          -- 21 triggered+gradable rows -> PIT episode mapping (0 unreconciled)
├── pr210_auxiliary_cohort.json  -- OLD Miss/Defense episodes, comparison-only, NOT official
├── market_summary.json          -- per-market population boundary + aggregates
├── coverage_gap.json            -- reused verbatim from replay.coverage_gap
├── gain_path_distribution.json  -- descriptive stats (min/max/median/mean)
├── giveback_distribution.json   -- descriptive stats on confirmed giveback/recovery
├── policy_input_packet.json     -- EARLY_EXIT_OPPORTUNITY_COST_DIAGNOSTIC, UNRATIFIED, no verdict
└── audit_report.md              -- human-readable summary
```

Regenerated byte-identically by `python3 harvest_audit/
run_profit_harvest_audit.py` (see `test_profit_harvest_end_to_end.py::
DeterminismTests`).

## Real numbers on current evidence (re-derived under the corrected,
non-outcome-selected population; see `audit_report.md` for the full table)

BTC: 2 PIT episodes (1 `HOLD_BENEFIT`, 1 `FLAT_NO_MATERIAL_OUTCOME`).
Korea: 5 PIT episodes (2 `HOLD_BENEFIT`, 3 `FLAT_NO_MATERIAL_OUTCOME`,
including the previously-excluded 298040 episode). Crypto: 4 PIT episodes
(1 `HARVEST_OPPORTUNITY`, 3 `FLAT_NO_MATERIAL_OUTCOME`). Zero
`DATA_FAILURE` root-cause episodes ever tagged `HARVEST_OPPORTUNITY` (the
official population structurally requires a real trigger, which
`DATA_FAILURE` rows by definition never have). Every early-exit-horizon
scenario aggregate is `NOT_COMPUTABLE_POLICY_PARAMETERS_UNRATIFIED` with
`grid_status=ANALYTICAL_GRID_UNRATIFIED` -- real sample counts (0-4) are
shown, never a verdict.

## Verification

64 tests across `test/test_profit_harvest_gain_path.py`,
`test/test_profit_harvest_population.py`,
`test/test_profit_harvest_end_to_end.py` (the original 50 B-8 items plus
14 new round-1-methodology-review regressions). All pass in forward
order, reverse order, and as 3 individual per-file subprocesses. No
`importlib.reload()` anywhere in this package. Cross-validated against
PR #210's related `replay/` test suite (259 tests total, including this
package's own 64, all green together).
