# mutation infrastructure 구현 + historical revalidation 결과 (CIO 승인 범위 한정)

```
HEAD                 11ee80d
authoritative run    2026-08-16 · catalog 69 · 2회 반복 재현 확인
정본 worktree        무수정 (변이는 전부 일회용 checkout 안에서만 일어났다)
```

⛔ 구현 범위 준수: C4/MSFT production **무변경** · 기존 회귀 **무변경** ·
   fixture/Rule/capture metadata **무변경** · 공용 helper **무변경** ·
   Actions PASS 4-AND 정의 **무변경** · `run_all.py` **무변경** · 결과 파일 **미커밋**.

---

## 0. 다섯 축 — 각각 독립 판정

```
① catalog completeness      PASS   69/69 · 원 harness 와 바이트 동일 · id 중복 0
② deterministic execution   PASS   2회 반복 완전 재현 · 격리 검사 207지점 위반 0
③ expected-killer attribution  ─   최초 선언 54/69 (78.3%) → 수정 9건 → 63/69 (91.3%)
④ verdict partition         PASS   합 69 == catalog total · 각 변이 verdict 정확히 1개
⑤ provenance completeness   PASS   69/69 필수필드 · witness 일관 69/69 · 누락 0
```

```
catalog total       69
KILLED              63
SURVIVED             0
MISATTRIBUTED        6      ← 전부 회귀 CRASH. 별건으로 올린다
NOT_APPLICABLE       0
INVALID_RUN          0
```

★ ③ 만 `PASS` 로 적지 않았다. 최초 선언이 15건 빗나갔고, 그중 9건은 **내 선언 오류**였다.
  고친 근거를 catalog 안 `declaration_history` 에 남겼다. ⛔ 관측 결과를 베껴 넣어
  KILLED 를 만든 것이 아니라는 판단은 CIO 가 그 이력을 보고 하셔야 한다.

---

## 1. ① catalog completeness

```
원 harness                 /tmp/mut.py … mut9.py  9개
변이                       12+10+8+8+6+5+6+7+7 = 69
catalog                    4파일 · 69건
anchor/replacement 바이트 동일   True   ← 손으로 옮겨 적지 않았다
```

★ 이관은 **AST 로 각 harness 의 `MUT` 리터럴을 추출**해 생성했다. 손 전사(轉寫)를
  하지 않았으므로 문자열이 미묘하게 달라질 경로가 없다. 위 대조가 그것을 확인한다.

★ `NOT_APPLICABLE 0` — 69종 **전부** 현재 정본에서 앵커가 성립했다.
  설계 단계에서 「일부는 낡아 성립하지 않을 것이 거의 확실하다」고 적었는데
  **틀렸다.** 그대로 정정한다. 다만 한 건은 개수가 달라졌다:

```
SELFTEST-X-3   원 harness 작성 시점 `_t4_` 참조 3곳 → 현재 정본 6곳
               원 harness 는 개수를 세지 않고 replace-all 했다
               이관하면서 「모든 참조를 바꾼다」는 의도를 현재 소스에 대해 6으로 **선언**했다
               ⛔ 정본이 바뀌면 INVALID_RUN 으로 드러난다 — 조용히 따라가지 않는다
```

## 2. ② deterministic execution

```
격리 방식      변이마다 git archive HEAD → 새 임시 tree (69회, 경로 전부 상이)
__pycache__    baseline 전 · baseline 후 · 변이 실행 후 = 변이당 3지점 검사
               69 × 3 = 207지점 · 위반 0
정본 worktree  git status 무변경 (tracked 변경 0)
재현           동일 HEAD 재실행 → partition · verdict · killers_fired ·
               mutated_sha256 **전부 동일**
```

### `-B` 정정을 그대로 유지했다 (CIO 지시)

`-B` / `PYTHONDONTWRITEBYTECODE` 는 pyc **쓰기**만 막고 **읽기**는 막지 않는다.
따라서 determinism 수단이 **아니다.** 여기서는 일회용 tree 에 pyc 를 남기지 않는
**위생** 목적으로만 쓰고, 실제 계약은 위 207지점의 **검사**가 진다 — 가정하지 않는다.

### ★ 검증기를 검증했다 (음성 대조)

「절대 실패할 수 없는 검사」를 만들지 않기 위해 세 가지를 실측했다.

```
__pycache__ 를 심은 tree      → IsolationError 발생 (검사가 공허하지 않다)
깨끗한 tree                   → 통과 (항상 실패하지도 않는다)
치환해도 소스가 그대로인 변이  → INVALID_RUN «변이 후 소스가 baseline 과 동일하다»
현재 정본에 없는 앵커          → NOT_APPLICABLE (실패로 집계되지 않는다)
```

## 3. ③ expected-killer attribution — 가장 많은 것이 나온 축

### 3-1. 최초 선언 기준

```
KILLED 54 / 69 = 78.3%      선언한 검사가 실제로 실패한 비율
불일치 15건
  ├ 9건  내 선언 오류 — 계약을 **소유한 검사**를 잘못 지목했다
  └ 6건  회귀 CRASH — expected_killers 가 실행조차 되지 않았다
```

★ **이 숫자가 이번 Gate 의 핵심이다.** 예전 방식(`rc != 0`)으로는 15건 모두
  「잡힘」이었다. 즉 **판별력의 21.7% 는 내가 근거 없이 주장한 것**이었다.

### 3-2. 9건의 선언 수정 — 전부 `declaration_history` 에 남겼다

| 변이 | 내가 선언한 검사 (불발) | 실제로 계약을 소유한 검사 |
|---|---|---|
| MSFT-EXHIBIT-M1 | `0건 → 거부` | `filename=msft-ex99_1.htm → 거부` 군 |
| MSFT-EXHIBIT-M6 | `후보 건수가 남는다` | `후보 … 가 로그에 있다` 군 |
| MSFT-EXHIBIT-M12 | `<TEXT> 앞 헤더만 본다` | `본문의 phantom.htm 이 후보로 …` |
| MSFT-EXTRACT-P6 | `identify 가 AZURE_ROW 를 참조한다` | `identify 와 결합 가능성이 어긋나지 않는다` |
| MSFT-OBSERVE-Q8 | `→ 거부한다` | `걸러진 후보의 기간 신호를 남긴다` |
| CAPAZ-N1 | `부분 문자열**이다` | `보존된 조각에 엔티티가 원문 그대로 남는다` |
| CAPAZ-N3 | `레이아웃 표를 반쯤 자르지 않는다` | `제목을 포함한다` |
| CAPAZ-N7 | `엔티티를 푼 내용은 통과하지 못한다` | `부분 문자열임을 실행 중 검증한다` |
| CAPAZ-N10 | `제목을 포함한다` | `태그 분할 (<b>cloud</b>) → 슬라이스 성공` |

수정 후 **63 / 69 = 91.3%**. 남은 6건은 전부 CRASH 이며 수정으로 닫히지 않는다.

⛔ 수정 기준을 한 줄로 고정했다: **「계약을 의미상 소유하는 검사」로만 바꾸고,
   소유 검사가 없으면 만들지 않고 MISATTRIBUTED 로 남긴다.**

## 4. ④ verdict partition

```
KILLED 63 + SURVIVED 0 + MISATTRIBUTED 6 + NOT_APPLICABLE 0 + INVALID_RUN 0 = 69
catalog total = 69                                              ✔ 합이 정확히 같다
각 변이가 정확히 하나의 verdict 를 갖는다                        ✔
```

⛔ `69/69 KILLED` 를 성공조건으로 삼지 않았다 (CIO 지시).

## 5. ⑤ provenance completeness

변이 1건마다 남는 것:

```
mutation_id · catalog_file · origin_harness · origin_label · note
target_file · regression · expected_killers · declaration_history
checkout_identity {head, base_tar_sha256, checkout_dir}
baseline_sha256 · mutated_sha256 · source_changed · anchor_found/anchors_expected
executed_witness {checkout, target 상대경로, mutated sha256, subprocess cwd,
                  imported module __file__, imported module sha256, consistent}
baseline {outcome, reported_fail_count}
mutated  {outcome, reported_fail_count, failed_checks[], noise_lines_removed,
          parse_exact, stderr_tail}
verdict · verdict_reason · killers_fired
```

```
필수필드 완비        69/69
witness 일관         69/69   (import 경로 62 · main script 7)
provenance_missing   0건
```

★ `main script 7` — `test/test_c4_sec_edgar.py` 를 변이하는 7건은 회귀 자신이 대상이다.
  CPython 은 `__main__` 을 pyc 로 캐시하지 않으므로 이 경로에는 stale 위험이 없다.

### 실패 줄 해석의 한계를 숨기지 않았다

회귀의 `check()` 와 **collector 자신의 진단 출력**이 둘 다 `✗` 로 시작하고,
`capture_azure_fixture.py:169` 는 들여쓰기까지 같다. 세 겹으로 막았다 —
① 정확히 두 칸 들여쓴 줄만 ② baseline 에 이미 있던 줄 제외 ③ 남은 개수를 회귀가
스스로 보고한 `N PASS / M FAIL` 의 M 과 대조. ③ 이 어긋나면 `parse_exact=false` 로
provenance 불완전 판정한다. 실제로 개발 중 CAPAZ-N2 가 이 경로로 걸렸고,
①을 넣어 닫았다. ⛔ 회귀 출력 형식을 고쳐 해결하지 않았다 (범위 밖).

---

## 6. ★ 별건 — 이번 run 이 드러낸 것 (⛔ 이번 범위에서 고치지 않았다)

### 별건 1 ★★ 이름이 주장하는 것을 판별하지 못하는 정적 검사 — 이미 판정된 계열의 재발

```
test/test_msft_azure_cc.py
  check("★ identify 가 AZURE_ROW 를 실제로 참조한다",
        "AZURE_ROW" in __import__("inspect").getsource(M.identify))
```

`identify` 안 `AZURE_ROW` 출현 **3곳** 중 코드는 **1곳**뿐이고 나머지는 주석과
문자열 리터럴이다. 변이 P6 이 **코드에서 지웠는데도 이 검사는 통과했다.**

★ CIO 가 이미 판정한 **「정적 계약 검사는 문자열 검색이 아니라 AST 로 한다」**
  의 위반 사례다. 이 판정 이전에 만들어진 검사이며, 같은 계열이 더 있는지는
  조사하지 않았다 (범위 밖).

### 별건 2 ★ 단언이 이름보다 약한 검사

```
test/test_capture_azure_fixture.py
  check("★ 레이아웃 표를 반쯤 자르지 않는다 (바깥 표까지 닫는다)",
        s is not None and s.rstrip().endswith("</table>"))
```

**안쪽 표에서 끊어도** 문자열은 `</table>` 로 끝난다. 변이 N3(바깥 표까지 닫지 않음)
에서 이 검사는 통과했고, 실제로 잡은 것은 제목 포함 검사였다.

### 별건 3 ★ fixture 가 계약을 판별하지 못하는 검사

```
check("★ <TEXT> 앞 헤더만 본다 — SEQUENCE 가 본문 값(99)으로 오염되지 않는다", …)
```

변이 M12(`<TEXT>` 로 자르지 않음)를 적용해도 `_body_noise` 에서는 블록 3건 ·
SEQUENCE `['1','2','3']` 로 **동일했다**(실측). 본문 오염을 실제로 잡은 것은
phantom/decoy 검사군이다.

### 별건 4 ★★ 회귀가 보고하지 않고 **죽는다** — 14/69

```
변이 실행 중 회귀가 예외로 중단된 경우   14건 / 69
  ├ 그 전에 expected_killer 가 이미 실패했다   8건 → KILLED
  └ 실행되기 전에 죽었다                      6건 → MISATTRIBUTED
```

★ **8건의 KILLED 는 「죽기 전에 그 검사까지 도달했다」에 의존한다** — 검사 순서가
  바뀌면 MISATTRIBUTED 가 될 수 있다. 판별력이 실행 순서에 의존한다는 뜻이다.
★ 예외 종류: `HeaderPreconditionError` 2 · `TypeError: NoneType` 4 ·
  `IndexError` 4 · `AssertionError` 1 · `FileNotFoundError` 3.
  이 중 `FileNotFoundError` 3건은 fixture 파일명을 바꾸는 변이라 **정상적인 검출**이다.

⛔ 이 별건들은 **회귀의 판별력 문제**이며, 이번 승인 범위(mutation infrastructure)
   밖이다. 하나도 고치지 않았다.

---

## 7. CIO 판정이 필요한 항목

```
1. CRASH 를 5분할 어디에 넣을 것인가
   현재는 MISATTRIBUTED + `mutated.outcome == "CRASH"` + 사유로 남겼다.
   「expected_killers 는 모두 PASS」라는 정의에 정확히 맞지 않는다 —
   실행되지 않은 것이지 통과한 것이 아니다. ⛔ 6번째 verdict 를 임의로 만들지 않았다.

2. 별건 1~4 의 처리 순서
   특히 별건 1 은 이미 판정된 「정적 검사는 AST」 계열의 재발이다.

3. 별건 4 의 성격 분류
   회귀가 예외로 죽는 것을 「검출」로 볼 것인가, 「보고 실패」로 볼 것인가.

4. SELFTEST-X-3 의 anchors_expected = 6 선언 (§1)
   개수를 정본에 맞춰 선언한 것이 맞는지, 아니면 변이를 다시 쓸 것인지.
```

## 8. 하지 않은 것

- 회귀 수정 **0줄** — SURVIVED 0 이었고, MISATTRIBUTED 6 도 고치지 않았다
- production · fixture · Rule 상태 · capture metadata · 공용 helper **무변경**
- `run_all.py` · Actions PASS 정의 **무변경** (CI 5번째 조건 미승인 상태 유지)
- 결과 파일 **미커밋** (`--json` 은 선택 출력이며 정본에 넣지 않는다)
- code-object fingerprint · canary · checked-hash **미도입** (CIO 판정 e·f)

## 9. 상태

```
mutation harness hardening
  DESIGN                  APPROVED @ f2ad6f8
  IMPLEMENT               DONE @ 11ee80d — 판정 대기
  CI 5th gate             NOT APPROVED (유지)
  production              NO CHANGE

authoritative historical mutation evidence
  OLD (표본 1회 · race)    QUALIFIED — 대체됨
  NEW @ 11ee80d           69종 · KILLED 63 / SURVIVED 0 / MISATTRIBUTED 6 /
                          NOT_APPLICABLE 0 / INVALID_RUN 0 · 2회 재현

신규 별건 (전부 OPEN · 미착수)
  별건1 정적 검사가 문자열 검색이다 (AST 판정 위반 재발)     ★★
  별건2 단언이 이름보다 약한 검사                            ★
  별건3 fixture 가 계약을 판별하지 못하는 검사               ★
  별건4 회귀가 보고하지 않고 죽는다 14/69                    ★★

기존
  C4 parser/selection/build_header                CLOSED
  TSMC fixture coverage cardinality               CLOSED @ 98bd6a7
  P3 · RULE-0003/0007/0008                        CLOSED · READY 유지
  capture unit metadata                           OBSERVABILITY DEBT · HOLD
  FI-3 frozen input tamper                        KNOWN GAP · NOT GATED
  공용 helper inventory                            후순위
```

```
UNDEFINED 3 · MISSING 18 · SOURCE_UNRESOLVED 12 · BLOCKED 18 · READY 7
consumable_by_evaluator = false · Production HOLD
```
