#!/usr/bin/env python3
"""C4 SEC EDGAR parser 회귀 (CIO 승인 2026-08-15).

★ 경계를 먼저 밝힌다 — 이 회귀가 증명하는 것과 못 하는 것
   증명한다 : HTML <table> → 행렬 → **의미 기반 컬럼 결합** → Decision 값 추출,
             cross-check 층 정합성, 발표일 분리, 미끼 문서 거부, fail-closed.
   증명 못 한다 : EDGAR 가 실제로 내보내는 마크업 그 자체.
   ⛔ 아래 HTML 은 **합성이다. 공식 캡처가 아니다.** 표의 논리 내용(제목·단위·헤더·
      숫자)만 CIO 가 SEC 원문에서 확인해 확정한 값으로 채웠고, 마크업 형태는
      EDGAR 에서 흔한 변형(빈 spacer 셀 · 헤더 줄바꿈 분할 · th/td 혼용 · &nbsp;)을
      일부러 섞었다. 실제 마크업 검증은 GitHub live run 이 담당한다.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
import c4_sec_edgar_check as C                                    # noqa: E402

ok, bad = [], []


def check(name, cond, extra=""):
    (ok if cond else bad).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))


# ── 합성 문서 빌더 ────────────────────────────────────────────────
def doc(month, year, prev_month, prev_year_month, vals, prose, thousands,
        pub_date, split_header=False, spacer=False, use_th=False):
    """vals = (cur, prev_m, mom, prev_y, yoy, cum, cum_prev, cum_yoy)"""
    cur, prev_m, mom, prev_y, yoy, cum, cum_prev, cum_yoy = vals
    heads = [f"{month} {year}", f"{prev_month} {year}", "M-o-M Increase (Decrease) %",
             f"{month} {year - 1}", "Y-o-Y Increase (Decrease) %",
             f"January to {month} {year}", f"January to {month} {year - 1}",
             "Y-o-Y Increase (Decrease) %"]
    cells = [cur, prev_m, mom, prev_y, yoy, cum, cum_prev, cum_yoy]
    tag = "th" if use_th else "td"
    sp = f"<{tag}>&nbsp;</{tag}>" if spacer else ""

    if split_header:
        # 헤더를 두 줄로 쪼갠다: 라벨 / 연도
        top = "".join(f"<{tag}>{h.rsplit(' ', 1)[0] if h[-4:].isdigit() else h}</{tag}>{sp}"
                      for h in heads)
        bot = "".join(f"<{tag}>{h.rsplit(' ', 1)[1] if h[-4:].isdigit() else ''}</{tag}>{sp}"
                      for h in heads)
        hrows = (f"<tr><{tag}>Period</{tag}>{sp}{top}</tr>"
                 f"<tr><{tag}></{tag}>{sp}{bot}</tr>")
    else:
        hrows = ("<tr><%s>Period</%s>%s%s</tr>"
                 % (tag, tag, sp, "".join(f"<{tag}>{h}</{tag}>{sp}" for h in heads)))

    drow = ("<tr><td>Net Revenue</td>%s%s</tr>"
            % (sp, "".join(f"<td>{v}</td>{sp}" for v in cells)))

    th_rows = ""
    if thousands:
        th_rows = (f"<table><tr><td>Period</td><td>Items</td><td>{year}</td><td>{year-1}</td></tr>"
                   f"<tr><td>{month[:3]}.</td><td>Net Revenue</td><td>{thousands[0]}</td>"
                   f"<td>{thousands[2]}</td></tr>"
                   f"<tr><td>Jan.~{month[:3]}.</td><td>Net Revenue</td><td>{thousands[1]}</td>"
                   f"<td>{thousands[3]}</td></tr></table>")

    return f"""<html><body>
<h1>FORM 6-K</h1>
<h2>TSMC {month} {year} Revenue Report</h2>
<p>Hsinchu, Taiwan, R.O.C., {pub_date} &ndash; TSMC today announced its net revenue.
On a consolidated basis, revenue for {month} {year} was approximately NT${prose[0]} billion,
an increase of {mom} percent from {prev_month} {year} and an increase of {yoy} percent from
{month} {year - 1}. Revenue for January through {month} {year} totaled NT${prose[1]} billion,
an increase of {cum_yoy} percent compared to the same period in {year - 1}.</p>
<h3>TSMC {month} Revenue Report (Consolidated):</h3>
<p>(Unit:NT$ million)</p>
<table>{hrows}{drow}</table>
<h3>Revenue (in NT$ thousands)</h3>
{th_rows}
</body></html>"""


JUL = dict(month="July", year=2026, prev_month="June", prev_year_month="July",
           vals=("467,580", "442,680", "5.6", "323,166", "44.7",
                 "2,872,064", "2,096,211", "37.0"),
           prose=("467.58", "2,872.06"),
           thousands=("467,580,548", "2,872,064,238", "323,165,707", "2,096,211,240"),
           pub_date="August 10, 2026")
JUN = dict(month="June", year=2026, prev_month="May", prev_year_month="June",
           vals=("442,680", "416,975", "6.2", "263,709", "67.9",
                 "2,404,484", "1,773,046", "35.6"),
           prose=("442.68", "2,404.48"),
           thousands=("442,679,969", "2,404,483,690", "263,708,758", "1,773,046,000"),
           pub_date="July 13, 2026")

EXPECT = {"July": ("467,580", "44.7", "2,872,064", "37.0"),
          "June": ("442,680", "67.9", "2,404,484", "35.6")}


def run_doc(spec, **kw):
    html = doc(**spec, **kw)
    text = C.strip_html(html)
    p = C.TableCollector()
    p.feed(html)
    found = C.find_decision_table(p.tables, spec["month"], spec["year"])
    for ti, rows, di in found:
        header = C.build_header(rows, di)
        bound, _ = C.bind_columns(header, rows[di], spec["month"], spec["year"])
        if bound:
            return bound, text, p.tables
    return None, text, p.tables


print("① 식별 — 내용 기반")
for spec in (JUL, JUN):
    t = C.strip_html(doc(**spec))
    ck = C.identify(t, spec["month"], spec["year"])
    check(f"{spec['month']} {spec['year']} 월매출 보고서 식별 (4요건)",
          all(v for _, v, _ in ck) and len(ck) == 4, str([(l, v) for l, v, _ in ck]))

DECOY_BOARD = "<html><body><h2>TSMC Board Meeting Resolutions</h2><p>On a consolidated basis, " \
              "the Board approved capital appropriations.</p></body></html>"
DECOY_Q = "<html><body><h2>TSMC Second Quarter 2026 Results</h2><p>Gross margin was 58.6 percent " \
          "and diluted EPS was NT$15.36. On a consolidated basis, revenue for July 2026 was " \
          "approximately NT$467.58 billion.</p></body></html>"
for nm, d in (("이사회 결의 6-K", DECOY_BOARD), ("분기 실적 6-K", DECOY_Q)):
    ck = C.identify(C.strip_html(d), "July", 2026)
    check(f"{nm} 거부", not all(v for _, v, _ in ck))

print("\n② 의미 기반 컬럼 결합 — 마크업 변형에 걸쳐")
for label, kw in (("기본", {}), ("spacer 셀", {"spacer": True}),
                  ("헤더 2줄 분할", {"split_header": True}),
                  ("th 사용 + spacer", {"use_th": True, "spacer": True}),
                  ("분할 + spacer", {"split_header": True, "spacer": True})):
    for spec in (JUL, JUN):
        b, _, _ = run_doc(spec, **kw)
        e = EXPECT[spec["month"]]
        got = (b["monthly_revenue"], b["monthly_yoy"], b["cumulative_revenue"],
               b["cumulative_yoy"]) if b else None
        check(f"[{label}] {spec['month']} = {e}", got == e, str(got))

print("\n③ 두 Y-o-Y 컬럼을 뒤바꾸지 않는가")
b, _, _ = run_doc(JUL)
check("월 YoY 는 44.7 (누계 37.0 아님)", b["monthly_yoy"] == "44.7", b["monthly_yoy"])
check("누계 YoY 는 37.0 (월 44.7 아님)", b["cumulative_yoy"] == "37.0", b["cumulative_yoy"])
check("M-o-M 5.6 은 Decision 값이 아님",
      b.get("mom_not_decision") == "5.6" and "5.6" not in
      {b["monthly_yoy"], b["cumulative_yoy"]})
check("월 매출은 전년동월 323,166 이 아님", b["monthly_revenue"] == "467,580")
check("누계 매출은 전년누계 2,096,211 이 아님", b["cumulative_revenue"] == "2,872,064")

print("\n④ cross-check — 각 층의 공표 자릿수로만 비교")
for spec in (JUL, JUN):
    b, text, tables = run_doc(spec)
    pl = C.prose_layer(text, spec["month"], spec["year"])
    tl = C.thousands_layer(tables, spec["month"], spec["year"])
    notes, bd = C.crosscheck(b, pl, tl)
    check(f"{spec['month']} 산문 층 검출", "monthly" in pl and "cumulative" in pl, str(pl))
    check(f"{spec['month']} 천원표 층 검출", "monthly" in tl and "cumulative" in tl, str(tl))
    check(f"{spec['month']} 3층 정합", not bd, str(bd))
    check(f"{spec['month']} 천원표 축약 방식이 기록된다",
          any("버림" in x and "반올림" in x for x in notes), str(notes))
check("천원표 누계 정수부가 Decision 과 일치 (2,872,064,238→2,872,064)",
      2872064238 // 1000 == 2872064)
check("산문 누계는 반올림값이라 Decision 과 다르다 (2,872.06→2,872,060 ≠ 2,872,064)",
      int(2872.06 * 1000) != 2872064)
check("★ 두 달의 축약 방식이 서로 다르다 — 07 은 버림, 06 은 반올림",
      467580548 // 1000 == 467580 and (442679969 + 500) // 1000 == 442680
      and 442679969 // 1000 != 442680 and (467580548 + 500) // 1000 != 467580)

print("\n⑤ 불일치는 자동 선택하지 않고 실패시킨다")
b, text, tables = run_doc(JUL)
_, bd = C.crosscheck(b, {"monthly": ("999.99", "billion")}, {})
check("산문이 어긋나면 bad 로 보고", bool(bd), str(bd))
_, bd2 = C.crosscheck(b, {}, {"monthly": "111,111,111"})
check("천원표가 어긋나면 bad 로 보고", bool(bd2), str(bd2))
bad_bind, _ = C.bind_columns(["Period", "July 2026", "Y-o-Y Increase (Decrease) %"],
                             ["Net Revenue", "467,580", "44.7"], "July", 2026)
check("헤더가 모자라면 결합하지 않는다", bad_bind is None)

print("\n⑥ 발표일 / provenance 분리")
for spec, want in ((JUL, "2026-08-10"), (JUN, "2026-07-13")):
    t = C.strip_html(doc(**spec))
    pub, ev = C.body_published_at(t, spec["year"], C.month_index(spec["month"]))
    check(f"{spec['month']} 본문 발표일 = {want}", pub == want, f"{pub} ({ev})")
t = C.strip_html(doc(**JUL))
check("대상월과 같은 달의 날짜는 발표일로 채택하지 않는다",
      C.body_published_at(t, 2026, 8)[0] != "2026-08-10")

print("\n⑦ 계약 준수 정적 확인 (AST)")
import ast
src = open(os.path.join(ROOT, "collectors", "c4_sec_edgar_check.py"), encoding="utf-8").read()
tree = ast.parse(src)
fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
main_src = ast.get_source_segment(src, fns["main"])
# ★ 2026-08-16 — selection 이 순수계층으로 빠지면서 대입 문면이 바뀌었다.
#   검사를 지우지 않고 **불변식**(decision 은 bind_columns 산출물에서만 온다)에
#   다시 겨눈다. ⛔ 문자열이 아니라 AST 로 대입식을 본다.
# ⛔ 어느 함수에 있는지 추측하지 않는다 — 모듈 전체에서 `decision` 대입을 찾는다.
#    (원 검사는 main() 만 봤는데 실제 대입은 observe() 에 있었다.)
_dec_srcs, _dec_fns = [], set()
for _fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
    for _n in ast.walk(_fn):
        if isinstance(_n, ast.Assign):
            for _t in _n.targets:
                if isinstance(_t, ast.Name) and _t.id == "decision":
                    _dec_srcs.append(ast.dump(_n.value))
                    _dec_fns.add(_fn.name)
check("Decision 대입이 존재한다", len(_dec_srcs) >= 1, str(_dec_srcs))
check("Decision 은 bind_columns 산출물(bound)에서만 온다",
      all(("'bound'" in d) or d.startswith("Name(id='bound'") for d in _dec_srcs),
      str(_dec_srcs))
# final_candidates 안에서 bound 가 bind_columns 결과인지도 확인한다
_fc = fns["final_candidates"]
_fc_src = ast.get_source_segment(src, _fc)
check("final_candidates 의 bound 는 bind_columns 가 만든다",
      "bound, probs = bind_columns(" in _fc_src)
check('final_candidates 가 담는 값은 "bound" 키다', '"bound": bound' in _fc_src)
check("Decision 을 prose_layer/thousands_layer 에서 만들지 않는다",
      not re.search(r"decision\s*(\[[^]]+\])?\s*=\s*(prose_layer|thousands_layer|pl|tl)\b", src))
check("YoY 를 나눗셈으로 재계산하지 않는다",
      not re.search(r"yoy[^=\n]*=\s*[^\n]*[/]\s*(?!1000)", src))
check("published_at 에 SEC acceptance 를 대입하지 않는다",
      not re.search(r"pub\s*=\s*[^\n]*acceptance", src)
      and "pub, ev = body_published_at" in ast.get_source_segment(src, fns["observe"]))
check("float 로 관측값을 만들지 않는다",
      not re.search(r"float\(\s*(decision|obs|bound)", src))
check("임의 허용오차 상수가 없다",
      not re.search(r"TOLERANCE|EPSILON|abs\(.*\)\s*<=?\s*\d", src))


print("\n⑧ 단위 검증은 식별이 아니라 추출 게이트다 (CIO 판정 1)")
labels = [l for l, _, _ in C.identify(C.strip_html(doc(**JUL)), "July", 2026)]
check("식별 4요건에 단위 조건이 없다",
      len(labels) == 4 and not any("Unit" in l for l in labels), str(labels))
no_unit = doc(**JUL).replace("<p>(Unit:NT$ million)</p>", "")
check("단위 선언이 없어도 문서 식별 자체는 통과",
      all(v for _, v, _ in C.identify(C.strip_html(no_unit), "July", 2026)))
check("그러나 추출 단위 게이트는 실패한다",
      C.verify_unit_million(C.strip_html(no_unit))[0] is False)
check("단위 선언이 있으면 게이트 통과",
      C.verify_unit_million(C.strip_html(doc(**JUL)))[0] is True)

print("\n⑨ entity 정규화 경계 — 표준 entity 만 허용, 문자/DOM 변형은 보정 금지")
VARIANTS = {
    "평문": "<p>(Unit:NT$ million)</p>",
    "&nbsp;": "<p>(Unit:NT$&nbsp;million)</p>",
    "&#160;": "<p>(Unit:NT$&#160;million)</p>",
    "&#36;": "<p>(Unit:NT&#36; million)</p>",
    "&#x24;": "<p>(Unit:NT&#x24; million)</p>",
    "셀 분할": "<table><tr><td>(Unit:NT$</td><td>million)</td></tr></table>",
    "공백 삽입": "<p>( Unit : NT$ million )</p>",
}
for nm, h in VARIANTS.items():
    check(f"[표준 entity] {nm} → 통과", C.verify_unit_million(C.strip_html(h))[0], nm)
NOT_NORMALIZED = {
    "$ 가 별도 태그": "<p>(Unit:NT<span>$</span> million)</p>",
    "전각 ＄": "<p>(Unit:NT＄ million)</p>",
}
for nm, h in NOT_NORMALIZED.items():
    check(f"[자동 보정 금지] {nm} → 통과시키지 않는다",
          not C.verify_unit_million(C.strip_html(h))[0], nm)

print("\n⑩ NT$ thousands 표는 Decision observation 이 될 수 없다")
ONLY_TH = """<html><body><h2>TSMC July 2026 Revenue Report</h2>
<h3>TSMC July Revenue Report (Consolidated):</h3><p>(Unit:NT$ million)</p>
<p>January to July 2026</p>
<h3>Revenue (in NT$ thousands)</h3>
<table><tr><td>Period</td><td>Items</td><td>2026</td><td>2025</td></tr>
<tr><td>Jul.</td><td>Net Revenue</td><td>467,580,548</td><td>323,165,707</td></tr>
<tr><td>Jan.~Jul.</td><td>Net Revenue</td><td>2,872,064,238</td><td>2,096,211,240</td></tr>
</table></body></html>"""
pth = C.TableCollector(); pth.feed(ONLY_TH)
found_th = C.find_decision_table(pth.tables, "July", 2026)
bound_any = None
for ti, rows, di in found_th:
    b, _ = C.bind_columns(C.build_header(rows, di), rows[di], "July", 2026)
    if b:
        bound_any = b
        break
check("천원표만 있는 문서에서는 Decision 결합이 성립하지 않는다", bound_any is None,
      str(bound_any))
check("천원표의 완전한 값(467,580,548)이 Decision 으로 새어 나오지 않는다",
      bound_any is None or "467,580,548" not in str(bound_any))
b_full, _, tabs_full = run_doc(JUL)
check("두 표가 함께 있어도 Decision 은 million 표 값(467,580)",
      b_full["monthly_revenue"] == "467,580")
check("Decision 이 천원 값이 아니다", b_full["monthly_revenue"] != "467,580,548")

print("\n⑪ cross-check 는 기록·노출 전용 — Decision 을 바꾸지 않는다")
tl_bad = {"monthly": "999,999,999", "cumulative": "888,888,888"}
notes_b, bad_b = C.crosscheck(b_full, {}, tl_bad)
check("천원표가 달라도 Decision 값은 그대로", b_full["monthly_revenue"] == "467,580")
check("불일치는 참고 목록으로만 보고", bool(bad_b) and all("참고" in x for x in bad_b),
      str(bad_b))
check("양쪽 raw 값이 기록된다", any("raw" in n for n in notes_b), str(notes_b[:2]))

print("\n⑫ 실패 evidence 출력")
ev = C.evidence("aaa Unit:NT＄ million bbb", r"Unit")
check("probe 주변 텍스트를 repr 로 남긴다", ev and "Unit" in ev[0] and ev[0].startswith("'"))
check("probe 가 없으면 그 사실을 알린다",
      C.evidence("nothing here", r"Consolidated")[0].startswith("(probe"))
check("전체 문서를 뿌리지 않는다 (길이 제한)",
      len(C.evidence("x" * 5000 + "Unit" + "y" * 5000, r"Unit")[0]) < 400)

print("\n⑬ 이 회귀는 네트워크를 쓰지 않는다 (CIO 판정 — run_all 에 live 호출 금지)")
self_src = open(os.path.abspath(__file__), encoding="utf-8").read()
# ★ needle 을 조립한다 — 문자열을 그대로 쓰면 이 파일 자신과 매칭돼 자기충족 테스트가 된다.
NEEDLE_LIB = "url" + "lib"
NEEDLE_REQ = "requ" + "ests"
NEEDLE_GET = "C." + "get("
NEEDLE_MAIN = "C." + "main("
check("회귀 파일에 네트워크 라이브러리 import 없음",
      not re.search(rf"^import {NEEDLE_LIB}|^import {NEEDLE_REQ}|^from {NEEDLE_LIB}",
                    self_src, re.M))
check("회귀가 네트워크 진입점을 호출하지 않음",
      NEEDLE_GET not in self_src.replace('"C." + "get("', "")
      and NEEDLE_MAIN not in self_src.replace('"C." + "main("', ""))
check("run_all 이 이 회귀를 승인 목록에 담고 있다",
      "test/test_c4_sec_edgar.py" in
      open(os.path.join(ROOT, "run_all.py"), encoding="utf-8").read())


print("\n⑭ 실측 header matrix (2026-07 live run) 고정")
# ★ 합성이 아니다. 2026-08-15 GitHub live run 이 실제 SEC 문서에서 뽑아 로그에 남긴
#   header/data 행렬 그대로다. 제목·단위 선언이 헤더 셀에 흡수된 실제 구조를 담는다.
LIVE_HEADER_202607 = [
    'TSMC July Revenue Report (Consolidated): Period', 'July 2026', 'June 2026',
    'M-o-M Increase (Decrease) %', 'July 2025', 'Y-o-Y Increase (Decrease) %',
    'January to July 2026', '(Unit:NT$ million) January to July 2025',
    'Y-o-Y Increase (Decrease) %']
LIVE_DATA_202607 = ['Net Revenue', '467,580', '442,680', '5.6', '323,166', '44.7',
                    '2,872,064', '2,096,211', '37.0']

lb, lp = C.bind_columns(LIVE_HEADER_202607, LIVE_DATA_202607, "July", 2026)
check("실측 header 로 결합 성공", lb is not None, str(lp))
if lb:
    check("실측 월매출 = 467,580", lb["monthly_revenue"] == "467,580", lb["monthly_revenue"])
    check("실측 월 YoY = 44.7", lb["monthly_yoy"] == "44.7", lb["monthly_yoy"])
    check("실측 누계 = 2,872,064", lb["cumulative_revenue"] == "2,872,064",
          lb["cumulative_revenue"])
    check("실측 누계 YoY = 37.0", lb["cumulative_yoy"] == "37.0", lb["cumulative_yoy"])
    check("제목이 흡수된 [0] 셀을 기간 컬럼으로 잡지 않는다",
          0 not in lb["_column_index"].values(), str(lb["_column_index"]))
    check("단위가 흡수된 [7] 셀을 전년누계로 정확히 잡는다",
          lb["_column_index"]["cumulative_prior_year"] == 7,
          str(lb["_column_index"]))

print("\n⑮ monthly ↔ cumulative 오인 — 양방향 독립 음성 테스트")
# (가) 'January to July 2025' 를 전년동월(July 2025)로 오인하면 실패해야 한다
H_NO_MONTHLY_PRIOR = ['Period', 'July 2026', 'January to July 2026',
                      'January to July 2025', 'Y-o-Y Increase (Decrease) %']
D_NO_MONTHLY_PRIOR = ['Net Revenue', '467,580', '2,872,064', '2,096,211', '37.0']
b_a, why_a = C.bind_columns(H_NO_MONTHLY_PRIOR, D_NO_MONTHLY_PRIOR, "July", 2026)
check("(가) 전년동월 컬럼이 없으면 결합하지 않는다 — 누계를 월로 오인 금지",
      b_a is None and any("전년동월" in x for x in why_a), str(why_a))

# (나) 'July 2025' 를 전년누계로 오인하면 실패해야 한다
H_NO_CUM_PRIOR = ['Period', 'July 2026', 'July 2025', 'Y-o-Y Increase (Decrease) %',
                  'January to July 2026', 'Y-o-Y Increase (Decrease) %']
D_NO_CUM_PRIOR = ['Net Revenue', '467,580', '323,166', '44.7', '2,872,064', '37.0']
b_b, why_b = C.bind_columns(H_NO_CUM_PRIOR, D_NO_CUM_PRIOR, "July", 2026)
check("(나) 전년누계 컬럼이 없으면 결합하지 않는다 — 월을 누계로 오인 금지",
      b_b is None and any("전년누계" in x for x in why_b), str(why_b))

# (다) 누계 셀은 월 anchor 로 절대 계산되지 않는다 — 중복 매칭 검사
H_DUP = ['Period', 'July 2025', 'January to July 2025']
dup_month = [i for i, h in enumerate(H_DUP)
             if __import__("re").search(r"\bJuly\s+2025\b",
                                        __import__("re").sub(r"January\s+to\s+July\s+\d{4}",
                                                             " ", h, flags=2), 2)]
check("(다) 'July 2025' 로 매칭되는 셀은 1개뿐 (누계 셀 제외됨)",
      dup_month == [1], str(dup_month))

# (라) 실측 헤더에서 각 anchor 가 정확히 1개
import re as _re
for lbl, pat, cum in (("대상월", 2026, False), ("전년동월", 2025, False),
                      ("당해누계", 2026, True), ("전년누계", 2025, True)):
    if cum:
        hits = [i for i, h in enumerate(LIVE_HEADER_202607)
                if _re.search(rf"January\s+to\s+July\s+{pat}\b", h, _re.I)]
    else:
        hits = [i for i, h in enumerate(LIVE_HEADER_202607)
                if _re.search(rf"\bJuly\s+{pat}\b",
                              _re.sub(r"January\s+to\s+July\s+\d{4}", " ", h, flags=_re.I),
                              _re.I)]
    check(f"(라) 실측 헤더 {lbl} anchor 정확히 1건", len(hits) == 1, str(hits))


print("\n⑯ 월 연속성 입력 구성 (RULE-0003 capability) — 반증 가능하게")
def R(m, yoy, acc, pub):
    return {"target_month": m, "monthly_yoy_pct": yoy, "cumulative_yoy_pct": "0",
            "accession": acc, "published_at": pub}
GOOD = [R("2026-06", "67.9", "acc-A", "2026-07-13"), R("2026-07", "44.7", "acc-B", "2026-08-10")]
res = C.contiguity_checks(GOOD)
check("June→July 연속 계열은 전부 통과", all(v for _, v, _ in res),
      str([(l, v) for l, v, _ in res]))
def fails(seq, label):
    r = C.contiguity_checks(seq)
    failed = [l for l, v, _ in r if not v]
    check(f"{label} → 거부", bool(failed), str(r))
    return failed
fails([R("2026-05", "1", "a", "2026-06-10"), R("2026-07", "2", "b", "2026-08-10")], "월 건너뜀(05→07)")
fails([R("2026-07", "1", "a", "2026-08-10"), R("2026-07", "2", "b", "2026-08-10")], "같은 달 중복")
fails([R("2026-06", "1", "a", "2026-07-13"), R("2026-07", "2", "a", "2026-08-10")], "같은 filing 재사용")
fails([R("2026-06", "1", "a", "2026-09-01"), R("2026-07", "2", "b", "2026-08-10")], "published_at 역순")
fails([R("2026-07", "1", "a", "2026-08-10"), R("2026-06", "2", "b", "2026-07-13")], "대상월 내림차순")
check("연말 경계 12월→1월도 연속으로 본다",
      all(v for _, v, _ in C.contiguity_checks(
          [R("2026-12", "1", "a", "2027-01-10"), R("2027-01", "2", "b", "2027-02-10")])))
check("⛔ 연속성 함수가 RULE-0003 조건을 평가하지 않는다",
      "40" not in __import__("inspect").getsource(C.contiguity_checks))



# ══════════════════════════════════════════════════════════════════════
# ⑰ 실제 SEC 마크업 회귀 — row identity 0/2+ (CIO 승인 2026-08-16)
#
#   ★ 앞의 ①~⑮ 는 **합성 문서**다. 이 절만 실제 TSMC 6-K 원문 슬라이스를 쓴다
#     (Actions run 31926693739 · artifact tsmc-raw-fixtures · 재구성 없음).
#   ★ 우선순위: **실제 SEC evidence > 합성 fixture**. 충돌하면 fixture 가 틀렸다.
#
#   ⛔ 이번 범위는 **row-local 0/2+ guard 뿐**이다 (CIO 판정).
#      table-level 2+ guard · 컬럼 identity 합성 FI · build_header 는 제외한다.
# ══════════════════════════════════════════════════════════════════════
import hashlib as _hl                                                # noqa: E402

FX = os.path.join(ROOT, "collectors", "fixtures")

# (파일, slice sha256, Net Revenue 행 수, Y-o-Y 헤더 유무) — MANIFEST 기록값 고정
TSMC_FX = [
    ("2026-06-10_0001046179-26-000367_t4_unit-unknown.html",
     "fbc3482b673ccb2ff27230313763f8249936658031ce0e418bacc569c3d90589", 1, True),
    ("2026-06-10_0001046179-26-000367_t6_NT-thousands.html",
     "c07d23d90a24057ffcfe1fdedbbc2b9ac3466ec40458be1ee534767c80cb9ef6", 2, False),
    ("2026-07-13_0001046179-26-000447_t4_unit-unknown.html",
     "18d3b35374fef90d93418af0bbd70df165bbe30400e8c5e0b3238bfc83002020", 1, True),
    ("2026-07-13_0001046179-26-000447_t6_NT-thousands.html",
     "c7e71a780ae27fe254d36ba2330470c38e70b5be430a7efa1388dde8d3bcc242", 2, False),
    ("2026-08-10_0001046179-26-000471_t4_unit-unknown.html",
     "88f4c53b21df55ccd1c85a99d1549526df6f20771189dd37b19bbc4a59ffaab2", 1, True),
    ("2026-08-10_0001046179-26-000471_t6_NT-thousands.html",
     "6b3fd34fa732781f7e40279a5ab32623f2050c7dc19cb53a2100c3ea9eab6e66", 2, False),
]
# 제출일 → (대상월, 연도) · 기존 P3 관측값
TSMC_MONTH = {"2026-06-10": ("May", 2026), "2026-07-13": ("June", 2026),
              "2026-08-10": ("July", 2026)}
P3_VALUES = {"June": ("442,680", "67.9", "2,404,484", "35.6"),
             "July": ("467,580", "44.7", "2,872,064", "37.0")}


def _fx(name):
    return open(os.path.join(FX, name), encoding="utf-8").read()


def _tables(html):
    p = C.TableCollector()
    p.feed(html)
    return p.tables


print("\n⑰-0 실제 fixture 무결성 — 원문 슬라이스가 그대로인가")
for name, want, nrows, yoy in TSMC_FX:
    path = os.path.join(FX, name)
    check(f"{name[:28]} 존재", os.path.exists(path))
    if not os.path.exists(path):
        continue
    got = _hl.sha256(open(path, "rb").read()).hexdigest()
    check(f"{name[:28]} sha256 일치", got == want, got[:16])

print("\n⑰-1 실제 구조 — 결정표 1행 · 천원표 2행")
for name, _, nrows, yoy in TSMC_FX:
    rows = C.drop_empty_columns(_tables(_fx(name))[0])
    hits = [i for i, r in enumerate(rows)
            if r and any(C.RE_NET_REVENUE.match(c) for c in r)]
    check(f"{name[:28]} Net Revenue 행 {nrows}개", len(hits) == nrows, str(hits))
    has = any(C.RE_YOY.search(c) for r in rows for c in r)
    check(f"{name[:28]} Y-o-Y {yoy}", has == yoy, str(has))

print("\n⑰-2 ★★ 수정 전 재현 보존 — 첫 행을 조용히 골랐다")
# ⛔ 이 절이 없으면 「고친 뒤 막힌다」만 남고 무엇을 막았는지 알 수 없다.


def PRE_FIX_first_row(rows):
    """b2c77ae 시점 동작 — Net Revenue 첫 행에서 break 하고 나머지를 버린다."""
    for ri, r in enumerate(rows):
        if r and any(C.RE_NET_REVENUE.match(c) for c in r):
            return ri
    return None


_t6 = _fx("2026-08-10_0001046179-26-000471_t6_NT-thousands.html")
_r6 = C.drop_empty_columns(_tables(_t6)[0])
_hits6 = [i for i, r in enumerate(_r6) if r and any(C.RE_NET_REVENUE.match(c) for c in r)]
check("★ 실제 천원표에 Net Revenue 행이 2개다 (합성 아님)", len(_hits6) == 2, str(_hits6))
check("★★ 수정 전 로직은 첫 행을 골랐다", PRE_FIX_first_row(_r6) == _hits6[0])
check("★★ 그때 둘째 행(누계)은 조용히 버려졌다", PRE_FIX_first_row(_r6) != _hits6[1])
check("★ 버려진 행이 실제로 누계 행이다",
      any("Jan" in c for c in _r6[_hits6[1]]), str(_r6[_hits6[1]][:2]))

print("\n⑰-3 ★ row identity 0/2+ → fail-closed (수정 후)")
# 최소변형: 천원표가 decision predicate 를 통과하게 만든다 (셀 문면 1곳)
_t6m = _t6.replace(">2025</font>", ">2025 Y-o-Y Increase (Decrease) %</font>", 1)
check("★ 최소변형이 적용됐다 (셀 1곳)", _t6m != _t6)
_rej = []
_f6 = C.find_decision_table(_tables(_t6m), "July", 2026, rejected=_rej)
check("★★ Net Revenue 2행 표는 후보가 되지 않는다", len(_f6) == 0, str(len(_f6)))
check("★ 거부 근거를 남긴다", len(_rej) >= 1, str(_rej))
if _rej:
    check("★ 근거에 후보 행 번호가 있다", _rej[0].get("net_revenue_rows") == _hits6,
          str(_rej[0].get("net_revenue_rows")))
    check("★ 근거에 표 번호가 있다", "table_index" in _rej[0], str(sorted(_rej[0])))
    check("★ 근거에 사유가 있다", "정확히 1" in _rej[0].get("reason", ""),
          _rej[0].get("reason", ""))
# 0건 — Net Revenue 가 없는 표
_none = C.find_decision_table(_tables("<html><body><table><tr><td>Period</td>"
                                      "<td>Y-o-Y</td></tr><tr><td>Gross</td>"
                                      "<td>1</td></tr></table></body></html>"),
                              "July", 2026)
check("★ Net Revenue 0건 → 후보 없음", len(_none) == 0)

print("\n⑰-3b ★ 기존 계약 두 가지 — 변이 시험이 드러낸 회귀 공백을 메운다")
# ⛔ 아래 둘은 **원래부터 있던 동작**인데 회귀가 없었다. 변이(U5·U6)를 주입해도
#    아무 검사도 실패하지 않아서 발견했다. 계약을 새로 만드는 것이 아니라 고정한다.
_yoy_less = ("<html><body><table>"
             "<tr><td>Period</td><td>July 2026</td></tr>"
             "<tr><td>Net Revenue</td><td>467,580</td></tr>"
             "</table></body></html>")
check("★ Y-o-Y 헤더가 없으면 결정표 후보가 아니다 (기존 계약)",
      len(C.find_decision_table(_tables(_yoy_less), "July", 2026)) == 0)
_row0 = ("<html><body><table>"
         "<tr><td>Net Revenue</td><td>467,580</td></tr>"
         "<tr><td>Y-o-Y Increase (Decrease) %</td><td>44.7</td></tr>"
         "</table></body></html>")
check("★ Net Revenue 가 첫 행(row 0)이면 후보가 아니다 (기존 계약)",
      len(C.find_decision_table(_tables(_row0), "July", 2026)) == 0)
# 양성 대조 — 위 거부가 「무조건 거부」가 아님을 보인다
check("★ 양성 대조: 실제 결정표는 여전히 후보다",
      len(C.find_decision_table(
          _tables(_fx("2026-08-10_0001046179-26-000471_t4_unit-unknown.html")),
          "July", 2026)) == 1)

print("\n⑰-4 ★ 무변형 원문에서 기존 P3 관측이 그대로 재현된다")
for name, _, nrows, yoy in TSMC_FX:
    if "_t4_" not in name:
        continue
    d = name[:10]
    mn, yr = TSMC_MONTH[d]
    html = _fx(name)
    found = C.find_decision_table(_tables(html), mn, yr)
    check(f"{mn} 결정표 후보 1건", len(found) == 1, str(len(found)))
    if not found:
        continue
    ti, rows, di = found[0]
    check(f"{mn} 단위 (Unit:NT$ million) 확인",
          bool(C.RE_UNIT_MILLION.search(C.strip_html(html))))
    b, probs = C.bind_columns(C.build_header(rows, di), rows[di], mn, yr)
    check(f"{mn} 결합 성공", b is not None, str(probs))
    if b and mn in P3_VALUES:
        got = (b["monthly_revenue"], b["monthly_yoy"],
               b["cumulative_revenue"], b["cumulative_yoy"])
        check(f"★ {mn} 값이 P3 기록과 같다 {P3_VALUES[mn]}", got == P3_VALUES[mn], str(got))

print("\n⑰-5 정적 — 범위를 넘지 않았다")
_c4src = open(os.path.join(ROOT, "collectors", "c4_sec_edgar_check.py"),
              encoding="utf-8").read()
_c4ast = __import__("ast").parse(_c4src)
_ast = __import__("ast")
_fdt = [n for n in _ast.walk(_c4ast) if isinstance(n, _ast.FunctionDef)
        and n.name == "find_decision_table"][0]
check("★ find_decision_table 에 첫 행 break 가 없다",
      not any(isinstance(x, _ast.Break) for x in _ast.walk(_fdt)))
check("★ 거부 근거 통로가 있다", "rejected" in [a.arg for a in _fdt.args.args])
# ⛔ 이번 범위 밖 — 넣지 않았음을 확인한다
check("⛔ table-level 2+ guard 를 넣지 않았다 (별건)",
      "표가 정확히 1건이 아니다" not in _c4src and "table_ambiguous" not in _c4src)
check("⛔ build_header 를 손대지 않았다",
      "is_data_row" not in _c4src)



# ══════════════════════════════════════════════════════════════════════
# ⑱ 최종 관측 후보 유일성 (CIO 승인 2026-08-16)
#
#   계약: L1 candidates → 각 후보에 L2 → L1∧L2 성공 후보 **전체** 수집
#         → 정확히 1건이면 선택 / 0건·2+ 면 evidence 와 함께 fail-closed
#
#   ★ 책임이 두 층으로 나뉜다 (CIO 판정)
#     `final_candidates` → 후보 **전체 보존**이 책임. 안에서 고르거나 축약하지 않는다.
#     `unique_candidate`  → 유일성 판정이 책임. 0/2+ 에서 거부하고 근거를 남긴다.
#     ⇒ 검사도 층을 나눠서 한다. 섞으면 어느 층이 틀렸는지 가리키지 못한다.
# ══════════════════════════════════════════════════════════════════════
print("\n⑱-C positive — 실제 fixture 3개월에서 L1∧L2 == 1 (T-1~T-4)")
for _name, _, _nr, _yoy in TSMC_FX:
    if "_t4_" not in _name:
        continue
    _d = _name[:10]
    _mn, _yr = TSMC_MONTH[_d]
    _tb = _tables(_fx(_name))
    _rej = []
    _l1 = C.find_decision_table(_tb, _mn, _yr, rejected=_rej)
    _fin = C.final_candidates(_tb, _mn, _yr)
    check(f"T-1 {_mn} L1 후보 1건", len(_l1) == 1, str(len(_l1)))
    check(f"T-2 {_mn} 최종 후보(L1∧L2) 1건", len(_fin) == 1, str(len(_fin)))
    _ch, _pb = C.unique_candidate(_fin)
    check(f"T-2b {_mn} 유일성 판정 통과", _ch is not None, str(_pb))
    if _ch and _mn in P3_VALUES:
        _b = _ch["bound"]
        check(f"T-3 {_mn} 값이 P3 기록과 같다",
              (_b["monthly_revenue"], _b["monthly_yoy"], _b["cumulative_revenue"],
               _b["cumulative_yoy"]) == P3_VALUES[_mn])
# T-4 천원표는 최종 후보에 들어오지 않는다
_t6n = "2026-08-10_0001046179-26-000471_t6_NT-thousands.html"
check("T-4 천원표는 최종 후보 0건", len(C.final_candidates(_tables(_fx(_t6n)), "July", 2026)) == 0)
# T-5 최소변형에서도 최종 후보는 1건 — 「가」를 채택하지 않은 판정을 고정한다
_t6mod = _fx(_t6n).replace(">2025</font>", ">2025 Y-o-Y Increase (Decrease) %</font>", 1)
_t4n = "2026-08-10_0001046179-26-000471_t4_unit-unknown.html"
_both = _tables(_fx(_t4n)) + _tables(_t6mod)
check("T-5 ★ 최소변형으로 천원표가 L1 을 통과해도 최종 후보는 1건",
      len(C.final_candidates(_both, "July", 2026)) == 1,
      str(len(C.final_candidates(_both, "July", 2026))))
_rej5 = []
C.final_candidates(_both, "July", 2026, rejected=_rej5)
check("T-5b ★ 그 탈락 근거가 남는다", len(_rej5) >= 1, str(len(_rej5)))
# ★ 위 t6mod 는 **L1(행 유일성)** 에서 걸린다 — L2 까지 가지 않는다.
#   L2 탈락을 실제로 시험하려면 L1 은 통과하고 L2 만 실패하는 입력이 필요하다.
#   실제 fixture 로 만들 수 있다: **대상월을 다른 달로 조회**하면 된다.
#   (7월 결정표를 June 으로 조회 → L1 통과 · 전년동월/누계 헤더 없음 → L2 실패)
_rej5b = []
_l1_only = C.find_decision_table(_tables(_fx(_t4n)), "June", 2026)
_fin_l2fail = C.final_candidates(_tables(_fx(_t4n)), "June", 2026, rejected=_rej5b)
check("T-5c ★ L1 은 통과한다 (전제 확인)", len(_l1_only) == 1, str(len(_l1_only)))
check("T-5d ★★ L2 실패 후보는 최종에 들어오지 않는다", len(_fin_l2fail) == 0,
      str(len(_fin_l2fail)))
check("T-5e ★★ L2 탈락 근거가 남는다", any("L2" in r["reason"] for r in _rej5b),
      str([r["reason"][:40] for r in _rej5b]))
check("T-5f ★ 근거에 헤더·데이터 행이 함께 있다",
      all("header" in r and "data_row" in r for r in _rej5b if "L2" in r["reason"]))

print("\n⑱-B ★ UNIT CARDINALITY TEST — 실제 TSMC 문서 구조가 아니다 (T-10~T-13)")
# ⛔⛔ 아래는 **단위 cardinality 시험**이다. 실제 원문 table object 를 함수의 입력
#     collection 에 두 번 넣어 계수 계약을 본다.
#     ⛔ 「TSMC 문서에 결정표가 둘이다」를 주장하지 않는다. markup 을 만들지 않았다.
#     (앞서 합성 FI 와 실측 증거가 한 번 섞인 적이 있어 경계를 명시한다.)
_t4tabs = _tables(_fx(_t4n))
_dup = _t4tabs + _t4tabs
_fin_dup = C.final_candidates(_dup, "July", 2026)
check("T-10 ★ final_candidates 는 2건을 **보존**한다 (고르지 않는다)",
      len(_fin_dup) == 2, str(len(_fin_dup)))
check("T-10b 보존된 후보의 table 번호가 서로 다르다",
      len({c["table_index"] for c in _fin_dup}) == 2,
      str([c["table_index"] for c in _fin_dup]))
_ch2, _pb2 = C.unique_candidate(_fin_dup)
check("T-10c ★ 유일성 판정이 2건에서 fail-closed 한다", _ch2 is None)
# ⛔ 부분문자열 검사는 「1건이 아니다」의 '1' 에 걸려 항상 참이었다.
#    후보 목록의 **정확한 렌더링**을 비교한다.
check("T-11 ★ 거부 근거에 후보 **전체**가 남는다 (첫 건만이 아니다)",
      str([c["table_index"] for c in _fin_dup]) in " ".join(_pb2), str(_pb2))
check("T-12 0건 → fail-closed · 사유 구분",
      C.unique_candidate([])[0] is None and "0건" in " ".join(C.unique_candidate([])[1]))
# T-13 순서 무관 — 다중집합으로 비교한다 (표시 순서는 달라도 후보 유실 금지)
import collections as _co
_a = C.final_candidates(_dup, "July", 2026)
_b2 = C.final_candidates(list(reversed(_dup)), "July", 2026)
check("T-13 ★ 순서를 바꿔도 후보 개수가 같다", len(_a) == len(_b2))
check("T-13b ★ 후보 다중집합이 같다 (중복 개수까지 보존)",
      _co.Counter(c["bound"]["monthly_revenue"] for c in _a)
      == _co.Counter(c["bound"]["monthly_revenue"] for c in _b2))
check("T-13c ★ 양쪽 모두 fail-closed",
      C.unique_candidate(_a)[0] is None and C.unique_candidate(_b2)[0] is None)
# ★ 서로 다른 실제 표를 섞어도 순서가 결과를 정하지 않는다 (t6 는 탈락)
_mix1 = _tables(_fx(_t6n)) + _t4tabs
_mix2 = _t4tabs + _tables(_fx(_t6n))
_m1, _m2 = C.final_candidates(_mix1, "July", 2026), C.final_candidates(_mix2, "July", 2026)
check("T-13d ★ 서로 다른 실제 표 2개 — 배치와 무관하게 후보 1건",
      len(_m1) == 1 and len(_m2) == 1, f"{len(_m1)}/{len(_m2)}")
check("T-13e ★ 그 값도 배치와 무관하게 같다",
      _m1[0]["bound"]["monthly_revenue"] == _m2[0]["bound"]["monthly_revenue"]
      == P3_VALUES["July"][0])

print("\n⑱-A 정적 — 구조 (T-6~T-9)")
_fc_fn = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
          and n.name == "final_candidates"][0]
_uq_fn = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
          and n.name == "unique_candidate"][0]
check("T-6 ★ final_candidates 에 break 제어문이 없다",
      not any(isinstance(x, ast.Break) for x in ast.walk(_fc_fn)))
check("T-6b ★ final_candidates 가 안에서 유일성을 판정하지 않는다 (책임 분리)",
      "unique_candidate" not in ast.get_source_segment(src, _fc_fn))
check("T-7 ★ 호출부가 unique_candidate 로 명시적으로 판정한다",
      "unique_candidate(cands)" in src)
check("T-8 ★ 거부 evidence 통로가 있다",
      "rejected" in [a.arg for a in _fc_fn.args.args])
check("T-9 ⛔ L3 는 여전히 문서 단위다 (범위 밖 미변경)",
      "verify_unit_million(text)" in src)
check("T-9b ⛔ build_header 를 손대지 않았다 (범위 밖)", "is_data_row" not in src)


print(f"\n{len(ok)} PASS / {len(bad)} FAIL")
sys.exit(1 if bad else 0)