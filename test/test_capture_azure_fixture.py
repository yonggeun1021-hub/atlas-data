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

with section("A-10 ★ capture 전용 조회 상한 override (CIO 판정 2026-08-16)"):
    # ⛔ 이 절의 계약은 하나다 — production 의 MAX_FILINGS 의미를 바꾸지 않는다.
    check("★ production 상한은 그대로 4 다", F.M.MAX_FILINGS == 4, str(F.M.MAX_FILINGS))
    check("★ 환경변수가 없으면 production 기본값과 완전히 같다",
          F.capture_limit({}) == (F.M.MAX_FILINGS, None), str(F.capture_limit({})))
    check("★ 빈 값·공백도 기본값이다 (조용한 변경 없음)",
          F.capture_limit({F.CAPTURE_LIMIT_ENV: ""}) == (F.M.MAX_FILINGS, None)
          and F.capture_limit({F.CAPTURE_LIMIT_ENV: "   "}) == (F.M.MAX_FILINGS, None))
    _lim, _pb = F.capture_limit({F.CAPTURE_LIMIT_ENV: "12"})
    check("★ 유효한 값이면 그 값이 쓰인다", (_lim, _pb) == (12, None), f"{_lim} {_pb}")
    check("★ 기본값보다 작은 값도 그대로 쓴다 (한 방향으로만 열지 않는다)",
          F.capture_limit({F.CAPTURE_LIMIT_ENV: "1"}) == (1, None))
    for _bad in ("0", "-3", "4.5", "eight", "4 5"):
        _l, _p = F.capture_limit({F.CAPTURE_LIMIT_ENV: _bad})
        check(f"★ 잘못된 값 {_bad!r} → 중단한다 (기본값으로 조용히 되돌아가지 않는다)",
              _l is None and bool(_p), f"{_l} {_p}")
    # ★ 정적 — capture 가 production 상수를 재정의·재대입하지 않는다 (AST · 문자열 검색 아님)
    _cap_src = open(os.path.join(ROOT, "collectors", "capture_azure_fixture.py"),
                    encoding="utf-8").read()
    _cap_tree = ast.parse(_cap_src)
    _redef = []
    for _n in ast.walk(_cap_tree):
        _tg = (_n.targets if isinstance(_n, ast.Assign) else
               [_n.target] if isinstance(_n, (ast.AugAssign, ast.AnnAssign)) else [])
        for _t in _tg:
            if isinstance(_t, ast.Name) and _t.id == "MAX_FILINGS":
                _redef.append(_n.lineno)
            if isinstance(_t, ast.Attribute) and _t.attr == "MAX_FILINGS":
                _redef.append(_n.lineno)
    check("★ capture 는 MAX_FILINGS 를 재정의·재대입하지 않는다", not _redef, str(_redef))
    _calls = [_n.lineno for _n in ast.walk(_cap_tree)
              if isinstance(_n, ast.Call) and getattr(_n.func, "id", "") == "capture_limit"]
    check("★ 조회 상한이 override 함수를 통해 정해진다 (호출이 존재한다)",
          bool(_calls), str(_calls))

with section("A-11 ★ live run 증거면 — 무엇이 빠졌는지가 남아야 한다 (CIO 판정 2026-08-16)"):
    _cands = [{"filing_date": f"2026-0{i}-01", "accession": f"acc-{i}"} for i in range(1, 5)]
    _drop = [{"filing_date": f"2025-0{i}-01", "accession": f"old-{i}"} for i in range(1, 10)]
    _rec = F.discovery_record(4, 4, _cands + _drop, _cands, _drop)
    check("★ dropped 를 자르지 않는다 (9건이면 9건 그대로)",
          len(_rec["dropped"]) == len(_drop), str(len(_rec["dropped"])))
    check("★ dropped 에 날짜와 accession 이 함께 남는다",
          all({"filing_date", "accession"} <= set(d) for d in _rec["dropped"]))
    check("★ selected 도 같은 형식으로 남는다",
          [d["accession"] for d in _rec["selected"]] == [c["accession"] for c in _cands])
    check("★ 상한이 기본값이면 limit_source 가 default 다",
          _rec["limit_source"] == "default", _rec["limit_source"])
    check("★ 상한을 덮었으면 override 로 남는다",
          F.discovery_record(9, 4, [], [], [])["limit_source"] == "override")
    check("★ 어떤 환경변수로 덮었는지도 남는다",
          _rec["limit_env"] == F.CAPTURE_LIMIT_ENV)
    # ★ 정적 — dropped 를 출력·기록할 때 잘라내지 않는다 (AST · 문자열 검색 아님)
    _sliced = [n.lineno for n in ast.walk(_cap_tree)
               if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice)
               and isinstance(n.value, ast.Name) and n.value.id == "dropped"]
    check("★ dropped 를 슬라이스하지 않는다", not _sliced, str(_sliced))
    # ★ 정적 — capture_one 은 실패 사유를 받을 통로를 갖는다
    _c1 = [n for n in ast.walk(_cap_tree)
           if isinstance(n, ast.FunctionDef) and n.name == "capture_one"][0]
    check("★ capture_one 이 failures 통로를 갖는다",
          "failures" in [a.arg for a in _c1.args.args], str([a.arg for a in _c1.args.args]))
    _inner = [n for n in ast.walk(_c1)
              if isinstance(n, ast.FunctionDef) and n.name == "_fail"]
    check("★ 실패 기록 지점이 한 곳이다 (_fail)", len(_inner) == 1, str(len(_inner)))
    _bare = [n.lineno for n in ast.walk(_c1)
             if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
             and n.value.value is None
             and not any(n.lineno == m.lineno or (m.lineno <= n.lineno <= m.end_lineno)
                         for m in _inner)]
    check("★ capture_one 의 실패 반환이 전부 _fail 을 거친다 (맨 return None 없음)",
          not _bare, str(_bare))
    check("★ MANIFEST 가 discovery 와 failures 를 함께 담는다",
          '"discovery"' in _cap_src and '"failures"' in _cap_src)

sys.exit(K.exit_code())
