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
import subprocess
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
    check("★ live 경로는 acquisition primitive 만 쓴다 (S4B wiring)",
          "msft_sec_results_acquisition" in oi)

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
        # ★ S4B-LIVE-WIRING 으로 live option 이 노출됐다. 그러나 **노출 ≠ 실행 승인**이다 —
        #   dispatch 는 별도 CIO 승인 1회이며, 그 계약은 E-5 절이 본다.
        #
        # ★★ H1 (CIO 확정 2026-08-16) — 이 두 검사의 **계약이 바뀌었다**.
        #    이전 계약: `options: [fixture, live]` inline flow · `default: fixture`
        #    새 계약  : 첫 option 을 무효 sentinel 로 두어 **명시 선택 없이는 실행되지 않는다.**
        #    ⛔ 약화가 아니라 방향 반전이다 — `default: fixture` 는 이제 **금지**다.
        #    ⛔ 또한 `options: [...]` 형태에 정규식을 걸지 않는다. YAML 표기 형식이
        #       바뀌었다고 계약 검사가 깨지는 것은 검사가 형식에 붙어 있었다는 뜻이다.
        src_block = wf.split("source:", 1)[1].split("capture_max_filings:", 1)[0]
        check("★ source 선택지에 fixture 와 live 가 모두 노출된다",
              "fixture" in src_block and "live" in src_block, src_block)
        check("★★ H1 · default 가 fixture 가 아니다 — 조용히 연습 데이터로 내려가지 않는다",
              re.search(r"default:\s*fixture\s*$", src_block, re.M) is None, src_block)
        check("★★ H1 · source 입력이 required 다",
              re.search(r"required:\s*true", src_block) is not None, src_block)
        check("★ permissions 가 contents: read 다", "contents: read" in wf)
        check("★ artifact upload 가 있다", "upload-artifact" in wf)
        check("★★ observe 가 저장소 밖(runner.temp)으로 emit 한다",
              "runner.temp" in wf)
        check("★ 이 workflow 가 commit/push 를 하지 않는다",
              "git commit" not in wf and "git push" not in wf)

with section("D-3. ★★ repository-clean guard 를 **실제로 실행**한다 "
             "(S4A.1 · REPOSITORY_CLEAN_GUARD_UNTRACKED_GAP)"):
    # ★ workflow 의 guard 를 문자열로만 대조하지 않는다 — YAML 에서 그대로 뽑아
    #   진짜 git 저장소에서 돌려 exit code 를 본다.
    #   ⛔ 「`git status` 를 출력한다」와 「`git status` 로 판정한다」는 다르다.
    wf_text = open(WF, encoding="utf-8").read()

    def guard_scripts(text):
        """`repository-clean guard` 단계들의 run 블록을 그대로 뽑는다."""
        out = []
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            if ln.strip().startswith("- name: repository-clean guard"):
                j = i + 1
                while j < len(lines) and not lines[j].strip().startswith("run:"):
                    j += 1
                if j >= len(lines):
                    continue
                body, k = [], j + 1
                indent = len(lines[j]) - len(lines[j].lstrip())
                while k < len(lines):
                    cur = lines[k]
                    if cur.strip() and (len(cur) - len(cur.lstrip())) <= indent:
                        break
                    body.append(cur[indent + 2:] if len(cur) > indent + 2 else "")
                    k += 1
                out.append("\n".join(body).rstrip() + "\n")
        return out

    scripts = guard_scripts(wf_text)
    if not need("guard 단계 2개를 추출했다", len(scripts) == 2, str(len(scripts))):
        skip("guard 실행 검증", "추출 실패")
    else:
        def executable_lines(script):
            """주석·빈 줄을 제외한 **실행되는 줄**만 남긴다."""
            return [ln.strip() for ln in script.splitlines()
                    if ln.strip() and not ln.strip().startswith("#")]
        check("★★ 두 guard 의 실행 계약이 동일하다 (observe 직후 · persist 직후)",
              executable_lines(scripts[0]) == executable_lines(scripts[1]),
              str(executable_lines(scripts[0]))[:80])
        for gi, sc in enumerate(scripts):
            ex = executable_lines(sc)
            check(f"★ guard[{gi}] 가 `git diff --exit-code` 를 쓴다",
                  any("git diff --exit-code" in x for x in ex))
            check(f"★★ guard[{gi}] 가 `git status --porcelain` 을 **판정에** 쓴다",
                  any("git status --porcelain" in x and "=" in x for x in ex),
                  str(ex)[:80])
            check(f"★★ guard[{gi}] 에 명시적 `exit 1` 이 있다",
                  any(x == "exit 1" for x in ex), str(ex)[:80])

        def run_guard(script, setup):
            """빈 임시 git 저장소를 만들고 setup 을 적용한 뒤 guard 를 돌린다."""
            with tempfile.TemporaryDirectory() as d:
                env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
                def git(*a):
                    return subprocess.run(["git", *a], cwd=d, env=env,
                                          capture_output=True, text=True)
                git("init", "-q")
                open(os.path.join(d, "tracked.txt"), "w").write("original\n")
                git("add", "-A")
                git("commit", "-qm", "init")
                setup(d)
                sp = os.path.join(d, "_guard.sh")
                open(sp, "w").write(script)
                # ⛔ guard 스크립트 파일 자체가 untracked 로 잡히지 않도록 저장소 밖에 둔다
                outside = os.path.join(tempfile.mkdtemp(), "guard.sh")
                os.replace(sp, outside)
                r = subprocess.run(["bash", outside], cwd=d, env=env,
                                   capture_output=True, text=True)
                return r.returncode, (r.stdout + r.stderr)

        CASES = [
            ("clean repository", lambda d: None, 0),
            ("tracked modification",
             lambda d: open(os.path.join(d, "tracked.txt"), "w").write("changed\n"), 1),
            ("untracked file",
             lambda d: open(os.path.join(d, "stray.json"), "w").write("{}\n"), 1),
            ("untracked 디렉터리",
             lambda d: (os.makedirs(os.path.join(d, "observations")),
                        open(os.path.join(d, "observations", "x.json"), "w").write("{}")), 1),
            ("staged 신규 파일",
             lambda d: (open(os.path.join(d, "new.txt"), "w").write("x"),
                        subprocess.run(["git", "add", "new.txt"], cwd=d,
                                       capture_output=True)), 1),
        ]
        for gi, script in enumerate(scripts):
            tag = "observe 직후" if gi == 0 else "persist 직후"
            for name, setup, want in CASES:
                rc, out = run_guard(script, setup)
                ok = (rc == 0) if want == 0 else (rc != 0)
                check(f"★★ [{tag}] {name} → guard {'PASS' if want == 0 else 'FAIL'}",
                      ok, f"rc={rc} {out.strip()[:80]}")

        # ★ 실제 observe / persist 를 fixture 로 돌린 뒤 저장소 state 가 그대로인지 본다.
        #   ⛔ 「저장소가 clean 이다」로 단언하지 않는다 — 개발 중 로컬 변경이 있으면
        #      계약과 무관하게 실패한다. **실행 전후가 같은가**가 계약이다.
        BEFORE_STATUS = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                       capture_output=True, text=True).stdout
        for stage in ("observe", "persist"):
            with tempfile.TemporaryDirectory() as work:
                emit = os.path.join(work, "emission.json")
                rc = OBSV.main(["--source", "fixture", "--manifest", MAN26, "--out", emit])
                need(f"{stage} 준비 — observe 성공", rc == 0, str(rc))
                if stage == "persist":
                    rc = PERS.main(["--emission", emit,
                                    "--store", os.path.join(work, "store.json"),
                                    "--result", os.path.join(work, "result.json")])
                    need("persist 성공", rc == 0, str(rc))
                after = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                       capture_output=True, text=True).stdout
                check(f"★★ 실제 {stage} fixture run 이 저장소 state 를 바꾸지 않는다",
                      after == BEFORE_STATUS,
                      "변경된 항목: "
                      + str(sorted(set(after.splitlines()) ^ set(BEFORE_STATUS.splitlines())))[:120])

# ══════════════════════════════════════════════════════════════════════
# E. S4B-LIVE-WIRING — network 없이 live 경로를 검증한다
#    ★ `observe_live(limit, fetch=...)` 의 주입 이음매에 mock 을 넣는다.
#    ⛔ 이 이음매는 fallback 이 아니다 — 실패해도 fixture 로 내려가지 않는다.
# ══════════════════════════════════════════════════════════════════════
CAP = json.load(open(MAN26, encoding="utf-8"))["captured"]
CAP_SORTED = sorted(CAP, key=lambda x: x["filing_date"], reverse=True)


def _sub_json(entries):
    return json.dumps({"filings": {"recent": {
        "form": [e.get("form", "8-K") for e in entries],
        "items": [e.get("items", "2.02,9.01") for e in entries],
        "accessionNumber": [e["accession"] for e in entries],
        "filingDate": [e["filing_date"] for e in entries],
        "reportDate": ["" for _ in entries],
        "acceptanceDateTime": ["" for _ in entries]}}}).encode()


def _txt(exhibit="msft-ex99_1.htm", type_="EX-99.1"):
    return (f"<DOCUMENT>\n<TYPE>8-K\n<SEQUENCE>1\n<FILENAME>c.htm\n<TEXT>x\n</DOCUMENT>\n"
            f"<DOCUMENT>\n<TYPE>{type_}\n<SEQUENCE>2\n<FILENAME>{exhibit}\n<TEXT>y\n"
            f"</DOCUMENT>\n").encode()


def _index(exhibit="msft-ex99_1.htm", type_="EX-99.1"):
    return (f"<table><tr><td>2</td><td>EX</td>"
            f'<td><a href="/x/{exhibit}">{exhibit}</a></td><td>{type_}</td></tr></table>'
            ).encode()


def make_fetch(entries, *, exhibit="msft-ex99_1.htm", type_="EX-99.1",
               sec_type=None, sub_payload=None, fail_on=None, calls=None):
    """URL → (meta, bytes) mock. ⛔ 네트워크를 쓰지 않는다."""
    body = {}
    for e in entries:
        acc = e["accession"].replace("-", "")
        html = open(os.path.join(FX_DIR, e["fixture_file"]), encoding="utf-8").read()
        body[f"/{acc}/{e['accession']}.txt"] = _txt(exhibit, type_)
        body[f"/{acc}/{e['accession']}-index.html"] = _index(
            exhibit, sec_type or type_)
        body[f"/{acc}/{exhibit}"] = html.encode()

    def fetch(url):
        if calls is not None:
            calls.append(url)
        if fail_on and fail_on in url:
            raise OSError("mocked network failure")
        if url == OBSV.ACQ.SUBMISSIONS_URL:
            return {}, (sub_payload if sub_payload is not None else _sub_json(entries))
        for suffix, b in body.items():
            if url.endswith(suffix):
                return {}, b
        raise OSError(f"mocked 404: {url}")
    return fetch


with section("E. ★★ live 경로 — mocked payload 로 record 4건"):
    calls = []
    em = OBSV.observe_live(4, fetch=make_fetch(CAP_SORTED, calls=calls))
    check("★★ source 가 live 로 기록된다", em["source"] == "live", em["source"])
    check("★★ FY26 형태 mocked payload → record 4건", em["observed"] == 4,
          str(em["observed"]))
    check("★ 실패 0건", em["failed"] == 0, str(em["failures"])[:100])
    for r in em["records"]:
        RC.validate_record(r)
    check("★★ live record 4건이 전부 record 계약을 통과한다", True)
    vals = sorted(r["decision"]["raw_value"] for r in em["records"])
    check("★★ live 관측값이 fixture 경로와 같다",
          vals == sorted(x["decision"]["raw_value"] for x in EM26["records"]), str(vals))
    check("★ meta 에 limit 이 기록된다", em["meta"]["limit"] == 4, str(em["meta"]))
    check("★★ discovery 가 submissions 를 실제로 조회했다",
          any(u == OBSV.ACQ.SUBMISSIONS_URL for u in calls))
    check("★★ exhibit 취득이 primary(.txt) · secondary(index) · 본문 3단을 거친다",
          any(u.endswith(".txt") for u in calls)
          and any(u.endswith("-index.html") for u in calls)
          and any(u.endswith("msft-ex99_1.htm") for u in calls), str(len(calls)))
    check("★ live 경로도 Store 를 건드리지 않는다 (emission 만 만든다)",
          set(em) == set(EM26))

    # ── ★★ EX-99.1 primary / secondary identity 성공 증거 (CIO 판정 ②) ──
    #    ⛔ 「실패하지 않았음」으로 추론하지 않는다 — 성공 자체가 관측 가능해야 한다.
    for r in em["records"]:
        ev = (r["provenance"] or {}).get("identity_evidence")
        acc = r["provenance"]["accession"]
        if not need(f"★★ {acc} 에 identity evidence 가 있다", ev is not None):
            continue
        check(f"★★ {acc} primary 선택 결과가 기록된다",
              ev["primary_document"] == "msft-ex99_1.htm", str(ev["primary_document"]))
        check(f"  {acc} primary 선택 방식이 기록된다",
              ev["primary_selection"] == "full_submission_sgml_type_exact_match")
        check(f"★★ {acc} secondary cross-check 결과가 기록된다",
              ev["secondary_document"] == "msft-ex99_1.htm", str(ev["secondary_document"]))
        check(f"  {acc} secondary 가 본 type 이 기록된다",
              ev["secondary_type"] == "EX-99.1", str(ev["secondary_type"]))
        check(f"★★ {acc} primary 와 secondary 가 동일 document 임이 명시된다",
              ev["cross_check"] == "AGREE" and ev["primary_document"] == ev["secondary_document"],
              str(ev["cross_check"]))
    check("★★ 모든 live record 가 identity evidence 를 갖는다",
          all((r["provenance"] or {}).get("identity_evidence") for r in em["records"]))
    check("★ 기존 exhibit_identity provenance 도 그대로 유지된다",
          all(r["provenance"]["exhibit_identity"]["document"] == "msft-ex99_1.htm"
              and r["provenance"]["exhibit_identity"]["type"] == "EX-99.1"
              for r in em["records"]))
    check("★★ identity evidence 가 `exhibit_identity` 안이 아니라 형제 키다 "
          "(store material provenance 축 불변)",
          all("identity_evidence" not in r["provenance"]["exhibit_identity"]
              for r in em["records"]))
    # ⛔ 관측성 보강이 store 의미를 바꾸지 않았는지 — material provenance 축 대조
    mp_live = ST.material_provenance(em["records"][0])
    check("★★ identity evidence 가 material provenance 에 섞이지 않는다",
          "identity_evidence" not in mp_live and "cross_check" not in mp_live,
          str(sorted(mp_live)))

    # ── live limit 전달 ────────────────────────────────────────────────
    em2 = OBSV.observe_live(2, fetch=make_fetch(CAP_SORTED))
    check("★★ limit 이 discovery 에 전달된다 (2건만 관측)", em2["observed"] == 2,
          str(em2["observed"]))
    check("  상한으로 제외된 건수가 기록된다", em2["meta"]["dropped_by_limit"] == 2,
          str(em2["meta"]))

with section("E-2. ★★ fixture source 는 network 함수를 한 번도 부르지 않는다"):
    calls = []
    orig_get = OBSV.ACQ.get
    OBSV.ACQ.get = lambda url, **kw: (_ for _ in ()).throw(
        AssertionError(f"fixture 경로가 network 를 호출했다: {url}"))
    try:
        em_fx = OBSV.observe_fixture(MAN26)
        check("★★ fixture 관측이 network 호출 없이 완료된다", em_fx["observed"] == 4,
              str(em_fx["observed"]))
    except AssertionError as e:
        check("★★ fixture 관측이 network 호출 없이 완료된다", False, str(e))
    finally:
        OBSV.ACQ.get = orig_get
    check("★ fixture emission 의 source 가 live 가 아니다", em_fx["source"] == "fixture")

with section("E-3. ★★ live fail-closed 매트릭스"):
    FAULTS = [
        ("SEC 접근 실패(submissions)",
         lambda: OBSV.observe_live(4, fetch=make_fetch(CAP_SORTED,
                                                       fail_on="submissions"))),
        ("SEC 접근 실패(exhibit)",
         lambda: OBSV.observe_live(4, fetch=make_fetch(CAP_SORTED, fail_on=".txt"))),
        ("submissions schema 이상",
         lambda: OBSV.observe_live(4, fetch=make_fetch(CAP_SORTED,
                                                       sub_payload=b'{"nope":1}'))),
        ("submissions 가 JSON 이 아님",
         lambda: OBSV.observe_live(4, fetch=make_fetch(CAP_SORTED, sub_payload=b"<html>"))),
        ("후보 0건 (form 이 8-K 아님)",
         lambda: OBSV.observe_live(4, fetch=make_fetch(
             [dict(e, form="10-Q") for e in CAP_SORTED]))),
        ("후보 0건 (item 2.02 없음)",
         lambda: OBSV.observe_live(4, fetch=make_fetch(
             [dict(e, items="5.02") for e in CAP_SORTED]))),
        ("EX-99.1 identity 실패 (type 불일치)",
         lambda: OBSV.observe_live(4, fetch=make_fetch(CAP_SORTED, type_="EX-99.2"))),
        # ★ primary 는 통과하는데 secondary index 가 다른 경우 — 교차확인이 유일한 관문이다
        ("secondary 교차확인 불일치",
         lambda: OBSV.observe_live(4, fetch=make_fetch(CAP_SORTED, sec_type="EX-99.2"))),
    ]
    # ★ 「어쨌든 ObserveError 면 됐다」로 두지 않는다 — 뒤쪽 관문이 대신 잡아주면
    #   앞쪽 관문이 사라진 것을 알 수 없다. **실패 사유 문면**까지 못 박는다.
    REASON = {
        "SEC 접근 실패(submissions)": "submissions 조회 실패",
        "SEC 접근 실패(exhibit)": "exhibit 취득 실패",
        "submissions schema 이상": "submissions schema 이상",
        "submissions 가 JSON 이 아님": "submissions 조회 실패",
        "후보 0건 (form 이 8-K 아님)": "후보 0건",
        "후보 0건 (item 2.02 없음)": "후보 0건",
        "EX-99.1 identity 실패 (type 불일치)": "primary exhibit identity 실패",
        "secondary 교차확인 불일치": "secondary 교차확인 실패",
    }
    for why, fn in FAULTS:
        try:
            got = fn()
            check(f"★★ {why} → ObserveError (fail-closed)", False,
                  f"통과해버렸다 observed={got['observed']}")
        except OBSV.ObserveError as e:
            check(f"★★ {why} → ObserveError (fail-closed)", True)
            check(f"  {why} → 사유가 정확하다", REASON[why] in str(e), str(e)[:90])

    # ★★ secondary 가 **문면 그대로** 기록되는지 — 상수를 베껴 적으면 증거가 아니라 주장이다.
    #    ★ `select_exhibit` 은 대소문자·공백을 정규화해 비교하므로 `ex-99.1` 도 통과한다.
    #      그때 evidence 는 index 가 **실제로 적은 문면**을 담아야 한다.
    em_lower = OBSV.observe_live(4, fetch=make_fetch(CAP_SORTED, sec_type="ex-99.1"))
    check("★ 소문자 secondary type 도 교차확인을 통과한다 (정규화 비교)",
          em_lower["observed"] == 4, str(em_lower["observed"]))
    check("★★ secondary_type 이 index 원문(`ex-99.1`)을 그대로 담는다 — 상수 복사가 아니다",
          all(r["provenance"]["identity_evidence"]["secondary_type"] == "ex-99.1"
              for r in em_lower["records"]),
          str([r["provenance"]["identity_evidence"]["secondary_type"]
               for r in em_lower["records"]][:2]))

    # ★★ primary 와 secondary 가 다른 document 를 가리키면 여전히 fail-closed 다.
    #    ⛔ 관측성 보강이 fail-closed 조건을 무르게 하지 않았는지 확인한다.
    try:
        OBSV.observe_live(4, fetch=make_fetch(CAP_SORTED, sec_type="EX-99.2"))
        check("★★ identity 불일치는 여전히 fail-closed (증거만 남기고 통과하지 않는다)",
              False, "통과해버렸다")
    except OBSV.ObserveError as e:
        check("★★ identity 불일치는 여전히 fail-closed (증거만 남기고 통과하지 않는다)",
              "secondary 교차확인 실패" in str(e), str(e)[:90])

    # ★ fetch 를 주입하지 않았을 때 fixture 로 조용히 내려가지 않는다.
    #   ⛔ network 를 쓰지 않기 위해 acquisition 의 `get` 을 실패시키고 결과를 본다.
    orig_get = OBSV.ACQ.get
    OBSV.ACQ.get = lambda url, **kw: (_ for _ in ()).throw(OSError("no network"))
    try:
        got = OBSV.observe_live(4)
        check("★★ fetch 미주입 + network 불가 → fixture 로 내려가지 않는다", False,
              f"emission 이 만들어졌다 source={got['source']} observed={got['observed']}")
    except OBSV.ObserveError as e:
        check("★★ fetch 미주입 + network 불가 → fixture 로 내려가지 않는다 (fail-closed)",
              "submissions 조회 실패" in str(e), str(e)[:90])
    finally:
        OBSV.ACQ.get = orig_get

    # CLI 수준 — non-zero + emission 파일 없음
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "e.json")
        orig = OBSV.observe_live
        OBSV.observe_live = lambda limit, fetch=None: (_ for _ in ()).throw(
            OBSV.ObserveError("mocked live failure"))
        try:
            rc = OBSV.main(["--source", "live", "--limit", "4", "--out", out])
        finally:
            OBSV.observe_live = orig
        check("★★ live 실패 → CLI non-zero", rc != 0, str(rc))
        check("★★ live 실패 → emission 파일이 만들어지지 않는다", not os.path.exists(out))
        # persist 는 존재하지 않는 emission 을 읽지 못한다 → 진입 자체가 막힌다
        rc2 = PERS.main(["--emission", out, "--store", os.path.join(d, "s.json"),
                         "--result", os.path.join(d, "r.json")])
        check("★★ observe 실패 후 persist 가 진입하지 못한다", rc2 != 0, str(rc2))
        check("  store 파일도 만들어지지 않는다", not os.path.exists(os.path.join(d, "s.json")))

with section("E-4. ★★ D-5 처리 — pre-series 는 observation 을 만들지 않는다"):
    check("★★ observe 의 series start 가 store 의 상수와 같다",
          OBSV.COMMERCIAL_RPO_SERIES_START == ST.COMMERCIAL_RPO_SERIES_START,
          f"{OBSV.COMMERCIAL_RPO_SERIES_START} vs {ST.COMMERCIAL_RPO_SERIES_START}")
    check("★ observe 가 store 를 import 하지 않고도 같은 경계를 갖는다",
          "store" not in _imports(OBSV_SRC))

    # ① row absence 만 있는 과거 filing (FY25) — 성공 observation 이 아니다
    cap25 = sorted(json.load(open(MAN25, encoding="utf-8"))["captured"],
                   key=lambda x: x["filing_date"], reverse=True)
    em25 = OBSV.observe_live(4, fetch=make_fetch(cap25))
    check("★★ FY25 filing 만 있으면 record 0건이다", em25["observed"] == 0,
          str(em25["observed"]))
    check("★★ 그 실패는 ROW_ABSENT 다 (오류가 아니다)",
          all(f["outcome"] == "ROW_ABSENT" for f in em25["failures"]),
          str([f["outcome"] for f in em25["failures"]]))
    check("★ ObserveError 로 죽지 않는다 — 행 부재는 정상 관측 결과다", em25["failed"] == 4)

    # ② FY25 + FY26 이 섞여도 FY26 만 emit 된다
    em_mix = OBSV.observe_live(8, fetch=make_fetch(CAP_SORTED + cap25))
    check("★★ FY25+FY26 혼합 discovery → FY26 4건만 emit", em_mix["observed"] == 4,
          str(em_mix["observed"]))
    check("  나머지 4건은 실패 목록에 남는다", em_mix["failed"] == 4, str(em_mix["failed"]))
    check("★★ emit 된 record 는 전부 series start 이후다",
          all(r["economic_period_end"] >= OBSV.COMMERCIAL_RPO_SERIES_START
              for r in em_mix["records"]))

    # ③ ★ emit 필터를 직접 검증한다 — 실제 fixture 로는 pre-series record 가
    #      만들어질 수 없으므로(D-6: 행 자체가 없다) 관측 단계를 대체해 필터만 본다.
    pre_rec = copy.deepcopy(EM26["records"][0])
    pre_rec["economic_period_end"] = "2025-06-30"
    orig_obs = OBSV._observe_one
    OBSV._observe_one = lambda html, prov: (copy.deepcopy(pre_rec), None)
    try:
        em_pre = OBSV.observe_live(4, fetch=make_fetch(CAP_SORTED))
    finally:
        OBSV._observe_one = orig_obs
    check("★★ pre-series record 는 emit 되지 않는다", em_pre["observed"] == 0,
          str(em_pre["observed"]))
    check("★★ 그 사유가 PRE_SERIES_NOT_EMITTED 다",
          all(f["outcome"] == OBSV.PRE_SERIES_NOT_EMITTED for f in em_pre["failures"]),
          str([f["outcome"] for f in em_pre["failures"]]))
    check("  사유에 series start 가 명시된다",
          all(OBSV.COMMERCIAL_RPO_SERIES_START in f["problems"][0]
              for f in em_pre["failures"]))

    # ④ pre-series period 를 가진 record 는 emit 대상이 아니다
    fake = copy.deepcopy(EM26["records"][0])
    fake["economic_period_end"] = "2025-06-30"
    check("★★ `_pre_series` 가 series start 이전을 True 로 판정한다",
          OBSV._pre_series(fake) is True)
    check("  경계값 자체는 pre-series 가 아니다",
          OBSV._pre_series(EM26["records"][0]) is False)
    check("★ PRE_SERIES_NOT_EMITTED 코드가 선언돼 있다",
          OBSV.PRE_SERIES_NOT_EMITTED == "PRE_SERIES_NOT_EMITTED")

with section("E-5. workflow live input 계약"):
    wf2 = open(WF, encoding="utf-8").read()
    # ★★ H1 — 위 D 절과 같은 이유로 표기 형식이 아니라 계약을 본다.
    src2 = wf2.split("source:", 1)[1].split("capture_max_filings:", 1)[0]
    check("★★ source 에 fixture · live 두 값이 노출된다",
          "fixture" in src2 and "live" in src2, src2)
    check("★★ H1 · 그러나 어느 쪽도 기본값으로 내려가지 않는다",
          re.search(r"default:\s*(fixture|live)\s*$", src2, re.M) is None, src2)
    check("★★ capture_max_filings input 이 있다", "capture_max_filings:" in wf2)
    check("★★ 실제 전달값을 로그로 남긴다",
          "echo \"SOURCE=$SOURCE\"" in wf2 and "echo \"CAPTURE_MAX_FILINGS=" in wf2)
    check("★★ live 는 `--limit` 을 전달한다", "--limit \"$CAPTURE_MAX_FILINGS\"" in wf2)
    check("★★ live 분기는 manifest 를 쓰지 않는다 (fallback 없음)",
          "--source live" in wf2
          and wf2.split("--source live")[1].split("fi")[0].find("--manifest") == -1)
    check("★ schedule 트리거는 여전히 없다", "schedule:" not in wf2)
    check("★ 여전히 commit/push 를 하지 않는다",
          "git commit" not in wf2 and "git push" not in wf2)
    check("★ permissions 는 contents: read 다", "contents: read" in wf2)

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
