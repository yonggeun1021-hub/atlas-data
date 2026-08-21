# P8-02 Unified Decision Contract

`decision/unified_decision_contract.py` assembles the existing daily read-model
outputs into one deterministic object in this fixed dependency order:

`Regime → Rotation / Discovery → Rule → Portfolio`

The object also carries the P8-03 Action Boundary packet as a final safety
boundary. Each source packet is embedded without interpretation and linked by
its exact `packet_sha256`. A missing source remains an explicit `UNAVAILABLE`
component with one or more machine-readable reasons; it is never silently
omitted.

Before assembly, every available component is passed through its production
validator: Regime, Rotation/Discovery, Rule, Portfolio Bucket, Portfolio
Currency, and Action Boundary. Exact identity, slot, time, and packet-hash
checks remain in front of those semantic checks. A component therefore cannot
change a derived row or summary and regain acceptance merely by recomputing its
own hash. Component and unified packet schemas are unchanged.

The contract implements assembly and lineage only. It does not authorize Regime
interpretation, candidate promotion, Rule PASS/FAIL, portfolio sizing, action or
order generation, Production, or trading. Even when every component is present,
the only permitted final state is `NO_ACTION_AUTHORIZED` and action, entry,
position size, and order fields remain `null`.

## Input envelope

The CLI accepts one JSON envelope containing the exact six component keys,
unavailability reasons for the same keys, `decision_date`, `slot`, and
`generated_at`. Available components use an empty reason list. Unavailable
components use `null` plus a sorted, unique reason list.

```bash
python decision/unified_decision_contract.py /tmp/input.json \
  --out /tmp/unified-decision.json
```

Tracked repository outputs are forbidden. Live daily wiring remains a separate
Exit Gate item; this module is an offline, policy-neutral contract capability.
