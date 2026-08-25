#!/usr/bin/env python3
"""P2-04 provider-free Crypto Rotation source-pair population.

This adapter prepares the exact ``crypto_rotation_input/1`` pair consumed by
``rotation/crypto_rotation.py`` from two adjacent, committed P1-CR-07
Leadership observations.  It deliberately does *not* create or infer a
rotation policy: the repository default remains ABSENT, so no rank, bucket,
transition, P2 state, candidate, Stage, action, order, Production, or trading
authority can be produced here.

Insufficient contiguous P1-CR-07 history is an expected operational BLOCKED
outcome.  Corrupt raw evidence, lineage drift, or an existing content-addressed
packet mismatch remains a hard failure.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "evidence" / "crypto" / "breadth" / "raw"
DATA_ROOT = ROOT / "data" / "observations" / "crypto_rotation_source_pair"
SCHEMA_VERSION = "crypto_rotation_source_pair_population/1"
WINDOW_ID = "pilot_7d"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


LEADERSHIP = _load_module(
    "crypto_leadership_for_rotation_source_pair",
    ".github/scripts/crypto_leadership.py",
)


class SourcePairPopulationError(ValueError):
    """Fail-closed P2-04 source-pair population violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _parse_date(value: str, code: str) -> dt.date:
    if not isinstance(value, str):
        raise SourcePairPopulationError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise SourcePairPopulationError(code) from exc
    if parsed.isoformat() != value:
        raise SourcePairPopulationError(code)
    return parsed


def _window(packet: dict, window_id: str = WINDOW_ID) -> dict:
    windows = packet.get("windows") if isinstance(packet, dict) else None
    if not isinstance(windows, list):
        raise SourcePairPopulationError("LEADERSHIP_WINDOWS_INVALID")
    selected = [
        item
        for item in windows
        if isinstance(item, dict) and item.get("window_id") == window_id
    ]
    if len(selected) != 1:
        raise SourcePairPopulationError("LEADERSHIP_WINDOW_NOT_UNIQUE")
    return selected[0]


def _blocked_reason(label: str, packet: dict) -> str | None:
    selected = _window(packet)
    if selected.get("status") == "OBSERVED_UNCLASSIFIED":
        return None
    reason = selected.get("unknown_reason")
    if not isinstance(reason, str) or not reason:
        raise SourcePairPopulationError(
            f"LEADERSHIP_WINDOW_STATUS_INVALID:{label}"
        )
    return f"{label.upper()}_{reason}"


def _build_leadership(
    raw_root: Path,
    end_date: dt.date,
    *,
    contract_path: Path = LEADERSHIP.CONTRACT_PATH,
    universe_policy_path: Path = LEADERSHIP.UNIVERSE_POLICY_PATH,
    exclusion_taxonomy_path: Path = LEADERSHIP.BREADTH.EXCLUSION_TAXONOMY_PATH,
    leadership_policy_path: Path = LEADERSHIP.LEADERSHIP_POLICY_PATH,
    taxonomy_path: Path = LEADERSHIP.TAXONOMY_PATH,
    identity_exceptions_path: Path = LEADERSHIP.IDENTITY_EXCEPTIONS_PATH,
) -> dict:
    return LEADERSHIP.build_transform(
        raw_root,
        contract_path=contract_path,
        universe_policy_path=universe_policy_path,
        exclusion_taxonomy_path=exclusion_taxonomy_path,
        leadership_policy_path=leadership_policy_path,
        taxonomy_path=taxonomy_path,
        identity_exceptions_path=identity_exceptions_path,
        end_date=end_date.isoformat(),
    )


def build_source_pair(
    *,
    raw_root: Path = RAW_ROOT,
    as_of_date: str | None = None,
    contract_path: Path = LEADERSHIP.CONTRACT_PATH,
    universe_policy_path: Path = LEADERSHIP.UNIVERSE_POLICY_PATH,
    exclusion_taxonomy_path: Path = LEADERSHIP.BREADTH.EXCLUSION_TAXONOMY_PATH,
    leadership_policy_path: Path = LEADERSHIP.LEADERSHIP_POLICY_PATH,
    taxonomy_path: Path = LEADERSHIP.TAXONOMY_PATH,
    identity_exceptions_path: Path = LEADERSHIP.IDENTITY_EXCEPTIONS_PATH,
) -> dict:
    """Build one deterministic policy-free source pair.

    Returns ``{"status": "ready", "packet": ...}`` or the expected
    ``{"status": "blocked", "reason": ...}``.  Only insufficient or
    otherwise UNKNOWN Leadership windows are converted to BLOCKED; structural
    evidence errors raised by the canonical Leadership builder propagate.
    """
    raw_root = Path(raw_root)
    snapshots = LEADERSHIP.discover_snapshot_map(raw_root)
    if not snapshots:
        raise SourcePairPopulationError("RAW_SNAPSHOT_ARCHIVE_EMPTY")
    current_date = (
        max(snapshots)
        if as_of_date is None
        else _parse_date(as_of_date, "AS_OF_DATE_INVALID")
    )
    prior_date = current_date - dt.timedelta(days=1)
    common = {
        "contract_path": contract_path,
        "universe_policy_path": universe_policy_path,
        "exclusion_taxonomy_path": exclusion_taxonomy_path,
        "leadership_policy_path": leadership_policy_path,
        "taxonomy_path": taxonomy_path,
        "identity_exceptions_path": identity_exceptions_path,
    }
    prior = _build_leadership(raw_root, prior_date, **common)
    current = _build_leadership(raw_root, current_date, **common)
    blockers = [
        reason
        for reason in (
            _blocked_reason("prior", prior),
            _blocked_reason("current", current),
        )
        if reason is not None
    ]
    if blockers:
        return {
            "status": "blocked",
            "reason": ";".join(blockers),
            "as_of_date": current_date.isoformat(),
        }

    rotation_input = {
        "schema_version": "crypto_rotation_input/1",
        "as_of_date": current_date.isoformat(),
        "prior_observation": prior,
        "current_observation": current,
    }
    packet = {
        "schema_version": SCHEMA_VERSION,
        "market": "CRYPTO",
        "as_of_date": current_date.isoformat(),
        "status": "SOURCE_PAIR_READY_ROTATION_POLICY_ABSENT",
        "window_id": WINDOW_ID,
        "rotation_input": rotation_input,
        "lineage": {
            "prior_leadership_sha256": payload_sha256(prior),
            "current_leadership_sha256": payload_sha256(current),
            "prior_manifest_sha256_by_date": prior["lineage"][
                "manifest_sha256_by_date"
            ],
            "current_manifest_sha256_by_date": current["lineage"][
                "manifest_sha256_by_date"
            ],
        },
        "policy_boundary": {
            "repository_default_rotation_policy": "ABSENT",
            "rotation_policy_authorized": False,
            "rotation_engine_invoked": False,
            "reason": "EXTERNAL_RATIFIED_ROTATION_POLICY_REQUIRED",
        },
        "authority": {
            "source_pair_population_only": True,
            "bucket_ranking_authorized": False,
            "bucket_transition_authorized": False,
            "p2_state_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "unresolved_boundaries": [
            "ROTATION_POLICY_ABSENT",
            "SECTOR_CHAIN_GROUP_COVERAGE_POLICY_UNRATIFIED",
            "P2_STATE_VOCABULARY_PENDING_P2_05",
            "ROTATION_LEDGER_NOT_POPULATED",
            "BRIEFING_INTEGRATION_NOT_AUTHORIZED",
        ],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return {"status": "ready", "packet": packet}


def validate_source_pair(
    packet: dict,
    *,
    raw_root: Path = RAW_ROOT,
    contract_path: Path = LEADERSHIP.CONTRACT_PATH,
    universe_policy_path: Path = LEADERSHIP.UNIVERSE_POLICY_PATH,
    exclusion_taxonomy_path: Path = LEADERSHIP.BREADTH.EXCLUSION_TAXONOMY_PATH,
    leadership_policy_path: Path = LEADERSHIP.LEADERSHIP_POLICY_PATH,
    taxonomy_path: Path = LEADERSHIP.TAXONOMY_PATH,
    identity_exceptions_path: Path = LEADERSHIP.IDENTITY_EXCEPTIONS_PATH,
) -> dict:
    if not isinstance(packet, dict):
        raise SourcePairPopulationError("SOURCE_PAIR_INVALID")
    digest = packet.get("payload_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise SourcePairPopulationError("SOURCE_PAIR_SHA_INVALID")
    unsigned = dict(packet)
    unsigned.pop("payload_sha256", None)
    if payload_sha256(unsigned) != digest:
        raise SourcePairPopulationError("SOURCE_PAIR_SHA_MISMATCH")
    rebuilt = build_source_pair(
        raw_root=raw_root,
        as_of_date=packet.get("as_of_date"),
        contract_path=contract_path,
        universe_policy_path=universe_policy_path,
        exclusion_taxonomy_path=exclusion_taxonomy_path,
        leadership_policy_path=leadership_policy_path,
        taxonomy_path=taxonomy_path,
        identity_exceptions_path=identity_exceptions_path,
    )
    if rebuilt.get("status") != "ready" or rebuilt.get("packet") != packet:
        raise SourcePairPopulationError("SOURCE_PAIR_REBUILD_MISMATCH")
    return packet


def output_path(packet: dict, data_root: Path = DATA_ROOT) -> Path:
    digest = packet["payload_sha256"]
    return Path(data_root) / packet["as_of_date"] / f"pair-{digest[:16]}.json"


def publish_append_only(packet: dict, data_root: Path = DATA_ROOT) -> tuple[Path, bool]:
    target = output_path(packet, data_root)
    payload = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") != payload:
            raise SourcePairPopulationError("CONTENT_ADDRESSED_PACKET_DRIFT")
        return target, False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target, True


def populate(
    *,
    raw_root: Path = RAW_ROOT,
    data_root: Path = DATA_ROOT,
    as_of_date: str | None = None,
    **paths,
) -> dict:
    built = build_source_pair(raw_root=raw_root, as_of_date=as_of_date, **paths)
    if built["status"] == "blocked":
        return {
            "outcome": "blocked",
            "reason": built["reason"],
            "as_of_date": built["as_of_date"],
            "path": None,
            "payload_sha256": None,
        }
    packet = validate_source_pair(built["packet"], raw_root=raw_root, **paths)
    target, created = publish_append_only(packet, data_root)
    return {
        "outcome": "populated" if created else "verified_existing",
        "reason": "ROTATION_POLICY_ABSENT_SOURCE_PAIR_ONLY",
        "as_of_date": packet["as_of_date"],
        "path": str(target),
        "payload_sha256": packet["payload_sha256"],
    }


def _write_github_output(result: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    def clean(value) -> str:
        return str(value or "").replace("\n", " ").replace("\r", " ")
    with open(path, "a", encoding="utf-8") as handle:
        for key in ("outcome", "reason", "as_of_date", "path", "payload_sha256"):
            handle.write(f"{key}={clean(result.get(key))}\n")


def run(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--as-of-date")
    args = parser.parse_args(argv)
    try:
        result = populate(
            raw_root=args.raw_root,
            data_root=args.data_root,
            as_of_date=args.as_of_date,
        )
    except (SourcePairPopulationError, LEADERSHIP.LeadershipError) as exc:
        _write_github_output({
            "outcome": "failed", "reason": str(exc), "as_of_date": args.as_of_date,
            "path": None, "payload_sha256": None,
        })
        print(f"P2-04 source-pair population failed: {exc}")
        return 1
    _write_github_output(result)
    print(
        "P2-04 source-pair population"
        f" outcome={result['outcome']} as_of_date={result['as_of_date']}"
        f" reason={result['reason']} path={result['path']}"
        f" sha256={result['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
