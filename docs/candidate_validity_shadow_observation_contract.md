# P8-12 Candidate Validity Shadow Observation Contract

## Purpose

This artifact accumulates real Dynamic Clock candidate timing samples before
any candidate-validity window is ratified. It is an observation mechanism,
not an investment rule.

- Current contract: `candidate_validity_shadow_observation/4`
- Mode: `PROVISIONAL_SHADOW_OBSERVATION_ONLY`
- Validity policy: `UNRATIFIED_NO_CANDIDATE_VALIDITY_WINDOW_AUTHORITY`
- Candidate outcome: always
  `NOT_COMPUTABLE_CANDIDATE_FRESHNESS_UNRATIFIED`

It cannot open Risk Capacity, P8-13 Entry Proposal, Stage, Buy, Action,
Order, Production, or trading authority.

## Inputs and PIT boundary

The only operational input is the already-built P8-12 Dynamic Clock report.
Every review candidate is independently revalidated through
`clock.review_candidate.validate_review_candidate()`. The observer then
enforces the date-level ordering:

`evidence_as_of <= trigger_observed_at <= candidate_updated_at <= decision_at <= observation_date`

and `candidate_created_at <= candidate_updated_at`.

An exact collector timestamp does not make the whole candidate timestamp-
precise. The upstream aggregate remains `time_precision=DATE_ONLY`, so the
observer exposes both:

- `pit_date_order_status=PIT_DATE_ORDER_VALID`; and
- `timestamp_order_status=NOT_COMPUTABLE_TIME_PRECISION`.

These are diagnostic facts, not freshness approval.

### Exact operational evaluation time in `/4`

The main operational workflow derives one UTC-second timestamp and its KST
`decision_date` from the same epoch. The exact instant is supplied to the
Python process as `--evaluation-at-utc`; the Python modules never read a wall
clock. It is retained at report, market, candidate, and shadow-observation
levels as `operational_evaluation`.

This timestamp means only that the current process evaluated the candidate at
that instant. It is not the historical time at which a date-only trigger first
became observable. Accordingly, `trigger_observed_at`, `decision_at`,
`candidate_created_at`, and `candidate_updated_at` remain `DATE_ONLY`, the
aggregate remains `time_precision=DATE_ONLY`, and freshness remains
`NOT_COMPUTABLE_CANDIDATE_FRESHNESS_UNRATIFIED`.

The validator rejects timezone-naive/noncanonical values, a timestamp whose
KST date differs from `decision_at`, and any evidence/price timestamp later
than the operational evaluation instant. Historical replay may not accept a
current operational evaluation timestamp.

Each `/4` observation also records
`source_dynamic_clock.evaluation_invariant_report_sha256`. It binds the full
source semantics after removing only the current-run evaluation context and
the hashes/precision member derived from it. Natural-sample accounting must
deduplicate on this value rather than `report_sha256`: two evaluations of
unchanged evidence remain separate operational records but only one evidence
basis. This prevents workflow retries or repeated manual dispatches from
inflating the validity-window sample population.

`/4` recursively removes the evaluation context from every occurrence in the
report before hashing. This is required because the in-memory report may share
one candidate object across multiple collections while a retained JSON source
contains independent copies. The invariant hash must therefore be identical
before and after canonical JSON round-trip. The first `/3` manual run (GitHub
run 32841777516) exposed this defect and its committed observation is
deliberately rejected by semantic revalidation; it is not a validity sample
and is never rewritten or counted.

## Review schedule versus candidate validity

The existing Dynamic Clock expiry is a provisional engineering re-review
schedule (`dynamic_clock_policy/1`, `PROVISIONAL_CIO_MVP`). The observer may
report whether a candidate is inside that schedule, but labels the result
`DIAGNOSTIC_ONLY_PROVISIONAL_POLICY`. It is never interpreted as an
investment-validity window.

The four trigger families for which the v2 design review found no retained
live sample remain explicit when their count is zero:

- `CATALYST_APPROACH`
- `EXPECTATION_DISLOCATION`
- `FLOW_REVERSAL`
- `FUNDAMENTAL_REVISION`

Their status is `UNVALIDATED_NO_LIVE_SAMPLE`. A later observed sample is
still only `PROVISIONAL_SHADOW_SAMPLE_ONLY`; it does not self-ratify a rule.

## Persistence

The `/2` and `/4` contracts persist two append-only, content-addressed files:

`evidence/operational/dynamic_clock/candidate_validity_observations/<decision-date>/observation-<observation-sha256>.json`

`evidence/operational/dynamic_clock/candidate_validity_source_reports/report-<source-report-sha256>.json`

- An identical run is a byte-identical no-op.
- A distinct same-day Dynamic Clock report creates a distinct file.
- The retained source report is exact canonical UTF-8 JSON with one trailing
  LF. Its filename, bytes, embedded hash, and the observation's source
  retention metadata must all agree.
- A manual and a natural run over the same report create different
  observations but share exactly one retained source report.
- A natural upstream `workflow_run`, a manual `workflow_dispatch`, and a
  local reproduction are labeled separately. The same report reached by two
  different trigger kinds is preserved as two different observations, so a
  manual proof can never be mistaken for a natural operational sample.
- An existing path with different bytes is a hard failure.
- No wall clock, provider request, future-return, MFE/MAE, account value,
  position size, or order field is read or emitted.

`load_and_validate_observation()` resolves the retained source path from the
observation, verifies exact canonical bytes and the content-addressed
filename, and rebuilds the entire observation. It deliberately does not read
the rolling `dynamic_clock_report.json`, which a later run overwrites.
Re-signing a modified observation, deleting the retained source, changing
only JSON whitespace, or replacing the source with different canonical JSON
therefore cannot turn a fail-closed status into an approved one.

The `/1` artifacts already committed before this contract revision remain
append-only historical records. They are not rewritten or deleted, but they
do not claim the self-contained source-retention guarantee introduced in
`/2`. The first `/2` operational run starts the independently rebuildable
series.

Existing `/2` sources remain independently revalidatable without invented
timestamps. A retained source that predates the new report field continues to
rebuild as `/2`; a new source that explicitly carries the evaluation context
rebuilds as `/4`. No historical source is upgraded or backfilled. The single
committed `/3` artifact remains append-only evidence of a rejected validation
attempt, not a sample.

## Exit boundary

This contract only starts the sample-collection phase. P5-06/P7-08 and P8-13
remain blocked until enough natural operational samples exist, a candidate
validity policy is reviewed and explicitly ratified, and the ratified
contract is separately coded and revalidated. All trading authority remains
false.
