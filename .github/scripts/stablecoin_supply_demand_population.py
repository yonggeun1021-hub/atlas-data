#!/usr/bin/env python3
"""Populate P3-09 Crypto raw supply features from one stablecoin PIT vintage.

This adapter reuses the validated DefiLlama ``stablecoincharts_all`` capture.
It publishes only policy-neutral three-point supply observations.  It does not
choose an improvement direction, threshold, candidate, rank, or trading action.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / ".github" / "scripts"
DISCOVERY_DIR = ROOT / "discovery"
for directory in (SCRIPT_DIR, DISCOVERY_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import stablecoin_net_issuance as stablecoin  # noqa: E402
import supply_demand  # noqa: E402


OUTPUT_ROOT = ROOT / "evidence" / "supply_demand" / "crypto"
SERIES_ID = "CRYPTO.STABLECOIN.NATIVE.SUPPLY"
ASSET_ID = "CRYPTO:USD_PEGGED_AGGREGATE"
MEASUREMENT_IDENTITY = (
    "DefiLlama stablecoincharts/all totalCirculating.peggedUSD aggregate "
    "native USD-pegged token supply"
)
COMPARISON_BASIS = "CONSECUTIVE_CALENDAR_DAY_WITHIN_ONE_PIT_VINTAGE"


class PopulationError(RuntimeError):
    """Fail-closed P3-09 stablecoin population violation."""


def fail(code: str, detail: str) -> None:
    raise PopulationError(f"{code}: {detail}")


def parse_snapshot_date(value: object) -> dt.date:
    try:
        parsed = dt.date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        fail("SNAPSHOT_DATE_INVALID", str(value))
    if parsed.isoformat() != value:
        fail("SNAPSHOT_DATE_INVALID", str(value))
    return parsed


def expected_periods(snapshot_date: str) -> list[str]:
    latest = parse_snapshot_date(snapshot_date)
    return [
        (latest - dt.timedelta(days=offset)).isoformat()
        for offset in (2, 1, 0)
    ]


def build_input(transform: dict) -> dict:
    required_top = {
        "transform_version",
        "market",
        "measurement",
        "unit",
        "lineage",
        "source",
        "rows",
    }
    if not isinstance(transform, dict) or not required_top.issubset(transform):
        fail("TRANSFORM_SHAPE_INVALID", "required fields absent")
    if (
        transform["transform_version"] != stablecoin.TRANSFORM_VERSION
        or transform["market"] != "CRYPTO"
        or transform["measurement"]
        != "stablecoin_net_issuance_native_usd_peg"
        or transform["unit"] != "USD_PEGGED_TOKEN"
    ):
        fail("TRANSFORM_IDENTITY_INVALID", "unexpected stablecoin transform")

    source = transform["source"]
    lineage = transform["lineage"]
    snapshot_date = source.get("snapshot_date")
    periods = expected_periods(snapshot_date)
    available_at = lineage.get("available_at")
    if (
        source.get("endpoint_name") != stablecoin.SOURCE_ENDPOINT
        or source.get("endpoint")
        != "https://stablecoins.llama.fi/stablecoincharts/all"
        or source.get("fetched_at_utc") != available_at
        or source.get("source_semantics") != "historical_series"
    ):
        fail("SOURCE_IDENTITY_INVALID", "unexpected PIT source")

    rows = transform.get("rows")
    if not isinstance(rows, list):
        fail("TRANSFORM_ROWS_INVALID", "rows must be a list")
    by_date = {}
    for row in rows:
        if not isinstance(row, dict):
            fail("TRANSFORM_ROW_INVALID", "row must be an object")
        day = row.get("observation_date")
        if day in by_date:
            fail("TRANSFORM_ROW_DUPLICATE", str(day))
        by_date[day] = row

    source_identity = {
        "source_id": "defillama_stablecoins_api",
        "source_url": source["endpoint"],
        "source_sha256": source["response_sha256"],
        # The API does not provide a publication timestamp.  All historical
        # points in this vintage are therefore available no earlier than the
        # exact Atlas fetch time for these bytes.
        "available_at": available_at,
        "retrieved_at_utc": source["fetched_at_utc"],
    }
    points = []
    for period in periods:
        row = by_date.get(period)
        value = None if row is None else row.get("gross_supply_native_usd_peg")
        if value is None:
            reason = (
                "EXACT_PERIOD_OBSERVATION_ABSENT"
                if row is None
                else "NATIVE_USD_PEG_SUPPLY_ABSENT"
            )
            points.append(
                {
                    "period_end": period,
                    "status": "EVIDENCE_UNRESOLVED",
                    "numeric_value": None,
                    "missing_reasons": [reason],
                    "source_identity": None,
                }
            )
        else:
            points.append(
                {
                    "period_end": period,
                    "status": "EVIDENCE_AVAILABLE",
                    "numeric_value": value,
                    "missing_reasons": [],
                    "source_identity": dict(source_identity),
                }
            )

    return {
        "schema_version": supply_demand.INPUT_SCHEMA_VERSION,
        "as_of_utc": source["fetched_at_utc"],
        "series": [
            {
                "series_id": SERIES_ID,
                "market": "CRYPTO",
                "asset_id": ASSET_ID,
                "measurement_identity": MEASUREMENT_IDENTITY,
                "metric_type": "AGGREGATE_TOKEN_SUPPLY",
                "unit": "USD_PEGGED_TOKEN",
                "frequency": "DAILY",
                "comparison_basis": COMPARISON_BASIS,
                "expected_periods": periods,
                "evidence_points": points,
            }
        ],
    }


def build_packet(snapshot_dir: Path) -> dict:
    transform = stablecoin.build_transform(Path(snapshot_dir))
    packet = supply_demand.build_packet(build_input(transform))
    supply_demand.validate_packet(packet)
    return packet


def packet_bytes(packet: dict) -> bytes:
    return (
        json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def publish_packet(
    packet: dict,
    snapshot_date: str,
    output_root: Path = OUTPUT_ROOT,
) -> tuple[Path, str]:
    parse_snapshot_date(snapshot_date)
    output_root = Path(output_root)
    target = output_root / snapshot_date / "rev-001.json"
    expected = packet_bytes(packet)
    if target.exists():
        if target.read_bytes() != expected:
            fail("APPEND_ONLY_PACKET_MISMATCH", str(target))
        return target, "existing_identical"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    if temporary.exists():
        fail("TEMPORARY_PATH_EXISTS", str(temporary))
    try:
        temporary.write_bytes(expected)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target, "published"


def run(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args(argv)
    packet = build_packet(args.snapshot_dir)
    snapshot_date = args.snapshot_dir.name
    target, result = publish_packet(packet, snapshot_date, args.output_root)
    print(
        json.dumps(
            {
                "result": result,
                "path": str(target),
                "payload_sha256": packet["payload_sha256"],
                "feature_status": packet["series_results"][0]["feature_status"],
                "case_count": packet["case_count"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (
        PopulationError,
        stablecoin.TransformError,
        supply_demand.SupplyDemandError,
        OSError,
    ) as exc:
        print(f"stablecoin supply-demand population STOP: {exc}", file=sys.stderr)
        raise SystemExit(2)
