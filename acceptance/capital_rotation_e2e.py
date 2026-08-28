#!/usr/bin/env python3
"""P8-15 natural-chain evidence and acceptance evaluator.

This module records *how* the daily briefing workflow was invoked.  The
retrieval-authority envelope proves immutable delivery bytes, but deliberately
does not claim whether the workflow was a scheduled run or a manual recovery.
P8-15 must never infer that missing provenance from a filename or from a
successful packet.

The evaluator is evidence-only.  It grants no Regime, strategy, Stage, Buy,
Action, Order, Production, or trading authority.  The canonical Exit Gate is
three distinct KST dates with both natural AM/PM receipts, viewer-visible
Portal receipts for both slots, and one separately attested genuine scheduled
fail-closed run.  Manual and replay runs remain visible but never count.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Iterable

from acceptance import portal_observation_receipt as portal_observation


ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "evidence" / "capital_rotation_acceptance" / "run_receipts"
PORTAL_ROOT = ROOT / "evidence" / "capital_rotation_acceptance" / "portal_receipts"
FAIL_ROOT = ROOT / "evidence" / "capital_rotation_acceptance" / "fail_closed_receipts"
INVENTORY_PATH = ROOT / "evidence" / "operational" / "capital_rotation_e2e_acceptance.json"
CONTRACT_PATH = ROOT / "config" / "capital_rotation_e2e_acceptance_contract.json"

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RUN_FILE = re.compile(r"^run-(\d+)-attempt-(\d+)\.json$")
SLOTS = {"morning", "evening"}
SCHEDULES = {
    "morning": "5 22 * * 0-4",
    "evening": "30 9 * * 1-5",
}
AUTHORITY = {
    "evidence_inventory_only": True,
    "regime_authority": False,
    "strategy_authority": False,
    "stage_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
}


class AcceptanceError(RuntimeError):
    pass


def fail(code: str, detail: str = "") -> None:
    raise AcceptanceError(f"{code}{': ' + detail if detail else ''}")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: dict, hash_field: str) -> str:
    payload = dict(value)
    payload.pop(hash_field, None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _positive_int(value, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AcceptanceError(f"{field}_INVALID") from exc
    if parsed < 1:
        fail(f"{field}_INVALID")
    return parsed


def _date(value, field: str) -> str:
    if not isinstance(value, str) or DATE.fullmatch(value) is None:
        fail(f"{field}_INVALID")
    try:
        if dt.date.fromisoformat(value).isoformat() != value:
            fail(f"{field}_INVALID")
    except ValueError as exc:
        raise AcceptanceError(f"{field}_INVALID") from exc
    return value


def _safe_path(value, field: str) -> str:
    if not isinstance(value, str) or not value:
        fail(f"{field}_INVALID")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or ".." in parsed.parts:
        fail(f"{field}_INVALID")
    return parsed.as_posix()


def _load_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError(code) from exc
    if not isinstance(value, dict):
        fail(code)
    return value


def load_contract(repo_root: Path) -> dict:
    value = _load_json(
        repo_root / CONTRACT_PATH.relative_to(ROOT),
        "ACCEPTANCE_CONTRACT_UNREADABLE",
    )
    if set(value) != {
        "schema_version", "wbs_item", "exit_gate", "manual_and_replay_count_as_natural",
        "portal_receipt_required", "portal_receipt_producer_status",
        "fail_closed_receipt_producer_status",
        "github_attestation_trusted_root_sha256", "authority",
    }:
        fail("ACCEPTANCE_CONTRACT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != "capital_rotation_e2e_acceptance_contract/2"
        or value.get("wbs_item") != "P8-15"
        or value.get("manual_and_replay_count_as_natural") is not False
        or value.get("portal_receipt_required") is not True
        or value.get("portal_receipt_producer_status") != "IMPLEMENTED_GITHUB_ATTESTED_IMPORT"
        or value.get("fail_closed_receipt_producer_status") != "NOT_IMPLEMENTED_FAIL_CLOSED"
        or SHA256.fullmatch(str(value.get("github_attestation_trusted_root_sha256", ""))) is None
        or value.get("authority") != AUTHORITY
        or value.get("exit_gate") != {
            "required_distinct_natural_pair_dates": 3,
            "required_genuine_scheduled_fail_closed_samples": 1,
            "required_viewer_visible_projected_pair_dates": 3,
        }
    ):
        fail("ACCEPTANCE_CONTRACT_INVALID")
    return value


def _git_blob(repo_root: Path, commit: str, path: str) -> bytes:
    if FULL_SHA.fullmatch(str(commit)) is None:
        fail("SOURCE_COMMIT_INVALID")
    result = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"{commit}:{path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        fail("SOURCE_PINNED_VALIDATOR_UNAVAILABLE", path)
    return result.stdout


def _scheduled_module(repo_root: Path, source_commit: str):
    """Load the P0-06 validator from the immutable advertised commit.

    Revalidating an old receipt with today's mutable validator would make
    historical evidence fail merely because a later contract added fields.
    The validator and its direct helper therefore come from the same immutable
    source commit as the delivery bytes.
    """
    publisher_raw = _git_blob(
        repo_root, source_commit,
        ".github/scripts/publish_scheduled_briefing_authority.py",
    )
    helper_raw = _git_blob(
        repo_root, source_commit,
        ".github/scripts/fetch_briefing_read_model.py",
    )
    with tempfile.TemporaryDirectory() as name:
        scripts = Path(name) / ".github" / "scripts"
        scripts.mkdir(parents=True)
        publisher_path = scripts / "publish_scheduled_briefing_authority.py"
        helper_path = scripts / "fetch_briefing_read_model.py"
        publisher_path.write_bytes(publisher_raw)
        helper_path.write_bytes(helper_raw)
        module_name = f"atlas_p006_publisher_{source_commit}"
        prior_helper = sys.modules.pop("fetch_briefing_read_model", None)
        try:
            spec = importlib.util.spec_from_file_location(module_name, publisher_path)
            if spec is None or spec.loader is None:
                fail("P0_06_VALIDATOR_UNAVAILABLE")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop("fetch_briefing_read_model", None)
            if prior_helper is not None:
                sys.modules["fetch_briefing_read_model"] = prior_helper
            while str(scripts) in sys.path:
                sys.path.remove(str(scripts))
        return module


def _qualification(event_name: str, event_schedule: str | None, slot: str) -> str:
    if event_name == "workflow_dispatch":
        return "MANUAL_DIAGNOSTIC_EXCLUDED"
    if event_name != "schedule":
        return "NON_SCHEDULE_EVENT_EXCLUDED"
    if event_schedule != SCHEDULES[slot]:
        return "NATURAL_PROVENANCE_NOT_COMPUTABLE"
    return "NATURAL_SCHEDULED_RUN"


def build_run_receipt(
    repo_root: Path,
    *,
    event_name: str,
    event_schedule: str | None,
    run_id: int,
    run_attempt: int,
    workflow_head_sha: str,
    decision_date: str,
    slot: str,
    source_commit: str,
    authority_path: str,
) -> dict:
    load_contract(repo_root)
    if event_name not in {"schedule", "workflow_dispatch"}:
        fail("EVENT_NAME_UNSUPPORTED")
    if slot not in SLOTS:
        fail("SLOT_INVALID")
    decision_date = _date(decision_date, "DECISION_DATE")
    run_id = _positive_int(run_id, "RUN_ID")
    run_attempt = _positive_int(run_attempt, "RUN_ATTEMPT")
    if not isinstance(workflow_head_sha, str) or FULL_SHA.fullmatch(workflow_head_sha) is None:
        fail("WORKFLOW_HEAD_SHA_INVALID")
    if not isinstance(source_commit, str) or FULL_SHA.fullmatch(source_commit) is None:
        fail("SOURCE_COMMIT_INVALID")
    authority_path = _safe_path(authority_path, "AUTHORITY_PATH")
    path = repo_root / authority_path
    envelope = _load_json(path, "AUTHORITY_UNREADABLE")
    publisher = _scheduled_module(repo_root, source_commit)
    try:
        publisher.validate_envelope(repo_root, envelope)
        publisher.validate_expected_identity(
            repo_root, envelope, path, source_commit, slot, decision_date
        )
    except Exception as exc:
        fail("RETRIEVAL_AUTHORITY_INVALID", type(exc).__name__)
    if envelope.get("stale_detection") != "PASS":
        fail("RETRIEVAL_AUTHORITY_STALE")
    locator = envelope.get("delivery_locator")
    if not isinstance(locator, dict):
        fail("DELIVERY_LOCATOR_INVALID")
    receipt = {
        "schema_version": "capital_rotation_run_receipt/1",
        "workflow": "Atlas Daily Briefing Integration v1",
        "github": {
            "event_name": event_name,
            "event_schedule": event_schedule or None,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "workflow_head_sha": workflow_head_sha,
        },
        "sample_qualification": _qualification(event_name, event_schedule, slot),
        "decision_date": decision_date,
        "slot": slot,
        "source_commit": source_commit,
        "generation_id": envelope["generation_id"],
        "retrieval_authority": {
            "path": authority_path,
            "sha256": file_sha256(path),
            "stale_detection": "PASS",
        },
        "delivery": {
            "locator_path": "data/briefing/daily_briefing_sources.json",
            "packet_path": locator["packet_path"],
            "packet_sha256": locator["packet_sha256"],
            "briefing_path": locator["briefing_path"],
            "briefing_sha256": locator["briefing_sha256"],
        },
        "completion_state": "RETRIEVAL_AND_DELIVERY_VALIDATED",
        "authority": dict(AUTHORITY),
    }
    receipt["receipt_sha256"] = payload_sha256(receipt, "receipt_sha256")
    return receipt


def _run_path(root: Path, receipt: dict) -> Path:
    github = receipt["github"]
    return (
        root
        / receipt["decision_date"]
        / receipt["slot"]
        / f"run-{github['run_id']}-attempt-{github['run_attempt']}.json"
    )


def _write_append_only(target: Path, value: dict) -> bool:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") == rendered:
            return False
        fail("APPEND_ONLY_CONFLICT", target.as_posix())
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        temp.write_text(rendered, encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return True


def validate_run_receipt(
    repo_root: Path,
    value: dict,
    path: Path | None = None,
    expected_root: Path | None = None,
) -> dict:
    fields = {
        "schema_version", "workflow", "github", "sample_qualification",
        "decision_date", "slot", "source_commit", "generation_id",
        "retrieval_authority", "delivery", "completion_state", "authority",
        "receipt_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        fail("RUN_RECEIPT_FIELDS_MISMATCH")
    github = value.get("github")
    if not isinstance(github, dict) or set(github) != {
        "event_name", "event_schedule", "run_id", "run_attempt", "workflow_head_sha"
    }:
        fail("RUN_RECEIPT_GITHUB_INVALID")
    if value.get("schema_version") != "capital_rotation_run_receipt/1" or value.get("workflow") != "Atlas Daily Briefing Integration v1":
        fail("RUN_RECEIPT_IDENTITY_INVALID")
    if value.get("slot") not in SLOTS:
        fail("RUN_RECEIPT_SLOT_INVALID")
    _date(value.get("decision_date"), "RUN_RECEIPT_DATE")
    _positive_int(github.get("run_id"), "RUN_RECEIPT_RUN_ID")
    _positive_int(github.get("run_attempt"), "RUN_RECEIPT_RUN_ATTEMPT")
    if FULL_SHA.fullmatch(str(github.get("workflow_head_sha", ""))) is None or FULL_SHA.fullmatch(str(value.get("source_commit", ""))) is None:
        fail("RUN_RECEIPT_COMMIT_INVALID")
    if SHA256.fullmatch(str(value.get("generation_id", ""))) is None:
        fail("RUN_RECEIPT_GENERATION_INVALID")
    expected_qualification = _qualification(
        github.get("event_name"), github.get("event_schedule"), value["slot"]
    )
    if value.get("sample_qualification") != expected_qualification:
        fail("RUN_RECEIPT_QUALIFICATION_TAMPERED")
    if value.get("completion_state") != "RETRIEVAL_AND_DELIVERY_VALIDATED" or value.get("authority") != AUTHORITY:
        fail("RUN_RECEIPT_AUTHORITY_INVALID")
    retrieval = value.get("retrieval_authority")
    delivery = value.get("delivery")
    if not isinstance(retrieval, dict) or set(retrieval) != {"path", "sha256", "stale_detection"}:
        fail("RUN_RECEIPT_RETRIEVAL_INVALID")
    if not isinstance(delivery, dict) or set(delivery) != {"locator_path", "packet_path", "packet_sha256", "briefing_path", "briefing_sha256"}:
        fail("RUN_RECEIPT_DELIVERY_INVALID")
    authority_path = _safe_path(retrieval.get("path"), "RUN_RECEIPT_AUTHORITY_PATH")
    if retrieval.get("stale_detection") != "PASS" or SHA256.fullmatch(str(retrieval.get("sha256", ""))) is None:
        fail("RUN_RECEIPT_RETRIEVAL_INVALID")
    for key in ("packet_sha256", "briefing_sha256"):
        if SHA256.fullmatch(str(delivery.get(key, ""))) is None:
            fail("RUN_RECEIPT_DELIVERY_INVALID")
    for key in ("locator_path", "packet_path", "briefing_path"):
        _safe_path(delivery.get(key), f"RUN_RECEIPT_{key.upper()}")
    authority_file = repo_root / authority_path
    if not authority_file.is_file() or file_sha256(authority_file) != retrieval["sha256"]:
        fail("RUN_RECEIPT_AUTHORITY_BYTES_MISMATCH")
    envelope = _load_json(authority_file, "RUN_RECEIPT_AUTHORITY_UNREADABLE")
    publisher = _scheduled_module(repo_root, value["source_commit"])
    try:
        publisher.validate_envelope(repo_root, envelope)
    except Exception as exc:
        fail("RUN_RECEIPT_AUTHORITY_REVALIDATION_FAILED", type(exc).__name__)
    locator = envelope.get("delivery_locator", {})
    if (
        envelope.get("source_commit") != value["source_commit"]
        or envelope.get("generation_id") != value["generation_id"]
        or envelope.get("slot") != value["slot"]
        or envelope.get("expected_kst_date") != value["decision_date"]
        or delivery.get("packet_path") != locator.get("packet_path")
        or delivery.get("packet_sha256") != locator.get("packet_sha256")
        or delivery.get("briefing_path") != locator.get("briefing_path")
        or delivery.get("briefing_sha256") != locator.get("briefing_sha256")
    ):
        fail("RUN_RECEIPT_LINEAGE_MISMATCH")
    if value.get("receipt_sha256") != payload_sha256(value, "receipt_sha256"):
        fail("RUN_RECEIPT_HASH_MISMATCH")
    if path is not None:
        expected = _run_path(
            expected_root or (repo_root / RUN_ROOT.relative_to(ROOT)), value
        )
        if path.resolve() != expected.resolve() or RUN_FILE.fullmatch(path.name) is None:
            fail("RUN_RECEIPT_PATH_MISMATCH")
    return value


def _iter_json(root: Path) -> Iterable[Path]:
    return sorted(root.glob("**/*.json")) if root.exists() else []


def _display_path(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def _portal_slot_lineage(run_receipt: dict) -> dict:
    """Return the exact natural-run identity embedded by the Portal observer."""
    github = run_receipt["github"]
    delivery = run_receipt["delivery"]
    return {
        "slot": run_receipt["slot"],
        "run_id": github["run_id"],
        "run_attempt": github["run_attempt"],
        "workflow_head_sha": github["workflow_head_sha"],
        "source_commit": run_receipt["source_commit"],
        "generation_id": run_receipt["generation_id"],
        "packet_sha256": delivery["packet_sha256"],
        "briefing_sha256": delivery["briefing_sha256"],
    }


def _portal_pair_lineage(portal_receipt: dict) -> dict:
    pair = portal_receipt["natural_pair"]
    return {
        "decision_date": pair["decision_date"],
        "atlas_discovery_commit": pair["atlas_discovery_commit"],
        "slots": {
            slot_row["slot"]: slot_row
            for slot_row in pair["slots"]
        },
    }


def build_inventory(
    repo_root: Path = ROOT,
    *,
    run_root: Path | None = None,
    portal_root: Path | None = None,
    fail_root: Path | None = None,
    portal_attestation_verifier=None,
    portal_trusted_root_sha256: str | None = None,
) -> dict:
    contract = load_contract(repo_root)
    run_root = run_root or (repo_root / RUN_ROOT.relative_to(ROOT))
    portal_root = portal_root or (repo_root / PORTAL_ROOT.relative_to(ROOT))
    fail_root = fail_root or (repo_root / FAIL_ROOT.relative_to(ROOT))
    run_receipts = []
    invalid = []
    for path in _iter_json(run_root):
        try:
            value = validate_run_receipt(
                repo_root,
                _load_json(path, "RUN_RECEIPT_UNREADABLE"),
                path,
                run_root,
            )
            run_receipts.append((path, value))
        except AcceptanceError as exc:
            invalid.append({"path": _display_path(path, repo_root), "error": str(exc).split(":", 1)[0]})
    if invalid:
        fail("INVALID_RUN_RECEIPT_PRESENT", invalid[0]["path"])

    natural = [(path, row) for path, row in run_receipts if row["sample_qualification"] == "NATURAL_SCHEDULED_RUN"]
    manual_count = sum(row["sample_qualification"] != "NATURAL_SCHEDULED_RUN" for _, row in run_receipts)
    candidates_by_date: dict[str, dict[str, list[tuple[Path, dict]]]] = {}
    for path, row in natural:
        candidates_by_date.setdefault(row["decision_date"], {}).setdefault(row["slot"], []).append((path, row))
    by_date: dict[str, dict[str, tuple[Path, dict]]] = {}
    superseded_attempt_count = 0
    for date, slot_candidates in candidates_by_date.items():
        selected_slots = {}
        for slot, rows in slot_candidates.items():
            run_ids = {row["github"]["run_id"] for _, row in rows}
            if len(run_ids) != 1:
                fail("DUPLICATE_NATURAL_SLOT_DISTINCT_RUNS", f"{date}/{slot}")
            rows = sorted(rows, key=lambda item: item[1]["github"]["run_attempt"])
            attempts = [row["github"]["run_attempt"] for _, row in rows]
            if len(set(attempts)) != len(attempts):
                fail("DUPLICATE_NATURAL_SLOT_ATTEMPT", f"{date}/{slot}")
            selected_slots[slot] = rows[-1]
            superseded_attempt_count += len(rows) - 1
        by_date[date] = selected_slots
    pair_dates = sorted(date for date, slots in by_date.items() if set(slots) == SLOTS)

    try:
        portal_receipts = portal_observation.iter_imported_receipts(
            portal_root,
            verifier=portal_attestation_verifier,
            expected_trusted_root_sha256=(
                portal_trusted_root_sha256
                or contract["github_attestation_trusted_root_sha256"]
            ),
        )
    except portal_observation.PortalReceiptError as exc:
        code = str(exc).split(":", 1)[0]
        if code == "UNTRUSTED_PORTAL_RECEIPT_PRESENT":
            fail(code)
        fail("INVALID_TRUSTED_PORTAL_RECEIPT_PRESENT", code)
    if list(_iter_json(fail_root)):
        fail("UNTRUSTED_FAIL_CLOSED_RECEIPT_PRESENT")
    portal_natural = [
        row for row in portal_receipts
        if row["sample_qualification"] == "NATURAL_SCHEDULED_PORTAL_OBSERVATION"
    ]
    portal_by_date: dict[str, list[dict]] = {}
    for row in portal_natural:
        portal_by_date.setdefault(row["natural_pair"]["decision_date"], []).append(row)
    for date, rows in portal_by_date.items():
        pair_lineages = {canonical_json(_portal_pair_lineage(row)) for row in rows}
        if len(pair_lineages) != 1:
            fail("PORTAL_RECEIPT_LINEAGE_CONFLICT", date)
        if date not in by_date or set(by_date[date]) != SLOTS:
            continue
        expected_slots = {
            slot: _portal_slot_lineage(run_receipt)
            for slot, (_, run_receipt) in by_date[date].items()
        }
        actual_slots = _portal_pair_lineage(rows[0])["slots"]
        if actual_slots != expected_slots:
            fail("PORTAL_RECEIPT_SOURCE_LINEAGE_MISMATCH", date)
    projected_pair_dates = sorted(
        date
        for date in portal_by_date
        if date in by_date and set(by_date[date]) == SLOTS
    )
    fail_closed_count = 0
    exit_gate = contract["exit_gate"]
    blockers = []
    if len(pair_dates) < exit_gate["required_distinct_natural_pair_dates"]:
        blockers.append("THREE_DISTINCT_NATURAL_AM_PM_PAIRS_NOT_MET")
    if len(projected_pair_dates) < exit_gate["required_viewer_visible_projected_pair_dates"]:
        blockers.append("THREE_VIEWER_VISIBLE_PROJECTED_PAIRS_NOT_MET")
    if fail_closed_count < exit_gate["required_genuine_scheduled_fail_closed_samples"]:
        blockers.append("GENUINE_SCHEDULED_FAIL_CLOSED_RECEIPT_MISSING")
        blockers.append("TRUSTED_FAIL_CLOSED_RECEIPT_PRODUCER_NOT_IMPLEMENTED")
    inventory = {
        "schema_version": "capital_rotation_e2e_acceptance/2",
        "wbs_item": "P8-15",
        "status": "PASS" if not blockers else "NOT_READY",
        "exit_gate": exit_gate,
        "observed": {
            "run_receipt_count": len(run_receipts),
            "natural_run_receipt_count": len(natural),
            "superseded_natural_rerun_attempt_count": superseded_attempt_count,
            "manual_or_non_schedule_receipt_count": manual_count,
            "natural_pair_dates": pair_dates,
            "portal_receipt_count": len(portal_receipts),
            "manual_or_non_schedule_portal_receipt_count": len(portal_receipts) - len(portal_natural),
            "viewer_visible_projected_pair_dates": projected_pair_dates,
            "genuine_scheduled_fail_closed_sample_count": fail_closed_count,
        },
        "blockers": blockers,
        "provenance_rules": {
            "manual_or_replay_counts_as_natural": False,
            "filename_or_packet_success_infers_natural": False,
            "portal_receipt_required_for_projection": True,
            "portal_receipt_producer_status": contract["portal_receipt_producer_status"],
            "github_attestation_trusted_root_sha256": contract["github_attestation_trusted_root_sha256"],
            "fail_closed_receipt_producer_status": contract["fail_closed_receipt_producer_status"],
            "successful_packet_infers_fail_closed_sample": False,
        },
        "authority": dict(AUTHORITY),
    }
    inventory["inventory_sha256"] = payload_sha256(inventory, "inventory_sha256")
    return inventory


def validate_inventory(repo_root: Path, value: dict) -> dict:
    expected = build_inventory(repo_root)
    if value != expected:
        fail("ACCEPTANCE_INVENTORY_DRIFT_OR_TAMPER")
    return value


def write_inventory(repo_root: Path, target: Path) -> bool:
    value = build_inventory(repo_root)
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") == rendered:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        temp.write_text(rendered, encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    publish_parser = sub.add_parser("publish-run-receipt")
    publish_parser.add_argument("--event-name", required=True)
    publish_parser.add_argument("--event-schedule", default="")
    publish_parser.add_argument("--run-id", required=True)
    publish_parser.add_argument("--run-attempt", required=True)
    publish_parser.add_argument("--workflow-head-sha", required=True)
    publish_parser.add_argument("--decision-date", required=True)
    publish_parser.add_argument("--slot", required=True, choices=sorted(SLOTS))
    publish_parser.add_argument("--source-commit", required=True)
    publish_parser.add_argument("--authority-path", required=True)
    sub.add_parser("evaluate")
    validate_parser = sub.add_parser("validate-inventory")
    validate_parser.add_argument("path", type=Path)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    if args.command == "publish-run-receipt":
        value = build_run_receipt(
            repo_root,
            event_name=args.event_name,
            event_schedule=args.event_schedule or None,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            workflow_head_sha=args.workflow_head_sha,
            decision_date=args.decision_date,
            slot=args.slot,
            source_commit=args.source_commit,
            authority_path=args.authority_path,
        )
        target = _run_path(repo_root / RUN_ROOT.relative_to(ROOT), value)
        changed = _write_append_only(target, value)
        print(f"run_receipt_path={target.relative_to(repo_root).as_posix()}")
        print(f"run_receipt_changed={'true' if changed else 'false'}")
        return 0
    if args.command == "evaluate":
        target = repo_root / INVENTORY_PATH.relative_to(ROOT)
        changed = write_inventory(repo_root, target)
        print(f"acceptance_path={target.relative_to(repo_root).as_posix()}")
        print(f"acceptance_changed={'true' if changed else 'false'}")
        return 0
    path = args.path if args.path.is_absolute() else repo_root / args.path
    validate_inventory(repo_root, _load_json(path, "ACCEPTANCE_INVENTORY_UNREADABLE"))
    print("P8_15_ACCEPTANCE_VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
