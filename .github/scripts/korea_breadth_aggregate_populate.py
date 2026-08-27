#!/usr/bin/env python3
"""Retain exact P1-KR-05 aggregate packets without promoting an axis.

The live producer deliberately does not retain KRX raw response bodies or
per-symbol rows.  This helper therefore preserves only the four exact
``korea_breadth_observation/1`` aggregate files already emitted by that
producer, together with immutable GitHub run/artifact lineage.  It validates
packet hashes, identities, dates, count/fraction arithmetic, and false
authority before an atomic append-only publish.

Retention is evidence-loss prevention, not source replay.  A valid retained
bundle remains ineligible to define KR/BREADTH until a separate source and
scoring policy is ratified.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "korea_breadth_aggregate_retention_contract.json"
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SCHEMA_VERSION = "korea_breadth_aggregate_retention/1"
PACKET_SCHEMA_VERSION = "korea_breadth_observation/1"
SCOPES = ("historical", "recent")
MARKETS = ("KOSPI", "KOSDAQ")
PACKET_NAMES = tuple(
    f"korea-breadth-{scope}-{market.lower()}.json"
    for scope in SCOPES
    for market in MARKETS
)


class AggregateRetentionError(ValueError):
    """Fail-closed aggregate-retention contract violation."""


def fail(code: str, detail: object = "") -> None:
    raise AggregateRetentionError(f"{code}:{detail}")


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": SCHEMA_VERSION,
        "source_packet_schema_version": PACKET_SCHEMA_VERSION,
        "allowed_workflow_paths": [
            ".github/workflows/p1-kr05-korea-breadth-live.yml",
            ".github/workflows/p2-03-korea-observation-pair.yml",
        ],
        "required_scopes": list(SCOPES),
        "required_markets": list(MARKETS),
        "historical_pair": {"previous_date": "20100104", "as_of_date": "20100105"},
        "market_endpoints": {
            "KOSPI": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
            "KOSDAQ": "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd",
        },
        "universe_semantics": "exact_date_official_response_rows_source_coverage_not_investable",
        "output_decimal_places": 12,
        "output_root": "data/observations/korea_breadth_aggregate",
        "retention_boundary": {
            "exact_aggregate_packet_bytes_retained": True,
            "raw_response_bodies_retained": False,
            "per_symbol_identity_and_price_retained": False,
            "independent_source_replay_available": False,
            "axis_evidence_eligible": False,
        },
        "authority": {
            "breadth_classification_authorized": False,
            "threshold_authorized": False,
            "regime_score_authorized": False,
            "strategy_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "capital_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("CONTRACT_INVALID", exc)
    if value != _expected_contract():
        fail("CONTRACT_INVALID", "pinned semantics")
    return value


def _compact_date(value: object, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"\d{8}", value) is None:
        fail("DATE_INVALID", label)
    return value


def _utc(value: object, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or UTC_SECOND.fullmatch(value) is None:
        fail("TIMESTAMP_INVALID", label)
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        fail("COUNT_INVALID", label)
    return value


def _ratio(numerator: int, denominator: int, places: int) -> str:
    if denominator <= 0:
        fail("RATIO_DENOMINATOR_INVALID", denominator)
    quantum = Decimal(1).scaleb(-places)
    value = (Decimal(numerator) / Decimal(denominator)).quantize(
        quantum, rounding=ROUND_HALF_EVEN
    )
    return format(value, "f")


def _packet_identity(name: str) -> tuple[str, str]:
    if name not in PACKET_NAMES:
        fail("PACKET_NAME_INVALID", name)
    parts = name.removesuffix(".json").split("-")
    return parts[2], parts[3].upper()


def validate_source_packet(value: object, name: str, contract: dict) -> dict:
    scope, market = _packet_identity(name)
    if not isinstance(value, dict):
        fail("SOURCE_PACKET_INVALID", name)
    expected_keys = {
        "schema_version", "scope", "market", "previous_date", "as_of_date",
        "request_identity", "fetched_at_utc", "source_available_at", "captured_at",
        "first_seen_at", "universe", "participation", "breadth_classification_authorized",
        "threshold_authorized", "regime_score_authorized", "production_wiring_authorized",
        "trading_action_authorized", "payload_sha256",
    }
    if set(value) != expected_keys:
        fail("SOURCE_PACKET_FIELDS_INVALID", name)
    if value["schema_version"] != PACKET_SCHEMA_VERSION:
        fail("SOURCE_PACKET_SCHEMA_INVALID", name)
    if value["scope"] != scope or value["market"] != market:
        fail("SOURCE_PACKET_IDENTITY_INVALID", name)
    previous = _compact_date(value["previous_date"], f"{name}.previous_date")
    current = _compact_date(value["as_of_date"], f"{name}.as_of_date")
    if previous >= current:
        fail("SOURCE_PACKET_DATE_ORDER_INVALID", name)
    if scope == "historical" and {
        "previous_date": previous, "as_of_date": current
    } != contract["historical_pair"]:
        fail("HISTORICAL_PAIR_INVALID", name)

    request = value["request_identity"]
    fetched = value["fetched_at_utc"]
    if not isinstance(request, dict) or set(request) != {"previous", "current"}:
        fail("REQUEST_IDENTITY_INVALID", name)
    if not isinstance(fetched, dict) or set(fetched) != {"previous", "current"}:
        fail("FETCH_LINEAGE_INVALID", name)
    for point in ("previous", "current"):
        row = request[point]
        if not isinstance(row, dict) or set(row) != {"endpoint", "response_sha256"}:
            fail("REQUEST_IDENTITY_INVALID", f"{name}.{point}")
        if row["endpoint"] != contract["market_endpoints"][market]:
            fail("SOURCE_ENDPOINT_INVALID", f"{name}.{point}")
        if not isinstance(row["response_sha256"], str) or SHA256.fullmatch(
            row["response_sha256"]
        ) is None:
            fail("SOURCE_SHA256_INVALID", f"{name}.{point}")
        _utc(fetched[point], f"{name}.fetched_at_utc.{point}")
    if fetched["previous"] > fetched["current"]:
        fail("FETCH_LINEAGE_ORDER_INVALID", name)
    if value["source_available_at"] is not None:
        fail("SOURCE_AVAILABLE_AT_INFERRED", name)
    captured = _utc(value["captured_at"], f"{name}.captured_at")
    first_seen = _utc(value["first_seen_at"], f"{name}.first_seen_at")
    if captured != fetched["current"] or first_seen != captured:
        fail("FIRST_SEEN_LINEAGE_INVALID", name)

    universe = value["universe"]
    expected_universe_keys = {
        "previous_count", "current_count", "shared_count", "entered_count",
        "exited_count", "previous_unavailable_close_count",
        "current_unavailable_close_count", "paired_price_unavailable_count", "semantics",
    }
    if not isinstance(universe, dict) or set(universe) != expected_universe_keys:
        fail("UNIVERSE_INVALID", name)
    counts = {
        key: _nonnegative_int(universe[key], f"{name}.universe.{key}")
        for key in expected_universe_keys
        if key != "semantics"
    }
    if universe["semantics"] != contract["universe_semantics"]:
        fail("UNIVERSE_SEMANTICS_INVALID", name)
    if counts["current_count"] != counts["shared_count"] + counts["entered_count"]:
        fail("UNIVERSE_ARITHMETIC_INVALID", f"{name}.current")
    if counts["previous_count"] != counts["shared_count"] + counts["exited_count"]:
        fail("UNIVERSE_ARITHMETIC_INVALID", f"{name}.previous")
    if counts["previous_unavailable_close_count"] > counts["previous_count"] or (
        counts["current_unavailable_close_count"] > counts["current_count"]
    ) or counts["paired_price_unavailable_count"] > counts["shared_count"]:
        fail("UNIVERSE_COVERAGE_INVALID", name)

    participation = value["participation"]
    expected_participation_keys = {
        "paired_count", "advancing_count", "declining_count", "unchanged_count",
        "advance_fraction", "decline_fraction", "unchanged_fraction", "classification",
    }
    if not isinstance(participation, dict) or set(participation) != expected_participation_keys:
        fail("PARTICIPATION_INVALID", name)
    paired = _nonnegative_int(participation["paired_count"], f"{name}.paired_count")
    advancing = _nonnegative_int(participation["advancing_count"], f"{name}.advancing")
    declining = _nonnegative_int(participation["declining_count"], f"{name}.declining")
    unchanged = _nonnegative_int(participation["unchanged_count"], f"{name}.unchanged")
    if paired <= 0 or paired != advancing + declining + unchanged:
        fail("PARTICIPATION_ARITHMETIC_INVALID", name)
    if paired != counts["shared_count"] - counts["paired_price_unavailable_count"]:
        fail("PAIRED_COVERAGE_INVALID", name)
    places = contract["output_decimal_places"]
    for key, count in (
        ("advance_fraction", advancing),
        ("decline_fraction", declining),
        ("unchanged_fraction", unchanged),
    ):
        if participation[key] != _ratio(count, paired, places):
            fail("PARTICIPATION_FRACTION_INVALID", f"{name}.{key}")
    if participation["classification"] != "UNDEFINED":
        fail("PARTICIPATION_CLASSIFICATION_INVALID", name)

    for key in (
        "breadth_classification_authorized", "threshold_authorized",
        "regime_score_authorized", "production_wiring_authorized",
        "trading_action_authorized",
    ):
        if value[key] is not False:
            fail("SOURCE_PACKET_AUTHORITY_INVALID", f"{name}.{key}")
    unsigned = {key: item for key, item in value.items() if key != "payload_sha256"}
    if value["payload_sha256"] != payload_sha256(unsigned):
        fail("SOURCE_PACKET_HASH_INVALID", name)
    return value


def _read_packets(derived_dir: Path, contract: dict) -> tuple[dict[str, bytes], dict[str, dict]]:
    raw_by_name: dict[str, bytes] = {}
    packet_by_name: dict[str, dict] = {}
    for name in PACKET_NAMES:
        path = Path(derived_dir) / name
        if not path.is_file():
            fail("SOURCE_PACKET_MISSING", name)
        raw = path.read_bytes()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            fail("SOURCE_PACKET_JSON_INVALID", f"{name}:{exc}")
        raw_by_name[name] = raw
        packet_by_name[name] = validate_source_packet(value, name, contract)
    recent_dates = {
        packet_by_name[f"korea-breadth-recent-{market.lower()}.json"]["as_of_date"]
        for market in MARKETS
    }
    if len(recent_dates) != 1:
        fail("RECENT_MARKET_DATE_MISMATCH")
    return raw_by_name, packet_by_name


def _source_metadata(
    *, repository: str = "yonggeun1021-hub/atlas-data",
    workflow_path: str = ".github/workflows/p1-kr05-korea-breadth-live.yml",
    workflow_run_id: str, run_attempt: int, source_head_sha: str,
    artifact_id: str, artifact_name: str, artifact_digest: str,
    artifact_created_at: str | None, artifact_expires_at: str | None,
) -> dict:
    if repository != "yonggeun1021-hub/atlas-data":
        fail("SOURCE_REPOSITORY_INVALID", repository)
    if workflow_path not in _expected_contract()["allowed_workflow_paths"]:
        fail("SOURCE_WORKFLOW_INVALID", workflow_path)
    if re.fullmatch(r"[1-9]\d*", workflow_run_id or "") is None:
        fail("WORKFLOW_RUN_ID_INVALID", workflow_run_id)
    if isinstance(run_attempt, bool) or not isinstance(run_attempt, int) or run_attempt <= 0:
        fail("RUN_ATTEMPT_INVALID", run_attempt)
    if GIT_SHA.fullmatch(source_head_sha or "") is None:
        fail("SOURCE_HEAD_SHA_INVALID", source_head_sha)
    if re.fullmatch(r"[1-9]\d*", artifact_id or "") is None:
        fail("ARTIFACT_ID_INVALID", artifact_id)
    expected_name = f"p1-kr05-derived-outputs-{workflow_run_id}-{run_attempt}"
    if artifact_name != expected_name:
        fail("ARTIFACT_NAME_INVALID", artifact_name)
    if re.fullmatch(r"sha256:[0-9a-f]{64}", artifact_digest or "") is None:
        fail("ARTIFACT_DIGEST_INVALID", artifact_digest)
    _utc(artifact_created_at, "artifact_created_at", nullable=True)
    _utc(artifact_expires_at, "artifact_expires_at", nullable=True)
    return {
        "repository": repository,
        "workflow_path": workflow_path,
        "workflow_run_id": workflow_run_id,
        "run_attempt": run_attempt,
        "source_head_sha": source_head_sha,
        "artifact_id": artifact_id,
        "artifact_name": artifact_name,
        "artifact_digest": artifact_digest,
        "artifact_created_at": artifact_created_at,
        "artifact_expires_at": artifact_expires_at,
    }


def build_manifest(
    raw_by_name: dict[str, bytes], packet_by_name: dict[str, dict], source: dict,
    contract: dict,
) -> dict:
    recent = packet_by_name["korea-breadth-recent-kospi.json"]
    as_of_compact = recent["as_of_date"]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "as_of_date": (
            f"{as_of_compact[0:4]}-{as_of_compact[4:6]}-{as_of_compact[6:8]}"
        ),
        "capture_mode": "forward_live",
        "source": source,
        "source_files": {
            name: {
                "sha256": sha256(raw_by_name[name]),
                "size_bytes": len(raw_by_name[name]),
                "packet_payload_sha256": packet_by_name[name]["payload_sha256"],
            }
            for name in PACKET_NAMES
        },
        "retention_boundary": contract["retention_boundary"],
        "authority": contract["authority"],
    }
    manifest["payload_sha256"] = payload_sha256(manifest)
    return manifest


def output_dir(manifest: dict, root: Path = ROOT) -> Path:
    source = manifest["source"]
    relative = Path("data/observations/korea_breadth_aggregate")
    return (
        Path(root) / relative / manifest["as_of_date"]
        / f"run-{source['workflow_run_id']}-attempt-{source['run_attempt']}"
    )


def validate_retained_dir(
    path: Path, contract_path: Path = CONTRACT_PATH, root: Path = ROOT
) -> dict:
    contract = load_contract(contract_path)
    manifest_path = Path(path) / "manifest.json"
    try:
        manifest_raw = manifest_path.read_bytes()
        manifest = json.loads(manifest_raw)
    except (OSError, json.JSONDecodeError) as exc:
        fail("MANIFEST_INVALID", exc)
    expected_manifest_keys = {
        "schema_version", "as_of_date", "capture_mode", "source", "source_files",
        "retention_boundary", "authority", "payload_sha256",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_manifest_keys:
        fail("MANIFEST_FIELDS_INVALID")
    if manifest["schema_version"] != SCHEMA_VERSION or manifest["capture_mode"] != "forward_live":
        fail("MANIFEST_IDENTITY_INVALID")
    source = _source_metadata(**manifest["source"])
    unsigned = {key: value for key, value in manifest.items() if key != "payload_sha256"}
    if manifest["payload_sha256"] != payload_sha256(unsigned):
        fail("MANIFEST_HASH_INVALID")
    if manifest["retention_boundary"] != contract["retention_boundary"] or (
        manifest["authority"] != contract["authority"]
    ):
        fail("MANIFEST_AUTHORITY_INVALID")
    raw_by_name, packet_by_name = _read_packets(Path(path), contract)
    if set(manifest["source_files"]) != set(PACKET_NAMES):
        fail("MANIFEST_SOURCE_FILES_INVALID")
    expected = build_manifest(raw_by_name, packet_by_name, source, contract)
    if manifest != expected:
        fail("MANIFEST_DERIVATION_MISMATCH")
    if output_dir(manifest, root).resolve() != Path(path).resolve():
        fail("MANIFEST_PATH_IDENTITY_MISMATCH")
    return manifest


def populate(
    derived_dir: Path, *, workflow_run_id: str, run_attempt: int,
    source_head_sha: str, artifact_id: str, artifact_name: str,
    artifact_digest: str, artifact_created_at: str | None = None,
    artifact_expires_at: str | None = None, root: Path = ROOT,
    contract_path: Path = CONTRACT_PATH,
    workflow_path: str = ".github/workflows/p1-kr05-korea-breadth-live.yml",
) -> dict:
    contract = load_contract(contract_path)
    raw_by_name, packet_by_name = _read_packets(Path(derived_dir), contract)
    source = _source_metadata(
        workflow_run_id=workflow_run_id, run_attempt=run_attempt,
        source_head_sha=source_head_sha, artifact_id=artifact_id,
        artifact_name=artifact_name, artifact_digest=artifact_digest,
        artifact_created_at=artifact_created_at, artifact_expires_at=artifact_expires_at,
        workflow_path=workflow_path,
    )
    manifest = build_manifest(raw_by_name, packet_by_name, source, contract)
    target = output_dir(manifest, root)
    if target.exists():
        existing = validate_retained_dir(target, contract_path, root)
        if existing != manifest or any(
            (target / name).read_bytes() != raw_by_name[name] for name in PACKET_NAMES
        ):
            fail("EXISTING_BUNDLE_DRIFT_OR_TAMPER")
        return {"outcome": "verified_existing", "path": str(target),
                "payload_sha256": manifest["payload_sha256"]}

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".aggregate-retention-", dir=target.parent))
    try:
        for name in PACKET_NAMES:
            (staging / name).write_bytes(raw_by_name[name])
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    validate_retained_dir(target, contract_path, root)
    return {"outcome": "populated", "path": str(target),
            "payload_sha256": manifest["payload_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--derived-dir", type=Path, required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--source-head-sha", required=True)
    parser.add_argument(
        "--workflow-path",
        default=".github/workflows/p1-kr05-korea-breadth-live.yml",
    )
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--artifact-digest", required=True)
    parser.add_argument("--artifact-created-at")
    parser.add_argument("--artifact-expires-at")
    args = parser.parse_args()
    try:
        result = populate(
            args.derived_dir,
            workflow_run_id=args.workflow_run_id,
            run_attempt=args.run_attempt,
            source_head_sha=args.source_head_sha,
            workflow_path=args.workflow_path,
            artifact_id=args.artifact_id,
            artifact_name=args.artifact_name,
            artifact_digest=args.artifact_digest,
            artifact_created_at=args.artifact_created_at,
            artifact_expires_at=args.artifact_expires_at,
        )
    except AggregateRetentionError as exc:
        print(f"korea breadth aggregate retention failed reason={exc}")
        return 1
    print(
        f"korea breadth aggregate retention outcome={result['outcome']} "
        f"path={result['path']} payload_sha256={result['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
