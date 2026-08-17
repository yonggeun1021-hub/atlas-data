#!/usr/bin/env python3
"""Acquisition primitive — Microsoft 실적 8-K 취득 층 (Rule 의미 없음).

★ 이 파일이 아는 것 (전부)
    ① submissions 에서 form=8-K AND items 에 2.02 인 filing 후보를 고른다
    ② full submission `.txt` 의 <DOCUMENT> SGML 헤더에서 <TYPE>EX-99.1 을 지목한다
    ③ `{accession}-index.html` 의 Type 컬럼으로 교차확인한다
    ④ accession · filing_date · exhibit identity provenance 를 만든다

⛔ 이 파일이 알아서는 안 되는 것 — 위반하면 층이 무너진다
    ⛔ Azure                       ⛔ Commercial RPO
    ⛔ RULE-0021 · RULE-0022       ⛔ Decision column
    ⛔ 표 제목 · 행 · 열 · 관측값 · 임계값 · 저장

★ 왜 기존 `msft_azure_cc.py` 에서 **추출하지 않고 복제**했는가 (CIO 판정 §9-C)
    추출은 `msft_azure_cc.py` 를 수정하는 일이고, 그 파일은 RULE-0021 회귀 309 checks
    와 mutation 34건이 걸린 **동결 대상**이다. 추출이 무증상임을 증명하는 비용이
    복제 비용보다 크다고 판정됐다. 그래서 `copy → neutralize → prove` 를 택한다.
    ⛔ `msft_azure_cc.py` 를 import 하지 않는다 · 수정하지 않는다.
    ★ RULE-0021 은 이번 단계에서 **기존 코드 그대로** 둔다. migration 은 별도 Gate.

⛔ 저장소에 쓰지 않는다.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))

# HTML/HTTP primitive 는 Rule 의미가 없고 P3(C4)에서 이미 닫힌 계약이다.
# ⛔ 여기서 재정의하지 않는다 — 같은 파서를 두 벌 두면 그 자체가 결함 표면이다.
from c4_sec_edgar_check import get                                  # noqa: E402,F401

CIK = "0000789019"
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/789019"

EARNINGS_ITEM = "2.02"          # Results of Operations and Financial Condition
EXHIBIT_TYPE = "EX-99.1"

# `<DOCUMENT>` 로 먼저 자르고 각 조각의 `<TEXT>` 앞 SGML 헤더만 본다.
# ⛔ 문서 전체에서 <TYPE> 을 훑으면 본문(XBRL·HTML) 안의 <TYPE> 이 유령 후보가 되어
#    「정확히 1건」 판정을 오염시킨다.
RE_DOC_BLOCK = re.compile(r"<DOCUMENT>(.*?)(?:</DOCUMENT>|\Z)", re.S | re.I)
RE_SGML_FIELD = re.compile(r"^<(TYPE|SEQUENCE|FILENAME|DESCRIPTION)>(.*)$", re.M | re.I)

CAND_LOG_LIMIT = 20
POLITE_DELAY_SEC = 0.5


# ── ① discovery — 순수 함수. 네트워크와 분리한다 ──────────────────────
def filter_earnings_candidates(recent: dict, limit: int | None = None) -> tuple:
    """submissions 의 `filings.recent` 에서 실적 8-K 후보를 고른다.

    돌려주는 것: (선정 목록, 상한으로 제외된 목록)
    ⛔ 상한으로 잘라낸 것을 조용히 버리지 않는다 — 호출자가 기록할 수 있게 함께 낸다.
    ★ 순수 함수다. 네트워크를 모른다 — 그래서 fixture 로 검증할 수 있다.
    """
    n = len(recent.get("form", []))
    out = []
    for i in range(n):
        if recent["form"][i] != "8-K":
            continue
        items = (recent.get("items", [""] * n)[i] or "")
        if EARNINGS_ITEM not in items:
            continue
        out.append({
            "accession": recent["accessionNumber"][i],
            "filing_date": recent["filingDate"][i],
            "report_date": recent.get("reportDate", [""] * n)[i],
            "acceptance": recent.get("acceptanceDateTime", [""] * n)[i],
            "items": items,
        })
    out.sort(key=lambda c: c["filing_date"], reverse=True)
    if limit is None:
        return out, []
    return out[:limit], out[limit:]


# ── ② exhibit identity — 순수 함수 ────────────────────────────────────
def parse_document_blocks(sgml_text: str) -> list:
    """full submission `.txt` 의 `<DOCUMENT>` 블록에서 TYPE/SEQUENCE/FILENAME/DESCRIPTION."""
    docs = []
    for block in RE_DOC_BLOCK.findall(sgml_text):
        head = block.split("<TEXT>", 1)[0]
        rec = {"type": "", "sequence": "", "filename": "", "description": ""}
        for k, v in RE_SGML_FIELD.findall(head):
            rec[k.lower()] = v.strip()
        if rec["type"] or rec["filename"]:
            docs.append(rec)
    return docs


def index_html_types(html_text: str) -> dict:
    """`{accession}-index.html` 의 Type 컬럼을 파일명 → type 으로 읽는다."""
    out = {}
    for row in re.findall(r"<tr\b.*?</tr>", html_text, re.S | re.I):
        cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.S | re.I)
        if len(cells) < 4:
            continue
        plain = [re.sub(r"<[^>]+>", " ", c) for c in cells]
        plain = [re.sub(r"\s+", " ", c).strip() for c in plain]
        href = re.search(r'href="([^"]+)"', cells[2] if len(cells) > 2 else "", re.I)
        name = os.path.basename(href.group(1)) if href else ""
        if name:
            out[name] = plain[3]
    return out


def select_exhibit(docs: list, sec_types: dict | None = None) -> tuple:
    """`<TYPE>EX-99.1` 정확 일치가 **정확히 1건**일 때만 그 파일명을 낸다.

    돌려주는 것: (filename | None, problems, chosen_doc | None)
    ⛔ 파일명 패턴으로 고르지 않는다 · ⛔ 후보가 여럿일 때 첫 번째를 고르지 않는다.
    """
    hits = [d for d in docs if d["type"].upper() == EXHIBIT_TYPE]
    if len(hits) != 1:
        return None, [f"<TYPE>{EXHIBIT_TYPE} 정확 일치가 1건이 아니다 ({len(hits)}건)"], None
    chosen = hits[0]
    name = chosen["filename"].strip()
    if not name:
        return None, [f"<TYPE>{EXHIBIT_TYPE} 문서에 FILENAME 이 없다"], None
    if sec_types is not None:
        got = sec_types.get(name)
        if got is None:
            return None, [f"secondary index 에 {name} 이 없다"], None
        if got.upper().replace(" ", "") != EXHIBIT_TYPE.replace(" ", ""):
            return None, [f"secondary index 의 Type 이 다르다: {got!r}"], None
    return name, [], chosen


def exhibit_provenance(cand: dict, exhibit_name: str, source_sha256: str) -> dict:
    """관측 층이 그대로 실어 나를 provenance 조각. ⛔ 여기서 값을 만들지 않는다."""
    return {
        "source_kind": "sec_edgar_8k_ex99_1",
        "accession": cand["accession"],
        "filing_date": cand["filing_date"],
        "report_date": cand.get("report_date", ""),
        "items": cand.get("items", ""),
        "exhibit_identity": {
            "type": EXHIBIT_TYPE,
            "document": exhibit_name,
            "selection": "full_submission_sgml_type_exact_match",
            "secondary_cross_check": "index_html_type_column",
        },
        "source_sha256": source_sha256,
        "exhibit_url": f"{ARCHIVE_BASE}/{cand['accession'].replace('-', '')}/{exhibit_name}",
    }


def log_candidates(docs: list, reason: str) -> None:
    """실패 시 후보 집합과 판정 신호를 함께 남긴다 (collector 공통 규칙)."""
    print(f"    ✗ {reason}")
    print(f"    후보 전체 {len(docs)}건 (상한 {CAND_LOG_LIMIT}):")
    for d in docs[:CAND_LOG_LIMIT]:
        print(f"      TYPE={d['type']!r} SEQ={d['sequence']!r} "
              f"FILE={d['filename']!r} DESC={d['description'][:40]!r}")
    if len(docs) > CAND_LOG_LIMIT:
        print(f"      … 나머지 {len(docs) - CAND_LOG_LIMIT}건 생략")
