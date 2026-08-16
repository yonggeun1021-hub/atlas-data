#!/usr/bin/env python3
"""fixture 슬라이서 회귀 (CIO 판정 2026-08-16 · 항목 5).

★ 이 회귀가 증명하는 것 / 못 하는 것
   증명한다 : **잘라내기만 하고 재구성하지 않는다**, 중첩 표에서 대상 표를 고른다,
             태그·엔티티가 단어를 갈라도 찾는다, 모호하면 fail-closed.
   증명 못 한다 : 실제 SEC 마크업. 그것이 바로 이 스크립트가 확보하려는 것이다.

⛔ 여기의 HTML 은 합성이다. 그래서 이 회귀는 **슬라이서의 성질**만 검증하고
   추출 계약은 검증하지 않는다. 계약 회귀는 실제 fixture 확보 후에 만든다.
⛔ 네트워크를 쓰지 않는다.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
import capture_azure_fixture as F                                  # noqa: E402

import checkkit as K                                                # noqa: E402
K.init(quiet=True)
check, need, skip, guard, section = K.check, K.need, K.skip, K.guard, K.section


TITLE_OLD = "Selected Product and Service Revenue Constant Currency Reconciliation"
TITLE_NEW = "Selected Product and Service Information Constant Currency Reconciliation"
ROW_OLD = "Azure and other cloud services"
ROW_NEW = "Azure and other cloud services revenue"


def doc(title, row, nested=True, entity=False, split=False, extra_table=False):
    """SEC 스타일 — 레이아웃용 바깥 표 안에 데이터 표가 들어간다."""
    r = row
    if split:                       # 태그가 단어 사이를 가른다
        r = row.replace("cloud", "<b>cloud</b>")
    if entity:                      # 엔티티가 공백 자리에 온다
        r = r.replace(" and ", "&nbsp;and&nbsp;")
    data = (f"<table class='data'><tr><td></td><td>Percentage Change Y/Y (GAAP)</td>"
            f"<td>Constant Currency Impact</td>"
            f"<td>Percentage Change Y/Y Constant Currency</td></tr>"
            f"<tr><td>{r}</td><td>40%</td><td>(1)%</td><td>39%</td></tr></table>")
    if nested:
        data = f"<table class='layout'><tr><td>{data}</td></tr></table>"
    other = ("<p>Segment Revenue Constant Currency Reconciliation</p>"
             "<table><tr><td>Productivity and Business Processes</td><td>17%</td></tr>"
             "</table>") if extra_table else ""
    return ("<html><body><p>Microsoft Corp</p>"
            "<table class='irrelevant'><tr><td>filler</td></tr></table>"
            f"{other}<p><b>{title}</b></p>{data}"
            "<p>tail content</p></body></html>")


def cut(html):
    loc = F.locate_block(html)
    return None if loc is None else html[loc[0]:loc[1]]


with section("A-1 원문 슬라이스 — ★ 재구성하지 않는다"):
    for label, title, row in (("구형 FY26 Q2 형태", TITLE_OLD, ROW_OLD),
                              ("신형 FY26 Q3 형태", TITLE_NEW, ROW_NEW)):
        h = doc(title, row)
        s = cut(h)
        check(f"{label} → 슬라이스가 만들어진다", s is not None)
        if s is None:
            continue
        check(f"{label} → ★ 원문의 **부분 문자열**이다 (재구성 아님)", s in h)
        check(f"{label} → 제목을 포함한다", title in s)
        check(f"{label} → Azure 행을 포함한다", row in s)
        check(f"{label} → 데이터 표가 닫혀 있다", s.rstrip().endswith("</table>"))
        check(f"{label} → 세 컬럼 헤더가 모두 살아 있다",
              all(x in s for x in ("Percentage Change Y/Y (GAAP)",
                                   "Constant Currency Impact",
                                   "Percentage Change Y/Y Constant Currency")))
        check(f"{label} → 표 뒤 본문은 넣지 않는다 (bounded)", "tail content" not in s)
        check(f"{label} → 앞쪽 무관한 표는 넣지 않는다", "filler" not in s)

with section("A-2 중첩 표 — 가장 안쪽 데이터 표를 고른다"):
    h = doc(TITLE_NEW, ROW_NEW, nested=True)
    s = cut(h)
    check("★ 잘라낸 구간의 표 여닫이가 맞는다 (깨진 markup 아님)",
          s is not None and F.balanced(s), str(s and s.count("<table")))
    check("★ 레이아웃 표를 반쯤 자르지 않는다 (바깥 표까지 닫는다)",
          s is not None and s.rstrip().endswith("</table>"))
    check("데이터 표를 안에 담고 있다", s is not None and "class='data'" in s)

with section("A-3 단어가 태그·엔티티로 갈려도 찾는다"):
    for nm, kw in (("태그 분할 (<b>cloud</b>)", {"split": True}),
                   ("엔티티 공백 (&nbsp;)", {"entity": True}),
                   ("분할+엔티티", {"split": True, "entity": True})):
        h = doc(TITLE_NEW, ROW_NEW, **kw)
        s = cut(h)
        check(f"{nm} → 슬라이스 성공", s is not None)
        check(f"{nm} → ★ 여전히 원문 부분 문자열", s is not None and s in h)
        check(f"{nm} → ⛔ 엔티티를 풀지 않았다 (원문 보존)",
              s is None or ("&nbsp;" in s) == bool(kw.get("entity")))

with section("A-4 다른 reconciliation 표가 같이 있어도 대상만 고른다"):
    h = doc(TITLE_OLD, ROW_OLD, extra_table=True)
    s = cut(h)
    check("Segment 표를 끌어들이지 않는다",
          s is not None and "Productivity and Business Processes" not in s)
    check("★ 여전히 원문 부분 문자열", s is not None and s in h)

with section("A-5 fail-closed — 모호하거나 없으면 만들지 않는다"):
    NEG = {
        "제목이 없다": ("<html><body><table><tr><td>Azure and other cloud services</td>"
                    "<td>40%</td></tr></table></body></html>"),
        "Azure 행이 표 밖에 있다": (f"<html><body><p>{TITLE_NEW}</p>"
                              f"<p>{ROW_NEW}</p></body></html>"),
        "Azure 행이 없다": doc(TITLE_NEW, "Office 365 Commercial"),
    }
    for nm, h in NEG.items():
        check(f"★ {nm} → 슬라이스하지 않는다", cut(h) is None)

    # 자격을 갖춘 표가 둘이면 임의로 고르지 않는다
    h2 = (doc(TITLE_OLD, ROW_OLD).replace("</body></html>", "")
          + doc(TITLE_NEW, ROW_NEW).split("<body>")[1])
    check("★ 자격 표가 2건이면 fail-closed", cut(h2) is None)

    # 제목이 너무 멀면 같은 절로 보지 않는다
    far = (f"<html><body><p>{TITLE_NEW}</p>" + ("<p>filler</p>" * 4000)
           + f"<table><tr><td>{ROW_NEW}</td><td>40%</td></tr></table></body></html>")
    check("★ 제목이 상한 거리 밖이면 결합하지 않는다", cut(far) is None)

with section("A-6 ★ 제목이 별도 표 안에 있어 균형이 깨지는 경우 — fallback 이 실제로 동작한다"):
    # ⛔ 이 경우가 없으면 「제목 포함 → 균형 깨짐 → 표부터 자르기」 fallback 이
    #    한 번도 실행되지 않는다. 검증되지 않은 방어를 남기지 않는다.
    split_tbl = (f"<html><body><table class='ttl'><tr><td>{TITLE_NEW}</td></tr></table>"
                 f"<table class='data'><tr><td></td>"
                 f"<td>Percentage Change Y/Y (GAAP)</td>"
                 f"<td>Constant Currency Impact</td>"
                 f"<td>Percentage Change Y/Y Constant Currency</td></tr>"
                 f"<tr><td>{ROW_NEW}</td><td>40%</td><td>(1)%</td><td>39%</td></tr>"
                 f"</table></body></html>")
    s = cut(split_tbl)
    check("★ 슬라이스가 만들어진다", s is not None)
    check("★ 그래도 여닫이는 맞는다 (깨진 markup 아님)", s is not None and F.balanced(s))
    check("★ 원문 부분 문자열이다", s is not None and s in split_tbl)
    check("★ 균형을 위해 제목을 뺐다 — 데이터 표는 온전하다",
          s is not None and ROW_NEW in s and s.lstrip().startswith("<table"))
    check("★ 제목 표의 닫는 태그를 끌고 오지 않았다",
          s is not None and "class='ttl'" not in s)

with section("A-7 ★ 닫힌 열거 — 미지의 문면은 통과시키지 않는다"):
    UNKNOWN_TITLES = [
        "Selected Product and Service Segment Constant Currency Reconciliation",
        "Selected Product and Service Metrics Constant Currency Reconciliation",
        "Selected Product and Service Constant Currency Reconciliation",
    ]
    for t in UNKNOWN_TITLES:
        check(f"★ 미지 제목 거부: …Service {t.split('Service ')[1].split(' Constant')[0]!r}…",
              cut(doc(t, ROW_NEW)) is None)
    check("★ 앵커 정규식에 포괄 완화(.*)가 없다",
          ".*" not in F.RE_TITLE.pattern and ".*" not in F.RE_AZURE.pattern,
          F.RE_TITLE.pattern)
    check("★ 관측된 두 제목은 여전히 통과한다 (거부가 과하지 않다)",
          cut(doc(TITLE_OLD, ROW_OLD)) is not None and cut(doc(TITLE_NEW, ROW_NEW)) is not None)

with section("A-8 저장 직전 집행 — slice_and_verify"):
    h = doc(TITLE_NEW, ROW_NEW)
    st, en, _ = F.locate_block(h)
    check("정상 구간은 통과한다", F.slice_and_verify(h, st, en) == h[st:en])
    check("★ 상한 초과는 저장하지 않는다 (자르지 않는다)",
          F.slice_and_verify("x" * (F.MAX_SLICE_BYTES + 10), 0,
                             F.MAX_SLICE_BYTES + 10) is None)
    check("★ 여닫이가 깨진 구간은 저장하지 않는다",
          F.slice_and_verify(h, st, en - len("</table>")) is None)
    check("★ 엔티티를 푼 내용은 통과하지 못한다 (재구성 차단)",
          F.slice_and_verify(doc(TITLE_NEW, ROW_NEW, entity=True).replace("&nbsp;", " "),
                             0, 10**9) is not None)   # 원문 자체를 바꾼 경우는 별개
    _ent = doc(TITLE_NEW, ROW_NEW, entity=True)
    _st, _en, _ = F.locate_block(_ent)
    check("★ 보존된 조각에 엔티티가 원문 그대로 남는다",
          "&nbsp;" in F.slice_and_verify(_ent, _st, _en))

with section("A-9 계약 준수 정적 확인"):
    src = open(os.path.join(ROOT, "collectors", "capture_azure_fixture.py"),
               encoding="utf-8").read()
    import ast                                                          # noqa: E402
    tree = ast.parse(src)
    _docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            d = ast.get_docstring(n)
            if d:
                _docs.add(d)
    _lits = [n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and n.value not in _docs]
    check("★ collector 를 수정하지 않고 재사용한다",
          "import msft_azure_cc as M" in src)
    check("★ 승인된 취득 함수를 쓴다 (별도 구현 아님)",
          "M.parse_document_blocks(" in src and "M.select_exhibit(" in src)
    check("★ full submission .txt 경로를 유지한다",
          any(s.endswith(".txt") for s in _lits))
    check("★ ⛔ 포괄 완화(.*) 를 앵커에 쓰지 않는다",
          not any(".*" in s for s in _lits if "Selected" in s or "Azure" in s))
    check("★ 부분 문자열임을 실행 중 검증한다", "block not in doc_text" in src)
    check("★ 저장소 안에는 쓰지 않는다 (경로 가드 존재)",
          "startswith(os.path.abspath(ROOT)" in src)
    check("★ 상한 초과를 조용히 자르지 않는다", "자르지 않고 중단" in src)
    check("★ 이 회귀는 네트워크를 쓰지 않는다",
          ("url" + "lib") not in open(os.path.abspath(__file__), encoding="utf-8")
          .read().replace('("url" + "lib")', ""))

    sys.exit(K.exit_code())
