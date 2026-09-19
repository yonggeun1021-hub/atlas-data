# Public workflow byte-pin inventory (bounded read-only audit)

`python3 audit/public_workflow_pin_inventory.py --root .` reads config JSON and
reports sibling `*workflow_path`/`*workflow_sha256` pins, including the four plain
pairs in `regime_source_owner_registry_v2.json` and its prefixed natural-receipt
pair. Missing targets, malformed sources and missing digests do not disappear.
An intentional path-only reference is labeled UNPINNED_REFERENCE_DECLARED_NON_COVERAGE,
not a forged broken hash. Nothing is repinned or dispatched. Exit 1 means attention
is required, not that a server run should be blocked. The command is not attached
to scheduled workflows; only its regression tests run in the existing CI suite.

This borrows the recursive inventory approach of private PR225, but does not copy
its trigger parser or assert that file discovery proves a validator is executed.
The inventory's DECLARED_NON_COVERAGE lists trigger/path-filter semantics, actual
validator invocation, non-workflow pins, historical pins/overlays and server pins.

STALE_PIN means bytes differ from a recorded SHA256. It is not a compatibility
verdict and does not override an approved amendment. On main cb5bffc22782a1d5230b7625b1c3aa8f9dcae35e,
the US free-market-data pin differs; the existing
`free_market_data_source_owner_amendment_v1.json` must be consulted by its owner.
Four matching hashes are not proof of complete public coverage.

## Server warning aggregation, with explicit evidence boundary

Optional `--telemetry-jsonl FILE` accepts **normalized redacted** rows:
`workflow_path`, `pinned_sha`, `live_sha`, `decision` (drift_compatible,
drift_blocked or match), `observed_at` (UTC ISO). Git SHA1 or SHA256 is accepted,
but both hashes must have the same length. Unknown fields/raw lines are not echoed.
This is not a raw server-log parser or automatic server collector. Original records
must be retained privately and normalization audited separately.

Groups retain compatible/blocked counts separately, changing live hashes and stale
observation counts. They label mismatches STALE_PIN_REVIEW_REQUIRED even if all
source decisions say compatible. No repeated warning is suppressed and no pin is
refreshed. A source-reported compatible event does not prove semantic compatibility.

CIO's 2026-09-19 report of compatible22/blocked0 and two Upbit live revisions is a
supplied operational observation, not independently queried by this patch. A
synthetic 22-row regression proves the aggregation behavior only. Frozen
stablecoin workflow, server runtime and pin records are untouched. Any scheduled
consumer, notification policy or pin refresh requires a separate CIO decision.
