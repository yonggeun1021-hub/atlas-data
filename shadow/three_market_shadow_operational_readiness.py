#!/usr/bin/env python3
"""P10-01 live-input readiness boundary for the zero-capital Shadow ledger."""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "three_market_shadow_operational_readiness_contract.json"
DEFAULT_HISTORY_ROOT = (
    ROOT / "evidence" / "operational" / "three_market_shadow_readiness" / "records"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DAILY_LINEAGE = _load(
    "atlas_p10_01_daily_lineage", "decision/decision_change_lineage_operational.py"
)
SHADOW = _load("atlas_p10_01_shadow", "shadow/three_market_shadow_ledger.py")


class ThreeMarketShadowOperationalReadinessError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ThreeMarketShadowOperationalReadinessError(
            f"JSON_READ_FAILED:{path}:{exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ThreeMarketShadowOperationalReadinessError("JSON_OBJECT_REQUIRED")
    return value


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "three_market_shadow_operational_readiness/1",
        "approval_status": "IMPLEMENTED_FAIL_CLOSED_NO_NEW_POLICY",
        "required_daily_components": [
            "UNIFIED_DECISION",
            "ENTRY_EXIT_TRIGGER_ELIGIBILITY",
            "INTRADAY_RISK_ESCALATION",
        ],
        "input_status_vocabulary": [
            "READY_VALIDATED",
            "NOT_AVAILABLE_DAILY_COMPONENT_NOT_WIRED",
        ],
        "readiness_status_vocabulary": [
            "READY_FOR_ZERO_CAPITAL_SHADOW_APPEND",
            "BLOCKED_MISSING_EXACT_P9_LIVE_INPUTS",
        ],
        "authority": {
            "shadow_observation_recording_authorized": False,
            "action_generation_authorized": False,
            "capital_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if value != expected:
        raise ThreeMarketShadowOperationalReadinessError("CONTRACT_TAMPER_OR_DRIFT")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return validate_contract(_read_json(path))


def _components(daily: dict) -> dict[str, dict]:
    rows = daily.get("components")
    if not isinstance(rows, list):
        raise ThreeMarketShadowOperationalReadinessError("DAILY_COMPONENTS_NOT_LIST")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("component_id"), str):
            raise ThreeMarketShadowOperationalReadinessError("DAILY_COMPONENT_ROW_INVALID")
        component_id = row["component_id"]
        if component_id in result:
            raise ThreeMarketShadowOperationalReadinessError(
                f"DAILY_COMPONENT_DUPLICATE:{component_id}"
            )
        result[component_id] = row
    return result


def _validate_shadow_inputs_at_commit(
    source_commit: str, relative: str
) -> tuple[dict, dict, dict]:
    """Validate all three Shadow inputs with the immutable commit's code.

    A historical Daily packet must never be reinterpreted by today's validator:
    additive component schemas legitimately evolve.  When the two P9 packets
    eventually become live Daily components, this helper validates their
    schemas and cross-packet lineage inside an isolated archive of the exact
    source commit.
    """
    # _git_blob performs the full immutable-SHA verification before archive use.
    DAILY_LINEAGE._git_blob(source_commit, relative)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", source_commit],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if archive.returncode != 0:
        raise ThreeMarketShadowOperationalReadinessError(
            "SOURCE_COMMIT_ARCHIVE_UNAVAILABLE"
        )
    program = r'''
import importlib.util
import json
from pathlib import Path
import sys

relative = sys.argv[1]
daily = json.loads(Path(relative).read_text(encoding="utf-8"))
components = {
    row["component_id"]: row["packet"]
    for row in daily["components"]
    if isinstance(row, dict) and row.get("validated") is True
    and isinstance(row.get("packet"), dict)
}
spec = importlib.util.spec_from_file_location(
    "exact_three_market_shadow", Path("shadow/three_market_shadow_ledger.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
unified = module.UNIFIED.validate_packet(components["UNIFIED_DECISION"])
entry_exit, intraday_risk = module._validate_intraday_sources(
    unified,
    components["ENTRY_EXIT_TRIGGER_ELIGIBILITY"],
    components["INTRADAY_RISK_ESCALATION"],
    module.load_contract(),
)
print(json.dumps({
    "unified": unified,
    "entry_exit": entry_exit,
    "intraday_risk": intraday_risk,
}, sort_keys=True))
'''
    with tempfile.TemporaryDirectory(prefix="atlas-p10-01-validate-") as temporary:
        checkout = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as handle:
            members = handle.getmembers()
            if any(
                member.name.startswith("/") or ".." in Path(member.name).parts
                for member in members
            ):
                raise ThreeMarketShadowOperationalReadinessError(
                    "SOURCE_ARCHIVE_PATH_INVALID"
                )
            handle.extractall(checkout)
        completed = subprocess.run(
            [sys.executable, "-c", program, relative],
            cwd=checkout,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    if completed.returncode != 0:
        raise ThreeMarketShadowOperationalReadinessError(
            f"P9_LIVE_INPUTS_INVALID_AT_SOURCE_COMMIT:{completed.stdout.strip()}"
        )
    try:
        checked = json.loads(completed.stdout)
        return checked["unified"], checked["entry_exit"], checked["intraday_risk"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ThreeMarketShadowOperationalReadinessError(
            "P9_SOURCE_COMMIT_VALIDATION_OUTPUT_INVALID"
        ) from exc


def _derive_packet(
    briefing_path: Path,
    source_commit: str,
    recorded_at: str,
    contract: dict,
) -> dict:
    DAILY_LINEAGE._utc(recorded_at, "RECORDED_AT_INVALID")
    relative = DAILY_LINEAGE._repo_relative(briefing_path)
    disk_bytes = DAILY_LINEAGE._read_bytes(briefing_path)
    blob = DAILY_LINEAGE._git_blob(source_commit, relative)
    if disk_bytes != blob:
        raise ThreeMarketShadowOperationalReadinessError("SOURCE_DISK_COMMIT_MISMATCH")
    blob_sha = hashlib.sha256(blob).hexdigest()
    daily = DAILY_LINEAGE._validate_daily_at_commit(source_commit, relative, blob_sha)
    components = _components(daily)
    statuses = {}
    source_packets = {}
    for component_id in contract["required_daily_components"]:
        row = components.get(component_id)
        if (
            isinstance(row, dict)
            and row.get("validated") is True
            and isinstance(row.get("packet"), dict)
        ):
            statuses[component_id] = "READY_VALIDATED"
            source_packets[component_id] = copy.deepcopy(row["packet"])
        else:
            statuses[component_id] = "NOT_AVAILABLE_DAILY_COMPONENT_NOT_WIRED"
            source_packets[component_id] = None
    if statuses["UNIFIED_DECISION"] != "READY_VALIDATED":
        raise ThreeMarketShadowOperationalReadinessError(
            "UNIFIED_DECISION_MUST_BE_VALIDATED"
        )
    # _validate_daily_at_commit already ran the source commit's own complete
    # Daily validator. Do not feed a valid historical component into the current
    # checkout's Unified validator: that creates schema-lookahead drift.
    validated_unified = copy.deepcopy(source_packets["UNIFIED_DECISION"])
    if DAILY_LINEAGE._utc(
        validated_unified["generated_at"], "UNIFIED_GENERATED_AT_INVALID"
    ) > DAILY_LINEAGE._utc(recorded_at, "RECORDED_AT_INVALID"):
        raise ThreeMarketShadowOperationalReadinessError("UNIFIED_DECISION_FROM_FUTURE")
    shadow_inputs_ready = all(
        statuses[name] == "READY_VALIDATED"
        for name in contract["required_daily_components"]
    )
    entry_exit_packet_sha256 = None
    intraday_risk_packet_sha256 = None
    if shadow_inputs_ready:
        (
            exact_unified,
            checked_entry_exit,
            checked_intraday_risk,
        ) = _validate_shadow_inputs_at_commit(source_commit, relative)
        if exact_unified != validated_unified:
            raise ThreeMarketShadowOperationalReadinessError(
                "SOURCE_COMMIT_UNIFIED_VALIDATION_MISMATCH"
            )
        entry_exit_packet_sha256 = checked_entry_exit["packet_sha256"]
        intraday_risk_packet_sha256 = checked_intraday_risk["packet_sha256"]
        recorded = DAILY_LINEAGE._utc(recorded_at, "RECORDED_AT_INVALID")
        if max(
            DAILY_LINEAGE._utc(
                checked_entry_exit["generated_at"], "ENTRY_EXIT_GENERATED_AT_INVALID"
            ),
            DAILY_LINEAGE._utc(
                checked_intraday_risk["observed_at"], "INTRADAY_RISK_OBSERVED_AT_INVALID"
            ),
        ) > recorded:
            raise ThreeMarketShadowOperationalReadinessError(
                "P9_LIVE_INPUTS_FROM_FUTURE"
            )
    # Actual appending remains structurally closed until both exact P9 packets
    # are live components. This readiness boundary never fabricates either.
    packet = {
        "schema_version": "three_market_shadow_operational_readiness_packet/1",
        "contract_version": contract["contract_version"],
        "recorded_at": recorded_at,
        "status": (
            "READY_FOR_ZERO_CAPITAL_SHADOW_APPEND"
            if shadow_inputs_ready
            else "BLOCKED_MISSING_EXACT_P9_LIVE_INPUTS"
        ),
        "source": {
            "source_commit": source_commit,
            "daily_briefing_path": relative,
            "daily_briefing_blob_sha256": blob_sha,
            "daily_briefing_packet_sha256": daily["packet_sha256"],
            "unified_decision_packet_sha256": validated_unified["packet_sha256"],
            "entry_exit_trigger_eligibility_packet_sha256": entry_exit_packet_sha256,
            "intraday_risk_escalation_packet_sha256": intraday_risk_packet_sha256,
        },
        "input_status": statuses,
        "summary": {
            "unified_decision_ready_count": 1,
            "entry_exit_trigger_eligibility_ready_count": int(
                statuses["ENTRY_EXIT_TRIGGER_ELIGIBILITY"] == "READY_VALIDATED"
            ),
            "intraday_risk_escalation_ready_count": int(
                statuses["INTRADAY_RISK_ESCALATION"] == "READY_VALIDATED"
            ),
            "shadow_append_ready_count": int(shadow_inputs_ready),
            "shadow_record_count": 0,
            "real_capital_deployed": "0",
            "real_order_count": 0,
        },
        "shadow_ledger": None,
        "action": None,
        "order_intent": None,
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def build_packet(
    briefing_path: Path,
    source_commit: str,
    recorded_at: str,
    contract: dict | None = None,
) -> dict:
    contract = validate_contract(contract) if contract is not None else load_contract()
    packet = _derive_packet(briefing_path, source_commit, recorded_at, contract)
    return validate_packet(packet, briefing_path, source_commit, recorded_at, contract)


def validate_packet(
    packet: dict,
    briefing_path: Path,
    source_commit: str,
    recorded_at: str,
    contract: dict | None = None,
) -> dict:
    contract = validate_contract(contract) if contract is not None else load_contract()
    expected = copy.deepcopy(packet)
    expected.pop("packet_sha256", None)
    if not isinstance(packet.get("packet_sha256"), str) or SHA256_RE.fullmatch(
        packet["packet_sha256"]
    ) is None:
        raise ThreeMarketShadowOperationalReadinessError("PACKET_SHA256_INVALID")
    if payload_sha256(expected) != packet["packet_sha256"]:
        raise ThreeMarketShadowOperationalReadinessError("PACKET_SHA256_MISMATCH")
    # Independent semantic re-derivation through the same pure implementation.
    rebuilt = _derive_packet(briefing_path, source_commit, recorded_at, contract)
    if packet != rebuilt:
        raise ThreeMarketShadowOperationalReadinessError(
            "SHADOW_OPERATIONAL_READINESS_SEMANTIC_TAMPER_OR_DRIFT"
        )
    return copy.deepcopy(packet)


def write_packet(packet: dict, root: Path = DEFAULT_HISTORY_ROOT) -> tuple[Path, bool]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"readiness-{packet['packet_sha256']}.json"
    encoded = (json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != encoded:
            raise ThreeMarketShadowOperationalReadinessError(
                "CONTENT_ADDRESSED_READINESS_COLLISION"
            )
        return path, False
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(root))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return path, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("briefing_packet", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--recorded-at", required=True)
    parser.add_argument("--history-root", type=Path, default=DEFAULT_HISTORY_ROOT)
    args = parser.parse_args()
    try:
        packet = build_packet(args.briefing_packet, args.source_commit, args.recorded_at)
        path, created = write_packet(packet, args.history_root)
        print(f"readiness_path={path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
        print(f"readiness_created={'true' if created else 'false'}")
        print(json.dumps(packet["summary"], sort_keys=True))
        return 0
    except (ThreeMarketShadowOperationalReadinessError, OSError, ValueError) as exc:
        print(f"Three-market Shadow readiness failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
