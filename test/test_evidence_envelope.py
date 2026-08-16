#!/usr/bin/env python3
"""Observation → Decision 입력 자격 확인 계층 회귀 (WS3 · 2026-08-16).

★ 이 회귀가 증명하는 것
   ① envelope 은 available / blocked / unresolved 세 상태를 **구별한다**
   ② 관측 부재를 0 이나 통과로 바꾸지 않는다
   ③ CONFLICT · REVISION 미해소를 전달 자격으로 승격시키지 않는다
   ④ 출처 축이 불완전하면 전달 자격이 없다
   ⑤ **투자 판정 어휘를 산출하지 않는다** — 소스에도 출력에도 없다
   ⑥ 순수 함수다 — 입력 state 를 변형하지 않는다
   ⑦ Git · 네트워크 · Notion · evaluator 를 모른다

★ 이 회귀가 증명하지 못하는 것
   RULE-0022 의 값이 옳은지 · 그 값이 투자적으로 무엇을 뜻하는지.
   그것은 이 층의 질문이 아니다.

⛔ 네트워크를 쓰지 않는다. fixture only.
"""
from __future__ import annotations

import ast
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
sys.path.insert(0, os.path.join(ROOT, "observation"))
sys.path.insert(0, os.path.join(ROOT, "bridge"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store as ST                                                  # noqa: E402
import record as RC                                                 # noqa: E402
import rule0022_commercial_rpo as OBS                                # noqa: E402
import msft_sec_results_acquisition as ACQ                           # noqa: E402
import evidence_envelope as EV                                       # noqa: E402

import checkkit as K                                                # noqa: E402
K.init(quiet=True)
check, need, section = K.check, K.need, K.section

FX_DIR = os.path.join(ROOT, "collectors", "fixtures")
BRIDGE_SRC = os.path.join(ROOT, "bridge", "evidence_envelope.py")

# ── 판정 엔진 어휘 — 이 층이 만들어서는 안 되는 것 ─────────────────────
#   ★ 정확히 일치하는 리터럴만 본다. `THRESHOLD` 안의 `HOLD` 같은 부분일치로
#     오탐하지 않기 위해서다 (언급과 산출은 다르다).
BANNED_VERDICTS = {"BUY", "SELL", "HOLD", "REDUCE", "ADD", "TRIM",
                   "OVERWEIGHT", "UNDERWEIGHT", "STRONG BUY", "STRONG SELL",
                   "매수", "매도", "보유", "축소"}
BANNED_KEYS = {"action", "recommendation", "verdict", "rating", "signal",
               "order", "target_price", "price_target", "position_size",
               "weight", "conviction"}


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
        r, _ = RC.try_build(d)
        if r is not None:
            out.append(r)
    return out


def state_with(records):
    st = ST.empty_state()
    for r in records:
        st, _ = ST.apply_record(st, r)
    return st


def strings_in(obj):
    """중첩 구조 안의 모든 문자열 값."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from strings_in(k)
            yield from strings_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from strings_in(v)


def keys_in(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from keys_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from keys_in(v)


FY26 = records_from("azure_cc_MANIFEST.json")
KEY0 = ("MSFT", "Commercial remaining performance obligation", "2025-09-30")
KEY_ABSENT = ("MSFT", "Commercial remaining performance obligation", "2026-09-30")

# ══════════════════════════════════════════════════════════════════════
with section("A. 층 경계 — bridge 는 네트워크 · Git · Notion · evaluator 를 모른다"):
    tree = ast.parse(open(BRIDGE_SRC, encoding="utf-8").read())
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    for banned in ("requests", "urllib", "http", "socket", "git", "subprocess",
                   "notion", "smtplib"):
        check(f"⛔ `{banned}` 를 import 하지 않는다", banned not in imported,
              str(sorted(imported)))
    check("★ evaluator 를 import 하지 않는다",
          not any("eval" in m for m in imported), str(sorted(imported)))
    check("★ store 만 통해 관측을 읽는다", "store" in imported, str(sorted(imported)))

with section("B. ★★ 판정 어휘를 산출하지 않는다 — 소스 리터럴"):
    docstrings = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ds = ast.get_docstring(n, clean=False)
            if ds:
                docstrings.add(ds)
    literals = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    literals -= docstrings
    offend = sorted(s for s in literals if s.strip().upper() in BANNED_VERDICTS)
    check("★★ 판정 어휘와 정확히 일치하는 문자열 리터럴이 없다", not offend, str(offend))
    bad_keys = sorted(s for s in literals if s in BANNED_KEYS)
    check("★★ 판정용 키 이름을 만들지 않는다", not bad_keys, str(bad_keys))
    check("★ 그럼에도 금지 사실은 문서화돼 있다 — 언급과 산출은 다르다",
          any("⛔" in d for d in docstrings))

with section("C. ★★ EVIDENCE_AVAILABLE — 정상 series"):
    st = state_with(FY26)
    env = EV.envelope_for(st, KEY0)
    check("status = EVIDENCE_AVAILABLE", env["status"] == EV.EVIDENCE_AVAILABLE,
          env["status"])
    check("schema_version 이 붙는다",
          env["schema_version"] == EV.EVIDENCE_ENVELOPE_SCHEMA_VERSION)
    for axis in EV.REQUIRED_AXES:
        check(f"  최소 확인축 `{axis}` 가 있다", axis in env, str(sorted(env)))
    check("★ consumable = true", env["consumable"] is True)
    check("★ blocked_by 가 비어 있다", env["blocked_by"] == [])
    check("★ acquisition provenance 존재가 확인된다",
          env["acquisition_provenance_present"] is True)
    for f in EV.SOURCE_IDENTITY_FIELDS:
        check(f"  source identity 축 `{f}` 가 채워진다",
              bool(env["source_identity"].get(f)), str(env["source_identity"]))
    check("★★ slice_sha256 은 source identity 축이 아니다",
          "slice_sha256" not in env["source_identity"])
    obs = env["observation"]
    check("★ 관측값이 원문 표기 그대로다", obs["raw_value"] == "51%", str(obs["raw_value"]))
    check("★ 정규화값이 문자열 Decimal 이다", obs["numeric_value"] == "51",
          repr(obs["numeric_value"]))
    check("★ Decision column identity 가 실린다",
          "GAAP" in (obs["decision_column_identity"] or ""),
          str(obs["decision_column_identity"]))
    check("⛔ 해석 문장이 붙지 않는다", "interpretation" not in env and "comment" not in env)

with section("C-2. ★★ 출력 어디에도 판정 어휘가 없다"):
    st = state_with(FY26)
    envs = EV.envelopes_from_state(st)
    check("4개 period 전부 envelope 이 나온다", len(envs) == 4, str(len(envs)))
    vals = [s for s in strings_in(envs)]
    offend = sorted({s for s in vals if s.strip().upper() in BANNED_VERDICTS})
    check("★★ 출력 문자열 중 판정 어휘와 일치하는 것이 없다", not offend, str(offend))
    bad = sorted({k for k in keys_in(envs) if k in BANNED_KEYS})
    check("★★ 출력 키 중 판정용 키가 없다", not bad, str(bad))
    check("★ 전부 available 이다",
          all(e["status"] == EV.EVIDENCE_AVAILABLE for e in envs),
          str([e["status"] for e in envs]))

with section("D. ★★ EVIDENCE_UNRESOLVED — 관측이 없다"):
    st = state_with(FY26)
    env = EV.envelope_for(st, KEY_ABSENT)
    check("status = EVIDENCE_UNRESOLVED", env["status"] == EV.EVIDENCE_UNRESOLVED,
          env["status"])
    check("★ 사유가 OBSERVATION_ABSENT", EV.OBSERVATION_ABSENT in env["reasons"],
          str(env["reasons"]))
    check("★★ 값을 0 으로 채우지 않는다", env["observation"] is None)
    check("★★ consumable 이 아니다", env["consumable"] is False)
    check("★ 출처도 비어 있다", env["source_identity"] is None)
    check("★ 빈 store 에서도 동일하다",
          EV.envelope_for(ST.empty_state(), KEY0)["status"] == EV.EVIDENCE_UNRESOLVED)

with section("E. ★★ EVIDENCE_BLOCKED — CONFLICT 미해소"):
    tampered = copy.deepcopy(FY26[0])
    tampered["decision"]["raw_value"] = "77%"
    tampered["decision"]["numeric_value"] = "77"
    st = state_with([FY26[0], tampered])
    env = EV.envelope_for(st, KEY0)
    check("status = EVIDENCE_BLOCKED", env["status"] == EV.EVIDENCE_BLOCKED, env["status"])
    check("★ store 사유를 그대로 전달한다 — 새 이름을 붙이지 않는다",
          ST.OBSERVATION_CONFLICT_UNRESOLVED in env["blocked_by"], str(env["blocked_by"]))
    check("★★ 값을 실어 내보내지 않는다", env["observation"] is None)
    check("★★ deliverable 에 들어가지 않는다", EV.deliverable([env]) == [])

with section("F. ★★ EVIDENCE_BLOCKED — REVISION authority 미해소"):
    other = copy.deepcopy(FY26[0])
    other["provenance"]["source_sha256"] = "f" * 64
    st = state_with([FY26[0], other])
    env = EV.envelope_for(st, KEY0)
    check("status = EVIDENCE_BLOCKED", env["status"] == EV.EVIDENCE_BLOCKED, env["status"])
    check("★ 사유가 REVISION_AUTHORITY_UNRESOLVED",
          ST.REVISION_AUTHORITY_UNRESOLVED in env["blocked_by"], str(env["blocked_by"]))
    check("★★ 두 revision 중 하나를 고르지 않는다", env["observation"] is None)
    check("★★ source identity 도 고르지 않는다", env["source_identity"] is None)

with section("G. 출처 필수 축 결손은 **Store 이전에** 이미 막힌다"):
    # ★ 처음에 나는 accession 을 비운 record 가 store 에 들어간 뒤 bridge 가
    #   막는 시나리오를 상정했는데, 그런 상태는 도달 불가능하다:
    #   `record.REQUIRED_PROVENANCE` 가 accession · filing_date · source_sha256 을
    #   이미 필수로 걸고 있어 `apply_record` 의 첫 동작에서 거부된다.
    #   ⛔ 도달 불가능한 상태를 억지로 만들어 통과시키지 않는다 — 실제 경로를 증명한다.
    holed = copy.deepcopy(FY26[0])
    holed["provenance"]["accession"] = ""
    st, res = ST.apply_record(ST.empty_state(), holed)
    check("★★ accession 결손 record 는 Store 가 거부한다",
          res["outcome"] == ST.REJECTED_INVALID_RECORD, res["outcome"])
    check("★ 따라서 series 에 남지 않는다", st["series"] == {}, str(sorted(st["series"])))
    env = EV.envelope_for(st, KEY0)
    check("★★ bridge 는 그것을 available 로 만들지 않는다",
          env["status"] == EV.EVIDENCE_UNRESOLVED, env["status"])
    check("★ 관측 부재로 보고한다", EV.OBSERVATION_ABSENT in env["reasons"],
          str(env["reasons"]))

with section("G-2. ★★ EVIDENCE_BLOCKED — exhibit identity 부재 (도달 가능한 결손)"):
    # ★ 반면 `exhibit_identity` 는 record 필수 축이 **아니다** — record 계약을
    #   통과해 Store 까지 도달할 수 있다. 이 층이 막아야 하는 것은 여기다.
    stripped = copy.deepcopy(FY26[1])
    stripped["provenance"].pop("exhibit_identity", None)
    st, res = ST.apply_record(ST.empty_state(), stripped)
    check("전제 · record 계약은 통과한다 (exhibit_identity 는 필수가 아니다)",
          res["outcome"] == ST.NEW, res["outcome"])
    key = ("MSFT", "Commercial remaining performance obligation", "2025-12-31")
    env = EV.envelope_for(st, key)
    check("acquisition provenance 부재가 감지된다",
          env["acquisition_provenance_present"] is False)
    check("★★ status = EVIDENCE_BLOCKED", env["status"] == EV.EVIDENCE_BLOCKED, env["status"])
    check("★ 사유가 ACQUISITION_PROVENANCE_MISSING",
          EV.ACQUISITION_PROVENANCE_MISSING in env["blocked_by"], str(env["blocked_by"]))
    check("★ 출처 축 결손도 함께 보고된다",
          EV.SOURCE_IDENTITY_INCOMPLETE in env["blocked_by"], str(env["blocked_by"]))
    check("★ 어느 축이 비었는지 적힌다",
          any("exhibit" in r for r in env["reasons"]), str(env["reasons"]))
    check("★★ store 는 consumable 이라 해도 전달 자격은 아니다",
          env["consumable"] is True and env["status"] == EV.EVIDENCE_BLOCKED)
    check("★★ 값을 실어 내보내지 않는다", env["observation"] is None)
    check("★★ deliverable 에 들어가지 않는다", EV.deliverable([env]) == [])

with section("H. 순수성 · 입력 불변"):
    st = state_with(FY26)
    before = ST.canonical_json(st)
    EV.envelopes_from_state(st)
    EV.envelope_for(st, KEY0)
    EV.envelope_for(st, KEY_ABSENT)
    check("★★ state 를 변형하지 않는다", ST.canonical_json(st) == before)
    a = EV.envelope_for(st, KEY0)
    b = EV.envelope_for(st, KEY0)
    check("★ 같은 입력에 같은 출력 (deterministic)",
          json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))

with section("I. partition · deliverable"):
    tampered = copy.deepcopy(FY26[0])
    tampered["decision"]["raw_value"] = "77%"
    tampered["decision"]["numeric_value"] = "77"
    st = state_with(FY26 + [tampered])
    envs = EV.envelopes_from_state(st)
    part = EV.partition(envs)
    check("세 상태 키가 모두 있다", sorted(part) == sorted(EV.STATUSES))
    check("★ 합계가 보존된다 — 어떤 envelope 도 사라지지 않는다",
          sum(len(v) for v in part.values()) == len(envs))
    check("★ 충돌 1건이 blocked 로 분리된다",
          len(part[EV.EVIDENCE_BLOCKED]) == 1, str(len(part[EV.EVIDENCE_BLOCKED])))
    check("★ 나머지 3건은 available", len(part[EV.EVIDENCE_AVAILABLE]) == 3,
          str(len(part[EV.EVIDENCE_AVAILABLE])))
    check("★★ deliverable 은 available 만이다",
          all(e["status"] == EV.EVIDENCE_AVAILABLE for e in EV.deliverable(envs))
          and len(EV.deliverable(envs)) == 3)

with section("J. 입력 검증 — 조용히 수용하지 않는다"):
    for bad, why in [({}, "빈 dict"),
                     ({"schema_version": "other/1", "series": {}}, "다른 schema"),
                     (None, "None")]:
        try:
            EV.envelope_for(bad, KEY0)
            check(f"★ {why} 를 거부한다", False, "통과해버렸다")
        except EV.EnvelopeError:
            check(f"★ {why} 를 거부한다", True)
    st = state_with(FY26)
    for bad, why in [(("MSFT", "x"), "축 2개"),
                     (("MSFT", "x", "y", "z"), "축 4개"),
                     (("MSFT", "", "2025-09-30"), "빈 축"),
                     ("MSFT|x|y", "문자열")]:
        try:
            EV.envelope_for(st, bad)
            check(f"★ key {why} 를 거부한다", False, "통과해버렸다")
        except EV.EnvelopeError:
            check(f"★ key {why} 를 거부한다", True)

with section("J-2. ★★ 손상된 Store state — bridge 가 스스로 fail-closed 인가"):
    # ★ CIO 판정 2026-08-16. Store 불변식이 원래 막는다 해도 이 층이 그것을
    #   **가정하지 않고 확인**해야 한다. 아래 state 들은 Store 가 만들지 않지만,
    #   손상 · 수기 편집 · 다른 버전이 쓴 파일로는 도달할 수 있다.
    base = state_with([FY26[0]])
    ks = ST.key_str(ST.observation_key(FY26[0]))
    check("전제 · 정상 state 는 available 이다",
          EV.envelope_for(base, KEY0)["status"] == EV.EVIDENCE_AVAILABLE)

    broken = copy.deepcopy(base)
    broken["series"][ks]["consumable"] = False          # blocked_by 는 여전히 []
    env = EV.envelope_for(broken, KEY0)
    check("★★ consumable=False · blocked_by=[] → available 로 흘러가지 않는다",
          env["status"] == EV.EVIDENCE_BLOCKED, env["status"])
    check("★ 사유가 STORE_STATE_INCONSISTENT",
          EV.STORE_STATE_INCONSISTENT in env["blocked_by"], str(env["blocked_by"]))
    check("★★ 값을 실어 내보내지 않는다", env["observation"] is None)
    check("★★ deliverable 에 들어가지 않는다", EV.deliverable([env]) == [])

    lying = copy.deepcopy(base)
    lying["series"][ks]["consumable"] = True
    lying["series"][ks]["blocked_by"] = [ST.OBSERVATION_CONFLICT_UNRESOLVED]
    env = EV.envelope_for(lying, KEY0)
    check("★★ 반대 방향(consumable=True 인데 차단 사유 존재)도 차단한다",
          env["status"] == EV.EVIDENCE_BLOCKED, env["status"])
    check("★ 원래 차단 사유가 보존된다",
          ST.OBSERVATION_CONFLICT_UNRESOLVED in env["blocked_by"], str(env["blocked_by"]))
    check("★ 불일치 사실도 함께 보고된다",
          EV.STORE_STATE_INCONSISTENT in env["blocked_by"], str(env["blocked_by"]))

    revs = copy.deepcopy(base)
    revs["series"][ks]["revisions"] = revs["series"][ks]["revisions"] * 2
    revs["series"][ks]["consumable"] = True
    revs["series"][ks]["blocked_by"] = []
    env = EV.envelope_for(revs, KEY0)
    check("★★ revision 2건인데 consumable=True 라 주장해도 차단한다",
          env["status"] == EV.EVIDENCE_BLOCKED, env["status"])
    check("★★ 두 revision 중 하나를 고르지 않는다", env["observation"] is None)

    zero = copy.deepcopy(base)
    zero["series"][ks]["revisions"] = []
    zero["series"][ks]["consumable"] = True
    zero["series"][ks]["blocked_by"] = []
    env = EV.envelope_for(zero, KEY0)
    check("★★ revision 0건인데 consumable=True 라 주장해도 차단한다",
          env["status"] == EV.EVIDENCE_BLOCKED, env["status"])
    check("★ 출처를 지어내지 않는다", env["source_identity"] is None)

with section("J-3. ★★ dict 아닌 state — 예외가 새지 않는다"):
    # ★ WS4 에서 잡힌 것과 같은 종류다: 거부 메시지를 만들다가 EnvelopeError 가
    #   아니라 AttributeError 가 나가면 호출자의 fail-closed 처리가 빗나간다.
    for bad, why in [("store", "truthy 문자열"), (42, "정수"), (["a"], "리스트"),
                     (("a",), "튜플"), (0, "falsy 정수"), (True, "boolean"),
                     (set(), "빈 집합")]:
        try:
            EV.envelope_for(bad, KEY0)
            check(f"★★ {why} 를 EnvelopeError 로 거부한다", False, "통과해버렸다")
        except EV.EnvelopeError:
            check(f"★★ {why} 를 EnvelopeError 로 거부한다", True)
        except Exception as e:
            check(f"★★ {why} 를 EnvelopeError 로 거부한다", False,
                  f"{type(e).__name__} 이 샜다: {e}")
    for bad, why in [("store", "truthy 문자열"), (42, "정수")]:
        try:
            EV.envelopes_from_state(bad)
            check(f"★ envelopes_from_state 도 {why} 를 거부한다", False, "통과해버렸다")
        except EV.EnvelopeError:
            check(f"★ envelopes_from_state 도 {why} 를 거부한다", True)
        except Exception as e:
            check(f"★ envelopes_from_state 도 {why} 를 거부한다", False,
                  f"{type(e).__name__} 이 샜다: {e}")

with section("K. Store 계약과의 정합"):
    check("★★ source identity 축은 Store material provenance 의 부분집합이다",
          set(EV.SOURCE_IDENTITY_FIELDS) <= set(ST.MATERIAL_PROVENANCE_FIELDS),
          str(sorted(set(EV.SOURCE_IDENTITY_FIELDS) - set(ST.MATERIAL_PROVENANCE_FIELDS))))
    check("★ 차단 사유 어휘를 Store 에서 그대로 가져온다",
          ST.OBSERVATION_CONFLICT_UNRESOLVED and ST.REVISION_AUTHORITY_UNRESOLVED)
    check("★ 세 상태는 서로 배타적이다", len(set(EV.STATUSES)) == 3)

sys.exit(K.exit_code())
