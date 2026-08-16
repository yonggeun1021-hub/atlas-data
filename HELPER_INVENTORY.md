# 공용 parsing helper 사용처 Inventory (CIO 지시 2026-08-16)

⛔ **읽기 전용 조사다. 코드를 수정하지 않았다.**
⛔ P3 상태를 바꾸지 않았다. ⛔ 「공용이니 한 번에 고친다」를 하지 않았다.

목적: `build_header` 오염과 first-match 선택 결함의 **blast radius 확정**.
수정 방식(A. MSFT local wrapper 격리 / B. common helper contract 수정)은 CIO 판정 사항.

---

## 1. 공용 표면 — 실제로 공유되는 것은 6개뿐이다

`collectors/c4_sec_edgar_check.py` 가 사실상 공용 모듈 역할을 하고 있다.
**별도의 공용 패키지가 아니라 collector 하나를 다른 collector 가 import 한다.**

| helper | 정의 위치 | import 하는 곳 |
|---|---|---|
| `TableCollector` | `c4_sec_edgar_check` | `msft_azure_cc` |
| `strip_html` | `c4_sec_edgar_check` | `msft_azure_cc` |
| `drop_empty_columns` | `c4_sec_edgar_check` | `msft_azure_cc` |
| **`build_header`** | `c4_sec_edgar_check` | `msft_azure_cc` |
| `evidence` | `c4_sec_edgar_check` | `msft_azure_cc` |
| `get` | `c4_sec_edgar_check` | `msft_azure_cc` · `capture_azure_fixture` |

### ★ `bind_columns` 는 공용이 **아니다** — 중요한 정정

두 파일에 **같은 이름의 다른 함수**가 있다.

```
c4_sec_edgar_check.bind_columns(header, data, month_name, year)   ← TSMC 전용 (월/연 인자)
msft_azure_cc.bind_columns(header, data)                          ← MSFT 자체 구현
```

`msft_azure_cc` 의 import 목록에 `bind_columns` 는 없다. **각자 구현이며 공유되지 않는다.**
⇒ 「각 컬럼이 정확히 1개」 가드도 **각자 따로** 갖고 있다. 한쪽을 고쳐도 다른 쪽은 안 바뀐다.

## 2. 사용처별 표

| collector | helper 사용 | 방식 | decision observation 영향 | fixture/회귀 | `build_header` 오염 발생 구조 | 공통 수정 시 재검증 대상 CLOSED Gate |
|---|---|---|---|---|---|---|
| **`msft_azure_cc`** (RULE-0021) | `TableCollector` · `strip_html` · `drop_empty_columns` · **`build_header`** · `evidence` · `get` | **직접 import** | ✅ **있음** — Azure cc 관측값 | ✅ 실제 SEC 마크업 4건 + 회귀 243건 | ✅ **있음** (실측 확인) | RULE-0021 acquisition/extraction (2026-08-16 CLOSED) |
| **`c4_sec_edgar_check`** (RULE-0003/0007/0008) | 자기 자신이 정의 · **`build_header` 사용** | 정의처 | ✅ **있음** — TSMC 월매출 YoY | ✅ 회귀 `test_c4_sec_edgar.py` · fixture 없음(합성) | ✅ **있음** (구조 동일 · 미검증) | **P3 CLOSED** (RULE-0003/0007/0008 = AVAILABLE/SOURCE_RESOLVED/READY) |
| **`tsmc_monthly`** (C1) | ❌ 사용 안 함 — `_TableReader` · `_clean` **별도 구현** | **복사 아님, 독립 구현** | ⛔ **없음** — 회귀가 「이 collector 는 Rule 상태의 근거가 아니다」로 명시 | ✅ HTML fixture + snapshot TSV | ❌ **없음** — 헤더를 이어붙이지 않고 `HEADER_LABELS` 완전일치로 컬럼을 잡는다 | 없음 |
| **`capture_azure_fixture`** | `get` 만 (+ `msft_azure_cc` 함수 재사용) | 직접 import | ⛔ 없음 — fixture 보존 전용 | ✅ 회귀 58건 | ❌ 없음 (파싱 안 함) | 없음 |
| `sec.py` · `dart.py` · `krx.py` · `common.py` · `tsmc_live_check.py` | 자체 `get` 등 · 표 파싱 helper 미사용 | 무관 | ⛔ 없음 | — | ❌ 없음 | 없음 |

## 3. first-match 선택 패턴 — ★ C4 에 **그대로 남아 있다**

RULE-0021 에서 닫은 결함과 **같은 구조**가 `c4_sec_edgar_check` 에 있다.

| 위치 | 코드 | 성격 |
|---|---|---|
| `find_decision_table` (L182~) | `Net Revenue` 행 찾다 첫 행에서 `break` | **행 first-match** |
| main (L548~) | 후보 표를 돌며 첫 결합 성공에서 `break` | **표 first-match** |

```python
# c4_sec_edgar_check.find_decision_table
for ri, r in enumerate(rows):
    if r and any(RE_NET_REVENUE.match(c) for c in r):
        data_i = ri
        break            # ← 같은 표에 Net Revenue 행이 둘이면 위쪽이 조용히 선택된다

# c4_sec_edgar_check main
for ti, rows, di in found:
    ...
    if bound:
        decision = bound
        break            # ← 후보 표가 둘이면 문서 순서가 값을 정한다
```

⇒ **RULE-0021 에서 재현한 silent-wrong 과 동일 계열**이다.
⛔ 다만 C4 에서 실제로 재현하지는 않았다 — 조사 범위가 inventory 이기 때문이다.
   「구조가 같으니 결함도 같다」로 **단정하지 않는다.** 재현은 별도 Gate 사안이다.

### C4 가 RULE-0021 보다 위험이 낮을 수 있는 요인 (확인 필요, 단정 아님)

- C4 는 `find_decision_table` 단계에서 **`Y-o-Y` 헤더 존재**를 추가 조건으로 요구한다
- TSMC 월매출 표는 MSFT 처럼 「분기표/연간표」가 병존하는 구조인지 미확인

## 4. 오염 노출 비교 — 왜 `tsmc_monthly` 는 안전한가

| | 컬럼 결합 방식 | 오염 노출 |
|---|---|---|
| `build_header` 계열 (C4 · MSFT) | 대상 행 **위 모든 행을 열 단위로 이어 붙여** 헤더를 만든 뒤, 그 문자열을 정규식으로 분류 | ✅ 다른 data-row 의 라벨·값이 헤더에 섞인다 |
| `tsmc_monthly` | `HEADER_LABELS` 와 **셀 완전일치**로 컬럼 인덱스를 잡는다 (상위 3행 한정) | ❌ 이어 붙이지 않으므로 섞이지 않는다 |

★ 즉 오염은 **`build_header` 라는 특정 설계 선택**에서 나온다. 표 파싱 일반의 문제가 아니다.
  이미 저장소 안에 오염되지 않는 대안 구현(`tsmc_monthly`)이 존재한다.

## 5. blast radius 요약

```
build_header 를 공통 수정하면 재검증 대상:
    RULE-0021 acquisition/extraction Gate   (2026-08-16 CLOSED)
    P3 Gate — RULE-0003 · 0007 · 0008       (2026-08-15 CLOSED)
                                             = AVAILABLE / SOURCE_RESOLVED / READY 3건

MSFT 쪽만 격리 수정하면 재검증 대상:
    RULE-0021 Gate 만
```

### 판정에 필요한 사실 관계

- 공용 표면은 **collector 하나(`c4_sec_edgar_check`)를 다른 collector 가 import** 하는 형태다.
  진짜 공용 모듈이 아니므로, **C4 를 고치면 TSMC Rule 3건이 함께 흔들린다.**
- `bind_columns`(가드 보유자)는 이미 **각자 구현**이다. 즉 부분 격리가 이미 존재한다.
- C4 쪽 first-match 결함은 **구조만 확인했고 재현하지 않았다.**

## 6. ⛔ 하지 않은 것

- 코드 수정 · P3 상태 변경 · C4 결함 재현 · 공통 helper 리팩터
- 「공용이니 한 번에 고친다」
- A/B 방식 선택 — **CIO 판정 사항이므로 권고를 적지 않는다**

---

## 부기 — RULE-0021 live run 최종 결과 (2026-08-16 · `7c1364c`)

```
2026-07-29  표[5] · Azure 행 1건 · period June 30, 2026       43 /  0  / 43
2026-04-29  표[3] · Azure 행 1건 · period March 31, 2026      40 / (1) / 39
2026-01-28  표[3] · Azure 행 1건 · period December 31, 2025   39 / (1) / 38
2025-10-29  표[3] · Azure 행 1건 · period September 30, 2025  40 / (1) / 39
```

4/4 통과 · 값 변화 없음 · period end 가 각 filing 의 직전 분기말과 일치.
CIO 판정: **RULE-0021 period/table/row ambiguity = CLOSED**.

## 부기 — engineering note (CIO 승인 2026-08-16)

> **새 정적 계약 검사는 문자열 검색이 아니라 AST 를 기본으로 한다.**

같은 유형의 오탐이 세 번 반복됐다 — 문자열 검색이 **금지 문구 주석**과 **실제 코드**를
구별하지 못했다.

| # | 검사 | 오탐 내용 |
|---|---|---|
| 1 | `45` · `3` 기준선 판정 부재 | 「⛔ 45% 기준선은 evaluator 층」 **주석**을 위반으로 잡음 |
| 2 | `index.json` 미조회 | 「⛔ index.json 의 type 은 쓰지 않는다」 **주석**을 위반으로 잡음 |
| 3 | `find_azure_table` 의 `break` 부재 | 「예전에는 `break` 했다」 **docstring** 을 코드로 오인 |

⛔ 공통 정적검사 프레임워크는 만들지 않는다 (CIO 판정). 원칙만 남긴다.
