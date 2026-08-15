"""B1 46칸 전체 검증 실행 경로 + CIO 검수 3건에 대한 음성 회귀

pilot 만 돌리지 않고 `decompose_full.json` 46칸을 동일 validator 로 검증한다.
그리고 이번에 고친 세 지점이 **실제로 실패를 검출하는지** 를 음성으로 확인한다.
  ① builder occurrence 배치가 fail-closed 인가
  ② reference cell 의 Rule 승격이 0건으로 유지되는가
  ③ 결합 표기가 사라지면 I-1 이 잡는가
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
from build_full_decomposition import place_and_merge, N       # noqa: E402

PASS = FAIL = 0
_FAILS: list = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        _FAILS.append(name)
        print(f"  FAIL  {name}" + (f"   — {detail}" if detail else ""))


FULL = json.load(open(os.path.join(_ROOT, "rules", "decompose_full.json"), encoding="utf-8"))
RAWS = V._raw_texts(os.path.join(_ROOT, "config", "rules.candidates.json"))
REP = V.validate(FULL, RAWS)


# ── 전체 검증 실행 경로 ──────────────────────────────────────────────────
def test_full_baseline():
    print("\n[F-0] 46칸 전체를 동일 validator 로 검증한다")
    check("★★ 46/46 셀이 검증됐다", len(REP["cells"]) == 46, str(len(REP["cells"])))
    check("★★ 불변식 위반 0", REP["valid"], str(REP["violations"][:5]))
    check("★★ 의미 있는 미분해 구간 0",
          all(c["uncovered_meaningful"] == 0 for c in REP["cells"]))
    check("★ 모든 셀이 full 로 선언됐다",
          all(c["scope"] == "full" for c in REP["cells"]))


# ── ① occurrence 배치 fail-closed ───────────────────────────────────────
def test_placement_fail_closed():
    print("\n[F-1] builder occurrence 배치가 fail-closed 인가 (CIO 검수 ①)")

    # 같은 문자열이 원문에 두 번 나오는 synthetic fixture.
    # 투자 의미를 새로 만들지 않도록 중립 토큰만 쓴다.
    raw = "AAA / BBB / AAA"

    ok = [dict(raw_fragment="AAA", object_role="non_rule_evidence", rule_kind=None,
               downstream_effect=None, annotates_split_index=[], notes="", split_index=1),
          dict(raw_fragment="BBB", object_role="non_rule_evidence", rule_kind=None,
               downstream_effect=None, annotates_split_index=[], notes="", split_index=2),
          dict(raw_fragment="AAA", object_role="non_rule_evidence", rule_kind=None,
               downstream_effect=None, annotates_split_index=[], notes="", split_index=3)]
    merged, errs = place_and_merge("T", raw, copy.deepcopy(ok), "full")
    check("★ 순서를 지킨 반복 조각은 통과한다", not errs, str(errs))
    check("★ 반복 조각도 결합 표기까지 포함해 전 구간을 덮는다",
          "".join(f["raw_fragment"] for f in merged).replace("/", "") == "AAABBBAAA")

    # ★ 음성 — 순서를 뒤집으면 cursor 이후에서 못 찾아야 하고, 즉시 실패해야 한다
    bad = copy.deepcopy(ok)
    bad[0]["raw_fragment"], bad[1]["raw_fragment"] = "BBB", "AAA"
    _, errs = place_and_merge("T", raw, bad, "full")
    check("★★ [음성] 순서가 뒤집히면 BUILD 실패한다",
          any("순서/occurrence 불일치" in e for e in errs), str(errs))

    # ★★ 음성 — 예전 fallback(raw.find(t)) 이 살아 있었다면 통과했을 케이스.
    #    'AAA' 를 세 번 요구하면 세 번째는 cursor 이후에 없다.
    greedy = copy.deepcopy(ok)
    greedy[1]["raw_fragment"] = "AAA"
    _, errs = place_and_merge("T", raw, greedy, "full")
    check("★★ [음성] occurrence 가 모자라면 앞으로 되돌아가 잡지 않는다",
          any("순서/occurrence 불일치" in e for e in errs), str(errs))

    # ★★ -1 이 span 에 들어가지 않는다 — 배치 실패 시 병합 자체를 하지 않는다
    _, errs = place_and_merge("T", raw, greedy, "full")
    merged2, _ = place_and_merge("T", raw, copy.deepcopy(greedy), "full")
    check("★★ 배치 실패 회차는 결합 표기를 만들어 붙이지 않는다",
          all(f["raw_fragment"] not in VC.CONNECTIVES for f in merged2))


# ── ② reference cell 승격 0건 ───────────────────────────────────────────
def test_reference_promotion_zero():
    print("\n[F-2] reference cell 의 Rule 승격이 0건인가 (CIO 검수 ②)")
    ref = [(c["candidate_id"], f) for c in FULL["cells"]
           if c["source_cell"] in V.REFERENCE_CELLS for f in c["fragments"]]
    promoted = [f"{cid}#{f['split_index']}" for cid, f in ref
                if f["object_role"] == "rule_candidate"
                or f["downstream_effect"] in ("daily_eligibility", "강등 검토")]
    check("★★ 46칸에서 reference → Rule 승격 0건", not promoted, str(promoted))
    check("★ 참조 칸 조각은 execution_reference 이거나 부재 표식뿐이다",
          all(f["object_role"] in ("execution_reference", "non_rule_evidence") for _, f in ref))

    # ★ 음성 — 승격을 주입하면 잡혀야 한다. effect_evidence 를 붙여도 마찬가지다.
    for extra, label in (({}, "근거 없이"), ({"effect_evidence": "SMA20"}, "effect_evidence 를 붙여도")):
        p = copy.deepcopy(FULL)
        for c in p["cells"]:
            if c["candidate_id"] == "MSFT::핵심 지지":
                c["fragments"][0].update(object_role="rule_candidate", rule_kind="ENT",
                                         downstream_effect="daily_eligibility", **extra)
        r = V.validate(p, RAWS)
        check(f"★★ [음성] {label} 승격하면 잡힌다",
              any("Rule 승격은 허용하지 않는다" in x for x in r["violations"]),
              str(r["violations"][:3]))


# ── ③ 결합 표기 보존 ────────────────────────────────────────────────────
def test_connective_preserved():
    print("\n[F-3] 결합 표기가 사라지면 I-1 이 잡는가 (CIO 검수 ③)")
    check("★★ builder 와 validator 가 같은 정의를 공유한다",
          VC.CONNECTIVES and not (VC.CONNECTIVES & VC.PUNCTUATION))
    check("★★ 결합 표기는 무시 문자에 들어 있지 않다",
          all(t not in VC.IGNORABLE_CHARS for t in VC.CONNECTIVES))
    check("★ 무시 문자는 공백과 문장 부호뿐이다",
          set(VC.IGNORABLE_CHARS) == set(" \t\n") | VC.PUNCTUATION)

    conn = [(c["candidate_id"], f["split_index"]) for c in FULL["cells"]
            for f in c["fragments"] if f["raw_fragment"] in VC.CONNECTIVES]
    check(f"★ 46칸에 결합 표기 객체가 보존돼 있다 ({len(conn)}건)", len(conn) > 0)

    # ★ 음성 — 결합 표기 객체를 하나 지우면 I-1 위반이 나야 한다
    cid, idx = conn[0]
    p = copy.deepcopy(FULL)
    for c in p["cells"]:
        if c["candidate_id"] == cid:
            c["fragments"] = [f for f in c["fragments"] if f["split_index"] != idx]
            for i, f in enumerate(c["fragments"], 1):
                f["split_index"] = i
    r = V.validate(p, RAWS)
    check(f"★★ [음성] 결합 표기({cid}#{idx})를 지우면 I-1 위반이 난다",
          any("미분해 구간" in x for x in r["violations"]), str(r["violations"][:3]))

    # ★ 음성 — '·' 와 '/' 를 무시 문자로 되돌리면 위 검사가 무력화된다는 것을 고정한다
    check("★★ '/' 와 '·' 는 무시 문자가 아니다",
          "/" not in VC.IGNORABLE_CHARS and "·" not in VC.IGNORABLE_CHARS)


# ── 권한 경계 ────────────────────────────────────────────────────────────
def test_authority():
    print("\n[F-4] 46칸 산출물도 authority 가 아니다")
    check("★★ authority = False", FULL["authority"] is False)
    check("★★ consumable_by_evaluator = False", FULL["consumable_by_evaluator"] is False)
    check("★ dedup 금지가 명시돼 있다", "합치지 않는다" in FULL["dedup_policy"])
    check("★ 강등 검토가 legacy 로 표시돼 있다", "강등 검토" in FULL["legacy_tokens"])
    check("★ execution_reference 가 Inventory 집계 제외로 표시돼 있다",
          "제외" in FULL["inventory_scope"]["execution_reference"])


SUITES = [test_full_baseline, test_placement_fail_closed, test_reference_promotion_zero,
          test_connective_preserved, test_authority]


def main():
    print("B1 46칸 전체 검증 + CIO 검수 3건 음성 회귀")
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
