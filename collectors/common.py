"""Atlas Data Server — 공통 유틸
 
v4 (2026-08-13) — Notion을 판정 정본으로 삼되 수집은 Notion∪파일 합집합
 
  v2 문제: DB ID를 고정해 두었더니 404가 났고, Notion의 404는
           "ID가 틀림"과 "공유 안 됨"을 구분해 주지 않는다.
  v3 해결: /v1/search 로 통합에 공유된 데이터베이스를 나열해 이름으로 찾는다.
           search 결과는 "이 통합이 실제로 볼 수 있는 것"이므로
           실패 시 무엇이 보이는지까지 그대로 보고한다 → 원인이 즉시 드러난다.
 
  우선순위:
    1) Notion PM Watchlist  (NOTION_TOKEN 이 있고 공유돼 있을 때)   ← SSOT
    2) config/universe.json (Notion 실패 시)                       ← Fallback
  두 곳이 다르면 조용히 넘기지 않고 universe_divergence 로 보고한다.
 
★ 설계 원칙 — 실패를 빈 값·직전 값·추정치로 대체하지 않는다.
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
 
# 비워두면 search로 자동 탐색한다. 특정 DB를 강제하려면 환경변수로 지정.
NOTION_DB_ID = os.getenv("NOTION_WATCHLIST_DB", "").strip()
NOTION_DB_TITLE = os.getenv("NOTION_WATCHLIST_TITLE", "PM Watchlist").strip()
NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"
 
KR_TICKER = re.compile(r"^\d{6}$")
 
universe_meta: dict = {}
 
 
def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()
 
 
def now_utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
 
 
# ────────────────────────────────────────────────────────────
# Notion
# ────────────────────────────────────────────────────────────
 
def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
 
 
def _plain(prop: dict) -> str:
    if not prop:
        return ""
    items = prop.get("rich_text") or prop.get("title") or []
    return "".join(i.get("plain_text", "") for i in items).strip()
 
 
def _db_title(db: dict) -> str:
    return "".join(t.get("plain_text", "") for t in db.get("title", [])).strip()
 
 
def _find_db() -> str:
    """통합에 공유된 데이터베이스 중 이름이 맞는 것을 찾는다."""
    if NOTION_DB_ID:
        universe_meta["notion_db_source"] = "env"
        return NOTION_DB_ID
 
    r = requests.post(f"{API}/search", headers=_headers(), timeout=30, json={
        "filter": {"property": "object", "value": "database"},
        "page_size": 100,
    })
    if r.status_code != 200:
        raise RuntimeError(f"Notion search {r.status_code}: {r.text[:300]}")
 
    dbs = r.json().get("results", [])
    visible = [{"id": d["id"], "title": _db_title(d)} for d in dbs]
    universe_meta["notion_visible_databases"] = visible   # ★ 진단용 — 항상 남긴다
 
    if not visible:
        raise RuntimeError(
            "통합에 공유된 데이터베이스가 0개다. "
            "Notion에서 PM Watchlist를 열고 ··· → 연결 → atlas-data 를 추가해야 한다."
        )
 
    for d in visible:                       # 정확히 일치
        if d["title"] == NOTION_DB_TITLE:
            universe_meta["notion_db_source"] = "search_exact"
            return d["id"]
    for d in visible:                       # 부분 일치
        if NOTION_DB_TITLE.lower() in d["title"].lower() or "watchlist" in d["title"].lower():
            universe_meta["notion_db_source"] = "search_partial"
            universe_meta["notion_db_title"] = d["title"]
            return d["id"]
 
    raise RuntimeError(
        f"'{NOTION_DB_TITLE}' 를 찾지 못했다. 통합이 볼 수 있는 DB: "
        f"{[d['title'] for d in visible]}"
    )
 
 
def _from_notion() -> list:
    if not os.getenv("NOTION_TOKEN"):
        raise RuntimeError("NOTION_TOKEN 미설정")
 
    db_id = _find_db()
    universe_meta["notion_db"] = db_id
 
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(f"{API}/databases/{db_id}/query",
                          headers=_headers(), json=body, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Notion query {r.status_code}: {r.text[:300]}")
        j = r.json()
        rows.extend(j.get("results", []))
        if not j.get("has_more"):
            break
        cursor = j.get("next_cursor")
 
    universe, skipped = [], []
    for row in rows:
        props = row.get("properties", {})
        name = _plain(props.get("종목")) or _plain(props.get("Name"))
        ticker = _plain(props.get("티커")) or _plain(props.get("Ticker"))
        state = (props.get("상태") or {}).get("select") or {}
 
        m = re.search(r"\d{6}", ticker)
        if m and KR_TICKER.match(m.group(0)):
            universe.append({
                "code": m.group(0),
                "name": name or m.group(0),
                "notion_state": state.get("name"),
            })
        else:
            skipped.append({"name": name, "ticker": ticker,
                            "reason": "한국 6자리 코드 아님(미국 종목 자동수집은 Unimplemented)"})
 
    universe_meta["notion_rows"] = len(rows)
    universe_meta["notion_skipped"] = skipped
    if not universe:
        raise RuntimeError(f"DB는 읽었으나 한국 종목 0건 (전체 {len(rows)}행). '티커' 칸 확인 필요")
    return universe
 
 
# ────────────────────────────────────────────────────────────
# 종목 목록
# ────────────────────────────────────────────────────────────
 
def _from_file() -> list:
    with open(UNIVERSE_FILE, encoding="utf-8") as f:
        return json.load(f)["kr"]
 
 
def load_universe() -> list:
    file_list, file_err = [], None
    try:
        file_list = _from_file()
    except Exception as e:                          # noqa: BLE001
        file_err = f"{type(e).__name__}: {e}"
 
    try:
        notion_list = _from_notion()
        n = {s["code"] for s in notion_list}
        f = {s["code"] for s in file_list}
 
        # ★ 수집은 합집합(union)으로 한다 — 판정 정본은 Notion이지만 데이터는 넓게 확보한다.
        #   빠뜨리면 그날 판정 자체가 불가능해지고 되돌릴 수 없다.
        #   더 수집하는 것은 비용이 거의 없고 해가 없다. (종목당 API 5콜)
        #   불일치는 숨기지 않고 universe_divergence 로 계속 표면화한다.
        merged = list(notion_list)
        for s in file_list:
            if s["code"] not in n:
                merged.append({**s, "notion_state": None})
 
        for s in merged:
            s["in_notion"] = s["code"] in n
            s["in_file"] = s["code"] in f
 
        universe_meta.update({
            "source": "notion+file_union",
            "source_tier": "SSOT(Notion) + 수집 안전망(file)",
            "decision_ssot": "notion",
            "count": len(merged),
            "count_notion": len(n),
            "count_file_only": len(f - n),
            "file_error": file_err,
        })
        if n != f:
            universe_meta["universe_divergence"] = {
                "notion_only": sorted(n - f),
                "file_only": sorted(f - n),
                "note": ("판정 정본은 Notion PM Watchlist다. file_only 종목은 정본에 없으므로 "
                         "수집은 하되 '정본 미등재' 상태로 표시한다 — CIO 정리 대상."),
            }
        print(f"[universe] Notion {len(n)}종목 + 파일전용 {len(f - n)}종목 = 합계 {len(merged)}")
        if universe_meta.get("universe_divergence"):
            print(f"[universe] ⚠ 불일치: {universe_meta['universe_divergence']}")
        if universe_meta.get("notion_skipped"):
            print(f"[universe] ⚠ 제외(한국 코드 아님): "
                  f"{[s['ticker'] for s in universe_meta['notion_skipped']]}")
        return merged
 
    except Exception as e:                          # noqa: BLE001
        universe_meta.update({
            "source": "file_fallback",
            "source_tier": "Fallback",
            "notion_error": f"{type(e).__name__}: {e}",
            "count": len(file_list),
        })
        print(f"[universe] ⚠ Notion 실패 → 파일 폴백 ({len(file_list)}종목)")
        print(f"[universe]   사유: {type(e).__name__}: {e}")
        if universe_meta.get("notion_visible_databases") is not None:
            print(f"[universe]   통합이 볼 수 있는 DB: "
                  f"{[d['title'] for d in universe_meta['notion_visible_databases']]}")
        if not file_list:
            raise RuntimeError(f"Notion·파일 모두 실패. Notion: {e} / 파일: {file_err}") from e
        return file_list
 
 
# ────────────────────────────────────────────────────────────
# 저장
# ────────────────────────────────────────────────────────────
 
def save(payload: dict, filename: str, date: dt.date | None = None) -> str:
    date = date or today_kst()
    payload["universe_meta"] = dict(universe_meta)
 
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
