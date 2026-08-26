# P5-02 Ratified Rule Decision Contract

This capability validates an externally ratified, complete TSM Rule result
slice. It does not calculate PASS/FAIL, select evidence, invent a threshold, or
change `config/rules.json`. Version 2 retires the former bare-string
`authority_ref` boundary: every decision-determining field must be approved by
an exact external authority envelope whose bytes are clean at `HEAD` and whose
first exact appearance is independently recomputed from full git history.

The complete v1 population is `RULE-0003` through `RULE-0009`. Partial slices,
UNKNOWN/UNDEFINED, missing evidence references, subject drift, registry drift,
and self-rehashed semantic changes fail closed.

The approval binds the complete result population, Rule registry SHA, Evidence
set SHA, evaluator, evaluation time, and authority identity. The usable time is
the later of ratification and exact-content first-seen time; backdated approval,
dirty files, fixture paths, missing history, and a result mutation followed by
self-rehash all fail closed.

No ratified production packet is committed by this capability. Tests construct
an isolated git repository and never expose that fixture to the operational
root. Real PASS/FAIL remains unavailable until a human authority supplies a
genuine committed approval envelope.
