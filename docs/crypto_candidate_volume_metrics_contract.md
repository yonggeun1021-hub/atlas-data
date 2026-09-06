# Crypto Candidate Volume Metrics Contract (P5-08)

Status: implemented calculation capability. It computes observation
statistics only. It changes no P5-08/P5-09 emitted packet, no criterion
evaluator, no production wiring, and no candidate authority.

## Missing capability this closes

Validated P4-07 finalized candles already retain real base volume
(`candle_acc_trade_volume`) and KRW turnover (`candle_acc_trade_price`), but
`universe/crypto_candidate_promotion.py::evaluate_volume_liquidity` reads only
evidence-family presence and trade count. It computes neither a volume
baseline nor a latest-to-baseline ratio. `universe/crypto_candidate_volume_metrics.py`
adds exactly that, and nothing else.

## Non-duplication

`discovery/market_behavior.py` (P3-07) already publishes the two volume
features — latest volume divided by the prior window's arithmetic mean, and
by its median, under a 50-digit `Decimal` context. That arithmetic is now a
single source-independent public helper in that same module,
`volume_baseline_features(prior_volumes, latest_volume)`, called by both:

- P3-07's own unchanged `_window_features`, and
- this P5 calculator.

The extraction preserves the existing 50-digit operation order, mean/median
semantics, zero-baseline behavior, output rendering and packet bytes. P3-07's
source contract is untouched: its CRYPTO source registry still accepts only
`kraken_public_api`, and no Upbit data is ever presented to it. P3-12 already
computes 30-day turnover and admission thresholds; none of that is
recalculated or re-ratified here.

## Input contract

```
build_volume_metrics(market_evidence_packet, market, evaluation_as_of, calculation_contract)
validate_volume_metrics(metrics, original_packet, original_contract, original_evaluation_as_of)
```

`calculation_contract` uses schema `crypto_candidate_volume_calculation/1`:

```json
{
  "schema_version": "crypto_candidate_volume_calculation/1",
  "prior_finalized_candle_counts": {"1d": 20, "4h": 30}
}
```

- Both timeframes' prior finalized candle counts are **required, explicit,
  exact positive integers**. There is no default, no fallback, no automatic
  window selection and no tuning. `bool` is not accepted as an integer.
- Both base volume and KRW turnover, each with both mean and median ratios,
  are always reported, so no metric is silently chosen for the caller.

Source: one already-built P4-07 `upbit_market_evidence_packet/1`. It is
revalidated through the existing P5-08 consumer boundary
(`crypto_candidate_promotion._validate_market_evidence_packet`, reused, not
restated), which enforces the exact schema, payload hash, P4 ratified or
honestly unratified policy pin, timestamp ordering, timeframe set, market
identity, counts and all-false authority.

Selection: for each timeframe, exactly the latest finalized candle plus the
explicitly requested number of immediately preceding finalized candles. No
new TTL/PIT rule, freshness threshold or gap calendar is invented — the
existing P4 policy/finality/freshness result is consumed as-is.

## Output contract

Schema `crypto_candidate_volume_metrics/1`, per timeframe `CALCULATED` or
`UNAVAILABLE`, with `base_volume` and `quote_turnover` blocks:

| Field | Meaning |
|---|---|
| `latest` | Latest finalized candle's value. |
| `prior_mean` / `prior_median` | Baseline over exactly the requested prior window. |
| `latest_vs_prior_mean` / `latest_vs_prior_median` | Ratios, or `null`. |
| `baseline_status` | `OBSERVED` or `ZERO_BASELINE_UNKNOWN`. |

Numbers use the already-existing P3-07 serialization
(`market_behavior_radar_contract.json`'s `output_decimal_places`); no new
precision default is selected here.

### Two distinct null shapes

- **`ZERO_BASELINE_UNKNOWN`** — the timeframe *was* calculated, but a zero
  mean or median denominator makes that one ratio unknown. The ratio is
  `null`, never `0` and never infinity. `latest`, `prior_mean` and
  `prior_median` are still present, and the timeframe status stays
  `CALCULATED`.
- **timeframe `UNAVAILABLE`** — the source evidence itself is unusable:
  non-PASS P4 evidence status (stale, gap, duplicate, no finalized candle) or
  insufficient finalized history for the requested window. Its metric blocks
  and window are `null`, with named reasons
  (`EVIDENCE_STATUS_NOT_PASS:...`, `INSUFFICIENT_FINALIZED_HISTORY:have/need`).

A timeframe that is unavailable never suppresses a healthy timeframe: the
healthy one retains its own complete metrics. Overall `status` is
`CALCULATED` only when both timeframes are calculable, otherwise
`UNAVAILABLE`, with reasons prefixed by their timeframe.

Malformed input is a third thing again and **rejects** rather than reporting
`UNAVAILABLE`: an inconsistent schema, hash, market identity or candle time
ordering, and any non-string, non-finite or negative number, raises
`CryptoCandidateVolumeMetricsError`.

## Validation

`validate_volume_metrics` requires the original evaluation date as an
independently supplied argument, binds it to the recorded evaluation date,
and re-binds the source-packet and calculation-contract digests to the
*original* inputs. It rebuilds the whole derivation using that original date
for canonical comparison. Editing the evaluation date, a ratio, baseline
status, window endpoint or calculation status and then rehashing the output
cannot pass. No input argument is mutated by either function.

## Authority boundary

Every `status` emitted here is a **calculation** status, never a candidate
`PASS`/`FAIL` and never a promotion state. The packet's `authority` block
reuses P5-08's key set with every value hardcoded `false`. This capability
adds no threshold, no default period, no TTL/PIT rule, no source choice, and
no candidate, entry, sizing, Stage, Production, order or trading authority.
Existing P5-08 criterion outputs and production paths are unchanged.

## Regression

`test/test_crypto_candidate_volume_metrics.py`, registered in `run_all.py`'s
`APPROVED_TESTS`. Every evidence packet in that suite is built by the real
`build_market_evidence_packet` under the exact ratified P4 policy.
