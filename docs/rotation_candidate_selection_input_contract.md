# Rotation Stage 3 candidate-selection input projection

`briefing/rotation_candidate_selection_input.py` turns four caller-supplied,
revalidated packets into the non-interpretive input a later candidate-selection
stage would read:

1. `rotation_discovery_briefing_packet/4`,
2. the exact `rotation_state_ledger_packet/1` it binds,
3. `capital_flow_posture_reference/v1` (Stage 2), and
4. the exact `paper_regime_reference/v2` (Stage 1) bound at Stage 2 source index
   `0`.

It is a projection, not a stage: it answers "which rotation state changes and
which upstream lineage are available to inspect", never "which one is the
candidate".

## Input admission

The accepted input is a caller-supplied **four-packet tuple**. All four objects
are required. The module does not discover a file (including a latest Stage 1
pointer), call a provider, or touch the network, and it has no default input
path. Before any field is read, the briefing must

1. be a `dict`,
2. declare `schema_version == rotation_discovery_briefing_packet/4`,
3. declare `contract_version == rotation_discovery_briefing/4`, and
4. still pass the unmodified `rotation_discovery.validate_briefing()`.

A briefing that was mutated after it was hashed, had its authority flags
opened, or was downgraded to an earlier schema fails closed as
`SOURCE_BRIEFING_REVALIDATION_FAILED` / `SOURCE_BRIEFING_SCHEMA_INVALID`. The
source packet is never repaired, normalised, or trusted on its declared digest
alone, and it is deep-copied before validation so the caller's object is never
mutated.

### Why the briefing alone is not an admissible source

Passing the producer's validator only proves a briefing is *internally*
self-consistent. A tamperer who edits `rotation.latest_changes` and then
repairs `state_counts`, `latest_change_count`, `summary.rotation_change_count`
and `packet_sha256` produces a **self-resigned** briefing that the unmodified
producer still accepts. Its own rows can therefore never be the authority for
this projection.

The ledger the briefing bound by `rotation.source_ledger_sha256` is required as
a second input and must

1. be a `dict`,
2. declare `schema_version == rotation_state_ledger_packet/1`,
3. declare `contract_version == rotation_state_ledger/1`,
4. still pass the unmodified `rotation_state_ledger.validate_ledger()`, and
5. hash to exactly the `source_ledger_sha256` the briefing declared.

A ledger that fails 1–4 is `SOURCE_LEDGER_INVALID` /
`SOURCE_LEDGER_SCHEMA_INVALID` / `SOURCE_LEDGER_CONTRACT_INVALID` /
`SOURCE_LEDGER_REVALIDATION_FAILED`; a valid but different ledger substituted
for the bound one is `SOURCE_LEDGER_NOT_BOUND`.

The whole rotation section is then re-derived from that ledger using the
producer's own derivation — reused rather than reimplemented, so this
projection cannot diverge from it or introduce a second rotation policy,
ordering rule, or state mapping. The briefing's stored section is compared
against the re-derivation and never trusted: a row that was added, dropped,
reordered, restated, or rehashed fails closed as
`SOURCE_ROTATION_SECTION_TAMPERED` **even when the counts and `packet_sha256`
were recomputed**, and so does an edited `ledger_status`, `ledger_revision`, or
`source_boundaries`.

### Stage 2 to Stage 1 admission

The two upstream packets are independently passed to their existing producer
validators, rather than accepted from their declared hashes. The Stage 2
packet's exact source entry at index `0` must then equal the supplied Stage 1
packet's complete identity:

`source_type`, `path`, `sha256`, `schema_version`, `contract_version`,
`payload_sha256`, and `generation_id`.

The Stage 1 `file_sha256` is computed with the producer's immutable packet-byte
format and must therefore equal the digest Stage 2 actually bound. Stage 2's
file digest is computed in the same producer format and carried separately.
The admitted Stage 1 market rows must occur exactly as `US`, `KR`, `CRYPTO`.
There is no aliasing or normalisation such as `KOREA` to `KR`.

Only the exact four fields `market`, `as_of_date`, `candidate_regime`, and
`runtime_regime` are projected from each Stage 1 market row. `runtime_regime`
must remain `UNKNOWN`. The module does not derive a natural-observation label,
promote the paper regime, or interpret the Stage 2 posture as investability.

## Projection

`rotation.latest_changes` is projected one-to-one from the **ledger-derived**
section, in the order the Rotation Discovery derivation already fixes
(`market_order` → `scope_id` → `entity_id`). No row is added, dropped,
reordered, merged, or filtered, and neither source object is mutated.

Each row copies exactly these source fields and nothing else:

`market`, `scope_id`, `entity_id`, `as_of_date`,
`structural_bucket_transition`, `prior_state`, `current_state`,
`state_transition`, `record_sha256`, `source_packet_sha256`.

Each row then carries the fixed closed constants:

| field | value |
| --- | --- |
| `selection_rank` | `null` |
| `selected` | `false` |
| `candidate_eligible` | `false` |
| `ready_status` | `NOT_EVALUATED` |
| `promotion_status` | `PROMOTION_NOT_AUTHORIZED` |
| `action` | `null` |

These are constants, not defaults: the validator rejects any row where one of
them has been opened, including a boolean silently replaced by an equal-valued
integer. A row therefore cannot be read as a ranked, selected, eligible, ready,
promoted, or actionable candidate.

## Carried metadata and bound hashes

`source` carries the briefing's `contract_version`, `schema_version`, `slot`,
`generated_at`, and `status` verbatim, plus the projected section name and two
bound source hashes: `briefing_sha256` (the source packet digest) and
`rotation_ledger_sha256` (the ledger digest the briefing itself bound).
`rotation_ledger_sha256` is the digest of the ledger packet that was actually
supplied and re-derived from, so it is a proven binding rather than a carried
claim. `unresolved_boundaries` is carried verbatim from the briefing; this
projection neither resolves nor adds a boundary.

`stage1_lineage` is a distinct top-level object. It carries the admitted Stage
1 schema, contract, generation ID, payload digest, producer-format file digest,
and the exact three four-field market rows. Its nested `stage2_binding` carries
the admitted Stage 2 schema, contract, generation ID, payload digest,
producer-format file digest, source index, and the closed status
`EXACT_STAGE2_TO_STAGE1_BINDING_REVALIDATED`. It is not folded into Rotation
rows and is not a candidate label.

The packet is closed with a deterministic `payload_sha256` over its canonical
JSON, so the same source pair always yields the same bytes and a different one
always yields a different digest.

## Validator

`validate_candidate_selection_input(packet, briefing, rotation_ledger,
stage2_reference=..., stage1_reference=..., ...)` re-derives the whole
projection from the same four-packet tuple and requires exact equality before
recomputing the digest over the re-derived bytes. It does not check the packet
for internal self-consistency and then trust it. Consequently a reordered,
resized, rescored, field-added, metadata-edited, boundary-edited,
lineage-rebound, or hash-rebound packet fails as `INPUT_DERIVATION_MISMATCH`,
and a packet presented against a different source fails. All four producer
admission paths run during validation too.

## Authority this projection does not have

The contract's `authority` block keeps `input_projection_only` true and every
other flag false: no candidate selection, ranking, scoring, readiness
evaluation, stage promotion, action generation, persistence default, runtime
regime, capital, order, production, or trading authority. Every row retains
`selection_rank=null`, `selected=false`, `candidate_eligible=false`, and
`action=null`. The module adds no metric, threshold, score, weight, TTL, or
policy of any kind.

## Tracked output

Output may be written only outside the repository, and `Path.resolve()` alone
is not a sufficient guard because it follows symlinks in both directions. A
lexically in-repository path whose parent is a symlink resolves *outside* the
tree and would pass a resolved-only check while the tracked path is still what
gets created; an out-of-repository path can equally resolve back *into* the
tree. `write_json_atomic()` therefore rejects

* a lexical or fully resolved path inside the repository, as
  `TRACKED_OUTPUT_FORBIDDEN`, and
* the target itself or any parent being a symlink that either sits inside the
  repository or points into it, as `TRACKED_OUTPUT_SYMLINK_FORBIDDEN`.

The guard runs before the parent directory is created and again immediately
before `os.replace`, so a symlink swapped in after the first check still fails
closed and the temporary file is removed.

## Regression registration

`test/test_rotation_candidate_selection_input.py` is the authoritative test for
this contract and is registered in `run_all.py`'s `APPROVED_TESTS`, next to the
`test/test_rotation_discovery_briefing.py` producer invariant it depends on, so
the projection is covered by the standard regression run rather than by a
focused invocation only.

## CLI

```
python3 briefing/rotation_candidate_selection_input.py <briefing.json> \
    --rotation-ledger <rotation_state_ledger.json> \
    --stage2-posture-reference <capital_flow_posture_reference.json> \
    --stage1-paper-reference <paper_regime_reference.json> \
    --out <path outside the repo>
```

All four source paths are required; there is no default for any of them, and no
source is discovered.

## Remaining unresolved boundaries

US and GAM source admission remain an `ADMISSION_GAP` upstream, so this
projection is exercised against the existing Rotation Discovery briefing
sources only; it does not itself admit a new source. Candidate selection,
ranking, and promotion policy remain unratified, so no consumer of this input
is authorised to act on it yet. If an actual briefing or its bound rotation
ledger is unavailable, no natural Stage 3 packet is emitted; availability of
the Stage 1/2 lineage alone cannot manufacture one.
