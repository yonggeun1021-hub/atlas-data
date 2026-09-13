#!/usr/bin/env python3
"""Connect one retained daily Rotation component to Stage 3.

The daily bundle is only a retained container here.  This adapter verifies its
self-hash and fixed producer identity, then independently validates the exact
embedded ``rotation_discovery_briefing_packet/4`` and re-derives the ledger it
binds from the caller-supplied frozen Rotation source.  With no frozen source,
the only admissible ledger is the canonical empty ledger.

No latest pointer, provider, state policy, candidate, or authority is invented.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from briefing import rotation_candidate_selection_input as STAGE3  # noqa: E402
from rotation import rotation_state_ledger as LEDGER  # noqa: E402


DAILY_CONTRACT_PATH = ROOT / "config" / "daily_orchestrator_contract.json"
DAILY_CONTRACT_VERSION = "daily_orchestrator/6"
DAILY_SCHEMA_VERSION = 1
DAILY_OUTPUT_SCHEMA_VERSION = "daily_briefing_packet/1"
DAILY_CAPTURE_MODE = "provider_free_aggregation_of_persisted_evidence_only"
ROTATION_SOURCE_KEY = "US_ROTATION_LEDGER"
ROTATION_SOURCE_FIELDS = {"rotation_packet", "state_policy", "previous_ledger"}
COMPONENT_FIELDS = {
    "component_id", "status", "reason", "as_of_date", "generated_at",
    "available_at", "source_packet_path", "source_packet_sha256", "validated",
    "authority", "packet", "decision_eligible", "action_eligible",
    "order_eligible", "contract_version",
}


class RotationCandidateSelectionDailyHandoffError(ValueError):
    """Fail-closed retained-daily handoff violation."""


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RotationCandidateSelectionDailyHandoffError(
            f"JSON_READ_FAILED:{path}:{exc}"
        ) from exc
    if not isinstance(value, dict):
        raise RotationCandidateSelectionDailyHandoffError(
            f"JSON_ROOT_INVALID:{path}"
        )
    return value


def _daily_contract() -> dict:
    value = _read_json(DAILY_CONTRACT_PATH)
    required = {
        "schema_version", "contract_version", "output_schema_version", "slots",
        "component_order", "component_status_values", "capture_mode", "authority",
    }
    if not required.issubset(value):
        raise RotationCandidateSelectionDailyHandoffError(
            "DAILY_CONTRACT_FIELDS_INVALID"
        )
    authority = value.get("authority")
    if (
        value["schema_version"] != DAILY_SCHEMA_VERSION
        or value["contract_version"] != DAILY_CONTRACT_VERSION
        or value["output_schema_version"] != DAILY_OUTPUT_SCHEMA_VERSION
        or value["capture_mode"] != DAILY_CAPTURE_MODE
        or not isinstance(authority, dict)
        or authority.get("aggregation_only") is not True
        or authority.get("component_build_authorized") is not True
        or any(
            allowed is not False
            for key, allowed in authority.items()
            if key not in {"aggregation_only", "component_build_authorized"}
        )
    ):
        raise RotationCandidateSelectionDailyHandoffError(
            "DAILY_CONTRACT_IDENTITY_INVALID"
        )
    return value


def _checked_daily_container(packet: dict) -> tuple[dict, dict]:
    if not isinstance(packet, dict):
        raise RotationCandidateSelectionDailyHandoffError("DAILY_PACKET_INVALID")
    contract = _daily_contract()
    if (
        packet.get("schema_version") != contract["schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("output_schema_version") != contract["output_schema_version"]
        or packet.get("slot") not in contract["slots"]
        or packet.get("capture_mode") != contract["capture_mode"]
        or packet.get("authority") != contract["authority"]
    ):
        raise RotationCandidateSelectionDailyHandoffError(
            "DAILY_PACKET_IDENTITY_INVALID"
        )
    unsigned = copy.deepcopy(packet)
    claimed = unsigned.pop("packet_sha256", None)
    if STAGE3.payload_sha256(unsigned) != claimed:
        raise RotationCandidateSelectionDailyHandoffError(
            "DAILY_PACKET_SHA_MISMATCH"
        )
    components = packet.get("components")
    if (
        not isinstance(components, list)
        or any(not isinstance(row, dict) for row in components)
        or [row.get("component_id") for row in components]
        != contract["component_order"]
    ):
        raise RotationCandidateSelectionDailyHandoffError(
            "DAILY_COMPONENT_ORDER_INVALID"
        )
    matches = [
        row for row in components
        if row.get("component_id") == "ROTATION_DISCOVERY"
    ]
    if len(matches) != 1:
        raise RotationCandidateSelectionDailyHandoffError(
            "DAILY_ROTATION_COMPONENT_INVALID"
        )
    component = matches[0]
    if not isinstance(component, dict) or set(component) != COMPONENT_FIELDS:
        raise RotationCandidateSelectionDailyHandoffError(
            "DAILY_ROTATION_COMPONENT_FIELDS_INVALID"
        )
    briefing = component.get("packet")
    for key in ("action_eligible", "decision_eligible", "order_eligible"):
        if component.get(key) is not False:
            raise RotationCandidateSelectionDailyHandoffError(
                f"DAILY_ROTATION_AUTHORITY_OPENED:{key}"
            )
    if (
        component.get("validated") is not True
        or component.get("status") not in contract["component_status_values"]
        or component.get("contract_version")
        != STAGE3.BRIEFING.load_contract()["contract_version"]
    ):
        raise RotationCandidateSelectionDailyHandoffError(
            "DAILY_ROTATION_COMPONENT_IDENTITY_INVALID"
        )
    try:
        checked = STAGE3.BRIEFING.validate_briefing(copy.deepcopy(briefing))
    except STAGE3.BRIEFING.RotationDiscoveryBriefingError as exc:
        raise RotationCandidateSelectionDailyHandoffError(
            f"DAILY_ROTATION_REVALIDATION_FAILED:{exc}"
        ) from exc
    if (
        component.get("source_packet_sha256") != checked["packet_sha256"]
        or component.get("authority") != checked["authority"]
        or component.get("generated_at") != checked["generated_at"]
        or packet.get("generated_at") != checked["generated_at"]
        or packet.get("slot") != checked["slot"]
    ):
        raise RotationCandidateSelectionDailyHandoffError(
            "DAILY_ROTATION_COMPONENT_BINDING_MISMATCH"
        )
    frozen = packet.get("frozen_sources")
    if not isinstance(frozen, dict):
        raise RotationCandidateSelectionDailyHandoffError(
            "DAILY_FROZEN_SOURCES_INVALID"
        )
    return checked, frozen


def _rederived_ledger(frozen: dict) -> dict:
    if ROTATION_SOURCE_KEY not in frozen:
        return LEDGER.empty_ledger()
    source = frozen[ROTATION_SOURCE_KEY]
    if not isinstance(source, dict) or set(source) != ROTATION_SOURCE_FIELDS:
        raise RotationCandidateSelectionDailyHandoffError(
            "DAILY_ROTATION_SOURCE_INVALID"
        )
    rotation_packet = source["rotation_packet"]
    state_policy = source["state_policy"]
    previous_ledger = source["previous_ledger"]
    if (
        not isinstance(rotation_packet, dict)
        or not isinstance(state_policy, dict)
        or (previous_ledger is not None and not isinstance(previous_ledger, dict))
        or rotation_packet.get("market") != "US"
        or state_policy.get("market") != "US"
    ):
        raise RotationCandidateSelectionDailyHandoffError(
            "DAILY_ROTATION_SOURCE_INVALID"
        )
    try:
        return LEDGER.apply_rotation(
            copy.deepcopy(rotation_packet),
            copy.deepcopy(state_policy),
            copy.deepcopy(previous_ledger),
        )
    except LEDGER.RotationStateLedgerError as exc:
        raise RotationCandidateSelectionDailyHandoffError(
            f"DAILY_ROTATION_LEDGER_REDERIVATION_FAILED:{exc}"
        ) from exc


def retained_rotation_inputs(daily_packet: dict) -> tuple[dict, dict]:
    briefing, frozen = _checked_daily_container(daily_packet)
    ledger = LEDGER.validate_ledger(_rederived_ledger(frozen))
    if ledger["payload_sha256"] != briefing["rotation"]["source_ledger_sha256"]:
        raise RotationCandidateSelectionDailyHandoffError(
            "DAILY_ROTATION_LEDGER_BINDING_MISMATCH"
        )
    return copy.deepcopy(briefing), copy.deepcopy(ledger)


def build_from_daily_packet(
    daily_packet: dict, stage2_reference: dict, stage1_reference: dict,
) -> dict:
    briefing, ledger = retained_rotation_inputs(daily_packet)
    return STAGE3.build_candidate_selection_input(
        briefing,
        ledger,
        stage2_reference=stage2_reference,
        stage1_reference=stage1_reference,
    )


def run(
    daily_path: Path, stage2_path: Path, stage1_path: Path, output_path: Path,
) -> int:
    try:
        packet = build_from_daily_packet(
            _read_json(daily_path), _read_json(stage2_path), _read_json(stage1_path)
        )
        STAGE3.write_json_atomic(output_path, packet)
        return 0
    except (
        RotationCandidateSelectionDailyHandoffError,
        STAGE3.RotationCandidateSelectionInputError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"Rotation candidate-selection daily handoff failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Connect one retained daily Rotation component to Stage 3"
    )
    parser.add_argument("daily_packet", type=Path)
    parser.add_argument("--stage2-posture-reference", type=Path, required=True)
    parser.add_argument("--stage1-paper-reference", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(
        args.daily_packet,
        args.stage2_posture_reference,
        args.stage1_paper_reference,
        args.out,
    )


if __name__ == "__main__":
    raise SystemExit(main())
