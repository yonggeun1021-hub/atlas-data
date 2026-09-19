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
    decline/unchanged counts. source_available_at is always null (KRX
    gives no verified official publication timing); captured_at/
    first_seen_at are the real fetch instant -- this is a source-
    observation proof, not a decision input. Real decision eligibility
    (confirmed-history, never same-day) is derived downstream, from
    these raw facts only, by rotation/korea_capital_rotation_ledger_
    wire.py -- never decided here.
  - one P3-03 exact-date KOSPI/KOSDAQ source-coverage Global Master
    packet, built by universe/krx_global_universe.py's own
    build_packet() unchanged, from the "recent" scope's current-date
    responses already fetched above.
  - one metadata-only request receipt recording, per unique market/date,
    what was actually requested and what actually came back BEFORE
    decode/validate: the real requested basDd, the contract endpoint
    (no query, header or credential), attempt/outcome, the real capture
    instant, the observed HTTP status, the response SHA-256/byte count,
    the safe parsed block shape, the exact contract error code, and the
    dependent dates explicitly NOT_ATTEMPTED because an earlier date in
    the same pair failed. It records observed facts only -- it never
    attributes a failure to publication timing, entitlement, or any
    other cause the response does not actually show.

Both derived outputs are returned to the caller (written only under the
caller-supplied --out-dir, intended to be $RUNNER_TEMP) -- this module
never persists a raw response body, a per-symbol price, or any tracked
repository file.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
BREADTH_PACKET_SCHEMA_VERSION = "korea_breadth_observation/1"
REQUEST_RECEIPT_SCHEMA_VERSION = "korea_breadth_request_receipt/1"
REQUEST_RECEIPT_NAME = "korea-breadth-request-receipt.json"
# KRX exposes no verified provider status or official publication
# instant, so both stay UNKNOWN/null instead of being inferred.
PROVIDER_STATUS_UNOBSERVABLE = "UNKNOWN"
SAFE_ERROR_CODE_RE = re.compile(r"^[A-Z0-9_]{1,64}$")


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


def safe_error_code(exc) -> str:
    """The exact contract error code, or a fixed placeholder.

    korea_breadth.py only ever raises constructed uppercase codes, so the
    code itself is safe to persist. Only the leading code segment is kept,
    and anything that is not a plain uppercase token -- a provider
    message or an arbitrary exception string that could carry a secret --
    is replaced rather than recorded.
    """
    code = str(exc).strip().split(":", 1)[0].strip()
    if SAFE_ERROR_CODE_RE.fullmatch(code):
        return code
    return "CONTRACT_ERROR_CODE_UNSAFE"


def attempt_fetch(auth_key, bas_dd, market, opener=urlopen, contract=None) -> dict:
    """Exactly one HTTP request for this market/date, keeping the response
    evidence captured BEFORE decode/validate.

    Returns {"receipt": metadata-only record, "snapshot": validated
    lineage or None}. A failure no longer discards the actually requested
    date, the observed HTTP status, the response digest or the response
    shape -- that loss is what made a RESPONSE_ZERO_ROWS undiagnosable.
    This never raises a contract failure and never retries; the caller
    decides what a failed attempt means.
    """
    contract = contract or KOREA_BREADTH.load_contract()
    evidence = KOREA_BREADTH.new_response_evidence(contract)
    receipt = {
        "market": None,
        "requested_bas_dd": bas_dd if isinstance(bas_dd, str) else None,
        "endpoint": None,
        "attempt": "NOT_ATTEMPTED",
        "outcome": "FAILED",
        "error_code": None,
        "not_attempted_reason": None,
        "blocked_by": None,
        "provider_status": PROVIDER_STATUS_UNOBSERVABLE,
        "source_available_at": None,
    }
    snapshot = None
    try:
        normalized = KOREA_BREADTH.validate_market(market, contract)
        receipt["market"] = normalized.upper()
        receipt["endpoint"] = contract["market_endpoints"][normalized]
        request = KOREA_BREADTH.build_request(
            auth_key, bas_dd, normalized, contract=contract
        )
        # Past this point one real HTTP request is issued for this
        # market/date, so the attempt is genuinely ATTEMPTED.
        receipt["attempt"] = "ATTEMPTED"
        body = KOREA_BREADTH._http_fetch(request, opener=opener, evidence=evidence)
        payload = KOREA_BREADTH._decode_payload(body)
        validated = KOREA_BREADTH.validate_snapshot(
            payload, bas_dd, normalized, contract=contract
        )
    except KOREA_BREADTH.BreadthError as exc:
        receipt["error_code"] = safe_error_code(exc)
        if receipt["attempt"] == "NOT_ATTEMPTED":
            receipt["not_attempted_reason"] = "REQUEST_NOT_CONSTRUCTED"
    else:
        receipt["outcome"] = "SUCCESS"
        snapshot = {
            **validated,
            "response_sha256": evidence["response_sha256"],
            "response_body_base64": base64.b64encode(body).decode("ascii"),
            "endpoint": receipt["endpoint"],
            "fetched_at_utc": evidence["captured_at"],
        }
    receipt.update(
        {
            "captured_at": evidence["captured_at"],
            "http_status": evidence["http_status"],
            "response_sha256": evidence["response_sha256"],
            "response_byte_count": evidence["response_byte_count"],
            "response_shape": evidence["response_shape"],
        }
    )
    return {"receipt": receipt, "snapshot": snapshot}


def not_attempted_receipt(market, bas_dd, blocked_by_date, blocked_by_error) -> dict:
    """A dependent date that was deliberately never requested because an
    earlier date in the same pair failed. Recorded explicitly so a skipped
    date is never mistaken for an attempted one."""
    return {
        "market": market.upper(),
        "requested_bas_dd": None,
        "dependent_bas_dd": bas_dd,
        "endpoint": None,
        "attempt": "NOT_ATTEMPTED",
        "outcome": "NOT_ATTEMPTED",
        "error_code": None,
        "not_attempted_reason": "PREVIOUS_DATE_FAILED",
        "blocked_by": {"bas_dd": blocked_by_date, "error_code": blocked_by_error},
        "provider_status": PROVIDER_STATUS_UNOBSERVABLE,
        "source_available_at": None,
        "captured_at": None,
        "http_status": None,
        "response_sha256": None,
        "response_byte_count": None,
        "response_shape": None,
    }


def fetch_with_provenance(auth_key, bas_dd, market, opener=urlopen, contract=None):
    """Exactly one HTTP fetch -- reusing korea_breadth.py's own request/
    parse/validate chain -- plus the response SHA-256 and the real UTC
    capture instant of that same request. Never a second request for the
    same market/date. Still fail-closed with the exact contract error."""
    attempt = attempt_fetch(auth_key, bas_dd, market, opener=opener, contract=contract)
    if attempt["snapshot"] is None:
        raise KOREA_BREADTH.BreadthError(attempt["receipt"]["error_code"])
    return attempt["snapshot"]


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
        # Time-lineage triad (P1-KR-05 first-seen policy, see
        # korea_breadth_context_populate.py/korea_capital_rotation_
        # ledger_wire.py docstrings for the full CIO rule): only what can
        # actually be proven is recorded, never a fabricated stand-in.
        # source_available_at stays null -- KRX gives no verified official
        # publication timing, an honest, unchanged gap. captured_at is the
        # real instant this script's own request actually completed.
        # first_seen_at = captured_at is legitimate for a genuine forward
        # live capture (this fetch is the first and only time this data
        # was ever observed) -- never used to backdate an already-known
        # historical value as if it were contemporaneous.
        "source_available_at": None,
        "captured_at": current["fetched_at_utc"],
        "first_seen_at": current["fetched_at_utc"],
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


def _evidence_suffix(receipt: dict) -> str:
    """Safe, bounded failure evidence for a printed summary line: status,
    digest, size and block shape only -- never a body, header, key, query
    or row value."""
    shape = receipt.get("response_shape") or {}
    fields = {
        "failing_bas_dd": receipt.get("requested_bas_dd"),
        "attempt": receipt.get("attempt"),
        "http_status": receipt.get("http_status"),
        "response_sha256": receipt.get("response_sha256"),
        "response_byte_count": receipt.get("response_byte_count"),
        "expected_block_present": shape.get("expected_block_present"),
        "expected_block_row_count": shape.get("expected_block_row_count"),
        "provider_status": receipt.get("provider_status"),
    }
    return " ".join("%s=%s" % item for item in fields.items())


def _write_request_receipt(out_dir: Path, receipt: dict) -> Path:
    """Additive, metadata-only receipt written under the caller's out_dir
    so the existing upload-artifact step preserves the failure evidence
    even when main() returns non-zero."""
    receipt["payload_sha256"] = payload_sha256(receipt)
    target = Path(out_dir) / REQUEST_RECEIPT_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


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
    Never re-fetches a market/date already fetched in this run -- a failed
    attempt is cached exactly like a successful one, so a repeated date
    across supplied pairs is never re-requested and never retried."""
    contract = contract or KOREA_BREADTH.load_contract()
    out_dir = Path(out_dir)
    attempts: dict[tuple[str, str], dict] = {}
    request_receipts = []
    not_attempted = []
    scope_receipts = []
    summaries = []
    notices = []
    breadth_paths = []
    failed = 0

    def attempt(normalized_market, bas_dd):
        key = (normalized_market, bas_dd)
        if key not in attempts:
            record = attempt_fetch(
                auth_key, bas_dd, normalized_market, opener=opener, contract=contract
            )
            attempts[key] = record
            request_receipts.append(record["receipt"])
        return attempts[key]

    def fail_scope(scope, normalized_market, previous_date, current_date, receipt, stage):
        nonlocal failed
        failed += 1
        error_code = receipt.get("error_code")
        scope_receipts.append(
            {
                "scope": scope,
                "market": normalized_market.upper(),
                "previous_date": previous_date,
                "as_of_date": current_date,
                "status": "FAILED",
                "failing_stage": stage,
                "failing_bas_dd": receipt.get("requested_bas_dd"),
                "error_code": error_code,
            }
        )
        summaries.append(
            f"status=FAILED scope={scope} market={normalized_market.upper()} "
            f"previous_date={previous_date} as_of_date={current_date} "
            f"error={error_code} failing_stage={stage} {_evidence_suffix(receipt)}"
        )

    for market in markets:
        normalized_market = KOREA_BREADTH.validate_market(market, contract)
        for scope, previous_date, current_date in pairs:
            previous_attempt = attempt(normalized_market, previous_date)
            if previous_attempt["snapshot"] is None:
                # The current date is deliberately NOT requested after a
                # failed previous date -- one original request per unique
                # market/date, no retry. Record that skip explicitly
                # instead of leaving the current date silently unexplained.
                if (normalized_market, current_date) not in attempts:
                    skip = not_attempted_receipt(
                        normalized_market,
                        current_date,
                        previous_date,
                        previous_attempt["receipt"].get("error_code"),
                    )
                    skip["scope"] = scope
                    not_attempted.append(skip)
                    notices.append(
                        f"status=NOT_ATTEMPTED scope={scope} "
                        f"market={normalized_market.upper()} "
                        f"dependent_bas_dd={current_date} "
                        f"blocked_by_bas_dd={previous_date} "
                        f"blocked_by_error={previous_attempt['receipt'].get('error_code')}"
                    )
                fail_scope(
                    scope,
                    normalized_market,
                    previous_date,
                    current_date,
                    previous_attempt["receipt"],
                    "previous_date_fetch",
                )
                continue

            current_attempt = attempt(normalized_market, current_date)
            if current_attempt["snapshot"] is None:
                fail_scope(
                    scope,
                    normalized_market,
                    previous_date,
                    current_date,
                    current_attempt["receipt"],
                    "current_date_fetch",
                )
                continue

            try:
                packet = build_breadth_packet(
                    previous_attempt["snapshot"],
                    current_attempt["snapshot"],
                    scope,
                    contract=contract,
                )
            except KOREA_BREADTH.BreadthError as exc:
                fail_scope(
                    scope,
                    normalized_market,
                    previous_date,
                    current_date,
                    {
                        "error_code": safe_error_code(exc),
                        "provider_status": PROVIDER_STATUS_UNOBSERVABLE,
                    },
                    "observation",
                )
                continue
            scope_receipts.append(
                {
                    "scope": scope,
                    "market": normalized_market.upper(),
                    "previous_date": previous_date,
                    "as_of_date": current_date,
                    "status": "PASS",
                    "failing_stage": None,
                    "failing_bas_dd": None,
                    "error_code": None,
                }
            )
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
    p3_03_receipt = None
    recent_pairs = [pair for pair in pairs if pair[0] == "recent"]
    if recent_pairs and set(markets) >= {"kospi", "kosdaq"}:
        _, _, recent_current_date = recent_pairs[0]
        market_results = {}
        missing_markets = []
        for market in ("kospi", "kosdaq"):
            record = attempts.get((market, recent_current_date))
            if record is not None and record["snapshot"] is not None:
                market_results[market.upper()] = record["snapshot"]
            else:
                missing_markets.append(market.upper())
        if missing_markets:
            # A prior fetch failure (or a never-attempted dependent date)
            # for this market/date -- already recorded above as its own
            # scope failure and request receipt -- means P3-03 cannot be
            # built from a shared fetch without re-requesting. Report this
            # explicitly rather than silently omitting the packet.
            failed += 1
            p3_03_receipt = {
                "status": "FAILED",
                "as_of_date": recent_current_date,
                "error_code": "DEPENDENCY_UNAVAILABLE",
                "missing_markets": missing_markets,
            }
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
            except (KRX_UNIVERSE.KrxUniverseError, DerivedOutputError) as exc:
                failed += 1
                p3_03_receipt = {
                    "status": "FAILED",
                    "as_of_date": recent_current_date,
                    "error_code": safe_error_code(exc),
                    "missing_markets": [],
                }
                summaries.append(f"status=FAILED scope=p3_03 error={exc}")
            else:
                p3_03_path = out_dir / "p3-03-krx-global-universe.json"
                p3_03_path.parent.mkdir(parents=True, exist_ok=True)
                p3_03_path.write_text(
                    json.dumps(p3_03_packet, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
                p3_03_receipt = {
                    "status": "PASS",
                    "as_of_date": recent_current_date,
                    "error_code": None,
                    "missing_markets": [],
                }
                summaries.append(
                    f"status=PASS scope=p3_03 as_of_date={p3_03_packet['as_of_date']} "
                    f"total_count={p3_03_packet['total_count']}"
                )

    receipt_path = _write_request_receipt(
        out_dir,
        {
            "schema_version": REQUEST_RECEIPT_SCHEMA_VERSION,
            "generated_at": KOREA_BREADTH.utc_timestamp(),
            # The receipt is bounded metadata only: no body, no base64, no
            # header, no auth key, no query string, no per-symbol identity
            # and no price row. raw_persistence stays closed.
            "raw_persistence": contract["raw_persistence"],
            "requests": request_receipts,
            "not_attempted": not_attempted,
            "scopes": scope_receipts,
            "p3_03": p3_03_receipt,
            "failed_count": failed,
        },
    )

    return {
        "summaries": summaries,
        "notices": notices,
        "breadth_paths": breadth_paths,
        "p3_03_path": p3_03_path,
        "request_receipt_path": receipt_path,
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
    for line in result["notices"]:
        print(line)
    ok_count = len(result["summaries"]) - result["failed_count"]
    if result["failed_count"]:
        # The metadata-only request receipt is already written under
        # --out-dir, so the artifact upload preserves the real per-date
        # request evidence even on this non-zero exit.
        print("request_receipt=%s" % result["request_receipt_path"].name)
        print(
            "P1_KR05_DERIVED_OUTPUTS=FAILED ok=%s failed=%s"
            % (ok_count, result["failed_count"])
        )
        return 2
    print("P1_KR05_DERIVED_OUTPUTS=PASS ok=%s failed=0" % ok_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
