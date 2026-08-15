"""B5-2A — decision normalization 불변식 회귀 (음성 포함)

핵심 둘: **답을 만들지 않았는가**, **묶어서는 안 되는 것을 묶지 않았는가.**
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "rules"))
import decision_normalization as N                            # noqa: E402

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


NORM = json.load(open(os.path.join(_ROOT, "rules", "decision_normalization.json"),
                     encoding="utf-8"))
DEC = json.load(open(os.path.join(_ROOT, "rules", "definition_decision.json"),
                     encoding="utf-8"))
AMB = json.load(open(os.path.join(_ROOT, "rules", "data_source_ambiguity.json"),
                     encoding="utf-8"))
CANON = json.load(open(os.path.join(_ROOT, "rules", "canonical_rules.json"), encoding="utf-8"))

UNIVERSE = {N.qid(d["canonical_rule_id"], q["from_component"])
            for d in DEC["items"] for q in d["decision_required"]}
UNIVERSE |= {N.qid(a["canonical_rule_id"], q["from_component"])
             for a in AMB["items"] for q in a["additional_decision_required"]}


def rebuild(shared=None, conditional=None, specific=None):
    o = (N.SHARED_GROUPS, N.CONDITIONAL_GROUPS, N.RULE_SPECIFIC)
    tmp = None
    try:
        if shared is not None:
            N.SHARED_GROUPS = shared
        if conditional is not None:
            N.CONDITIONAL_GROUPS = conditional
        if specific is not None:
            N.RULE_SPECIFIC = specific
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            tmp = f.name
        return N.build(out_path=tmp)[1]
    finally:
        N.SHARED_GROUPS, N.CONDITIONAL_GROUPS, N.RULE_SPECIFIC = o
        if tmp:
            os.unlink(tmp)


def all_assigned():
    a = []
    for g in NORM["shared_decision_groups"] + NORM["conditional_share_candidates"]:
        a += g["member_questions"] + g.get("must_remain_rule_specific", [])
    for rs in NORM["must_remain_rule_specific"]:
        a += rs["questions"]
    return a


def test_partition():
    print("\n[N-0] 43개 질문이 정확히 한 번씩 배정된다")
    a = all_assigned()
    check("★★ 질문 43건", len(UNIVERSE) == 43, str(len(UNIVERSE)))
    check("★★ 배정 총계 43건 (중복 0)", len(a) == 43 and len(set(a)) == 43, str(len(a)))
    check("★★ 배정 집합 == 질문 집합", set(a) == UNIVERSE, str(set(a) ^ UNIVERSE))
    check("★★ [음성] 배정 누락이 있으면 잡힌다",
          any("배정되지 않은 질문" in e
              for e in rebuild(specific=N.RULE_SPECIFIC[:-1])))
    dupg = copy.deepcopy(N.SHARED_GROUPS)
    dupg[0]["member_questions"] = dupg[0]["member_questions"] + [
        N.qid("RULE-0025", "time_window")]
    check("★★ [음성] 한 질문이 두 곳에 배정되면 잡힌다",
          any("두 곳에 배정" in e for e in rebuild(shared=dupg)))
    badg = copy.deepcopy(N.SHARED_GROUPS)
    badg[0]["member_questions"] = badg[0]["member_questions"] + ["RULE-9999::threshold"]
    check("★★ [음성] 존재하지 않는 질문을 넣으면 잡힌다",
          any("존재하지 않는 질문" in e for e in rebuild(shared=badg)))


def test_no_answers():
    print("\n[N-1] ★★ 답을 만들지 않았다")
    check("★★ answers 0 · definitions 0 · candidates 0 · targets 0",
          NORM["answers_created"] == 0 and NORM["definitions_created"] == 0
          and NORM["candidate_values_created"] == 0 and NORM["targets_selected"] == 0)
    blob = json.dumps(NORM, ensure_ascii=False)
    check("★★ 답을 담을 수 있는 필드가 없다",
          not any(f'"{k}"' in blob for k in
                  ("answer", "decided_value", "value", "threshold_value",
                   "candidate_values", "recommended", "proposed_definition")))
    # 원문 밖 숫자·대상명이 그룹 산문에 없는가
    ct = {r["canonical_rule_id"]: r["condition_text"] for r in CANON["canonical_rules"]}
    bad = []
    for g in NORM["shared_decision_groups"] + NORM["conditional_share_candidates"]:
        allow_n, allow_l = set(), set()
        for r in g["member_rules"]:
            allow_n |= set(re.findall(r"\d+", ct[r]))
            allow_l |= {t.lower() for t in re.findall(r"[A-Za-z]{2,}", ct[r])}
        for f in ("shared_semantics_reason", "precondition", "note"):
            t = g.get(f) or ""
            bad += [(g["group_id"], x) for x in re.findall(r"[A-Za-z]{2,}", t)
                    if x.lower() not in allow_l
                    and not re.fullmatch(r"(RULE|NO_MERGE|DISTINCT|CIO|ASP|DRAM|HBM|LTA)", x)]
    check("★★ 그룹 산문에 원문 밖 대상명 0건", not bad, str(bad))

    g = copy.deepcopy(N.SHARED_GROUPS)
    g[0]["shared_semantics_reason"] += " 하락 폭은 5% 로 한다."
    check("★★ [음성] 산문에 값을 적으면 잡힌다",
          any("값을 만든 것" in e for e in rebuild(shared=g)))
    g = copy.deepcopy(N.SHARED_GROUPS)
    g[0]["shared_semantics_reason"] += " 계열은 DRAMeXchange 로 한다."
    check("★★ [음성] 산문에 대상을 적으면 잡힌다",
          any("대상을 선택한 것" in e for e in rebuild(shared=g)))


def test_grouping_discipline():
    print("\n[N-2] ★ 묶어서는 안 되는 것을 묶지 않았다")
    G = {g["group_id"]: g for g in NORM["shared_decision_groups"]}
    check("★★ 확정 공유 그룹 2개 (CIO 가 직접 든 예시)", len(G) == 2)
    check("★★ G1 = DRAM ASP 두 종목", G["G1"]["member_rules"] == ["RULE-0002", "RULE-0018"])
    check("★★ G2 = 상대강도 두 종목", G["G2"]["member_rules"] == ["RULE-0010", "RULE-0019"])

    spec = {q for rs in NORM["must_remain_rule_specific"] for q in rs["questions"]}
    for rid, why in (("RULE-0021", "Azure 유의미 하회"), ("RULE-0022", "RPO 급둔화"),
                     ("RULE-0017", "HBM 예약·LTA 축소")):
        check(f"★★ {rid} ({why}) 는 묶이지 않았다",
              all(q.startswith(rid) for q in spec if q.startswith(rid))
              and any(q.startswith(rid) for q in spec))
    check("★★ RULE-0017 과 RULE-0020 을 HBM 이라는 이유로 묶지 않았다",
          not any({"RULE-0017", "RULE-0020"} <= set(g["member_rules"])
                  for g in NORM["shared_decision_groups"]
                  + NORM["conditional_share_candidates"]))
    check("★★ RULE-0025 를 형태 유사성으로 G2 에 넣지 않았다",
          "RULE-0025" not in G["G2"]["member_rules"]
          and N.qid("RULE-0025", "time_window") in spec)
    check("★ 묶지 않은 이유가 전부 기록돼 있다",
          all(rs["reason"] for rs in NORM["must_remain_rule_specific"]))
    check("★★ [음성] 사유 없는 rule_specific 항목은 잡힌다",
          any("사유가 없다" in e for e in rebuild(
              specific=[{**N.RULE_SPECIFIC[0], "reason": ""}] + N.RULE_SPECIFIC[1:])))

    g = copy.deepcopy(N.SHARED_GROUPS)
    g[0]["member_rules"] = ["RULE-0002"]
    g[0]["member_questions"] = [q for q in g[0]["member_questions"]
                                if q.startswith("RULE-0002")]
    check("★★ [음성] 구성원 1개짜리 '그룹' 은 잡힌다",
          any("2개 미만" in e for e in rebuild(shared=g)))
    g = copy.deepcopy(N.SHARED_GROUPS)
    g[1]["shared_decision_units"] = g[1]["shared_decision_units"] + ["threshold"]
    check("★★ [음성] 구성원에 없는 질문을 공유 단위로 넣으면 잡힌다",
          any("질문이 없는데 공유 단위" in e for e in rebuild(shared=g)))


def test_conditional_not_confirmed():
    print("\n[N-3] 조건부 후보는 확정 그룹이 아니다")
    C = NORM["conditional_share_candidates"]
    check("★★ 조건부 후보 1건 (capex 하향)", len(C) == 1 and C[0]["group_id"] == "C1")
    check("★★ precondition 이 명시돼 있다", bool(C[0].get("precondition")))
    check("★★ 지금 확정하지 않는다고 적혀 있다", "지금 확정하지 않는다" in C[0]["precondition"])
    check("★★ 확정 그룹 목록과 분리돼 있다",
          C[0]["group_id"] not in {g["group_id"] for g in NORM["shared_decision_groups"]})
    check("★ data_source 2건은 그룹 안에서도 rule 고유로 남겼다",
          set(C[0]["must_remain_rule_specific"])
          == {N.qid("RULE-0004", "data_source"), N.qid("RULE-0015", "data_source")})
    check("★★ 축약 결과를 두 가지로 함께 보고한다",
          NORM["counts"]["decision_units_if_conditional_shared"]
          != NORM["counts"]["decision_units_if_conditional_rejected"])


def test_counts():
    print("\n[N-4] 축약 결과")
    c = NORM["counts"]
    check("★★ 질문 43 → 결정 단위 34 (조건부 공유 시)",
          c["questions_total"] == 43 and c["decision_units_if_conditional_shared"] == 34,
          str(c))
    check("★ 조건부 기각 시 36", c["decision_units_if_conditional_rejected"] == 36)
    check("★ 공유 그룹이 흡수한 질문 14건",
          c["questions_in_shared_groups"] + c["questions_in_conditional_groups"] == 18
          and c["questions_in_shared_groups"] == 14)
    check("★ 축약 폭이 작은 이유가 관찰로 기록돼 있다",
          any("기업 고유 사업지표" in o for o in NORM["observations"]))


def test_upstream_untouched():
    print("\n[N-5] 상위 artifact 를 건드리지 않았다")
    for k, f in (("definition_decision_sha256", "definition_decision.json"),
                 ("data_source_ambiguity_sha256", "data_source_ambiguity.json"),
                 ("canonical_rules_sha256", "canonical_rules.json")):
        check(f"★★ {f} 해시 불변",
              NORM["decided_against"][k] == N._sha(os.path.join(_ROOT, "rules", f)))
    check("★★ B5-1 resolution_status 15건 불변",
          all(d["resolution_status"] == "REQUIRES_CIO_DEFINITION" for d in DEC["items"]))
    check("★★ canonical record 25 불변 · condition_semantics/scope UNRESOLVED",
          len(CANON["canonical_rules"]) == 25
          and all(r["condition_semantics"] == "UNRESOLVED" and r["scope"] == "UNRESOLVED"
                  for r in CANON["canonical_rules"]))
    check("★★ authority / consumable = False",
          NORM["authority"] is False and NORM["consumable_by_evaluator"] is False)


SUITES = [test_partition, test_no_answers, test_grouping_discipline,
          test_conditional_not_confirmed, test_counts, test_upstream_untouched]


def main():
    print("B5-2A decision normalization — 불변식 회귀")
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
