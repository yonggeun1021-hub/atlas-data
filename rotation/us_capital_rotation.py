#!/usr/bin/env python3
"""P2-02 policy-gated US Theme capital-rotation transform.

The transform consumes two retained-free ``us_leadership/v1`` derived packets.
It never sees vendor price rows.  Ranking and TOP/MIDDLE/BOTTOM transitions are
emitted only when a separate external policy is RATIFIED, effective across both
observations, and bound to the exact Theme-taxonomy and upstream taxonomy
policy hashes.  P2-05 state vocabulary, Regime, Stage, Production and trading
authority remain closed.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]

try:
    from rotation import theme_taxonomy as TT
except ModuleNotFoundError:  # direct ``python rotation/us_capital_rotation.py`` CLI
    import sys

    sys.path.insert(0, str(ROOT))
    from rotation import theme_taxonomy as TT

CONTRACT_PATH = ROOT / "config" / "us_capital_rotation_contract.json"
LEADERSHIP_SCRIPT = ROOT / ".github" / "scripts" / "us_leadership.py"
INPUT_SCHEMA_VERSION = "us_capital_rotation_input/1"
POLICY_SCHEMA_VERSION = "us_capital_rotation_policy/1"
OUTPUT_SCHEMA_VERSION = "us_capital_rotation_packet/2"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
ASSET_RE = re.compile(r"^[A-Z0-9._-]{1,20}$")

# The historical opaque binding (contract["taxonomy_contract_version"]) plus the
# source and derived fields a real ``theme_taxonomy/2`` consumption derives for
# itself.  The derived fields are never accepted from the caller at build time:
# build_packet() runs the exact supplied graph source bytes through the real
# P2-01 producer and writes back what that producer actually returned.
#
# ``upstream_taxonomy_policy_sha256`` is US-specific and unrelated to the P2-01
# graph: it is the effective-dated taxonomy *policy* hash each us_leadership/v1
# packet declares for itself, and it keeps binding the two Leadership
# observations exactly as before.
TAXONOMY_BINDING_FIELDS = {
    "taxonomy_contract_version", "taxonomy_id", "taxonomy_decision_id",
    "taxonomy_decision_sha256", "taxonomy_packet_sha256",
    "upstream_taxonomy_policy_sha256",
}
TAXONOMY_V2_DERIVED_FIELDS = {
    "taxonomy_source_json", "taxonomy_source_sha256", "taxonomy_graph_status",
    "taxonomy_authority_status", "theme_membership_authorized",
}

# The exact three ``graph_status`` verdicts the P2-01 producer emits.
# ``DRAFT_OR_NOT_EFFECTIVE_GRAPH`` is the producer's own name for a graph whose
# approval is not a ratified, currently-effective one on the decision date --
# an explicitly UNRATIFIED approval, a ratified approval that has not started
# yet, and a ratified approval that has already expired all land here.  Such a
# document is not a point-in-time taxonomy fact at all, so it can never carry
# US rotation ranking (see _taxonomy_source_effective).  The remaining two are
# real effective-dated documents; the difference between them is only whether
# the separate approval-authority registry authorizes membership activation,
# which US rotation has never claimed and still does not claim.
TAXONOMY_GRAPH_STATUS_AUTHORIZED = "AUTHORIZED_EFFECTIVE_GRAPH"
TAXONOMY_GRAPH_STATUS_CLAIM_NOT_AUTHORIZED = (
    "STRUCTURALLY_VALID_RATIFICATION_CLAIM_NOT_AUTHORIZED"
)
TAXONOMY_GRAPH_STATUS_NOT_EFFECTIVE = "DRAFT_OR_NOT_EFFECTIVE_GRAPH"
TAXONOMY_GRAPH_STATUSES = frozenset({
    TAXONOMY_GRAPH_STATUS_AUTHORIZED,
    TAXONOMY_GRAPH_STATUS_CLAIM_NOT_AUTHORIZED,
    TAXONOMY_GRAPH_STATUS_NOT_EFFECTIVE,
})


class USCapitalRotationError(ValueError):
    """Fail-closed P2-02 contract violation."""


def _load_leadership_module():
    spec = importlib.util.spec_from_file_location(
        "atlas_us_leadership_output_validator", LEADERSHIP_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise USCapitalRotationError("UPSTREAM_VALIDATOR_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEADERSHIP = _load_leadership_module()


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise USCapitalRotationError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "us_capital_rotation/2",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "policy_schema_version": POLICY_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "upstream_contract_version": "us_leadership_contract/v1",
        "upstream_transform_version": "us_leadership/v1",
        "taxonomy_contract_version": "theme_taxonomy/1",
        "measurement": "us_theme_relative_rotation_observation",
        "ranking_metric": "GROUP_RELATIVE_STRENGTH_VS_BENCHMARK",
        "ranking_order": "DESCENDING",
        "tie_break": "THEME_ID_ASC",
        "bucket_vocabulary": ["TOP", "MIDDLE", "BOTTOM"],
        "transition_semantics": "PRIOR_BUCKET_TO_CURRENT_BUCKET",
        "effective_interval": "[effective_from, effective_to)",
        "input_retention_policy": "UPSTREAM_DERIVED_PACKETS_ONLY_NO_VENDOR_ROWS",
        "output_retention_policy": "NON_RECONSTRUCTIVE_DERIVED_OBSERVATIONS_ONLY",
        "output_decimal_places": 12,
        "rounding": "ROUND_HALF_EVEN",
        "repository_default_policy": "ABSENT",
        "authority": {
            "external_ratified_rotation_policy_only": True,
            "default_theme_selection_authorized": False,
            "default_benchmark_authorized": False,
            "default_lookback_authorized": False,
            "p2_state_vocabulary_authorized": False,
            "state_ledger_authorized": False,
            "regime_input_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise USCapitalRotationError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise USCapitalRotationError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str):
        raise USCapitalRotationError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise USCapitalRotationError(code) from exc
    if parsed.isoformat() != value:
        raise USCapitalRotationError(code)
    return parsed


def _timestamp(value, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise USCapitalRotationError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise USCapitalRotationError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise USCapitalRotationError(code)
    return parsed.astimezone(dt.timezone.utc)


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise USCapitalRotationError(code)
    return value


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise USCapitalRotationError(code)
    return value


def _asset(value, code: str) -> str:
    if not isinstance(value, str) or ASSET_RE.fullmatch(value) is None:
        raise USCapitalRotationError(code)
    return value


def _positive_int(value, code: str) -> int:
    if type(value) is not int or value < 1:
        raise USCapitalRotationError(code)
    return value


def _decimal(value, code: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise USCapitalRotationError(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise USCapitalRotationError(code) from exc
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise USCapitalRotationError(code)
    return parsed


def _render(value: Decimal, places: int) -> str:
    try:
        with localcontext() as context:
            context.prec = 50
            rendered = value.quantize(
                Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN
            )
    except InvalidOperation as exc:
        raise USCapitalRotationError("OUTPUT_NUMBER_INVALID") from exc
    if rendered == 0:
        rendered = Decimal(0)
    text = format(rendered, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def taxonomy_producer_contract_version() -> str:
    """The exact ``contract_version`` of the real P2-01 Theme taxonomy producer.

    Read from that producer's own committed contract instead of being
    duplicated as a literal here, so this consumer cannot drift into accepting
    a taxonomy contract version the producer no longer emits.
    """
    try:
        return TT.load_contract()["contract_version"]
    except TT.ThemeTaxonomyError as exc:
        raise USCapitalRotationError(
            f"TAXONOMY_PRODUCER_CONTRACT_UNAVAILABLE:{exc}"
        ) from exc


def _validate_binding(value: dict, contract: dict, *, derived: bool) -> dict:
    """Validate the taxonomy binding for either supported binding version.

    ``contract["taxonomy_contract_version"]`` (``theme_taxonomy/1``) is the
    unchanged legacy binding: four opaque caller-supplied taxonomy identity
    strings this module can only carry, never resolve, alongside the US-native
    upstream taxonomy-policy hash it has always re-checked against both
    Leadership observations.  The producer's own current version
    (``theme_taxonomy/2``) additionally requires the exact graph source bytes
    at build time, and the persisted packet then carries the source and derived
    fields build_packet() derived from the real producer.
    """
    if not isinstance(value, dict):
        raise USCapitalRotationError("TAXONOMY_BINDING_FIELDS_MISMATCH")
    version = value.get("taxonomy_contract_version")
    legacy_version = contract["taxonomy_contract_version"]
    producer_version = taxonomy_producer_contract_version()
    if version not in (legacy_version, producer_version):
        raise USCapitalRotationError("TAXONOMY_CONTRACT_VERSION_MISMATCH")
    fields = set(TAXONOMY_BINDING_FIELDS)
    if derived and version == producer_version:
        fields |= TAXONOMY_V2_DERIVED_FIELDS
    if set(value) != fields:
        raise USCapitalRotationError("TAXONOMY_BINDING_FIELDS_MISMATCH")
    binding = {
        "taxonomy_contract_version": version,
        "taxonomy_id": _token(value.get("taxonomy_id"), "TAXONOMY_ID_INVALID"),
        "taxonomy_decision_id": _token(
            value.get("taxonomy_decision_id"), "TAXONOMY_DECISION_ID_INVALID"
        ),
        "taxonomy_decision_sha256": _sha(
            value.get("taxonomy_decision_sha256"), "TAXONOMY_DECISION_SHA_INVALID"
        ),
        "taxonomy_packet_sha256": _sha(
            value.get("taxonomy_packet_sha256"), "TAXONOMY_PACKET_SHA_INVALID"
        ),
        "upstream_taxonomy_policy_sha256": _sha(
            value.get("upstream_taxonomy_policy_sha256"),
            "UPSTREAM_TAXONOMY_POLICY_SHA_INVALID",
        ),
    }
    if derived and version == producer_version:
        membership_authorized = value.get("theme_membership_authorized")
        if membership_authorized is not True and membership_authorized is not False:
            raise USCapitalRotationError("TAXONOMY_MEMBERSHIP_AUTHORITY_INVALID")
        if not isinstance(value.get("taxonomy_source_json"), str):
            raise USCapitalRotationError("TAXONOMY_SOURCE_JSON_INVALID")
        binding.update({
            "taxonomy_source_json": value["taxonomy_source_json"],
            "taxonomy_source_sha256": _sha(
                value.get("taxonomy_source_sha256"), "TAXONOMY_SOURCE_SHA_INVALID"
            ),
            "taxonomy_graph_status": _token(
                value.get("taxonomy_graph_status"), "TAXONOMY_GRAPH_STATUS_INVALID"
            ),
            "taxonomy_authority_status": _token(
                value.get("taxonomy_authority_status"),
                "TAXONOMY_AUTHORITY_STATUS_INVALID",
            ),
            "theme_membership_authorized": membership_authorized,
        })
    return binding


def _consume_taxonomy(
    binding: dict,
    source_bytes,
    as_of_date: dt.date,
    registry_path,
    trusted_commit: str | None,
) -> tuple[dict, set[str] | None]:
    """Actually consume a real ``theme_taxonomy/2`` graph, or stay legacy.

    Nothing the caller declares about the taxonomy is trusted.  The exact
    supplied source bytes are re-run through the existing P2-01 producer
    (``theme_taxonomy.build_packet``), which performs its own structural
    validation and its own independent, git-provenance-bound authority
    resolution (``theme_taxonomy_authority.resolve_graph_authority``).  The
    caller's declared taxonomy identity and packet digest must equal what that
    producer actually derived, so a ``theme_taxonomy/1`` binding cannot be
    relabelled ``theme_taxonomy/2`` by editing four strings.

    The producer's authorization verdict is recorded exactly as returned --
    with the repository's empty approval-authority registry that verdict is
    "not authorized", and this consumer never upgrades or fabricates it.  It
    never becomes positive rotation authority either: what US rotation ranks,
    the single common benchmark, the TOP/BOTTOM counts and the observation gap
    are still granted only by the externally supplied rotation_policy.  The one
    thing the verdict does do is subtract -- a source the producer reports as
    DRAFT_OR_NOT_EFFECTIVE_GRAPH withholds ranking entirely, however ratified
    the rotation policy is (see _taxonomy_source_effective).

    Returns the derived binding fields and, for a real consumption, the set of
    Theme node ids that graph reports as active on this decision date.
    """
    version = binding["taxonomy_contract_version"]
    if version != taxonomy_producer_contract_version():
        if source_bytes is not None:
            raise USCapitalRotationError(
                "TAXONOMY_SOURCE_NOT_ALLOWED_FOR_LEGACY_BINDING"
            )
        return {}, None
    if source_bytes is None:
        raise USCapitalRotationError("TAXONOMY_SOURCE_REQUIRED_FOR_V2_BINDING")
    if not isinstance(source_bytes, (bytes, bytearray)):
        raise USCapitalRotationError("TAXONOMY_SOURCE_BYTES_INVALID")
    source_bytes = bytes(source_bytes)
    try:
        graph = json.loads(source_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise USCapitalRotationError(f"TAXONOMY_SOURCE_JSON_INVALID:{exc}") from exc
    try:
        packet = TT.build_packet(
            graph,
            authority_registry_path=(
                TT.TTA.REGISTRY_PATH if registry_path is None else Path(registry_path)
            ),
            trusted_commit=trusted_commit,
        )
    except TT.ThemeTaxonomyError as exc:
        raise USCapitalRotationError(
            f"TAXONOMY_SOURCE_REJECTED_BY_PRODUCER:{exc}"
        ) from exc
    if (
        packet.get("schema_version") != TT.OUTPUT_SCHEMA_VERSION
        or packet.get("contract_version") != version
    ):
        raise USCapitalRotationError("TAXONOMY_PRODUCER_IDENTITY_MISMATCH")
    # The graph must have been evaluated on this rotation's own decision date;
    # a graph resolved for another day is a different point-in-time fact and is
    # never reused here.
    if packet.get("as_of_date") != as_of_date.isoformat():
        raise USCapitalRotationError("TAXONOMY_AS_OF_DATE_MISMATCH")
    approval = packet.get("approval")
    if not isinstance(approval, dict):
        raise USCapitalRotationError("TAXONOMY_APPROVAL_INVALID")
    if (
        packet.get("taxonomy_id") != binding["taxonomy_id"]
        or approval.get("decision_id") != binding["taxonomy_decision_id"]
        or approval.get("decision_sha256") != binding["taxonomy_decision_sha256"]
    ):
        raise USCapitalRotationError("TAXONOMY_SOURCE_IDENTITY_MISMATCH")
    if packet.get("payload_sha256") != binding["taxonomy_packet_sha256"]:
        raise USCapitalRotationError("TAXONOMY_PACKET_SHA_NOT_DERIVED_FROM_SOURCE")
    membership_authorized = packet.get("theme_membership_authorized")
    if membership_authorized is not True and membership_authorized is not False:
        raise USCapitalRotationError("TAXONOMY_MEMBERSHIP_AUTHORITY_INVALID")
    resolution = packet.get("authority_resolution")
    if not isinstance(resolution, dict):
        raise USCapitalRotationError("TAXONOMY_AUTHORITY_RESOLUTION_INVALID")
    # The producer's effectivity verdict, taken from its own two independently
    # derived facts and cross-proved against each other so this consumer cannot
    # silently drift if the producer ever renames or adds a status.
    graph_status = _token(
        packet.get("graph_status"), "TAXONOMY_GRAPH_STATUS_INVALID"
    )
    if graph_status not in TAXONOMY_GRAPH_STATUSES:
        raise USCapitalRotationError(f"TAXONOMY_GRAPH_STATUS_UNKNOWN:{graph_status}")
    eligible_claim = packet.get("structurally_eligible_ratification_claim")
    if eligible_claim is not True and eligible_claim is not False:
        raise USCapitalRotationError("TAXONOMY_ELIGIBLE_CLAIM_INVALID")
    if eligible_claim is (graph_status == TAXONOMY_GRAPH_STATUS_NOT_EFFECTIVE):
        raise USCapitalRotationError("TAXONOMY_GRAPH_STATUS_DERIVATION_MISMATCH")
    nodes = packet.get("nodes")
    if not isinstance(nodes, list):
        raise USCapitalRotationError("TAXONOMY_NODES_INVALID")
    # Exactly the producer's own interval semantics, reused rather than
    # re-specified, and cross-checked against its own active_node_count.
    active_theme_ids = {
        node["theme_id"]
        for node in nodes
        if TT._active(node["valid_from"], node["valid_to"], packet["as_of_date"])
    }
    if len(active_theme_ids) != packet.get("active_node_count"):
        raise USCapitalRotationError("TAXONOMY_ACTIVE_NODE_DERIVATION_MISMATCH")
    derived = {
        "taxonomy_source_json": source_bytes.decode("utf-8"),
        "taxonomy_source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "taxonomy_graph_status": graph_status,
        "taxonomy_authority_status": _token(
            resolution.get("status"), "TAXONOMY_AUTHORITY_STATUS_INVALID"
        ),
        "theme_membership_authorized": membership_authorized,
    }
    return derived, active_theme_ids


def _assert_theme_nodes(theme_ids, active_theme_ids: set[str] | None) -> None:
    """Every rotation Theme id must name a node the consumed graph itself
    reports as active on this decision date.

    In US the rotation Theme id *is* the upstream Leadership ``group_id``, so
    this is referential integrity between the exact graph that was actually
    consumed and the group vocabulary US already ranks on -- there is no
    Korea-style series-to-Theme proxy mapping to resolve.  It selects nothing,
    ratifies nothing, and creates no asset-to-Theme membership.  It only
    applies when a real graph was consumed; the legacy opaque binding cannot be
    checked at all, which is precisely the gap the ``theme_taxonomy/2`` path
    closes.
    """
    if active_theme_ids is None:
        return
    for theme_id in sorted(theme_ids):
        if theme_id not in active_theme_ids:
            raise USCapitalRotationError(f"TAXONOMY_THEME_NODE_NOT_ACTIVE:{theme_id}")


def _taxonomy_source_effective(binding: dict) -> bool:
    """Whether the bound taxonomy source is an effective-dated document at all.

    A ``theme_taxonomy/2`` binding carries the producer's own re-derived
    ``taxonomy_graph_status``.  When that verdict is
    ``DRAFT_OR_NOT_EFFECTIVE_GRAPH`` the source is a draft, an explicitly
    UNRATIFIED approval, or a ratified approval that is not yet in force or has
    already expired on this decision date.  None of those is a point-in-time
    taxonomy fact, so the Theme vocabulary this rotation ranks on has no
    effective source behind it and ranking must not happen -- independently of
    the rotation policy, which is a separate ratification and cannot substitute
    for one.  This is a fail-closed gate only: it never grants ranking, never
    upgrades the producer's verdict, and never authorizes membership.

    The legacy ``theme_taxonomy/1`` binding has no such producer fact to check
    (that opacity is exactly what the /2 path exists to close), so it is left
    exactly as it has always behaved and stays gated by rotation policy alone.
    """
    if "taxonomy_graph_status" not in binding:
        return True
    return binding["taxonomy_graph_status"] != TAXONOMY_GRAPH_STATUS_NOT_EFFECTIVE


def _rotation_status(policy_effective: bool, taxonomy_effective: bool) -> str:
    """The single output status, derived from the two independent gates.

    The rotation policy is reported first so the long-standing invariant
    ``rotation_policy_effective is False`` <-> ``POLICY_NOT_EFFECTIVE`` still
    holds exactly, and the taxonomy-source state is named only when it is the
    reason ranking was withheld from an otherwise effective policy.
    """
    if not policy_effective:
        return "POLICY_NOT_EFFECTIVE"
    if not taxonomy_effective:
        return "TAXONOMY_SOURCE_NOT_EFFECTIVE"
    return "ROTATION_BUCKETS_OBSERVED"


AUTHORITY_FIELDS = {
    "leader_classification_authorized", "ranking_authorized",
    "trend_direction_authorized", "breadth_direction_authorized",
    "threshold_authorized", "regime_axis_input_authorized",
    "regime_score_authorized", "production_wiring_authorized",
    "trading_action_authorized",
}


def _validate_upstream(value: dict, label: str, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "transform_version", "market",
        "measurement", "status", "observation_date", "available_at",
        "benchmark_asset", "window", "temporal_eligibility",
        "asset_relative_strength", "partial_window_assets",
        "group_relative_strength", "daily_relative_participation", "retention",
        "policies", "lineage",
    } | AUTHORITY_FIELDS
    if not isinstance(value, dict) or set(value) != fields:
        raise USCapitalRotationError(f"UPSTREAM_FIELDS_MISMATCH:{label}")
    if (
        value.get("schema_version") != 1
        or value.get("contract_version") != contract["upstream_contract_version"]
        or value.get("transform_version") != contract["upstream_transform_version"]
        or value.get("market") != "US"
        or value.get("measurement") != "us_cross_sectional_leadership_observation"
        or value.get("status") != "OBSERVED_UNCLASSIFIED"
    ):
        raise USCapitalRotationError(f"UPSTREAM_IDENTITY_INVALID:{label}")
    if any(value[field] is not False for field in AUTHORITY_FIELDS):
        raise USCapitalRotationError(f"UPSTREAM_AUTHORITY_EXPANDED:{label}")
    observation_date = _date(value.get("observation_date"), f"UPSTREAM_DATE_INVALID:{label}")
    available_at = _timestamp(value.get("available_at"), f"UPSTREAM_AVAILABLE_AT_INVALID:{label}")
    if available_at.date() < observation_date:
        raise USCapitalRotationError(f"UPSTREAM_AVAILABLE_BEFORE_OBSERVATION:{label}")
    benchmark = _asset(value.get("benchmark_asset"), f"UPSTREAM_BENCHMARK_INVALID:{label}")

    window = value.get("window")
    window_fields = {
        "first_input_session", "first_return_session", "last_return_session",
        "lookback_sessions", "exact_expected_sessions",
    }
    if not isinstance(window, dict) or set(window) != window_fields:
        raise USCapitalRotationError(f"UPSTREAM_WINDOW_INVALID:{label}")
    first_input = _date(window.get("first_input_session"), f"UPSTREAM_WINDOW_INVALID:{label}")
    first_return = _date(window.get("first_return_session"), f"UPSTREAM_WINDOW_INVALID:{label}")
    last_return = _date(window.get("last_return_session"), f"UPSTREAM_WINDOW_INVALID:{label}")
    lookback = _positive_int(window.get("lookback_sessions"), f"UPSTREAM_WINDOW_INVALID:{label}")
    if not (first_input < first_return <= last_return == observation_date) or window.get("exact_expected_sessions") is not True:
        raise USCapitalRotationError(f"UPSTREAM_WINDOW_INVALID:{label}")

    temporal = value.get("temporal_eligibility")
    temporal_fields = {
        "run_mode", "price_basis", "eligibility", "reason_code",
        "authoritative_historical_pit", "forward_pit_qualified",
    }
    if (
        not isinstance(temporal, dict) or set(temporal) != temporal_fields
        or temporal.get("run_mode") != "FORWARD_SHADOW"
        or not isinstance(temporal.get("price_basis"), str)
        or not temporal["price_basis"]
        or temporal.get("eligibility") != "FORWARD_PIT_QUALIFIED"
        or not isinstance(temporal.get("reason_code"), str)
        or not temporal["reason_code"]
        or temporal.get("forward_pit_qualified") is not True
        or temporal.get("authoritative_historical_pit") is not False
    ):
        raise USCapitalRotationError(f"UPSTREAM_NOT_FORWARD_PIT:{label}")

    retention = value.get("retention")
    retention_fields = {
        "input_policy", "output_policy", "vendor_rows_emitted",
        "vendor_prices_emitted", "reconstructive_series_emitted",
    }
    if (
        not isinstance(retention, dict) or set(retention) != retention_fields
        or retention.get("input_policy") != "transient_memory_or_stdin_only"
        or retention.get("output_policy") != "non_reconstructive_derived_observations_only"
        or any(retention.get(field) is not False for field in (
            "vendor_rows_emitted", "vendor_prices_emitted", "reconstructive_series_emitted"
        ))
    ):
        raise USCapitalRotationError(f"UPSTREAM_RETENTION_INVALID:{label}")

    policies = value.get("policies")
    if not isinstance(policies, dict) or set(policies) != {"leadership", "universe", "taxonomy"}:
        raise USCapitalRotationError(f"UPSTREAM_POLICIES_INVALID:{label}")
    policy_fields = {
        "leadership": {
            "policy_version", "policy_sha256", "approval_status",
            "session_calendar_source",
        },
        "universe": {
            "policy_version", "policy_sha256", "approval_status",
            "membership_kind",
        },
        "taxonomy": {
            "policy_version", "policy_sha256", "approval_status",
            "effective_dated",
        },
    }
    for policy_name in ("leadership", "universe", "taxonomy"):
        item = policies.get(policy_name)
        if (
            not isinstance(item, dict)
            or set(item) != policy_fields[policy_name]
            or item.get("approval_status") != "RATIFIED"
            or not isinstance(item.get("policy_version"), str)
            or not item["policy_version"].strip()
        ):
            raise USCapitalRotationError(f"UPSTREAM_POLICY_UNRATIFIED:{label}:{policy_name}")
        _sha(item.get("policy_sha256"), f"UPSTREAM_POLICY_SHA_INVALID:{label}:{policy_name}")
    if (
        not isinstance(policies["leadership"]["session_calendar_source"], str)
        or not policies["leadership"]["session_calendar_source"].strip()
        or policies["universe"]["membership_kind"] != "point_in_time_source_coverage"
        or policies["taxonomy"]["effective_dated"] is not True
    ):
        raise USCapitalRotationError(f"UPSTREAM_POLICY_SEMANTICS_INVALID:{label}")

    lineage = value.get("lineage")
    lineage_fields = {
        "input_sha256", "source_temporal_contract", "session_count",
        "return_session_count", "session_coverage_complete",
        "current_membership_backfill_authorized",
    }
    if (
        not isinstance(lineage, dict) or set(lineage) != lineage_fields
        or lineage.get("session_coverage_complete") is not True
        or lineage.get("current_membership_backfill_authorized") is not False
        or lineage.get("return_session_count") != lookback
        or lineage.get("session_count") != lookback + 1
        or lineage.get("source_temporal_contract") != "atlas_price_pit_contract.py/v0.1"
    ):
        raise USCapitalRotationError(f"UPSTREAM_LINEAGE_INVALID:{label}")
    _sha(lineage.get("input_sha256"), f"UPSTREAM_INPUT_SHA_INVALID:{label}")

    asset_rows = value.get("asset_relative_strength")
    if not isinstance(asset_rows, list) or not asset_rows:
        raise USCapitalRotationError(f"UPSTREAM_LIST_INVALID:{label}:asset_relative_strength")
    asset_ids = []
    for row in asset_rows:
        if not isinstance(row, dict) or set(row) != {
            "asset", "observed_session_count", "cumulative_gross_return",
            "relative_strength_vs_benchmark", "classification",
        }:
            raise USCapitalRotationError(f"UPSTREAM_ASSET_FIELDS_MISMATCH:{label}")
        asset_id = _asset(row.get("asset"), f"UPSTREAM_ASSET_ID_INVALID:{label}")
        asset_ids.append(asset_id)
        if (
            _positive_int(row.get("observed_session_count"), f"UPSTREAM_ASSET_COUNT_INVALID:{label}") != lookback
            or _decimal(row.get("cumulative_gross_return"), f"UPSTREAM_ASSET_RETURN_INVALID:{label}", positive=True) <= 0
            or _decimal(row.get("relative_strength_vs_benchmark"), f"UPSTREAM_ASSET_RS_INVALID:{label}") <= Decimal(-1)
            or row.get("classification") != "UNDEFINED"
        ):
            raise USCapitalRotationError(f"UPSTREAM_ASSET_SEMANTICS_INVALID:{label}:{asset_id}")
    if asset_ids != sorted(set(asset_ids)):
        raise USCapitalRotationError(f"UPSTREAM_ASSET_ORDER_INVALID:{label}")

    partial_rows = value.get("partial_window_assets")
    if not isinstance(partial_rows, list):
        raise USCapitalRotationError(f"UPSTREAM_LIST_INVALID:{label}:partial_window_assets")
    partial_ids = []
    for row in partial_rows:
        if not isinstance(row, dict) or set(row) != {
            "asset", "observed_session_count", "required_session_count", "reason",
        }:
            raise USCapitalRotationError(f"UPSTREAM_PARTIAL_FIELDS_MISMATCH:{label}")
        asset_id = _asset(row.get("asset"), f"UPSTREAM_PARTIAL_ASSET_INVALID:{label}")
        partial_ids.append(asset_id)
        observed = _positive_int(row.get("observed_session_count"), f"UPSTREAM_PARTIAL_COUNT_INVALID:{label}")
        required = _positive_int(row.get("required_session_count"), f"UPSTREAM_PARTIAL_COUNT_INVALID:{label}")
        if observed >= required or required != lookback or row.get("reason") != "not_present_in_every_point_in_time_universe":
            raise USCapitalRotationError(f"UPSTREAM_PARTIAL_SEMANTICS_INVALID:{label}:{asset_id}")
    if partial_ids != sorted(set(partial_ids)) or set(partial_ids) & set(asset_ids):
        raise USCapitalRotationError(f"UPSTREAM_PARTIAL_ORDER_INVALID:{label}")

    raw_groups = value.get("group_relative_strength")
    if not isinstance(raw_groups, list) or len(raw_groups) < 3:
        raise USCapitalRotationError(f"UPSTREAM_GROUPS_INSUFFICIENT:{label}")
    groups = {}
    group_order = []
    group_fields = {
        "group_id", "observed_session_count", "minimum_daily_member_count",
        "required_minimum_member_count", "cumulative_gross_return",
        "relative_strength_vs_benchmark", "classification",
    }
    for raw in raw_groups:
        if not isinstance(raw, dict) or set(raw) != group_fields:
            raise USCapitalRotationError(f"UPSTREAM_GROUP_FIELDS_MISMATCH:{label}")
        group_id = _token(raw.get("group_id"), f"UPSTREAM_GROUP_ID_INVALID:{label}")
        if group_id in groups:
            raise USCapitalRotationError(f"UPSTREAM_GROUP_DUPLICATE:{label}:{group_id}")
        group_order.append(group_id)
        observed = _positive_int(
            raw.get("observed_session_count"), f"UPSTREAM_GROUP_COUNT_INVALID:{label}"
        )
        minimum = _positive_int(
            raw.get("minimum_daily_member_count"), f"UPSTREAM_GROUP_COUNT_INVALID:{label}"
        )
        required = _positive_int(
            raw.get("required_minimum_member_count"), f"UPSTREAM_GROUP_COUNT_INVALID:{label}"
        )
        cumulative = _decimal(
            raw.get("cumulative_gross_return"), f"UPSTREAM_GROUP_RETURN_INVALID:{label}", positive=True
        )
        relative = _decimal(
            raw.get("relative_strength_vs_benchmark"),
            f"UPSTREAM_GROUP_RELATIVE_STRENGTH_INVALID:{label}",
        )
        if observed != lookback or minimum < required or relative <= Decimal(-1) or raw.get("classification") != "UNDEFINED":
            raise USCapitalRotationError(f"UPSTREAM_GROUP_SEMANTICS_INVALID:{label}:{group_id}")
        groups[group_id] = {
            "theme_id": group_id,
            "cumulative_gross_return": cumulative,
            "relative_strength_vs_benchmark": relative,
            "minimum_daily_member_count": minimum,
            "required_minimum_member_count": required,
        }
    if group_order != sorted(group_order):
        raise USCapitalRotationError(f"UPSTREAM_GROUP_ORDER_INVALID:{label}")

    daily_rows = value.get("daily_relative_participation")
    if not isinstance(daily_rows, list) or len(daily_rows) != lookback:
        raise USCapitalRotationError(f"UPSTREAM_DAILY_LIST_INVALID:{label}")
    daily_dates = []
    for row in daily_rows:
        if not isinstance(row, dict) or set(row) != {
            "session_date", "eligible_non_benchmark_count",
            "outperforming_benchmark_count", "outperformance_participation_fraction",
            "required_group_member_counts",
        }:
            raise USCapitalRotationError(f"UPSTREAM_DAILY_FIELDS_MISMATCH:{label}")
        day = _date(row.get("session_date"), f"UPSTREAM_DAILY_DATE_INVALID:{label}")
        daily_dates.append(day)
        eligible = _positive_int(
            row.get("eligible_non_benchmark_count"), f"UPSTREAM_DAILY_COUNT_INVALID:{label}"
        )
        outperforming = row.get("outperforming_benchmark_count")
        if type(outperforming) is not int or not 0 <= outperforming <= eligible:
            raise USCapitalRotationError(f"UPSTREAM_DAILY_COUNT_INVALID:{label}")
        fraction = _decimal(
            row.get("outperformance_participation_fraction"),
            f"UPSTREAM_DAILY_FRACTION_INVALID:{label}",
        )
        if not Decimal(0) <= fraction <= Decimal(1):
            raise USCapitalRotationError(f"UPSTREAM_DAILY_FRACTION_INVALID:{label}")
        counts = row.get("required_group_member_counts")
        if not isinstance(counts, list):
            raise USCapitalRotationError(f"UPSTREAM_DAILY_GROUP_COUNTS_INVALID:{label}")
        count_ids = []
        for item in counts:
            if not isinstance(item, dict) or set(item) != {"group_id", "member_count"}:
                raise USCapitalRotationError(f"UPSTREAM_DAILY_GROUP_COUNTS_INVALID:{label}")
            count_ids.append(_token(item.get("group_id"), f"UPSTREAM_DAILY_GROUP_ID_INVALID:{label}"))
            _positive_int(item.get("member_count"), f"UPSTREAM_DAILY_GROUP_COUNT_INVALID:{label}")
        if count_ids != group_order:
            raise USCapitalRotationError(f"UPSTREAM_DAILY_GROUP_SET_MISMATCH:{label}")
    if daily_dates != sorted(set(daily_dates)) or (daily_dates and daily_dates[-1] != observation_date):
        raise USCapitalRotationError(f"UPSTREAM_DAILY_DATE_ORDER_INVALID:{label}")
    try:
        LEADERSHIP.validate_output(copy.deepcopy(value))
    except LEADERSHIP.USLeadershipError as exc:
        raise USCapitalRotationError(
            f"UPSTREAM_PRODUCTION_VALIDATION_FAILED:{label}:{exc}"
        ) from exc
    return {
        "observation_date": observation_date,
        "available_at": available_at,
        "benchmark_asset": benchmark,
        "lookback_sessions": lookback,
        "taxonomy_policy_sha256": policies["taxonomy"]["policy_sha256"],
        "groups": groups,
        "packet_sha256": payload_sha256(value),
    }


def _validate_policy(
    value: dict,
    binding: dict,
    theme_ids: list[str],
    prior_date: dt.date,
    current_date: dt.date,
    prior_available_at: dt.datetime,
) -> tuple[dict, bool]:
    fields = {
        "schema_version", "policy_id", "approval_status", "ratified_by",
        "ratified_at_utc", "effective_from", "effective_to",
        "taxonomy_decision_sha256", "taxonomy_packet_sha256",
        "upstream_taxonomy_policy_sha256", "theme_ids", "ranking_metric",
        "ranking_order", "tie_break", "top_count", "bottom_count",
        "maximum_calendar_gap_days",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise USCapitalRotationError("POLICY_FIELDS_MISMATCH")
    if value.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise USCapitalRotationError("POLICY_SCHEMA_MISMATCH")
    _token(value.get("policy_id"), "POLICY_ID_INVALID")
    status = value.get("approval_status")
    if status not in {"RATIFIED", "UNRATIFIED"}:
        raise USCapitalRotationError("POLICY_APPROVAL_STATUS_INVALID")
    effective_from = _date(value.get("effective_from"), "POLICY_EFFECTIVE_FROM_INVALID")
    effective_to = None
    if value.get("effective_to") is not None:
        effective_to = _date(value["effective_to"], "POLICY_EFFECTIVE_TO_INVALID")
        if effective_to <= effective_from:
            raise USCapitalRotationError("POLICY_EFFECTIVE_TO_INVALID")
    if value.get("taxonomy_decision_sha256") != binding["taxonomy_decision_sha256"]:
        raise USCapitalRotationError("POLICY_TAXONOMY_DECISION_MISMATCH")
    if value.get("taxonomy_packet_sha256") != binding["taxonomy_packet_sha256"]:
        raise USCapitalRotationError("POLICY_TAXONOMY_PACKET_MISMATCH")
    if value.get("upstream_taxonomy_policy_sha256") != binding["upstream_taxonomy_policy_sha256"]:
        raise USCapitalRotationError("POLICY_UPSTREAM_TAXONOMY_MISMATCH")
    supplied_themes = value.get("theme_ids")
    if (
        not isinstance(supplied_themes, list)
        or supplied_themes != sorted(set(supplied_themes))
        or any(TOKEN_RE.fullmatch(item) is None for item in supplied_themes)
        or supplied_themes != theme_ids
    ):
        raise USCapitalRotationError("POLICY_THEME_SET_MISMATCH")
    if value.get("ranking_metric") != "GROUP_RELATIVE_STRENGTH_VS_BENCHMARK":
        raise USCapitalRotationError("POLICY_RANKING_METRIC_INVALID")
    if value.get("ranking_order") != "DESCENDING" or value.get("tie_break") != "THEME_ID_ASC":
        raise USCapitalRotationError("POLICY_RANKING_ORDER_INVALID")
    top_count = _positive_int(value.get("top_count"), "POLICY_TOP_COUNT_INVALID")
    bottom_count = _positive_int(value.get("bottom_count"), "POLICY_BOTTOM_COUNT_INVALID")
    maximum_gap = _positive_int(
        value.get("maximum_calendar_gap_days"), "POLICY_MAXIMUM_GAP_INVALID"
    )
    if top_count + bottom_count > len(theme_ids):
        raise USCapitalRotationError("POLICY_BUCKETS_OVERLAP")
    covers_both = (
        effective_from <= prior_date
        and (effective_to is None or current_date < effective_to)
    )
    if status == "UNRATIFIED":
        if value.get("ratified_by") is not None or value.get("ratified_at_utc") is not None:
            raise USCapitalRotationError("UNRATIFIED_POLICY_PROOF_FORBIDDEN")
    else:
        if not isinstance(value.get("ratified_by"), str) or not value["ratified_by"].strip():
            raise USCapitalRotationError("POLICY_RATIFICATION_PROOF_INVALID")
        if not isinstance(value.get("ratified_at_utc"), str) or not value["ratified_at_utc"].endswith("Z"):
            raise USCapitalRotationError("POLICY_RATIFICATION_PROOF_INVALID")
        ratified_at = _timestamp(value["ratified_at_utc"], "POLICY_RATIFICATION_PROOF_INVALID")
        if covers_both and ratified_at > prior_available_at:
            raise USCapitalRotationError("POLICY_RATIFIED_AFTER_PRIOR_OBSERVATION")
    effective = status == "RATIFIED" and covers_both
    if effective and (current_date - prior_date).days > maximum_gap:
        raise USCapitalRotationError("OBSERVATION_GAP_EXCEEDS_POLICY")
    return copy.deepcopy(value), effective


def _rank(groups: dict[str, dict]) -> list[str]:
    return [
        item[0]
        for item in sorted(
            groups.items(),
            key=lambda item: (-item[1]["relative_strength_vs_benchmark"], item[0]),
        )
    ]


def _buckets(ranked: list[str], top_count: int, bottom_count: int) -> dict[str, str]:
    top = set(ranked[:top_count])
    bottom = set(ranked[-bottom_count:])
    return {
        theme_id: "TOP" if theme_id in top else "BOTTOM" if theme_id in bottom else "MIDDLE"
        for theme_id in ranked
    }


def build_packet(
    value: dict,
    policy: dict,
    contract: dict | None = None,
    *,
    taxonomy_source_bytes: bytes | None = None,
    taxonomy_authority_registry_path=None,
    taxonomy_trusted_commit: str | None = None,
) -> dict:
    """Build one US rotation packet.

    ``taxonomy_source_bytes`` is the optional real P2-01 input path: the exact
    bytes of a ``theme_taxonomy_input/1`` graph document.  It is required when
    the input's ``taxonomy_binding`` declares the producer's own
    ``theme_taxonomy/2`` contract version, and forbidden for the legacy opaque
    binding.  The legacy path is unchanged and still produces byte-identical
    packets.
    """
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "as_of_date", "taxonomy_binding",
        "prior_observation", "current_observation",
    }
    if not isinstance(value, dict) or value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise USCapitalRotationError("INPUT_SCHEMA_MISMATCH")
    if set(value) != fields:
        raise USCapitalRotationError("INPUT_FIELDS_MISMATCH")
    as_of_date = _date(value.get("as_of_date"), "AS_OF_DATE_INVALID")
    binding = _validate_binding(value.get("taxonomy_binding"), contract, derived=False)
    taxonomy_derived, active_theme_ids = _consume_taxonomy(
        binding, taxonomy_source_bytes, as_of_date,
        taxonomy_authority_registry_path, taxonomy_trusted_commit,
    )
    binding.update(taxonomy_derived)
    prior = _validate_upstream(value.get("prior_observation"), "prior", contract)
    current = _validate_upstream(value.get("current_observation"), "current", contract)
    if not prior["observation_date"] < current["observation_date"] == as_of_date:
        raise USCapitalRotationError("OBSERVATION_DATE_ORDER_INVALID")
    if prior["available_at"] >= current["available_at"]:
        raise USCapitalRotationError("OBSERVATION_AVAILABLE_AT_ORDER_INVALID")
    if prior["benchmark_asset"] != current["benchmark_asset"]:
        raise USCapitalRotationError("UPSTREAM_BENCHMARK_DRIFT")
    if prior["lookback_sessions"] != current["lookback_sessions"]:
        raise USCapitalRotationError("UPSTREAM_LOOKBACK_DRIFT")
    if (
        prior["taxonomy_policy_sha256"] != binding["upstream_taxonomy_policy_sha256"]
        or current["taxonomy_policy_sha256"] != binding["upstream_taxonomy_policy_sha256"]
    ):
        raise USCapitalRotationError("UPSTREAM_TAXONOMY_BINDING_MISMATCH")
    theme_ids = sorted(prior["groups"])
    if theme_ids != sorted(current["groups"]):
        raise USCapitalRotationError("UPSTREAM_THEME_SET_DRIFT")
    checked_policy, policy_effective = _validate_policy(
        policy,
        binding,
        theme_ids,
        prior["observation_date"],
        current["observation_date"],
        prior["available_at"],
    )
    _assert_theme_nodes(theme_ids, active_theme_ids)
    # Both ratifications must hold independently: an effective rotation policy
    # over a not-yet-effective, expired or UNRATIFIED taxonomy source produces
    # no ranks, buckets, transitions, top/bottom Themes or ranking authority.
    taxonomy_effective = _taxonomy_source_effective(binding)
    effective = policy_effective and taxonomy_effective
    observations = []
    top_themes = []
    bottom_themes = []
    if effective:
        prior_ranked = _rank(prior["groups"])
        current_ranked = _rank(current["groups"])
        prior_ranks = {theme_id: index + 1 for index, theme_id in enumerate(prior_ranked)}
        current_ranks = {theme_id: index + 1 for index, theme_id in enumerate(current_ranked)}
        prior_buckets = _buckets(prior_ranked, checked_policy["top_count"], checked_policy["bottom_count"])
        current_buckets = _buckets(current_ranked, checked_policy["top_count"], checked_policy["bottom_count"])
        top_themes = current_ranked[: checked_policy["top_count"]]
        bottom_themes = list(reversed(current_ranked[-checked_policy["bottom_count"] :]))
    else:
        prior_ranks = current_ranks = prior_buckets = current_buckets = {}
    places = contract["output_decimal_places"]
    for theme_id in theme_ids:
        prior_value = prior["groups"][theme_id]["relative_strength_vs_benchmark"]
        current_value = current["groups"][theme_id]["relative_strength_vs_benchmark"]
        prior_bucket = prior_buckets.get(theme_id)
        current_bucket = current_buckets.get(theme_id)
        observations.append({
            "theme_id": theme_id,
            "prior_relative_strength_vs_benchmark": _render(prior_value, places),
            "current_relative_strength_vs_benchmark": _render(current_value, places),
            "relative_strength_change": _render(current_value - prior_value, places),
            "prior_rank": prior_ranks.get(theme_id),
            "current_rank": current_ranks.get(theme_id),
            "rank_change": (
                prior_ranks[theme_id] - current_ranks[theme_id] if effective else None
            ),
            "prior_bucket": prior_bucket,
            "current_bucket": current_bucket,
            "bucket_transition": (
                f"{prior_bucket}_TO_{current_bucket}" if effective else None
            ),
            "p2_state": "UNDEFINED_PENDING_P2_05",
        })
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "measurement": contract["measurement"],
        "market": "US",
        "as_of_date": as_of_date.isoformat(),
        "status": _rotation_status(policy_effective, taxonomy_effective),
        "benchmark_asset": current["benchmark_asset"],
        "observation_pair": {
            "prior_date": prior["observation_date"].isoformat(),
            "current_date": current["observation_date"].isoformat(),
            "calendar_gap_days": (current["observation_date"] - prior["observation_date"]).days,
            "lookback_sessions": current["lookback_sessions"],
            # Both upstream observations' own available_at, persisted so a
            # standalone validate_packet() call can independently re-prove
            # temporal order and ratified-before-prior without either
            # re-reading the two full upstream Leadership packets (which
            # output_retention_policy forbids retaining) or trusting the
            # persisted rotation_policy_effective flag blindly.
            "prior_available_at": prior["available_at"].isoformat(),
            "current_available_at": current["available_at"].isoformat(),
        },
        "taxonomy_binding": binding,
        "rotation_policy": checked_policy,
        # Unchanged meaning: the rotation policy's own ratification and
        # coverage. It stays true for a ratified, covering policy even when the
        # taxonomy source withheld ranking, so the two gates remain separately
        # auditable rather than being collapsed into one ambiguous flag.
        "rotation_policy_effective": policy_effective,
        "ranking_method": {
            "metric": contract["ranking_metric"],
            "order": contract["ranking_order"],
            "tie_break": contract["tie_break"],
        } if effective else None,
        "top_themes": top_themes,
        "bottom_themes": bottom_themes,
        "theme_observations": observations,
        "retention": {
            "input_policy": contract["input_retention_policy"],
            "output_policy": contract["output_retention_policy"],
            "vendor_rows_received": False,
            "vendor_prices_emitted": False,
            "reconstructive_series_emitted": False,
        },
        "lineage": {
            "prior_upstream_packet_sha256": prior["packet_sha256"],
            "current_upstream_packet_sha256": current["packet_sha256"],
            "rotation_policy_sha256": payload_sha256(checked_policy),
            "taxonomy_packet_sha256": binding["taxonomy_packet_sha256"],
            "upstream_taxonomy_policy_sha256": binding["upstream_taxonomy_policy_sha256"],
        },
        "authority": copy.deepcopy(contract["authority"]) | {
            "theme_ranking_authorized": effective,
            "top_bottom_bucket_authorized": effective,
            "bucket_transition_authorized": effective,
        },
        "unresolved_boundaries": [
            "US_PRICE_AND_BREADTH_OPERATIONAL_SOURCE_INCOMPLETE",
            "THEME_TAXONOMY_OPERATIONAL_POPULATION_NOT_IMPLEMENTED",
            "P2_STATE_VOCABULARY_PENDING_P2_05",
            "ROTATION_LEDGER_NOT_IMPLEMENTED",
            "BRIEFING_INTEGRATION_NOT_IMPLEMENTED",
            "PRODUCTION_NOT_AUTHORIZED",
        ],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return validate_packet(
        packet, contract,
        taxonomy_source_bytes=taxonomy_source_bytes,
        taxonomy_authority_registry_path=taxonomy_authority_registry_path,
        taxonomy_trusted_commit=taxonomy_trusted_commit,
    )


def validate_packet(
    packet: dict,
    contract: dict | None = None,
    *,
    taxonomy_source_bytes: bytes | None = None,
    taxonomy_authority_registry_path=None,
    taxonomy_trusted_commit: str | None = None,
) -> dict:
    """Validate the complete v1 output without inventing omitted source rows.

    V2 packets carry the exact public graph source text. Every validation,
    including a ledger's packet-only call, re-runs the existing producer and
    independent registry resolver. An optional external source must match the
    embedded source exactly. Derived authority fields are never trusted.
    """
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "measurement", "market",
        "as_of_date", "status", "benchmark_asset", "observation_pair",
        "taxonomy_binding", "rotation_policy", "rotation_policy_effective",
        "ranking_method", "top_themes", "bottom_themes",
        "theme_observations", "retention", "lineage", "authority",
        "unresolved_boundaries", "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise USCapitalRotationError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != OUTPUT_SCHEMA_VERSION
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("measurement") != contract["measurement"]
        or packet.get("market") != "US"
    ):
        raise USCapitalRotationError("OUTPUT_IDENTITY_INVALID")
    as_of = _date(packet.get("as_of_date"), "OUTPUT_AS_OF_DATE_INVALID")
    _asset(packet.get("benchmark_asset"), "OUTPUT_BENCHMARK_INVALID")
    pair = packet.get("observation_pair")
    pair_fields = {
        "prior_date", "current_date", "calendar_gap_days", "lookback_sessions",
        "prior_available_at", "current_available_at",
    }
    if not isinstance(pair, dict) or set(pair) != pair_fields:
        raise USCapitalRotationError("OUTPUT_OBSERVATION_PAIR_INVALID")
    prior_date = _date(pair.get("prior_date"), "OUTPUT_PRIOR_DATE_INVALID")
    current_date = _date(pair.get("current_date"), "OUTPUT_CURRENT_DATE_INVALID")
    _positive_int(pair.get("lookback_sessions"), "OUTPUT_LOOKBACK_INVALID")
    if (
        not prior_date < current_date == as_of
        or pair.get("calendar_gap_days") != (current_date - prior_date).days
    ):
        raise USCapitalRotationError("OUTPUT_OBSERVATION_PAIR_MISMATCH")
    # Both upstream available_at timestamps, independently re-parsed and
    # re-ordered here -- not trusted from the persisted rotation_policy_
    # effective flag. This is what makes prior-vs-current temporal order
    # standalone-provable without either upstream Leadership packet.
    prior_available_at = _timestamp(
        pair.get("prior_available_at"), "OUTPUT_PRIOR_AVAILABLE_AT_INVALID"
    )
    current_available_at = _timestamp(
        pair.get("current_available_at"), "OUTPUT_CURRENT_AVAILABLE_AT_INVALID"
    )
    if prior_available_at >= current_available_at:
        raise USCapitalRotationError("OUTPUT_AVAILABLE_AT_ORDER_INVALID")
    binding = _validate_binding(packet.get("taxonomy_binding"), contract, derived=True)
    active_theme_ids = None
    if binding["taxonomy_contract_version"] == taxonomy_producer_contract_version():
        embedded_source = binding["taxonomy_source_json"].encode("utf-8")
        if taxonomy_source_bytes is not None and taxonomy_source_bytes != embedded_source:
            raise USCapitalRotationError("OUTPUT_TAXONOMY_DERIVATION_MISMATCH")
        taxonomy_source_bytes = embedded_source
    if taxonomy_source_bytes is not None:
        derived, active_theme_ids = _consume_taxonomy(
            binding, taxonomy_source_bytes, as_of,
            taxonomy_authority_registry_path, taxonomy_trusted_commit,
        )
        if {key: binding.get(key) for key in TAXONOMY_V2_DERIVED_FIELDS} != derived:
            raise USCapitalRotationError("OUTPUT_TAXONOMY_DERIVATION_MISMATCH")
    policy = packet.get("rotation_policy")
    policy_fields = {
        "schema_version", "policy_id", "approval_status", "ratified_by",
        "ratified_at_utc", "effective_from", "effective_to",
        "taxonomy_decision_sha256", "taxonomy_packet_sha256",
        "upstream_taxonomy_policy_sha256", "theme_ids", "ranking_metric",
        "ranking_order", "tie_break", "top_count", "bottom_count",
        "maximum_calendar_gap_days",
    }
    if not isinstance(policy, dict) or set(policy) != policy_fields:
        raise USCapitalRotationError("OUTPUT_POLICY_FIELDS_MISMATCH")
    if policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise USCapitalRotationError("OUTPUT_POLICY_SCHEMA_MISMATCH")
    _token(policy.get("policy_id"), "OUTPUT_POLICY_ID_INVALID")
    status = policy.get("approval_status")
    if status not in {"RATIFIED", "UNRATIFIED"}:
        raise USCapitalRotationError("OUTPUT_POLICY_STATUS_INVALID")
    start = _date(policy.get("effective_from"), "OUTPUT_POLICY_START_INVALID")
    end = (
        None
        if policy.get("effective_to") is None
        else _date(policy["effective_to"], "OUTPUT_POLICY_END_INVALID")
    )
    if end is not None and end <= start:
        raise USCapitalRotationError("OUTPUT_POLICY_END_INVALID")
    if (
        policy.get("taxonomy_decision_sha256") != binding["taxonomy_decision_sha256"]
        or policy.get("taxonomy_packet_sha256") != binding["taxonomy_packet_sha256"]
        or policy.get("upstream_taxonomy_policy_sha256")
        != binding["upstream_taxonomy_policy_sha256"]
        or policy.get("ranking_metric") != contract["ranking_metric"]
        or policy.get("ranking_order") != contract["ranking_order"]
        or policy.get("tie_break") != contract["tie_break"]
    ):
        raise USCapitalRotationError("OUTPUT_POLICY_BINDING_INVALID")
    theme_ids = policy.get("theme_ids")
    if (
        not isinstance(theme_ids, list)
        or theme_ids != sorted(set(theme_ids))
        or not theme_ids
        or any(
            not isinstance(item, str) or TOKEN_RE.fullmatch(item) is None
            for item in theme_ids
        )
    ):
        raise USCapitalRotationError("OUTPUT_POLICY_THEME_IDS_INVALID")
    _assert_theme_nodes(theme_ids, active_theme_ids)
    top_count = _positive_int(policy.get("top_count"), "OUTPUT_TOP_COUNT_INVALID")
    bottom_count = _positive_int(
        policy.get("bottom_count"), "OUTPUT_BOTTOM_COUNT_INVALID"
    )
    maximum_gap = _positive_int(
        policy.get("maximum_calendar_gap_days"), "OUTPUT_MAXIMUM_GAP_INVALID"
    )
    if top_count + bottom_count > len(theme_ids):
        raise USCapitalRotationError("OUTPUT_POLICY_BUCKETS_OVERLAP")
    ratified_at = None
    if status == "UNRATIFIED":
        if (
            policy.get("ratified_by") is not None
            or policy.get("ratified_at_utc") is not None
        ):
            raise USCapitalRotationError("OUTPUT_UNRATIFIED_PROOF_FORBIDDEN")
    else:
        if (
            not isinstance(policy.get("ratified_by"), str)
            or not policy["ratified_by"].strip()
            or not isinstance(policy.get("ratified_at_utc"), str)
            or not policy["ratified_at_utc"].endswith("Z")
        ):
            raise USCapitalRotationError("OUTPUT_RATIFICATION_PROOF_INVALID")
        ratified_at = _timestamp(
            policy["ratified_at_utc"], "OUTPUT_RATIFICATION_PROOF_INVALID"
        )
    covers_both = start <= prior_date and (end is None or current_date < end)
    # Standalone re-proof of ratified-before-prior: build_packet() refuses
    # to ever produce a packet where a policy covering both observations
    # was ratified after the prior one was already available, but that
    # refusal only protects packets that genuinely went through build_
    # packet() -- a tampered packet (e.g. a widened effective_to on an
    # already-persisted policy) could claim covers_both without this
    # having actually held. Re-checking it here, from prior_available_at
    # now persisted in observation_pair, closes that gap.
    if status == "RATIFIED" and covers_both and ratified_at > prior_available_at:
        raise USCapitalRotationError("OUTPUT_POLICY_RATIFIED_AFTER_PRIOR_OBSERVATION")
    policy_effective = status == "RATIFIED" and covers_both
    if policy_effective and (current_date - prior_date).days > maximum_gap:
        raise USCapitalRotationError("OUTPUT_OBSERVATION_GAP_EXCEEDS_POLICY")
    if packet.get("rotation_policy_effective") is not policy_effective:
        raise USCapitalRotationError("OUTPUT_POLICY_EFFECTIVE_MISMATCH")
    # The taxonomy-source gate is re-derived here from the binding's own
    # producer-derived status, which the derivation check above already re-ran
    # the real producer to re-prove. A packet that was ranked over a draft,
    # UNRATIFIED, not-yet-effective or expired source therefore cannot pass a
    # standalone validation even if every digest in it was re-signed.
    taxonomy_effective = _taxonomy_source_effective(binding)
    effective = policy_effective and taxonomy_effective
    raw_rows = packet.get("theme_observations")
    row_fields = {
        "theme_id", "prior_relative_strength_vs_benchmark",
        "current_relative_strength_vs_benchmark", "relative_strength_change",
        "prior_rank", "current_rank", "rank_change", "prior_bucket",
        "current_bucket", "bucket_transition", "p2_state",
    }
    if not isinstance(raw_rows, list) or len(raw_rows) != len(theme_ids):
        raise USCapitalRotationError("OUTPUT_THEME_OBSERVATIONS_INVALID")
    rows = []
    places = contract["output_decimal_places"]
    for row in raw_rows:
        if not isinstance(row, dict) or set(row) != row_fields:
            raise USCapitalRotationError("OUTPUT_THEME_FIELDS_MISMATCH")
        theme_id = _token(row.get("theme_id"), "OUTPUT_THEME_ID_INVALID")
        prior = _decimal(
            row.get("prior_relative_strength_vs_benchmark"),
            f"OUTPUT_PRIOR_RELATIVE_STRENGTH_INVALID:{theme_id}",
        )
        current = _decimal(
            row.get("current_relative_strength_vs_benchmark"),
            f"OUTPUT_CURRENT_RELATIVE_STRENGTH_INVALID:{theme_id}",
        )
        if (
            row["prior_relative_strength_vs_benchmark"] != _render(prior, places)
            or row["current_relative_strength_vs_benchmark"] != _render(current, places)
            or row.get("relative_strength_change") != _render(current - prior, places)
            or row.get("p2_state") != "UNDEFINED_PENDING_P2_05"
        ):
            raise USCapitalRotationError(
                f"OUTPUT_THEME_DERIVATION_MISMATCH:{theme_id}"
            )
        rows.append({
            "theme_id": theme_id,
            "prior": prior,
            "current": current,
            "row": row,
        })
    if [item["theme_id"] for item in rows] != theme_ids:
        raise USCapitalRotationError("OUTPUT_THEME_ORDER_MISMATCH")
    prior_ranked = [
        item["theme_id"]
        for item in sorted(
            rows, key=lambda item: (-item["prior"], item["theme_id"])
        )
    ]
    current_ranked = [
        item["theme_id"]
        for item in sorted(
            rows, key=lambda item: (-item["current"], item["theme_id"])
        )
    ]
    if effective:
        prior_ranks = {
            theme_id: index + 1 for index, theme_id in enumerate(prior_ranked)
        }
        current_ranks = {
            theme_id: index + 1 for index, theme_id in enumerate(current_ranked)
        }
        prior_buckets = _buckets(prior_ranked, top_count, bottom_count)
        current_buckets = _buckets(current_ranked, top_count, bottom_count)
        for item in rows:
            row = item["row"]
            theme_id = item["theme_id"]
            expected = {
                "prior_rank": prior_ranks[theme_id],
                "current_rank": current_ranks[theme_id],
                "rank_change": prior_ranks[theme_id] - current_ranks[theme_id],
                "prior_bucket": prior_buckets[theme_id],
                "current_bucket": current_buckets[theme_id],
                "bucket_transition": (
                    f"{prior_buckets[theme_id]}_TO_{current_buckets[theme_id]}"
                ),
            }
            if any(row.get(key) != value for key, value in expected.items()):
                raise USCapitalRotationError(
                    f"OUTPUT_RANK_BUCKET_MISMATCH:{theme_id}"
                )
        if (
            packet.get("status") != "ROTATION_BUCKETS_OBSERVED"
            or packet.get("ranking_method") != {
                "metric": contract["ranking_metric"],
                "order": contract["ranking_order"],
                "tie_break": contract["tie_break"],
            }
            or packet.get("top_themes") != current_ranked[:top_count]
            or packet.get("bottom_themes")
            != list(reversed(current_ranked[-bottom_count:]))
        ):
            raise USCapitalRotationError("OUTPUT_RANKING_SUMMARY_MISMATCH")
    else:
        for item in rows:
            row = item["row"]
            if any(
                row.get(key) is not None
                for key in (
                    "prior_rank", "current_rank", "rank_change", "prior_bucket",
                    "current_bucket", "bucket_transition",
                )
            ):
                raise USCapitalRotationError("OUTPUT_UNAUTHORIZED_RANKING")
        if (
            packet.get("status")
            != _rotation_status(policy_effective, taxonomy_effective)
            or packet.get("ranking_method") is not None
            or packet.get("top_themes") != []
            or packet.get("bottom_themes") != []
        ):
            raise USCapitalRotationError(
                "OUTPUT_INEFFECTIVE_POLICY_BOUNDARY_MISMATCH"
                if not policy_effective
                else "OUTPUT_INEFFECTIVE_TAXONOMY_BOUNDARY_MISMATCH"
            )
    if packet.get("retention") != {
        "input_policy": contract["input_retention_policy"],
        "output_policy": contract["output_retention_policy"],
        "vendor_rows_received": False,
        "vendor_prices_emitted": False,
        "reconstructive_series_emitted": False,
    }:
        raise USCapitalRotationError("OUTPUT_RETENTION_MISMATCH")
    lineage = packet.get("lineage")
    lineage_fields = {
        "prior_upstream_packet_sha256", "current_upstream_packet_sha256",
        "rotation_policy_sha256", "taxonomy_packet_sha256",
        "upstream_taxonomy_policy_sha256",
    }
    if not isinstance(lineage, dict) or set(lineage) != lineage_fields:
        raise USCapitalRotationError("OUTPUT_LINEAGE_FIELDS_MISMATCH")
    for key in lineage_fields:
        _sha(lineage.get(key), f"OUTPUT_LINEAGE_SHA_INVALID:{key}")
    if (
        lineage["rotation_policy_sha256"] != payload_sha256(policy)
        or lineage["taxonomy_packet_sha256"] != binding["taxonomy_packet_sha256"]
        or lineage["upstream_taxonomy_policy_sha256"]
        != binding["upstream_taxonomy_policy_sha256"]
    ):
        raise USCapitalRotationError("OUTPUT_LINEAGE_BINDING_MISMATCH")
    expected_authority = copy.deepcopy(contract["authority"]) | {
        "theme_ranking_authorized": effective,
        "top_bottom_bucket_authorized": effective,
        "bucket_transition_authorized": effective,
    }
    if packet.get("authority") != expected_authority:
        raise USCapitalRotationError("OUTPUT_AUTHORITY_MISMATCH")
    if packet.get("unresolved_boundaries") != [
        "US_PRICE_AND_BREADTH_OPERATIONAL_SOURCE_INCOMPLETE",
        "THEME_TAXONOMY_OPERATIONAL_POPULATION_NOT_IMPLEMENTED",
        "P2_STATE_VOCABULARY_PENDING_P2_05",
        "ROTATION_LEDGER_NOT_IMPLEMENTED",
        "BRIEFING_INTEGRATION_NOT_IMPLEMENTED",
        "PRODUCTION_NOT_AUTHORIZED",
    ]:
        raise USCapitalRotationError("OUTPUT_BOUNDARIES_MISMATCH")
    digest = _sha(packet.get("payload_sha256"), "OUTPUT_PACKET_SHA_INVALID")
    normalized = copy.deepcopy(packet)
    normalized.pop("payload_sha256")
    if payload_sha256(normalized) != digest:
        raise USCapitalRotationError("OUTPUT_PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise USCapitalRotationError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _read_bytes(path: Path) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise USCapitalRotationError(
            f"TAXONOMY_SOURCE_READ_FAILED:{path}:{exc}"
        ) from exc


def run(
    input_path: Path,
    policy_path: Path,
    output_path: Path,
    taxonomy_graph_path: Path | None = None,
    taxonomy_trusted_commit: str | None = None,
) -> int:
    try:
        source_bytes = (
            None if taxonomy_graph_path is None else _read_bytes(taxonomy_graph_path)
        )
        write_json_atomic(
            output_path,
            build_packet(
                _read_json(input_path), _read_json(policy_path),
                taxonomy_source_bytes=source_bytes,
                taxonomy_trusted_commit=taxonomy_trusted_commit,
            ),
        )
        return 0
    except (USCapitalRotationError, OSError, TypeError, ValueError) as exc:
        print(f"US capital rotation failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a policy-gated US Theme rotation packet")
    parser.add_argument("input", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--taxonomy-graph", type=Path, default=None,
        help=(
            "Exact theme_taxonomy_input/1 graph document consumed through the "
            "real theme_taxonomy producer. Required for a theme_taxonomy/2 "
            "binding, forbidden for the legacy opaque binding."
        ),
    )
    parser.add_argument(
        "--taxonomy-trusted-commit", default=None,
        help="Immutable commit the taxonomy authority registry is read at.",
    )
    args = parser.parse_args()
    return run(
        args.input, args.policy, args.out,
        args.taxonomy_graph, args.taxonomy_trusted_commit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
