# 별건 4 — regression crash inventory (CIO 지시 2026-08-16)

⛔ **조사만이다.** 회귀 코드 변경 0줄 · `try/except` 추가 없음 · 검사 순서 변경 없음 ·
   production 무변경 · fixture 무변경 · 별건 1~3 미착수.
★ 유일한 코드 변경은 CIO 판정 1이 명시적으로 지시한 **MISATTRIBUTED 하위 사유 코드화**
  (`4d74971`) 이며 **verdict 판정 논리는 바꾸지 않았다.** 재실행 결과 partition 동일.

```
HEAD 4d74971 · 69종 재실행
KILLED 63 · SURVIVED 0 · MISATTRIBUTED 6 · NOT_APPLICABLE 0 · INVALID_RUN 0
  MISATTRIBUTED
    EXPECTED_KILLER_NOT_FIRED             0
    REGRESSION_CRASH_BEFORE_ATTRIBUTION   6
```

---

## 1. 요약 — 14건은 **네 가지 기전**으로 갈린다

```
A  회귀가 fail-closed 산출물(None·빈 목록)을 검사 없이 첨자 접근한다      7건
B  변이가 만든 예외가 정상 입력에서 발생한다 (회귀가 감싸지 않고 직접 호출)  3건
C  fixture 파일 부재 — `_fx()` 가 존재 확인 없이 open()                  3건
D  변이가 만든 None 이 production 내부로 흘러 들어가 예외                  1건
```

★ **A 가 절반이다.** 그리고 A 는 production 결함이 **아니다** — production 은
  계약대로 fail-closed 하여 `None` 을 돌려주는데, **회귀가 그것을 그대로 첨자 접근한다.**
  CIO 가 지적한 「production 예외를 삼키라는 뜻이 아니다 · 회귀 harness 의 관측/보고
  구조 문제」와 정확히 일치한다.

## 2. 전수 inventory

| 기전 | mutation | verdict | 예외 | crash 지점 | 실행/전체 | 미실행 |
|---|---|---|---|---|---|---|
| A | `C4-PRECOND-W-7` | KILLED | IndexError | `test/test_c4_sec_edgar.py:730` | 120/147 | 27 |
| A | `MSFT-EXTRACT-P2` | KILLED | IndexError | `test/test_msft_azure_cc.py:646` | 108/147 | 39 |
| A | `MSFT-EXTRACT-P7` | MISATTRIBUTED | TypeError | `test/test_msft_azure_cc.py:115` | **5/147** | **142** |
| A | `MSFT-HEADER-S2` | KILLED | IndexError | `test/test_msft_azure_cc.py:781` | 131/147 | 16 |
| A | `MSFT-HEADER-S3` | MISATTRIBUTED | TypeError | `test/test_msft_azure_cc.py:115` | **5/147** | **142** |
| A | `MSFT-HEADER-S4` | MISATTRIBUTED | TypeError | `test/test_msft_azure_cc.py:759` | 129/147 | 18 |
| A | `MSFT-HEADER-S6` | MISATTRIBUTED | IndexError | `test/test_msft_azure_cc.py:781` | 131/147 | 16 |
| B | `C4-PRECOND-W-2` | KILLED | AssertionError | `collectors/c4_sec_edgar_check.py:266` | 132/147 | 15 |
| B | `C4-PRECOND-W-3` | MISATTRIBUTED | HeaderPreconditionError | `collectors/…:267` | **2/147** | **145** |
| B | `C4-PRECOND-W-4` | MISATTRIBUTED | HeaderPreconditionError | `collectors/…:266` | **2/147** | **145** |
| C | `SELFTEST-X-2` | KILLED | FileNotFoundError | `test/test_c4_sec_edgar.py:499` | 74/147 | 73 |
| C | `SELFTEST-X-3` | KILLED | FileNotFoundError | `test/test_c4_sec_edgar.py:499` | 74/147 | 73 |
| C | `SELFTEST-X-5` | KILLED | FileNotFoundError | `test/test_c4_sec_edgar.py:499` | 74/147 | 73 |
| D | `MSFT-OBSERVE-Q6` | KILLED | TypeError | `collectors/c4_sec_edgar_check.py:265` | 122/147 | 25 |

```
14회 실행에서 **실행되지 못한 검사 949건** (평균 67.8건/회 · 회귀당 총 147개)
```

★ 「실행/전체」는 stdout 이 아니라 **소스의 `check()` 호출 위치**(AST)로 잡았다.
  이유는 §5 에 있다 — `test_msft_azure_cc.py` 는 stdout 만으로는 진행 위치를 알 수 없다.

## 3. 기전별 — 무엇이 crash 를 만드는가

### A. fail-closed 산출물을 검사 없이 첨자 접근 (7건)

```python
test/test_msft_azure_cc.py:115
  check("같은 분기: 두 컬럼 값이 동일해 구별 불가", b_same["gaap"] == b_same["cc"])
                                                  ^^^^^^ 결합 실패 시 None

test/test_msft_azure_cc.py:759   base = (b0["gaap"], b0["cc_impact"], b0["cc"])
test/test_msft_azure_cc.py:781   _di = [i for i in range(ri) if M.is_data_row(rows[i])][-1]
test/test_c4_sec_edgar.py:730    _m1[0]["bound"]["monthly_revenue"] == …
```

★ **`test_msft_azure_cc.py:115` 한 줄이 두 변이(P7 · S3)를 5번째 검사에서 죽인다.**
  147개 중 142개가 실행되지 않는다. 두 변이의 귀속이 이 한 줄 때문에 불가능하다.
★ 이 줄들은 **`check()` 의 인자를 계산하는 도중**에 죽는다. 즉 그 검사조차
  「실패」로 기록되지 못한다 — PASS 도 FAIL 도 아닌 상태로 사라진다.

### B. 변이가 만든 예외가 정상 입력에서 발생 (3건)

```python
test/test_c4_sec_edgar.py:107 (run_doc 헬퍼)
  header = C.build_header(rows, di)      # ← 감싸지 않고 직접 호출
```

- `W-3`(전제를 위치 계약으로) · `W-4`(연도 단독 셀도 값으로)는 **정상 입력에서**
  `HeaderPreconditionError` 를 발생시킨다. 회귀 두 번째 검사에서 죽는다(2/147).
- `W-2`(전제를 `assert` 로)는 `AssertionError` 를 낸다. 다만 **정적 검사
  「전제 검사가 assert 가 아니다」(753줄)가 먼저 실패**해서 KILLED 다 —
  KILLED 근거는 crash 가 아니다.

### C. fixture 파일 부재 (3건)

```python
test/test_c4_sec_edgar.py:499
  return open(os.path.join(FX, name), encoding="utf-8").read()   # 존재 확인 없음
```

`⑰-0` 은 `TSMC_FX` 목록 항목에 대해 존재·sha 를 확인하지만, **파일명을 직접 지목하는
호출(586·669·795줄)은 그 경로를 거치지 않는다.** X-2·X-3·X-5 는 이름 규약을 바꾸므로
직접 지목이 먼저 터진다.
★ 세 건 모두 **cardinality gate(474줄)가 먼저 실패**해서 KILLED 다 — 역시 crash 가
  근거가 아니다.

### D. 변이가 만든 None 이 production 내부로 (1건)

```python
collectors/c4_sec_edgar_check.py:265
  above = [i for i in range(data_i) if is_data_row_c4(rows[i])]
                          ^^^^^^ data_i 가 None
```

`Q6`(period 를 위치로 찾는다)이 `None` 을 만들고, 회귀가 그 값을 그대로
production 에 넘긴다. **production 결함이 아니다** — 회귀가 검증하지 않은 값을
넘긴 것이다.

## 4. CIO 판정 3 적용 — crash 는 검출이 아니다

판정을 그대로 대입한 결과, **현재 8건의 KILLED 는 전부 crash 가 아닌 근거를 갖는다.**

| mutation | KILLED 근거 | crash 와의 관계 |
|---|---|---|
| `C4-PRECOND-W-2` | 정적 검사 753줄 | crash 이전에 발화 |
| `C4-PRECOND-W-7` | 630줄 | crash 이전 · 사이 검사 21개 |
| `MSFT-EXTRACT-P2` | 런타임 발화 확인 (f-string 이라 정적 대응 미해결) | crash 이전 |
| `MSFT-HEADER-S2` | 733줄 | crash 이전 · 사이 6개 |
| `MSFT-OBSERVE-Q6` | 588줄 | crash 이전 · 사이 28개 |
| `SELFTEST-X-2·3·5` | cardinality gate 474줄 | crash 이전 · 사이 9개 |

⇒ **「예외로 죽었으니 잡혔다」로 집계된 건은 0건이다.** 판정 3 을 적용해도
  현재 partition 은 바뀌지 않는다.

### 다만 안정성은 다르다 — 여유(margin)

```
가장 얇은 것   MSFT-HEADER-S2   killer 733줄 → crash 781줄   사이 검사 6개
              C4-PRECOND-W-2   killer 753줄 → crash 804줄   사이 검사 5개
가장 두꺼운 것 MSFT-OBSERVE-Q6  killer 588줄 → crash 723줄   사이 검사 28개
```

★ 여유가 5~6개인 두 건은 **검사를 몇 개만 앞뒤로 옮겨도 MISATTRIBUTED 로 바뀐다.**
  CIO 가 「안정적인 판별력이라고 보지 않는다」고 하신 것이 수치로 확인된다.

### `expected contract` 로서의 예외는 현재 0건이다

판정 3 의 예외 조항(「그 예외 자체가 mutation 의 명시적 계약이면」)에 해당하는
변이는 **catalog 69종 중 0건**이다. `assertRaises` 형태로 특정 예외를 계약으로
검증하는 회귀 검사도 확인되지 않았다 — `HeaderPreconditionError` 는 호출부가
잡아서 **evidence 로 기록하는지**를 보는 형태(`W-6` 의 killer)로만 검증된다.

## 5. ★ 조사 중 발견 — 세 회귀의 **관측 가능성이 다르다**

| 회귀 | 성공한 검사 출력 | crash 시 진행 위치를 stdout 으로 알 수 있나 |
|---|---|---|
| `test_c4_sec_edgar.py` | `✓` 출력함 | **가능** |
| `test_capture_azure_fixture.py` | `✓` 출력함 | **가능** |
| `test_msft_azure_cc.py` | **실패만 출력** | **불가능** |

```python
test/test_msft_azure_cc.py:28
  def check(name, cond, extra=""):
      if cond:  PASS += 1                    # ← 성공은 찍지 않는다
      else:     FAIL += 1; print(f"  ✗ {name}…")
```

★ 그래서 이번 조사는 진행 위치를 **AST 호출 위치로 우회**해야 했다.
  MSFT 쪽 crash 7건은 **stdout 만으로는 어디까지 갔는지 알 수 없다.**
★ 세 회귀 모두 crash 시 `N PASS / M FAIL` 요약이 나오지 않는다 —
  **몇 개가 통과했는지도 남지 않는다.**

⇒ 별건 4 의 실체는 두 겹이다.
```
① 회귀가 mutation 으로 비정상 상태가 되면 **끝까지 가지 못한다** (14/69 · 949검사 미실행)
② 끝까지 못 갔을 때 **어디까지 갔는지조차 남지 않는다** (MSFT 는 stdout 으로 불가)
```
①만 고치고 ②를 두면 다음 run 에서도 같은 해석 비용이 든다. 반대로 ②만 고쳐도
미실행 검사는 그대로다. ⛔ 어느 쪽을 먼저 볼지는 적지 않는다 — 판정 사항이다.

## 6. 조사 범위에서 확인한 사실만

1. 14건은 **A 7 · B 3 · C 3 · D 1** 네 기전으로 갈린다. **A 가 최다이며
   production 결함이 아니라 회귀의 관측 구조 문제다.**
2. 단일 최다 원인은 `test_msft_azure_cc.py:115` **한 줄**이며, 두 변이를
   5번째 검사에서 죽여 각각 142개 검사를 잃게 한다.
3. **crash 를 근거로 KILLED 가 된 건은 0건이다** — 판정 3 을 적용해도 partition 불변.
4. 다만 KILLED 8건 중 2건은 여유가 **검사 5~6개**뿐이라 순서 변화에 취약하다.
5. 예외 자체가 계약인 변이는 catalog 에 **0건**이다.
6. 세 회귀의 관측 가능성이 다르고, `test_msft_azure_cc.py` 는 crash 시
   진행 위치가 stdout 에 남지 않는다.

⛔ 권고를 적지 않는다. 수정 계약은 CIO 판정 사항이다.

## 7. 이번 조사에서 하지 않은 것

- 회귀 수정 · `try/except` 추가 · 검사 순서 변경 · 보고 형식 변경
- production · fixture · Rule 상태 · capture metadata · 공용 helper 변경
- 별건 1~3 착수
- `run_all.py` · Actions PASS 정의 변경

## 8. 상태

```
mutation infrastructure          CLOSED @ 11ee80d
  └ MISATTRIBUTED 사유 코드화     @ 4d74971 (CIO 판정 1 이행 · 판정 논리 불변)

historical mutation evidence     REBUILT / AUTHORITATIVE @ 4d74971
  KILLED 63 · SURVIVED 0 · MISATTRIBUTED 6 (전부 CRASH_BEFORE_ATTRIBUTION) ·
  NOT_APPLICABLE 0 · INVALID_RUN 0

attribution quality              63/69 = 91.3% · OPEN
CI 5th gate                      NOT APPROVED — 유지

별건 4  regression crash          조사 완료 · 판정 대기  ← 이 문서
별건 1  정적 검사가 문자열 검색     OPEN · 미착수
별건 2  단언이 이름보다 약한 검사    OPEN · 미착수
별건 3  fixture 가 판별하지 못함    OPEN · 미착수

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
