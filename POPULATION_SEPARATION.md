# B2-1 Population Separation

**canonical rule_id 미부여 · dedup 미착수 · 병합 없음 · 새 객체 생성 없음** — occurrence index view 다.

총 occurrence **106** = evaluation_rules 25 + monitoring_inventory 16 + execution_references 21 + non_rule_evidence 44  *(portfolio_operation_candidates 1건은 위 population 중 하나를 가리키는 중복 index)*


## `evaluation_rules` — 25건

- rule_kind: `FAL` 21 · `ENT` 4
- definition_status: `UNDEFINED` 15 · `DEFINED` 10
- data_status: `MISSING` 22 · `AVAILABLE` 3
- source_qualification: `SOURCE_UNRESOLVED` 16 · `None` 9
- **evaluator_status: `BLOCKED` 24 · `READY` 1**
- 차단 원인(중복 계상): `DATA_MISSING` 22 · `SOURCE_UNRESOLVED` 16 · `DEFINITION_UNDEFINED` 15

| candidate_id | cell | # | kind | def | data | src_qual |
|---|---|---:|---|---|---|---|
| `MU::탈락 조건` | 탈락 조건 | 1 | FAL | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `MU::탈락 조건` | 탈락 조건 | 3 | FAL | UNDEFINED | MISSING | SOURCE_UNRESOLVED |
| `TSM::탈락 조건` | 탈락 조건 | 1 | FAL | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `TSM::탈락 조건` | 탈락 조건 | 3 | FAL | UNDEFINED | MISSING | SOURCE_UNRESOLVED |
| `TSM::기술적 무효화` | 기술적 무효화 | 1 | FAL | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `TSM::기술적 무효화` | 기술적 무효화 | 2 | ENT | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `TSM::다음 이벤트` | 다음 이벤트 | 3 | FAL | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `TSM::다음 이벤트` | 다음 이벤트 | 5 | ENT | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `TSM::진입 패턴` | 진입 패턴 | 1 | ENT | UNDEFINED | MISSING | SOURCE_UNRESOLVED |
| `CRDO::탈락 조건` | 탈락 조건 | 1 | FAL | UNDEFINED | MISSING | SOURCE_UNRESOLVED |
| `CRDO::탈락 조건` | 탈락 조건 | 3 | FAL | UNDEFINED | MISSING | None |
| `ANET::탈락 조건` | 탈락 조건 | 1 | FAL | UNDEFINED | MISSING | SOURCE_UNRESOLVED |
| `ANET::탈락 조건` | 탈락 조건 | 3 | FAL | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `NVDA::탈락 조건` | 탈락 조건 | 1 | FAL | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `NVDA::탈락 조건` | 탈락 조건 | 3 | FAL | UNDEFINED | MISSING | None |
| `NVDA::다음 이벤트` | 다음 이벤트 | 2 | ENT | UNDEFINED | MISSING | SOURCE_UNRESOLVED |
| `000660.KS::탈락 조건` | 탈락 조건 | 1 | FAL | UNDEFINED | MISSING | None |
| `000660.KS::탈락 조건` | 탈락 조건 | 3 | FAL | UNDEFINED | MISSING | SOURCE_UNRESOLVED |
| `005930.KS::탈락 조건` | 탈락 조건 | 1 | FAL | UNDEFINED | AVAILABLE | None |
| `005930.KS::탈락 조건` | 탈락 조건 | 3 | FAL | UNDEFINED | MISSING | None |
| `MSFT::탈락 조건` | 탈락 조건 | 1 | FAL | UNDEFINED | MISSING | SOURCE_UNRESOLVED |
| `MSFT::탈락 조건` | 탈락 조건 | 3 | FAL | UNDEFINED | MISSING | None |
| `298040.KS::탈락 조건` | 탈락 조건 | 1 | FAL | DEFINED | MISSING | None |
| `298040.KS::탈락 조건` | 탈락 조건 | 3 | FAL | DEFINED | AVAILABLE | None |
| `298040.KS::탈락 조건` | 탈락 조건 | 5 | FAL | UNDEFINED | AVAILABLE | None |

## `monitoring_inventory` — 16건

- rule_kind: `MON` 16
- definition_status: `UNRESOLVED` 16
- data_status: `UNRESOLVED` 16
- source_qualification: `None` 16

| candidate_id | cell | # | kind | def | data | src_qual |
|---|---|---:|---|---|---|---|
| `MU::다음 이벤트` | 다음 이벤트 | 1 | MON | UNRESOLVED | UNRESOLVED | None |
| `TSM::다음 이벤트` | 다음 이벤트 | 1 | MON | UNRESOLVED | UNRESOLVED | None |
| `TSM::다음 이벤트` | 다음 이벤트 | 8 | MON | UNRESOLVED | UNRESOLVED | None |
| `TSM::다음 이벤트` | 다음 이벤트 | 9 | MON | UNRESOLVED | UNRESOLVED | None |
| `CRDO::다음 이벤트` | 다음 이벤트 | 1 | MON | UNRESOLVED | UNRESOLVED | None |
| `CRDO::다음 이벤트` | 다음 이벤트 | 3 | MON | UNRESOLVED | UNRESOLVED | None |
| `CRDO::다음 이벤트` | 다음 이벤트 | 5 | MON | UNRESOLVED | UNRESOLVED | None |
| `CRDO::다음 이벤트` | 다음 이벤트 | 6 | MON | UNRESOLVED | UNRESOLVED | None |
| `ANET::다음 이벤트` | 다음 이벤트 | 1 | MON | UNRESOLVED | UNRESOLVED | None |
| `ANET::다음 이벤트` | 다음 이벤트 | 3 | MON | UNRESOLVED | UNRESOLVED | None |
| `ANET::다음 이벤트` | 다음 이벤트 | 4 | MON | UNRESOLVED | UNRESOLVED | None |
| `NVDA::다음 이벤트` | 다음 이벤트 | 1 | MON | UNRESOLVED | UNRESOLVED | None |
| `MSFT::다음 이벤트` | 다음 이벤트 | 1 | MON | UNRESOLVED | UNRESOLVED | None |
| `329180.KS::다음 이벤트` | 다음 이벤트 | 1 | MON | UNRESOLVED | UNRESOLVED | None |
| `SNDK::다음 이벤트` | 다음 이벤트 | 1 | MON | UNRESOLVED | UNRESOLVED | None |
| `SNDK::다음 이벤트` | 다음 이벤트 | 3 | MON | UNRESOLVED | UNRESOLVED | None |

## `execution_references` — 21건

- definition_status: `DEFINED` 21
- data_status: `MISSING` 21
- source_qualification: `SOURCE_UNRESOLVED` 21

| candidate_id | cell | # | kind | def | data | src_qual |
|---|---|---:|---|---|---|---|
| `MU::핵심 지지` | 핵심 지지 | 1 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `MU::핵심 저항` | 핵심 저항 | 1 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `TSM::핵심 지지` | 핵심 지지 | 1 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `TSM::핵심 지지` | 핵심 지지 | 3 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `TSM::핵심 저항` | 핵심 저항 | 1 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `TSM::핵심 저항` | 핵심 저항 | 3 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `CRDO::핵심 지지` | 핵심 지지 | 1 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `CRDO::핵심 지지` | 핵심 지지 | 3 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `CRDO::핵심 저항` | 핵심 저항 | 1 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `CRDO::핵심 저항` | 핵심 저항 | 3 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `ANET::핵심 지지` | 핵심 지지 | 1 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `ANET::핵심 지지` | 핵심 지지 | 3 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `ANET::핵심 저항` | 핵심 저항 | 1 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `ANET::핵심 저항` | 핵심 저항 | 3 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `NVDA::핵심 지지` | 핵심 지지 | 1 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `NVDA::핵심 지지` | 핵심 지지 | 3 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `NVDA::핵심 저항` | 핵심 저항 | 1 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `NVDA::핵심 저항` | 핵심 저항 | 3 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `MSFT::핵심 지지` | 핵심 지지 | 1 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `MSFT::핵심 저항` | 핵심 저항 | 1 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |
| `MSFT::핵심 저항` | 핵심 저항 | 3 | UNRESOLVED | DEFINED | MISSING | SOURCE_UNRESOLVED |

## `non_rule_evidence` — 44건

- definition_status: `UNRESOLVED` 44
- data_status: `UNRESOLVED` 44
- source_qualification: `None` 44
- 내역: 결합 표기 24 · 주석·부재표식 20

| candidate_id | cell | # | kind | def | data | src_qual |
|---|---|---:|---|---|---|---|
| `TSM::기술적 무효화` | 기술적 무효화 | 3 | None | UNRESOLVED | UNRESOLVED | None |
| `TSM::다음 이벤트` | 다음 이벤트 | 2 | None | UNRESOLVED | UNRESOLVED | None |
| `TSM::다음 이벤트` | 다음 이벤트 | 6 | None | UNRESOLVED | UNRESOLVED | None |
| `TSM::다음 이벤트` | 다음 이벤트 | 7 | None | UNRESOLVED | UNRESOLVED | None |
| `000660.KS::다음 이벤트` | 다음 이벤트 | 1 | None | UNRESOLVED | UNRESOLVED | None |
| `000660.KS::핵심 지지` | 핵심 지지 | 1 | None | UNRESOLVED | UNRESOLVED | None |
| `000660.KS::핵심 저항` | 핵심 저항 | 1 | None | UNRESOLVED | UNRESOLVED | None |
| `005930.KS::다음 이벤트` | 다음 이벤트 | 1 | None | UNRESOLVED | UNRESOLVED | None |
| `005930.KS::핵심 지지` | 핵심 지지 | 1 | None | UNRESOLVED | UNRESOLVED | None |
| `005930.KS::핵심 저항` | 핵심 저항 | 1 | None | UNRESOLVED | UNRESOLVED | None |
| `267260.KS::탈락 조건` | 탈락 조건 | 1 | None | UNRESOLVED | UNRESOLVED | None |
| `267260.KS::다음 이벤트` | 다음 이벤트 | 1 | None | UNRESOLVED | UNRESOLVED | None |
| `329180.KS::탈락 조건` | 탈락 조건 | 1 | None | UNRESOLVED | UNRESOLVED | None |
| `298040.KS::탈락 조건` | 탈락 조건 | 6 | None | UNRESOLVED | UNRESOLVED | None |
| `298040.KS::다음 이벤트` | 다음 이벤트 | 1 | None | UNRESOLVED | UNRESOLVED | None |
| `SNDK::탈락 조건` | 탈락 조건 | 1 | None | UNRESOLVED | UNRESOLVED | None |
| `SNDK::기술적 무효화` | 기술적 무효화 | 1 | None | UNRESOLVED | UNRESOLVED | None |
| `SNDK::핵심 지지` | 핵심 지지 | 1 | None | UNRESOLVED | UNRESOLVED | None |
| `SNDK::핵심 저항` | 핵심 저항 | 1 | None | UNRESOLVED | UNRESOLVED | None |
| `SNDK::진입 패턴` | 진입 패턴 | 1 | None | UNRESOLVED | UNRESOLVED | None |

*(결합 표기 24건은 표에서 생략 — `또는`·`/`·`→`·`+`·`·`)*

## `portfolio_operation_candidates` — 1건

> ⛔ 새 Portfolio 객체가 아니다. CIO 판정 B2-0 #2 로 명시된 occurrence 를 가리키는 **index 뿐**이며 원문을 분할하지 않았다. 해당 occurrence 는 `evaluation_rules` 에도 그대로 남아 있다.

- definition_status: `DEFINED` 1
- data_status: `MISSING` 1
- source_qualification: `SOURCE_UNRESOLVED` 1

| candidate_id | cell | # | kind | def | data | src_qual |
|---|---|---:|---|---|---|---|
| `ANET::탈락 조건` | 탈락 조건 | 3 | FAL | DEFINED | MISSING | SOURCE_UNRESOLVED |


## cross-cell duplicate 후보 — 탐색만

⛔ **병합하지 않았다. canonical ID 도 만들지 않았다.**

### (a) 원문 문자열 동일 — 2 cluster

- `DRAM ASP 하락 전환`
    - `MU::탈락 조건#3` · kind=FAL · def=UNDEFINED · data=MISSING
    - `000660.KS::탈락 조건#3` · kind=FAL · def=UNDEFINED · data=MISSING
- `236.5`
    - `CRDO::핵심 저항#3` · kind=UNRESOLVED · def=DEFINED · data=MISSING
    - `NVDA::핵심 저항#3` · kind=UNRESOLVED · def=DEFINED · data=MISSING

### (b) 사람이 판독한 의미 중복 후보 — 2건 (B1 보고에서 이월)

| 후보 | occurrence | 왜 자동 병합하면 안 되는가 |
|---|---|---|
| TSMC 월매출 약화 → 매수 취소·Ready 해제 | `TSM::다음 이벤트#5` · `TSM::편입 사유#4`(pilot) | 편입 사유 쪽은 `⚠ PROTOTYPE — 정본 규칙 아님` 표식 아래에 있다. 권위가 다르다 |
| TSMC $398 이탈 | `TSM::기술적 무효화#1`(FAL·강등 검토) · `TSM::편입 사유#5`(pilot·UNRESOLVED) | 같은 숫자인데 효과가 다르다(무효화 vs 청산). 중복이 아니라 서로 다른 층일 수 있다 |

*(b)의 편입 사유 occurrence 는 46칸 밖(pilot)이므로 이번 population 에 포함되지 않는다.*


## semantic unresolved gate

- `rule_candidate` 중 `rule_kind ∉ {FAL, ENT, MON}` : **0건** → PASS
- ⛔ 이 gate 와 섞지 않는 별도 dimension: `data_capability` 97 · `definition_status` 60 · `data_status` 60
