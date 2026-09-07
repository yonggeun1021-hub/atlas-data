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
or live consumer is modified. The preview API writes no file at all; the separate
application API below writes only the one destination file its caller names.
There is no publishing CLI and no operational caller is wired to either API.
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

## Master application to one designated destination

`apply_theme_ingestion_preview()` is implemented as a library capability only.
It applies an already validated preview to one explicitly designated, already
existing master destination file. No CLI, scheduler, web surface or operational
caller is wired to it, no master data ships with it, and its existence does not
ratify Theme Authority, PIT/membership policy or live migration.

### Caller-supplied inputs

The function takes the preview plus every original input that produced it, and
nothing derived from the preview alone:

- `preview`: the object returned by `build_theme_ingestion_preview()`;
- `master_source`, `taxonomy_source`, `requests`: the same originals, supplied
  again by the caller;
- keyword-only `trusted_commit` and optional `authority_registry_path`: the same
  independently sourced authority context, resolved through
  `theme_taxonomy_authority.resolve_graph_authority()` at that immutable commit;
- keyword-only `destination_path`: the exact existing output file the caller
  designates;
- keyword-only `expected_previous_master_sha256`: the destination's expected
  current `payload_sha256`. There is no "must not exist" value, because a
  missing destination always fails closed;
- keyword-only `operational_application_approved`: an explicit caller assertion
  of operational approval, with no default, which must be exactly `True`.

`operational_application_approved` is a trusted library caller boundary and
nothing more. It does not substitute Theme authority or PIT validation, does not
attest to itself, and never skips an original, digest or authority check. The
preview's own `master_population_authorized=false` output and every existing
preview, binder and API result are unchanged by it.

A preview cannot attest to itself. `preview["payload_sha256"]`,
`preview["input_digests"]["master_source_sha256"]`, `["taxonomy_source_sha256"]`,
`["requests_sha256"]`, `["original_master_payload_sha256"]`,
`["taxonomy_payload_sha256"]` and `["trusted_commit"]`, together with
`preview["binding_report"]["master_payload_sha256"]`,
`["taxonomy_payload_sha256"]` and `["taxonomy_authority_resolution"]`, are
checked against values recomputed from the supplied originals and the committed
lineage. They are never accepted as the source of their own truth.

### Preconditions checked immediately before applying

Argument shape is checked first, with nothing opened or created:
`operational_application_approved` is exactly `True`
(`APPLICATION_APPROVAL_NOT_EXACTLY_TRUE`; `1`, `"true"` and any other truthy
value are refused), `destination_path` is designated and names an existing file,
and `expected_previous_master_sha256` is a literal sha256.

Everything else happens inside one cooperative exclusion boundary for that
destination, in this order, with no partial effect before all of it passes:

1. The destination's existing bytes are read and re-derived through
   `validate_packet()`. Its `payload_sha256` must equal
   `expected_previous_master_sha256`.
2. The supplied original master is re-derived through `_validated_master()` and
   its `master_id`, `as_of_date` and `payload_sha256` must equal the
   destination's. The two identity fields are redundant while a digest is
   intact; each is still compared so it fails closed on its own.
3. `validate_theme_ingestion_preview(preview, master_source, taxonomy_source,
   requests, trusted_commit=..., authority_registry_path=...)` is re-run in full
   against the supplied originals and the committed authority at that immutable
   commit. Any edit to the candidate, binding report, authority block or digests
   still raises the existing `INGESTION_PREVIEW_DERIVATION_MISMATCH`. Only the
   recomputed preview is used from here on; the caller's object is never
   consulted for its own truth.
4. That recomputed preview's `original_master_payload_sha256` lineage must equal
   the destination's digest, `preview["status"] == "STRUCTURAL_PREVIEW"`,
   `preview["failure_reasons"]` is empty, `preview["candidate_master"]` is not
   null, and `preview["binding_report"]["status"] ==
   "THEME_SOURCE_BINDING_VERIFIED"`. An `UNRESOLVED` or `NOT_VERIFIED` binding is
   not applicable; a `BLOCKED` preview is never applicable.

A changed previous master is always a conflict. There is no silent overwrite, no
implicit rebase onto the newer destination, and no automatic re-preview against
it. The caller must rebuild and revalidate a preview from the new original.

### Cooperative per-destination exclusion

The caller-designated existing destination is resolved strictly to its real
path before locking, reading or publishing. Relative paths and symbolic-link
aliases therefore share the target's sidecar and replacement path; an alias
is preserved as a symbolic link. Resolution failure or a non-file target fails
closed. The returned `destination_path` names that resolved target. This does
not choose a default destination or initialize an absent target.

The read, the previous-digest and original-identity checks, the full original
revalidation and the publish all run while one advisory `flock` is held on a
stable sidecar next to the destination. The destination inode itself is not
locked, because the atomic publish replaces it and a lock on a replaced inode
excludes nothing. The sidecar is a zero-byte cooperative marker: it holds no
master data, is never read as state, is not an absence marker, and is not
removed, so its identity stays stable across publishes.

This protocol is shared by every caller of this API and excludes only those
callers. It is not a compare-and-swap against a writer that does not take the
same lock, and no such claim is made. On a platform without `fcntl` the call
fails closed with `APPLICATION_LOCK_UNSUPPORTED_PLATFORM`; the preview API
continues to import and work there.

### APPEND, NO_CHANGE and collisions

Change semantics reuse the existing preview values, not a second rule set:

- `change == "APPEND"` with `addition_count > 0` publishes the already validated
  `candidate_master` and returns an `APPLIED_APPEND` outcome.
- `change == "NO_CHANGE"` means the requested rows are byte-identical to rows
  already present. The candidate must be canonically identical to the validated
  destination packet; it is then an exact idempotent no-op that writes nothing
  and returns `APPLIED_NO_CHANGE`. This is reachable only when the caller has
  explicitly rebuilt the preview from the current destination as its original
  and supplied the updated expected digest. Replaying the earlier preview after
  an `APPLIED_APPEND` is a conflict, never an idempotent retry: its expected
  digest no longer matches the destination, and pairing it with the new digest
  fails the original-identity or derivation check instead. Nothing rebases,
  retries or re-previews on the caller's behalf.
- Conflicting rows, duplicate targets and alias/membership/cross-record
  collisions are already rejected inside `build_master()` and `_requests()`
  before any preview exists, so they can never reach application.

No original or historical row is deleted, replaced or rewritten. Legacy rows are
not migrated by mapping a source label; superseding an existing membership is a
separate explicit request, not a side effect of applying one.

### Destination and approval boundary

The caller names the destination file and the approval. Neither is inferred. The
implementation does not fall back to a repository default master path, a
configured output location, the working tree, a most-recent packet, or an
"obvious" sibling of the input. Only an existing normal master file is
supported: a missing target fails closed with
`APPLICATION_DESTINATION_NOT_AN_EXISTING_FILE` and never triggers an absence
marker, an initialization, a canonical path inference, a destination-parent
creation or any other operational file creation. Missing or unratified
authority, missing PIT or membership policy, an undesignated destination, or
absent explicit approval each make application unavailable rather than defaulted.

### Failure, preservation and atomic publish

No mutation occurs on failed revalidation, previous-master conflict, missing
approval or a failure raised before the publish step. Application performs every
check first and only then performs a single publish step, reusing the existing
`write_json_atomic()` (temporary file in the destination directory, `fsync`,
then `os.replace`). Any failure before that `os.replace` leaves the prior
destination bytes exactly as they were, and leaves at most an unreferenced
temporary file. After the replace the publication may already have occurred, so
a failure raised at or after that point is explicitly not a promise that the old
bytes survived. This is a concrete description of applying this master to one
file: no general transaction manager, journal, lock service, versioning scheme
or new schema is introduced.

### Result

The call returns a minimal in-memory object only: `outcome`
(`APPLIED_APPEND` or `APPLIED_NO_CHANGE`), `change`, `destination_path`,
`addition_count`, `unchanged_count`, `published`, and the before/after master
identity (`master_id`, `as_of_date`, `payload_sha256`). There is no receipt
schema, ledger, history file or persisted application record of any kind, and
nothing but the destination packet is written. Every failed precondition raises
`AssetMasterError` naming the specific check.

### Structural capability is not operational adoption

Structural ability to append a validated row is not permission to run a live
master. Contract authority flags remain false, the current registry still blocks
every preview, the binding report keeps its
`THEME_MEMBERSHIP_INGESTION_NOT_IMPLEMENTED` marker, and Theme Authority
ratification, PIT/membership policy and reviewed live migration remain separate
canonical gates. Until an explicit destination, authority and approval are
supplied by a caller who holds them, application is unavailable by definition.
A structural writer existing in this library is not an operating master.

### Focused application verification

Six tests in the existing `ThemeIngestionTests` class cover the changed scope and
reuse the existing synthetic isolated authority repository, real validators and
temporary destinations. The preview construction, binding and tamper cases are
not duplicated.

Positive: one approved append into a temporary destination publishes exactly the
validated candidate, and an explicitly rebuilt preview against that new
destination is an `APPLIED_NO_CHANGE` that writes nothing.

Negative: the earlier preview replayed after the append, that preview paired with
the new expected digest, and a hand-rebased preview all conflict; a non-`True`
approval, an undesignated, absent or non-file destination, a malformed or
mismatched expected digest and a `BLOCKED` preview all fail closed without
creating anything; a destination whose `master_id`, `as_of_date` or
`payload_sha256` differs from the supplied original is a conflict that preserves
the destination bytes; two cooperating concurrent callers are serialized so that
exactly one appends and the other conflicts; and an injected failure at the
publish step leaves the original bytes and leaves the boundary usable.

Concurrency is proven with events and bounded waits, never with sleeps.

### Implementation scope actually taken

One function, `apply_theme_ingestion_preview()`, plus small private helpers in
`universe/global_asset_master_theme_ingestion.py`, six focused tests appended to
the existing class in `test/test_global_asset_master_theme_ingestion.py`, and
this document. No new module, test file, contract file, config value, CLI,
scheduler entry, schema or receipt object was added, and `global_asset_master.py`
is unmodified.

Required caller arguments: `preview`, `master_source`, `taxonomy_source`,
`requests`, `trusted_commit`, `authority_registry_path`, `destination_path`,
`expected_previous_master_sha256`, `operational_application_approved`.

Outcomes: `APPLIED_APPEND`, `APPLIED_NO_CHANGE`, or a raised `AssetMasterError`
carrying the specific precondition that failed.

Unresolved operational inputs that this document does not and cannot supply:
who authorizes `master_population_authorized`; the ratified authority registry
content; the canonical destination path and its retention/history policy; the
PIT and membership policy for superseding existing rows; and whether a persisted
application receipt is ever required, which this capability deliberately does not
introduce. Exclusion holds only among callers of this API; a destination that a
foreign writer can also replace is still outside what this boundary can promise.
