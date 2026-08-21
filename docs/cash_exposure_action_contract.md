# Cash / Exposure Reduction Action Boundary (P6-01)

Status: independent action boundary implemented; evaluation policy, portfolio
snapshot, risk budget, Production, and trading authority are not ratified.

## Boundary

Holding cash and reducing long exposure are first-class portfolio actions.
Neither means opening a short, selecting an inverse instrument, constructing a
hedge, or creating an order.  The packet therefore keeps separate
`cash_action` and `exposure_reduction_action` fields while independently fixing
all short, hedge, and order intent collections to empty.

The current upstream `regime_output/v1` runtime authorizes only `UNKNOWN`.
There is no approved cash/exposure policy, portfolio exposure snapshot, or
portfolio risk budget.  Consequently the only current result is
`CASH_EXPOSURE_ACTION_NOT_EVALUATED`; action fields and target weights are
`null`, and position adjustments are empty.  `NO_CHANGE` is not used as a
substitute because that would be an evaluated portfolio decision.

## Required future inputs

Evaluation remains blocked until all of these are independently ratified and
implemented:

- an authorized Regime classification;
- an exact portfolio exposure snapshot;
- a cash/exposure action policy;
- a portfolio risk budget;
- action risk checks.

This contract does not define thresholds, target cash, target gross exposure,
position sizing, or market allocation.  Those values must not be inferred from
the action vocabulary or from a future `RISK_OFF`/`STRESS` label.

## Offline use

```bash
python3 portfolio/cash_exposure_action.py /tmp/regime.json \
  --out /tmp/cash-exposure-action.json
```

The CLI validates the upstream Regime packet and exact hash lineage, is
deterministic, makes no network call, and refuses to write inside the
repository.  It creates no tracked packet, workflow, notification, Production
action, or trade.
