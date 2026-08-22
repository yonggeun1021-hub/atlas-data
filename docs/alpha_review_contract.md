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

**CIO Gate Hardening (contract_version `alpha_review/2`).** A CIO review
found the pre-hardening gate too loose: real Pilot runs produced
`SHADOW_ENTRY_REVIEW`-eligible states even though `price_reflection.status
==UNKNOWN` for every Pilot, and even for a subject with
`expectations_gap.status==NEGATIVE`.

**`alpha_review/3` (CIO review round 2 on PR #212).**
`decision/price_reflection.py` split its old single conflated `status`
field into `price_state` (pure momentum: `OVEREXTENDED | STRONG_MOMENTUM |
MODERATE | WEAK | UNKNOWN`) and `reflection_status` (`UNDER_REFLECTED |
PARTIALLY_REFLECTED | FULLY_REFLECTED | UNKNOWN`, only ever non-`UNKNOWN`
when a real event/expectation reference point exists) -- the CIO's
diagnosis: momentum alone was standing in for a reflection judgment with no
reference at all. Every gate/table row below that used to read
`price_reflection.status` now reads `reflection_status` for the "can we
judge reflection at all" question, and `price_state` for the pure
momentum/positioning question (e.g. `OVEREXTENDED`) -- `price_state` is
consulted even when `reflection_status` is already known-non-`UNKNOWN`,
since an overextended price is still real, useful timing information
independent of whether a reflection reference exists.

**`alpha_review/4` (CIO review round 3 on PR #212, required item 4).**
`price_reflection.threshold_basis` is `"PROVISIONAL"` — the momentum/
reflection classification cutoffs behind `price_state`/`reflection_status`
have never been CIO-ratified. Row 3 below now ALSO fires whenever
`threshold_basis != "RATIFIED"`, in addition to `reflection_status==
UNKNOWN` — so no positive/differentiated `opportunity_state` (rows 5-10) is
EVER reachable while the underlying thresholds remain provisional,
regardless of what `price_state`/`reflection_status` value they produced.
`price_reflection.py` itself is still free to compute and surface real
`price_state`/`reflection_status` values under provisional thresholds
(diagnostic output) — this module is where the fail-closed OPERATIONAL
boundary lives.

`classify_opportunity_state()` is a small, pure, ordered if/elif chain --
each rule below, once matched, returns immediately (no fallthrough).
`BLOCKED`, then the Expectations-Gap-negative gate, then the
Reflection-UNKNOWN-or-unratified gate, then the narrative-only-core-evidence
gate, are always checked first, before any positive-state classification, so
a broken/negative/unpriced/thin/unratified case can never be shadowed by a
positive one. In order:

| # | State | Fires when |
|---|-------|------------|
| 1 | `BLOCKED` | `len(observed_facts)==0 AND len(evidence_lineage)==0` (nothing beyond narrative-free inference), **OR** `earnings_conversion.status==UNKNOWN AND expectations_gap.status==UNKNOWN AND reflection_status==UNKNOWN` (triple-UNKNOWN) |
| 2a | `REJECTED` | `earnings_conversion.status==CONVERSION_DISAPPOINTED` (independent of gap status) **OR** (`expectations_gap.status==NEGATIVE AND earnings_conversion.status==UNKNOWN`) |
| 2b | `WAIT_FOR_THESIS_REPAIR` | `expectations_gap.status==NEGATIVE AND earnings_conversion.status!=UNKNOWN` (and 2a didn't already fire) -- a real earnings-conversion hypothesis still stands, just currently disagreed-with by the market proxy |
| 3 | `WAIT_FOR_PRICE` | `reflection_status==UNKNOWN` **OR** `threshold_basis!="RATIFIED"` (blanket rule: no positive state may ever be reached while reflection is unjudgeable OR the classification thresholds are unratified, however strong the thesis/gap or however extreme the raw momentum otherwise looks) |
| 4 | `WAIT_FOR_EVIDENCE` (narrative-only-core-evidence gate) | `len(observed_facts)>0 AND` none of them have `source_class==EXHIBIT_EXTRACTED` (only `NARRATIVE_SOURCED`/`PRICE_FEED`) |
| 5 | `EXPECTATION_EXHAUSTED` | `reflection_status==FULLY_REFLECTED AND expectations_gap.status==POSITIVE` |
| 6 | `WAIT_FOR_PULLBACK` | (`reflection_status==FULLY_REFLECTED OR price_state==OVEREXTENDED`) `AND expectations_gap.status!=NEGATIVE` |
| 7 | `CONFIRMATION_REVIEW` | `earnings_conversion.status ∈ {REVENUE_CONVERSION_EXPECTED, MARGIN_CONVERSION_EXPECTED, CONVERSION_CONFIRMED} AND reflection_status!=FULLY_REFLECTED AND price_state!=OVEREXTENDED` (reflection is already known-non-UNKNOWN by row 3) |
| 8 | `WAIT_FOR_EVIDENCE` (old rule) | `earnings_conversion.status ∈ {PRE_REVENUE_SIGNAL, BACKLOG_BUILDING, UNKNOWN} AND expectations_gap.status==UNKNOWN AND reflection_status!=UNDER_REFLECTED` |
| 9 | `ANTICIPATORY_REVIEW` | ALL 7 gates below hold simultaneously |
| 10 | `EARLY_DISCOVERY` | default fallback — has real evidence (not `BLOCKED`), reflection known, gap not negative, at least one `EXHIBIT_EXTRACTED` fact, but fits none of the above |

Rows 2a/2b fold in the pre-hardening `OVEREXTENDED+NEGATIVE -> REJECTED`
clause (now a strict subset of the broader NEGATIVE-gap rule) and the
`CONVERSION_DISAPPOINTED -> REJECTED` clause, so `REJECTED` keeps exactly
one point of truth. Row 5 is checked strictly before row 6 so
`FULLY_REFLECTED + POSITIVE` always resolves to `EXPECTATION_EXHAUSTED`
rather than `WAIT_FOR_PULLBACK`; `price_state==OVEREXTENDED` (any
non-`NEGATIVE` gap) and `reflection_status==FULLY_REFLECTED` with a
non-`POSITIVE`, non-`NEGATIVE` gap both resolve to `WAIT_FOR_PULLBACK`. Row
8's `WAIT_FOR_EVIDENCE` and row 9's `ANTICIPATORY_REVIEW` are, in practice,
only reachable once rows 1-4 have already been cleared -- i.e. reflection is
known, the threshold basis is RATIFIED, and at least one `EXHIBIT_EXTRACTED`
fact exists -- which is why real current Pilot evidence (no ratified P5
packet, no real reference point supplied for any subject, no RATIFIED
threshold basis, and narrative-only evidence for the ones with a thesis)
never reaches rows 5-10 today.

### The 7 `ANTICIPATORY_REVIEW` gates

All 7 must hold. See `anticipatory_review_gates()`:

1. `forward_thesis` has ≥1 non-empty catalyst AND ≥1 `observed_facts` entry
   AND ≥1 `evidence_lineage` entry (i.e., not `BLOCKED`).
2. `revenue_recipient` / `atlas_linked_ticker` present (non-empty) — always
   true for a validly-built `forward_thesis` packet; kept as a defensive
   re-check.
3. `earnings_conversion.status != UNKNOWN` — an actual conversion hypothesis
   exists, even if early-stage (`PRE_REVENUE_SIGNAL`/`BACKLOG_BUILDING`).
4. `expectations_gap.status==POSITIVE` **OR** (`expectations_gap.status!=
   NEGATIVE AND expectations_gap.market_expectation_basis.basis_type==
   PROXY`).
5. `reflection_status ∉ {FULLY_REFLECTED, UNKNOWN} AND price_state !=
   OVEREXTENDED`.
6. `len(catalysts)>0 AND len(invalidation_conditions)>0` (re-asserted; both
   already guaranteed non-empty upstream).
7. No future-dated evidence anywhere in `evidence_lineage`/`observed_facts`
   relative to `decision_date` — re-asserted defensively; already guaranteed
   by `forward_thesis`'s own `validate_packet()`.

If any single gate fails, the packet does **not** classify as
`ANTICIPATORY_REVIEW` and falls through to the remaining rows in order.

## `validate_packet()` — tamper detection and its boundary

`validate_packet()` recomputes `packet_sha256` from the unsigned payload and
rejects any mismatch. It also re-checks closed-vocabulary membership for
every enum field and re-verifies `opportunity_state` against the subset of
`classify_opportunity_state()`'s invariants that are reconstructable from
fields this packet actually retains (`earnings_conversion_status`, the
embedded `expectations_gap`/`price_reflection` sub-objects,
`catalyst_timing.catalysts` count, `invalidation_conditions` count).

Gates 1/2/7 of `ANTICIPATORY_REVIEW`, and the evidence-count arm of
`BLOCKED`, depend on `forward_thesis` fields (`observed_facts`,
`evidence_lineage`, `revenue_recipient`, `atlas_linked_ticker`, evidence
dates) that are **not** persisted as stand-alone fields on this packet —
those gates were already enforced once, at `build_packet()` time, against
the real, freshly re-validated `forward_thesis` packet. This is the same
boundary `expectations_gap.py`/`price_reflection.py` already accept for
their own upstream-supplied-then-not-persisted raw category inputs.

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
