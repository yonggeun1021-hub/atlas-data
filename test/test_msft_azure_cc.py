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
ck = M.identify(M.strip_html(doc("39%", "0%", "39%")))
check("실적 발표문으로 식별", all(v for _, v, _ in ck) and len(ck) == 3)
for nm, d in (("표 제목이 없는 문서",
               "<html><body><p>Microsoft Azure and other cloud services grew.</p></body></html>"),
              ("Azure 항목이 없는 문서",
               "<html><body><h3>Selected Product and Service Revenue Constant Currency "
               "Reconciliation</h3><p>Microsoft</p></body></html>")):
    check(f"{nm} 거부", not all(v for _, v, _ in M.identify(M.strip_html(d))))

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
check("★ EX-99.1 을 type 으로 지목한다", 'EXHIBIT_TYPE = "EX-99.1"' in src
      and 'it.get("type")' in body)
check("★ items 2.02 로 discovery 를 좁힌다", 'EARNINGS_ITEM = "2.02"' in src)
check("★ 상한 초과분을 로그에 남긴다", "조회하지 않은 것" in body)
check("★ open() 쓰기 모드가 없다", not re.search(r"open\([^)]*['\"][wa]", body))
check("★ 이 회귀는 네트워크를 쓰지 않는다",
      ("url" + "lib") not in open(os.path.abspath(__file__), encoding="utf-8").read()
      .replace('("url" + "lib")', ""))

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
