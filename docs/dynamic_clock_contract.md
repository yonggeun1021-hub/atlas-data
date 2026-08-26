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

**P8-10 <-> P8-12 integration (2026-08-23, locked spec, post PR #212
merge `4802dad`)**: connects the two now-approved contracts PIT-safely --
see "P8-10 integration" below. Not another patch round: the CIO supplied
the full design, boundaries, expected results, and required tests up front
in one locked spec; this section documents that implementation and its
verification.

**CIO integration review round 1 (2026-08-23, PR #211 HEAD `d7353ae`)**:
despite CI/tests passing, the CIO independently reproduced 4 real
operational defects the locked spec's own tests did not catch -- see
"CIO integration review round 1" below for the full fix-by-fix
documentation. This SUPERSEDES the round-2 `_effective_as_of()`/`max()`
mechanism described below (item 5) and the "`post_hoc_audit_note` attached
to the candidate" description in "Linkage cap" below -- both sections are
left in place for history but are no longer how the code behaves; see the
new section for what replaced them.

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
`clock/run_dynamic_clock.py::run(decision_date=...)` accepts the real
operational "today" (supplied externally -- e.g.
`briefing/daily_orchestrator.py`'s own `decision_date`, itself from a real
`TZ=Asia/Seoul date` shell command, never `datetime.now()` inside this
module) and evaluates staleness against it.

★ **superseded by integration review round 1's defect 1** (see the
dedicated section below): the mechanism above originally used
`_effective_as_of(decision_date, evidence_as_of) = max(...)`, which the CIO
found silently overwrote an EARLIER explicit `decision_date` with LATER
evidence -- a real lookahead violation. `_effective_as_of()` and its test
class are deleted entirely; `decision_at` is now taken directly from
`decision_date` (no `max()` correction of any kind), and the scanner itself
(`clock/operational_scan.py`) filters evidence to `capture_date <=
decision_date` at the source, so `evidence_as_of <= decision_date` holds
structurally rather than needing a later correction. Omitting
`decision_date` entirely (the bare `python3 clock/run_dynamic_clock.py`
CLI) still falls back to each market's own real latest evidence --
byte-identical, reproducible artifact mode, unaffected by wall-clock time.

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
read, for regression-explanation purposes only, never as tier input --
★ **superseded by integration review round 1's defect 3 (see below):** it
is no longer attached to the operational candidate object at all. It is
built into the physically separate `clock/audit_diagnostics.py` artifact
instead, so the operational `review_queue` record carries no trace of it.
BTC's 2026-08-20 diagnostic record still carries this note (a real PR #210
Miss) even though its operational candidate sits at `WATCH_REVIEW`, exactly
the honest answer: as of 2026-08-20 itself, a single tactical trigger with
no thesis/price confirmation is a watch item, not a confirmed opportunity
-- only PR #210's later audit (using real subsequent returns) could tell
you it was a Miss.

## P8-10 integration -- real, PIT-safe, done (CIO's locked integration spec, 2026-08-23)

PR #212 (P8-10) merged (`4802dad`, source `e559df3`). PR #211 rebased onto
the new `main` (clean, zero conflicts -- PR #212's own scope confirmation
claimed "zero overlap with PR #211's file list" and the rebase confirmed
it) and now wires in the REAL `price_reflection` link:

- **`clock/price_reflection_link.py`** -- reuses `decision/price_evidence.py`'s
  `assemble_price_evidence()` and `decision/price_reflection.py`'s
  `build_packet()`/`validate_packet()` UNCHANGED (dynamically loaded,
  mirroring `decision/pilot_evidence_intake.py`'s own established call
  pattern). `price_reflection_supported(subject, market)` reflects P8-10's
  real, honest coverage boundary: BTC and KRX Korea codes are linked for
  real; CRYPTO-market (non-BTC) subjects get `NOT_SUPPORTED_FOR_SUBJECT`
  (no crypto-altcoin price-evidence source exists in this repo -- never
  guessed via the wrong evidence path). `link_price_reflection()` never
  raises -- a genuine failure (bad evidence, a rejected packet) is recorded
  as `LINK_FAILED` for that ONE candidate only (integration spec item 3.8).
- **`verify_and_extract()`** -- independently re-validates every packet via
  `decision.price_reflection.validate_packet()` before reading a single
  field, cross-checks `subject`/`decision_date`, and re-asserts
  `reflection_status == "UNKNOWN"` a SECOND time on top of that validator's
  own enforcement. Only the field allowlist (item 3) ever flows out:
  `subject`, `decision_date`, `price_state`, `reflection_status`,
  `data_state`, `threshold_basis`, `price_as_of`, `reasons`,
  `contract_version`/`packet_sha256` -- never `relative_strength`/
  `recent_return_windows`/the inert `event_reaction`/`reflection_reference`.
- **A THIRD, independent lock** at the consuming layer:
  `clock/review_candidate.py::_assert_price_reflection_status_is_pit_safe`
  rejects a `"LINKED"` `price_reflection_status` with any non-`"UNKNOWN"`
  `reflection_status`, even if it bypassed `verify_and_extract()` entirely
  (a directly-injected tampered dict) -- item 8.2's exact scenario.
- **`compute_tier()`'s cap logic** (`_is_confirmatory_linkage`): a real
  `"LINKED"` price_reflection does NOT by itself lift the `IMMEDIATE_REVIEW`
  cap -- `threshold_basis` must also be `"RATIFIED"`, never `"PROVISIONAL"`
  (item 3.4/5.3). P8-10's own contract has
  `classification_thresholds_approval_status: "PROVISIONAL"` today, so a
  real, successfully-linked `price_state=OVEREXTENDED` (BTC) or
  `MODERATE`/`WEAK` (Korea names) is diagnostic information only and can
  NEVER elevate a candidate -- verified against REAL current evidence
  (`RealP810IntegrationTests`), not just synthetic fixtures.
- **Thesis linkage (P8-08) stays OUT OF SCOPE** -- the locked spec connects
  only P8-10's `price_reflection` and P8-12's Dynamic Clock, not a third
  contract; `thesis_linkage` remains the honest `NOT_LINKED_THIS_SLICE`
  placeholder.

**Real numbers on current evidence** (see section 9's re-derivation table
in the PR report for the full split): BTC `price_state=OVEREXTENDED`,
`reflection_status=UNKNOWN`, `threshold_basis=PROVISIONAL`, tier
`WATCH_REVIEW`. Korea 005930 `MODERATE`, 000660 `WEAK`, 298040 `MODERATE`,
all `WATCH_REVIEW`. Korea 034020 (두산에너빌리티, no evidence PIT-available
at that report's decision date)
`price_state=UNKNOWN`/`data_state=PRICE_DATA_MISSING` -- honest, not
fabricated. CRYPTO altcoins: `price_reflection_status=NOT_LINKED_THIS_SLICE`
for all (P8-10 doesn't cover them). `IMMEDIATE_REVIEW` remains 0 in every
market -- the correct, PIT-honest state until a RATIFIED threshold basis or
a real thesis linkage exists.

## CIO integration review round 1 (4 defects, 2026-08-23)

CI/tests passing on PR #211 HEAD `d7353ae` did not catch 4 real operational
defects the CIO independently reproduced against real evidence. Fixed
together, in one pass (not sequential micro-patches, per explicit
instruction):

**Defect 1 (P0) -- past `decision_date` silently used future evidence.**
The old `_effective_as_of(decision_date, evidence_as_of) = max(...)`
(item 5 above) silently overwrote an EARLIER explicit `decision_date` with
LATER evidence -- a real PIT lookahead violation (requesting
`decision_date=2026-08-20` showed evidence/decision_date fields from
2026-08-21/2026-08-22 in the output). Removed entirely, not patched.
Fixed at the source: `clock/operational_scan.py`'s `scan_btc`/`scan_korea`/
`scan_crypto` now accept `decision_date` and filter snapshots to
`capture_date <= decision_date` (`_filter_snapshots`) BEFORE any series is
built or trigger is detected -- the scanning boundary itself, not a
post-hoc correction. `clock/run_dynamic_clock.py` adds
`mode="OPERATIONAL"` (default: fails closed with
`DECISION_DATE_PRECEDES_EVIDENCE_AS_OF` if the real, unfiltered latest
evidence is AHEAD of a caller-supplied `decision_date` -- anomalous for
live use) vs `mode="HISTORICAL_REPLAY"` (the genuine past-reconstruction
case; the same scanner-level filtering still applies, only the fail-closed
anomaly check is skipped). Regression:
`test/test_dynamic_clock_orchestrator_defects.py::
Defect1NoFutureEvidenceAnywhereTests` recursively sweeps the ENTIRE report
and briefing section for any date after the requested cutoff (excluding
`expiry`/`next_review_at`, which are deliberately forward-looking
SCHEDULE outputs of the cooldown policy, not evidence used to reach a
decision); `test/test_dynamic_clock_fail_closed.py::
DecisionDatePrecedesEvidenceFailClosedTests` covers the fail-closed/
historical-replay/invalid-mode paths. `EffectiveAsOfTests` (the test that
asserted the OLD `max()` behavior as correct) is deleted, per explicit
instruction, not left as a stale pass.

**Defect 2 (P0) -- "new" triggers leaking stale raw triggers forever.**
`new_triggers_this_run` used to mean `opened_at == evidence_as_of` -- a
date-equality check that stays true on EVERY re-run for as long as no
fresher evidence arrives (a re-run the next day still showed CRYPTO's 95
prior triggers as "new"). Fixed:
`_load_previous_committed_episode_ids(market)` reads the market's own
PREVIOUSLY COMMITTED `dynamic_clock_report.json` (if one exists) and an
episode is "new" only if its `episode_id` was NOT already present there.
No prior committed state (genuine bootstrap) is explicitly
`newness_status="NEWNESS_NOT_COMPUTABLE"` -- never defaulted to "everything
is new". The raw, per-trigger `new_triggers_this_run` list stays
audit-only in the full report; the briefing's `new_triggers` is always
subject-level consolidated from `new_subjects_this_run` via `review_queue`
(`_briefing_candidate_summary`), never the raw list. Regression:
`Defect2NewnessIsCommittedStateDiffTests::
test_second_run_against_identical_committed_state_shows_zero_new_triggers`
writes a first run's report to an isolated temp `REPORT_PATH`, re-runs
against the SAME evidence, and asserts `new_triggers_this_run == []` and
`new_subjects_this_run == []` for every market on the second run; a
companion test confirms the bootstrap (no prior state) case is
`NEWNESS_NOT_COMPUTABLE`, not silently "new".

**Defect 3 (P1) -- post-hoc data physically present in the operational
object.** `post_hoc_audit_note`/`reference_forward_metrics_first_detection`/
`reference_forward_metrics_latest_detection` used to be fields ON the
`review_queue` Candidate itself (`authoritative_for_tier: False` was true,
but the fields were still physically readable by any downstream consumer of
that object). Not being a tier INPUT was not enough. Fixed: `build_
subject_review_candidate()`/`build_raw_trigger_record()` no longer accept
or emit any of these fields at all -- removed from their signatures
entirely (`TypeError` on a forbidden kwarg, not a silently-ignored one).
The new, physically SEPARATE `clock/audit_diagnostics.py` module (the ONLY
importer of `clock/audit_confirmed_miss.py` anywhere in this package) builds
`build_audit_diagnostic_record()` independently, written to its own
committed file `evidence/operational/dynamic_clock/audit_diagnostics.json`.
`clock/run_dynamic_clock.py::run()` (what `build_briefing_section()` and
`dynamic_clock_report.json` are built from) strips this key before
returning; only `run_with_diagnostics()` surfaces both.
`briefing/daily_orchestrator.py` calls `DYNAMIC_CLOCK.run()`, never
`run_with_diagnostics()`. Regression:
`Defect3AuditArtifactNeverReadByOperationalPathTests::
test_run_output_is_byte_identical_whether_or_not_the_miss_evidence_file_exists`
monkeypatches `clock.audit_confirmed_miss.MISS_EPISODES_PATH` to a
nonexistent file and proves `run()`'s output is byte-identical either way
(not merely "the value isn't used" -- the file is never even read on the
operational path); companion tests assert `clock/review_candidate.py`'s own
namespace never references `audit_confirmed_miss`/`audit_diagnostics` and
`briefing/daily_orchestrator.py`'s source never imports either module.

**Defect 4 (P1) -- required PIT timing fields never implemented.** The
locked P8-10 integration spec required a full timing contract; candidates
only carried `detected_at`/`first_detected_at`/`next_review_at`. Fixed:
`build_subject_review_candidate()` now requires `decision_at` and emits
`evidence_as_of`, `trigger_observed_at`, `decision_at`, `price_as_of`,
`candidate_created_at`, `candidate_updated_at`, and
`time_precision="DATE_ONLY"` (this repo's evidence is date-granularity;
`price_as_of`, when P8-10 supplies a real UTC timestamp, is compared at
date resolution only). `clock/review_candidate.py::
_validate_candidate_timing()` enforces, independently for each rule:
`evidence_as_of <= trigger_observed_at <= decision_at`,
`price_as_of <= decision_at` (skipped when `price_as_of` is `None`/
`"UNKNOWN"`), and `candidate_created_at <= candidate_updated_at <=
decision_at`, raising `TIMING_INVARIANT_VIOLATED:<field>(...)><field>(...)`
on any violation. Regression:
`Defect4TimingOrderingIndependentRejectionTests` has one dedicated test per
ordering rule (5 rules), each violating exactly ONE constraint with all
others left valid, plus an end-to-end test proving `build_subject_review_
candidate()` itself (not just the bare validator) rejects a `decision_at`
behind `trigger_observed_at`.

### Collector timestamp lineage (P8-12 precision foundation)

The operational price inputs already commit exact, timezone-aware collector
timestamps: BTC and Crypto Breadth use manifest `fetched_at_utc`; KRX uses
`collected_at_utc`. These values are now retained as
`evidence_captured_at` with
`evidence_capture_time_precision="TIMESTAMP"` through the evidence index,
`ClockEvent`, episode trail, raw ledger, and subject candidate. They are
never derived from a path, the decision date, or wall-clock time.

This does **not** make the candidate timestamp-precise. Trigger detection,
decision, creation, and update remain real `DATE_ONLY` observations. Thus
top-level `time_precision` remains `DATE_ONLY`, while `timing_precision`
reports each field independently. A same-day exact capture cannot establish
whether it preceded a date-only decision and therefore cannot open a
validity window, Risk Capacity, P8-13, Stage, Buy, Action, Order, Production,
or trading authority.

The validator independently reconstructs the precision map and rejects a
re-signed packet that promotes aggregate precision, alters a field's
precision, supplies a future capture date, or supplies a timezone-naive
timestamp. Candidate validity policy remains `UNRATIFIED`; this slice only
preserves real arrival-time evidence needed for later shadow observation.

### Exact operational evaluation timestamp (current-run fact only)

The operational workflow supplies `--evaluation-at-utc` using the same epoch
from which it derives the KST `decision_date`. The resulting
`operational_evaluation.evaluated_at_utc` is carried at report, market, and
candidate levels and independently revalidated. It is a truthful timestamp of
the current computation, not a reconstruction of when an older date-only
trigger first fired.

The contract therefore keeps `trigger_observed_at`, `decision_at`, creation,
and update fields at `DATE_ONLY`; exact operational evaluation never changes a
tier, authority, or top-level precision. Historical replay rejects the field,
artifact reproduction emits an explicit `NOT_AVAILABLE` context, and an
evidence or price timestamp after the supplied evaluation instant fails
closed. This starts collecting exact forward operational evaluation samples
without backdating legacy candidates or opening candidate validity.

### Current-run identity review material

After each operational run, the workflow rebuilds the candidate identity
observation and unresolved-gap inventory, then immediately rebuilds the
mechanical identity proposal and its cross-row conflict audit from that same
gap packet. This prevents an older proposal population from being displayed
as if it were bound to the latest candidates.

Both downstream packets remain review material only. They write solely below
`evidence/identity/proposals`, report
`canonical_authority_rows_created=0`, and never modify
`config/canonical_security_identity.json` or
`config/market_account_scope_map.json`. Mechanical completeness or coherence
does not evaluate candidate validity, risk capacity, P8-13, or a money action.

All 4 defects' regressions live together in
`test/test_dynamic_clock_orchestrator_defects.py` (registered in
`run_all.py`'s `APPROVED_TESTS` and the workflow's offline regression
step).

## CIO integration review round 2 (2 closing items, 2026-08-23, HEAD `d26b4e3`)

Independent verification confirmed 3 of the 4 defects above correctly
fixed (decision_date fail-closed/historical-replay, newness diffing, PIT
timing fields all reproduced correctly against real evidence). Two narrow
closing items remained on defect 3 and the new test file itself:

**Closing item 1 -- defect 3's round-1 fix was cosmetic field-stripping,
not physical separation.** `run_dynamic_clock.py` still had
`from replay.forward_metrics import compute_forward_metrics` and
`from clock.audit_diagnostics import build_audit_diagnostic_record` as
TOP-LEVEL imports, and `_market_result()` computed BOTH for every subject
unconditionally, only stripping the resulting keys from the returned dict
at the very end. So `run()` still READ the post-hoc/forward-return
machinery and would still fail if either broke, even though the output
never showed it. Fixed for real: both imports removed from module top
level entirely; `_market_result()` (what `run()`/`_assemble()` are built
from) contains no call to either function anywhere. A new, genuinely
separate `_market_diagnostics()` function -- called ONLY from
`run_with_diagnostics()` -- lazily imports and computes them via its own
independent re-scan, sharing no state with `_market_result()`. Regression
(`test_dynamic_clock_orchestrator_defects.py::
Defect3AuditArtifactNeverReadByOperationalPathTests`):
- `test_compute_forward_metrics_and_confirmed_miss_for_are_called_zero_times_during_run`
  -- mock call-count assertion (`call_count == 0`) for both functions
  across a real `run()` execution, in both OPERATIONAL and
  HISTORICAL_REPLAY modes.
- `test_the_same_two_functions_ARE_called_during_run_with_diagnostics` --
  companion sanity check proving the mocks are wired to something real
  (a call-count-zero test that patched the wrong target would trivially
  "pass" for the wrong reason).
- `test_operational_run_is_unaffected_if_audit_diagnostics_computation_raises`
  -- patches `build_audit_diagnostic_record` to raise; `run()` still
  succeeds and produces the byte-identical result, while
  `run_with_diagnostics()` (which DOES call the audit path) genuinely
  propagates the failure, proving the mock would have been hit had `run()`
  called it.
- `test_run_dynamic_clock_module_source_has_no_top_level_audit_import` --
  structural source scan for the two forbidden top-level imports.

**Closing item 2 -- the test suite depended on run order.** Running
`test_dynamic_clock_pit_tier_invariant.py` before
`test_review_candidate_contract.py` (and some other orderings) in the same
`python -m unittest` process produced ERRORs. Root cause:
`test_module_level_guard_runs_at_import_time` did
`importlib.reload(clock.review_candidate)`, which rebinds
`ReviewCandidateError` (and every function in that module) to NEW
class/function objects -- any other test in the same process that had
already statically imported the OLD class before that reload then failed
`assertRaises(ReviewCandidateError, ...)` against the new one, silently.
The round-1 fix only patched the SYMPTOM (the new defect-test file's own
imports); the actual cause -- the `reload()` call -- was still there and
still poisoned other tests. Fixed for real:
`importlib.reload()` is banned from the shared test process entirely.
`test_module_level_guard_runs_at_import_time` now calls
`clock.review_candidate._assert_tier_signature_is_pit_safe()` directly
(idempotent -- proven not to raise on a second call after it already
passed once at import), and a new companion
`test_fresh_subprocess_import_does_not_raise` proves the module-level
guard genuinely fires on a real, first-time import by importing
`clock.review_candidate` in an actual separate subprocess -- a strictly
more faithful proof than `reload()` ever was. Verified: both
`test_dynamic_clock_pit_tier_invariant` -> `test_review_candidate_contract`
and the reverse ordering pass; the full 12-file dynamic-clock suite (260
tests) passes in both forward and reverse full-suite order, and passes
run file-by-file as 12 separate processes (matching `run_all.py`'s own
per-file subprocess convention).

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
wires in the new `DYNAMIC_CLOCK` component (introduced under
`daily_orchestrator/2`; the current additive contract is
`daily_orchestrator/3` after the separate zero-capital review bridge).
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
  correctly at `WATCH_REVIEW`, with NO `post_hoc_audit_note` field on the
  candidate itself (integration round 1, defect 3) -- the note is still
  independently verifiable in the separate `audit_diagnostics` artifact --
  `BtcRegressionCaseTests`.
- `CorrectedTierCountsTests` asserts `IMMEDIATE_REVIEW == 0` in every
  market on real current evidence -- the direct, checked consequence of
  removing the round-1 exception.
- `test_dynamic_clock_pit_tier_invariant.py` -- the round-2 core fix's
  regression: `compute_tier()`'s signature structurally cannot accept a
  post-hoc argument (enumerated allowlist + forbidden-substring scan +
  `TypeError` on an unexpected kwarg), and tampering with a built
  candidate's forward-return value never changes `tier`; plus integration
  round 1's structural proof that the candidate carries no post-hoc field
  and that `clock/review_candidate.py` never imports the audit modules.
- Flood-prevention (round 1, still enforced): raw trigger count stays
  high/complete AND `IMMEDIATE_REVIEW` count stays small, asserted
  together.
- P5-not-PASS invariant: structural, see Authority above.
- All prior lookahead/duplicate/expiry/reactivation/determinism tests
  remain green, plus round-2 additions: `DecisionDateValidationTests`
  (item 5), `AtomicityHardeningTests`/`RealDecisionDateTests` (item 6),
  `test_dynamic_clock_pit_tier_invariant.py` (item 2), plus integration
  review round 1's 4-defect regressions in
  `test_dynamic_clock_orchestrator_defects.py` and
  `DecisionDatePrecedesEvidenceFailClosedTests` (`EffectiveAsOfTests`,
  which asserted the now-removed `max()` behavior as correct, is deleted).

Committed, reproducible artifacts: `evidence/operational/dynamic_clock/
dynamic_clock_report.json` + `.../briefing_section.json` +
`.../audit_diagnostics.json` (physically separate, integration round 1
defect 3), all regenerated byte-identically (given unchanged evidence) by
`python3 clock/run_dynamic_clock.py` and refreshed operationally by the
workflow above.

## Structured source-identity lineage

Provider adapters now carry their already-known `source_name` and
`source_asset_id` through `ClockEvent`, episode evidence trails, the raw
trigger ledger, and each consolidated subject candidate. The consolidated
field is `source_identity_lineage`, containing a canonical sorted set of
provider pairs. It is `AVAILABLE` only when every contributing evidence
event supplies a complete pair; legacy or partial evidence is
`NOT_COMPUTABLE_SOURCE_IDENTITY_LINEAGE_MISSING`.

This is transport, not resolution. Dynamic Clock does not parse citation
paths, infer a provider from market/ticker, resolve a canonical instrument,
change a candidate tier, ratify validity, or grant any authority. A pinned
downstream consumer must independently validate the candidate, reconcile the
raw episode lineage, and use the separate canonical-identity authority.
