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
