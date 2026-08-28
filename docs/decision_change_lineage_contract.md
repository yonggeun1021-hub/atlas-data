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
full-SHA Git blob, verifies the complete blob's content hash, and extracts the
exact Unified Decision. Each prior/current snapshot is then revalidated by the
Unified Decision validator from the immutable source commit named in that
snapshot's strict raw-GitHub source reference. That commit must also be an
actual ancestor of the current checkout, so an unmerged side-branch commit
cannot supply executable historical validation code. This per-snapshot boundary is
intentional: replaying a historical Daily Briefing as if every diagnostic
component were produced from today's mutable source files or today's visible
Git ref graph can change DART timing and first-seen diagnostics even though the
recorded Decision bytes are unchanged. Phase B commits the resulting
content-addressed forward-only record beside the scheduled retrieval-authority
envelope. Retries are idempotent; a broken prior record chain, mutable or
non-Atlas source ref, disk/blob mismatch, future decision, or semantic tamper
fails closed. It makes no provider call and never interprets a change.

The remaining unresolved boundary is the exact three-market Shadow-ledger
lineage link. Neither CLI has money, action, order, Production, or trading
authority.
