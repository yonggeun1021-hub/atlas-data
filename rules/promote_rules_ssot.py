"""config/rules.json — Rule SSOT 승격 (CIO 승인 2026-08-15).

승인 범위는 **Rule 정의·상태의 authority SSOT 생성**까지다.
⛔ evaluator 사용 승인이 아니다 · executable 승격이 아니다 ·
   Production HOLD 해제가 아니다 · 매매/Stage/Portfolio 동작 변경이 아니다.

승격되는 것은 human-reviewed decomposition & mapping 을 통과한
`rule_ssot_candidate` **25건뿐**이다. MON · execution_reference ·
non_rule_evidence 는 끌어올리지 않는다.

★ 이 단계는 값을 만들지 않는다. 상위 산출물의 값을 **그대로** 옮기고,
  옮긴 것이 원본과 같은지 스스로 대조한다. 하나라도 어긋나면 쓰지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rules"))
import ssot_mapping as SM                                            # noqa: E402

CANON = os.path.join(ROOT, "rules", "canonical_rules.json")
CARDS = os.path.join(ROOT, "rules", "decision_cards.json")
MAPPING = os.path.join(ROOT, "rules", "ssot_mapping.json")
OUT = os.path.join(ROOT, "config", "rules.json")

ARTIFACT = "Rule SSOT (config/rules.json)"

# ★ 승격 대상 경계 — mapping 이 이미 판정한 것을 그대로 쓴다. 새로 정하지 않는다.
PROMOTED_DESTINATION = SM.DEST_SSOT

# ★ 승격해도 바뀌지 않는 것 — 이 값들이 바뀌면 승인 범위를 벗어난 것이다.
FROZEN_FLAGS = {
    "consumable_by_evaluator": False,
    "production_state": "HOLD 유지 — 이번 승격은 실행 연결 승인이 아니다",
    "evaluator_wiring": "미연결",
}


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def build(out_path=OUT, mapping_path=MAPPING):
    errs = []
    mapping = json.load(open(mapping_path, encoding="utf-8"))
    canon = {r["canonical_rule_id"]: r
             for r in json.load(open(CANON, encoding="utf-8"))["canonical_rules"]}
    cards = json.load(open(CARDS, encoding="utf-8"))
    card_by_unit = {c["decision_unit"]: c for c in cards["cards"]}

    # ── 승격 전 게이트 ────────────────────────────────────────────────
    if mapping["counts"]["conflicts"]:
        errs.append(f"mapping 충돌 {mapping['counts']['conflicts']}건 — 승격하지 않는다")
    if mapping["counts"]["held"]:
        errs.append(f"mapping 보류 {mapping['counts']['held']}건 — 승격하지 않는다")

    # ★ 상위 산출물 고정 대조 — mapping 이 판정한 그 파일들을 그대로 싣는가.
    #   ⛔ 같은 파일을 읽어 자기끼리 비교하면 아무것도 검사하지 않는다. 그래서
    #     mapping 이 박아 둔 해시와 대조한다 — mapping 이후 상위가 바뀐 경로를 잡는다.
    for _name, _path, _key in (("canonical_rules", CANON, "canonical_rules_sha256"),
                               ("decision_cards", CARDS, "decision_cards_sha256")):
        if _sha(_path) != mapping["decided_against"][_key]:
            errs.append(f"{_name}: mapping 이 고정한 해시와 다르다 — mapping 이후 상위 "
                        f"산출물이 바뀌었다. 승격하지 않는다")

    src = [m for m in mapping["mapping"] if m["destination"] == PROMOTED_DESTINATION]
    other = [m for m in mapping["mapping"] if m["destination"] != PROMOTED_DESTINATION]

    rules = []
    for m in src:
        rid = m.get("canonical_rule_id")
        if rid not in canon:
            errs.append(f"{m['occurrence_id']}: canonical identity 가 없다 — 승격 불가")
            continue
        c = canon[rid]

        # B5-2B 판정을 **원문 그대로** 싣는다. 요약·재서술하지 않는다.
        decisions = []
        for u in m.get("decision_units", []):
            card = card_by_unit[u["decision_unit"]]
            decisions.append({
                "decision_unit": card["decision_unit"],
                "card_order": card["order"],
                "execution_status": card["execution_status"],
                "cio_decision": card["cio_decision"],
            })

        rules.append({
            # identity — B3 의 opaque ID 를 재사용한다. 재발급하지 않는다.
            "rule_id": rid,
            "subject": c["subject"],
            "rule_kind": c["rule_kind"],
            "downstream_effect": m["downstream_effect"],
            "condition_text": c["condition_text"],
            "condition_text_sha256": c["condition_text_sha256"],
            "condition_semantics": c["condition_semantics"],
            "scope": c["scope"],
            "source_occurrences": c["source_occurrences"],
            # 상태 — 해소하지 않고 그대로 싣는다
            "definition_status": m["definition_status"],
            "data_status": m["data_status"],
            "data_capability": m["data_capability"],
            "source_qualification": m["source_qualification"],
            "evaluator_status": m["evaluator_status"],
            "blocked_by": m["blocked_by"],
            # 정의 결핍과 그 판정
            "definition_resolution": m.get("definition_resolution"),
            "missing_components": m.get("missing_components", []),
            "data_capability_axis": m.get("data_capability_axis", []),
            "cio_definition_decisions": decisions,
            "portfolio_operation_candidate": m["portfolio_operation_candidate"],
        })

    rules.sort(key=lambda r: r["rule_id"])

    # ── 승격 후 자기 대조 ────────────────────────────────────────────
    if len(rules) != mapping["counts"]["rule_ssot_candidates"]:
        errs.append(f"승격 수 {len(rules)} ≠ mapping 후보 "
                    f"{mapping['counts']['rule_ssot_candidates']}")
    if len(rules) != mapping["counts"]["canonical_records"]:
        errs.append(f"승격 수 {len(rules)} ≠ canonical {mapping['counts']['canonical_records']}")

    ids = [r["rule_id"] for r in rules]
    if len(set(ids)) != len(ids):
        errs.append("rule_id 가 중복됐다")
    if set(ids) != set(canon):
        errs.append(f"canonical 과 양방향 일치하지 않는다: {sorted(set(ids) ^ set(canon))}")

    # 제외 객체 유입 0
    promoted_occ = {o for r in rules for o in r["source_occurrences"]}
    for m in other:
        if m["occurrence_id"] in promoted_occ:
            errs.append(f"{m['occurrence_id']}: 제외 객체가 Rule SSOT 로 새어 들어왔다")
        if m["destination"] in (SM.DEST_INVENTORY_ONLY, SM.DEST_EXCLUDED_EXEC,
                                SM.DEST_EXCLUDED_NRE):
            continue
        errs.append(f"{m['occurrence_id']}: 알 수 없는 목적지 {m['destination']!r}")

    # 원문·provenance 불변
    for r in rules:
        c = canon[r["rule_id"]]
        if r["condition_text"] != c["condition_text"]:
            errs.append(f"{r['rule_id']}: condition_text 가 canonical 과 다르다")
        if hashlib.sha256(r["condition_text"].encode("utf-8")).hexdigest() \
                != r["condition_text_sha256"]:
            errs.append(f"{r['rule_id']}: condition_text_sha256 이 본문과 맞지 않는다")
        if r["source_occurrences"] != c["source_occurrences"]:
            errs.append(f"{r['rule_id']}: source_occurrences 가 canonical 과 다르다")

    # 상태 개수 불변 — mapping 이 보고한 수와 같아야 한다
    got = {
        "definition_undefined": sum(1 for r in rules
                                    if r["definition_status"] == "UNDEFINED"),
        "data_missing": sum(1 for r in rules if r["data_status"] == "MISSING"),
        "source_unresolved": sum(1 for r in rules
                                 if r["source_qualification"] == "SOURCE_UNRESOLVED"),
        "evaluator_blocked": sum(1 for r in rules if r["evaluator_status"] == "BLOCKED"),
        "evaluator_ready": sum(1 for r in rules if r["evaluator_status"] == "READY"),
    }
    for k, v in got.items():
        if mapping["counts"][k] != v:
            errs.append(f"{k}: 승격 전 {mapping['counts'][k]} → 승격 후 {v} 로 변했다")

    # I-3 fail-closed — UNDEFINED 가 실행 가능으로 새지 않는다
    for r in rules:
        if r["definition_status"] == "UNDEFINED":
            if r["evaluator_status"] == "READY":
                errs.append(f"{r['rule_id']}: UNDEFINED 인데 READY 다 — I-3 위반")
            if "DEFINITION_UNDEFINED" not in (r["blocked_by"] or []):
                errs.append(f"{r['rule_id']}: UNDEFINED 인데 차단 사유에 없다")
        if r["data_status"] == "MISSING" and r["evaluator_status"] == "READY":
            errs.append(f"{r['rule_id']}: MISSING 인데 READY 다")

    # B5-2B 판정 — 미판정이 실리거나 금지 표기가 사라지지 않았는가.
    #   ★ 판정 원문 자체가 B5-2B 와 같은지는 위의 해시 고정 대조가 담당한다.
    #     같은 파일에서 읽은 값끼리 비교하는 검사는 두지 않는다 — 통과해도 의미가 없다.
    for r in rules:
        for d in r["cio_definition_decisions"]:
            if d["cio_decision"] is None:
                errs.append(f"{d['decision_unit']}: 미판정인데 승격에 실렸다")
            if "금지" not in d["execution_status"]:
                errs.append(f"{d['decision_unit']}: evaluator 연결 금지 표기가 사라졌다")

    payload = {
        "artifact": ARTIFACT,
        "schema_note": "정본 §21-9① — SSOT 판정은 이 파일의 승격 시점에 발생한다.",
        "authority": True,
        "promotion": {
            "approved_by": "CIO",
            "source": "CIO 승인 2026-08-15 — config/rules.json Rule SSOT 승격",
            "scope": ("Rule 정의 · 상태의 authority SSOT 생성까지. "
                      "evaluator 사용 승인 · executable 승격 · Production HOLD 해제 · "
                      "매매 · Stage · Portfolio 동작 변경은 승인 범위가 아니다."),
            "promoted_population": "human-reviewed mapping 의 rule_ssot_candidate 25건",
            "not_promoted": ("MON(rule_inventory_only) · execution_reference · "
                             "non_rule_evidence 는 승격하지 않는다."),
        },
        **FROZEN_FLAGS,
        "authority_note": ("★ 오늘 이후 Rule membership 과 Rule 상태의 현재 SSOT 는 이 "
                           "파일이다. rules.candidates.json · decomposition · B2 · B5 "
                           "중간 산출물은 provenance 이자 재현용 upstream 이며 이 파일을 "
                           "역으로 덮어쓸 수 없다. 변경은 기존 Rule governance 절차를 "
                           "거친다."),
        "executability_note": ("★ authority 와 executability 는 다른 축이다. 이 파일에 "
                               "있다는 사실은 계산 가능하다는 뜻도, 실행 가능하다는 뜻도 "
                               "아니다. `definition_status` · `data_status` · "
                               "`evaluator_status` 를 각각 본다."),
        "ready_note": ("★ evaluator_status=READY 는 소비 승인이 아니다. "
                       "`consumable_by_evaluator=false` 이므로 이 승격만으로 어떤 Rule 도 "
                       "소비 경로에 연결되지 않으며, Stage 변경 · 투자 판단으로 이어지지 "
                       "않는다."),
        "definitions_created": 0,
        "statuses_resolved": 0,
        "rule_count": len(rules),
        "state_counts": got,
        "provenance": {
            "ssot_mapping_sha256": _sha(MAPPING),
            "canonical_rules_sha256": _sha(CANON),
            "decision_cards_sha256": _sha(CARDS),
            **mapping["decided_against"],
        },
        "rules": rules,
    }

    # ★ fail-closed publish — 위반이 있으면 authority 파일을 쓰지 않는다
    if errs:
        return payload, errs
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return payload, errs


if __name__ == "__main__":
    p, e = build()
    s = p["state_counts"]
    print(f"[Rule SSOT] {OUT}")
    print(f"  Rule {p['rule_count']} · authority {p['authority']} · "
          f"consumable_by_evaluator {p['consumable_by_evaluator']}")
    print(f"  UNDEFINED {s['definition_undefined']} · MISSING {s['data_missing']} · "
          f"SOURCE_UNRESOLVED {s['source_unresolved']}")
    print(f"  BLOCKED {s['evaluator_blocked']} · READY {s['evaluator_ready']}")
    print(f"  내가 만든 정의 {p['definitions_created']} · 해소한 상태 "
          f"{p['statuses_resolved']}")
    print(f"  production {p['production_state']} · evaluator {p['evaluator_wiring']}")
    for x in e:
        print("  ⛔", x)
    print("  ✅ 위반 0" if not e else f"  ⛔ 위반 {len(e)}")
    sys.exit(1 if e else 0)
