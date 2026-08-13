"""Atlas Data Server — 공통 유틸
 
v2 (2026-08-13) — 종목 목록 SSOT를 Notion PM Watchlist로 이관
 
  왜: config/universe.json 이 "Atlas가 추적하는 종목"의 두 번째 저장소가 되어
      Notion 정본과 어긋날 수 있었다. 어긋나면 그 종목 수급이 조용히 빠진다.
      Incident #6(상태가 두 곳에 있던 문제)과 같은 계통이므로 저장소를 하나로 만든다.
 
  우선순위:
    1) Notion PM Watchlist  (NOTION_TOKEN 이 있을 때)   ← SSOT
    2) config/universe.json (Notion 실패 시)            ← Fallback
  두 곳이 다르면 조용히 넘기지 않고 universe_divergence 로 보고한다.
 
★ 설계 원칙 (Atlas Operating Discipline 구현)
   수집 실패를 빈 값·직전 값·추정치로 대체하지 않는다.
   실패는 status="FAILED" / 원문 에러로 남긴다.
"""
import os
import re
import json
import datetime as dt
from zoneinfo import ZoneInfo
 
import requests
 
KST = ZoneInfo("Asia/Seoul")
DATA_DIR = "data"
UNIVERSE_FILE = "config/universe.json"
 
# Notion PM Watchlist 데이터베이스
NOTION_DB_ID = os.getenv("NOTION_WATCHLIST_DB", "024702bd-3978-48bd-b789-297c251684c2")
NOTION_VERSION = "2022-06-28"
 
KR_TICKER = re.compile(r"^\d{6}$")
 
# 이 실행에서 종목 목록을 어디서 읽었는지 (수집기가 산출물에 기록)
universe_meta: dict = {}
 
 
def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()
 
 
def now_utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
 
 
# ────────────────────────────────────────────────────────────
# 종목 목록
# ────────────────────────────────────────────────────────────
 
def _plain(prop: dict) -> str:
    """Notion rich_text / title 을 평문으로."""
    if not prop:
        return ""
    items = prop.get("rich_text") or prop.get("title") or []
    return "".join(i.get("plain_text", "") for i in items).strip()
 
 
def _from_notion() -> list:
    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise RuntimeError("NOTION_TOKEN 미설정")
 
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
 
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
            headers=headers, json=body, timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Notion {r.status_code}: {r.text[:300]}")
        j = r.json()
        rows.extend(j.get("results", []))
        if not j.get("has_more"):
            break
        cursor = j.get("next_cursor")
 
    universe, skipped = [], []
    for row in rows:
        props = row.get("properties", {})
        name = _plain(props.get("종목"))
        ticker = _plain(props.get("티커"))
        state = (props.get("상태") or {}).get("select") or {}
 
        # 티커 칸에 'A005930' 'KRX:005930' 같은 표기가 섞여도 6자리 숫자만 뽑는다
        m = re.search(r"\d{6}", ticker)
        if m and KR_TICKER.match(m.group(0)):
            universe.append({
                "code": m.group(0),
                "name": name or m.group(0),
                "notion_state": state.get("name"),
            })
        else:
            # 미국 종목 등 — 조용히 버리지 않고 남긴다
            skipped.append({"name": name, "ticker": ticker,
                            "reason": "한국 6자리 코드 아님(미국 종목 자동수집은 Unimplemented)"})
 
    universe_meta["notion_rows"] = len(rows)
    universe_meta["notion_skipped"] = skipped
    return universe
 
 
def _from_file() -> list:
    with open(UNIVERSE_FILE, encoding="utf-8") as f:
        return json.load(f)["kr"]
 
 
def load_universe() -> list:
    """종목 목록을 Notion 정본에서 읽고, 실패 시 파일로 폴백한다."""
    file_list, file_err = [], None
    try:
        file_list = _from_file()
    except Exception as e:                          # noqa: BLE001
        file_err = f"{type(e).__name__}: {e}"
 
    try:
        notion_list = _from_notion()
        universe_meta.update({
            "source": "notion",
            "source_tier": "SSOT",
            "notion_db": NOTION_DB_ID,
            "count": len(notion_list),
            "file_error": file_err,
        })
 
        # 두 목록 비교 — 다르면 조용히 넘기지 않는다
        n = {s["code"] for s in notion_list}
        f = {s["code"] for s in file_list}
        if n != f:
            universe_meta["universe_divergence"] = {
                "notion_only": sorted(n - f),
                "file_only": sorted(f - n),
                "note": "Notion을 채택함. config/universe.json 은 폴백용이며 정본이 아니다.",
            }
        print(f"[universe] Notion PM Watchlist {len(notion_list)}종목")
        if universe_meta.get("universe_divergence"):
            print(f"[universe] ⚠ 파일과 불일치: {universe_meta['universe_divergence']}")
        if universe_meta.get("notion_skipped"):
            print(f"[universe] ⚠ 제외(한국 코드 아님): {universe_meta['notion_skipped']}")
        return notion_list
 
    except Exception as e:                          # noqa: BLE001
        universe_meta.update({
            "source": "file_fallback",
            "source_tier": "Fallback",
            "notion_error": f"{type(e).__name__}: {e}",
            "count": len(file_list),
        })
        print(f"[universe] ⚠ Notion 실패 → 파일 폴백 ({len(file_list)}종목)")
        print(f"[universe]   사유: {type(e).__name__}: {e}")
        if not file_list:
            raise RuntimeError(f"Notion·파일 모두 실패. Notion: {e} / 파일: {file_err}") from e
        return file_list
 
 
# ────────────────────────────────────────────────────────────
# 저장
# ────────────────────────────────────────────────────────────
 
def save(payload: dict, filename: str, date: dt.date | None = None) -> str:
    """data/<YYYY-MM-DD>/<filename> 과 data/latest_<stem>.json 두 곳에 저장."""
    date = date or today_kst()
    payload["universe_meta"] = dict(universe_meta)   # 어디서 종목을 읽었는지 항상 남긴다
 
    outdir = os.path.join(DATA_DIR, date.isoformat())
    os.makedirs(outdir, exist_ok=True)
 
    path = os.path.join(outdir, filename)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
 
    stem = os.path.splitext(filename)[0]
    latest = os.path.join(DATA_DIR, f"latest_{stem}.json")
    with open(latest, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
 
    print(f"[saved] {path}")
    print(f"[saved] {latest}")
    return path
