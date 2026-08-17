# mutation infrastructure

⛔ **이 층은 회귀(test/)가 아니다.** 회귀가 무엇을 증명하는지를 **회귀 자신에 대해**
   측정하는 validation-infrastructure 층이다. `run_all.py` 의 승인 테스트 목록에
   **포함되지 않으며**, Actions PASS 의 4-AND 정의를 **바꾸지 않는다** (CIO 판정 d).

```
mutations/
  runner.py            변이 적용 · 격리 실행 · verdict 판정 · provenance 기록
  catalog/             변이 목록 = **데이터**. runner 와 분리해 따로 review 한다
    msft_azure_cc.json           34
    capture_azure_fixture.json   10
    c4_sec_edgar_check.json      18
    test_c4_sec_edgar.json        7   ← 대상이 **회귀 자신**이다
                                 ── 69
```

## 실행

```bash
python3 mutations/runner.py                          # 전수
python3 mutations/runner.py --catalog c4_sec_edgar_check.json
python3 mutations/runner.py --id C4-CAND-V-2
python3 mutations/runner.py --json /tmp/run.json     # ⛔ 결과는 정본에 커밋하지 않는다
```

정본 worktree 에 **tracked 변경이 있으면 시작하지 않는다.** 일회용 checkout 을
HEAD 에서 만들기 때문에, 그렇지 않으면 「무엇을 변이했는가」를 설명할 수 없다.

## 격리 계약 (CIO 판정 b · e · f)

변이마다:

```
① git archive HEAD → 새 임시 디렉터리로 풀어 **일회용 checkout** 을 만든다
② checkout 에 __pycache__ 가 없음을 **검사**한다
③ 같은 checkout 에서 baseline 회귀 실행 → PASS 가 아니면 INVALID_RUN
④ __pycache__ 가 여전히 없음을 **검사**한다
⑤ 변이를 checkout 안 대상 파일에 적용 (anchor 수 == 선언 수)
⑥ witness — import 경로 · cwd · sha256 일관성 검사
⑦ 같은 checkout 에서 변이 회귀 실행 → __pycache__ 없음을 다시 **검사**
⑧ checkout 파기
```

★ **정본 worktree 는 한 번도 수정되지 않는다.** (이전 `/tmp/mut*.py` 는 정본 파일을
  직접 덮어쓰고 끝에 복원했다 — 중단되면 오염됐고 clean-worktree 계약과 충돌했다.)

★ **`-B` / `PYTHONDONTWRITEBYTECODE` 는 determinism 수단이 아니다.**
  pyc **쓰기**만 막고 **읽기**는 막지 않는다 (2026-08-16 실측 · Python 공식 의미와 일치).
  여기서는 일회용 tree 에 pyc 를 남기지 않는 **위생** 목적으로만 쓰고, 실제 계약은
  ②④⑦ 의 **검사**가 진다. 가정하지 않는다.

★ code-object fingerprint 는 **넣지 않았다** (CIO 판정 e). 격리 자체가
  execution-integrity 계약이다. 검증기를 다시 검증해야 하는 문제를 만들지 않는다.

## verdict (CIO 판정 c)

```
KILLED           expected_killers 중 ≥1 이 실패했다
SURVIVED         회귀가 전부 통과했다 — 판별력 결함
MISATTRIBUTED    회귀는 FAIL 했지만 expected_killers 는 모두 PASS
NOT_APPLICABLE   anchor 0건 — 현재 정본에서 성립하지 않는 변이 (실패가 아니다)
INVALID_RUN      baseline FAIL · anchor 수 불일치 · 격리/witness 위반
```

`rc != 0` 은 판정 근거가 **아니다.** 「실패했다」와 「의도한 회귀가 이 변이를 잡았다」는
다르다 — 실제로 stale bytecode 아래에서 다른 변이의 잔재가 실패를 만든 사례가 있었다.

### expected_killers

각 변이는 **깨뜨리기로 선언한 검사**를 최소 1개 갖는다. 회귀 출력의 실패 줄에 대한
**부분 문자열**로 대조한다. 선언을 고칠 때는 `declaration_history` 에 원 선언과 사유를
남긴다 — ⛔ 관측 결과를 사후에 베껴 넣어 KILLED 를 만들지 않는다.

### ★ 5분할이 덮지 않는 경우 — CRASH

회귀가 예외로 죽으면 expected_killers 는 **실행되지 않는다.** 「모두 PASS」가 아니므로
MISATTRIBUTED 의 정의에 정확히 맞지 않는다. 현재는 MISATTRIBUTED 로 두되
`mutated.outcome == "CRASH"` 와 사유를 함께 남긴다. **CIO 판정 대기 항목이다.**

## 실패 줄 해석의 한계

회귀의 `check()` 와 **collector 자신의 진단 출력**이 둘 다 `✗` 로 시작한다.
세 겹으로 막는다 — ① 정확히 두 칸 들여쓴 줄만 ② baseline 에 이미 있던 줄은 제외
③ 남은 개수를 회귀가 보고한 `N PASS / M FAIL` 의 M 과 대조. ③ 이 어긋나면
`parse_exact=false` 로 남기고 provenance 를 불완전으로 판정한다 — 숨기지 않는다.
⛔ 회귀 파일의 출력 형식을 고쳐서 해결하지 않는다 (범위 밖).

## exit code

**run 의 유효성**이다. `partition 합 == catalog total` ∧ `INVALID_RUN == 0` ∧
`provenance 완전` 이면 0.

⛔ **exit 0 은 「모든 변이가 잡혔다」는 뜻이 아니다.** SURVIVED · MISATTRIBUTED 는
   발견 사실이며 별건으로 올린다. ⛔ 이 runner 는 회귀를 고치지 않는다.

## 이 층이 증명하지 않는 것

- 회귀 프로세스가 로드한 code object 그 자체 (fingerprint 미도입)
- witness probe 와 회귀가 **같은 프로세스**라는 것 (같은 checkout · cwd · 인터프리터의
  별개 프로세스다)
- 변이 문면이 의도한 의미를 실제로 바꾸는지 — **사람이 review 한다**
- `run_all.py` 승인 테스트 19개 중 **3개**만 mutation 이 닿는다. 나머지 16개의
  판별력은 여전히 미측정이다.
