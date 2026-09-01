#!/usr/bin/env python3
"""P3-12 Upbit KRW tradeable-universe / PAPER-eligibility classifier.

Per-market state machine over Upbit's own public KRW market data only:

    OBSERVATION_POOL -> TRADEABLE_UNIVERSE -> PAPER_ELIGIBLE
                      \\-> BLOCKED (identity collision / evidence tamper)

``state`` is a *classification*, never an authority grant. Every output
row's ``authority`` block is hardcoded all-``false`` regardless of state --
turning a classification into real investable/PAPER/order authority is a
separate, later, explicitly-ratified change that this module cannot make.

Three independently ratified inputs must all be effective for the evaluated
vintage before any market can leave ``OBSERVATION_POOL``:

1. ``tradeable_universe_policy`` and ``taxonomy`` are both ``RATIFIED`` and
   their document-level effective date is not later than ``evaluation_as_of``.
2. The specific market's canonical identity mapping appears in the exact,
   evidence-bound ``config/upbit_asset_identity_registry.json`` mapping and
   that registry is effective for ``evaluation_as_of``.
3. The specific market's canonical identity mapping appears in
   ``ratified_identity_registry`` (``{upbit_market: canonical_asset_id}``).
   The production population script derives this argument only through the
   fail-closed registry loader below; callers cannot promote a ticker match.

The v1 ratification is effective 2026-08-30. Earlier proposed taxonomy and
identity evidence retain their original dates and are never backfilled into
an earlier classification.

A trading-suspended / investment-warning market (Upbit's own
``market_event.warning``) is force-excluded from ``TRADEABLE_UNIVERSE`` /
``PAPER_ELIGIBLE`` unconditionally in code -- this is not a policy toggle a
future config edit could weaken.

Kraken breadth/leadership membership is READ-ONLY, OBSERVATIONAL labeling
here (``kraken_cross_exchange_reference``). See the
``# SAFETY INVARIANT`` comment on ``build_classification`` below: that
value is never read anywhere else in this module's gating logic.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_CAPTURE_SPEC = importlib.util.spec_from_file_location(
    "upbit_market_capture_for_universe",
    ROOT / ".github" / "scripts" / "upbit_market_capture.py",
)
UPBIT_CAPTURE = importlib.util.module_from_spec(_CAPTURE_SPEC)
assert _CAPTURE_SPEC.loader is not None
_CAPTURE_SPEC.loader.exec_module(UPBIT_CAPTURE)

# P3-12-GOV-05: runtime exact-approval binding. ``approval_status ==
# "RATIFIED"`` alone is never sufficient for the identity registry or
# taxonomy -- see ``_approval_effective()`` below and
# ``governance/upbit_exact_release_binding.py``'s module docstring.
_EXACT_RELEASE_BINDING_SPEC = importlib.util.spec_from_file_location(
    "upbit_tradeable_universe_exact_release_binding",
    ROOT / "governance" / "upbit_exact_release_binding.py",
)
EXACT_RELEASE_BINDING = importlib.util.module_from_spec(_EXACT_RELEASE_BINDING_SPEC)
assert _EXACT_RELEASE_BINDING_SPEC.loader is not None
_EXACT_RELEASE_BINDING_SPEC.loader.exec_module(EXACT_RELEASE_BINDING)


CONTRACT_PATH = ROOT / "config" / "upbit_tradeable_universe_contract.json"
CAPTURE_CONTRACT_PATH = ROOT / "config" / "upbit_market_capture_contract.json"
POLICY_PATH = ROOT / "config" / "upbit_tradeable_universe_policy.json"
TAXONOMY_PATH = ROOT / "config" / "upbit_exclusion_taxonomy.json"
IDENTITY_REGISTRY_PATH = ROOT / "config" / "upbit_asset_identity_registry.json"
OUTPUT_SCHEMA_VERSION = "upbit_tradeable_universe_packet/1"
POPULATION_RECORD_SCHEMA_VERSION = "upbit_universe_population/1"
TRANSITION_SCHEMA_VERSION = "upbit_universe_same_vintage_transition/1"
POPULATION_DATA_ROOT = ROOT / "data" / "observations" / "upbit_tradeable_universe"
TRANSITION_RELEASE_BUILDER_PATH = ROOT / "identity" / "upbit_exact_release_binding_release.py"

STATE_OBSERVATION_POOL = "OBSERVATION_POOL"
STATE_TRADEABLE_UNIVERSE = "TRADEABLE_UNIVERSE"
STATE_PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
STATE_BLOCKED = "BLOCKED"

_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# P3-12's declared scope is Upbit KRW spot only ("Upbit KRW 현물 시장 식별").
# GET /v1/market/all returns every quote currency Upbit lists (KRW-*, BTC-*,
# USDT-*) in one response; BTC-/USDT-quoted pairs are out of scope and must
# never reach classification or identity review. Matches
# identity/upbit_market_identity_proposal.py's own _MARKET_RE.
_KRW_MARKET_RE = re.compile(r"^KRW-[A-Z0-9]{2,20}$")

# Hardcoded, not policy-driven: never read, set, or made overridable by any
# config value in this module.
_ROW_AUTHORITY = {
    "investable_eligible": False,
    "paper_eligible": False,
    "stage_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "order_authorized": False,
}


class UpbitUniverseError(ValueError):
    """Fail-closed P3-12 classifier/contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpbitUniverseError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _file_sha(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise UpbitUniverseError(f"FILE_HASH_FAILED:{path}:{exc}") from exc


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(Path(path))
    if not isinstance(value, dict) or value.get("contract_version") != "upbit_tradeable_universe_classifier/1":
        raise UpbitUniverseError("CONTRACT_FIELD_MISMATCH:contract_version")
    if value.get("kraken_cross_reference_policy", {}).get("may_affect_state") is not False:
        raise UpbitUniverseError("CONTRACT_FIELD_MISMATCH:kraken_cross_reference_policy.may_affect_state")
    for key, expected in value.get("authority", {}).items():
        if expected is not False:
            raise UpbitUniverseError(f"CONTRACT_AUTHORITY_NOT_FALSE:{key}")
    return copy.deepcopy(value)


def load_policy(path: Path = POLICY_PATH) -> dict:
    doc = _read_json(Path(path))
    required = {
        "approval_status", "min_listing_history_finalized_days", "turnover_lookback_finalized_days",
        "min_30d_avg_krw_turnover", "max_spread_bps", "max_estimated_paper_slippage_bps",
        "paper_slippage_estimate_notional_krw", "max_capture_age_hours",
    }
    if not isinstance(doc, dict) or not required.issubset(doc):
        raise UpbitUniverseError("POLICY_FIELDS_INVALID")
    if doc.get("approval_status") == "RATIFIED":
        if not _DATE_RE.fullmatch(str(doc.get("effective_date", ""))):
            raise UpbitUniverseError("POLICY_EFFECTIVE_DATE_INVALID")
        if not _UTC_RE.fullmatch(str(doc.get("ratified_at_utc", ""))):
            raise UpbitUniverseError("POLICY_RATIFIED_AT_INVALID")
        if doc.get("paper_only") is not True:
            raise UpbitUniverseError("POLICY_SCOPE_NOT_PAPER_ONLY")
        for key in (
            "exchange_authorized", "order_authorized", "paper_exit_authorized",
            "production_authorized", "real_capital_authorized", "trading_authorized",
        ):
            if doc.get(key) is not False:
                raise UpbitUniverseError(f"POLICY_AUTHORITY_NOT_FALSE:{key}")
    return doc


def load_taxonomy(path: Path = TAXONOMY_PATH) -> dict:
    doc = _read_json(Path(path))
    required = {"approval_status", "eligible_category", "excluded_categories", "unknown_asset_policy", "records"}
    if not isinstance(doc, dict) or not required.issubset(doc):
        raise UpbitUniverseError("TAXONOMY_FIELDS_INVALID")
    if doc.get("unknown_asset_policy") != "fail_closed_unknown":
        raise UpbitUniverseError("TAXONOMY_UNKNOWN_POLICY_NOT_FAIL_CLOSED")
    if doc.get("approval_status") in {"RATIFIED", "PENDING_GOVERNANCE_RESOLUTION"}:
        if not _DATE_RE.fullmatch(str(doc.get("effective_from", ""))):
            raise UpbitUniverseError("TAXONOMY_EFFECTIVE_FROM_INVALID")
        if not _UTC_RE.fullmatch(str(doc.get("ratified_at_utc", ""))):
            raise UpbitUniverseError("TAXONOMY_RATIFIED_AT_INVALID")
    return doc


def _resolve_repo_path(relative_path: str, label: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path or relative_path.startswith("/"):
        raise UpbitUniverseError(f"{label}_PATH_INVALID")
    candidate = (ROOT / relative_path).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise UpbitUniverseError(f"{label}_PATH_OUTSIDE_REPOSITORY") from exc
    return candidate


def load_identity_registry(path: Path = IDENTITY_REGISTRY_PATH) -> dict:
    """Load the retained mapping and revalidate its exact evidence pins.

    The source candidate packet deliberately remains a historical
    ``PROPOSED_UNRATIFIED`` observation. A governance freeze preserves the
    55-row mapping for audit while ``approval_status`` is
    ``PENDING_GOVERNANCE_RESOLUTION``; ``effective_identity_mapping`` then
    returns an empty mapping, so no consumer can promote it.
    """
    doc = _read_json(Path(path))
    required = {
        "registry_version", "approval_status", "effective_from", "ratified_at_utc",
        "source_candidate_packet", "source_identity_evidence", "unknown_market_policy",
        "mappings", "authority",
    }
    if not isinstance(doc, dict) or not required.issubset(doc):
        raise UpbitUniverseError("IDENTITY_REGISTRY_FIELDS_INVALID")
    if doc.get("registry_version") != "upbit_asset_identity_registry/v1":
        raise UpbitUniverseError("IDENTITY_REGISTRY_VERSION_INVALID")
    if doc.get("approval_status") not in {"RATIFIED", "PENDING_GOVERNANCE_RESOLUTION"}:
        raise UpbitUniverseError("IDENTITY_REGISTRY_STATUS_INVALID")
    if not _DATE_RE.fullmatch(str(doc.get("effective_from", ""))):
        raise UpbitUniverseError("IDENTITY_REGISTRY_EFFECTIVE_FROM_INVALID")
    if not _UTC_RE.fullmatch(str(doc.get("ratified_at_utc", ""))):
        raise UpbitUniverseError("IDENTITY_REGISTRY_RATIFIED_AT_INVALID")
    if doc.get("unknown_market_policy") != "fail_closed_unratified_identity":
        raise UpbitUniverseError("IDENTITY_REGISTRY_UNKNOWN_POLICY_INVALID")
    if not isinstance(doc.get("authority"), dict) or not doc["authority"]:
        raise UpbitUniverseError("IDENTITY_REGISTRY_AUTHORITY_INVALID")
    for key, value in doc["authority"].items():
        if value is not False:
            raise UpbitUniverseError(f"IDENTITY_REGISTRY_AUTHORITY_NOT_FALSE:{key}")

    source = doc["source_candidate_packet"]
    evidence_source = doc["source_identity_evidence"]
    if not isinstance(source, dict) or not isinstance(evidence_source, dict):
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_INVALID")
    packet_path = _resolve_repo_path(source.get("path"), "IDENTITY_REGISTRY_SOURCE_PACKET")
    evidence_path = _resolve_repo_path(evidence_source.get("path"), "IDENTITY_REGISTRY_SOURCE_EVIDENCE")
    if _file_sha(packet_path) != source.get("file_sha256"):
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_PACKET_FILE_HASH_MISMATCH")
    if _file_sha(evidence_path) != evidence_source.get("file_sha256"):
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_EVIDENCE_HASH_MISMATCH")

    packet = _read_json(packet_path)
    stored_payload_hash = packet.get("payload_sha256")
    if stored_payload_hash != source.get("payload_sha256"):
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_PACKET_PAYLOAD_PIN_MISMATCH")
    if payload_sha256({key: value for key, value in packet.items() if key != "payload_sha256"}) != stored_payload_hash:
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_PACKET_SELF_HASH_MISMATCH")
    if packet.get("review_status") != source.get("review_status"):
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_REVIEW_STATUS_MISMATCH")
    if packet.get("snapshot_date") != source.get("snapshot_date"):
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_SNAPSHOT_DATE_MISMATCH")
    if packet.get("evaluation_as_of") != source.get("evaluation_as_of"):
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_EVALUATION_DATE_MISMATCH")

    mappings = doc.get("mappings")
    if not isinstance(mappings, dict) or not mappings:
        raise UpbitUniverseError("IDENTITY_REGISTRY_MAPPINGS_INVALID")
    if sorted(mappings) != list(mappings):
        raise UpbitUniverseError("IDENTITY_REGISTRY_MAPPINGS_NOT_SORTED")
    if any(not _KRW_MARKET_RE.fullmatch(str(market)) for market in mappings):
        raise UpbitUniverseError("IDENTITY_REGISTRY_MARKET_INVALID")
    if any(not isinstance(asset, str) or not asset for asset in mappings.values()):
        raise UpbitUniverseError("IDENTITY_REGISTRY_CANONICAL_ID_INVALID")
    if len(set(mappings.values())) != len(mappings):
        raise UpbitUniverseError("IDENTITY_REGISTRY_DUPLICATE_CANONICAL_TARGET")
    source_mappings = {
        row["market"]: row["canonical_asset_id"]
        for row in packet.get("registry_candidates", [])
    }
    if source_mappings != mappings:
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_MAPPING_MISMATCH")
    held_markets = {row.get("market") for row in packet.get("hold_list", [])}
    if held_markets & set(mappings):
        raise UpbitUniverseError("IDENTITY_REGISTRY_HELD_MARKET_PROMOTED")
    return copy.deepcopy(doc)


def _policy_approval_effective(document: dict, evaluation_as_of: str, *, date_field: str) -> bool:
    """Plain date-gated ``approval_status`` check. Used ONLY for
    ``policy`` (``config/upbit_tradeable_universe_policy.json``), which
    P3-12-GOV-05 does not touch -- this function has no exact-hash binding
    at all and must never be used for the identity registry or taxonomy.
    See ``_identity_taxonomy_exact_bound_effective`` below, which is the
    ONLY function those two document kinds may go through; there is no
    shared function or boolean flag a new identity/taxonomy consumer could
    accidentally call into this weaker path with.
    """
    if document.get("approval_status") != "RATIFIED":
        return False
    effective = document.get(date_field)
    # Synthetic fixtures predating effective-dated ratification remain valid
    # for unit-level classifier tests. Real loaded RATIFIED documents are
    # required by their loaders above to carry the field.
    return effective is None or (isinstance(effective, str) and effective <= evaluation_as_of)


def _identity_taxonomy_exact_bound_effective(
    document: dict, evaluation_as_of: str, *, date_field: str, content_field: str,
    repo_root: Path | None = None,
) -> bool:
    """The ONLY approval-effectiveness check for the identity registry
    (``content_field="mappings"``) or taxonomy (``content_field="records"``).
    Unlike ``_policy_approval_effective``, there is no boolean toggle here
    to accidentally omit -- every call always runs the full exact-hash
    binding first. ``document.approval_status == "RATIFIED"`` is necessary
    but never sufficient by itself: the document's own content AND
    currently-running code must both independently resolve through
    ``governance/upbit_exact_release_binding.py``'s two one-way chains
    (content chain + code chain) before the date check even runs. This is
    P3-12-GOV-04's P1 finding: editing ``approval_status`` back to
    ``"RATIFIED"`` on a document whose content was never re-approved must
    never revive authority on its own.
    """
    try:
        validation_kwargs = {}
        if repo_root is not None:
            validation_kwargs["repo_root"] = Path(repo_root)
        if not EXACT_RELEASE_BINDING.validate_exact_release(
            document, content_field=content_field, evaluation_as_of=evaluation_as_of,
            **validation_kwargs,
        ):
            return False
    except EXACT_RELEASE_BINDING.ExactReleaseBindingError:
        return False
    if document.get("approval_status") != "RATIFIED":
        return False
    effective = document.get(date_field)
    return effective is None or (isinstance(effective, str) and effective <= evaluation_as_of)


def effective_identity_mapping(
    registry: dict,
    evaluation_as_of: str,
    *,
    repo_root: Path | None = None,
) -> dict:
    if not _identity_taxonomy_exact_bound_effective(
        registry, evaluation_as_of, date_field="effective_from", content_field="mappings",
        repo_root=repo_root,
    ):
        return {}
    return copy.deepcopy(registry["mappings"])


# ---------------------------------------------------------------------------
# Append-only same-vintage population transition consumer
# ---------------------------------------------------------------------------

def _transition_repo_path(relative_path, *, repo_root: Path, label: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise UpbitUniverseError(f"{label}_PATH_INVALID")
    relative = Path(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise UpbitUniverseError(f"{label}_PATH_INVALID")
    root = Path(repo_root).absolute()
    candidate = root / relative
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise UpbitUniverseError(f"{label}_PATH_OUTSIDE_REPOSITORY") from exc
    current = root
    if current.is_symlink():
        raise UpbitUniverseError(f"{label}_SYMLINK_FORBIDDEN")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise UpbitUniverseError(f"{label}_SYMLINK_FORBIDDEN")
    return candidate


def _transition_assert_manifest_lexical_path(manifest_path: Path, *, repo_root: Path) -> None:
    root = Path(repo_root).absolute()
    candidate = Path(manifest_path).absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise UpbitUniverseError("TRANSITION_MANIFEST_PATH_OUTSIDE_REPOSITORY") from exc
    current = root
    if current.is_symlink():
        raise UpbitUniverseError("TRANSITION_MANIFEST_SYMLINK_FORBIDDEN")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise UpbitUniverseError("TRANSITION_MANIFEST_SYMLINK_FORBIDDEN")


def _transition_self_hash(value: dict, label: str) -> str:
    declared = value.get("payload_sha256")
    actual = payload_sha256({key: item for key, item in value.items() if key != "payload_sha256"})
    if not isinstance(declared, str) or declared != actual:
        raise UpbitUniverseError(f"{label}_PAYLOAD_SHA256_MISMATCH")
    return actual


def _transition_parse_utc(value, label: str) -> dt.datetime:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise UpbitUniverseError(label)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise UpbitUniverseError(label) from exc


def _transition_read_record_pin(
    pin: dict,
    *,
    repo_root: Path,
    label: str,
    retained_path: Path | None = None,
) -> tuple[Path, dict, str, str]:
    if not isinstance(pin, dict) or set(pin) != {"path", "file_sha256", "payload_sha256"}:
        raise UpbitUniverseError(f"{label}_PIN_INVALID")
    if retained_path is None:
        path = _transition_repo_path(pin.get("path"), repo_root=repo_root, label=label)
    else:
        path = Path(retained_path).absolute()
        _transition_assert_manifest_lexical_path(path, repo_root=repo_root)
    if path.is_symlink() or not path.is_file():
        raise UpbitUniverseError(f"{label}_FILE_HASH_MISMATCH")
    try:
        record_bytes = path.read_bytes()
        record = json.loads(record_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpbitUniverseError(f"{label}_READ_FAILED:{exc}") from exc
    record_file_hash = hashlib.sha256(record_bytes).hexdigest()
    if record_file_hash != pin.get("file_sha256"):
        raise UpbitUniverseError(f"{label}_FILE_HASH_MISMATCH")
    if not isinstance(record, dict):
        raise UpbitUniverseError(f"{label}_ROOT_INVALID")
    record_hash = _transition_self_hash(record, label)
    if record_hash != pin.get("payload_sha256"):
        raise UpbitUniverseError(f"{label}_PAYLOAD_PIN_MISMATCH")
    return path, record, record_hash, record_file_hash


def _transition_authority_closed(record: dict, *, allow_observation_pool_marker: bool = False) -> bool:
    packet = record.get("packet") or {}
    packet_authority = packet.get("authority") or {}
    record_authority = record.get("authority") or {}
    rows = packet.get("markets") or []
    expected_record_keys = {
        "observation_pool_population_only",
        "identity_ratification_authorized",
        "taxonomy_ratification_authorized",
        "policy_ratification_authorized",
        "tradeable_universe_promotion_authorized",
        "paper_eligible_promotion_authorized",
        "production_authorized",
        "trading_authorized",
        "order_authorized",
    }
    record_closed = (
        set(record_authority) == expected_record_keys
        and record_authority.get("observation_pool_population_only") is allow_observation_pool_marker
        and all(
            item is False
            for key, item in record_authority.items()
            if key != "observation_pool_population_only"
        )
    )
    return (
        packet_authority == _ROW_AUTHORITY
        and record_closed
        and bool(rows)
        and all(
            isinstance(row, dict)
            and row.get("authority") == _ROW_AUTHORITY
            for row in rows
        )
    )


def rebuild_same_vintage_population_record(
    source_record: dict,
    *,
    evaluation_as_of: str,
    repo_root: Path = ROOT,
) -> dict:
    """Deterministically rebuild the only permissible successor from live bytes.

    The raw snapshot and pre-ratification identity review remain fixed by the
    canonical source. Policy, taxonomy, and identity mapping are loaded from
    their live, exact-code-approved files. Callers cannot supply any successor
    row, reason, summary, metadata, or ratification field to this primitive.
    """
    if not isinstance(source_record, dict):
        raise UpbitUniverseError("TRANSITION_SOURCE_RECORD_ROOT_INVALID")
    if not isinstance(evaluation_as_of, str) or not _DATE_RE.fullmatch(evaluation_as_of):
        raise UpbitUniverseError("TRANSITION_EVALUATION_AS_OF_INVALID")
    expected_raw_relative = f"evidence/crypto/upbit/raw/{evaluation_as_of}"
    raw_path = _transition_repo_path(
        expected_raw_relative,
        repo_root=repo_root,
        label="TRANSITION_RAW_SNAPSHOT",
    )
    capture_contract_path = _transition_repo_path(
        "config/upbit_market_capture_contract.json",
        repo_root=repo_root,
        label="TRANSITION_CAPTURE_CONTRACT",
    )
    try:
        capture_contract = UPBIT_CAPTURE.load_contract(capture_contract_path)
        core = load_snapshot_core(raw_path, capture_contract)
    except UPBIT_CAPTURE.CaptureError as exc:
        raise UpbitUniverseError(f"TRANSITION_RAW_REBUILD_FAILED:{exc}") from exc
    expected_raw_snapshot = {
        "path": expected_raw_relative,
        "manifest_sha256": core["manifest_sha256"],
    }
    if (
        source_record.get("schema_version") != POPULATION_RECORD_SCHEMA_VERSION
        or source_record.get("snapshot_date") != evaluation_as_of
        or source_record.get("generated_at") != core["available_at"]
        or source_record.get("raw_snapshot") != expected_raw_snapshot
        or source_record.get("builder") != {
            "module": "universe/upbit_tradeable_universe.py",
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
        }
    ):
        raise UpbitUniverseError("TRANSITION_SOURCE_RAW_OR_BUILDER_PIN_MISMATCH")
    identity_review = source_record.get("identity_review")
    if not isinstance(identity_review, dict):
        raise UpbitUniverseError("TRANSITION_SOURCE_IDENTITY_REVIEW_INVALID")
    blocked_markets = identity_review.get("blocked_markets") or []
    if not isinstance(blocked_markets, list) or any(not isinstance(item, str) for item in blocked_markets):
        raise UpbitUniverseError("TRANSITION_SOURCE_BLOCKED_MARKETS_INVALID")

    policy_path = _transition_repo_path(
        "config/upbit_tradeable_universe_policy.json",
        repo_root=repo_root,
        label="TRANSITION_POLICY",
    )
    taxonomy_path = _transition_repo_path(
        "config/upbit_exclusion_taxonomy.json",
        repo_root=repo_root,
        label="TRANSITION_TAXONOMY",
    )
    registry_path = _transition_repo_path(
        "config/upbit_asset_identity_registry.json",
        repo_root=repo_root,
        label="TRANSITION_REGISTRY",
    )
    policy = load_policy(policy_path)
    taxonomy = load_taxonomy(taxonomy_path)
    registry = _read_json(registry_path)
    effective_registry = effective_identity_mapping(
        registry,
        evaluation_as_of,
        repo_root=repo_root,
    )
    packet = build_classification(
        core,
        evaluation_as_of=evaluation_as_of,
        policy=policy,
        taxonomy=taxonomy,
        ratified_identity_registry=effective_registry,
        blocked_markets=set(blocked_markets),
        repo_root=repo_root,
    )
    record = {
        "schema_version": POPULATION_RECORD_SCHEMA_VERSION,
        "snapshot_date": evaluation_as_of,
        "generated_at": core["available_at"],
        "raw_snapshot": expected_raw_snapshot,
        "builder": {
            "module": "universe/upbit_tradeable_universe.py",
            "output_schema_version": packet["schema_version"],
        },
        "ratification": {
            "effective_for_snapshot": bool(
                packet["policy_ratified"] and packet["taxonomy_ratified"] and effective_registry
            ),
            "policy": {
                "path": "config/upbit_tradeable_universe_policy.json",
                "file_sha256": _file_sha(policy_path),
                "effective_from": policy.get("effective_date"),
            },
            "taxonomy": {
                "path": "config/upbit_exclusion_taxonomy.json",
                "file_sha256": _file_sha(taxonomy_path),
                "effective_from": taxonomy.get("effective_from"),
            },
            "identity_registry": {
                "path": "config/upbit_asset_identity_registry.json",
                "file_sha256": _file_sha(registry_path),
                "registry_version": registry.get("registry_version"),
                "effective_from": registry.get("effective_from"),
                "mapping_count": len(effective_registry),
            },
        },
        "identity_review": copy.deepcopy(identity_review),
        "authority": {
            "observation_pool_population_only": not bool(effective_registry),
            "identity_ratification_authorized": False,
            "taxonomy_ratification_authorized": False,
            "policy_ratification_authorized": False,
            "tradeable_universe_promotion_authorized": False,
            "paper_eligible_promotion_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "order_authorized": False,
        },
        "packet": packet,
    }
    record["payload_sha256"] = payload_sha256(record)
    return record


def validate_same_vintage_transition(
    manifest_path: Path,
    *,
    repo_root: Path = ROOT,
    _retained_source_path: Path | None = None,
    _retained_successor_path: Path | None = None,
) -> dict:
    """Independently validate one immutable population transition.

    This function is deliberately inside the exact-code-approved universe
    consumer.  The manifest must pin this file and the exact-code-approved
    release builder, and the live registry/taxonomy must still pass the full
    content+code chain before the successor becomes selectable.
    """
    repo_root = Path(repo_root)
    manifest_path = Path(manifest_path)
    retained_mode = _retained_source_path is not None or _retained_successor_path is not None
    if retained_mode and (_retained_source_path is None or _retained_successor_path is None):
        raise UpbitUniverseError("TRANSITION_RETAINED_BUNDLE_INCOMPLETE")
    _transition_assert_manifest_lexical_path(manifest_path, repo_root=repo_root)
    if (
        manifest_path.is_symlink()
        or manifest_path.parent.is_symlink()
        or manifest_path.name != "transition.json"
    ):
        raise UpbitUniverseError("TRANSITION_MANIFEST_PATH_INVALID")
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpbitUniverseError(f"TRANSITION_MANIFEST_READ_FAILED:{exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != TRANSITION_SCHEMA_VERSION:
        raise UpbitUniverseError("TRANSITION_SCHEMA_VERSION_MISMATCH")
    manifest_hash = _transition_self_hash(manifest, "TRANSITION_MANIFEST")
    snapshot_date = manifest.get("snapshot_date")
    if not isinstance(snapshot_date, str) or not _DATE_RE.fullmatch(snapshot_date):
        raise UpbitUniverseError("TRANSITION_SNAPSHOT_DATE_INVALID")
    if manifest.get("evaluation_as_of") != snapshot_date:
        raise UpbitUniverseError("TRANSITION_EVALUATION_AS_OF_MISMATCH")

    contract = EXACT_RELEASE_BINDING.load_policy_contract(
        _transition_repo_path(
            "config/upbit_exact_release_binding_policy_contract.json",
            repo_root=repo_root,
            label="TRANSITION_POLICY_CONTRACT",
        )
    )
    authority = manifest.get("authority")
    if authority != contract.get("authority") or not authority or any(item is not False for item in authority.values()):
        raise UpbitUniverseError("TRANSITION_AUTHORITY_NOT_FALSE")

    expected_code_files = {
        "builder": str(TRANSITION_RELEASE_BUILDER_PATH.relative_to(ROOT)),
        "consumer": str(Path(__file__).resolve().relative_to(ROOT)),
    }
    for label, expected_relative in expected_code_files.items():
        pin = manifest.get(label)
        if not isinstance(pin, dict) or set(pin) != {"path", "file_sha256"}:
            raise UpbitUniverseError(f"TRANSITION_{label.upper()}_PIN_INVALID")
        if pin.get("path") != expected_relative:
            raise UpbitUniverseError(f"TRANSITION_{label.upper()}_PATH_MISMATCH")
        code_path = _transition_repo_path(
            expected_relative, repo_root=repo_root, label=f"TRANSITION_{label.upper()}",
        )
        if code_path.is_symlink() or not code_path.is_file() or _file_sha(code_path) != pin.get("file_sha256"):
            raise UpbitUniverseError(f"TRANSITION_{label.upper()}_FILE_HASH_MISMATCH")

    source_path, source, source_hash, source_file_hash = _transition_read_record_pin(
        manifest.get("source_record"),
        repo_root=repo_root,
        label="TRANSITION_SOURCE_RECORD",
        retained_path=_retained_source_path,
    )
    successor_path, successor, successor_hash, _successor_file_hash = _transition_read_record_pin(
        manifest.get("successor_record"),
        repo_root=repo_root,
        label="TRANSITION_SUCCESSOR_RECORD",
        retained_path=_retained_successor_path,
    )
    expected_source = f"data/observations/upbit_tradeable_universe/{snapshot_date}/packet.json"
    if manifest["source_record"]["path"] != expected_source:
        raise UpbitUniverseError("TRANSITION_SOURCE_NOT_CANONICAL_SAME_VINTAGE")
    successor_relative = manifest["successor_record"]["path"]
    expected_successor_prefix = (
        f"data/observations/upbit_tradeable_universe/{snapshot_date}/transitions/"
    )
    if (
        not successor_relative.startswith(expected_successor_prefix)
        or not successor_relative.endswith("/packet.json")
    ):
        raise UpbitUniverseError("TRANSITION_SUCCESSOR_PATH_INVALID")
    expected_manifest_path = (repo_root / successor_relative).parent / "transition.json"
    expected_date_root = repo_root / "data" / "observations" / "upbit_tradeable_universe" / snapshot_date
    expected_transitions_root = expected_date_root / "transitions"
    if retained_mode:
        bundle_root = manifest_path.parent
        if (
            manifest_path != bundle_root / "transition.json"
            or source_path != bundle_root / "canonical-source.json"
            or successor_path != bundle_root / "successor.json"
        ):
            raise UpbitUniverseError("TRANSITION_RETAINED_BUNDLE_LAYOUT_INVALID")
    elif (
        expected_date_root.is_symlink()
        or expected_transitions_root.is_symlink()
        or expected_manifest_path.parent.is_symlink()
        or manifest_path.resolve() != expected_manifest_path.resolve()
    ):
        raise UpbitUniverseError("TRANSITION_MANIFEST_LOCATION_MISMATCH")
    if (
        Path(successor_relative).parent.name != f"{source_hash}-to-{successor_hash}"
        or (
            not retained_mode
            and (
                manifest_path.resolve().parent != successor_path.parent.resolve()
                or successor_path.name != "packet.json"
            )
        )
    ):
        raise UpbitUniverseError("TRANSITION_CONTENT_ADDRESSED_PATH_MISMATCH")

    immutable_keys = (
        "schema_version", "snapshot_date", "generated_at",
        "raw_snapshot", "builder", "identity_review",
    )
    expected_raw = f"evidence/crypto/upbit/raw/{snapshot_date}"
    if not all(source.get(key) == successor.get(key) for key in immutable_keys):
        raise UpbitUniverseError("TRANSITION_SAME_RAW_VINTAGE_MISMATCH")
    if (
        source.get("snapshot_date") != snapshot_date
        or successor.get("snapshot_date") != snapshot_date
        or (source.get("raw_snapshot") or {}).get("path") != expected_raw
        or (successor.get("raw_snapshot") or {}).get("path") != expected_raw
    ):
        raise UpbitUniverseError("TRANSITION_RAW_PATH_MISMATCH")

    source_packet = source.get("packet") or {}
    successor_packet = successor.get("packet") or {}
    source_summary = source_packet.get("summary") or {}
    successor_summary = successor_packet.get("summary") or {}
    source_rows = source_packet.get("markets") or []
    successor_rows = successor_packet.get("markets") or []
    if (
        source_packet.get("policy_ratified") is not True
        or source_packet.get("taxonomy_ratified") is not False
        or (source.get("ratification") or {}).get("effective_for_snapshot") is not False
        or source_summary.get("tradeable_universe_count") != 0
        or source_summary.get("paper_eligible_count") != 0
        or source_summary.get("market_count") != len(source_rows)
        or (
            source_summary.get("observation_pool_count", 0)
            + source_summary.get("blocked_count", 0)
        ) != len(source_rows)
        or any(
            not isinstance(row, dict)
            or row.get("state") not in {STATE_OBSERVATION_POOL, STATE_BLOCKED}
            for row in source_rows
        )
    ):
        raise UpbitUniverseError("TRANSITION_SOURCE_STATE_NOT_POLICY_TRUE_TAXONOMY_FALSE")
    if (
        successor_packet.get("policy_ratified") is not True
        or successor_packet.get("taxonomy_ratified") is not True
        or (successor.get("ratification") or {}).get("effective_for_snapshot") is not True
        or successor_summary.get("market_count") != len(successor_rows)
        or successor_summary.get("tradeable_universe_count") != 0
        or successor_summary.get("paper_eligible_count") != 8
        or (
            successor_summary.get("observation_pool_count", 0)
            + successor_summary.get("blocked_count", 0)
            + successor_summary.get("paper_eligible_count", 0)
        ) != len(successor_rows)
        or any(
            not isinstance(row, dict)
            or row.get("state") not in {STATE_OBSERVATION_POOL, STATE_BLOCKED, STATE_PAPER_ELIGIBLE}
            for row in successor_rows
        )
    ):
        raise UpbitUniverseError("TRANSITION_SUCCESSOR_NOT_EXACT_EIGHT_EFFECTIVE")
    if not _transition_authority_closed(source, allow_observation_pool_marker=True):
        raise UpbitUniverseError("TRANSITION_SOURCE_AUTHORITY_NOT_CLOSED")
    if not _transition_authority_closed(successor):
        raise UpbitUniverseError("TRANSITION_SUCCESSOR_AUTHORITY_NOT_CLOSED")

    freeze = _read_json(
        _transition_repo_path(
            "config/upbit_identity_taxonomy_governance_freeze.json",
            repo_root=repo_root,
            label="TRANSITION_FREEZE",
        )
    )
    if manifest.get("base_content_approval") != freeze.get("approval_resolution"):
        raise UpbitUniverseError("TRANSITION_BASE_CONTENT_APPROVAL_MISMATCH")
    resolution = freeze.get("code_approval_resolution")
    if not isinstance(resolution, dict) or manifest.get("exact_release_resolution") != resolution:
        raise UpbitUniverseError("TRANSITION_EXACT_RELEASE_RESOLUTION_MISMATCH")
    if manifest.get("transition_available_at") != resolution.get("ratified_at_utc"):
        raise UpbitUniverseError("TRANSITION_AVAILABLE_AT_NOT_CODE_APPROVAL")
    transition_available_at = _transition_parse_utc(
        manifest.get("transition_available_at"), "TRANSITION_AVAILABLE_AT_INVALID",
    )
    if transition_available_at.date().isoformat() > snapshot_date:
        raise UpbitUniverseError("TRANSITION_APPROVAL_AFTER_EVALUATION_DATE")

    registry = _read_json(
        _transition_repo_path(
            "config/upbit_asset_identity_registry.json",
            repo_root=repo_root,
            label="TRANSITION_REGISTRY",
        )
    )
    taxonomy = _read_json(
        _transition_repo_path(
            "config/upbit_exclusion_taxonomy.json",
            repo_root=repo_root,
            label="TRANSITION_TAXONOMY",
        )
    )
    if not EXACT_RELEASE_BINDING.validate_exact_release(
        registry, content_field="mappings", evaluation_as_of=snapshot_date, repo_root=repo_root,
    ):
        raise UpbitUniverseError("TRANSITION_REGISTRY_EXACT_RELEASE_FAILED")
    if not EXACT_RELEASE_BINDING.validate_exact_release(
        taxonomy, content_field="records", evaluation_as_of=snapshot_date, repo_root=repo_root,
    ):
        raise UpbitUniverseError("TRANSITION_TAXONOMY_EXACT_RELEASE_FAILED")
    released = freeze.get("released_paper_markets")
    paper_markets = sorted(
        row.get("market")
        for row in successor_packet.get("markets") or []
        if isinstance(row, dict) and row.get("state") == STATE_PAPER_ELIGIBLE
    )
    if (
        not isinstance(released, list)
        or len(released) != 8
        or len(set(released)) != 8
        or sorted(registry.get("mappings") or {}) != sorted(released)
        or paper_markets != sorted(released)
    ):
        raise UpbitUniverseError("TRANSITION_EXACT_EIGHT_CONTENT_MISMATCH")

    deterministic_successor = rebuild_same_vintage_population_record(
        source,
        evaluation_as_of=snapshot_date,
        repo_root=repo_root,
    )
    if successor != deterministic_successor:
        raise UpbitUniverseError("TRANSITION_SUCCESSOR_NOT_DETERMINISTIC_REBUILD")

    return {
        "date": snapshot_date,
        "path": successor_path,
        "record": successor,
        "packet": successor_packet,
        "transition_manifest_path": manifest_path,
        "transition_manifest": manifest,
        "transition_manifest_payload_sha256": manifest_hash,
        "transition_manifest_file_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "transition_available_at": transition_available_at,
        "source_path": source_path,
        "source_record": source,
        "source_file_sha256": source_file_hash,
        "source_payload_sha256": source_hash,
    }


def validate_retained_same_vintage_transition(
    manifest_path: Path,
    *,
    source_record_path: Path,
    successor_record_path: Path,
    repo_root: Path = ROOT,
) -> dict:
    """Replay one retained manifest+canonical-source+successor bundle."""
    return validate_same_vintage_transition(
        manifest_path,
        repo_root=repo_root,
        _retained_source_path=source_record_path,
        _retained_successor_path=successor_record_path,
    )


def find_latest_population_record(
    data_root: Path = POPULATION_DATA_ROOT,
    *,
    not_after: dt.datetime | None = None,
    repo_root: Path = ROOT,
) -> dict | None:
    """Select latest canonical P3 record or its validated same-day successor.

    Once a latest-date directory exists, invalid/unavailable transition
    material never causes a prior-day fallback.  A valid transition becomes
    selectable only at its exact code-approval time.
    """
    data_root = Path(data_root)
    if not_after is not None:
        if not isinstance(not_after, dt.datetime) or not_after.tzinfo is None or not_after.utcoffset() is None:
            raise UpbitUniverseError("UNIVERSE_NOT_AFTER_MUST_BE_TIMEZONE_AWARE")
        not_after = not_after.astimezone(dt.timezone.utc)
    if not data_root.is_dir():
        return None
    dates = sorted(
        path for path in data_root.iterdir()
        if path.is_dir() and _DATE_RE.fullmatch(path.name)
    )
    for date_dir in reversed(dates):
        if date_dir.is_symlink():
            raise UpbitUniverseError("LATEST_UNIVERSE_DATE_DIRECTORY_SYMLINK_FORBIDDEN")
        canonical_path = date_dir / "packet.json"
        if canonical_path.is_symlink() or not canonical_path.is_file():
            raise UpbitUniverseError("LATEST_UNIVERSE_CANONICAL_PACKET_MISSING")
        canonical = _read_json(canonical_path)
        if not isinstance(canonical, dict):
            raise UpbitUniverseError("UNIVERSE_CANONICAL_RECORD_INVALID")
        _transition_self_hash(canonical, "UNIVERSE_CANONICAL_RECORD")
        if (
            canonical.get("schema_version") != POPULATION_RECORD_SCHEMA_VERSION
            or canonical.get("snapshot_date") != date_dir.name
        ):
            raise UpbitUniverseError("UNIVERSE_CANONICAL_RECORD_IDENTITY_MISMATCH")
        packet = canonical.get("packet") or {}
        canonical_available_at = _transition_parse_utc(
            packet.get("available_at"), "UNIVERSE_CANONICAL_AVAILABLE_AT_INVALID",
        )
        if not_after is not None and canonical_available_at > not_after:
            return None

        transitions_root = date_dir / "transitions"
        manifests = []
        if transitions_root.exists():
            if transitions_root.is_symlink() or not transitions_root.is_dir():
                raise UpbitUniverseError("TRANSITIONS_ROOT_INVALID")
            children = sorted(child for child in transitions_root.iterdir() if not child.name.startswith("."))
            for child in children:
                if child.is_symlink():
                    raise UpbitUniverseError("TRANSITION_DIRECTORY_SYMLINK_FORBIDDEN")
                manifest = child / "transition.json"
                if not child.is_dir() or manifest.is_symlink() or not manifest.is_file():
                    raise UpbitUniverseError("TRANSITION_INCOMPLETE_OR_UNMANIFESTED")
                manifests.append(manifest)
        if len(manifests) > 1:
            raise UpbitUniverseError("MULTIPLE_SAME_VINTAGE_TRANSITIONS_UNSUPPORTED")
        if manifests:
            transition = validate_same_vintage_transition(manifests[0], repo_root=repo_root)
            if not_after is None:
                raise UpbitUniverseError("TRANSITION_SELECTION_REQUIRES_NOT_AFTER")
            if transition["transition_available_at"] <= not_after:
                return transition
        return {
            "date": date_dir.name,
            "path": canonical_path,
            "record": canonical,
            "packet": packet,
        }
    return None


def _decimal(value, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise UpbitUniverseError(f"DECIMAL_INVALID:{label}:{value!r}") from exc


def _taxonomy_category(canonical_asset_id: str, as_of: str, taxonomy: dict) -> str | None:
    matches = []
    for row in taxonomy["records"]:
        if row.get("canonical_asset_id") != canonical_asset_id:
            continue
        start = row.get("effective_from")
        end = row.get("effective_to")
        if not isinstance(start, str) or start > as_of:
            continue
        if end is not None and end < as_of:
            continue
        matches.append(row)
    if len(matches) > 1:
        raise UpbitUniverseError(f"TAXONOMY_RECORD_OVERLAP:{canonical_asset_id}")
    return matches[0]["category"] if matches else None


# ---------------------------------------------------------------------------
# Snapshot parsing -- structural hash validation is reused unchanged from the
# capture module; this section only turns validated raw bytes into normalized
# per-market metrics.
# ---------------------------------------------------------------------------

def _read_gz_json(snapshot_dir: Path, relative_gz: str):
    import gzip
    raw = gzip.open(snapshot_dir / relative_gz, "rb").read()
    return json.loads(raw, parse_float=Decimal, parse_int=int)


def _dedupe_by_market(rows: list, key: str = "market") -> tuple[dict, dict]:
    out: dict = {}
    duplicates: dict = {}
    for row in rows:
        code = row.get(key)
        if code in out:
            duplicates[code] = duplicates.get(code, 1) + 1
            continue
        out[code] = row
    return out, duplicates


def load_snapshot_core(snapshot_dir: Path, contract: dict | None = None) -> dict:
    """Validate a captured snapshot directory's hashes (fail-closed on any
    tamper/mismatch) and parse it into a normalized per-market metrics dict.
    Deterministic: a pure function of the bytes already committed to disk.
    """
    snapshot_dir = Path(snapshot_dir)
    contract = contract or UPBIT_CAPTURE.load_contract(CAPTURE_CONTRACT_PATH)
    manifest = UPBIT_CAPTURE.validate_snapshot(snapshot_dir)

    market_all_rows, market_all_dupes = _dedupe_by_market(
        _read_gz_json(snapshot_dir, contract["market_all_raw_file"])
    )
    ticker_rows, ticker_dupes = _dedupe_by_market(
        _read_gz_json(snapshot_dir, contract["ticker_raw_file"])
    )
    orderbook_rows, orderbook_dupes = _dedupe_by_market(
        _read_gz_json(snapshot_dir, contract["orderbook_raw_file"])
    )

    candles_by_market: dict = {}
    candle_dupes: dict = {}
    bundle_path = snapshot_dir / contract["candles_bundle_raw_file"]
    import gzip
    raw_bundle = gzip.open(bundle_path, "rb").read()
    for line in raw_bundle.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        market = record["market"]
        if market in candles_by_market:
            candle_dupes[market] = candle_dupes.get(market, 1) + 1
            continue
        body = base64.b64decode(record["body_b64"])
        if hashlib.sha256(body).hexdigest() != record["response_sha256"]:
            raise UpbitUniverseError(f"CANDLE_BODY_HASH_MISMATCH:{market}")
        candles_by_market[market] = json.loads(body, parse_float=Decimal, parse_int=int)

    lookback_days = contract.get("turnover_lookback_finalized_days", 30)
    all_codes = set(market_all_rows) | set(manifest.get("markets", []))
    # manifest["markets"] is already KRW-only (the capture script's own
    # krw_markets() filter). market_all_rows is the raw, unfiltered
    # GET /v1/market/all archive and legitimately includes BTC-/USDT-quoted
    # pairs (e.g. a real market like "BTC-0G") -- those are out of P3-12's
    # declared "Upbit KRW 현물" scope and must never reach classification or
    # identity review (identity/upbit_market_identity_proposal.py's
    # default_candidate_canonical_asset_id() requires a KRW- prefix and
    # raises for anything else). Exclude them here, once, at the source.
    non_krw_excluded = sorted(code for code in all_codes if not _KRW_MARKET_RE.fullmatch(code))
    markets: dict = {}
    for market in sorted(all_codes - set(non_krw_excluded)):
        row = market_all_rows.get(market)
        entry: dict = {"market": market}
        if row is None:
            entry["market_all_available"] = False
        else:
            entry["market_all_available"] = True
            entry["korean_name"] = row.get("korean_name")
            entry["english_name"] = row.get("english_name")
            event = row.get("market_event") or {}
            entry["market_event_warning"] = event.get("warning")
            caution = event.get("caution") or {}
            entry["market_event_caution_any"] = any(bool(v) for v in caution.values()) if caution else False
            entry["market_event_caution_flags"] = caution

        orderbook = orderbook_rows.get(market)
        if orderbook is None or not orderbook.get("orderbook_units"):
            entry["orderbook_available"] = False
        else:
            entry["orderbook_available"] = True
            units = orderbook["orderbook_units"]
            entry["best_bid"] = units[0]["bid_price"]
            entry["best_ask"] = units[0]["ask_price"]
            entry["ask_levels"] = [
                {"price": unit["ask_price"], "size": unit["ask_size"]} for unit in units
            ]

        candles = candles_by_market.get(market)
        if candles is None or not isinstance(candles, list) or not candles:
            entry["candles_available"] = False
        else:
            entry["candles_available"] = True
            entry["observed_daily_candle_count"] = len(candles)
            finalized = candles[1:1 + lookback_days]  # exclude today (index 0), same T-1 discipline as Kraken
            entry["trailing_turnover_finalized_day_count"] = len(finalized)
            entry["trailing_30d_krw_turnover"] = sum(
                (Decimal(str(row.get("candle_acc_trade_price", 0))) for row in finalized), Decimal(0)
            )

        markets[market] = entry

    return {
        "snapshot_date": manifest["vintage_date"],
        "available_at": manifest["downloaded_at_utc"],
        "capture_version": manifest["capture_version"],
        "manifest_sha256": _file_sha(snapshot_dir / "_manifest.json"),
        "markets": markets,
        "duplicate_market_codes": {
            "market_all": market_all_dupes, "ticker": ticker_dupes,
            "orderbook": orderbook_dupes, "candles": candle_dupes,
        },
        "non_krw_market_codes_excluded": non_krw_excluded,
        "component_hashes": manifest["checksums"],
    }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _spread_bps(best_bid: Decimal, best_ask: Decimal) -> Decimal | None:
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return None
    mid = (best_bid + best_ask) / 2
    if mid == 0:
        return None
    return (best_ask - best_bid) / mid * Decimal(10000)


def _estimate_slippage_bps(ask_levels: list, best_ask: Decimal, notional_krw: Decimal) -> Decimal | None:
    """Volume-weighted average execution price for a market buy of
    ``notional_krw``, walking the captured ask side level by level, versus
    the best ask. Returns ``None`` (fail closed, never a silent estimate)
    when the captured depth cannot fill the requested notional.
    """
    remaining = notional_krw
    qty_filled = Decimal(0)
    notional_filled = Decimal(0)
    for level in ask_levels:
        price = Decimal(str(level["price"]))
        size = Decimal(str(level["size"]))
        if price <= 0 or size <= 0:
            continue
        level_notional = price * size
        take_notional = level_notional if level_notional <= remaining else remaining
        qty_filled += take_notional / price
        notional_filled += take_notional
        remaining -= take_notional
        if remaining <= 0:
            break
    if remaining > 0 or qty_filled <= 0 or best_ask <= 0:
        return None
    avg_price = notional_filled / qty_filled
    return (avg_price - best_ask) / best_ask * Decimal(10000)


def build_classification(
    core: dict,
    *,
    evaluation_as_of: str,
    policy: dict,
    taxonomy: dict,
    ratified_identity_registry: dict | None = None,
    blocked_markets: set | None = None,
    kraken_known_canonical_ids: set | None = None,
    repo_root: Path | None = None,
) -> dict:
    if not isinstance(evaluation_as_of, str) or not _DATE_RE.fullmatch(evaluation_as_of):
        raise UpbitUniverseError("EVALUATION_AS_OF_INVALID")
    available_at = core.get("available_at")
    if not isinstance(available_at, str) or not _UTC_RE.fullmatch(available_at):
        raise UpbitUniverseError("CORE_AVAILABLE_AT_INVALID")
    available_at_dt = dt.datetime.strptime(available_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    evaluation_dt = dt.datetime.strptime(evaluation_as_of + "T23:59:59Z", "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc
    )
    if available_at_dt > evaluation_dt:
        raise UpbitUniverseError("AVAILABLE_AT_FUTURE_DATED")

    ratified_identity_registry = ratified_identity_registry or {}
    blocked_markets = blocked_markets or set()
    # SAFETY INVARIANT: kraken_known_canonical_ids is READ-ONLY labeling.
    # It MUST NEVER be read anywhere below this point except to compute the
    # `kraken_cross_exchange_reference` display field. See
    # test_upbit_tradeable_universe.py::test_kraken_presence_never_promotes.
    kraken_known_canonical_ids = kraken_known_canonical_ids or set()

    policy_ratified = _policy_approval_effective(policy, evaluation_as_of, date_field="effective_date")
    taxonomy_ratified = _identity_taxonomy_exact_bound_effective(
        taxonomy, evaluation_as_of, date_field="effective_from", content_field="records",
        repo_root=repo_root,
    )
    min_listing_days = int(policy["min_listing_history_finalized_days"])
    min_turnover = _decimal(policy["min_30d_avg_krw_turnover"], "min_30d_avg_krw_turnover")
    max_spread = _decimal(policy["max_spread_bps"], "max_spread_bps")
    max_slippage = _decimal(policy["max_estimated_paper_slippage_bps"], "max_estimated_paper_slippage_bps")
    notional = _decimal(policy["paper_slippage_estimate_notional_krw"], "paper_slippage_estimate_notional_krw")
    max_age_hours = _decimal(policy["max_capture_age_hours"], "max_capture_age_hours")
    stale = (evaluation_dt - available_at_dt) > dt.timedelta(hours=float(max_age_hours))

    rows = []
    for market in sorted(core["markets"]):
        entry = core["markets"][market]
        reason = None
        state = STATE_OBSERVATION_POOL
        candidate_canonical_asset_id = None

        if market in blocked_markets:
            state, reason = STATE_BLOCKED, "IDENTITY_COLLISION"
        elif not entry.get("market_all_available"):
            reason = "MISSING_FIELD:market_all"
        elif entry.get("market_event_warning") is None:
            reason = "MISSING_FIELD:market_event"
        elif not entry.get("orderbook_available"):
            reason = "MISSING_FIELD:orderbook"
        elif not entry.get("candles_available"):
            reason = "MISSING_FIELD:candles"
        elif entry["market_event_warning"] is True:
            reason = "INVESTMENT_WARNING_ACTIVE"
        else:
            candidate_canonical_asset_id = ratified_identity_registry.get(market)
            if candidate_canonical_asset_id is None:
                reason = "IDENTITY_UNRATIFIED"
            elif not taxonomy_ratified:
                reason = "TAXONOMY_UNRATIFIED"
            else:
                category = _taxonomy_category(candidate_canonical_asset_id, evaluation_as_of, taxonomy)
                if category is None:
                    reason = "TAXONOMY_UNKNOWN"
                elif category != taxonomy["eligible_category"]:
                    reason = f"TAXONOMY_EXCLUDED:{category}"
                elif not policy_ratified:
                    reason = "POLICY_UNRATIFIED"
                elif stale:
                    reason = "STALE_CAPTURE"
                elif entry["observed_daily_candle_count"] < min_listing_days:
                    reason = "LISTING_HISTORY_BELOW_THRESHOLD"
                elif entry["trailing_turnover_finalized_day_count"] < policy["turnover_lookback_finalized_days"]:
                    reason = "TURNOVER_HISTORY_INCOMPLETE"
                # The ratified threshold is the mean finalized daily KRW
                # turnover across the complete 30-day lookback, not the
                # 30-day aggregate. Keep the aggregate in the packet for
                # schema compatibility and compute the comparison exactly
                # here from the already-validated finalized-day count.
                elif (
                    entry["trailing_30d_krw_turnover"]
                    / Decimal(entry["trailing_turnover_finalized_day_count"])
                ) < min_turnover:
                    reason = "TURNOVER_BELOW_THRESHOLD"
                else:
                    spread_bps = _spread_bps(Decimal(str(entry["best_bid"])), Decimal(str(entry["best_ask"])))
                    if spread_bps is None:
                        reason = "SPREAD_NOT_COMPUTABLE"
                    elif spread_bps > max_spread:
                        reason = "SPREAD_ABOVE_THRESHOLD"
                    else:
                        state = STATE_TRADEABLE_UNIVERSE
                        slippage_bps = _estimate_slippage_bps(
                            entry["ask_levels"], Decimal(str(entry["best_ask"])), notional
                        )
                        if slippage_bps is None:
                            reason = "SLIPPAGE_NOT_COMPUTABLE"
                        elif slippage_bps > max_slippage:
                            reason = "SLIPPAGE_ABOVE_THRESHOLD"
                        else:
                            state = STATE_PAPER_ELIGIBLE
                            reason = "PAPER_ELIGIBLE_ALL_GATES_PASSED"

        rows.append({
            "market": market,
            "state": state,
            "reason": reason,
            "candidate_canonical_asset_id": candidate_canonical_asset_id,
            "market_event_warning": entry.get("market_event_warning"),
            "market_event_caution_any": entry.get("market_event_caution_any"),
            "observed_daily_candle_count": entry.get("observed_daily_candle_count"),
            "trailing_30d_krw_turnover": (
                str(entry["trailing_30d_krw_turnover"]) if "trailing_30d_krw_turnover" in entry else None
            ),
            "kraken_cross_exchange_reference": (
                candidate_canonical_asset_id is not None and candidate_canonical_asset_id in kraken_known_canonical_ids
            ),
            "authority": dict(_ROW_AUTHORITY),
        })

    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "snapshot_date": core["snapshot_date"],
        "evaluation_as_of": evaluation_as_of,
        "available_at": available_at,
        "manifest_sha256": core["manifest_sha256"],
        "policy_version": policy.get("policy_version"),
        "policy_ratified": policy_ratified,
        "taxonomy_version": taxonomy.get("policy_version"),
        "taxonomy_ratified": taxonomy_ratified,
        "duplicate_market_codes": core.get("duplicate_market_codes", {}),
        "summary": {
            "market_count": len(rows),
            "observation_pool_count": sum(1 for r in rows if r["state"] == STATE_OBSERVATION_POOL),
            "tradeable_universe_count": sum(1 for r in rows if r["state"] == STATE_TRADEABLE_UNIVERSE),
            "paper_eligible_count": sum(1 for r in rows if r["state"] == STATE_PAPER_ELIGIBLE),
            "blocked_count": sum(1 for r in rows if r["state"] == STATE_BLOCKED),
        },
        "markets": rows,
        "authority": dict(_ROW_AUTHORITY),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet
