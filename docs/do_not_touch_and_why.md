# Do not touch, and why

This file exists because a guard without a recorded reason gets worked around.
On 2026-09-18 the CIO nearly repeated a 2026-08-25 mistake -- narrowing a
pinned public-repo checkout from full history to `fetch-depth: 1` -- even
though a standing test already forbade it. The test caught the byte change.
Nothing had recorded *why* the test existed, so the attempt was made anyway,
burning the review cycle the test was supposed to save. The same day produced
four more cases of the same shape: a stale docstring sent an investigation
down the wrong path for an hour (`test/rolling_pointer_snapshot.py` names the
wrong workflow as the KR bounded-review writer); the comment block at
`run_all.py:2985` ("clean checkout 사본 보존 · builder 직렬 재빌드 · byte
비교") correctly describes the retired, still-used-elsewhere 2-shard
`regression_shards()` but was read as describing what `--phase regression`
(`ci_phase_regression_shards()`, the function CI actually calls, which does
none of that -- it only runs `test_set()` and the selected test files as
child processes) does; a `source_owner` substring search matched a module's
own docstring and was reported as a real consumer; and a
prior ratification record cited `test_rule_registry_and_lineage.py:851` as
the live enforcement of a pin it does not enforce at all (see the entry
below for `regime_source_owner_registry_v2.json` -- the correction is
recorded in `config/free_market_data_source_owner_amendment_v1.json` under
`corrected_citation`).

**Reading a comment is not verification.** A comment or docstring is a claim
someone made about the code at the time they wrote it. Code changes; comments
don't change with it unless someone remembers to. Before you rely on what a
comment says a test covers, or what a docstring says a module consumes, open
the test and read its assertions, or grep for the actual call site. Every
entry below was checked that way against `main` at `04c08193a` -- the guard
test was opened, the fail-closed code path was found, and the file paths and
line numbers were confirmed to exist, not copied from a prior report.

If you are about to change something in this repository and it is not on
this list, that absence is not permission -- it may just mean nobody has
been burned by it yet, or it may mean it is genuinely safe to change. This
list has one purpose: for the specific paths named below, stop and read the
entry before you touch them.

---

## 1. Public checkouts that a first-seen/tamper consumer reads must fetch full history

**What must not be changed:** `fetch-depth: 0` on any GitHub Actions
`actions/checkout` step whose checked-out tree is read by code that walks
real git history to compute a first-seen or tamper verdict. Two confirmed
cases in this repository:

- `.github/workflows/actions-pass.yml`, the `regression` job's checkout
  (`fetch-depth: 0`, all four matrix shards). Read by
  `test/test_replay_asset_identity.py::EffectiveFromNeverBackdatedVsRealGitHistoryTests`,
  which reads the real commit history of
  `config/crypto_breadth_exclusion_taxonomy.json` to prove `effective_from`
  was never backdated -- and, in the same job, by
  `test/test_kis_valuation_authority.py`, which exercises
  `portfolio_risk/kis_valuation_authority.py`'s `_row_first_seen()` /
  `_approval_first_seen()` (git `log`/`show` via `subprocess`, lines ~325-608)
  against real history. Only the first consumer is named in the workflow's
  inline comment; the second is not mentioned anywhere near the checkout
  step, which is exactly the shape of the incident below.
- `.github/workflows/btc-price-capture.yml`, the single job's checkout
  (`fetch-depth: 0`). The step has an explicit inline comment: the P3-10 step
  runs `p3_10_crypto_risk_population.py` -> `identity/canonical_identity.py`,
  whose `resolve_instrument_identity()` calls `_git_history_commits()`
  (`git log --follow`) and `verify_document_matches_source()`
  (`git show <commit>:<path>`) to derive first-seen/tamper verdicts.

**What breaks if you do:** a shallow clone truncates the commit graph the
consumer walks. `kis_valuation_authority.py`'s `git log`/`git show` calls
either fail outright or silently resolve to the wrong (truncated-history)
first-seen commit; `identity/canonical_identity.py` is explicit that a
shallow clone makes those checks fail closed as `NOT_COMPUTABLE` rather than
erroring loudly -- so the failure mode is a silent wrong answer, not a crash
you'd notice in CI.

**The incident:**
- 2026-08-25: the original failure that created the standing defense (see
  below) -- a shallow-checkout change was made without the history-walking
  consumer being found by a local grep.
- 2026-09-18: the CIO nearly repeated it. Two related, verified events:
  - `atlas-private-evidence` PR #221 (private repo) shrank a pinned KIS
    checkout to shallow as part of a Docker-prune PR; the shallow-checkout
    half was reverted because the pinned checkout's own
    `kis_valuation_authority.py` walks git history, and a standing test in
    that repo, `test_shadow_matrix.py::test_every_public_checkout_fetches_full_history_for_first_seen_verification`
    (added 2026-08-25 after the same failure class), already forbade it.
    **This guard test lives in `atlas-private-evidence`, not in this
    repository** -- it could not be opened or verified from within this
    clone of `atlas-data`; it is recorded here because it is the same
    failure class against the same public-repo code
    (`kis_valuation_authority.py`), and the origin is
    `gpt/outputs/CLAUDE_CIO_DELEGATED_MERGES_20260914.md` (2026-09-18 entry,
    private #221).
  - Within this repository, commit `b12e33d60` ("Revert
    crypto-breadth-capture.yml to fetch-depth 0; pin cascade too wide",
    2026-09-18, part of PR #787) shows the parallel mistake nearly happening
    here too: five capture workflows were correctly narrowed to
    `fetch-depth: 1` because they never read git history, but
    `crypto-breadth-capture.yml` was narrowed along with them and had to be
    reverted byte-for-byte once the sha256 pin cascade in
    `config/regime_source_owner_registry_v2.json` was found (see entry 2).
    That revert was about a *pin*, not a history-walking consumer --
    `crypto-breadth-capture.yml`'s own steps never call `git log`/`git show`
    -- but it landed in the same PR and the same day, and is easy to
    conflate with this entry. Keep them separate: entry 1 is about a
    consumer that reads history: entry 2 is about a byte-identical pin.

**The guard that enforces it (in this repository):**
`test/test_runner_reporting.py::ActionsPassDiagnosticIsolationTest.test_python_version_and_checkout_pin_repeated_identically_per_job`
parses `.github/workflows/actions-pass.yml` with `yaml.safe_load` (not a
substring search -- the test's own comment explains a plain-text `in` check
would false-positive on prose that mentions "fetch-depth") and asserts the
`regression` job's checkout has `fetch-depth: 0` while `preflight`,
`structural` and `fault-injection` do not.
**`btc-price-capture.yml` has no guard.** Its `fetch-depth: 0` is explained
by a code comment only. No test in this repository's `test/` directory
(`test_btc_scheduler_telemetry.py`, `test_btc_risk.py`, `test_btc_trend.py`,
`test_p3_10_crypto_risk_population.py`, `test_crypto_paper_descriptive_normalization.py`
-- every test file that loads this workflow) asserts its checkout depth.
Narrowing it today would pass CI.

**The legitimate way to change it:** if a consumer genuinely no longer reads
git history (e.g. it is rewritten to read a committed manifest instead),
narrowing the checkout is legitimate -- but first grep every module the
workflow's steps import, transitively, for `git log`/`git show`/`git
blame`/`subprocess` calls into git, not just the modules named in the
nearest comment. `identity/canonical_identity.py` and
`portfolio_risk/kis_valuation_authority.py` are the two confirmed consumers
in this repository as of `04c08193a`; there may be others not yet found by
this search.

---

## 2. `config/regime_source_owner_registry_v2.json` bytes

**What must not be changed:** the file's bytes, in particular any
`*.source_owner.workflow_sha256` / `*.workflow_path` pair for KRX
(`korea-market-signals.yml`), US (`free-market-data.yml`), CRYPTO
(`crypto-breadth-capture.yml`) or CRYPTO status
(`paper-regime-reference.yml`) -- the only four workflows this registry
pins, confirmed by walking every `workflow_path`/`workflow_sha256` node in
the file.

**What breaks if you do:** the registry's own bytes are whole-file
sha256-pinned by at least eleven other config files (confirmed by grep:
`crypto_paper_runtime_v1.json`, `kr_paper_runtime_ratification_candidate_v1.json`,
`us_session_calendar_source_v1.json`, `kr_first_paper_experiment_application.json`,
`paper_runtime_normalization_v1.json`, `regime_semantic_freshness_policy_v1.json`,
`market_scoped_pit_acceptance_contract_v1.json`, `us_historical_pit_replay_identity_v1.json`,
`us_paper_runtime_contract_v1.json`, `korea_five_signal_pointer_writer_v1.json`,
`free_market_data_source_owner_amendment_v1.json`), and two of those --
`crypto_paper_runtime_v1.json` and `kr_paper_runtime_ratification_candidate_v1.json`
-- fail closed with **`POLICY_BINDING_DRIFT`** (raised in
`regime/crypto_paper_runtime.py` and
`regime/kr_paper_runtime_ratification_candidate.py`) on any byte change to
the registry. Those two are themselves bound by committed append-only
evidence: `config/free_market_data_source_owner_amendment_v1.json` records
that applying the direct edit end to end on a throwaway branch touched nine
files that can mechanically absorb it, then hit a wall in seven retained
append-only packets under `evidence/regime/crypto_paper_runtime/2026-09-14`
through `2026-09-17` plus `data/latest_crypto_paper_runtime_decision.json`
-- fixing those means rewriting `policy_sha256` inside committed evidence,
which is append-only by design. The observed failures once that edit is
applied: `CRYPTO_RUNTIME_DECISION_IDENTITY_MISMATCH` (raised in
`universe/crypto_candidate_promotion.py`) and sixteen failures in
`test/test_crypto_paper_wiring_v2.py` that collapse to
`PROMOTION_PACKET_UNAVAILABLE`. A separate full-graph sweep in commit
`b12e33d60`'s message counted 23 files total in the pin-cascade closure from
one workflow-byte change.

**The incident:** 2026-09-18, twice.
1. Commit `b12e33d60` -- narrowing `crypto-breadth-capture.yml` to
   `fetch-depth: 1` as part of a batch of capture-workflow shrinks (PR #787)
   required bumping the registry's `workflow_sha256` for that workflow,
   which cascaded into `POLICY_BINDING_DRIFT` failures in
   `test_kr_paper_runtime_ratification_candidate.py`,
   `test_us_official_session_calendar.py` and
   `test_us_replay_range_declaration.py`, on top of
   `test_regime_decision_authority.py` already failing. None of the five
   affected files carry a ratified/frozen status tied to
   `crypto-breadth-capture.yml`'s content specifically -- they are KR/US
   contracts that happen to snapshot-pin the shared registry -- so
   regenerating them was out of scope for a fetch-depth-only PR, and the
   change was reverted byte-for-byte.
2. `gpt/outputs/CLAUDE_CIO_DEFERRED_UNTIL_RESET_20260918.md` item 33: a
   genuine, user-ratified fix (retry the push on `free-market-data.yml` so
   captured US evidence stops being silently dropped, later merged as PR
   #805 / commit `04c08193a`) was blocked for days because updating the
   registry's `workflow_sha256` for that one workflow hit the same wall.
   `USER_RATIFICATION_REGISTRY_OVERLAY_PIN_VERIFICATION_20260918.json`
   records the measured cost and the ratified way out.

**The guard that enforces it:**
`test/test_regime_decision_authority.py::RegimeSourceOwnerRegistryV2Test`
(the file-level `assert_pins` helper, generic over every `*_sha256`/`*_path`
pair in the registry) plus, downstream,
`test/test_kr_paper_runtime_ratification_candidate.py`,
`test/test_us_official_session_calendar.py`,
`test/test_us_replay_range_declaration.py` and
`test/test_crypto_paper_wiring_v2.py`.
Correction on record: an earlier ratification cited
`test_rule_registry_and_lineage.py:851` as the live enforcement of the US
`workflow_sha256` pin -- that line asserts the *`paper-regime-reference.yml`*
pin, not `free-market-data.yml`'s. The correct citation is recorded in
`config/free_market_data_source_owner_amendment_v1.json` under
`corrected_citation`, alongside a note that the record's conclusion (moving
a ratified binding is outside CIO discretion) was still right even though
its premise was misattributed.

**The legitimate way to change it:** do not edit the registry's bytes.
Ratify an **additive overlay** instead -- a separate config file that
supersedes exactly one `(section, path_key)` pin, bound to the registry's
*exact current bytes* (so it silently stops applying if the registry is
ever edited), that records the value it supersedes, and that is backed by
two re-hashed `USER_RATIFICATION` records (the workflow-byte-change
ratification and the verification-mechanism ratification). This mechanism
is implemented, not merely proposed: `config/free_market_data_source_owner_amendment_v1.json`
is the working example (`registry_binding.mode:
ADDITIVE_OVERLAY_REGISTRY_BYTES_UNCHANGED`), recognized by
`RegimeSourceOwnerRegistryV2Test.assert_pins` via
`RATIFIED_PIN_OVERLAYS` and its `ratified_overlay_pin()` helper, which
refuses the overlay unless it resolves, is bound to the registry's current
bytes, both ratification records re-hash, and the superseded value it
records matches what the registry still carries. Earlier precedents that
establish the same "separate record instead of editing the pinned file"
pattern (though not all of them supersede a hashed pin, which is why only
the US amendment above needed its own extra ratification):
`config/paper_runtime_normalization_v1.json`,
`config/regime_semantic_freshness_policy_v1.json`,
`config/us_session_calendar_source_v1.json` and
`config/korea_five_signal_pointer_writer_v1.json`.

---

## 3. `.github/workflows/stablecoin-capture.yml` -- frozen until 2026-09-24

**What must not be changed:** any byte of this workflow file, including
comments -- a comment-only edit still changes the file's sha256.

**What breaks if you do:** an Ubuntu server at `192.168.0.205` runs a
dispatch controller that reads this workflow's bytes from the GitHub
contents API at `ref: main` and compares them against a fingerprint it holds
on the server side. On a mismatch it returns `drift_blocked` and does not
dispatch the run for that day -- and per
`gpt/outputs/CLAUDE_CIO_DAY_CLOSE_20260918.md` ("9/24까지
`stablecoin-capture.yml` 변경 금지 -- 바이트가 지문으로 고정돼 있어 바뀌면
그날 대신 실행이 조용히 차단됨"), that slot is then permanently resolved for
the day: there is no retry and no alarm. A running 5-day crypto observation
clock (first market-state classification due 2026-09-23 16:00 KST per the
same record) depends on this workflow firing on its own schedule every day
through the freeze window.

**The incident:** ongoing as of 2026-09-18 -- this is a standing freeze, not
a single past event. It is recorded specifically because the freeze date
(2026-09-24) is not self-evident from anything in this repository and the
mechanism that enforces it is not in this repository either.

**The guard that enforces it: none, in this repository.** Every test file
that loads `stablecoin-capture.yml`
(`test/test_crypto_paper_runtime_schedule.py`,
`test/test_population_observation_daily_schedule.py`,
`test/test_stablecoin_supply_demand_population.py`,
`test/test_stablecoin_revision_contract.py`,
`test/test_stablecoin_schedule_hardening.py`) was checked; none of them
compares the workflow's bytes to a fixed sha256 -- they check structure and
behavior (guard ordering, atomic staging, dispatch-input handling), not a
byte fingerprint. The fingerprint that actually blocks a drifted file lives
only on the server's dispatch controller, outside this repository's CI and
outside version control this doc-writing session had access to. **Nothing
in this repository's test suite or CI would catch an edit to this file
before it reached `main` and silently broke a day's dispatch.** Say so
plainly rather than implying a guard exists.

**The legitimate way to change it:** wait until after 2026-09-24, or get the
server-side fingerprint updated in the same change (a server change, which
per project convention requires explicit approval -- see
`reference-ubuntu-server-access.md` in CIO memory: "changes need approval").

---

## 4. `REALTIME_INPUT_AFTER_DECISION` (`decision/crypto_paper_decision_snapshot.py:1604`)

**What must not be changed:** the invariant itself --
`raise CryptoPaperDecisionSnapshotError("REALTIME_INPUT_AFTER_DECISION")`,
raised in `/4`-mode `build_snapshot()` when a realtime input
(`realtime_inputs_latest_at()`, which covers gate status and every retained
message receipt, at sub-second precision) is later than the decision's own
`generated_at`. Do not weaken it, wrap it in a `try`/`except`, or add a
tolerance window.

**What breaks if you do:** the invariant exists to stop a paper-trading
decision from being built using information that postdates the decision
itself -- future information leaking into a point-in-time judgment. Loosen
it and a decision can silently incorporate data captured after the decision
was supposedly made; there is no test that would catch that once the check
itself is weakened, because the check *is* the test for that condition.

**The incident:** 2026-09-18, PR #810 ("Stop the axis-bridge ceiling
arriving a second before its own inputs"). A test helper,
`test_crypto_axis_trade_bridge.py`'s `source_observation_ceiling()`, computed
its ceiling from `run.ended_at` (whole-second precision) and handed it
straight to `build_snapshot()`, while the invariant itself gates on
sub-second-precision message receipts. A capture that retained a message in
its own final second (observed: `run_017`, `ended_at`
`2026-09-18T07:15:37Z`, last message-log receipt at
`07:15:37.221674Z`) had an input genuinely later than the test's own
ceiling, so `build_snapshot()` correctly raised
`REALTIME_INPUT_AFTER_DECISION` -- and whether this happened depended only
on whether the newest committed capture happened to retain such a message,
which is why it looked like flakiness (passing file-by-file, failing on the
full shard) rather than the real bug it was. The fix was entirely in the
test helper (`source_observation_ceiling()` now delegates to the producer's
own ratified `decision_time_not_before_inputs()` instead of inventing its
own tolerance); the invariant at line 1604 was not touched.

**The guard that enforces it:** the invariant itself is exercised by (at
minimum, all in `test/test_crypto_axis_trade_bridge.py::SourceAvailabilityRegressionTests`)
`test_ceiling_refuses_an_input_more_than_one_second_after_the_decision`,
`test_current_ceiling_survives_a_capture_committed_after_the_decision`
(directly asserts `"REALTIME_INPUT_AFTER_DECISION"` is raised) and
`test_ceiling_clears_a_subsecond_receipt_the_capture_stream_retains`
-- four regressions total per the PR #810 commit message, pinning both
directions, including one that goes red if the invariant is ever disabled.
`test/test_crypto_axis_trade_bridge_explanation.py`'s `PRODUCER_PINS` also
carries this test file's own sha256.

**The legitimate way to change it:** if a caller hits this and it looks
wrong, the fix is almost always in the caller's own instant-resolution logic
(as PR #810 was), not in the invariant. `populate()` never had this bug
because it already resolves the instant through
`decision_time_not_before_inputs()` before building; any new caller of
`build_snapshot()` must do the same. Loosening the invariant itself requires
a user decision, since it is the boundary that keeps future information out
of a point-in-time paper-trading judgment.

---

## 5. Pinned implementation files under `evidence/authority/*.json`

**What must not be changed:** any file listed in an adoption record's
`IMPLEMENTATION_PATHS` without also updating that adoption record. Two
confirmed, enumerated lists as of `04c08193a`:

- KR (`regime/kr_information_system_runtime_bridge.py::IMPLEMENTATION_PATHS`,
  checked by `regime/kr_paper_runtime_adoption_v1.py` against
  `evidence/authority/kr_paper_runtime_adoption_v1.json`):
  `regime/kr_information_system_runtime_bridge.py`,
  `regime/kr_paper_runtime.py`, `regime/krx_information_system_capture.py`,
  `regime/paper_regime_reference.py`, `regime/decision_authority.py`,
  `rotation/kr_internal_paper_theme_application.py`,
  `market_data/krx_official_holiday_calendar.py` -- plus, in the same
  adoption record's pin set, `config/korea_leadership_policy.json`
  (`LEADERSHIP_POLICY_PATH`), `config/paper_regime_reference_policy_v1.json`
  (`REFERENCE_POLICY_PATH`) and
  `config/krx_information_system_source_candidate_v1.json`
  (`SOURCE_CONTRACT_PATH`).
- US (`regime/us_paper_runtime.py::IMPLEMENTATION_PATHS`):
  `regime/us_paper_runtime.py`, `regime/us_paper_runtime_publication.py`,
  `regime/decision_authority.py`, `regime/paper_regime_reference.py`,
  `regime/regime_semantic_freshness.py`,
  `regime/market_scoped_pit_acceptance.py`,
  `regime/us_historical_replay_population.py`,
  `config/us_historical_pit_replay_identity_v1.json` (optional -- its
  documented *absence* is itself a bound, meaningful state, not skipped),
  `collectors/free_market_data.py`, `collectors/fred_vix_provenance.py`.

`regime/decision_authority.py` and `regime/paper_regime_reference.py` are
pinned by **both** lists -- an edit to either affects both markets' adoption
status at once.

**What breaks if you do:** any byte change to a pinned path makes
`current_pins()` (or the US equivalent's `implementation_sha256()`) disagree
with the value recorded in the adoption record, and the load path fails
closed with **`ADOPTION_PIN_DRIFT_REQUALIFICATION_REQUIRED`** (KR;
`regime/kr_paper_runtime_adoption_v1.py`, `fail(...)` call adjacent to the
`current_pins()` comparison) or the analogous
**`ADOPTION_BINDING_MISMATCH_IMPLEMENTATION_SHA256`** (US; asserted first in
the ordered `packet["reasons"]` list evaluated from
`regime/us_paper_runtime.py`'s adoption-gate checks). Per
`evidence/authority/kr_paper_runtime_adoption_v1.json`'s own
`requalification_rule` field: "Any change to a pinned implementation, policy
or contract byte fails closed ... until a new adoption record with updated
pins is CIO-adopted." Bumping the pin without requalifying the code is not a
safe workaround -- it re-asserts a ratification over code that was never
actually requalified against the new bytes.

**The incident:** not independently dated in the source material gathered
for this doc; documented here from the code and adoption record directly
(`04c08193a`), per the task's instruction to verify against code rather than
transcribe a summary. If a specific dated drift incident exists, it was not
found in `gpt/outputs/` during this pass -- treat the mechanism as verified
and the incident history as **unverified/not found**.

**The guard that enforces it:**
`test/test_kr_paper_runtime_adoption_v1.py::AdoptionRecordTests.test_pin_drift_authority_and_status_fail_closed`
(asserts `AdoptionError` with message matching
`"PIN_DRIFT_REQUALIFICATION_REQUIRED"` when `current_pins()` is mocked to
drift) for KR;
`test/test_us_paper_runtime.py::GateTest.test_adoption_gates`, the
`"ADOPTION_BINDING_MISMATCH_IMPLEMENTATION_SHA256"` case (drifts
`regime/us_paper_runtime.py`'s recorded hash specifically and asserts it is
the first reason returned) for US, backed by
`test/test_us_paper_runtime_publication.py`'s direct
`implementation_sha256()` assertions (including the documented-absent-file
case for `config/us_historical_pit_replay_identity_v1.json`).

**The legitimate way to change it:** a new adoption record, CIO-adopted,
with `current_pins()` (or the US equivalent) recomputed and the record
explicitly updated to match -- not a hand-edited hash. This is a
requalification event, not a fingerprint refresh: per the same
`requalification_rule` text, it does not just re-pin the bytes, it asserts
that the new code was actually reviewed under the adoption's authority
grant.

---

## 6. `run_all.py`'s `APPROVED_TESTS` list

**What must not be changed:** nothing needs to *not* change here -- the
trap is the opposite: `APPROVED_TESTS` (declared at `run_all.py:70`) must be
kept in exact sync with the actual contents of `test/test_*.py` any time a
test file is added, removed or renamed. It is filename-keyed and lives
outside `config/`, so a grep over `config/*.json` for pin-cascade impact
(the kind of sweep entry 2 required) will not surface it.

**What breaks if you do:** `Runner.test_set()`
(`run_all.py:3299`) lists the actual `test/test_*.py` files on disk, compares
that sorted list against `sorted(APPROVED_TESTS + [FI_SUITE])`, and calls
`self.fail("test-set", ...)` with the exact missing/unapproved file sets on
any mismatch. `approved_tests()` (`run_all.py:3263`) calls `test_set()`
*first* and returns `False` immediately if it fails -- **before a single
regression test file is executed as a child process.** Because
`.github/workflows/actions-pass.yml`'s `regression` job runs this same
check independently inside each of its 4 matrix shards
(`python3 run_all.py --phase regression --shard-count 4 --shard-index N`),
one unregistered test file fails all four shards at once, simultaneously,
with a generic "list mismatch" error that gives no hint that the rest of
main is fine -- which is exactly what makes it easy to misdiagnose as "main
is broken" instead of "this PR forgot one line in `run_all.py`."

**The incident:** 2026-09-18 -- `gpt/outputs/CLAUDE_CIO_DAY_CLOSE_20260918.md`,
CIO mistake log entry 4: "시험 승인목록 오진 -- main 탓으로 돌림" ("test
approval-list misdiagnosis -- blamed on main"), listed among nine
same-day mistakes the CIO made from not measuring directly. Ten separate
PRs merged on 2026-09-18 alone each added new test files and had to append
to `APPROVED_TESTS` in the same commit (`#800`, `#802`, `#799`, `#793`,
`#794`, `#790`, `#786`, `#785`, `#809`, `#805` all touch `run_all.py`) --
this is not a rare edge case, it is the normal shape of nearly every PR that
adds a test file, which is what makes a single missed registration easy to
mistake for a broken `main` rather than a local oversight.

**The guard that enforces it:** `run_all.py:3299`,
`Runner.test_set()`, called from `approved_tests()` at
`run_all.py:3263` before any child test runs; wired into CI via
`.github/workflows/actions-pass.yml`'s 4-way `regression` matrix. There is
no separate named test for this -- the check is inline in the runner itself,
which is why it reads as an opaque runner failure rather than a named,
citable regression.

**The legitimate way to change it:** when adding a new `test/test_*.py`
file, add its path to `APPROVED_TESTS` (`run_all.py:70` onward) in the same
commit. When removing or renaming a test file, remove or update its entry
the same way. `run_all.py --phase regression` (or the full run) will fail
loudly and immediately if this is missed -- read the "누락"
(missing)/"미승인" (unapproved) lists in the failure message rather than
assuming CI itself is broken.
