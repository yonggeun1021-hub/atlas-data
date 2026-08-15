"""TSMC Monthly Revenue collector — pilot (CIO 승인 2026-08-15).

대상은 `RULE-0003 · RULE-0007 · RULE-0008` 뿐이다.

★ Primary authoritative source (CIO 확정)
    TSMC Investor Relations — Historical Monthly Revenue
    https://investor.tsmc.com/english/monthly-revenue

★ Decision 입력 계약 (CIO 확정 2026-08-15)
    monthly YoY     = TSMC **공표값** 그대로
    cumulative YoY  = TSMC 가 표에 공표한 **Total YoY** 그대로
    ⛔ 월별 매출액을 합산해 더 정밀한 YoY 를 역산하여 판정에 쓰지 않는다.
       그 파생값은 `validation` 에만 남기고 Decision 입력이 아니다.
    ⛔ 공표 정밀도에 추가 반올림·자릿수 확장을 하지 않는다. `37.0` 은 `37.0` 이다.
       비교는 십진 그대로 한다 — 부동소수 왕복을 거치지 않는다.

★ 이 파일이 하지 않는 것
    ⛔ Rule threshold(40 / 35 / 34.6)를 구현하지 않는다 — 판정은 이 층의 일이 아니다.
    ⛔ `config/rules.json` 의 `data_status` · `source_qualification` ·
       `evaluator_status` 를 건드리지 않는다. collector 가 생겼다는 사실만으로
       상태를 승격시키지 않는다 (CIO 지시).
    ⛔ evaluator 배선 · Production 연결 · 보도자료 자동 fallback 을 하지 않는다.

★ 보도자료(`pr.tsmc.com`)는 **secondary verification only** 다. primary 장애 시
  자동으로 Decision SSOT 로 승격시키지 않는다 — source fallback 은 별도 판정 사항이다.

★ 발표일을 매월 10일로 하드코딩하지 않는다. `target_month` 와 실제 `published_at`
  을 분리해 싣는다 (TSMC 공식 일정 자체가 휴무 등으로 이동한다).
"""
from __future__ import annotations

import json
import os
import re
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "collectors", "fixtures",
                       "tsmc_ir_monthly_2026_snapshot.tsv")

SOURCE = "TSMC Investor Relations — Historical Monthly Revenue"
SOURCE_URL = "https://investor.tsmc.com/english/monthly-revenue"
SOURCE_TIER = "official_published"
SECONDARY_ONLY = {
    "name": "TSMC monthly revenue press release",
    "url": "https://pr.tsmc.com/",
    "role": "secondary verification only",
    "note": "⛔ primary 장애 시 자동 승격 금지 — source fallback 은 별도 CIO 판정 사항",
}
COLLECTOR_VERSION = "tsmc_monthly v0.1 (pilot · observation only)"

MONTHS = {"Jan.": 1, "Feb.": 2, "Mar.": 3, "Apr.": 4, "May": 5, "Jun.": 6,
          "Jul.": 7, "Aug.": 8, "Sept.": 9, "Oct.": 10, "Nov.": 11, "Dec.": 12}
TOTAL_LABEL = "Total"


class SourceUnavailable(RuntimeError):
    """원천을 신뢰할 수 없다. 이 경우 산출물을 만들지 않는다 (extract.py 와 같은 계약)."""


# ══════════════════════════════════════════════════════════════════════
# 파싱 — 순수 함수. 네트워크를 타지 않는다.
# ══════════════════════════════════════════════════════════════════════
def _num(s):
    """`401,255` → Decimal. 빈 칸은 None (미발표월)."""
    s = (s or "").strip().replace(",", "")
    if not s:
        return None
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        raise SourceUnavailable(f"수치로 읽을 수 없다: {s!r}")
    return Decimal(s)


def _pct(s):
    """`44.7%` → ('44.7', Decimal('44.7')). ★ 공표 문자열을 그대로 보존한다."""
    s = (s or "").strip()
    if not s:
        return None, None
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*%", s)
    if not m:
        raise SourceUnavailable(f"퍼센트로 읽을 수 없다: {s!r}")
    return m.group(1), Decimal(m.group(1))


def parse_table(text: str) -> dict:
    """IR 월매출 표(연 1장)를 읽는다. 입력은 헤더 + 월 행 + Total 행."""
    year = None
    rows, total = [], None
    for line in text.splitlines():
        if line.startswith("#"):
            m = re.search(r"year=(\d{4})", line)
            if m:
                year = int(m.group(1))
            continue
        if not line.strip():
            continue
        cells = line.split("\t")
        label = cells[0].strip()
        if label == "Month":
            continue
        rev = _num(cells[1] if len(cells) > 1 else "")
        pct_s, pct_d = _pct(cells[2] if len(cells) > 2 else "")
        if label == TOTAL_LABEL:
            total = {"net_revenue_ntd_mn": rev,
                     "yoy_pct_published": pct_s, "yoy_pct_decimal": pct_d}
        elif label in MONTHS:
            rows.append({"month_no": MONTHS[label], "label": label,
                         "net_revenue_ntd_mn": rev,
                         "yoy_pct_published": pct_s, "yoy_pct_decimal": pct_d})
        else:
            raise SourceUnavailable(f"알 수 없는 행 라벨: {label!r}")

    if year is None:
        raise SourceUnavailable("표에 연도 표기가 없다")
    if not rows:
        raise SourceUnavailable("월 행이 하나도 없다")
    if total is None:
        raise SourceUnavailable("Total 행이 없다 — 누계 YoY 는 공표값만 쓴다")
    if len(rows) != 12:
        raise SourceUnavailable(f"월 행이 12개가 아니다: {len(rows)}")
    return {"year": year, "months": rows, "total": total}


# ══════════════════════════════════════════════════════════════════════
# 정규화 — 관측만 한다. 판정하지 않는다.
# ══════════════════════════════════════════════════════════════════════
def normalize(parsed: dict, published_at: str | None = None,
              audited: bool = False) -> dict:
    """`target_month` 와 `published_at` 을 분리해 싣는다.

    ⛔ 발표일을 계산하지 않는다. 호출자가 **관측한** 실제 발표일을 받는다 —
       TSMC 공식 일정 자체가 휴무 등으로 이동하므로 '익월 10일' 은 사실이 아니다.

    ★ 관측(observation)과 판정 준비(decision_ready)를 분리한다 (CIO 확정 2026-08-15).
      공식 페이지에서 월매출을 정상 취득했다면 그것은 **관측 성공**이다.
      발표일 provenance 를 확보하지 못했다면 `decision_ready=false` 로 막을 뿐,
      이미 공식 공표된 월매출을 수집 실패로 버리지 않는다.
    """
    if published_at is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published_at):
        raise SourceUnavailable(f"published_at 형식이 아니다: {published_at!r}")

    months = {}
    for r in parsed["months"]:
        key = f"{parsed['year']}-{r['month_no']:02d}"
        if r["net_revenue_ntd_mn"] is None and r["yoy_pct_published"] is None:
            continue                      # 미발표월 — 없는 것으로 둔다
        if r["net_revenue_ntd_mn"] is None or r["yoy_pct_published"] is None:
            raise SourceUnavailable(f"{key}: 매출과 YoY 중 하나만 있다 — 부분 관측")
        months[key] = {
            "target_month": key,
            "net_revenue_ntd_mn": str(r["net_revenue_ntd_mn"]),
            # ★ Decision 입력 — 공표 문자열 그대로
            "monthly_yoy_pct_published": r["yoy_pct_published"],
        }

    t = parsed["total"]
    if t["yoy_pct_published"] is None:
        raise SourceUnavailable("Total YoY 가 공표되지 않았다")
    reported = sorted(months)
    cumulative = {
        "through_month": reported[-1] if reported else None,
        "months_covered": len(reported),
        "net_revenue_ntd_mn": str(t["net_revenue_ntd_mn"]),
        # ★ Decision 입력 — 공표된 Total YoY 그대로
        "cumulative_yoy_pct_published": t["yoy_pct_published"],
    }

    return {
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "source_tier": SOURCE_TIER,
        "source_status": "official_published",
        "secondary_verification_only": SECONDARY_ONLY,
        "collector_version": COLLECTOR_VERSION,
        "year": parsed["year"],
        "published_at": published_at,     # ★ 관측된 실제 발표일 (없으면 None)
        "published_at_status": ("observed" if published_at else "unobserved"),
        # ★ 관측은 성공했지만 판정 준비는 아직 아니다 — 둘을 섞지 않는다.
        "decision_ready": bool(published_at) and bool(months),
        "decision_ready_blockers": (
            [] if (published_at and months)
            else ([] if published_at else ["published_at_unobserved"])
                 + ([] if months else ["no_reported_month"])),
        "decision_ready_note": (
            "★ 발표일 provenance 가 없으면 판정 입력으로 쓰지 않는다. 그러나 이미 공식 "
            "공표된 월매출 관측 자체는 버리지 않는다 — ⛔ 발표일을 추정해 채우지 않는다."),
        "audited": audited,               # 당해연도는 TSMC 가 unaudited 로 명시
        "decision_input_contract": (
            "monthly_yoy_pct_published · cumulative_yoy_pct_published 만 판정 입력이다. "
            "net_revenue 와 calculated_* 는 provenance · validation 용이다."),
        "months": months,
        "cumulative": cumulative,
        "validation": _validate(parsed, months, cumulative),
        "observation_only": ("★ 이 산출물은 관측이다. Rule threshold 를 적용하지 않으며 "
                             "config/rules.json 의 상태를 바꾸지 않는다."),
    }


def _validate(parsed, months, cumulative):
    """월별 매출 합으로 누계를 재계산한다 — **validation 전용**.

    ⛔ 이 값은 Decision 입력이 아니다. 공표값과 다르더라도 공표값을 바꾸지 않는다.
       불일치는 숨기지 않고 표시만 한다.
    """
    s = sum(Decimal(m["net_revenue_ntd_mn"]) for m in months.values())
    pub = Decimal(cumulative["net_revenue_ntd_mn"])
    return {
        "calculated_cumulative_revenue_ntd_mn": str(s),
        "published_cumulative_revenue_ntd_mn": str(pub),
        "cumulative_revenue_matches": s == pub,
        "note": ("★ 재계산 누계 YoY 는 만들지 않는다 — 직전 연도 월별 매출이 필요하고, "
                 "무엇보다 판정 입력은 TSMC 공표 Total YoY 이기 때문이다 (CIO 확정). "
                 "여기서는 매출 합계 일치만 확인한다."),
    }


# ══════════════════════════════════════════════════════════════════════
# 관측 도우미 — 판정이 아니라 **입력 준비**다
# ══════════════════════════════════════════════════════════════════════
def consecutive_runs(months: dict) -> list:
    """연속한 `YYYY-MM` 구간만 묶는다. ⛔ 결측월은 연속으로 세지 않는다."""
    keys = sorted(months)
    runs, cur = [], []
    for k in keys:
        if cur:
            py, pm = map(int, cur[-1].split("-"))
            cy, cm = map(int, k.split("-"))
            if (cy * 12 + cm) - (py * 12 + pm) != 1:
                runs.append(cur)
                cur = []
        cur.append(k)
    if cur:
        runs.append(cur)
    return runs


def rule_inputs(norm: dict) -> dict:
    """세 Rule 이 요구하는 **관측 입력**만 뽑는다.

    ⛔ 임계값(40 / 35 / 34.6)을 여기서 비교하지 않는다. 판정은 이 층의 일이 아니다.
    """
    months = norm["months"]
    return {
        "RULE-0003": {
            "needs": "단월 YoY · 월 연속성",
            "monthly_yoy_pct_published": {k: v["monthly_yoy_pct_published"]
                                          for k, v in sorted(months.items())},
            "consecutive_runs": consecutive_runs(months),
            "note": "⛔ 결측월은 연속으로 세지 않는다. threshold 비교는 하지 않는다.",
        },
        "RULE-0007": {
            "needs": "단월 YoY · 누계 YoY",
            "latest_month": norm["cumulative"]["through_month"],
            "monthly_yoy_pct_published":
                months.get(norm["cumulative"]["through_month"], {})
                .get("monthly_yoy_pct_published"),
            "cumulative_yoy_pct_published":
                norm["cumulative"]["cumulative_yoy_pct_published"],
        },
        "RULE-0008": {
            "needs": "단월 YoY · 누계 YoY (0007 과 동일 관측, 효과만 반대)",
            "latest_month": norm["cumulative"]["through_month"],
            "monthly_yoy_pct_published":
                months.get(norm["cumulative"]["through_month"], {})
                .get("monthly_yoy_pct_published"),
            "cumulative_yoy_pct_published":
                norm["cumulative"]["cumulative_yoy_pct_published"],
        },
        "threshold_note": ("⛔ collector 는 40 / 35 / 34.6 을 알지 못한다. "
                           "임계값 비교는 Rule 평가 층의 일이며 아직 연결되지 않았다."),
    }


def detect_revisions(prev: dict | None, new: dict) -> list:
    """같은 `target_month` 의 공표값이 바뀌었는가. ⛔ silent overwrite 를 만들지 않는다."""
    if not prev:
        return []
    out = []
    for k, n in sorted(new["months"].items()):
        p = prev.get("months", {}).get(k)
        if not p:
            continue
        for f in ("net_revenue_ntd_mn", "monthly_yoy_pct_published"):
            if p[f] != n[f]:
                out.append({"target_month": k, "field": f,
                            "from": p[f], "to": n[f],
                            "prev_published_at": prev.get("published_at"),
                            "new_published_at": new.get("published_at")})
    pc, nc = prev.get("cumulative", {}), new["cumulative"]
    for f in ("net_revenue_ntd_mn", "cumulative_yoy_pct_published"):
        if pc.get(f) is not None and pc.get(f) != nc.get(f) \
                and pc.get("through_month") == nc.get("through_month"):
            out.append({"target_month": nc.get("through_month"), "field": f"cumulative.{f}",
                        "from": pc.get(f), "to": nc.get(f),
                        "prev_published_at": prev.get("published_at"),
                        "new_published_at": new.get("published_at")})
    return out


def from_fixture(path=FIXTURE, published_at=None, audited=False) -> dict:
    """네트워크를 타지 않는 경로. 회귀는 이것만 쓴다."""
    with open(path, encoding="utf-8") as f:
        return normalize(parse_table(f.read()), published_at, audited)


if __name__ == "__main__":
    n = from_fixture()
    print(f"[TSMC monthly] {n['source']}")
    print(f"  year {n['year']} · published_at {n['published_at']} · audited {n['audited']}")
    print(f"  관측 월 {len(n['months'])} · 누계 {n['cumulative']['through_month']} "
          f"({n['cumulative']['months_covered']}개월)")
    print(f"  단월 YoY(최신) "
          f"{n['months'][n['cumulative']['through_month']]['monthly_yoy_pct_published']}"
          f" · 누계 YoY {n['cumulative']['cumulative_yoy_pct_published']}")
    print(f"  매출 합계 일치 {n['validation']['cumulative_revenue_matches']}")
    print(f"  연속 구간 {consecutive_runs(n['months'])}")
    print("  ⛔ 관측만 한다 — threshold 비교 · 상태 승격 · evaluator 연결 없음")


# ══════════════════════════════════════════════════════════════════════
# Official Revenue HTML Extraction (CIO 승인 2026-08-15)
#
# ★ 확정 URL 계약 — 연도는 **경로**에 들어간다. `?year=` 방식은 쓰지 않는다.
#     https://investor.tsmc.com/english/monthly-revenue/{YYYY}
#
# ★ 이 추출기는 마크업의 클래스명·중첩·행 순서에 기대지 않는다. 의존하는 것은
#   CIO 가 실제 공식 페이지에서 확인한 **의미 계약**뿐이다 —
#   heading `{YYYY} Monthly Revenue` · 컬럼 `Month` / `Consolidated Net Revenue` /
#   `YoY Change` · 월 행 · `Total` 행.
#   ⛔ 관측하지 않은 DOM 구조를 추정해 하드코딩하지 않는다.
#
# ⛔ live fetch 실패 시 fixture 로 대체하지 않는다. 마지막 성공값을 최신값처럼
#    재사용하지도 않는다. 실패는 실패다.
# ══════════════════════════════════════════════════════════════════════
from html.parser import HTMLParser                                   # noqa: E402
import html as _html                                                 # noqa: E402

MONTHLY_URL_TEMPLATE = "https://investor.tsmc.com/english/monthly-revenue/{year}"
HEADER_LABELS = ("Month", "Consolidated Net Revenue", "YoY Change")
HTML_FIXTURE = os.path.join(ROOT, "collectors", "fixtures",
                            "tsmc_ir_monthly_2026.html")
FETCH_USER_AGENT = "Atlas Research (yonggeun1021@gmail.com)"
FETCH_TIMEOUT_SEC = 30


def monthly_revenue_url(year: int) -> str:
    if not isinstance(year, int) or not (1999 <= year <= 2100):
        raise SourceUnavailable(f"연도가 비정상이다: {year!r}")
    return MONTHLY_URL_TEMPLATE.format(year=year)


class _TableReader(HTMLParser):
    """모든 `<table>` 의 셀 텍스트만 뽑는다. 클래스명·중첩에 의존하지 않는다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._t, self._row, self._cell, self._depth = [], None, None, None, 0
        self.text_chunks = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._t = []
        elif tag == "tr" and self._t is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(_clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._t.append(self._row)
            self._row = None
        elif tag == "table":
            if self._depth == 1 and self._t is not None:
                self.tables.append(self._t)
                self._t = None
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        self.text_chunks.append(data)


def _clean(s: str) -> str:
    """엔티티 · nbsp · 각주 표식 · 잉여 공백을 정리한다. 값 자체는 바꾸지 않는다."""
    s = _html.unescape(s or "").replace(" ", " ")
    s = re.sub(r"[\*†‡]", "", s)          # 각주 표식
    return re.sub(r"\s+", " ", s).strip()


def extract_from_html(html_text: str, requested_year: int) -> dict:
    """공식 HTML → `parse_table()` 과 **같은 모양**의 구조. 이후 정규화는 공유한다."""
    p = _TableReader()
    p.feed(html_text)

    # ① heading 의 연도가 요청 연도와 같은가 — 다르면 다른 해를 읽은 것이다
    page_text = _clean(" ".join(p.text_chunks))
    years = re.findall(r"(\d{4})\s+Monthly Revenue", page_text)
    if not years:
        raise SourceUnavailable("heading 에서 '{YYYY} Monthly Revenue' 를 찾지 못했다")
    if str(requested_year) not in years:
        raise SourceUnavailable(
            f"요청 연도 {requested_year} 와 heading 연도 {years} 가 다르다")

    # ② 세 컬럼을 모두 가진 표를 고른다 — 표시 순서나 위치에 기대지 않는다
    target = None
    for t in p.tables:
        for row in t[:3]:
            cells = [c for c in row]
            if all(any(lbl == c for c in cells) for lbl in HEADER_LABELS):
                target = (t, [cells.index(lbl) for lbl in HEADER_LABELS])
                break
        if target:
            break
    if target is None:
        raise SourceUnavailable(f"컬럼 {HEADER_LABELS} 을 가진 표가 없다")
    table, (i_m, i_r, i_y) = target

    months, total, seen = [], None, set()
    for row in table:
        if len(row) <= max(i_m, i_r, i_y):
            continue
        label = row[i_m]
        if label in HEADER_LABELS or not label:
            continue
        rev_s, yoy_s = row[i_r], row[i_y]
        if label == TOTAL_LABEL:
            if total is not None:
                raise SourceUnavailable("Total 행이 둘 이상이다")
            total = {"net_revenue_ntd_mn": _num(rev_s),
                     "yoy_pct_published": _pct(yoy_s)[0],
                     "yoy_pct_decimal": _pct(yoy_s)[1]}
        elif label in MONTHS:
            if label in seen:
                raise SourceUnavailable(f"월 행이 중복됐다: {label}")
            seen.add(label)
            months.append({"month_no": MONTHS[label], "label": label,
                           "net_revenue_ntd_mn": _num(rev_s),
                           "yoy_pct_published": _pct(yoy_s)[0],
                           "yoy_pct_decimal": _pct(yoy_s)[1]})
        else:
            raise SourceUnavailable(f"알 수 없는 행 라벨: {label!r}")

    if total is None:
        raise SourceUnavailable("Total 행이 없다 — 누계 YoY 는 공표값만 쓴다")
    if len(months) != 12:
        raise SourceUnavailable(f"월 행이 12개가 아니다: {len(months)}")

    # ③ 발표월은 1월부터의 연속 접두여야 한다 — 중간이 빈 채 뒤가 채워지면 이상이다
    months.sort(key=lambda r: r["month_no"])
    filled = [m["month_no"] for m in months if m["yoy_pct_published"] is not None]
    if filled and filled != list(range(1, len(filled) + 1)):
        raise SourceUnavailable(f"예상 외 populated month 배열이다: {filled}")

    return {"year": requested_year, "months": months, "total": total}


def from_html_fixture(path=HTML_FIXTURE, year=2026, published_at=None,
                      audited=False) -> dict:
    with open(path, encoding="utf-8") as f:
        return normalize(extract_from_html(f.read(), year), published_at, audited)


def fetch_live(year: int, timeout=FETCH_TIMEOUT_SEC) -> str:
    """공식 IR 페이지를 직접 읽는다.

    ⛔ 일반 회귀는 이 함수를 호출하지 않는다 (네트워크 의존 금지).
    ⛔ 실패 시 fixture 로 대체하지 않는다 — 예외를 그대로 올린다.
    """
    import urllib.request
    req = urllib.request.Request(
        monthly_revenue_url(year),
        headers={"User-Agent": FETCH_USER_AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status != 200:
            raise SourceUnavailable(f"HTTP {r.status}")
        return r.read().decode("utf-8", errors="replace")
