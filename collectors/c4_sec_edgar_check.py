#!/usr/bin/env python3
"""C4 SEC EDGAR — TSMC monthly revenue end-to-end 검증 (CIO 승인 2026-08-15).

C4 = TSMC 가 **직접 제출한** SEC 6-K 월매출 보고서 (CIK 0001046179).

★ 계층 계약 (CIO 확정 2026-08-15) — 이 순서를 섞지 않는다
   · Decision observation SSOT = `TSMC {Month} Revenue Report (Consolidated)` 표,
     단위 `NT$ million`. **오직 이 표만** Decision 값을 만든다.
   · 산문의 `NT$ … billion / … percent`      = 문서 식별 + cross-check 전용
   · 뒤쪽 `Revenue (in NT$ thousands)` 표      = 정밀도 cross-check 전용
   ⛔ 산문·천원표는 **fallback 이 아니다.** Decision 표를 못 읽었다고 해서 그쪽에서
      값을 가져오지 않는다. 층이 서로 어긋나면 자동 선택 없이 fail-closed.

★ YoY 는 공표값을 그대로 소비한다. 천원 raw 로 재계산해 소수점을 늘리지 않는다.
   계산은 cross-check 까지만 허용하며 Decision 값으로 승격하지 않는다.

★ 두 개의 `Y-o-Y Increase (Decrease) %` 컬럼을 **column index 로 구분하지 않는다.**
   대상월 헤더(`{Month} {Year-1}`)와 누계 헤더(`January to {Month} {Year-1}`)와의
   **의미 인접 관계**로 각각 monthly / cumulative 에 매핑한다.

★ 시각 계층 분리 (CIO 확정)
   · target_month   = Revenue Report 내용에서 획득
   · published_at   = Revenue Report 본문의 발표일
   · sec_acceptance = SEC acceptanceDateTime — **provenance/validation 전용**
   ⛔ SEC 접수시각을 TSMC 발표시각인 것처럼 쓰지 않는다. 둘이 모순되면 fail-closed.

⛔ 이 pilot 이 하지 않는 것
   · persistent incremental cursor 생성 (운영화 단계 별도 판정 — 여기서는 target month 를
     명시 입력으로 받는다)
   · C1(TSMC IR) · C2(FSC/TWSE) fallback · 실패 시 다른 source 자동 확장
   · UA 변경 · 브라우저 위장 · 차단 우회
   · 새 임계값 생성 — 아래 cross-check 는 **각 층이 스스로 공표한 정밀도**로만 비교한다
   · collector · Source Contract · Rule 상태 · `config/rules.json` 변경
   · 저장소 파일 생성 · 수정 · commit · push
   · RULE-0003/0007/0008 상태 자동 변경 — 성공해도 CIO 판정을 요청하고 멈춘다
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import sys
import time
from html.parser import HTMLParser

CIK = "0001046179"
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/1046179"

FETCH_USER_AGENT = "Atlas Research (yonggeun1021@gmail.com)"   # 신원 표시 — 위장 아님
FETCH_TIMEOUT_SEC = 30
POLITE_DELAY_SEC = 0.5

# ★ CIO 판정 — persistent cursor 를 만들지 않고 명시 target month 를 입력으로 받는다.
#   2026-06 · 2026-07 두 달을 관측해 **월 연속성 입력 구성**이 성립하는지까지 본다
#   (RULE-0003 은 "40% 미달 2개월 연속" 이라 단월 관측으로는 판정 capability 가 없다).
#   ⛔ 그래도 RULE-0003 을 평가하지 않는다 — 조건 발동 여부가 아니라 관측 capability 만 본다.
TARGET_MONTHS = ["2026-06", "2026-07"]

MONTHS = ("January February March April May June July August September "
          "October November December").split()

# 승인된 관측값 (CIO 확정 2026-08-15) — differential 표시용.
FIXTURE_EXPECTED = {
    "2026-07": {"monthly_revenue_ntd_mn": "467,580", "monthly_yoy_pct": "44.7",
                "cumulative_revenue_ntd_mn": "2,872,064", "cumulative_yoy_pct": "37.0"},
    "2026-06": {"monthly_revenue_ntd_mn": "442,680", "monthly_yoy_pct": "67.9",
                "cumulative_revenue_ntd_mn": "2,404,484", "cumulative_yoy_pct": "35.6"},
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUEST_LOG = []


# ══════════════════════════════════════════════════════════════════
# HTML → 표 행렬
# ══════════════════════════════════════════════════════════════════
class TableCollector(HTMLParser):
    """모든 <table> 을 행×셀 문자열 행렬로 모은다. 중첩 table 도 각각 담는다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._stack = []          # 열려 있는 table 들의 (rows, cur_row, cur_cell)

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append({"rows": [], "row": None, "cell": None})
        elif self._stack:
            t = self._stack[-1]
            if tag == "tr":
                t["row"] = []
            elif tag in ("td", "th"):
                if t["row"] is None:
                    t["row"] = []
                t["cell"] = []
            elif tag == "br" and t["cell"] is not None:
                t["cell"].append(" ")

    def handle_endtag(self, tag):
        if not self._stack:
            return
        t = self._stack[-1]
        if tag in ("td", "th"):
            if t["cell"] is not None:
                t["row"].append(norm(" ".join(t["cell"])))
                t["cell"] = None
        elif tag == "tr":
            if t["row"] is not None:
                t["rows"].append(t["row"])
                t["row"] = None
        elif tag == "table":
            done = self._stack.pop()
            if done["row"]:
                done["rows"].append(done["row"])
            self.tables.append(done["rows"])

    def handle_data(self, data):
        if self._stack and self._stack[-1]["cell"] is not None:
            self._stack[-1]["cell"].append(data)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def strip_html(html_text: str) -> str:
    """태그를 제거하고 **표준 HTML entity 만** 정규 디코딩한다.

    ⛔ CIO 판정 2026-08-15 — 경계를 지킨다:
       · 표준 entity(`&nbsp;` `&#160;` `&#x24;` …) → `html.unescape` 로 정상화 허용
       · 의미가 달라질 수 있는 변형(`$` 가 별도 태그로 분리 · 전각 `＄`)
         → **자동 보정 금지.** 통과시키려고 임의 정규화하지 않는다.
    """
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", html_text)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = _html.unescape(t)                     # ★ 수동 치환 목록을 대체한다
    return re.sub(r"\s+", " ", t.replace("\xa0", " ")).strip()


def evidence(text: str, probe: str, width: int = 160, limit: int = 3):
    """실패한 신호 주변의 제한된 normalized text 를 돌려준다 (CIO 판정 3).

    ⛔ 전체 문서를 뿌리지 않는다. probe 주변 일부만 남긴다.
    """
    out = []
    for m in re.finditer(probe, text, re.I):
        a, b = max(0, m.start() - width // 2), min(len(text), m.end() + width // 2)
        out.append(repr(text[a:b]))           # repr — 전각·제어문자를 눈으로 구분하려고
        if len(out) >= limit:
            break
    return out or ["(probe 자체가 문서에 없다)"]


def drop_empty_columns(rows):
    """모든 행에서 비어 있는 열을 제거한다 (EDGAR 의 spacer 셀 정리)."""
    if not rows:
        return rows
    width = max(len(r) for r in rows)
    padded = [r + [""] * (width - len(r)) for r in rows]
    keep = [i for i in range(width) if any(p[i] for p in padded)]
    return [[p[i] for i in keep] for p in padded]


# ══════════════════════════════════════════════════════════════════
# Decision 표 식별 + 의미 기반 컬럼 결합
# ══════════════════════════════════════════════════════════════════
RE_NET_REVENUE = re.compile(r"^net\s+revenue$", re.I)
RE_YOY = re.compile(r"y\s*-?\s*o\s*-?\s*y", re.I)
RE_MOM = re.compile(r"m\s*-?\s*o\s*-?\s*m", re.I)
RE_NUM = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")


def month_index(name: str):
    for i, m in enumerate(MONTHS, 1):
        if m.lower() == name.strip().lower():
            return i
    return None


def find_decision_table(tables, month_name: str, year: int, rejected=None):
    """`Net Revenue` 행과 두 개의 Y-o-Y 헤더를 가진 표만 후보로 삼는다.

    ★ row identity 유일성 (CIO 승인 2026-08-16)
      같은 표 안 `Net Revenue` identity 는 **정확히 1행**이어야 한다.
        0행   → 애초에 대상이 아니다 (기존과 동일)
        2행+  → **모호**하다. ⛔ 첫 행을 고르지 않고 후보에서 뺀다.
      예전에는 첫 행에서 `break` 하고 나머지를 조용히 버렸다. 실제 TSMC 6-K 의
      `Revenue (in NT$ thousands)` 표는 **월 행과 누계 행 두 개**를 갖는다
      (2026-05·06·07 원문에서 확인). 지금은 그 표가 `Y-o-Y` 조건에서 걸리지만,
      선택 층에 유일성 판정이 없다는 사실 자체는 시스템의 fail-closed 규율과 맞지
      않는다. 그래서 여기를 닫는다.

    ⛔ 이 변경의 범위는 **row-local 유일성 뿐**이다.
       table-level 2+ guard · 컬럼 identity · build_header 는 건드리지 않는다 (별건).
    ⛔ 정상 원문 3개월에서 결정표의 `Net Revenue` 는 1행이므로 관측값은 바뀌지 않는다.

    `rejected` 를 주면 걸러낸 표의 **후보 evidence** 를 담아 돌려준다
    (결과만 남기지 않고 무엇을 왜 걸렀는지 남긴다 — collector 공통 규칙).
    """
    cands = []
    for ti, rows in enumerate(tables):
        rows = drop_empty_columns(rows)
        hits = [ri for ri, r in enumerate(rows)
                if r and any(RE_NET_REVENUE.match(c) for c in r)]
        if not hits or hits[0] == 0:
            continue
        head = [c for r in rows[:hits[0]] for c in r]
        if not any(RE_YOY.search(c) for c in head):
            continue
        if len(hits) != 1:
            if rejected is not None:
                rejected.append({
                    "table_index": ti,
                    "reason": f"같은 표 안 `Net Revenue` 행이 정확히 1개가 아니다 "
                              f"({len(hits)}개) — 첫 행을 고르지 않는다",
                    "net_revenue_rows": hits,
                    "row_labels": [rows[i][:2] for i in hits],
                })
            continue
        cands.append((ti, rows, hits[0]))
    return cands


def build_header(rows, data_i):
    """데이터 행 위의 모든 행을 열 단위로 이어 붙여 헤더 한 줄을 만든다."""
    width = len(rows[data_i])
    cols = []
    for i in range(width):
        parts = []
        for r in rows[:data_i]:
            if i < len(r) and r[i]:
                parts.append(r[i])
        cols.append(norm(" ".join(parts)))
    return cols


# ══════════════════════════════════════════════════════════════════════
# 최종 관측 후보 — L1 ∧ L2 (CIO 승인 2026-08-16)
#
#   확정 계약:
#     L1 candidates → 각 후보에 L2 적용 → L1∧L2 성공 후보 **전체** 수집
#                   → 정확히 1건이면 선택 / 0건·2+ 면 evidence 와 함께 fail-closed
#
#   ★ 책임 분리 (CIO 판정)
#     `final_candidates` 는 **후보 전체를 돌려주는 것까지**가 책임이다.
#       ⛔ 안에서 1건을 고르지 않는다. ⛔ 0/2+ 를 성공값으로 축약하지 않는다.
#     유일성 판정은 `unique_candidate` 로 **호출부에서 명시적으로** 보인다.
#     ⇒ 두 층이 섞이면 「전체를 돌려준다」는 계약과 충돌하고, 어느 층이 틀렸는지
#       회귀가 가리키지 못한다.
#
#   ⛔ 이번 범위 밖: L3(문서 단위 단위검증) · build_header · 공용 helper.
def final_candidates(tables, month_name: str, year: int, rejected=None) -> list:
    """L1 ∧ L2 를 모두 통과한 표를 **전부** 돌려준다.

    ⛔ 고르지 않는다 · 축약하지 않는다 · 첫 성공에서 멈추지 않는다.
    `rejected` 를 주면 L1/L2 에서 걸린 표의 evidence 를 담는다.
    """
    out = []
    for ti, rows, di in find_decision_table(tables, month_name, year,
                                            rejected=rejected):
        header = build_header(rows, di)
        bound, probs = bind_columns(header, rows[di], month_name, year)
        if bound is None:
            if rejected is not None:
                rejected.append({
                    "table_index": ti,
                    "reason": f"L2 컬럼 identity 미충족 — {'; '.join(probs)}",
                    "header": header,
                    "data_row": rows[di],
                })
            continue
        out.append({"table_index": ti, "data_i": di,
                    "header": header, "bound": bound})
    return out


def unique_candidate(cands):
    """최종 후보의 **유일성 판정**. (선택 | None, 사유 목록) 을 돌려준다.

    ⛔ 0건·2건 이상이면 고르지 않는다 — 문서 순서가 값을 정하게 두지 않는다.
    """
    if len(cands) != 1:
        return None, [
            f"최종 관측 후보가 정확히 1건이 아니다 ({len(cands)}건"
            + (f": table {[c['table_index'] for c in cands]}" if cands else "")
            + ") — 첫 후보를 고르지 않는다"]
    return cands[0], []


def bind_columns(header, data, month_name: str, year: int):
    """컬럼을 **의미**로 묶는다. 모호하면 묶지 않고 사유를 돌려준다."""
    y1 = year - 1
    m = re.escape(month_name)

    # ★ 헤더 셀이 기간 라벨만으로 이루어져 있다고 가정하지 않는다.
    #   실측(2026-07 live run): 제목·단위 선언이 같은 표의 헤더 셀에 흡수돼 있었다.
    #     [0] 'TSMC July Revenue Report (Consolidated): Period'
    #     [7] '(Unit:NT$ million) January to July 2025'
    #   ⛔ 그렇다고 단순 contains 로 완화하면 `January to July 2025` 가 `July 2025` 로
    #      오인돼 대상월/누계가 뒤섞인다. 그래서 **누계를 먼저 식별해 제외한 뒤**
    #      월 표현을 찾는다 (CIO 판정 2026-08-15).
    CUM_ANY = rf"January\s+to\s+{m}\s+\d{{4}}"

    def cum_hits(yr):
        return [i for i, h in enumerate(header)
                if re.search(rf"January\s+to\s+{m}\s+{yr}\b", h, re.I)]

    def month_hits(yr):
        out = []
        for i, h in enumerate(header):
            residue = re.sub(CUM_ANY, " ", h, flags=re.I)   # ← 누계 표현 제거가 먼저다
            if re.search(rf"\b{m}\s+{yr}\b", residue, re.I):
                out.append(i)
        return out

    i_cum = cum_hits(year)
    i_cum_prev = cum_hits(y1)
    i_cur = month_hits(year)
    i_prev_year = month_hits(y1)
    i_yoy = [i for i, h in enumerate(header) if RE_YOY.search(h)]
    i_mom = [i for i, h in enumerate(header) if RE_MOM.search(h)]

    problems = []
    for label, hits in (("대상월", i_cur), ("전년동월", i_prev_year),
                        ("당해누계", i_cum), ("전년누계", i_cum_prev)):
        if len(hits) != 1:
            problems.append(f"{label} 헤더가 정확히 1개가 아니다 ({len(hits)}개)")
    if len(i_yoy) != 2:
        problems.append(f"Y-o-Y 헤더가 2개가 아니다 ({len(i_yoy)}개)")
    if problems:
        return None, problems

    # ★ 의미 인접: 전년동월 컬럼 뒤의 첫 Y-o-Y = 월 YoY,
    #             전년누계 컬럼 뒤의 첫 Y-o-Y = 누계 YoY. index 자체에 의미를 두지 않는다.
    after = lambda anchor: [i for i in i_yoy if i > anchor]
    a_m = after(i_prev_year[0])
    a_c = after(i_cum_prev[0])
    if not a_m or not a_c:
        return None, ["Y-o-Y 컬럼이 앵커 뒤에 없다"]
    yoy_m, yoy_c = min(a_m), min(a_c)
    if yoy_m == yoy_c:
        return None, ["월 YoY 와 누계 YoY 가 같은 컬럼으로 묶였다 — 모호"]

    idx = {"monthly_revenue": i_cur[0], "monthly_prior_year": i_prev_year[0],
           "monthly_yoy": yoy_m, "cumulative_revenue": i_cum[0],
           "cumulative_prior_year": i_cum_prev[0], "cumulative_yoy": yoy_c}
    if i_mom:
        idx["mom_not_decision"] = i_mom[0]

    out = {}
    for k, i in idx.items():
        if i >= len(data):
            return None, [f"{k} 컬럼({i})이 데이터 행 범위를 넘는다"]
        v = data[i]
        if not RE_NUM.match(v):
            return None, [f"{k} 값이 숫자 형태가 아니다: {v!r}"]
        out[k] = v
    out["_header"] = header
    out["_column_index"] = idx
    return out, []


# ══════════════════════════════════════════════════════════════════
# cross-check 층 — 각 층이 스스로 공표한 정밀도로만 비교한다 (새 임계값 없음)
# ══════════════════════════════════════════════════════════════════
def prose_layer(text: str, month_name: str, year: int):
    out = {}
    m = re.search(rf"revenue for {month_name}\s+{year} was approximately "
                  rf"NT\$\s*([\d,.]+)\s*(billion|million)", text, re.I)
    if m:
        out["monthly"] = (m.group(1), m.group(2).lower())
    c = re.search(rf"revenue for January through {month_name}\s+{year} totaled "
                  rf"NT\$\s*([\d,.]+)\s*(billion|million)", text, re.I)
    if c:
        out["cumulative"] = (c.group(1), c.group(2).lower())
    return out


def thousands_layer(tables, month_name: str, year: int):
    """`Revenue (in NT$ thousands)` 표에서 당월/누계 원값을 찾는다 (cross-check 전용)."""
    tgt = month_name[:3].lower()
    out = {}
    for rows in tables:
        rows = drop_empty_columns(rows)
        for r in rows:
            if not r:
                continue
            label = r[0].lower().rstrip(".")
            nums = [c for c in r if RE_NUM.match(c) and len(c.replace(",", "")) >= 9]
            if not nums:
                continue
            if label.startswith(tgt) and "~" not in label and "to" not in label:
                out.setdefault("monthly", nums[0])
            if "~" in label or " to " in label:
                if label.startswith("jan") and tgt in label:
                    out.setdefault("cumulative", nums[0])
    return out


def to_int(s: str) -> int:
    return int(s.replace(",", ""))


def crosscheck(decision, prose, thousands):
    """cross-check 층 — **기록과 비교 결과 노출 전용** (CIO 판정 2026-08-15).

    ⛔ 이 함수의 결과로 Decision 값을 만들지 않는다. fallback 아니다.
    ⛔ thousands → million 변환으로 Decision 값을 **생성하지 않는다.**
       Consolidated NT$ million 공표값이 SSOT 이고 thousands 는 정합성 evidence 다.
    ⛔ 허용 범위(tolerance)를 새로 발명하지 않는다 — 양쪽 raw 값을 기록해 비교만 노출한다.
    반환 second 값은 **참고용 불일치 목록**이며 실행을 실패시키는 조건이 아니다.
    """
    notes, bad = [], []

    # ── 천원표 → 백만 ──────────────────────────────────────────────
    # ⚠️ TSMC 문서의 두 층은 **하나의 축약 규칙으로 연결되지 않는다** (실측):
    #      2026-07  467,580.548 → 표기 467,580  (버림)
    #      2026-06  442,679.969 → 표기 442,680  (반올림)
    #    따라서 어느 한쪽 규칙을 정답으로 못박으면 다른 달에서 오탐이 난다.
    #    ⛔ 임의의 허용오차(±N)를 만들지 않는다. 대신 "표기값은 천원값의 **버림 또는
    #       반올림 중 하나**여야 한다"만 요구하고, 어느 쪽이었는지 기록한다.
    #    ★ 이 관계를 어떻게 확정할지는 CIO 판정 대상이다 (표본 2개로 규칙을 정하지 않는다).
    for key, dk in (("monthly", "monthly_revenue"), ("cumulative", "cumulative_revenue")):
        if key in thousands:
            th = to_int(thousands[key])
            floor_v, round_v = th // 1000, (th + 500) // 1000
            want = to_int(decision[dk])
            how = ("버림 일치" if want == floor_v else
                   "반올림 일치" if want == round_v else "어느 쪽도 아님")
            # ★ raw 양쪽을 그대로 기록한다. 변환 규칙을 확정하지 않는다.
            notes.append(f"천원표 {key}: raw {thousands[key]} · Decision raw {decision[dk]} "
                         f"(참고 버림 {floor_v} / 반올림 {round_v}) → {how}")
            if how == "어느 쪽도 아님":
                bad.append(f"천원표 {key} 참고 불일치")

    # 산문 billion → Decision(백만) 을 산문의 공표 자릿수로 되돌려 비교한다
    for key, dk in (("monthly", "monthly_revenue"), ("cumulative", "cumulative_revenue")):
        if key in prose:
            val, unit = prose[key]
            dec = to_int(decision[dk])
            if unit == "billion":
                dp = len(val.split(".")[1]) if "." in val else 0
                got = f"{round(dec / 1000, dp):.{dp}f}"
                ok = got.replace(",", "") == val.replace(",", "")
                notes.append(f"산문 {key}: NT${val} billion vs Decision {dec} "
                             f"→ 같은 자릿수로 {got} → {'일치' if ok else '불일치'}")
            else:
                ok = val.replace(",", "") == str(dec)
                notes.append(f"산문 {key}: NT${val} million vs Decision {dec} "
                             f"→ {'일치' if ok else '불일치'}")
            if not ok:
                bad.append(f"산문 {key} 참고 불일치")
    return notes, bad


# ══════════════════════════════════════════════════════════════════
# 발표일 / provenance 시각
# ══════════════════════════════════════════════════════════════════
RE_BODY_DATE = re.compile(r"\b(%s)\s+(\d{1,2}),\s*(\d{4})\b" % "|".join(MONTHS))


def body_published_at(text: str, target_year: int, target_month_no: int):
    """본문 발표일. 대상월의 **다음 달**이어야 한다 (CIO 진술 기준)."""
    exp_y, exp_m = (target_year + 1, 1) if target_month_no == 12 else (target_year,
                                                                      target_month_no + 1)
    for mo in RE_BODY_DATE.finditer(text):
        mi = month_index(mo.group(1))
        yr = int(mo.group(3))
        if mi == exp_m and yr == exp_y:
            return f"{yr:04d}-{mi:02d}-{int(mo.group(2)):02d}", mo.group(0)
    return None, None


# ══════════════════════════════════════════════════════════════════
def get(url: str, timeout: int = FETCH_TIMEOUT_SEC):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": FETCH_USER_AGENT})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if "gzip" in (r.headers.get("Content-Encoding") or "").lower():
            import gzip
            raw = gzip.decompress(raw)
        rec = {"url": url, "status": r.status, "final_url": r.geturl(),
               "content_type": r.headers.get("Content-Type", "(없음)"),
               "bytes": len(raw), "elapsed": round(time.monotonic() - t0, 3)}
    REQUEST_LOG.append(rec)
    print(f"  GET {rec['status']} · {rec['bytes']}B · {rec['elapsed']}s · {rec['final_url']}")
    return rec, raw


def identify(text: str, month_name: str, year: int):
    """월매출 보고서인지 **내용**으로 판정 — CIO 확정 4요건 (2026-08-15).

    ⛔ `(Unit:NT$ million)` 은 여기 들어가지 않는다. 그것은 **Decision 표 추출 시점의
       필수 검증**으로 옮겼다 (원문에서 제목·표 식별 정보와 단위 정보의 위치가 다르다).
    각 항목에 실패 시 근거를 찾을 probe 를 함께 둔다.
    """
    return [
        (f"제목 'TSMC {month_name} {year} Revenue Report'",
         bool(re.search(rf"TSMC\s+{month_name}\s+{year}\s+Revenue\s+Report", text, re.I)),
         r"Revenue\s+Report"),
        ("'Revenue Report (Consolidated)' 절",
         bool(re.search(r"Revenue\s+Report\s*\(\s*Consolidated\s*\)", text, re.I)),
         r"Consolidated"),
        (f"대상월 '{month_name} {year}'",
         bool(re.search(rf"\b{month_name}\s+{year}\b", text, re.I)),
         rf"{month_name}"),
        (f"당해 누계 기간 'January to {month_name} {year}'",
         bool(re.search(rf"January\s+to\s+{month_name}\s+{year}", text, re.I)),
         r"January\s+to"),
    ]


# ★ Decision 표 추출 시점의 **필수** 단위 검증 (CIO 판정 1, 2026-08-15).
#   ⛔ 실패하면 다른 표·산문으로 fallback 하지 않고 fail-closed.
CAND_LOG_LIMIT_C4 = 20      # 후보 evidence 출력 상한 (조용한 절단 금지)
RE_UNIT_MILLION = re.compile(r"\(\s*Unit\s*:?\s*NT\$\s*million\s*\)", re.I)


def verify_unit_million(text: str):
    """Decision 표가 `NT$ million` 층임을 확인한다. (판정, 근거) 를 돌려준다."""
    m = RE_UNIT_MILLION.search(text)
    if m:
        return True, m.group(0)
    return False, None


def observe(TARGET_MONTH: str):
    """한 대상월을 end-to-end 관측한다. (rc, 관측결과) 를 돌려준다."""
    ty, tm = int(TARGET_MONTH[:4]), int(TARGET_MONTH[5:7])
    month_name = MONTHS[tm - 1]
    print("C4 SEC EDGAR — TSMC monthly revenue end-to-end 검증")
    print(f"  target month  {TARGET_MONTH} ({month_name} {ty})  ★ 명시 입력 (persistent cursor 없음)")
    print(f"  ua  {FETCH_USER_AGENT}")
    print("  ⛔ Decision 은 Consolidated NT$ million 표에서만 — 산문·천원표는 cross-check 전용")
    print("  ⛔ C1/C2 fallback 없음 · YoY 재계산 금지 · 저장소 쓰기 없음\n")

    # ── ① Discovery ────────────────────────────────────────────────
    print("① Discovery — submissions metadata")
    _, raw = get(SUBMISSIONS_URL)
    rec = json.loads(raw.decode("utf-8"))["filings"]["recent"]
    n = len(rec["form"])
    ay, am = (ty + 1, 1) if tm == 12 else (ty, tm + 1)
    win = f"{ay:04d}-{am:02d}"
    cands = []
    for i in range(n):
        if rec["form"][i] != "6-K":
            continue
        if not rec["filingDate"][i].startswith(win):
            continue
        cands.append({"accession": rec["accessionNumber"][i],
                      "filing_date": rec["filingDate"][i],
                      "report_date": rec.get("reportDate", [""] * n)[i],
                      "acceptance": rec.get("acceptanceDateTime", [""] * n)[i],
                      "primary_doc": rec.get("primaryDocument", [""] * n)[i],
                      "doc_desc": rec.get("primaryDocDescription", [""] * n)[i]})
    print(f"  discovery window  filingDate = {win}-* (대상월 다음 달)")
    print(f"  6-K 후보          {len(cands)}건")
    # ★ 파일명·description 은 **우선순위 hint 전용**. 판정 근거가 아니다.
    cands.sort(key=lambda c: (0 if "revenue" in c["primary_doc"].lower() else 1,
                              c["filing_date"]))
    for k, c in enumerate(cands):
        hint = "hint:파일명 revenue" if "revenue" in c["primary_doc"].lower() else ""
        print(f"    [{k}] {c['filing_date']} · {c['accession']} · {c['primary_doc']} {hint}")
    print("  ⛔ hint 는 순서만 바꾼다 — 최종 판정은 문서 내용으로 한다")
    if not cands:
        print("\n⛔ discovery 결과 0건 — 중단")
        return 1, None

    # ── ② Verification ─────────────────────────────────────────────
    print("\n② Verification — 문서 내용으로 월매출 보고서 확정")
    hit = None
    for k, c in enumerate(cands):
        url = f"{ARCHIVE_BASE}/{c['accession'].replace('-', '')}/{c['primary_doc']}"
        print(f"\n  후보[{k}] {c['primary_doc']}")
        time.sleep(POLITE_DELAY_SEC)
        try:
            r, body = get(url)
        except Exception as e:                                   # noqa: BLE001
            print(f"    ✗ 요청 실패 — {type(e).__name__}: {e}")
            continue
        html_text = body.decode("utf-8", errors="replace")
        text = strip_html(html_text)
        checks = identify(text, month_name, ty)
        okk = all(v for _, v, _ in checks)
        for label, v, probe in checks:
            print(f"    {'✓' if v else '✗'} {label}")
            if not v:                          # ★ CIO 판정 3 — 근거를 반드시 남긴다
                for ln in evidence(text, probe):
                    print(f"        근거후보 {ln}")
        if okk:
            print("    → 월매출 보고서로 확정")
            hit = (c, r, html_text, text)
            break
        print("    → 아님, 값을 읽지 않고 다음 후보로")

    if hit is None:
        print("\n⛔ 월매출 보고서를 확정하지 못했다 — 값을 읽지 않고 중단")
        return 1, None
    c, r, html_text, text = hit

    # ── ③ Decision observation — Consolidated NT$ million 표만 ─────
    print("\n③ Decision observation — Consolidated NT$ million 표")
    # ★ 단위 검증은 식별이 아니라 **추출 시점의 필수 게이트** (CIO 판정 1).
    unit_ok, unit_ev = verify_unit_million(text)
    print(f"  단위 선언 '(Unit:NT$ million)'  {'확인 ' + repr(unit_ev) if unit_ok else '미확인'}")
    if not unit_ok:
        print("\n⛔ Decision 표의 단위 선언을 확인하지 못했다 — 값을 읽지 않는다")
        print("   ⛔ 다른 표(NT$ thousands)나 산문으로 fallback 하지 않는다.")
        print("   근거 — 'Unit' 주변 normalized text:")
        for ln in evidence(text, r"Unit"):
            print(f"     {ln}")
        print("   근거 — 'million' 주변:")
        for ln in evidence(text, r"million", limit=2):
            print(f"     {ln}")
        return 1, None
    parser = TableCollector()
    parser.feed(html_text)
    tables = parser.tables
    print(f"  문서 내 table 수  {len(tables)}")
    rejected = []
    found = find_decision_table(tables, month_name, ty, rejected=rejected)
    rejected_l1 = list(rejected)
    print(f"  'Net Revenue' + Y-o-Y 헤더를 가진 표  {len(found)}건")
    # ⛔ 결과만 남기지 않는다 — 무엇을 왜 걸렀는지 함께 남긴다 (collector 공통 규칙)
    for rj in rejected:
        print(f"  ⚠️ table[{rj['table_index']}] 후보 제외 — {rj['reason']}")
        print(f"     Net Revenue 행 {rj['net_revenue_rows']} · 라벨 {rj['row_labels']}")
    # ★ 최종 후보를 **전부** 모은다 — 첫 성공에서 멈추지 않는다.
    cands = final_candidates(tables, month_name, ty, rejected=rejected)
    print(f"  최종 관측 후보 (L1∧L2)  {len(cands)}건 "
          f"→ table {[c['table_index'] for c in cands]}")
    for rj in rejected[len(rejected_l1):]:
        print(f"  ⚠️ table[{rj['table_index']}] 후보 제외 — {rj['reason'][:96]}")
    # ★ 유일성 판정은 여기서 **명시적으로** 한다 (함수 안에서 축약하지 않는다).
    chosen, uniq_probs = unique_candidate(cands)
    if chosen is None:
        print("\n⛔ 최종 관측 후보가 유일하지 않다 — 값을 읽지 않는다")
        for x in uniq_probs:
            print(f"   {x}")
        print("   ⛔ 산문·천원표로 대체하지 않는다 (fallback 금지).")
        print(f"   후보 전체 {[c['table_index'] for c in cands]}")
        for rj in rejected[:CAND_LOG_LIMIT_C4]:
            print(f"   제외 table[{rj['table_index']}] — {rj['reason'][:96]}")
        print("   ★ 위 근거가 다음 수정의 출발점이다 — 마크업을 추측하지 않는다.")
        return 1, None
    decision = chosen["bound"]
    print(f"  → table[{chosen['table_index']}] 에서 의미 결합 성공 (후보 1건)")

    print(f"  header  {decision['_header']}")
    print(f"  결합    {decision['_column_index']}")
    print("\n  ⛔ 원문 그대로, 반올림·단위 변환 없음:")
    print(f"    monthly_revenue      {decision['monthly_revenue']}")
    print(f"    monthly_yoy          {decision['monthly_yoy']}")
    print(f"    cumulative_revenue   {decision['cumulative_revenue']}")
    print(f"    cumulative_yoy       {decision['cumulative_yoy']}")
    print(f"    (참고, Decision 아님) M-o-M {decision.get('mom_not_decision')} · "
          f"전년동월 {decision['monthly_prior_year']} · 전년누계 {decision['cumulative_prior_year']}")

    # ── ④ cross-check 층 ───────────────────────────────────────────
    print("\n④ cross-check — ⛔ fallback 아님, 정합성 확인 전용")
    pl = prose_layer(text, month_name, ty)
    tl = thousands_layer(tables, month_name, ty)
    print(f"  산문 층    {pl or '(미검출)'}")
    print(f"  천원표 층  {tl or '(미검출)'}")
    notes, bad = crosscheck(decision, pl, tl)
    for nline in notes:
        print(f"    {nline}")
    if bad:
        print(f"  ⚠️ 참고 불일치 {bad}")
        print("     ⛔ 그래도 Decision 값을 바꾸거나 다른 층에서 가져오지 않는다.")
        print("     ★ cross-check 는 기록·노출 전용이다 (CIO 판정 2026-08-15) — "
              "이 불일치로 실행을 실패시키지 않는다.")
    else:
        print("  ✓ 참고 비교 이상 없음")
    print("  ★ 양쪽 raw 값을 기록했을 뿐 변환 규칙을 확정하지 않았다 — 새 tolerance 없음")

    # ── ⑤ 시각 계층 ────────────────────────────────────────────────
    print("\n⑤ 시각 계층 — published_at 과 provenance 를 섞지 않는다")
    pub, ev = body_published_at(text, ty, tm)
    print(f"  published_at (본문 발표일)      {pub}   근거 {ev!r}")
    print(f"  SEC filingDate                 {c['filing_date']}")
    print(f"  SEC acceptanceDateTime         {c['acceptance']}   ★ provenance 전용")
    if pub is None:
        print("\n⛔ 본문에서 발표일을 얻지 못했다 — SEC 접수시각으로 대체하지 않는다")
        return 1, None
    if pub != c["filing_date"]:
        print("\n⛔ 본문 발표일과 SEC filingDate 가 다르다 — 모순으로 보고 중단한다")
        return 1, None
    print("  ✓ 본문 발표일 == SEC filingDate")

    # ── ⑥ fixture differential ─────────────────────────────────────
    print("\n⑥ fixture differential")
    exp = FIXTURE_EXPECTED.get(TARGET_MONTH)
    if exp is None:
        print(f"  ⛔ {TARGET_MONTH} 의 승인 기대값이 없다")
        return 1, None
    diff = 0
    for key, dk in (("monthly_revenue_ntd_mn", "monthly_revenue"),
                    ("monthly_yoy_pct", "monthly_yoy"),
                    ("cumulative_revenue_ntd_mn", "cumulative_revenue"),
                    ("cumulative_yoy_pct", "cumulative_yoy")):
        got, want = decision[dk], exp[key]
        same = got.replace(",", "") == want.replace(",", "")
        diff += 0 if same else 1
        print(f"  {key:28s} 기대 {want!r} · 관측 {got!r} → {'일치' if same else '불일치'}")
    if diff:
        print(f"\n⛔ 승인 기대값과 {diff}건 불일치 — fixture 를 고치지 않는다. CIO 보고 대상이다.")
        return 1, None

    # ── ⑦ 저장소 무변경 ────────────────────────────────────────────
    made = [p for p in ("data/latest_tsmc_monthly.json", "data/latest_sec_6k.json")
            if os.path.exists(os.path.join(ROOT, p))]
    print(f"\n⑦ 저장소 — 예기치 않은 산출물 {made or '없음'}")
    if made:
        return 1, None

    print(f"\n  요청 수 총 {len(REQUEST_LOG)}건")
    result = {"target_month": TARGET_MONTH, "published_at": pub,
              "sec_filing_date": c["filing_date"], "sec_acceptance": c["acceptance"],
              "accession": c["accession"], "primary_doc": c["primary_doc"],
              "monthly_revenue_ntd_mn": decision["monthly_revenue"],
              "monthly_yoy_pct": decision["monthly_yoy"],
              "cumulative_revenue_ntd_mn": decision["cumulative_revenue"],
              "cumulative_yoy_pct": decision["cumulative_yoy"]}
    print("\n✅ C4 end-to-end PASS — discovery → 내용 확정 → Decision 표 추출 → "
          "cross-check → 발표일 분리 → fixture differential")
    print("   ⛔ 그래도 RULE-0003/0007/0008 상태를 바꾸지 않는다.")
    print("   ⛔ `Official Fetch/Extraction` 은 OPEN 유지 · Production HOLD · evaluator 미연결.")
    print("   ★ 상태 변경은 CIO 판정 대상이다.")
    return 0, result


def contiguity_checks(seq):
    """월 연속성 입력이 성립하는지 — 순수 함수라 회귀에서 반증 가능하다.

    ⛔ RULE-0003 을 평가하지 않는다. 계열을 **만들 수 있는가**만 본다.
    """
    months = [r["target_month"] for r in seq]
    gaps = []
    for a, b in zip(seq, seq[1:]):
        ay, am = int(a["target_month"][:4]), int(a["target_month"][5:7])
        by, bm = int(b["target_month"][:4]), int(b["target_month"][5:7])
        nxt = (ay + 1, 1) if am == 12 else (ay, am + 1)
        if (by, bm) != nxt:
            gaps.append(f"{a['target_month']}→{b['target_month']}")
    accs = [r["accession"] for r in seq]
    pubs = [r["published_at"] for r in seq]
    return [
        ("대상월 중복 없음", len(set(months)) == len(months), str(months)),
        ("월이 끊김 없이 연속", not gaps, str(gaps)),
        ("대상월이 오름차순", months == sorted(months), str(months)),
        ("서로 다른 filing 에서 왔다", len(set(accs)) == len(accs), str(accs)),
        ("published_at 이 대상월 순서와 같은 방향", pubs == sorted(pubs), str(pubs)),
    ]


def main() -> int:
    print("=" * 74)
    print("C4 SEC EDGAR — 다월 관측 + 월 연속성 입력 구성 검증")
    print(f"  대상월  {TARGET_MONTHS}")
    print("  ⛔ RULE-0003 을 평가하지 않는다 — 관측 capability 만 본다")
    print("=" * 74)
    results = []
    for tmn in TARGET_MONTHS:
        print("\n" + "─" * 74)
        rc, res = observe(tmn)
        if rc != 0:
            print(f"\n⛔ {tmn} 관측 실패 — 뒤 단계로 넘어가지 않는다")
            return rc
        results.append(res)

    # ── ⑧ 월 연속성 입력 구성 ───────────────────────────────────────
    print("\n" + "=" * 74)
    print("⑧ 월 연속성(month contiguity) 입력 구성")
    seq = sorted(results, key=lambda r: r["target_month"])
    for r in seq:
        print(f"  {r['target_month']}  월 YoY {r['monthly_yoy_pct']:>6}  "
              f"누계 YoY {r['cumulative_yoy_pct']:>6}  "
              f"published_at {r['published_at']}  acc {r['accession']}")

    checks = contiguity_checks(seq)
    for label, v, extra in checks:
        print(f"  {'✓' if v else '✗'} {label}" + ("" if v else f" — {extra}"))
    if not all(v for _, v, _ in checks):
        print("\n⛔ 월 연속성 입력을 구성하지 못했다")
        return 1

    print("\n  ★ 구성된 연속 관측 계열 (RULE-0003 이 요구하는 '2개월 연속' 의 입력 형태):")
    print("    " + " · ".join(f"{r['target_month']}={r['monthly_yoy_pct']}" for r in seq))
    print("  ⛔ 이 계열로 RULE-0003 을 평가하지 않는다. 조건 발동 여부는 이 run 의 대상이 아니며,")
    print("     여기서 증명한 것은 **두 달을 정확히 관측해 계열을 만들 수 있다**는 capability 뿐이다.")
    print("  ⛔ 상태 변경 없음 · Production HOLD · consumable_by_evaluator=false · evaluator 미연결.")
    print("\n✅ 다월 관측 + 월 연속성 입력 구성 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
