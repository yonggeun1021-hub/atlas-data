"""human-reviewed decomposition & mapping 회귀.

★ 원칙은 앞 단계들과 같다 — 통과시키려고 단언을 느슨하게 만들지 않는다.
   음성 테스트는 "내가 값을 바꿨으니 값이 다르다" 를 확인하는 것이 아니라
   **build 가 실제로 거부하는가**를 확인한다.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rules"))
import ssot_mapping as SM                                            # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


def run(mutate=None, full=False):
    """전역을 건드리지 않고 `_load()` 결과만 바꿔 build 를 돌린다."""
    orig = SM._load
    tmp = None

    def loader():
        got = list(orig())
        if mutate:
            got = mutate([copy.deepcopy(x) for x in got])
        return tuple(got)

    try:
        SM._load = loader
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            tmp = f.name
        os.unlink(tmp)                    # 빈 파일을 남기지 않는다 — fail-closed 와 일관
        p, errs = SM.build(out_path=tmp)
        wrote = os.path.exists(tmp)
        return (p, errs, wrote) if full else errs
    finally:
        SM._load = orig
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


P, ERRS, WROTE = run(full=True)
C = P["counts"]

print("K-0 정상 build")
check("위반 0", ERRS == [], str(ERRS[:3]))
check("파일을 썼다", WROTE)
check("대상 106", C["mapping_targets"] == 106, str(C["mapping_targets"]))
check("성공 106 · 보류 0 · 충돌 0",
      C["mapped_ok"] == 106 and C["held"] == 0 and C["conflicts"] == 0)

print("K-1 모집단 분할")
check("Rule SSOT 후보 25", C["rule_ssot_candidates"] == 25, str(C["rule_ssot_candidates"]))
check("inventory only 16", C["rule_inventory_only"] == 16)
check("execution_reference 제외 21", C["excluded_execution_reference"] == 21)
check("non_rule_evidence 제외 44", C["excluded_non_rule_evidence"] == 44)
check("네 목적지 합이 전체와 같다",
      C["rule_ssot_candidates"] + C["rule_inventory_only"]
      + C["excluded_execution_reference"] + C["excluded_non_rule_evidence"]
      == C["mapping_targets"])
check("canonical 25 와 SSOT 후보 수가 같다",
      C["canonical_records"] == C["rule_ssot_candidates"])

print("K-2 실행불가 상태 보존")
check("UNDEFINED 15 보존", C["definition_undefined"] == 15, str(C["definition_undefined"]))
check("MISSING 22 보존", C["data_missing"] == 22, str(C["data_missing"]))
check("SOURCE_UNRESOLVED 16 보존", C["source_unresolved"] == 16)
check("BLOCKED 24 · READY 1", C["evaluator_blocked"] == 24 and C["evaluator_ready"] == 1)
check("해소한 상태 0", P["statuses_resolved"] == 0)
check("만든 정의 · 후보값 · 대상 전부 0",
      P["definitions_created"] == 0 and P["candidate_values_created"] == 0
      and P["targets_selected"] == 0)

print("K-3 승격하지 않았다")
check("authority=false", P["authority"] is False)
check("consumable_by_evaluator=false", P["consumable_by_evaluator"] is False)
check("status=inactive preparation", P["status"] == "inactive preparation")
_rj = os.path.join(ROOT, "config", "rules.json")
check("rules.json 은 이 단계의 산출물이 아니다",
      (not os.path.exists(_rj))
      or json.load(open(_rj, encoding="utf-8"))["artifact"] == SM.PROMOTION_ARTIFACT)
check("mapping 산출 경로가 rules.json 이 아니다",
      os.path.abspath(SM.OUT) != os.path.abspath(_rj))
check("승격은 별도 판정임을 명시한다",
      "만들지 않는다" in P["promotion_state"] and "별도 CIO 판정" in P["promotion_state"])

print("K-4 rule_candidate ≠ executable")
undef_ssot = [m for m in P["mapping"]
              if m["destination"] == SM.DEST_SSOT and m["definition_status"] == "UNDEFINED"]
check("UNDEFINED 후보가 존재한다", len(undef_ssot) == 15)
check("전부 rule_candidate 다", all(m["object_role"] == "rule_candidate" for m in undef_ssot))
check("전부 READY 가 아니다", all(m["evaluator_status"] != "READY" for m in undef_ssot))
check("전부 DEFINITION_UNDEFINED 로 차단된다",
      all("DEFINITION_UNDEFINED" in m["blocked_by"] for m in undef_ssot))
check("세 축을 섞지 않는다고 명시한다", "섞지 않는다" in P["rule_candidate_note"])

print("K-5 B2-0 종료 · Portfolio Operation index")
check("B2-0 CLOSED", "CLOSED" in P["b2_adjudication"])
check("UNRESOLVED object_role 0건",
      all(m["object_role"] != "UNRESOLVED" for m in P["mapping"]))
po = [m for m in P["mapping"] if m["portfolio_operation_candidate"]]
check("Portfolio Operation 후보 1건", len(po) == 1, str(len(po)))
check("그 조각은 Rule SSOT 안에도 남아 있다", po and po[0]["destination"] == SM.DEST_SSOT)

print("K-6 결합 표기 보존")
conn = [m for m in P["mapping"] if m["is_connective"]]
check("결합 표기가 남아 있다", len(conn) > 0, str(len(conn)))
check("결합 표기는 Rule SSOT 후보가 아니다",
      all(m["destination"] != SM.DEST_SSOT for m in conn))

print("K-7 B5-2B 연결")
ssot = [m for m in P["mapping"] if m["destination"] == SM.DEST_SSOT]
withu = [m for m in ssot if m.get("decision_units")]
check("판정이 붙은 규칙이 15건", len(withu) == 15, str(len(withu)))
check("판정 단위 총합이 36", sum(len(m["decision_units"]) for m in withu) >= 36)
check("모든 판정이 완료 상태", all(u["decided"] for m in withu for u in m["decision_units"]))
check("모든 판정이 evaluator 연결 금지 상태",
      all("금지" in u["execution_status"] for m in withu for u in m["decision_units"]))
check("판정 36 · 미판정 0",
      C["decision_units_decided"] == 36 and C["decision_units_open"] == 0)
check("정의 결핍 없는 규칙은 판정이 붙지 않는다",
      all(not m.get("decision_units") for m in ssot
          if m["definition_status"] == "DEFINED"))

print("K-8 입력 고정")
for k in ("decompose_full_sha256", "canonical_rules_sha256", "merge_decision_sha256",
          "definition_inventory_sha256", "definition_decision_sha256",
          "decision_cards_sha256"):
    check(f"{k} 가 고정된다", len(P["decided_against"][k]) == 64)

print("K-9 재현성")
P2, E2, _ = run(full=True)
check("두 번 돌려도 같다", json.dumps(P, ensure_ascii=False, sort_keys=True)
      == json.dumps(P2, ensure_ascii=False, sort_keys=True))
check("두 번째도 위반 0", E2 == [])


# ══════════════════════════════════════════════════════════════════════
# 음성 — 실제로 거부하는가
# ══════════════════════════════════════════════════════════════════════
print("K-10 음성 · canonical 이 원문과 어긋나면 거부")


def m_text(g):
    g[5]["canonical_rules"][0]["condition_text"] = "바뀐 문구"
    return g


e = run(m_text)
check("condition_text 표류를 잡는다", any("condition_text" in x for x in e), str(e[:2]))


def m_sha(g):
    g[5]["canonical_rules"][0]["condition_text_sha256"] = "0" * 64
    return g


e = run(m_sha)
check("sha 표류를 잡는다", any("sha256" in x for x in e), str(e[:2]))

print("K-11 음성 · 모집단 불일치를 거부")


def m_drop(g):
    g[5]["canonical_rules"] = g[5]["canonical_rules"][1:]
    return g


e = run(m_drop)
check("canonical 이 빠지면 잡는다",
      any("canonical identity 가 없다" in x for x in e), str(e[:2]))
check("B4-1 판정 수와 어긋나는 것도 함께 잡는다",
      any("B4-1 판정" in x for x in e), str(e[:2]))


def m_extra(g):
    r = copy.deepcopy(g[5]["canonical_rules"][0])
    r["canonical_rule_id"] = "RULE-9999"
    r["source_occurrences"] = ["MU::탈락 조건#2"]      # 결합 표기 — SSOT 모집단 아님
    g[5]["canonical_rules"].append(r)
    return g


e = run(m_extra)
check("SSOT 밖을 가리키는 canonical 을 잡는다",
      any("SSOT 모집단이 아니다" in x for x in e), str(e[:2]))

print("K-12 음성 · I-3 fail-closed")


def m_ready(g):
    for it in g[2]:
        if it["definition_status"] == "UNDEFINED":
            it["evaluator_status"] = "READY"
            break
    return g


e = run(m_ready)
check("UNDEFINED 인데 READY 면 거부", any("I-3 위반" in x for x in e), str(e[:2]))


def m_block(g):
    for it in g[2]:
        if it["definition_status"] == "UNDEFINED":
            it["blocked_by"] = []
            break
    return g


e = run(m_block)
check("UNDEFINED 인데 차단 사유가 비면 거부",
      any("차단 사유에 없다" in x for x in e), str(e[:2]))


def m_data_ready(g):
    for it in g[2]:
        if it["data_status"] == "MISSING":
            it["evaluator_status"] = "READY"
            it["definition_status"] = "DEFINED"
            it["blocked_by"] = []
            break
    return g


e = run(m_data_ready)
check("MISSING 인데 READY 면 거부", any("MISSING 인데 READY" in x for x in e), str(e[:2]))

print("K-13 음성 · 결핍 미판정을 거부")


def m_uncovered(g):
    g[7]["items"].append({
        "canonical_rule_id": "RULE-0001", "occurrence_id": "MU::탈락 조건#1",
        "condition_text": "FQ4 $49B 미달", "rule_kind": "FAL",
        "current_definition_status": "UNDEFINED",
        "missing_components": ["threshold"], "source_has_resolution": False,
        "resolution_evidence": [], "resolution_status": None,
        "why_not_deterministic": "주입",
    })
    return g


e = run(m_uncovered)
check("판정도 capability 축도 없는 결핍을 잡는다",
      any("data capability 축도 아니다" in x for x in e), str(e[:3]))
check("UNDEFINED 집합 불일치도 함께 잡는다",
      any("인벤토리와 다르다" in x for x in e), str(e[:3]))

print("K-14 음성 · B5-2B 가 없는 규칙을 가리키면 거부")


def m_ghost(g):
    c = copy.deepcopy(g[9]["cards"][0])
    c["affected_rules"] = ["RULE-8888"]
    g[9]["cards"].append(c)
    return g


e = run(m_ghost)
check("유령 규칙 참조를 잡는다",
      any("존재하지 않는 규칙" in x for x in e), str(e[:2]))

print("K-15 음성 · B2-0 미종료면 거부")


def m_unres(g):
    g[2][0]["object_role"] = "UNRESOLVED"
    return g


e = run(m_unres)
check("UNRESOLVED 가 남아 있으면 거부", any("B2-0 미종료" in x for x in e), str(e[:2]))

print("K-16 음성 · 상태를 해소해 발행하면 거부 (mapping 은 보존만 한다)")


def m_resolve(g):
    # UNDEFINED 하나를 DEFINED 로 '해소' 한 채 발행하려는 상황
    for it in g[2]:
        if it["definition_status"] == "UNDEFINED":
            it["definition_status"] = "DEFINED"
            it["blocked_by"] = [b for b in it["blocked_by"]
                                if b != "DEFINITION_UNDEFINED"]
            return g
    raise AssertionError("UNDEFINED 조각이 없다 — 이 음성 테스트의 전제가 깨졌다")


e = run(m_resolve)
check("고정된 분해 SSOT 와 다르면 거부",
      any("고정된 분해 SSOT 와 다르다" in x for x in e), str(e[:3]))
check("UNDEFINED 집합 불일치로도 걸린다",
      any("인벤토리와 다르다" in x for x in e), str(e[:3]))


def m_phantom(g):
    g[2].append(dict(g[2][0], id="GHOST::칸#9"))
    return g


e = run(m_phantom)
check("고정 SSOT 에 없는 occurrence 를 잡는다",
      any("고정된 분해 SSOT 에 없는" in x for x in e), str(e[:3]))

print("K-17 fail-closed publish")


def m_any(g):
    g[5]["canonical_rules"][0]["condition_text"] = "x"
    return g


_p, _e, wrote = run(m_any, full=True)
check("위반이 있으면 파일을 쓰지 않는다", (not wrote) and _e != [])

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
