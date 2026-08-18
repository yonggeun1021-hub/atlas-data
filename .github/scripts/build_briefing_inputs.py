#!/usr/bin/env python3

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT = DATA / "briefing"


def load_json(path):
    raw = path.read_bytes()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"incomplete_or_invalid_json:{path}:{e}") from e

    if not isinstance(obj, dict):
        raise RuntimeError(f"top_level_not_object:{path}")

    return obj, hashlib.sha256(raw).hexdigest()


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def require_summary(name, obj):
    s = obj.get("summary")
    if not isinstance(s, dict):
        raise RuntimeError(f"{name}:summary_missing")

    ok = s.get("ok")
    failed = s.get("failed")

    if not isinstance(ok, int) or not isinstance(failed, int):
        raise RuntimeError(f"{name}:summary_invalid")

    return {"ok": ok, "failed": failed}


def source_meta(name, obj, sha):
    return {
        "source_file": f"data/latest_{name}.json",
        "source_sha256": sha,
        "collected_at_utc": obj.get("collected_at_utc"),
        "collected_for_kst_date": obj.get("collected_for_kst_date"),
    }


def latest_confirmed_daily(stock):
    daily = stock.get("daily")
    if not isinstance(daily, dict):
        return None, None

    confirmed = []
    for day, row in daily.items():
        if isinstance(row, dict) and row.get("confirmed") is True:
            confirmed.append((day, row))

    if not confirmed:
        return None, None

    return max(confirmed, key=lambda x: x[0])


def build_krx_views(obj, sha):
    stocks = obj.get("stocks")
    if not isinstance(stocks, dict):
        raise RuntimeError("krx:stocks_missing")

    target = OUT / "krx"
    target.mkdir(parents=True, exist_ok=True)

    expected = set()

    for code, stock in stocks.items():
        if not isinstance(stock, dict):
            continue

        day, row = latest_confirmed_daily(stock)

        payload = {
            "schema_version": 1,
            "market": "KRX",
            "symbol": code,
            "name": stock.get("name"),
            "atlas_stage": stock.get("atlas_stage"),
            "status": stock.get("status"),
            "latest_confirmed_day": day,
            "latest_confirmed_row": row,
            "decision_readiness": {
                "confirmed_through": obj.get("decision_readiness", {}).get(
                    "confirmed_through"
                ),
                "same_day_confirmation": obj.get("same_day_confirmation"),
            },
            "source": source_meta("krx", obj, sha),
        }

        path = target / f"{code}.json"
        write_json(path, payload)
        expected.add(path.name)

    for path in target.glob("*.json"):
        if path.name not in expected:
            path.unlink()


def compact_sec_stock(stock):
    keep = [
        "name",
        "atlas_stage",
        "coverage",
        "collected",
        "cik",
        "status",
        "entity_name",
        "sic_description",
        "fiscal_year_end",
        "filer_profile",
        "form_family_counts",
        "filings_recent",
    ]
    out = {k: stock.get(k) for k in keep if k in stock}
    if isinstance(out.get("filings_recent"), list):
        out["filings_recent"] = out["filings_recent"][:10]
    return out


def build_sec_views(obj, sha):
    stocks = obj.get("stocks")
    if not isinstance(stocks, dict):
        raise RuntimeError("sec:stocks_missing")

    target = OUT / "sec"
    target.mkdir(parents=True, exist_ok=True)

    expected = set()

    for symbol, stock in stocks.items():
        if not isinstance(stock, dict):
            continue

        payload = {
            "schema_version": 1,
            "market": "SEC",
            "symbol": symbol,
            "stock": compact_sec_stock(stock),
            "source": source_meta("sec", obj, sha),
        }

        path = target / f"{symbol}.json"
        write_json(path, payload)
        expected.add(path.name)

    for path in target.glob("*.json"):
        if path.name not in expected:
            path.unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--today")
    args = ap.parse_args()

    sources = {}
    hashes = {}

    for name in ("krx", "dart", "sec"):
        obj, sha = load_json(DATA / f"latest_{name}.json")
        sources[name] = obj
        hashes[name] = sha

    summaries = {
        name: require_summary(name, sources[name])
        for name in ("krx", "dart", "sec")
    }

    dates = {
        name: sources[name].get("collected_for_kst_date")
        for name in ("krx", "dart", "sec")
    }

    expected_date = args.today
    freshness = {
        name: (
            "fresh"
            if expected_date and dates[name] == expected_date
            else "unknown" if not expected_date
            else "stale"
        )
        for name in dates
    }

    overall = (
        "pass"
        if all(s["failed"] == 0 for s in summaries.values())
        and all(v == "fresh" for v in freshness.values())
        else "fail"
    )

    status = {
        "schema_version": 1,
        "purpose": "Atlas briefing Step 0 compact readiness SSOT",
        "expected_kst_date": expected_date,
        "overall": overall,
        "collectors": {},
        "totals": {
            "ok": sum(s["ok"] for s in summaries.values()),
            "failed": sum(s["failed"] for s in summaries.values()),
        },
        "scheduled_collectors": [
            {
                "name": "daily_collect",
                "date_basis": "KST",
                "exact_paths": [
                    "data/latest_krx.json",
                    "data/latest_dart.json",
                    "data/latest_sec.json",
                ],
            },
            {
                "name": "stablecoin_daily_capture",
                "date_basis": "UTC",
                "exact_path_templates": [
                    "evidence/stablecoin/raw/{UTC_DATE}/_downloaded_at.txt",
                    "evidence/stablecoin/raw/{UTC_DATE}/_sha256.txt",
                ],
            },
        ],
    }

    for name in ("krx", "dart", "sec"):
        status["collectors"][name] = {
            **summaries[name],
            "freshness": freshness[name],
            **source_meta(name, sources[name], hashes[name]),
        }

    write_json(OUT / "step0_status.json", status)
    build_krx_views(sources["krx"], hashes["krx"])
    build_sec_views(sources["sec"], hashes["sec"])

    print(
        f"PASS build: total_ok={status['totals']['ok']} "
        f"total_failed={status['totals']['failed']} "
        f"overall={status['overall']}"
    )


if __name__ == "__main__":
    main()
