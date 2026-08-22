# P8-12 Opportunity Trigger + Dynamic Review Clock

Operational (not retrospective) layer: `Evidence -> Trigger Event -> Dynamic
Re-review -> Human-review candidate`. Built on top of PR #210's (P10-02/
P10-03 Opportunity Capture PIT Replay) `replay/` package: every actual
trigger DETECTION call is the same, unmodified `replay.trigger_engine`
function that audit used, itself gated by `replay.lookahead_gate`.

**CIO review round 1 on PR #211** required the flood/wiring fixes described
throughout this document (marked ★ round 1 below): the consolidation/
tiering layer, calendar policy config, workflow wiring, and briefing wiring.

**CIO review round 2 on PR #211** found a real PIT lookahead violation in
round 1's tiering fix (marked ★ round 2 below): `AUDIT_CONFIRMED_MISS`, a
retrospective PR #210 audit conclusion computed from REAL RETURNS AFTER the
decision date, was being used to ELEVATE an OPERATIONAL priority tier as of
that same decision date. That is exactly the outcome-based reasoning
("we later confirmed it went up, so it should have been flagged at the
time") this whole workstream exists to eliminate. Fixed by removing the
exception's power over tier entirely -- see "Linkage cap (item 4)" below.

## WBS scope note and merge order

The Notion Master WBS Tracker's canonical rows separate **P8-10 "Price
Reflection"** (a different WBS row, being built in its own separate PR --
real historical price/benchmark time series,
`PRICE_DATA_MISSING`/`PRICE_STALE`/`REFLECTION_UNCERTAIN_WITH_VALID_PRICE`)
from **P8-12 "Opportunity Trigger + Dynamic Review Clock"** (this module's
scope). Confirmed merge order: P8-10's PR gets its own methodology fixes
and merges first; PR #211 then rebases onto the new `main` and wires in
P8-10's real output (item 4 below, deferred), before P8-12 can be marked
complete in WBS.

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

## Dynamic Clock (item 2 baseline; item 5 "now" fix)

`clock/dynamic_clock.py::build_episode_history` turns a chronological
stream of trigger detections for one `(subject, market, trigger_type)` into
**episodes**: duplicate-event suppression, cooldown (renewal), expiry,
re-activation.

★ round 1: cadence numbers live in `config/dynamic_clock_policy.json`
(`load_policy()`), `approval_status: "PROVISIONAL_CIO_MVP"`. KOREA uses
`add_business_days` (Mon-Fri only) with `calendar_confidence =
"UNVERIFIED_NO_HOLIDAY_CALENDAR"` (no committed KRX holiday evidence
exists); BTC/CRYPTO use plain calendar-day arithmetic
(`calendar_confidence = "VERIFIED_24_7"`).

★ round 2, item 5 ("real now" handling): round 1 gated episode staleness
purely off `evidence_as_of` (the latest evidence capture_date) -- an
episode could stay "ACTIVE" indefinitely just because no new collector run
happened, even if real calendar time had long since passed its `expiry`.
`clock/run_dynamic_clock.py::run(decision_date=...)` now accepts the real
operational "today" (supplied externally -- e.g.
`briefing/daily_orchestrator.py`'s own `decision_date`, itself from a real
`TZ=Asia/Seoul date` shell command, never `datetime.now()` inside this
module) and evaluates staleness against
`_effective_as_of(decision_date, evidence_as_of) = max(...)` -- never
earlier than the evidence itself (protects against a bad decision_date
input making something look falsely fresh), but no longer capped at
evidence when real time has moved further. Omitting `decision_date`
(the bare `python3 clock/run_dynamic_clock.py` CLI) falls back to
`evidence_as_of` alone -- byte-identical, reproducible artifact mode,
unaffected by wall-clock time.

## Output contract -- raw ledger + consolidated, tiered candidates (item 3)

★ round 1 fix ("candidate flood"): the original version emitted one
`human_review_required=True` record per ACTIVE `(subject, trigger_type)`
episode -- 99 for CRYPTO alone in one run. Split into two granularities:

- **`clock/review_candidate.py::build_raw_trigger_record`** -- one record
  per `(subject, trigger_type)` episode, kept in FULL under
  `raw_trigger_ledger` for audit (nothing ever dropped), with NO
  `human_review_required` field.
- **`build_subject_review_candidate`** -- ONE record per subject,
  consolidating every currently-ACTIVE episode across its trigger types
  into `trigger_types` + `confirmation_count`, then assigned a priority
  **tier** derived only from quantities knowable as of the candidate's own
  `detected_at` (PIT-safe by construction -- see item 4):
  - `confirmation_count >= 2` reuses the exact threshold
    `replay.action_conversion_gate._condition_2` already uses for "PASS".
  - PIT/asset-identity eligibility reuses
    `replay.asset_identity.asset_identity_status` verbatim.

  Tiers: `IMMEDIATE_REVIEW` (`human_review_required=True`, the ONLY tier
  that carries it), `WATCH_REVIEW`, `OBSERVATION_ONLY` (PIT-ineligible).
  Lower tiers are never deleted -- they remain in `review_queue`.

  **Real effect on 2026-08-22 evidence, corrected after round 2's fix**:
  CRYPTO: 99 raw triggers -> 65 consolidated subjects -> **0**
  `IMMEDIATE_REVIEW` (was 2 before the fix -- both were the round-1
  lookahead bug). BTC: 1 raw trigger -> 1 subject -> **`WATCH_REVIEW`**
  (was `IMMEDIATE_REVIEW` via the now-removed exception). KOREA: 0
  `IMMEDIATE_REVIEW`. This is the honest, PIT-correct state: no candidate
  can reach `IMMEDIATE_REVIEW` today because no real thesis/price linkage
  exists yet (item 4).

## Linkage cap -- no exception (item 4, PIT fix)

If BOTH `thesis_linkage` and `price_reflection_status` are
`NOT_LINKED_THIS_SLICE` (true for every candidate today), a candidate that
would otherwise reach `IMMEDIATE_REVIEW` is capped to `WATCH_REVIEW`
(`capped_for_missing_linkage=True`). **There is no exception to this cap of
any kind** -- `compute_tier()`'s function signature structurally cannot
even accept a forward-return/MFE/post-hoc-audit argument
(`test_dynamic_clock_pit_tier_invariant.py::TierSignatureIsPITSafeTests`
enumerates its exact allowed parameters and fails if a future edit adds
one). This is the round-2 fix: round 1's `AUDIT_CONFIRMED_MISS` exception
elevated a candidate to `IMMEDIATE_REVIEW` using PR #210's real,
retrospective audit conclusion (computed from real returns strictly AFTER
the decision date) -- a lookahead violation now removed entirely, not
merely narrowed.

PR #210's Miss-episode registry (`clock.audit_confirmed_miss`) is still
read and attached to each candidate as `post_hoc_audit_note`
(`authoritative_for_tier: False`, `purpose:
"post_hoc_regression_explanation_only"`) -- for regression-explanation
purposes only, never as tier input. BTC's 2026-08-20 candidate carries this
note (a real PR #210 Miss) while sitting at `WATCH_REVIEW`, exactly the
honest answer: as of 2026-08-20 itself, a single tactical trigger with no
thesis/price confirmation is a watch item, not a confirmed opportunity --
only PR #210's later audit (using real subsequent returns) could tell you
it was a Miss.

## P8-10 integration (item 4/8, deferred until PR #212 merges)

**Confirmed merge order**: PR #212 (P8-10) gets its own methodology fixes
first -> re-reviewed, CI green, merged -> PR #211 rebased onto the new
`main` -> PR #211 wires in the real linkages below -> PR #211 re-reviewed
-> only then can P8-12 be marked complete in WBS. Not started in this
revision. When PR #212 merges:
1. Rebase/merge this branch onto the new `main`, resolve the `run_all.py`
   `APPROVED_TESTS` list conflict (both PRs append to the same list).
2. Replace `_thesis_linkage_placeholder()` / `_price_reflection_placeholder()`
   in `clock/review_candidate.py` with real reads of PR #208's P8-08
   `decision/forward_thesis.py` output (read-only) and P8-10's real
   structured `data_state`/`price_state`/`reflection_status` output.
3. Where no linkable thesis exists, use `THESIS_NOT_AVAILABLE` (a real,
   evidence-checked absence) instead of the current blanket
   `NOT_LINKED_THIS_SLICE` placeholder -- different facts, must not be
   conflated.
4. Once real linkage exists, some `WATCH_REVIEW`-capped candidates will
   gain a real (not structural) basis for `IMMEDIATE_REVIEW`.

## Authority (unchanged both rounds)

A Trigger firing is a re-review REQUEST only. Every record's `authority`
block is hard-`False`/`None`/`0`, checked end-to-end in
`AuthorityInvariantAcrossReportTests`. This module has no P5 concept of its
own -- nothing it produces can ever become an Action Proposal/Shadow
Entry/Order regardless of P5 status, structurally (the authority block is
unconditional, not P5-conditioned).

## Real operational wiring (item 6; round 2 atomicity hardening)

★ round 1: `.github/workflows/p8-12-dynamic-clock.yml` triggers via
`workflow_run` on the three REAL upstream collectors this module consumes
(`BTC Price Daily Capture`, `P1-CR-06 Crypto Breadth Daily Capture`,
`Atlas Daily Collect`) plus `workflow_dispatch`. Zero new provider/API
calls. Idempotency: `git diff --cached --quiet` gates the commit.

★ round 2 (atomicity hardening): the workflow now (a) re-syncs to the
absolute latest `main` (`git fetch && git reset --hard origin/<branch>`)
**immediately before computing**, not after -- so the Dynamic Clock is
always computed against the freshest evidence available at commit time;
(b) computes the real `decision_date` via `TZ=Asia/Seoul date +%F` and
passes it through (`--decision-date`); (c) on push, if a race occurred
(something else pushed between the re-sync and this push), **fails closed
rather than rebasing-and-pushing a possibly-stale result** -- the workflow
that just landed on `main` will itself trigger a fresh recompute. Never
force-pushes.

Note: `workflow_run` only activates once this workflow file is on the
default branch (a platform constraint) -- validated by shape/content tests
(`test/test_dynamic_clock_workflow_wiring.py`), not a live trigger.

## Briefing connection (item 7; round 2 policy-status + shape fixes)

★ round 1: `briefing/daily_orchestrator.py::build_dynamic_clock_status()`
wires in the new `DYNAMIC_CLOCK` component (`config/
daily_orchestrator_contract.json` -> `daily_orchestrator/2`, additive-only).
Re-checked for conflicts against current `main` before wiring (PR #211 was
the only open PR at the time; Forward Alpha's touches to this file were
already merged).

★ round 2 fixes:
- **Real "now" threaded through**: `build_dynamic_clock_status()` now calls
  `DYNAMIC_CLOCK.run(decision_date=decision_date)` using the briefing's own
  real decision_date (item 5).
- **Policy status surfaced** (item 7): `policy_approval_status` (=
  `"PROVISIONAL_CIO_MVP"`) is a top-level field on both the full report and
  the briefing packet, so a component's `READY` status is never mistaken
  for "the cadence/tiering policy is finally ratified". KOREA's
  `calendar_confidence` (`UNVERIFIED_NO_HOLIDAY_CALENDAR`) is surfaced at
  the market level in the briefing section too, not only buried per-record.
- **Shape fix** (item 8): `build_briefing_section()` exposes ONLY the
  subject-level `review_queue` slices (`immediate_review`/`watch_review`,
  plus `observation_only_count` and `raw_trigger_count_audit_only` as a
  bare integer) -- the raw 99-record ledger itself never appears in the
  briefing, only in the full committed report for audit. Every candidate
  carries a template-only `reason` string (confirmation_count/PIT-
  eligibility/linkage-presence) instead of any forward-return figure --
  `reference_forward_metrics_*`/`post_hoc_audit_note` never appear anywhere
  in `build_briefing_section()`'s output at all
  (`test_briefing_section_never_carries_a_forward_return_figure` scans the
  whole section for those field names).

## Verification (item 9)

- BTC 2026-08-20 remains present in `review_queue` after triage, now
  correctly at `WATCH_REVIEW` with a `post_hoc_audit_note` (not
  `IMMEDIATE_REVIEW`) -- `BtcRegressionCaseTests`.
- `CorrectedTierCountsTests` asserts `IMMEDIATE_REVIEW == 0` in every
  market on real current evidence -- the direct, checked consequence of
  removing the round-1 exception.
- `test_dynamic_clock_pit_tier_invariant.py` -- the round-2 core fix's
  regression: `compute_tier()`'s signature structurally cannot accept a
  post-hoc argument (enumerated allowlist + forbidden-substring scan +
  `TypeError` on an unexpected kwarg), and tampering with a built
  candidate's forward-return value never changes `tier`.
- Flood-prevention (round 1, still enforced): raw trigger count stays
  high/complete AND `IMMEDIATE_REVIEW` count stays small, asserted
  together.
- P5-not-PASS invariant: structural, see Authority above.
- All prior lookahead/duplicate/expiry/reactivation/determinism tests
  remain green, plus round-2 additions: `EffectiveAsOfTests`/
  `DecisionDateValidationTests` (item 5),
  `AtomicityHardeningTests`/`RealDecisionDateTests` (item 6),
  `test_dynamic_clock_pit_tier_invariant.py` (item 2).

Committed, reproducible artifacts: `evidence/operational/dynamic_clock/
dynamic_clock_report.json` + `.../briefing_section.json`, regenerated
byte-identically by `python3 clock/run_dynamic_clock.py` and refreshed
operationally by the workflow above.
