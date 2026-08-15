# Definition Invariants — 안건 등록 (2026-08-15)

⛔ **이 문서는 안건과 발견 근거만 담는다.** invariant 구현·전 Rule 재작성은 아직 하지 않는다.

2026-08-15 의 `RULE-0001` 조사에서 **서로 다른 failure mode 세 개**가 드러났다.
셋은 원인도 고칠 자리도 다르므로 **합치지 않는다.**

| 결함 | 질문 | 상태 |
|---|---|---|
| **A. Metric Identity Completeness** | Rule 자체에 "무엇을 측정하는가" 가 있는가 | **안건 등록** |
| **B. Context-Carry Completeness** | decomposition 하면서 필요한 문맥을 잃지 않았는가 | **안건 등록** |
| **C. Vocabulary Enforcement** | 상태 필드가 허용된 enum 밖으로 나갈 수 없는가 | ✅ **수정 완료** |
| **D. Comparison Baseline Completeness** | 방향성 조건이 **무엇과 비교하는지** 명시돼 있는가 | **안건 등록** |

---

## A. Metric Identity Completeness

### 발견 근거 — `RULE-0001`

```
condition_text        「FQ4 $49B 미달」
definition_status     DEFINED          ← 당시
missing_components    []               ← "결핍 없음"
cio_definition_decisions  0건          ← 정의 카드가 열린 적 없음
```

**숫자 threshold 는 있는데 그 숫자가 무엇을 측정하는지가 없다.**
원천 `_watchlist_rows.json` 의 MU 행 원문도 `FQ4 $49B 미달 또는 DRAM ASP 하락 전환` 이며
계정과목이 없다 — decomposition 손실이 아니라 **처음부터 부재**다.

현재 completeness gate 는 threshold 의 **존재**는 보지만 **metric identity** 는 보지 않는다.

### 제안하는 불변식 (미구현)

threshold 또는 비교 연산이 있는 Rule 은 최소한 다음 구조가 식별되어야 한다.

```
metric + operator + threshold (+ comparison basis, 필요한 경우)
```

`$49B 미달` 만 있고 metric 이 없으면 `DEFINED` 가 될 수 없다.

### 전수점검 결과 (2026-08-15)

정의 카드가 한 번도 열리지 않은 **control population 10건**
(`0001 · 0003 · 0005 · 0006 · 0007 · 0008 · 0013 · 0014 · 0023 · 0024`)을 검사했다.

- **명확한 반례: `RULE-0001` 1건** → `UNDEFINED` 로 reopen 완료
- 나머지는 측정 대상이 문면에 있거나(`월매출` · `종가` · `매출`) threshold 자체가 없다

⛔ 전체 25 Rule 재검증은 하지 않는다. 이미 닫은 정의를 불필요하게 흔들게 된다.

---

## B. Context-Carry Completeness

### 발견 근거 — `RULE-0007` · `RULE-0008`

```
condition_text  「약화 = 단월 YoY < +35% OR 누계 YoY < +34.6% → C(매수 취소·Ready 해제)」
```

**`YoY` 는 비교 형식이지 측정 대상이 아니다.** 무엇의 YoY 인지 조각에 없다.

원문 문맥에는 있다 —

> ★ 8/10경 TSMC **7월 월매출** — Ready Action Plan 발동점 … 약화 = **단월 YoY** < +35% …

즉 같은 cell 앞머리의 「7월 월매출」이 측정 대상을 공급하는데,
**decomposition 이 fragment 단위로 자르면서 그 의미가 조각에서 사라졌다.**

`RULE-0001`(원문에도 없음)과는 **다른 결함**이다.

### 제안하는 불변식 (미구현)

원문의 상위 문맥이 fragment 의미에 필수적이면, decomposition 이 그 의미를
**fragment 의 구조화 필드로 carry** 해야 한다.

> fragment 단독으로 evaluation 의미를 재구성할 수 없고 parent context 를 다시 읽어야 한다면
> decomposition 은 불완전하다.

### 조치 (2026-08-15)

상태는 되돌리지 않았다. `RULE-0007·0008` 의 `AVAILABLE` · `SOURCE_RESOLVED` 는
P3 판정 그대로 유지하고, **누락된 provenance 만** `context_provenance` 로 소급 기록했다.
성격은 `retroactive clarification / context provenance` 이며 신규 정의가 아니다.

---

## C. Vocabulary Enforcement — 수정 완료

### 발견 근거

`SOURCE_RESOLVED` 가 P3 승격에 실렸는데 **회귀 16/16 · Actions PASS 를 통과했다.**
`rules/vocabulary.py` 의 `SOURCE_QUALIFICATION` 에 그 값이 **없었는데도** 통과했다.

원인: 어휘 검사(`VOCAB`)가 `validate_decomposition.py` 의 **분해 단계(fragment)** 에만
걸려 있었고, `config/rules.json` · `rules/rule_inventory.json` 같은 **하류 authoritative
산출물** 에는 없었다. **승격 단계에서 만들어진 값은 어떤 검사도 통과할 필요가 없었다.**

### 수정

- `SOURCE_RESOLVED` 를 `SOURCE_QUALIFICATION` 에 정식 등록 (값은 되돌리지 않는다 — CIO 승인값)
- `vocabulary.vocab_violations()` 신설 — 새 어휘를 만들지 않고 폐쇄 집합을 **강제**만 한다
- 검사 대상 7필드: `definition_status · data_status · data_capability ·
  source_qualification · evaluator_status · rule_kind · downstream_effect`
- `promote_rules_ssot.py` · `rule_inventory.py` 양쪽에 배선
- 회귀: 정상 25건 통과 + **위조값 6종 거부** + 배선 여부 정적 확인
- **FI-6 invalid vocabulary value** — authoritative 산출물에 오타 1글자를 넣으면 실패해야 한다

### 마무리 — `blocked_by` 어휘 폐쇄 (CIO 판정 2026-08-15)

`blocked_by` 원소 어휘가 `derive_blocked_by` 안에만 있고 폐쇄 집합으로 선언돼 있지 않던
문제를 닫았다.

- `BLOCKED_BY` 선언 — **새 어휘가 아니라** `derive_blocked_by` 가 이미 내보내던 6개 값
  (`DEFINITION_UNDEFINED` · `DATA_MISSING` · `DATA_UNDETERMINED` · `SOURCE_UNRESOLVED` ·
  `SOURCE_UNVERIFIED` · `STATUS_UNRESOLVED`)
- `VOCAB_LIST` — 목록형 필드는 스칼라와 검사 방식이 다르므로 따로 둔다.
  원소 어휘 · **중복 원소** · **목록이 아닌 값** 을 각각 잡는다.
- **`covers_derive_outputs()`** — 선언과 함수의 실제 출력이 **정확히 일치**하는지 양방향 확인.
  선언이 좁으면 정상 산출물이 위반으로 잡히고, 넓으면 오타를 못 잡는다. 둘 다 결함이다.
  `definition_status × data_status × source_qualification` 전 조합을 돌려 도달 가능한
  출력을 전부 모은 뒤 대조한다.
- 회귀는 **선언을 일부러 좁혀** 정상값이 걸리는 것까지 확인한다 — 통과만 보는 검사를 두지 않는다.
- FI-6 을 목록형까지 확장했다.

---

## D. Comparison Baseline Completeness

### 질문

> 방향성 조건(하향 · 상회 · 미달 · 약화 · 개선 · 둔화 · 열위 …)이 있다면
> **무엇과 비교하는지**가 명시돼 있는가?

### 발견 근거 — 같은 언어가 다르게 처리됐다

방향성 동사를 가진 Rule **18건**을 전수 대조한 결과, **같은 동사인데 처리가 갈렸다.**

| Rule | 원문 | 처리 |
|---|---|---|
| `RULE-0004` | 「**capex 하향**」 | `threshold` + `comparison_baseline` + `data_source` 카드 **3건** |
| `RULE-0015` | 「하이퍼스케일러 2곳+ **capex 하향**」 | 동일하게 **3건** |
| **`RULE-0013`** | 「AI 매출 목표 **하향** 시 CRDO에 슬롯 이양」 | **0건 · `missing_components=[]`** |

`RULE-0013` 은 *무엇 대비* 하향인지 · *얼마나* 하향인지 · *어디서 관측하는지* 가 모두 없는데
결핍 없음으로 분류돼 있다.

18건 중 `comparison_baseline` 이 다뤄진 것은 **7건**이다.

### 제안하는 불변식 (미구현)

방향성 조건에 **comparison reference** 가 필요한데 정본에 없으면 `DEFINED` 로 둘 수 없다.

### ⛔ 예외 — 모든 방향성 Rule 에 baseline 을 요구하지 않는다

baseline 이 **본질적으로 불필요한** 조건이 있다. 이들을 위반으로 잡으면 오탐이다.

| 예외 유형 | 예 | 이유 |
|---|---|---|
| **절대 임계값** | `RULE-0005` 「종가 기준 $398 이탈」 · `RULE-0024` 「1,894,000원 재이탈」 | 비교 대상이 **고정 수준**이며 그 자체가 기준이다 |
| **사건형** | `RULE-0017` 「HBM 예약 취소·LTA 축소」 · `RULE-0020` 「HBM 공급 확대 미확인」 | 사건의 발생 여부가 조건이며 비교 계열이 아니다 |

⇒ 이 불변식은 **조건 유형 분류가 선행**되어야 적용할 수 있다.
분류 없이 일괄 적용하면 위 예외들이 전부 위반으로 잡힌다.

### 이번 발견의 한계 — 함께 기록한다

앞서 수행한 control population 전수점검은 **metric identity 축만** 검사했다.
그래서 `RULE-0013` · `RULE-0014` 를 통과시켰다 — 둘 다 metric 은 있고
**비교 기준이 없는** 경우였기 때문이다. **점검 축이 실제 완결성 질문보다 좁았다.**

### 이번 조치 (2026-08-15)

| Rule | 처리 |
|---|---|
| `RULE-0013` | **definition reopen 후보** — 취득 조사 금지. 대상 metric · 비교 baseline · 하향 threshold 필요 여부를 정의 카드로 올린다 |
| `RULE-0014` | **Comparison Baseline review** — metric identity 는 원문 문맥상 「다음 분기 매출 가이드 하단」 근거가 있으므로 **즉시 `UNDEFINED` 로 찍지 않는다.** 어느 FQ2/vintage 가이드와 실제값을 비교하는지가 불명확해 취득 조사만 보류 |

⛔ 두 Rule 모두 `config/rules.json` 의 상태를 **변경하지 않았다.**
⛔ invariant D 는 안건 등록만 한다 — 전 Rule 자동 재판정은 하지 않는다.

---

## 우선순위 근거

C 를 A·B 보다 먼저 고쳤다. **A·B 가 아무리 잘 작동해도 상태값 자체가 자유 문자열이면
이후 상태기계의 신뢰성이 깨지기 때문**이다. `SOURCE_RESOLVEDD` 같은 오타 하나가
정상 상태처럼 흐를 수 있었고, evaluator 연결 후였다면 발견이 훨씬 늦었을 것이다.
Production HOLD 상태에서 잡힌 것이 다행인 종류의 결함이다.
