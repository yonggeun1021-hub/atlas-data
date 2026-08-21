# P9-03 ENTRY / EXIT trigger eligibility

This read-only contract joins the validated P8-02 Unified Decision packet with
the validated P9-01 intraday freshness packet. It audits whether the minimum
evidence needed for a later ENTRY or EXIT evaluation is present without
inventing a trigger policy.

`READY`, a generic observed signal, and a fresh quote are necessary evidence;
they are not sufficient for ENTRY. The existing signal has no ENTRY/EXIT kind
authority. EXIT additionally requires separately ratified position, Rule, and
trigger evidence, which is not available in the current contract.

Accordingly every subject remains `NOT_EVALUATED` with `eligible=null`,
`trigger=null`, `action=null`, `position_size=null`, and `order_intent=null`.
Missing or stale quotes and an unavailable Action Boundary are explicit. Full
source packets and SHA-256 lineage are embedded and revalidated, and any output
derivation or authority drift fails closed.

There is no repository default trigger policy. This module makes no provider
request, writes only outside the repository, and grants no action, order,
Production, broker, or trading authority.
