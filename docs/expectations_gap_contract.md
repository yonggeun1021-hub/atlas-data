# P8-09 Expectations Gap Contract

`decision/expectations_gap.py` builds an **Expectations Gap** packet: an
assessment of whether what a company is signalling (via free/official-data
proxies) is running ahead of, behind, or in line with what the market
currently expects — for a single `subject` as of a single `decision_date`.

## No paid consensus feed required

A paid consensus-estimate feed is explicitly **not** a required dependency.
The caller may supply any subset — including none — of nine evidence
categories:

- `guidance_changes`
- `backlog_or_new_orders`
- `capex_or_expansion`
- `pricing_or_lead_time`
- `revenue_margin_trend`
- `official_ir_targets`
- `public_estimates` — optional secondary input; the closest thing to an
  actual consensus observation, supplied only when the caller happens to
  have it
- `earnings_reaction` — price reaction around a **past** earnings/event date
- `relative_strength_volume`

**The single hardest rule in this module:** the absence of `public_estimates`
must never block, fail, or raise an exception from packet construction. It
only ever moves `expectations_gap.market_expectation_basis.basis_type` away
from `CONSENSUS`:

- `CONSENSUS` — only when `public_estimates` was actually supplied.
- `PROXY` — when at least one of the eight free/official-data proxy
  categories was supplied, but not `public_estimates`.
- `UNKNOWN` — when nothing usable was supplied at all. This is still a
  successfully built packet, not an error.

Feeding zero input categories at all is a **valid** input: the packet still
builds successfully, with `status = UNKNOWN`,
`market_expectation_basis.basis_type = UNKNOWN`, and `missing_inputs` listing
all nine categories.

## Caller-classified inputs, not raw evidence parsing

This module does not read filings, transcripts, or press releases itself.
Each supplied category (except `earnings_reaction`) is a small,
caller-classified object:

```json
{"direction": "POSITIVE", "evidence_note": "raised FY26 revenue guide by 4pts"}
```

`direction` is a closed enum: `POSITIVE | NEGATIVE | NEUTRAL | UNKNOWN`.
`earnings_reaction` additionally carries `event_date`, which **must not be in
the future relative to `decision_date`** — the module rejects (raises,
doesn't silently coerce) any future-dated earnings reaction as an
anti-lookahead violation.

## Status / magnitude / confidence derivation

`status`, `magnitude`, and `confidence` are closed enums. The module never
invents a status or magnitude from nothing — thin or absent evidence prefers
`UNKNOWN`/`SMALL` over guessing `LARGE`.

- Each supplied category with a scored direction (`POSITIVE`=+1,
  `NEGATIVE`=-1, `NEUTRAL`=0) contributes to a net score.
- `status` is `POSITIVE`/`NEGATIVE` if the net score is nonzero in that
  direction, `NEUTRAL` if scored inputs exist but net to zero, and `UNKNOWN`
  if there are zero scored inputs (including the zero-input case).
- `magnitude` requires both breadth (`scored_count`) and agreement
  (`|net_score| / scored_count`) above the thresholds in
  `config/expectations_gap_contract.json → magnitude_thresholds` before it
  will claim `LARGE` or `MEDIUM`; otherwise it is `SMALL`. When `status` is
  `UNKNOWN`, `magnitude` is always `UNKNOWN`.
- `confidence` similarly requires breadth and agreement, gated by
  `confidence_thresholds`. **Hard rule:** `status = UNKNOWN` always implies
  `confidence = LOW` — a `HIGH`/`MEDIUM` confidence paired with an `UNKNOWN`
  status is rejected by `validate_packet`.

## Authority

```json
{
  "expectations_gap_assembly_only": true,
  "rule_authority_substitution_authorized": false,
  "stage_promotion_authorized": false,
  "candidate_ready_buy_promotion_authorized": false,
  "rule_pass_fail_authorized": false,
  "action_authorized": false,
  "order_authorized": false,
  "production_authorized": false,
  "trading_authorized": false
}
```

This module never grants Rule PASS/FAIL, Stage, Candidate/Ready/Buy
promotion, Action/Order, Production, or trading authority. It only assembles
whatever evidence the caller already has.

## CLI

```bash
python decision/expectations_gap.py /tmp/p8-09-input.json --out /tmp/p8-09-out.json
```

Output is allowed only outside the tracked repository, same as every other
builder in this family.
