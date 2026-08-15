# B1 reviewed decomposition — 46칸 전체 결과

**상태: inactive preparation** · authority=False · consumable_by_evaluator=False · 저장소 commit 0

> 목적은 Rule Inventory 구축이 아니라 **전체 원문의 무손실 구조화 및 미결정 지점 노출**이다.

## ① 총 셀 / 조각

- 셀 **46** · 조각 **106**
- 그중 결합 표기 객체 **24** (원문의 `또는`·`/`·`→`·`+`·`·`)
- 내용 조각 **82**

## ② full / partial · uncovered

- `full` **46** · `partial` **0**
- 의미 있는 미분해 구간이 남은 셀 **0건**
- 커버리지 100% 셀 **22 / 46** · 최저 84.6%
- 미피복으로 남긴 것은 문장부호 `.` `—` 뿐이다. 결합 표기는 객체로 보존했다.

## ③ role 별 개수

| object_role | 개수 | 비고 |
|---|---:|---|
| `non_rule_evidence` | 42 | 주석·부재표식·폐기이력 18 + 결합표기 24 |
| `rule_candidate` | 39 | Rule Inventory 후보 |
| `execution_reference` | 21 | Inventory 집계 제외 (CIO 판정 ③) |
| `UNRESOLVED` | 4 | 층 자체를 정할 수 없는 조각 |

rule_candidate 내 kind: `FAL` 19 · `MON` 16 · `ENT` 3 · `UNRESOLVED` 1

downstream_effect: `강등 검토` 20 · `monitoring` 16 · `daily_eligibility` 3

**MON 16건은 Rule Inventory 포함 · Evaluator Population 제외** (CIO 판정 ④)

## ④ UNDEFINED / UNRESOLVED 목록

### `definition_status = UNDEFINED` — 15건 (정의를 만들지 않았다)

| 조각 | data | evaluator | blocked_by |
|---|---|---|---|
| `000660.KS::탈락 조건#1` | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING |
| `000660.KS::탈락 조건#3` | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING, SOURCE_UNRESOLVED |
| `005930.KS::탈락 조건#1` | AVAILABLE | **BLOCKED** | DEFINITION_UNDEFINED |
| `005930.KS::탈락 조건#3` | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING |
| `298040.KS::탈락 조건#5` | AVAILABLE | **BLOCKED** | DEFINITION_UNDEFINED |
| `ANET::탈락 조건#1` | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING, SOURCE_UNRESOLVED |
| `CRDO::탈락 조건#1` | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING, SOURCE_UNRESOLVED |
| `CRDO::탈락 조건#3` | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING |
| `MSFT::탈락 조건#1` | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING, SOURCE_UNRESOLVED |
| `MSFT::탈락 조건#3` | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING |
| `MU::탈락 조건#3` | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING, SOURCE_UNRESOLVED |
| `NVDA::다음 이벤트#2` | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING, SOURCE_UNRESOLVED |
| `NVDA::탈락 조건#3` | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING |
| `TSM::진입 패턴#1` | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING, SOURCE_UNRESOLVED |
| `TSM::탈락 조건#3` | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING, SOURCE_UNRESOLVED |

### `object_role = UNRESOLVED` — 4건 (층 자체가 미정)

- `267260.KS::다음 이벤트#1`
- `298040.KS::다음 이벤트#1`
- `ANET::탈락 조건#3`
- `TSM::진입 패턴#1`

### 파생 `READY` — **1건** → ['298040.KS::탈락 조건#3']

⛔ Rule Inventory READY 수치가 아니다. `DEFINED × AVAILABLE` 이라는 구조적 파생 결과일 뿐이며 Stage 변경·투자 판단으로 이어지지 않는다.

## ⑤ cross-cell duplicate 후보

⛔ **합치지 않았다.** 서로 다른 source occurrence 로 전부 보존돼 있으며, canonical `rule_id` 부여와 dedup 은 별도 단계다.

### (a) 원문 문자열 동일 — 2건

- `DRAM ASP 하락 전환` → `MU::탈락 조건#3`, `000660.KS::탈락 조건#3`
- `236.5` → `CRDO::핵심 저항#3`, `NVDA::핵심 저항#3`

### (b) 사람이 판독한 의미 중복 — 2건

| 후보 | occurrence | 근거 |
|---|---|---|
| TSMC 월매출 약화 → 매수 취소·Ready 해제 | `TSM::다음 이벤트#5` · `TSM::편입 사유#4`(pilot) | 같은 판정 기준이 두 칸에 기재. 편입 사유 쪽은 `⚠ PROTOTYPE — 정본 규칙 아님` 표식 아래에 있다 |
| TSMC $398 이탈 | `TSM::기술적 무효화#1`(→강등 검토) · `TSM::편입 사유#5`(→청산) | ★ 같은 숫자인데 **효과가 다르다**. 중복이 아니라 서로 다른 층의 두 객체일 수 있다 — dedup 단계 판정 사항 |

### (c) 같은 종목 안에서 참조값과 Rule 이 같은 숫자를 쓰는 경우 — 2건

- TSMC `SMA20 $409` — `TSM::핵심 지지#1`(참조) vs `TSM::기술적 무효화#2`(daily_eligibility)
- TSMC `$398` — `TSM::핵심 지지#3`(참조) vs `TSM::기술적 무효화#1`(강등 검토)

→ 숫자가 같아도 **효과가 붙은 쪽만 Rule** 이다. 참조값은 Inventory 집계 대상이 아니므로 dedup 대상도 아니다.

## ⑥ prototype / 폐기 / 사용금지 annotation 전파

| 표식 | 위치 | 사정거리 | 처리 |
|---|---|---|---|
| `⚠ PROTOTYPE 사례 1번 — 정본 규칙 아님` | `TSM::편입 사유#1` | 같은 칸 조각 2~6 (Action Plan A/B/C/D · Max Position) | 주석 대상으로 연결. **정본 Rule 로 승격 금지** |
| `※ 종전 3등급 및 +50% 기준은 폐기` | `TSM::다음 이벤트#7` | 조각 5·6(약화/비약화 기준) | 폐기 이력으로 보존. 폐기된 기준을 Rule 후보로 만들지 않음 |
| `⚠ '연속'의 정의가 Undefined … 탈락 판정하지 않는다` | `298040.KS::탈락 조건#6` | 조각 5(기관 순매수 연속 끊김) | ★ 검증기가 강제 — 이 조각을 `DEFINED` 로 올리면 빌드 실패 |
| `Entry Language … Undefined → A 성립 판정하지 않는다` | `TSM::편입 사유#7` | 조각 2·3(A/B 진입) | 주석 연결 · 두 조각 모두 `UNDEFINED` |
| `(펌더멘털 무효화=탈락 조건과 분리)` | `TSM::기술적 무효화#3` | 조각 1·2 | 칸 경계 설명 |
| `현장에서 정의 만들지 않음` | `SNDK::탈락 조건#1` | 자기 칸 | 부재 표식 겸 지시 |

**부재 표식 8건** — `해당 없음 — Coverage 단계`(267260) · `미정 — Discovery 단계`(329180·SNDK) · `미설정`(000660·005930 지지/저항) · `❓미확인`(SNDK 지지/저항) · `미지정`(SNDK 진입 패턴) · `3Q 실적 일정 미공표`(000660·005930).
전부 `non_rule_evidence` 로 보존했고 Rule 후보로 만들지 않았다.

★ **칸 경계를 넘는 주석 4건은 아직 연결하지 못했다.** `annotates_split_index` 는 칸 안에서만 작동한다 — 아래 ⑧ 참조.

## ⑦ 불변식 테스트 결과

| 스위트 | 결과 |
|---|---|
| extractor fail-closed 회귀 | **37 PASS / 0 FAIL** |
| decomposition pilot 회귀 (양성+음성) | **36 PASS / 0 FAIL** |
| 46칸 전체 불변식 검증 | **위반 0** |
| 빌드 시 원문 부분문자열 assert | 106/106 통과 |

## ⑧ 확장 중 새로 드러난 미결정 지점 — 판정 요청

⛔ 전부 판정하지 않고 원문 그대로 올린다.

**(1) 조건 간 결합(AND/OR)을 어디에 둘 것인가**
원문의 `또는`·`/`·`+` 24건을 `non_rule_evidence` 객체로 보존했다. 결합이 Rule 객체의 속성인지, 별도 객체인지, 아니면 분해 자체를 바꿔야 하는지가 정본에 없다. 삼성전자 `탈락 조건`이 실제 경계다 — 편입 사유가 "탈락 조건은 AND 구조"라 명시했는데, 두 조각을 따로 두면 각각 독립 평가 가능한 것처럼 보인다.

**(2) cross-cell annotation 이 필요하다 — 4건**
`annotates_split_index` 는 칸 안에서만 작동한다. 실제로는 다른 칸이 조건을 제한하고 있다.

| 제한하는 칸 | 제한받는 조각 | 원문 |
|---|---|---|
| `CRDO::편입 사유` | `CRDO::탈락 조건#3` | "고객 집중도 기준이 분기/연간 중 어느 것인지 미정의" |
| `ANET::편입 사유` | `ANET::탈락 조건#1` | "주가 하락만으로는 제외하지 않는다" |
| `005930::편입 사유` | `005930::탈락 조건#1·#3` | "탈락 조건은 AND 구조이며 HBM 공급 확대가 명시적으로 해소돼 성립하지 않는다" |
| `TSM::다음 이벤트#8` | `TSM::편입 사유` Action Plan | "Action Plan 가격조건은 변경 없음" |

지금 구조로는 이 제한들이 **연결되지 않은 채 남아 있다.** 필드 신설은 판정 사항이라 만들지 않았다.

**(3) `편입 사유` 11칸이 미착수**
CIO 판정으로 review-required source 가 됐으나 이번 "46칸" 범위 밖이다. TSM 1칸만 pilot 에서 분해했다. 위 (2)의 제한 문언이 대부분 여기 있으므로 **다음 단계 입력으로 필요하다.**

**(4) `진입 패턴` select 값의 층**
`TSM::진입 패턴 = B 박스권 돌파 후 재확인` — 스키마 설명이 "Ready 승격 가능"이라 Stage 변경(§21-7 Decision Layer)에 걸린다. `object_role` `UNRESOLVED` 로 뒀다.

**(5) `슬롯 이양` 효과**
`ANET::탈락 조건#3` = "AI 매출 목표 하향 시 CRDO에 슬롯 이양" — 다른 종목으로의 자본 배분 이동이다. Rule/Stage/Portfolio 어느 층인지 정본에 대응물이 없다.

**(6) B1-Q3 실제 사례 2건 발생**
- `267260.KS::다음 이벤트` = "재진입 게이트 3항목 재점검" — 날짜 없음
- `298040.KS::다음 이벤트` = "Expectations Gap 판정 기준 확정 (8/15 Review)" — 날짜는 있으나 작업항목

지시대로 정의하지 않고 `UNRESOLVED` 로 올린다.

**(7) B1-Q1 완료 이벤트 6건 · B1-Q2 미확정 날짜 7건**
CIO 판정대로 보존하되 상태를 채우지 않았다. `event_status` 및 날짜 상태 vocabulary 는 만들지 않았다 — 필요하면 별도 제안으로 올린다.

- **완료(Q1)**: `TSM::다음 이벤트#1` · `CRDO::다음 이벤트#1·#3·#5` · `ANET::다음 이벤트#1·#3`
- **미확정 날짜(Q2)**: `MU::다음 이벤트#1` · `TSM::다음 이벤트#11` · `CRDO::다음 이벤트#7` · `ANET::다음 이벤트#5` · `MSFT::다음 이벤트#1` · `SNDK::다음 이벤트#1` · `000660·005930::다음 이벤트`(일정 미공표)

## 경계

Evaluator 연결 · Production 반영 · Stage 변경 · Notion 정본 수정 · 실제 Rule ID 확정 — **전부 미실행**.
Production HOLD 유지 · 저장소 commit 0 · 투자 행동 없음.
