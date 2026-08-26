# P8-12 Candidate Validity Evidence Inventory

This read-only inventory separates retained Candidate Validity artifacts into
independently revalidatable natural samples, manual samples, legacy artifacts,
and a rejected contract artifact. It validates every `/2` and `/4` observation
against its exact content-addressed Dynamic Clock source report.

It deliberately does not define a minimum sample count, select a validity
window, classify candidate freshness, open Risk Capacity, or start P8-13.
Natural and manual samples are counted separately, and evaluation-invariant
source hashes prevent repeated evaluation of unchanged evidence from being
misrepresented as a larger evidence population.
Artifact counts and distinct evidence-sample counts are exposed as separate
fields; only the latter is deduplicated by the evaluation-invariant hash.

The rolling output is
`evidence/operational/dynamic_clock/candidate_validity_evidence_inventory.json`.
It contains no forward return, account value, position size, order, or trading
authority. Every authority flag remains false.
