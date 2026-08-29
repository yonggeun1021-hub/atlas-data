# Crypto PAPER Buy Eligibility Contract (P5-09)

Status: fail-closed pure derivation implemented. The current repository
cannot produce a real `PAPER_BUY_ELIGIBLE` row: P5-08 cannot itself produce a
genuine `FOCUSED_REVIEW` row while the Regime aggregate is restricted to
`UNKNOWN` (pending P1-COM-05), so P5-09's only real input population is
empty. This is the intended, inherited boundary, not a bug or a request to
synthesize missing market judgment.

## State machine

```
FOCUSED_REVIEW (P5-08)
    -> WATCH               (a gating criterion is UNKNOWN, none FAILED)
    -> BLOCKED             (a gating criterion FAILED)
    -> WAIT                (every gating criterion PASSED, but the order
                              draft is not yet fully computable)
    -> PAPER_BUY_ELIGIBLE  (every gating criterion PASSED AND the order
                              draft has no null/UNKNOWN field)
```

Transition rule:

1. any gating criterion `FAIL` -> `BLOCKED`;
2. otherwise any gating criterion `UNKNOWN` -> `WATCH`;
3. otherwise (every gating criterion `PASS`): `ORDER_DRAFT_COMPLETE ==
   PASS` -> `PAPER_BUY_ELIGIBLE`; otherwise -> `WAIT`.

`ORDER_DRAFT_COMPLETE` (criterion 7) deliberately never participates in the
`BLOCKED`/`WATCH` gate — it only decides `WAIT` vs `PAPER_BUY_ELIGIBLE` once
every other criterion has already passed. `PAPER_BUY_ELIGIBLE` is a
deterministic eligibility judgment, never Stage/Buy/Action/Order/
Production/Trading authority. Every row and packet keeps
`investable_eligible`, `paper_eligible`, `paper_buy_eligible_authorized`,
`entry_authorized`, `stage_authorized`, `production_authorized`,
`trading_authorized`, `order_authorized`, and `exchange_order_authorized`
hardcoded `false`.

## Source-consumption boundary

`build_eligibility_packet()` takes ONE already-built P5-08
`crypto_candidate_promotion_packet/2` and nothing else network- or
capture-shaped. It calls `crypto_candidate_promotion.py::validate_output()`
first — full re-derivation from that packet's own embedded
universe/regime/market-evidence/leadership sources — before reading
anything out of it. Only rows whose (revalidated) `promotion_state ==
"FOCUSED_REVIEW"` are evaluated; every other row is dropped before this
module's own nine criteria ever run. A caller cannot fabricate a
`FOCUSED_REVIEW` row by hand-editing a cached P5-08 packet.

The embedded P5-09 policy is also compared byte-semantically with the
repository policy on every build and validation. Replacing a threshold,
rebuilding the output, and recomputing its hash is rejected with
`POLICY_REPOSITORY_PIN_MISMATCH`.

These corrections are published as
`crypto_paper_buy_eligibility_packet/2` with
`crypto_paper_buy_eligibility_contract/2`; cached v1 output is not silently
reinterpreted under the stronger semantics.

Two additional, clearly-optional inputs widen what P5-09 can compute
without ever becoming required for a valid packet:

- `paper_account_state` — a caller-supplied virtual PAPER NAV +
  currently-open PAPER positions snapshot, used only for the
  `PAPER_RISK_BUDGET` criterion and PAPER quantity/planned-loss math. When
  omitted, that criterion and the order draft's quantity/fee/planned-loss
  fields resolve `UNKNOWN`/`None`.
- `fee_rate` — a caller-supplied assumption, matching P10-11's own
  "`CALLER_SUPPLIED_FEE_RATE...NO_DEFAULTS`" discipline. No internal
  default fee is ever invented.
- `known_idempotency_keys` — an optional prior-keys set for the duplicate
  sub-check; omitted means "not checked," never "assumed novel."

## Nine criteria

| # | Criterion | Current interpretation |
|---|---|---|
| 1 | `FOCUSED_REVIEW_UPSTREAM` | Ratified/deterministic. Echoes P5-08's already-revalidated `promotion_state`. Raises (never guesses) if a non-`FOCUSED_REVIEW` row is ever handed to the per-candidate evaluator directly. |
| 2 | `REGIME_PERMITS_ENTRY` | UNKNOWN by construction. Delegates directly to `crypto_candidate_promotion.evaluate_regime()`; the Regime aggregate authorizes only `"UNKNOWN"` today (P1-CR-08), so this can never be PASS via real evaluation until P1-COM-05 ratifies. |
| 3 | `TRIGGER_TIMEFRAME_ALIGNMENT` | Mechanical, PAPER-baseline. Requires finalized 15m and 1h directions to confirm upward together, with no conflicting 4h/1d direction (two-close comparison, same primitive P5-08 already established as mechanical). Missing/flat input is UNKNOWN; a downward 15m/1h leg or downward 4h/1d direction is FAIL. |
| 4 | `BREAKOUT_OR_PULLBACK` | Mechanical, PAPER-baseline for Breakout only (20x 1h-bar high broken + volume ≥ 1.5x the lookback median, per `PROPOSED_PAPER_BASELINE`). Pullback stays UNKNOWN forever — the policy doc's "EMA20 부근" (near EMA20) has no numeric proximity tolerance anywhere, ratified or proposed, so it is never fabricated. A disjunctive criterion with one undecidable leg resolves UNKNOWN, not FAIL, when the decidable leg (Breakout) does not itself PASS. |
| 5 | `INDEPENDENT_PRICE_VOLUME_EVIDENCE` | Ratified/deterministic. Structural presence only — candle family present across all four timeframes AND orderbook+trades family present — same discipline as P5-08's `VOLUME_LIQUIDITY`. |
| 6 | `NO_BLOCKER_STALE_OVERHEAT_DUPLICATE` | Composite worst-of four sub-checks. `MATERIAL_BLOCKER` echoes P5-08 exactly (FAIL only on active Upbit caution, else UNKNOWN — no coverage). `OVEREXTENSION` echoes P5-08 exactly (UNKNOWN — no ratified "과열" definition anywhere). `FRESHNESS` covers 15m/1h/4h/1d candles, trades, and orderbook: it is UNKNOWN while P4-07's freshness policy is unratified, FAIL when a ratified input is STALE, and PASS only when every required component is FRESH. `DUPLICATE` is PASS/FAIL only when a prior-keys ledger is supplied by the caller, else UNKNOWN. |
| 7 | `ORDER_DRAFT_COMPLETE` | PASS/UNKNOWN only (never FAIL). Whether entry zone, invalidation price, planned stop, quantity, fee rate/amount, assumed slippage, planned loss, expiry, next-review time, and duplicate-guard key are ALL non-null. Does not participate in the BLOCKED/WATCH gate. |
| 8 | `PAPER_RISK_BUDGET` | Mechanical, PAPER-baseline, against a caller-supplied virtual PAPER account snapshot. Checks projected total/single-asset PAPER exposure and concurrent-position count against `PROPOSED_PAPER_BASELINE`'s 5%/2%/3-position caps. Existing total exposure is the sum of `portfolio_weight_nav_fraction`, not planned-loss fractions. UNKNOWN when no snapshot is supplied — no NAV is known. |
| 9 | `ZERO_ORDER_ENDPOINT_CALLS` | Constant PASS. A structural invariant of this module (no network import anywhere in `universe/crypto_paper_buy_eligibility.py`), not a per-candidate fact; verified by `test_module_source_has_no_network_import`. |

These are conjunctive gates (criteria 1-6, 8, 9); criterion 7 only selects
`WAIT` vs `PAPER_BUY_ELIGIBLE` once the gate has already cleared.

## On `PROPOSED_PAPER_BASELINE`

The canonical Crypto policy doc gives criteria 3/4/8's numeric thresholds
as an explicit, versioned, "effective" PAPER-only comparison baseline
(`config/crypto_paper_buy_eligibility_policy.json`) — distinct in kind from
P3-12's/P4-07's own still-`PROPOSED_UNRATIFIED`/`PROPOSED_PAPER_BASELINE_
UNRATIFIED` internal operational policies, and explicitly **not** a
live-capital limit ("이 숫자들은 PAPER 비교용이지 실거래 한도가 아니다").
This module treats that doc as the deterministic source for exactly those
numbers (EMA period, breakout lookback/volume ratio, PAPER risk %s) and the
entry-zone/planned-slippage assumption reuses P3-12's own
`max_estimated_paper_slippage_bps` directly (numerically identical to the
policy doc's 0.30% figure) rather than re-deriving it. Using these numbers
for PAPER-simulation-only arithmetic never grants investment, entry, Stage,
order, Production, or Trading authority. A criterion with genuinely no
numeric source anywhere (`OVEREXTENSION`, the Pullback leg's EMA20
proximity tolerance, P4-07's own staleness policy) is never guessed into a
threshold — it stays UNKNOWN. In particular, an unratified P4-07 policy can
never turn an observed freshness label into PASS.

## Why `PAPER_BUY_ELIGIBLE` is unreachable now

`REGIME_PERMITS_ENTRY` and the `OVEREXTENSION` leg of criterion 6 are
hard-capped short of a real PASS today — both are direct echoes of
already-published P5-08/P1-CR-08 boundaries this module does not own and
must not reinterpret. `test/test_crypto_paper_buy_eligibility.py`'s
`EndToEndReachabilityTests` proves reachability two ways: (a)
`aggregate_state()` against a synthetic all-PASS criteria dict, mirroring
P5-08's own `test_all_pass_yields_focused_review`; and (b) a full
`evaluate_candidate()` run with every criterion this module itself owns
(trigger alignment, breakout, evidence independence, order-draft
completeness, PAPER risk budget, duplicate-guard) genuinely computed from
synthetic evidence, with only the two upstream-boundary leaf functions
(`crypto_candidate_promotion.evaluate_regime` /
`evaluate_overextension`/`evaluate_material_blocker`) mocked past their
documented ceiling. `ProductionEmptyTests` separately confirms that real,
unmocked P5-08 output today (even under a hypothetical near-term P3-12
ratification) still yields zero `FOCUSED_REVIEW` rows because of `REGIME`
alone, hence zero P5-09 candidates — the correct, expected state, not a bug
to work around.

## Determinism and safety

Every function in this module is a pure function of its arguments: no
wall-clock or random value is read inside any derivation, and the
duplicate-guard key is computed from evidence identity (market, evaluation
date, trigger candle close time, entry/invalidation price), never a random
UUID. No result-shopping: thresholds are fixed in the loaded
`PROPOSED_PAPER_BASELINE` policy file before evaluation and are never
adjusted after seeing an outcome. The module makes zero network,
credential, order, or withdrawal calls — it never imports `urllib`,
`requests`, `socket`, or any HTTP client, verified by
`test_module_source_has_no_network_import`.
The complete policy object is exact-pinned to the repository copy on both
build and consumer validation, so a rehashed embedded-policy substitution
is rejected.

## Wiring shape toward P10-11 / P7-13

`build_order_draft()`'s fields (`quantity`, `entry_zone` -> a BUY limit
price, `fee_rate`, `expires_at`, `duplicate_guard_key` -> P10-11's
`idempotency_key`/P9-04's idempotency token shape) are deliberately named
and shaped to match `shadow/crypto_paper_simulator.py::build_intent()`'s
kwargs, so that turning a `PAPER_BUY_ELIGIBLE` row into an actual PAPER BUY
intent is a mechanical field mapping, not a schema translation exercise.
`portfolio/crypto_paper_exit_manager.py` (P7-13) is one step further
downstream still: it consumes a P10-11 `account_state` produced *after* an
intent has been submitted and filled, not a P5-09 eligibility row directly
— P5-09's output is upstream of P10-11's simulator, which is upstream of
P7-13's exit plan. No schema conflict was found; no code in this PR calls
either module.
