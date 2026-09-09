# AI external-analysis Shadow supplemental evaluation

This preserves the existing `ai_external_analysis_shadow_extension_record/1`
contract and consumes hash-only private receipt/output references in memory.
It does not bind a record to authenticated Stage3/P10 source content yet:
`STAGE3_CONTRACT_UNBOUND` is mandatory and no effectiveness claim is allowed.

An unbound row must use ANNOTATION_ONLY, empty AI event features and null return,
drawdown and cost. The comparator rebuilds all semantics, checks chain sequence,
previous hashes, real UTC dates, temporal order and duplicate P10 decision
identities. Changing the model or prompt does not create an independent sample.
The record hash proves content integrity, not source authenticity.

Portal projection revalidates the supplied records and recomputes comparison
counts. Without records only a zero-count comparison is accepted. Metrics and
market/regime segmentation remain null, and `notUsedInScore` is always true.
Neither arbitrary metrics nor raw source fields can pass the projection.
`minimum_observations=30` is a provisional display threshold, not a ratified
sample-sufficiency policy; any count at or above it still requires a bound
sample and cannot unlock metrics, score changes or trading authority.

Runtime stores structured rows and evaluation ledger on the verified durable
volume. Public Git stores only code/contracts/tests/aggregate schema. Private
Git stores compact immutable receipts. Do not commit source text, prompts or
symbol-level runtime rows. Actual Stage3 source/receipt/output resolution and
same-cutoff baseline validation remain a separate incomplete integration gate.
