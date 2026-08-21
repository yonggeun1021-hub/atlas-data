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
