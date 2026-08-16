# 별건 4 — 수정 설계 (CIO 지시 2026-08-16)

⛔ **설계만이다. 코드 변경 0줄 · 구현 미착수 · 회귀 무수정.**
⛔ 별건 1~3 미착수 · CI 5th gate 무변경 · production 무변경.
★ 수정 계약(CIO 문장)을 그대로 목표로 삼는다:

> **한 계약의 실패 또는 그 계약을 준비하는 값의 부재가,
> 독립적으로 평가 가능한 후속 계약까지 죽이지 않아야 한다.**

---

## 0. 실측 먼저 — 회복 가능량이 기전별로 갈린다

절(section) 경계는 **이미 소스에 있다** — 최상위 `print("A-1 …")` 표시다.

```
test_msft_azure_cc.py     절 37 · check 147 (전부 최상위)
test_c4_sec_edgar.py      절 33 · check 147 (146 최상위 + 1 함수 안)
test_capture_azure_fixture.py  절 10 · check  40   (crash 0건)
```

14건이 죽은 절이 **뒤 절에 물려주는 이름**을 셌다 (AST).

```
물려주는 이름 0개   11건   ← 뒤 절이 그대로 독립적으로 평가 가능하다
물려주는 이름 1~2개  3건   (W-3·W-4 `label` · W-2 `_rows2,_di2` · Q6 `_DL,lbl`)
```

| | 회복 가능한 미실행 검사 | 잔존 |
|---|---|---|
| 죽은 절 **뒤 절**의 검사 | **914 / 949 (96.3%)** | — |
| 죽은 절 **안**의 후속 검사 | — | **35** |

⇒ **미실행 949건의 96.3% 는 죽은 절과 데이터 의존이 없다.**
  「독립적으로 평가 가능한 후속 계약」이 실제로 대부분이라는 뜻이며,
  CIO 의 수정 계약이 이 회귀들에서 성립 가능하다는 실측 근거다.

## 1. 두 층으로 나눈다 — 기전이 다르면 수단도 다르다

```
층 1  dependency precondition   A(7) · C(3) · D(1) = 11건
      값의 부재를 **먼저 check() 로 판정**하고, 성립할 때만 그 값을 쓴다.
      ⛔ 예외를 잡지 않는다. 예외가 날 일 자체를 없앤다.

층 2  절 경계 (bounded)          B(3) = 3건
      production 호출 자체가 예외를 내는 경우. 예외를 **ERROR 로 기록**하고
      다음 절로 간다. ⛔ 성공으로 바꾸지 않는다.
```

### 층 1 이 닫는 것 — 기전별 근거

| 기전 | 왜 층 1 로 닫히는가 |
|---|---|
| **A** (7) | `b_same["gaap"]` 앞에 `check("전제: 결합 성립", b_same is not None)` 을 두면 예외가 나지 않는다 |
| **C** (3) | `_fx(name)` 앞에 존재 계약을 판정하면 `open()` 이 다시 터지지 않는다 |
| **D** (1) | `build_header(rows, di)` 앞에 `di is not None` 을 판정하면 None 이 production 에 들어가지 않는다 |

### 층 2 가 필요한 것 — B 만 남는다

```
W-2  변이가 넣은 assert            → AssertionError
W-3  전제를 위치 계약으로          → 정상 입력에서 HeaderPreconditionError
W-4  연도 단독 셀도 값으로         → 정상 입력에서 HeaderPreconditionError
```

★ **정상 입력에 대한 production 호출이 예외를 낸다.** 호출 전에 판정할 수 있는
  전제가 없다 — 예외는 production 내부에서 결정된다. 층 1 로는 닫히지 않는다.

### 층 1 만 채택할 경우의 잔존량 (실측)

```
CRASH   14 → 3   (B 만 남는다)
미실행  949 → 305   (W-2 15 · W-3 145 · W-4 145)
```

⇒ 층 1 만으로도 **644건(67.9%)** 이 회복된다. 층 2 없이 갈지는 §6-a 판정 항목이다.

## 2. 층 1 설계 — dependency precondition

새 어휘 하나만 도입한다.

```python
def need(name, cond, extra=""):
    """전제 계약. check() 와 동일하게 기록하되, 성립 여부를 **돌려준다**."""
    check(name, cond, extra)
    return bool(cond)
```

사용 형태 (⛔ 예시다. 실제 문구는 구현 승인 후 확정한다):

```python
if need("전제: [기본] 결합이 성립한다", b_same is not None):
    check("같은 분기: 두 컬럼 값이 동일해 구별 불가", b_same["gaap"] == b_same["cc"])
else:
    skip("같은 분기: 두 컬럼 값이 동일해 구별 불가", "전제 미성립 — 결합 실패")
```

### 왜 `skip` 이 필요한가 — 이미 겪은 실패 모드다

전제가 깨졌을 때 그냥 `continue` 하면 **검사 수만 조용히 줄어든다.**
`98bd6a7`(TSMC fixture coverage cardinality)에서 닫은 것과 **같은 구조의 결함**이다.
그래서 건너뛴 검사는 **이름과 미성립 사유를 달고 `SKIPPED` 로 남긴다.**

### 남용을 막는 불변식 — 이것이 이 층의 안전장치다

```
불변식 1   baseline(무변형)에서  SKIPPED == 0        ← 하나라도 있으면 정상 경로를 가리고 있다
불변식 2   baseline 검사 이름 집합이 변경 전후 동일   ← 기존 계약을 지우지 않았다
불변식 3   추가된 `need(...)` 전제 검사는 **별도 목록으로 선언**하고 개수를 고정
불변식 4   SKIPPED 는 PASS 가 아니다 — 요약에 별도 칸으로 남고, mutation runner 는
           SKIPPED 를 killer 발화로 인정하지 않는다
```

★ 불변식 1 이 핵심이다. `need` 가 정상 경로를 조용히 건너뛰는 데 쓰이면
  **baseline 이 즉시 그것을 드러낸다.**

### 적용 범위 — 규모

관측된 crash 지점만이 아니라 같은 idiom 전체를 봐야 한다.

```
test_msft_azure_cc.py   bind 결과 첨자 36 · x[0][ 4 · [...][-1] 1   ≈ 41곳
test_c4_sec_edgar.py    ["bound"…] 19 · x[0][ 2 · _fx( 16          ≈ 37곳
test_capture_azure_fixture.py                                        0곳
```

⛔ **전부를 고칠지, 관측된 7+3+1 지점만 고칠지는 판정 항목이다** (§6-b).
   ★ 잠재 지점의 전체 집합은 **알 수 없다** — mutation 이 닿아야 드러난다.
   현재 mutation 이 닿는 회귀는 19개 중 3개뿐이다.

## 3. 층 2 설계 — 절 경계

### 3-1. 결과 어휘 — `ERROR` 는 새 결과값이지 새 계약이 아니다

```
PASS      계약 성립
FAIL      계약 위반 (assertion failure)
ERROR     계약을 평가하지 못했다 (예상하지 않은 예외)
SKIPPED   전제 미성립으로 평가하지 않았다
```

⛔ **금지 사항 — 명시한다.**
```
· blanket `except Exception: pass` 금지. 반드시 ERROR 로 기록한다.
· ERROR 가 있으면 회귀는 exit != 0 이다. 예외를 성공으로 바꾸지 않는다.
· ERROR 를 mutation 의 KILLED 근거로 쓰지 않는다 (runner 계약 · §5).
· 특정 예외를 「기대 계약」으로 새로 발명하지 않는다.
  현재 catalog 69종 중 예외 자체가 계약인 변이는 **0건**이며, 그 조사 결과를 유지한다.
· attribution 을 맞추려고 검사 순서를 이동하지 않는다 (§7 불변식 5).
```

### 3-2. 절 경계를 어떻게 구현하는가 — 선택지 (⛔ 고르지 않았다)

| | 방식 | 모듈 스코프 데이터 흐름 | diff 성격 | 위험 |
|---|---|---|---|---|
| **가** | 절마다 `with section("A-1"):` (context manager 가 예외를 ERROR 로 기록하고 억제) | **보존됨** (`with` 는 스코프를 만들지 않는다) | 80개 절 전체 **재들여쓰기** — 기계적이지만 diff 가 크다 | 큰 diff = review 난이도 |
| **나** | 절마다 함수로 감싸 호출 | **깨진다** — 절을 넘어 쓰이는 이름이 msft 44 · c4 46개 있다. `global` 선언이 대량 필요 | 재작성에 가깝다 | 높다 |
| **다** | 파일을 절 단위로 잘라 공유 namespace 에서 `exec` | 보존됨 | 재들여쓰기 없음 | traceback 이 흐려진다 · 파일이 「평범한 스크립트」가 아니게 된다 |
| **라** | 층 2 를 채택하지 않는다 (층 1 만) | — | 최소 | CRASH 3 · 미실행 305 잔존 |

★ **「나」는 실측상 성립하지 않는다** — 절을 넘어 쓰이는 이름이 90개다.
★ 「가」와 「다」의 차이는 **정확성이 아니라 diff 형태와 traceback 품질**이다.

## 4. 목표 2 — observability

### 4-1. crash 에서도 요약이 남아야 한다

`atexit` 은 **처리되지 않은 예외로 죽어도 실행된다** (실측 확인).

```
$ python3 atx.py
checks running…
Traceback (most recent call last):
  …
ValueError: boom
[atexit] 3 PASS / 0 FAIL / 1 ERROR      ← ★ traceback 뒤에 그대로 나온다
exit=1
```

⇒ 세 회귀가 요약을 `atexit` 으로 내면 **crash 여부와 무관하게** 다음이 남는다.

```
실행된 check 수 · PASS · FAIL · ERROR · SKIPPED · 마지막으로 시작한 절
```

### 4-2. mutation runner 가 AST 추정에 의존하지 않으려면

현재 runner 는 `✗` 줄을 세 겹으로 걸러 해석하고(§CRASH_ATTRIBUTION_SURVEY §5),
이번 조사는 진행 위치를 **AST 로 역추정**해야 했다. 근본 원인은
**실행된 검사의 identity 가 기계 판독 가능한 형태로 남지 않는다**는 것이다.

| | 방식 | runner 가 얻는 것 | 대가 |
|---|---|---|---|
| **가** | 세 회귀가 **모든** check 를 stdout 에 출력 (현재 C4·capture 방식으로 통일) | 실행 identity | MSFT 출력량 증가 (147줄). collector 진단 출력과의 혼선은 **그대로 남는다** |
| **나** | 환경변수로 켜지는 **기계 판독 trace**(JSONL)를 별도 경로에 기록 | identity · 결과 · 순서 · 절 | 회귀에 출력 경로 개념이 생긴다. 정상 실행에는 영향 없음 |
| **다** | 가 + 나 | 둘 다 | 둘 다 |

★ **「나」는 `✗` 파싱 자체를 없앤다** — collector 진단 출력과 check 출력이 같은
  `  ✗ ` 로 시작하는 문제(§CRASH_ATTRIBUTION_SURVEY)가 구조적으로 사라진다.
★ 「가」만으로는 그 혼선이 남는다. ⛔ 어느 쪽인지는 판정 항목이다 (§6-c).

### 4-3. 세 회귀의 관측 의미 통일

```
현재   test_c4 · test_capture   ✓ 와 ✗ 를 모두 출력
       test_msft                실패만 출력 (성공은 카운터만)
목표   세 회귀가 같은 어휘(PASS/FAIL/ERROR/SKIPPED)와 같은 요약 형식을 쓴다
```

구현 위치 선택지 — ⛔ 고르지 않았다.

| | 방식 | 성격 |
|---|---|---|
| **가** | 새 공용 test helper 모듈 (`test/checkkit.py` 등) | 한 곳에서 관리. 단일 실패점이 생긴다. 선례 있음(`test/fault_injection.py`) |
| **나** | 세 파일에 같은 ~20줄을 각각 둔다 | 의존 없음. 세 곳이 어긋날 수 있다 |

## 5. runner 측 변경 (mutation infrastructure · 승인 범위 안)

```
· 요약 파싱: `N PASS / M FAIL` → PASS/FAIL/ERROR/SKIPPED 형식 대응
· `mutated.outcome` 에 ERROR 를 반영 (CRASH 는 「요약조차 없다」로 좁아진다)
· ★ ERROR·SKIPPED 는 killer 발화로 **인정하지 않는다** — KILLED 근거는
  여전히 「expected_killer 가 FAIL 했다」 하나뿐이다
· 「나」 채택 시: `✗` 3겹 휴리스틱을 trace 파싱으로 대체
```

⛔ `run_all.py` 는 회귀의 **exit code 만** 본다(실측 — stdout 을 파싱하지 않는다).
   따라서 요약 형식 변경은 `run_all.py` 를 건드리지 않는다.

## 6. 판정이 필요한 항목

```
a. 층 2 를 채택하는가
   채택       CRASH 14→0 · 미실행 949→35 목표 가능
   미채택(라) CRASH 14→3 · 미실행 949→305 · 예외 억제가 코드에 전혀 없다
b. 층 1 의 적용 범위
   관측된 11지점만 · 같은 idiom 전체(msft≈41 · c4≈37)
c. observability 방식
   가(전부 stdout) · 나(기계 판독 trace) · 다(둘 다)
d. 절 경계 구현 방식 (층 2 채택 시)
   가(context manager · 재들여쓰기) · 다(절 단위 exec) — 「나」는 실측상 불가
e. 공용 test helper 를 만드는가, 세 파일에 중복하는가
```

## 7. 재검증 계획 — CIO acceptance 를 그대로 쓰고 불변식을 더한다

```
baseline
  ① 세 회귀 전체 PASS · ERROR 0 · SKIPPED 0
  ② 변경 전후 **검사 이름 집합 동일** (추가된 need() 전제는 별도 선언 목록)
  ③ 기존 검사들의 **상대 순서 동일** — 기계적으로 대조한다 (불변식 5)
  ④ run_all.py 결과 불변 (50 PASS / 0 FAIL + mode guard)

mutation
  ⑤ 69종 전수 재실행 · partition 합 == 69 · INVALID_RUN 0 · provenance 완전
  ⑥ CRASH 14 → 0 (목표)
  ⑦ regression-crash 로 미실행된 독립 check 949 → 0 (목표)
  ⑧ ERROR·SKIPPED 가 KILLED 근거가 된 건 0건임을 결과로 증명

⛔ 성공조건으로 삼지 않는 것
  · 63/69 유지 · 69/69 KILLED · MISATTRIBUTED 감소
  · attribution 이 바뀌면 **새 evidence 대로 판정한다.**
    6건이 KILLED 로 바뀔 수도, 현재 KILLED 일부가 귀속 문제로 드러날 수도 있다.
    둘 다 허용한다 — 목적은 모든 변이에게 **끝까지 관측될 기회**를 주는 것이다.
```

★ **불변식 5** — 「검사 순서를 이동하지 않는다」를 증명 가능한 형태로 적는다:
  변경 전 검사 이름 순열을 S, 변경 후에서 추가 전제 검사를 제외한 순열을 S' 라 할 때
  **S == S'** 를 대조한다. 추가는 허용되고 이동은 허용되지 않는다.

## 8. 이 설계가 보장하지 못하는 것 — 미리 적는다

1. **잠재 crash 지점의 전체 집합을 모른다.** mutation 이 닿아야 드러나고,
   현재 mutation 은 승인 회귀 19개 중 **3개**에만 닿는다.
2. 층 2 를 채택해도, 죽은 절이 뒤 절에 이름을 물려주는 **3건**은 후속 절이
   `NameError` 로 다시 ERROR 가 될 수 있다 (`label` · `_rows2,_di2` · `_DL,lbl`).
   ⑦의 949→0 이 완전히 달성되지 않을 수 있다.
3. 죽은 절 **안**의 후속 검사 35건은 층 2 로 회복되지 않는다 — 층 1 의 몫이다.
4. `ERROR` 를 도입하면 「예외가 났다」가 결과로 남지만, **그것이 어떤 계약의
   위반인지는 여전히 사람이 읽어야 한다.**
5. 이 설계는 **회귀의 관측 구조**만 바꾼다. 별건 1~3(검사 자체의 판별력)은
   그대로 열려 있다.

## 9. 상태

```
별건 4  조사        CLOSED @ eaa5d66
        수정 설계    제출 · 구현 미승인 · 판정 대기 (§6 a~e)

mutation infrastructure          CLOSED @ 11ee80d (사유 코드화 @ 4d74971)
historical mutation evidence     REBUILT / AUTHORITATIVE @ 4d74971
  KILLED 63 · SURVIVED 0 · MISATTRIBUTED 6 (전부 CRASH_BEFORE_ATTRIBUTION) ·
  NOT_APPLICABLE 0 · INVALID_RUN 0
attribution quality              63/69 = 91.3% · OPEN
CI 5th gate                      NOT APPROVED — 유지

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
