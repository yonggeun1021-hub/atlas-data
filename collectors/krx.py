"""KRX 투자자별 수급 + OHLCV 수집 (pykrx)

원천: KRX 정보데이터시스템 (data.krx.co.kr)
인증: 환경변수 KRX_ID / KRX_PW  (pykrx 1.2.8+ 요구사항)
"""
import os
import sys
import datetime as dt

from common import save, load_universe, today_kst, now_utc_iso

if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
    print("FATAL: KRX_ID / KRX_PW 환경변수가 없습니다. GitHub Secrets를 확인하세요.")
    sys.exit(1)

from pykrx import stock  # noqa: E402  (로그인 환경변수 확인 후 import)

# 수집 대상 투자자 구분
INVESTORS = ["기관합계", "외국인", "개인", "연기금", "금융투자", "투신", "기타법인"]

LOOKBACK_DAYS = 40  # 20일선 계산에 필요한 영업일 확보용


def pick(row, columns) -> dict:
    return {k: int(row[k]) for k in INVESTORS if k in columns}


def collect_one(code: str, start: str, end: str) -> dict:
    value = stock.get_market_trading_value_by_date(start, end, code)
    volume = stock.get_market_trading_volume_by_date(start, end, code)
    ohlcv = stock.get_market_ohlcv_by_date(start, end, code)

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
        if idx in value.index:
            entry["net_value"] = pick(value.loc[idx], value.columns)
        if idx in volume.index:
            entry["net_volume"] = pick(volume.loc[idx], volume.columns)
        daily[key] = entry

    closes = [v["close"] for v in daily.values()]
    sma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else None

    return {
        "daily": daily,
        "latest_trading_day": max(daily) if daily else None,
        "sma20": sma20,          # 20개 미만이면 None — 추정하지 않는다
        "sma20_basis": len(closes[-20:]),
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
        "range": {"start": start, "end": end},
        "stocks": {},
    }

    ok = failed = 0
    for s in load_universe():
        code, name = s["code"], s["name"]
        try:
            payload["stocks"][code] = {
                "name": name,
                "stage": s.get("stage"),
                "status": "ok",
                **collect_one(code, start, end),
            }
            ok += 1
            print(f"[ok]     {code} {name}")
        except Exception as e:                      # noqa: BLE001
            payload["stocks"][code] = {
                "name": name,
                "stage": s.get("stage"),
                "status": "FAILED",
                "error": f"{type(e).__name__}: {e}",
            }
            failed += 1
            print(f"[FAILED] {code} {name} — {type(e).__name__}: {e}")

    payload["summary"] = {"ok": ok, "failed": failed}
    save(payload, "krx.json", today)

    # 전 종목 실패 = 수집 자체가 안 된 것이므로 워크플로를 실패로 표시한다.
    if ok == 0:
        print("FATAL: 모든 종목 수집 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
