# P2 — 미국 실적·재무 dependency map (조사 기록 2026-08-15)

⛔ **이 문서는 현황 기록이다. Rule SSOT 를 바꾸는 authority 가 아니다.**
Rule 상태의 정본은 `config/rules.json` 이며 이 문서는 그것을 변경하지 않는다.

## 하위 cluster 와 현재 판정

| Cluster | Rule | 현재 P2 판정 |
|---|---|---|
| **P2-a** | `RULE-0022` MSFT RPO | **XBRL FEASIBLE** — direct RPO observation 확인. comparability validation 미해결 |
| (제외) | `RULE-0001` MU FQ4 $49B | **REOPENED** — `definition_status: DEFINED → UNDEFINED` 적용 완료 (2026-08-15). 측정 대상 계정과목이 정본·원문 모두에 없음 |
| **P2-b** | **`RULE-0004` · `0021`** | **실착수 대상** — 정의 카드가 서 있어 취득 조사 가능 |
| P2-b (보류) | `RULE-0013` | **definition reopen 후보** — 취득 조사 금지. invariant D 참조 |
| P2-b (보류) | `RULE-0014` | **Comparison Baseline review** — 취득 조사 보류. `UNDEFINED` 로 찍지 않는다 |
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

### 반영 결과 (2026-08-15)

`definition_status` 를 **`UNDEFINED`** 로 되돌렸다. **어휘를 늘리지 않았다** —
`DEFINITION_REOPEN_REQUIRED` 라는 새 상태값을 만들지 않고 기존 `UNDEFINED` 를 쓴다.
그래야 `DEFINITION_UNDEFINED → BLOCKED` 파생이 정상 작동한다.

「처음부터 미검토된 UNDEFINED」와 「DEFINED 였다가 검증으로 다시 열린 UNDEFINED」는
**`definition_reopen` provenance 로 구별**한다.

```
definition_status                        UNDEFINED
definition_status_before_application     DEFINED      ← 과거가 보존된다
definition_reopen.reason                 metric_identity_missing
definition_reopen.open_question          「FQ4 $49B 는 무엇의 $49B 인가?」
definition_reopen.superseded_definition_resolution
                                         「정의 결핍 없음 (defined control population)」
                                         ← 상류의 옛 판정 문구를 지우지 않고 나란히 남긴다
```

⛔ `prohibited` 에 **계정과목 추정 · derived FQ4 생성 · SEC concept mapping 재개**를 명시했다.
★ reopen 은 **닫는 방향**이므로 기존 application(여는 방향)과 **별도 경로**다.
  application 의 역연산으로 겸용하지 않는다.

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

### Gate #2 종결 계약 (CIO 확정 2026-08-15)

```
숫자 관측     XBRL us-gaap:RevenueRemainingPerformanceObligation 의 direct fact 를 SSOT 로 쓴다
comparability veto
             후행 filing 이 존재한다는 사실만으로 발동하지 않는다.
             해당 RPO concept 의 정의·포함범위, 또는 해당 economic-period 의 fact 가
             **실질적으로 변경됐다는 증거**가 있을 때만 발동한다.
veto 단위     filing 단위가 아니라 **concept / economic observation 단위**
서술          숫자 SSOT 가 아니라 **comparability 검증 증거**
```

### 2024-12-03 8-K 의 정체 — 원문 확인 결과

`0000950170-24-132722` = `msft-20241203.htm` · **Item 8.01 + 9.01**

> *"In August 2024, we announced changes to the composition of our segments, most notably
> bringing the commercial components of Microsoft 365 together in the Productivity and
> Business Processes segment."*
>
> *"**The updates do not represent a restatement of previously issued financial statements.**"*

분류: **`segment-presentation recast / non-restatement / RPO comparability veto 아님`**

이유 — 이 8-K 는 **세그먼트 층**의 표시 변경이고, RPO 는 **entity-wide 총액**이다.
두 fact 의 값도 동일(275B)하다. **filing 단위로 veto 를 걸면 오탐이 된다.**

⛔ 앞선 조사에서 제안했던 *"후행 8-K 재제출 = 비교가능성 신호"* 라는 구조적 축은
   **이대로 쓰면 안 된다** — 위 원칙(concept 단위)으로 대체한다.

### 서술 확인 결과 — 숫자와 교차 일치한다

| 시점 | 총액 | commercial portion | 12개월 내 인식 |
|---|---|---|---|
| FY2025 10-K (2025-06-30) | **$375B** | $368B | 약 40% |
| FY2026 Q1 10-Q (2025-09-30) | **$398B** | $392B | 약 40% |

★ **중요한 identity 확인** — 이 총액이 XBRL fact 와 정확히 일치한다
(`2025-06-30 = 375B` · `2025-09-30 = 398B`).
⇒ `us-gaap:RevenueRemainingPerformanceObligation` 은 **total company RPO** 이며
**commercial portion($392B) 이 아니다.** collector 가 commercial 값을 집으면 틀린다.

또한 FY2025 → FY2026 Q1 사이에 RPO 설명의 핵심 범위(unearned revenue + 향후 invoice 될
금액, 40%/12개월 구조)가 **일관**된다. 급격한 정의·범위 변경 증거는 현재 없다.

⛔ 그렇다고 과거 6개 분기 전부의 comparability 를 자동 승인하지 않는다.

### 이번 조사 분류

| 구분 | 내용 |
|---|---|
| `DIRECT_OBSERVATION` | RPO total 이 표준 concept 의 instant fact 로 직접 존재. 분기 cadence 와 과거 이력 확보 가능 |
| `COMPARABILITY_VALIDATED` | `end=2024-06-30` 중복 — **veto 아님** 으로 확정 (segment recast · non-restatement · 값 동일) · FY2025↔FY2026Q1 서술 범위 일관 |
| `COMPARABILITY_UNRESOLVED` | 나머지 economic period 는 개별 검증하지 않았다 — 자동 승인하지 않는다 |

⛔ 성장률 · 10%p · 2회 연속 판정은 계산하지 않았다.

---

## `RULE-0022` data/source blocker 해제 가능 여부 — 판정 요청

⛔ **해제하지 않았다.** 아래는 근거 정리이며 판정은 CIO 몫이다.

### 해제를 지지하는 근거

| 축 | 근거 |
|---|---|
| `DATA_MISSING → AVAILABLE` | 표준 taxonomy concept 의 **direct instant fact** · 10-Q/10-K 분기 cadence · 과거 이력 존재 · **서술 총액과 XBRL 값이 교차 일치** |
| `SOURCE_UNRESOLVED → SOURCE_RESOLVED` | SEC EDGAR XBRL companyconcept — **인증 불요** · 공식 API · `data.sec.gov` 는 P3(C4)에서 **GitHub-hosted runner 도달이 이미 실증**됨 |

### 해제를 미룰 근거 — 세 가지

**(가) P3 선례와 비대칭이다.**
`RULE-0003/0007/0008` 은 **GitHub live run 으로 end-to-end 추출을 실증한 뒤** 해제했다.
`RULE-0022` 는 아직 **collector 도 live run 도 없다.** 문서 근거만으로 해제하면
P3 에서 세운 기준보다 낮은 문턱을 적용하는 것이 된다.

**(나) 중복 fact 선택 규칙이 아직 없다.**
같은 `end` 에 복수 fact 가 존재할 때 **어느 accession 을 소비하는가**가 계약에 없다.
`frame` 은 중복 간 부여가 불안정해 키로 쓸 수 없음이 실측으로 확인됐다.

**(다) 이 Rule 은 단일 관측이 아니라 계열을 요구한다.**
카드 18·19·20 을 합치면 판정에 필요한 관측은 다음과 같다.

```
성장률(t)        = RPO(t)      / RPO(t-4Q)
전년동기 성장률   = RPO(t-4Q)   / RPO(t-8Q)
2회 연속          → t 와 t-1Q 각각에 대해 위 두 값이 필요
⇒ 최소 RPO(t) · (t-1Q) · (t-4Q) · (t-5Q) · (t-8Q) · (t-9Q)  ≈ 10개 분기
```

`RULE-0003` 이 "2개월 연속" 때문에 단월 관측만으로 해제되지 않았던 것과 **같은 구조**다.
관측된 표본은 2023-09-30 ~ 2025-09-30 의 9개 분기이며, 그 표본이 계열의 최신 꼬리인지도
확인되지 않았다.

### 제안 (판정 대상)

`RULE-0022` 의 해제를 **P3 와 같은 순서**로 두는 것 — ① 중복 선택 규칙 확정 →
② collector + live run 으로 10개 분기 계열 구성 실증 → ③ 그 결과로 해제 판정.

⛔ 성장률 · 10%p · 2회 연속 계산은 하지 않았다. evaluator 연결도 하지 않았다.

---

## P2-b 취득 조사 (진행 중) — `RULE-0004` · `RULE-0021`

### `RULE-0004` TSM capex 하향

**요구 관측값** (CIO 카드 3건으로 이미 확정):
TSMC 가 공식 실적발표에서 제시하는 **연간 consolidated capital budget / CapEx 가이던스의
범위 하단**. 동일 대상 회계연도의 **직전 공식 가이던스 하단**과 비교. 단순 범위 축소는 제외.
새 대상연도 최초 가이던스는 판정하지 않음.

**조사 결과 — 1차 후보에서는 찾지 못했다.**

`0001046179-26-000451` (2026-07-16, 2Q26 실적 6-K) 의 구성:

| 파일 | 성격 | 결과 |
|---|---|---|
| `tsm-20260716x6k.htm` (17KB) | 6-K 표지 | 본문 없음 — 두 exhibit 을 참조만 한다 |
| `a2q26e_withguidancexfinal.htm` (28KB) | **Ex-99.1 press release** | **분기(3Q26) 매출·마진 가이던스만 있고 연간 capex 가이던스 없음** |
| `a2q26presentatione.htm` (9.6KB) + **JPG 12장** | Ex-99.2 실적 컨퍼런스 자료 | ⚠️ **슬라이드가 이미지다** |

**두 가지 구조적 사실**

1. **값이 표지가 아니라 exhibit 에 있다.** P3(C4)에서는 primary document 자체에 관측값이
   있었는데, 여기서는 **exhibit 을 지목하는 식별 계약**이 추가로 필요하다.
2. **Ex-99.2 는 이미지 슬라이드다.** 연간 capex 가이던스가 이 자료에만 있다면
   **텍스트 추출로는 닿지 않는다.**

**남은 확인** — TSMC 는 통상 **연간 capital budget 을 4분기/연간 실적 발표(1월)** 에서
제시한다. 그 릴리스에 텍스트로 존재하는지 확인해야 `RULE-0004` 의 취득 가능성이 닫힌다.
⛔ 확인 전까지 `PASS` 로 적지 않는다.

### `RULE-0021` MSFT Azure 45%cc 유의미 하회

**요구 관측값** (CIO 카드 1건): 공표된 **Azure 성장률의 constant-currency 값**.
45% 기준선보다 **3%p 이상** 낮으면 발동(정확히 3%p 포함).

**조사 결과 — XBRL 에는 없다.**

- `us-gaap` 매출 concept 은 존재하나(131 entries) **성장률 개념이 없다.**
- ⚠️ 「XBRL 은 퍼센트를 안 태깅한다」는 뜻이 **아니다** —
  `RevenueRemainingPerformanceObligationPercentage` 는 실제로 태깅돼 있다.
  즉 **퍼센트 태깅은 가능하지만 Azure 성장률은 태깅 대상이 아니다.**
- ⇒ 관측 원천은 **실적 press release(8-K Ex-99.1) 또는 10-Q MD&A 서술**이다.

### 2차 조사 — 둘 다 **문서 층에서는 FEASIBLE**

핵심은 **EDGAR full-text search(`efts.sec.gov`)** 였다. `submissions` 로는 후보를 좁힐 수
없었는데(대상 문서가 exhibit 이라 목록에 안 보인다), **내용으로 문서를 찾는 경로**가 열렸다.

#### `RULE-0004` — 연간 capital budget 은 **prose text 로 존재한다**

`0001046179-26-000008` (2026-01-15) · `a4q25e_withguidancexfinal.htm` (4Q25/연간 실적):

> **"The management further expects the 2026 capital budget to be between
> US$52 billion and US$56 billion."**

- **본문 텍스트**다 — 이미지가 아니다. 우려했던 슬라이드 이미지 문제는 이 값에는 해당하지 않는다.
- 카드가 요구하는 **범위 하단** = `US$52 billion` 을 그대로 얻을 수 있다.

**⚠️ 함께 확인된 두 가지**

1. **연 1회가 아니다.** `"capital budget"` 전문검색 결과 1월(4Q) 릴리스가 주축이지만
   **4월·7월·10월 릴리스에도 등장한 해가 있다**(2015-04 · 2016-07 · 2016-10 · 2017-10 ·
   2018-04 · 2019-10 · 2020-07 · 2021-04). 카드가 *"가장 최근 공식적으로 제시한"* 을 요구하므로
   **중간 갱신을 놓치면 안 된다.**
2. **모든 릴리스에 있는 것은 아니다.** 2Q26 릴리스에는 없었다. ⇒ collector 는
   *"이번 릴리스에 가이던스 없음"* 을 **오류가 아니라 정상 상태**로 처리해야 한다.

#### `RULE-0021` — Azure cc 성장률은 **명명된 표의 명명된 컬럼**에 있다

MSFT 실적 8-K 의 **`EX-99.1`** (`msft-ex99_1.htm`) — 전문검색으로 18개 분기 연속 확인.

표 이름: **`Selected Product and Service Revenue Constant Currency Reconciliation`**

| 항목 | 값 (FY2025 Q4 예) |
|---|---|
| Azure and other cloud services · Percentage Change Y/Y (GAAP) | 39% |
| Constant Currency Impact | 0% |
| **Percentage Change Y/Y Constant Currency** | **39%** |

P3 의 `TSMC {Month} Revenue Report (Consolidated)` 와 **같은 형태** — 명명된 표에서
명명된 컬럼을 집으면 된다.

**⚠️ 함정 두 가지 — 계약에 반영해야 한다**

1. **같은 문서 안에 다른 숫자가 있다.** CEO 인용문은 *"Azure surpassed \$75 billion in
   revenue, **up 34 percent**"* 라고 적는데 이는 표의 **39%** 와 다르다(측정 대상이 다름).
   ⛔ **산문에서 집으면 틀린다.** 관측 대상을 표로 못박아야 한다.
2. **이 분기는 GAAP 과 cc 가 같다**(Constant Currency Impact 0%). 이 분기만으로 테스트하면
   **두 컬럼을 구별하지 못한다.** 값이 갈리는 분기를 반드시 포함해야 한다.

#### 발견의 파급 — P2-b 전체

`efts.sec.gov` 전문검색은 **내용 기반 문서 발견**을 제공한다. P2-b·P2-c 처럼 값이
exhibit·narrative 에 있는 Rule 에서 *"어느 문서를 열어야 하는가"* 를 추측 없이 좁힐 수 있다.
⛔ 다만 **문서를 찾는 수단이지 값의 출처가 아니다** — 값은 원문에서 읽고 검증한다.

⛔ 두 Rule 모두 source 채택·collector 구현·상태 변경 없음.

---

## 경계 (유지)

`RULE-0022` 의 `DATA_MISSING` 미해제 · `RULE-0001` 은 `UNDEFINED` 로 되돌렸고
metric identity 는 **미확정**(계정과목 추정 금지) · collector 구현 없음 · source 채택 없음 · evaluator 미연결 · Production HOLD ·
`consumable_by_evaluator=false`.

---

# RULE-0021 — Gate CLOSED (CIO 판정 2026-08-16)

## 판정

```
definition = DEFINED
data       = MISSING          → AVAILABLE
source     = SOURCE_UNRESOLVED → SOURCE_RESOLVED
evaluator  = BLOCKED → READY   (파생 · derive 함수가 계산)
blocked_by = [DATA_MISSING, SOURCE_UNRESOLVED] → []

acquisition verification = PASS
extraction identity      = PASS
```

⛔ **evaluator 연결은 여전히 금지**다. `consumable_by_evaluator=false` ·
Production HOLD 유지. Rule 의 **데이터 확보 문제**가 해결된 것이지 실행 연결이
승인된 것이 아니다.

## 근거 (live run 2차 · `70f1d2b`)

| 조건 | 결과 |
|---|---|
| discovery 4건 | ✅ |
| primary `<TYPE>EX-99.1` 결정론적 식별 | ✅ 4/4 |
| secondary index cross-check | ✅ 4/4 |
| exhibit 취득 | ✅ 4/4 |
| extraction identity | ✅ 4/4 |
| provenance · 값 출력 | ✅ 4/4 |

관측값 — live · 저장소 raw fixture 회귀 · 외부 출처 **3원 일치**:

```
FY26 Q1 2025-10-29   40 / (1) / 39      FY26 Q3 2026-04-29   40 / (1) / 39
FY26 Q2 2026-01-28   39 / (1) / 38      FY26 Q4 2026-07-29   43 /  0  / 43
```

## 경위 — 두 번의 실패가 무엇을 고쳤나

1. **1차 live run 4/4 실패** — `index.json` 의 `type` 을 SEC document type 으로
   오인. 실측 결과 `text.gif` · `compressed.gif` 뿐인 **디렉터리 아이콘**이었다.
   → primary identity 를 full submission `.txt` 의 `<DOCUMENT>` `<TYPE>` 로 교체.
2. **2차 live run 2/4 실패** — Microsoft 가 문면을 바꿨다. 표 제목
   `Revenue → Information`, 행 라벨에 `revenue` suffix 추가.
   → 관측된 두 형태만 **폐쇄 열거**. `.*` 일반화 금지.
   ★ 변경이 **두 개**였다 — 제목만 고쳤다면 행 라벨에서 다시 막혔다.
3. 두 번 다 **fail-closed 로 멈췄고 틀린 값을 만들지 않았다.** 이것이 설계 의도다.

---

# ⚠️ 별건 결함 — `build_header` 오염 (OPEN)

## 상태

```
build_header contamination defect = OPEN
```

⛔ `RULE-0021` 의 Gate CLOSED 가 이 결함을 닫지 않는다 (CIO 판정 2026-08-16).
   이번 성공은 **현재 네 문서에서 우연히 안전했다**는 증거이지 parser 가
   구조적으로 안전하다는 증거가 아니다.

## 증거 (live run 로그 원문)

```
header[0] = 'Three Months Ended June 30, 2026 Microsoft Cloud revenue
             Commercial remaining performance obligation
             Microsoft 365 Commercial cloud revenue … Dynamics 365 revenue'
```

`build_header` 가 Azure 행 **위 모든 data-row 의 라벨과 값**을 헤더 문자열에
흡수한다. 컬럼 분류는 헤더 문자열의 단어로 하므로, 어떤 **행 라벨**이
`constant currency` 또는 `percentage change` 를 포함하게 되면 컬럼 0 이 cc 나
gaap 으로 오분류되어 **조용히 틀린 값**이 나올 수 있다.

★ 위험이 커진 이유: 신형 문면부터 **행 라벨에 지표명이 들어가기 시작했다.**

## 불변식 (CIO 제시)

> column identity 는 Azure 행 **위의 다른 data-row 내용에 영향을 받아서는 안 된다.**

## 다음 Gate 순서 (CIO 판정)

1. 이미 확보한 실제 4개 fixture 로 **fault injection** — 현재 parser 가 silent
   misclassification 가능한지 **먼저 깨뜨려 증명**한다. ⛔ 새 네트워크 조사 불필요.
2. 증명된 뒤에만 수정안을 올린다.
3. `0013/0014` · `0004` · P2-c 로 범위를 넓히기 **전에** 닫는다 —
   이후 P2-b/c 의 표 기반 collector 에도 재사용될 **parser 계층 문제**이기 때문이다.
