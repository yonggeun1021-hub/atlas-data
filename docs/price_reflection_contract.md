# P8-10 Price Reflection Contract

`decision/price_reflection.py` builds a **Price Reflection** packet: a
structurally separated read on (1) price/momentum and (2) whether the
market's price already reflects a specific, real expectation or event,
based strictly on price, volume, relative-strength, and valuation-history
evidence the caller supplies.

## ★★★ SCOPE REDUCTION — CIO final integration ruling on PR #212 (2026-08-23)

**Read this section first.** Everything below it (rounds 2-9) is kept as an
audit trail of what was built and why it was ultimately removed — the
`event_reaction`/`reflection_reference`-citation-verification machinery
those sections describe **no longer exists in this repository**.

Round 9 fixed the two local defects the CIO had requested, but integrated
review then found a further, deeper PIT defect in the Event Evidence
Authority engine built across rounds 5-9: the ratification-authority lookup
parsed `ratified_at` but never compared it to `decision_at`, so a rule
ratified in the FUTURE relative to a historical decision could still be
applied retrospectively to that decision, and the "evidence" backing a
ratification record was only ever hash-checked against an arbitrary repo
file, never validated as a genuine, structured Rule Authority record. This
is the same class of provenance failure rounds 5-9 kept finding and fixing
at the evidence layer, recurring one layer up at the policy/ratification
layer.

The CIO explicitly declined a round-10 local patch — an implementation that
needed 9 successive integrity-defect rounds is over-scoped for one PR. PR
#212 was reduced to the proven P8-10 MVP boundary instead:

**Kept:** real historical price-series linkage and PIT-safe price endpoints
(`decision/price_evidence.py`, untouched); `price_state` structurally
separate from `reflection_status`; `PRICE_DATA_MISSING`/`PRICE_STALE`/
`REFLECTION_UNCERTAIN_WITH_VALID_PRICE` data states; `PROVISIONAL`
threshold-basis exposure; Korea market-membership fail-closed behavior;
honest BTC/Korea/TSM/Doosan outputs; `decision/alpha_review.py`'s
fail-closed entry-blocking behavior and `authority=false` posture.

**Removed from this module and this PR entirely:** `decision/event_
evidence.py` (the whole Event Evidence Authority engine — provenance
verification, direction-rule implementation tables, the ratification-
authority registry — deleted, not patched); this module's own
`event_reaction`/`reflection_reference` build_packet parameters and every
internal function that only existed to verify or classify them. There is
no code path left in this module — not merely an empty table, an actually
absent function — that could compute `reflection_status` as anything other
than the hardcoded literal `"UNKNOWN"`. `price_state` (the pure,
price/volume-only momentum read) is completely unaffected and remains
fully real.

**Deferred, not abandoned:** a future, separate, dependent PR must design a
Reflection Evidence Authority together with Atlas P5 Rule Authority —
append-only per-rule canonical records, `ratified_at`/`effective_from`,
exact-content provenance, explicit decision-time ordering checks, and a
structured authority-evidence schema — and get that design approved BEFORE
any implementation is written, not merely before merge. Tracked on the
existing P8-10 WBS row.

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

## The reference point requirement (Rule 2 — REMOVED, see scope reduction above)

Rounds 3-5 tightened this into a full evidence-verification apparatus
(`event_reaction.event_at`/`reflection_reference.expectations_gap_packet_
ref` reference points, `_resolve_reflection_basis`, `_compute_verified_
return`, `decision/event_evidence.py`'s Event Evidence Envelope
verification) — all of it has been **removed entirely** per the CIO's final
integration ruling on PR #212 (2026-08-23). There is no longer any
`event_reaction`/`reflection_reference` input parameter, and no reference
point of any kind can ever unlock a non-`UNKNOWN` `reflection_status` in
this module today.

`reflection_status` is the literal constant `"UNKNOWN"` in every packet
`build_packet()` can produce, unconditionally — `data_state` is
`REFLECTION_UNCERTAIN_WITH_VALID_PRICE` whenever price data is present and
fresh (never `VALID`, which remains a legal vocabulary member but is now
structurally unreachable through this module's own builder). This is the
round-2 fix taken to its current logical conclusion: BTC rallying hard is
real `price_state=OVEREXTENDED` evidence, but momentum alone was never a
reflection verdict, and this reduced scope no longer has ANY machinery that
could turn a momentum read into one — only a future, separately-designed
Reflection Evidence Authority (see scope-reduction section) can reintroduce
that capability.

Korea (`298040`/`267260`/`005930`/`000660`) and all 4 real Pilot subjects
(`TSM`/`298040.KS`/`267260.KS`/`034020.KS`) report real, honest
`price_state` from real momentum/relative-strength inputs, and honestly,
structurally `reflection_status=UNKNOWN` — not because no reference point
happens to exist today, but because there is no code path in this module
that could ever compute anything else.

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
- **`VALID`** — `reflection_status` is one of the confident values. Remains
  a legal vocabulary member (no contract bump) but is currently
  **structurally unreachable** through this module's own `build_packet()`
  — see the scope-reduction section at the top of this document: the
  reference-point/citation-verification machinery that used to produce a
  confident `reflection_status` has been removed entirely. Reserved for a
  future, separately-designed Reflection Evidence Authority workstream.

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

`decision/alpha_review.py` independently gates its own operational
`opportunity_state` on `reflection_status`/`threshold_basis` too (see that
module's own docstring for the current, reduced-scope decision table — only
4 of its 10 vocabulary members remain reachable through a real, validated
packet today). This module's own output is unaffected either way — it still
computes and reports the real `price_state`/`reflection_status` value
regardless of what `alpha_review.py` does with it downstream.

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
  window. At the frozen Pilot boundary, `034020` has no PIT-available KRX
  evidence and honestly returns `PRICE_DATA_MISSING`; later captures now in
  the repository are never backfilled into that historical decision.
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

## Event Evidence Envelope verification (REMOVED — see scope reduction above)

`decision/event_evidence.py` (the Event Evidence Authority engine this
section used to document across rounds 5-9: Event Evidence Envelope
verification, raw-source citation schemas, the exact-content git-provenance
gate, the direction-origin implementation/authority-registry split) has
been **deleted from this repository entirely** per the CIO's final
integration ruling on PR #212 (2026-08-23) — see the "SCOPE REDUCTION"
section near the top of this document for why, and what a future,
separately-designed and separately-approved Reflection Evidence Authority
workstream will need to cover. `decision/price_reflection.py` no longer has
an `event_reaction`/`reflection_reference` input parameter at all.

## CLI

```bash
python decision/price_reflection.py /tmp/p8-10-input.json --out /tmp/p8-10-out.json
```

The input JSON is read as a single envelope and unpacked directly as
`build_packet(**envelope)` keyword arguments. Output is allowed only outside
the tracked repository.
