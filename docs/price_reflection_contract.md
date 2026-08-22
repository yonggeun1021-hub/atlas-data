# P8-10 Price Reflection Contract

`decision/price_reflection.py` builds a **Price Reflection** packet: an
assessment of whether the market's current price already reflects what is
known about a subject, based strictly on price, volume, relative-strength,
event-reaction, and valuation-history evidence the caller supplies.

## Structurally price/volume only — never fundamentals

The public builder, `build_packet(...)`, is a keyword-only function whose
entire parameter list is: `subject`, `decision_date`, `generated_at`,
`price_as_of`, `freshness_ceiling_days`, `relative_strength`,
`recent_return_windows`, `event_reaction`, `valuation_context`,
`data_source_scope`, `contract`. There is **no** "thesis quality" or
"fundamental strength" parameter anywhere in that list, and there never can
be by accident: `test_price_reflection.py` inspects the live function
signature and fails the build if any parameter name contains `thesis`,
`fundamental`, `quality`, `conviction`, `narrative`, or `story`
(`FORBIDDEN_PARAMETER_SUBSTRINGS` in the module). Good fundamentals alone can
never produce `UNDER_REFLECTED` — the module has no channel through which
fundamentals could even arrive.

## Staleness overrides everything (Rule 1)

`price_as_of` plus a freshness ceiling is the load-bearing input. **Chosen
default: `price_as_of` older than 5 calendar days relative to
`decision_date` is STALE** (`default_freshness_ceiling_days: 5` in
`config/price_reflection_contract.json`). The spec did not name an exact
number; 5 calendar days was chosen because it comfortably covers a weekend
plus one holiday without treating a routine Friday-to-Monday gap as staleness,
while still catching genuinely abandoned/cached price data. Callers may
override per-call via `freshness_ceiling_days`.

If `price_as_of` is missing, in the future relative to `decision_date`
(rejected outright as an anti-lookahead violation — this one raises, it does
not silently downgrade), or older than the ceiling, `status` is forced to
`UNKNOWN` and `confidence` is forced to `UNKNOWN` — **unconditionally**,
regardless of how strong every other input looks. This check runs first and
short-circuits every other signal.

## Korea data (Rule 7)

When `data_source_scope == "KRX_OFFICIAL"`, the module requires the 1-month
return, `relative_strength.vs_market`, and
`relative_strength.position_vs_recent_high_pct` to all be present before it
will attempt any classification. If any of the three is missing, `status` is
forced `UNKNOWN` rather than attempting an English/US-style computation on
incomplete Korea inputs.

## `data_source_scope` propagation

This module never claims market-wide price authority. `data_source_scope` is
a closed enum (`IEX_ONLY_PARTIAL_US_MARKET | KRX_OFFICIAL | UNKNOWN`) that the
**caller** declares — this module does not infer it. When the caller's price
input traces back to Alpaca/IEX (see `config/free_market_data_contract.json`,
scoped `"IEX_ONLY_PARTIAL_US_MARKET"`), the caller must pass that scope
through verbatim; the module propagates it into the output rather than
silently dropping it or upgrading it to an implied market-wide claim.

## Status vocabulary

`UNDER_REFLECTED | PARTIALLY_REFLECTED | FULLY_REFLECTED | OVEREXTENDED |
UNKNOWN`. There is no `REJECTED` value anywhere in this vocabulary — Rule /
Portfolio rejection is a different system's job. A recent sharp rally alone
(large 1-month return near a recent high, or paired with an expensive
valuation-history position) produces `OVEREXTENDED`, not a rejection and not
an automatic negative status.

## `data_state`: real, distinct reasons behind a blanket `UNKNOWN` (P8-10)

`status == "UNKNOWN"` used to be a single blanket bucket regardless of WHY.
`reasons[0]` now always carries a `"DATA_STATE:<value>"` marker from a
closed, real-evidence-only vocabulary (`contract["allowed_data_state"]`),
parseable with `data_state_of(price_reflection_dict)`:

- **`PRICE_DATA_MISSING`** — no price evidence exists for this subject/period
  at all (`price_as_of` was never supplied).
- **`PRICE_STALE`** — a `price_as_of` exists but is older than
  `freshness_ceiling_days` relative to `decision_date`.
- **`REFLECTION_UNCERTAIN_WITH_VALID_PRICE`** — `price_as_of` is fresh and
  valid, but there isn't enough real relative-strength/momentum signal
  (Korea's 1m + vs_market + position_vs_recent_high requirement unmet, or
  fewer than 2 of the 5 scoreable signals present) to render a reflection
  judgment. This is the honest outcome for a subject with only a single
  point-in-time price snapshot (e.g. TSM, sourced from Alpaca IEX) — a fresh
  price is known, but no historical series exists yet to judge momentum
  against.
- **`VALID`** — set whenever `status` is one of the confident values
  (`UNDER_REFLECTED | PARTIALLY_REFLECTED | FULLY_REFLECTED | OVEREXTENDED`).
  `NOT_REFLECTED`/`PARTIALLY_REFLECTED`/`FULLY_REFLECTED`-shaped confident
  statuses are only ever produced when real evidence genuinely supports
  them — see `decision/price_evidence.py`, the real historical-price +
  Korea KOSPI/KOSDAQ composite-benchmark evidence-assembly layer that feeds
  this module for real subjects.

`data_state` is encoded inside the existing `reasons` field rather than as a
new top-level key, deliberately: `decision/alpha_review.py` hard-validates
the embedded `price_reflection` sub-object with its own exact field-set
check, and is out of scope for this change (see that module and
`shadow/alpha_shadow_ledger.py`'s own docs for the still-`status`-keyed
`WAIT_FOR_PRICE` gate this preserves byte-for-byte). `status` itself always
stays literally `"UNKNOWN"` for all three non-`VALID` `data_state` values,
so every existing downstream consumer's gate keeps working unchanged.

**`OVEREXTENDED` means entry-timing risk is elevated. It does not mean the
underlying business is bad.** A company can be an excellent business and
still be `OVEREXTENDED` on price after a sharp run — this status is about
*when* to buy, not *whether* the company is good.

## Never a Rule verdict

No field in this module's output is named or shaped like a P5 Rule
PASS/FAIL result. `status` and `confidence` use a vocabulary disjoint from
`PASS`/`FAIL`/`REJECTED`/`BLOCKED`, and `validate_packet` asserts the
contract's `allowed_status` list never gains a `REJECTED`-shaped value.

## Authority

```json
{
  "price_reflection_assembly_only": true,
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

## Real evidence sources (`decision/price_evidence.py`)

This builder never fetches evidence itself (see top of this doc); real
subjects are fed by `decision/price_evidence.py`, which assembles genuine
committed-repo evidence into `build_packet()` kwargs, reusing existing
collectors rather than inventing new external calls:

- **KRX daily closes** — `replay/price_series.py` + `replay/evidence_index.py`
  (built for PR #210's PIT replay audit, reused unchanged), merging every
  committed `data/<date>/krx.json` snapshot's embedded multi-week `daily`
  window. Covers `298040`/`267260`; `034020` has zero KRX evidence anywhere
  in this repo (confirmed, not assumed) and honestly returns
  `PRICE_DATA_MISSING`.
- **Korea KOSPI/KOSDAQ composite benchmark** — chain-linked from the real,
  committed `data/observations/korea_leadership_context/<date>/packet.json`
  `KOSPI_BENCHMARK`/`KOSDAQ_BENCHMARK` day-over-day `cumulative_gross_return`
  facts (P1-KR-07 real KRX Open API index data). This repo has never
  committed a raw KOSPI/KOSDAQ index price series (`korea_leadership_live_
  fetch.py` deliberately never persists raw index closes, only the outcome),
  so this chain-linked proxy is the only real, non-fabricated market-index
  series this repo's own evidence can support. Which composite applies to a
  given KRX code (`KOREA_STOCK_MARKET_MEMBERSHIP`) is a small, explicitly
  declared identity fact, not sourced from any committed evidence — an
  undeclared code fails closed to no `vs_market` figure rather than a
  guessed benchmark.
- **US single-name price** — `evidence/free_market_data/raw/<date>/
  manifest.json` (Alpaca IEX). Each day is a single most-recent-bar
  snapshot; with only one day committed as of this module's build,
  return-window/relative-strength fields are honestly left `None` rather
  than computed from one point — this widens automatically as the existing
  daily cron commits more days.

Every figure's evidence dates are checked with
`replay.lookahead_gate.assert_no_signal_lookahead` (reused unchanged from
PR #210) before being returned — see `test/test_price_evidence_lookahead.py`.

## CLI

```bash
python decision/price_reflection.py /tmp/p8-10-input.json --out /tmp/p8-10-out.json
```

The input JSON is read as a single envelope and unpacked directly as
`build_packet(**envelope)` keyword arguments. Output is allowed only outside
the tracked repository.
