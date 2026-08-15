"""B4-1 — Merge Decision 기록. **decision artifact 만 만든다.**

⛔ merge 엔진을 만들지 않는다. approved merge 가 0건인데 미래 merge 기능을 구현하면
   현재 단계의 검증 범위만 불필요하게 넓어진다. 필요해지면 그때 만든다.

⛔ survivor 없음 · retired canonical ID 없음 · alias 없음 ·
   source_occurrences 이동 없음 · B3 condition_semantics/scope 수정 없음 ·
   B4-0 relation 수정 없음 · evaluator 연결 없음.

★ dedup 결과가 0건이라고 해서 B4 가 실패한 것이 아니다.
  **현재 25개 canonical identity 중 합쳐도 된다고 증명된 것이 없다**는 것이 B4 의 결과다.
  이는 임시 실패 상태가 아니라 현재 증거에서의 정상 canonical population 이다.

★ 이 파일이 쓰는 `NO_MERGE` · `shared_scope_not_established` 는
  **administrative decision 값/사유일 뿐** Rule semantic vocabulary 가 아니다.
  vocabulary.py 에 추가하지 않으며 canonical record 에도 쓰지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = os.path.join(ROOT, "rules", "canonical_rules.json")
EQUIV = os.path.join(ROOT, "rules", "equivalence_candidates.json")
OUT = os.path.join(ROOT, "rules", "merge_decision.json")

POSSIBLE = "POSSIBLE_EQUIVALENT"

# ── administrative decision 어휘 — 이 artifact 안에서만 쓴다 ────────────
NO_MERGE = "NO_MERGE"
ADMIN_TOKENS = {NO_MERGE, "shared_scope_not_established"}


def _sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def build():
    canon = json.load(open(CANON, encoding="utf-8"))
    equiv = json.load(open(EQUIV, encoding="utf-8"))
    n = len(canon["canonical_rules"])

    reviewed = [{
        "left_rule_id": "RULE-0002",
        "right_rule_id": "RULE-0018",
        "decision": NO_MERGE,
        "relation_preserved": POSSIBLE,
        "reason": "shared_scope_not_established",
        "reason_detail": (
            "`DRAM ASP 하락 전환` 의 condition text 와 hash 가 동일하고 measured fact / "
            "predicate 도 동일할 가능성이 높지만, source 는 각각 MU 와 000660.KS 의 "
            "탈락조건으로 존재한다. 현재 provenance 만으로 "
            "(1) 하나의 industry-level Rule 을 두 종목이 참조하는 것인지, "
            "(2) 같은 문구를 가진 두 개의 security-specific Rule 인지 판정할 수 없다. "
            "merge 하면 source 가 말하지 않은 industry-wide shared scope 를 새로 만드는 "
            "결과가 된다. ⛔ fail closed — merge 하지 않는다."),
        "reconsideration_trigger": (
            "정본에서 `DRAM ASP 하락 전환` 이 명시적으로 공통 산업 Rule 로 정의되고, "
            "MU·SK하이닉스가 그 Rule 을 참조한다는 provenance 가 생기면 그때 다시 "
            "adjudication 한다. 지금 미리 합치지 않는다."),
    }]

    payload = {
        "artifact": "B4-1 Merge Decision",
        "status": "inactive preparation",
        "authority": False,
        "consumable_by_evaluator": False,

        "approved_merges": [],
        "reviewed_possible_equivalents": reviewed,

        "merge_performed": False,
        "canonical_records_before": n,
        "canonical_records_after": n,

        "result_note": (
            "★ merge 0건은 실패가 아니다. 현재 25개 canonical identity 중 "
            "**합쳐도 된다고 증명된 것이 없다**는 것이 B4 의 결과이며, "
            "이는 현재 증거에서의 정상 canonical population 이다. "
            "25 occurrence → 25 canonical rules 상태를 그대로 유지한다."),
        "vocabulary_note": (
            "`NO_MERGE` · `shared_scope_not_established` 는 이 decision artifact 의 "
            "administrative 값/사유일 뿐 Rule semantic vocabulary 가 아니다. "
            "vocabulary.py 에 추가하지 않고 canonical record 에도 쓰지 않는다."),
        "no_merge_engine": (
            "approved merge 가 0건이므로 merge 엔진을 구현하지 않았다. "
            "필요해지는 시점에 만든다."),

        "decided_against": {
            "canonical_rules_sha256": _sha(CANON),
            "equivalence_candidates_sha256": _sha(EQUIV),
        },
        "next_gate_note": (
            "다음 단계는 dedup 을 더 파는 것이 아니다 — evaluation rules 25건 중 "
            "15건이 definition_status=UNDEFINED 인 문제로 돌아가는 것이 맞다. "
            "Definition Resolution 게이트는 CIO 가 별도로 연다."),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return payload, canon, equiv


def validate(payload, canon, equiv) -> list:
    v = []
    recs = {r["canonical_rule_id"]: r for r in canon["canonical_rules"]}

    if payload["approved_merges"] != []:
        v.append("approved_merges 가 비어 있지 않다 — 승인된 merge 는 0건이다")
    if payload["merge_performed"] is not False:
        v.append("merge_performed 가 False 가 아니다")
    if payload["authority"] is not False or payload["consumable_by_evaluator"] is not False:
        v.append("authority/consumable 이 False 가 아니다")
    if payload["canonical_records_before"] != payload["canonical_records_after"]:
        v.append("canonical record 수가 변했다")
    if payload["canonical_records_after"] != len(recs):
        v.append("artifact 의 record 수가 실제 canonical artifact 와 다르다")

    # 구조 변경이 없어야 한다
    for rid, r in recs.items():
        for forbidden in ("survivor", "retired", "alias", "merged_into", "superseded_by"):
            if forbidden in r:
                v.append(f"{rid}: 금지된 필드 {forbidden}")
        if len(r["source_occurrences"]) != 1:
            v.append(f"{rid}: source_occurrences 가 1개가 아니다 — merge 흔적")
        if r.get("condition_semantics") != "UNRESOLVED" or r.get("scope") != "UNRESOLVED":
            v.append(f"{rid}: B3 의 condition_semantics/scope 가 수정됐다")

    # B4-0 의 POSSIBLE_EQUIVALENT 를 하나도 빠뜨리지 않고 검토했는가
    pe = {frozenset((c["left_rule_id"], c["right_rule_id"]))
          for c in equiv["candidates"] if c["equivalence_status"] == POSSIBLE}
    rv = {frozenset((r["left_rule_id"], r["right_rule_id"]))
          for r in payload["reviewed_possible_equivalents"]}
    if pe - rv:
        v.append(f"검토하지 않은 POSSIBLE_EQUIVALENT: {[sorted(x) for x in pe - rv]}")
    if rv - pe:
        v.append(f"B4-0 에 없는 relation 을 검토했다: {[sorted(x) for x in rv - pe]}")

    for r in payload["reviewed_possible_equivalents"]:
        if r["decision"] != NO_MERGE:
            v.append(f"{r['left_rule_id']}↔{r['right_rule_id']}: 승인되지 않은 decision "
                     f"{r['decision']!r}")
        if r["relation_preserved"] != POSSIBLE:
            v.append(f"{r['left_rule_id']}↔{r['right_rule_id']}: B4-0 relation 이 변경됐다")
        for x in (r["left_rule_id"], r["right_rule_id"]):
            if x not in recs:
                v.append(f"존재하지 않는 canonical ID {x}")

    # B3 / B4-0 artifact 가 결정 시점 그대로인가
    if payload["decided_against"]["canonical_rules_sha256"] != _sha(CANON):
        v.append("canonical_rules.json 이 결정 이후 변경됐다")
    if payload["decided_against"]["equivalence_candidates_sha256"] != _sha(EQUIV):
        v.append("equivalence_candidates.json 이 결정 이후 변경됐다")
    return v


def main():
    payload, canon, equiv = build()
    v = validate(payload, canon, equiv)
    print(f"[B4-1] {OUT}")
    print(f"  approved_merges        : {len(payload['approved_merges'])}")
    print(f"  reviewed POSSIBLE_EQ   : {len(payload['reviewed_possible_equivalents'])} "
          f"→ 전부 {NO_MERGE}")
    print(f"  canonical records      : {payload['canonical_records_before']} → "
          f"{payload['canonical_records_after']}")
    print(f"  merge_performed        : {payload['merge_performed']}")
    if v:
        print(f"\n★ 불변식 위반 {len(v)}건")
        for x in v:
            print("  ✗", x)
        sys.exit(1)
    print("  ✅ 불변식 위반 0")


if __name__ == "__main__":
    main()
