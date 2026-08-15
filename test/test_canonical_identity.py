"""B3 — canonical identity 불변식 회귀

CIO 가 요구한 최소 검사 6종 + 음성.
  C-1  evaluation occurrence 25건 모두 정확히 하나의 canonical ID 를 갖는다
  C-2  하나의 occurrence 가 2개 canonical ID 에 연결되면 FAIL
  C-3  canonical ID 중복 정의 FAIL
  C-4  source occurrence 가 실제 evaluation population 에 없으면 FAIL
  C-5  canonicalization 과정에서 source fragment text 변경 시 FAIL
  C-6  ★ 기존 mapping 이 있는 상태에서 재실행했을 때 ID 가 바뀌면 FAIL
"""
from __future__ import annotations

import collections
import copy
import hashlib
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "rules"))
import canonicalize as K                                     # noqa: E402

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


DECOMP = json.load(open(os.path.join(_ROOT, "rules", "decompose_full.json"), encoding="utf-8"))
CANON = json.load(open(os.path.join(_ROOT, "rules", "canonical_rules.json"), encoding="utf-8"))
OCC = K.evaluation_occurrences(DECOMP)
POP = {o["occurrence_id"] for o in OCC}
RECS = CANON["canonical_rules"]


def validate(canon, occurrences):
    """canonical artifact 가 지켜야 하는 불변식. 위반 목록을 낸다."""
    v = []
    pop = {o["occurrence_id"]: o for o in occurrences}

    ids = [r["canonical_rule_id"] for r in canon["canonical_rules"]]
    dup = [k for k, n in collections.Counter(ids).items() if n > 1]
    if dup:
        v.append(f"C-3 canonical ID 중복 정의: {dup}")

    seen = collections.Counter()
    for r in canon["canonical_rules"]:
        for occ in r["source_occurrences"]:
            seen[occ] += 1
            if occ not in pop:
                v.append(f"C-4 orphan — {occ} 는 evaluation population 에 없다")
    multi = [k for k, n in seen.items() if n > 1]
    if multi:
        v.append(f"C-2 multi-mapped occurrence: {multi}")

    missing = sorted(pop) if not seen else sorted(set(pop) - set(seen))
    if missing:
        v.append(f"C-1 canonical ID 가 없는 occurrence: {missing}")

    for r in canon["canonical_rules"]:
        for occ in r["source_occurrences"]:
            src = pop.get(occ)
            if src and src["raw_fragment"] != r["condition_text"]:
                v.append(f"C-5 source mutation — {occ} 의 원문과 condition_text 가 다르다")
            if src and hashlib.sha256(src["raw_fragment"].encode()).hexdigest() \
                    != r["condition_text_sha256"]:
                v.append(f"C-5 sha 불일치 — {occ}")
    return v


# ── 기준선 ───────────────────────────────────────────────────────────────
def test_baseline():
    print("\n[C-0] 기준선")
    check("★★ evaluation population 25건", len(OCC) == 25, str(len(OCC)))
    check("★★ canonical record 25건", len(RECS) == 25, str(len(RECS)))
    check("★★ 불변식 위반 0", not validate(CANON, OCC), str(validate(CANON, OCC)))
    check("★ FAL 21 / ENT 4",
          collections.Counter(r["rule_kind"] for r in RECS) == {"FAL": 21, "ENT": 4},
          str(collections.Counter(r["rule_kind"] for r in RECS)))


# ── C-1 / C-2 / C-3 / C-4 ───────────────────────────────────────────────
def test_mapping_invariants():
    print("\n[C-1~4] mapping 불변식")
    seen = collections.Counter(o for r in RECS for o in r["source_occurrences"])
    check("★★ occurrence coverage 25/25", set(seen) == POP, str(POP - set(seen)))
    check("★★ multi-mapped occurrence 0", all(n == 1 for n in seen.values()))
    check("★★ duplicate canonical ID 0",
          len({r["canonical_rule_id"] for r in RECS}) == len(RECS))
    check("★★ orphan 0", not (set(seen) - POP))

    # 음성 — 같은 occurrence 를 두 record 가 물면 잡혀야 한다
    c = copy.deepcopy(CANON)
    c["canonical_rules"].append({**c["canonical_rules"][0], "canonical_rule_id": "RULE-9999"})
    check("★★ [음성] multi-mapped 를 만들면 잡힌다",
          any("C-2" in x for x in validate(c, OCC)), str(validate(c, OCC)))

    # 음성 — canonical ID 를 중복 정의하면 잡혀야 한다
    c = copy.deepcopy(CANON)
    c["canonical_rules"][1]["canonical_rule_id"] = c["canonical_rules"][0]["canonical_rule_id"]
    check("★★ [음성] canonical ID 중복이면 잡힌다", any("C-3" in x for x in validate(c, OCC)))

    # 음성 — population 밖 occurrence 를 물면 잡혀야 한다
    c = copy.deepcopy(CANON)
    c["canonical_rules"][0]["source_occurrences"] = ["MSFT::핵심 지지#1"]   # reference cell
    check("★★ [음성] population 밖 occurrence 는 orphan 으로 잡힌다",
          any("C-4" in x for x in validate(c, OCC)))

    # 음성 — occurrence 하나를 빠뜨리면 잡혀야 한다
    c = copy.deepcopy(CANON)
    c["canonical_rules"] = c["canonical_rules"][:-1]
    check("★★ [음성] 누락된 occurrence 가 잡힌다", any("C-1" in x for x in validate(c, OCC)))


# ── C-5 source mutation ─────────────────────────────────────────────────
def test_no_source_mutation():
    print("\n[C-5] source fragment 를 건드리지 않는다")
    pop = {o["occurrence_id"]: o for o in OCC}
    check("★★ condition_text 가 원문과 바이트 동일",
          all(r["condition_text"] == pop[r["source_occurrences"][0]]["raw_fragment"]
              for r in RECS))
    check("★ canonical artifact 가 decomposition 을 수정하지 않았다 — "
          "원본에 canonical field 없음",
          not any("canonical_rule_id" in f
                  for c in DECOMP["cells"] for f in c["fragments"]))

    c = copy.deepcopy(CANON)
    c["canonical_rules"][0]["condition_text"] += " (정리)"
    check("★★ [음성] condition_text 를 다듬으면 잡힌다",
          any("C-5" in x for x in validate(c, OCC)))


# ── C-6 ★ ID 안정성 ─────────────────────────────────────────────────────
def test_id_stability():
    print("\n[C-6] ★ ID 는 재실행·순서변경으로 바뀌지 않는다")
    before = {o: r["canonical_rule_id"] for r in RECS for o in r["source_occurrences"]}

    # 같은 입력으로 재실행
    recs2, stat2 = K.assign(OCC, CANON)
    after2 = {o: r["canonical_rule_id"] for r in recs2 for o in r["source_occurrences"]}
    check("★★ 재실행해도 ID 동일", after2 == before)
    check("★★ 재실행 시 신규 발급 0", stat2["new"] == 0, str(stat2))

    # ★★ 입력 순서를 뒤집어 재실행 — 정렬 의존이면 여기서 깨진다
    recs3, stat3 = K.assign(list(reversed(OCC)), CANON)
    after3 = {o: r["canonical_rule_id"] for r in recs3 for o in r["source_occurrences"]}
    check("★★ 입력 순서를 뒤집어도 ID 동일 (정렬 의존 아님)", after3 == before,
          str({k: (before[k], after3[k]) for k in before if before[k] != after3[k]}))

    # ★ 새 occurrence 만 다음 번호를 받는다 — 기존 ID 는 불변
    extra = OCC + [{"occurrence_id": "ZZZ::탈락 조건#1", "candidate_id": "ZZZ",
                    "source_cell": "탈락 조건", "split_index": 1,
                    "rule_kind": "FAL", "raw_fragment": "신규 조각"}]
    recs4, stat4 = K.assign(extra, CANON)
    after4 = {o: r["canonical_rule_id"] for r in recs4 for o in r["source_occurrences"]}
    check("★★ 새 occurrence 추가 시 기존 ID 불변",
          all(after4[k] == v for k, v in before.items()))
    check("★ 새 occurrence 만 다음 번호를 받는다",
          stat4["new"] == 1 and after4["ZZZ::탈락 조건#1"] not in before.values(),
          f"{stat4} {after4.get('ZZZ::탈락 조건#1')}")

    # ★ 음성 — 빈 mapping 에서 순서를 바꾸면 최초 배정은 달라질 수 있다.
    #   그래서 최초 배정 이후 mapping 재사용이 필수라는 것을 고정한다.
    a, _ = K.assign(OCC, None)
    b, _ = K.assign(list(reversed(OCC)), None)
    fresh_differs = ({o: r["canonical_rule_id"] for r in a for o in r["source_occurrences"]}
                     != {o: r["canonical_rule_id"] for r in b for o in r["source_occurrences"]})
    check("★ [근거] mapping 없이 재배정하면 순서에 따라 달라진다 → 재사용이 필수",
          fresh_differs)


# ── 금지사항 ────────────────────────────────────────────────────────────
def test_prohibitions():
    print("\n[C-7] B3 에서 금지된 것을 하지 않았다")
    check("★★ dedup 미착수 — record 수 = occurrence 수", len(RECS) == len(OCC))
    check("★★ 어떤 record 도 2개 이상 occurrence 를 묶지 않았다",
          all(len(r["source_occurrences"]) == 1 for r in RECS))
    dram = [r for r in RECS if r["condition_text"] == "DRAM ASP 하락 전환"]
    check("★★ 'DRAM ASP 하락 전환' 두 occurrence 가 합쳐지지 않았다",
          len(dram) == 2 and dram[0]["canonical_rule_id"] != dram[1]["canonical_rule_id"],
          str([r["canonical_rule_id"] for r in dram]))
    check("★ 대상은 FAL/ENT 뿐 — MON/reference/evidence 미포함",
          all(r["rule_kind"] in ("FAL", "ENT") for r in RECS))
    check("★★ opaque ID — 의미를 encoding 하지 않았다",
          all(r["canonical_rule_id"].startswith("RULE-")
              and r["canonical_rule_id"][5:].isdigit() for r in RECS))
    check("★ condition_semantics / scope 는 UNRESOLVED 로 남겼다 (B4 판정 대상)",
          all(r["condition_semantics"] == "UNRESOLVED" and r["scope"] == "UNRESOLVED"
              for r in RECS))
    check("★★ authority=False · consumable_by_evaluator=False",
          CANON["authority"] is False and CANON["consumable_by_evaluator"] is False)
    check("★★ populations.json 을 입력으로 쓰지 않았다",
          "populations.json" in CANON["input_ssot"]["excluded_input"])


SUITES = [test_baseline, test_mapping_invariants, test_no_source_mutation,
          test_id_stability, test_prohibitions]


def main():
    print("B3 canonical identity — 불변식 회귀")
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
