"""Portfolio Risk Input Contract -- a READ-ONLY account-facts snapshot.

Purpose: NOT "decide how much to buy". Supplies the real, PIT-safe account
facts (NAV, cash, positions, exposure) a FUTURE sizing/policy decision will
need. Risk-budget percentages, stop-loss caps, max-concurrent-Probe counts,
and any other policy numbers are NOT ratified or implemented here -- see
`risk_policy` (always `UNRATIFIED`) and `position_size` (always
`NOT_COMPUTABLE_POLICY_UNRATIFIED`) in every snapshot this package builds.

See docs/portfolio_risk_input_contract.md for the full design.
"""
