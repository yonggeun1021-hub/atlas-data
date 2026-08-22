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
    fetched_at_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "market": market,
        "bas_dd": bas_dd,
        "response_sha256": hashlib.sha256(body).hexdigest(),
        "fetched_at_utc": fetched_at_utc,
        "index_names_to_close": parsed,
    }


def _iso_date(bas_dd: str) -> str:
    return f"{bas_dd[0:4]}-{bas_dd[4:6]}-{bas_dd[6:8]}"


def build_upstream_payload(
    market: str, prior: dict, current: dict, *, run_mode: str = "FORWARD_SHADOW"
) -> dict:
    """Real series_rows for every index name present with a real close on
    BOTH dates -- absent on either date means excluded, never guessed.
    No price value is retained past this in-memory construction; the
    caller only persists the attempt's outcome and name catalog."""
    common_names = sorted(
        set(prior["index_names_to_close"]) & set(current["index_names_to_close"])
    )
    if not common_names:
        raise LeadershipLiveFetchError(f"NO_COMMON_INDEX_NAMES:{market}")
    series_rows = [
        {
            "series_identity": name,
            "rows": [
                {"session_date": _iso_date(prior["bas_dd"]), "close": prior["index_names_to_close"][name]},
                {"session_date": _iso_date(current["bas_dd"]), "close": current["index_names_to_close"][name]},
            ],
        }
        for name in common_names
    ]
    observation_date = _iso_date(current["bas_dd"])
    return {
        "schema_version": 1,
        "source_name": "KRX_OPEN_API_INDEX_LIVE",
        "market": "KOREA",
        "market_timezone": "Asia/Seoul",
        "run_mode": run_mode,
        "observation_date": observation_date,
        "fetched_at": current["fetched_at_utc"],
        "available_at": current["fetched_at_utc"],
        "decision_at": current["fetched_at_utc"],
        "expected_session_dates": [_iso_date(prior["bas_dd"]), observation_date],
        "series_rows": series_rows,
    }, common_names


def attempt_leadership_transform(payload: dict) -> dict:
    """Calls korea_leadership.build_transform() UNCHANGED. A genuinely
    UNRATIFIED policy (today's real state) fails closed here -- caught
    and reported as a clean outcome=blocked, never a job failure and
    never a fabricated pass."""
    try:
        packet = LEADERSHIP.build_transform(payload)
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


def run(
    auth_key: str, prior_date: str, current_date: str, *, opener=urlopen
) -> dict:
    per_market = {}
    fetched_at_values = []
    for market in REQUIRED_MARKETS:
        prior = fetch_index_family(auth_key, prior_date, market, opener=opener)
        current = fetch_index_family(auth_key, current_date, market, opener=opener)
        fetched_at_values.append(current["fetched_at_utc"])
        payload, common_names = build_upstream_payload(market, prior, current)
        attempt = attempt_leadership_transform(payload)
        per_market[market.upper()] = {
            "outcome": attempt["outcome"],
            "reason": attempt["reason"],
            "leadership_packet_sha256": (
                attempt["packet"]["payload_sha256"] if attempt["packet"] else None
            ),
            "raw_response_sha256": {"prior": prior["response_sha256"], "current": current["response_sha256"]},
            "common_index_name_count": len(common_names),
            "discovered_index_names": common_names,
        }
    observation_date = _iso_date(current_date)
    # generated_at is derived from the real fetch timestamps recorded
    # during this run, never wall-clock, for byte-identical
    # reproducibility on a rerun against the same source.
    summary = {
        "schema_version": SCHEMA_VERSION,
        "observation_date": observation_date,
        "prior_date": _iso_date(prior_date),
        "markets": per_market,
        "generated_at": max(fetched_at_values),
    }
    summary["payload_sha256"] = payload_sha256(summary)
    return summary


def populate(auth_key: str, prior_date: str, current_date: str, *, opener=urlopen) -> dict:
    summary = run(auth_key, prior_date, current_date, opener=opener)
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
    args = parser.parse_args()
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
