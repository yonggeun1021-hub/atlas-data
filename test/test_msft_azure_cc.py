#!/usr/bin/env python3
"""RULE-0021 Azure cc 추출 회귀 (CIO 승인 2026-08-15).

★ 경계 — 이 회귀가 증명하는 것과 못 하는 것
   증명한다 : 표 → 행렬 → **의미 기반 컬럼 결합** → cc 값 선택, 문서 식별,
             GAAP/cc 혼용 방지, fail-closed.
   증명 못 한다 : SEC EDGAR 가 실제로 내보내는 마크업. 그것은 live run 이 담당한다.

⛔ **특정 숫자에 fixture 를 맞추지 않는다** (CIO 지시).
   Azure 의 cc 값은 분기마다 달라진다 — 예: FY2025 Q3 은 GAAP 33% / cc 35%,
   FY2025 Q4 는 GAAP 39% / cc 39%(영향 0%). 따라서 검증 대상은 값이 아니라
   **「Azure 행에서 cc 컬럼을 정확히 선택하는가」** 라는 구조다.
⛔ 네트워크를 쓰지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
import msft_azure_cc as M                                          # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


# ── 합성 문서 ─────────────────────────────────────────────────────
# ⛔ 공식 캡처가 아니다. 표의 **논리 구조**(제목·행 이름·컬럼 이름)만 실제와 맞추고
#    마크업은 EDGAR 에서 흔한 변형을 섞었다.
def doc(gaap, impact, cc, spacer=False, split_header=False, extra_rows=True):
    tag, sp = "td", ("<td>&nbsp;</td>" if spacer else "")
    heads = ["Percentage Change Y/Y (GAAP)", "Constant Currency Impact",
             "Percentage Change Y/Y Constant Currency"]
    if split_header:
        # 헤더를 두 줄로 쪼갠다 — 첫 단어 / 나머지
        parts = [(h.split(" ", 1) + [""])[:2] for h in heads]
        top = "".join(f"<{tag}>{a}</{tag}>{sp}" for a, _ in parts)
        bot = "".join(f"<{tag}>{b}</{tag}>{sp}" for _, b in parts)
        hrows = (f"<tr><{tag}></{tag}>{sp}{top}</tr><tr><{tag}></{tag}>{sp}{bot}</tr>")
    else:
        hrows = "<tr><td></td>%s%s</tr>" % (sp, "".join(f"<td>{h}</td>{sp}" for h in heads))
    other = ("<tr><td>Office 365 Commercial</td>%s<td>15%%</td>%s<td>1%%</td>%s"
             "<td>16%%</td>%s</tr>" % (sp, sp, sp, sp)) if extra_rows else ""
    azure = ("<tr><td>Azure and other cloud services</td>%s<td>%s</td>%s<td>%s</td>%s"
             "<td>%s</td>%s</tr>" % (sp, gaap, sp, impact, sp, cc, sp))
    return f"""<html><body>
<h1>Microsoft Cloud Strength Drives Results</h1>
<p>Azure surpassed $75 billion in revenue, up 34 percent</p>
<h3>Selected Product and Service Revenue Constant Currency Reconciliation</h3>
<table>{hrows}{other}{azure}</table>
</body></html>"""


def run(html):
    p = M.TableCollector()
    p.feed(html)
    for ti, rows, ri in M.find_azure_table(p.tables):
        b, _ = M.bind_columns(M.build_header(rows, ri), rows[ri])
        if b:
            return b
    return None


print("A-1 문서 식별 — 내용으로 판정")


def ident(html):
    """identify 는 결합과 같은 predicate 를 쓰므로 표까지 넘긴다."""
    p = M.TableCollector()
    p.feed(html)
    return M.identify(M.strip_html(html), p.tables)


ck = ident(doc("39%", "0%", "39%"))
check("실적 발표문으로 식별", all(v for _, v, _ in ck) and len(ck) == 3)
for nm, d in (("표 제목이 없는 문서",
               "<html><body><p>Microsoft Azure and other cloud services grew.</p></body></html>"),
              ("Azure 항목이 없는 문서",
               "<html><body><h3>Selected Product and Service Revenue Constant Currency "
               "Reconciliation</h3><p>Microsoft</p></body></html>")):
    check(f"{nm} 거부", not all(v for _, v, _ in ident(d)))

print("A-2 cc 컬럼 선택 — 값이 아니라 구조를 검증한다")
CASES = [("FY2025 Q3 형태 (GAAP 33 · 영향 2 · cc 35)", "33%", "2%", "35%"),
         ("FY2025 Q4 형태 (영향 0 · GAAP == cc)", "39%", "0%", "39%"),
         ("음(-) 영향", "40%", "-3%", "37%"),
         ("소수점", "38.5%", "0.5%", "39.0%")]
for label, g, i, c in CASES:
    for variant, kw in (("기본", {}), ("spacer 셀", {"spacer": True}),
                        ("헤더 2줄 분할", {"split_header": True}),
                        ("분할+spacer", {"split_header": True, "spacer": True})):
        b = run(doc(g, i, c, **kw))
        check(f"[{variant}] {label} → cc={c}", b is not None and b["cc"] == c,
              str(b and b["cc"]))
        check(f"[{variant}] {label} → GAAP 을 cc 로 잘못 집지 않는다",
              b is not None and b["gaap"] == g and b["cc"] != b["gaap"] or g == c)
        check(f"[{variant}] {label} → 영향 컬럼을 cc 로 잘못 집지 않는다",
              b is not None and b["cc_impact"] == i and b["cc"] != i or i == c)

print("A-3 ★ GAAP 과 cc 가 같은 분기만으로는 판별력이 없다 — 다른 분기가 반드시 필요")
b_same = run(doc("39%", "0%", "39%"))
b_diff = run(doc("33%", "2%", "35%"))
check("같은 분기: 두 컬럼 값이 동일해 구별 불가", b_same["gaap"] == b_same["cc"])
check("다른 분기: 두 컬럼이 실제로 갈린다", b_diff["gaap"] != b_diff["cc"])
check("★ 다른 분기에서 cc 를 정확히 골랐다", b_diff["cc"] == "35%")
check("★ 다른 분기에서 GAAP 을 cc 로 집지 않았다", b_diff["cc"] != b_diff["gaap"])

print("A-4 fail-closed — 구조가 다르면 값을 만들지 않는다")
NEG = {
    "cc 컬럼이 없다": "<html><body><h3>Selected Product and Service Revenue Constant Currency "
                  "Reconciliation</h3><table><tr><td></td><td>Percentage Change Y/Y (GAAP)</td>"
                  "</tr><tr><td>Azure and other cloud services</td><td>39%</td></tr>"
                  "</table></body></html>",
    "Azure 행이 없다": doc("39%", "0%", "39%").replace("Azure and other cloud services",
                                                   "Other cloud services"),
    "값이 퍼센트 형태가 아니다": doc("n/a", "0%", "39%"),
}
for nm, d in NEG.items():
    check(f"★ {nm} → 결합하지 않는다", run(d) is None)
check("★ cc 컬럼이 2개면 모호로 두고 결합하지 않는다",
      M.bind_columns(["", "Percentage Change Y/Y Constant Currency",
                      "Percentage Change Y/Y Constant Currency"],
                     ["Azure and other cloud services", "39%", "38%"])[0] is None)

print("A-5 계약 준수 정적 확인")
src = open(os.path.join(ROOT, "collectors", "msft_azure_cc.py"), encoding="utf-8").read()
body = src.split('"""', 2)[2]
# ⛔ 문자열 검색으로는 「⛔ 45% 기준선은 evaluator 층」 이라는 **금지 문구**와
#    실제 비교 로직을 구별할 수 없다. AST 로 **연산에 쓰인 숫자**만 본다.
import ast
tree = ast.parse(src)
_docstrings = set()
for node in ast.walk(tree):
    if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
        d = ast.get_docstring(node)
        if d:
            _docstrings.add(d)
_op_nums = []
for node in ast.walk(tree):
    if isinstance(node, (ast.Compare, ast.BinOp)):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, (int, float)) \
                    and not isinstance(sub.value, bool):
                _op_nums.append(sub.value)
check("★ 비교·연산에 쓰인 숫자에 45 가 없다 (기준선 판정 부재)",
      45 not in _op_nums, str(sorted(set(_op_nums))))
check("★ 비교·연산에 쓰인 숫자에 3 이 없다 (3%p 판정 부재)",
      3 not in _op_nums, str(sorted(set(_op_nums))))
check("★ 그래도 금지 문구는 코드에 남아 있다 (문서화는 유지)",
      "45% 기준선" in src)
check("★ evaluator 판정 어휘가 실행 코드에 없다",
      not re.search(r"breach|triggered|is_violation", body))
check("★ 명명된 Azure 행 정규식이 있다", "AZURE_ROW" in body)
check("★ EX-99.1 을 type 으로 지목한다", 'EXHIBIT_TYPE = "EX-99.1"' in src)
check("★ items 2.02 로 discovery 를 좁힌다", 'EARNINGS_ITEM = "2.02"' in src)
check("★ 상한 초과분을 로그에 남긴다", "조회하지 않은 것" in body)
check("★ open() 쓰기 모드가 없다", not re.search(r"open\([^)]*['\"][wa]", body))
check("★ 이 회귀는 네트워크를 쓰지 않는다",
      ("url" + "lib") not in open(os.path.abspath(__file__), encoding="utf-8").read()
      .replace('("url" + "lib")', ""))

# ══════════════════════════════════════════════════════════════════════
# B. Exhibit identity 회귀 (CIO 판정 2026-08-15 — 6종)
#
#   근본 원인: `index.json` 의 `type` 을 SEC document type 으로 오인했다.
#   실측 결과 그 필드는 `text.gif` · `compressed.gif` 두 값뿐인 **디렉터리 아이콘**이다.
#   ★ Primary identity source = 제출문 SGML 의 `<DOCUMENT>` 블록 `<TYPE>`
#   ★ Secondary cross-check   = `{accession}-index.html` 의 Type 컬럼
#   ⛔ 파일명(`ex99_1`)은 hint 전용
#
#   ⛔ 아래 fixture 는 공식 캡처가 아니다. **블록 구조**만 실제와 맞췄다.
# ══════════════════════════════════════════════════════════════════════
import io                                                        # noqa: E402
import contextlib                                                # noqa: E402


def sgml(*blocks):
    """`-index-headers.html` / full submission `.txt` 공통 SGML 헤더를 만든다."""
    out = ["<SEC-HEADER>ACCEPTANCE-DATETIME 20260129160500</SEC-HEADER>",
           "CONFORMED SUBMISSION TYPE:\t8-K"]
    for t, seq, fn, desc in blocks:
        out.append(f"<DOCUMENT>\n<TYPE>{t}\n<SEQUENCE>{seq}\n<FILENAME>{fn}\n"
                   f"<DESCRIPTION>{desc}\n<TEXT>\n<html>…</html>\n</TEXT>\n</DOCUMENT>")
    return "\n".join(out)


# 실측 구조를 따른 정상 제출문 — 8-K 본문 + EX-99.1 + XBRL 부속
REAL = sgml(("8-K", "1", "msft-20260129.htm", "8-K"),
            ("EX-99.1", "2", "msft-ex99_1.htm", "EX-99.1"),
            ("GRAPHIC", "3", "logo.jpg", "GRAPHIC"),
            ("EX-101.SCH", "4", "msft-20260129.xsd", "XBRL TAXONOMY EXTENSION SCHEMA"))

print("B-0 SGML <DOCUMENT> 파싱")
_d = M.parse_document_blocks(REAL)
check("블록 4건을 뽑는다", len(_d) == 4, str(len(_d)))
check("TYPE/SEQUENCE/FILENAME/DESCRIPTION 4요소를 모두 뽑는다",
      _d[1] == {"type": "EX-99.1", "sequence": "2",
                "filename": "msft-ex99_1.htm", "description": "EX-99.1"}, str(_d[1]))

print("B-1 ★ index.json 의 type(디렉터리 아이콘)을 identity 로 쓰지 않는다")
# ① 정적 — ⛔ 문자열 검색은 **금지 문구 주석**과 실제 조회를 구별하지 못한다
#    (앞서 45%/3%p 에서 겪은 것과 같은 결함). AST 의 **문자열 리터럴**만 본다.
#    주석은 AST 에 없으므로 이 검사는 코드만 본다.
_str_lits = [n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and n.value not in _docstrings]
_urlish = [s for s in _str_lits if "index.json" in s]
check("★ 코드(주석 아님)가 index.json URL 을 만들지 않는다", not _urlish, str(_urlish))
check("★ 그래도 금지 사유는 주석에 남아 있다 (문서화 유지)", "index.json" in src)
check("★ index.json 의 type 을 읽는 코드가 없다", 'it.get("type")' not in body)
# ★ CIO 판정 2026-08-16 — primary 는 **full submission `.txt`** 다.
#   `-index-headers.html` 대체는 불승인됐다. 「현재 표본에서 같으니 동등」을 막는다.
check("★ primary 취득처가 full submission .txt 다",
      any(s.endswith("}.txt") or s.endswith(".txt") for s in _str_lits),
      str([s for s in _str_lits if ".txt" in s]))
check("★ ⛔ -index-headers.html 을 조회하지 않는다 (CIO 불승인)",
      not any("index-headers" in s for s in _str_lits),
      str([s for s in _str_lits if "index-headers" in s]))
check("★ 그래도 불승인 사유는 주석에 남아 있다 (문서화 유지)",
      "index-headers.html" in src and "불승인" in src)
# ② 행동 — 아이콘 값은 identity 를 만족시킬 수 없다
ICONS = [{"type": "text.gif", "sequence": "", "filename": "msft-ex99_1.htm",
          "description": ""},
         {"type": "compressed.gif", "sequence": "", "filename": "msft-20260129.htm",
          "description": ""}]
_t, _p, _ = M.select_exhibit(ICONS)
check("★ type 이 아이콘 값(text.gif 등)뿐이면 식별하지 않는다", _t is None, str(_t))
check("★ 그때 사유는 '정확히 1건이 아니다'", any("1건이 아니" in x for x in _p), str(_p))

print("B-2 ★ filename 이 달라도 <TYPE>EX-99.1 이면 식별한다")
FNS = ("pressrelease.htm", "a8kexhibit.htm", "d123456dex991.htm", "q2fy26.htm")
for fn in FNS:
    docs = sgml(("8-K", "1", "body.htm", "8-K"),
                ("EX-99.1", "2", fn, "Press Release"))
    t, p, ch = M.select_exhibit(M.parse_document_blocks(docs))
    check(f"filename={fn} → TYPE 으로 식별", t == fn, f"{t!r} {p}")
# ★ hint 비의존 증명은 **hint 에 걸리지 않는 파일명**에서만 성립한다.
#   `d123456dex991.htm` 은 `ex.?99.?1` 에 걸리므로 이 증명에 쓸 수 없다 — 구분한다.
_no_hint = [f for f in FNS if not M.FILENAME_HINT.search(f)]
check("★ hint 에 걸리지 않는 파일명이 실제로 존재한다 (아래 증명이 공허하지 않다)",
      len(_no_hint) >= 2, str(_no_hint))
for fn in _no_hint:
    docs = sgml(("EX-99.1", "1", fn, "Press Release"))
    check(f"★ hint 불일치 filename={fn} 인데도 식별됐다 (hint 비의존 증명)",
          M.select_exhibit(M.parse_document_blocks(docs))[0] == fn)

print("B-3 ★ filename 이 ex99_1 이어도 TYPE 이 다르면 거부한다")
for bad_type in ("EX-99.2", "EX-99", "8-K", "GRAPHIC", "EX-99.1A"):
    docs = sgml(("8-K", "1", "body.htm", "8-K"),
                (bad_type, "2", "msft-ex99_1.htm", "Press Release"))
    t, p, _ = M.select_exhibit(M.parse_document_blocks(docs))
    check(f"TYPE={bad_type} · filename=msft-ex99_1.htm → 거부", t is None, f"{t!r}")
check("★ 위 파일명들은 hint 정규식에는 걸린다 (그래도 거부됐다 = hint 비의존)",
      bool(M.FILENAME_HINT.search("msft-ex99_1.htm")))

print("B-4 ★ 0건 / 2건 이상 fail-closed — 「EX-99.1 이면 무조건 한 파일」 가정 금지")
_zero = sgml(("8-K", "1", "body.htm", "8-K"), ("EX-99.2", "2", "x.htm", "Other"))
t, p, _ = M.select_exhibit(M.parse_document_blocks(_zero))
check("★ 0건 → 거부", t is None and any("0건" in x for x in p), f"{t!r} {p}")
_two = sgml(("8-K", "1", "body.htm", "8-K"),
            ("EX-99.1", "2", "press.htm", "Press Release"),
            ("EX-99.1", "3", "slides.htm", "Presentation"))
t, p, _ = M.select_exhibit(M.parse_document_blocks(_two))
check("★ 2건 → 임의 선택하지 않고 거부", t is None and any("2건" in x for x in p), f"{t!r} {p}")
check("★ 2건일 때 첫 번째를 몰래 고르지 않았다", t != "press.htm")
check("★ 2건일 때 hint 에 맞는 쪽을 몰래 고르지 않았다", t != "slides.htm")
_three = sgml(("EX-99.1", "1", "a.htm", ""), ("EX-99.1", "2", "b.htm", ""),
              ("EX-99.1", "3", "c.htm", ""))
check("★ 3건 → 거부", M.select_exhibit(M.parse_document_blocks(_three))[0] is None)
_nofn = [{"type": "EX-99.1", "sequence": "2", "filename": "", "description": "EX-99.1"}]
t, p, _ = M.select_exhibit(_nofn)
check("★ FILENAME 이 비면 거부", t is None and any("FILENAME" in x for x in p), str(p))
# 양성 대조 — 위 거부들이 「무조건 거부」가 아님을 보인다
t, p, _ = M.select_exhibit(M.parse_document_blocks(REAL))
check("★ 양성 대조: 정상 제출문은 식별된다", t == "msft-ex99_1.htm", f"{t!r} {p}")

print("B-5 ★ 실패 시 후보 집합과 판정 신호를 함께 남긴다 (collector 공통 규칙)")


def cap(docs, reason):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        M.log_candidates(docs, reason)
    return buf.getvalue()


_out = cap(M.parse_document_blocks(REAL), "테스트 사유")
check("사유가 남는다", "테스트 사유" in _out)
check("후보 건수가 남는다", "후보 전체 4건" in _out, _out)
for _f in ("msft-20260129.htm", "msft-ex99_1.htm", "logo.jpg", "msft-20260129.xsd"):
    check(f"후보 {_f} 가 로그에 있다", _f in _out)
for _sig in ("TYPE=", "SEQ=", "FILE=", "DESC="):
    check(f"판정 신호 {_sig} 가 로그에 있다", _sig in _out)
check("★ 걸러진 것(EX-99.1 아닌 것)도 남는다 — 결과만 남기지 않는다",
      "'GRAPHIC'" in _out and "'EX-101.SCH'" in _out, _out)
_many = [{"type": f"T{i}", "sequence": str(i), "filename": f"f{i}.htm",
          "description": "d"} for i in range(M.CAND_LOG_LIMIT + 7)]
_out2 = cap(_many, "상한 초과")
check("★ 상한까지는 남긴다", f"f{M.CAND_LOG_LIMIT - 1}.htm" in _out2)
check("★ 상한 초과분은 조용히 자르지 않고 건수를 남긴다", "나머지 7건 생략" in _out2, _out2)
check("★ 실패 경로가 log_candidates 를 실제로 호출한다", body.count("log_candidates(") >= 2,
      str(body.count("log_candidates(")))

print("B-6 ★ primary(<TYPE>) 와 secondary(index.html Type) 충돌 시 거부")
_docs = M.parse_document_blocks(REAL)
check("★ 일치하면 통과", M.select_exhibit(
    _docs, {"msft-ex99_1.htm": "EX-99.1", "msft-20260129.htm": "8-K"})[0]
    == "msft-ex99_1.htm")
for bad in ("EX-99.2", "8-K", "GRAPHIC", "text.gif", ""):
    t, p, _ = M.select_exhibit(_docs, {"msft-ex99_1.htm": bad})
    check(f"★ secondary Type={bad!r} 충돌 → 거부", t is None, f"{t!r}")
    check(f"★ secondary Type={bad!r} 사유가 충돌/미발견으로 기록된다",
          any(("충돌" in x) or ("찾지 못했다" in x) for x in p), str(p))
t, p, _ = M.select_exhibit(_docs, {"other.htm": "EX-99.1"})
check("★ secondary 에 해당 파일이 없으면 추정하지 않고 거부",
      t is None and any("찾지 못했다" in x for x in p), f"{t!r} {p}")
check("★ secondary 가 빈 dict 여도 통과시키지 않는다", M.select_exhibit(_docs, {})[0] is None)
# secondary 파서 — Type 컬럼을 의미로 읽는다
_ihtml = ("<html><body><table><tr><th>Seq</th><th>Description</th><th>Document</th>"
          "<th>Type</th><th>Size</th></tr>"
          "<tr><td>1</td><td>8-K</td><td>msft-20260129.htm</td><td>8-K</td><td>1</td></tr>"
          "<tr><td>2</td><td>EX-99.1</td><td>msft-ex99_1.htm&nbsp;&nbsp;iXBRL</td>"
          "<td>EX-99.1</td><td>2</td></tr></table></body></html>")
_st = M.index_html_types(_ihtml)
check("★ index.html Type 컬럼을 filename→type 으로 읽는다",
      _st.get("msft-ex99_1.htm") == "EX-99.1", str(_st))
check("★ iXBRL 꼬리표를 파일명에서 떼어낸다", "msft-ex99_1.htm" in _st, str(_st))
check("★ 위 secondary 로 실제 식별이 성립한다",
      M.select_exhibit(_docs, _st)[0] == "msft-ex99_1.htm")

print("B-7 ★ full submission .txt 의 **본문**이 유령 후보를 만들지 않는다")
# ★ 이 위험은 `-index-headers.html`(헤더만) 에는 없고 full `.txt`(본문 포함) 에만 있다.
#   계약을 full `.txt` 로 되돌리면서 새로 생긴 경로이므로 별도로 막는다.
#   ⛔ `<TYPE>` 을 문서 전체에서 훑으면 본문 안의 `<TYPE>` 이 후보가 된다.
_body_noise = (
    "<SEC-DOCUMENT>0001193125-26-323632.txt\n"
    "<DOCUMENT>\n<TYPE>8-K\n<SEQUENCE>1\n<FILENAME>msft-20260129.htm\n"
    "<DESCRIPTION>8-K\n<TEXT>\n"
    "<html><body><p>see exhibit</p></body></html>\n</TEXT>\n</DOCUMENT>\n"
    "<DOCUMENT>\n<TYPE>EX-99.1\n<SEQUENCE>2\n<FILENAME>msft-ex99_1.htm\n"
    "<DESCRIPTION>EX-99.1\n<TEXT>\n"
    # ↓ 본문 안에 SGML 처럼 보이는 문자열을 심는다 (XBRL·escape 되지 않은 마크업 모사)
    "<html><body><pre>&lt;TYPE&gt;EX-99.1</pre>\n"
    "<TYPE>EX-99.1\n<SEQUENCE>99\n<FILENAME>decoy.htm\n"
    "<xbrli:unit><TYPE>EX-99.1</TYPE></xbrli:unit>\n"
    "</body></html>\n</TEXT>\n</DOCUMENT>\n"
    "<DOCUMENT>\n<TYPE>EX-101.SCH\n<SEQUENCE>3\n<FILENAME>msft-20260129.xsd\n"
    "<DESCRIPTION>XBRL\n<TEXT>\n<schema/>\n</TEXT>\n</DOCUMENT>\n</SEC-DOCUMENT>")
_bd = M.parse_document_blocks(_body_noise)
check("★ <DOCUMENT> 블록 수만큼만 뽑는다 (본문 <TYPE> 무시)", len(_bd) == 3, str(len(_bd)))
check("★ 본문에 심은 decoy.htm 이 후보에 없다",
      not any(d["filename"] == "decoy.htm" for d in _bd), str(_bd))
check("★ 본문 잡음이 있어도 EX-99.1 은 정확히 1건",
      sum(1 for d in _bd if d["type"].upper() == "EX-99.1") == 1)
_t, _p, _ = M.select_exhibit(_bd)
check("★ 본문 잡음이 있어도 올바른 exhibit 을 식별한다", _t == "msft-ex99_1.htm", f"{_t!r} {_p}")
check("★ <TEXT> 앞 헤더만 본다 — SEQUENCE 가 본문 값(99)으로 오염되지 않는다",
      [d["sequence"] for d in _bd] == ["1", "2", "3"], str([d["sequence"] for d in _bd]))
# 본문이 통째로 없어도(= 헤더만 있는 문서) 같은 결과여야 한다 — 파서 일관성
_hdr_only = re.sub(r"<TEXT>.*?</TEXT>", "", _body_noise, flags=re.S | re.I)
check("★ 본문을 제거해도 동일한 블록 목록을 얻는다 (본문 비의존)",
      M.parse_document_blocks(_hdr_only) == _bd, str(M.parse_document_blocks(_hdr_only)))

# ★ `<TEXT>` 절단이 실제로 하는 일 — 헤더에 <TYPE> 이 **없는** 블록에서
#   본문의 <TYPE> 을 끌어다 쓰지 않는다. 절단이 없으면 유령 EX-99.1 이 생겨
#   「정확히 1건」이 2건이 되고, 잘못된 파일을 취득하거나 ambiguity 로 오판한다.
#   ⛔ 없는 것을 본문에서 만들어내지 않는다 (fail-closed 방향).
_hdrless = (
    "<DOCUMENT>\n<TYPE>EX-99.1\n<SEQUENCE>1\n<FILENAME>real.htm\n"
    "<DESCRIPTION>EX-99.1\n<TEXT>\n<html>ok</html>\n</TEXT>\n</DOCUMENT>\n"
    # ↓ 헤더가 손상돼 <TYPE> 이 없는 블록. 본문에는 <TYPE>EX-99.1 이 들어 있다.
    "<DOCUMENT>\n<TEXT>\n<TYPE>EX-99.1\n<SEQUENCE>2\n<FILENAME>phantom.htm\n"
    "</TEXT>\n</DOCUMENT>")
_hl = M.parse_document_blocks(_hdrless)
check("★ 헤더에 <TYPE> 이 없는 블록은 건너뛴다 (본문에서 만들어내지 않는다)",
      len(_hl) == 1, str(_hl))
check("★ 본문의 phantom.htm 이 후보로 올라오지 않는다",
      not any(d["filename"] == "phantom.htm" for d in _hl), str(_hl))
_t2, _p2, _ = M.select_exhibit(_hl)
check("★ 유령 후보로 ambiguity(2건) 오판이 생기지 않는다", _t2 == "real.htm", f"{_t2!r} {_p2}")

# ══════════════════════════════════════════════════════════════════════
# C. ★ 실제 SEC 마크업 회귀 (CIO 승인 2026-08-16)
#
#   앞의 A·B 절은 **합성 마크업**이다. 그것이 이번 Gate 실패를 못 잡은 이유다.
#   이 절의 fixture 는 GitHub Actions 가 SEC 에서 **실제 취득한 원문의 슬라이스**다
#   (run 31922768254 · artifact azure-cc-raw-fixtures · 재구성 없음).
#
#   ★ 우선순위: **실제 SEC evidence > 합성 fixture** (CIO 원칙 2026-08-16)
#      충돌하면 틀린 쪽은 fixture 다.
# ══════════════════════════════════════════════════════════════════════
FX_DIR = os.path.join(ROOT, "collectors", "fixtures")

# (filing_date, accession, slice sha256) — MANIFEST 기록값을 고정한다.
#   ⛔ fixture 가 바뀌면 여기서 잡힌다. 조용한 교체를 막는다.
FIXTURES = [
    ("2025-10-29", "0001193125-25-256310",
     "b810178f54e89c9bccd156adf2a26f8a36213cac4de8a82b11e0f4f653afd285"),
    ("2026-01-28", "0001193125-26-027198",
     "e2cde7f3106d5863c3ed1754e3d1d8de5c3f1a5e9e6c57171e02b7d12be9de59"),
    ("2026-04-29", "0001193125-26-191457",
     "d8026c916282a93a90f3c3b81d0cb20a5106ed0ca50d690d46697b9058b99a9c"),
    ("2026-07-29", "0001193125-26-323632",
     "48e3ec437f43b19ad93e910fcb3ec3e2300b2716e4e811b3305efa6bc7c77e54"),
]
# 기대 관측값 — SEC EX-99.1 원문 · Microsoft IR · CIO 교차검증 3원 일치분.
#   ⚠️ 2026-07-29 은 GAAP == cc (영향 0%) 라 **단독으로는 판별력이 없다.**
EXPECTED = {"2025-10-29": ("40%", "(1)%", "39%"),
            "2026-01-28": ("39%", "(1)%", "38%"),
            "2026-04-29": ("40%", "(1)%", "39%"),
            "2026-07-29": ("43%", "0%", "43%")}
DISCRIMINATING = {d for d, (g, i, c) in EXPECTED.items() if g != c}
OLD_FORM = {"2025-10-29", "2026-01-28"}      # 제목 Revenue · 행 suffix 없음
NEW_FORM = {"2026-04-29", "2026-07-29"}      # 제목 Information · 행 suffix revenue


def fx_path(date, acc):
    return os.path.join(FX_DIR, f"{date}_{acc}_azure_cc_table.html")


def fx_html(date, acc):
    return open(fx_path(date, acc), encoding="utf-8").read()


def extract(html, title_re=None, row_re=None):
    """현재 계약(또는 주어진 문면)으로 관측을 시도한다. (gaap, impact, cc) 또는 사유."""
    import contextlib as _c
    import io as _io
    o_t, o_r = M.RECON_TABLE_TITLE, M.AZURE_ROW
    if title_re is not None:
        M.RECON_TABLE_TITLE = title_re
    if row_re is not None:
        M.AZURE_ROW = row_re
    try:
        p = M.TableCollector()
        p.feed(html)
        with _c.redirect_stdout(_io.StringIO()):
            ck = M.identify(M.strip_html(html), p.tables)
        if not all(v for _, v, _ in ck):
            bad = [n for n, v, _ in ck if not v]
            return f"identify FAIL {bad}"
        cands = M.find_azure_table(p.tables)
        if not cands:
            return "Azure 행 0건"
        ti, rows, ri = cands[0]
        b, probs = M.bind_columns(M.build_header(rows, ri), rows[ri])
        if not b:
            return f"결합 실패 {probs}"
        return (b["gaap"], b["cc_impact"], b["cc"])
    finally:
        M.RECON_TABLE_TITLE, M.AZURE_ROW = o_t, o_r


print("C-0 fixture 무결성 — 원문 슬라이스가 그대로인가")
for date, acc, want in FIXTURES:
    p = fx_path(date, acc)
    check(f"{date} fixture 존재", os.path.exists(p), p)
    if not os.path.exists(p):
        continue
    got = hashlib.sha256(open(p, "rb").read()).hexdigest()
    check(f"{date} sha256 가 MANIFEST 기록과 일치", got == want, got[:16])
_man = json.load(open(os.path.join(FX_DIR, "azure_cc_MANIFEST.json"), encoding="utf-8"))
check("MANIFEST 가 4건을 기록한다", len(_man["captured"]) == 4)
check("★ MANIFEST 가 부분 문자열임을 단언한다",
      all(r["verbatim_substring_of_exhibit"] for r in _man["captured"]))
check("★ 슬라이스 길이와 구간 길이가 일치한다 (재구성 없음)",
      all(r["slice_end"] - r["slice_start"] == r["slice_chars"] for r in _man["captured"]))

print("C-1 ★★ 수정 전 FAIL 재현 — 이 회귀가 실제 장애를 잡는다는 근거")
# ⛔ 이 절이 없으면 「고친 뒤 통과한다」만 남고, 회귀가 무엇을 막는지 알 수 없다.
#    9c61da8 시점의 문면을 **그대로 박아** 신형에서 실패하는 것을 보존한다.
PRE_FIX_TITLE = re.compile(
    r"Selected\s+Product\s+and\s+Service\s+Revenue\s+Constant\s+Currency\s+Reconciliation",
    re.I)
PRE_FIX_ROW = re.compile(r"^Azure\s+and\s+other\s+cloud\s+services$", re.I)
for date, acc, _ in FIXTURES:
    r = extract(fx_html(date, acc), PRE_FIX_TITLE, PRE_FIX_ROW)
    if date in OLD_FORM:
        check(f"★ 구형 {date} 는 수정 전에도 통과했다", r == EXPECTED[date], str(r))
    else:
        check(f"★★ 신형 {date} 는 수정 전 실패한다 (live 장애 재현)",
              isinstance(r, str), str(r))
        check(f"★ {date} 실패 지점이 표 제목이다", isinstance(r, str) and "identify" in r,
              str(r))
# 제목만 고쳤다면 어떻게 되는가 — **두 번째 차단기**
FIXED_TITLE = re.compile(
    r"Selected\s+Product\s+and\s+Service\s+(?:Revenue|Information)\s+"
    r"Constant\s+Currency\s+Reconciliation", re.I)
for date in sorted(NEW_FORM):
    acc = dict((d, a) for d, a, _ in FIXTURES)[date]
    r = extract(fx_html(date, acc), FIXED_TITLE, PRE_FIX_ROW)
    check(f"★★ {date}: 제목만 고치면 여전히 실패한다 (행 라벨이 두 번째 차단기)",
          isinstance(r, str), str(r))

print("C-2 positive — 실제 마크업 4건에서 정확한 cc 를 집는다")
for date, acc, _ in FIXTURES:
    r = extract(fx_html(date, acc))
    check(f"{date} → {EXPECTED[date]}", r == EXPECTED[date], str(r))

print("C-3 ★ 판별력 — Q4 단독으로는 컬럼 오결합을 잡을 수 없다")
check("★ 판별 가능한 분기가 실제로 존재한다", len(DISCRIMINATING) >= 1, str(DISCRIMINATING))
check("★ 2026-04-29 가 판별 분기다 (GAAP 40 ≠ cc 39)", "2026-04-29" in DISCRIMINATING)
check("★ 2026-07-29 는 판별 분기가 아니다 (43 == 43) — 단독 검증 금지",
      "2026-07-29" not in DISCRIMINATING)
check("★ 신형 중 판별 분기가 최소 1건 있다", bool(DISCRIMINATING & NEW_FORM),
      str(DISCRIMINATING & NEW_FORM))
_q3 = extract(fx_html("2026-04-29", "0001193125-26-191457"))
check("★ 판별 분기에서 cc 를 골랐다 (GAAP 을 집지 않았다)",
      _q3[2] == "39%" and _q3[0] == "40%", str(_q3))
check("★ 판별 분기에서 영향 컬럼을 cc 로 집지 않았다", _q3[2] != _q3[1], str(_q3))

print("C-4 negative — ★ 실제 마크업의 최소 변형이 fail-closed 된다")
# ⛔ 합성 HTML 이 아니다. raw fixture 한 군데만 바꾼다.
BASE_D, BASE_A = "2026-04-29", "0001193125-26-191457"
_base = fx_html(BASE_D, BASE_A)
VARIANTS = [
    ("제목 Information→Metrics", "Service Information", "Service Metrics"),
    ("제목 Information→Segment", "Service Information", "Service Segment"),
    ("제목에서 Information 제거", "Service Information Constant", "Service Constant"),
    ("행 suffix revenue→sales", "cloud services revenue", "cloud services sales"),
    ("행 suffix 추가 …revenue growth", "cloud services revenue",
     "cloud services revenue growth"),
    ("행 이름 Azure→Azure AI", "Azure and other cloud", "Azure AI and other cloud"),
]
check("★ 변형 전 원문은 통과한다 (양성 대조)", extract(_base) == EXPECTED[BASE_D])
for nm, old, new in VARIANTS:
    mutated = _base.replace(old, new)
    check(f"★ 변형이 실제로 적용됐다: {nm}", mutated != _base)
    check(f"★ 미지 문면 거부: {nm}", isinstance(extract(mutated), str), str(extract(mutated)))

print("C-5 ★ identify 와 table binding 이 같은 Azure predicate 를 쓴다")
# ⛔ 예전에는 identify 가 비앵커 search, 결합은 앵커 match 였다. 그래서 로그에
#    `✓ Azure … 항목` 이 찍혔는데 실제 결합은 불가능했다. 그 불일치를 막는다.
for nm, old, new in [("suffix 변형(sales)", "cloud services revenue", "cloud services sales"),
                     ("이름 변형(Azure AI)", "Azure and other cloud", "Azure AI and other cloud")]:
    mutated = _base.replace(old, new)
    p = M.TableCollector()
    p.feed(mutated)
    _ck = M.identify(M.strip_html(mutated), p.tables)
    azure_ok = [v for n, v, _ in _ck if "Azure" in n][0]
    binds = bool(M.find_azure_table(p.tables))
    check(f"★ {nm}: identify 와 결합 가능성이 어긋나지 않는다 "
          f"(identify={azure_ok} · 결합={binds})", azure_ok == binds)
check("★ identify 가 AZURE_ROW 를 실제로 참조한다", "AZURE_ROW" in
      __import__("inspect").getsource(M.identify))

print("C-6 구형·신형 구조 차이는 값에 영향을 주지 않는다")
for date in sorted(OLD_FORM | NEW_FORM):
    acc = dict((d, a) for d, a, _ in FIXTURES)[date]
    p = M.TableCollector()
    p.feed(fx_html(date, acc))
    rows = M.drop_empty_columns(p.tables[0])
    check(f"{date} 표가 1개다", len(p.tables) == 1, str(len(p.tables)))
    check(f"{date} 4열이다", max(len(r) for r in rows) == 4)
check("★ 구형은 spacer 행이 더 많다 (17행 대 13행) — markup 정리일 뿐",
      len(M.drop_empty_columns((lambda h: (lambda p: (p.feed(h), p.tables)[1])(
          M.TableCollector()))(fx_html("2026-01-28", "0001193125-26-027198"))[0]))
      > len(M.drop_empty_columns((lambda h: (lambda p: (p.feed(h), p.tables)[1])(
          M.TableCollector()))(fx_html("2026-07-29", "0001193125-26-323632"))[0])))

print("B-8 이 회귀 자체의 경계")
check("★ B 절은 네트워크를 쓰지 않는다 (fixture 만 쓴다)",
      "http" not in "".join(REAL.split("<html>")))

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
