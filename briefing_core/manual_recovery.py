#!/usr/bin/env python3
"""Append one explicitly non-natural, source-bound briefing recovery revision."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

from briefing.daily_orchestrator import (
    FLOW_REPLAY_UNPROVABLE_ERRORS,
    DailyOrchestratorError,
    validate_packet,
)
from briefing_core import major_events


INDEX_SCHEMA = 1
RECOVERY_SCHEMA = "briefing_manual_recovery/1"
SLOT = {"morning": "AM", "evening": "PM"}
AUTHORITY = {
    "stage": False,
    "buy": False,
    "action": False,
    "order": False,
    "production": False,
    "trading": False,
}


class ManualRecoveryError(RuntimeError):
    pass


# Everything this command may fail with that is a decision rather than a bug.
# A packet that cannot be validated -- for want of trusted Flow history
# context, or because its frozen inputs cannot be proven -- is a STOP, and the
# exact code survives that boundary instead of becoming a traceback.
STOP_ERRORS = (
    ManualRecoveryError,
    major_events.MajorEventError,
    DailyOrchestratorError,
) + FLOW_REPLAY_UNPROVABLE_ERRORS


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManualRecoveryError(f"RECOVERY_JSON_UNREADABLE:{path}") from exc
    if not isinstance(value, dict):
        raise ManualRecoveryError(f"RECOVERY_JSON_NOT_OBJECT:{path}")
    return value


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _label_recovery(briefing: bytes) -> bytes:
    text = briefing.decode("utf-8").lstrip("\ufeff")
    label = "복구 유형: MANUAL_RECOVERY · 자연 실행 표본 아님"
    if label in text:
        return text.rstrip().encode("utf-8") + b"\n"
    lines = text.splitlines(keepends=True)
    if lines and lines[0].startswith("# "):
        text = lines[0].rstrip("\r\n") + "\n\n" + label + "\n\n" + "".join(lines[1:]).lstrip("\r\n")
    else:
        text = label + "\n\n" + text
    return text.rstrip().encode("utf-8") + b"\n"


def publish(
    repo_root: Path,
    *,
    slot: str,
    decision_date: str,
    generated_at: str,
    registry_path: str,
    historical_source_commit: str | None = None,
    trusted_repository_root: Path | None = None,
    trusted_validation_head: str | None = None,
) -> dict:
    """Append one labelled recovery revision over an already-published one.

    The three history arguments are EXTERNAL trusted operator context, passed
    straight through to ``validate_packet``.  This module never derives them:
    it does not read a source commit out of the packet it is recovering, out
    of a locator, or out of the live HEAD.

    A recovery target that is a legacy packet (no ``flow_replay_version``
    marker) and is offered no context fails with the approved unreplayable
    diagnostic.  That failure is preserved deliberately -- recovering a
    briefing must not be a way to accept a packet whose Flow inputs cannot be
    proven.
    """
    repo_root = repo_root.resolve()
    if trusted_repository_root is None:
        trusted_repository_root = repo_root
    if slot not in SLOT:
        raise ManualRecoveryError("RECOVERY_SLOT_INVALID")
    date_root = repo_root / "evidence/daily_briefing" / slot / decision_date
    index_path = date_root / "index.json"
    index = _read_json(index_path)
    revisions = index.get("revisions")
    latest = index.get("latest_revision")
    if (
        index.get("schema_version") != INDEX_SCHEMA
        or not isinstance(revisions, list)
        or not revisions
        or latest != len(revisions)
    ):
        raise ManualRecoveryError("RECOVERY_DAILY_INDEX_INVALID")
    entry = revisions[-1]
    if entry.get("revision") != latest or entry.get("path") != f"rev-{latest:03d}":
        raise ManualRecoveryError("RECOVERY_DAILY_INDEX_LATEST_MISMATCH")

    base_root = date_root / entry["path"]
    packet_body = (base_root / "packet.json").read_bytes()
    briefing_body = (base_root / "briefing.md").read_bytes()
    packet = json.loads(packet_body)
    validate_packet(
        packet,
        trusted_repository_root=Path(trusted_repository_root),
        trusted_validation_head=trusted_validation_head,
        historical_source_commit=historical_source_commit,
    )
    if packet.get("packet_sha256") != entry.get("packet_sha256"):
        raise ManualRecoveryError("RECOVERY_PACKET_INDEX_SHA_MISMATCH")
    if packet.get("slot") != slot or packet.get("decision_date") != decision_date:
        raise ManualRecoveryError("RECOVERY_PACKET_IDENTITY_MISMATCH")

    relative_registry = Path(registry_path)
    if relative_registry.is_absolute() or ".." in relative_registry.parts:
        raise ManualRecoveryError("RECOVERY_REGISTRY_PATH_INVALID")
    registry_body = (repo_root / relative_registry).read_bytes()
    registry = json.loads(registry_body)
    major_events.validate_registry(
        registry, briefing_date=decision_date, slot=SLOT[slot]
    )
    coverage = major_events.correct_handoff(
        {"correction_history": []}, registry
    )["major_event_coverage"]
    recovered = _label_recovery(
        major_events.render_corrected_briefing(briefing_body, coverage)
    )
    if recovered == briefing_body:
        return {
            "result": "NO_CHANGE",
            "revision": latest,
            "path": base_root.relative_to(repo_root).as_posix(),
            "duplicate_count": 0,
        }

    revision = latest + 1
    revision_name = f"rev-{revision:03d}"
    target = date_root / revision_name
    if target.exists():
        raise ManualRecoveryError("RECOVERY_APPEND_ONLY_CONFLICT")
    temporary = date_root / f".{revision_name}.tmp.{os.getpid()}"
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir()
    try:
        (temporary / "packet.json").write_bytes(packet_body)
        (temporary / "briefing.md").write_bytes(recovered)
        manifest = {
            "schema_version": RECOVERY_SCHEMA,
            "recovery_type": "MANUAL_RECOVERY",
            "sample_qualification": "MANUAL_RECOVERY_NOT_NATURAL_SAMPLE",
            "decision_date": decision_date,
            "slot": slot,
            "generated_at": generated_at,
            "base_revision": latest,
            "recovery_revision": revision,
            "packet_sha256": packet["packet_sha256"],
            "base_briefing_sha256": _sha(briefing_body),
            "recovered_briefing_sha256": _sha(recovered),
            "major_event_registry_path": relative_registry.as_posix(),
            "major_event_registry_sha256": _sha(registry_body),
            "overwrite_performed": False,
            "duplicate_count": 0,
            "authority": AUTHORITY,
        }
        (temporary / "manual-recovery.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)

    updated = dict(index)
    updated["latest_revision"] = revision
    updated["revisions"] = revisions + [{
        "revision": revision,
        "path": revision_name,
        "packet_sha256": packet["packet_sha256"],
        "generated_at": generated_at,
        "component_status_counts": packet["component_status_counts"],
    }]
    _atomic_json(index_path, updated)
    return {
        "result": "APPLIED",
        "revision": revision,
        "path": target.relative_to(repo_root).as_posix(),
        "duplicate_count": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--slot", required=True, choices=tuple(SLOT))
    parser.add_argument("--decision-date", required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--registry-path", required=True)
    # Optional external history context. A current (Flow version 1) packet
    # carries its own frozen envelope and needs none of these; they exist so a
    # trusted operator can recover over a legacy packet. Never defaulted from
    # the packet or from live HEAD.
    parser.add_argument(
        "--historical-source-commit",
        help="externally trusted ORIGINAL Flow source commit for a legacy packet",
    )
    parser.add_argument("--trusted-repository-root", type=Path)
    parser.add_argument("--trusted-validation-head")
    args = parser.parse_args()
    result = publish(
        args.repo_root,
        slot=args.slot,
        decision_date=args.decision_date,
        generated_at=args.generated_at,
        registry_path=args.registry_path,
        historical_source_commit=args.historical_source_commit,
        trusted_repository_root=args.trusted_repository_root,
        trusted_validation_head=args.trusted_validation_head,
    )
    for key in ("result", "revision", "path", "duplicate_count"):
        print(f"{key}={result[key]}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except STOP_ERRORS as exc:
        # Printed verbatim, so the exact code -- UNREPLAYABLE_FLOW_HISTORY_
        # SOURCE_COMMIT_REQUIRED, a FLOW_REPLAY_* provenance code,
        # OUTPUT_MISMATCH -- survives this boundary intact.
        print(f"STOP:{exc}", file=os.sys.stderr)
        raise SystemExit(2) from None
