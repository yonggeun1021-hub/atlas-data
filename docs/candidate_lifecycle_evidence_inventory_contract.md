# P8-12 Candidate Lifecycle Evidence Inventory

## Purpose

`clock/candidate_lifecycle_evidence_inventory.py` independently rebuilds the
retained, forward-only Candidate Lifecycle artifacts and summarizes the
empirical observation record needed for a later Candidate Validity policy
review.

It answers only factual questions:

- how many natural, manual, and local lifecycle artifacts exist;
- whether the natural artifacts form one unforked forward chain;
- how many distinct-evidence versus evaluation-only transitions exist;
- which endpoint events Atlas observed by market and trigger type; and
- the exact gap between Atlas observation instants.

## Critical interpretation boundary

An active candidate at two observation endpoints does **not** prove that it
was continuously active between them. `observation_gap_seconds` and
`observation_span_seconds` are scheduler-observation gaps, not candidate
lifetimes and not validity-window recommendations.

Manual and local artifacts remain visible for audit, but never advance or
count as the natural policy-evidence chain. A repeated evaluation whose
source evidence basis is unchanged is counted separately from a distinct
evidence transition.

## Independent rebuild

For every lifecycle artifact the inventory:

1. recursively validates the lifecycle chain;
2. revalidates the retained Candidate Validity observation;
3. revalidates that observation against its exact retained Dynamic Clock
   report;
4. checks that the natural artifacts form one baseline-to-tip chain with no
   fork, cycle, missing parent, or disconnected branch; and
5. rebuilds the complete inventory before accepting a persisted output.

## Policy and authority locks

This contract defines no minimum sample count, validity duration, freshness
classification, sizing rule, or entry proposal. It always exposes:

- `minimum_sample_threshold = null`;
- `validity_window_days = null`;
- `validity_window_selected = false`;
- `candidate_freshness_evaluated = false`;
- `risk_capacity_opened = false`;
- `p8_13_entry_proposal_opened = false`; and
- the shared all-false authority block.

The output is diagnostic evidence for later CIO policy design only.
