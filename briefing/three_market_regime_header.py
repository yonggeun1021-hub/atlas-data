#!/usr/bin/env python3
"""P8-04 deterministic US/KR/Crypto Regime briefing header.

This module only validates and re-presents three upstream Regime outputs.  It
does not rank markets, interpret UNKNOWN, select a strategy, or create an
action.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "three_market_regime_header_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _load_regime_output():
    path = ROOT / "regime" / "output_contract.py"
    spec = importlib.util.spec_from_file_location("atlas_regime_output_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"REGIME_OUTPUT_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


regime_output = _load_regime_output()


class ThreeMarketRegimeHeaderError(ValueError):
    """Fail-closed P8-04 input or output contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ThreeMarketRegimeHeaderError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "briefing_three_market_regime_header/1",
        "output_schema_version": "three_market_regime_header/2",
        "source_contract_version": "regime_output/v1",
        "required_markets": ["US", "KR", "CRYPTO"],
        "market_labels": {"US": "US", "KR": "Korea", "CRYPTO": "Crypto"},
        "slots": ["morning", "evening"],
        "status": "HEADER_ASSEMBLED_NO_DECISION_AUTHORITY",
        "source_ordering": "REQUIRED_MARKET_ORDER",
        "confidence_policy": "PRESERVE_SOURCE_NULL",
        "authority": {
            "briefing_read_model_only": True,
            "market_ranking_authorized": False,
            "favorable_market_selection_authorized": False,
            "regime_interpretation_authorized": False,
            "strategy_eligibility_authorized": False,
            "action_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ThreeMarketRegimeHeaderError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ThreeMarketRegimeHeaderError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _source_row(source: dict, contract: dict, header_generated_at: str) -> tuple[dict, dict]:
    try:
        validated = regime_output.validate_output(copy.deepcopy(source))
        source_generated = regime_output.parse_utc(
            validated["generated_at"], "source.generated_at"
        )
        header_generated = regime_output.parse_utc(
            header_generated_at, "header.generated_at"
        )
    except regime_output.OutputContractError as exc:
        raise ThreeMarketRegimeHeaderError(f"SOURCE_REGIME_INVALID:{exc}") from exc
    if source_generated > header_generated:
        raise ThreeMarketRegimeHeaderError(
            f"SOURCE_FROM_FUTURE:{validated['market']}:{validated['generated_at']}"
        )
    market = validated["market"]
    if validated["contract_version"] != contract["source_contract_version"]:
        raise ThreeMarketRegimeHeaderError(f"SOURCE_CONTRACT_INVALID:{market}")
    coverage = validated["coverage"]
    return validated, {
        "market": market,
        "label": contract["market_labels"][market],
        "market_timezone": validated["market_timezone"],
        "regime": validated["regime"],
        "direction": validated["direction"],
        "confidence": validated["confidence"],
        "source_generated_at": validated["generated_at"],
        "coverage": {
            "defined_count": coverage["defined_count"],
            "required_count": coverage["required_count"],
            "ratio": coverage["ratio"],
            "minimum_gate_status": coverage["minimum_gate_status"],
        },
        "evidence_as_of": copy.deepcopy(validated["evidence_as_of"]),
        "available_as_of": validated["available_as_of"],
        "warnings": list(validated["warnings"]),
        "source_sha256": payload_sha256(validated),
    }


def _assemble_header(
    sources: list[dict],
    slot: str,
    generated_at: str,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if slot not in contract["slots"]:
        raise ThreeMarketRegimeHeaderError(f"SLOT_INVALID:{slot}")
    try:
        regime_output.parse_utc(generated_at, "header.generated_at")
    except regime_output.OutputContractError as exc:
        raise ThreeMarketRegimeHeaderError(f"GENERATED_AT_INVALID:{exc}") from exc
    if not isinstance(sources, list):
        raise ThreeMarketRegimeHeaderError("SOURCES_NOT_LIST")

    by_market = {}
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ThreeMarketRegimeHeaderError(f"SOURCE_NOT_OBJECT:{index}")
        market = source.get("market")
        if market in by_market:
            raise ThreeMarketRegimeHeaderError(f"SOURCE_MARKET_DUPLICATE:{market}")
        if market not in contract["required_markets"]:
            raise ThreeMarketRegimeHeaderError(f"SOURCE_MARKET_UNEXPECTED:{market}")
        validated, row = _source_row(source, contract, generated_at)
        by_market[market] = {"source": validated, "row": row}

    missing = [market for market in contract["required_markets"] if market not in by_market]
    if missing:
        raise ThreeMarketRegimeHeaderError(f"SOURCE_MARKET_MISSING:{','.join(missing)}")

    markets = [by_market[market]["row"] for market in contract["required_markets"]]
    source_packets = [
        by_market[market]["source"] for market in contract["required_markets"]
    ]
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "slot": slot,
        "generated_at": generated_at,
        "status": contract["status"],
        "markets": markets,
        "source_packets": source_packets,
        "summary": {
            "market_count": len(markets),
            "required_market_count": len(contract["required_markets"]),
            "all_markets_present": True,
            "ranked_market": None,
            "favorable_market": None,
            "action": None,
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "REGIME_SCORING_NOT_AUTHORIZED",
            "MARKET_RANKING_NOT_AUTHORIZED",
            "FAVORABLE_MARKET_SELECTION_NOT_AUTHORIZED",
            "ACTION_GENERATION_NOT_AUTHORIZED",
            "PRODUCTION_WIRING_NOT_IMPLEMENTED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def build_header(
    sources: list[dict],
    slot: str,
    generated_at: str,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    return validate_header(
        _assemble_header(sources, slot, generated_at, contract), contract
    )


def validate_header(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "slot", "generated_at", "status",
        "markets", "source_packets", "summary", "authority",
        "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise ThreeMarketRegimeHeaderError("HEADER_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("slot") not in contract["slots"]
        or packet.get("status") != contract["status"]
        or packet.get("authority") != contract["authority"]
    ):
        raise ThreeMarketRegimeHeaderError("HEADER_IDENTITY_INVALID")
    try:
        header_generated = regime_output.parse_utc(
            packet.get("generated_at"), "header.generated_at"
        )
    except regime_output.OutputContractError as exc:
        raise ThreeMarketRegimeHeaderError(f"HEADER_TIME_INVALID:{exc}") from exc

    rows = packet.get("markets")
    if not isinstance(rows, list) or [row.get("market") for row in rows if isinstance(row, dict)] != contract["required_markets"]:
        raise ThreeMarketRegimeHeaderError("HEADER_MARKET_ORDER_INVALID")
    row_fields = {
        "market", "label", "market_timezone", "regime", "direction", "confidence",
        "source_generated_at", "coverage", "evidence_as_of", "available_as_of",
        "warnings", "source_sha256",
    }
    source_contract = regime_output.load_contract()
    for row in rows:
        market = row["market"]
        if set(row) != row_fields:
            raise ThreeMarketRegimeHeaderError(f"HEADER_MARKET_FIELDS_MISMATCH:{market}")
        if (
            row["label"] != contract["market_labels"][market]
            or row["market_timezone"] != source_contract["market_timezones"][market]
            or row["regime"] not in source_contract["runtime_authorized_regimes"]
            or row["direction"] not in source_contract["runtime_authorized_directions"]
            or row["confidence"] is not None
        ):
            raise ThreeMarketRegimeHeaderError(f"HEADER_MARKET_VALUE_INVALID:{market}")
        try:
            source_generated = regime_output.parse_utc(
                row["source_generated_at"], f"{market}.source_generated_at"
            )
        except regime_output.OutputContractError as exc:
            raise ThreeMarketRegimeHeaderError(f"HEADER_SOURCE_TIME_INVALID:{market}:{exc}") from exc
        if source_generated > header_generated:
            raise ThreeMarketRegimeHeaderError(f"HEADER_SOURCE_FROM_FUTURE:{market}")
        coverage = row["coverage"]
        if not isinstance(coverage, dict) or set(coverage) != {
            "defined_count", "required_count", "ratio", "minimum_gate_status"
        }:
            raise ThreeMarketRegimeHeaderError(f"HEADER_COVERAGE_INVALID:{market}")
        if (
            type(coverage["defined_count"]) is not int
            or type(coverage["required_count"]) is not int
            or coverage["required_count"] != len(source_contract["required_axes"])
            or coverage["ratio"] != f"{coverage['defined_count']}/{coverage['required_count']}"
            or coverage["minimum_gate_status"] != source_contract["minimum_coverage_policy"]
        ):
            raise ThreeMarketRegimeHeaderError(f"HEADER_COVERAGE_VALUE_INVALID:{market}")
        warnings = row["warnings"]
        warning_pattern = re.compile(source_contract["warning_code_pattern"])
        if (
            not isinstance(warnings, list)
            or warnings != sorted(set(warnings))
            or any(not isinstance(item, str) or warning_pattern.fullmatch(item) is None for item in warnings)
        ):
            raise ThreeMarketRegimeHeaderError(f"HEADER_WARNINGS_INVALID:{market}")
        if not isinstance(row["source_sha256"], str) or SHA256_RE.fullmatch(row["source_sha256"]) is None:
            raise ThreeMarketRegimeHeaderError(f"HEADER_SOURCE_SHA_INVALID:{market}")

    expected_summary = {
        "market_count": 3,
        "required_market_count": 3,
        "all_markets_present": True,
        "ranked_market": None,
        "favorable_market": None,
        "action": None,
    }
    if packet.get("summary") != expected_summary:
        raise ThreeMarketRegimeHeaderError("HEADER_SUMMARY_INVALID")
    expected_unresolved = [
        "REGIME_SCORING_NOT_AUTHORIZED",
        "MARKET_RANKING_NOT_AUTHORIZED",
        "FAVORABLE_MARKET_SELECTION_NOT_AUTHORIZED",
        "ACTION_GENERATION_NOT_AUTHORIZED",
        "PRODUCTION_WIRING_NOT_IMPLEMENTED",
    ]
    if packet.get("unresolved_boundaries") != expected_unresolved:
        raise ThreeMarketRegimeHeaderError("HEADER_BOUNDARIES_INVALID")
    digest = packet.get("packet_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise ThreeMarketRegimeHeaderError("HEADER_SHA_INVALID")
    normalized = copy.deepcopy(packet)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise ThreeMarketRegimeHeaderError("HEADER_SHA_MISMATCH")
    try:
        expected = _assemble_header(
            packet.get("source_packets"),
            packet["slot"],
            packet["generated_at"],
            contract,
        )
    except ThreeMarketRegimeHeaderError as exc:
        raise ThreeMarketRegimeHeaderError(
            f"HEADER_SOURCE_PACKET_INVALID:{exc}"
        ) from exc
    if canonical_json(packet) != canonical_json(expected):
        raise ThreeMarketRegimeHeaderError("HEADER_DERIVATION_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ThreeMarketRegimeHeaderError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(source_paths: list[Path], slot: str, generated_at: str, output_path: Path) -> int:
    try:
        sources = [_read_json(path) for path in source_paths]
        write_json_atomic(output_path, build_header(sources, slot, generated_at))
        return 0
    except (ThreeMarketRegimeHeaderError, OSError, TypeError, ValueError) as exc:
        print(f"Three-market Regime header failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a non-interpretive 3-market Regime header")
    parser.add_argument("sources", type=Path, nargs=3)
    parser.add_argument("--slot", choices=("morning", "evening"), required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.sources, args.slot, args.generated_at, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
