#!/usr/bin/env python3
"""P3-01 committed three-market source-coverage readiness.

This module inventories the latest committed source-coverage population on or
before an explicit date.  It independently rebuilds US and Crypto results from
their immutable raw archives through the existing production population
builders.  Korea remains blocked until an exact KRX population packet is
committed; the repository intentionally does not retain reconstructive KRX raw
responses.

Readiness here means source coverage only.  It never approves a universe,
infers freshness without a policy, decides investability, promotes a Stage, or
creates an action/order/trade.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "global_asset_master_population_readiness_contract.json"
US_RAW_ROOT = ROOT / "evidence" / "us_breadth" / "raw"
US_DATA_ROOT = ROOT / "data" / "observations" / "us_global_universe"
CRYPTO_RAW_ROOT = ROOT / "evidence" / "crypto" / "breadth" / "raw"
CRYPTO_DATA_ROOT = ROOT / "data" / "observations" / "crypto_global_universe"
KOREA_DATA_ROOT = ROOT / "data" / "observations" / "krx_global_universe"
OUTPUT_SCHEMA_VERSION = "global_asset_master_population_readiness/1"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


US_POPULATION = _load_module(
    "us_forward_universe_for_master_readiness",
    ".github/scripts/us_forward_universe_populate.py",
)
CRYPTO_POPULATION = _load_module(
    "crypto_forward_universe_for_master_readiness",
    ".github/scripts/crypto_forward_universe_populate.py",
)


class ReadinessError(ValueError):
    """Fail-closed P3-01 readiness violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadinessError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _validate_contract(value: dict) -> dict:
    expected = {
        "schema_version": 1,
        "contract_version": "global_asset_master_population_readiness/1",
        "markets": ["US", "KOREA", "CRYPTO"],
        "date_selection": "LATEST_COMMITTED_SOURCE_DATE_ON_OR_BEFORE_AS_OF",
        "knowledge_time_policy": (
            "EXACT_CONTENT_GIT_FIRST_SEEN_ON_OR_BEFORE_AS_OF_END_UTC"
        ),
        "freshness_policy": "UNRATIFIED_NO_STALE_INFERENCE",
        "overall_ready_requirement": "ALL_THREE_MARKETS_SOURCE_COVERAGE_READY",
        "authority": {
            "source_coverage_readiness_only": True,
            "universe_approval_authorized": False,
            "investability_authorized": False,
            "stage_promotion_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    if value != expected:
        raise ReadinessError("CONTRACT_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _valid_date(value: str) -> bool:
    try:
        return isinstance(value, str) and dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _dated_directories(root: Path, as_of_date: str) -> list[str]:
    if not Path(root).is_dir():
        return []
    dates = []
    for child in Path(root).iterdir():
        if child.is_dir() and _valid_date(child.name) and child.name <= as_of_date:
            dates.append(child.name)
    return sorted(dates)


def _latest_date(root: Path, as_of_date: str) -> str | None:
    dates = _dated_directories(Path(root), as_of_date)
    return dates[-1] if dates else None


def _git(*args: str, binary: bool = False):
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReadinessError("KNOWLEDGE_PROVENANCE_UNVERIFIED") from exc
    return completed.stdout if binary else completed.stdout.decode("utf-8")


def _parse_timestamp(value: str, code: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ReadinessError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReadinessError(code)
    return parsed.astimezone(dt.timezone.utc)


def _exact_content_first_seen(path: Path) -> tuple[str, str]:
    if _git("rev-parse", "--is-shallow-repository").strip() != "false":
        raise ReadinessError("KNOWLEDGE_PROVENANCE_SHALLOW_HISTORY")
    path = Path(path).resolve()
    try:
        relative = path.relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ReadinessError("KNOWLEDGE_EVIDENCE_PATH_INVALID") from exc
    if not path.is_file():
        raise ReadinessError(f"KNOWLEDGE_EVIDENCE_MISSING:{relative}")
    if _git("status", "--porcelain", "--", relative).strip():
        raise ReadinessError(f"KNOWLEDGE_EVIDENCE_DIRTY:{relative}")
    current = path.read_bytes()
    commits = [
        item
        for item in _git("log", "--reverse", "--format=%H", "--", relative).splitlines()
        if item
    ]
    for commit in commits:
        try:
            historical = _git("show", f"{commit}:{relative}", binary=True)
        except ReadinessError:
            continue
        if historical == current:
            first_seen_at = _git("show", "-s", "--format=%cI", commit).strip()
            _parse_timestamp(first_seen_at, "KNOWLEDGE_FIRST_SEEN_AT_INVALID")
            return commit, first_seen_at
    raise ReadinessError(f"KNOWLEDGE_EXACT_CONTENT_NOT_IN_GIT:{relative}")


def _latest_knowledge_eligible_date(
    root: Path, as_of_date: str, evidence_name: str
) -> tuple[str | None, str | None, str | None]:
    candidates = _dated_directories(Path(root), as_of_date)
    as_of_end = dt.datetime.combine(
        dt.date.fromisoformat(as_of_date), dt.time.max, tzinfo=dt.timezone.utc
    )
    for source_date in reversed(candidates):
        evidence_path = Path(root) / source_date / evidence_name
        if not evidence_path.is_file():
            continue
        first_seen_commit, first_seen_at = _exact_content_first_seen(evidence_path)
        if _parse_timestamp(first_seen_at, "KNOWLEDGE_FIRST_SEEN_AT_INVALID") <= as_of_end:
            return source_date, first_seen_commit, first_seen_at
    return None, None, None


def _closed_authority(contract: dict) -> dict:
    return copy.deepcopy(contract["authority"])


def _us_state(as_of_date: str, raw_root: Path, data_root: Path) -> dict:
    dated_candidates = _dated_directories(data_root, as_of_date)
    source_date, first_seen_commit, first_seen_at = _latest_knowledge_eligible_date(
        data_root, as_of_date, "packet.json"
    )
    if source_date is None:
        return {
            "market": "US",
            "status": "SOURCE_COVERAGE_NOT_READY",
            "reason": (
                "COMMITTED_POPULATION_PACKET_NOT_KNOWN_BY_AS_OF"
                if dated_candidates
                else "COMMITTED_POPULATION_PACKET_MISSING"
            ),
            "source_date": None,
            "knowledge_first_seen_commit": None,
            "knowledge_first_seen_at": None,
            "record_count": None,
            "packet_path": None,
            "packet_sha256": None,
        }
    target = US_POPULATION.output_path(source_date, data_root)
    existing = _read_json(target)
    schema_version = existing.get("schema_version")
    if schema_version not in US_POPULATION.SUPPORTED_RECORD_SCHEMA_VERSIONS:
        raise ReadinessError(f"US_PACKET_SCHEMA_UNSUPPORTED:{schema_version}")
    rebuilt = US_POPULATION.rebuild(
        source_date,
        raw_root=raw_root,
        record_schema_version=schema_version,
    )
    if existing != rebuilt:
        raise ReadinessError(f"US_PACKET_DRIFT_OR_TAMPER:{source_date}")
    return {
        "market": "US",
        "status": "SOURCE_COVERAGE_READY",
        "reason": None,
        "source_date": source_date,
        "knowledge_first_seen_commit": first_seen_commit,
        "knowledge_first_seen_at": first_seen_at,
        "record_count": rebuilt["packet"]["total_count"],
        "packet_path": f"data/observations/us_global_universe/{source_date}/packet.json",
        "packet_sha256": rebuilt["payload_sha256"],
    }


def _crypto_state(as_of_date: str, raw_root: Path, data_root: Path) -> dict:
    dated_candidates = _dated_directories(raw_root, as_of_date)
    source_date, first_seen_commit, first_seen_at = _latest_knowledge_eligible_date(
        raw_root, as_of_date, "_manifest.json"
    )
    if source_date is None:
        return {
            "market": "CRYPTO",
            "status": "SOURCE_COVERAGE_NOT_READY",
            "reason": (
                "COMMITTED_RAW_SNAPSHOT_NOT_KNOWN_BY_AS_OF"
                if dated_candidates
                else "COMMITTED_RAW_SNAPSHOT_MISSING"
            ),
            "source_date": None,
            "knowledge_first_seen_commit": None,
            "knowledge_first_seen_at": None,
            "record_count": None,
            "packet_path": None,
            "packet_sha256": None,
        }
    rebuilt = CRYPTO_POPULATION.rebuild(source_date, raw_root=raw_root)
    if rebuilt["status"] == "blocked":
        return {
            "market": "CRYPTO",
            "status": "SOURCE_COVERAGE_NOT_READY",
            "reason": rebuilt["reason"],
            "source_date": source_date,
            "knowledge_first_seen_commit": first_seen_commit,
            "knowledge_first_seen_at": first_seen_at,
            "record_count": None,
            "packet_path": None,
            "packet_sha256": None,
        }
    record = rebuilt["record"]
    target = CRYPTO_POPULATION.output_path(source_date, data_root)
    if not target.is_file():
        return {
            "market": "CRYPTO",
            "status": "SOURCE_COVERAGE_NOT_READY",
            "reason": "COMMITTED_POPULATION_PACKET_MISSING",
            "source_date": source_date,
            "knowledge_first_seen_commit": first_seen_commit,
            "knowledge_first_seen_at": first_seen_at,
            "record_count": None,
            "packet_path": None,
            "packet_sha256": None,
        }
    if _read_json(target) != record:
        raise ReadinessError(f"CRYPTO_PACKET_DRIFT_OR_TAMPER:{source_date}")
    return {
        "market": "CRYPTO",
        "status": "SOURCE_COVERAGE_READY",
        "reason": None,
        "source_date": source_date,
        "knowledge_first_seen_commit": first_seen_commit,
        "knowledge_first_seen_at": first_seen_at,
        "record_count": record["packet"]["selected_count"],
        "packet_path": f"data/observations/crypto_global_universe/{source_date}/packet.json",
        "packet_sha256": record["payload_sha256"],
    }


def _korea_state(as_of_date: str, data_root: Path) -> dict:
    source_date = _latest_date(data_root, as_of_date)
    if source_date is None:
        return {
            "market": "KOREA",
            "status": "SOURCE_COVERAGE_NOT_READY",
            "reason": "COMMITTED_EXACT_KRX_POPULATION_PACKET_MISSING",
            "source_date": None,
            "knowledge_first_seen_commit": None,
            "knowledge_first_seen_at": None,
            "record_count": None,
            "packet_path": None,
            "packet_sha256": None,
        }
    # No tracked KRX population contract exists yet. Refuse to bless an
    # arbitrary file merely because a dated directory appeared.
    raise ReadinessError(f"KOREA_POPULATION_VALIDATOR_NOT_IMPLEMENTED:{source_date}")


def build_readiness(
    as_of_date: str,
    *,
    us_raw_root: Path = US_RAW_ROOT,
    us_data_root: Path = US_DATA_ROOT,
    crypto_raw_root: Path = CRYPTO_RAW_ROOT,
    crypto_data_root: Path = CRYPTO_DATA_ROOT,
    korea_data_root: Path = KOREA_DATA_ROOT,
    contract: dict | None = None,
) -> dict:
    if not _valid_date(as_of_date):
        raise ReadinessError("AS_OF_DATE_INVALID")
    contract = load_contract() if contract is None else _validate_contract(contract)
    by_market = {
        "US": _us_state(as_of_date, Path(us_raw_root), Path(us_data_root)),
        "KOREA": _korea_state(as_of_date, Path(korea_data_root)),
        "CRYPTO": _crypto_state(
            as_of_date, Path(crypto_raw_root), Path(crypto_data_root)
        ),
    }
    markets = [by_market[market] for market in contract["markets"]]
    ready_count = sum(row["status"] == "SOURCE_COVERAGE_READY" for row in markets)
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "as_of_date": as_of_date,
        "status": (
            "THREE_MARKET_SOURCE_COVERAGE_READY"
            if ready_count == len(markets)
            else "BLOCKED_SOURCE_COVERAGE_INCOMPLETE"
        ),
        "ready_market_count": ready_count,
        "required_market_count": len(markets),
        "markets": markets,
        "freshness_policy": contract["freshness_policy"],
        "knowledge_time_policy": contract["knowledge_time_policy"],
        "authority": _closed_authority(contract),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_readiness(packet: dict, **kwargs) -> dict:
    if not isinstance(packet, dict) or packet.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        raise ReadinessError("PACKET_SCHEMA_MISMATCH")
    as_of_date = packet.get("as_of_date")
    expected = build_readiness(as_of_date, **kwargs)
    if packet != expected:
        raise ReadinessError("PACKET_DRIFT_OR_TAMPER")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        temp.write_text(payload, encoding="utf-8")
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("as_of_date")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        packet = build_readiness(args.as_of_date)
        validate_readiness(packet)
        if args.out is not None:
            write_json_atomic(args.out, packet)
    except (
        ReadinessError,
        US_POPULATION.PopulationError,
        US_POPULATION.US_BREADTH.ContractError,
        US_POPULATION.UGU.UsUniverseError,
        CRYPTO_POPULATION.PopulationError,
        CRYPTO_POPULATION.CGU.CryptoUniverseError,
    ) as exc:
        print(f"P3-01 population readiness failed: {exc}")
        return 1
    print(canonical_json(packet))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
