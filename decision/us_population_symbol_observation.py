#!/usr/bin/env python3
"""US adapter for the full-population symbol observation packet.

Population: ``us_global_universe_packet/1`` ``source_attribute_rows`` (Nasdaq
Trader directories) for the source date.  Per-symbol inputs: the IEX daily
bars already captured by ``collectors/free_market_data.py`` for the contract's
fixed symbol list, grouped by symbol exactly as the bounded review groups
them.  A symbol without bars is ``DATA_NOT_OBSERVED`` and ``NOT_EVALUABLE``
with ``PRICE_SOURCE_NOT_CONFIGURED`` (a watchlist symbol outside the fixed
list) or ``PRICE_SOURCE_NOT_RETAINED`` (everything else); its directory facts
and the ``us_investable_registry`` facts it still lacks are recorded so the
gap is explicit.  The registry evaluator is *not* run with fabricated fact
records.
"""
from __future__ import annotations

import copy

from pathlib import Path

import importlib.util
import sys


ROOT = Path(__file__).resolve().parents[1]


def _core():
    module = sys.modules.get("population_symbol_observation")
    if module is None:
        spec = importlib.util.spec_from_file_location(
            "population_symbol_observation", ROOT / "decision" / "population_symbol_observation.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["population_symbol_observation"] = module
        spec.loader.exec_module(module)
    return module


CORE = _core()
DATE_RE = CORE.DATE_RE
_fail = CORE._fail
formal_candidate = CORE.formal_candidate
load_module = CORE.load_module
payload_sha256 = CORE.payload_sha256
read_json = CORE.read_json
source_ref = CORE.source_ref
stage_snapshot = CORE.stage_snapshot
symbol_row = CORE.symbol_row

US_REVIEW = load_module("population_us_symbol_market_review", "decision/us_symbol_market_review.py")
US_REGISTRY = load_module("population_us_investable_registry", "universe/us_investable_registry.py")


def _latest_universe(root: Path, session_date: str | None) -> Path:
    base = root / "data" / "observations" / "us_global_universe"
    found = []
    for directory in sorted(base.iterdir()) if base.is_dir() else []:
        candidate = directory / "packet.json"
        if DATE_RE.fullmatch(directory.name) is None or not candidate.is_file():
            continue
        record = read_json(candidate, "US_UNIVERSE_READ_FAILED")
        if record.get("source_date") != directory.name:
            _fail("US_UNIVERSE_DIRECTORY_DATE_MISMATCH", str(candidate))
        found.append((directory.name, candidate))
    if session_date is not None:
        match = [path for date, path in found if date == session_date]
        if not match:
            _fail("US_UNIVERSE_FOR_SESSION_MISSING", session_date)
        return match[0]
    if not found:
        _fail("US_UNIVERSE_MISSING")
    return sorted(found)[-1][1]


def default_inputs(root: Path = ROOT, *, session_date: str | None = None) -> dict:
    universe_path = _latest_universe(root, session_date)
    session = session_date or read_json(universe_path, "US_UNIVERSE_READ_FAILED")["source_date"]
    return {
        "session_date": session,
        "universe_path": universe_path,
        "market_data_path": root / "data" / "latest_free_market_data.json",
        "stage_history_path": root / "data" / "stage_history.json",
        "bounded_review_path": root / "data" / "latest_us_symbol_market_review.json",
    }


def load_context(inputs: dict, *, generated_at: str, contract: dict) -> dict:
    session = inputs["session_date"]
    universe = read_json(inputs["universe_path"], "US_UNIVERSE_READ_FAILED")
    unsigned = dict(universe)
    claimed = unsigned.pop("payload_sha256", None)
    packet = universe.get("packet")
    if (
        payload_sha256(unsigned) != claimed
        or universe.get("schema_version") != "us_forward_universe_population/2"
        or not isinstance(packet, dict)
        or packet.get("schema_version") != "us_global_universe_packet/1"
    ):
        _fail("US_UNIVERSE_INVALID")
    if packet.get("as_of_date") != session:
        _fail("US_UNIVERSE_SESSION_MISMATCH", f"{packet.get('as_of_date')}!={session}")
    population: dict = {}
    for row in packet["source_attribute_rows"]:
        symbol = row.get("primary_symbol")
        if not isinstance(symbol, str) or not symbol:
            _fail("US_UNIVERSE_ROW_SYMBOL_INVALID")
        population.setdefault(symbol, []).append(row)
    market = read_json(inputs["market_data_path"], "US_MARKET_DATA_READ_FAILED")
    stages = read_json(inputs["stage_history_path"], "STAGE_HISTORY_READ_FAILED")
    stage_as_of, latest_stage = stage_snapshot(stages)
    review_contract = US_REVIEW.load_contract()
    bounded = US_REVIEW.build_review(market, stages, contract=review_contract)
    committed = read_json(inputs["bounded_review_path"], "US_BOUNDED_REVIEW_READ_FAILED")
    if committed != bounded:
        _fail("US_BOUNDED_REVIEW_NOT_REPRODUCIBLE")
    # The session must actually be the one this market data describes.
    #
    # KR enforces this with exact equality on the date
    # (``KR_BOUNDED_REVIEW_SESSION_MISMATCH``) because its
    # ``operational_date_kst`` IS the session by construction
    # (``korea_symbol_market_review`` sets it from ``market["as_of_date"]``).  US
    # has no such field: ``us_symbol_market_review`` derives
    # ``operational_date_kst`` from the observation instant in Asia/Seoul, and
    # the capture's wall-clock distance from the session is not a defect signal
    # at all -- over a weekend it is legitimately 2-3 days (the 2026-09-13
    # snapshot is a Sunday capture of the Friday 2026-09-11 session, and is
    # correct).
    #
    # What actually matters is coverage, not elapsed days: the newest session
    # this capture contains must BE the session the packet claims.  If the
    # capture already holds a later session, the packet would evaluate an older
    # session using data that includes later trading -- on 2026-09-18 a
    # session-2026-09-16 packet would be built from a capture whose newest bar
    # is 2026-09-17.  If it holds only earlier sessions, it does not cover this
    # session at all.  Both are refused; a weekend-lagged capture whose newest
    # bar is the session passes.
    bar_days = sorted({
        bar["opened_at"][:10]
        for bar in (market.get("alpaca") or {}).get("daily_bars") or []
        if isinstance(bar, dict) and isinstance(bar.get("opened_at"), str)
    })
    if not bar_days:
        _fail("US_MARKET_DATA_NO_SESSION_BARS", session)
    if bar_days[-1] != session:
        _fail("US_BOUNDED_REVIEW_SESSION_MISMATCH",
              f"newest_daily_bar={bar_days[-1]} session={session} "
              f"observed_at_utc={market.get('observed_at_utc')}")
    bounded_rows = {row["symbol"]: row for row in bounded["symbols"]}
    source = US_REVIEW._compact_source(market, stages, review_contract)
    coverage = US_REVIEW._axes(source, review_contract)
    bars: dict = {}
    alpaca = market.get("alpaca") or {}
    for bar in alpaca.get("daily_bars") or []:
        if isinstance(bar, dict) and isinstance(bar.get("symbol"), str):
            bars.setdefault(bar["symbol"], []).append(copy.deepcopy(bar))
    for rows in bars.values():
        rows.sort(key=lambda row: row.get("opened_at", ""))
    registry_contract = US_REGISTRY.load_contract()
    input_refs = [
        source_ref(inputs["universe_path"], universe["payload_sha256"]),
        source_ref(inputs["market_data_path"], market.get("packet_sha256")),
        source_ref(inputs["stage_history_path"], payload_sha256(stages)),
        source_ref(inputs["bounded_review_path"], bounded["packet_sha256"]),
    ]
    policy_undefined = [
        {"condition": f"US_{policy.upper()}_RATIFIED", "status": "UNMET", "defined_by": "us_global_universe.policy_status"}
        for policy, status in sorted((packet.get("policy_status") or {}).items()) if status == "UNRATIFIED"
    ] + [
        {"condition": "US_LIQUIDITY_POLICY_RATIFIED", "status": "UNMET",
         "defined_by": "us_investable_registry_contract.liquidity.repository_default_policy=" + str((registry_contract.get("liquidity") or {}).get("repository_default_policy"))},
        {"condition": "FINAL_US_REGIME_AVAILABLE", "status": "UNMET",
         "defined_by": "us_symbol_market_review five_axis.aggregate_regime=" + str(bounded["five_axis"].get("aggregate_regime"))},
        {"condition": "CANDIDATE_PASS_RULE", "status": "미정", "defined_by": "no ratified population-level candidate rule"},
        {"condition": "STAGE_TRANSITION_RULE", "status": "미정", "defined_by": "no ratified Discovery/Candidate/Ready transition rule"},
    ]
    snapshot_at = max(
        value for value in (bounded["generated_at"], market.get("observed_at_utc"), packet.get("as_of_utc"), universe.get("generated_at"))
        if isinstance(value, str) and CORE.UTC_RE.fullmatch(value)
    )
    return {
        "market": "US",
        "session_date": session,
        "generated_at": generated_at,
        "snapshot_at": snapshot_at,
        "contract": contract,
        "review_contract": review_contract,
        "registry_contract": registry_contract,
        "universe": universe,
        "population_records": population,
        "population_symbols": sorted(population),
        "population": {
            "count": len(population),
            "row_count": len(packet["source_attribute_rows"]),
            "as_of": packet["as_of_date"],
            "as_of_utc": packet.get("as_of_utc"),
            "population_id": (packet.get("asset_master") or {}).get("master_id"),
            "semantics": packet["membership_semantics"],
            "source_counts": copy.deepcopy(packet.get("source_counts")),
            "source": source_ref(inputs["universe_path"], universe["payload_sha256"]),
        },
        "stage_as_of": stage_as_of,
        "latest_stage": latest_stage,
        "bounded": bounded,
        "bounded_rows": bounded_rows,
        "bounded_subjects": set(review_contract["supported_pipeline_subjects"]),
        "coverage": coverage,
        "leadership_by_symbol": US_REVIEW._leadership_by_symbol(coverage),
        "breadth": coverage["axes"]["BREADTH"].get("facts"),
        "source_scope": source["market_capture"]["alpaca_scope"],
        "bars": bars,
        "configured_symbols": sorted(bars),
        "input_refs": input_refs,
        "policy_undefined": policy_undefined,
        "sources": {
            "universe": source_ref(inputs["universe_path"], universe["payload_sha256"]),
            "market_data": {"observed_at_utc": market.get("observed_at_utc"), "feed": alpaca.get("feed"),
                            "source_scope": alpaca.get("source_scope"), "daily_bar_symbols": sorted(bars),
                            "daily_bar_rows": len(alpaca.get("daily_bars") or []),
                            **source_ref(inputs["market_data_path"], market.get("packet_sha256"))},
            "bounded_review": {"generated_at": bounded["generated_at"], "operational_date_kst": bounded["operational_date_kst"],
                               **source_ref(inputs["bounded_review_path"], bounded["packet_sha256"])},
            "stage_history": {"as_of": stage_as_of, **source_ref(inputs["stage_history_path"], payload_sha256(stages))},
        },
    }


def _directory_facts(rows: list, registry_contract: dict) -> dict:
    fields = [row.get("fields") or {} for row in rows]
    etf = sorted({str(f.get("ETF")) for f in fields if f.get("ETF") is not None})
    test_issue = sorted({str(f.get("Test Issue")) for f in fields if f.get("Test Issue") is not None})
    financial = sorted({str(f.get("Financial Status")) for f in fields if f.get("Financial Status") is not None})
    available = {"listing": True, "security_type": bool(etf), "liquidity": False, "trading_halt": False,
                 "scheduled_delisting": False, "corporate_action_state": False}
    required = registry_contract.get("required_fail_closed_facts") or []
    return {
        "source_names": sorted({str(row.get("source_name")) for row in rows}),
        "security_names": sorted({str(f.get("Security Name")) for f in fields if f.get("Security Name")}),
        "etf_flag": etf,
        "test_issue": test_issue,
        "financial_status": financial if financial else None,
        "investable_eligible": [row.get("investable_eligible") for row in rows],
        "registry_required_facts_missing": [fact for fact in required if not available.get(fact, False)],
        "registry_evaluation": "NOT_RUN:REQUIRED_FACTS_MISSING",
    }


def build_symbol(ctx: dict, symbol: str) -> dict:
    rows = ctx["population_records"][symbol]
    session = ctx["session_date"]
    formal = formal_candidate(symbol, ctx["stage_as_of"], ctx["latest_stage"], ctx["bounded_subjects"])
    name = next((str((row.get("fields") or {}).get("Security Name")) for row in rows if (row.get("fields") or {}).get("Security Name")), None)
    membership = {
        "asset_ids": [row.get("asset_id") for row in rows],
        "source_names": sorted({str(row.get("source_name")) for row in rows}),
        "duplicate_across_sources": len(rows) > 1,
    }
    facts = _directory_facts(rows, ctx["registry_contract"])
    evidence_refs = []
    bars = ctx["bars"].get(symbol) or []
    in_stage_history = symbol in ctx["latest_stage"]

    if symbol in ctx["bounded_rows"]:
        row = copy.deepcopy(ctx["bounded_rows"][symbol])
        price_ok = row["price_context"]["status"] == "OBSERVED"
        evaluability = (
            {"status": "EVALUABLE", "level": "EVALUABLE_PRICE", "reasons": [], "session_price_present": True}
            if price_ok else
            {"status": "NOT_EVALUABLE", "level": None, "reasons": ["PRICE_SOURCE_NOT_CONFIGURED"], "session_price_present": False}
        )
        return symbol_row(
            symbol=symbol, name=row.get("name") or name, membership=membership,
            data_observation={"status": "DATA_OBSERVED" if price_ok else "DATA_NOT_OBSERVED",
                              "session_date": row["price_context"].get("as_of_session_date"),
                              "sources": ["iex_daily_bars"] if price_ok else [],
                              "fields_present": ["daily_bars"] if price_ok else [], "fields_missing": [] if price_ok else ["daily_bars"]},
            evaluability=evaluability,
            evaluation={"status": "EVALUATED_BOUNDED", "evaluator_contract": ctx["bounded"]["contract_version"],
                        "evaluated_at": ctx["bounded"]["generated_at"], "entry_state": row["entry_review"]["state"],
                        "reasons": list(row["entry_review"]["reasons"]), "row": row, "row_source": "bounded_review_packet"},
            formal=formal, facts=facts, evidence_refs=evidence_refs + [{"role": "bounded_review_row", "packet_sha256": ctx["bounded"]["packet_sha256"]}],
        )

    if bars:
        identity = {"name": (ctx["latest_stage"].get(symbol) or {}).get("name") or name or symbol, "stage": formal["stage"]}
        try:
            row = US_REVIEW._symbol_row(
                symbol, identity, bars, ctx["coverage"], ctx["review_contract"], ctx["stage_as_of"],
                leadership_by_symbol=ctx["leadership_by_symbol"], breadth=ctx["breadth"], source_scope=ctx["source_scope"],
            )
            evaluation = {"status": "EVALUATED", "evaluator_contract": ctx["review_contract"]["contract_version"],
                          "evaluated_at": ctx["snapshot_at"], "evaluated_at_basis": "INPUT_SNAPSHOT_TIME",
                          "entry_state": row["entry_review"]["state"],
                          "reasons": list(row["entry_review"]["reasons"]), "row": row, "row_source": "extracted_symbol_row"}
            evaluability = {"status": "EVALUABLE", "level": "EVALUABLE_PRICE", "reasons": [], "session_price_present": True}
        except (US_REVIEW.UsSymbolMarketReviewError, KeyError, TypeError, ValueError) as exc:
            evaluability = {"status": "NOT_EVALUABLE", "level": None, "reasons": [f"ROW_BUILD_FAILED:{exc}"], "session_price_present": True}
            evaluation = {"status": "NOT_EVALUATED", "entry_state": None, "reasons": [f"ROW_BUILD_FAILED:{exc}"], "row": None}
        return symbol_row(
            symbol=symbol, name=identity["name"], membership=membership,
            data_observation={"status": "DATA_OBSERVED", "session_date": str(bars[-1].get("opened_at"))[:10],
                              "sources": ["iex_daily_bars"], "fields_present": ["daily_bars"], "fields_missing": []},
            evaluability=evaluability, evaluation=evaluation, formal=formal, facts=facts,
            evidence_refs=evidence_refs + [{"role": "iex_daily_bars", "session_count": len(bars)}],
        )

    reason = "PRICE_SOURCE_NOT_CONFIGURED" if in_stage_history else "PRICE_SOURCE_NOT_RETAINED"
    return symbol_row(
        symbol=symbol, name=(ctx["latest_stage"].get(symbol) or {}).get("name") or name, membership=membership,
        data_observation={"status": "DATA_NOT_OBSERVED", "session_date": session, "sources": [], "fields_present": ["directory_attributes"],
                          "fields_missing": ["daily_bars"]},
        evaluability={"status": "NOT_EVALUABLE", "level": None, "reasons": [reason], "session_price_present": False},
        evaluation={"status": "NOT_EVALUATED", "entry_state": None, "reasons": [reason], "row": None},
        formal=formal, facts=facts, evidence_refs=evidence_refs,
    )


def reconciliation(ctx: dict, rows: list) -> dict:
    by_symbol = {row["symbol"]: row for row in rows}
    bounded_ok = all(
        by_symbol.get(symbol, {}).get("evaluation", {}).get("row") == row
        for symbol, row in ctx["bounded_rows"].items()
    )
    return {
        "bounded_rows_byte_identical_to_review": bounded_ok,
        "bounded_review_symbols": sorted(ctx["bounded_rows"]),
        "daily_bar_symbols_not_in_population": sorted(set(ctx["bars"]) - set(ctx["population_records"])),
        "duplicate_primary_symbols_across_sources": sorted(s for s, r in ctx["population_records"].items() if len(r) > 1),
        "stage_history_symbols_not_in_population": sorted(
            s for s in ctx["latest_stage"] if s.isalpha() and s not in ctx["population_records"]
        ),
    }
