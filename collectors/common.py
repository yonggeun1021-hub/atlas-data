"""Atlas Data Server — 공통 유틸

v7 (2026-08-13) — 단계 이력을 누적한다 (Pipeline 전환율 KPI의 재료)
  전환율은 '현재 분포'가 아니라 '상태 변화'다. 변화를 보려면 어제가 있어야 한다.
  → data/stage_history.json 에 하루 한 장씩 스냅샷을 쌓는다.
  ⛔ 전환율 자체는 계산하지 않는다 — 정의가 Undefined 이고, 정의를 현장에서 만들지 않는다.

v6 (2026-08-13) — Stage 와 Coverage 를 분리한다 (CIO 확정)
  Coverage 는 투자 단계가 아니라 '계속 추적하는 대상'이라는 성격이다.
  Notion 은 Freeze 대상이라 태그 표기(`Atlas Stage: Coverage`)를 그대로 두고,
  Data Server 내부 JSON 에서만 `coverage: true` / `atlas_stage: null` 로 변환한다.
  Stage 어휘는 Discovery / Candidate / Ready / Buy / Holding / Closed 6개뿐이다.

v5 (2026-08-13) — Atlas 단계를 DB select가 아닌 `편입 사유` 태그에서 읽는다 (논리·저장소 분리)

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
STAGE_HISTORY = "data/stage_history.json"

# 비워두면 search로 자동 탐색한다. 특정 DB를 강제하려면 환경변수로 지정.
NOTION_DB_ID = os.getenv("NOTION_WATCHLIST_DB", "").strip()
NOTION_DB_TITLE = os.getenv("NOTION_WATCHLIST_TITLE", "PM Watchlist").strip()
NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"

KR_TICKER = re.compile(r"^\d{6}$")

# Atlas 단계는 DB select가 아니라 `편입 사유` 텍스트 태그로 관리한다 (CIO 확정 2026-08-13)
#   Atlas Stage: Candidate
#   Atlas Coverage: Y
ATLAS_STAGE_TAG = re.compile(r"Atlas\s+Stage\s*[:：]\s*([A-Za-z가-힣]+)")
ATLAS_COVERAGE_TAG = re.compile(r"Atlas\s+Coverage\s*[:：]\s*([A-Za-z가-힣]+)")

# ★ CIO 확정 2026-08-13 (v6) — Stage 와 Coverage 를 분리한다.
#   Stage 는 '투자 단계'다. 아래 6개만 Stage 다.
#   Coverage 는 '사업을 계속 추적하는 대상'이라는 성격이며 Stage 가 아니다.
#   따라서 JSON 내부 표현은 coverage(bool) 와 atlas_stage(단계 or None) 를 따로 둔다.
#   Notion 은 Freeze 대상이므로 그대로 두고, 변환은 여기(Data Server 내부)에서만 한다.
VALID_STAGES = ("Discovery", "Candidate", "Ready", "Buy", "Holding", "Closed")
_STAGE_LOOKUP = {s.lower(): s for s in VALID_STAGES}

_TRUEY = {"y", "yes", "true", "1", "o", "예"}
_FALSEY = {"n", "no", "false", "0", "x", "아니오"}

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

    universe, skipped, untagged = [], [], []
    invalid_tags, coverage_unknown = [], []
    for row in rows:
        props = row.get("properties", {})
        name = _plain(props.get("종목")) or _plain(props.get("Name"))
        ticker = _plain(props.get("티커")) or _plain(props.get("Ticker"))
        db_state = (props.get("상태") or {}).get("select") or {}
        notes = _plain(props.get("편입 사유")) or _plain(props.get("Notes"))

        # ★ CIO 확정 2026-08-13 — 논리와 저장소를 분리한다.
        #   DB의 '상태' select 는 Freeze로 건드리지 않는다(관찰·진입대기·Ready·파일럿·보유 유지).
        #   Atlas 단계(Discovery/Candidate/Ready/Buy/Holding/Closed)는
        #   기존 '편입 사유' 필드의 `Atlas Stage:` 태그로만 관리한다.
        #   ⛔ DB 상태 → Atlas 단계 매핑 금지. 태그가 없으면 만들어내지 않고 None으로 남긴다.
        stage_m = ATLAS_STAGE_TAG.search(notes)
        cov_m = ATLAS_COVERAGE_TAG.search(notes)
        stage_raw = stage_m.group(1).strip() if stage_m else None
        cov_raw = cov_m.group(1).strip() if cov_m else None

        atlas_stage = None      # 투자 단계 — 6개 중 하나, 없으면 None
        coverage = None         # 추적 대상 여부 — bool, 모르면 None(Unknown)

        if stage_raw is None:
            untagged.append({"name": name, "ticker": ticker,
                             "reason": "편입 사유에 `Atlas Stage:` 태그 없음 — 단계 Unknown"})
        elif stage_raw.lower() == "coverage":
            # ★ Coverage 는 Stage 가 아니다 → 단계는 비우고 추적 대상으로만 표시한다.
            coverage = True
        elif stage_raw.lower() in _STAGE_LOOKUP:
            atlas_stage = _STAGE_LOOKUP[stage_raw.lower()]
        else:
            # 정의되지 않은 값은 조용히 버리지 않는다 — 만들어내지도 않는다.
            invalid_tags.append({"name": name, "ticker": ticker, "tag": stage_raw,
                                 "reason": f"Stage 어휘 밖의 값. 허용: {list(VALID_STAGES)}"})

        if cov_raw is not None:                     # 명시 태그가 있으면 그것이 우선
            lc = cov_raw.lower()
            if lc in _TRUEY:
                coverage = True
            elif lc in _FALSEY:
                coverage = False
            else:
                invalid_tags.append({"name": name, "ticker": ticker, "tag": cov_raw,
                                     "reason": "Coverage 태그를 Y/N 으로 해석할 수 없음"})

        if coverage is None:
            coverage_unknown.append({"name": name, "ticker": ticker,
                                     "reason": "Coverage 태그 없음 — 추론하지 않고 Unknown 으로 둔다"})

        m = re.search(r"\d{6}", ticker)
        if m and KR_TICKER.match(m.group(0)):
            universe.append({
                "code": m.group(0),
                "name": name or m.group(0),
                "atlas_stage": atlas_stage,          # 투자 단계 (정본) — Coverage 는 여기 들어가지 않는다
                "coverage": coverage,                # 추적 대상 여부 (Stage 밖의 성격)
                "db_state": db_state.get("name"),    # DB select 원본 — 참고용, 판정에 쓰지 않는다
            })
        else:
            skipped.append({"name": name, "ticker": ticker,
                            "atlas_stage": atlas_stage,
                            "coverage": coverage,
                            "reason": "한국 6자리 코드 아님(미국 종목 자동수집은 Unimplemented)"})

    universe_meta["notion_rows"] = len(rows)
    universe_meta["notion_skipped"] = skipped
    universe_meta["notion_untagged"] = untagged   # ★ 태그 누락은 조용히 넘기지 않는다
    universe_meta["notion_invalid_tags"] = invalid_tags
    universe_meta["notion_coverage_unknown"] = coverage_unknown
    universe_meta["stage_vocabulary"] = list(VALID_STAGES)
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
                merged.append({**s, "atlas_stage": None, "coverage": None,
                               "db_state": None})

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
        if universe_meta.get("notion_untagged"):
            print(f"[universe] ⚠ Atlas Stage 태그 없음: "
                  f"{[s['ticker'] for s in universe_meta['notion_untagged']]}")
        if universe_meta.get("notion_invalid_tags"):
            print(f"[universe] ⚠ 해석 불가 태그: {universe_meta['notion_invalid_tags']}")
        if universe_meta.get("notion_coverage_unknown"):
            print(f"[universe] ⚠ Coverage Unknown: "
                  f"{[s['ticker'] for s in universe_meta['notion_coverage_unknown']]}")
        stages = {s["code"]: (s.get("atlas_stage"), s.get("coverage")) for s in merged}
        print(f"[universe] (stage, coverage): {stages}")
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

def _all_rows(universe: list) -> dict:
    """Atlas Universe 전체(수집 대상 + 미수집 미국 종목)를 한 장으로 모은다."""
    rows: dict = {}
    for s in universe:                      # 수집 대상(한국)
        rows[s["code"]] = {
            "name": s.get("name"),
            "stage": s.get("atlas_stage"),
            "coverage": s.get("coverage"),
            "collected": True,
        }
    for s in universe_meta.get("notion_skipped", []):   # 미수집(미국) — 단계는 살아 있다
        key = s.get("ticker") or s.get("name")
        if key:
            rows[key] = {
                "name": s.get("name"),
                "stage": s.get("atlas_stage"),
                "coverage": s.get("coverage"),
                "collected": False,          # Unimplemented — Unknown이 아니다
            }
    return rows


def stage_distribution(universe: list, date: dt.date | None = None) -> dict:
    """CIO 확정 KPI (2026-08-13) — 보는 것은 '분포'이지 '전환율'이 아니다.

    ⛔ Stage Conversion Rate 는 계산하지 않는다. 정의(분모·기간·재진입)는 Review #3.
       세는 일을 사람이 하지 않게 여기서 계산해 실어 보낸다 —
       오늘 Discovery 를 1이 아닌 2로 센 사례가 있었고, 그 종류의 오차를 없앤다.
    """
    rows = _all_rows(universe)
    by_stage: dict = {}
    for r in rows.values():
        key = r["stage"] or "미부여"
        by_stage[key] = by_stage.get(key, 0) + 1

    return {
        "as_of": (date or today_kst()).isoformat(),
        "metric": "Stage Distribution (분포)",
        "not_a_conversion_rate": ("각 종목은 정확히 한 단계에만 있다. "
                                  "이 숫자는 상태의 분포이며 상태의 변화가 아니다."),
        "coverage_total": sum(1 for r in rows.values() if r["coverage"] is True),
        "stage_assigned": sum(1 for r in rows.values() if r["stage"]),
        "buy": by_stage.get("Buy", 0),
        "by_stage": by_stage,
        "universe_total": len(rows),
        "conversion_rate": None,
        "conversion_rate_status": "Undefined — 분모·기간·재진입 정의는 Review #3 (2026-08-15)",
    }


def record_stage_snapshot(universe: list, date: dt.date | None = None) -> str:
    """Atlas Universe 전체의 단계를 하루 한 장씩 누적한다 (v7, CIO 지시 2026-08-13).

    ★ 왜 이력이 필요한가
      Pipeline 전환율은 '지금 몇 개가 어느 단계에 있는가'(분포)로는 계산할 수 없다.
      전환율은 '어제 Discovery였던 것 중 몇 개가 오늘 Candidate가 되었는가'(변화)다.
      변화를 보려면 어제와 오늘이 둘 다 있어야 한다 — 그래서 오늘부터 남긴다.

    ⛔ 여기서 전환율을 계산하지는 않는다. 전환율의 정의(분모·기간·재진입 처리)는
       아직 Undefined 이며, 정의를 현장에서 만들어내는 것은 금지된 행동이다.
       이 함수는 '정의가 정해졌을 때 계산할 수 있는 재료'만 쌓는다.
    """
    date = date or today_kst()
    rows = _all_rows(universe)

    hist: dict = {}
    if os.path.exists(STAGE_HISTORY):
        try:
            with open(STAGE_HISTORY, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception as e:               # noqa: BLE001
            print(f"[stage_history] ⚠ 기존 이력을 읽지 못했다: {type(e).__name__}: {e}")
            print("[stage_history]   덮어쓰지 않고 중단한다 — 이력 손실이 더 큰 손해다.")
            return ""

    hist[date.isoformat()] = rows

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STAGE_HISTORY, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2, sort_keys=True)

    dist: dict = {}
    for r in rows.values():
        dist[r["stage"]] = dist.get(r["stage"], 0) + 1
    print(f"[stage_history] {date} {len(rows)}종목 기록 · 단계 분포(전환율 아님): {dist}")
    print(f"[stage_history] 누적 {len(hist)}일")
    return STAGE_HISTORY


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


def save_incident(payload: dict, filename: str, date: dt.date | None = None) -> str:
    """전멸(ok==0) 산출물을 격리 경로에만 남긴다. latest_<stem>.json 은 건드리지 않는다."""
    date = date or today_kst()
    payload["universe_meta"] = dict(universe_meta)
    outdir = os.path.join(DATA_DIR, "incident")
    os.makedirs(outdir, exist_ok=True)
    stem = os.path.splitext(filename)[0]
    path = os.path.join(outdir, f"{date.isoformat()}_{stem}_failed.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    print(f"[incident] {path}")
    print(f"[incident] 정본 latest_{stem}.json 은 덮지 않았습니다 — 다음 슬롯이 재시도합니다")
    return path
