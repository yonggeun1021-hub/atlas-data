#!/usr/bin/env python3
"""S4A 통합 회귀 — observe / persist 경계와 end-to-end dataflow (CIO 승인 2026-08-16).

★ 이 회귀가 증명하는 것
   ① observe 가 Store 를 **import 하지 않는다** / persist 가 acquisition·observer·
      normalize 를 **import 하지 않는다** — 두 단계가 물리적으로 분리됐다
   ② observe 는 저장소 밖으로만 emit 한다 (경로 수준 fail-closed)
   ③ FY26 4건 end-to-end: draft 4 → record 4 → Store NEW 4 → series 4 key
   ④ 재적용 → IDEMPOTENT 4 · store serialization 불변
   ⑤ malformed record → persist 미진입 / pre-series → reject /
      conflict · revision → blocked · non-consumable / observe 실패 → store 무변경
   ⑥ workflow 가 계약 순서를 갖는다

★ 이 회귀가 증명하지 못하는 것
   실제 SEC 취득 · dispatch · pair · evaluator — S4B 이후 Gate 다.

⛔ 네트워크를 쓰지 않는다. fixture only.
"""
from __future__ import annotations

import ast
import copy
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
sys.path.insert(0, os.path.join(ROOT, "observation"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import observe_rule0022 as OBSV                                     # noqa: E402
import persist_rule0022 as PERS                                     # noqa: E402
import store as ST                                                  # noqa: E402
import record as RC                                                 # noqa: E402

import checkkit as K                                                # noqa: E402
K.init(quiet=True)
check, need, skip, guard, section = K.check, K.need, K.skip, K.guard, K.section

FX_DIR = os.path.join(ROOT, "collectors", "fixtures")
MAN26 = os.path.join(FX_DIR, "azure_cc_MANIFEST.json")
MAN25 = os.path.join(FX_DIR, "azure_cc_fy25_MANIFEST.json")
OBSV_SRC = os.path.join(ROOT, "observation", "observe_rule0022.py")
PERS_SRC = os.path.join(ROOT, "observation", "persist_rule0022.py")
WF = os.path.join(ROOT, ".github", "workflows", "rule0022-observation.yml")


def _imports(path):
    out = set()
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add((node.module or "").split(".")[0])
    return out


# ══════════════════════════════════════════════════════════════════════
with section("A. ★★ observe / persist 물리적 분리 (AST)"):
    oi, pi = _imports(OBSV_SRC), _imports(PERS_SRC)
    check("★★ observe 가 `store` 를 import 하지 않는다", "store" not in oi, str(sorted(oi)))
    check("★★ observe 가 `persist_rule0022` 를 import 하지 않는다",
          "persist_rule0022" not in oi)
    check("observe 가 acquisition 을 쓴다", "msft_sec_results_acquisition" in oi)
    check("observe 가 observer 를 쓴다", "rule0022_commercial_rpo" in oi)
    check("observe 가 record(normalization) 를 쓴다", "record" in oi)
    check("★★ persist 가 acquisition 을 import 하지 않는다",
          "msft_sec_results_acquisition" not in pi, str(sorted(pi)))
    check("★★ persist 가 observer 를 import 하지 않는다",
          "rule0022_commercial_rpo" not in pi)
    check("★★ persist 가 normalize 를 import 하지 않는다", "normalize" not in pi)
    check("★★ persist 가 record 를 직접 import 하지 않는다 (store 를 통한다)",
          "record" not in pi, str(sorted(pi)))
    check("persist 가 store 를 쓴다", "store" in pi)
    for banned in ("subprocess", "requests", "urllib", "git"):
        check(f"★ observe 가 `{banned}` 를 import 하지 않는다", banned not in oi)
        check(f"★ persist 가 `{banned}` 를 import 하지 않는다", banned not in pi)

with section("A-2. ★★ observe 는 저장소 밖으로만 emit 한다"):
    for bad in (os.path.join(ROOT, "emission.json"),
                os.path.join(ROOT, "data", "emission.json"),
                os.path.join(ROOT, "observations", "x.json"),
                ROOT):
        try:
            OBSV.assert_outside_repository(bad)
            check(f"★★ 저장소 안 경로 거부 — {os.path.relpath(bad, ROOT)}", False, "통과해버렸다")
        except OBSV.ObserveError:
            check(f"★★ 저장소 안 경로 거부 — {os.path.relpath(bad, ROOT)}", True)
    with tempfile.TemporaryDirectory() as d:
        check("★ 저장소 밖 경로는 허용된다",
              OBSV.assert_outside_repository(os.path.join(d, "e.json")).startswith(d))
    # ⛔ 「소스에 `required=True` 문자열이 있다」로 보지 않는다 — 다른 인자에도 있어
    #    변이를 구별하지 못한다(실제로 IN-SRC-1 이 그 틈으로 SURVIVED 했다).
    #    ★ 실제로 `--source` 없이 부르면 실행되지 않는다는 **행동**을 못 박는다.
    with tempfile.TemporaryDirectory() as d:
        try:
            rc = OBSV.main(["--manifest", MAN26, "--out", os.path.join(d, "e.json")])
            check("★★ `--source` 없이 부르면 실행되지 않는다 (fallback 없음)",
                  False, f"실행돼버렸다 rc={rc}")
        except SystemExit as e:
            check("★★ `--source` 없이 부르면 실행되지 않는다 (fallback 없음)",
                  e.code != 0, str(e.code))
        check("  그 경우 emit 파일도 만들지 않는다",
              not os.path.exists(os.path.join(d, "e.json")))
    try:
        OBSV.observe_live(4)
        check("★★ live 관측은 S4A 에서 실행되지 않는다", False, "실행돼버렸다")
    except OBSV.ObserveError:
        check("★★ live 관측은 S4A 에서 실행되지 않는다 (S4B 승인 전 fail-closed)", True)

# ══════════════════════════════════════════════════════════════════════
with section("A-3. ★ 실패 층을 구별한다 — 관측 실패 vs 정규화 실패"):
    # ★ 「어느 층에서 실패했는가」가 사라지면 원인 귀속이 무너진다.
    html26 = open(os.path.join(FX_DIR, json.load(open(MAN26, encoding="utf-8"))
                               ["captured"][0]["fixture_file"]), encoding="utf-8").read()
    rec, why = OBSV._observe_one(html26, {"accession": "a", "filing_date": "d",
                                          "source_sha256": "s"})
    check("정상 provenance 조합이면 record 가 만들어진다", rec is not None, str(why))

    # provenance 필수 항목이 없으면 **정규화/record 단계**에서 실패한다
    rec2, why2 = OBSV._observe_one(html26, {"accession": "", "filing_date": "",
                                            "source_sha256": ""})
    check("★ provenance 결손 → record 가 만들어지지 않는다", rec2 is None)
    check("★★ 그 실패는 `normalization` 층으로 귀속된다",
          why2["stage"] == "normalization", str(why2["stage"]))
    check("★★ outcome 이 NORMALIZATION_FAILED 다 (ROW_ABSENT 로 뭉개지지 않는다)",
          why2["outcome"] == "NORMALIZATION_FAILED", str(why2["outcome"]))

    # 대조군 — 행이 없는 문서는 **관측** 층 ROW_ABSENT 다
    man25 = json.load(open(MAN25, encoding="utf-8"))
    html25 = open(os.path.join(FX_DIR, man25["captured"][0]["fixture_file"]),
                  encoding="utf-8").read()
    rec3, why3 = OBSV._observe_one(html25, {"accession": "a", "filing_date": "d",
                                            "source_sha256": "s"})
    check("★ 대조군: 행 부재는 `observation` 층이다", why3["stage"] == "observation",
          str(why3["stage"]))
    check("★★ 두 실패의 outcome 이 서로 다르다", why2["outcome"] != why3["outcome"],
          f'{why2["outcome"]} vs {why3["outcome"]}')

with section("B. ★★ FY26 end-to-end — draft 4 → record 4 → Store NEW 4"):
    em = OBSV.observe_fixture(MAN26)
    check("★ emission schema", em["schema_version"] == OBSV.EMISSION_SCHEMA_VERSION)
    check("★ source 가 fixture 로 기록된다", em["source"] == "fixture")
    check("★★ record 4건", em["observed"] == 4, str(em["observed"]))
    check("★ 실패 0건", em["failed"] == 0, str(em["failures"])[:80])
    for r in em["records"]:
        RC.validate_record(r)                 # 계약 위반이면 예외
    check("★★ emit 된 4건 전부 record 계약을 통과한다", True)
    check("★ emission 이 canonical JSON 으로 직렬화된다",
          OBSV.canonical_json(em) == json.dumps(em, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":")))
    check("★ emission 에 timestamp 류 비결정 값이 없다",
          not re.search(r"\d{4}-\d\d-\d\dT\d\d:", OBSV.canonical_json(em)))

    st0 = ST.empty_state()
    st1, res1 = PERS.persist(em, st0)
    check("★★ Store 결과가 NEW 4건", res1["outcome_counts"] == {"NEW": 4},
          str(res1["outcome_counts"]))
    check("★★ series 4 key", len(st1["series"]) == 4, str(len(st1["series"])))
    check("★★ 4 key 전부 consumable", len(res1["consumable_keys"]) == 4)
    check("★ blocked 0", res1["blocked_keys"] == {}, str(res1["blocked_keys"]))
    check("★ unresolved=False", res1["unresolved"] is False)
    check("★ store digest 가 결과에 기록된다", res1["store_digest"] == ST.digest(st1))
    check("★ 입력 state 불변 (순수 함수)", st0 == ST.empty_state())
    periods = sorted(e["key"]["economic_period_end"] for e in st1["series"].values())
    check("★ FY26 Q1~Q4 series",
          periods == ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"], str(periods))
    EM26, ST1 = em, st1

with section("B-2. ★★ 재적용 idempotency"):
    st2, res2 = PERS.persist(EM26, ST1)
    check("★★ 재적용 결과가 IDEMPOTENT 4건",
          res2["outcome_counts"] == {"IDEMPOTENT": 4}, str(res2["outcome_counts"]))
    check("★★ store serialization 이 동일하다", ST.serialize(st2) == ST.serialize(ST1))
    check("★ store digest 도 동일하다", res2["store_digest"] == res1["store_digest"])
    check("★ 여전히 4 key 소비 가능", len(res2["consumable_keys"]) == 4)
    st3, res3 = PERS.persist(EM26, st2)
    check("★ 3회째도 동일", ST.serialize(st3) == ST.serialize(ST1))
    # observe 재실행도 동일한 emission 을 낸다
    em_again = OBSV.observe_fixture(MAN26)
    check("★★ observe 재실행이 동일한 emission 을 낸다",
          OBSV.canonical_json(em_again) == OBSV.canonical_json(EM26))

with section("B-3. observe 단계 CLI 와 persist 단계 CLI 가 파일로 이어진다"):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "emission.json")
        rc = OBSV.main(["--source", "fixture", "--manifest", MAN26, "--out", out])
        check("★ observe CLI 성공", rc == 0, str(rc))
        check("★ emit 파일이 생성된다", os.path.exists(out))
        em_file = PERS.read_emission(out)
        check("★★ persist 가 파일에서 다시 읽는다 (직렬화 왕복)",
              len(em_file["records"]) == 4, str(len(em_file["records"])))
        store_p, result_p = os.path.join(d, "store.json"), os.path.join(d, "result.json")
        rc = PERS.main(["--emission", out, "--store", store_p, "--result", result_p])
        check("★ persist CLI 성공", rc == 0, str(rc))
        check("★ store 파일이 생성된다", os.path.exists(store_p))
        check("★ persistence artifact 가 생성된다", os.path.exists(result_p))
        saved = ST.load_state(store_p)
        check("★★ 저장된 store 가 4 key 를 갖는다", len(saved["series"]) == 4)
        rc2 = PERS.main(["--emission", out, "--store", store_p, "--result", result_p])
        check("★★ persist CLI 재실행도 성공하고 store 가 불변이다",
              rc2 == 0 and ST.serialize(ST.load_state(store_p)) == ST.serialize(saved))
        art = json.load(open(result_p, encoding="utf-8"))
        check("★ artifact schema", art["schema_version"] == PERS.PERSIST_SCHEMA_VERSION)
        check("★ artifact 가 IDEMPOTENT 를 보고한다",
              art["outcome_counts"] == {"IDEMPOTENT": 4}, str(art["outcome_counts"]))

# ══════════════════════════════════════════════════════════════════════
with section("C. ★★ failure injection"):
    # ① malformed record → persist 미진입
    bad_em = copy.deepcopy(EM26)
    bad_em["records"][0]["subject"] = "AAPL"
    st, res = PERS.persist(bad_em, ST.empty_state())
    check("★★ malformed record → REJECTED_INVALID_RECORD",
          res["outcome_counts"].get(ST.REJECTED_INVALID_RECORD) == 1,
          str(res["outcome_counts"]))
    check("★★ 그 record 는 series 에 들어가지 않는다", len(st["series"]) == 3,
          str(len(st["series"])))
    check("★ 거부 증거가 rejection 로그에 남는다", len(st["rejections"]) == 1)

    # ② emission schema 위반 → persist 진입 자체를 막는다
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "e.json")
        open(p, "w", encoding="utf-8").write('{"schema_version":"nope","records":[]}')
        try:
            PERS.read_emission(p)
            check("★★ emission schema 위반 → persist 미진입", False, "통과해버렸다")
        except PERS.PersistError:
            check("★★ emission schema 위반 → persist 미진입", True)
        open(p, "w", encoding="utf-8").write(
            '{"schema_version":"observation_emission/1","records":"nope"}')
        try:
            PERS.read_emission(p)
            check("★ records 가 목록이 아니면 거부", False, "통과해버렸다")
        except PERS.PersistError:
            check("★ records 가 목록이 아니면 거부", True)
        # record 0건이면 persist CLI 가 non-zero
        open(p, "w", encoding="utf-8").write(
            '{"schema_version":"observation_emission/1","records":[],"failures":[]}')
        rc = PERS.main(["--emission", p, "--store", os.path.join(d, "s.json"),
                        "--result", os.path.join(d, "r.json")])
        check("★★ record 0건이면 persist 하지 않는다 (non-zero)", rc != 0, str(rc))
        check("  그 경우 store 파일도 만들지 않는다",
              not os.path.exists(os.path.join(d, "s.json")))

    # ③ pre-series → store reject
    pre_em = copy.deepcopy(EM26)
    pre_em["records"] = [copy.deepcopy(EM26["records"][0])]
    pre_em["records"][0]["economic_period_end"] = "2025-06-30"
    st, res = PERS.persist(pre_em, ST.empty_state())
    check("★★ pre-series → PRE_SERIES_BACKFILL_FORBIDDEN",
          res["outcome_counts"].get(ST.REJECTED_PRE_SERIES) == 1, str(res["outcome_counts"]))
    check("★★ series 가 비어 있다", st["series"] == {}, str(st["series"]))
    check("★ 거부 증거가 남는다", len(st["rejections"]) == 1)

    # ④ conflict → blocked · non-consumable · unresolved
    conf_em = copy.deepcopy(EM26)
    conf_em["records"] = [copy.deepcopy(EM26["records"][0])]
    conf_em["records"][0]["decision"]["raw_value"] = "52%"
    conf_em["records"][0]["decision"]["numeric_value"] = "52"
    st, res = PERS.persist(conf_em, ST1)
    check("★★ conflict → CONFLICT", res["outcome_counts"].get(ST.CONFLICT) == 1,
          str(res["outcome_counts"]))
    check("★★ conflict → unresolved=True (정상 성공으로 소비 금지)",
          res["unresolved"] is True)
    check("★★ conflict → 그 key 가 blocked", len(res["blocked_keys"]) == 1,
          str(res["blocked_keys"]))
    check("★ conflict → consumable 3 key 만 남는다", len(res["consumable_keys"]) == 3)

    # ⑤ revision → blocked · non-consumable
    rev_em = copy.deepcopy(EM26)
    rev_em["records"] = [copy.deepcopy(EM26["records"][0])]
    rev_em["records"][0]["provenance"]["accession"] = "0001193125-26-777777"
    st, res = PERS.persist(rev_em, ST1)
    check("★★ revision → REVISION", res["outcome_counts"].get(ST.REVISION) == 1,
          str(res["outcome_counts"]))
    check("★★ revision → unresolved=True", res["unresolved"] is True)
    check("★ revision → blocked 사유가 REVISION_AUTHORITY_UNRESOLVED",
          ST.REVISION_AUTHORITY_UNRESOLVED in list(res["blocked_keys"].values())[0],
          str(res["blocked_keys"]))

    # ⑥ observe 실패 → store state 무변경
    em25 = OBSV.observe_fixture(MAN25)
    check("★★ FY25 관측은 record 0건이다", em25["observed"] == 0, str(em25["observed"]))
    check("★★ FY25 실패 사유가 ROW_ABSENT 다",
          all(f["outcome"] == "ROW_ABSENT" for f in em25["failures"]),
          str([f["outcome"] for f in em25["failures"]]))
    before = ST.serialize(ST1)
    st, res = PERS.persist(em25, ST1)
    check("★★ observe 실패분은 store state 를 바꾸지 않는다", ST.serialize(st) == before)
    check("  emitted_records 0 · outcome 0", res["emitted_records"] == 0
          and res["outcomes"] == [])
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "e.json")
        rc = OBSV.main(["--source", "fixture", "--manifest", MAN25, "--out", out])
        check("★★ 관측 0건이면 observe CLI 가 성공으로 끝나지 않는다", rc != 0, str(rc))

# ══════════════════════════════════════════════════════════════════════
with section("D. ★★ workflow 계약 순서"):
    if not need("workflow 파일이 있다", os.path.exists(WF), WF):
        skip("workflow 계약", "파일 없음")
    else:
        wf = open(WF, encoding="utf-8").read()
        ORDER = ["- name: checkout",
                 "- name: observe",
                 "- name: repository-clean guard",
                 "- name: emitted record validation",
                 "- name: persist",
                 "- name: resulting store validation",
                 "- name: artifact upload"]
        pos = [wf.find(s) for s in ORDER]
        for s, p in zip(ORDER, pos):
            check(f"★ workflow 에 `{s.replace('- name: ', '')}` 단계가 있다", p != -1)
        check("★★ 계약 순서가 지켜진다 (checkout → … → artifact upload)",
              pos == sorted(pos) and -1 not in pos, str(pos))
        check("★★ observe 직후에 repository-clean guard 가 온다",
              wf.find("- name: repository-clean guard") > wf.find("- name: observe")
              and wf.find("- name: repository-clean guard")
              < wf.find("- name: emitted record validation"))
        check("★★ guard 가 `git diff --exit-code` 를 쓴다", "git diff --exit-code" in wf)
        check("★★ persist 는 record validation **뒤**에 온다",
              wf.find("- name: persist") > wf.find("- name: emitted record validation"))
        check("★ schedule 트리거가 없다 (자동 발동 금지)", "schedule:" not in wf)
        check("★ workflow_dispatch 전용이다", "workflow_dispatch:" in wf)
        check("★★ S4A 는 fixture source 만 노출한다",
              re.search(r"options:\s*\[fixture\]", wf) is not None)
        check("★ live 옵션이 노출되지 않는다", "options: [fixture, live]" not in wf)
        check("★ permissions 가 contents: read 다", "contents: read" in wf)
        check("★ artifact upload 가 있다", "upload-artifact" in wf)
        check("★★ observe 가 저장소 밖(runner.temp)으로 emit 한다",
              "runner.temp" in wf)
        check("★ 이 workflow 가 commit/push 를 하지 않는다",
              "git commit" not in wf and "git push" not in wf)

with section("D-2. canonical store 영속 경로 제안"):
    check("★ 제안 경로가 상수로 선언돼 있다",
          PERS.PROPOSED_STORE_PATH
          == "observations/MSFT/commercial-remaining-performance-obligation.json",
          PERS.PROPOSED_STORE_PATH)
    check("★★ 제안 경로가 run-date snapshot 모델(`data/<날짜>/`)과 분리된다",
          not PERS.PROPOSED_STORE_PATH.startswith("data/"))
    check("★★ 이번 단계에서 그 경로에 실제로 commit 하지 않았다",
          not os.path.exists(os.path.join(ROOT, PERS.PROPOSED_STORE_PATH)))
    check("★ common.save() 모델을 고쳐 끼워 넣지 않았다",
          "common" not in _imports(PERS_SRC) and "common" not in _imports(OBSV_SRC))

sys.exit(K.exit_code())
