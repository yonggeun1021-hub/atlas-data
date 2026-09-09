# Crypto candidate detail — explicitly requested trend calculations

`crypto_candidate_detail_view_enriched_trend/v1`

Owner module: `decision/crypto_candidate_detail_view.py`
(`build_enriched_trend_view`, `validate_enriched_trend_view`, CLI `--enriched-trend`)
Calculator reused unchanged: `universe/crypto_candidate_trend_metrics.py`
(`crypto_candidate_trend_calculation/1`, see
`docs/crypto_candidate_trend_metrics_contract.md`)

## What this is

The default candidate detail view (`crypto_candidate_detail_view/v1`) reuses
P5-08's own `TREND` criterion verbatim from a committed decision-snapshot row.
That criterion is a two-close direction fact; it carries no EMA levels and no
statement about whether the numbers behind it were even computable.

This capability answers a different, narrower question, and only when a caller
explicitly asks it:

> For **this exact already-taken decision**, what were the actual numeric trend
> observations over **that decision's own hash-bound P4-07 evidence**?

It is a read model. It adds numbers beside the existing view; it changes no
existing field, and it decides nothing.

## What this is not

* Not a trend rule, promotion rule or eligibility rule. Observation `status` is
  only ever `CALCULATED`, `UNAVAILABLE` or `NOT_REQUESTED` — never `PASS`,
  `BUY` or `FOCUSED_REVIEW`.
* Not a change to the default path. Without `--enriched-trend` (or a direct
  `build_enriched_trend_view` call), `build_view` and the CLI produce exactly
  the bytes they produced before this existed. The calculator module is loaded
  lazily, so the default path does not even import it.
* Not an authority grant. The enriched packet's `authority` block is all-false
  except `calculation_only`, the embedded view's authority block is unchanged
  and all-false, and each embedded metrics payload carries the calculator's own
  all-false authority. No Portal live release, workflow change, producer
  activation, new period/threshold, or promotion/entry/order/trading authority
  is created here.

## Required, explicit inputs — no defaults anywhere

| Input | Rule |
| --- | --- |
| `decision_packet_path` | Required. The immutable decision being explained. Verified by the existing `_verified_decision_entry` (path time basis, internal timestamp, generation id, payload hash). There is **no** latest-decision fallback on this path. |
| `evaluation_as_of` | Required. The caller's own original evaluation date (`YYYY-MM-DD`). Never derived from a source and never defaulted. |
| `trend_calculation_contracts` | Required. A map of market → complete `crypto_candidate_trend_calculation/1` contract. Every investment-shaped parameter (`ema_period`, `seed_method`, `min_finalized_candles`, `rising_lag_bars`, `decimal_precision`, `decimal_rounding`, `output_scale`) is supplied by the caller on every call. Supplying them is a calculation input, never a ratification of those numbers. |

## Source discipline

P4-07 evidence is resolved **only** through the decision's own
`upbit_market_evidence_packet` source reference, using the existing
hash-verified `_source_entry_from_decision` (single-role, path-containment and
exact-bytes checks). Unlike `build_view`, this path deliberately has **no**
`find_latest_market_evidence_packet` fallback: an old decision must never be
explained with newer numbers, and an unrelated latest packet is never picked up
even when one exists on disk.

The per-market packet is then handed to PR603's `build_trend_metrics`
unchanged, which re-validates the packet schema, identity, payload hash,
all-false evidence authority and its time basis against `evaluation_as_of`, and
which reports healthy-timeframe metrics even when the other timeframe is
`UNAVAILABLE`.

## Status semantics

| Status | Meaning |
| --- | --- |
| `CALCULATED` | The caller requested this market and the calculator produced complete metrics and both comparisons. |
| `UNAVAILABLE` | The caller requested this market, but the answer is withheld. Reason is either the calculator's own coverage/quality reasons (`unavailable_reasons`, verbatim), or `NO_DECISION_BOUND_P4_MARKET_EVIDENCE_SOURCE` (the decision bound no P4 reference at all) or `NO_DECISION_BOUND_P4_MARKET_EVIDENCE_PACKET_FOR_MARKET` (it did, but that packet does not cover this market). |
| `NOT_REQUESTED` | The caller supplied no contract for this market. Explicitly distinct from `UNAVAILABLE`: nothing was asked, so nothing was withheld. |

Every market in the embedded view gets an entry, so absence is always explicit.

## Hard rejections (fail closed, never softened into `UNAVAILABLE`)

| Condition | Error |
| --- | --- |
| No `decision_packet_path` | `ENRICHED_DECISION_PACKET_REQUIRED` |
| Malformed / non-calendar `evaluation_as_of` | `ENRICHED_EVALUATION_AS_OF_INVALID` |
| `evaluation_as_of` after the decision's own date | `ENRICHED_EVALUATION_AS_OF_FUTURE` |
| Contract map is not a mapping, or a key is not a non-empty string | `ENRICHED_TREND_CONTRACTS_INVALID` / `ENRICHED_TREND_CONTRACT_MARKET_INVALID` |
| Contract names a market this decision's universe never carried | `ENRICHED_TREND_CONTRACT_MARKET_UNKNOWN` |
| Incomplete, extra-field or otherwise malformed contract (validated up front, even when that market has no P4 data) | `ENRICHED_TREND_CONTRACT_INVALID` |
| Malformed, future-dated, identity-mismatched or otherwise corrupt bound P4 source | `ENRICHED_TREND_CALCULATION_REJECTED:<market>:<calculator error>` |
| Bound source bytes no longer match the decision's declared hash | `DECISION_SOURCE_BYTES_MISMATCH` (existing check, reused) |
| Partial explicit CLI argument set | `argparse` error; the missing arguments are named, never chosen |

## Output shape

```
schema_version            1
contract_version          crypto_candidate_detail_view_enriched_trend/v1
base_contract_version     crypto_candidate_detail_view/v1
evaluation_as_of          the caller's original date
decision_source           path / date / hhmm / generation_id / payload_sha256
trend_calculation_source  role / path / sha256 / snapshot_date  (null if unbound)
requested_markets         sorted markets the caller supplied a contract for
trend_calculations        market -> { market, status, reasons,
                                      calculation_contract_sha256, metrics }
view                      the unchanged crypto_candidate_detail_view/v1 result
authority                 all false except calculation_only
payload_sha256
```

`metrics` is PR603's own payload, embedded whole — including its
`calculation_contract`, `calculation_contract_sha256`, `source`, hash-pinned
`source_packet`, per-timeframe numbers, comparisons and `unavailable_reasons`.
The candidate criteria, funnel stages, blocker reasons, funnel counts, trigger
prerequisites and authority flags live in `view` and are reproduced byte-for-byte
from the default build.

## Validation

`validate_enriched_trend_view(enriched, *, decision_packet_path,
evaluation_as_of, trend_calculation_contracts, ...)` takes the originals as its
own arguments and reads **nothing** back out of the untrusted packet to decide
what that packet should have been. It checks shape, versions, authority and the
payload hash, then rebuilds the whole enrichment from those independent
originals and compares canonical JSON, then re-runs the calculator's own
`validate_trend_metrics` over each embedded metrics payload.

A caller who edits an emitted metric and recomputes every hash — the metrics
payload's and the enriched packet's — still fails with
`ENRICHED_DERIVATION_MISMATCH`, because a self-rehash is not a rederivation.
Supplying substituted originals (a different contract, date or decision than the
ones actually used) fails the same way.

## Determinism

Pure function of its arguments: no wall clock, no network, no randomness, no
mutation of caller-supplied objects. `generated_at` defaults to the decision
packet's own internal timestamp, so the same decision plus the same contracts
always yields a byte-identical payload.
