#!/usr/bin/env python3
"""Capture free FRED VIX and Alpaca IEX evidence without trading authority."""
from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "free_market_data_contract.json"
UTC = dt.timezone.utc


def _load_fred_provenance():
    path = ROOT / "collectors" / "fred_vix_provenance.py"
    spec = importlib.util.spec_from_file_location("atlas_fred_vix_provenance", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("FRED_PROVENANCE_MODULE_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FRED_PROVENANCE = _load_fred_provenance()


class FreeMarketDataError(ValueError):
    pass


def canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("contract_version") != "free_market_data/3":
        raise FreeMarketDataError("CONTRACT_VERSION_INVALID")
    if value.get("alpaca", {}).get("feed") != "iex":
        raise FreeMarketDataError("ALPACA_FEED_MUST_BE_IEX")
    if value.get("fred", {}).get("raw_retention") != FRED_PROVENANCE.RAW_RETENTION:
        raise FreeMarketDataError("FRED_RAW_RETENTION_INVALID")
    if value.get("fred", {}).get("liquidity_raw_retention") != "TRANSIENT_NOT_PERSISTED_HASH_ATTESTED":
        raise FreeMarketDataError("FRED_LIQUIDITY_RAW_RETENTION_INVALID")
    if value.get("fred", {}).get("risk_series") != ["VIXCLS"]:
        raise FreeMarketDataError("FRED_RISK_SERIES_INVALID")
    if value.get("fred", {}).get("liquidity_series") != ["WRESBAL", "TOTBKCR"]:
        raise FreeMarketDataError("FRED_LIQUIDITY_SERIES_INVALID")
    if value.get("fred", {}).get("partial_publish_authorized") is not True:
        raise FreeMarketDataError("FRED_PARTIAL_PUBLISH_NOT_AUTHORIZED")
    if value.get("alpaca", {}).get("credential_scope") != "DEDICATED_MARKET_DATA_ONLY":
        raise FreeMarketDataError("ALPACA_CREDENTIAL_SCOPE_INVALID")
    if value.get("alpaca", {}).get("trend_symbols") != ["SPY", "QQQ", "IWM"]:
        raise FreeMarketDataError("ALPACA_TREND_SYMBOLS_INVALID")
    if value.get("alpaca", {}).get("return_windows_sessions") != [5, 20, 60]:
        raise FreeMarketDataError("ALPACA_RETURN_WINDOWS_INVALID")
    authority = value.get("authority")
    if authority != {
        "evidence_capture_only": True,
        "us_breadth_authorized": False,
        "market_wide_price_authorized": False,
        "entry_authorized": False,
        "action_authorized": False,
        "order_authorized": False,
        "broker_submission_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }:
        raise FreeMarketDataError("AUTHORITY_INVALID")
    return value


def _get(url: str, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise FreeMarketDataError(f"HTTP_STATUS:{response.status}")
            return response.read()
    except urllib.error.HTTPError as exc:
        # Never allow urllib's exception text to escape: it contains the
        # requested URL, and FRED credentials live in that URL's query.
        raise FreeMarketDataError(f"HTTP_ERROR:{exc.code}") from None
    except urllib.error.URLError:
        raise FreeMarketDataError("NETWORK_ERROR:URL_ERROR") from None
    except TimeoutError:
        raise FreeMarketDataError("NETWORK_ERROR:TIMEOUT") from None
    except OSError as exc:
        raise FreeMarketDataError(f"NETWORK_ERROR:{type(exc).__name__}") from None


def fetch_fred(api_key: str, observed_at: dt.datetime, getter=_get) -> tuple[bytes, dict]:
    start = (observed_at.date() - dt.timedelta(days=60)).isoformat()
    query = urllib.parse.urlencode({
        "series_id": "VIXCLS", "api_key": api_key, "file_type": "json",
        "observation_start": start,
    })
    raw = getter("https://api.stlouisfed.org/fred/series/observations?" + query)
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FreeMarketDataError("FRED_JSON_INVALID") from exc
    rows = body.get("observations")
    if not isinstance(rows, list) or not rows:
        raise FreeMarketDataError("FRED_OBSERVATIONS_MISSING")
    valid = [r for r in rows if isinstance(r, dict) and r.get("value") not in (None, ".")]
    if not valid:
        raise FreeMarketDataError("FRED_VALUES_MISSING")
    latest = valid[-1]
    return raw, {
        "series_id": "VIXCLS", "observation_date": latest["date"],
        "value": latest["value"], "realtime_start": latest.get("realtime_start"),
        "realtime_end": latest.get("realtime_end"),
    }


FRED_LIQUIDITY_UNITS = {
    "Millions of Dollars": ("Millions of U.S. Dollars", Decimal("1")),
    "Millions of U.S. Dollars": ("Millions of U.S. Dollars", Decimal("1")),
    "Billions of Dollars": ("Millions of U.S. Dollars", Decimal("1000")),
    "Billions of U.S. Dollars": ("Millions of U.S. Dollars", Decimal("1000")),
}


def _decimal(value: object, code: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FreeMarketDataError(code) from exc
    if not parsed.is_finite():
        raise FreeMarketDataError(code)
    return parsed


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def fetch_fred_liquidity(
    api_key: str,
    observed_at: dt.datetime,
    series_ids: list[str],
    getter=_get,
) -> dict:
    """Fetch current official liquidity observations without retaining raw bytes.

    Response hashes and minimum source metadata remain in the derived packet;
    the response bodies are discarded after deterministic normalization.
    """
    start = (observed_at.date() - dt.timedelta(days=180)).isoformat()
    rows = []
    response_hashes = {}
    for series_id in series_ids:
        metadata_query = urllib.parse.urlencode({
            "series_id": series_id, "api_key": api_key, "file_type": "json",
        })
        observations_query = urllib.parse.urlencode({
            "series_id": series_id, "api_key": api_key, "file_type": "json",
            "observation_start": start,
        })
        metadata_raw = getter(
            "https://api.stlouisfed.org/fred/series?" + metadata_query
        )
        observations_raw = getter(
            "https://api.stlouisfed.org/fred/series/observations?" + observations_query
        )
        try:
            metadata_body = json.loads(metadata_raw)
            observations_body = json.loads(observations_raw)
        except json.JSONDecodeError as exc:
            raise FreeMarketDataError(
                f"FRED_LIQUIDITY_JSON_INVALID:{series_id}"
            ) from exc
        metadata_rows = metadata_body.get("seriess")
        observations = observations_body.get("observations")
        if not isinstance(metadata_rows, list) or len(metadata_rows) != 1:
            raise FreeMarketDataError(
                f"FRED_LIQUIDITY_METADATA_INVALID:{series_id}"
            )
        if not isinstance(observations, list):
            raise FreeMarketDataError(
                f"FRED_LIQUIDITY_OBSERVATIONS_INVALID:{series_id}"
            )
        metadata = metadata_rows[0]
        units = metadata.get("units")
        unit_base = units.split(",", 1)[0].strip() if isinstance(units, str) else None
        if unit_base not in FRED_LIQUIDITY_UNITS:
            raise FreeMarketDataError(
                f"FRED_LIQUIDITY_UNITS_INVALID:{series_id}"
            )
        normalized_unit, factor = FRED_LIQUIDITY_UNITS[unit_base]
        valid = [
            row for row in observations
            if isinstance(row, dict) and row.get("value") not in (None, ".")
        ]
        if len(valid) < 2:
            raise FreeMarketDataError(
                f"FRED_LIQUIDITY_HISTORY_INSUFFICIENT:{series_id}"
            )
        previous, latest = valid[-2], valid[-1]
        previous_value = _decimal(previous["value"], "FRED_LIQUIDITY_VALUE_INVALID") * factor
        latest_value = _decimal(latest["value"], "FRED_LIQUIDITY_VALUE_INVALID") * factor
        metadata_sha = sha256_bytes(metadata_raw)
        observations_sha = sha256_bytes(observations_raw)
        response_hashes[series_id] = {
            "metadata_response_sha256": metadata_sha,
            "observations_response_sha256": observations_sha,
        }
        rows.append({
            "series_id": series_id,
            "title": metadata.get("title"),
            "frequency": metadata.get("frequency"),
            "source_unit": units,
            "normalized_unit": normalized_unit,
            "normalization_factor": _decimal_text(factor),
            "observation_date": latest.get("date"),
            "value": _decimal_text(latest_value),
            "previous_observation_date": previous.get("date"),
            "previous_value": _decimal_text(previous_value),
            "change": _decimal_text(latest_value - previous_value),
            "realtime_start": latest.get("realtime_start"),
            "realtime_end": latest.get("realtime_end"),
            "metadata_response_sha256": metadata_sha,
            "observations_response_sha256": observations_sha,
        })
    return {
        "status": "READY",
        "derivation_version": "fred_liquidity_current/v1",
        "source_scope": "FRED_OFFICIAL_SERIES_API",
        "raw_retention": "TRANSIENT_NOT_PERSISTED_HASH_ATTESTED",
        "captured_at_utc": observed_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "series": rows,
        "response_hashes": response_hashes,
        "derived_payload_sha256": sha256_bytes(canonical_bytes(rows)),
        "warnings": ["CURRENT_SNAPSHOT_ONLY_NOT_HISTORICAL_PIT_REPLAY"],
    }


def fetch_alpaca(key: str, secret: str, symbols: list[str], getter=_get) -> tuple[bytes, list[dict]]:
    query = urllib.parse.urlencode({"symbols": ",".join(symbols), "feed": "iex"})
    raw = getter(
        "https://data.alpaca.markets/v2/stocks/bars/latest?" + query,
        {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
    )
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FreeMarketDataError("ALPACA_JSON_INVALID") from exc
    bars = body.get("bars")
    if not isinstance(bars, dict):
        raise FreeMarketDataError("ALPACA_BARS_MISSING")
    normalized = []
    for symbol in symbols:
        bar = bars.get(symbol)
        if not isinstance(bar, dict):
            continue
        if not all(k in bar for k in ("c", "v", "t")):
            raise FreeMarketDataError(f"ALPACA_BAR_FIELDS_MISSING:{symbol}")
        normalized.append({"symbol": symbol, "close": str(bar["c"]), "volume": str(bar["v"]), "provider_timestamp": bar["t"]})
    if not normalized:
        raise FreeMarketDataError("ALPACA_NO_SYMBOLS_RETURNED")
    return raw, normalized


def fetch_alpaca_daily_bars(key: str, secret: str, symbols: list[str], observed_at: dt.datetime, getter=_get) -> tuple[bytes, list[dict]]:
    """Capture a bounded, IEX-only daily OHLCV window for read-only charting."""
    start = (observed_at.astimezone(UTC).date() - dt.timedelta(days=180)).isoformat()
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    responses: dict[str, object] = {}
    normalized: list[dict] = []
    for symbol in symbols:
        query = urllib.parse.urlencode({
            "timeframe": "1Day", "start": start, "end": observed_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 240, "adjustment": "raw", "feed": "iex", "sort": "asc",
        })
        raw = getter(f"https://data.alpaca.markets/v2/stocks/{symbol}/bars?" + query, headers)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FreeMarketDataError(f"ALPACA_DAILY_JSON_INVALID:{symbol}") from exc
        bars = body.get("bars")
        if not isinstance(bars, list):
            raise FreeMarketDataError(f"ALPACA_DAILY_BARS_MISSING:{symbol}")
        responses[symbol] = body
        for bar in bars:
            if not isinstance(bar, dict) or not all(key in bar for key in ("o", "h", "l", "c", "v", "t")):
                raise FreeMarketDataError(f"ALPACA_DAILY_BAR_FIELDS_MISSING:{symbol}")
            values = [bar["o"], bar["h"], bar["l"], bar["c"], bar["v"]]
            try:
                numeric = [float(value) for value in values]
            except (TypeError, ValueError) as exc:
                raise FreeMarketDataError(f"ALPACA_DAILY_BAR_VALUES_INVALID:{symbol}") from exc
            if not all(value >= 0 for value in numeric) or min(numeric[:4]) <= 0 or numeric[1] < max(numeric[0], numeric[3]) or numeric[2] > min(numeric[0], numeric[3]):
                raise FreeMarketDataError(f"ALPACA_DAILY_OHLC_INVALID:{symbol}")
            if not isinstance(bar["t"], str) or not bar["t"]:
                raise FreeMarketDataError(f"ALPACA_DAILY_TIME_INVALID:{symbol}")
            normalized.append({
                "symbol": symbol, "opened_at": bar["t"], "open": str(bar["o"]), "high": str(bar["h"]),
                "low": str(bar["l"]), "close": str(bar["c"]), "volume": str(bar["v"]),
            })
    if not normalized:
        raise FreeMarketDataError("ALPACA_DAILY_NO_BARS_RETURNED")
    return canonical_bytes({"responses": responses}), normalized


def _session_return(closes: list[Decimal], sessions: int) -> str:
    if len(closes) <= sessions or closes[-(sessions + 1)] == 0:
        raise FreeMarketDataError(f"ALPACA_RETURN_HISTORY_INSUFFICIENT:{sessions}")
    value = ((closes[-1] / closes[-(sessions + 1)]) - Decimal("1")) * Decimal("100")
    return _decimal_text(value.quantize(Decimal("0.0001")))


def derive_us_market_reference(daily_bars: list[dict], contract: dict) -> dict:
    """Derive objective ETF returns only; never emit a market label."""
    grouped: dict[str, list[dict]] = {}
    for row in daily_bars:
        if not isinstance(row, dict) or not isinstance(row.get("symbol"), str):
            raise FreeMarketDataError("ALPACA_DAILY_NORMALIZED_INVALID")
        grouped.setdefault(row["symbol"], []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: row.get("opened_at", ""))

    windows = contract["alpaca"]["return_windows_sessions"]
    required = sorted(set(
        contract["alpaca"]["trend_symbols"]
        + contract["alpaca"]["sector_reference_symbols"]
    ))
    observations = {}
    missing = []
    for symbol in required:
        rows = grouped.get(symbol, [])
        try:
            closes = [_decimal(row["close"], "ALPACA_CLOSE_INVALID") for row in rows]
            returns = {
                f"{window}_session_pct": _session_return(closes, window)
                for window in windows
            }
        except (KeyError, FreeMarketDataError):
            missing.append(symbol)
            continue
        observations[symbol] = {
            "symbol": symbol,
            "as_of_session_date": str(rows[-1]["opened_at"])[:10],
            "close": _decimal_text(closes[-1]),
            "available_session_count": len(rows),
            "returns": returns,
        }

    spy = observations.get("SPY")
    sectors = []
    for symbol in contract["alpaca"]["sector_reference_symbols"]:
        row = observations.get(symbol)
        if row is None or spy is None:
            continue
        relative = {}
        for window in windows:
            key = f"{window}_session_pct"
            relative[key] = _decimal_text(
                (_decimal(row["returns"][key], "ALPACA_RETURN_INVALID")
                 - _decimal(spy["returns"][key], "ALPACA_RETURN_INVALID"))
                .quantize(Decimal("0.0001"))
            )
        sectors.append({**row, "relative_to_spy_pct": relative})

    trend = [
        observations[symbol]
        for symbol in contract["alpaca"]["trend_symbols"]
        if symbol in observations
    ]
    status = "READY" if not missing else "PARTIAL"
    as_of_dates = sorted({row["as_of_session_date"] for row in trend})
    result = {
        "schema_version": "us_market_reference/v1",
        "status": status,
        "as_of_session_date": as_of_dates[-1] if len(as_of_dates) == 1 else None,
        "trend_etfs": trend,
        "sector_etfs": sectors,
        "coverage": {
            "required_symbols": required,
            "observed_symbols": sorted(observations),
            "missing_symbols": missing,
            "ratio": f"{len(observations)}/{len(required)}",
        },
        "source_scope": "ALPACA_IEX_DAILY_BARS_PARTIAL_EXCHANGE_REFERENCE",
        "interpretation": "OBSERVED_UNCLASSIFIED",
        "warnings": [
            "NOT_US_BREADTH",
            "NOT_CANONICAL_US_LEADERSHIP",
            "REGIME_INTERPRETATION_UNAUTHORIZED",
        ],
    }
    result["payload_sha256"] = sha256_bytes(canonical_bytes(result))
    return result


def validate_alpaca_daily_evidence(root: Path, packet: dict) -> dict:
    """Replay the retained daily response and reproduce the US ETF reference."""
    observed = packet.get("observed_at_utc")
    if not isinstance(observed, str) or len(observed) < 10:
        raise FreeMarketDataError("CAPTURE_TIME_INVALID")
    day = observed[:10]
    relative = Path("evidence/free_market_data/raw") / day / "alpaca_iex_daily_bars.json.gz"
    path = (Path(root).resolve() / relative).resolve()
    try:
        path.relative_to(Path(root).resolve())
        compressed = path.read_bytes()
        raw = gzip.decompress(compressed)
    except (OSError, ValueError, gzip.BadGzipFile) as exc:
        raise FreeMarketDataError("ALPACA_DAILY_RAW_INVALID") from exc
    if sha256_bytes(raw) != packet.get("alpaca", {}).get("daily_raw_sha256"):
        raise FreeMarketDataError("ALPACA_DAILY_RAW_HASH_MISMATCH")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FreeMarketDataError("ALPACA_DAILY_RAW_JSON_INVALID") from exc
    responses = body.get("responses")
    if not isinstance(responses, dict):
        raise FreeMarketDataError("ALPACA_DAILY_RAW_RESPONSES_INVALID")
    normalized = []
    for symbol, response in responses.items():
        bars = response.get("bars") if isinstance(response, dict) else None
        if not isinstance(bars, list):
            raise FreeMarketDataError(f"ALPACA_DAILY_RAW_BARS_INVALID:{symbol}")
        for bar in bars:
            if not isinstance(bar, dict) or not all(
                key in bar for key in ("o", "h", "l", "c", "v", "t")
            ):
                raise FreeMarketDataError(f"ALPACA_DAILY_RAW_FIELDS_INVALID:{symbol}")
            normalized.append({
                "symbol": symbol,
                "opened_at": bar["t"],
                "open": str(bar["o"]),
                "high": str(bar["h"]),
                "low": str(bar["l"]),
                "close": str(bar["c"]),
                "volume": str(bar["v"]),
            })
    expected_daily = packet.get("alpaca", {}).get("daily_bars")
    if not isinstance(expected_daily, list) or sorted(
        normalized, key=lambda row: (row["symbol"], row["opened_at"])
    ) != sorted(
        expected_daily, key=lambda row: (row.get("symbol", ""), row.get("opened_at", ""))
    ):
        raise FreeMarketDataError("ALPACA_DAILY_REDERIVATION_MISMATCH")
    contract = load_contract(Path(root) / "config/free_market_data_contract.json")
    reference = derive_us_market_reference(normalized, contract)
    if reference != packet.get("us_market_reference"):
        raise FreeMarketDataError("US_MARKET_REFERENCE_REDERIVATION_MISMATCH")
    return {
        "reference": reference,
        "raw_path": relative.as_posix(),
        "raw_response_sha256": sha256_bytes(raw),
    }


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    os.replace(temp, path)


def build_capture(
    observed_at: dt.datetime,
    fred_raw: bytes,
    fred: dict,
    contract: dict,
    *,
    fred_evidence: dict,
    fred_liquidity: dict,
    alpaca_status: str,
    alpaca_raw: bytes | None = None,
    bars: list[dict] | None = None,
    daily_raw: bytes | None = None,
    daily_bars: list[dict] | None = None,
) -> dict:
    observed = observed_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    bars = bars or []
    daily_bars = daily_bars or []
    if alpaca_status == "READY":
        if alpaca_raw is None or daily_raw is None or not bars or not daily_bars:
            raise FreeMarketDataError("ALPACA_READY_EVIDENCE_INCOMPLETE")
    elif alpaca_raw is not None or daily_raw is not None or bars or daily_bars:
        raise FreeMarketDataError("ALPACA_BLOCKED_MUST_NOT_CARRY_EVIDENCE")
    us_market_reference = (
        derive_us_market_reference(daily_bars, contract)
        if alpaca_status == "READY"
        else {
            "schema_version": "us_market_reference/v1",
            "status": "BLOCKED",
            "reason": alpaca_status,
            "trend_etfs": [],
            "sector_etfs": [],
            "coverage": {
                "required_symbols": sorted(set(
                    contract["alpaca"]["trend_symbols"]
                    + contract["alpaca"]["sector_reference_symbols"]
                )),
                "observed_symbols": [],
                "missing_symbols": sorted(set(
                    contract["alpaca"]["trend_symbols"]
                    + contract["alpaca"]["sector_reference_symbols"]
                )),
                "ratio": "0/15",
            },
            "source_scope": "ALPACA_IEX_DAILY_BARS_PARTIAL_EXCHANGE_REFERENCE",
            "interpretation": "OBSERVED_UNCLASSIFIED",
            "warnings": ["REGIME_INTERPRETATION_UNAUTHORIZED"],
        }
    )
    packet = {
        "schema_version": "free_market_data_capture/5",
        "contract_version": contract["contract_version"],
        "observed_at_utc": observed,
        "fred": {
            **fred,
            "status": "READY",
            "source_scope": contract["fred"]["source_scope"],
            "response_sha256": sha256_bytes(fred_raw),
            "raw_retention": contract["fred"]["raw_retention"],
            "evidence": fred_evidence,
        },
        "fred_liquidity": fred_liquidity,
        "alpaca": {
            "status": alpaca_status,
            "feed": "iex",
            "source_scope": contract["alpaca"]["source_scope"],
            "bars": bars,
            "raw_sha256": sha256_bytes(alpaca_raw) if alpaca_raw is not None else None,
            "daily_bars": daily_bars,
            "daily_raw_sha256": sha256_bytes(daily_raw) if daily_raw is not None else None,
            "daily_timeframe": "1Day",
            "daily_adjustment": "raw",
        },
        "us_market_reference": us_market_reference,
        "authority": contract["authority"],
    }
    packet["packet_sha256"] = sha256_bytes(canonical_bytes(packet))
    return packet


def publish(
    root: Path,
    observed_at: dt.datetime,
    packet: dict,
    *,
    fred_bundle: dict,
    alpaca_raw: bytes | None = None,
    daily_raw: bytes | None = None,
) -> None:
    day = observed_at.astimezone(UTC).date().isoformat()
    derived_dir = root / "evidence" / "free_market_data" / "derived" / day
    FRED_PROVENANCE.publish_evidence_bundle(root, fred_bundle)
    _atomic_write(derived_dir / "manifest.json", json.dumps(packet, indent=2, sort_keys=True).encode() + b"\n")
    if packet["alpaca"]["status"] == "READY":
        if alpaca_raw is None or daily_raw is None:
            raise FreeMarketDataError("ALPACA_READY_RAW_MISSING")
        raw_dir = root / "evidence" / "free_market_data" / "raw" / day
        _atomic_write(raw_dir / "alpaca_iex_latest_bars.json.gz", gzip.compress(alpaca_raw, mtime=0))
        _atomic_write(raw_dir / "alpaca_iex_daily_bars.json.gz", gzip.compress(daily_raw, mtime=0))
    _atomic_write(root / "data" / "latest_free_market_data.json", json.dumps(packet, indent=2, sort_keys=True).encode() + b"\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    fred_key = os.getenv("FRED_API_KEY", "").strip()
    if not fred_key:
        raise SystemExit("FREE_MARKET_DATA_CREDENTIALS_MISSING")
    # ★ 2026-08-23 cutover: the account/trading Alpaca credential
    # (`ALPACA_API_KEY`/`ALPACA_API_SECRET`) now lives ONLY in the private
    # `atlas-private-evidence` repo (see portfolio_risk/). This collector
    # is a DIFFERENT, market-data-only consumer and must never fall back
    # to reusing that name or that credential -- it requires its own,
    # dedicated, disposable market-data-only credential under a
    # DIFFERENT env var name. There is no code path here that even reads
    # `ALPACA_API_KEY`/`ALPACA_API_SECRET` any more.
    alpaca_key = os.getenv("ALPACA_MARKET_DATA_API_KEY", "").strip()
    alpaca_secret = os.getenv("ALPACA_MARKET_DATA_API_SECRET", "").strip()
    observed_at = dt.datetime.now(UTC).replace(microsecond=0)
    contract = load_contract(args.root / "config" / "free_market_data_contract.json")
    fred_raw, fred = fetch_fred(fred_key, observed_at)
    fred_bundle = FRED_PROVENANCE.build_evidence_bundle(observed_at, fred_raw)
    normalized_fred = {
        "series_id": fred.get("series_id"),
        "observation_date": fred.get("observation_date"),
        "value": fred.get("value"),
        "realtime_start": fred.get("realtime_start"),
        "realtime_end": fred.get("realtime_end"),
    }
    if fred_bundle["manifest"]["observation"] != normalized_fred:
        raise FreeMarketDataError("FRED_DERIVATION_MISMATCH")
    try:
        fred_liquidity = fetch_fred_liquidity(
            fred_key,
            observed_at,
            contract["fred"]["liquidity_series"],
        )
    except FreeMarketDataError as exc:
        fred_liquidity = {
            "status": f"FRED_LIQUIDITY_CAPTURE_FAILED:{exc}",
            "derivation_version": "fred_liquidity_current/v1",
            "source_scope": "FRED_OFFICIAL_SERIES_API",
            "raw_retention": "TRANSIENT_NOT_PERSISTED_HASH_ATTESTED",
            "captured_at_utc": observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "series": [],
            "response_hashes": {},
            "derived_payload_sha256": sha256_bytes(canonical_bytes([])),
            "warnings": ["CURRENT_SNAPSHOT_ONLY_NOT_HISTORICAL_PIT_REPLAY"],
        }
    alpaca_raw = daily_raw = None
    bars: list[dict] = []
    daily_bars: list[dict] = []
    if bool(alpaca_key) != bool(alpaca_secret):
        alpaca_status = "BLOCKED_BY_INCOMPLETE_DEDICATED_MARKET_DATA_CREDENTIAL"
    elif not alpaca_key:
        alpaca_status = "BLOCKED_BY_DEDICATED_MARKET_DATA_CREDENTIAL"
    else:
        try:
            alpaca_raw, bars = fetch_alpaca(
                alpaca_key, alpaca_secret, contract["alpaca"]["symbols"]
            )
            daily_raw, daily_bars = fetch_alpaca_daily_bars(
                alpaca_key,
                alpaca_secret,
                contract["alpaca"]["symbols"],
                observed_at,
            )
            alpaca_status = "READY"
        except FreeMarketDataError as exc:
            # One provider failure must not erase an independent valid FRED
            # observation. The failed component carries no rows or raw bytes.
            alpaca_raw = daily_raw = None
            bars = []
            daily_bars = []
            alpaca_status = f"ALPACA_CAPTURE_FAILED:{exc}"
    packet = build_capture(
        observed_at,
        fred_raw,
        fred,
        contract,
        fred_evidence=fred_bundle["pointer"],
        fred_liquidity=fred_liquidity,
        alpaca_status=alpaca_status,
        alpaca_raw=alpaca_raw,
        bars=bars,
        daily_raw=daily_raw,
        daily_bars=daily_bars,
    )
    publish(
        args.root,
        observed_at,
        packet,
        fred_bundle=fred_bundle,
        alpaca_raw=alpaca_raw,
        daily_raw=daily_raw,
    )
    status = "PASS" if alpaca_status == "READY" else "PARTIAL"
    print(json.dumps({
        "status": status,
        "alpaca_status": alpaca_status,
        "packet_sha256": packet["packet_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
