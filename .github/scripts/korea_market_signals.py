#!/usr/bin/env python3
"""Build a policy-neutral Korea market five-signal observation from KRX.

Raw KRX response bytes and per-symbol rows are used only in memory. The
tracked packet retains aggregate measurements and response hashes only.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "korea_market_signals_contract.json"
LEADERSHIP_POLICY_PATH = ROOT / "config" / "korea_leadership_policy.json"
OBSERVATION_ROOT = ROOT / "data" / "observations" / "korea_market_signals"
LATEST_PATH = ROOT / "data" / "latest_korea_market_signals.json"
KST = ZoneInfo("Asia/Seoul")
DATE8 = re.compile(r"^[0-9]{8}$")
DATE10 = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
UTC_SECOND = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MARKETS = ("kospi", "kosdaq")
SCHEMA_VERSION = "korea_market_signals_observation/1"


class KoreaMarketSignalsError(ValueError):
    """An official KRX observation cannot be safely constructed."""


def fail(code: str, detail: str = "") -> None:
    suffix = f":{detail}" if detail else ""
    raise KoreaMarketSignalsError(f"{code}{suffix}")


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        fail("CANONICAL_JSON_INVALID", str(exc))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KoreaMarketSignalsError(code) from exc
    if not isinstance(value, dict):
        fail(code, "object required")
    return value


def latest_confirmed_krx_session(
    path: Path,
    *,
    expected_collected_for_kst_date: str,
) -> str:
    """Return the one confirmed KRX session from the current daily collector.

    The daily collector is an independent confirmation source.  A signals
    packet may be newer than it, but it must never be older while still being
    published as the latest observation.
    """
    value = _read_json(path, "KRX_CONFIRMATION_SOURCE_INVALID")
    if DATE10.fullmatch(str(expected_collected_for_kst_date)) is None:
        fail("KRX_CONFIRMATION_EXPECTED_DATE_INVALID")
    if value.get("collected_for_kst_date") != expected_collected_for_kst_date:
        fail(
            "KRX_CONFIRMATION_SOURCE_STALE",
            f"expected={expected_collected_for_kst_date}:observed={value.get('collected_for_kst_date')}",
        )
    stocks = value.get("stocks")
    if not isinstance(stocks, dict) or not stocks:
        fail("KRX_CONFIRMATION_SOURCE_INVALID", "stocks")
    if any(not isinstance(row, dict) or row.get("status") != "ok" for row in stocks.values()):
        fail("KRX_CONFIRMATION_SOURCE_INCOMPLETE")
    dates = {
        row.get("latest_trading_day")
        for row in stocks.values()
    }
    if len(dates) != 1:
        fail("KRX_CONFIRMATION_SESSION_AMBIGUOUS")
    confirmed = dates.pop()
    if not isinstance(confirmed, str) or DATE10.fullmatch(confirmed) is None:
        fail("KRX_CONFIRMATION_SESSION_INVALID")
    return confirmed


def require_not_older_than_confirmed_session(packet: dict, confirmed_date: str) -> None:
    packet = validate_packet(packet)
    if DATE10.fullmatch(str(confirmed_date)) is None:
        fail("KRX_CONFIRMATION_SESSION_INVALID")
    if packet["as_of_date"] < confirmed_date:
        fail(
            "KRX_MARKET_SIGNALS_STALE",
            f"confirmed={confirmed_date}:observed={packet['as_of_date']}",
        )


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(path, "CONTRACT_INVALID")
    if (
        value.get("schema_version") != 1
        or value.get("contract_version") != "korea_market_signals/1"
        or tuple(value.get("required_axes", []))
        != ("TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP")
        or set(value.get("stock_endpoints", {})) != set(MARKETS)
        or set(value.get("index_endpoints", {})) != set(MARKETS)
        or value.get("raw_persistence") != 0
        or value.get("per_symbol_persistence") != 0
    ):
        fail("CONTRACT_INVALID", "pinned semantics")
    authority = value.get("authority")
    if not isinstance(authority, dict) or authority.get("observation_only") is not True:
        fail("CONTRACT_INVALID", "authority")
    for key, enabled in authority.items():
        if key.endswith("_authorized") and enabled is not False:
            fail("CONTRACT_INVALID", f"authority.{key}")
    return copy.deepcopy(value)


def _parse_date8(value: str) -> dt.date:
    if not isinstance(value, str) or DATE8.fullmatch(value) is None:
        fail("DATE_FORMAT_INVALID")
    try:
        parsed = dt.datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise KoreaMarketSignalsError("DATE_CALENDAR_INVALID") from exc
    return parsed


def _date10(value: str) -> str:
    return _parse_date8(value).isoformat()


def _decimal(value: object, label: str, *, allow_blank: bool = False) -> Decimal | None:
    if value is None or not str(value).strip():
        if allow_blank:
            return None
        fail("NUMERIC_FIELD_MISSING", label)
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise KoreaMarketSignalsError(f"NUMERIC_FIELD_INVALID:{label}") from exc
    if not parsed.is_finite():
        fail("NUMERIC_FIELD_INVALID", label)
    return parsed


def _format(value: Decimal, places: int) -> str:
    quantum = Decimal(1).scaleb(-places)
    return format(value.quantize(quantum, rounding=ROUND_HALF_EVEN), "f")


def _ratio(numerator: Decimal, denominator: Decimal, places: int) -> str:
    if denominator == 0:
        fail("RATIO_DENOMINATOR_ZERO")
    return _format(numerator / denominator, places)


def _pct_change(previous: Decimal, current: Decimal, places: int) -> str:
    if previous == 0:
        fail("PERCENT_CHANGE_DENOMINATOR_ZERO")
    return _format((current / previous - Decimal(1)) * Decimal(100), places)


def _request(auth_key: str, endpoint: str, bas_dd: str) -> Request:
    key = str(auth_key or "").strip()
    if not key:
        fail("KRX_API_KEY_MISSING")
    _parse_date8(bas_dd)
    return Request(
        endpoint + "?" + urlencode({"basDd": bas_dd}),
        headers={
            "AUTH_KEY": key,
            "Accept": "application/json",
            "User-Agent": "Atlas-Korea-Market-Signals/1.0",
        },
        method="GET",
    )


def _http(request: Request, opener=urlopen, timeout: int = 30) -> bytes:
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            body = response.read()
    except HTTPError as exc:
        raise KoreaMarketSignalsError(f"KRX_HTTP_ERROR:{exc.code}") from exc
    except URLError as exc:
        raise KoreaMarketSignalsError("KRX_NETWORK_ERROR") from exc
    if status != 200:
        fail("KRX_HTTP_ERROR", str(status))
    return body


def _decode(body: bytes) -> dict:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KoreaMarketSignalsError("KRX_RESPONSE_INVALID_JSON") from exc
    if not isinstance(value, dict):
        fail("KRX_RESPONSE_INVALID_ROOT")
    return value


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stock_snapshot(payload: dict, bas_dd: str, market: str) -> dict:
    rows = payload.get("OutBlock_1")
    if not isinstance(rows, list) or not rows:
        fail("KRX_RESPONSE_EMPTY", f"stock:{market}:{bas_dd}")
    members = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            fail("KRX_ROW_INVALID", f"stock:{market}:{index}")
        if str(row.get("BAS_DD")) != bas_dd:
            fail("KRX_DATE_MISMATCH", f"stock:{market}")
        identity = str(row.get("ISU_CD") or "").strip()
        if not identity or identity in members:
            fail("KRX_IDENTITY_INVALID", f"stock:{market}")
        members[identity] = {
            "close": _decimal(row.get("TDD_CLSPRC"), "TDD_CLSPRC"),
            "return_pct": _decimal(row.get("FLUC_RT"), "FLUC_RT"),
            "trading_value": _decimal(row.get("ACC_TRDVAL"), "ACC_TRDVAL"),
            "market_cap": _decimal(row.get("MKTCAP"), "MKTCAP"),
        }
    return {"market": market, "date": bas_dd, "members": members}


def _index_snapshot(payload: dict, bas_dd: str, market: str) -> dict:
    rows = payload.get("OutBlock_1")
    if not isinstance(rows, list) or not rows:
        fail("KRX_RESPONSE_EMPTY", f"index:{market}:{bas_dd}")
    indices = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            fail("KRX_ROW_INVALID", f"index:{market}:{index}")
        if str(row.get("BAS_DD")) != bas_dd:
            fail("KRX_DATE_MISMATCH", f"index:{market}")
        name = str(row.get("IDX_NM") or "").strip()
        close = row.get("CLSPRC_IDX")
        # KRX may include non-series/category rows in OutBlock_1. Those rows
        # have the requested date but no usable index close and must not make
        # the complete market response fail. A named numeric series remains
        # strict and duplicate identities still fail closed.
        if not name or close is None or not str(close).strip():
            continue
        if name in indices:
            fail("KRX_INDEX_IDENTITY_INVALID", f"index:{market}")
        indices[name] = _decimal(close, "CLSPRC_IDX")
    if not indices:
        fail("KRX_RESPONSE_EMPTY", f"index:{market}:{bas_dd}:usable_series")
    return {"market": market, "date": bas_dd, "indices": indices}


def fetch_family(
    auth_key: str,
    bas_dd: str,
    market: str,
    family: str,
    *,
    opener=urlopen,
    contract: dict | None = None,
) -> dict:
    contract = contract or load_contract()
    if market not in MARKETS or family not in ("stock", "index"):
        fail("SOURCE_FAMILY_INVALID", f"{family}:{market}")
    endpoint = contract[f"{family}_endpoints"][market]
    body = _http(_request(auth_key, endpoint, bas_dd), opener=opener)
    payload = _decode(body)
    snapshot = (
        _stock_snapshot(payload, bas_dd, market)
        if family == "stock"
        else _index_snapshot(payload, bas_dd, market)
    )
    return {
        **snapshot,
        "endpoint": endpoint,
        "response_sha256": hashlib.sha256(body).hexdigest(),
        "fetched_at_utc": _now_utc(),
    }


def fetch_complete_session(
    auth_key: str,
    bas_dd: str,
    *,
    opener=urlopen,
    contract: dict | None = None,
) -> dict:
    contract = contract or load_contract()
    result = {"date": bas_dd, "stock": {}, "index": {}}
    for family in ("stock", "index"):
        for market in MARKETS:
            result[family][market] = fetch_family(
                auth_key, bas_dd, market, family, opener=opener, contract=contract
            )
    return result


def discover_session_pair(
    auth_key: str,
    *,
    anchor: dt.date | None = None,
    opener=urlopen,
    contract: dict | None = None,
) -> tuple[dict, dict]:
    contract = contract or load_contract()
    anchor = anchor or dt.datetime.now(KST).date()
    sessions = []
    for offset in range(contract["maximum_session_search_calendar_days"]):
        day = (anchor - dt.timedelta(days=offset)).strftime("%Y%m%d")
        try:
            session = fetch_complete_session(
                auth_key, day, opener=opener, contract=contract
            )
        except KoreaMarketSignalsError as exc:
            if str(exc).startswith("KRX_RESPONSE_EMPTY:"):
                continue
            raise
        sessions.append(session)
        if len(sessions) == 2:
            return sessions[1], sessions[0]
    fail("TWO_COMPLETED_SESSIONS_NOT_FOUND")


def _active_leadership_identities(as_of_date: str, path: Path = LEADERSHIP_POLICY_PATH) -> set[str]:
    policy = _read_json(path, "LEADERSHIP_POLICY_INVALID")
    if policy.get("approval_status") != "RATIFIED":
        fail("LEADERSHIP_POLICY_UNRATIFIED")
    observed = dt.date.fromisoformat(as_of_date)
    identities = set()
    for row in policy.get("records", []):
        if not isinstance(row, dict):
            fail("LEADERSHIP_POLICY_INVALID", "record")
        start = dt.date.fromisoformat(row["effective_from"])
        end = dt.date.fromisoformat(row["effective_to"]) if row.get("effective_to") else None
        if start <= observed and (end is None or observed <= end):
            identities.add(row["series_identity"])
    if not identities:
        fail("LEADERSHIP_POLICY_EMPTY")
    return identities


def _source_lineage(previous: dict, current: dict) -> dict:
    result = {}
    for family in ("stock", "index"):
        result[family] = {}
        for market in MARKETS:
            result[family][market.upper()] = {
                "endpoint": current[family][market]["endpoint"],
                "previous_response_sha256": previous[family][market]["response_sha256"],
                "current_response_sha256": current[family][market]["response_sha256"],
                "previous_fetched_at_utc": previous[family][market]["fetched_at_utc"],
                "current_fetched_at_utc": current[family][market]["fetched_at_utc"],
            }
    return result


def _breadth(previous: dict, current: dict, places: int) -> dict:
    markets = {}
    total = {"advancing_count": 0, "declining_count": 0, "unchanged_count": 0}
    for market in MARKETS:
        before = previous["stock"][market]["members"]
        after = current["stock"][market]["members"]
        paired = sorted(set(before) & set(after))
        advancing = declining = unchanged = 0
        for identity in paired:
            left, right = before[identity]["close"], after[identity]["close"]
            if right > left:
                advancing += 1
            elif right < left:
                declining += 1
            else:
                unchanged += 1
        if not paired:
            fail("BREADTH_PAIRED_UNIVERSE_EMPTY", market)
        item = {
            "paired_count": len(paired),
            "advancing_count": advancing,
            "declining_count": declining,
            "unchanged_count": unchanged,
            "advance_fraction": _ratio(Decimal(advancing), Decimal(len(paired)), places),
            "decline_fraction": _ratio(Decimal(declining), Decimal(len(paired)), places),
        }
        markets[market.upper()] = item
        for key in total:
            total[key] += item[key]
    total["paired_count"] = sum(item["paired_count"] for item in markets.values())
    total["advance_fraction"] = _ratio(
        Decimal(total["advancing_count"]), Decimal(total["paired_count"]), places
    )
    total["decline_fraction"] = _ratio(
        Decimal(total["declining_count"]), Decimal(total["paired_count"]), places
    )
    return {"markets": markets, "combined": total}


def _trend(previous: dict, current: dict, contract: dict, places: int) -> dict:
    benchmarks = {}
    for market in MARKETS:
        name = contract["benchmark_names"][market]
        before = previous["index"][market]["indices"].get(name)
        after = current["index"][market]["indices"].get(name)
        if before is None or after is None:
            fail("BENCHMARK_INDEX_MISSING", f"{market}:{name}")
        benchmarks[market.upper()] = {
            "name": name,
            "one_session_return_pct": _pct_change(before, after, places),
        }
    return {"benchmarks": benchmarks}


def _risk_vol(current: dict, trend: dict, places: int) -> dict:
    markets = {}
    all_abs = []
    for market in MARKETS:
        moves = [abs(row["return_pct"]) for row in current["stock"][market]["members"].values()]
        if not moves:
            fail("RISK_CROSS_SECTION_EMPTY", market)
        all_abs.extend(moves)
        markets[market.upper()] = {
            "stock_count": len(moves),
            "mean_absolute_stock_move_pct": _format(sum(moves) / Decimal(len(moves)), places),
            "benchmark_absolute_move_pct": _format(
                abs(Decimal(trend["benchmarks"][market.upper()]["one_session_return_pct"])),
                places,
            ),
        }
    return {
        "markets": markets,
        "combined_mean_absolute_stock_move_pct": _format(
            sum(all_abs) / Decimal(len(all_abs)), places
        ),
    }


def _liquidity(previous: dict, current: dict, places: int) -> dict:
    markets = {}
    combined_previous_value = combined_current_value = Decimal(0)
    combined_previous_cap = combined_current_cap = Decimal(0)
    for market in MARKETS:
        before = previous["stock"][market]["members"].values()
        after = current["stock"][market]["members"].values()
        previous_value = sum((row["trading_value"] for row in before), Decimal(0))
        previous_cap = sum((row["market_cap"] for row in previous["stock"][market]["members"].values()), Decimal(0))
        current_value = sum((row["trading_value"] for row in after), Decimal(0))
        current_cap = sum((row["market_cap"] for row in current["stock"][market]["members"].values()), Decimal(0))
        markets[market.upper()] = {
            "previous_trading_value_krw": str(previous_value),
            "current_trading_value_krw": str(current_value),
            "trading_value_change_pct": _pct_change(previous_value, current_value, places),
            "previous_turnover_pct": _format(previous_value / previous_cap * Decimal(100), places),
            "current_turnover_pct": _format(current_value / current_cap * Decimal(100), places),
        }
        combined_previous_value += previous_value
        combined_current_value += current_value
        combined_previous_cap += previous_cap
        combined_current_cap += current_cap
    return {
        "markets": markets,
        "combined": {
            "previous_trading_value_krw": str(combined_previous_value),
            "current_trading_value_krw": str(combined_current_value),
            "trading_value_change_pct": _pct_change(
                combined_previous_value, combined_current_value, places
            ),
            "previous_turnover_pct": _format(
                combined_previous_value / combined_previous_cap * Decimal(100), places
            ),
            "current_turnover_pct": _format(
                combined_current_value / combined_current_cap * Decimal(100), places
            ),
        },
    }


def _leadership(previous: dict, current: dict, contract: dict, places: int) -> dict:
    as_of_date = _date10(current["date"])
    active = _active_leadership_identities(as_of_date)
    observations = []
    coverage = {}
    for market in MARKETS:
        benchmark_name = contract["benchmark_names"][market]
        before = previous["index"][market]["indices"]
        after = current["index"][market]["indices"]
        benchmark_return = Decimal(
            _pct_change(before[benchmark_name], after[benchmark_name], places)
        )
        expected = sorted(
            identity for identity in active if identity.startswith(f"{market.upper()}::")
        )
        available = 0
        for identity in expected:
            name = identity.split("::", 1)[1]
            if name == benchmark_name or name not in before or name not in after:
                continue
            available += 1
            sector_return = Decimal(_pct_change(before[name], after[name], places))
            observations.append({
                "market": market.upper(),
                "sector_name": name,
                "sector_return_pct": _format(sector_return, places),
                "relative_return_vs_benchmark_pct": _format(
                    sector_return - benchmark_return, places
                ),
            })
        coverage[market.upper()] = {
            "ratified_identity_count": len(expected),
            "observed_sector_count": available,
        }
    if not observations:
        fail("LEADERSHIP_OBSERVATION_EMPTY")
    by_relative = sorted(
        observations,
        key=lambda row: (Decimal(row["relative_return_vs_benchmark_pct"]), row["market"], row["sector_name"]),
    )
    return {
        "coverage": coverage,
        "largest_relative_returns": list(reversed(by_relative[-5:])),
        "smallest_relative_returns": by_relative[:5],
        "observations": sorted(observations, key=lambda row: (row["market"], row["sector_name"])),
        "investment_ranking_authorized": False,
    }


def build_packet(previous: dict, current: dict, contract: dict | None = None) -> dict:
    contract = contract or load_contract()
    if _parse_date8(previous.get("date")) >= _parse_date8(current.get("date")):
        fail("SESSION_PAIR_NOT_ORDERED")
    for family in ("stock", "index"):
        if set(previous.get(family, {})) != set(MARKETS) or set(current.get(family, {})) != set(MARKETS):
            fail("SESSION_FAMILY_INCOMPLETE", family)
    places = contract["output_decimal_places"]
    trend = _trend(previous, current, contract, places)
    axes = {
        "TREND": {"status": "OBSERVED", "measurement": trend},
        "BREADTH": {"status": "OBSERVED", "measurement": _breadth(previous, current, places)},
        "RISK_VOL": {"status": "OBSERVED", "measurement": _risk_vol(current, trend, places)},
        "LIQUIDITY": {"status": "OBSERVED", "measurement": _liquidity(previous, current, places)},
        "LEADERSHIP": {"status": "OBSERVED", "measurement": _leadership(previous, current, contract, places)},
    }
    fetched = [
        session[family][market]["fetched_at_utc"]
        for session in (previous, current)
        for family in ("stock", "index")
        for market in MARKETS
    ]
    generated_at = max(fetched)
    packet = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "status": "OBSERVED_UNCLASSIFIED",
        "market": "KOREA",
        "market_timezone": contract["market_timezone"],
        "previous_date": _date10(previous["date"]),
        "as_of_date": _date10(current["date"]),
        "generated_at": generated_at,
        "available_at": generated_at,
        "source": {
            "name": contract["source_name"],
            "tier": contract["source_tier"],
            "raw_persistence": contract["raw_persistence"],
            "per_symbol_persistence": contract["per_symbol_persistence"],
            "requests": _source_lineage(previous, current),
        },
        "axes": axes,
        "coverage": {
            "required_axes": list(contract["required_axes"]),
            "observed_axes": list(contract["required_axes"]),
            "observed_count": len(contract["required_axes"]),
            "required_count": len(contract["required_axes"]),
            "ratio": "5/5",
        },
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = contract or load_contract()
    if not isinstance(packet, dict):
        fail("PACKET_INVALID", "object required")
    digest = packet.get("payload_sha256")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256", None)
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None or payload_sha256(unsigned) != digest:
        fail("PACKET_HASH_INVALID")
    if (
        packet.get("schema_version") != SCHEMA_VERSION
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status") != "OBSERVED_UNCLASSIFIED"
        or packet.get("market") != "KOREA"
        or set(packet.get("axes", {})) != set(contract["required_axes"])
        or packet.get("coverage", {}).get("ratio") != "5/5"
    ):
        fail("PACKET_SEMANTICS_INVALID")
    if DATE10.fullmatch(str(packet.get("as_of_date"))) is None or DATE10.fullmatch(str(packet.get("previous_date"))) is None:
        fail("PACKET_DATE_INVALID")
    if packet["previous_date"] >= packet["as_of_date"]:
        fail("PACKET_DATE_INVALID")
    if UTC_SECOND.fullmatch(str(packet.get("generated_at"))) is None:
        fail("PACKET_TIMESTAMP_INVALID")
    if packet.get("authority") != contract["authority"]:
        fail("PACKET_AUTHORITY_INVALID")
    for axis in contract["required_axes"]:
        if packet["axes"][axis].get("status") != "OBSERVED":
            fail("PACKET_AXIS_INVALID", axis)
    return copy.deepcopy(packet)


def _write_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def publish(packet: dict, root: Path = ROOT) -> dict:
    packet = validate_packet(packet)
    root = Path(root)
    observation_path = root / "data" / "observations" / "korea_market_signals" / packet["as_of_date"] / "packet.json"
    latest_path = root / "data" / "latest_korea_market_signals.json"
    if observation_path.exists():
        existing = validate_packet(_read_json(observation_path, "EXISTING_PACKET_INVALID"))
        if existing != packet:
            fail("APPEND_ONLY_CONFLICT", packet["as_of_date"])
    else:
        _write_atomic(observation_path, packet)
    current = None
    if latest_path.exists():
        current = validate_packet(_read_json(latest_path, "LATEST_PACKET_INVALID"))
    if current is None or current["as_of_date"] <= packet["as_of_date"]:
        _write_atomic(latest_path, packet)
    return {"observation_path": str(observation_path), "latest_path": str(latest_path)}


def _existing_today(anchor: dt.date, root: Path = ROOT) -> dict | None:
    path = Path(root) / "data" / "observations" / "korea_market_signals" / anchor.isoformat() / "packet.json"
    return validate_packet(_read_json(path, "EXISTING_PACKET_INVALID")) if path.is_file() else None


def run(
    auth_key: str,
    *,
    previous_date: str | None = None,
    current_date: str | None = None,
    anchor: dt.date | None = None,
    opener=urlopen,
    root: Path = ROOT,
) -> dict:
    contract = load_contract()
    if bool(previous_date) != bool(current_date):
        fail("EXPLICIT_SESSION_PAIR_INCOMPLETE")
    anchor = anchor or dt.datetime.now(KST).date()
    if previous_date and current_date:
        existing_path = Path(root) / "data" / "observations" / "korea_market_signals" / _date10(current_date) / "packet.json"
        if existing_path.is_file():
            packet = validate_packet(_read_json(existing_path, "EXISTING_PACKET_INVALID"), contract)
            return {"packet": packet, "publish": publish(packet, root), "reused": True}
        previous = fetch_complete_session(auth_key, previous_date, opener=opener, contract=contract)
        current = fetch_complete_session(auth_key, current_date, opener=opener, contract=contract)
    else:
        existing = _existing_today(anchor, root)
        if existing is not None:
            return {"packet": existing, "publish": publish(existing, root), "reused": True}
        previous, current = discover_session_pair(
            auth_key, anchor=anchor, opener=opener, contract=contract
        )
        discovered_path = Path(root) / "data" / "observations" / "korea_market_signals" / _date10(current["date"]) / "packet.json"
        if discovered_path.is_file():
            packet = validate_packet(_read_json(discovered_path, "EXISTING_PACKET_INVALID"), contract)
            return {"packet": packet, "publish": publish(packet, root), "reused": True}
    packet = build_packet(previous, current, contract)
    return {"packet": packet, "publish": publish(packet, root), "reused": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-date")
    parser.add_argument("--current-date")
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--confirmed-session-source", type=Path)
    parser.add_argument("--expected-collected-for-kst-date")
    args = parser.parse_args()
    if bool(args.confirmed_session_source) != bool(args.expected_collected_for_kst_date):
        fail("KRX_CONFIRMATION_ARGUMENTS_INCOMPLETE")
    if args.verify:
        packet = validate_packet(_read_json(args.verify, "PACKET_INVALID"))
        if args.confirmed_session_source:
            confirmed = latest_confirmed_krx_session(
                args.confirmed_session_source,
                expected_collected_for_kst_date=args.expected_collected_for_kst_date,
            )
            require_not_older_than_confirmed_session(packet, confirmed)
        print(f"PASS_KOREA_MARKET_SIGNALS_VERIFIED:{packet['as_of_date']}:{packet['coverage']['ratio']}")
        return 0
    result = run(
        os.environ.get("KRX_API_KEY", ""),
        previous_date=args.previous_date,
        current_date=args.current_date,
    )
    packet = result["packet"]
    if args.confirmed_session_source:
        confirmed = latest_confirmed_krx_session(
            args.confirmed_session_source,
            expected_collected_for_kst_date=args.expected_collected_for_kst_date,
        )
        require_not_older_than_confirmed_session(packet, confirmed)
    mode = "REUSED" if result["reused"] else "PUBLISHED"
    print(f"PASS_KOREA_MARKET_SIGNALS_{mode}:{packet['as_of_date']}:{packet['coverage']['ratio']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
