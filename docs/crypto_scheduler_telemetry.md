# Crypto Breadth and Leadership scheduler telemetry

`P1-CR-06 Crypto Breadth Daily Capture` writes an operations-only record for
each workflow run to:

```text
data/operations/crypto_breadth_capture_runs/{UTC_DATE}/run-{RUN_ID}-attempt-{N}.json
```

The record exposes the GitHub event, cron expression, run identity, first
observed runner time, schedule delay, Kraken capture/skip/failure outcome,
P1-CR-06 validation outcome, and P1-CR-07 replay outcome. A read-only observer
can therefore distinguish scheduled and manual execution without GitHub
Actions API access.

Telemetry does not call Kraken or read market payloads. It is not a Breadth or
Leadership observation, does not establish data readiness or ranking, and has
no Regime, Production, or trading authority. Raw snapshots retain their
append-only path and transient factor outputs remain Actions artifacts only.
