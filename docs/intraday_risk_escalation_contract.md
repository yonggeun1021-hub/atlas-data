# P9-05 Intraday Risk Escalation

This offline evaluator computes four observable intraday risk metrics from a
caller-supplied normalized batch: drawdown from the reference close, negative
opening gap, bid/ask spread in basis points, and volume relative to the expected
volume at the observation time.

Thresholds exist only in an external, effective-dated CIO-ratified policy. The
repository has no default threshold. Values above maximum drawdown, gap, or
spread thresholds and values below the minimum relative-volume threshold are
reported as `ALERT`; equality is a pass.

An alert is evidence, not an action. `exposure_reduction_candidate`,
`stop_candidate`, `action`, `position_size`, and `order_intent` always remain
null. P9-03 trigger eligibility, P9-02 important events, P7-03 concentration,
and P7-06 planned-loss packets are carried as exact SHA-256 lineage only and
grant no semantic authority.

The output embeds the normalized source batch and ratified policy and is fully
re-derived on validation. The module makes no provider request, writes only
outside the repository, and grants no notification, broker, Production, or
trading authority.
