"""회귀 공용 관측 helper (CIO 승인 2026-08-16 · 별건 4 수정 계약).

⛔ **test 전용이다.** production/collector 로 공용화하지 않는다.

이 파일이 지는 계약은 하나다.

> 한 계약의 실패 또는 그 계약을 준비하는 값의 부재가,
> **독립적으로 평가 가능한 후속 계약까지 죽이지 않는다.**

결과 어휘 네 가지 — 표준 test 의미론과 같게 **assertion failure 와 예상하지
않은 예외를 구분**한다.

```
PASS      계약 성립
FAIL      계약 위반 (assertion failure)
ERROR     계약을 평가하지 못했다 (예상하지 않은 예외)
SKIPPED   전제 미성립으로 평가하지 않았다
```

⛔ 금지 — 설계 승인문에 명시된 것을 코드에 그대로 남긴다.
  · blanket `except Exception: pass` 를 쓰지 않는다. 절 경계는 반드시 **ERROR 로
    기록**하고 exit code 를 1 로 만든다. 예외를 성공으로 바꾸지 않는다.
  · `ERROR` 와 `SKIPPED` 는 mutation 의 KILLED 근거가 **아니다.**
    KILLED 는 「expected_killer 가 **FAIL** 했다」 하나뿐이다 (runner 계약).
  · 특정 예외를 「기대 계약」으로 발명하지 않는다.
  · `skip()` 은 전제 미성립을 **기록**하는 것이지 감추는 것이 아니다.
    무변형 baseline 에서 `SKIPPED == 0` 이어야 한다 — 하나라도 있으면
    이 장치가 정상 경로를 가리고 있다는 뜻이다.

★ `atexit` 의 한계를 계약에 명시한다.
  등록된 handler 는 **정상적인 interpreter 종료**에서 실행된다 — 처리되지 않은
  예외로 죽는 경우도 여기 포함된다(traceback 뒤에 실행된다 · 실측 확인).
  ⛔ 그러나 **처리되지 않은 signal · Python 내부 fatal error · `os._exit()`**
     에서는 실행되지 않는다. 따라서 mutation runner 가 말하는 `CRASH` 는
     이 세 경우로 좁아진다 — 「요약도 trace 도 확보하지 못한 실행」.
"""
from __future__ import annotations

import atexit
import json
import os
import traceback

TRACE_ENV = "ATLAS_CHECK_TRACE"

PASS: list = []
FAIL: list = []
ERROR: list = []
SKIPPED: list = []

_TRACE: list = []
_STATE = {"quiet": False, "section": None, "summarized": False,
          "sections_entered": []}


def init(quiet: bool = False) -> None:
    """`quiet=True` 면 실패만 stdout 에 찍는다 — 기존 회귀별 출력 습관을 보존한다.
    ⛔ 정상 실행의 stdout 을 불필요하게 바꾸지 않기 위한 것이며, 기록(trace)은
       quiet 여부와 무관하게 항상 남는다."""
    _STATE["quiet"] = quiet


def _rec(kind: str, name: str, detail: str = "") -> None:
    _TRACE.append({"seq": len(_TRACE) + 1, "kind": kind, "name": name,
                   "section": _STATE["section"], "detail": str(detail)[:500]})


# ── 계약 판정 ────────────────────────────────────────────────────────
def check(name, cond, extra="") -> bool:
    """기존 세 회귀의 `check()` 와 호출 형태·출력이 같다."""
    cond = bool(cond)
    if cond:
        PASS.append(name)
        _rec("PASS", name)
        if not _STATE["quiet"]:
            print("  ✓ " + name)
    else:
        FAIL.append(name)
        _rec("FAIL", name, extra)
        print("  ✗ " + name + (f" — {extra}" if extra else ""))
    return cond


def need(name, cond, extra="") -> bool:
    """**전제** 계약. `check()` 와 똑같이 기록하고 성립 여부를 돌려준다.

    ⛔ 전제가 깨졌을 때 그냥 건너뛰면 검사 수만 조용히 줄어든다 —
       `98bd6a7` 에서 닫은 것과 같은 구조의 결함이다. 반드시 `skip()` 을 짝지어
       후속 계약의 이름과 미성립 사유를 남긴다."""
    return check(name, cond, extra)


def skip(name, reason) -> None:
    """전제 미성립으로 평가하지 않은 계약. **PASS 가 아니다.**"""
    SKIPPED.append(name)
    _rec("SKIPPED", name, reason)
    print("  ⊘ " + name + f" — 전제 미성립: {reason}")


def guard(cond, precondition, dependents, reason, extra="") -> bool:
    """전제를 판정하고, 미성립이면 **의존 계약을 이름째로** SKIPPED 에 남긴다.

    ⛔ `dependents` 를 비워 두고 조용히 건너뛰면 안 된다 — 그러면 검사 수만
       줄어드는, 우리가 이미 닫은 결함과 같은 모양이 된다."""
    if need(precondition, cond, extra):
        return True
    for d in dependents:
        skip(d, reason)
    return False


# ── 절 경계 ──────────────────────────────────────────────────────────
class section:
    """절(section) 경계. 절 안의 예상하지 않은 예외를 **ERROR 로 기록**하고
    다음 절로 진행한다.

    ⛔ 삼키지 않는다 — ERROR 는 결과로 남고 exit code 를 1 로 만든다.
    ⛔ `Exception` 만 잡는다. `SystemExit` · `KeyboardInterrupt` 는 통과시킨다.
    ★ `with` 는 스코프를 만들지 않으므로 절을 넘어 쓰이는 모듈 수준 이름
      (실측 msft 44 · c4 46개) 이 그대로 보존된다."""

    def __init__(self, title: str):
        self.title = title

    def __enter__(self):
        _STATE["section"] = self.title.strip()
        _STATE["sections_entered"].append(self.title.strip())
        print(self.title)
        return self

    def __exit__(self, et, ev, tb):
        if et is None:
            return False
        if not issubclass(et, Exception):
            return False                      # SystemExit 등은 통과시킨다
        where = ""
        for fr in traceback.extract_tb(tb):
            where = f"{os.path.basename(fr.filename)}:{fr.lineno}"
        detail = f"{et.__name__}: {ev} @ {where}"
        ERROR.append(self.title.strip())
        _rec("ERROR", self.title.strip(), detail)
        print(f"  ⚠ ERROR — 이 절을 평가하지 못했다: {detail}")
        return True                           # 다음 절로 진행한다


# ── 종료 ─────────────────────────────────────────────────────────────
def exit_code() -> int:
    return 1 if (FAIL or ERROR) else 0


@atexit.register
def _summary() -> None:
    if _STATE["summarized"]:
        return
    _STATE["summarized"] = True
    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL / "
          f"{len(ERROR)} ERROR / {len(SKIPPED)} SKIPPED")
    path = os.environ.get(TRACE_ENV)
    if not path:
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pass": len(PASS), "fail": len(FAIL), "error": len(ERROR),
                   "skipped": len(SKIPPED),
                   "sections_entered": _STATE["sections_entered"],
                   "last_section": _STATE["section"],
                   "trace": _TRACE}, f, ensure_ascii=False)
