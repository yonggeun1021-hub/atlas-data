#!/usr/bin/env python3
"""RULE-0021 — MSFT Azure constant-currency 성장률 관측 (CIO 승인 2026-08-15).

★ 이 run 의 목적 (CIO 판정 그대로)
   `AVAILABLE` 숫자를 늘리는 것이 아니다. P2-b 에서 확정한 **document-semantic
   extraction 계약이 실제 GitHub-hosted acquisition 에서도 재현되는지** 검증한다.

★ 관측 계약
   · 대상  Microsoft 가 공식 공표한 `Azure and other cloud services` 성장률
   · 값    **constant-currency growth**
   · collector 책임은 **raw observation 까지**다.

⛔ 이 collector 가 하지 않는 것
   · `45% 기준선` · `3%p 이상 하회` 판정 — **evaluator 층이다.** 여기서 계산하지 않는다.
   · GAAP growth 와 constant-currency growth 를 혼용
   · 명명된 Azure 행/항목 밖의 값으로 fallback
   · 값이 없거나 구조가 달라졌을 때의 추정·대체 (fail-closed)
   · 저장소 파일 생성 · 수정 · commit · push
   · Rule 상태 변경 · evaluator 연결

★ 발견 계약 (P3/C4 와 같은 방식)
   submissions 의 **form=8-K AND items 에 2.02(Results of Operations)** 로 후보를 좁히고,
   각 filing 의 index 에서 **EX-99.1** 을 지목한 뒤, **문서 내용**으로 실적 발표문임을
   확정한다. ⛔ 파일명·날짜만으로 고르지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
# ★ 표 파싱 유틸은 C4 에서 이미 승인·검증된 것을 재사용한다. 중복 구현하지 않는다.
from c4_sec_edgar_check import (TableCollector, strip_html, drop_empty_columns,  # noqa: E402
                                build_header, evidence, get)

CIK = "0000789019"
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/789019"

EARNINGS_ITEM = "2.02"          # Results of Operations and Financial Condition
EXHIBIT_TYPE = "EX-99.1"

# ══════════════════════════════════════════════════════════════════════
# Exhibit identity 계약 (CIO 판정 2026-08-15)
#   ⛔ `index.json` 의 `type` 을 SEC document type 으로 쓰지 않는다 — 실측 결과
#      그 필드는 `text.gif` · `compressed.gif` 두 값뿐인 **디렉터리 아이콘 종류**다.
#   ★ Primary identity source = **full submission `.txt`** 의 `<DOCUMENT>` 블록
#        <TYPE> · <SEQUENCE> · <FILENAME> · <DESCRIPTION>
#     ⛔ `{accession}-index-headers.html` 로 대체하지 않는다 — **CIO 불승인**
#        (2026-08-16). 그 문서에서도 동일 필드가 관측되는 것은 사실이나, SEC 가
#        「해당 filing 에서 제출된 모든 document」의 원본 경로로 명시하는 것은 full
#        `.txt` 이고 `-index-headers.html` 은 별도 제공물이다. **현재 표본에서
#        같다는 것만으로 동등하다고 간주하지 않는다** — P3 에서 엄격히 적용한 원칙을
#        4KB 절약을 위해 P2 에서 풀지 않는다. 성능 문제가 실제로 발생하면 그때
#        동등성을 별도 검증해 계약 변경안을 올린다.
#   ★ Secondary cross-check = `{accession}-index.html` 의 Type 컬럼
#   ⛔ 파일명 패턴(`ex99_1`)은 **hint 로만** 쓰고 identity 근거로 쓰지 않는다.
#   ⛔ primary 와 secondary 가 충돌하거나 후보가 1건이 아니면 fail-closed.
#      「EX-99.1 이면 무조건 한 파일」이라는 가정도 하지 않는다 — 복수 EX-99.1 이
#      실재하므로 1건이 아니면 ambiguity 로 막는다.
RE_DOC_BLOCK = re.compile(
    r"<TYPE>(?P<type>[^\s<]+)\s*"
    r"(?:<SEQUENCE>(?P<seq>[^\s<]*)\s*)?"
    r"(?:<FILENAME>(?P<filename>[^\s<]*)\s*)?"
    r"(?:<DESCRIPTION>(?P<desc>[^\n<]*))?", re.I)
FILENAME_HINT = re.compile(r"ex.?99.?1", re.I)      # ⛔ hint 전용

# 실패 로그 상한 — 후보 전체를 남기되 무한정 쏟지 않는다.
CAND_LOG_LIMIT = 20

# ⛔ 상한. 상한에 걸려 못 본 후보는 반드시 로그에 남긴다 (조용한 절단 금지).
MAX_FILINGS = 4                 # 복수 과거 분기 재현 확인용

POLITE_DELAY_SEC = 0.5

# ── 문서 식별 — 내용으로 판정한다 ────────────────────────────────────
RECON_TABLE_TITLE = re.compile(
    r"Selected\s+Product\s+and\s+Service\s+Revenue\s+Constant\s+Currency\s+Reconciliation",
    re.I)
AZURE_ROW = re.compile(r"^Azure\s+and\s+other\s+cloud\s+services$", re.I)

# ── 컬럼 결합 — 이름이 비슷한 컬럼이 여럿이므로 의미로 가른다 ──────────
#   실측 헤더(FY2025 Q4): Percentage Change Y/Y (GAAP) · Constant Currency Impact ·
#                        Percentage Change Y/Y Constant Currency
#   ⛔ 'constant currency' 라는 단어만 보고 고르면 Impact 컬럼과 섞인다.
RE_PCT_CHANGE = re.compile(r"percentage\s+change", re.I)
RE_CC = re.compile(r"constant\s+currency", re.I)
RE_IMPACT = re.compile(r"impact", re.I)
RE_PCT_VALUE = re.compile(r"^\(?-?\d+(?:\.\d+)?\)?\s*%?$")


def parse_document_blocks(sgml_text: str) -> list:
    """full submission `.txt` 의 `<DOCUMENT>` 블록에서 TYPE/SEQUENCE/FILENAME/DESCRIPTION.

    ★ **`<DOCUMENT>` 로 먼저 자르고, 각 조각에서 `<TEXT>` 앞의 SGML 헤더만** 본다.
      full `.txt` 는 각 document 의 **본문까지** 담는다. `<TYPE>` 을 문서 전체에서
      무작정 훑으면 본문(XBRL·HTML) 안의 `<TYPE>` 이 유령 후보가 되어 「정확히 1건」
      판정을 오염시킨다. 계약이 지목하는 것은 `<DOCUMENT>` 의 **SGML 헤더**다.
    ⛔ 본문에서 식별 근거를 찾지 않는다.
    """
    out = []
    for chunk in re.split(r"<DOCUMENT>", sgml_text, flags=re.I)[1:]:
        header = re.split(r"<TEXT>", chunk, maxsplit=1, flags=re.I)[0]
        m = RE_DOC_BLOCK.search(header)
        if not m:
            continue
        out.append({"type": (m.group("type") or "").strip(),
                    "sequence": (m.group("seq") or "").strip(),
                    "filename": (m.group("filename") or "").strip(),
                    "description": (m.group("desc") or "").strip()})
    return out


def index_html_types(html_text: str) -> dict:
    """secondary — `{accession}-index.html` 의 Type 컬럼을 filename → type 으로 돌려준다."""
    p = TableCollector()
    p.feed(html_text)
    out = {}
    for rows in p.tables:
        rows = drop_empty_columns(rows)
        if not rows:
            continue
        head = [c.strip().lower() for c in rows[0]]
        if "document" not in head or "type" not in head:
            continue
        di, ti = head.index("document"), head.index("type")
        for r in rows[1:]:
            if len(r) <= max(di, ti):
                continue
            name = re.sub(r"\s*iXBRL\s*$", "", r[di]).strip()
            if name:
                out[name] = r[ti].strip()
    return out


def log_candidates(docs, reason):
    """⛔ 공통 규칙 — 필터 결과만 남기지 않는다. **무엇을 필터링했는지** 함께 남긴다.

    (실패가 세 번 반복된 뒤 고정한 collector 공통 진단 규칙)
    """
    print(f"    ✗ {reason}")
    print(f"    후보 전체 {len(docs)}건 (상한 {CAND_LOG_LIMIT}):")
    for d in docs[:CAND_LOG_LIMIT]:
        print(f"      TYPE={d['type']!r} SEQ={d['sequence']!r} "
              f"FILE={d['filename']!r} DESC={d['description'][:40]!r}")
    if len(docs) > CAND_LOG_LIMIT:
        print(f"      … 나머지 {len(docs) - CAND_LOG_LIMIT}건 생략")


def select_exhibit(docs, sec_types=None):
    """`<DOCUMENT>` 블록 목록에서 EX-99.1 을 **하나** 식별한다.

    ★ 판정 근거는 `<TYPE>` **정확 일치**뿐이다.
      · 파일명이 `ex99_1` 형태가 아니어도 `<TYPE>EX-99.1` 이면 식별한다.
      · 파일명이 `ex99_1` 형태여도 `<TYPE>` 이 다르면 거부한다.
    ⛔ 0건 · 2건 이상은 모두 fail-closed — 「EX-99.1 이면 무조건 한 파일」로 가정하지 않는다.
    ⛔ `sec_types` (secondary `-index.html` Type 컬럼) 와 충돌하면 거부한다.
    ⛔ 이 함수는 네트워크를 쓰지 않는다 — 판정만 담아 회귀가 검증할 수 있게 분리했다.

    돌려주는 것: `(filename | None, problems: list, chosen | None)`
    """
    hits = [d for d in docs if d["type"].upper() == EXHIBIT_TYPE]
    if len(hits) != 1:
        return None, [f"<TYPE>{EXHIBIT_TYPE} 후보가 정확히 1건이 아니다 "
                      f"({len(hits)}건) — 복수여도 임의로 고르지 않는다"], None
    chosen = hits[0]
    target = chosen["filename"]
    if not target:
        return None, ["식별된 <DOCUMENT> 에 <FILENAME> 이 없다"], chosen
    if sec_types is None:                      # secondary 미조회 — primary 만 판정
        return target, [], chosen
    sec = sec_types.get(target)
    if sec is None:
        return None, [f"secondary 에서 {target} 의 Type 을 찾지 못했다 — 추정하지 않는다"], chosen
    if sec.upper() != EXHIBIT_TYPE:
        return None, [f"primary({EXHIBIT_TYPE}) 와 secondary({sec}) 가 충돌한다 "
                      f"— fail-closed"], chosen
    return target, [], chosen


def find_azure_table(tables):
    """`Azure and other cloud services` 행을 가진 표를 후보로 삼는다."""
    out = []
    for ti, rows in enumerate(tables):
        rows = drop_empty_columns(rows)
        for ri, r in enumerate(rows):
            if r and any(AZURE_ROW.match(c) for c in r):
                if ri > 0:
                    out.append((ti, rows, ri))
                break
    return out


def bind_columns(header, data):
    """세 컬럼을 **의미**로 가른다. 각각 정확히 1개가 아니면 결합하지 않는다."""
    idx_gaap, idx_impact, idx_cc = [], [], []
    for i, h in enumerate(header):
        cc, imp, pct = bool(RE_CC.search(h)), bool(RE_IMPACT.search(h)), bool(RE_PCT_CHANGE.search(h))
        if cc and imp:
            idx_impact.append(i)
        elif cc and pct:
            idx_cc.append(i)
        elif pct and not cc:
            idx_gaap.append(i)

    problems = []
    for label, hits in (("GAAP 성장률", idx_gaap), ("constant currency 영향", idx_impact),
                        ("constant currency 성장률", idx_cc)):
        if len(hits) != 1:
            problems.append(f"{label} 컬럼이 정확히 1개가 아니다 ({len(hits)}개)")
    if problems:
        return None, problems

    out = {"_header": header,
           "_column_index": {"gaap": idx_gaap[0], "cc_impact": idx_impact[0],
                             "cc": idx_cc[0]}}
    for k, i in out["_column_index"].items():
        if i >= len(data):
            return None, [f"{k} 컬럼({i})이 데이터 행 범위를 넘는다"]
        v = data[i]
        if not RE_PCT_VALUE.match(v):
            return None, [f"{k} 값이 퍼센트 형태가 아니다: {v!r}"]
        out[k] = v
    return out, []


def identify(text):
    """실적 발표문인지 **내용**으로 판정한다."""
    return [
        ("Microsoft 문서", bool(re.search(r"Microsoft", text, re.I)), r"Microsoft"),
        ("constant currency reconciliation 표 제목",
         bool(RECON_TABLE_TITLE.search(text)), r"Constant\s+Currency"),
        ("`Azure and other cloud services` 항목",
         bool(re.search(r"Azure and other cloud services", text, re.I)), r"Azure"),
    ]


def observe_one(c, errs):
    """한 filing 을 관측한다. (결과, ok) 를 돌려준다."""
    acc = c["accession"].replace("-", "")
    print(f"\n  ── {c['filing_date']} · {c['accession']} · items {c['items']!r}")

    # ① Exhibit identity — primary = **full submission `.txt`** 의 <DOCUMENT> 블록 <TYPE>
    #    ⛔ index.json 의 type 은 쓰지 않는다 (디렉터리 아이콘 값이다)
    #    ⛔ -index-headers.html 로 대체하지 않는다 (CIO 불승인 2026-08-16)
    time.sleep(POLITE_DELAY_SEC)
    try:
        _, raw = get(f"{ARCHIVE_BASE}/{acc}/{c['accession']}.txt")
    except Exception as e:                                       # noqa: BLE001
        print(f"    ✗ full submission .txt 조회 실패 — {type(e).__name__}: {e}")
        return None, False
    docs = parse_document_blocks(raw.decode("utf-8", errors="replace"))
    n_hit = sum(1 for d in docs if d["type"].upper() == EXHIBIT_TYPE)
    print(f"    <DOCUMENT> 블록 {len(docs)}건 · <TYPE>{EXHIBIT_TYPE} 정확 일치 {n_hit}건")

    # primary 단독 판정 먼저 — 여기서 걸리면 secondary 를 조회하지 않는다
    target, probs, chosen = select_exhibit(docs, sec_types=None)
    if target is None:
        log_candidates(docs, "; ".join(probs))
        return None, False
    print(f"    primary  TYPE={chosen['type']!r} SEQ={chosen['sequence']!r} "
          f"FILE={target!r} DESC={chosen['description'][:40]!r}")
    # 파일명 패턴은 **hint 로만** 기록한다 — 판정 근거가 아니다
    print(f"    (hint) 파일명이 ex99_1 형태인가 {bool(FILENAME_HINT.search(target))} "
          f"— ⛔ identity 근거로 쓰지 않는다")

    # secondary cross-check — {accession}-index.html 의 Type 컬럼
    time.sleep(POLITE_DELAY_SEC)
    try:
        _, ihtml = get(f"{ARCHIVE_BASE}/{acc}/{c['accession']}-index.html")
        sec_types = index_html_types(ihtml.decode("utf-8", errors="replace"))
    except Exception as e:                                       # noqa: BLE001
        print(f"    ✗ secondary index 조회 실패 — {type(e).__name__}: {e}")
        return None, False
    print(f"    secondary index.html Type[{target}] = {sec_types.get(target)!r}")
    target2, probs2, _ = select_exhibit(docs, sec_types=sec_types)
    if target2 is None:
        print(f"    ✗ {'; '.join(probs2)}")
        print(f"      secondary 목록 {list(sec_types.items())[:CAND_LOG_LIMIT]}")
        log_candidates(docs, "primary/secondary 교차확인 실패 — 후보 집합을 함께 남긴다")
        return None, False
    print("    ✓ primary · secondary 일치")
    ex = [{"name": target2}]

    # ② 문서 취득 + 내용 식별
    time.sleep(POLITE_DELAY_SEC)
    rec, body = get(f"{ARCHIVE_BASE}/{acc}/{ex[0]['name']}")
    html_text = body.decode("utf-8", errors="replace")
    text = strip_html(html_text)
    checks = identify(text)
    for label, v, probe in checks:
        print(f"    {'✓' if v else '✗'} {label}")
        if not v:
            for ln in evidence(text, probe):
                print(f"        근거후보 {ln}")
    if not all(v for _, v, _ in checks):
        print("    → 실적 발표문으로 확정하지 못했다. 값을 읽지 않는다")
        return None, False

    # ③ Azure 행 → 세 컬럼 의미 결합
    p = TableCollector()
    p.feed(html_text)
    cands = find_azure_table(p.tables)
    print(f"    Azure 행을 가진 표 {len(cands)}건")
    bound, why = None, []
    for ti, rows, ri in cands:
        header = build_header(rows, ri)
        b, probs = bind_columns(header, rows[ri])
        if b:
            bound = b
            print(f"    → table[{ti}] 에서 결합 성공")
            break
        why.append((ti, probs, header, rows[ri]))
    if bound is None:
        print("    ✗ 컬럼을 의미로 결합하지 못했다 — 값을 읽지 않는다")
        for ti, probs, header, drow in why[:2]:
            print(f"      table[{ti}] 사유 {probs}")
            print(f"        header {header}")
            print(f"        data   {drow}")
        print("      ★ 위 행렬이 다음 수정의 근거다 — 마크업을 추측하지 않는다.")
        return None, False

    print(f"    header {bound['_header']}")
    print(f"    결합   {bound['_column_index']}")
    print("    ⛔ 원문 그대로:")
    print(f"      GAAP 성장률                 {bound['gaap']}")
    print(f"      constant currency 영향       {bound['cc_impact']}")
    print(f"      ★ constant currency 성장률   {bound['cc']}   ← Decision 관측값")

    # ④ 참고 정합성 — 기록만. ⛔ 이 값으로 관측값을 보정하지 않는다.
    def _n(x):
        return float(x.replace("%", "").replace("(", "-").replace(")", ""))
    try:
        note = ("일치" if abs(_n(bound["gaap"]) + _n(bound["cc_impact"])
                              - _n(bound["cc"])) < 1e-9 else "불일치")
    except ValueError:
        note = "판정 불가"
    print(f"    참고: GAAP + 영향 = cc 인가 → {note} (기록만 · 보정하지 않는다)")

    return {"accession": c["accession"], "filing_date": c["filing_date"],
            "report_date": c["report_date"], "acceptance": c["acceptance"],
            "items": c["items"], "exhibit": ex[0]["name"],
            "final_url": rec["final_url"],
            "gaap_growth_pct": bound["gaap"],
            "cc_impact_pct": bound["cc_impact"],
            "azure_cc_growth_pct": bound["cc"],
            "gaap_plus_impact_equals_cc": note}, True


def main() -> int:
    print("=" * 74)
    print("RULE-0021 — MSFT Azure constant-currency 성장률 관측")
    print(f"  cik {CIK} · 대상 = 'Azure and other cloud services' 의 constant-currency growth")
    print("  ⛔ 45% 기준선 · 3%p 하회 판정은 evaluator 층이다 — 여기서 계산하지 않는다")
    print("  ⛔ GAAP 과 cc 를 혼용하지 않는다 · 명명된 Azure 항목 밖으로 fallback 하지 않는다")
    print("  ⛔ 저장소를 쓰지 않는다")
    print("=" * 74)

    # ── ① Discovery — items 2.02 로 결정론적으로 좁힌다 ──────────────
    print("\n① Discovery — submissions · form=8-K AND items 에 2.02")
    _, raw = get(SUBMISSIONS_URL)
    rec = json.loads(raw.decode("utf-8"))["filings"]["recent"]
    n = len(rec["form"])
    cands = []
    for i in range(n):
        if rec["form"][i] != "8-K":
            continue
        items = rec.get("items", [""] * n)[i] or ""
        if EARNINGS_ITEM not in items:
            continue
        cands.append({"accession": rec["accessionNumber"][i],
                      "filing_date": rec["filingDate"][i],
                      "report_date": rec.get("reportDate", [""] * n)[i],
                      "acceptance": rec.get("acceptanceDateTime", [""] * n)[i],
                      "items": items})
    cands.sort(key=lambda c: c["filing_date"], reverse=True)
    print(f"  item {EARNINGS_ITEM} 을 가진 8-K {len(cands)}건")
    dropped = cands[MAX_FILINGS:]
    cands = cands[:MAX_FILINGS]
    for c in cands:
        print(f"    {c['filing_date']} · {c['accession']} · items {c['items']!r}")
    if dropped:
        print(f"  ⚠️ 상한({MAX_FILINGS})으로 조회하지 않은 것 {len(dropped)}건: "
              f"{[d['accession'] for d in dropped[:6]]}{' …' if len(dropped) > 6 else ''}")
    if not cands:
        print("\n⛔ 후보 0건 — 중단")
        return 1

    # ── ② 관측 ──────────────────────────────────────────────────────
    print("\n② 관측 — filing index 에서 EX-99.1 을 type 으로 지목하고 내용으로 확정한다")
    errs, results = [], []
    for c in cands:
        r, ok = observe_one(c, errs)
        if ok:
            results.append(r)

    # ── ③ 재현성 ────────────────────────────────────────────────────
    print("\n" + "=" * 74)
    print("③ 복수 분기 재현")
    print(f"  시도 {len(cands)}건 · 관측 성공 {len(results)}건")
    for r in results:
        print(f"  {r['filing_date']}  cc {r['azure_cc_growth_pct']:>6}  "
              f"(GAAP {r['gaap_growth_pct']} · 영향 {r['cc_impact_pct']})  "
              f"acc {r['accession']}  ex {r['exhibit']}")
    if len(results) != len(cands):
        print("\n⛔ 일부 분기에서 동일 계약이 재현되지 않았다 — 추정으로 채우지 않는다")
        return 1
    if len(results) < 2:
        print("\n⛔ 복수 분기 재현을 확인하지 못했다")
        return 1
    if len({r["accession"] for r in results}) != len(results):
        print("\n⛔ 같은 filing 이 중복 계산됐다")
        return 1

    print("\n  ★ 관측값은 raw 그대로다. 45% 기준선·3%p 하회 판정은 여기서 하지 않는다.")
    print("  ⛔ RULE-0021 의 DATA_MISSING · SOURCE_UNRESOLVED 를 이 실행이 해제하지 않는다.")
    print("  ⛔ Production HOLD · consumable_by_evaluator=false · evaluator 미연결 유지.")
    print("\n✅ document-semantic extraction 계약이 복수 분기에서 재현됐다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
