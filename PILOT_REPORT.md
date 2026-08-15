# B1 reviewed decomposition pilot — 결과

**상태: inactive preparation** · authority=False · 저장소 커밋 0건

| 셀 | scope | 조각 | 커버리지 | 미분해(유의미) |
|---|---|---:|---:|---:|
| `TSM::기술적 무효화` | full | 3 | 98.3% | 0 |
| `298040.KS::탈락 조건` | full | 4 | 92.9% | 0 |
| `TSM::다음 이벤트` | full | 8 | 97.4% | 0 |
| `MSFT::다음 이벤트` | full | 1 | 100.0% | 0 |
| `MSFT::핵심 지지` | full | 1 | 100.0% | 0 |
| `TSM::편입 사유` | partial | 7 | 35.1% | 3 |

총 조각 24건 · 불변식 위반 **0건**

## 파생 결과 — `evaluator_status`는 입력이 아니라 계산값이다

| 조각 | role | kind | downstream_effect | definition | data | evaluator | blocked_by |
|---|---|---|---|---|---|---|---|
| `TSM::기술적 무효화#1` | rule_candidate | UNRESOLVED | 강등 검토 | DEFINED | MISSING | **BLOCKED** | DATA_MISSING, SOURCE_UNRESOLVED |
| `TSM::기술적 무효화#2` | rule_candidate | ENT | daily_eligibility | DEFINED | MISSING | **BLOCKED** | DATA_MISSING, SOURCE_UNRESOLVED |
| `298040.KS::탈락 조건#1` | rule_candidate | FAL | 강등 검토 | DEFINED | MISSING | **BLOCKED** | DATA_MISSING |
| `298040.KS::탈락 조건#2` | rule_candidate | FAL | 강등 검토 | DEFINED | AVAILABLE | **READY** | — |
| `298040.KS::탈락 조건#3` | rule_candidate | FAL | 강등 검토 | UNDEFINED | AVAILABLE | **BLOCKED** | DEFINITION_UNDEFINED |
| `TSM::다음 이벤트#1` | rule_candidate | MON | monitoring | UNRESOLVED | UNRESOLVED | **UNRESOLVED** | STATUS_UNRESOLVED |
| `TSM::다음 이벤트#3` | rule_candidate | FAL | 강등 검토 | DEFINED | MISSING | **BLOCKED** | DATA_MISSING, SOURCE_UNRESOLVED |
| `TSM::다음 이벤트#4` | rule_candidate | ENT | daily_eligibility | DEFINED | MISSING | **BLOCKED** | DATA_MISSING, SOURCE_UNRESOLVED |
| `TSM::다음 이벤트#7` | rule_candidate | MON | monitoring | UNRESOLVED | UNRESOLVED | **UNRESOLVED** | STATUS_UNRESOLVED |
| `TSM::다음 이벤트#8` | rule_candidate | MON | monitoring | UNRESOLVED | UNRESOLVED | **UNRESOLVED** | STATUS_UNRESOLVED |
| `MSFT::다음 이벤트#1` | rule_candidate | MON | monitoring | UNRESOLVED | UNRESOLVED | **UNRESOLVED** | STATUS_UNRESOLVED |
| `MSFT::핵심 지지#1` | execution_reference | UNRESOLVED | execution_reference | DEFINED | MISSING | **BLOCKED** | DATA_MISSING, SOURCE_UNRESOLVED |
| `TSM::편입 사유#2` | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNDEFINED | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING, SOURCE_UNRESOLVED |
| `TSM::편입 사유#3` | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNDEFINED | MISSING | **BLOCKED** | DEFINITION_UNDEFINED, DATA_MISSING, SOURCE_UNRESOLVED |
| `TSM::편입 사유#4` | UNRESOLVED | UNRESOLVED | UNRESOLVED | DEFINED | MISSING | **BLOCKED** | DATA_MISSING, SOURCE_UNRESOLVED |
| `TSM::편입 사유#5` | UNRESOLVED | UNRESOLVED | UNRESOLVED | DEFINED | MISSING | **BLOCKED** | DATA_MISSING, SOURCE_UNRESOLVED |
| `TSM::편입 사유#6` | UNRESOLVED | None | UNRESOLVED | DEFINED | UNRESOLVED | **UNRESOLVED** | STATUS_UNRESOLVED |

`READY` **1건** → ['298040.KS::탈락 조건#2']

⛔ 이 숫자는 Rule Inventory 가 아니다 — pilot 6칸의 파생 결과일 뿐이며 `provisional`이다.

## UNRESOLVED 로 올리는 어휘 공백

1. rule_kind 3토큰(FAL/ENT/MON)은 소실된 draft 의 rule_id 에서 관측됐을 뿐 정본에 정의가 없다. 정의 신설 여부는 CIO 판정 사항이다.
2. '강등 검토'(§21-13)는 Stage 변경이며 §21-7 이 'Stage 변경은 별도 Decision Layer 소관'이라 했으므로, Rule Evaluator 의 downstream_effect 로 두는 것이 맞는지 미정이다.
3. execution_reference 객체는 Rule 이 아니므로 rule_kind 어휘에 대응물이 없고, Rule Inventory 집계 대상인지도 정본에 규정이 없다.
4. monitoring 객체는 §21-12 상 Inventory 에는 포함되나 Evaluator Population 에서는 제외된다. 따라서 definition_status/data_status 를 채우는 것이 의미를 갖는지 미정이다.

## 이번 pilot 에서 실제로 걸린 B1 열린 질문

- B1-Q1 (완료 이벤트를 Inventory 에 남기는가) — TSM::다음 이벤트 조각 1에서 실제로 걸림
- B1-Q2 (날짜 미공표 '미정' vs '지남') — TSM::다음 이벤트 조각 8 · MSFT::다음 이벤트 조각 1에서 걸림
- B1-Q3 (날짜 없는 안건은 이벤트인가 작업항목인가) — 이번 6칸에서는 발생하지 않음
