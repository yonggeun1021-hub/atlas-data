#!/usr/bin/env python3
"""P8-16 Crypto funnel and PAPER-decision briefing read model.

The sole input is one exact, fully revalidated P1/P3/P4/P5/P9
``crypto_paper_decision_snapshot_packet/1`` generation.  This module does
not capture market data, calculate a factor, promote a candidate, authorize
a PAPER order, or call any network/private/order endpoint.  It projects the
already-derived facts into one JSON/API contract and one deterministic Korean
Markdown rendering so their counts, reasons, freshness and authority cannot
drift apart.

P10/P7 PAPER account and exit state is intentionally not a public input yet.
Until a separately approved, redacted cross-Mac contract exists, the
PAPER_POSITION stage is ``count=null``/``status=UNKNOWN`` -- never a false
zero and never a private holding, quantity, fee, or P&L copied into public
evidence.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONTRACT_PATH = ROOT / "config" / "crypto_funnel_briefing_contract.json"
OUTPUT_ROOT = ROOT / "evidence" / "crypto_funnel_briefing"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_TIME_BASIS_FIELDS = {
    "captured_at_utc", "captured_at_kst", "operational_date_kst",
    "path_time_basis", "scheduled_for", "started_at", "completed_at",
}


class CryptoFunnelBriefingError(ValueError):
    """Fail-closed P8-16 source, derivation, or persistence violation."""


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CryptoFunnelBriefingError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DECISION = _load(
    "crypto_funnel_briefing_decision",
    "decision/crypto_paper_decision_snapshot.py",
)


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise CryptoFunnelBriefingError(f"FILE_HASH_FAILED:{path}:{exc}") from exc


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoFunnelBriefingError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "crypto_funnel_briefing_contract/1",
        "output_schema_version": "crypto_funnel_briefing/1",
        "source_schema_version": DECISION.OUTPUT_SCHEMA_VERSION,
        "status": "READ_MODEL_ONLY",
        "axis_order": ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"],
        "funnel_order": [
            "OBSERVATION_POOL", "TRADEABLE_UNIVERSE", "FOCUSED_REVIEW",
            "PAPER_READY", "PAPER_POSITION",
        ],
        "missing_position_policy": "UNKNOWN_NULL_NOT_ZERO",
        "authority": {
            "briefing_read_model_only": True,
            "paper_order_authorized": False,
            "exchange_order_authorized": False,
            "withdrawal_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "real_capital_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if value != expected:
        raise CryptoFunnelBriefingError("CONTRACT_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _relative_source_path(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(ROOT.resolve()))
    except ValueError as exc:
        raise CryptoFunnelBriefingError("SOURCE_PATH_OUTSIDE_REPOSITORY") from exc


def _resolve_source_path(path: str, *, allow_external_sources: bool) -> Path:
    if not isinstance(path, str) or not path:
        raise CryptoFunnelBriefingError("SOURCE_REF_PATH_INVALID")
    raw = Path(path)
    resolved = raw.resolve() if raw.is_absolute() else (ROOT / raw).resolve()
    if not allow_external_sources:
        try:
            resolved.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise CryptoFunnelBriefingError("SOURCE_REF_PATH_ESCAPE") from exc
    if not resolved.is_file():
        raise CryptoFunnelBriefingError("SOURCE_REF_FILE_MISSING")
    return resolved


def _axis_rows(source: dict, contract: dict) -> list[dict]:
    factors = source["crypto_regime_five_axis"]
    if not isinstance(factors, dict) or set(factors) != set(contract["axis_order"]):
        raise CryptoFunnelBriefingError("SOURCE_AXIS_SET_INVALID")
    return [{"axis": axis, **copy.deepcopy(factors[axis])} for axis in contract["axis_order"]]


def _require_explicit_time_basis(source: dict) -> None:
    if not REQUIRED_TIME_BASIS_FIELDS.issubset(source):
        raise CryptoFunnelBriefingError("SOURCE_EXPLICIT_TIME_BASIS_REQUIRED")


def _candidate_rows(source: dict) -> list[dict]:
    rows = []
    for item in source["candidates"]:
        p5_09 = item.get("p5_09")
        rows.append({
            "market": item["market"],
            "canonical_asset_id": item.get("canonical_asset_id"),
            "state": item["state"],
            "reason": item["reason"],
            "freshness_capped": item["freshness_capped"],
            "freshness_cap_reason": item["freshness_cap_reason"],
            "p3_12_state": item.get("p3_12_state"),
            "trend": copy.deepcopy(item["p5_08"]["criteria"].get("TREND")),
            "relative_strength": copy.deepcopy(
                item["p5_08"]["criteria"].get("RELATIVE_STRENGTH")
            ),
            "liquidity": copy.deepcopy(
                item["p5_08"]["criteria"].get("VOLUME_LIQUIDITY")
            ),
            "trigger": copy.deepcopy(
                p5_09["criteria"].get("BREAKOUT_OR_PULLBACK") if p5_09 else None
            ),
            "order_draft": copy.deepcopy(p5_09.get("order_draft") if p5_09 else None),
            "authority": copy.deepcopy(item["authority"]),
        })
    return rows


def _state_counts(candidates: list[dict]) -> dict:
    result = {}
    for row in candidates:
        result[row["state"]] = result.get(row["state"], 0) + 1
    return dict(sorted(result.items()))


def _render_markdown(packet: dict) -> str:
    lines = [
        "# Crypto PAPER 브리핑",
        "",
        f"- 기준 시각: `{packet['as_of']['captured_at_kst']}` (KST)",
        f"- source generation: `{packet['source_ref']['generation_id']}`",
        "- 모드: `PAPER · READ_MODEL_ONLY`",
        "",
        "## 시장판정 5축",
        "",
    ]
    for row in packet["regime"]["axes"]:
        warnings = ", ".join(row.get("warnings") or []) or "none"
        lines.append(f"- {row['axis']}: `{row['status']}` — {warnings}")
    lines.extend(["", "## Funnel", ""])
    for stage in packet["funnel"]["stages"]:
        count = "UNKNOWN" if stage["count"] is None else str(stage["count"])
        suffix = f" — {stage['reason']}" if stage.get("reason") else ""
        lines.append(f"- {stage['stage']}: `{count}`{suffix}")
    lines.extend(["", "## 데이터 freshness", ""])
    for key in ("upbit_universe", "market_evidence", "realtime", "overall"):
        lines.append(f"- {key}: `{packet['freshness'][key]}`")
    lines.extend(["", "## 후보", ""])
    if not packet["candidates"]:
        lines.append("- 없음")
    else:
        for row in packet["candidates"]:
            lines.append(f"- {row['market']}: `{row['state']}` — {row['reason']}")
    lines.extend(["", "## WAIT / BLOCKED 근거", ""])
    for reason in packet["reasons"]:
        lines.append(f"- `{reason}`")
    lines.extend([
        "",
        "## 권한",
        "",
        "- 브리핑 읽기 전용: `true`",
        "- PAPER 주문 권한: `false`",
        "- Upbit 실제 주문·출금·REAL·Production·Trading 권한: `false`",
        "",
    ])
    return "\n".join(lines)


def _assemble(source: dict, source_path: str, source_file_sha256: str, contract: dict) -> dict:
    _require_explicit_time_basis(source)
    axes = _axis_rows(source, contract)
    candidates = _candidate_rows(source)
    counts = source["funnel_counts"]
    stages = [
        {"stage": "OBSERVATION_POOL", "count": counts["observation_pool_count"], "reason": None},
        {"stage": "TRADEABLE_UNIVERSE", "count": counts["tradeable_universe_count"], "reason": None},
        {"stage": "FOCUSED_REVIEW", "count": counts["focused_review_count"], "reason": None},
        {"stage": "PAPER_READY", "count": counts["paper_ready_count"], "reason": None},
        {
            "stage": "PAPER_POSITION", "count": None,
            "reason": "P10_P7_REDACTED_POSITION_SUMMARY_NOT_WIRED",
        },
    ]
    reasons = sorted(set(
        list(source["derivation_notes"])
        + [row["reason"] for row in candidates]
        + ["P10_P7_REDACTED_POSITION_SUMMARY_NOT_WIRED"]
    ))
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": contract["status"],
        "source_ref": {
            "role": "crypto_paper_decision_snapshot",
            "path": source_path,
            "file_sha256": source_file_sha256,
            "generation_id": source["generation_id"],
            "payload_sha256": source["payload_sha256"],
            "source_commit": source["source_commit"],
        },
        "source_packet": copy.deepcopy(source),
        "as_of": {
            key: source[key] for key in (
                "captured_at_utc", "captured_at_kst", "operational_date_kst",
                "path_time_basis", "scheduled_for", "started_at", "completed_at",
            )
        },
        "regime": {
            "aggregate": "UNKNOWN",
            "axes": axes,
            "defined_axis_count": sum(row["status"] == "DEFINED" for row in axes),
            "required_axis_count": len(axes),
            "aggregate_authorized": False,
        },
        "funnel": {
            "stages": stages,
            "candidate_state_counts": _state_counts(candidates),
            "order_draft_count": sum(row["order_draft"] is not None for row in candidates),
        },
        "candidates": candidates,
        "freshness": copy.deepcopy(source["freshness_status"]),
        "finalized_candle": copy.deepcopy(source["finalized_candle_attestation"]),
        "realtime_quote_orderbook_status": source["freshness_status"]["realtime"],
        "reasons": reasons,
        "authority": copy.deepcopy(contract["authority"]),
        "rendered_markdown": None,
    }
    packet["rendered_markdown"] = _render_markdown(packet)
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def build_briefing(
    source_packet: dict,
    *,
    source_path: str,
    source_file_sha256: str,
    contract: dict | None = None,
    allow_external_sources: bool = False,
) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    try:
        source = DECISION.validate_output(
            copy.deepcopy(source_packet), allow_external_sources=allow_external_sources
        )
    except DECISION.CryptoPaperDecisionSnapshotError as exc:
        raise CryptoFunnelBriefingError(f"SOURCE_DECISION_INVALID:{exc}") from exc
    if source["schema_version"] != contract["source_schema_version"]:
        raise CryptoFunnelBriefingError("SOURCE_SCHEMA_VERSION_INVALID")
    if not isinstance(source_file_sha256, str) or not SHA256_RE.fullmatch(source_file_sha256):
        raise CryptoFunnelBriefingError("SOURCE_FILE_SHA256_INVALID")
    return validate_briefing(
        _assemble(source, source_path, source_file_sha256, contract),
        contract,
        allow_external_sources=allow_external_sources,
    )


def validate_briefing(
    packet: dict,
    contract: dict | None = None,
    *,
    allow_external_sources: bool = False,
) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "schema_version", "contract_version", "status", "source_ref", "source_packet",
        "as_of", "regime", "funnel", "candidates", "freshness", "finalized_candle",
        "realtime_quote_orderbook_status", "reasons", "authority", "rendered_markdown",
        "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise CryptoFunnelBriefingError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status") != contract["status"]
        or packet.get("authority") != contract["authority"]
    ):
        raise CryptoFunnelBriefingError("OUTPUT_IDENTITY_INVALID")
    digest = packet.get("packet_sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise CryptoFunnelBriefingError("OUTPUT_SHA256_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoFunnelBriefingError("OUTPUT_SHA256_MISMATCH")
    ref = packet.get("source_ref")
    if not isinstance(ref, dict) or set(ref) != {
        "role", "path", "file_sha256", "generation_id", "payload_sha256", "source_commit",
    } or ref["role"] != "crypto_paper_decision_snapshot":
        raise CryptoFunnelBriefingError("SOURCE_REF_INVALID")
    path = _resolve_source_path(ref["path"], allow_external_sources=allow_external_sources)
    if _file_sha256(path) != ref["file_sha256"]:
        raise CryptoFunnelBriefingError("SOURCE_FILE_SHA256_MISMATCH")
    on_disk = _read_json(path)
    if canonical_json(on_disk) != canonical_json(packet["source_packet"]):
        raise CryptoFunnelBriefingError("SOURCE_EMBEDDED_PACKET_MISMATCH")
    try:
        source = DECISION.validate_output(
            copy.deepcopy(on_disk), allow_external_sources=allow_external_sources
        )
    except DECISION.CryptoPaperDecisionSnapshotError as exc:
        raise CryptoFunnelBriefingError(f"SOURCE_DECISION_INVALID:{exc}") from exc
    expected = _assemble(source, ref["path"], ref["file_sha256"], contract)
    if canonical_json(expected) != canonical_json(packet):
        raise CryptoFunnelBriefingError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(packet)


def output_path(source: dict, output_root: Path = OUTPUT_ROOT) -> Path:
    return (
        Path(output_root) / source["capture_date"] / source["capture_hhmm"]
        / source["generation_id"] / "packet.json"
    )


def _atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".packet.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def populate(source_path: Path, output_root: Path = OUTPUT_ROOT) -> dict:
    source_path = Path(source_path)
    source = _read_json(source_path)
    relative = _relative_source_path(source_path)
    file_sha = _file_sha256(source_path)
    packet = build_briefing(
        source, source_path=relative, source_file_sha256=file_sha
    )
    target = output_path(source, output_root)
    if target.exists():
        existing = validate_briefing(_read_json(target))
        if canonical_json(existing) != canonical_json(packet):
            raise CryptoFunnelBriefingError("OUTPUT_PATH_COLLISION")
        outcome = "verified_existing"
    else:
        _atomic_write(target, packet)
        validate_briefing(_read_json(target))
        outcome = "populated"
    return {"outcome": outcome, "path": str(target), "record": packet}


def _github_output(result: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"outcome={result['outcome']}\n")
        handle.write(f"path={result['path']}\n")
        handle.write(f"generation_id={result['record']['source_ref']['generation_id']}\n")
        handle.write(f"packet_sha256={result['record']['packet_sha256']}\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-packet", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = populate(args.decision_packet)
    except CryptoFunnelBriefingError as exc:
        print(f"P8_16_CRYPTO_FUNNEL_BRIEFING_BLOCKED:{exc}", file=sys.stderr)
        return 1
    _github_output(result)
    print(json.dumps({
        "outcome": result["outcome"],
        "path": result["path"],
        "generation_id": result["record"]["source_ref"]["generation_id"],
        "packet_sha256": result["record"]["packet_sha256"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
