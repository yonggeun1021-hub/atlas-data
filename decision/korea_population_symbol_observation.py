#!/usr/bin/env python3
"""KR adapter for the full-population symbol observation packet.

Population: ``krx_global_universe_packet/1`` records for the session date.
Per-symbol inputs, in priority order:

1. the watchlist observation file ``data/briefing/krx/<symbol>.json`` (pykrx
   confirmed close, SMA20, investor flows) -- the same file the bounded
   review already consumes;
2. the ``price_history_session/1`` store (``universe/price_history_store.py``)
   when a caller configures its root.  The store lives in the private
   evidence repository, so this input is absent by default and the packet is
   then byte-identical to the one produced before the store existed.  When it
   is configured, a symbol holding this session's stored bar reaches the
   contract's existing ``EVALUABLE_PRICE`` level -- the same level the US
   adapter already uses for daily bars -- and SMA20 becomes computable from
   the stored closes.  Investor flows still do not exist in this source, so
   the row stays explicitly incomplete rather than being completed by
   estimation;
3. the retained KRX information-system all-stock response for the session
   (``evidence/regime/kr_information_system/<pub>/source-capture``), verified
   through the runtime bridge's manifest validator and projected with the
   capture module's own ``_raw_projection`` (close, return, trading value,
   market cap).  SMA20 and investor flows do not exist there, so such a
   symbol is ``DATA_OBSERVED`` but ``NOT_EVALUABLE`` with the exact reasons.

Rows for the bounded review's own subjects are copied from the review packet
the contract already produces (verified byte-identical); other watchlist
symbols with full inputs go through the same extracted ``_symbol_row``.
Nothing is estimated, no stage is changed, no candidate is promoted.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import re

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
PopulationSymbolObservationError = CORE.PopulationSymbolObservationError
_fail = CORE._fail
file_sha256 = CORE.file_sha256
formal_candidate = CORE.formal_candidate
load_module = CORE.load_module
payload_sha256 = CORE.payload_sha256
read_json = CORE.read_json
relative = CORE.relative
source_ref = CORE.source_ref
stage_snapshot = CORE.stage_snapshot
symbol_row = CORE.symbol_row

KOREA_REVIEW = load_module("population_korea_symbol_market_review", "decision/korea_symbol_market_review.py")
CAPTURE = load_module("population_krx_information_system_capture", "regime/krx_information_system_capture.py")
BRIDGE = load_module("population_kr_information_system_runtime_bridge", "regime/kr_information_system_runtime_bridge.py")
COMPACT_DATE_RE = re.compile(r"^\d{8}$")


def _latest_universe(root: Path, session_date: str | None) -> Path:
    base = root / "data" / "observations" / "krx_global_universe"
    found = []
    for directory in sorted(base.iterdir()) if base.is_dir() else []:
        candidate = directory / "packet.json"
        if DATE_RE.fullmatch(directory.name) is None or not candidate.is_file():
            continue
        record = read_json(candidate, "KR_UNIVERSE_READ_FAILED")
        if record.get("as_of_date") != directory.name:
            _fail("KR_UNIVERSE_DIRECTORY_DATE_MISMATCH", str(candidate))
        found.append((directory.name, candidate))
    if session_date is not None:
        match = [path for date, path in found if date == session_date]
        if not match:
            _fail("KR_UNIVERSE_FOR_SESSION_MISSING", session_date)
        return match[0]
    if not found:
        _fail("KR_UNIVERSE_MISSING")
    return sorted(found)[-1][1]


def _latest_capture_dir(root: Path, session_compact: str) -> Path | None:
    base = root / "evidence" / "regime" / "kr_information_system"
    if not base.is_dir():
        return None
    candidates = []
    for directory in sorted(base.iterdir()):
        manifest = directory / "source-capture" / "manifest.json"
        if not manifest.is_file():
            continue
        record = read_json(manifest, "KR_CAPTURE_MANIFEST_READ_FAILED")
        if session_compact in (record.get("dates") or []):
            candidates.append(directory / "source-capture")
    return candidates[-1] if candidates else None


def default_inputs(root: Path = ROOT, *, session_date: str | None = None) -> dict:
    market = read_json(root / "data" / "latest_korea_market_signals.json", "KR_MARKET_SIGNALS_READ_FAILED")
    session = session_date or market.get("as_of_date")
    if not isinstance(session, str) or DATE_RE.fullmatch(session) is None:
        _fail("KR_SESSION_DATE_INVALID", repr(session))
    compact = session.replace("-", "")
    return {
        "session_date": session,
        "universe_path": _latest_universe(root, session),
        "market_signals_path": root / "data" / "latest_korea_market_signals.json",
        "stage_history_path": root / "data" / "stage_history.json",
        "bounded_review_path": root / "data" / "latest_korea_symbol_market_review.json",
        "watchlist_root": root / "data" / "briefing" / "krx",
        "capture_dir": _latest_capture_dir(root, compact),
        "price_history_root": price_history_root(),
    }


PRIVATE_ONLY_DISTRIBUTION = "PRIVATE_ONLY_KRX_OPENAPI_DERIVED"


def price_history_root() -> Path | None:
    """Root of the private ``price_history_session/1`` store, when configured.

    The store is private evidence, so the public default is "not configured".
    An absent store is reported as absent; it is never substituted with a
    partial source.
    """
    configured = os.environ.get("ATLAS_PRICE_HISTORY_ROOT") or ""
    return Path(configured) if configured.strip() else None


def _load_price_history(inputs: dict, snapshot_at: str) -> dict:
    """Session bars from the private price-history store, as of ``snapshot_at``.

    Only sessions whose ``first_available_observed_at_utc`` is at or before
    the evaluation snapshot are read, so the packet can never see a bar that
    did not exist yet.  A gap stays a gap: the store never pads a symbol's
    window, and this loader never fills one.
    """
    root = inputs.get("price_history_root")
    if root is None:
        return {
            "status": "NOT_CONFIGURED", "window": [], "by_code": {},
            "sma_sessions": None, "sma_report": None, "latest_bas_dd": None,
        }
    if CORE.inside_public_repository(Path(root)):
        _fail("KR_PRICE_HISTORY_STORE_INSIDE_PUBLIC_REPOSITORY", str(root))
    store_module = load_module("population_price_history_store", "universe/price_history_store.py")
    try:
        store = store_module.PriceHistoryStore(root)
        sma_sessions = int(store.contract["sma20_sessions"])
        window = store.session_window("KR", sma_sessions, snapshot_at)
        by_code: dict[str, list] = {}
        for day in window:
            for code, row in store.session_index("KR", day).items():
                by_code.setdefault(code, []).append({"bas_dd": day, **row})
        report = store.sma_readiness("KR", snapshot_at, sessions=sma_sessions)
    except (store_module.PriceHistoryStoreError, OSError, ValueError) as exc:
        _fail("KR_PRICE_HISTORY_STORE_INVALID", str(exc))
    return {
        "status": "LOADED",
        "store": store,
        "window": window,
        "by_code": by_code,
        "sma_sessions": sma_sessions,
        "sma_report": report,
        "latest_bas_dd": window[-1] if window else None,
    }


def _load_capture(capture_dir: Path | None, session_compact: str) -> dict:
    if capture_dir is None:
        return {"status": "NOT_AVAILABLE", "rows": {}, "projection": {}, "retained_sessions": 0, "refs": []}
    manifest_path = Path(capture_dir) / "manifest.json"
    manifest = read_json(manifest_path, "KR_CAPTURE_MANIFEST_READ_FAILED")
    raw_responses = {}
    refs = [source_ref(manifest_path, manifest.get("payload_sha256"))]
    for record in manifest.get("records") or []:
        path = (record.get("response") or {}).get("path")
        if isinstance(path, str):
            file_path = Path(capture_dir) / path
            raw_responses[path] = file_path.read_bytes()
            refs.append(source_ref(file_path, (record.get("response") or {}).get("sha256")))
    try:
        by_key, _receipts = BRIDGE._validate_manifest(manifest, raw_responses)
    except BRIDGE.InformationSystemRuntimeError as exc:
        _fail("KR_CAPTURE_MANIFEST_INVALID", str(exc))
    rows: dict = {}
    projection: dict = {}
    for key, (record, raw) in by_key.items():
        day, market_name, family = key.split(":")
        if family != "stock" or day != session_compact:
            continue
        _obj, payload_rows = CAPTURE._validate_provider_payload(raw, "stock")
        frame = CAPTURE._raw_projection(raw, "stock")
        for payload_row in payload_rows:
            code = CAPTURE._normalize_pykrx(payload_row["ISU_SRT_CD"]).zfill(6)
            if code in rows:
                _fail("KR_CAPTURE_DUPLICATE_CODE", code)
            rows[code] = {
                "market": market_name,
                "name": payload_row.get("ISU_ABBRV"),
                "market_segment": payload_row.get("SECT_TP_NM") or None,
                "response_path": relative(Path(capture_dir) / record["response"]["path"]),
                "response_sha256": record["response"]["sha256"],
            }
        projection.update(frame)
    return {
        "status": "VALIDATED" if rows else "SESSION_NOT_IN_CAPTURE",
        "rows": rows,
        "projection": projection,
        "retained_sessions": len(manifest.get("dates") or []),
        "capture_completed_at_utc": manifest.get("capture_completed_at_utc"),
        "refs": refs,
    }


def _load_watchlist(watchlist_root: Path) -> dict:
    observed = {}
    if not Path(watchlist_root).is_dir():
        return observed
    for path in sorted(Path(watchlist_root).glob("*.json")):
        record = read_json(path, "KR_WATCHLIST_READ_FAILED")
        symbol = record.get("symbol")
        if not isinstance(symbol, str) or symbol != path.stem:
            _fail("KR_WATCHLIST_IDENTITY_INVALID", str(path))
        observed[symbol] = {"record": record, "ref": source_ref(path, (record.get("source") or {}).get("source_sha256"))}
    return observed


def load_context(inputs: dict, *, generated_at: str, contract: dict) -> dict:
    session = inputs["session_date"]
    compact = session.replace("-", "")
    universe = read_json(inputs["universe_path"], "KR_UNIVERSE_READ_FAILED")
    unsigned = dict(universe)
    claimed = unsigned.pop("payload_sha256", None)
    if payload_sha256(unsigned) != claimed or universe.get("schema_version") != "krx_global_universe_packet/1":
        _fail("KR_UNIVERSE_INVALID")
    if universe.get("as_of_date") != session:
        _fail("KR_UNIVERSE_SESSION_MISMATCH", f"{universe.get('as_of_date')}!={session}")
    records = universe["asset_master"]["records"]
    population = {}
    for record in records:
        symbol = record.get("primary_symbol")
        if symbol in population:
            _fail("KR_UNIVERSE_DUPLICATE_SYMBOL", symbol)
        population[symbol] = record
    market = read_json(inputs["market_signals_path"], "KR_MARKET_SIGNALS_READ_FAILED")
    stages = read_json(inputs["stage_history_path"], "STAGE_HISTORY_READ_FAILED")
    stage_as_of, latest_stage = stage_snapshot(stages)
    review_contract = KOREA_REVIEW.load_contract()
    # The bounded review must read the same watchlist files this run records
    # in input_refs, not the module-default data/briefing/krx behind them.
    bounded = KOREA_REVIEW.build_review(
        market, stages, contract=review_contract, briefing_root=Path(inputs["watchlist_root"])
    )
    committed = read_json(inputs["bounded_review_path"], "KR_BOUNDED_REVIEW_READ_FAILED")
    if committed != bounded:
        _fail("KR_BOUNDED_REVIEW_NOT_REPRODUCIBLE")
    if bounded["operational_date_kst"] != session:
        _fail("KR_BOUNDED_REVIEW_SESSION_MISMATCH", f"{bounded['operational_date_kst']}!={session}")
    bounded_rows = {row["symbol"]: row for row in bounded["symbols"]}
    watchlist = _load_watchlist(inputs["watchlist_root"])
    capture = _load_capture(inputs.get("capture_dir"), compact)
    input_refs = [
        source_ref(inputs["universe_path"], universe["payload_sha256"]),
        source_ref(inputs["market_signals_path"], market.get("payload_sha256")),
        source_ref(inputs["stage_history_path"], payload_sha256(stages)),
        source_ref(inputs["bounded_review_path"], bounded["packet_sha256"]),
        *[entry["ref"] for entry in watchlist.values()],
        *capture["refs"],
    ]
    policy_undefined = [
        {"condition": f"KRX_{policy.upper()}_RATIFIED", "status": "UNMET", "defined_by": "krx_global_universe.policy_status"}
        for policy, status in sorted((universe.get("policy_status") or {}).items()) if status == "UNRATIFIED"
    ] + [
        {"condition": "FINAL_KOREA_REGIME_POLICY_RATIFIED", "status": "UNMET",
         "defined_by": "korea_symbol_market_review five_axis.final_policy=" + str(bounded["five_axis"].get("final_policy"))},
        {"condition": "CANDIDATE_PASS_RULE", "status": "미정", "defined_by": "no ratified population-level candidate rule"},
        {"condition": "STAGE_TRANSITION_RULE", "status": "미정", "defined_by": "no ratified Discovery/Candidate/Ready transition rule"},
    ]
    snapshot_at = max(
        value for value in (
            bounded["generated_at"], market.get("available_at"), universe.get("asset_master", {}).get("generated_at"),
            capture.get("capture_completed_at_utc"),
            *[(entry["record"].get("source") or {}).get("collected_at_utc", "").replace("+00:00", "Z") for entry in watchlist.values()],
        ) if isinstance(value, str) and CORE.UTC_RE.fullmatch(value)
    )
    price_history = _load_price_history(inputs, snapshot_at)
    sources = {
        "universe": source_ref(inputs["universe_path"], universe["payload_sha256"]),
        "market_signals": {"as_of_date": market.get("as_of_date"), "available_at": market.get("available_at"),
                           **source_ref(inputs["market_signals_path"], market.get("payload_sha256"))},
        "bounded_review": {"generated_at": bounded["generated_at"], "operational_date_kst": bounded["operational_date_kst"],
                           **source_ref(inputs["bounded_review_path"], bounded["packet_sha256"])},
        "stage_history": {"as_of": stage_as_of, **source_ref(inputs["stage_history_path"], payload_sha256(stages))},
        "watchlist_files": sorted(watchlist),
        "information_system_capture": {
            "status": capture["status"],
            "retained_sessions": capture["retained_sessions"],
            "capture_completed_at_utc": capture.get("capture_completed_at_utc"),
            "directory": relative(inputs["capture_dir"]) if inputs.get("capture_dir") else None,
            "session_row_count": len(capture["rows"]),
        },
    }
    if price_history["status"] == "LOADED":
        # Only a configured store adds this block, so the default packet stays
        # byte-identical to the one produced before the store existed.
        sources["price_history_store"] = {
            "status": price_history["status"],
            "contract_version": "price_history_session/1",
            "sessions_in_window": len(price_history["window"]),
            "window_first_bas_dd": price_history["window"][0] if price_history["window"] else None,
            "window_last_bas_dd": price_history["latest_bas_dd"],
            "sma_sessions": price_history["sma_sessions"],
            "sma_computable_symbol_count": (price_history["sma_report"] or {}).get(
                "sma_computable_symbol_count"
            ),
            "sma_readiness_status": (price_history["sma_report"] or {}).get("status"),
            "distribution": PRIVATE_ONLY_DISTRIBUTION,
        }
    return {
        "market": "KR",
        # Per-symbol KRX Open API fields (close, return, traded value, market
        # cap, SMA) flow into rows only when the private store is loaded; the
        # core builder refuses to persist such a packet inside this public
        # repository (KRX Open API terms: no redistribution).
        "distribution": PRIVATE_ONLY_DISTRIBUTION if price_history["status"] == "LOADED" else "PUBLIC",
        "session_date": session,
        "session_compact": compact,
        "price_history": price_history,
        "generated_at": generated_at,
        "snapshot_at": snapshot_at,
        "contract": contract,
        "review_contract": review_contract,
        "missing_policy": contract["missing_policy"]["KR"],
        "universe": universe,
        "population_records": population,
        "population_symbols": sorted(population),
        "population": {
            "count": len(population),
            "as_of": universe["as_of_date"],
            "population_id": universe["asset_master"]["master_id"],
            "semantics": universe["membership_semantics"],
            "market_counts": copy.deepcopy(universe["market_counts"]),
            "source": source_ref(inputs["universe_path"], universe["payload_sha256"]),
        },
        "stage_as_of": stage_as_of,
        "latest_stage": latest_stage,
        "bounded": bounded,
        "bounded_rows": bounded_rows,
        "bounded_subjects": set(review_contract["supported_pipeline_subjects"]),
        "watchlist": watchlist,
        "capture": capture,
        "input_refs": input_refs,
        "policy_undefined": policy_undefined,
        "sources": sources,
    }


def _compact_partial(partial: dict | None) -> dict | None:
    """Keep only the explicit missing-input verdict of a partial row; it is not an evaluation."""
    if not partial:
        return None
    if "error" in partial:
        return {"is_evaluation": False, "error": partial["error"]}
    return {
        "is_evaluation": False,
        "entry_state": partial["entry_review"]["state"],
        "reasons": list(partial["entry_review"]["reasons"]),
        "price_status": partial["price_context"]["status"],
    }


def _membership(record: dict) -> dict:
    return {
        "asset_id": record.get("asset_id"),
        "market": record.get("market"),
        "memberships": sorted(m.get("membership_id") for m in record.get("memberships") or []),
        "investable_eligible": record.get("investable_eligible"),
        "universe_approved": record.get("universe_approved"),
    }


def build_symbol(ctx: dict, symbol: str) -> dict:
    record = ctx["population_records"][symbol]
    session = ctx["session_date"]
    formal = formal_candidate(symbol, ctx["stage_as_of"], ctx["latest_stage"], ctx["bounded_subjects"])
    watch = ctx["watchlist"].get(symbol)
    capture_row = ctx["capture"]["rows"].get(symbol)
    frame = ctx["capture"]["projection"].get(symbol)
    evidence_refs = []
    facts: dict = {"market_segment": capture_row.get("market_segment") if capture_row else None}
    if frame:
        facts.update({
            "session_close": frame.get("close"),
            "session_return_pct": frame.get("return_pct"),
            "session_trading_value": frame.get("trading_value"),
            "session_market_cap": frame.get("market_cap"),
            "session_source": "krx_information_system_all_stock_response",
        })
        evidence_refs.append({"role": "information_system_stock_response", "path": capture_row["response_path"],
                              "sha256": capture_row["response_sha256"], "session_date": session})

    # 1. watchlist file with the existing evaluator's full inputs
    if watch is not None:
        observed = watch["record"]
        evidence_refs.append({"role": "watchlist_observation", **watch["ref"]})
        latest = observed.get("latest_confirmed_row") or {}
        metrics = observed.get("confirmed_metrics") or {}
        net_value = latest.get("net_value") or {}
        session_ok = observed.get("latest_confirmed_day") == session and observed.get("status") == "ok" and latest.get("confirmed") is True
        has_close = latest.get("close") is not None
        has_sma20 = metrics.get("sma20") is not None
        has_flows = all(net_value.get(k) is not None for k in ("외국인합계", "기관합계", "개인"))
        missing = []
        if not session_ok:
            missing.append("SESSION_DATE_MISMATCH")
        if not has_close:
            missing.append("PRICE_SOURCE_NOT_RETAINED")
        if not has_sma20:
            missing.append("SMA20_NOT_COMPUTABLE:" + str(metrics.get("reason") or "SMA20_MISSING"))
        if not has_flows:
            missing.append("INVESTOR_FLOW_NOT_AVAILABLE")
        data_observation = {
            "status": "DATA_OBSERVED" if (has_close and session_ok) or frame else "DATA_NOT_OBSERVED",
            "session_date": observed.get("latest_confirmed_day"),
            "sources": ["watchlist_observation"] + (["information_system_stock_response"] if frame else []),
            "fields_present": [f for f, ok in (("confirmed_close", has_close), ("sma20", has_sma20), ("investor_flows", has_flows)) if ok],
            "fields_missing": [f for f, ok in (("confirmed_close", has_close), ("sma20", has_sma20), ("investor_flows", has_flows)) if not ok],
        }
        if not missing:
            evaluability = {"status": "EVALUABLE", "level": "EVALUABLE_PRICE_FLOW_SMA20", "reasons": [], "session_price_present": True}
            if symbol in ctx["bounded_rows"]:
                row = copy.deepcopy(ctx["bounded_rows"][symbol])
                evaluation = {"status": "EVALUATED_BOUNDED", "evaluator_contract": ctx["bounded"]["contract_version"],
                              "evaluated_at": ctx["bounded"]["generated_at"], "entry_state": row["entry_review"]["state"],
                              "reasons": list(row["entry_review"]["reasons"]), "row": row, "row_source": "bounded_review_packet"}
            else:
                try:
                    row = KOREA_REVIEW._symbol_row(symbol, observed, ctx["stage_as_of"], ctx["review_contract"], missing_policy=ctx["missing_policy"])
                    evaluation = {"status": "EVALUATED", "evaluator_contract": ctx["review_contract"]["contract_version"],
                                  "evaluated_at": ctx["snapshot_at"], "evaluated_at_basis": "INPUT_SNAPSHOT_TIME",
                                  "entry_state": row["entry_review"]["state"],
                                  "reasons": list(row["entry_review"]["reasons"]), "row": row, "row_source": "extracted_symbol_row"}
                except (KOREA_REVIEW.KoreaSymbolMarketReviewError, KeyError, TypeError, ValueError) as exc:
                    evaluability = {"status": "NOT_EVALUABLE", "level": None, "reasons": [f"ROW_BUILD_FAILED:{exc}"], "session_price_present": has_close}
                    evaluation = {"status": "NOT_EVALUATED", "entry_state": None, "reasons": [f"ROW_BUILD_FAILED:{exc}"], "row": None}
        else:
            evaluability = {"status": "NOT_EVALUABLE", "level": None, "reasons": missing, "session_price_present": bool(has_close and session_ok) or bool(frame)}
            partial = None
            if has_close and session_ok:
                try:
                    partial = KOREA_REVIEW._symbol_row(symbol, observed, ctx["stage_as_of"], ctx["review_contract"], missing_policy=ctx["missing_policy"])
                except (KOREA_REVIEW.KoreaSymbolMarketReviewError, KeyError, TypeError, ValueError) as exc:
                    partial = {"error": f"ROW_BUILD_FAILED:{exc}"}
            evaluation = {"status": "NOT_EVALUATED", "entry_state": None, "reasons": missing, "row": None,
                          "partial_review": _compact_partial(partial)}
        return symbol_row(symbol=symbol, name=observed.get("name") or record.get("display_name"), membership=_membership(record),
                          data_observation=data_observation, evaluability=evaluability, evaluation=evaluation, formal=formal,
                          facts=facts, evidence_refs=evidence_refs)

    # 2. price-history store bars for this session (private store, when configured)
    history = ctx["price_history"]
    bars = history["by_code"].get(symbol) or [] if history["status"] == "LOADED" else []
    if bars and bars[-1]["bas_dd"] == ctx["session_compact"]:
        store = history["store"]
        sma_sessions = history["sma_sessions"]
        sma20 = store.simple_moving_average(bars, sma_sessions)
        latest = bars[-1]
        facts.update({
            "session_close": latest.get("close"),
            "session_return_pct": latest.get("fluc_rt"),
            "session_trading_value": latest.get("value"),
            "session_market_cap": latest.get("mktcap"),
            "session_source": "price_history_session_store",
            "price_history_sessions": len(bars),
        })
        evidence_refs.append({
            "role": "price_history_session",
            "market": "KR",
            "bas_dd": latest["bas_dd"],
            "session_count": len(bars),
            "sma_sessions": sma_sessions,
        })
        observed = {
            "name": (capture_row or {}).get("name") or record.get("display_name"),
            "atlas_stage": formal["stage"],
            "latest_confirmed_day": session,
            "latest_confirmed_row": {
                "close": latest.get("close"),
                "change_pct": latest.get("fluc_rt"),
                "volume": latest.get("vol"),
                "confirmed": True,
                "net_value": {},
            },
            "confirmed_metrics": (
                {"sma20": format(sma20, "f"), "status": "COMPUTED",
                 "reason": f"PRICE_HISTORY_SESSIONS={len(bars)}"}
                if sma20 is not None else
                {"sma20": None, "status": "NOT_COMPUTABLE",
                 "reason": f"PRICE_HISTORY_SESSIONS={len(bars)}"}
            ),
        }
        try:
            row = KOREA_REVIEW._symbol_row(symbol, observed, ctx["stage_as_of"], ctx["review_contract"],
                                           missing_policy=ctx["missing_policy"])
            evaluation = {"status": "EVALUATED", "evaluator_contract": ctx["review_contract"]["contract_version"],
                          "evaluated_at": ctx["snapshot_at"], "evaluated_at_basis": "INPUT_SNAPSHOT_TIME",
                          "entry_state": row["entry_review"]["state"],
                          "reasons": list(row["entry_review"]["reasons"]), "row": row,
                          "row_source": "price_history_session_store"}
            # Bars are present, so the contract's existing EVALUABLE_PRICE level
            # applies -- the same level the US adapter uses for daily bars.
            # Investor flows are still absent and are never estimated, so the
            # row itself stays explicitly incomplete.
            evaluability = {"status": "EVALUABLE", "level": "EVALUABLE_PRICE", "reasons": [],
                            "session_price_present": True}
        except (KOREA_REVIEW.KoreaSymbolMarketReviewError, KeyError, TypeError, ValueError) as exc:
            evaluability = {"status": "NOT_EVALUABLE", "level": None, "reasons": [f"ROW_BUILD_FAILED:{exc}"],
                            "session_price_present": True}
            evaluation = {"status": "NOT_EVALUATED", "entry_state": None,
                          "reasons": [f"ROW_BUILD_FAILED:{exc}"], "row": None}
        return symbol_row(
            symbol=symbol, name=observed["name"], membership=_membership(record),
            data_observation={
                "status": "DATA_OBSERVED", "session_date": session,
                "sources": ["price_history_session_store"] + (["information_system_stock_response"] if frame else []),
                "fields_present": ["daily_bars", "session_close"] + (["sma20"] if sma20 is not None else []),
                "fields_missing": ([] if sma20 is not None else ["sma20"]) + ["investor_flows"],
            },
            evaluability=evaluability, evaluation=evaluation, formal=formal, facts=facts,
            evidence_refs=evidence_refs,
        )

    # 3. information-system session row only
    if frame:
        reasons = [f"SMA20_NOT_COMPUTABLE:RETAINED_SESSIONS={ctx['capture']['retained_sessions']}", "INVESTOR_FLOW_NOT_AVAILABLE"]
        observed = {
            "name": capture_row.get("name") or record.get("display_name"),
            "atlas_stage": formal["stage"],
            "latest_confirmed_day": session,
            "latest_confirmed_row": {"close": frame.get("close"), "change_pct": frame.get("return_pct"), "confirmed": True, "net_value": {}},
            "confirmed_metrics": {"sma20": None, "status": "NOT_COMPUTABLE", "reason": f"RETAINED_SESSIONS={ctx['capture']['retained_sessions']}"},
        }
        try:
            partial = KOREA_REVIEW._symbol_row(symbol, observed, ctx["stage_as_of"], ctx["review_contract"], missing_policy=ctx["missing_policy"])
        except (KOREA_REVIEW.KoreaSymbolMarketReviewError, KeyError, TypeError, ValueError) as exc:
            partial = {"error": f"ROW_BUILD_FAILED:{exc}"}
        return symbol_row(
            symbol=symbol, name=capture_row.get("name") or record.get("display_name"), membership=_membership(record),
            data_observation={"status": "DATA_OBSERVED", "session_date": session, "sources": ["information_system_stock_response"],
                              "fields_present": ["session_close", "session_return_pct", "session_trading_value", "session_market_cap"],
                              "fields_missing": ["sma20", "investor_flows"]},
            evaluability={"status": "NOT_EVALUABLE", "level": None, "reasons": reasons, "session_price_present": True},
            evaluation={"status": "NOT_EVALUATED", "entry_state": None, "reasons": reasons, "row": None,
                        "partial_review": _compact_partial(partial)},
            formal=formal, facts=facts, evidence_refs=evidence_refs,
        )

    # 4. nothing observed for this session
    reason = "SOURCE_ROW_MISSING" if ctx["capture"]["status"] == "VALIDATED" else "PRICE_SOURCE_NOT_RETAINED"
    return symbol_row(
        symbol=symbol, name=record.get("display_name"), membership=_membership(record),
        data_observation={"status": "DATA_NOT_OBSERVED", "session_date": session, "sources": [], "fields_present": [],
                          "fields_missing": ["confirmed_close", "sma20", "investor_flows"]},
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
        "watchlist_symbols_not_in_population": sorted(set(ctx["watchlist"]) - set(ctx["population_records"])),
        "orphan_source_rows": sorted(set(ctx["capture"]["rows"]) - set(ctx["population_records"])),
        "population_symbols_missing_from_session_response": (
            sorted(set(ctx["population_records"]) - set(ctx["capture"]["rows"])) if ctx["capture"]["status"] == "VALIDATED" else "NOT_AVAILABLE"
        ),
        "stage_history_symbols_not_in_population": sorted(
            s for s in ctx["latest_stage"] if s not in ctx["population_records"] and not s.isalpha()
        ),
    }
