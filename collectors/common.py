"""Atlas Data Server — 공통 유틸

★ 설계 원칙 (Atlas Operating Discipline 구현)
   수집 실패를 빈 값·직전 값·추정치로 대체하지 않는다.
   실패는 status="FAILED"와 에러 원문으로 그대로 남긴다.
   그래야 브리핑이 그것을 Unknown으로 읽고 판정을 연기할 수 있다.
"""
import os
import json
import datetime as dt
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DATA_DIR = "data"


def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


def now_utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def save(payload: dict, filename: str, date: dt.date | None = None) -> str:
    """data/<YYYY-MM-DD>/<filename> 과 data/latest_<stem>.json 두 곳에 저장."""
    date = date or today_kst()
    outdir = os.path.join(DATA_DIR, date.isoformat())
    os.makedirs(outdir, exist_ok=True)

    path = os.path.join(outdir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    stem = os.path.splitext(filename)[0]
    latest = os.path.join(DATA_DIR, f"latest_{stem}.json")
    with open(latest, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[saved] {path}")
    print(f"[saved] {latest}")
    return path


def load_universe() -> list:
    with open("config/universe.json", encoding="utf-8") as f:
        return json.load(f)["kr"]
