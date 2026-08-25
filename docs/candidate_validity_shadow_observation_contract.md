# P8-12 Candidate Validity Shadow Observation Contract

## Purpose

This artifact accumulates real Dynamic Clock candidate timing samples before
any candidate-validity window is ratified. It is an observation mechanism,
not an investment rule.

- Contract: `candidate_validity_shadow_observation/1`
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

Files are append-only and content-addressed:

`evidence/operational/dynamic_clock/candidate_validity_observations/<decision-date>/observation-<observation-sha256>.json`

- An identical run is a byte-identical no-op.
- A distinct same-day Dynamic Clock report creates a distinct file.
- A natural upstream `workflow_run`, a manual `workflow_dispatch`, and a
  local reproduction are labeled separately. The same report reached by two
  different trigger kinds is preserved as two different observations, so a
  manual proof can never be mistaken for a natural operational sample.
- An existing path with different bytes is a hard failure.
- No wall clock, provider request, future-return, MFE/MAE, account value,
  position size, or order field is read or emitted.

`validate_observation()` rebuilds the entire observation from the source
Dynamic Clock report. Re-signing a modified observation therefore cannot
turn a fail-closed status into an approved one.

## Exit boundary

This contract only starts the sample-collection phase. P5-06/P7-08 and P8-13
remain blocked until enough natural operational samples exist, a candidate
validity policy is reviewed and explicitly ratified, and the ratified
contract is separately coded and revalidated. All trading authority remains
false.
