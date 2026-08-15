"""B4-1 — merge decision 불변식 회귀 (음성 포함)

핵심은 하나다: **merge 0건이 구조적으로 유지되는가.**
그리고 administrative 어휘가 Rule semantic vocabulary 로 새지 않는가.
"""
from __future__ import annotations

import copy
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "rules"))
import merge_decision as M                                    # noqa: E402
import vocabulary as VC                                       # noqa: E402
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


DEC = json.load(open(os.path.join(_ROOT, "rules", "merge_decision.json"), encoding="utf-8"))
CANON = json.load(open(os.path.join(_ROOT, "rules", "canonical_rules.json"), encoding="utf-8"))
EQUIV = json.load(open(os.path.join(_ROOT, "rules", "equivalence_candidates.json"),
                      encoding="utf-8"))
DECOMP = json.load(open(os.path.join(_ROOT, "rules", "decompose_full.json"), encoding="utf-8"))
RECS = {r["canonical_rule_id"]: r for r in CANON["canonical_rules"]}


def mut(**kw):
    p = copy.deepcopy(DEC)
    p.update(kw)
    return M.validate(p, CANON, EQUIV)


def test_baseline():
    print("\n[M-0] 기준선")
    check("★★ 불변식 위반 0", not M.validate(DEC, CANON, EQUIV),
          str(M.validate(DEC, CANON, EQUIV)))
    check("★★ approved_merges = []", DEC["approved_merges"] == [])
    check("★★ merge_performed = False", DEC["merge_performed"] is False)
    check("★★ 25 → 25 불변",
          DEC["canonical_records_before"] == 25 == DEC["canonical_records_after"])
    check("★ authority / consumable = False",
          DEC["authority"] is False and DEC["consumable_by_evaluator"] is False)


def test_no_merge_structurally():
    print("\n[M-1] ★ merge 0건이 구조적으로 유지된다")
    check("★★ RULE-0002 · RULE-0018 둘 다 살아 있다",
          "RULE-0002" in RECS and "RULE-0018" in RECS)
    check("★★ 각각 occurrence 를 정확히 1개씩 문다",
          len(RECS["RULE-0002"]["source_occurrences"]) == 1
          and len(RECS["RULE-0018"]["source_occurrences"]) == 1)
    check("★★ survivor / retired / alias / merged_into 필드가 어디에도 없다",
          not any(k in r for r in RECS.values()
                  for k in ("survivor", "retired", "alias", "merged_into", "superseded_by")))
    check("★★ 모든 canonical record 가 여전히 occurrence 1개",
          all(len(r["source_occurrences"]) == 1 for r in RECS.values()))
    check("★ B3 condition_semantics / scope 수정 없음",
          all(r["condition_semantics"] == "UNRESOLVED" and r["scope"] == "UNRESOLVED"
              for r in RECS.values()))

    check("★★ [음성] approved_merges 에 항목을 넣으면 잡힌다",
          any("approved_merges" in x for x in mut(approved_merges=[{"a": 1}])))
    check("★★ [음성] merge_performed=True 면 잡힌다",
          any("merge_performed" in x for x in mut(merge_performed=True)))
    check("★★ [음성] record 수가 줄면 잡힌다",
          any("canonical record 수" in x for x in mut(canonical_records_after=24)))

    # 실제 merge 를 흉내내면 잡히는가
    c = copy.deepcopy(CANON)
    surv = next(r for r in c["canonical_rules"] if r["canonical_rule_id"] == "RULE-0002")
    other = next(r for r in c["canonical_rules"] if r["canonical_rule_id"] == "RULE-0018")
    surv["source_occurrences"] += other["source_occurrences"]
    c["canonical_rules"] = [r for r in c["canonical_rules"]
                            if r["canonical_rule_id"] != "RULE-0018"]
    check("★★ [음성] 실제로 두 record 를 합치면 잡힌다",
          any("merge 흔적" in x or "record 수가 실제" in x
              for x in M.validate(DEC, c, EQUIV)), str(M.validate(DEC, c, EQUIV)))


def test_relation_preserved():
    print("\n[M-2] B4-0 relation 을 수정하지 않았다")
    pe = [c for c in EQUIV["candidates"] if c["equivalence_status"] == M.POSSIBLE]
    check("★★ B4-0 의 POSSIBLE_EQUIVALENT 1건이 그대로다", len(pe) == 1)
    check("★★ 그 relation 이 B4-1 에서 보존됐다",
          DEC["reviewed_possible_equivalents"][0]["relation_preserved"] == M.POSSIBLE)
    check("★★ 검토를 빠뜨린 POSSIBLE_EQUIVALENT 0건",
          not any("검토하지 않은" in x for x in M.validate(DEC, CANON, EQUIV)))
    check("★★ [음성] POSSIBLE_EQUIVALENT 를 검토 목록에서 빼면 잡힌다",
          any("검토하지 않은" in x for x in mut(reviewed_possible_equivalents=[])))
    check("★★ [음성] B4-0 에 없는 relation 을 검토하면 잡힌다",
          any("B4-0 에 없는" in x for x in mut(reviewed_possible_equivalents=[
              {**DEC["reviewed_possible_equivalents"][0], "right_rule_id": "RULE-0003"}])))
    check("★★ [음성] decision 을 NO_MERGE 밖으로 바꾸면 잡힌다",
          any("승인되지 않은 decision" in x for x in mut(reviewed_possible_equivalents=[
              {**DEC["reviewed_possible_equivalents"][0], "decision": "MERGE"}])))


def test_admin_vocabulary_not_leaked():
    print("\n[M-3] ★ administrative 어휘가 Rule vocabulary 로 새지 않는다")
    sem = (VC.OBJECT_ROLE | VC.RULE_KIND | {x for x in VC.DOWNSTREAM_EFFECT if x}
           | VC.DEFINITION_STATUS | VC.DATA_STATUS | VC.DATA_CAPABILITY
           | {x for x in VC.SOURCE_QUALIFICATION if x})
    check("★★ NO_MERGE 가 Rule semantic vocabulary 에 없다", "NO_MERGE" not in sem)
    check("★★ shared_scope_not_established 가 Rule semantic vocabulary 에 없다",
          "shared_scope_not_established" not in sem)
    src = open(os.path.join(_ROOT, "rules", "vocabulary.py"), encoding="utf-8").read()
    check("★★ vocabulary.py 에 administrative 토큰이 추가되지 않았다",
          not any(t in src for t in M.ADMIN_TOKENS))
    blob = json.dumps(CANON, ensure_ascii=False) + json.dumps(DECOMP, ensure_ascii=False)
    check("★★ canonical record · decomposition 에 administrative 토큰이 없다",
          not any(t in blob for t in M.ADMIN_TOKENS))


def test_no_merge_engine():
    print("\n[M-4] merge 엔진을 만들지 않았다")
    src = open(os.path.join(_ROOT, "rules", "merge_decision.py"), encoding="utf-8").read()
    check("★★ merge 를 수행하는 함수가 없다",
          not any(t in src for t in ("def merge", "def apply_merge", "def do_merge")))
    check("★★ decision artifact 는 canonical_rules.json 을 쓰지 않는다 (읽기만)",
          'open(CANON, "w"' not in src and "json.dump" in src
          and src.count("json.dump") == 1 and "OUT)" not in src.split("json.dump")[1][:60]
          or 'open(CANON, "w"' not in src)
    check("★★ artifact 가 merge 엔진 부재를 명시한다", "no_merge_engine" in DEC)
    check("★ canonicalize.py 에도 merge 함수가 없다",
          "def merge" not in open(os.path.join(_ROOT, "rules", "canonicalize.py"),
                                  encoding="utf-8").read())


def test_provenance_pinned():
    print("\n[M-5] 무엇에 대해 결정했는지가 고정돼 있다")
    check("★★ B3 canonical artifact 해시가 기록돼 있다",
          DEC["decided_against"]["canonical_rules_sha256"]
          == M._sha(os.path.join(_ROOT, "rules", "canonical_rules.json")))
    check("★★ B4-0 equivalence artifact 해시가 기록돼 있다",
          DEC["decided_against"]["equivalence_candidates_sha256"]
          == M._sha(os.path.join(_ROOT, "rules", "equivalence_candidates.json")))
    check("★★ [음성] 결정 이후 canonical artifact 가 바뀌면 잡힌다",
          any("변경됐다" in x for x in mut(decided_against={
              "canonical_rules_sha256": "0" * 64,
              "equivalence_candidates_sha256":
                  DEC["decided_against"]["equivalence_candidates_sha256"]})))
    check("★ evaluation population 25 불변",
          len(K.evaluation_occurrences(DECOMP)) == 25)


def test_result_is_not_failure():
    print("\n[M-6] merge 0건은 실패가 아니라 결과다")
    check("★★ artifact 가 그 사실을 명시한다", "실패가 아니다" in DEC["result_note"])
    check("★ 재검토 trigger 가 기록돼 있다",
          "reconsideration_trigger" in DEC["reviewed_possible_equivalents"][0])
    check("★ 다음 게이트가 Definition Resolution 임이 기록돼 있다",
          "definition_status=UNDEFINED" in DEC["next_gate_note"])
    # ★ 다음 게이트의 근거를 실제로 센다 — 문장만 믿지 않는다
    ev = {o["occurrence_id"] for o in K.evaluation_occurrences(DECOMP)}
    und = sum(1 for c in DECOMP["cells"] for f in c["fragments"]
              if f"{c['candidate_id']}#{f['split_index']}" in ev
              and f["definition_status"] == "UNDEFINED")
    check("★★ 근거 실측 — evaluation rules 25건 중 UNDEFINED 15건",
          len(ev) == 25 and und == 15, f"population={len(ev)} undefined={und}")


SUITES = [test_baseline, test_no_merge_structurally, test_relation_preserved,
          test_admin_vocabulary_not_leaked, test_no_merge_engine,
          test_provenance_pinned, test_result_is_not_failure]


def main():
    print("B4-1 merge decision — 불변식 회귀")
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
