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
null. Contract v2 embeds and revalidates the exact P9-03 trigger-eligibility and
P9-02 important-event packets, then binds their packet hashes and evidence times
to the observation batch. A validly rehashed semantic mutation, a substituted
packet, or a packet from the future fails closed. P7-03 concentration and P7-06
planned-loss remain explicit SHA-256 lineage only because those capabilities do
not yet expose standalone packet validators; those hashes grant no semantic
authority.

The output embeds the normalized source batch, ratified policy, and both
validated P9 source packets and is fully re-derived on validation. The module
makes no provider request, writes only outside the repository, and grants no
notification, broker, Production, or trading authority.
