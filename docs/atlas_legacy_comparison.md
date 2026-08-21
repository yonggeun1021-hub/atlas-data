# P10-02 Atlas vs Existing Judgment Comparison

`shadow/atlas_legacy_comparison.py` aligns the P10-01 Shadow ledger, externally
recorded existing-method judgments, and externally recorded same-window outcomes
by exact `(decision_id, market)` keys. Contract v2 consumes the Shadow ledger v2
chain, so its comparison evidence inherits the validated P9-03 and P9-05 source
lineage without interpreting the eligibility or risk values.

Missing legacy judgments and outcomes remain explicit. Atlas action is not
inferred from Regime or `NO_ACTION_AUTHORIZED`; while P8 action authority is
closed it remains undefined. The packet can report only whether two explicit
action labels are the same or different. Effectiveness and winner fields stay
`NOT_EVALUATED` / `null` until a separate evaluation policy is ratified and real
Shadow observations exist.

The CLI is offline and may write only outside the repository. The module does
not change Atlas decisions, interpret performance, select a winner, generate an
action, or authorize Production or trading.
