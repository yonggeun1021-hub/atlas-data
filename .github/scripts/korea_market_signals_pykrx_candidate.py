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
from decimal import Decimal
from pathlib import Path
import sys

from pykrx import stock


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
MARKETS = ("kospi", "kosdaq")


class CandidateError(ValueError):
    pass


def frame_sha256(frame) -> str:
    raw = frame.sort_index().to_csv(index=True, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def require_columns(frame, required: set[str], label: str) -> None:
    if frame.empty:
        raise CandidateError(f"SOURCE_EMPTY:{label}")
    missing = required - {str(value) for value in frame.columns}
    if missing:
        raise CandidateError(f"SOURCE_COLUMNS_MISSING:{label}:{','.join(sorted(missing))}")


def stock_snapshot(date: str, market: str, fetched_at: str) -> dict:
    frame = stock.get_market_ohlcv_by_ticker(date, market.upper(), alternative=False)
    required = {"종가", "등락률", "거래대금", "시가총액"}
    require_columns(frame, required, f"stock:{market}:{date}")
    members = {}
    for identity, row in frame.iterrows():
        key = str(identity).strip().zfill(6)
        if not key or key in members:
            raise CandidateError(f"SOURCE_IDENTITY_INVALID:stock:{market}:{date}")
        members[key] = {
            "close": Decimal(str(row["종가"])),
            "return_pct": Decimal(str(row["등락률"])),
            "trading_value": Decimal(str(row["거래대금"])),
            "market_cap": Decimal(str(row["시가총액"])),
        }
    return {
        "market": market,
        "date": date,
        "members": members,
        "endpoint": "KRX_INFORMATION_DATA_SYSTEM_PYKRX_STOCK_FRAME",
        "response_sha256": frame_sha256(frame),
        "fetched_at_utc": fetched_at,
    }


def index_name(identity: object) -> str:
    value = str(identity).strip()
    if not value:
        raise CandidateError("SOURCE_IDENTITY_INVALID:index")
    if not value.isdigit():
        return value
    name = stock.get_index_ticker_name(value)
    if not isinstance(name, str) or not name.strip():
        raise CandidateError(f"INDEX_NAME_MISSING:{value}")
    return name.strip()


def index_snapshot(date: str, market: str, fetched_at: str) -> dict:
    frame = stock.get_index_ohlcv_by_ticker(date, market.upper())
    require_columns(frame, {"종가"}, f"index:{market}:{date}")
    indices = {}
    for identity, row in frame.iterrows():
        name = index_name(identity)
        if name in indices:
            raise CandidateError(f"SOURCE_IDENTITY_INVALID:index:{market}:{date}:{name}")
        indices[name] = Decimal(str(row["종가"]))
    return {
        "market": market,
        "date": date,
        "indices": indices,
        "endpoint": "KRX_INFORMATION_DATA_SYSTEM_PYKRX_INDEX_FRAME",
        "response_sha256": frame_sha256(frame),
        "fetched_at_utc": fetched_at,
    }


def session(date: str, fetched_at: str) -> dict:
    value = {"date": date, "stock": {}, "index": {}}
    for market in MARKETS:
        value["stock"][market] = stock_snapshot(date, market, fetched_at)
        value["index"][market] = index_snapshot(date, market, fetched_at)
    return value


def build(previous_date: str, current_date: str, fetched_at: str) -> dict:
    contract = SIGNALS.load_contract()
    previous = session(previous_date, fetched_at)
    current = session(current_date, fetched_at)
    places = contract["output_decimal_places"]
    trend = SIGNALS._trend(previous, current, contract, places)
    leadership = SIGNALS._leadership(previous, current, contract, places)
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
        "per_security_persistence": 0,
        "normalized_frame_persistence": 0,
        "hash_semantics": "SHA256_OF_IN_MEMORY_NORMALIZED_DATAFRAME_CSV",
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-date", required=True)
    parser.add_argument("--current-date", required=True)
    parser.add_argument("--fetched-at", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.previous_date, args.current_date, args.fetched_at)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "PASS_KR_PAPER_INFORMATION_SYSTEM_REFERENCE_CANDIDATE:"
        f"{result['paper_reference']['as_of_date']}:"
        f"{result['paper_reference']['paper_reference']['candidate_regime']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
