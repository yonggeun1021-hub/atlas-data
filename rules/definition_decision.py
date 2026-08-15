"""B5-1 — Definition Resolution Decision. **판정과 질문만 만든다.**

⛔ 정의를 만들지 않는다 · 후보값 금지 · 권장값 금지 · 제안 정의 금지.
      candidate_values: [2, 3, 5]      ⛔
      recommended_value: 3             ⛔
      proposed_definition: "3일…"      ⛔
   질문까지만 허용된다.

⛔ decompose_full.json · B3 canonical · B4 artifacts · B5-0 inventory 수정 금지.
   canonical definition_status=UNDEFINED 를 DEFINED 로 바꾸지 않는다.

★ resolution_status 어휘는 2개뿐이다 (CIO 확정 B5-1)

    RESOLVABLE_FROM_CANON     기존 정본 어딘가에 **이미 완전한 정의가 존재**하고
                              provenance 로 연결할 수 있다. 새 숫자·기간·산식·대상 목록을
                              **선택할 필요가 없어야** 한다.
    REQUIRES_CIO_DEFINITION   현재 정본만으로 deterministic definition 을 만들 수 없다.
                              값을 채우려면 CIO 의 새 정책/전략 판단이 필요하다.

  ⛔ DATA_MISSING · PARSER_MISSING · SCHEDULED_FOR_REVIEW · PARTIALLY_DEFINED 같은
     status 를 만들지 않는다. 그것들은 resolution 가능성이 아니라 **왜 막혔는지**의
     속성이며, 이미 missing_components · resolution_evidence · observed_situations 가
     그 정보를 보존하고 있다.

★ definition resolution 과 data capability 는 **별도 축**이다.
  데이터 수집기가 없다는 이유만으로 REQUIRES_CIO_DEFINITION 이 되지 않는다.
  따라서 질문은 **semantic component 에서만** 생성하고 `data_source` 는 제외한다.
  각 항목의 판정 근거가 data_source 를 뺀 뒤에도 남는지 빌드 시점에 검증한다.

★ 질문은 B5-0 의 missing_components 로부터 **추적 가능**해야 한다.
  `time_window` 가 없는데 갑자기 "3거래일 또는 5거래일 중?" 이라고 물으면 FAIL 이다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INV = os.path.join(ROOT, "rules", "definition_inventory.json")
CANON = os.path.join(ROOT, "rules", "canonical_rules.json")
DECOMP = os.path.join(ROOT, "rules", "decompose_full.json")
OUT = os.path.join(ROOT, "rules", "definition_decision.json")

RESOLVABLE = "RESOLVABLE_FROM_CANON"
REQUIRES = "REQUIRES_CIO_DEFINITION"
STATUS = {RESOLVABLE, REQUIRES}

# data capability 축 — 판정 근거에서 제외한다
DATA_AXIS = {"data_source"}

# B5-0 의 정의 성분 어휘 — 구조 미결 축이 여기 섞이지 않는지 확인하는 데 쓴다
MISSING_COMPONENT_TOKENS = {"threshold", "time_window", "comparison_baseline",
                            "observation_frequency", "aggregation",
                            "event_definition", "data_source"}


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


# ── 사람이 작성한 질문 — 각 질문은 어느 missing_component 에서 나왔는지 밝힌다 ──
#   ⛔ 후보값·권장값·범위를 넣지 않는다. 숫자는 원문에 있는 것만 쓸 수 있다.
QUESTIONS = {
    "RULE-0002": [
        ("threshold", "'하락'의 폭을 어떤 기준으로 정의할 것인가?"),
        ("time_window", "'전환'을 선언하기까지의 기간을 어떻게 정의할 것인가?"),
        ("comparison_baseline", "무엇 대비 하락으로 볼 것인가?"),
    ],
    "RULE-0018": [
        ("threshold", "'하락'의 폭을 어떤 기준으로 정의할 것인가?"),
        ("time_window", "'전환'을 선언하기까지의 기간을 어떻게 정의할 것인가?"),
        ("comparison_baseline", "무엇 대비 하락으로 볼 것인가?"),
    ],
    "RULE-0004": [
        ("threshold", "'하향'의 폭을 어떤 기준으로 정의할 것인가?"),
        ("comparison_baseline", "무엇 대비 하향으로 볼 것인가?"),
    ],
    "RULE-0009": [
        ("event_definition", "'박스권 돌파'와 '재확인'을 어떤 기계적 조건으로 정의할 것인가?"),
        ("threshold", "박스권 상단과 돌파 폭을 어떤 기준으로 정의할 것인가?"),
        ("time_window", "'재확인'에 필요한 기간을 어떻게 정의할 것인가?"),
    ],
    "RULE-0010": [
        ("event_definition", "'상대강도'와 '열위'를 어떤 기계적 조건으로 정의할 것인가?"),
        ("time_window", "'지속'의 기간을 어떻게 정의할 것인가?"),
        ("comparison_baseline", "상대강도를 어떤 기준선 대비로 산출할 것인가?"),
    ],
    "RULE-0011": [
        ("observation_frequency", "고객 집중도를 어느 관측 주기로 볼 것인가?"),
    ],
    "RULE-0012": [
        ("event_definition", "'호실적'과 '선반영'을 어떤 기계적 조건으로 정의할 것인가?"),
        ("threshold", "주가 하락의 폭을 어떤 기준으로 정의할 것인가?"),
        ("time_window", "판정에 쓰는 기간을 어떻게 정의할 것인가?"),
    ],
    "RULE-0015": [
        ("threshold", "'하향'의 폭을 어떤 기준으로 정의할 것인가?"),
        ("comparison_baseline", "무엇 대비 하향으로 볼 것인가?"),
    ],
    "RULE-0016": [
        ("time_window", "'실적 전'의 기간을 어떻게 정의할 것인가?"),
        ("threshold", "'포지션 제한'의 수준을 어떤 기준으로 정의할 것인가?"),
    ],
    "RULE-0017": [
        ("event_definition", "'취소'와 '축소'를 어떤 기계적 조건으로 정의할 것인가?"),
        ("threshold", "'축소'의 폭을 어떤 기준으로 정의할 것인가?"),
    ],
    "RULE-0019": [
        ("event_definition", "'상대강도'와 '열위'를 어떤 기계적 조건으로 정의할 것인가?"),
        ("time_window", "'지속'의 기간을 어떻게 정의할 것인가?"),
        ("comparison_baseline", "상대강도를 어떤 기준선 대비로 산출할 것인가?"),
    ],
    "RULE-0020": [
        ("event_definition", "'공급 확대'와 '미확인'을 어떤 기계적 조건으로 정의할 것인가?"),
        ("threshold", "'확대'의 폭을 어떤 기준으로 정의할 것인가?"),
    ],
    "RULE-0021": [
        ("event_definition", "'유의미 하회'를 어떤 기준으로 정의할 것인가?"),
    ],
    "RULE-0022": [
        ("threshold", "'급둔화'의 '급'을 어떤 기준으로 정의할 것인가?"),
        ("time_window", "어느 기간을 보고 판정할 것인가?"),
        ("comparison_baseline", "무엇 대비 둔화로 볼 것인가?"),
    ],
    "RULE-0025": [
        ("event_definition", "무엇을 한 번의 '끊김' 관측으로 볼 것인가?"),
        ("time_window", "'연속'을 몇 거래일로 정의할 것인가?"),
    ],
}

# ★★ 원문 구조 미결에서 나오는 질문 — 정의 성분이 아니다.
#   B5-0 의 source_semantics_unresolved 로부터 추적된다.
#   ⛔ 두 읽기 중 하나를 고르지 않는다. 고르는 것은 CIO 다.
SEMANTIC_QUESTIONS = {
    "RULE-0017": [
        ("semantic_scope",
         "'예약 취소'와 'LTA 축소'가 동시에 충족돼야 하는가, 어느 하나로 충분한가?"),
    ],
    "RULE-0025": [
        ("semantic_scope",
         "'연속'이 '기관 순매수'에 걸리는가, '끊김'에 걸리는가?"),
    ],
}
SEMANTIC_AXIS = "source_semantics"
COMPONENT_AXIS = "definition_component"


def build(out_path: str = OUT):
    inv = json.load(open(INV, encoding="utf-8"))
    canon = json.load(open(CANON, encoding="utf-8"))
    recs = {r["canonical_rule_id"]: r for r in canon["canonical_rules"]}

    errs, items = [], []
    seen = set()
    for it in inv["items"]:
        rid = it["canonical_rule_id"]
        seen.add(rid)
        missing = it["missing_components"]
        semantic = [m for m in missing if m not in DATA_AXIS]

        # ★ 판정 근거가 data capability 축에만 기대고 있지 않은가
        if not semantic:
            errs.append(f"{rid}: 판정 근거가 data_source 뿐이다 — "
                        f"데이터 수집기 부재만으로 {REQUIRES} 를 줄 수 없다")

        qs = QUESTIONS.get(rid, [])
        if not qs:
            errs.append(f"{rid}: decision_required 가 비어 있다")

        # ★★ 구조 미결 질문 — B5-0 의 source_semantics_unresolved 로만 추적된다
        sq = SEMANTIC_QUESTIONS.get(rid, [])
        declared_axes = {x["axis"] for x in inv.get("source_semantics_unresolved", [])
                         if x["canonical_rule_id"] == rid}
        for comp, q in sq:
            if comp not in declared_axes:
                errs.append(f"{rid}: 구조 미결 질문이 B5-0 에 없는 축 {comp!r} 에서 나왔다")
            if comp in MISSING_COMPONENT_TOKENS:
                errs.append(f"{rid}: ★★ 구조 미결 축 {comp!r} 가 정의 성분과 겹친다")
            allowed = set(re.findall(r"\d+", it["condition_text"]))
            bad = [n for n in re.findall(r"\d+", q) if n not in allowed]
            if bad:
                errs.append(f"{rid}: ★★ 구조 미결 질문에 원문 밖 숫자 {bad}")
        if declared_axes and not sq:
            errs.append(f"{rid}: B5-0 이 구조 미결을 기록했는데 질문이 없다")

        # ★ 질문 → missing_component 추적 가능성 (양방향)
        for comp, q in qs:
            if comp not in missing:
                errs.append(f"{rid}: 질문이 B5-0 결핍 항목에 없는 {comp!r} 에서 나왔다")
            if comp in DATA_AXIS:
                errs.append(f"{rid}: data capability 축({comp})에서 질문을 만들었다")
            # ★★ 후보값·범위를 넣지 않았는가 — 숫자는 원문에 있는 것만
            allowed = set(re.findall(r"\d+", it["condition_text"]))
            bad = [n for n in re.findall(r"\d+", q) if n not in allowed]
            if bad:
                errs.append(f"{rid}: ★★ 질문에 원문 밖 숫자 {bad} — 후보값을 만든 것이다")
        covered = {c for c, _ in qs}
        if set(semantic) - covered:
            errs.append(f"{rid}: 질문이 없는 결핍 항목 {sorted(set(semantic) - covered)}")

        items.append({
            "canonical_rule_id": rid,
            "occurrence_id": it["occurrence_id"],
            "condition_text": it["condition_text"],
            "resolution_status": REQUIRES,
            "judgment_basis": semantic,                 # data_source 를 뺀 뒤 남는 근거
            "data_capability_axis": [m for m in missing if m in DATA_AXIS],
            "decision_required": (
                [{"from_component": c, "question": q, "axis": COMPONENT_AXIS}
                 for c, q in qs]
                + [{"from_component": c, "question": q, "axis": SEMANTIC_AXIS}
                   for c, q in sq]),
        })

    extra = set(QUESTIONS) - seen
    if extra:
        errs.append(f"inventory 에 없는 rule 에 질문을 만들었다: {sorted(extra)}")

    payload = {
        "artifact": "B5-1 Definition Resolution Decision",
        "status": "inactive preparation",
        "authority": False,
        "consumable_by_evaluator": False,
        "definitions_created": 0,
        "candidate_values_created": 0,
        "status_vocabulary": sorted(STATUS),
        "vocabulary_note": (
            "2개뿐이다. DATA_MISSING · PARSER_MISSING · SCHEDULED_FOR_REVIEW · "
            "PARTIALLY_DEFINED 같은 status 를 만들지 않는다 — 그것들은 resolution "
            "가능성이 아니라 왜 막혔는지의 속성이며 B5-0 이 이미 보존하고 있다."),
        "axis_note": (
            "definition resolution 과 data capability 는 별도 축이다. 질문은 semantic "
            "component 에서만 생성했고 data_source 는 제외했다. 데이터 수집기 부재만으로 "
            f"{REQUIRES} 를 주지 않는다."),
        "counts": {
            REQUIRES: len(items),
            RESOLVABLE: 0,
            "decision_questions": sum(len(i["decision_required"]) for i in items),
            "definition_component_questions": sum(
                1 for i in items for q in i["decision_required"]
                if q["axis"] == COMPONENT_AXIS),
            "source_semantics_questions": sum(
                1 for i in items for q in i["decision_required"]
                if q["axis"] == SEMANTIC_AXIS),
        },
        "judgment_note": (
            f"현재 B5-0 증거 기준 `source_has_resolution` 이 15/15 false 이므로 "
            f"{RESOLVABLE} 은 0건이다. RULE-0011 · RULE-0015 · RULE-0021 · RULE-0025 처럼 "
            f"거의 완성된 Rule 도 예외가 아니다 — **의미 있는 판단을 하나라도 추가해야 "
            f"완성된다면 같은 상태다.**"),
        "source_semantics_note": (
            "★ 구조 미결에서 나온 질문은 정의 성분이 아니라 원문이 어느 규칙을 말하는지의 "
            "문제다. axis 로 구분해 두었고, B5-0 의 source_semantics_unresolved 로만 "
            "추적된다. 정의 성분 어휘에 섞지 않았다."),
        "boundary_case_note": (
            "RULE-0025 가 경계 사례다. 데이터는 확보돼 있고 며칠로 할지 선택하는 순간 "
            f"전략을 만든 것이므로 {RESOLVABLE} 이 아니다. "
            "★ 재검사 정정 — 한때 '빠진 것은 time_window 하나뿐' 으로 적혀 있었으나 "
            "사실이 아니었다. 무엇을 한 번의 단절 관측으로 볼지가 함께 빠져 있었고, "
            "그 위에 원문 구조 미결까지 있다."),
        "decided_against": {
            "definition_inventory_sha256": _sha(INV),
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
    print(f"[B5-1] {OUT}")
    print(f"  {REQUIRES:24s} {p['counts'][REQUIRES]}")
    print(f"  {RESOLVABLE:24s} {p['counts'][RESOLVABLE]}")
    print(f"  decision questions       {p['counts']['decision_questions']}")
    print(f"  definitions_created      {p['definitions_created']} · "
          f"candidate_values_created {p['candidate_values_created']}")
    if errs:
        print(f"\n★ 위반 {len(errs)}건")
        for e in errs:
            print("  ✗", e)
        sys.exit(1)
    print("  ✅ 위반 0")


if __name__ == "__main__":
    main()
