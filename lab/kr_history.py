"""Entry Validation Lab — KR 장기 이력 수집 (Comparator 입력)

역할은 하나다. **pykrx 로 유니버스의 수년치를 받아 파일로 남긴다. 분석하지 않는다.**

★ Collector / Analyzer 분리 원칙을 그대로 따른다.
  여기는 KRX 스키마 변경에만 반응하고, 판단은 rule_comparator.py 가 한다.

인증: KRX_ID / KRX_PW (collectors/krx.py 와 동일)
출력: data/kr_history.json

⛔ 여기서 재료(feature)를 만들지 않는다. 원천 수치만 저장한다.
"""
import os
import sys
import json
import datetime as dt

YEARS = int(os.getenv("LAB_YEARS", "6"))
OUT_PATH = "data/kr_history.json"

if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
    print("FATAL: KRX_ID / KRX_PW 환경변수가 없습니다.")
    sys.exit(1)

from pykrx import stock                                    # noqa: E402
from common import load_universe, today_kst, now_utc_iso   # noqa: E402


def year_chunks(start: str, end: str):
    """연 단위로 끊는다 — 한 번에 수년치를 요청하면 응답이 불안정하거나 잘린다."""
    s = dt.datetime.strptime(start, "%Y%m%d").date()
    e = dt.datetime.strptime(end, "%Y%m%d").date()
    cur = s
    while cur <= e:
        nxt = min(dt.date(cur.year, 12, 31), e)
        yield cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d")
        cur = nxt + dt.timedelta(days=1)


def fetch_stock(code: str, start: str, end: str) -> dict:
    daily, missing, chunks = {}, set(), 0

    for cs, ce in year_chunks(start, end):
        ohlcv = stock.get_market_ohlcv_by_date(cs, ce, code)
        vol = stock.get_market_trading_volume_by_date(cs, ce, code)
        chunks += 1

        for idx in ohlcv.index:
            row = {
                "close": int(ohlcv.loc[idx, "종가"]),
                "high": int(ohlcv.loc[idx, "고가"]),
                "low": int(ohlcv.loc[idx, "저가"]),
                "volume": int(ohlcv.loc[idx, "거래량"]),
            }
            if idx in vol.index:
                for want, name in (("기관합계", "inst"), ("외국인합계", "foreign")):
                    if want in vol.columns:
                        row[name] = int(vol.loc[idx, want])
                    else:
                        missing.add(want)      # 조용히 버리지 않는다
            daily[idx.strftime("%Y-%m-%d")] = row

    return {"daily": daily, "missing_columns": sorted(missing), "chunks": chunks}


def main() -> None:
    today = today_kst()
    start = (today - dt.timedelta(days=365 * YEARS + 10)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    payload = {
        "collected_at_utc": now_utc_iso(),
        "source": "KRX 정보데이터시스템 (pykrx)",
        "source_tier": "Official",
        "history_version": "kr_hist_v1",
        "years_requested": YEARS,
        "range": {"start": start, "end": end},
        "stocks": {},
    }

    ok = failed = 0
    for s in load_universe():
        code, name = s["code"], s["name"]
        try:
            row = {"name": name, "atlas_stage": s.get("atlas_stage"), "status": "ok"}
            row.update(fetch_stock(code, start, end))
            payload["stocks"][code] = row
            ok += 1
            print(f"[hist] {code} {name} — {len(row['daily'])}일"
                  + (f"  ⚠ 누락: {row['missing_columns']}" if row["missing_columns"] else ""))
        except Exception as e:                              # noqa: BLE001
            payload["stocks"][code] = {"name": name, "status": "FAILED",
                                       "error": f"{type(e).__name__}: {e}"}
            failed += 1
            print(f"[hist] FAILED {code} {name} — {type(e).__name__}: {e}")

    payload["summary"] = {"ok": ok, "failed": failed}
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"[hist] saved {OUT_PATH}  (ok={ok}, failed={failed})")

    if ok == 0:
        print("FATAL: 전 종목 수집 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
