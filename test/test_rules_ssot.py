"""config/rules.json Rule SSOT 승격 회귀.

CIO 가 지정한 9개 승격 직후 회귀 + `298040.KS::탈락 조건#3` READY 함정 검사.
음성 테스트는 값을 바꿨다는 사실이 아니라 **build 가 거부하는가**를 본다.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rules"))
import promote_rules_ssot as PR                                      # noqa: E402
import ssot_mapping as SM                                            # noqa: E402

PASS = FAIL = 0


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


def run(mut_mapping=None, mut_canon=None, mut_cards=None, full=False):
    """전역 경로만 임시로 바꿔 build 를 돌린다. 원본 파일은 건드리지 않는다."""
    mapping = json.load(open(PR.MAPPING, encoding="utf-8"))
    canon = json.load(open(PR.CANON, encoding="utf-8"))
    cards = json.load(open(PR.CARDS, encoding="utf-8"))
    if mut_mapping:
        mapping = mut_mapping(copy.deepcopy(mapping))
    if mut_canon:
        canon = mut_canon(copy.deepcopy(canon))
    if mut_cards:
        cards = mut_cards(copy.deepcopy(cards))
    o = (PR.CANON, PR.CARDS)
    # ★ 변형하지 않은 입력은 원본 경로를 그대로 쓴다 — 임시 파일로 다시 직렬화하면
    #   내용이 같아도 바이트가 달라져 상위 해시 고정 검사가 헛돈다.
    tmps = [_tmp(mapping),
            _tmp(canon) if mut_canon else None,
            _tmp(cards) if mut_cards else None]
    out = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False).name
    os.unlink(out)
    try:
        PR.CANON = tmps[1] or o[0]
        PR.CARDS = tmps[2] or o[1]
        p, errs = PR.build(out_path=out, mapping_path=tmps[0])
        wrote = os.path.exists(out)
        return (p, errs, wrote) if full else errs
    finally:
        PR.CANON, PR.CARDS = o
        for t in [t for t in tmps if t] + [out]:
            if os.path.exists(t):
                os.unlink(t)


LIVE = json.load(open(PR.OUT, encoding="utf-8"))
MAP = json.load(open(PR.MAPPING, encoding="utf-8"))
CANON = json.load(open(PR.CANON, encoding="utf-8"))["canonical_rules"]
CARDS = json.load(open(PR.CARDS, encoding="utf-8"))
R = LIVE["rules"]

print("K-0 ① Rule 수 = 25")
check("rule_count 25", LIVE["rule_count"] == 25, str(LIVE["rule_count"]))
check("rules 배열도 25", len(R) == 25, str(len(R)))
check("mapping 후보 수와 같다", len(R) == MAP["counts"]["rule_ssot_candidates"])

print("K-1 ② canonical ↔ rules.json 양방향 identity")
cids = {c["canonical_rule_id"] for c in CANON}
rids = {r["rule_id"] for r in R}
check("양방향 일치", cids == rids, str(sorted(cids ^ rids)))
check("rule_id 중복 없음", len(rids) == len(R))
check("ID 를 재발급하지 않았다", all(r["rule_id"].startswith("RULE-") for r in R))

print("K-2 ③ condition_text · provenance 불변")
cbyid = {c["canonical_rule_id"]: c for c in CANON}
check("condition_text 불변",
      all(r["condition_text"] == cbyid[r["rule_id"]]["condition_text"] for r in R))
check("sha256 이 본문과 맞다",
      all(hashlib.sha256(r["condition_text"].encode()).hexdigest()
          == r["condition_text_sha256"] for r in R))
check("sha256 이 canonical 과 같다",
      all(r["condition_text_sha256"] == cbyid[r["rule_id"]]["condition_text_sha256"]
          for r in R))
check("source_occurrences 불변",
      all(r["source_occurrences"] == cbyid[r["rule_id"]]["source_occurrences"] for r in R))
check("condition_semantics · scope 도 그대로 UNRESOLVED 가 보존된다",
      all(r["condition_semantics"] == cbyid[r["rule_id"]]["condition_semantics"]
          and r["scope"] == cbyid[r["rule_id"]]["scope"] for r in R))
for k in ("ssot_mapping_sha256", "canonical_rules_sha256", "decision_cards_sha256"):
    check(f"provenance {k} 고정", len(LIVE["provenance"][k]) == 64)

print("K-3 ④ UNDEFINED · MISSING · SOURCE_UNRESOLVED 전후 불변")
s = LIVE["state_counts"]
check("UNDEFINED 15", s["definition_undefined"] == 15, str(s["definition_undefined"]))
check("MISSING 22", s["data_missing"] == 22, str(s["data_missing"]))
check("SOURCE_UNRESOLVED 16", s["source_unresolved"] == 16)
check("mapping 전후 동일",
      all(MAP["counts"][k] == v for k, v in s.items()),
      str({k: (MAP["counts"][k], v) for k, v in s.items() if MAP["counts"][k] != v}))
check("해소한 상태 0", LIVE["statuses_resolved"] == 0)
check("만든 정의 0", LIVE["definitions_created"] == 0)

print("K-4 ⑤ UNDEFINED → executable 경로 0")
und = [r for r in R if r["definition_status"] == "UNDEFINED"]
check("UNDEFINED 15건 존재", len(und) == 15)
check("READY 인 UNDEFINED 0건", all(r["evaluator_status"] != "READY" for r in und))
check("전부 DEFINITION_UNDEFINED 로 차단",
      all("DEFINITION_UNDEFINED" in r["blocked_by"] for r in und))
check("MISSING 인데 READY 인 Rule 0건",
      all(not (r["data_status"] == "MISSING" and r["evaluator_status"] == "READY")
          for r in R))
check("executable 을 뜻하는 필드가 없다",
      not any(k in r for r in R for k in ("executable", "is_executable", "enabled",
                                          "active", "consumable")))

print("K-5 ⑥ evaluator READY / BLOCKED 불변")
check("BLOCKED 24", s["evaluator_blocked"] == 24, str(s["evaluator_blocked"]))
check("READY 1", s["evaluator_ready"] == 1, str(s["evaluator_ready"]))
check("BLOCKED + READY = 25", s["evaluator_blocked"] + s["evaluator_ready"] == 25)
mp = {m["occurrence_id"]: m for m in MAP["mapping"]}
check("Rule 별 evaluator_status 가 mapping 과 하나도 다르지 않다",
      all(r["evaluator_status"] == mp[o]["evaluator_status"]
          for r in R for o in r["source_occurrences"]))
check("blocked_by 도 그대로",
      all(r["blocked_by"] == mp[o]["blocked_by"] for r in R for o in r["source_occurrences"]))

print("K-6 ⑦ 생성만으로 소비 가능해지지 않는다")
check("authority=true", LIVE["authority"] is True)
check("consumable_by_evaluator=false", LIVE["consumable_by_evaluator"] is False)
check("production HOLD 유지", "HOLD" in LIVE["production_state"])
check("evaluator 미연결", LIVE["evaluator_wiring"] == "미연결")
check("승인 범위에 evaluator 사용 승인이 아님이 적혀 있다",
      "승인 범위가 아니다" in LIVE["promotion"]["scope"])
check("authority 와 executability 를 분리해 명시한다",
      "다른 축" in LIVE["executability_note"])
check("upstream 이 authority 를 덮어쓸 수 없다고 명시한다",
      "역으로 덮어쓸 수 없다" in LIVE["authority_note"])

print("K-7 함정 · READY 1건 (298040.KS::탈락 조건#3)")
trap = [r for r in R if "298040.KS::탈락 조건#3" in r["source_occurrences"]]
check("그 occurrence 가 승격돼 있다", len(trap) == 1, str(len(trap)))
t = trap[0]
check("그 Rule 이 READY 다", t["evaluator_status"] == "READY")
check("READY 인 Rule 은 이것 하나뿐",
      [r["rule_id"] for r in R if r["evaluator_status"] == "READY"] == [t["rule_id"]])
check("blocked_by 가 비어 있다", t["blocked_by"] == [])
check("★ 그럼에도 소비 경로가 열리지 않는다",
      LIVE["consumable_by_evaluator"] is False and "HOLD" in LIVE["production_state"])
check("READY 가 소비 승인이 아님을 파일이 명시한다",
      "소비 승인이 아니다" in LIVE["ready_note"] and "Stage 변경" in LIVE["ready_note"])
check("downstream_effect 를 승격 과정에서 바꾸지 않았다",
      t["downstream_effect"] == mp["298040.KS::탈락 조건#3"]["downstream_effect"])
check("Stage · Portfolio 동작을 뜻하는 새 필드가 없다",
      not any(k in t for k in ("stage_action", "promote_stage", "portfolio_action",
                               "order", "trade")))

print("K-8 제외 객체 유입 0")
promoted = {o for r in R for o in r["source_occurrences"]}
for d in (SM.DEST_INVENTORY_ONLY, SM.DEST_EXCLUDED_EXEC, SM.DEST_EXCLUDED_NRE):
    bad = [m["occurrence_id"] for m in MAP["mapping"]
           if m["destination"] == d and m["occurrence_id"] in promoted]
    check(f"{d} 유입 0", bad == [], str(bad[:3]))
check("승격 occurrence 는 전부 rule_ssot_candidate 였다",
      all(mp[o]["destination"] == SM.DEST_SSOT for o in promoted))
check("승격 occurrence 25건", len(promoted) == 25)

print("K-9 B5-2B 판정 원문 보존")
cbu = {c["decision_unit"]: c for c in CARDS["cards"]}
dec = [(r["rule_id"], d) for r in R for d in r["cio_definition_decisions"]]
check("판정이 실린 Rule 15건",
      len({rid for rid, _ in dec}) == 15, str(len({rid for rid, _ in dec})))
check("서로 다른 판정 단위 36건",
      len({d["decision_unit"] for _, d in dec}) == 36,
      str(len({d["decision_unit"] for _, d in dec})))
check("부착 43건 — 공유 그룹 카드가 여러 Rule 에 붙는다", len(dec) == 43, str(len(dec)))
check("부착이 43 인 이유가 공유 그룹이다",
      len(dec) - len({d["decision_unit"] for _, d in dec})
      == CARDS["counts"]["shared_group_cards"])
check("판정 원문이 B5-2B 와 글자 단위로 같다",
      all(d["cio_decision"] == cbu[d["decision_unit"]]["cio_decision"] for _, d in dec))
check("상위 산출물 해시가 mapping 이 고정한 값과 같다",
      LIVE["provenance"]["decision_cards_sha256"]
      == MAP["decided_against"]["decision_cards_sha256"]
      and LIVE["provenance"]["canonical_rules_sha256"]
      == MAP["decided_against"]["canonical_rules_sha256"])
check("미판정이 실리지 않았다", all(d["cio_decision"] is not None for _, d in dec))
check("전부 CIO 가 판정한 것",
      all(d["cio_decision"]["decided_by"] == "CIO" for _, d in dec))
check("evaluator 연결 금지 표기가 전부 살아 있다",
      all("금지" in d["execution_status"] for _, d in dec))

print("K-10 ⑨ 재빌드 byte-identical")
before = hashlib.sha256(open(PR.OUT, "rb").read()).hexdigest()
p2, e2 = PR.build()
after = hashlib.sha256(open(PR.OUT, "rb").read()).hexdigest()
check("두 번 돌려도 같은 파일", before == after, f"{before[:12]} vs {after[:12]}")
check("재빌드 위반 0", e2 == [])

print("K-11 mapping 이 승격 파일을 덮어쓰지 않는다")
_, me = SM.build(out_path=SM.OUT), None
check("mapping build 는 여전히 위반 0", SM.build(out_path=SM.OUT)[1] == [])
check("mapping 은 config/rules.json 을 쓰지 않는다",
      os.path.abspath(SM.OUT) != os.path.abspath(PR.OUT))


# ══════════════════════════════════════════════════════════════════════
# 음성 — 실제로 거부하는가
# ══════════════════════════════════════════════════════════════════════
print("K-12 음성 · 상태를 해소해 승격하면 거부")


def m_resolve(m):
    for x in m["mapping"]:
        if x["destination"] == SM.DEST_SSOT and x["definition_status"] == "UNDEFINED":
            x["definition_status"] = "DEFINED"
            return m
    raise AssertionError("UNDEFINED 가 없다 — 전제가 깨졌다")


e = run(m_resolve)
check("UNDEFINED 개수 변화를 잡는다",
      any("definition_undefined" in x for x in e), str(e[:3]))


def m_ready(m):
    for x in m["mapping"]:
        if x["destination"] == SM.DEST_SSOT and x["definition_status"] == "UNDEFINED":
            x["evaluator_status"] = "READY"
            return m


e = run(m_ready)
check("UNDEFINED 인데 READY 면 거부", any("I-3 위반" in x for x in e), str(e[:3]))

print("K-13 음성 · 제외 객체를 끌어올리면 거부")


def m_pull(m):
    for x in m["mapping"]:
        if x["destination"] == SM.DEST_INVENTORY_ONLY:
            x["destination"] = SM.DEST_SSOT
            x["canonical_rule_id"] = None
            return m


e = run(m_pull)
check("canonical 없는 승격을 거부", any("승격 불가" in x for x in e), str(e[:3]))


def m_leak(c):
    # canonical 이 MON occurrence 를 가리키게 만든다 = 제외 객체 유입 경로
    c["canonical_rules"][0]["source_occurrences"] = ["MU::다음 이벤트#1"]
    return c


e = run(mut_canon=m_leak)
check("제외 객체가 Rule SSOT 로 새면 거부",
      any("새어 들어왔다" in x for x in e), str(e[:3]))

print("K-14 음성 · 원문 표류를 거부")


def m_text(c):
    c["canonical_rules"][0]["condition_text"] = "바뀐 문구"
    return c


e = run(mut_canon=m_text)
check("condition_text 표류를 잡는다",
      any("condition_text" in x for x in e), str(e[:3]))

print("K-15 음성 · 판정 원문 변형·미판정 승격을 거부")


def m_card(cd):
    for c in cd["cards"]:
        if c["cio_decision"]:
            c["cio_decision"] = dict(c["cio_decision"], decision="요약본")
            return cd


e = run(mut_cards=m_card)
check("판정 원문 변형을 해시 고정으로 잡는다",
      any("mapping 이 고정한 해시와 다르다" in x for x in e), str(e[:3]))


def m_open(cd):
    for c in cd["cards"]:
        if c["cio_decision"]:
            c["cio_decision"] = None
            return cd


e = run(mut_cards=m_open)
check("미판정 승격을 잡는다", any("미판정인데" in x for x in e), str(e[:3]))

print("K-16 음성 · mapping 충돌·보류가 있으면 승격 거부")


def m_conf(m):
    m["counts"]["conflicts"] = 1
    return m


e = run(m_conf)
check("충돌이 있으면 승격하지 않는다", any("충돌" in x for x in e), str(e[:3]))


def m_hold(m):
    m["counts"]["held"] = 2
    return m


e = run(m_hold)
check("보류가 있으면 승격하지 않는다", any("보류" in x for x in e), str(e[:3]))

print("K-17 fail-closed publish")
_p, _e, wrote = run(m_conf, full=True)
check("위반이 있으면 authority 파일을 쓰지 않는다", (not wrote) and _e != [])
_p, _e, wrote = run(full=True)
check("정상이면 쓴다", wrote and _e == [])

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
