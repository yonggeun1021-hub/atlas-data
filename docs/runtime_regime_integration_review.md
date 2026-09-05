# Runtime Regime integration review

Task: `runtime-regime-integration-review-01`

Review date: 2026-09-05 KST

Role: independent integrator/reviewer

Reviewed public baseline: `d212a0d882fc41c3a01e8558bc494b90e8281a32`

## Decision

The historical replay mechanism is merged, deterministic, and bounded to
SHADOW replay evidence. It does **not** make P1-COM-05 runtime-ready and does not
complete the P1 WBS row. On the reviewed baseline, `P1_REGIME_DECISION` is
truthfully unavailable to P6-06. A complete coverage packet, a replay that
classifies `RISK_ON`, or a forged claim that normalization/PIT acceptance is
complete must not change that result.

Safe technical wiring may add a validated readiness packet and connect it to the
existing P6-06 slot. Until every CIO gate below is satisfied, the runtime
readiness result and the P6-06 source row must remain unavailable/blocked. Even
after the Regime source becomes available, it supplies evidence only: it cannot
select `CASH_PRIORITY`, `REDUCE_REVIEW`, `HEDGE_REVIEW`, `INVERSE_REVIEW`, or
`NO_ACTION`, because the separate P6 Defensive Action policy remains unratified.

No policy values, thresholds, weights, TTL, PIT acceptance, runtime decision,
Stage, Buy, Action, Order, capital, REAL/live, Production, or Trading authority
are introduced by this review.

## Canonical mandate and limitations

- Current mandate: advance the investment Critical Path by independently
  integrating P1-COM-05 runtime readiness into the P6-06 consumer, fail closed,
  with no policy invention or authority expansion.
- Canonical P1-COM-05 state: `🟡 개발중`. PRs #569 and #575 provide common-v1
  replay and a signed-axis boundary, while market normalization, common
  aggregation v2, freshness/TTL, invalidation, formal PIT acceptance,
  market-kill stress, and runtime consumer wiring remain outstanding.
- Canonical P6-06 state: `🟠 승인대기`. P2-COM-02 Flow is connected; the runtime
  Regime decision and standalone Flow Ledger are unavailable. The separate
  Defensive Action policy is not ratified, so `decision_status=BLOCKED` and all
  action/order authority stays false.
- Exact P1-COM-05 WBS Exit Gate readback: “Ratified v1 values implemented as
  hash-bound policy; bull/bear/sideways/stress PIT replay passes determinism,
  transition and stress tests; current 3/5 Crypto remains UNKNOWN until 5/5.”
- Exact P6-06 WBS Exit Gate readback: “CASH_PRIORITY / REDUCE_REVIEW /
  HEDGE_REVIEW / INVERSE_REVIEW / NO_ACTION eligibility와 위험예산·해제조건을
  deterministic 산출; 자동 주문 0”.
- The P1 row's newer Dependency/State Basis also names common aggregation v2,
  freshness/TTL, invalidation, formal PIT acceptance, market-kill stress, and
  runtime wiring as outstanding. The older `v1` Exit Gate wording and that newer
  dependency statement must be reconciled canonically before availability; this
  review does not choose a version or infer ratification.
- The supplied Doctrine readback and Master Map readback are marked deleted.
  The attached content was used as historical evidence under the current user
  mandate; no irreversible or authority-expanding action was taken.
- Live GitHub API access failed (`api.github.com` unavailable). The supplied
  current open-PR inventory—public #570/#576/#545/#475 and private #150—was
  treated as canonical and left untouched. Exact current PR HEAD/CI states could
  not be independently refreshed.
- The public baseline is locally verified at `origin/main=d212a0d…`; the local
  `main` ref is stale at `8ce655f2…`. The supplied private main is
  `201b451ec9f988110e5c6099a20a2aabd04c9609`; the accessible private clone had
  stale local refs and network refresh was unavailable, so that private SHA is
  canonical-readback evidence, not an independent local verification. No
  private account facts were opened or copied.
- The separate `technical_wiring` worktree was clean at `d212a0d…` when
  inspected. The replay/determinism worktree contained changes only in its
  separately owned replay files; there was no overlap with this review's two
  allowed paths.

## Merged-history evidence

All listed merge commits are ancestors of the reviewed baseline.

| PR | Merge commit | What it proves | What it does not prove |
| --- | --- | --- | --- |
| #569 | `26a1c1342c2eb22431835203539f6d6ecbd756f2` | Hash-bound common-v1 PAPER replay mechanism | Market-specific normalization, PIT acceptance, runtime wiring |
| #575 | `42918a1470b3a2ce1b5ee6301087478c715c7ddd` | Signed-axis normalization boundary | Any signed direction or runtime classification |
| #577 | `0407f7810a9b8d4d410d72c5c49194f2ce91e3ca` | P2-COM-02 Flow source binding into P6-06 | P1 runtime availability or P6 policy ratification |
| #578 | `24854515e45cf03b5c4211529487323032c568b4` | Normalization replay-readiness evidence mechanism | Normalization policy ratification |
| #579 | `d36dbb0b156dd6d5a8b775694d68cc202e3415d7` | KR historical SHADOW replay population | NATURAL evidence or runtime Regime |
| #580 | `d496b6ebb14e20da8feb3f4c99b889580607777d` | US free-axis historical SHADOW population | Complete US 5/5 classification |
| #581 | `245f8ab92d0dd68063d6228bbc09e6d3c683137f` | Combined KR+US SHADOW population | Cross-market score or decision |
| #582 | `cb0bc93b2d1085f44abcc81233552aa21e85eaa2` | Deterministic replay evidence summary | Policy conclusion, WBS completion, or runtime authority |

The merged replay code itself enforces the distinction:

- `regime/combined_shadow_historical_replay.py:154-180` grants only historical
  replay evidence and explicitly denies natural promotion, normalization/TTL/PIT
  ratification, runtime wiring, action, order, capital, REAL, Production, and
  Trading authority.
- `regime/combined_shadow_historical_replay.py:1119-1157` requires the combined
  and per-market `runtime_regime` values to remain `UNKNOWN`, forbids a combined
  score/confidence, and prevents partial US coverage from classifying.
- `regime/deterministic_replay_evidence.py:129-160` declares
  `SHADOW_REPLAY_EVIDENCE_ONLY_NOT_POLICY` and preserves UNKNOWN as a distinct
  state rather than an observed middle value.
- `regime/deterministic_replay_evidence.py:168-216` separates the unratified
  market-specific policy from the common replay-only policy.
- `regime/deterministic_replay_evidence.py:1255-1276` withholds every policy
  conclusion and states that sufficiency, thresholds, stress, hysteresis, and
  replay acceptance require separate CIO ratification.

## Current code-path audit

### P1 decision authority

- `regime/decision_authority.py:223-271`: 5/5 coverage changes the status only
  from `BLOCKED_COVERAGE` to `BLOCKED_POLICY_UNRATIFIED`. Classification and
  replay eligibility stay false; Regime and direction are `UNKNOWN`, confidence
  is null.
- `regime/decision_authority.py:317-320`: common-v1 is explicitly
  `SHADOW_PIT_REPLAY_ONLY_RUNTIME_NOT_WIRED`, PIT acceptance is `NOT_ACCEPTED`,
  and market-kill stress is unratified/not implemented.
- `regime/decision_authority.py:573-592`: common-v1 permits replay only. Market
  normalization, freshness, PIT acceptance, runtime classification/binding,
  strategy, Stage/Buy/Action/Order/capital/Production/Trading remain false.
- `regime/decision_authority.py:720-870`: the replay can deterministically emit a
  replay classification, direction, confidence, and final Regime, but carries
  the replay-only mode, non-acceptance, and closed authority in the same packet.
- `regime/decision_authority.py:912-958`: a non-null registry normalization
  policy is rejected as unimplemented; PIT acceptance and market acceptance must
  retain their blocked canonical state.
- `regime/decision_authority.py:984-1089`: real 5/5 evidence coverage still emits
  no signed direction, normalized value, replay step, Regime, direction, or
  confidence without the separate market policy.

Review finding R1 (bounded, not an authority breach): at
`regime/decision_authority.py:984-995`, a direct caller may supply the optional
in-memory `policy` object instead of loading the canonical binding. The current
implementation still hard-codes every signed direction/value and runtime result
to null/UNKNOWN and every runtime authority to false, so even a forged policy
claim cannot promote a decision. The future runtime-readiness producer must not
treat that optional object or the echoed market-binding fields as ratification;
it must load and independently validate the canonical hash-bound policy itself.

### P6-06 and daily consumer

- `portfolio/defensive_action_decision.py:141-174`: the baseline contract lists
  `P1_REGIME_DECISION` and `P2_FLOW_LEDGER` as unavailable-only source slots.
- `portfolio/defensive_action_decision.py:322-365`: supported sources must pass
  their owning semantic validator, closed-authority checks, market identity, and
  point-in-time bounds before they are marked available.
- `portfolio/defensive_action_decision.py:368-407`: an unavailable-only slot
  rejects any packet instead of accepting a look-alike or self-rehashed payload.
- `portfolio/defensive_action_decision.py:434-459`: every decision stays
  `NOT_EVALUATED`, with `eligible=null`; missing inputs are not `NO_ACTION`.
- `portfolio/defensive_action_decision.py:462-535`: an unratified policy packet is
  forbidden, the output stays `BLOCKED`, and action/allocation/instrument/size are
  null with `order_intents=[]`.
- `briefing/daily_orchestrator.py:1695-1708`: current live factor evidence builds
  an UNKNOWN Regime output only; it does not call the replay classifier.
- `briefing/daily_orchestrator.py:2686-2710`: P2-COM-02 is independently rebuilt
  and supplied as diagnostic evidence, not a defensive decision.
- `briefing/daily_orchestrator.py:2724-2766`: the orchestrator converts validated
  component rows into the exact P6 source bundle and surfaces a pending,
  policy-not-ratified readiness row.
- `briefing/daily_orchestrator.py:3092-3095,3135-3149`: UNKNOWN Regime outputs feed
  only cash/inverse invariants; P2 Flow is built before P6-06, while no P1 runtime
  decision producer is currently inserted.

This path is semantically correct on the baseline. The integration gap is the
absence of a separately validated runtime-readiness producer and its exact P1
source adapter—not a reason to relabel replay output as runtime evidence.

## Readiness contract for the technical-wiring head

The technical-wiring implementation is acceptable only if it observes all of
the following without adding parameter values.

1. **Input identity:** consume the current live per-market `regime_output/v1`
   source packet and its coverage result at the decision instant. Historical
   populations, deterministic replay reports, normalization-readiness reports,
   and common-v1 replay packets are forbidden substitutes.
2. **Independent derivation:** validate source bytes, lineage, market identity,
   observation dates, `available_at`, and packet hash through owning validators.
   A re-signed derived packet or direct function call cannot bypass re-derivation.
3. **Truthful state:** incomplete/unavailable evidence yields `UNKNOWN` and an
   unavailable/blocked readiness state. It never yields `NEUTRAL`, `NO_ACTION`, a
   carried-forward prior decision, or a synthetic signed axis.
4. **Policy separation:** a policy-presence claim is not ratification. The
   readiness validator must bind exact canonical policy identity and reject an
   injected dict, edited registry, replay success, or boolean alias as proof.
5. **Consumer separation:** P6-06 may mark `P1_REGIME_DECISION` available only for
   the exact supported runtime packet contract. Availability supplies evidence;
   it must not change P6 `decision_status=BLOCKED`, `eligible=null`,
   `selected_action=null`, `action_proposal=null`, or `order_intents=[]` while
   the P6 policy is unratified.
6. **Authority closure:** readiness/validation may be true; classification,
   strategy, Stage, Buy, Action, Order, capital, REAL/live, Production, Trading,
   broker, and account-write authority remain false.

## Availability transition conditions and exact CIO gates

`P1_REGIME_DECISION` must remain `UNAVAILABLE` unless **all** gates below are
proved by exact canonical, hash-bound inputs. This list names required decisions;
it supplies no threshold, weight, TTL, or acceptance value.

| Gate | Required proof | Current baseline |
| --- | --- | --- |
| G1 source/lineage | Exact market packet, owning validator, source timestamps and PIT ordering valid | Mechanism exists; runtime final packet absent |
| G2 coverage | All five required axes are valid under the canonical coverage contract | 5/5 can be observed, but is not sufficient |
| G3 market normalization | Per-market signed normalization policy is explicitly CIO-ratified, implemented, hash-bound, and applied | Unratified/absent |
| G4 freshness/TTL | Exact freshness and TTL policy is CIO-ratified and every axis satisfies it at `decision_at` | Unratified |
| G5 aggregation/classification | Runtime common aggregation v2, classification and direction policy are CIO-ratified and implemented | Outstanding; common v1 is replay-only |
| G6 confidence/override | Confidence and stress/market-kill override semantics are CIO-ratified and implemented | Outstanding |
| G7 invalidation/hysteresis | Invalidation and runtime hysteresis semantics are CIO-ratified and implemented | Outstanding |
| G8 PIT/replay acceptance | Formal CIO PIT acceptance exists for the exact implemented policy and the bull/bear/sideways/stress/transition replay suite passes deterministically | `NOT_ACCEPTED` |
| G9 runtime validator | Exact runtime packet is independently re-derived; tamper, fake completion and direct-call bypass tests pass | Producer absent |
| G10 consumer wiring | Daily orchestrator supplies that exact validated packet to P6-06 with aligned market/time identity | Absent |
| G11 P6 separation | P6 policy remains a separate ratification and all action/order fields stay closed | Preserved on baseline |

Transition rules:

- `UNAVAILABLE -> AVAILABLE` is allowed only when G1-G10 all pass on the exact
  decision-time inputs. A replay PASS or 5/5 coverage alone is never a trigger.
- Any failed, missing, stale, future-dated, unratified, hash-mismatched, or
  semantically invalid gate keeps or immediately returns the source to
  `UNAVAILABLE`; last-known output must not be silently reused as current.
- `UNKNOWN` remains `UNKNOWN`. It is not an availability shortcut, `NEUTRAL`, or
  `NO_ACTION`.
- P1 availability does not satisfy G11 and cannot select or propose a defensive
  action. Separate CIO ratification and evidence are still required.
- Code merge/green CI proves the mechanism only. P1 WBS completion still
  requires canonical evidence, formal PIT acceptance, consumer observation, and
  the canonical Exit Gate; no status change is authorized by this review.

## Independent integration tests

`test/test_runtime_regime_integration_contract.py` is the baseline executable
contract:

- lines 128-173: 5/5 coverage and signed-normalization anti-promotion;
- lines 143-155: a fully classifying replay remains replay-only and unaccepted;
- lines 175-219: forged in-memory and registry normalization/PIT claims cannot
  create a runtime output;
- lines 221-238: P6 stays blocked and cannot infer `NO_ACTION`;
- lines 240-267: decision-authority, normalization, and replay packets are all
  rejected from the baseline P1 runtime slot;
- lines 269-292: the daily consumer preserves P1 unavailable, blocked decision,
  null action, and empty order intents.

## Baseline dry-run

Executed on `d212a0d882fc41c3a01e8558bc494b90e8281a32` before any Claude technical-wiring
change:

| Command | Result |
| --- | --- |
| `python3 test/test_runtime_regime_integration_contract.py` | **8/8 PASS** |
| `python3 test/test_daily_orchestrator.py DailyOrchestratorTest.test_defensive_and_strategic_readiness_are_wired_fail_closed` | **1/1 PASS** |
| `python3 test/test_regime_decision_authority.py` | 38 PASS, 1 FAIL, 1 ERROR because sparse-checkout omitted tracked `data/observations/us_natural_finished_session/2026-09-01/receipt.json` |
| `python3 test/test_defensive_action_decision.py` | Import-time blocked because sparse-checkout omitted tracked predecessor `evidence/portfolio/cross_market_flow_transition_ledger/2026-09-02/58f34d06c92d66d96d64a0deb0261462aaae06a4ac99da7c43d4d2cfc35161cf/packet.json` |

Both missing files exist in the reviewed Git tree and carry the sparse-index
`S` flag; these are worktree materialization limitations, not a reason to waive
the suites. Root should run adjacent regressions in a full checkout.

## Focused and adjacent regression plan

On the exact Claude head, and again after integration with this review head:

1. `python3 test/test_runtime_regime_integration_contract.py`
2. `python3 test/test_runtime_regime_readiness.py`
3. `python3 test/test_regime_decision_authority.py`
4. `python3 test/test_defensive_action_decision.py`
5. `python3 test/test_daily_orchestrator.py`
6. `python3 test/test_action_risk_portfolio_summary.py`
7. `python3 test/test_strategic_capital_posture.py`
8. `python3 test/test_combined_shadow_historical_replay.py`
9. `python3 test/test_deterministic_replay_evidence.py`
10. Repository authoritative runner and `git diff --check` in a full checkout.

The integration review must compare exact Claude HEAD/tree and inspect every
Claude-owned diff. Required negative cases are: fake 5/5 coverage; replay PASS;
forged policy/acceptance; re-signed tamper; future/stale inputs; missing axis;
UNKNOWN-to-NEUTRAL or UNKNOWN-to-NO_ACTION promotion; unsupported packet type;
and any authority field changed to true.

## Operational owner and handoff

- `.github/workflows/paper-regime-reference.yml:5-15,37-54` refreshes and verifies
  the PAPER reference/Flow evidence after source workflows and at 10:20/10:50
  KST. It does not ratify runtime Regime policy.
- `.github/workflows/cross-market-flow-transition-ledger.yml:15-19,60-73`
  independently appends the natural P2 transition observation at 11:05 KST and
  preserves manual/recovery/replay labels.
- `.github/workflows/daily-briefing.yml:3-23,62-82` is the scheduled runtime
  consumer at 07:05 KST daily and 18:30 KST weekdays, with offline regressions.
- The existing 22-hour Codex automation is read-only reporting and cannot create
  evidence or change WBS state.
- PM/root is the single accountable owner that must consume success or failure
  into the existing canonical rows, in `Tracker -> Cockpit -> Master Map` order.
  This review changes no canonical row.

Next executable step: root supplies the exact Claude technical-wiring head;
independent review then verifies the diff against this contract, runs the full
focused/adjacent matrix in a materialized checkout, and reports mechanism
readiness separately from WBS/operational completion.
