# B5-2 — CIO 결정 대상 40건 (worksheet)

> 이 문서는 **B5-1 · B5-1A 두 artifact 에서 질문만 뽑아 조립한 것**이다. 새 내용 0건.
> ⛔ 후보값·권장값·범위 없음. 답은 CIO 가 채운다.

**총 40건** = B5-1 의미 질문 34 + B5-1A 측정 대상 질문 6

## 종목별

### RULE-0002 · MU · FAL
> `DRAM ASP 하락 전환`

- occurrence: `MU::탈락 조건#3`
- 결핍: `threshold` · `time_window` · `comparison_baseline` · `data_source`
- data_source 성격: **both**

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `threshold` | '하락'의 폭을 어떤 기준으로 정의할 것인가? |  |
| 2 | B5-1 | `time_window` | '전환'을 선언하기까지의 기간을 어떻게 정의할 것인가? |  |
| 3 | B5-1 | `comparison_baseline` | 무엇 대비 하락으로 볼 것인가? |  |
| 4 | B5-1A | `data_source` | 'DRAM ASP'를 어느 가격 계열로 정의할 것인가? |  |

### RULE-0018 · 000660.KS · FAL
> `DRAM ASP 하락 전환`

- occurrence: `000660.KS::탈락 조건#3`
- 결핍: `threshold` · `time_window` · `comparison_baseline` · `data_source`
- data_source 성격: **both**

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `threshold` | '하락'의 폭을 어떤 기준으로 정의할 것인가? |  |
| 2 | B5-1 | `time_window` | '전환'을 선언하기까지의 기간을 어떻게 정의할 것인가? |  |
| 3 | B5-1 | `comparison_baseline` | 무엇 대비 하락으로 볼 것인가? |  |
| 4 | B5-1A | `data_source` | 'DRAM ASP'를 어느 가격 계열로 정의할 것인가? |  |

### RULE-0004 · TSM · FAL
> `capex 하향`

- occurrence: `TSM::탈락 조건#3`
- 결핍: `threshold` · `comparison_baseline` · `data_source`
- data_source 성격: **semantic target ambiguity**

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `threshold` | '하향'의 폭을 어떤 기준으로 정의할 것인가? |  |
| 2 | B5-1 | `comparison_baseline` | 무엇 대비 하향으로 볼 것인가? |  |
| 3 | B5-1A | `data_source` | 'capex 하향'의 측정 대상을 누구의 capex 로 정의할 것인가? |  |

### RULE-0009 · TSM · ENT
> `B 박스권 돌파 후 재확인`

- occurrence: `TSM::진입 패턴#1`
- 결핍: `event_definition` · `threshold` · `time_window`

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `event_definition` | '박스권 돌파'와 '재확인'을 어떤 기계적 조건으로 정의할 것인가? |  |
| 2 | B5-1 | `threshold` | 박스권 상단과 돌파 폭을 어떤 기준으로 정의할 것인가? |  |
| 3 | B5-1 | `time_window` | '재확인'에 필요한 기간을 어떻게 정의할 것인가? |  |

### RULE-0010 · CRDO · FAL
> `ANET 대비 상대강도 열위 지속`

- occurrence: `CRDO::탈락 조건#1`
- 결핍: `event_definition` · `time_window` · `comparison_baseline` · `data_source`
- data_source 성격: **data capability gap**

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `event_definition` | '상대강도'와 '열위'를 어떤 기계적 조건으로 정의할 것인가? |  |
| 2 | B5-1 | `time_window` | '지속'의 기간을 어떻게 정의할 것인가? |  |
| 3 | B5-1 | `comparison_baseline` | 상대강도를 어떤 기준선 대비로 산출할 것인가? |  |

### RULE-0011 · CRDO · FAL
> `고객 집중 심화(10%+ 고객 3개 미만)`

- occurrence: `CRDO::탈락 조건#3`
- 결핍: `observation_frequency` · `data_source`
- data_source 성격: **data capability gap**

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `observation_frequency` | 고객 집중도를 어느 관측 주기로 볼 것인가? |  |

### RULE-0012 · ANET · FAL
> `호실적+주가 하락(선반영)`

- occurrence: `ANET::탈락 조건#1`
- 결핍: `event_definition` · `threshold` · `time_window`

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `event_definition` | '호실적'과 '선반영'을 어떤 기계적 조건으로 정의할 것인가? |  |
| 2 | B5-1 | `threshold` | 주가 하락의 폭을 어떤 기준으로 정의할 것인가? |  |
| 3 | B5-1 | `time_window` | 판정에 쓰는 기간을 어떻게 정의할 것인가? |  |

### RULE-0015 · NVDA · FAL
> `하이퍼스케일러 2곳+ capex 하향`

- occurrence: `NVDA::탈락 조건#3`
- 결핍: `threshold` · `comparison_baseline` · `data_source`
- data_source 성격: **both**

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `threshold` | '하향'의 폭을 어떤 기준으로 정의할 것인가? |  |
| 2 | B5-1 | `comparison_baseline` | 무엇 대비 하향으로 볼 것인가? |  |
| 3 | B5-1A | `data_source` | '하이퍼스케일러'의 대상 범위를 어떻게 정의할 것인가? |  |

### RULE-0016 · NVDA · ENT
> `실적 전 포지션 제한(PM)`

- occurrence: `NVDA::다음 이벤트#2`
- 결핍: `time_window` · `threshold`

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `time_window` | '실적 전'의 기간을 어떻게 정의할 것인가? |  |
| 2 | B5-1 | `threshold` | '포지션 제한'의 수준을 어떤 기준으로 정의할 것인가? |  |

### RULE-0017 · 000660.KS · FAL
> `HBM 예약 취소·LTA 축소`

- occurrence: `000660.KS::탈락 조건#1`
- 결핍: `event_definition` · `threshold` · `data_source`
- data_source 성격: **both**

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `event_definition` | '취소'와 '축소'를 어떤 기계적 조건으로 정의할 것인가? |  |
| 2 | B5-1 | `threshold` | '축소'의 폭을 어떤 기준으로 정의할 것인가? |  |
| 3 | B5-1A | `data_source` | '예약'과 'LTA'를 어느 관측 대상에서 읽을 것인가? |  |

### RULE-0019 · 005930.KS · FAL
> `SK 대비 상대강도 지속 열위`

- occurrence: `005930.KS::탈락 조건#1`
- 결핍: `event_definition` · `time_window` · `comparison_baseline`

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `event_definition` | '상대강도'와 '열위'를 어떤 기계적 조건으로 정의할 것인가? |  |
| 2 | B5-1 | `time_window` | '지속'의 기간을 어떻게 정의할 것인가? |  |
| 3 | B5-1 | `comparison_baseline` | 상대강도를 어떤 기준선 대비로 산출할 것인가? |  |

### RULE-0020 · 005930.KS · FAL
> `HBM 공급 확대 미확인`

- occurrence: `005930.KS::탈락 조건#3`
- 결핍: `event_definition` · `threshold` · `data_source`
- data_source 성격: **both**

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `event_definition` | '공급 확대'와 '미확인'을 어떤 기계적 조건으로 정의할 것인가? |  |
| 2 | B5-1 | `threshold` | '확대'의 폭을 어떤 기준으로 정의할 것인가? |  |
| 3 | B5-1A | `data_source` | 'HBM 공급 확대'를 어느 관측 대상으로 볼 것인가? |  |

### RULE-0021 · MSFT · FAL
> `Azure 성장 45%cc 유의미 하회`

- occurrence: `MSFT::탈락 조건#1`
- 결핍: `event_definition` · `data_source`
- data_source 성격: **data capability gap**

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `event_definition` | '유의미 하회'를 어떤 기준으로 정의할 것인가? |  |

### RULE-0022 · MSFT · FAL
> `RPO 급둔화`

- occurrence: `MSFT::탈락 조건#3`
- 결핍: `threshold` · `time_window` · `comparison_baseline` · `data_source`
- data_source 성격: **data capability gap**

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `threshold` | '급둔화'의 '급'을 어떤 기준으로 정의할 것인가? |  |
| 2 | B5-1 | `time_window` | 어느 기간을 보고 판정할 것인가? |  |
| 3 | B5-1 | `comparison_baseline` | 무엇 대비 둔화로 볼 것인가? |  |

### RULE-0025 · 298040.KS · FAL
> `기관 순매수 연속 끊김`

- occurrence: `298040.KS::탈락 조건#5`
- 결핍: `time_window`

| # | 출처 | 결핍 항목 | 결정해야 할 질문 | CIO 결정 |
|---|---|---|---|---|
| 1 | B5-1 | `time_window` | '연속'을 몇 거래일로 정의할 것인가? |  |

## 결정하지 않아도 되는 것 (참고)

아래 4건의 `data_source` 결핍은 **측정 대상이 이미 명확**하고 수집기만 없는 문제라 정의 질문으로 올리지 않았다. 별도 축이다.

- `RULE-0010` · CRDO — `ANET 대비 상대강도 열위 지속`
- `RULE-0011` · CRDO — `고객 집중 심화(10%+ 고객 3개 미만)`
- `RULE-0021` · MSFT — `Azure 성장 45%cc 유의미 하회`
- `RULE-0022` · MSFT — `RPO 급둔화`
