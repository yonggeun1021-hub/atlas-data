#!/usr/bin/env python3
"""C4 Gate — TSMC 월매출 6-K 의 **원문 HTML 표 블록**을 bounded fixture 로 보존한다.
(CIO 승인 2026-08-16 · raw fixture capture 만)

★ 왜 필요한가
   C4 first-match FI 를 **합성 문서 위에서** 했다. MSFT 때 확립한 증거 규율
   (실제 원문 → 회귀 → 수정)을 C4 에도 그대로 적용하기 위해 원문을 먼저 확보한다.

⛔ 이 실행이 하지 않는 것
   · `c4_sec_edgar_check` 수정 — **읽기 전용으로 재사용만** 한다
   · C4 parser 수정 · P3 reopen · common helper 변경 (전부 미승인)
   · 장기 역사 조사 — 대상은 아래 3개월로 **고정**한다
   · 저장소 쓰기 · Rule 상태 변경 · 값 판정

★ 절대 규칙 — 재구성하지 않는다
   원문을 **잘라내기만** 한다. 파싱 후 재직렬화·정규화·태그 보정 없음.
   `slice_and_verify` 가 저장 직전에 부분 문자열·상한·`<table>` 균형을 집행한다.
   (MSFT capture 도구에서 이미 회귀로 검증된 것을 재사용한다)

★ MSFT capture 와 다른 점
   MSFT 는 대상 표 **1개**를 잡았다. 여기서는 `Net Revenue` 를 가진 표를 **전부**
   잡는다 — million 표와 thousands 표의 구조 차이를 보는 것이 이번 Gate 의 목적이다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
# ★ C4 는 **읽기 전용 재사용**이다. 수정하지 않는다.
import c4_sec_edgar_check as C                                      # noqa: E402
# ★ 슬라이싱 유틸은 MSFT capture 도구에서 이미 회귀로 검증된 것을 재사용한다.
#   ⛔ 계약이 아니라 범용 유틸이다 (오프셋 보존 · 표 구간 · 균형 · 저장 직전 집행).
from capture_azure_fixture import (text_with_offsets, table_spans,  # noqa: E402
                                   outermost_span_after, balanced,
                                   slice_and_verify, MAX_SLICE_BYTES)

# ── 대상 — ⛔ CIO 가 정한 경계. 여기서 늘리지 않는다 ────────────────────
#   2026-06 · 2026-07 = 현재 C4/P3 가 근거로 삼은 달
#   2026-05           = 구조 변화 확인용 인접 달
TARGET_MONTHS = [(2026, 5), (2026, 6), (2026, 7)]

MONTH_NAME = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May",
              6: "June", 7: "July", 8: "August", 9: "September", 10: "October",
              11: "November", 12: "December"}

# 단위 선언 — 표 identity 의 핵심 신호. 최소 주변 markup 으로 함께 보존한다.
#   ⛔ 관측된 두 문면만 쓴다. 새 어휘를 만들지 않는다.
RE_UNIT_THOUSANDS = re.compile(r"\(\s*in\s+NT\$\s*thousands?\s*\)", re.I)
MAX_UNIT_DISTANCE = 40_000
POLITE_DELAY_SEC = 0.5


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def net_revenue_rows(rows) -> list:
    """`Net Revenue` 와 일치하는 행 번호 전부. ⛔ 첫 행에서 멈추지 않는다."""
    return [i for i, r in enumerate(rows)
            if any(C.RE_NET_REVENUE.match(c.strip()) for c in r)]


def unit_label(raw: str, table_start: int):
    """표 앞쪽의 단위 선언을 **의미로** 찾는다. (종류, 원문 인덱스) 또는 None."""
    text, off = text_with_offsets(raw)
    best = None
    for kind, rx in (("NT$ million", C.RE_UNIT_MILLION),
                     ("NT$ thousands", RE_UNIT_THOUSANDS)):
        for m in rx.finditer(text):
            pos = off[m.start()]
            if pos < table_start and table_start - pos <= MAX_UNIT_DISTANCE:
                if best is None or pos > best[1]:
                    best = (kind, pos)
    return best


def capture_tables(raw: str) -> list:
    """`Net Revenue` 를 가진 표를 **전부** 잘라낸다. 고르지 않는다."""
    p = C.TableCollector()
    p.feed(raw)
    spans = table_spans(raw)
    out = []
    for ti, rows in enumerate(p.tables):
        rr = C.drop_empty_columns(rows)
        nr = net_revenue_rows(rr)
        if not nr:
            continue
        # 이 표의 원문 구간 — 표 안 첫 `Net Revenue` 텍스트 위치로 찾는다
        text, off = text_with_offsets(raw)
        m = None
        for mm in re.finditer(r"Net\s+Revenue", text, re.I):
            pos = off[mm.start()]
            cand = [s for s in spans if s[0] <= pos < s[1]]
            if cand:
                inner = min(cand, key=lambda s: s[1] - s[0])
                if not any(o["table_span"] == list(inner) for o in out):
                    m = inner
                    break
        if m is None:
            continue
        t_start, t_end = m
        outer = outermost_span_after(spans, t_start, 0)
        if outer is not None:
            t_start, t_end = outer
        unit = unit_label(raw, t_start)
        start = t_start
        unit_included = False
        if unit is not None:
            lt = raw.rfind("<", max(0, unit[1] - 300), unit[1])
            cand_start = lt if lt != -1 else unit[1]
            if balanced(raw[cand_start:t_end]):
                start, unit_included = cand_start, True
        block = slice_and_verify(raw, start, t_end)
        if block is None:
            print(f"    ✗ table[{ti}] 슬라이스 거부 — 저장하지 않는다")
            continue
        out.append({
            "table_index": ti,
            "table_span": [t_start, t_end],
            "slice_start": start, "slice_end": t_end,
            "slice_chars": len(block), "slice_sha256": sha(block),
            "unit_declaration": unit[0] if unit else None,
            "unit_included_in_slice": unit_included,
            "net_revenue_row_count": len(nr),
            "net_revenue_rows": nr,
            "has_yoy_header": bool(any(C.RE_YOY.search(c) for r in rr for c in r)),
            "rows": len(rr), "cols": max(len(r) for r in rr),
            "verbatim_substring_of_document": True,
            "_block": block,
        })
    return out


def capture_month(year, month, outdir):
    mn = MONTH_NAME[month]
    print(f"\n  ── {mn} {year}")
    ay, am = (year + 1, 1) if month == 12 else (year, month + 1)
    win = f"{ay:04d}-{am:02d}"

    time.sleep(POLITE_DELAY_SEC)
    _, raw = C.get(C.SUBMISSIONS_URL)
    rec = json.loads(raw.decode("utf-8"))["filings"]["recent"]
    n = len(rec["form"])
    cands = [{"accession": rec["accessionNumber"][i],
              "filing_date": rec["filingDate"][i],
              "primary_doc": rec.get("primaryDocument", [""] * n)[i]}
             for i in range(n)
             if rec["form"][i] == "6-K" and rec["filingDate"][i].startswith(win)]
    # ⛔ 파일명은 순서 hint 전용 — 판정은 문서 내용으로 한다 (C4 와 같은 규율)
    cands.sort(key=lambda c: (0 if "revenue" in c["primary_doc"].lower() else 1,
                              c["filing_date"]))
    print(f"    window {win}-* · 6-K 후보 {len(cands)}건")

    hit = None
    for c in cands:
        url = f"{C.ARCHIVE_BASE}/{c['accession'].replace('-', '')}/{c['primary_doc']}"
        time.sleep(POLITE_DELAY_SEC)
        try:
            _, body = C.get(url)
        except Exception as e:                                      # noqa: BLE001
            print(f"    ✗ {c['primary_doc']} 요청 실패 — {type(e).__name__}")
            continue
        html_text = body.decode("utf-8", errors="replace")
        checks = C.identify(C.strip_html(html_text), mn, year)
        if all(v for _, v, _ in checks):
            print(f"    ✓ 월매출 보고서 확정 — {c['primary_doc']}")
            hit = (c, url, html_text)
            break
        print(f"    · {c['primary_doc']} 아님 ({[l for l, v, _ in checks if not v]})")
    if hit is None:
        print("    ✗ 월매출 보고서를 확정하지 못했다 — 이 달은 건너뛴다")
        return None

    c, url, html_text = hit
    tables = capture_tables(html_text)
    print(f"    `Net Revenue` 를 가진 표 {len(tables)}건")

    # ★ 현재 parser 가 원문에서 어떤 candidate set 을 만드는가 (관찰만)
    p = C.TableCollector(); p.feed(html_text)
    found = C.find_decision_table(p.tables, mn, year)
    print(f"    find_decision_table 후보 {len(found)}건 "
          f"→ table {[t for t, _, _ in found]}")

    recs = []
    for t in tables:
        name = (f"{c['filing_date']}_{c['accession']}_t{t['table_index']}_"
                f"{(t['unit_declaration'] or 'unit-unknown').replace('$','').replace(' ','-')}.html")
        with open(os.path.join(outdir, name), "w", encoding="utf-8", newline="") as f:
            f.write(t.pop("_block"))
        t["fixture_file"] = name
        print(f"      ✓ {name}  {t['slice_chars']}자 · 단위 {t['unit_declaration']!r} · "
              f"NetRevenue 행 {t['net_revenue_row_count']}개 · Y-o-Y {t['has_yoy_header']}")
        recs.append(t)

    return {"target": f"{year}-{month:02d}", "month_name": mn,
            "accession": c["accession"], "filing_date": c["filing_date"],
            "primary_doc": c["primary_doc"], "document_url": url,
            "document_sha256": sha(html_text), "document_chars": len(html_text),
            "decision_table_candidates": [t for t, _, _ in found],
            "decision_table_candidate_count": len(found),
            "tables": recs}


def main() -> int:
    print("=" * 74)
    print("C4 Gate — TSMC 월매출 6-K 원문 표 블록 보존")
    print(f"  대상 {['%d-%02d' % t for t in TARGET_MONTHS]}  ⛔ 여기서 늘리지 않는다")
    print("  ⛔ 원문을 잘라내기만 한다 — 재구성·정규화 없음")
    print("  ⛔ C4 parser 를 수정하지 않는다 · P3 를 건드리지 않는다")
    print("  ⛔ 저장소에 쓰지 않는다 (출력은 FIXTURE_OUT)")
    print("=" * 74)

    outdir = os.environ.get("FIXTURE_OUT") or tempfile.mkdtemp(prefix="atlas_tsmc_")
    if os.path.abspath(outdir).startswith(os.path.abspath(ROOT) + os.sep):
        print(f"\n⛔ FIXTURE_OUT 이 저장소 안이다 ({outdir}) — 중단")
        return 1
    os.makedirs(outdir, exist_ok=True)
    print(f"\n출력 {outdir}")

    out = [r for r in (capture_month(y, m, outdir) for y, m in TARGET_MONTHS) if r]

    with open(os.path.join(outdir, "MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump({"captured": out, "attempted": len(TARGET_MONTHS),
                   "note": "각 fixture 는 원문 문서의 부분 문자열이다. "
                           "재구성·정규화하지 않았다. "
                           "`Net Revenue` 를 가진 표를 전부 보존했다 — 고르지 않았다."},
                  f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 74)
    print(f"③ 결과 — 시도 {len(TARGET_MONTHS)}개월 · 보존 {len(out)}개월")
    for r in out:
        print(f"  {r['target']}  표 {len(r['tables'])}건 · "
              f"결정표 후보 {r['decision_table_candidate_count']}건")
        for t in r["tables"]:
            print(f"     {t['unit_declaration']!r:18} NetRevenue행 {t['net_revenue_row_count']} "
                  f"· Y-o-Y {t['has_yoy_header']} · {t['slice_chars']}자")
    if len(out) < 2:
        print("\n⛔ 2개월 미만 — 구조 비교가 불가능하다")
        return 1
    print("\n★ 이 fixture 는 원문 슬라이스다. 값 판정·parser 수정은 여기서 하지 않는다.")
    print("⛔ P3 · RULE-0003/0007/0008 상태는 이 실행으로 바뀌지 않는다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
