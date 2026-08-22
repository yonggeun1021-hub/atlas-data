# P8-12 Opportunity Trigger + Dynamic Review Clock

Operational (not retrospective) layer: `Evidence -> Trigger Event -> Dynamic
Re-review -> Human-review candidate`. Built on top of PR #210's (P10-02/
P10-03 Opportunity Capture PIT Replay) `replay/` package: every actual
trigger DETECTION call is the same, unmodified `replay.trigger_engine`
function that audit used, itself gated by `replay.lookahead_gate`.

**CIO review round 1 on PR #211** required the flood/wiring fixes described
throughout this document (marked ★ round 1 below). The engine foundation
(trigger detection reuse, episode state machine) was accepted as-is; the
consolidation/tiering layer, calendar policy config, workflow wiring, and
briefing wiring are new in this revision.

## WBS scope note

The Notion Master WBS Tracker's canonical rows separate **P8-10 "Price
Reflection"** (a different WBS row, now being built in its own separate
parallel session/PR -- real historical price/benchmark time series,
`PRICE_DATA_MISSING`/`PRICE_STALE`/`REFLECTION_UNCERTAIN_WITH_VALID_PRICE`,
and `NOT_REFLECTED`/`PARTIALLY_REFLECTED`/`FULLY_REFLECTED`) from **P8-12
"Opportunity Trigger + Dynamic Review Clock"** (this module's actual
scope). This module does not implement P8-10 and does not import
`decision/price_reflection.py`. See "P8-10 integration readiness" below for
how the two will connect once that other PR merges.

## Markets and trigger types (item 1)

Markets: BTC, KOREA (current watchlist), CRYPTO (ratified PIT-eligible
taxonomy). Trigger types: the same seven `replay.opportunity_trigger.
TRIGGER_TYPES` PR #210 defined.

`clock/operational_scan.py::MARKET_TRIGGER_COMPUTABILITY` declares, per
market, whether each type is `COMPUTABLE` or `NOT_COMPUTABLE` from this
repo's real committed evidence today -- never silently omitted:

| Trigger type | BTC | KOREA | CRYPTO |
|---|---|---|---|
| PRICE_CONFIRMATION | COMPUTABLE | COMPUTABLE | COMPUTABLE |
| INVALIDATION_TRIGGER | COMPUTABLE | COMPUTABLE | COMPUTABLE |
| FLOW_REVERSAL | NOT_COMPUTABLE | COMPUTABLE (KRX `net_value.외국인합계`) | NOT_COMPUTABLE |
| RELATIVE_STRENGTH_REVERSAL | NOT_COMPUTABLE (no peer series for BTC) | COMPUTABLE | COMPUTABLE |
| FUNDAMENTAL_REVISION / CATALYST_APPROACH / EXPECTATION_DISLOCATION | NOT_COMPUTABLE | NOT_COMPUTABLE | NOT_COMPUTABLE |

## Dynamic Clock (item 2)

`clock/dynamic_clock.py::build_episode_history` turns a chronological
stream of trigger detections for one `(subject, market, trigger_type)` into
**episodes**: duplicate-event suppression, cooldown (renewal), expiry,
re-activation. Unchanged from round 1 except:

★ round 1: cadence numbers now live in `config/dynamic_clock_policy.json`
(`load_policy()`), not a hardcoded dict, with `approval_status:
"PROVISIONAL_CIO_MVP"` (never presented as a ratified investment
threshold). `add_review_days(date, days, market)` is market-calendar-aware:
KOREA uses `add_business_days` (Mon-Fri only) and stamps every derived date
with `calendar_confidence = "UNVERIFIED_NO_HOLIDAY_CALENDAR"` (this repo
has no committed KRX public-holiday evidence, so a real holiday inside the
window is NOT skipped -- the uncertainty is surfaced, never hidden). BTC/
CRYPTO trade 24/7, so plain calendar-day arithmetic is exact
(`calendar_confidence = "VERIFIED_24_7"`).

## Output contract -- raw ledger + consolidated, tiered candidates (item 3)

★ round 1 fix ("candidate flood"): the original version emitted one
`human_review_required=True` record per ACTIVE `(subject, trigger_type)`
episode -- 99 for CRYPTO alone in one run, all requiring human review. Now
split into two granularities:

- **`clock/review_candidate.py::build_raw_trigger_record`** -- one record
  per `(subject, trigger_type)` episode, kept in FULL under
  `raw_trigger_ledger` for audit (nothing ever dropped), with NO
  `human_review_required` field.
- **`build_subject_review_candidate`** -- ONE record per subject,
  consolidating every currently-ACTIVE episode across its trigger types
  into `trigger_types` + `confirmation_count`, then assigned a priority
  **tier** derived only from quantities that already exist elsewhere in
  this system:
  - `confirmation_count >= 2` reuses the exact threshold
    `replay.action_conversion_gate._condition_2` already uses for "PASS".
  - PIT/asset-identity eligibility reuses
    `replay.asset_identity.asset_identity_status` verbatim.

  Tiers: `IMMEDIATE_REVIEW` (`human_review_required=True`, the ONLY tier
  that carries it), `WATCH_REVIEW`, `OBSERVATION_ONLY` (PIT-ineligible).
  Lower tiers are never deleted -- they remain in `review_queue` and its
  convenience slices `watch_review`/`observation_only`.

  Real effect (2026-08-22 evidence): CRYPTO went from 99 raw triggers / 99
  would-be candidates to 65 consolidated subjects, only 2 of which reach
  `IMMEDIATE_REVIEW`.

## Linkage cap + AUDIT_CONFIRMED_MISS exception (item 4)

★ round 1: if BOTH `thesis_linkage` and `price_reflection_status` are
`NOT_LINKED_THIS_SLICE` (true for every candidate today -- see "Deliberate
non-coupling" below), a candidate that would otherwise reach
`IMMEDIATE_REVIEW` is capped to `WATCH_REVIEW`
(`capped_for_missing_linkage=True`).

**Exception**: `clock/audit_confirmed_miss.py` reads PR #210's own real,
committed `evidence/audit/pit_replay/opportunity_miss_episodes.json` (never
a hardcoded guess) and looks up whether a candidate's detection dates fall
inside a real `ACTION_CONVERSION_FAILURE` Miss episode window. If so, the
candidate is elevated to `IMMEDIATE_REVIEW` regardless of
`confirmation_count` (`audit_confirmed_miss_exception_applied=True`,
`audit_confirmed_miss` carries the matched PR #210 episode) -- STILL not a
buy signal (see Authority below). This is why BTC 2026-08-20 (a single
PRICE_CONFIRMATION trigger; `RELATIVE_STRENGTH_REVERSAL` is structurally
NOT_COMPUTABLE for BTC, so `confirmation_count` can never reach 2) still
reaches `IMMEDIATE_REVIEW` -- without the exception it could never have,
despite being PR #210's own headline finding.

PIT ineligibility (`asset_identity_status != PASS`) is a safety floor even
this exception cannot lift.

## P8-10 integration readiness (item 8, deferred until that PR merges)

Not started -- the P8-10 PR has not merged yet (as of this revision, `gh pr
list --state open` shows no such PR). When it does:
1. Rebase/merge this branch onto the new `main`.
2. Replace `_thesis_linkage_placeholder()` / `_price_reflection_placeholder()`
   in `clock/review_candidate.py` with real reads of P8-08's
   `decision/forward_thesis.py` output and P8-10's real
   `decision/price_reflection.py` output.
3. Where no linkable thesis exists, use `THESIS_NOT_AVAILABLE` (a real,
   evidence-checked absence) instead of the current blanket
   `NOT_LINKED_THIS_SLICE` (a "we didn't wire this yet" placeholder) --
   these are different facts and must not be conflated.
4. Once real linkage exists, most `WATCH_REVIEW`-capped candidates will
   either gain a real basis for `IMMEDIATE_REVIEW` or stay capped for a
   real (not structural) reason.

## Authority (item 5, unchanged)

A Trigger firing is a re-review REQUEST only. Every record's `authority`
block is hard-`False`/`None`/`0` -- `AUTHORITY_ALL_FALSE`,
`validate_review_candidate`, and swept end-to-end in
`test_dynamic_clock_end_to_end.py::AuthorityInvariantAcrossReportTests`.
Nothing here is promoted regardless of any P5 Rule status (P5 is never
evaluated by this module at all).

## Real operational wiring (item 6)

★ round 1: `.github/workflows/p8-12-dynamic-clock.yml` triggers via
`workflow_run` on the three REAL upstream collectors this module actually
consumes (`BTC Price Daily Capture`, `P1-CR-06 Crypto Breadth Daily
Capture`, `Atlas Daily Collect`) plus `workflow_dispatch`. Makes ZERO new
provider/API calls (`clock/run_dynamic_clock.py` only reads already-committed
evidence). Idempotency: `git add evidence/operational/dynamic_clock && git
diff --cached --quiet` gates the commit, mirroring
`p1-kr05-korea-breadth-live.yml`'s existing pattern -- no-op if the
recomputed output is byte-identical to what's already committed. A
`git diff --exit-code -- . ':!evidence/operational/dynamic_clock'` guard
ensures this workflow only ever touches its own output directory.

Note: `workflow_run` only activates once this workflow file itself is
merged to the default branch (a GitHub Actions platform constraint) -- it
cannot be proven "firing live" from a feature branch;
`test/test_dynamic_clock_workflow_wiring.py` validates the file's shape/
content instead.

## Briefing connection (item 7)

★ round 1: re-checked for conflicts against the CURRENT `main` before
wiring (PR #211 is the only open PR; the Forward Alpha commits that
previously touched `briefing/daily_orchestrator.py` are already merged) --
the earlier deferral reason is stale, so this revision wires it in.

`briefing/daily_orchestrator.py::build_dynamic_clock_status()` calls
`clock/run_dynamic_clock.py`'s `run()` + `build_briefing_section()`
directly and is wired into `build_packet()` as the new `DYNAMIC_CLOCK`
component (`config/daily_orchestrator_contract.json` ->
`daily_orchestrator/2`, additive-only: `FORWARD_ALPHA_REVIEW`'s row and
every earlier component are untouched). Renders new triggers,
`IMMEDIATE_REVIEW` candidates, `WATCH_REVIEW` candidates, expired triggers,
and NOT_COMPUTABLE types per market in the markdown briefing. Purely
informational -- does not feed `UNIFIED_DECISION` or any action/order path.
`test/test_daily_orchestrator.py` 46/46 green with this component present.

## Verification (item 9)

- BTC 2026-08-20 remains present in `review_queue` after triage (and
  reaches `IMMEDIATE_REVIEW` via the AUDIT_CONFIRMED_MISS exception) --
  `test_dynamic_clock_end_to_end.py::BtcRegressionCaseTests`.
- Flood-prevention: `CandidateFloodRegressionTests` asserts BOTH the raw
  trigger count (must stay high/complete) AND the `IMMEDIATE_REVIEW` count
  (must stay small, `<= 10` and strictly less than the raw count) together,
  so triage can't silently regress back to flooding.
- P5-not-PASS invariant: this module has no P5 concept of its own -- every
  record's `authority` block (including `action_authority`/
  `order_authority`/`trade_proposal`) is unconditionally `False`/`None`
  regardless of anything, checked in
  `AuthorityInvariantAcrossReportTests::test_p5_not_pass_never_promoted_anywhere_in_this_module`.
- All prior lookahead/duplicate/expiry/reactivation/determinism tests
  remain green, plus new coverage:
  `test/test_dynamic_clock_calendar.py` (config + business-day logic),
  `test/test_audit_confirmed_miss.py` (registry, real evidence + fail-closed),
  `test/test_dynamic_clock_workflow_wiring.py` (workflow shape/content).

Committed, reproducible artifacts: `evidence/operational/dynamic_clock/
dynamic_clock_report.json` + `.../briefing_section.json`, regenerated
byte-identically by `python3 clock/run_dynamic_clock.py` and refreshed
operationally by the workflow above.
