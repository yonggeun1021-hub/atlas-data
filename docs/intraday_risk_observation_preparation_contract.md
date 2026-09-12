# P9-05 Intraday Risk Observation Preparation Contract

## Purpose and authority boundary

`execution/intraday_risk_observation_preparation.py` prepares one normalized
observation row for the existing `intraday_risk_observation_batch/3` consumer.
It is deterministic, policy-neutral, and observation-only. It does not select a
provider or market session, ratify a lookback or threshold, evaluate risk,
schedule a run, generate an action or order, or authorize production/REAL use.

The caller must explicitly provide all four selections: source/session profile,
`15m` elapsed timeframe, `PRIOR_MEAN` or `PRIOR_MEDIAN`, and a positive integer
prior comparable-session count. The repository supplies no operational default.
Only `15m` is accepted because that is the common completed-bucket contract
currently supported by the three source profiles in scope.

## Source and session profiles

The contract binds each source profile to an exact existing source contract and
SHA-256. It also pins the existing P9-05 consumer contract, metric formula
implementation, and reused volume arithmetic source, so drift fails closed.
US regular and early-close sessions use `America/New_York` with IANA
DST validation and require a date-specific official calendar upstream. Korea
uses `Asia/Seoul` and likewise requires a date-specific official calendar;
special/unknown sessions remain fail-closed.

`UPBIT_UTC_DAY` is a caller-selected Upbit provider convention anchored at UTC
00:00. It is not asserted as a universal crypto-market session fact. Another
crypto provider/session boundary requires a new explicit source/session profile
contract; it must never be inferred from this profile.

The module consumes volumes only from contiguous, completed 15-minute bars.
For Upbit this corresponds to `candle_acc_trade_volume`. The rolling ticker
field `acc_trade_volume_24h` is not a session cumulative and is outside this
contract. KIS real-time cumulative volume is also outside this preparation
contract because the existing qualification does not prove an exhaustive,
gap-ledgered completed-bar stream.

## Expected volume to elapsed time

For each prior comparable session, cumulative volume is the sum of the exact
contiguous bar prefix from that session's explicit open through the current
session's completed elapsed-bucket count. `expected_volume_to_time` is the
caller-selected arithmetic mean or median of those prior cumulative values.
Arithmetic is reused from
`discovery.market_behavior.volume_baseline_features`, including its 50-digit
Decimal context.

All supplied prior sessions must use the same subject, market, source-compatible
session profile, and elapsed prefix, and their number must exactly equal the
caller's explicit count. No missing-bucket fallback, cross-session substitution,
or shorter-window fallback exists. Source availability after the observation
instant is rejected as future data. Availability earlier than the latest bar
close is also rejected because it cannot prove the supplied completed prefix.

If the selected denominator is zero, the receipt is `NOT_AVAILABLE`, its
baseline status is `ZERO_BASELINE_UNKNOWN`, and no consumer observation row is
emitted. Zero is not converted to a neutral relative-volume value or infinity.

## Fail-closed validation

The module rejects missing or duplicate buckets, non-15-minute buckets, a bar
whose close is later than the observation instant, future source availability,
wrong timezone offsets, DST inconsistencies, regular/early-close mismatches,
wrong provider-contract identity, crossed quotes, and non-finite or invalid
numeric strings. US early-close observations must explicitly select
`US_EARLY_CLOSE`; a 13:00 close under `US_REGULAR` is invalid.

Session timestamps carry an explicit offset and are checked against the IANA
timezone in the selected session profile. Consumer timestamps remain canonical
UTC `Z` timestamps. No wall clock is consulted.

## Metric naming

The existing wire name `DRAWDOWN_FRACTION` and its formula are unchanged:

`max(0, (reference_close - last_price) / reference_close)`

Its exact semantic display name is **Reference-close decline fraction**, with
semantic ID `REFERENCE_CLOSE_TO_LAST_DECLINE_FRACTION/1`. It is not true peak
drawdown, and this preparation layer neither calculates nor introduces a peak
drawdown field.

`RELATIVE_VOLUME_FRACTION` remains
`cumulative_volume / expected_volume_to_time`; this module only prepares the two
inputs under the explicit baseline definition above. Threshold direction and
threshold values remain solely in any separately ratified P9-05 policy input.
