# 다음 READY 후보 — 정본 상태 전수 조사 (CIO 지시 2026-08-16)

⛔ **조사만이다. 코드 변경 0줄 · Rule 상태 변경 0건 · 승격 미착수.**
⛔ **어느 Rule 을 다음에 올릴지 적지 않는다.** 후보 선정은 CIO 판정 사항이다.
⛔ 정의·임계·측정 대상·데이터 출처를 **추정하지 않는다.**

---

## 0. 직전 CIO 판정 기록

```
별건 4                        CLOSED
mutation infrastructure       authoritative 인정
historical mutation evidence  authoritative 인정
「69/69 KILLED」의 의미        Atlas 전체가 완벽하다는 뜻이 아니다 —
                              **현재 catalog 에 정의된 69개 결함에 대해 해당 회귀가
                              의도한 판별력을 재현 가능하게 보유한다**는 의미로만 쓴다.
별건 1~3 · 5~7                OPEN 보존 · 지금 221 을 0 으로 만들러 가지 않는다
다음 우선순위                  테스트 인프라가 아니라 **Rule 검증 파이프라인 복귀**
```

## 1. 현재 정본 상태 — 25개 evaluation rule 전수

```
UNDEFINED 3 · MISSING 18 · SOURCE_UNRESOLVED 12 · BLOCKED 18 · READY 7
consumable_by_evaluator = false · Production HOLD
```

| rule | subject | definition | data | source_qual | eval | blocked_by |
|---|---|---|---|---|---|---|
| 0001 | MU | **UNDEFINED** | MISSING | UNRESOLVED | BLOCKED | 정의·데이터·출처 |
| 0002 | MU | DEFINED | MISSING | UNRESOLVED | BLOCKED | 데이터·출처 |
| **0003** | TSM | DEFINED | AVAILABLE | RESOLVED | **READY** | — |
| 0004 | TSM | DEFINED | MISSING | UNRESOLVED | BLOCKED | 데이터·출처 |
| 0005 | TSM | DEFINED | MISSING | UNRESOLVED | BLOCKED | 데이터·출처 |
| 0006 | TSM | DEFINED | MISSING | UNRESOLVED | BLOCKED | 데이터·출처 |
| **0007** | TSM | DEFINED | AVAILABLE | RESOLVED | **READY** | — |
| **0008** | TSM | DEFINED | AVAILABLE | RESOLVED | **READY** | — |
| 0009 | TSM | **UNDEFINED** | MISSING | UNRESOLVED | BLOCKED | 정의·데이터·출처 |
| 0010 | CRDO | DEFINED | MISSING | UNRESOLVED | BLOCKED | 데이터·출처 |
| 0011 | CRDO | DEFINED | MISSING | *(없음)* | BLOCKED | 데이터 |
| 0012 | ANET | DEFINED | MISSING | UNRESOLVED | BLOCKED | 데이터·출처 |
| 0013 | ANET | DEFINED | MISSING | UNRESOLVED | BLOCKED | 데이터·출처 |
| 0014 | NVDA | DEFINED | MISSING | UNRESOLVED | BLOCKED | 데이터·출처 |
| 0015 | NVDA | DEFINED | MISSING | *(없음)* | BLOCKED | 데이터 |
| 0016 | NVDA | **UNDEFINED** | MISSING | UNRESOLVED | BLOCKED | 정의·데이터·출처 |
| 0017 | 000660.KS | DEFINED | MISSING | *(없음)* | BLOCKED | 데이터 |
| 0018 | 000660.KS | DEFINED | MISSING | UNRESOLVED | BLOCKED | 데이터·출처 |
| **0019** | 005930.KS | DEFINED | AVAILABLE | *(없음)* | **READY** | — |
| 0020 | 005930.KS | DEFINED | MISSING | *(없음)* | BLOCKED | 데이터 |
| **0021** | MSFT | DEFINED | AVAILABLE | RESOLVED | **READY** | — |
| 0022 | MSFT | DEFINED | MISSING | *(없음)* | BLOCKED | 데이터 |
| 0023 | 298040.KS | DEFINED | MISSING | *(없음)* | BLOCKED | 데이터 |
| **0024** | 298040.KS | DEFINED | AVAILABLE | *(없음)* | **READY** | — |
| **0025** | 298040.KS | DEFINED | AVAILABLE | *(없음)* | **READY** | — |

### BLOCKED 18 의 분해

```
정의가 막는다 (UNDEFINED)        3건   RULE-0001 · 0009 · 0016   ← CIO 전용. 내가 열 수 없다
정의는 있고 데이터가 막는다       15건
  ├ source_qualification 미해결   10건
  └ source_qualification 미기재    5건   RULE-0011 · 0015 · 0017 · 0020 · 0023
```

## 2. ★ 새로 관측한 것 — READY 7 안의 provenance 비대칭

```
acquisition contract 을 통과한 READY   4건   RULE-0003 · 0007 · 0008 · 0021
  data_status: MISSING → AVAILABLE · source_qualification: UNRESOLVED → RESOLVED
  `data_capability_application` 기록 있음 · collector 와 회귀가 정본에 있음

계약 없이 처음부터 AVAILABLE 인 READY   3건   RULE-0019 · 0024 · 0025
  data_status_before_application = AVAILABLE · source_qualification = **없음**
  `data_capability_application` = null
```

⇒ **READY 7 은 균질하지 않다.** 4건은 취득 계약을 통과했고, 3건은 그 축을 지나지
   않았다. `derive_evaluator_status` 는 (DEFINED, AVAILABLE) 로 READY 를 내므로
   `source_qualification` 이 비어 있어도 READY 가 된다.

⛔ 「그러므로 3건을 강등해야 한다」고 적지 않는다. **관측 사실만 올린다.**
   이 3건이 어떤 근거로 AVAILABLE 이 됐는지는 정본에 기록이 없다.

## 3. ★ 또 하나 — `condition_semantics` 는 25건 전부 `UNRESOLVED` 다

```
condition_semantics   UNRESOLVED 25 / 25   (READY 7 포함)
scope                 UNRESOLVED 25 / 25   (READY 7 포함)
```

⇒ `definition_status = DEFINED` 와 `condition_semantics = RESOLVED` 는 **다른 축**이며,
  현재 후자는 **어느 Rule 도 통과하지 않았다.** READY 는 「평가기가 소비할 수 있는
  상태」이지 「조건 문면의 의미가 확정됐다」는 뜻이 아니다.

⛔ 이것이 결함인지, 애초에 다른 층의 값인지는 **판정 사항**이다. 나는 값만 보고한다.

## 4. 이미 확보한 취득 경로와의 인접성 — 사실만

정본에 **작동하는 collector 와 Actions-gated 회귀**가 있는 취득 경로는 둘이다.

```
TSMC 월매출 6-K      collectors/c4_sec_edgar_check.py     RULE-0003 · 0007 · 0008
MSFT 8-K EX-99.1     collectors/msft_azure_cc.py          RULE-0021
```

### 4-1. `RULE-0022` (MSFT · 「RPO 급둔화」) — 같은 문서·같은 표에 문면이 있다

이미 캡처해 둔 MSFT fixture **4건 전부**에서 관측된다.

```
2025-10-29  … remaining performance obligation   51%  0%   51% …
2026-01-28  … remaining performance obligation  110%  0%  110% …
2026-04-29  … remaining performance obligation   99%  0%   99% …
2026-07-29  … remaining performance obligation   84%  0%   84% …
```

⇒ RULE-0021 이 쓰는 **바로 그 표(Selected Product and Service … Constant Currency
  Reconciliation)** 안에 있다. 새 취득 경로가 필요하지 않다는 **관측**이다.

⛔ 그러나 「급둔화」의 정의·임계·측정 대상은 **정본에 없다.** 위 숫자가 어떤 계열인지
   (전년 대비 증가율인지 잔량 자체인지)도 이 조사에서 판정하지 않았다.
   ⛔ 정의 없이 데이터가 옆에 있다는 사실만으로 승격 후보라고 적지 않는다.

### 4-2. TSM 의 나머지 4건은 현재 경로와 문면이 다르다

```
RULE-0004  capex 하향        ← 월매출 6-K 문면이 아니다
RULE-0005  종가 $398 이탈    ← 가격 계열
RULE-0006  종가 SMA20 $409   ← 가격 계열
RULE-0009  B 박스권 돌파      ← 가격 계열 · 정의 UNDEFINED
```

⇒ TSM 이 READY 3건을 갖고 있다고 해서 나머지가 같은 경로로 열리지 않는다.

## 5. 조건 문면 기준 분류 (⛔ 데이터 출처 판정이 아니다 · 문면만 센다)

```
가격 문면        3건   RULE-0005 · 0006 · 0009      (종가 · SMA · 박스권)
상대·수급 문면    2건   RULE-0010 · 0025(READY)      (상대강도 · 순매수)
재무·실적 문면   12건   나머지 대부분
미분류           1건   RULE-0001 「FQ4 $49B 미달」 — 측정 대상 자체가 없다
```

★ **조건 문면이 완전히 같은 Rule 이 한 쌍 있다** (sha256 동일):

```
RULE-0002 [MU]        「DRAM ASP 하락 전환」
RULE-0018 [000660.KS] 「DRAM ASP 하락 전환」
```

⇒ 같은 문면이 **다른 종목에 걸려 있다.** 정의와 데이터 요구가 공유되는지,
  종목별로 달라지는지는 **판정 사항**이다.

## 6. 판정에 필요한 사실만

1. BLOCKED 18 중 **3건은 정의가 막는다** (RULE-0001 · 0009 · 0016). 나는 열 수 없다.
2. 나머지 15건은 전부 **데이터가 막는다.** 그중 5건은 `source_qualification` 자체가
   기재돼 있지 않다.
3. **READY 7 은 균질하지 않다** — 4건만 취득 계약을 통과했고, 3건은 그 축의 기록이 없다.
4. `condition_semantics` 는 **READY 를 포함해 25건 전부 UNRESOLVED** 다.
5. 이미 확보한 취득 경로 안에 문면이 있는 BLOCKED Rule 은 **RULE-0022 하나**가
   관측됐다. 다만 그 조건의 정의·임계는 정본에 없다.
6. 조건 문면이 같은 Rule 이 한 쌍(RULE-0002 · 0018) 있다.

⛔ 권고를 적지 않는다. 다음 후보 선정과 Gate 범위는 CIO 판정 사항이다.

## 7. 상태

```
별건 4                            CLOSED
mutation infrastructure           CLOSED @ 11ee80d · authoritative 인정
historical mutation evidence      AUTHORITATIVE @ 350948b
  KILLED 69 · SURVIVED 0 · MISATTRIBUTED 0 · NOT_APPLICABLE 0 · INVALID_RUN 0
  ⛔ 「69/69」는 catalog 69개 결함에 대한 재현 가능한 판별력만을 뜻한다
CI 5th gate                       NOT APPROVED — 유지

OPEN · 미착수
  별건 1  정적 검사가 문자열 검색
  별건 2  단언이 이름보다 약한 검사
  별건 3  fixture 가 판별하지 못함
  별건 5  회귀의 기존 조용한 건너뜀 45건
  별건 6  층 1 미적용 지점 (ERROR 21건의 출처)
  별건 7  NameError 연쇄
  ★ 신규 관측 A  READY 7 안의 provenance 비대칭 (0019 · 0024 · 0025)
  ★ 신규 관측 B  condition_semantics 가 25/25 UNRESOLVED

C4 parser/selection/build_header  CLOSED
TSMC fixture coverage cardinality CLOSED @ 98bd6a7
P3 · RULE-0003/0007/0008          CLOSED · READY 유지
capture unit metadata             OBSERVABILITY DEBT · HOLD
FI-3 frozen input tamper          KNOWN GAP · NOT GATED
```

```
UNDEFINED 3 · MISSING 18 · SOURCE_UNRESOLVED 12 · BLOCKED 18 · READY 7
consumable_by_evaluator = false · Production HOLD
```
