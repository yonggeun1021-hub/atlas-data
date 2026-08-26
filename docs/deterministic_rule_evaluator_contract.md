# P5-04 Deterministic Rule Evaluator boundary contract

Status: `UNKNOWN / UNDEFINED` boundary-classification capability only. `PASS /
FAIL`, evaluator wiring, Production, and trading remain unauthorized.

The current canonical Rule registry has 25 Rules but explicitly says
`consumable_by_evaluator=false`. P5-02 also remains blocked on per-Rule source,
freshness, fallback, period, unit, and threshold decisions. Therefore this
capability does not invent an evaluation DSL, threshold, source selection, or
default spec.

It validates one exact `rule_evidence_binding/2` packet against the current Rule
registry SHA and deterministically applies this precedence:

1. A canonical `definition_status=UNDEFINED` is `UNDEFINED`.
2. Otherwise an unavailable/blocked evidence link or a Rule SSOT execution
   blocker is `UNKNOWN`.
3. A READY Rule with an available link is still `UNDEFINED` while the evaluation
   spec and evaluator authority are absent.

`LINK_AVAILABLE` is never promoted to `PASS`, and a blocked Rule is never
converted to `FAIL`. Output contract `/2` retains the exact Rule condition
hash, upstream link status/reasons, Rule SSOT states, evidence-reference hash,
packet lineage, and the complete validated `frozen_binding_packet`. The CLI is
offline and writes only outside the repository.

`validate_packet()` first sends the frozen packet through the production
P5-03 validator. It then re-derives each emitted link state,
evidence-reference-set hash, boundary classification, summary, and lineage
from that frozen packet and the canonical Rule registry. Recomputing either
packet hash cannot legitimize a substituted envelope, binding packet, result,
summary, or lineage. This proves packet-internal derivation only; external
source authenticity, qualification, and freshness remain upstream authority
responsibilities. It does not add an evaluation spec or PASS/FAIL authority.
