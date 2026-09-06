# Crypto Candidate Trend Metrics Contract (P5-08 calculation capability)

Status: calculation capability implemented, deliberately unwired. This module
adds the EMA arithmetic P5-08 did not have. It does **not** add a trend rule, a
promotion rule, or an eligibility rule, and it does not resolve U1/U2/U3/U4 or
any trend-policy selection. Those remain unratified.

## Why this exists

Before this module there was no EMA computation anywhere in the candidate
pipeline:

- `universe/crypto_candidate_promotion.py::_direction` compares only the two
  most-recently-finalized closes of one timeframe;
  `evaluate_trend` records those two directions and always returns
  `UNKNOWN` / `NO_RATIFIED_CANDIDATE_TREND_RULE`.
- `universe/crypto_paper_buy_eligibility.py` validates `trend.ema_period == 20`
  in its baseline policy file but contains no EMA computation; its timeframe
  alignment reuses `PROMOTION._direction`.
- A narrow search of the existing `universe/`, `regime/` and `microstructure/`
  sources found no reusable EMA implementation. The private EXIT basic adapter
  is out of scope and is not duplicated here.

## What the module is not

`status` is only ever `CALCULATED` or `UNAVAILABLE` — never `PASS`, `FAIL`,
`BUY`, `FOCUSED_REVIEW`, or `PAPER_BUY_ELIGIBLE`. Every payload carries a
hardcoded authority block:

```
calculation_only                 true
investment_policy_ratified       false
candidate_promotion_authorized   false
buy_authorized                   false
order_authorized                 false
exchange_authorized              false
real_capital_authorized          false
production_authorized            false
trading_authorized               false
```

`calculation_only` is the only true flag the module may emit, and no
caller-supplied value can change any of the rest. A positive comparison is a
mathematical fact about candles; it grants nothing.

### Integration boundary

`build_trend_metrics` / `validate_trend_metrics` are a directly callable public
calculation API and nothing else. The calculator is **not** wired into
`evaluate_trend`, the P5-08 `crypto_candidate_promotion_packet/2` or P5-09
`crypto_paper_buy_eligibility_packet/2` emitted packets, any runtime config,
workflow, registry, scheduler, or natural collection. Binding these numbers to
a decision requires a separate, explicitly-ratified policy change that this
module cannot make. Neither production module imports it, and both keep their
existing results byte-identical on their existing input paths — including on
evidence where this calculator reports `CALCULATED` with both comparisons
`true`.

## Input 1 — `crypto_candidate_trend_calculation/1`

Every field is required on every call. There is no default, no fallback, and
no committed populated parameter file. An omitted, null, boolean, non-integer,
out-of-range or unknown field is a hard error, never a silently-chosen number.
Unknown extra keys are rejected too, so an approval-flavoured label cannot be
smuggled into the parameter block and echoed back.

| Field | Type | Constraint |
|---|---|---|
| `schema_version` | int | exactly `1` |
| `contract_version` | str | `crypto_candidate_trend_calculation/1` |
| `timeframes` | object | exactly the keys `1d` and `4h` |
| `timeframes.<tf>.ema_period` | int | `>= 2` |
| `timeframes.<tf>.seed_method` | str | a supported seed method |
| `timeframes.<tf>.min_finalized_candles` | int | `>= 1` (explicit history floor) |
| `rising_lag_bars` | int | `>= 1` (bars back on the 4h EMA series) |
| `decimal_precision` | int | `1..60` significant digits for the recursion |
| `decimal_rounding` | str | a supported Decimal rounding mode |
| `output_scale` | int | `0..36` decimal places for every emitted value |

Supported seed methods (explicitly-selected mathematical algorithms, never
implicitly-selected policy; an omitted or unsupported value is rejected):

- `FIRST_FINALIZED_CLOSE` — seed is the first finalized close; the series
  starts at index `0`.
- `SMA_FIRST_PERIOD_FINALIZED_CLOSES` — seed is the simple average of the first
  `ema_period` finalized closes; the series starts at index `ema_period - 1`.

Supported rounding modes: `ROUND_CEILING`, `ROUND_DOWN`, `ROUND_FLOOR`,
`ROUND_HALF_DOWN`, `ROUND_HALF_EVEN`, `ROUND_HALF_UP`, `ROUND_UP`,
`ROUND_05UP`.

Numeric values supplied by a caller or a test are calculation inputs. They are
not a ratification of those numbers.

## Input 2 — the P4-07 market-evidence packet

The packet is validated by the **existing** P5-08 validator,
`crypto_candidate_promotion._validate_market_evidence_packet`, which already
pins the packet schema, market identity, `payload_sha256`, all-false evidence
authority, timeframe set, per-timeframe candle identity and counts,
`as_of`/`captured_at` ordering against `evaluation_as_of`, and the exact
ratified-vs-proposed P4 policy binding. No new source, fetch, TTL or
point-in-time interpretation is introduced, and no freshness threshold is
invented: P4-07's own `evidence_status`, `freshness.status`,
`duplicate_row_count` and `gap_count` are consumed as prerequisites.

## Computation

For an explicit period `n`, `alpha = 2 / (n + 1)` and

```
ema[i] = close[i] * alpha + ema[i-1] * (1 - alpha)
```

Every arithmetic step runs inside an explicit Decimal context
(`decimal_precision` significant digits, `decimal_rounding`), and every emitted
level is quantized to `output_scale` decimal places. The comparisons are made
on the exact emitted values, so the published numbers justify the published
booleans and the recursion is reproducible by hand.

Only finalized 1d/4h candles are used. The original market, open/close times,
`as_of`/`captured_at` and the source packet digest are preserved.

Outputs per timeframe: `finalized_candle_count`, `first_finalized_close_time`,
`latest_finalized_close_time`, `latest_close`, `seed_index`,
`ema_series_length`, `latest_ema`; the 4h block additionally carries
`rising_lag_bars`, `lagged_ema` and `lagged_ema_close_time`.

Comparisons (both strict, both purely mathematical, neither a threshold):

- `daily_close_above_daily_ema` — latest finalized daily close `>` latest daily EMA.
- `four_hour_ema_rising` — latest 4h EMA `>` the 4h EMA `rising_lag_bars` back.

## Two fail-closed modes

Neither mode ever guesses a value, and neither can produce `CALCULATED`.

**raise** — the inputs themselves are malformed, inconsistent or tampered:

- an invalid or incomplete calculation contract;
- an invalid packet schema, market identity, policy pin or `payload_sha256`;
- a row inside `finalized_candles` whose close time has not elapsed as of the
  packet's own `as_of` (i.e. not finalized), even in a self-consistent packet;
- rows that are not strictly increasing in close time (which also covers
  duplicated close times), or a close time at or before its own open time;
- a non-positive, non-finite, or float-typed close price.

**`UNAVAILABLE`** — the inputs are well-formed but P4-07's own evidence quality
or coverage does not support the requested calculation. The failing timeframe's
metrics are `null`; healthy-timeframe metrics remain reported. Both comparisons
are `null`, and `unavailable_reasons` lists the exact causes:

| Reason | Meaning |
|---|---|
| `<tf>:DUPLICATE_CANDLE_ROWS` | P4-07 reported `duplicate_row_count > 0` |
| `<tf>:CANDLE_GAP` | P4-07 reported `gap_count > 0` |
| `<tf>:CANDLE_NOT_FRESH:<status>` | P4-07 freshness is not `FRESH` |
| `<tf>:EVIDENCE_STATUS_NOT_PASS:<status>` | P4-07 `evidence_status` is not `PASS` |
| `<tf>:INSUFFICIENT_FINALIZED_CANDLES_FOR_SEED` | fewer closes than the selected seed needs |
| `<tf>:BELOW_MIN_FINALIZED_CANDLES` | fewer closes than the explicit `min_finalized_candles` |
| `4h:INSUFFICIENT_EMA_SERIES_FOR_LAG` | EMA series shorter than `rising_lag_bars + 1` |

Reasons are per timeframe, so stale 4h evidence does not suppress an otherwise
fresh 1d block's own reporting. A single failing timeframe is nonetheless
enough to set the whole payload to `UNAVAILABLE` and to null **both**
comparisons, so no consumer can read a partial `true`. The failing timeframe's
metrics are null; the healthy timeframe's metrics are still reported, which is
what makes the reason list explainable rather than opaque.

## Determinism, lineage and tamper refusal

Every function is a pure function of its arguments: no wall clock, no
randomness, no network, no mutation of any caller-supplied object. The same
packet plus the same contract always yields a byte-identical payload.

The output binds `calculation_contract_sha256`, the source packet identity and
`source.payload_sha256`, and embeds the full source packet.
`validate_trend_metrics` therefore:

1. pins the output key set, schema version, `status` domain and authority block;
2. recomputes `payload_sha256`;
3. recomputes the calculation-contract digest and the source-packet digest;
4. optionally requires a supplied packet to be exactly the embedded one;
5. **re-derives the entire calculation** from the embedded exact sources and
   requires byte-equivalent canonical output.

Step 5 is why a self-rehash does not work: editing a metric, a comparison, the
status, or a parameter and recomputing `payload_sha256` — even recomputing the
contract digest as well — still fails, because the stored metrics no longer
match what those exact sources and parameters actually produce. Changing a
parameter produces a different contract digest and a different payload: new
lineage, never a rewrite of previously-emitted evidence.

## Verification

`test/test_crypto_candidate_trend_metrics.py` proves the arithmetic against
hand-derived examples whose steps are written out in each test docstring, for
both seed methods, non-default periods, rising/falling/flat data, history and
lag edges, and rounding-mode/`output_scale` behaviour (including an exact
`15.66665` tie that resolves differently under `ROUND_HALF_EVEN` and
`ROUND_HALF_UP`). Fixtures are built with the real P4-07
`build_market_evidence_packet` against the committed ratified P4 policy; the
calculator is never mocked to demonstrate positive math. Integration tests
import the actual `PROMOTION.evaluate_trend` and P5-09
`evaluate_trigger_timeframe_alignment` and assert their original results,
their unchanged field sets, and an unmutated input packet, on the very packet
where this calculator reports `CALCULATED` with both comparisons `true`.

## Residual policy

U1/U2/U3/U4 and trend-policy selection remain unratified. No policy value,
source authority, legacy packet, private runtime, workflow, registry,
scheduler, or existing production module is edited by this capability.
