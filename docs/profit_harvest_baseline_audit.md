# P7-11 Profit Harvesting Baseline Audit

**BASELINE AUDIT ONLY.** This is a diagnostic measurement layer over
already-approved PR #210 (P10-02/P10-03 Opportunity Capture PIT Replay)
episodes -- it is NOT an operational Harvest Engine and NOT a sell-policy
ratification. No sell threshold, no liquidation rule, no real quantity, no
Trade Proposal, no order generation, and no Harvest action of any kind is
produced anywhere in this package.

## Design decision packet (AI 개발 실행 헌장, section 1)

- **투자 목적**: 이 기능은 "수익실현(harvest)" 그 자체를 자동화하지 않는다.
  Atlas가 과거에 놓친(Miss) 또는 방어한(Defense) episode들에 대해, "만약
  진입했다면 그 이후 가격 경로가 어떻게 전개되었는가"를 PIT-safe하게
  측정하는 감사 기반이다. 목적은 향후 P7-11 정책 설계(부분실현/위험회수/
  핵심물량유지/trailing 임계값)를 위한 실증 근거를 축적하는 것이며, 그
  자체로 매도 시점이나 임계값을 확정하지 않는다.
- **입력 정본**: `replay.run_pit_replay.load_all_series`/
  `build_signal_replay_ledger` (PR #210, 무수정 재사용),
  `replay.opportunity_miss_ledger.build_miss_episodes`,
  `replay.defense_ledger.build_defense_episodes`,
  `replay.price_series.PriceSeries`. 원본 evidence: BTC Kraken OHLC, KRX
  daily, Crypto breadth Kraken OHLC -- `REPO_HISTORY_STARTS_AT=2026-08-13`
  이전은 구조적으로 `DATA_FAILURE`.
- **의미 계약**: 아래 "진단 카테고리" 절 참조.
- **권한 계약**: 진단(Diagnostic)만 허용. Review/Shadow/Buy/Order 권한
  전혀 없음. `policy_input_packet.json`은 미래 별도 CIO 설계결정의 INPUT일
  뿐 정책 자체가 아니다.
- **모집단**: PR #210의 Signal Replay Ledger 전체 population 재사용
  (BTC 전용 컬렉터, KRX committed codes, Crypto PIT-eligible taxonomy).
  생존편향 방지: Miss episode 전체 + Defense episode 전체를 포함하며,
  PR #210 자신의 사전정의된 materiality threshold(`MATERIALITY_THRESHOLD_PCT
  =5.0`/`MATERIALITY_DRAWDOWN_THRESHOLD_PCT=-5.0`, 무수정) 외에는 결과
  크기로 필터링하지 않는다. 현재 급등 중인 종목을 결과 때문에 모집단에
  추가하지 않는다 (모집단은 오직 PR #210의 기존 ledger에서만 파생).
- **실패 정책**: `gain_path.status != "OK"` → `NOT_GRADABLE`. 시장별
  `kpi_population_status`는 PR #210의 정적 분류를 그대로 재사용(Korea/
  Crypto는 `NOT_COMPUTABLE_*`). 조기청산 시나리오 비교의 표본이
  `MIN_SAMPLE_SIZE(=5)` 미만이면 `NOT_COMPUTABLE_INSUFFICIENT_SAMPLE`.
- **하류 소비자**: 없음. `evidence/audit/profit_harvest_baseline/`만
  생성하며, 어떤 운영 경로(`decision/`, `clock/`, `shadow/`, `briefing/`)도
  이 출력을 import하지 않는다 (구조적 테스트로 증명,
  `test_profit_harvest_end_to_end.py::NoDownstreamImportTests`).
- **반례**: 사후 최고가 최적화, 미래정보 재주입, 결과기반 모집단 선택,
  샘플 부족 상태에서 "optimal" 임계값 산출 -- 전부 금지·테스트로 검증
  (`test_profit_harvest_*.py`, 19개 필수 항목 전부 구현).

이 설계 패킷은 P5 Rule Authority나 기존 정본 어휘를 바꾸지 않는다 --
`replay/`는 완전히 무수정 재사용이고, `harvest_audit/`은 새 네임스페이스에
새 진단 어휘만 추가한다. 따라서 헌장의 "설계 패킷이 P5 Rule Authority
또는 기존 정본 어휘를 바꾸면 코드를 먼저 만들지 않는다" 조항의 사전
CIO 설계승인 요구 대상이 아니다.

## Reuse boundary

Every episode's underlying trigger detection, action-conversion gate,
materiality threshold, and episode-deduplication logic is `replay/`'s own,
**completely unmodified**:

- `replay/run_pit_replay.py::load_all_series`/`build_signal_replay_ledger`/
  `market_of`/`PRIORITY_SUBJECTS`
- `replay/opportunity_miss_ledger.py::build_miss_episodes`
- `replay/defense_ledger.py::build_defense_episodes`
- `replay/coverage_gap.py::build_coverage_gap_report`
- `replay/price_series.py::PriceSeries`

`harvest_audit/` is purely additive: it attaches a genuinely NEW,
independently-written path-level measurement (`harvest_audit/gain_path.py`)
to each already-approved episode, and classifies every episode into
exactly one `diagnostic_category`. It never re-derives trigger detection,
gate evaluation, or episode grouping a second, possibly-inconsistent way.

## Diagnostic categories

| Category | Meaning |
|---|---|
| `HARVEST_OPPORTUNITY_DIAGNOSTIC` | A PR #210 Miss episode (materially positive real move Atlas did not act on) with a gradable hypothetical entry. Answers "if entered, how did the price path unfold." |
| `DEFENSE_EPISODE` | A PR #210 Defense episode (materially negative real move, avoided by capital=0 structural default). |
| `NOT_GRADABLE` | No real trading date exists strictly after the episode's `first_action_eligible_date` (or the episode itself has no real action-eligible date, e.g. a residual `DATA_FAILURE`/`SIGNAL_MISS` grouping edge case). |
| `NOT_COMPUTABLE` | Market-level population boundary (reused verbatim from PR #210): Korea's historical PIT watchlist and most of the Crypto window cannot be reconstructed -- see `MARKET_KPI_STATUS`. |
| `EARLY_EXIT_OPPORTUNITY_COST_DIAGNOSTIC` | A SEPARATE, research-only scenario comparison (not an episode category) built only for gradable `HARVEST_OPPORTUNITY_DIAGNOSTIC` episodes: early-exit-at-horizon-N vs full-hold-to-endpoint. Locked `approval_status=UNRATIFIED`/`scenario_type=ANALYTICAL_SCENARIO_ONLY`/`action_authorized=false`/`order_authorized=false` on every record. |

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
  ever storing REAL committed rows (no interpolated calendar day of any
  kind), so Korea entries can never claim a weekend trading date.
- `time_precision="DATE_ONLY"` (this repo's evidence is date-granularity).

## Continuous measurements (B-5)

Per gradable episode, `harvest_audit/gain_path.py::compute_gain_path`
computes, from the entry date through a real, endpoint-truncated window of
up to 20 real trading days:

`mfe_pct`/`mfe_date`/`time_to_mfe_days`, `mae_pct`/`mae_date`/
`time_to_mae_days`, `first_positive_return_date`/
`time_to_first_positive_return_days`,
`breakeven_after_positive_mfe_status` (`NO_GIVEBACK_BELOW_BREAKEVEN` /
`RECOVERED` / `NOT_RECOVERED_IN_WINDOW`) with
`time_to_breakeven_after_positive_mfe_days`, `max_giveback_after_mfe_pct`
(computed ONLY from the MFE date onward -- never a date before it),
per-horizon (1/3/5/10/20) `forward_return_pct`/`mfe_pct`/`mae_pct`/
`mfe_retention_ratio` (each horizon's retention ratio uses ONLY that
horizon's own sub-window MFE, never the full-window/global peak, so an
early horizon's retention never uses a later day's information),
`terminal_return_pct`, `peak_to_terminal_giveback_pct`,
`positive_return_duration_days`/`underwater_duration_days`/
`at_breakeven_duration_days`, and an honest `endpoint_coverage` block
(never an arbitrary/interpolated endpoint).

★ Daily-bar convention: the MFE day's own low IS included in the
post-MFE giveback window (`>=`, not `>`), since a daily OHLC bar cannot
prove whether that day's high or low occurred first intraday -- the
conservative assumption, documented in code, not a defect. What must
never leak in is a low from a date strictly BEFORE the MFE date.

Cross-validated against PR #210's own, separately-written
`replay.forward_metrics.compute_forward_metrics` on real BTC evidence
(`test_profit_harvest_gain_path.py::
CrossValidationAgainstReplayForwardMetricsTests`) -- the two independent
implementations of the same PIT-safe entry-timing rule agree exactly on
the 1/3/5/10-day horizons where they overlap.

## No policy ratification (B-6)

Every scenario-comparison record in `policy_input_packet.json` is locked:

```json
{"approval_status": "UNRATIFIED", "scenario_type": "ANALYTICAL_SCENARIO_ONLY",
 "action_authorized": false, "order_authorized": false}
```

When the gradable sample for a given early-exit horizon (1/3/5 trading
days) is below `MIN_SAMPLE_SIZE=5`, the aggregate summary is
`NOT_COMPUTABLE_INSUFFICIENT_SAMPLE` -- no average/"optimal" figure is
ever produced from too few episodes
(`test_profit_harvest_population.py::
InsufficientSampleNeverProducesOptimalAnswerTests`).

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
├── episode_ledger.json       -- every Miss/Defense episode + its gain_path
├── market_summary.json       -- per-market population boundary + aggregates
├── coverage_gap.json         -- reused verbatim from replay.coverage_gap
├── gain_path_distribution.json  -- descriptive stats (min/max/median/mean)
├── giveback_distribution.json   -- descriptive stats on giveback/recovery
├── policy_input_packet.json     -- EARLY_EXIT_OPPORTUNITY_COST_DIAGNOSTIC, UNRATIFIED
└── audit_report.md              -- human-readable summary
```

Regenerated byte-identically by `python3 harvest_audit/
run_profit_harvest_audit.py` (see `test_profit_harvest_end_to_end.py::
DeterminismTests`).

## Real numbers on current evidence (see `audit_report.md` for the full
table)

BTC: 1 `HARVEST_OPPORTUNITY_DIAGNOSTIC` episode (the 2026-08-20 episode,
also PR #210's real audited Miss). Korea: 1 `HARVEST_OPPORTUNITY_DIAGNOSTIC`
(005930) + 4 `DEFENSE_EPISODE` (005930/000660). Crypto: 2
`HARVEST_OPPORTUNITY_DIAGNOSTIC`. Zero `DATA_FAILURE` episodes ever tagged
`HARVEST_OPPORTUNITY_DIAGNOSTIC`. Every early-exit-horizon scenario
aggregate is currently `NOT_COMPUTABLE_INSUFFICIENT_SAMPLE` (real sample
sizes 0-4, below `MIN_SAMPLE_SIZE=5`) -- honestly reported, not padded or
estimated.

## Verification

19 required regression items (B-8) implemented across
`test/test_profit_harvest_gain_path.py`,
`test/test_profit_harvest_population.py`,
`test/test_profit_harvest_end_to_end.py` (50 tests total). All pass in
forward order, reverse order, and as 3 individual per-file subprocesses.
No `importlib.reload()` anywhere in this package (the exact class of bug
found and fixed in PR #211's own test suite -- structurally banned here
from the start via a dedicated regression). Cross-validated against
PR #210's related `replay/` test suite (245 tests total, including this
package's own 50, all green together).
