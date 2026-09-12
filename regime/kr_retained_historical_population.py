#!/usr/bin/env python3
"""Build a KR historical PIT population from every committed retained packet.

This module performs no provider call.  It discovers the complete retained
``data/observations/korea_market_signals/*/packet.json`` set, validates each
packet with the original KRX producer validator, reuses the existing KR
historical population record constructor and candidate normalization, and
emits the existing ``regime_kr_historical_replay_population/v1`` bundle shape.

The retained file bytes, source identity, request timestamps, availability,
and any matching committed official calendar snapshot are hash-bound in an
additional manifest.  Missing standalone calendar snapshots remain explicit;
they are never inferred from weekdays.  The official KRX session response in
the source packet remains the session evidence for those dates.

Output stays SHADOW historical evidence.  Runtime Regime, Stage, Buy, Action,
capital, Order, Production, trading, and REAL authority remain closed.
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

from regime import kr_historical_replay_population as KRP
from regime import market_scoped_pit_acceptance as PIT


ROOT = Path(__file__).resolve().parents[1]
OBSERVATION_ROOT = ROOT / "data" / "observations" / "korea_market_signals"
CALENDAR_ROOT = ROOT / "evidence" / "market_calendar"
SOURCE_MODE = "COMMITTED_RETAINED_KRX_PACKETS_FULL_SET"
MANIFEST_VERSION = "kr_retained_historical_population_manifest/v1"
CALENDAR_MATCHED = "MATCHED_COMMITTED_OFFICIAL_CALENDAR_SNAPSHOT"
CALENDAR_MISSING = "DIRECT_KRX_SESSION_RESPONSE_NO_SEPARATE_CALENDAR_SNAPSHOT"


class RetainedHistoricalPopulationError(ValueError):
    """A retained packet set cannot be represented without weakening PIT."""


def fail(code: str, detail: str = "") -> None:
    raise RetainedHistoricalPopulationError(
        f"{code}:{detail}" if detail else code
    )


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise RetainedHistoricalPopulationError(
            f"RETAINED_FILE_UNREADABLE:{path}"
        ) from exc


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RetainedHistoricalPopulationError(
            f"RETAINED_JSON_INVALID:{path}"
        ) from exc
    if not isinstance(value, dict):
        fail("RETAINED_JSON_INVALID", str(path))
    return value


def _utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str):
        fail("TIMESTAMP_INVALID", label)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RetainedHistoricalPopulationError(
            f"TIMESTAMP_INVALID:{label}"
        ) from exc
    if parsed.tzinfo is None:
        fail("TIMESTAMP_INVALID", label)
    return parsed.astimezone(dt.timezone.utc)


def discover_retained_packets(root: Path = ROOT) -> list[Path]:
    observation_root = Path(root) / OBSERVATION_ROOT.relative_to(ROOT)
    paths = sorted(observation_root.glob("*/packet.json"))
    if not paths:
        fail("NO_RETAINED_KRX_PACKETS")
    if any(not path.is_file() for path in paths):
        fail("RETAINED_PACKET_NOT_FILE")
    return paths


def _relative(path: Path, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError as exc:
        raise RetainedHistoricalPopulationError(
            f"RETAINED_PATH_OUTSIDE_ROOT:{path}"
        ) from exc


def _request_times(packet: dict) -> list[str]:
    requests = packet.get("source", {}).get("requests")
    if not isinstance(requests, dict):
        fail("REQUEST_LINEAGE_MISSING", str(packet.get("as_of_date")))
    values = []
    for family in KRP.REQUEST_FAMILIES:
        markets = requests.get(family)
        if not isinstance(markets, dict):
            fail("REQUEST_LINEAGE_MISSING", family)
        for market in KRP.KMS.MARKETS:
            row = markets.get(market.upper())
            if not isinstance(row, dict):
                fail("REQUEST_LINEAGE_MISSING", f"{family}.{market}")
            values.extend(
                [
                    row.get("previous_fetched_at_utc"),
                    row.get("current_fetched_at_utc"),
                ]
            )
    if any(not isinstance(value, str) for value in values):
        fail("REQUEST_TIMESTAMP_MISSING", str(packet.get("as_of_date")))
    return values


def _validate_availability(packet: dict) -> None:
    as_of = packet["as_of_date"]
    generated = packet.get("generated_at")
    available = packet.get("available_at")
    request_times = _request_times(packet)
    latest_request = max(request_times)
    _utc(generated, f"{as_of}.generated_at")
    _utc(available, f"{as_of}.available_at")
    for index, value in enumerate(request_times):
        _utc(value, f"{as_of}.request.{index}")
    if generated != latest_request or available != latest_request:
        fail("PACKET_AVAILABILITY_MISMATCH", as_of)


def _calendar_rows(root: Path, session_date: str) -> list[dict]:
    calendar_root = Path(root) / CALENDAR_ROOT.relative_to(ROOT)
    rows = []
    for path in sorted(calendar_root.glob(f"*/*/calendar-{session_date}.json")):
        value = _read_json(path)
        calendar = value.get("calendar")
        if (
            value.get("schema_version") != "krx_date_specific_session_source/1"
            or value.get("as_of_date") != session_date
            or not isinstance(calendar, dict)
            or calendar.get("session_date") != session_date
            or calendar.get("status") != "OPEN_REGULAR"
            or calendar.get("timezone") != "Asia/Seoul"
        ):
            fail("CALENDAR_EVIDENCE_INVALID", _relative(path, root))
        _utc(calendar.get("available_at"), f"calendar.{session_date}.available_at")
        rows.append(
            {
                "path": _relative(path, root),
                "file_sha256": file_sha256(path),
                "provider_id": calendar.get("provider_id"),
                "available_at": calendar.get("available_at"),
                "status": calendar.get("status"),
                "source_ref": calendar.get("source_ref"),
                "source_sha256": calendar.get("source_sha256"),
            }
        )
    return rows


def _manifest_row(path: Path, packet: dict, root: Path) -> dict:
    relative = _relative(path, root)
    expected = (
        f"data/observations/korea_market_signals/"
        f"{packet['as_of_date']}/packet.json"
    )
    if relative != expected:
        fail("RETAINED_PACKET_PATH_DATE_MISMATCH", relative)
    _validate_availability(packet)
    calendars = _calendar_rows(root, packet["as_of_date"])
    return {
        "path": relative,
        "file_sha256": file_sha256(path),
        "packet_payload_sha256": packet["payload_sha256"],
        "previous_trading_date": packet["previous_date"],
        "effective_trading_date": packet["as_of_date"],
        "generated_at": packet["generated_at"],
        "available_at": packet["available_at"],
        "calendar_evidence_status": (
            CALENDAR_MATCHED if calendars else CALENDAR_MISSING
        ),
        "calendar_evidence": calendars,
    }


def _assemble(root: Path = ROOT) -> dict:
    root = Path(root)
    contract = KRP.KMS.load_contract()
    policy = KRP._load_candidate_policy()
    records = []
    manifest = []
    for path in discover_retained_packets(root):
        try:
            packet = KRP.KMS.validate_packet(_read_json(path), contract)
            normalized = KRP.PRR.build_kr(packet, policy)
        except (
            KRP.KMS.KoreaMarketSignalsError,
            KRP.PRR.PaperRegimeReferenceError,
        ) as exc:
            raise RetainedHistoricalPopulationError(
                f"RETAINED_PACKET_REJECTED:{_relative(path, root)}:{exc}"
            ) from exc
        manifest.append(_manifest_row(path, packet, root))
        records.append(
            KRP._observed_record(
                packet["as_of_date"],
                {"date": packet["previous_date"]},
                {"date": packet["as_of_date"]},
                packet,
                normalized,
                contract,
            )
        )

    requested = [record["requested_date"] for record in records]
    if requested != sorted(set(requested)):
        fail("RETAINED_DATES_NOT_UNIQUE_SORTED")
    population = {
        "schema_version": KRP.SCHEMA_VERSION,
        "mode": KRP.MODE,
        "wbs": "P1-COM-05",
        "evidence_class": KRP.EVIDENCE_CLASS,
        "requested_dates": requested,
        "source_contract": {
            "path": KRP.SOURCE_CONTRACT_PATH,
            "sha256": KRP.file_sha256(KRP.KMS.CONTRACT_PATH),
            "contract_version": contract["contract_version"],
        },
        "candidate_policy": {
            "path": KRP.CANDIDATE_POLICY_PATH,
            "sha256": KRP.file_sha256(KRP.PRR.POLICY_PATH),
            "status": policy.get("status"),
        },
        "candidate_rule_source": "regime/paper_regime_reference.py::build_kr",
        "source_population_mode": SOURCE_MODE,
        "retained_manifest_version": MANIFEST_VERSION,
        "retained_source_manifest": manifest,
        "records": records,
        "authority": dict(KRP.AUTHORITY),
    }
    population["payload_sha256"] = KRP.payload_sha256(population)
    return population


def build_population(root: Path = ROOT) -> dict:
    population = _assemble(root)
    return validate_population(population, root=root)


def validate_population(value: dict, *, root: Path = ROOT) -> dict:
    try:
        KRP.validate_population(copy.deepcopy(value))
    except KRP.ReplayPopulationError as exc:
        raise RetainedHistoricalPopulationError(
            f"BASE_POPULATION_INVALID:{exc}"
        ) from exc
    if value.get("source_population_mode") != SOURCE_MODE:
        fail("SOURCE_POPULATION_MODE_INVALID")
    if value.get("retained_manifest_version") != MANIFEST_VERSION:
        fail("RETAINED_MANIFEST_VERSION_INVALID")
    expected = _assemble(root)
    if canonical_json(value) != canonical_json(expected):
        fail("RETAINED_POPULATION_SOURCE_DERIVATION_MISMATCH")
    return copy.deepcopy(value)


def evaluate_pit(value: dict, *, root: Path = ROOT) -> dict:
    checked = validate_population(value, root=root)
    result = PIT.evaluate_market_pit_acceptance("KR", checked)
    if result["authority"]["runtime_decision_available"] is not False:
        fail("PIT_RUNTIME_AUTHORITY_OPEN")
    return result


def _default_out(prefix: str) -> Path:
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".json")
    os.close(fd)
    return Path(name)


def _write_external(value: dict, path: Path) -> Path:
    KRP._forbid_tracked_output(ROOT, path)
    KRP._atomic_write(
        Path(path),
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return Path(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--status-out", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)

    output_path = None
    if args.verify:
        population = validate_population(_read_json(args.verify))
    else:
        population = build_population()
        output_path = _write_external(
            population,
            args.out or _default_out("kr_retained_historical_population."),
        )
    status = evaluate_pit(population)
    status_path = None
    if args.status_out:
        status_path = _write_external(status, args.status_out)
    print(
        json.dumps(
            {
                "payload_sha256": population["payload_sha256"],
                "records": len(population["records"]),
                "pit_status": status["status"],
                "evaluated_date_count": status["evaluated_date_count"],
                "regimes_observed": status["regimes_observed"],
                "missing_regimes": status["missing_regimes"],
                "runtime_decision_available": False,
                "out": str(output_path) if output_path else None,
                "status_out": str(status_path) if status_path else None,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        RetainedHistoricalPopulationError,
        KRP.ReplayPopulationError,
        PIT.MarketScopedPitAcceptanceError,
    ) as exc:
        print(f"FATAL: {exc}")
        raise SystemExit(1)
