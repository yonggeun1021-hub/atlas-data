#!/usr/bin/env python3
"""Real KRX Open API index data -> korea_leadership.py wiring attempt.

Reuses atlas_krx_r2_openapi_probe.py's own request-building/HTTP-fetch/
JSON-decode primitives UNCHANGED (build_request/_http_fetch/
_decode_payload) -- the same already-approved, already live-proven R2
KRX Open API index endpoint (idx/{krx,kospi,kosdaq}_dd_trd) this repo's
"R2 KRX Open API Live Proof" workflow already probes. Not a second/new
endpoint, not a re-fetch of anything another workflow already fetched
today (this endpoint has never been fetched for Leadership purposes
before).

Builds the real, two-date upstream_payload shape .github/scripts/
korea_leadership.py's build_transform() (UNCHANGED) requires, from every
real IDX_NM series common to both dates within one market response, and
attempts the transform. korea_leadership.py's own require_ratified()
gate is untouched: config/korea_leadership_policy.json is currently
UNRATIFIED (empty records), so this attempt correctly, honestly fails
closed with outcome=blocked -- this module does not invent a theme/
sector taxonomy or ratify anything to force a pass. Per-symbol/per-
index raw closing prices (CLSPRC_IDX) are used only in memory for the
transform attempt and are never written to any committed file -- only
the attempt's own outcome, real response SHA-256 lineage, and the
discovered official KRX index NAME catalog (names only, no prices) are
committed, as real evidence for a future CIO taxonomy ratification
decision.
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
import tempfile
from urllib.request import urlopen
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")


ROOT = Path(__file__).resolve().parents[2]
CONTEXT_ROOT = ROOT / "data" / "observations" / "korea_leadership_context"
SCHEMA_VERSION = "korea_leadership_live_attempt/1"
REQUIRED_MARKETS = ("kospi", "kosdaq")


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PROBE = _load_module("atlas_krx_r2_openapi_probe_for_leadership", "atlas_krx_r2_openapi_probe.py")
LEADERSHIP = _load_module("korea_leadership_for_live_fetch", ".github/scripts/korea_leadership.py")


class LeadershipLiveFetchError(ValueError):
    """Fail-closed Korea Leadership live-fetch wiring violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _now_utc_iso() -> str:
    """Isolated so tests can pin a single fixed instant across every
    fetch_index_family() call in one run() -- four real dt.datetime.now()
    calls (2 markets x 2 dates) each independently truncated to whole
    seconds can otherwise straddle a real second boundary under a slow/
    loaded runner, making an "immediate rerun" idempotency check flake
    on generated_at (= max of these) even though nothing substantive
    changed."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_index_family(auth_key: str, bas_dd: str, market: str, opener=urlopen) -> dict:
    """One real HTTP fetch via the unchanged R2 probe primitives. Returns
    every real row from OutBlock_1 (IDX_NM/CLSPRC_IDX/BAS_DD), the raw
    response's own SHA-256, and the UTC instant it was actually fetched
    -- the raw bytes themselves are never returned/retained beyond this
    call's own local scope."""
    request = PROBE.build_request(auth_key, bas_dd, market=market)
    body = PROBE._http_fetch(request, opener=opener)
    payload = PROBE._decode_payload(body)
    rows = payload.get("OutBlock_1")
    if not isinstance(rows, list) or not rows:
        raise LeadershipLiveFetchError(f"KRX_RESPONSE_EMPTY:{market}:{bas_dd}")
    parsed = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("IDX_NM")
        close = row.get("CLSPRC_IDX")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(close, str) or not close.strip():
            continue
        parsed[name.strip()] = close.strip()
    fetched_at_utc = _now_utc_iso()
    return {
        "market": market,
        "bas_dd": bas_dd,
        "response_sha256": hashlib.sha256(body).hexdigest(),
        "fetched_at_utc": fetched_at_utc,
        "index_names_to_close": parsed,
    }


def _iso_date(bas_dd: str) -> str:
    return f"{bas_dd[0:4]}-{bas_dd[4:6]}-{bas_dd[6:8]}"


def _to_kst_iso(utc_iso_z: str) -> str:
    """korea_leadership.py's own parse_timestamp() requires an ISO
    timestamp whose utcoffset() is exactly +09:00 (KST) -- a bare UTC
    "Z" instant (what the real fetch instant is recorded as) fails that
    check. Converts the SAME real instant into its KST representation;
    no wall-clock re-sampling, no fabricated time."""
    parsed = dt.datetime.fromisoformat(utc_iso_z.replace("Z", "+00:00"))
    return parsed.astimezone(KST).isoformat()


def qualified_identity(market: str, index_name: str) -> str:
    """Canonical identity is index code/market/source lineage, never a
    bare name string -- KRX's idx/{kospi,kosdaq}_dd_trd rows carry no
    separate numeric code, so the official IDX_NM is qualified with its
    exact source market (the real disambiguator: KOSPI's own "IT 서비스"
    sector index and KOSDAQ's own "IT 서비스" sector index are two
    distinct real indices that happen to share a name -- a bare name
    would silently collide them)."""
    return f"{market.upper()}::{index_name}"


def ratified_identities_for(policy: dict, observation_date) -> set[str]:
    """Every series_identity the ratified policy actually requires for
    observation_date, across every market -- korea_leadership.py's own
    build_transform() validates ONE combined KOREA-wide payload per call
    (KOSPI and KOSDAQ series together, each within its own benchmark
    scope), never a market at a time, so the required set is never
    market-filtered here."""
    return {
        record["series_identity"]
        for record in policy["records"]
        if LEADERSHIP.active_record(
            policy["records"], record["series_identity"], observation_date
        )
        is record
    }


def build_combined_upstream_payload(
    per_market: dict, policy: dict, *, run_mode: str = "FORWARD_SHADOW",
) -> tuple[dict, dict]:
    """Real series_rows spanning EVERY market's ratified series_identity
    present with a real close on both dates, combined into the single
    payload korea_leadership.build_transform() actually expects (its own
    PIT taxonomy coverage check spans the whole ratified policy, not one
    market at a time). Only the ratified policy's own required set is
    ever included -- an index this repo fetched but the policy never
    ratified is dropped here, not smuggled into the transform attempt.
    Coverage gaps (a ratified series missing from either date's real
    response) are left for build_transform()'s own fail-closed
    PIT_TAXONOMY_COVERAGE_MISMATCH check, never silently patched here.
    No price value is retained past this in-memory construction; the
    caller only persists the attempt's outcome and per-market name
    catalog. Returns (payload, {market: common_names})."""
    import datetime as _dt

    current_dates = {data["current"]["bas_dd"] for data in per_market.values()}
    prior_dates = {data["prior"]["bas_dd"] for data in per_market.values()}
    if len(current_dates) != 1 or len(prior_dates) != 1:
        raise LeadershipLiveFetchError("MARKET_DATE_MISMATCH")
    (current_bas_dd,) = current_dates
    (prior_bas_dd,) = prior_dates
    observation_date = _dt.date.fromisoformat(_iso_date(current_bas_dd))
    required = ratified_identities_for(policy, observation_date)

    common_by_market = {}
    series_rows = []
    latest_current_fetch = None
    for market, data in per_market.items():
        prior, current = data["prior"], data["current"]
        common_names = sorted(
            set(prior["index_names_to_close"]) & set(current["index_names_to_close"])
        )
        common_by_market[market] = common_names
        if latest_current_fetch is None or current["fetched_at_utc"] > latest_current_fetch:
            latest_current_fetch = current["fetched_at_utc"]
        for name in common_names:
            identity = qualified_identity(market, name)
            if identity not in required:
                continue
            series_rows.append({
                "series_identity": identity,
                "rows": [
                    {"session_date": _iso_date(prior_bas_dd), "close": prior["index_names_to_close"][name]},
                    {"session_date": _iso_date(current_bas_dd), "close": current["index_names_to_close"][name]},
                ],
            })
    if not series_rows:
        raise LeadershipLiveFetchError("NO_RATIFIED_COMMON_INDEX_NAMES")
    series_rows.sort(key=lambda row: row["series_identity"])
    payload = {
        "schema_version": 1,
        "source_name": "KRX_OPEN_API_INDEX_LIVE",
        "market": "KOREA",
        "market_timezone": "Asia/Seoul",
        "run_mode": run_mode,
        "observation_date": observation_date.isoformat(),
        "fetched_at": _to_kst_iso(latest_current_fetch),
        "available_at": _to_kst_iso(latest_current_fetch),
        "decision_at": _to_kst_iso(latest_current_fetch),
        "expected_session_dates": [_iso_date(prior_bas_dd), observation_date.isoformat()],
        "series_rows": series_rows,
    }
    return payload, common_by_market


def attempt_leadership_transform(payload: dict, policy_path=None) -> dict:
    """Calls korea_leadership.build_transform() UNCHANGED -- including its
    own re-read of the policy from disk (it takes a path, not a dict).
    policy_path defaults to the real committed file; tests pass their own
    temp-file path so they never depend on whatever the real file's
    effective_from happens to be today. A genuinely UNRATIFIED (or not-
    yet-effective) policy fails closed here -- caught and reported as a
    clean outcome=blocked, never a job failure and never a fabricated
    pass."""
    kwargs = {} if policy_path is None else {"policy_path": policy_path}
    try:
        packet = LEADERSHIP.build_transform(payload, **kwargs)
    except LEADERSHIP.KoreaLeadershipError as exc:
        return {"outcome": "blocked", "reason": str(exc), "packet": None}
    return {"outcome": "populated", "reason": None, "packet": packet}


def write_json_atomic(path: Path, value: dict) -> None:
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


def output_path_for(as_of_date: str) -> Path:
    return CONTEXT_ROOT / as_of_date / "packet.json"


def verify_existing_observation(prior_date: str, current_date: str) -> dict:
    """Revalidate a committed same-date observation without any provider call.

    A workflow rerun must reuse the first committed evidence bytes.  This
    verifier independently checks the binding fields and both retained payload
    hashes before the workflow is allowed to skip KRX.
    """
    expected_prior = _iso_date(prior_date)
    expected_current = _iso_date(current_date)
    path = output_path_for(expected_current)
    if not path.is_file():
        raise LeadershipLiveFetchError("EXISTING_PACKET_NOT_FOUND")
    try:
        packet = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LeadershipLiveFetchError("EXISTING_PACKET_INVALID_JSON") from exc
    if not isinstance(packet, dict):
        raise LeadershipLiveFetchError("EXISTING_PACKET_NOT_OBJECT")
    if packet.get("schema_version") != SCHEMA_VERSION:
        raise LeadershipLiveFetchError("EXISTING_PACKET_SCHEMA_MISMATCH")
    if packet.get("observation_date") != expected_current:
        raise LeadershipLiveFetchError("EXISTING_PACKET_OBSERVATION_DATE_MISMATCH")
    if packet.get("prior_date") != expected_prior:
        raise LeadershipLiveFetchError("EXISTING_PACKET_PRIOR_DATE_MISMATCH")
    markets = packet.get("markets")
    if not isinstance(markets, dict) or set(markets) != {m.upper() for m in REQUIRED_MARKETS}:
        raise LeadershipLiveFetchError("EXISTING_PACKET_MARKETS_MISMATCH")
    claimed = packet.get("payload_sha256")
    unsigned = dict(packet)
    unsigned.pop("payload_sha256", None)
    if not isinstance(claimed, str) or claimed != payload_sha256(unsigned):
        raise LeadershipLiveFetchError("EXISTING_PACKET_HASH_MISMATCH")
    leadership_packet = packet.get("leadership_packet")
    leadership_claimed = packet.get("leadership_packet_sha256")
    if leadership_packet is None:
        if leadership_claimed is not None:
            raise LeadershipLiveFetchError("EXISTING_LEADERSHIP_PACKET_HASH_MISMATCH")
    else:
        if not isinstance(leadership_packet, dict):
            raise LeadershipLiveFetchError("EXISTING_LEADERSHIP_PACKET_NOT_OBJECT")
        inner_claimed = leadership_packet.get("payload_sha256")
        inner_unsigned = dict(leadership_packet)
        inner_unsigned.pop("payload_sha256", None)
        if (
            not isinstance(inner_claimed, str)
            or inner_claimed != LEADERSHIP.canonical_payload_sha256(inner_unsigned)
            or leadership_claimed != inner_claimed
        ):
            raise LeadershipLiveFetchError("EXISTING_LEADERSHIP_PACKET_HASH_MISMATCH")
    return packet


def run(
    auth_key: str, prior_date: str, current_date: str, *, opener=urlopen, policy_path=None
) -> dict:
    # policy is loaded from the SAME path attempt_leadership_transform()
    # will have korea_leadership.build_transform() re-read -- filtering
    # (here) and validation (there) must never see two different
    # policies for the same run.
    resolved_path = policy_path if policy_path is not None else LEADERSHIP.POLICY_PATH
    policy = LEADERSHIP.load_policy(resolved_path)
    per_market_fetch = {}
    market_evidence = {}
    for market in REQUIRED_MARKETS:
        prior = fetch_index_family(auth_key, prior_date, market, opener=opener)
        current = fetch_index_family(auth_key, current_date, market, opener=opener)
        per_market_fetch[market] = {"prior": prior, "current": current}
        market_evidence[market.upper()] = {
            "raw_response_sha256": {"prior": prior["response_sha256"], "current": current["response_sha256"]},
        }
    # ONE combined payload: korea_leadership.build_transform()'s own PIT
    # taxonomy coverage check spans the whole ratified policy across
    # every market in a single call, never a market at a time (each
    # market's series still only ranks within its own benchmark scope
    # downstream in korea_capital_rotation.py -- this is purely about
    # what one build_transform() call requires as input).
    payload, common_by_market = build_combined_upstream_payload(per_market_fetch, policy)
    attempt = attempt_leadership_transform(payload, policy_path=resolved_path)
    for market in REQUIRED_MARKETS:
        common_names = common_by_market.get(market, [])
        ratified_count = sum(
            1 for row in payload["series_rows"]
            if row["series_identity"].startswith(f"{market.upper()}::")
        )
        market_evidence[market.upper()].update({
            "common_index_name_count": len(common_names),
            "ratified_series_count": ratified_count,
            "discovered_index_names": common_names,
        })
    observation_date = _iso_date(current_date)
    # generated_at is derived from the real fetch timestamps recorded
    # during this run, never wall-clock, for byte-identical
    # reproducibility on a rerun against the same source.
    fetched_at_values = [
        per_market_fetch[m]["current"]["fetched_at_utc"] for m in REQUIRED_MARKETS
    ]
    summary = {
        "schema_version": SCHEMA_VERSION,
        "observation_date": observation_date,
        "prior_date": _iso_date(prior_date),
        "outcome": attempt["outcome"],
        "reason": attempt["reason"],
        "leadership_packet_sha256": (
            attempt["packet"]["payload_sha256"] if attempt["packet"] else None
        ),
        # The full Leadership packet itself is safe to persist here --
        # korea_leadership.py's own output_retention_policy already
        # guarantees it is non_reconstructive_derived_observations_only
        # (relative_strength_observations only, never a raw index
        # close). This is what korea_capital_rotation.py's
        # prior_observation/current_observation actually need as input,
        # not just a hash of it.
        "leadership_packet": attempt["packet"],
        "markets": market_evidence,
        "generated_at": max(fetched_at_values),
    }
    summary["payload_sha256"] = payload_sha256(summary)
    return summary


def populate(
    auth_key: str, prior_date: str, current_date: str, *, opener=urlopen, policy_path=None
) -> dict:
    summary = run(auth_key, prior_date, current_date, opener=opener, policy_path=policy_path)
    path = output_path_for(summary["observation_date"])
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing == summary:
            return {"outcome": "verified_existing", "path": str(path), "payload_sha256": summary["payload_sha256"]}
        raise LeadershipLiveFetchError("EXISTING_PACKET_DRIFT_OR_TAMPER")
    write_json_atomic(path, summary)
    return {"outcome": "populated", "path": str(path), "payload_sha256": summary["payload_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-date", required=True, help="YYYYMMDD")
    parser.add_argument("--current-date", required=True, help="YYYYMMDD")
    parser.add_argument("--auth-env", default="KRX_API_KEY")
    parser.add_argument(
        "--verify-existing-only",
        action="store_true",
        help="validate and reuse a committed same-date packet without KRX access",
    )
    args = parser.parse_args()
    if args.verify_existing_only:
        try:
            packet = verify_existing_observation(args.prior_date, args.current_date)
        except LeadershipLiveFetchError as exc:
            print(f"korea leadership existing evidence verification failed reason={exc}")
            return 1
        print(
            "korea leadership existing evidence verified "
            f"path={output_path_for(packet['observation_date'])}"
        )
        return 0
    key = os.getenv(args.auth_env, "")
    if not key:
        print("STOP: KRX_API_KEY secret missing")
        return 2
    try:
        result = populate(key, args.prior_date, args.current_date)
    except LeadershipLiveFetchError as exc:
        print(f"korea leadership live fetch failed reason={exc}")
        return 1
    print(f"korea leadership live fetch outcome={result['outcome']} path={result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
