# Explicit-file theme application CLI (P3-01 / P2-01 consumer)

`universe/global_asset_master_theme_application_cli.py` is the missing callable
connection between five caller-named original JSON files and the already
accepted library capabilities `validate_theme_ingestion_preview()` and
`apply_theme_ingestion_preview()`.

Both of those are used unchanged. This module adds no builder, no validator, no
policy, no default destination, no schedule, no journal and no receipt. It never
constructs a preview: a reviewed preview is a required input file. It performs
no provider, network or credential access.

## Exact invocation

Validation only (the default; no destination argument is accepted):

```
python universe/global_asset_master_theme_application_cli.py \
  --preview            <PREVIEW_JSON_PATH>    --preview-sha256            <PREVIEW_FILE_SHA256> \
  --master-source      <MASTER_JSON_PATH>     --master-source-sha256      <MASTER_FILE_SHA256> \
  --taxonomy-source    <TAXONOMY_JSON_PATH>   --taxonomy-source-sha256    <TAXONOMY_FILE_SHA256> \
  --requests           <REQUESTS_JSON_PATH>   --requests-sha256           <REQUESTS_FILE_SHA256> \
  --authority-registry <REGISTRY_JSON_PATH>   --authority-registry-sha256 <REGISTRY_FILE_SHA256> \
  --trusted-commit     <IMMUTABLE_COMMIT_SHA>
```

Application adds the explicit switch, the existing destination and its expected
current digest:

```
  --apply \
  --destination <EXISTING_MASTER_JSON_PATH> \
  --expected-previous-master-sha256 <DESTINATION_CURRENT_PAYLOAD_SHA256>
```

Every placeholder is supplied by the caller. Nothing is discovered from the
working directory, an environment variable, a sibling file, a configured
location or a most-recent packet. `--destination` or
`--expected-previous-master-sha256` without `--apply` is rejected
(`DESTINATION_ARGUMENTS_REQUIRE_APPLY`) rather than silently ignored, because
accepting them would imply a destination had been considered.

`run(argv)` is the same entry point in process and returns the exit code.
`--help` still exits through `argparse`.

## Supported standalone input documents

Each file is one complete standalone document. No envelope is unwrapped, no
member is selected out of a container, and no input is reduced or reinterpreted:

| Flag | Document |
| --- | --- |
| `--preview` | one `global_asset_master_theme_ingestion_preview/1` object, exactly as returned by `build_theme_ingestion_preview()` |
| `--master-source` | one original `global_asset_master_input/1` input or `global_asset_master_packet/1` packet |
| `--taxonomy-source` | the original ThemeTaxonomy/2 graph input document, not a claimed prevalidated report |
| `--requests` | a JSON array of explicit request objects, each with `asset_id`, `gam_membership_id`, `taxonomy_membership_id`, `evidence_id` and `gam_source_identity` |
| `--authority-registry` | the committed `theme_taxonomy_authority_registry/1` file, inside the git repository whose commit is pinned by `--trusted-commit` |

`--trusted-commit` must be a full 40- or 64-hex object name. A branch, tag or
`HEAD` is refused before any file is opened, because an authority boundary that
moves under the check is not a boundary.

## How inputs are read

Each file is read exactly once as raw bytes. Its SHA256 is compared to the
externally supplied digest **before** the bytes are decoded or parsed, so a file
that does not match what the caller pinned is never interpreted. Parsing then
rejects malformed JSON, duplicate object keys (which JSON itself would resolve
last-writer-wins), non-UTF-8 bytes, `NaN`/`Infinity` literals and numbers that
overflow to an infinity.

Expected digests come only from the command line. No expected digest is ever
read out of an input file: a document's own claim about its inputs would only
attest to itself. In particular `--expected-previous-master-sha256` is never
taken from `preview["input_digests"]["original_master_payload_sha256"]`.

The authority registry is the one input the CLI does not hand to the library as
a value: the library takes its path and re-reads it, then independently verifies
it and its approval evidence against the committed bytes at `--trusted-commit`.
Before either mode calls the library, the CLI additionally compares that
external digest to the registry blob at the same immutable commit and passes
the resolved file path onward. Thus a later successful library read, which
must match those committed bytes, is bound to the digest the caller supplied.
The local Git reads disable lazy fetching and have five-second timeouts. This
connects input identity; the existing authority resolver still decides whether
the registry and approval evidence authorize the graph.

## Validation-only effects

Validation-only calls the existing `validate_theme_ingestion_preview()` against
the supplied originals, which recomputes the whole preview and rejects a
self-rehashed edit. It emits one compact JSON line carrying the preview identity
and status (`payload_sha256`, `status`, `change`, `addition_count`,
`unchanged_count`, binding status, `master_id`, `as_of_date`, `failure_reasons`),
the preview's own `input_digests`, the five input file digests and the trusted
commit.

No destination, parent directory, lock sidecar, temporary file or output file is
created, and no destination is read. `destination_checked` is `false` and the
result carries `DESTINATION_APPLICABILITY_NOT_CHECKED`: this mode has not
checked, and does not claim, that the preview would apply to any destination.
Only the preview's own derivation and status are reported.

Exit `0` requires a preview that recomputed exactly and is
`STRUCTURAL_PREVIEW`, with no failure reasons, a non-null candidate and
`THEME_SOURCE_BINDING_VERIFIED`. Anything else, including a faithfully derived
`BLOCKED` preview, exits `1` with `PREVIEW_NOT_APPLICABLE` and the reasons.

## Application effects

`--apply` passes the caller's reviewed preview, the same originals, the registry
path, the trusted commit, the destination and the expected previous digest to
the existing `apply_theme_ingestion_preview()` with
`operational_application_approved=True`.

That flag expresses this explicit caller action and nothing else. It does not
authorize a graph, ratify Theme Authority, satisfy PIT or membership policy, or
change any authority output; the published packet keeps exactly the authority
block the existing builder produces, and the preview keeps
`master_population_authorized=false`.

All existing guarantees are preserved by delegation, not reimplemented: the
cooperative per-destination lock, the previous-digest and original-master
identity checks, full revalidation of the preview against the originals, the
stale-preview conflict, the immutable-commit requirement, source/PIT validation,
and `APPEND` versus explicitly rebuilt `NO_CHANGE`. A stale preview is a
conflict, never an idempotent retry, and this CLI performs no rebase or
re-preview on the caller's behalf.

On success it emits one compact JSON line with `outcome`
(`APPLIED_APPEND` or `APPLIED_NO_CHANGE`), `change`, `published`, the resolved
`destination_path`, the counts, the before/after master identity, the revalidated
preview digest, the input file digests and the trusted commit.

## Output, exit codes and failure

Standard output is always exactly one compact JSON line of outcome and hashes.
It never contains the master, taxonomy, requests, candidate packet or raw source
values. Failures add one concise line on standard error. Exit `0` is success;
exit `1` is any rejected input, argument error, non-applicable preview or failed
production validation.

A failure result carries `destination_state`:

- `NO_MODE_SELECTED_ARGUMENTS_REJECTED` — arguments were rejected, nothing read.
- `NOT_USED_IN_VALIDATION_ONLY_MODE` — no destination exists in this mode.
- `NOT_REACHED_NO_APPLICATION_ATTEMPTED` — apply was requested but an input or
  digest check failed first, so the application call was never made.
- `REQUIRES_INSPECTION` — the application call was made and raised.

An exception during application is **not** proof of rollback. This caller cannot
observe whether the library's single atomic publish already replaced the
destination, so it reports `applied: null`, makes no rollback claim, and states that the destination may
require inspection, and never retries automatically. The library documents that
failures before its final `os.replace` leave the destination bytes unchanged;
that is the library's property, not something this CLI verifies.

## Existing-source limitations

This connects the existing capability to explicit local files. It does not
supply what the existing sources still lack: the ratified authority registry
content, the canonical destination path and its retention/history policy, the
PIT and membership policy for superseding existing rows, and whether a persisted
application receipt is ever required. The repository's committed registry is
intentionally empty, so a preview built against it stays `BLOCKED` and cannot be
applied through this CLI either. Cooperative exclusion holds only among callers
of the library API; a destination that a foreign writer can also replace remains
outside what this boundary can promise.

## Not an operational admission

A callable consumer existing is not an operating master. Contract authority flags
remain false, the binding report keeps its
`THEME_MEMBERSHIP_INGESTION_NOT_IMPLEMENTED` marker, and Theme Authority
ratification, PIT/membership policy and reviewed live migration remain separate
canonical gates. Every result carries `NOT_AN_OPERATIONAL_ADMISSION`. No
scheduler, runtime, contract or config is changed. Its focused test is registered
in `run_all.py`, and the ingestion document links to this explicit caller.

The earlier ingestion document's “no CLI or operational caller” described the
scope implemented at that time. The currently adopted development scope adds
this explicit-file caller; it does not prohibit the caller or approve an actual
master migration. The existing library and source-policy contracts are intact.

## Focused verification

`test/test_global_asset_master_theme_application_cli.py` invokes the installed
CLI as a subprocess with externally hashed files in temporary directories,
against the real synthetic `AuthorityRepo` fixture, the real preview builder and
the unchanged production validators. Nothing is mocked into success and no real
master or dossier input is used.

Covered: validation-only reporting with no side effect anywhere; a real `APPEND`
publishing exactly the validated candidate; an explicitly rebuilt preview
applying as `NO_CHANGE` without writing; the stale preview replayed after an
append conflicting without mutation; a wrong external hash on each of the five
inputs and a malformed digest rejected before the destination is even opened;
four self-rehashed false previews rejected inside revalidation before any
destination mutation; an unauthorized (`PROPOSED`) registry blocking both modes;
malformed, duplicate-key and non-finite JSON failing closed; argument-shape and
destination-argument misuse failing closed with no destination created; and
`run(argv)` in process producing byte-identical output to the subprocess.
Two additional in-process regressions reproduce a registry change between
reads and a failure after the real atomic publication. They prove that the
external registry digest cannot describe different consumed bytes and that an
uncertain application result never reports a false rollback or retries.
