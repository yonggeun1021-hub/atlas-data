# P2 — 미국 실적·재무 dependency map (조사 기록 2026-08-15)

⛔ **이 문서는 현황 기록이다. Rule SSOT 를 바꾸는 authority 가 아니다.**
Rule 상태의 정본은 `config/rules.json` 이며 이 문서는 그것을 변경하지 않는다.

## 하위 cluster 와 현재 판정

| Cluster | Rule | 현재 P2 판정 |
|---|---|---|
| **P2-a** | `RULE-0022` MSFT RPO | **XBRL FEASIBLE** — direct RPO observation 확인. comparability validation 미해결 |
| (제외) | `RULE-0001` MU FQ4 $49B | **DEFINITION REOPEN REQUIRED** — 측정 대상 계정과목이 정본·원문 모두에 없음 |
| **P2-b** | `RULE-0004` · `0013` · `0014` · `0021` | 대기 — forward guidance / 목표 / non-GAAP·constant-currency 의 document-semantic extraction |
| **P2-c** | `RULE-0011` CRDO 고객집중도 | 대기 — 10-K/10-Q narrative·footnote 서술 |
| **P2-d** | `RULE-0015` NVDA 하이퍼스케일러 capex | 대기 · **착수 금지** — 대상 기업군(universe)과 comparison baseline 이 정본에 없음. 데이터 취득 이전에 definition/coverage decision 필요 |

---

## `RULE-0001` — provenance 역추적 결과

**결론: decomposition 과정의 손실이 아니다. 원문에도 계정과목이 없다.**

원천(`Notion PM Watchlist` 수집본 `_watchlist_rows.json`)의 MU 행 원문:

```
"티커": "MU"
"탈락 조건": "FQ4 $49B 미달 또는 DRAM ASP 하락 전환"
```

이 문자열이 `decompose_full.json` 의 `raw_fragment: "FQ4 $49B 미달"` 로,
다시 `canonical_rules.json` 의 `condition_text` 로 **문자 그대로** 이어진다.
어느 단계에서도 계정과목이 탈락하지 않았다 — **처음부터 없었다.**

⇒ `$49B` 가 매출인지 다른 항목인지 확정할 수 없으므로 SEC concept 매핑을 시작할 수 없다.
⛔ 매출로 가정해 `RevenueFromContractWithCustomerExcludingAssessedTax` 에 연결하지 않았다.

### 부수 관측 — direct observation 부재 (계정과목이 정해진 뒤에야 의미를 갖는다)

매출이라고 **가정할 경우에도**, MU 의 해당 concept 에는
`fp` 값이 `FY · Q1 · Q2 · Q3` 만 존재하고 **discrete Q4 항목이 없다.**
FQ4 를 얻으려면 `FY − (Q1+Q2+Q3)` 가 필요하다.

⛔ derived quarter 를 만들지 않았다. 계정과목 확정 → direct observation 존재 여부 →
(없으면) derived 허용 여부 순서를 지킨다.

### 시스템 차원의 관찰

`definition_status = DEFINED` · `missing_components = []` 인데
**무엇을 측정하는지가 없다.** 현재 definition completeness gate 는 숫자 threshold 의
존재는 보지만 **그 숫자가 무엇을 측정하는가(metric identity)** 는 보지 않는다.
⇒ `RULE-0001` 이 실제 반례로 확정되면, invariant 에 metric identity 축이 필요한지
별도 안건으로 올린다. ⛔ 이번에는 invariant 를 고치지 않는다.

---

## `RULE-0022` — SEC RPO fact provenance

**concept**: `us-gaap:RevenueRemainingPerformanceObligation` (표준 taxonomy, extension 아님)
**unit**: USD · **성격**: point-in-time instant · **cadence**: 10-Q + 10-K

관측된 provenance 표본:

| end | val | accn | fy | fp | form | filed | frame |
|---|---|---|---|---|---|---|---|
| 2023-09-30 | 216B | 0000950170-23-054855 | 2024 | Q1 | 10-Q | 2023-10-24 | CY2023Q3I |
| 2023-12-31 | 229B | 0000950170-24-008814 | 2024 | Q2 | 10-Q | 2024-01-30 | CY2023Q4I |
| 2024-03-31 | 242B | 0000950170-24-048288 | 2024 | Q3 | 10-Q | 2024-04-25 | CY2024Q1I |
| **2024-06-30** | 275B | **0000950170-24-087843** | 2024 | FY | **10-K** | 2024-07-30 | *(없음)* |
| **2024-06-30** | 275B | **0000950170-24-132722** | — | — | **8-K** | **2024-12-03** | **CY2024Q2I** |
| 2024-09-30 | 266B | 0000950170-24-118967 | 2025 | Q1 | 10-Q | 2024-10-30 | CY2024Q3I |
| 2024-12-31 | 304B | 0000950170-25-010491 | 2025 | Q2 | 10-Q | 2025-01-29 | CY2024Q4I |
| 2025-03-31 | 321B | 0000950170-25-061046 | 2025 | Q3 | 10-Q | 2025-04-30 | CY2025Q1I |
| 2025-06-30 | 375B | 0000950170-25-100235 | 2025 | FY | 10-K | 2025-07-30 | CY2025Q2I |
| 2025-09-30 | 398B | 0001193125-25-256321 | 2026 | Q1 | 10-Q | 2025-10-29 | CY2025Q3I |

⚠️ 이 표가 계열의 **최신 꼬리라고 단정하지 않는다.** 같은 endpoint 를 다른 질의로 읽었을 때
더 나중 기간이 보인 적이 있어, 표본으로만 기록한다.

### 중복 fact 의 분해 — 임의 선택하지 않았다

`end = 2024-06-30` 에 두 fact 가 존재한다. **값은 같고(275B) 출처가 다르다.**

- 원 제출: **10-K** `…-24-087843` (2024-07-30) — `frame` 없음
- 재등장: **8-K** `…-24-132722` (**2024-12-03**) — `frame = CY2024Q2I` 가 **이쪽에 부여됨**

⇒ 중복은 정정(amendment)이 아니다. 데이터셋에 `10-Q/A` · `10-K/A` 는 **없다.**
후행 8-K 에서 같은 기간 값이 다시 제출된 형태다.

**두 가지 함의**

1. **`frame` 을 키로 삼는 collector 는 조용히 8-K 행을 고른다.** `frame` 부여가 중복 간에
   안정적이지 않다. ⛔ 키 선택을 구현 편의로 정하면 안 된다.
2. 후행 8-K 에서 동일 기간 값이 재제출되는 사건은 **비교가능성 검증의 구조적 신호**가 될 수 있다.
   서술을 읽지 않고도 provenance 만으로 탐지 가능한 축이 있다는 뜻이다.

### 계층 분리 (CIO 확정)

```
Observation             → SEC XBRL 의 RPO total fact
Comparability validation → 동일 concept / unit / entity-wide context / filing cadence 확인
                          + RPO disclosure 의 측정범위 변경 신호 확인
Evaluation              → 성장률 → 10%p 기준 → 2회 연속
```

narrative 는 **숫자를 얻는 source 가 아니라 비교가능성을 무효화할 수 있는 veto source** 다.
변경 신호가 있거나 비교가능성을 판단할 수 없으면 **숫자를 보정하지 말고**
그 관측 pair 를 `COMPARABILITY_UNRESOLVED` 로 막는다.

### 이번 조사 분류

| 구분 | 내용 |
|---|---|
| `DIRECT_OBSERVATION` | RPO total 이 표준 concept 의 instant fact 로 직접 존재. 분기 cadence 와 과거 이력 확보 가능 |
| `COMPARABILITY_VALIDATED` | **없음** |
| `COMPARABILITY_UNRESOLVED` | `end=2024-06-30` 의 10-K ↔ 8-K 중복. 후행 8-K 재제출의 의미가 미판정 |
| 미수행 | RPO disclosure **서술 문구** 확인 — filing 본문 취득이 별도 단계라 이번 조사에서 하지 않았다 |

⛔ 성장률 · 10%p · 2회 연속 판정은 계산하지 않았다.

---

## 경계 (유지)

`RULE-0022` 의 `DATA_MISSING` 미해제 · `RULE-0001` 의 `definition_status` 미변경 ·
collector 구현 없음 · source 채택 없음 · evaluator 미연결 · Production HOLD ·
`consumable_by_evaluator=false`.
