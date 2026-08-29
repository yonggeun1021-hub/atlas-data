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
