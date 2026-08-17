# `build_header` 흡수 경계 조사 (CIO 지시 2026-08-16)

⛔ **조사만이다. 코드 변경 0줄 · 수정안 없음 · 수정 미착수.**
★ 분류 전제 유지 — **가용성/강건성 결함**이다. ⛔ silent-wrong correctness defect 로
  전제하지 않는다 (180개 주입에서 재현되지 않았다).

목표 불변식:

> column identity 는 **header 영역의 셀만으로** 결정되어야 하며,
> data-row 의 라벨·값 변경에 의존해서는 안 된다.

---

## 1. ★ 근본 원인 — `build_header` 의 암묵 가정

```python
def build_header(rows, data_i):
    """데이터 행 위의 **모든 행**을 열 단위로 이어 붙여 헤더 한 줄을 만든다."""
    for r in rows[:data_i]:      # ← 위쪽 전부를 header 로 간주한다
```

**「대상 행 위는 전부 header 다」** 라는 가정이 들어 있다.
이 가정은 **대상 행이 그 표의 첫 데이터 행일 때만** 참이다.
함수는 header 영역이라는 개념을 갖고 있지 않다 — 경계를 **위치(data_i)** 로만 잡는다.

## 2. 그래서 C4 에서는 드러나지 않고 MSFT 에서만 드러났다

| | 대상 행 | 위쪽 header 영역 | 위쪽 **데이터 행** | 대상이 몇 번째 데이터 행 |
|---|---|---|---|---|
| **C4 결정표** (3개월 전부) | `Net Revenue` row 5 | `[0,1,2,3,4]` | **`[]` 없음** | **1번째** |
| **MSFT 구형** (Q1·Q2) | Azure row 13 | `[0…6]` | `[7…12]` 6개 | **7번째** |
| **MSFT 신형** (Q3·Q4) | Azure row 9 | `[0,1,2]` | `[3…8]` 6개 | **7번째** |

★ **C4 는 대상이 첫 데이터 행이라 가정이 성립한다.** 그래서 같은 함수를 쓰고도
  TSMC 쪽에서는 오염이 발생하지 않았다.
★ MSFT 는 Azure 가 **7번째** 데이터 행이라 위쪽 6개 행이 통째로 흡수된다.
  구형·신형 모두 7번째로 동일하다 — 문면이 바뀌어도 이 성질은 안 바뀌었다.

## 3. 흡수 경계 — 실제로 무엇이 더 들어오는가

2026-04-29 (신형) 기준. `header 영역` = 값 행이 아닌 행만, `공용` = 위쪽 전부.

```
[0]  header 영역   'Three Months Ended March 31, 2026'
     공용이 더 먹음 'Microsoft Cloud revenue Commercial remaining performance obligation …'
[1]  header 영역   'Percentage Change Y/Y (GAAP)'
     공용이 더 먹음 '29% 99% 19% 33% 12% 22%'
[2]  header 영역   'Constant Currency Impact'
     공용이 더 먹음 '(4)% 0% (4)% (4)% (3)% (5)%'
[3]  header 영역   'Percentage Change Y/Y Constant Currency'
     공용이 더 먹음 '25% 99% 15% 29% 9% 17%'
```

⇒ 추가분의 정체는 **Azure 위 6개 데이터 행의 라벨(열 0)과 값(열 1~3)** 이다.
  라벨은 열 0 에, 값은 값 열에 각각 쌓인다.

## 4. 두 구현의 경계 정의 비교 (현재 저장소 상태)

| | 경계를 무엇으로 잡는가 | 현재 사용처 |
|---|---|---|
| `c4_sec_edgar_check.build_header` | **위치** — `rows[:data_i]` 전부 | C4 (TSMC 3 Rule) |
| `msft_azure_cc.build_header` | **의미** — 값 행(퍼센트 값 셀 보유)을 건너뜀 | MSFT (RULE-0021) |

★ MSFT 쪽은 2026-08-16 격리로 이미 불변식을 만족한다 (회귀 E-4: data-row 주입
  fixture 당 120·96건 전부 무해). 남은 것은 **공용(C4) 쪽 정의**다.

## 5. 판정에 필요한 사실만

1. C4 의 현재 사용 패턴에서는 가정이 성립해 **오염이 발생하지 않는다** (3개월 실측).
2. 그러나 가정은 **함수 계약에 적혀 있지 않다.** docstring 은 「위의 모든 행」이라고만
   말하고, 「대상이 첫 데이터 행이어야 한다」는 전제 조건이 없다.
3. 따라서 이 결함의 성격은 **「잘못 계산한다」가 아니라 「전제가 문서화·강제되지
   않았다」** 이다. 새 사용처가 그 전제를 깨면 그때 드러난다 — 실제로 MSFT 에서 그랬다.
4. MSFT 는 이미 격리로 닫혔다. 공용 쪽을 고칠지, 전제를 계약으로 명시만 할지는
   **선택지가 갈린다.**

⛔ 권고를 적지 않는다. 수정 계약은 CIO 판정 사항이다.

## 6. 조사 범위 밖 — 하지 않은 것

- 코드 수정 · 공용 helper 리팩터 · C4 재검증 · 새 FI
- 「공용도 의미 기반으로 바꾸자」 같은 수정안 제안

## 7. 상태

```
C4 row-local uniqueness       CLOSED @ 36aa11b
C4 table-level uniqueness     CLOSED @ ede4ad5
P3 · RULE-0003/0007/0008      CLOSED · READY 유지

OPEN
  build_header robustness           조사 완료 · 수정 계약 판정 대기
  capture unit 탐색 범위
  공용 helper inventory
  mutation harness cache/provenance hardening (engineering debt)
```
