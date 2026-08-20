# P8-05 Rotation / Discovery briefing

This read model combines a validated append-only Rotation state ledger with
Discovery Cases rebuilt from the existing SEC D1 records and explicit evidence
bindings. It presents the latest state observation for each market/scope/entity
and the evidence status of each event case.

The adapter does not call a data provider and does not infer importance,
direction, candidate rank, promotion, or action. Current Discovery policy marks
importance as unratified and promotion as unauthorized, so `new_candidates` and
`existing_candidate_changes` remain explicitly empty. A Discovery Case is not
misrepresented as a promoted candidate.

Rotation states are copied only from a ledger that passes its complete source,
policy, record-chain, and digest validation. Future-dated Rotation observations
or Discovery evidence are rejected. Output is deterministic, digest-bound, and
may be written only outside the repository.

Production briefing wiring, cross-market Discovery sources beyond current SEC
coverage, candidate promotion policy, and live morning/evening observation
remain unresolved.
