# P3-06 Expectations / Estimate Revision Radar

`discovery/expectations_revision.py` compares exactly two vintages of the same
market, asset, metric, fiscal period, statistic, unit, and comparison basis. It
records the latest-minus-prior change as `UPWARD`, `DOWNWARD`, `UNCHANGED`, or
`UNKNOWN_EVIDENCE`. A confirmed non-zero change creates a lineage-complete
observation case without making the asset a candidate.

The repository does not select or buy a consensus provider. The caller must
supply an effective CIO-ratified source contract that binds the provider ID,
host, market coverage, `available_at` semantics, license evidence, and permitted
research use. This keeps provider choice, cost, and licensing outside the code.

There is no importance label, ranking, Stage promotion, Rule evaluation,
Action, Production, or trading authority. Live source selection and operational
population remain P4-05 / P3-06 Exit Gate work; paid data requires explicit user
reapproval.
