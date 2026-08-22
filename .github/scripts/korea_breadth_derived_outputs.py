#!/usr/bin/env python3
"""P1-KR-05 shared-fetch derived output builder.

Reuses exactly the P1-KR-05 live fetch primitives from
.github/scripts/korea_breadth.py (build_request/_http_fetch/
_decode_payload/validate_snapshot/build_observation) -- not a second
fetch, not a copy of their logic -- to build two non-reconstructive
derived outputs in memory for the manual live-proof workflow:

  - a Korea Breadth observation packet per market/scope: no raw response
    body, no per-symbol identity or price, only source identity/SHA-256/
    fetched_at, shared/entered/exited/paired counts, and advance/
    decline/unchanged counts. available_at is always null and
    decision_eligible is always false -- this is a source-observation
    proof, not a decision input.
  - one P3-03 exact-date KOSPI/KOSDAQ source-coverage Global Master
    packet, built by universe/krx_global_universe.py's own
    build_packet() unchanged, from the "recent" scope's current-date
    responses already fetched above.

Both derived outputs are returned to the caller (written only under the
caller-supplied --out-dir, intended to be $RUNNER_TEMP) -- this module
never persists a raw response body, a per-symbol price, or any tracked
repository file.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
BREADTH_PACKET_SCHEMA_VERSION = "korea_breadth_observation/1"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


KOREA_BREADTH = _load_module("korea_breadth_for_derived", ".github/scripts/korea_breadth.py")
KRX_UNIVERSE = _load_module("krx_global_universe_for_derived", "universe/krx_global_universe.py")


class DerivedOutputError(ValueError):
    """Fail-closed P1-KR-05 shared-fetch derived-output violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def fetch_with_provenance(auth_key, bas_dd, market, opener=urlopen, contract=None):
    """Exactly one HTTP fetch -- reusing korea_breadth.py's own request/
    parse/validate chain -- plus the response SHA-256 and a UTC fetch
    timestamp captured at this same call. Never a second request for the
    same market/date."""
    contract = contract or KOREA_BREADTH.load_contract()
    market = KOREA_BREADTH.validate_market(market, contract)
    request = KOREA_BREADTH.build_request(auth_key, bas_dd, market, contract=contract)
    body = KOREA_BREADTH._http_fetch(request, opener=opener)
    payload = KOREA_BREADTH._decode_payload(body)
    validated = KOREA_BREADTH.validate_snapshot(payload, bas_dd, market, contract=contract)
    fetched_at_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        **validated,
        "response_sha256": hashlib.sha256(body).hexdigest(),
        "response_body_base64": base64.b64encode(body).decode("ascii"),
        "endpoint": contract["market_endpoints"][market],
        "fetched_at_utc": fetched_at_utc,
    }


def build_breadth_packet(previous, current, scope, contract=None) -> dict:
    """Wrap korea_breadth.py's own build_observation() (unchanged, reused
    directly) with source identity/SHA-256/fetched_at lineage and the
    explicit non-decision boundary. Carries no raw body and no per-symbol
    identity or price -- build_observation() already never returns
    those."""
    contract = contract or KOREA_BREADTH.load_contract()
    observation = KOREA_BREADTH.build_observation(previous, current, scope, contract=contract)
    packet = {
        "schema_version": BREADTH_PACKET_SCHEMA_VERSION,
        "scope": observation["scope"],
        "market": observation["market"],
        "previous_date": observation["previous_date"],
        "as_of_date": observation["as_of_date"],
        "request_identity": {
            "previous": {
                "endpoint": previous["endpoint"],
                "response_sha256": previous["response_sha256"],
            },
            "current": {
                "endpoint": current["endpoint"],
                "response_sha256": current["response_sha256"],
            },
        },
        "fetched_at_utc": {
            "previous": previous["fetched_at_utc"],
            "current": current["fetched_at_utc"],
        },
        "available_at": None,
        "decision_eligible": False,
        "universe": observation["universe"],
        "participation": observation["participation"],
        "breadth_classification_authorized": observation[
            "breadth_classification_authorized"
        ],
        "threshold_authorized": observation["threshold_authorized"],
        "regime_score_authorized": observation["regime_score_authorized"],
        "production_wiring_authorized": observation["production_wiring_authorized"],
        "trading_action_authorized": observation["trading_action_authorized"],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def build_p3_03_packet(
    market_results: dict, as_of_date_iso: str, master_id: str, contract=None
) -> dict:
    """Feed the already-fetched KOSPI/KOSDAQ responses (no second fetch)
    into universe/krx_global_universe.py's own build_packet() unchanged.
    market_results maps 'KOSPI'/'KOSDAQ' to a fetch_with_provenance()
    result for the same as_of_date."""
    expected_date = as_of_date_iso.replace("-", "")
    snapshots = []
    for market_key, result in market_results.items():
        if result["date"] != expected_date:
            raise DerivedOutputError(
                f"P3_03_DATE_MISMATCH:{market_key}:{result['date']}!={expected_date}"
            )
        snapshots.append(
            {
                "market": market_key,
                "response_body_base64": result["response_body_base64"],
                "source_identity": {
                    "source_id": "krx_open_api_stock_daily",
                    "source_url": f"{result['endpoint']}?basDd={expected_date}",
                    "source_sha256": result["response_sha256"],
                    "available_at": as_of_date_iso,
                    "retrieved_at_utc": result["fetched_at_utc"],
                },
            }
        )
    value = {
        "schema_version": KRX_UNIVERSE.INPUT_SCHEMA_VERSION,
        "master_id": master_id,
        "as_of_date": as_of_date_iso,
        "snapshots": snapshots,
    }
    return KRX_UNIVERSE.build_packet(value, contract)


def _iso_date(bas_dd: str) -> str:
    return f"{bas_dd[0:4]}-{bas_dd[4:6]}-{bas_dd[6:8]}"


def run_derived_outputs(
    auth_key: str,
    markets: tuple[str, ...],
    pairs: tuple[tuple[str, str, str], ...],
    out_dir: Path,
    opener=urlopen,
    contract=None,
) -> dict:
    """Run the same market/scope matrix P1-KR-05 already probes, sharing
    every fetch between the original PASS/FAIL summary, the Breadth
    observation packets, and the single P3-03 Global Master packet.
    Never re-fetches a market/date already fetched in this run."""
    contract = contract or KOREA_BREADTH.load_contract()
    out_dir = Path(out_dir)
    fetched: dict[tuple[str, str], dict] = {}
    summaries = []
    breadth_paths = []
    failed = 0

    for market in markets:
        normalized_market = KOREA_BREADTH.validate_market(market, contract)
        for scope, previous_date, current_date in pairs:
            try:
                previous = fetched.get((normalized_market, previous_date)) or fetch_with_provenance(
                    auth_key, previous_date, normalized_market, opener=opener, contract=contract
                )
                fetched[(normalized_market, previous_date)] = previous
                current = fetched.get((normalized_market, current_date)) or fetch_with_provenance(
                    auth_key, current_date, normalized_market, opener=opener, contract=contract
                )
                fetched[(normalized_market, current_date)] = current
                packet = build_breadth_packet(previous, current, scope, contract=contract)
            except KOREA_BREADTH.BreadthError as exc:
                failed += 1
                summaries.append(
                    f"status=FAILED scope={scope} market={normalized_market.upper()} "
                    f"previous_date={previous_date} as_of_date={current_date} error={exc}"
                )
                continue
            target = out_dir / f"korea-breadth-{scope}-{normalized_market}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            breadth_paths.append(target)
            summaries.append(
                f"status=PASS scope={scope} market={packet['market']} "
                f"previous_date={packet['previous_date']} as_of_date={packet['as_of_date']} "
                f"paired={packet['participation']['paired_count']} "
                f"advancing={packet['participation']['advancing_count']} "
                f"declining={packet['participation']['declining_count']}"
            )

    p3_03_path = None
    recent_pairs = [pair for pair in pairs if pair[0] == "recent"]
    if recent_pairs and set(markets) >= {"kospi", "kosdaq"}:
        _, _, recent_current_date = recent_pairs[0]
        market_results = {}
        missing_markets = []
        for market in ("kospi", "kosdaq"):
            result = fetched.get((market, recent_current_date))
            if result is not None:
                market_results[market.upper()] = result
            else:
                missing_markets.append(market.upper())
        if missing_markets:
            # A prior fetch failure for this market/date (already recorded
            # above as its own scope failure) means P3-03 cannot be built
            # from a shared fetch without re-requesting -- report this
            # explicitly rather than silently omitting the packet.
            failed += 1
            summaries.append(
                f"status=FAILED scope=p3_03 error=DEPENDENCY_UNAVAILABLE:{','.join(missing_markets)}"
            )
        else:
            try:
                p3_03_packet = build_p3_03_packet(
                    market_results,
                    _iso_date(recent_current_date),
                    f"P3.03.KRX.{recent_current_date}",
                    contract=None,
                )
            except KRX_UNIVERSE.KrxUniverseError as exc:
                failed += 1
                summaries.append(f"status=FAILED scope=p3_03 error={exc}")
            else:
                p3_03_path = out_dir / "p3-03-krx-global-universe.json"
                p3_03_path.parent.mkdir(parents=True, exist_ok=True)
                p3_03_path.write_text(
                    json.dumps(p3_03_packet, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
                summaries.append(
                    f"status=PASS scope=p3_03 as_of_date={p3_03_packet['as_of_date']} "
                    f"total_count={p3_03_packet['total_count']}"
                )

    return {
        "summaries": summaries,
        "breadth_paths": breadth_paths,
        "p3_03_path": p3_03_path,
        "failed_count": failed,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", action="append", choices=("kospi", "kosdaq"))
    parser.add_argument("--historical-previous", default="20100104")
    parser.add_argument("--historical-date", default="20100105")
    parser.add_argument("--recent-previous", required=True)
    parser.add_argument("--recent-date", required=True)
    parser.add_argument("--auth-env", default="KRX_API_KEY")
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    key = os.getenv(args.auth_env, "")
    markets = tuple(args.market or ["kospi", "kosdaq"])
    pairs = (
        ("historical", args.historical_previous, args.historical_date),
        ("recent", args.recent_previous, args.recent_date),
    )
    result = run_derived_outputs(key, markets, pairs, args.out_dir)
    for line in result["summaries"]:
        print(line)
    ok_count = len(result["summaries"]) - result["failed_count"]
    if result["failed_count"]:
        print(
            "P1_KR05_DERIVED_OUTPUTS=FAILED ok=%s failed=%s"
            % (ok_count, result["failed_count"])
        )
        return 2
    print("P1_KR05_DERIVED_OUTPUTS=PASS ok=%s failed=0" % ok_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
