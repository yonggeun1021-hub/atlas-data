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
converted to `FAIL`. Output retains the exact Rule condition hash, upstream
link status/reasons, Rule SSOT states, evidence-reference hash, and packet
lineage. The CLI is offline and writes only outside the repository.

`validate_packet()` rechecks the complete emitted Rule row set against the
canonical registry, recomputes every boundary classification and the summary,
and verifies lineage and packet hashes. Recomputing `packet_sha256` therefore
cannot legitimize a changed result or summary. This adds output integrity, not
an evaluation spec or PASS/FAIL authority.
