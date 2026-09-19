#!/usr/bin/env python3
"""Build a complete KR historical replay range from the official KRX calendar.

The caller supplies only range boundaries. Every OPEN_REGULAR session in that
range is derived from the committed official KRX annual holiday capture and is
sent to the existing KR historical replay producer. Provider failures remain
BLOCKED records; dates are never selected according to the resulting regime.
All outputs are historical evidence outside the checkout and grant no runtime
or trading authority.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile

from market_data import krx_official_holiday_calendar as CALENDAR
from regime import kr_historical_replay_population as POPULATION
from regime import market_scoped_pit_acceptance as PIT


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPTURE = (
    ROOT
    / "evidence/market_calendar/krx_global_holiday/2026-09-09/capture-2026.json"
)
POLICY_PATH = ROOT / "config/korea_leadership_policy.json"
SCHEMA = "kr_contiguous_historical_range_receipt/1"


class KrContiguousHistoricalRangeError(ValueError):
    """A complete official-calendar range cannot be safely produced."""


def fail(code: str, detail: str = "") -> None:
    raise KrContiguousHistoricalRangeError(f"{code}:{detail}" if detail else code)


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse_date(value: str) -> dt.date:
    try:
        result = dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise KrContiguousHistoricalRangeError("RANGE_DATE_INVALID") from exc
    if result.isoformat() != value:
        fail("RANGE_DATE_INVALID")
    return result


def leadership_policy_floor(path: Path = POLICY_PATH) -> dt.date:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KrContiguousHistoricalRangeError("LEADERSHIP_POLICY_INVALID") from exc
    if not isinstance(value, dict):
        fail("LEADERSHIP_POLICY_INVALID")
    records = value.get("records")
    if value.get("approval_status") != "RATIFIED" or not isinstance(records, list) or not records:
        fail("LEADERSHIP_POLICY_INVALID")
    try:
        starts = [parse_date(row["effective_from"]) for row in records]
    except (KeyError, TypeError) as exc:
        raise KrContiguousHistoricalRangeError("LEADERSHIP_POLICY_INVALID") from exc
    return min(starts)


def official_open_dates(
    start: str,
    end: str,
    *,
    capture_path: Path = DEFAULT_CAPTURE,
) -> tuple[list[str], list[dict]]:
    first, last = parse_date(start), parse_date(end)
    if first > last:
        fail("RANGE_ORDER_INVALID")
    policy_floor = leadership_policy_floor()
    if first < policy_floor:
        fail("RANGE_PRECEDES_LEADERSHIP_POLICY", policy_floor.isoformat())
    capture_raw = Path(capture_path).read_bytes()
    source_ref = Path(capture_path).relative_to(ROOT).as_posix()
    dates, receipts = [], []
    current = first
    while current <= last:
        _, receipt = CALENDAR.build_calendar_packet(
            capture_raw, source_ref, current.isoformat()
        )
        receipts.append(receipt)
        if receipt["status"] == "OPEN_REGULAR":
            dates.append(current.isoformat())
        current += dt.timedelta(days=1)
    if not dates:
        fail("RANGE_HAS_NO_OPEN_SESSION")
    if dates[0] != first.isoformat() or dates[-1] != last.isoformat():
        fail("RANGE_BOUNDARY_NOT_OPEN_REGULAR")
    return dates, receipts


def build(
    auth_key: str,
    start: str,
    end: str,
    *,
    capture_path: Path = DEFAULT_CAPTURE,
    opener=None,
) -> tuple[dict, dict, dict]:
    dates, calendar_receipts = official_open_dates(
        start, end, capture_path=capture_path
    )
    kwargs = {} if opener is None else {"opener": opener}
    population = POPULATION.build_population(auth_key, dates, **kwargs)
    POPULATION.validate_population(population)
    pit_status = PIT.evaluate_market_pit_acceptance("KR", population)
    records = population["records"]
    observed = [row for row in records if row["status"] == "OBSERVED"]
    blocked = [row for row in records if row["status"] == "BLOCKED"]
    complete = [
        row
        for row in observed
        if row["five_axis"]["coverage"]["ratio"] == "5/5"
    ]
    denominator = len(records)
    receipt = {
        "schema": SCHEMA,
        "range": {"start": start, "end": end},
        "source_population_mode": "OFFICIAL_KRX_CALENDAR_CONTIGUOUS_FULL_RANGE",
        "calendar": {
            "provider_id": CALENDAR.PROVIDER_ID,
            "market_rule_source": CALENDAR.MARKET_RULE_SOURCE,
            "capture_path": Path(capture_path).relative_to(ROOT).as_posix(),
            "capture_sha256": sha256_bytes(Path(capture_path).read_bytes()),
            "calendar_date_count": len(calendar_receipts),
            "open_session_dates": dates,
            "closed_dates": [
                row["session_date"]
                for row in calendar_receipts
                if row["status"] == "CLOSED"
            ],
            "weekday_or_holiday_guess_used": False,
        },
        "coverage": {
            "requested_session_count": denominator,
            "observed_count": len(observed),
            "complete_five_axis_count": len(complete),
            "blocked_count": len(blocked),
            "missing_rate": format(len(blocked) / denominator, ".6f"),
            "blocked_records": [
                {
                    "requested_date": row["requested_date"],
                    "failure_reason": row["failure_reason"],
                }
                for row in blocked
            ],
        },
        "population": {
            "schema_version": population["schema_version"],
            "payload_sha256": population["payload_sha256"],
        },
        "pit_status": copy.deepcopy(pit_status),
        "selection": {
            "regime_based_date_selection_used": False,
            "all_official_open_sessions_in_range_requested": True,
        },
        "authority": {
            "historical_replay_evidence_authorized": True,
            "pit_acceptance_authorized": False,
            "runtime_regime_wiring_authorized": False,
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
    return population, pit_status, receipt


def forbid_checkout_output(path: Path) -> None:
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
    except ValueError:
        return
    fail("TRACKED_OUTPUT_FORBIDDEN")


def atomic_write(path: Path, value: dict) -> None:
    forbid_checkout_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--capture", type=Path, default=DEFAULT_CAPTURE)
    args = parser.parse_args()
    population, pit_status, receipt = build(
        os.environ.get("KRX_API_KEY", ""),
        args.start,
        args.end,
        capture_path=args.capture,
    )
    atomic_write(args.out_dir / "population.json", population)
    atomic_write(args.out_dir / "pit-status.json", pit_status)
    atomic_write(args.out_dir / "range-receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
