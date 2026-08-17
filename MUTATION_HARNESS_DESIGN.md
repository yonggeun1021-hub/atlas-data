# mutation harness 정본화 — 설계 (CIO 지시 2026-08-16)

⛔ **설계만이다. 코드 변경 0줄 · harness 이관 미착수 · 구현 미승인.**
⛔ 범위는 **mutation infrastructure 뿐**이다.
   C4/MSFT production 코드 · 기존 fixture · Rule 상태 · capture metadata ·
   공용 helper 는 **이번 설계에서 건드리지 않는다.**
⛔ canary 를 필수 계약으로 전제하지 않는다. 실행 fingerprint 의 구현법을 미리 고정하지 않는다.
   **선택지를 실측과 함께 비교만 한다.**

---

## 0. ★ 먼저 내 직전 실측 주장을 정정한다

설계 근거를 만들려고 저장소 밖(`/tmp/mxv`, `/tmp/mxc`)에서 실측하던 중,
**내가 이 세션에서 먼저 말한 fingerprint 관련 주장 두 개가 틀렸다는 것을 확인했다.**
설계에 들어가기 전에 정정한다.

| 내가 앞서 말한 것 | 실측 결과 | 판정 |
|---|---|---|
| `marshal.dumps(code)` 는 **불안정**하다 | 같은 조건 3회 반복은 **동일**했다 (`aef3a9090002` ×3). 불안정한 조건은 「반복 실행」이 아니라 **pyc 로드 경로 vs 소스 컴파일 경로**였다 (`825b0cfd4cea` vs `aef3a9090002`) | **조건을 잘못 말했다 — 정정** |
| `sha256(co_code + repr(co_consts))` 는 **안정**한 fingerprint 다 | 인자 없는 장난감 함수에서만 안정했다. 실제 `c4_sec_edgar_check` 모듈에서는 로드 경로에 따라 **달라진다** (`a5c726a03e358cc6` vs `c701d9f7622467c3`) | **일반화가 틀렸다 — 정정** |

원인: `co_consts` 에는 **중첩 code object**(comprehension·내부 함수)가 들어 있고,
그 `repr` 은 **메모리 주소를 포함**한다. 장난감 함수에는 중첩 code object 가 없어서
안정해 보였을 뿐이다.

⇒ **작은 표본에서 성립한 성질을 일반 계약으로 격상하려던 것**이고,
   이 프로젝트가 반복해서 막아 온 실패 모드와 같은 것이다. 그대로 정정한다.

정정 후 실측: `co_consts` 의 중첩 code object 를 **재귀적으로 정규화**하면 안정하다.

```
재귀 정규화 fingerprint   pyc 없음 cf66c9f8c8baea14
                         pyc 있음 cf66c9f8c8baea14
                         pyc 있음(2회) cf66c9f8c8baea14
```

★ 이 사실 자체가 설계에 영향을 준다 — **§6 「마」(실행 fingerprint)는
  "간단한 방법"이 아니다.** 아래 비교에 반영했다.

---

## 1. 설계가 닫아야 하는 것 (CIO 지정 4항)

```
1. Execution determinism    timestamp/size race 가 결과에 영향을 못 준다
2. Per-mutation provenance  ID·대상·baseline SHA·mutated SHA·실행된 대상·
                            어떤 test 가 왜 실패했는지 (rc != 0 은 불충분)
3. Harness provenance       저장소 안에서 review/re-run 가능 · CI 편입 경계
4. Historical revalidation  기존 69종을 새 harness 로 돌렸을 때 무엇을 재확증할지
```

---

## 2. 영역 1 — Execution determinism

### 2-1. ★★ CIO 지시문에 들어 있던 전제 하나가 실측과 다르다

지시문은 「`-B`/bytecode 차단」을 determinism 수단의 하나로 열거했다.
**`-B` 는 이 결함을 막지 못한다.** 직접 측정했다.

```
target.py v1 실행 → pyc 생성
target.py 를 v2 로 덮어씀 (크기 동일 + mtime 같은 초로 고정)

A 그대로               f()=1   ← stale (기대: 2)
C  python -B           f()=1   ← ⛔ 여전히 stale
D  PYTHONDONTWRITEBYTECODE=1   f()=1   ← ⛔ 여전히 stale
E  PYTHONPYCACHEPREFIX=<새 경로> f()=2  ← 정상
F  __pycache__ 삭제     f()=2   ← 정상
G  checked-hash pyc     f()=3   ← 정상 (mtime·크기 동일해도 내용으로 무효화)
```

`-B` / `PYTHONDONTWRITEBYTECODE` 는 pyc **쓰기**만 막고 **읽기**는 막지 않는다.
**이미 존재하는 stale pyc 가 그대로 실행된다.**
⇒ 이 두 가지는 determinism 수단 목록에서 **제외해야 한다.** 설계에 반영했다.

### 2-2. 유효한 선택지 비교 (실측 포함)

| | 방식 | race 를 차단하는 원리 | 실측 비용 (`test_c4` 1회, 3회 평균) | 남는 위험 |
|---|---|---|---|---|
| **D-1** | 매 변이 전 `__pycache__` 삭제 — **현재 방식** | pyc 자체를 없앤다 | 0.223s (기준 0.213s) | 삭제 대상 디렉터리를 **하나라도 빠뜨리면** 그 모듈만 조용히 stale. 대상이 늘면 누락 위험이 는다 |
| **D-2** | `PYTHONPYCACHEPREFIX` 를 **변이마다 새 임시 경로**로 | 캐시 네임스페이스를 격리 | 0.247s | 경로를 재사용하면 무효. 하위 프로세스에만 적용되므로 harness 자신이 import 하면 안 됨 |
| **D-3** | pyc 를 `checked-hash` 로 생성 | mtime/size 를 아예 안 본다 | (사전 컴파일 1회 필요) | 대상 모듈을 **미리 컴파일해 둬야** 하고, 새로 생긴 import 는 여전히 timestamp 모드 |
| **D-4** | 변이를 **저장소 사본**(임시 디렉터리)에서 수행 | 프로세스마다 새 트리 → 캐시 공유 자체가 없음 | 사본 생성 비용 (미측정) | 사본 경로 의존성 확인 필요 |
| **D-5** | D-1 + **실행 witness 검증**(§6) | 차단이 아니라 **검출** | witness 비용 | 차단이 아니므로 단독으로는 부족. 다른 항과 **조합**용 |

★ **D-1~D-4 는 전부 「원인 제거」이고 서로 대체 가능하다.**
  D-5 만 성격이 다르다 — 「원인 제거가 실패했는지 그 run 안에서 스스로 검출한다」.
★ 비용은 전부 무시할 수준이다 (변이 1건당 +0.01~0.03s → 69변이 ≈ +1~2s).
  **determinism 을 비용 때문에 포기할 이유는 없다.**

### 2-3. ★ 변이 대상이 「회귀 자신」인 경우

`mut9.py` 는 `test/test_c4_sec_edgar.py` 를 변이한다.
이 경우 stale 위험은 collector 가 아니라 **test 모듈 쪽**에 있다.
D-1 이 `collectors/__pycache__` 만 지우면 이 경로는 **덮이지 않는다.**

⇒ determinism 수단은 **대상 파일이 무엇이냐와 무관하게** 성립해야 한다.
  D-2 · D-4 는 자동으로 성립하고, D-1 은 **삭제 대상 목록을 사람이 유지**해야 한다.
  이것이 D-1 의 실질적 약점이다. ⛔ 어느 쪽을 택할지는 적지 않는다.

---

## 3. 영역 2 — Per-mutation provenance

### 3-1. ★ 왜 `rc != 0` 이 불충분한지 — 이미 관측된 사실

조사(`c598226` §2)에서 stale 을 강제해 관측한 것:

```
소스 = V-5 변형 · 실행된 bytecode = V-4 변형

실패한 검사  T-11 (정적/AST — 소스를 직접 읽으므로 항상 최신)  ← V-4 잔재를 잡음
통과한 검사  T-10 (행동 — stale 모듈을 봄)                    ← V-5 는 실행조차 안 됨

rc != 0 이므로 harness 는 「V-5 잡힘」으로 기록했다. 사실이 아니다.
```

⇒ 「잡혔다」의 **이유가 이번 변이가 아닐 수 있다.** 그래서 기록해야 하는 것은
  `rc` 가 아니라 **어떤 검사가 실패했는가**이고, 나아가 **그 실패가 이번 변이에
  귀속되는가**이다.

### 3-2. 변이 1건이 남겨야 할 기록 (설계안)

CIO 가 지정한 6항에 실측에서 필요해진 항을 더한 것이다.

| 필드 | 무엇인가 | 왜 필요한가 |
|---|---|---|
| `mutation_id` | `MSFT-BH-S1` 처럼 **안정한 식별자** | 지금은 `S1`·`V-4` 같은 라벨이 harness 안에서만 유효하다 |
| `target_file` | 변이 대상 경로 | collector 인지 test 인지 구분 |
| `baseline_sha256` | 변이 전 소스 sha | 어느 정본 상태를 변이했는가 |
| `mutated_sha256` | 변이 후 소스 sha | 실제로 무엇이 디스크에 있었는가 |
| `anchor_count` | 치환 앵커 발견 수 (계약: 정확히 1) | 앵커 0 또는 2 는 「변이 미적용」이며 결과가 아니다 |
| `executed_witness` | **실행된 프로그램의 증거** (§6에서 방식 비교) | 디스크 sha 는 실행을 증명하지 못한다 (§6-2) |
| `baseline_result` | 무변형 상태 회귀 결과 | 기준이 이미 FAIL 이면 모든 변이가 「잡힘」으로 보인다 |
| `failed_checks[]` | 실패한 **검사 이름 전체** | `rc` 만으로는 §3-1 을 구분 못 한다 |
| `verdict` | KILLED / SURVIVED / NOT-APPLICABLE / INVALID-RUN | 아래 3-3 |

### 3-3. verdict 를 4값으로 나누는 이유

```
KILLED          앵커 1건 · witness 일치 · baseline PASS · failed_checks 비어있지 않음
SURVIVED        위 조건에서 failed_checks 가 비어 있음        ← 회귀 판별력 결함
NOT-APPLICABLE  앵커가 0건 (소스가 이후 커밋으로 바뀌어 변이가 더 이상 성립 안 함)
INVALID-RUN     witness 불일치 · baseline FAIL · 앵커 2건 이상
```

★ 지금 harness 는 이 넷을 **전부 「잡힘/놓침」 둘로 뭉갠다.**
  특히 `INVALID-RUN` 이 「잡힘」으로 집계되는 것이 §3-1 에서 실제로 일어난 일이다.
★ `NOT-APPLICABLE` 을 실패로 처리하면 안 된다 — 그것은 **변이 목록을 갱신하라는 신호**다.

### 3-4. ⛔ 아직 정하지 않는 것 — `expected_killers`

「이 변이는 **이 검사가** 잡아야 한다」를 변이마다 미리 선언하고,
다른 검사가 잡으면 `MISATTRIBUTED` 로 판정하는 방식이 가능하다.

| | 장점 | 대가 |
|---|---|---|
| 선언한다 | §3-1 의 오귀속을 **구조적으로** 막는다 | 변이 69종 × 기대 검사 목록을 유지해야 한다. 회귀 이름이 바뀌면 전부 깨진다 |
| 선언 안 하고 `failed_checks` 만 기록 | 유지비 0 | 오귀속은 **사람이 기록을 읽어야** 발견된다 |

⛔ 어느 쪽이 낫다고 적지 않는다. **CIO 판정 항목으로 올린다** (§8-c).

---

## 4. 영역 3 — Harness provenance

### 4-1. 현재 상태

```
저장소 안 mutation harness   0개
/tmp 안                      9개 · 변이 69종
run_all.py 승인 목록          미포함
Actions PASS 조건             미포함
세션 종료 시                  소멸
```

⇒ 「변이 69종 놓침 0건」이라는 보고의 **근거가 정본에 존재하지 않는다.**
  회귀와 fixture 는 sha256 까지 고정해 두고, **그 회귀의 판별력을 증명하는 장치만
  아무 통제도 받지 않는다.**

### 4-2. 저장소 배치 — 선택지

| | 배치 | 성격 | 걸리는 문제 |
|---|---|---|---|
| **P-1** | `mutations/` 최상위 (runner + 변이 catalog + 결과) | mutation infra 를 **회귀와 분리된 3번째 층**으로 명시 | 최상위 디렉터리 신설. `run_all.py` 의 파일-단위 계약과의 관계를 정해야 함 |
| **P-2** | `test/mutations/` | 기존 test 트리 안 | `run_all.py` 의 `APPROVED_TESTS` 가 `test/*.py` 를 파일 단위로 관리한다 — 변이 catalog 가 test 로 오인될 수 있다 |
| **P-3** | 각 회귀 파일 안에 변이를 내장 | 대상과 변이가 한 곳 | 회귀 자신을 변이하는 `mut9` 계열이 **자기참조**가 된다 |

★ 어느 배치든 **변이 catalog 는 데이터, runner 는 코드**로 분리하는 형태가 가능하다
  (`catalog/msft_azure_cc.json` 에 `{id, anchor_old, anchor_new, note}`).
  그러면 CIO 가 **변이 목록만 따로 review** 할 수 있다.
⛔ 배치는 CIO 판정 항목이다 (§8-a).

### 4-3. ★★ 저장소 오염 위험 — 지금 방식의 실질적 결함

현재 harness 는 **저장소의 소스 파일을 직접 덮어쓰고** 끝에 복원한다.

```python
open(SRC, "w").write(orig.replace(old, new, 1))   # ← 정본 파일을 변이한다
...
open(SRC, "w").write(orig)                        # ← 마지막에 복원
```

- 중간에 죽으면 **저장소가 변이된 상태로 남는다.**
- 프로젝트 계약상 authoritative rebuild 는 **clean worktree** 를 요구한다.
  즉 이 harness 는 **Actions PASS 조건과 구조적으로 충돌하는 방식**이다.

| | 변이 수행 위치 | 저장소 오염 |
|---|---|---|
| **W-1** | 현재 — 작업 트리 직접 수정 후 복원 | 중단 시 오염. 복원 성공 여부를 harness 가 스스로 보고할 뿐 |
| **W-2** | 저장소 사본(임시 디렉터리)에서 변이 | 오염 없음 (D-4 와 같은 조치가 된다) |
| **W-3** | `ATLAS_DISPOSABLE_CHECKOUT` 규율 재사용 | 기존 규율과 일관 |

★ **W-2/W-3 를 택하면 §2 의 determinism 이 부수적으로 함께 닫힌다** — 사본마다
  캐시가 새로 생기기 때문이다. 두 문제를 하나로 닫을 수 있는 유일한 지점이다.
⛔ 그래도 「그러니 W-2 로 하자」고 적지 않는다. 판정 항목이다 (§8-b).

### 4-4. CI 편입 경계 — 선택지와 실측 비용

실측 (변이 없는 1회 실행):

```
test_msft_azure_cc.py       0.29s
test_c4_sec_edgar.py        0.21s
test_capture_azure_fixture  0.06s
test_capture_tsmc_fixture   0.08s
run_all.py (inspection)     10.18s   (50 PASS / 0 FAIL + mode guard FAIL 1 — 정상)
```

69 변이를 대상 회귀에 매핑하면 순수 실행분 **≈ 16s**, determinism 조치 포함 **≈ 18~20s**.
⇒ `run_all.py` 에 통합하면 전체가 **약 2~3배**가 된다.

| | 편입 방식 | 성격 |
|---|---|---|
| **C-1** | `run_all.py` 승인 목록에 편입 — 매 회귀마다 실행 | 판별력이 **상시 계약**이 된다. 실행 시간 2~3배 |
| **C-2** | 별도 진입점(`--mutations`) + **Actions PASS 조건 ⑤ 신설** | 조건 ④(FI suite) 와 나란한 층. 현재 4-AND 구조를 5-AND 로 바꾸는 **계약 변경**이다 |
| **C-3** | 정본에 두되 실행은 수동, **결과 파일만 커밋** | 비용 0. 그러나 결과가 **최신이라는 보장이 없다** — 지금 fixture MANIFEST 가 소비처 0 인 것과 같은 모양이 된다 |

⛔ 판정 항목이다 (§8-d). 특히 **C-2 는 Actions PASS 정의 변경**이므로 CIO 판정 없이는
  설계상으로도 전제하지 않는다.

---

## 5. 영역 4 — Historical revalidation

### 5-1. ★ 무엇을 재확증하지 **않는가** 를 먼저 고정한다

```
⛔ 과거 특정 실행의 결과를 복원하지 않는다.
   과거 실행은 race 였고, 표본 1회였다. 복원 가능한 대상이 아니다.
⛔ 「그때도 0 놓침이었다」를 사후에 증명하려 하지 않는다.
```

⇒ 정본에 남은 과거 주장은 **QUALIFIED 로 유지**하고,
  새 deterministic run 이 **그것을 대체하는 새 정본 증거**가 된다.
  (CIO 가 `ede4ad5` 문장을 정본 의미상 철회한 것과 같은 처리다.)

### 5-2. 새 run 이 재확증하는 것 — 변이 1건 단위로 정의한다

```
① 적용 가능성   앵커가 현재 정본에서 정확히 1건 존재하는가
                → 0건이면 NOT-APPLICABLE (실패가 아니다. 변이를 다시 쓸 신호다)
② 실행 사실     변이된 프로그램이 실제로 실행됐는가 (§6 의 witness)
③ 판별          회귀가 실패했는가
④ 귀속          실패한 검사가 이번 변이로 설명되는가 (failed_checks 기록 · §3-4는 미정)
```

★ ①이 새로 필요한 이유: 69종은 여러 커밋에 걸쳐 만들어졌고 그 사이에 소스가 바뀌었다
  (`36aa11b` · `ede4ad5` · `24838c5` 등). **일부 앵커는 지금 성립하지 않을 것이 거의 확실하다.**
  그것을 「놓침」이나 「실패」로 집계하면 결과가 즉시 무의미해진다.

### 5-3. 이관 후 나올 수 있는 결과와 그때 하는 일

| 결과 | 의미 | 이번 Gate 에서 하는 일 |
|---|---|---|
| KILLED | 판별력 확인 | 기록 |
| **SURVIVED** | **회귀 판별력 결함** | ⛔ **이번 Gate 에서 고치지 않는다.** 별건 등록 |
| NOT-APPLICABLE | 변이가 낡았다 | 기록. 재작성 여부는 별도 판정 |
| INVALID-RUN | harness 결함 | harness 를 고친다 (이번 범위 안) |

★ **SURVIVED 가 나오는 것은 이 Gate 의 실패가 아니라 성과다.** 지금까지는
  그런 것이 있어도 보이지 않았다. ⛔ 그러나 그것을 고치는 것은 이번 범위가 아니다.

### 5-4. 산출물

새 run 은 **기계가 읽는 결과 파일 1개 + 사람이 읽는 요약 1개**를 낸다.
결과 파일에는 §3-2 의 필드가 변이마다 들어간다.
⛔ 그 파일을 정본에 커밋할지, 커밋한다면 Actions 가 대조할지는 §8-d 와 묶인 판정 항목이다.

---

## 6. ★★ 「변이된 프로그램을 검증했다」를 가장 단순·독립적으로 증명하는 방법

CIO 가 명시적으로 **비교만** 요구한 부분이다. 실측을 붙여 성질만 적는다.

### 6-1. 후보

| | 방법 | 증명하는 것 | 독립성 | 단순성 |
|---|---|---|---|---|
| **가** | 캐시 제거 / 격리 (§2 D-1~D-4) | 「stale 경로를 막았다」 | 높음 | 높음 |
| **나** | **canary 변이** — 반드시 잡혀야 하는 변이를 run 마다 섞고, 안 잡히면 run 전체 무효 | 「이 run 에서 변이 적용→실행→판별 경로가 살아 있다」 | 높음 — 대상 코드에 대한 지식 불필요 | 높음 |
| **다** | 회귀가 **로드된 모듈의 파일을 읽어** sha 출력 | ⛔ **아무것도 증명하지 못한다** (6-2) | — | — |
| **라** | 회귀가 **실행 중인 code object** 를 재귀 정규화해 fingerprint 출력 | 「그 bytecode 가 실행됐다」에 가장 근접 | 중간 | **낮음 — §0 참조** |
| **마** | 변이를 **관측 가능한 부작용**으로 설계 (변이가 특정 문자열을 찍게) | 직접적 | 낮음 — 변이마다 맞춤 | 낮음 |

### 6-2. ★★ 「다」는 단순히 약한 게 아니라 **틀렸다** — 실측

실제 `collectors/c4_sec_edgar_check.py` 사본으로 측정했다.

```
1 기준                    exec_fp=cf66c9f8c8baea14   file_sha=6afc1a8192af7dcd
2 변이(크기 동일+같은 초)   exec_fp=cf66c9f8c8baea14   file_sha=c25da72ef3cb5ec6   ← ★
3 캐시 제거 후             exec_fp=c9e39b1684490f70   file_sha=c25da72ef3cb5ec6
```

**2행이 결정적이다.** 바로 그 stale 상황에서
- **파일 읽기 sha 는 변이된 값을 보고한다** (`c25da…`) → 「변이 소스가 맞다」고 **거짓 확인**
- 실행 fingerprint 는 **옛 값 그대로다** (`cf66…`) → 실제로는 옛 bytecode 가 돌았다

⇒ **파일 읽기 기반 witness 는 우리가 막으려는 바로 그 실패 모드에서 거짓 통과한다.**
  「간접적이다」가 아니라 **그 용도로는 쓸 수 없다.**

### 6-3. 「라」의 실제 비용 — §0 의 정정이 여기에 걸린다

```
naive  sha256(co_code + repr(co_consts))   → 로드 경로에 따라 값이 바뀐다 (a5c7… vs c701…)
naive  sha256(marshal.dumps(code))         → 로드 경로에 따라 값이 바뀐다 (825b… vs aef3…)
재귀 정규화 (중첩 code object 를 재귀 처리) → 안정 (cf66… ×3), 변이도 검출 (c9e3…)
```

⇒ 「라」는 **올바르게 구현하면 작동하지만, 올바른 구현이 자명하지 않다.**
  내가 한 번 틀렸고, 그 틀린 버전은 **stale 을 못 잡는 게 아니라 값이 흔들려서
  정상 run 을 무효로 만든다** — fail-closed 장치가 정상 입력을 막는 쪽으로 고장나는,
  이 프로젝트에서 이미 두 번 겪은 계열이다 (`RE_NUM` 연도 오탐 · 과도한 AST 검사).

### 6-4. 세 접근의 성질 차이 (⛔ 우열을 적지 않는다)

```
가   원인 제거    — 「그런 일이 일어날 수 없게」 한다. 일어났는지는 모른다.
나   체계 검증    — run 단위로 「경로가 살아 있다」를 증명한다. 개별 변이는 미증명.
라   개별 증명    — 변이 단위로 증명한다. 구현 난이도가 가장 높다.
```

★ 셋은 **대체재가 아니라 서로 다른 범위**를 덮는다.
★ 「나」는 대상 코드에 대한 지식이 필요 없고 실패 시 **run 전체를 무효화**하므로,
  세 후보 중 **판정 규칙이 가장 단순**하다. ⛔ 그래서 채택하라는 뜻은 아니다 —
  개별 변이가 실행됐는지는 여전히 증명하지 못한다는 것을 같이 적어 둔다.

---

## 7. 이 설계가 닫지 **못하는** 것 — 미리 적는다

1. **회귀 자신의 판별력**은 mutation 으로만 측정된다. mutation 이 없는 영역은
   여전히 미측정이다 — `run_all.py` 의 승인 테스트 **19개 중 mutation 이 닿은 것은
   3개뿐**이다 (`test_msft_azure_cc` · `test_c4_sec_edgar` · `test_capture_azure_fixture`).
   `test_capture_tsmc_fixture` 를 포함한 **나머지 16개는 판별력이 한 번도 측정된 적이 없다.**
   ⛔ 이 설계는 그 범위를 넓히지 않는다 — 넓힐지는 별도 판정이다.
2. **`SURVIVED` 를 고치는 것**은 이번 범위가 아니다.
3. **FI-3 frozen input tamper KNOWN GAP** 은 이 설계와 무관하며 계속 열려 있다.
4. 변이 catalog 자체의 정확성 — 「앵커가 의도한 의미를 바꾸는가」는 여전히
   **사람이 review** 해야 한다. 자동화 대상이 아니다.
5. 본 설계는 **Python 모듈 import 경로**만 다룬다. subprocess 로 띄우는 다른 언어·
   외부 도구는 대상이 아니다 (현재 해당 없음).

---

## 8. ★ CIO 판정이 필요한 항목 — 선택지만 제시한다

⛔ 나는 어느 것도 고르지 않았다.

```
a. 저장소 배치            P-1 mutations/ 최상위 · P-2 test/mutations/ · P-3 회귀 내장   (§4-2)
b. 변이 수행 위치          W-1 작업 트리 직접 · W-2 저장소 사본 · W-3 disposable checkout (§4-3)
   └ W-2/W-3 는 §2 determinism 을 동시에 닫는다 — 다만 그 이유로 미리 정하지 않았다
c. 오귀속 방지 수준        expected_killers 선언 · failed_checks 기록만              (§3-4)
d. CI 편입 경계            C-1 run_all 편입 · C-2 Actions 조건 ⑤ 신설 · C-3 수동+결과 커밋 (§4-4)
   └ C-2 는 Actions PASS 정의 변경이다
e. 실행 증명 방식          가(원인 제거) 단독 · +나(canary) · +라(재귀 fingerprint) · 조합 (§6)
   └ 다(파일 읽기 sha)는 실측상 **틀렸다**. 후보에서 제외해야 한다
f. determinism 수단        D-1 캐시 삭제 · D-2 PYCACHEPREFIX · D-3 checked-hash · D-4 사본 (§2-2)
   └ `-B` / PYTHONDONTWRITEBYTECODE 는 **효과 없음이 실측됨** — 목록에서 제외
```

---

## 9. 이번 작업에서 하지 않은 것

- 저장소 코드 변경 **0줄** (`git status` clean · HEAD `c598226`)
- harness 이관 미착수 · 변이 catalog 미작성 · runner 미작성
- 기존 69 변이 재실행 **하지 않음** (새 harness 없이 돌리면 §5-1 을 위반한다)
- C4/MSFT production · fixture · Rule 상태 · capture metadata · 공용 helper **무변경**
- 실측은 전부 저장소 **밖**에서 했다 (`/tmp/mxv` · `/tmp/mxc` — 저장소 사본)

## 10. 상태

```
C4 parser/selection/build_header      CLOSED
TSMC fixture coverage cardinality     CLOSED @ 98bd6a7
P3 · RULE-0003/0007/0008              CLOSED · READY 유지

mutation harness hardening            OPEN — 설계 제출 · 구현 미승인 · 판정 대기 (§8 a~f)
capture unit metadata                 OBSERVABILITY DEBT · HOLD
fixture filename selector             CLOSED @ 98bd6a7
공용 helper inventory                 후순위
FI-3 frozen input tamper              KNOWN GAP · NOT GATED
```

```
UNDEFINED 3 · MISSING 18 · SOURCE_UNRESOLVED 12 · BLOCKED 18 · READY 7
consumable_by_evaluator = false · Production HOLD
```
