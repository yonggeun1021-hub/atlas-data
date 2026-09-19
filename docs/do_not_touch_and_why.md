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

## 3. The 13 workflow files the server dispatcher pins by fingerprint

**What must not be changed:** on any of the thirteen files listed below, three
kinds of edit silently and permanently disable that workflow's server-side
catch-up. They are, exactly:

1. the `schedule:` cron expressions,
2. the `workflow_dispatch:` input **names** -- adding, removing or renaming
   one, or removing the `workflow_dispatch:` trigger,
3. **any line** matching the dispatcher's event-reference regex (below),
   including a line inside a **comment**.

Plus, for `stablecoin-capture.yml` only, **any byte at all**, including a
comment, until **2026-09-24** (see the sub-entry at the end).

Everything else -- `run:` step bodies, `env:` values that do not match the
regex, third-party `uses:` pins, added or removed steps, job names,
indentation, comments containing none of the regex tokens -- is a
**compatible** change: the file's bytes drift from the pin, the dispatcher
logs a warning, and it keeps dispatching. This distinction is the whole point
of this entry. A blanket "do not touch these thirteen files" would be wrong
(six of them have already drifted and still work) and would therefore be
ignored.

### The thirteen pinned files

Pinned at commit `a3f28ed3` (`pinned_at_utc` 2026-09-14T06:05:00Z) in
`/etc/atlas-schedule-dispatcher/config.json` on the Ubuntu server at
`192.168.0.205`. "Slot at risk" is what a blocking change destroys.

| # | workflow file | crons (UTC) | mode | dispatch inputs the pin allows | slot at risk |
|---|---|---|---|---|---|
| 1 | `upbit-realtime-capture.yml` | `6,36 * * * *` | primary | `duration_seconds`, `validation_duration_seconds` | every 30 min |
| 2 | `crypto-breadth-capture.yml` | `40 0 * * *` | catch_up | (none) | daily 00:40Z |
| 3 | `upbit-universe-capture.yml` | `50 0 * * *` | catch_up | (none) | daily 00:50Z |
| 4 | `upbit-microstructure-capture.yml` | `20 1 * * *` | catch_up | (none) | daily 01:20Z |
| 5 | `stablecoin-capture.yml` | `50 5`, `20 6`, `20 7`, `20 8` | catch_up | `guard_mode` | daily 05:50Z + 06:20Z |
| 6 | `crypto-paper-runtime.yml` | `15 7 * * *`, `45 8 * * *` | catch_up | (none) | daily 07:15Z + 08:45Z |
| 7 | `briefing-handoff-watchdog.yml` | `25 22 * * *`, `50 9 * * 1-5` | catch_up | `decision_date`, `fail_on_alert` | 22:25Z + 09:50Z |
| 8 | `daily-briefing.yml` | `5 22 * * *`, `30 9 * * 1-5` | **alert_only** | `slot`, `mode`, `decision_date`, `portal_canary` | (never dispatches) |
| 9 | `daily-briefing-recovery.yml` | `20 22`, `40 22`, `45 9`, `5 10` | **alert_only** | (not dispatchable) | (never dispatches) |
| 10 | `spdr-sector-holdings.yml` | `0 22 * * 1-5` | catch_up | `tickers` | weekdays 22:00Z |
| 11 | `fred-dexkous-fx.yml` | `40 21 * * 0-5` | catch_up | `backfill` | daily 21:40Z |
| 12 | `kr-paper-runtime-daily-publish.yml` | `40 23 * * 0-4`, `45 0 * * 1-5` | catch_up | `mode`, `signals_run_id` | 23:40Z + 00:45Z |
| 13 | `us-paper-runtime.yml` | `55 21 * * 0-5`, `40 23 * * 0-5` | catch_up | (none) | 21:55Z + 23:40Z |

Rows 8 and 9 are the two exceptions: all their targets are `alert_only`
(`max_dispatches_per_24h: 0`), and the pin gate is skipped for `alert_only`
targets, so a blocking change to those two files does not suppress their
alert. **The other eleven each carry at least one catch-up or primary slot
that a blocking change kills.** Row 9 is additionally `dispatchable: false`,
so its input axis is not checked at all.

There is a second config file, `/etc/atlas-schedule-dispatcher/config-private.json`,
which pins two more workflows (`kis-master-capture.yml`,
`price-history-capture.yml`) under the same rules -- but its `repository` is
`yonggeun1021-hub/atlas-private-evidence`, **not this repository**. Those two
files do not exist here and nothing you change in `atlas-data` can affect
them. They are named only so that "fifteen pinned files" in an older note
resolves.

### The rule, exactly as implemented

From `evaluate_pin()` in `/opt/atlas-schedule-dispatcher/atlas_schedule_dispatcher.py`
(read on 2026-09-19; the file is `root`-owned and world-readable, so this was
read from the implementation, not from a summary):

- If the file's live git blob sha on `main` equals the pinned `blob_sha`, the
  status is `pinned` and **nothing else is examined**.
- Otherwise -- i.e. on any byte change -- the dispatcher derives a problem
  list:
  - `crons_changed` -- the parsed `schedule:` crons differ from the pinned
    `crons`.
  - `event_branch_lines_changed` -- the `event_ref_fingerprint` differs.
  - and, only for `dispatchable` workflows: `workflow_dispatch_removed`;
    `dispatch_input_removed` (an input a target fills is gone);
    `required_input_unfilled:<name>` (a `required: true` input that no target
    fills); `dispatch_inputs_changed` (the set of input names differs from
    `dispatch_inputs_allowed`).
- Empty problem list -> **`drift_compatible`**: a warning is logged and the
  workflow keeps dispatching. Any problem -> **`drift_blocked`**.

`event_ref_fingerprint` is the sha256 of every **stripped** line matching,
case-insensitively:

```
GITHUB_EVENT|github\.event|github\.(triggering_)?actor|github\[|toJSON\(github|\binputs\.|EVENT_NAME|EVENT_SCHEDULE|uses:\s*\./
```

joined by `\n`. Three consequences that are not obvious and have each already
cost something:

- **Comments are not excluded.** The fingerprint function does not strip
  comments (unlike `parse_workflow_triggers()`, which does). A comment
  containing `github.event`, or even the bare word `inputs.` -- `\binputs\.`
  matches `dispatch inputs.` at the end of an English sentence -- changes the
  fingerprint and blocks the slot.
- **Indentation alone is safe, reordering is not.** Lines are stripped before
  hashing, so re-indenting a matching line does not change the fingerprint;
  moving one matching line above another does.
- **`uses: ./` means local composite actions only.** `uses: actions/checkout@...`
  does not match; `uses: ./.github/actions/foo` does.

### What "blocked" costs

From `evaluate()` in the same file: when a target whose mode is not
`alert_only` sees `drift_blocked`, the dispatcher calls
`resolve(key, slot, "skip_pin_drift_blocked")`, which writes that slot into
`state["resolved"]`. `evaluate()` returns immediately for any key already in
`state["resolved"]`. **The slot is therefore never reconsidered: no retry,
ever, for that slot.** The process logs at `level=error` and exits `6`, but
that is a line in the systemd journal on a machine nobody is watching -- there
is no mail, no webhook, no GitHub signal. The workflow's own GitHub page looks
normal, because the *schedule* still fires; only the catch-up is gone. Note
the contrast with `unverified` (a GitHub API failure): that also skips, but
does **not** resolve, so it retries on the next cycle. Only `drift_blocked` is
permanent.

### Verified drift state on `main` as of 2026-09-19

Recomputed by re-implementing `git_blob_sha()` and `event_ref_fingerprint()`
against `origin/main` (`3af23514b`) and comparing with the values in the
server config -- not read from the dispatcher's own state, which lives in
`/var/lib/atlas-schedule-dispatcher/` and is `root`-only:

- **`drift_compatible` (bytes differ from the 2026-09-14 pin; crons,
  fingerprint and input names all unchanged):**
  `upbit-realtime-capture.yml`, `upbit-universe-capture.yml`,
  `upbit-microstructure-capture.yml`, `daily-briefing.yml`,
  `fred-dexkous-fx.yml`, `kr-paper-runtime-daily-publish.yml`.
- **`pinned` (byte-identical):** the other seven.
- **`drift_blocked`: none.**

Six of thirteen already drifted and all six still dispatch. That is the
evidence for the compatible/blocking distinction, and the reason a blanket
freeze on these files would be both wrong and counterproductive.

### The incident

**2026-09-18/19, PR #815** ("Consolidate every workflow push retry onto one
shared script", ready for review, not a draft). It touches **seven** of the
thirteen pinned files. Computing the fingerprint on `refs/pull/815/merge`
(`3613b2fb6`) against the pinned values gives:

- **Blocking** -- `event_branch_lines_changed`, because the PR adds
  `DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}`, which
  matches `github\.event`:
  - `fred-dexkous-fx.yml` -> kills the daily 21:40Z catch-up.
  - `spdr-sector-holdings.yml` -> kills the weekday 22:00Z catch-up.
- **Compatible** -- bytes change but the fingerprint, crons and input names do
  not: `briefing-handoff-watchdog.yml`, `kr-paper-runtime-daily-publish.yml`,
  `upbit-microstructure-capture.yml`, `upbit-universe-capture.yml`,
  `upbit-realtime-capture.yml`. These five move to `drift_compatible` and keep
  working.

Merging #815 as-is would therefore have permanently and silently ended
catch-up for two slots, with CI fully green -- the repository has no test that
knows the fingerprint exists. A hold is recorded on #815. **The earlier
version of this entry is what should have caught it, and could not: it froze
one file and said nothing about the other twelve.**

Note also that the PR's own `bash .github/scripts/push_to_default_branch.sh`
lines are *not* what blocks -- a called script is outside the fingerprint by
design ("workflow YAML only, not scripts it calls"). Only the
`DEFAULT_BRANCH:` env line does. The fix for #815 is correspondingly small:
on those two files only, obtain the default branch without naming
`github.event` (`$GITHUB_BASE_REF`/`$GITHUB_REF_NAME`, a literal `main`, or
`gh repo view`), or leave those two files out of the consolidation.

### The guard that enforces it: none, in this repository

The fingerprint lives only in `/etc/atlas-schedule-dispatcher/config.json` on
the server. Nothing in `test/`, in `run_all.py`, or in any workflow knows the
pinned shas, the fingerprint, or even that the dispatcher exists. **No CI
check will fail on a blocking edit to any of these thirteen files.** The
failure surfaces only as a capture that quietly stops being caught up, days
later, when a day of evidence is already missing. Say so plainly rather than
implying a guard exists.

A repo-side guard is possible in principle -- a test that re-implements
`event_ref_fingerprint()` and compares against a committed copy of the 13
expected values -- but it would need the committed copy to be updated in
lockstep with the server, so it trades a silent server-side failure for a
noisy repo-side one. It has not been built, and building it is a decision, not
a cleanup.

### The legitimate way to change a pinned file

1. **First determine whether your change is even blocking.** Grep your diff
   on those files for the regex above. If no matching line is added, removed
   or reordered, and you changed no cron and no `workflow_dispatch:` input
   name, the change is compatible: merge it normally. Most workflow edits are.
2. **If it is blocking**, the edit and the server re-pin must land together,
   and the server change requires explicit user approval (see
   `reference-ubuntu-server-access.md` in CIO memory: "changes need
   approval"). Prepare an approval request naming the file, the new
   `blob_sha`, the new `event_ref_fingerprint`, and any changed `crons` /
   `dispatch_inputs_allowed`. The dispatcher ships a `verify-pins` mode that
   recomputes all of this against `main`; run it after re-pinning.
3. **Until the re-pin lands, do not merge the blocking edit.** There is no
   safe ordering in the other direction: the pin is checked against `main`, so
   the moment the edit reaches `main` the slot begins resolving as
   `skip_pin_drift_blocked`, and every slot resolved that way is gone for good.
4. **If the change is cosmetic, drop it instead.** Two slots of daily
   catch-up is a steep price for a consistency refactor.

### Sub-entry: `stablecoin-capture.yml` is frozen on *every* byte until 2026-09-24

For this one file the rule above is tightened to all bytes, comments included,
because the freeze was ratified on the byte sha rather than on the fingerprint
(`gpt/outputs/CLAUDE_CIO_DAY_CLOSE_20260918.md`: "9/24까지
`stablecoin-capture.yml` 변경 금지 -- 바이트가 지문으로 고정돼 있어 바뀌면
그날 대신 실행이 조용히 차단됨"). Treat a comment-only edit as forbidden here.

**Correction to the previous version of this entry.** It said the 5-day crypto
observation clock "depends on this workflow firing on its own schedule every
day through the freeze window." **That is no longer true, and the truth makes
the freeze matter more, not less.** GitHub's scheduled runs for this workflow
now land 3-6 hours late. The primary cron is `50 5 * * *` and the ratified
crypto runtime cutoff is `available_at <= 07:00:00Z`; observed first scheduled
run of the day:

| date | first scheduled run created | before the 07:00:00Z cut? |
|---|---|---|
| 2026-09-11 | 06:37:48Z | yes |
| 2026-09-12 | 06:35:41Z | yes |
| 2026-09-13 | 06:39:18Z | yes |
| 2026-09-14 | 10:59:13Z | **no** |
| 2026-09-15 | 10:28:29Z | **no** |
| 2026-09-16 | 10:18:49Z | **no** |
| 2026-09-17 | 10:27:00Z | **no** |
| 2026-09-18 | 10:03:27Z | **no** |

Since 2026-09-14 **not one scheduled run has landed before the cut.** On
2026-09-18 the only pre-cut capture was the dispatcher's own
`workflow_dispatch` at 06:31:21Z (`available_at` 06:32:10Z). The clock now
depends on the dispatcher's catch-up window for the 05:50Z and 06:20Z slots
(eligible fires 06:25:07Z-06:35:07Z and 06:30:07Z-06:35:07Z; the 07:20Z and
08:20Z crons deliberately have no target, because catching them up would claim
the date with an `available_at` past the cutoff and destroy the day).

So the dependency runs the other way from what the old text said: the GitHub
schedule can no longer cover for a blocked dispatcher, because the schedule
itself now lands ~3.5 hours after the cut. If this file drifts in a blocking
way, the first market-state classification due **2026-09-23 16:00 KST** loses
its day, and the 5-day observation clock restarts from zero.

**The incident:** ongoing as of 2026-09-19 -- a standing freeze, not a single
past event. It is recorded because the freeze date (2026-09-24) is not
self-evident from anything in this repository and the mechanism that enforces
it is not in this repository either.

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
