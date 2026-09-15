#!/usr/bin/env python3
"""User-ratified capital rotation confirmation layer (v1, 2026-09-15).

Ratification: ``evidence/authority/capital_rotation_rules_v1_user_ratification_20260915.json``
(sha256 bound in ``config/rotation_confirmation_policy_v1.json``). CRYPTO is
RATIFIED, US PROVISIONAL, KR TEMPORARY; the common T1/T2/NEUTRAL wiring and
release handling are RATIFIED.

What this layer adds on top of the CIO-ratified per-observation 9-cell mapping
(``rotation/rotation_state_policy_ratification.py``): a *confirmation* rule over
consecutive observations of the same entity.

* STRONG_CONFIRMED -- TOP on 2 consecutive observations (state on the day it
  is confirmed);
* STRONG_HELD      -- confirmed earlier and not released yet;
* STRONG_RELEASED  -- was strong and today is BOTTOM, or non-TOP on 2
  consecutive observations (state on the release observation only);
* EMERGING_WATCH   -- not strong, not TOP, rank improved by the configured
  number of places versus N observations earlier (display only);
* NEUTRAL          -- everything else.

TOP/BOTTOM per market: CRYPTO rank 1 / rank 3 of BTC-ETH-ALT by 30d strength vs
BTC; US top 3 / bottom 3 of the 11 SPDR sector ETFs by 20-session strength vs
SPY; KR top 3 / bottom 3 within each ratified benchmark scope by the existing
1-session relative strength (TEMPORARY basis; the 20-session basis switch is
present in config and refused if enabled).

Evidence discipline:

* inputs are committed daily evidence only (no vendor call, no network);
* the packet for date ``d`` is computed from observations dated ``<= d`` only;
* an UNKNOWN observation is treated as missing (no state inference), and a gap
  above the market's maximum observation gap resets streaks and strong status;
* packets carry no wall-clock timestamps, so a rebuild is byte-identical and
  committed packets are append-only (a different rebuild fails closed).

Nothing here places orders, changes allocation v2, or touches the crypto PAPER
runtime chain pinned by the private repository.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, getcontext
import glob
import hashlib
import json
from pathlib import Path
import sys
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
POLICY_RELATIVE_PATH = "config/rotation_confirmation_policy_v1.json"
STATE_MAPPING_RELATIVE_PATH = "config/rotation_state_policy_ratification_contract.json"
POLICY_SCHEMA_VERSION = "rotation_confirmation_policy/1"
PACKET_SCHEMA_VERSION = "rotation_confirmation_packet/1"
PROJECTION_SCHEMA_VERSION = "rotation_confirmation_portal_ch02/1"
EVIDENCE_RELATIVE_ROOT = "evidence/rotation/confirmation"
PORTAL_RELATIVE_PATH = "data/latest_rotation_confirmation_portal_ch02.json"
MARKETS = ("CRYPTO", "KR", "US")
STATES = (
    "STRONG_CONFIRMED", "STRONG_HELD", "STRONG_RELEASED", "EMERGING_WATCH", "NEUTRAL",
)
STRONG_STATES = ("STRONG_CONFIRMED", "STRONG_HELD")
RULE_REF_VERSION = 1

getcontext().prec = 28


class RotationConfirmationError(ValueError):
    pass


def _fail(code: str, detail: str = "") -> None:
    raise RotationConfirmationError(f"{code}:{detail}" if detail else code)


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def render_json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("JSON_READ_FAILED", f"{path}:{exc}")


def _date(value, code: str) -> dt.date:
    try:
        parsed = dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        _fail(code, str(value))
    if parsed.isoformat() != value:
        _fail(code, str(value))
    return parsed


def _decimal(value, code: str) -> Decimal:
    if not isinstance(value, str):
        _fail(code, repr(value))
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        _fail(code, value)
    if not parsed.is_finite():
        _fail(code, value)
    return parsed


def _relative(path: Path, root: Path) -> str:
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


# ---------------------------------------------------------------------------
# Policy (bound to the user ratification record sha)
# ---------------------------------------------------------------------------

def load_policy(root: Path = ROOT, path: Optional[Path] = None) -> dict:
    root = Path(root)
    path = root / POLICY_RELATIVE_PATH if path is None else Path(path)
    policy = _read_json(path)
    if not isinstance(policy, dict) or policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        _fail("POLICY_SCHEMA_INVALID")
    record = policy.get("ratification_record") or {}
    record_path = root / str(record.get("repo_path", ""))
    if not record_path.is_file():
        _fail("RATIFICATION_RECORD_MISSING", str(record.get("repo_path")))
    if file_sha256(record_path) != record.get("sha256"):
        _fail("RATIFICATION_RECORD_SHA_MISMATCH")
    ratification = _read_json(record_path)
    if ratification.get("id") != record.get("record_id"):
        _fail("RATIFICATION_RECORD_ID_MISMATCH")
    if (ratification.get("source_document") or {}).get("sha256") != record.get("source_document_sha256"):
        _fail("RATIFICATION_SOURCE_DOCUMENT_SHA_MISMATCH")
    decisions = ratification.get("decisions") or {}
    expected_status = {"CRYPTO": "RATIFIED", "US": "PROVISIONAL", "KR": "TEMPORARY"}
    for market, status in expected_status.items():
        if (decisions.get(market) or {}).get("status") != status:
            _fail("RATIFICATION_STATUS_MISMATCH", market)
        if policy["markets"][market]["ratification_status"] != status:
            _fail("POLICY_STATUS_MISMATCH", market)
    for key, section in (("COMMON", "common"), ("RELEASE_HANDLING", "release_handling")):
        if (decisions.get(key) or {}).get("status") != "RATIFIED" or policy[section]["ratification_status"] != "RATIFIED":
            _fail("RATIFICATION_STATUS_MISMATCH", key)
    if tuple(policy.get("state_vocabulary", ())) != STATES:
        _fail("POLICY_STATE_VOCABULARY_MISMATCH")
    if tuple(policy.get("decision_eligible_states", ())) != STRONG_STATES:
        _fail("POLICY_ELIGIBLE_STATES_MISMATCH")
    for key in ("t1_selection_states", "t2_c5_pass_states"):
        if tuple(policy["common"][key]) != STRONG_STATES:
            _fail("POLICY_COMMON_STATES_MISMATCH", key)
    confirmation = policy.get("confirmation") or {}
    if (
        confirmation.get("enter_consecutive_top_observations") != 2
        or confirmation.get("release_consecutive_non_top_observations") != 2
        or confirmation.get("release_on_single_bottom_observation") is not True
    ):
        _fail("POLICY_CONFIRMATION_RULE_MISMATCH")
    release = policy["release_handling"]
    if release.get("new_buy_stop") is not True or release.get("forced_exit") is not False:
        _fail("POLICY_RELEASE_HANDLING_MISMATCH")
    if set(policy["markets"]) != set(MARKETS):
        _fail("POLICY_MARKETS_MISMATCH")
    kr = policy["markets"]["KR"]
    if kr["strength_basis"]["twenty_session_basis"]["enabled"] is not False:
        # The switch exists for the future KRX-history re-verification; enabling
        # it needs a new user ratification and a history source, neither exists.
        _fail("KR_TWENTY_SESSION_BASIS_NOT_RATIFIED")
    if kr["strength_basis"]["active"] != "ONE_SESSION_RATIFIED_TEMPORARY" or kr["strength_basis"]["lookback_sessions"] != 1:
        _fail("KR_STRENGTH_BASIS_MISMATCH")
    kr_policy_path = root / kr["source"]["sector_policy_path"]
    if not kr_policy_path.is_file() or file_sha256(kr_policy_path) != kr["source"]["sector_policy_sha256"]:
        _fail("KR_SECTOR_POLICY_SHA_MISMATCH")
    if policy["markets"]["CRYPTO"]["top_rank_max"] != 1 or policy["markets"]["CRYPTO"]["bottom_rank_min"] != 3:
        _fail("CRYPTO_BUCKET_RULE_MISMATCH")
    for market in ("US", "KR"):
        if policy["markets"][market]["top_count"] != 3 or policy["markets"][market]["bottom_count"] != 3:
            _fail("TOP_BOTTOM_COUNT_MISMATCH", market)
    if len(policy["markets"]["US"]["entities"]) != 11 or "SMH" in policy["markets"]["US"]["entities"]:
        _fail("US_SPDR_ENTITIES_MISMATCH")
    return policy


def policy_identity(policy: dict) -> dict:
    return {
        "policy_id": policy["policy_id"],
        "policy_sha256": payload_sha256(policy),
        "ratification_record_id": policy["ratification_record"]["record_id"],
        "ratification_record_sha256": policy["ratification_record"]["sha256"],
    }


def load_state_mapping(root: Path = ROOT) -> dict:
    contract = _read_json(Path(root) / STATE_MAPPING_RELATIVE_PATH)
    mapping = contract.get("state_by_bucket_transition")
    if not isinstance(mapping, dict) or len(mapping) != 9:
        _fail("STATE_MAPPING_INVALID")
    return dict(mapping)


# ---------------------------------------------------------------------------
# rule_refs (inline shape of governance/rule_refs.py)
# ---------------------------------------------------------------------------

def rule_ref(policy: dict, rule_id: str, role: str) -> dict:
    # TODO(rule-registry): switch to governance.rule_refs.make_rule_ref once the
    # parallel rule registry PR merges. Same closed field set; registry_sha256
    # stays null until config/rule_registry_v1.json exists on main.
    return {
        "rule_id": rule_id,
        "version": RULE_REF_VERSION,
        "registry_sha256": None,
        "source_record_sha256": policy["ratification_record"]["sha256"],
        "role": role,
    }


def entity_rule_refs(policy: dict, market: str, state: str) -> list:
    """Per-entity lineage for the entry gate: which ratified rule admits or blocks it."""
    roles = [(policy["markets"][market]["rule_id"], "APPLIED")]
    if state in STRONG_STATES:
        roles.append((policy["common"]["rule_id"], "APPLIED"))
    else:
        roles.append((policy["common"]["rule_id"], "BLOCKED_BY"))
    if state == "STRONG_RELEASED":
        roles.append((policy["release_handling"]["rule_id"], "BLOCKED_BY"))
    return sorted((rule_ref(policy, rule_id, role) for rule_id, role in roles), key=lambda r: (r["rule_id"], r["role"]))


def _entry_gate_view(policy: dict, market: str, flat: list) -> list:
    return [
        _with_coverage_recalculation({
            "scope_id": scope_id,
            "entity_id": e["entity_id"],
            "label": e["source_identity"],
            "state": e["state"],
            "decision_eligible": e["decision_eligible"],
            "state_since_date": e["state_since_date"],
            "observations_in_state": e["observations_in_state"],
            "calendar_days_in_state": e["calendar_days_in_state"],
            "release_new_buy_stop": e["release_new_buy_stop"],
            "forced_exit": False,
            "rule_refs": entity_rule_refs(policy, market, e["state"]),
        }, e.get("coverage_recalculation"))
        for scope_id, e in flat
    ]


def _with_coverage_recalculation(gate: dict, mark: Optional[dict]) -> dict:
    """Rows resting on recalculated observations carry the '재계산' mark and the P1 rule lineage."""
    if mark is None:
        return gate
    gate["coverage_recalculation"] = copy.deepcopy(mark)
    gate["rule_refs"] = sorted(
        gate["rule_refs"] + [{
            "rule_id": mark["rule_id"], "version": RULE_REF_VERSION, "registry_sha256": None,
            "source_record_sha256": mark["ratification_record_sha256"], "role": "APPLIED",
        }],
        key=lambda r: (r["rule_id"], r["role"]),
    )
    return gate


def market_rule_refs(policy: dict, market: str) -> list:
    refs = [
        rule_ref(policy, policy["markets"][market]["rule_id"], "APPLIED"),
        rule_ref(policy, policy["common"]["rule_id"], "APPLIED"),
        rule_ref(policy, policy["release_handling"]["rule_id"], "APPLIED"),
    ]
    return sorted(refs, key=lambda item: (item["rule_id"], item["role"]))


# ---------------------------------------------------------------------------
# Observation extraction from committed evidence
# ---------------------------------------------------------------------------

def _unknown(as_of: str, reason: str, sources: list) -> dict:
    return {
        "as_of_date": as_of, "status": "UNKNOWN", "unknown_reason": reason,
        "sources": sources, "scopes": {}, "aux": {},
    }


def crypto_observations(policy: dict, root: Path = ROOT) -> list:
    cfg = policy["markets"]["CRYPTO"]
    source = cfg["source"]
    result = []
    _coverage_recalc_module().reset_notices(root)
    for path in sorted(glob.glob(str(Path(root) / source["root"] / "*" / "packet.json"))):
        path = Path(path)
        packet = _read_json(path)
        as_of = packet.get("as_of_date")
        if as_of != path.parent.name:
            _fail("CRYPTO_SOURCE_DATE_MISMATCH", str(path))
        _date(as_of, "CRYPTO_SOURCE_DATE_INVALID")
        sources = [{"path": _relative(path, root), "sha256": file_sha256(path)}]
        if packet.get("contract_version") != source["contract_version"]:
            result.append(_unknown(as_of, "SOURCE_CONTRACT_VERSION_UNSUPPORTED", sources))
            continue
        windows = [w for w in packet.get("windows") or [] if w.get("window_id") == source["window_id"]]
        if len(windows) != 1:
            result.append(_unknown(as_of, "PRIMARY_WINDOW_MISSING", sources))
            continue
        window = windows[0]
        aux = {"daily_points": _crypto_daily_points(window, cfg)}
        recalculation = None
        if window.get("status") != "OBSERVED_UNCLASSIFIED":
            # RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1: the 30-day strength input only
            # (never the regime LEADERSHIP axis). Recalculated days never feed ``aux``.
            outcome = _crypto_coverage_recalc_outcome(root, as_of, packet, window)
            if outcome["kind"] in ("NATURAL", "UNKNOWN"):
                reason = outcome.get("unknown_reason") or f"WINDOW_{window.get('unknown_reason') or 'UNKNOWN'}"
                item = _unknown(as_of, reason, sources)
                item["aux"] = aux
                result.append(item)
                continue
            if outcome["kind"] == "COMMITTED":
                # Committed packets are preferred: a later edit elsewhere never changes their replay.
                committed = outcome["packet"]
                result.append({
                    "as_of_date": as_of, "status": "OBSERVED", "unknown_reason": None,
                    "sources": copy.deepcopy(committed["observation"]["sources"]),
                    "scopes": {
                        scope["scope_id"]: [
                            {"entity_id": e["entity_id"], "source_identity": e["source_identity"], "strength": e["strength"]}
                            for e in scope["entities"]
                        ]
                        for scope in committed["scopes"]
                    },
                    "aux": aux,
                    "coverage_recalculation": copy.deepcopy(committed["observation"]["coverage_recalculation"]),
                })
                continue
            window, recalculation = outcome["window"], outcome["mark"]
            sources = sources + [
                {"path": day["point_path"], "sha256": file_sha256(Path(root) / day["point_path"])}
                for day in recalculation["recalculated_days"]
            ]
        rows = {row.get("group_id"): row for row in (window.get("group_relative_strength") or {}).get("bucket") or []}
        entities = []
        reason = None
        for entity in cfg["entities"]:
            row = rows.get(entity)
            if row is None or row.get("status") != "OBSERVED_UNCLASSIFIED" or row.get("relative_strength_vs_btc") is None:
                reason = f"BUCKET_NOT_OBSERVED_{entity}"
                break
            _decimal(row["relative_strength_vs_btc"], "CRYPTO_STRENGTH_INVALID")
            entities.append({"entity_id": entity, "source_identity": entity, "strength": row["relative_strength_vs_btc"]})
        if reason is not None:
            item = _unknown(as_of, reason, sources)
            item["aux"] = aux
            result.append(item)
            continue
        observed = {
            "as_of_date": as_of, "status": "OBSERVED", "unknown_reason": None,
            "sources": sources, "scopes": {cfg["scope_id"]: entities}, "aux": aux,
        }
        if recalculation is not None:
            observed["coverage_recalculation"] = recalculation
        result.append(observed)
    return result


_COVERAGE_RECALC_MODULE: list = []


def _coverage_recalc_module():
    if not _COVERAGE_RECALC_MODULE:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "atlas_crypto_rotation_30d_coverage_recalc", Path(__file__).resolve().parent / "crypto_rotation_30d_coverage_recalc.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _COVERAGE_RECALC_MODULE.append(module)
    return _COVERAGE_RECALC_MODULE[0]


def _crypto_coverage_recalc_outcome(root: Path, as_of: str, packet: dict, window: dict) -> dict:
    """RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1 decision; never raises (drift -> crypto notice file)."""
    committed_path = evidence_path(root, "CRYPTO", as_of)
    committed = None
    if committed_path.exists():
        committed = _read_json(committed_path)
        if "coverage_recalculation" in (committed.get("observation") or {}):
            validate_packet(committed)  # a tampered committed packet is not drift
    return _coverage_recalc_module().rotation_override(root, as_of, packet, window, committed)


CRYPTO_COVERAGE_RECALC_NOTICE_RELATIVE_PATH = "data/rotation_confirmation_crypto_coverage_recalc_notice.json"


def _crypto_daily_points(window: dict, cfg: dict) -> list:
    points = []
    for point in window.get("daily_points") or []:
        groups = {g.get("group_id"): g for g in ((point.get("groups") or {}).get("bucket") or [])}
        values = {}
        for entity in cfg["entities"]:
            group = groups.get(entity) or {}
            value = group.get("daily_gross_return")
            if group.get("status") != "OBSERVED_UNCLASSIFIED" or not isinstance(value, str):
                values = None
                break
            values[entity] = value
        if values is not None and isinstance(point.get("as_of_date"), str):
            points.append({"as_of_date": point["as_of_date"], "gross": values})
    return points


def _us_capture_order(path: Path, root: Path):
    relative = Path(_relative(path, root)).parts
    # evidence/free_market_data/derived/<capture_date>[/<sha>]/manifest.json
    capture_date = relative[3]
    content_addressed = len(relative) == 6
    return (capture_date, 0 if content_addressed else 1, "/".join(relative))


def us_observations(policy: dict, root: Path = ROOT) -> list:
    cfg = policy["markets"]["US"]
    source = cfg["source"]
    base = Path(root) / source["root"]
    paths = [Path(p) for p in glob.glob(str(base / "*" / "manifest.json"))]
    paths += [Path(p) for p in glob.glob(str(base / "*" / "*" / "manifest.json"))]
    first_by_session = {}
    for path in sorted(paths, key=lambda p: _us_capture_order(p, root)):
        manifest = _read_json(path)
        reference = manifest.get("us_market_reference")
        if not isinstance(reference, dict):
            continue
        rows = {row.get("symbol"): row for row in reference.get("sector_etfs") or [] if isinstance(row, dict)}
        present = [rows[e] for e in cfg["entities"] if e in rows]
        if not present:
            continue
        sessions = {row.get("as_of_session_date") for row in present}
        if len(sessions) != 1:
            continue
        session = sessions.pop()
        _date(session, "US_SESSION_DATE_INVALID")
        if session in first_by_session:
            continue  # first capture of a session wins (later captures are not read)
        sources = [{
            "path": _relative(path, root),
            "us_market_reference_payload_sha256": reference.get("payload_sha256"),
        }]
        if len(present) != len(cfg["entities"]):
            first_by_session[session] = _unknown(session, "SECTOR_ETF_SET_INCOMPLETE", sources)
            continue
        entities = []
        reason = None
        for entity in cfg["entities"]:
            row = rows[entity]
            value = (row.get("relative_to_spy_pct") or {}).get(f"{source['strength_window_sessions']}_session_pct")
            count = row.get("available_session_count")
            if not isinstance(value, str) or type(count) is not int or count < source["minimum_available_session_count"]:
                reason = f"STRENGTH_NOT_OBSERVED_{entity}"
                break
            _decimal(value, "US_STRENGTH_INVALID")
            entities.append({"entity_id": entity, "source_identity": entity, "strength": value})
        if reason is not None:
            first_by_session[session] = _unknown(session, reason, sources)
            continue
        first_by_session[session] = {
            "as_of_date": session, "status": "OBSERVED", "unknown_reason": None,
            "sources": sources, "scopes": {cfg["scope_id"]: entities}, "aux": {},
        }
    return [first_by_session[key] for key in sorted(first_by_session)]


def kr_observations(policy: dict, root: Path = ROOT) -> list:
    cfg = policy["markets"]["KR"]
    source = cfg["source"]
    sector_policy = _read_json(Path(root) / source["sector_policy_path"])
    scopes = {
        scope["benchmark_identity"]: {m["series_identity"]: m["theme_id"] for m in scope["members"]}
        for scope in sector_policy["benchmark_scopes"]
    }
    result = []
    for path in sorted(glob.glob(str(Path(root) / source["root"] / "*" / "packet.json"))):
        path = Path(path)
        packet = _read_json(path)
        as_of = packet.get("observation_date")
        if as_of != path.parent.name:
            _fail("KR_SOURCE_DATE_MISMATCH", str(path))
        _date(as_of, "KR_SOURCE_DATE_INVALID")
        sources = [{"path": _relative(path, root), "payload_sha256": packet.get("payload_sha256")}]
        leadership = packet.get("leadership_packet")
        if packet.get("outcome") != "populated" or not isinstance(leadership, dict):
            result.append(_unknown(as_of, f"CONTEXT_OUTCOME_{str(packet.get('outcome')).upper()}", sources))
            continue
        if leadership.get("status") != source["leadership_status"]:
            result.append(_unknown(as_of, "LEADERSHIP_NOT_OBSERVED", sources))
            continue
        if (leadership.get("window") or {}).get("lookback_sessions") != cfg["strength_basis"]["lookback_sessions"]:
            result.append(_unknown(as_of, "STRENGTH_BASIS_MISMATCH", sources))
            continue
        rows = {}
        for row in leadership.get("relative_strength_observations") or []:
            if row.get("role") == "SECTOR":
                rows[(row.get("benchmark_identity"), row.get("series_identity"))] = row
        observed = {}
        reason = None
        for scope_id in sorted(scopes):
            entities = []
            for series_identity, theme_id in sorted(scopes[scope_id].items()):
                row = rows.get((scope_id, series_identity))
                if row is None or not isinstance(row.get("relative_strength_vs_benchmark"), str):
                    reason = "SECTOR_MEMBER_NOT_OBSERVED"
                    break
                _decimal(row["relative_strength_vs_benchmark"], "KR_STRENGTH_INVALID")
                entities.append({
                    "entity_id": theme_id, "source_identity": series_identity,
                    "strength": row["relative_strength_vs_benchmark"],
                })
            if reason is not None:
                break
            observed[scope_id] = entities
        if reason is not None:
            result.append(_unknown(as_of, reason, sources))
            continue
        result.append({
            "as_of_date": as_of, "status": "OBSERVED", "unknown_reason": None,
            "sources": sources, "scopes": observed, "aux": {},
        })
    return result


EXTRACTORS = {"CRYPTO": crypto_observations, "KR": kr_observations, "US": us_observations}


def observations_from_ledger(ledger: dict, market: str, policy: dict) -> list:
    """Bucket-only observations from a validated ``rotation_state_ledger`` value.

    Ranks and strengths are not stored in ledger records, so EMERGING_WATCH is
    not evaluable on this path; STRONG_* confirmation/release is.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "atlas_rotation_state_ledger_for_confirmation", Path(__file__).resolve().parent / "rotation_state_ledger.py"
    )
    LEDGER = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(LEDGER)

    LEDGER.validate_ledger(ledger)
    ledger_market = policy["markets"][market]["ledger_market"]
    by_date = {}
    for record in ledger["records"]:
        if record["market"] != ledger_market:
            continue
        bucket = record["structural_bucket_transition"].split("_TO_")[1]
        day = by_date.setdefault(record["as_of_date"], {})
        day.setdefault(record["scope_id"], []).append({
            "entity_id": record["entity_id"], "source_identity": record["source_identity"],
            "strength": None, "bucket": bucket,
        })
    return [
        {
            "as_of_date": day, "status": "OBSERVED", "unknown_reason": None,
            "sources": [{"ledger_payload_sha256": ledger["payload_sha256"]}],
            "scopes": {scope: sorted(rows, key=lambda r: r["entity_id"]) for scope, rows in scopes.items()},
            "aux": {},
        }
        for day, scopes in sorted(by_date.items())
    ]


# ---------------------------------------------------------------------------
# Ranking, buckets, confirmation state machine
# ---------------------------------------------------------------------------

def rank_entities(entities: list, tie_break: str) -> list:
    key_field = "source_identity" if tie_break == "SERIES_IDENTITY_ASC" else "entity_id"
    ordered = sorted(entities, key=lambda e: (-_decimal(e["strength"], "STRENGTH_INVALID"), e[key_field]))
    return [dict(entity, rank=index) for index, entity in enumerate(ordered, 1)]


def bucket_for(market_cfg: dict, rank: int, size: int) -> str:
    if "top_rank_max" in market_cfg:
        if rank <= market_cfg["top_rank_max"]:
            return "TOP"
        if rank >= market_cfg["bottom_rank_min"]:
            return "BOTTOM"
        return "MIDDLE"
    if rank <= market_cfg["top_count"]:
        return "TOP"
    if rank > size - market_cfg["bottom_count"]:
        return "BOTTOM"
    return "MIDDLE"


def _fresh_tracker() -> dict:
    return {
        "top_streak": 0, "non_top_streak": 0, "strong": False, "strong_since": None,
        "strong_observations": 0, "state": None, "state_since": None, "state_observations": 0,
        "last_bucket": None, "ranks": [], "last_release_on": None,
        "top_recalc": [], "non_top_recalc": [], "episode_recalc": [],
    }


def _crypto_lagging(daily: dict, as_of: str, cfg: dict) -> dict:
    lag_cfg = cfg["lagging_warning"]
    n, m = lag_cfg["ratio_sma_days"], lag_cfg["momentum_lookback_days"]
    needed = n + m
    end = _date(as_of, "CRYPTO_LAGGING_DATE_INVALID")
    days = [(end - dt.timedelta(days=offset)).isoformat() for offset in range(needed - 1, -1, -1)]
    result = {}
    missing = [day for day in days if day not in daily]
    for entity in cfg["entities"]:
        if entity in lag_cfg["excluded_entities"]:
            result[entity] = {"status": "EXCLUDED_BENCHMARK", "quadrant": None, "rs_ratio": None, "rs_momentum": None, "lagging_warning": None}
            continue
        if missing:
            result[entity] = {
                "status": "UNKNOWN", "reason": "INSUFFICIENT_CONTIGUOUS_DAILY_POINTS",
                "required_days": needed, "missing_day_count": len(missing),
                "quadrant": None, "rs_ratio": None, "rs_momentum": None, "lagging_warning": None,
            }
            continue
        ratio = Decimal(1)
        series = []
        for day in days:
            gross = daily[day]
            ratio = ratio * _decimal(gross[entity], "CRYPTO_GROSS_INVALID") / _decimal(gross["BTC"], "CRYPTO_GROSS_INVALID")
            series.append(ratio)

        def rs_ratio(index: int) -> Decimal:
            window = series[index - n + 1:index + 1]
            return Decimal(100) * series[index] / (sum(window) / Decimal(n))

        current = rs_ratio(len(series) - 1)
        prior = rs_ratio(len(series) - 1 - m)
        momentum = Decimal(100) * current / prior
        if current > 100 and momentum > 100:
            quadrant = "LEADING"
        elif current > 100:
            quadrant = "WEAKENING"
        elif momentum <= 100:
            quadrant = "LAGGING"
        else:
            quadrant = "IMPROVING"
        result[entity] = {
            "status": "OBSERVED", "quadrant": quadrant,
            "rs_ratio": format(current.quantize(Decimal("0.000001")), "f"),
            "rs_momentum": format(momentum.quantize(Decimal("0.000001")), "f"),
            "lagging_warning": quadrant == "LAGGING",
        }
    return result


def build_market_packets(policy: dict, market: str, observations: list, mapping: dict) -> list:
    """Replay observations in date order; one packet per observation date."""
    cfg = policy["markets"][market]
    gap = cfg["maximum_observation_gap_days"]
    watch_cfg = cfg["emerging_watch"]
    identity = policy_identity(policy)
    refs = market_rule_refs(policy, market)
    dates = [item["as_of_date"] for item in observations]
    if dates != sorted(set(dates)):
        _fail("OBSERVATION_DATES_NOT_UNIQUE_ASCENDING", market)
    trackers = {}
    previous_observed = None
    chain_start = None
    chain_index = -1
    chain_inputs = []
    crypto_daily = {}
    recalculation_refs = {}
    packets = []
    for observation in observations:
        as_of = observation["as_of_date"]
        day = _date(as_of, "OBSERVATION_DATE_INVALID")
        chain_inputs.append({"as_of_date": as_of, "status": observation["status"], "sources": observation["sources"]})
        if market == "CRYPTO":
            for point in observation["aux"].get("daily_points", []):
                if point["as_of_date"] <= as_of and point["as_of_date"] not in crypto_daily:
                    crypto_daily[point["as_of_date"]] = point["gross"]
        packet = {
            "schema_version": PACKET_SCHEMA_VERSION,
            "market": market,
            "as_of_date": as_of,
            "rule_id": cfg["rule_id"],
            "ratification_status": cfg["ratification_status"],
            "evidence_badge_ko": cfg["evidence_badge_ko"],
            "policy": identity,
            "decisions_effective_from": policy["decisions_effective_from"],
            "evidence_phase": (
                "DECISION_EFFECTIVE" if as_of >= policy["decisions_effective_from"]
                else "PRE_DECISION_EFFECTIVE_REPLAY"
            ),
            "maximum_observation_gap_days": gap,
            "observation": {
                "status": observation["status"],
                "unknown_reason": observation["unknown_reason"],
                "sources": observation["sources"],
            },
            "strength_basis": copy.deepcopy(cfg.get("strength_basis")),
            "pending_definitions": list(cfg["pending_definitions"]),
            "rule_refs": copy.deepcopy(refs),
            "authority": copy.deepcopy(policy["authority"]),
        }
        recalculation = observation.get("coverage_recalculation")
        if recalculation is not None:  # only recalculated observations carry the key (others stay byte-identical)
            packet["observation"]["coverage_recalculation"] = copy.deepcopy(recalculation)
        recalc_date = as_of if recalculation is not None else None
        if observation["status"] != "OBSERVED":
            packet["chain"] = {
                "chain_start_date": chain_start, "observation_index": None,
                "previous_observation_date": previous_observed, "reset": False,
                "last_observed_as_of_date": previous_observed,
            }
            packet["scopes"] = []
            packet["summary"] = _summary([])
            packet["decision_view"] = _decision_view([])
            packet["entry_gate_view"] = []
            packet["input_chain_sha256"] = payload_sha256(chain_inputs)
            packet["payload_sha256"] = payload_sha256(packet)
            packets.append(packet)
            continue
        reset = previous_observed is None or (day - _date(previous_observed, "DATE")).days > gap
        lapsed = set()
        if reset:
            lapsed = {key for key, tracker in trackers.items() if tracker["strong"]}
            trackers = {}
            chain_start = as_of
            chain_index = 0
        else:
            chain_index += 1
        lagging = _crypto_lagging(crypto_daily, as_of, cfg) if market == "CRYPTO" else None
        if recalculation is not None:
            recalculation_refs[as_of] = {"rule_id": recalculation["rule_id"], "sha256": recalculation["ratification_record_sha256"]}
        scopes_out = []
        for scope_id in sorted(observation["scopes"]):
            rows = observation["scopes"][scope_id]
            if all(row.get("bucket") for row in rows) and all(row.get("strength") is None for row in rows):
                ranked = [dict(row, rank=None) for row in sorted(rows, key=lambda r: r["entity_id"])]
            else:
                ranked = rank_entities(rows, cfg["tie_break"])
            entities_out = []
            for row in ranked:
                key = (scope_id, row["entity_id"])
                tracker = trackers.setdefault(key, _fresh_tracker())
                bucket = row.get("bucket") or bucket_for(cfg, row["rank"], len(ranked))
                prior_bucket = tracker["last_bucket"]
                transition = None if prior_bucket is None else f"{prior_bucket}_TO_{bucket}"
                is_top = bucket == "TOP"
                tracker["top_streak"] = tracker["top_streak"] + 1 if is_top else 0
                tracker["non_top_streak"] = 0 if is_top else tracker["non_top_streak"] + 1
                today_recalc = [recalc_date] if recalc_date else []
                tracker["top_recalc"] = tracker["top_recalc"] + today_recalc if is_top else []
                tracker["non_top_recalc"] = [] if is_top else tracker["non_top_recalc"] + today_recalc
                confirmed_today = released_today = False
                if tracker["strong"]:
                    if bucket == "BOTTOM" or tracker["non_top_streak"] >= policy["confirmation"]["release_consecutive_non_top_observations"]:
                        tracker["strong"] = False
                        tracker["last_release_on"] = as_of
                        released_today = True
                elif tracker["top_streak"] >= policy["confirmation"]["enter_consecutive_top_observations"]:
                    tracker["strong"] = True
                    tracker["strong_since"] = as_of
                    tracker["strong_observations"] = 0
                    confirmed_today = True
                watch = {"lookback_observations": watch_cfg["lookback_observations"], "lookback_rank": None, "rank_improvement": None, "condition_met": None}
                if row["rank"] is not None:
                    tracker["ranks"].append(row["rank"])
                    lookback = watch_cfg["lookback_observations"]
                    if len(tracker["ranks"]) > lookback:
                        prior_rank = tracker["ranks"][-1 - lookback]
                        improvement = prior_rank - row["rank"]
                        watch.update({
                            "lookback_rank": prior_rank, "rank_improvement": improvement,
                            "condition_met": (not is_top) and improvement >= watch_cfg["minimum_rank_improvement"],
                        })
                if released_today:
                    state = "STRONG_RELEASED"
                elif confirmed_today:
                    state = "STRONG_CONFIRMED"
                elif tracker["strong"]:
                    state = "STRONG_HELD"
                elif watch["condition_met"]:
                    state = "EMERGING_WATCH"
                else:
                    state = "NEUTRAL"
                if state in STRONG_STATES:
                    tracker["strong_observations"] += 1
                    observations_in_state = tracker["strong_observations"]
                    state_since = tracker["strong_since"]
                elif state == tracker["state"]:
                    tracker["state_observations"] += 1
                    observations_in_state = tracker["state_observations"]
                    state_since = tracker["state_since"]
                else:
                    tracker["state_observations"] = 1
                    tracker["state_since"] = as_of
                    observations_in_state = 1
                    state_since = as_of
                tracker["state"] = state
                tracker["last_bucket"] = bucket
                # '재계산' dependency: recalculated observations the state rests on
                # (confirmation streak + strong episode; release adds the release streak).
                if released_today:
                    dependency = tracker["episode_recalc"] + tracker["non_top_recalc"]
                    tracker["episode_recalc"] = []
                elif confirmed_today:
                    tracker["episode_recalc"] = list(tracker["top_recalc"])
                    dependency = tracker["episode_recalc"]
                elif tracker["strong"]:
                    tracker["episode_recalc"] = tracker["episode_recalc"] + today_recalc
                    dependency = tracker["episode_recalc"]
                else:
                    dependency = today_recalc
                entity_out = {
                    "entity_id": row["entity_id"],
                    "source_identity": row["source_identity"],
                    "strength": row.get("strength"),
                    "rank": row["rank"],
                    "bucket": bucket,
                    "prior_bucket": prior_bucket,
                    "ledger_bucket_transition": transition,
                    "ledger_p2_state": None if transition is None else mapping[transition],
                    "state": state,
                    "state_since_date": state_since,
                    "observations_in_state": observations_in_state,
                    "calendar_days_in_state": (day - _date(state_since, "DATE")).days + 1,
                    "top_streak": tracker["top_streak"],
                    "non_top_streak": tracker["non_top_streak"],
                    "strong_confirmed_on": tracker["strong_since"] if tracker["strong"] or released_today else None,
                    "last_release_on": tracker["last_release_on"],
                    "strong_lapsed_by_gap": key in lapsed,
                    "emerging_watch": watch,
                    "lagging": None if lagging is None else lagging.get(row["entity_id"]),
                    "decision_eligible": state in STRONG_STATES,
                    "release_new_buy_stop": released_today,
                    "forced_exit": False,
                }
                if dependency:
                    entity_out["coverage_recalculation"] = _coverage_recalculation_mark(recalculation_refs, dependency)
                entities_out.append(entity_out)
            scopes_out.append({"scope_id": scope_id, "entities": entities_out})
        packet["chain"] = {
            "chain_start_date": chain_start, "observation_index": chain_index,
            "previous_observation_date": previous_observed, "reset": reset,
            "last_observed_as_of_date": as_of,
        }
        packet["scopes"] = scopes_out
        flat = [(scope["scope_id"], entity) for scope in scopes_out for entity in scope["entities"]]
        packet["summary"] = _summary(flat)
        packet["decision_view"] = _decision_view(flat)
        packet["entry_gate_view"] = _entry_gate_view(policy, market, flat)
        packet["input_chain_sha256"] = payload_sha256(chain_inputs)
        packet["payload_sha256"] = payload_sha256(packet)
        packets.append(packet)
        previous_observed = as_of
    return packets


def _coverage_recalculation_mark(refs: dict, dates: list) -> dict:
    dates = sorted(set(dates))
    sources = {(refs[d]["rule_id"], refs[d]["sha256"]) for d in dates}
    if len(sources) != 1:
        _fail("COVERAGE_RECALCULATION_RULE_SOURCE_AMBIGUOUS")
    rule_id, sha = sources.pop()
    return {
        "recalculated": True,
        "mark_ko": "재계산",
        "rule_id": rule_id,
        "ratification_record_sha256": sha,
        "depends_on_recalculated_observation_dates": dates,
    }


def _summary(flat: list) -> dict:
    lists = {state: [] for state in STATES}
    for scope_id, entity in flat:
        lists[entity["state"]].append({"scope_id": scope_id, "entity_id": entity["entity_id"]})
    return {
        "by_state": lists,
        "counts": {state: len(items) for state, items in lists.items()},
    }


def _decision_view(flat: list) -> dict:
    return {
        "t1_selection": [
            {"scope_id": s, "entity_id": e["entity_id"], "state": e["state"]}
            for s, e in flat if e["decision_eligible"]
        ],
        "release_new_buy_stop": [
            {"scope_id": s, "entity_id": e["entity_id"]} for s, e in flat if e["release_new_buy_stop"]
        ],
        "forced_exit": [],
        "emerging_watch_display_only": [
            {"scope_id": s, "entity_id": e["entity_id"]} for s, e in flat if e["state"] == "EMERGING_WATCH"
        ],
    }


def validate_packet(packet: dict) -> dict:
    if not isinstance(packet, dict) or packet.get("schema_version") != PACKET_SCHEMA_VERSION:
        _fail("PACKET_SCHEMA_INVALID")
    body = copy.deepcopy(packet)
    digest = body.pop("payload_sha256", None)
    if digest != payload_sha256(body):
        _fail("PACKET_SHA_MISMATCH")
    if packet.get("market") not in MARKETS:
        _fail("PACKET_MARKET_INVALID")
    for scope in packet.get("scopes") or []:
        for entity in scope["entities"]:
            if entity["state"] not in STATES:
                _fail("PACKET_STATE_INVALID")
            if entity["decision_eligible"] != (entity["state"] in STRONG_STATES) or entity["forced_exit"] is not False:
                _fail("PACKET_DECISION_FLAGS_INVALID")
    return packet


# ---------------------------------------------------------------------------
# Build, append-only write, portal projection
# ---------------------------------------------------------------------------

def evidence_path(root: Path, market: str, as_of: str) -> Path:
    return Path(root) / EVIDENCE_RELATIVE_ROOT / market / as_of / "packet.json"


def latest_path(root: Path, market: str) -> Path:
    return Path(root) / f"data/latest_rotation_confirmation_{market.lower()}.json"


def committed_as_of_dates(root: Path, market: str) -> set:
    base = Path(root) / EVIDENCE_RELATIVE_ROOT / market
    return {path.parent.name for path in base.glob("*/packet.json")} if base.is_dir() else set()


def select_append_observations(observations: list, committed_dates: set) -> tuple:
    """Committed packets are preferred: ``(kept, late_excluded_dates)``.

    Every replayed observation produces exactly one committed packet, so an
    observation dated on or before the newest committed packet without a
    packet of its own arrived late (older evidence committed afterwards).
    Replaying it would rewrite every later committed packet and fail the
    append-only check, so it is excluded from the replay and reported instead.
    With no committed packet (fresh root) nothing is excluded.
    """
    if not committed_dates:
        return list(observations), []
    newest = max(committed_dates)
    kept, late = [], []
    for observation in observations:
        as_of = observation["as_of_date"]
        if as_of <= newest and as_of not in committed_dates:
            late.append(as_of)
        else:
            kept.append(observation)
    return kept, late


def late_older_evidence_dates(market: str, root: Path = ROOT, policy: Optional[dict] = None) -> list:
    policy = load_policy(root) if policy is None else policy
    return select_append_observations(EXTRACTORS[market](policy, root), committed_as_of_dates(root, market))[1]


LATE_EVIDENCE_NOTICE_SCHEMA_VERSION = "rotation_confirmation_late_older_evidence/1"


def late_evidence_notice_path(root: Path, market: str) -> Path:
    return Path(root) / f"data/rotation_confirmation_late_older_evidence_{market.lower()}.json"


def late_evidence_notice(market: str, root: Path = ROOT, policy: Optional[dict] = None) -> dict:
    """Persistent notice of observations excluded because committed packets are preferred.

    No wall-clock field: the content changes only when the excluded set changes.
    """
    policy = load_policy(root) if policy is None else policy
    observations = EXTRACTORS[market](policy, root)
    late = set(select_append_observations(observations, committed_as_of_dates(root, market))[1])
    notice = {
        "schema_version": LATE_EVIDENCE_NOTICE_SCHEMA_VERSION,
        "market": market,
        "handling": "COMMITTED_PACKETS_PREFERRED_LATE_OLDER_EVIDENCE_NOT_REPLAYED",
        "late_older_evidence_not_replayed": [
            {"as_of_date": o["as_of_date"], "observation_status": o["status"], "sources": o["sources"]}
            for o in observations if o["as_of_date"] in late
        ],
    }
    notice["payload_sha256"] = payload_sha256(notice)
    return notice


def build_market(market: str, root: Path = ROOT, policy: Optional[dict] = None) -> list:
    if market not in MARKETS:
        _fail("MARKET_UNSUPPORTED", market)
    policy = load_policy(root) if policy is None else policy
    observations, _late = select_append_observations(EXTRACTORS[market](policy, root), committed_as_of_dates(root, market))
    return build_market_packets(policy, market, observations, load_state_mapping(root))


def write_market(market: str, packets: list, root: Path = ROOT) -> dict:
    root = Path(root)
    conflicts, new = [], []
    for packet in packets:
        path = evidence_path(root, market, packet["as_of_date"])
        data = render_json(packet)
        if path.exists():
            if path.read_bytes() != data:
                conflicts.append(_relative(path, root))
        else:
            new.append((path, data))
    if conflicts:
        _fail("APPEND_ONLY_EVIDENCE_CONFLICT", ",".join(conflicts))
    for path, data in new:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if packets:
        latest = latest_path(root, market)
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_bytes(render_json(packets[-1]))
    return {"market": market, "written": [_relative(p, root) for p, _ in new], "packet_count": len(packets)}


def verify_market(market: str, packets: list, root: Path = ROOT) -> list:
    root = Path(root)
    problems = []
    for packet in packets:
        path = evidence_path(root, market, packet["as_of_date"])
        if not path.exists():
            problems.append(f"MISSING:{_relative(path, root)}")
        elif path.read_bytes() != render_json(packet):
            problems.append(f"MISMATCH:{_relative(path, root)}")
    latest = latest_path(root, market)
    if packets and (not latest.exists() or latest.read_bytes() != render_json(packets[-1])):
        problems.append(f"LATEST_MISMATCH:{_relative(latest, root)}")
    return problems


def _projection_rows(packet: dict, states: tuple) -> list:
    rows = []
    for scope in packet["scopes"]:
        for entity in scope["entities"]:
            if entity["state"] in states:
                row = {
                    "scope_id": scope["scope_id"],
                    "entity_id": entity["entity_id"],
                    "label": entity["source_identity"],
                    "state": entity["state"],
                    "rank": entity["rank"],
                    "state_since_date": entity["state_since_date"],
                    "observations_in_state": entity["observations_in_state"],
                    "calendar_days_in_state": entity["calendar_days_in_state"],
                }
                if entity.get("coverage_recalculation"):
                    row["mark_ko"] = entity["coverage_recalculation"]["mark_ko"]
                rows.append(row)
    return sorted(rows, key=lambda r: (r["scope_id"], r["rank"] if r["rank"] is not None else 0, r["entity_id"]))


def build_portal_projection(root: Path = ROOT, policy: Optional[dict] = None) -> dict:
    root = Path(root)
    policy = load_policy(root) if policy is None else policy
    markets, sources = [], []
    for market in MARKETS:
        cfg = policy["markets"][market]
        path = latest_path(root, market)
        entry = {
            "market": market,
            "rule_id": cfg["rule_id"],
            "ratification_status": cfg["ratification_status"],
            "evidence_badge_ko": cfg["evidence_badge_ko"],
        }
        if not path.exists():
            entry.update({"status": "NOT_BUILT", "as_of_date": None, "strong": [], "released": [], "emerging_watch": [], "lagging_warnings": []})
            markets.append(entry)
            continue
        packet = validate_packet(_read_json(path))
        sources.append({"market": market, "path": _relative(path, root), "as_of_date": packet["as_of_date"], "payload_sha256": packet["payload_sha256"]})
        lagging = []
        for scope in packet["scopes"]:
            for entity in scope["entities"]:
                lag = entity.get("lagging") or {}
                if lag.get("lagging_warning") is True:
                    lagging.append({"scope_id": scope["scope_id"], "entity_id": entity["entity_id"], "label": entity["source_identity"]})
        entry.update({
            "status": packet["observation"]["status"],
            "unknown_reason": packet["observation"]["unknown_reason"],
            "as_of_date": packet["as_of_date"],
            "last_observed_as_of_date": packet["chain"]["last_observed_as_of_date"],
            "evidence_phase": packet["evidence_phase"],
            "maximum_observation_gap_days": packet["maximum_observation_gap_days"],
            "strong": _projection_rows(packet, STRONG_STATES),
            "released": _projection_rows(packet, ("STRONG_RELEASED",)),
            "emerging_watch": _projection_rows(packet, ("EMERGING_WATCH",)),
            "lagging_warnings": lagging,
            "counts": packet["summary"]["counts"],
            "pending_definitions": packet["pending_definitions"],
        })
        markets.append(entry)
    projection = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "chapter": "02",
        "section": "capital_rotation_confirmation",
        "policy": policy_identity(policy),
        "display_rules_ko": [
            "강세 확정·유지 섹터만 T1 선정과 T2 C5에 쓰입니다.",
            "떠오름 관찰은 표시만 하며 매수 근거가 아닙니다.",
            "강세 해제 시 신규 매수만 중단하고 보유분은 강제 청산하지 않습니다.",
            "시장 사이 자금 이동은 이 표시로 판정하지 않습니다.",
        ],
        "markets": markets,
        "source_packets": sources,
        "authority": copy.deepcopy(policy["authority"]),
    }
    projection["payload_sha256"] = payload_sha256(projection)
    return projection


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="replay committed evidence and write append-only packets")
    build.add_argument("--market", action="append", choices=MARKETS, required=True)
    build.add_argument("--write", action="store_true")
    build.add_argument("--no-portal", action="store_true",
                       help="do not rebuild the shared portal projection (the workflow runs `portal --write` once at the end)")
    build.add_argument("--root", type=Path, default=ROOT)
    verify = sub.add_parser("verify", help="rebuild and compare with committed packets")
    verify.add_argument("--market", action="append", choices=MARKETS)
    verify.add_argument("--root", type=Path, default=ROOT)
    portal_cmd = sub.add_parser("portal", help="rebuild the ch02 portal projection from the latest per-market packets")
    portal_cmd.add_argument("--write", action="store_true")
    portal_cmd.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        policy = load_policy(args.root)
    except RotationConfirmationError as exc:
        print(f"Rotation confirmation failed: {exc}", file=sys.stderr)
        return 2
    # Per-market isolation: one market's failure (e.g. an append-only conflict)
    # never blocks the other markets' packets; the run still exits non-zero.
    failed = []
    if args.command == "portal":
        try:
            data = render_json(build_portal_projection(args.root, policy))
        except RotationConfirmationError as exc:
            print(f"Rotation confirmation portal projection failed: {exc}", file=sys.stderr)
            return 2
        portal = Path(args.root) / PORTAL_RELATIVE_PATH
        if args.write:
            portal.parent.mkdir(parents=True, exist_ok=True)
            portal.write_bytes(data)
            return 0
        return 0 if portal.exists() and portal.read_bytes() == data else 1
    if args.command == "build":
        for market in args.market:
            try:
                notice = late_evidence_notice(market, args.root, policy)
                late = [item["as_of_date"] for item in notice["late_older_evidence_not_replayed"]]
                if args.write:
                    notice_path = late_evidence_notice_path(args.root, market)
                    notice_path.parent.mkdir(parents=True, exist_ok=True)
                    notice_path.write_bytes(render_json(notice))
                    if late:
                        print(f"::warning::late older {market} evidence not replayed: {','.join(late)}", file=sys.stderr)
                    if market == "CRYPTO":  # crypto-only coverage recalculation notice (drift is never raised)
                        recalc_notice = _coverage_recalc_module().notice_document(args.root)
                        recalc_path = Path(args.root) / CRYPTO_COVERAGE_RECALC_NOTICE_RELATIVE_PATH
                        recalc_path.parent.mkdir(parents=True, exist_ok=True)
                        recalc_path.write_bytes(render_json(recalc_notice))
                        if recalc_notice["notices"] or recalc_notice["point_classification_drift"]:
                            print("::warning::crypto coverage recalculation notice written", file=sys.stderr)
                packets = build_market(market, args.root, policy)
                latest = packets[-1] if packets else None
                if args.write:
                    report = write_market(market, packets, args.root)
                    print(json.dumps(report, ensure_ascii=False))
                print(json.dumps({
                    "market": market,
                    "as_of_date": None if latest is None else latest["as_of_date"],
                    "observation_status": None if latest is None else latest["observation"]["status"],
                    "counts": None if latest is None else latest["summary"]["counts"],
                    "late_older_evidence_not_replayed": late,
                }, ensure_ascii=False))
            except RotationConfirmationError as exc:
                failed.append(market)
                print(f"Rotation confirmation failed for {market}: {exc}", file=sys.stderr)
        if args.write and not args.no_portal:
            try:
                portal = Path(args.root) / PORTAL_RELATIVE_PATH
                portal.parent.mkdir(parents=True, exist_ok=True)
                portal.write_bytes(render_json(build_portal_projection(args.root, policy)))
            except RotationConfirmationError as exc:
                print(f"Rotation confirmation portal projection failed: {exc}", file=sys.stderr)
                return 2
        return 3 if failed else 0
    problems = []
    for market in args.market or MARKETS:
        try:
            problems += verify_market(market, build_market(market, args.root, policy), args.root)
        except RotationConfirmationError as exc:
            failed.append(market)
            print(f"Rotation confirmation verify failed for {market}: {exc}", file=sys.stderr)
    if not args.market:  # the shared portal projection is checked on a full verify only
        try:
            portal = Path(args.root) / PORTAL_RELATIVE_PATH
            if not portal.exists() or portal.read_bytes() != render_json(build_portal_projection(args.root, policy)):
                problems.append(f"PORTAL_PROJECTION_MISMATCH:{PORTAL_RELATIVE_PATH}")
        except RotationConfirmationError as exc:
            print(f"Rotation confirmation portal projection failed: {exc}", file=sys.stderr)
            return 2
    for problem in problems:
        print(problem)
    if failed:
        return 3
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(run())
