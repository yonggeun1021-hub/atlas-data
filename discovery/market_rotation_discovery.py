"""T1 ``market_rotation_discovery/1`` -- CRYPTO builder (TKT-1).

Display/observation only. No trading authority anywhere in this module --
see :data:`AUTHORITY_FIELDS` and ``config/market_rotation_discovery_contract.json``'s
own ``authority`` block, both hardcoded ``False``.

Per the CIO's W5 diagnosis and the user-ratified candidate-pipeline-rebuild
plan (``USER_RATIFICATION_CANDIDATE_PIPELINE_REBUILD_20260913``,
sha256 ``6870b4572fe46901e9e2ce14e07e01d89c54ba2860ab42a0087f16a4c4703625``):
the crypto rotation *selection* policy (which bucket/window/top-N/ranking
rule actually decides "this bucket is the strong rotation target") has not
been ratified. The contract (``config/market_rotation_discovery_contract.json``)
declares the fields that policy will eventually populate, but until its
``rotation_selection_policy.approval_status`` becomes ``RATIFIED``, this
builder always reports ``rotation_selection_status="UNKNOWN:<code>"`` and
leaves ``rotation_selection``/``ranked`` empty. **No substitute ranking is
ever produced** -- this is a hard rule, not a fallback to approximate.

Inputs (each read read-only, never mutated, never re-derived by this
module beyond the checks below):

* the latest published ``upbit_tradeable_universe`` snapshot
  (``data/observations/upbit_tradeable_universe/<date>/packet.json``) --
  the 282-row public population, and the (currently 8-asset) identity-
  ratified subset via each row's ``candidate_canonical_asset_id``.
* the latest published ``crypto_leadership`` packet
  (``data/observations/crypto_leadership/<date>/packet.json``) -- the
  already-computed 7d/30d relative-strength numbers. This module never
  recomputes leadership math itself: every numeric value it ever surfaces
  in ``ranked[].features`` is an exact passthrough of what
  ``.github/scripts/crypto_leadership.py`` already computed (Decimal-text,
  12dp ``ROUND_HALF_EVEN``, ``cumulative_gross_return / btc − 1``) --
  see :func:`extract_asset_relative_strength`. This is how "7d/30d values
  match the existing leadership formula to the decimal" is guaranteed: by
  never touching the math a second time, not by reimplementing it against
  a different price source (Upbit candles are a different exchange/price
  series from the Kraaken-sourced ``crypto_leadership`` packet; recomputing
  against them would not reproduce the same numbers -- see
  :func:`read_upbit_candle_freshness` for the one thing this module *does*
  use the Upbit candle archive for: a per-asset price-freshness check, not
  a ranking input).
* the latest crypto regime status
  (``data/latest_crypto_regime_refresh_status.json``) -- surfaced verbatim
  as ``regime_display`` (both its ``current_reference.leadership_code``
  and its stricter ``official_decision.runtime_regime`` are kept, since
  they can and currently do disagree; this module does not collapse them
  into one field and silently drop the other).

PIT (point-in-time) discipline: mirrors the crypto-market convention
already used by ``universe/upbit_tradeable_universe.py`` and
``rotation/crypto_rotation.py`` -- a plain ``available_at``/``as_of_date``
vs. ``evaluation_as_of`` timestamp comparison, never the US-EOD-specific
``atlas_price_pit_contract.py`` (that module is New-York-market-close
specific and has no crypto caller anywhere in this repo). Any input whose
own timestamp is later than ``evaluation_as_of`` fails the whole build
closed (:class:`MarketRotationDiscoveryError`), never a partial result.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "market_rotation_discovery_contract.json"
OUTPUT_ROOT = ROOT / "data" / "observations" / "market_rotation_discovery"

AUTHORITY_FIELDS = (
    "rotation_selection_authorized",
    "ranking_authorized",
    "trading_authorized",
    "order_authorized",
    "production_authorized",
)


class MarketRotationDiscoveryError(ValueError):
    """A recoverable, fail-closed build error -- never a partial result."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def deterministic_bucket(asset_id: str) -> str:
    """The frozen ``btc_eth_else_alt`` rule, copied verbatim from
    ``.github/scripts/crypto_leadership.py``'s own ``deterministic_bucket``
    (and its ``expected_bucket`` cross-check) -- a plain asset-id string
    match, no config lookup, always deterministic."""
    return asset_id if asset_id in {"BTC", "ETH"} else "ALT"


def _parse_utc(value: str) -> dt.datetime:
    if len(value) == 10:  # a bare YYYY-MM-DD date -> end-of-day UTC
        value = f"{value}T23:59:59Z"
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rotation_selection_policy(contract: dict) -> dict:
    return contract.get("rotation_selection_policy") or {}


def _window(leadership_packet: dict, window_id: str) -> dict | None:
    for window in leadership_packet.get("windows", []) or []:
        if window.get("window_id") == window_id:
            return window
    return None


def extract_asset_relative_strength(
    leadership_packet: dict, window_id: str, asset_id: str
) -> dict | None:
    """Pure passthrough: the exact row the published ``crypto_leadership``
    packet already computed for ``asset_id`` in ``window_id`` -- every
    field (including ``relative_strength_vs_btc``, a Decimal-text string)
    returned unchanged. Returns ``None`` if the window is not
    ``OBSERVED`` or the asset has no row in it. Never recomputes,
    rounds, or reformats any value -- this is what guarantees decimal-
    exact agreement with the existing formula."""
    window = _window(leadership_packet, window_id)
    if window is None or window.get("status") != "OBSERVED":
        return None
    for row in window.get("asset_relative_strength") or []:
        if row.get("canonical_asset_id") == asset_id:
            return dict(row)
    return None


def extract_group_relative_strength(
    leadership_packet: dict, window_id: str, group_id: str
) -> dict | None:
    """Same passthrough contract as :func:`extract_asset_relative_strength`,
    for a bucket/group row instead of an asset row."""
    window = _window(leadership_packet, window_id)
    if window is None or window.get("status") != "OBSERVED":
        return None
    bucket_rows = ((window.get("group_relative_strength") or {}).get("bucket")) or []
    for row in bucket_rows:
        if row.get("group_id") == group_id:
            return dict(row)
    return None


def rotation_selection_gate(contract: dict, leadership_packet: dict | None) -> tuple[str, str | None]:
    """Returns ``(status, unknown_code)``. ``status`` is ``"OBSERVED"`` or
    ``"UNKNOWN"``. Checked in this fixed order: (1) a ratified rotation
    selection policy must exist at all; (2) the leadership packet itself
    must be available; (3) the policy's declared window must be
    ``OBSERVED`` in that packet. Any failure is ``UNKNOWN`` with a
    specific code -- never a silent substitute."""
    policy = rotation_selection_policy(contract)
    if policy.get("approval_status") != "RATIFIED":
        return "UNKNOWN", policy.get("unknown_code_when_unratified") or "NO_RATIFIED_ROTATION_SELECTION_POLICY"
    if leadership_packet is None:
        return "UNKNOWN", "LEADERSHIP_PACKET_NOT_AVAILABLE"
    window_id = policy.get("window_id")
    window = _window(leadership_packet, window_id)
    if window is None:
        return "UNKNOWN", "ROTATION_WINDOW_NOT_PRESENT"
    if window.get("status") != "OBSERVED":
        return "UNKNOWN", window.get("unknown_reason") or "LEADERSHIP_WINDOW_UNKNOWN"
    return "OBSERVED", None


def build_ranked_row(
    market_row: dict,
    leadership_packet: dict,
    window_id: str,
    rank_in_sector: int,
) -> dict:
    """Builds one ``ranked[]`` row for an identity-resolved asset. Only
    ever called once a rotation selection policy is ratified and its
    window is ``OBSERVED`` (see :func:`rotation_selection_gate`) -- kept
    as a standalone, directly-testable function so its decimal-exact
    passthrough behavior (acceptance criterion 4) is verifiable now, even
    though it is not reachable through the current always-UNKNOWN
    pipeline (acceptance criterion 3)."""
    asset_id = market_row["candidate_canonical_asset_id"]
    market_code = market_row["market"]
    symbol = market_code.split("-", 1)[1] if "-" in market_code else market_code
    membership_id = deterministic_bucket(asset_id)
    relative_strength = extract_asset_relative_strength(leadership_packet, window_id, asset_id)
    reasons: list[str] = ["IDENTITY_RATIFIED", f"BUCKET_{membership_id}"]
    data_gaps: list[str] = []
    features: dict[str, Any] = {}
    if relative_strength is None:
        data_gaps.append("LEADERSHIP_ASSET_ROW_NOT_OBSERVED")
    else:
        features["relative_strength_vs_btc"] = relative_strength.get("relative_strength_vs_btc")
        features["cumulative_gross_return"] = relative_strength.get("cumulative_gross_return")
        features["window_id"] = window_id
        reasons.append("RELATIVE_STRENGTH_VS_BTC_OBSERVED")
    return {
        "membership_id": membership_id,
        "asset_id": asset_id,
        "symbol": symbol,
        "rank_in_sector": rank_in_sector,
        "features": features,
        "reasons": reasons,
        "data_gaps": data_gaps,
    }


def _regime_display(regime_status: dict | None) -> dict:
    if regime_status is None:
        return {"status": "UNKNOWN", "leadership_code": None, "runtime_regime": None}
    current_reference = regime_status.get("current_reference") or {}
    official_decision = regime_status.get("official_decision") or {}
    return {
        "status": regime_status.get("status"),
        "leadership_code": current_reference.get("leadership_code"),
        "runtime_regime": official_decision.get("runtime_regime"),
    }


def build_crypto_rotation_discovery(
    *,
    universe_packet: dict,
    leadership_packet: dict | None,
    regime_status: dict | None,
    contract: dict,
    evaluation_as_of: str,
) -> dict:
    """Pure function of its explicit arguments. Raises
    :class:`MarketRotationDiscoveryError` (fail closed, never a partial
    result) if any input is future-dated relative to ``evaluation_as_of``
    or structurally missing what PIT requires.
    """
    evaluation_dt = _parse_utc(evaluation_as_of)
    evaluation_date = evaluation_as_of[:10]

    packet = universe_packet.get("packet", universe_packet)
    universe_available_at = packet.get("available_at")
    if not universe_available_at:
        raise MarketRotationDiscoveryError("UNIVERSE_AVAILABLE_AT_MISSING")
    if _parse_utc(universe_available_at) > evaluation_dt:
        raise MarketRotationDiscoveryError("UNIVERSE_AVAILABLE_AT_FUTURE_DATED")

    if leadership_packet is not None:
        leadership_as_of = leadership_packet.get("as_of_date")
        if leadership_as_of and leadership_as_of > evaluation_date:
            raise MarketRotationDiscoveryError("LEADERSHIP_AS_OF_DATE_FUTURE_DATED")

    if regime_status is not None:
        regime_as_of = (regime_status.get("current_reference") or {}).get("as_of_date")
        if regime_as_of and regime_as_of > evaluation_date:
            raise MarketRotationDiscoveryError("REGIME_AS_OF_DATE_FUTURE_DATED")

    markets = packet.get("markets") or []
    population_count = len(markets)
    identity_resolved = [m for m in markets if m.get("candidate_canonical_asset_id")]
    identity_resolved_count = len(identity_resolved)

    bucket_counts = {"BTC": 0, "ETH": 0, "ALT": 0}
    for row in identity_resolved:
        bucket_counts[deterministic_bucket(row["candidate_canonical_asset_id"])] += 1

    rotation_status, rotation_code = rotation_selection_gate(contract, leadership_packet)
    rotation_selection_status = "OBSERVED" if rotation_status == "OBSERVED" else f"UNKNOWN:{rotation_code}"

    rotation_selection: list[dict] = []
    ranked: list[dict] = []
    if rotation_status == "OBSERVED":
        # Not reachable while rotation_selection_policy.approval_status is
        # UNRATIFIED (always true today) -- kept for when it is ratified.
        policy = rotation_selection_policy(contract)
        window_id = policy["window_id"]
        for index, row in enumerate(
            sorted(identity_resolved, key=lambda m: m["candidate_canonical_asset_id"]), start=1
        ):
            ranked.append(build_ranked_row(row, leadership_packet, window_id, index))
        rotation_selection = sorted({row["membership_id"] for row in ranked})

    result: dict[str, Any] = {
        "schema_version": contract.get("schema_version"),
        "contract_id": contract.get("contract_id"),
        "market": "CRYPTO",
        "snapshot_date": evaluation_date,
        "evaluation_as_of": evaluation_as_of,
        "regime_display": _regime_display(regime_status),
        "rotation_selection_status": rotation_selection_status,
        "rotation_selection": rotation_selection,
        "ranked": ranked,
        "counts": {
            "population_count": population_count,
            "identity_resolved_count": identity_resolved_count,
            "identity_unresolved_count": population_count - identity_resolved_count,
            "bucket_counts": bucket_counts,
            "rotation_selection_count": len(rotation_selection),
            "ranked_count": len(ranked),
        },
        "lineage": {
            "universe_packet_payload_sha256": packet.get("payload_sha256") or universe_packet.get("payload_sha256"),
            "universe_snapshot_date": packet.get("snapshot_date") or universe_packet.get("snapshot_date"),
            "leadership_packet_as_of_date": leadership_packet.get("as_of_date") if leadership_packet else None,
            "leadership_packet_status": leadership_packet.get("status") if leadership_packet else None,
            "regime_status_generation_id": regime_status.get("generation_id") if regime_status else None,
            "contract_sha256": payload_sha256(contract),
        },
        "authority": {field: False for field in AUTHORITY_FIELDS},
    }
    result["payload_sha256"] = payload_sha256(result)
    return result


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_dated_packet(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    dated = sorted((entry for entry in root.iterdir() if entry.is_dir()), key=lambda p: p.name)
    for entry in reversed(dated):
        candidate = entry / "packet.json"
        if candidate.is_file():
            return candidate
    return None


def read_upbit_candle_freshness(raw_root: Path, market: str) -> dict | None:
    """The one use this module makes of the Upbit candle archive: a
    per-asset price-data freshness check (not a ranking input -- ranking
    numbers come only from the published ``crypto_leadership`` packet,
    see the module docstring). Returns ``None`` if no candle file is
    found for ``market``; otherwise the most recent finalized candle's
    own timestamp field, read straight through with no computation.
    """
    if not raw_root.is_dir():
        return None
    dated = sorted((entry for entry in raw_root.iterdir() if entry.is_dir()), key=lambda p: p.name)
    for entry in reversed(dated):
        candle_path = entry / "upbit_candles_days.ndjson.gz"
        if not candle_path.is_file():
            continue
        with gzip.open(candle_path, "rt", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row.get("market") == market:
                    return row
        return None
    return None


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-as-of", default=None, help="ISO-8601 UTC timestamp; defaults to now")
    parser.add_argument("--universe-root", type=Path, default=ROOT / "data/observations/upbit_tradeable_universe")
    parser.add_argument("--leadership-root", type=Path, default=ROOT / "data/observations/crypto_leadership")
    parser.add_argument("--regime-status-path", type=Path, default=ROOT / "data/latest_crypto_regime_refresh_status.json")
    parser.add_argument("--contract-path", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--dry-run", action="store_true", help="Print the packet; do not write it")
    args = parser.parse_args(argv)

    evaluation_as_of = args.evaluation_as_of or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    universe_path = _latest_dated_packet(args.universe_root)
    if universe_path is None:
        raise MarketRotationDiscoveryError(f"UNIVERSE_PACKET_NOT_FOUND:{args.universe_root}")
    universe_packet = _load_json(universe_path)

    leadership_path = _latest_dated_packet(args.leadership_root)
    leadership_packet = _load_json(leadership_path) if leadership_path else None

    regime_status = _load_json(args.regime_status_path) if args.regime_status_path.is_file() else None

    contract = load_contract(args.contract_path)

    result = build_crypto_rotation_discovery(
        universe_packet=universe_packet,
        leadership_packet=leadership_packet,
        regime_status=regime_status,
        contract=contract,
        evaluation_as_of=evaluation_as_of,
    )

    if args.dry_run:
        print(canonical_json(result))
        return 0

    snapshot_date = evaluation_as_of[:10]
    output_dir = args.output_root / snapshot_date
    output_path = output_dir / "packet.json"
    if output_path.is_file():
        existing = _load_json(output_path)
        if existing.get("payload_sha256") == result["payload_sha256"]:
            print(canonical_json({"outcome": "verified_existing", "payload_sha256": result["payload_sha256"]}))
            return 0
        raise MarketRotationDiscoveryError(f"OUTPUT_ALREADY_PRESENT_WITH_DIFFERENT_PAYLOAD:{output_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(canonical_json(result), encoding="utf-8")
    print(canonical_json({"outcome": "populated", "payload_sha256": result["payload_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
