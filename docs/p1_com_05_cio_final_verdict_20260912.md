# P1-COM-05 CIO Final Verdict — 2026-09-12

This document is the ratification record for the decision identity
`CIO-P1-COM-05-NORMALIZATION-FRESHNESS-PIT-FINAL-V1-2026-09-12`. It is a
verbatim transcription of the CIO decision delivered in chat on 2026-09-12,
following two prior investigation packets:
`CIO_REGIME_RATIFICATION_PACKET` (this session, earlier) and
`CIO_REGIME_FINAL_TWO_GATES_PACKET` (this session, earlier).

It is a **separate, additional** ratification. It does not amend, supersede,
or retroactively expand `config/regime_source_owner_registry_v2.json`'s own
decision (`CIO-GATE2-3MARKET-REGIME-SOURCE-FIRST-B-2026-09-01`,
`OPTION_B_SOURCE_FIRST_LAYERED_RATIFICATION`, architecture-scope only). That
registry's `forbidden_promotions` (`SIGNED_NORMALIZATION_RATIFICATION`,
`TTL_OR_FRESHNESS_RATIFICATION`) remain intact and unedited — they name
promotions that decision may not make on its own; they do not forbid a
distinct, later CIO decision from ratifying the same policy domains under its
own identity, which is what this document records.

## Verbatim CIO verdict

> CIO 판정:
>
> * US/KR signed-axis normalization 표의 후보값을 `PAPER_RUNTIME_NORMALIZATION_V1`으로 비준한다.
> * Crypto normalization은 비준하지 않는다. 계속 UNKNOWN.
> * common v1 aggregation/classification/direction/hysteresis는 기존 비준값 그대로 재사용한다.
> * missing/invalid/stale required axis → 즉시 UNKNOWN.
> * 일반 전이 2 finalized packets, STRESS 진입 즉시, STRESS 해제 non-stress 2회 + S>-3.
> * 별도 market-kill override는 이번 v1에서 추가하지 않는다. RISK_VOL=STRESS만 사용한다.
>
> 단 `runtime_decision_available`은 아직 열지 마.
>
> [... G4 Freshness / G8 PIT investigation instructions ...]

> CIO FINAL P1 POLICY VERDICT.
>
> G4 Freshness — RATIFIED
> numeric seconds TTL은 사용하지 않는다.
> source-frequency semantic freshness를 비준한다.
>
> US session-based axes:
> * TREND / BREADTH / LEADERSHIP
> * latest officially completed US session exact match required
> * expected completed session이 존재하는데 observation이 이전 session이면 UNKNOWN / SOURCE_NOT_ADVANCED
> * arbitrary carry/substitution 금지
>
> Korea:
> * 5 axes 모두 latest officially completed KRX session exact match
> * existing 18:00 KST same-session usability gate 유지
> * stale/prior-session substitution 금지
>
> US release-based:
> * VIXCLS = daily FRED publication semantics
> * WRESBAL / TOTBKCR = weekly FRED publication semantics
> * successful current fetch + retained source/hash validation 필요
> * weekly unchanged observation은 정상
> * ETF session date로 coerce 금지
>
> missing / failed / calendar unknown / expected-source-not-advanced는 즉시 UNKNOWN.
>
> G8 PIT — acceptance contract RATIFIED, evidence NOT ACCEPTED
> 기존 `regime_replay_harness/v1`의 3-market contract는 변경하지 마.
> 별도의 market-scoped runtime PIT acceptance를 만든다.
> US / KR / CRYPTO 각각 독립 상태를 갖는다.
> Crypto 미완이 US/KR acceptance를 막지 않는다.
>
> 각 market의 PIT_ACCEPTED 조건:
> 1. REAL historical evidence only — synthetic fixture credit 0
> 2. required 5/5 axes
> 3. no-lookahead/PIT validation PASS
> 4. deterministic rerun byte-identical
> 5. ratified classification + hysteresis exact replay
> 6. 실제 historical sequence에서 RISK_ON / NEUTRAL / RISK_OFF / STRESS 각 최소 1 episode 존재
>
> episode 날짜를 policy에 임의로 박지 마.
> classifier output을 보고 좋은 기간만 cherry-pick하는 것도 금지.
>
> 구현할 첫 PR
> current main에서 bounded P1 runtime policy implementation PR을 만들어.
> 포함:
> * CIO-ratified US/KR normalization policy identity
> * CIO-ratified semantic freshness policy
> * 기존 common-v1 classification/direction/hysteresis exact reuse
> * market-scoped PIT acceptance contract + validator
> * initial acceptance status는 US/KR/CRYPTO 모두 NOT_ACCEPTED
> * runtime readiness가 정확히 `POLICY_READY_PIT_EVIDENCE_PENDING`류 상태를 표현하도록 연결
>
> 포함하지 말 것:
> * `runtime_decision_available=true`
> * historical episode를 synthetic 생성
> * Crypto normalization
> * action/capital/order/trading authority
> * P6/P7/VCA 수정
>
> 기존 3-market replay harness는 약화/변경하지 마.
> focused tests에서 반드시:
> * session source old-date reject
> * weekly FRED unchanged current-fetch permit
> * market-scoped US acceptance가 Crypto 상태와 독립적임
> * synthetic episode acceptance credit=0
> * missing one required regime episode → NOT_ACCEPTED
> * authority false
> 를 검증해.
>
> 구현 → authoritative/full CI → PR 생성 → exact-head receipt 후 STOP.
> merge는 하지 마.
>
> 그와 동시에 다음 단계용으로, 기존 historical replay population capability가 실제
> US/KR 각각 어느 기간까지 real PIT history를 재구성 가능한지만 evidence inventory로
> 남겨. 아직 episode selection이나 acceptance 승격은 하지 마.

## Ratification scope (this document ratifies exactly this, nothing more)

1. `PAPER_RUNTIME_NORMALIZATION_V1` — the per-market signed-axis normalization
   method and thresholds already present, byte-for-byte unchanged, in
   `config/paper_regime_reference_policy_v1.json`'s `markets.US` and
   `markets.KR` blocks, for US and KR only. No number in that block is
   invented or altered by this ratification; it is ratified exactly as it
   already existed as a PM candidate.
2. Crypto signed-axis normalization: explicitly **not** ratified. Crypto stays
   `UNKNOWN`.
3. Common v1 aggregation/classification/direction/hysteresis: the existing
   `RATIFIED_PAPER_BASELINE_V1` values in
   `config/regime_source_owner_registry_v2.json`'s `common_v1_alignment` are
   reused exactly, unchanged. No new number is introduced here.
4. `G4` freshness: **source-frequency semantic freshness**, not a numeric TTL.
   Session-based axes require an exact match to the latest officially
   completed session; release-based axes (US `VIXCLS` daily, `WRESBAL`/
   `TOTBKCR` weekly) require the latest successfully fetched, hash-retained
   publication, permitting an unchanged weekly value. Missing/failed/
   calendar-unknown/not-advanced evidence is UNKNOWN immediately.
5. `G8` PIT: a **market-scoped runtime PIT acceptance contract** is ratified
   (the rules below), independent per market (US/KR/CRYPTO). The **evidence**
   for every market starts, and today remains, `NOT_ACCEPTED`. This
   ratification does not accept any market's evidence; it only ratifies the
   acceptance rule and opens the validator.
6. `runtime_decision_available` stays `False`. This document opens no
   authority: no action, capital, order, trading, Stage, Buy, Production, or
   real authority is authorized by anything in this document.

## What this document does not do

It does not modify `config/regime_replay_harness_contract.json` or
`regime/replay_harness.py` (the existing 3-market `regime_replay_harness/v1`
contract). It does not touch `portfolio/defensive_action_decision.py` (P6-06),
`portfolio/strategic_capital_posture.py` (P7-12), or Capital
Allocation/VCA. It does not select, invent, or commit any historical
bull/bear/sideways/stress episode date.
