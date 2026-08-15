"""B5-0 — definition resolution inventory 불변식 회귀 (음성 포함)

핵심 하나: **정의를 만들지 않았는가.**
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "rules"))
import definition_inventory as D                              # noqa: E402
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


INV = json.load(open(os.path.join(_ROOT, "rules", "definition_inventory.json"),
                     encoding="utf-8"))
CANON = json.load(open(os.path.join(_ROOT, "rules", "canonical_rules.json"), encoding="utf-8"))
DECOMP = json.load(open(os.path.join(_ROOT, "rules", "decompose_full.json"), encoding="utf-8"))
RECS = {r["canonical_rule_id"]: r for r in CANON["canonical_rules"]}
DSTAT = {f"{c['candidate_id']}#{f['split_index']}": f["definition_status"]
         for c in DECOMP["cells"] for f in c["fragments"]}


def rebuild_with(items):
    """ITEMS 를 바꿔치기해 빌더의 검증이 실제로 잡는지 본다.

    ★ 반드시 임시 경로에 쓴다 — 음성 테스트가 실제 산출물을 덮어쓰면
      테스트가 검사 대상을 오염시킨다. (실제로 한 번 그랬다)
    """
    import tempfile
    orig = D.ITEMS
    try:
        D.ITEMS = items
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            tmp = f.name
        return D.build(out_path=tmp)[1]
    finally:
        D.ITEMS = orig
        os.unlink(tmp)


def test_coverage():
    print("\n[D-0] 15/15 coverage")
    und = [rid for rid, r in RECS.items()
           if DSTAT.get(r["source_occurrences"][0]) == "UNDEFINED"]
    check("★★ UNDEFINED 15건", len(und) == 15, str(len(und)))
    check("★★ inventory 15건", len(INV["items"]) == 15, str(len(INV["items"])))
    check("★★ 15/15 정확히 대응", {i["canonical_rule_id"] for i in INV["items"]} == set(und))
    check("★★ DEFINED 를 inventory 에 넣지 않았다",
          all(DSTAT[i["occurrence_id"]] == "UNDEFINED" for i in INV["items"]))
    check("★ DEFINED control population 10건이 기록돼 있다",
          len(INV["defined_control_population"]) == 10)
    check("★★ [음성] UNDEFINED 하나를 빠뜨리면 잡힌다",
          any("inventory 에 없는" in e for e in rebuild_with(D.ITEMS[:-1])))
    check("★★ [음성] DEFINED rule 을 inventory 에 넣으면 잡힌다",
          any("UNDEFINED 가 아닌데" in e for e in rebuild_with(
              D.ITEMS + [dict(rule="RULE-0001", missing=["threshold"], why="x", evidence=[])])))


def test_no_definitions_created():
    print("\n[D-1] ★★ 정의를 만들지 않았다")
    check("★★ definitions_created = 0", INV["definitions_created"] == 0)
    check("★★ 정의를 담을 수 있는 필드가 스키마에 없다",
          not any(k in i for i in INV["items"]
                  for k in ("threshold", "threshold_value", "time_window_value",
                            "definition", "resolved_definition", "proposed_definition",
                            "baseline", "window_days", "enum")))
    check("★★ resolution_status 는 전부 미기입",
          all(i["resolution_status"] is None for i in INV["items"]))

    # ★★ 서술문 안의 숫자는 전부 원문에서 와야 한다
    bad = []
    for i in INV["items"]:
        allowed = set(re.findall(r"\d+", i["condition_text"]))
        for e in i["resolution_evidence"]:
            allowed |= set(re.findall(r"\d+", e["quote"]))
        bad += [(i["canonical_rule_id"], n)
                for n in re.findall(r"\d+", i["why_not_deterministic"]) if n not in allowed]
    check("★★ 서술에 원문 밖 숫자가 0건", not bad, str(bad))

    # 음성 — CIO 가 예로 든 그 오류를 실제로 주입한다
    inj = copy.deepcopy(D.ITEMS)
    inj[-1] = {**inj[-1], "why": "'연속' 은 3거래일 연속으로 본다."}
    check("★★ [음성] `연속 = 3거래일` 을 만들면 잡힌다",
          any("원문에 없는 숫자" in e for e in rebuild_with(inj)), )
    inj = copy.deepcopy(D.ITEMS)
    inj = [x if x["rule"] != "RULE-0022" else {**x, "why": "RPO 가 QoQ -10% 이하면 급둔화다."}
           for x in inj]
    check("★★ [음성] `RPO 급둔화 = QoQ -10%` 를 만들면 잡힌다",
          any("원문에 없는 숫자" in e for e in rebuild_with(inj)))
    inj = [x if x["rule"] != "RULE-0002" else {**x, "why": "2개월 연속 하락이면 전환이다."}
           for x in copy.deepcopy(D.ITEMS)]
    check("★★ [음성] `DRAM ASP = 2개월 연속 하락` 을 만들면 잡힌다",
          any("원문에 없는 숫자" in e for e in rebuild_with(inj)))
    inj = [x if x["rule"] != "RULE-0022" else {**x, "why": "x", "xref": "임계값은 10 이다"}
           for x in copy.deepcopy(D.ITEMS)]
    check("★★ [음성] cross_reference 로 숫자를 우회하면 잡힌다",
          any("식별자가 아닌 숫자" in e for e in rebuild_with(inj)))


def test_evidence_is_real():
    print("\n[D-2] 근거를 지어내지 않았다")
    frag = {f"{c['candidate_id']}#{f['split_index']}": f["raw_fragment"]
            for c in DECOMP["cells"] for f in c["fragments"]}
    rows = json.load(open(os.path.join(_ROOT, "_watchlist_rows.json"),
                         encoding="utf-8"))["results"]
    srcs = {**frag, **{f"{r['티커']}::편입 사유": r["편입 사유"] for r in rows if r.get("편입 사유")}}
    ev = [(i["canonical_rule_id"], e) for i in INV["items"] for e in i["resolution_evidence"]]
    check(f"★ 근거 인용 {len(ev)}건", len(ev) >= 5, str(len(ev)))
    check("★★ 모든 인용이 실제 원문의 부분문자열",
          all(e["quote"] in srcs.get(e["source"], "") for _, e in ev))
    check("★★ [음성] 없는 인용을 넣으면 잡힌다",
          any("근거 인용이 원문에 없다" in e for e in rebuild_with(
              [{**D.ITEMS[0], "evidence": [dict(source="TSM::편입 사유", quote="존재하지 않는 문장")]}]
              + D.ITEMS[1:])))
    check("★★ [음성] 없는 원천을 가리키면 잡힌다",
          any("존재하지 않는 근거 원천" in e for e in rebuild_with(
              [{**D.ITEMS[0], "evidence": [dict(source="ZZZ::없는 칸", quote="x")]}]
              + D.ITEMS[1:])))
    check("★★ source_has_resolution 은 15건 전부 false",
          all(i["source_has_resolution"] is False for i in INV["items"]))


def test_vocabulary_closed():
    print("\n[D-3] missing_components 어휘가 닫혀 있다")
    used = {m for i in INV["items"] for m in i["missing_components"]}
    check("★★ 허용 집합 밖 토큰 0", used <= D.MISSING_COMPONENTS, str(used - D.MISSING_COMPONENTS))
    check("★ 빈 missing_components 0", all(i["missing_components"] for i in INV["items"]))
    check("★ 억지로 채우지 않았다 — 쓰이지 않은 category 가 있다",
          D.MISSING_COMPONENTS - used, str(D.MISSING_COMPONENTS - used))
    check("★★ [음성] 새 category 를 만들면 잡힌다",
          any("허용 밖 missing_component" in e for e in rebuild_with(
              [{**D.ITEMS[0], "missing": ["volatility_band"]}] + D.ITEMS[1:])))


def test_upstream_untouched():
    print("\n[D-4] 상위 artifact 를 건드리지 않았다")
    check("★★ decompose_full 해시 불변",
          INV["decided_against"]["decompose_full_sha256"]
          == D._sha(os.path.join(_ROOT, "rules", "decompose_full.json")))
    check("★★ canonical_rules 해시 불변",
          INV["decided_against"]["canonical_rules_sha256"]
          == D._sha(os.path.join(_ROOT, "rules", "canonical_rules.json")))
    ev = {r["source_occurrences"][0] for r in RECS.values()}
    check("★★ definition_status 변경 0 — evaluation 25 = UNDEFINED 15 / DEFINED 10",
          sum(1 for o in ev if DSTAT[o] == "UNDEFINED") == 15
          and sum(1 for o in ev if DSTAT[o] == "DEFINED") == 10
          and len(ev) == 25)
    check("★★ canonical record 25 불변 · occurrence 1개씩",
          len(RECS) == 25 and all(len(r["source_occurrences"]) == 1 for r in RECS.values()))
    check("★★ source text 변경 0",
          all(i["condition_text"] == RECS[i["canonical_rule_id"]]["condition_text"]
              for i in INV["items"]))
    check("★ condition_semantics / scope 여전히 UNRESOLVED",
          all(r["condition_semantics"] == "UNRESOLVED" and r["scope"] == "UNRESOLVED"
              for r in RECS.values()))
    check("★ DEFINED 10건 비변경 — inventory 가 이들을 건드리지 않았다",
          not ({i["canonical_rule_id"] for i in INV["items"]}
               & set(INV["defined_control_population"])))


def test_not_consumable():
    print("\n[D-5] evaluator 로 흘러가지 않는다")
    check("★★ authority = False", INV["authority"] is False)
    check("★★ consumable_by_evaluator = False", INV["consumable_by_evaluator"] is False)
    check("★ 상태 공간이 토큰이 아니라 사례로 제시된다",
          all("situation" in s and "rules" in s for s in INV["observed_situations"]))
    check("★★ observed_situations 가 실제 rule 만 가리킨다",
          all(r in RECS for s in INV["observed_situations"] for r in s["rules"]))
    check("★ resolution_status vocabulary 가 CIO 대기임이 명시된다",
          "CIO" in INV["resolution_status_note"])


SUITES = [test_coverage, test_no_definitions_created, test_evidence_is_real,
          test_vocabulary_closed, test_upstream_untouched, test_not_consumable]


def main():
    print("B5-0 definition inventory — 불변식 회귀")
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
