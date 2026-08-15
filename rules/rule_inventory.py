"""machine Rule Inventory — README 경계 다이어그램의 마지막 단계.

    config/rules.json        ← Rule SSOT / authority
            ↓
    machine Rule Inventory   ← 이 파일

기존 정본 계약만 따른다. 새 산식을 만들지 않는다.
  · §21-12 monitoring  — Rule Inventory 포함 · Evaluator Population 제외 (CIO 판정 ④)
  · §21-12 execution_reference — Rule Inventory 집계 대상 제외 (CIO 판정 ③)
  · §21-12 "통계에 포함되는 모든 객체는 고유 rule_id 를 갖는다."

⛔ evaluator 배선 금지 · `consumable_by_evaluator=true` 금지 · HOLD 해제 금지 ·
   상태 해소 금지 · rules.json 변경 금지 · execution_reference / non_rule_evidence /
   Portfolio Operation 의 Rule 승격 금지.

★ Inventory 에 들어간다는 것은 executable · READY · evaluator 소비 가능이라는 뜻이
  아니다. Rule Inventory 와 Evaluator Population 은 다른 집계다.

★ 권위의 방향 — evaluation rule 25건의 membership 과 상태는 `config/rules.json`
  에서만 온다. mapping 은 그 25건에 대해 **아무것도 기여하지 않으며**, 값이
  어긋나면 보정하지 않고 fail-closed 한다. mapping 이 유일한 출처인 것은 정본이
  Inventory 에 포함하라고 한 monitoring 객체뿐이다.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rules"))
import ssot_mapping as SM                                            # noqa: E402

RULES = os.path.join(ROOT, "config", "rules.json")
MAPPING = os.path.join(ROOT, "rules", "ssot_mapping.json")
MONID = os.path.join(ROOT, "rules", "monitoring_identity.json")
OUT = os.path.join(ROOT, "rules", "rule_inventory.json")

ARTIFACT = "machine Rule Inventory"

# ★ 집계 구성 — 전부 기존 계약이다. 새 분류를 만들지 않았다.
IN_INVENTORY = (SM.DEST_SSOT, SM.DEST_INVENTORY_ONLY)
OUT_OF_INVENTORY = (SM.DEST_EXCLUDED_EXEC, SM.DEST_EXCLUDED_NRE)

# ★ Evaluator Population — §21-12 상 evaluation rule 만. monitoring 은 제외.
EVALUATOR_POPULATION = (SM.DEST_SSOT,)

# ★ 집계 클래스별 귀속. 아래 CONTRACT 는 정본 문구이며 이 표가 그것과 어긋나면
#   검사가 거부한다 — 표를 고쳐서 통과시킬 수 없다.
IN_POPULATION_BY_CLASS = {"evaluation_rule": True, "monitoring": False}
CONSUMABLE_BY_CLASS = {"evaluation_rule": False, "monitoring": False}

# §21-12 · CIO 판정 ④ — 이 값은 계약이다. 코드가 이것을 따라야 한다.
CONTRACT_IN_POPULATION = {"evaluation_rule": True, "monitoring": False}
CONTRACT_CONSUMABLE = {"evaluation_rule": False, "monitoring": False}


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def build(out_path=OUT, rules_path=RULES, mapping_path=MAPPING, monid_path=MONID):
    errs = []
    rules_doc = json.load(open(rules_path, encoding="utf-8"))
    mapping = json.load(open(mapping_path, encoding="utf-8"))
    # ★ CIO 판정 2026-08-15 — monitoring_identity 는 DAG 상 **required input** 이다.
    #   ⛔ 부재 시 빈 identity 집합으로 진행하지 않는다. 그렇게 하면 §21-12 결핍이
    #      「ID 없는 객체를 세는 상태」로 조용히 되살아난다. fail-closed 한다.
    if not os.path.exists(monid_path):
        raise FileNotFoundError(
            f"required input 이 없다: {monid_path} — monitoring identity 없이 "
            f"Inventory 를 집계하지 않는다")
    monid_doc = json.load(open(monid_path, encoding="utf-8"))
    monid = {r["occurrence_id"]: r["monitoring_id"]
             for r in monid_doc["monitoring_identities"]}
    mp = {m["occurrence_id"]: m for m in mapping["mapping"]}

    # ── 입력 게이트 ──────────────────────────────────────────────────
    if rules_doc.get("authority") is not True:
        errs.append("config/rules.json 이 authority 가 아니다 — Inventory 의 기준이 될 수 없다")
    if rules_doc.get("consumable_by_evaluator") is not False:
        errs.append("consumable_by_evaluator 가 false 가 아니다 — 이 단계는 소비를 열지 않는다")
    if mapping["counts"]["conflicts"] or mapping["counts"]["held"]:
        errs.append("mapping 에 충돌·보류가 있다 — 집계하지 않는다")
    if _sha(mapping_path) != rules_doc["provenance"]["ssot_mapping_sha256"]:
        errs.append("ssot_mapping 이 rules.json 이 고정한 해시와 다르다")

    entries = []

    # ① evaluation rules — 출처는 config/rules.json **뿐**이다
    for r in rules_doc["rules"]:
        entries.append({
            "inventory_class": "evaluation_rule",
            "identity_source": "config/rules.json",
            "rule_id": r["rule_id"],
            "subject": r["subject"],
            "rule_kind": r["rule_kind"],
            "downstream_effect": r["downstream_effect"],
            "source_occurrences": r["source_occurrences"],
            "definition_status": r["definition_status"],
            "data_status": r["data_status"],
            "source_qualification": r["source_qualification"],
            "evaluator_status": r["evaluator_status"],
            "blocked_by": r["blocked_by"],
            "identity_namespace": "RULE",
            "in_evaluator_population": IN_POPULATION_BY_CLASS["evaluation_rule"],
            "evaluator_consumable": CONSUMABLE_BY_CLASS["evaluation_rule"],
        })

    # ② monitoring — 정본이 Inventory 에 포함하라고 한 객체. 출처는 mapping 뿐이다.
    for m in mapping["mapping"]:
        if m["destination"] != SM.DEST_INVENTORY_ONLY:
            continue
        entries.append({
            "inventory_class": "monitoring",
            "identity_source": "rules/monitoring_identity.json",
            # ★ §21-12 identity — CIO 판정 2026-08-15 ⓐ. monitoring 전용 namespace 다.
            #   ⛔ canonical evaluation rule identity 가 아니다.
            "rule_id": monid.get(m["occurrence_id"]),
            "identity_namespace": "MON",
            "occurrence_id": m["occurrence_id"],
            "subject": m["candidate_id"].split("::", 1)[0],
            "rule_kind": m["rule_kind"],
            "downstream_effect": m["downstream_effect"],
            "definition_status": m["definition_status"],
            "data_status": m["data_status"],
            "source_qualification": m["source_qualification"],
            "evaluator_status": m["evaluator_status"],
            "blocked_by": m["blocked_by"],
            "in_evaluator_population": IN_POPULATION_BY_CLASS["monitoring"],
            "evaluator_consumable": CONSUMABLE_BY_CLASS["monitoring"],
        })

    ev = [e for e in entries if e["inventory_class"] == "evaluation_rule"]
    mon = [e for e in entries if e["inventory_class"] == "monitoring"]

    # ══════════════════════════════════════════════════════════════════
    # 계약 검사 — 어긋나면 보정하지 않고 fail-closed 한다
    # ══════════════════════════════════════════════════════════════════

    # V-1 rules.json 25건이 전부 Inventory 에서 식별 가능한가
    rj_ids = {r["rule_id"] for r in rules_doc["rules"]}
    inv_ids = {e["rule_id"] for e in ev}
    for x in sorted(rj_ids - inv_ids):
        errs.append(f"{x}: rules.json 에 있는데 Inventory 에서 유실됐다")
    for x in sorted(inv_ids - rj_ids):
        errs.append(f"{x}: rules.json 에 없는 Rule 이 Inventory 에 있다")
    if len(ev) != rules_doc["rule_count"]:
        errs.append(f"evaluation rule {len(ev)} ≠ rules.json {rules_doc['rule_count']}")

    # V-2 monitoring 이 정본 계약대로 포함됐는가
    map_mon = [m for m in mapping["mapping"] if m["destination"] == SM.DEST_INVENTORY_ONLY]
    if len(mon) != mapping["counts"]["rule_inventory_only"]:
        errs.append(f"monitoring {len(mon)} ≠ mapping 이 집계한 "
                    f"{mapping['counts']['rule_inventory_only']} — 포함 계약 위반")
    if len(mon) != len(map_mon):
        errs.append(f"monitoring {len(mon)} ≠ mapping 목록 {len(map_mon)}")
    if any(e["rule_kind"] != "MON" for e in mon):
        errs.append("monitoring 집계에 MON 이 아닌 객체가 있다")

    # V-3 · V-4 제외 객체가 새어 들어오지 않았는가
    inv_occ = {o for e in ev for o in e["source_occurrences"]} | \
              {e["occurrence_id"] for e in mon}
    for m in mapping["mapping"]:
        if m["destination"] in OUT_OF_INVENTORY and m["occurrence_id"] in inv_occ:
            errs.append(f"{m['occurrence_id']}: {m['destination']} 가 Inventory 에 "
                        f"들어왔다 — 집계 대상이 아니다")

    # V-5 종류를 섞지 않았는가 — 숫자가 맞아도 섞였으면 실패다.
    #   ★ 먼저 귀속 표가 정본 계약과 같은지 본다. 표를 고쳐서 통과시킬 수 없다.
    for _cls in CONTRACT_IN_POPULATION:
        if IN_POPULATION_BY_CLASS.get(_cls) != CONTRACT_IN_POPULATION[_cls]:
            errs.append(f"{_cls}: Evaluator Population 귀속이 §21-12 계약과 다르다 "
                        f"— Inventory 포함을 evaluator 자격으로 해석했다")
        if CONSUMABLE_BY_CLASS.get(_cls) != CONTRACT_CONSUMABLE[_cls]:
            errs.append(f"{_cls}: 소비 자격이 계약과 다르다 — 이 단계는 소비를 열지 않는다")
    for e in mon:
        if e["in_evaluator_population"]:
            errs.append(f"{e['occurrence_id']}: monitoring 이 Evaluator Population 에 있다")
        if e["downstream_effect"] != "monitoring":
            errs.append(f"{e['occurrence_id']}: monitoring 의 효과가 monitoring 이 아니다")
    for e in ev:
        if not e["in_evaluator_population"]:
            errs.append(f"{e['rule_id']}: evaluation rule 이 Population 에서 빠졌다")
        if e["rule_kind"] not in ("FAL", "ENT"):
            errs.append(f"{e['rule_id']}: evaluation rule 의 kind 가 {e['rule_kind']!r} 다")

    # V-6 upstream 이 rules.json 을 덮어쓰려 하는가 — 보정하지 않고 실패한다
    for e in ev:
        for o in e["source_occurrences"]:
            m = mp.get(o)
            if m is None:
                errs.append(f"{e['rule_id']}: mapping 에 {o} 가 없다")
                continue
            for f in ("definition_status", "data_status", "source_qualification",
                      "evaluator_status", "blocked_by", "downstream_effect"):
                if m[f] != e[f]:
                    errs.append(f"{e['rule_id']}: upstream 의 {f} 가 rules.json 과 다르다 "
                                f"— 자동 보정하지 않는다 (rules.json 이 authority)")

    # V-7 §21-12 — 통계에 포함되는 모든 객체는 고유 rule_id 를 갖는다
    noid = [e for e in entries if not e["rule_id"]]
    if noid:
        errs.append(f"§21-12 위반 — Inventory 집계 대상 {len(noid)}건에 rule_id 가 없다 "
                    f"({noid[0].get('occurrence_id')} 외). ⛔ 이 단계에서 ID 를 만들지 "
                    f"않는다. 새 identity 부여는 CIO 판정 대상이다")
    ids = [e["rule_id"] for e in entries if e["rule_id"]]
    if len(set(ids)) != len(ids):
        errs.append("§21-12 위반 — rule_id 가 중복됐다")

    # V-7b MON identity 가 evaluation 쪽으로 새지 않는가
    _rule_ids = {r["rule_id"] for r in rules_doc["rules"]}
    for e in mon:
        mid = e["rule_id"]
        if mid and not mid.startswith("MON-"):
            errs.append(f"{e['occurrence_id']}: monitoring identity 가 MON namespace 가 "
                        f"아니다 ({mid})")
        if mid in _rule_ids:
            errs.append(f"{mid}: monitoring identity 가 rules.json 의 Rule 과 겹친다")
        if e["identity_namespace"] != "MON":
            errs.append(f"{e['occurrence_id']}: identity namespace 가 MON 이 아니다")
    for e in ev:
        if e["identity_namespace"] != "RULE":
            errs.append(f"{e['rule_id']}: evaluation identity namespace 가 RULE 이 아니다")
    if {e["rule_id"] for e in mon} & _rule_ids:
        errs.append("두 namespace 가 충돌한다")
    # monitoring identity 는 occurrence 와 1:1 이어야 한다
    _mo = [e["occurrence_id"] for e in mon]
    _mi = [e["rule_id"] for e in mon if e["rule_id"]]
    if len(set(_mi)) != len(_mi) or (len(_mi) and len(_mi) != len(set(_mo))):
        errs.append("monitoring identity 가 occurrence 와 1:1 이 아니다")

    # V-8 READY 함정 — 집계에 나타나도 소비 경로는 0 이어야 한다
    for e in entries:
        if e["evaluator_consumable"]:
            errs.append(f"{e.get('rule_id') or e.get('occurrence_id')}: 소비 가능으로 "
                        f"표시됐다 — 이번 단계는 소비를 열지 않는다")
        for k in ("stage_action", "portfolio_action", "order", "trade", "executable"):
            if k in e:
                errs.append(f"{e.get('rule_id')}: 실행 의미 필드 {k!r} 가 붙었다")

    # V-9 이 단계가 rules.json 을 건드리지 않았는가
    if os.path.abspath(out_path) == os.path.abspath(rules_path):
        errs.append("Inventory 가 config/rules.json 을 덮어쓰려 한다")
    if os.path.dirname(os.path.abspath(out_path)) == \
            os.path.dirname(os.path.abspath(rules_path)):
        errs.append("Inventory 산출물이 authority 디렉터리에 쓰이려 한다")

    # V-10 기존 항등식 — POPULATION_SEPARATION 이 이미 세운 관계만 검증한다
    total_occ = len(mapping["mapping"])
    parts = (mapping["counts"]["rule_ssot_candidates"]
             + mapping["counts"]["rule_inventory_only"]
             + mapping["counts"]["excluded_execution_reference"]
             + mapping["counts"]["excluded_non_rule_evidence"])
    if total_occ != parts:
        errs.append(f"기존 항등식 위반 — occurrence {total_occ} ≠ 네 population 합 {parts}")

    counts = {
        "rule_inventory_total": len(entries),
        "evaluation_rule": len(ev),
        "monitoring": len(mon),
        "excluded_execution_reference": mapping["counts"]["excluded_execution_reference"],
        "excluded_non_rule_evidence": mapping["counts"]["excluded_non_rule_evidence"],
        "evaluator_population": sum(1 for e in entries if e["in_evaluator_population"]),
        "evaluator_consumable": sum(1 for e in entries if e["evaluator_consumable"]),
        "evaluator_ready": sum(1 for e in ev if e["evaluator_status"] == "READY"),
        "evaluator_blocked": sum(1 for e in ev if e["evaluator_status"] == "BLOCKED"),
        "definition_undefined": sum(1 for e in ev if e["definition_status"] == "UNDEFINED"),
        "data_missing": sum(1 for e in ev if e["data_status"] == "MISSING"),
        "source_unresolved": sum(1 for e in ev
                                 if e["source_qualification"] == "SOURCE_UNRESOLVED"),
        "entries_without_rule_id": len(noid),
    }

    payload = {
        "artifact": ARTIFACT,
        "authority": False,
        "authority_source": "config/rules.json (evaluation rule 25건의 membership · 상태)",
        "consumable_by_evaluator": False,
        "production_state": "HOLD 유지 — 이번 단계는 실행 연결 승인이 아니다",
        "evaluator_wiring": "미연결",
        "scope_contract": {
            "monitoring": "Rule Inventory 포함 · Evaluator Population 제외 (§21-12 · CIO 판정 ④)",
            "execution_reference": "Rule Inventory 집계 대상 제외 (§21-12 · CIO 판정 ③)",
            "non_rule_evidence": "Rule Inventory 대상 아님",
            "identity": "§21-12 — 통계에 포함되는 모든 객체는 고유 rule_id 를 갖는다",
        },
        "membership_note": ("★ Inventory 포함은 executable · READY · evaluator 소비 "
                            "가능을 뜻하지 않는다. Rule Inventory 와 Evaluator "
                            "Population 은 다른 집계다."),
        "states_hidden": 0,
        "statuses_resolved": 0,
        "ids_created": 0,
        "counts": counts,
        "identity_note": ("★ monitoring 은 `MON-nnnn`, evaluation rule 은 "
                          "`RULE-nnnn` 로 namespace 가 분리된다. identity 를 가졌다는 "
                          "사실이 evaluator 자격 · 실행 자격 · rules.json 편입을 "
                          "뜻하지 않는다."),
        "provenance": {
            "rules_json_sha256": _sha(rules_path),
            "ssot_mapping_sha256": _sha(mapping_path),
            "monitoring_identity_sha256": _sha(monid_path),   # required — 위에서 보장
        },
        "entries": entries,
    }

    # ★ fail-closed publish
    if errs:
        return payload, errs
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return payload, errs


if __name__ == "__main__":
    p, e = build()
    c = p["counts"]
    print(f"[Rule Inventory] {OUT}")
    print(f"  Inventory 총 {c['rule_inventory_total']} = evaluation rule "
          f"{c['evaluation_rule']} + monitoring {c['monitoring']}")
    print(f"  집계 제외 — execution_reference {c['excluded_execution_reference']} · "
          f"non_rule_evidence {c['excluded_non_rule_evidence']}")
    print(f"  Evaluator Population {c['evaluator_population']} "
          f"(READY {c['evaluator_ready']} · BLOCKED {c['evaluator_blocked']}) · "
          f"소비 가능 {c['evaluator_consumable']}")
    print(f"  UNDEFINED {c['definition_undefined']} · MISSING {c['data_missing']} · "
          f"SOURCE_UNRESOLVED {c['source_unresolved']}")
    print(f"  숨긴 상태 {p['states_hidden']} · 해소한 상태 {p['statuses_resolved']} · "
          f"만든 ID {p['ids_created']}")
    for x in e:
        print("  ⛔", x)
    print("  ✅ 위반 0" if not e else f"  ⛔ 위반 {len(e)} — 발행하지 않았다")
    sys.exit(1 if e else 0)
