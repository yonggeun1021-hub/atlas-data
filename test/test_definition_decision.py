"""B5-1 — definition resolution decision 불변식 회귀 (음성 포함)

핵심 둘: **정의도 후보값도 만들지 않았는가**, **질문이 B5-0 결핍 항목에서 추적되는가.**
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
import definition_decision as DD                              # noqa: E402

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


DEC = json.load(open(os.path.join(_ROOT, "rules", "definition_decision.json"),
                     encoding="utf-8"))
INV = json.load(open(os.path.join(_ROOT, "rules", "definition_inventory.json"),
                     encoding="utf-8"))
CANON = json.load(open(os.path.join(_ROOT, "rules", "canonical_rules.json"), encoding="utf-8"))
DECOMP = json.load(open(os.path.join(_ROOT, "rules", "decompose_full.json"), encoding="utf-8"))
INV_BY = {i["canonical_rule_id"]: i for i in INV["items"]}


def rebuild_with(questions):
    """QUESTIONS 를 바꿔치기해 빌더 검증이 실제로 잡는지 본다.

    ★ 임시 경로에만 쓴다 — 음성 테스트가 실제 산출물을 덮어쓰면 안 된다.
    """
    orig = DD.QUESTIONS
    tmp = None
    try:
        DD.QUESTIONS = questions
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            tmp = f.name
        return DD.build(out_path=tmp)[1]
    finally:
        DD.QUESTIONS = orig
        if tmp:
            os.unlink(tmp)


def test_coverage_and_status():
    print("\n[R-0] 15/15 coverage · 허용 status 만")
    check("★★ decision 15건", len(DEC["items"]) == 15, str(len(DEC["items"])))
    check("★★ B5-0 inventory 15건과 정확히 대응",
          {i["canonical_rule_id"] for i in DEC["items"]} == set(INV_BY))
    check("★★ 허용 status 밖 토큰 0",
          {i["resolution_status"] for i in DEC["items"]} <= DD.STATUS)
    check("★★ REQUIRES_CIO_DEFINITION 15 / RESOLVABLE_FROM_CANON 0",
          DEC["counts"][DD.REQUIRES] == 15 and DEC["counts"][DD.RESOLVABLE] == 0
          and all(i["resolution_status"] == DD.REQUIRES for i in DEC["items"]))
    # ★ 금지 토큰이 **status 값·어휘 목록으로 쓰였는가**를 본다.
    #   artifact 본문에서 "이런 토큰을 만들지 않았다"고 언급하는 것은 위반이 아니다 —
    #   전체 blob 문자열 검사는 그 언급까지 잡아 검사를 무의미하게 만든다.
    banned = {"DATA_MISSING", "PARSER_MISSING", "SCHEDULED_FOR_REVIEW", "PARTIALLY_DEFINED"}
    used_as_status = ({i["resolution_status"] for i in DEC["items"]}
                      | set(DEC["status_vocabulary"]) | set(DEC["counts"]))
    check("★★ 금지된 status 토큰이 status 값·어휘로 쓰이지 않았다",
          not (banned & used_as_status), str(banned & used_as_status))
    check("★ status_vocabulary 가 2개로 고정",
          sorted(DEC["status_vocabulary"]) == sorted(DD.STATUS))


def test_no_definitions_no_candidates():
    print("\n[R-1] ★★ 정의도 후보값도 만들지 않았다")
    check("★★ definitions_created = 0 · candidate_values_created = 0",
          DEC["definitions_created"] == 0 and DEC["candidate_values_created"] == 0)
    check("★★ 후보값을 담을 수 있는 필드가 스키마에 없다",
          not any(k in i for i in DEC["items"]
                  for k in ("candidate_values", "recommended_value", "proposed_definition",
                            "options", "default", "suggested", "range")))
    check("★★ 질문 안에도 그런 필드가 없다",
          not any(k in q for i in DEC["items"] for q in i["decision_required"]
                  for k in ("candidate_values", "recommended_value", "proposed_definition")))

    # ★★ 질문 안 숫자는 전부 원문에서 와야 한다
    bad = []
    for i in DEC["items"]:
        allowed = set(re.findall(r"\d+", i["condition_text"]))
        for q in i["decision_required"]:
            bad += [(i["canonical_rule_id"], n)
                    for n in re.findall(r"\d+", q["question"]) if n not in allowed]
    check("★★ 질문에 원문 밖 숫자 0건", not bad, str(bad))

    # 음성 — CIO 가 든 그 오류를 그대로 주입한다
    q = copy.deepcopy(DD.QUESTIONS)
    q["RULE-0025"] = [("time_window", "'연속'을 3거래일 또는 5거래일 중 무엇으로 할 것인가?")]
    check("★★ [음성] `3거래일 또는 5거래일 중?` 을 물으면 잡힌다",
          any("원문 밖 숫자" in e for e in rebuild_with(q)))
    q = copy.deepcopy(DD.QUESTIONS)
    q["RULE-0021"] = [("event_definition", "'유의미 하회'를 45%cc 대비 3%p 로 볼 것인가?")]
    check("★★ [음성] `45%cc 대비 -3%p` 같은 후보를 넣으면 잡힌다",
          any("원문 밖 숫자" in e for e in rebuild_with(q)))


def test_questions_traceable():
    print("\n[R-2] ★ 질문이 B5-0 결핍 항목에서 추적된다")
    check(f"★ 질문 {DEC['counts']['decision_questions']}건 "
          f"(정의 성분 {DEC['counts']['definition_component_questions']} · "
          f"구조 미결 {DEC['counts']['source_semantics_questions']})",
          DEC["counts"]["decision_questions"] == 37
          and DEC["counts"]["definition_component_questions"] == 35
          and DEC["counts"]["source_semantics_questions"] == 2)
    check("★★ 질문 수가 두 축의 합과 같다 — 어느 축에도 없는 질문이 없다",
          DEC["counts"]["decision_questions"]
          == DEC["counts"]["definition_component_questions"]
          + DEC["counts"]["source_semantics_questions"])
    check("★★ 정의 성분 질문은 missing_components 에서 나왔다",
          all(q["from_component"] in INV_BY[i["canonical_rule_id"]]["missing_components"]
              for i in DEC["items"] for q in i["decision_required"]
              if q["axis"] == "definition_component"))
    # ★★ 구조 미결 질문은 다른 목록에서 추적된다 — 정의 성분에 섞이면 안 된다
    SEM = {x["canonical_rule_id"]: {y["axis"] for y in INV["source_semantics_unresolved"]
                                    if y["canonical_rule_id"] == x["canonical_rule_id"]}
           for x in INV["source_semantics_unresolved"]}
    check("★★ 구조 미결 질문은 source_semantics_unresolved 에서 나왔다",
          all(q["from_component"] in SEM.get(i["canonical_rule_id"], set())
              for i in DEC["items"] for q in i["decision_required"]
              if q["axis"] == "source_semantics"))
    check("★★ 두 축이 어휘를 공유하지 않는다",
          not ({q["from_component"] for i in DEC["items"] for q in i["decision_required"]
                if q["axis"] == "source_semantics"}
               & set(INV["allowed_missing_components"])))
    check("★★ 모든 질문에 축이 표시돼 있다",
          all(q.get("axis") in ("definition_component", "source_semantics")
              for i in DEC["items"] for q in i["decision_required"]))
    check("★★ 질문 없는 semantic 결핍 항목 0",
          all(set(i["judgment_basis"])
              <= {q["from_component"] for q in i["decision_required"]}
              for i in DEC["items"]))
    check("★★ decision_required 가 빈 항목 0",
          all(i["decision_required"] for i in DEC["items"]))

    q = copy.deepcopy(DD.QUESTIONS)
    q["RULE-0025"] = [("threshold", "임계값을 어떻게 정의할 것인가?")]
    check("★★ [음성] 결핍 항목에 없는 component 에서 질문을 만들면 잡힌다",
          any("결핍 항목에 없는" in e for e in rebuild_with(q)))
    q = copy.deepcopy(DD.QUESTIONS)
    q["RULE-0025"] = []
    check("★★ [음성] 질문을 비우면 잡힌다",
          any("decision_required 가 비어" in e or "질문이 없는 결핍" in e
              for e in rebuild_with(q)))
    q = copy.deepcopy(DD.QUESTIONS)
    q["RULE-0022"] = [x for x in q["RULE-0022"] if x[0] != "time_window"]
    check("★★ [음성] semantic 결핍 하나를 안 물으면 잡힌다",
          any("질문이 없는 결핍" in e for e in rebuild_with(q)))


def test_axis_separation():
    print("\n[R-3] ★ definition resolution 과 data capability 는 별도 축이다")
    check("★★ 어떤 항목도 data_source 만을 근거로 삼지 않았다",
          all(i["judgment_basis"] for i in DEC["items"]),
          str([i["canonical_rule_id"] for i in DEC["items"] if not i["judgment_basis"]]))
    check("★★ data capability 축에서 질문을 만들지 않았다",
          not any(q["from_component"] in DD.DATA_AXIS
                  for i in DEC["items"] for q in i["decision_required"]))
    check("★ data_source 결핍은 별도 필드로 보존된다",
          sum(1 for i in DEC["items"] if i["data_capability_axis"]) == 10)
    check("★ RULE-0019 는 data_source 결핍이 없는데도 REQUIRES 다 (정의만 없음)",
          next(i for i in DEC["items"] if i["canonical_rule_id"] == "RULE-0019")
          ["data_capability_axis"] == []
          and next(i for i in DEC["items"] if i["canonical_rule_id"] == "RULE-0019")
          ["resolution_status"] == DD.REQUIRES)

    q = copy.deepcopy(DD.QUESTIONS)
    q["RULE-0022"] = q["RULE-0022"] + [("data_source", "어느 원천을 쓸 것인가?")]
    check("★★ [음성] data capability 축에서 질문을 만들면 잡힌다",
          any("data capability 축" in e for e in rebuild_with(q)))


def test_boundary_case():
    print("\n[R-4] 경계 사례 — 거의 완성된 Rule 도 예외가 아니다")
    for rid in ("RULE-0011", "RULE-0015", "RULE-0021", "RULE-0025"):
        it = next(i for i in DEC["items"] if i["canonical_rule_id"] == rid)
        check(f"★★ {rid} 도 {DD.REQUIRES}", it["resolution_status"] == DD.REQUIRES)
    r25 = next(i for i in DEC["items"] if i["canonical_rule_id"] == "RULE-0025")
    check("★★ RULE-0025 의 결핍은 하나가 아니다 — 재검사로 event_definition 이 추가됐다",
          r25["judgment_basis"] == ["event_definition", "time_window"],
          str(r25["judgment_basis"]))
    check("★★ 그 위에 원문 구조 미결이 별도 축으로 올라와 있다",
          any(q["axis"] == "source_semantics" for q in r25["decision_required"]))
    check("★★ 낡은 서술이 정정됐다",
          "재검사 정정" in DEC["boundary_case_note"])
    check("★ 그 이유가 artifact 에 기록돼 있다",
          "전략을 만든 것" in DEC["boundary_case_note"])


def test_upstream_untouched():
    print("\n[R-5] 상위 artifact 를 건드리지 않았다")
    check("★★ B5-0 inventory 해시 불변",
          DEC["decided_against"]["definition_inventory_sha256"]
          == DD._sha(os.path.join(_ROOT, "rules", "definition_inventory.json")))
    check("★★ B3 canonical 해시 불변",
          DEC["decided_against"]["canonical_rules_sha256"]
          == DD._sha(os.path.join(_ROOT, "rules", "canonical_rules.json")))
    check("★★ decompose_full 해시 불변",
          DEC["decided_against"]["decompose_full_sha256"]
          == DD._sha(os.path.join(_ROOT, "rules", "decompose_full.json")))
    check("★★ B5-0 missing_components mutation 0",
          all(INV_BY[i["canonical_rule_id"]]["missing_components"]
              == sorted(i["judgment_basis"] + i["data_capability_axis"],
                        key=INV_BY[i["canonical_rule_id"]]["missing_components"].index)
              for i in DEC["items"]))
    dstat = {f"{c['candidate_id']}#{f['split_index']}": f["definition_status"]
             for c in DECOMP["cells"] for f in c["fragments"]}
    check("★★ canonical definition_status 를 DEFINED 로 바꾸지 않았다",
          all(dstat[i["occurrence_id"]] == "UNDEFINED" for i in DEC["items"]))
    recs = {r["canonical_rule_id"]: r for r in CANON["canonical_rules"]}
    check("★★ canonical record 25 불변 · condition_semantics/scope UNRESOLVED",
          len(recs) == 25
          and all(r["condition_semantics"] == "UNRESOLVED" and r["scope"] == "UNRESOLVED"
                  for r in recs.values()))
    check("★★ condition_text 변경 0",
          all(i["condition_text"] == recs[i["canonical_rule_id"]]["condition_text"]
              for i in DEC["items"]))


def test_not_consumable():
    print("\n[R-6] evaluator 로 흘러가지 않는다")
    check("★★ authority = False", DEC["authority"] is False)
    check("★★ consumable_by_evaluator = False", DEC["consumable_by_evaluator"] is False)
    check("★ 판정 근거가 artifact 에 기록돼 있다",
          "source_has_resolution" in DEC["judgment_note"])


SUITES = [test_coverage_and_status, test_no_definitions_no_candidates,
          test_questions_traceable, test_axis_separation, test_boundary_case,
          test_upstream_untouched, test_not_consumable]


def main():
    print("B5-1 definition decision — 불변식 회귀")
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
