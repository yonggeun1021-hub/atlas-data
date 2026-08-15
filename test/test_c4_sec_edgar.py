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
check("Decision 은 bind_columns 결과에서만 온다",
      "decision = bound" in ast.get_source_segment(src, fns["main"])
      or "decision = bound" in src)
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

print(f"\n{len(ok)} PASS / {len(bad)} FAIL")
sys.exit(1 if bad else 0)
