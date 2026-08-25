# Candidate Canonical Identity Observation /1

This contract gives read-only consumers one deterministic answer to a narrow
question: **which ratified canonical instrument and account scope, if any,
match each committed Dynamic Clock candidate's exact provider lineage?**

It revalidates every candidate with `validate_review_candidate()`, requires
the candidate's exact caller-supplied operational evaluation timestamp, and
delegates identity and scope resolution to `identity/canonical_identity.py`.
No ticker, market, path, or subject-name inference is allowed. Multiple source
pairs resolve only when every pair resolves to the same issuer, instrument,
and listing.

The output lives at
`evidence/operational/dynamic_clock/candidate_identity_observation.json` and is
rebuilt in the same workflow run as the Dynamic Clock report. It contains only
mechanical identity facts and explicit non-computable statuses.
The source-report lineage hash is explicitly a canonical-JSON hash; authority
document hashes are byte hashes of the exact committed files.

## Append-only operational history

Each operational invocation also retains the complete independently validated
packet at
`candidate_identity_observations/<decision-date>/<trigger-kind>/observation-<record-sha256>.json`.
The path separates natural upstream workflow runs from manual dispatches and
local reproduction. Identical input is a byte-identical no-op; two distinct
same-day evaluation instants create two records and cannot overwrite one
another. This history measures identity coverage over time. It does not turn
repeated evaluations into independent policy evidence and does not decide a
candidate-validity window.

## Hard boundary

- A resolved identity is not investability or entry eligibility.
- Candidate validity is not evaluated by this contract.
- Position sizing and portfolio participation are not evaluated.
- Stage, Buy, Action, Order, Production, and Trading authority are all false.
- The exact operational timestamp may unlock a ratified identity row, but it
  never upgrades the candidate's aggregate `DATE_ONLY` precision or opens a
  validity window.
