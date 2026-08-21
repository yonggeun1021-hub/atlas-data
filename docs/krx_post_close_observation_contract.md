# KRX Post-Close Observation Contract (P0-04)

## Purpose

The 18:00 briefing may observe a Korean stock's same-day close and investor
flow without treating that row as final.  The next-morning KRX collection is
still the only path that confirms a prior session for SMA20, rules, regime, or
orders.

## Publication boundary

The post-close workflow publishes one immutable exact-date bundle:

```text
data/observations/krx_post_close/{KST_DATE}/
  index.json
  source.json
  symbols/{CODE}.json
```

It never writes either morning authority:

```text
data/{KST_DATE}/krx.json
data/latest_krx.json
```

The bundle is built in a sibling staging directory and renamed into place only
after every tracked symbol passes the contract.  A second publication for the
same date fails with `APPEND_ONLY_VIOLATION`.  Missing or partial responses go
to `data/incident/krx_post_close/{KST_DATE}/` and do not create a briefing
bundle.

## Symbol read view

Each `symbols/{CODE}.json` keeps the confirmed and observed timelines separate:

- `latest_trading_day`: last next-day-confirmed session
- `latest_observed_day`: exact post-close observation date
- `decision_boundary`: confirmed-only SMA20 metadata
- `observed_row`: same-day OHLCV and basic investor flow

`observed_row` always contains:

```json
{
  "observation_status": "observed_unconfirmed",
  "decision_eligible": false,
  "confirmed": false,
  "confirm_reason": "deferred_to_next_day",
  "trading_day": "YYYY-MM-DD",
  "observed_at_kst": "ISO-8601 with timezone",
  "source_snapshot_sha256": "sha256(source.json)"
}
```

OHLCV and investor flow exist only under `observed_row`; they are not copied
into a confirmed or decision field.  The consumer must first require the exact
KST date and `status=ready_observed_unconfirmed`, and must never convert
`decision_eligible=false` into `NEUTRAL`, a score, or an order input.

## Scheduling and failure semantics

The separate workflow has 16:05, 16:25, and 16:45 KST weekday opportunities.
The first complete bundle wins; later slots skip it.  The three slots handle
GitHub scheduler delay and source timing variance, while the exact-date and
append-only guards prevent duplicate publication.

Every runner writes a separate operations-only record to:

```text
data/operations/krx_post_close_runs/{KST_DATE}/run-{RUN_ID}-attempt-{N}.json
```

The record preserves the GitHub event and schedule, run identity, directly
observed runner start time, expected slot and delay, Guard result, and the
collector's captured/skipped/failed outcome.  A failed Guard prevents the
provider collection step from running.  Telemetry never confirms the observed
row and all briefing, regime, production, and trading authority flags remain
false.

No row is made final because the workflow ran after market close.  Missing
today rows, missing basic investor rows or columns, a partial stock response,
an invalid timestamp, or a broken confirmed-history boundary all fail closed
as `UNKNOWN` incident evidence.

## Operational exit gate

The read-only evening consumer is:

```text
briefing/krx_post_close.py
```

It validates the complete immutable bundle before exposing any value. A valid
bundle becomes a digest-bound `READY_OBSERVED_UNCONFIRMED` packet whose every
symbol visibly retains `Observed / Unconfirmed`, `decision_eligible=false`,
and the confirmed-only decision boundary. A missing, partial, or modified
bundle becomes `UNKNOWN`; symbol values and numeric counts are not substituted
with zero, `NEUTRAL`, or an action. The CLI writes only outside the repository
so the 18:00 observer cannot mutate capture or morning authority.

Code and offline regressions do not close P0-04.  Closure additionally requires
one real trading-day path from scheduled capture to the compact symbol view and
the 18:00 briefing, with the briefing visibly respecting
`decision_eligible=false`.
