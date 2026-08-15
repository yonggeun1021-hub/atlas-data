"""B5-1A — data-source ambiguity 불변식 회귀 (음성 포함)

핵심: **대상 목록·데이터 소스를 새로 선택하지 않았는가.**
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
import data_source_ambiguity as A                             # noqa: E402

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


AMB = json.load(open(os.path.join(_ROOT, "rules", "data_source_ambiguity.json"),
                     encoding="utf-8"))
INV = json.load(open(os.path.join(_ROOT, "rules", "definition_inventory.json"),
                     encoding="utf-8"))
DEC = json.load(open(os.path.join(_ROOT, "rules", "definition_decision.json"),
                     encoding="utf-8"))
CANON = json.load(open(os.path.join(_ROOT, "rules", "canonical_rules.json"), encoding="utf-8"))
INV_BY = {i["canonical_rule_id"]: i for i in INV["items"]}


def rebuild_with(adj):
    orig = A.ADJUDICATION
    tmp = None
    try:
        A.ADJUDICATION = adj
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            tmp = f.name
        return A.build(out_path=tmp)[1]
    finally:
        A.ADJUDICATION = orig
        if tmp:
            os.unlink(tmp)


def test_scope():
    print("\n[A-0] 범위 — data_source 결핍 10건만")
    scope = {i["canonical_rule_id"] for i in INV["items"]
             if "data_source" in i["missing_components"]}
    check("★★ 대상 10건", len(scope) == 10, str(len(scope)))
    check("★★ 판정 10/10", {i["canonical_rule_id"] for i in AMB["items"]} == scope)
    check("★★ data_source 결핍이 없는 rule 은 포함되지 않았다",
          not ({i["canonical_rule_id"] for i in AMB["items"]}
               & {"RULE-0009", "RULE-0012", "RULE-0016", "RULE-0019", "RULE-0025"}))
    check("★ 대상 집합을 B5-0 원본에서 재계산했다 (B5-1 view 미사용)",
          "definition_decision" not in AMB["scope_note"])
    check("★★ [음성] data_source 결핍이 아닌 rule 을 판정하면 잡힌다",
          any("data_source 결핍이 아닌" in e for e in rebuild_with(
              {**A.ADJUDICATION, "RULE-0025": dict(cls=A.CAPABILITY, why="x", question=None)})))
    check("★★ [음성] 대상 하나를 빠뜨리면 잡힌다",
          any("판정이 없다" in e for e in rebuild_with(
              {k: v for k, v in A.ADJUDICATION.items() if k != "RULE-0004"})))


def test_vocabulary_closed():
    print("\n[A-1] 분류 어휘가 닫혀 있다")
    used = {i["data_source_deficiency_class"] for i in AMB["items"]}
    check("★★ 허용 밖 분류 0", used <= A.CLASSES, str(used - A.CLASSES))
    check("★ CIO 지시문 표현 3개 그대로", sorted(AMB["classification_vocabulary"])
          == sorted(A.CLASSES))
    check("★★ [음성] 새 분류 토큰을 만들면 잡힌다",
          any("허용 밖 분류" in e for e in rebuild_with(
              {**A.ADJUDICATION,
               "RULE-0004": dict(cls="TARGET_UNDEFINED", why="x", question="q")})))


def test_no_selection():
    print("\n[A-2] ★★ 대상·데이터 소스를 선택하지 않았다")
    check("★★ targets_selected = 0 · definitions 0 · candidates 0",
          AMB["targets_selected"] == 0 and AMB["definitions_created"] == 0
          and AMB["candidate_values_created"] == 0)
    check("★★ 선택을 담을 수 있는 필드가 없다",
          not any(k in i for i in AMB["items"]
                  for k in ("selected_target", "chosen_source", "universe",
                            "candidate_targets", "series", "ticker_list")))

    # ★★ 질문 안 라틴 토큰·숫자는 전부 원문에서 와야 한다
    bad = []
    for i in AMB["items"]:
        lat = {t.lower() for t in re.findall(r"[A-Za-z]{2,}", i["condition_text"])}
        num = set(re.findall(r"\d+", i["condition_text"]))
        for q in i["additional_decision_required"]:
            bad += [(i["canonical_rule_id"], t) for t in re.findall(r"[A-Za-z]{2,}", q["question"])
                    if t.lower() not in lat]
            bad += [(i["canonical_rule_id"], n) for n in re.findall(r"\d+", q["question"])
                    if n not in num]
    check("★★ 질문에 원문 밖 대상명·숫자 0건", not bad, str(bad))

    # 음성 — CIO 가 경고한 그 오류를 그대로 주입한다
    a = copy.deepcopy(A.ADJUDICATION)
    a["RULE-0015"] = dict(cls=A.BOTH, why="x",
                          question="'하이퍼스케일러'를 MSFT GOOG META AMZN 으로 할 것인가?")
    check("★★ [음성] hyperscaler 를 임의 지정하면 잡힌다",
          any("원문 밖 대상명" in e for e in rebuild_with(a)))
    a = copy.deepcopy(A.ADJUDICATION)
    a["RULE-0002"] = dict(cls=A.BOTH, why="x",
                          question="'DRAM ASP'를 DRAMeXchange 계약가로 할 것인가?")
    check("★★ [음성] 데이터 소스를 임의 선택하면 잡힌다",
          any("원문 밖 대상명" in e for e in rebuild_with(a)))


def test_question_rules():
    print("\n[A-3] 질문은 semantic 결핍에만 붙는다")
    sem = [i for i in AMB["items"]
           if i["data_source_deficiency_class"] in (A.SEMANTIC, A.BOTH)]
    cap = [i for i in AMB["items"] if i["data_source_deficiency_class"] == A.CAPABILITY]
    check(f"★★ semantic 계열 {len(sem)}건 전부 질문이 있다",
          all(i["additional_decision_required"] for i in sem))
    check(f"★★ capability 전용 {len(cap)}건은 질문이 없다",
          not any(i["additional_decision_required"] for i in cap))
    check("★ 추가 질문 6건", AMB["counts"]["additional_questions"] == 6)
    check("★★ 모든 추가 질문이 data_source 에서 추적된다",
          all(q["from_component"] == "data_source"
              for i in AMB["items"] for q in i["additional_decision_required"]))
    check("★★ [음성] semantic 인데 질문이 없으면 잡힌다",
          any("semantic 결핍인데 질문이 없다" in e for e in rebuild_with(
              {**A.ADJUDICATION,
               "RULE-0004": dict(cls=A.SEMANTIC, why="x", question=None)})))
    check("★★ [음성] capability 전용에 질문을 붙이면 잡힌다",
          any("capability 전용인데" in e for e in rebuild_with(
              {**A.ADJUDICATION,
               "RULE-0021": dict(cls=A.CAPABILITY, why="x", question="무엇으로 볼 것인가?")})))


def test_cio_checklist():
    print("\n[A-4] CIO 확인 대상 6건의 결과")
    by = {i["canonical_rule_id"]: i["data_source_deficiency_class"] for i in AMB["items"]}
    check("★★ RULE-0004 누구의 capex → semantic target ambiguity",
          by["RULE-0004"] == A.SEMANTIC)
    check("★★ RULE-0015 hyperscaler universe → both", by["RULE-0015"] == A.BOTH)
    check("★★ RULE-0002 · RULE-0018 DRAM ASP series → both",
          by["RULE-0002"] == A.BOTH and by["RULE-0018"] == A.BOTH)
    check("★★ RULE-0017 HBM 예약·LTA → both", by["RULE-0017"] == A.BOTH)
    check("★★ RULE-0020 HBM 공급 확대 → both", by["RULE-0020"] == A.BOTH)
    check("★★ RULE-0021 · RULE-0022 → data capability gap (CIO 가설 확인)",
          by["RULE-0021"] == A.CAPABILITY and by["RULE-0022"] == A.CAPABILITY)
    check("★ RULE-0004 는 capability 를 '아직 판정 불가' 로 남겼다",
          "판정할 수 없다" in AMB["capability_undetermined_note"]
          or "판정할 수 없다" in next(i["why"] for i in AMB["items"]
                                  if i["canonical_rule_id"] == "RULE-0004"))


def test_upstream_untouched():
    print("\n[A-5] 상위 artifact 를 건드리지 않았다")
    for k, f in (("definition_inventory_sha256", "definition_inventory.json"),
                 ("definition_decision_sha256", "definition_decision.json"),
                 ("canonical_rules_sha256", "canonical_rules.json"),
                 ("decompose_full_sha256", "decompose_full.json")):
        check(f"★★ {f} 해시 불변",
              AMB["decided_against"][k] == A._sha(os.path.join(_ROOT, "rules", f)))
    check("★★ B5-0 missing_components mutation 0",
          all(i["b5_0_missing_components"] == INV_BY[i["canonical_rule_id"]]["missing_components"]
              for i in AMB["items"]))
    check("★★ B5-1 resolution_status 를 바꾸지 않았다",
          all(d["resolution_status"] == "REQUIRES_CIO_DEFINITION" for d in DEC["items"]))
    check("★★ canonical record 25 불변 · condition_semantics/scope UNRESOLVED",
          len(CANON["canonical_rules"]) == 25
          and all(r["condition_semantics"] == "UNRESOLVED" and r["scope"] == "UNRESOLVED"
                  for r in CANON["canonical_rules"]))
    check("★★ condition_text 변경 0",
          all(i["condition_text"] == INV_BY[i["canonical_rule_id"]]["condition_text"]
              for i in AMB["items"]))


def test_not_consumable():
    print("\n[A-6] evaluator 로 흘러가지 않는다")
    check("★★ authority = False", AMB["authority"] is False)
    check("★★ consumable_by_evaluator = False", AMB["consumable_by_evaluator"] is False)
    check("★ 선택 금지가 artifact 에 명시돼 있다", "이미 정의 생성" in AMB["no_selection_note"])


SUITES = [test_scope, test_vocabulary_closed, test_no_selection, test_question_rules,
          test_cio_checklist, test_upstream_untouched, test_not_consumable]


def main():
    print("B5-1A data-source ambiguity — 불변식 회귀")
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
