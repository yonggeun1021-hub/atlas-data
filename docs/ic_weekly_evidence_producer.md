# Weekly evidence report producer

`audit.ic_weekly_producer` produces a new diagnostic report from a caller-pinned public
commit. `audit.ic_weekly_control` remains the packet checker/formatter; the existing
`ic-weekly-control.yml` and local Saturday seed rollover remain unchanged.

## Scope and truth labels

This bounded slice advances Decision Readiness by exposing which current public
inputs the CIO can actually inspect. It does not grade profitability, invent KPI
scores, create natural PAPER observations, or perform the CIO's analysis.

Four fixed public paths are read through Git objects, never working-tree files:
crypto runtime, market PIT acceptance, PAPER regime reference and capital-flow
reference. Only allowlisted status strings, normalized timestamps, paths and hashes
are published. Unknown fields (including any financial/account data) are discarded.
Source status is a **reported** diagnostic, not independently revalidated authority.

The report explicitly says `LATEST_SNAPSHOT_PER_SOURCE_NOT_FULL_WEEK_HISTORY`:
a current pointer cannot prove every observation in the week. Missing timestamps
remain `SOURCE_TIME_MISSING`; missing/invalid/prior-period/after-cutoff are separate.
The source timestamp is not an authenticated availability receipt. Thus
`source_available_at=null` and `SOURCE_AVAILABILITY_NOT_INDEPENDENTLY_PROVEN` remain;
this report must not be admitted as historical investment/PIT proof.

A commit dated after cutoff is refused. Source claims after cutoff are marked and
their status withheld. Git commit timestamps themselves are not publication receipts.
KPI deltas are all null/NOT_COMPUTABLE until an independently specified comparable
baseline producer is connected. A changed input is not automatically a KPI improvement.

Existing `produce`/`projection` are reused. There is one CREATED routing entry, with
no surfaced/acknowledged/decided evidence. `AWAITING_CIO_ANALYSIS` and `NOT_DELIVERED`
are intentional. No auto-queued actions, authority changes, model calls, API keys,
server installation or direct-main publisher are added. The installer mismatch
identified in the CIO queue is outside this change.

## Local production

```sh
python3 -m audit.ic_weekly_producer --source-commit <40-hex-public-commit> \
  --period-start <explicit-time-with-zone> --cutoff <explicit-time-with-zone> \
  --output-dir <evidence-output-directory>
```

The evidence key includes period/cutoff/commit/source hashes. Same key reuses the
original report byte-for-byte including generation time; changed input gets a new
key, not overwrite. Corrupt/conflicting existing files are refused. Output is JSON;
it is a diagnostic evidence report ready for CIO review, not a finished investment
opinion. Git failures are errors rather than fabricated missing-evidence results.

## Execution and retention

`ic-weekly-producer.yml` has a dedicated PR contract test job. On explicit manual
workflow_dispatch, a separate `produce` job collects and uploads the report as an
artifact with contents:read only. Its success means artifact creation, not human
receipt, final CIO analysis, Portal delivery or permanent retention.

No schedule is activated. Recommend GitHub weekly initiation only **after** CIO
sets the period boundary/acceptable delay and reconciles ownership with the existing
external weekly IC. Explicit cutoff must remain stable even when the job starts late.
A future schedule adapter must derive the agreed period, not use late wall-clock time
as a substitute cutoff. Server scheduling is not needed for this diagnostic slice;
if chosen later, it requires the separate server approval described in the queue.

Artifacts expire: the CIO must retrieve and retain the exact report/hash in the
existing controlled evidence process before expiry. This slice supplies the bundle;
a final analyzed/surfaced report and durable publication remain explicit follow-up,
not automatically marked complete. No private holdings, quantities, cash or PnL are
emitted or committed by this producer.

## Verification

`python3 validation/tests/test_ic_weekly_producer.py` exercises immutable reads,
private-field omission, time guards, actual Git inputs, idempotent saves, conflicting
outputs and fresh-process CLI reruns. Existing 26 control tests must still pass.
The production workflow is distinct from the unchanged checker workflow; no schedule,
model invocation, repository write or server activation occurs during PR CI.
