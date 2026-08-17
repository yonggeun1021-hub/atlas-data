# 별건 4 수정 결과 + 69종 authoritative revalidation (CIO 승인 범위 한정)

```
HEAD                 350948b
authoritative run    2026-08-16 · catalog 69 · 2회 반복 완전 재현
                     (문서 커밋 후 350948b 에서 재실행 · 결과 동일)
정본 worktree        무수정 (변이는 전부 일회용 checkout 안에서만)
```

⛔ 범위 준수: production/collector **무변경** · fixture **무변경** · Rule 상태
**무변경** · capture metadata **무변경** · `run_all.py` · Actions PASS 4-AND 정의
**무변경** · 별건 1~3 **미착수** · 결과 파일 **미커밋**.

---

## 0. 결과 — 목표와 실측

```
                      수정 전        수정 후
CRASH                    14      →      0     ★ 목표 달성
평가되지 못한 검사        949      →    221     (아래 §4 에서 분해)
KILLED                   63      →     69
SURVIVED                  0      →      0
MISATTRIBUTED             6      →      0
NOT_APPLICABLE            0      →      0
INVALID_RUN               0      →      0
```

★ **69/69 를 목표로 삼지 않았다.** 그렇게 나온 이유는 §2 에 적었고, 근거가
  타당한지 CIO 가 직접 볼 수 있게 「무엇이 무엇을 잡았는지」를 그대로 남겼다.
★ ERROR·SKIPPED 가 KILLED 근거가 된 건 **0건**이다 (기계적으로 확인).

## 1. 필수 Gate — 전부 PASS

```
① baseline 69회 전부  FAIL 0 · ERROR 0 · SKIPPED 0          PASS
     c4 237 PASS · msft 309 PASS · capture 58 PASS
② 기존 검사 이름 집합 보존   147 / 147 / 40  그대로           PASS
③ 기존 검사 상대 순서  S == S' (위치 무관 AST 대조)           PASS
④ 추가 `need`/`guard` 호출 개수 고정  msft 6 · c4 6 · capture 0  PASS
⑤ ERROR·SKIPPED 를 killer 로 인정한 건 0                     PASS
⑥ partition 합 == 69 · INVALID_RUN 0 · provenance 완전       PASS
⑦ run_all.py  50 PASS / 0 FAIL (+ mode guard) — 불변         PASS
⑧ 2회 반복 재현: partition · verdict · killers · 미평가 합 동일  PASS
```

★ ④의 보조 증거 — `guard(...)` 의 dependents **29건이 전부 실제 검사 이름과
  짝지어짐**을 AST 로 대조했다. 이름을 지어내 SKIPPED 를 만든 곳이 없다.

## 2. 이전 MISATTRIBUTED 6건이 무엇으로 잡혔나

⛔ 숫자를 맞추려고 손대지 않았다. **원래 선언했던 killer 가 그대로 발화**했다 —
  crash 가 그것을 가리고 있었을 뿐이라는 것이 이번 결과로 확인된다.

| mutation | 발화한 expected_killer |
|---|---|
| `C4-PRECOND-W-3` | 전제 판별이 위치가 아니라 의미다 |
| `C4-PRECOND-W-4` | 연도 단독 셀은 값 행으로 세지 않는다 |
| `MSFT-EXTRACT-P7` | GAAP 을 집지 않았다 |
| `MSFT-HEADER-S3` | 컬럼 헤더 문면은 그대로 남아 있다 |
| `MSFT-HEADER-S4` | header 구성에 행·열 번호를 박지 않았다 |
| `MSFT-HEADER-S6` | 값 행 판별이 퍼센트 값 표식이다 |

★ 여섯 건 모두 **최초 catalog 선언(의도 기반)** 그대로다. 사후에 고친 것이 아니다.

## 3. 무엇을 만들었나

### 3-1. 공용 test helper `test/checkkit.py` (CIO 판정 5)

`PASS/FAIL/ERROR/SKIPPED` · `check` · `need` · `skip` · `guard` · `section` ·
`atexit` 요약 · 환경변수로 켜지는 기계 판독 trace 를 한 곳에서 관리한다.
⛔ **test 전용이다.** production/collector 로 공용화하지 않았다.

`atexit` 의 한계를 계약에 명시했다 — 처리되지 않은 예외로 죽어도 실행되지만,
**처리되지 않은 signal · 내부 fatal error · `os._exit()`** 에서는 실행되지 않는다.
그래서 runner 의 `CRASH` 는 이제 「**trace 도 요약도 남지 않은 실행**」으로 좁아졌다.

### 3-2. 층 1 — dependency precondition (관측된 11 mutation · 7 지점)

값의 부재를 **먼저 `need()` 로 판정**하고, 성립할 때만 그 값을 쓴다.
⛔ 예외를 잡지 않는다 — 예외가 날 일 자체를 없앤다.

```
msft  A-3 결합 결과 · D-4 Azure 표 후보 · E-1 대상 행 · E-4 기준 결합 · E-5 위쪽 data row
c4    ⑰-1·⑰-4·⑱-C·⑲-1 fixture 존재 · ⑲-1 결정표 후보 1건 · T-13e 후보 1건
```

⛔ 같은 idiom 일괄 변환(msft≈41 · c4≈37)은 **하지 않았다** — 승인 범위 밖이다.

### 3-3. 층 2 — 절 경계 `with section(...)` 77곳

절 안의 예상하지 않은 예외를 **ERROR 로 기록**하고 다음 절로 간다.
⛔ blanket `except Exception: pass` 가 아니다 — ERROR 는 결과로 남고 exit code 를
1 로 만든다. `Exception` 만 잡고 `SystemExit`·`KeyboardInterrupt` 는 통과시킨다.

### 3-4. ★ 재들여쓰기가 기계적이었음을 별도로 검증했다 (CIO 판정 4)

```
`with section(...)` 을 원래의 print + 본문으로 되돌린 뒤 HEAD 와 top-level 문 단위 대조
  → 공통 문의 상대 순서 동일 · 차이는 head/tail 4건뿐 (check 정의 · 요약 · exit)
```

★ **첫 시도에서 여러 줄 문자열 리터럴 47개가 들여쓰기로 오염됐다.**
  (`ONLY_TH` 등 합성 HTML fixture 의 본문 줄이 4칸씩 밀렸다. 검사는 그래도
  전부 통과했으므로 **테스트 결과만 보고 있었다면 발견하지 못했다.**)
  `tokenize` 로 여러 줄 토큰의 이어지는 줄을 제외해 다시 했고, 문자열 상수 집합이
  HEAD 와 같음을 대조했다 — **새로 생긴 문자열 0건**.

### 3-5. runner — 우회 두 개를 제거했다

```
제거   stdout 의 `✗` 줄을 세 겹으로 걸러 해석하던 휴리스틱
제거   crash 시 진행 위치를 AST 로 역추정하던 방식
근거   기계 판독 trace 하나 (check identity · 결과 · 순서 · 절 · 요약 카운터)
```

⛔ trace 는 checkout **밖**에 쓴다 — 일회용 tree 를 오염시키지 않는다.
⛔ ERROR·SKIPPED 는 killer 로 인정하지 않는다. KILLED 는 **expected_killer 가
   FAIL** 한 경우뿐이다.

## 4. ★ 남은 221 — 분해해서 적는다

```
모든 변이에서 진입한 절 수 == baseline 절 수   (c4 32/32 · msft 36/36)
⇒ **절 단위로 잃은 계약은 0이다.** 앞선 ERROR 때문에 뒤 절이 통째로 사라지는 일은 없다.
```

| 갈래 | 건수 | 성격 |
|---|---|---|
| ERROR 가 난 **절 안**의 후속 검사 | **176** | 그 절의 입력이 없어진 뒤의 검사 |
| 회귀의 **기존 조건 분기** (`if not found: continue` 등) | **45** | ⛔ 이번 변경과 무관 — 원래 있던 경로 |

★ 갈래 2 는 **원래 회귀에 있던 조용한 건너뜀**이다 (`C4-ROW-U1/U2/U3` 각 3건 ·
  `SELFTEST-X-1` 17건 등, ERROR 0건인데 미평가가 있다). 이번 변경이 만든 것이
  아니지만, **이번에 처음 보이게 됐다.** 별건 후보로 올린다.

### 4-1. ERROR 21건이 난 곳 — 전부 승인 범위 밖의 새 지점이다

```
msft_azure_cc.py:341   E-2·E-3·E-4 가 rows_for() 산출물을 확인 없이 쓴다   (Q6 3 · P2 3)
c4 run_doc:build_header  ② ③ ④ ⑤ ⑩ 이 production 호출을 감싸지 않는다      (W-3 6 · W-4 1 · W-2 1)
fixture 부재 연쇄        ⑰-3b · ⑱-C · ⑱-B · ⑲-2                        (X-3 4)
NameError 연쇄           ⑲-3 `_rows2` · ⑪ `b_full`                       (X-3 1 · W-3 1)
```

★ **설계 §8-2 에서 예고한 `NameError` 연쇄가 실제로 2건 발생했다.** 죽은 절이
  뒤 절에 이름을 물려주는 경우이며, 그 뒤 절은 **독립적으로 평가 가능하지 않다.**
★ 나머지는 층 1 을 적용하지 않은 지점이다. CIO 지시대로 **관측된 11지점만**
  닫았고, 이 지점들은 이번 run 에서 **처음 관측**됐다. ⛔ 고치지 않았다.

### 4-2. Gate 문장에 대한 해석 — 두 가지를 모두 적는다

> 독립적으로 평가 가능한 후속 check 가 앞선 ERROR 때문에 미실행된 건 = 0

```
절 단위 해석    0건 — 모든 절이 진입됐다 (측정 완료 · 위)
검사 단위 해석  176건이 ERROR 절 **안**에 남아 있다. 그중 몇 개가 그 절의 잃어버린
                입력과 무관하게 독립적으로 평가 가능했는지는 **증명하지 못했다.**
```

⛔ 「전부 의존적이므로 0건이다」라고 적지 않는다. 확인하지 않은 것을 확인했다고
   적지 않는다. 검사 단위로 0 을 만들려면 **절보다 작은 경계**(loop 반복 단위)가
   필요하고, 그것은 이번 승인 범위 밖이다.

## 5. catalog 재앵커 5건 — 숨기지 않고 적는다

절 경계 도입으로 `test/test_c4_sec_edgar.py` 가 4칸 들여쓰기되면서 **SELFTEST 변이
5건의 앵커가 성립하지 않게 됐다.**

```
1차 run   NOT_APPLICABLE 5 · 미평가 191
재앵커     변이의 **의도는 그대로**, 앵커 문면만 새 들여쓰기를 따라간다
          (X-5 는 줄 중간에서 시작하는 앵커라 이어지는 줄만 들여썼다)
2차 run   NOT_APPLICABLE 0
```

★ 이것은 **새 infrastructure 가 제대로 동작한 사례**다 — 대상 소스가 바뀌자
  조용히 통과하지 않고 `NOT_APPLICABLE` 로 드러났다. 변경 사유는 catalog 의
  `declaration_history` 에 남겼다.

## 6. 이번 run 이 새로 드러낸 것 (⛔ 전부 미착수 · 별건 후보)

```
별건 5  회귀에 **원래 있던 조용한 건너뜀** 45건 (`if not found: continue` 등)
        — 검사 수만 줄고 아무 기록도 남지 않는다. 이번에 처음 보였다.
별건 6  층 1 미적용 지점 — `msft E-2·E-3·E-4` 가 `rows_for()` 산출물을,
        `c4 run_doc` 이 production 호출을 확인 없이 쓴다 (ERROR 21건의 출처)
별건 7  절이 뒤 절에 이름을 물려줄 때의 `NameError` 연쇄 (2건 관측)
```

기존 별건은 그대로 열려 있다 — 1(정적 검사가 문자열 검색) · 2(단언이 이름보다 약함) ·
3(fixture 가 판별하지 못함).

## 7. 하지 않은 것

- production/collector · fixture · Rule 상태 · capture metadata 변경
- `run_all.py` · Actions PASS 정의 변경 (CI 5th gate 여전히 NOT APPROVED)
- 별건 1~3 · 5~7 착수
- 검사 순서 이동 · 기존 검사 이름 변경 · 새 기대 예외 계약 발명
- 결과 파일 커밋 (`--json` 은 선택 출력이다)

## 8. 상태

```
별건 4  조사        CLOSED @ eaa5d66
        수정 설계    APPROVED @ 9252fbb
        수정 구현    DONE — 판정 대기
          CRASH 14 → 0 · 미평가 949 → 221 (ERROR 절 안 176 · 기존 조건 분기 45)

mutation infrastructure          CLOSED @ 11ee80d (사유 코드화 4d74971 · trace 결선 857c4bf~)
historical mutation evidence     REBUILT / AUTHORITATIVE @ 350948b
  KILLED 69 · SURVIVED 0 · MISATTRIBUTED 0 · NOT_APPLICABLE 0 · INVALID_RUN 0
attribution quality              69/69 · CRASH 로 가려졌던 6건이 원 선언대로 발화
CI 5th gate                      NOT APPROVED — 유지

별건 1  정적 검사가 문자열 검색     OPEN · 미착수
별건 2  단언이 이름보다 약한 검사    OPEN · 미착수
별건 3  fixture 가 판별하지 못함    OPEN · 미착수
별건 5  기존 조용한 건너뜀 45건      OPEN(신규) · 미착수
별건 6  층 1 미적용 지점            OPEN(신규) · 미착수
별건 7  NameError 연쇄              OPEN(신규) · 미착수

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
