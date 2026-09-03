#!/usr/bin/env python3
"""Atlas AM/PM Briefing Handoff Watchdog.

Detects when the validated-narrative handoff has stalled after a natural
AM/PM briefing slot -- i.e. the briefing ran, but the chain that carries it
to Portal/Notion did not finish. Read-only: CHECK -> CLASSIFY -> ALERT.

This module authors nothing and is not a second briefing pipeline. It reads
only evidence already committed by the existing, unmodified chain:

    evidence/daily_briefing/{slot}/{date}/                 natural receipt
    data/briefing/finalization/{date}/{slot}/               semantic verdict,
                                                              portal-final
                                                              receipt, delivery
                                                              receipt
                                                              (briefing_finalization.py,
                                                              contract briefing_finalization/18)
    evidence/briefing_events/{date}/{slot}/                 source bridge
                                                              (briefing_core/chain.py)
    evidence/validated_briefing_portal/{slot}/{date}/       validated envelope
                                                              (validated_briefing_portal_producer.py)

It never:
  - decides FACT / INFERENCE / UNKNOWN for any claim,
  - authors a source bridge,
  - invokes briefing_core/manual_recovery.py,
  - builds or commits a Portal envelope,
  - dispatches to atlas-portal,
  - writes Notion,
  - creates or edits any NATURAL evidence,
  - changes any Stage / Buy / Action / Order / Production / Trading authority
    (all of which remain false everywhere in this module).

The one status artifact it writes lives under its own namespace,
`data/briefing/handoff_watchdog/{date}/{slot}/`, append-only and separate
from the finalization ledger it observes.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SUPPORTED_SLOTS = ("morning", "evening")
KST = ZoneInfo("Asia/Seoul")

# The exact natural-cron fire times this watchdog measures grace against,
# copied verbatim from .github/workflows/daily-briefing.yml `on.schedule`.
# This is not a second scheduling authority -- it is the one fact needed to
# compute elapsed time since the natural slot was due. If that cron ever
# changes, update this table in the same change.
NATURAL_CRON_KST = {
    "morning": (7, 5),    # cron "5 22 * * *"   -> 07:05 KST daily
    "evening": (18, 30),  # cron "30 9 * * 1-5" -> 18:30 KST Mon-Fri
}
# "30 9 * * 1-5" -- Mon-Fri only. Not invented here; mirrored from the same
# workflow so the watchdog does not expect an evening slot on weekends that
# the natural producer itself never schedules.
EVENING_WEEKDAYS_ONLY = True

WATCHDOG_ROOT = "data/briefing/handoff_watchdog"
WATCHDOG_SCHEMA = "briefing_handoff_watchdog/1"

STATUSES = (
    "COMPLETE",
    "WAITING_VALIDATION",
    "SOURCE_BRIDGE_MISSING",
    "ENVELOPE_MISSING",
    "PORTAL_HANDOFF_MISSING",
    "FINAL_DRAIN_MISSING",
    "NATURAL_RECEIPT_MISSING",
)

NO_AUTHORITY = {
    "fact_inference_unknown_decision": False,
    "source_bridge_authored": False,
    "manual_recovery_invoked": False,
    "envelope_created": False,
    "dispatch_invoked": False,
    "notion_written": False,
    "natural_evidence_changed": False,
}


class WatchdogError(RuntimeError):
    pass


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_bytes(body)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _latest_rev(directory: Path, prefix: str) -> Path | None:
    if not directory.exists():
        return None
    found = sorted(directory.glob(f"{prefix}-rev-*.json"))
    return found[-1] if found else None


def _next_rev(directory: Path, prefix: str) -> int:
    if not directory.exists():
        return 1
    revs = []
    for p in directory.glob(f"{prefix}-rev-*.json"):
        tail = p.stem.rsplit("-", 1)[-1]
        if tail.isdigit():
            revs.append(int(tail))
    return max(revs) + 1 if revs else 1


def _validate_slot(slot: str) -> str:
    if slot not in SUPPORTED_SLOTS:
        raise WatchdogError(f"WATCHDOG_SLOT_INVALID:{slot}")
    return slot


def _validate_date(date: str) -> str:
    try:
        _dt.date.fromisoformat(date)
    except ValueError as exc:
        raise WatchdogError(f"WATCHDOG_DATE_INVALID:{date}") from exc
    return date


# --------------------------------------------------------------- evidence reads
# Every check below is a structural existence/field read. None of them
# interprets briefing content, re-derives a verdict, or second-guesses the
# producer/validator that wrote the file -- that would be exactly the FACT
# judgment this watchdog must not perform.

def check_natural_receipt(repo_root: Path, slot: str, date: str) -> dict:
    path = repo_root / "evidence/daily_briefing" / slot / date / "index.json"
    return {"exists": path.exists(), "path": str(path.relative_to(repo_root))}


def check_source_bridge(repo_root: Path, slot: str, date: str) -> dict:
    root = repo_root / "evidence/briefing_events" / date / slot
    index_path = root / "index.json"
    index = _read_json(index_path)
    discoverable = index is not None
    any_registry = root.exists() and any(root.glob("rev-*/registry.json"))
    return {
        # True if a source bridge is present in ANY committed form.
        "exists": bool(discoverable or any_registry),
        # True only if briefing_core/chain.py's automatic index-based
        # discovery would actually find it on the next natural build. A
        # registry committed without its index.json (e.g. an ad hoc
        # recovery commit) is real evidence but is NOT auto-discoverable.
        "discoverable_by_chain_build": discoverable,
        "path": str(index_path.relative_to(repo_root)),
    }


def check_semantic_verdict(repo_root: Path, slot: str, date: str) -> dict:
    directory = repo_root / "data/briefing/finalization" / date / slot
    path = _latest_rev(directory, "validation")
    if path is None:
        return {"exists": False}
    body = _read_json(path)
    if body is None:
        return {"exists": True, "readable": False, "path": str(path.relative_to(repo_root))}
    routing = body.get("routing") or {}
    return {
        "exists": True,
        "readable": True,
        "path": str(path.relative_to(repo_root)),
        "validation_status": body.get("validation_status"),
        "status_deliverable": routing.get("status_deliverable"),
        "hold_reasons": body.get("hold_reasons") or [],
    }


def check_envelope(repo_root: Path, slot: str, date: str) -> dict:
    root = repo_root / "evidence/validated_briefing_portal" / slot / date
    index_path = root / "index.json"
    index = _read_json(index_path)
    if index is None:
        return {"exists": False, "path": str(index_path.relative_to(repo_root))}
    result = {
        "exists": True,
        "path": str(index_path.relative_to(repo_root)),
        "latest_revision": index.get("latest_revision"),
        "projection_id": index.get("latest_projection_id"),
        "recovery_type": None,
    }
    revisions = index.get("revisions")
    if isinstance(revisions, list) and revisions:
        envelope_path = revisions[-1].get("envelope_path")
        if isinstance(envelope_path, str):
            envelope = _read_json(repo_root / envelope_path)
            if envelope:
                for item in envelope.get("display_proposal") or []:
                    content = (item or {}).get("content") or {}
                    if content.get("recovery_type"):
                        result["recovery_type"] = content["recovery_type"]
                        break
    return result


def check_portal_final_receipt(repo_root: Path, slot: str, date: str) -> dict:
    directory = repo_root / "data/briefing/finalization" / date / slot
    path = _latest_rev(directory, "portal-final-receipt")
    return {"exists": path is not None,
            "path": str(path.relative_to(repo_root)) if path else None}


def check_final_drain(repo_root: Path, slot: str, date: str) -> dict:
    path = repo_root / "data/briefing/finalization" / date / slot / "delivery_receipt.json"
    return {"exists": path.exists(), "path": str(path.relative_to(repo_root))}


def load_semantic_timeout_minutes(repo_root: Path) -> int:
    """The one documented SLA number the watchdog measures grace against,
    read live from the same config the finalization gate itself reads
    (config/atlas_semantic_validator.json, consumed by
    briefing_finalization.load_semantic_validator_policy). Never a
    separately invented deadline; falls back to that module's own default
    (20) only if the config is missing or malformed the same way it does.
    """
    data = _read_json(repo_root / "config/atlas_semantic_validator.json")
    if not data:
        return 20
    try:
        minutes = int(data.get("timeout_minutes", 20))
    except (TypeError, ValueError):
        return 20
    return minutes if 1 <= minutes <= 1440 else 20


# ------------------------------------------------------------------- timing

def slot_start_kst(date: str, slot: str) -> _dt.datetime:
    year, month, day = (int(x) for x in date.split("-"))
    hour, minute = NATURAL_CRON_KST[slot]
    return _dt.datetime(year, month, day, hour, minute, tzinfo=KST)


def is_slot_expected(date: str, slot: str) -> bool:
    """Whether the natural cron itself is scheduled to fire for this
    date/slot at all -- mirrors daily-briefing.yml's own cron restriction
    (evening is Mon-Fri only) rather than inventing a new rule."""
    if slot == "evening" and EVENING_WEEKDAYS_ONLY:
        year, month, day = (int(x) for x in date.split("-"))
        return _dt.date(year, month, day).weekday() <= 4  # Mon=0 .. Fri=4
    return True


# --------------------------------------------------------------- classify

def classify(natural: dict, semantic: dict, bridge: dict, envelope: dict,
             portal_receipt: dict, drain: dict) -> str:
    if not natural["exists"]:
        return "NATURAL_RECEIPT_MISSING"
    if envelope["exists"]:
        # A validated envelope is concrete forward progress regardless of
        # whether it arrived through the normal semantic-verdict path or
        # through briefing_core/manual_recovery.py -- do not re-demand a
        # semantic verdict record once the envelope itself exists.
        if not portal_receipt["exists"]:
            return "PORTAL_HANDOFF_MISSING"
        if not drain["exists"]:
            return "FINAL_DRAIN_MISSING"
        return "COMPLETE"
    if not semantic["exists"]:
        return "WAITING_VALIDATION"
    if semantic.get("status_deliverable") is not True:
        # Held or otherwise not clear to proceed. If the source bridge is
        # also absent, that is the single most actionable diagnosis; if the
        # bridge is present, something else is holding the verdict and this
        # watchdog does not attempt to name what (that would be re-deriving
        # a semantic judgment).
        if not bridge["exists"]:
            return "SOURCE_BRIDGE_MISSING"
        return "WAITING_VALIDATION"
    return "ENVELOPE_MISSING"


# ----------------------------------------------------------------- run/report

def run_check(repo_root: Path, slot: str, date: str, *,
              now: _dt.datetime | None = None) -> dict:
    slot = _validate_slot(slot)
    date = _validate_date(date)
    now = now.astimezone(KST) if now else _dt.datetime.now(tz=KST)

    natural = check_natural_receipt(repo_root, slot, date)
    semantic = check_semantic_verdict(repo_root, slot, date)
    bridge = check_source_bridge(repo_root, slot, date)
    envelope = check_envelope(repo_root, slot, date)
    portal_receipt = check_portal_final_receipt(repo_root, slot, date)
    drain = check_final_drain(repo_root, slot, date)

    status = classify(natural, semantic, bridge, envelope, portal_receipt, drain)
    expected = is_slot_expected(date, slot)
    timeout_minutes = load_semantic_timeout_minutes(repo_root)
    deadline = slot_start_kst(date, slot) + _dt.timedelta(minutes=timeout_minutes)
    past_grace = now >= deadline
    alert = bool(expected and past_grace and status != "COMPLETE")

    notes = []
    if envelope.get("recovery_type") == "MANUAL_RECOVERY" and status != "COMPLETE":
        notes.append(
            "validated envelope exists via MANUAL_RECOVERY, which does not "
            "itself record data/briefing/finalization portal-final-receipt "
            "or delivery_receipt. If Portal/Notion were already updated "
            "out-of-band, the formal ledger record for this slot is still "
            "open; if this is intentional, no further automated action is "
            "expected from this watchdog."
        )
    if bridge["exists"] and not bridge["discoverable_by_chain_build"]:
        notes.append(
            "a source bridge registry is committed but its index.json is "
            "missing, so briefing_core/chain.py's automatic discovery will "
            "still see this slot as source_status=UNAVAILABLE on a future "
            "natural rebuild."
        )

    return {
        "schema_version": WATCHDOG_SCHEMA,
        "briefing_id": f"{date}-{slot}",
        "slot": slot,
        "decision_date": date,
        "checked_at_utc": now.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "slot_expected_today": expected,
        "grace_deadline_kst": deadline.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "grace_deadline_source": (
            "config/atlas_semantic_validator.json:timeout_minutes applied to "
            "the daily-briefing.yml natural cron fire time for this slot"
        ),
        "grace_timeout_minutes": timeout_minutes,
        "past_grace": past_grace,
        "status": status,
        "alert": alert,
        "notes": notes,
        "checks": {
            "natural_receipt": natural,
            "semantic_verdict": semantic,
            "source_bridge": bridge,
            "validated_envelope": envelope,
            "portal_final_receipt": portal_receipt,
            "final_drain": drain,
        },
        "authority": dict(NO_AUTHORITY),
    }


def publish(repo_root: Path, report: dict) -> dict:
    """Append-only status artifact, separate from the finalization ledger.
    Writes a new rev only when the comparable content actually changed
    (idempotent NO_CHANGE, matching the rest of this codebase's convention)."""
    directory = repo_root / WATCHDOG_ROOT / report["decision_date"] / report["slot"]
    comparable = {k: v for k, v in report.items() if k != "checked_at_utc"}
    prior = _latest_rev(directory, "status")
    if prior is not None:
        prior_body = _read_json(prior) or {}
        prior_comparable = {k: v for k, v in prior_body.items() if k != "checked_at_utc"}
        if prior_comparable == comparable:
            return {"changed": False, "path": str(prior.relative_to(repo_root))}
    rev = _next_rev(directory, "status")
    path = directory / f"status-rev-{rev:03d}.json"
    body = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    _atomic_write(path, body)
    _atomic_write(directory / "latest.json", body)
    return {"changed": True, "path": str(path.relative_to(repo_root))}


# ----------------------------------------------------------------------- CLI

def _emit(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def _parse_now(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    parsed = _dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Atlas AM/PM briefing handoff watchdog (read-only: check, classify, alert)")
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--slot", required=True, choices=list(SUPPORTED_SLOTS))
    parser.add_argument("--decision-date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--now", help="ISO8601 timestamp override, for tests and replay only")
    parser.add_argument("--publish", action="store_true",
                        help="write/update the append-only status artifact")
    parser.add_argument("--fail-on-alert", action="store_true",
                        help="exit 1 when status != COMPLETE past the documented grace deadline")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    try:
        report = run_check(repo_root, args.slot, args.decision_date, now=_parse_now(args.now))
        if args.publish:
            report["_publish"] = publish(repo_root, report)
    except WatchdogError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    _emit(report)
    if args.fail_on_alert and report["alert"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
