# US PAPER runtime producer — U4 design draft (2026-09-14)

Base: `main` at clone time (see PR for the exact head SHA). Scope: CIO
"U4 draft" item from the US/Crypto regime gap diagnosis
(`CLAUDE_CIO_US_CRYPTO_REGIME_GAP_DIAGNOSIS_20260914.md`, §3/§4, US row 4).
This document explains `regime/us_paper_runtime.py` and its test file
`test/test_us_paper_runtime.py`. It is **not** an implementation status
report for U1/U2/U3/U5/U6 — those remain exactly where the diagnosis left
them.

## What this is

A design draft that mirrors `regime/kr_paper_runtime.py` /
`regime/kr_information_system_runtime_bridge.py` (the KR "PR #696 pattern")
structurally, for US. It is explicitly **not** a fully wired production
producer:

- No US session calendar module exists in this repository (KR has
  `market_data/krx_session_calendar_v2.py`, independently verified against a
  committed calendar contract). This draft accepts
  `latest_completed_session_date` as a caller-attested parameter instead of
  independently deriving it. Closing that gap is separate follow-up work, not
  performed here.
- No publication/schedule step is added. The diagnosis's U4 row also asks for
  "schedule after `free-market-data.yml` (21:35Z Sun-Fri)" — that workflow
  wiring is out of scope for this draft; only the calculation function exists.
- U3 (a real US PIT-accepted evidence bundle) and U5 (the
  `US_PAPER_RUNTIME_ADOPTION_V1` identity) are **not** performed by this PR.
  Both are explicitly out of scope, per the CIO's own ordering in the
  diagnosis and per this task's instructions. This agent did not run, queue,
  or dispatch any Alpaca/FRED collection, and authored no ratification
  document.

## Why this can only ever emit `runtime_regime: "UNKNOWN"` today

Three independent facts, each backed by real code this module actually calls
(not a comment-only promise):

### 1. `US_PIT_ACCEPTED_BUNDLE_AVAILABLE` — sourced from the real acceptance gate

`evaluate_us_paper_runtime` calls the existing, unmodified
`regime.market_scoped_pit_acceptance.evaluate_market_pit_acceptance("US",
pit_acceptance_bundle)` on every invocation and stores its `status ==
"PIT_ACCEPTED"` result in the output packet's
`us_pit_accepted_bundle_available` field. This is not a hardcoded stub: it is
whatever that real function actually returns for whatever bundle the caller
supplies.

As of this draft, that call can never return `PIT_ACCEPTED` for US using real
evidence, because of a fact about a *different*, already-existing module:
`regime/us_historical_replay_population.py` computes only three of five
required axes (`TREND`, `RISK_VOL`, `LIQUIDITY`; see its own module
docstring, lines 10-31). `BREADTH` and `LEADERSHIP` are never populated,
because doing so would be a new ratification (U2,
`US_ETF_PROXY_HISTORICAL_PIT_SCOPE_V1`) that has not happened. Condition 2 of
`market_scoped_pit_acceptance`'s acceptance rule ("Required 5-of-5 axes per
evaluated date; an incomplete date is excluded, never substituted") can
therefore never be satisfied by a real US population bundle today — this
producer does not special-case that; it is a structural consequence of
composing two already-existing, unmodified modules.

`test_no_bundle_is_not_accepted_and_stays_unknown` and
`test_fake_schema_bundle_is_rejected_same_as_no_bundle` exercise this gate
directly against the real `market_scoped_pit_acceptance` module (no mocking).

### 2. `US_PAPER_RUNTIME_ADOPTION_RATIFIED` — sourced from a real, currently-absent file

`_us_runtime_adoption_ratified()` reads
`config/us_paper_runtime_adoption_v1.json` (the U5 identity) fresh on every
call, exactly like the existing
`market_scoped_pit_acceptance.normalization_and_freshness_ratified` helper
reads its two identity files, and fails closed to `False` on any missing
file or structural mismatch. That file does not exist in this repository
today, so the function returns `False` — but the check is wired to real file
state, not a Python literal: if a future PR lands a genuinely ratified U5
identity at that path, this function's result changes without touching this
module's code.

`test_u5_identity_file_absent_today` asserts this against the real repository
state (no mocking): `_us_runtime_adoption_ratified()` is `False` and the file
does not exist.

Both gates are required (`require(pit_ok, ...)`, `require(adoption_ok,
...)`) before any axis aggregation is attempted. Either one being false
(both are false today) short-circuits to `runtime_regime: "UNKNOWN"`,
`runtime_decision_available: False`, with a single reason code identifying
which gate failed.

### 3. `runtime_regime` has exactly one assignment in the whole module — literally `"UNKNOWN"`

Even setting aside gates 1 and 2, the packet skeleton
(`_skeleton()`) sets `"runtime_regime": "UNKNOWN"` once, and no other line in
`evaluate_us_paper_runtime` ever reassigns that key. When both gates
hypothetically pass (exercised in tests by mocking gate 2 and constructing a
synthetic bundle that satisfies gate 1's real provenance check), the common-v1
aggregation result is published only as `paper_regime` — the PAPER-simulation
treatment the KR bridge already gives `SYNTHETIC_OFFLINE_FIXTURE` /
`HISTORICAL_REPLAY` evidence — never promoted to `runtime_regime`.

`test_both_gates_true_still_cannot_promote_runtime_regime` is the load-bearing
test for this claim: it supplies a synthetic bundle spanning all four
required regimes (so `market_scoped_pit_acceptance` genuinely returns
`PIT_ACCEPTED`), mocks `_us_runtime_adoption_ratified` to `True`, and a fully
valid, in-vintage, session-matched live packet. The aggregation legitimately
classifies `STRESS` and that value lands in `paper_regime` —
`aggregation["final_regime"]` and `paper_regime` agree — while `runtime_regime`
is still exactly `"UNKNOWN"` and `runtime_decision_available` is `False`.
`test_no_assignment_of_runtime_regime_other_than_unknown_in_source` is a
second, source-level check: every literal assignment of the
`"runtime_regime"` packet key in `regime/us_paper_runtime.py` is grepped and
asserted to be the string `"UNKNOWN"`.

Promoting `paper_regime` to `runtime_regime` under `LIVE_NATURAL` evidence
class, once real gates 1 and 2 both genuinely pass, is exactly the one
additional step a real U5 ratification would authorize (mirroring
`kr_paper_runtime.evaluate_kr_paper_runtime`'s `LIVE_NATURAL` branch, which
does promote). This draft intentionally does not wire that promotion.

## What was reused vs. adapted from the KR bridge

| KR pattern | This draft |
| --- | --- |
| `evaluate_market_pit_acceptance` gate (didn't exist per-market in KR bridge itself, but is the same acceptance module US and KR both feed) | Reused unmodified, called with `market="US"`. |
| `COMMON.replay_common_v1` / `COMMON.load_common_v1_policy` common-v1 aggregation, hysteresis, STRESS-immediate-entry | Reused unmodified. The historical sequence is extended with one appended live step and replayed exactly once per call, so hysteresis state carries over the same way KR's 28-step replay did. |
| `paper_regime_reference.normalize_kr_measurements` / `build_kr` axis arithmetic reuse | Adapted to `paper_regime_reference.build_us`, called with the exact `us_market_reference` / `fred` / `fred_liquidity` field names `build_us` already expects — no field name invented. |
| KR session-boundary trust-anchor contract (independently verified calendar proves D immediately precedes E) | **Not reused as-is.** No NYSE session calendar module exists yet; this draft accepts a caller-attested `latest_completed_session_date` instead. Documented limitation, not hidden. |
| FRED ALFRED vintage pin (`us_historical_replay_population._assert_vintage_covers`) | **Not imported** (explicit instruction: this producer is structurally parallel to, not dependent on, that historical-replay module, which had an unmerged sibling PR at draft time). Reimplemented locally as `_fred_vintage_covers`, same containment semantics (window must contain the anchor date; lookahead vs. superseded are distinct, separately coded failures). |
| `kr_information_system_runtime_bridge`'s qualification-file / hash-pinned evidence chain (`RATIFIED_KR_PAPER_DISPLAY_ONLY`, `_manifest_payload_sha256`, retained-byte re-derivation of every axis from raw provider responses) | **Not reused.** That machinery exists because KR already has a real natural evidence source to bind byte-for-byte. US has none yet (that is exactly U3), so there is nothing to bind against; adding that scaffolding now would be speculative. When U3 lands, a follow-up PR should build the equivalent US evidence-chain validator against the real bundle shape U3 actually produces, not one guessed here. |
| `market_scoped_pit_acceptance.normalization_and_freshness_ratified` (file-backed, fail-closed-to-False identity check) | Reused as the *pattern* for gate 2 (`_us_runtime_adoption_ratified`), pointed at the not-yet-existing U5 identity path instead of the two already-ratified identities that function checks. |
| `validate_kr_paper_runtime` rederivation check | Mirrored as `validate_us_paper_runtime` — rebuild from inputs, compare canonical bytes, reject any tamper including a rehashed `decision_id`. |
| KR bridge's `reduce_funnel_with_kr_regime` consumer wiring | **Not added.** `runtime_decision_available` is unconditionally `False` in this draft, so a funnel consumer would always reject with `REGIME_NOT_AVAILABLE`; wiring that dead path now adds surface area with no behavior to test. |

## Input shape

`evaluate_us_paper_runtime` takes `current_source_packet` shaped exactly like
`data/latest_free_market_data.json` (verified against the live file's actual
keys during this draft, not assumed): `fred.{value,realtime_start,
realtime_end}`, `fred_liquidity.series[].{series_id,change,realtime_start,
realtime_end}`, `us_market_reference.{status,as_of_session_date,trend_etfs,
proxy_axes.{BREADTH,LEADERSHIP}}` — the identical shape
`paper_regime_reference.build_us` already consumes, reused unmodified rather
than re-implemented.

`pit_acceptance_bundle` must be the real, unmodified output of
`regime.us_historical_replay_population.build_population` — this producer
never trusts a caller's claim about it; it hands the object byte-for-byte to
`market_scoped_pit_acceptance.evaluate_market_pit_acceptance`, which itself
requires an exact `schema_version`/`mode`/`wbs` match against that population
module's own constants before extracting any record.

## Freshness

`config/regime_semantic_freshness_policy_v1.json`'s existing, already-ratified
US block is read and required `RATIFIED` before any axis is trusted (not
reinvented: no new numeric TTL is introduced anywhere in this module).
`TREND`/`BREADTH`/`LEADERSHIP` use that policy's `SESSION_EXACT_MATCH` form
(`live_session_date == latest_completed_session_date`, no carry/substitution
— a stale-but-present session fails closed to UNKNOWN via
`SOURCE_NOT_ADVANCED_EXPECTED_SESSION`, the exact reason code the policy
names). `RISK_VOL` (VIX) and `LIQUIDITY` (WRESBAL/TOTBKCR) use its
`RELEASE_CYCLE_LATEST_FETCH` form: `session_date_coercion` is `FORBIDDEN`, so
these are bound against the evaluation date via ALFRED vintage containment,
never coerced to the ETF session date.

## Authority

Every authority flag is `False` unconditionally except
`us_paper_experiment_calculation_only`, which is always `True` — this module
performs a PAPER calculation only. `test_authority_is_all_false_except_the_
calculation_only_marker` checks this on both the always-blocked path (no
inputs supplied) and the fully-gate-mocked, fully-computed path.

## What this draft explicitly does not decide

- Whether the historical population's per-record `no_lookahead_attestation`
  and `candidate_normalized_result.axes` shape (reused here via
  `market_scoped_pit_acceptance._real_evidence_bundle` /
  `_build_sequence`, both private helpers of that already-existing module,
  reused rather than reimplemented — the same convention
  `kr_paper_runtime.py` already uses for `kr_internal_paper_theme_
  application`'s private helpers) will still be the real shape once U1/U2
  actually populate BREADTH/LEADERSHIP for US. If that work changes the
  bundle's record shape, this module's `_live_axis_directions` and the
  historical-sequence join would need revisiting — deliberately left to that
  follow-up rather than guessed at here.
- U5's actual contents (`US_PAPER_RUNTIME_ADOPTION_V1`: bundle hash binding,
  `paper_runtime_normalization_v1.json` US block hash, freshness policy hash,
  common-v1 binding, `LIVE_NATURAL` evidence class, expected-session freshness
  — per the diagnosis's U5 row). This draft only reads
  `config/us_paper_runtime_adoption_v1.json` if and when it exists; it does
  not author it, propose its exact schema, or pre-commit to one.
- A NYSE session calendar module. `latest_completed_session_date` stays an
  explicit, documented, caller-trusted input until that gap is separately
  closed.
