# P4-02 → P5-03 TSM monthly-revenue exact link contract

This adapter is the single registered link from the retained TSMC SEC 6-K
monthly-revenue observation to existing `RULE-0007` and `RULE-0008`. It does
not evaluate either Rule. Both links use `ALL_REQUIRED`; every emitted Rule row
keeps `evaluation_status=EVALUATION_NOT_AUTHORIZED` and `rule_result=null`.

## Exact input and extraction boundary

The input is the latest unique content-addressed P4-04 observation packet built
from P4-02 retained bytes. The adapter independently validates that packet,
its referenced P4-02 manifest, and the exact primary gzip bytes. It accepts
only the unique latest `TSMC_CONSOLIDATED_MONTHLY_REVENUE` economic period and
only the two registered published fields: monthly YoY and cumulative YoY.

The selected table row is reparsed from the retained primary document. The
complete normalized row is preserved as the exact quote, its unique character
offset is recorded, and the registered column identity is checked against the
published value. Missing inputs, duplicate periods, non-unique quotes,
unregistered measurements, lineage drift, or value conflicts fail closed.

## Preservation boundary

The full-submission and filing-index bodies are not copied into the output.
Their canonical SEC URLs, SHA-256 digests, byte counts, and document names are
frozen as lineage identities with
`URL_SHA_LINEAGE_ONLY_BODY_NOT_PRESERVED`. The primary document keeps the
existing P4-02 `raw_cache_policy`; this adapter neither extends nor shortens
the Stage-based 90-day/permanent boundary. Canonical URL, SHA, quote, offset,
measurement, unit, and period remain frozen in the P5-03 packet.

## Authority boundary

This is linkage only. It creates no source ranking or fallback, threshold,
interpretation, Rule PASS/FAIL, candidate, Stage, Buy, Action, Order, broker,
REAL, live, Production, or Trading authority. SNDK is not registered and
remains observation-only. A scheduled packet proves only that the binding ran;
whether the first post-adoption natural packet satisfies a WBS Exit Gate is a
separate canonical review.
