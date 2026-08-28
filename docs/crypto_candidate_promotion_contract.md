# Crypto Candidate Promotion Contract (P5-08)

Status: pure-derivation classifier implemented. Every input this module
depends on is itself either unratified (P3-12's own tradeable-universe
policy, P4-07's market-evidence policy) or is contractually restricted to
`UNKNOWN` for the Regime aggregate (P1-CR-08/P1-COM-01). In production this
means the pipeline upstream of this module never actually produces any
`TRADEABLE_UNIVERSE`/`PAPER_ELIGIBLE` market for it to evaluate, and even if
one existed, its `REGIME` criterion could never resolve to anything but
`UNKNOWN` today. This is the expected, correct current state, not a bug --
the same "everything stays fail-closed until a human/CIO ratification
lands" pattern as every other module in this lineage.

## State machine

```
TRADEABLE_UNIVERSE / PAPER_ELIGIBLE (P3-12)
    -> WATCH            (one or more of the 8 criteria UNKNOWN, none FAILED)
    -> FOCUSED_REVIEW    (all 8 criteria PASSED)
    -> BLOCKED           (one or more criteria FAILED)
```

`promotion_state` is a review-queue classification, never an authority
grant. Every output row's `authority` block (`investable_eligible`,
`paper_eligible`, `focused_review_authorized`, `entry_authorized`,
`stage_authorized`, `production_authorized`, `trading_authorized`,
`order_authorized`) is hardcoded `false` in code, unconditionally, regardless
of `promotion_state`. Turning a `FOCUSED_REVIEW` classification into real
investable/PAPER/Stage/order authority is a separate, later,
explicitly-ratified change this module cannot make.

### Transition rule (exact)

Given the 8 per-criterion `PASS`/`FAIL`/`UNKNOWN` results:

1. If **any** criterion is `FAIL` -> `BLOCKED`, reason
   `CRITERIA_FAILED:<sorted failed criterion names>`. `FAIL` always wins,
   even when other criteria are simultaneously `UNKNOWN`.
2. Else, if **any** criterion is `UNKNOWN` -> `WATCH`, reason
   `CRITERIA_UNKNOWN:<sorted unknown criterion names>`.
3. Else (all 8 `PASS`) -> `FOCUSED_REVIEW`, reason `ALL_CRITERIA_PASSED`.

This rule (`aggregate_state()` in `universe/crypto_candidate_promotion.py`)
is a pure function of an already-computed criteria dict, tested directly
with a synthetic all-`PASS` input to prove the rule itself can reach
`FOCUSED_REVIEW` -- see "Why `FOCUSED_REVIEW` is unreachable today" below
for why no *real* evaluation reaches it yet.

Only markets already at P3-12 state `TRADEABLE_UNIVERSE` or `PAPER_ELIGIBLE`
are evaluated at all. A market still at `OBSERVATION_POOL` or P3-12
`BLOCKED` is out of this module's scope and simply does not appear in its
output -- `TRADEABLE_UNIVERSE` (P3-12's own label) remains the correct,
unmodified description of such a market; this module never re-emits or
reinterprets it as one of its own three states.

## Scope boundary vs P5-09

This module implements only the Notion policy doc's
`Universe -> Focused Review` step. It never computes `entry zone`,
`invalidation condition/price`, `planned stop`, `PAPER quantity`,
`fee`/`slippage` assumptions, `planned loss` vs. Crypto risk headroom,
`expiry`/`next review time`, or a `duplicate guard key` -- every one of
those fields is `Focused Review -> PAPER_READY`'s job, i.e. **P5-09 (Crypto
PAPER Buy Eligibility)**, the next WBS item. P5-08 stops at a
classification + reasons; P5-09 is expected to consume `FOCUSED_REVIEW`
rows from this module's output as its own input set.

## The 8 criteria: ratified/deterministic vs. UNKNOWN-by-construction

The WBS Exit Gate names 8 checks (identity, tradability, regime, trend, RS,
volume/liquidity, overextension, event blocker); the Notion policy doc's
`Universe -> Focused Review` bullets name 6. These reconcile one-to-one:
`IDENTITY` and `TRADABILITY` are already-passed P3-12 gates, surfaced here
for completeness; the remaining 6 map directly onto the Notion bullets.

| Criterion | Evidence basis | Can it ever be PASS/FAIL today? | Can it ever be FAIL? |
|---|---|---|---|
| `IDENTITY` | **Ratified/deterministic** -- reuses P3-12's own `ratified_identity_registry` gate (a market cannot reach `TRADEABLE_UNIVERSE` without a ratified `canonical_asset_id`). | Yes, always PASS for in-scope rows. | No (structurally unreachable given P3-12's own gating; `UNKNOWN` branch is defensive-only). |
| `TRADABILITY` | **Ratified/deterministic** -- reuses P3-12's turnover/spread/listing-history/capture-freshness gates verbatim. | Yes, always PASS for in-scope rows. | No (same reason as `IDENTITY`; out-of-scope rows raise instead, they are never silently mis-scored). |
| `REGIME` | **UNKNOWN by construction** -- `regime/output_contract.py`'s `runtime_authorized_regimes == ["UNKNOWN"]` for every market, pending P1-COM-05's minimum-coverage-gate ratification. No RISK_ON/NEUTRAL/RISK_OFF/STRESS value is readable anywhere in this repo. | No -- always `UNKNOWN` today. | No. |
| `TREND` | **Deterministic/mechanical** -- compares the two most-recently-finalized candles' close prices per timeframe (1d and 4h), from P4-07's already-hash-validated candle evidence. No EMA/lookback period is invented (N=1, the only parameter-free choice); this is intentionally narrower than `btc_trend.py`'s BTC-specific, ratified 200DMA convention, which has no generalization to arbitrary Upbit KRW markets. | Yes. | Yes -- when the two timeframes' directions are strictly opposite (`UP` vs `DOWN`). |
| `RELATIVE_STRENGTH` | **Ratified/deterministic, narrowed scope** -- uses only `crypto_leadership.py`'s ratified BTC-reference leg (`relative_strength_vs_btc`, PRIMARY 30-day window, `config/crypto_leadership_policy.json` `approval_status: RATIFIED`). The Notion text's peer-group leg ("동종 peer 대비") is **not** evaluated: `crypto_leadership`'s own `group_coverage_policy_status` is permanently `UNRATIFIED` (bucket/sector/chain classification is unauthorized). This is a documented, narrower-than-literal scope decision -- see "RS scope decision" below. | Yes. | Yes -- when the BTC-reference relative strength is `<= 0`. |
| `VOLUME_LIQUIDITY` | **Structural/deterministic** -- checks only that two independently-sourced evidence families exist (P4-07 candles = price; P4-07 orderbook + trades = volume/liquidity). Never reads the (currently `PROPOSED_UNRATIFIED`) spread/slippage/staleness NORMAL/ABNORMAL thresholds from `upbit_market_evidence_policy.json` -- those are unratified numeric cutoffs this module must not use for a PASS/FAIL judgment. | Yes (PASS/UNKNOWN only). | No real FAIL path with available evidence -- a missing family resolves to `UNKNOWN`, not `FAIL`, since absence of evidence is not itself proof of a blocking condition. |
| `OVEREXTENSION` | **UNKNOWN by construction** -- no mechanical or ratified definition of "과열·급등 추격" exists anywhere in this repository. Inventing one (an arbitrary % move or z-score cutoff) is exactly the kind of unratified-threshold guess this module must never make. | No -- always `UNKNOWN`. | No. |
| `MATERIAL_BLOCKER` | **Ratified/deterministic, partial coverage** -- reuses P3-12's captured Upbit `market_event.caution` flag (`market_event_caution_any`), an already-captured, deterministic, Upbit-native signal, not an invented threshold. Investment-warning-class listing blockers (`market_event.warning`) are already force-excluded upstream by P3-12, so that half of "유의종목·상장폐지" is structurally satisfied for every row this module receives. Security-incident/network-outage evidence has **no dedicated source anywhere in this repo** -- not independently checked; documented as a coverage gap, not fabricated as `UNKNOWN`. | Yes. | Yes -- when Upbit's own caution flag is active. |

### Why `FOCUSED_REVIEW` is unreachable today

Because `REGIME` is `UNKNOWN` by construction for every real evaluation
(see above), a real call to `build_promotion_packet()` can never produce a
row with all 8 criteria `PASS` -- the best achievable outcome today is
`WATCH`. This is a direct, correct implementation of the Notion policy
text's own words: *"Regime가 RISK_OFF/STRESS가 아니며 UNKNOWN이면 WATCH만
허용"* -- literally, Regime `UNKNOWN` caps promotion at `WATCH`. See
`test_crypto_candidate_promotion.py::RegimeCriterionTests` (asserts `REGIME`
is never anything but `UNKNOWN` for any valid `CRYPTO` regime payload) and
`AggregateStateTests::test_all_pass_yields_focused_review` (proves the
*transition rule itself* is capable of `FOCUSED_REVIEW`, using a synthetic
all-`PASS` criteria dict -- independent of whether today's evidence ever
supplies one).

### RS scope decision

The Notion text reads "BTC 및 동종 peer 대비 상대강도 양수" (relative
strength positive vs. both BTC and same-class peers). This module evaluates
only the BTC leg. The peer leg would require `crypto_leadership.py`'s
bucket/sector/chain group classification, whose `group_coverage_policy_status`
is permanently `UNRATIFIED` in `config/crypto_leadership_policy.json` --
there is no ratified per-asset peer-group assignment anywhere in this repo
to read. Per Step 1's own instruction ("use whatever exists, UNKNOWN if it
doesn't cover the specific candidate"), this module uses the ratified BTC
leg and does not block or force `UNKNOWN` on the criterion merely because
the peer leg is structurally unavailable -- unlike `REGIME`, where the
Notion text's entire condition is unratified, here a real, ratified,
per-asset measurement (`relative_strength_vs_btc`) does exist and is used.
This is a documented, narrower-than-literal scope decision, not a fresh
authority conflict requiring escalation.

## Determinism

`build_promotion_packet()` and every `evaluate_*` function are pure
functions of their arguments: no wall-clock or random value is read inside
the derivation math (only already-timestamped upstream fields, e.g.
`regime_payload["generated_at"]`, are echoed through). The same four inputs
always produce byte-identical output --
`test_crypto_candidate_promotion.py::BuildPromotionPacketTests::test_determinism_same_input_twice_identical_output`.

## Kraken invariant

Carried forward from P3-12: this module never reads
`kraken_cross_exchange_reference` anywhere in its criteria or state-machine
logic. `test_crypto_candidate_promotion.py::test_kraken_presence_never_promotes`
asserts an otherwise-identical run with that field `True` vs. `False`
produces an identical `criteria`/`promotion_state`.

## No new capture

This module takes already-built packets as plain function arguments (the
same "pure derivation over already-validated evidence" pattern as
`microstructure/upbit_market_evidence.py`). It adds no capture script, no
GitHub Actions workflow, and calls no network endpoint of any kind --
`0` real order/private endpoint calls, verified by inspection (no
`requests`/`urllib`/`http` import anywhere in `universe/crypto_candidate_promotion.py`).
