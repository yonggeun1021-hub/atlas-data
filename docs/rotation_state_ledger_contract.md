# P2-05 Rotation state / transition ledger contract

Status: offline common ledger capability. No repository state policy, live
ledger, briefing wiring, Production authority, or trading authority exists.

P2-02~04 deliberately emit only structural `TOP / MIDDLE / BOTTOM` bucket
transitions. They do not define `EMERGING`, `STRONG`, or `WEAKENING`. This
capability preserves that boundary: an external RATIFIED policy must bind one
market, the exact upstream rotation contract and rotation-policy SHA, the exact
three-state vocabulary, every one of the nine structural bucket transitions,
and the maximum allowed ledger gap. The repository supplies no mapping.

The policy must be ratified before the observation-date UTC boundary and be
effective on that observation date. Only then can the transform append one
state record per observed entity. Each record retains the source packet hash,
state-policy hash, structural bucket transition, prior record hash, prior/current
state, and state transition. Existing records and source packets are validated
and never reclassified. Reapplying the exact source packet and policy is byte
idempotent; applying another policy to an ingested packet fails closed.

US themes, Korea themes within their own benchmark scopes, and deterministic
Crypto BTC/ETH/ALT buckets share the ledger format without being cross-ranked.
A missing entity creates neither a synthetic state nor a tombstone. Regime,
Candidate, Stage, briefing, Production, and trading remain unauthorized. The
CLI writes only to an explicit path outside the repository.

## Production-proof boundary correction

The Korea capital-rotation one-shot proof is a P2-03 rotation and briefing
pointer proof only. It has no state-policy builder, `--ledger-out` option, or
call to `rotation_state_ledger.apply_rotation()`. Earlier code copied a test
fixture mapping into that production-facing script, labelled it RATIFIED, and
could write an external ledger. That contradicted this contract's
`repository_default_policy = ABSENT` boundary and has been removed. Generic
ledger mechanics remain fully tested in `test_rotation_state_ledger.py`, where
the policy is explicitly test-only.

## Operational readiness inventory

`rotation/rotation_state_ledger_operational_readiness.py` re-derives the
repository's current readiness without provider calls or caller-supplied policy
objects. A rolling briefing pointer is reported only as pointer lineage; it is
never promoted to the full producer packet, an approved state policy, or an
append-only operational ledger. The exact pointer bytes must match the current
committed git blob.

The current repository result is intentionally fail-closed: zero of US, Korea,
and Crypto have all three required inputs. Korea has a committed rotation
briefing pointer but not the full producer packet; US and Crypto have no
committed operational rotation evidence. The repository provides no P2-05 state
policy and no operational state-ledger evidence for any market. Therefore no
P2 state, Regime input, Candidate ranking, Stage, briefing integration,
Production, or trading authority is opened by this readiness artifact.

## Producer validation boundary

The ledger does not substitute a generic packet-shape check for producer
semantics. Before reading an entity or structural transition it dispatches the
complete packet to the matching P2-02, P2-03, or P2-04 production
`validate_packet()` implementation. Only a packet that passes its market's
policy, ranking, bucket, transition, lineage, authority, unresolved-boundary,
and digest checks can enter the common ledger. A self-rehashed semantic change
is rejected before the state policy is evaluated.

After producer validation, the ledger still enforces its own independent
market/scope/entity extraction, state-policy binding, append-only source
identity chain, missing-entity behavior, and cross-market isolation. Producer
validation grants no Regime, Candidate, Stage, briefing, Production, or trading
authority.
