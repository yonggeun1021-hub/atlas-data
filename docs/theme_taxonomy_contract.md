# P2-01 Theme / Value-Chain taxonomy contract

Status: external-graph validation capability; no repository taxonomy or live membership population.

## No default taxonomy

The WBS gives AI Infrastructure and its value-chain segments only as an
example. This implementation does not turn that example into policy. The
repository contains no Theme node, segment, role vocabulary, asset membership,
weight, or score. All such choices must arrive in a versioned external graph.

## Graph and membership boundary

`rotation/theme_taxonomy.py` validates effective-dated `THEME` and
`VALUE_CHAIN_SEGMENT` nodes plus `CONTAINS`, `SUPPLIES`, `DEPENDS_ON`, or
`ENABLES` edges. `CONTAINS` must be acyclic. Other value-chain relations may
form real-world networks but cannot self-reference.

US and Korea memberships are explicit and effective-dated. Every membership
names an externally defined role and carries at least one market-appropriate,
source-linked evidence item. There are no weights, ranks, inferred memberships,
or fallback sources. Overlapping duplicate membership intervals fail closed.

## Ratification gate

An `UNRATIFIED` graph can be structurally inspected but activates zero Theme
memberships. A `RATIFIED` graph must have decision identity/hash, ratifier/time,
at least one edge and membership, and both US and Korea coverage *somewhere
across its full history* -- this is a one-time completeness check on the
document itself. Evidence must have existed no later than the ratification
time.

That structural check is not enough on its own: every edge and membership
carries its own `valid_from`/`valid_to`, independent of the approval's
effective window, so a market's coverage can lapse (or not yet have started)
on any given `as_of_date` even though the ratified document once named both
markets. `build_packet` therefore also requires, before it will call the
graph `EFFECTIVE_RATIFIED_GRAPH`, that the *active* slice as of `as_of_date`
still covers both required markets and still has at least one active edge.
If it does not, the graph deactivates exactly like a not-yet-effective
approval window does -- `graph_status = DRAFT_OR_NOT_EFFECTIVE_GRAPH`,
`theme_membership_authorized = false`, empty adapter -- rather than raising,
since a lapsed interval is expected temporal behavior, not a document defect.
The packet reports both facts separately: `covered_markets` (historical
union, unaffected) and `active_covered_markets`/`active_edge_count` (the
as-of-date slice that actually gates the adapter).

Only a graph that is *currently* effective by that combined test emits the
detached Global Asset Master Theme membership adapter -- the mechanism that
actually connects US and Korea names into one Theme graph on a given date.
The adapter remains `DETACHED_REQUIRES_SEPARATE_MASTER_INGESTION`; it does
not modify the tracked Master or authorize Production.

## Authority and operation

The validator does not infer Themes, memberships, roles, weights, source rank,
rotation score, candidate rank, Stage, or action. Production and trading remain
false. It is offline and writes only to an explicit path outside the repository.
Tracked taxonomy publication, Master ingestion, operating population, rotation
engines, and briefing integration remain later gates.
