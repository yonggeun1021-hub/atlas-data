# P5-05 Rule evaluator mutation matrix

This offline matrix exercises the P5-03 Rule–Evidence binding and P5-04
deterministic boundary evaluator as one fail-closed chain.

The cases cover missing, blocked, lineage-incomplete, and internally
inconsistent evidence; duplicate keys, subject mismatch, and hidden selection;
packet/hash/condition drift; and attempts to expand linkage or Rule-registry
evaluator authority. An observation-value mutation is also replayed to prove
that changing a number cannot create `PASS` or `FAIL` while evaluation specs and
authority are absent.

Every accepted negative case remains `UNKNOWN` or `UNDEFINED`. Structural or
authority corruption is rejected. This test asset does not define a source,
threshold, evaluation spec, Rule result, Production action, or trading action.
P5-04 remains partial until P5-02 and explicit evaluator authorization are
closed.
