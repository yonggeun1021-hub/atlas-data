#!/usr/bin/env python3
"""RULE-0022 observation workflow 입력 계약 회귀 (H1 · CIO 확정 2026-08-16).

★ 이 회귀가 증명하는 것
   ① `source` 는 명시 선택 없이는 관측에 도달하지 못한다 — 누락 · 빈 값 ·
      sentinel · 예상 밖 값 **네 경로 모두** fail-closed 다
   ② 값 보정이 없다 — "Live" · " live" · "LIVE" 를 관대하게 받지 않는다
   ③ `capture_max_filings` 는 source=live 에서 양의 정수일 때만 통과한다
   ④ 요청 parameter 와 산출물이 어긋나면 멈춘다 (`RUN_PARAMETER_NOT_APPLIED`)
   ⑤ 2026-08-16 실제 실패 산출물 형태를 넣으면 ④ 가 실제로 잡아낸다

★ 이 회귀가 증명하지 못하는 것
   GitHub UI 가 default 를 어떻게 렌더링하는지 · Actions runner 의 실제 동작.
   그래서 계약을 **UI 동작에 의존하지 않는 배치**로 짰고, 여기서는 그 배치가
   런타임에서 fail-closed 인지를 실제 shell/python 으로 실행해 확인한다.

⛔ 네트워크를 쓰지 않는다 · ⛔ workflow 를 dispatch 하지 않는다.
   workflow 파일에 적힌 **바로 그 스크립트 바이트**를 꺼내 로컬에서 실행할 뿐이다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import checkkit as K                                                # noqa: E402
K.init(quiet=True)
check, need, section = K.check, K.need, K.section

WF_PATH = os.path.join(ROOT, ".github", "workflows", "rule0022-observation.yml")
SENTINEL = "__SELECT_SOURCE_EXPLICITLY__"

with open(WF_PATH, encoding="utf-8") as fh:
    WF = yaml.safe_load(fh)

# ★ PyYAML 은 `on:` 을 boolean True 로 파싱한다 (YAML 1.1). 문자열 검색이 아니라
#   구조로 접근하되, 두 표기 모두 받아들인다.
TRIGGERS = WF.get("on", WF.get(True))
STEPS = WF["jobs"]["observe-and-persist"]["steps"]
STEP_NAMES = [s.get("name") or s.get("uses") for s in STEPS]


def step(name):
    for s in STEPS:
        if s.get("name") == name:
            return s
    return None


def run_input_contract(source, capture=""):
    """workflow 에 적힌 `input contract` 스크립트를 그대로 실행한다."""
    sh = step("input contract (fail-closed)")["run"]
    env = dict(os.environ, SOURCE=source, CAPTURE_MAX_FILINGS=capture)
    p = subprocess.run(["bash", "-c", sh], env=env,
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def run_application_guard(source, capture, emission):
    """workflow 에 적힌 `parameter application guard` 스크립트를 그대로 실행한다."""
    sh = step("parameter application guard")["run"]
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "emission.json"), "w", encoding="utf-8") as fh:
            json.dump(emission, fh)
        env = dict(os.environ, EMIT_DIR=d, SOURCE=source,
                   CAPTURE_MAX_FILINGS=capture)
        p = subprocess.run(["bash", "-c", sh], env=env,
                           capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


# ══════════════════════════════════════════════════════════════════════
with section("A. trigger 계약 — 자동 발동이 없다"):
    need("workflow_dispatch 트리거가 있다", "workflow_dispatch" in (TRIGGERS or {}))
    check("⛔ schedule 트리거가 없다 — 자동 발동하지 않는다",
          "schedule" not in (TRIGGERS or {}), str(sorted(TRIGGERS or {})))
    check("⛔ push / pull_request 로 발동하지 않는다",
          not ({"push", "pull_request"} & set(TRIGGERS or {})))
    check("permissions 가 contents: read 로 최소화돼 있다",
          WF.get("permissions", {}).get("contents") == "read",
          str(WF.get("permissions")))

with section("B. ★★ H1 — source 입력 선언이 fail-closed 배치인가"):
    src = TRIGGERS["workflow_dispatch"]["inputs"]["source"]
    check("source 는 choice 다", src.get("type") == "choice", str(src.get("type")))
    check("★★ required: true — API 가 생략하면 GitHub 이 거부한다",
          src.get("required") is True, str(src.get("required")))
    opts = src.get("options") or []
    check("★★ 첫 option 이 유효하지 않은 sentinel 이다",
          opts and opts[0] == SENTINEL, str(opts))
    check("★★ sentinel 은 관측 가능한 source 가 아니다",
          SENTINEL not in ("fixture", "live"))
    check("★★ default 가 fixture 가 아니다 — 조용히 연습 데이터로 내려가지 않는다",
          src.get("default") != "fixture", str(src.get("default")))
    check("★ default 는 sentinel 이다", src.get("default") == SENTINEL,
          str(src.get("default")))
    check("★ 관측 가능한 값은 fixture · live 둘뿐이다",
          sorted(o for o in opts if o != SENTINEL) == ["fixture", "live"], str(opts))

with section("B-2. capture_max_filings 선언"):
    cmf = TRIGGERS["workflow_dispatch"]["inputs"]["capture_max_filings"]
    check("★ number 가 아니라 string 이다 — 생략과 명시적 0 을 구별하기 위해서다",
          cmf.get("type") == "string", str(cmf.get("type")))
    check("★ default 가 빈 문자열이다 — 임의 기본값이 없다",
          cmf.get("default") == "", repr(cmf.get("default")))
    check("★ source=fixture 에서도 강제되지 않도록 required 가 아니다",
          cmf.get("required") is False, str(cmf.get("required")))

with section("C. 단계 순서 — 입력 판정이 관측보다 먼저다"):
    for name in ("input contract (fail-closed)", "observe",
                 "repository-clean guard", "parameter application guard",
                 "emitted record validation", "persist",
                 "resulting store validation",
                 "repository-clean guard (post-persist)", "artifact upload"):
        check(f"단계 `{name}` 가 있다", name in STEP_NAMES, str(STEP_NAMES))
    check("★★ input contract 가 observe 보다 앞이다",
          STEP_NAMES.index("input contract (fail-closed)") < STEP_NAMES.index("observe"))
    check("★ clean guard 가 observe 직후다",
          STEP_NAMES.index("repository-clean guard") == STEP_NAMES.index("observe") + 1)
    check("★ parameter application guard 가 persist 보다 앞이다",
          STEP_NAMES.index("parameter application guard") < STEP_NAMES.index("persist"))
    check("★ artifact upload 는 항상 실행된다 (실패 run 도 증거를 남긴다)",
          step("artifact upload").get("if") == "always()")

with section("D. ★★ input contract 런타임 — source 거부 경로"):
    for bad, why in [("", "빈 값"),
                     (SENTINEL, "sentinel 그대로 실행"),
                     ("Live", "대문자 혼용"),
                     ("LIVE", "전부 대문자"),
                     (" live", "앞 공백"),
                     ("live ", "뒤 공백"),
                     ("fixtures", "오타"),
                     ("prod", "예상하지 않은 값"),
                     ("true", "boolean 유입")]:
        rc, out = run_input_contract(bad, "4")
        check(f"★★ source={bad!r} ({why}) → non-zero", rc != 0, f"rc={rc}")
    rc, out = run_input_contract("", "4")
    check("★ 빈 값 거부 사유가 로그에 남는다", "비어 있다" in out, out[:200])
    rc, out = run_input_contract(SENTINEL, "4")
    check("★ 계약 밖 값 거부 사유가 로그에 남는다", "계약 밖" in out, out[:200])
    check("★ 거부 로그가 실제 전달값을 그대로 보여준다", SENTINEL in out)

with section("D-2. ★★ input contract 런타임 — 통과 경로"):
    rc, out = run_input_contract("fixture", "")
    check("source=fixture · limit 없음 → 통과", rc == 0, f"rc={rc} {out[:200]}")
    rc, out = run_input_contract("live", "4")
    check("source=live · limit=4 → 통과", rc == 0, f"rc={rc} {out[:200]}")
    check("★ 통과 로그에 적용될 limit 이 찍힌다", "capture_max_filings=4" in out, out[:200])
    # ★★ Q5 (CIO 판정 2026-08-16) — 모순된 입력은 처음부터 닫는다.
    #    이전 설계는 「로그에 남기고 통과」였다. 그것은 적용되지 않는 입력을 받아
    #    성공으로 끝내는 형태이고, 8/16 무효 run 이 바로 그 형태였다.
    for bad in ("4", "1", "0", "abc"):
        rc, out = run_input_contract("fixture", bad)
        check(f"★★ Q5 · source=fixture + capture_max_filings={bad!r} → non-zero",
              rc != 0, f"rc={rc}")
    rc, out = run_input_contract("fixture", "4")
    check("★ 거부 사유에 모순된 입력임이 적힌다", "모순된 입력" in out, out[:300])
    check("★ 해결 방법이 함께 적힌다", "비워라" in out, out[:300])
    rc, out = run_input_contract("fixture", "")
    check("★ 반면 비워 두면 정상 통과한다", rc == 0, f"rc={rc}")

with section("D-3. ★★ capture_max_filings 검증 (source=live)"):
    for bad, why in [("", "누락"), ("0", "0 은 양의 정수가 아니다"),
                     ("-1", "음수"), ("4.5", "실수"), ("abc", "문자열"),
                     ("4 ", "뒤 공백"), (" 4", "앞 공백"), ("1e3", "지수 표기")]:
        rc, out = run_input_contract("live", bad)
        check(f"★ live · capture_max_filings={bad!r} ({why}) → non-zero",
              rc != 0, f"rc={rc}")
    for ok in ("1", "4", "8", "25"):
        rc, out = run_input_contract("live", ok)
        check(f"  live · capture_max_filings={ok!r} → 통과", rc == 0, f"rc={rc}")

# ══════════════════════════════════════════════════════════════════════
# E. ★★ parameter application guard — 요청과 산출물이 어긋나면 멈춘다
# ══════════════════════════════════════════════════════════════════════
LIVE_OK = {"schema_version": "observation_emission/1", "source": "live",
           "meta": {"limit": 4, "considered": 4, "dropped_by_limit": 21},
           "records": [], "failures": []}
FIXTURE_OK = {"schema_version": "observation_emission/1", "source": "fixture",
              "meta": {"manifest": "azure_cc_MANIFEST.json", "attempted": 4},
              "records": [], "failures": []}

with section("E. parameter application guard — 통과 경로"):
    rc, out = run_application_guard("live", "4", LIVE_OK)
    check("live 요청 · live 산출물 · limit 일치 → 통과", rc == 0, f"rc={rc} {out[:200]}")
    rc, out = run_application_guard("fixture", "", FIXTURE_OK)
    check("fixture 요청 · fixture 산출물 → 통과", rc == 0, f"rc={rc} {out[:200]}")

with section("E-2. ★★ 2026-08-16 실제 실패 재현 — 이 가드가 있었다면 멈췄다"):
    # ★ 실제 무효화된 run 의 산출물 형태다: source=live 를 요청했는데
    #   emission 은 fixture 였고 meta 에 manifest 가 실려 있었다.
    rc, out = run_application_guard("live", "4", FIXTURE_OK)
    check("★★ live 요청인데 fixture 산출물 → non-zero", rc != 0, f"rc={rc}")
    check("★ source 불일치가 사유로 적힌다", "source" in out, out[:300])
    check("★ manifest 유입도 사유로 적힌다", "manifest" in out, out[:300])

with section("E-3. parameter application guard — 그 밖의 어긋남"):
    bad_limit = json.loads(json.dumps(LIVE_OK))
    bad_limit["meta"]["limit"] = 2
    rc, _ = run_application_guard("live", "4", bad_limit)
    check("★ 요청 limit=4 인데 meta.limit=2 → non-zero", rc != 0, f"rc={rc}")

    no_limit = json.loads(json.dumps(LIVE_OK))
    no_limit["meta"].pop("limit")
    rc, _ = run_application_guard("live", "4", no_limit)
    check("★ live 인데 meta.limit 이 아예 없다 → non-zero", rc != 0, f"rc={rc}")

    live_for_fixture = json.loads(json.dumps(LIVE_OK))
    rc, _ = run_application_guard("fixture", "", live_for_fixture)
    check("★★ fixture 요청인데 live 산출물 → non-zero (반대 방향도 막는다)",
          rc != 0, f"rc={rc}")

    manifest_in_live = json.loads(json.dumps(LIVE_OK))
    manifest_in_live["meta"]["manifest"] = "azure_cc_MANIFEST.json"
    rc, _ = run_application_guard("live", "4", manifest_in_live)
    check("★ live 산출물에 manifest 가 섞이면 → non-zero", rc != 0, f"rc={rc}")

with section("F. observe 단계가 fixture fallback 을 갖지 않는다"):
    obs = step("observe")["run"]
    live_branch = obs.split('if [ "$SOURCE" = "live" ]; then', 1)[1].split("else", 1)[0]
    check("★★ live 분기는 --manifest 를 넘기지 않는다",
          "--manifest" not in live_branch, live_branch.strip()[:200])
    check("★ live 분기는 --limit 을 넘긴다", "--limit" in live_branch)
    check("★ live 분기는 --source live 로 호출한다", "--source live" in live_branch)

sys.exit(K.exit_code())
