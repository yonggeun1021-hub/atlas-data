# mutation harness 조사 (CIO 지시 2026-08-16)

⛔ **조사만이다. 코드 변경 0줄 · harness 수정 미착수 · 권고 없음.**

---

## 0. ★ 내 앞선 진단을 정정한다

커밋 `ede4ad5` 에서 원인을 **「stale bytecode」** 라고 적었다. 방향은 맞지만
**조건을 특정하지 않은 채 단정**했다. 검증 결과 실제 조건은 더 좁고, 성질은 더 나쁘다.

```
검증 1  캐시가 있어도 대부분 정상 무효화된다 (pyc 재생성 · 변이 잡힘)
검증 2  pyc 헤더 flags=0 → **timestamp 기반** 무효화
검증 3  pyc 는 source mtime 을 **초 단위 정수**로 저장한다
검증 4  V-4 와 V-5 는 변형 후 파일 크기가 **정확히 같다** (둘 다 44,040B)
검증 5  mtime 을 같은 초로 고정하면 pyc 가 **무효화되지 않는다** (재생성 안 됨)
검증 6  테스트 1회 실행 시간 ≈ 0.21s → 연속 변이가 **같은 초에 몰리기 쉽다**
```

⇒ 실제 조건: **연속한 두 변이의 파일 크기가 같고, 같은 wall-clock 초 안에 기록될 때**
   pyc 가 유효한 것으로 판정되어 **이전 변이의 bytecode 가 실행된다.**

## 1. ★★ 그래서 성질이 「버그」가 아니라 「race」다

- 같은 harness 를 두 번 돌려도 **결과가 다를 수 있다.** 초 경계에 걸리느냐의 문제다.
- 실제로 `mut7` 최초 실행에서 V-3·V-4·V-5·V-6 이 「놓침」으로 나왔고,
  캐시를 지운 뒤에는 V-5 가 「잡힘」으로 바뀌었다.
- ⇒ **한 번 다시 돌려서 0 놓침이 나온 것으로는 「그때도 0 놓침이었다」를 증명하지 못한다.**
  내가 앞서 「이전 6개 스위트를 재검증했고 결과가 유지됐다」고 보고한 것은
  **재현성 있는 증명이 아니라 한 번의 표본**이다. 그대로 정정한다.

## 2. ★ 정적 검사와 행동 검사의 비대칭 — 가장 위험한 부분

stale 상태를 강제해 관측했다.

```
source = V-5 변형 · 실행된 bytecode = V-4 변형

실패한 검사   T-11 (거부 근거에 후보 전체가 남는다)   ← V-4 의 잔재를 잡았다
통과한 검사   T-10 (final_candidates 가 2건 보존)    ← V-5 의 실제 변형은 실행조차 안 됨
```

⇒ **rc != 0 이 나와도 그 이유가 「이번 변이」가 아닐 수 있다.**
- **정적/AST 검사**는 소스 파일을 직접 읽으므로 **항상 최신**을 본다
- **행동 검사**만 stale 모듈을 본다

⇒ 「잡혔다」는 판정이 **엉뚱한 변이 때문**일 수 있고, 반대로 진짜 놓친 것이
  이전 변이의 잔재로 **가려질** 수도 있다. **양방향으로 틀린다.**

## 3. harness 전수 inventory

★ **모든 harness 가 `/tmp` 에 있고 저장소에 없다.**

| harness | 변이 대상 | 변이 | 실행 회귀 | 캐시 제거(현재) |
|---|---|---|---|---|
| `mut.py` | `collectors/msft_azure_cc.py` | 12 | `test_msft_azure_cc.py` | ✅ |
| `mut2.py` | `collectors/capture_azure_fixture.py` | 10 | `test_capture_azure_fixture.py` | ✅ |
| `mut3.py` | `collectors/msft_azure_cc.py` | 8 | `test_msft_azure_cc.py` | ✅ |
| `mut4.py` | `collectors/msft_azure_cc.py` | 8 | `test_msft_azure_cc.py` | ✅ |
| `mut5.py` | `collectors/msft_azure_cc.py` | 6 | `test_msft_azure_cc.py` | ✅ |
| `mut6.py` | `collectors/c4_sec_edgar_check.py` | 5 | `test_c4_sec_edgar.py` | ✅ |
| `mut7.py` | `collectors/c4_sec_edgar_check.py` | 6 | `test_c4_sec_edgar.py` | ✅ |
| `mut8.py` | `collectors/c4_sec_edgar_check.py` | 7 | `test_c4_sec_edgar.py` | ✅ |
| `mut9.py` | **`test/test_c4_sec_edgar.py`** (회귀 자신) | 7 | `test_c4_sec_edgar.py` | ✅ |

**합계 69 변이.** (캐시 제거는 오늘 사후 추가한 것이다 — 최초 실행 시에는 없었다.)

### ★ provenance 관점의 최대 결함 — harness 가 저장소 밖에 있다

```
저장소 내 mutation harness:  0개
```

- CIO 가 **검토할 수 없다.** 재실행할 수 없다. 세션이 끝나면 사라진다.
- 「변이 N종 놓침 0건」이라는 보고의 **근거 자체가 정본에 없다.**
- `run_all.py` 의 승인 테스트 목록에도 없어 Actions PASS 에 포함되지 않는다.
- ⇒ 회귀·fixture 는 sha256 까지 고정해 두고, **그 회귀의 판별력을 증명하는 장치는
  아무 통제도 받지 않는다.** 이번 조사에서 가장 큰 비대칭이다.

## 4. 실행 방식 · import/cache 경로

```
harness → subprocess.run([sys.executable, "test/test_*.py"])
회귀    → sys.path.insert(0, ROOT/collectors) ; import <collector>
Python  → collectors/__pycache__/<mod>.cpython-311.pyc
          헤더 flags=0 (timestamp) · source mtime(초) + size 로 유효성 판정
```

- 변이는 **소스 파일을 덮어쓰고** 하위 프로세스를 띄우는 방식이다.
- 따라서 유효성 판정이 mtime(초)+size 에만 의존한다.
- `sys.dont_write_bytecode` · `-B` · `PYTHONDONTWRITEBYTECODE` ·
  `PYTHONPYCACHEPREFIX` · hash 기반 pyc 는 **현재 아무것도 쓰이지 않는다.**

## 5. 「변이된 소스가 실제로 실행됐다」를 증명하는 방법 — 선택지만

⛔ 권고하지 않는다. 성질만 적는다.

| | 방법 | 증명하는 것 | 한계 |
|---|---|---|---|
| **가** | 캐시 제거 (`rmtree`) — **현재 방식** | pyc 재사용 없음 | 실행됐다는 **직접 증거는 아니다**. 경로를 하나 막을 뿐 |
| **나** | bytecode 생성 자체를 끈다 (`-B` / `PYTHONDONTWRITEBYTECODE=1`) | pyc 가 없으므로 소스가 곧 실행 대상 | 같은 성격. 증거가 아니라 예방 |
| **다** | pyc 를 hash 기반으로 (`checked-hash`) | mtime/size 와 무관하게 내용으로 무효화 | 도구 설정 필요. 여전히 간접 |
| **라** | **canary 변이** — 반드시 잡혀야 하는 변이를 매 run 에 섞는다 | canary 가 안 잡히면 **그 run 전체를 무효**로 판정 | 체계적 결함만 잡는다. 개별 변이의 실행은 여전히 미증명 |
| **마** | **실행 fingerprint** — 회귀가 로드된 모듈의 소스 sha256 을 출력하고 harness 가 변이 소스와 대조 | 「그 소스가 로드됐다」에 가장 근접 | 파일 읽기 기반이면 여전히 간접. code object 수준 확인은 변이마다 맞춤 필요 |
| **바** | harness 를 **저장소로 옮기고** 승인 목록·Actions 에 포함 | 결과가 **재현·검토 가능**해진다 | 실행 시간 증가. 승인 범위 결정 필요 |

★ 「가」와 「나」는 **원인 제거**이고, 「라」·「마」는 **증거 생성**이며,
  「바」는 **provenance 확보**다. 셋은 서로 다른 문제를 푼다.
★ 현재 적용된 것은 「가」 하나뿐이며, 그것도 **오늘 사후에** 넣었다.

## 6. 판정에 필요한 사실만

1. 원인은 stale 캐시가 맞지만 조건은 **「연속 변이의 크기 동일 + 같은 초」** 이고,
   성질은 **race** 다 — 재실행마다 결과가 달라질 수 있다.
2. 그래서 **한 번의 재검증은 과거 결과를 복원하지 못한다.** 내 앞선 보고를 정정한다.
3. 정적 검사는 항상 최신을 보고 행동 검사만 오염된다 — 「잡혔다」의 **이유**가
   틀릴 수 있다.
4. harness 9개(변이 69종)가 **전부 저장소 밖**에 있어 검토·재현·CI 편입이 불가능하다.
5. 원인 제거 / 증거 생성 / provenance 확보는 **서로 다른 조치**이며 현재는
   원인 제거 한 가지만, 그것도 사후에 적용돼 있다.

## 7. 상태

```
C4 parser/selection/build_header      CLOSED
TSMC fixture coverage cardinality     CLOSED @ 98bd6a7
P3 · RULE-0003/0007/0008              CLOSED · READY

capture unit metadata                 OBSERVABILITY DEBT · HOLD
mutation harness hardening            OPEN — 조사 완료 · 판정 대기
공용 helper inventory                 후순위
```
