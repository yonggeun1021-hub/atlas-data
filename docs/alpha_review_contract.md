# P8-11 Anticipatory Alpha Review Contract

This is a pure composition and classification engine. It consumes three
already-built, already-*validated* upstream packets — P8-08 Forward Thesis
(`decision/forward_thesis.py`), P8-09 Expectations Gap
(`decision/expectations_gap.py`), and P8-10 Price Reflection
(`decision/price_reflection.py`) — and assembles them into one
`opportunity_state`-classified Alpha Review packet.

This module is a **sibling** to P8-07 `decision/investment_decision_review.py`,
built independently and in parallel. It does not import from, modify, or
replace that module, and it does not decide Stage, Candidate, Ready, or Buy
promotion. It never generates a Rule (P5) PASS/FAIL result and never
generates a Portfolio Gate decision — both `p5_rule_status` and
`portfolio_status` are caller-supplied, closed-vocabulary pass-throughs only.

## Inputs

All three sub-packets are **required**. Each is independently re-validated
against its own module's `validate_packet()` — a missing or invalid
sub-packet fails the whole build closed. All three must share the same
`subject` and the same `decision_date`
(`SUBJECT_MISMATCH_ACROSS_INPUT_PACKETS`,
`DECISION_DATE_MISMATCH_ACROSS_INPUT_PACKETS`).

`p5_rule_status` (optional) and `portfolio_status` (optional) are
caller-supplied closed-vocabulary strings, never computed by this module:

* `p5_rule_status ∈ {PASS, FAIL, UNKNOWN, UNDEFINED, NOT_EVALUATED}`,
  defaulting to `NOT_EVALUATED` when the caller has no ratified/evaluated
  Rule packet for this subject.
* `portfolio_status ∈ {PASS, FAIL, UNKNOWN, NOT_EVALUATED}`, defaulting to
  `NOT_EVALUATED` — there is no Portfolio Gate engine yet (Master Map Phase
  6-7 is `⬜ 미처리`).

## Output fields

`thesis_status` and `earnings_conversion_status` are both a verbatim
pass-through of `forward_thesis.earnings_conversion.status` (the module
introduces no new vocabulary of its own). `expectations_gap` and
`price_reflection` are the full, unmodified inner sub-objects from their
respective upstream packets. `catalyst_timing` is a pass-through/summary of
`forward_thesis.catalysts` plus `forward_thesis.earnings_conversion.
expected_start_window` — no new data.

`why_now[]` and `what_market_may_be_missing[]` are arrays of strings that
each cite a specific field from one of the three input packets — never free
invention. `why_now` draws from `forward_thesis.catalysts`,
`forward_thesis.forward_inferences`, and `expectations_gap.gap_reasons`.
`what_market_may_be_missing` draws **only** from `expectations_gap.
gap_reasons` and `forward_thesis.forward_inferences`.

`invalidation_conditions[]` is **required non-empty**
(`INVALIDATION_CONDITIONS_EMPTY`): it passes through
`forward_thesis.invalidation_conditions` (itself already guaranteed
non-empty upstream) plus one additional price_reflection-derived condition.

`next_review_date` must be one of `forward_thesis.review_dates`, at
`decision_date` or later. If every `review_dates` entry on file is stale
(before `decision_date`), the module falls back to `decision_date +
default_next_review_cadence_days` (30 days) — always later, never in the
past (`NEXT_REVIEW_DATE_IN_PAST`).

`trade_proposal` is **hard-coded to `null`** in every packet this module can
ever produce. There is no ratified P5 PASS + Human Approval pathway wired to
this module in this MVP; a later stage may add one, but no code path in this
module can set it to anything else (`TRADE_PROPOSAL_MUST_BE_NULL`).

## `opportunity_state` — closed vocabulary and decision table

★ SCOPE (CIO closing-fix ruling on PR #212, 2026-08-23): Reflection Evidence
Authority is deferred to a future design done jointly with Atlas P5 Rule
Authority (see `decision/price_reflection.py`'s own docstring for the full
note). Because `price_reflection.py`'s `reflection_status` is now
structurally, unconditionally `"UNKNOWN"` in every packet it can build or
validate, only **4 of the 10** `opportunity_state` vocabulary members are
reachable through a real, validated packet today: `BLOCKED`, `REJECTED`,
`WAIT_FOR_THESIS_REPAIR`, `WAIT_FOR_PRICE`. `classify_opportunity_state()`
is a small, pure, ordered if/elif chain:

| # | State | Fires when |
|---|-------|------------|
| 1 | `BLOCKED` | `len(observed_facts)==0 AND len(evidence_lineage)==0` (nothing beyond narrative-free inference), **OR** `earnings_conversion.status==UNKNOWN AND expectations_gap.status==UNKNOWN AND reflection_status==UNKNOWN` (triple-UNKNOWN) |
| 2a | `REJECTED` | `earnings_conversion.status==CONVERSION_DISAPPOINTED` (independent of gap status) **OR** (`expectations_gap.status==NEGATIVE AND earnings_conversion.status==UNKNOWN`) |
| 2b | `WAIT_FOR_THESIS_REPAIR` | `expectations_gap.status==NEGATIVE AND earnings_conversion.status!=UNKNOWN` (and 2a didn't already fire) -- a real earnings-conversion hypothesis still stands, just currently disagreed-with by the market proxy |
| 3 | `WAIT_FOR_PRICE` | unconditional fallback once gates 1-2 have passed -- fires whenever `reflection_status != UNKNOWN` is impossible to establish from real evidence (which, in this reduced scope, is always) |

`EARLY_DISCOVERY`, `ANTICIPATORY_REVIEW`, `WAIT_FOR_PULLBACK`,
`WAIT_FOR_EVIDENCE`, `CONFIRMATION_REVIEW`, and `EXPECTATION_EXHAUSTED`
remain legal, defined vocabulary members (kept so a future Reflection
Evidence Authority does not need a fresh contract-version bump to
reintroduce them) but the code paths that used to reach them have been
removed, not merely made unreachable — `classify_opportunity_state()` no
longer branches on `reflection_status` beyond the single non-`UNKNOWN`
check above. `WAIT_FOR_RULE_RATIFICATION` has been retired from the
vocabulary entirely (not kept as reserved) — it specifically named a
ratification-authority mechanism with no genuine implementation anywhere in
the repo, mirroring the deleted `decision/event_evidence.py` engine's own
retired ratification registry.

## `validate_packet()` — tamper detection and its boundary

`validate_packet()` recomputes `packet_sha256` from the unsigned payload and
rejects any mismatch. It also re-checks closed-vocabulary membership for
every enum field and re-verifies `opportunity_state` against the subset of
`classify_opportunity_state()`'s invariants that are reconstructable from
fields this packet actually retains (`earnings_conversion_status`, the
embedded `expectations_gap`/`price_reflection` sub-objects,
`catalyst_timing.catalysts` count, `invalidation_conditions` count).

**Closing-fix defense-in-depth (2026-08-23):** `validate_packet()`
independently rejects any packet whose embedded
`price_reflection.reflection_status != "UNKNOWN"`
(`PRICE_REFLECTION_REFLECTION_STATUS_MUST_BE_UNKNOWN_IN_THIS_REDUCED_SCOPE`),
on top of — not instead of — `price_reflection.py`'s own identical lock on
its own `validate_packet()`. This closes the bypass surface where a forged
or externally-injected packet could reach `alpha_review.validate_packet()`
without ever passing back through `price_reflection.validate_packet()`
first. It is unconditional and does not depend on `opportunity_state`.

## Authority

```json
{
  "alpha_review_assembly_only": true,
  "opportunity_state_classification_only": true,
  "stage_promotion_authorized": false,
  "candidate_ready_buy_promotion_authorized": false,
  "rule_pass_fail_authorized": false,
  "rule_result_generation_authorized": false,
  "portfolio_decision_authorized": false,
  "trade_proposal_authorized": false,
  "action_authorized": false,
  "order_authorized": false,
  "production_authorized": false,
  "trading_authorized": false
}
```

Only the two `*_only` flags are ever `true`. No code path in this module can
set any other flag to `true`, and no code path can produce a non-null
`trade_proposal` — both are covered by regression tests in
`test/test_alpha_review.py`.

## CLI

The CLI accepts one input JSON envelope (`forward_thesis_packet`,
`expectations_gap_packet`, `price_reflection_packet`, `generated_at`, and
optionally `p5_rule_status`/`portfolio_status`) and `--out` for the output
packet path. This module fetches nothing itself — only assembles and
classifies what it is given. Output is allowed only outside the tracked
repository:

```bash
python decision/alpha_review.py /tmp/p8-11-input.json \
  --out /tmp/p8-11-alpha-review.json
```
