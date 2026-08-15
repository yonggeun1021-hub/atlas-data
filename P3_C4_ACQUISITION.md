# P3 — TSMC 월매출 Acquisition Contract (CIO 확정 2026-08-15)

## Gate 상태

| Gate | 상태 | 근거 |
|---|---|---|
| `Official Fetch/Extraction` | **CLOSED** | 2026-08-15 GitHub-hosted live run 에서 Discovery → Identification → Unit validation → Decision-table selection → semantic binding → extraction → cross-check → published date → fixture differential 전 구간이 **실제 네트워크로** 통과 |

⛔ CLOSED 는 **fetch/extraction capability 증명**이다. evaluator 사용 승인도 Production HOLD 해제도 아니다.

## Source Contract

| 층 | 확정 |
|---|---|
| **Primary acquisition** | SEC EDGAR — TSMC 가 직접 제출한 6-K (CIK `0001046179`) |
| **Decision observation SSOT** | 6-K 내부 `TSMC {Month} Revenue Report (Consolidated)` 표, 단위 `NT$ million` |
| Secondary verification | TSMC IR (`investor.tsmc.com`) — 사람이 확인하는 원발표 / reference |
| 자동 취득 제외 | FSC/TWSE 개방데이터 (C2) — 독립 cross-check source 로만 남긴다 |

### 취득 경로 판정 근거 (실측)

| 후보 | GitHub-hosted runner 결과 |
|---|---|
| C1 TSMC IR HTML | **HTTP 403** — 서버가 거부 |
| C2 FSC/TWSE CSV | **HTTP 200 이지만 CSV 아닌 800B HTML** — 데이터셋 미제공 |
| **C4 SEC EDGAR** | **HTTP 200 · 정상 문서** — 요청 2건으로 end-to-end 성립 |

⛔ 브라우저 위장·헤더 추가·WAF 우회는 어느 단계에서도 하지 않았다.

## 계층 규칙

- Decision 값은 **Consolidated `NT$ million` 표에서만** 만든다.
- 산문 `NT$ … billion / … percent` = 문서 식별 + cross-check 전용.
- 뒤쪽 `Revenue (in NT$ thousands)` 표 = 정밀도 cross-check 전용.
- ⛔ 산문·천원표는 **fallback 이 아니다.** Decision 표를 못 읽으면 fail-closed.
- ⛔ **천원표 → million 축약 규칙은 Decision 계약에서 제외**한다 (CIO 판정 2026-08-15).
  Decision SSOT 가 Consolidated 공표값으로 확정됐으므로 그 변환으로 값을 **생성할 일이 없다**.
  천원표는 cross-check evidence 로만 기록한다. tolerance 를 발명하지 않는다.
- YoY 는 **공표값 직접 소비**. 재계산은 cross-check 까지만 허용하며 Decision 값으로 승격하지 않는다.

## 식별 계약

문서 식별 4요건 — 전부 **내용**으로 판정한다. 파일명·description·accession·날짜는 근거가 아니다
(파일명은 후보 정렬 hint 전용, accession 번호는 제출일 순서와 일치하지 않음이 실측으로 확인됨).

1. `TSMC {Month} {Year} Revenue Report`
2. `Revenue Report (Consolidated)`
3. 대상월 `{Month} {Year}`
4. 당해 누계 기간 `January to {Month} {Year}`

단위 `(Unit:NT$ million)` 확인은 **식별이 아니라 추출 시점의 필수 게이트**다.
⛔ table-local invariant 로 승격하지 않았다 — 한 달 구조만으로 계약을 만들지 않는다.

## 컬럼 결합 계약

헤더 셀이 기간 라벨만으로 구성된다고 가정하지 않는다 (실측: 제목·단위가 헤더 셀에 흡수됨).

- cumulative-current = `January to {Month} {Year}`
- cumulative-prior = `January to {Month} {Year-1}`
- monthly-current = **누계 표현 제거 후** `{Month} {Year}`
- monthly-prior = **누계 표현 제거 후** `{Month} {Year-1}`
- 네 anchor 각각 **정확히 1개**. 0개·2개 이상이면 fail-closed.
- YoY 는 prior-month / prior-cumulative anchor 뒤의 대응 Y-o-Y 로 결합.

⛔ 단순 `contains` 완화 금지 — `January to July 2025` 를 `July 2025` 로 오인하지 않는다.

## 시각 계층

| 항목 | 출처 | 용도 |
|---|---|---|
| `target_month` | Revenue Report 내용 | 관측 대상 |
| `published_at` | Revenue Report 본문 발표일 | 발표일 |
| `sec_acceptance` | SEC `acceptanceDateTime` | **provenance/validation 전용** |

⛔ SEC 접수시각을 TSMC 발표시각으로 쓰지 않는다. 본문 발표일 ≠ SEC `filingDate` 면 fail-closed.

## Rule 상태 판정 (CIO 2026-08-15)

| Rule | 조건 | 판정 |
|---|---|---|
| `RULE-0007` | 단월 YoY < +35% OR 누계 YoY < +34.6% | `DATA_MISSING`·`SOURCE_UNRESOLVED` **해제 승인** |
| `RULE-0008` | 단월 YoY ≥ +35% AND 누계 YoY ≥ +34.6% | 동일 **해제 승인** |
| `RULE-0003` | 월매출 YoY 40% 미달 **2개월 연속** | **해제 보류** — 월 시계열 capability 검증 후 판정 |

⛔ 해제는 evaluator 사용 승인이 아니다. `consumable_by_evaluator=false` · Production HOLD ·
evaluator 미연결 유지.

⛔ `condition_semantics` · `scope` · `data_capability` 의 `UNRESOLVED` 세 필드는 **건드리지 않는다.**
이 legacy/metadata 필드가 READY 를 별도로 차단하는지 계약 확인이 먼저다.

## 운영화 단계로 이관 (이 Gate 를 다시 열지 않는다)

- 정정(revision) 탐지
- historical backfill
- persistent incremental cursor — 현재는 명시 target month 입력
- 결정론적 회귀에 live 네트워크를 넣지 않는 구조상, SEC 문서 구조 변경은 수동 실행 전까지 감지되지 않는다

## 미머지

이 계약과 구현은 `b1-pilot/c4-sec-edgar` 에만 있다. merge 는 `RULE-0003` 의 2개월 capability
검증 결과까지 보류한다 (CIO 판정).
