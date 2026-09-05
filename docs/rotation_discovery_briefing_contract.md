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
Historical all-success `dart_event_observation_packet/1` and current
partial-failure-capable `/2` are accepted only through the producer's exact
validator. The section exposes `source_failed_count`, `content_failure_count`,
and redacted `source_failures`, so a valid observation cannot hide a sibling
metadata/content failure and a partial failure cannot erase valid rows.

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

## Daily US rotation ledger wiring

The daily orchestrator previously handed this read model an unconditional empty
ledger, so the `rotation` section could never show a real state history no
matter what P2-05 had already recorded. `briefing/daily_orchestrator.py` now
accepts one optional, caller-explicit rotation source
(`frozen_sources["US_ROTATION_LEDGER"]`, exactly the three keys
`rotation_packet`, `state_policy`, `previous_ledger`) and passes it through the
unchanged `rotation_state_ledger.apply_rotation()` before building this packet.
This read model's own contract, schema, output bytes, and authority flags are
unchanged; only the ledger it is given can now be non-empty.

The wiring adds no authority of its own. It ratifies no state policy, supplies
no default bucket-transition mapping, populates no registry, discovers no file,
and never writes. The applied state vocabulary is whatever the caller's already
ratified external policy declares, and the packet, policy, previous ledger, gap,
forward-only, append-only chain, and digest checks are the producer's and the
ledger's existing ones, unmodified. Duplicate application of the same source
packet stays idempotent through the ledger's own `SOURCE_PACKET_POLICY_CONFLICT`
and duplicate-source handling, and any non-US history already inside the
supplied previous ledger is appended to, never replaced.

Rules that hold at this boundary:

- Absent input keeps the legacy empty-ledger output byte-for-byte, and adds no
  key to the daily packet. Absent means the `US_ROTATION_LEDGER` key is not in
  `frozen_sources` at all (and, at the function boundary, that the argument was
  not passed). A key that is present and holds `null` is a supplied input, not
  an absent one: it is frozen exactly as supplied and fails the row closed, so
  an explicitly broken build can never produce the same bytes as a build that
  had no rotation source.
- Only a `US` rotation packet with a matching `US` state policy is accepted.
- The raw packet/policy/previous ledger, not the derived ledger, are frozen
  into the daily packet, so standalone validation independently re-runs
  `apply_rotation()` over the original inputs instead of trusting a ledger that
  merely rehashes itself. A frozen input is deep-copied, so a later mutation of
  the caller's own object cannot change what the packet was built from. A
  supplied input that is rejected is frozen too, exactly as it was supplied, so
  the fail-closed verdict replays deterministically from the same input instead
  of from a key that was quietly dropped.
- A supplied input the ledger rejects -- wrong market, unratified or mismatched
  policy, future-dated observation, rehashed semantic forgery, broken record
  chain -- fails the whole `ROTATION_DISCOVERY` row closed with an explicit
  reason, as does a supplied value that is null or structurally unusable before
  the ledger is ever reached. It is never repaired and never degraded into the
  empty ledger, so "an invalid rotation observation was supplied" cannot render
  identically to "no rotation was observed today".
- A rotation observation's `as_of_date` is exposed to the orchestrator's common
  temporal boundary like every other dated source.

Cross-market Discovery sources beyond current SEC and evidence-only DART
coverage, DART item/event interpretation, candidate importance/ranking/promotion
policy, and capital authority remain unresolved.
P3-11 operational intake/publication and briefing consumption are implemented,
but a genuine main submission/live briefing sample has not yet been observed.
