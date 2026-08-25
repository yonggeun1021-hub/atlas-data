# P9-02 Important Filing / News Event Detector

`execution/important_event_detector.py` applies an externally ratified CIO
policy to normalized SEC, DART, and official-news events by exact
`(source_kind, market, event_type)` key. Only confirmed events matched to an
`IMPORTANT` rule become `ESCALATED`; routine, unmatched, and evidence-blocked
events remain distinct.

Contract v2 embeds the exact normalized event batch and ratified policy in the
result. `validate_packet()` therefore revalidates both sources and their SHA
lineage without requiring caller-supplied side inputs; a self-rehashed source
authority mutation fails closed.

For escalated events the packet measures delay from `available_at` to the
caller-supplied detection time and reports `ON_TIME` or `LATE` against the
ratified rule. It does not infer event types, invent an importance policy, send
a notification, promote a candidate, create an action/order, or authorize
Production/trading.

The repository has no default importance policy.  The provider-free
`execution/important_event_observation_population.py` adapter revalidates the
exact published P3-08 SEC packet and emits a content-addressed append-only
normalized observation packet on the existing Daily Collect path.  Current
P3-08 evidence retains the official filing date and Atlas retrieval timestamp,
but not an authoritative filing-time timestamp.  Consequently every adapted
row is explicitly `BLOCKED` with `EVENT_TIME_PRECISION_DATE_ONLY`; this wiring
cannot create an escalation even if a policy is supplied later.
The required `event_at` field carries only a date-floor placeholder and is
separately marked `EVENT_AT_DATE_FLOOR_PLACEHOLDER`; it must not be interpreted
as an asserted filing timestamp.

DART/news/crypto adapters, intraday polling, a RATIFIED importance policy, and
notification delivery remain separate Exit Gate work.  The detector CLI stays
offline and never authorizes candidate promotion, action/order, Production, or
trading.
