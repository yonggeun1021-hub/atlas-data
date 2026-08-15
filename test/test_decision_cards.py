"""B5-2B — decision card 불변식 회귀 (음성 포함)

핵심 셋: **답을 만들지 않았는가**, **게이트 순서가 지켜지는가**,
**공표 수치가 임계값으로 새는 경로가 없는가.**
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
import decision_cards as DC                                   # noqa: E402

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


CARDS = json.load(open(os.path.join(_ROOT, "rules", "decision_cards.json"),
                       encoding="utf-8"))
NORM = json.load(open(os.path.join(_ROOT, "rules", "decision_normalization.json"),
                      encoding="utf-8"))
CANON = json.load(open(os.path.join(_ROOT, "rules", "canonical_rules.json"), encoding="utf-8"))
AMB = json.load(open(os.path.join(_ROOT, "rules", "data_source_ambiguity.json"),
                     encoding="utf-8"))
DEC = json.load(open(os.path.join(_ROOT, "rules", "definition_decision.json"),
                     encoding="utf-8"))
C = CARDS["cards"]


# ★★ rebuild() 가 저장·복원하는 전역. K-15 가 같은 목록을 쓴다 —
#   두 곳에 따로 적으면 한쪽만 갱신돼 계약과 코드가 어긋난다(실제로 한 번 어긋났다).
RESTORED_GLOBALS = ("CARDS", "EVIDENCE", "OBSOLESCENCE", "CIO_DECISIONS",
                    "PREREQ_GRAPH", "CIO_FIXED_GRAPH")


def rebuild(cards=None, evidence=None, obsolescence=None, decisions=None,
            graph=None, fixed_graph=None, full=False):
    o = tuple(getattr(DC, n) for n in RESTORED_GLOBALS)
    tmp = None
    try:
        if cards is not None:
            DC.CARDS = cards
        if evidence is not None:
            DC.EVIDENCE = evidence
        if obsolescence is not None:
            DC.OBSOLESCENCE = obsolescence
        if decisions is not None:
            DC.CIO_DECISIONS = decisions
        if graph is not None:
            DC.PREREQ_GRAPH = graph
        if fixed_graph is not None:
            DC.CIO_FIXED_GRAPH = fixed_graph
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            tmp = f.name
        os.unlink(tmp)          # ★ 빈 파일을 남겨두지 않는다 — fail-closed 경로와 일관되게
        p, errs = DC.build(out_path=tmp)
        return (p, errs) if full else errs
    finally:
        for _n, _v in zip(RESTORED_GLOBALS, o):
            setattr(DC, _n, _v)
        if tmp and os.path.exists(tmp):   # 위반 시에는 애초에 쓰이지 않는다
            os.unlink(tmp)


def test_coverage_and_order():
    print("\n[K-0] 43질문 · 36카드 · 게이트 우선")
    check("★★ 카드 36건", len(C) == 36, str(len(C)))
    check("★★ 질문 43/43 커버",
          CARDS["counts"]["questions_covered"] == CARDS["counts"]["questions_total"] == 43,
          f'{CARDS["counts"]["questions_covered"]}/{CARDS["counts"]["questions_total"]}')
    check("★★ 첫 카드가 RULE-0004::data_source (게이트)",
          C[0]["decision_unit"] == "RULE-0004::data_source" and C[0]["is_gate"])
    check("★★ 게이트 카드는 1건", sum(1 for c in C if c["is_gate"]) == 1)
    # ★★ C1 기각 이후 — 게이트에 막힌 카드는 남아 있으면 안 된다
    check("★★ C1 대기 카드 0건", not any(c["blocked_by"] and "C1" in c["blocked_by"]
                                     for c in C))
    # ★★ D-2 — 선행조건은 충족돼도 이력이 남는다
    def prereqs(c):
        pr = c["prerequisite"]
        return [pr] if isinstance(pr, dict) else (pr or [])
    pre = [c for c in C if prereqs(c)]
    check("★★ 선행조건을 가진 카드 6건",
          [c["decision_unit"] for c in pre]
          == ["RULE-0015::threshold", "RULE-0015::comparison_baseline",
              "RULE-0025::event_definition", "RULE-0025::time_window",
              "RULE-0022::threshold", "RULE-0017::event_definition"],
          str([c["decision_unit"] for c in pre]))
    d15 = [c for c in pre if c["decision_unit"].startswith("RULE-0015")]
    check("★★ 수요측 둘은 충족됨으로 전이했다",
          all(p["status"] == "충족됨" for c in d15 for p in prereqs(c)))
    check("★★ 무엇에 막혀 있었는지가 지워지지 않았다",
          all(p["unit"] == "RULE-0015::data_source"
              and "이번 턴에 판정된 사항이 아니다" in p["history"]
              for c in d15 for p in prereqs(c)))
    check("★★ 무엇이 그것을 풀었는지가 기록된다",
          all("카드 4" in p["satisfied_by"] for c in d15 for p in prereqs(c)))
    check("★★ 선행 카드가 의존 카드보다 앞에 온다",
          all(next(i for i, x in enumerate(C) if x["decision_unit"] == p["unit"])
              < next(i for i, x in enumerate(C) if x is c)
              for c in pre for p in prereqs(c)))
    check("★ 선행 간선 8건 — 전부 충족",
          CARDS["counts"]["prerequisite_satisfied"] == 8
          and CARDS["counts"]["prerequisite_waiting"] == 0,
          f'{CARDS["counts"]["prerequisite_satisfied"]}/{CARDS["counts"]["prerequisite_waiting"]}')

    # ★★ 의존성은 배열 순서가 아니라 그래프로 표현돼 있는가
    edges = {u: {e["unit"] for e in es} for u, es in DC.PREREQ_GRAPH.items()}
    check("★★ 의미 축이 사건 정의의 직접 선행이다",
          edges.get("RULE-0025::event_definition") == {"RULE-0025::semantic_scope"},
          str(edges.get("RULE-0025::event_definition")))
    check("★★ 기간은 두 선행에 직접 걸린다 — 간접 충족으로 새지 않는다",
          edges.get("RULE-0025::time_window")
          == {"RULE-0025::semantic_scope", "RULE-0025::event_definition"},
          str(edges.get("RULE-0025::time_window")))
    check("★★ 모든 선행 간선이 실재 카드를 가리킨다",
          all(u in {c["decision_unit"] for c in C} and t in {c["decision_unit"] for c in C}
              for u, ts in edges.items() for t in ts))
    check("★★ 선행 단위가 항상 앞 순번이다",
          all(next(x["order"] for x in C if x["decision_unit"] == t)
              < next(x["order"] for x in C if x["decision_unit"] == u)
              for u, ts in edges.items() for t in ts))
    bad = copy.deepcopy(DC.PREREQ_GRAPH)
    bad["RULE-0025::time_window"] = [e for e in bad["RULE-0025::time_window"]
                                     if e["unit"] != "RULE-0025::semantic_scope"]
    errs = rebuild(graph=bad)
    check("★★ [음성] 의미 축 직접 간선을 빼면 판정 형태 위반으로 잡힌다",
          any("판정된 의존 형태와 다르다" in e for e in errs))

    # ★★ 재검사 반영 — 판정값은 보존하되 집행 가능으로 승격하지 않는다
    held = [c for c in C if c["execution_status"].startswith("보류")]
    check("★★ 보류 카드 0건 — 선행이 모두 풀렸다",
          not held, str([c["decision_unit"] for c in held]))
    check("★ 판정 후 보류 집계도 0",
          CARDS["counts"]["decided_but_held"] == 0)
    # ★★ [음성] 선행 하나를 되돌리면 보류와 대기 표시가 복원된다
    d = {k: v for k, v in DC.CIO_DECISIONS.items()
         if k != "RULE-0025::event_definition"}
    p = rebuild(decisions=d, full=True)[0]
    tw2 = next(c for c in p["cards"] if c["decision_unit"] == "RULE-0025::time_window")
    check("★★ [음성] 선행 판정을 지우면 다시 보류로 돌아간다",
          tw2["execution_status"].startswith("보류")
          and "RULE-0025::event_definition" in tw2["blocked_by"]
          and "RULE-0025::semantic_scope" not in tw2["blocked_by"]
          and "기존 판정값은 보존한다" in tw2["blocked_by"])
    check("★★ [음성] 되돌려도 판정값 자체는 남는다", bool(tw2["cio_decision"]))
    check("★★ 판정 완료 카드도 evaluator 연결은 아니라고 적힌다",
          all("evaluator 연결은 여전히 금지" in c["execution_status"]
              for c in C if c["cio_decision"] and not c["blocked_by"]))

    bad = copy.deepcopy(DC.CARDS)
    bad[1]["pending_c1"] = True
    check("★★ [음성] C1 기각 뒤에 대기 카드를 되살리면 잡힌다",
          any("C1 대기 카드가 남아 있다" in e for e in rebuild(cards=bad)))
    # ★★ 선행 카드 판정을 없애면 대기 상태로 되돌아가야 한다 (전이가 한 방향이 아님)
    d = {k: v for k, v in DC.CIO_DECISIONS.items() if k != "RULE-0015::data_source"}
    p = rebuild(decisions=d, full=True)[0]
    back = [c for c in p["cards"] if c["prerequisite"]]  # dict 또는 list
    check("★★ [음성] 선행 판정이 사라지면 다시 대기로 돌아간다",
          all(p["status"] == "대기" and c["blocked_by"]
              for c in back if c["decision_unit"].startswith("RULE-0015")
              for p in ([c["prerequisite"]] if isinstance(c["prerequisite"], dict)
                        else c["prerequisite"])))

    bad = copy.deepcopy(DC.CARDS)
    bad = bad[1:] + bad[:1]
    check("★★ [음성] 게이트를 앞에 두지 않으면 잡힌다",
          any("맨 앞이 아니다" in e for e in rebuild(cards=bad)))
    bad = [c for c in copy.deepcopy(DC.CARDS) if c["unit"] != "RULE-0025::time_window"]
    check("★★ [음성] 카드가 빠진 질문이 있으면 잡힌다",
          any("카드가 없는 질문" in e for e in rebuild(cards=bad)))
    dup = copy.deepcopy(DC.CARDS)
    dup.append(dup[-1])
    check("★★ [음성] 질문이 두 카드에 배정되면 잡힌다",
          any("두 카드에 배정" in e for e in rebuild(cards=dup)))


def test_no_decisions():
    print("\n[K-1] ★★ 답을 만들지 않았다 — 답란은 CIO 판정 기록에서만 온다")
    check("★★ 내가 만든 결정 0 · definitions 0 · candidates 0 · targets 0",
          CARDS["decisions_authored_by_claude"] == 0 and CARDS["definitions_created"] == 0
          and CARDS["candidate_values_created"] == 0 and CARDS["targets_selected"] == 0)
    answered = {c["decision_unit"] for c in C if c["cio_decision"]}
    check("★★ 답란이 있는 카드는 CIO 판정 목록과 정확히 일치한다",
          answered == set(DC.CIO_DECISIONS), str(sorted(answered)))
    check("★★ 그 외 카드의 답란은 전부 비어 있다",
          all(c["cio_decision"] is None for c in C
              if c["decision_unit"] not in DC.CIO_DECISIONS))
    check("★★ 판정 수와 미판정 수의 합이 카드 수와 같다",
          CARDS["counts"]["answered_cards"] + CARDS["counts"]["open_cards"] == len(C))
    check("★★ 모든 판정에 작성자와 출처가 붙어 있다",
          all(c["cio_decision"]["decided_by"] == "CIO" and c["cio_decision"]["source"]
              for c in C if c["cio_decision"]))
    check("★★ 측정 대상 판정에 선언되지 않은 수치가 0건",
          all(set(DC.scan_numeric_tokens(c["cio_decision"]["decision"]))
              <= {d["token"] for d in c["cio_decision"].get("declared_numerics", [])}
              for c in C if c["cio_decision"]
              and c["decision_unit"].endswith("::data_source")))
    # ★★ 자릿수 분해 구멍 — '2026' 이 이미 선언된 한 자리들로 통과되면 안 된다
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0015::threshold"]["decision"] += " 기준연도는 2026 으로 한다."
    check("★★ [음성] 여러 자리 숫자가 선언된 한 자리로 통과되지 않는다",
          any("2026" in e and "선언되지 않은 숫자" in e for e in rebuild(decisions=d)))
    check("★★ 모든 판정이 수치 도입 여부를 명시적으로 선언한다",
          all(isinstance(c["cio_decision"].get("numeric_threshold_introduced"), bool)
              for c in C if c["cio_decision"]))
    # ★★ decision 한 필드가 아니라 판정 기록 전체를 훑는다
    check("★★ 판정 기록 전체의 숫자가 토큰 단위로 선언돼 있다",
          all(set(DC.scan_numeric_tokens(c["cio_decision"]))
              <= {d["token"] for d in c["cio_decision"].get("declared_numerics", [])}
              for c in C if c["cio_decision"]),
          str([(c["decision_unit"],
                sorted(set(DC.scan_numeric_tokens(c["cio_decision"]))
                       - {d["token"] for d in c["cio_decision"].get("declared_numerics", [])}))
               for c in C if c["cio_decision"]
               and set(DC.scan_numeric_tokens(c["cio_decision"]))
               - {d["token"] for d in c["cio_decision"].get("declared_numerics", [])}][:3]))
    # ★★ 회귀와 validator 가 같은 숫자 언어를 쓰는가 — specification drift 차단
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    own = re.findall(r"re\.(?:findall|search|match|fullmatch)\(r?\"[^\"]*\\\\d",
                     src)
    check("★★ 회귀가 자체 숫자 정규식을 쓰지 않는다 — production lexer 만 쓴다",
          not own, str(own[:3]))
    for probe in ("전각 １２ 와 ASCII 12", "١٢٣", "10%p", "RULE-0004", "45%cc"):
        check(f"★ 같은 입력에 같은 토큰: {probe[:12]}",
              DC.scan_numeric_tokens(probe) == DC.scan_numeric_tokens({"x": probe}),
              str(DC.scan_numeric_tokens(probe)))
    check("★ 현재 lexer 의 유니코드 처리 (D-4 로 기록된 동작)",
          isinstance(DC.scan_numeric_tokens("１２"), list),
          str(DC.scan_numeric_tokens("１２")))
    check("★★ 스캔 제외 키는 출처와 선언 블록 둘뿐이다",
          DC.NUMERIC_SCAN_SKIP == {"source", "declared_numerics"},
          str(sorted(DC.NUMERIC_SCAN_SKIP)))
    check("★★ 판정 일자는 스캔에서 빠진다 — 그래서 출처는 선언이 필요 없다",
          not DC.scan_numeric_tokens({"source": "CIO 판정 2026-08-15 · 카드 1"}))

    # ★★ [음성] 산문 필드 어디에 숫자를 숨겨도 잡힌다
    for field, payload in (("rationale", " 하단이 10% 이상 낮아진 경우로 본다."),
                           ("boundaries", "하락 폭은 10% 이상이어야 한다."),
                           ("excluded_from_definition", " 기준은 10% 로 둔다.")):
        d = copy.deepcopy(DC.CIO_DECISIONS)
        rec = d["RULE-0004::threshold"] if field != "excluded_from_definition" \
            else d["RULE-0004::data_source"]
        if field == "boundaries":
            rec["boundaries"].append(payload)
        elif field == "excluded_from_definition":
            rec[field] = rec.get(field, "") + payload
        else:
            rec[field] += payload
        check(f"★★ [음성] {field} 에 숨긴 수치가 잡힌다",
              any("선언되지 않은 숫자" in e for e in rebuild(decisions=d)))
    # 새로 생긴 산문 필드도 자동으로 검사 대상이 된다
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0004::threshold"]["implementation_note"] = "실무상 10% 를 쓴다."
    check("★★ [음성] 나중에 추가되는 산문 필드도 자동으로 훑는다",
          any("선언되지 않은 숫자" in e for e in rebuild(decisions=d)))
    # 중첩 구조 안에 숨겨도 잡힌다
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0004::threshold"]["notes"] = {"detail": ["여유 폭 10% 를 둔다."]}
    check("★★ [음성] 중첩된 구조 안에 숨겨도 잡힌다",
          any("선언되지 않은 숫자" in e for e in rebuild(decisions=d)))
    check("★★ 선언된 숫자 역할이 닫힌 집합 안에 있다",
          all((d["role"] in DC.DECLARED_NUMERIC_ROLES or d["role"] == DC.THRESHOLD_ROLE)
              and d.get("why")
              for c in C if c["cio_decision"]
              for d in c["cio_decision"].get("declared_numerics", [])))
    check("★★ 임계값 역할은 도입을 선언한 카드에만 있다",
          all(c["cio_decision"]["numeric_threshold_introduced"] is True
              for c in C if c["cio_decision"]
              and any(d["role"] == DC.THRESHOLD_ROLE
                      for d in c["cio_decision"].get("declared_numerics", []))))
    check("★★ 도입을 선언한 카드에는 실제 임계값 숫자가 있다",
          all(any(d["role"] == DC.THRESHOLD_ROLE
                  for d in c["cio_decision"].get("declared_numerics", []))
              for c in C if c["cio_decision"]
              and c["cio_decision"]["numeric_threshold_introduced"] is True))
    check("★ 지금까지 임계값을 도입한 카드 7건 · 임계값 숫자 7개",
          CARDS["numeric_thresholds_introduced"] == 7
          and CARDS["declared_threshold_numerics"] == 7,
          f"{CARDS['numeric_thresholds_introduced']}/"
          f"{CARDS['declared_threshold_numerics']}")
    check("★★ 임계값은 전부 어느 카드에서 왔는지 추적된다",
          all(c["cio_decision"]["source"] for c in C if c["cio_decision"]
              and any(d["role"] == DC.THRESHOLD_ROLE
                      for d in c["cio_decision"].get("declared_numerics", []))))
    check("★★ 답을 담을 수 있는 다른 필드가 없다",
          not any(k in c for c in C
                  for k in ("decided_value", "recommended", "proposed_definition",
                            "candidate_values", "default")))

    ct = {r["canonical_rule_id"]: r["condition_text"] for r in CANON["canonical_rules"]}
    bad = []
    for c in C:
        allow_n, allow_l = set(), set()
        for r in c["affected_rules"]:
            allow_n |= set(DC.scan_numeric_tokens(ct[r]))
            allow_l |= {t.lower() for t in re.findall(r"[A-Za-z]{2,}", ct[r])}
        prose = [c["investment_intent"], c["implementation_consequence"]] + \
                [e["text"] for e in c["observable_evidence"]
                 if e["origin"] == "atlas_artifact"]
        if c["semantic_obsolescence_risk"]:
            prose.append(c["semantic_obsolescence_risk"])
        for t in prose:
            bad += [(c["decision_unit"], x) for x in re.findall(r"[A-Za-z]{2,}", t)
                    if x.lower() not in allow_l and not re.fullmatch(r"(RULE|CIO)", x)]
    check("★★ 산문에 원문 밖 대상명 0건", not bad, str(bad[:4]))

    ev = copy.deepcopy(DC.EVIDENCE)
    ev["RULE-0025"] = [DC.A("연속은 3거래일로 본다.")]
    check("★★ [음성] 산문에 값을 적으면 잡힌다",
          any("값을 만든 것" in e for e in rebuild(evidence=ev)))

    # ★★ 답란이 내 손으로 채워지는 경로를 전부 막았는가
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0025::time_window"] = {"decided_by": "Claude", "source": "추론",
                                   "decision": "연속은 사흘로 본다."}
    check("★★ [음성] 내가 작성자로 들어가면 잡힌다",
          any("내가 대신 결정한 것이다" in e for e in rebuild(decisions=d)))
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0025::time_window"] = {"decided_by": "CIO", "source": "",
                                   "decision": "연속은 사흘로 본다."}
    check("★★ [음성] 출처 없는 판정은 잡힌다",
          any("CIO 판정에 출처가 없다" in e for e in rebuild(decisions=d)))
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0099::data_source"] = copy.deepcopy(d["RULE-0004::data_source"])
    check("★★ [음성] 카드에 없는 단위의 판정은 잡힌다",
          any("카드에 없는 결정 단위" in e for e in rebuild(decisions=d)))
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0004::data_source"]["decision"] += " 연간 예산 규모는 400 수준이다."
    check("★★ [음성] 측정 대상 판정에 선언 없는 수치를 넣으면 잡힌다",
          any("data_source 판정에 선언되지 않은 숫자" in e for e in rebuild(decisions=d)))
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0004::threshold"]["decision"] += " 하단이 10% 이상 낮아진 경우로 한다."
    check("★★ [음성] 수치 없음이라 선언하고 임계값을 넣으면 잡힌다",
          any("선언되지 않은 숫자" in e for e in rebuild(decisions=d)))
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0015::data_source"]["decision"] += " 하향 폭은 7% 로 본다."
    check("★★ [음성] 선언된 숫자가 있어도 새 숫자는 따로 잡힌다",
          any("7" in e and "선언되지 않은 숫자" in e for e in rebuild(decisions=d)))
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0015::data_source"]["declared_numerics"][0]["role"] = DC.THRESHOLD_ROLE
    check("★★ [음성] 도입하지 않았다면서 임계값 역할을 쓰면 잡힌다",
          any("임계값을 도입하지 않았다고 선언했다" in e for e in rebuild(decisions=d)))
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0015::data_source"]["declared_numerics"][0]["role"] = "measurement_scale"
    check("★★ [음성] 집합 밖 역할을 만들면 잡힌다",
          any("허용되지 않은 숫자 역할" in e for e in rebuild(decisions=d)))
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["G1::time_window"]["declared_numerics"][0]["role"] = "card_reference"
    check("★★ [음성] 임계값을 비임계 역할로 위장하면 잡힌다",
          any("임계값 역할의 숫자가 하나도 없다" in e for e in rebuild(decisions=d)))
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0015::data_source"]["declared_numerics"][0]["why"] = ""
    check("★★ [음성] 역할 근거 없는 숫자 선언은 잡힌다",
          any("역할 근거가 없다" in e for e in rebuild(decisions=d)))
    d = copy.deepcopy(DC.CIO_DECISIONS)
    del d["RULE-0004::threshold"]["numeric_threshold_introduced"]
    check("★★ [음성] 수치 도입 여부를 선언하지 않으면 잡힌다",
          any("선언하지 않았다" in e for e in rebuild(decisions=d)))


def test_quoted_baseline_vs_new_threshold():
    print("\n[K-12] 원문에 있던 값과 새로 만든 값이 섞이지 않는다")
    by = {c["decision_unit"]: c for c in C}
    d17 = by["RULE-0021::event_definition"]["cio_decision"]
    roles = {x["token"]: x["role"] for x in d17["declared_numerics"]}
    ct = by["RULE-0021::event_definition"]["original_condition"]["RULE-0021"]
    check("★★ 카드 17 에 판정이 기록됐다", d17 and d17["decided_by"] == "CIO")
    check("★★ 기준선은 원문 인용으로, 하회 폭은 임계값으로 분리 선언됐다",
          roles == {"45": "quoted_phrase", "3": DC.THRESHOLD_ROLE}, str(roles))
    check("★★ 인용값은 실제로 원문에 있다", "45" in DC.scan_numeric_tokens(ct))
    check("★★ 새 임계값은 원문에 없다", "3" not in DC.scan_numeric_tokens(ct))
    check("★★ 그 사실이 근거에 적혀 있다",
          any(x["role"] == DC.THRESHOLD_ROLE and "원문에서 유도되지 않았고" in x["why"]
              for x in d17["declared_numerics"]))
    check("★★ 계산으로 나오는 경계값을 기록하지 않았다 — 수치를 하나 더 만들지 않는다",
          "42" not in DC.scan_numeric_tokens(d17["decision"])
          and any("발동 경계값 자체를 판정 기록에 적지 않는다" in b
                  for b in d17["boundaries"]))
    check("★★ 수집기 부재를 정의 변경 사유로 쓰지 않았다",
          any("정의를 바꾸지 않는다" in b and "평가 불가 상태를 유지" in b
              for b in d17["boundaries"]))
    check("★★ 이 카드는 event_definition 만 닫았다 — data_source 는 여전히 결핍",
          "data_source" in json.load(
              open(os.path.join(_ROOT, "rules", "definition_inventory.json"),
                   encoding="utf-8"))["items"][0].get("missing_components", [])
          or "data_source" in next(
              i["missing_components"] for i in json.load(
                  open(os.path.join(_ROOT, "rules", "definition_inventory.json"),
                       encoding="utf-8"))["items"]
              if i["canonical_rule_id"] == "RULE-0021"))


def test_rule0022_graph():
    print("\n[K-13] RULE-0022 — 필요한 만큼만 의존시킨다")
    by = {c["decision_unit"]: c for c in C}
    def prs(c):
        pr = c["prerequisite"]
        return [pr] if isinstance(pr, dict) else (pr or [])
    G = {u: {e["unit"] for e in es} for u, es in DC.PREREQ_GRAPH.items()}

    check("★★ data_source 카드를 만들지 않았다 — capability 축은 판정 대상이 아니다",
          "RULE-0022::data_source" not in {c["decision_unit"] for c in C})
    check("★★ 이 규칙의 카드는 셋뿐이다",
          {c["decision_unit"] for c in C if c["decision_unit"].startswith("RULE-0022")}
          == {"RULE-0022::time_window", "RULE-0022::comparison_baseline",
              "RULE-0022::threshold"})
    check("★★ 폭은 기간·기준 두 곳에 직접 걸린다",
          G.get("RULE-0022::threshold")
          == {"RULE-0022::time_window", "RULE-0022::comparison_baseline"},
          str(G.get("RULE-0022::threshold")))
    check("★★ 기간과 기준 사이에는 간선이 없다 — 필요 이상으로 묶지 않았다",
          "RULE-0022::time_window" not in G
          and "RULE-0022::comparison_baseline" not in G)
    check("★★ 선행 두 장이 폭보다 앞 순번이다",
          all(by[u]["order"] < by["RULE-0022::threshold"]["order"]
              for u in G["RULE-0022::threshold"]))
    check("★★ 두 선행이 모두 충족돼 폭이 열렸다",
          {p["unit"]: p["status"] for p in prs(by["RULE-0022::threshold"])}
          == {"RULE-0022::time_window": "충족됨",
              "RULE-0022::comparison_baseline": "충족됨"}
          and by["RULE-0022::threshold"]["blocked_by"] is None)
    # ★★ 카드 20 — 폭만 닫고 앞 두 결정을 다시 열지 않았다
    d20 = by["RULE-0022::threshold"]["cio_decision"]
    check("★★ 카드 20 에 판정이 기록됐다", d20 and d20["decided_by"] == "CIO")
    check("★★ 여섯 번째 임계값으로 선언됐다",
          d20["numeric_threshold_introduced"] is True
          and any(x["role"] == DC.THRESHOLD_ROLE for x in d20["declared_numerics"]))
    check("★★ 퍼센트와 퍼센트포인트를 구분해 적었다", "10%p" in d20["decision"]
          and "10% 가 아니라" in d20["decision"])
    check("★★ 앞 카드의 값을 이 기록에 복제하지 않았다 — 카드 번호로만 참조한다",
          {x["token"] for x in d20["declared_numerics"] if x["role"] == "card_reference"}
          == {"18", "19"}
          and "2 개 연속" not in d20["decision"])
    check("★★ 사후 최적화 금지를 근거와 선언 양쪽에 남겼다",
          "사후 최적화하지 않는다" in d20["rationale"]
          and any(x["role"] == DC.THRESHOLD_ROLE and "사후 최적화 대상이 아니다" in x["why"]
                  for x in d20["declared_numerics"]))
    check("★★ 앞 두 결정을 다시 열지 않았다",
          any("기간과 비교 기준을 다시 열지 않는다" in b for b in d20["boundaries"])
          and "카드 18" in by["RULE-0022::time_window"]["cio_decision"]["source"]
          and "카드 19" in by["RULE-0022::comparison_baseline"]["cio_decision"]["source"])
    check("★★ 정의가 채워져도 평가 가능은 아니라고 적는다",
          any("곧바로 평가 가능한" in b for b in d20["boundaries"]))
    check("★★ 설명용 예시 수치를 기록에 남기지 않았다",
          set(DC.scan_numeric_tokens(d20["decision"]))
          <= {x["token"] for x in d20["declared_numerics"]})
    check("★★ 선행 두 장 자신은 대기가 아니다",
          not by["RULE-0022::time_window"]["blocked_by"]
          and not by["RULE-0022::comparison_baseline"]["blocked_by"])
    # ★★ 카드 19 — 비교 기준만 닫고 폭을 선취하지 않았다
    d19 = by["RULE-0022::comparison_baseline"]["cio_decision"]
    check("★★ 카드 19 에 판정이 기록됐다", d19 and d19["decided_by"] == "CIO")
    check("★★ 잔액이 아니라 성장률을 비교한다",
          any("잔액 자체가 아니라 성장률이다" in b for b in d19["boundaries"]))
    check("★★ 직전 기간이 아니라 전년동기다",
          any("직전 보고기간 성장률이 아니라 전년동기" in b for b in d19["boundaries"]))
    check("★★ 계열이 끊기면 보정하지 않고 판정 불가로 둔다",
          any("보정하지 않고 판정 불가로 둔다" in b for b in d19["boundaries"]))
    check("★★ 카드 19 가 폭을 선취하지 않았다 — 폭은 카드 20 이 정했다",
          d19["numeric_threshold_introduced"] is False
          and any("감소폭은 정하지 않는다" in b for b in d19["boundaries"])
          and "카드 20" in by["RULE-0022::threshold"]["cio_decision"]["source"])
    # ★★ 카드 18 — 기간만 닫고 뒤 두 축을 선취하지 않았다
    d18 = by["RULE-0022::time_window"]["cio_decision"]
    check("★★ 카드 18 에 판정이 기록됐다", d18 and d18["decided_by"] == "CIO")
    check("★★ 보고기간으로 센다 — 달력일로 환산하지 않는다",
          any("달력일이나 임의의 일수로 환산하지 않는다" in b for b in d18["boundaries"]))
    check("★★ 관측이 생성되지 않은 기간은 횟수를 늘리지 않는다",
          any("횟수를 증가시키지 않는다" in b for b in d18["boundaries"]))
    check("★★ 카드 18 이 비교 기준과 폭을 선취하지 않았다",
          any("전년동기 · 직전 기간 · 특정 기준선이나 감소폭을 정하지 않는다" in b
              for b in d18["boundaries"])
          and "카드 19" in by["RULE-0022::comparison_baseline"]["cio_decision"]["source"]
          and "카드 20" in by["RULE-0022::threshold"]["cio_decision"]["source"])
    check("★★ 세 축이 각각 다른 카드에서 왔다 — 한 카드가 다 정하지 않았다",
          len({by[u]["cio_decision"]["source"] for u in
               ("RULE-0022::time_window", "RULE-0022::comparison_baseline",
                "RULE-0022::threshold")}) == 3)
    check("★★ 새 임계값임을 선언했다",
          d18["numeric_threshold_introduced"] is True
          and any(x["token"] == "2" and x["role"] == DC.THRESHOLD_ROLE
                  and "복사하지도 않았다" in x["why"]
                  for x in d18["declared_numerics"]))

    # ★★ 구현된 그래프가 CIO 판정 형태와 대조된다 — 계약이 코드 안에 있다
    check("★★ 판정된 의존 형태가 따로 기록돼 있다",
          {u: w for u, (w, _) in DC.CIO_FIXED_GRAPH.items()}.get("RULE-0022::threshold")
          == {"RULE-0022::time_window", "RULE-0022::comparison_baseline"})
    check("★★ 그 기록에 출처가 붙어 있다",
          all(src for _, src in DC.CIO_FIXED_GRAPH.values()))

    # ★★ [음성] 직접 간선을 하나라도 빼면 build 가 거부한다
    for drop in ("RULE-0022::time_window", "RULE-0022::comparison_baseline"):
        bad = copy.deepcopy(DC.PREREQ_GRAPH)
        bad["RULE-0022::threshold"] = [e for e in bad["RULE-0022::threshold"]
                                       if e["unit"] != drop]
        errs = rebuild(graph=bad)
        check(f"★★ [음성] {drop.split('::')[1]} 간선을 빼면 build 가 거부한다",
              any("판정된 의존 형태와 다르다" in e and "RULE-0022::threshold" in e
                  for e in errs), str(errs[:1]))

    # ★★ [음성] 두 선행 사이에 없던 간선을 넣으면 build 가 거부한다
    bad = copy.deepcopy(DC.PREREQ_GRAPH)
    bad["RULE-0022::comparison_baseline"] = [
        {"unit": "RULE-0022::time_window", "reason": "임의로 넣은 간선"}]
    errs = rebuild(graph=bad)
    check("★★ [음성] 두 선행 사이에 간선을 넣으면 build 가 거부한다",
          any("판정된 의존 형태와 다르다" in e
              and "RULE-0022::comparison_baseline" in e for e in errs),
          str(errs[:1]))

    # ★★ [음성] 판정되지 않은 규칙에 간선을 만들면 잡힌다
    bad = copy.deepcopy(DC.PREREQ_GRAPH)
    bad["RULE-0011::observation_frequency"] = [
        {"unit": "RULE-0022::time_window", "reason": "판정된 적 없는 간선"}]
    errs = rebuild(graph=bad)
    check("★★ [음성] 판정되지 않은 의존 간선은 잡힌다",
          any("판정되지 않은 의존 간선" in e for e in errs), str(errs[:1]))

    # ★★ [음성] 같은 숫자를 두 역할로 선언하면 잡힌다 — provenance 가 겹치는 경로
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0021::event_definition"]["declared_numerics"].append(
        {"token": "45", "role": DC.THRESHOLD_ROLE, "why": "중복 선언"})
    check("★★ [음성] 원문 인용 숫자를 임계값으로도 이중 선언하면 잡힌다",
          any("두 번 선언됐다" in e for e in rebuild(decisions=d)))
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0022::threshold"]["declared_numerics"].append(
        {"token": "10", "role": "quoted_phrase", "why": "중복 선언"})
    check("★★ [음성] 임계값 숫자를 인용으로도 이중 선언하면 잡힌다",
          any("두 번 선언됐다" in e for e in rebuild(decisions=d)))

    # ★★ [음성] 선행 그래프에 순환을 넣으면 잡힌다
    cyc = copy.deepcopy(DC.PREREQ_GRAPH)
    cyc["RULE-0022::time_window"] = [
        {"unit": "RULE-0022::threshold", "reason": "역방향 간선"}]
    fixed = dict(DC.CIO_FIXED_GRAPH)
    fixed["RULE-0022::time_window"] = ({"RULE-0022::threshold"}, "TEST FIXTURE")
    errs = rebuild(graph=cyc, fixed_graph=fixed)
    check("★★ [음성] 선행 그래프에 순환을 만들면 build 가 거부한다",
          any("순환이 있다" in e for e in errs), str(errs[:2]))
    check("★★ [음성] 순환이면 순번 검사에도 걸린다",
          any("뒤에 온다" in e for e in errs))

    # ★★ 부분 충족으로는 전이되지 않고, 둘 다 채워야 풀린다
    # ⚠ TEST FIXTURE ONLY — 실제 CIO 판정이 아니다. 정본에 들어가지 않는다.
    #   여기서 검증하는 것은 판정의 내용·provenance 가 아니라, 선행 판정의 존재 여부에
    #   따른 prerequisite 상태 전이뿐이다. 임시 경로로만 빌드하며 정본은 건드리지 않는다.
    #   ⛔ decided_by 를 "CIO" 로 두는 이유는 그 자체가 빌더의 요구 조건이기 때문이며,
    #      이 fixture 를 판정 기록의 예시로 삼아서는 안 된다.
    stub = {"decided_by": "CIO", "source": "TEST FIXTURE — 실제 판정 아님",
            "decision": "상태 전이 검증용 더미", "numeric_threshold_introduced": False,
            "declared_numerics": []}
    # ★ fixture 를 현재 판정 상태에 기대지 않는다 — 두 선행을 먼저 비우고 하나만 채운다.
    #   그래야 나중에 두 카드가 실제로 판정돼도 이 음성 테스트가 헛돌지 않는다.
    base = {k: v for k, v in copy.deepcopy(DC.CIO_DECISIONS).items()
            if k not in ("RULE-0022::time_window", "RULE-0022::comparison_baseline")}
    none_yet = rebuild(decisions=base, full=True)[0]
    th0 = next(c for c in none_yet["cards"]
               if c["decision_unit"] == "RULE-0022::threshold")
    check("★★ 둘 다 비우면 폭은 두 선행 모두를 대기한다",
          {p["status"] for p in th0["prerequisite"]} == {"대기"}
          and all(u in th0["blocked_by"] for u in
                  ("RULE-0022::time_window", "RULE-0022::comparison_baseline")))
    half = copy.deepcopy(base)
    half["RULE-0022::time_window"] = copy.deepcopy(stub)
    p = rebuild(decisions=half, full=True)[0]
    th = next(c for c in p["cards"] if c["decision_unit"] == "RULE-0022::threshold")
    check("★★ 하나만 채우면 폭은 여전히 대기다",
          th["blocked_by"] and "RULE-0022::comparison_baseline" in th["blocked_by"]
          and "RULE-0022::time_window" not in th["blocked_by"])
    both = copy.deepcopy(half)
    both["RULE-0022::comparison_baseline"] = copy.deepcopy(stub)
    p = rebuild(decisions=both, full=True)[0]
    th = next(c for c in p["cards"] if c["decision_unit"] == "RULE-0022::threshold")
    check("★★ 둘 다 채우면 폭이 판정 가능 상태로 전이한다",
          th["blocked_by"] is None
          and all(x["status"] == "충족됨" for x in th["prerequisite"]))


def test_observation_frequency():
    print("\n[K-14] 관측 주기 — 달력 주기를 새로 만들지 않았다")
    by = {c["decision_unit"]: c for c in C}
    d21 = by["RULE-0011::observation_frequency"]["cio_decision"]
    ct = by["RULE-0011::observation_frequency"]["original_condition"]["RULE-0011"]
    check("★★ 카드 21 에 판정이 기록됐다", d21 and d21["decided_by"] == "CIO")
    check("★★ 새 임계값을 도입하지 않았다",
          d21["numeric_threshold_introduced"] is False
          and not any(x["role"] == DC.THRESHOLD_ROLE for x in d21["declared_numerics"]))
    check("★★ 원문 숫자는 인용으로만 선언됐다",
          {x["token"]: x["role"] for x in d21["declared_numerics"]}
          == {"10": "quoted_phrase", "3": "quoted_phrase"},
          str({x["token"]: x["role"] for x in d21["declared_numerics"]}))
    check("★★ 그 숫자가 실제로 원문에 있다",
          {"10", "3"} <= set(DC.scan_numeric_tokens(ct)), ct)
    check("★★ 판정문 자체에는 숫자가 없다 — 주기를 수치로 고정하지 않았다",
          not DC.scan_numeric_tokens(d21["decision"]))
    check("★★ 달력 주기로 고정하지 않는다고 명시한다",
          any("임의의 달력 주기로 고정하지 않는다" in b for b in d21["boundaries"])
          and "분기마다 본다' 가 아니라" in d21["decision"])
    check("★★ 공시 사이를 보간해 관측을 만들지 않는다",
          any("보간하여 새로운 관측을 만들지 않는다" in b for b in d21["boundaries"]))
    check("★★ 새 정보가 없으면 같은 값을 다시 세지 않는다",
          any("새 관측으로 다시 세지 않는다" in b for b in d21["boundaries"]))
    check("★★ 원문 조건을 다시 정의하지 않았다",
          any("비율이나 고객 수 기준을 다시 정의하지 않는다" in b
              for b in d21["boundaries"]))
    check("★★ 지속성 조건을 함께 만들지 않았다",
          any("지속성 조건이나 추가 발동 조건을 만들지 않는다" in b
              for b in d21["boundaries"]))
    check("★★ 이 규칙의 카드는 하나뿐이다 — data_source 카드를 만들지 않았다",
          [c["decision_unit"] for c in C
           if c["decision_unit"].startswith("RULE-0011")]
          == ["RULE-0011::observation_frequency"])


class _Tripwire:
    """★ 계약: build() 는 실행 '중에도' 전역을 변형하지 않는다.
    최종 상태만 비교하면 '변형했다가 되돌리는' 경로가 통과한다. 그 경로는
    예외로 중단될 때 전역을 오염된 채로 남기므로, 여기서는 변형 시점에 잡는다."""
    #   ★ in-place 연산자까지 포함한다. `*=` · `&=` · `^=` · `-=` 도 변형 경로다.
    _MUTATORS = ("__setitem__", "__delitem__",
                 "update", "pop", "popitem", "clear", "setdefault",
                 "append", "extend", "insert", "remove", "sort", "reverse",
                 "add", "discard",
                 "__iadd__", "__imul__",
                 "__ior__", "__iand__", "__ixor__", "__isub__")

    @staticmethod
    def wrap(obj, name, log):
        base = type(obj)

        def blow(mname):
            def _f(self, *a, **k):
                log.append(f"{name}.{mname}()")
                raise AssertionError(f"build() 가 전역 {name} 을 실행 중 변형했다 "
                                     f"— {mname}()")
            return _f

        ns = {m: blow(m) for m in _Tripwire._MUTATORS if hasattr(base, m)}
        return type(f"Tripwire_{base.__name__}", (base,), ns)(obj)


def test_build_purity():
    print("\n[K-15] ★★ build() 가 전역 상태를 건드리지 않는다 (실행 중 포함)")
    import copy as _c
    MUT = (dict, list, set, bytearray)
    names = [n for n in dir(DC) if not n.startswith("__")
             and isinstance(getattr(DC, n), MUT)]
    before = {n: _c.deepcopy(getattr(DC, n)) for n in names}
    ids = {n: id(getattr(DC, n)) for n in names}

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        tmp = f.name
    os.unlink(tmp)
    try:
        DC.build(out_path=tmp)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    changed = [n for n in names if getattr(DC, n) != before[n]]
    rebound = [n for n in names if id(getattr(DC, n)) != ids[n]]
    check("★★ build() 가 전역의 내용을 바꾸지 않는다", not changed, str(changed))
    check("★★ build() 가 전역을 재바인딩하지 않는다", not rebound, str(rebound))
    # ★ rebuild() 가 복구하지 않는 전역을 build 가 건드리면 테스트 간 오염이 된다
    restored = set(RESTORED_GLOBALS)          # ★ rebuild() 와 같은 출처에서 온다
    check("★★ 복구 대상 밖 전역을 건드리지 않는다 — rebuild() 의 숨은 전제가 성립한다",
          not (set(changed + rebound) - restored),
          str(sorted(set(changed + rebound) - restored)))
    check("★★ 복구 목록이 실제 전역과 일치한다 — 이름이 사라져도 잡힌다",
          restored <= set(names), str(sorted(restored - set(names))))
    check("★ 검사 대상 전역이 실제로 존재한다", len(names) >= 10, str(len(names)))

    # ★★ 실행 '중' 변형까지 막는가 — 되돌리는 경로도 통과시키지 않는다
    log, orig = [], {n: getattr(DC, n) for n in names}
    try:
        for n in names:
            try:
                setattr(DC, n, _Tripwire.wrap(orig[n], n, log))
            except TypeError:                     # 상속 불가 타입은 건너뛴다
                pass
        wrapped = sum(1 for n in names
                      if type(getattr(DC, n)).__name__.startswith("Tripwire_"))
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            tmp2 = f.name
        os.unlink(tmp2)
        try:
            DC.build(out_path=tmp2)
            tripped = False
        except AssertionError:
            tripped = True
        finally:
            if os.path.exists(tmp2):
                os.unlink(tmp2)
    finally:
        for n in names:
            setattr(DC, n, orig[n])
    check("★ 감시 대상 전역을 실제로 감쌌다", wrapped >= 10, str(wrapped))
    check("★★ 실행 중에도 전역을 변형하지 않는다 — 변형 후 복구 경로도 없다",
          not tripped, str(log[:3]))
    check("★★ 감시 해제 후 전역이 원래 객체로 돌아왔다",
          all(getattr(DC, n) is orig[n] for n in names))
    # ★★ [음성] 감시망이 실제로 작동하는가 — 변형 경로별로 확인한다
    def _blocked(obj, op):
        log2 = []
        w = _Tripwire.wrap(obj, "PROBE", log2)
        try:
            op(w)
            return False
        except AssertionError:
            return bool(log2)

    def _iop(f):
        """in-place 연산자는 결과를 재대입하므로 함수로 감싼다."""
        return f

    cases = [
        ("dict 항목 대입", {"a": 1}, lambda w: w.__setitem__("b", 2)),
        ("dict update", {"a": 1}, lambda w: w.update({"b": 2})),
        ("dict |=", {"a": 1}, _iop(lambda w: w.__ior__({"b": 2}))),
        ("list append", [1], lambda w: w.append(2)),
        ("list +=", [1], _iop(lambda w: w.__iadd__([2]))),
        ("list *=", [1], _iop(lambda w: w.__imul__(2))),
        ("set add", {1}, lambda w: w.add(2)),
        ("set |=", {1}, _iop(lambda w: w.__ior__({2}))),
        ("set &=", {1}, _iop(lambda w: w.__iand__({1}))),
        ("set ^=", {1}, _iop(lambda w: w.__ixor__({2}))),
        ("set -=", {1}, _iop(lambda w: w.__isub__({1}))),
    ]
    missed = [n for n, o, f in cases if not _blocked(o, f)]
    check("★★ [음성] 감시망이 모든 변형 경로를 잡는다 — in-place 연산자 포함",
          not missed, str(missed))


def test_rule0017_structure():
    print("\n[K-16] RULE-0017 — 결합 의미가 사건 정의보다 앞선다")
    by = {c["decision_unit"]: c for c in C}
    G = {u: {e["unit"] for e in es} for u, es in DC.PREREQ_GRAPH.items()}
    fixed = {u: w for u, (w, _) in DC.CIO_FIXED_GRAPH.items()}
    units = [c["decision_unit"] for c in C
             if c["decision_unit"].startswith("RULE-0017")]
    check("★★ 카드 넷이고 의미 → 사건 → 폭 → 원천 순서다",
          units == ["RULE-0017::semantic_scope", "RULE-0017::event_definition",
                    "RULE-0017::threshold", "RULE-0017::data_source"], str(units))
    check("★★ 사건 정의가 결합 의미에 직접 의존한다",
          G.get("RULE-0017::event_definition") == {"RULE-0017::semantic_scope"},
          str(G.get("RULE-0017::event_definition")))
    check("★★ 관측 경로를 사건 정의의 선행으로 만들지 않았다",
          "RULE-0017::data_source" not in G.get("RULE-0017::event_definition", set())
          and fixed.get("RULE-0017::data_source") == set())
    check("★★ 폭에도 이번 턴 새 간선을 만들지 않았다",
          fixed.get("RULE-0017::threshold") == set()
          and "RULE-0017::threshold" not in G)
    check("★★ 결합 의미가 풀려 사건 정의의 대기가 해제됐다",
          not by["RULE-0017::event_definition"]["blocked_by"]
          and by["RULE-0017::event_definition"]["prerequisite"][0]["status"] == "충족됨")
    # ★★ 카드 25 — 관측 경로 없음으로 닫았고 proxy 를 만들지 않았다
    d25 = by["RULE-0017::data_source"]["cio_decision"]
    check("★★ 네 칸이 모두 판정됐다", all(by[u]["cio_decision"] for u in units))
    check("★★ 관측 경로 없음으로 닫았다",
          "현재 평가 가능한 공개 관측 경로 없음" in d25["decision"])
    check("★★ '공개자료가 없다' 와 구별해 적었다",
          "「공개자료가 없다」 가 아니라" in d25["decision"])
    check("★★ 대체값을 만들지 않았다 — 다섯 가지 proxy 를 명시적으로 배제한다",
          sum(1 for b in d25["boundaries"] if "대체값으로 쓰지 않는다" in b) == 5,
          str(sum(1 for b in d25["boundaries"] if "대체값으로 쓰지 않는다" in b)))
    check("★★ 계약 구조 자체를 원천으로 채택하지 않았다",
          any("계약 구조 자체를 관측 원천으로 채택하지 않는다" in b
              for b in d25["boundaries"]))
    check("★★ 없으면 추정하지 않고 평가 불가로 둔다",
          any("추정하지 않고 평가 불가로 둔다" in b for b in d25["boundaries"]))
    check("★★ 나중에 관측이 가능해져도 정의를 바꾸지 않는다고 적는다",
          any("정의를 바꾸지 않고 데이터 확보 가능성만 재검토한다" in b
              for b in d25["boundaries"]))
    check("★★ 실패가 아니라 정상 종착 상태라고 기록한다",
          any("규칙의 실패도 미완성도 아니다" in b for b in d25["boundaries"]))
    check("★★ 이 판정에 숫자를 만들지 않았다",
          d25["numeric_threshold_introduced"] is False
          and d25["declared_numerics"] == []
          and not DC.scan_numeric_tokens(d25["decision"]))
    check("★★ 정의는 완결됐으나 evaluator 연결은 아니다",
          all("evaluator 연결은 여전히 금지" in by[u]["execution_status"]
              for u in units)
          and CARDS["consumable_by_evaluator"] is False)
    # ★★ 카드 24 — 측정축은 정하고 관측 원천은 열어 뒀다
    d24 = by["RULE-0017::threshold"]["cio_decision"]
    check("★★ 카드 24 에 판정이 기록됐다", d24 and d24["decided_by"] == "CIO")
    check("★★ 일곱 번째 임계값으로 선언됐다",
          d24["numeric_threshold_introduced"] is True
          and any(x["token"] == "10" and x["role"] == DC.THRESHOLD_ROLE
                  for x in d24["declared_numerics"]))
    check("★★ 분모가 물량이다 — 금액·가격으로 계산하지 않는다",
          any("금액 · 매출 · 가격 변화로 이 비율을 계산하지 않는다" in b
              for b in d24["boundaries"]))
    check("★★ 계약 명칭이 계산 조건이 아니다",
          any("계약 명칭은 이 계산의 조건이 아니다" in b for b in d24["boundaries"]))
    check("★★ 관측 원천을 열어 뒀다고 명시한다",
          any("카드 25 는 그대로 열어 둔다" in b for b in d24["boundaries"]))
    check("★★ ★ 원천을 찾도록 정의를 변형하지 않는다고 못박았다",
          any("반드시 찾아라" in b and "평가 불가로 둔다" in b
              for b in d24["boundaries"]))
    check("★★ 임계값 미만을 없는 것으로 만들지 않는다",
          any("없는 것으로 취급하는 것은 아니다" in b for b in d24["boundaries"]))
    check("★★ 사후 최적화 금지를 근거와 선언 양쪽에 남겼다",
          "사후 최적화 대상이 아니다" in d24["rationale"]
          and any(x["role"] == DC.THRESHOLD_ROLE and "사후 최적화 대상이 아니다" in x["why"]
                  for x in d24["declared_numerics"]))
    check("★★ 이 규칙의 임계값이 다른 카드에서 복사되지 않았다",
          any(x["role"] == DC.THRESHOLD_ROLE and "복사하지도 않았다" in d24["rationale"]
              for x in d24["declared_numerics"]))
    # ★★ 카드 23 — 명칭에 묶지도, 명칭을 바꾸지도 않았다
    d23 = by["RULE-0017::event_definition"]["cio_decision"]
    check("★★ 카드 23 에 판정이 기록됐다", d23 and d23["decided_by"] == "CIO")
    check("★★ 계약 명칭을 판정 조건으로 삼지 않는다",
          any("계약 명칭 자체를 사건 판정 조건으로 삼지 않는다" in b
              for b in d23["boundaries"])
          and "계약 명칭이 아니라" in d23["decision"])
    check("★★ 이름만 바뀐 경우를 사건으로 만들지 않는다",
          any("이름만 변경되고" in b and "보지 않는다" in b for b in d23["boundaries"]))
    check("★★ 취소와 축소를 구분해 정의했다",
          "더 이상 존속하지 않는 상태로 전이" in d23["decision"]
          and "감소하는 방향으로 변경된 경우" in d23["decision"])
    check("★★ 카드 23 은 방향만 정하고 폭은 카드 24 가 정했다",
          any("감소 방향만 정의한다" in b for b in d23["boundaries"])
          and any("최소 감소폭은 카드 24" in b for b in d23["boundaries"])
          and "카드 24" in by["RULE-0017::threshold"]["cio_decision"]["source"])
    check("★★ 네 축이 서로 다른 카드에서 왔다 — 한 카드가 다 정하지 않았다",
          len({by[u]["cio_decision"]["source"] for u in units
               if by[u]["cio_decision"]}) == 4)
    check("★★ 카드 23 이 관측 원천을 넘겼고 카드 25 가 정했다",
          any("관측 원천은 카드 25" in b for b in d23["boundaries"])
          and "카드 25" in by["RULE-0017::data_source"]["cio_decision"]["source"])
    check("★★ 폭·금액을 이 카드에 만들지 않았다 — 숫자는 카드 번호뿐",
          d23["numeric_threshold_introduced"] is False
          and {x["role"] for x in d23["declared_numerics"]} == {"card_reference"})
    check("★★ 노후화를 근거로 투자 논거를 바꾸지 않는다고 명시한다",
          any("투자 논거 자체를 다른 사건으로 바꾸지 않는다" in b
              for b in d23["boundaries"]))
    check("★★ 비교 불가 시 환산해 축소를 만들지 않는다",
          any("임의로 환산하거나 연결하여 축소를 만들지 않는다" in b
              for b in d23["boundaries"]))
    check("★★ 원문은 여전히 옛 명칭 그대로다 — 기록을 현대화하지 않았다",
          "LTA" in by["RULE-0017::event_definition"]
          ["original_condition"]["RULE-0017"])
    # ★★ 카드 22 — 결합만 닫고 아래를 선취하지 않았다
    d22 = by["RULE-0017::semantic_scope"]["cio_decision"]
    check("★★ 카드 22 에 판정이 기록됐다", d22 and d22["decided_by"] == "CIO")
    check("★★ 택일로 판정했고 동시 충족을 요구하지 않는다",
          "택일 관계로 해석한다" in d22["decision"]
          and any("동시 충족을 요구하지 않는다" in b for b in d22["boundaries"]))
    check("★★ 숫자가 하나도 없다 — 폭을 선취하지 않았다",
          not DC.scan_numeric_tokens(d22["decision"])
          and d22["declared_numerics"] == []
          and d22["numeric_threshold_introduced"] is False)
    check("★★ 사건 정의 · 폭 · 원천을 각각 다음 카드로 남겼다",
          all(any(k in b for b in d22["boundaries"])
              for k in ("사건 정의는 다음 카드에서", "축소 폭이나 최소 수치 조건을",
                        "관측 원천이나 수집 가능성을")))
    check("★★ 노후화를 근거로 원문을 재작성하지 않는다고 명시한다",
          any("원문 자체를 재작성하지 않는다" in b for b in d22["boundaries"]))
    check("★★ 외부 자료 판정이 아니라고 기록한다",
          "외부 자료를 근거로 한 판정이 아니라" in d22["rationale"])
    check("★★ 결합 질문이 두 읽기를 나란히 놓는다",
          "동시에 충족돼야 하는가, 어느 하나로 충분한가"
          in by["RULE-0017::semantic_scope"]["question"]["RULE-0017"])
    check("★★ 구조 미결이 정의 성분 어휘와 섞이지 않았다",
          "semantic_scope" not in json.load(
              open(os.path.join(_ROOT, "rules", "definition_inventory.json"),
                   encoding="utf-8"))["allowed_missing_components"])
    check("★★ B5-0 에 구조 미결로 기록됐다",
          any(x["canonical_rule_id"] == "RULE-0017" and x["axis"] == "semantic_scope"
              and x["resolution"] is None
              for x in json.load(
                  open(os.path.join(_ROOT, "rules", "definition_inventory.json"),
                       encoding="utf-8"))["source_semantics_unresolved"]))
    check("★★ 원문 조건은 그대로다",
          by["RULE-0017::semantic_scope"]["original_condition"]["RULE-0017"]
          == next(r["condition_text"] for r in CANON["canonical_rules"]
                  if r["canonical_rule_id"] == "RULE-0017"))
    check("★★ 옛 명칭을 새 구조 이름으로 치환하지 않았다",
          "SCA" not in json.dumps(by["RULE-0017::semantic_scope"]["original_condition"],
                                  ensure_ascii=False)
          and "LTA" in by["RULE-0017::semantic_scope"]
          ["original_condition"]["RULE-0017"])


def test_rule0020_event():
    print("\n[K-17] 관측 실패가 투자 신호가 되지 않는다")
    by = {c["decision_unit"]: c for c in C}
    G = {u: {e["unit"] for e in es} for u, es in DC.PREREQ_GRAPH.items()}
    d26 = by["RULE-0020::event_definition"]["cio_decision"]
    check("★★ 카드 26 에 판정이 기록됐다", d26 and d26["decided_by"] == "CIO")
    # ★★ 이 카드의 핵심 — 세 상태 분리
    check("★★ 확대 확인 / 미확인 / 평가 불가 세 상태를 구분한다",
          "확대 확인 / 확대 미확인 / 평가 불가" in d26["decision"])
    check("★★ 자료 부재를 미확인으로 판정하지 않는다",
          any("「미확인」 으로 판정하지 않는다" in b for b in d26["boundaries"])
          and any("평가 불가로 둔다" in b for b in d26["boundaries"]))
    check("★★ 그 이유가 근거에 남아 있다 — 구현 결핍이 발동이 되면 안 된다",
          "구현 결핍 자체가 투자 규칙의 발동으로 바뀐다" in d26["rationale"]
          and "관측 실패와 사건 부재는 같은 값이 아니다" in d26["rationale"])
    # ★★ 숨은 임계값 차단
    check("★★ 숫자를 만들지 않았다",
          d26["numeric_threshold_introduced"] is False
          and d26["declared_numerics"] == []
          and not DC.scan_numeric_tokens(d26["decision"]))
    check("★★ 정도를 함축하는 낱말을 쓰지 않기로 명시했다",
          any("숨은 임계값이 된다" in b for b in d26["boundaries"]))
    for w in ("유의미", "실질적"):
        check(f"★★ 판정문에 '{w}' 가 들어가지 않았다", w not in d26["decision"])
    # ★★ 대상 선택과 지속 조건을 뒤로 넘겼다
    check("★★ 고객 인증을 사건에서 배제했다",
          any("고객 인증 자체를" in b and "보지 않는다" in b for b in d26["boundaries"]))
    check("★★ 카드 26 이 계열 선택을 넘겼고 원천 카드가 정했다",
          any("관측 원천 카드에서 결정한다" in b for b in d26["boundaries"])
          and "카드 28" in by["RULE-0020::data_source"]["cio_decision"]["source"])
    check("★★ 서로 다른 계열을 이어 붙여 사건을 만들지 않는다",
          any("임의로 연결 · 환산하여" in b for b in d26["boundaries"]))
    check("★★ 지속 조건을 새로 만들지 않았다",
          any("지속 기간이나 연속 횟수를 추가하지 않는다" in b
              for b in d26["boundaries"]))
    check("★★ 카드 26 이 폭을 선취하지 않았고 카드 27 이 정했다",
          any("수치 조건은 이 카드에서 정하지 않는다" in b for b in d26["boundaries"])
          and "카드 27" in by["RULE-0020::threshold"]["cio_decision"]["source"])
    # ★★ 카드 27 — 임계값을 두지 않기로 판정했다
    d27 = by["RULE-0020::threshold"]["cio_decision"]
    check("★★ 카드 27 에 판정이 기록됐다", d27 and d27["decided_by"] == "CIO")
    check("★★ 최소 증가폭을 두지 않기로 명시했다",
          "별도의 최소 증가폭 임계값은 두지 않는다" in d27["decision"]
          and d27["numeric_threshold_introduced"] is False
          and d27["declared_numerics"] == []
          and not DC.scan_numeric_tokens(d27["decision"]))
    check("★ 누적 임계값이 늘지 않았다", CARDS["numeric_thresholds_introduced"] == 7)
    check("★★ 부정 조건의 비대칭성이 근거에 기록됐다",
          "부정 조건이라 임의 임계값의 영향이 비대칭적이다" in d27["rationale"]
          and "규칙이 더 쉽게 발동한다" in d27["rationale"])
    check("★★ 동일·감소는 확대가 아니라고 적는다",
          any("동일한 상태는 증가가 아니므로" in b for b in d27["boundaries"])
          and any("감소 역시 확대가 아니다" in b for b in d27["boundaries"]))
    check("★★ 평가 불가 구분을 뒤집지 않았다",
          any("평가 불가를 유지한다" in b for b in d27["boundaries"]))
    check("★★ 사후 도입 금지를 남겼다",
          any("사후 도입하지 않는다" in b for b in d27["boundaries"]))
    check("★★ 임계값 없음이 관측 자유를 뜻하지 않는다고 못박았다",
          any("아무 숫자나 관측해도 된다는 뜻이 아니다" in b for b in d27["boundaries"]))
    check("★★ 관측 원천은 카드 28 이 정했다",
          any("관측 원천 카드의 책임으로 남긴다" in b for b in d27["boundaries"])
          and "카드 28" in by["RULE-0020::data_source"]["cio_decision"]["source"])
    # ★★ 카드 28 — 능력이 아니라 실제 출하. proxy 를 만들지 않았다
    d28 = by["RULE-0020::data_source"]["cio_decision"]
    check("★★ 카드 28 에 판정이 기록됐다", d28 and d28["decided_by"] == "CIO")
    check("★★ 실제 출하를 채택했다",
          "실제 출하의 확대 여부로 관측한다" in d28["decision"]
          and any(b.startswith("채택 —") and "실제 출하 계열" in b
                  for b in d28["boundaries"]))
    check("★★ 능력·증설·생산개시·인증·계약을 모두 배제했다",
          sum(1 for b in d28["boundaries"] if b.startswith("배제 —")) == 5,
          str(sum(1 for b in d28["boundaries"] if b.startswith("배제 —"))))
    check("★★ 그 이유가 근거에 있다 — 능력은 실제 공급이 아니다",
          "공급할 수 있는 능력이지 실제 공급이 아니다" in d28["rationale"]
          and "「공급능력 확대」 로 바뀐다" in d28["rationale"])
    check("★★ 계열을 이어 붙여 대체하지 않는다",
          any("대체값으로 이어 붙이지 않는다" in b for b in d28["boundaries"]))
    check("★★ 없으면 proxy 대신 평가 불가 — 미확인으로 바꾸지 않는다",
          any("평가 불가로 둔다" in b and "「확대 미확인」 으로 처리하지 않는다" in b
              for b in d28["boundaries"]))
    check("★★ 정의 단계에서 관측 경로 없음으로 닫지 않았다",
          any("정의 단계에서 「관측 경로 없음」 으로 닫지 않는다" in b
              for b in d28["boundaries"])
          and "현재 평가 가능한 공개 관측 경로 없음" not in d28["decision"])
    check("★★ 앞 두 카드를 다시 열지 않았다",
          any("앞 카드의 「두지 않음」 판정을 그대로" in b for b in d28["boundaries"])
          and any("앞 카드 판정 그대로 사건에서 제외한다" in b
                  for b in d28["boundaries"]))
    check("★★ 숫자도 선행 간선도 만들지 않았다",
          d28["numeric_threshold_introduced"] is False
          and d28["declared_numerics"] == []
          and not DC.scan_numeric_tokens(d28["decision"])
          and not any(u.startswith("RULE-0020") for u in DC.PREREQ_GRAPH))
    check("★★ 세 축이 서로 다른 카드에서 왔다",
          len({by[u]["cio_decision"]["source"] for u in
               ("RULE-0020::event_definition", "RULE-0020::threshold",
                "RULE-0020::data_source")}) == 3)
    # ★★ 선행 간선을 만들지 않았다 — 의미가 자료에 끌려가지 않도록
    check("★★ 이 규칙에는 선행 간선이 없다",
          not any(u.startswith("RULE-0020") for u in G))
    check("★★ 그 판정이 고정 그래프에도 기록됐다",
          all(DC.CIO_FIXED_GRAPH.get(u, (None, None))[0] == set()
              for u in ("RULE-0020::event_definition", "RULE-0020::threshold",
                        "RULE-0020::data_source")
              if u in DC.CIO_FIXED_GRAPH) or
          not any(u.startswith("RULE-0020") for u in DC.CIO_FIXED_GRAPH))
    check("★★ 반대 방향 규칙의 정의를 복사하지 않았다",
          any("측정 방향이 반대다" in b for b in d26["boundaries"]))


def test_fail_closed_publish():
    print("\n[K-10] ★★ 검증 실패 산출물이 정본 자리에 남지 않는다")
    import hashlib

    def build_with_violation(out_path):
        """선언 없는 숫자를 주입해 반드시 위반이 나게 한다."""
        d = copy.deepcopy(DC.CIO_DECISIONS)
        d["RULE-0004::threshold"]["rationale"] += " 하단이 10% 이상 낮아진 경우로 본다."
        o = DC.CIO_DECISIONS
        try:
            DC.CIO_DECISIONS = d
            return DC.build(out_path=out_path)[1]
        finally:
            DC.CIO_DECISIONS = o

    # ① 출력 파일이 없는 상태 — 위반이면 만들어지지 않는다
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        tmp = f.name
    os.unlink(tmp)
    errs = build_with_violation(tmp)
    check("★★ 위반이 실제로 발생했다", bool(errs), str(errs[:1]))
    check("★★ 위반이면 산출물을 만들지 않는다", not os.path.exists(tmp))

    # ② 정상본이 이미 있는 상태 — 위반 빌드가 그것을 훼손하지 않는다
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        tmp2 = f.name
    try:
        DC.build(out_path=tmp2)                      # 정상본 생성
        good = hashlib.sha256(open(tmp2, "rb").read()).hexdigest()
        errs = build_with_violation(tmp2)
        after = hashlib.sha256(open(tmp2, "rb").read()).hexdigest()
        check("★★ 마지막 정상본의 해시가 바뀌지 않는다", good == after and bool(errs))
    finally:
        if os.path.exists(tmp2):
            os.unlink(tmp2)

    # ③ 정상 빌드는 여전히 쓴다 — fail-closed 가 정상 경로를 막지 않는다
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        tmp3 = f.name
    os.unlink(tmp3)
    try:
        _, errs = DC.build(out_path=tmp3)
        check("★★ 위반 0 이면 정상적으로 쓴다", not errs and os.path.exists(tmp3))
    finally:
        if os.path.exists(tmp3):
            os.unlink(tmp3)


def test_sign_boundary_role():
    print("\n[K-11] ★★ 부호 경계가 임계값의 은신처가 되지 않는다")
    by = {c["decision_unit"]: c for c in C}
    d15 = by["RULE-0025::event_definition"]["cio_decision"]
    check("★ 부호 경계 역할이 어휘에 있다", DC.SIGN_BOUNDARY_ROLE in DC.DECLARED_NUMERIC_ROLES)
    check("★★ 부호 경계는 0 에만 허용된다", DC.SIGN_BOUNDARY_TOKENS == {"0"},
          str(DC.SIGN_BOUNDARY_TOKENS))
    check("★★ 실제 선언이 0 에만 붙어 있다",
          all(x["token"] in DC.SIGN_BOUNDARY_TOKENS for c in C if c["cio_decision"]
              for x in c["cio_decision"].get("declared_numerics", [])
              if x["role"] == DC.SIGN_BOUNDARY_ROLE))
    check("★★ 부호 경계는 임계값 도입으로 세지 않는다",
          d15["numeric_threshold_introduced"] is False
          and not any(x["role"] == DC.THRESHOLD_ROLE
                      for x in d15["declared_numerics"]))
    check("★★ 그 근거가 임계값이 아님을 명시한다",
          any(x["role"] == DC.SIGN_BOUNDARY_ROLE and "임계값이 아니다" in x["why"]
              for x in d15["declared_numerics"]))

    # ★★ [음성] 최소폭 조건의 숫자를 부호 경계로 위장하면 잡힌다
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0025::event_definition"]["decision"] += " 순매수가 10 이상이어야 유지된다."
    d["RULE-0025::event_definition"]["declared_numerics"].append(
        {"token": "10", "role": DC.SIGN_BOUNDARY_ROLE, "why": "부호 경계다."})
    check("★★ [음성] 최소폭 숫자를 부호 경계로 위장하면 잡힌다",
          any("부호 경계는 0 에만 쓸 수 있다" in e for e in rebuild(decisions=d)))
    # ★★ [음성] 부호 경계라도 임계값 도입 선언과 뒤섞이면 안 된다
    d = copy.deepcopy(DC.CIO_DECISIONS)
    d["RULE-0025::event_definition"]["declared_numerics"][0]["role"] = DC.THRESHOLD_ROLE
    check("★★ [음성] 부호 경계를 임계값 역할로 바꾸면 잡힌다",
          any("임계값을 도입하지 않았다고 선언했다" in e for e in rebuild(decisions=d)))


def test_cio_context_no_numbers():
    print("\n[K-2] ★★ 공표 수치가 임계값으로 새지 않는다")
    cio = [e for c in C for e in c["observable_evidence"] if e["origin"] == "cio_provided"]
    check(f"★ CIO 제공 사실 {len(cio)}건", len(cio) >= 5, str(len(cio)))
    check("★★ 전부 출처가 붙어 있다", all(e.get("source") for e in cio))
    check("★★ CIO 제공 사실에 숫자가 0건",
          not any(DC.scan_numeric_tokens(e["text"]) for e in cio),
          str([e["text"][:20] for e in cio if DC.scan_numeric_tokens(e["text"])]))
    check("★ 우리 관측과 CIO 제공을 origin 으로 구분한다",
          {e["origin"] for c in C for e in c["observable_evidence"]}
          == {"atlas_artifact", "cio_provided"})

    ev = copy.deepcopy(DC.EVIDENCE)
    ev["RULE-0021"] = [DC.C("이 기업은 성장률 39% 를 공표한다.")]
    check("★★ [음성] CIO 제공 사실에 수치를 넣으면 잡힌다",
          any("임계값으로 새는 경로" in e for e in rebuild(evidence=ev)))
    ev = copy.deepcopy(DC.EVIDENCE)
    ev["RULE-0021"] = [{"origin": "cio_provided", "text": "출처 없는 주장"}]
    check("★★ [음성] 출처 없는 CIO 항목은 잡힌다",
          any("출처가 없다" in e for e in rebuild(evidence=ev)))


def test_obsolescence():
    print("\n[K-3] 원문과 현재 공시 구조의 어긋남을 숨기지 않았다")
    r17 = [c for c in C if c["decision_unit"].startswith("RULE-0017")]
    check("★★ RULE-0017 카드 4건 전부 노후화 위험 표시 — 규칙 단위 표시다",
          len(r17) == 4 and all(c["semantic_obsolescence_risk"].startswith("★ 확인됨")
                                for c in r17), str(len(r17)))
    check("★★ 그 내용이 장기계약 구조 변화를 지목한다",
          "장기 공급계약 구조" in r17[0]["semantic_obsolescence_risk"])
    check("★★ 규칙 문구를 바꾸지 않았다고 명시한다",
          "임의로 바꾸지 않았다" in r17[0]["semantic_obsolescence_risk"])
    check("★★ 원문 조건이 그대로 보존된다",
          all(c["original_condition"][r] ==
              next(x["condition_text"] for x in CANON["canonical_rules"]
                   if x["canonical_rule_id"] == r)
              for c in C for r in c["affected_rules"]))
    g1 = [c for c in C if c["shared_group"] == "G1"]
    check("★ G1 도 가격 계열 확인 필요를 표시한다",
          all(c["semantic_obsolescence_risk"] for c in g1))
    # ★★ 빈칸이 '위험 없음' 으로 읽히면 안 된다 — 세 상태를 전부 문장으로 적었는가
    check("★★ 모든 카드에 상태 문장이 있다 (빈칸 0)",
          all(isinstance(c["semantic_obsolescence_risk"], str)
              and c["semantic_obsolescence_risk"] for c in C))
    clear = {c["decision_unit"] for c in C
             if c["semantic_obsolescence_risk"].startswith("없음")}
    derived = {a["canonical_rule_id"] for a in AMB["items"]
               if a["data_source_deficiency_class"] == "data capability gap"}
    check("★★ '없음' 은 앞 단계가 대상 모호성 없음으로 판정한 건에만 붙는다",
          clear == {c["decision_unit"] for c in C
                    if not c["semantic_obsolescence_risk"].startswith("★")
                    and c["shared_group"] is None
                    and all(r in derived for r in c["affected_rules"])},
          str(sorted(clear)))
    check("★★ 나머지는 '미평가' 이고 위험 없음을 뜻하지 않는다고 적는다",
          all("위험이 없다는 뜻이 아니다" in c["semantic_obsolescence_risk"]
              for c in C if c["semantic_obsolescence_risk"].startswith("미평가")))
    check("★ 세 상태 합이 카드 수와 같다",
          CARDS["counts"]["obsolescence_flagged"] + CARDS["counts"]["obsolescence_clear"]
          + CARDS["counts"]["obsolescence_not_assessed"] == len(C))
    # ★★ [음성] 근거 없는 rule 을 '없음' 대상으로 밀어 넣어도 유도가 넘어가지 않는다
    inj = copy.deepcopy(DC.CARDS)
    for c in inj:
        if c["unit"] == "RULE-0025::time_window":
            c["rules"] = ["RULE-0025"]
    p = rebuild(cards=inj, full=True)[0]
    got = next(c["semantic_obsolescence_risk"] for c in p["cards"]
               if c["decision_unit"] == "RULE-0025::time_window")
    check("★★ [음성] 앞 단계 근거가 없는 rule 은 '없음' 을 받지 않는다",
          got.startswith("미평가"), got[:30])


def test_group_state():
    print("\n[K-4] 승인 상태가 카드에 반영된다")
    check("★★ G1 승인 · G2 승인 · C1 기각",
          "승인" in CARDS["approval_state"]["G1"]
          and "승인" in CARDS["approval_state"]["G2"]
          and CARDS["approval_state"]["C1"].startswith("기각"))
    r = CARDS["c1_resolution"]
    check("★★ C1 기각에 작성자·출처·근거가 남아 있다",
          r["decided_by"] == "CIO" and r["source"] and "공급측" in r["rationale"]
          and "수요측" in r["rationale"])
    check("★★ 기각이므로 두 규칙의 정의를 공유하지 않는다고 적는다",
          "공유하지 않는다" in r["rationale"] and "각각 유지" in r["rationale"])
    g1 = [c for c in C if c["shared_group"] == "G1"]
    g2 = [c for c in C if c["shared_group"] == "G2"]
    check("★★ G1 카드 4건 · 각 카드가 두 규칙을 함께 든다",
          len(g1) == 4 and all(c["affected_rules"] == ["RULE-0002", "RULE-0018"] for c in g1))
    check("★★ G2 카드 3건 · 각 카드가 두 규칙을 함께 든다",
          len(g2) == 3 and all(c["affected_rules"] == ["RULE-0010", "RULE-0019"] for c in g2))
    check("★★ G1 카드가 '정의 공유 ≠ 병합' 을 명시한다",
          all("병합하지 않는다" in c["implementation_consequence"] for c in g1))
    check("★★ G2 카드가 '비교 대상은 결정 대상이 아님' 을 명시한다",
          all("결정 대상이 아니다" in c["implementation_consequence"] for c in g2))
    check("★★ C1 은 끝까지 공유 카드로 만들지 않았다",
          not any(c["shared_group"] == "C1" for c in C))
    c1_units = {"RULE-0004::threshold", "RULE-0004::comparison_baseline",
                "RULE-0015::threshold", "RULE-0015::comparison_baseline"}
    check("★★ 기각된 4건은 각 규칙별 개별 카드로 남아 있다",
          all(len([c for c in C if c["decision_unit"] == u]) == 1 for u in c1_units))
    check("★★ 그 카드들이 공유하지 않는다는 귀결을 담고 있다",
          all("공유되지 않는다" in c["implementation_consequence"]
              for c in C if c["decision_unit"] in c1_units))


def test_upstream_untouched():
    print("\n[K-5] 상위 artifact 를 건드리지 않았다")
    for k, f in (("definition_decision_sha256", "definition_decision.json"),
                 ("data_source_ambiguity_sha256", "data_source_ambiguity.json"),
                 ("decision_normalization_sha256", "decision_normalization.json"),
                 ("canonical_rules_sha256", "canonical_rules.json")):
        check(f"★★ {f} 해시 불변",
              CARDS["decided_against"][k] == DC._sha(os.path.join(_ROOT, "rules", f)))
    check("★★ B5-1 resolution_status 15건 불변",
          all(d["resolution_status"] == "REQUIRES_CIO_DEFINITION" for d in DEC["items"]))
    check("★★ canonical 25 불변 · condition_semantics/scope UNRESOLVED",
          len(CANON["canonical_rules"]) == 25
          and all(r["condition_semantics"] == "UNRESOLVED" and r["scope"] == "UNRESOLVED"
                  for r in CANON["canonical_rules"]))
    # ★ rules.json 은 CIO 승인 2026-08-15 로 승격 단계가 만들었다. 존재 자체는 위반이
    #   아니며, 이 단계가 그것을 만들거나 자기 산출물로 주장하는 것이 위반이다.
    _rj = os.path.join(_ROOT, "config", "rules.json")
    check("★★ rules.json 은 B5-2B 의 산출물이 아니다",
          (not os.path.exists(_rj))
          or json.load(open(_rj, encoding="utf-8"))["artifact"]
          == "Rule SSOT (config/rules.json)")
    check("★★ B5-2B 는 여전히 authority 가 아니다",
          CARDS["authority"] is False)
    check("★★ authority / consumable = False",
          CARDS["authority"] is False and CARDS["consumable_by_evaluator"] is False)


def test_card2_scope():
    print("\n[K-6] 공급측 판정이 옆 카드로 번지지 않았다")
    by = {c["decision_unit"]: c for c in C}
    d2 = by["RULE-0004::threshold"]["cio_decision"]
    d3 = by["RULE-0004::comparison_baseline"]["cio_decision"]
    d5 = by["RULE-0015::threshold"]["cio_decision"]
    check("★★ 카드 2 에 판정이 기록됐다", d2 is not None and d2["decided_by"] == "CIO")
    check("★★ 카드 3 에 판정이 기록됐다", d3 is not None and d3["decided_by"] == "CIO")
    # ★★ 카드 3 — 같은 대상연도 안에서만 비교한다
    check("★★ 경계 조건 7건이 보존된다", len(d3["boundaries"]) == 7, str(len(d3["boundaries"])))
    for key in ("전년도", "동일 대상 회계연도", "기준선 생성", "가장 높은 값이나 최초 값"):
        check(f"★ 카드 3 경계에 '{key}' 가 남아 있다",
              any(key in b for b in d3["boundaries"]))
    check("★★ 최초 가이던스만으로는 하향 판정하지 않는다고 적혀 있다",
          "그 자체로 하향 판정하지 않는다" in " ".join(d3["boundaries"])
          and "발표 자체만으로" in d3["decision"])
    check("★★ 카드 2 의 하단 비교를 뒤집지 않는다",
          any("하단" in b for b in d3["boundaries"]))
    check("★★ 두 공급측 판정 모두 실제 집행액을 기준에서 배제한다",
          any("실제 집행" in b for b in d2["boundaries"])
          and any("실제 집행" in b for b in d3["boundaries"]))
    check("★★ 공급측 비교 기준이 수요측으로 복제되지 않았다",
          by["RULE-0015::comparison_baseline"]["cio_decision"]["decision"]
          != d3["decision"])

    # ★★ 카드 4 — 관측군 고정. 원문의 발동 조건은 손대지 않는다
    d4 = by["RULE-0015::data_source"]["cio_decision"]
    check("★★ 카드 4 에 판정이 기록됐다", d4 is not None and d4["decided_by"] == "CIO")
    check("★★ 판정 총 36건 · 미판정 0건",
          CARDS["counts"]["answered_cards"] == 36 and CARDS["counts"]["open_cards"] == 0,
          f"{CARDS['counts']['answered_cards']}/{CARDS['counts']['open_cards']}")
    check("★★ 경계 조건 8건이 보존된다", len(d4["boundaries"]) == 8, str(len(d4["boundaries"])))
    check("★★ 원문의 발동 조건을 바꾸지 않았다고 명시한다",
          any("「2곳+」 조건은 그대로 유지한다" in b for b in d4["boundaries"]))
    check("★★ 원문 조건은 그대로 보존된다",
          by["RULE-0015::data_source"]["original_condition"]["RULE-0015"]
          == next(r["condition_text"] for r in CANON["canonical_rules"]
                  if r["canonical_rule_id"] == "RULE-0015"))
    check("★★ 관측군 규모는 임계값이 아니라고 선언돼 있다",
          any(d["role"] == "universe_size" and "임계값이 아니다" in d["why"]
              for d in d4["declared_numerics"]))
    check("★★ 공급망 기업을 관측군에서 배제한다",
          any("Broadcom" in b for b in d4["boundaries"])
          and any("TSMC" in b for b in d4["boundaries"]))
    check("★★ 제3자 추정치를 원천으로 대체하지 않는다",
          any("제3자 데이터만으로" in b for b in d4["boundaries"]))
    # ★★ 카드 6 — 공급측 기준을 복사하지 않았다
    d6 = by["RULE-0015::comparison_baseline"]["cio_decision"]
    check("★★ 카드 6 에 판정이 기록됐다", d6 is not None and d6["decided_by"] == "CIO")
    check("★★ 선행조건 이력은 판정 후에도 남아 있다",
          by["RULE-0015::comparison_baseline"]["prerequisite"]["status"] == "충족됨")
    check("★★ 카드 6 경계 조건 9건이 보존된다", len(d6["boundaries"]) == 9,
          str(len(d6["boundaries"])))
    check("★★ 공급측 카드 3 의 판정문을 그대로 복사하지 않았다",
          d6["decision"] != d3["decision"] and d6["boundaries"] != d3["boundaries"])
    check("★★ 공급측은 대상연도 고정, 수요측은 측정계열·기간 단위 고정",
          "대상 회계연도" in d3["decision"]
          and "동일한 측정 정의와 동일한 기간 단위" in d6["decision"])
    check("★★ 회계연도를 달력연도로 강제 정렬하지 않는다",
          any("달력연도로 강제 정렬하지 않는다" in b for b in d6["boundaries"]))
    check("★★ 비교 가능한 값이 없으면 대체하지 않고 판정 불가로 둔다",
          any("판정 불가로 두며 다른 값으로 대체하지 않는다" in b for b in d6["boundaries"]))
    check("★★ 전망치와 실제치를 섞지 않는다 — 카드 5 와 같은 방향",
          any("전망치와 실제 집행치를 서로 비교하지 않는다" in b for b in d6["boundaries"])
          and any("실제 집행치와 전망치를 섞어서" in b for b in d5["boundaries"]))
    check("★★ 카드 5 와 책임이 갈린다 — 폭은 카드 5, 비교 대상은 카드 6",
          "최소 수치 임계값을 두지 않는다" in d5["decision"]
          and "비교 기준으로 한다" in d6["decision"]
          and "임계값" not in d6["decision"])
    check("★★ 수요측 3칸이 모두 판정됐다",
          {c["decision_unit"] for c in C
           if c["decision_unit"].startswith("RULE-0015") and c["cio_decision"]}
          == {"RULE-0015::data_source", "RULE-0015::threshold",
              "RULE-0015::comparison_baseline"})
    check("★★ 카드 5 경계 조건 9건이 보존된다", len(d5["boundaries"]) == 9,
          str(len(d5["boundaries"])))
    check("★★ 감소폭 임계값을 만들지 않았다고 명시한다",
          any("감소폭 임계값을 만들지 않는다" in b for b in d5["boundaries"])
          and "별도의 최소 수치 임계값을 두지 않는다" in d5["decision"])
    check("★★ 비교 대상을 카드 5 가 아니라 카드 6 이 정했다",
          any("카드 6 의 결정사항" in b for b in d5["boundaries"])
          and "카드 6" in by["RULE-0015::comparison_baseline"]["cio_decision"]["source"])
    check("★★ 원문의 기업 수 조건을 바꾸지 않았다고 적는다",
          any("원문의 기업 수 발동 조건" in b for b in d5["boundaries"]))
    check("★★ 인용한 원문 숫자를 임계값이 아닌 것으로 선언했다",
          any(x["token"] == "2" and x["role"] == "quoted_phrase"
              for x in d5["declared_numerics"]))
    check("★★ 하단 원칙을 쓰되 공급측과 정의를 공유하지 않는다",
          "공유하는 것이 아니라" in d5["rationale"]
          and CARDS["approval_state"]["C1"].startswith("기각"))
    check("★★ 카드 2 경계 조건 6건이 보존된다", len(d2["boundaries"]) == 6,
          str(len(d2["boundaries"])))
    for key in ("midpoint", "상단만", "실제 집행", "숫자 임계값을 새로 만들지 않는다"):
        check(f"★ 경계에 '{key}' 가 남아 있다",
              any(key in b for b in d2["boundaries"]))
    check("★★ 판정문이 하단 비교 규칙이다 — 금액이 아니다",
          "하단" in d2["decision"] and not DC.scan_numeric_tokens(d2["decision"]))
    check("★★ 카드 1 결정을 다시 인용해 대상 범위를 좁히지 않았다",
          any("카드 1 결정대로" in b for b in d2["boundaries"]))
    check("★★ 원문 조건은 그대로다",
          by["RULE-0004::threshold"]["original_condition"]["RULE-0004"]
          == next(r["condition_text"] for r in CANON["canonical_rules"]
                  if r["canonical_rule_id"] == "RULE-0004"))


def test_shared_group_decision():
    print("\n[K-7] G1 공유 판정이 한 카드에 갇혀 있다")
    by = {c["decision_unit"]: c for c in C}
    g1 = [c for c in C if c["shared_group"] == "G1"]
    d7 = by["G1::data_source"]["cio_decision"]
    check("★★ 카드 7 에 판정이 기록됐다", d7 is not None and d7["decided_by"] == "CIO")
    check("★★ G1 4장이 모두 판정됐다",
          not [c["decision_unit"] for c in g1 if not c["cio_decision"]],
          str([c["decision_unit"] for c in g1 if not c["cio_decision"]]))

    # ★★ 카드 10 — 첫 실제 임계값. 승격 조건이 fail-closed 인가
    d10 = by["G1::time_window"]["cio_decision"]
    check("★★ 카드 10 에 판정이 기록됐다", d10 is not None and d10["decided_by"] == "CIO")
    check("★★ 임계값 도입을 숨기지 않고 선언했다",
          d10["numeric_threshold_introduced"] is True)
    check("★★ 그 숫자가 임계값 역할로, 원문 유도가 아니라고 기록됐다",
          any(x["token"] == "2" and x["role"] == DC.THRESHOLD_ROLE
              and "원문에서 유도된 값이 아니라" in x["why"]
              for x in d10["declared_numerics"]))
    check("★★ 카드 번호는 여전히 임계값이 아니다",
          {x["token"] for x in d10["declared_numerics"] if x["role"] == "card_reference"}
          == {"8", "9"})
    check("★★ 첫 하락만으로 전환을 확정하지 않는다",
          any("첫 하락 관측만으로 「하락 전환」 을 확정하지 않는다" in b
              for b in d10["boundaries"]))
    check("★★ 동일값·상승이면 연속이 끊긴다",
          any("동일하거나 상승하면 연속 하락 카운트는 끊긴다" in b for b in d10["boundaries"])
          and "다시 첫 하락 관측부터 확인한다" in d10["decision"])
    check("★★ 판정 유보와 기준선 생성은 횟수에 포함하지 않는다 — 관측 공백이 "
          "전환을 만들지 않는다",
          any("새 기준선이 생성되는 관측은 하락 횟수에 포함하지 않는다" in b
              for b in d10["boundaries"])
          and any("판정이 유보된 경우에도 하락 횟수를 증가시키지 않는다" in b
                  for b in d10["boundaries"]))
    check("★★ 경과일수가 아니라 공식 관측 횟수를 센다",
          any("공식 관측 횟수를 기준으로 한다" in b for b in d10["boundaries"]))
    check("★★ 앞 카드의 하락폭 정의를 다시 열지 않았다",
          any("최소 퍼센트 · 금액 조건을 추가하지 않는다" in b for b in d10["boundaries"]))

    # ★★ 카드 9 — 계열이 끊기면 하락을 만들지 않는다 (fail-closed)
    d9 = by["G1::comparison_baseline"]["cio_decision"]
    check("★★ 카드 9 에 판정이 기록됐다", d9 is not None and d9["decided_by"] == "CIO")
    check("★★ 계열 교체를 하락으로 만들지 않는다",
          "이어 붙여 하락을 판정하지 않고" in d9["decision"]
          and any("기준선 생성으로만 처리하며, 그 자체로 하락을 판정하지 않는다" in b
                  for b in d9["boundaries"]))
    check("★★ 비교값이 없으면 대체하지 않고 유보한다",
          any("대체하지 않고 그 시점의 하락 판정을 유보한다" in b for b in d9["boundaries"]))
    check("★★ 원문에 없는 기준기간을 만들지 않았다",
          all(any(k in b for b in d9["boundaries"])
              for k in ("전년동기", "분기 평균이나 이동평균", "사이클 고점")))
    check("★★ 카드 8 의 방향 정의를 뒤집지 않는다",
          any("하락 방향 정의를 변경하지 않는다" in b for b in d9["boundaries"]))
    check("★★ 전환 횟수는 카드 9 가 아니라 카드 10 이 정했다",
          any("카드 10 의 결정사항으로 남긴다" in b for b in d9["boundaries"])
          and "카드 10" in by["G1::time_window"]["cio_decision"]["source"])

    # ★★ 카드 8 — '하락' 과 '하락 전환' 을 갈라 놓았다
    d8 = by["G1::threshold"]["cio_decision"]
    check("★★ 카드 8 에 판정이 기록됐다", d8 is not None and d8["decided_by"] == "CIO")
    check("★★ 최소 하락폭을 만들지 않았다",
          "별도의 최소 수치 임계값을 두지 않는다" in d8["decision"]
          and d8["numeric_threshold_introduced"] is False)
    check("★★ 판정문의 숫자는 전부 카드 번호로 선언됐다",
          all(x["role"] == "card_reference" for x in d8["declared_numerics"])
          and {x["token"] for x in d8["declared_numerics"]} == {"7", "9", "10"})
    check("★★ '하락' 과 '하락 전환' 을 구분한다고 명시한다",
          "구분하며" in d8["decision"]
          and any("전체가 확정됐다고 판정하지 않는다" in b for b in d8["boundaries"]))
    check("★★ 전환 기간은 카드 8 이 아니라 카드 10 이 정했다",
          any("카드 10 에서 결정한다" in b for b in d8["boundaries"])
          and "카드 10" in by["G1::time_window"]["cio_decision"]["source"])
    check("★★ 비교 기준은 카드 8 이 아니라 카드 9 가 정했다",
          any("카드 9 의 결정사항" in b for b in d8["boundaries"])
          and "카드 9" in by["G1::comparison_baseline"]["cio_decision"]["source"])
    check("★★ 카드 7 의 경계를 다시 열지 않았다",
          any("변경하지 않는다" in b for b in d8["boundaries"]))
    check("★★ 동가는 하락이 아니다 — 방향 판정에 등호가 섞이지 않았다",
          any("가격이 동일하면 하락으로 보지 않는다" in b for b in d8["boundaries"]))
    check("★★ 판정이 두 규칙에 함께 적용된다",
          by["G1::data_source"]["affected_rules"] == ["RULE-0002", "RULE-0018"])
    check("★★ 정의 공유이지 병합이 아니라고 판정문에 남아 있다",
          any("병합하지 않는다" in b for b in d7["boundaries"]))
    check("★★ 대표 상품을 이 카드에서 고정하지 않았다",
          any("영구 대표값으로 고정하지 않는다" in b for b in d7["boundaries"])
          and "고정 대표값으로 선택하지 않고" in d7["decision"])
    check("★★ 뒤 카드의 결정사항을 미리 정하지 않았다",
          any("threshold · comparison_baseline · time_window 카드에서" in b
              for b in d7["boundaries"]))
    check("★★ 그룹 식별자 안의 숫자를 임계값이 아닌 것으로 선언했다",
          any(x["role"] == "group_reference" for x in d7["declared_numerics"]))

    # ★★ 노후화 경고는 판정됐지만 카드에 남아 있다 — 지우지 않았다
    check("★★ G1 4카드의 노후화 표시가 그대로 유지된다",
          all(c["semantic_obsolescence_risk"] and not
              c["semantic_obsolescence_risk"].startswith(("없음", "미평가")) for c in g1))
    check("★★ 노후화 판정은 카드 7 안에만 기록됐다",
          "obsolescence_adjudication" in d7
          and not any("obsolescence_adjudication" in (c["cio_decision"] or {})
                      for c in C if c["decision_unit"] != "G1::data_source"))
    check("★★ 그 판정이 규칙을 수정하지 않는다고 밝힌다",
          "규칙을 수정하지 않고" in d7["obsolescence_adjudication"])
    check("★★ 원문 조건은 두 규칙 모두 그대로다",
          all(by["G1::data_source"]["original_condition"][r]
              == next(x["condition_text"] for x in CANON["canonical_rules"]
                      if x["canonical_rule_id"] == r)
              for r in ("RULE-0002", "RULE-0018")))


def test_g2_event_definition():
    print("\n[K-8] G2 는 계산만 공유하고 비교 대상은 각자 유지한다")
    by = {c["decision_unit"]: c for c in C}
    g2 = [c for c in C if c["shared_group"] == "G2"]
    d11 = by["G2::event_definition"]["cio_decision"]
    check("★★ 카드 11 에 판정이 기록됐다", d11 is not None and d11["decided_by"] == "CIO")
    check("★★ G2 3장이 모두 판정됐다",
          not [c["decision_unit"] for c in g2 if not c["cio_decision"]],
          str([c["decision_unit"] for c in g2 if not c["cio_decision"]]))

    # ★★ 카드 13 — 값은 G1 과 같지만 유래가 다르다. 관측 공백은 세지 않는다
    d13 = by["G2::time_window"]["cio_decision"]
    check("★★ 카드 13 에 판정이 기록됐다", d13 is not None and d13["decided_by"] == "CIO")
    check("★★ 임계값 도입을 선언했다", d13["numeric_threshold_introduced"] is True)
    check("★★ 값이 같아도 복사가 아니라고 기록됐다",
          any(x["role"] == DC.THRESHOLD_ROLE and "복사한 값이 아니라" in x["why"]
              for x in d13["declared_numerics"])
          and "복사한 것이 아니라" in d13["rationale"])
    check("★★ 두 그룹의 임계값이 서로 다른 카드에서 왔다",
          by["G1::time_window"]["cio_decision"]["source"] != d13["source"])
    check("★★ 첫 열위만으로 지속을 확정하지 않는다",
          any("후보 상태로만 둔다" in b for b in d13["boundaries"]))
    check("★★ 동일·상승이면 연속이 끊기고 다시 첫 관측부터 센다",
          any("연속성은 끊긴다" in b for b in d13["boundaries"])
          and any("새로운 첫 관측으로 본다" in b for b in d13["boundaries"]))
    check("★★ 경과일이 아니라 실제 생성된 관측 횟수를 센다",
          any("달력일이나 거래일 경과 수를 세지 않는다" in b for b in d13["boundaries"])
          and "실제 생성된 비교 가능한 상대강도 관측 횟수를 센다" in d13["decision"])
    check("★★ 관측이 만들어지지 않은 날은 횟수에 들어가지 않는다 — 공백이 승격을 "
          "만들지 않는다",
          any("관측값 자체를 만들지 않은 날은 횟수에 포함하지 않는다" in b
              for b in d13["boundaries"]))

    # ★★ 카드 12 — 서로 다른 거래일을 합성해 비율을 만들지 않는다 (fail-closed)
    d12 = by["G2::comparison_baseline"]["cio_decision"]
    check("★★ 카드 12 에 판정이 기록됐다", d12 is not None and d12["decided_by"] == "CIO")
    check("★★ 공통 거래일에만 관측을 만든다",
          "공통 거래일에 대해서만 상대강도 비율을 생성하고" in d12["decision"]
          and any("공통 거래일만 상대강도 관측으로 인정한다" in b
                  for b in d12["boundaries"]))
    check("★★ 휴장일에는 관측을 만들지 않는다 — 값을 지어내지 않는다",
          "상대강도 관측값 자체를 만들지 않는다" in d12["decision"]
          and any("휴장한 날짜에는 상대강도 비율을 새로 생성하지 않는다" in b
                  for b in d12["boundaries"]))
    check("★★ 종가 이월과 다른 거래일 조합을 둘 다 막았다",
          any("다음 날짜로 이월하지 않는다" in b for b in d12["boundaries"])
          and any("서로 다른 거래일의 종가를 조합해" in b for b in d12["boundaries"]))
    check("★★ 원천 부재를 대체 가격으로 메우지 않는다",
          any("대체 가격 · 전일 가격 · 비공식 가격을 사용하지 않는다" in b
              for b in d12["boundaries"]))
    check("★★ 비교값이 없으면 유보한다",
          any("대체하지 않고 그 시점의 열위 판정을 유보한다" in b for b in d12["boundaries"]))
    check("★★ 원문에 없는 기준선을 만들지 않았다",
          all(k in " ".join(d12["boundaries"])
              for k in ("이동평균", "전년동기", "과거 고점", "고정 기준")))
    check("★★ 카드 11 의 산식·방향·최소폭 없음을 다시 열지 않았다",
          any("최소 열위 폭 없음은 변경하지 않는다" in b for b in d12["boundaries"]))
    check("★★ 「지속」 을 카드 12 가 선취하지 않았다",
          any("카드 13 의 결정사항" in b for b in d12["boundaries"])
          and "카드 13" in by["G2::time_window"]["cio_decision"]["source"])
    check("★★ 카드 12 자신은 임계값을 도입하지 않았다",
          d12["numeric_threshold_introduced"] is False
          and not d12.get("declared_numerics", [{}])[0].get("role") == DC.THRESHOLD_ROLE)
    check("★★ 두 원문이 그대로 보존된다 — 어순 차이도 지우지 않았다",
          by["G2::event_definition"]["original_condition"]["RULE-0010"] == "ANET 대비 상대강도 열위 지속"
          and by["G2::event_definition"]["original_condition"]["RULE-0019"] == "SK 대비 상대강도 지속 열위")
    check("★★ 어순 차이로 계산을 갈라놓지 않는다고 명시한다",
          "계산 의미상 동일하게 취급한다" in d11["decision"]
          and any("서로 다른 계산식을 만들지 않는다" in b for b in d11["boundaries"]))
    check("★★ 비교 대상을 하나로 통합하지 않는다",
          any("하나의 공통 기준으로 통합하지 않는다" in b for b in d11["boundaries"]))
    check("★★ 상대비율 정의다 — 절대주가만으로 판정하지 않는다",
          all(any(k in b for b in d11["boundaries"])
              for k in ("평가 대상의 절대주가", "비교 대상의 절대주가")))
    check("★★ 동가는 열위가 아니다", any("동일하면 열위가 아니다" in b
                                    for b in d11["boundaries"]))
    check("★★ 수집 불가를 정의 변경 사유로 쓰지 않았다",
          any("정의를 변경할 이유로 사용하지 않는다" in b
              and "평가 불가 상태를 유지한다" in b for b in d11["boundaries"]))
    check("★★ 「지속」 과 비교 기준을 카드 11 이 선취하지 않았다",
          any("카드 13 의 결정사항" in b for b in d11["boundaries"])
          and any("카드 12 의 결정사항" in b for b in d11["boundaries"])
          and "카드 13" in by["G2::time_window"]["cio_decision"]["source"]
          and "카드 12" in by["G2::comparison_baseline"]["cio_decision"]["source"])
    check("★★ 카드 11 자신은 임계값을 도입하지 않았다",
          d11["numeric_threshold_introduced"] is False
          and not any(x["role"] == DC.THRESHOLD_ROLE
                      for x in d11.get("declared_numerics", [])))
    check("★★ 규칙 식별자 안의 숫자를 임계값이 아닌 것으로 선언했다",
          {x["token"] for x in d11["declared_numerics"] if x["role"] == "rule_reference"}
          == {"0010", "0019"})
    # ★★ G1 의 지속 조건이 G2 로 복사되지 않았다
    g1_tw = by["G1::time_window"]["cio_decision"]
    check("★★ G1 의 전환 횟수가 계산 정의 카드로 새지 않았다",
          "2" in {x["token"] for x in g1_tw["declared_numerics"]
                  if x["role"] == DC.THRESHOLD_ROLE}
          and not any(x["role"] == DC.THRESHOLD_ROLE
                      for x in d11["declared_numerics"])
          and not any(x["role"] == DC.THRESHOLD_ROLE
                      for x in d12.get("declared_numerics", [])))


def test_rule_specific_cards():
    print("\n[K-9] 규칙 고유 카드 — 인벤토리 범위를 넘지 않았다")
    by = {c["decision_unit"]: c for c in C}
    d14 = by["RULE-0025::time_window"]["cio_decision"]
    inv = {i["canonical_rule_id"]: i for i in json.load(
        open(os.path.join(_ROOT, "rules", "definition_inventory.json"),
             encoding="utf-8"))["items"]}
    check("★★ 카드 16 RULE-0025::time_window 에 판정이 기록됐다",
          d14 is not None and d14["decided_by"] == "CIO")
    check("★★ 임계값 도입을 선언했다", d14["numeric_threshold_introduced"] is True)
    check("★★ 값이 앞 그룹에서 복사된 것이 아니라고 기록됐다",
          any(x["role"] == DC.THRESHOLD_ROLE and "복사한 값이 아니다" in x["why"]
              for x in d14["declared_numerics"]))
    check("★★ 임계값이 전부 서로 다른 카드에서 왔다",
          len({c["cio_decision"]["source"] for c in C if c["cio_decision"]
               and any(x["role"] == DC.THRESHOLD_ROLE
                       for x in c["cio_decision"].get("declared_numerics", []))})
          == CARDS["numeric_thresholds_introduced"])

    # ★★ B5-0 결핍 범위를 넘지 않았는가 — event_definition 을 조용히 만들지 않았다
    check("★★ B5-0 결핍이 재검사로 둘이 됐다",
          inv["RULE-0025"]["missing_components"] == ["event_definition", "time_window"],
          str(inv["RULE-0025"]["missing_components"]))
    check("★★ 그 위에 구조 미결이 별도 축으로 기록됐다",
          any(x["canonical_rule_id"] == "RULE-0025" and x["axis"] == "semantic_scope"
              for x in json.load(open(os.path.join(_ROOT, "rules",
                                                   "definition_inventory.json"),
                                      encoding="utf-8"))["source_semantics_unresolved"]))
    check("★★ 이 규칙의 카드가 셋이고 의미 → 사건 → 기간 순서다",
          [c["decision_unit"] for c in C
           if c["decision_unit"].startswith("RULE-0025")]
          == ["RULE-0025::semantic_scope", "RULE-0025::event_definition",
              "RULE-0025::time_window"])
    check("★★ 구조 미결 축은 정의 성분 어휘와 겹치지 않는다",
          "semantic_scope" not in json.load(
              open(os.path.join(_ROOT, "rules", "definition_inventory.json"),
                   encoding="utf-8"))["allowed_missing_components"])
    check("★★ 「끊김」 을 새로 정의하지 않았다고 명시한다",
          "새로 정의하지 않는다" in d14["decision"]
          and any("event_definition 을 생성하지 않는다" in b for b in d14["boundaries"]))
    check("★★ scope_limit 이 현재 사실로 정정됐다 — 낡은 전제가 남아 있지 않다",
          "scope_limit" in d14
          and "사건 정의와 기간 둘로 정정됐고" in d14["scope_limit"]
          and "기간 하나뿐" not in d14["scope_limit"].replace("'정의 결핍이 기간 하나뿐'", ""))
    check("★★ 무엇이 틀렸는지 명시한다 — 값이 아니라 전제였다",
          "값이 아니라" in d14["scope_limit"] and "과거 전제" in d14["scope_limit"])
    check("★★ 판정값 자체는 보존한다고 적는다",
          "기간 판정값은 보존하되" in d14["scope_limit"])
    check("★★ 배제 대상으로 인용된 값은 채택값이 아니라고 선언했다",
          any(x["token"] == "0" and x["role"] == "quoted_phrase"
              and "채택한 값이 아니다" in x["why"] for x in d14["declared_numerics"]))
    check("★★ 관측 공백은 횟수를 늘리지 않는다",
          any("관측 자체가 생성되지 않은 날은 횟수를 증가시키지 않는다" in b
              for b in d14["boundaries"]))
    check("★★ 거래일 경과가 아니라 관측을 센다",
          any("거래일 경과를 세지 않는다" in b for b in d14["boundaries"]))
    # ★★ 의미 판정이 아래 단계를 선취하지 않았다
    d_sem = by["RULE-0025::semantic_scope"]["cio_decision"]
    check("★★ 카드 14 RULE-0025::semantic_scope 에 판정이 기록됐다",
          d_sem and d_sem["decided_by"] == "CIO")
    check("★★ 의미 귀속만 닫았다고 명시한다",
          "의미 귀속만 닫는다" in " ".join(d_sem["boundaries"]))
    check("★★ 의미 판정 자신은 사건 정의를 선취하지 않았다",
          any("여전히 미결이다" in b for b in d_sem["boundaries"])
          and "카드 15" in by["RULE-0025::event_definition"]["cio_decision"]["source"])
    check("★★ 기간을 새로 정하지 않았다 — 판정문에 숫자가 없다",
          not DC.scan_numeric_tokens(d_sem["decision"])
          and d_sem["numeric_threshold_introduced"] is False)
    check("★★ 두 번째 읽기를 배제한다고 적는다",
          "의미로 해석하지 않는다" in d_sem["decision"])
    check("★★ 외부 자료를 쓰지 않았다고 기록한다",
          any("외부 자료를 쓰지 않았다" in b for b in d_sem["boundaries"]))
    # ★★ 상태 전이 — 하나 풀렸다고 아래까지 풀리지 않는다
    tw = by["RULE-0025::time_window"]
    prs = {p["unit"]: p["status"] for p in
           ([tw["prerequisite"]] if isinstance(tw["prerequisite"], dict)
            else tw["prerequisite"])}
    check("★★ 두 선행이 모두 충족됐다",
          prs == {"RULE-0025::semantic_scope": "충족됨",
                  "RULE-0025::event_definition": "충족됨"}, str(prs))
    check("★★ 보류가 해제됐다 — 판정값은 그대로다",
          not tw["execution_status"].startswith("보류")
          and tw["blocked_by"] is None
          and "3 개 연속 거래일" in tw["cio_decision"]["decision"])
    check("★★ 해제됐어도 evaluator 연결은 아니라고 적힌다",
          "evaluator 연결은 여전히 금지" in tw["execution_status"])
    check("★★ 선행 이력은 해제 후에도 남아 있다",
          all(p.get("reason") for p in
              ([tw["prerequisite"]] if isinstance(tw["prerequisite"], dict)
               else tw["prerequisite"])))
    check("★★ 사건 정의 카드의 선행은 이제 충족됐다 — 다음 판정 대상이다",
          by["RULE-0025::event_definition"]["blocked_by"] is None)

    check("★★ 원문 조건은 그대로다",
          by["RULE-0025::time_window"]["original_condition"]["RULE-0025"]
          == next(r["condition_text"] for r in CANON["canonical_rules"]
                  if r["canonical_rule_id"] == "RULE-0025"))


SUITES = [test_coverage_and_order, test_no_decisions, test_cio_context_no_numbers,
          test_obsolescence, test_group_state, test_upstream_untouched, test_card2_scope,
          test_shared_group_decision, test_g2_event_definition, test_rule_specific_cards,
          test_fail_closed_publish, test_sign_boundary_role,
          test_quoted_baseline_vs_new_threshold, test_rule0022_graph,
          test_observation_frequency, test_build_purity, test_rule0017_structure,
          test_rule0020_event]


def main():
    print("B5-2B decision cards — 불변식 회귀")
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
