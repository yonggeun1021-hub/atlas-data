"""human-reviewed decomposition & mapping — README 경계 다이어그램의 다음 단계.

    config/rules.candidates.json
            ↓
    human-reviewed decomposition & mapping     ← 이 파일
            ↓
    config/rules.json                          ← Rule SSOT / authority (미착수)

⛔ 이 단계가 하지 않는 것 (전부 fail-closed 로 강제한다)
  · `config/rules.json` 생성 금지
  · canonical 수정 금지 · evaluator 연결 금지
  · `UNDEFINED` / `MISSING` 임의 해소 금지
  · 새 정의 · 새 임계값 · 새 dependency · 새 단계 생성 금지
  · 기존 CIO 판정 재판정 금지

이 단계가 하는 일은 하나다 — 인간 판정이 반영된 현재 분해 조각들을 Rule SSOT
후보로 **정확히 매핑**하고, 상위 산출물들 사이의 불일치를 찾아내는 것이다.
값을 만들지 않고 이어 붙이기만 하며, 이어 붙일 수 없으면 보류하거나 충돌로
올린다. ★ `rule_candidate` 는 executable Rule 이라는 뜻이 아니다.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rules"))
import population_separation as PS                                   # noqa: E402

DECOMP = os.path.join(ROOT, "rules", "decompose_full.json")
CANON = os.path.join(ROOT, "rules", "canonical_rules.json")
MERGE = os.path.join(ROOT, "rules", "merge_decision.json")
INV = os.path.join(ROOT, "rules", "definition_inventory.json")
DEC = os.path.join(ROOT, "rules", "definition_decision.json")
CARDS = os.path.join(ROOT, "rules", "decision_cards.json")
OUT = os.path.join(ROOT, "rules", "ssot_mapping.json")
RULES_JSON = os.path.join(ROOT, "config", "rules.json")

# ★ 승격 산출물의 이름 — 이 단계는 그 파일을 만들지 않고, 남이 만든 것만 확인한다.
PROMOTION_ARTIFACT = "Rule SSOT (config/rules.json)"

# ★ Rule SSOT 모집단 경계 — B3 이 이미 선언한 것을 그대로 쓴다. 새로 정하지 않는다.
SSOT_KINDS = ("FAL", "ENT")

# ★ 목적지 어휘 — 전부 기존 산출물이 이미 쓰는 말이다. 새 토큰을 만들지 않았다.
DEST_SSOT = "rule_ssot_candidate"
DEST_INVENTORY_ONLY = "rule_inventory_only"          # MON — Evaluator Population 제외
DEST_EXCLUDED_EXEC = "excluded::execution_reference"  # Rule Inventory 집계 제외
DEST_EXCLUDED_NRE = "excluded::non_rule_evidence"


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def _load():
    full, rep, items = PS.load()
    pops, conn = PS.separate(items)
    canon = json.load(open(CANON, encoding="utf-8"))
    merge = json.load(open(MERGE, encoding="utf-8"))
    inv = json.load(open(INV, encoding="utf-8"))
    dec = json.load(open(DEC, encoding="utf-8"))
    cards = json.load(open(CARDS, encoding="utf-8"))
    return full, rep, items, pops, conn, canon, merge, inv, dec, cards


def build(out_path=OUT):
    full, rep, items, pops, conn, canon, merge, inv, dec, cards = _load()
    errs = []
    holds = []

    by_occ = {}
    for r in canon["canonical_rules"]:
        for o in r["source_occurrences"]:
            by_occ[o] = r

    inv_by_rule = {i["canonical_rule_id"]: i for i in inv["items"]}
    dec_by_rule = {i["canonical_rule_id"]: i for i in dec["items"]}

    # B5-2B 판정을 규칙별로 모은다 — 값은 옮기지 않고 존재 여부만 본다.
    units_by_rule = {}
    for c in cards["cards"]:
        for rid in c["affected_rules"]:
            units_by_rule.setdefault(rid, []).append({
                "order": c["order"],
                "decision_unit": c["decision_unit"],
                "component": c["decision_unit"].split("::", 1)[1],
                "decided": c["cio_decision"] is not None,
                "execution_status": c["execution_status"],
            })

    ssot_ids = {i["id"] for i in pops["evaluation_rules"]}
    portfolio = {i["id"] for i in pops["portfolio_operation_candidates"]}

    mapped = []
    for it in items:
        rec = {
            "occurrence_id": it["id"],
            "candidate_id": it["candidate_id"],
            "source_cell": it["source_cell"],
            "split_index": it["split_index"],
            "raw_fragment": it["raw_fragment"],
            # ★ 인간 판정이 들어간 세 필드 — 그대로 옮긴다
            "object_role": it["object_role"],
            "rule_kind": it["rule_kind"],
            "downstream_effect": it["downstream_effect"],
            # ★ 실행불가 상태 — 해소하지 않고 보존한다
            "definition_status": it["definition_status"],
            "data_status": it["data_status"],
            "data_capability": it["data_capability"],
            "source_qualification": it["source_qualification"],
            "evaluator_status": it["evaluator_status"],
            "blocked_by": it["blocked_by"],
            "portfolio_operation_candidate": it["id"] in portfolio,
            "is_connective": it["raw_fragment"] in PS.VC.CONNECTIVES,
        }

        if it["object_role"] == "rule_candidate" and it["rule_kind"] in SSOT_KINDS:
            rec["destination"] = DEST_SSOT
            r = by_occ.get(it["id"])
            if r is None:
                rec["mapping_status"] = "보류"
                rec["hold_reason"] = "canonical identity 가 없다 — B3 을 다시 열어야 한다"
                holds.append(rec["occurrence_id"])
            else:
                rec["canonical_rule_id"] = r["canonical_rule_id"]
                rec["condition_semantics"] = r["condition_semantics"]
                rec["scope"] = r["scope"]
                rec["merge_status"] = ("병합 없음 — B4-1 approved merge 0건"
                                       if not merge["merge_performed"] else "병합됨")
                rid = r["canonical_rule_id"]
                if rid in inv_by_rule:
                    i = inv_by_rule[rid]
                    rec["definition_resolution"] = dec_by_rule[rid]["resolution_status"] \
                        if rid in dec_by_rule else None
                    rec["missing_components"] = i["missing_components"]
                    rec["data_capability_axis"] = \
                        dec_by_rule.get(rid, {}).get("data_capability_axis", [])
                else:
                    rec["definition_resolution"] = "정의 결핍 없음 (defined control population)"
                    rec["missing_components"] = []
                    rec["data_capability_axis"] = []
                rec["decision_units"] = sorted(units_by_rule.get(rid, []),
                                               key=lambda x: x["order"])
                rec["mapping_status"] = "성공"
        elif it["object_role"] == "rule_candidate" and it["rule_kind"] == "MON":
            rec["destination"] = DEST_INVENTORY_ONLY
            rec["scope_note"] = full["inventory_scope"]["monitoring"]
            rec["mapping_status"] = "성공"
        elif it["object_role"] == "execution_reference":
            rec["destination"] = DEST_EXCLUDED_EXEC
            rec["scope_note"] = full["inventory_scope"]["execution_reference"]
            rec["mapping_status"] = "성공"
        elif it["object_role"] == "non_rule_evidence":
            rec["destination"] = DEST_EXCLUDED_NRE
            rec["mapping_status"] = "성공"
        else:
            rec["destination"] = None
            rec["mapping_status"] = "보류"
            rec["hold_reason"] = f"object_role 이 {it['object_role']!r} 이라 목적지가 없다"
            holds.append(rec["occurrence_id"])
        mapped.append(rec)

    # ══════════════════════════════════════════════════════════════════
    # 충돌 검사 — 값을 고치지 않는다. 어긋나면 올린다.
    # ══════════════════════════════════════════════════════════════════
    conflicts = []

    def clash(msg):
        conflicts.append(msg)
        errs.append(msg)

    # C-1 B2-0 종료 확인 — UNRESOLVED object_role 이 남아 있으면 mapping 자체가 이르다
    for it in items:
        if it["object_role"] == "UNRESOLVED":
            clash(f"{it['id']}: object_role 이 아직 UNRESOLVED 다 — B2-0 미종료")

    # C-2 SSOT 모집단 ↔ canonical 양방향 일치
    canon_occ = {o for r in canon["canonical_rules"] for o in r["source_occurrences"]}
    for x in sorted(ssot_ids - canon_occ):
        clash(f"{x}: Rule SSOT 모집단인데 canonical identity 가 없다")
    for x in sorted(canon_occ - ssot_ids):
        clash(f"{x}: canonical record 가 가리키는데 SSOT 모집단이 아니다")

    # C-3 원문 표류 — canonical 이 들고 있는 문자열이 현재 분해와 다른가
    idx = {i["id"]: i for i in items}
    for r in canon["canonical_rules"]:
        for o in r["source_occurrences"]:
            if o not in idx:
                clash(f"{r['canonical_rule_id']}: source_occurrence {o} 가 분해에 없다")
                continue
            t = idx[o]["raw_fragment"]
            if t != r["condition_text"]:
                clash(f"{r['canonical_rule_id']}: condition_text 가 원문과 다르다")
            if hashlib.sha256(t.encode("utf-8")).hexdigest() != r["condition_text_sha256"]:
                clash(f"{r['canonical_rule_id']}: condition_text_sha256 이 원문과 다르다")

    # C-4 B4-1 이 판정한 canonical 수와 실제 수
    if len(canon["canonical_rules"]) != merge["canonical_records_after"]:
        clash(f"canonical {len(canon['canonical_rules'])}건 ≠ B4-1 판정 "
              f"{merge['canonical_records_after']}건")

    # C-5 UNDEFINED 모집단 ↔ B5-0 인벤토리
    undef = {rec["canonical_rule_id"] for rec in mapped
             if rec.get("canonical_rule_id") and rec["definition_status"] == "UNDEFINED"}
    if undef != set(inv_by_rule):
        clash(f"UNDEFINED 규칙 집합이 B5-0 인벤토리와 다르다: "
              f"{sorted(undef ^ set(inv_by_rule))}")

    # C-6 정의 성분 결핍이 B5-2B 판정으로 전부 덮였는가
    #     ★ 예외는 B5-1 이 `data_capability_axis` 로 분리한 성분뿐이다 —
    #       데이터 원천 부재는 정의 결핍이 아니므로 CIO 정의 질문이 되지 않는다.
    for rid, i in inv_by_rule.items():
        covered = {u["component"] for u in units_by_rule.get(rid, [])}
        cap = set(dec_by_rule.get(rid, {}).get("data_capability_axis", []))
        for m in i["missing_components"]:
            if m not in covered and m not in cap:
                clash(f"{rid}::{m}: 결핍인데 판정도 없고 data capability 축도 아니다")

    # C-7 I-3 fail-closed — UNDEFINED 가 실행 가능으로 새지 않는가
    for rec in mapped:
        if rec["definition_status"] == "UNDEFINED":
            if rec["evaluator_status"] == "READY":
                clash(f"{rec['occurrence_id']}: UNDEFINED 인데 READY 다 — I-3 위반")
            if "DEFINITION_UNDEFINED" not in (rec["blocked_by"] or []):
                clash(f"{rec['occurrence_id']}: UNDEFINED 인데 차단 사유에 없다")
        if rec["data_status"] == "MISSING" and rec["evaluator_status"] == "READY":
            clash(f"{rec['occurrence_id']}: MISSING 인데 READY 다")

    # C-8 상태 보존 — 발행된 mapping 이 **디스크에 고정된 분해 SSOT** 와 같은가.
    #   ★ 메모리 안의 같은 객체끼리 비교하면 아무것도 검사하지 않는다. 그래서 파일을
    #     다시 읽어 대조한다 — 이 단계가 상태를 해소·보정해 발행하는 경로를 막는다.
    on_disk = {}
    for cell in json.load(open(DECOMP, encoding="utf-8"))["cells"]:
        for fr in cell["fragments"]:
            on_disk[f"{cell['candidate_id']}#{fr['split_index']}"] = fr
    for rec in mapped:
        src = on_disk.get(rec["occurrence_id"])
        if src is None:
            clash(f"{rec['occurrence_id']}: 고정된 분해 SSOT 에 없는 occurrence 다")
            continue
        for f in ("object_role", "rule_kind", "downstream_effect", "definition_status",
                  "data_status", "data_capability", "source_qualification",
                  "raw_fragment"):
            if rec[f] != src.get(f):
                clash(f"{rec['occurrence_id']}: 발행된 {f} 가 고정된 분해 SSOT 와 다르다")

    # C-9 B5-2B 가 가리키는 규칙이 전부 실재하는가
    all_rids = {r["canonical_rule_id"] for r in canon["canonical_rules"]}
    for rid in units_by_rule:
        if rid not in all_rids:
            clash(f"{rid}: B5-2B 판정이 존재하지 않는 규칙을 가리킨다")

    # C-10 결합 표기 보존 — '또는' 같은 조각이 사라지지 않았는가
    for it in items:
        if it["raw_fragment"] in PS.VC.CONNECTIVES:
            if it["id"] not in {m["occurrence_id"] for m in mapped}:
                clash(f"{it['id']}: 결합 표기가 mapping 에서 사라졌다")

    # C-11 이 단계는 승격 단계가 아니다 — rules.json 을 **이 단계가** 쓰지 않는다.
    #   ★ rules.json 의 존재 자체를 금지하지는 않는다(CIO 승인 2026-08-15 로 승격됨).
    #     금지하는 것은 이 단계가 그 파일을 만들거나 덮어쓰는 것이다.
    if os.path.abspath(out_path) == os.path.abspath(RULES_JSON):
        clash("mapping 이 config/rules.json 을 쓰려 한다 — 이 단계는 승격 단계가 아니다")
    if os.path.exists(RULES_JSON):
        _r = json.load(open(RULES_JSON, encoding="utf-8"))
        if _r.get("artifact") != PROMOTION_ARTIFACT:
            clash(f"config/rules.json 을 {_r.get('artifact')!r} 가 만들었다 — "
                  f"승격 단계의 산출물이 아니다")

    counts = {
        "mapping_targets": len(mapped),
        "mapped_ok": sum(1 for m in mapped if m["mapping_status"] == "성공"),
        "held": len(holds),
        "conflicts": len(conflicts),
        "rule_ssot_candidates": sum(1 for m in mapped if m["destination"] == DEST_SSOT),
        "rule_inventory_only": sum(1 for m in mapped
                                   if m["destination"] == DEST_INVENTORY_ONLY),
        "excluded_execution_reference": sum(1 for m in mapped
                                            if m["destination"] == DEST_EXCLUDED_EXEC),
        "excluded_non_rule_evidence": sum(1 for m in mapped
                                          if m["destination"] == DEST_EXCLUDED_NRE),
        "canonical_records": len(canon["canonical_rules"]),
        "definition_undefined": sum(1 for m in mapped
                                    if m["destination"] == DEST_SSOT
                                    and m["definition_status"] == "UNDEFINED"),
        "data_missing": sum(1 for m in mapped if m["destination"] == DEST_SSOT
                            and m["data_status"] == "MISSING"),
        "source_unresolved": sum(1 for m in mapped if m["destination"] == DEST_SSOT
                                 and m["source_qualification"] == "SOURCE_UNRESOLVED"),
        "evaluator_blocked": sum(1 for m in mapped if m["destination"] == DEST_SSOT
                                 and m["evaluator_status"] == "BLOCKED"),
        "evaluator_ready": sum(1 for m in mapped if m["destination"] == DEST_SSOT
                               and m["evaluator_status"] == "READY"),
        "decision_units_decided": sum(1 for c in cards["cards"]
                                      if c["cio_decision"] is not None),
        "decision_units_open": sum(1 for c in cards["cards"]
                                   if c["cio_decision"] is None),
    }

    payload = {
        "artifact": "human-reviewed decomposition & mapping",
        "status": "inactive preparation",
        "authority": False,
        "consumable_by_evaluator": False,
        # ★ 이 단계 자신의 입장만 적는다. 승격 여부(바깥 세상의 상태)를 여기 적으면
        #   승격이 이 파일을 바꾸고, 승격이 고정한 이 파일의 해시가 깨진다.
        "promotion_state": ("이 단계는 config/rules.json 을 만들지 않는다 — "
                            "승격은 별도 CIO 판정이다"),
        "purpose": ("인간 판정이 반영된 분해 조각을 Rule SSOT 후보로 매핑한다. "
                    "값을 만들지 않고 잇기만 하며, 이을 수 없으면 보류 · 어긋나면 충돌로 "
                    "올린다."),
        "definitions_created": 0,
        "candidate_values_created": 0,
        "targets_selected": 0,
        "statuses_resolved": 0,
        "rule_candidate_note": ("★ `rule_candidate` 는 executable Rule 이라는 뜻이 아니다. "
                                "무엇인가(object_role) · 계산 가능한가(definition_status) · "
                                "실행 가능한가(evaluator_status) 는 서로 다른 축이며 "
                                "이 단계에서 섞지 않는다."),
        "population_boundary": canon["input_ssot"]["population_boundary"],
        "inventory_scope": full["inventory_scope"],
        "b2_adjudication": full["b2_adjudication"]["status"],
        "counts": counts,
        "holds": holds,
        "conflicts": conflicts,
        "decided_against": {
            "decompose_full_sha256": _sha(DECOMP),
            "canonical_rules_sha256": _sha(CANON),
            "merge_decision_sha256": _sha(MERGE),
            "definition_inventory_sha256": _sha(INV),
            "definition_decision_sha256": _sha(DEC),
            "decision_cards_sha256": _sha(CARDS),
        },
        "mapping": mapped,
    }

    # ★ fail-closed publish — 위반이 있으면 쓰지 않는다 (B5-2B 와 같은 규약)
    if errs:
        return payload, errs
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return payload, errs


if __name__ == "__main__":
    p, e = build()
    c = p["counts"]
    print(f"[mapping] {OUT}")
    print(f"  대상 {c['mapping_targets']} · 성공 {c['mapped_ok']} · 보류 {c['held']} · "
          f"충돌 {c['conflicts']}")
    print(f"  Rule SSOT 후보 {c['rule_ssot_candidates']} · inventory only "
          f"{c['rule_inventory_only']} · 제외 "
          f"{c['excluded_execution_reference'] + c['excluded_non_rule_evidence']}")
    print(f"  UNDEFINED {c['definition_undefined']} · MISSING {c['data_missing']} · "
          f"SOURCE_UNRESOLVED {c['source_unresolved']}")
    print(f"  evaluator BLOCKED {c['evaluator_blocked']} · READY {c['evaluator_ready']}")
    print(f"  내가 만든 정의 {p['definitions_created']} · 해소한 상태 "
          f"{p['statuses_resolved']}")
    for x in e:
        print("  ⛔", x)
    print("  ✅ 위반 0" if not e else f"  ⛔ 위반 {len(e)}")
    sys.exit(1 if e else 0)
