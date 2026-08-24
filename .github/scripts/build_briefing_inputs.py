#!/usr/bin/env python3

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT = DATA / "briefing"
HEALTH = DATA / "briefing_status.json"


def _load_sibling(name, filename):
    # Path-relative load (not a plain `import`) so this resolves the same
    # way whether this script is run directly (`python3 .../build_briefing_
    # inputs.py`) or loaded by path via importlib.util.spec_from_file_
    # location, as the test suite does.
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _load_sibling("briefing_generation_for_build", "briefing_generation.py")


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


def krx_confirmed_metrics(stock, latest_confirmed_day):
    sma20 = stock.get("sma20")
    basis = stock.get("sma20_basis")
    through = stock.get("sma20_through")
    status = stock.get("sma20_status")

    numeric_sma20 = (
        sma20 is None
        or (
            isinstance(sma20, (int, float))
            and not isinstance(sma20, bool)
        )
    )
    valid = (
        numeric_sma20
        and type(basis) is int
        and 0 <= basis <= 20
        and through == latest_confirmed_day
        and status in {"ok", "insufficient_confirmed_history"}
        and (
            (status == "ok" and sma20 is not None and basis == 20)
            or (status != "ok" and sma20 is None)
        )
    )

    if not valid:
        return {
            "history_basis": "confirmed_only",
            "status": "unknown",
            "reason": "source_contract_missing_or_invalid",
            "sma20": None,
            "sma20_basis": None,
            "sma20_through": None,
        }

    return {
        "history_basis": "confirmed_only",
        "status": status,
        "reason": None,
        "sma20": sma20,
        "sma20_basis": basis,
        "sma20_through": through,
    }


def krx_investor_data_completeness(stock):
    missing_investors = stock.get("missing_investors")
    missing_rows = stock.get("investor_rows_missing")
    missing_by_source = stock.get("investor_rows_missing_by_source")

    valid = (
        isinstance(missing_investors, list)
        and all(isinstance(x, str) for x in missing_investors)
        and isinstance(missing_rows, list)
        and all(isinstance(x, str) for x in missing_rows)
        and isinstance(missing_by_source, dict)
        and all(
            isinstance(source, str)
            and isinstance(days, list)
            and all(isinstance(day, str) for day in days)
            for source, days in missing_by_source.items()
        )
    )

    if not valid:
        return {
            "status": "unknown",
            "complete": False,
            "reason": "source_contract_missing_or_invalid",
            "missing_investors": None,
            "investor_rows_missing": None,
            "investor_rows_missing_by_source": None,
        }

    complete = not (
        missing_investors
        or missing_rows
        or any(missing_by_source.values())
    )

    return {
        "status": "complete" if complete else "incomplete",
        "complete": complete,
        "reason": (
            "no_missing_investor_data"
            if complete
            else "reported_missing_investor_data"
        ),
        "missing_investors": sorted(missing_investors),
        "investor_rows_missing": sorted(missing_rows),
        "investor_rows_missing_by_source": {
            source: sorted(days)
            for source, days in sorted(missing_by_source.items())
        },
    }


def build_krx_views(obj, sha, out_root, generation):
    stocks = obj.get("stocks")
    if not isinstance(stocks, dict):
        raise RuntimeError("krx:stocks_missing")

    target = out_root / "krx"
    target.mkdir(parents=True, exist_ok=True)

    expected = set()

    for code, stock in stocks.items():
        if not isinstance(stock, dict):
            continue

        day, row = latest_confirmed_daily(stock)

        payload = {
            "schema_version": GEN.COMPACT_SCHEMA_VERSIONS["krx"],
            "generation": generation,
            "market": "KRX",
            "symbol": code,
            "name": stock.get("name"),
            "atlas_stage": stock.get("atlas_stage"),
            "status": stock.get("status"),
            "latest_confirmed_day": day,
            "latest_confirmed_row": row,
            "confirmed_metrics": krx_confirmed_metrics(stock, day),
            "investor_data_completeness": (
                krx_investor_data_completeness(stock)
            ),
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


def sec_content_index(content):
    if content is None:
        return {}
    if content.get("schema_version") != "sec_filing_content_run/1":
        raise RuntimeError("sec_content:schema_invalid")
    records = content.get("records")
    if not isinstance(records, list):
        raise RuntimeError("sec_content:records_missing")
    out = {}
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("sec_content:record_invalid")
        identity = record.get("filing_identity") or {}
        key = (record.get("ticker"), identity.get("accession"))
        if not all(isinstance(part, str) and part for part in key):
            raise RuntimeError("sec_content:identity_invalid")
        if key in out:
            raise RuntimeError(f"sec_content:identity_duplicate:{key}")
        out[key] = record
    return out


def dart_content_index(content):
    if content is None:
        return {}
    if content.get("schema_version") != "dart_filing_content_run/1":
        raise RuntimeError("dart_content:schema_invalid")
    records = content.get("records")
    if not isinstance(records, list):
        raise RuntimeError("dart_content:records_missing")
    out = {}
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("dart_content:record_invalid")
        identity = record.get("filing_identity") or {}
        key = (record.get("ticker"), identity.get("rcept_no"))
        if not all(isinstance(part, str) and part for part in key):
            raise RuntimeError("dart_content:identity_invalid")
        if key in out:
            raise RuntimeError(f"dart_content:identity_duplicate:{key}")
        out[key] = record
    return out


def compact_content_record(record, content_source):
    keep = [
        "filing_classification",
        "capture_policy",
        "discovery_status",
        "content_status",
        "evidence_status",
        "interpretation_status",
        "rule_impact",
        "action",
        "reasons",
        "retrieved_at_utc",
        "extractor_version",
        "source_archive",
        "documents",
        "extracted",
        "operation",
        "skip_reason",
        "publication_status",
        "raw_cache_policy",
    ]
    return {
        **{key: record.get(key) for key in keep if key in record},
        "source": content_source,
    }


def compact_dart_stock(stock, symbol=None, content=None, content_source=None):
    keep = [
        "name",
        "atlas_stage",
        "coverage",
        "db_state",
        "in_notion",
        "corp_code",
        "status",
        "total_count",
        "relevant_count",
        "relevant",
    ]
    out = {key: stock.get(key) for key in keep if key in stock}
    if isinstance(out.get("relevant"), list):
        filings = []
        for source_filing in out["relevant"][:20]:
            if not isinstance(source_filing, dict):
                continue
            filing = dict(source_filing)
            record = (content or {}).get((symbol, filing.get("rcept_no")))
            if record is not None:
                compact = compact_content_record(record, content_source)
                filing["content"] = compact
                filing["body_captured"] = compact.get("content_status") == "OK"
                filing["body_capture_status"] = compact.get("content_status")
            filings.append(filing)
        out["relevant"] = filings
    return out


def build_dart_views(obj, sha, out_root, generation, content=None, content_sha=None):
    stocks = obj.get("stocks")
    if not isinstance(stocks, dict):
        raise RuntimeError("dart:stocks_missing")

    target = out_root / "dart"
    target.mkdir(parents=True, exist_ok=True)
    expected = set()
    content_records = dart_content_index(content)
    content_source = None
    if content is not None:
        content_source = {
            "source_file": "data/latest_dart_content.json",
            "source_sha256": content_sha,
            "collected_for_kst_date": content.get("collected_for_kst_date"),
            "observed_at_utc": content.get("observed_at_utc"),
            "run_status": content.get("run_status"),
        }

    for symbol, stock in stocks.items():
        if not isinstance(stock, dict):
            continue
        payload = {
            "schema_version": GEN.COMPACT_SCHEMA_VERSIONS["dart"],
            "generation": generation,
            "market": "DART",
            "symbol": symbol,
            "stock": compact_dart_stock(
                stock,
                symbol=symbol,
                content=content_records,
                content_source=content_source,
            ),
            "source": source_meta("dart", obj, sha),
        }
        path = target / f"{symbol}.json"
        write_json(path, payload)
        expected.add(path.name)

    for path in target.glob("*.json"):
        if path.name not in expected:
            path.unlink()


def compact_sec_stock(stock, symbol=None, content=None, content_source=None):
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
        filings = []
        for source_filing in out["filings_recent"][:10]:
            if not isinstance(source_filing, dict):
                continue
            filing = dict(source_filing)
            record = (content or {}).get((symbol, filing.get("accession")))
            if record is not None:
                compact = compact_content_record(record, content_source)
                filing["content"] = compact
                filing["body_captured"] = compact.get("content_status") == "OK"
                filing["body_capture_status"] = compact.get("content_status")
            filings.append(filing)
        out["filings_recent"] = filings
    return out


def build_sec_views(obj, sha, out_root, generation, content=None, content_sha=None):
    stocks = obj.get("stocks")
    if not isinstance(stocks, dict):
        raise RuntimeError("sec:stocks_missing")

    target = out_root / "sec"
    target.mkdir(parents=True, exist_ok=True)

    expected = set()
    content_records = sec_content_index(content)
    content_source = None
    if content is not None:
        content_source = {
            "source_file": "data/latest_sec_content.json",
            "source_sha256": content_sha,
            "collected_for_kst_date": content.get("collected_for_kst_date"),
            "observed_at_utc": content.get("observed_at_utc"),
            "run_status": content.get("run_status"),
        }

    for symbol, stock in stocks.items():
        if not isinstance(stock, dict):
            continue

        payload = {
            "schema_version": GEN.COMPACT_SCHEMA_VERSIONS["sec"],
            "generation": generation,
            "market": "SEC",
            "symbol": symbol,
            "stock": compact_sec_stock(
                stock,
                symbol=symbol,
                content=content_records,
                content_source=content_source,
            ),
            "source": source_meta("sec", obj, sha),
        }

        path = target / f"{symbol}.json"
        write_json(path, payload)
        expected.add(path.name)

    for path in target.glob("*.json"):
        if path.name not in expected:
            path.unlink()


def atomic_publish(staging, target):
    backup = target.parent / f".{target.name}.bak.{os.getpid()}"

    if backup.exists():
        shutil.rmtree(backup)

    had_target = target.exists()

    try:
        if had_target:
            target.rename(backup)

        staging.rename(target)

    except Exception:
        if target.exists() and not had_target:
            shutil.rmtree(target)

        if backup.exists() and not target.exists():
            backup.rename(target)

        raise

    else:
        if backup.exists():
            shutil.rmtree(backup)


def write_health(
    expected_date, data_ready, read_model_ready, error=None, generation=None
):
    payload = {
        "schema_version": 2,
        "expected_kst_date": expected_date,
        "data_ready": data_ready,
        "read_model_ready": read_model_ready,
        "status": (
            "ready"
            if data_ready and read_model_ready
            else "read_model_degraded"
            if data_ready
            else "data_not_ready"
        ),
        "error": error,
        # P0-05A -- the build step's own canary copy of the generation it
        # just published to step0_status.json/compact views. check_briefing_
        # readiness.py re-validates this against its own independent
        # recomputation before overwriting this file with the final health.
        # None (not omitted) when the build failed before a generation
        # could be computed -- read_model_ready is already False in that
        # case, so this is diagnostic only, never treated as a match.
        "generation": generation,
    }
    write_json(HEALTH, payload)


def source_data_ready(expected_date):
    if not expected_date:
        return False

    for name in ("krx", "dart", "sec"):
        obj, _ = load_json(DATA / f"latest_{name}.json")
        summary = require_summary(name, obj)
        if (
            obj.get("collected_for_kst_date") != expected_date
            or summary["failed"] != 0
        ):
            return False

    return True


def load_optional_sec_content(expected_date):
    path = DATA / "latest_sec_content.json"
    if not path.exists():
        return None, None, {
            "status": "missing",
            "source_file": "data/latest_sec_content.json",
        }
    try:
        content, sha = load_json(path)
        sec_content_index(content)
    except Exception as exc:
        return None, None, {
            "status": "invalid",
            "source_file": "data/latest_sec_content.json",
            "error": f"{type(exc).__name__}:{exc}",
        }
    observed_date = content.get("collected_for_kst_date")
    if not expected_date or observed_date != expected_date:
        return None, None, {
            "status": "stale",
            "source_file": "data/latest_sec_content.json",
            "source_sha256": sha,
            "collected_for_kst_date": observed_date,
        }
    run_status = content.get("run_status")
    if run_status not in {"OK", "DEGRADED"}:
        return None, None, {
            "status": "failed",
            "source_file": "data/latest_sec_content.json",
            "source_sha256": sha,
            "collected_for_kst_date": observed_date,
            "run_status": run_status,
            "reasons": content.get("reasons", []),
        }
    return content, sha, {
        "status": "available" if run_status == "OK" else "degraded",
        "source_file": "data/latest_sec_content.json",
        "source_sha256": sha,
        "collected_for_kst_date": observed_date,
        "run_status": run_status,
    }


def load_optional_dart_content(expected_date):
    path = DATA / "latest_dart_content.json"
    if not path.exists():
        return None, None, {
            "status": "missing",
            "source_file": "data/latest_dart_content.json",
        }
    try:
        content, sha = load_json(path)
        dart_content_index(content)
    except Exception as exc:
        return None, None, {
            "status": "invalid",
            "source_file": "data/latest_dart_content.json",
            "error": f"{type(exc).__name__}:{exc}",
        }
    observed_date = content.get("collected_for_kst_date")
    if not expected_date or observed_date != expected_date:
        return None, None, {
            "status": "stale",
            "source_file": "data/latest_dart_content.json",
            "source_sha256": sha,
            "collected_for_kst_date": observed_date,
        }
    run_status = content.get("run_status")
    if run_status not in {"OK", "DEGRADED"}:
        return None, None, {
            "status": "failed",
            "source_file": "data/latest_dart_content.json",
            "source_sha256": sha,
            "collected_for_kst_date": observed_date,
            "run_status": run_status,
            "reasons": content.get("reasons", []),
        }
    return content, sha, {
        "status": "available" if run_status == "OK" else "degraded",
        "source_file": "data/latest_dart_content.json",
        "source_sha256": sha,
        "collected_for_kst_date": observed_date,
        "run_status": run_status,
    }


def build_and_publish(expected_date, fail_before_publish=False):
    sources = {}
    hashes = {}

    for name in ("krx", "dart", "sec"):
        obj, sha = load_json(DATA / f"latest_{name}.json")
        sources[name] = obj
        hashes[name] = sha

    sec_content, sec_content_sha, sec_content_status = (
        load_optional_sec_content(expected_date)
    )
    dart_content, dart_content_sha, dart_content_status = (
        load_optional_dart_content(expected_date)
    )

    summaries = {
        name: require_summary(name, sources[name])
        for name in ("krx", "dart", "sec")
    }

    dates = {
        name: sources[name].get("collected_for_kst_date")
        for name in ("krx", "dart", "sec")
    }

    freshness = {
        name: (
            "fresh"
            if expected_date and dates[name] == expected_date
            else "unknown" if not expected_date
            else "stale"
        )
        for name in dates
    }

    data_ready = (
        bool(expected_date)
        and all(dates[name] == expected_date for name in dates)
        and all(summaries[name]["failed"] == 0 for name in summaries)
    )

    overall = (
        "pass"
        if data_ready and all(v == "fresh" for v in freshness.values())
        else "fail"
    )

    optional_evidence = {
        "dart_content": dart_content_status,
        "sec_content": sec_content_status,
    }

    # P0-05A -- binds step0_status.json, every compact view, and
    # briefing_status.json to one shared generation_id. See
    # briefing_generation.py for why this is not a git commit SHA.
    generation = GEN.generation_block(
        expected_date, hashes, optional_evidence, sources
    )

    status = {
        "schema_version": 2,
        "purpose": "Atlas briefing Step 0 compact readiness SSOT",
        "expected_kst_date": expected_date,
        "generation": generation,
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
        "read_model_inventory": {
            "date_basis": "KST",
            "authority_path": "data/briefing/step0_status.json",
            "health_path": "data/briefing_status.json",
            "compact_path_templates": [
                "data/briefing/krx/{SYMBOL}.json",
                "data/briefing/dart/{SYMBOL}.json",
                "data/briefing/sec/{SYMBOL}.json",
            ],
            "optional_evidence_sources": [
                "data/latest_dart_content.json",
                "data/latest_sec_content.json",
            ],
            "operations_telemetry_sources": (
                [
                    "data/operations/collect_runs/"
                    f"{expected_date}/index.json"
                ]
                if expected_date
                else []
            ),
        },
        "optional_evidence": optional_evidence,
    }

    for name in ("krx", "dart", "sec"):
        status["collectors"][name] = {
            **summaries[name],
            "freshness": freshness[name],
            **source_meta(name, sources[name], hashes[name]),
        }

    staging = DATA / f".briefing.tmp.{os.getpid()}"

    if staging.exists():
        shutil.rmtree(staging)

    try:
        write_json(staging / "step0_status.json", status)
        build_krx_views(sources["krx"], hashes["krx"], staging, generation)
        build_dart_views(
            sources["dart"],
            hashes["dart"],
            staging,
            generation,
            content=dart_content,
            content_sha=dart_content_sha,
        )
        build_sec_views(
            sources["sec"],
            hashes["sec"],
            staging,
            generation,
            content=sec_content,
            content_sha=sec_content_sha,
        )

        if fail_before_publish:
            raise RuntimeError("injected_failure_before_publish")

        atomic_publish(staging, OUT)

    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    write_health(
        expected_date=expected_date,
        data_ready=data_ready,
        read_model_ready=True,
        error=None,
        generation=generation,
    )

    return status


def run(expected_date, fail_before_publish=False):
    try:
        return build_and_publish(
            expected_date,
            fail_before_publish=fail_before_publish,
        )
    except Exception as exc:
        try:
            data_ready = source_data_ready(expected_date)
        except Exception:
            data_ready = False

        write_health(
            expected_date=expected_date,
            data_ready=data_ready,
            read_model_ready=False,
            error=f"{type(exc).__name__}:{exc}",
        )
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--today")
    args = ap.parse_args()

    status = run(args.today)

    print(
        f"PASS build: total_ok={status['totals']['ok']} "
        f"total_failed={status['totals']['failed']} "
        f"overall={status['overall']}"
    )


if __name__ == "__main__":
    main()
