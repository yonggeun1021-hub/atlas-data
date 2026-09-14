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
    return {
        "universe": universe_entry,
        "market_evidence": market_entry,
        "realtime": realtime_entry,
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
        and decision.get("schema_version") in DECISION.PER_MARKET_OUTPUT_SCHEMA_VERSIONS
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
) -> dict:
    if request_schema_version not in REQUEST_SCHEMA_VERSIONS:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_SCHEMA_VERSION_UNSUPPORTED")
    legacy_request = request_schema_version == LEGACY_REQUEST_SCHEMA_VERSION
    source_root = _safe_observation_root(observation_root)
    decision = validate_decision_snapshot(
        decision, expected_source_commit=expected_source_commit,
        observation_root=source_root,
    )
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
    if checked_account is not None:
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
            except CryptoPaperRuntimeBridgeError as exc:
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
    if promotion is None:
        blockers.append("PROMOTION_PACKET_UNAVAILABLE")
    elif missing:
        blockers.extend("RUNTIME_INPUT_MISSING:" + item for item in missing)
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
                market_regime_status=promotion["source_packets"]["regime"].get("regime", "UNKNOWN"),
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
) -> dict:
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
