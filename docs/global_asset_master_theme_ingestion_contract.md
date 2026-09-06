# Explicit Theme-to-GAM ingestion preview (P3-01 / P2-01 dependency)

This capability constructs a candidate master in memory from an existing master,
an original ThemeTaxonomy/2 graph, and explicit caller selections. It connects
the missing membership-construction step to the existing GAM builder and
`validate_theme_source_binding()`. It does not populate an operational master.

## Inputs and API

`universe.global_asset_master_theme_ingestion.build_theme_ingestion_preview()`
accepts:

- `master_source`: an original GAM/1 input or validated GAM packet;
- `taxonomy_source`: the original ThemeTaxonomy/2 graph input, not a claimed
  prevalidated report;
- `requests`: a nonempty list of explicit `asset_id`, `gam_membership_id`,
  `taxonomy_membership_id`, `evidence_id`, and `gam_source_identity` objects;
- keyword-only `trusted_commit`: the immutable authority commit required by the
  existing binding validator; and
- optional `authority_registry_path`: the existing independently verified
  committed registry boundary, with the same semantics as the taxonomy builder.

`gam_source_identity` contains all five GAM lineage fields. It is supplied by
the caller: this adapter never translates a taxonomy retrieval-channel label
into a GAM label, finds a symbol match, selects evidence, or invents an asset.
The requested GAM ID must match the taxonomy theme under the existing binder.
Intervals come from the independently rebuilt taxonomy membership. The original
taxonomy membership, all its evidence, role and identity, and the proposed GAM
identity remain available side by side in the preview and binding report.

## Actual construction and validation

1. Validate the existing master with the existing GAM validator/builder and
   rebuild the graph with the existing ThemeTaxonomy producer and authority.
2. Find only explicitly named records and memberships. Add the requested THEME
   row to a detached copy of the master input. An identical existing row is a
   no-op; conflicting rows pass through the existing collision checks, which
   reject them. No original or historical row is overwritten or removed.
3. Build a real GAM candidate packet and invoke the existing source-binding
   validator against both originals. No validator is weakened or copied.
4. Return `BLOCKED` and `candidate_master=null` when the binder identifies any
   defined failure (including unratified/empty taxonomy authority, mismatched
   date, asset, market, theme, interval or document, and missing evidence).
   Malformed inputs, missing asset/membership, duplicate targets and structural
   collisions raise `AssetMasterError` before any result is returned.
5. With zero defined failures, return `STRUCTURAL_PREVIEW` and `APPEND` or
   `NO_CHANGE`. This status describes construction only. Undefined source-ID
   comparison remains unresolved and unverified in the unchanged binding report.

The schema is `global_asset_master_theme_ingestion_preview/1`. It carries the
input digests, authority commit, original master and rebuilt graph identities,
explicit selections, candidate packet, original binding report and full selected
taxonomy memberships. Selection order does not change output, but the original
master and graph input byte-equivalent JSON identities remain pinned.

`validate_theme_ingestion_preview()` takes the preview and all the same original
inputs and recomputes the complete result. Updating a candidate, report or
authority field and recalculating its hash does not make the edit valid. Callers
must supply the trusted original inputs and authority context; the preview is
not its own authority root.

## Unchanged policy and operational boundaries

Current GAM and taxonomy source registries are disjoint. A structurally valid
candidate can therefore have `THEME_SOURCE_BINDING_UNRESOLVED`; it is not a
verified ingestion. Caller source identities do not ratify a cross-mapping.
The existing report's unresolved boundaries are preserved, including its legacy
`THEME_MEMBERSHIP_INGESTION_NOT_IMPLEMENTED` marker: that validator still does
not implement ingestion, and this preview does not implement operational writes.

`master_population_authorized=false`. Existing GAM identity-only authority,
universe/investability/Stage/Production/trading restrictions remain unchanged.
No registry, current master, tracked data, historical packet, scheduler, workflow
or live consumer is modified. There is no publishing CLI or file-writing API.
The existing GAM output and CLI remain unchanged for existing callers.

This module implements a reusable data-construction step. Source cross-mapping,
Theme Authority, reviewed live membership migration, actual consumer use and
natural ordered-pair evidence remain separate canonical gates. Passing synthetic
fixture tests does not close Rotation, P3-01, or a natural operating gate.

## Focused verification

The adapter tests use the existing synthetic isolated authority repository and
real GAM/ThemeTaxonomy validators. They exercise literal expected row creation,
repeat no-op, unchanged original inputs, current empty registry rejection,
source/identity/interval mismatches, collisions, deterministic selections,
retained evidence and rehashed tamper rejection. Existing GAM tests protect the
unmodified legacy API. No market collection or historical packet is fabricated.
