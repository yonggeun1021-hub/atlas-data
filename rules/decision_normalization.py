"""B5-2A — CIO Decision Normalization. **묶기만 한다.**

목적은 하나다 — 40번 독립적으로 판단해서 **서로 모순되는 definition 이 생기는 것**을 막는다.

⛔ 40개 질문에 답하지 않는다 · 새 threshold·기간·산식·대상·후보값 생성 금지 ·
   data source 선택 금지 · 기존 artifact 수정 금지 · evaluator 연결 금지.

★ 묶는 기준은 **동일한 투자 의미**다. 문장 형태가 닮았다는 이유로 묶지 않는다.
  "기계적으로 만들 수 있다"는 이유로 묶는 것도 금지다 —
  `기관 순매수 연속 끊김` 의 기간은 구현 난이도가 아니라 그 Rule 이 원래 잡으려던
  투자 논리에 맞아야 한다.

★ 반대로 기업 고유 사업지표는 억지로 합치지 않는다.
  `Azure 유의미 하회` · `RPO 급둔화` · `HBM 예약·LTA 축소` 는 서로 다른 사업지표다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEC = os.path.join(ROOT, "rules", "definition_decision.json")
AMB = os.path.join(ROOT, "rules", "data_source_ambiguity.json")
CANON = os.path.join(ROOT, "rules", "canonical_rules.json")
OUT = os.path.join(ROOT, "rules", "decision_normalization.json")


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def qid(rule, comp):
    return f"{rule}::{comp}"


# ── ① CIO 가 직접 예로 든, 동일 투자 의미로 확인된 그룹 ────────────────────
SHARED_GROUPS = [
    dict(
        group_id="G1",
        label="DRAM ASP 하락 전환",
        member_rules=["RULE-0002", "RULE-0018"],
        member_questions=[qid(r, c) for r in ("RULE-0002", "RULE-0018")
                          for c in ("threshold", "time_window",
                                    "comparison_baseline", "data_source")],
        shared_decision_units=["threshold", "time_window",
                               "comparison_baseline", "data_source"],
        shared_semantics_reason=(
            "동일 산업 신호다. 두 종목의 조건문이 글자까지 같고, 측정하는 사실도 "
            "같은 산업 가격 계열을 가리킨다. 종목이 둘이라는 이유로 하락 폭·기간·기준·"
            "가격 계열을 따로 만들 이유가 없다. "
            "⚠ 단 B4-1 에서 두 식별자는 병합하지 않기로 남아 있다 — "
            "**정의를 공유하는 것과 하나로 합치는 것은 다르다.**"),
        must_remain_rule_specific=[],
    ),
    dict(
        group_id="G2",
        label="상대강도 열위 지속",
        member_rules=["RULE-0010", "RULE-0019"],
        member_questions=[qid(r, c) for r in ("RULE-0010", "RULE-0019")
                          for c in ("event_definition", "time_window",
                                    "comparison_baseline")],
        shared_decision_units=["event_definition", "time_window", "comparison_baseline"],
        shared_semantics_reason=(
            "비교 대상만 다르고 **계산 방식은 같다** — 상대강도를 어떻게 산출하고, "
            "'열위' 를 어떻게 판정하고, '지속' 을 어떻게 세는가는 공통 정책으로 둘 수 있다. "
            "★ 비교 대상 자체는 원문에 이미 명시돼 있으므로 결정 대상이 아니다."),
        must_remain_rule_specific=[],
    ),
]

# ── ② 공유 가능성이 보이지만 **선행 결정에 의존**하는 후보 ──────────────────
#   ⛔ 확정된 그룹이 아니다. CIO 확인이 필요하다.
CONDITIONAL_GROUPS = [
    dict(
        group_id="C1",
        label="capex 하향",
        member_rules=["RULE-0004", "RULE-0015"],
        member_questions=[qid("RULE-0004", "threshold"),
                          qid("RULE-0004", "comparison_baseline"),
                          qid("RULE-0015", "threshold"),
                          qid("RULE-0015", "comparison_baseline")],
        shared_decision_units=["threshold", "comparison_baseline"],
        shared_semantics_reason=(
            "둘 다 '설비투자 축소' 라는 같은 종류의 사업 신호를 본다. 하향 폭과 비교 기준을 "
            "공통 정책으로 둘 여지가 있다."),
        precondition=(
            "★ RULE-0004 의 측정 대상이 아직 미정이다(누구의 capex 인가). "
            "그 결정이 RULE-0015 와 **같은 측정 유형**으로 귀결될 때만 공유가 성립한다. "
            "자사 capex 축소와 고객 capex 축소는 같은 폭이라도 투자 의미가 다를 수 있다. "
            "⛔ 지금 확정하지 않는다."),
        must_remain_rule_specific=[
            qid("RULE-0004", "data_source"),
            qid("RULE-0015", "data_source"),
        ],
        note=("B4-0 은 두 조건을 판정 함수 차이로 서로 다르다고 판정했다 — "
              "한쪽에만 개수 조건이 있다. 서로 다른 조건인 것과 정의 구성요소를 공유하는 "
              "것은 다른 층이지만, 이 후보를 확정 그룹으로 올리지 않은 이유 중 하나다."),
    ),
]

# ── ③ 억지로 합치지 않은 것 — 사유를 남긴다 ───────────────────────────────
RULE_SPECIFIC = [
    dict(questions=[qid("RULE-0009", c) for c in
                    ("event_definition", "threshold", "time_window")],
         reason=("진입 패턴 정의는 정본이 Entry Language 로 별도 미정 처리한 영역이다. "
                 "UNDEFINED 25건 중 이 영역에 속한 것은 이 Rule 뿐이라 공유 상대가 없다.")),
    dict(questions=[qid("RULE-0011", "observation_frequency")],
         reason="고객 집중도의 관측 주기는 이 기업의 공시 구조에 달린 문제다."),
    dict(questions=[qid("RULE-0012", c) for c in
                    ("event_definition", "threshold", "time_window")],
         reason=("'호실적' 은 매출 성장·마진·가이던스 등 여러 사업지표가 동시에 걸리는 "
                 "복합 판정이다. 단일 지표로 축소하면 원래 의미가 훼손된다. "
                 "공유 상대를 만들지 않는다.")),
    dict(questions=[qid("RULE-0016", c) for c in ("time_window", "threshold")],
         reason=("'실적 전 포지션 제한' 은 이벤트 전 노출 축소라는 고유 정책이다. "
                 "다른 Rule 의 기간·임계값과 의미가 다르다.")),
    dict(questions=[qid("RULE-0017", c) for c in
                    ("event_definition", "threshold", "data_source")],
         reason=("고객 확약(예약·LTA)의 취소·축소는 기업 고유 사업지표다. "
                 "CIO 가 각각 정의해야 한다고 명시한 항목이다.")),
    dict(questions=[qid("RULE-0020", c) for c in
                    ("event_definition", "threshold", "data_source")],
         reason=("자사 공급 능력 확대 여부는 RULE-0017 의 고객 확약 취소와 **측정 대상이 "
                 "반대 방향**이다. 같은 HBM 이라는 이유로 묶지 않는다.")),
    dict(questions=[qid("RULE-0021", "event_definition")],
         reason=("'유의미 하회' 는 이 기업이 공표하는 성장률 지표에 대한 판단이다. "
                 "CIO 가 각각 정의해야 한다고 명시한 항목이다.")),
    dict(questions=[qid("RULE-0022", c) for c in
                    ("threshold", "time_window", "comparison_baseline")],
         reason=("'급둔화' 는 이 기업이 공표하는 계약 잔고 지표에 대한 판단이다. "
                 "CIO 가 각각 정의해야 한다고 명시한 항목이다.")),
    dict(questions=[qid("RULE-0025", "time_window")],
         reason=("수급 흐름의 단절을 보는 조건이다. 상대강도 '지속'(G2)과 문장 형태는 "
                 "닮았으나 투자 논리가 다르다 — 상대 성과 악화와 수급 국면 전환은 "
                 "서로 다른 신호다. 형태 유사성으로 묶지 않는다.")),
    dict(questions=[qid("RULE-0017", "semantic_scope")],
         reason=("★ 원문 구조 미결이다. 두 사건을 잇는 결합 표기의 실행 의미 문제이며 "
                 "정의 성분이 아니다. 다른 규칙과 묶을 대상이 없다 — 이 원문 하나의 "
                 "표기 문제다.")),
    dict(questions=[qid("RULE-0025", "semantic_scope")],
         reason=("★ 원문 구조 미결이다. 정의 성분이 아니라 원문이 어느 규칙을 말하는지의 "
                 "문제이므로, 어떤 정의 공유 그룹에도 넣지 않는다. 다른 규칙과 묶을 "
                 "대상 자체가 없다 — 이 원문 하나의 문장 구조 문제다.")),
    dict(questions=[qid("RULE-0025", "event_definition")],
         reason=("무엇을 한 번의 단절 관측으로 볼지의 문제다. '열위'(G2)·'미확인'·'취소' "
                 "처럼 상태 전이 술어를 정의하는 질문이지만, 대상이 수급 데이터로 "
                 "서로 달라 계산을 공유할 수 없다. 개별로 둔다.")),
]

# ── ④ 그룹이 아니라 관찰 — CIO 가 원하면 별도 결정 단위로 올릴 수 있다 ──────
OBSERVATIONS = [
    ("'지속' · '연속' 을 세는 단위(거래일/달력일)가 G2 와 RULE-0025 에 공통으로 걸린다. "
     "투자 의미는 다르지만 계수 단위 규약은 하나로 둘 여지가 있다. "
     "⛔ 이것을 결정 단위로 만들지 않았다 — 새 단위를 제안하는 것은 이 단계의 범위 밖이다."),
    ("정성 수식어를 정량화해야 하는 질문이 다수다('유의미'·'급'·'호'·'확대'·'축소'). "
     "CIO 가 각각 정의하라고 명시했으므로 묶지 않았다."),
    ("공유 그룹이 2개(+조건부 1개)뿐인 이유 — Watchlist 의 UNDEFINED Rule 대부분이 "
     "**기업 고유 사업지표**이기 때문이다. 산업 공통 신호는 DRAM ASP 계열과 상대강도 "
     "계열 둘뿐이다. 축약 폭이 크지 않은 것은 원천의 성질이다."),
]


def build(out_path: str = OUT):
    dec = json.load(open(DEC, encoding="utf-8"))
    amb = json.load(open(AMB, encoding="utf-8"))
    canon = {r["canonical_rule_id"]: r
             for r in json.load(open(CANON, encoding="utf-8"))["canonical_rules"]}

    # 40개 질문의 정본 집합을 상위 artifact 에서 다시 계산한다
    universe = {}
    for d in dec["items"]:
        for q in d["decision_required"]:
            universe[qid(d["canonical_rule_id"], q["from_component"])] = q["question"]
    for a in amb["items"]:
        for q in a["additional_decision_required"]:
            universe[qid(a["canonical_rule_id"], q["from_component"])] = q["question"]

    errs = []
    assigned, dup = set(), []
    groups = SHARED_GROUPS + CONDITIONAL_GROUPS

    def take(qs, where):
        for q in qs:
            if q not in universe:
                errs.append(f"{where}: 존재하지 않는 질문 {q}")
            elif q in assigned:
                dup.append(q)
            else:
                assigned.add(q)

    # 텍스트에 원문 밖 숫자·대상명이 섞이지 않았는가
    def textcheck(g, fields):
        allowed_n, allowed_l = set(), set()
        for r in g["member_rules"]:
            ct = canon[r]["condition_text"]
            allowed_n |= set(re.findall(r"\d+", ct))
            allowed_l |= {t.lower() for t in re.findall(r"[A-Za-z]{2,}", ct)}
        for f in fields:
            t = g.get(f) or ""
            bad_n = [n for n in re.findall(r"\d+", t) if n not in allowed_n
                     and not re.search(rf"(RULE-\d*{n}|B{n}\b|B\d-{n}\b|G{n}\b|C{n}\b)", t)]
            if bad_n:
                errs.append(f"{g['group_id']}.{f}: 원문 밖 숫자 {bad_n} — 값을 만든 것이다")
            bad_l = [x for x in re.findall(r"[A-Za-z]{2,}", t)
                     if x.lower() not in allowed_l
                     and not re.fullmatch(r"(RULE|NO_MERGE|DISTINCT|CIO|ASP|DRAM|HBM|LTA)", x)]
            if bad_l:
                errs.append(f"{g['group_id']}.{f}: 원문 밖 대상명 {bad_l} — 대상을 선택한 것이다")

    for g in groups:
        take(g["member_questions"], g["group_id"])
        take(g.get("must_remain_rule_specific", []), g["group_id"] + ".specific")
        textcheck(g, ("shared_semantics_reason", "precondition", "note"))
        if len(g["member_rules"]) < 2:
            errs.append(f"{g['group_id']}: 구성원이 2개 미만이면 그룹이 아니다")
        # 공유 단위가 실제로 모든 구성원에 존재하는가
        for r in g["member_rules"]:
            for u in g["shared_decision_units"]:
                if qid(r, u) not in universe:
                    errs.append(f"{g['group_id']}: {r} 에 {u} 질문이 없는데 공유 단위로 넣었다")

    for rs in RULE_SPECIFIC:
        take(rs["questions"], "rule_specific")
        if not rs["reason"]:
            errs.append("rule_specific 항목에 사유가 없다")

    if dup:
        errs.append(f"한 질문이 두 곳에 배정됐다: {sorted(set(dup))}")
    unassigned = sorted(set(universe) - assigned)
    if unassigned:
        errs.append(f"배정되지 않은 질문 {len(unassigned)}건: {unassigned}")

    shared_units = sum(len(g["shared_decision_units"]) for g in SHARED_GROUPS)
    cond_units = sum(len(g["shared_decision_units"]) for g in CONDITIONAL_GROUPS)
    specific = sum(len(rs["questions"]) for rs in RULE_SPECIFIC) \
        + sum(len(g.get("must_remain_rule_specific", [])) for g in groups)

    payload = {
        "artifact": "B5-2A CIO Decision Normalization",
        "status": "inactive preparation",
        "authority": False,
        "consumable_by_evaluator": False,
        "answers_created": 0,
        "definitions_created": 0,
        "candidate_values_created": 0,
        "targets_selected": 0,
        "purpose": ("40번 독립 판단으로 서로 모순되는 definition 이 생기는 것을 막는다. "
                    "새 Rule 을 만드는 작업이 아니라 중복 CIO 결정을 방지하는 작업이다."),
        "grouping_principle": (
            "동일한 **투자 의미**로만 묶는다. 문장 형태 유사성이나 구현 편의로 묶지 않는다. "
            "기업 고유 사업지표는 억지로 합치지 않는다."),
        "counts": {
            "questions_total": len(universe),
            "shared_groups": len(SHARED_GROUPS),
            "conditional_groups": len(CONDITIONAL_GROUPS),
            "questions_in_shared_groups": sum(len(g["member_questions"])
                                              for g in SHARED_GROUPS),
            "questions_in_conditional_groups": sum(len(g["member_questions"])
                                                   for g in CONDITIONAL_GROUPS),
            "rule_specific_questions": specific,
            "decision_units_if_conditional_shared": shared_units + cond_units + specific,
            "decision_units_if_conditional_rejected": shared_units + cond_units * 2 + specific,
        },
        "shared_decision_groups": SHARED_GROUPS,
        "conditional_share_candidates": CONDITIONAL_GROUPS,
        "must_remain_rule_specific": RULE_SPECIFIC,
        "observations": OBSERVATIONS,
        "decided_against": {
            "definition_decision_sha256": _sha(DEC),
            "data_source_ambiguity_sha256": _sha(AMB),
            "canonical_rules_sha256": _sha(CANON),
        },
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return payload, errs


def main():
    p, errs = build()
    c = p["counts"]
    print(f"[B5-2A] {OUT}")
    print(f"  질문 {c['questions_total']} → 결정 단위 "
          f"{c['decision_units_if_conditional_shared']} "
          f"(조건부 그룹 기각 시 {c['decision_units_if_conditional_rejected']})")
    print(f"  공유 그룹 {c['shared_groups']} · 조건부 후보 {c['conditional_groups']} · "
          f"Rule 고유 {c['rule_specific_questions']}")
    print(f"  answers {p['answers_created']} · definitions {p['definitions_created']} · "
          f"candidates {p['candidate_values_created']} · targets {p['targets_selected']}")
    if errs:
        print(f"\n★ 위반 {len(errs)}건")
        for e in errs:
            print("  ✗", e)
        sys.exit(1)
    print("  ✅ 위반 0")


if __name__ == "__main__":
    main()
