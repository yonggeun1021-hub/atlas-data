# Crypto Candidate Price Distance Metrics Contract (P5-08 observation capability)

Status: observation capability implemented, deliberately unwired. This module
adds the two price-distance measurements P5-08 did not have. It does **not**
add an overextension rule, a bound, a predicate, or any promotion/eligibility
rule, and it does not resolve U2. Those remain unratified.

## Why this exists

`universe/crypto_candidate_promotion.py::evaluate_overextension` returns
`UNKNOWN` / `NO_RATIFIED_OVEREXTENSION_THRESHOLD` because no mechanical
overextension definition is ratified anywhere in this repository. That stays
exactly as it is — this capability does not change it, is not imported by it,
and is not wired into it.

What was missing was not the predicate; it was the *measurement*. The merged
trend calculator (`universe/crypto_candidate_trend_metrics.py`, PR603)
publishes a latest close and a latest EMA per timeframe, but nothing reports
how far the close sits from that EMA, or what the close did over an explicitly
requested number of candles. `outputs/P5_POLICY_DECISION_MATRIX.md`'s U2 names
EMA distance / ATR distance / period increase as *unresolved predicate
choices*; this module computes two of the described quantities and leaves the
predicate selection exactly as unresolved as it found it.

## The two observations

Per timeframe, both reported as **fractions**:

```
close_to_ema_fraction        = (latest_close - latest_ema)  / latest_ema
lagged_close_return_fraction = (latest_close - lagged_close) / lagged_close
```

A fraction is not a percentage. `0.1` means one tenth. The module never
multiplies by 100, never emits a `%`-suffixed string, and never compares either
value to anything. A positive, negative or large fraction is a mathematical
fact about candles — never a verdict, a candidate, an entry, a `PASS`/`FAIL`,
or a bound.

## What the module is not

`status` is only ever `CALCULATED` or `UNAVAILABLE`. Every payload carries the
same hardcoded authority block the trend calculator emits (it is reused, not
restated):

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
caller-supplied value can change any of the rest.

### Integration boundary

`build_price_distance_metrics` / `validate_price_distance_metrics` are a
directly callable public calculation API and nothing else. Nothing is wired
into `evaluate_overextension`, `evaluate_trend`, the P5-08
`crypto_candidate_promotion_packet/2` or P5-09
`crypto_paper_buy_eligibility_packet/2` emitted packets, any runtime config,
workflow, registry, scheduler, or natural collection. No threshold is defined,
read, or compared against anywhere in the module, so no consumer can extract
one from it. Binding these numbers to a decision requires a separate,
explicitly-ratified policy change that this module cannot make.

## Reused, not reimplemented

| Concern | Reused from |
|---|---|
| EMA series, seed methods, `alpha` recursion | `crypto_candidate_trend_metrics.compute_ema_series` (called through `build_trend_metrics`) |
| `latest_close` / `latest_ema` | the trend calculator's own emitted values, consumed verbatim |
| Decimal context and quantization | `crypto_candidate_trend_metrics._calculation_context` / `_quantize` |
| Candle finality / ordering / positive-close guards | `crypto_candidate_trend_metrics._finalized_closes` |
| Packet schema, identity, hash, policy pin, all-false evidence authority | `crypto_candidate_promotion._validate_market_evidence_packet` (via the trend module) |
| P4-07 `evidence_status` / `freshness` / duplicate / gap results | consumed as prerequisites, exactly as the trend module consumes them |
| Status vocabulary, authority block, integer/`bool` guards | the trend module's constants and helpers |

The EMA is **not** implemented a second time, and no existing dependency is
edited. No source, fetch, TTL, point-in-time rule, calendar, or freshness
threshold is introduced.

## Input 1 — `crypto_candidate_price_distance_calculation/1`

Every field is required on every call. There is no default, no fallback, and no
committed populated parameter file. An omitted, null, boolean, non-integer,
out-of-range or unknown field is a hard error. Unknown extra keys are rejected
at both levels, so an approval-flavoured label cannot be smuggled into the
parameter block and echoed back.

| Field | Type | Constraint |
|---|---|---|
| `schema_version` | int | exactly `1` |
| `contract_version` | str | `crypto_candidate_price_distance_calculation/1` |
| `trend_calculation_contract` | object | a complete `crypto_candidate_trend_calculation/1`, validated by the trend module itself |
| `timeframes` | object | exactly the keys `1d` and `4h` |
| `timeframes.<tf>.return_lag_candles` | int | `>= 1`, explicit per timeframe |
| `fraction_output_scale` | int | `0..36` decimal places for every emitted fraction |

### Both `return_lag_candles` values are explicit and digest-bound

Each timeframe names its own lag. There is deliberately no shared value, no
fallback and no chosen default: an omitted `4h` lag is an error, never the `1d`
lag reused. `1` is a valid caller-selected input, not a default — the module
supplies nothing when the field is absent.

Both lags live inside the single `calculation_contract`, together with the
complete EMA parameter set they are calculated against, and
`calculation_contract_sha256` digests that whole object. Changing either lag
alone therefore changes the digest and produces a different payload: new
lineage, never a rewrite of previously-emitted evidence.

### Why `fraction_output_scale` is separate

A fraction is a different quantity from a price. Silently reusing the trend
contract's price `output_scale` for it would be an invented choice (at
`output_scale = 4`, a fraction of `0.00012345` would be published as `0.0001`),
so the caller must state the fraction scale explicitly. Arithmetic itself runs
under the trend contract's already-declared `decimal_precision` and
`decimal_rounding`; no new numeric policy is introduced.

## Input 2 — the P4-07 market-evidence packet

The packet is validated by the **existing** P5-08 validator through the trend
module, which already pins the packet schema, market identity,
`payload_sha256`, all-false evidence authority, timeframe set, per-timeframe
candle identity and counts, `as_of`/`captured_at` ordering against
`evaluation_as_of`, and the exact ratified-vs-proposed P4 policy binding.

## Computation

`latest_close` and `latest_ema` are the trend calculator's own emitted
(quantized) values, so both sides of `close_to_ema_fraction` are exactly the
numbers that module already stands behind. `lagged_close` is read from the same
finalized rows — `return_lag_candles` source candles back from the latest,
counted in candles, not in EMA series positions — and quantized under the
identical declared context at the same price `output_scale`, so numerator and
denominator are rounded the same way. Each fraction is then computed inside the
declared Decimal context and quantized at `fraction_output_scale`.

Time endpoints are preserved: each timeframe reports
`latest_finalized_close_time` and the `lagged_close_time` actually used.

Outputs per timeframe: `timeframe`, `return_lag_candles`,
`finalized_candle_count`, `latest_finalized_close_time`, `lagged_close_time`,
`latest_close`, `latest_ema`, `lagged_close`, `close_to_ema_fraction`,
`lagged_close_return_fraction`, `close_to_ema_denominator_status`,
`lagged_close_return_denominator_status`.

The payload additionally binds `calculation_contract` +
`calculation_contract_sha256`, the `source` summary, and the full embedded
`trend_metrics` + `trend_metrics_sha256` (which itself embeds the exact source
packet).

## Independent resolution and partial results

Each of the four observations is resolved **independently**. A 4h evidence
failure never blanks a healthy 1d measurement, and an insufficient return lag
never blanks that same timeframe's close-to-EMA measurement. Source or history
unavailability stays unavailable; healthy observations stay reported. A single
withheld observation is nonetheless enough to set the whole payload to
`UNAVAILABLE`, so no consumer can read `CALCULATED` as "all four present" when
it is not.

### Named denominator statuses

| Value | Meaning |
|---|---|
| `AVAILABLE` | the denominator was non-zero and the fraction is reported |
| `ZERO_DENOMINATOR_LATEST_EMA` | the emitted `latest_ema` was exactly zero; fraction is `null` |
| `ZERO_DENOMINATOR_LAGGED_CLOSE` | the emitted `lagged_close` was exactly zero; fraction is `null` |
| `SOURCE_UNAVAILABLE` | this timeframe's evidence or history did not support the observation; fraction is `null` |

A close is always positive (a non-positive or non-finite close raises), but the
*emitted* EMA or lagged close can still quantize to exactly zero at a coarse
`output_scale`. That is a real, reachable input — not an impossibility — so it
is never divided by, never silently treated as a missing row, and never
guessed. The affected observation is `null` with its named status; the other
observation stays reported.

## Two fail-closed modes

Neither mode ever guesses a value, and neither can produce `CALCULATED`.

**raise** — the inputs themselves are malformed, inconsistent or tampered: an
invalid price-distance or nested trend contract; an invalid packet schema,
market identity, policy pin or `payload_sha256`; an unfinalized row; rows that
are not strictly increasing in close time; a close time at or before its own
open time; a non-positive, non-finite or float-typed close. These are the
trend/P4-07 guards, reused as-is.

**`UNAVAILABLE`** — the inputs are well-formed but do not support some
requested observation. `unavailable_reasons` lists the exact causes:

| Reason | Meaning |
|---|---|
| `<tf>:DUPLICATE_CANDLE_ROWS` | P4-07 reported `duplicate_row_count > 0` (inherited verbatim) |
| `<tf>:CANDLE_GAP` | P4-07 reported `gap_count > 0` (inherited verbatim) |
| `<tf>:CANDLE_NOT_FRESH:<status>` | P4-07 freshness is not `FRESH` (inherited verbatim) |
| `<tf>:EVIDENCE_STATUS_NOT_PASS:<status>` | P4-07 `evidence_status` is not `PASS` (inherited verbatim) |
| `<tf>:INSUFFICIENT_FINALIZED_CANDLES_FOR_SEED` | inherited verbatim from the trend module |
| `<tf>:BELOW_MIN_FINALIZED_CANDLES` | inherited verbatim from the trend module |
| `4h:INSUFFICIENT_EMA_SERIES_FOR_LAG` | inherited verbatim from the trend module |
| `<tf>:INSUFFICIENT_FINALIZED_CANDLES_FOR_RETURN_LAG` | fewer than `return_lag_candles + 1` finalized closes |
| `<tf>:ZERO_DENOMINATOR_LATEST_EMA` | named zero denominator |
| `<tf>:ZERO_DENOMINATOR_LAGGED_CLOSE` | named zero denominator |

Only the last three are new vocabulary. Every upstream reason is re-emitted
verbatim and only for its own timeframe; the other timeframe's reasons are
never inherited. When a timeframe's evidence was refused upstream, its return
is withheld rather than computed off rejected evidence — that would be a new,
weaker source rule.

## Independent validation of the ORIGINAL inputs

`validate_price_distance_metrics(metrics, *, market, evaluation_as_of,
market_evidence_packet, calculation_contract)` takes every trusted input as its
own mandatory keyword-only argument. **None of them is ever read out of the
untrusted output.** The market, evaluation date, P4-07 packet and calculation
contract must be supplied by the caller from their own original source, and the
output's corresponding claims are then compared *against* them.

This is deliberate: the volume review found that a self-rehashed evaluation
date could pass a validator that rebuilt from an output-supplied date. Here
there is no signature path by which the output could supply its own trusted
parameters, and the regression asserts that mechanically (all four arguments
are keyword-only with no default).

Validation performs, in order:

1. exact output key set, schema version, `status` domain, `source` key set and
   authority block;
2. `payload_sha256` recomputation;
3. market and evaluation date compared against the **supplied originals**;
4. the supplied original contract re-validated, digested, and compared against
   both `calculation_contract_sha256` and the embedded contract;
5. `trend_metrics_sha256` binding, then the embedded trend payload re-derived
   by the trend module's **own** `validate_trend_metrics` against the supplied
   packet — the EMA lineage is checked by the module that owns it;
6. full re-derivation of this payload from the supplied originals, compared
   byte-for-byte in canonical form.

Step 6 is why a self-rehash does not work: editing the evaluation date, the
market, a lag, a fraction, a denominator status or the status and recomputing
every embedded digest still fails, because a re-signed output cannot supply the
originals it is being checked against.

## Determinism

Every function is a pure function of its arguments: no wall clock, no
randomness, no network, no mutation of any caller-supplied object. The same
packet plus the same contract always yields a byte-identical payload.

## Verification

`test/test_crypto_candidate_price_distance_metrics.py` runs the arithmetic
against hand-derived examples whose steps are written out in each test
docstring — a rising 1d series (`(110-100)/100 = 0.1`), a falling 4h series
(`(60-80)/80 = -0.25`), per-timeframe independent lags, a non-terminating
`20/90` under two rounding modes, and fraction-scale behaviour. It covers
reachable zero denominators for both the EMA and the lagged close, source
unavailability (duplicates, gaps, staleness), history unavailability at the
exact lag boundary, healthy independent/partial results within and across
timeframes, the reused malformed-source raises, and self-rehashed
original-input substitutions for the market, evaluation date, lag, fraction,
status, denominator status, embedded trend payload and authority block.

Fixtures come from the merged trend suite's own P4-07 packet builder, which
uses the real `build_market_evidence_packet` against the committed ratified P4
policy; the calculator is never mocked to demonstrate positive math. The
unchanged EMA tests are reused, not repeated. The suite also asserts that
`evaluate_overextension` still returns `UNKNOWN` /
`NO_RATIFIED_OVEREXTENSION_THRESHOLD`, that the promotion module does not
import this capability, and that the module body names no threshold, verdict or
percentage scaling at all.

## Residual policy

U2 — and U1/U3/U4 — remain unratified. No overextension bound, predicate,
timeframe choice, ATR method, pullback rule, relative-strength peer choice,
TTL/PIT policy, promotion/entry output, policy value, source authority, legacy
packet, private runtime, workflow, registry, scheduler, or existing production
module is edited by this capability.
