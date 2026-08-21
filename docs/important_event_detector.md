# P9-02 Important Filing / News Event Detector

`execution/important_event_detector.py` applies an externally ratified CIO
policy to normalized SEC, DART, and official-news events by exact
`(source_kind, market, event_type)` key. Only confirmed events matched to an
`IMPORTANT` rule become `ESCALATED`; routine, unmatched, and evidence-blocked
events remain distinct.

For escalated events the packet measures delay from `available_at` to the
caller-supplied detection time and reports `ON_TIME` or `LATE` against the
ratified rule. It does not infer event types, invent an importance policy, send
a notification, promote a candidate, create an action/order, or authorize
Production/trading.

The repository has no default importance policy and no live normalized
SEC/DART/news adapter wiring. The CLI is offline and writes only outside the
repository; those operational integrations remain separate Exit Gate work.
