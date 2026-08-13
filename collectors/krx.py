"""KRX 투자자별 수급 + OHLCV 수집 (pykrx)

원천: KRX 정보데이터시스템 (data.krx.co.kr)
인증: 환경변수 KRX_ID / KRX_PW  (pykrx 1.2.8+ 요구사항)

v3.1 (2026-08-13) — Stage 와 Coverage 를 분리한다 (CIO 확정)
  `atlas_stage: "Coverage"` 는 쓰지 않는다. Coverage 는 단계가 아니라 추적 대상 여부다.
  → `{"atlas_stage": null, "coverage": true}` / `{"atlas_stage": "Candidate", "coverage": true}`

v3 (2026-08-13) — 종목 레벨에 Atlas 단계를 실어 보낸다
  문제: 종목 payload에 `stage: null`만 있어, 브리핑이 latest_krx.json만 읽으면
        효성중공업이 Candidate인지 알 수 없었다. 조용한 누락이다.
  수정: Notion `편입 사유`의 `Atlas Stage:` 태그를 종목마다 실어 보낸다.
        DB select 원본은 db_state 로 참고 보존만 하고 판정에 쓰지 않는다.

v2 (2026-08-13) — 수정 2건
  ① 외국인 컬럼 누락 수정 — 기본 조회 컬럼명은 '외국인합계'이지 '외국인'이 아니다.
  ② 조용한 누락 금지 — 요청한 구분이 응답에 없으면 missing_investors 에 명시한다.
"""
import os
import sys
import datetime as dt

from common import (save, load_universe, today_kst, now_utc_iso,
                    record_stage_snapshot, stage_distribution)

if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
    print("FATAL: KRX_ID / KRX_PW 환경변수가 없습니다. GitHub Secrets를 확인하세요.")
    sys.exit(1)

from pykrx import stock  # noqa: E402

# 기본 조회(detail=False) 컬럼 — 판정에 쓰는 공식 집계
BASIC = ["기관합계", "외국인합계", "개인", "기타법인"]

# 상세 조회(detail=True) 컬럼 — 참고용 세부 주체
DETAIL = [
    "금융투자", "보험", "투신", "사모", "은행", "기타금융",
    "연기금", "기타법인", "개인", "외국인", "기타외국인",
]

LOOKBACK_DAYS = 40  # 20일선 계산용 영업일 확보


def pick(row, columns, wanted):
    """요청한 구분 중 응답에 없는 것을 조용히 버리지 않는다."""
    got = {k: int(row[k]) for k in wanted if k in columns}
    missing = [k for k in wanted if k not in columns]
    return got, missing


def collect_one(code: str, start: str, end: str) -> dict:
    val = stock.get_market_trading_value_by_date(start, end, code)
    vol = stock.get_market_trading_volume_by_date(start, end, code)
    val_d = stock.get_market_trading_value_by_date(start, end, code, detail=True)
    vol_d = stock.get_market_trading_volume_by_date(start, end, code, detail=True)
    ohlcv = stock.get_market_ohlcv_by_date(start, end, code)

    missing_all = set()
    daily = {}

    for idx in ohlcv.index:
        key = idx.strftime("%Y-%m-%d")
        entry = {
            "close": int(ohlcv.loc[idx, "종가"]),
            "open": int(ohlcv.loc[idx, "시가"]),
            "high": int(ohlcv.loc[idx, "고가"]),
            "low": int(ohlcv.loc[idx, "저가"]),
            "volume": int(ohlcv.loc[idx, "거래량"]),
            "change_pct": float(ohlcv.loc[idx, "등락률"]),
        }

        for src_df, wanted, name in (
            (val,   BASIC,  "net_value"),
            (vol,   BASIC,  "net_volume"),
            (val_d, DETAIL, "net_value_detail"),
            (vol_d, DETAIL, "net_volume_detail"),
        ):
            if idx in src_df.index:
                got, missing = pick(src_df.loc[idx], src_df.columns, wanted)
                entry[name] = got
                missing_all.update(missing)

        daily[key] = entry

    closes = [v["close"] for v in daily.values()]
    sma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else None

    return {
        "daily": daily,
        "latest_trading_day": max(daily) if daily else None,
        "sma20": sma20,                       # 20개 미만이면 None — 추정하지 않는다
        "sma20_basis": len(closes[-20:]),
        "missing_investors": sorted(missing_all),   # 비어 있어야 정상
    }


def meta(s: dict) -> dict:
    """★ Atlas 단계는 Notion `편입 사유`의 Atlas Stage 태그에서 온다 (CIO 확정 2026-08-13).
    Stage 와 Coverage 는 서로 다른 축이다 — Coverage 는 Stage 값이 아니다.
      atlas_stage : Discovery / Candidate / Ready / Buy / Holding / Closed / None
      coverage    : true / false / None(Unknown)
    DB select 원본(db_state)은 참고 보존만 하고 판정에 쓰지 않는다."""
    return {
        "atlas_stage": s.get("atlas_stage"),
        "coverage": s.get("coverage"),
        "db_state": s.get("db_state"),
        "in_notion": s.get("in_notion"),
    }


def main() -> None:
    today = today_kst()
    start = (today - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    payload = {
        "collected_at_utc": now_utc_iso(),
        "collected_for_kst_date": today.isoformat(),
        "source": "KRX 정보데이터시스템 (pykrx)",
        "source_tier": "Official",
        "collector_version": "v3.3",
        "range": {"start": start, "end": end},
        "stocks": {},
    }

    universe = load_universe()
    record_stage_snapshot(universe, today)   # 단계 이력은 수집 성패와 무관하게 먼저 남긴다
    payload["stage_distribution"] = stage_distribution(universe, today)

    ok = failed = 0
    for s in universe:
        code, name = s["code"], s["name"]
        try:
            payload["stocks"][code] = {
                "name": name,
                **meta(s),
                "status": "ok",
                **collect_one(code, start, end),
            }
            ok += 1
            miss = payload["stocks"][code]["missing_investors"]
            print(f"[ok]     {code} {name} "
                  f"[stage={s.get('atlas_stage')} coverage={s.get('coverage')}]"
                  + (f"  ⚠ 누락: {miss}" if miss else ""))
        except Exception as e:                      # noqa: BLE001
            payload["stocks"][code] = {
                "name": name,
                **meta(s),
                "status": "FAILED",
                "error": f"{type(e).__name__}: {e}",
            }
            failed += 1
            print(f"[FAILED] {code} {name} — {type(e).__name__}: {e}")

    payload["summary"] = {"ok": ok, "failed": failed}
    save(payload, "krx.json", today)

    if ok == 0:
        print("FATAL: 모든 종목 수집 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
