# P6-04 Long FAIL != Short PASS invariant

This capability enforces one authority boundary: a failed long thesis is not an
independent positive short thesis. It does not select a short candidate, approve
a hedge instrument, allocate a bear-risk budget, create an order, or change
Production/trading state.

The repository's current `deterministic_rule_evaluator/1` cannot emit `PASS` or
`FAIL`; its authorized output remains `UNKNOWN` / `UNDEFINED`. The integration
path therefore validates that exact packet and emits `short_result=null` plus
`short_evaluation_status=NOT_EVALUATED` for every Rule. Any upstream `PASS` or
`FAIL` smuggled into the current no-authority packet is rejected.

The invariant primitive separately accepts the four long-result vocabulary
values so the rule is directly testable and future-proof. Even for a synthetic
`long_result=FAIL`, the only output is:

- `short_result=null`
- `short_evaluation_status=NOT_EVALUATED`
- `LONG_FAIL_DOES_NOT_IMPLY_SHORT_PASS`

Any caller that proposes a derived short result, including `PASS`, is rejected.
An actual short evaluation requires all three independent prerequisites named in
the contract: ratified hedge-instrument eligibility, a separate bear/hedge risk
budget, and an independent short-rule evaluation. The CLI is offline and writes
only outside the repository.
