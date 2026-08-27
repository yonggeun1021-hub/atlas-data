# P4-03 DART structural content index

## Purpose

This slice makes retained OpenDART documents mechanically navigable without
claiming to understand them.  It consumes the existing validated DART metadata,
content-run record, receipt ZIP and member cache.  It does not call OpenDART or
any other provider.

For each raw-byte-verified member it records only:

- filing/member identity and exact byte hash/size;
- extension and text/binary status already established by the content manifest;
- start/end tag, table, row, cell and locator-attribute counts; and
- a structure-only hash derived from tag names and attribute *names*.

Filing text, attribute values, numeric values and semantic item labels are
discarded.  `semantic_items` is always empty.

## Immutable input boundary

`data/latest_dart.json` and `data/latest_dart_content.json` are rolling pointers.
Each structural packet therefore publishes exact-byte, content-addressed copies
of both files beside the packet.  Validation reads those snapshots, verifies
their SHA-256 digests, invokes the existing P3-08 DART observation validator,
revalidates the retained ZIP/member cache, and independently rebuilds the full
packet.  A newer mutable pointer is never substituted for historical inputs.

The workflow wall clock is only an upper-bound PIT check and is not persisted
as packet identity. `decision_at` is deterministically fixed to the later of
the exact source collector timestamp and exact content-run timestamp, with
`decision_time_basis=MAX_EXACT_SOURCE_AND_CONTENT_TIMESTAMPS`. Consequently,
re-running unchanged inputs later is byte-identical and creates no new packet.

The output directory is:

`data/observations/dart_structural_content_index/<source-date>/`

It contains `source-<sha16>.json`, `content-run-<sha16>.json`, one
`manifest-<subject>-<receipt>-<sha16>.json` for every indexed filing, and
`packet-<sha16>.json`.  Publication independently rebuilds the packet and
preflights every manifest before creating any output; the packet is written
last as the completion marker.  Existing content-addressed files are
byte-checked and never overwritten.

The exact manifests are retained with the packet, while ZIP/member bytes remain
under the existing P4-03 raw-cache retention contract.  If those governed raw
bytes are no longer available or differ from the retained manifest, replay
fails closed; it never substitutes a newer archive or manifest.

## Authority boundary

The only true authority field is `structural_evidence_only`.  Semantic item
extraction, interpretation, Rule evaluation, Stage promotion, Action, Order,
Production and trading are all false.  Structural counts and hashes cannot be
used as positive or negative investment evidence.

The packet remains blocked by:

- `DART_ITEM_EXTRACTION_POLICY_UNRATIFIED`; and
- `STRUCTURAL_INDEX_IS_NOT_SEMANTIC_EVIDENCE`.

An item-specific extraction contract, independently ratified evidence locators,
and downstream Rule bindings remain separate future work.
