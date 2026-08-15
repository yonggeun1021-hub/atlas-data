"""machine Rule Inventory 회귀.

★ 지금 정상 build 는 **의도적으로 실패**한다 — §21-12 가 요구하는 고유 `rule_id`
  가 monitoring 16건에 없기 때문이다. 이 회귀는 그 실패가 정확히 그 이유 하나이며
  숫자를 맞추려 객체 종류를 섞지 않았음을 확인한다.
  ⛔ 통과시키려고 ID 를 만들지 않는다. identity 부여는 CIO 판정 대상이다.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rules"))
import rule_inventory as RI                                          # noqa: E402
import ssot_mapping as SM                                            # noqa: E402

PASS = FAIL = 0
ID_ERR = "§21-12 위반 — Inventory 집계 대상"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


def _tmp(obj):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(obj, f, ensure_ascii=False)
    f.close()
    return f.name


def run(mut_rules=None, mut_map=None, pop=None, cons=None, full=False):
    rules = json.load(open(RI.RULES, encoding="utf-8"))
    mapping = json.load(open(RI.MAPPING, encoding="utf-8"))
    tmps = []
    rp, mpth = RI.RULES, RI.MAPPING
    if mut_rules:
        rp = _tmp(mut_rules(copy.deepcopy(rules)))
        tmps.append(rp)
    if mut_map:
        mpth = _tmp(mut_map(copy.deepcopy(mapping)))
        tmps.append(mpth)
    out = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False).name
    os.unlink(out)
    o_pop = dict(RI.IN_POPULATION_BY_CLASS)
    o_con = dict(RI.CONSUMABLE_BY_CLASS)
    try:
        if pop:
            RI.IN_POPULATION_BY_CLASS.update(pop)
        if cons:
            RI.CONSUMABLE_BY_CLASS.update(cons)
        # ★ mapping 을 임시 파일로 옮기면 rules.json 이 고정한 해시와 어긋난다.
        #   그 자체가 정상 동작이므로, 해시 오류는 검사 대상에서 제외해 읽는다.
        p, errs = RI.build(out_path=out, rules_path=rp, mapping_path=mpth)
        wrote = os.path.exists(out)
        return (p, errs, wrote) if full else errs
    finally:
        RI.IN_POPULATION_BY_CLASS.clear()
        RI.IN_POPULATION_BY_CLASS.update(o_pop)
        RI.CONSUMABLE_BY_CLASS.clear()
        RI.CONSUMABLE_BY_CLASS.update(o_con)
        for t in tmps + [out]:
            if os.path.exists(t):
                os.unlink(t)


P, ERRS, WROTE = run(full=True)
C = P["counts"]

print("K-0 §21-12 identity 충족 — 정상 발행")
check("위반 0", ERRS == [], str(ERRS[:3]))
check("발행했다", WROTE)
check("실제 산출물 파일이 있다", os.path.exists(RI.OUT))
check("ID 없는 집계 대상 0건", C["entries_without_rule_id"] == 0,
      str(C["entries_without_rule_id"]))
check("Inventory 는 ID 를 만들지 않는다 — 부여는 별도 산출물 소관",
      P["ids_created"] == 0)

print("K-1 ② Inventory 총수와 scope 구성")
check("총 41", C["rule_inventory_total"] == 41, str(C["rule_inventory_total"]))
check("evaluation rule 25", C["evaluation_rule"] == 25)
check("monitoring 16", C["monitoring"] == 16)
check("총수 = 25 + 16", C["evaluation_rule"] + C["monitoring"] == C["rule_inventory_total"])

print("K-2 ③ 제외 21 · 44 가 집계에 없다")
check("execution_reference 21 제외", C["excluded_execution_reference"] == 21)
check("non_rule_evidence 44 제외", C["excluded_non_rule_evidence"] == 44)
MAP = json.load(open(RI.MAPPING, encoding="utf-8"))
mp = {m["occurrence_id"]: m for m in MAP["mapping"]}
inv_occ = {o for e in P["entries"] if e["inventory_class"] == "evaluation_rule"
           for o in e["source_occurrences"]} | \
          {e["occurrence_id"] for e in P["entries"] if e["inventory_class"] == "monitoring"}
for d in (SM.DEST_EXCLUDED_EXEC, SM.DEST_EXCLUDED_NRE):
    bad = [k for k, m in mp.items() if m["destination"] == d and k in inv_occ]
    check(f"{d} 유입 0", bad == [], str(bad[:3]))
check("집계 대상 41 occurrence", len(inv_occ) == 41, str(len(inv_occ)))

print("K-3 ⑤ 종류를 섞지 않았다")
ev = [e for e in P["entries"] if e["inventory_class"] == "evaluation_rule"]
mon = [e for e in P["entries"] if e["inventory_class"] == "monitoring"]
check("evaluation rule 은 전부 FAL/ENT", all(e["rule_kind"] in ("FAL", "ENT") for e in ev))
check("monitoring 은 전부 MON", all(e["rule_kind"] == "MON" for e in mon))
check("monitoring 효과는 전부 monitoring",
      all(e["downstream_effect"] == "monitoring" for e in mon))
_ev_occ = {o for e in ev for o in e["source_occurrences"]}
_mon_occ = {e["occurrence_id"] for e in mon}
check("두 클래스의 occurrence 가 겹치지 않는다", not (_ev_occ & _mon_occ),
      str(sorted(_ev_occ & _mon_occ)[:3]))
check("두 클래스 합이 집계 총수와 같다",
      len(_ev_occ) + len(_mon_occ) == C["rule_inventory_total"])
check("identity 출처가 클래스별로 다르다",
      all(e["identity_source"] == "config/rules.json" for e in ev)
      and all(e["identity_source"] == "rules/monitoring_identity.json" for e in mon))

print("K-4 ④ Evaluator Population 과 상태 집계는 별도다")
check("Evaluator Population 25", C["evaluator_population"] == 25)
check("Population ≠ Inventory 총수", C["evaluator_population"] != C["rule_inventory_total"])
check("monitoring 은 Population 에 없다",
      all(not e["in_evaluator_population"] for e in mon))
check("READY 6 · BLOCKED 19 (P0 적용 2건 + P3 적용 3건 반영)",
      C["evaluator_ready"] == 6 and C["evaluator_blocked"] == 19,
      f"{C['evaluator_ready']}/{C['evaluator_blocked']}")
check("UNDEFINED 3 · MISSING 19 · SOURCE_UNRESOLVED 13",
      C["definition_undefined"] == 3 and C["data_missing"] == 19
      and C["source_unresolved"] == 13,
      f"{C['definition_undefined']}/{C['data_missing']}/{C['source_unresolved']}")
_dapp = [e for e in P["entries"] if e.get("data_capability_application")]
check("★ P3 데이터 축 적용도 Inventory 에 그대로 보인다 — 3건",
      len(_dapp) == 3 and sorted(e["rule_id"] for e in _dapp)
      == ["RULE-0003", "RULE-0007", "RULE-0008"],
      str(sorted(e["rule_id"] for e in _dapp)))
check("★ 적용 전 값(MISSING · SOURCE_UNRESOLVED)이 Inventory 에 보존된다",
      all(e["data_status_before_application"] == "MISSING"
          and e["source_qualification_before_application"] == "SOURCE_UNRESOLVED"
          for e in _dapp))
check("★ Inventory 가 데이터 축 적용을 숨기지 않는다",
      all(e["data_status"] == "AVAILABLE"
          and e["source_qualification"] == "SOURCE_RESOLVED" for e in _dapp))
_app = [e for e in P["entries"] if e.get("definition_application")]
check("★ 적용 기록이 Inventory 에도 그대로 보인다 — 13건",
      len(_app) == 13 and sorted(e["rule_id"] for e in _app)
      == ["RULE-0002", "RULE-0004", "RULE-0010", "RULE-0011", "RULE-0012",
          "RULE-0015", "RULE-0017", "RULE-0018", "RULE-0019", "RULE-0020",
          "RULE-0021", "RULE-0022", "RULE-0025"],
      str(sorted(e["rule_id"] for e in _app)))
check("적용됐지만 데이터가 없는 Rule 은 DATA_MISSING 만 남는다",
      all("DEFINITION_UNDEFINED" not in e["blocked_by"] and "DATA_MISSING" in e["blocked_by"]
          for e in _app if e["data_status"] == "MISSING"))
check("적용 전 값도 숨기지 않는다",
      all(e["definition_status_before_application"] == "UNDEFINED"
          and e["definition_status"] == "DEFINED" for e in _app))
check("적용된 Rule 도 소비 가능이 아니다",
      all(e["evaluator_consumable"] is False for e in _app))
check("상태를 숨기지 않았다", P["states_hidden"] == 0 and P["statuses_resolved"] == 0)
check("상태 필드가 모든 항목에 살아 있다",
      all(set(("definition_status", "data_status", "source_qualification",
               "evaluator_status", "blocked_by")) <= set(e) for e in P["entries"]))

print("K-5 ⑤ authority · consumable · Production 불변")
RJ = json.load(open(RI.RULES, encoding="utf-8"))
check("rules.json authority=true 그대로", RJ["authority"] is True)
check("rules.json consumable=false 그대로", RJ["consumable_by_evaluator"] is False)
check("rules.json Rule 25 그대로", RJ["rule_count"] == 25)
check("Inventory 는 authority 가 아니다", P["authority"] is False)
check("Inventory consumable=false", P["consumable_by_evaluator"] is False)
check("Production HOLD 유지", "HOLD" in P["production_state"])
check("evaluator 미연결", P["evaluator_wiring"] == "미연결")
check("소비 가능 0건", C["evaluator_consumable"] == 0)
check("포함 ≠ 실행 가능 을 명시한다", "뜻하지 않는다" in P["membership_note"])

print("K-6 ⑧ READY 함정")
trap = [e for e in ev if "298040.KS::탈락 조건#3" in e["source_occurrences"]]
check("그 Rule 이 Inventory 에 있다", len(trap) == 1)
t = trap[0]
check("READY 로 나타난다", t["evaluator_status"] == "READY")
check("그래도 소비 가능이 아니다", t["evaluator_consumable"] is False)
check("Stage · Portfolio · 주문 필드가 없다",
      not any(k in t for k in ("stage_action", "portfolio_action", "order", "trade",
                               "executable")))
check("READY 는 evaluation rule 중 6건 (원래 1 + 정의 적용 2 + P3 데이터 적용 3)",
      sum(1 for e in ev if e["evaluator_status"] == "READY") == 6,
      str(sum(1 for e in ev if e["evaluator_status"] == "READY")))
check("그 6건 전부 소비 가능이 아니다",
      all(e["evaluator_consumable"] is False
          for e in ev if e["evaluator_status"] == "READY"))

print("K-7 ⑦ 기존 항등식만 검증한다")
check("occurrence 106 = 25 + 16 + 21 + 44",
      len(MAP["mapping"]) == 25 + 16 + 21 + 44)
check("계약 문구를 그대로 싣는다",
      "Evaluator Population 제외" in P["scope_contract"]["monitoring"]
      and "집계 대상 제외" in P["scope_contract"]["execution_reference"]
      and "고유 rule_id" in P["scope_contract"]["identity"])


# ══════════════════════════════════════════════════════════════════════
# 음성 — CIO 가 지정한 7가지. 전부 "build 가 거부하는가" 를 본다.
# ══════════════════════════════════════════════════════════════════════
def only_new(errs):
    """§21-12 identity 결핍은 현재 상시 발생하므로 그 외 위반만 본다."""
    return [x for x in errs if ID_ERR not in x and "고정한 해시와 다르다" not in x]


print("K-8 음성 · monitoring 하나를 빼면 실패")


def m_drop_mon(m):
    for i, x in enumerate(m["mapping"]):
        if x["destination"] == SM.DEST_INVENTORY_ONLY:
            del m["mapping"][i]
            return m


e = only_new(run(mut_map=m_drop_mon))
check("포함 계약 위반을 잡는다", any("포함 계약 위반" in x for x in e), str(e[:3]))

print("K-9 음성 · execution_reference 를 넣으면 실패")


def m_add_exec(m):
    for x in m["mapping"]:
        if x["destination"] == SM.DEST_EXCLUDED_EXEC:
            x["destination"] = SM.DEST_INVENTORY_ONLY
            return m


e = only_new(run(mut_map=m_add_exec))
check("MON 이 아닌 객체를 잡는다",
      any("MON 이 아닌 객체" in x for x in e), str(e[:3]))
check("포함 수 계약도 함께 깨진다", any("포함 계약 위반" in x for x in e), str(e[:3]))

print("K-10 음성 · non_rule_evidence 를 넣으면 실패")


def m_add_nre(m):
    for x in m["mapping"]:
        if x["destination"] == SM.DEST_EXCLUDED_NRE:
            x["destination"] = SM.DEST_INVENTORY_ONLY
            return m


e = only_new(run(mut_map=m_add_nre))
check("MON 이 아닌 객체를 잡는다", any("MON 이 아닌 객체" in x for x in e), str(e[:3]))

print("K-11 음성 · rules.json Rule 하나가 유실되면 실패")


def m_lose(r):
    del r["rules"][0]
    return r


e = only_new(run(mut_rules=m_lose))
check("수 불일치를 잡는다", any("≠ rules.json" in x for x in e), str(e[:3]))

print("K-12 음성 · upstream 이 rules.json 을 덮어쓰려 하면 실패")


def m_override(m):
    """★ 적용 기록이 **없는** Rule 을 골라 upstream 을 위조한다.
    적용된 Rule 은 세 파생 필드가 다를 수 있도록 설계돼 있으므로, 그것을 고르면
    「기록으로 설명되는 차이」와 구별되지 않는다."""
    import rule_inventory as _RI
    rules = {r["rule_id"]: r for r in json.load(open(_RI.RULES, encoding="utf-8"))["rules"]}
    for x in m["mapping"]:
        rid = x.get("canonical_rule_id")
        if (x["destination"] == SM.DEST_SSOT and rid in rules
                and rules[rid]["definition_application"] is None
                and x["definition_status"] == "UNDEFINED"):
            x["definition_status"] = "DEFINED"
            return m
    raise AssertionError("적용 기록 없는 UNDEFINED 가 없다 — 전제가 깨졌다")


e = only_new(run(mut_map=m_override))
check("자동 보정하지 않고 거부한다",
      any("자동 보정하지 않는다" in x for x in e), str(e[:3]))
check("rules.json 이 authority 임을 오류가 말한다",
      any("rules.json 이 authority" in x for x in e), str(e[:3]))

print("K-13 음성 · Inventory 포함을 evaluator 자격으로 해석하면 실패")
e = only_new(run(pop={"monitoring": True}))
check("monitoring 을 Population 에 넣으면 거부",
      any("evaluator 자격으로 해석했다" in x for x in e), str(e[:3]))
check("개별 항목 검사도 함께 걸린다",
      any("Evaluator Population 에 있다" in x for x in e), str(e[:3]))

print("K-14 음성 · READY 를 소비 가능으로 승격하면 실패")
e = only_new(run(cons={"evaluation_rule": True}))
check("소비 자격 계약 위반을 잡는다",
      any("소비를 열지 않는다" in x for x in e), str(e[:3]))
check("항목별 소비 표시도 잡는다",
      any("소비 가능으로" in x for x in e), str(e[:3]))

print("K-15 음성 · 입력 게이트")


def m_auth(r):
    r["authority"] = False
    return r


e = only_new(run(mut_rules=m_auth))
check("authority 가 아니면 집계하지 않는다",
      any("authority 가 아니다" in x for x in e), str(e[:3]))


def m_cons(r):
    r["consumable_by_evaluator"] = True
    return r


e = only_new(run(mut_rules=m_cons))
check("consumable 이 true 면 거부", any("false 가 아니다" in x for x in e), str(e[:3]))


def m_conf(m):
    m["counts"]["conflicts"] = 1
    return m


e = only_new(run(mut_map=m_conf))
check("mapping 충돌이 있으면 집계하지 않는다",
      any("충돌·보류" in x for x in e), str(e[:3]))

print("K-16 fail-closed publish · 재현성")
_p, _e, wrote = run(mut_rules=m_auth, full=True)
check("위반이 있으면 쓰지 않는다", (not wrote) and _e != [])
P2, E2, _ = run(full=True)
check("두 번 돌려도 같은 결과",
      json.dumps(P, ensure_ascii=False, sort_keys=True)
      == json.dumps(P2, ensure_ascii=False, sort_keys=True))
check("두 번째도 같은 1건", E2 == ERRS)

print("K-17 §21-12 MON identity — namespace 분리 · 1:1 · 안정성")
import monitoring_identity as MI                                     # noqa: E402

MID = json.load(open(MI.OUT, encoding="utf-8"))
recs = MID["monitoring_identities"]
check("16건 부여", len(recs) == 16, str(len(recs)))
check("prefix 는 MON 하나뿐", MID["prefix"] == "MON")
check("형식 MON-{n:04d}", MID["id_format"] == "MON-{n:04d}")
check("새로 도입한 것이 prefix 하나임을 명시", "하나뿐" in MID["newly_introduced"])
mids = [r["monitoring_id"] for r in recs]
check("monitoring_id 유일", len(set(mids)) == 16)
check("occurrence 와 1:1",
      len({r["occurrence_id"] for r in recs}) == 16
      and {r["occurrence_id"] for r in recs} == {e["occurrence_id"] for e in mon})
check("전부 MON- 로 시작", all(m.startswith("MON-") for m in mids))
check("RULE namespace 와 겹치지 않음",
      not (set(mids) & {r["rule_id"] for r in RJ["rules"]}))
check("종목명·kind 어휘가 ID 에 없다",
      all(r["subject"] not in r["monitoring_id"] for r in recs)
      and not any(t in m for m in mids for t in ("FAL", "ENT")))
check("draft 형식(TSM-MON-01)을 채택하지 않았다",
      all("-MON-" not in m for m in mids))
check("병합하지 않았음을 명시", "병합하지 않는다" in MID["no_merge_note"])
check("부여하지 않은 것 6종을 명시", len(MID["not_granted"]) == 6)
check("B3 scope 분리를 명시", "FAL+ENT 그대로" in MID["scope_separation"])
check("Inventory 의 MON 항목이 그 ID 를 그대로 쓴다",
      {e["occurrence_id"]: e["rule_id"] for e in mon}
      == {r["occurrence_id"]: r["monitoring_id"] for r in recs})
check("namespace 표기가 항목마다 붙는다",
      all(e["identity_namespace"] == "MON" for e in mon)
      and all(e["identity_namespace"] == "RULE" for e in ev))
check("MON identity 가 rules.json 에 편입되지 않았다",
      not (set(mids) & {r["rule_id"] for r in RJ["rules"]}) and RJ["rule_count"] == 25)
check("MON identity 가 Population 에 편입되지 않았다",
      all(not e["in_evaluator_population"] for e in mon))

print("K-18 ② ID 안정성 — 재빌드·순서 역전에도 불변")
p2, e2, st_rebuild = MI.build()
check("재빌드 위반 0", e2 == [])
check("재빌드해도 같은 ID",
      {r["occurrence_id"]: r["monitoring_id"] for r in p2["monitoring_identities"]}
      == {r["occurrence_id"]: r["monitoring_id"] for r in recs})
check("재빌드는 전부 재사용 (진단값 — 산출물에는 없다)",
      st_rebuild["unchanged"] == 16 and st_rebuild["newly_assigned"] == 0)
check("★ 실행 이력이 authoritative payload 에 실리지 않는다",
      "unchanged" not in p2["counts"] and "newly_assigned" not in p2["counts"])
occ = MI.monitoring_occurrences(json.load(open(MI.MAPPING, encoding="utf-8")))
r_rev, _ = MI.assign(list(reversed(occ)), {"monitoring_identities": recs})
check("입력 순서를 뒤집어도 같은 ID",
      {r["occurrence_id"]: r["monitoring_id"] for r in r_rev}
      == {r["occurrence_id"]: r["monitoring_id"] for r in recs})
fresh, st = MI.assign(occ, None)
check("빈 상태에서 부여하면 신규 16", st["newly_assigned"] == 16)
new_only, st2 = MI.assign(occ, {"monitoring_identities": recs[:10]})
check("일부만 알려져 있으면 나머지만 새 번호",
      st2["unchanged"] == 10 and st2["newly_assigned"] == 6)
check("기존 10건의 ID 는 그대로",
      {r["occurrence_id"]: r["monitoring_id"] for r in new_only
       if r["occurrence_id"] in {x["occurrence_id"] for x in recs[:10]}}
      == {r["occurrence_id"]: r["monitoring_id"] for r in recs[:10]})
check("번호 부여는 canonicalize 계약을 그대로 호출한다",
      MI.K._next_id(set(), "MON") == "MON-0001"
      and MI.K._next_id(set()) == "RULE-0001")

print("K-19 음성 · MON identity 계약 위반을 거부")
_o = MI.MONITORING_PREFIX
try:
    MI.MONITORING_PREFIX = "RULE"
    _p, _e = MI.assign(occ, None), None
    recs_bad, _ = MI.assign(occ, None)
    clash = {r["monitoring_id"] for r in recs_bad} & {r["rule_id"] for r in RJ["rules"]}
    check("RULE prefix 를 쓰면 실제로 충돌한다", bool(clash), "충돌이 없다")
finally:
    MI.MONITORING_PREFIX = _o


def m_strip(m):
    for x in m["mapping"]:
        if x["destination"] == SM.DEST_INVENTORY_ONLY:
            x["occurrence_id"] = "GHOST::칸#1"
            return m


e = only_new(run(mut_map=m_strip))
check("ID 없는 monitoring 이 생기면 Inventory 가 거부",
      any(ID_ERR in x for x in run(mut_map=m_strip)), str(e[:3]))

print("K-9 결함 C — Inventory 도 폐쇄 어휘를 강제한다")
import vocabulary as _VC
check("Inventory entry 전부 폐쇄 어휘 안에 있다",
      not [v for e in P["entries"] for v in _VC.vocab_violations(e)],
      str([v for e in P["entries"] for v in _VC.vocab_violations(e)][:3]))
_f2 = dict(P["entries"][0]); _f2["evaluator_status"] = "READYY"
check("★ Inventory 위조값도 거부된다", bool(_VC.vocab_violations(_f2)))
check("★ 검사가 rule_inventory.py 에 배선돼 있다",
      "vocab_violations" in open(os.path.join(ROOT, "rules", "rule_inventory.py"),
                                 encoding="utf-8").read())

check("★ Inventory 가 reopen 기록도 그대로 싣는다",
      [e["rule_id"] for e in P["entries"] if e.get("definition_reopen")] == ["RULE-0001"],
      str([e["rule_id"] for e in P["entries"] if e.get("definition_reopen")]))

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
