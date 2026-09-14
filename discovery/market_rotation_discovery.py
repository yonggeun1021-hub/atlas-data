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
declares the fields that policy will eventually populate.

**Independent review of the first draft (FIX, applied here) found the
OBSERVED branch was hiding a forbidden substitute ranking**: it selected
every bucket, ignored ``top_n``, and used alphabetical order as
``rank_in_sector``. :func:`rotation_selection_gate` now *always* returns
``UNKNOWN`` -- ``NO_RATIFIED_ROTATION_SELECTION_POLICY`` while the policy
is unratified, and (even once it is ratified) `ROTATION_SELECTION_RULE_NOT_IMPLEMENTED``,
because the actual selection algorithm (rank groups by relative strength
within the policy's window, select the top ``top_n``, rank assets within
each selected bucket, a deterministic tie-break) is not implemented in
this work unit -- that is explicitly a separate, later ticket, once the
policy itself is ratified. ``rotation_selection``/``ranked`` are therefore
*always* empty from this module today. **No substitute ranking is ever
produced.** :func:`build_ranked_row` and the ``extract_*`` passthrough
helpers below remain as standalone, directly-tested building blocks for
that later ticket -- they are simply never called from
:func:`build_crypto_rotation_discovery` itself yet.

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
  is an exact passthrough of what ``.github/scripts/crypto_leadership.py``
  already computed (Decimal-text, 12dp ``ROUND_HALF_EVEN``,
  ``cumulative_gross_return / btc − 1``) -- see
  :func:`extract_asset_relative_strength`. **A window or bucket row is
  "observed" only when its own ``status`` field is exactly
  ``"OBSERVED_UNCLASSIFIED"``** -- the real status vocabulary emitted by
  ``crypto_leadership.py`` (confirmed by reading that file directly);
  earlier drafts of this module checked for a literal ``"OBSERVED"``,
  which never occurs in real output and would have made the passthrough
  permanently unreachable even after ratification (independent-review
  finding, fixed here). A bucket row can independently be ``"UNKNOWN"``
  (``BUCKET_EMPTY_ON_REQUIRED_DATE``) even while its window's own status
  is ``"OBSERVED_UNCLASSIFIED"`` -- :func:`extract_group_relative_strength`
  checks the bucket row's own status, not just the window's.
* the latest crypto regime status
  (``data/latest_crypto_regime_refresh_status.json``) -- surfaced verbatim
  as ``regime_display`` (both its ``current_reference.leadership_code``
  and its stricter ``official_decision.runtime_regime`` are kept, since
  they can and currently do disagree; this module does not collapse them
  into one field and silently drop the other).

PIT (point-in-time) discipline: mirrors the crypto-market convention
already used by ``universe/upbit_tradeable_universe.py`` and
``rotation/crypto_rotation.py`` -- a plain ``available_at``/``as_of_date``/
``generated_at`` vs. ``evaluation_as_of`` timestamp comparison, never the
US-EOD-specific ``atlas_price_pit_contract.py`` (that module is
New-York-market-close specific and has no crypto caller anywhere in this
repo). Any input whose own timestamp is later than ``evaluation_as_of``,
or that is missing the field this check depends on, fails the whole build
closed (:class:`MarketRotationDiscoveryError`), never a partial result.
When replaying a *past* ``evaluation_as_of`` (backfill), :func:`_latest_dated_packet`
only considers dated subdirectories at or before that date -- it never
looks ahead to a packet published after the replay point.
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

#: The only status value crypto_leadership.py ever writes for a window or
#: bucket row it actually computed. Never the literal "OBSERVED".
LEADERSHIP_OBSERVED_STATUS = "OBSERVED_UNCLASSIFIED"

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
    returned unchanged. Returns ``None`` if the window's own ``status`` is
    not exactly :data:`LEADERSHIP_OBSERVED_STATUS` or the asset has no row
    in it. Never recomputes, rounds, or reformats any value -- this is
    what guarantees decimal-exact agreement with the existing formula."""
    window = _window(leadership_packet, window_id)
    if window is None or window.get("status") != LEADERSHIP_OBSERVED_STATUS:
        return None
    for row in window.get("asset_relative_strength") or []:
        if row.get("canonical_asset_id") == asset_id:
            return dict(row)
    return None


def extract_group_relative_strength(
    leadership_packet: dict, window_id: str, group_id: str
) -> dict | None:
    """Same passthrough contract as :func:`extract_asset_relative_strength`,
    for a bucket/group row instead of an asset row. A bucket row carries
    its *own* ``status`` (e.g. ``"UNKNOWN"``/``BUCKET_EMPTY_ON_REQUIRED_DATE``
    for one thin bucket like BTC/ETH even while the window overall is
    ``OBSERVED_UNCLASSIFIED``) -- both the window's and the row's own
    status must be observed before this returns a value."""
    window = _window(leadership_packet, window_id)
    if window is None or window.get("status") != LEADERSHIP_OBSERVED_STATUS:
        return None
    bucket_rows = ((window.get("group_relative_strength") or {}).get("bucket")) or []
    for row in bucket_rows:
        if row.get("group_id") == group_id:
            if row.get("status") != LEADERSHIP_OBSERVED_STATUS:
                return None
            return dict(row)
    return None


def rotation_selection_gate(contract: dict) -> tuple[str, str]:
    """Returns ``(status, code)``. ``status`` is always ``"UNKNOWN"`` today
    (independent-review FIX): either because no rotation selection policy
    is ratified yet, or -- even once one is -- because the actual
    selection algorithm is not implemented in this work unit. Implementing
    it (rank groups by relative strength within the policy's window,
    select the top ``top_n``, rank assets within each selected bucket,
    apply a deterministic tie-break) is a separate, later ticket. This
    function therefore never returns anything but ``"UNKNOWN"`` right now
    -- there is no reachable path in this module that produces a
    substitute ranking.
    """
    policy = rotation_selection_policy(contract)
    if policy.get("approval_status") != "RATIFIED":
        return "UNKNOWN", policy.get("unknown_code_when_unratified") or "NO_RATIFIED_ROTATION_SELECTION_POLICY"
    return "UNKNOWN", "ROTATION_SELECTION_RULE_NOT_IMPLEMENTED"


def build_ranked_row(
    market_row: dict,
    leadership_packet: dict,
    window_id: str,
    rank_in_sector: int,
) -> dict:
    """Builds one ``ranked[]`` row for an identity-resolved asset.

    **Not called anywhere in :func:`build_crypto_rotation_discovery`
    today** -- :func:`rotation_selection_gate` never returns ``"OBSERVED"``
    (see its docstring). Kept as a standalone, directly-testable function
    so its decimal-exact passthrough behavior (TKT-1 acceptance criterion
    4) is verifiable now, and so the later ticket that implements the real
    selection rule has a tested building block to call, rather than
    writing this logic from scratch under time pressure.
    """
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
    or missing a field PIT depends on.
    """
    evaluation_dt = _parse_utc(evaluation_as_of)
    evaluation_date = evaluation_as_of[:10]

    packet = universe_packet.get("packet", universe_packet)
    universe_available_at = packet.get("available_at")
    if not universe_available_at:
        raise MarketRotationDiscoveryError("UNIVERSE_AVAILABLE_AT_MISSING")
    if _parse_utc(universe_available_at) > evaluation_dt:
        raise MarketRotationDiscoveryError("UNIVERSE_AVAILABLE_AT_FUTURE_DATED")
    universe_snapshot_date = packet.get("snapshot_date") or universe_packet.get("snapshot_date")
    universe_snapshot_age_days: int | None = None
    if universe_snapshot_date:
        universe_snapshot_age_days = (
            dt.date.fromisoformat(evaluation_date) - dt.date.fromisoformat(universe_snapshot_date)
        ).days

    if leadership_packet is not None:
        leadership_as_of = leadership_packet.get("as_of_date")
        if not leadership_as_of:
            raise MarketRotationDiscoveryError("LEADERSHIP_AS_OF_DATE_MISSING")
        if leadership_as_of > evaluation_date:
            raise MarketRotationDiscoveryError("LEADERSHIP_AS_OF_DATE_FUTURE_DATED")

    if regime_status is not None:
        regime_generated_at = regime_status.get("generated_at")
        if not regime_generated_at:
            raise MarketRotationDiscoveryError("REGIME_GENERATED_AT_MISSING")
        if _parse_utc(regime_generated_at) > evaluation_dt:
            raise MarketRotationDiscoveryError("REGIME_GENERATED_AT_FUTURE_DATED")
        regime_as_of = (regime_status.get("current_reference") or {}).get("as_of_date")
        if not regime_as_of:
            raise MarketRotationDiscoveryError("REGIME_AS_OF_DATE_MISSING")
        if regime_as_of > evaluation_date:
            raise MarketRotationDiscoveryError("REGIME_AS_OF_DATE_FUTURE_DATED")

    markets = packet.get("markets") or []
    population_count = len(markets)
    identity_resolved = [m for m in markets if m.get("candidate_canonical_asset_id")]
    identity_resolved_count = len(identity_resolved)

    bucket_counts = {"BTC": 0, "ETH": 0, "ALT": 0}
    for row in identity_resolved:
        bucket_counts[deterministic_bucket(row["candidate_canonical_asset_id"])] += 1

    rotation_status, rotation_code = rotation_selection_gate(contract)
    rotation_selection_status = f"UNKNOWN:{rotation_code}"

    # rotation_status is always "UNKNOWN" today (see rotation_selection_gate's
    # own docstring) -- rotation_selection/ranked are therefore always
    # empty. No branch in this module ever populates them yet.
    rotation_selection: list[dict] = []
    ranked: list[dict] = []

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
            "universe_snapshot_date": universe_snapshot_date,
            "universe_snapshot_age_days": universe_snapshot_age_days,
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


def _latest_dated_packet(root: Path, *, not_after_date: str | None = None) -> Path | None:
    """Newest ``root/<YYYY-MM-DD>/packet.json`` by directory name.

    ``not_after_date`` (a ``YYYY-MM-DD`` string), when given, excludes any
    dated subdirectory strictly after it -- this is what makes a
    *historical* ``evaluation_as_of`` replay pick the packet that was
    actually the latest one available *as of that date*, never one
    published later (a look-ahead/PIT violation the un-cutoff version of
    this function would otherwise allow for backfill use).
    """
    if not root.is_dir():
        return None
    dated = sorted((entry for entry in root.iterdir() if entry.is_dir()), key=lambda p: p.name)
    if not_after_date is not None:
        dated = [entry for entry in dated if entry.name <= not_after_date]
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
    parser.add_argument(
        "--evaluation-as-of", default=None,
        help="ISO-8601 UTC timestamp; defaults to today's end-of-day (23:59:59Z UTC), "
             "not the exact current instant -- so multiple runs on the same UTC day "
             "produce byte-identical output (see the CI fix note in TKT1_RESULT.md).",
    )
    parser.add_argument("--universe-root", type=Path, default=ROOT / "data/observations/upbit_tradeable_universe")
    parser.add_argument("--leadership-root", type=Path, default=ROOT / "data/observations/crypto_leadership")
    parser.add_argument("--regime-status-path", type=Path, default=ROOT / "data/latest_crypto_regime_refresh_status.json")
    parser.add_argument("--contract-path", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--dry-run", action="store_true", help="Print the packet; do not write it")
    args = parser.parse_args(argv)

    if args.evaluation_as_of:
        evaluation_as_of = args.evaluation_as_of
    else:
        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        evaluation_as_of = f"{today}T23:59:59Z"
    evaluation_date = evaluation_as_of[:10]

    universe_path = _latest_dated_packet(args.universe_root, not_after_date=evaluation_date)
    if universe_path is None:
        raise MarketRotationDiscoveryError(f"UNIVERSE_PACKET_NOT_FOUND:{args.universe_root}")
    universe_packet = _load_json(universe_path)

    leadership_path = _latest_dated_packet(args.leadership_root, not_after_date=evaluation_date)
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

    output_dir = args.output_root / evaluation_date
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
