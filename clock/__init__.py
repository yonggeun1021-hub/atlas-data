"""P8-12 Opportunity Trigger + Dynamic Review Clock.

Operational (not retrospective/audit) layer built on top of PR #210's
`replay/` PIT-discipline modules (trigger detection, anti-lookahead gate,
asset-identity / PIT-eligibility boundaries). Where `replay/` answered "what
would have happened if we replayed the past", this package answers "what
needs a human's attention right now, and when should it be looked at again"
-- it never grades outcomes and never sets any Buy/Action/Order/trading
authority. See `docs/dynamic_clock_contract.md` for the full contract.
"""
