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

`gam_source_identity` contains all five literal original disclosure lineage
fields. For THEME rows the GAM contract delegates source/market/host/time
validation to the existing ThemeTaxonomy/2 validator. The caller supplies the
identity explicitly: this adapter never translates an identity-provider label
into a disclosure label, finds a symbol match, selects evidence or invents an
asset. Record, MARKET and UNIVERSE identity providers remain unchanged.
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
   `NO_CHANGE`. This status describes construction only. The binding report
   separately records literal source comparison and independent authority.

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

THEME provenance uses the existing taxonomy disclosure source vocabulary.
A literal exact binding can be verified under an independently ratified graph;
this does not authorize operational ingestion. Different source IDs are never
aliased, even with an identical document URL/hash. Invalid role/market/host
lineage raises before a candidate is returned, while defined binding failures
(including a valid but different disclosure source ID) return BLOCKED/null.
The current empty authority registry still blocks every attempted ingestion
preview. The report retains its operational
`THEME_MEMBERSHIP_INGESTION_NOT_IMPLEMENTED` marker because neither that binder
nor this preview writes a live master.

`master_population_authorized=false`. Existing GAM identity-only authority,
universe/investability/Stage/Production/trading restrictions remain unchanged.
No registry, current master, tracked data, historical packet, scheduler, workflow
or live consumer is modified. There is no publishing CLI or file-writing API.
Identity-only GAM output and the CLI interface remain unchanged. Legacy THEME
rows using identity-provider provenance now fail closed and must be supplied
with explicit disclosure evidence; they are never automatically rewritten.

This module implements a reusable data-construction step. Theme Authority,
reviewed live membership migration, actual consumer use and
natural ordered-pair evidence remain separate canonical gates. Passing synthetic
fixture tests does not close Rotation, P3-01, or a natural operating gate.

## Focused verification

The adapter tests use the existing synthetic isolated authority repository and
real GAM/ThemeTaxonomy validators. They exercise literal expected row creation,
repeat no-op, unchanged original inputs, current empty registry rejection,
role/market/host/source/identity/interval mismatches, collisions, deterministic
selections,
retained evidence and rehashed tamper rejection. Existing GAM tests protect the
unmodified legacy API. No market collection or historical packet is fabricated.
