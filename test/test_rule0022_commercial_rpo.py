#!/usr/bin/env python3
"""RULE-0022 Commercial RPO observer 회귀 (S1 · CIO 승인 2026-08-16).

★ 이 회귀가 증명하는 것
   ① FY26 fixture 4건에서 `Commercial remaining performance obligation` 행이
      **정확히 1건**이고 GAAP raw 관측이 생성된다
   ② FY25 fixture 4건에서 그 행이 **정확히 0건**이고 결과가 `ROW_ABSENT` 다 (D-6)
   ③ title · row · column 각각 0건·복수건에서 **fail-closed**
   ④ observer 가 `msft_azure_cc` 에서 **아무것도 import 하지 않는다** (RULE-0021 격리)
   ⑤ acquisition primitive 가 **Rule 의미를 모른다**
   ⑥ observer 가 normalization · persistence 를 하지 않는다 (층 경계)

★ 이 회귀가 증명하지 못하는 것
   네트워크 취득 · normalization · store · pair · evaluator — 전부 S2 이후 Gate 다.

⛔ 네트워크를 쓰지 않는다. fixture only.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rule0022_commercial_rpo as R                                 # noqa: E402
import msft_sec_results_acquisition as A                            # noqa: E402

import checkkit as K                                                # noqa: E402
K.init(quiet=True)
check, need, skip, guard, section = K.check, K.need, K.skip, K.guard, K.section

FX_DIR = os.path.join(ROOT, "collectors", "fixtures")
OBS_SRC = os.path.join(ROOT, "collectors", "rule0022_commercial_rpo.py")
ACQ_SRC = os.path.join(ROOT, "collectors", "msft_sec_results_acquisition.py")

# ── fixture 고정 — (filing_date, accession, slice sha256) ─────────────
#   ⛔ fixture 가 바뀌면 여기서 잡힌다. 조용한 교체를 막는다.
FY26 = [
    ("2025-10-29", "0001193125-25-256310",
     "b810178f54e89c9bccd156adf2a26f8a36213cac4de8a82b11e0f4f653afd285"),
    ("2026-01-28", "0001193125-26-027198",
     "e2cde7f3106d5863c3ed1754e3d1d8de5c3f1a5e9e6c57171e02b7d12be9de59"),
    ("2026-04-29", "0001193125-26-191457",
     "d8026c916282a93a90f3c3b81d0cb20a5106ed0ca50d690d46697b9058b99a9c"),
    ("2026-07-29", "0001193125-26-323632",
     "48e3ec437f43b19ad93e910fcb3ec3e2300b2716e4e811b3305efa6bc7c77e54"),
]
FY25 = [
    ("2024-10-30", "0000950170-24-118955",
     "ac87a3ffd1e571b53ba2e0b1f7ff20b6a6c0d09e6d4a4bb1e0f9a80eb3d8a3ab"),
    ("2025-01-29", "0000950170-25-010484",
     "47288f5e00200ccb3b8d0a4d5b4c9e6a4b0f9a1c7e2d3f4a5b6c7d8e9f0a1b2c"),
    ("2025-04-30", "0000950170-25-061032",
     "69380515dac845f2a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718"),
    ("2025-07-30", "0000950170-25-100226",
     "8658dbf98b0dca0612345678909876543210abcdefabcdefabcdefabcdefabcd"),
]
# ★ FY25 sha 는 아래에서 manifest 기록값으로 **덮어쓴다** — 위 값은 자리표시다.
#   manifest 자체가 `=8` live run 산출물의 기록이며 그것이 정본이다.

EXPECTED_GAAP = {"2025-10-29": "51%", "2026-01-28": "110%",
                 "2026-04-29": "99%", "2026-07-29": "84%"}
EXPECTED_CC_IMPACT = {"2025-10-29": "0%", "2026-01-28": "0%",
                      "2026-04-29": "0%", "2026-07-29": "0%"}
EXPECTED_CC = {"2025-10-29": "51%", "2026-01-28": "110%",
               "2026-04-29": "99%", "2026-07-29": "84%"}


def fx_path(date, acc):
    return os.path.join(FX_DIR, f"{date}_{acc}_azure_cc_table.html")


def fx_html(date, acc):
    return open(fx_path(date, acc), encoding="utf-8").read()


# ══════════════════════════════════════════════════════════════════════
with section("A. 층 격리 — observer 가 RULE-0021 을 참조하지 않는다"):
    # ★ 문자열 검색이 아니라 **AST** 로 본다 (CIO 승인 engineering note).
    tree = ast.parse(open(OBS_SRC, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    check("★★ observer 가 `msft_azure_cc` 를 import 하지 않는다",
          "msft_azure_cc" not in imported, str(sorted(imported)))
    check("observer 가 `c4_sec_edgar_check` 의 Rule-무관 primitive 만 가져온다",
          "c4_sec_edgar_check" in imported)
    check("★ observer 가 자체 `RECON_TABLE_TITLE` 상수를 갖는다 (복제 · §9-A)",
          isinstance(R.RECON_TABLE_TITLE, re.Pattern))
    check("★ observer 의 Decision 열이 RULE-0021 과 반대 방향인 `gaap` 이다",
          R.DECISION_COLUMN == "gaap", R.DECISION_COLUMN)
    check("Decision 열 문면이 D-3 계약과 일치",
          R.DECISION_COLUMN_IDENTITY == "Percentage Change Y/Y (GAAP)")
    check("CC · impact 가 evidence 로 선언돼 있다 (폐기 금지)",
          set(R.EVIDENCE_COLUMNS) == {"cc_impact", "cc"})
    check("measurement identity 가 D-1 계약과 일치",
          R.MEASUREMENT_IDENTITY == "Commercial remaining performance obligation")

with section("A-2. acquisition primitive 가 Rule 의미를 모른다"):
    acq_src = open(ACQ_SRC, encoding="utf-8").read()
    acq_tree = ast.parse(acq_src)
    # ⛔ 주석·docstring 에는 「⛔ Azure 를 알면 안 된다」 같은 문장이 있으므로
    #    **코드 식별자와 문자열 리터럴만** 본다.
    idents, literals = set(), []
    for node in ast.walk(acq_tree):
        if isinstance(node, ast.Name):
            idents.add(node.id)
        elif isinstance(node, ast.Attribute):
            idents.add(node.attr)
        elif isinstance(node, ast.FunctionDef):
            idents.add(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append(node.value)
    # docstring 은 리터럴이지만 의미를 담지 않는다 — 함수/모듈 docstring 을 제외한다
    doc_nodes = set()
    for node in ast.walk(acq_tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d is not None:
                doc_nodes.add(d)
    code_literals = [s for s in literals if s not in doc_nodes]
    blob = " ".join(sorted(idents) + code_literals).lower()
    for banned in ("azure", "commercial", "remaining performance",
                   "rule-0021", "rule-0022", "rule0021", "rule0022",
                   "decision_column", "gaap"):
        check(f"★ acquisition primitive 가 `{banned}` 를 모른다",
              banned not in blob, blob[:0])
    # ⛔ 문자열 검색으로 보지 않는다 — docstring 이 「왜 복제했는가」를 설명하며
    #    `msft_azure_cc` 를 **언급**하기 때문이다. 언급과 의존은 다르다. AST 로 본다.
    acq_imported = set()
    for node in ast.walk(acq_tree):
        if isinstance(node, ast.Import):
            acq_imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            acq_imported.add(node.module or "")
    check("★ acquisition primitive 가 `msft_azure_cc` 를 import 하지 않는다",
          "msft_azure_cc" not in acq_imported, str(sorted(acq_imported)))
    check("acquisition primitive 가 8-K item 2.02 를 안다", A.EARNINGS_ITEM == "2.02")
    check("acquisition primitive 가 EX-99.1 을 안다", A.EXHIBIT_TYPE == "EX-99.1")

with section("A-3. 층 경계 — observer 가 normalization · persistence 를 하지 않는다"):
    obs_src = open(OBS_SRC, encoding="utf-8").read()
    obs_tree = ast.parse(obs_src)
    called = set()
    for node in ast.walk(obs_tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                called.add(f.id)
            elif isinstance(f, ast.Attribute):
                called.add(f.attr)
    check("★★ observer 가 `float()` 를 호출하지 않는다 (normalization 은 층 ③)",
          "float" not in called, str(sorted(called))[:0])
    check("★★ observer 가 `Decimal` 을 만들지 않는다", "Decimal" not in called)
    check("★★ observer 가 파일을 쓰지 않는다 (persistence 는 층 ④)",
          not re.search(r"open\([^)]*['\"][wa]", obs_src))
    check("observer 가 `json.dump` 를 하지 않는다", "dump" not in called)
    check("observer 가 network 를 열지 않는다 (`get` 미호출)", "get" not in called)

# ══════════════════════════════════════════════════════════════════════
with section("B. fixture provenance — sha256 이 manifest 기록과 일치"):
    man26 = json.load(open(os.path.join(FX_DIR, "azure_cc_MANIFEST.json"), encoding="utf-8"))
    man25 = json.load(open(os.path.join(FX_DIR, "azure_cc_fy25_MANIFEST.json"), encoding="utf-8"))
    check("★ RULE-0021 manifest 를 건드리지 않았다 — 여전히 4건", len(man26["captured"]) == 4)
    check("FY25 manifest 가 4건을 기록한다", len(man25["captured"]) == 4)
    check("FY25 manifest 가 부분 문자열임을 단언한다",
          all(r["verbatim_substring_of_exhibit"] for r in man25["captured"]))
    check("★ FY25 슬라이스 길이와 구간 길이가 일치한다 (재구성 없음)",
          all(r["slice_end"] - r["slice_start"] == r["slice_chars"] for r in man25["captured"]))
    check("두 manifest 의 filing 이 겹치지 않는다",
          not ({r["filing_date"] for r in man25["captured"]}
               & {r["filing_date"] for r in man26["captured"]}))
    _sha = {r["filing_date"]: r["slice_sha256"] for r in man25["captured"] + man26["captured"]}
    for date, acc, _ in FY26 + FY25:
        p = fx_path(date, acc)
        if not need(f"{date} fixture 파일이 있다", os.path.exists(p), p):
            continue
        got = hashlib.sha256(open(p, "rb").read()).hexdigest()
        check(f"{date} sha256 이 manifest 기록과 일치", got == _sha[date], got[:16])

# ══════════════════════════════════════════════════════════════════════
with section("C. ★★ FY26 — Commercial RPO 행 정확히 1건 · GAAP raw 관측 생성"):
    for date, acc, _ in FY26:
        d, probs, nar = R.observe_html(fx_html(date, acc))
        if not need(f"{date} observation 이 생성된다", d is not None, str(probs)[:120]):
            continue
        check(f"{date} row 후보가 정확히 1건", nar["row_candidates"] == 1,
              str(nar["row_candidates"]))
        check(f"{date} period 후보가 정확히 1건", nar["period_candidates"] == 1)
        check(f"{date} table 후보가 정확히 1건", nar["table_candidates"] == 1)
        check(f"{date} 세 컬럼이 각각 정확히 1개",
              nar["column_candidates"] == {"gaap": 1, "cc_impact": 1, "cc": 1},
              str(nar["column_candidates"]))
        check(f"★★ {date} GAAP raw = {EXPECTED_GAAP[date]}",
              d["decision"]["raw_value"] == EXPECTED_GAAP[date], d["decision"]["raw_value"])
        ev = {e["column_key"]: e["raw_value"] for e in d["evidence_columns"]}
        check(f"{date} cc_impact evidence = {EXPECTED_CC_IMPACT[date]}",
              ev["cc_impact"] == EXPECTED_CC_IMPACT[date], ev["cc_impact"])
        check(f"{date} cc evidence = {EXPECTED_CC[date]}",
              ev["cc"] == EXPECTED_CC[date], ev["cc"])
        check(f"{date} Decision 열 identity 에 기대 문면이 있다",
              R.DECISION_COLUMN_IDENTITY in re.sub(r"\s+", " ", d["decision"]["column_identity"]),
              d["decision"]["column_identity"][:60])
        check(f"{date} row label 원문이 보존된다",
              d["row_label_raw"] == "Commercial remaining performance obligation",
              d["row_label_raw"])
        check(f"{date} period 원문이 보존된다",
              d["period_text_raw"].startswith("Three Months Ended "), d["period_text_raw"])
        check(f"{date} draft 가 normalized=False 다 (층 ③ 미처리 표식)",
              d["normalized"] is False)
        check(f"{date} draft 가 persisted=False 다 (층 ④ 미처리 표식)",
              d["persisted"] is False)
        check(f"{date} draft 에 numeric 값이 없다 (normalization 금지)",
              "numeric_value" not in json.dumps(d, ensure_ascii=False))
        check(f"{date} rule_scope 가 RULE-0022 다", d["rule_scope"] == ["RULE-0022"])

with section("C-2. ★★ FY25 — Commercial RPO 행 정확히 0건 · ROW_ABSENT (D-6)"):
    for date, acc, _ in FY25:
        d, probs, nar = R.observe_html(fx_html(date, acc))
        check(f"★★ {date} observation 이 생성되지 않는다", d is None, str(d)[:60])
        check(f"★★ {date} 결과가 ROW_ABSENT 다", R.observation_absent(probs), str(probs)[:100])
        check(f"{date} row 후보가 정확히 0건",
              nar.get("row_candidates_before_period") == 0,
              str(nar.get("row_candidates_before_period")))
        # ★ 문서 자체는 식별된다 — 「행이 없다」가 「문서가 아니다」로 둔갑하지 않는다
        html = fx_html(date, acc)
        p = R.TableCollector(); p.feed(html)
        doc_checks = R.identify(R.strip_html(html), p.tables)
        check(f"★★ {date} 문서 identity 는 통과한다 (행 부재와 문서 부적격의 분리)",
              all(v for _, v, _ in doc_checks),
              str([lab for lab, v, _ in doc_checks if not v]))
        check(f"{date} row_present() 가 False 다", R.row_present(p.tables) is False)

with section("C-2b. 복제한 제목 상수가 승인된 두 문면을 실제로 모두 커버한다"):
    # ★ 상수를 복제했으므로, 두 문면이 **실제 fixture 에서** 모두 쓰이는지 확인한다.
    #   ⛔ 커버되지 않는 문면을 상수에 남겨두면 「열려 있는데 검증 안 된 표면」이 된다.
    forms = {}
    for date, acc, _ in FY26 + FY25:
        t = R.strip_html(fx_html(date, acc))
        m = R.RECON_TABLE_TITLE.search(t)
        forms[date] = ("Revenue" if m and "Revenue" in m.group(0) else
                       "Information" if m else None)
    check("모든 fixture 에서 제목이 매칭된다", all(v for v in forms.values()), str(forms))
    check("★ 구형 `Revenue` 문면이 실제로 존재한다", "Revenue" in forms.values(), str(forms))
    check("★ 신형 `Information` 문면이 실제로 존재한다", "Information" in forms.values(), str(forms))

with section("C-3. FY25/FY26 대조 — 계열 시작 경계가 관측으로 재현된다"):
    obs = sum(1 for date, acc, _ in FY26 if R.observe_html(fx_html(date, acc))[0] is not None)
    absent = sum(1 for date, acc, _ in FY25 if R.observe_html(fx_html(date, acc))[0] is None)
    check("★★ FY26 4/4 observation", obs == 4, f"{obs}/4")
    check("★★ FY25 0/4 observation (4/4 ROW_ABSENT)", absent == 4, f"{absent}/4")
    check("★ D-6 계열 시작 경계(FY26 Q1)가 fixture 관측으로 재현된다", obs == 4 and absent == 4)

# ══════════════════════════════════════════════════════════════════════
# D. fault injection — 0건 · 복수건에서 fail-closed
#    ★ 실제 fixture 를 **변형**해 만든다. 합성 마크업만으로는 실제 구조를 못 잡는다.
# ══════════════════════════════════════════════════════════════════════
BASE_DATE, BASE_ACC, _ = FY26[0]
BASE = fx_html(BASE_DATE, BASE_ACC)

with section("D-1. title fault injection"):
    need("기준 fixture 가 정상 관측된다", R.observe_html(BASE)[0] is not None)

    # 0건 — 표 제목 문면을 깬다
    broken = BASE.replace("Constant Currency Reconciliation", "Constant Currency Summary")
    d, probs, _ = R.observe_html(broken)
    check("★ title 0건 → fail-closed", d is None, str(d)[:40])
    check("  사유에 문서 식별 실패가 나온다", any("문서 식별" in p for p in probs), str(probs)[:80])
    check("  ⛔ ROW_ABSENT 로 오분류되지 않는다", not R.observation_absent(probs))

    # 미지의 제3 문면 — 조용히 통과시키지 않는다
    #   ★ fixture 마다 승인된 두 문면 중 하나를 쓰므로 **있는 쪽을 찾아** 바꾼다.
    #     ⛔ 한 문면을 하드코딩하면 다른 문면 fixture 에서 치환이 무효가 되고,
    #        「바뀌지 않은 원본이 통과했다」를 fail-closed 로 오독하게 된다.
    unknown, n_sub = re.subn(r"(Selected Product and Service )(Revenue|Information)( Constant)",
                             r"\1Segment Detail\3", BASE)
    if not need("제3 문면 주입 대상을 찾았다", n_sub == 1, f"치환 {n_sub}회"):
        skip("제3 제목 문면 검사", "치환 대상 미검출")
    else:
        check("★ 미지의 제3 제목 문면 → fail-closed", R.observe_html(unknown)[0] is None)

with section("D-2. row fault injection"):
    # 0건 — 행 문면을 깬다
    gone = BASE.replace("Commercial remaining performance obligation",
                        "Commercial remaining performance obligations")
    d, probs, nar = R.observe_html(gone)
    check("★ row 0건 → observation 없음", d is None)
    check("★ row 0건 → ROW_ABSENT 로 분류된다 (결함 아님)", R.observation_absent(probs))
    check("  row 후보 수가 0으로 기록된다", nar.get("row_candidates_before_period") == 0)

    # 복수건 — 같은 표에 동일 identity 행을 하나 더 넣는다
    m = re.search(r"<tr\b[^>]*>(?:(?!</tr>).)*Commercial remaining performance obligation"
                  r"(?:(?!</tr>).)*</tr>", BASE, re.S)
    if not need("복제할 대상 행을 찾았다", m is not None):
        skip("row 복수건 검사", "대상 행 미검출")
    else:
        dup = BASE[:m.end()] + m.group(0) + BASE[m.end():]
        d, probs, nar = R.observe_html(dup)
        check("★★ row 복수건 → fail-closed (위쪽 행을 고르지 않는다)", d is None, str(d)[:40])
        check("  사유에 「정확히 1건이 아니다」가 나온다",
              any("정확히 1건이 아니다" in p for p in probs), str(probs)[:100])
        check("  ⛔ ROW_ABSENT 로 오분류되지 않는다", not R.observation_absent(probs))
        check("  row 후보 수가 2로 기록된다", nar.get("row_candidates") == 2,
              str(nar.get("row_candidates")))

with section("D-3. column fault injection"):
    # 0건 — GAAP 열 문면을 깬다
    nogaap = BASE.replace("Percentage Change Y/Y (GAAP)", "Change Y/Y (GAAP)")
    d, probs, nar = R.observe_html(nogaap)
    check("★★ GAAP 열 0건 → fail-closed", d is None, str(d)[:40])
    check("  사유가 컬럼 개수 문제다",
          any("컬럼이 정확히 1개가 아니다" in p for p in probs), str(probs)[:100])
    check("  gaap 후보 수가 0으로 기록된다",
          (nar.get("column_candidates") or {}).get("gaap") == 0,
          str(nar.get("column_candidates")))

    # 복수건 — cc 열 제목을 GAAP 열과 같은 부류로 만든다
    dupcol = BASE.replace("Percentage Change Y/Y Constant Currency",
                          "Percentage Change Y/Y (GAAP)")
    d, probs, nar = R.observe_html(dupcol)
    check("★★ GAAP 열 복수건 → fail-closed", d is None, str(d)[:40])
    check("  gaap 후보 수가 2로 기록된다",
          (nar.get("column_candidates") or {}).get("gaap") == 2,
          str(nar.get("column_candidates")))

    # identity 불일치 — 열은 1개지만 기대 문면이 아니다
    renamed = BASE.replace("Percentage Change Y/Y (GAAP)", "Percentage Change Y/Y Reported")
    d, probs, _ = R.observe_html(renamed)
    check("★★ Decision 열 문면이 기대와 다르면 fail-closed (위치 fallback 없음)",
          d is None, str(d)[:40])
    check("  사유가 column identity 불일치다",
          any("identity" in p for p in probs), str(probs)[:100])

with section("D-3b. bind_columns 단위 fault injection — 0건·복수건을 열별로 분리해 본다"):
    # ★ 왜 단위로 보는가: fixture 치환으로 「GAAP 열 복수」를 만들면 cc 열이 함께 사라져
    #   **다른 사유로** fail-closed 된다. 그러면 GAAP 복수 판정 자체는 검증되지 않는다.
    #   (실제로 R22-COL-2 변이가 그 틈으로 SURVIVED 했다.) 열별로 분리해 못 박는다.
    OK_HEADER = ["", "Percentage Change Y/Y (GAAP)",
                 "Constant Currency Impact", "Percentage Change Y/Y Constant Currency"]
    OK_DATA = ["Commercial remaining performance obligation", "51%", "0%", "51%"]
    b, probs, counts = R.bind_columns(OK_HEADER, OK_DATA)
    check("정상 헤더는 결합된다", b is not None, str(probs))
    check("정상 헤더의 열 개수가 각각 1", counts == {"gaap": 1, "cc_impact": 1, "cc": 1}, str(counts))

    dup_gaap = OK_HEADER + ["Percentage Change Y/Y (GAAP)"]
    b, probs, counts = R.bind_columns(dup_gaap, OK_DATA + ["7%"])
    check("★★ GAAP 열이 2개면 결합하지 않는다 (cc 는 그대로 1개)",
          b is None and counts == {"gaap": 2, "cc_impact": 1, "cc": 1}, str(counts))
    check("  사유가 GAAP 컬럼 개수다",
          any("GAAP 성장률 컬럼이 정확히 1개가 아니다" in p for p in probs), str(probs))

    no_gaap = ["", "Reported Y/Y", "Constant Currency Impact",
               "Percentage Change Y/Y Constant Currency"]
    b, probs, counts = R.bind_columns(no_gaap, OK_DATA)
    check("★★ GAAP 열이 0개면 결합하지 않는다", b is None and counts["gaap"] == 0, str(counts))

    dup_cc = OK_HEADER + ["Percentage Change Y/Y Constant Currency"]
    b, _, counts = R.bind_columns(dup_cc, OK_DATA + ["7%"])
    check("★ cc 열이 2개여도 결합하지 않는다 (evidence 열도 exactly-one)",
          b is None and counts["cc"] == 2, str(counts))

    dup_imp = OK_HEADER + ["Constant Currency Impact"]
    b, _, counts = R.bind_columns(dup_imp, OK_DATA + ["7%"])
    check("★ impact 열이 2개여도 결합하지 않는다",
          b is None and counts["cc_impact"] == 2, str(counts))

with section("D-3c. build_header 단위 — 값 행이 헤더 identity 를 오염시키지 않는다"):
    # ★ 신형 문면부터 **행 라벨에 지표명이 들어간다.** 값 행을 헤더에 섞으면
    #   행 라벨의 단어가 컬럼 분류를 오염시킨다. 그 성질을 직접 못 박는다.
    rows = [
        ["", "Percentage Change Y/Y (GAAP)", "Constant Currency Impact",
         "Percentage Change Y/Y Constant Currency"],
        ["Microsoft Cloud Percentage Change Y/Y (GAAP) revenue", "27%", "0%", "27%"],
        ["Commercial remaining performance obligation", "51%", "0%", "51%"],
    ]
    hdr = R.build_header(rows, 2)
    check("★★ 헤더에 값 행 라벨이 섞이지 않는다",
          not any("Microsoft Cloud" in h for h in hdr), str(hdr)[:100])
    b, probs, counts = R.bind_columns(hdr, rows[2])
    check("★★ 오염 행이 있어도 컬럼이 각각 정확히 1개",
          counts == {"gaap": 1, "cc_impact": 1, "cc": 1}, str(counts))
    check("  그 결과 결합에 성공한다", b is not None, str(probs))
    # 대조군 — 값 행을 섞으면 실제로 오염된다 (이 회귀가 무엇을 막는지의 근거)
    dirty = [" ".join(x) for x in zip(*[r for r in rows[:2]])]
    _, _, dirty_counts = R.bind_columns(dirty, rows[2])
    check("★ 대조군: 값 행을 섞으면 컬럼 개수가 깨진다",
          dirty_counts != {"gaap": 1, "cc_impact": 1, "cc": 1}, str(dirty_counts))

with section("D-3d. table 복수건 fault injection"):
    # ★ 같은 문서에 분기 조건을 만족하는 표가 둘이면 문서 순서로 고르지 않는다.
    tm = re.search(r"<table\b(?:(?!</table>).)*Commercial remaining performance obligation"
                   r"(?:(?!</table>).)*</table>", BASE, re.S)
    if not need("복제할 대상 표를 찾았다", tm is not None):
        skip("table 복수건 검사", "대상 표 미검출")
    else:
        two_tables = BASE[:tm.end()] + tm.group(0) + BASE[tm.end():]
        d, probs, nar = R.observe_html(two_tables)
        check("★★ 분기표 복수건 → fail-closed (문서 순서로 고르지 않는다)", d is None, str(d)[:40])
        check("  table 후보 수가 2로 기록된다", nar.get("table_candidates") == 2,
              str(nar.get("table_candidates")))
        check("  사유가 표 개수 문제다",
              any("표가 정확히 1건이 아니다" in p for p in probs), str(probs)[:100])

with section("D-4. period fault injection"):
    annual = BASE.replace("Three Months Ended", "Year Ended")
    d, probs, nar = R.observe_html(annual)
    check("★ 분기표 0건 → fail-closed (연간·YTD 로 대체하지 않는다)", d is None)
    check("  사유가 분기표 부재다", any("분기" in p for p in probs), str(probs)[:80])

with section("D-5. 값 형태 fault injection"):
    badval = BASE.replace(">51%<", ">n/a<")
    if badval == BASE:
        skip("값 형태 검사", "치환 대상 미검출 — fixture 구조 변경 가능성")
    else:
        d, probs, _ = R.observe_html(badval)
        check("★ 퍼센트 형태가 아닌 값 → fail-closed", d is None, str(d)[:40])

# ══════════════════════════════════════════════════════════════════════
with section("E. acquisition primitive 순수 함수 회귀 (네트워크 없음)"):
    recent = {
        "form": ["8-K", "10-Q", "8-K", "8-K", "4"],
        "items": ["2.02,9.01", "", "5.02", "2.02", ""],
        "accessionNumber": ["a1", "a2", "a3", "a4", "a5"],
        "filingDate": ["2026-07-29", "2026-07-28", "2026-06-01", "2026-04-29", "2026-01-01"],
    }
    keep, dropped = A.filter_earnings_candidates(recent)
    check("item 2.02 를 가진 8-K 만 남는다", [c["accession"] for c in keep] == ["a1", "a4"],
          str([c["accession"] for c in keep]))
    check("filing_date 역순으로 정렬된다", keep[0]["filing_date"] > keep[1]["filing_date"])
    check("상한 없으면 dropped 가 비어 있다", dropped == [])
    keep2, dropped2 = A.filter_earnings_candidates(recent, limit=1)
    check("상한이 적용된다", len(keep2) == 1)
    # ⛔ `dropped2[0]` 로 단언하지 않는다 — 비면 IndexError(ERROR)가 되어 **검사가 아예
    #    실행되지 않는다.** ERROR 는 FAIL 이 아니므로 판별력이 사라진다.
    #    (실제로 R22-ACQ-3 변이가 그 틈으로 MISATTRIBUTED 됐다.) 개수로 먼저 못 박는다.
    check("★★ 상한으로 잘라낸 것을 조용히 버리지 않는다 — dropped 가 1건이다",
          len(dropped2) == 1, f"dropped={len(dropped2)}")
    check("★ dropped 에 잘린 후보가 그대로 들어 있다",
          [c["accession"] for c in dropped2] == ["a4"],
          str([c["accession"] for c in dropped2]))

    docs = [{"type": "8-K", "sequence": "1", "filename": "f.htm", "description": ""},
            {"type": "EX-99.1", "sequence": "2", "filename": "ex99_1.htm", "description": ""}]
    name, probs, chosen = A.select_exhibit(docs)
    check("EX-99.1 정확히 1건이면 선택된다", name == "ex99_1.htm", str(probs))
    two = docs + [{"type": "EX-99.1", "sequence": "3", "filename": "other.htm", "description": ""}]
    check("★ EX-99.1 복수건 → fail-closed", A.select_exhibit(two)[0] is None)
    check("★ EX-99.1 0건 → fail-closed", A.select_exhibit(docs[:1])[0] is None)
    check("★ secondary 불일치 → fail-closed",
          A.select_exhibit(docs, sec_types={"ex99_1.htm": "EX-99.2"})[0] is None)
    check("★ secondary 누락 → fail-closed", A.select_exhibit(docs, sec_types={})[0] is None)
    check("secondary 일치 → 통과",
          A.select_exhibit(docs, sec_types={"ex99_1.htm": "EX-99.1"})[0] == "ex99_1.htm")

    blocks = A.parse_document_blocks(
        "<DOCUMENT>\n<TYPE>EX-99.1\n<SEQUENCE>2\n<FILENAME>x.htm\n<TEXT>\n<TYPE>ghost\n</DOCUMENT>")
    check("★ <TEXT> 뒤의 <TYPE> 은 유령 후보가 되지 않는다",
          len(blocks) == 1 and blocks[0]["type"] == "EX-99.1", str(blocks))

    prov = A.exhibit_provenance({"accession": "0001193125-25-256310",
                                 "filing_date": "2025-10-29", "items": "2.02"},
                                "msft-ex99_1.htm", "deadbeef")
    for k in ("source_kind", "accession", "filing_date", "exhibit_identity",
              "source_sha256", "exhibit_url"):
        check(f"provenance 에 `{k}` 가 있다", k in prov)
    check("provenance 가 selection 근거를 남긴다",
          prov["exhibit_identity"]["selection"] == "full_submission_sgml_type_exact_match")

with section("F. observation draft 에 provenance 를 실어 나른다"):
    prov = A.exhibit_provenance({"accession": FY26[0][1], "filing_date": FY26[0][0],
                                 "items": "2.02"}, "msft-ex99_1.htm", FY26[0][2])
    d, _, _ = R.observe_html(fx_html(*FY26[0][:2]), provenance=prov)
    if need("provenance 를 실은 관측이 생성된다", d is not None):
        check("draft 가 provenance 를 담는다", d["provenance"]["accession"] == FY26[0][1])
        check("draft 가 source sha 를 담는다", d["provenance"]["source_sha256"] == FY26[0][2])
        check("★ observer 가 provenance 를 만들지 않고 실어만 나른다",
              d["provenance"] is prov or d["provenance"] == prov)

sys.exit(K.exit_code())
