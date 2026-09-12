#!/usr/bin/env python3
"""Build a non-runtime KR PAPER reference from KRX information-system rows.

This is a bounded source-recovery candidate for review.  Per-security and
per-index rows stay in memory; the artifact retains aggregate measurements
and normalized-frame hashes only.  It cannot open Regime runtime, strategy,
order, trading, or REAL authority.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import re
from decimal import Decimal
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


SIGNALS = load_module("kr_signals_candidate_source", ROOT / ".github/scripts/korea_market_signals.py")
REFERENCE = load_module("kr_signals_candidate_reference", ROOT / "regime/paper_regime_reference.py")
CAPTURE = load_module(
    "kr_signals_candidate_capture",
    ROOT / "regime/krx_information_system_capture.py",
)
MARKETS = ("kospi", "kosdaq")
PYKRX_INDEX_NAME_FILTER = re.compile(r"[^-\w\.]")


class CandidateError(ValueError):
    pass


def pykrx_stock():
    """Import the network adapter only when a collection function is called."""
    from pykrx import stock

    return stock


def pykrx_rendered_index_name(value: str) -> str:
    r"""Reproduce pykrx 1.2.8's index-frame name transformation.

    ``get_index_ohlcv_by_ticker`` applies ``r"[^-\w\.]"`` replacement to
    every cell before setting the index.  That removes spaces and the KRX
    middle dot from index names.  The ratified names are transformed by the
    same rule here so identity resolution is deterministic rather than an
    inferred alias table.
    """
    return PYKRX_INDEX_NAME_FILTER.sub("", str(value))


def canonical_index_name_map(market: str) -> dict[str, str]:
    policy = json.loads(SIGNALS.LEADERSHIP_POLICY_PATH.read_text(encoding="utf-8"))
    return canonical_index_name_map_from_records(policy["records"], market)


def canonical_index_name_map_from_records(records: list[dict], market: str) -> dict[str, str]:
    prefix = f"{market.upper()}::"
    result: dict[str, str] = {}
    for record in records:
        identity = record["series_identity"]
        if not identity.startswith(prefix):
            continue
        canonical = identity.split("::", 1)[1]
        rendered = pykrx_rendered_index_name(canonical)
        if not rendered:
            raise CandidateError(f"INDEX_NAME_NORMALIZATION_EMPTY:{identity}")
        prior = result.get(rendered)
        if prior is not None and prior != canonical:
            raise CandidateError(
                f"INDEX_NAME_NORMALIZATION_COLLISION:{market}:{rendered}:{prior}:{canonical}"
            )
        result[rendered] = canonical
    return result


def require_complete_leadership(leadership: dict) -> None:
    incomplete = {
        market: value
        for market, value in leadership["coverage"].items()
        if value["observed_sector_count"] != value["ratified_identity_count"] - 1
    }
    if incomplete:
        detail = ",".join(
            f"{market}={value['observed_sector_count']}/{value['ratified_identity_count'] - 1}"
            for market, value in sorted(incomplete.items())
        )
        raise CandidateError(f"LEADERSHIP_COVERAGE_INCOMPLETE:{detail}")


def frame_sha256(frame) -> str:
    raw = frame.sort_index().to_csv(index=True, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def require_columns(frame, required: set[str], label: str) -> None:
    if frame.empty:
        raise CandidateError(f"SOURCE_EMPTY:{label}")
    missing = required - {str(value) for value in frame.columns}
    if missing:
        raise CandidateError(f"SOURCE_COLUMNS_MISSING:{label}:{','.join(sorted(missing))}")


def stock_snapshot(date: str, market: str, fetched_at: str, source_capture=None) -> dict:
    stock = pykrx_stock()
    frame = stock.get_market_ohlcv_by_ticker(date, market.upper(), alternative=False)
    required = {"종가", "등락률", "거래대금", "시가총액"}
    require_columns(frame, required, f"stock:{market}:{date}")
    members = {}
    for identity in frame.index:
        key = str(identity).strip().zfill(6)
        if not key or key in members:
            raise CandidateError(f"SOURCE_IDENTITY_INVALID:stock:{market}:{date}")
        members[key] = {
            "close": Decimal(str(frame.at[identity, "종가"])),
            "return_pct": Decimal(str(frame.at[identity, "등락률"])),
            "trading_value": Decimal(str(frame.at[identity, "거래대금"])),
            "market_cap": Decimal(str(frame.at[identity, "시가총액"])),
        }
    digest = frame_sha256(frame)
    if source_capture is not None:
        source_capture.bind_normalized_frame(
            f"{date}:{market.upper()}:stock",
            digest,
            {
                identity: {
                    "close": values["close"],
                    "return_pct": values["return_pct"],
                    "trading_value": values["trading_value"],
                    "market_cap": values["market_cap"],
                }
                for identity, values in members.items()
            },
        )
    return {
        "market": market,
        "date": date,
        "members": members,
        "endpoint": "KRX_INFORMATION_DATA_SYSTEM_PYKRX_STOCK_FRAME",
        "response_sha256": digest,
        "fetched_at_utc": fetched_at,
    }


def index_name(identity: object) -> str:
    value = str(identity).strip()
    if not value:
        raise CandidateError("SOURCE_IDENTITY_INVALID:index")
    if not value.isdigit():
        return value
    stock = pykrx_stock()
    name = stock.get_index_ticker_name(value)
    if not isinstance(name, str) or not name.strip():
        raise CandidateError(f"INDEX_NAME_MISSING:{value}")
    return name.strip()


def index_snapshot(date: str, market: str, fetched_at: str, source_capture=None) -> dict:
    stock = pykrx_stock()
    frame = stock.get_index_ohlcv_by_ticker(date, market.upper())
    require_columns(frame, {"종가"}, f"index:{market}:{date}")
    indices = {}
    canonical_names = canonical_index_name_map(market)
    resolved_count = 0
    source_projection = {}
    for identity in frame.index:
        source_name = index_name(identity)
        close = Decimal(str(frame.at[identity, "종가"]))
        source_projection[source_name] = {"close": close}
        name = canonical_names.get(source_name, source_name)
        if name != source_name:
            resolved_count += 1
        if name in indices:
            raise CandidateError(f"SOURCE_IDENTITY_INVALID:index:{market}:{date}:{name}")
        indices[name] = close
    digest = frame_sha256(frame)
    if source_capture is not None:
        source_capture.bind_normalized_frame(
            f"{date}:{market.upper()}:index", digest, source_projection
        )
    return {
        "market": market,
        "date": date,
        "indices": indices,
        "endpoint": "KRX_INFORMATION_DATA_SYSTEM_PYKRX_INDEX_FRAME",
        "response_sha256": digest,
        "fetched_at_utc": fetched_at,
        "identity_normalization": {
            "source_version": "pykrx/1.2.8",
            "source_function": "get_index_ohlcv_by_ticker",
            "source_regex": r"[^-\w\.]",
            "canonical_policy": "config/korea_leadership_policy.json",
            "resolved_name_count": resolved_count,
        },
    }


def session(date: str, fetched_at: str, source_capture=None) -> dict:
    value = {"date": date, "stock": {}, "index": {}}
    for market in MARKETS:
        value["stock"][market] = stock_snapshot(date, market, fetched_at, source_capture)
        value["index"][market] = index_snapshot(date, market, fetched_at, source_capture)
    return value


def build(previous_date: str, current_date: str, fetched_at: str, source_capture=None) -> dict:
    contract = SIGNALS.load_contract()
    previous = session(previous_date, fetched_at, source_capture)
    current = session(current_date, fetched_at, source_capture)
    places = contract["output_decimal_places"]
    trend = SIGNALS._trend(previous, current, contract, places)
    leadership = SIGNALS._leadership(previous, current, contract, places)
    require_complete_leadership(leadership)
    axes = {
        "TREND": {"status": "OBSERVED", "measurement": trend},
        "BREADTH": {"status": "OBSERVED", "measurement": SIGNALS._breadth(previous, current, places)},
        "RISK_VOL": {"status": "OBSERVED", "measurement": SIGNALS._risk_vol(current, trend, places)},
        "LIQUIDITY": {"status": "OBSERVED", "measurement": SIGNALS._liquidity(previous, current, places)},
        "LEADERSHIP": {"status": "OBSERVED", "measurement": leadership},
    }
    source = {
        "name": "KRX_INFORMATION_DATA_SYSTEM_PYKRX",
        "tier": "Official",
        "adapter_status": "CANDIDATE_NOT_RUNTIME_ADOPTED",
        "per_security_persistence": 1,
        "per_security_persistence_scope": "ORIGINAL_PROVIDER_RESPONSE_BYTES",
        "normalized_frame_persistence": 0,
        "hash_semantics": "SHA256_OF_IN_MEMORY_NORMALIZED_DATAFRAME_CSV",
        "authentication_boundary": {
            "session_initialized_before_source_capture": True,
            "credentials_retained": False,
            "authentication_response_retained": False,
            "captured_market_data_request_count": 8,
        },
        "requests": SIGNALS._source_lineage(previous, current),
    }
    packet = {
        "schema_version": "korea_market_signals_information_system_candidate/1",
        "status": "OBSERVED_UNCLASSIFIED",
        "market": "KOREA",
        "market_timezone": "Asia/Seoul",
        "previous_date": dt.datetime.strptime(previous_date, "%Y%m%d").date().isoformat(),
        "as_of_date": dt.datetime.strptime(current_date, "%Y%m%d").date().isoformat(),
        "generated_at": fetched_at,
        "available_at": fetched_at,
        "source": source,
        "axes": axes,
        "coverage": {
            "required_axes": list(contract["required_axes"]),
            "observed_axes": list(contract["required_axes"]),
            "observed_count": 5,
            "required_count": 5,
            "ratio": "5/5",
        },
        "authority": {
            "paper_reference_display_authorized": True,
            "runtime_regime_authorized": False,
            "strategy_authorized": False,
            "stage_authorized": False,
            "buy_authorized": False,
            "action_authorized": False,
            "capital_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "real_authorized": False,
        },
    }
    packet["payload_sha256"] = SIGNALS.payload_sha256(packet)
    policy = json.loads(REFERENCE.POLICY_PATH.read_text(encoding="utf-8"))
    reference = REFERENCE.build_kr(
        packet, policy, render_version=REFERENCE.CURRENT_RENDER_VERSION
    )
    return {
        "schema_version": "kr_paper_information_system_reference_candidate/1",
        "status": "PAPER_REFERENCE_AVAILABLE_RUNTIME_UNKNOWN",
        "mode": "PAPER_DIAGNOSTIC_NOT_RUNTIME",
        "source_packet": packet,
        "paper_reference": reference,
        "authority": packet["authority"],
    }


def bind_receipt_times(packet: dict, manifest: dict) -> None:
    """Replace provisional start times with each response's actual receipt."""
    receipt_by_key = {
        record["key"]: record["response"]["received_at_utc"]
        for record in manifest["records"]
    }
    previous_date, current_date = manifest["dates"]
    for family, markets in packet["source"]["requests"].items():
        for market, lineage in markets.items():
            lineage["previous_fetched_at_utc"] = receipt_by_key[
                f"{previous_date}:{market}:{family}"
            ]
            lineage["current_fetched_at_utc"] = receipt_by_key[
                f"{current_date}:{market}:{family}"
            ]
            lineage["time_semantics"] = "ACTUAL_RESPONSE_RECEIVED_AT_UTC"


def bind_source_capture(result: dict, manifest: dict, calendar: dict) -> dict:
    """Bind retained source bytes after all eight calls have completed."""
    packet = result["source_packet"]
    packet["source"]["source_capture"] = {
        "manifest_path": "source-capture/manifest.json",
        "manifest_payload_sha256": manifest["payload_sha256"],
        "record_count": len(manifest["records"]),
        "original_response_bytes_retained": True,
        "request_headers_retained": False,
        "cookies_retained": False,
        "credentials_retained": False,
        "provider_published_at_is_received_at": False,
        "raw_to_normalized_frame_equivalence": "VERIFIED_FOR_ALL_REQUIRED_PROJECTIONS",
    }
    packet["source"]["session_calendar"] = calendar
    bind_receipt_times(packet, manifest)
    packet["source"]["dependency_lock"] = {
        "contract": "config/krx_information_system_source_candidate_v1.json",
        "requirements": "requirements-korea-paper-source.lock",
        "pykrx": "1.2.8",
        "pandas": "2.3.2",
        "numpy": "2.2.6",
        "requests": "2.34.2",
    }
    packet["source"]["normalization_contract"] = {
        "stock_fields": ["TDD_CLSPRC", "FLUC_RT", "ACC_TRDVAL", "MKTCAP"],
        "index_fields": ["IDX_NM", "CLSPRC_IDX"],
        "index_name_regex": r"[^-\w\.]",
        "client_price_adjustment": "NONE",
    }
    packet["generated_at"] = manifest["capture_completed_at_utc"]
    packet["available_at"] = manifest["capture_completed_at_utc"]
    unsigned = dict(packet)
    unsigned.pop("payload_sha256", None)
    packet["payload_sha256"] = SIGNALS.payload_sha256(unsigned)
    policy = json.loads(REFERENCE.POLICY_PATH.read_text(encoding="utf-8"))
    result["paper_reference"] = REFERENCE.build_kr(
        packet, policy, render_version=REFERENCE.CURRENT_RENDER_VERSION
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-date", required=True)
    parser.add_argument("--current-date", required=True)
    parser.add_argument(
        "--contract",
        type=Path,
        default=ROOT / "config/krx_information_system_source_candidate_v1.json",
    )
    parser.add_argument("--fetched-at", required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise CandidateError("NO_OVERWRITE")
    try:
        # pykrx 1.2.8 creates its authenticated read-only KRX session while
        # importing the adapter.  Keep that credential exchange outside the
        # market-data capture so the retained set is exactly eight public
        # source responses and contains no credential-bearing bytes.
        pykrx_stock()
    except Exception as exc:
        result = CAPTURE.unknown_status(f"SOURCE_SESSION_INITIALIZATION_FAILED:{type(exc).__name__}")
        CAPTURE.write_new(
            args.out,
            (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
        )
        print("STOP_KR_PAPER_INFORMATION_SYSTEM_REFERENCE_CANDIDATE:SOURCE_SESSION_INITIALIZATION_FAILED")
        return 2
    source_capture = CAPTURE.SourceCapture(
        args.capture_dir, (args.previous_date, args.current_date)
    )
    try:
        CAPTURE.require_claimed_start(
            args.fetched_at, source_capture.capture_started_at_utc
        )
        calendar = CAPTURE.require_completed_session_pair(
            args.previous_date,
            args.current_date,
            args.contract,
            source_capture.capture_started_at_utc,
        )
        with CAPTURE.capture_requests(source_capture):
            result = build(
                args.previous_date,
                args.current_date,
                source_capture.capture_started_at_utc,
                source_capture,
            )
        manifest = source_capture.finalize()
        result = bind_source_capture(result, manifest, calendar)
    except (CAPTURE.CaptureError, CandidateError) as exc:
        result = CAPTURE.unknown_status(str(exc))
        CAPTURE.write_new(
            args.out,
            (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
        )
        print(f"STOP_KR_PAPER_INFORMATION_SYSTEM_REFERENCE_CANDIDATE:{exc}")
        return 2
    CAPTURE.write_new(
        args.out,
        (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )
    print(
        "PASS_KR_PAPER_INFORMATION_SYSTEM_REFERENCE_CANDIDATE:"
        f"{result['paper_reference']['as_of_date']}:"
        f"{result['paper_reference']['paper_reference']['candidate_regime']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
