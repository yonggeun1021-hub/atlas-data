# KIS PAPER valuation freshness policy proposal

Status: `PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY`.

This packet proposes a five-minute (`300` second) maximum age for each KIS
PAPER valuation source record and a two-minute (`120` second) maximum absolute gap between
the full-account v3 record and the instrument-specific buy-capacity record.
Both comparisons are inclusive and use `availableAt` against the explicit
review clock. Future-dated evidence is never fresh.

The source-age value is a conservative analogy to the existing KIS PAPER
decision-age default. The pair-gap value is a cross-domain heuristic using
that path's human-confirmation TTL default. Neither value is presented as
valuation evidence. In particular, a confirmation-token lifetime does not
prove that two broker responses represent one coherent account state.

The reference code comes from private merge
`72300ef09b4b8ce501588492e970f9e24bd9c4db`. The reviewer does not trust a
statement or content hash alone: it reads
the exact private git blob, checks the byte hash, and parses the Python AST to
reproduce both defaults, both environment defaults, both allowed ranges, and
the strict `age > maximum` stale comparison. This only proves what the order
path does; it does not bridge those meanings into valuation freshness.

There is currently no live full-account-v3 to buy-capacity pair sample and no
atomic capture-session binding. Therefore even the exact proposal plus exact
private source bytes must remain `REVIEW_INCOMPLETE` with
`VALUATION_PAIR_GAP_EVIDENCE_UNVALIDATED_NO_LIVE_PAIR_SAMPLE`. Rehashing a
self-declared sample cannot clear that blocker.

The proposal deliberately does not modify the valuation-semantic proposal,
`portfolio_account_fact/2`, any canonical authority config, bridge, order
path, WBS, or generated operational artifact. It is not retrospective. A
separate ratification must define an effective time, and only evidence at or
after that time may use the policy. Synthetic fixtures do not become
operational evidence.

Even after a future ratification, this policy would authorize freshness
classification only. It would not authorize valuation semantics,
`accountFact/3`, Portfolio Risk Input, candidate eligibility, position size,
Stage, Buy, Action, Order, Production, or Trading.
