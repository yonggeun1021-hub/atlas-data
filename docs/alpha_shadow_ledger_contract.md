# P10-07 Alpha Shadow Evaluation Ledger

This is an append-only, hash-chained ledger of `decision/alpha_review.py`
(P8-11) packets, mirroring `shadow/investment_review_shadow_ledger.py`
(P10-06)'s exact chain pattern: each record links to the previous record's
`entry_hash` via `previous_entry_hash`, and `validate_record()` independently
recomputes `entry_hash` from the unsigned payload and rejects any mismatch or
broken link.

Every `opportunity_state` is recorded — `BLOCKED` and `REJECTED` included —
as a hypothetical, always-zero-capital shadow proposal for later
retrospective learning. Recording a record here never grants Shadow
eligibility, never changes Atlas Stage, never allocates capital, and never
creates a real action or order.

## `shadow_proposal`

```
capital: 0                        # hard-coded integer 0, no override parameter exists
action: SHADOW_ENTRY_REVIEW | WAIT | REJECT
hypothetical_entry_condition       # "; ".join(alpha_review.entry_conditions)
hypothetical_invalidation          # "; ".join(alpha_review.invalidation_conditions)
hypothetical_add_condition         # "; ".join(alpha_review.add_conditions)
hypothetical_exit_condition        # "; ".join(alpha_review.reduce_conditions)
expiry                              # = alpha_review.next_review_date (already >= decision_date)
human_approval_required: true      # hard-coded, no override parameter exists
```

`capital` and `human_approval_required` are hard-coded constants with no
corresponding parameter anywhere in `build_record()`'s signature — see
`test_alpha_shadow_ledger.py`'s regression, which inspects the live function
signature in addition to asserting the values.

## `opportunity_state` → `action` mapping (exhaustive)

| `opportunity_state` | `action` |
|---|---|
| `BLOCKED` | `REJECT` |
| `REJECTED` | `REJECT` |
| `ANTICIPATORY_REVIEW` | `SHADOW_ENTRY_REVIEW` |
| `EARLY_DISCOVERY` | `SHADOW_ENTRY_REVIEW` |
| `CONFIRMATION_REVIEW` | `SHADOW_ENTRY_REVIEW` |
| `WAIT_FOR_PULLBACK` | `WAIT` |
| `WAIT_FOR_EVIDENCE` | `WAIT` |
| `EXPECTATION_EXHAUSTED` | `WAIT` |

This mapping is a plain dict lookup (`config/alpha_shadow_ledger_contract.
json`'s `opportunity_state_to_action`) — every one of the 8 `opportunity_state`
values maps to exactly one `action`; there is no fallback/default branch.

## Administrative fields

`signal_date` (= the Alpha Review packet's `decision_date`), `subject`,
`alpha_review_packet_sha256` (linking back to the exact reviewed packet),
`entry_hash`, `previous_entry_hash` are retained for later retrospective
evaluation and audit. The ledger itself exposes no update or delete API —
only `build_record()` (append) and `validate_record()` (read-time
verification).

## Explicitly out of scope: retrospective evaluation

This stage does **not** implement `catalyst_date`, `hypothetical_return`,
`benchmark_relative_return`, `maximum_adverse_excursion`,
`maximum_favorable_excursion`, `invalidation_hit`, or `thesis_confirmation`.
None of those fields exist anywhere in this module's output schema. That is
a deliberate, separate follow-up capability for a later stage of P10-07, not
a half-implementation left here.

Whenever that follow-up work is built, it **must never** compute any of
those retrospective fields using data dated after the owning record's own
`signal_date` (anti-lookahead) — the same discipline
`decision/forward_thesis.py` already enforces between `observed_facts[].
as_of` and `decision_date`.

## Authority

```json
{
  "append_only_alpha_observation": true,
  "stage_change_authorized": false,
  "shadow_eligibility_authorized": false,
  "capital_authorized": false,
  "action_authorized": false,
  "order_authorized": false,
  "production_authorized": false,
  "trading_authorized": false
}
```
