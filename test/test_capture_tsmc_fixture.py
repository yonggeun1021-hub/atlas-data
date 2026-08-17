#!/usr/bin/env python3
"""TSMC raw fixture capture 회귀 (CIO 승인 2026-08-16).

★ 증명한다 : `Net Revenue` 를 가진 표를 **전부** 보존한다(고르지 않는다),
             단위 선언을 의미로 식별한다, 원문 부분 문자열이다, 표 균형이 맞다,
             저장소에 쓰지 않는다, C4 를 수정하지 않는다.
★ 증명 못 한다 : 실제 SEC 마크업. 그것이 이 도구가 확보하려는 것이다.

⛔ 여기의 HTML 은 C4 회귀의 **합성 빌더** 산출물이다. 실제 6-K 가 아니다.
⛔ 네트워크를 쓰지 않는다.
⛔ 이 회귀는 C4 parser 를 검증하지 않는다 — capture 도구만 본다.
"""
from __future__ import annotations

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
import capture_tsmc_fixture as T                                    # noqa: E402
import c4_sec_edgar_check as C                                      # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


# C4 회귀의 합성 문서 빌더를 그대로 빌려 쓴다 (중복 구현하지 않는다)
_src = open(os.path.join(ROOT, "test", "test_c4_sec_edgar.py"), encoding="utf-8").read()
_ns = {}
exec(_src[_src.index("def doc("):_src.index("\nJUL = dict")],
     {"__builtins__": __builtins__}, _ns)          # noqa: S102
doc = _ns["doc"]
JUL = dict(month="July", year=2026, prev_month="June", prev_year_month="July",
           vals=("467,580", "442,680", "5.6", "323,166", "44.7",
                 "2,872,064", "2,096,211", "37.0"),
           prose=("467.58", "2,872.06"),
           thousands=("467,580,548", "2,872,064,238", "323,165,707", "2,096,211,240"),
           pub_date="August 10, 2026")
HTML = doc(**JUL)

print("A-1 ★ `Net Revenue` 를 가진 표를 전부 보존한다 — 고르지 않는다")
ts = T.capture_tables(HTML)
check("표 2건을 모두 잡는다 (million + thousands)", len(ts) == 2, str(len(ts)))
by_unit = {t["unit_declaration"]: t for t in ts}
check("million 표를 단위로 식별", "NT$ million" in by_unit, str(sorted(by_unit)))
check("thousands 표를 단위로 식별", "NT$ thousands" in by_unit, str(sorted(by_unit)))
check("★ 후보를 하나로 줄이지 않았다 (선택은 이 도구의 일이 아니다)", len(ts) > 1)

print("A-2 ★ 구조 진단이 실제 값과 맞는다")
if len(ts) == 2:
    check("million 표의 Net Revenue 행은 1개",
          by_unit["NT$ million"]["net_revenue_row_count"] == 1,
          str(by_unit["NT$ million"]["net_revenue_row_count"]))
    check("★ thousands 표의 Net Revenue 행은 2개 (월 행 + 누계 행)",
          by_unit["NT$ thousands"]["net_revenue_row_count"] == 2,
          str(by_unit["NT$ thousands"]["net_revenue_row_count"]))
    check("million 표에 Y-o-Y 헤더가 있다", by_unit["NT$ million"]["has_yoy_header"])
    check("★ thousands 표에는 Y-o-Y 헤더가 없다 (이것이 후보를 가른다)",
          not by_unit["NT$ thousands"]["has_yoy_header"])

print("A-3 ★ 원문을 잘라내기만 한다")
for t in ts:
    check(f"[{t['unit_declaration']}] 원문의 부분 문자열이다", t["_block"] in HTML)
    check(f"[{t['unit_declaration']}] 표 여닫이가 맞는다", T.balanced(t["_block"]))
    check(f"[{t['unit_declaration']}] Net Revenue 를 담고 있다",
          re.search(r"Net\s+Revenue", t["_block"], re.I) is not None)
    check(f"[{t['unit_declaration']}] 슬라이스 길이와 구간 길이가 같다",
          t["slice_end"] - t["slice_start"] == t["slice_chars"])
    check(f"[{t['unit_declaration']}] 문서 전체를 담지 않는다 (bounded)",
          t["slice_chars"] < len(HTML))

print("A-4 fail-closed — 대상이 없으면 만들지 않는다")
check("★ Net Revenue 가 없는 문서 → 0건",
      T.capture_tables("<html><body><table><tr><td>Gross Margin</td><td>1</td></tr>"
                       "</table></body></html>") == [])
check("★ 표 밖의 Net Revenue 는 잡지 않는다",
      T.capture_tables("<html><body><p>Net Revenue</p></body></html>") == [])

print("A-5 정적 — 경계와 금지 사항")
src = open(os.path.join(ROOT, "collectors", "capture_tsmc_fixture.py"),
           encoding="utf-8").read()
tree = ast.parse(src)
_docs = {ast.get_docstring(n) for n in ast.walk(tree)
         if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))}
_lits = [n.value for n in ast.walk(tree)
         if isinstance(n, ast.Constant) and isinstance(n.value, str)
         and n.value not in _docs]
check("★ C4 를 읽기 전용으로 재사용한다", "import c4_sec_edgar_check as C" in src)
check("★ 승인된 C4 primitive 를 쓴다 (재구현 아님)",
      "C.RE_NET_REVENUE" in src and "C.identify(" in src and "C.get(" in src)
check("★ 저장소 안에는 쓰지 않는다 (경로 가드)",
      "startswith(os.path.abspath(ROOT)" in src)
check("★ 저장 직전 집행을 재사용한다", "slice_and_verify" in src)
check("★ 대상 월이 3개로 고정돼 있다 (장기 역사 확대 금지)",
      len(T.TARGET_MONTHS) == 3, str(T.TARGET_MONTHS))
check("★ C4 가 근거로 삼은 두 달이 들어 있다",
      (2026, 6) in T.TARGET_MONTHS and (2026, 7) in T.TARGET_MONTHS)
check("★ 인접 달이 하나 들어 있다", (2026, 5) in T.TARGET_MONTHS)
# ⛔ 값 판정·선택을 하지 않는다 — 그런 어휘가 실행 코드에 없어야 한다
_body = src.split('"""', 2)[2]
check("★ 값 판정 어휘가 실행 코드에 없다",
      not re.search(r"\bdecision\s*=|monthly_yoy|cumulative_yoy", _body))
# ⛔ 「first-match 아님」을 `break` 유무로 볼 수 없다 — capture_tables 는 표 구간을
#    찾을 때 내부적으로 break 를 쓴다. **행동으로** 판정한다: 표를 추가하면 결과도
#    늘어나야 한다. 고르는 도구라면 늘지 않는다.
_two_million = HTML.replace("<h1>FORM 6-K</h1>", "<h1>FORM 6-K</h1>", 1)
_dec = re.search(r"<table>(?:(?!</table>).)*M-o-M(?:(?!</table>).)*</table>", HTML, re.S)
check("★ 결정표 블록을 찾았다 (아래 검사의 전제)", _dec is not None)
if _dec:
    _three = HTML.replace(_dec.group(0), _dec.group(0) + _dec.group(0), 1)
    check("★ 자격 표를 늘리면 보존 결과도 늘어난다 (고르지 않는다는 행동 증거)",
          len(T.capture_tables(_three)) == len(ts) + 1,
          f"{len(T.capture_tables(_three))} vs {len(ts) + 1}")

print("A-6 ★ C4 를 수정하지 않았다")
c4 = open(os.path.join(ROOT, "collectors", "c4_sec_edgar_check.py"),
          encoding="utf-8").read()
c4t = ast.parse(c4)
_fdt = [n for n in ast.walk(c4t) if isinstance(n, ast.FunctionDef)
        and n.name == "find_decision_table"][0]
# ★ 2026-08-16 갱신 — row-local guard 는 별도 CIO 승인으로 들어갔다.
#   이 회귀(capture 도구)가 지켜야 할 것은 「capture 가 C4 를 바꾸지 않는다」이다.
check("★ capture 도구는 C4 를 import 만 한다 (수정 아님)",
      "import c4_sec_edgar_check as C" in src and "C." in src)
# ⛔ C4 함수를 **호출**하는 것은 정상이다 (진단 목적). 막아야 할 것은 **수정**이다.
#    monkey-patch(= C 모듈 속성에 대입)가 없는지를 AST 로 본다.
_patches = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
            and t.value.id == "C"]
check("⛔ capture 도구가 C4 모듈을 monkey-patch 하지 않는다", not _patches,
      str([getattr(t, "attr", "?") for n in _patches for t in n.targets]))
check("★ C4 의 row guard 는 첫 행 선택을 하지 않는다 (승인된 상태 확인)",
      not any(isinstance(x, ast.Break) for x in ast.walk(_fdt)))
check("★ 이 회귀는 네트워크를 쓰지 않는다",
      ("url" + "lib") not in open(os.path.abspath(__file__), encoding="utf-8")
      .read().replace('("url" + "lib")', ""))

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
