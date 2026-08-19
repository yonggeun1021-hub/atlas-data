# Data Coverage Matrix Contract (P4-01)

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
true only when every source is qualified, freshness and fallback are defined,
and cost is either free or free-tier.

## Input authority and drift detection

The builder reads:

- `config/data_coverage_matrix_contract.json` for the closed schema and status
  vocabularies;
- `config/data_coverage_registry.json` for audited source and WBS mappings;
- `config/regime_output_contract.json` for the exact market/axis cross-product;
- `config/rules.json` for current Rule membership and state.

It rejects missing or duplicate consumers, unknown source references, missing
source-evidence files, an incomplete mapping for a `SOURCE_RESOLVED` Rule, and
contract vocabulary drift. The output records canonical SHA-256 values for all
four inputs and can be revalidated against them.

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
