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

## External ratification claim boundary

An `UNRATIFIED` graph can be structurally inspected but activates zero Theme
memberships. An externally supplied `RATIFIED` **claim** must have decision
identity/hash, ratifier/time, at least one edge and membership, and both US and
Korea coverage somewhere across its full history. Evidence must have existed
no later than the claimed ratification time.

Those fields remain caller-supplied claims, not independent proof that a
canonical CIO/Rule Authority record approved these exact bytes. A syntactically
valid SHA-256, ratifier name, timestamp, or re-signed payload therefore cannot
open membership authority. The repository currently has no P2-01 approval
authority registry. `theme_taxonomy/2` reports a coherent active claim as
`STRUCTURALLY_VALID_RATIFICATION_CLAIM_NOT_AUTHORIZED`, while
`theme_membership_authorized` stays false and the adapter stays empty.

Every edge and membership still carries its own `valid_from`/`valid_to`. The
active slice must cover both required markets and include an active edge before
the claim is even structurally eligible. Otherwise the graph reports
`DRAFT_OR_NOT_EFFECTIVE_GRAPH`. The packet separates historical-union
`covered_markets` from `active_covered_markets` and `active_edge_count`.

A later contract may add adapter activation only after a separate authority
registry binds the complete determining payload, approval evidence, effective
interval, and PIT availability. That authority design is not implemented or
presumed here.

The existing US/Korea rotation contracts still declare `theme_taxonomy/1`.
They are intentionally not relabeled as `/2`: their current binding shape does
not consume or independently verify the new authority boundary. Integration
therefore remains blocked rather than implying compatibility by version alone.

## Authority and operation

The validator does not infer Themes, memberships, roles, weights, source rank,
rotation score, candidate rank, Stage, or action. Production and trading remain
false. It is offline and writes only to an explicit path outside the repository.
Tracked taxonomy publication, Master ingestion, operating population, rotation
engines, and briefing integration remain later gates.
