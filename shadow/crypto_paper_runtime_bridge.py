#!/usr/bin/env python3
"""P5/P9 -> P10-11 private-runtime bridge for Crypto PAPER observations.

This module is deliberately offline and side-effect free.  It independently
rebuilds a committed ``crypto_paper_decision_snapshot_packet/1`` from its
exact public source files, then (only when caller-supplied private PAPER
account/economic inputs make P5-09 genuinely eligible) builds P10-11 PAPER
intents and exact public-orderbook snapshots.

The returned request is value-bearing and is therefore PRIVATE-RUNTIME ONLY.
This module never writes it to the public repository, opens a socket, reads a
credential, or calls an exchange endpoint.  Persisting/recovering the ledger
is owned by ``atlas-private-evidence``.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DECISION_PATH = ROOT / "decision" / "crypto_paper_decision_snapshot.py"
SIMULATOR_PATH = ROOT / "shadow" / "crypto_paper_simulator.py"
REALTIME_GATE_PATH = ROOT / "realtime" / "upbit_realtime_gate.py"
LIVE_AXIS_PATH = ROOT / "regime" / "live_axis_adapter.py"
STAGE5_PATH = ROOT / "shadow" / "stage5_paper_envelope_ledger.py"

REQUEST_SCHEMA_VERSION = "crypto_paper_runtime_request/3"
# Requests issued before the per-market realtime freshness ratification
# (CRYPTO-REALTIME-FRESHNESS-PER-MARKET-V1-20260914) keep revalidating with
# their original aggregate-freshness derivation, byte for byte.
LEGACY_REQUEST_SCHEMA_VERSION = "crypto_paper_runtime_request/2"
REQUEST_SCHEMA_VERSIONS = (LEGACY_REQUEST_SCHEMA_VERSION, REQUEST_SCHEMA_VERSION)
# /4 (crypto PAPER wiring v2, build plan PR3 item 3): the request for a
# ``crypto_paper_decision_snapshot_packet/4``.  /2 and /3 requests are unchanged.
V4_REQUEST_SCHEMA_VERSION = "crypto_paper_runtime_request/4"
ALL_REQUEST_SCHEMA_VERSIONS = REQUEST_SCHEMA_VERSIONS + (V4_REQUEST_SCHEMA_VERSION,)
PER_MARKET_FRESHNESS_MODE = "PER_MARKET_RATIFIED"
AGGREGATE_FRESHNESS_MODE = "AGGREGATE_DECISION_V1"
ENTRY_OPEN = "ENTRY_OPEN"
ENTRY_CAPPED = "ENTRY_CAPPED"
MARKET_NOT_SUBSCRIBED_REASON = "MARKET_NOT_SUBSCRIBED_IN_DECISION_REALTIME"
RUNTIME_CONFIG_SCHEMA_VERSION = "crypto_paper_runtime_config/1"
RUNTIME_CONFIG_APPROVAL = "USER_RATIFIED_PAPER_RUNTIME"
LATEST_PUBLIC_MESSAGES_SCHEMA_VERSION = "upbit_realtime_latest_public_messages/1"
PRIVATE_RUNTIME_MODE = "PRIVATE_RUNTIME_ONLY_DO_NOT_PUBLISH"
STAGE5_CONNECTION_SCHEMA_VERSION = "crypto_stage5_fixture_connection_receipt/1"
STAGE5_CONNECTION_MODE = "MOCK_PATH_VERIFIED_NOT_PAPER_EXECUTION"
STAGE5_FIXTURE_LEDGER_PREFIX = "STAGE5.FIXTURE."

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
MARKET_RE = re.compile(r"^KRW-[A-Z0-9]{2,20}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

AUTHORITY = {
    "paper_runtime_request_only": True,
    "network_access_authorized": False,
    "credential_access_authorized": False,
    "investment_eligibility_authorized": False,
    "action_authorized": False,
    "exchange_order_authorized": False,
    "broker_submission_authorized": False,
    "withdrawal_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "real_capital_authorized": False,
}


class CryptoPaperRuntimeBridgeError(ValueError):
    """Fail-closed bridge contract, lineage, or runtime-input violation."""


class MarketEvidenceUnavailableError(CryptoPaperRuntimeBridgeError):
    """One market's realtime evidence is not usable (not FRESH, missing, late).

    Under per-market freshness this is that market's blocker only.  Identity,
    hash, or value violations stay plain ``CryptoPaperRuntimeBridgeError`` and
    still fail the whole request closed.
    """


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CryptoPaperRuntimeBridgeError(f"MODULE_LOAD_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DECISION = _load("crypto_paper_runtime_decision", DECISION_PATH)
SIMULATOR = _load("crypto_paper_runtime_simulator", SIMULATOR_PATH)
REALTIME = _load("crypto_paper_runtime_realtime", REALTIME_GATE_PATH)
STAGE5 = _load("crypto_paper_runtime_stage5", STAGE5_PATH)
PROMOTION = DECISION.PROMOTION
ELIGIBILITY = DECISION.ELIGIBILITY


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoPaperRuntimeBridgeError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise CryptoPaperRuntimeBridgeError(f"FILE_HASH_FAILED:{path}:{exc}") from exc


def _safe_observation_root(value: object | None) -> Path:
    if value is not None and not isinstance(value, (str, Path)):
        raise CryptoPaperRuntimeBridgeError("OBSERVATION_ROOT_INVALID")
    root = ROOT if value is None else Path(value)
    if not root.is_absolute():
        raise CryptoPaperRuntimeBridgeError("OBSERVATION_ROOT_NOT_ABSOLUTE")
    if root.is_symlink() or not root.is_dir():
        raise CryptoPaperRuntimeBridgeError("OBSERVATION_ROOT_INVALID")
    return root.resolve()


def _decision_validator(observation_root: Path):
    """Load approved code in isolation while resolving evidence elsewhere."""
    if observation_root == ROOT.resolve():
        return DECISION
    name = "crypto_paper_runtime_decision_observation_" + hashlib.sha256(
        str(observation_root).encode("utf-8")
    ).hexdigest()[:16]
    validator = _load(name, DECISION_PATH)
    for dependency in (
        "UNIVERSE", "PROMOTION", "ELIGIBILITY", "MARKET_EVIDENCE",
        "CANDLE_FINALIZATION", "REALTIME_GATE", "REGIME_OUTPUT",
    ):
        setattr(validator, dependency, getattr(DECISION, dependency))
    live_axis = _load(
        "crypto_paper_runtime_live_axis_observation_"
        + hashlib.sha256(str(observation_root).encode("utf-8")).hexdigest()[:16],
        LIVE_AXIS_PATH,
    )
    # Keep every adapter and transform imported from the approved code checkout,
    # but resolve their retained public evidence inside the separately verified
    # rolling observation checkout. Reusing DECISION.LIVE_AXIS here would leave
    # its module-level ROOT pinned to the older code checkout and silently turn
    # later stablecoin or other component evidence back into UNDEFINED.
    live_axis.ROOT = observation_root
    validator.LIVE_AXIS = live_axis
    # The module and every imported policy/transform came from the approved
    # code checkout. Only its relative evidence-path root is redirected to the
    # separately verified, read-only observation checkout.
    validator.ROOT = observation_root
    validator.CRYPTO_BREADTH_RAW_ROOT = observation_root / "evidence" / "crypto" / "breadth" / "raw"
    return validator


def _safe_repo_path(relative: object, *, observation_root: Path | None = None) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise CryptoPaperRuntimeBridgeError("SOURCE_REF_PATH_INVALID")
    root = _safe_observation_root(observation_root)
    relative_path = Path(relative)
    if ".." in relative_path.parts:
        raise CryptoPaperRuntimeBridgeError("SOURCE_REF_PATH_ESCAPE")
    candidate = root / relative_path
    current = root
    for part in relative_path.parts:
        current /= part
        if current.is_symlink():
            raise CryptoPaperRuntimeBridgeError(
                f"SOURCE_REF_FILE_INVALID:{relative}"
            )
    target = candidate.resolve()
    if root not in target.parents:
        raise CryptoPaperRuntimeBridgeError("SOURCE_REF_PATH_ESCAPE")
    if not target.is_file():
        raise CryptoPaperRuntimeBridgeError(f"SOURCE_REF_FILE_INVALID:{relative}")
    return target


def _require_sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise CryptoPaperRuntimeBridgeError(code)
    return value


def _require_sha40(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise CryptoPaperRuntimeBridgeError(code)
    return value


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise CryptoPaperRuntimeBridgeError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise CryptoPaperRuntimeBridgeError(code) from exc
    return parsed


def _format_decimal(value: object, code: str, *, positive: bool = False) -> str:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CryptoPaperRuntimeBridgeError(code) from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        raise CryptoPaperRuntimeBridgeError(code)
    if parsed == 0:
        return "0"
    rendered = format(parsed, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _assert_all_false_authority(block: object, code: str) -> None:
    if not isinstance(block, dict) or not block or any(value is not False for value in block.values()):
        raise CryptoPaperRuntimeBridgeError(code)


def _source_refs(
    packet: dict, *, observation_root: Path | None = None,
) -> dict[str, dict]:
    refs = packet.get("source_refs")
    if not isinstance(refs, list):
        raise CryptoPaperRuntimeBridgeError("DECISION_SOURCE_REFS_INVALID")
    by_role = {}
    for row in refs:
        if not isinstance(row, dict) or set(row) != {"role", "path", "sha256"}:
            raise CryptoPaperRuntimeBridgeError("DECISION_SOURCE_REF_FIELDS_INVALID")
        role = row.get("role")
        if role in by_role:
            raise CryptoPaperRuntimeBridgeError(f"DECISION_SOURCE_ROLE_DUPLICATE:{role}")
        path = _safe_repo_path(row.get("path"), observation_root=observation_root)
        expected = _require_sha256(row.get("sha256"), "DECISION_SOURCE_SHA_INVALID")
        if _file_sha256(path) != expected:
            raise CryptoPaperRuntimeBridgeError(f"DECISION_SOURCE_SHA_MISMATCH:{role}")
        by_role[role] = {"path": path, "record": _read_json(path)}
    return by_role


def _preflight_source_paths(packet: dict, *, observation_root: Path) -> None:
    refs = packet.get("source_refs")
    if not isinstance(refs, list):
        return
    for row in refs:
        if not isinstance(row, dict):
            continue
        try:
            _safe_repo_path(row.get("path"), observation_root=observation_root)
        except CryptoPaperRuntimeBridgeError as exc:
            raise CryptoPaperRuntimeBridgeError(
                f"DECISION_REDERIVATION_FAILED:{exc}"
            ) from exc


def _entries(packet: dict, *, observation_root: Path | None = None) -> dict:
    refs = _source_refs(packet, observation_root=observation_root)
    universe_ref = refs.get("upbit_tradeable_universe_packet")
    market_ref = refs.get("upbit_market_evidence_packet")
    realtime_ref = refs.get("upbit_realtime_capture_run")

    universe_entry = None
    if universe_ref is not None:
        record = universe_ref["record"]
        universe_entry = {
            "date": universe_ref["path"].parent.name,
            "path": universe_ref["path"],
            "record": record,
            "packet": record.get("packet") if isinstance(record, dict) else None,
        }
    market_entry = None
    if market_ref is not None:
        market_record = market_ref["record"]
        market_entry = {
            # P4 v2 is stored under an immutable exact-generation directory
            # (YYYY-MM-DD-p3-<record-hash-prefix>).  The operational date is
            # the hash-pinned record's embedded snapshot_date, not that
            # directory name.  Using path.parent.name here would make a
            # valid same-day P3/P4 pair look mixed and silently remove all
            # market evidence from PAPER promotion.
            "date": market_record.get("snapshot_date"),
            "path": market_ref["path"],
            "record": market_record,
        }
    realtime_entry = None
    if realtime_ref is not None:
        realtime_entry = {
            "date": realtime_ref["path"].parent.name,
            "path": realtime_ref["path"],
            "record": realtime_ref["record"],
        }
    v4 = {}
    for role, name in (("crypto_paper_runtime_decision", "runtime_decision"), ("rotation_confirmation_packet", "rotation")):
        ref = refs.get(role)
        v4[name] = None if ref is None else {
            "date": ref["path"].parent.name, "path": ref["path"], "record": ref["record"],
        }
    return {
        "universe": universe_entry,
        "market_evidence": market_entry,
        "realtime": realtime_entry,
        **v4,
    }


def validate_decision_snapshot(
    value: object, *, expected_source_commit: str | None = None,
    observation_root: Path | None = None,
) -> dict:
    """Consume PR #441's canonical full-rederivation boundary."""
    if not isinstance(value, dict):
        raise CryptoPaperRuntimeBridgeError("DECISION_FIELDS_INVALID")
    source_commit = _require_sha40(
        value.get("source_commit"), "DECISION_SOURCE_COMMIT_INVALID",
    )
    if expected_source_commit is not None and source_commit != _require_sha40(
        expected_source_commit, "EXPECTED_SOURCE_COMMIT_INVALID"
    ):
        raise CryptoPaperRuntimeBridgeError("DECISION_SOURCE_COMMIT_MISMATCH")
    root = _safe_observation_root(observation_root)
    _preflight_source_paths(value, observation_root=root)
    validator = _decision_validator(root)
    try:
        return validator.validate_output(value)
    except validator.CryptoPaperDecisionSnapshotError as exc:
        raise CryptoPaperRuntimeBridgeError(f"DECISION_REDERIVATION_FAILED:{exc}") from exc


def load_and_validate_decision_snapshot(
    path: Path, *, expected_source_commit: str | None = None,
    observation_root: Path | None = None,
) -> dict:
    root = _safe_observation_root(observation_root)
    raw_path = Path(path)
    if not raw_path.is_absolute():
        raw_path = root / raw_path
    if raw_path.is_symlink():
        raise CryptoPaperRuntimeBridgeError("DECISION_PATH_INVALID")
    try:
        relative = raw_path.resolve().relative_to(root)
        checked_path = _safe_repo_path(str(relative), observation_root=root)
    except (ValueError, CryptoPaperRuntimeBridgeError) as exc:
        if isinstance(exc, CryptoPaperRuntimeBridgeError):
            raise CryptoPaperRuntimeBridgeError("DECISION_PATH_INVALID") from exc
        raise CryptoPaperRuntimeBridgeError("DECISION_PATH_INVALID")
    return validate_decision_snapshot(
        _read_json(checked_path), expected_source_commit=expected_source_commit,
        observation_root=root,
    )


def _derive_stage5_fixture_connection(
    envelope: dict,
    *,
    expected_envelope_sha256: str,
    expected_decision_packet_sha256: str,
    expected_decision_source_sha256: str,
    observation_root: Path | None = None,
) -> dict:
    """Call the merged Stage5 adapter without entering an operational ledger.

    The Stage4 source is consumed indirectly through the envelope's immutable
    repo-relative reference.  The exact source JSON is retained in the receipt
    so upstream identity, evaluation time, validity, and rejection reasons are
    not reduced to a bare status on the way into Stage5.
    """
    root = _safe_observation_root(observation_root)
    if not isinstance(envelope, dict) or not isinstance(envelope.get("decision"), dict):
        raise CryptoPaperRuntimeBridgeError("STAGE5_FIXTURE_ENVELOPE_INVALID")
    plan = envelope.get("plan")
    if (
        not isinstance(plan, dict)
        or not isinstance(plan.get("ledger_id"), str)
        or not plan["ledger_id"].startswith(STAGE5_FIXTURE_LEDGER_PREFIX)
    ):
        raise CryptoPaperRuntimeBridgeError("STAGE5_FIXTURE_LEDGER_NAMESPACE_INVALID")

    decision = envelope["decision"]
    source_ref = decision.get("source_ref")
    source_path = _safe_repo_path(source_ref, observation_root=root)
    expected_source = _require_sha256(
        expected_decision_source_sha256,
        "STAGE5_EXPECTED_DECISION_SOURCE_SHA_INVALID",
    )
    actual_source = _file_sha256(source_path)
    if actual_source != expected_source:
        raise CryptoPaperRuntimeBridgeError("STAGE5_DECISION_SOURCE_SHA_MISMATCH")
    if decision.get("source_sha256") != expected_source:
        raise CryptoPaperRuntimeBridgeError("STAGE5_DECISION_SOURCE_PIN_MISMATCH")

    try:
        result = STAGE5.build_result(
            envelope,
            expected_envelope_sha256=expected_envelope_sha256,
            expected_decision_packet_sha256=expected_decision_packet_sha256,
            expected_decision_source_sha256=expected_source,
        )
    except STAGE5.Stage5PaperEnvelopeError as exc:
        raise CryptoPaperRuntimeBridgeError(f"STAGE5_FIXTURE_ADAPTER_REJECTED:{exc}") from exc
    if (
        result.get("mode") != "PAPER_CONTRACT_FIXTURE_ONLY"
        or result.get("authority") != STAGE5.load_contract()["authority"]
        or any(value is not False for value in result["authority"].values())
    ):
        raise CryptoPaperRuntimeBridgeError("STAGE5_FIXTURE_AUTHORITY_INVALID")

    receipt = {
        "schema_version": STAGE5_CONNECTION_SCHEMA_VERSION,
        "mode": STAGE5_CONNECTION_MODE,
        "execution_state": "NOT_EXECUTED_FIXTURE_RESULT_ONLY",
        "stage4_lineage": {
            "decision_id": decision["decision_id"],
            "decision_status": decision["status"],
            "evaluated_at": decision["decided_at"],
            "source_ref": source_ref,
            "source_sha256": expected_source,
            "source_record": _read_json(source_path),
        },
        "stage5_result": result,
        "authority": copy.deepcopy(result["authority"]),
        "source_inputs": {
            "envelope": copy.deepcopy(envelope),
            "expected_envelope_sha256": expected_envelope_sha256,
            "expected_decision_packet_sha256": expected_decision_packet_sha256,
            "expected_decision_source_sha256": expected_source,
            "observation_root": str(root),
        },
    }
    receipt["packet_sha256"] = payload_sha256(receipt)
    return receipt


def build_stage5_fixture_connection(
    envelope: dict,
    *,
    expected_envelope_sha256: str,
    expected_decision_packet_sha256: str,
    expected_decision_source_sha256: str,
    observation_root: Path | None = None,
) -> dict:
    receipt = _derive_stage5_fixture_connection(
        envelope,
        expected_envelope_sha256=expected_envelope_sha256,
        expected_decision_packet_sha256=expected_decision_packet_sha256,
        expected_decision_source_sha256=expected_decision_source_sha256,
        observation_root=observation_root,
    )
    return validate_stage5_fixture_connection(
        receipt, expected_observation_root=observation_root,
    )


def validate_stage5_fixture_connection(
    value: object, *, expected_observation_root: Path | None = None,
) -> dict:
    fields = {
        "schema_version", "mode", "execution_state", "stage4_lineage",
        "stage5_result", "authority", "source_inputs", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperRuntimeBridgeError("STAGE5_CONNECTION_FIELDS_INVALID")
    if (
        value.get("schema_version") != STAGE5_CONNECTION_SCHEMA_VERSION
        or value.get("mode") != STAGE5_CONNECTION_MODE
        or value.get("execution_state") != "NOT_EXECUTED_FIXTURE_RESULT_ONLY"
    ):
        raise CryptoPaperRuntimeBridgeError("STAGE5_CONNECTION_IDENTITY_INVALID")
    if not isinstance(value.get("authority"), dict) or any(
        item is not False for item in value["authority"].values()
    ):
        raise CryptoPaperRuntimeBridgeError("STAGE5_CONNECTION_AUTHORITY_INVALID")
    claimed = _require_sha256(
        value.get("packet_sha256"), "STAGE5_CONNECTION_SHA_INVALID",
    )
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != claimed:
        raise CryptoPaperRuntimeBridgeError("STAGE5_CONNECTION_SHA_MISMATCH")
    source_inputs = value.get("source_inputs")
    if not isinstance(source_inputs, dict) or set(source_inputs) != {
        "envelope", "expected_envelope_sha256",
        "expected_decision_packet_sha256", "expected_decision_source_sha256",
        "observation_root",
    }:
        raise CryptoPaperRuntimeBridgeError("STAGE5_CONNECTION_SOURCE_INPUTS_INVALID")
    observation_root_value = source_inputs["observation_root"]
    if not isinstance(observation_root_value, str):
        raise CryptoPaperRuntimeBridgeError("STAGE5_CONNECTION_OBSERVATION_ROOT_INVALID")
    root = _safe_observation_root(Path(observation_root_value))
    if (
        expected_observation_root is not None
        and root != _safe_observation_root(expected_observation_root)
    ):
        raise CryptoPaperRuntimeBridgeError("STAGE5_CONNECTION_OBSERVATION_ROOT_MISMATCH")
    rebuilt = _derive_stage5_fixture_connection(
        source_inputs["envelope"],
        expected_envelope_sha256=source_inputs["expected_envelope_sha256"],
        expected_decision_packet_sha256=source_inputs[
            "expected_decision_packet_sha256"
        ],
        expected_decision_source_sha256=source_inputs[
            "expected_decision_source_sha256"
        ],
        observation_root=root,
    )
    if canonical_json(rebuilt) != canonical_json(value):
        raise CryptoPaperRuntimeBridgeError("STAGE5_CONNECTION_DERIVATION_MISMATCH")
    return copy.deepcopy(value)


def validate_runtime_config(value: object) -> dict:
    fields = {
        "schema_version", "approval_status", "approved_by", "approved_at",
        "ledger_id", "initial_cash_krw", "fee_rate", "queue_fraction",
        "order_type", "limit_price_source", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_FIELDS_INVALID")
    if (
        value.get("schema_version") != RUNTIME_CONFIG_SCHEMA_VERSION
        or value.get("approval_status") != RUNTIME_CONFIG_APPROVAL
        or value.get("authority") != AUTHORITY
    ):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_IDENTITY_INVALID")
    if not isinstance(value.get("approved_by"), str) or not value["approved_by"].strip():
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_APPROVED_BY_INVALID")
    _parse_utc(value.get("approved_at"), "RUNTIME_CONFIG_APPROVED_AT_INVALID")
    if not isinstance(value.get("ledger_id"), str):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_LEDGER_ID_INVALID")
    SIMULATOR._identifier(value["ledger_id"], "RUNTIME_CONFIG_LEDGER_ID_INVALID")
    initial_cash = _format_decimal(value.get("initial_cash_krw"), "RUNTIME_CONFIG_CASH_INVALID", positive=True)
    fee_rate = _format_decimal(value.get("fee_rate"), "RUNTIME_CONFIG_FEE_INVALID")
    queue_fraction = _format_decimal(
        value.get("queue_fraction"), "RUNTIME_CONFIG_QUEUE_INVALID", positive=True
    )
    if Decimal(fee_rate) >= 1 or Decimal(queue_fraction) > 1:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_RATE_RANGE_INVALID")
    order_type = value.get("order_type")
    limit_source = value.get("limit_price_source")
    if order_type not in {"LIMIT", "MARKET"}:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_ORDER_TYPE_INVALID")
    if order_type == "LIMIT" and limit_source not in {"ENTRY_ZONE_LOW", "ENTRY_ZONE_HIGH"}:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_LIMIT_SOURCE_INVALID")
    if order_type == "MARKET" and limit_source is not None:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_MARKET_LIMIT_SOURCE_FORBIDDEN")
    digest = _require_sha256(value.get("packet_sha256"), "RUNTIME_CONFIG_SHA_INVALID")
    normalized = copy.deepcopy(value)
    normalized.update({
        "initial_cash_krw": initial_cash,
        "fee_rate": fee_rate,
        "queue_fraction": queue_fraction,
    })
    unsigned = copy.deepcopy(normalized)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_SHA_MISMATCH")
    normalized["packet_sha256"] = digest
    return normalized


def build_runtime_config(**kwargs) -> dict:
    value = {
        "schema_version": RUNTIME_CONFIG_SCHEMA_VERSION,
        "approval_status": kwargs["approval_status"],
        "approved_by": kwargs["approved_by"],
        "approved_at": kwargs["approved_at"],
        "ledger_id": kwargs["ledger_id"],
        "initial_cash_krw": _format_decimal(kwargs["initial_cash_krw"], "RUNTIME_CONFIG_CASH_INVALID", positive=True),
        "fee_rate": _format_decimal(kwargs["fee_rate"], "RUNTIME_CONFIG_FEE_INVALID"),
        "queue_fraction": _format_decimal(kwargs["queue_fraction"], "RUNTIME_CONFIG_QUEUE_INVALID", positive=True),
        "order_type": kwargs["order_type"],
        "limit_price_source": kwargs.get("limit_price_source"),
        "authority": copy.deepcopy(AUTHORITY),
    }
    value["packet_sha256"] = payload_sha256(value)
    return validate_runtime_config(value)


def _normalize_open_position_risk(value: object) -> list[dict] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise CryptoPaperRuntimeBridgeError("OPEN_POSITION_RISK_INVALID")
    normalized = []
    seen = set()
    for row in value:
        if not isinstance(row, dict) or set(row) != {"market", "planned_loss_krw"}:
            raise CryptoPaperRuntimeBridgeError("OPEN_POSITION_RISK_FIELDS_INVALID")
        market = row.get("market")
        if (
            not isinstance(market, str)
            or MARKET_RE.fullmatch(market) is None
            or market in seen
        ):
            raise CryptoPaperRuntimeBridgeError("OPEN_POSITION_RISK_MARKET_INVALID")
        seen.add(market)
        normalized.append({
            "market": market,
            "planned_loss_krw": _format_decimal(
                row.get("planned_loss_krw"),
                "OPEN_POSITION_PLANNED_LOSS_INVALID",
                positive=True,
            ),
        })
    return sorted(normalized, key=lambda row: row["market"])


def _normalize_known_idempotency_keys(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise CryptoPaperRuntimeBridgeError("KNOWN_IDEMPOTENCY_KEYS_INVALID")
    if not all(isinstance(row, str) and row for row in value):
        raise CryptoPaperRuntimeBridgeError("KNOWN_IDEMPOTENCY_KEY_INVALID")
    return sorted(set(value))


def paper_account_state_from_ledger(
    account_state: dict, *, open_position_risk: list[dict],
) -> dict:
    checked = SIMULATOR.validate_account_state(account_state)
    if checked["total_nav"] is None:
        raise CryptoPaperRuntimeBridgeError("PAPER_ACCOUNT_NAV_UNKNOWN")
    total_nav = Decimal(checked["total_nav"])
    if total_nav <= 0:
        raise CryptoPaperRuntimeBridgeError("PAPER_ACCOUNT_NAV_NOT_POSITIVE")
    open_position_risk = _normalize_open_position_risk(open_position_risk)
    if open_position_risk is None:
        raise CryptoPaperRuntimeBridgeError("OPEN_POSITION_RISK_INVALID")
    risk_by_market = {}
    for row in open_position_risk:
        if not isinstance(row, dict) or set(row) != {"market", "planned_loss_krw"}:
            raise CryptoPaperRuntimeBridgeError("OPEN_POSITION_RISK_FIELDS_INVALID")
        market = row.get("market")
        if not isinstance(market, str) or MARKET_RE.fullmatch(market) is None or market in risk_by_market:
            raise CryptoPaperRuntimeBridgeError("OPEN_POSITION_RISK_MARKET_INVALID")
        risk_by_market[market] = Decimal(
            _format_decimal(
                row.get("planned_loss_krw"),
                "OPEN_POSITION_PLANNED_LOSS_INVALID",
                positive=True,
            )
        )
    positions = []
    for position in checked["positions"]:
        market = position["market"]
        if market not in risk_by_market:
            raise CryptoPaperRuntimeBridgeError(f"OPEN_POSITION_RISK_MISSING:{market}")
        positions.append({
            "market": market,
            "planned_loss_nav_fraction": _format_decimal(
                risk_by_market[market] / total_nav,
                "OPEN_POSITION_LOSS_FRACTION_INVALID",
            ),
            "portfolio_weight_nav_fraction": _format_decimal(
                Decimal(position["market_value"]) / total_nav,
                "OPEN_POSITION_WEIGHT_INVALID",
            ),
        })
    extra = sorted(set(risk_by_market) - {row["market"] for row in checked["positions"]})
    if extra:
        raise CryptoPaperRuntimeBridgeError("OPEN_POSITION_RISK_ORPHAN:" + ",".join(extra))
    return {"total_nav_krw": checked["total_nav"], "open_positions": positions}


def position_markets_from_ledger(ledger: dict) -> list[str]:
    """Return markets with positive virtual quantity after exact replay."""
    checked = SIMULATOR.validate_ledger(ledger)
    state = SIMULATOR._replay(checked["events"], SIMULATOR.load_contract())
    return sorted(
        market
        for market, position in state["positions"].items()
        if position["quantity"] > 0
    )


def _realtime_source(
    decision: dict, *, observation_root: Path | None = None,
) -> tuple[Path, dict]:
    refs = _source_refs(decision, observation_root=observation_root)
    entry = refs.get("upbit_realtime_capture_run")
    if entry is None:
        raise CryptoPaperRuntimeBridgeError("REALTIME_SOURCE_MISSING")
    record = entry["record"]
    if not isinstance(record, dict) or record.get("schema_version") != "upbit_realtime_capture_run/1":
        raise CryptoPaperRuntimeBridgeError("REALTIME_RECORD_INVALID")
    run = record.get("run")
    if not isinstance(run, dict) or record.get("source_sha256") != payload_sha256(run):
        raise CryptoPaperRuntimeBridgeError("REALTIME_RECORD_SHA_MISMATCH")
    if run.get("latest_public_messages_schema_version") != LATEST_PUBLIC_MESSAGES_SCHEMA_VERSION:
        raise CryptoPaperRuntimeBridgeError("REALTIME_LATEST_PUBLIC_MESSAGES_UNAVAILABLE")
    if not isinstance(run.get("latest_public_messages"), dict):
        raise CryptoPaperRuntimeBridgeError("REALTIME_LATEST_PUBLIC_MESSAGES_INVALID")
    return entry["path"], record


def is_per_market_decision(decision: dict) -> bool:
    """Whether a decision packet carries ratified per-market realtime freshness."""
    return (
        isinstance(decision, dict)
        and decision.get("schema_version") in DECISION.PER_MARKET_LAYOUT_SCHEMA_VERSIONS
    )


def market_freshness_view(decision: dict) -> dict[str, dict]:
    """Per-market realtime/floor/cap view of a validated per-market decision.

    ``/3`` records realtime status for every subscribed market
    (``subscribed_market_realtime``); an issued ``/2`` packet only has its
    candidate rows.  A market absent from both is not in this view and is
    treated as MISSING by every consumer.  A ``/1`` packet has no per-market
    view (empty result).
    """
    if not is_per_market_decision(decision):
        return {}
    view = {}
    for row in decision["candidates"]:
        realtime = row["realtime_freshness"]
        view[row["market"]] = {
            "candidate": True,
            "realtime_status": realtime["status"],
            "realtime_reasons": list(realtime.get("reasons") or []),
            "liquidity_floor_status": row["realtime_liquidity_floor"]["status"],
            "market_action_cap_reason": row["market_action_cap_reason"],
        }
    block = decision.get("realtime_per_market_freshness") or {}
    subscribed = block.get("subscribed_market_realtime")
    if subscribed is None:
        if decision["schema_version"] != DECISION.PER_MARKET_V2_OUTPUT_SCHEMA_VERSION:
            raise CryptoPaperRuntimeBridgeError("DECISION_SUBSCRIBED_MARKET_REALTIME_MISSING")
        subscribed = {}
    if not isinstance(subscribed, dict):
        raise CryptoPaperRuntimeBridgeError("DECISION_SUBSCRIBED_MARKET_REALTIME_INVALID")
    for market, realtime in sorted(subscribed.items()):
        if not isinstance(realtime, dict) or not isinstance(realtime.get("status"), str):
            raise CryptoPaperRuntimeBridgeError("DECISION_SUBSCRIBED_MARKET_REALTIME_INVALID")
        if market in view:
            if realtime["status"] != view[market]["realtime_status"]:
                raise CryptoPaperRuntimeBridgeError(
                    f"DECISION_MARKET_REALTIME_INCONSISTENT:{market}"
                )
            continue
        view[market] = {
            "candidate": False,
            "realtime_status": realtime["status"],
            "realtime_reasons": list(realtime.get("reasons") or []),
            "liquidity_floor_status": None,
            "market_action_cap_reason": None,
        }
    return dict(sorted(view.items()))


def market_realtime_status(decision: dict, market: str) -> tuple[str, list[str]]:
    """One market's own ratified realtime status (MISSING when not recorded)."""
    row = market_freshness_view(decision).get(market)
    if row is None:
        return DECISION.MISSING, [MARKET_NOT_SUBSCRIBED_REASON]
    return row["realtime_status"], list(row["realtime_reasons"])


def _latest_public_message(
    decision: dict, *, market: str, kind: str,
    observation_root: Path | None = None,
    per_market: bool | None = None,
) -> tuple[dict, Path, dict]:
    """Return one retained public message only when its evidence is usable.

    ``per_market`` (default: whether the decision is per-market) selects the
    ratified gate.  Per-market: the market's own ratified realtime status and
    that market's own ``freshness_by_kind[kind]`` must be FRESH; the aggregate
    realtime status and the run's gate ``overall_status`` (worst over every
    market) are telemetry only.  Aggregate (``/1`` decisions and replay of
    ``/2`` requests): the pre-ratification checks, unchanged.
    """
    if per_market is None:
        per_market = is_per_market_decision(decision)
    if per_market:
        status, reasons = market_realtime_status(decision, market)
        if status != DECISION.FRESH:
            detail = f":{','.join(reasons)}" if reasons else ""
            raise MarketEvidenceUnavailableError(
                f"MARKET_REALTIME_NOT_FRESH:{market}:{status}{detail}"
            )
    elif (decision.get("freshness_status") or {}).get("realtime") != DECISION.FRESH:
        raise MarketEvidenceUnavailableError("DECISION_REALTIME_FRESHNESS_NOT_RATIFIED_FRESH")
    path, record = _realtime_source(decision, observation_root=observation_root)
    run = record["run"]
    key = f"{kind}|-|{market}"
    row = run["latest_public_messages"].get(key)
    if row is None:
        raise MarketEvidenceUnavailableError(f"REALTIME_{kind.upper()}_MISSING:{market}")
    if not isinstance(row, dict) or set(row) != {
        "kind", "timeframe", "market", "received_at", "source_sha256", "raw"
    }:
        raise CryptoPaperRuntimeBridgeError(f"REALTIME_{kind.upper()}_MISSING:{market}")
    if row["kind"] != kind or row["timeframe"] is not None or row["market"] != market:
        raise CryptoPaperRuntimeBridgeError(f"REALTIME_{kind.upper()}_IDENTITY_INVALID:{market}")
    parsed = REALTIME.parse_message(row["raw"])
    if (
        parsed["kind"] != kind
        or parsed["market"] != market
        or parsed["payload_sha256"] != row["source_sha256"]
    ):
        raise CryptoPaperRuntimeBridgeError(f"REALTIME_{kind.upper()}_SHA_MISMATCH:{market}")
    try:
        received_at = dt.datetime.strptime(
            row["received_at"], "%Y-%m-%dT%H:%M:%S.%fZ",
        ).replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError) as exc:
        raise CryptoPaperRuntimeBridgeError(
            f"REALTIME_{kind.upper()}_RECEIVED_AT_INVALID:{market}"
        ) from exc
    if received_at > _parse_utc(
        decision.get("generated_at"), "DECISION_GENERATED_AT_INVALID",
    ):
        raise CryptoPaperRuntimeBridgeError(
            f"REALTIME_{kind.upper()}_FUTURE_DATED:{market}"
        )
    status = run.get("status") or {}
    if not per_market and status.get("overall_status") != "FRESH":
        raise MarketEvidenceUnavailableError("REALTIME_STATUS_NOT_FRESH")
    market_rows = [item for item in status.get("markets", []) if item.get("market") == market]
    if len(market_rows) != 1:
        raise MarketEvidenceUnavailableError(f"REALTIME_MARKET_STATUS_MISSING:{market}")
    if (market_rows[0].get("freshness_by_kind") or {}).get(kind, {}).get("status") != "FRESH":
        raise MarketEvidenceUnavailableError(f"REALTIME_{kind.upper()}_NOT_FRESH:{market}")
    return row, path, record


def latest_mark_prices_by_market(
    decision: dict, markets: list[str], *, observation_root: Path | None = None,
    per_market: bool | None = None,
) -> dict:
    """Per-market marks: a market without usable evidence is UNKNOWN, not fatal.

    Returns ``marks`` (FRESH markets only), ``mark_status`` (FRESH/UNKNOWN for
    every requested market), ``unavailable_reasons`` and the source reference
    plus a hash over the FRESH source rows only.  Tampered or malformed
    evidence still raises.
    """
    root = _safe_observation_root(observation_root)
    marks = {}
    mark_status = {}
    unavailable = {}
    source_rows = []
    for market in sorted(set(markets)):
        if not isinstance(market, str) or MARKET_RE.fullmatch(market) is None:
            raise CryptoPaperRuntimeBridgeError("MARK_MARKET_INVALID")
        try:
            row, path, _record = _latest_public_message(
                decision, market=market, kind="ticker", observation_root=root,
                per_market=per_market,
            )
        except MarketEvidenceUnavailableError as exc:
            mark_status[market] = DECISION.UNKNOWN
            unavailable[market] = str(exc)
            continue
        marks[market] = _format_decimal(row["raw"].get("trade_price"), "TICKER_PRICE_INVALID", positive=True)
        mark_status[market] = DECISION.FRESH
        source_rows.append({"path": str(path.relative_to(root)), "sha256": row["source_sha256"]})
    return {
        "marks": marks,
        "mark_status": mark_status,
        "unavailable_reasons": unavailable,
        "source_ref": "public://upbit/realtime/latest-ticker",
        "source_sha256": payload_sha256(source_rows),
    }


def latest_mark_prices(
    decision: dict, markets: list[str], *, observation_root: Path | None = None,
) -> tuple[dict[str, str], str, str]:
    """All-or-nothing marks (the simulator account view needs every position)."""
    result = latest_mark_prices_by_market(
        decision, markets, observation_root=observation_root,
    )
    if result["unavailable_reasons"]:
        market = sorted(result["unavailable_reasons"])[0]
        raise MarketEvidenceUnavailableError(result["unavailable_reasons"][market])
    return result["marks"], result["source_ref"], result["source_sha256"]


def orderbook_snapshot(
    decision: dict, *, market: str, observation_root: Path | None = None,
    per_market: bool | None = None,
) -> dict:
    root = _safe_observation_root(observation_root)
    row, path, _record = _latest_public_message(
        decision, market=market, kind="orderbook", observation_root=root,
        per_market=per_market,
    )
    raw = row["raw"]
    units = raw.get("orderbook_units")
    if not isinstance(units, list) or not units:
        raise MarketEvidenceUnavailableError(f"ORDERBOOK_UNITS_MISSING:{market}")
    asks = []
    bids = []
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            raise CryptoPaperRuntimeBridgeError(f"ORDERBOOK_UNIT_INVALID:{market}:{index}")
        asks.append({
            "price": _format_decimal(unit.get("ask_price"), "ORDERBOOK_ASK_PRICE_INVALID", positive=True),
            "quantity": _format_decimal(unit.get("ask_size"), "ORDERBOOK_ASK_SIZE_INVALID", positive=True),
        })
        bids.append({
            "price": _format_decimal(unit.get("bid_price"), "ORDERBOOK_BID_PRICE_INVALID", positive=True),
            "quantity": _format_decimal(unit.get("bid_size"), "ORDERBOOK_BID_SIZE_INVALID", positive=True),
        })
    try:
        received = dt.datetime.strptime(row["received_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=dt.timezone.utc
        )
    except (TypeError, ValueError) as exc:
        raise CryptoPaperRuntimeBridgeError("ORDERBOOK_RECEIVED_AT_INVALID") from exc
    captured_at = received.strftime("%Y-%m-%dT%H:%M:%SZ")
    return SIMULATOR.build_snapshot(
        snapshot_id=f"P9.{market}.{row['source_sha256'][:24].upper()}",
        market=market,
        captured_at=captured_at,
        freshness_status="FRESH",
        ask_levels=asks,
        bid_levels=bids,
        source_ref=f"{path.relative_to(root)}#latest_public_messages/{market}/orderbook",
        source_sha256=row["source_sha256"],
    )


def _promotion_packet(
    decision: dict, *, observation_root: Path | None = None,
) -> dict | None:
    entries = _entries(decision, observation_root=observation_root)
    universe_entry = entries["universe"]
    market_entry = entries["market_evidence"]
    if universe_entry is None or universe_entry["packet"] is None:
        return None
    if market_entry is None or market_entry["date"] != universe_entry["date"]:
        market_by_market = {}
    else:
        market_by_market = market_entry["record"].get("packets", {})
    regime = DECISION.build_regime_snapshot(decision["generated_at"], None)
    try:
        return PROMOTION.build_promotion_packet(
            universe_entry["packet"],
            regime,
            market_by_market,
            None,
            evaluation_as_of=universe_entry["packet"]["evaluation_as_of"],
        )
    except PROMOTION.CryptoCandidatePromotionError:
        return None


def _request_market_status(decision: dict) -> dict[str, dict]:
    """Per-market lineage carried by a ``/3`` request (no value field)."""
    status = {}
    for market, row in market_freshness_view(decision).items():
        entry_open = (
            row["candidate"]
            and row["realtime_status"] == DECISION.FRESH
            and row["liquidity_floor_status"] == DECISION.PER_MARKET.INCLUDED
            and row["market_action_cap_reason"] is None
        )
        status[market] = {
            "candidate": row["candidate"],
            "realtime_status": row["realtime_status"],
            "liquidity_floor_status": row["liquidity_floor_status"],
            "market_action_cap_reason": row["market_action_cap_reason"],
            "entry_state": ENTRY_OPEN if entry_open else ENTRY_CAPPED,
        }
    return status


def _per_market_entry_blocker(view: dict, market: str) -> str | None:
    """Why this market may not receive a new PAPER intent (None = open)."""
    row = view.get(market)
    if row is None:
        return f"MARKET_REALTIME_NOT_FRESH:{market}:{DECISION.MISSING}:{MARKET_NOT_SUBSCRIBED_REASON}"
    if not row["candidate"]:
        return f"MARKET_NOT_A_DECISION_CANDIDATE:{market}"
    if row["market_action_cap_reason"] is not None:
        return f"MARKET_ACTION_CAPPED:{market}:{row['market_action_cap_reason']}"
    if row["liquidity_floor_status"] != DECISION.PER_MARKET.INCLUDED:
        return f"MARKET_LIQUIDITY_FLOOR_NOT_INCLUDED:{market}:{row['liquidity_floor_status']}"
    if row["realtime_status"] != DECISION.FRESH:
        return f"MARKET_REALTIME_NOT_FRESH:{market}:{row['realtime_status']}"
    return None


def _carried_open_order_matches(
    decision: dict, checked_account: dict | None, *, source_root: Path,
    per_market: bool, legacy_request: bool,
) -> dict:
    """Match snapshots for orders carried from a prior ledger state (shared by /2-/4)."""
    match_snapshots = []
    blockers = []
    carried_open_order_ids = []
    if checked_account is None:
        return {"match_snapshots": match_snapshots, "blockers": blockers,
                "carried_open_order_ids": carried_open_order_ids}
    intent_by_order_id = {
        event["order_id"]: event["payload"]["intent"]
        for event in checked_account["source_ledger"]["events"]
        if event["event_type"] == "ORDER_SUBMITTED"
    }
    open_orders_by_market = {}
    for order in checked_account["orders"]:
        if order["status"] in {"OPEN", "PARTIALLY_FILLED"}:
            carried_open_order_ids.append(order["order_id"])
            open_orders_by_market.setdefault(order["market"], []).append(order)
    for market, orders in sorted(open_orders_by_market.items()):
        try:
            if per_market:
                # A fill creates a position the P10-11 account view must
                # mark FRESH.  Under per-market freshness a market's ticker
                # channel can lag its book, so a match also requires this
                # market's usable ticker (the aggregate gate implied it).
                _latest_public_message(
                    decision, market=market, kind="ticker",
                    observation_root=source_root, per_market=True,
                )
            snapshot = orderbook_snapshot(
                decision, market=market, observation_root=source_root,
                per_market=per_market,
            )
        except (
            CryptoPaperRuntimeBridgeError if legacy_request
            else MarketEvidenceUnavailableError
        ) as exc:
            # /3: only unavailable evidence is this market's blocker;
            # tampered or malformed evidence aborts the whole request.
            # /2 keeps its issued catch-all for byte-identical replay.
            blockers.append(f"MATCH_SNAPSHOT_UNAVAILABLE:{market}:{exc}")
            continue
        captured = _parse_utc(snapshot["captured_at"], "ORDERBOOK_CAPTURED_AT_INVALID")
        eligible_order_ids = []
        for order in orders:
            intent = intent_by_order_id.get(order["order_id"])
            if intent is None:
                raise CryptoPaperRuntimeBridgeError(
                    f"OPEN_ORDER_INTENT_MISSING:{order['order_id']}"
                )
            submitted = _parse_utc(
                intent["submitted_at"], "OPEN_ORDER_SUBMITTED_AT_INVALID"
            )
            expires = _parse_utc(intent["expires_at"], "OPEN_ORDER_EXPIRY_INVALID")
            if captured <= submitted:
                blockers.append(f"MATCH_SNAPSHOT_NOT_AFTER_OPEN_ORDER:{order['order_id']}")
            elif captured >= expires:
                blockers.append(f"OPEN_ORDER_EXPIRY_REACHED:{order['order_id']}")
            else:
                eligible_order_ids.append(order["order_id"])
        if eligible_order_ids:
            match_snapshots.append({
                "market": market,
                "order_ids": sorted(eligible_order_ids),
                "snapshot": snapshot,
            })
    return {"match_snapshots": match_snapshots, "blockers": blockers,
            "carried_open_order_ids": carried_open_order_ids}


def simulator_market_regime_status(value: object) -> str:
    """Map a market state onto the simulator vocabulary (build plan C8).

    ``config/crypto_paper_simulator_contract.json`` accepts only
    PASS/FAIL/UNKNOWN/NOT_EVALUATED.  RISK_ON and NEUTRAL permit new buys under
    allocation v2 (PASS), RISK_OFF and STRESS deny them (FAIL), UNKNOWN stays
    UNKNOWN; a value already in the simulator vocabulary passes through.
    """
    contract_values = SIMULATOR.load_contract()["market_regime_statuses"]
    if value in contract_values:
        return value
    mapping = {"RISK_ON": "PASS", "NEUTRAL": "PASS", "RISK_OFF": "FAIL", "STRESS": "FAIL"}
    if value not in mapping:
        raise CryptoPaperRuntimeBridgeError(f"MARKET_REGIME_STATUS_UNMAPPABLE:{value}")
    return mapping[value]


def _derive_runtime_request(
    decision: dict,
    *,
    expected_source_commit: str,
    public_code_commit_sha: str | None = None,
    observation_root: Path | None = None,
    observation_commit_sha: str | None = None,
    account_state: dict | None,
    open_position_risk: list[dict] | None,
    runtime_config: dict | None,
    known_idempotency_keys=None,
    request_schema_version: str = REQUEST_SCHEMA_VERSION,
    **v4_inputs,
) -> dict:
    if request_schema_version == V4_REQUEST_SCHEMA_VERSION:
        return _derive_runtime_request_v4(
            decision,
            expected_source_commit=expected_source_commit,
            public_code_commit_sha=public_code_commit_sha,
            observation_root=observation_root,
            observation_commit_sha=observation_commit_sha,
            account_state=account_state,
            open_position_risk=open_position_risk,
            runtime_config=runtime_config,
            known_idempotency_keys=known_idempotency_keys,
            **v4_inputs,
        )
    if v4_inputs:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_V4_INPUTS_REQUIRE_V4_REQUEST")
    if isinstance(decision, dict) and decision.get("schema_version") == DECISION.V4_OUTPUT_SCHEMA_VERSION:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_V4_REQUIRED_FOR_DECISION_V4")
    if request_schema_version not in REQUEST_SCHEMA_VERSIONS:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_SCHEMA_VERSION_UNSUPPORTED")
    legacy_request = request_schema_version == LEGACY_REQUEST_SCHEMA_VERSION
    source_root = _safe_observation_root(observation_root)
    decision = validate_decision_snapshot(
        decision, expected_source_commit=expected_source_commit,
        observation_root=source_root,
    )
    if legacy_request and decision["schema_version"] != DECISION.LEGACY_OUTPUT_SCHEMA_VERSION:
        # No /2 request was ever issued for a per-market decision (the runtime
        # pin that emitted /2 could not read /2 or /3 decisions).  Accepting one
        # would let a relabelled request downgrade a per-market decision to the
        # aggregate gate, so it fails closed.
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_LEGACY_SCHEMA_REQUIRES_V1_DECISION")
    # A /2 request is replayed exactly as issued: aggregate freshness gate and a
    # whole-request abort on an unusable entry orderbook.  A /3 request judges
    # each market by its own ratified freshness when the decision carries it.
    per_market = (not legacy_request) and is_per_market_decision(decision)
    view = market_freshness_view(decision) if per_market else {}
    code_commit = _require_sha40(
        public_code_commit_sha or expected_source_commit,
        "PUBLIC_CODE_COMMIT_INVALID",
    )
    observation_commit = _require_sha40(
        observation_commit_sha or code_commit,
        "OBSERVATION_COMMIT_INVALID",
    )
    config = validate_runtime_config(runtime_config) if runtime_config is not None else None
    if config is not None and _parse_utc(
        config["approved_at"], "RUNTIME_CONFIG_APPROVED_AT_INVALID",
    ) > _parse_utc(decision["generated_at"], "DECISION_GENERATED_AT_INVALID"):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_APPROVED_AFTER_DECISION")
    checked_account = (
        SIMULATOR.validate_account_state(account_state)
        if account_state is not None else None
    )
    if (
        legacy_request and checked_account is not None
        and checked_account["schema_version"] != SIMULATOR.load_contract()["account_state_schema_version"]
    ):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_LEGACY_SCHEMA_REQUIRES_V1_ACCOUNT")
    normalized_risk = _normalize_open_position_risk(open_position_risk)
    normalized_keys = _normalize_known_idempotency_keys(known_idempotency_keys)
    missing = []
    if checked_account is None:
        missing.append("PAPER_ACCOUNT_STATE")
    if normalized_risk is None:
        missing.append("OPEN_POSITION_RISK")
    if config is None:
        missing.append("USER_RATIFIED_RUNTIME_CONFIG")

    promotion = _promotion_packet(decision, observation_root=source_root)
    eligibility = None
    requests = []
    match_snapshots = []
    blockers = []
    carried_open_order_ids = []
    new_intent_allocation_blocked = False
    market_blocked = False

    # A current run's retained orderbook predates the decision assembled at
    # the tail of that run, so it may support the decision but cannot fill a
    # newly submitted order.  It can only match orders carried from a prior
    # ledger state.  New intents wait for a later capture.
    carried = _carried_open_order_matches(
        decision, checked_account, source_root=source_root,
        per_market=per_market, legacy_request=legacy_request,
    )
    match_snapshots.extend(carried["match_snapshots"])
    blockers.extend(carried["blockers"])
    carried_open_order_ids.extend(carried["carried_open_order_ids"])
    nav_unknown_markets = (
        sorted(row["market"] for row in checked_account["positions"] if row.get("mark_status") == "UNKNOWN")
        if checked_account is not None and checked_account["total_nav"] is None else []
    )
    if promotion is None:
        blockers.append("PROMOTION_PACKET_UNAVAILABLE")
    elif missing:
        blockers.extend("RUNTIME_INPUT_MISSING:" + item for item in missing)
    elif nav_unknown_markets:
        # A per-market account view with an UNKNOWN-valued position has no
        # NAV, so no new entry can be sized; carried matches still proceed.
        blockers.append("PAPER_ACCOUNT_NAV_UNKNOWN:" + ",".join(nav_unknown_markets))
    else:
        paper_account = paper_account_state_from_ledger(
            checked_account, open_position_risk=normalized_risk or [],
        )
        eligibility = ELIGIBILITY.build_eligibility_packet(
            promotion,
            evaluation_as_of=promotion["evaluation_as_of"],
            paper_account_state=paper_account,
            fee_rate=config["fee_rate"],
            known_idempotency_keys=normalized_keys,
        )
        eligibility = ELIGIBILITY.validate_output(eligibility)
        eligible_rows = [
            row for row in eligibility["candidates"]
            if row["eligibility_state"] == "PAPER_BUY_ELIGIBLE"
        ]
        if per_market:
            # The P5-09 rebuild here does not see the decision's per-market
            # caps, so a STALE/MISSING/UNKNOWN, floor-excluded or otherwise
            # capped market is removed before allocation.  Other markets are
            # evaluated normally (user ratification, action_cap rule).
            open_rows = []
            for row in eligible_rows:
                blocker = _per_market_entry_blocker(view, row["market"])
                if blocker is None:
                    open_rows.append(row)
                else:
                    market_blocked = True
                    blockers.append(blocker)
            eligible_rows = open_rows
        if eligible_rows and carried_open_order_ids:
            new_intent_allocation_blocked = True
            blockers.append(
                "NEW_INTENT_BLOCKED_PENDING_OPEN_ORDERS:"
                + ",".join(sorted(carried_open_order_ids))
            )
            eligible_rows = []
        elif len(eligible_rows) > 1:
            new_intent_allocation_blocked = True
            blockers.append(
                "MULTIPLE_ELIGIBLE_CANDIDATES_REQUIRE_ALLOCATION_POLICY:"
                + ",".join(sorted(row["market"] for row in eligible_rows))
            )
            eligible_rows = []
        for row in eligible_rows:
            draft = row["order_draft"]
            submitted_at = decision["generated_at"]
            if _parse_utc(draft["expires_at"], "ORDER_DRAFT_EXPIRY_INVALID") <= _parse_utc(
                submitted_at, "DECISION_GENERATED_AT_INVALID"
            ):
                blockers.append(f"ORDER_DRAFT_EXPIRED:{row['market']}")
                continue
            if legacy_request:
                snapshot = orderbook_snapshot(
                    decision, market=row["market"], observation_root=source_root,
                    per_market=False,
                )
            else:
                try:
                    snapshot = orderbook_snapshot(
                        decision, market=row["market"], observation_root=source_root,
                        per_market=per_market,
                    )
                except MarketEvidenceUnavailableError as exc:
                    # One market's missing/stale book is that market's blocker;
                    # it never aborts carried matches or other markets.
                    market_blocked = True
                    blockers.append(f"ENTRY_SNAPSHOT_UNAVAILABLE:{row['market']}:{exc}")
                    continue
            limit_price = None
            if config["order_type"] == "LIMIT":
                key = "low" if config["limit_price_source"] == "ENTRY_ZONE_LOW" else "high"
                limit_price = draft["entry_zone"][key]
            guard = draft["duplicate_guard_key"]
            order_id = f"PAPER.BUY.{row['market']}.{hashlib.sha256(guard.encode()).hexdigest()[:24].upper()}"
            intent = SIMULATOR.build_intent(
                order_id=order_id,
                idempotency_key=guard,
                market=row["market"],
                side="BUY",
                order_type=config["order_type"],
                quantity=draft["quantity"],
                limit_price=limit_price,
                fee_rate=config["fee_rate"],
                queue_fraction=config["queue_fraction"],
                submitted_at=submitted_at,
                expires_at=draft["expires_at"],
                market_regime_status=simulator_market_regime_status(
                    promotion["source_packets"]["regime"].get("regime", "UNKNOWN")
                ),
                source_plan_ref=f"public://crypto-paper-decision/{decision['generation_id']}",
                source_plan_sha256=decision["payload_sha256"],
                source_evidence_ref=snapshot["source_ref"],
                source_evidence_sha256=snapshot["source_sha256"],
            )
            requests.append({
                "market": row["market"],
                "planned_loss_krw": draft["planned_loss_krw"],
                "order_draft": copy.deepcopy(draft),
                "intent": intent,
                "source_snapshot": snapshot,
            })

    if requests:
        status = "PAPER_INTENTS_READY"
    elif match_snapshots:
        status = "PAPER_MATCHES_READY"
    elif promotion is None:
        status = "WAIT_PROMOTION_UNAVAILABLE"
    elif missing:
        status = "WAIT_RUNTIME_INPUTS_MISSING"
    elif nav_unknown_markets:
        status = "WAIT_ACCOUNT_NAV_UNKNOWN"
    elif new_intent_allocation_blocked:
        status = "WAIT_ALLOCATION_POLICY"
    elif market_blocked:
        status = "WAIT_MARKET_EVIDENCE_OR_CAP"
    else:
        status = "NO_ELIGIBLE_CANDIDATE"
    packet = {
        "schema_version": request_schema_version,
        "mode": PRIVATE_RUNTIME_MODE,
        "status": status,
        "observed_at": decision["generated_at"],
        "decision_generation_id": decision["generation_id"],
        "decision_payload_sha256": decision["payload_sha256"],
        "decision_source_commit_sha": decision["source_commit"],
        "public_code_commit_sha": code_commit,
        "observation_commit_sha": observation_commit,
        "runtime_config_sha256": config["packet_sha256"] if config is not None else None,
        "eligibility": eligibility,
        "requests": requests,
        "match_snapshots": match_snapshots,
        "blockers": sorted(blockers),
        "authority": copy.deepcopy(AUTHORITY),
        "source_inputs": {
            "decision": copy.deepcopy(decision),
            "public_code_commit_sha": code_commit,
            "observation_root": str(source_root),
            "observation_commit_sha": observation_commit,
            "account_state": copy.deepcopy(checked_account),
            "open_position_risk": copy.deepcopy(normalized_risk),
            "runtime_config": copy.deepcopy(config),
            "known_idempotency_keys": normalized_keys,
        },
    }
    if not legacy_request:
        packet["decision_schema_version"] = decision["schema_version"]
        packet["freshness_mode"] = (
            PER_MARKET_FRESHNESS_MODE if per_market else AGGREGATE_FRESHNESS_MODE
        )
        packet["market_status"] = _request_market_status(decision) if per_market else {}
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def build_runtime_request(
    decision: dict,
    *,
    expected_source_commit: str,
    public_code_commit_sha: str | None = None,
    observation_root: Path | None = None,
    observation_commit_sha: str | None = None,
    account_state: dict | None,
    open_position_risk: list[dict] | None,
    runtime_config: dict | None,
    known_idempotency_keys=None,
    **v4_inputs,
) -> dict:
    """``/3`` for decisions /1-/3 (unchanged); ``/4`` for a decision /4, which
    additionally takes ``allocation_envelope``, ``recorded_session_budget``,
    ``position_fills`` and ``exit_intents`` (see ``_derive_runtime_request_v4``)."""
    v4 = isinstance(decision, dict) and decision.get("schema_version") == DECISION.V4_OUTPUT_SCHEMA_VERSION
    packet = _derive_runtime_request(
        decision,
        expected_source_commit=expected_source_commit,
        public_code_commit_sha=public_code_commit_sha,
        observation_root=observation_root,
        observation_commit_sha=observation_commit_sha,
        account_state=account_state,
        open_position_risk=open_position_risk,
        runtime_config=runtime_config,
        known_idempotency_keys=known_idempotency_keys,
        **({"request_schema_version": V4_REQUEST_SCHEMA_VERSION} if v4 else {}),
        **v4_inputs,
    )
    return validate_runtime_request(
        packet,
        expected_public_code_commit_sha=packet["public_code_commit_sha"],
        expected_observation_root=observation_root,
        expected_observation_commit_sha=packet["observation_commit_sha"],
    )


LEGACY_REQUEST_FIELDS = frozenset({
    "schema_version", "mode", "status", "observed_at",
    "decision_generation_id", "decision_payload_sha256",
    "decision_source_commit_sha", "public_code_commit_sha",
    "observation_commit_sha",
    "runtime_config_sha256", "eligibility",
    "requests", "match_snapshots", "blockers", "authority", "source_inputs",
    "packet_sha256",
})
REQUEST_FIELDS = LEGACY_REQUEST_FIELDS | {
    "decision_schema_version", "freshness_mode", "market_status",
}


def validate_runtime_request(
    value: object, *, expected_public_code_commit_sha: str | None = None,
    expected_observation_root: Path | None = None,
    expected_observation_commit_sha: str | None = None,
) -> dict:
    schema_version = value.get("schema_version") if isinstance(value, dict) else None
    if schema_version == V4_REQUEST_SCHEMA_VERSION:
        return _validate_runtime_request_v4(
            value,
            expected_public_code_commit_sha=expected_public_code_commit_sha,
            expected_observation_root=expected_observation_root,
            expected_observation_commit_sha=expected_observation_commit_sha,
        )
    fields = LEGACY_REQUEST_FIELDS if schema_version == LEGACY_REQUEST_SCHEMA_VERSION else REQUEST_FIELDS
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_FIELDS_INVALID")
    if (
        schema_version not in REQUEST_SCHEMA_VERSIONS
        or value.get("mode") != PRIVATE_RUNTIME_MODE
        or value.get("authority") != AUTHORITY
    ):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_IDENTITY_INVALID")
    if schema_version == REQUEST_SCHEMA_VERSION:
        if (
            value.get("freshness_mode") not in {PER_MARKET_FRESHNESS_MODE, AGGREGATE_FRESHNESS_MODE}
            or not isinstance(value.get("market_status"), dict)
            or value.get("decision_schema_version") not in DECISION.OUTPUT_SCHEMA_VERSIONS
        ):
            raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_PER_MARKET_FIELDS_INVALID")
    _parse_utc(value.get("observed_at"), "RUNTIME_REQUEST_OBSERVED_AT_INVALID")
    _require_sha256(value.get("decision_generation_id"), "RUNTIME_REQUEST_GENERATION_INVALID")
    _require_sha256(value.get("decision_payload_sha256"), "RUNTIME_REQUEST_DECISION_SHA_INVALID")
    _require_sha40(value.get("decision_source_commit_sha"), "RUNTIME_REQUEST_DECISION_COMMIT_INVALID")
    public_commit = _require_sha40(
        value.get("public_code_commit_sha"), "RUNTIME_REQUEST_PUBLIC_COMMIT_INVALID",
    )
    if expected_public_code_commit_sha is not None and public_commit != _require_sha40(
        expected_public_code_commit_sha, "EXPECTED_PUBLIC_CODE_COMMIT_INVALID",
    ):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_PUBLIC_COMMIT_MISMATCH")
    observation_commit = _require_sha40(
        value.get("observation_commit_sha"), "RUNTIME_REQUEST_OBSERVATION_COMMIT_INVALID",
    )
    if (
        expected_observation_commit_sha is not None
        and observation_commit != _require_sha40(
            expected_observation_commit_sha,
            "EXPECTED_OBSERVATION_COMMIT_INVALID",
        )
    ):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_OBSERVATION_COMMIT_MISMATCH")
    if value.get("runtime_config_sha256") is not None:
        _require_sha256(value["runtime_config_sha256"], "RUNTIME_REQUEST_CONFIG_SHA_INVALID")
    eligibility = value.get("eligibility")
    if eligibility is not None:
        ELIGIBILITY.validate_output(eligibility)
    requests = value.get("requests")
    if not isinstance(requests, list):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_ROWS_INVALID")
    for index, row in enumerate(requests):
        if not isinstance(row, dict) or set(row) != {
            "market", "planned_loss_krw", "order_draft", "intent", "source_snapshot"
        }:
            raise CryptoPaperRuntimeBridgeError(f"RUNTIME_REQUEST_ROW_FIELDS_INVALID:{index}")
        intent = SIMULATOR.validate_intent(row["intent"])
        snapshot = SIMULATOR.validate_snapshot(row["source_snapshot"])
        if intent["market"] != row["market"] or snapshot["market"] != row["market"]:
            raise CryptoPaperRuntimeBridgeError(f"RUNTIME_REQUEST_ROW_MARKET_MISMATCH:{index}")
        _format_decimal(row["planned_loss_krw"], "RUNTIME_REQUEST_PLANNED_LOSS_INVALID")
    match_snapshots = value.get("match_snapshots")
    if not isinstance(match_snapshots, list):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_MATCH_SNAPSHOTS_INVALID")
    for index, row in enumerate(match_snapshots):
        if not isinstance(row, dict) or set(row) != {"market", "order_ids", "snapshot"}:
            raise CryptoPaperRuntimeBridgeError(
                f"RUNTIME_REQUEST_MATCH_SNAPSHOT_FIELDS_INVALID:{index}"
            )
        snapshot = SIMULATOR.validate_snapshot(row["snapshot"])
        if snapshot["market"] != row["market"]:
            raise CryptoPaperRuntimeBridgeError(
                f"RUNTIME_REQUEST_MATCH_SNAPSHOT_MARKET_MISMATCH:{index}"
            )
        order_ids = row["order_ids"]
        if (
            not isinstance(order_ids, list)
            or not order_ids
            or order_ids != sorted(set(order_ids))
            or not all(isinstance(order_id, str) and order_id for order_id in order_ids)
        ):
            raise CryptoPaperRuntimeBridgeError(
                f"RUNTIME_REQUEST_MATCH_ORDER_IDS_INVALID:{index}"
            )
    blockers = value.get("blockers")
    if not isinstance(blockers, list) or not all(isinstance(row, str) and row for row in blockers):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_BLOCKERS_INVALID")
    if value["status"] == "PAPER_INTENTS_READY" and not requests:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_READY_WITHOUT_INTENTS")
    if requests and value["status"] != "PAPER_INTENTS_READY":
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_INTENTS_WITH_NONREADY_STATUS")
    if value["status"] == "PAPER_MATCHES_READY" and not match_snapshots:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_MATCH_READY_WITHOUT_SNAPSHOT")
    if match_snapshots and not requests and value["status"] != "PAPER_MATCHES_READY":
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_MATCH_WITH_NONREADY_STATUS")
    digest = _require_sha256(value.get("packet_sha256"), "RUNTIME_REQUEST_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_SHA_MISMATCH")
    source_inputs = value.get("source_inputs")
    if not isinstance(source_inputs, dict) or set(source_inputs) != {
        "decision", "public_code_commit_sha", "observation_root",
        "observation_commit_sha", "account_state",
        "open_position_risk", "runtime_config", "known_idempotency_keys",
    }:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_SOURCE_INPUTS_INVALID")
    if source_inputs["public_code_commit_sha"] != public_commit:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_SOURCE_COMMIT_MISMATCH")
    if source_inputs["observation_commit_sha"] != observation_commit:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_OBSERVATION_SOURCE_COMMIT_MISMATCH")
    source_root = _safe_observation_root(Path(source_inputs["observation_root"]))
    if (
        expected_observation_root is not None
        and source_root != _safe_observation_root(expected_observation_root)
    ):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_OBSERVATION_ROOT_MISMATCH")
    decision = source_inputs["decision"]
    if not isinstance(decision, dict):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_SOURCE_DECISION_INVALID")
    rebuilt = _derive_runtime_request(
        decision,
        expected_source_commit=decision.get("source_commit"),
        public_code_commit_sha=public_commit,
        observation_root=source_root,
        observation_commit_sha=observation_commit,
        account_state=source_inputs["account_state"],
        open_position_risk=source_inputs["open_position_risk"],
        runtime_config=source_inputs["runtime_config"],
        known_idempotency_keys=source_inputs["known_idempotency_keys"],
        request_schema_version=schema_version,
    )
    if canonical_json(rebuilt) != canonical_json(value):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_DERIVATION_MISMATCH")
    return copy.deepcopy(value)


# ===========================================================================
# crypto_paper_runtime_request/4 (crypto PAPER wiring v2, build plan PR3 item 3)
# ===========================================================================
#
# For a ``crypto_paper_decision_snapshot_packet/4`` only.  Differences from /3:
#
# * P5-08 is rebuilt under contract/3 from the decision's retained runtime
#   decision and rotation confirmation sources; P5-09 runs contract/3.
# * Several eligible candidates share one session budget
#   (``portfolio/paper_session_budget.py``: B = Room/3, equal split, water
#   filling) instead of the /3 multi-candidate cancel.
# * A carried open order no longer blocks every new intent.  New buys stop
#   only when this market and session already hold an allocation record from
#   an earlier decision (canon 2-3/2-4, build plan C2); open buy orders are
#   reservations inside the budget.
# * Orders are marketable limits on the decision snapshot book with the
#   RULE.EXEC.QUALITY_LAYERS.V1 actual-notional slippage threshold: the
#   largest quantity whose consumption VWAP stays within the threshold of the
#   best price, limit = the worst level that quantity needs (D3).
# * Open ``paper_exit_intent/1`` records (PR #756 exit policy) become SELL
#   requests with the same sizing; a market with an open exit intent gets no
#   new buy.
# * The simulator market regime status is mapped from the market state
#   (``simulator_market_regime_status``).
#
# Pure and offline, like the rest of this module.

V4_REQUEST_EXTRA_FIELDS = frozenset({"session_budget_record", "sell_requests", "cancel_requests", "wiring"})
V4_CANCEL_ROW_FIELDS = frozenset({"market", "order_id", "exit_intent_id", "reason_code"})
V4_REQUEST_FIELDS = REQUEST_FIELDS | V4_REQUEST_EXTRA_FIELDS
V4_SOURCE_INPUT_FIELDS = frozenset({
    "decision", "public_code_commit_sha", "observation_root", "observation_commit_sha",
    "account_state", "open_position_risk", "runtime_config", "known_idempotency_keys",
    "allocation_envelope", "recorded_session_budget", "position_fills", "exit_intents",
})
V4_REQUEST_ROW_FIELDS = frozenset({"market", "planned_loss", "order_draft", "intent", "source_snapshot", "execution_sizing"})
V4_SELL_ROW_FIELDS = frozenset({"market", "exit_intent_id", "exit_reason_code", "intent", "source_snapshot", "execution_sizing"})
EXIT_INTENT_INPUT_FIELDS = frozenset({"intent", "remaining_quantity"})
ORDER_PRICE_RULE = "MARKETABLE_LIMIT_DECISION_SNAPSHOT_BOOK_VWAP_WITHIN_THRESHOLD"
NAV_MODE = "CRYPTO_LEDGER_ONLY_WHILE_KR_US_HOLD_NOTHING"
RULE_QUALITY_LAYERS = "RULE.EXEC.QUALITY_LAYERS.V1"

_V4_MODULES: dict = {}


def _v4_modules() -> dict:
    if not _V4_MODULES:
        from portfolio import paper_execution_core as core_module
        from portfolio import paper_session_budget as budget_module
        from portfolio import paper_allocation_envelope as envelope_module
        exit_module = _load("crypto_paper_runtime_exit_policy", ROOT / "portfolio" / "paper_exit_policy_v1.py")
        _V4_MODULES.update(CORE=core_module, BUDGET=budget_module, ENVELOPE=envelope_module, EXIT=exit_module)
    return _V4_MODULES


def _core_call(function, *args, **kwargs):
    modules = _v4_modules()
    try:
        return function(*args, **kwargs)
    except (modules["CORE"].PaperExecutionCoreError, modules["EXIT"].PaperExitPolicyError) as exc:
        raise CryptoPaperRuntimeBridgeError(f"EXECUTION_CORE_REJECTED:{exc}") from exc


def crypto_slippage_rule(core) -> dict:
    """RULE.EXEC.QUALITY_LAYERS.V1 ``crypto_slippage`` read from the registry."""
    row = core.context.rules[RULE_QUALITY_LAYERS]
    value = row["key_parameters"]["crypto_slippage"]["value"]
    if value.get("basis") != "ACTUAL_ORDER_NOTIONAL" or value.get("action") != "REDUCE_QUANTITY" \
            or type(value.get("threshold_bp")) is not int or value["threshold_bp"] <= 0:
        raise CryptoPaperRuntimeBridgeError("CRYPTO_SLIPPAGE_RULE_UNEXPECTED")
    return {"rule_id": RULE_QUALITY_LAYERS, "version": row["version"], **value}


def _fraction_text(value: Fraction) -> str:
    value = Fraction(value)
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _decimal_text_exact(value: Fraction, scale: int) -> str:
    """A quantity already floored to ``10**-scale`` as a plain decimal string."""
    scaled = value * (10 ** scale)
    if scaled.denominator != 1:
        raise CryptoPaperRuntimeBridgeError("QUANTITY_NOT_ON_SCALE")
    return _format_decimal(Decimal(scaled.numerator).scaleb(-scale), "QUANTITY_FORMAT_INVALID")


def _floor_scale(value: Fraction, scale: int) -> Fraction:
    unit = 10 ** scale
    return Fraction((value.numerator * unit) // value.denominator, unit)


def _walk(levels: list, *, side: str, quantity: Fraction) -> tuple:
    remaining, gross, limit = quantity, Fraction(0), None
    for price, capacity in levels:
        if remaining <= 0:
            break
        take = min(capacity, remaining)
        if take <= 0:
            continue
        gross += price * take
        remaining -= take
        limit = price
    return gross, limit


def crypto_quantity_decimal_places() -> int:
    """Crypto order quantity step (canon 2-2 allocation step 4: coin quantities
    at 8 decimal places), carried by config/crypto_paper_wiring_v2.json."""
    value = DECISION.load_wiring_config()["cio_interpretations"]["quantity_step"]["decimal_places"]
    if type(value) is not int or not 0 <= value <= SIMULATOR.load_contract()["decimal_scale"]:
        raise CryptoPaperRuntimeBridgeError("QUANTITY_DECIMAL_PLACES_INVALID")
    return value


def marketable_limit_sizing(
    snapshot: dict, *, side: str, fee_rate: str, queue_fraction: str, threshold_bp: int,
    budget_krw: Fraction | None = None, max_quantity: Fraction | None = None,
    quantity_decimal_places: int | None = None,
) -> dict:
    """Largest quantity on the decision snapshot book whose consumption VWAP
    stays within ``threshold_bp`` of the best price, capped by the budget
    (limit x quantity x (1 + fee) <= budget, BUY) or the quantity to sell;
    the limit price is the worst level that quantity needs.

    Level capacity is floor(level quantity x queue_fraction) at the simulator
    decimal scale, exactly as the simulator fills.  The order quantity is
    floored to ``quantity_decimal_places`` (default: the crypto quantity step);
    a SELL whose whole remaining quantity fits the bounds sells it exactly, so
    no sub-step remainder is left behind.
    """
    checked = SIMULATOR.validate_snapshot(snapshot)
    capacity_scale = SIMULATOR.load_contract()["decimal_scale"]
    scale = crypto_quantity_decimal_places() if quantity_decimal_places is None else quantity_decimal_places
    if side not in ("BUY", "SELL"):
        raise CryptoPaperRuntimeBridgeError("SIZING_SIDE_INVALID")
    if (side == "BUY") == (budget_krw is None):
        raise CryptoPaperRuntimeBridgeError("SIZING_BOUND_INVALID")
    fee = Fraction(Decimal(_format_decimal(fee_rate, "SIZING_FEE_INVALID")))
    queue = Fraction(Decimal(_format_decimal(queue_fraction, "SIZING_QUEUE_INVALID", positive=True)))
    raw_levels = checked["ask_levels"] if side == "BUY" else checked["bid_levels"]
    levels = [
        (Fraction(Decimal(row["price"])), _floor_scale(Fraction(Decimal(row["quantity"])) * queue, capacity_scale))
        for row in raw_levels
    ]
    best = levels[0][0]
    move = Fraction(threshold_bp, 10000)
    bound = best * (1 + move) if side == "BUY" else best * (1 - move)

    def largest(with_slippage_bound: bool) -> Fraction:
        filled, gross = Fraction(0), Fraction(0)
        for price, capacity in levels:
            if capacity <= 0:
                continue
            take = capacity
            if max_quantity is not None:
                take = min(take, max_quantity - filled)
            if budget_krw is not None:
                take = min(take, budget_krw / (price * (1 + fee)) - filled)
            outside = price > bound if side == "BUY" else price < bound
            if with_slippage_bound and outside:
                take = min(take, (bound * filled - gross) / (price - bound) if side == "BUY"
                           else (gross - bound * filled) / (bound - price))
            if take <= 0:
                break
            filled += take
            gross += price * take
            if take < capacity:
                break
        if side == "SELL" and max_quantity is not None and filled == max_quantity:
            return filled
        return _floor_scale(filled, scale)

    unbounded = largest(False)
    quantity = largest(True)
    gross, limit = _walk(levels, side=side, quantity=quantity)
    reasons = []
    if quantity < unbounded:
        reasons.append("QUANTITY_REDUCED_ACTUAL_NOTIONAL_SLIPPAGE_ABOVE_THRESHOLD")
    book_depth = sum((capacity for _price, capacity in levels), Fraction(0))
    if quantity > 0 and book_depth - quantity < Fraction(1, 10 ** scale):
        reasons.append("QUANTITY_LIMITED_BY_DECISION_SNAPSHOT_DEPTH")
    if quantity == 0:
        reasons.append("QUANTITY_ZERO")
    vwap = None if quantity == 0 else gross / quantity
    slippage = None if vwap is None else (
        (vwap - best) / best * 10000 if side == "BUY" else (best - vwap) / best * 10000
    )
    return {
        "side": side,
        "order_price_rule": ORDER_PRICE_RULE,
        "threshold_bp": threshold_bp,
        "best_price": _format_decimal(Decimal(best.numerator) / Decimal(best.denominator), "SIZING_PRICE_INVALID"),
        "slippage_bound_price": _fraction_text(bound),
        "quantity": _decimal_text_exact(quantity, capacity_scale),
        "quantity_without_slippage_bound": _decimal_text_exact(unbounded, capacity_scale),
        "quantity_decimal_places": scale,
        "limit_price": None if limit is None else _format_decimal(
            Decimal(limit.numerator) / Decimal(limit.denominator), "SIZING_PRICE_INVALID",
        ),
        "expected_vwap": None if vwap is None else _fraction_text(vwap),
        "expected_slippage_bps": None if slippage is None else _fraction_text(slippage),
        "budget_krw": None if budget_krw is None else _fraction_text(budget_krw),
        "submitted_amount_krw": (
            _fraction_text(limit * quantity * (1 + fee)) if side == "BUY" and limit is not None else None
        ),
        "fee_rate": _fraction_text(fee),
        "queue_fraction": _fraction_text(queue),
        "reasons": reasons,
        "snapshot_id": checked["snapshot_id"],
    }


def _promotion_packet_v4(decision: dict, *, observation_root: Path | None = None) -> dict | None:
    entries = _entries(decision, observation_root=observation_root)
    universe_entry = entries["universe"]
    market_entry = entries["market_evidence"]
    if universe_entry is None or universe_entry["packet"] is None:
        return None
    if market_entry is None or market_entry["date"] != universe_entry["date"]:
        market_by_market = {}
    else:
        market_by_market = market_entry["record"].get("packets", {})
    regime = DECISION.build_regime_snapshot(decision["generated_at"], None)
    try:
        return PROMOTION.build_promotion_packet(
            universe_entry["packet"], regime, market_by_market, None,
            evaluation_as_of=universe_entry["packet"]["evaluation_as_of"],
            **DECISION.v4_promotion_kwargs(entries["runtime_decision"], entries["rotation"]),
        )
    except PROMOTION.CryptoCandidatePromotionError:
        return None


def effective_market_regime(promotion: dict) -> str:
    """The CRYPTO market state in force for this decision under contract/3."""
    criterion = PROMOTION.evaluate_crypto_runtime_regime(
        promotion["source_packets"]["crypto_runtime_decision"],
        reference_at=promotion["regime_generated_at"],
    )
    return criterion["effective_regime"]


def _normalize_position_fills(value: object) -> list | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise CryptoPaperRuntimeBridgeError("POSITION_FILLS_INVALID")
    return sorted(copy.deepcopy(value), key=lambda row: str(row.get("fill_id")))


def _normalize_exit_intents(value: object) -> list | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise CryptoPaperRuntimeBridgeError("EXIT_INTENTS_INVALID")
    exit_module = _v4_modules()["EXIT"]
    normalized, symbols = [], set()
    for row in value:
        if not isinstance(row, dict) or set(row) != EXIT_INTENT_INPUT_FIELDS:
            raise CryptoPaperRuntimeBridgeError("EXIT_INTENT_INPUT_FIELDS_INVALID")
        intent = _core_call(exit_module.validate_intent, copy.deepcopy(row["intent"]))
        if intent["market"] != "CRYPTO" or MARKET_RE.fullmatch(str(intent["symbol"])) is None:
            raise CryptoPaperRuntimeBridgeError("EXIT_INTENT_MARKET_INVALID")
        if intent["symbol"] in symbols:
            raise CryptoPaperRuntimeBridgeError(f"EXIT_INTENT_SYMBOL_DUPLICATE:{intent['symbol']}")
        symbols.add(intent["symbol"])
        remaining = _format_decimal(row["remaining_quantity"], "EXIT_INTENT_REMAINING_INVALID", positive=True)
        if Decimal(remaining) > Decimal(intent["quantity"]):
            raise CryptoPaperRuntimeBridgeError(f"EXIT_INTENT_REMAINING_EXCEEDS_INTENT:{intent['symbol']}")
        normalized.append({"intent": intent, "remaining_quantity": remaining})
    return sorted(normalized, key=lambda row: row["intent"]["symbol"])


def _nav_snapshot_from_account(checked_account: dict, intent_by_order_id: dict, *, excluded_order_ids=frozenset()) -> tuple:
    """Crypto-ledger NAV snapshot for the session budget (build plan 2-3 principle 4)."""
    blockers = []
    reservations = []
    for order in checked_account["orders"]:
        if order["status"] not in {"OPEN", "PARTIALLY_FILLED"} or order["side"] != "BUY" \
                or order["order_id"] in excluded_order_ids:
            continue
        intent = intent_by_order_id.get(order["order_id"])
        if intent is None:
            raise CryptoPaperRuntimeBridgeError(f"OPEN_ORDER_INTENT_MISSING:{order['order_id']}")
        if intent["limit_price"] is None:
            blockers.append(f"OPEN_MARKET_BUY_RESERVATION_UNKNOWN:{order['order_id']}")
            continue
        reserved = (
            Fraction(Decimal(intent["limit_price"])) * Fraction(Decimal(order["remaining_quantity"]))
            * (1 + Fraction(Decimal(intent["fee_rate"])))
        )
        reservations.append({
            "market": "CRYPTO", "instrument": order["market"], "order_id": order["order_id"],
            "reserved_krw": _fraction_text(reserved),
        })
    holdings = [
        {
            "market": "CRYPTO", "instrument": position["market"], "currency": "KRW",
            "valuation": position["market_value"], "last_verified_valuation": None, "is_inverse_hedge": False,
        }
        for position in checked_account["positions"]
    ]
    snapshot = {
        "as_of_utc": checked_account["observed_at"],
        "virtual_cash_krw": checked_account["cash"],
        "holdings": holdings,
        "open_buy_reservations": sorted(reservations, key=lambda row: row["order_id"]),
        "fx_observation": None,
    }
    return snapshot, blockers


def sell_order_valid_before(generated_at: str, session_order_valid_before: str | None = None) -> str:
    """Exit sells are valid through the next decision slot, never past the session.

    Decision slots follow the realtime capture schedule
    (``DECISION.SCHEDULED_SLOT_MINUTES``): a sell issued in slot N can be matched
    by the capture of slot N+1 and expires at the end of that slot, so the
    decision after it re-sizes the remainder on a fresh book instead of leaving
    a stale limit open.  Canon 2-3: no order outlives its crypto decision
    cycle, so the slot bound is capped at the session's ``order_valid_before``
    (07:00Z); a sell issued in the last slot before 07:00Z is re-issued by the
    first decision of the next session.
    """
    slot = dt.timedelta(minutes=DECISION.SCHEDULED_SLOT_MINUTES)
    start = DECISION._floor_to_schedule_slot(_parse_utc(generated_at, "DECISION_GENERATED_AT_INVALID"))
    bound = start + 2 * slot
    if session_order_valid_before is not None:
        bound = min(bound, _parse_utc(session_order_valid_before, "SESSION_ORDER_VALID_BEFORE_INVALID"))
    return bound.strftime("%Y-%m-%dT%H:%M:%SZ")


def sell_issuance_deferred(generated_at: str, session_order_valid_before: str) -> bool:
    """Whether the session end would cut this decision's sell validity short of its slot bound."""
    return sell_order_valid_before(generated_at, session_order_valid_before) != sell_order_valid_before(generated_at)


# Buy-side failures that only mean "this decision's private buy inputs are
# stale or disagree with its market state": a caller envelope for another
# decision instant or state, or a validly signed recorded session budget for
# another session or regime.  They touch neither the decision packet, whose
# full re-derivation already passed, nor the exit intents, account or books
# the sells are built from, so exits proceed.  Every other buy-side failure
# (a record or envelope the execution core rejects as tampered or not
# re-derivable, a promotion rebuild inconsistent with the decision) is an
# integrity fault and aborts the whole request.
BUY_SIDE_FAILURES_EXITS_MAY_PROCEED = frozenset({
    "ALLOCATION_ENVELOPE_NOT_THIS_DECISION",
    "ALLOCATION_ENVELOPE_STATE_NOT_DECISION_REGIME",
    "RECORDED_SESSION_BUDGET_NOT_THIS_SESSION",
    "RECORDED_SESSION_BUDGET_STATE_NOT_DECISION_REGIME",
})


def _exit_orders(
    decision: dict, *, exit_intents: list, checked_account: dict, intent_by_order_id: dict,
    config: dict, threshold_bp: int, regime_status: str, source_root: Path, session_order_valid_before: str,
) -> tuple:
    """SELL requests for open exit intents and cancel requests for the open
    buys in those markets (canon 1-4: open buy remainders are cancelled before
    the exit order)."""
    requests, cancels, blockers, stopped_markets = [], [], [], set()
    positions = {row["market"]: row for row in checked_account["positions"]}
    generated = decision["generated_at"]
    open_orders = [order for order in checked_account["orders"] if order["status"] in {"OPEN", "PARTIALLY_FILLED"}]
    for row in exit_intents:
        intent = row["intent"]
        market = intent["symbol"]
        stopped_markets.add(market)
        if intent["timestamps"]["t_dec"] > generated:
            raise CryptoPaperRuntimeBridgeError(f"EXIT_INTENT_DECIDED_AFTER_DECISION:{market}")
        for order in open_orders:
            if order["market"] == market and order["side"] == "BUY":
                cancels.append({
                    "market": market, "order_id": order["order_id"], "exit_intent_id": intent["intent_id"],
                    "reason_code": "OPEN_BUY_CANCELLED_BEFORE_EXIT_ORDER",
                })
        held = positions.get(market)
        if held is None or Decimal(held["quantity"]) < Decimal(row["remaining_quantity"]):
            blockers.append(f"EXIT_INTENT_POSITION_QUANTITY_MISMATCH:{market}")
            continue
        if sell_issuance_deferred(generated, session_order_valid_before):
            # The session end would cut this sell's one match opportunity short
            # (last slot before 07:00Z): the next session's first decision issues it.
            blockers.append(f"EXIT_SELL_DEFERRED_TO_NEXT_SESSION:{market}")
            continue
        live_sells = [
            order for order in open_orders
            if order["market"] == market and order["side"] == "SELL"
            and _parse_utc(intent_by_order_id[order["order_id"]]["expires_at"], "OPEN_ORDER_EXPIRY_INVALID")
            > _parse_utc(generated, "DECISION_GENERATED_AT_INVALID")
        ]
        if live_sells:
            blockers.append(f"EXIT_SELL_ORDER_ALREADY_OPEN:{market}")
            continue
        try:
            snapshot = orderbook_snapshot(decision, market=market, observation_root=source_root, per_market=True)
        except MarketEvidenceUnavailableError as exc:
            # RULE.EXEC.DATA_FAILURE_PRIORITY.V1: an ordinary exit holds while STALE.
            blockers.append(f"EXIT_SNAPSHOT_UNAVAILABLE_HOLD:{market}:{exc}")
            continue
        sizing = marketable_limit_sizing(
            snapshot, side="SELL", fee_rate=config["fee_rate"], queue_fraction=config["queue_fraction"],
            threshold_bp=threshold_bp, max_quantity=Fraction(Decimal(row["remaining_quantity"])),
        )
        if sizing["quantity"] == "0":
            blockers.append(f"EXIT_ORDER_QUANTITY_ZERO_AFTER_SLIPPAGE_LIMIT:{market}")
            continue
        key = f"CRYPTO-PAPER-EXIT-{intent['intent_id'][:24].upper()}-{decision['generation_id'][:24].upper()}"
        order = SIMULATOR.build_intent(
            order_id=f"PAPER.SELL.{market}.{hashlib.sha256(key.encode()).hexdigest()[:24].upper()}",
            idempotency_key=key,
            market=market, side="SELL", order_type="LIMIT",
            quantity=sizing["quantity"], limit_price=sizing["limit_price"],
            fee_rate=config["fee_rate"], queue_fraction=config["queue_fraction"],
            submitted_at=generated, expires_at=sell_order_valid_before(generated, session_order_valid_before),
            market_regime_status=regime_status,
            source_plan_ref=f"public://paper-exit-intent/{intent['intent_id']}",
            source_plan_sha256=intent["payload_sha256"],
            source_evidence_ref=snapshot["source_ref"],
            source_evidence_sha256=snapshot["source_sha256"],
        )
        requests.append({
            "market": market, "exit_intent_id": intent["intent_id"], "exit_reason_code": intent["reason_code"],
            "intent": order, "source_snapshot": snapshot, "execution_sizing": sizing,
        })
    return requests, sorted(cancels, key=lambda row: row["order_id"]), blockers, stopped_markets


def _buy_side(
    decision: dict, *, promotion: dict, regime: str, regime_status: str, view: dict, core, session_id: str,
    checked_account: dict, config: dict, envelope: dict, recorded_session_budget: dict | None, fills: list,
    normalized_keys: list, exit_markets: set, excluded_order_ids: set, intent_by_order_id: dict,
    slippage: dict, source_root: Path,
) -> dict:
    budget_module = _v4_modules()["BUDGET"]
    generated = decision["generated_at"]
    result = {"eligibility": None, "record": None, "requests": [], "blockers": [],
              "allocation_blocked": False, "reservation_blocked": False, "market_blocked": False, "reused": False}
    blockers = result["blockers"]
    common = {
        "evaluation_as_of": promotion["evaluation_as_of"], "decision_at_utc": generated,
        "decision_packet_id": decision["generation_id"], "known_idempotency_keys": normalized_keys,
        "position_fills": fills, "fee_rate": config["fee_rate"],
    }
    decision_states = {row["market"]: row["p5_08"]["promotion_state"] for row in decision["candidates"]}
    for row in promotion["candidates"]:
        if decision_states.get(row["market"]) != row["promotion_state"]:
            raise CryptoPaperRuntimeBridgeError(f"PROMOTION_REBUILD_INCONSISTENT_WITH_DECISION:{row['market']}")
    phase_one = ELIGIBILITY.build_eligibility_packet_v3(promotion, **common)
    result["eligibility"] = phase_one
    books = {}
    for row in phase_one["candidates"]:
        if row["eligibility_state"] != ELIGIBILITY.STATE_WAIT:
            continue
        market = row["market"]
        blocker = _per_market_entry_blocker(view, market)
        if blocker is None and market in exit_markets:
            blocker = f"NEW_BUY_STOPPED_BY_OPEN_EXIT_INTENT:{market}"
        if blocker is not None:
            result["market_blocked"] = True
            blockers.append(blocker)
            continue
        try:
            books[market] = orderbook_snapshot(decision, market=market, observation_root=source_root, per_market=True)
        except MarketEvidenceUnavailableError as exc:
            result["market_blocked"] = True
            blockers.append(f"ENTRY_SNAPSHOT_UNAVAILABLE:{market}:{exc}")
    record = None
    if recorded_session_budget is not None:
        recorded = _core_call(budget_module.validate_session_budget_record, copy.deepcopy(recorded_session_budget))
        if recorded["key"]["market"] != "CRYPTO" or recorded["key"]["session_id"] != session_id:
            raise CryptoPaperRuntimeBridgeError("RECORDED_SESSION_BUDGET_NOT_THIS_SESSION")
        if recorded["inputs"]["envelope"]["markets"]["CRYPTO"]["confirmed_state"] != regime:
            raise CryptoPaperRuntimeBridgeError("RECORDED_SESSION_BUDGET_STATE_NOT_DECISION_REGIME")
        record = recorded
        result["record"] = recorded
        result["reused"] = True
        if recorded["decision_at_utc"] != generated:
            result["allocation_blocked"] = True
            blockers.append(f"SESSION_BUDGET_ALREADY_ALLOCATED:{session_id}")
            return result
        # Same decision re-run (restart): the recorded allocation is reused
        # verbatim; lines whose idempotency key is already known are BLOCKED by
        # the duplicate guard, the rest are (re)submitted.
    elif books:
        # Integrity first: a tampered envelope is rejected by the execution core
        # (never allowlisted) before the staleness and state checks can see it.
        _core_call(
            _v4_modules()["ENVELOPE"].validate_envelope, copy.deepcopy(envelope),
            root=budget_module.core_root(core),
        )
        if envelope.get("decision_at_utc") != generated:
            raise CryptoPaperRuntimeBridgeError("ALLOCATION_ENVELOPE_NOT_THIS_DECISION")
        if envelope["markets"]["CRYPTO"]["confirmed_state"] != regime:
            raise CryptoPaperRuntimeBridgeError("ALLOCATION_ENVELOPE_STATE_NOT_DECISION_REGIME")
        nav_snapshot, nav_blockers = _nav_snapshot_from_account(
            checked_account, intent_by_order_id, excluded_order_ids=excluded_order_ids,
        )
        if nav_blockers:
            blockers.extend(nav_blockers)
            result["reservation_blocked"] = True
            return result
        places = crypto_quantity_decimal_places()
        candidates = [
            {
                "instrument": market,
                "avg_traded_value": floor_row["krw_30d_avg_turnover"],
                "adv_window": "30_DAYS",
                "adv_source": "decision.candidates.realtime_liquidity_floor.krw_30d_avg_turnover",
                "limit_price": books[market]["ask_levels"][0]["price"],
                "quantity_step": _fraction_text(Fraction(1, 10 ** places)),
                "fee_rate": config["fee_rate"],
            }
            for market, floor_row in sorted(
                (row["market"], row["realtime_liquidity_floor"]) for row in decision["candidates"]
                if row["market"] in books
            )
        ]
        record = _core_call(
            budget_module.build_session_budget_record, core, market="CRYPTO", session_id=session_id,
            decision_at_utc=generated, nav_snapshot=nav_snapshot, envelope=envelope, candidates=candidates,
        )
        result["record"] = record
    if record is None:
        return result
    eligibility = ELIGIBILITY.build_eligibility_packet_v3(promotion, **common, session_budget_record=record)
    result["eligibility"] = eligibility
    for row in eligibility["candidates"]:
        market = row["market"]
        if row["eligibility_state"] != ELIGIBILITY.STATE_PAPER_BUY_ELIGIBLE:
            if market in books and row["eligibility_state"] == ELIGIBILITY.STATE_WAIT:
                blockers.append(f"SESSION_BUDGET_NO_QUANTITY:{market}")
            continue
        if market not in books:
            blockers.append(f"ENTRY_SNAPSHOT_NOT_AVAILABLE_FOR_RECORDED_LINE:{market}")
            continue
        draft = row["order_draft"]
        sizing = marketable_limit_sizing(
            books[market], side="BUY", fee_rate=config["fee_rate"], queue_fraction=config["queue_fraction"],
            threshold_bp=slippage["threshold_bp"], budget_krw=Fraction(draft["allocated_krw"]),
        )
        if sizing["quantity"] == "0":
            blockers.append(f"ORDER_QUANTITY_ZERO_AFTER_SLIPPAGE_LIMIT:{market}")
            continue
        guard = draft["duplicate_guard_key"]
        intent = SIMULATOR.build_intent(
            order_id=f"PAPER.BUY.{market}.{hashlib.sha256(guard.encode()).hexdigest()[:24].upper()}",
            idempotency_key=guard,
            market=market, side="BUY", order_type="LIMIT",
            quantity=sizing["quantity"], limit_price=sizing["limit_price"],
            fee_rate=config["fee_rate"], queue_fraction=config["queue_fraction"],
            submitted_at=generated, expires_at=draft["expires_at"],
            market_regime_status=regime_status,
            source_plan_ref=f"public://crypto-paper-decision/{decision['generation_id']}",
            source_plan_sha256=decision["payload_sha256"],
            source_evidence_ref=books[market]["source_ref"],
            source_evidence_sha256=books[market]["source_sha256"],
        )
        result["requests"].append({
            "market": market, "planned_loss": copy.deepcopy(draft["planned_loss"]),
            "order_draft": copy.deepcopy(draft), "intent": intent,
            "source_snapshot": books[market], "execution_sizing": sizing,
        })
    return result


def _derive_runtime_request_v4(
    decision: dict,
    *,
    expected_source_commit: str,
    public_code_commit_sha: str | None = None,
    observation_root: Path | None = None,
    observation_commit_sha: str | None = None,
    account_state: dict | None,
    open_position_risk: list[dict] | None,
    runtime_config: dict | None,
    known_idempotency_keys=None,
    allocation_envelope: dict | None = None,
    recorded_session_budget: dict | None = None,
    position_fills: list | None = None,
    exit_intents: list | None = None,
) -> dict:
    modules = _v4_modules()
    budget_module = modules["BUDGET"]
    source_root = _safe_observation_root(observation_root)
    decision = validate_decision_snapshot(
        decision, expected_source_commit=expected_source_commit, observation_root=source_root,
    )
    if decision["schema_version"] != DECISION.V4_OUTPUT_SCHEMA_VERSION:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_V4_REQUIRES_DECISION_V4")
    generated = decision["generated_at"]
    view = market_freshness_view(decision)
    code_commit = _require_sha40(public_code_commit_sha or expected_source_commit, "PUBLIC_CODE_COMMIT_INVALID")
    observation_commit = _require_sha40(observation_commit_sha or code_commit, "OBSERVATION_COMMIT_INVALID")
    config = validate_runtime_config(runtime_config) if runtime_config is not None else None
    if config is not None and _parse_utc(config["approved_at"], "RUNTIME_CONFIG_APPROVED_AT_INVALID") > _parse_utc(
        generated, "DECISION_GENERATED_AT_INVALID",
    ):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_APPROVED_AFTER_DECISION")
    checked_account = SIMULATOR.validate_account_state(account_state) if account_state is not None else None
    normalized_risk = _normalize_open_position_risk(open_position_risk)
    normalized_keys = _normalize_known_idempotency_keys(known_idempotency_keys)
    fills = _normalize_position_fills(position_fills)
    exits = _normalize_exit_intents(exit_intents)
    envelope = copy.deepcopy(allocation_envelope) if allocation_envelope is not None else None
    missing = [
        name for name, value in (
            ("PAPER_ACCOUNT_STATE", checked_account), ("USER_RATIFIED_RUNTIME_CONFIG", config),
            ("ALLOCATION_ENVELOPE", envelope), ("POSITION_FILLS", fills), ("EXIT_INTENTS", exits),
        ) if value is None
    ]
    core = _core_call(modules["CORE"].load_core)
    slippage = crypto_slippage_rule(core)
    session_id = _core_call(budget_module.crypto_session_id, core, generated)
    bounds = _core_call(budget_module.session_bounds, core, session_id)

    carried = _carried_open_order_matches(
        decision, checked_account, source_root=source_root, per_market=True, legacy_request=False,
    )
    match_snapshots = list(carried["match_snapshots"])
    blockers = list(carried["blockers"])
    promotion = _promotion_packet_v4(decision, observation_root=source_root)
    regime = effective_market_regime(promotion) if promotion is not None else DECISION.UNKNOWN
    regime_status = simulator_market_regime_status(regime)
    intent_by_order_id = (
        {
            event["order_id"]: event["payload"]["intent"]
            for event in checked_account["source_ledger"]["events"]
            if event["event_type"] == "ORDER_SUBMITTED"
        }
        if checked_account is not None else {}
    )
    sell_requests, cancel_requests, exit_markets = [], [], set()
    if checked_account is not None and config is not None and exits is not None:
        sell_requests, cancel_requests, exit_blockers, exit_markets = _exit_orders(
            decision, exit_intents=exits, checked_account=checked_account, intent_by_order_id=intent_by_order_id,
            config=config, threshold_bp=slippage["threshold_bp"], regime_status=regime_status,
            source_root=source_root, session_order_valid_before=bounds["order_valid_before_utc"],
        )
        blockers.extend(exit_blockers)
        cancelled = {row["order_id"] for row in cancel_requests}
        if cancelled:
            # A buy remainder being cancelled for an exit is neither matched nor reserved.
            match_snapshots = [
                dict(row, order_ids=[order_id for order_id in row["order_ids"] if order_id not in cancelled])
                for row in match_snapshots
            ]
            match_snapshots = [row for row in match_snapshots if row["order_ids"]]
    nav_unknown_markets = (
        sorted(row["market"] for row in checked_account["positions"] if row.get("mark_status") == "UNKNOWN")
        if checked_account is not None and checked_account["total_nav"] is None else []
    )
    buy = {"eligibility": None, "record": None, "requests": [], "blockers": [],
           "allocation_blocked": False, "reservation_blocked": False, "market_blocked": False, "reused": False}
    if promotion is None:
        blockers.append("PROMOTION_PACKET_UNAVAILABLE")
    elif missing:
        blockers.extend("RUNTIME_INPUT_MISSING:" + item for item in missing)
    elif nav_unknown_markets:
        blockers.append("PAPER_ACCOUNT_NAV_UNKNOWN:" + ",".join(nav_unknown_markets))
    else:
        try:
            buy = _buy_side(
                decision, promotion=promotion, regime=regime, regime_status=regime_status, view=view,
                core=core, session_id=session_id, checked_account=checked_account, config=config,
                envelope=envelope, recorded_session_budget=recorded_session_budget, fills=fills,
                normalized_keys=normalized_keys, exit_markets=exit_markets,
                excluded_order_ids={row["order_id"] for row in cancel_requests},
                intent_by_order_id=intent_by_order_id, slippage=slippage, source_root=source_root,
            )
        except CryptoPaperRuntimeBridgeError as exc:
            if not sell_requests or str(exc) not in BUY_SIDE_FAILURES_EXITS_MAY_PROCEED:
                raise
            # Exits still go out when the buy side cannot be derived.
            buy["blockers"] = [f"BUY_SIDE_BLOCKED_EXITS_PROCEED:{exc}"]
    blockers.extend(buy["blockers"])
    requests = buy["requests"]
    eligibility = buy["eligibility"]
    record = buy["record"]

    if requests or sell_requests or cancel_requests:
        status = "PAPER_INTENTS_READY"
    elif match_snapshots:
        status = "PAPER_MATCHES_READY"
    elif promotion is None:
        status = "WAIT_PROMOTION_UNAVAILABLE"
    elif missing:
        status = "WAIT_RUNTIME_INPUTS_MISSING"
    elif nav_unknown_markets:
        status = "WAIT_ACCOUNT_NAV_UNKNOWN"
    elif buy["allocation_blocked"]:
        status = "WAIT_SESSION_BUDGET_ALLOCATED"
    elif buy["reservation_blocked"]:
        status = "WAIT_OPEN_ORDER_RESERVATION_UNKNOWN"
    elif buy["market_blocked"]:
        status = "WAIT_MARKET_EVIDENCE_OR_CAP"
    else:
        status = "NO_ELIGIBLE_CANDIDATE"
    packet = {
        "schema_version": V4_REQUEST_SCHEMA_VERSION,
        "mode": PRIVATE_RUNTIME_MODE,
        "status": status,
        "observed_at": generated,
        "decision_generation_id": decision["generation_id"],
        "decision_payload_sha256": decision["payload_sha256"],
        "decision_source_commit_sha": decision["source_commit"],
        "public_code_commit_sha": code_commit,
        "observation_commit_sha": observation_commit,
        "runtime_config_sha256": config["packet_sha256"] if config is not None else None,
        "eligibility": eligibility,
        "requests": requests,
        "sell_requests": sell_requests,
        "cancel_requests": cancel_requests,
        "match_snapshots": match_snapshots,
        "session_budget_record": record,
        "blockers": sorted(blockers),
        "authority": copy.deepcopy(AUTHORITY),
        "decision_schema_version": decision["schema_version"],
        "freshness_mode": PER_MARKET_FRESHNESS_MODE,
        "market_status": _request_market_status(decision),
        "wiring": {
            "promotion_contract_version": promotion["contract_version"] if promotion is not None else None,
            "eligibility_contract_version": ELIGIBILITY.load_contract_v3()["contract_version"],
            "session_id": session_id,
            "order_valid_before_utc": bounds["order_valid_before_utc"],
            "nav_mode": NAV_MODE,
            "market_regime": regime,
            "simulator_market_regime_status": regime_status,
            "order_price_rule": ORDER_PRICE_RULE,
            "crypto_slippage_rule": slippage,
            "runtime_config_order_fields_superseded_by": RULE_QUALITY_LAYERS,
            "session_budget_record_reused": buy["reused"],
            # Null when this decision's slot is cut short by the session end: exit
            # sells are then deferred to the next session and none is issued.
            "sell_order_valid_before_utc": (
                None if sell_issuance_deferred(generated, bounds["order_valid_before_utc"])
                else sell_order_valid_before(generated, bounds["order_valid_before_utc"])
            ),
            "sell_issuance_deferred_to_next_session": sell_issuance_deferred(generated, bounds["order_valid_before_utc"]),
            "quantity_decimal_places": crypto_quantity_decimal_places(),
        },
        "source_inputs": {
            "decision": copy.deepcopy(decision),
            "public_code_commit_sha": code_commit,
            "observation_root": str(source_root),
            "observation_commit_sha": observation_commit,
            "account_state": copy.deepcopy(checked_account),
            "open_position_risk": copy.deepcopy(normalized_risk),
            "runtime_config": copy.deepcopy(config),
            "known_idempotency_keys": normalized_keys,
            "allocation_envelope": envelope,
            "recorded_session_budget": copy.deepcopy(recorded_session_budget),
            "position_fills": fills,
            "exit_intents": copy.deepcopy(exits),
        },
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def _validate_runtime_request_v4(
    value: dict, *, expected_public_code_commit_sha: str | None = None,
    expected_observation_root: Path | None = None,
    expected_observation_commit_sha: str | None = None,
) -> dict:
    if set(value) != V4_REQUEST_FIELDS:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_FIELDS_INVALID")
    if value.get("mode") != PRIVATE_RUNTIME_MODE or value.get("authority") != AUTHORITY:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_IDENTITY_INVALID")
    digest = _require_sha256(value.get("packet_sha256"), "RUNTIME_REQUEST_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_SHA_MISMATCH")
    public_commit = _require_sha40(value.get("public_code_commit_sha"), "RUNTIME_REQUEST_PUBLIC_COMMIT_INVALID")
    if expected_public_code_commit_sha is not None and public_commit != _require_sha40(
        expected_public_code_commit_sha, "EXPECTED_PUBLIC_CODE_COMMIT_INVALID",
    ):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_PUBLIC_COMMIT_MISMATCH")
    observation_commit = _require_sha40(value.get("observation_commit_sha"), "RUNTIME_REQUEST_OBSERVATION_COMMIT_INVALID")
    if expected_observation_commit_sha is not None and observation_commit != _require_sha40(
        expected_observation_commit_sha, "EXPECTED_OBSERVATION_COMMIT_INVALID",
    ):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_OBSERVATION_COMMIT_MISMATCH")
    for index, row in enumerate(value.get("requests") or []):
        if not isinstance(row, dict) or set(row) != V4_REQUEST_ROW_FIELDS:
            raise CryptoPaperRuntimeBridgeError(f"RUNTIME_REQUEST_ROW_FIELDS_INVALID:{index}")
        intent = SIMULATOR.validate_intent(row["intent"])
        if intent["market"] != row["market"] or intent["side"] != "BUY":
            raise CryptoPaperRuntimeBridgeError(f"RUNTIME_REQUEST_ROW_MARKET_MISMATCH:{index}")
    for index, row in enumerate(value.get("sell_requests") or []):
        if not isinstance(row, dict) or set(row) != V4_SELL_ROW_FIELDS:
            raise CryptoPaperRuntimeBridgeError(f"RUNTIME_REQUEST_SELL_ROW_FIELDS_INVALID:{index}")
        intent = SIMULATOR.validate_intent(row["intent"])
        if intent["market"] != row["market"] or intent["side"] != "SELL":
            raise CryptoPaperRuntimeBridgeError(f"RUNTIME_REQUEST_SELL_ROW_MARKET_MISMATCH:{index}")
    for index, row in enumerate(value.get("cancel_requests") or []):
        if not isinstance(row, dict) or set(row) != V4_CANCEL_ROW_FIELDS:
            raise CryptoPaperRuntimeBridgeError(f"RUNTIME_REQUEST_CANCEL_ROW_FIELDS_INVALID:{index}")
    if value["status"] == "PAPER_INTENTS_READY" and not (
        value["requests"] or value["sell_requests"] or value["cancel_requests"]
    ):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_READY_WITHOUT_INTENTS")
    source_inputs = value.get("source_inputs")
    if not isinstance(source_inputs, dict) or set(source_inputs) != V4_SOURCE_INPUT_FIELDS:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_SOURCE_INPUTS_INVALID")
    if source_inputs["public_code_commit_sha"] != public_commit:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_SOURCE_COMMIT_MISMATCH")
    if source_inputs["observation_commit_sha"] != observation_commit:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_OBSERVATION_SOURCE_COMMIT_MISMATCH")
    source_root = _safe_observation_root(Path(source_inputs["observation_root"]))
    if expected_observation_root is not None and source_root != _safe_observation_root(expected_observation_root):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_OBSERVATION_ROOT_MISMATCH")
    decision = source_inputs["decision"]
    if not isinstance(decision, dict):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_SOURCE_DECISION_INVALID")
    rebuilt = _derive_runtime_request_v4(
        decision,
        expected_source_commit=decision.get("source_commit"),
        public_code_commit_sha=public_commit,
        observation_root=source_root,
        observation_commit_sha=observation_commit,
        account_state=source_inputs["account_state"],
        open_position_risk=source_inputs["open_position_risk"],
        runtime_config=source_inputs["runtime_config"],
        known_idempotency_keys=source_inputs["known_idempotency_keys"],
        allocation_envelope=source_inputs["allocation_envelope"],
        recorded_session_budget=source_inputs["recorded_session_budget"],
        position_fills=source_inputs["position_fills"],
        exit_intents=[
            {"intent": row["intent"], "remaining_quantity": row["remaining_quantity"]}
            for row in source_inputs["exit_intents"]
        ] if source_inputs["exit_intents"] is not None else None,
    )
    if canonical_json(rebuilt) != canonical_json(value):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_DERIVATION_MISMATCH")
    return copy.deepcopy(value)
