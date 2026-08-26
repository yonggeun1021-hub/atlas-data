#!/usr/bin/env python3
"""Capture free FRED VIX and Alpaca IEX evidence without trading authority."""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
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


class FreeMarketDataError(ValueError):
    pass


def canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("contract_version") != "free_market_data/1":
        raise FreeMarketDataError("CONTRACT_VERSION_INVALID")
    if value.get("alpaca", {}).get("feed") != "iex":
        raise FreeMarketDataError("ALPACA_FEED_MUST_BE_IEX")
    if value.get("fred", {}).get("raw_retention") != "TRANSIENT_NOT_PERSISTED":
        raise FreeMarketDataError("FRED_RAW_RETENTION_INVALID")
    if value.get("fred", {}).get("partial_publish_authorized") is not True:
        raise FreeMarketDataError("FRED_PARTIAL_PUBLISH_NOT_AUTHORIZED")
    if value.get("alpaca", {}).get("credential_scope") != "DEDICATED_MARKET_DATA_ONLY":
        raise FreeMarketDataError("ALPACA_CREDENTIAL_SCOPE_INVALID")
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
    packet = {
        "schema_version": "free_market_data_capture/3",
        "contract_version": contract["contract_version"],
        "observed_at_utc": observed,
        "fred": {
            **fred,
            "status": "READY",
            "source_scope": contract["fred"]["source_scope"],
            "response_sha256": sha256_bytes(fred_raw),
            "raw_retention": contract["fred"]["raw_retention"],
        },
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
        "authority": contract["authority"],
    }
    packet["packet_sha256"] = sha256_bytes(canonical_bytes(packet))
    return packet


def publish(
    root: Path,
    observed_at: dt.datetime,
    packet: dict,
    *,
    alpaca_raw: bytes | None = None,
    daily_raw: bytes | None = None,
) -> None:
    day = observed_at.astimezone(UTC).date().isoformat()
    derived_dir = root / "evidence" / "free_market_data" / "derived" / day
    # FRED response bytes are intentionally transient. Only derived value,
    # vintage metadata and the response digest are retained.
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
