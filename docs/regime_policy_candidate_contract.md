# P1-COM-05 Regime Policy Candidate — Shadow Diagnostic v1

## Why this slice exists

`regime_output/v1` proves that the five Regime axes exist, are point-in-time
bounded, and are tied to exact evidence. It intentionally contains no axis
direction, normalized score, threshold, or classification authority. The
existing `regime_decision_authority/v1` therefore correctly returns either
`BLOCKED_COVERAGE` or `BLOCKED_POLICY_UNRATIFIED`.

This slice adds a **draft comparison surface**, not a runtime Regime engine. It
allows replay inputs to attach an evidence-bound orientation and change state to
each defined axis and evaluates one explainable consensus candidate. The output
is suitable only for `POLICY_REPLAY` and `CIO_COMPARISON`.

## Design options considered

1. **Weighted composite score** — rejected for the first candidate. It creates
   false precision before the three markets have comparable, replay-tested
   normalization and weights.
2. **Explainable axis consensus with a Risk/Vol stress override** — selected as
   the first draft. Every classification has a short rule trace and no hidden
   coefficient.
3. **Statistical or machine-learning classifier** — deferred until a larger
   point-in-time panel and explicit out-of-sample evaluation exist.

## Input boundary

The evaluator requires one validated, five-of-five `regime_output/v1` source and
one assessment for each axis:

- `orientation`: `SUPPORTIVE | NEUTRAL | ADVERSE | STRESS`
- `change`: `IMPROVING | STABLE | DETERIORATING`
- `normalization_version`
- the exact factor evidence SHA-256 already present in the source output
- explicit warning codes

`STRESS` is allowed only for `RISK_VOL`. A missing axis, an evidence hash
mismatch, an unknown normalization version, duplicate warning, float, future or
otherwise invalid source packet fails closed.

This module does **not** decide how a raw sensor becomes SUPPORTIVE or ADVERSE.
Each market-specific normalization transform remains a separate policy and
replay obligation.

## Draft classification candidate

- `STRESS`: `RISK_VOL=STRESS` and at least one other axis is `ADVERSE`.
- `RISK_OFF`: at least three axes are `ADVERSE` or `STRESS`.
- `RISK_ON`: at least three axes are `SUPPORTIVE` and no axis is `ADVERSE` or
  `STRESS`.
- otherwise `NEUTRAL`.

Direction is `IMPROVING` or `DETERIORATING` only when at least three axes agree
and none points the opposite way; otherwise it is `STABLE`.

Confidence is a **diagnostic band**, not a probability. Four-of-five consensus
is `HIGH`, three-of-five is `MEDIUM`, and anything weaker is `LOW`.

## Authority boundary

The policy ID is `EXPLAINABLE_CONSENSUS_V1_DRAFT` and the status is always
`DRAFT_NOT_RATIFIED`.

The candidate cannot feed `regime_output/v1`, `regime_decision_authority/v1`, a
strategy, Stage, Buy, Action, Order, Production, or trading. Hysteresis is not
implemented in this slice. The next gate is point-in-time replay across US,
Korea, and Crypto, comparison against alternative candidates, and explicit CIO
ratification or rejection.
