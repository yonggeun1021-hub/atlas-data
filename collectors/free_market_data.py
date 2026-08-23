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
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise FreeMarketDataError(f"HTTP_STATUS:{response.status}")
        return response.read()


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


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    os.replace(temp, path)


def build_capture(observed_at: dt.datetime, fred_raw: bytes, fred: dict, alpaca_raw: bytes, bars: list[dict], contract: dict) -> dict:
    observed = observed_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    packet = {
        "schema_version": "free_market_data_capture/1",
        "contract_version": contract["contract_version"],
        "observed_at_utc": observed,
        "fred": {**fred, "source_scope": contract["fred"]["source_scope"], "raw_sha256": sha256_bytes(fred_raw)},
        "alpaca": {"feed": "iex", "source_scope": contract["alpaca"]["source_scope"], "bars": bars, "raw_sha256": sha256_bytes(alpaca_raw)},
        "authority": contract["authority"],
    }
    packet["packet_sha256"] = sha256_bytes(canonical_bytes(packet))
    return packet


def publish(root: Path, observed_at: dt.datetime, fred_raw: bytes, alpaca_raw: bytes, packet: dict) -> None:
    day = observed_at.astimezone(UTC).date().isoformat()
    raw_dir = root / "evidence" / "free_market_data" / "raw" / day
    _atomic_write(raw_dir / "fred_vixcls.json.gz", gzip.compress(fred_raw, mtime=0))
    _atomic_write(raw_dir / "alpaca_iex_latest_bars.json.gz", gzip.compress(alpaca_raw, mtime=0))
    _atomic_write(raw_dir / "manifest.json", json.dumps(packet, indent=2, sort_keys=True).encode() + b"\n")
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
    if not all((alpaca_key, alpaca_secret)):
        # ★ Fail-closed, explicit, non-silent: this is not "missing config"
        # in the generic sense -- it specifically means the dedicated
        # market-data-only credential has not been provisioned yet. Never
        # skip the Alpaca leg silently, never fabricate a placeholder
        # price, never treat this as PASS.
        raise SystemExit("BLOCKED_BY_DEDICATED_MARKET_DATA_CREDENTIAL")
    observed_at = dt.datetime.now(UTC).replace(microsecond=0)
    contract = load_contract(args.root / "config" / "free_market_data_contract.json")
    fred_raw, fred = fetch_fred(fred_key, observed_at)
    alpaca_raw, bars = fetch_alpaca(alpaca_key, alpaca_secret, contract["alpaca"]["symbols"])
    packet = build_capture(observed_at, fred_raw, fred, alpaca_raw, bars, contract)
    publish(args.root, observed_at, fred_raw, alpaca_raw, packet)
    print(json.dumps({"status": "PASS", "packet_sha256": packet["packet_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
