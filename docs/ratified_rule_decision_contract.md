# P5-02 Ratified Rule Decision Contract

This capability validates an externally ratified, complete TSM Rule result
slice. It does not calculate PASS/FAIL, select evidence, invent a threshold, or
change `config/rules.json`. Each result is bound to the canonical Rule ID,
subject, condition-text SHA, Evidence-set SHA, evaluator identity, time, and an
explicit authority reference.

The complete v1 population is `RULE-0003` through `RULE-0009`. Partial slices,
UNKNOWN/UNDEFINED, missing evidence references, subject drift, registry drift,
and self-rehashed semantic changes fail closed.

No ratified production packet is committed by this capability. Tests use
synthetic external authority. Real PASS/FAIL remains unavailable until a human
authority supplies such a packet.
