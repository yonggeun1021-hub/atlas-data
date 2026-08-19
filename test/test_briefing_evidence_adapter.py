#!/usr/bin/env python3
"""브리핑 evidence adapter 회귀 (WS4 · 2026-08-16).

★ 이 회귀가 증명하는 것
   ① 세 층(확인된 사실 / 투자판정 반영 / 현재 행동)이 **분리되어** 나온다
   ② 사실 층에 해석·형용사·매매 어휘가 섞이지 않는다
   ③ 판정 반영 상태를 **스스로 정하지 않는다** — 입력으로만 받는다
   ④ 판정이 승인된 경우의 표기는 **정의되지 않았다고 멈춘다** (임의 생성 금지)
   ⑤ 관측 부재를 「변화 없음」이나 「0」으로 바꾸지 않는다
   ⑥ 차단된 관측의 값을 사실처럼 내보내지 않는다
   ⑦ 순수 함수다 — 네트워크 · 파일 · Notion 을 모른다

⛔ 네트워크 없음 · Notion 쓰기 없음 · evaluator 호출 없음.
"""
from __future__ import annotations

import ast
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "briefing"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import evidence_adapter as A                                        # noqa: E402

import checkkit as K                                                # noqa: E402
K.init(quiet=True)
check, need, section = K.check, K.need, K.section

SRC = os.path.join(ROOT, "briefing", "evidence_adapter.py")

BANNED_VERDICTS = {"BUY", "SELL", "HOLD", "REDUCE", "ADD", "TRIM",
                   "OVERWEIGHT", "UNDERWEIGHT", "매수", "매도", "축소", "비중확대"}
BANNED_KEYS = {"recommendation", "verdict", "rating", "signal",
               "order", "target_price", "price_target", "position_size",
               "weight", "conviction"}
# ★ `current_action.action` 은 CIO 가 지정한 **상위 입력 통로**다 (Q8 반려 판정).
#   이 모듈이 만드는 것이 아니라 받아 싣는 자리이므로 금지 키가 아니다.
#   ⛔ 대신 그 통로가 정확히 세 축만 갖는지를 아래에서 따로 고정한다.
SANCTIONED_ACTION_KEYS = {"action", "authority", "note"}
# ★ 사실 층에 나타나면 안 되는 **해석 어휘**.
INTERPRETIVE = ("강하다", "높다", "개선", "둔화", "명분", "긍정", "부정",
                "호재", "악재", "우려", "기대")


def envelope(status="EVIDENCE_AVAILABLE", period="2026-06-30", raw="84%",
             blocked=None, with_source=True):
    env = {
        "schema_version": "evidence_envelope/1",
        "subject": "MSFT",
        "measurement_identity": "Commercial remaining performance obligation",
        "economic_period_end": period,
        "status": status,
        "reasons": [],
        "consumable": status == "EVIDENCE_AVAILABLE",
        "blocked_by": blocked or [],
        "acquisition_provenance_present": status == "EVIDENCE_AVAILABLE",
        "source_identity": None,
        "audit_provenance": None,
        "observation": None,
    }
    if with_source and status != "EVIDENCE_UNRESOLVED":
        env["source_identity"] = {
            "accession": "0001193125-26-323632",
            "filing_date": "2026-07-29",
            "exhibit_type": "EX-99.1",
            "exhibit_document": "msft-ex99_1.htm",
            "source_sha256": "03471d4dc122c24deace523de4130d53205a78de2ec11fc965764738bf97149a",
        }
    if status == "EVIDENCE_AVAILABLE":
        env["observation"] = {
            "raw_value": raw, "numeric_value": raw.rstrip("%"),
            "unit": "pct", "sign_convention": "none",
            "decision_column_identity": "Percentage Change Y/Y (GAAP)",
            "row_label_raw": "Commercial remaining performance obligation",
            "period_end_raw": "June 30, 2026",
            "observed_by": "rule0022_commercial_rpo",
        }
    return env


NOT_AUTH = {"status": A.EVALUATION_NOT_AUTHORIZED,
            "authority": "CIO 판정 2026-08-16 · RULE-0022_EVALUATION_NOT_AUTHORIZED"}


def strings_in(obj):
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


# ══════════════════════════════════════════════════════════════════════
with section("A. 층 경계 — adapter 는 네트워크 · Notion · evaluator 를 모른다"):
    tree = ast.parse(open(SRC, encoding="utf-8").read())
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    for banned in ("requests", "urllib", "http", "socket", "git", "subprocess",
                   "notion", "json", "os", "smtplib"):
        check(f"⛔ `{banned}` 를 import 하지 않는다", banned not in imported,
              str(sorted(imported)))
    check("★★ 아무것도 import 하지 않는 순수 formatter 다",
          imported <= {"__future__"}, str(sorted(imported)))

with section("B. ★★ 매매 어휘를 만들지 않는다 — 소스 리터럴"):
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
    check("★★ 매매 어휘와 정확히 일치하는 리터럴이 없다", not offend, str(offend))
    check("★★ 판정용 키 이름을 만들지 않는다",
          not sorted(s for s in literals if s in BANNED_KEYS))
    check("★★ 매매 방향을 뜻하는 리터럴을 어디에도 두지 않는다",
          not sorted(s for s in literals
                     if s.strip().upper() in BANNED_VERDICTS | {"NO_CHANGE"}))
    check("★ 그럼에도 금지 사실은 문서화돼 있다", any("⛔" in d for d in docstrings))

with section("C. ★★ 세 층이 분리되어 나온다"):
    block = A.briefing_block([envelope()], NOT_AUTH, slot=A.MORNING)
    check("schema_version 이 붙는다",
          block["schema_version"] == A.EVIDENCE_BRIEFING_SCHEMA_VERSION)
    for layer in ("confirmed_facts", "evaluation_reflection", "current_action"):
        check(f"  층 `{layer}` 가 있다", layer in block, str(sorted(block)))
    check("★ ① 사실이 1건이다", len(block["confirmed_facts"]) == 1)
    check("★ ② 반영 상태가 미승인이다",
          block["evaluation_reflection"]["status"] == A.EVALUATION_NOT_AUTHORIZED)
    check("★★ ② reflected = False", block["evaluation_reflection"]["reflected"] is False)
    check("★ ② 판정 출처가 함께 간다",
          "CIO" in block["evaluation_reflection"]["authority"])
    # ★★ Q8 반려 (CIO 판정 2026-08-16) — formatter 는 행동을 만들지 않는다.
    act = block["current_action"]
    check("★★ ③ 행동을 합성하지 않는다 — 미제공이면 action 이 None 이다",
          act["action"] is None, str(act))
    check("★★ 「기존 판단 유지」 같은 문장을 만들지 않는다",
          "유지" not in str(act.get("action")), str(act))
    check("★ 미정임을 표기로 남긴다", act["note"] == A.ACTION_UNDETERMINED_LABEL, str(act))
    check("★ 출처 없는 행동을 적지 않는다", act["authority"] is None, str(act))
    check("★★ 행동 통로는 정확히 세 축뿐이다 — 임의 필드가 늘지 않는다",
          set(act) == SANCTIONED_ACTION_KEYS, str(sorted(act)))
    check("⛔ ACTION_NO_CHANGE 상수가 더 이상 존재하지 않는다",
          not hasattr(A, "ACTION_NO_CHANGE"))

with section("C-2. 사실 층 내용 — 원문 표기 그대로"):
    block = A.briefing_block([envelope()], NOT_AUTH)
    f = block["confirmed_facts"][0]
    check("★ 관측값이 원문 표기 그대로 들어간다", "84%" in f["line"], f["line"])
    check("★ 측정 대상이 명시된다",
          "Commercial remaining performance obligation" in f["line"])
    check("★ 기간이 명시된다", "2026-06-30" in f["line"])
    check("★ Decision column identity 가 명시된다", "GAAP" in f["line"])
    check("★★ 출처가 함께 간다", f["source"] and "0001193125-26-323632" in f["source"])
    check("★ 출처에 원문 해시가 포함된다", "03471d4dc122" in f["source"])
    check("★★ 해석 어휘가 섞이지 않는다",
          not any(w in f["line"] for w in INTERPRETIVE), f["line"])

with section("C-3. ★ P4-04 official-release source identity 표기"):
    ir = envelope()
    ir["subject"] = "TSM"
    ir["measurement_identity"] = "TSMC consolidated net revenue monthly YoY"
    ir["economic_period_end"] = "2026-07-31"
    ir["observation"]["raw_value"] = "44.7%"
    ir["observation"]["numeric_value"] = "44.7"
    ir["observation"]["decision_column_identity"] = "YoY Change"
    ir["source_identity"] = {
        "identity_kind": "company_ir_web",
        "source_id": "tsmc_ir_monthly_revenue",
        "source_url": "https://investor.tsmc.com/english/monthly-revenue/2026",
        "source_sha256": "a" * 64,
        "available_at": "2026-08-15",
    }
    block = A.briefing_block([ir], NOT_AUTH)
    fact = block["confirmed_facts"][0]
    check("★ SEC 원문으로 잘못 표기하지 않는다", "기업 IR 원문" in fact["line"],
          fact["line"])
    check("★ source id·URL·available_at·hash가 함께 간다",
          "tsmc_ir_monthly_revenue" in fact["source"]
          and "2026-08-15" in fact["source"]
          and "investor.tsmc.com" in fact["source"]
          and "aaaaaaaaaaaa" in fact["source"], fact["source"])
    bad_kind = copy.deepcopy(ir)
    bad_kind["source_identity"]["identity_kind"] = "news_article"
    try:
        A.briefing_block([bad_kind], NOT_AUTH)
        check("⛔ 미등록 source identity kind를 거부한다", False, "통과해버렸다")
    except A.AdapterError:
        check("⛔ 미등록 source identity kind를 거부한다", True)

with section("D. ★★ 차단된 관측 — 값을 사실처럼 내보내지 않는다"):
    env = envelope(status="EVIDENCE_BLOCKED",
                   blocked=["OBSERVATION_CONFLICT_UNRESOLVED"])
    block = A.briefing_block([env], NOT_AUTH)
    line = block["confirmed_facts"][0]["line"]
    check("★★ 값이 브리핑 문장에 나타나지 않는다", "84%" not in line, line)
    check("★ 전달 자격 미확인이 명시된다", "전달 자격" in line, line)
    check("★ 차단 사유가 적힌다", "CONFLICT" in line, line)
    check("★ 상태가 보존된다", block["confirmed_facts"][0]["status"] == "EVIDENCE_BLOCKED")

with section("E. ★★ 관측 부재 — 0 이나 변화없음으로 바꾸지 않는다"):
    env = envelope(status="EVIDENCE_UNRESOLVED", with_source=False)
    block = A.briefing_block([env], NOT_AUTH)
    line = block["confirmed_facts"][0]["line"]
    check("★★ 관측 없음이라고 적는다", "관측 없음" in line, line)
    check("★★ 0 으로 표기하지 않는다", " 0" not in line and "0%" not in line, line)
    check("★★ 「변화 없음」으로 바꾸지 않는다", "변화 없음" not in line, line)
    check("★ 판정 이전임을 명시한다", "판정 이전" in line, line)
    check("★ 출처를 지어내지 않는다", block["confirmed_facts"][0]["source"] is None)

with section("F. ★★ 판정 승인 분기 — 임의로 만들지 않고 멈춘다"):
    auth = {"status": A.EVALUATION_AUTHORIZED, "authority": "가상 승인"}
    try:
        A.briefing_block([envelope()], auth)
        check("★★ 승인 분기에서 멈춘다", False, "문장을 만들어버렸다")
    except A.CIODefinitionRequired as e:
        check("★★ 승인 분기에서 멈춘다", True)
        check("★ 사유가 CIO DEFINITION REQUIRED 다", "CIO DEFINITION REQUIRED" in str(e))

with section("G. ★★ 판정 상태를 스스로 정하지 않는다"):
    for bad, why in [({}, "status 없음"),
                     ({"status": "AUTHORIZED"}, "어휘 밖 status"),
                     ({"status": A.EVALUATION_NOT_AUTHORIZED}, "authority 없음"),
                     ({"status": A.EVALUATION_NOT_AUTHORIZED, "authority": ""}, "빈 authority"),
                     (None, "None")]:
        try:
            A.briefing_block([envelope()], bad)
            check(f"★ {why} 를 거부한다", False, "통과해버렸다")
        except A.AdapterError:
            check(f"★ {why} 를 거부한다", True)
    check("★★ 기본값으로 미승인을 가정하지도 않는다 — 입력이 없으면 실행되지 않는다",
          "evaluation" in A.briefing_block.__code__.co_varnames)

with section("H. 입력 계약 — envelope 이 아닌 것을 받지 않는다"):
    for bad, why in [({"schema_version": "other/1"}, "다른 schema"),
                     ({}, "schema 없음"),
                     ("envelope", "문자열"),
                     (None, "None")]:
        try:
            A.briefing_block([bad], NOT_AUTH)
            check(f"★ {why} 를 거부한다", False, "통과해버렸다")
        except A.AdapterError:
            check(f"★ {why} 를 거부한다", True)
    try:
        A.briefing_block("not a list", NOT_AUTH)
        check("★ envelopes 가 리스트가 아니면 거부한다", False, "통과해버렸다")
    except A.AdapterError:
        check("★ envelopes 가 리스트가 아니면 거부한다", True)
    for bad in ("noon", "", None, "MORNING"):
        try:
            A.briefing_block([envelope()], NOT_AUTH, slot=bad)
            check(f"★ slot={bad!r} 을 거부한다", False, "통과해버렸다")
        except A.AdapterError:
            check(f"★ slot={bad!r} 을 거부한다", True)
    unknown = envelope()
    unknown["status"] = "EVIDENCE_MAYBE"
    try:
        A.briefing_block([unknown], NOT_AUTH)
        check("★★ 알 수 없는 envelope status 를 조용히 넘기지 않는다", False, "통과해버렸다")
    except A.AdapterError:
        check("★★ 알 수 없는 envelope status 를 조용히 넘기지 않는다", True)

with section("I. slot — 라벨일 뿐 행 계약을 바꾸지 않는다"):
    m = A.briefing_block([envelope()], NOT_AUTH, slot=A.MORNING)
    e = A.briefing_block([envelope()], NOT_AUTH, slot=A.EVENING)
    check("slot 이 기록된다", m["slot"] == "morning" and e["slot"] == "evening")
    check("★★ slot 을 빼면 두 블록이 완전히 동일하다",
          {k: v for k, v in m.items() if k != "slot"}
          == {k: v for k, v in e.items() if k != "slot"})
    check("★ 아침/저녁이 서로 다른 사실을 만들지 않는다",
          m["confirmed_facts"] == e["confirmed_facts"])

with section("J. ★★ 출력 어디에도 매매 어휘가 없다"):
    envs = [envelope(), envelope(status="EVIDENCE_BLOCKED",
                                 blocked=["REVISION_AUTHORITY_UNRESOLVED"],
                                 period="2026-03-31"),
            envelope(status="EVIDENCE_UNRESOLVED", period="2026-09-30",
                     with_source=False)]
    block = A.briefing_block(envs, NOT_AUTH)
    vals = list(strings_in(block))
    offend = sorted({s for s in vals if s.strip().upper() in BANNED_VERDICTS})
    check("★★ 출력 문자열에 매매 어휘가 없다", not offend, str(offend))
    outside_action = {k: v for k, v in block.items() if k != "current_action"}
    check("★★ 출력 키에 판정용 키가 없다",
          not sorted({k for k in keys_in(outside_action) if k in BANNED_KEYS}),
          str(sorted({k for k in keys_in(outside_action) if k in BANNED_KEYS})))
    check("★★ 행동 통로 밖에는 `action` 키가 아예 없다",
          "action" not in set(keys_in(outside_action)))
    text = A.render_text(block)
    check("★ 세 층 라벨이 모두 평문에 나온다",
          "확인된 사실:" in text and "투자판정 반영:" in text and "현재 행동:" in text, text)
    check("★★ 미제공 행동은 미정으로 표기된다", "현재 행동: 미정" in text, text)
    check("★★ 평문에도 매매 어휘가 없다",
          not any(w in text.upper() for w in ("BUY", "SELL", " 매수", " 매도")), text)
    check("★ 세 사실이 모두 실린다", text.count("· MSFT") == 3, text)
    check("★ 차단된 건의 값은 평문에도 없다", text.count("84%") == 1, text)

with section("K. render_text 계약"):
    for bad in ({"schema_version": "other/1"}, {}, None, "block"):
        try:
            A.render_text(bad)
            check("★ briefing block 이 아니면 거부한다", False, "통과해버렸다")
        except A.AdapterError:
            check("★ briefing block 이 아니면 거부한다", True)
    empty = A.briefing_block([], NOT_AUTH)
    check("★ 사실이 0건이어도 층 구조가 유지된다",
          "(없음)" in A.render_text(empty))
    check("★★ 사실이 0건이어도 행동을 만들어내지 않는다",
          empty["current_action"]["action"] is None, str(empty["current_action"]))

with section("L. 순수성"):
    env = envelope()
    before = copy.deepcopy(env)
    A.briefing_block([env], NOT_AUTH)
    check("★★ 입력 envelope 을 변형하지 않는다", env == before)
    ev = dict(NOT_AUTH)
    A.briefing_block([envelope()], ev)
    check("★ 입력 evaluation 을 변형하지 않는다", ev == NOT_AUTH)
    a = A.briefing_block([envelope()], NOT_AUTH)
    b = A.briefing_block([envelope()], NOT_AUTH)
    check("★ 같은 입력에 같은 출력", a == b)

with section("M. ★★ Q8 — current_action 은 받아 적을 뿐이다"):
    supplied = {"action": "포지션 변경 없음 · 리스크 점검 주기 유지",
                "authority": "CIO 판정 2026-08-16"}
    block = A.briefing_block([envelope()], NOT_AUTH, current_action=supplied)
    act = block["current_action"]
    check("★ 상위가 준 문장을 그대로 싣는다", act["action"] == supplied["action"], str(act))
    check("★★ 문구를 고쳐 쓰지 않는다", act["action"] is supplied["action"])
    check("★ 출처가 함께 간다", act["authority"] == "CIO 판정 2026-08-16")
    check("★ 미정 표기는 붙지 않는다", act["note"] is None, str(act))
    text = A.render_text(block)
    check("★ 평문에 행동과 출처가 같이 나온다",
          supplied["action"] in text and "CIO 판정 2026-08-16" in text, text)
    check("★★ 입력 dict 를 변형하지 않는다",
          supplied == {"action": "포지션 변경 없음 · 리스크 점검 주기 유지",
                       "authority": "CIO 판정 2026-08-16"})

with section("M-2. ★★ 출처 없는 행동 · 형태 밖 입력은 거부한다"):
    for bad, why in [("기존 판단 유지", "문자열만 — authority 없음"),
                     ({"action": "x"}, "authority 누락"),
                     ({"authority": "CIO"}, "action 누락"),
                     ({"action": "", "authority": "CIO"}, "빈 action"),
                     ({"action": "   ", "authority": "CIO"}, "공백 action"),
                     ({"action": "x", "authority": ""}, "빈 authority"),
                     ({"action": "x", "authority": "   "}, "공백 authority"),
                     ({"action": 4, "authority": "CIO"}, "action 이 문자열이 아님"),
                     ({"action": "x", "authority": 4}, "authority 가 문자열이 아님"),
                     (["기존 판단 유지"], "리스트"),
                     (42, "정수")]:
        try:
            A.briefing_block([envelope()], NOT_AUTH, current_action=bad)
            check(f"★ {why} 를 거부한다", False, "통과해버렸다")
        except A.AdapterError:
            check(f"★ {why} 를 거부한다", True)
        except Exception as e:
            check(f"★ {why} 를 거부한다", False, f"{type(e).__name__} 이 샜다: {e}")

with section("M-3. ★★ 행동 미제공이 곧 「행동 없음」을 뜻하지 않는다"):
    a = A.briefing_block([envelope()], NOT_AUTH)
    b = A.briefing_block([envelope()], NOT_AUTH,
                         current_action={"action": "리스크 축소 검토",
                                         "authority": "포트폴리오 매니저"})
    check("★★ 미제공과 제공은 서로 다른 블록이다",
          a["current_action"] != b["current_action"])
    check("★★ 미제공은 어떤 행동 문장도 만들지 않는다",
          a["current_action"]["action"] is None)
    check("★ 상위가 evaluation 과 무관한 risk action 을 줄 수 있다",
          b["current_action"]["action"] == "리스크 축소 검토")
    check("★ 그래도 ② 반영 상태는 미승인 그대로다",
          b["evaluation_reflection"]["status"] == A.EVALUATION_NOT_AUTHORIZED)
    check("★★ ②와 ③은 서로를 결정하지 않는다",
          a["evaluation_reflection"] == b["evaluation_reflection"])

sys.exit(K.exit_code())
