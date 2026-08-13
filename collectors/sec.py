"""SEC EDGAR 수집 — 미국 종목 (Collector Layer only)

원천: SEC `data.sec.gov` 조회 API + `www.sec.gov/files/company_tickers.json`
인증: **API Key 없음.** 환경변수 `SEC_USER_AGENT` 만 요구한다.
      (SEC 는 연락 가능한 User-Agent 를 요구한다. 예: "Atlas Research yonggeun1021@gmail.com")
      ⚠ EDGAR Next API 는 공시를 *제출* 하기 위한 별개 시스템이며 토큰이 필요하다. 여기서 쓰지 않는다.

★★ 구현 경계 (CIO 확정 2026-08-13 · Review #3 안건 6)
  ✅ 여기서 구현하는 것 — Collector Layer
       CIK 매핑 · Filing Index 수집 · 8-K 등 메타데이터 · 폼 타입 구분(10-K/10-Q/20-F/6-K) · XBRL 수집
  ⛔ 여기서 구현하지 않는 것 — Decision Layer (Review #3 승인 이후에만)
       Event Score · Business 판정 · Stage 변경 · Ready/Watchlist 판정 · 투자 의사결정 규칙
  이 파일은 데이터를 모으기만 한다. 어떤 필드도 '좋다/나쁘다'를 뜻하지 않는다.

★ 미국에는 한국의 투자자별 수급(기관/외국인 일별 순매수)에 해당하는 원천이 없다.
  13F(분기) · Form 4 · Short Interest(격주) 뿐이며, 이는 대체재가 아니다.
  따라서 이 수집기는 수급을 흉내내지 않는다. 없는 것은 없는 채로 둔다.
"""
import os
import sys
import json
import time
import datetime as dt

import requests

from common import save, load_universe, universe_meta, today_kst, now_utc_iso

UA = os.getenv("SEC_USER_AGENT", "").strip()
if not UA:
    print("FATAL: SEC_USER_AGENT 환경변수가 없습니다.")
    print("       SEC 는 연락 가능한 User-Agent 를 요구합니다. 예: 'Atlas Research name@example.com'")
    sys.exit(1)

HEADERS = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
CONCEPT = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{tag}.json"

CIK_MAP_PATH = "config/cik_map.json"
LOOKBACK_DAYS = 30
THROTTLE = 0.15                      # SEC 권고(초당 10건 이하)보다 여유 있게

# 폼 타입 구분 — 판정이 아니라 분류다.
DOMESTIC_FORMS = {"10-K", "10-Q", "8-K"}
FPI_FORMS = {"20-F", "6-K", "40-F"}          # Foreign Private Issuer (TSMC 등)

# Form Family — Decision Layer 가 폼 번호를 몰라도 되게 미리 분류해 둔다.
# ⛔ 중요도·점수를 뜻하지 않는다. 어느 가족에 속하는가만 말한다.
FORM_FAMILY = {
    "annual_report":    {"10-K", "20-F", "40-F", "11-K"},
    "quarterly_report": {"10-Q"},
    "current_report":   {"8-K", "6-K"},
    "ownership":        {"3", "4", "5", "SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A", "13F-HR"},
    "proxy":            {"DEF 14A", "DEFA14A", "PRE 14A", "DEFM14A"},
    "registration":     {"S-1", "S-3", "S-4", "S-8", "F-1", "F-3", "F-4",
                         "424B1", "424B2", "424B3", "424B4", "424B5"},
}
_FAMILY_LOOKUP = {form: fam for fam, forms in FORM_FAMILY.items() for form in forms}


def form_family(form: str) -> str:
    """정정 공시(`/A`)는 원 폼과 같은 가족으로 본다. 모르면 'other' — 지어내지 않는다."""
    f = (form or "").strip()
    return _FAMILY_LOOKUP.get(f) or _FAMILY_LOOKUP.get(f.removesuffix("/A")) or "other"


# ★ 이 수집기가 실제 SEC 응답으로 검증됐는지를 데이터에 남긴다 (선언이 아니라 실행 중 도출).
#   스텁으로 돌린 결과와 실 응답으로 돌린 결과를 나중에 구분할 수 있어야 한다.
VALIDATION = {"live_requests": 0, "schema_issues": []}
REQUIRED_SUBMISSION_KEYS = ("form", "filingDate", "accessionNumber")

# XBRL — us-gaap 을 먼저 시도하고, 없으면 ifrs-full 을 시도한다.
# 어느 쪽도 없으면 만들어내지 않고 missing 으로 남긴다.
TAXONOMY_TAGS = {
    "us-gaap": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "OperatingIncomeLoss", "NetIncomeLoss"],
    "ifrs-full": ["Revenue", "GrossProfit", "ProfitLoss"],
}


def get(url: str):
    """404 는 '없음'이며 오류가 아니다 — 있는 척도, 죽는 것도 하지 않는다."""
    time.sleep(THROTTLE)
    r = requests.get(url, headers=HEADERS, timeout=30)
    VALIDATION["live_requests"] += 1        # ★ 실제로 원천에 다녀왔다는 사실만 센다
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def validation_report() -> dict:
    live = VALIDATION["live_requests"] > 0
    issues = VALIDATION["schema_issues"]
    return {
        "source": "data.sec.gov",
        "schema_verified": bool(live and not issues),
        "verification_method": "live" if live else "stub",
        "live_requests": VALIDATION["live_requests"],
        "schema_issues": issues,
        "reason": (None if live and not issues
                   else "schema mismatch on live response" if live
                   else "live endpoint not reached (stub or blocked network)"),
    }


# ────────────────────────────────────────────────────────────
# CIK 매핑
# ────────────────────────────────────────────────────────────

def build_cik_map() -> dict:
    print("[sec] cik_map 생성 중...")
    data = get(TICKERS_URL)
    if not data:
        raise RuntimeError("company_tickers.json 을 받지 못했다")
    mapping = {}
    for row in data.values():
        t = (row.get("ticker") or "").strip().upper()
        cik = row.get("cik_str")
        if t and cik is not None:
            mapping[t] = str(cik).zfill(10)
    os.makedirs("config", exist_ok=True)
    with open(CIK_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False)
    print(f"[sec] cik_map {len(mapping)}건 생성")
    return mapping


def get_cik_map() -> dict:
    if os.path.exists(CIK_MAP_PATH):
        with open(CIK_MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    return build_cik_map()


# ────────────────────────────────────────────────────────────
# 공시 인덱스
# ────────────────────────────────────────────────────────────

def fetch_filings(cik: str, days: int = LOOKBACK_DAYS) -> dict:
    body = get(SUBMISSIONS.format(cik=cik))
    if not body:
        raise RuntimeError(f"submissions 없음 (CIK {cik})")

    recent = body.get("filings", {}).get("recent", {})
    missing_keys = [k for k in REQUIRED_SUBMISSION_KEYS if k not in recent]
    if missing_keys:                        # 스키마가 바뀌면 조용히 넘기지 않는다
        VALIDATION["schema_issues"].append({"cik": cik, "missing_keys": missing_keys})

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    items = recent.get("items", [""] * len(forms))

    cutoff = (today_kst() - dt.timedelta(days=days)).isoformat()
    out, form_counts, family_counts = [], {}, {}

    for i, form in enumerate(forms):
        d = dates[i] if i < len(dates) else ""
        form_counts[form] = form_counts.get(form, 0) + 1
        if d < cutoff:
            continue
        acc = (accs[i] if i < len(accs) else "").replace("-", "")
        fam = form_family(form)
        family_counts[fam] = family_counts.get(fam, 0) + 1
        out.append({
            "date": d,
            "form": form,
            "form_family": fam,                            # Decision Layer 재사용용 분류
            "form_class": ("domestic" if form in DOMESTIC_FORMS
                           else "fpi" if form in FPI_FORMS
                           else "other"),
            "items": items[i] if i < len(items) else "",   # 8-K 항목코드 (해석하지 않는다)
            "accession": accs[i] if i < len(accs) else "",
            "url": (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/"
                    f"{docs[i]}" if acc and i < len(docs) and docs[i] else ""),
        })

    # ★ 발행사 프로파일은 제출된 폼에서 '도출'한다. 종목명으로 하드코딩하지 않는다.
    #   판단이 안 서면 unknown 으로 남긴다 — 추정으로 채우지 않는다.
    seen = set(form_counts)
    has_dom = bool(seen & DOMESTIC_FORMS)
    has_fpi = bool(seen & FPI_FORMS)
    profile = ("foreign_private_issuer" if has_fpi and not has_dom
               else "domestic" if has_dom and not has_fpi
               else "mixed" if has_dom and has_fpi
               else "unknown")

    return {
        "entity_name": body.get("name"),
        "sic_description": body.get("sicDescription"),
        "fiscal_year_end": body.get("fiscalYearEnd"),
        "filer_profile": profile,          # domestic / foreign_private_issuer / mixed / unknown
        "form_types_on_file": sorted(seen),
        "form_family_counts": family_counts,
        "filings_recent": out,
        "filings_recent_count": len(out),
        "lookback_days": days,
    }


# ────────────────────────────────────────────────────────────
# XBRL
# ────────────────────────────────────────────────────────────

def fetch_xbrl(cik: str) -> dict:
    """선언한 태그만 조회하고, 없는 태그는 조용히 버리지 않고 missing 에 남긴다."""
    for taxonomy, tags in TAXONOMY_TAGS.items():
        found, missing = {}, []
        for tag in tags:
            body = get(CONCEPT.format(cik=cik, taxonomy=taxonomy, tag=tag))
            if not body:
                missing.append(tag)
                continue
            units = body.get("units", {})
            unit = next(iter(units), None)
            rows = units.get(unit, []) if unit else []
            if not rows:
                missing.append(tag)
                continue
            last = max(rows, key=lambda r: r.get("end", ""))
            found[tag] = {"value": last.get("val"), "unit": unit,
                          "end": last.get("end"), "form": last.get("form"),
                          "fy": last.get("fy"), "fp": last.get("fp")}
        if found:
            return {"taxonomy": taxonomy, "facts": found, "missing_tags": missing}

    return {"taxonomy": None, "facts": {},
            "missing_tags": sorted({t for tags in TAXONOMY_TAGS.values() for t in tags}),
            "note": "us-gaap · ifrs-full 어느 쪽에서도 선언 태그를 찾지 못했다 — Unknown"}


# ────────────────────────────────────────────────────────────

def meta(s: dict) -> dict:
    """krx.py v3.1 / dart.py v2.1 과 동일 — Stage 와 Coverage 는 다른 축이다."""
    return {
        "atlas_stage": s.get("atlas_stage"),
        "coverage": s.get("coverage"),
        "collected": True,
    }


def us_universe() -> list:
    """미국 종목은 load_universe() 반환값이 아니라 universe_meta['notion_skipped'] 에 있다.
    (한국 6자리 코드가 아니어서 KRX 수집 대상에서 제외된 종목들 — 단계는 살아 있다)"""
    load_universe()
    out = []
    for s in universe_meta.get("notion_skipped", []):
        t = (s.get("ticker") or "").strip().upper()
        if t and not t.isdigit():
            out.append({"ticker": t, "name": s.get("name"),
                        "atlas_stage": s.get("atlas_stage"),
                        "coverage": s.get("coverage")})
    return out


def main() -> None:
    today = today_kst()
    cik_map = get_cik_map()

    payload = {
        "collected_at_utc": now_utc_iso(),
        "collected_for_kst_date": today.isoformat(),
        "source": "SEC EDGAR (data.sec.gov 조회 API)",
        "source_tier": "Official",
        "collector_version": "v1.1",
        "layer": "collector_only",
        "decision_layer": None,
        "decision_layer_status": ("Undefined — 미국 판정 규칙은 Review #3 승인 이후에만 구현한다 "
                                  "(Event Score · Business 판정 · Stage 변경 금지)"),
        "supply_demand": None,
        "supply_demand_status": ("Unavailable — 미국에는 종목별·일별 투자자 유형 순매수 원천이 없다. "
                                 "13F(분기)·Form 4·Short Interest(격주)는 대체재가 아니다."),
        "stocks": {},
    }

    ok = failed = 0
    for s in us_universe():
        t, name = s["ticker"], s["name"]
        cik = cik_map.get(t)
        if not cik:
            payload["stocks"][t] = {"name": name, **meta(s), "status": "FAILED",
                                    "error": "CIK 매핑 없음"}
            failed += 1
            print(f"[FAILED] {t} {name} — CIK 없음")
            continue
        try:
            row = {"name": name, **meta(s), "cik": cik, "status": "ok"}
            row.update(fetch_filings(cik))
            row["xbrl"] = fetch_xbrl(cik)
            payload["stocks"][t] = row
            ok += 1
            print(f"[ok]     {t} {name} "
                  f"[stage={s.get('atlas_stage')} coverage={s.get('coverage')}] "
                  f"— {row['filer_profile']} · 공시 {row['filings_recent_count']}건 "
                  f"· XBRL {row['xbrl']['taxonomy']}"
                  + (f"  ⚠ 태그 누락: {row['xbrl']['missing_tags']}"
                     if row["xbrl"]["missing_tags"] else ""))
        except Exception as e:                      # noqa: BLE001
            payload["stocks"][t] = {"name": name, **meta(s), "cik": cik,
                                    "status": "FAILED",
                                    "error": f"{type(e).__name__}: {e}"}
            failed += 1
            print(f"[FAILED] {t} {name} — {type(e).__name__}: {e}")

    payload["summary"] = {"ok": ok, "failed": failed}
    payload["validation"] = validation_report()
    v = payload["validation"]
    print(f"[validation] schema_verified={v['schema_verified']} "
          f"method={v['verification_method']} live_requests={v['live_requests']}"
          + (f" ⚠ {v['schema_issues']}" if v["schema_issues"] else ""))
    save(payload, "sec.json", today)

    if ok == 0:
        print("FATAL: 모든 종목 수집 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
