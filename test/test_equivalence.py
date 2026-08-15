"""B4-0 — equivalence adjudication view 불변식 회귀 (음성 포함)"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "rules"))
import equivalence_candidates as E                            # noqa: E402
import canonicalize as K                                      # noqa: E402

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


CANON = json.load(open(os.path.join(_ROOT, "rules", "canonical_rules.json"), encoding="utf-8"))
RECS = {r["canonical_rule_id"]: r for r in CANON["canonical_rules"]}
VIEW = json.load(open(os.path.join(_ROOT, "rules", "equivalence_candidates.json"),
                     encoding="utf-8"))
DECOMP = json.load(open(os.path.join(_ROOT, "rules", "decompose_full.json"), encoding="utf-8"))


def mutate(**kw):
    p = copy.deepcopy(VIEW)
    p["candidates"][0].update(kw)
    return E.validate(p, RECS)


def test_baseline():
    print("\n[E-0] 기준선")
    check("★★ 불변식 위반 0", not E.validate(VIEW, RECS), str(E.validate(VIEW, RECS)))
    check("★★ merge_performed = False", VIEW["merge_performed"] is False)
    check("★★ authority / consumable = False",
          VIEW["authority"] is False and VIEW["consumable_by_evaluator"] is False)
    check("★ 300 pair 중 후보만 판정 · 나머지는 not_compared",
          VIEW["counts"]["all_pairs"] == 300
          and VIEW["counts"]["adjudicated_pairs"] + VIEW["counts"]["not_compared_pairs"] == 300)
    check("★★ EQUIVALENT 0건 (CIO 사전 승인 0건)",
          not [c for c in VIEW["candidates"] if c["equivalence_status"] == E.EQUIVALENT])


def test_b3_untouched():
    print("\n[E-1] B3 artifact 를 건드리지 않았다")
    occ = K.evaluation_occurrences(DECOMP)
    pop = {o["occurrence_id"]: o for o in occ}
    check("★★ canonical record 25 불변", len(CANON["canonical_rules"]) == 25)
    check("★★ canonical ID mutation 0",
          sorted(RECS) == [f"RULE-{i:04d}" for i in range(1, 26)])
    check("★★ canonical record 삭제 0", len(RECS) == 25)
    check("★★ merge 0 — 어떤 record 도 occurrence 를 2개 이상 물지 않는다",
          all(len(r["source_occurrences"]) == 1 for r in RECS.values()))
    check("★★ source occurrence mutation 0",
          all(r["condition_text"] == pop[r["source_occurrences"][0]]["raw_fragment"]
              for r in RECS.values()))
    check("★★ B3 artifact 에 equivalence 필드가 주입되지 않았다",
          not any(k in r for r in RECS.values()
                  for k in ("equivalence_status", "merged_into", "condition_semantics_filled")))
    check("★ condition_semantics / scope 는 여전히 UNRESOLVED",
          all(r["condition_semantics"] == "UNRESOLVED" and r["scope"] == "UNRESOLVED"
              for r in RECS.values()))
    check("★★ view 는 canonical record 밖의 occurrence 를 만들어내지 않았다",
          all(set(c["evidence_occurrences"]) <= set(pop) for c in VIEW["candidates"]))


def test_fail_closed():
    print("\n[E-2] ★ fail-closed — 모르면 합치지 않는다")
    check("★★ [음성] scope 미확정인데 EQUIVALENT → 잡힌다",
          any("미확정 dimension" in x for x in
              mutate(equivalence_status=E.EQUIVALENT,
                     measured_fact_status=E.EQUIVALENT, predicate_status=E.EQUIVALENT,
                     scope_status=E.INSUFFICIENT)))
    check("★★ [음성] predicate 미확정인데 EQUIVALENT → 잡힌다",
          any("미확정 dimension" in x for x in
              mutate(equivalence_status=E.EQUIVALENT,
                     measured_fact_status=E.EQUIVALENT, predicate_status=E.POSSIBLE,
                     scope_status=E.EQUIVALENT)))
    # ★★ 실제 구멍이었던 지점 — 세 dimension 을 사람이 EQUIVALENT 로 채우면 통과했었다.
    r = mutate(equivalence_status=E.EQUIVALENT, measured_fact_status=E.EQUIVALENT,
               predicate_status=E.EQUIVALENT, scope_status=E.EQUIVALENT)
    check("★★ [음성] 세 dimension 을 채워도 CIO 승인 참조 없이는 EQUIVALENT 불가",
          any("CIO 승인 참조가 없다" in x for x in r), str(r))
    check("★★ 현재 view 에 CIO 승인 참조를 가진 pair 는 0건",
          not [c for c in VIEW["candidates"] if c.get("cio_approved_equivalence")])

    # rule_kind 가 다른 pair 를 EQUIVALENT 로
    p = copy.deepcopy(VIEW)
    tgt = next(c for c in p["candidates"] if not c["same_rule_kind"])
    tgt.update(equivalence_status=E.EQUIVALENT, measured_fact_status=E.EQUIVALENT,
               predicate_status=E.EQUIVALENT, scope_status=E.EQUIVALENT)
    check("★★ [음성] rule_kind 다른데 EQUIVALENT → 잡힌다",
          any("rule_kind 가 다르다" in x for x in E.validate(p, RECS)))

    # mixed semantics 포함 pair 를 EQUIVALENT 로
    p = copy.deepcopy(VIEW)
    tgt = next(c for c in p["candidates"] if c["mixed_semantics"])
    tgt.update(equivalence_status=E.EQUIVALENT, measured_fact_status=E.EQUIVALENT,
               predicate_status=E.EQUIVALENT, scope_status=E.EQUIVALENT, same_rule_kind=True)
    check("★★ [음성] mixed semantics 포함인데 EQUIVALENT → 잡힌다",
          any("mixed semantics" in x for x in E.validate(p, RECS)))


def test_pair_hygiene():
    print("\n[E-3] pair 위생")
    check("★★ [음성] self-pair → 잡힌다",
          any("self-pair" in x for x in mutate(right_rule_id=VIEW["candidates"][0]["left_rule_id"])))
    check("★★ [음성] unknown canonical ID → 잡힌다",
          any("unknown canonical ID" in x for x in mutate(right_rule_id="RULE-9999")))
    p = copy.deepcopy(VIEW)
    a = p["candidates"][0]
    p["candidates"].append({**a, "left_rule_id": a["right_rule_id"],
                            "right_rule_id": a["left_rule_id"]})
    check("★★ [음성] 순서만 뒤집은 중복 pair → unordered 로 잡힌다",
          any("unordered pair 중복" in x for x in E.validate(p, RECS)))
    check("★★ [음성] evidence_occurrences 를 옮기면 잡힌다",
          any("evidence_occurrences" in x for x in mutate(evidence_occurrences=[])))
    check("★ 새 판정 vocabulary 를 쓰면 잡힌다",
          any("허용 어휘 밖" in x for x in mutate(equivalence_status="LIKELY")))


def test_no_split_no_new_rule():
    print("\n[E-4] mixed semantics occurrence 에서 새 Rule 을 만들지 않았다")
    check("★★ ANET::탈락 조건#3 이 mixed 로 표시된다",
          VIEW["mixed_semantics_occurrences"] == ["ANET::탈락 조건#3"])
    check("★★ 그 occurrence 는 여전히 정확히 1개 canonical record 에만 속한다",
          sum(1 for r in RECS.values() if "ANET::탈락 조건#3" in r["source_occurrences"]) == 1)
    check("★★ 'AI 매출 목표 하향' 만 떼어낸 새 record 가 없다",
          not [r for r in RECS.values()
               if r["condition_text"] == "AI 매출 목표 하향"])
    check("★ 원 occurrence 의 condition_text 가 통째로 보존된다",
          any(r["condition_text"] == "AI 매출 목표 하향 시 CRDO에 슬롯 이양"
              for r in RECS.values()))


def test_hash_is_evidence_only():
    print("\n[E-5] 문자열 동일성은 evidence 일 뿐이다")
    ih = [c for c in VIEW["candidates"] if c["identical_condition_hash"]]
    check("★★ identical hash pair 가 존재한다 (DRAM ASP)", len(ih) == 1, str(len(ih)))
    check("★★ 그 pair 는 EQUIVALENT 가 아니라 POSSIBLE_EQUIVALENT 다",
          ih and ih[0]["equivalence_status"] == E.POSSIBLE)
    check("★★ RULE-0002 ↔ RULE-0018 이 후보로 올라와 있다",
          ih and {ih[0]["left_rule_id"], ih[0]["right_rule_id"]} == {"RULE-0002", "RULE-0018"})
    diff_text = [c for c in VIEW["candidates"]
                 if not c["identical_condition_hash"]
                 and RECS[c["left_rule_id"]]["condition_text"]
                 != RECS[c["right_rule_id"]]["condition_text"]]
    check("★ 문자열이 다른데도 후보로 올린 pair 가 있다 (diff text ⇒ DISTINCT 아님)",
          len(diff_text) >= 1, str(len(diff_text)))
    # ★ 회귀 fixture — 문자열 동일성이 identity 를 증명하지 않는다 (execution reference 반례)
    ref = [f["raw_fragment"] for c in DECOMP["cells"] for f in c["fragments"]
           if f["object_role"] == "execution_reference"]
    check("★★ [fixture] 참조값 '236.5' 가 두 종목에 동일 문자열로 존재한다",
          ref.count("236.5") == 2, str(ref.count("236.5")))
    check("★ 그 반례는 evaluation population 밖이라 pair 에 들어오지 않는다",
          not any("236.5" in RECS[c["left_rule_id"]]["condition_text"]
                  or "236.5" in RECS[c["right_rule_id"]]["condition_text"]
                  for c in VIEW["candidates"]))


SUITES = [test_baseline, test_b3_untouched, test_fail_closed, test_pair_hygiene,
          test_no_split_no_new_rule, test_hash_is_evidence_only]


def main():
    print("B4-0 equivalence adjudication — 불변식 회귀")
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
