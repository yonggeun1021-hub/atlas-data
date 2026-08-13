"""OpenDART 공시 수집

인증: 환경변수 DART_API_KEY
주의: DART는 종목코드(6자리)가 아니라 고유번호 corp_code(8자리)를 사용한다.
      config/corp_map.json 이 없으면 자동으로 내려받아 생성한다.

v2 (2026-08-13) — 종목 레벨에 Atlas 단계를 실어 보낸다 (krx.py v3과 동일 패턴)
  문제: 공시 payload만 읽으면 그 종목이 Candidate인지 Coverage인지 알 수 없었다.
        브리핑이 "어느 단계 종목의 공시인가"를 판단할 근거가 빠져 있었다 — 조용한 누락이다.
  수정: Notion `편입 사유`의 `Atlas Stage:` 태그를 종목마다 실어 보낸다.
        DB select 원본은 db_state 로 참고 보존만 하고 판정에 쓰지 않는다.
"""
import io
import os
import sys
import json
import zipfile
import datetime as dt
import xml.etree.ElementTree as ET

import requests

from common import save, load_universe, today_kst, now_utc_iso

KEY = os.getenv("DART_API_KEY")
if not KEY:
    print("FATAL: DART_API_KEY 환경변수가 없습니다.")
    sys.exit(1)

BASE = "https://opendart.fss.or.kr/api"
CORP_MAP_PATH = "config/corp_map.json"

# Atlas 증거 우선순위에 해당하는 공시만 남긴다 (전체는 노이즈)
KEYWORDS = [
    "단일판매", "공급계약",        # 1순위 — 확약 물량
    "신규시설투자",                # 1순위 — CAPEX
    "유상증자", "전환사채", "신주인수권부사채",   # 자금조달·희석
    "영업(잠정)실적", "매출액또는손익구조",       # 3순위 — 실적
]

LOOKBACK_DAYS = 7


def build_corp_map() -> dict:
    """전체 기업 고유번호 ZIP을 내려받아 {종목코드: corp_code} 생성."""
    print("[dart] corp_map 생성 중...")
    r = requests.get(f"{BASE}/corpCode.xml", params={"crtfc_key": KEY}, timeout=60)
    r.raise_for_status()
    if r.headers.get("Content-Type", "").startswith("application/json"):
        raise RuntimeError(f"corpCode 응답 오류: {r.text[:200]}")

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        xml = z.read(z.namelist()[0])

    mapping = {}
    for item in ET.fromstring(xml).iter("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            mapping[stock_code] = corp_code

    os.makedirs("config", exist_ok=True)
    with open(CORP_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False)
    print(f"[dart] corp_map {len(mapping)}건 생성")
    return mapping


def get_corp_map() -> dict:
    if os.path.exists(CORP_MAP_PATH):
        with open(CORP_MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    return build_corp_map()


def fetch(corp_code: str, days: int = LOOKBACK_DAYS) -> list:
    end = today_kst()
    start = end - dt.timedelta(days=days)
    r = requests.get(f"{BASE}/list.json", params={
        "crtfc_key": KEY,
        "corp_code": corp_code,
        "bgn_de": start.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "page_count": 100,
    }, timeout=30)
    r.raise_for_status()
    body = r.json()

    status = body.get("status")
    if status == "013":          # 조회 결과 없음 — 정상
        return []
    if status != "000":
        raise RuntimeError(f"DART status={status}: {body.get('message')}")
    return body.get("list", [])


def is_relevant(report_nm: str) -> bool:
    return any(k in report_nm for k in KEYWORDS)


def meta(s: dict) -> dict:
    """★ Atlas 단계는 Notion `편입 사유`의 Atlas Stage 태그에서 온다 (CIO 확정 2026-08-13).
    DB select 원본(db_state)은 참고 보존만 하고 판정에 쓰지 않는다."""
    return {
        "atlas_stage": s.get("atlas_stage"),
        "atlas_coverage": s.get("atlas_coverage"),
        "db_state": s.get("db_state"),
        "in_notion": s.get("in_notion"),
    }


def main() -> None:
    corp_map = get_corp_map()
    payload = {
        "collected_at_utc": now_utc_iso(),
        "collected_for_kst_date": today_kst().isoformat(),
        "source": "OpenDART (금융감독원)",
        "source_tier": "Official",
        "collector_version": "v2",
        "lookback_days": LOOKBACK_DAYS,
        "filter_keywords": KEYWORDS,
        "stocks": {},
    }

    ok = failed = 0
    for s in load_universe():
        code, name = s["code"], s["name"]
        corp_code = corp_map.get(code)
        if not corp_code:
            payload["stocks"][code] = {
                "name": name, **meta(s),
                "status": "FAILED",
                "error": "corp_code 매핑 없음",
            }
            failed += 1
            print(f"[FAILED] {code} {name} — corp_code 없음")
            continue
        try:
            items = fetch(corp_code)
            relevant = [
                {
                    "date": i.get("rcept_dt"),
                    "title": i.get("report_nm"),
                    "rcept_no": i.get("rcept_no"),
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={i.get('rcept_no')}",
                }
                for i in items if is_relevant(i.get("report_nm", ""))
            ]
            payload["stocks"][code] = {
                "name": name,
                **meta(s),
                "corp_code": corp_code,
                "status": "ok",
                "total_count": len(items),
                "relevant_count": len(relevant),
                "relevant": relevant,
            }
            ok += 1
            print(f"[ok]     {code} {name} [{s.get('atlas_stage')}] "
                  f"— 전체 {len(items)} / 관련 {len(relevant)}")
        except Exception as e:                      # noqa: BLE001
            payload["stocks"][code] = {
                "name": name, **meta(s),
                "corp_code": corp_code,
                "status": "FAILED",
                "error": f"{type(e).__name__}: {e}",
            }
            failed += 1
            print(f"[FAILED] {code} {name} — {type(e).__name__}: {e}")

    payload["summary"] = {"ok": ok, "failed": failed}
    save(payload, "dart.json")

    if ok == 0:
        print("FATAL: 모든 종목 수집 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
