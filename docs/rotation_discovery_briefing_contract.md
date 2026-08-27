# P8-05 Rotation / Discovery briefing

This read model combines a validated append-only Rotation state ledger with
Discovery Cases rebuilt from the existing SEC D1 records and explicit evidence
bindings. Version 2 added the real Dynamic Clock signal-observation
population used by P8-03, so a live trigger is no longer hidden behind an empty
candidate list. It presents the latest state observation for each
market/scope/entity and the evidence status of each event case.

Version 3 also consumes P3-11 operational Wildcard envelopes. The loader reads
only committed append-only envelopes at or before the briefing generation,
re-derives each envelope from its immutable source commit, and retains the
latest revision per committed submission path. A source-envelope mismatch,
same-time conflicting revision, future envelope, or altered projection fails
closed. No wildcard is fabricated when the evidence directory is empty.

Version 4 adds the committed P3-08 DART observation packet as a separate
`dart_observations` section. The loader selects only an append-only packet whose
decision timestamp is at or before briefing generation, verifies its
content-addressed locator, and reruns the production DART packet validator
against the exact retained metadata/content hashes. The briefing exposes the
filing title and whether raw bytes were verified or only metadata was available.
It does not infer an event type, direction, importance, candidate, or action.

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

Wildcard rows are separately labelled `wildcard_observations`. Evidence-linked
cases and still-pending submissions remain distinguishable, and their immutable
source commit and envelope digest stay attached. Every row retains unratified
strength/importance, `candidate_eligible=false`, `ready_status=NOT_EVALUATED`,
`promotion_status=PROMOTION_NOT_AUTHORIZED`, and `action=null`. They may be
shown to the operator but cannot populate `new_candidates`, READY, entry, rank,
or action counts.

DART rows are separately labelled `dart_observations`. Every row keeps
`event_type=null`, `direction=null`, `importance=null`,
`ready_status=NOT_EVALUATED`, `promotion_status=PROMOTION_NOT_AUTHORIZED`, and
`action=null`. The renderer caps inline rows at ten while retaining the full
validated source packet and count in the read model.

Rotation states are copied only from a ledger that passes its complete source,
policy, record-chain, and digest validation. Future-dated Rotation observations
or Discovery evidence are rejected. Output is deterministic, digest-bound, and
may be written only outside the repository.

Cross-market Discovery sources beyond current SEC and evidence-only DART
coverage, DART item/event interpretation, candidate importance/ranking/promotion
policy, and capital authority remain unresolved.
P3-11 operational intake/publication and briefing consumption are implemented,
but a genuine main submission/live briefing sample has not yet been observed.
