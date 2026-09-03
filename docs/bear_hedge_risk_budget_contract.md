# Bear / Hedge Risk Budget Registry (P6-03)

Status: explicit budget-set validator implemented; no repository default
budget, automatic allocation, hedge sizing, Production, or trading authority.

## Contract

Bear/Hedge risk uses a budget that is explicitly separate from the long budget.
Atlas accepts only a CIO-ratified, hash-bound external budget set.  The set
binds distinct portfolio-loss and long-budget source hashes.  Each effective
budget record defines one portfolio-total or market scope and must provide:

- maximum loss and maximum gross exposure in the declared `NAV_FRACTION` unit;
- a positive holding horizon in calendar days;
- the exact approved hedge-eligibility registry SHA;
- an exact budget-basis reference and SHA;
- non-overlapping effective dates.

The validator checks values for type, finiteness, non-negativity, lineage,
identity stability, and deterministic ordering.  It does not choose the
numbers, compare them with an invented threshold, or infer a budget from the
long book.

Authority flags are exact JSON booleans at every boundary. Python considers
numeric `1 == true` and `0 == false`, but the contract, external budget set,
embedded source packet, and standalone output validator reject those numeric
aliases instead of normalizing them into authority-bearing booleans. Canonical
boolean packets and their schema/version/bytes are unchanged.

## Authority boundary

Validated budgets remain definitions, not allocations.  `budget_usage` and
`hedge_size` are `null`; `order_intents` is empty.  A future portfolio usage
adapter and explicit sizing/order authorization are separate gates.

There is intentionally no committed default budget set.  Until one is
separately ratified, the operating Exit Gate remains open.

## Offline use

```bash
python3 portfolio/bear_hedge_risk_budget.py /tmp/budget-set.json \
  --as-of-date 2026-08-21 \
  --out /tmp/bear-hedge-budget.json
```

The CLI is offline and refuses to write inside the repository.

Output schema v2 embeds the exact `RATIFIED` set in
`source_packets.BUDGET_SET`. `validate_packet()` reruns the complete budget-set
validation at consumption time, including CIO identity, effective interval,
record history, distinct long/portfolio lineage, authority, and the original
set SHA. It then re-derives active budgets, the summary, null usage/sizing, and
the empty order list. An unratified budget cannot be added by merely
recomputing the output hash; no default budget or allocation authority is
added.
