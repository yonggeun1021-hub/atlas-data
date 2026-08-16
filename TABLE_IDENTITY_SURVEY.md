# Table-level identity 조사 (CIO 지시 2026-08-16)

⛔ **조사만 했다.** 코드 변경 0줄 · FI 확대 없음 · 수정안 없음 · P3 불변.

---

## 1. selection 계층이 지금 어디에 남아 있는가

| collector | table 선택 | 상태 |
|---|---|---|
| **`msft_azure_cc`** | `select_observation` — period → **table 정확히 1건** → row | ✅ **닫힘** (2026-08-16) |
| **`c4_sec_edgar_check`** | main 의 `for ti, rows, di in found: … if bound: break` | ⚠️ **열림** |
| `tsmc_monthly` (C1) | 표 선택 없음 (`HEADER_LABELS` 완전일치) · Rule 상태 근거 아님 | — |

⇒ selection 계층에서 남은 것은 **C4 main 의 「첫 결합 성공 표」 하나**다.

## 2. 현재 C4 의 table candidate 정의 — 층이 셋이다

| 층 | 위치 | 판정 내용 |
|---|---|---|
| **L1** | `find_decision_table` | `Net Revenue` 행 **정확히 1개**(2026-08-16 신설) · row0 아님 · head 에 `Y-o-Y` |
| **L2** | `bind_columns(header, data, month_name, year)` | 대상월 · 전년동월 · 당해누계 · 전년누계 각 **정확히 1개** + `Y-o-Y` **정확히 2개** |
| **L3** | `verify_unit_million(text)` | `(Unit:NT$ million)` — ⚠️ **문서 단위 검사다** |

★ **선택(selection)은 L1 을 통과한 표들 중 L2 에 처음 성공하는 표**다.
  L1·L2 는 각각 「정확히 N개」 규율을 갖는데, **그 둘을 조합하는 단계에는 없다.**

### ⚠️ L3 는 table 판별자가 아니다

`verify_unit_million` 은 `strip_html(html_text)` — **문서 전체 텍스트**를 본다.
실측상 `(Unit:NT$ million)` 은 결정표 **안**(row2/col7)에 있지만, 검사는 문서
어디에 있어도 통과한다. ⇒ 「단위로 표를 고른다」는 현재 구현되어 있지 않다.

## 3. ★ C4 의 period identity 는 이미 L2 안에 있다 — MSFT 와 구조가 다르다

실제 결정표 헤더 (2026-08-10 · July):

```
[1] 'July 2026'                              [4] 'July 2025'
[6] 'January to July 2026'                   [7] '(Unit:NT$ million) January to July 2025'
```

`bind_columns(header, data, "July", 2026)` → **성공**
`bind_columns(header, data, "June", 2026)` → **FAIL-CLOSED** (전년동월·누계 헤더 0개)

⇒ **기간이 컬럼 헤더에 직접 박혀 있고, 결합이 기간으로 판정한다.**
  MSFT 는 기간이 표 상단의 별도 셀(`Three Months Ended …`)에 있어 **table 층에서**
  period 를 판정했다. C4 는 **column 층에서** 이미 판정한다.
  ⇒ 「MSFT 와 같은 방식으로 period → table 을 만든다」가 그대로 이식되지 않는다.

## 4. 실제 fixture 에서 각 층의 판별력

| fixture | L1 후보 | L2 결합 | L3(문서) |
|---|---|---|---|
| t4 결정표 (3개월 전부) | **1** | 성공 | True |
| t6 천원표 (3개월 전부) | **0** | — | False (슬라이스 단독일 때) |

t6 가 걸리는 지점 (July 기준):

```
Net Revenue 행 [2, 3]        → row 유일성(L1)  거부
head 에 Y-o-Y 없음            → Y-o-Y 조건(L1)  거부
```

★ **t6 는 L1 에서 두 가지 이유로 각각 독립적으로 걸린다.** L2 까지 갈 일이 없다.
⇒ 실제 문서에서는 L1 통과 표가 **항상 1건**이므로 main 의 `break` 가 고를 것이
  하나뿐이다. **현재 table-level ambiguity 는 관측되지 않는다.**

## 5. 그래서 남은 질문은 「무엇을 table identity 로 정의할 것인가」다

현재 구조에서 가능한 방향을 **사실만** 적는다. ⛔ 권고하지 않는다.

| | 방향 | 사실 관계 |
|---|---|---|
| **가** | L1 통과 표가 2건 이상이면 무조건 fail-closed | 가장 단순. 다만 L2 에서 정상 탈락하는 표까지 실패로 만든다 — 최소변형 FI 에서 실제로 그런 경우(천원표+Y-o-Y)가 나왔다 |
| **나** | L1∧L2 를 모두 통과한 표가 정확히 1건이어야 함 | 「후보」의 정의를 **결합 성공까지** 포함하는 것. 정상 문서를 깨뜨리지 않는다. 다만 모든 후보에 대해 결합을 시도해야 하므로 `break` 를 없애야 한다 |
| **다** | 단위(L3)를 표 단위 판별자로 승격 | 현재 문서 단위라 **구현이 없다.** 실측상 단위는 결정표 안에 있으므로 판별자가 될 수 있다. 다만 새 계약이다 |

★ **가**와 **나**는 결과가 갈린다. 최소변형(천원표에 `Y-o-Y` 부여) 시:
```
가 → L1 후보 2건 → 실패        (정상 원문은 무사 · 변형 시 관측 중단)
나 → L2 통과 1건 → 정상 관측    (b2c77ae 에서 실측한 현재 동작과 같다)
```

⛔ 어느 쪽이 옳은지는 **관측 계약의 문제**다. 내가 정하지 않는다.

## 6. 조사 범위 밖 — 하지 않은 것

- 코드 수정 · 합성 FI 확대 · 공용 helper 변경 · `build_header` 착수
- 「단위를 표 판별자로 쓰자」 같은 새 계약 제안 (사실로만 적었다)

## 7. 상태

```
RULE-0003 · 0007 · 0008          P3 CLOSED 유지 · READY
C4 row-local uniqueness          CLOSED @ 36aa11b
C4 table-level ambiguity         OPEN · 조사 완료 · 계약 판정 대기
build_header robustness          OPEN (후순위 · CIO 판정)
```
