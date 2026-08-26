# Data Coverage Matrix Contract (P4-01 inventory + P4-06 provenance boundary)

## What the matrix proves

The matrix inventories every current data consumer in three upstream sets:

- 15 Regime inputs: three markets times five required axes;
- 11 Discovery inputs copied from the P3 WBS snapshot dated 2026-08-20;
- all 25 members of the authoritative Rule SSOT.

Each consumer has four explicit dimensions: source, freshness, cost, and
fallback. A dimension may be unresolved. That is a recorded gap, not an empty
cell and not an implicit success.

`inventory_complete=true` means all 51 expected consumers were found exactly
once. It does not mean their data is operationally complete. The latter is
only a declared audit classification when every source is marked qualified,
freshness and fallback are marked defined, and cost is free or free-tier. It
is not runtime evidence eligibility or policy authority.

## Input authority and drift detection

The builder reads:

- `config/data_coverage_matrix_contract.json` for the closed schema and status
  vocabularies;
- `config/data_coverage_registry.json` for audited source and WBS mappings;
- `config/regime_output_contract.json` for the exact market/axis cross-product;
- `config/rules.json` for current Rule membership and state.

It rejects missing or duplicate consumers, unknown source references, missing
source-evidence files, an incomplete mapping for a `SOURCE_RESOLVED` Rule, and
contract vocabulary drift. The default contract, registry, Regime contract,
and Rule contract must be clean and byte-identical to the current `HEAD` blob.
The output records canonical SHA-256 values for all four inputs and can be
revalidated against them.

## P4-06 source-evidence provenance boundary

Every source catalog row binds its evidence reference to:

- a normalized repository-relative path with no absolute/path-traversal form;
- the exact current file bytes (`evidence_sha256`);
- the earliest commit in full git history containing those exact bytes; and
- that commit's committer timestamp.

The builder independently recomputes all four facts. Dirty evidence files,
shallow/unavailable history, hash drift, and first-seen backdating fail closed.
This proves the catalog is referring to an exact, historically observable
repository artifact; existence of an arbitrary file is no longer sufficient.

It deliberately does **not** prove that the referenced artifact is the correct
business source for the consumer, nor that a free-text freshness/fallback
claim is ratified. A reviewed catalog update can rebind a source row to another
tracked artifact, so `dimension_claim_scope` remains
`DECLARED_AUDIT_CLASSIFICATION_ONLY` and `runtime_evidence_eligibility` remains
`NOT_AUTHORIZED_BY_THIS_AUDIT`. Independent source-qualification, freshness,
fallback, evaluator, Production, and trading authorities all remain false.

Rule source status is never guessed. A Rule with `data_status=AVAILABLE` but no
`source_qualification` remains `UNRECORDED`; availability is not silently
promoted into source qualification. Rule freshness and fallback stay
`UNRESOLVED` until separately ratified.

## Cost and authorization boundary

The committed catalog currently contains only free public sources and one
free-tier transient source. If any catalog entry changes to
`PAID_REAPPROVAL_REQUIRED`, every affected consumer is listed in
`paid_source_reapproval_required_for`. The matrix cannot select or purchase
that source.

Source selection, freshness/fallback ratification, evaluator wiring,
Production wiring, and trading action are all false. The matrix has no network
call, scheduled workflow, tracked output, or runtime authority.

## Usage

Build to standard output:

```bash
python3 audit/data_coverage_matrix.py build
```

Write atomically to a temporary or user-selected path:

```bash
python3 audit/data_coverage_matrix.py build --out /tmp/atlas-coverage.json
```

Revalidate that output against the current inputs:

```bash
python3 audit/data_coverage_matrix.py validate /tmp/atlas-coverage.json
```
