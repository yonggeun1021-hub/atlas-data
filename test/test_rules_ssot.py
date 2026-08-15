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
import vocabulary as VC                                              # noqa: E402
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

print("K-3 ④ 상태 — P0 적용분 외에는 전부 불변")
s = LIVE["state_counts"]
APPLIED = [r for r in R if r["definition_application"]]
# ★ P3 Data Capability Application (CIO 승인 2026-08-15) — 데이터/원천 축 적용분.
DATA_APPLIED = [r for r in R if r.get("data_capability_application")]
check("UNDEFINED 2 (15 − 적용 13)", s["definition_undefined"] == 2,
      str(s["definition_undefined"]))
check("MISSING 19 (22 − P3 적용 3)", s["data_missing"] == 22 - len(DATA_APPLIED),
      str(s["data_missing"]))
check("SOURCE_UNRESOLVED 13 (16 − P3 적용 3)",
      s["source_unresolved"] == 16 - len(DATA_APPLIED), str(s["source_unresolved"]))
check("P3 적용은 정확히 3건 (RULE-0003/0007/0008)",
      sorted(r["rule_id"] for r in DATA_APPLIED)
      == ["RULE-0003", "RULE-0007", "RULE-0008"],
      str(sorted(r["rule_id"] for r in DATA_APPLIED)))
check("★ mapping 대비 차이가 적용 기록으로 정확히 설명된다",
      s["definition_undefined"] == MAP["counts"]["definition_undefined"] - len(APPLIED)
      and s["data_missing"] == MAP["counts"]["data_missing"] - len(DATA_APPLIED)
      and s["source_unresolved"]
      == MAP["counts"]["source_unresolved"] - len(DATA_APPLIED),
      str({k: (MAP["counts"][k], v) for k, v in s.items()}))
check("★ P3 적용 Rule 은 적용 전 값이 MISSING · SOURCE_UNRESOLVED 였다",
      all(r["data_status_before_application"] == "MISSING"
          and r["source_qualification_before_application"] == "SOURCE_UNRESOLVED"
          for r in DATA_APPLIED))
check("★ legacy 필드 data_capability 는 건드리지 않았다",
      all(r["data_capability"] == "UNRESOLVED" for r in DATA_APPLIED))
check("★ condition_semantics · scope 도 그대로 UNRESOLVED",
      all(r["condition_semantics"] == "UNRESOLVED" and r["scope"] == "UNRESOLVED"
          for r in DATA_APPLIED))
check("해소한 상태 0", LIVE["statuses_resolved"] == 0)
check("만든 정의 0", LIVE["definitions_created"] == 0)

print("K-4 ⑤ UNDEFINED → executable 경로 0")
und = [r for r in R if r["definition_status"] == "UNDEFINED"]
check("UNDEFINED 2건 존재", len(und) == 2, str(len(und)))
check("READY 인 UNDEFINED 0건", all(r["evaluator_status"] != "READY" for r in und))
check("전부 DEFINITION_UNDEFINED 로 차단",
      all("DEFINITION_UNDEFINED" in r["blocked_by"] for r in und))
check("MISSING 인데 READY 인 Rule 0건",
      all(not (r["data_status"] == "MISSING" and r["evaluator_status"] == "READY")
          for r in R))
check("executable 을 뜻하는 필드가 없다",
      not any(k in r for r in R for k in ("executable", "is_executable", "enabled",
                                          "active", "consumable")))

print("K-5 ⑥ evaluator readiness — 파생값이며 적용분만 이동한다")
check("BLOCKED 19 (22 − P3 적용 3)", s["evaluator_blocked"] == 19,
      str(s["evaluator_blocked"]))
check("READY 6 (3 + P3 적용 3)", s["evaluator_ready"] == 6, str(s["evaluator_ready"]))
check("BLOCKED + READY = 25", s["evaluator_blocked"] + s["evaluator_ready"] == 25)
mp = {m["occurrence_id"]: m for m in MAP["mapping"]}
_moved = [r["rule_id"] for r in R
          for o in r["source_occurrences"] if r["evaluator_status"] != mp[o]["evaluator_status"]]
# ★ 적용했다고 readiness 가 움직이는 것이 아니다 — 데이터가 있어야 움직인다.
#   따라서 「움직인 집합 == 적용 ∩ 데이터 확보」 여야 한다.
_should_move = sorted({r["rule_id"] for r in APPLIED if r["data_status"] == "AVAILABLE"}
                     | {r["rule_id"] for r in DATA_APPLIED})
check("★ readiness 가 움직인 Rule = 적용 ∩ data AVAILABLE",
      sorted(set(_moved)) == _should_move, f"moved={sorted(set(_moved))} want={_should_move}")
check("★ 데이터가 없는 적용 Rule 은 readiness 가 그대로 BLOCKED",
      all(r["evaluator_status"] == "BLOCKED" for r in APPLIED
          if r["data_status"] != "AVAILABLE"))
check("어느 적용도 받지 않은 Rule 은 blocked_by 가 그대로",
      all(r["blocked_by"] == mp[o]["blocked_by"] for r in R
          if not r["definition_application"] and not r.get("data_capability_application")
          for o in r["source_occurrences"]))
check("readiness 는 vocabulary 파생 계약과 일치한다",
      all(r["evaluator_status"]
          == VC.derive_evaluator_status(r["definition_status"], r["data_status"])
          and r["blocked_by"]
          == VC.derive_blocked_by(r["definition_status"], r["data_status"],
                                  r["source_qualification"])
          for r in R))

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
_ready = sorted(r["rule_id"] for r in R if r["evaluator_status"] == "READY")
_want_ready = sorted({t["rule_id"]}
                     | {r["rule_id"] for r in APPLIED if r["data_status"] == "AVAILABLE"}
                     | {r["rule_id"] for r in DATA_APPLIED})
check("READY 는 원래 1건 + 정의 적용∩데이터 + P3 데이터 적용 뿐",
      _ready == _want_ready, f"{_ready} vs {_want_ready}")
check("이 Rule 은 적용 대상이 아니었다 — 원래부터 READY 였다",
      t["definition_application"] is None
      and t["definition_status_before_application"] == "DEFINED")
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

print("K-9b Definition Application — pilot + 2차 확대")
by_id0 = {r["rule_id"]: r for r in R}
EXPECTED_APPLIED = ["RULE-0002", "RULE-0004", "RULE-0010", "RULE-0011",
                    "RULE-0012", "RULE-0015", "RULE-0017", "RULE-0018",
                    "RULE-0019", "RULE-0020", "RULE-0021", "RULE-0022",
                    "RULE-0025"]
check("적용된 Rule 이 정확히 13건 (pilot 2 + 2차 7 + 최종 4)",
      len(APPLIED) == 13, str(len(APPLIED)))
check("적용 대상이 승인 목록과 정확히 같다",
      sorted(r["rule_id"] for r in APPLIED) == EXPECTED_APPLIED,
      str(sorted(r["rule_id"] for r in APPLIED)))
check("★ pilot 2건의 적용이 확대 후에도 보존된다",
      all(by_id0[r]["definition_status"] == "DEFINED"
          and by_id0[r]["evaluator_status"] == "READY"
          and by_id0[r]["definition_application"] is not None
          for r in ("RULE-0019", "RULE-0025")))
check("★ 데이터가 없는 적용 Rule 은 READY 가 아니다",
      all(by_id0[r]["evaluator_status"] == "BLOCKED"
          and "DEFINITION_UNDEFINED" not in by_id0[r]["blocked_by"]
          and "DATA_MISSING" in by_id0[r]["blocked_by"]
          for r in ("RULE-0002", "RULE-0004", "RULE-0012", "RULE-0015",
                    "RULE-0017", "RULE-0018", "RULE-0020")))
# ★ data_source 경계 확정 — capability 축은 definition 요구에서 빠지되,
#   데이터·source 상태는 그대로 남아야 한다. 양방향으로 확인한다.
_CAP4 = ("RULE-0010", "RULE-0011", "RULE-0021", "RULE-0022")
check("★ capability 축 4건이 DEFINED 로 적용됐다",
      all(by_id0[r]["definition_status"] == "DEFINED"
          and by_id0[r]["definition_application"] is not None for r in _CAP4))
check("★ 그 4건은 data_source 를 capability 축 제외로 명시한다",
      all(by_id0[r]["definition_application"]["capability_axis_excluded"]
          == ["data_source"] for r in _CAP4))
check("★ DEFINED 가 됐다고 DATA_MISSING 이 풀리지 않는다",
      all(by_id0[r]["data_status"] == "MISSING"
          and "DATA_MISSING" in by_id0[r]["blocked_by"] for r in _CAP4))
check("★ SOURCE_UNRESOLVED 도 자동 해제되지 않는다",
      all(by_id0[r]["source_qualification"] == "SOURCE_UNRESOLVED"
          and "SOURCE_UNRESOLVED" in by_id0[r]["blocked_by"]
          for r in ("RULE-0010", "RULE-0021")))
check("경계 판정이 코드 상수로 기록돼 있다",
      PR.CAPABILITY_AXIS_IS_NOT_DEFINITION is True
      and "자동 해제하지 않는다" in PR.CAPABILITY_AXIS_NOTE)
check("capability 축이 없는 Rule 은 제외 목록이 비어 있다",
      all(r["definition_application"]["capability_axis_excluded"] == []
          for r in APPLIED if not r["data_capability_axis"]))
check("allowlist 와 일치", set(PR.DEFINITION_APPLICATION_PILOT)
      == {r["rule_id"] for r in APPLIED})
check("★ 남은 UNDEFINED 는 적용 금지 집합과 정확히 같다",
      {r["rule_id"] for r in R if r["definition_status"] == "UNDEFINED"}
      == set(PR.DEFINITION_APPLICATION_EXCLUDED),
      str(sorted(r["rule_id"] for r in R if r["definition_status"] == "UNDEFINED")))
for r in APPLIED:
    check(f"{r['rule_id']} 적용 기록이 UNDEFINED→DEFINED 를 남긴다",
          r["definition_application"]["from"] == "UNDEFINED"
          and r["definition_application"]["to"] == "DEFINED"
          and r["definition_status_before_application"] == "UNDEFINED"
          and r["definition_status"] == "DEFINED")
    # ★ definition 축 성분만 판정으로 덮이면 된다. capability 축은 제외 기록으로
    #   남고, 그 제외분은 반드시 `data_capability_axis` 에 근거해야 한다.
    _cov = {d["decision_unit"].split("::", 1)[1] for d in r["cio_definition_decisions"]}
    _exc = set(r["definition_application"]["capability_axis_excluded"])
    check(f"{r['rule_id']} definition 축 결핍이 전부 판정으로 덮여 있다",
          set(r["missing_components"]) - _exc <= _cov,
          str(sorted(set(r["missing_components"]) - _exc - _cov)))
    check(f"{r['rule_id']} 제외분은 capability 축 기록에 근거한다",
          _exc <= set(r["data_capability_axis"]), str(sorted(_exc)))
    check(f"{r['rule_id']} 판정 원문이 그대로 남아 있다",
          all(d["cio_decision"] is not None for d in r["cio_definition_decisions"]))
check("★ 새 정의를 만들지 않았다 — 판정 단위 수가 늘지 않았다",
      sum(len(r["cio_definition_decisions"]) for r in R) == 43)
by_id = {r["rule_id"]: r for r in R}
for rid in ("RULE-0009", "RULE-0016"):
    check(f"{rid} 는 건드리지 않았다",
          by_id[rid]["definition_application"] is None
          and by_id[rid]["definition_status"] == "UNDEFINED"
          and by_id[rid]["evaluator_status"] == "BLOCKED")
    check(f"{rid} 는 적용 금지 목록에 있다", rid in PR.DEFINITION_APPLICATION_EXCLUDED)
check("적용하지 않은 23건은 적용 전 값과 동일하다",
      all(r["definition_status"] == r["definition_status_before_application"]
          for r in R if not r["definition_application"]))
check("Production 경계는 그대로", LIVE["consumable_by_evaluator"] is False
      and "HOLD" in LIVE["production_state"])

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
    """★ 적용 allowlist 밖의 UNDEFINED 를 골라 위조한다 — allowlist 안을 고르면
    어차피 적용될 Rule 이라 개수가 움직이지 않아 아무것도 검사하지 못한다."""
    for x in m["mapping"]:
        if (x["destination"] == SM.DEST_SSOT and x["definition_status"] == "UNDEFINED"
                and x.get("canonical_rule_id") not in PR.DEFINITION_APPLICATION_PILOT):
            x["definition_status"] = "DEFINED"
            return m
    raise AssertionError("allowlist 밖 UNDEFINED 가 없다 — 전제가 깨졌다")


e = run(m_resolve)
check("UNDEFINED 개수 변화를 잡는다",
      any("definition_undefined" in x for x in e), str(e[:3]))


def m_ready(m):
    """★ evaluator_status 는 이제 rules.json 이 파생한다. 그래서 upstream 을 READY 로
    바꾸는 것으로는 I-3 를 깰 수 없다 — 대신 **적용 대상이 아닌** UNDEFINED Rule 의
    데이터를 AVAILABLE 로 위조해 파생 자체가 READY 를 내도록 만든다."""
    for x in m["mapping"]:
        if (x["destination"] == SM.DEST_SSOT and x["definition_status"] == "UNDEFINED"
                and x.get("canonical_rule_id") not in PR.DEFINITION_APPLICATION_PILOT):
            x["data_status"] = "AVAILABLE"
            return m


_p, _e, _w = run(m_ready, full=True)
# ★ 정직하게 적는다 — evaluator_status 가 파생값이 된 뒤로 「UNDEFINED 인데 READY」인
#   레코드는 **구조적으로 만들어지지 않는다**. 따라서 I-3 오류 문자열을 기대하는 것은
#   이제 의미가 없다. 대신 실제로 falsifiable 한 두 가지를 본다.
_forged = [r for r in _p["rules"]
           if r["definition_status"] == "UNDEFINED" and r["data_status"] == "AVAILABLE"]
check("위조로 UNDEFINED × AVAILABLE 레코드가 실제로 생겼다", len(_forged) >= 1)
check("① 그래도 READY 가 되지 않는다",
      all(r["evaluator_status"] != "READY" for r in _forged))
check("① 차단 사유에 DEFINITION_UNDEFINED 가 남는다",
      all("DEFINITION_UNDEFINED" in r["blocked_by"] for r in _forged))
check("② 상태 개수 이동이 적용 기록으로 설명되지 않아 거부된다",
      any("이어야 하는데" in x for x in _e), str(_e[:3]))
check("③ 위반이 있으므로 발행하지 않았다", not _w)
check("파생 계약 자체가 UNDEFINED 를 READY 로 만들지 않는다",
      VC.derive_evaluator_status("UNDEFINED", "AVAILABLE") != "READY")

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
