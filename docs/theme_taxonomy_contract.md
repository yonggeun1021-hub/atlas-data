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
at least one edge and membership, and both US and Korea coverage. Evidence must
have existed no later than the ratification time.

Only an effective ratified graph emits the detached Global Asset Master Theme
membership adapter. The adapter remains
`DETACHED_REQUIRES_SEPARATE_MASTER_INGESTION`; it does not modify the tracked
Master or authorize Production.

## Authority and operation

The validator does not infer Themes, memberships, roles, weights, source rank,
rotation score, candidate rank, Stage, or action. Production and trading remain
false. It is offline and writes only to an explicit path outside the repository.
Tracked taxonomy publication, Master ingestion, operating population, rotation
engines, and briefing integration remain later gates.
