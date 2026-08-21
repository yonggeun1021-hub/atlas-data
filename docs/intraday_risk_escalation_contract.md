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
null. Contract v3 embeds and revalidates the exact P9-03 trigger-eligibility,
P9-02 important-event, P7-03 concentration, and P7-06 planned-loss packets with
their production validators. It binds all four packet hashes and evidence dates
or times to the observation batch, and requires the planned-loss packet to name
the exact concentration packet in its own lineage. A validly rehashed semantic
mutation, substituted packet, cross-packet lineage break, or future packet fails
closed.

The output embeds the normalized source batch, ratified policy, and all four
validated upstream packets and is fully re-derived on validation. The module
makes no provider request, writes only outside the repository, and grants no
notification, broker, Production, or trading authority.
