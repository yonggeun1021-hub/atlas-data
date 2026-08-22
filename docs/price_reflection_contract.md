# P8-10 Price Reflection Contract

`decision/price_reflection.py` builds a **Price Reflection** packet: a
structurally separated read on (1) price/momentum and (2) whether the
market's price already reflects a specific, real expectation or event,
based strictly on price, volume, relative-strength, event-reaction,
reflection-reference, and valuation-history evidence the caller supplies.

## `price_reflection/2` (CIO review round 2 on PR #212): the core fix

Round 1 conflated momentum and reflection into a single `status` field and
let a large price move alone (e.g. 1-month return ≥ 8%) produce
`FULLY_REFLECTED`, with no event or expectation reference at all. The CIO
correctly flagged this: **a price having risen is not, by itself, evidence
that expectations/events/a thesis have been "reflected" — reflection
requires a reference point for WHAT is supposed to be reflected in the
price.** This module now keeps two claims structurally separate:

## `price_reflection/3` (CIO review round 3): four further defects closed

Round 2's reference-point requirement was necessary but not sufficient —
round 3 closed four remaining holes CI/the test suite alone didn't catch:

1. **A bare `direction`/`expectations_gap_status` string was still not a
   real reference.** A caller could type `direction="POSITIVE"` with zero
   evidence behind it, or a bare `expectations_gap_status="POSITIVE"`
   string with no actual P8-09 packet. `reflection_reference.
   expectations_gap_status` is retired; callers now pass `reflection_
   reference.expectations_gap_packet` (the FULL, already-built P8-09
   packet), independently re-validated via `decision/expectations_gap.py`'s
   own `validate_packet` (hash/tamper/vocab) with `subject`/`decision_date`
   cross-checked against this packet's own. `event_reaction.direction` now
   requires `event_reaction.source_ref` + `source_sha256` (a real evidence
   citation) before it counts toward a reflection verdict — still accepted
   as plain input without them (a caller may legitimately record an
   observed direction it can't yet cite), just never sufficient alone.
2. **Reflection was graded off a generic, "now"-anchored return that could
   be almost entirely PRE-event movement.** `reflection_status` now
   requires a real, caller-computed `event_reaction.post_event_return_pct`
   / `reflection_reference.post_reference_return_pct` — a return measured
   specifically from the reference date forward — never the generic
   `recent_return_windows`/`relative_strength` figures `price_state` uses.
3. **`price_state=UNKNOWN` + a non-`UNKNOWN` `reflection_status` could
   coexist** — CIO's exact reproduction: 1-month return +10%, one positive
   event/reference point, no other price signal, produced `price_state=
   UNKNOWN` / `reflection_status=FULLY_REFLECTED` / `data_state=VALID`, a
   contradiction `alpha_review.py` assumed was structurally impossible. Now
   a hard invariant in both `_classify` (forces `reflection_status` back to
   `UNKNOWN` whenever `price_state` came out `UNKNOWN`) and
   `validate_packet` (`OUTPUT_PRICE_STATE_UNKNOWN_REFLECTION_STATUS_
   CONTRADICTION`, unconditional on any packet however constructed).
4. **`threshold_basis="PROVISIONAL"` didn't actually gate anything
   operational.** `decision/alpha_review.py` (`alpha_review/4`) now treats a
   non-`RATIFIED` `threshold_basis` as an independent trigger for its
   blanket `WAIT_FOR_PRICE` gate, alongside `reflection_status=="UNKNOWN"` —
   no positive/differentiated `opportunity_state` is reachable while
   thresholds remain provisional, regardless of what `price_state`/
   `reflection_status` value they produced. `price_reflection.py` itself
   still computes and surfaces real values under provisional thresholds
   (diagnostic output); `alpha_review.py` is the fail-closed operational
   boundary.

- **`price_state`** — `OVEREXTENDED | STRONG_MOMENTUM | MODERATE | WEAK |
  UNKNOWN`. A pure, price/volume-only read on momentum and positioning.
  Momentum alone can never produce a reflection verdict.
- **`reflection_status`** — `UNDER_REFLECTED | PARTIALLY_REFLECTED |
  FULLY_REFLECTED | UNKNOWN`. Only ever leaves `UNKNOWN` when a real
  **reference point** is present (see below) AND a comparable direction AND
  real momentum exist. Abundant, fresh, valid price data with NO reference
  point still forces `reflection_status=UNKNOWN` /
  `data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE` — momentum magnitude,
  however large, is never a substitute for a reference.

## Structurally price/volume/reference-point only — never fundamentals

The public builder, `build_packet(...)`, is a keyword-only function whose
entire parameter list is: `subject`, `decision_date`, `generated_at`,
`price_as_of`, `freshness_ceiling_days`, `relative_strength`,
`recent_return_windows`, `event_reaction`, `reflection_reference`,
`valuation_context`, `data_source_scope`, `contract`. There is **no**
"thesis quality" or "fundamental strength" parameter anywhere in that list,
and there never can be by accident: `test_price_reflection.py` inspects the
live function signature and fails the build if any parameter name contains
`thesis`, `fundamental`, `quality`, `conviction`, `narrative`, or `story`
(`FORBIDDEN_PARAMETER_SUBSTRINGS` in the module). Good fundamentals alone can
never produce `UNDER_REFLECTED` — the module has no channel through which
fundamentals could even arrive.

## Staleness overrides everything (Rule 1)

`price_as_of` plus a freshness ceiling is the load-bearing input. **Chosen
default: `price_as_of` older than 5 calendar days relative to
`decision_date` is STALE** (`default_freshness_ceiling_days: 5` in
`config/price_reflection_contract.json`). Callers may override per-call via
`freshness_ceiling_days`.

If `price_as_of` is missing, in the future relative to `decision_date`
(rejected outright as an anti-lookahead violation — this one raises, it does
not silently downgrade), or older than the ceiling, **both `price_state` AND
`reflection_status`** are forced to `UNKNOWN` and `confidence` is forced to
`UNKNOWN` — unconditionally, regardless of how strong every other input
looks. This check runs first and short-circuits everything else.

## The reference point requirement (Rule 2, tightened in round 3)

`reflection_status` requires a real reference point for what the market was
supposed to have priced in. At least one of the following must be present:

- `event_reaction.event_date` (a real, dated event),
- `reflection_reference.reference_event_id` (an explicit reference-event
  token),
- `reflection_reference.expectation_as_of` (the date an expectation was
  captured), or
- `reflection_reference.expectations_gap_packet` — the FULL, already-built
  P8-09 Expectations Gap packet (not a bare status string, round 3), which
  this module independently re-validates via `decision/expectations_gap.py`'s
  own `validate_packet` (hash/tamper/closed-vocab) and cross-checks
  `subject`/`decision_date` against this packet's own before ever trusting
  its `status`.

A reference point alone is necessary but not sufficient for a confident
verdict — `_resolve_reflection_basis` additionally requires, per path:

- **event_reaction path**: `direction` is `POSITIVE`/`NEGATIVE` AND
  `source_ref` + `source_sha256` (a real evidence citation, round 3) AND
  `post_event_return_pct` (a real, event-anchored return the caller
  computed specifically from the reference date forward — never the
  generic `recent_return_windows`/`relative_strength` figures, round 3)
  are all present.
- **expectations_gap path**: the independently-re-validated packet's
  `status` is `POSITIVE`/`NEGATIVE` AND `post_reference_return_pct` is
  present.

A bare `direction`/`reference_event_id`/`expectation_as_of` with no
lineage, no anchored return, or no comparable direction still leaves
`reflection_status=UNKNOWN` — never a crash, always a graceful downgrade
(`REFERENCE_POINT_PRESENT_BUT_NOT_LINEAGE_VERIFIED_OR_POST_REFERENCE_
RETURN_NOT_COMPUTABLE`).

Without any reference point at all, `reflection_status` is `UNKNOWN` and
`data_state` is `REFLECTION_UNCERTAIN_WITH_VALID_PRICE` — even with
abundant, valid price data. This is the round-2 fix in concrete terms: BTC
rallying hard is real `price_state=OVEREXTENDED` evidence, but with no
expectation/catalyst reference point in this repo's evidence, that is *not*
"future expectations are fully reflected" — those are different claims.

Korea (`298040`/`267260`/`005930`/`000660`) had a round-1 momentum-only
`PARTIALLY_REFLECTED` verdict; that verdict is retracted in round 2. Real
price/momentum/relative-market-performance remain valid grounds for
`price_state`, but `reflection_status` can only be (re-)determined once a
real expectation or event linkage exists — none of the 4 real Pilot
subjects' `decision/pilot_evidence_intake.py` inputs currently supply one,
so all four honestly report `reflection_status=UNKNOWN`.

## `data_source_scope` propagation

This module never claims market-wide price authority. `data_source_scope` is
a closed enum (`IEX_ONLY_PARTIAL_US_MARKET | KRX_OFFICIAL | KRAKEN_OHLC |
UNKNOWN`) that the **caller** declares — this module does not infer it. When
the caller's price input traces back to Alpaca/IEX (see
`config/free_market_data_contract.json`, scoped
`"IEX_ONLY_PARTIAL_US_MARKET"`) or Kraken OHLC, the caller must pass that
scope through verbatim; the module propagates it into the output rather than
silently dropping it or upgrading it to an implied market-wide claim.

## Vocabularies

- `allowed_price_state`: `OVEREXTENDED | STRONG_MOMENTUM | MODERATE | WEAK |
  UNKNOWN`. A rally alone (large 1-month return near a recent high, or
  paired with an expensive valuation-history position) produces
  `OVEREXTENDED` — entry-timing risk, not a rejection.
- `allowed_reflection_status`: `UNDER_REFLECTED | PARTIALLY_REFLECTED |
  FULLY_REFLECTED | UNKNOWN`.

There is no `REJECTED` value in either vocabulary — Rule/Portfolio rejection
is a different system's job.

**`price_state=OVEREXTENDED` means entry-timing risk is elevated. It does not mean the underlying business is bad, and it does not by itself mean `reflection_status=FULLY_REFLECTED`.** A company can be an excellent
business and still be `OVEREXTENDED` on price after a sharp run — this
status is about *when* to buy, not *whether* the company is good, and not a
claim about whether the market has priced in any specific expectation.

## `data_state`: real, distinct reasons behind a blanket `UNKNOWN`

`data_state` is a real, structured top-level field (round 1 encoded this as
a `reasons[0]=="DATA_STATE:..."` string marker as a stopgap to avoid
touching `decision/alpha_review.py`'s strict field-set check; round 2
updates that module directly instead — see its own docstring — so this is a
proper field now). Tracks `reflection_status` specifically: `VALID` iff
`reflection_status != "UNKNOWN"`.

- **`PRICE_DATA_MISSING`** — no price evidence exists for this subject/period
  at all (`price_as_of` was never supplied).
- **`PRICE_STALE`** — a `price_as_of` exists but is older than
  `freshness_ceiling_days` relative to `decision_date`.
- **`REFLECTION_UNCERTAIN_WITH_VALID_PRICE`** — `price_as_of` is fresh and
  valid, but either there is no reference point (see Rule 2 above) or not
  enough real momentum signal to render a reflection judgment even with one.
- **`VALID`** — `reflection_status` is one of the confident values.
  `UNDER_REFLECTED`/`PARTIALLY_REFLECTED`/`FULLY_REFLECTED` are only ever
  produced when real evidence (a real reference point AND real momentum)
  genuinely supports them — see `decision/price_evidence.py`, the real
  historical-price evidence-assembly layer that feeds this module for real
  subjects.

## Threshold approval status (Rule 7)

`classification_thresholds` (the 15%/8%/3%/2%-style cutoffs) have never been
CIO-ratified. `classification_thresholds_approval_status` in the contract
says so explicitly (`"PROVISIONAL"`, one of `allowed_threshold_basis`), and
every output packet echoes it verbatim as `price_reflection.threshold_basis`
— tamper-evident via the packet hash, so no downstream consumer can silently
treat a provisional-threshold verdict as ratified. A `PROVISIONAL` basis is
not a defect (it is the honestly-true current state) but is a visible signal
that no `price_state`/`reflection_status` value this module emits is a
CIO-ratified final call — consistent with `authority.
rule_authority_substitution_authorized: false` below. Promoting
`classification_thresholds_approval_status` to `RATIFIED` requires an actual
CIO ratification decision on the specific cutoff numbers, not a code change.

**Round 3**: this used to be diagnostic-only in practice — `threshold_basis`
was surfaced but nothing downstream actually refused to act on a
provisional-threshold verdict. `decision/alpha_review.py` (`alpha_review/4`)
now gates its OWN operational `opportunity_state` on it directly: a
non-`RATIFIED` `threshold_basis` is an independent trigger for its blanket
`WAIT_FOR_PRICE` state, so no positive/differentiated review state can ever
be unlocked by a provisional-threshold `price_state`/`reflection_status`
value. This module's own output is unaffected — it still computes and
reports the real value either way.

## Never a Rule verdict

No field in this module's output is named or shaped like a P5 Rule
PASS/FAIL result. `price_state`/`reflection_status`/`confidence` use a
vocabulary disjoint from `PASS`/`FAIL`/`REJECTED`/`BLOCKED`, and
`validate_packet` asserts neither vocabulary ever gains a `REJECTED`-shaped
value.

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
  window. Covers `298040`/`267260`/`005930`/`000660`; `034020` has zero KRX
  evidence anywhere in this repo (confirmed, not assumed) and honestly
  returns `PRICE_DATA_MISSING`.
- **Korea KOSPI/KOSDAQ composite benchmark** — chain-linked from the real,
  committed `data/observations/korea_leadership_context/<date>/packet.json`
  `KOSPI_BENCHMARK`/`KOSDAQ_BENCHMARK` day-over-day `cumulative_gross_return`
  facts (P1-KR-07 real KRX Open API index data). This repo has never
  committed a raw KOSPI/KOSDAQ index price series (`korea_leadership_live_
  fetch.py` deliberately never persists raw index closes, only the outcome),
  so this chain-linked proxy is the only real, non-fabricated market-index
  series this repo's own evidence can support.
- **Korea market (KOSPI/KOSDAQ) membership** — `config/
  korea_market_membership.json`, an explicit, auditable canonical mapping
  with `source`/`observation_date`/`source_sha256`/`approval_status` per
  entry. Only `approval_status == "RATIFIED"` entries are ever used for
  `relative_strength.vs_market`; as of this build every entry is
  `UNRATIFIED` (no committed, hash-verified KRX Open API stock-master lookup
  exists confirming market venue per code yet), so `vs_market` is currently
  `None` for every Korea subject regardless of code. This replaced a round-1
  hardcoded `KOREA_STOCK_MARKET_MEMBERSHIP` dict the CIO correctly rejected
  as "a code comment is not real evidence."
- **BTC** — `replay/price_series.py`'s `build_btc_series` (Kraken OHLC, PR
  #210, unchanged) — ~720 real calendar days, genuinely supporting 1m/3m/6m
  windows. No separate crypto market-index series exists in this repo
  distinct from BTC's own price, so `relative_strength.vs_market` is left
  `None` rather than fabricated or made tautological (BTC vs BTC).
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
