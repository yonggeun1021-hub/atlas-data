"""B2-1 Population Separation — view 만 만든다.

⛔ canonical rule_id 부여 금지 · dedup/병합 금지 · evaluator 연결 금지 ·
   원문 occurrence 분할 금지 · 새 객체 생성 금지.

이 스크립트는 `decompose_full.json` 의 106개 occurrence 를 다섯 population 으로
**나누어 보여주기만** 한다. 산출물은 index view 이며 authority 가 아니다.
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rules"))
import validate_decomposition as V                                  # noqa: E402
import vocabulary as VC                                             # noqa: E402

# ★ Portfolio Operation 후보 — **notes 문자열을 기계가 읽어 판별하지 않는다.**
#   notes 는 설명·근거·미결 사유만 담으며 machine semantics 의 입력이 아니다(CIO 제한).
#   따라서 CIO 가 명시적으로 판정한 occurrence id 를 여기에 **직접 선언**한다.
#   ⛔ 새 Portfolio 객체를 만드는 것이 아니라 기존 occurrence 를 가리키는 index 다.
PORTFOLIO_OPERATION_CANDIDATES = [
    "ANET::탈락 조건#3",       # CIO 판정 B2-0 #2 — 'CRDO 에 슬롯 이양' 부분
]


def load():
    full = json.load(open(os.path.join(ROOT, "rules", "decompose_full.json"),
                          encoding="utf-8"))
    raws = V._raw_texts(os.path.join(ROOT, "config", "rules.candidates.json"))
    rep = V.validate(full, raws)
    items = []
    for c in full["cells"]:
        for f in c["fragments"]:
            items.append({
                "id": f"{c['candidate_id']}#{f['split_index']}",
                "candidate_id": c["candidate_id"],
                "source_cell": c["source_cell"],
                "split_index": f["split_index"],
                "raw_fragment": f["raw_fragment"],
                **{k: f.get(k) for k in ("object_role", "rule_kind", "downstream_effect",
                                         "definition_status", "data_status",
                                         "data_capability", "source_qualification")},
            })
    D = {x["id"]: x for x in rep["derived"]}
    for it in items:
        d = D.get(it["id"])
        it["evaluator_status"] = d["evaluator_status"] if d else None
        it["blocked_by"] = d["blocked_by"] if d else []
    return full, rep, items


def separate(items):
    conn = {i["id"] for i in items if i["raw_fragment"] in VC.CONNECTIVES}
    pops = collections.OrderedDict()
    pops["evaluation_rules"] = [i for i in items
                                if i["object_role"] == "rule_candidate"
                                and i["rule_kind"] in ("FAL", "ENT")]
    pops["monitoring_inventory"] = [i for i in items
                                    if i["object_role"] == "rule_candidate"
                                    and i["rule_kind"] == "MON"]
    pops["execution_references"] = [i for i in items
                                    if i["object_role"] == "execution_reference"]
    pops["non_rule_evidence"] = [i for i in items
                                 if i["object_role"] == "non_rule_evidence"]
    idx = {i["id"]: i for i in items}
    pops["portfolio_operation_candidates"] = [idx[k] for k in PORTFOLIO_OPERATION_CANDIDATES
                                              if k in idx]
    return pops, conn


def dist(rows, field):
    c = collections.Counter(str(r[field]) for r in rows)
    return " · ".join(f"`{k}` {v}" for k, v in c.most_common())


def duplicate_clusters(items):
    """탐색만 한다. 병합하지 않고 canonical ID 도 만들지 않는다."""
    norm = lambda s: re.sub(r"\s+", "", s)
    by = collections.defaultdict(list)
    for i in items:
        if i["object_role"] == "non_rule_evidence":
            continue                        # 주석·부재표식·결합표기는 대상 아님
        by[norm(i["raw_fragment"])].append(i)
    exact = {k: v for k, v in by.items()
             if len({x["candidate_id"] for x in v}) > 1}
    return exact


def main():
    full, rep, items = load()
    pops, conn = separate(items)

    out = {
        "artifact": "B2-1 Population Separation (view)",
        "status": "inactive preparation",
        "authority": False,
        "consumable_by_evaluator": False,
        "note": ("occurrence 를 나누어 보여주는 index view 다. canonical rule_id 미부여 · "
                 "dedup 미착수 · 병합 없음 · 새 객체 생성 없음."),
        "source_artifact_sha256_note": "decompose_full.json (B2 adjudicated) 기준",
        "populations": {k: [i["id"] for i in v] for k, v in pops.items()},
        "duplicate_clusters_探索": {k: [i["id"] for i in v]
                                  for k, v in duplicate_clusters(items).items()},
    }
    with open(os.path.join(ROOT, "rules", "populations.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")

    P = print
    P("# B2-1 Population Separation\n")
    P("**canonical rule_id 미부여 · dedup 미착수 · 병합 없음 · 새 객체 생성 없음** — "
      "occurrence index view 다.\n")
    P(f"총 occurrence **{len(items)}** = " +
      " + ".join(f"{k} {len(v)}" for k, v in pops.items() if k != "portfolio_operation_candidates")
      + f"  *(portfolio_operation_candidates {len(pops['portfolio_operation_candidates'])}건은 "
        f"위 population 중 하나를 가리키는 중복 index)*\n")

    for name, rows in pops.items():
        P(f"\n## `{name}` — {len(rows)}건\n")
        if not rows:
            P("(없음)\n"); continue
        if name == "portfolio_operation_candidates":
            P("> ⛔ 새 Portfolio 객체가 아니다. CIO 판정 B2-0 #2 로 명시된 occurrence 를 "
              "가리키는 **index 뿐**이며 원문을 분할하지 않았다. 해당 occurrence 는 "
              "`evaluation_rules` 에도 그대로 남아 있다.\n")
        if name in ("evaluation_rules", "monitoring_inventory"):
            P(f"- rule_kind: {dist(rows,'rule_kind')}")
        P(f"- definition_status: {dist(rows,'definition_status')}")
        P(f"- data_status: {dist(rows,'data_status')}")
        P(f"- source_qualification: {dist(rows,'source_qualification')}")
        if name == "evaluation_rules":
            P(f"- **evaluator_status: {dist(rows,'evaluator_status')}**")
            bb = collections.Counter()
            for r in rows:
                for x in r["blocked_by"]:
                    bb[x] += 1
            P(f"- 차단 원인(중복 계상): " + " · ".join(f"`{k}` {v}" for k, v in bb.most_common()))
        if name == "non_rule_evidence":
            P(f"- 내역: 결합 표기 {sum(1 for r in rows if r['id'] in conn)} · "
              f"주석·부재표식 {sum(1 for r in rows if r['id'] not in conn)}")
        P("")
        P("| candidate_id | cell | # | kind | def | data | src_qual |")
        P("|---|---|---:|---|---|---|---|")
        for r in rows:
            if r["id"] in conn:
                continue                     # 결합 표기 24건은 표에서 접는다
            P(f"| `{r['candidate_id']}` | {r['source_cell']} | {r['split_index']} | "
              f"{r['rule_kind']} | {r['definition_status']} | {r['data_status']} | "
              f"{r['source_qualification']} |")
        if name == "non_rule_evidence":
            P(f"\n*(결합 표기 {len(conn)}건은 표에서 생략 — `또는`·`/`·`→`·`+`·`·`)*")

    P("\n\n## cross-cell duplicate 후보 — 탐색만\n")
    P("⛔ **병합하지 않았다. canonical ID 도 만들지 않았다.**\n")
    dc = duplicate_clusters(items)
    P(f"### (a) 원문 문자열 동일 — {len(dc)} cluster\n")
    for k, v in dc.items():
        P(f"- `{v[0]['raw_fragment']}`")
        for i in v:
            P(f"    - `{i['id']}` · kind={i['rule_kind']} · def={i['definition_status']} "
              f"· data={i['data_status']}")
    P("\n### (b) 사람이 판독한 의미 중복 후보 — 2건 (B1 보고에서 이월)\n")
    P("| 후보 | occurrence | 왜 자동 병합하면 안 되는가 |")
    P("|---|---|---|")
    P("| TSMC 월매출 약화 → 매수 취소·Ready 해제 | `TSM::다음 이벤트#5` · `TSM::편입 사유#4`(pilot) | "
      "편입 사유 쪽은 `⚠ PROTOTYPE — 정본 규칙 아님` 표식 아래에 있다. 권위가 다르다 |")
    P("| TSMC $398 이탈 | `TSM::기술적 무효화#1`(FAL·강등 검토) · `TSM::편입 사유#5`(pilot·UNRESOLVED) | "
      "같은 숫자인데 효과가 다르다(무효화 vs 청산). 중복이 아니라 서로 다른 층일 수 있다 |")
    P("\n*(b)의 편입 사유 occurrence 는 46칸 밖(pilot)이므로 이번 population 에 포함되지 않는다.*")

    P("\n\n## semantic unresolved gate\n")
    P(f"- `rule_candidate` 중 `rule_kind ∉ {{FAL, ENT, MON}}` : "
      f"**{len(rep['semantic_unresolved'])}건** → "
      f"{'PASS' if rep['semantic_gate_pass'] else 'FAIL — B2 종료 불가'}")
    for x in rep["semantic_unresolved"]:
        P(f"  - ✗ {x}")
    fld = collections.Counter()
    for i in items:
        for k in ("definition_status", "data_status", "data_capability"):
            if i.get(k) == VC.UNRESOLVED:
                fld[k] += 1
    P(f"- ⛔ 이 gate 와 섞지 않는 별도 dimension: " +
      " · ".join(f"`{k}` {v}" for k, v in fld.most_common()))


if __name__ == "__main__":
    main()
