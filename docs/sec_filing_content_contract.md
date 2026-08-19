# P4-02 SEC filing content acquisition

This capability implements the approved SEC Evidence-layer contract. It does not
interpret a filing, evaluate an Atlas Rule, change Stage, or create a trading action.

## State separation

Each filing exposes independent `discovery_status`, `content_status`,
`evidence_status`, `interpretation_status`, and `rule_impact` fields. A discovered URL
is never reported as consumed content. Evidence can be `OK` while interpretation
remains `UNDETERMINED` and `rule_impact` remains `NONE`.

## Scope and fail-closed boundaries

- Ready, Buy, Holding, and Candidate material filings are required captures.
- Discovery material filings are best-effort. An unassigned Stage is index-only.
- Material forms are 8-K, 10-Q, 10-K, 6-K, and 20-F, including `/A` amendments.
- The primary document and every SGML/index-confirmed `EX-99.*` are one content unit.
- A document above 5 MiB, more than three EX-99 exhibits, missing identity evidence,
  conflicting primary/secondary types, or a changed source hash remains `PENDING`.
- Silent truncation, filename guessing, first-candidate selection, and automatic source
  mutation overwrite are prohibited.

## Canonical and cache records

The canonical record is URL + SHA-256 + extracted values with exact quote and offset.
Raw documents are deterministic gzip cache entries. Required-stage material raw is
permanent; best-effort cache may be deleted after 90 days. A successful unchanged
capture is recorded as `skipped(already_captured)` without another provider call.

The first registered extractor is deliberately narrow: TSM accession
`0001046179-26-000536`. It extracts the three approved currency-separated observations
and leaves interpretation `UNDETERMINED`, Rule impact `NONE`, and action `NO_CHANGE`.
Every other document remains `evidence_status=PENDING` until an explicit extractor is
registered; the collector never guesses.

## Operations

The Daily Collect workflow runs this as a content-only repair path even when the raw
metadata Guard is fresh. The helper independently requires today's KST SEC metadata.
Failures are written to `data/latest_sec_content.json`; successful manifests and raw
cache are under `data/sec_content/<TICKER>/<ACCESSION>/`. The workflow preserves these
records even when the content step reports a non-zero result.
