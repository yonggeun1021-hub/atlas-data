# P2-01 Theme / Value-Chain taxonomy contract

Status: external-graph validation plus an empty independent authority-registry
mechanism; no approved repository taxonomy or live membership population.

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
open membership authority.

`config/theme_taxonomy_authority_registry.json` now supplies the separate
authority boundary, but deliberately contains zero records. A record can be
used only when all of the following independently hold:

- its complete determining payload binds the exact graph payload;
- the registry file and approval-evidence file match their exact bytes at the
  current clean git HEAD or an externally supplied immutable full commit SHA;
- the approval evidence repeats and hashes that complete determining payload;
- the registry row and the exact evidence bytes are found in real git history;
- `real_usable_from = max(effective_from, ratified_at, row_first_seen_at,
  evidence_first_seen_at)` precedes the date-only decision day (same-day
  availability remains not computable rather than being backdated);
- exactly one active `RATIFIED` record matches.

There is no caller-injected registry dictionary and no mutable branch/tag/HEAD
pin. Dirty, missing, ambiguous, unratified, expired, path-traversing, or
re-signed evidence fails closed. With the committed empty registry,
`theme_taxonomy/2` reports a coherent active claim as
`STRUCTURALLY_VALID_RATIFICATION_CLAIM_NOT_AUTHORIZED`, while
`theme_membership_authorized` stays false and the adapter stays empty.

Every edge and membership still carries its own `valid_from`/`valid_to`. The
active slice must cover both required markets and include an active edge before
the claim is even structurally eligible. Otherwise the graph reports
`DRAFT_OR_NOT_EFFECTIVE_GRAPH`. The packet separates historical-union
`covered_markets` from `active_covered_markets` and `active_edge_count`.

The mechanism can activate only a detached, unweighted membership adapter after
a future separately reviewed authority record meets that boundary. No such
record, Theme node, membership, or role is added by this change. Source ranking,
rotation scoring, candidate selection, and trading authority remain independent
unratified gates.

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
