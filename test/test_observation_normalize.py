#!/usr/bin/env python3
"""Observation Layer 층 ③ 회귀 — Normalization + ObservationRecord (S2 · CIO 승인 2026-08-16).

★ 이 회귀가 증명하는 것
   ① `51% / (1)% / -1% / 1.5% / 0%` → exact Decimal · sign_convention 보존
   ② malformed fault matrix 전건 **fail-closed** — 실패가 성공 record 로 흐르지 않는다
   ③ raw 문면이 어떤 경우에도 폐기되지 않는다
   ④ numeric 이 **문자열**로 직렬화되고 float 이 쓰이지 않는다
   ⑤ CC · impact 가 evidence-only 이며 Decision 값으로 승격되지 않는다
   ⑥ record invariant 위반이 전부 fail-closed
   ⑦ 층 순서 — observer 는 normalize 를 모르고, record 는 `persisted=False` 로 만든다

★ 이 회귀가 증명하지 못하는 것
   store · pair · evaluator · live 취득 — 전부 S3 이후 Gate 다.

⛔ 네트워크를 쓰지 않는다. fixture only.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
sys.path.insert(0, os.path.join(ROOT, "observation"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import normalize as N                                               # noqa: E402
import record as RC                                                 # noqa: E402
import rule0022_commercial_rpo as OBS                                # noqa: E402
import msft_sec_results_acquisition as ACQ                           # noqa: E402

import checkkit as K                                                # noqa: E402
K.init(quiet=True)
check, need, skip, guard, section = K.check, K.need, K.skip, K.guard, K.section

FX_DIR = os.path.join(ROOT, "collectors", "fixtures")
NORM_SRC = os.path.join(ROOT, "observation", "normalize.py")
REC_SRC = os.path.join(ROOT, "observation", "record.py")


def _calls(path):
    called = set()
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                called.add(f.id)
            elif isinstance(f, ast.Attribute):
                called.add(f.attr)
    return called


def walk_values(obj):
    """중첩 구조의 모든 leaf 값을 낸다 — float 유입을 구석까지 훑기 위해서다."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from walk_values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from walk_values(v)
    else:
        yield obj


def _imports(path):
    out = set()
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
    return out


# ══════════════════════════════════════════════════════════════════════
with section("A. float 부재 · Decimal 사용 (AST)"):
    for name, path in (("normalize.py", NORM_SRC), ("record.py", REC_SRC)):
        c = _calls(path)
        check(f"★★ {name} 가 `float()` 를 호출하지 않는다", "float" not in c, str(sorted(c))[:0])
        check(f"{name} 가 `round()` 를 호출하지 않는다", "round" not in c)
    check("★★ normalize 가 `Decimal` 을 쓴다", "Decimal" in _calls(NORM_SRC))
    check("normalize 가 `decimal` 모듈에서 가져온다", "decimal" in _imports(NORM_SRC))
    # ⛔ 「소스에 `float` 문자열이 없다」 식의 문자열 검색 검사는 두지 않는다.
    #    docstring 이 「⛔ float 금지」라고 **설명**하면 그 검사가 실패한다 — 언급과
    #    사용은 다르다. 위 AST 검사가 사용 여부의 정본이다. (별건 1 과 같은 유형)

with section("A-2. 층 순서 — observer 는 normalization 층을 모른다"):
    obs_imp = _imports(os.path.join(ROOT, "collectors", "rule0022_commercial_rpo.py"))
    check("★★ observer 가 `normalize` 를 import 하지 않는다", "normalize" not in obs_imp,
          str(sorted(obs_imp)))
    check("★★ observer 가 `record` 를 import 하지 않는다", "record" not in obs_imp)
    check("★ record 가 observer 를 import 하지 않는다 (역방향 결합도 없다)",
          "rule0022_commercial_rpo" not in _imports(REC_SRC), str(sorted(_imports(REC_SRC))))
    check("record 가 normalize 를 import 한다", "normalize" in _imports(REC_SRC))

# ══════════════════════════════════════════════════════════════════════
with section("B. ★★ 승인된 percent 표기 — exact decimal · sign_convention"):
    # ⛔ 기대값에 `N.SIGN_*` 상수를 쓰지 않는다 — 상수를 바꾸면 기대값도 같이 움직여
    #    검사가 항진명제가 된다. (실제로 N-SIGN-2 변이가 그 틈으로 SURVIVED 했다.)
    #    계약 문자열을 **문면 그대로** 박는다.
    CASES = [
        ("51%", "51", "none"),
        ("(1)%", "-1", "accounting_parentheses"),
        ("-1%", "-1", "explicit_minus"),
        ("1.5%", "1.5", "none"),
        ("0%", "0", "none"),
        ("110%", "110", "none"),
        ("(1.5)%", "-1.5", "accounting_parentheses"),
    ]
    check("★ 계약 상수 문면이 고정돼 있다 — none",
          N.SIGN_NONE == "none", N.SIGN_NONE)
    check("★ 계약 상수 문면이 고정돼 있다 — accounting_parentheses",
          N.SIGN_PARENS == "accounting_parentheses", N.SIGN_PARENS)
    check("★ 계약 상수 문면이 고정돼 있다 — explicit_minus",
          N.SIGN_MINUS == "explicit_minus", N.SIGN_MINUS)
    check("★ 세 sign_convention 이 서로 다르다",
          len({N.SIGN_NONE, N.SIGN_PARENS, N.SIGN_MINUS}) == 3)
    for raw, num, sign in CASES:
        try:
            f = N.normalize_pct(raw)
        except N.NormalizationError as e:
            check(f"{raw!r} 가 정규화된다", False, str(e))
            continue
        check(f"★ {raw!r} → numeric {num!r}", f["numeric_value"] == num, f["numeric_value"])
        check(f"  {raw!r} sign_convention = {sign}", f["sign_convention"] == sign,
              f["sign_convention"])
        check(f"  {raw!r} raw 문면이 보존된다", f["raw_value"] == raw, f["raw_value"])
        check(f"  {raw!r} unit = pct", f["unit"] == N.UNIT_PCT)
        check(f"  {raw!r} numeric 이 문자열이다 (JSON 직렬화 계약)",
              isinstance(f["numeric_value"], str))
        check(f"  {raw!r} exact decimal 로 되살아난다",
              N.numeric(f) == Decimal(num), str(N.numeric(f)))

with section("B-2. ★ 부호 표기 provenance — 같은 값, 다른 표기"):
    a, b = N.normalize_pct("(1)%"), N.normalize_pct("-1%")
    check("★★ 두 표기의 numeric 은 같다", a["numeric_value"] == b["numeric_value"])
    check("★★ 두 표기의 sign_convention 은 다르다",
          a["sign_convention"] != b["sign_convention"],
          f'{a["sign_convention"]} vs {b["sign_convention"]}')
    check("★★ 두 표기의 raw 는 다르다 — source 복원성이 유지된다",
          a["raw_value"] != b["raw_value"])
    check("양수 일반형은 taxonomy 를 키우지 않는다 (`none`)",
          N.normalize_pct("51%")["sign_convention"] == "none")

with section("B-3. exact decimal — float 이었다면 깨지는 경우"):
    x = N.numeric(N.normalize_pct("0.1%")) + N.numeric(N.normalize_pct("0.2%"))
    check("★★ 0.1 + 0.2 == 0.3 (exact decimal)", x == Decimal("0.3"), str(x))
    check("  float 이었다면 성립하지 않는다 (대조군)", (0.1 + 0.2) != 0.3)
    check("★ 후행 0 이 보존된다 — 1.50% 와 1.5% 는 raw 로 구별된다",
          N.normalize_pct("1.50%")["numeric_value"] == "1.50"
          and N.normalize_pct("1.5%")["numeric_value"] == "1.5")
    check("  그래도 수치는 같다",
          N.numeric(N.normalize_pct("1.50%")) == N.numeric(N.normalize_pct("1.5%")))

# ══════════════════════════════════════════════════════════════════════
with section("C. ★★ malformed fault matrix — 전건 fail-closed"):
    MALFORMED = [
        ("", "빈 문자열"),
        ("n/a", "숫자가 아니다"),
        ("(1%", "괄호 불균형(열림)"),
        ("1)%", "괄호 불균형(닫힘)"),
        ("--1%", "이중 부호"),
        ("( -1 )%", "괄호 안 공백 + 부호 — 이중 의미"),
        ("1", "unit 없음"),
        ("51", "percent 기호 없음"),
        ("%", "숫자 없음"),
        (" 51%", "선행 공백 — 임의 trim 금지"),
        ("51% ", "후행 공백 — 임의 trim 금지"),
        ("51 %", "내부 공백"),
        ("+1%", "승인되지 않은 부호 표기"),
        ("(-1)%", "괄호와 마이너스 동시 사용"),
        ("-(1)%", "마이너스와 괄호 중첩"),
        ("1.%", "소수점 뒤 자리 없음"),
        (".5%", "정수부 없음"),
        ("1,5%", "쉼표 구분자"),
        ("51%%", "이중 percent"),
        ("nan%", "특수값"),
        ("Infinity%", "특수값"),
        (None, "None 입력"),
        (51, "int 입력"),
        (51.0, "float 입력"),
    ]
    for raw, why in MALFORMED:
        try:
            got = N.normalize_pct(raw)
            check(f"★ {raw!r} → fail-closed ({why})", False, f"통과해버렸다: {got}")
        except N.NormalizationError:
            check(f"★ {raw!r} → fail-closed ({why})", True)
        except Exception as e:                                      # noqa: BLE001
            check(f"★ {raw!r} → NormalizationError 로 실패한다 ({why})", False,
                  f"{type(e).__name__}: {e}")

with section("C-1b. ★ 타입 오류와 형식 오류를 구별한다"):
    # ⛔ 「어쨌든 실패하니까 됐다」로 두지 않는다. 문자열이 아닌 입력을 `str()` 로 강제
    #    변환해도 형식 검사에 걸려 실패하므로, **실패했다는 사실만** 보면 타입 계약이
    #    사라진 것을 알 수 없다. (실제로 N-TYPE-1 변이가 그 틈으로 SURVIVED 했다.)
    #    그래서 실패 **사유**를 구별한다.
    for bad, why in [(None, "None"), (51, "int"), (51.0, "float"), (b"51%", "bytes"),
                     (Decimal("51"), "Decimal")]:
        try:
            N.normalize_pct(bad)
            check(f"★ {why} 입력 → fail-closed", False, "통과해버렸다")
        except N.NormalizationError as e:
            check(f"★★ {why} 입력이 **타입 오류**로 거부된다 (형식 오류가 아니라)",
                  "문자열이 아니다" in str(e), str(e))
    e_fmt = None
    try:
        N.normalize_pct("n/a")
    except N.NormalizationError as e:
        e_fmt = str(e)
    check("대조군: 문자열 malformed 는 **형식 오류**로 거부된다",
          e_fmt is not None and "문자열이 아니다" not in e_fmt, str(e_fmt))

with section("C-2. 기간 문면 fault matrix"):
    ok = N.normalize_period_end("September 30, 2025")
    check("★ `September 30, 2025` → `2025-09-30`",
          ok["economic_period_end"] == "2025-09-30", ok["economic_period_end"])
    check("  기간 raw 문면이 보존된다", ok["period_end_raw"] == "September 30, 2025")
    for raw, why in [("", "빈 문자열"), (None, "None"), ("2025-09-30", "이미 ISO — 승인 표기 아님"),
                     ("Sept 30, 2025", "축약 월 이름"), ("Smarch 30, 2025", "없는 월"),
                     ("September 32, 2025", "일자 범위 초과"),
                     ("September 30 2025", "쉼표 없음"), ("september 30, 2025", "소문자 시작")]:
        try:
            N.normalize_period_end(raw)
            check(f"★ 기간 {raw!r} → fail-closed ({why})", False, "통과해버렸다")
        except N.NormalizationError:
            check(f"★ 기간 {raw!r} → fail-closed ({why})", True)

# ══════════════════════════════════════════════════════════════════════
# D. draft → record — 실제 fixture 로
# ══════════════════════════════════════════════════════════════════════
MAN = json.load(open(os.path.join(FX_DIR, "azure_cc_MANIFEST.json"), encoding="utf-8"))
MAN25 = json.load(open(os.path.join(FX_DIR, "azure_cc_fy25_MANIFEST.json"), encoding="utf-8"))
EXPECT = {"2025-09-30": ("51%", "51"), "2025-12-31": ("110%", "110"),
          "2026-03-31": ("99%", "99"), "2026-06-30": ("84%", "84")}


def draft_for(c):
    html = open(os.path.join(FX_DIR, c["fixture_file"]), encoding="utf-8").read()
    prov = ACQ.exhibit_provenance(c, c["exhibit"], c["exhibit_sha256"])
    prov["slice_sha256"] = c["slice_sha256"]
    d, probs, _ = OBS.observe_html(html, provenance=prov)
    return d, probs


with section("D. ★★ FY26 4건 — draft → record 파이프라인"):
    built = 0
    for c in sorted(MAN["captured"], key=lambda x: x["filing_date"]):
        d, probs = draft_for(c)
        if not need(f"{c['filing_date']} draft 생성", d is not None, str(probs)[:80]):
            continue
        rec, err = RC.try_build(d)
        if not need(f"{c['filing_date']} record 생성", rec is not None, str(err)[:120]):
            continue
        built += 1
        pe = rec["economic_period_end"]
        raw, num = EXPECT[pe]
        check(f"★★ {pe} decision raw = {raw}", rec["decision"]["raw_value"] == raw,
              rec["decision"]["raw_value"])
        check(f"★★ {pe} decision numeric = {num}", rec["decision"]["numeric_value"] == num,
              rec["decision"]["numeric_value"])
        check(f"{pe} unit = pct", rec["decision"]["unit"] == "pct")
        check(f"{pe} sign_convention = none", rec["decision"]["sign_convention"] == "none")
        check(f"{pe} normalized = True", rec["normalized"] is True)
        check(f"★★ {pe} persisted = False (층 ④ 미침범)", rec["persisted"] is False)
        check(f"{pe} economic_period_kind = quarter", rec["economic_period_kind"] == "quarter")
        check(f"{pe} period raw 문면이 보존된다",
              rec["period_text_raw"].startswith("Three Months Ended"), rec["period_text_raw"])
        check(f"{pe} provenance accession 이 있다", bool(rec["provenance"]["accession"]))
        check(f"{pe} provenance source sha 가 있다", bool(rec["provenance"]["source_sha256"]))
        check(f"{pe} narrowing exactly-one 증거가 보존된다",
              rec["narrowing"]["row_candidates"] == 1
              and rec["narrowing"]["column_candidates"] == {"gaap": 1, "cc_impact": 1, "cc": 1})
        check(f"{pe} decision 이 Decision 값으로 표시된다",
              rec["decision"]["is_decision_value"] is True)
        # ── evidence-only 구조 증거 ────────────────────────────────────
        ev = {e["column_key"]: e for e in rec["evidence_columns"]}
        check(f"★★ {pe} evidence 가 cc_impact · cc 두 건이다",
              sorted(ev) == ["cc", "cc_impact"], str(sorted(ev)))
        for k, e in ev.items():
            check(f"★★ {pe} evidence[{k}] 가 Decision 값이 아니다",
                  e["is_decision_value"] is False)
            check(f"{pe} evidence[{k}] 에 raw 와 numeric 이 함께 있다",
                  bool(e["raw_value"]) and isinstance(e["numeric_value"], str))
            check(f"{pe} evidence[{k}] unit = pct", e["unit"] == "pct")
        check(f"★ {pe} Decision 값은 정확히 1개다",
              sum(1 for x in [rec["decision"]] + rec["evidence_columns"]
                  if x.get("is_decision_value")) == 1)
        # ── JSON 직렬화 계약 ──────────────────────────────────────────
        blob = json.loads(json.dumps(rec, ensure_ascii=False))
        check(f"★★ {pe} JSON 왕복 후에도 numeric 이 문자열이다",
              isinstance(blob["decision"]["numeric_value"], str))
        check(f"★★ {pe} JSON 전체에 float 타입 값이 하나도 없다",
              not any(isinstance(v, float) for v in walk_values(blob)))
    check("★★ FY26 4/4 record 가 생성된다", built == 4, f"{built}/4")

with section("D-2. ★ FY25 4건 — draft 가 없으므로 record 도 없다"):
    for c in sorted(MAN25["captured"], key=lambda x: x["filing_date"]):
        d, probs = draft_for(c)
        check(f"{c['filing_date']} draft 가 없다 (ROW_ABSENT)", d is None)
        check(f"★ {c['filing_date']} ROW_ABSENT 는 normalization 층에 도달하지 않는다",
              OBS.observation_absent(probs))

# ══════════════════════════════════════════════════════════════════════
with section("E. ★★ record invariant fault matrix — 전건 fail-closed"):
    base_c = sorted(MAN["captured"], key=lambda x: x["filing_date"])[0]
    BASE_DRAFT, _ = draft_for(base_c)
    need("기준 draft 가 있다", BASE_DRAFT is not None)

    def mutate(**over):
        import copy
        d = copy.deepcopy(BASE_DRAFT)
        for path, val in over.items():
            keys = path.split(".")
            cur = d
            for k in keys[:-1]:
                cur = cur[k]
            if val is RC and keys[-1] in cur:
                del cur[keys[-1]]
            else:
                cur[keys[-1]] = val
        return d

    rec, err = RC.try_build(mutate())
    check("대조군: 무변형 draft 는 record 가 된다", rec is not None, str(err)[:80])

    FAULTS = [
        ("subject 누락", {"subject": ""}),
        ("subject 오염", {"subject": "AAPL"}),
        ("measurement identity 오염", {"measurement_identity": "Total remaining performance obligation"}),
        ("period kind 가 quarter 아님", {"economic_period_kind": "annual"}),
        ("Decision 열이 cc 로 뒤바뀜", {"decision.column_key": "cc"}),
        ("Decision 열 identity 오염", {"decision.column_identity": "Percentage Change Y/Y Constant Currency"}),
        ("decision raw 가 malformed", {"decision.raw_value": "n/a"}),
        ("decision raw 가 빈 문자열", {"decision.raw_value": ""}),
        ("period 문면 malformed", {"period_end_text": "Sept 30, 2025"}),
        ("narrowing row 가 2", {"narrowing.row_candidates": 2}),
        ("narrowing table 이 0", {"narrowing.table_candidates": 0}),
        ("narrowing column 이 각각 1이 아님",
         {"narrowing.column_candidates": {"gaap": 2, "cc_impact": 1, "cc": 1}}),
        ("provenance 가 비었음", {"provenance": {}}),
        ("provenance accession 누락", {"provenance.accession": ""}),
        ("provenance source sha 누락", {"provenance.source_sha256": ""}),
        ("draft 가 이미 normalized 라고 주장", {"normalized": True}),
        ("draft 가 이미 persisted 라고 주장", {"persisted": True}),
        ("evidence 열이 사라짐", {"evidence_columns": []}),
        ("evidence 열 순서/구성이 다름",
         {"evidence_columns": [{"column_key": "cc", "column_identity": "x", "raw_value": "51%"}]}),
        ("evidence raw 가 malformed",
         {"evidence_columns": [{"column_key": "cc_impact", "column_identity": "x", "raw_value": "(1%"},
                               {"column_key": "cc", "column_identity": "y", "raw_value": "51%"}]}),
    ]
    for why, over in FAULTS:
        r, e = RC.try_build(mutate(**over))
        check(f"★ record fail-closed — {why}", r is None, f"통과해버렸다: {str(r)[:60]}")
        check(f"  사유가 보고된다 — {why}", bool(e))

    check("★ draft 가 dict 가 아니면 fail-closed", RC.try_build("nope")[0] is None)

    check("★ draft 가 None 이면 fail-closed", RC.try_build(None)[0] is None)

with section("E-2. validate_record 는 조립과 분리돼 독립 실행된다"):
    rec, _ = RC.try_build(BASE_DRAFT)
    if need("검증 대상 record 가 있다", rec is not None):
        RC.validate_record(rec)          # 예외 없이 통과해야 한다
        check("정상 record 는 validate 를 통과한다", True)
        import copy
        for why, path, val in [("persisted 가 True", "persisted", True),
                               ("normalized 가 False", "normalized", False),
                               ("schema_version 이 다름", "schema_version", "observation/0")]:
            bad = copy.deepcopy(rec)
            bad[path] = val
            try:
                RC.validate_record(bad)
                check(f"★ validate fail-closed — {why}", False, "통과해버렸다")
            except RC.RecordInvariantError:
                check(f"★ validate fail-closed — {why}", True)
        bad = copy.deepcopy(rec)
        bad["decision"]["numeric_value"] = 51.0
        try:
            RC.validate_record(bad)
            check("★★ validate fail-closed — numeric 이 float", False, "통과해버렸다")
        except RC.RecordInvariantError:
            check("★★ validate fail-closed — numeric 이 float 이면 잡는다", True)
        bad = copy.deepcopy(rec)
        bad["evidence_columns"][0]["is_decision_value"] = True
        try:
            RC.validate_record(bad)
            check("★★ validate fail-closed — evidence 가 Decision 으로 승격", False, "통과해버렸다")
        except RC.RecordInvariantError:
            check("★★ validate fail-closed — evidence 승격을 잡는다", True)

sys.exit(K.exit_code())
