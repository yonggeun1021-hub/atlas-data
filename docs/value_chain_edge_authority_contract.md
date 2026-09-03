# P2-01 cross-market Value-Chain EDGE authority contract

Status: separate evidence-bound edge authority mechanism plus an empty
independent registry; no approved edge, no Theme/Value-Chain graph change,
no live membership population.

## CIO architecture decision (2026-09-04)

Korea, US, and Crypto keep their own market-native classification families as
the authoritative source of truth in their own markets. This repository does
**not** force all three into one unified Theme taxonomy. `theme_taxonomy/2`
(`rotation/theme_taxonomy.py`) and its independent
`theme_taxonomy_authority_registry/1` (`rotation/theme_taxonomy_authority.py`)
are unchanged and remain the only mechanism that can activate a market-native
membership.

`rotation/value_chain_edge_authority.py` adds a **separate** authority layer
for cross-market value-chain EDGEs on top of that unchanged mechanism. It does
not redefine, re-score, or duplicate market-native membership — it only lets
an already-ratified membership be referenced as one endpoint of an edge.

## No default edge catalog

There is no default value-chain edge, no example (AI Infrastructure/Compute/
Power or otherwise) promoted into policy, and no repository-default relation
vocabulary weighting. Every node reference and edge must arrive in an external
`value_chain_edge_input/1` document.

## Node references: reference, never redefine

A node reference (`node_ref_id`, `market`, `membership_source`, `asset_id`,
`membership_id`, `membership_packet`) is resolved **exclusively** against an
already-built `theme_taxonomy_packet/2` supplied by the caller:

1. the packet's own `payload_sha256` is independently recomputed the same way
   `theme_taxonomy.py` computes it — a mismatch is
   `UNKNOWN_MEMBERSHIP_PACKET_TAMPERED_OR_MALFORMED`;
2. the packet must report `theme_membership_authorized: true` — i.e. an
   independent `theme_taxonomy_authority_registry/1` record already backs it,
   the same fail-closed mechanism `theme_taxonomy_authority.py` already
   enforces. Otherwise `UNKNOWN_MARKET_NATIVE_MEMBERSHIP_NOT_RATIFIED`;
3. the exact `membership_id`/`asset_id`/`market` must be present in that
   packet's own `global_asset_master_membership_adapter` (the only place
   `theme_taxonomy.py` already lists genuinely active, authorized
   memberships). Otherwise `UNKNOWN_MEMBERSHIP_NOT_IN_RATIFIED_ADAPTER`.

`market` structurally allows `CRYPTO` in addition to `KOREA`/`US`, but
`allowed_node_membership_sources` currently lists only
`theme_taxonomy_packet/2`, which never carries a `CRYPTO` membership (its own
contract only allows `KOREA`/`US`). A `CRYPTO` node reference — or any
`membership_source` other than `theme_taxonomy_packet/2` — therefore always
resolves to `UNKNOWN_MEMBERSHIP_SOURCE_NOT_SUPPORTED` or fails the adapter
match. This is deliberate: Crypto sector/chain membership is not wired to any
ratified authority in this slice, so it fails closed rather than being
inferred from an asset name, ticker, or watchlist. The same applies to US: the
2026-09-01 source-population audit recorded US Theme membership at zero rows,
so every US node reference stays `UNKNOWN` until a real
`theme_taxonomy_authority_registry/1` record exists — nothing in this module
can manufacture US membership from an ETF/index name.

A node reference's provenance/first-seen is not re-derived; it is the
underlying packet's own `authority_resolution.real_usable_from`, already
git-provenance-verified by `theme_taxonomy_authority.py`.

## Edges: exact evidence binding, own window, independent registry

Every edge (`edge_id`, `from_node_ref_id`, `to_node_ref_id`, `relation_type`
in `COMPETES_WITH`/`CUSTOMER_OF`/`DEPENDS_ON`/`ENABLES`/`SUPPLIES`, `evidence`,
`valid_from`/`valid_to`) carries its own hash-bound evidence: `evidence_id`,
`claim_text`, and a `source_identity` (`source_id`/`source_url`/
`source_sha256`/`available_at`/`retrieved_at_utc`) validated against the same
`market_sources`/`source_hosts` allow-list already committed in
`theme_taxonomy_contract.json` for either endpoint's market — reused via
`theme_taxonomy.py`'s own source validator, not re-implemented. Evidence must
exist no later than the graph's own `as_of_date`.

An edge activates only when **all** of the following hold, checked in order,
each with its own explicit non-error status:

1. both endpoint node references independently resolve to
   `RATIFIED_MARKET_NATIVE_MEMBERSHIP` — otherwise
   `UNKNOWN_MARKET_NATIVE_MEMBERSHIP_NOT_RATIFIED` (the fail-closed gate: a
   ratified market-native record is required on **both** sides, or the edge
   never activates, regardless of anything else about the edge);
2. the edge's `[valid_from, valid_to)` is contained in both endpoints'
   resolved membership window — otherwise
   `UNKNOWN_INTERVAL_OUTSIDE_NODE_MEMBERSHIP_WINDOW`;
3. a matching `RATIFIED` row exists in the separate, git-provenance-verified
   `config/value_chain_edge_authority_registry.json`
   (`value_chain_edge_authority_registry/1`) — otherwise
   `UNKNOWN_EDGE_AUTHORITY_NOT_RATIFIED`.

Step 3 reuses the exact git first-seen/tamper/PIT-safety mechanism already
hardened in `theme_taxonomy_authority.py` (`_run_git`, `_repo_root`,
`_git_blob`, `_first_seen_exact_bytes`, the
`real_usable_from = max(effective_from, ratified_at, row_first_seen,
evidence_first_seen)` rule, same-day availability staying not computable) —
imported and called directly, applied to one edge's own determining payload
(`edge_id`, both endpoints' market/asset/membership identity, evidence,
window) instead of a whole taxonomy graph. `config/
value_chain_edge_authority_registry.json` ships committed and empty, exactly
like `theme_taxonomy_authority_registry.json` did at P2-01's own start.

## Partial graph, UNKNOWN as steady state

A structurally well-formed input document never fails to build merely because
some node or edge cannot be authorized. Every node reference and edge reports
its own explicit status (`RATIFIED_MARKET_NATIVE_MEMBERSHIP` /
`UNKNOWN_*` for nodes, `RATIFIED_CROSS_MARKET_VALUE_CHAIN_EDGE` / `UNKNOWN_*`
for edges); the packet still builds with a mix of both. Only malformed input
(bad token, missing field, self-reference, disallowed evidence source,
unknown relation type) raises `ValueChainEdgeAuthorityError` and aborts the
whole build — that is an input-quality failure, not an authority outcome.

## Authority and operation

No Theme, membership, weight, source rank, rotation score, candidate rank,
Stage, capital, order, or trading authority is opened anywhere in this
module. `edge_activation_authorized` is the only authority flag that can ever
be `true`, and only for the specific edges with a matching `RATIFIED`
registry row; every other authority field is hardcoded `false`. The module is
offline and writes nothing — `build_packet` returns an in-memory dict; no CLI
or tracked output path is added in this slice.

Global Asset Master ingestion, Rotation engine consumption, and briefing
integration remain later, out-of-scope gates, same as `theme_taxonomy/2`
itself.
