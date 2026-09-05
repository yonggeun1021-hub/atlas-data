# Runtime Regime Technical Wiring — Exact Gap Map

Task: `runtime-regime-readiness-technical-wiring-01`
Date: 2026-09-05
Scope: readiness conveyance only. No policy, threshold, weight, TTL, or PIT
acceptance is created here. No runtime/live/REAL/order/trading authority is
opened. `UNKNOWN` stays `UNKNOWN`.

This document is the exact, code-level reading of where a *runtime* Regime
decision stops today, what the P6-06 `P1_REGIME_DECISION` slot can and cannot
receive, and which smallest honest technical slice was implemented against it.

---

## 1. `regime/decision_authority.py` — the three boundaries it actually contains

The module is not one gate. It contains three separate, independently bound
mechanisms. Conflating them is the single most common source of false
"Regime is ready" claims, so they are separated verbatim here.

### 1.1 Runtime decision boundary — `evaluate_decision_authority()`

- Contract: `config/regime_decision_authority_contract.json`,
  `contract_version = regime_decision_authority/v1`,
  `contract_mode = UNRATIFIED_CLASSIFICATION_GATE`.
- Inputs: a `regime_output/v1` envelope (`regime/output_contract.py`) plus a
  `regime_minimum_coverage/v1` gate (`regime/minimum_coverage.py`).
- `repository_policy_registry_status` is pinned `ABSENT`.
- All nine `required_policy_components` are pinned non-ratified:
  `FACTOR_NORMALIZATION`, `FRESHNESS`, `DIRECTION`, `CONFIDENCE`,
  `STRESS_OVERRIDE`, `INVALIDATION`, `HYSTERESIS` are `UNRATIFIED`;
  `AGGREGATION_WEIGHTS` and `CLASSIFICATION_THRESHOLDS` are `ABSENT`.
- `allowed_decision_statuses` is exactly `["BLOCKED_COVERAGE",
  "BLOCKED_POLICY_UNRATIFIED"]`. There is **no** success status in the
  vocabulary; the function is structurally incapable of emitting one.
- Output pins `regime = UNKNOWN`, `direction = UNKNOWN`, `confidence = null`,
  `policy_gate.classification_eligible = false`,
  `policy_gate.replay_eligible = false`.
- `authority.classification_authorized`, `direction_authorized`,
  `confidence_authorized`, `production_authorized`, `trading_authorized` are
  all `false`.

**Gap:** this is a *validated refusal*, not a decision. Nothing downstream can
turn it into a Regime.

### 1.2 Common v1 aggregation replay — `replay_common_v1()`

- Bound by hash to `config/regime_source_owner_registry_v2.json`
  → `common_v1_alignment` (`policy_status = RATIFIED_PAPER_BASELINE_V1`), the
  decision packet
  `CIO-GATE2-3MARKET-REGIME-SOURCE-FIRST-B-2026-09-01` /
  `bdeb9b99…7591c`, the legacy contract sha
  `de5e9d6b…e29f3`, and `config/paper_regime_reference_policy_v1.json`.
- `contract_mode` is the literal
  `SHADOW_PIT_REPLAY_ONLY_RUNTIME_NOT_WIRED`.
- `pit_replay_acceptance = NOT_ACCEPTED`.
- `market_kill_stress_condition_status = UNRATIFIED_NOT_IMPLEMENTED`.
- `market_specific_normalization_freshness_and_replay_inherited = false` in
  the ratified registry block itself.
- **Its input is an already-signed axis direction** per axis
  (`POSITIVE`/`NEUTRAL`/`NEGATIVE`, plus `STRESS` on `RISK_VOL` only). It does
  not accept, and cannot derive, a direction from evidence.

**Gap — and the hard rule for this lane:** this mechanism being complete and
deterministic means the *historical replay mechanism* is complete. It does
**not** mean P1 WBS is complete and it must **never** be labelled
runtime-ready. Its own contract string says so.

### 1.3 Signed-axis normalization boundary — `normalize_signed_axes()`

This is the exact seam between §1.1 and §1.2.

- `contract_version = regime_signed_axis_normalization/v1`,
  `contract_mode = UNRATIFIED_SIGNED_NORMALIZATION_GATE`.
- Input: a `regime_output/v1` envelope, which proves axis **presence only**
  (`DEFINED`/`UNDEFINED`) — see `regime/live_axis_adapter.py`, whose mode is
  the literal `EVIDENCE_ONLY_NO_INTERPRETATION`.
- Output: real per-axis coverage, and `signed_direction = null` /
  `normalized_value = null` for **every** axis, always.
- `common_v1_replay_step = null`, `replay_step_emitted = false`.
- Result is `BLOCKED_COVERAGE` (when axes are missing) or
  `BLOCKED_SIGNED_NORMALIZATION_UNRATIFIED` (when 5/5 coverage is met), never
  anything else; `regime`/`direction` fail closed to `UNKNOWN`.
- `load_signed_axis_policy()` fails closed with
  `SIGNED_AXIS_POLICY_UNIMPLEMENTED` if it ever finds a **non-null**
  `signed_normalization_policy` in the registry — i.e. a registry edit alone
  cannot activate normalization; implementing one is a separate ratified
  slice.

**Gap:** coverage 5/5 does not create direction. Without a ratified per-market
signed-normalization policy, §1.2 has no legal input from live evidence, so
there is no path from real evidence to a runtime classification. This is the
binding blocker, not a missing consumer.

---

## 2. `config/regime_source_owner_registry_v2.json` — exact per-market state

`registry_version = regime_source_owner_registry/v2`,
`registry_mode = SOURCE_OWNER_ARCHITECTURE_ONLY`.

| registry market | `acceptance_status` | `signed_normalization_policy` | freshness | `pit_replay_acceptance` |
| --- | --- | --- | --- | --- |
| `KRX` (= `regime_output` market `KR`) | `BLOCKED_SIGNED_NORMALIZATION_TTL_PIT_REPLAY` | `null` | `ttl_seconds: null` | `NOT_ACCEPTED` |
| `US` | `BLOCKED_FINISHED_SESSION_TTL_PIT_REPLAY` | `null` | `ttl_seconds: null` | `NOT_ACCEPTED` |
| `CRYPTO` | `BLOCKED_OVERALL_FRESHNESS_PIT_REPLAY` | `null` | `overall_freshness_policy: null` | `NOT_ACCEPTED` |

Note the name mapping already encoded in `decision_authority.py`:
`SIGNED_AXIS_REGISTRY_MARKET = {"US": "US", "KR": "KRX", "CRYPTO": "CRYPTO"}`.

`aggregate.acceptance_status = BLOCKED_MIXED_EVIDENCE_CLASSES`,
`aggregate.runtime_output_status = "UNKNOWN/HOLD/WAIT"`,
`aggregate.pin_update_allowed = false`.

`forbidden_promotions` includes `SIGNED_NORMALIZATION_RATIFICATION`,
`TTL_OR_FRESHNESS_RATIFICATION`, `PIT_REPLAY_ACCEPTANCE`,
`REGIME_RESULT_RATIFICATION`, `THRESHOLD_OVERRIDE`,
`FIXTURE_OR_BASELINE_PROMOTION`, `CANDIDATE_FORCING`.

`authority.runtime_binding_authorized = false`,
`market_regime_authorized = false`, and `real`/`live`/`order`/`production`/
`trading` are all `false`.

Also unresolved and named in the registry itself: CRYPTO
`leadership_source.sector_chain_group_layer = UNKNOWN_GROUP_LAYER` with
`group_coverage_policy_status = UNRATIFIED`; US
`natural_receipt_owner.owner_status =
DESIGNATED_NATURAL_RUNNER_IMPLEMENTATION_PENDING`; CRYPTO
`natural_receipt_owner.owner_status =
DESIGNATED_NATURAL_OWNER_PACKAGE_IMPLEMENTATION_PENDING`.

---

## 3. Current evidence and briefing consumers

Real runtime path that already exists, end to end:

```
scheduled collectors / archives
  -> briefing/daily_orchestrator.py component rows
  -> regime/live_axis_adapter.py  (EVIDENCE_ONLY_NO_INTERPRETATION, v8)
  -> regime/output_contract.py build_unknown_output()   [regime_output/v1]
  -> build_regime_outputs()  { "US": …, "KR": …, "CRYPTO": … }
       |-> briefing/three_market_regime_header.py       (header row)
       |-> portfolio/cash_exposure_action.py            (CASH_EXPOSURE_*)
       `-> portfolio/regime_inverse_invariant.py        (INVERSE_*)
```

Facts about that path:

- `regime/output_contract.py` pins `runtime_authorized_regimes = ["UNKNOWN"]`
  and `runtime_authorized_directions = ["UNKNOWN"]`; `confidence` must be
  `null`. `minimum_coverage_policy` inside it is `UNRATIFIED`.
- The header row's reason is
  `LIVE_AXIS_EVIDENCE_WIRED_REGIME_SCORING_UNRATIFIED` when any axis is
  `DEFINED`, otherwise `NO_QUALIFIED_LIVE_AXIS_EVIDENCE`.
- `CASH_EXPOSURE_*` and `INVERSE_*` rows carry
  `REGIME_UNKNOWN_NOT_EVALUATED`.
- **Nothing on this path calls `regime/decision_authority.py`.** Before this
  slice, the merged runtime consumer of the coverage gate, the runtime
  decision boundary, and the signed-axis boundary was zero. That is the
  technical wiring gap this task addresses.

---

## 4. P6-06 `P1_REGIME_DECISION` slot — exact mechanics

In `portfolio/defensive_action_decision.py`:

- `P1_REGIME_DECISION` is listed in `unavailable_only_source_slots`
  (with `P2_FLOW_LEDGER`).
- `_source_rows()` raises
  `SOURCE_PACKET_NOT_YET_SUPPORTED:P1_REGIME_DECISION` if any packet is
  supplied for it. Supplying a packet is therefore impossible by design and
  must stay impossible.
- Its `unavailable_reasons[...]` list **is** part of the derivation: it is
  re-derived in `validate_packet()` and covered by `packet_sha256`. It is
  validated by `_reasons()` — non-empty, sorted, unique, each matching
  `^[A-Z0-9][A-Z0-9_.:-]{2,159}$`.
- Every `DECISION_SOURCES` entry for `CASH_PRIORITY`, `REDUCE_REVIEW`,
  `HEDGE_REVIEW`, `INVERSE_REVIEW` includes `P1_REGIME_DECISION`, so each
  decision row carries `SOURCE_UNAVAILABLE:P1_REGIME_DECISION`.
- `unresolved_boundaries` always contains
  `P1_REGIME_DECISION_UNAVAILABLE`.
- `NO_ACTION` always carries
  `MISSING_OR_UNEVALUATED_INPUT_IS_NOT_NO_ACTION`.
- Contract is byte-pinned against `config/defensive_action_decision_contract.json`
  by `_expected_contract()`; the contract cannot be changed from this lane.

Before this slice, `briefing/daily_orchestrator.py` filled that slot with the
single placeholder string
`P1_REGIME_DECISION_PRODUCTION_CONTRACT_UNAVAILABLE` — true, but not
diagnostic: it does not say *which* market, *which* axis, or *which* policy
component is missing.

---

## 5. Implemented technical slice (this task)

Smallest real slice that adds information without adding authority.

### 5.1 `regime/runtime_regime_readiness.py` (new)

`runtime_regime_readiness/v1`, mode
`RUNTIME_READINESS_ONLY_NO_REGIME_DECISION`.

Chains only pre-existing validators, per market, over the **real** runtime
`regime_output/v1` envelopes the orchestrator already builds:

1. `regime/output_contract.py::validate_output`
2. `regime/minimum_coverage.py::evaluate_minimum_coverage`
3. `regime/decision_authority.py::evaluate_decision_authority`  (§1.1)
4. `regime/decision_authority.py::normalize_signed_axes`        (§1.3)

It emits real coverage, the real per-market registry acceptance state, and an
exact, sorted, machine-readable blocker list. It pins
`runtime_decision_available = false`, `regime = UNKNOWN`,
`direction = UNKNOWN`, `confidence = null`,
`decision_status = BLOCKED`,
`historical_replay_is_not_runtime_ready = true`, and every authority flag
`false` except `readiness_inventory_only`.

`validate_readiness()` re-derives the whole packet from the embedded
envelopes and fails closed on any tamper, including any attempt to flip
`runtime_decision_available` or shorten the blocker list.

Blocker vocabulary (all exact, all derived, none authored per-run):

| code | source |
| --- | --- |
| `P1_REGIME_DECISION_NOT_RUNTIME_WIRED` | invariant of this contract |
| `COMMON_V1_REPLAY_MODE:SHADOW_PIT_REPLAY_ONLY_RUNTIME_NOT_WIRED` | §1.2 contract mode |
| `REGIME_POLICY_COMPONENT_MISSING:<COMPONENT>` | §1.1 `missing_components` |
| `DECISION_AUTHORITY_BLOCKED:<MARKET>:<STATUS>` | §1.1 `decision_status` |
| `SIGNED_NORMALIZATION_POLICY_UNRATIFIED:<MARKET>` | §1.3 |
| `MINIMUM_COVERAGE_NOT_MET:<MARKET>` | §1.1/§1.3 coverage |
| `AXIS_UNDEFINED:<MARKET>:<AXIS>` | real missing axes |
| `MARKET_ACCEPTANCE_BLOCKED:<MARKET>:<ACCEPTANCE_STATUS>` | registry §2 |
| `PIT_REPLAY_NOT_ACCEPTED:<MARKET>` | registry §2 |

The module additionally fails closed if any upstream invariant it depends on
stops holding — a non-`UNKNOWN` regime/direction, a non-null confidence, an
`classification_eligible = true`, an emitted replay step, or a non-null
`signed_direction` on any axis. It reports; it cannot be the thing that
promotes.

### 5.2 `portfolio/defensive_action_decision.py`

Added `p1_regime_decision_unavailable_reasons(readiness_packet)`: validates
the readiness packet with the readiness module's own validator, refuses any
packet claiming `runtime_decision_available` is anything but `false`, and
returns the exact blocker list.

The readiness packet's own `packet_sha256` is deliberately **not** forwarded.
It covers `regime_output/v1` envelopes, which embed the caller's invocation
`generated_at` and per-axis `age_seconds`; forwarding it would inject
invocation-time noise into `daily_orchestrator.py`'s
`_component_semantic_fingerprint()` (whose noise filter is key-name based and
would not strip a hash embedded inside a reason *string*), producing spurious
same-day republish revisions. The blockers themselves are invocation-
independent and independently recomputable from the same envelopes; this is
pinned by a test in each of the three suites.

The P6-06 contract, packet field set, source order,
`unavailable_only_source_slots`, decision vocabulary, and authority block are
**unchanged**. `P1_REGIME_DECISION` remains packet-refusing.

### 5.3 `briefing/daily_orchestrator.py`

`build_defensive_action_decision()` now accepts the already-built
`regime_outputs` and, for the `P1_REGIME_DECISION` slot only, replaces the
single placeholder reason with the exact derived blocker list. Any failure
falls back to the previous placeholder plus
`P1_REGIME_READINESS_INVALID:<VALIDATOR_CODE>` — never to availability.

No new component id was introduced (the orchestrator's `component_order` is
config-owned and outside this lane's scope), and no row status changed:
`DEFENSIVE_ACTION_DECISION` stays `PENDING` with
`decision_status = BLOCKED`.

### 5.4 Tests

`test/test_runtime_regime_readiness.py` (new), plus bounded additions to
`test/test_defensive_action_decision.py` and
`test/test_daily_orchestrator.py`.

Root integration registers `test/test_runtime_regime_readiness.py` in the
explicit `run_all.py` test list. The other two suites are already registered
and cover the new wiring.

---

## 6. What is still BLOCKED / NOT_COMPUTABLE after this slice

Unchanged and explicitly preserved:

1. **`SIGNED_NORMALIZATION_POLICY` — `null` for US, KRX, CRYPTO.** No signed
   axis direction can be produced from live evidence. `NOT_COMPUTABLE`.
2. **Freshness/TTL** — `ttl_seconds: null` (US, KRX),
   `overall_freshness_policy: null` (CRYPTO). `NOT_COMPUTABLE`.
3. **PIT replay acceptance** — `NOT_ACCEPTED` for all three markets and for
   common v1. `BLOCKED`.
4. **Common aggregation v2**, market-kill stress
   (`UNRATIFIED_NOT_IMPLEMENTED`), invalidation and hysteresis runtime
   semantics. `BLOCKED`.
5. **Crypto LEADERSHIP real history** incomplete;
   `sector_chain_group_layer = UNKNOWN_GROUP_LAYER`,
   `group_coverage_policy_status = UNRATIFIED`. `UNKNOWN`.
6. **`P1_REGIME_DECISION` remains UNAVAILABLE** in P6-06, and
   `P2_FLOW_LEDGER` remains independently unavailable for its own documented
   reason (append-only history with no top-level PIT identity).
7. **`DEFENSIVE_ACTION_POLICY_NOT_RATIFIED`** still binds every P6-06
   decision row; `selected_action`, `risk_budget_allocation`,
   `target_exposures`, `position_size`, `action_proposal` stay `null` and
   `order_intents` stays `[]`.
8. Historical/PIT replay completeness is **not** P1 WBS completeness and is
   **not** runtime readiness.

Authority unchanged and all `false`: classification, direction, confidence,
signed normalization, freshness/TTL, PIT acceptance, runtime binding, Regime
result ratification, strategy eligibility, Stage, Buy, action, order, capital,
REAL, live, Production, Trading.

---

## 7. CIO decision packet — what would actually unblock a runtime Regime

Ordered, each independently ratifiable. None of these is performed here.

1. **Per-market signed-normalization policy (the binding blocker).**
   For each of US / KRX / CRYPTO, ratify a hash-bound policy that maps each
   of the five axes' evidence to `POSITIVE` / `NEUTRAL` / `NEGATIVE`
   (plus `STRESS` for `RISK_VOL`), written into
   `markets.<M>.signed_normalization_policy`. Note the deliberate fail-closed
   trap: `decision_authority.load_signed_axis_policy()` currently *raises*
   `SIGNED_AXIS_POLICY_UNIMPLEMENTED` on a non-null value, so ratification and
   the implementation slice that consumes it must land together.
2. **Freshness / TTL per market.** `ttl_seconds` (US, KRX) and
   `overall_freshness_policy` (CRYPTO), plus the staleness → `UNKNOWN`
   transition rule.
3. **PIT acceptance criteria.** What evidence set, over what window, with what
   counterexamples, converts `pit_replay_acceptance` from `NOT_ACCEPTED`.
4. **Common aggregation v2 + market-kill stress condition**, replacing
   `UNRATIFIED_NOT_IMPLEMENTED`, with invalidation and hysteresis defined for
   runtime (not replay) operation.
5. **Crypto coverage.** LEADERSHIP group layer ratification and sufficient
   real history so CRYPTO can reach 5/5 rather than remaining `UNKNOWN`.
6. **Runtime consumer contract.** Markets may advance independently; missing
   Crypto coverage is not a new prerequisite for US or KRX. For each market whose applicable gates are satisfied, define the runtime Regime
   decision packet identity (its own `generated_at`/`as_of_date` and PIT
   identity) that P6-06's `P1_REGIME_DECISION` slot would accept, and move it
   out of `unavailable_only_source_slots` in
   `config/defensive_action_decision_contract.json` — a config/registry change
   outside this lane.
7. **Separately**, P6-06's own Defensive Action policy
   (`DEFENSIVE_ACTION_POLICY_NOT_RATIFIED`) must be ratified before any
   `CASH_PRIORITY` / `REDUCE_REVIEW` / `HEDGE_REVIEW` / `INVERSE_REVIEW` /
   `NO_ACTION` is evaluated, regardless of Regime.

Sequencing note for the CIO: items 1–2 are policy ratifications that no
implementation lane can substitute for. Until item 1 exists, additional P6
hardening does not move the money path; the readiness packet added here exists
precisely so that the *exact* remaining blockers are visible in the daily
briefing artifact instead of being restated by hand.
