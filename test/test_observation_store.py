#!/usr/bin/env python3
"""Observation Layer 층 ④ 회귀 — Observation Store (S3 · CIO 승인 2026-08-16).

★ 이 회귀가 증명하는 것
   ① key 는 subject + measurement_identity + economic_period_end **셋뿐**
   ② 첫 동작이 `validate_record()` — 유효하지 않은 record 는 저장 판단에 못 들어간다
   ③ D-6 경계: FY26 Q1 이전 period → `PRE_SERIES_BACKFILL_FORBIDDEN` · 거부 증거 보존
   ④ IDEMPOTENT / CONFLICT / REVISION 세 갈래 분리
   ⑤ 조용한 overwrite 없음 · 기존 revision 삭제 없음 · authority 자동 선택 없음
   ⑥ CONFLICT · REVISION 둘 다 fail-open 으로 소비 가능해지지 않는다
   ⑦ deterministic serialization · 입력 state 불변
   ⑧ store 가 Git · workflow · evaluator · collector 를 모른다

★ 이 회귀가 증명하지 못하는 것
   pair · runtime state · evaluator · live 취득 — 전부 S4 이후 Gate 다.

⛔ 네트워크를 쓰지 않는다. fixture only.
"""
from __future__ import annotations

import ast
import copy
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
sys.path.insert(0, os.path.join(ROOT, "observation"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store as S                                                   # noqa: E402
import record as RC                                                 # noqa: E402
import rule0022_commercial_rpo as OBS                                # noqa: E402
import msft_sec_results_acquisition as ACQ                           # noqa: E402

import checkkit as K                                                # noqa: E402
K.init(quiet=True)
check, need, skip, guard, section = K.check, K.need, K.skip, K.guard, K.section

FX_DIR = os.path.join(ROOT, "collectors", "fixtures")
STORE_SRC = os.path.join(ROOT, "observation", "store.py")


def _imports(path):
    out = set()
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add((node.module or "").split(".")[0])
    return out


def _calls(path):
    out = set()
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def records_from(manifest):
    out = []
    man = json.load(open(os.path.join(FX_DIR, manifest), encoding="utf-8"))
    for c in sorted(man["captured"], key=lambda x: x["filing_date"]):
        html = open(os.path.join(FX_DIR, c["fixture_file"]), encoding="utf-8").read()
        prov = ACQ.exhibit_provenance(c, c["exhibit"], c["exhibit_sha256"])
        prov["slice_sha256"] = c["slice_sha256"]
        d, _, _ = OBS.observe_html(html, provenance=prov)
        if d is None:
            continue
        r, err = RC.try_build(d)
        if r is not None:
            out.append(r)
    return out


def as_live(record):
    """같은 SEC 원문을 **live 경로로 읽었을 때의 record 표현**을 만든다.

    ★ 값도 원문도 바꾸지 않는다. 바뀌는 것은 acquisition representation 뿐이다.
      live 경로(`observe_live`)는 fixture manifest 가 없으므로 `slice_sha256` 을
      산출하지 않고, 대신 EX-99.1 교차확인 증거(`identity_evidence`)를 남긴다.
    ⛔ source_sha256 · accession · filing_date · exhibit identity 는 건드리지 않는다 —
      건드리면 이 helper 가 검사하려는 것 자체가 사라진다.
    """
    live = copy.deepcopy(record)
    prov = live["provenance"]
    prov.pop("slice_sha256", None)
    ident = prov.get("exhibit_identity") or {}
    prov["identity_evidence"] = {
        "primary_document": ident.get("document"),
        "primary_selection": ident.get("selection"),
        "primary_type": ident.get("type"),
        "secondary_document": ident.get("document"),
        "secondary_selection": "index_html_type_column",
        "secondary_type": ident.get("type"),
        "cross_check": "AGREE",
    }
    return live


FY26 = records_from("azure_cc_MANIFEST.json")
KEY0 = ("MSFT", "Commercial remaining performance obligation", "2025-09-30")

# ══════════════════════════════════════════════════════════════════════
with section("A. 층 경계 — store 가 Git · workflow · evaluator · collector 를 모른다"):
    imp = _imports(STORE_SRC)
    for banned in ("git", "subprocess", "requests", "urllib", "http",
                   "rule0022_commercial_rpo", "msft_azure_cc",
                   "msft_sec_results_acquisition", "c4_sec_edgar_check"):
        check(f"★ store 가 `{banned}` 를 import 하지 않는다", banned not in imp, str(sorted(imp)))
    check("★★ store 가 record 층만 참조한다 (validate_record)", "record" in imp)
    check("store 가 normalize 를 직접 import 하지 않는다 (record 를 통한다)",
          "normalize" not in imp, str(sorted(imp)))
    calls = _calls(STORE_SRC)
    check("★★ store 의 첫 동작 계약 — `validate_record` 를 호출한다", "validate_record" in calls)
    check("★ store 가 `float()` 를 쓰지 않는다", "float" not in calls)
    # ⛔ 「소스에 `workflow` 문자열이 없다」식 검사는 두지 않는다 — docstring 이
    #    「⛔ workflow 를 모른다」고 **설명**하면 실패한다. 언급과 의존은 다르다.
    #    의존은 위 AST import 검사가, 동작은 아래 호출 검사가 정본이다.
    for banned in ("run", "check_output", "Popen", "system", "urlopen", "commit"):
        check(f"★ store 가 `{banned}()` 를 호출하지 않는다", banned not in calls,
              str(sorted(calls))[:0])

with section("A-2. key 계약 — 세 축뿐이다"):
    r0 = FY26[0]
    k = S.observation_key(r0)
    check("★★ key 가 정확히 3축이다", len(k) == 3, str(len(k)))
    check("key = (subject, measurement_identity, economic_period_end)",
          k == KEY0, str(k))
    ks = S.key_str(k)
    check("key 문자열이 세 축만 담는다", ks == "|".join(KEY0), ks)
    for banned in ("accession", "filing_date", "source_sha256", "run_date", "frame"):
        check(f"★ key 에 `{banned}` 가 들어가지 않는다",
              str(r0["provenance"].get(banned, "@@none@@")) not in ks.split("|"))
    # 같은 period, 다른 accession → 같은 key
    other = copy.deepcopy(r0)
    other["provenance"]["accession"] = "0000000000-00-000000"
    other["provenance"]["filing_date"] = "2030-01-01"
    check("★★ accession/filing_date 가 달라도 key 는 같다",
          S.observation_key(other) == k, str(S.observation_key(other)))
    # key 축 누락
    for miss in ("subject", "measurement_identity", "economic_period_end"):
        bad = copy.deepcopy(r0)
        bad[miss] = ""
        try:
            S.observation_key(bad)
            check(f"★ key 축 `{miss}` 가 비면 거부한다", False, "통과해버렸다")
        except S.StoreError:
            check(f"★ key 축 `{miss}` 가 비면 거부한다", True)
    try:
        S.key_str(("MSFT|X", "m", "2025-09-30"))
        check("★ key 축에 구분자가 있으면 거부한다", False, "통과해버렸다")
    except S.StoreError:
        check("★ key 축에 구분자가 있으면 거부한다", True)

# ══════════════════════════════════════════════════════════════════════
with section("B. ★★ FY26 Q1~Q4 정상 series 구성"):
    need("FY26 record 4건이 있다", len(FY26) == 4, str(len(FY26)))
    st0 = S.empty_state()
    st, results = S.apply_many(st0, FY26)
    check("★★ 4건 전부 NEW 다", [r["outcome"] for r in results] == [S.NEW] * 4,
          str([r["outcome"] for r in results]))
    check("★★ series 가 4개 key 를 갖는다", len(st["series"]) == 4, str(len(st["series"])))
    check("★★ 4개 key 전부 소비 가능하다", len(S.consumable_keys(st)) == 4,
          str(S.consumable_keys(st)))
    check("차단된 key 가 없다", S.blocked_keys(st) == {}, str(S.blocked_keys(st)))
    periods = sorted(e["key"]["economic_period_end"] for e in st["series"].values())
    check("★ economic period 가 FY26 Q1~Q4 다",
          periods == ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"], str(periods))
    for e in st["series"].values():
        check(f"{e['key']['economic_period_end']} revision 1건", len(e["revisions"]) == 1)
        check(f"{e['key']['economic_period_end']} conflict 0건", e["conflicts"] == [])
        check(f"★ {e['key']['economic_period_end']} `active_revision` 필드가 없다",
              "active_revision" not in e)
    check("★★ 빈 state 를 변형하지 않았다 (순수 함수)", st0 == S.empty_state())
    GOOD_STATE = st

# ══════════════════════════════════════════════════════════════════════
with section("C. ★★ IDEMPOTENT — 재관측이 revision 을 만들지 않는다"):
    before = S.serialize(GOOD_STATE)
    st, res = S.apply_record(GOOD_STATE, copy.deepcopy(FY26[0]))
    check("★★ 재삽입 결과가 IDEMPOTENT", res["outcome"] == S.IDEMPOTENT, res["outcome"])
    check("★★ series 가 전혀 변하지 않았다", S.serialize(st) == before)
    check("★ revision 이 늘지 않았다", res["revision_count"] == 1, str(res["revision_count"]))
    check("여전히 소비 가능하다", res["consumable"] is True)
    # provenance 의 비-material 필드가 달라도 IDEMPOTENT 여야 한다
    same = copy.deepcopy(FY26[0])
    same["provenance"]["observed_at"] = "2026-08-16T00:00:00Z"     # operational metadata
    same["provenance"]["report_date"] = "2025-10-29"
    st2, res2 = S.apply_record(GOOD_STATE, same)
    check("★★ operational metadata 가 달라도 IDEMPOTENT 다 (revision 오분류 없음)",
          res2["outcome"] == S.IDEMPOTENT, res2["outcome"])
    check("  그 경우도 series 가 변하지 않는다", S.serialize(st2) == before)

with section("C-2. ★★ CONFLICT — 같은 provenance · 다른 값"):
    for field, val, why in [("raw_value", "52%", "raw 변조"),
                            ("numeric_value", "52", "numeric 변조"),
                            ("sign_convention", "explicit_minus", "sign 변조")]:
        bad = copy.deepcopy(FY26[0])
        bad["decision"][field] = val
        st, res = S.apply_record(GOOD_STATE, bad)
        check(f"★★ {why} → CONFLICT", res["outcome"] == S.CONFLICT, res["outcome"])
        check(f"  {why} → 소비 불가", res["consumable"] is False)
        check(f"  {why} → 차단 사유가 기록된다",
              S.OBSERVATION_CONFLICT_UNRESOLVED in res["blocked_by"], str(res["blocked_by"]))
        e = S.get_entry(st, KEY0)
        check(f"  {why} → 기존 revision 이 삭제되지 않는다", len(e["revisions"]) == 1)
        check(f"★★ {why} → 기존 값이 덮이지 않는다",
              e["revisions"][0]["record"]["decision"]["raw_value"] == "51%",
              e["revisions"][0]["record"]["decision"]["raw_value"])
        c = e["conflicts"][0]
        check(f"  {why} → conflict 가 두 값을 함께 보존한다",
              bool(c["existing"]) and bool(c["incoming"]))
        check(f"  {why} → incoming 내용이 보존된다",
              c["incoming"]["content"]["decision"][field] == val,
              str(c["incoming"]["content"]["decision"][field]))
    # evidence 변조도 CONFLICT
    bad = copy.deepcopy(FY26[0])
    bad["evidence_columns"][0]["raw_value"] = "1%"
    bad["evidence_columns"][0]["numeric_value"] = "1"
    st, res = S.apply_record(GOOD_STATE, bad)
    check("★ evidence 변조도 CONFLICT 다", res["outcome"] == S.CONFLICT, res["outcome"])

with section("C-3. ★★ REVISION — 같은 key · 다른 provenance"):
    for field, val, why in [("accession", "0001193125-26-999999", "다른 accession"),
                            ("source_sha256", "f" * 64, "다른 source sha"),
                            ("filing_date", "2025-11-01", "다른 filing date")]:
        rev = copy.deepcopy(FY26[0])
        rev["provenance"][field] = val
        st, res = S.apply_record(GOOD_STATE, rev)
        check(f"★★ {why} → REVISION", res["outcome"] == S.REVISION, res["outcome"])
        check(f"  {why} → revision 이 2건이 된다", res["revision_count"] == 2,
              str(res["revision_count"]))
        check(f"★★ {why} → 소비 불가 (authority 미확정)", res["consumable"] is False)
        check(f"  {why} → 차단 사유가 REVISION_AUTHORITY_UNRESOLVED",
              S.REVISION_AUTHORITY_UNRESOLVED in res["blocked_by"], str(res["blocked_by"]))
        e = S.get_entry(st, KEY0)
        check(f"★★ {why} → 첫 record 가 삭제되지 않는다", len(e["revisions"]) == 2)
        accs = sorted(r["material_provenance"]["accession"] for r in e["revisions"])
        check(f"  {why} → 두 provenance 가 모두 보존된다", len(set(accs)) >= 1, str(accs))
        check(f"★★ {why} → `active_revision` 같은 자동 선택 필드가 없다",
              "active_revision" not in e and "current" not in e and "latest" not in e,
              str(sorted(e)))
    # exhibit document 변경도 revision
    rev = copy.deepcopy(FY26[0])
    rev["provenance"]["exhibit_identity"]["document"] = "msft-ex99_2.htm"
    _, res = S.apply_record(GOOD_STATE, rev)
    check("★ exhibit document 변경도 REVISION 이다", res["outcome"] == S.REVISION, res["outcome"])
    # row label / period raw 변경도 material 이다
    rev = copy.deepcopy(FY26[0])
    rev["period_end_raw"] = "September 30, 2025 "
    _, res = S.apply_record(GOOD_STATE, rev)
    check("★ period raw 변경도 REVISION 이다 (material provenance)",
          res["outcome"] == S.REVISION, res["outcome"])

with section("C-4. ★★ CONFLICT · REVISION 은 fail-open 으로 소비되지 않는다"):
    bad = copy.deepcopy(FY26[0]); bad["decision"]["raw_value"] = "52%"
    st_c, _ = S.apply_record(GOOD_STATE, bad)
    rev = copy.deepcopy(FY26[0]); rev["provenance"]["accession"] = "0001193125-26-999999"
    st_r, _ = S.apply_record(GOOD_STATE, rev)
    for name, st in (("CONFLICT", st_c), ("REVISION", st_r)):
        check(f"★★ {name} 후 그 key 는 consumable_keys 에서 빠진다",
              S.key_str(KEY0) not in S.consumable_keys(st), str(S.consumable_keys(st)))
        check(f"★★ {name} 후 blocked_keys 에 사유와 함께 나타난다",
              S.key_str(KEY0) in S.blocked_keys(st), str(S.blocked_keys(st)))
        check(f"  {name} 후에도 나머지 3개 key 는 소비 가능하다",
              len(S.consumable_keys(st)) == 3, str(len(S.consumable_keys(st))))
    # 차단이 해제되려면 revision 이 1건이고 conflict 가 없어야 한다
    e = S.get_entry(st_r, KEY0)
    check("★ series_consumable 은 revision 이 2건이면 False 다",
          S.series_consumable(e) is False and len(e["revisions"]) == 2)
    # ⛔ revision 0건도 소비 가능해서는 안 된다 — 「차단 사유가 없다」와
    #    「소비할 관측이 있다」는 다른 명제다.
    empty_entry = {"key": {}, "revisions": [], "conflicts": []}
    check("★★ series_consumable 은 revision 이 0건이면 False 다",
          S.series_consumable(empty_entry) is False, str(S.series_blocked_by(empty_entry)))
    one_entry = {"key": {}, "revisions": [{"material_provenance": {}}], "conflicts": []}
    check("★ revision 이 정확히 1건이고 차단 사유가 없으면 True 다",
          S.series_consumable(one_entry) is True)

# ══════════════════════════════════════════════════════════════════════
with section("C-5. ★★ conflict evidence idempotency (S3.1 · REPEATED_CONFLICT_NOT_IDEMPOTENT)"):
    # ★ 재시도(네트워크 · workflow 재실행 · persistence retry)가 충돌 하나를 N 개처럼
    #   부풀리면 안 된다. 같은 미해소 충돌의 재관측은 **새 충돌이 아니다.**
    A52 = copy.deepcopy(FY26[0])
    A52["decision"]["raw_value"] = "52%"
    A52["decision"]["numeric_value"] = "52"
    B53 = copy.deepcopy(FY26[0])
    B53["decision"]["raw_value"] = "53%"
    B53["decision"]["numeric_value"] = "53"

    s1, r1 = S.apply_record(GOOD_STATE, A52)
    check("★ 첫 충돌 → CONFLICT", r1["outcome"] == S.CONFLICT, r1["outcome"])
    check("★ 첫 충돌은 새 충돌이다", r1.get("new_conflict") is True, str(r1.get("new_conflict")))
    check("  conflict 가 1건", r1["conflict_count"] == 1, str(r1["conflict_count"]))

    s2, r2 = S.apply_record(s1, copy.deepcopy(A52))
    check("★★ 동일 conflict 재전달 → conflict count 불변",
          r2["conflict_count"] == 1, str(r2["conflict_count"]))
    check("★★ 동일 conflict 재전달 → canonical serialization 불변",
          S.serialize(s2) == S.serialize(s1))
    check("★★ 재전달은 새 충돌이 아니다 (new_conflict=False)",
          r2.get("new_conflict") is False, str(r2.get("new_conflict")))
    check("  outcome 어휘는 그대로 CONFLICT 다 (새 상태어휘 없음)",
          r2["outcome"] == S.CONFLICT, r2["outcome"])
    check("★ 3회째도 여전히 1건", S.apply_record(s2, copy.deepcopy(A52))[1]["conflict_count"] == 1)

    s3, r3 = S.apply_record(s2, B53)
    check("★★ 제3의 새 content → conflict count +1", r3["conflict_count"] == 2,
          str(r3["conflict_count"]))
    check("★ 그것은 새 충돌이다", r3.get("new_conflict") is True, str(r3.get("new_conflict")))
    s4, r4 = S.apply_record(s3, copy.deepcopy(B53))
    check("★ 새 충돌도 재전달하면 늘지 않는다", r4["conflict_count"] == 2, str(r4["conflict_count"]))
    check("  그때도 직렬화 불변", S.serialize(s4) == S.serialize(s3))

    for name, st_ in (("1회", s1), ("재전달", s2), ("제3충돌", s3)):
        check(f"★★ {name} 후에도 consumable=False", not S.get_entry(st_, KEY0)["consumable"])
        check(f"  {name} 후 차단 사유 유지",
              S.OBSERVATION_CONFLICT_UNRESOLVED in S.get_entry(st_, KEY0)["blocked_by"])
        e_ = S.get_entry(st_, KEY0)
        check(f"★★ {name} 후 original revision 이 불변이다",
              len(e_["revisions"]) == 1
              and e_["revisions"][0]["record"]["decision"]["raw_value"] == "51%",
              e_["revisions"][0]["record"]["decision"]["raw_value"])
        check(f"  {name} 후 나머지 3 series 는 그대로 소비 가능",
              len(S.consumable_keys(st_)) == 3, str(len(S.consumable_keys(st_))))

    # ⛔ provenance 만 같다고 같은 충돌로 뭉개지 않는다 — 위 B53 이 그 증거다
    e3 = S.get_entry(s3, KEY0)
    digs = [c["incoming"]["content_digest"] for c in e3["conflicts"]]
    check("★★ 두 충돌의 incoming digest 가 서로 다르다", len(set(digs)) == 2, str(len(set(digs))))
    check("  두 충돌의 material provenance digest 는 같다",
          len({c["material_provenance_digest"] for c in e3["conflicts"]}) == 1)

    # ⛔ content 만 같다고 같은 충돌로 뭉개지 않는다 — provenance 가 다르면 다른 충돌이다.
    #   ★ revision 이 둘(P1·P2)일 때 각각에 같은 값(52%)이 충돌해 들어오는 경우다.
    P2 = copy.deepcopy(FY26[0])
    P2["provenance"]["accession"] = "0001193125-26-888888"
    P2["provenance"]["source_sha256"] = "a" * 64
    sp1, rp1 = S.apply_record(GOOD_STATE, P2)
    check("P2 는 REVISION 이다", rp1["outcome"] == S.REVISION, rp1["outcome"])
    sp2, _ = S.apply_record(sp1, A52)                    # P1 provenance 로 52% 충돌
    P2_52 = copy.deepcopy(P2)
    P2_52["decision"]["raw_value"] = "52%"
    P2_52["decision"]["numeric_value"] = "52"
    sp3, rp3 = S.apply_record(sp2, P2_52)                # P2 provenance 로 같은 52% 충돌
    check("★★ provenance 가 다르면 같은 content 라도 새 충돌이다",
          rp3["conflict_count"] == 2, str(rp3["conflict_count"]))
    check("★ 그것은 new_conflict=True 다", rp3.get("new_conflict") is True,
          str(rp3.get("new_conflict")))
    ep = S.get_entry(sp3, KEY0)
    check("  두 충돌의 material provenance digest 가 서로 다르다",
          len({c["material_provenance_digest"] for c in ep["conflicts"]}) == 2)
    check("  두 충돌의 incoming content digest 는 같다",
          len({c["incoming"]["content_digest"] for c in ep["conflicts"]}) == 1)

    # REVISION 재전달 idempotency 도 유지되는지 (기존 계약)
    rev = copy.deepcopy(FY26[0])
    rev["provenance"]["accession"] = "0001193125-26-999999"
    sr1, _ = S.apply_record(GOOD_STATE, rev)
    sr2, rr2 = S.apply_record(sr1, copy.deepcopy(rev))
    check("★★ REVISION 재전달은 IDEMPOTENT 다 (기존 계약 유지)",
          rr2["outcome"] == S.IDEMPOTENT, rr2["outcome"])
    check("  그 경우 revision 이 2건에서 늘지 않는다", rr2["revision_count"] == 2,
          str(rr2["revision_count"]))
    check("  직렬화도 불변", S.serialize(sr2) == S.serialize(sr1))

    check("★ FY26 정상 4 series 는 이 절 전체에서 변하지 않았다",
          len(S.consumable_keys(GOOD_STATE)) == 4 and len(GOOD_STATE["series"]) == 4)

with section("D. ★★ D-6 경계 — pre-series backfill 금지"):
    FY25_PERIODS = ["2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30"]
    for pe in FY25_PERIODS:
        r = copy.deepcopy(FY26[0])
        r["economic_period_end"] = pe
        r["period_end_raw"] = "June 30, 2025"
        st, res = S.apply_record(GOOD_STATE, r)
        check(f"★★ {pe} → PRE_SERIES_BACKFILL_FORBIDDEN",
              res["outcome"] == S.REJECTED_PRE_SERIES, res["outcome"])
        check(f"  {pe} → accepted=False", res["accepted"] is False)
        check(f"★★ {pe} → series 에 저장되지 않는다",
              S.serialize(st) == S.serialize(GOOD_STATE))
        check(f"  {pe} → 거부 증거에 시도된 key 가 있다",
              res["evidence"]["attempted_key"].endswith(pe), res["evidence"]["attempted_key"])
        check(f"  {pe} → 거부 증거에 material provenance 가 있다",
              bool(res["evidence"]["material_provenance"]["accession"]))
        check(f"  {pe} → 거부 증거에 series 경계가 명시된다",
              res["evidence"]["series_start"] == "2025-09-30", res["evidence"]["series_start"])
    check("★ 경계값 2025-09-30 자체는 허용된다 (경계 포함)",
          S.COMMERCIAL_RPO_SERIES_START == "2025-09-30")
    r = copy.deepcopy(FY26[0])
    r["economic_period_end"] = "2025-09-29"
    _, res = S.apply_record(S.empty_state(), r)
    check("★★ 경계 하루 전은 거부된다", res["outcome"] == S.REJECTED_PRE_SERIES, res["outcome"])

with section("D-2. 거부 증거는 series 밖 로그에 남는다"):
    r = copy.deepcopy(FY26[0]); r["economic_period_end"] = "2024-09-30"
    st, res = S.apply_record(GOOD_STATE, r)
    logged = S.record_rejection(st, res)
    check("★ rejection 로그에 기록된다", len(logged["rejections"]) == 1)
    check("★★ rejection 이 series 를 오염시키지 않는다",
          logged["series"] == GOOD_STATE["series"])
    check("  로그에 사유가 남는다", "D-6" in logged["rejections"][0]["reason"])
    # ⛔ 최소 dict 를 넘기지 않는다 — 가드가 사라지면 KeyError(ERROR)가 되어 **검사가
    #    실행되지 않는다.** ERROR 는 FAIL 이 아니므로 판별력이 사라진다.
    _accepted = {"outcome": S.NEW, "accepted": True, "key": None,
                 "reason": "ok", "evidence": {}}
    try:
        S.record_rejection(GOOD_STATE, _accepted)
        check("★★ 수락된 결과를 rejection 으로 기록하려 하면 거부한다", False, "통과해버렸다")
    except S.StoreError:
        check("★★ 수락된 결과를 rejection 으로 기록하려 하면 거부한다", True)

# ══════════════════════════════════════════════════════════════════════
with section("E. ★★ 입력 검증 — 첫 동작이 validate_record 다"):
    INVALID = [
        ("subject 오염", "subject", "AAPL"),
        ("measurement identity 오염", "measurement_identity", "Total RPO"),
        ("normalized 가 False", "normalized", False),
        ("persisted 가 True", "persisted", True),
        ("schema_version 이 다름", "schema_version", "observation/0"),
        ("economic_period_end 가 존재하지 않는 날짜", "economic_period_end", "2025-02-31"),
        ("period_end_raw 소실", "period_end_raw", ""),
    ]
    for why, path, val in INVALID:
        bad = copy.deepcopy(FY26[0])
        bad[path] = val
        st, res = S.apply_record(GOOD_STATE, bad)
        check(f"★★ 무효 record → 거부 ({why})",
              res["outcome"] == S.REJECTED_INVALID_RECORD, res["outcome"])
        check(f"  {why} → series 무변화", S.serialize(st) == S.serialize(GOOD_STATE))
        check(f"  {why} → 저장 판단에 들어가지 않는다 (key 없음)", res["key"] is None)
    st, res = S.apply_record(GOOD_STATE, {"nope": 1})
    check("★ record 아닌 dict → 거부", res["outcome"] == S.REJECTED_INVALID_RECORD)
    st, res = S.apply_record(GOOD_STATE, "nope")
    check("★ record 가 문자열 → 거부", res["outcome"] == S.REJECTED_INVALID_RECORD)
    try:
        S.apply_record({"schema_version": "wrong"}, FY26[0])
        check("★ store state schema 가 다르면 거부", False, "통과해버렸다")
    except S.StoreError:
        check("★ store state schema 가 다르면 거부", True)

# ══════════════════════════════════════════════════════════════════════
with section("F. ★★ deterministic serialization · 이전 record 불변"):
    a = S.serialize(GOOD_STATE)
    b = S.serialize(copy.deepcopy(GOOD_STATE))
    check("★★ 같은 state 는 항상 같은 바이트열", a == b)
    check("  키가 정렬돼 있다 (canonical)", a == S.canonical_json(GOOD_STATE) + "\n")
    check("  왕복이 동일하다", S.serialize(S.deserialize(a)) == a)
    # 삽입 순서를 바꿔도 같은 state
    st_fwd, _ = S.apply_many(S.empty_state(), FY26)
    st_rev, _ = S.apply_many(S.empty_state(), list(reversed(FY26)))
    check("★★ 삽입 순서가 달라도 직렬화 결과가 같다 (determinism)",
          S.serialize(st_fwd) == S.serialize(st_rev))
    # revision 삽입 순서도 무관
    r1 = copy.deepcopy(FY26[0]); r1["provenance"]["accession"] = "0000000000-00-000001"
    s_a, _ = S.apply_many(S.empty_state(), [FY26[0], r1])
    s_b, _ = S.apply_many(S.empty_state(), [r1, FY26[0]])
    check("★★ revision 삽입 순서가 달라도 결과가 같다",
          S.serialize(s_a) == S.serialize(s_b))
    check("  그 경우도 revision 2건 · 소비 불가",
          len(S.get_entry(s_a, KEY0)["revisions"]) == 2
          and not S.get_entry(s_a, KEY0)["consumable"])

    # 이전 record 불변 — 원본 record 객체를 변형해도 store 안은 그대로
    st, _ = S.apply_record(S.empty_state(), FY26[0])
    snapshot = S.serialize(st)
    FY26[0]["decision"]["raw_value"] = "@@tampered@@"
    check("★★ 저장 후 원본 record 를 변형해도 store 는 그대로다 (deep copy)",
          S.serialize(st) == snapshot)
    FY26[0]["decision"]["raw_value"] = "51%"   # 원복

with section("F-2. IO 경계 — 파일만 안다"):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "sub", "store.json")
        check("★ 파일이 없으면 빈 state", S.load_state(p) == S.empty_state())
        S.save_state(GOOD_STATE, p)
        check("★ 저장 후 왕복이 동일하다", S.serialize(S.load_state(p)) == S.serialize(GOOD_STATE))
        check("★★ save_state 가 `os.replace` 로 원자적 교체를 한다 (부분 기록 방지)",
              "replace" in _calls(STORE_SRC), str(sorted(_calls(STORE_SRC)))[:0])
        check("  임시 파일이 남지 않는다",
              [f for f in os.listdir(os.path.dirname(p)) if f.startswith(".obsstore-")] == [])
        # 손상 파일을 조용히 빈 state 로 대체하지 않는다
        open(p, "w", encoding="utf-8").write('{"schema_version":"nope"}')
        try:
            S.load_state(p)
            check("★★ 손상/다른 schema 파일을 조용히 수용하지 않는다", False, "통과해버렸다")
        except S.StoreError:
            check("★★ 손상/다른 schema 파일을 조용히 수용하지 않는다", True)
        open(p, "w", encoding="utf-8").write("{not json")
        try:
            S.load_state(p)
            check("★ 깨진 JSON 을 조용히 수용하지 않는다", False, "통과해버렸다")
        except (S.StoreError, json.JSONDecodeError):
            check("★ 깨진 JSON 을 조용히 수용하지 않는다", True)

with section("G. material provenance 정의"):
    r = FY26[0]
    mp = S.material_provenance(r)
    for f in S.MATERIAL_PROVENANCE_FIELDS + S.MATERIAL_RECORD_FIELDS:
        check(f"material provenance 에 `{f}` 축이 있다", f in mp)
    check("★ operational metadata 는 들어가지 않는다",
          not any(k in mp for k in ("observed_at", "run_id", "acceptance", "report_date")),
          str(sorted(mp)))
    check("★ decision column identity 가 material 이다",
          mp["decision_column_identity"] == r["decision"]["column_identity"])
    # ★★ H2 (CIO 판정 2026-08-16 · `LIVE_FIXTURE_STORE_PROVENANCE_DIVERGENCE`)
    #    이전 계약은 `slice_sha256` 을 비교 축으로 두었고, 그 결과 같은 SEC 원문을
    #    fixture 로 읽었는지 live 로 읽었는지가 「다른 출처」로 둔갑했다.
    #    ⛔ 아래 두 검사는 **약화가 아니라 방향 반전**이다 — 계약 자체가 바뀌었다.
    check("★★ H2 · slice_sha256 은 material 비교 축이 아니다",
          "slice_sha256" not in mp
          and "slice_sha256" not in S.MATERIAL_PROVENANCE_FIELDS)
    a = copy.deepcopy(r); a["provenance"].pop("slice_sha256", None)
    check("★★ H2 · slice_sha256 유무가 provenance 차이를 만들지 않는다",
          S.digest(S.material_provenance(a)) == S.digest(mp))
    check("★ identity_evidence 도 material 축이 아니다", "identity_evidence" not in mp)
    b = copy.deepcopy(r); b["provenance"]["identity_evidence"] = {"cross_check": "AGREE"}
    check("★ identity_evidence 추가가 provenance 차이를 만들지 않는다",
          S.digest(S.material_provenance(b)) == S.digest(mp))

with section("G-2. audit provenance — 비교 축이 아니라고 버리는 것이 아니다"):
    r = FY26[0]
    check("`AUDIT_PROVENANCE_FIELDS` 가 정의되어 있다",
          isinstance(S.AUDIT_PROVENANCE_FIELDS, tuple)
          and len(S.AUDIT_PROVENANCE_FIELDS) >= 2)
    overlap = set(S.MATERIAL_PROVENANCE_FIELDS) & set(S.AUDIT_PROVENANCE_FIELDS)
    check("★ material 축과 audit 축은 교집합이 없다", not overlap, str(sorted(overlap)))
    ap = S.audit_provenance(r)
    for f in S.AUDIT_PROVENANCE_FIELDS:
        check(f"audit provenance 가 `{f}` 축을 노출한다", f in ap)
    check("★ fixture record 의 slice_sha256 이 보존된다",
          ap["slice_sha256"] == r["provenance"]["slice_sha256"])
    live = as_live(r)
    check("★ live record 는 slice_sha256 부재를 None 으로 드러낸다",
          S.audit_provenance(live)["slice_sha256"] is None)
    check("★ live record 의 identity_evidence 는 보존된다",
          S.audit_provenance(live)["identity_evidence"] is not None)
    st, _ = S.apply_record(S.empty_state(), r)
    entry = st["series"][S.key_str(S.observation_key(r))]
    check("★★ store 에 저장된 record 안에 slice_sha256 이 그대로 있다",
          entry["revisions"][0]["record"]["provenance"].get("slice_sha256")
          == r["provenance"]["slice_sha256"])
    check("★ 그럼에도 material_provenance 에는 없다",
          "slice_sha256" not in entry["revisions"][0]["material_provenance"])

# ══════════════════════════════════════════════════════════════════════
# H. ★★ H2 Exit Criteria (CIO 확정 2026-08-16)
#    「동일 SEC 원문을 fixture 로 읽든 live 로 읽든 Store 는 같은 자료로 인식한다」
#    ⛔ 기존 REVISION / CONFLICT fail-closed 계약은 약화하지 않는다 — H-3 · H-4 가 지킨다.
# ══════════════════════════════════════════════════════════════════════
with section("H-1. fixture → live · 동일 원문 · 동일 값 → IDEMPOTENT"):
    r = FY26[0]
    live = as_live(r)
    check("전제 · 두 표현의 acquisition 축이 실제로 다르다",
          S.audit_provenance(r) != S.audit_provenance(live))
    check("전제 · source_sha256 은 같다",
          r["provenance"]["source_sha256"] == live["provenance"]["source_sha256"])
    st, r1 = S.apply_record(S.empty_state(), r)
    check("  1차 입력 = NEW", r1["outcome"] == S.NEW, r1["outcome"])
    st2, r2 = S.apply_record(st, live)
    check("★★ 2차 입력 = IDEMPOTENT", r2["outcome"] == S.IDEMPOTENT, r2["outcome"])
    e = st2["series"][S.key_str(S.observation_key(r))]
    check("★★ revision 수 = 1", len(e["revisions"]) == 1, str(len(e["revisions"])))
    check("★★ consumable = true", e["consumable"] is True)
    check("★ blocked_by 가 비어 있다", e["blocked_by"] == [], str(e["blocked_by"]))
    check("★ IDEMPOTENT 는 state 를 바꾸지 않는다", st2 is st)

with section("H-2. live → fixture · 역방향도 동일"):
    r = FY26[1]
    live = as_live(r)
    st, r1 = S.apply_record(S.empty_state(), live)
    check("  1차 입력(live) = NEW", r1["outcome"] == S.NEW, r1["outcome"])
    st2, r2 = S.apply_record(st, r)
    check("★★ 2차 입력(fixture) = IDEMPOTENT", r2["outcome"] == S.IDEMPOTENT, r2["outcome"])
    e = st2["series"][S.key_str(S.observation_key(r))]
    check("★★ revision 수 = 1", len(e["revisions"]) == 1, str(len(e["revisions"])))
    check("★★ consumable = true", e["consumable"] is True)

with section("H-3. source_sha256 이 실제로 다르면 → REVISION (약화 금지)"):
    r = FY26[2]
    other = copy.deepcopy(r)
    other["provenance"]["source_sha256"] = "f" * 64
    st, _ = S.apply_record(S.empty_state(), r)
    st2, res = S.apply_record(st, other)
    check("★★ 다른 원문은 여전히 REVISION 이다", res["outcome"] == S.REVISION, res["outcome"])
    e = st2["series"][S.key_str(S.observation_key(r))]
    check("★ revision 이 2건으로 보존된다", len(e["revisions"]) == 2, str(len(e["revisions"])))
    check("★★ authority 미해소로 소비 차단", e["consumable"] is False)
    check("★ 차단 사유가 REVISION_AUTHORITY_UNRESOLVED",
          S.REVISION_AUTHORITY_UNRESOLVED in e["blocked_by"], str(e["blocked_by"]))
    # ★ audit 축만 다른 경우와 **구별**되는지가 이 절의 핵심이다.
    _, res3 = S.apply_record(st, as_live(r))
    check("★★ 반면 audit 축만 다르면 REVISION 이 아니다",
          res3["outcome"] == S.IDEMPOTENT, res3["outcome"])

with section("H-4. 동일 material provenance · 다른 값 → CONFLICT (약화 금지)"):
    r = FY26[3]
    tampered = copy.deepcopy(r)
    tampered["decision"]["raw_value"] = "77%"
    tampered["decision"]["numeric_value"] = "77"
    st, _ = S.apply_record(S.empty_state(), r)
    st2, res = S.apply_record(st, tampered)
    check("★★ 같은 출처 · 다른 값은 여전히 CONFLICT", res["outcome"] == S.CONFLICT, res["outcome"])
    e = st2["series"][S.key_str(S.observation_key(r))]
    check("★ 충돌 증거가 보존된다", len(e["conflicts"]) == 1, str(len(e["conflicts"])))
    check("★★ 소비 차단", e["consumable"] is False)
    check("★ 차단 사유가 OBSERVATION_CONFLICT_UNRESOLVED",
          S.OBSERVATION_CONFLICT_UNRESOLVED in e["blocked_by"], str(e["blocked_by"]))
    # ★ live 표현으로 같은 다른-값이 들어와도 여전히 CONFLICT 여야 한다.
    _, res3 = S.apply_record(st, as_live(tampered))
    check("★★ live 표현의 다른 값도 CONFLICT 다", res3["outcome"] == S.CONFLICT, res3["outcome"])

with section("H-5. 입력 순서 대칭 — 순서가 결과를 바꾸지 않는다"):
    for r in FY26:
        live = as_live(r)
        a, _ = S.apply_record(S.empty_state(), r)
        a, _ = S.apply_record(a, live)
        b, _ = S.apply_record(S.empty_state(), live)
        b, _ = S.apply_record(b, r)
        ks = S.key_str(S.observation_key(r))
        ka, kb = a["series"][ks], b["series"][ks]
        pe = r["economic_period_end"]
        check(f"  [{pe}] 양방향 모두 revision 1건",
              len(ka["revisions"]) == 1 and len(kb["revisions"]) == 1)
        check(f"  [{pe}] 양방향 모두 consumable",
              ka["consumable"] is True and kb["consumable"] is True)
        check(f"★ [{pe}] material provenance digest 가 순서 무관하게 같다",
              ka["revisions"][0]["material_provenance_digest"]
              == kb["revisions"][0]["material_provenance_digest"])
    fwd = S.empty_state()
    for r in FY26:
        fwd, _ = S.apply_record(fwd, r)
    for r in FY26:
        fwd, _ = S.apply_record(fwd, as_live(r))
    rev = S.empty_state()
    for r in reversed(FY26):
        rev, _ = S.apply_record(rev, as_live(r))
    for r in reversed(FY26):
        rev, _ = S.apply_record(rev, r)
    check("★★ 전체 series key 집합이 같다", sorted(fwd["series"]) == sorted(rev["series"]))
    check("★★ 전체 series 가 모두 consumable 이다",
          all(e["consumable"] for e in fwd["series"].values())
          and all(e["consumable"] for e in rev["series"].values()))
    check("★★ key 별 material provenance digest 가 순서 무관하게 같다",
          {k: v["revisions"][0]["material_provenance_digest"] for k, v in fwd["series"].items()}
          == {k: v["revisions"][0]["material_provenance_digest"] for k, v in rev["series"].items()})

sys.exit(K.exit_code())
