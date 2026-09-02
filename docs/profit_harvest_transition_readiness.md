# P7-11 formal-transition readiness

`portfolio/profit_harvest_transition_readiness.py` is a money-free,
evidence-only adoption audit for canonical WBS row P7-11 (page
`3c49f2d7-3c84-8138-8644-eee246dd713f`, Order `711`). It does not replace the
current `profit_harvest_readiness/1` operational validator, change its locked
packet, or implement a Harvest action.

## Why this is separate

The current P7-11 packet correctly proves that policy is unratified, P8-13 has
no executable proposal, no canonical live position is connected, and no
Harvest quantity, action, proceeds, reallocation handoff or order exists. That
closed-state validator must remain exact.

Formal transition needs a different proof path. A future settled-proceeds
claim must not become usable merely because an amount is positive or because a
packet can be rehashed. The transition audit therefore keeps these gates
independent:

1. an externally ratified, effective P7-11 evidence policy whose exact hash is
   supplied through a separate trusted pin;
2. settled-proceeds origin from a private virtual-ledger SELL-fill
   reconciliation;
3. exact settlement, ledger, instrument, entry-order, exit-order and fill
   identity;
4. a strictly positive canonical decimal string plus explicit currency (never
   a JSON number, Boolean alias, zero, exponent or noncanonical trailing zero);
5. an exact P8-13 HARVEST/REDUCE/EXIT review-proposal lineage link;
6. an exact future P7-10 consumer-contract link that explicitly accepts the
   settlement receipt schema; and
7. the first independently pinned `NATURAL_AUTOMATED` / `SCHEDULE` attestation,
   occurring after ratification becomes effective and enclosing the runtime,
   settlement and completion timestamps in order.

Ratification without a natural settlement yields
`WAITING_FIRST_GENUINE_SCHEDULED_NATURAL_EVIDENCE`. Natural settlement without
both exact links yields `BLOCKED_EXACT_LINEAGE_OR_CONSUMER_LINKAGE`. Only all
seven gates yields `ADOPTION_READY_LOCAL_ONLY`.

## Authority boundary

The readiness output deliberately redacts the validated amount and currency.
It exposes only hashes, exact integer counts, gate states and blockers.
`candidate=NONE`, `capital=0`, every amount/proceeds/proposal/order field is
`null`, `recommended_action=NONE`, and every Stage, Candidate, Buy, Harvest,
Quantity, Reallocation, Action, Order, Production and Trading authority remains
`false` in every result, including `ADOPTION_READY_LOCAL_ONLY`.

The module has no CLI, filesystem write, network, credential, broker, scheduler
or WBS mutation path. A later accountable owner must separately merge the
P8-13 predecessor, the P7-11 validator, and a P7-10 consumer in that order,
then obtain post-ratification genuine scheduled evidence before proposing any
canonical status change.
