# P8-03 READY != ENTRY / Signal != Order boundary

This capability keeps candidate qualification, signal observation, entry
eligibility, and order creation as four separate layers. It does not implement
the pending P8-02 Unified Decision Contract.

Every subject supplies an explicit, source-hash-bound READY status and signal
status. The boundary preserves those observations but always emits:

- `entry_trigger=null`, `entry_trigger_status=NOT_EVALUATED`; and
- `order_intent=null`, `order_status=NOT_EVALUATED`.

This remains true for the strongest admissible input combination:
`ready_status=READY` and `signal_status=PRESENT`. A caller attempting to derive
an entry trigger from READY or an order from a signal is rejected by the direct
invariant guards.

READY/NOT_READY and PRESENT/ABSENT observations require explicit source
references and SHA-256 lineage. NOT_EVALUATED cannot carry hidden lineage.
Duplicate subjects, digest drift, and authority expansion fail closed.

Entry policy, order eligibility, position sizing, portfolio risk checks,
Production, and trading remain unauthorized. The CLI is offline and writes only
outside the repository.
