# US Risk / Vol Sensor Contract (P1-US-05)

Status: transient-only data contract implemented; source, representative asset,
price basis, lookbacks, annualization, and session-calendar policy unratified;
no stress, Regime, Production, or trading authority.

## Purpose

This helper calculates non-reconstructive realized-volatility and drawdown
features from a US EOD price envelope held in memory or supplied on stdin.  It
does not download prices.  Vendor rows and prices are never returned, logged,
or written to a tracked file.

The helper reuses `atlas_price_pit_contract.py` rather than inventing a second
availability rule:

- `FORWARD_SHADOW` is accepted only after the 20:15 America/New_York
  qualification cutoff and only when the decision timestamp is at or after
  Atlas ingestion;
- present-day historical `RAW` is `CAUSAL_RESEARCH_ONLY`, never authoritative
  historical PIT;
- present-day historical `ADJUSTED` is `REVISED_SENSITIVITY_ONLY`.

The temporal contract issues `available_at` only for `FORWARD_PIT_QUALIFIED`
Forward Shadow inputs. For both historical backfill classes it deliberately
withholds `available_at`, because a present-day fetch does not prove when the
source made an old row available. `build_transform` records the Atlas
ingestion timestamp (`fetched_at`) in that case instead — it is the only
capture-time fact it has — but fails closed with
`AVAILABLE_AT_PRECEDES_OBSERVATION` if that timestamp would predate the
`observation_date` it is attached to. `validate_output()` independently
re-derives the same relation, plus every other structural relation the
non-reconstructive output retains (window ordering, status/eligibility
mapping, stress feature-vector cross-references, retention and authority
locks, lineage hash shapes), so a hand-edited or corrupted artifact cannot
silently claim availability before its own observation. It does not
recompute realized volatility or drawdown from raw closes, since those rows
are transient and not retained.

## Approval gate

`config/us_risk_input_policy.json` is deliberately `UNRATIFIED`.  The Atlas
SSOT does not yet choose all of the following:

- official/reproducible source;
- representative asset such as SPY or QQQ;
- raw or adjusted price basis;
- realized-volatility and drawdown lookbacks;
- annualization session count;
- authoritative exchange-session calendar source;
- historical split handling.

The helper refuses all transforms until a versioned policy makes every choice,
is effective for the full input window, and is separately approved.  The
fixture policy used in tests demonstrates code capability only and is not a
production policy.

## Input and coverage

The stdin/memory envelope contains source identity, temporal context, an exact
`expected_session_dates` list, and rows with `session_date`, `close`, and
`split_factor`.  Numbers must be decimal strings.  Dates must be ordered,
unique weekdays, and rows must match the expected session list exactly.  No
missing value is forward-filled or converted to zero.

Version 1 supports only `no_split_events_required`.  A split inside the
calculation window fails because an unadjusted price jump would masquerade as
market stress.  Split-aware as-of adjustment is a future, separately ratified
contract.

## Derived features

For a policy-selected number of close-to-close simple returns:

```text
annualized realized volatility
  = sqrt(mean(simple_return²) × annualization_sessions)
```

For the policy-selected close window, the helper emits current drawdown and the
maximum observed peak-to-trough drawdown.  It emits window dates and aggregate
features but no close, return series, or reconstructive vendor data.

The output is `AVAILABLE_UNCALIBRATED`.  Stress thresholds and classification
remain `UNRATIFIED` / `UNDEFINED`, and all Regime, Production, and trading
authorities remain false.

## Retention boundary

Tiingo Starter terms prohibit persistent vendor-data retention.  Accordingly:

- input is memory or stdin only;
- no input-file argument exists;
- no workflow or live API is wired;
- the output retains only aggregate derived features, policy/contract identity,
  session coverage, timestamps, and a non-reconstructive input hash;
- test rows are synthetic and live only in temporary fixtures.

## Offline command shape

After a policy is separately ratified, a transient producer may pipe an
in-memory envelope into:

```bash
producer-that-does-not-persist-vendor-data | \
  python3 .github/scripts/us_risk.py transform \
    --policy /tmp/ratified-us-risk-policy.json \
    --out /tmp/us-risk-derived.json
```

This change does not provide or authorize that producer.
