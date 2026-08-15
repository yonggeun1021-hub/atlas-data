#!/usr/bin/env python3
"""TSMC Live Fetch Validation — GitHub Actions 수동 실행 전용 (CIO 승인 2026-08-15).

목적은 하나다 — GitHub-hosted runner 가 TSMC 공식 IR 에서 **실제 HTML** 을 받아
기존 extractor 가 그것을 그대로 해석하는지 검증한다.

⛔ 이 스크립트가 하지 않는 것
   · 저장소 파일 수정 · commit · push
   · live HTML 을 authoritative fixture 로 덮어쓰기
   · live 실패 시 fixture fallback (실패는 실패다)
   · Rule 상태 변경 · evaluator wiring · Production 연결
   · `run_all.py` 의 결정론적 회귀에 편입 (이 파일은 그 목록에 없다)

★ 검증 대상 값은 **CIO 가 공식 페이지에서 확인한 것**과 승인된 TSV fixture 다.
  live 결과가 다르면 fixture 를 고치지 않고 **실패로 보고**한다.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
import tsmc_monthly as T                                             # noqa: E402

YEAR = 2026
EXPECT_JUL = {"target_month": "2026-07", "net_revenue_ntd_mn": "467580",
              "monthly_yoy_pct_published": "44.7"}
EXPECT_TOTAL_REV = "2872064"
EXPECT_TOTAL_YOY = "37.0"
FUTURE_MONTHS = {"2026-08", "2026-09", "2026-10", "2026-11", "2026-12"}

ok = []
bad = []


def check(name, cond, extra=""):
    (ok if cond else bad).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))


def main():
    print("TSMC Live Fetch Validation")
    print(f"  url  {T.monthly_revenue_url(YEAR)}")
    print(f"  ua   {T.FETCH_USER_AGENT} · timeout {T.FETCH_TIMEOUT_SEC}s")
    print("  ⛔ 실패 시 fixture 로 대체하지 않는다 · 저장소를 쓰지 않는다\n")

    # ── ① HTTP fetch ────────────────────────────────────────────────
    html = T.fetch_live(YEAR)               # 실패하면 예외 그대로 → non-zero
    check("HTTP fetch 성공", bool(html), "빈 응답")
    print(f"  받은 바이트 {len(html)}")

    # ② final URL / domain — 공식 IR 인지
    check("요청 URL 이 TSMC 공식 IR 도메인",
          T.monthly_revenue_url(YEAR).startswith(
              "https://investor.tsmc.com/english/monthly-revenue/"))
    check("연도가 경로에 있다", T.monthly_revenue_url(YEAR).endswith(f"/{YEAR}"))

    # ③ heading — extractor 가 연도 불일치를 자체 거부한다
    parsed = T.extract_from_html(html, YEAR)
    check(f"heading = '{YEAR} Monthly Revenue' 로 해석됨", parsed["year"] == YEAR)

    # ④ unaudited 고지
    check("unaudited 고지가 페이지에 있다",
          "have not been audited" in html or "not been audited" in html)

    # ⑤ 정규화 — published_at 미확보 상태 그대로
    live = T.normalize(parsed, published_at=None)
    months = live["months"]
    check("Jan~Jul 7개 populated month", len(months) == 7, str(sorted(months)))
    check("Aug~Dec 는 observation 으로 생성되지 않음",
          not (FUTURE_MONTHS & set(months)), str(sorted(FUTURE_MONTHS & set(months))))
    check("Jul = 467,580 / 44.7", months.get("2026-07") == EXPECT_JUL,
          str(months.get("2026-07")))
    check(f"Total = {EXPECT_TOTAL_REV} / {EXPECT_TOTAL_YOY}",
          live["cumulative"]["net_revenue_ntd_mn"] == EXPECT_TOTAL_REV
          and live["cumulative"]["cumulative_yoy_pct_published"] == EXPECT_TOTAL_YOY,
          str(live["cumulative"]))

    # ⑥ published_at 미확보 → 관측은 성공, 판정 준비는 닫힘
    check("published_at 미확보 상태로 관측 성공",
          live["published_at"] is None and len(months) == 7)
    check("decision_ready = false",
          live["decision_ready"] is False
          and live["decision_ready_blockers"] == ["published_at_unobserved"])

    # ⑦ 승인된 TSV fixture 와 의미값 동일
    ref = T.from_fixture()
    check("live months == fixture months", months == ref["months"])
    check("live cumulative == fixture cumulative",
          live["cumulative"] == ref["cumulative"])

    # ⑧ 저장소를 건드리지 않았다
    check("data/latest_tsmc_monthly.json 을 만들지 않았다",
          not os.path.exists(os.path.join(ROOT, "data", "latest_tsmc_monthly.json")))

    print(f"\n{len(ok)} PASS / {len(bad)} FAIL")
    if bad:
        print("⛔ live 결과가 승인된 관측과 다르다 — fixture 를 고치지 않는다. "
              "CIO 보고 대상이다.")
        return 1
    print("✅ live fetch 로 받은 실제 HTML 에서 extractor 가 동일 관측을 냈다")
    print("   ⛔ 단, 이것은 관측 검증이며 Rule 상태 승격도 evaluator 연결도 아니다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
