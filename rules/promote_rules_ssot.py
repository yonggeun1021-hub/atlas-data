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
import vocabulary as VC                                              # noqa: E402

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


# ══════════════════════════════════════════════════════════════════════
# P0 Definition Application pilot — CIO 승인 2026-08-15
#   이미 확정된 CIO 판정을 Rule SSOT 의 상태에 **적용**하는 단계다.
#   ⛔ 새 정의를 만들지 않는다 · 판정의 의미를 보충·수정하지 않는다 ·
#      threshold 를 추가하지 않는다 · data source 를 고르지 않는다 ·
#      READY 를 목표값으로 강제하지 않는다.
#   ★ 일반 규칙이 아니라 **명시적 allowlist** 다. UNDEFINED 15건 일괄 해제는
#     CIO 가 금지했다 — pilot 통과 후 확대 여부를 다시 판정한다.
DEFINITION_APPLICATION_PILOT = {
    # ── P0 pilot (1차) ────────────────────────────────────────────────
    "RULE-0019": "CIO 판정 2026-08-15 · P0 Definition Application pilot",
    "RULE-0025": "CIO 판정 2026-08-15 · P0 Definition Application pilot",
    # ── 2차 확대 (CIO 승인 2026-08-15) ────────────────────────────────
    #   ★ 이 7건은 적용해도 `data_status=MISSING` 이라 READY 로 가지 않는다.
    #     그래서 오히려 좋은 검증 조건이다 — 정의 기술부채만 줄고 실제 잔존
    #     dependency(`DATA_MISSING` 등)가 그대로 드러나야 한다.
    "RULE-0002": "CIO 판정 2026-08-15 · Definition Application 2차 확대",
    "RULE-0004": "CIO 판정 2026-08-15 · Definition Application 2차 확대",
    "RULE-0012": "CIO 판정 2026-08-15 · Definition Application 2차 확대",
    "RULE-0015": "CIO 판정 2026-08-15 · Definition Application 2차 확대",
    "RULE-0017": "CIO 판정 2026-08-15 · Definition Application 2차 확대",
    "RULE-0018": "CIO 판정 2026-08-15 · Definition Application 2차 확대",
    "RULE-0020": "CIO 판정 2026-08-15 · Definition Application 2차 확대",
    # ── 최종 확대 (CIO 판정 2026-08-15 · data_source 경계 확정) ────────
    #   ★ 이 4건은 `data_source` 만 capability 축에 남는다. 의미는 확정돼 있다.
    "RULE-0010": "CIO 판정 2026-08-15 · data_source 경계 확정 후 최종 확대",
    "RULE-0011": "CIO 판정 2026-08-15 · data_source 경계 확정 후 최종 확대",
    "RULE-0021": "CIO 판정 2026-08-15 · data_source 경계 확정 후 최종 확대",
    "RULE-0022": "CIO 판정 2026-08-15 · data_source 경계 확정 후 최종 확대",
}

# ══════════════════════════════════════════════════════════════════════
# P3 Data Capability Application — CIO 승인 2026-08-15
#   TSMC 월매출 3개 Rule 의 **데이터/원천 축** 을 적용한다.
#   근거: GitHub-hosted live run 2회에서 SEC EDGAR(TSMC 제출 6-K)의
#         `Revenue Report (Consolidated)` NT$ million 표에서
#           2026-06 → 442,680 / 67.9 / 2,404,484 / 35.6
#           2026-07 → 467,580 / 44.7 / 2,872,064 / 37.0
#         을 end-to-end 추출하고 June→July 월 연속성 입력 구성까지 확인.
#
#   ⛔ 이것은 evaluator 사용 승인이 **아니다**. `consumable_by_evaluator=false` ·
#      Production HOLD · evaluator 미연결은 그대로다.
#   ⛔ READY 를 목표값으로 강제하지 않는다 — readiness 는 기존 derive 함수가 계산한다.
#   ⛔ `condition_semantics` · `scope` · `data_capability` 의 UNRESOLVED 는
#      **건드리지 않는다.** 이 legacy/metadata 필드가 evaluator readiness 를 실제로
#      차단하도록 설계된 필드인지 확인이 먼저다 (CIO 판정). 임의로 의미를 부여하면
#      B1 에서 지켜온 layer separation 이 다시 무너진다.
#   ★ 명시적 allowlist 다. 같은 원천을 쓴다는 이유로 다른 Rule 로 번지지 않는다.
DATA_CAPABILITY_APPLICATION = {
    "RULE-0003": "CIO 판정 2026-08-15 · P3 — 월 YoY 연속 관측 capability 확보 "
                 "(2026-06=67.9 · 2026-07=44.7, 서로 다른 공식 filing, 월 연속성 성립). "
                 "⛔ 조건(40% 미달 2개월 연속)의 참·거짓과 무관하다.",
    "RULE-0007": "CIO 판정 2026-08-15 · P3 — 단월 YoY · 누계 YoY 를 공식 원천에서 확보",
    "RULE-0008": "CIO 판정 2026-08-15 · P3 — 단월 YoY · 누계 YoY 를 공식 원천에서 확보",
}

# 적용 후 값 — 새 vocabulary 를 만들지 않고 기존 어휘를 그대로 쓴다.
DATA_APPLIED_STATUS = "AVAILABLE"
DATA_APPLIED_SOURCE = "SOURCE_RESOLVED"

# 취득 계약 (P3_C4_ACQUISITION.md 에 기록된 것과 같은 내용)
DATA_CAPABILITY_SOURCE = {
    "primary_acquisition": "SEC EDGAR — TSMC 제출 6-K (CIK 0001046179)",
    "decision_observation": "6-K 내부 `TSMC {Month} Revenue Report (Consolidated)` 표 "
                            "(Unit: NT$ million)",
    "secondary_verification": "TSMC IR (investor.tsmc.com) — 사람이 확인하는 원발표",
    "independent_cross_check": "FSC/TWSE 개방데이터 — 자동 취득 경로에서는 제외",
    "deferred_to_operations": ["revision detection", "historical backfill",
                               "persistent incremental cursor",
                               "상시 network monitoring", "evaluator wiring"],
}

# ══════════════════════════════════════════════════════════════════════
# ★ data_source 경계 — CIO 판정 2026-08-15 (최종 확대)
#
#   Rule definition   = 무엇을 관측해 어떤 조건이면 사건이 성립하는가.
#   data capability   = 그 관측값을 어느 source · collector · parser 로 확보하는가.
#
#   ⛔ collector · API · parser 가 없다는 이유만으로 **이미 의미가 확정된** Rule 을
#      `UNDEFINED` 로 두지 않는다. 그렇게 두면 API 부재가 투자 규칙의 미정의로
#      둔갑한다.
#   ⛔ 반대 방향도 막는다 — definition 이 DEFINED 가 됐다는 이유로 `DATA_MISSING`
#      이나 `SOURCE_UNRESOLVED` 를 자동 해제하지 않는다. 어느 source 를
#      authoritative 로 쓸지 미정이면 source qualification 은 그대로 남는다.
#
#   ★ 여기서 새로 정하는 것은 **축의 귀속 하나뿐**이다. 어떤 Rule 의 어떤 성분이
#     capability 축인지는 B5-1 이 이미 기록한 `data_capability_axis` 를 그대로 쓴다.
CAPABILITY_AXIS_IS_NOT_DEFINITION = True
CAPABILITY_AXIS_NOTE = (
    "data_source 결핍이 B5-1 의 `data_capability_axis` 에 기록돼 있으면 definition "
    "component 가 아니라 execution/data capability 로 본다 (CIO 판정 2026-08-15). "
    "⛔ 그래도 DATA_MISSING · SOURCE_UNRESOLVED 는 자동 해제하지 않는다.")

# ⛔ 적용 금지 — 판정은 있으나 **참조 대상 자체가 없다**. pilot 이 건드리지 않는다.
DEFINITION_APPLICATION_EXCLUDED = {
    "RULE-0009": "「B 박스권」 의 기계적 생성 정의가 정본에 없다 — 소비 계약만 있다",
    "RULE-0016": "자본배분 · 리스크 정책의 실적 이벤트 노출 한도가 없다 — Rule SSOT 밖 층",
}


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def _apply_definition(rid, missing, decisions, errs, capability_axis=()):
    """확정된 CIO 판정을 definition 상태에 적용해도 되는가.

    적용 자격 — **definition 축의** 결핍 성분이 전부 CIO 판정으로 덮여 있을 것.
    ★ `data_capability_axis` 에 기록된 성분은 definition 축이 아니므로 이 요구에서
      제외한다 (CIO 판정 2026-08-15). 그 성분은 데이터 확보 문제로 남으며,
      `DATA_MISSING` · `SOURCE_UNRESOLVED` 는 그대로 유지된다.
    """
    if rid not in DEFINITION_APPLICATION_PILOT:
        return None
    if rid in DEFINITION_APPLICATION_EXCLUDED:
        errs.append(f"{rid}: 적용 금지 대상인데 pilot 에 들어 있다 — "
                    f"{DEFINITION_APPLICATION_EXCLUDED[rid]}")
        return None
    covered = {d["decision_unit"].split("::", 1)[1]
               for d in decisions if d["cio_decision"] is not None}
    cap = set(capability_axis) if CAPABILITY_AXIS_IS_NOT_DEFINITION else set()
    uncovered = [m for m in missing if m not in covered and m not in cap]
    if uncovered:
        errs.append(f"{rid}: 결핍 성분 {uncovered} 에 CIO 판정이 없다 — 적용하지 않는다")
        return None
    return {
        "from": "UNDEFINED",
        "to": "DEFINED",
        "source": DEFINITION_APPLICATION_PILOT[rid],
        "applied_decision_units": sorted(d["decision_unit"] for d in decisions),
        "capability_axis_excluded": sorted(cap & set(missing)),
        "capability_axis_note": CAPABILITY_AXIS_NOTE if (cap & set(missing)) else None,
        "note": ("★ 새 정의를 만든 것이 아니라 확정된 CIO 판정을 상태에 적용한 것이다. "
                 "판정 원문은 `cio_definition_decisions` 에 그대로 있다."),
    }


def _apply_data_capability(rid, m, errs):
    """P3 데이터/원천 축 적용. allowlist 밖이면 None — 조용히 번지지 않는다."""
    if rid not in DATA_CAPABILITY_APPLICATION:
        return None
    before_data = m["data_status"]
    before_src = m["source_qualification"]
    # ⛔ 이미 적용된 상태를 다시 적용하지 않는다 — 상류가 바뀌면 그 사실을 드러낸다.
    if before_data == DATA_APPLIED_STATUS and before_src == DATA_APPLIED_SOURCE:
        errs.append(f"{rid}: 상류가 이미 {DATA_APPLIED_STATUS}/{DATA_APPLIED_SOURCE} 다 — "
                    f"적용 기록이 중복된다")
        return None
    return {
        "data_status": {"from": before_data, "to": DATA_APPLIED_STATUS},
        "source_qualification": {"from": before_src, "to": DATA_APPLIED_SOURCE},
        "source": DATA_CAPABILITY_APPLICATION[rid],
        "acquisition_contract": DATA_CAPABILITY_SOURCE,
        # ★ 이 필드들은 건드리지 않았다는 사실을 명시적으로 남긴다.
        "untouched_legacy_fields": ["condition_semantics", "scope", "data_capability"],
        "not_an_evaluator_approval": True,
    }


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

        applied = _apply_definition(rid, m.get("missing_components", []),
                                    decisions, errs,
                                    m.get("data_capability_axis", []))
        def_status = "DEFINED" if applied else m["definition_status"]

        # ── P3 데이터/원천 축 적용 ────────────────────────────────
        data_applied = _apply_data_capability(rid, m, errs)
        data_status = (data_applied["data_status"]["to"] if data_applied
                       else m["data_status"])
        source_qual = (data_applied["source_qualification"]["to"] if data_applied
                       else m["source_qualification"])

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
            # 상태 — 해소하지 않고 그대로 싣는다.
            #   유일한 예외가 P0 pilot 의 definition 적용이며, 그 경우에도
            #   원래 값과 근거를 `definition_application` 에 남긴다.
            "definition_status": def_status,
            "definition_status_before_application": m["definition_status"],
            "definition_application": applied,
            "data_status": data_status,
            "data_status_before_application": m["data_status"],
            # ⛔ legacy/metadata 필드다. P3 적용 대상이 아니며 상류 값을 그대로 싣는다.
            "data_capability": m["data_capability"],
            "source_qualification": source_qual,
            "source_qualification_before_application": m["source_qualification"],
            "data_capability_application": data_applied,
            # ★ readiness 는 파생값이다 — 기존 vocabulary 계약을 그대로 호출한다.
            #   ⛔ 목표 숫자를 맞추려고 손으로 적지 않는다.
            "evaluator_status": VC.derive_evaluator_status(def_status, data_status),
            "blocked_by": VC.derive_blocked_by(def_status, data_status, source_qual),
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

    # ── 결함 C — 폐쇄 어휘 강제 (CIO 판정 2026-08-15) ────────────────
    #   ⛔ 어휘 검사는 그동안 분해 단계에만 있었고 이 산출물에는 없었다.
    #      승격 단계에서 만들어진 값이 아무 검사도 거치지 않던 공백을 여기서 닫는다.
    #   ★ 새 어휘를 만들지 않는다 — vocabulary.py 의 폐쇄 집합을 강제할 뿐이다.
    for r in rules:
        errs.extend(VC.vocab_violations(r, tag=f"{r['rule_id']}: "))

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
    # ★ P0 pilot 이 적용된 Rule 만큼만 달라져야 한다. 그 외의 이동은 전부 위반이다.
    #   ⛔ 「달라질 수 있다」로 느슨하게 열지 않는다 — **정확히 몇 건이 왜 달라졌는지**
    #      를 적용 기록에서 계산해 대조한다.
    applied_rules = [r for r in rules if r["definition_application"]]
    data_applied_rules = [r for r in rules if r["data_capability_application"]]

    # ★ readiness 이동은 **적용 전 값으로 다시 파생시켜** 비교한다.
    #   목표 숫자를 세지 않고, 움직인 Rule 하나하나가 적용 기록을 갖는지 본다.
    def _pre(r):
        return VC.derive_evaluator_status(r["definition_status_before_application"],
                                          r["data_status_before_application"])

    moved_out_blocked = [r for r in rules
                         if _pre(r) == "BLOCKED" and r["evaluator_status"] != "BLOCKED"]
    moved_in_blocked = [r for r in rules
                        if _pre(r) != "BLOCKED" and r["evaluator_status"] == "BLOCKED"]
    moved_in_ready = [r for r in rules
                      if _pre(r) != "READY" and r["evaluator_status"] == "READY"]
    moved_out_ready = [r for r in rules
                       if _pre(r) == "READY" and r["evaluator_status"] != "READY"]

    # 움직인 Rule 은 반드시 적용 기록을 갖는다 — 근거 없는 이동은 위반이다.
    for r in moved_out_blocked + moved_in_blocked + moved_in_ready + moved_out_ready:
        if not (r["definition_application"] or r["data_capability_application"]):
            errs.append(f"{r['rule_id']}: 적용 기록 없이 evaluator_status 가 움직였다")

    expected_delta = {
        "definition_undefined": -len(applied_rules),
        "evaluator_blocked": len(moved_in_blocked) - len(moved_out_blocked),
        "evaluator_ready": len(moved_in_ready) - len(moved_out_ready),
        "data_missing": -len([r for r in data_applied_rules
                              if r["data_status_before_application"] == "MISSING"]),
        "source_unresolved": -len([r for r in data_applied_rules
                                   if r["source_qualification_before_application"]
                                   == "SOURCE_UNRESOLVED"]),
    }
    for k, v in got.items():
        want = mapping["counts"][k] + expected_delta[k]
        if want != v:
            errs.append(f"{k}: 승격 전 {mapping['counts'][k]} + 적용분 "
                        f"{expected_delta[k]:+d} = {want} 이어야 하는데 {v} 다")

    # 적용 대상이 allowlist 와 정확히 같은가 — 조용히 번지지 않았는가
    if {r["rule_id"] for r in applied_rules} - set(DEFINITION_APPLICATION_PILOT):
        errs.append("적용이 pilot allowlist 밖으로 번졌다")
    for r in rules:
        if r["definition_application"] and r["rule_id"] in DEFINITION_APPLICATION_EXCLUDED:
            errs.append(f"{r['rule_id']}: 적용 금지 대상에 적용됐다")
        # 적용하지 않은 Rule 은 상위 상태와 한 글자도 달라선 안 된다
        if not r["definition_application"] \
                and r["definition_status"] != r["definition_status_before_application"]:
            errs.append(f"{r['rule_id']}: 적용 기록 없이 definition_status 가 바뀌었다")

    # ── P3 데이터/원천 축 가드 ──────────────────────────────────────
    if {r["rule_id"] for r in data_applied_rules} - set(DATA_CAPABILITY_APPLICATION):
        errs.append("데이터 축 적용이 allowlist 밖으로 번졌다")
    for r in rules:
        if not r["data_capability_application"]:
            if r["data_status"] != r["data_status_before_application"]:
                errs.append(f"{r['rule_id']}: 적용 기록 없이 data_status 가 바뀌었다")
            if r["source_qualification"] != r["source_qualification_before_application"]:
                errs.append(f"{r['rule_id']}: 적용 기록 없이 source_qualification 이 바뀌었다")
        else:
            if r["data_status"] != DATA_APPLIED_STATUS:
                errs.append(f"{r['rule_id']}: 적용됐는데 data_status 가 "
                            f"{DATA_APPLIED_STATUS} 가 아니다")
            if r["source_qualification"] != DATA_APPLIED_SOURCE:
                errs.append(f"{r['rule_id']}: 적용됐는데 source_qualification 이 "
                            f"{DATA_APPLIED_SOURCE} 가 아니다")
            # ⛔ legacy 필드는 손대지 않았다
            if r["data_capability"] != "UNRESOLVED":
                errs.append(f"{r['rule_id']}: data_capability 가 상류와 달라졌다 — "
                            f"이번 적용 대상이 아니다")

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
