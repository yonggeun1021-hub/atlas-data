# P2-03 Korea Capital Rotation contract

Status: offline price-relative capability; live Theme population, durable
breadth lineage, confirmed investor-flow release timing, state ledger, and
briefing integration remain open.

## Own-benchmark scopes

The transform consumes two hash-bound `korea_leadership/v1` derived packets.
KOSPI and KOSDAQ Theme series have different benchmarks, so the transform does
not compare or rank them in one cross-benchmark list. An external policy must
provide disjoint benchmark scopes, and ranking occurs only within each exact
scope using relative strength versus that scope's own benchmark.

KRX `series_identity` is never assumed to be a P2-01 Theme ID. Every scope
contains an explicit externally ratified `series_identity → theme_id` mapping.
One Theme ID cannot silently use multiple benchmark proxies; an aggregation
policy for that case is not ratified, so duplicate proxy mapping fails closed.

No benchmark, Theme, TOP/BOTTOM count, or cadence is a repository default.
The policy is bound to the exact P2-01 taxonomy decision/packet and Korea
Leadership policy SHA, must predate the prior observation, and must be
effective across both observations.

## Breadth and investor-flow boundary

Current Korea Breadth is an in-memory observation proof without durable
`available_at`/lineage. Current investor flow is KRX-only, excludes NXT, has an
unverified source release time and `available_at=null`, and is not decision
eligible. Both boundaries are exact required context, but neither is a ranking
input. They cannot silently become zeros, neutral breadth, or total-market
flow.

Therefore this packet is explicitly price-relative rotation, not a complete
price+breadth+flow capital-allocation claim. Those coverage gaps remain in the
output.

## Transition and authority

An effective policy emits prior/current within-benchmark ranks,
TOP/MIDDLE/BOTTOM buckets, and structural bucket transitions. It does not emit
`EMERGING`, `STRONG`, or `WEAKENING`; P2-05 owns that vocabulary and ledger.
Cross-benchmark ranking, Regime, candidate ranking, Stage, Production, and
trading remain false. The CLI is offline and can write only outside the repo.

## Standalone output validation

`validate_packet()` treats a stored packet as untrusted. It validates the exact
identity, observation pair, taxonomy and coverage bindings, embedded policy,
retention, lineage, authority, unresolved boundaries, and packet digest. For
each benchmark scope it independently re-derives canonical numeric values,
prior/current ranks, rank changes, TOP/MIDDLE/BOTTOM buckets, transitions, and
top/bottom summaries. Recomputing `payload_sha256` after changing one of those
fields cannot make the packet valid.

The output deliberately omits upstream source rows -- retaining full upstream
Leadership packets would violate this module's own `output_retention_policy`
-- but `observation_pair` (schema `korea_capital_rotation_packet/2`) persists
each observation's own `available_at` alongside its date. `validate_packet()`
re-parses both timestamps, requiring them present, ISO8601, and
timezone-aware, and independently re-derives prior-before-current order, the
effective interval covering both observations, and
ratified-before-prior-observation from those persisted values alone -- with
no live source pointer, current file, or monkeypatch -- so a revision's own
packet remains standalone-reprovable even after live source state moves on,
and a self-rehashed tamper of any of these facts (order, gap, ratification
timing) fails closed.
