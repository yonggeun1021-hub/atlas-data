"""B5-1A — Data-Source Ambiguity Adjudication. **관계만 판정한다.**

★ 왜 필요한가 — B5-0 의 `data_source` 하나가 서로 다른 두 가지를 함께 담고 있다.
      semantic target 미정   : 누구의 capex 인지, 어떤 hyperscaler universe 인지
      data capability 부재   : 대상은 아는데 collector/parser 가 없다
  이 상태로 34개 질문에 답하면 `RULE-0004` 처럼 **"하향 폭은 정의했는데 정작
  누구의 capex 인지 모르는" 반쪽짜리 DEFINED** 가 생긴다.

⛔ B5-0 vocabulary 를 소급 수정하지 않는다 · missing_components 수정 금지 ·
   새 vocabulary 금지 · 정의 생성 금지 · 후보값 금지 ·
   **대상 목록이나 데이터 소스를 새로 선택하지 않는다.**
   hyperscaler 를 임의로 지정하는 순간 그것은 이미 정의 생성이다.

★ 분류 어휘는 CIO 지시문의 표현을 그대로 쓴다. 토큰을 새로 만들지 않는다.
      "semantic target ambiguity"
      "data capability gap"
      "both"
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INV = os.path.join(ROOT, "rules", "definition_inventory.json")
DEC = os.path.join(ROOT, "rules", "definition_decision.json")
CANON = os.path.join(ROOT, "rules", "canonical_rules.json")
DECOMP = os.path.join(ROOT, "rules", "decompose_full.json")
OUT = os.path.join(ROOT, "rules", "data_source_ambiguity.json")

SEMANTIC = "semantic target ambiguity"
CAPABILITY = "data capability gap"
BOTH = "both"
CLASSES = {SEMANTIC, CAPABILITY, BOTH}


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


# ── 사람이 판독한 분류 ───────────────────────────────────────────────────
#   `question` 은 semantic 쪽이 걸린 건에만 붙인다. ⛔ 답을 적지 않는다.
ADJUDICATION = {
    "RULE-0002": dict(
        cls=BOTH,
        why=("어떤 DRAM ASP 계열을 보는지 원문에 없다(계약가·현물가, 집계 주체). "
             "동시에 Atlas 는 산업 가격 원천을 수집하지 않으므로, 어느 계열을 택하든 "
             "capability 는 별도로 없다 — 두 결핍이 독립적으로 성립한다."),
        question="'DRAM ASP'를 어느 가격 계열로 정의할 것인가?"),
    "RULE-0018": dict(
        cls=BOTH,
        why="같은 문구를 가진 다른 종목의 조건이며 결핍 성격도 동일하다.",
        question="'DRAM ASP'를 어느 가격 계열로 정의할 것인가?"),
    "RULE-0004": dict(
        cls=SEMANTIC,
        why=("★ **누구의 capex 인지**가 원문에 없다. 자사 capex 인지 고객 capex 인지에 따라 "
             "읽어야 할 값이 완전히 달라진다. "
             "⚠ capability 는 대상이 정해지기 전에는 **판정할 수 없다** — 아직 없다고 "
             "적으면 확인하지 않은 사실을 기록하는 것이 된다."),
        question="'capex 하향'의 측정 대상을 누구의 capex 로 정의할 것인가?"),
    "RULE-0010": dict(
        cls=CAPABILITY,
        why=("측정 대상은 명확하다 — 두 종목의 주가다. 미국 확정 가격 원천이 확보되지 "
             "않았을 뿐이다. 산식·'열위'·'지속' 의 미정은 이미 event_definition · "
             "time_window · comparison_baseline 으로 별도 표시돼 있다."),
        question=None),
    "RULE-0011": dict(
        cls=CAPABILITY,
        why=("측정 대상은 명확하다 — 해당 기업의 고객 집중도다. 공시 본문 재무·주석 수치를 "
             "파싱하는 구현이 없을 뿐이다. 관측 주기 미정은 observation_frequency 로 별도 표시."),
        question=None),
    "RULE-0015": dict(
        cls=BOTH,
        why=("★ '하이퍼스케일러' 의 대상 범위가 원문에 없다. 동시에, 어느 기업 집합을 택하든 "
             "capex 는 공시 본문 재무 수치이고 그 파싱 구현이 없다 — capability 결핍은 "
             "대상 선택과 무관하게 성립한다."),
        question="'하이퍼스케일러'의 대상 범위를 어떻게 정의할 것인가?"),
    "RULE-0017": dict(
        cls=BOTH,
        why=("★ '예약' 과 'LTA' 를 어느 관측 대상에서 읽는지 원문에 없다. 그리고 고객 확약 "
             "정보는 공시 본문이거나 비공개라 관측 경로 자체가 구현돼 있지 않다."),
        question="'예약'과 'LTA'를 어느 관측 대상에서 읽을 것인가?"),
    "RULE-0020": dict(
        cls=BOTH,
        why=("★ 'HBM 공급 확대' 의 관측 대상이 원문에 없다(생산 능력·출하·고객 인증 중 무엇인지). "
             "그리고 어느 것이든 공시 본문 파싱이 구현돼 있지 않다."),
        question="'HBM 공급 확대'를 어느 관측 대상으로 볼 것인가?"),
    "RULE-0021": dict(
        cls=CAPABILITY,
        why=("★ CIO 가 지목한 확인 대상 — metric 은 명확하다. 해당 기업이 실적에서 공표하는 "
             "성장률(cc)이며 대상 모호성이 없다. 수집기가 없을 뿐이다. "
             "'유의미' 의 미정은 event_definition 으로 별도 표시돼 있다."),
        question=None),
    "RULE-0022": dict(
        cls=CAPABILITY,
        why=("★ CIO 가 지목한 확인 대상 — metric 은 명확하다. RPO 는 해당 기업이 공표하는 "
             "재무 항목이며 대상 모호성이 없다. 재무 본문 파싱 구현이 없을 뿐이다."),
        question=None),
}


def build(out_path: str = OUT):
    inv = json.load(open(INV, encoding="utf-8"))
    dec = json.load(open(DEC, encoding="utf-8"))
    inv_by = {i["canonical_rule_id"]: i for i in inv["items"]}

    # ★ 대상 집합을 B5-0 원본에서 다시 계산한다 (B5-1 view 를 입력으로 쓰지 않는다)
    scope = [i["canonical_rule_id"] for i in inv["items"]
             if "data_source" in i["missing_components"]]

    errs, items = [], []
    for rid in scope:
        a = ADJUDICATION.get(rid)
        if a is None:
            errs.append(f"{rid}: data_source 결핍인데 판정이 없다"); continue
        if a["cls"] not in CLASSES:
            errs.append(f"{rid}: 허용 밖 분류 {a['cls']!r}")

        ct = inv_by[rid]["condition_text"]
        allowed_num = set(re.findall(r"\d+", ct))
        # 라틴 문자 토큰은 원문에 있는 것만 쓸 수 있다 — 대상 목록을 지어넣는 경로를 막는다
        allowed_lat = {t.lower() for t in re.findall(r"[A-Za-z]{2,}", ct)}

        q = a.get("question")
        if a["cls"] in (SEMANTIC, BOTH) and not q:
            errs.append(f"{rid}: semantic 결핍인데 질문이 없다")
        if a["cls"] == CAPABILITY and q:
            errs.append(f"{rid}: capability 전용인데 semantic 질문을 만들었다")
        if q:
            bad_n = [n for n in re.findall(r"\d+", q) if n not in allowed_num]
            if bad_n:
                errs.append(f"{rid}: ★★ 질문에 원문 밖 숫자 {bad_n} — 후보값을 만든 것이다")
            bad_l = [t for t in re.findall(r"[A-Za-z]{2,}", q)
                     if t.lower() not in allowed_lat]
            if bad_l:
                errs.append(f"{rid}: ★★ 질문에 원문 밖 대상명 {bad_l} — "
                            f"대상 목록을 선택한 것이다")

        items.append({
            "canonical_rule_id": rid,
            "occurrence_id": inv_by[rid]["occurrence_id"],
            "condition_text": ct,
            # ⛔ B5-0 을 수정하지 않는다. 참조만 한다.
            "b5_0_missing_components": inv_by[rid]["missing_components"],
            "data_source_deficiency_class": a["cls"],
            "why": a["why"],
            "additional_decision_required": (
                [{"from_component": "data_source", "question": q}] if q else []),
        })

    extra = set(ADJUDICATION) - set(scope)
    if extra:
        errs.append(f"data_source 결핍이 아닌 rule 을 판정했다: {sorted(extra)}")

    import collections
    dist = collections.Counter(i["data_source_deficiency_class"] for i in items)

    payload = {
        "artifact": "B5-1A Data-Source Ambiguity Adjudication",
        "status": "inactive preparation",
        "authority": False,
        "consumable_by_evaluator": False,
        "definitions_created": 0,
        "candidate_values_created": 0,
        "targets_selected": 0,
        "scope_note": ("B5-0 에서 `data_source` 로 표시된 Rule 만 대상이다. "
                       "관계만 판정하며 missing_components 는 수정하지 않는다."),
        "classification_vocabulary": sorted(CLASSES),
        "vocabulary_note": ("CIO 지시문의 표현을 그대로 쓴다. 새 토큰을 만들지 않았다."),
        "no_selection_note": ("⛔ 대상 목록·데이터 소스를 새로 선택하지 않았다. "
                              "hyperscaler 를 임의로 지정하면 그것은 이미 정의 생성이다. "
                              "semantic 결핍에는 **질문만** 붙였다."),
        "counts": {"in_scope": len(items),
                   SEMANTIC: dist[SEMANTIC], CAPABILITY: dist[CAPABILITY], BOTH: dist[BOTH],
                   "additional_questions": sum(len(i["additional_decision_required"])
                                               for i in items)},
        "cio_checklist_result": {
            "RULE-0004 누구의 capex 인가": SEMANTIC,
            "RULE-0015 hyperscaler universe": BOTH,
            "RULE-0002/0018 DRAM ASP series": BOTH,
            "RULE-0017 HBM 예약·LTA 관측 대상": BOTH,
            "RULE-0020 HBM 공급 확대 관측 대상": BOTH,
            "RULE-0021/0022 collector 부재에 가까운가": f"{CAPABILITY} — 그렇다",
        },
        "capability_undetermined_note": (
            "RULE-0004 는 대상이 정해지기 전에는 capability 를 판정할 수 없어 "
            f"'{BOTH}' 로 적지 않았다. 아직 확인하지 않은 사실을 기록하지 않는다."),
        "decided_against": {
            "definition_inventory_sha256": _sha(INV),
            "definition_decision_sha256": _sha(DEC),
            "canonical_rules_sha256": _sha(CANON),
            "decompose_full_sha256": _sha(DECOMP),
        },
        "items": items,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return payload, errs


def main():
    p, errs = build()
    print(f"[B5-1A] {OUT}")
    c = p["counts"]
    print(f"  in scope {c['in_scope']} — {SEMANTIC} {c[SEMANTIC]} · "
          f"{CAPABILITY} {c[CAPABILITY]} · {BOTH} {c[BOTH]}")
    print(f"  additional semantic questions {c['additional_questions']}")
    print(f"  definitions {p['definitions_created']} · candidates "
          f"{p['candidate_values_created']} · targets selected {p['targets_selected']}")
    if errs:
        print(f"\n★ 위반 {len(errs)}건")
        for e in errs:
            print("  ✗", e)
        sys.exit(1)
    print("  ✅ 위반 0")


if __name__ == "__main__":
    main()
