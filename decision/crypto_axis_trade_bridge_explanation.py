#!/usr/bin/env python3
"""P5-10 per-symbol five-axis entry/exit explanation read model.

This module is a read-only consumer of exactly one committed
``crypto_axis_trade_bridge`` packet.  It re-validates that packet with the
producer's own validator and then relabels the state and reason codes the
bridge already emitted into a fixed user-language vocabulary held in
``config/crypto_axis_trade_bridge_explanation_contract.json``.

It derives no state of its own.  Every stage label is a total, fail-closed
relabeling of an upstream code: an unknown entry state, exit state or reason
code aborts the build instead of falling into a default bucket.  Stages that
this input cannot reach are emitted as explicitly empty and carry the verbatim
upstream reason code for their emptiness.  An empty 보유/축소/청산검토 stage
means the input packet carries no virtual-fill position; it never asserts
anything about real account holdings.

No aggregate Regime is computed, no numeric threshold, market membership,
schedule, network call, order, account or key access is added.  Every
authority flag is false.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "crypto_axis_trade_bridge_explanation_contract.json"
# Canonical committed location.  The CLI still requires an explicit
# ``--output-root``; there is deliberately no default live target.
CANONICAL_OUTPUT_ROOT = ROOT / "evidence" / "crypto_axis_trade_bridge_explanation"
CONTRACT_VERSION = "crypto_axis_trade_bridge_explanation/1"
OUTPUT_SCHEMA_VERSION = "crypto_axis_trade_bridge_explanation_packet/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CAPTURE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CAPTURE_HHMM_RE = re.compile(r"^\d{4}$")


class CryptoAxisTradeBridgeExplanationError(ValueError):
    """Fail-closed P5-10 explanation contract or derivation violation."""


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CryptoAxisTradeBridgeExplanationError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BRIDGE = _load(
    "crypto_axis_trade_bridge_explanation_bridge",
    "decision/crypto_axis_trade_bridge.py",
)

# Single definition reused from the producer, never re-implemented here, so a
# hash in this packet is computed exactly the way the bridge computes its own.
canonical_json = BRIDGE.canonical_json
payload_sha256 = BRIDGE.payload_sha256


def _fail(code: str) -> None:
    raise CryptoAxisTradeBridgeExplanationError(code)


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoAxisTradeBridgeExplanationError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _require_all_false(authority: object, code: str = "AUTHORITY_INVALID") -> None:
    if (
        not isinstance(authority, dict)
        or not authority
        or any(value is not False for value in authority.values())
    ):
        _fail(code)


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "mode": "PAPER_REVIEW_ONLY",
        "source_contract_version": "crypto_axis_trade_bridge/1",
        "source_packet_schema_version": "crypto_axis_trade_bridge_packet/1",
        "axis_order": ["TREND", "RISK_VOL", "LIQUIDITY", "BREADTH", "LEADERSHIP"],
        "axis_labels": {
            "TREND": "추세",
            "RISK_VOL": "위험·변동성",
            "LIQUIDITY": "유동성",
            "BREADTH": "시장 폭",
            "LEADERSHIP": "주도력",
        },
        "axis_status_labels": {"DEFINED": "확보됨", "UNDEFINED": "미확보"},
        "axis_hold_text": "{axis_label}({axis}) 축이 미확보라 이 종목의 신규 진입이 보류된다.",
        "axis_defined_text": (
            "{axis_label}({axis}) 축은 확보됐다. 확보는 데이터 완성도를 뜻할 뿐 매수 허가가 아니다."
        ),
        "stages": [
            {
                "stage_id": "ENTRY_WAIT", "label": "매수대기", "kind": "ENTRY",
                "source_entry_state": "WAIT", "empty_reason_code": None,
            },
            {
                "stage_id": "ENTRY_BLOCKED", "label": "진입 차단", "kind": "ENTRY",
                "source_entry_state": "BLOCKED", "empty_reason_code": None,
            },
            {
                "stage_id": "ENTRY_REVIEW", "label": "진입검토", "kind": "ENTRY",
                "source_entry_state": None,
                "empty_reason_code": "AGGREGATE_POLICY_UNRATIFIED",
            },
            {
                "stage_id": "HOLDING", "label": "보유", "kind": "POSITION",
                "source_entry_state": None,
                "empty_reason_code": "NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET",
            },
            {
                "stage_id": "REDUCE", "label": "축소", "kind": "POSITION",
                "source_entry_state": None,
                "empty_reason_code": "NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET",
            },
            {
                "stage_id": "EXIT_REVIEW", "label": "청산검토", "kind": "POSITION",
                "source_entry_state": None,
                "empty_reason_code": "NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET",
            },
        ],
        "exit_state_labels": {
            "NOT_APPLICABLE_UNTIL_VIRTUAL_FILL": "가상 체결 전이라 청산 단계가 아직 해당되지 않음",
        },
        "exit_priority_ladder": [
            {"category": "HARD_EXIT", "label": "하드 청산(최우선)"},
            {"category": "SECURITY_LIQUIDITY", "label": "종목 안전성·유동성"},
            {"category": "RISK_REGIME", "label": "위험·국면"},
            {"category": "TREND", "label": "추세"},
            {"category": "PROFIT_TRAIL", "label": "이익 보전"},
            {"category": "TIME_REVIEW", "label": "기간 경과 검토"},
        ],
        "reason_text": {
            "AGGREGATE_POLICY_UNRATIFIED": (
                "종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다."
            ),
            "NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET": (
                "이 입력 패킷에는 가상 체결 포지션이 없어 이 단계에 표시할 종목이 없다. "
                "실제 계좌의 보유 자산 유무를 뜻하지 않는다."
            ),
            "P7_13_HARD_EXIT_SECURITY_LIQUIDITY_PRIORITY_PRESERVED": (
                "가상 체결 이후 청산은 기존 P7-13 관리자가 담당하며 하드 청산·안전성/유동성 "
                "우선순위가 그대로 유지된다."
            ),
            "NO_SYMBOL_ROWS_IN_SOURCE_PACKET": (
                "원본 브리지 패킷에 종목 행이 없어 어떤 단계에도 표시할 종목이 없다."
            ),
            "NO_SYMBOL_IN_THIS_STAGE": "이번 세대에는 이 단계로 분류된 종목이 없다.",
        },
        "reason_prefix_text": {
            "UPSTREAM_STATE": "상류 P5-08/P5-09 판정 상태는 {value}이다.",
            "OFFICIAL_AXES_INCOMPLETE": (
                "공식 5개 축 가운데 {value}이(가) 아직 미확보라 신규 진입이 보류된다."
            ),
        },
        "authority": {
            "stage_authorized": False,
            "buy_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "real_capital_authorized": False,
            "market_judgment_authorized": False,
            "entry_eligibility_authorized": False,
            "exit_action_authorized": False,
            "exchange_order_authorized": False,
            "broker_submission_authorized": False,
        },
    }


def validate_contract(value: dict) -> dict:
    """Exact-match the presentation contract and re-pin it to the producer.

    The vocabulary is presentation only.  The axis order and the exit ladder
    are not re-declared independently: they are compared against the producer's
    own contract so this module cannot drift from P5-10/P7-13.
    """
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        _fail("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            _fail(f"CONTRACT_FIELD_MISMATCH:{key}")
    _require_all_false(value["authority"], "CONTRACT_AUTHORITY_INVALID")

    bridge_contract = BRIDGE.load_contract()
    if value["source_contract_version"] != bridge_contract["contract_version"]:
        _fail("SOURCE_CONTRACT_VERSION_MISMATCH")
    if value["source_packet_schema_version"] != BRIDGE.OUTPUT_SCHEMA_VERSION:
        _fail("SOURCE_PACKET_SCHEMA_VERSION_MISMATCH")
    if value["axis_order"] != bridge_contract["required_axes"]:
        _fail("AXIS_ORDER_DRIFT")
    ladder = [row["category"] for row in value["exit_priority_ladder"]]
    if ladder != bridge_contract["exit_policy"]["priority_categories"]:
        _fail("EXIT_PRIORITY_DRIFT")
    if ladder[0] != "HARD_EXIT":
        _fail("EXIT_PRIORITY_DRIFT")
    # Stage ids, labels, empty-reason codes and axis labels need no further
    # structural check here: the exact-match above already pins them, so any
    # edit to the config file is rejected as CONTRACT_FIELD_MISMATCH:<key>.
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return validate_contract(_read_json(Path(path)))


def _reason_text(code: object, contract: dict) -> str:
    """Total, fail-closed relabeling of one upstream reason code."""
    if not isinstance(code, str) or not code:
        _fail("REASON_CODE_INVALID")
    exact = contract["reason_text"]
    if code in exact:
        return exact[code]
    prefix, separator, value = code.partition(":")
    if separator and value and prefix in contract["reason_prefix_text"]:
        return contract["reason_prefix_text"][prefix].replace("{value}", value)
    _fail(f"REASON_CODE_UNMAPPED:{code}")


def _reason_rows(codes: object, contract: dict, label: str) -> list[dict]:
    if not isinstance(codes, list):
        _fail(f"REASON_LIST_INVALID:{label}")
    return [{"code": code, "text": _reason_text(code, contract)} for code in codes]


def _axis_rows(coverage: dict, contract: dict) -> list[dict]:
    axes = coverage.get("axes")
    if not isinstance(axes, dict) or set(axes) != set(contract["axis_order"]):
        _fail("SOURCE_AXIS_SET_MISMATCH")
    missing = coverage.get("missing_axes")
    if not isinstance(missing, list):
        _fail("SOURCE_MISSING_AXES_INVALID")
    rows = []
    for axis in contract["axis_order"]:
        row = axes[axis]
        status = row.get("status")
        if status not in contract["axis_status_labels"]:
            _fail(f"AXIS_STATUS_UNMAPPED:{axis}:{status}")
        is_hold_cause = axis in missing
        if is_hold_cause and status != "UNDEFINED":
            # A DEFINED axis may never be presented as a hold cause.
            _fail(f"AXIS_HOLD_STATUS_CONFLICT:{axis}")
        if status == "UNDEFINED" and not is_hold_cause:
            _fail(f"AXIS_MISSING_LIST_INCOMPLETE:{axis}")
        label = contract["axis_labels"][axis]
        template = contract["axis_hold_text"] if is_hold_cause else contract["axis_defined_text"]
        bindings = row.get("bindings") or {}
        rows.append({
            "axis": axis,
            "axis_label": label,
            "status": status,
            "status_label": contract["axis_status_labels"][status],
            "observation_date": row.get("observation_date"),
            "available_at": row.get("available_at"),
            "warnings": copy.deepcopy(row.get("warnings") or []),
            "entry_consumers": copy.deepcopy(bindings.get("entry_consumers") or []),
            "exit_consumers": copy.deepcopy(bindings.get("exit_consumers") or []),
            "is_hold_cause": is_hold_cause,
            "text": template.replace("{axis_label}", label).replace("{axis}", axis),
        })
    return rows


def _symbol_axis_holds(entry_reasons: list, coverage: dict, contract: dict) -> list[dict]:
    """Name a hold axis only where this symbol's own entry reason says so."""
    named: list[str] = []
    for code in entry_reasons:
        prefix, separator, value = str(code).partition(":")
        if not separator or prefix != "OFFICIAL_AXES_INCOMPLETE":
            continue
        named.extend(part for part in value.split(",") if part)
    for axis in named:
        if axis not in contract["axis_order"]:
            _fail(f"AXIS_HOLD_UNKNOWN:{axis}")
        if coverage["axes"][axis].get("status") != "UNDEFINED":
            _fail(f"AXIS_HOLD_STATUS_CONFLICT:{axis}")
    # Every UNDEFINED axis must be named on every symbol, in the producer's
    # own order -- a dropped or reordered hold cause fails closed.
    if named != list(coverage["missing_axes"]):
        _fail("AXIS_HOLD_COVERAGE_MISMATCH")
    if not named:
        return []
    labels = contract["axis_labels"]
    return [
        {
            "axis": axis,
            "axis_label": labels[axis],
            "status": coverage["axes"][axis]["status"],
            "text": (
                contract["axis_hold_text"]
                .replace("{axis_label}", labels[axis])
                .replace("{axis}", axis)
            ),
        }
        for axis in named
    ]


def _ladder_rows(contract: dict) -> list[dict]:
    return [
        {"rank": index + 1, "category": row["category"], "label": row["label"]}
        for index, row in enumerate(contract["exit_priority_ladder"])
    ]


def _symbol_rows(packet: dict, contract: dict) -> list[dict]:
    source_rows = packet.get("symbol_rules")
    if not isinstance(source_rows, list):
        _fail("SOURCE_SYMBOL_RULES_INVALID")
    coverage = packet["five_axis"]
    ladder_categories = [row["category"] for row in contract["exit_priority_ladder"]]
    ladder = _ladder_rows(contract)
    stage_by_state = {
        row["source_entry_state"]: row["stage_id"] for row in contract["stages"]
        if row["source_entry_state"] is not None
    }
    label_by_stage = {row["stage_id"]: row["label"] for row in contract["stages"]}

    rows = []
    seen: set[str] = set()
    for source in source_rows:
        if not isinstance(source, dict):
            _fail("SOURCE_SYMBOL_ROW_INVALID")
        market = source.get("market")
        if not isinstance(market, str) or not market:
            _fail("SOURCE_SYMBOL_MARKET_INVALID")
        if market in seen:
            _fail(f"SYMBOL_DUPLICATE:{market}")
        seen.add(market)

        entry = source.get("entry")
        exit_context = source.get("exit")
        if not isinstance(entry, dict) or not isinstance(exit_context, dict):
            _fail(f"SOURCE_SYMBOL_CONTEXT_INVALID:{market}")

        entry_state = entry.get("state")
        stage_id = stage_by_state.get(entry_state)
        if stage_id is None:
            _fail(f"ENTRY_STATE_UNMAPPED:{market}:{entry_state}")
        exit_state = exit_context.get("state")
        if exit_state not in contract["exit_state_labels"]:
            _fail(f"EXIT_STATE_UNMAPPED:{market}:{exit_state}")
        if exit_context.get("priority_categories") != ladder_categories:
            _fail(f"EXIT_PRIORITY_DRIFT:{market}")

        entry_reasons = entry.get("reasons")
        if not isinstance(entry_reasons, list):
            _fail(f"REASON_LIST_INVALID:{market}.entry")
        rows.append({
            "market": market,
            "canonical_asset_id": source.get("canonical_asset_id"),
            "stage_id": stage_id,
            "entry_label": label_by_stage[stage_id],
            "entry_state": entry_state,
            "entry_reason_codes": copy.deepcopy(entry_reasons),
            "entry_reason_text": _reason_rows(entry_reasons, contract, f"{market}.entry"),
            "upstream_state": source.get("upstream_state"),
            "upstream_reason": source.get("upstream_reason"),
            "axis_holds": _symbol_axis_holds(entry_reasons, coverage, contract),
            "exit_state": exit_state,
            "exit_label": contract["exit_state_labels"][exit_state],
            "exit_reason_codes": copy.deepcopy(exit_context.get("reasons") or []),
            "exit_reason_text": _reason_rows(
                exit_context.get("reasons") or [], contract, f"{market}.exit",
            ),
            "exit_ladder": copy.deepcopy(ladder),
        })
    rows.sort(key=lambda row: row["market"])
    return rows


def _stage_rows(symbols: list[dict], contract: dict) -> list[dict]:
    members: dict[str, list[str]] = {row["stage_id"]: [] for row in contract["stages"]}
    for symbol in symbols:
        members[symbol["stage_id"]].append(symbol["market"])

    entry_codes_per_symbol = [set(row["entry_reason_codes"]) for row in symbols]
    exit_codes_per_symbol = [set(row["exit_reason_codes"]) for row in symbols]

    rows = []
    for stage in contract["stages"]:
        stage_id = stage["stage_id"]
        stage_members = sorted(members[stage_id])
        if stage["source_entry_state"] is None and stage_members:
            _fail(f"UNREACHABLE_STAGE_HAS_MEMBERS:{stage_id}")
        empty_reason = None
        if not stage_members:
            if not symbols:
                code = "NO_SYMBOL_ROWS_IN_SOURCE_PACKET"
            elif stage["empty_reason_code"] is not None:
                code = stage["empty_reason_code"]
                observed = (
                    entry_codes_per_symbol if stage["kind"] == "ENTRY"
                    else exit_codes_per_symbol
                )
                # The declared emptiness reason must be a code the producer
                # actually emitted for every symbol, never an invented one.
                if not all(code in codes for codes in observed):
                    _fail(f"EMPTY_STAGE_REASON_UNSUPPORTED:{stage_id}")
            else:
                code = "NO_SYMBOL_IN_THIS_STAGE"
            empty_reason = {"code": code, "text": _reason_text(code, contract)}
        rows.append({
            "stage_id": stage_id,
            "label": stage["label"],
            "kind": stage["kind"],
            "source_entry_state": stage["source_entry_state"],
            "member_count": len(stage_members),
            "members": stage_members,
            "empty_reason": empty_reason,
        })
    if sum(row["member_count"] for row in rows) != len(symbols):
        _fail("STAGE_MEMBERSHIP_INCOMPLETE")
    return rows


def _source_path_fields(packet: dict) -> tuple[str, str]:
    source = packet.get("source")
    snapshot = source.get("decision_snapshot") if isinstance(source, dict) else None
    if not isinstance(snapshot, dict):
        _fail("SOURCE_DECISION_SNAPSHOT_MISSING")
    capture_date = snapshot.get("capture_date")
    capture_hhmm = snapshot.get("capture_hhmm")
    if not isinstance(capture_date, str) or CAPTURE_DATE_RE.fullmatch(capture_date) is None:
        _fail("SOURCE_CAPTURE_DATE_INVALID")
    if not isinstance(capture_hhmm, str) or CAPTURE_HHMM_RE.fullmatch(capture_hhmm) is None:
        _fail("SOURCE_CAPTURE_HHMM_INVALID")
    return capture_date, capture_hhmm


def _assemble_unsigned(packet: dict, contract: dict) -> dict:
    """Pure relabeling helper; never returns a signed explanation packet."""
    if packet.get("schema_version") != contract["source_packet_schema_version"]:
        _fail("SOURCE_PACKET_SCHEMA_VERSION_MISMATCH")
    if packet.get("contract_version") != contract["source_contract_version"]:
        _fail("SOURCE_CONTRACT_VERSION_MISMATCH")
    _require_all_false(packet.get("authority"), "SOURCE_AUTHORITY_INVALID")
    bridge_sha256 = packet.get("packet_sha256")
    if not isinstance(bridge_sha256, str) or SHA256_RE.fullmatch(bridge_sha256) is None:
        _fail("SOURCE_BRIDGE_SHA256_INVALID")
    capture_date, capture_hhmm = _source_path_fields(packet)

    coverage = packet.get("five_axis")
    if not isinstance(coverage, dict):
        _fail("SOURCE_FIVE_AXIS_INVALID")
    axis_rows = _axis_rows(coverage, contract)
    symbols = _symbol_rows(packet, contract)
    stages = _stage_rows(symbols, contract)

    source_summary = packet.get("summary") or {}
    stage_counts = {row["stage_id"]: row["member_count"] for row in stages}
    entry_wait = stage_counts.get("ENTRY_WAIT", 0)
    entry_blocked = stage_counts.get("ENTRY_BLOCKED", 0)
    if source_summary.get("symbol_count") != len(symbols):
        _fail("SUMMARY_SYMBOL_COUNT_MISMATCH")
    if source_summary.get("entry_wait_count") != entry_wait:
        _fail("SUMMARY_ENTRY_WAIT_COUNT_MISMATCH")
    if source_summary.get("entry_blocked_count") != entry_blocked:
        _fail("SUMMARY_ENTRY_BLOCKED_COUNT_MISMATCH")

    aggregate_policy = packet.get("aggregate_policy") or {}
    explanation = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "mode": contract["mode"],
        "generated_at": packet.get("generated_at"),
        "operational_date_kst": packet.get("operational_date_kst"),
        "source_generation_id": packet.get("source_generation_id"),
        "source_bridge_sha256": bridge_sha256,
        "source_bridge_contract_version": packet.get("contract_version"),
        "source_decision_sha256": packet.get("source_decision_sha256"),
        "source_capture_date": capture_date,
        "source_capture_hhmm": capture_hhmm,
        "aggregate_policy": copy.deepcopy(aggregate_policy),
        "five_axis_explained": axis_rows,
        "stages": stages,
        "symbols": symbols,
        "exit_priority_ladder": _ladder_rows(contract),
        "summary": {
            "symbol_count": len(symbols),
            "axis_required_count": coverage.get("required_count"),
            "axis_defined_count": coverage.get("defined_count"),
            "axis_hold_cause_count": sum(row["is_hold_cause"] for row in axis_rows),
            "entry_wait_count": entry_wait,
            "entry_blocked_count": entry_blocked,
            "position_stage_member_count": sum(
                row["member_count"] for row in stages if row["kind"] == "POSITION"
            ),
            "stage_member_counts": stage_counts,
            "aggregate_policy_status": aggregate_policy.get("status"),
        },
        "authority": copy.deepcopy(contract["authority"]),
    }
    _require_all_false(explanation["authority"])
    return explanation


def assemble(bridge_packet: dict, contract: dict | None = None) -> dict:
    """Validate the original bridge before assembling and hashing output."""
    contract = load_contract() if contract is None else validate_contract(contract)
    try:
        validated = BRIDGE.validate_output(bridge_packet)
    except BRIDGE.CryptoAxisTradeBridgeError as exc:
        raise CryptoAxisTradeBridgeExplanationError(
            f"SOURCE_BRIDGE_INVALID:{exc}"
        ) from exc
    explanation = _assemble_unsigned(validated, contract)
    explanation["payload_sha256"] = payload_sha256(explanation)
    return explanation


def explain(bridge_packet: dict, *, contract: dict | None = None) -> dict:
    """Revalidate one bridge packet with its producer, then relabel it."""
    return assemble(bridge_packet, contract)


def validate_output(
    explanation: dict, *, bridge_packet: dict, contract: dict | None = None,
) -> dict:
    """Require exact derivation from the supplied, producer-validated source.

    A matching payload hash alone cannot establish source provenance.
    """
    contract = load_contract() if contract is None else validate_contract(contract)
    expected_keys = {
        "schema_version", "contract_version", "mode", "generated_at",
        "operational_date_kst", "source_generation_id", "source_bridge_sha256",
        "source_bridge_contract_version", "source_decision_sha256",
        "source_capture_date", "source_capture_hhmm", "aggregate_policy",
        "five_axis_explained", "stages", "symbols", "exit_priority_ladder",
        "summary", "authority", "payload_sha256",
    }
    if not isinstance(explanation, dict) or set(explanation) != expected_keys:
        _fail("OUTPUT_SCHEMA_MISMATCH")
    if explanation["schema_version"] != OUTPUT_SCHEMA_VERSION:
        _fail("OUTPUT_SCHEMA_VERSION_MISMATCH")
    if explanation["contract_version"] != contract["contract_version"]:
        _fail("OUTPUT_CONTRACT_VERSION_MISMATCH")
    claimed = explanation["payload_sha256"]
    if not isinstance(claimed, str) or SHA256_RE.fullmatch(claimed) is None:
        _fail("PAYLOAD_SHA256_INVALID")
    unsigned = copy.deepcopy(explanation)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != claimed:
        _fail("PAYLOAD_SHA256_MISMATCH")
    _require_all_false(explanation["authority"])
    if explanation["authority"] != contract["authority"]:
        _fail("OUTPUT_AUTHORITY_BLOCK_MISMATCH")
    if explanation["exit_priority_ladder"] != _ladder_rows(contract):
        _fail("EXIT_PRIORITY_DRIFT")
    stage_ids = [row["stage_id"] for row in explanation["stages"]]
    if stage_ids != [row["stage_id"] for row in contract["stages"]]:
        _fail("STAGE_SET_MISMATCH")
    members: list[str] = []
    for row in explanation["stages"]:
        if row["member_count"] != len(row["members"]):
            _fail(f"STAGE_MEMBER_COUNT_MISMATCH:{row['stage_id']}")
        if not row["members"] and row["empty_reason"] is None:
            _fail(f"STAGE_EMPTY_REASON_MISSING:{row['stage_id']}")
        members.extend(row["members"])
    if sorted(members) != sorted(row["market"] for row in explanation["symbols"]):
        _fail("STAGE_MEMBERSHIP_INCOMPLETE")
    if len(set(members)) != len(members):
        _fail("STAGE_MEMBERSHIP_DUPLICATE")
    rebuilt = explain(bridge_packet, contract=contract)
    if canonical_json(rebuilt) != canonical_json(explanation):
        _fail("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(explanation)


def _fmt(value: object) -> str:
    return "-" if value is None or value == "" else str(value)


def _join(values: list) -> str:
    return ", ".join(str(value) for value in values) if values else "-"


def render_markdown(
    explanation: dict, *, bridge_packet: dict, contract: dict | None = None,
) -> str:
    """Validate source derivation before returning a public explanation."""
    validated = validate_output(explanation, bridge_packet=bridge_packet, contract=contract)
    return _render_markdown(validated)


def _render_markdown(explanation: dict) -> str:
    """Format an explanation already produced or verified by this module."""
    summary = explanation["summary"]
    lines: list[str] = []
    lines.append("# Crypto 5축 진입·청산 설명 (읽기 전용)")
    lines.append("")
    lines.append(f"- 생성 시각(UTC): {_fmt(explanation['generated_at'])}")
    lines.append(f"- 운영일(KST): {_fmt(explanation['operational_date_kst'])}")
    lines.append(f"- 원본 브리지 생성 ID: {_fmt(explanation['source_generation_id'])}")
    lines.append(f"- 원본 브리지 payload_sha256: {_fmt(explanation['source_bridge_sha256'])}")
    lines.append(
        f"- 종합 Regime 정책: {_fmt(summary['aggregate_policy_status'])} "
        f"(현재 승인된 국면: {_join(explanation['aggregate_policy'].get('authorized_regimes') or [])})"
    )
    lines.append(
        "- 권한: 모든 실행 권한 false. 이 문서는 기존 판정의 설명일 뿐 "
        "매수·매도·주문 허가가 아니다."
    )
    lines.append("")

    lines.append(
        f"## 1. 공식 5개 축 현황 "
        f"({_fmt(summary['axis_defined_count'])}/{_fmt(summary['axis_required_count'])} 확보)"
    )
    lines.append("")
    lines.append("| 축 | 상태 | 관측일 | 사용 가능 시각 | 진입 보류 사유 | 경고 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for row in explanation["five_axis_explained"]:
        lines.append(
            f"| {row['axis_label']}({row['axis']}) | {row['status_label']}({row['status']}) "
            f"| {_fmt(row['observation_date'])} | {_fmt(row['available_at'])} "
            f"| {'예' if row['is_hold_cause'] else '아니오'} | {_join(row['warnings'])} |"
        )
    lines.append("")

    lines.append(f"## 2. 단계별 종목 (총 {summary['symbol_count']}종목)")
    lines.append("")
    for row in explanation["stages"]:
        lines.append(f"### {row['label']} ({row['member_count']}종목)")
        lines.append("")
        if row["members"]:
            for market in row["members"]:
                lines.append(f"- {market}")
        else:
            reason = row["empty_reason"]
            lines.append(f"- (없음) 사유 `{reason['code']}`: {reason['text']}")
        lines.append("")

    lines.append("## 3. 종목별 설명")
    lines.append("")
    for row in explanation["symbols"]:
        lines.append(f"### {row['market']} ({_fmt(row['canonical_asset_id'])})")
        lines.append("")
        lines.append(f"- 단계: {row['entry_label']} (`{row['entry_state']}`)")
        lines.append(
            f"- 상류 판정: `{_fmt(row['upstream_state'])}` / `{_fmt(row['upstream_reason'])}`"
        )
        for reason in row["entry_reason_text"]:
            lines.append(f"- 진입 사유 `{reason['code']}`: {reason['text']}")
        if row["axis_holds"]:
            for hold in row["axis_holds"]:
                lines.append(f"- 진입 보류 축 `{hold['axis']}`: {hold['text']}")
        else:
            lines.append("- 진입 보류 축: 이 종목의 진입 사유가 지목한 미확보 축이 없다.")
        lines.append(f"- 청산 단계: {row['exit_label']} (`{row['exit_state']}`)")
        for reason in row["exit_reason_text"]:
            lines.append(f"- 청산 사유 `{reason['code']}`: {reason['text']}")
        lines.append(
            "- 청산 우선순위: "
            + " → ".join(entry["category"] for entry in row["exit_ladder"])
            + " (전 종목 동일)"
        )
        lines.append("")

    lines.append("## 4. 청산 우선순위 사다리")
    lines.append("")
    for entry in explanation["exit_priority_ladder"]:
        lines.append(f"{entry['rank']}. `{entry['category']}` — {entry['label']}")
    lines.append("")
    return "\n".join(lines) + "\n"


def output_directory(explanation: dict, output_root: Path) -> Path:
    return (
        Path(output_root) / explanation["source_capture_date"] /
        explanation["source_capture_hhmm"] / explanation["source_generation_id"]
    )


def _write_atomic(target: Path, rendered: str) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def populate(bridge_packet_path: Path, *, output_root: Path) -> dict:
    explanation = explain(_read_json(Path(bridge_packet_path)))
    directory = output_directory(explanation, output_root)
    packet_path = directory / "packet.json"
    markdown_path = directory / "briefing.md"
    rendered = {
        packet_path: json.dumps(explanation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        markdown_path: _render_markdown(explanation),
    }

    existing = [path for path in rendered if path.exists()]
    if existing:
        for path in existing:
            if path.read_text(encoding="utf-8") != rendered[path]:
                _fail(f"EXISTING_OUTPUT_DRIFT_OR_TAMPER:{path}")
        if len(existing) != len(rendered):
            _fail(f"EXISTING_OUTPUT_INCOMPLETE:{directory}")
        outcome = "verified_existing"
    else:
        directory.mkdir(parents=True, exist_ok=True)
        for path, text in rendered.items():
            _write_atomic(path, text)
        outcome = "populated"
    return {
        "outcome": outcome,
        "path": str(packet_path),
        "markdown_path": str(markdown_path),
        "payload_sha256": explanation["payload_sha256"],
        "source_generation_id": explanation["source_generation_id"],
    }


def _write_github_output(result: dict) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        for key in ("outcome", "path", "payload_sha256", "source_generation_id"):
            handle.write(f"{key}={result[key]}\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Explain one committed crypto_axis_trade_bridge packet. "
            f"Canonical committed root: {CANONICAL_OUTPUT_ROOT}"
        ),
    )
    parser.add_argument("--bridge-packet", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    result = populate(args.bridge_packet, output_root=args.output_root)
    _write_github_output(result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
