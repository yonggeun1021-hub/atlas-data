# Crypto PAPER wiring v2 (build plan PR3)

Config: `config/crypto_paper_wiring_v2.json`. Tests: `test/test_crypto_paper_wiring_v2.py`.
No order, exchange, secret, server or REAL authority. Every number comes from
`config/rule_registry_v1.json` or an existing repository contract.

## 1. Cutover (T_cut)

| Setting | Behaviour |
|---|---|
| `decision_snapshot_v4_cutover.t_cut_utc = null`, `status = NOT_ACTIVE` (committed default) | `decision/crypto_paper_decision_snapshot.py` emits `crypto_paper_decision_snapshot_packet/3`, byte-identical to before. |
| `t_cut_utc = "<YYYY-MM-DD>T07:00:00Z"`, `status = ACTIVE_FROM_T_CUT` | Packets generated at or after T_cut are `/4`; earlier ones stay `/3`. `validate_output` rejects a `/4` packet dated before T_cut or while inactive. |

T_cut must be at the crypto decision cycle time of `RULE.EXEC.TIME_CONTRACT.V1`
(07:00Z) and is chosen by the CIO/user after the private runtime reinstall
(build plan section 6 S1). This PR does not set it.

The frozen tuples `OUTPUT_SCHEMA_VERSIONS` / `PER_MARKET_OUTPUT_SCHEMA_VERSIONS`
(/1-/3) are unchanged; `/4`-aware code uses `PER_MARKET_LAYOUT_SCHEMA_VERSIONS`
and `ALL_OUTPUT_SCHEMA_VERSIONS`.

## 2. Decision snapshot `/4`

Same layout as `/3` plus:

* `source_refs` roles `crypto_paper_runtime_decision`
  (`evidence/regime/crypto_paper_runtime/<date>/<sha>.json`, latest
  `evaluation_at <= generated_at`) and `rotation_confirmation_packet`
  (`evidence/rotation/confirmation/CRYPTO/<as_of>/packet.json`, latest as-of
  date strictly before the capture date). Both are retained under
  `_sources/sha256/` like the other sources.
* P5-08 runs `crypto_candidate_promotion_contract/3` with both sources
  (T2_REGIME_PERMITS_NEW_BUYS and T2_ROTATION_MEMBERSHIP are now wired).
* P5-09 runs `crypto_paper_buy_eligibility_contract/3` without private inputs,
  so a FOCUSED_REVIEW row stays WATCH (re-entry and duplicate guard need the
  private ledger).
* Candidate `p5_08` adds `t2_required_conditions`, `warnings`, `rule_refs`,
  `unapplied_rules`; `p5_09` adds `record_only_features`, `rule_refs`.
* Top-level `crypto_paper_wiring` block (contract versions, runtime decision
  id/date/regime, rotation as-of/observation status, unavailable reasons).

## 3. Promotion contract/3 rotation source

`build_promotion_packet(..., contract_version=3, crypto_runtime_decision=...,
rotation_confirmation=<CRYPTO rotation_confirmation_packet/1>)`.
T2_ROTATION_MEMBERSHIP for bucket BTC / ETH / ALT: STRONG_CONFIRMED or
STRONG_HELD -> PASS (with `strong_confirmed_on`), other observed state -> FAIL,
unobserved / stale / not decision-effective packet or unresolved bucket ->
UNKNOWN. A packet dated on or after the reference UTC day raises
`ROTATION_CONFIRMATION_LOOKAHEAD`. Without the argument the contract/3 output
is unchanged.

## 4. Buy eligibility contract/3

`build_eligibility_packet_v3(promotion_v3, evaluation_as_of, decision_at_utc,
decision_packet_id, known_idempotency_keys, position_fills,
session_budget_record, fee_rate)` -> `crypto_paper_buy_eligibility_packet/3`.

| Criterion | Role |
|---|---|
| FOCUSED_REVIEW_UPSTREAM, REGIME_PERMITS_ENTRY, ROTATION_MEMBERSHIP | gate (echo of the six T2 conditions, RULE.ENTRY.PAPER_BASELINE_B.V1) |
| REENTRY_PERMITTED | gate: `portfolio/paper_position_episode.reentry_decision` (RULE.EXEC.REENTRY.V1, D7) |
| DUPLICATE_GUARD | gate: R1 key (CRYPTO, decision packet id, instrument) |
| ORDER_DRAFT_COMPLETE | WAIT vs PAPER_BUY_ELIGIBLE: needs a session budget allocation line with a quantity |
| TRIGGER_TIMEFRAME_ALIGNMENT, BREAKOUT_OR_PULLBACK, INDEPENDENT_PRICE_VOLUME_EVIDENCE, CURRENT_EVIDENCE_FRESHNESS, MATERIAL_BLOCKER, OVEREXTENSION | `record_only_features`, never a gate |

Order draft: `session_id`, `session_budget_record_sha256`, `allocated_krw`,
`planning_price_krw`, `quantity`, `fee_rate`, `submitted_amount_krw`,
`expires_at` = `next_review_at` = next 07:00Z, `duplicate_guard_key`,
`reentry_key`, `planned_loss` (record-only: quantity x DS5 ATR multiple x
Wilder ATR14, UNKNOWN when not computable). Contract/2 is unchanged.

## 5. Runtime request `/4`

`shadow/crypto_paper_runtime_bridge.build_runtime_request(decision_v4, ...,
allocation_envelope, recorded_session_budget, position_fills, exit_intents)`.
Decisions `/1-/3` keep producing request `/3` (and `/2` replays) unchanged.

1. Rebuilds P5-08 contract/3 from the decision's retained sources and checks
   every candidate state against the decision.
2. P5-09 contract/3 with the private inputs; WAIT rows pass the per-market
   entry blockers (as `/3`) and markets with an open exit intent are removed.
3. Session budget (`portfolio/paper_session_budget.py`): NAV0 from the crypto
   ledger (cash + FRESH marks, open buy orders as reservations; build plan 2-3
   principle 4), ADV = the decision's `krw_30d_avg_turnover`, planning price =
   decision snapshot best ask, quantity step = simulator decimal scale. The
   envelope's CRYPTO `confirmed_state` must equal the decision regime.
   * `recorded_session_budget` from an earlier decision in the session ->
     `WAIT_SESSION_BUDGET_ALLOCATED`, no new buys.
   * The same decision re-derives the same record (restart safe); a different
     record under the same decision raises `RECORDED_SESSION_BUDGET_CONFLICT`.
   * A decision with no allocation candidate writes no record.
4. Each PAPER_BUY_ELIGIBLE row -> LIMIT BUY sized by `marketable_limit_sizing`:
   largest quantity whose decision-book VWAP stays within
   `RULE.EXEC.QUALITY_LAYERS.V1.crypto_slippage.threshold_bp` (150) of the
   best ask, with limit x quantity x (1 + fee) <= allocated amount; limit = the
   worst level needed.
5. Each exit intent -> LIMIT SELL with the same sizing (quantity <= remaining),
   held while the market's book is not FRESH.
6. `market_regime_status` = `simulator_market_regime_status(state)`
   (RISK_ON/NEUTRAL -> PASS, RISK_OFF/STRESS -> FAIL, UNKNOWN -> UNKNOWN). The
   `/3` path applies the same mapping (its input is always UNKNOWN).

## 6. What PR4 (private runtime v2) must consume

* Decision `crypto_paper_decision_snapshot_packet/4` (validator accepts it only
  after T_cut) and request `crypto_paper_runtime_request/4`.
* Request top-level fields: `/3` fields + `session_budget_record`
  (`session_budget_record/1` or null), `sell_requests`, `wiring`.
  Row fields: `requests[]` = market, planned_loss, order_draft, intent,
  source_snapshot, execution_sizing; `sell_requests[]` = market,
  exit_intent_id, exit_reason_code, intent, source_snapshot, execution_sizing.
* Persist `session_budget_record` once per (CRYPTO, session_id, rule version)
  via `SessionBudgetLedger`, pass it back as `recorded_session_budget`, consume
  `execution_sizing.submitted_amount_krw` per submitted BUY, never restore on
  cancel/expiry.
* Supply `allocation_envelope` (`paper_allocation_envelope/1` at the decision
  instant), `position_fills` (`paper_position_episode` FILL rows with exit
  reasons, `[]` when none), `exit_intents` (open `paper_exit_intent/1` from the
  store with `remaining_quantity`, `[]` when none), `known_idempotency_keys`.
* Runtime config `/1` `order_type` / `limit_price_source` are not used by `/4`
  (superseded by RULE.EXEC.QUALITY_LAYERS.V1); `fee_rate`, `queue_fraction`
  still are. `open_position_risk` is optional (record-only planned loss).

## 7. Follow-up: /4 consumers and the decision time bound

* `portfolio/crypto_paper_stale_hold.py` accepts `/4` (same per-market layout).
* `governance/rule_lineage_producers.py` accepts `/4`: adds `promotion_t2_required`
  and `buy_eligibility` events from the candidates' `rule_refs`; `/1-/3`
  sidecars are unchanged.
* `briefing/crypto_funnel_briefing.py` contract `/4` (sources `/1-/4`); issued
  contract `/3` briefings revalidate under the frozen `/3` contract. `/4`
  briefings add the runtime decision / rotation reference and T2 state per row.
* Decision time: the workflow samples `generated_at` after the capture and
  truncates it to the second, so the last realtime message could postdate it
  (bridge `REALTIME_*_FUTURE_DATED`). `populate()` now stamps new packets
  (`/3` and `/4`) with `decision_time_not_before_inputs`: the first whole second
  no realtime input postdates (at most +1s; nothing uncaptured is admitted and
  freshness is judged at the later instant). `/4` build rejects any realtime
  input after `generated_at`. Committed packets keep their own `generated_at`
  and re-derive byte-identically (they are not re-stamped, so a committed
  packet like 2026-09-14 23:43:41 still cannot seed a bridge request). The
  decision step writes the stamped `generated_at` to `GITHUB_OUTPUT`, and the
  capture-gap guard accepts a packet stamped exactly +1s only when a realtime
  input lies inside that second. If the +1s bound would cross into the next UTC
  date, the slot writes no packet (`WAIT:DECISION_TIME_BOUND_CROSSES_UTC_DATE`,
  NOT_EVALUATED) instead of filing sampled-day inputs under the next day.

## 8. Activation fixes (#763 review)

* Restart: a `recorded_session_budget` from the same decision is reused
  verbatim (no re-build, no conflict abort); lines whose idempotency key is
  already known are blocked by the duplicate guard and the rest are
  (re)submitted; an all-submitted re-run still returns the recorded record.
  A record from an earlier decision in the session still blocks new buys.
* Exit sells: valid through the next decision slot
  (`sell_order_valid_before`, slot = `SCHEDULED_SLOT_MINUTES`), capped at the
  session's `order_valid_before` (07:00Z, canon 2-3), and re-sized on a fresh
  book by the following decision; an open sell already past its validity does
  not block re-issue. A sell whose slot bound the session end would cut short
  (decisions in the last slot before 07:00Z) is not issued
  (`EXIT_SELL_DEFERRED_TO_NEXT_SESSION:{market}`); the next session's first
  decision issues it; the request then reports `wiring.sell_order_valid_before_utc`
  = null with `sell_issuance_deferred_to_next_session` = true. The envelope is
  first re-derived by the execution core (a tampered envelope raises
  `EXECUTION_CORE_REJECTED:ENVELOPE_SHA_MISMATCH`, never allowlisted) and must
  then be for this decision instant (`ALLOCATION_ENVELOPE_NOT_THIS_DECISION`). When sells exist, only the stale or
  mismatched private buy inputs in `BUY_SIDE_FAILURES_EXITS_MAY_PROCEED`
  (envelope for another instant or state, signed record for another session or
  regime) become a `BUY_SIDE_BLOCKED_EXITS_PROCEED:*` blocker; integrity faults
  (record/envelope rejected by the execution core, promotion rebuild
  inconsistent with the decision) still abort the request.
* Open buys in a market with an exit intent are emitted as `cancel_requests`
  (canon 1-4), excluded from match snapshots and budget reservations.
  Request `/4` gains the `cancel_requests` field
  (`market, order_id, exit_intent_id, reason_code`).
* Crypto quantities (/4 orders and session budget lines) are floored to 8
  decimal places (canon 2-2 step 4; config `quantity_step.decimal_places`);
  a sell of the whole remaining quantity is not floored. `/3` unchanged.
* `/4` packets record `crypto_paper_wiring.t_cut_utc`; validation checks the
  packet's own value and that the configuration still names the same T_cut
  (immutable once set).

## 9. T_cut set (crypto PAPER v2 operation record)

* `USER_RATIFICATION_CRYPTO_PAPER_V2_OPERATION_20260915` (sha256 `ccc846a3…`)
  is byte-copied to `evidence/authority/` and registered as five rows
  (ledger genesis, order type, T_cut, reduction pace, portal projection /2).
* `decision_snapshot_v4_cutover.t_cut_utc = 2026-09-18T07:00:00Z`
  (`ACTIVE_FROM_T_CUT`, `source_record` = that record). The loader checks the
  registry decision cycle, the record bytes and that the record's
  RULE.CRYPTO.PAPER_V2_TCUT.V1 names the same instant. Packets before T_cut
  stay `/3`. Blocking old-path (/3) new buys before T_cut is the private
  runtime's job (PR4).
* Execution core: for CRYPTO, UNKNOWN-cap and NAV-drawdown-override reductions
  use RULE.EXEC.REDUCTION_PACE_UNKNOWN_CAP_AND_DRAWDOWN.V1 = the D5-b downgrade
  pace (half of the excess in session 1, the remainder in session 2; the
  `downgrade_progress` input is shared). KR/US stay NOT_DEFINED (record scope).
