# P8-05 Rotation / Discovery briefing

This read model combines a validated append-only Rotation state ledger with
Discovery Cases rebuilt from the existing SEC D1 records and explicit evidence
bindings. Version 2 also presents the real Dynamic Clock signal-observation
population used by P8-03, so a live trigger is no longer hidden behind an empty
candidate list. It presents the latest state observation for each
market/scope/entity and the evidence status of each event case.

The adapter does not call a data provider and does not infer importance,
direction, candidate rank, promotion, or action. Current Discovery policy marks
importance as unratified and promotion as unauthorized, so `new_candidates` and
`existing_candidate_changes` remain explicitly empty. A Discovery Case is not
misrepresented as a promoted candidate.

Dynamic Clock rows are separately labelled `signal_observations`. Their source
market, subject, trigger types, content-addressed signal identity and observed
tier are retained, but the tier is diagnostic only. Every row is hard-bound to
`ready_status=NOT_EVALUATED`, `promotion_status=PROMOTION_NOT_AUTHORIZED`, and
`action=null`. The summary independently keeps `ready_count=0` and
`entry_trigger_count=0`. Thus “signals exist” and “policy may deploy capital”
cannot be collapsed into the same status.

Rotation states are copied only from a ledger that passes its complete source,
policy, record-chain, and digest validation. Future-dated Rotation observations
or Discovery evidence are rejected. Output is deterministic, digest-bound, and
may be written only outside the repository.

Cross-market Discovery sources beyond current SEC coverage, candidate
importance/ranking/promotion policy, and capital authority remain unresolved.
