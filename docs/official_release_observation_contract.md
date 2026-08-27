# P4-04 retained official-release observation contract

This provider-free adapter turns exact TSMC SEC 6-K bytes already retained by
P4-02 into evidence-only monthly-revenue observations. It does not call SEC,
TSMC IR, or any other provider.

## Accepted identity

Only a P4-02 manifest that independently passes `validate_manifest()` and whose
single primary document passes the existing
`parse_retained_monthly_report()` identity, table-cardinality, unit, publication
date, and published-value checks can become an observation. Other valid TSMC
6-K filings are retained in the packet as `NOT_MONTHLY_REVENUE_REPORT`; an
identified but malformed monthly-revenue report fails the entire build rather
than being silently excluded.

Every row binds the exact manifest bytes, permanent raw-cache path and digest,
SEC source URL, accession, and P4-02 retrieval time. `decision_at` is only a PIT
upper bound. The persisted `evidence_as_of` is derived from eligible retained
inputs, so running later with unchanged inputs is byte-identical. A document's
published date may not be later than the verified capture date; the actual
PIT-availability gate remains the precise P4-02 retrieval timestamp.

## Publication and validation

Packets are content-addressed and append-only. Validation recomputes the packet
from the referenced governed manifests and raw gzip bytes at the packet's own
`evidence_as_of`; recomputing the packet hash cannot make altered published
values, counts, exclusions, or authority valid.

## Deliberate boundary

The packet records the company's published monthly revenue, monthly YoY,
cumulative revenue, and cumulative YoY values. It assigns no positive/negative
meaning, performs no threshold comparison, and grants no Rule, Stage, Action,
Order, Production, or trading authority. `source_hierarchy_status` remains
`UNRATIFIED_NO_GLOBAL_RANKING`. TSMC IR remains a separate human secondary
verification surface under the existing contract.
