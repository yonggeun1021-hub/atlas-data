# P8-07 Investment Decision Review Contract

This is the first executable `Evidence → Thesis → Buy Review → Trade Proposal`
slice. It extends the existing P8 family without changing P8-02 Unified
Decision or P8-03 Action Boundary authority.

The v1 subject is TSM. A thesis must contain supporting and counter evidence,
an earnings-conversion statement, invalidation conditions, and the exact
Evidence-set SHA consumed by P5. The module validates the production P5 packet
and consumes only configured Rule IDs. It does not evaluate a Rule or apply a
threshold.

Current deterministic P5 explicitly reports `PASS_FAIL_NOT_AUTHORIZED` and
closes downstream action authority. Therefore current routine inputs produce:

```text
Buy Review: BLOCKED
Trade Proposal: null
```

This is intentional. `UNKNOWN` or `UNDEFINED` cannot be offset by a score or by
other PASS rows. The separate P5-02 `ratified_rule_decision/1` validator is the
only PASS/FAIL ingress. A complete externally ratified slice with every Rule
PASS creates a zero-capital, review-only Trade Proposal draft. Any FAIL produces
REJECTED and no proposal. The draft always requires human approval, has null
size/risk budget/order intent, and cannot submit to a broker.

P8-07 never changes Discovery/Candidate/Ready stage, account mode, position,
approval, order, or broker state. Shadow eligibility also remains outside this
contract until its Rule Authority is ratified.

The CLI accepts one envelope containing `thesis`, the exact production
`rule_packet`, and `generated_at`. Output is allowed only outside the tracked
repository:

```bash
python decision/investment_decision_review.py /tmp/p8-07-input.json \
  --out /tmp/p8-07-review.json
```
