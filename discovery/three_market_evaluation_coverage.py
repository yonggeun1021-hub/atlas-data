#!/usr/bin/env python3
"""Read-only KR/US/CRYPTO population-to-evaluation coverage receipt.

This module does not scan, rank, or create candidates.  It validates the
existing source-coverage universes and the current market outputs, then
reports only counts those sources actually establish.  Bounded review
symbols are never projected as full-population evaluation counts.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import csv
import datetime as dt
from decimal import Decimal
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
NOT_COUNTED = "미집계"
SCHEMA_VERSION = "three_market_evaluation_coverage/1"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ThreeMarketEvaluationCoverageError(ValueError):
    """A source or derived coverage claim failed closed."""


def _load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"SOURCE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GLOBAL_ASSET_MASTER = _load_module(
    "coverage_global_asset_master", "universe/global_asset_master.py"
)
KOREA_REVIEW = _load_module(
    "coverage_korea_symbol_market_review", "decision/korea_symbol_market_review.py"
)
US_REVIEW = _load_module(
    "coverage_us_symbol_market_review", "decision/us_symbol_market_review.py"
)
US_INVESTABLE_REGISTRY = _load_module(
    "coverage_us_investable_registry", "universe/us_investable_registry.py"
)
CRYPTO_DECISION = _load_module(
    "coverage_crypto_paper_decision", "decision/crypto_paper_decision_snapshot.py"
)
UPBIT_IDENTITY_REVIEW = _load_module(
    "coverage_upbit_identity_review",
    ".github/scripts/upbit_identity_review_bundle.py",
)
UPBIT_BOUNDED_IDENTITY = _load_module(
    "coverage_upbit_bounded_identity",
    "identity/upbit_bounded_identity_registry.py",
)


def canonical_json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ThreeMarketEvaluationCoverageError(code) from exc
    if not isinstance(value, dict):
        raise ThreeMarketEvaluationCoverageError(code)
    return value


def _validate_self_hash(value: dict, field: str, code: str) -> None:
    claimed = value.get(field)
    unsigned = copy.deepcopy(value)
    unsigned.pop(field, None)
    if not isinstance(claimed, str) or payload_sha256(unsigned) != claimed:
        raise ThreeMarketEvaluationCoverageError(code)


def _require_not_future(value: str, observed_at: dt.datetime, code: str) -> None:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise ThreeMarketEvaluationCoverageError(code)
    parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc
    )
    if parsed > observed_at:
        raise ThreeMarketEvaluationCoverageError(code)


def _require_date_not_future(value: str, observed_at: dt.datetime, code: str) -> None:
    try:
        parsed = dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ThreeMarketEvaluationCoverageError(code) from exc
    if parsed.isoformat() != value or parsed > observed_at.date():
        raise ThreeMarketEvaluationCoverageError(code)


def _source_ref(path: Path, packet_sha256: str) -> dict:
    try:
        relative = Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ThreeMarketEvaluationCoverageError("SOURCE_PATH_OUTSIDE_REPOSITORY") from exc
    return {
        "path": relative,
        "file_sha256": _file_sha256(path),
        "packet_sha256": packet_sha256,
    }


def _validated_global_universe(
    path: Path, market: str, observed_at: dt.datetime,
) -> tuple[dict, int]:
    wrapper = _read_json(path, f"{market}_UNIVERSE_READ_FAILED")
    if market == "KR":
        packet = wrapper
        expected_schema = "krx_global_universe_packet/1"
        expected_status = "SOURCE_COVERAGE_UNIVERSE_VALIDATED"
    elif market == "US":
        if wrapper.get("schema_version") != "us_forward_universe_population/2":
            raise ThreeMarketEvaluationCoverageError("US_UNIVERSE_WRAPPER_INVALID")
        _validate_self_hash(wrapper, "payload_sha256", "US_UNIVERSE_WRAPPER_HASH_MISMATCH")
        _require_not_future(
            wrapper.get("generated_at"), observed_at, "US_UNIVERSE_FROM_FUTURE"
        )
        packet = wrapper.get("packet")
        expected_schema = "us_global_universe_packet/1"
        expected_status = "FORWARD_SOURCE_COVERAGE_UNIVERSE_VALIDATED"
    else:
        raise ThreeMarketEvaluationCoverageError("GLOBAL_UNIVERSE_MARKET_INVALID")
    if not isinstance(packet, dict):
        raise ThreeMarketEvaluationCoverageError(f"{market}_UNIVERSE_PACKET_INVALID")
    _validate_self_hash(packet, "payload_sha256", f"{market}_UNIVERSE_HASH_MISMATCH")
    try:
        master = GLOBAL_ASSET_MASTER.validate_packet(packet.get("asset_master"))
    except GLOBAL_ASSET_MASTER.GlobalAssetMasterError as exc:
        raise ThreeMarketEvaluationCoverageError(
            f"{market}_ASSET_MASTER_INVALID:{exc}"
        ) from exc
    _require_date_not_future(
        packet.get("as_of_date"), observed_at, f"{market}_UNIVERSE_FROM_FUTURE"
    )
    source_snapshots = packet.get("source_snapshots")
    if not isinstance(source_snapshots, list) or not source_snapshots:
        raise ThreeMarketEvaluationCoverageError(
            f"{market}_UNIVERSE_SOURCE_SNAPSHOTS_INVALID"
        )
    source_count = 0
    count_field = "universe_count" if market == "KR" else "record_count"
    for source in source_snapshots:
        if not isinstance(source, dict) or type(source.get(count_field)) is not int:
            raise ThreeMarketEvaluationCoverageError(
                f"{market}_UNIVERSE_SOURCE_SNAPSHOTS_INVALID"
            )
        _require_not_future(
            source.get("retrieved_at_utc"), observed_at,
            f"{market}_UNIVERSE_SOURCE_FROM_FUTURE",
        )
        source_count += source[count_field]
    count = packet.get("total_count")
    if (
        packet.get("schema_version") != expected_schema
        or packet.get("status") != expected_status
        or packet.get("as_of_date") != master.get("as_of_date")
        or type(count) is not int
        or count < 0
        or count != master.get("record_count")
        or count != len(master.get("records", []))
        or count != source_count
    ):
        raise ThreeMarketEvaluationCoverageError(f"{market}_UNIVERSE_COUNT_INVALID")
    return packet, count


def _validated_crypto_universe(path: Path, generated_at: dt.datetime) -> tuple[dict, int]:
    record = _read_json(path, "CRYPTO_UNIVERSE_READ_FAILED")
    entry = {
        "date": record.get("snapshot_date"),
        "path": Path(path),
        "record": record,
        "packet": record.get("packet"),
    }
    try:
        CRYPTO_DECISION._validate_universe_entry(entry, not_after=generated_at)
    except CRYPTO_DECISION.CryptoPaperDecisionSnapshotError as exc:
        raise ThreeMarketEvaluationCoverageError(
            f"CRYPTO_UNIVERSE_INVALID:{exc}"
        ) from exc
    packet = record["packet"]
    summary = packet.get("summary")
    markets = packet.get("markets")
    if (
        not isinstance(summary, dict)
        or not isinstance(markets, list)
        or type(summary.get("market_count")) is not int
        or summary["market_count"] != len(markets)
        or sum(
            summary.get(key, -1)
            for key in (
                "observation_pool_count", "tradeable_universe_count",
                "paper_eligible_count", "blocked_count",
            )
        ) != len(markets)
    ):
        raise ThreeMarketEvaluationCoverageError("CRYPTO_UNIVERSE_COUNT_INVALID")
    market_codes = []
    state_counts = Counter()
    for row in markets:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("market"), str)
            or row.get("state") not in {
                "OBSERVATION_POOL", "TRADEABLE_UNIVERSE",
                "PAPER_ELIGIBLE", "BLOCKED",
            }
            or not isinstance(row.get("reason"), str)
            or not row["reason"]
        ):
            raise ThreeMarketEvaluationCoverageError(
                "CRYPTO_UNIVERSE_MARKET_STATE_INVALID"
            )
        try:
            CRYPTO_DECISION._require_all_false(row.get("authority"))
        except CRYPTO_DECISION.CryptoPaperDecisionSnapshotError as exc:
            raise ThreeMarketEvaluationCoverageError(
                f"CRYPTO_UNIVERSE_MARKET_AUTHORITY_INVALID:{exc}"
            ) from exc
        market_codes.append(row["market"])
        state_counts[row["state"]] += 1
    if len(market_codes) != len(set(market_codes)):
        raise ThreeMarketEvaluationCoverageError("CRYPTO_UNIVERSE_MARKET_DUPLICATE")
    expected_state_counts = {
        "OBSERVATION_POOL": summary["observation_pool_count"],
        "TRADEABLE_UNIVERSE": summary["tradeable_universe_count"],
        "PAPER_ELIGIBLE": summary["paper_eligible_count"],
        "BLOCKED": summary["blocked_count"],
    }
    if {
        state: state_counts.get(state, 0) for state in expected_state_counts
    } != expected_state_counts:
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_UNIVERSE_STATE_COUNTS_INVALID"
        )
    return record, summary["market_count"]


def _validated_review(path: Path, market: str, observed_at: dt.datetime) -> dict:
    value = _read_json(path, f"{market}_REVIEW_READ_FAILED")
    try:
        if market == "KR":
            checked = KOREA_REVIEW.validate_output(value)
        elif market == "US":
            checked = US_REVIEW.validate_output(value)
        else:
            raise ThreeMarketEvaluationCoverageError("REVIEW_MARKET_INVALID")
    except (KOREA_REVIEW.KoreaSymbolMarketReviewError,
            US_REVIEW.UsSymbolMarketReviewError) as exc:
        raise ThreeMarketEvaluationCoverageError(
            f"{market}_BOUNDED_REVIEW_INVALID:{exc}"
        ) from exc
    _require_not_future(
        checked.get("generated_at"), observed_at, f"{market}_REVIEW_FROM_FUTURE"
    )
    return checked


def _validated_crypto_decision(path: Path, observed_at: dt.datetime) -> dict:
    try:
        checked = CRYPTO_DECISION.validate_output(
            _read_json(path, "CRYPTO_DECISION_READ_FAILED")
        )
    except CRYPTO_DECISION.CryptoPaperDecisionSnapshotError as exc:
        raise ThreeMarketEvaluationCoverageError(
            f"CRYPTO_DECISION_INVALID:{exc}"
        ) from exc
    _require_not_future(
        checked.get("generated_at"), observed_at, "CRYPTO_DECISION_FROM_FUTURE"
    )
    return checked


def _validated_crypto_identity_review(path: Path, snapshot_dir: Path) -> dict:
    packet = _read_json(path, "CRYPTO_IDENTITY_REVIEW_READ_FAILED")
    snapshot_date = packet.get("snapshot_date")
    if (
        packet.get("schema_version") != UPBIT_IDENTITY_REVIEW.SCHEMA_VERSION
        or not isinstance(snapshot_date, str)
        or Path(snapshot_dir).name != snapshot_date
    ):
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_IDENTITY_REVIEW_IDENTITY_INVALID"
        )
    try:
        rebuilt = UPBIT_IDENTITY_REVIEW.build_bundle(
            snapshot_date, raw_root=Path(snapshot_dir).parent
        )
    except (
        UPBIT_IDENTITY_REVIEW.IdentityReviewBundleError,
        UPBIT_IDENTITY_REVIEW.UNI.UpbitUniverseError,
    ) as exc:
        raise ThreeMarketEvaluationCoverageError(
            f"CRYPTO_IDENTITY_REVIEW_SOURCE_INVALID:{exc}"
        ) from exc
    if packet != rebuilt:
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_IDENTITY_REVIEW_DERIVATION_MISMATCH"
        )
    return packet


def _validated_free_market_data(path: Path, observed_at: dt.datetime) -> dict:
    packet = _read_json(path, "US_FREE_MARKET_DATA_READ_FAILED")
    _validate_self_hash(packet, "packet_sha256", "US_FREE_MARKET_DATA_HASH_MISMATCH")
    _require_not_future(
        packet.get("observed_at_utc"), observed_at,
        "US_FREE_MARKET_DATA_FROM_FUTURE",
    )
    authority = packet.get("authority")
    if (
        packet.get("schema_version") != "free_market_data_capture/5"
        or packet.get("contract_version") != "free_market_data/3"
        or not isinstance(authority, dict)
        or authority.get("evidence_capture_only") is not True
        or any(
            value is not False
            for key, value in authority.items()
            if key != "evidence_capture_only"
        )
    ):
        raise ThreeMarketEvaluationCoverageError(
            "US_FREE_MARKET_DATA_BOUNDARY_INVALID"
        )
    alpaca = packet.get("alpaca")
    daily = alpaca.get("daily_bars") if isinstance(alpaca, dict) else None
    latest = alpaca.get("bars") if isinstance(alpaca, dict) else None
    if (
        alpaca.get("status") != "READY"
        or alpaca.get("source_scope") != "IEX_ONLY_PARTIAL_US_MARKET"
        or not isinstance(daily, list)
        or not isinstance(latest, list)
        or any(
            not isinstance(row, dict)
            or set(row) != {
                "symbol", "opened_at", "open", "high", "low", "close",
                "volume",
            }
            for row in daily
        )
        or any(
            not isinstance(row, dict)
            or set(row) != {
                "symbol", "provider_timestamp", "close", "volume",
            }
            for row in latest
        )
    ):
        raise ThreeMarketEvaluationCoverageError(
            "US_FREE_MARKET_DATA_ALPACA_INVALID"
        )
    return packet


def _read_symbol_directory(path: Path, expected_headers: list[str]) -> tuple[bytes, list[dict]]:
    try:
        raw = gzip.open(path, "rb").read()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ThreeMarketEvaluationCoverageError(
            "US_SYMBOL_DIRECTORY_READ_FAILED"
        ) from exc
    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    if reader.fieldnames != expected_headers:
        raise ThreeMarketEvaluationCoverageError(
            "US_SYMBOL_DIRECTORY_HEADERS_INVALID"
        )
    symbol_field = expected_headers[0]
    rows = [
        row for row in reader
        if row.get(symbol_field) and not row[symbol_field].startswith(
            "File Creation Time"
        )
    ]
    return raw, rows


def _crypto_held_reason_analysis(candidates: list[dict]) -> dict:
    """Classify the owning evaluator's exact UNKNOWN reasons, never scores."""
    policy_reasons = {
        "REGIME_AGGREGATE_UNAUTHORIZED_PENDING_P1_COM_05",
        "NO_RATIFIED_CANDIDATE_TREND_RULE",
        "VOLUME_LIQUIDITY_THRESHOLDS_UNRATIFIED",
        "NO_RATIFIED_OVEREXTENSION_THRESHOLD",
        "BTC_SELF_REFERENCE_RULE_UNRATIFIED",
        "PEER_RELATIVE_STRENGTH_UNRATIFIED",
    }
    data_reasons = {
        "SECURITY_AND_NETWORK_OUTAGE_COVERAGE_MISSING",
        "INSUFFICIENT_FINALIZED_CANDLES",
        "EVIDENCE_FAMILY_INCOMPLETE",
        "LEADERSHIP_ASSET_WINDOW_INCOMPLETE",
        "LEADERSHIP_ASSET_NOT_COVERED",
    }
    connection_reasons = {
        "MARKET_EVIDENCE_PACKET_MISSING",
        "LEADERSHIP_OUTPUT_MISSING",
    }
    categories = (
        "UNRATIFIED_POLICY_OR_AUTHORITY",
        "SOURCE_DATA_OR_COVERAGE_INSUFFICIENT",
        "CONSUMER_INPUT_NOT_CONNECTED",
        "OTHER_UNKNOWN_REASON",
    )
    occurrence_counts = Counter()
    reason_counts = Counter()
    markets_by_category = {category: set() for category in categories}
    held_rows = [row for row in candidates if row.get("state") in {"WATCH", "WAIT"}]
    for row in held_rows:
        criteria = (row.get("p5_08") or {}).get("criteria")
        if not isinstance(criteria, dict):
            raise ThreeMarketEvaluationCoverageError(
                "CRYPTO_HELD_CRITERIA_INVALID"
            )
        for criterion, result in criteria.items():
            if not isinstance(result, dict) or result.get("status") not in {
                "PASS", "FAIL", "UNKNOWN",
            }:
                raise ThreeMarketEvaluationCoverageError(
                    "CRYPTO_HELD_CRITERIA_INVALID"
                )
            if result["status"] != "UNKNOWN":
                continue
            reason = result.get("reason")
            if not isinstance(reason, str) or not reason:
                raise ThreeMarketEvaluationCoverageError(
                    "CRYPTO_HELD_REASON_INVALID"
                )
            if reason in policy_reasons:
                category = "UNRATIFIED_POLICY_OR_AUTHORITY"
            elif reason in data_reasons or reason.startswith(
                "LEADERSHIP_WINDOW_UNKNOWN:"
            ):
                category = "SOURCE_DATA_OR_COVERAGE_INSUFFICIENT"
            elif reason in connection_reasons:
                category = "CONSUMER_INPUT_NOT_CONNECTED"
            else:
                category = "OTHER_UNKNOWN_REASON"
            occurrence_counts[category] += 1
            reason_counts[f"{criterion}:{reason}"] += 1
            markets_by_category[category].add(row["market"])
    return {
        "count_semantics": "UNKNOWN_CRITERION_OCCURRENCES_NOT_DISTINCT_MARKETS",
        "held_market_count": len(held_rows),
        "criterion_occurrence_counts": {
            category: occurrence_counts.get(category, 0)
            for category in categories
        },
        "affected_market_counts": {
            category: len(markets_by_category[category])
            for category in categories
        },
        "reason_counts": dict(sorted(reason_counts.items())),
    }


def _crypto_evaluation_scope_readiness(
    *, universe_record: dict, identity_review: dict, snapshot_dir: Path,
    prior_identity_evidence_path: Path,
) -> dict:
    universe_module = CRYPTO_DECISION.UNIVERSE
    try:
        core = universe_module.load_snapshot_core(Path(snapshot_dir))
        policy = universe_module.load_policy()
    except universe_module.UpbitUniverseError as exc:
        raise ThreeMarketEvaluationCoverageError(
            f"CRYPTO_EVALUATION_SOURCE_INVALID:{exc}"
        ) from exc
    universe_rows = universe_record["packet"]["markets"]
    proposals = identity_review.get("proposals")
    proposal_markets = [
        (row.get("claim") or {}).get("upbitMarket")
        for row in proposals
    ] if isinstance(proposals, list) else []
    universe_markets = [row["market"] for row in universe_rows]
    if (
        len(proposal_markets) != identity_review["summary"]["proposal_count"]
        or sorted(proposal_markets) != sorted(universe_markets)
        or len(proposal_markets) != len(set(proposal_markets))
    ):
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_EVALUATION_IDENTITY_SCOPE_MISMATCH"
        )

    evaluation_markets = sorted(
        row["market"] for row in universe_rows
        if row["reason"] == "IDENTITY_UNRATIFIED"
    )
    safety_exclusions = sorted(
        row["market"] for row in universe_rows
        if row["reason"] == "INVESTMENT_WARNING_ACTIVE"
    )
    current_paper_markets = sorted(
        row["market"] for row in universe_rows
        if row["state"] == "PAPER_ELIGIBLE"
    )
    unclassified_exclusions = [
        row for row in universe_rows
        if row["state"] in {"OBSERVATION_POOL", "BLOCKED"}
        and row["reason"] not in {
            "IDENTITY_UNRATIFIED", "INVESTMENT_WARNING_ACTIVE",
        }
    ]
    if (
        len(evaluation_markets) != 267
        or len(safety_exclusions) != 7
        or len(current_paper_markets) != 8
        or unclassified_exclusions
    ):
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_EVALUATION_SCOPE_DISPOSITION_INVALID"
        )

    try:
        prior_assets = UPBIT_BOUNDED_IDENTITY.load_identity_evidence(
            Path(prior_identity_evidence_path)
        )
    except UPBIT_BOUNDED_IDENTITY.BoundedIdentityRegistryError as exc:
        raise ThreeMarketEvaluationCoverageError(
            f"CRYPTO_PRIOR_IDENTITY_EVIDENCE_INVALID:{exc}"
        ) from exc
    evaluation_symbols = {market.removeprefix("KRW-") for market in evaluation_markets}
    prior_overlap = sorted(evaluation_symbols & set(prior_assets))
    prior_verdict_counts = Counter(
        UPBIT_BOUNDED_IDENTITY.compute_verdict(
            symbol, prior_assets.get(symbol), evaluation_as_of=core["snapshot_date"]
        )[0]
        for symbol in sorted(evaluation_symbols)
    )

    min_listing_days = int(policy["min_listing_history_finalized_days"])
    turnover_days = int(policy["turnover_lookback_finalized_days"])
    min_turnover = Decimal(str(policy["min_30d_avg_krw_turnover"]))
    max_spread = Decimal(str(policy["max_spread_bps"]))
    max_slippage = Decimal(str(policy["max_estimated_paper_slippage_bps"]))
    notional = Decimal(str(policy["paper_slippage_estimate_notional_krw"]))
    availability = Counter()
    data_gates = Counter()
    all_data_gate_markets = []
    for market in evaluation_markets:
        entry = core["markets"].get(market)
        if not isinstance(entry, dict):
            raise ThreeMarketEvaluationCoverageError(
                "CRYPTO_EVALUATION_MARKET_SOURCE_MISSING"
            )
        available = {
            "market_metadata": entry.get("market_all_available") is True,
            "warning_state": entry.get("market_event_warning") is not None,
            "orderbook": entry.get("orderbook_available") is True,
            "daily_candles": entry.get("candles_available") is True,
        }
        availability.update(key for key, passed in available.items() if passed)
        listing = (
            available["daily_candles"]
            and entry.get("observed_daily_candle_count", 0) >= min_listing_days
        )
        turnover_history = (
            available["daily_candles"]
            and entry.get("trailing_turnover_finalized_day_count", 0)
            >= turnover_days
        )
        turnover = (
            turnover_history
            and entry["trailing_30d_krw_turnover"]
            / Decimal(entry["trailing_turnover_finalized_day_count"])
            >= min_turnover
        )
        spread = None
        slippage = None
        if available["orderbook"]:
            spread = universe_module._spread_bps(
                Decimal(str(entry["best_bid"])),
                Decimal(str(entry["best_ask"])),
            )
            slippage = universe_module._estimate_slippage_bps(
                entry["ask_levels"], Decimal(str(entry["best_ask"])), notional
            )
        gates = {
            "listing_history": listing,
            "complete_turnover_history": turnover_history,
            "turnover": turnover,
            "spread": spread is not None and spread <= max_spread,
            "slippage": slippage is not None and slippage <= max_slippage,
        }
        data_gates.update(key for key, passed in gates.items() if passed)
        if all(available.values()) and all(gates.values()) and entry["market_event_warning"] is False:
            all_data_gate_markets.append(market)

    manifest = _read_json(
        Path(snapshot_dir) / "_manifest.json",
        "CRYPTO_RAW_MANIFEST_READ_FAILED",
    )
    manifest_markets = manifest.get("markets")
    if not isinstance(manifest_markets, list) or manifest.get("market_count") != len(manifest_markets):
        raise ThreeMarketEvaluationCoverageError("CRYPTO_RAW_MANIFEST_INVALID")
    regex_excluded = sorted(set(manifest_markets) - set(core["markets"]))
    batch_size = universe_module.UPBIT_CAPTURE.MAX_MARKETS_PER_BATCH_CALL
    batch_calls = (len(manifest_markets) + batch_size - 1) // batch_size
    successful_calls = 1 + batch_calls + batch_calls + len(manifest_markets)
    minimum_pacing_seconds = Decimal(max(len(manifest_markets) - 1, 0)) * Decimal("1.05")
    return {
        "status": "PROPOSAL_ONLY_NOT_ADOPTED",
        "selection": {
            "source_reason": "IDENTITY_UNRATIFIED",
            "market_count": len(evaluation_markets),
            "market_set_sha256": payload_sha256(evaluation_markets),
            "source_identity_review_payload_sha256": identity_review["payload_sha256"],
            "interpretation": "OBSERVATION_AND_EVALUATION_ONLY_NOT_ELIGIBILITY",
        },
        "current_paper_identity_scope": "UPBIT_KRW_SPOT_CRYPTO_PAPER_EIGHT_ONLY",
        "current_paper_market_count": len(current_paper_markets),
        "safety_exclusions": {
            "reason": "INVESTMENT_WARNING_ACTIVE",
            "market_count": len(safety_exclusions),
            "markets": safety_exclusions,
        },
        "identity_verification": {
            "proposal_count": identity_review["summary"]["proposal_count"],
            "proposal_status": identity_review["review_status"],
            "collision_finding_count": identity_review["summary"]["finding_count"],
            "cross_reference_check_status": identity_review["review_boundary"]["cross_reference_check_status"],
            "prior_bounded_research_row_count": len(prior_assets),
            "prior_bounded_research_overlap_count": len(prior_overlap),
            "prior_verdict_counts": dict(sorted(prior_verdict_counts.items())),
            "external_cross_reference_still_required_count": prior_verdict_counts.get(
                UPBIT_BOUNDED_IDENTITY.VERDICT_HOLD_MISSING_SECOND_SOURCE, 0
            ),
            "zero_findings_meaning": identity_review["review_boundary"]["meaning_of_zero_findings"],
        },
        "retained_source_availability_counts": {
            key: availability.get(key, 0)
            for key in (
                "market_metadata", "warning_state", "orderbook", "daily_candles",
            )
        },
        "existing_policy_market_data_gate_pass_counts": {
            key: data_gates.get(key, 0)
            for key in (
                "listing_history", "complete_turnover_history", "turnover",
                "spread", "slippage",
            )
        },
        "all_existing_market_data_gates_pass_count": len(all_data_gate_markets),
        "all_existing_market_data_gates_pass_semantics": (
            "DATA_ONLY_PRECHECK_NOT_IDENTITY_TAXONOMY_CANDIDATE_OR_PAPER_PASS"
        ),
        "raw_scope_gap": {
            "raw_krw_market_count": len(manifest_markets),
            "classifier_market_count": len(core["markets"]),
            "regex_excluded_krw_market_count": len(regex_excluded),
            "regex_excluded_krw_markets": regex_excluded,
            "decision_required": "SEPARATE_IDENTITY_CONTRACT_DECISION",
        },
        "source_call_cost": {
            "retained_snapshot_additional_call_count": 0,
            "fresh_full_capture_successful_http_call_count": successful_calls,
            "fresh_full_capture_minimum_candle_pacing_seconds": str(
                minimum_pacing_seconds
            ),
            "authentication_required": manifest.get("auth_required"),
            "order_or_withdrawal_endpoints_called": manifest.get(
                "order_or_withdrawal_endpoints_called"
            ),
        },
        "minimum_change_design": {
            "separate_evaluation_identity_authority_required": True,
            "must_not_modify": [
                "config/upbit_asset_identity_registry.json",
                "config/upbit_exclusion_taxonomy.json",
            ],
            "must_not_feed": [
                "TRADEABLE_UNIVERSE",
                "PAPER_ELIGIBLE",
                "CANDIDATE_PROMOTION",
                "ORDER_DRAFT",
            ],
            "market_data_adapter_required": False,
            "reason": "RETAINED_SOURCE_FIELDS_AVAILABLE_FOR_ALL_267",
        },
        "cio_decision_targets": [
            "ADOPT_OR_REJECT_EXACT_267_EVALUATION_ONLY_SCOPE",
            "REUSE_OR_REREVIEW_45_EXISTING_VERIFIED_CANDIDATE_IDENTITIES",
            "KEEP_24_TICKER_COLLISIONS_ON_IDENTITY_HOLD",
            "SECOND_SOURCE_PLAN_FOR_198_UNRESOLVED_IDENTITIES",
            "KEEP_OR_REVIEW_6_ONE_CHARACTER_MARKETS_OUTSIDE_CLASSIFIER",
            "SET_EVALUATION_IDENTITY_REFRESH_AND_EXPIRY_WITHOUT_PAPER_FLOW",
        ],
        "authority": {
            "identity_ratification_authorized": False,
            "taxonomy_ratification_authorized": False,
            "candidate_authorized": False,
            "paper_scope_change_authorized": False,
            "stage_promotion_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _us_investable_input_readiness(
    *, us_packet: dict, raw_snapshot_dir: Path, market_data: dict,
    contract: dict,
) -> dict:
    nasdaq_headers = [
        "Symbol", "Security Name", "Market Category", "Test Issue",
        "Financial Status", "Round Lot Size", "ETF", "NextShares",
    ]
    other_headers = [
        "ACT Symbol", "Security Name", "Exchange", "CQS Symbol", "ETF",
        "Round Lot Size", "Test Issue", "NASDAQ Symbol",
    ]
    nasdaq_raw, nasdaq_rows = _read_symbol_directory(
        Path(raw_snapshot_dir) / "nasdaqlisted.txt.gz", nasdaq_headers
    )
    other_raw, other_rows = _read_symbol_directory(
        Path(raw_snapshot_dir) / "otherlisted.txt.gz", other_headers
    )
    sources = {row["source_name"]: row for row in us_packet["source_snapshots"]}
    expected = {
        "nasdaq_listed": (nasdaq_raw, nasdaq_rows),
        "other_listed": (other_raw, other_rows),
    }
    for name, (raw, rows) in expected.items():
        source = sources.get(name)
        if (
            not isinstance(source, dict)
            or source.get("record_count") != len(rows)
            or source.get("source_sha256") != hashlib.sha256(raw).hexdigest()
        ):
            raise ThreeMarketEvaluationCoverageError(
                "US_SYMBOL_DIRECTORY_SOURCE_MISMATCH"
            )
    all_rows = nasdaq_rows + other_rows
    alpaca = market_data["alpaca"]
    daily_symbols = sorted({row["symbol"] for row in alpaca["daily_bars"]})
    latest_symbols = sorted({row["symbol"] for row in alpaca["bars"]})
    if daily_symbols != latest_symbols:
        raise ThreeMarketEvaluationCoverageError(
            "US_ALPACA_SYMBOL_SCOPE_MISMATCH"
        )
    rows_by_symbol = {}
    for row in nasdaq_rows:
        rows_by_symbol.setdefault(row["Symbol"], []).append(row)
    for row in other_rows:
        rows_by_symbol.setdefault(row["ACT Symbol"], []).append(row)
    if any(symbol not in rows_by_symbol for symbol in daily_symbols):
        raise ThreeMarketEvaluationCoverageError(
            "US_ALPACA_SYMBOL_NOT_IN_SOURCE_UNIVERSE"
        )
    bounded_etfs = sorted(
        symbol for symbol in daily_symbols
        if any(row.get("ETF") == "Y" for row in rows_by_symbol[symbol])
    )
    population_count = len(all_rows)
    return {
        "status": "NATURAL_INVESTABLE_SNAPSHOT_NOT_CONNECTED",
        "required_input_schema": "us_investable_snapshot/1",
        "current_fully_closable_natural_symbol_count": 0,
        "field_source_matrix": [
            {
                "fact": "asset_id_symbol_listing_venue",
                "source": "us_global_universe_packet/1",
                "available_count": population_count,
                "status": "AVAILABLE_SOURCE_COVERAGE_ONLY",
            },
            {
                "fact": "etf_indicator",
                "source": "NASDAQ_SYMBOL_DIRECTORY",
                "available_count": population_count,
                "etf_confirmed_count": sum(row.get("ETF") == "Y" for row in all_rows),
                "status": "AVAILABLE_ETF_ONLY_COMMON_STOCK_NOT_PROVEN",
            },
            {
                "fact": "test_issue",
                "source": "NASDAQ_SYMBOL_DIRECTORY",
                "available_count": population_count,
                "status": "AVAILABLE_RAW_NOT_EMITTED_AS_SNAPSHOT_FACT",
            },
            {
                "fact": "financial_status",
                "source": "NASDAQ_LISTED_ONLY",
                "available_count": len(nasdaq_rows),
                "status": "PARTIAL_RAW_NOT_EMITTED_AS_SNAPSHOT_FACT",
            },
            {
                "fact": "listing",
                "source": "CURRENT_SYMBOL_DIRECTORY_MEMBERSHIP",
                "available_count": population_count,
                "status": "CURRENT_OBSERVATION_NOT_EXACT_LISTING_FACT",
            },
            {
                "fact": "trading_halt",
                "source": None,
                "available_count": 0,
                "status": "MISSING",
            },
            {
                "fact": "scheduled_delisting",
                "source": None,
                "available_count": 0,
                "status": "MISSING",
            },
            {
                "fact": "corporate_action_state",
                "source": None,
                "available_count": 0,
                "status": "MISSING",
            },
            {
                "fact": "liquidity",
                "source": "ALPACA_IEX_ONLY_PARTIAL_US_MARKET",
                "available_count": len(daily_symbols),
                "daily_bar_row_count": len(alpaca["daily_bars"]),
                "status": "PARTIAL_OHLCV_ONLY_TRADE_COUNT_AND_SPREAD_MISSING",
            },
        ],
        "liquidity_policy_status": (
            "ABSENT_EXTERNAL_RATIFIED_POLICY_REQUIRED"
            if contract["liquidity"]["repository_default_policy"] == "ABSENT"
            else "UNEXPECTED_OPEN_POLICY"
        ),
        "first_bounded_source_aligned_target": {
            "status": "NOT_CLOSABLE_SOURCE_ALIGNMENT_ONLY",
            "market_data_symbol_count": len(daily_symbols),
            "directory_etf_type_proven_symbol_count": len(bounded_etfs),
            "directory_etf_type_proven_symbols": bounded_etfs,
            "remaining_blockers": [
                "EXACT_LISTING_FACT_NOT_CONNECTED",
                "TRADING_HALT_FACT_MISSING",
                "SCHEDULED_DELISTING_FACT_MISSING",
                "CORPORATE_ACTION_STATE_FACT_MISSING",
                "LIQUIDITY_TRADE_COUNT_AND_SPREAD_MISSING",
                "RATIFIED_LIQUIDITY_POLICY_ABSENT",
            ],
        },
        "adapter_decision": "DO_NOT_CREATE_ADAPTER_UNTIL_FACT_SOURCES_AND_POLICY_EXIST",
        "next_source_requirements": [
            "OFFICIAL_SECURITY_MASTER_FOR_NON_ETF_INSTRUMENT_TYPE",
            "EXACT_ACTIVE_LISTING_FACT",
            "TRADING_HALT_FACT",
            "SCHEDULED_DELISTING_FACT",
            "CORPORATE_ACTION_STATE_FACT",
            "MEDIAN_DAILY_TRADE_COUNT",
            "MEDIAN_SPREAD_BPS",
            "EXTERNAL_RATIFIED_LIQUIDITY_POLICY",
        ],
        "authority": copy.deepcopy(contract["authority"]),
    }


def _base_market_row(
    market: str, universe_count: int, universe_ref: dict, review_count: int,
    review_ref: dict,
) -> dict:
    return {
        "market": market,
        "universe_count": universe_count,
        "source_valid_count": universe_count,
        "bounded_current_output_count": review_count,
        "evaluation_input_count": NOT_COUNTED,
        "evaluated_count": NOT_COUNTED,
        "candidate_count": NOT_COUNTED,
        "candidate_count_semantics": "NOT_COUNTED_NO_POPULATION_CANDIDATE_OUTPUT",
        "held_count": NOT_COUNTED,
        "excluded_count": NOT_COUNTED,
        "paper_ready_count": NOT_COUNTED,
        "coverage_status": "BOUNDED_OUTPUT_NOT_FULL_POPULATION_EVALUATION",
        "missing_reasons": [
            "FULL_POPULATION_EVALUATION_INPUT_NOT_CONNECTED",
            "FULL_POPULATION_CANDIDATE_AND_EXCLUSION_COUNTS_NOT_EMITTED",
        ],
        "state_lifetime": {
            "snapshot_only": True,
            "automatic_carry_forward": False,
            "reevaluation_required": True,
        },
        "sources": {"universe": universe_ref, "current_output": review_ref},
    }


def build_report(
    *, generated_at: str, kr_universe_path: Path, kr_review_path: Path,
    us_universe_path: Path, us_review_path: Path, crypto_universe_path: Path,
    crypto_decision_path: Path, crypto_identity_review_path: Path,
    crypto_snapshot_dir: Path, prior_identity_evidence_path: Path,
    us_raw_snapshot_dir: Path, us_market_data_path: Path,
) -> dict:
    if not isinstance(generated_at, str) or UTC_RE.fullmatch(generated_at) is None:
        raise ThreeMarketEvaluationCoverageError("GENERATED_AT_INVALID")
    try:
        observed_at = dt.datetime.strptime(
            generated_at, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise ThreeMarketEvaluationCoverageError("GENERATED_AT_INVALID") from exc
    kr_universe, kr_count = _validated_global_universe(
        Path(kr_universe_path), "KR", observed_at
    )
    us_universe, us_count = _validated_global_universe(
        Path(us_universe_path), "US", observed_at
    )
    crypto_universe, crypto_count = _validated_crypto_universe(
        Path(crypto_universe_path), observed_at
    )

    kr_review = _validated_review(Path(kr_review_path), "KR", observed_at)
    us_review = _validated_review(Path(us_review_path), "US", observed_at)
    crypto_decision = _validated_crypto_decision(
        Path(crypto_decision_path), observed_at
    )
    crypto_identity_review = _validated_crypto_identity_review(
        Path(crypto_identity_review_path), Path(crypto_snapshot_dir)
    )
    us_market_data = _validated_free_market_data(
        Path(us_market_data_path), observed_at
    )
    crypto_universe_ref = next(
        (
            ref for ref in crypto_decision["source_refs"]
            if ref["role"] == "upbit_tradeable_universe_packet"
        ),
        None,
    )
    if crypto_universe_ref is None:
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_DECISION_UNIVERSE_REF_MISSING"
        )
    decision_universe = _read_json(
        ROOT / crypto_universe_ref["path"],
        "CRYPTO_DECISION_UNIVERSE_SOURCE_READ_FAILED",
    )
    if decision_universe != crypto_universe:
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_DECISION_UNIVERSE_SOURCE_MISMATCH"
        )

    kr_row = _base_market_row(
        "KR", kr_count,
        _source_ref(kr_universe_path, kr_universe["payload_sha256"]),
        kr_review["summary"]["symbol_count"],
        _source_ref(kr_review_path, kr_review["packet_sha256"]),
    )
    us_row = _base_market_row(
        "US", us_count,
        _source_ref(us_universe_path, us_universe["payload_sha256"]),
        us_review["summary"]["symbol_count"],
        _source_ref(us_review_path, us_review["packet_sha256"]),
    )
    us_symbols = us_review.get("symbols")
    if (
        not isinstance(us_symbols, list)
        or len(us_symbols) != us_review["summary"]["symbol_count"]
    ):
        raise ThreeMarketEvaluationCoverageError("US_BOUNDED_REVIEW_SYMBOLS_INVALID")
    us_entry_state_counts = Counter()
    us_entry_reason_counts = Counter()
    for row in us_symbols:
        entry_review = row.get("entry_review") if isinstance(row, dict) else None
        if (
            not isinstance(entry_review, dict)
            or not isinstance(entry_review.get("state"), str)
            or not isinstance(entry_review.get("reasons"), list)
            or any(
                not isinstance(reason, str) or not reason
                for reason in entry_review["reasons"]
            )
        ):
            raise ThreeMarketEvaluationCoverageError(
                "US_BOUNDED_REVIEW_DISPOSITION_INVALID"
            )
        us_entry_state_counts[entry_review["state"]] += 1
        us_entry_reason_counts.update(entry_review["reasons"])
    us_registry_contract = US_INVESTABLE_REGISTRY.load_contract()
    if (
        us_registry_contract.get("source_coverage_is_investability") is not False
        or (us_registry_contract.get("liquidity") or {}).get(
            "repository_default_policy"
        )
        != "ABSENT"
    ):
        raise ThreeMarketEvaluationCoverageError(
            "US_INVESTABLE_REGISTRY_BOUNDARY_INVALID"
        )
    us_row.update({
        "bounded_output_scope": (
            "SUPPORTED_PIPELINE_SUBJECTS_ONLY_NOT_POPULATION_EVALUATION"
        ),
        "bounded_output_state_counts": dict(
            sorted(us_entry_state_counts.items())
        ),
        "bounded_output_reason_counts": dict(
            sorted(us_entry_reason_counts.items())
        ),
        "population_evaluation_connection": {
            "status": "NOT_CONNECTED",
            "required_input_schema": "us_investable_snapshot/1",
            "existing_evaluator_output_schema": "us_investable_registry_result/1",
            "connected_source_universe_is_investability": False,
            "required_fail_closed_facts": copy.deepcopy(
                us_registry_contract["required_fail_closed_facts"]
            ),
            "liquidity_policy_status": "ABSENT_EXTERNAL_RATIFIED_POLICY_REQUIRED",
            "reason": (
                "NATURAL_POPULATION_INPUT_AND_LIQUIDITY_POLICY_NOT_CONNECTED"
            ),
        },
        "investable_input_readiness": _us_investable_input_readiness(
            us_packet=us_universe,
            raw_snapshot_dir=Path(us_raw_snapshot_dir),
            market_data=us_market_data,
            contract=us_registry_contract,
        ),
    })

    funnel = crypto_decision.get("funnel_counts")
    candidates = crypto_decision.get("candidates")
    if not isinstance(funnel, dict) or not isinstance(candidates, list):
        raise ThreeMarketEvaluationCoverageError("CRYPTO_DECISION_COUNTS_INVALID")
    evaluation_input_count = funnel.get("tradeable_universe_count")
    candidate_count = funnel.get("focused_review_count")
    paper_ready_count = funnel.get("paper_ready_count")
    observation_pool_count = funnel.get("observation_pool_count")
    universe_markets = crypto_universe["packet"]["markets"]
    admitted_rows = [
        row for row in universe_markets
        if row["state"] in {"TRADEABLE_UNIVERSE", "PAPER_ELIGIBLE"}
    ]
    excluded_rows = [
        row for row in universe_markets
        if row["state"] in {"OBSERVATION_POOL", "BLOCKED"}
    ]
    candidate_markets = [row.get("market") for row in candidates]
    admitted_markets = [row["market"] for row in admitted_rows]
    if (
        any(type(value) is not int or value < 0 for value in (
            evaluation_input_count, candidate_count, paper_ready_count,
            observation_pool_count,
        ))
        or evaluation_input_count != len(candidates)
        or evaluation_input_count != len(admitted_rows)
        or observation_pool_count != sum(
            row["state"] == "OBSERVATION_POOL" for row in universe_markets
        )
        or len(excluded_rows) + evaluation_input_count != crypto_count
        or len(candidate_markets) != len(set(candidate_markets))
        or sorted(candidate_markets) != sorted(admitted_markets)
    ):
        raise ThreeMarketEvaluationCoverageError("CRYPTO_DECISION_COUNTS_INVALID")
    held_count = sum(row.get("state") in {"WATCH", "WAIT"} for row in candidates)
    if candidate_count + paper_ready_count + held_count != evaluation_input_count:
        raise ThreeMarketEvaluationCoverageError("CRYPTO_DECISION_STATE_COUNTS_INVALID")
    excluded_reason_counts = dict(sorted(Counter(
        row["reason"] for row in excluded_rows
    ).items()))
    excluded_state_counts = {
        state: sum(row["state"] == state for row in excluded_rows)
        for state in ("OBSERVATION_POOL", "BLOCKED")
    }
    identity_registry = CRYPTO_DECISION.UNIVERSE.load_identity_registry()
    identity_scope = identity_registry.get("scope")
    if identity_scope != "UPBIT_KRW_SPOT_CRYPTO_PAPER_EIGHT_ONLY":
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_IDENTITY_APPROVAL_SCOPE_INVALID"
        )
    effective_identity_mapping = (
        CRYPTO_DECISION.UNIVERSE.effective_identity_mapping(
            identity_registry, crypto_universe["packet"]["evaluation_as_of"]
        )
    )
    if set(admitted_markets) != set(effective_identity_mapping):
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_ADMITTED_IDENTITY_SCOPE_MISMATCH"
        )
    pre_evaluation_reason_classification = {
        "IDENTITY_UNRATIFIED": {
            "market_count": excluded_reason_counts.get("IDENTITY_UNRATIFIED", 0),
            "classification": "OUTSIDE_RATIFIED_IDENTITY_SCOPE",
            "approval_scope": identity_scope,
            "interpretation": (
                "NOT_AN_INVESTMENT_CONDITION_FAILURE"
            ),
        },
        "INVESTMENT_WARNING_ACTIVE": {
            "market_count": excluded_reason_counts.get(
                "INVESTMENT_WARNING_ACTIVE", 0
            ),
            "classification": "SAFETY_EXCLUSION",
            "interpretation": (
                "UPBIT_CAUTION_HARD_EXCLUSION_NOT_A_CANDIDATE_SCORE"
            ),
        },
    }
    if sum(
        row["market_count"] for row in pre_evaluation_reason_classification.values()
    ) != len(excluded_rows):
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_PRE_EVALUATION_REASON_UNCLASSIFIED"
        )
    held_reason_analysis = _crypto_held_reason_analysis(candidates)
    if held_reason_analysis["held_market_count"] != held_count:
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_HELD_REASON_COUNT_INVALID"
        )
    crypto_row = {
        "market": "CRYPTO",
        "universe_count": crypto_count,
        "source_valid_count": crypto_count,
        "bounded_current_output_count": evaluation_input_count,
        "evaluation_input_count": evaluation_input_count,
        "evaluated_count": len(candidates),
        "candidate_count": candidate_count,
        "candidate_count_semantics": "SOURCE_FOCUSED_REVIEW_COUNT",
        "held_count": held_count,
        "excluded_count": len(excluded_rows),
        "excluded_count_semantics": (
            "SOURCE_MEMBERS_NOT_ADMITTED_TO_CURRENT_EVALUATION_INPUT"
        ),
        "excluded_state_counts": excluded_state_counts,
        "excluded_reason_counts": excluded_reason_counts,
        "pre_evaluation_reason_classification": (
            pre_evaluation_reason_classification
        ),
        "held_reason_analysis": held_reason_analysis,
        "evaluation_only_scope_readiness": _crypto_evaluation_scope_readiness(
            universe_record=crypto_universe,
            identity_review=crypto_identity_review,
            snapshot_dir=Path(crypto_snapshot_dir),
            prior_identity_evidence_path=Path(prior_identity_evidence_path),
        ),
        "paper_ready_count": paper_ready_count,
        "coverage_status": "SOURCE_POPULATION_EVALUATION_DISPOSITION_ACCOUNTED",
        "missing_reasons": [],
        "observation_pool_count": observation_pool_count,
        "state_lifetime": {
            "snapshot_only": True,
            "automatic_carry_forward": False,
            "reevaluation_required": True,
        },
        "sources": {
            "universe": _source_ref(
                crypto_universe_path, crypto_universe["payload_sha256"]
            ),
            "current_output": _source_ref(
                crypto_decision_path, crypto_decision["payload_sha256"]
            ),
            "identity_review": _source_ref(
                crypto_identity_review_path,
                crypto_identity_review["payload_sha256"],
            ),
        },
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "markets": [kr_row, us_row, crypto_row],
        "summary": {
            "market_count": 3,
            "full_population_universe_count_available": 3,
            "full_population_evaluation_count_available": 0,
            "bounded_subset_evaluation_count_available": 1,
            "full_population_evaluation_disposition_available": 1,
            "crypto_evaluation_only_scope_proposal_available": 1,
            "us_fully_closable_natural_investable_input_count": 0,
        },
        "authority": {
            "candidate_creation_authorized": False,
            "candidate_ranking_authorized": False,
            "evaluation_scope_adoption_authorized": False,
            "paper_identity_scope_change_authorized": False,
            "stage_promotion_authorized": False,
            "action_authorized": False,
            "trading_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
        },
    }
    report["payload_sha256"] = payload_sha256(report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--kr-universe", type=Path, required=True)
    parser.add_argument("--kr-review", type=Path, required=True)
    parser.add_argument("--us-universe", type=Path, required=True)
    parser.add_argument("--us-review", type=Path, required=True)
    parser.add_argument("--us-raw-snapshot", type=Path, required=True)
    parser.add_argument("--us-market-data", type=Path, required=True)
    parser.add_argument("--crypto-universe", type=Path, required=True)
    parser.add_argument("--crypto-decision", type=Path, required=True)
    parser.add_argument("--crypto-identity-review", type=Path, required=True)
    parser.add_argument("--crypto-snapshot", type=Path, required=True)
    parser.add_argument("--prior-identity-evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_report(
        generated_at=args.generated_at,
        kr_universe_path=args.kr_universe,
        kr_review_path=args.kr_review,
        us_universe_path=args.us_universe,
        us_review_path=args.us_review,
        us_raw_snapshot_dir=args.us_raw_snapshot,
        us_market_data_path=args.us_market_data,
        crypto_universe_path=args.crypto_universe,
        crypto_decision_path=args.crypto_decision,
        crypto_identity_review_path=args.crypto_identity_review,
        crypto_snapshot_dir=args.crypto_snapshot,
        prior_identity_evidence_path=args.prior_identity_evidence,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
