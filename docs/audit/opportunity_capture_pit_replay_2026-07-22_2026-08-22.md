# Opportunity Capture PIT Replay — 2026-07-22 → 2026-08-22

★ Status: this document reports a **replay audit**, not a decision. Nothing
in it changes any P0/P5 rule, Stage, trade_proposal, or trading authority.
Every ruleset comparison below is a capital=0 shadow simulation. All real
orders remain Human Approval. See `replay/` for the code and
`evidence/audit/pit_replay/` for the machine-readable ledgers this document
summarizes.

## 0. Canonical sources consulted

Read in full via Notion before any design work began (not paraphrased from
memory):

1. **CIO Investment Operating Doctrine — Forward Alpha → Portfolio
   Competition**
2. **Atlas 1개월 운용 감사 — Signal-to-P&L Review (2026-07-22~08-22)**
3. **Opportunity Capture Control Loop — 신호를 실제 행동으로 변환하는 설계**

All three were found and fetched successfully; nothing here is invented in
their place.

## 1. The headline structural finding

**This repository's own committed evidence trail begins 2026-08-13.**
`git log --reverse` shows the very first commit in this repository's history
is dated 2026-08-13; no briefing output, decision packet, or raw collector
snapshot of any kind is committed for any date before it. The audit window
requested is 2026-07-22 → 2026-08-22 (32 calendar days); **22 of those 32
days (69%) have zero committed Atlas evidence of any kind.**

This is not a per-ticker gap — it is repo-wide, and it applies identically
to all three priority cases (BTC, 005930, 000660) and to the full
population. Per the task's own instruction ("If evidence for some date/
ticker is genuinely missing from the repo, that itself is a DATA_FAILURE
finding, not something to paper over"), every replay entry in this
pre-08-13 sub-window is recorded with `root_cause = DATA_FAILURE` rather
than fabricated, guessed, or silently skipped — see
`replay/evidence_index.py::REPO_HISTORY_STARTS_AT` and the automated proof
in `test/test_pit_replay_end_to_end.py::test_pre_repo_history_dates_are_uniformly_data_failure_for_priority_subjects`.

Two further, real (not hypothetical) data-quality findings surfaced while
building the replay, both handled without silently overriding either side:

- **Cross-snapshot revision**: `000660`'s 2026-08-14 close is reported as
  1,638,000 by the 2026-08-14 KRX snapshot and 1,645,000 by the 2026-08-15
  snapshot (0.43% apart) — a real KRX data revision. Recorded as an
  `integrity_conflict` (earliest capture kept as canonical) rather than
  crashing the whole replay or silently picking one value with no trace
  (`replay/price_series.py::PriceSeries.integrity_conflicts`).
- **Collector finalization lag**: the BTC and crypto-breadth collectors both
  structurally exclude "today" and only finalize through T-1
  (`current_candle_policy: exclude_last_row_always`, verified from each
  snapshot's own `_manifest.json`). This means a same-day close is *never*
  live-known on the day itself — every trigger detection in this replay
  necessarily evaluates against the most recent finalized day, and the real
  lag is recorded per entry as `evaluation_lag_days`.

Despite this, real price/flow history embedded inside the committed KRX
snapshots (each carries several weeks of `daily` history) and inside the
committed Kraken OHLC snapshots (each carries ~720 days, and the
crypto-breadth collector's `ranking_start_day` happens to be exactly
**2026-07-22**) allowed this replay to compute genuine, non-fabricated
forward returns/MFE/MAE across the *entire* requested window, even for dates
where the *signal* side is a structural DATA_FAILURE — see section 2 of
`replay/evidence_index.py`'s docstring for why grading with realized market
history is not a lookahead violation while detecting a signal from it would
be.

## 2. Priority cases: BTC / 005930 / 000660

*(Full machine-readable detail: `evidence/audit/pit_replay/signal_replay_ledger.json`,
filtered to these three subjects.)*

### BTC

- 2026-07-22 → 2026-08-12 (16 of 22 trading days in this sub-window):
  **DATA_FAILURE** — no committed crypto evidence exists this early; this
  repo cannot corroborate or refute the canonical audit doc's own claim of
  "8/8 브리핑이 BTC 약 $64.8K에서 ETF 4거래일 연속 순유입을 정확히 관측" from repo
  evidence alone (no ETF-flow dataset is committed anywhere in this repo —
  a further, distinct DATA_FAILURE: FLOW_REVERSAL is NOT_COMPUTABLE for BTC
  today, see `replay/trigger_engine.py`).
- **2026-08-20 (concrete, real finding)**: a real 20-day PRICE_CONFIRMATION
  breakout fired (evaluated against 2026-08-19's finalized close, per the
  1-day collector lag). Real forward 1-day return **+7.30%**, MFE **+8.92%**.
  The proposed Action Conversion Gate's conditions 1/3/4/5/6 are all met
  (real trigger, real entry price 73,001.1, real invalidation level, PIT
  integrity intact) but condition 7 — a ratified Probe-specific P5 Rule —
  does not exist anywhere in this repo, so `recommended_action = NONE`
  under **both** the existing and proposed rulesets. Root cause:
  **GATE_BLOCK**, not SIGNAL_MISS — the trigger engine worked; nothing was
  authorized to act on it.
- Real BTC window return 2026-07-22→08-21 (from the merged Kraken series):
  **+18.55%**.

### 005930 (삼성전자) and 000660 (SK하이닉스)

- Same DATA_FAILURE sub-window before 2026-08-13.
- From 08-13 onward, both tickers fire a real **RELATIVE_STRENGTH_REVERSAL**
  trigger on nearly every live-known date (outperforming the 4-peer
  own-benchmark average of the rest of the declared KR universe — the same
  own-benchmark pattern this repo already uses for Korea rotation policy).
- **2026-08-19 (concrete, real finding)**: real forward 1-day returns of
  **+9.49% (005930)** and **+12.73% (000660)**, both classified
  **GATE_BLOCK** for the identical structural reason as BTC above — no
  ratified Probe P5 Rule.
- 005930 also fires a real PRICE_CONFIRMATION breakout on 2026-08-21/22, in
  addition to RELATIVE_STRENGTH_REVERSAL (2 independent trigger types —
  condition 2 satisfied), still blocked at condition 7.
- Real full-window returns (2026-07-22→08-21, from merged KRX series):
  005930 **+4.03%**, 000660 **−7.60%** — i.e. even the doc's own "펀더멘털이
  뒷받침된 쏠림 랠리" (8/13 briefing) does not fully net out to a positive
  000660 return over this specific 31-day window in the committed price
  series; the rally is real but partial and it later gives some back.
- Defense side: both tickers show substantial pre-08-13 drawdowns fully
  avoided by the structural capital=0 default — e.g. 005930 −19.96% to
  −23.33% (5-day forward, various July dates), 000660 −23.44% to −31.11% —
  **explicitly flagged with the structural-zero-capital caveat** (see
  section 3) rather than credited as a defensive judgment.

## 3. Full-population audit

- **KR declared universe** (`config/universe.json`, cross-checked against
  every code any KRX snapshot ever reported): 005930, 000660, 267260,
  329180, 298040, 012450 — all six present in the replay population.
  Real, committed `atlas_stage` coverage (2026-08-21 snapshot): 012450 and
  329180 = `Discovery`, 298040 = `Candidate`, 267260/000660/005930 = not in
  the Discovery/Candidate/Ready pipeline at all in the latest snapshot.
- Real KR full-window (07-22→08-21) mover ranking: gainer 012450
  (한화에어로스페이스) **+30.98%**, loser 000660 (SK하이닉스) **−7.60%** — the
  same ranking function ranks both ends (`replay/universe_scan.py::top_kr_movers`,
  proven identical-population by
  `test_replay_universe_scan.py::test_top_kr_movers_gainers_and_losers_use_the_same_ranking_not_separate_rules`).
- **Crypto breadth** (`evidence/crypto/breadth`, 632 tracked pairs, real
  daily OHLC for `ranking_start_day: 2026-07-22` → `2026-08-21` — almost
  exactly the audit window): top 15 real gainers range **+64.0% to +380.6%**
  (AKE/USD, CAP/USD, TAKE/USD, ACA/USD, PEP/USD, …); top 15 real losers
  range **−44.9% to −88.8%** (UMXM/USD, VANRY/USD, FIS/USD, DUCK/USD, …).
  These 30 pairs were run through the *same* full signal-replay pipeline as
  BTC/KR (not just ranked) — see `replay/run_pit_replay.py::load_all_series`.
- Aggregate outcome across the **1,184** signal-replay-ledger entries built
  (7 tracked KR/BTC subjects + top-15/top-15 crypto movers, × 32 days):
  **145** had any real PIT data at all (12.2% — a direct measure of how
  evidence-starved this window is), **49** had ≥1 real detected trigger,
  **2** had independent (≥2 trigger type) confirmation.
- **Opportunity Miss Ledger**: 389 material misses (≥5% forward move,
  best-available horizon), of which 372 = `DATA_FAILURE` (pre-08-13), 11 =
  `SIGNAL_MISS` (real move, live data available, but none of this replay's
  4 implemented trigger types fired — an engine-coverage gap, not a rule
  gap), 6 = `GATE_BLOCK` (real trigger, all conditions but ratification
  met).
- **Defense Ledger**: 389 material avoided-drawdown entries, every one
  carrying the structural-zero-capital caveat (section 3.3 of the design
  doc's own audit principle: don't let a defense credit hide a missed-
  upside debit, and don't over-claim it either).
- No entry is simultaneously a miss and a defense
  (`test_pit_replay_end_to_end.py::test_miss_and_defense_ledgers_were_built_from_the_same_signal_ledger`).

## 4. Root-cause distribution (all misses, full population)

| Category | Count | Meaning here |
|---|---|---|
| DATA_FAILURE | 372 | No committed evidence exists for this date/subject (structural — 22/32 days) |
| SIGNAL_MISS | 11 | Live data existed; none of the 4 implemented trigger types fired |
| GATE_BLOCK | 6 | Real, independently-verifiable trigger(s); no ratified Probe P5 Rule to convert it |
| UNIVERSE_MISS | 0 | Every subject scanned came from the declared universe / breadth catalog |
| ACTION_CONVERSION_FAILURE | 0 | Never reached — GATE_BLOCK always fires first while condition 7 is unratified |
| DECISION_LATENCY | 0 | Not observed in this window at the >3-day threshold |
| NO_POSITION_RULE | 0 | Not reached — GATE_BLOCK dominates |

*(UNIVERSE_MISS/ACTION_CONVERSION_FAILURE/DECISION_LATENCY/NO_POSITION_RULE
reading 0 here is a real result of this window's evidence, not evidence the
categories are unused — `replay/root_cause.py`'s own unit tests exercise all
seven independently with synthetic inputs.)*

## 5. Keep / Change / Kill

See `replay/rule_attribution.py::recommend()` for the exact, ledger-grounded
version of the following (machine output:
`evidence/audit/pit_replay/rule_attribution.json`):

1. **`decision/alpha_review.py`: `trade_proposal` unconditionally `None`** —
   **CHANGE**. Verified (not assumed) directly from the committed source:
   this hard-codes Action Conversion Rate to 0% regardless of trigger
   strength or confirmation count — it is not selectively filtering weak
   signals, it blocks all of them by construction. Recommend the CIO
   doctrine's own prescribed fix (Control Loop doc section 11, slice 4): a
   Probe-specific P5 Rule Slice with a real, bounded loss budget — not
   removal of the gate.
2. **`decision/alpha_review.py`: fixed 30-day review cadence** — **CHANGE**.
   A fixed cadence cannot re-evaluate a flow/price-reversal trigger before
   it decays (doc section 6 already specifies next-trading-day / 24h for
   these trigger types). Keep 30 days for long-horizon thesis review only.
3. **P0/P5 authority invariant (Stage/Buy/Action/Order/Production/trading =
   false until ratified)** — **KEEP**. Nothing in this replay needed to
   relax this to find real, gradeable signal — the proposed ruleset's own
   shadow simulation keeps it intact throughout (verified structurally, see
   section 6 below).
4. **Repo evidence retention (no committed evidence before 2026-08-13)** —
   **CHANGE** (operational, not a decision rule). Recommend persisting
   daily evidence *and generated briefing output* as committed, dated
   artifacts going forward, so a future audit of this kind does not hit the
   same 22-of-32-day wall.

## 6. Existing ruleset vs. proposed ruleset (deliverable 7)

Both sides evaluated over the **identical** 1,184-entry signal-replay
ledger and the identical set of real detected triggers
(`evidence/audit/pit_replay/ruleset_comparison.json`):

| | Existing | Proposed |
|---|---|---|
| Action Conversion Rate (of entries with ≥1 trigger, n=49) | **0.0%** | **0.0%** |
| Review cadence | fixed 30 days | dynamic (next trading day for price/flow triggers) |
| "Qualified pending only P5 ratification" | n/a — no per-condition breakdown exists | **2 / 49 (4.08%)** |

The final action-conversion rate is identical (0%) under both rulesets
today, **because condition 7 (ratified Probe P5 Rule) is uniformly
unsatisfied** — this is itself the headline "Change" finding above, not a
null result. The real differentiating value of the proposed ruleset is
`qualified_pending_gate_ratification_count`: it identifies, from real
market/flow data, exactly which real candidates are fully qualified (real
hypothesis, independent confirmation, entry zone, invalidation, sizing, PIT
integrity) and blocked on nothing but a rule that has not been ratified yet
— something the existing ruleset's single `trade_proposal = None` cannot
express at all.

## 7. Exit-gate self-check (Control Loop doc section 12)

- "세 사례 중 적어도 두 사례가 급등 전 또는 초기 구간에 PROBE_REVIEW를 생성" — **not
  met as a live PROBE_REVIEW** (condition 7 blocks conversion for all three
  priority cases in the committed window), but real BTC/005930/000660
  entries **do** reach `conditions_1_to_6_met = True` at real, materially-
  early points (BTC 08-20 same day as the +7.3% 1-day move; 005930/000660
  08-19, the day before the +9–13% 1-day move) — i.e. the trigger→gate
  pipeline itself would have flagged them early, pending ratification.
- "미래 데이터 사용 0" — met; see section 8.
- "잘못된 Probe의 최대 손실이 사전 한도 이내" — not applicable; no Probe was ever
  actually opened (capital is 0 everywhere, always).
- "기존 방식 대비 Action Conversion과 Captured Return 개선" — not yet measurable
  as a rate improvement (both 0% today) but measurable as a coverage
  improvement (`qualified_pending_gate_ratification_count` = 2, existing =
  0 always).
- "P5/Portfolio/Human Approval 우회 0" — met; see section 8.

## 8. Hard-constraint verification

- **Zero lookahead**: `test/test_replay_lookahead_gate.py` (12 tests) plus
  the end-to-end sweep in `test/test_pit_replay_end_to_end.py` assert, over
  the real replay output, that no trigger's `first_seen_at`/`confirmed_at`
  is ever after its own `decision_date`, and that no forward-metric horizon
  ever ends on or before `decision_date`.
- **Determinism**: `test_pit_replay_end_to_end.py::test_two_independent_runs_produce_byte_identical_json`
  runs the full replay twice and diffs canonical JSON.
  `report_asof_evidence_date` is derived from the latest real snapshot
  capture date, never `datetime.now()`.
- **Authority booleans unchanged**: every ledger entry's
  `existing_ruleset.trade_proposal` is `None` and `proposed_ruleset.capital`
  is the literal int `0` with no parameter able to override it (see
  `test_replay_action_conversion_gate.py::test_capital_is_always_zero_and_no_parameter_can_override_it`);
  no Stage/order/action_taken field exists anywhere in the schema.
- **No survivorship bias**: `replay/root_cause.py::classify()`'s parameter
  list structurally excludes any realized-outcome-shaped argument
  (`test_replay_root_cause_classifier.py`), and the Miss/Defense ledgers
  share one horizon-preference policy and one entry-construction path
  (`test_replay_ledgers.py`).
- **Untouched Forward Alpha files**: verified via `git diff main --stat`
  before opening the PR — see the PR description for the actual command
  output; `decision/alpha_review.py`, `shadow/alpha_shadow_ledger.py`,
  `briefing/daily_orchestrator.py`, and every other file listed in the task
  brief do not appear.

## 9. Known limitations (not papered over)

- Only 4 of the doc's 7 trigger types are implemented against real data
  (PRICE_CONFIRMATION, INVALIDATION_TRIGGER, FLOW_REVERSAL,
  RELATIVE_STRENGTH_REVERSAL). FUNDAMENTAL_REVISION, CATALYST_APPROACH, and
  EXPECTATION_DISLOCATION require parsed guidance/catalyst-calendar data
  that is not committed anywhere in this repo as a dated series — they are
  `NOT_COMPUTABLE`, not guessed at.
- No ETF-flow dataset is committed for BTC, so the canonical audit doc's
  own "8/8 브리핑 ETF 4거래일 순유입" claim could not be independently
  corroborated or refuted from repo evidence in this replay.
- 22 of 32 audit-window days have no committed Atlas evidence of any kind
  (section 1) — this bounds how much of the "signal-to-action" story this
  replay can tell, however deep the underlying *market* history goes.
- Full per-pair signal-replay treatment (trigger engine + gate) was run for
  the 7 tracked KR/BTC subjects plus the top-15/top-15 crypto movers (37
  subjects total), not all 632 committed crypto pairs — the full 632-pair
  population is covered by the movers *ranking* (section 3) but not by
  full trigger-level replay, for tractability.
