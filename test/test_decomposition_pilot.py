"""B1 reviewed decomposition pilot — 회귀 테스트

CIO 가 이 pilot 에서 확인하겠다고 한 것은 셋이다.
  P-1  "한 셀 → 여러 객체" 분해가 안정적인가
  P-2  execution_reference 와 daily_eligibility 가 섞이지 않는가
  P-3  Undefined 를 억지로 executable Rule 로 만들지 않는가

각 항목을 **양성(실제 pilot 이 통과한다)** 과
**음성(위반을 주입하면 검증기가 잡는다)** 으로 나눠 검사한다.
양성만 있으면 "검증기가 아무것도 안 해도 통과"와 구별되지 않는다.
"""
from __future__ import annotations

import copy
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "rules"))
import validate_decomposition as V                            # noqa: E402
import vocabulary as VC                                       # noqa: E402

PASS = FAIL = 0
_FAILS: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        _FAILS.append(name)
        print(f"  FAIL  {name}" + (f"   — {detail}" if detail else ""))


def load():
    with open(os.path.join(_ROOT, "rules", "decompose_pilot.json"), encoding="utf-8") as f:
        pilot = json.load(f)
    with open(os.path.join(_ROOT, "_watchlist_rows.json"), encoding="utf-8") as f:
        rows = json.load(f)["results"]
    extra = {f"{r['티커']}::편입 사유": r["편입 사유"] for r in rows if r.get("편입 사유")}
    raws = V._raw_texts(os.path.join(_ROOT, "config", "rules.candidates.json"), extra)
    return pilot, raws


PILOT, RAWS = load()


def mutate(cid: str, split: int, **kw):
    """조각 하나를 바꿔 검증기가 잡는지 본다."""
    p = copy.deepcopy(PILOT)
    for c in p["cells"]:
        if c["candidate_id"] == cid:
            for fr in c["fragments"]:
                if fr["split_index"] == split:
                    fr.update(kw)
    return V.validate(p, RAWS)


def caught(rep, needle: str) -> bool:
    return any(needle in x for x in rep["violations"])


def IDX(cid: str, substr: str) -> int:
    """조각 번호를 원문으로 찾는다.

    ★ 하드코딩한 번호를 쓰면 결합 표기가 끼어들 때 테스트가 엉뚱한 조각을 가리킨다.
      (실제로 그 사고를 한 번 겪었다 — 효성 '연속' 주석 off-by-N)
    """
    for c in PILOT["cells"]:
        if c["candidate_id"] != cid:
            continue
        for f in c["fragments"]:
            if substr in f["raw_fragment"]:
                return f["split_index"]
    raise KeyError(f"{cid} 에 {substr!r} 조각이 없다")


def FID(cid: str, substr: str) -> str:
    return f"{cid}#{IDX(cid, substr)}"


# ── 기준선 ───────────────────────────────────────────────────────────────
def test_baseline() -> None:
    print("\n[P-0] 제출된 pilot 자체가 불변식을 지킨다")
    rep = V.validate(PILOT, RAWS)
    check("★★ 불변식 위반 0건", rep["valid"], str(rep["violations"]))
    check("★ 6칸 전부 검증됐다", len(rep["cells"]) == 6, str(len(rep["cells"])))


# ── P-1 : 한 셀 → 여러 객체 ──────────────────────────────────────────────
def test_p1_decomposition_stable() -> None:
    print("\n[P-1] '한 셀 → 여러 객체' 분해가 안정적인가")
    rep = V.validate(PILOT, RAWS)
    cells = {c["candidate_id"]: c for c in rep["cells"]}

    check("★ 1조각 셀도 같은 스키마로 표현된다 (MSFT::다음 이벤트)",
          cells["MSFT::다음 이벤트"]["fragments"] == 1)
    check("★ 8조각 셀도 같은 스키마로 표현된다 (TSM::다음 이벤트)",
          cells["TSM::다음 이벤트"]["fragments"] >= 8)
    check("★★ full 로 선언한 셀은 의미 있는 미분해 구간이 0이다",
          all(c["uncovered_meaningful"] == 0
              for c in rep["cells"] if c["scope"] == "full"))
    check("★★ partial 은 사유 없이 선언할 수 없다",
          all(next(x for x in PILOT["cells"] if x["candidate_id"] == c["candidate_id"])
              .get("partial_reason")
              for c in rep["cells"] if c["scope"] == "partial"))

    # 음성 — 조각을 하나 지우면 커버리지가 깨져야 한다
    p = copy.deepcopy(PILOT)
    for c in p["cells"]:
        if c["candidate_id"] == "298040.KS::탈락 조건":
            drop = IDX("298040.KS::탈락 조건", "기관 순매수 연속 끊김")
            c["fragments"] = [f for f in c["fragments"] if f["split_index"] != drop]
            for i, f in enumerate(c["fragments"], 1):
                f["split_index"] = i
    r = V.validate(p, RAWS)
    check("★★ [음성] 조각을 누락시키면 잡힌다", caught(r, "미분해 구간"), str(r["violations"]))

    # 음성 — 원문에 없는 조각을 넣으면 잡혀야 한다
    r = mutate("MSFT::핵심 지지", IDX("MSFT::핵심 지지", "451~457"), raw_fragment="451~999")
    check("★★ [음성] 원문에 없는 조각을 지어내면 잡힌다", caught(r, "원문에 없는 조각"))

    # 음성 — split_index 를 건너뛰면 잡혀야 한다
    p = copy.deepcopy(PILOT)
    for c in p["cells"]:
        if c["candidate_id"] == "TSM::기술적 무효화":
            c["fragments"][2]["split_index"] = 9
    r = V.validate(p, RAWS)
    check("★ [음성] split_index 불연속이 잡힌다", caught(r, "연속이 아니다"))


# ── P-2 : 두 층이 섞이지 않는가 ─────────────────────────────────────────
def test_p2_layer_separation() -> None:
    print("\n[P-2] execution_reference 와 daily_eligibility 가 섞이지 않는가")
    rep = V.validate(PILOT, RAWS)
    d = {x["id"]: x for x in rep["derived"]}

    check("★★ 핵심 지지는 execution_reference 로 남는다 (Rule 승격 없음)",
          d[FID("MSFT::핵심 지지", "451~457")]["downstream_effect"] == "execution_reference"
          and d[FID("MSFT::핵심 지지", "451~457")]["rule_kind"] == VC.UNRESOLVED)
    check("★★ 효과가 명시된 SMA20 조각만 daily_eligibility 다",
          d[FID("TSM::기술적 무효화", "SMA20 $409 아래면")]["downstream_effect"] == "daily_eligibility")
    check("★ 같은 $398 이 서로 다른 층의 두 객체를 만든다",
          d[FID("TSM::기술적 무효화", "$398 이탈")]["downstream_effect"] == "강등 검토"
          and d[FID("TSM::편입 사유", "D($398 이탈)→청산")]["downstream_effect"] == VC.UNRESOLVED)

    # ★ 음성 — CIO 가 가장 경계한 오류: 가격 레벨을 eligibility Rule 로 승격
    r = mutate("MSFT::핵심 지지", IDX("MSFT::핵심 지지", "451~457"),
               object_role="rule_candidate", rule_kind="ENT",
               downstream_effect="daily_eligibility")
    # ★★ CIO 검수 2026-08-15 ② — 종전 양성 대조군을 삭제했다.
    #   effect_evidence="SMA20" 으로 참조값을 ENT/daily_eligibility 로 승격시키는
    #   테스트였는데, SMA20 은 지표명이지 허가/차단 효과의 근거가 아니다.
    #   그 테스트는 이번에 막으려던 오류를 오히려 허용하고 있었다.
    #   → 이번 B1 에서 reference cell 의 Rule 승격은 0건으로 유지한다.
    check("★★ [음성] 참조값을 daily_eligibility 로 승격하면 잡힌다",
          caught(r, "Rule 승격은 허용하지 않는다"), str(r["violations"]))

    r = mutate("MSFT::핵심 지지", IDX("MSFT::핵심 지지", "451~457"), object_role="rule_candidate", rule_kind="ENT",
               downstream_effect="daily_eligibility", effect_evidence="SMA20")
    check("★★ [음성] effect_evidence 를 붙여도 승격은 여전히 막힌다",
          caught(r, "Rule 승격은 허용하지 않는다"), str(r["violations"]))

    r = mutate("MSFT::핵심 지지", IDX("MSFT::핵심 지지", "451~457"), rule_kind="ENT")
    check("★★ [음성] execution_reference 에 rule_kind 를 붙이면 잡힌다",
          caught(r, "Rule 로 승격했다"))

    r = mutate("MSFT::핵심 지지", IDX("MSFT::핵심 지지", "451~457"), downstream_effect="daily_eligibility")
    check("★ [음성] role 과 effect 가 어긋나면 잡힌다", caught(r, "effect 가"))

    r = mutate("TSM::기술적 무효화", IDX("TSM::기술적 무효화", "펌더멘털 무효화"), rule_kind="FAL")
    check("★ [음성] non_rule_evidence 에 rule_kind 를 붙이면 잡힌다",
          caught(r, "채워져 있다"))


# ── P-3 : Undefined 를 executable 로 만들지 않는가 ──────────────────────
def test_p3_undefined_fail_closed() -> None:
    print("\n[P-3] Undefined 를 억지로 executable Rule 로 만들지 않는가")
    rep = V.validate(PILOT, RAWS)
    d = {x["id"]: x for x in rep["derived"]}

    k = FID("298040.KS::탈락 조건", "기관 순매수 연속 끊김")   # 기관 순매수 '연속' — 원문이 Undefined 라고 명시
    check("★★ '연속' 조각은 UNDEFINED 로 남는다",
          d[k]["definition_status"] == "UNDEFINED")
    check("★★ 데이터가 있어도 BLOCKED 다 (정의가 없으므로)",
          d[k]["data_status"] == "AVAILABLE" and d[k]["evaluator_status"] == "BLOCKED")
    check("★★ 차단 원인이 DEFINITION_UNDEFINED 로 남는다",
          "DEFINITION_UNDEFINED" in d[k]["blocked_by"])
    check("★ 원문의 사용 금지 표식이 별도 객체로 보존된다",
          any("'연속'의 정의가 Undefined" in f["raw_fragment"]
              and f["object_role"] == "non_rule_evidence"
              for c in PILOT["cells"] if c["candidate_id"] == "298040.KS::탈락 조건"
              for f in c["fragments"]))

    # ★ 음성 — 정의를 현장에서 만들어 채우면 잡혀야 한다
    r = mutate("298040.KS::탈락 조건", IDX("298040.KS::탈락 조건", "기관 순매수 연속 끊김"),
               definition_status="DEFINED", threshold="연속 = 3거래일")
    check("★★ [음성] UNDEFINED 에 threshold 를 채우면 잡힌다",
          caught(r, "정의를 만들었다"), str(r["violations"]))

    r = mutate("298040.KS::탈락 조건", IDX("298040.KS::탈락 조건", "기관 순매수 연속 끊김"),
               object_role="execution_reference", downstream_effect="execution_reference")
    check("★★ [음성] UNDEFINED 를 execution_reference 로 우회시키면 잡힌다",
          caught(r, "우회시켰다"))

    # ★ 음성 — evaluator_status 를 직접 적으면 잡혀야 한다 (파생 강제)
    r = mutate("298040.KS::탈락 조건", IDX("298040.KS::탈락 조건", "기관 순매수 연속 끊김"), evaluator_status="READY")
    check("★★ [음성] evaluator_status 를 직접 적으면 잡힌다", caught(r, "파생값이다"))

    r = mutate("298040.KS::탈락 조건", IDX("298040.KS::탈락 조건", "기관 순매수 연속 끊김"), blocked_by=[])
    check("★★ [음성] blocked_by 를 직접 적으면 잡힌다", caught(r, "파생값이다"))

    # 파생식 자체
    check("★★ UNDEFINED 는 어떤 data_status 와 조합해도 READY 가 되지 않는다",
          all(VC.derive_evaluator_status("UNDEFINED", ds) != "READY"
              for ds in ("AVAILABLE", "MISSING", "UNDETERMINED")))
    check("★ READY 는 DEFINED × AVAILABLE 에서만 나온다",
          VC.derive_evaluator_status("DEFINED", "AVAILABLE") == "READY"
          and sum(VC.derive_evaluator_status(a, b) == "READY"
                  for a in ("DEFINED", "UNDEFINED", VC.UNRESOLVED)
                  for b in ("AVAILABLE", "MISSING", "UNDETERMINED", VC.UNRESOLVED)) == 1)


# ── 어휘 폐쇄 ────────────────────────────────────────────────────────────
def test_vocabulary_closed() -> None:
    print("\n[P-4] 문서에 없는 어휘를 만들지 않는다")
    r = mutate("298040.KS::탈락 조건", IDX("298040.KS::탈락 조건", "수주잔고"), definition_status="PARTIALLY_DEFINED")
    check("★★ [음성] 새 definition_status 토큰을 만들면 잡힌다", caught(r, "허용 어휘 밖"))
    r = mutate("298040.KS::탈락 조건", IDX("298040.KS::탈락 조건", "수주잔고"), downstream_effect="stage_demotion")
    check("★★ [음성] 새 downstream_effect 토큰을 만들면 잡힌다", caught(r, "허용 어휘 밖"))
    r = mutate("298040.KS::탈락 조건", IDX("298040.KS::탈락 조건", "수주잔고"), rule_kind="EXIT")
    check("★★ [음성] 새 rule_kind 토큰을 만들면 잡힌다", caught(r, "허용 어휘 밖"))
    check("★ pilot 이 올린 어휘 공백 4건이 CIO 판정으로 해소됐다",
          len(VC.VOCABULARY_GAPS) == 0 and len(VC.VOCABULARY_GAPS_RESOLVED) == 4,
          f"open={len(VC.VOCABULARY_GAPS)} resolved={len(VC.VOCABULARY_GAPS_RESOLVED)}")
    check("★★ '강등 검토'는 legacy/provisional 로 표시된다 (executable 금지)",
          "강등 검토" in V.LEGACY_PROVISIONAL_EFFECTS)


# ── 권한 경계 ────────────────────────────────────────────────────────────
def test_authority() -> None:
    print("\n[P-5] pilot 산출물도 authority 가 아니다")
    check("★★ authority = False", PILOT["authority"] is False)
    check("★★ consumable_by_evaluator = False", PILOT["consumable_by_evaluator"] is False)
    check("★ Rule Inventory 를 만드는 것이 아님이 명시된다",
          "Inventory 를 만드는 것이 아니라" in PILOT["purpose"])
    check("★ 열린 B1 질문이 어디서 걸렸는지 기록된다",
          len(PILOT["open_questions_hit"]) == 3)


SUITES = [test_baseline, test_p1_decomposition_stable, test_p2_layer_separation,
          test_p3_undefined_fail_closed, test_vocabulary_closed, test_authority]


def main() -> None:
    print("B1 reviewed decomposition pilot — 회귀 테스트")
    for fn in SUITES:
        try:
            fn()
        except Exception as e:                               # noqa: BLE001
            check(f"[{fn.__name__}] 그룹이 예외로 중단되지 않는다", False,
                  f"{type(e).__name__}: {e}")
    print(f"\n{'='*60}\n  {PASS} PASS / {FAIL} FAIL")
    for n in _FAILS:
        print(f"    ✗ {n}")
    print("="*60)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
