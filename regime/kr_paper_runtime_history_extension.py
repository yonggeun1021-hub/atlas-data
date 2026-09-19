#!/usr/bin/env python3
"""Roll the accepted KR 28-session common-v1 history forward one session.

The unchanged KR runtime bridge accepts exactly 28 historical steps that end
on the live observation's previous session.  This module derives that window
for the next session from:

* the predecessor window, which must itself pass the unchanged bridge
  ``validate_historical_replay``; and
* the one validated natural observation for the predecessor's next session
  (its aggregate reference packet), whose axis directions are rederived with
  the unchanged KR normalization.

The oldest step is dropped, the observation is appended with the same packet
identity the live evaluation used, and the unchanged common-v1 replay is run
again.  No threshold, weight, normalization or hysteresis rule is introduced.
A missing session is never skipped: the chain stops (fail closed).  The output
receipt is a mechanical derivation; whether it may be used at runtime is the
adoption record's decision, not this module's.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import decision_authority as COMMON
from regime import kr_information_system_runtime_bridge as BRIDGE
from regime import paper_regime_reference as REFERENCE


EXTENSION_METHOD = "ROLLING_28_SESSION_COMMON_V1_EXTENSION_V1"
RECEIPT_SCHEMA = "kr_contiguous_historical_pit_receipt/1"
RECEIPT_STATUS = (
    "KR_HISTORICAL_PIT_CONDITION_SATISFIED_BY_ROLLING_EXTENSION_RUNTIME_AUTHORITY_CLOSED"
)
WINDOW = 28


class HistoryExtensionError(ValueError):
    """The next history window cannot be derived exactly."""


def fail(code: str) -> None:
    raise HistoryExtensionError(code)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def history_file_name(through_date: str) -> str:
    return f"common-v1-replay-through-{through_date}.json"


def observation_step(reference_raw: bytes, manifest_raw: bytes) -> dict:
    """Extract one natural observation step from aggregate bytes only.

    Raw provider rows are validated by the bridge when the bundle is admitted;
    this function needs only the aggregate reference and the manifest.
    """
    try:
        wrapper = BRIDGE.object_from(reference_raw, "REFERENCE_JSON_INVALID")
        manifest = BRIDGE.object_from(manifest_raw, "SOURCE_MANIFEST_JSON_INVALID")
    except BRIDGE.InformationSystemRuntimeError as exc:
        raise HistoryExtensionError(str(exc)) from exc
    source = wrapper.get("source_packet")
    if not isinstance(source, dict):
        fail("SOURCE_PACKET_MISSING")
    unsigned = dict(source)
    claimed = unsigned.pop("payload_sha256", None)
    if claimed != sha256(BRIDGE.canonical_bytes(unsigned)):
        fail("SOURCE_PACKET_PAYLOAD_HASH_MISMATCH")
    if manifest.get("payload_sha256") != source.get("source", {}).get(
        "source_capture", {}
    ).get("manifest_payload_sha256"):
        fail("SOURCE_MANIFEST_BINDING_MISMATCH")
    dates = manifest.get("dates")
    if not isinstance(dates, list) or len(dates) != 2:
        fail("SOURCE_MANIFEST_DATES_INVALID")
    iso = [f"{day[:4]}-{day[4:6]}-{day[6:]}" for day in dates]
    if (source.get("previous_date"), source.get("as_of_date")) != tuple(iso):
        fail("SOURCE_SESSION_IDENTITY_MISMATCH")
    policy = BRIDGE.object_from(
        BRIDGE.REFERENCE_POLICY_PATH.read_bytes(), "REFERENCE_POLICY_INVALID"
    )
    try:
        normalized = REFERENCE.normalize_kr_measurements(source, policy)
    except Exception as exc:  # the reference module raises its own error type
        raise HistoryExtensionError("REFERENCE_NORMALIZATION_FAILED") from exc
    directions = {row["axis"]: row["direction"] for row in normalized}
    return {
        "packet_id": claimed,
        "previous_date": source["previous_date"],
        "as_of_date": source["as_of_date"],
        "available_at": source.get("available_at"),
        "axis_directions": directions,
        "reference_sha256": sha256(reference_raw),
        "manifest_sha256": sha256(manifest_raw),
    }


def _validated(history_raw: bytes, receipt_raw: bytes) -> dict:
    try:
        report, _ = BRIDGE.validate_historical_replay(
            history_raw, sha256(history_raw), receipt_raw, sha256(receipt_raw)
        )
    except BRIDGE.InformationSystemRuntimeError as exc:
        raise HistoryExtensionError(f"PREDECESSOR_{exc}") from exc
    return report


def extend(
    history_raw: bytes,
    receipt_raw: bytes,
    observation: dict,
    *,
    validation_sha256: str,
) -> tuple[bytes, bytes]:
    """Return (history bytes, receipt bytes) for the window ending on observation."""
    if not isinstance(validation_sha256, str) or not BRIDGE.SHA256.fullmatch(validation_sha256):
        fail("OBSERVATION_VALIDATION_BINDING_MISSING")
    report = _validated(history_raw, receipt_raw)
    predecessor = json_object(receipt_raw)
    steps = report["steps"]
    if steps[-1]["as_of_date"] != observation["previous_date"]:
        fail("EXTENSION_CHAIN_GAP")
    if observation["as_of_date"] <= steps[-1]["as_of_date"]:
        fail("EXTENSION_ORDER_INVALID")
    sequence_steps = [
        {
            "packet_id": row["packet_id"],
            "as_of_date": row["as_of_date"],
            "axes": {
                axis: {"status": "DEFINED", "direction": direction}
                for axis, direction in row["axis_directions"].items()
            },
        }
        for row in steps[1:]
    ]
    sequence_steps.append(
        {
            "packet_id": observation["packet_id"],
            "as_of_date": observation["as_of_date"],
            "axes": {
                axis: {"status": "DEFINED", "direction": direction}
                for axis, direction in observation["axis_directions"].items()
            },
        }
    )
    try:
        rebuilt = COMMON.replay_common_v1(
            {
                "schema_version": 1,
                "market": "KR",
                "case_id": report["case_id"],
                "steps": sequence_steps,
            }
        )
    except COMMON.DecisionAuthorityError as exc:
        raise HistoryExtensionError("EXTENSION_REPLAY_INVALID") from exc
    if rebuilt.get("step_count") != WINDOW:
        fail("EXTENSION_WINDOW_INVALID")
    history_bytes = BRIDGE.pretty_bytes(rebuilt)
    confirmed = sorted(
        {row["confirmed_regime"] for row in rebuilt["steps"]} - {"UNKNOWN"}
    )
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": RECEIPT_STATUS,
        "authority": copy.deepcopy(predecessor.get("authority")),
        "coverage": {
            "blocked_count": 0,
            "blocked_records": [],
            "complete_five_axis_count": WINDOW,
            "missing_rate": "0.000000",
            "observed_count": WINDOW,
            "requested_session_count": WINDOW,
        },
        "range": {
            "start": rebuilt["steps"][0]["as_of_date"],
            "end": rebuilt["steps"][-1]["as_of_date"],
        },
        "selection": {
            "all_official_open_sessions_in_range_requested": True,
            "regime_based_date_selection_used": False,
        },
        "pit_status": {
            "status": "PIT_ACCEPTED",
            "evaluated_date_count": WINDOW,
            "reasons": [],
            "replay_report_sha256": COMMON.payload_sha256(rebuilt),
            "confirmed_regimes_in_window": confirmed,
        },
        "derivation": {
            "method": EXTENSION_METHOD,
            "predecessor_history_sha256": sha256(history_raw),
            "predecessor_acceptance_sha256": sha256(receipt_raw),
            "dropped_session": steps[0]["as_of_date"],
            "appended_observation": {
                "as_of_date": observation["as_of_date"],
                "previous_date": observation["previous_date"],
                "packet_id": observation["packet_id"],
                "available_at": observation["available_at"],
                "reference_sha256": observation["reference_sha256"],
                "manifest_sha256": observation["manifest_sha256"],
                "validation_sha256": validation_sha256,
            },
        },
        "integration_boundary": {
            "historical_pit_condition_satisfied": True,
            "latest_market_scoped_pit_pointer_updated": False,
            "runtime_integration_requires_separate_ratification": True,
            "runtime_market_judgement_visible": False,
        },
    }
    receipt_bytes = BRIDGE.pretty_bytes(receipt)
    # The unchanged bridge validator must accept the derived window as-is.
    _validated(history_bytes, receipt_bytes)
    return history_bytes, receipt_bytes


def verify_extension(
    history_raw: bytes,
    receipt_raw: bytes,
    predecessor_history_raw: bytes,
    predecessor_receipt_raw: bytes,
    observation: dict,
) -> None:
    """Rebuild a committed extension and compare bytes exactly."""
    receipt = json_object(receipt_raw)
    appended = receipt.get("derivation", {}).get("appended_observation", {})
    expected = extend(
        predecessor_history_raw,
        predecessor_receipt_raw,
        observation,
        validation_sha256=appended.get("validation_sha256"),
    )
    if expected != (history_raw, receipt_raw):
        fail("EXTENSION_BYTES_MISMATCH")


def json_object(raw: bytes) -> dict:
    try:
        return BRIDGE.object_from(raw, "JSON_INVALID")
    except BRIDGE.InformationSystemRuntimeError as exc:
        raise HistoryExtensionError(str(exc)) from exc
