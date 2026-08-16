#!/usr/bin/env python3
"""RULE-0022 — `Commercial remaining performance obligation` GAAP YoY 관측 (층 ②).

★ 이 파일의 책임 (Observation Layer 계약 §1 · 층 ②)
    문서 식별 조건 · row predicate · Decision column 지목 · observation naming
    period → table → row → column 4단 좁히기, 각 단계 **정확히 1건** 아니면 FAIL-CLOSED

⛔ 이 파일이 하지 않는 것 — 다른 층의 책임이다
    ⛔ 숫자 normalization  `(1)%` → -1 은 층 ③ Normalization 이 소유한다.
                          여기서는 **raw 문면 그대로** 넘긴다.
    ⛔ persistence         층 ④ Observation Store 가 소유한다. 저장소에 쓰지 않는다.
    ⛔ 비교 · pair 구성    층 ⑤ Pair Validation 이 소유한다.
    ⛔ 10%p · 2회 연속     층 ⑥ Evaluator 가 소유한다.

★ RULE-0021 과의 격리 (CIO 판정 §9-A · §9-C · S1 목표)
    ⛔ `msft_azure_cc.py` 를 import 하지 않는다. 한 심볼도 가져오지 않는다.
    ⛔ `msft_azure_cc.RECON_TABLE_TITLE` import 금지 — 아래에 **독립 상수로 복제**한다.
    ★ 두 Rule 이 현재 같은 표를 쓴다는 사실과, 동일 table identity contract 를 영구
      공유해야 한다는 것은 **다른 명제**다. 한쪽 Rule 때문에 제목 허용범위를 넓혔는데
      다른 Rule 까지 조용히 넓어지는 coupling 을 허용하지 않는다.
    ★ 장기간 동일하다는 증거가 생기면 별도 deduplication Gate 에서 공통화할 수 있다.

★ measurement 계약 (CIO 판정 D-1 · D-2 · D-3 · D-6)
    D-1 계열   `Commercial remaining performance obligation` — total RPO 아님
    D-2 형태   회사가 **직접 공표한** YoY 성장률 — 잔액에서 파생하지 않는다
    D-3 열     `Percentage Change Y/Y (GAAP)` 가 Decision 열
               CC · impact 는 Decision 값이 아니지만 **evidence 로 반드시 함께 보존**
    D-6 경계   FY26 Q1 이전에는 이 행이 존재하지 않는다 — 그 사실을 그대로 관측한다.
               ⛔ backfill · 재구성 · 다른 source 보충 금지.

⛔ 네트워크를 여기서 직접 열지 않는다 — 취득은 층 ① acquisition primitive 의 책임이다.
⛔ 저장소에 쓰지 않는다.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))

# Rule 의미가 없는 HTML primitive 만 가져온다 (P3 에서 닫힌 계약).
# ⛔ `msft_azure_cc` 에서는 아무것도 가져오지 않는다.
from c4_sec_edgar_check import (TableCollector, strip_html,        # noqa: E402
                                drop_empty_columns, evidence)

# ══════════════════════════════════════════════════════════════════════
# 표 제목 identity — ⛔ 복제본이다. `msft_azure_cc` 것을 import 하지 않는다.
#   현재 승인된 두 문면만 받는다. 미지의 세 번째 문면은 fail-closed 로 막는다.
#   ⛔ 앵커를 풀지 않는다 · ⛔ RULE-0021 때문에 이 상수를 넓히지 않는다.
# ══════════════════════════════════════════════════════════════════════
RECON_TABLE_TITLE = re.compile(
    r"Selected\s+Product\s+and\s+Service\s+(?:Revenue|Information)\s+"
    r"Constant\s+Currency\s+Reconciliation", re.I)

# ══════════════════════════════════════════════════════════════════════
# row identity — RULE-0022 measurement identity 그 자체 (CIO 판정 D-1)
#   ★ 관측된 FY26 Q1~Q4 4건에서 문면이 **완전히 동일**하다 (Azure 행과 달리 drift 없음).
#     그래서 접미사 변형을 미리 열어두지 않는다 — 실제로 관측되면 그때 CIO 판정으로 연다.
#   ⛔ 앵커(`^…$`)를 풀지 않는다 · ⛔ `total` · `remaining performance obligation` 단독으로
#      넓히지 않는다 (08-16 prohibited: 「total RPO 로 계열 확대」).
# ══════════════════════════════════════════════════════════════════════
COMMERCIAL_RPO_ROW = re.compile(
    r"^Commercial\s+remaining\s+performance\s+obligation$", re.I)

# ══════════════════════════════════════════════════════════════════════
# Decision column — CIO 판정 D-3
#   ★ 이 상수가 RULE-0021 과 **반대 방향**이다 (RULE-0021 = "cc").
#     그래서 컬럼 지목은 절대 공유하지 않는다.
# ══════════════════════════════════════════════════════════════════════
DECISION_COLUMN = "gaap"
DECISION_COLUMN_IDENTITY = "Percentage Change Y/Y (GAAP)"
EVIDENCE_COLUMNS = ("cc_impact", "cc")

MEASUREMENT_IDENTITY = "Commercial remaining performance obligation"
SUBJECT = "MSFT"
RULE_SCOPE = ("RULE-0022",)

# ── 컬럼 결합 — 이름이 비슷한 컬럼이 여럿이므로 **의미**로 가른다 ─────
RE_PCT_CHANGE = re.compile(r"percentage\s+change", re.I)
RE_CC = re.compile(r"constant\s+currency", re.I)
RE_IMPACT = re.compile(r"impact", re.I)
RE_PCT_VALUE = re.compile(r"^\(?-?\d+(?:\.\d+)?\)?\s*%?$")

# ── period identity ──────────────────────────────────────────────────
QUARTER_PERIOD = re.compile(
    r"Three\s+Months\s+Ended\s+([A-Z][a-z]+\s+\d{1,2},\s*\d{4})", re.I)
NON_QUARTER_PERIOD = re.compile(
    r"(?:Year|Six\s+Months|Nine\s+Months|Twelve\s+Months)\s+Ended", re.I)

# 행 부재는 결함이 아니라 **관측 결과**다 (CIO 판정 D-6).
ROW_ABSENT = "ROW_ABSENT"


# ══════════════════════════════════════════════════════════════════════
# 표 층 helper — ⛔ 복제본이다 (copy → neutralize → prove).
#   `msft_azure_cc` 의 동명 함수를 import 하지 않는다. 그 파일은 동결 대상이고,
#   공유하면 RULE-0021 의 변경이 RULE-0022 의 의미를 조용히 바꾼다.
#   ★ 중복 제거는 양쪽 계약이 장기간 동일하다는 증거가 생긴 뒤 별도 Gate 에서 한다.
# ══════════════════════════════════════════════════════════════════════
def is_data_row(row) -> bool:
    """값 행인가 — 퍼센트 값 셀을 하나라도 가지면 값 행이다."""
    return any(RE_PCT_VALUE.match(c.strip()) for c in row if c.strip())


def build_header(rows, data_i):
    """대상 행 **위의 헤더 행만** 열 단위로 이어 붙인다.

    ⛔ 다른 data-row 의 라벨·값을 헤더 identity 에 넣지 않는다. 신형 문면부터 행 라벨에
       지표명이 들어가기 시작해, 섞으면 컬럼 정체성이 오염된다.
    ⛔ 헤더 행을 하나도 못 찾으면 만들어내지 않는다 — 빈 헤더는 `bind_columns` 에서
       「정확히 1개가 아니다」로 fail-closed 된다.
    """
    width = len(rows[data_i])
    cols = []
    for i in range(width):
        parts = []
        for r in rows[:data_i]:
            if is_data_row(r):
                continue
            if i < len(r) and r[i]:
                parts.append(r[i])
        cols.append(re.sub(r"\s+", " ", " ".join(parts)).strip())
    return cols


def bind_columns(header, data):
    """세 컬럼을 **의미**로 가른다. 각각 정확히 1개가 아니면 결합하지 않는다.

    ⛔ 열 **위치**로 찾지 않는다 — 실측에서 위치가 문서마다 다르다.
    ⛔ 'constant currency' 라는 단어만 보고 고르면 Impact 컬럼과 섞인다.
    """
    idx_gaap, idx_impact, idx_cc = [], [], []
    for i, h in enumerate(header):
        cc, imp, pct = bool(RE_CC.search(h)), bool(RE_IMPACT.search(h)), bool(RE_PCT_CHANGE.search(h))
        if cc and imp:
            idx_impact.append(i)
        elif cc and pct:
            idx_cc.append(i)
        elif pct and not cc:
            idx_gaap.append(i)

    counts = {"gaap": len(idx_gaap), "cc_impact": len(idx_impact), "cc": len(idx_cc)}
    problems = []
    for key, label, hits in (("gaap", "GAAP 성장률", idx_gaap),
                             ("cc_impact", "constant currency 영향", idx_impact),
                             ("cc", "constant currency 성장률", idx_cc)):
        if len(hits) != 1:
            problems.append(f"{label} 컬럼이 정확히 1개가 아니다 ({len(hits)}개)")
    if problems:
        return None, problems, counts

    out = {"_header": header,
           "_column_index": {"gaap": idx_gaap[0], "cc_impact": idx_impact[0], "cc": idx_cc[0]}}
    for k, i in out["_column_index"].items():
        if i >= len(data):
            return None, [f"{k} 컬럼({i})이 데이터 행 범위를 넘는다"], counts
        v = data[i]
        if not RE_PCT_VALUE.match(v):
            return None, [f"{k} 값이 퍼센트 형태가 아니다: {v!r}"], counts
        out[k] = v
    return out, [], counts


def table_period(rows, ri):
    """표의 기간 문면을 **의미로** 찾는다. 위치(열 번호)로 찾지 않는다."""
    for r in rows[:ri]:
        for c in r:
            m = QUARTER_PERIOD.search(c)
            if m:
                return ("QUARTER", m.group(1).strip())
            if NON_QUARTER_PERIOD.search(c):
                return ("NON_QUARTER", c.strip())
    return None


# ══════════════════════════════════════════════════════════════════════
# Rule-specific 좁히기 — ⛔ RULE-0021 과 공유하지 않는다
# ══════════════════════════════════════════════════════════════════════
def _identity_contains(header_text: str, expected: str) -> bool:
    """결합된 헤더 문면이 기대 column identity 를 담고 있는가.

    ★ 공백만 정규화한다. ⛔ 대소문자·구두점·괄호를 무르게 하지 않는다 —
      `(GAAP)` 의 괄호가 사라지면 다른 열과 구별되지 않는다.
    """
    norm = lambda s: re.sub(r"\s+", " ", s).strip()
    return norm(expected) in norm(header_text)


def find_target_rows(tables):
    """`COMMERCIAL_RPO_ROW` 와 일치하는 행을 **전부** 낸다. (ti, rows, ri) 목록.

    ⛔ 여기서 고르지 않는다. 고르는 판단은 `select_observation` 이 하고, 모호하면 멈춘다.
    """
    out = []
    for ti, rows in enumerate(tables):
        rows = drop_empty_columns(rows)
        for ri, r in enumerate(rows):
            if r and any(COMMERCIAL_RPO_ROW.match(c.strip()) for c in r) and ri > 0:
                out.append((ti, rows, ri))
    return out


def identify(text, tables):
    """**문서**가 대상 실적 발표문인지 내용으로 판정한다.

    ★ 여기에 「대상 행이 있는가」를 넣지 않는다 — 그것이 이 층의 핵심 설계 결정이다.
      RULE-0021 의 `identify()` 는 3번째 체크가 Azure 행 존재인데, 같은 형태를 쓰면
      **행 부재(D-6 이 관측 결과로 규정한 상태)가 「문서 식별 실패」로 둔갑**한다.
      D-6 은 FY26 Q1 이전에 이 행이 없다는 사실을 그대로 관측하라고 요구한다.
      ⇒ 문서 identity 와 row identity 를 분리한다.
    ⛔ RULE-0021 의 `identify()` 를 import 하지 않는다.
    """
    return [
        ("Microsoft 문서", bool(re.search(r"Microsoft", text, re.I)), r"Microsoft"),
        ("constant currency reconciliation 표 제목",
         bool(RECON_TABLE_TITLE.search(text)), r"Constant\s+Currency"),
    ]


def row_present(tables) -> bool:
    """대상 행이 문서 어딘가에 있는가.

    ★ `find_target_rows` 와 **동일한 predicate** 를 쓴다 — 진단에서는 통과하고 결합에서
      실패하는 어긋남을 구조로 막는다 (RULE-0021 에서 실제로 발생했던 결함 유형).
    """
    return any(COMMERCIAL_RPO_ROW.match(c.strip())
               for rows in tables for r in rows for c in r)


def select_observation(tables):
    """period → table → row 로 좁힌다. (rows, ri, period_end, problems, narrowing).

    ⛔ 어느 단계든 후보가 정확히 1건이 아니면 값을 만들지 않는다.
    ★ 행이 0건인 것은 **결함이 아니라 관측 결과**다 (D-6). 그래서 사유를 구분해 낸다.
    """
    narrowing = {"period_candidates": 0, "table_candidates": 0, "row_candidates": 0}
    cands = find_target_rows(tables)
    narrowing["row_candidates_before_period"] = len(cands)
    if not cands:
        return None, None, None, [f"{ROW_ABSENT}: `{MEASUREMENT_IDENTITY}` 행이 0건이다"], narrowing

    kept, dropped = [], []
    for ti, rows, ri in cands:
        per = table_period(rows, ri)
        if per and per[0] == "QUARTER":
            kept.append((ti, rows, ri, per[1]))
        else:
            dropped.append((ti, ri, per))
    narrowing["period_candidates"] = len(kept)
    if not kept:
        return None, None, None, [
            f"분기(Three Months Ended) 표가 0건이다 — 연간·YTD 로 대체하지 않는다. "
            f"후보 {[(ti, ri, p) for ti, ri, p in dropped]}"], narrowing

    tis = sorted({ti for ti, _, _, _ in kept})
    narrowing["table_candidates"] = len(tis)
    if len(tis) != 1:
        return None, None, None, [
            f"분기 조건을 만족하는 표가 정확히 1건이 아니다 ({len(tis)}건: table {tis}) "
            f"— 문서 순서로 고르지 않는다"], narrowing

    same = [k for k in kept if k[0] == tis[0]]
    narrowing["row_candidates"] = len(same)
    if len(same) != 1:
        return None, None, None, [
            f"표[{tis[0]}] 안 `{MEASUREMENT_IDENTITY}` 행이 정확히 1건이 아니다 "
            f"({len(same)}건: row {[k[2] for k in same]}) — 위쪽 행을 고르지 않는다"], narrowing

    ti, rows, ri, period_end = same[0]
    return rows, ri, period_end, [], narrowing


# ══════════════════════════════════════════════════════════════════════
# ObservationDraft — 층 ③ Normalization 에 넘길 **raw 전용** 레코드
#   ⛔ numeric_value 를 여기서 만들지 않는다 · ⛔ 부호를 해석하지 않는다.
# ══════════════════════════════════════════════════════════════════════
def observe_html(html_text: str, provenance: dict | None = None,
                 verbose: bool = False) -> tuple:
    """EX-99.1 원문 → ObservationDraft. (draft | None, problems, narrowing).

    ★ 이 함수는 **문자열만** 받는다 — 네트워크를 모른다. 그래서 fixture 로 검증된다.
    """
    p = TableCollector()
    p.feed(html_text)
    text = strip_html(html_text)

    checks = identify(text, p.tables)
    if verbose:
        for label, v, probe in checks:
            print(f"    {'✓' if v else '✗'} {label}")
            if not v:
                for ln in evidence(text, probe):
                    print(f"        근거후보 {ln}")
    if not all(v for _, v, _ in checks):
        failed = [label for label, v, _ in checks if not v]
        return None, [f"문서 식별 실패: {failed}"], {}

    rows, ri, period_end, problems, narrowing = select_observation(p.tables)
    if rows is None:
        return None, problems, narrowing

    header = build_header(rows, ri)
    bound, colproblems, counts = bind_columns(header, rows[ri])
    narrowing["column_candidates"] = counts
    if bound is None:
        return None, colproblems, narrowing

    # ── column identity 검증 (CIO 판정 D-3) ────────────────────────────
    #   ★ 기대 문면이 결합된 헤더 안에 **문면 그대로** 있어야 한다.
    #     구형 표는 헤더 행이 여럿이라 결합 결과가
    #     `Three Months Ended … Percentage Change Y/Y (GAAP)` 처럼 접두를 갖는다.
    #     그래서 **완전 일치가 아니라 포함**을 계약으로 삼되, 그 규칙을 명시 선언한다.
    #   ⛔ 기대한 column identity 가 없으면 값을 만들지 않는다 (fail-closed).
    #   ⛔ 열 위치로 대체하지 않는다.
    decision_header = header[bound["_column_index"][DECISION_COLUMN]]
    if not _identity_contains(decision_header, DECISION_COLUMN_IDENTITY):
        return None, [f"Decision 열의 identity 가 기대 문면을 담고 있지 않다: "
                      f"{decision_header!r} ⊉ {DECISION_COLUMN_IDENTITY!r}"], narrowing

    row_label = next(c.strip() for c in rows[ri] if COMMERCIAL_RPO_ROW.match(c.strip()))

    draft = {
        "schema_version": "observation_draft/1",
        "subject": SUBJECT,
        "measurement_identity": MEASUREMENT_IDENTITY,
        "economic_period_kind": "quarter",
        "period_text_raw": f"Three Months Ended {period_end}",
        "period_end_text": period_end,
        # ── Decision — raw 만. ⛔ normalization 은 층 ③ ──────────────
        "decision": {
            "column_key": DECISION_COLUMN,
            "column_identity": header[bound["_column_index"][DECISION_COLUMN]],
            "column_identity_expected": DECISION_COLUMN_IDENTITY,
            "raw_value": bound[DECISION_COLUMN],
        },
        # ── evidence — Decision 값은 아니지만 버리지 않는다 (D-3) ────
        "evidence_columns": [
            {"column_key": k,
             "column_identity": header[bound["_column_index"][k]],
             "raw_value": bound[k]}
            for k in EVIDENCE_COLUMNS
        ],
        "row_label_raw": row_label,
        "table_header_raw": header,
        "narrowing": narrowing,
        "observed_by": "rule0022_commercial_rpo",
        "rule_scope": list(RULE_SCOPE),
        "normalized": False,          # ★ 층 ③ 이 아직 처리하지 않았다는 표식
        "persisted": False,           # ★ 층 ④ 가 아직 처리하지 않았다는 표식
    }
    if provenance:
        draft["provenance"] = provenance
    return draft, [], narrowing


def observation_absent(problems) -> bool:
    """실패 사유가 `ROW_ABSENT` 인가 — 결함이 아니라 관측 결과다 (D-6)."""
    return any(str(p).startswith(ROW_ABSENT) for p in problems)


if __name__ == "__main__":                                          # pragma: no cover
    print("RULE-0022 Commercial RPO observation entrypoint (층 ②)")
    print(f"  measurement : {MEASUREMENT_IDENTITY}")
    print(f"  decision col: {DECISION_COLUMN_IDENTITY}")
    print("  ⛔ 이 진입점은 S1 범위에서 네트워크를 열지 않는다 — fixture 전용이다.")
    print("  ⛔ normalization · persistence · pair · evaluator 는 다른 층의 책임이다.")
    sys.exit(0)
