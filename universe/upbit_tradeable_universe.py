#!/usr/bin/env python3
"""P3-12 Upbit KRW tradeable-universe / PAPER-eligibility classifier.

Per-market state machine over Upbit's own public KRW market data only:

    OBSERVATION_POOL -> TRADEABLE_UNIVERSE -> PAPER_ELIGIBLE
                      \\-> BLOCKED (identity collision / evidence tamper)

``state`` is a *classification*, never an authority grant. Every output
row's ``authority`` block is hardcoded all-``false`` regardless of state --
turning a classification into real investable/PAPER/order authority is a
separate, later, explicitly-ratified change that this module cannot make.

Three independently ratified inputs must all be effective for the evaluated
vintage before any market can leave ``OBSERVATION_POOL``:

1. ``tradeable_universe_policy`` and ``taxonomy`` are both ``RATIFIED`` and
   their document-level effective date is not later than ``evaluation_as_of``.
2. The specific market's canonical identity mapping appears in the exact,
   evidence-bound ``config/upbit_asset_identity_registry.json`` mapping and
   that registry is effective for ``evaluation_as_of``.
3. The specific market's canonical identity mapping appears in
   ``ratified_identity_registry`` (``{upbit_market: canonical_asset_id}``).
   The production population script derives this argument only through the
   fail-closed registry loader below; callers cannot promote a ticker match.

The v1 ratification is effective 2026-08-30. Earlier proposed taxonomy and
identity evidence retain their original dates and are never backfilled into
an earlier classification.

A trading-suspended / investment-warning market (Upbit's own
``market_event.warning``) is force-excluded from ``TRADEABLE_UNIVERSE`` /
``PAPER_ELIGIBLE`` unconditionally in code -- this is not a policy toggle a
future config edit could weaken.

Kraken breadth/leadership membership is READ-ONLY, OBSERVATIONAL labeling
here (``kraken_cross_exchange_reference``). See the
``# SAFETY INVARIANT`` comment on ``build_classification`` below: that
value is never read anywhere else in this module's gating logic.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_CAPTURE_SPEC = importlib.util.spec_from_file_location(
    "upbit_market_capture_for_universe",
    ROOT / ".github" / "scripts" / "upbit_market_capture.py",
)
UPBIT_CAPTURE = importlib.util.module_from_spec(_CAPTURE_SPEC)
assert _CAPTURE_SPEC.loader is not None
_CAPTURE_SPEC.loader.exec_module(UPBIT_CAPTURE)

# P3-12-GOV-05: runtime exact-approval binding. ``approval_status ==
# "RATIFIED"`` alone is never sufficient for the identity registry or
# taxonomy -- see ``_approval_effective()`` below and
# ``governance/upbit_exact_release_binding.py``'s module docstring.
_EXACT_RELEASE_BINDING_SPEC = importlib.util.spec_from_file_location(
    "upbit_tradeable_universe_exact_release_binding",
    ROOT / "governance" / "upbit_exact_release_binding.py",
)
EXACT_RELEASE_BINDING = importlib.util.module_from_spec(_EXACT_RELEASE_BINDING_SPEC)
assert _EXACT_RELEASE_BINDING_SPEC.loader is not None
_EXACT_RELEASE_BINDING_SPEC.loader.exec_module(EXACT_RELEASE_BINDING)


CONTRACT_PATH = ROOT / "config" / "upbit_tradeable_universe_contract.json"
CAPTURE_CONTRACT_PATH = ROOT / "config" / "upbit_market_capture_contract.json"
POLICY_PATH = ROOT / "config" / "upbit_tradeable_universe_policy.json"
TAXONOMY_PATH = ROOT / "config" / "upbit_exclusion_taxonomy.json"
IDENTITY_REGISTRY_PATH = ROOT / "config" / "upbit_asset_identity_registry.json"
OUTPUT_SCHEMA_VERSION = "upbit_tradeable_universe_packet/1"

STATE_OBSERVATION_POOL = "OBSERVATION_POOL"
STATE_TRADEABLE_UNIVERSE = "TRADEABLE_UNIVERSE"
STATE_PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
STATE_BLOCKED = "BLOCKED"

_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# P3-12's declared scope is Upbit KRW spot only ("Upbit KRW 현물 시장 식별").
# GET /v1/market/all returns every quote currency Upbit lists (KRW-*, BTC-*,
# USDT-*) in one response; BTC-/USDT-quoted pairs are out of scope and must
# never reach classification or identity review. Matches
# identity/upbit_market_identity_proposal.py's own _MARKET_RE.
_KRW_MARKET_RE = re.compile(r"^KRW-[A-Z0-9]{2,20}$")

# Hardcoded, not policy-driven: never read, set, or made overridable by any
# config value in this module.
_ROW_AUTHORITY = {
    "investable_eligible": False,
    "paper_eligible": False,
    "stage_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "order_authorized": False,
}


class UpbitUniverseError(ValueError):
    """Fail-closed P3-12 classifier/contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpbitUniverseError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _file_sha(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise UpbitUniverseError(f"FILE_HASH_FAILED:{path}:{exc}") from exc


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(Path(path))
    if not isinstance(value, dict) or value.get("contract_version") != "upbit_tradeable_universe_classifier/1":
        raise UpbitUniverseError("CONTRACT_FIELD_MISMATCH:contract_version")
    if value.get("kraken_cross_reference_policy", {}).get("may_affect_state") is not False:
        raise UpbitUniverseError("CONTRACT_FIELD_MISMATCH:kraken_cross_reference_policy.may_affect_state")
    for key, expected in value.get("authority", {}).items():
        if expected is not False:
            raise UpbitUniverseError(f"CONTRACT_AUTHORITY_NOT_FALSE:{key}")
    return copy.deepcopy(value)


def load_policy(path: Path = POLICY_PATH) -> dict:
    doc = _read_json(Path(path))
    required = {
        "approval_status", "min_listing_history_finalized_days", "turnover_lookback_finalized_days",
        "min_30d_avg_krw_turnover", "max_spread_bps", "max_estimated_paper_slippage_bps",
        "paper_slippage_estimate_notional_krw", "max_capture_age_hours",
    }
    if not isinstance(doc, dict) or not required.issubset(doc):
        raise UpbitUniverseError("POLICY_FIELDS_INVALID")
    if doc.get("approval_status") == "RATIFIED":
        if not _DATE_RE.fullmatch(str(doc.get("effective_date", ""))):
            raise UpbitUniverseError("POLICY_EFFECTIVE_DATE_INVALID")
        if not _UTC_RE.fullmatch(str(doc.get("ratified_at_utc", ""))):
            raise UpbitUniverseError("POLICY_RATIFIED_AT_INVALID")
        if doc.get("paper_only") is not True:
            raise UpbitUniverseError("POLICY_SCOPE_NOT_PAPER_ONLY")
        for key in (
            "exchange_authorized", "order_authorized", "paper_exit_authorized",
            "production_authorized", "real_capital_authorized", "trading_authorized",
        ):
            if doc.get(key) is not False:
                raise UpbitUniverseError(f"POLICY_AUTHORITY_NOT_FALSE:{key}")
    return doc


def load_taxonomy(path: Path = TAXONOMY_PATH) -> dict:
    doc = _read_json(Path(path))
    required = {"approval_status", "eligible_category", "excluded_categories", "unknown_asset_policy", "records"}
    if not isinstance(doc, dict) or not required.issubset(doc):
        raise UpbitUniverseError("TAXONOMY_FIELDS_INVALID")
    if doc.get("unknown_asset_policy") != "fail_closed_unknown":
        raise UpbitUniverseError("TAXONOMY_UNKNOWN_POLICY_NOT_FAIL_CLOSED")
    if doc.get("approval_status") in {"RATIFIED", "PENDING_GOVERNANCE_RESOLUTION"}:
        if not _DATE_RE.fullmatch(str(doc.get("effective_from", ""))):
            raise UpbitUniverseError("TAXONOMY_EFFECTIVE_FROM_INVALID")
        if not _UTC_RE.fullmatch(str(doc.get("ratified_at_utc", ""))):
            raise UpbitUniverseError("TAXONOMY_RATIFIED_AT_INVALID")
    return doc


def _resolve_repo_path(relative_path: str, label: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path or relative_path.startswith("/"):
        raise UpbitUniverseError(f"{label}_PATH_INVALID")
    candidate = (ROOT / relative_path).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise UpbitUniverseError(f"{label}_PATH_OUTSIDE_REPOSITORY") from exc
    return candidate


def load_identity_registry(path: Path = IDENTITY_REGISTRY_PATH) -> dict:
    """Load the retained mapping and revalidate its exact evidence pins.

    The source candidate packet deliberately remains a historical
    ``PROPOSED_UNRATIFIED`` observation. A governance freeze preserves the
    55-row mapping for audit while ``approval_status`` is
    ``PENDING_GOVERNANCE_RESOLUTION``; ``effective_identity_mapping`` then
    returns an empty mapping, so no consumer can promote it.
    """
    doc = _read_json(Path(path))
    required = {
        "registry_version", "approval_status", "effective_from", "ratified_at_utc",
        "source_candidate_packet", "source_identity_evidence", "unknown_market_policy",
        "mappings", "authority",
    }
    if not isinstance(doc, dict) or not required.issubset(doc):
        raise UpbitUniverseError("IDENTITY_REGISTRY_FIELDS_INVALID")
    if doc.get("registry_version") != "upbit_asset_identity_registry/v1":
        raise UpbitUniverseError("IDENTITY_REGISTRY_VERSION_INVALID")
    if doc.get("approval_status") not in {"RATIFIED", "PENDING_GOVERNANCE_RESOLUTION"}:
        raise UpbitUniverseError("IDENTITY_REGISTRY_STATUS_INVALID")
    if not _DATE_RE.fullmatch(str(doc.get("effective_from", ""))):
        raise UpbitUniverseError("IDENTITY_REGISTRY_EFFECTIVE_FROM_INVALID")
    if not _UTC_RE.fullmatch(str(doc.get("ratified_at_utc", ""))):
        raise UpbitUniverseError("IDENTITY_REGISTRY_RATIFIED_AT_INVALID")
    if doc.get("unknown_market_policy") != "fail_closed_unratified_identity":
        raise UpbitUniverseError("IDENTITY_REGISTRY_UNKNOWN_POLICY_INVALID")
    if not isinstance(doc.get("authority"), dict) or not doc["authority"]:
        raise UpbitUniverseError("IDENTITY_REGISTRY_AUTHORITY_INVALID")
    for key, value in doc["authority"].items():
        if value is not False:
            raise UpbitUniverseError(f"IDENTITY_REGISTRY_AUTHORITY_NOT_FALSE:{key}")

    source = doc["source_candidate_packet"]
    evidence_source = doc["source_identity_evidence"]
    if not isinstance(source, dict) or not isinstance(evidence_source, dict):
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_INVALID")
    packet_path = _resolve_repo_path(source.get("path"), "IDENTITY_REGISTRY_SOURCE_PACKET")
    evidence_path = _resolve_repo_path(evidence_source.get("path"), "IDENTITY_REGISTRY_SOURCE_EVIDENCE")
    if _file_sha(packet_path) != source.get("file_sha256"):
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_PACKET_FILE_HASH_MISMATCH")
    if _file_sha(evidence_path) != evidence_source.get("file_sha256"):
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_EVIDENCE_HASH_MISMATCH")

    packet = _read_json(packet_path)
    stored_payload_hash = packet.get("payload_sha256")
    if stored_payload_hash != source.get("payload_sha256"):
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_PACKET_PAYLOAD_PIN_MISMATCH")
    if payload_sha256({key: value for key, value in packet.items() if key != "payload_sha256"}) != stored_payload_hash:
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_PACKET_SELF_HASH_MISMATCH")
    if packet.get("review_status") != source.get("review_status"):
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_REVIEW_STATUS_MISMATCH")
    if packet.get("snapshot_date") != source.get("snapshot_date"):
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_SNAPSHOT_DATE_MISMATCH")
    if packet.get("evaluation_as_of") != source.get("evaluation_as_of"):
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_EVALUATION_DATE_MISMATCH")

    mappings = doc.get("mappings")
    if not isinstance(mappings, dict) or not mappings:
        raise UpbitUniverseError("IDENTITY_REGISTRY_MAPPINGS_INVALID")
    if sorted(mappings) != list(mappings):
        raise UpbitUniverseError("IDENTITY_REGISTRY_MAPPINGS_NOT_SORTED")
    if any(not _KRW_MARKET_RE.fullmatch(str(market)) for market in mappings):
        raise UpbitUniverseError("IDENTITY_REGISTRY_MARKET_INVALID")
    if any(not isinstance(asset, str) or not asset for asset in mappings.values()):
        raise UpbitUniverseError("IDENTITY_REGISTRY_CANONICAL_ID_INVALID")
    if len(set(mappings.values())) != len(mappings):
        raise UpbitUniverseError("IDENTITY_REGISTRY_DUPLICATE_CANONICAL_TARGET")
    source_mappings = {
        row["market"]: row["canonical_asset_id"]
        for row in packet.get("registry_candidates", [])
    }
    if source_mappings != mappings:
        raise UpbitUniverseError("IDENTITY_REGISTRY_SOURCE_MAPPING_MISMATCH")
    held_markets = {row.get("market") for row in packet.get("hold_list", [])}
    if held_markets & set(mappings):
        raise UpbitUniverseError("IDENTITY_REGISTRY_HELD_MARKET_PROMOTED")
    return copy.deepcopy(doc)


def _policy_approval_effective(document: dict, evaluation_as_of: str, *, date_field: str) -> bool:
    """Plain date-gated ``approval_status`` check. Used ONLY for
    ``policy`` (``config/upbit_tradeable_universe_policy.json``), which
    P3-12-GOV-05 does not touch -- this function has no exact-hash binding
    at all and must never be used for the identity registry or taxonomy.
    See ``_identity_taxonomy_exact_bound_effective`` below, which is the
    ONLY function those two document kinds may go through; there is no
    shared function or boolean flag a new identity/taxonomy consumer could
    accidentally call into this weaker path with.
    """
    if document.get("approval_status") != "RATIFIED":
        return False
    effective = document.get(date_field)
    # Synthetic fixtures predating effective-dated ratification remain valid
    # for unit-level classifier tests. Real loaded RATIFIED documents are
    # required by their loaders above to carry the field.
    return effective is None or (isinstance(effective, str) and effective <= evaluation_as_of)


def _identity_taxonomy_exact_bound_effective(
    document: dict, evaluation_as_of: str, *, date_field: str, content_field: str,
) -> bool:
    """The ONLY approval-effectiveness check for the identity registry
    (``content_field="mappings"``) or taxonomy (``content_field="records"``).
    Unlike ``_policy_approval_effective``, there is no boolean toggle here
    to accidentally omit -- every call always runs the full exact-hash
    binding first. ``document.approval_status == "RATIFIED"`` is necessary
    but never sufficient by itself: the document's own content AND
    currently-running code must both independently resolve through
    ``governance/upbit_exact_release_binding.py``'s two one-way chains
    (content chain + code chain) before the date check even runs. This is
    P3-12-GOV-04's P1 finding: editing ``approval_status`` back to
    ``"RATIFIED"`` on a document whose content was never re-approved must
    never revive authority on its own.
    """
    try:
        if not EXACT_RELEASE_BINDING.validate_exact_release(document, content_field=content_field):
            return False
    except EXACT_RELEASE_BINDING.ExactReleaseBindingError:
        return False
    if document.get("approval_status") != "RATIFIED":
        return False
    effective = document.get(date_field)
    return effective is None or (isinstance(effective, str) and effective <= evaluation_as_of)


def effective_identity_mapping(registry: dict, evaluation_as_of: str) -> dict:
    if not _identity_taxonomy_exact_bound_effective(
        registry, evaluation_as_of, date_field="effective_from", content_field="mappings",
    ):
        return {}
    return copy.deepcopy(registry["mappings"])


def _decimal(value, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise UpbitUniverseError(f"DECIMAL_INVALID:{label}:{value!r}") from exc


def _taxonomy_category(canonical_asset_id: str, as_of: str, taxonomy: dict) -> str | None:
    matches = []
    for row in taxonomy["records"]:
        if row.get("canonical_asset_id") != canonical_asset_id:
            continue
        start = row.get("effective_from")
        end = row.get("effective_to")
        if not isinstance(start, str) or start > as_of:
            continue
        if end is not None and end < as_of:
            continue
        matches.append(row)
    if len(matches) > 1:
        raise UpbitUniverseError(f"TAXONOMY_RECORD_OVERLAP:{canonical_asset_id}")
    return matches[0]["category"] if matches else None


# ---------------------------------------------------------------------------
# Snapshot parsing -- structural hash validation is reused unchanged from the
# capture module; this section only turns validated raw bytes into normalized
# per-market metrics.
# ---------------------------------------------------------------------------

def _read_gz_json(snapshot_dir: Path, relative_gz: str):
    import gzip
    raw = gzip.open(snapshot_dir / relative_gz, "rb").read()
    return json.loads(raw, parse_float=Decimal, parse_int=int)


def _dedupe_by_market(rows: list, key: str = "market") -> tuple[dict, dict]:
    out: dict = {}
    duplicates: dict = {}
    for row in rows:
        code = row.get(key)
        if code in out:
            duplicates[code] = duplicates.get(code, 1) + 1
            continue
        out[code] = row
    return out, duplicates


def load_snapshot_core(snapshot_dir: Path, contract: dict | None = None) -> dict:
    """Validate a captured snapshot directory's hashes (fail-closed on any
    tamper/mismatch) and parse it into a normalized per-market metrics dict.
    Deterministic: a pure function of the bytes already committed to disk.
    """
    snapshot_dir = Path(snapshot_dir)
    contract = contract or UPBIT_CAPTURE.load_contract(CAPTURE_CONTRACT_PATH)
    manifest = UPBIT_CAPTURE.validate_snapshot(snapshot_dir)

    market_all_rows, market_all_dupes = _dedupe_by_market(
        _read_gz_json(snapshot_dir, contract["market_all_raw_file"])
    )
    ticker_rows, ticker_dupes = _dedupe_by_market(
        _read_gz_json(snapshot_dir, contract["ticker_raw_file"])
    )
    orderbook_rows, orderbook_dupes = _dedupe_by_market(
        _read_gz_json(snapshot_dir, contract["orderbook_raw_file"])
    )

    candles_by_market: dict = {}
    candle_dupes: dict = {}
    bundle_path = snapshot_dir / contract["candles_bundle_raw_file"]
    import gzip
    raw_bundle = gzip.open(bundle_path, "rb").read()
    for line in raw_bundle.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        market = record["market"]
        if market in candles_by_market:
            candle_dupes[market] = candle_dupes.get(market, 1) + 1
            continue
        body = base64.b64decode(record["body_b64"])
        if hashlib.sha256(body).hexdigest() != record["response_sha256"]:
            raise UpbitUniverseError(f"CANDLE_BODY_HASH_MISMATCH:{market}")
        candles_by_market[market] = json.loads(body, parse_float=Decimal, parse_int=int)

    lookback_days = contract.get("turnover_lookback_finalized_days", 30)
    all_codes = set(market_all_rows) | set(manifest.get("markets", []))
    # manifest["markets"] is already KRW-only (the capture script's own
    # krw_markets() filter). market_all_rows is the raw, unfiltered
    # GET /v1/market/all archive and legitimately includes BTC-/USDT-quoted
    # pairs (e.g. a real market like "BTC-0G") -- those are out of P3-12's
    # declared "Upbit KRW 현물" scope and must never reach classification or
    # identity review (identity/upbit_market_identity_proposal.py's
    # default_candidate_canonical_asset_id() requires a KRW- prefix and
    # raises for anything else). Exclude them here, once, at the source.
    non_krw_excluded = sorted(code for code in all_codes if not _KRW_MARKET_RE.fullmatch(code))
    markets: dict = {}
    for market in sorted(all_codes - set(non_krw_excluded)):
        row = market_all_rows.get(market)
        entry: dict = {"market": market}
        if row is None:
            entry["market_all_available"] = False
        else:
            entry["market_all_available"] = True
            entry["korean_name"] = row.get("korean_name")
            entry["english_name"] = row.get("english_name")
            event = row.get("market_event") or {}
            entry["market_event_warning"] = event.get("warning")
            caution = event.get("caution") or {}
            entry["market_event_caution_any"] = any(bool(v) for v in caution.values()) if caution else False
            entry["market_event_caution_flags"] = caution

        orderbook = orderbook_rows.get(market)
        if orderbook is None or not orderbook.get("orderbook_units"):
            entry["orderbook_available"] = False
        else:
            entry["orderbook_available"] = True
            units = orderbook["orderbook_units"]
            entry["best_bid"] = units[0]["bid_price"]
            entry["best_ask"] = units[0]["ask_price"]
            entry["ask_levels"] = [
                {"price": unit["ask_price"], "size": unit["ask_size"]} for unit in units
            ]

        candles = candles_by_market.get(market)
        if candles is None or not isinstance(candles, list) or not candles:
            entry["candles_available"] = False
        else:
            entry["candles_available"] = True
            entry["observed_daily_candle_count"] = len(candles)
            finalized = candles[1:1 + lookback_days]  # exclude today (index 0), same T-1 discipline as Kraken
            entry["trailing_turnover_finalized_day_count"] = len(finalized)
            entry["trailing_30d_krw_turnover"] = sum(
                (Decimal(str(row.get("candle_acc_trade_price", 0))) for row in finalized), Decimal(0)
            )

        markets[market] = entry

    return {
        "snapshot_date": manifest["vintage_date"],
        "available_at": manifest["downloaded_at_utc"],
        "capture_version": manifest["capture_version"],
        "manifest_sha256": _file_sha(snapshot_dir / "_manifest.json"),
        "markets": markets,
        "duplicate_market_codes": {
            "market_all": market_all_dupes, "ticker": ticker_dupes,
            "orderbook": orderbook_dupes, "candles": candle_dupes,
        },
        "non_krw_market_codes_excluded": non_krw_excluded,
        "component_hashes": manifest["checksums"],
    }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _spread_bps(best_bid: Decimal, best_ask: Decimal) -> Decimal | None:
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return None
    mid = (best_bid + best_ask) / 2
    if mid == 0:
        return None
    return (best_ask - best_bid) / mid * Decimal(10000)


def _estimate_slippage_bps(ask_levels: list, best_ask: Decimal, notional_krw: Decimal) -> Decimal | None:
    """Volume-weighted average execution price for a market buy of
    ``notional_krw``, walking the captured ask side level by level, versus
    the best ask. Returns ``None`` (fail closed, never a silent estimate)
    when the captured depth cannot fill the requested notional.
    """
    remaining = notional_krw
    qty_filled = Decimal(0)
    notional_filled = Decimal(0)
    for level in ask_levels:
        price = Decimal(str(level["price"]))
        size = Decimal(str(level["size"]))
        if price <= 0 or size <= 0:
            continue
        level_notional = price * size
        take_notional = level_notional if level_notional <= remaining else remaining
        qty_filled += take_notional / price
        notional_filled += take_notional
        remaining -= take_notional
        if remaining <= 0:
            break
    if remaining > 0 or qty_filled <= 0 or best_ask <= 0:
        return None
    avg_price = notional_filled / qty_filled
    return (avg_price - best_ask) / best_ask * Decimal(10000)


def build_classification(
    core: dict,
    *,
    evaluation_as_of: str,
    policy: dict,
    taxonomy: dict,
    ratified_identity_registry: dict | None = None,
    blocked_markets: set | None = None,
    kraken_known_canonical_ids: set | None = None,
) -> dict:
    if not isinstance(evaluation_as_of, str) or not _DATE_RE.fullmatch(evaluation_as_of):
        raise UpbitUniverseError("EVALUATION_AS_OF_INVALID")
    available_at = core.get("available_at")
    if not isinstance(available_at, str) or not _UTC_RE.fullmatch(available_at):
        raise UpbitUniverseError("CORE_AVAILABLE_AT_INVALID")
    available_at_dt = dt.datetime.strptime(available_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    evaluation_dt = dt.datetime.strptime(evaluation_as_of + "T23:59:59Z", "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc
    )
    if available_at_dt > evaluation_dt:
        raise UpbitUniverseError("AVAILABLE_AT_FUTURE_DATED")

    ratified_identity_registry = ratified_identity_registry or {}
    blocked_markets = blocked_markets or set()
    # SAFETY INVARIANT: kraken_known_canonical_ids is READ-ONLY labeling.
    # It MUST NEVER be read anywhere below this point except to compute the
    # `kraken_cross_exchange_reference` display field. See
    # test_upbit_tradeable_universe.py::test_kraken_presence_never_promotes.
    kraken_known_canonical_ids = kraken_known_canonical_ids or set()

    policy_ratified = _policy_approval_effective(policy, evaluation_as_of, date_field="effective_date")
    taxonomy_ratified = _identity_taxonomy_exact_bound_effective(
        taxonomy, evaluation_as_of, date_field="effective_from", content_field="records",
    )
    min_listing_days = int(policy["min_listing_history_finalized_days"])
    min_turnover = _decimal(policy["min_30d_avg_krw_turnover"], "min_30d_avg_krw_turnover")
    max_spread = _decimal(policy["max_spread_bps"], "max_spread_bps")
    max_slippage = _decimal(policy["max_estimated_paper_slippage_bps"], "max_estimated_paper_slippage_bps")
    notional = _decimal(policy["paper_slippage_estimate_notional_krw"], "paper_slippage_estimate_notional_krw")
    max_age_hours = _decimal(policy["max_capture_age_hours"], "max_capture_age_hours")
    stale = (evaluation_dt - available_at_dt) > dt.timedelta(hours=float(max_age_hours))

    rows = []
    for market in sorted(core["markets"]):
        entry = core["markets"][market]
        reason = None
        state = STATE_OBSERVATION_POOL
        candidate_canonical_asset_id = None

        if market in blocked_markets:
            state, reason = STATE_BLOCKED, "IDENTITY_COLLISION"
        elif not entry.get("market_all_available"):
            reason = "MISSING_FIELD:market_all"
        elif entry.get("market_event_warning") is None:
            reason = "MISSING_FIELD:market_event"
        elif not entry.get("orderbook_available"):
            reason = "MISSING_FIELD:orderbook"
        elif not entry.get("candles_available"):
            reason = "MISSING_FIELD:candles"
        elif entry["market_event_warning"] is True:
            reason = "INVESTMENT_WARNING_ACTIVE"
        else:
            candidate_canonical_asset_id = ratified_identity_registry.get(market)
            if candidate_canonical_asset_id is None:
                reason = "IDENTITY_UNRATIFIED"
            elif not taxonomy_ratified:
                reason = "TAXONOMY_UNRATIFIED"
            else:
                category = _taxonomy_category(candidate_canonical_asset_id, evaluation_as_of, taxonomy)
                if category is None:
                    reason = "TAXONOMY_UNKNOWN"
                elif category != taxonomy["eligible_category"]:
                    reason = f"TAXONOMY_EXCLUDED:{category}"
                elif not policy_ratified:
                    reason = "POLICY_UNRATIFIED"
                elif stale:
                    reason = "STALE_CAPTURE"
                elif entry["observed_daily_candle_count"] < min_listing_days:
                    reason = "LISTING_HISTORY_BELOW_THRESHOLD"
                elif entry["trailing_turnover_finalized_day_count"] < policy["turnover_lookback_finalized_days"]:
                    reason = "TURNOVER_HISTORY_INCOMPLETE"
                # The ratified threshold is the mean finalized daily KRW
                # turnover across the complete 30-day lookback, not the
                # 30-day aggregate. Keep the aggregate in the packet for
                # schema compatibility and compute the comparison exactly
                # here from the already-validated finalized-day count.
                elif (
                    entry["trailing_30d_krw_turnover"]
                    / Decimal(entry["trailing_turnover_finalized_day_count"])
                ) < min_turnover:
                    reason = "TURNOVER_BELOW_THRESHOLD"
                else:
                    spread_bps = _spread_bps(Decimal(str(entry["best_bid"])), Decimal(str(entry["best_ask"])))
                    if spread_bps is None:
                        reason = "SPREAD_NOT_COMPUTABLE"
                    elif spread_bps > max_spread:
                        reason = "SPREAD_ABOVE_THRESHOLD"
                    else:
                        state = STATE_TRADEABLE_UNIVERSE
                        slippage_bps = _estimate_slippage_bps(
                            entry["ask_levels"], Decimal(str(entry["best_ask"])), notional
                        )
                        if slippage_bps is None:
                            reason = "SLIPPAGE_NOT_COMPUTABLE"
                        elif slippage_bps > max_slippage:
                            reason = "SLIPPAGE_ABOVE_THRESHOLD"
                        else:
                            state = STATE_PAPER_ELIGIBLE
                            reason = "PAPER_ELIGIBLE_ALL_GATES_PASSED"

        rows.append({
            "market": market,
            "state": state,
            "reason": reason,
            "candidate_canonical_asset_id": candidate_canonical_asset_id,
            "market_event_warning": entry.get("market_event_warning"),
            "market_event_caution_any": entry.get("market_event_caution_any"),
            "observed_daily_candle_count": entry.get("observed_daily_candle_count"),
            "trailing_30d_krw_turnover": (
                str(entry["trailing_30d_krw_turnover"]) if "trailing_30d_krw_turnover" in entry else None
            ),
            "kraken_cross_exchange_reference": (
                candidate_canonical_asset_id is not None and candidate_canonical_asset_id in kraken_known_canonical_ids
            ),
            "authority": dict(_ROW_AUTHORITY),
        })

    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "snapshot_date": core["snapshot_date"],
        "evaluation_as_of": evaluation_as_of,
        "available_at": available_at,
        "manifest_sha256": core["manifest_sha256"],
        "policy_version": policy.get("policy_version"),
        "policy_ratified": policy_ratified,
        "taxonomy_version": taxonomy.get("policy_version"),
        "taxonomy_ratified": taxonomy_ratified,
        "duplicate_market_codes": core.get("duplicate_market_codes", {}),
        "summary": {
            "market_count": len(rows),
            "observation_pool_count": sum(1 for r in rows if r["state"] == STATE_OBSERVATION_POOL),
            "tradeable_universe_count": sum(1 for r in rows if r["state"] == STATE_TRADEABLE_UNIVERSE),
            "paper_eligible_count": sum(1 for r in rows if r["state"] == STATE_PAPER_ELIGIBLE),
            "blocked_count": sum(1 for r in rows if r["state"] == STATE_BLOCKED),
        },
        "markets": rows,
        "authority": dict(_ROW_AUTHORITY),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet
