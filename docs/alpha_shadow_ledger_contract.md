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

## `opportunity_state` → `action` mapping (exhaustive, P5-gated)

**CIO Gate Hardening (contract_version `alpha_shadow_ledger/2`).** The table
below is the *base* mapping (`config/alpha_shadow_ledger_contract.json`'s
`opportunity_state_to_action`) — every one of the 10 `opportunity_state`
values maps to exactly one base `action`; there is no fallback/default
branch. But a base `SHADOW_ENTRY_REVIEW` is **not** the final word:
`action_for_opportunity_state()` downgrades it to `WAIT` whenever the Alpha
Review packet's own `p5_rule_status != "PASS"` (i.e. is `NOT_EVALUATED`,
`UNKNOWN`, `UNDEFINED`, or `FAIL`) — never silently dropped, never an error.
Every other base action (`REJECT`/`WAIT`) is untouched by `p5_rule_status`.

| `opportunity_state` | base `action` | actual `action` |
|---|---|---|
| `BLOCKED` | `REJECT` | `REJECT` (p5-independent) |
| `REJECTED` | `REJECT` | `REJECT` (p5-independent) |
| `ANTICIPATORY_REVIEW` | `SHADOW_ENTRY_REVIEW` | `SHADOW_ENTRY_REVIEW` iff `p5_rule_status==PASS`, else `WAIT` |
| `EARLY_DISCOVERY` | `SHADOW_ENTRY_REVIEW` | `SHADOW_ENTRY_REVIEW` iff `p5_rule_status==PASS`, else `WAIT` |
| `CONFIRMATION_REVIEW` | `SHADOW_ENTRY_REVIEW` | `SHADOW_ENTRY_REVIEW` iff `p5_rule_status==PASS`, else `WAIT` |
| `WAIT_FOR_PULLBACK` | `WAIT` | `WAIT` (p5-independent) |
| `WAIT_FOR_EVIDENCE` | `WAIT` | `WAIT` (p5-independent) |
| `EXPECTATION_EXHAUSTED` | `WAIT` | `WAIT` (p5-independent) |
| `WAIT_FOR_PRICE` | `WAIT` | `WAIT` (p5-independent) |
| `WAIT_FOR_THESIS_REPAIR` | `WAIT` | `WAIT` (p5-independent) |

Since no ratified P5 packet exists for any real Pilot subject today
(`p5_rule_status==NOT_EVALUATED` for all four), this gate is what currently
prevents **every** real Pilot from ever reaching `SHADOW_ENTRY_REVIEW`,
regardless of any future evidence improvement, until a real ratified P5
packet exists for that subject.

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
