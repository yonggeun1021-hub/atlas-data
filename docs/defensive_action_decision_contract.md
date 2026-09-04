# P6-06 Defensive Action Decision Readiness Contract

`portfolio/defensive_action_decision.py` is the fail-closed integration boundary
for the P6-06 decision root.  It does not choose a defensive action.  It
records whether the exact P1, P2, and P6 inputs needed for a later ratified
policy evaluation are present and semantically valid.

## Current boundary

- Scope is `ZERO_CAPITAL_DECISION_REVIEW`.
- `P2_FLOW_ENGINE` (P2-COM-02's Cross-Market Capital Flow Engine,
  `portfolio/capital_flow_posture_reference.py`) is a supported source: its
  own `validate_reference` re-derivation is rerun at the consumption boundary
  exactly like every other P6-06 source.  Connecting it does not create any
  decision, budget, or authority -- it only lets P6-06's `BLOCKED` reason
  reflect that this source is now real and validated, not absent.
- `P1_REGIME_DECISION` and `P2_FLOW_LEDGER` remain explicitly unavailable-only
  in this contract version:
  - P1-COM-05 (`regime/decision_authority.py`) has so far only merged
    common-aggregation PIT replay.  It is not a ratified, runtime-wired final
    Regime decision, so this slot must not be promoted or relabeled onto it.
  - P2-COM-03's transition ledger (`portfolio/cross_market_flow_transition_ledger.py`)
    is an append-only history, not a single point-in-time decision packet: it
    carries no top-level `generated_at`/`as_of_date` of its own, so binding it
    independently would require inventing an undefined timestamp-selection
    rule.  Its evidence is already read into `P2_FLOW_ENGINE`'s
    `flow_candidates.transition`/`persistence` fields, so it is not an
    unrepresented gap today.
- Existing P6-01 through P6-05 packets may be supplied, but each packet is
  revalidated by its production validator before its SHA, market, status, or
  date is used.
- Missing or unratified inputs produce `DEFENSIVE_ACTION_READINESS_BLOCKED`.
  They never produce `NO_ACTION`.

## Decision vocabulary

The future decision vocabulary is fixed to:

1. `CASH_PRIORITY`
2. `REDUCE_REVIEW`
3. `HEDGE_REVIEW`
4. `INVERSE_REVIEW`
5. `NO_ACTION`

Every row is `NOT_EVALUATED`, `eligible=null`, and `review_proposal=null` in
version 1.  `NO_ACTION` is a policy result, not a synonym for missing inputs.

## Invariants

- Long FAIL never implies Short PASS.
- RISK_OFF or STRESS never implies an automatic inverse order.
- Hedge eligibility never implies an action proposal.
- An action proposal never implies an order.
- Missing or unevaluated evidence never implies `NO_ACTION`.

## Point-in-time and validation

Available source packets are embedded in full.  Their producer validators are
rerun at the consumption boundary.  Market-specific Cash and Inverse packets
must occupy their exact market slots.  A timestamped source cannot be later
than `generated_at` or after `as_of_date`; dated registries and budgets cannot
be after `as_of_date`.  A self-rehashed semantic modification is rejected.

## Authority

Only readiness inventory authority is true.  Policy evaluation, strategy
eligibility, defensive action, `NO_ACTION` inference, instrument selection,
risk-budget allocation, target exposure, size, action proposal, order,
Production, and trading authority are false.

Merging this capability does not complete the P6-06 WBS Exit Gate.  Actual
decision evaluation still requires a ratified, runtime-wired P1-COM-05 Regime
decision, an independently bindable P2-COM-03 ledger identity (or a ratified
decision that P2_FLOW_ENGINE's embedded ledger evidence suffices), plus an
independently ratified defensive-action policy and action-risk checks.
