"""B4-0 — Dedup Equivalence Adjudication View.

⛔ merge 금지 · canonical ID 삭제/변경 금지 · survivor 선택 금지 ·
   source_occurrences 이동 금지 · condition_semantics/scope 를 canonical artifact 에
   쓰기 금지 · evaluator 연결 금지.

★ 이 파일은 25개 canonical record 사이에서 **어떤 pair 가 같은 Rule 일 가능성이 있는지**
  판정 가능한 형태로 보여주기만 한다. B3 artifact 는 읽기만 하고 수정하지 않는다.

★ 동일 Rule 의 필요조건 (CIO 확정 B4-0)
      rule_kind + measured_fact + predicate + scope   ← 넷이 전부 같아야 EQUIVALENT
  `subject` 는 필요조건이 아니다 — 서로 다른 종목이 하나의 산업 공통조건을 공유할
  가능성을 열어두고, 같은 종목이라고 같은 Rule 이라고도 보지 않는다.

★ 판정 어휘는 4개뿐이다. 새 토큰을 만들지 않는다.
  세 dimension status 에도 **같은 4개 어휘를 재사용**한다 — 새 vocabulary 를 만들지
  않기 위해서다.

★ 문자열 동일성은 evidence 이지 identity 가 아니다.
      same text  ⇒ EQUIVALENT   ⛔ 금지
      diff text  ⇒ DISTINCT     ⛔ 금지
  hash 는 candidate discovery 신호일 뿐 identity 결정자가 아니다.
"""
from __future__ import annotations

import itertools
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = os.path.join(ROOT, "rules", "canonical_rules.json")
OUT = os.path.join(ROOT, "rules", "equivalence_candidates.json")

EQUIVALENT = "EQUIVALENT"
DISTINCT = "DISTINCT"
POSSIBLE = "POSSIBLE_EQUIVALENT"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
STATUS = {EQUIVALENT, DISTINCT, POSSIBLE, INSUFFICIENT}

# ★ Portfolio Operation semantics 가 섞인 occurrence — 분할하지 않는다(CIO 판정 B2-0 #2).
#   B4 에서 'AI 매출 목표 하향' 만 떼어 새 canonical Rule 을 만들면 안 된다.
MIXED_SEMANTICS_OCCURRENCES = ["ANET::탈락 조건#3"]


# ── 사람이 판독한 adjudication ──────────────────────────────────────────
#   discovery 신호로 후보를 올리고, 판정은 사람이 한다.
#   ⛔ 어떤 신호도 자동 EQUIVALENT 로 이어지지 않는다.
CANDIDATES = [
    dict(
        left="RULE-0002", right="RULE-0018",
        discovery_reason="identical condition_text_sha256 + CIO 명시 후보",
        measured_fact_status=POSSIBLE, predicate_status=POSSIBLE, scope_status=INSUFFICIENT,
        equivalence_status=POSSIBLE,
        notes=("rule_kind 동일(FAL) · condition text byte-identical · measured_fact(DRAM ASP)와 "
               "predicate(하락 전환)가 동일해 보인다. 그러나 **scope 가 확정되지 않았다** — "
               "MU-specific 인지, 000660-specific 인지, DRAM industry-wide 하나를 두 종목이 "
               "참조하는지 source 만으로 결정되지 않는다. 두 occurrence 모두 "
               "definition_status=UNDEFINED('전환'의 판정 시점 미정의)라 predicate 도 완결이 아니다. "
               "⛔ fail-closed — 최대 POSSIBLE_EQUIVALENT. CIO 사전 판정과 일치."),
    ),
    dict(
        left="RULE-0003", right="RULE-0007",
        discovery_reason="동일 subject(TSM) + 동일 measured_fact 후보(TSMC 월매출)",
        measured_fact_status=POSSIBLE, predicate_status=DISTINCT, scope_status=POSSIBLE,
        equivalence_status=DISTINCT,
        notes=("★ CIO 가 든 예시. 둘 다 TSMC 매출 약화를 보지만 판정 함수가 다르다 — "
               "`YoY 40% 미달 2개월 연속` vs `단월 YoY < +35% OR 누계 YoY < +34.6%`. "
               "같은 숫자를 본다는 것보다 **판정 함수가 같은가**가 equivalence 의 핵심이므로 DISTINCT."),
    ),
    dict(
        left="RULE-0007", right="RULE-0008",
        discovery_reason="동일 source cell + 동일 threshold 참조(+35% / +34.6%)",
        measured_fact_status=POSSIBLE, predicate_status=DISTINCT, scope_status=POSSIBLE,
        equivalence_status=DISTINCT,
        notes=("같은 임계값을 쓰는 보수 관계(약화 / 비약화)이고 measured_fact 도 같아 보이나 "
               "**rule_kind 가 다르다**(FAL vs ENT). 필요조건 하나가 명시적으로 어긋나므로 DISTINCT. "
               "downstream action 차이(Ready 해제 vs A/B 활성)는 판정 근거로 쓰지 않았다."),
    ),
    dict(
        left="RULE-0005", right="RULE-0006",
        discovery_reason="동일 subject(TSM) + 동일 source cell + 가격 레벨 조건",
        measured_fact_status=DISTINCT, predicate_status=DISTINCT, scope_status=POSSIBLE,
        equivalence_status=DISTINCT,
        notes=("rule_kind 가 다르고(FAL vs ENT) 임계값도 다르다($398 vs SMA20 $409). "
               "§21-13 이 '한 칸에 두 Rule'로 지목한 바로 그 쌍이다."),
    ),
    dict(
        left="RULE-0004", right="RULE-0015",
        discovery_reason="동일 metric token(`capex 하향`)",
        measured_fact_status=INSUFFICIENT, predicate_status=DISTINCT, scope_status=INSUFFICIENT,
        equivalence_status=DISTINCT,
        notes=("문자열 일부가 같아 후보로 올라왔다. NVDA 쪽은 `하이퍼스케일러 2곳+`라는 "
               "**개수 조건**을 명시하고 TSM 쪽은 개수 조건이 없다 → predicate 가 다르다는 "
               "적극적 근거가 있으므로 DISTINCT. "
               "★ 단 TSM 쪽 `capex 하향`이 누구의 capex 인지는 source 에 없다(measured_fact 미확정). "
               "predicate 근거가 없었다면 INSUFFICIENT_EVIDENCE 였을 pair 다."),
    ),
    dict(
        left="RULE-0010", right="RULE-0019",
        discovery_reason="동일 source concept(`상대강도 … 지속 열위`)",
        measured_fact_status=DISTINCT, predicate_status=INSUFFICIENT, scope_status=DISTINCT,
        equivalence_status=DISTINCT,
        notes=("비교 대상 쌍이 원문에 명시돼 서로 다르다 — CRDO↔ANET vs 005930↔SK. "
               "measured_fact·scope 가 다르다는 적극적 근거가 있으므로 DISTINCT. "
               "predicate('지속'/'열위')는 양쪽 다 UNDEFINED 라 미확정이지만, DISTINCT 판정에는 "
               "'하나라도 다르다는 근거'면 충분하다."),
    ),
    dict(
        left="RULE-0001", right="RULE-0014",
        discovery_reason="동일 source concept(매출 목표 미달)",
        measured_fact_status=DISTINCT, predicate_status=DISTINCT, scope_status=DISTINCT,
        equivalence_status=DISTINCT,
        notes=("측정 대상이 다르고(MU FQ4 매출 vs NVDA FQ2 매출) 기준점 형태도 다르다 — "
               "절대액 `$49B` vs 상대 기준 `가이드 하단`."),
    ),
    dict(
        left="RULE-0017", right="RULE-0020",
        discovery_reason="동일 entity token(HBM) + 인접 종목(메모리 슬롯)",
        measured_fact_status=DISTINCT, predicate_status=INSUFFICIENT, scope_status=DISTINCT,
        equivalence_status=DISTINCT,
        notes=("`HBM 예약 취소·LTA 축소`(고객 확약의 취소)와 `HBM 공급 확대 미확인`(자사 공급 능력)은 "
               "측정 대상이 다르다. 양쪽 다 정의는 UNDEFINED."),
    ),
    dict(
        left="RULE-0022", right="RULE-0023",
        discovery_reason="동일 source concept(계약 잔고 감소) — 서로 다른 문장",
        measured_fact_status=POSSIBLE, predicate_status=DISTINCT, scope_status=DISTINCT,
        equivalence_status=DISTINCT,
        notes=("★ 문자열이 전혀 다른데 후보로 올린 pair 다 — `different text ⇒ DISTINCT` 를 "
               "쓰지 않는다는 규칙의 실사례. RPO(잔여 이행의무)와 수주잔고는 같은 경제적 개념 "
               "계열이지만, predicate 가 다르다(`급둔화` UNDEFINED vs `전분기 대비 감소` DEFINED) "
               "→ DISTINCT."),
    ),
    dict(
        left="RULE-0005", right="RULE-0024",
        discovery_reason="동일 predicate 형태(가격 레벨 이탈)",
        measured_fact_status=DISTINCT, predicate_status=POSSIBLE, scope_status=DISTINCT,
        equivalence_status=DISTINCT,
        notes=("판정 함수 형태('특정 종가 레벨을 이탈하면 무효')는 닮았으나 측정 대상이 다르다 "
               "(TSM $398 vs 298040 ₩1,894,000). 형태 유사성은 equivalence 가 아니다."),
    ),
    dict(
        left="RULE-0012", right="RULE-0013",
        discovery_reason="동일 source cell + `또는` 결합 표기로 직접 연결",
        measured_fact_status=DISTINCT, predicate_status=DISTINCT, scope_status=POSSIBLE,
        equivalence_status=DISTINCT,
        notes=("한 칸 안에서 `또는`로 묶인 형제 조각이지만 측정 대상이 다르다 "
               "(주가·실적 반응 vs AI 매출 목표). "
               "★ RULE-0013 은 mixed Rule/Portfolio semantics occurrence 다 — "
               "'CRDO 에 슬롯 이양' 부분을 떼어 새 canonical Rule 을 만들지 않았다."),
    ),
]

# ★ B2 에서 발견됐으나 pair 를 만들 수 없는 후보 — 사라지지 않도록 기록만 한다.
OUT_OF_POPULATION = [
    dict(candidate="TSMC 월매출 약화 → 매수 취소·Ready 해제",
         inside="RULE-0007 (TSM::다음 이벤트#3)",
         outside="TSM::편입 사유#4 (pilot · 46칸 밖)",
         reason="상대 occurrence 가 evaluation population 밖이라 canonical pair 를 만들 수 없다. "
                "그쪽은 `⚠ PROTOTYPE — 정본 규칙 아님` 표식 아래에 있어 권위도 다르다."),
    dict(candidate="TSMC $398 이탈",
         inside="RULE-0005 (TSM::기술적 무효화#1)",
         outside="TSM::편입 사유#5 (pilot · 46칸 밖)",
         reason="같은 숫자이나 효과가 다르다(무효화 vs 청산). 상대가 population 밖이라 pair 불가."),
]


def build():
    with open(CANON, encoding="utf-8") as f:
        canon = json.load(f)
    recs = {r["canonical_rule_id"]: r for r in canon["canonical_rules"]}
    ids = sorted(recs)

    rows = []
    for c in CANDIDATES:
        l, r = c["left"], c["right"]
        lr = recs.get(l), recs.get(r)
        rows.append({
            "left_rule_id": l,
            "right_rule_id": r,
            "discovery_reason": c["discovery_reason"],
            "same_rule_kind": (lr[0] and lr[1] and lr[0]["rule_kind"] == lr[1]["rule_kind"]),
            "measured_fact_status": c["measured_fact_status"],
            "predicate_status": c["predicate_status"],
            "scope_status": c["scope_status"],
            "equivalence_status": c["equivalence_status"],
            "evidence_occurrences": sorted(
                (lr[0]["source_occurrences"] if lr[0] else [])
                + (lr[1]["source_occurrences"] if lr[1] else [])),
            "identical_condition_hash": bool(
                lr[0] and lr[1]
                and lr[0]["condition_text_sha256"] == lr[1]["condition_text_sha256"]),
            "mixed_semantics": sorted(
                set(MIXED_SEMANTICS_OCCURRENCES)
                & set((lr[0]["source_occurrences"] if lr[0] else [])
                      + (lr[1]["source_occurrences"] if lr[1] else []))),
            "cio_approved_equivalence": c.get("cio_approved_equivalence", False),
            "notes": c["notes"],
        })

    compared = {frozenset((c["left"], c["right"])) for c in CANDIDATES}
    all_pairs = {frozenset(p) for p in itertools.combinations(ids, 2)}

    payload = {
        "artifact": "B4-0 Dedup Equivalence Adjudication View",
        "status": "inactive preparation",
        "authority": False,
        "consumable_by_evaluator": False,
        "merge_performed": False,
        "equivalence_key": "rule_kind + measured_fact + predicate + scope "
                           "(subject 는 필요조건이 아니다)",
        "status_vocabulary": sorted(STATUS),
        "vocabulary_note": ("세 dimension status 에도 위 4개 어휘를 재사용한다 — "
                            "새 판정 vocabulary 를 만들지 않기 위해서다."),
        "policy": {
            "hash_is_evidence_only": "same text ⇒ EQUIVALENT 금지 · diff text ⇒ DISTINCT 금지",
            "downstream_action_excluded": ("Ready 해제·매수 취소·강등 검토·슬롯 이양 은 "
                                           "equivalence key 에서 제외한다"),
            "fail_closed": ("scope/predicate/measured_fact 중 하나라도 미확정이거나 "
                            "mixed semantics 가 판단에 영향을 주면 최대 POSSIBLE_EQUIVALENT"),
            "no_split": "mixed semantics occurrence 를 분할해 새 canonical Rule 을 만들지 않는다",
        },
        "counts": {
            "canonical_records": len(ids),
            "all_pairs": len(all_pairs),
            "adjudicated_pairs": len(rows),
            "not_compared_pairs": len(all_pairs - compared),
        },
        "equivalence_approval": ("B4-0 에서 CIO 가 사전 승인한 equivalence 는 0건이다. "
                                 "`cio_approved_equivalence` 참조 없이는 EQUIVALENT 를 낼 수 없다. "
                                 "merge 여부는 B4-1 에서 CIO 가 판정한다."),
        "not_compared_policy": ("후보로 올라오지 않은 pair 는 개별 DISTINCT 를 생성하지 않고 "
                                "`not_compared` 로 남긴다. 비교하지 않은 것과 다르다고 판정한 것은 "
                                "다른 사실이다."),
        "mixed_semantics_occurrences": MIXED_SEMANTICS_OCCURRENCES,
        "candidates": rows,
        "out_of_population_candidates": OUT_OF_POPULATION,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return payload, recs


def validate(payload: dict, recs: dict) -> list:
    """B4-0 불변식. 위반이 있으면 이 view 를 다음 단계 입력으로 쓰지 않는다."""
    v = []
    if payload.get("merge_performed") is not False:
        v.append("merge_performed 가 False 가 아니다")
    if payload.get("authority") is not False or payload.get("consumable_by_evaluator") is not False:
        v.append("authority/consumable 이 False 가 아니다")

    seen = set()
    for c in payload["candidates"]:
        l, r = c["left_rule_id"], c["right_rule_id"]
        tag = f"{l}↔{r}"
        if l == r:
            v.append(f"{tag}: self-pair 금지")
        for x in (l, r):
            if x not in recs:
                v.append(f"{tag}: unknown canonical ID {x}")
        key = frozenset((l, r))
        if key in seen:
            v.append(f"{tag}: unordered pair 중복")
        seen.add(key)

        for f in ("equivalence_status", "measured_fact_status",
                  "predicate_status", "scope_status"):
            if c[f] not in STATUS:
                v.append(f"{tag}: {f}={c[f]!r} 는 허용 어휘 밖")

        if c["equivalence_status"] == EQUIVALENT:
            # ★★ B4-0 에서 CIO 가 사전 승인한 equivalence 는 **0건**이다.
            #    따라서 EQUIVALENT 는 명시적 승인 참조 없이는 나올 수 없다.
            #    이것이 "identical hash 만을 근거로 EQUIVALENT" 를 구조적으로 막는 지점이다 —
            #    세 dimension 을 사람이 EQUIVALENT 로 채워도 승인 참조가 없으면 통과하지 못한다.
            #    merge 여부는 B4-1 에서 CIO 가 판정한다.
            if not c.get("cio_approved_equivalence"):
                v.append(f"{tag}: EQUIVALENT 인데 CIO 승인 참조가 없다 "
                         f"— B4-0 의 사전 승인 equivalence 는 0건이다")
            # ★ fail-closed — 모르면 합치지 않는다
            unresolved = [f for f in ("measured_fact_status", "predicate_status", "scope_status")
                          if c[f] != EQUIVALENT]
            if unresolved:
                v.append(f"{tag}: EQUIVALENT 인데 미확정 dimension {unresolved}")
            if not c["same_rule_kind"]:
                v.append(f"{tag}: EQUIVALENT 인데 rule_kind 가 다르다")
            if c["mixed_semantics"]:
                v.append(f"{tag}: EQUIVALENT 인데 mixed semantics occurrence 포함")
            # ★ hash 동일만을 근거로 삼았는지
            if c["identical_condition_hash"] and c["scope_status"] != EQUIVALENT:
                v.append(f"{tag}: identical hash 만을 근거로 EQUIVALENT 판정")

        # source_occurrences 이동/생성이 없어야 한다
        expect = sorted(recs[l]["source_occurrences"] + recs[r]["source_occurrences"]) \
            if l in recs and r in recs else None
        if expect is not None and c["evidence_occurrences"] != expect:
            v.append(f"{tag}: evidence_occurrences 가 canonical record 와 다르다")
    return v


def main():
    payload, recs = build()
    v = validate(payload, recs)
    print(f"[B4-0] {OUT}")
    print(f"  canonical records {payload['counts']['canonical_records']} · "
          f"all pairs {payload['counts']['all_pairs']} · "
          f"adjudicated {payload['counts']['adjudicated_pairs']} · "
          f"not_compared {payload['counts']['not_compared_pairs']}")
    import collections
    st = collections.Counter(c["equivalence_status"] for c in payload["candidates"])
    print("  판정 분포: " + " · ".join(f"{k} {n}" for k, n in st.most_common()))
    print(f"  merge_performed = {payload['merge_performed']}")
    if v:
        print(f"\n★ 불변식 위반 {len(v)}건")
        for x in v:
            print("  ✗", x)
        sys.exit(1)
    print("  ✅ 불변식 위반 0")


if __name__ == "__main__":
    main()
