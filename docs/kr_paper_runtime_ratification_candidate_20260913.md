# KR PAPER runtime Regime ratification candidate — 2026-09-13

## Source-aware bridge update

The natural 2026-09-11 KRX Information System artifact now retains and
revalidates all eight provider response bodies. The explicit runtime branch
recomputes all five measurements from those bodies, verifies the accepted
28-session replay through 2026-09-10, then appends the natural session to the
unchanged common-v1 replay. The review result is confirmed `NEUTRAL`, direction
`DETERIORATING`; the current raw observation is `RISK_OFF` score `-4` and stays
pending at confirmation `1/2`. Leadership is immediately displayable as
negative, with 17 positive sectors among 46.

The committed qualification remains `PENDING_CIO_RATIFICATION`, so this branch
cannot open the display before independent review and a separate exact-byte
ratification receipt. Strategy, Stage, buy, capital, order, production,
trading, and REAL authority remain false in every result.

Status: `DRAFT_NOT_RATIFIED`

This packet prepares a bounded CIO technical decision. It does not accept the
historical evidence, enable runtime classification, merge either prerequisite
PR, or authorize a strategy, Stage, Buy, Action, capital movement, order,
Production, trading, or REAL account use.

## User outcome

When the latest officially completed KRX session has a genuine, complete,
fresh five-axis packet, PAPER may display the KR market Regime produced by the
already-ratified KR normalization and unchanged common-v1
classification/direction/hysteresis. Missing, failed, calendar-unknown, or
not-advanced input remains `UNKNOWN` immediately.

This is a market context result only. It is not the `15m` / `1h` / `1d` entry
proposal bar gate and it does not make a candidate or an order eligible.

## Changed facts since the 2026-09-12 STOP decision

| Prior blocking premise | Current independently reproducible evidence | Result |
| --- | --- | --- |
| No real KR historical 5/5 bundle retained | Official-calendar contiguous 2026-08-03 through 2026-09-10 run produced 28 requested, 28 observed, 28 complete 5/5, 0 blocked | Satisfied for the candidate decision |
| Market-scoped PIT evidence `NOT_ACCEPTED` | The unchanged validator returns `PIT_ACCEPTED` | Satisfied for the candidate decision |
| Four real confirmed regimes not all proven | The full preselected range produced NEUTRAL, RISK_OFF, RISK_ON, and STRESS | Satisfied |
| No-lookahead and deterministic rerun unproven | Population verification and byte-identical rerun passed | Satisfied |
| Runtime binding and Regime result not ratified | No later CIO adoption identity exists | Still pending |
| Latest completed session input exactness | Committed main points to 2026-09-10; official KRX calendar makes 2026-09-11 the latest completed session at this review time | Still blocked: `SOURCE_NOT_ADVANCED_EXPECTED_SESSION` |

The PIT label proves that the historical evidence satisfies the six ratified
acceptance conditions. It does not itself set `authority.evidence_accepted`,
`runtime_binding_authorized`, or `regime_result_ratification_authorized`.

## Recommended two-PR merge order

### A. Evidence adoption

Dependency: PR #689, exact head
`2b1e8204ecb35e3a5c9b08963c7bd76021e9325a`, CI run `34715669049`
SUCCESS.

1. Merge PR #689 after its current-main merge result is rechecked.
2. Retain the exact historical population, replay, status, and manifest under
   one immutable KR evidence directory. Do not retain only an expiring Actions
   artifact.
3. Add one KR evidence-binding contract containing the population payload,
   replay report, file hashes, source run, implementation head, and acceptance
   contract hash.
4. Make `runtime_regime_readiness` rebuild the committed market-scoped status
   from that exact bound KR bundle. Its KR label may then become
   `PIT_ACCEPTED_RUNTIME_DECISION_STILL_CLOSED`; every runtime and downstream
   authority remains false.

This PR accepts evidence only after the CIO approves its exact binding. It
does not emit a Regime.

### B. KR PAPER runtime adoption

Dependency: A merged plus a genuine packet for the latest officially
completed KRX session.

1. Add one KR-only adoption identity binding the exact evidence contract,
   `paper_runtime_normalization_v1`, `regime_semantic_freshness_policy/v1`, and
   the existing common-v1 policy bytes.
2. Reuse `regime.kr_paper_runtime.evaluate_kr_paper_runtime`; do not add a new
   score, weight, threshold, override, confidence rule, or hysteresis rule.
3. Require the live source packet date to equal the last officially completed
   KRX session derived from the committed official calendar. On 2026-09-13,
   that is 2026-09-11. Wall-clock date subtraction and prior-session carry are
   forbidden.
4. Require the existing 18:00 KST usability gate, exact source-byte and owner
   receipt bindings, a complete admitted source chain, and common-v1
   confirmation.
5. Publish only a read-only KR PAPER Regime reference. Do not connect it to
   Candidate, Stage, P6/P7, Entry, broker, ledger, or order consumers in this
   adoption.
6. US and Crypto stay `UNKNOWN` and independently blocked.

## Minimal production file scope after CIO ratification

- `config/kr_market_scoped_pit_evidence_binding_v1.json` — immutable accepted
  evidence identity.
- `data/accepted_regime_history/kr/...` — population, replay, status, and
  manifest exact bytes.
- `data/latest_market_scoped_pit_acceptance.json` — rederived KR
  `PIT_ACCEPTED`, US/Crypto unchanged.
- `regime/runtime_regime_readiness.py` — verify and consume the exact bound KR
  bundle; no classification in evidence PR A.
- `config/kr_paper_runtime_adoption_v1.json` — separate CIO runtime/result
  authority identity for PR B.
- One daily read-only producer that supplies existing `kr_paper_runtime` with
  the official latest-session boundary and externally pinned policy/source
  receipts.
- Focused validator and negative-path tests. Existing P6/P7, candidate,
  entry, broker, ledger, and order files are outside scope.

## Mandatory negative tests

- 27/28, one missing axis, one missing regime, synthetic evidence, future
  observation, hash tamper, response-lineage tamper, or non-deterministic
  replay cannot be adopted.
- A self-rehashed forged `PIT_ACCEPTED` pointer fails without the bound
  population bytes.
- 2026-09-10 live input fails on a 2026-09-11 expected session; weekends do
  not make 2026-09-13 the expected session.
- Missing official calendar, a calendar marked UNKNOWN, before-18:00 KST use,
  missing common-v1 confirmation, stale or substituted source, and policy hash
  drift all return `UNKNOWN`.
- KR readiness cannot change US or Crypto.
- KR PAPER Regime cannot fill any Candidate, Stage, Buy, Action, capital,
  Order, Production, Trading, or REAL field.

## Current decision

The historical prerequisites are ready for CIO evidence-adoption review. The
runtime adoption remains blocked on both a separate CIO identity and an exact
latest-session live packet. No seven-day wait is required: the source has
already shown historical availability, so the actionable next data step is a
dedicated 2026-09-11 five-axis capture/retention or a later exact expected
session, followed by the same validators.

Open PR #609 overlaps the three policy/status files but also contains broad
unrelated flow and daily-data changes from an old base. It is not the owner of
this bounded KR adoption. Do not combine or cherry-pick its broad diff; split
or recollect only the exact latest KR source packet through the owning source
workflow.
