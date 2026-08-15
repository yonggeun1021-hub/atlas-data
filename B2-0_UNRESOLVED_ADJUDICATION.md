# B2-0 — UNRESOLVED adjudication packet

> **이 문서는 판정을 담지 않는다.** 4건의 선택지를 정본 어휘 안에서 좁혀 CIO가 한 번에 판정할 수 있게 만든 것이다.
> Claude 자체 판정 없음 · 추천 판정 없음 · 새 vocabulary/effect/threshold 없음 · 원문 수정 없음.

## B1 종료 baseline (동결)

```
산출물     rules/decompose_full.json
sha256     b8212ffe3e5e88c1ae93097806fe36a541bd12ed4ae33daeba5cac5b8460e153
크기       71,480 bytes
46/46 cells covered · invariant violations 0 · 97 PASS / 0 FAIL
unresolved 4 · READY 1 · reference 21 · monitoring 16
non-rule evidence 42 · rule_candidate 39 · 총 조각 106
```

이후 단계에서 B1 원문 판독을 조용히 수정하지 않는다. 수정 필요성이 발견되면 regression이 아니라 **B1 amendment 후보로 CIO에게 먼저 보고**한다. 위 해시가 그 판별 기준이다.

**non-blocking technical debt (기록만 · 이번 단계 수정 안 함)** — `validate_decomposition.py` `coverage()` 의 occurrence fallback:

```python
i = raw.find(frag, cursor)
if i < 0:
    i = raw.find(frag)
    problems.append(...)
```

builder와 달리 여기서는 `problems` 에 violation을 남기므로 현재 `valid` 판정을 뚫지 못한다. 향후 span 계산까지 fail-closed로 통일 대상.

---

## 판정표 — 4행

| # | candidate_id | source_cell | raw_fragment | object_role | rule_kind | downstream_effect | definition_status | 왜 UNRESOLVED인가 | 선택 가능한 기존 정본 분류 | CIO가 결정해야 할 질문 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `TSM::진입 패턴#1` | 진입 패턴 | `B 박스권 돌파 후 재확인` | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNDEFINED | 이 칸의 스키마 설명이 *"이 중 하나가 확인돼야 Ready 승격 가능"* 이다. 즉 효과가 **Stage 승격**인데, §21-7은 *"Stage 변경은 별도 Decision Layer 소관"* 이라 Rule Evaluator의 산출이 아니라고 못박았다. | **object_role**: `rule_candidate` / `non_rule_evidence` 둘 다 표현 가능<br>**rule_kind**: `ENT` 사용 가능 (CIO 확정 "진입 eligibility 계열 조건 후보")<br>**downstream_effect**: `daily_eligibility` 사용 가능. **단 'Stage 승격' 자체를 나타내는 토큰은 `existing vocabulary insufficient`** — `강등 검토`는 방향이 반대이고 CIO 판정 ②로 executable 금지 | 진입 패턴 select 값은 **Rule SSOT 안의 객체인가, 아니면 Stage 승격 게이트로서 Rule SSOT 밖(Decision Layer)인가?**<br>Rule 안에 둔다면 효과를 `daily_eligibility`로 볼 것인가, 아니면 Stage 승격 표현이 없으므로 어휘 신설 판정으로 넘길 것인가? |
| 2 | `ANET::탈락 조건#3` | 탈락 조건 | `AI 매출 목표 하향 시 CRDO에 슬롯 이양` | UNRESOLVED | UNRESOLVED | UNRESOLVED | DEFINED | 한 조각 안에서 **조건은 ANET에, 효과는 CRDO에** 걸린다. §21-1은 Research/Portfolio/Execution/Risk 네 역할을, §21-7은 Stage/Action/Portfolio Operation 세 층을 섞지 말라고 한다. 자본 배분이 다른 종목으로 이동하는 효과는 셋 중 어디에도 대응 토큰이 없다. | **object_role**: `rule_candidate` / `non_rule_evidence` 둘 다 표현 가능<br>**rule_kind**: `FAL` 사용 가능 (조건부 — 조건 쪽만 본다면)<br>**downstream_effect**: **`existing vocabulary insufficient`** — cross-symbol 자본 재배분을 나타내는 토큰이 정본에 없다. `강등 검토`는 ANET 단독 강등만 표현하고 CRDO 승격을 담지 못한다 | 조건(ANET)과 효과(CRDO)가 서로 다른 종목에 걸리는 문장을 **한 Rule 객체로 둘 수 있는가?**<br>아니면 조건만 `FAL`로 남기고 '슬롯 이양'은 Portfolio Operation으로 분리해 Rule SSOT 밖으로 보낼 것인가? |
| 3 | `267260.KS::다음 이벤트#1` | 다음 이벤트 | `재진입 게이트 3항목 재점검` | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED | **B1-Q3 실제 발생** — 날짜가 없고, 관측 대상 사건이 아니라 우리 쪽 작업항목에 가깝다. §21-14(1)이 *"날짜 없는 안건은 이벤트인가 작업항목인가"* 를 미결로 남겼다. | **object_role**: `rule_candidate`(+`MON`) / `non_rule_evidence` 둘 다 표현 가능<br>**rule_kind**: `MON` 사용 가능 (CIO 확정 "관측 이벤트")<br>**downstream_effect**: `monitoring` 사용 가능<br>※ 사실 관계: `MON`으로 두면 §21-12상 **Rule Inventory 포함 · Evaluator Population 제외** 가 된다 | **B1-Q3 판정** — 날짜 없는 안건을 `MON`(관측 이벤트)으로 볼 것인가, `non_rule_evidence`(작업항목)로 볼 것인가?<br>구분 기준을 **날짜 유무**로 둘 것인가, **관측 대상 사건 vs 우리 작업**이라는 성격으로 둘 것인가? |
| 4 | `298040.KS::다음 이벤트#1` | 다음 이벤트 | `Expectations Gap 판정 기준 확정 (8/15 Review)` | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED | **B1-Q3 두 번째 사례 — 3번과 축이 다르다.** 이쪽은 **날짜가 있는데** 내용은 작업항목(우리가 기준을 확정하는 일)이다. 3번을 '날짜 유무'로 가르면 이 건은 반대쪽으로 떨어지고, '성격'으로 가르면 3번과 같은 쪽으로 떨어진다. | **object_role**: `rule_candidate`(+`MON`) / `non_rule_evidence` 둘 다 표현 가능<br>**rule_kind**: `MON` 사용 가능<br>**downstream_effect**: `monitoring` 사용 가능 | 3번과 **같은 규칙으로 함께 판정할 것인가, 별개로 볼 것인가?**<br>이 건이 3번과 다른 답이 되어야 한다면, 그 구분 기준은 무엇인가? |

---

## 항목별 보충 — 판정에 필요한 사실만

### 1. `TSM::진입 패턴#1`

- 원문은 Notion select 값이며, 스키마 설명은 *"진입 실행 기준 v1(2026-08-02 PM) Gate 4. 진입대기 이상 상태에서 지정. 이 중 하나가 확인돼야 Ready 승격 가능"*.
- 같은 행 `편입 사유`에 *"Entry Language(돌파·재지지·누름의 기계적 정의)가 Undefined → 8/15 Review에서 정의되기 전까지 A 성립 판정하지 않는다"* 가 있다. → `definition_status = UNDEFINED` 의 근거이며, **이 판정과 무관하게 UNDEFINED는 유지된다.**
- 현재 파생: `BLOCKED` / `blocked_by = [DEFINITION_UNDEFINED, DATA_MISSING, SOURCE_UNRESOLVED]`.
- 어느 쪽으로 판정하든 evaluator 결과는 바뀌지 않는다. 바뀌는 것은 **이 객체가 Rule SSOT 안에 있는가**이다.

### 2. `ANET::탈락 조건#3`

- 같은 칸의 조각 1(`호실적+주가 하락(선반영)`)은 이미 `FAL`로 분류돼 있고, 이 조각과 `또는`로 결합돼 있다(결합 표기 객체로 보존됨).
- → 만약 이 조각을 Rule SSOT 밖으로 보내면 **`또는`의 한쪽 항이 사라진다.** 결합 표기 객체가 가리키는 대상이 한쪽만 남는다.
- 같은 행 `편입 사유`에 *"탈락 조건은 'AI 매출 목표 하향'이며 주가 하락만으로는 제외하지 않는다"* — 이 제한은 현재 cross-cell이라 연결되지 않은 상태다(B1 보고 ⑧-(2)).
- `definition_status = DEFINED` 인 것은 조건 쪽('AI 매출 목표 하향')이 관측 가능하다는 뜻이며, 효과 쪽('슬롯 이양')의 정의 여부와는 별개다.

### 3 · 4. B1-Q3 두 사례

두 건의 좌표를 명시한다. 어느 축으로 자르는지에 따라 결과가 갈린다.

| | 날짜 | 성격 |
|---|---|---|
| `267260.KS` 재진입 게이트 3항목 재점검 | 없음 | 우리 작업 |
| `298040.KS` Expectations Gap 판정 기준 확정 (8/15 Review) | **있음** | 우리 작업 |
| *(대조군)* `329180.KS` 8/20 ERCOT 이사회 | 있음 | 외부 사건 — 이미 `MON`으로 분류됨 |
| *(대조군)* `000660.KS` 3Q 실적 일정 미공표 | 없음 | 외부 사건 — 이미 `non_rule_evidence`(부재 표식)로 분류됨 |

- **날짜 축으로 자르면**: 3번 → `non_rule_evidence`, 4번 → `MON`.
- **성격 축으로 자르면**: 3번·4번 모두 같은 쪽.
- 대조군 두 건은 이미 분류돼 있으므로, 판정 기준이 이 둘과 충돌하지 않는지 확인이 필요하다.
- 이 판정은 §21-14(1)이 남긴 **B1-Q1(완료 이벤트 보존)·B1-Q2(미확정 날짜)와 다른 축**이다. 두 질문은 CIO가 이미 판정했고 이번 4건에는 영향을 주지 않는다.

---

## 판정 후 반영 범위 (착수하지 않음 · 확인용)

CIO 판정이 나오면 `decompose_full.json` 의 해당 4개 조각의 `object_role` / `rule_kind` / `downstream_effect` 만 갱신된다. 이는 **B1 원문 판독 수정이 아니라 UNRESOLVED 자리를 채우는 것**이므로 amendment가 아니다. 갱신 후 baseline 해시는 새로 기록한다.

`definition_status` · `data_status` · 원문 · 결합 표기 · 다른 102개 조각은 건드리지 않는다.

---

**경계 유지** — Production HOLD · `authority=false` · `consumable_by_evaluator=false` · 저장소 commit 0 · canonical rule_id 미부여 · dedup 미착수 · evaluator 미연결 · Notion/Watchlist/Stage 무변경 · 투자 행동 없음.
