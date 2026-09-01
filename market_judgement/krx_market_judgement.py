#!/usr/bin/env python3
"""Build the deterministic, PAPER-only KRX market-judgement receipt.

The adapter reads canonical retained inputs and never fetches market data.  It
preserves KOSPI/KOSDAQ breadth, turnover and sector-relative-strength evidence,
but it does not infer a Regime when scoring or freshness authority is absent.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "krx_market_judgement_contract.json"
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
KST = ZoneInfo("Asia/Seoul")


class KrxMarketJudgementError(ValueError):
    """A canonical KRX input or derived receipt is invalid."""


def fail(code: str, detail: str = "") -> None:
    suffix = f":{detail}" if detail else ""
    raise KrxMarketJudgementError(f"{code}{suffix}")


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        fail("CANONICAL_JSON_INVALID", str(exc))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_bytes(path: Path, code: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise KrxMarketJudgementError(code) from exc


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(_read_bytes(path, code).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KrxMarketJudgementError(code) from exc
    if not isinstance(value, dict):
        fail(code, "object required")
    return value


def _file_sha256(path: Path, code: str) -> str:
    return hashlib.sha256(_read_bytes(path, code)).hexdigest()


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_SECOND.fullmatch(value) is None:
        fail(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        fail(code)


def _parse_date(value: object, code: str) -> dt.date:
    if not isinstance(value, str):
        fail(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        fail(code)
    if parsed.isoformat() != value:
        fail(code)
    return parsed


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        fail("SOURCE_VALIDATOR_IMPORT_FAILED", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


KOREA_SIGNALS = _load_module(
    "atlas_krx_judgement_korea_signals",
    ROOT / ".github" / "scripts" / "korea_market_signals.py",
)
KOREA_LEADERSHIP = _load_module(
    "atlas_krx_judgement_korea_leadership",
    ROOT / ".github" / "scripts" / "korea_leadership.py",
)


def _safe(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
        fail("CONTRACT_PATH_INVALID", relative)
    root = Path(root).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        fail("CONTRACT_PATH_INVALID", relative)
    return path


def load_contract(path: Path = CONTRACT_PATH, root: Path = ROOT) -> dict:
    value = _read_json(path, "JUDGEMENT_CONTRACT_INVALID")
    required_top = {
        "schema_version", "contract_version", "input_envelope_version",
        "receipt_version", "market", "source_market", "run_mode",
        "required_axes", "required_markets", "allowed_regimes", "literal_pass",
        "fail_closed_regime", "fail_closed_recommendation",
        "natural_evidence_class", "test_evidence_class",
        "scoring_receipt_version", "bound_contracts", "source_requirements",
        "all_pass_gate_order", "authority",
    }
    if set(value) != required_top:
        fail("JUDGEMENT_CONTRACT_INVALID", "schema")
    if (
        value["schema_version"] != 1
        or value["contract_version"] != "krx_market_judgement/1"
        or value["input_envelope_version"] != "krx_market_judgement_input/1"
        or value["receipt_version"] != "krx_market_judgement_receipt/1"
        or value["market"] != "KRX"
        or value["source_market"] != "KOREA"
        or value["run_mode"] != "PAPER"
        or value["required_axes"]
        != ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"]
        or value["required_markets"] != ["KOSPI", "KOSDAQ"]
        or value["literal_pass"] != "PASS"
        or value["fail_closed_regime"] != "UNKNOWN"
        or value["fail_closed_recommendation"] != "HOLD"
    ):
        fail("JUDGEMENT_CONTRACT_INVALID", "identity")
    if value["all_pass_gate_order"] != [
        "PAPER_SAFETY", "EXACT_SOURCE_HASH", "COMPLETED_BAR",
        "LEADERSHIP_POLICY", "AXIS_COVERAGE", "BREADTH_KOSPI_KOSDAQ",
        "TURNOVER_KOSPI_KOSDAQ", "SECTOR_RELATIVE_STRENGTH",
        "REGIME_SCORING_AUTHORITY", "TTL_POLICY", "FRESHNESS",
        "SCORING_RESULT",
    ]:
        fail("JUDGEMENT_CONTRACT_INVALID", "gate order")
    authority = value.get("authority")
    if not isinstance(authority, dict) or authority.get(
        "paper_market_judgement_input_only"
    ) is not True:
        fail("JUDGEMENT_CONTRACT_INVALID", "authority")
    for key, item in authority.items():
        if key.endswith("_authorized") and key not in {
            "market_observation_authorized", "source_lineage_authorized"
        } and item is not False:
            fail("JUDGEMENT_CONTRACT_INVALID", f"authority:{key}")
    for name, reference in value["bound_contracts"].items():
        if set(reference) != {"path", "sha256", "contract_version"}:
            fail("JUDGEMENT_CONTRACT_INVALID", f"binding:{name}")
        path_value = _safe(root, reference["path"])
        if _file_sha256(path_value, "BOUND_CONTRACT_MISSING") != reference["sha256"]:
            fail("BOUND_CONTRACT_SHA_MISMATCH", name)
        bound = _read_json(path_value, "BOUND_CONTRACT_INVALID")
        if bound.get("contract_version") != reference["contract_version"]:
            fail("BOUND_CONTRACT_VERSION_MISMATCH", name)
    return copy.deepcopy(value)


def _require_exact_hash(path: Path, expected_sha256: str, label: str) -> str:
    if not isinstance(expected_sha256, str) or SHA256.fullmatch(expected_sha256) is None:
        fail("EXPECTED_SOURCE_SHA_INVALID", label)
    actual = _file_sha256(path, "SOURCE_FILE_MISSING")
    if actual != expected_sha256:
        fail("EXACT_SOURCE_HASH_MISMATCH", label)
    return actual


def _active_policy_records(policy: dict, as_of_date: dt.date) -> dict[str, set[str]]:
    expected = {"KOSPI": set(), "KOSDAQ": set()}
    benchmark_count = {"KOSPI": 0, "KOSDAQ": 0}
    for row in policy["records"]:
        start = _parse_date(row["effective_from"], "LEADERSHIP_POLICY_DATE_INVALID")
        end = (
            _parse_date(row["effective_to"], "LEADERSHIP_POLICY_DATE_INVALID")
            if row["effective_to"] is not None
            else None
        )
        if not (start <= as_of_date and (end is None or as_of_date <= end)):
            continue
        market, separator, name = row["series_identity"].partition("::")
        if separator != "::" or market not in expected or not name:
            fail("LEADERSHIP_POLICY_IDENTITY_INVALID")
        if row["role"] == "SECTOR":
            if name in expected[market]:
                fail("LEADERSHIP_POLICY_IDENTITY_DUPLICATE", row["series_identity"])
            expected[market].add(name)
        elif row["role"] == f"{market}_BENCHMARK":
            benchmark_count[market] += 1
    if any(count != 1 for count in benchmark_count.values()):
        fail("LEADERSHIP_POLICY_BENCHMARK_COVERAGE_INVALID")
    if any(not names for names in expected.values()):
        fail("LEADERSHIP_POLICY_SECTOR_COVERAGE_INVALID")
    return expected


def _validate_leadership_binding(packet: dict, policy: dict) -> dict:
    as_of = _parse_date(packet["as_of_date"], "OBSERVATION_DATE_INVALID")
    expected = _active_policy_records(policy, as_of)
    leadership = packet["axes"]["LEADERSHIP"]["measurement"]
    observations = leadership.get("observations")
    if not isinstance(observations, list):
        fail("SECTOR_RELATIVE_STRENGTH_INVALID", "observations")
    actual = {"KOSPI": set(), "KOSDAQ": set()}
    for row in observations:
        if not isinstance(row, dict) or set(row) != {
            "market", "sector_name", "sector_return_pct",
            "relative_return_vs_benchmark_pct",
        }:
            fail("SECTOR_RELATIVE_STRENGTH_INVALID", "row")
        market = row["market"]
        if market not in actual or row["sector_name"] in actual[market]:
            fail("SECTOR_RELATIVE_STRENGTH_INVALID", "identity")
        actual[market].add(row["sector_name"])
    if actual != expected:
        fail("SECTOR_RELATIVE_STRENGTH_POLICY_MISMATCH")
    coverage = leadership.get("coverage")
    expected_coverage = {
        market: {
            "observed_sector_count": len(expected[market]),
            "ratified_identity_count": len(expected[market]) + 1,
        }
        for market in ("KOSDAQ", "KOSPI")
    }
    if coverage != expected_coverage:
        fail("SECTOR_RELATIVE_STRENGTH_COVERAGE_INVALID")
    if leadership.get("investment_ranking_authorized") is not False:
        fail("SECTOR_RANKING_AUTHORITY_INVALID")
    return {
        "status": "PASS",
        "KOSPI": {
            "observed_sector_count": len(expected["KOSPI"]),
            "ratified_identity_count": len(expected["KOSPI"]) + 1,
        },
        "KOSDAQ": {
            "observed_sector_count": len(expected["KOSDAQ"]),
            "ratified_identity_count": len(expected["KOSDAQ"]) + 1,
        },
    }


def _validate_market_measurements(packet: dict) -> None:
    breadth = packet["axes"]["BREADTH"]["measurement"]
    liquidity = packet["axes"]["LIQUIDITY"]["measurement"]
    for market in ("KOSPI", "KOSDAQ"):
        breadth_row = breadth.get("markets", {}).get(market)
        liquidity_row = liquidity.get("markets", {}).get(market)
        if not isinstance(breadth_row, dict) or set(breadth_row) != {
            "advance_fraction", "advancing_count", "decline_fraction",
            "declining_count", "paired_count", "unchanged_count",
        }:
            fail("BREADTH_MARKET_COVERAGE_INVALID", market)
        if not isinstance(liquidity_row, dict) or "current_turnover_pct" not in liquidity_row:
            fail("TURNOVER_MARKET_COVERAGE_INVALID", market)


def _validate_source_lineage(packet: dict, contract: dict) -> None:
    source = packet.get("source")
    if not isinstance(source, dict) or source.get("name") != "KRX_OPEN_API_STOCK_AND_INDEX_DAILY":
        fail("OBSERVATION_SOURCE_INVALID")
    if source.get("tier") != "Official" or source.get("raw_persistence") != 0:
        fail("OBSERVATION_SOURCE_INVALID")
    expected_endpoints = _read_json(
        _safe(ROOT, contract["bound_contracts"]["korea_market_signals"]["path"]),
        "MARKET_SIGNALS_CONTRACT_INVALID",
    )
    available = _parse_utc(packet["available_at"], "SOURCE_AVAILABLE_AT_INVALID")
    requests = source.get("requests")
    if not isinstance(requests, dict):
        fail("OBSERVATION_SOURCE_INVALID")
    for family, endpoint_key in (("stock", "stock_endpoints"), ("index", "index_endpoints")):
        rows = requests.get(family)
        if not isinstance(rows, dict):
            fail("OBSERVATION_SOURCE_INVALID", family)
        for market in ("KOSPI", "KOSDAQ"):
            row = rows.get(market)
            if not isinstance(row, dict):
                fail("OBSERVATION_SOURCE_INVALID", f"{family}:{market}")
            if row.get("endpoint") != expected_endpoints[endpoint_key][market.lower()]:
                fail("OBSERVATION_ENDPOINT_MISMATCH", f"{family}:{market}")
            for prefix in ("current", "previous"):
                timestamp = _parse_utc(
                    row.get(f"{prefix}_fetched_at_utc"),
                    "SOURCE_FETCHED_AT_INVALID",
                )
                digest = row.get(f"{prefix}_response_sha256")
                if timestamp > available or not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
                    fail("OBSERVATION_SOURCE_LINEAGE_INVALID", f"{family}:{market}:{prefix}")


def _policy_blocking_reasons(authority_contract: dict) -> list[str]:
    reasons: list[str] = []
    if authority_contract.get("repository_policy_registry_status") != "RATIFIED":
        reasons.append("REGIME_POLICY_REGISTRY_ABSENT")
    components = authority_contract.get("policy_component_status")
    reason_codes = authority_contract.get("policy_reason_codes")
    if not isinstance(components, dict) or not isinstance(reason_codes, dict):
        fail("REGIME_AUTHORITY_CONTRACT_INVALID")
    for component in authority_contract.get("required_policy_components", []):
        if components.get(component) != "RATIFIED":
            code = reason_codes.get(component)
            if not isinstance(code, str) or not code:
                fail("REGIME_AUTHORITY_CONTRACT_INVALID", component)
            reasons.append(code)
    authority = authority_contract.get("authority")
    if not isinstance(authority, dict) or authority.get("classification_authorized") is not True:
        reasons.append("REGIME_SCORING_AUTHORITY_UNRATIFIED")
    return reasons


def _gate(name: str, status: str, reasons: list[str] | None = None) -> dict:
    if status not in {"PASS", "FAIL"}:
        fail("GATE_STATUS_INVALID", name)
    return {"name": name, "status": status, "reasons": list(reasons or [])}


def build_input_envelope(
    *,
    decision_at: str,
    observation_path: Path,
    observation_sha256: str,
    retained_observation_path: Path,
    leadership_policy_path: Path,
    leadership_policy_sha256: str,
    evidence_class: str = "NATURAL_READ_ONLY",
    contract: dict | None = None,
) -> dict:
    contract = copy.deepcopy(contract or load_contract())
    if evidence_class not in {
        contract["natural_evidence_class"], contract["test_evidence_class"]
    }:
        fail("EVIDENCE_CLASS_INVALID")
    decision_time = _parse_utc(decision_at, "DECISION_AT_INVALID")
    observation_hash = _require_exact_hash(
        observation_path, observation_sha256, "KOREA_MARKET_SIGNALS"
    )
    retained_hash = _file_sha256(retained_observation_path, "RETAINED_OBSERVATION_MISSING")
    if retained_hash != observation_hash or _read_bytes(
        observation_path, "SOURCE_FILE_MISSING"
    ) != _read_bytes(retained_observation_path, "RETAINED_OBSERVATION_MISSING"):
        fail("LATEST_POINTER_APPEND_ONLY_MISMATCH")
    policy_hash = _require_exact_hash(
        leadership_policy_path, leadership_policy_sha256, "KOREA_LEADERSHIP_POLICY"
    )
    packet = KOREA_SIGNALS.validate_packet(
        _read_json(observation_path, "OBSERVATION_INVALID")
    )
    policy = KOREA_LEADERSHIP.load_policy(leadership_policy_path)
    KOREA_LEADERSHIP.require_ratified(policy)
    if (
        policy.get("policy_version")
        != contract["source_requirements"]["leadership_policy_version"]
        or policy.get("approval_status")
        != contract["source_requirements"]["leadership_policy_approval"]
        or contract["source_requirements"]["leadership_run_mode"]
        not in policy.get("allowed_run_modes", [])
    ):
        fail("LEADERSHIP_POLICY_NOT_LITERAL_PASS")
    if packet.get("schema_version") != contract["source_requirements"]["observation_schema_version"]:
        fail("OBSERVATION_SCHEMA_VERSION_MISMATCH")
    if packet.get("contract_version") != contract["source_requirements"]["observation_contract_version"]:
        fail("OBSERVATION_CONTRACT_VERSION_MISMATCH")
    if packet.get("status") != contract["source_requirements"]["observation_status"]:
        fail("OBSERVATION_STATUS_INVALID")
    _validate_source_lineage(packet, contract)
    _validate_market_measurements(packet)
    leadership_coverage = _validate_leadership_binding(packet, policy)

    as_of = _parse_date(packet["as_of_date"], "OBSERVATION_DATE_INVALID")
    available = _parse_utc(packet["available_at"], "SOURCE_AVAILABLE_AT_INVALID")
    if available > decision_time:
        fail("SOURCE_FROM_FUTURE")
    earliest = dt.datetime.combine(
        as_of, dt.time.fromisoformat(policy["earliest_usable_time"]), tzinfo=KST
    ).astimezone(dt.timezone.utc)
    completed_reasons: list[str] = []
    if as_of > decision_time.astimezone(KST).date() or available < earliest:
        completed_reasons.append("COMPLETED_BAR_NOT_PROVEN")
    completed_status = "PASS" if not completed_reasons else "FAIL"

    coverage = packet.get("coverage")
    axis_status = "PASS"
    if (
        coverage.get("required_axes") != contract["required_axes"]
        or coverage.get("observed_axes") != contract["required_axes"]
        or coverage.get("observed_count") != 5
        or coverage.get("required_count") != 5
        or coverage.get("ratio") != "5/5"
    ):
        axis_status = "FAIL"

    authority_path = _safe(
        ROOT, contract["bound_contracts"]["regime_decision_authority"]["path"]
    )
    authority_contract = _read_json(authority_path, "REGIME_AUTHORITY_CONTRACT_INVALID")
    policy_reasons = _policy_blocking_reasons(authority_contract)
    scoring_status = "PASS" if not policy_reasons else "FAIL"
    ttl_seconds = None
    source_age_seconds = int((decision_time - available).total_seconds())
    gates = [
        _gate("PAPER_SAFETY", "PASS"),
        _gate("EXACT_SOURCE_HASH", "PASS"),
        _gate("COMPLETED_BAR", completed_status, completed_reasons),
        _gate("LEADERSHIP_POLICY", "PASS"),
        _gate("AXIS_COVERAGE", axis_status, [] if axis_status == "PASS" else ["REGIME_AXIS_COVERAGE_NOT_5_OF_5"]),
        _gate("BREADTH_KOSPI_KOSDAQ", "PASS"),
        _gate("TURNOVER_KOSPI_KOSDAQ", "PASS"),
        _gate("SECTOR_RELATIVE_STRENGTH", "PASS"),
        _gate("REGIME_SCORING_AUTHORITY", scoring_status, policy_reasons),
        _gate("TTL_POLICY", "FAIL", ["TTL_POLICY_UNRATIFIED"]),
        _gate("FRESHNESS", "FAIL", ["FRESHNESS_CANNOT_PASS_WITHOUT_RATIFIED_TTL"]),
        _gate("SCORING_RESULT", "FAIL", ["REGIME_SCORING_RESULT_ABSENT"]),
    ]
    envelope = {
        "schema_version": contract["input_envelope_version"],
        "contract_version": contract["contract_version"],
        "market": contract["market"],
        "run_mode": contract["run_mode"],
        "evidence_class": evidence_class,
        "decision_at": decision_at,
        "source_time": packet["available_at"],
        "as_of_date": packet["as_of_date"],
        "previous_date": packet["previous_date"],
        "completed_bar": {
            "status": completed_status,
            "granularity": contract["source_requirements"]["completed_bar_granularity"],
            "as_of_date": packet["as_of_date"],
            "earliest_usable_at": earliest.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reasons": completed_reasons,
        },
        "freshness": {
            "status": "FAIL",
            "source_age_seconds": source_age_seconds,
            "ttl_seconds": ttl_seconds,
            "ttl_policy": contract["source_requirements"]["ttl_source"],
            "reasons": [
                "TTL_POLICY_UNRATIFIED",
                "FRESHNESS_CANNOT_PASS_WITHOUT_RATIFIED_TTL",
            ],
        },
        "source_lineage": {
            "KOREA_MARKET_SIGNALS": {
                "ref": "repo://data/latest_korea_market_signals.json",
                "append_only_ref": (
                    "repo://data/observations/korea_market_signals/"
                    f"{packet['as_of_date']}/packet.json"
                ),
                "sha256": observation_hash,
                "payload_sha256": packet["payload_sha256"],
                "pointer_append_only_byte_identical": True,
                "contract_version": packet["contract_version"],
            },
            "KOREA_LEADERSHIP_POLICY": {
                "ref": "repo://config/korea_leadership_policy.json",
                "sha256": policy_hash,
                "policy_version": policy["policy_version"],
                "approval_status": policy["approval_status"],
            },
            "REGIME_MINIMUM_COVERAGE_POLICY": copy.deepcopy(
                contract["bound_contracts"]["minimum_coverage"]
            ),
            "REGIME_DECISION_AUTHORITY": copy.deepcopy(
                contract["bound_contracts"]["regime_decision_authority"]
            ),
        },
        "policy_status": {
            "leadership_policy": "PASS",
            "minimum_axis_coverage_policy": "PASS",
            "regime_scoring_authority": scoring_status,
            "repository_policy_registry_status": authority_contract[
                "repository_policy_registry_status"
            ],
            "component_status": copy.deepcopy(authority_contract["policy_component_status"]),
        },
        "coverage": copy.deepcopy(coverage),
        "market_evidence": {
            "trend": copy.deepcopy(packet["axes"]["TREND"]),
            "breadth": copy.deepcopy(packet["axes"]["BREADTH"]),
            "risk_vol": copy.deepcopy(packet["axes"]["RISK_VOL"]),
            "turnover": copy.deepcopy(packet["axes"]["LIQUIDITY"]),
            "sector_relative_strength": copy.deepcopy(packet["axes"]["LEADERSHIP"]),
            "leadership_policy_coverage": leadership_coverage,
        },
        "gates": gates,
        "authority": copy.deepcopy(contract["authority"]),
    }
    envelope["envelope_sha256"] = payload_sha256(envelope)
    return envelope


def _blocking_reasons(envelope: dict) -> list[str]:
    result: list[str] = []
    for gate in envelope["gates"]:
        if gate["status"] != "PASS":
            for reason in gate["reasons"]:
                if reason not in result:
                    result.append(reason)
    return result


def build_receipt(envelope: dict, contract: dict | None = None) -> dict:
    contract = copy.deepcopy(contract or load_contract())
    unsigned_envelope = copy.deepcopy(envelope)
    digest = unsigned_envelope.pop("envelope_sha256", None)
    if not isinstance(digest, str) or payload_sha256(unsigned_envelope) != digest:
        fail("ENVELOPE_SHA_MISMATCH")
    gate_names = [row.get("name") for row in envelope.get("gates", [])]
    if gate_names != contract["all_pass_gate_order"]:
        fail("ENVELOPE_GATE_ORDER_INVALID")
    all_pass = all(row.get("status") == "PASS" for row in envelope["gates"])
    # This v1 adapter never invents a scored label. A later ratified scoring
    # receipt must revise the contract and make SCORING_RESULT literal PASS.
    if all_pass:
        fail("SCORING_RESULT_REQUIRED_FOR_DEFINED_REGIME")
    reasons = _blocking_reasons(envelope)
    receipt = {
        "schema_version": contract["receipt_version"],
        "contract_version": contract["contract_version"],
        "market": contract["market"],
        "run_mode": contract["run_mode"],
        "evidence_class": envelope["evidence_class"],
        "decision_at": envelope["decision_at"],
        "source_time": envelope["source_time"],
        "as_of_date": envelope["as_of_date"],
        "status": "HOLD_POLICY_OR_SOURCE_NOT_ALL_PASS",
        "market_judgement_status": "UNKNOWN",
        "regime": contract["fail_closed_regime"],
        "recommendation": contract["fail_closed_recommendation"],
        "confidence": None,
        "action": None,
        "all_required_gates_literal_pass": False,
        "blocking_reasons": reasons,
        "input_envelope_sha256": envelope["envelope_sha256"],
        "input_envelope": copy.deepcopy(envelope),
        "authority": copy.deepcopy(contract["authority"]),
    }
    receipt["receipt_sha256"] = payload_sha256(receipt)
    return receipt


def validate_receipt(receipt: dict, contract: dict | None = None) -> dict:
    contract = copy.deepcopy(contract or load_contract())
    if not isinstance(receipt, dict):
        fail("RECEIPT_INVALID")
    digest = receipt.get("receipt_sha256")
    unsigned = copy.deepcopy(receipt)
    unsigned.pop("receipt_sha256", None)
    if not isinstance(digest, str) or payload_sha256(unsigned) != digest:
        fail("RECEIPT_SHA_MISMATCH")
    expected = build_receipt(receipt.get("input_envelope", {}), contract)
    if expected != receipt:
        fail("RECEIPT_DERIVATION_MISMATCH")
    if (
        receipt["regime"] != "UNKNOWN"
        or receipt["recommendation"] != "HOLD"
        or receipt["confidence"] is not None
        or receipt["action"] is not None
        or receipt["all_required_gates_literal_pass"] is not False
    ):
        fail("RECEIPT_FAIL_CLOSED_INVARIANT_INVALID")
    return copy.deepcopy(receipt)


def _write_immutable(path: Path, value: dict) -> str:
    path = Path(path).resolve()
    root = ROOT.resolve()
    try:
        path.relative_to(root)
    except ValueError:
        pass
    else:
        fail("OUTPUT_INSIDE_REPOSITORY_FORBIDDEN")
    serialized = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != serialized:
            fail("IMMUTABLE_OUTPUT_CONFLICT", str(path))
        return "NO_CHANGE"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return "CREATED"


def run(
    *, decision_at: str, observation_path: Path, observation_sha256: str,
    retained_observation_path: Path, leadership_policy_path: Path,
    leadership_policy_sha256: str, envelope_output: Path, receipt_output: Path,
    evidence_class: str = "NATURAL_READ_ONLY",
) -> tuple[str, str, dict]:
    contract = load_contract()
    envelope = build_input_envelope(
        decision_at=decision_at,
        observation_path=observation_path,
        observation_sha256=observation_sha256,
        retained_observation_path=retained_observation_path,
        leadership_policy_path=leadership_policy_path,
        leadership_policy_sha256=leadership_policy_sha256,
        evidence_class=evidence_class,
        contract=contract,
    )
    receipt = validate_receipt(build_receipt(envelope, contract), contract)
    envelope_status = _write_immutable(envelope_output, envelope)
    receipt_status = _write_immutable(receipt_output, receipt)
    return envelope_status, receipt_status, receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-at", required=True)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--observation-sha256", required=True)
    parser.add_argument("--retained-observation", type=Path, required=True)
    parser.add_argument("--leadership-policy", type=Path, required=True)
    parser.add_argument("--leadership-policy-sha256", required=True)
    parser.add_argument("--envelope-output", type=Path, required=True)
    parser.add_argument("--receipt-output", type=Path, required=True)
    parser.add_argument(
        "--evidence-class",
        choices=("NATURAL_READ_ONLY", "TEST_ONLY_NON_PROMOTABLE"),
        default="NATURAL_READ_ONLY",
    )
    args = parser.parse_args()
    try:
        envelope_status, receipt_status, receipt = run(
            decision_at=args.decision_at,
            observation_path=args.observation,
            observation_sha256=args.observation_sha256,
            retained_observation_path=args.retained_observation,
            leadership_policy_path=args.leadership_policy,
            leadership_policy_sha256=args.leadership_policy_sha256,
            envelope_output=args.envelope_output,
            receipt_output=args.receipt_output,
            evidence_class=args.evidence_class,
        )
    except (KrxMarketJudgementError, KOREA_SIGNALS.KoreaMarketSignalsError,
            KOREA_LEADERSHIP.KoreaLeadershipError) as exc:
        print(f"FAIL_KRX_MARKET_JUDGEMENT:{exc}")
        return 1
    print(
        "PASS_KRX_MARKET_JUDGEMENT:"
        f"{receipt['market_judgement_status']}:{receipt['recommendation']}:"
        f"{receipt['receipt_sha256']}:{envelope_status}:{receipt_status}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
