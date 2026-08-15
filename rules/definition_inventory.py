"""B5-0 — Definition Resolution Inventory. **inventory 만 만든다.**

⛔ 정의를 만들지 않는다. source 에 숫자·기간·기준이 없으면 여기서 만들지 않는다.
   `RPO 급둔화` 를 보고 `QoQ -10%` 를 만들면 즉시 FAIL 이다.
   `DRAM ASP 하락 전환` 을 보고 `2개월 연속 하락` 을 만드는 것도 FAIL 이다.
   지금 할 일은 각각 **무엇이 없어서 판정 불가능한지** 밝히는 것뿐이다.

⛔ canonical artifact 수정 금지 · definition_status 변경 금지 · threshold 보완 금지 ·
   evaluator 연결 금지 · source qualification 변경 금지 · READY/BLOCKED 변경 금지 ·
   Portfolio/MON/execution_reference 확장 금지 · DEFINED Rule 의 정의 패턴을
   UNDEFINED Rule 에 복사 금지.

★ `resolution_status` 는 **채우지 않는다.** 15건을 inventory 한 뒤 실제 상태 공간을
  보고 CIO 가 vocabulary 를 판정한다. B4 에서 했던 순서와 같다 —
  데이터가 어떤 경우를 갖는지 먼저 보고 vocabulary 를 고정한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rules"))
import canonicalize as K                                          # noqa: E402

DECOMP = os.path.join(ROOT, "rules", "decompose_full.json")
CANON = os.path.join(ROOT, "rules", "canonical_rules.json")
ROWS = os.path.join(ROOT, "_watchlist_rows.json")
OUT = os.path.join(ROOT, "rules", "definition_inventory.json")

# ★ 허용된 missing-definition 분류 — 원문에서 기계적으로 확인 가능한 수준까지만.
#   ⛔ 필요 없는 category 를 억지로 채우지 않는다. 새 category 를 만들지 않는다.
MISSING_COMPONENTS = {
    "threshold",             # 임계값(폭·수준)이 없다
    "time_window",           # 기간·지속 길이가 없다
    "comparison_baseline",   # 무엇 대비인지가 없다
    "observation_frequency", # 관측 주기(분기/연간 등)가 없다
    "aggregation",           # 여러 관측을 어떻게 합치는지가 없다
    "event_definition",      # 사건 자체의 기계적 정의가 없다
    "data_source",           # 어느 원천의 값인지가 없다 / 원천이 확보되지 않았다
}


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


# ── 사람이 판독한 inventory ─────────────────────────────────────────────
#   각 항목의 `quote` 는 반드시 해당 source 원문의 **부분문자열**이어야 한다
#   (빌드 시 검증한다). 없는 근거를 지어낼 수 없다.
ITEMS = [
    dict(rule="RULE-0002",
         missing=["threshold", "time_window", "comparison_baseline", "data_source"],
         why=("'하락' 의 폭도, '전환' 을 선언할 기간도, 무엇 대비 하락인지도 원문에 없다. "
              "DRAM ASP 자체가 Atlas 수집 대상이 아니라 원천도 지정돼 있지 않다."),
         evidence=[]),
    dict(rule="RULE-0018",
         missing=["threshold", "time_window", "comparison_baseline", "data_source"],
         why="같은 문구를 가진 다른 종목의 조건과 결핍 항목이 동일하다.",
         xref="RULE-0002 와 동일 문구 · B4-1 에서 NO_MERGE 로 판정됨 — 합치지 않는다",
         evidence=[]),
    dict(rule="RULE-0004",
         missing=["threshold", "comparison_baseline", "data_source"],
         why=("'하향' 의 폭과 비교 기준이 없다. 그리고 **누구의 capex 인지**가 원문에 없어 "
              "어느 원천의 값을 읽어야 하는지 정해지지 않는다."),
         evidence=[]),
    dict(rule="RULE-0009",
         missing=["event_definition", "threshold", "time_window"],
         why=("'박스권 돌파' 와 '재확인' 의 기계적 정의가 없다. 박스권 상단·돌파폭·재확인 기간 "
              "모두 원문에 없다. ★ 원문이 스스로 미정의를 선언하고 해소 예정 시점까지 지목한다."),
         evidence=[dict(source="TSM::편입 사유",
                        quote="Entry Language(돌파·재지지·누름의 기계적 정의)가 Undefined")]),
    dict(rule="RULE-0010",
         missing=["event_definition", "time_window", "comparison_baseline", "data_source"],
         why=("'상대강도' 산식과 '열위' 판정 기준이 없고 '지속' 의 길이도 없다. "
              "미국 가격 원천도 확보되지 않았다."),
         evidence=[]),
    dict(rule="RULE-0011",
         missing=["observation_frequency", "data_source"],
         why=("★ 임계값은 **있다**(`10%+ 고객 3개 미만`). 빠진 것은 그 비율을 어느 주기로 "
              "보는가다. 그리고 고객 집중도는 공시 본문 재무수치라 파싱이 구현돼 있지 않다. "
              "★ 원문이 결핍 항목을 스스로 지목하지만 값은 주지 않는다."),
         evidence=[dict(source="CRDO::편입 사유",
                        quote="고객 집중도 기준이 분기/연간 중 어느 것인지 미정의")]),
    dict(rule="RULE-0012",
         missing=["event_definition", "threshold", "time_window"],
         why=("'호실적' 의 기준도 '선반영' 의 판정법도 없고 주가 하락 폭도 없다. "
              "★ 원문은 정의 대신 **사용 제한**을 준다 — 정의가 아니라 제약이다."),
         evidence=[dict(source="ANET::편입 사유",
                        quote="주가 하락만으로는 제외하지 않는다")]),
    dict(rule="RULE-0015",
         missing=["threshold", "comparison_baseline", "data_source"],
         why=("★ 개수 조건은 **있다**(`2곳+`). 빠진 것은 '하향' 의 폭과 비교 기준이다. "
              "그리고 '하이퍼스케일러' 의 대상 목록이 원문에 없어 어느 값을 읽을지 정해지지 않는다."),
         evidence=[]),
    dict(rule="RULE-0016",
         missing=["time_window", "threshold"],
         why=("'실적 전' 이 며칠 전부터인지, '제한' 이 어느 수준까지인지 원문에 없다. "
              "`(PM)` 은 권위 출처 표기이지 정의가 아니다."),
         evidence=[]),
    dict(rule="RULE-0017",
         missing=["event_definition", "threshold", "data_source"],
         why=("'취소'·'축소' 의 판정 기준과 축소 폭이 없다. 고객 확약 정보는 공시 본문이라 "
              "파싱이 구현돼 있지 않다. ※ 별건으로, 원문의 `·` 가 AND 인지 OR 인지도 "
              "확정되지 않았다."),
         xref="결합 표기(AND/OR) 미확정은 B1 에서 이미 별도 보고된 사안이다",
         evidence=[]),
    dict(rule="RULE-0019",
         missing=["event_definition", "time_window", "comparison_baseline"],
         why=("'상대강도' 산식·'열위' 기준·'지속' 길이가 없다. "
              "★ 단 데이터는 확보돼 있다 — 두 한국 종목 확정 종가는 krx.py 로 읽힌다. "
              "**데이터가 아니라 정의만 없는** 사례다."),
         evidence=[]),
    dict(rule="RULE-0020",
         missing=["event_definition", "threshold", "data_source"],
         why=("'공급 확대' 의 기준과 '미확인' 의 판정법이 없다. 공시 본문 파싱 미구현. "
              "★ 원문은 현재 **상태 진술**만 준다 — 정의가 아니다."),
         evidence=[dict(source="005930.KS::편입 사유",
                        quote="HBM 공급 확대가 명시적으로 해소돼 성립하지 않는다")]),
    dict(rule="RULE-0021",
         missing=["event_definition", "data_source"],
         why=("★ 임계값은 **있다**(`45%cc`). 빠진 것은 '유의미' 의 기준이다 — "
              "**숫자가 있어도 정의가 완결이 아니다**의 대표 사례. Azure 성장률(cc) 은 미수집."),
         evidence=[]),
    dict(rule="RULE-0022",
         missing=["threshold", "time_window", "comparison_baseline", "data_source"],
         why=("'급둔화' 의 '급' 이 무엇인지, 어느 기간을 보는지, 무엇 대비인지 모두 없다. "
              "RPO 는 재무 본문이라 파싱 미구현."),
         evidence=[]),
    dict(rule="RULE-0025",
         missing=["event_definition", "time_window"],
         why=("'연속' 의 길이가 없고, **무엇을 한 번의 '끊김' 관측으로 볼지도 없다** — "
              "기관 분류 · 순매수가 없는 날의 취급 · 순매도 전환 여부가 모두 원문에 없다. "
              "★ 데이터는 확보돼 있다(krx.py 수급). "
              "★ 재검사 정정 — 최초 inventory 는 결핍을 time_window 하나로만 잡았다. "
              "원문 주석이 '연속' 만 미정의로 선언했고 그 자기 선언을 결핍 목록으로 "
              "그대로 채택했기 때문이다. 자기 선언은 작성자가 알아챈 것이지 빠짐없는 "
              "목록이 아니다. 같은 인벤토리에서 '열위'·'미확인'·'취소'·'돌파' 같은 상태 "
              "전이 술어는 모두 event_definition 을 받았다 — '끊김' 만 빠져 있었다."),
         evidence=[dict(source="298040.KS::탈락 조건#6",
                        quote="'연속'의 정의가 Undefined(8/15 Review #2 이월)"),
                   dict(source="298040.KS::탈락 조건#5",
                        quote="기관 순매수 연속 끊김")]),
]

# ★★ B5-2C 신설 — 정의 결핍이 아니라 **원문 구조의 미결**이다.
#   ⛔ MISSING_COMPONENTS 에 넣지 않는다. 그 어휘는 '정의에서 빠진 성분' 을 뜻하며,
#      아래는 원문이 어느 규칙을 말하는지 자체가 갈리는 문제다. 층이 다르다.
#   ⛔ 어느 읽기가 옳은지 여기서 고르지 않는다.
SOURCE_SEMANTICS = [
    dict(rule="RULE-0025",
         axis="semantic_scope",
         why=("「기관 순매수 연속 끊김」 은 「연속」 이 무엇에 걸리는지가 갈린다. "
              "① 「기관 순매수 연속」 이 끊긴다 — 순매수가 이어지던 상태가 끝날 때. "
              "② 「끊김」 이 연속된다 — 끊김 관측이 이어질 때. "
              "두 읽기는 서로 다른 규칙이며, 어느 쪽인지가 time_window 가 세는 대상을 "
              "바꾼다. 상위 artifact 어디에도 고정돼 있지 않다."),
         structural_note=("원문 구조화 단계가 이 구절을 한 조각으로 두고 나누지 않았다. "
                          "따라서 이 미결은 정의 결핍이 아니라 구조화 단계가 남긴 "
                          "미결이며, 정의 판정보다 앞선다."),
         evidence=[dict(source="298040.KS::탈락 조건#5",
                        quote="기관 순매수 연속 끊김")]),
    dict(rule="RULE-0017",
         axis="semantic_scope",
         why=("「HBM 예약 취소·LTA 축소」 는 가운데 결합 표기가 두 사건의 동시 충족을 "
              "뜻하는지 어느 하나의 충족을 뜻하는지가 갈린다. 두 읽기는 서로 다른 "
              "발동 조건이며, 각 사건을 아무리 잘 정의해도 이 결합이 정해지지 않으면 "
              "규칙이 언제 발동하는지가 둘로 남는다. 상위 artifact 어디에도 고정돼 "
              "있지 않다."),
         structural_note=("원문 구조화 단계가 이 결합 표기를 보존만 하고 실행 의미는 "
                          "정의하지 않았다. 따라서 이 미결은 정의 결핍이 아니라 구조화 "
                          "단계가 남긴 미결이며, 사건 정의보다 앞선다."),
         evidence=[dict(source="000660.KS::탈락 조건#1",
                        quote="HBM 예약 취소·LTA 축소")]),
]


def load_sources():
    decomp = json.load(open(DECOMP, encoding="utf-8"))
    frag = {f"{c['candidate_id']}#{f['split_index']}": f["raw_fragment"]
            for c in decomp["cells"] for f in c["fragments"]}
    rows = json.load(open(ROWS, encoding="utf-8"))["results"]
    cells = {f"{r['티커']}::편입 사유": r["편입 사유"] for r in rows if r.get("편입 사유")}
    return decomp, {**frag, **cells}


def build(out_path: str = OUT):
    decomp, srcs = load_sources()
    canon = json.load(open(CANON, encoding="utf-8"))
    recs = {r["canonical_rule_id"]: r for r in canon["canonical_rules"]}

    occ = {o["occurrence_id"]: o for o in K.evaluation_occurrences(decomp)}
    dstat = {f"{c['candidate_id']}#{f['split_index']}": f["definition_status"]
             for c in decomp["cells"] for f in c["fragments"]}

    undefined = [rid for rid, r in recs.items()
                 if dstat.get(r["source_occurrences"][0]) == "UNDEFINED"]
    defined = [rid for rid, r in recs.items()
               if dstat.get(r["source_occurrences"][0]) == "DEFINED"]

    errs, items = [], []
    seen = set()
    for it in ITEMS:
        rid = it["rule"]
        if rid not in recs:
            errs.append(f"{rid}: 존재하지 않는 canonical ID"); continue
        if rid in seen:
            errs.append(f"{rid}: 중복 항목"); continue
        seen.add(rid)
        if rid not in undefined:
            errs.append(f"{rid}: UNDEFINED 가 아닌데 inventory 에 있다"); continue
        bad = [m for m in it["missing"] if m not in MISSING_COMPONENTS]
        if bad:
            errs.append(f"{rid}: 허용 밖 missing_component {bad}")
        if not it["missing"]:
            errs.append(f"{rid}: missing_components 가 비어 있다")

        oid = recs[rid]["source_occurrences"][0]
        ctext = recs[rid]["condition_text"]

        # ★ 근거 인용은 반드시 실제 원문의 부분문자열이어야 한다
        for e in it["evidence"]:
            raw = srcs.get(e["source"])
            if raw is None:
                errs.append(f"{rid}: 존재하지 않는 근거 원천 {e['source']}")
            elif e["quote"] not in raw:
                errs.append(f"{rid}: 근거 인용이 원문에 없다 → {e['quote'][:30]!r}")

        # ★★ 정의를 만들지 않았는가 — why 안의 숫자는 전부 원문에서 와야 한다
        allowed_num = set(re.findall(r"\d+", ctext))
        for e in it["evidence"]:
            allowed_num |= set(re.findall(r"\d+", e["quote"]))
        invented = [n for n in re.findall(r"\d+", it["why"]) if n not in allowed_num]
        if invented:
            errs.append(f"{rid}: ★★ 원문에 없는 숫자를 썼다 {invented} — 정의를 만든 것이다")
        # ★ cross_reference 는 숫자 검사에서 제외되므로, 대신 **식별자만** 담도록 강제한다.
        #   설명 문장을 여기로 옮겨 숫자 검사를 우회하는 경로를 막는다.
        xr = it.get("xref")
        if xr:
            stray = [n for n in re.findall(r"\d+", xr)
                     if not re.search(rf"(RULE-\d*{n}|B{n}\b|B\d-{n}\b)", xr)]
            if stray:
                errs.append(f"{rid}: cross_reference 에 식별자가 아닌 숫자 {stray}")

        items.append({
            "canonical_rule_id": rid,
            "occurrence_id": oid,
            "condition_text": ctext,
            "rule_kind": recs[rid]["rule_kind"],
            "current_definition_status": "UNDEFINED",
            "missing_components": it["missing"],
            # 원문 안에 판정을 **완결시키는 정의**가 있는가
            "source_has_resolution": False,
            "resolution_evidence": it["evidence"],
            # ⛔ CIO 가 상태 공간을 보고 vocabulary 를 판정할 때까지 비워둔다
            "resolution_status": None,
            "why_not_deterministic": it["why"],
            # artifact 식별자는 여기에만 둔다 — why 에 섞으면 숫자 검사가 무력해진다
            "cross_reference": it.get("xref"),
        })

    missing_rules = sorted(set(undefined) - seen)
    if missing_rules:
        errs.append(f"inventory 에 없는 UNDEFINED rule: {missing_rules}")

    # ★★ 원문 구조 미결 — 정의 결핍과 층이 다르므로 별도로 검증한다
    semantics = []
    for sm in SOURCE_SEMANTICS:
        rid = sm["rule"]
        if rid not in recs:
            errs.append(f"{rid}: 존재하지 않는 canonical ID (source_semantics)"); continue
        if sm["axis"] in MISSING_COMPONENTS:
            errs.append(f"{rid}: ★★ 구조 미결 축 {sm['axis']!r} 가 정의 성분 어휘와 "
                        f"겹친다 — 층을 섞으면 안 된다")
        ctext = recs[rid]["condition_text"]
        allowed = set(re.findall(r"\d+", ctext))
        for e in sm["evidence"]:
            raw = srcs.get(e["source"])
            if raw is None:
                errs.append(f"{rid}: 존재하지 않는 근거 원천 {e['source']}")
            elif e["quote"] not in raw:
                errs.append(f"{rid}: 구조 미결 인용이 원문에 없다 → {e['quote'][:30]!r}")
            else:
                allowed |= set(re.findall(r"\d+", e["quote"]))
        for field in ("why", "structural_note"):
            bad = [n for n in re.findall(r"\d+", sm[field]) if n not in allowed]
            if bad:
                errs.append(f"{rid}: ★★ 구조 미결 {field} 에 원문 밖 숫자 {bad}")
        semantics.append({
            "canonical_rule_id": rid,
            "occurrence_id": recs[rid]["source_occurrences"][0],
            "condition_text": ctext,
            "axis": sm["axis"],
            "why": sm["why"],
            "structural_note": sm["structural_note"],
            "evidence": sm["evidence"],
            "resolution": None,          # ⛔ 여기서 고르지 않는다
        })

    payload = {
        "artifact": "B5-0 Definition Resolution Inventory",
        "status": "inactive preparation",
        "authority": False,
        "consumable_by_evaluator": False,
        "definitions_created": 0,
        "purpose": ("15개 UNDEFINED 각각에 대해 **무엇이 빠져 있어서 evaluator 가 "
                    "deterministic 하게 판정할 수 없는가** 만 구조화한다. 정의는 만들지 않는다."),
        "allowed_missing_components": sorted(MISSING_COMPONENTS),
        "resolution_status_note": ("⛔ 아직 채우지 않는다. 15건의 실제 상태 공간을 보고 "
                                   "CIO 가 vocabulary 를 판정한 뒤에 채운다."),
        "control_population_note": ("DEFINED 10건은 control population 으로 읽기만 했다. "
                                    "⛔ 그 정의 패턴을 UNDEFINED Rule 에 복사하지 않았다."),
        "counts": {"undefined": len(undefined), "inventoried": len(items),
                   "defined_control": len(defined)},
        "defined_control_population": sorted(defined),
        "decided_against": {"decompose_full_sha256": _sha(DECOMP),
                            "canonical_rules_sha256": _sha(CANON)},
        "items": items,
        "source_semantics_unresolved": semantics,
        "source_semantics_note": (
            "★ 정의 결핍이 아니다. 원문이 어느 규칙을 말하는지 자체가 갈리는 "
            "구조 미결이며, allowed_missing_components 어휘에 넣지 않는다. "
            "정의 판정보다 앞서 해소돼야 한다. ⛔ 여기서 고르지 않았다 — "
            "resolution 은 전부 비어 있다."),
        # ★ 관측된 상태 공간 — 토큰을 만들지 않고 **사례로만** 제시한다
        "observed_situations": [
            {"situation": "원문이 스스로 미정의를 선언하고 해소 예정 시점까지 지목한다",
             "rules": ["RULE-0009", "RULE-0025"]},
            {"situation": "원문이 결핍 항목을 지목하지만 값은 주지 않는다",
             "rules": ["RULE-0011"]},
            {"situation": "원문이 정의 대신 사용 제한을 준다",
             "rules": ["RULE-0012"]},
            {"situation": "원문이 현재 상태 진술만 준다 (정의 아님)",
             "rules": ["RULE-0020"]},
            {"situation": "임계값·개수는 있으나 수식어가 미정의다",
             "rules": ["RULE-0011", "RULE-0015", "RULE-0021"]},
            {"situation": "데이터는 확보돼 있고 정의만 없다",
             "rules": ["RULE-0019", "RULE-0025"]},
            {"situation": "원문에 단서가 전혀 없다",
             "rules": ["RULE-0002", "RULE-0018", "RULE-0004", "RULE-0010",
                       "RULE-0016", "RULE-0017", "RULE-0022"]},
        ],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return payload, errs


def main():
    p, errs = build()
    print(f"[B5-0] {OUT}")
    print(f"  UNDEFINED {p['counts']['undefined']} · inventoried {p['counts']['inventoried']} "
          f"· DEFINED control {p['counts']['defined_control']}")
    print(f"  definitions_created = {p['definitions_created']}")
    print(f"  resolution_status   = 전부 미기입 (CIO vocabulary 판정 대기)")
    import collections
    mc = collections.Counter(m for i in p["items"] for m in i["missing_components"])
    print("  missing_components 분포: " + " · ".join(f"{k} {v}" for k, v in mc.most_common()))
    if errs:
        print(f"\n★ 위반 {len(errs)}건")
        for e in errs:
            print("  ✗", e)
        sys.exit(1)
    print("  ✅ 위반 0")


if __name__ == "__main__":
    main()
