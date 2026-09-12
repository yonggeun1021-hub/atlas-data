"""Validate retained KRX Information System bytes for KR PAPER runtime display.

The bridge is read-only.  It admits one natural KRX observation only after the
retained provider bodies reproduce the reported five axes, joins it to the
already accepted historical common-v1 replay, and runs the unchanged common-v1
confirmation rule.  It never grants strategy, capital, order, trading, or REAL
authority.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

from regime import decision_authority as COMMON
from regime import paper_regime_reference as REFERENCE
from regime import krx_information_system_capture as CAPTURE


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CONTRACT_PATH = ROOT / "config/krx_information_system_source_candidate_v1.json"
LEADERSHIP_POLICY_PATH = ROOT / "config/korea_leadership_policy.json"
REFERENCE_POLICY_PATH = ROOT / "config/paper_regime_reference_policy_v1.json"
IMPLEMENTATION_PATHS = (
    "regime/kr_information_system_runtime_bridge.py",
    "regime/kr_paper_runtime.py",
    "regime/krx_information_system_capture.py",
    "regime/paper_regime_reference.py",
    "regime/decision_authority.py",
    "rotation/kr_internal_paper_theme_application.py",
    "market_data/krx_official_holiday_calendar.py",
)
MARKETS = ("KOSPI", "KOSDAQ")
Q6 = Decimal("0.000001")
Q2 = Decimal("0.01")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class InformationSystemRuntimeError(ValueError):
    pass


def require(condition: bool, code: str) -> None:
    if not condition:
        raise InformationSystemRuntimeError(code)


def sha256(raw: bytes) -> str:
    require(isinstance(raw, bytes), "BYTES_REQUIRED")
    return hashlib.sha256(raw).hexdigest()


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def pretty_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def object_from(raw: bytes, code: str) -> dict:
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "DUPLICATE_JSON_KEY")
            value[key] = item
        return value

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: require(False, "NONFINITE_JSON"),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InformationSystemRuntimeError(code) from exc
    require(isinstance(value, dict), code)
    return value


def instant(value: object, code: str) -> dt.datetime:
    require(isinstance(value, str), code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InformationSystemRuntimeError(code) from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, code)
    return parsed


def number(value: object) -> Decimal:
    require(isinstance(value, str), "PROVIDER_NUMBER_INVALID")
    try:
        return Decimal(value.replace(",", "").strip())
    except Exception as exc:
        raise InformationSystemRuntimeError("PROVIDER_NUMBER_INVALID") from exc


def fmt(value: Decimal) -> str:
    return format(value.quantize(Q6, rounding=ROUND_HALF_EVEN), "f")


def pct(before: Decimal, after: Decimal) -> str:
    require(before != 0, "PROVIDER_ZERO_DENOMINATOR")
    return fmt((after / before - Decimal(1)) * 100)


def rendered(value: str) -> str:
    return re.sub(r"[^-\w\.]", "", value)


def canonical_names(policy: dict, market: str, as_of: str) -> dict[str, str]:
    day = dt.date.fromisoformat(as_of)
    result = {}
    for row in policy["records"]:
        if not row["series_identity"].startswith(market + "::"):
            continue
        start = dt.date.fromisoformat(row["effective_from"])
        end = dt.date.fromisoformat(row["effective_to"]) if row.get("effective_to") else None
        if start <= day and (end is None or day <= end):
            name = row["series_identity"].split("::", 1)[1]
            key = rendered(name)
            require(bool(key) and key not in result, "LEADERSHIP_IDENTITY_COLLISION")
            result[key] = name
    return result


def _manifest_payload_sha256(manifest: dict) -> str:
    unsigned = dict(manifest)
    unsigned.pop("payload_sha256", None)
    return sha256(canonical_bytes(unsigned) + b"\n")


def _validate_manifest(manifest: dict, raw_responses: Mapping[str, bytes]) -> tuple[dict, dict]:
    require(manifest.get("schema") == "krx_information_system_source_capture/2", "SOURCE_MANIFEST_SCHEMA_INVALID")
    require(manifest.get("status") == "COMPLETE", "SOURCE_MANIFEST_INCOMPLETE")
    require(manifest.get("original_response_bytes_retained") is True, "ORIGINAL_BYTES_NOT_RETAINED")
    require(manifest.get("credentials_retained") is False, "CREDENTIAL_RETENTION_INVALID")
    require(manifest.get("request_headers_retained") is False, "HEADER_RETENTION_INVALID")
    require(manifest.get("cookies_retained") is False, "COOKIE_RETENTION_INVALID")
    require(manifest.get("provider_published_at_is_received_at") is False, "PROVIDER_TIME_SEMANTICS_INVALID")
    require(manifest.get("payload_sha256") == _manifest_payload_sha256(manifest), "SOURCE_MANIFEST_PAYLOAD_HASH_MISMATCH")
    dates = manifest.get("dates")
    require(isinstance(dates, list) and len(dates) == 2 and dates[0] < dates[1], "SOURCE_MANIFEST_DATES_INVALID")
    expected = {f"{day}:{market}:{family}" for day in dates for market in MARKETS for family in ("stock", "index")}
    records = manifest.get("records")
    require(isinstance(records, list) and len(records) == 8, "SOURCE_MANIFEST_RECORD_COUNT_INVALID")
    by_key, receipts = {}, {}
    manifest_paths = {
        record.get("response", {}).get("path")
        for record in records if isinstance(record, dict)
    }
    require(set(raw_responses) == manifest_paths, "SOURCE_RESPONSE_SET_INVALID")
    started = instant(manifest.get("capture_started_at_utc"), "CAPTURE_TIME_INVALID")
    completed = instant(manifest.get("capture_completed_at_utc"), "CAPTURE_TIME_INVALID")
    for record in records:
        require(isinstance(record, dict), "SOURCE_MANIFEST_RECORD_INVALID")
        key = record.get("key")
        require(key in expected and key not in by_key, "SOURCE_MANIFEST_RECORD_KEY_INVALID")
        response = record.get("response")
        binding = record.get("normalized_frame")
        require(isinstance(response, dict) and isinstance(binding, dict), "SOURCE_MANIFEST_BINDING_INVALID")
        path = response.get("path")
        raw = raw_responses.get(path) if isinstance(path, str) else None
        require(isinstance(raw, bytes), "SOURCE_RESPONSE_BYTES_MISSING")
        require(response.get("bytes") == len(raw), "SOURCE_RESPONSE_SIZE_MISMATCH")
        digest = sha256(raw)
        require(response.get("sha256") == digest, "SOURCE_RESPONSE_HASH_MISMATCH")
        require(response.get("parser_input_sha256") == digest, "PARSER_INPUT_HASH_MISMATCH")
        family = key.rsplit(":", 1)[1]
        try:
            CAPTURE._validate_provider_payload(raw, family)
        except CAPTURE.CaptureError as exc:
            raise InformationSystemRuntimeError("SOURCE_PROVIDER_PAYLOAD_INVALID") from exc
        request = record.get("request")
        require(isinstance(request, dict) and request.get("method") == "POST"
                and request.get("url") == CAPTURE.ENDPOINT, "SOURCE_REQUEST_IDENTITY_INVALID")
        params = request.get("public_params")
        day, market, family = key.split(":")
        if family == "stock":
            expected_params = {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
                "trdDd": day,
                "mktId": {"KOSPI": "STK", "KOSDAQ": "KSQ"}[market],
            }
        else:
            expected_params = {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT00101",
                "trdDd": day,
                "idxIndMidclssCd": {"KOSPI": "02", "KOSDAQ": "03"}[market],
            }
        require(params == expected_params, "SOURCE_REQUEST_PARAMS_INVALID")
        require(response.get("provider_published_at") is None,
                "PROVIDER_TIME_SEMANTICS_INVALID")
        require(str(response.get("content_type", "")).split(";", 1)[0].strip().lower()
                in {"application/json", "text/html"}, "SOURCE_RESPONSE_CONTENT_TYPE_INVALID")
        require(binding.get("raw_to_frame_equivalent") is True, "RAW_FRAME_EQUIVALENCE_MISSING")
        require(isinstance(binding.get("sha256"), str) and SHA256.fullmatch(binding["sha256"]), "FRAME_HASH_INVALID")
        require(binding.get("schema") == f"pykrx_1_2_8_{family}_required_projection/1",
                "FRAME_SCHEMA_INVALID")
        try:
            projection_sha256 = sha256(CAPTURE.canonical_bytes(CAPTURE._raw_projection(raw, family)))
        except CAPTURE.CaptureError as exc:
            raise InformationSystemRuntimeError("RAW_PROJECTION_INVALID") from exc
        require(binding.get("required_projection_sha256") == projection_sha256,
                "RAW_PROJECTION_HASH_MISMATCH")
        received = instant(response.get("received_at_utc"), "RECEIPT_TIME_INVALID")
        require(started <= received <= completed, "RECEIPT_TIME_ORDER_INVALID")
        by_key[key] = (record, raw)
        receipts[key] = response["received_at_utc"]
    require(set(by_key) == expected, "SOURCE_MANIFEST_CHAIN_INCOMPLETE")
    ordered_receipts = sorted(instant(value, "RECEIPT_TIME_INVALID") for value in receipts.values())
    require(started <= ordered_receipts[0] <= ordered_receipts[-1] <= completed,
            "RECEIPT_TIME_ORDER_INVALID")
    return by_key, receipts


def _sessions(by_key: dict, dates: list[str], leadership_policy: dict, as_of: str) -> dict:
    sessions = {day: {market: {} for market in MARKETS} for day in dates}
    for key, (_, raw) in by_key.items():
        day, market, family = key.split(":")
        payload = object_from(raw, "PROVIDER_JSON_INVALID")
        if family == "stock":
            rows = payload.get("OutBlock_1")
            require(isinstance(rows, list) and rows, "PROVIDER_STOCK_ROWS_INVALID")
            values = {}
            for row in rows:
                require(isinstance(row, dict), "PROVIDER_STOCK_ROW_INVALID")
                identity = str(row.get("ISU_SRT_CD", "")).zfill(6)
                require(identity and identity not in values, "PROVIDER_STOCK_IDENTITY_INVALID")
                close = number(row.get("TDD_CLSPRC"))
                trading_value = number(row.get("ACC_TRDVAL"))
                market_cap = number(row.get("MKTCAP"))
                listed_shares = number(row.get("LIST_SHRS"))
                change = number(row.get("CMPPREVDD_PRC"))
                return_pct = number(row.get("FLUC_RT"))
                require(market_cap == close * listed_shares, "MARKET_CAP_SEMANTICS_INVALID")
                previous_close = close - change
                require(previous_close != 0, "PREVIOUS_CLOSE_INVALID")
                expected_return = (change / previous_close * 100).quantize(Q2, rounding=ROUND_HALF_UP)
                require(return_pct == expected_return, "RETURN_PERCENT_SEMANTICS_INVALID")
                values[identity] = {
                    "close": close, "return_pct": return_pct,
                    "trading_value": trading_value, "market_cap": market_cap,
                }
            sessions[day][market][family] = values
        else:
            rows = payload.get("output")
            require(isinstance(rows, list) and rows, "PROVIDER_INDEX_ROWS_INVALID")
            names = canonical_names(leadership_policy, market, as_of)
            values = {}
            for row in rows:
                require(isinstance(row, dict), "PROVIDER_INDEX_ROW_INVALID")
                if str(row.get("CLSPRC_IDX", "")).strip() == "-":
                    continue
                source_name = rendered(str(row.get("IDX_NM", "")))
                name = names.get(source_name, source_name)
                require(name and name not in values, "PROVIDER_INDEX_IDENTITY_INVALID")
                values[name] = number(row.get("CLSPRC_IDX"))
            sessions[day][market][family] = values
    return sessions


def _rederive(sessions: dict, previous: str, current: str, leadership_policy: dict) -> dict:
    benchmark = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
    trend = {}
    for market in MARKETS:
        name = benchmark[market]
        trend[market] = {"name": name, "one_session_return_pct": pct(
            sessions[previous][market]["index"][name], sessions[current][market]["index"][name]
        )}
    breadth_markets = {}
    totals = {"advancing_count": 0, "declining_count": 0, "unchanged_count": 0, "paired_count": 0}
    for market in MARKETS:
        before, after = sessions[previous][market]["stock"], sessions[current][market]["stock"]
        paired = sorted(set(before) & set(after))
        require(bool(paired), "BREADTH_PAIR_EMPTY")
        advancing = sum(after[k]["close"] > before[k]["close"] for k in paired)
        declining = sum(after[k]["close"] < before[k]["close"] for k in paired)
        unchanged = len(paired) - advancing - declining
        row = {"paired_count": len(paired), "advancing_count": advancing, "declining_count": declining,
               "unchanged_count": unchanged, "advance_fraction": fmt(Decimal(advancing) / len(paired)),
               "decline_fraction": fmt(Decimal(declining) / len(paired))}
        breadth_markets[market] = row
        for key in totals:
            totals[key] += row[key]
    totals["advance_fraction"] = fmt(Decimal(totals["advancing_count"]) / totals["paired_count"])
    totals["decline_fraction"] = fmt(Decimal(totals["declining_count"]) / totals["paired_count"])
    risk_markets, all_moves = {}, []
    for market in MARKETS:
        moves = [abs(row["return_pct"]) for row in sessions[current][market]["stock"].values()]
        all_moves.extend(moves)
        risk_markets[market] = {"stock_count": len(moves), "mean_absolute_stock_move_pct": fmt(sum(moves) / len(moves)),
                                "benchmark_absolute_move_pct": fmt(abs(Decimal(trend[market]["one_session_return_pct"])))}
    liquidity_markets = {}
    prev_value = curr_value = prev_cap = curr_cap = Decimal(0)
    for market in MARKETS:
        left, right = sessions[previous][market]["stock"].values(), sessions[current][market]["stock"].values()
        pv = sum((row["trading_value"] for row in left), Decimal(0)); cv = sum((row["trading_value"] for row in right), Decimal(0))
        pc = sum((row["market_cap"] for row in sessions[previous][market]["stock"].values()), Decimal(0))
        cc = sum((row["market_cap"] for row in sessions[current][market]["stock"].values()), Decimal(0))
        liquidity_markets[market] = {"previous_trading_value_krw": str(pv), "current_trading_value_krw": str(cv),
            "trading_value_change_pct": pct(pv, cv), "previous_turnover_pct": fmt(pv / pc * 100),
            "current_turnover_pct": fmt(cv / cc * 100)}
        prev_value += pv; curr_value += cv; prev_cap += pc; curr_cap += cc
    active = {market: set(canonical_names(leadership_policy, market, current).values()) for market in MARKETS}
    observations, coverage = [], {}
    for market in MARKETS:
        before, after = sessions[previous][market]["index"], sessions[current][market]["index"]
        benchmark_return = Decimal(trend[market]["one_session_return_pct"]); available = 0
        for name in sorted(active[market]):
            if name == benchmark[market] or name not in before or name not in after:
                continue
            available += 1; sector_return = Decimal(pct(before[name], after[name]))
            observations.append({"market": market, "sector_name": name, "sector_return_pct": fmt(sector_return),
                                 "relative_return_vs_benchmark_pct": fmt(sector_return - benchmark_return)})
        coverage[market] = {"ratified_identity_count": len(active[market]), "observed_sector_count": available}
    relative = sorted(observations, key=lambda r: (Decimal(r["relative_return_vs_benchmark_pct"]), r["market"], r["sector_name"]))
    return {
        "TREND": {"benchmarks": trend},
        "BREADTH": {"markets": breadth_markets, "combined": totals},
        "RISK_VOL": {"markets": risk_markets, "combined_mean_absolute_stock_move_pct": fmt(sum(all_moves) / len(all_moves))},
        "LIQUIDITY": {"markets": liquidity_markets, "combined": {"previous_trading_value_krw": str(prev_value),
            "current_trading_value_krw": str(curr_value), "trading_value_change_pct": pct(prev_value, curr_value),
            "previous_turnover_pct": fmt(prev_value / prev_cap * 100), "current_turnover_pct": fmt(curr_value / curr_cap * 100)}},
        "LEADERSHIP": {"coverage": coverage, "largest_relative_returns": list(reversed(relative[-5:])),
            "smallest_relative_returns": relative[:5], "observations": sorted(observations, key=lambda r: (r["market"], r["sector_name"])),
            "investment_ranking_authorized": False},
    }


def validate_natural_evidence(*, reference_raw: bytes, manifest_raw: bytes,
                              raw_responses: Mapping[str, bytes], expected: Mapping[str, str]) -> dict:
    for name in ("reference_sha256", "manifest_sha256", "source_contract_sha256", "leadership_policy_sha256", "reference_policy_sha256"):
        require(isinstance(expected.get(name), str) and SHA256.fullmatch(expected[name]), "SOURCE_TRUST_ANCHOR_MISSING")
    require(sha256(reference_raw) == expected["reference_sha256"], "REFERENCE_BYTES_MISMATCH")
    require(sha256(manifest_raw) == expected["manifest_sha256"], "MANIFEST_BYTES_MISMATCH")
    require(sha256(SOURCE_CONTRACT_PATH.read_bytes()) == expected["source_contract_sha256"], "SOURCE_CONTRACT_BINDING_MISMATCH")
    require(sha256(LEADERSHIP_POLICY_PATH.read_bytes()) == expected["leadership_policy_sha256"], "LEADERSHIP_POLICY_BINDING_MISMATCH")
    require(sha256(REFERENCE_POLICY_PATH.read_bytes()) == expected["reference_policy_sha256"], "REFERENCE_POLICY_BINDING_MISMATCH")
    wrapper = object_from(reference_raw, "REFERENCE_JSON_INVALID")
    require(wrapper.get("schema_version") == "kr_paper_information_system_reference_candidate/1", "REFERENCE_SCHEMA_INVALID")
    require(wrapper.get("status") == "PAPER_REFERENCE_AVAILABLE_RUNTIME_UNKNOWN", "REFERENCE_STATUS_INVALID")
    source = wrapper.get("source_packet")
    require(isinstance(source, dict), "SOURCE_PACKET_MISSING")
    require(source.get("schema_version") == "korea_market_signals_information_system_candidate/1", "SOURCE_PACKET_SCHEMA_INVALID")
    require(source.get("status") == "OBSERVED_UNCLASSIFIED" and source.get("market") == "KOREA", "SOURCE_PACKET_SCOPE_INVALID")
    require(source.get("market_timezone") == "Asia/Seoul", "SOURCE_TIMEZONE_INVALID")
    unsigned = dict(source); claimed = unsigned.pop("payload_sha256", None)
    require(claimed == hashlib.sha256(canonical_bytes(unsigned)).hexdigest(), "SOURCE_PACKET_PAYLOAD_HASH_MISMATCH")
    authority = source.get("authority")
    require(isinstance(authority, dict) and authority.get("paper_reference_display_authorized") is True, "SOURCE_AUTHORITY_INVALID")
    require(all(value is False for key, value in authority.items() if key != "paper_reference_display_authorized"), "SOURCE_AUTHORITY_ESCALATION")
    origin = source.get("source")
    require(isinstance(origin, dict) and origin.get("name") == "KRX_INFORMATION_DATA_SYSTEM_PYKRX", "SOURCE_IDENTITY_INVALID")
    require(origin.get("tier") == "Official" and origin.get("adapter_status") == "CANDIDATE_NOT_RUNTIME_ADOPTED", "SOURCE_IDENTITY_INVALID")
    require(origin.get("per_security_persistence_scope") == "ORIGINAL_PROVIDER_RESPONSE_BYTES", "SOURCE_PERSISTENCE_INVALID")
    require(origin.get("normalization_contract", {}).get("client_price_adjustment") == "NONE", "SOURCE_ADJUSTMENT_INVALID")
    manifest = object_from(manifest_raw, "SOURCE_MANIFEST_JSON_INVALID")
    require(origin.get("source_capture", {}).get("manifest_payload_sha256") == manifest.get("payload_sha256"), "SOURCE_MANIFEST_BINDING_MISMATCH")
    by_key, receipts = _validate_manifest(manifest, raw_responses)
    require(origin.get("source_capture", {}).get("record_count") == 8, "SOURCE_CAPTURE_COUNT_INVALID")
    require(origin.get("authentication_boundary") == {
        "authentication_response_retained": False,
        "captured_market_data_request_count": 8,
        "credentials_retained": False,
        "session_initialized_before_source_capture": True,
    }, "SOURCE_AUTHENTICATION_BOUNDARY_INVALID")
    contract = object_from(SOURCE_CONTRACT_PATH.read_bytes(), "SOURCE_CONTRACT_INVALID")
    dependency_lock = origin.get("dependency_lock")
    require(isinstance(dependency_lock, dict), "SOURCE_DEPENDENCY_LOCK_INVALID")
    for package in ("pykrx", "pandas", "numpy", "requests"):
        require(dependency_lock.get(package) == contract["dependency_lock"]["packages"][package]["version"],
                "SOURCE_DEPENDENCY_LOCK_INVALID")
    previous, current = manifest["dates"]
    expected_previous = dt.datetime.strptime(previous, "%Y%m%d").date().isoformat()
    expected_current = dt.datetime.strptime(current, "%Y%m%d").date().isoformat()
    require((source.get("previous_date"), source.get("as_of_date")) == (expected_previous, expected_current), "SOURCE_SESSION_IDENTITY_MISMATCH")
    require(source.get("available_at") == manifest.get("capture_completed_at_utc"), "SOURCE_AVAILABILITY_MISMATCH")
    require(source.get("generated_at") == manifest.get("capture_completed_at_utc"), "SOURCE_GENERATION_TIME_MISMATCH")
    calendar = origin.get("session_calendar")
    require(isinstance(calendar, dict)
            and calendar.get("evidence_sha256") == contract["session_calendar"]["sha256"]
            and calendar.get("previous_completed_session") == previous
            and calendar.get("latest_completed_session") == current,
            "SOURCE_CALENDAR_BINDING_INVALID")
    requests = origin.get("requests")
    require(isinstance(requests, dict) and set(requests) == {"stock", "index"},
            "SOURCE_LINEAGE_INVALID")
    expected_endpoints = {
        "stock": "KRX_INFORMATION_DATA_SYSTEM_PYKRX_STOCK_FRAME",
        "index": "KRX_INFORMATION_DATA_SYSTEM_PYKRX_INDEX_FRAME",
    }
    for family, markets in requests.items():
        require(isinstance(markets, dict) and set(markets) == set(MARKETS), "SOURCE_LINEAGE_INVALID")
        for market, row in markets.items():
            require(isinstance(row, dict) and row.get("endpoint") == expected_endpoints[family],
                    "SOURCE_ENDPOINT_MISMATCH")
            require(row.get("previous_fetched_at_utc") == receipts[f"{previous}:{market}:{family}"], "SOURCE_RECEIPT_BINDING_MISMATCH")
            require(row.get("current_fetched_at_utc") == receipts[f"{current}:{market}:{family}"], "SOURCE_RECEIPT_BINDING_MISMATCH")
            require(row.get("time_semantics") == "ACTUAL_RESPONSE_RECEIVED_AT_UTC", "SOURCE_TIME_SEMANTICS_INVALID")
            require(row.get("previous_response_sha256") == by_key[f"{previous}:{market}:{family}"][0]["normalized_frame"]["sha256"], "SOURCE_FRAME_HASH_MISMATCH")
            require(row.get("current_response_sha256") == by_key[f"{current}:{market}:{family}"][0]["normalized_frame"]["sha256"], "SOURCE_FRAME_HASH_MISMATCH")
    leadership_policy = object_from(LEADERSHIP_POLICY_PATH.read_bytes(), "LEADERSHIP_POLICY_INVALID")
    measurements = _rederive(_sessions(by_key, manifest["dates"], leadership_policy, expected_current), previous, current, leadership_policy)
    axes = source.get("axes")
    require(isinstance(axes, dict) and set(axes) == set(COMMON.load_common_v1_policy()["required_axes"]), "SOURCE_AXES_INVALID")
    for axis, measurement in measurements.items():
        require(axes[axis] == {"status": "OBSERVED", "measurement": measurement}, f"SOURCE_AXIS_MISMATCH_{axis}")
    reference_policy = object_from(REFERENCE_POLICY_PATH.read_bytes(), "REFERENCE_POLICY_INVALID")
    normalized = REFERENCE.normalize_kr_measurements(source, reference_policy)
    directions = {row["axis"]: row["direction"] for row in normalized}
    reported = wrapper.get("paper_reference")
    require(isinstance(reported, dict) and isinstance(reported.get("axes"), list),
            "PAPER_REFERENCE_REDERIVATION_MISMATCH")
    semantic_fields = ("axis", "direction", "score", "observed_value")
    require(
        [{key: row[key] for key in semantic_fields} for row in reported["axes"]]
        == [{key: row[key] for key in semantic_fields} for row in normalized],
        "PAPER_REFERENCE_REDERIVATION_MISMATCH",
    )
    rebuilt_reference = REFERENCE.build_kr(
        source, reference_policy, render_version=REFERENCE.CURRENT_RENDER_VERSION
    )
    require(reported.get("paper_reference") == rebuilt_reference.get("paper_reference"),
            "PAPER_REFERENCE_CLASSIFICATION_MISMATCH")
    return {"wrapper": wrapper, "source_packet": source, "axis_directions": directions,
            "available_at": source["available_at"], "previous_date": expected_previous,
            "as_of_date": expected_current, "source_sha256": sha256(reference_raw),
            "normalized_axes": normalized,
            "paper_reference": rebuilt_reference["paper_reference"]}


def validate_historical_replay(raw: bytes, expected_sha256: str,
                               acceptance_raw: bytes,
                               expected_acceptance_sha256: str) -> tuple[dict, list[dict]]:
    require(isinstance(expected_sha256, str) and SHA256.fullmatch(expected_sha256), "HISTORY_TRUST_ANCHOR_MISSING")
    require(sha256(raw) == expected_sha256, "HISTORY_BYTES_MISMATCH")
    report = object_from(raw, "HISTORY_JSON_INVALID")
    require(report.get("market") == "KR" and report.get("step_count") == 28, "HISTORY_SCOPE_INVALID")
    steps = report.get("steps")
    require(isinstance(steps, list) and len(steps) == 28, "HISTORY_CHAIN_INVALID")
    sequence = {"schema_version": 1, "market": "KR", "case_id": report.get("case_id"), "steps": [
        {"packet_id": row["packet_id"], "as_of_date": row["as_of_date"],
         "axes": {axis: {"status": "DEFINED", "direction": direction} for axis, direction in row["axis_directions"].items()}}
        for row in steps
    ]}
    try:
        rebuilt = COMMON.replay_common_v1(sequence)
    except COMMON.DecisionAuthorityError as exc:
        raise InformationSystemRuntimeError("HISTORY_REPLAY_REDERIVATION_MISMATCH") from exc
    require(rebuilt == report, "HISTORY_REPLAY_REDERIVATION_MISMATCH")
    require(isinstance(expected_acceptance_sha256, str) and SHA256.fullmatch(expected_acceptance_sha256),
            "HISTORY_ACCEPTANCE_TRUST_ANCHOR_MISSING")
    require(sha256(acceptance_raw) == expected_acceptance_sha256, "HISTORY_ACCEPTANCE_BYTES_MISMATCH")
    acceptance = object_from(acceptance_raw, "HISTORY_ACCEPTANCE_JSON_INVALID")
    require(acceptance.get("schema") == "kr_contiguous_historical_pit_receipt/1", "HISTORY_ACCEPTANCE_SCHEMA_INVALID")
    pit = acceptance.get("pit_status")
    require(isinstance(pit, dict) and pit.get("status") == "PIT_ACCEPTED", "HISTORY_NOT_ACCEPTED")
    require(pit.get("replay_report_sha256") == COMMON.payload_sha256(report),
            "HISTORY_ACCEPTANCE_REPLAY_BINDING_MISMATCH")
    coverage = acceptance.get("coverage")
    require(isinstance(coverage, dict) and coverage.get("requested_session_count") == 28
            and coverage.get("complete_five_axis_count") == 28
            and coverage.get("blocked_count") == 0, "HISTORY_ACCEPTANCE_COVERAGE_INVALID")
    return report, sequence["steps"]


def evaluate_runtime(*, reference_raw: bytes, manifest_raw: bytes,
                     raw_responses: Mapping[str, bytes], expected_source: Mapping[str, str],
                     historical_replay_raw: bytes, expected_historical_sha256: str,
                     historical_acceptance_raw: bytes, expected_historical_acceptance_sha256: str,
                     qualification_raw: bytes, expected_qualification_sha256: str,
                     evaluation_at: str, code_revision: str, session_boundary: dict) -> dict:
    now = instant(evaluation_at, "EVALUATION_TIME_INVALID")
    require(re.fullmatch(r"[0-9a-f]{40}", code_revision) is not None, "CODE_REVISION_INVALID")
    require(sha256(qualification_raw) == expected_qualification_sha256, "QUALIFICATION_BYTES_MISMATCH")
    qualification = object_from(qualification_raw, "QUALIFICATION_JSON_INVALID")
    require(qualification.get("schema_version") == "kr_information_system_runtime_qualification/1", "QUALIFICATION_SCHEMA_INVALID")
    require(qualification.get("status") == "RATIFIED_KR_PAPER_DISPLAY_ONLY", "QUALIFICATION_NOT_RATIFIED")
    authority = qualification.get("authority")
    require(isinstance(authority, dict) and authority.get("paper_runtime_display_authorized") is True, "QUALIFICATION_AUTHORITY_INVALID")
    require(all(value is False for key, value in authority.items() if key != "paper_runtime_display_authorized"), "QUALIFICATION_AUTHORITY_ESCALATION")
    bindings = qualification.get("bindings")
    require(isinstance(bindings, dict), "QUALIFICATION_BINDINGS_MISSING")
    expected_bindings = {
        "source_reference_sha256": expected_source["reference_sha256"],
        "source_manifest_sha256": expected_source["manifest_sha256"],
        "source_contract_sha256": expected_source["source_contract_sha256"],
        "leadership_policy_sha256": expected_source["leadership_policy_sha256"],
        "reference_policy_sha256": expected_source["reference_policy_sha256"],
        "raw_response_sha256": {
            path: sha256(raw) for path, raw in sorted(raw_responses.items())
        },
        "historical_replay_file_sha256": expected_historical_sha256,
        "historical_acceptance_sha256": expected_historical_acceptance_sha256,
        "common_policy_binding_sha256": COMMON.payload_sha256(
            COMMON.load_common_v1_policy()["binding"]
        ),
        "context_session_date": session_boundary.get("context_session_date"),
        "execution_session_date": session_boundary.get("execution_session_date"),
        "implementation_sha256": {
            path: sha256((ROOT / path).read_bytes()) for path in IMPLEMENTATION_PATHS
        },
    }
    require(bindings == expected_bindings, "QUALIFICATION_BINDING_MISMATCH")
    natural = validate_natural_evidence(reference_raw=reference_raw, manifest_raw=manifest_raw,
        raw_responses=raw_responses, expected=expected_source)
    history, steps = validate_historical_replay(
        historical_replay_raw, expected_historical_sha256,
        historical_acceptance_raw, expected_historical_acceptance_sha256,
    )
    require(history["steps"][-1]["as_of_date"] == natural["previous_date"], "HISTORY_LIVE_CHAIN_GAP")
    require(session_boundary.get("context_session_date") == natural["as_of_date"], "LATEST_SOURCE_NOT_CONTEXT_SESSION")
    require(session_boundary.get("context_session_close_at") == natural["as_of_date"] + "T06:30:00Z", "CONTEXT_CLOSE_MISMATCH")
    available = instant(natural["available_at"], "SOURCE_AVAILABILITY_INVALID")
    display_floor = dt.datetime.combine(dt.date.fromisoformat(natural["as_of_date"]), dt.time(18), ZoneInfo("Asia/Seoul")).astimezone(dt.timezone.utc)
    effective = instant(qualification.get("effective_at"), "QUALIFICATION_EFFECTIVE_TIME_INVALID")
    require(available >= display_floor, "SOURCE_BEFORE_EXISTING_EARLIEST_USABLE_TIME")
    require(max(available, effective) <= now, "SOURCE_NOT_YET_USABLE")
    require(now < instant(session_boundary.get("execution_session_close_at"), "EXECUTION_CLOSE_INVALID"), "LATEST_SOURCE_STALE")
    steps.append({"packet_id": natural["source_packet"]["payload_sha256"], "as_of_date": natural["as_of_date"],
        "axes": {axis: {"status": "DEFINED", "direction": direction} for axis, direction in natural["axis_directions"].items()}})
    sequence = {"schema_version": 1, "market": "KR", "case_id": "kr-paper-runtime-information-system", "steps": steps}
    replay = COMMON.replay_common_v1(sequence)
    current = replay["steps"][-1]
    require(replay["final_regime"] != "UNKNOWN", "COMMON_CONFIRMATION_PENDING")
    paper_reference = natural["paper_reference"]
    return {
        "schema_version": "kr_paper_runtime_decision/5", "market": "KR",
        "evaluation_at": evaluation_at, "code_revision": code_revision,
        "evidence_class": "LIVE_NATURAL", "source_adapter": "KRX_INFORMATION_DATA_SYSTEM_PYKRX",
        "decision_status": "PAPER_RUNTIME_CLASSIFIED", "paper_regime": replay["final_regime"],
        "runtime_regime": replay["final_regime"], "direction": replay["final_direction"],
        "confidence": replay["final_confidence"], "runtime_decision_available": True,
        "current_observation": {"as_of_date": natural["as_of_date"],
            "candidate_regime": current["raw_classification"], "score": current["score"],
            "candidate_confidence": paper_reference["confidence"],
            "confirmed_regime": current["confirmed_regime"], "hysteresis": current["hysteresis"],
            "leadership": natural["normalized_axes"][-1]},
        "source_sha256": natural["source_sha256"], "source_manifest_sha256": sha256(manifest_raw),
        "historical_replay_sha256": expected_historical_sha256,
        "historical_acceptance_sha256": expected_historical_acceptance_sha256,
        "qualification_sha256": expected_qualification_sha256,
        "session_boundary_freshness": session_boundary, "aggregation": replay,
        "reasons": [],
        "authority": {"paper_runtime_display_authorized": True, "strategy_authorized": False,
            "stage_authorized": False, "buy_authorized": False, "action_authorized": False,
            "capital_authorized": False, "order_authorized": False, "production_authorized": False,
            "trading_authorized": False, "real_authorized": False},
    }
