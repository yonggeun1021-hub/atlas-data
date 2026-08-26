# P10-04 Decision change lineage

This offline capability records why, from which evidence, and when the common
three-market Unified Decision changed. Contract v2 requires each prior/current
snapshot to embed an exact `unified_daily_decision/1` packet and revalidates it
with the production P8-02 validator. The snapshot Decision SHA, source SHA, and
decision time must equal that validated packet's SHA and `generated_at`.

Change types are derived, not trusted from input:

- no prior plus current: `CREATED`;
- equal prior/current Decision SHA: `UNCHANGED`;
- different prior/current Decision SHA: `CHANGED`;
- prior plus no current: `RETIRED`.

Created, changed, and retired entries require at least one canonical reason code
and one evidence record available by the change time. Unchanged entries must
have neither. Multiple claims for one Decision key must form an exact chain:
the next prior snapshot equals the previous current snapshot.

The exact source Decision packets remain inside their snapshots for later
self-validation, but the output does not synthesize or interpret a Decision and
cannot create or change a Decision, candidate stage, action, or order.

`decision/decision_change_lineage_operational.py` is the provider-free live
wiring for the scheduled Daily Briefing workflow. After Phase A commits the
consumer-ready bundle, the adapter reads the packet from its exact immutable
full-SHA Git blob, runs that commit's own Daily Briefing validator in an
isolated archive, extracts the exact validated Unified Decision, and appends a
content-addressed forward-only record. Phase B commits that record beside the
scheduled retrieval-authority envelope. Retries are idempotent; a broken prior
record chain, mutable ref, disk/blob mismatch, future decision, or semantic
tamper fails closed. It makes no provider call and never interprets a change.

The remaining unresolved boundary is the exact three-market Shadow-ledger
lineage link. Neither CLI has money, action, order, Production, or trading
authority.
