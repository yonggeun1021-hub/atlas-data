# P7-11 Profit Harvest Policy Boundary

This boundary converts the approved structure of the P7-11 design draft into a
machine-checkable, non-executable contract. It deliberately contains no gain,
giveback, horizon, sell-ratio, core-weight, quantity, or minimum-sample number.

The four axes are independent: trigger eligibility does not select an action;
an action-family label does not authorize quantity; expected proceeds are not
settled cash; and no result label from the baseline audit may be fed back into
the operating population.

The only accepted upstream state is the existing locked
`profit_harvest_readiness/1` packet. The emitted boundary always has action
`NONE`, empty review items, null harvest/quantity/reallocation proposals, and
all execution authority false. H2 is a design preference only, not a policy.

This foundation does not complete P7-11. Live canonical position eligibility,
P8-13 proposals, outcome-independent observations, CIO recommendation, user
ratification, and a separately approved quantity contract remain required.
