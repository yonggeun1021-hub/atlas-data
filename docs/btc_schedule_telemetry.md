# BTC capture scheduler telemetry

`BTC Price Daily Capture` writes one operations-only record per workflow run to:

```text
data/operations/btc_capture_runs/{UTC_DATE}/run-{RUN_ID}-attempt-{N}.json
```

The record exists so a read-only clone can distinguish a scheduled run from
`workflow_dispatch` even when the observer cannot access the GitHub Actions API.
It includes the event, cron expression, run identity, first observed runner time,
scheduled delay, capture/skip/failure result, and Trend/Risk validation outcome.

The telemetry is not market evidence and cannot establish data readiness, a BTC
trend/risk interpretation, Regime state, Production wiring, or trading action. It
does not call Kraken or read the captured OHLC payload.

Raw publication remains separately gated: a new snapshot is added to a commit only
when the capture result is `captured` and validation succeeds. A skipped or failed
run may publish its operations record, but it cannot publish a new raw snapshot.
Existing append-only snapshots and transient Trend/Risk artifacts retain their
prior contracts.
