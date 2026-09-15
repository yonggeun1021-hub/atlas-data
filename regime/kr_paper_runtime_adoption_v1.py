#!/usr/bin/env python3
"""KR_PAPER_RUNTIME_ADOPTION_V1: daily KR PAPER display decision publisher.

CIO decision CIO-KR-RUNTIME-DAILY-ADOPTION-20260914 replaced the hand-ratified
date-pinned qualification with a non-date-pinned adoption identity.  This
module applies it mechanically and nothing more:

1. The adoption record (evidence/authority/kr_paper_runtime_adoption_v1.json)
   pins the implementation/policy/contract hashes of the 2026-09-13
   qualification.  Any drift fails closed (re-qualification required).
2. A capture bundle for session D is admitted only if the unchanged bridge
   ``validate_natural_evidence`` accepts its raw provider bytes and its
   provenance names the scheduled producer.  Raw provider rows are never
   written to the repository; only aggregate reference, manifest (hashes),
   provenance and a validation record are committed.
3. The execution session E is the next OPEN_REGULAR session in the committed
   calendar packets derived from the official KRX capture.
4. The 28-session history ending on the previous session is rederived from
   the accepted root and the committed chain of validated observations
   (rolling extension; gated by the adoption record's CIO confirmation).
5. A per-session qualification is derived from the adoption pins plus the
   bundle hashes and the unchanged ``evaluate_kr_paper_runtime`` produces the
   ``kr_paper_runtime_decision/5`` packet.  Stale/missing inputs stay UNKNOWN.

It never grants strategy, capital, order, trading or REAL authority.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import decision_authority as COMMON
from regime import kr_information_system_runtime_bridge as BRIDGE
from regime import kr_paper_runtime as RUNTIME
from regime import kr_paper_runtime_calendar_packets as PACKETS
from regime import kr_paper_runtime_history_extension as EXTENSION


ADOPTION_PATH = "evidence/authority/kr_paper_runtime_adoption_v1.json"
ADOPTION_ID = "KR_PAPER_RUNTIME_ADOPTION_V1"
ADOPTION_STATUS = "CIO_ADOPTED_KR_PAPER_DISPLAY_ONLY"
ROLLING_CONFIRMED = "CIO_CONFIRMED"
EVIDENCE_BASE = "evidence/regime/kr_information_system"
OUTPUT_PATH = "data/latest_kr_paper_runtime_decision.json"
QUALIFICATION_SCHEMA = "kr_information_system_runtime_qualification/1"
PROVENANCE_SCHEMA = "kr_paper_runtime_bundle_provenance/1"
VALIDATION_SCHEMA = "kr_paper_runtime_bundle_validation/1"
PUBLICATION_SCHEMA = "kr_paper_runtime_daily_publication/1"
REFERENCE_NAME = "KR_PAPER_REFERENCE_CANDIDATE.json"
MANIFEST_NAME = "source-capture/manifest.json"
# Committed dated packets carry no raw rows, so the manifest is kept outside
# source-capture/: consumers that glob */source-capture/manifest.json expect
# the retained provider bodies next to it (only the ratified 2026-09-11 root).
COMMITTED_MANIFEST_NAME = "source-manifest.json"
ROLLED_RECEIPT_NAME = "rolling-extension-receipt.json"
BOT_AUTHOR = "github-actions[bot]"
SEOUL = ZoneInfo("Asia/Seoul")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
MAX_SESSION_GAP_DAYS = 14


class AdoptionError(ValueError):
    """The adopted daily publication cannot proceed; runtime stays UNKNOWN."""


def fail(code: str) -> None:
    raise AdoptionError(code)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def pretty(value: object) -> bytes:
    return BRIDGE.pretty_bytes(value)


def _object(raw: bytes, code: str) -> dict:
    try:
        return BRIDGE.object_from(raw, code)
    except BRIDGE.InformationSystemRuntimeError as exc:
        raise AdoptionError(str(exc)) from exc


def _instant(value: object, code: str) -> dt.datetime:
    try:
        return BRIDGE.instant(value, code)
    except BRIDGE.InformationSystemRuntimeError as exc:
        raise AdoptionError(str(exc)) from exc


def _utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _iso(yyyymmdd: str) -> str:
    return dt.datetime.strptime(yyyymmdd, "%Y%m%d").date().isoformat()


# --------------------------------------------------------------------------
# Adoption record
# --------------------------------------------------------------------------

def current_pins() -> dict:
    return {
        "common_policy_binding_sha256": COMMON.payload_sha256(
            COMMON.load_common_v1_policy()["binding"]
        ),
        "implementation_sha256": {
            path: sha256((ROOT / path).read_bytes()) for path in BRIDGE.IMPLEMENTATION_PATHS
        },
        "leadership_policy_sha256": sha256(BRIDGE.LEADERSHIP_POLICY_PATH.read_bytes()),
        "reference_policy_sha256": sha256(BRIDGE.REFERENCE_POLICY_PATH.read_bytes()),
        "source_contract_sha256": sha256(BRIDGE.SOURCE_CONTRACT_PATH.read_bytes()),
    }


def load_adoption(raw: bytes | None = None, root: Path = ROOT) -> tuple[dict, bytes]:
    raw = (root / ADOPTION_PATH).read_bytes() if raw is None else raw
    record = _object(raw, "ADOPTION_JSON_INVALID")
    if record.get("schema_version") != "kr_paper_runtime_adoption/1" or record.get(
        "adoption_id"
    ) != ADOPTION_ID:
        fail("ADOPTION_IDENTITY_INVALID")
    if record.get("status") != ADOPTION_STATUS:
        fail("ADOPTION_NOT_ADOPTED")
    authority = record.get("authority")
    if not isinstance(authority, dict) or authority.get("paper_runtime_display_authorized") is not True:
        fail("ADOPTION_AUTHORITY_INVALID")
    if any(value is not False for key, value in authority.items()
           if key != "paper_runtime_display_authorized"):
        fail("ADOPTION_AUTHORITY_ESCALATION")
    if record.get("pinned_bindings") != current_pins():
        fail("ADOPTION_PIN_DRIFT_REQUALIFICATION_REQUIRED")
    if record.get("calendar", {}).get("root") != PACKETS.CALENDAR_ROOT or record.get(
        "calendar", {}
    ).get("capture_sha256") != PACKETS.CAPTURE_SHA256:
        fail("ADOPTION_CALENDAR_BINDING_INVALID")
    producer = record.get("bundle_producer", {})
    if producer.get("manual_edits_authorized") is not False or producer.get(
        "raw_provider_rows_committed"
    ) is not False:
        fail("ADOPTION_PRODUCER_BOUNDARY_INVALID")
    _instant(record.get("effective_at"), "ADOPTION_EFFECTIVE_AT_INVALID")
    return record, raw


def rolling_confirmed(record: dict) -> bool:
    rolling = record.get("rolling_history_extension", {})
    return (
        rolling.get("method") == EXTENSION.EXTENSION_METHOD
        and rolling.get("status") == ROLLING_CONFIRMED
        and isinstance(rolling.get("confirmation_ref"), str)
        and BRIDGE.SHA256.fullmatch(rolling["confirmation_ref"]) is not None
    )


# --------------------------------------------------------------------------
# Sessions (committed calendar packets only)
# --------------------------------------------------------------------------

def _is_open(day: dt.date, root: Path) -> bool:
    return PACKETS.load_packet(day, root)["calendar"]["status"] == "OPEN_REGULAR"


def _close(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(15, 30), SEOUL)


def display_floor(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(18, 0), SEOUL)


def next_open_session(day: dt.date, root: Path = ROOT) -> dt.date:
    for offset in range(1, MAX_SESSION_GAP_DAYS + 1):
        candidate = day + dt.timedelta(days=offset)
        if _is_open(candidate, root):
            return candidate
    fail("EXECUTION_SESSION_NOT_FOUND")


def previous_open_session(day: dt.date, root: Path = ROOT) -> dt.date:
    for offset in range(1, MAX_SESSION_GAP_DAYS + 1):
        candidate = day - dt.timedelta(days=offset)
        if _is_open(candidate, root):
            return candidate
    fail("PREVIOUS_SESSION_NOT_FOUND")


def last_completed_pair(now: dt.datetime, root: Path = ROOT) -> tuple[dt.date, dt.date]:
    """Return (previous, current) completed sessions at ``now`` (capture rule)."""
    completed = []
    cursor = now.astimezone(SEOUL).date()
    for _ in range(3 * MAX_SESSION_GAP_DAYS):
        if _is_open(cursor, root) and now >= _close(cursor):
            completed.append(cursor)
            if len(completed) == 2:
                return completed[1], completed[0]
        cursor -= dt.timedelta(days=1)
    fail("COMPLETED_SESSION_PAIR_NOT_FOUND")


def session_boundary(context: dt.date, execution: dt.date, code_revision: str) -> dict:
    days = (execution - context).days
    return {
        "schema_version": RUNTIME.SESSION_BOUNDARY_INPUT_SCHEMA,
        "context_session_date": context.isoformat(),
        "execution_session_date": execution.isoformat(),
        "context_session_close_at": _utc(_close(context)),
        "execution_session_close_at": _utc(_close(execution)),
        "session_calendar_packet_paths": [
            PACKETS.packet_paths(context + dt.timedelta(days=offset))[0]
            for offset in range(days + 1)
        ],
        "trusted_commit": code_revision,
    }


# --------------------------------------------------------------------------
# Bundles, provenance, validated observation chain
# --------------------------------------------------------------------------

def read_bundle(bundle_dir: Path) -> dict:
    bundle_dir = Path(bundle_dir)
    reference = (bundle_dir / REFERENCE_NAME).read_bytes()
    manifest = (bundle_dir / MANIFEST_NAME).read_bytes()
    responses = {
        str(path.relative_to(bundle_dir / "source-capture")): path.read_bytes()
        for path in sorted((bundle_dir / "source-capture/responses").glob("*.json"))
    }
    if not responses:
        fail("BUNDLE_RAW_RESPONSES_MISSING")
    return {"reference_raw": reference, "manifest_raw": manifest, "raw_responses": responses}


def expected_source(record: dict, reference: bytes, manifest: bytes) -> dict:
    pins = record["pinned_bindings"]
    return {
        "reference_sha256": sha256(reference),
        "manifest_sha256": sha256(manifest),
        "source_contract_sha256": pins["source_contract_sha256"],
        "leadership_policy_sha256": pins["leadership_policy_sha256"],
        "reference_policy_sha256": pins["reference_policy_sha256"],
    }


def validate_provenance(raw: bytes, record: dict, current_date: str) -> dict:
    value = _object(raw, "PROVENANCE_JSON_INVALID")
    producer = record["bundle_producer"]
    mode = value.get("capture_mode")
    if value.get("schema_version") != PROVENANCE_SCHEMA:
        fail("PROVENANCE_SCHEMA_INVALID")
    if value.get("repository") != producer["repository"]:
        fail("PROVENANCE_REPOSITORY_INVALID")
    if mode not in producer["capture_modes"] or value.get("workflow_path") != producer[
        "capture_modes"
    ][mode]:
        fail("PROVENANCE_PRODUCER_INVALID")
    if value.get("producer_script") != producer["producer_script"]:
        fail("PROVENANCE_PRODUCER_INVALID")
    if value.get("event") not in {"schedule", "workflow_dispatch", "workflow_run"}:
        fail("PROVENANCE_EVENT_INVALID")
    if type(value.get("run_id")) is not int or type(value.get("run_attempt")) is not int:
        fail("PROVENANCE_RUN_INVALID")
    if not isinstance(value.get("head_sha"), str) or not SHA40.fullmatch(value["head_sha"]):
        fail("PROVENANCE_HEAD_INVALID")
    if value.get("manual_edits") is not False:
        fail("PROVENANCE_MANUAL_EDIT")
    if mode == "ARTIFACT_HANDOFF":
        if value.get("conclusion") != "success" or value.get(
            "artifact_name"
        ) != f"korea-market-signals-{current_date.replace('-', '')}":
            fail("PROVENANCE_ARTIFACT_INVALID")
    return value


def validation_record(record_raw: bytes, bundle: dict, provenance_raw: bytes) -> bytes:
    manifest = _object(bundle["manifest_raw"], "SOURCE_MANIFEST_JSON_INVALID")
    previous, current = (_iso(day) for day in manifest["dates"])
    return pretty({
        "schema_version": VALIDATION_SCHEMA,
        "status": "BRIDGE_NATURAL_EVIDENCE_VALIDATED",
        "adoption_id": ADOPTION_ID,
        "adoption_record_sha256": sha256(record_raw),
        "validator": "regime/kr_information_system_runtime_bridge.py::validate_natural_evidence",
        "previous_date": previous,
        "as_of_date": current,
        "source_reference_sha256": sha256(bundle["reference_raw"]),
        "source_manifest_sha256": sha256(bundle["manifest_raw"]),
        "raw_response_sha256": {
            path: sha256(raw) for path, raw in sorted(bundle["raw_responses"].items())
        },
        "raw_provider_rows_committed": False,
        "provenance_sha256": sha256(provenance_raw),
    })


def _committed_by_bot(root: Path, relative: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(root), "log", "--diff-filter=A", "--format=%an", "--", relative],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False,
    )
    authors = [line for line in proc.stdout.splitlines() if line.strip()]
    return proc.returncode == 0 and authors == [BOT_AUTHOR]


def validated_observation(day: str, record: dict, root: Path, require_bot: bool) -> dict:
    """Load one committed validated observation from aggregate bytes."""
    base = root / EVIDENCE_BASE / day
    history_root = record["history_root"]
    manifest_name = MANIFEST_NAME if day == history_root["context_session_date"] else COMMITTED_MANIFEST_NAME
    reference = (base / REFERENCE_NAME).read_bytes() if (base / REFERENCE_NAME).is_file() else None
    manifest = (base / manifest_name).read_bytes() if (base / manifest_name).is_file() else None
    if reference is None or manifest is None:
        fail(f"HISTORY_CHAIN_GAP:{day}")
    if day == history_root["context_session_date"]:
        if (sha256(reference), sha256(manifest)) != (
            history_root["source_reference_sha256"], history_root["source_manifest_sha256"]
        ):
            fail("HISTORY_ROOT_BYTES_MISMATCH")
        proof = (root / history_root["observation_validation_path"]).read_bytes()
        if sha256(proof) != history_root["observation_validation_sha256"]:
            fail("HISTORY_ROOT_VALIDATION_MISMATCH")
        validation_sha256 = history_root["observation_validation_sha256"]
    else:
        path = base / "validation.json"
        if not path.is_file():
            fail(f"HISTORY_CHAIN_UNVALIDATED:{day}")
        raw = path.read_bytes()
        value = _object(raw, "VALIDATION_JSON_INVALID")
        if value.get("schema_version") != VALIDATION_SCHEMA or value.get(
            "status"
        ) != "BRIDGE_NATURAL_EVIDENCE_VALIDATED" or value.get("as_of_date") != day:
            fail(f"HISTORY_CHAIN_UNVALIDATED:{day}")
        if (value.get("source_reference_sha256"), value.get("source_manifest_sha256")) != (
            sha256(reference), sha256(manifest)
        ):
            fail(f"HISTORY_CHAIN_BYTES_MISMATCH:{day}")
        if require_bot and not all(
            _committed_by_bot(root, f"{EVIDENCE_BASE}/{day}/{name}")
            for name in ("validation.json", REFERENCE_NAME, COMMITTED_MANIFEST_NAME)
        ):
            fail(f"HISTORY_CHAIN_NOT_SCHEDULED_PRODUCER:{day}")
        validation_sha256 = sha256(raw)
    try:
        observation = EXTENSION.observation_step(reference, manifest)
    except EXTENSION.HistoryExtensionError as exc:
        raise AdoptionError(f"HISTORY_CHAIN_OBSERVATION_INVALID:{day}:{exc}") from exc
    if observation["as_of_date"] != day:
        fail(f"HISTORY_CHAIN_BYTES_MISMATCH:{day}")
    observation["validation_sha256"] = validation_sha256
    return observation


def history_through(previous: str, record: dict, root: Path = ROOT,
                    require_bot: bool = False) -> tuple[bytes, bytes, bool]:
    """Return (history, receipt, rolled) for the 28-session window ending on ``previous``."""
    history_root = record["history_root"]
    history_raw = (root / history_root["historical_replay_path"]).read_bytes()
    receipt_raw = (root / history_root["historical_acceptance_path"]).read_bytes()
    if (sha256(history_raw), sha256(receipt_raw)) != (
        history_root["historical_replay_sha256"], history_root["historical_acceptance_sha256"]
    ):
        fail("HISTORY_ROOT_BYTES_MISMATCH")
    if previous == history_root["through_date"]:
        return history_raw, receipt_raw, False
    if previous < history_root["context_session_date"]:
        fail("HISTORY_BEFORE_ADOPTION_ROOT")
    chain = []
    cursor = previous
    while True:
        observation = validated_observation(cursor, record, root, require_bot)
        chain.append(observation)
        if cursor == history_root["context_session_date"]:
            break
        cursor = observation["previous_date"]
        if cursor < history_root["context_session_date"]:
            fail("HISTORY_CHAIN_ROOT_UNREACHABLE")
    if chain[-1]["previous_date"] != history_root["through_date"]:
        fail("HISTORY_CHAIN_ROOT_UNREACHABLE")
    if not rolling_confirmed(record):
        fail("ADOPTION_ROLLING_HISTORY_NOT_CONFIRMED")
    for observation in reversed(chain):
        try:
            history_raw, receipt_raw = EXTENSION.extend(
                history_raw, receipt_raw, observation,
                validation_sha256=observation["validation_sha256"],
            )
        except EXTENSION.HistoryExtensionError as exc:
            raise AdoptionError(f"HISTORY_EXTENSION_FAILED:{exc}") from exc
    return history_raw, receipt_raw, True


# --------------------------------------------------------------------------
# Qualification + decision
# --------------------------------------------------------------------------

def derive_qualification(record: dict, record_raw: bytes, bundle: dict, history_raw: bytes,
                         receipt_raw: bytes, context: dt.date, execution: dt.date,
                         available_at: str) -> bytes:
    pins = record["pinned_bindings"]
    effective = max(
        _instant(record["effective_at"], "ADOPTION_EFFECTIVE_AT_INVALID"),
        _instant(available_at, "SOURCE_AVAILABILITY_INVALID"),
    )
    return pretty({
        "schema_version": QUALIFICATION_SCHEMA,
        "status": "RATIFIED_KR_PAPER_DISPLAY_ONLY",
        "effective_at": _utc(effective),
        "authority": copy.deepcopy(record["authority"]),
        "bindings": {
            "common_policy_binding_sha256": pins["common_policy_binding_sha256"],
            "context_session_date": context.isoformat(),
            "execution_session_date": execution.isoformat(),
            "historical_acceptance_sha256": sha256(receipt_raw),
            "historical_replay_file_sha256": sha256(history_raw),
            "implementation_sha256": copy.deepcopy(pins["implementation_sha256"]),
            "leadership_policy_sha256": pins["leadership_policy_sha256"],
            "raw_response_sha256": {
                path: sha256(raw) for path, raw in sorted(bundle["raw_responses"].items())
            },
            "reference_policy_sha256": pins["reference_policy_sha256"],
            "source_contract_sha256": pins["source_contract_sha256"],
            "source_manifest_sha256": sha256(bundle["manifest_raw"]),
            "source_reference_sha256": sha256(bundle["reference_raw"]),
        },
        "derivation": {
            "adoption_id": ADOPTION_ID,
            "adoption_record_sha256": sha256(record_raw),
            "method": "MECHANICAL_PER_SESSION_DERIVATION_NO_MANUAL_EDIT",
        },
    })


def _decision_is_display(result: dict) -> bool:
    authority = result.get("authority")
    return (
        result.get("decision_status") == "PAPER_RUNTIME_CLASSIFIED"
        and result.get("runtime_decision_available") is True
        and result.get("actual_source_qualification") == "RATIFIED_KR_PAPER_DISPLAY_ONLY"
        and result.get("reasons") == []
        and isinstance(authority, dict)
        and authority.get("paper_runtime_display_authorized") is True
        and all(value is False for key, value in authority.items()
                if key != "paper_runtime_display_authorized")
    )


def publish(*, bundle_dir: Path, provenance_raw: bytes, evaluation_at: str,
            code_revision: str, root: Path = ROOT, require_bot_chain: bool = False) -> dict:
    """Admit one bundle, write its dated packet and (if displayable) the latest pointer."""
    record, record_raw = load_adoption(root=root)
    now = _instant(evaluation_at, "EVALUATION_TIME_INVALID")
    bundle = read_bundle(bundle_dir)
    manifest = _object(bundle["manifest_raw"], "SOURCE_MANIFEST_JSON_INVALID")
    dates = manifest.get("dates")
    if not isinstance(dates, list) or len(dates) != 2:
        fail("SOURCE_MANIFEST_DATES_INVALID")
    previous, current = (_iso(day) for day in dates)
    context = dt.date.fromisoformat(current)
    dated = root / EVIDENCE_BASE / current
    if (dated / "validation.json").exists() or (dated / "publication.json").exists():
        return {"status": "SKIPPED_EXISTING", "context_session_date": current}
    try:
        natural = BRIDGE.validate_natural_evidence(
            reference_raw=bundle["reference_raw"], manifest_raw=bundle["manifest_raw"],
            raw_responses=bundle["raw_responses"],
            expected=expected_source(record, bundle["reference_raw"], bundle["manifest_raw"]),
        )
    except BRIDGE.InformationSystemRuntimeError as exc:
        raise AdoptionError(f"BUNDLE_REJECTED:{exc}") from exc
    if _instant(natural["available_at"], "SOURCE_AVAILABILITY_INVALID") < display_floor(context):
        fail("BUNDLE_BEFORE_DISPLAY_FLOOR")
    if _instant(natural["available_at"], "SOURCE_AVAILABILITY_INVALID") > now:
        fail("BUNDLE_AFTER_EVALUATION")
    if not _is_open(context, root) or previous_open_session(context, root).isoformat() != previous:
        fail("SOURCE_PAIR_NOT_ADJACENT_OPEN_SESSIONS")
    validate_provenance(provenance_raw, record, current)
    validation_raw = validation_record(record_raw, bundle, provenance_raw)

    root_session = current == record["history_root"]["context_session_date"]
    files = {
        REFERENCE_NAME: bundle["reference_raw"],
        (MANIFEST_NAME if root_session else COMMITTED_MANIFEST_NAME): bundle["manifest_raw"],
        "provenance.json": provenance_raw,
        "validation.json": validation_raw,
    }
    result = None
    reason = None
    execution = None
    try:
        # Inside the try: a missing calendar packet (e.g. no 2027 capture yet)
        # records the observation with runtime UNKNOWN instead of losing it.
        execution = next_open_session(context, root)
        history_raw, receipt_raw, rolled = history_through(previous, record, root, require_bot_chain)
        qualification_raw = derive_qualification(
            record, record_raw, bundle, history_raw, receipt_raw, context, execution,
            natural["available_at"],
        )
        files[f"history/{EXTENSION.history_file_name(previous)}"] = history_raw
        files[f"history/{ROLLED_RECEIPT_NAME if rolled else 'final-receipt.json'}"] = receipt_raw
        files["qualification.json"] = qualification_raw
        result = RUNTIME.evaluate_kr_paper_runtime(
            source_packets=None, evaluation_at=evaluation_at, code_revision=code_revision,
            session_boundary_freshness=session_boundary(context, execution, code_revision),
            information_system_evidence={
                **bundle,
                "expected_source": expected_source(
                    record, bundle["reference_raw"], bundle["manifest_raw"]
                ),
                "historical_replay_raw": history_raw,
                "expected_historical_sha256": sha256(history_raw),
                "historical_acceptance_raw": receipt_raw,
                "expected_historical_acceptance_sha256": sha256(receipt_raw),
                "qualification_raw": qualification_raw,
                "expected_qualification_sha256": sha256(qualification_raw),
            },
        )
        files["decision.json"] = pretty(result)
        if not _decision_is_display(result):
            reason = (result.get("reasons") or ["RUNTIME_NOT_AVAILABLE"])[0]
    except (AdoptionError, PACKETS.CalendarPacketError) as exc:
        reason = str(exc)

    latest_updated = False
    if reason is None:
        latest_path = root / OUTPUT_PATH
        if latest_path.is_file():
            latest = _object(latest_path.read_bytes(), "LATEST_JSON_INVALID")
            prior = (latest.get("session_boundary_freshness") or {}).get("context_session_date")
            latest_updated = not isinstance(prior, str) or prior < current
        else:
            latest_updated = True
    files["publication.json"] = pretty({
        "schema_version": PUBLICATION_SCHEMA,
        "adoption_id": ADOPTION_ID,
        "context_session_date": current,
        "previous_session_date": previous,
        "execution_session_date": execution.isoformat() if execution else None,
        "evaluation_at": evaluation_at,
        "code_revision": code_revision,
        "status": "PUBLISHED_KR_PAPER_DISPLAY_ONLY" if reason is None
        else "EVIDENCE_RECORDED_RUNTIME_UNKNOWN",
        "reason": reason,
        "runtime_regime": result.get("runtime_regime") if result else "UNKNOWN",
        "decision_sha256": sha256(files["decision.json"]) if "decision.json" in files else None,
        "validation_sha256": sha256(validation_raw),
        "latest_pointer_updated": latest_updated,
    })
    for name, raw in files.items():
        path = dated / name
        if path.exists() and path.read_bytes() != raw:
            fail(f"NO_OVERWRITE_DIFFERENT_BYTES:{name}")
    for name, raw in files.items():
        path = dated / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    if latest_updated:
        target = root / OUTPUT_PATH
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(files["decision.json"])
        temporary.replace(target)
    return _object(files["publication.json"], "PUBLICATION_JSON_INVALID")


def check_latest(root: Path = ROOT) -> dict:
    """Offline check: the latest pointer equals a dated decision and its bindings."""
    raw = (root / OUTPUT_PATH).read_bytes()
    latest = _object(raw, "LATEST_JSON_INVALID")
    if not _decision_is_display(latest):
        fail("LATEST_NOT_DISPLAY_DECISION")
    unsigned = dict(latest)
    claimed = unsigned.pop("decision_id", None)
    if claimed != "kr-paper-regime:" + COMMON.payload_sha256(unsigned):
        fail("LATEST_DECISION_ID_MISMATCH")
    context = latest["session_boundary_freshness"]["context_session_date"]
    dated = root / EVIDENCE_BASE / context
    if (dated / "decision.json").read_bytes() != raw:
        fail("LATEST_NOT_EQUAL_DATED_DECISION")
    manifest_path = dated / COMMITTED_MANIFEST_NAME
    if not manifest_path.is_file():
        manifest_path = dated / MANIFEST_NAME
    if sha256((dated / REFERENCE_NAME).read_bytes()) != latest["source_sha256"] or sha256(
        manifest_path.read_bytes()
    ) != latest["source_manifest_sha256"]:
        fail("LATEST_SOURCE_BINDING_MISMATCH")
    qualification = dated / "qualification.json"
    if qualification.is_file() and sha256(qualification.read_bytes()) != latest["qualification_sha256"]:
        fail("LATEST_QUALIFICATION_BINDING_MISMATCH")
    return {"status": "LATEST_MATCHES_DATED_DECISION", "context_session_date": context,
            "execution_session_date": latest["session_boundary_freshness"]["execution_session_date"]}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pair = sub.add_parser("resolve-pair")
    pair.add_argument("--now", required=True)
    run = sub.add_parser("publish")
    run.add_argument("--bundle-dir", type=Path, required=True)
    run.add_argument("--provenance", type=Path, required=True)
    run.add_argument("--evaluation-at", required=True)
    run.add_argument("--code-revision", required=True)
    run.add_argument("--require-bot-chain", action="store_true")
    sub.add_parser("check-latest")
    args = parser.parse_args()
    if args.command == "resolve-pair":
        load_adoption()
        now = _instant(args.now, "NOW_INVALID")
        previous, current = last_completed_pair(now)
        dated = ROOT / EVIDENCE_BASE / current.isoformat()
        existing = (dated / "validation.json").exists() or (dated / "publication.json").exists()
        result = {
            "previous_date": previous.strftime("%Y%m%d"),
            "current_date": current.strftime("%Y%m%d"),
            "display_floor_reached": now >= display_floor(current),
            "dated_packet_exists": existing,
        }
    elif args.command == "publish":
        result = publish(
            bundle_dir=args.bundle_dir, provenance_raw=args.provenance.read_bytes(),
            evaluation_at=args.evaluation_at, code_revision=args.code_revision,
            require_bot_chain=args.require_bot_chain,
        )
    else:
        result = check_latest()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
