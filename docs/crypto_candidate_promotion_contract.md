# Crypto Candidate Promotion Contract (P5-08)

Status: fail-closed pure derivation implemented. The current repository
cannot produce a real `FOCUSED_REVIEW` row: P3-12's universe policy and
P4-07's market-evidence policy are unratified, the Regime aggregate is
restricted to `UNKNOWN`, and several required candidate criteria have no
ratified transform or complete evidence family. This is the intended
boundary, not a request to synthesize missing market judgment.

## State machine

```
TRADEABLE_UNIVERSE / PAPER_ELIGIBLE (P3-12)
    -> WATCH             (one or more criteria UNKNOWN, none FAIL)
    -> FOCUSED_REVIEW    (all eight criteria PASS)
    -> BLOCKED           (one or more criteria FAIL)
```

The transition rule is exact:

1. any `FAIL` -> `BLOCKED`;
2. otherwise any `UNKNOWN` -> `WATCH`;
3. otherwise all `PASS` -> `FOCUSED_REVIEW`.

`FOCUSED_REVIEW` is only a review-queue label. Every row and packet keeps
`investable_eligible`, `paper_eligible`, `focused_review_authorized`,
`entry_authorized`, `stage_authorized`, `production_authorized`,
`trading_authorized`, and `order_authorized` hardcoded `false`.

## Source-consumption boundary

`crypto_candidate_promotion_packet/2` embeds the complete four-source input
set under `source_packets`:

- P3-12 universe packet;
- CRYPTO Regime output;
- per-market P4-07 evidence packets;
- P1-CR-07 leadership output, when supplied.

The builder validates each supplied source before reading it. In particular,
the P3-12 packet must match its complete emitted schema, content hash,
summary, false authority boundary, current local policy/taxonomy versions and
ratification state. A caller cannot fabricate an in-scope row while the local
universe policy remains unratified. P4-07 evidence must match its schema,
content hash, policy pin, timestamp ordering, timeframes, market identity,
counts, and false authority boundary. Regime uses its authoritative
`validate_output()` implementation. Leadership is checked against the
ratified local policy/contract pins, window set, date boundary, and false
authority fields.

Downstream consumers, including P5-09, must call `validate_output()`. It
revalidates the embedded sources, rebuilds the complete derivation, and
requires byte-equivalent canonical output. Rehashing a modified cached state
or criterion cannot make it valid.

## Eight criteria

| Criterion | Current interpretation |
|---|---|
| `IDENTITY` | PASS only for an in-scope P3-12 row with a canonical asset ID. P3-12 source validation happens first. |
| `TRADABILITY` | PASS only for validated P3-12 `TRADEABLE_UNIVERSE`/`PAPER_ELIGIBLE` rows. No P5-08 threshold is invented. |
| `REGIME` | Always `UNKNOWN` while the Regime output contract authorizes only `UNKNOWN`. |
| `TREND` | `UNKNOWN`. Finalized 1d/4h close directions may be retained as observations, but there is no ratified candidate trend transform. A two-close comparison is not treated as an approval rule. |
| `RELATIVE_STRENGTH` | A non-positive ratified BTC leg is sufficient to `FAIL`. A positive BTC leg stays `UNKNOWN` because the required same-peer leg is unratified/missing. |
| `VOLUME_LIQUIDITY` | `UNKNOWN`. Evidence-family presence is coverage, not confirmation; the P4-07 policy thresholds are unratified. |
| `OVEREXTENSION` | `UNKNOWN`; no ratified chase/overextension definition exists. |
| `MATERIAL_BLOCKER` | Active Upbit caution is sufficient to `FAIL`. No caution still stays `UNKNOWN` because security-incident and network-outage coverage is absent. |

These are conjunctive gates. A known failing leg may block a candidate even
when another leg is unknown; a known passing subset never promotes through a
missing required leg.

## Why `FOCUSED_REVIEW` is unreachable now

At the current repository state, genuine P3-12 output contains no in-scope
candidate because its policy is unratified. Even after that policy is
ratified, Regime, trend, peer relative strength, volume/liquidity,
overextension, and complete event-blocker coverage must each become ratified
and measured before all eight criteria can pass. `aggregate_state()` has a
synthetic all-PASS unit test only to prove the state machine itself; the
production builder cannot manufacture that input.

## Scope boundary versus P5-09

P5-08 stops at `WATCH`/`FOCUSED_REVIEW`/`BLOCKED` plus auditable reasons. It
does not compute an entry zone, invalidation, stop, quantity, fee/slippage,
planned loss, risk headroom, expiry, next review time, duplicate-guard key,
or `PAPER_READY`. Those belong to P5-09, which must consume a successfully
revalidated P5-08 packet and must remain closed when there is no genuine
`FOCUSED_REVIEW` row.

## Determinism and safety

The derivation reads no wall clock or random value. The same validated source
packets and `evaluation_as_of` produce byte-identical output. Kraken's
cross-exchange label is display-only and cannot affect criteria or state.
The module adds no capture job, network request, private endpoint, order,
withdrawal, Production, Trading, or REAL path.

## Contract/3 (opt-in): ratified P4-07 reader and Crypto regime wiring

`crypto_candidate_promotion_contract/3`
(`config/crypto_candidate_promotion_contract_v3.json`, sha256-pinned in
code) is requested explicitly with
`build_promotion_packet(..., contract_version=3, crypto_runtime_decision=...)`
and emits `crypto_candidate_promotion_packet/3`, whose `source_packets` also
embed the consumed `crypto_paper_runtime_decision/1` packet (or `null`).
Contract/2 remains the default. Its output is byte-identical to the
pre-contract/3 code, because published Crypto PAPER decision packets are
re-derived byte-for-byte by the pinned private runtime. No production caller
requests contract/3 yet.

Contract/3 changes exactly two criterion evaluators and the state rule. The
evaluators:

| Criterion | Contract/3 interpretation |
|---|---|
| `REGIME` | The user-ratified `CRYPTO_PAPER_RUNTIME_V1` decision in force at the P1-CR-08 envelope's `generated_at` (07:00Z UTC decision boundary). It is mapped through `PAPER-MARKET-ALLOCATION-V2-20260913` (record sha256 `345801ab…`). `RISK_ON` gives PASS with new buys `PERMIT` and multiplier 1.00. `NEUTRAL` gives PASS with `PERMIT_SELECTIVE` and 0.70. `RISK_OFF` gives FAIL with `DENY` and 0.25. `STRESS` gives FAIL with `DENY` and 0.00. `UNKNOWN` gives UNKNOWN with `DENY` and holdings capped at 0.50. A missing decision is UNKNOWN. So is a decision for an earlier UTC decision date (no carry) and a decision whose ratified policy fails local validation. A decision evaluated after the reference instant is rejected as lookahead. A tampered `decision_id`, identity, authority, or a KNOWN regime on a non-accepted decision is rejected. A valid KNOWN value never raises. |
| `VOLUME_LIQUIDITY` | Reads only `config/upbit_market_evidence_policy_ratified.json`, bound by `packet_sha256` through the P4-07 contract. The proposal file is never read on this path. PASS requires three things. The packet must be bound to the ratified policy and captured inside its effective window. The 1d/4h candle and trade evidence must be PASS. The orderbook must be FRESH with full ratified depth and the ratified slippage notional, and spread/slippage recomputed from the packet numbers must be within `max_spread_bps_normal`/`max_slippage_bps_normal`. Any breach or non-PASS evidence is UNKNOWN with named reasons, following P4-07 `fail_closed_unknown`; it is never FAIL. An absent or invalid ratified policy is UNKNOWN. |

`TREND`, `RELATIVE_STRENGTH`, `OVEREXTENSION` and `MATERIAL_BLOCKER` use the
same evaluators as contract/2.

### State rule: `RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1`

This rule comes from user ratification B2
(`evidence/authority/paper_b2_b3_size_assembly_user_ratification_20260915.json`,
sha256 `0e2691e0…`). The six conditions come from
`CANDIDATE-PIPELINE-REBUILD-20260913` `t2_minimum_conditions`
(`evidence/authority/candidate_pipeline_rebuild_user_ratification_20260913.json`,
sha256 `6870b457…`). Both records are hash-verified when contract/3 loads.

Promotion blocks **only** on these six required conditions, emitted per row under
`t2_required_conditions`:

| Condition | PASS when | Otherwise |
|---|---|---|
| `T2_IDENTITY` | P3-12 ratified identity is resolved | UNKNOWN |
| `T2_POPULATION_MEMBERSHIP` | The row is in scope in the validated P3-12 population snapshot for the evaluation date | Row is not evaluated |
| `T2_LIQUIDITY` | The ratified Upbit `min_30d_avg_krw_turnover` (P3-12 policy) was met | UNKNOWN if turnover is missing |
| `T2_PRICE_DATA` | The finalized 1d candle closing at the reference day 00:00Z is present, with `available_at` ≤ reference | UNKNOWN |
| `T2_ROTATION_MEMBERSHIP` | The asset's bucket is `STRONG_CONFIRMED`/`STRONG_HELD` (`RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1`) | **UNKNOWN today**: no confirmation source is wired into P5-08 (`unapplied_rules` names it) |
| `T2_REGIME_PERMITS_NEW_BUYS` | The REGIME criterion above is PASS | FAIL (RISK_OFF/STRESS) or UNKNOWN |

State: any FAIL → `BLOCKED`, otherwise any UNKNOWN → `WATCH`, otherwise
`FOCUSED_REVIEW`.

The eight named criteria are still emitted for lineage. Five of them never
change the state and appear in `warnings` when not PASS:

- `TREND` and `OVEREXTENSION` are record-only entry-stage features (`RULE.ENTRY.PAPER_BASELINE_B.V1`).
- `RELATIVE_STRENGTH` is a score.
- `VOLUME_LIQUIDITY` (P4-07 spread/slippage) is a quality warning.
- `MATERIAL_BLOCKER` is a warning. This includes active Upbit caution flags. The Upbit investment warning is still excluded upstream by P3-12.

Each row carries additive `rule_refs` in the shape
`{rule_id, version, registry_sha256 (null until the rule registry is on main), source_record_sha256, role}`.
The role is `BLOCKED_BY` when that rule blocked the row.

The multipliers are lineage for later sizing only. Every authority field
stays false.
