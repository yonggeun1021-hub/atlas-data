"""Build a non-publishing GAM membership preview from explicit taxonomy refs.

This is the missing construction step before the existing source-binding
validator. A structural preview is never permission to populate a live master.
The disjoint source registries remain unresolved; no source label is inferred.

``apply_theme_ingestion_preview()`` publishes a revalidated preview onto one
caller-designated existing destination file. It is a library capability with no
CLI, scheduler or operational caller, and it does not raise any authority flag.
"""
from __future__ import annotations

import contextlib
import copy
import json
from pathlib import Path
import sys

try:  # advisory locking is POSIX; preview-only callers must still import.
    import fcntl
except ImportError:  # pragma: no cover - unsupported platform
    fcntl = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from universe import global_asset_master as GAM

AssetMasterError = GAM.AssetMasterError
SCHEMA = "global_asset_master_theme_ingestion_preview/1"
MASTER_IDENTITY_FIELDS = ("master_id", "as_of_date", "payload_sha256")
REQUEST_FIELDS = set(GAM.BINDING_REFERENCE_FIELDS) | {"gam_source_identity"}
SOURCE_FIELDS = {
    "source_id", "source_url", "source_sha256", "available_at", "retrieved_at_utc"
}
DERIVED_RECORD_FIELDS = {
    "active_aliases", "active_memberships", "universe_approved",
    "investable_eligible", "stage_transition",
}


def _requests(values):
    if not isinstance(values, list) or not values:
        raise AssetMasterError("INGESTION_REQUESTS_EMPTY_OR_INVALID")
    result, seen = [], set()
    for index, value in enumerate(values):
        if not isinstance(value, dict) or set(value) != REQUEST_FIELDS:
            raise AssetMasterError(f"INGESTION_REQUEST_FIELDS_MISMATCH:{index}")
        reference = GAM._theme_binding_reference(
            {key: value[key] for key in GAM.BINDING_REFERENCE_FIELDS}, index
        )
        source = value["gam_source_identity"]
        if not isinstance(source, dict) or set(source) != SOURCE_FIELDS:
            raise AssetMasterError(f"INGESTION_SOURCE_FIELDS_MISMATCH:{index}")
        # One target per call. Multiple sources/roles require an explicit
        # upstream selection, not last-writer-wins or silent deduplication.
        key = (reference["asset_id"], reference["gam_membership_id"])
        if key in seen:
            raise AssetMasterError(f"INGESTION_TARGET_DUPLICATE:{key}")
        seen.add(key)
        result.append({**reference, "gam_source_identity": copy.deepcopy(source)})
    return sorted(result, key=lambda row: tuple(row[k] for k in GAM.BINDING_REFERENCE_FIELDS))


def _master_input(packet):
    return {
        "schema_version": GAM.INPUT_SCHEMA_VERSION,
        "master_id": packet["master_id"],
        "as_of_date": packet["as_of_date"],
        "records": [
            {k: copy.deepcopy(v) for k, v in row.items() if k not in DERIVED_RECORD_FIELDS}
            for row in packet["records"]
        ],
    }


def build_theme_ingestion_preview(
    master_source, taxonomy_source, requests, *, trusted_commit,
    authority_registry_path=None,
):
    """Construct explicit memberships in memory, then verify both originals.

    ``gam_source_identity`` is a caller proposal, not a ratified conversion of
    the taxonomy's retrieval-channel label. The original taxonomy identity,
    every evidence row and role, and the existing unresolved comparison are
    retained. No files, configurations, registries or original objects change.
    """
    if not isinstance(trusted_commit, str) or GAM.TRUSTED_COMMIT_RE.fullmatch(trusted_commit) is None:
        raise AssetMasterError("BINDING_TRUSTED_COMMIT_INVALID")
    selections = _requests(requests)
    contract = GAM.load_contract()
    master = GAM._validated_master(copy.deepcopy(master_source), contract)
    TT = GAM._load_theme_taxonomy()
    registry = {} if authority_registry_path is None else {
        "authority_registry_path": Path(authority_registry_path)
    }
    try:
        taxonomy = TT.build_packet(
            copy.deepcopy(taxonomy_source), trusted_commit=trusted_commit, **registry
        )
    except TT.ThemeTaxonomyError as exc:
        raise AssetMasterError(f"TAXONOMY_SOURCE_INVALID:{exc}") from exc

    proposed = _master_input(master)
    records = {row["asset_id"]: row for row in proposed["records"]}
    members = {row["membership_id"]: row for row in taxonomy["memberships"]}
    references, retained, additions, unchanged = [], [], 0, 0
    for selection in selections:
        reference = {key: selection[key] for key in GAM.BINDING_REFERENCE_FIELDS}
        references.append(reference)
        record = records.get(selection["asset_id"])
        member = members.get(selection["taxonomy_membership_id"])
        if record is None:
            raise AssetMasterError(f"GAM_ASSET_NOT_FOUND:{selection['asset_id']}")
        if member is None:
            raise AssetMasterError(f"TAXONOMY_MEMBERSHIP_NOT_FOUND:{selection['taxonomy_membership_id']}")
        # The existing binding validator will reject asset/market/theme/source
        # mismatches; these candidates are never returned as an accepted master.
        row = {
            "membership_type": "THEME",
            "membership_id": selection["gam_membership_id"],
            "valid_from": member["valid_from"],
            "valid_to": member["valid_to"],
            "source_identity": copy.deepcopy(selection["gam_source_identity"]),
        }
        if row in record["memberships"]:
            unchanged += 1
        else:
            record["memberships"].append(row)
            additions += 1
        retained.append(copy.deepcopy(member))

    # Includes all legacy alias/membership/cross-record collision checks.
    candidate = GAM.build_master(proposed, contract)
    binding = GAM.validate_theme_source_binding(
        candidate, copy.deepcopy(taxonomy_source), references,
        trusted_commit=trusted_commit, **registry,
    )
    failures = sorted({
        reason for row in binding["bindings"] for reason in row["failure_reasons"]
    })
    blocked = bool(failures)
    authority = copy.deepcopy(master["authority"])
    authority["master_population_authorized"] = False
    payload = {
        "schema_version": SCHEMA,
        "status": "BLOCKED" if blocked else "STRUCTURAL_PREVIEW",
        "change": None if blocked else ("APPEND" if additions else "NO_CHANGE"),
        "addition_count": 0 if blocked else additions,
        "unchanged_count": 0 if blocked else unchanged,
        "candidate_master": None if blocked else candidate,
        "binding_report": binding,
        "requests": selections,
        "taxonomy_memberships": retained,
        "input_digests": {
            "master_source_sha256": GAM.payload_sha256(master_source),
            "taxonomy_source_sha256": GAM.payload_sha256(taxonomy_source),
            "requests_sha256": GAM.payload_sha256(selections),
            "original_master_payload_sha256": master["payload_sha256"],
            "taxonomy_payload_sha256": taxonomy["payload_sha256"],
            "trusted_commit": trusted_commit,
        },
        "failure_reasons": failures,
        "authority": authority,
        "unresolved_boundaries": copy.deepcopy(binding["unresolved_boundaries"]),
    }
    payload["payload_sha256"] = GAM.payload_sha256(payload)
    return payload


def validate_theme_ingestion_preview(
    preview, master_source, taxonomy_source, requests, *, trusted_commit,
    authority_registry_path=None,
):
    """Recompute the candidate and binding; a self-rehashed edit is not proof."""
    expected = build_theme_ingestion_preview(
        master_source, taxonomy_source, requests, trusted_commit=trusted_commit,
        authority_registry_path=authority_registry_path,
    )
    if GAM.canonical_json(preview) != GAM.canonical_json(expected):
        raise AssetMasterError("INGESTION_PREVIEW_DERIVATION_MISMATCH")
    return copy.deepcopy(expected)


def _lock_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.gam-apply.lock"


@contextlib.contextmanager
def _destination_apply_lock(destination: Path):
    """Hold the cooperative exclusive boundary for one destination path.

    Every caller of :func:`apply_theme_ingestion_preview` shares this protocol,
    and it only excludes such callers: an unrelated writer that does not take
    the same lock is not blocked, so this is never claimed as a compare-and-swap
    against arbitrary writers. The lock is a separate zero-byte sidecar next to
    an already existing destination, never the destination inode itself, because
    the atomic publish replaces that inode and a lock on a replaced inode does
    not exclude anything. It carries no master data, is never read as state and
    is not an absence marker; it is not removed, so its identity stays stable.
    """
    if fcntl is None:  # pragma: no cover - unsupported platform
        raise AssetMasterError("APPLICATION_LOCK_UNSUPPORTED_PLATFORM")
    path = _lock_path(destination)
    try:
        handle = open(path, "a+b")
    except OSError as exc:
        raise AssetMasterError(f"APPLICATION_LOCK_UNAVAILABLE:{path}:{exc}") from exc
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise AssetMasterError(f"APPLICATION_LOCK_FAILED:{path}:{exc}") from exc
        try:
            yield path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _master_identity(packet):
    return {field: packet[field] for field in MASTER_IDENTITY_FIELDS}


def _destination_master(destination: Path, contract):
    """Re-derive the existing destination bytes through the real validator."""
    try:
        existing = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetMasterError(f"APPLICATION_DESTINATION_UNREADABLE:{destination}:{exc}") from exc
    try:
        return GAM.validate_packet(existing, contract)
    except AssetMasterError as exc:
        raise AssetMasterError(f"APPLICATION_DESTINATION_INVALID:{destination}:{exc}") from exc


def apply_theme_ingestion_preview(
    preview, master_source, taxonomy_source, requests, *, trusted_commit,
    authority_registry_path=None, destination_path,
    expected_previous_master_sha256, operational_application_approved,
):
    """Publish one revalidated preview onto one existing master destination.

    ``operational_application_approved`` must be exactly ``True``. It is a
    trusted library caller boundary only: it does not substitute Theme authority
    or PIT validation, does not attest to itself, and does not change the
    preview's ``master_population_authorized=false`` output. No CLI, scheduler,
    web or live caller is wired to this function.

    Everything is recomputed from the supplied originals at ``trusted_commit``:
    a caller boolean never skips original, digest or authority validation. Only
    an existing normal master file is supported; a missing target fails closed
    without creating a destination, a parent directory, an absence marker or an
    inferred canonical path. The destination read, previous-digest check,
    original-master identity check, original revalidation and publish all happen
    inside one cooperative per-destination exclusion boundary.

    A stale preview conflicts. ``NO_CHANGE`` is only an explicitly rebuilt
    preview whose original is the current destination; no rebase, retry or
    implicit acceptance of an old preview exists here.

    Failures before the final ``os.replace`` inside the reused
    :func:`global_asset_master.write_json_atomic` leave the destination bytes
    exactly as they were. After that replace the publication may already have
    happened, so a failure raised at or after that point is not a promise that
    the old bytes survived.

    Returns a minimal in-memory outcome with the before/after master identity.
    Nothing is persisted besides the destination packet itself.
    """
    if operational_application_approved is not True:
        raise AssetMasterError("APPLICATION_APPROVAL_NOT_EXACTLY_TRUE")
    if destination_path is None or (
        isinstance(destination_path, str) and not destination_path.strip()
    ):
        raise AssetMasterError("APPLICATION_DESTINATION_PATH_REQUIRED")
    if (
        not isinstance(expected_previous_master_sha256, str)
        or GAM.SHA256_RE.fullmatch(expected_previous_master_sha256) is None
    ):
        raise AssetMasterError("APPLICATION_EXPECTED_PREVIOUS_SHA256_INVALID")
    destination = Path(destination_path)
    # Resolve existing aliases once so every cooperating caller locks, reads,
    # and replaces the same target rather than replacing a supplied symlink.
    try:
        destination = destination.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AssetMasterError(
            f"APPLICATION_DESTINATION_NOT_AN_EXISTING_FILE:{destination}"
        ) from exc
    # Checked before anything is opened or created; the same check is repeated
    # authoritatively inside the boundary below.
    if not destination.is_file():
        raise AssetMasterError(f"APPLICATION_DESTINATION_NOT_AN_EXISTING_FILE:{destination}")

    contract = GAM.load_contract()
    with _destination_apply_lock(destination):
        if not destination.is_file():
            raise AssetMasterError(f"APPLICATION_DESTINATION_NOT_AN_EXISTING_FILE:{destination}")
        previous = _destination_master(destination, contract)
        if previous["payload_sha256"] != expected_previous_master_sha256:
            raise AssetMasterError(
                "APPLICATION_EXPECTED_PREVIOUS_MASTER_MISMATCH:"
                f"{expected_previous_master_sha256}:{previous['payload_sha256']}"
            )
        # The supplied original is re-derived, not read from the preview. The
        # identity fields are redundant under an intact digest and are compared
        # anyway so each one fails closed on its own.
        original = GAM._validated_master(copy.deepcopy(master_source), contract)
        for field in MASTER_IDENTITY_FIELDS:
            if original[field] != previous[field]:
                raise AssetMasterError(
                    f"APPLICATION_ORIGINAL_MASTER_MISMATCH:{field}:"
                    f"{original[field]}:{previous[field]}"
                )

        preview = validate_theme_ingestion_preview(
            preview, master_source, taxonomy_source, requests,
            trusted_commit=trusted_commit,
            authority_registry_path=authority_registry_path,
        )
        lineage = preview["input_digests"]["original_master_payload_sha256"]
        if lineage != previous["payload_sha256"]:
            raise AssetMasterError(
                f"APPLICATION_PREVIEW_LINEAGE_MISMATCH:{lineage}:{previous['payload_sha256']}"
            )
        binding_status = preview["binding_report"]["status"]
        if (
            preview["status"] != "STRUCTURAL_PREVIEW"
            or preview["failure_reasons"]
            or preview["candidate_master"] is None
            or binding_status != "THEME_SOURCE_BINDING_VERIFIED"
        ):
            raise AssetMasterError(
                f"APPLICATION_PREVIEW_NOT_APPLICABLE:{preview['status']}:{binding_status}"
            )
        candidate = preview["candidate_master"]
        change = preview["change"]
        if change == "NO_CHANGE":
            # Only an explicitly rebuilt preview reaches here, so the candidate
            # must already be the destination packet byte for byte.
            if GAM.canonical_json(candidate) != GAM.canonical_json(previous):
                raise AssetMasterError("APPLICATION_NO_CHANGE_NOT_IDENTICAL")
            applied, outcome = previous, "APPLIED_NO_CHANGE"
        elif change == "APPEND":
            GAM.write_json_atomic(destination, candidate)
            applied, outcome = candidate, "APPLIED_APPEND"
        else:
            raise AssetMasterError(f"APPLICATION_CHANGE_UNSUPPORTED:{change}")

    return {
        "outcome": outcome,
        "change": change,
        "destination_path": str(destination),
        "addition_count": preview["addition_count"],
        "unchanged_count": preview["unchanged_count"],
        "previous_master": _master_identity(previous),
        "master": _master_identity(applied),
        "published": outcome == "APPLIED_APPEND",
    }
