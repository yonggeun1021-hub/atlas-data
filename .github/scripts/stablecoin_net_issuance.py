#!/usr/bin/env python3
"""Build the P1-CR-03 Stablecoin Net Issuance evidence transform.

The transform consumes one validated PIT capture of the DefiLlama
``stablecoincharts_all`` historical series.  It emits native USD-peg supply
deltas only; it does not calculate a Regime score or write to a tracked path by
default.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import stablecoin_revision_contract as revision  # noqa: E402


TRANSFORM_VERSION = "stablecoin_net_issuance/v1"
SOURCE_ENDPOINT = "stablecoincharts_all"
SOURCE_FIELD = "totalCirculating.peggedUSD"
DIAGNOSTIC_FIELD = "totalCirculatingUSD.peggedUSD"
UTC = dt.timezone.utc


class TransformError(RuntimeError):
    """Fail-closed Net Issuance transform violation."""


def fail(code: str, detail: str) -> None:
    raise TransformError(f"{code}: {detail}")


def reject_json_constant(value: str) -> None:
    fail("NUMBER_INVALID", value)


def exact_payload(raw: bytes) -> object:
    try:
        return json.loads(
            raw,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=reject_json_constant,
        )
    except (json.JSONDecodeError, InvalidOperation) as exc:
        fail("PAYLOAD_INVALID", str(exc))


def decimal_value(container: object, key: str, label: str) -> Decimal | None:
    if not isinstance(container, dict) or key not in container:
        return None
    value = container[key]
    if isinstance(value, bool) or not isinstance(value, Decimal):
        fail("SUPPLY_VALUE_INVALID", label)
    if not value.is_finite() or value < 0:
        fail("SUPPLY_VALUE_INVALID", label)
    return value


def decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        value = Decimal(0)
    return format(value, "f")


def observation_date(epoch: str, snapshot_date: dt.date) -> dt.date:
    if not isinstance(epoch, str) or not epoch.isdigit():
        fail("OBSERVATION_TIME_INVALID", str(epoch))
    try:
        moment = dt.datetime.fromtimestamp(int(epoch), tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        fail("OBSERVATION_TIME_INVALID", str(exc))
    if (moment.hour, moment.minute, moment.second, moment.microsecond) != (
        0,
        0,
        0,
        0,
    ):
        fail("OBSERVATION_TIME_INVALID", f"not UTC midnight: {epoch}")
    if moment.date() > snapshot_date:
        fail(
            "FUTURE_OBSERVATION",
            f"{moment.date().isoformat()} > {snapshot_date.isoformat()}",
        )
    return moment.date()


def normalize_rows(payload: object, snapshot_date: str) -> dict[dt.date, dict]:
    if not isinstance(payload, list):
        fail("PAYLOAD_SHAPE_INVALID", SOURCE_ENDPOINT)
    try:
        vintage_date = dt.date.fromisoformat(snapshot_date)
    except (TypeError, ValueError):
        fail("SNAPSHOT_DATE_INVALID", str(snapshot_date))

    rows = {}
    for index, record in enumerate(payload):
        if not isinstance(record, dict):
            fail("PAYLOAD_SHAPE_INVALID", f"row {index}")
        date_value = observation_date(record.get("date"), vintage_date)
        if date_value in rows:
            fail("OBSERVATION_DATE_DUPLICATE", date_value.isoformat())
        native = decimal_value(
            record.get("totalCirculating"),
            "peggedUSD",
            f"row {index} {SOURCE_FIELD}",
        )
        valued = decimal_value(
            record.get("totalCirculatingUSD"),
            "peggedUSD",
            f"row {index} {DIAGNOSTIC_FIELD}",
        )
        rows[date_value] = {
            "source_epoch": record["date"],
            "native_supply": native,
            "usd_valued_supply": valued,
        }
    if not rows:
        fail("PAYLOAD_EMPTY", SOURCE_ENDPOINT)
    return rows


def delta(
    rows: dict[dt.date, dict],
    current_date: dt.date,
    lookback_days: int,
) -> tuple[Decimal | None, str, str]:
    current = rows[current_date]["native_supply"]
    prior_date = current_date - dt.timedelta(days=lookback_days)
    prior = rows.get(prior_date)
    if current is None:
        return None, "MISSING_CURRENT", prior_date.isoformat()
    if prior is None or prior["native_supply"] is None:
        return None, "MISSING_EXACT_PRIOR", prior_date.isoformat()
    return current - prior["native_supply"], "AVAILABLE", prior_date.isoformat()


def transform_rows(payload: object, snapshot_date: str) -> list[dict]:
    rows = normalize_rows(payload, snapshot_date)
    transformed = []
    for date_value in sorted(rows):
        source = rows[date_value]
        daily, daily_status, daily_prior = delta(rows, date_value, 1)
        weekly, weekly_status, weekly_prior = delta(rows, date_value, 7)
        transformed.append(
            {
                "observation_date": date_value.isoformat(),
                "source_epoch": source["source_epoch"],
                "gross_supply_native_usd_peg": decimal_text(
                    source["native_supply"]
                ),
                "gross_supply_usd_valued_diagnostic": decimal_text(
                    source["usd_valued_supply"]
                ),
                "daily_net_issuance_native_usd_peg": decimal_text(daily),
                "daily_status": daily_status,
                "daily_prior_date": daily_prior,
                "weekly_net_issuance_native_usd_peg": decimal_text(weekly),
                "weekly_status": weekly_status,
                "weekly_prior_date": weekly_prior,
            }
        )
    return transformed


def validated_core(snapshot_dir: Path, contract: dict) -> tuple[dict, str]:
    try:
        core = revision.snapshot_core(snapshot_dir, contract)
        metadata_status = revision.validate_manifest(
            snapshot_dir,
            core,
            contract,
        )
    except revision.ContractError as exc:
        fail("INPUT_CONTRACT_FAILED", str(exc))
    return core, metadata_status


def build_transform(
    snapshot_dir: Path,
    contract: dict | None = None,
) -> dict:
    contract = revision.load_contract() if contract is None else contract
    snapshot_dir = Path(snapshot_dir)
    core, metadata_status = validated_core(snapshot_dir, contract)
    endpoint = next(
        (
            item
            for item in contract["endpoints"]
            if item["name"] == SOURCE_ENDPOINT
        ),
        None,
    )
    if endpoint is None or endpoint.get("semantics") != "historical_series":
        fail("SOURCE_CONTRACT_INVALID", SOURCE_ENDPOINT)

    raw, _ = revision.read_response(snapshot_dir, endpoint["raw_file"])
    payload = exact_payload(raw)
    rows = transform_rows(payload, core["snapshot_date"])
    source_meta = next(
        item for item in core["endpoints"] if item["name"] == SOURCE_ENDPOINT
    )
    daily_counts = {}
    weekly_counts = {}
    for row in rows:
        daily_counts[row["daily_status"]] = (
            daily_counts.get(row["daily_status"], 0) + 1
        )
        weekly_counts[row["weekly_status"]] = (
            weekly_counts.get(row["weekly_status"], 0) + 1
        )

    return {
        "schema_version": 1,
        "transform_version": TRANSFORM_VERSION,
        "market": "CRYPTO",
        "market_timezone": "UTC",
        "measurement": "stablecoin_net_issuance_native_usd_peg",
        "unit": "USD_PEGGED_TOKEN",
        "availability_semantics": "exact",
        "pit_status": "qualified_direct_capture",
        "metadata_status": metadata_status,
        "lineage": {
            "vintage_date": core["snapshot_date"],
            "available_at": core["fetched_at_utc"],
            "revision_policy": (
                "RECOMPUTE_WITHIN_EACH_PIT_VINTAGE_NO_OVERWRITE"
            ),
            "point_in_time_required": True,
            "verification_status": "VERIFIED_RAW_SHA256",
            "evidence_grade": "A_DIRECT_FETCH",
            "source_type": "secondary",
            "universe_policy": "USD_PEGGED_AGGREGATE_ONLY",
            "rename_policy": "NOT_APPLICABLE_AGGREGATE",
        },
        "source": {
            "snapshot_date": core["snapshot_date"],
            "fetched_at_utc": core["fetched_at_utc"],
            "endpoint_name": SOURCE_ENDPOINT,
            "endpoint": source_meta["endpoint"],
            "response_sha256": source_meta["response_sha256"],
            "source_semantics": source_meta["semantics"],
            "issuance_field": SOURCE_FIELD,
            "diagnostic_valuation_field": DIAGNOSTIC_FIELD,
        },
        "formula": {
            "daily": "S(t,v) - S(t-1 calendar day,v)",
            "weekly": "S(t,v) - S(t-7 calendar days,v)",
            "vintage_rule": "one snapshot vintage v; never mix vintages",
            "missing_data": "no interpolation or forward-fill",
        },
        "status_counts": {
            "daily": daily_counts,
            "weekly": weekly_counts,
        },
        "rows": rows,
        "regime_score_authorized": False,
        "threshold_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def write_output(payload: dict, target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return target


def run(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    payload = build_transform(args.snapshot_dir)
    if args.out is None:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(write_output(payload, args.out))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except TransformError as exc:
        print(f"stablecoin net issuance STOP: {exc}", file=sys.stderr)
        raise SystemExit(2)
