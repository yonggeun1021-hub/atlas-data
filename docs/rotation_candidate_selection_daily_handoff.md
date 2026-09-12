# Retained daily Rotation to Stage 3 handoff

`briefing/rotation_candidate_selection_daily_handoff.py` closes the existing
storage-to-consumer gap without adding a producer or a collection trigger. A
daily briefing bundle already retains the exact
`rotation_discovery_briefing_packet/4` under its `ROTATION_DISCOVERY` component.
The adapter accepts an explicit daily packet path, extracts that child packet,
and supplies it to `rotation_candidate_selection_input/2` together with the
exact ledger it binds and the caller-supplied Stage 1/2 packets.

No latest packet is discovered. The daily packet, Stage 1 packet, Stage 2
packet, and output path are all explicit CLI arguments.

## Admission boundary

The whole daily bundle is used only as a retained container. The adapter checks
its canonical `daily_orchestrator/6` identity, component order, closed
authority, and self-hash. It does not claim that every unrelated component in
an old daily bundle can still be rebuilt by today's producer revisions.

The selected Rotation child must be the single component in the contract's
exact position. Its component hash, contract, generation time, slot, authority,
and closed decision/action/order eligibility must bind the embedded packet.
The current `rotation_discovery.validate_briefing()` then independently
validates the complete child packet.

The bound ledger is never read from a child claim:

- if `frozen_sources` does not contain `US_ROTATION_LEDGER`, the adapter derives
  the canonical `rotation_state_ledger.empty_ledger()`;
- if that key is present, its exact original `rotation_packet`, `state_policy`,
  and `previous_ledger` are passed to the unchanged
  `rotation_state_ledger.apply_rotation()`;
- a key explicitly holding `null` is invalid, not equivalent to absence; and
- the derived ledger digest must equal the embedded briefing's
  `rotation.source_ledger_sha256`.

Stage 3 then repeats its own briefing/ledger derivation checks and Stage 2 to
Stage 1 lineage validation. A self-resigned daily container, child packet, or
source tuple cannot rebind the output.

## Current retained result

The retained morning revision for decision date 2026-09-12 contains a valid
Rotation child with digest
`b204685196c23f2a87d7d69ce0f5f6a60e3c08093b60153f7dba80dc14394f9e`.
It contains no explicit US Rotation source and binds the canonical empty-ledger
digest
`32ef8896b61f4c08ab91fa9c0926c15c394b942ac68918102cc9a85cc4c72ed3`.

The resulting real Stage 3 packet is therefore consumable and honest with
`input_count=0`, `inputs=[]`, and payload digest
`c2cd47d7ce8fd363225c8c487e6afbc183c35ec79de8e1769c99650de36a2ccc`.
Zero rows means no Rotation state observation was retained. It does not mean a
candidate, NATURAL observation, ranking outcome, or investability verdict.

## CLI

```text
python3 briefing/rotation_candidate_selection_daily_handoff.py \
  <retained-daily-packet.json> \
  --stage2-posture-reference <capital-flow-packet.json> \
  --stage1-paper-reference <paper-regime-packet.json> \
  --out <path-outside-the-repository>
```

The output guard is the Stage 3 producer's existing tracked-path and symlink
defence. Selection, ranking, readiness, stage promotion, runtime regime,
capital, action, order, production, and trading authority all remain closed.
