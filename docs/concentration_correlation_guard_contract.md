# P7-03 Concentration / Correlation Guard Contract

`portfolio/concentration_correlation_guard.py` is an offline, policy-gated risk
guard. It measures four independent exposure axes from an explicit long-only
portfolio snapshot:

- single asset NAV weight;
- market NAV weight;
- fractional, evidence-bound theme NAV weight; and
- gross weight of connected components whose positive Pearson correlation is
  at or above an externally ratified threshold.

The repository contains no default limit, correlation threshold, lookback, or
return basis. A policy is accepted only when its exact packet is `RATIFIED` by
`CIO`, effective for the evaluation date, and carries the contract's closed
authority map. The input must contain every unordered pair among active assets;
partial correlation coverage is rejected rather than treated as zero.

Theme fractions are explicit inputs and must sum to one per asset. The guard
does not infer a theme, correlation cluster, market weight, or missing pair.
Portfolio, bucket, taxonomy, correlation-dataset, position, identity, and
membership evidence hashes are preserved as lineage.

Contract v2 embeds the canonical validated input and exact ratified policy
packets. `validate_packet()` re-runs both production input validators and
re-derives every exposure, cluster, breach, summary, authority, and lineage
field. Rehashing a changed output or embedded policy therefore cannot turn a
semantic mutation into valid evidence.

The P8-06 briefing consumer remains pinned to the prior v1 identity until its
own versioned migration. Contract v2 therefore establishes a producer
capability; it does not silently change an existing consumer contract.

`WITHIN_RATIFIED_LIMITS` and `LIMIT_BREACH` are risk-check results only. Neither
result chooses a reduction, target weight, position size, hedge, or order.
Those fields stay null/empty and Production/trading authority remains false.
CLI output is forbidden anywhere inside the repository tree.
