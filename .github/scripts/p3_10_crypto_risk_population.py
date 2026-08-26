#!/usr/bin/env python3
"""Populate policy-neutral P3-10 BTC risk source context.

The scheduled Kraken BTC capture already provides immutable PIT bytes and the
P1-CR-05 risk transform.  This adapter publishes the latest two finalized risk
feature observations as a detached source packet.  It does not fabricate or
attach a Discovery Case.  Candidate binding belongs to a later consumer that
can validate an actual committed Discovery Case, not to this source adapter.
"""

from __future__ import annotations

import argparse
import copy
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / ".github" / "scripts"
DISCOVERY_DIR = ROOT / "discovery"
for directory in (ROOT, SCRIPT_DIR, DISCOVERY_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import btc_risk  # noqa: E402
import valuation_risk_context as valuation_risk  # noqa: E402
from identity import canonical_identity  # noqa: E402


OUTPUT_ROOT = ROOT / "evidence" / "valuation_risk_sources" / "crypto"
IDENTITY_PATH = ROOT / "config" / "canonical_security_identity.json"
SOURCE_SCHEMA_VERSION = "valuation_risk_source_observation/1"
POPULATION_VERSION = "p3_10_crypto_risk_population/1"
ASSET_ID = "CRYPTO:BTC"
LISTING_ID = "KRAKEN:BTC-USD:SPOT"
SOURCE_NAME = "kraken_spot_ohlc"
SOURCE_ASSET_ID = "BTC/USD"
COMPARISON_BASIS = "ADJACENT_FINALIZED_UTC_DAILY_RISK_FEATURES_WITHIN_ONE_PIT_VINTAGE"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

CONTEXT_SPECS = (
    {
        "context_id": "CRYPTO.BTC.CURRENT_DRAWDOWN.90D.MAGNITUDE",
        "measurement_identity": "btc_risk/v1 current 90-close peak-to-current drawdown loss magnitude",
        "metric_type": "CURRENT_DRAWDOWN",
        "extract": lambda point: _loss_magnitude(point["drawdown"]["current_fraction"]),
    },
    {
        "context_id": "CRYPTO.BTC.MAXIMUM_DRAWDOWN.90D.MAGNITUDE",
        "measurement_identity": "btc_risk/v1 maximum 90-close peak-to-trough drawdown loss magnitude",
        "metric_type": "MAXIMUM_DRAWDOWN",
        "extract": lambda point: _loss_magnitude(point["drawdown"]["maximum_fraction"]),
    },
    {
        "context_id": "CRYPTO.BTC.REALIZED_VOLATILITY.30D.ANNUALIZED",
        "measurement_identity": "btc_risk/v1 30-return annualized realized volatility fraction",
        "metric_type": "REALIZED_VOLATILITY",
        "extract": lambda point: _nonnegative(point["realized_volatility"]["annualized_fraction"]),
    },
)

AUTHORITY = {
    "raw_source_context_authorized": True,
    "candidate_attachment_requires_allowed_case": True,
    "deterioration_interpretation_authorized": False,
    "candidate_creation_authorized": False,
    "candidate_mutation_authorized": False,
    "candidate_ranking_authorized": False,
    "stage_promotion_authorized": False,
    "rule_evaluation_authorized": False,
    "portfolio_action_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}

UNRESOLVED = [
    "ALLOWED_BTC_DISCOVERY_CASE_ABSENT",
    "DEFAULT_INTERPRETATION_POLICY_ABSENT",
    "DETERIORATION_DIRECTION_UNRATIFIED",
    "MINIMUM_CHANGE_UNRATIFIED",
    "METRIC_SELECTION_UNRATIFIED",
    "CRYPTO_VALUATION_UNDEFINED",
    "US_KOREA_RISK_POPULATION_NOT_IMPLEMENTED",
]


class PopulationError(RuntimeError):
    """Fail-closed P3-10 population violation."""


def fail(code: str, detail: object) -> None:
    raise PopulationError(f"{code}: {detail}")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        fail("DECIMAL_NOT_STRING", label)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise PopulationError(f"DECIMAL_INVALID: {label}") from exc
    if not parsed.is_finite():
        fail("DECIMAL_INVALID", label)
    return parsed


def _render(value: Decimal) -> str:
    with localcontext() as context:
        context.prec = 50
        quantum = Decimal("0.000000000001")
        return format(value.quantize(quantum, rounding=ROUND_HALF_EVEN), "f")


def _loss_magnitude(value: object) -> str:
    parsed = _decimal(value, "drawdown")
    if parsed > 0:
        fail("DRAWDOWN_SIGN_INVALID", value)
    return _render(-parsed)


def _nonnegative(value: object) -> str:
    parsed = _decimal(value, "nonnegative-risk")
    if parsed < 0:
        fail("RISK_VALUE_NEGATIVE", value)
    return _render(parsed)


def _identity_ref(as_of_utc: str) -> dict:
    authority = canonical_identity.load_authority(IDENTITY_PATH)
    resolved = canonical_identity.resolve_instrument_identity(
        SOURCE_NAME,
        SOURCE_ASSET_ID,
        "BTC",
        as_of_utc,
        authority,
    )
    if resolved.get("status") != canonical_identity.RESOLVED:
        fail("CANONICAL_IDENTITY_NOT_RESOLVED", resolved.get("status"))
    if (
        resolved.get("canonical_instrument_id") != ASSET_ID
        or resolved.get("listing_id") != LISTING_ID
    ):
        fail("CANONICAL_IDENTITY_MISMATCH", resolved)
    basis = resolved["identity_basis"]
    return {
        "authority_path": "config/canonical_security_identity.json",
        "authority_sha256": hashlib.sha256(IDENTITY_PATH.read_bytes()).hexdigest(),
        "policy_version": authority["policy_version"],
        "source_alias_rule_id": basis["source_alias"]["rule_id"],
        "listing_rule_id": basis["listing"]["rule_id"],
        "instrument_rule_id": basis["instrument"]["rule_id"],
        "canonical_instrument_id": resolved["canonical_instrument_id"],
        "listing_id": resolved["listing_id"],
    }


def _source_identity(replay: dict, source_url: str) -> dict:
    return {
        "source_id": "kraken_public_api",
        "source_url": source_url,
        "source_sha256": replay["source_sha256"],
        # Kraken does not publish a historical-row release timestamp.  The
        # exact Atlas fetch is the conservative no-earlier-than boundary.
        "available_at": replay["source_available_at"],
        "retrieved_at_utc": replay["source_available_at"],
    }


def _assemble_source_packet(snapshot_dir: Path) -> dict:
    snapshot_dir = Path(snapshot_dir)
    risk_contract = btc_risk.load_contract()
    qualified = btc_risk.qualified_input(snapshot_dir, risk_contract)
    replay = btc_risk.build_replay(snapshot_dir, risk_contract)
    if replay.get("point_count", 0) < 2:
        fail("TWO_POINT_RISK_HISTORY_ABSENT", replay.get("point_count"))
    points = replay["points"][-2:]
    periods = [point["as_of_date"] for point in points]
    source_url = qualified["price_contract"]["endpoint"]
    source = _source_identity(replay, source_url)
    contexts = []
    for spec in CONTEXT_SPECS:
        contexts.append(
            {
                "context_id": spec["context_id"],
                "market": "CRYPTO",
                "asset_id": ASSET_ID,
                "dimension": "RISK",
                "measurement_identity": spec["measurement_identity"],
                "metric_type": spec["metric_type"],
                "unit": "fraction",
                "comparison_basis": COMPARISON_BASIS,
                "expected_periods": list(periods),
                "evidence_points": [
                    {
                        "period_end": point["as_of_date"],
                        "status": "EVIDENCE_AVAILABLE",
                        "numeric_value": spec["extract"](point),
                        "missing_reasons": [],
                        "source_identities": [copy.deepcopy(source)],
                    }
                    for point in points
                ],
            }
        )
    packet = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "population_version": POPULATION_VERSION,
        "as_of_utc": replay["source_available_at"],
        "source_vintage": replay["source_vintage"],
        "market": "CRYPTO",
        "asset_id": ASSET_ID,
        "identity_ref": _identity_ref(replay["source_available_at"]),
        "source_transform": {
            "capture_version": replay["capture_version"],
            "risk_transform_version": replay["transform_version"],
            "risk_replay_version": replay["replay_version"],
            "replay_mode": replay["mode"],
            "source_url": source_url,
            "source_sha256": replay["source_sha256"],
            "source_available_at": replay["source_available_at"],
        },
        "expected_periods": periods,
        "risk_context_observations": contexts,
        "candidate_binding": {
            "status": "BLOCKED_NO_ALLOWED_CASE",
            "allowed_case_schema_versions": list(
                valuation_risk.load_contract()["allowed_case_schema_versions"]
            ),
            "candidate_ref": None,
        },
        "interpretation_policy": None,
        "authority": copy.deepcopy(AUTHORITY),
        "unresolved_boundaries": list(UNRESOLVED),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_source_packet(packet: dict, snapshot_dir: Path) -> dict:
    fields = {
        "schema_version", "population_version", "as_of_utc", "source_vintage",
        "market", "asset_id", "identity_ref", "source_transform",
        "expected_periods", "risk_context_observations", "candidate_binding",
        "interpretation_policy", "authority", "unresolved_boundaries",
        "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        fail("SOURCE_PACKET_FIELDS_MISMATCH", sorted(packet) if isinstance(packet, dict) else type(packet))
    if (
        packet.get("schema_version") != SOURCE_SCHEMA_VERSION
        or packet.get("population_version") != POPULATION_VERSION
        or packet.get("market") != "CRYPTO"
        or packet.get("asset_id") != ASSET_ID
        or not isinstance(packet.get("as_of_utc"), str)
        or UTC_RE.fullmatch(packet["as_of_utc"]) is None
        or packet.get("interpretation_policy") is not None
        or packet.get("authority") != AUTHORITY
        or packet.get("unresolved_boundaries") != UNRESOLVED
    ):
        fail("SOURCE_PACKET_CONSTANT_MISMATCH", "top-level")
    claimed = packet.get("payload_sha256")
    unhashed = copy.deepcopy(packet)
    unhashed.pop("payload_sha256", None)
    if not isinstance(claimed, str) or SHA256_RE.fullmatch(claimed) is None or claimed != payload_sha256(unhashed):
        fail("SOURCE_PACKET_SHA256_MISMATCH", claimed)
    periods = packet.get("expected_periods")
    if not isinstance(periods, list) or len(periods) != 2 or periods != sorted(set(periods)):
        fail("SOURCE_PACKET_PERIODS_INVALID", periods)
    if packet.get("identity_ref") != _identity_ref(packet["as_of_utc"]):
        fail("SOURCE_PACKET_IDENTITY_MISMATCH", packet.get("identity_ref"))
    transform = packet.get("source_transform")
    transform_fields = {
        "capture_version", "risk_transform_version", "risk_replay_version",
        "replay_mode", "source_url", "source_sha256", "source_available_at",
    }
    if (
        not isinstance(transform, dict)
        or set(transform) != transform_fields
        or transform.get("capture_version") != "btc-price-capture/v1"
        or transform.get("risk_transform_version") != "btc_risk/v1"
        or transform.get("risk_replay_version") != "btc_risk_replay/v1"
        or transform.get("replay_mode") != "as_captured_prefix_only"
        or transform.get("source_url") != btc_risk.btc_trend.load_contract()["endpoint"]
        or not isinstance(transform.get("source_sha256"), str)
        or SHA256_RE.fullmatch(transform["source_sha256"]) is None
        or transform.get("source_available_at") != packet["as_of_utc"]
    ):
        fail("SOURCE_PACKET_TRANSFORM_MISMATCH", transform)
    contexts = packet.get("risk_context_observations")
    if not isinstance(contexts, list) or len(contexts) != len(CONTEXT_SPECS):
        fail("SOURCE_PACKET_CONTEXTS_INVALID", "count")
    if [item.get("context_id") for item in contexts] != [item["context_id"] for item in CONTEXT_SPECS]:
        fail("SOURCE_PACKET_CONTEXTS_INVALID", "identity")
    expected_source = {
        "source_id": "kraken_public_api",
        "source_url": transform["source_url"],
        "source_sha256": transform["source_sha256"],
        "available_at": packet["as_of_utc"],
        "retrieved_at_utc": packet["as_of_utc"],
    }
    context_fields = {
        "context_id", "market", "asset_id", "dimension",
        "measurement_identity", "metric_type", "unit", "comparison_basis",
        "expected_periods", "evidence_points",
    }
    point_fields = {
        "period_end", "status", "numeric_value", "missing_reasons",
        "source_identities",
    }
    for context, spec in zip(contexts, CONTEXT_SPECS):
        if (
            not isinstance(context, dict)
            or set(context) != context_fields
            or context.get("context_id") != spec["context_id"]
            or context.get("market") != "CRYPTO"
            or context.get("asset_id") != ASSET_ID
            or context.get("dimension") != "RISK"
            or context.get("measurement_identity") != spec["measurement_identity"]
            or context.get("metric_type") != spec["metric_type"]
            or context.get("unit") != "fraction"
            or context.get("comparison_basis") != COMPARISON_BASIS
            or context.get("expected_periods") != periods
            or not isinstance(context.get("evidence_points"), list)
            or len(context["evidence_points"]) != 2
        ):
            fail("SOURCE_PACKET_CONTEXTS_INVALID", spec["context_id"])
        for point, period in zip(context["evidence_points"], periods):
            value = point.get("numeric_value") if isinstance(point, dict) else None
            if (
                not isinstance(point, dict)
                or set(point) != point_fields
                or point.get("period_end") != period
                or point.get("status") != "EVIDENCE_AVAILABLE"
                or point.get("missing_reasons") != []
                or point.get("source_identities") != [expected_source]
                or not isinstance(value, str)
                or _render(_decimal(value, f"{spec['context_id']}:{period}")) != value
                or _decimal(value, f"{spec['context_id']}:{period}") < 0
            ):
                fail("SOURCE_PACKET_POINT_INVALID", f"{spec['context_id']}:{period}")
    binding = packet.get("candidate_binding")
    expected_binding = {
        "status": "BLOCKED_NO_ALLOWED_CASE",
        "allowed_case_schema_versions": list(
            valuation_risk.load_contract()["allowed_case_schema_versions"]
        ),
        "candidate_ref": None,
    }
    if binding != expected_binding:
        fail("SOURCE_PACKET_CANDIDATE_FABRICATION", binding)
    expected = _assemble_source_packet(Path(snapshot_dir))
    if canonical_json(packet) != canonical_json(expected):
        fail("SOURCE_PACKET_RAW_REPLAY_MISMATCH", snapshot_dir)
    return copy.deepcopy(packet)


def build_source_packet(snapshot_dir: Path) -> dict:
    packet = _assemble_source_packet(Path(snapshot_dir))
    return validate_source_packet(packet, snapshot_dir)


def packet_bytes(packet: dict) -> bytes:
    return (json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def publish_packet(packet: dict, snapshot_date: str, output_root: Path = OUTPUT_ROOT) -> tuple[Path, str]:
    if packet.get("source_vintage") != snapshot_date:
        fail("SNAPSHOT_DATE_MISMATCH", snapshot_date)
    target = Path(output_root) / snapshot_date / "rev-001.json"
    expected = packet_bytes(packet)
    if target.exists():
        if target.read_bytes() != expected:
            fail("APPEND_ONLY_PACKET_MISMATCH", target)
        return target, "existing_identical"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    if temporary.exists():
        fail("TEMPORARY_PATH_EXISTS", temporary)
    try:
        temporary.write_bytes(expected)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target, "published"


def run(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args(argv)
    packet = build_source_packet(args.snapshot_dir)
    target, result = publish_packet(packet, args.snapshot_dir.name, args.output_root)
    print(
        json.dumps(
            {
                "result": result,
                "path": str(target),
                "payload_sha256": packet["payload_sha256"],
                "candidate_binding_status": packet["candidate_binding"]["status"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (
        PopulationError,
        btc_risk.RiskError,
        valuation_risk.ValuationRiskContextError,
        canonical_identity.IdentityError,
        OSError,
    ) as exc:
        print(f"P3-10 crypto risk population STOP: {exc}", file=sys.stderr)
        raise SystemExit(2)
