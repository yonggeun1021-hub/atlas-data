# P0-02 Daily Collect scheduler telemetry index

Every Daily Collect run writes an immutable run record under
`data/operations/collect_runs/{KST_DATE}/run-{id}-attempt-{n}.json` and then
rebuilds the day's deterministic `index.json`.

The index records the exact file path and SHA-256, slot ID, run ID/attempt,
timing status, runner-start time, and Guard result for every observed run. It is
derived telemetry, not evidence that a missing slot did or did not fire.

Step 0 exposes the exact daily index through
`read_model_inventory.operations_telemetry_sources`. The index is deliberately
absent from `scheduled_collectors`: scheduler telemetry cannot make collector
data ready, stale, failed, or decision-eligible. Missing or malformed telemetry
also cannot change the readiness classification.
