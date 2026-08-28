#!/usr/bin/env python3
"""Atlas Briefing Finalization Gate -- contract ``briefing_finalization/18``.

Sits between the existing H-24 chain and any human-reaching delivery:

    daily_orchestrator.py publish              -> evidence/daily_briefing/{slot}/{date}/rev-NNN/
    daily_briefing_delivery.py publish-locator -> data/briefing/daily_briefing_sources.json
    daily_briefing_delivery.py consume         -> rendered delivery markdown
    >>> seal -> ingest verdict -> deliver <<<   (this module)
    human-reaching transports

Creates no parallel briefing system: `seal` reads only what the locator names
(EXACT_POINTER_ONLY_NO_FALLBACK).

Failure model this revision is written against: **a fresh runner with a fresh
checkout**, not a retry on a surviving filesystem.  Anything that must survive
a process death has to be committed and pushed before the irreversible act it
guards, which is why delivery intent is durability-gated.

Opens no Stage / Buy / Action / Order / Production / Trading authority.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas_ed25519 as ed25519  # noqa: E402

#: Covers BOTH the artifact schema and how recorded artifacts are interpreted.
#: rev 12 changed only the interpretation (authority became per-stream) and the
#: version stayed put, which would have let two incompatible readings share a
#: label.  A semantics change bumps this even when no field moves.
CONTRACT_VERSION = "briefing_finalization/18"

FINALIZATION_ROOT = "data/briefing/finalization"
LOCATOR_PATH = "data/briefing/daily_briefing_sources.json"
APPROVAL_PUBKEY_PATH = "config/atlas_approval_pubkey.txt"
CONCLUSION_SPEC_ALLOWLIST_PATH = "config/atlas_conclusion_diff_allowlist.json"
TRUST_LOG_PATH = "data/briefing/finalization/approval_trust_log.jsonl"
ACTIVATION_PATH = "config/atlas_finalization_activation.json"
SEMANTIC_VALIDATOR_PATH = "config/atlas_semantic_validator.json"
PROJECTION_PATH = "config/atlas_projection.json"
PORTAL_FINAL_RECEIPT_SCHEMA = "briefing_portal_final_receipt/1"

#: Out-of-band anchor for the approval public key.  A repo writer can edit the
#: key file; they cannot edit a GitHub secret.  When this is set, the key in the
#: repo must match it or verification fails.
APPROVAL_FINGERPRINT_ENV = "ATLAS_APPROVAL_PUBKEY_FINGERPRINT"

#: (P0) Phase A NEVER auto-applies, whatever a verdict claims.  rev 4 trusted
#: any non-null `spec_version`, so a validator could hand itself auto-apply by
#: inventing one ("evil/999").  Authority does not come from the artifact being
#: judged.  Flipping this to False is a ratification act, not a code tweak.
PHASE_A_AUTO_APPLY_DISABLED = True

KST = ZoneInfo("Asia/Seoul")
SUPPORTED_SLOTS = ("morning", "evening")
SLOT_SUFFIX = {"morning": "am", "evening": "pm"}
#: KST wall-clock the producer is scheduled for; used to decide whether a slot
#: that produced nothing is late (P0-7) rather than simply not due yet.
SLOT_DUE_KST = {"morning": (7, 5), "evening": (18, 30)}
SLOT_DUE_GRACE_MIN = 60
MISSED_SLOT_LOOKBACK_DAYS = 5

VALIDATION_TIMEOUT_MIN = 20
UNKNOWN = "UNKNOWN"

#: Statuses an external validator may submit.  UNVALIDATED_* is absent on
#: purpose (P0-4): rev 18 keeps those values readable only for historical audit
#: compatibility; neither a validator nor the delivery path may create them.
EXTERNAL_VALIDATION_STATUSES = ("PASS", "PASS_WITH_CORRECTION", "HOLD", "MACHINE_CLEARED")
#: Historical rev <=17 audit values.  They remain in the parser so already
#: committed evidence is readable, but rev 18 never treats them as authority.
#: ``internal=True`` is reserved for migration/audit tooling and is not used by
#: the production delivery path.
INTERNAL_VALIDATION_STATUSES = ("UNVALIDATED_TIMEOUT", "UNVALIDATED_NO_VALIDATOR")
VALIDATION_STATUSES = EXTERNAL_VALIDATION_STATUSES + INTERNAL_VALIDATION_STATUSES

#: Two independent verdict streams.  A machine checker and a semantic reviewer
#: are answering different questions, so one must not be able to overwrite the
#: other's answer.  Without this split a transient structural fault became a
#: PERMANENT hold: the machine could raise a block but had no way to withdraw
#: it, because withdrawing it by saying PASS would be claiming facts it never
#: checked.
AUTHORITY_STREAMS = ("machine", "semantic")
DEFAULT_AUTHORITY_STREAM = "semantic"

#: What the machine stream is allowed to assert.  PASS is deliberately absent:
#: passing structural checks is not evidence that anything is true.
MACHINE_STREAM_STATUSES = ("HOLD", "PASS_WITH_CORRECTION", "MACHINE_CLEARED")

#: Verdicts a signed resolution may record for a post-delivery source change.
#: NONE  = the change does not move any investment conclusion -> audit only.
#: PRESENT = it does -> the CIO acted; what they did is recorded, not inferred.
CAPITAL_IMPACT_VERDICTS = ("NONE", "PRESENT")

#: Withdraws the machine stream's own prior block.  It asserts nothing about
#: the briefing's content, so on its own it leaves the gate's verdict slot open
#: and the explicit semantic validator must still answer.
MACHINE_CLEARED = "MACHINE_CLEARED"

#: (P0-3) status -> may this ever reach a human?  Only an explicit semantic
#: PASS (or an explicitly reviewed correction) may do so.  Historical
#: UNVALIDATED_* records remain readable audit material, but rev 18 never
#: treats them as delivery authority.
STATUS_DELIVERABLE = {
    "PASS": True,
    "PASS_WITH_CORRECTION": True,
    "UNVALIDATED_TIMEOUT": False,
    "UNVALIDATED_NO_VALIDATOR": False,
    "MACHINE_CLEARED": True,     # never governs on its own; see resolve_validation
    "HOLD": False,
}

CORRECTION_CLASSES = ("FACT", "ARITHMETIC", "DATE", "EVIDENCE_GRADE", "WORDING",
                      "SOURCE_REVISION", "SOURCE_CONTENT")
DELIVERY_MARKER_PREFIX = "atlas-delivery-id:"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PORTAL_RESULT = ("DEPLOYED", "NO_CHANGE")
PORTAL_RECEIPT_AUTHORITY = {
    "read_only": True,
    "stage_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
    "broker_credentials_present": False,
}
PORTAL_FINAL_RECEIPT_FIELDS = {
    "schema_version", "briefing_id", "kst_date", "slot", "projection_id",
    "envelope_commit", "envelope_path", "envelope_sha256", "source_commit",
    "portal_result", "portal_run_id", "portal_source_sha", "deployment_url",
    "notion_receipt_page_id", "viewer_readback_verified",
    "notion_receipt_readback_verified", "delivery_payload_sha256",
    "validation_rev", "observed_at_utc", "authority",
}

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_CIO_GATE = 3
EXIT_VALIDATION_PENDING = 4
EXIT_TRANSPORT_FAILED = 5
EXIT_RECONCILE_PENDING = 6
EXIT_INTENT_NOT_DURABLE = 7
EXIT_HELD = 8
EXIT_DRAIN_INCOMPLETE = 9
EXIT_VALIDATION_INVALID = 10
EXIT_POST_DELIVERY_CHANGE = 11
EXIT_CIO_ATTENTION_REQUIRED = 12


class FinalizationError(RuntimeError):
    def __init__(self, code: str, message: str, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.exit_code = exit_code


# ---------------------------------------------------------------- primitives

def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _iso(moment: _dt.datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _parse_iso(value: str, code: str) -> _dt.datetime:
    try:
        parsed = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise FinalizationError(code, f"unparseable timestamp: {value!r}") from None
    if parsed.tzinfo is None:
        raise FinalizationError(code, f"timestamp is not timezone-aware: {value!r}")
    return parsed.astimezone(_dt.timezone.utc)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == payload:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)
    return True


def _read_json(path: Path, code: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FinalizationError(code, f"missing file: {path}") from None
    except json.JSONDecodeError as exc:
        raise FinalizationError(code, f"unreadable JSON at {path}: {exc}") from None


def _read_bytes(path: Path, code: str) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise FinalizationError(code, f"missing file: {path}") from None


def _validate_slot(slot: str) -> str:
    if slot not in SUPPORTED_SLOTS:
        raise FinalizationError(
            "FINALIZATION_SLOT_UNSUPPORTED", f"slot must be one of {SUPPORTED_SLOTS}, got {slot!r}"
        )
    return slot


def briefing_id(kst_date: str, slot: str) -> str:
    return f"{kst_date}-{SLOT_SUFFIX[_validate_slot(slot)]}"


def slot_dir(repo_root: Path, kst_date: str, slot: str) -> Path:
    return repo_root / FINALIZATION_ROOT / kst_date / _validate_slot(slot)


def _next_rev(directory: Path, prefix: str) -> int:
    if not directory.exists():
        return 1
    revs = [int(p.stem.rsplit("-", 1)[-1]) for p in directory.glob(f"{prefix}-rev-*.json")
            if p.stem.rsplit("-", 1)[-1].isdigit()]
    return max(revs) + 1 if revs else 1


def _latest(directory: Path, prefix: str) -> Path | None:
    if not directory.exists():
        return None
    found = sorted(directory.glob(f"{prefix}-rev-*.json"))
    return found[-1] if found else None


# ------------------------------------------------------- H-24 locator binding

def bind_locator(repo_root: Path, kst_date: str, slot: str) -> dict:
    """Read the H-24 locator and verify it against the bytes on disk."""
    locator = _read_json(repo_root / LOCATOR_PATH, "FINALIZATION_LOCATOR_UNREADABLE")
    if locator.get("slot") != slot or locator.get("decision_date") != kst_date:
        raise FinalizationError(
            "FINALIZATION_LOCATOR_IDENTITY_MISMATCH",
            f"locator names {locator.get('slot')}/{locator.get('decision_date')}, "
            f"asked for {slot}/{kst_date}",
        )
    briefing_bytes = _read_bytes(repo_root / locator["briefing_path"], "FINALIZATION_BRIEFING_MISSING")
    packet_bytes = _read_bytes(repo_root / locator["packet_path"], "FINALIZATION_PACKET_MISSING")
    index_bytes = _read_bytes(repo_root / locator["index_path"], "FINALIZATION_INDEX_MISSING")
    for label, actual, expected, code in (
        ("briefing", _sha256(briefing_bytes), locator.get("briefing_sha256"), "FINALIZATION_BRIEFING_SHA_MISMATCH"),
        ("packet_file", _sha256(packet_bytes), locator.get("packet_file_sha256"), "FINALIZATION_PACKET_SHA_MISMATCH"),
        ("index", _sha256(index_bytes), locator.get("index_sha256"), "FINALIZATION_INDEX_SHA_MISMATCH"),
    ):
        if expected != actual:
            raise FinalizationError(code, f"{label}: locator says {expected}, bytes hash to {actual}")
    return {"locator": locator, "briefing_bytes": briefing_bytes,
            "briefing_sha256": locator["briefing_sha256"],
            "packet_sha256": locator.get("packet_sha256"), "revision": locator.get("revision")}


def delivery_marker(kst_date: str, slot: str, rev: int) -> str:
    return f"<!-- {DELIVERY_MARKER_PREFIX} {briefing_id(kst_date, slot)}/rev-{rev:03d} -->"


def build_delivery_payload(briefing_bytes: bytes, consume_markdown: bytes | None, marker: str) -> bytes:
    body = briefing_bytes.rstrip(b"\n")
    if consume_markdown:
        body = body + b"\n\n" + consume_markdown.rstrip(b"\n")
    return body + b"\n\n" + marker.encode("utf-8") + b"\n"


# -------------------------------------------------------- seal (idempotent)

def _seal_key(bound: dict, consume_markdown: bytes | None) -> str:
    """Identity of the INPUT.  Same inputs must yield the same sealed draft."""
    return _sha256(_canonical(_source_fingerprint(bound, consume_markdown)))


def _source_fingerprint(bound: dict, consume_markdown: bytes | None) -> dict:
    """Every axis a change could arrive on, named separately.

    Named individually so a post-delivery change can say WHICH axis moved
    instead of reporting `revision 1 -> 1` when only the consume text changed.
    ``body_sha256`` is the marker-free content hash: the thing that would have
    been delivered, comparable across revisions.
    """
    body = bound["briefing_bytes"].rstrip(b"\n")
    if consume_markdown:
        body = body + b"\n\n" + consume_markdown.rstrip(b"\n")
    return {
        "briefing_sha256": bound["briefing_sha256"],
        "packet_sha256": bound["packet_sha256"],
        "revision": bound["revision"],
        "consume_sha256": _sha256(consume_markdown) if consume_markdown else None,
        "body_sha256": _sha256(body),
    }


def _fingerprint_delta(before: dict, after: dict) -> list[dict]:
    """Which axes actually moved.  Empty means the seal keys disagreed for a
    reason not captured here, which is itself worth surfacing."""
    axes = [
        ("revision", "source.revision", "SOURCE_REVISION"),
        ("briefing_sha256", "source.briefing_sha256", "SOURCE_CONTENT"),
        ("packet_sha256", "source.packet_sha256", "SOURCE_CONTENT"),
        ("consume_sha256", "consume", "SOURCE_CONTENT"),
        ("body_sha256", "delivery_body", "SOURCE_CONTENT"),
    ]
    out = []
    for key, field_path, cls in axes:
        if before.get(key) != after.get(key):
            out.append({"axis": key, "field_path": field_path, "class": cls,
                        "before": before.get(key), "after": after.get(key)})
    return out


def post_delivery_change_key(delivered_seal_key: str, new_seal_key: str) -> str:
    return _sha256(_canonical({"from": delivered_seal_key, "to": new_seal_key}))


def record_post_delivery_change(repo_root: Path, kst_date: str, slot: str,
                                delivered_draft: dict, receipt: dict,
                                new_seal_key: str, new_fingerprint: dict,
                                briefing_path: str) -> dict:
    """The source changed after delivery.  Audit it; do NOT make it deliverable.

    No ``draft-rev-NNN.json`` is written, so nothing downstream can pick this up
    as a normal briefing.  Correct the record, do not re-send the briefing.

    Idempotent: the same observation seals to the same key, so re-running does
    not stack duplicate artifacts or duplicate ledger entries.
    """
    directory = slot_dir(repo_root, kst_date, slot)
    change_key = post_delivery_change_key(delivered_draft.get("seal_key"), new_seal_key)
    for existing in sorted(directory.glob("post-delivery-change-rev-*.json")):
        body = _read_json(existing, "FINALIZATION_CHANGE_UNREADABLE")
        if body.get("post_delivery_change_key") == change_key:
            return {**body, "post_delivery_change": True, "reused": True}

    before = delivered_draft.get("source_fingerprint") or {
        "revision": delivered_draft["source"].get("revision"),
        "briefing_sha256": delivered_draft["source"].get("briefing_sha256"),
        "packet_sha256": delivered_draft["source"].get("packet_sha256"),
        "consume_sha256": None, "body_sha256": None,
    }
    delta = _fingerprint_delta(before, new_fingerprint)

    rev = _next_rev(directory, "post-delivery-change")
    body = {
        "contract_version": CONTRACT_VERSION,
        "briefing_id": briefing_id(kst_date, slot), "slot": slot, "kst_date": kst_date,
        "rev": rev, "post_delivery_change_key": change_key,
        "observed_at_utc": _iso(_utcnow()),
        "delivered_draft_rev": delivered_draft["rev"],
        "delivered_seal_key": delivered_draft.get("seal_key"),
        "delivered_payload_sha256": delivered_draft["delivery_payload_sha256"],
        "delivered_at_utc": receipt.get("delivered_at_utc"),
        "delivered_fingerprint": before,
        "new_seal_key": new_seal_key,
        "new_fingerprint": new_fingerprint,
        "changed_axes": delta,
        "normal_delivery": False,
        "redelivery": "FORBIDDEN",
        "resolution_path": "corrections_ledger_and_portal",
        # Whether this matters to money is a capital-impact question and the
        # conclusion_diff spec that would answer it is unratified.
        "capital_impact": UNKNOWN,
    }
    _atomic_write(directory / f"post-delivery-change-rev-{rev:03d}.json", _canonical(body) + b"\n")
    for item in delta:
        append_correction(repo_root, kst_date, slot, {
            "class": item["class"], "field_path": item["field_path"],
            "before": item["before"], "after": item["after"], "source": briefing_path,
        }, portal_synced=False)
    return {**body, "post_delivery_change": True, "reused": False}


def seal(repo_root: Path, kst_date: str, slot: str, consume_path: Path | None) -> dict:
    """Freeze the payload.  Idempotent (P0-5).

    rev 3 minted a new rev on every call, which changed the marker, changed the
    payload hash, and instantly invalidated any verdict already returned for the
    previous seal.  A retry of an unchanged briefing must reuse the existing
    draft, including its sealed_at_utc -- otherwise re-sealing silently resets
    the validation timeout clock.
    """
    bound = bind_locator(repo_root, kst_date, slot)
    consume_markdown = _read_bytes(consume_path, "FINALIZATION_CONSUME_UNREADABLE") if consume_path else None
    key = _seal_key(bound, consume_markdown)

    directory = slot_dir(repo_root, kst_date, slot)
    existing = _latest(directory, "draft")

    # (P0) Once a slot has been delivered, its receipt is supposed to represent
    # what the person actually holds.  The producer legitimately publishes a
    # new same-day revision when data recovers, and rev 7 happily sealed it as
    # draft-rev-002 -- which nobody would ever receive, while `backlog` saw the
    # receipt and called the slot complete.  A silently superseded briefing is
    # worse than a visibly missing one.
    receipt_file = receipt_path(repo_root, kst_date, slot)
    if receipt_file.exists():
        delivered = _read_json(receipt_file, "FINALIZATION_RECEIPT_UNREADABLE")
        delivered_draft = _read_json(
            directory / f"draft-rev-{delivered['draft_rev']:03d}.json",
            "FINALIZATION_DRAFT_UNREADABLE")
        if delivered_draft.get("seal_key") == key:
            delivered_draft["reused"] = True
            return delivered_draft
        return record_post_delivery_change(
            repo_root, kst_date, slot, delivered_draft, delivered, key,
            _source_fingerprint(bound, consume_markdown),
            bound["locator"]["briefing_path"])

    if existing is not None:
        prior = _read_json(existing, "FINALIZATION_DRAFT_UNREADABLE")
        if prior.get("seal_key") == key:
            prior["reused"] = True
            return prior

    rev = _next_rev(directory, "draft")
    marker = delivery_marker(kst_date, slot, rev)
    payload = build_delivery_payload(bound["briefing_bytes"], consume_markdown, marker)
    body = {
        "contract_version": CONTRACT_VERSION,
        "briefing_id": briefing_id(kst_date, slot), "slot": slot, "kst_date": kst_date,
        "rev": rev, "seal_key": key, "sealed_at_utc": _iso(_utcnow()),
        "source": {
            "locator_path": LOCATOR_PATH, "selection_policy": "EXACT_POINTER_ONLY_NO_FALLBACK",
            "briefing_path": bound["locator"]["briefing_path"],
            "packet_path": bound["locator"]["packet_path"],
            "index_path": bound["locator"]["index_path"],
            "revision": bound["revision"], "briefing_sha256": bound["briefing_sha256"],
            "packet_sha256": bound["packet_sha256"],
        },
        "delivery_marker": marker,
        "source_fingerprint": _source_fingerprint(bound, consume_markdown),
        "delivery_payload_sha256": _sha256(payload),
        "delivery_payload_bytes": len(payload),
        "consume_included": consume_markdown is not None,
        "reused": False,
    }
    _atomic_write(directory / f"draft-rev-{rev:03d}.json", _canonical(body) + b"\n")
    _atomic_write(directory / f"payload-rev-{rev:03d}.md", payload)
    return body


# ------------------------------------------------------ validation + routing

def load_ratified_specs(repo_root: Path) -> list[str]:
    """conclusion_diff spec versions the CIO has ratified, read from the repo.

    Absent file == nothing ratified.  This is read LOCALLY; a verdict naming a
    spec that is not on this list gets no authority from naming it.
    """
    path = repo_root / CONCLUSION_SPEC_ALLOWLIST_PATH
    if not path.exists():
        return []
    data = _read_json(path, "FINALIZATION_SPEC_ALLOWLIST_UNREADABLE")
    return list(data.get("ratified_spec_versions", []))


def derive_routing(validation: dict, ratified_specs: list[str] | None = None) -> dict:
    status = validation.get("validation_status")
    if status not in VALIDATION_STATUSES:
        raise FinalizationError(
            "FINALIZATION_STATUS_UNSUPPORTED",
            f"validation_status must be one of {VALIDATION_STATUSES}, got {status!r}")
    ratified_specs = ratified_specs or []
    corrections = validation.get("corrections") or []
    for corr in corrections:
        if corr.get("class") not in CORRECTION_CLASSES:
            raise FinalizationError("FINALIZATION_CORRECTION_CLASS_UNSUPPORTED",
                                    f"unknown correction class {corr.get('class')!r}")
    diff = validation.get("conclusion_diff") or {}
    claimed_spec = diff.get("spec_version")
    spec_ratified = claimed_spec is not None and claimed_spec in ratified_specs
    if PHASE_A_AUTO_APPLY_DISABLED or not spec_ratified:
        # The verdict's own claim about which spec it used is not authority.
        auto_apply_allowed, conclusion_changed = False, UNKNOWN
    else:
        conclusion_changed = diff.get("investment_conclusion_changed")
        auto_apply_allowed = (conclusion_changed is False
                              and diff.get("money_action_changed") is False
                              and diff.get("stage_changed") is False)
    return {
        "spec_version_claimed": claimed_spec,
        "spec_version_ratified": spec_ratified,
        "phase_a_auto_apply_disabled": PHASE_A_AUTO_APPLY_DISABLED,
        "investment_conclusion_changed": conclusion_changed,
        "correction_count": len(corrections),
        "auto_apply_allowed": auto_apply_allowed,
        "cio_gate_required": bool(corrections) and not auto_apply_allowed,
        "portal_update_required": bool(corrections),
        # (P0-3) the status itself is a delivery gate, not just a label
        "status_deliverable": STATUS_DELIVERABLE[status],
    }


def record_validation(repo_root: Path, kst_date: str, slot: str, validation: dict,
                      internal: bool = False) -> dict:
    """Bind a verdict to the exact sealed payload it examined.

    (P0-4) ``internal`` is the only compatibility path for historical
    UNVALIDATED_* audit records.  An external validator that submits one is
    refused, and rev 18's production delivery path never creates one.
    """
    status = validation.get("validation_status")
    if not internal and status in INTERNAL_VALIDATION_STATUSES:
        raise FinalizationError(
            "FINALIZATION_STATUS_NOT_EXTERNALLY_SUBMITTABLE",
            f"{status!r} is historical audit vocabulary only; "
            "an external validator cannot assert it and it cannot authorize delivery",
        )
    stream = validation.get("authority_stream", DEFAULT_AUTHORITY_STREAM)
    if stream not in AUTHORITY_STREAMS:
        raise FinalizationError("FINALIZATION_AUTHORITY_STREAM_UNSUPPORTED",
                                f"authority_stream must be one of {AUTHORITY_STREAMS}, got {stream!r}")
    if stream == "machine" and status not in MACHINE_STREAM_STATUSES and not internal:
        raise FinalizationError(
            "FINALIZATION_MACHINE_STREAM_STATUS_FORBIDDEN",
            f"the machine stream may assert {MACHINE_STREAM_STATUSES}, not {status!r}; "
            "passing structural checks is not evidence that a claim is true")
    if stream != "machine" and status == MACHINE_CLEARED:
        raise FinalizationError(
            "FINALIZATION_MACHINE_CLEAR_FROM_WRONG_STREAM",
            f"{MACHINE_CLEARED} withdraws the machine stream's own block and cannot be "
            "submitted on the semantic stream")
    directory = slot_dir(repo_root, kst_date, slot)
    draft_file = _latest(directory, "draft")
    if draft_file is None:
        raise FinalizationError("FINALIZATION_DRAFT_MISSING", f"no sealed draft for {kst_date}/{slot}")
    draft = _read_json(draft_file, "FINALIZATION_DRAFT_UNREADABLE")

    claimed = validation.get("delivery_payload_sha256")
    if not claimed:
        raise FinalizationError(
            "FINALIZATION_VALIDATION_PAYLOAD_UNBOUND",
            "validation result must carry delivery_payload_sha256 naming the exact "
            "payload it examined; an unbound verdict is not accepted")
    if claimed != draft["delivery_payload_sha256"]:
        raise FinalizationError(
            "FINALIZATION_VALIDATION_PAYLOAD_MISMATCH",
            f"validation targets {claimed}, sealed draft is {draft['delivery_payload_sha256']}")

    rev = _next_rev(directory, "validation")
    body = dict(validation)
    body.update({"contract_version": CONTRACT_VERSION, "briefing_id": briefing_id(kst_date, slot),
                 "slot": slot, "kst_date": kst_date, "rev": rev,
                 "recorded_at_utc": _iso(_utcnow()), "submitted_internally": internal,
                 "authority_stream": stream,
                 "delivery_payload_sha256": draft["delivery_payload_sha256"],
                 "routing": derive_routing(validation, load_ratified_specs(repo_root))})
    _atomic_write(directory / f"validation-rev-{rev:03d}.json", _canonical(body) + b"\n")
    return body


def verdict_digest(verdict: dict) -> str:
    """Canonical hash of everything a verdict asserts.

    (P0) rev 4 deduped on (payload_sha, status) only, so a second verdict with
    the SAME status but DIFFERENT corrections was silently discarded.
    """
    # Fields the gate injects when recording, plus path-derivable identity, are
    # not part of what the verdict ASSERTS.
    injected = {"rev", "recorded_at_utc", "routing", "submitted_internally",
                "contract_version", "briefing_id", "slot", "kst_date", "authority_stream"}
    body = {k: v for k, v in verdict.items() if k not in injected}
    # The stream IS part of what a verdict asserts, but the gate fills in the
    # default when the file omits it -- so normalise before hashing, otherwise
    # a recorded verdict never matches the inbox file it came from.
    body["authority_stream"] = verdict.get("authority_stream", DEFAULT_AUTHORITY_STREAM)
    return _sha256(_canonical(body))


def _inbox_rev(path: Path) -> int:
    """Legacy single-file inbox sorts before every numbered revision."""
    stem = path.stem.rsplit("-", 1)[-1]
    return int(stem) if stem.isdigit() else 0


def authoritative_inboxes(directory: Path) -> tuple[dict[str, Path], list[str], list[str]]:
    """The verdict file that speaks for EACH stream, plus history and rubble.

    (P0) rev 11 split the streams but kept ONE authoritative inbox: the highest
    numbered file overall.  So an un-ingested verdict from the other stream was
    demoted to "superseded" and never recorded, and the guarantees collapsed on
    timing alone --

      * un-ingested machine HOLD + newer semantic PASS  -> only PASS recorded
        -> "semantic PASS cannot lift machine HOLD" broken.
      * un-ingested semantic HOLD + newer MACHINE_CLEARED -> only the clear
        recorded -> "machine clears only its own block" broken.

    Authority is therefore per stream.  Within a stream the rev-8 rule is
    unchanged: the highest revision is authority and earlier ones are history,
    so good->bad still fails closed and bad->good still recovers.
    """
    authority: dict[str, Path] = {}
    superseded: list[str] = []
    unreadable: list[str] = []

    candidates = sorted(directory.glob("validation-inbox-rev-*.json"), key=_inbox_rev)
    legacy = directory / "validation-inbox.json"
    if legacy.exists():
        candidates.insert(0, legacy)

    for path in candidates:
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            unreadable.append(path.name)
            continue
        stream = body.get("authority_stream", DEFAULT_AUTHORITY_STREAM)
        if stream not in AUTHORITY_STREAMS:
            unreadable.append(path.name)
            continue
        previous = authority.get(stream)
        if previous is not None:
            superseded.append(previous.name)
        authority[stream] = path
    return authority, superseded, unreadable


def _recorded_validations(directory: Path) -> list[tuple[int, dict]]:
    out = []
    for path in sorted(directory.glob("validation-rev-*.json")):
        body = _read_json(path, "FINALIZATION_VALIDATION_UNREADABLE")
        out.append((body["rev"], body))
    return out


def ingest_inbox(repo_root: Path, kst_date: str, slot: str) -> dict:
    """Record each stream's authoritative verdict.

    Every stream is ingested on its own terms; one stream's newer file no
    longer buries another stream's unread verdict.
    """
    directory = slot_dir(repo_root, kst_date, slot)
    authority, superseded, unreadable = authoritative_inboxes(directory)
    if not authority:
        return {"ingested": False, "reason": "FINALIZATION_VALIDATION_INBOX_ABSENT",
                "superseded_files": superseded, "unreadable_files": unreadable}

    known = {verdict_digest(body) for _rev, body in _recorded_validations(directory)}
    ingested, skipped, errors = {}, {}, {}
    for stream in AUTHORITY_STREAMS:
        path = authority.get(stream)
        if path is None:
            continue
        verdict = _read_json(path, "FINALIZATION_VALIDATION_UNREADABLE")
        if verdict_digest(verdict) in known:
            skipped[stream] = path.name
            continue
        try:
            ingested[stream] = record_validation(repo_root, kst_date, slot, verdict)
        except FinalizationError as exc:
            # Recorded, not swallowed: resolve_validation fails closed because
            # this stream's authority is still unrecorded.
            errors[stream] = {"file": path.name, "error": exc.code, "message": exc.message}

    result = {"ingested": bool(ingested),
              "authority_files": {k: v.name for k, v in authority.items()},
              "superseded_files": superseded, "unreadable_files": unreadable,
              "skipped": skipped, "errors": errors,
              "count": len(ingested)}
    if not ingested and not errors:
        result["reason"] = "ALREADY_INGESTED"
    if ingested:
        result["validation"] = ingested.get("semantic") or ingested.get("machine")
    if errors:
        if len(errors) == 1:
            # Keep the specific reason rather than burying it in a wrapper.
            only = next(iter(errors.values()))
            raise FinalizationError(only["error"], only["message"], EXIT_VALIDATION_INVALID)
        raise FinalizationError(
            "FINALIZATION_VALIDATION_INVALID",
            f"authoritative verdicts could not be recorded: {errors}",
            EXIT_VALIDATION_INVALID)
    return result


def _stream_of(body: dict) -> str:
    return body.get("authority_stream", DEFAULT_AUTHORITY_STREAM)


def _latest_per_stream(recorded: list[tuple[int, dict]]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for _rev, body in recorded:
        latest[_stream_of(body)] = body
    return latest


def govern(recorded: list[tuple[int, dict]]) -> dict | None:
    """Which recorded verdict actually governs delivery.

    Blocking is the UNION of the two streams, so neither can wave the other
    through.  A clean machine run withdraws only the machine stream's own block;
    it can never lift a semantic or CIO hold, and it can never supply the PASS
    that would let a briefing out on machine evidence alone.
    """
    latest = _latest_per_stream(recorded)
    machine = latest.get("machine")
    semantic = latest.get("semantic")
    machine_status = machine["validation_status"] if machine else None
    semantic_status = semantic["validation_status"] if semantic else None

    if machine_status == "HOLD":
        return machine
    if semantic_status == "HOLD":
        return semantic
    if machine_status == "PASS_WITH_CORRECTION":
        return machine
    if semantic_status in ("PASS", "PASS_WITH_CORRECTION",
                           "UNVALIDATED_TIMEOUT", "UNVALIDATED_NO_VALIDATOR"):
        return semantic
    # Machine cleared (or said nothing) and no semantic verdict exists: the slot
    # stays sealed until the named semantic validator records an explicit answer.
    return None


def resolve_validation(directory: Path) -> tuple[dict | None, str | None]:
    """The verdict `deliver` must act on.

    Returns (validation, problem).  A recorded verdict is not enough: it has to
    be the one the AUTHORITATIVE inbox file asserts.  Otherwise a later, broken
    revision is invisible behind an earlier, valid one.
    """
    authority, _superseded, unreadable = authoritative_inboxes(directory)
    recorded = _recorded_validations(directory)
    if unreadable:
        return None, (f"unreadable verdict material present ({unreadable}); a file that "
                      "cannot be parsed is not silence")
    if not authority:
        return govern(recorded), None
    known = {verdict_digest(body) for _rev, body in recorded}
    for stream in AUTHORITY_STREAMS:
        path = authority.get(stream)
        if path is None:
            continue
        digest = verdict_digest(_read_json(path, "FINALIZATION_VALIDATION_UNREADABLE"))
        if digest not in known:
            return None, (f"the authoritative {stream} verdict {path.name!r} has not been "
                          "recorded; a malformed, stale or unbound verdict is not silence "
                          "and must not time out into delivery")
    return govern(recorded), None


# ------------------------------------------------ CIO approval (asymmetric)

def approval_message(briefing: str, payload_sha: str, validation_rev: int, approved_by: str,
                     decision: str, contract_version: str = CONTRACT_VERSION) -> bytes:
    """Every field the gate acts on must be inside the signature.

    (P0) rev 4 signed only identity+binding and left `decision` outside, so a
    DENY artifact could be edited to APPROVE and still verify.  Canonical JSON
    keeps the encoding unambiguous.
    """
    return _canonical({
        "contract_version": contract_version,
        "purpose": "atlas.briefing_finalization.approval",
        "briefing_id": briefing,
        "decision": decision,
        "approves_payload_sha256": payload_sha,
        "approves_validation_rev": validation_rev,
        "approved_by": approved_by,
    })


def pubkey_fingerprint(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()


def load_public_key(repo_root: Path) -> bytes:
    """The verification key is PUBLIC and lives in the repo on purpose.

    (P0-2) rev 3 used a shared HMAC secret, which cannot be a trust boundary:
    a job that can verify can also forge.  The private half now never enters CI
    at all -- the CIO signs offline and commits the signed artifact.
    """
    path = repo_root / APPROVAL_PUBKEY_PATH
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise FinalizationError(
            "FINALIZATION_APPROVAL_PUBKEY_MISSING",
            f"no approval public key at {APPROVAL_PUBKEY_PATH}; approvals cannot be verified",
            EXIT_CIO_GATE) from None
    try:
        key = bytes.fromhex(raw)
    except ValueError:
        raise FinalizationError("FINALIZATION_APPROVAL_PUBKEY_MALFORMED",
                                "approval public key is not hex", EXIT_CIO_GATE) from None
    if len(key) != 32:
        raise FinalizationError("FINALIZATION_APPROVAL_PUBKEY_MALFORMED",
                                f"approval public key must be 32 bytes, got {len(key)}", EXIT_CIO_GATE)
    # (P0) The key file lives in the repo, so a repo writer can swap it and
    # self-approve.  Code cannot bootstrap its own trust root; what it CAN do is
    # honour an out-of-band anchor when one is configured, and make an
    # unanchored key visible rather than silent.
    expected = os.environ.get(APPROVAL_FINGERPRINT_ENV, "").strip().lower()
    actual = pubkey_fingerprint(key)
    if not expected:
        # (P0) The anchor is the only thing standing between "the CIO approved
        # this" and "whoever could edit a file in the repo approved this".
        # Without repo-level branch protection there is no second line, so an
        # unanchored approval is refused rather than merely logged.
        raise FinalizationError(
            "FINALIZATION_APPROVAL_ANCHOR_MISSING",
            f"{APPROVAL_FINGERPRINT_ENV} is not set; the approval public key has no "
            "out-of-band anchor and cannot be trusted",
            EXIT_CIO_GATE)
    if expected != actual:
        raise FinalizationError(
            "FINALIZATION_APPROVAL_PUBKEY_UNTRUSTED",
            f"approval public key fingerprint {actual} does not match the out-of-band "
            f"anchor {expected}; the key in the repo has been changed",
            EXIT_CIO_GATE)
    return key


def change_resolution_message(briefing: str, change_key: str, capital_impact: str,
                              resolved_by: str, action_taken: str = "",
                              contract_version: str = CONTRACT_VERSION) -> bytes:
    """Everything the ruling asserts, including what was done about it.

    (P0) rev 14 left ``action_taken`` outside the signature, so a ruling signed
    as "PRESENT, Portal note only" could be edited to read "NO ALERT SENT;
    PORTAL NOT UPDATED; ORDER XYZ EXECUTED" and still verify -- the same class
    of defect as a DENY edited into an APPROVE.
    """
    return _canonical({
        "contract_version": contract_version,
        "purpose": "atlas.briefing_finalization.post_delivery_resolution",
        "briefing_id": briefing,
        "post_delivery_change_key": change_key,
        "capital_impact": capital_impact,
        "resolved_by": resolved_by,
        "action_taken": action_taken,
    })


def load_projection_policy(repo_root: Path) -> dict:
    path = repo_root / PROJECTION_PATH
    policy = {"portal": {"adapter": None, "implemented": False},
              "alert": {"required_for_capital_impact": ["PRESENT"], "user_reaching_channels": []}}
    if path.exists():
        data = _read_json(path, "FINALIZATION_PROJECTION_POLICY_UNREADABLE")
        policy["portal"].update(data.get("portal") or {})
        policy["alert"].update(data.get("alert") or {})
    return policy


def _receipt_rev(path: Path) -> int:
    stem = path.stem.rsplit("-", 1)[-1]
    return int(stem) if stem.isdigit() else 0


def _load_receipts(directory: Path, prefix: str, code: str) -> dict[str, dict]:
    """Latest revision per change wins -- the same authority rule as the inbox.

    (P0) rev 17 used ``setdefault``, so the OLDEST receipt for a change was
    authority forever.  A bad first receipt could never be superseded by a
    correct one, and because these artifacts are append-only the only escape
    was deleting the bad file.  It bit hardest on re-ruling: NONE -> receipt ->
    re-ruled PRESENT -> new Portal write -> new receipt still lost to the stale
    one, leaving the change permanently unfinishable.

    Latest-wins keeps the rev-8 property in both directions: a good receipt
    followed by a bad one fails closed on the bad one, and a bad receipt
    followed by a good one recovers.
    """
    out: dict[str, dict] = {}
    for path in sorted(directory.glob(f"{prefix}-rev-*.json"), key=_receipt_rev):
        body = _read_json(path, code)
        key = body.get("post_delivery_change_key")
        if key:
            out[key] = body
    return out


def load_projection_receipts(repo_root: Path, kst_date: str, slot: str) -> dict[str, dict]:
    """Proof that the Portal/SSOT write actually happened, keyed by change.

    A receipt is written by the adapter that performed the write and names what
    it wrote; it is not a boolean anyone can set.  rev 15 tracked this as
    ``portal_synced``, a hand-set flag -- the same shape as a delivery receipt
    that claims a channel nobody sent to.
    """
    return _load_receipts(slot_dir(repo_root, kst_date, slot),
                          "portal-projection-receipt", "FINALIZATION_PROJECTION_RECEIPT_UNREADABLE")


def load_alert_receipts(repo_root: Path, kst_date: str, slot: str) -> dict[str, dict]:
    return _load_receipts(slot_dir(repo_root, kst_date, slot),
                          "capital-alert-receipt", "FINALIZATION_ALERT_RECEIPT_UNREADABLE")


def expected_projection_content(briefing: str, change: dict, ruling: dict) -> dict:
    """Exactly what the Portal/SSOT is required to hold for this change.

    The receipt has to name a hash OF THIS.  Otherwise a receipt only claims
    that some write happened, not that the right thing was written -- and it
    cannot be produced before the ruling exists, because the ruling is part of
    the content.
    """
    return {
        "contract_version": CONTRACT_VERSION,
        "purpose": "atlas.briefing_finalization.portal_projection",
        "briefing_id": briefing,
        "post_delivery_change_key": change.get("post_delivery_change_key"),
        "changed_axes": [a["axis"] for a in change.get("changed_axes", [])],
        "capital_impact": ruling.get("capital_impact"),
        "action_taken": ruling.get("action_taken", ""),
        "redelivery": "FORBIDDEN",
    }


def expected_alert_content(briefing: str, change: dict, ruling: dict) -> dict:
    return {**expected_projection_content(briefing, change, ruling),
            "purpose": "atlas.briefing_finalization.capital_alert"}


def expected_projection_digest(briefing: str, change: dict, ruling: dict) -> str:
    return _sha256(_canonical(expected_projection_content(briefing, change, ruling)))


def expected_alert_digest(briefing: str, change: dict, ruling: dict) -> str:
    return _sha256(_canonical(expected_alert_content(briefing, change, ruling)))


def _verify_projection_receipt(receipt: dict, change_key: str, expected_sha: str,
                               policy: dict) -> str | None:
    """Reason the receipt is not proof, or None if it is."""
    if receipt.get("post_delivery_change_key") != change_key:
        return "PORTAL_RECEIPT_CHANGE_KEY_MISMATCH"
    if receipt.get("adapter") != policy["portal"].get("adapter"):
        return "PORTAL_RECEIPT_ADAPTER_MISMATCH"
    if not str(receipt.get("target", "")).strip():
        return "PORTAL_RECEIPT_INCOMPLETE"
    if not str(receipt.get("written_at_utc", "")).strip():
        return "PORTAL_RECEIPT_INCOMPLETE"
    if not str(receipt.get("readback_at_utc", "")).strip():
        return "PORTAL_RECEIPT_INCOMPLETE"
    if receipt.get("read_after_write_verified") is not True:
        return "PORTAL_RECEIPT_READBACK_UNVERIFIED"
    if receipt.get("content_sha256") != expected_sha:
        return "PORTAL_RECEIPT_CONTENT_MISMATCH"
    return None


def _verify_alert_receipt(receipt: dict, change_key: str, expected_sha: str,
                          policy: dict) -> str | None:
    if receipt.get("post_delivery_change_key") != change_key:
        return "ALERT_RECEIPT_CHANGE_KEY_MISMATCH"
    if receipt.get("channel") not in (policy["alert"].get("user_reaching_channels") or []):
        return "ALERT_RECEIPT_CHANNEL_NOT_USER_REACHING"
    if not str(receipt.get("sent_at_utc", "")).strip():
        return "ALERT_RECEIPT_INCOMPLETE"
    if not str(receipt.get("transport_id", "")).strip():
        return "ALERT_RECEIPT_INCOMPLETE"
    if receipt.get("transmitted_sha256") != expected_sha:
        return "ALERT_RECEIPT_CONTENT_MISMATCH"
    return None


def change_completion(briefing: str, change: dict, ruling: dict | None,
                      projection: dict | None, alert: dict | None, policy: dict) -> dict:
    """What this change still owes before it can be called finished.

    A signed ruling settles WHETHER the change matters.  It does not settle that
    the record was corrected, nor that anyone was told.  Those are separate
    facts and each needs its own proof -- and a proof has to be checkable.

    (P0) rev 16 accepted any file named ``portal-projection-receipt-rev-NNN``
    that carried a change key: a two-line stub completed the change while
    ``portal.implemented`` was false.  The prose said adapters could not produce
    receipts; the code never enforced it.
    """
    blocked_by: list[str] = []
    if ruling is None:
        blocked_by.append("CIO_RULING_MISSING")
    verdict = ruling["capital_impact"] if ruling else UNKNOWN

    projection_sha = expected_projection_digest(briefing, change, ruling) if ruling else None
    alert_sha = expected_alert_digest(briefing, change, ruling) if ruling else None

    portal_ok = False
    if not policy["portal"].get("implemented"):
        # No adapter exists, so no receipt can have been produced by one.
        blocked_by.append("PORTAL_ADAPTER_NOT_IMPLEMENTED")
        if projection is not None:
            blocked_by.append("PORTAL_RECEIPT_WITHOUT_ADAPTER")
    elif not policy["portal"].get("verified_against_live_api"):
        blocked_by.append("PORTAL_ADAPTER_NOT_LIVE_VERIFIED")
        if projection is not None:
            blocked_by.append("PORTAL_RECEIPT_WITHOUT_LIVE_VERIFICATION")
    elif projection is None:
        blocked_by.append("PORTAL_PROJECTION_RECEIPT_MISSING")
    elif ruling is None:
        blocked_by.append("PORTAL_RECEIPT_BEFORE_RULING")
    else:
        reason = _verify_projection_receipt(projection, change.get("post_delivery_change_key"),
                                            projection_sha, policy)
        if reason:
            blocked_by.append(reason)
        else:
            portal_ok = True

    alert_ok = False
    alert_required = verdict in (policy["alert"].get("required_for_capital_impact") or [])
    if alert_required:
        channels = policy["alert"].get("user_reaching_channels") or []
        if not channels:
            blocked_by.append("NO_USER_REACHING_CHANNEL_CONFIGURED")
        elif alert is None:
            blocked_by.append("CAPITAL_ALERT_RECEIPT_MISSING")
        else:
            reason = _verify_alert_receipt(alert, change.get("post_delivery_change_key"),
                                           alert_sha, policy)
            if reason:
                blocked_by.append(reason)
            else:
                alert_ok = True

    return {"capital_impact": verdict,
            "ruled": ruling is not None,
            "resolved_by": ruling.get("resolved_by") if ruling else None,
            "action_taken": ruling.get("action_taken") if ruling else None,
            "portal_synced": portal_ok,
            "alert_required": alert_required,
            "alert_delivered": alert_ok,
            "expected_projection_sha256": projection_sha,
            "expected_alert_sha256": alert_sha if alert_required else None,
            "complete": not blocked_by,
            "blocked_by": blocked_by}


def load_change_resolutions(repo_root: Path, kst_date: str, slot: str) -> dict[str, dict]:
    """Signed CIO judgements on post-delivery source changes, keyed by change.

    Whether a change moves an investment conclusion is exactly the question the
    unratified conclusion_diff spec cannot answer, so it is not inferred -- a
    person rules on it and signs, with the same key and the same out-of-band
    anchor that gates delivery approvals.  An unsigned file is not a ruling.
    """
    directory = slot_dir(repo_root, kst_date, slot)
    resolutions: dict[str, dict] = {}
    for path in sorted(directory.glob("post-delivery-resolution-rev-*.json")):
        body = _read_json(path, "FINALIZATION_RESOLUTION_UNREADABLE")
        verdict = body.get("capital_impact")
        resolved_by = body.get("resolved_by")
        change_key = body.get("post_delivery_change_key")
        action_taken = body.get("action_taken", "")
        if verdict not in CAPITAL_IMPACT_VERDICTS or not resolved_by or not change_key:
            continue
        if verdict == "PRESENT" and not str(action_taken).strip():
            # "It moves an investment conclusion" with no statement of what was
            # done is not a ruling anyone can audit.
            continue
        try:
            key = load_public_key(repo_root)
        except FinalizationError:
            continue
        message = change_resolution_message(
            briefing_id(kst_date, slot), change_key, verdict, resolved_by, action_taken,
            body.get("contract_version", CONTRACT_VERSION))
        try:
            signature = bytes.fromhex(str(body.get("signature", "")))
        except ValueError:
            continue
        if ed25519.verify(signature, message, key):
            resolutions[change_key] = {**body, "verified": True}
    return resolutions


def check_approval(repo_root: Path, directory: Path, draft: dict, validation: dict) -> dict:
    approval_file = _latest(directory, "approval")
    if approval_file is None:
        raise FinalizationError(
            "FINALIZATION_CIO_APPROVAL_REQUIRED",
            f"corrections present and auto-apply is unratified; "
            f"expected {directory}/approval-rev-NNN.json", EXIT_CIO_GATE)
    approval = _read_json(approval_file, "FINALIZATION_APPROVAL_UNREADABLE")
    if approval.get("decision") != "APPROVE":
        raise FinalizationError("FINALIZATION_CIO_APPROVAL_DENIED",
                                f"approval decision is {approval.get('decision')!r}", EXIT_CIO_GATE)
    if approval.get("approves_payload_sha256") != draft["delivery_payload_sha256"]:
        raise FinalizationError("FINALIZATION_APPROVAL_PAYLOAD_MISMATCH",
                                "approval is bound to a different payload hash", EXIT_CIO_GATE)
    if approval.get("approves_validation_rev") != validation["rev"]:
        raise FinalizationError("FINALIZATION_APPROVAL_VALIDATION_MISMATCH",
                                f"approval covers validation rev {approval.get('approves_validation_rev')}, "
                                f"current is {validation['rev']}", EXIT_CIO_GATE)
    approved_by = approval.get("approved_by")
    if not approved_by:
        raise FinalizationError("FINALIZATION_APPROVAL_UNATTRIBUTED",
                                "approval has no approved_by", EXIT_CIO_GATE)
    key = load_public_key(repo_root)
    message = approval_message(draft["briefing_id"], draft["delivery_payload_sha256"],
                               validation["rev"], approved_by, approval["decision"],
                               approval.get("contract_version", CONTRACT_VERSION))
    try:
        signature = bytes.fromhex(str(approval.get("signature", "")))
    except ValueError:
        signature = b""
    if not ed25519.verify(signature, message, key):
        raise FinalizationError(
            "FINALIZATION_APPROVAL_SIGNATURE_INVALID",
            "approval signature does not verify against the repo public key",
            EXIT_CIO_GATE)
    fingerprint = pubkey_fingerprint(key)
    _append_trust_log(repo_root, {
        "briefing_id": draft["briefing_id"], "approved_by": approved_by,
        "decision": approval["decision"], "pubkey_fingerprint": fingerprint,
        "anchored": True,
        "observed_at_utc": _iso(_utcnow())})
    return {**approval, "pubkey_fingerprint": fingerprint}


def _append_trust_log(repo_root: Path, entry: dict) -> None:
    """Append-only record of which key approved what.

    Detection, not prevention: if the key is ever swapped, this makes the
    change and everything approved under it visible after the fact.
    """
    path = repo_root / TRUST_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


# ------------------------------------------------------------------ adapters

class DeliveryProof(dict):
    """Evidence a transport accepted bytes, including WHICH bytes."""


class Adapter:
    name = "abstract"
    resend_safe = False
    verified_against_live_api = False
    #: FULL  -> transmits the sealed payload byte-for-byte
    #: SUMMARY -> transmits a derived, lossy message; cannot prove full delivery
    payload_fidelity = "FULL"

    def send(self, payload: bytes, meta: dict) -> DeliveryProof:
        raise NotImplementedError

    def probe(self, marker: str, meta: dict) -> bool | None:
        return None


class StepSummaryAdapter(Adapter):
    name = "github_step_summary"
    resend_safe = True
    verified_against_live_api = True
    payload_fidelity = "FULL"

    def send(self, payload: bytes, meta: dict) -> DeliveryProof:
        target = os.environ.get("GITHUB_STEP_SUMMARY")
        if not target:
            raise FinalizationError("DELIVERY_ADAPTER_UNAVAILABLE",
                                    "GITHUB_STEP_SUMMARY is unset", EXIT_TRANSPORT_FAILED)
        path = Path(target)
        before = path.stat().st_size if path.exists() else 0
        transmitted = f"## Atlas Daily Briefing — {meta['slot']} {meta['kst_date']}\n\n".encode() + payload + b"\n"
        with path.open("ab") as handle:
            handle.write(transmitted)
        after = path.stat().st_size
        if after <= before:
            raise FinalizationError("DELIVERY_TRANSPORT_UNVERIFIED",
                                    "step summary did not grow", EXIT_TRANSPORT_FAILED)
        return DeliveryProof(channel=self.name, transport_id=os.environ.get("GITHUB_RUN_ID", "unknown"),
                             sent_at_utc=_iso(_utcnow()), payload_fidelity=self.payload_fidelity,
                             transmitted_sha256=_sha256(transmitted),
                             transmitted_bytes=len(transmitted),
                             covers_full_payload=True)

    def probe(self, marker: str, meta: dict) -> bool | None:
        target = os.environ.get("GITHUB_STEP_SUMMARY")
        if not target or not Path(target).exists():
            return None
        return marker in Path(target).read_text(encoding="utf-8", errors="replace")


class KakaoMemoAdapter(Adapter):
    """Kakao "send to self" memo.

    (P0-9) The API caps message text, so this transport is structurally LOSSY.
    rev 3 truncated silently while the receipt hashed the full payload, which
    broke the "hash of the bytes delivered" contract the moment it went live.
    Here the marker is placed FIRST so truncation can never remove it, the
    proof records the hash of exactly what was transmitted, and the adapter is
    declared SUMMARY so it can never satisfy a full-delivery requirement.
    """

    name = "kakao"
    resend_safe = False
    payload_fidelity = "SUMMARY"
    ENDPOINT = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    TEXT_LIMIT = 900

    def build_message(self, payload: bytes, meta: dict) -> str:
        head = payload.decode("utf-8", errors="replace")
        marker = meta["delivery_marker"]
        body = head.replace(marker, "").strip()
        prefix = f"{marker}\n[Atlas {meta['slot']} {meta['kst_date']}]\n"
        room = self.TEXT_LIMIT - len(prefix) - 20
        if len(body) > room:
            body = body[:room] + "\n…(전문은 저장소 briefing.md)"
        return prefix + body

    def send(self, payload: bytes, meta: dict) -> DeliveryProof:
        token = os.environ.get("KAKAO_ACCESS_TOKEN")
        if not token:
            raise FinalizationError("DELIVERY_ADAPTER_UNAVAILABLE",
                                    "KAKAO_ACCESS_TOKEN is unset", EXIT_TRANSPORT_FAILED)
        text = self.build_message(payload, meta)
        template = json.dumps({"object_type": "text", "text": text,
                               "link": {"web_url": "", "mobile_web_url": ""}}, ensure_ascii=False)
        data = urllib.parse.urlencode({"template_object": template}).encode("utf-8")
        request = urllib.request.Request(
            self.ENDPOINT, data=data,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status, body = response.status, response.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 -- any transport error is fail-closed
            raise FinalizationError("DELIVERY_TRANSPORT_FAILED",
                                    f"kakao transport error: {exc}", EXIT_TRANSPORT_FAILED) from None
        if status != 200:
            raise FinalizationError("DELIVERY_TRANSPORT_FAILED",
                                    f"kakao returned HTTP {status}: {body}", EXIT_TRANSPORT_FAILED)
        transmitted = text.encode("utf-8")
        return DeliveryProof(channel=self.name, transport_id=f"http-{status}",
                             sent_at_utc=_iso(_utcnow()), payload_fidelity=self.payload_fidelity,
                             transmitted_sha256=_sha256(transmitted),
                             transmitted_bytes=len(transmitted),
                             covers_full_payload=False, response=body[:200])


class UnimplementedAdapter(Adapter):
    def __init__(self, name: str) -> None:
        self.name = name

    def send(self, payload: bytes, meta: dict) -> DeliveryProof:
        raise FinalizationError("DELIVERY_ADAPTER_UNAVAILABLE",
                                f"channel {self.name!r} has no implemented transport",
                                EXIT_TRANSPORT_FAILED)


ADAPTERS: dict[str, Adapter] = {a.name: a for a in (StepSummaryAdapter(), KakaoMemoAdapter())}
for _name in ("notion_cockpit", "push", "email"):
    ADAPTERS[_name] = UnimplementedAdapter(_name)


# ---------------------------------------------- durability, intent, progress

def git_durability_probe(repo_root: Path, path: Path) -> bool:
    """Is this file's exact content committed AND present on the remote branch?

    (P0-1) The delivery intent has to survive the runner, not just the process.
    A fresh checkout on a new runner sees only what was pushed.
    """
    try:
        rel = path.relative_to(repo_root).as_posix()
        local = subprocess.run(["git", "hash-object", str(path)], cwd=repo_root,
                               capture_output=True, text=True, check=True).stdout.strip()
        upstream = subprocess.run(["git", "rev-parse", "@{u}"], cwd=repo_root,
                                  capture_output=True, text=True, check=True).stdout.strip()
        committed = subprocess.run(["git", "rev-parse", f"{upstream}:{rel}"], cwd=repo_root,
                                   capture_output=True, text=True, check=True).stdout.strip()
        return bool(local) and local == committed
    except (subprocess.CalledProcessError, ValueError, OSError):
        return False


DurabilityProbe = Callable[[Path, Path], bool]


def intent_path(repo_root: Path, kst_date: str, slot: str) -> Path:
    return slot_dir(repo_root, kst_date, slot) / "delivery_intent.json"


def progress_path(repo_root: Path, kst_date: str, slot: str) -> Path:
    return slot_dir(repo_root, kst_date, slot) / "delivery_progress.json"


def receipt_path(repo_root: Path, kst_date: str, slot: str) -> Path:
    return slot_dir(repo_root, kst_date, slot) / "delivery_receipt.json"


def already_delivered(repo_root: Path, kst_date: str, slot: str) -> bool:
    return receipt_path(repo_root, kst_date, slot).exists()


def _load_progress(repo_root: Path, kst_date: str, slot: str, payload_sha: str | None = None) -> dict:
    """Progress is meaningful only for the payload it was recorded against.

    (P0) rev 4 carried draft-1 proofs forward onto draft-2, so a channel that
    had never seen the new payload counted as delivered and a receipt completed
    for bytes nobody sent.  A mismatch is quarantined, never reused.
    """
    path = progress_path(repo_root, kst_date, slot)
    if not path.exists():
        return {"channels": {}, "attempts": 0, "payload_sha256": payload_sha}
    progress = _read_json(path, "FINALIZATION_PROGRESS_UNREADABLE")
    if payload_sha is not None and progress.get("payload_sha256") != payload_sha:
        stale = progress.get("payload_sha256") or "unknown"
        _atomic_write(path.with_name(f"progress-superseded-{stale[:16]}.json"),
                      _canonical(progress) + b"\n")
        return {"channels": {}, "attempts": progress.get("attempts", 0),
                "payload_sha256": payload_sha, "superseded_from": stale}
    return progress


def write_intent(repo_root: Path, kst_date: str, slot: str, draft: dict,
                 channels: list[str], attempt: int, now: _dt.datetime) -> dict:
    intent = {
        "contract_version": CONTRACT_VERSION, "briefing_id": briefing_id(kst_date, slot),
        "payload_sha256": draft["delivery_payload_sha256"],
        "delivery_marker": draft["delivery_marker"], "channels": sorted(channels),
        "attempt": attempt, "intent_at_utc": _iso(now), "state": "OPEN",
    }
    _atomic_write(intent_path(repo_root, kst_date, slot), _canonical(intent) + b"\n")
    return intent


def _open_intent(repo_root: Path, kst_date: str, slot: str) -> dict | None:
    path = intent_path(repo_root, kst_date, slot)
    if not path.exists():
        return None
    intent = _read_json(path, "FINALIZATION_INTENT_UNREADABLE")
    return intent if intent.get("state") == "OPEN" else None


def reconcile(intent: dict, marker: str, meta: dict) -> dict:
    resolved, unresolved = {}, []
    for channel in intent.get("channels", []):
        adapter = ADAPTERS.get(channel)
        if adapter is None:
            unresolved.append({"channel": channel, "reason": "DELIVERY_ADAPTER_UNKNOWN"})
            continue
        probed = adapter.probe(marker, meta)
        if probed is True:
            resolved[channel] = "PROBED_DELIVERED"
        elif probed is False:
            resolved[channel] = "PROBED_NOT_DELIVERED"
        elif adapter.resend_safe:
            resolved[channel] = "RESEND_SAFE"
        else:
            unresolved.append({"channel": channel, "reason": "NO_PROBE_AND_RESEND_UNSAFE"})
    return {"resolved": resolved, "unresolved": unresolved}


def deliver(repo_root: Path, kst_date: str, slot: str, channels: list[str],
            required: list[str] | None = None, now: _dt.datetime | None = None,
            durability_probe: DurabilityProbe | None = None,
            intent_publisher: Callable[[Path, Path], None] | None = None) -> dict:
    """Deliver, gating the first irreversible send on a DURABLE intent.

    ``intent_publisher`` runs inside this process, between writing the intent
    and sending.  It must not be split across workflow steps: if the publish
    happens in an earlier step, the next process cannot tell "intent written,
    nothing sent yet" from "intent written, send may have happened", and every
    first attempt on an unprobeable channel would escalate for no reason.
    """
    now = now or _utcnow()
    required = sorted(set(required if required is not None else channels))
    probe_fn = durability_probe or git_durability_probe
    directory = slot_dir(repo_root, kst_date, slot)
    if receipt_path(repo_root, kst_date, slot).exists():
        raise FinalizationError("FINALIZATION_ALREADY_DELIVERED",
                                f"{briefing_id(kst_date, slot)} already delivered")

    draft_file = _latest(directory, "draft")
    if draft_file is None:
        raise FinalizationError("FINALIZATION_DRAFT_MISSING", f"no sealed draft for {kst_date}/{slot}")
    draft = _read_json(draft_file, "FINALIZATION_DRAFT_UNREADABLE")

    validation, problem = resolve_validation(directory)
    if problem is not None:
        # Both silence and malformed answers fail closed; malformed evidence is
        # surfaced separately so it cannot be mistaken for an ordinary wait.
        raise FinalizationError("FINALIZATION_VALIDATION_INVALID", problem, EXIT_VALIDATION_INVALID)
    if validation is None:
        policy = load_semantic_validator_policy(repo_root)
        sealed_at = _parse_iso(draft["sealed_at_utc"], "FINALIZATION_SEAL_TIME_UNREADABLE")
        elapsed_min = (now - sealed_at).total_seconds() / 60.0
        if not policy["expected"]:
            raise FinalizationError(
                "FINALIZATION_SEMANTIC_VALIDATOR_REQUIRED",
                "no semantic validator is configured; unvalidated bytes are never delivered",
                EXIT_VALIDATION_PENDING)
        if elapsed_min < policy["timeout_minutes"]:
            raise FinalizationError(
                "FINALIZATION_VALIDATION_PENDING",
                f"sealed {elapsed_min:.1f} min ago; a semantic validator is expected, "
                f"waiting until {policy['timeout_minutes']} min",
                EXIT_VALIDATION_PENDING)
        raise FinalizationError(
            "FINALIZATION_VALIDATION_TIMEOUT",
            f"semantic validator did not answer within {policy['timeout_minutes']} min; "
            "the slot remains sealed and undelivered until an explicit verdict arrives",
            EXIT_VALIDATION_PENDING)

    if validation["delivery_payload_sha256"] != draft["delivery_payload_sha256"]:
        raise FinalizationError("FINALIZATION_VALIDATION_STALE",
                                "latest validation does not cover the latest sealed payload")

    routing = validation.get("routing") or derive_routing(validation, load_ratified_specs(repo_root))
    if not routing["status_deliverable"]:
        raise FinalizationError(
            "FINALIZATION_HELD",
            f"validation_status={validation['validation_status']} never reaches a human; "
            "resolve the hold and publish a new verdict", EXIT_HELD)

    # The semantic verdict authorizes a projection candidate, not a user
    # delivery.  For the activated validation-first epoch, Portal must have
    # applied (or independently proven NO_CHANGE), its viewer bytes must match,
    # and its Notion receipt must have been read back before the final Notion
    # SSOT/user-delivery phase is allowed to start.
    verify_pre_delivery_portal_receipt(
        repo_root, kst_date, slot, draft=draft, validation=validation)

    approval = check_approval(repo_root, directory, draft, validation) if routing["cio_gate_required"] else None

    payload = _read_bytes(directory / f"payload-rev-{draft['rev']:03d}.md", "FINALIZATION_PAYLOAD_MISSING")
    if _sha256(payload) != draft["delivery_payload_sha256"]:
        raise FinalizationError("FINALIZATION_PAYLOAD_TAMPERED", "sealed payload bytes changed")

    meta = {"slot": slot, "kst_date": kst_date, "briefing_id": briefing_id(kst_date, slot),
            "delivery_marker": draft["delivery_marker"]}
    marker = draft["delivery_marker"]
    progress = _load_progress(repo_root, kst_date, slot, draft["delivery_payload_sha256"])
    done: dict[str, Any] = dict(progress.get("channels", {}))

    prior = _open_intent(repo_root, kst_date, slot)
    if prior is not None and prior.get("payload_sha256") != draft["delivery_payload_sha256"]:
        # An open intent for DIFFERENT bytes says a human may hold an older
        # version.  That is a reconciliation question, not something to inherit.
        raise FinalizationError(
            "FINALIZATION_INTENT_PAYLOAD_MISMATCH",
            f"an open delivery intent exists for payload {prior.get('payload_sha256')} but the "
            f"sealed payload is {draft['delivery_payload_sha256']}; resolve the earlier attempt "
            "before delivering different bytes",
            EXIT_RECONCILE_PENDING)
    reconciliation = None
    if prior is not None:
        reconciliation = reconcile(prior, marker, meta)
        if reconciliation["unresolved"]:
            raise FinalizationError(
                "FINALIZATION_RECONCILE_PENDING",
                f"a previous attempt left an open delivery intent and prior delivery cannot be "
                f"determined for {reconciliation['unresolved']}; human reconciliation required",
                EXIT_RECONCILE_PENDING)
        for channel, verdict in reconciliation["resolved"].items():
            if verdict == "PROBED_DELIVERED" and channel not in done:
                done[channel] = {"channel": channel, "recovered_by": "PROBE", "covers_full_payload": None}

    pending = [c for c in channels if c not in done]
    attempt = progress.get("attempts", 0) + 1

    if pending:
        # (P0-1) intent must be durable BEFORE any irreversible send
        write_intent(repo_root, kst_date, slot, draft, pending, attempt, now)
        if intent_publisher is not None:
            try:
                intent_publisher(repo_root, intent_path(repo_root, kst_date, slot))
            except Exception as exc:  # noqa: BLE001
                raise FinalizationError(
                    "FINALIZATION_INTENT_PUBLISH_FAILED",
                    f"could not publish the delivery intent ({exc}); refusing to transport",
                    EXIT_INTENT_NOT_DURABLE) from None
        if not probe_fn(repo_root, intent_path(repo_root, kst_date, slot)):
            raise FinalizationError(
                "FINALIZATION_INTENT_NOT_DURABLE",
                "delivery intent is not committed and pushed to the remote; refusing to "
                "transport because a fresh runner would not see that an attempt occurred",
                EXIT_INTENT_NOT_DURABLE)

    failures = []
    for channel in pending:
        adapter = ADAPTERS.get(channel)
        if adapter is None:
            failures.append({"channel": channel, "code": "DELIVERY_ADAPTER_UNKNOWN"})
            continue
        try:
            done[channel] = adapter.send(payload, meta)
        except FinalizationError as exc:
            failures.append({"channel": channel, "code": exc.code, "message": exc.message})

    progress = {"contract_version": CONTRACT_VERSION, "briefing_id": briefing_id(kst_date, slot),
                "payload_sha256": draft["delivery_payload_sha256"], "attempts": attempt,
                "channels": done, "last_failures": failures, "updated_at_utc": _iso(now)}
    _atomic_write(progress_path(repo_root, kst_date, slot), _canonical(progress) + b"\n")
    _atomic_write(intent_path(repo_root, kst_date, slot), _canonical(
        {"briefing_id": briefing_id(kst_date, slot), "attempt": attempt,
         "state": "CLOSED", "closed_at_utc": _iso(now)}) + b"\n")

    missing = [c for c in required if c not in done]
    if missing:
        # (P0-10) a receipt would permanently block the channels that still owe
        # a delivery, so none is written until every required channel is proven.
        raise FinalizationError(
            "DELIVERY_REQUIRED_CHANNEL_INCOMPLETE",
            f"required channels not yet delivered: {missing}; failures={failures}. "
            "progress recorded; retry will attempt only the missing channels",
            EXIT_TRANSPORT_FAILED)

    receipt = {
        "contract_version": CONTRACT_VERSION, "briefing_id": briefing_id(kst_date, slot),
        "slot": slot, "kst_date": kst_date,
        "sealed_payload_sha256": _sha256(payload), "delivery_marker": marker,
        "source_briefing_sha256": draft["source"]["briefing_sha256"],
        "source_revision": draft["source"]["revision"],
        "draft_rev": draft["rev"], "validation_rev": validation["rev"],
        "validation_status_at_delivery": validation["validation_status"],
        "cio_gate_required": routing["cio_gate_required"],
        "cio_approved_by": approval.get("approved_by") if approval else None,
        "cio_approval_pubkey_fingerprint": approval.get("pubkey_fingerprint") if approval else None,
        "delivered_at_utc": _iso(now), "attempts": attempt,
        "required_channels": required,
        "channels": sorted(done),
        "full_payload_channels": sorted(c for c, p in done.items() if p.get("covers_full_payload")),
        "delivery_proofs": [done[c] for c in sorted(done)],
        "reconciliation": reconciliation, "channel_failures": failures, "immutable": True,
    }
    _atomic_write(receipt_path(repo_root, kst_date, slot), _canonical(receipt) + b"\n")
    return receipt


# -------------------------------------------------- backlog / missed slots

def load_semantic_validator_policy(repo_root: Path) -> dict:
    """Load the fail-closed semantic validator policy.

    A timeout is an operational incident, not evidence that the briefing is
    correct.  Rev 18 therefore keeps the sealed slot pending after timeout and
    requires a later explicit semantic verdict before any Portal, Notion-final,
    or user-delivery step can proceed.
    """
    path = repo_root / SEMANTIC_VALIDATOR_PATH
    policy = {"expected": True, "timeout_minutes": VALIDATION_TIMEOUT_MIN,
              "timeout_action": "HOLD"}
    if path.exists():
        data = _read_json(path, "FINALIZATION_SEMANTIC_POLICY_UNREADABLE")
        policy["expected"] = bool(data.get("expected", True))
        policy["timeout_minutes"] = int(data.get("timeout_minutes", VALIDATION_TIMEOUT_MIN))
        policy["timeout_action"] = data.get("timeout_action", "HOLD")
    if not 1 <= policy["timeout_minutes"] <= 1440:
        raise FinalizationError("FINALIZATION_SEMANTIC_POLICY_INVALID",
                                "timeout_minutes must be between 1 and 1440")
    if policy["timeout_action"] != "HOLD":
        raise FinalizationError("FINALIZATION_SEMANTIC_POLICY_INVALID",
                                "timeout_action must remain HOLD")
    return policy


def load_activation(repo_root: Path) -> dict | None:
    """When finalization started being responsible for delivery.

    (P0) Without this, rolling rev 5 onto main made every slot in the lookback
    window "missing production" on day one -- the finalization artifacts could
    not exist before the feature did.  Absent file == not activated == nothing
    is owed, so a merge does not immediately turn the build red.
    """
    path = repo_root / ACTIVATION_PATH
    if not path.exists():
        return None
    data = _read_json(path, "FINALIZATION_ACTIVATION_UNREADABLE")
    if not data.get("active_from_kst_date"):
        return None
    return data


def _activation_applies(activation: dict | None, kst_date: str, slot: str) -> bool:
    cutoff = _activation_cutoff(activation)
    if cutoff is None:
        return False
    return (kst_date, SUPPORTED_SLOTS.index(slot)) >= (
        cutoff[0], SUPPORTED_SLOTS.index(cutoff[1]))


def portal_final_receipt_required(repo_root: Path, kst_date: str, slot: str) -> bool:
    activation = load_activation(repo_root)
    return bool(
        activation
        and activation.get("portal_before_delivery") is True
        and _activation_applies(activation, kst_date, slot)
    )


def _validate_portal_final_receipt(
    repo_root: Path, receipt: dict, *, kst_date: str, slot: str,
    draft: dict, validation: dict,
) -> None:
    if set(receipt) != PORTAL_FINAL_RECEIPT_FIELDS:
        raise FinalizationError("PORTAL_FINAL_RECEIPT_FIELDS_MISMATCH",
                                "Portal final receipt has an unexpected field set")
    suffix = SLOT_SUFFIX[slot]
    if (receipt.get("schema_version") != PORTAL_FINAL_RECEIPT_SCHEMA
            or receipt.get("briefing_id") != briefing_id(kst_date, slot)
            or receipt.get("kst_date") != kst_date
            or receipt.get("slot") != slot
            or receipt.get("delivery_payload_sha256") != draft.get("delivery_payload_sha256")
            or receipt.get("validation_rev") != validation.get("rev")):
        raise FinalizationError("PORTAL_FINAL_RECEIPT_IDENTITY_MISMATCH",
                                "Portal receipt is not bound to the governing draft and validation")
    projection_id = receipt.get("projection_id")
    if (not isinstance(projection_id, str)
            or not projection_id.startswith(f"{kst_date}-{'AM' if suffix == 'am' else 'PM'}-")):
        raise FinalizationError("PORTAL_FINAL_RECEIPT_PROJECTION_INVALID",
                                "projection_id does not match the briefing slot")
    for field in ("envelope_commit", "source_commit", "portal_source_sha"):
        if not isinstance(receipt.get(field), str) or FULL_SHA.fullmatch(receipt[field]) is None:
            raise FinalizationError("PORTAL_FINAL_RECEIPT_SHA_INVALID",
                                    f"{field} must be an exact 40-character SHA")
    for field in ("envelope_sha256", "delivery_payload_sha256"):
        if not isinstance(receipt.get(field), str) or SHA256.fullmatch(receipt[field]) is None:
            raise FinalizationError("PORTAL_FINAL_RECEIPT_HASH_INVALID",
                                    f"{field} must be an exact SHA-256")
    path = receipt.get("envelope_path")
    if (not isinstance(path, str) or path.startswith("/") or ".." in Path(path).parts
            or not path.startswith("evidence/validated_briefing_portal/")
            or not path.endswith("/portal-projection.json")):
        raise FinalizationError("PORTAL_FINAL_RECEIPT_PATH_INVALID",
                                "envelope_path is outside the immutable projection tree")
    envelope_path = repo_root / path
    envelope_bytes = _read_bytes(
        envelope_path, "PORTAL_FINAL_RECEIPT_ENVELOPE_MISSING")
    if _sha256(envelope_bytes) != receipt["envelope_sha256"]:
        raise FinalizationError("PORTAL_FINAL_RECEIPT_ENVELOPE_HASH_MISMATCH",
                                "the committed envelope bytes do not match the receipt")
    envelope = _read_json(
        envelope_path, "PORTAL_FINAL_RECEIPT_ENVELOPE_UNREADABLE")
    expected_slot = "AM" if suffix == "am" else "PM"
    if (envelope.get("schema_version") != "portal_projection/2"
            or envelope.get("projection_id") != projection_id
            or envelope.get("briefing_date") != kst_date
            or envelope.get("slot") != expected_slot
            or envelope.get("source_commit") != receipt["source_commit"]
            or envelope.get("completion_state") != "VALIDATED"
            or envelope.get("safety_attestation") != PORTAL_RECEIPT_AUTHORITY):
        raise FinalizationError("PORTAL_FINAL_RECEIPT_ENVELOPE_IDENTITY_MISMATCH",
                                "the envelope does not bind this validated briefing safely")

    # The receipt must point at immutable bytes already present on the source
    # repository's committed history. A working-tree-only file or a SHA that
    # names an unrelated commit is not a durable handoff to Portal.
    try:
        ancestor = subprocess.run(
            ["git", "-C", str(repo_root), "merge-base", "--is-ancestor",
             receipt["envelope_commit"], "HEAD"],
            capture_output=True, check=False)
        source_ancestor = subprocess.run(
            ["git", "-C", str(repo_root), "merge-base", "--is-ancestor",
             receipt["source_commit"], receipt["envelope_commit"]],
            capture_output=True, check=False)
        committed = subprocess.run(
            ["git", "-C", str(repo_root), "show",
             f"{receipt['envelope_commit']}:{path}"],
            capture_output=True, check=False)
    except OSError as exc:
        raise FinalizationError("PORTAL_FINAL_RECEIPT_GIT_UNAVAILABLE", type(exc).__name__) from exc
    if (ancestor.returncode != 0 or source_ancestor.returncode != 0
            or committed.returncode != 0):
        raise FinalizationError("PORTAL_FINAL_RECEIPT_COMMIT_UNVERIFIED",
                                "source/envelope lineage or committed envelope could not be verified")
    if committed.stdout != envelope_bytes:
        raise FinalizationError("PORTAL_FINAL_RECEIPT_COMMIT_BYTES_MISMATCH",
                                "envelope_commit does not contain the verified envelope bytes")
    if receipt.get("portal_result") not in PORTAL_RESULT:
        raise FinalizationError("PORTAL_FINAL_RECEIPT_RESULT_BLOCKED",
                                "Portal must be DEPLOYED or a viewer-verified NO_CHANGE")
    if not str(receipt.get("portal_run_id", "")).isdigit():
        raise FinalizationError("PORTAL_FINAL_RECEIPT_RUN_INVALID",
                                "portal_run_id must identify the verified target run")
    if (not isinstance(receipt.get("deployment_url"), str)
            or not receipt["deployment_url"].startswith("https://")):
        raise FinalizationError("PORTAL_FINAL_RECEIPT_URL_INVALID",
                                "deployment_url must be HTTPS")
    notion_id = str(receipt.get("notion_receipt_page_id", "")).replace("-", "")
    if re.fullmatch(r"[0-9a-f]{32}", notion_id) is None:
        raise FinalizationError("PORTAL_FINAL_RECEIPT_NOTION_ID_INVALID",
                                "Notion receipt page id is missing or malformed")
    if (receipt.get("viewer_readback_verified") is not True
            or receipt.get("notion_receipt_readback_verified") is not True):
        raise FinalizationError("PORTAL_FINAL_RECEIPT_READBACK_UNVERIFIED",
                                "Portal viewer and Notion receipt readbacks are both required")
    if receipt.get("authority") != PORTAL_RECEIPT_AUTHORITY:
        raise FinalizationError("PORTAL_FINAL_RECEIPT_AUTHORITY_FAILED",
                                "Portal receipt must preserve the all-false authority boundary")
    try:
        observed = _dt.datetime.fromisoformat(
            str(receipt.get("observed_at_utc", "")).replace("Z", "+00:00"))
    except ValueError:
        observed = None
    if observed is None or observed.tzinfo is None:
        raise FinalizationError("PORTAL_FINAL_RECEIPT_TIME_INVALID",
                                "observed_at_utc must be timezone-aware")


def verify_pre_delivery_portal_receipt(
    repo_root: Path, kst_date: str, slot: str, *, draft: dict, validation: dict,
) -> dict | None:
    if not portal_final_receipt_required(repo_root, kst_date, slot):
        return None
    directory = slot_dir(repo_root, kst_date, slot)
    path = _latest(directory, "portal-final-receipt")
    if path is None:
        raise FinalizationError(
            "PORTAL_FINAL_RECEIPT_MISSING",
            "semantic PASS exists, but Portal deployment/viewer/Notion receipt has not been proven",
            EXIT_VALIDATION_PENDING)
    receipt = _read_json(path, "PORTAL_FINAL_RECEIPT_UNREADABLE")
    _validate_portal_final_receipt(
        repo_root, receipt, kst_date=kst_date, slot=slot,
        draft=draft, validation=validation)
    return receipt


def record_portal_final_receipt(
    repo_root: Path, kst_date: str, slot: str, receipt: dict,
) -> dict:
    directory = slot_dir(repo_root, kst_date, slot)
    draft_path = _latest(directory, "draft")
    if draft_path is None:
        raise FinalizationError("FINALIZATION_DRAFT_MISSING",
                                f"no sealed draft for {kst_date}/{slot}")
    draft = _read_json(draft_path, "FINALIZATION_DRAFT_UNREADABLE")
    validation, problem = resolve_validation(directory)
    if problem is not None or validation is None:
        raise FinalizationError("PORTAL_FINAL_RECEIPT_VALIDATION_MISSING",
                                problem or "no governing semantic validation")
    routing = validation.get("routing") or derive_routing(
        validation, load_ratified_specs(repo_root))
    if not routing.get("status_deliverable"):
        raise FinalizationError("PORTAL_FINAL_RECEIPT_VALIDATION_HELD",
                                "a held briefing cannot acquire a final Portal receipt")
    _validate_portal_final_receipt(
        repo_root, receipt, kst_date=kst_date, slot=slot,
        draft=draft, validation=validation)
    prior = _latest(directory, "portal-final-receipt")
    body = _canonical(receipt) + b"\n"
    if prior is not None and prior.read_bytes() == body:
        return {"recorded": False, "reused": True,
                "path": str(prior.relative_to(repo_root))}
    rev = _next_rev(directory, "portal-final-receipt")
    path = directory / f"portal-final-receipt-rev-{rev:03d}.json"
    _atomic_write(path, body)
    return {"recorded": True, "reused": False,
            "path": str(path.relative_to(repo_root)), "rev": rev}


def _activation_cutoff(activation: dict | None) -> tuple[str, str] | None:
    if activation is None:
        return None
    return (activation["active_from_kst_date"],
            activation.get("active_from_slot", SUPPORTED_SLOTS[0]))


def expected_slots(now_utc: _dt.datetime, lookback_days: int = MISSED_SLOT_LOOKBACK_DAYS,
                   activation: dict | None = None) -> list[dict]:
    """Slots that should exist by now.

    (P0-7) rev 3's backlog only saw sealed-but-undelivered slots, so a day the
    scheduler never fired produced no draft and therefore looked like nothing
    was owed.  This enumerates what OUGHT to exist instead.

    Weekday-only.  The KRX/holiday calendar is not available to this module, so
    a public holiday yields a false positive rather than a silent miss; those
    are reported as `calendar_confidence: "WEEKDAY_ONLY_HOLIDAYS_UNKNOWN"`.
    """
    cutoff = _activation_cutoff(activation)
    if cutoff is None:
        # Not activated: finalization owes nothing for any slot.
        return []
    cutoff_date, cutoff_slot = cutoff
    now_kst = now_utc.astimezone(KST)
    out = []
    for back in range(lookback_days, -1, -1):
        day = (now_kst - _dt.timedelta(days=back)).date()
        if day.weekday() >= 5:
            continue
        for slot in SUPPORTED_SLOTS:
            hour, minute = SLOT_DUE_KST[slot]
            due = _dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)
            if now_kst < due + _dt.timedelta(minutes=SLOT_DUE_GRACE_MIN):
                continue
            iso = day.isoformat()
            if iso < cutoff_date:
                continue
            if iso == cutoff_date and SUPPORTED_SLOTS.index(slot) < SUPPORTED_SLOTS.index(cutoff_slot):
                continue
            out.append({"kst_date": iso, "slot": slot,
                        "due_kst": due.isoformat(),
                        "calendar_confidence": "WEEKDAY_ONLY_HOLIDAYS_UNKNOWN"})
    return out


def _all_sealed_slots(repo_root: Path) -> list[tuple[str, str]]:
    root = repo_root / FINALIZATION_ROOT
    if not root.exists():
        return []
    found = []
    for date_dir in sorted(root.iterdir()):
        if not date_dir.is_dir():
            continue
        for slot in SUPPORTED_SLOTS:
            if _latest(date_dir / slot, "draft") is not None:
                found.append((date_dir.name, slot))
    return found


def backlog(repo_root: Path, now: _dt.datetime | None = None) -> dict:
    now = now or _utcnow()
    activation = load_activation(repo_root)

    # (P0) A sealed briefing nobody received is debt FOREVER.  The lookback
    # window exists to decide whether a slot that produced nothing is late; it
    # must never be the reason an undelivered briefing stops being counted.
    # rev 6 aged debt out after five days and reported complete=true.
    pending = []
    for kst_date, slot in _all_sealed_slots(repo_root):
        directory = slot_dir(repo_root, kst_date, slot)
        if (directory / "delivery_receipt.json").exists():
            continue
        age_days = None
        try:
            sealed = _read_json(_latest(directory, "draft"), "FINALIZATION_DRAFT_UNREADABLE")
            age_days = round((now - _parse_iso(sealed["sealed_at_utc"],
                                               "FINALIZATION_SEAL_TIME_UNREADABLE")).days)
        except FinalizationError:
            pass
        pending.append({"kst_date": kst_date, "slot": slot,
                        "briefing_id": briefing_id(kst_date, slot),
                        "action": "INGEST_THEN_DELIVER", "age_days": age_days,
                        "intent_open": _open_intent(repo_root, kst_date, slot) is not None})

    changes = []
    policy = load_projection_policy(repo_root)
    root = repo_root / FINALIZATION_ROOT
    if root.exists():
        for date_dir in sorted(root.iterdir()):
            if not date_dir.is_dir():
                continue
            for slot in SUPPORTED_SLOTS:
                found = sorted((date_dir / slot).glob("post-delivery-change-rev-*.json")) \
                    if (date_dir / slot).is_dir() else []
                if found:
                    resolutions = load_change_resolutions(repo_root, date_dir.name, slot)
                    projections = load_projection_receipts(repo_root, date_dir.name, slot)
                    alerts = load_alert_receipts(repo_root, date_dir.name, slot)
                    for path in found:
                        body = _read_json(path, "FINALIZATION_CHANGE_UNREADABLE")
                        change_key = body.get("post_delivery_change_key")
                        status = change_completion(
                            briefing_id(date_dir.name, slot), body,
                            resolutions.get(change_key), projections.get(change_key),
                            alerts.get(change_key), policy)
                        changes.append({
                            "briefing_id": briefing_id(date_dir.name, slot),
                            "rev": body.get("rev"),
                            "post_delivery_change_key": change_key,
                            "changed_axes": [a["axis"] for a in body.get("changed_axes", [])],
                            "redelivery": "FORBIDDEN", **status})

    sealed_keys = {(d, s) for d, s in _all_sealed_slots(repo_root)}
    missing = [{**item, "briefing_id": briefing_id(item["kst_date"], item["slot"]),
                "action": "RUN_PRODUCER", "reason": "NO_SEALED_DRAFT_PAST_DUE"}
               for item in expected_slots(now, activation=activation)
               if (item["kst_date"], item["slot"]) not in sealed_keys]

    return {"pending_delivery": pending, "missing_production": missing,
            "post_delivery_changes": changes,
            "projection_policy": policy,
            "activated": activation is not None,
            "active_from": _activation_cutoff(activation),
            "generated_at_utc": _iso(now)}


def git_intent_publisher(repo_root: Path, path: Path) -> None:
    """Commit and push exactly the intent file, nothing else."""
    rel = path.relative_to(repo_root).as_posix()
    subprocess.run(["git", "add", "--", rel], cwd=repo_root, check=True, capture_output=True)
    status = subprocess.run(["git", "diff", "--cached", "--quiet", "--", rel],
                            cwd=repo_root, capture_output=True)
    if status.returncode != 0:
        subprocess.run(["git", "commit", "-m", f"delivery intent {rel}"],
                       cwd=repo_root, check=True, capture_output=True)
    upstream = subprocess.run(["git", "rev-parse", "--abbrev-ref", "@{u}"], cwd=repo_root,
                              capture_output=True, text=True, check=True).stdout.strip()
    remote, branch = upstream.split("/", 1)
    subprocess.run(["git", "push", remote, f"HEAD:{branch}"], cwd=repo_root,
                   check=True, capture_output=True)


def drain(repo_root: Path, channels: list[str], required: list[str] | None = None,
          now: _dt.datetime | None = None,
          durability_probe: DurabilityProbe | None = None,
          intent_publisher: Callable[[Path, Path], None] | None = None) -> dict:
    """Ingest any published verdict, then deliver -- per item, in that order.

    (P0-6) rev 3 ingested the inbox only on a normal run and then had drain call
    deliver directly, so a verdict published after the first run was never read
    and the slot timed out anyway.
    """
    now = now or _utcnow()
    results = []
    state = backlog(repo_root, now)
    for item in state["pending_delivery"]:
        entry = {"briefing_id": item["briefing_id"], "slot": item["slot"], "kst_date": item["kst_date"]}
        try:
            entry["ingest"] = ingest_inbox(repo_root, item["kst_date"], item["slot"])
        except FinalizationError as exc:
            entry["ingest"] = {"ingested": False, "error": exc.code, "message": exc.message}
        try:
            entry["delivered"] = True
            entry["receipt"] = deliver(repo_root, item["kst_date"], item["slot"], channels,
                                       required=required, now=now,
                                       durability_probe=durability_probe,
                                       intent_publisher=intent_publisher)
        except FinalizationError as exc:
            entry["delivered"] = False
            entry["error"] = exc.code
            entry["message"] = exc.message
            entry["exit_code"] = exc.exit_code
        results.append(entry)
    #  (P0) A due slot with no receipt is NOT a green outcome, whatever the
    #  reason.  rev 4 returned pending/HOLD/reconcile inside a successful result
    #  and the workflow went green while nobody had been briefed.
    machine_failure_codes = {
        "FINALIZATION_HELD", "FINALIZATION_RECONCILE_PENDING",
        "FINALIZATION_INTENT_NOT_DURABLE", "FINALIZATION_INTENT_PUBLISH_FAILED",
        "FINALIZATION_INTENT_PAYLOAD_MISMATCH", "FINALIZATION_CIO_APPROVAL_REQUIRED",
        "FINALIZATION_APPROVAL_SIGNATURE_INVALID", "FINALIZATION_APPROVAL_PUBKEY_MISSING",
        "FINALIZATION_APPROVAL_PUBKEY_UNTRUSTED", "FINALIZATION_APPROVAL_ANCHOR_MISSING",
        "DELIVERY_REQUIRED_CHANNEL_INCOMPLETE", "DELIVERY_NO_CHANNEL_SUCCEEDED",
        "FINALIZATION_VALIDATION_STALE", "FINALIZATION_VALIDATION_INVALID",
    }
    undelivered = [e for e in results if not e.get("delivered")]
    age_by_id = {i["briefing_id"]: i.get("age_days") for i in state["pending_delivery"]}
    debt = [{"briefing_id": e["briefing_id"], "age_days": age_by_id.get(e["briefing_id"]),
             "error": e.get("error")} for e in undelivered]
    failures = [e for e in undelivered if e.get("error") in machine_failure_codes]
    observed_pending = [e for e in undelivered if e.get("error") == "FINALIZATION_VALIDATION_PENDING"]
    unresolved_changes = [c for c in state["post_delivery_changes"] if not c["complete"]]
    outcome = {
        "drained": results,
        "activated": state["activated"],
        "semantic_validator_expected": load_semantic_validator_policy(repo_root)["expected"],
        "active_from": state["active_from"],
        "missing_production": state["missing_production"],
        "post_delivery_changes": state["post_delivery_changes"],
        # (P0) "we do not know whether this moved the investment conclusion" is
        # not a finished state. rev 14 warned and went green, so a change that
        # might matter could pass unnoticed -- and it will matter more once a
        # real user channel exists. Not knowing IS the escalation condition.
        "cio_attention_required": unresolved_changes,
        "pending_delivery_debt": debt,
        "undelivered_count": len(undelivered),
        "machine_failures": [{"briefing_id": e["briefing_id"], "error": e["error"]} for e in failures],
        "observed_pending": [{"briefing_id": e["briefing_id"], "error": e["error"]} for e in observed_pending],
        # Green requires: every due slot delivered AND nothing owed production.
        "complete": not undelivered and not state["missing_production"] and not unresolved_changes,
        "exit_code": (EXIT_OK if not (undelivered or state["missing_production"] or unresolved_changes)
                      else EXIT_CIO_ATTENTION_REQUIRED if (unresolved_changes and not undelivered
                                                           and not state["missing_production"])
                      else EXIT_DRAIN_INCOMPLETE),
    }
    return outcome


# ---------------------------------------------------- post-delivery ledger

def _ledger_path(repo_root: Path, kst_date: str, slot: str) -> Path:
    return slot_dir(repo_root, kst_date, slot) / "corrections.jsonl"


def _read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def append_correction(repo_root: Path, kst_date: str, slot: str, correction: dict,
                      portal_synced: bool = False) -> dict:
    """Append a correction.

    ``portal_synced`` is written False and stays False in the ledger: whether
    the Portal actually holds the correction is answered by a projection
    receipt, not by a flag a caller sets.  rev 15 exposed ``--portal-synced``
    on the CLI, which let anyone assert a write that never happened.
    """
    ledger = _ledger_path(repo_root, kst_date, slot)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    entry = {"briefing_id": briefing_id(kst_date, slot), "seq": len(_read_ledger(ledger)) + 1,
             "applied_at_utc": _iso(_utcnow()), "class": correction.get("class"),
             "field_path": correction.get("field_path"), "before": correction.get("before"),
             "after": correction.get("after"), "source": correction.get("source"),
             "portal_synced": bool(portal_synced), "redelivered": False}
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def correction_notice(repo_root: Path, kst_date: str, slot: str,
                      mark_surfaced: bool = False) -> str | None:
    """One line for the NEXT briefing -- and only the next one.

    (P1) rev 3 had no watermark, so the same sentence came back forever.  Only
    entries above the surfaced watermark are reported; marking advances it.
    """
    entries = _read_ledger(_ledger_path(repo_root, kst_date, slot))
    if not entries:
        return None
    watermark_path = slot_dir(repo_root, kst_date, slot) / "corrections_surfaced.json"
    watermark = _read_json(watermark_path, "FINALIZATION_WATERMARK_UNREADABLE")["seq"] \
        if watermark_path.exists() else 0
    fresh = [e for e in entries if e.get("seq", 0) > watermark]
    if not fresh:
        return None
    total = len(fresh)
    synced = sum(1 for e in fresh if e.get("portal_synced") is True)
    portal = ("Portal 반영 완료" if synced == total
              else "Portal 미반영" if synced == 0 else f"Portal {synced}/{total} 반영")
    if mark_surfaced:
        _atomic_write(watermark_path, _canonical(
            {"seq": max(e["seq"] for e in fresh), "surfaced_at_utc": _iso(_utcnow())}) + b"\n")
    return f"※ 이전 회차({briefing_id(kst_date, slot)}) 사후 정정 {total}건 — {portal} · 재발송 없음."


# ----------------------------------------------------------------------- CLI

def _emit(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=dict))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atlas briefing finalization gate")
    parser.add_argument("command", choices=["seal", "ingest", "validate", "portal-receipt",
                                            "deliver", "drain", "backlog", "correct",
                                            "notice", "status"])
    parser.add_argument("--slot", choices=list(SUPPORTED_SLOTS))
    parser.add_argument("--decision-date")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--consume-output")
    parser.add_argument("--input")
    parser.add_argument("--channel", action="append", default=[])
    parser.add_argument("--required-channel", action="append", default=[])
    parser.add_argument("--mark-surfaced", action="store_true")
    parser.add_argument("--allow-nondurable-intent", action="store_true",
                        help="TESTING ONLY: skip the durable-intent gate")
    parser.add_argument("--decision", default="APPROVE", choices=["APPROVE", "DENY"])
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    date, slot = args.decision_date, args.slot
    per_slot = {"seal", "ingest", "validate", "portal-receipt", "deliver", "correct",
                "notice", "status"}
    if args.command in per_slot and not (slot and date):
        parser.error("--slot and --decision-date are required for this command")

    channels = args.channel or ["github_step_summary"]
    required = args.required_channel or None
    probe = (lambda *_: True) if args.allow_nondurable_intent else None
    publisher = None if args.allow_nondurable_intent else git_intent_publisher

    try:
        if args.command == "seal":
            _emit(seal(repo_root, date, slot,
                       Path(args.consume_output) if args.consume_output else None))
        elif args.command == "ingest":
            _emit(ingest_inbox(repo_root, date, slot))
        elif args.command == "validate":
            _emit(record_validation(repo_root, date, slot,
                                    _read_json(Path(args.input), "FINALIZATION_VALIDATION_UNREADABLE")))
        elif args.command == "portal-receipt":
            _emit(record_portal_final_receipt(
                repo_root, date, slot,
                _read_json(Path(args.input), "PORTAL_FINAL_RECEIPT_UNREADABLE")))
        elif args.command == "deliver":
            if already_delivered(repo_root, date, slot):
                _emit({"briefing_id": briefing_id(date, slot), "delivered": False,
                       "reason": "FINALIZATION_ALREADY_DELIVERED"})
                return EXIT_OK
            _emit(deliver(repo_root, date, slot, channels, required=required,
                          durability_probe=probe, intent_publisher=publisher))
        elif args.command == "drain":
            outcome = drain(repo_root, channels, required=required, durability_probe=probe,
                            intent_publisher=publisher)
            _emit(outcome)
            return outcome["exit_code"]
        elif args.command == "backlog":
            _emit(backlog(repo_root))
        elif args.command == "correct":
            _emit(append_correction(repo_root, date, slot,
                                    _read_json(Path(args.input), "FINALIZATION_CORRECTION_UNREADABLE")))
        elif args.command == "notice":
            _emit({"notice": correction_notice(repo_root, date, slot, mark_surfaced=args.mark_surfaced)})
        elif args.command == "status":
            directory = slot_dir(repo_root, date, slot)
            _emit({
                "briefing_id": briefing_id(date, slot),
                "sealed": _latest(directory, "draft") is not None,
                "validated": _latest(directory, "validation") is not None,
                "approved": _latest(directory, "approval") is not None,
                "delivered": already_delivered(repo_root, date, slot),
                "intent_open": _open_intent(repo_root, date, slot) is not None,
                "progress": _load_progress(repo_root, date, slot).get("channels", {}),
                "validation_timeout_min": VALIDATION_TIMEOUT_MIN,
                "semantic_validator_policy": load_semantic_validator_policy(repo_root),
                "auto_apply_enabled": not PHASE_A_AUTO_APPLY_DISABLED,
                "auto_apply_blocked_by": "PHASE_A_AUTO_APPLY_DISABLED",
                "ratified_conclusion_specs": load_ratified_specs(repo_root),
                "activation": load_activation(repo_root),
                "approval_pubkey_anchored": bool(os.environ.get(APPROVAL_FINGERPRINT_ENV, "").strip()),
                "channels": {name: {"implemented": not isinstance(a, UnimplementedAdapter),
                                    "verified_against_live_api": a.verified_against_live_api,
                                    "resend_safe": a.resend_safe,
                                    "payload_fidelity": a.payload_fidelity}
                             for name, a in sorted(ADAPTERS.items())},
            })
    except FinalizationError as exc:
        print(json.dumps({"error": exc.code, "message": exc.message}, ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
