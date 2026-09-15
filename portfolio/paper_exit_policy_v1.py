#!/usr/bin/env python3
"""PAPER exit policy v1: persistent release-sell intent, observation-gap rule, crypto 21-day stop.

Ratified inputs (byte copies under ``evidence/authority/``, sha bound in
``config/paper_exit_policy_v1.json`` and pinned below):

* ``USER_RATIFICATION_PAPER_EXIT_PROVISIONAL_V1_20260915`` (sha 47276abe...)
  -- RULE.EXIT.RELEASE_FULL_SELL.V1 (on confirmed strength release, sell the
  held position in full at the first allowed execution time, all three
  markets; replaces the rotation record's "held positions follow existing
  stop/TP rules") and RULE.EXIT.CRYPTO_TIME_STOP_21D.V1 (crypto: sell in full
  21 days after the first buy).
* ``USER_RATIFICATION_ROTATION_INTERPRETATION_OBSERVATION_GAP_20260915``
  (sha ed2ca92d...) -- a strong state lapsing because data exceeded the
  maximum observation gap is not a release: hold, stop new buys, show
  '판정 공백'; the first judgment after data returns decides (outside the top
  bucket -> release sell, inside -> strength continues); time stops still apply.
* ``USER_RATIFICATION_PAPER_EXECUTION_CONTRACT_D1_D3_D5_D11_20260915``
  (sha 10de02bf...) -- D1 time contract: observation / availability /
  decision / order / fill times are separate; fill windows KR 09:15-15:20 KST,
  US 09:45-15:50 ET, crypto 07:00Z decision cycle; no NXT / KRX after-market /
  US extended-hours fills.
* ``USER_RATIFICATION_PAPER_DATA_FAILURE_RISK_REDUCTION_PRIORITY_C_20260915``
  (sha 3d07cbf1...) -- release and time exits are ordinary exits: they hold
  while the price is STALE; no fill from an unverified price.

Why a separate layer: ``STRONG_RELEASED`` exists on the release observation
only, so reading the latest rotation packet alone loses the signal the next
day or after a restart. This layer (1) re-derives release facts from the
whole visible packet history after the position's entry anchor and (2) turns
the first exit fact into a persistent ``paper_exit_intent/1`` record kept by
``ExitIntentStore`` until the position is fully sold. The rotation policy file
(``config/rotation_confirmation_policy_v1.json``) is not modified; its old
``held_position_action`` is shown as superseded through ``rule_refs``.

Pure and offline: no network, no secret, no order, no exchange call. Rotation
packets are accepted only when their ``available_at`` is not after the
decision time (no lookahead).
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
from typing import Optional
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG_RELATIVE_PATH = "config/paper_exit_policy_v1.json"
CONFIG_SCHEMA_VERSION = "paper_exit_policy/1"
INTENT_SCHEMA_VERSION = "paper_exit_intent/1"
INTENT_EVENT_SCHEMA_VERSION = "paper_exit_intent_event/1"
EVALUATION_SCHEMA_VERSION = "paper_exit_evaluation/1"
MARKETS = ("CRYPTO", "KR", "US")
SUPERSESSION_RELATION = "SUPERSEDED_BY"

PINNED_RECORD_SHA256 = {
    "exit_provisional_v1": "47276abe432102c33b208a5c3a5d10b30c3c30c79fcb809bb1283c97efb95619",
    "rotation_observation_gap": "ed2ca92d9b9cfe6b2e912c686f874c62f664fe25b0b20814264a853366c2487a",
    "execution_contract_d1_d11": "10de02bf98fd4e5776ed77c09daad36de914e03942675cbe960c121e5dbd668c",
    "data_failure_priority_c": "3d07cbf1fbba35caaed032b7d3d52cec78e804ad6b415ed7e240191b4f45d1f6",
    "build_plan_p1_p6": "2a94be2b593ed49a61e38cecfc2c992802ffa8102b292bf40bd964e7391d5fdd",
    "rotation_max_observation_gap": "d65f58c60eb7b78f5e8fa2e054497e17903cf290a517b5b9a246e0419b199903",
}
PINNED_ROTATION_POLICY_SHA256 = "0bf2af4b63171c2bc7dda2cbcfde0b7023bba4a63d4ef10d4ed5361a223885d8"

REASON_RELEASE = "RELEASE_CONFIRMED"
REASON_RELEASE_AFTER_GAP = "RELEASE_FIRST_JUDGMENT_AFTER_OBSERVATION_GAP_OUTSIDE_TOP"
REASON_RELEASE_AFTER_CONTINUATION = "RELEASE_CONFIRMED_AFTER_GAP_CONTINUATION"
REASON_TIME_STOP = "CRYPTO_TIME_STOP_21D"
RELEASE_REASONS = (REASON_RELEASE, REASON_RELEASE_AFTER_GAP, REASON_RELEASE_AFTER_CONTINUATION)

JUDGMENT_DISPLAY_KO = {
    "STRONG": "강세 유지",
    "RELEASED": "강세 해제 확인",
    "OBSERVATION_GAP": "판정 공백",
    "STRENGTH_CONTINUED_AFTER_GAP": "공백 뒤 강세 유지",
    "OBSERVATION_UNKNOWN_WITHIN_MAX_GAP": "판정 확인 불가(허용 공백 안)",
    "ENTITY_NOT_OBSERVED": "판정 확인 불가(종목 묶음 미관측)",
    "ENTRY_ANCHOR_UNAVAILABLE": "판정 확인 불가(진입 판정 없음)",
    "ENTRY_ANCHOR_NOT_STRONG": "판정 확인 불가(진입 판정이 강세 아님)",
}

UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,160}$")


class PaperExitPolicyError(ValueError):
    """Fail-closed exit-policy violation."""


def _fail(code: str, detail: str = "") -> None:
    raise PaperExitPolicyError(f"{code}:{detail}" if detail else code)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RC = _load_module("atlas_rotation_confirmation_for_paper_exit_policy", ROOT / "rotation" / "rotation_confirmation.py")
RR = _load_module("atlas_governance_rule_refs_for_paper_exit_policy", ROOT / "governance" / "rule_refs.py")
REG = RR.REGISTRY
ROLES = RR.ROLES


# ---------------------------------------------------------------------------
# Canonical helpers
# ---------------------------------------------------------------------------

def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def render_json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def with_payload_sha(value: dict) -> dict:
    body = copy.deepcopy(value)
    body.pop("payload_sha256", None)
    body["payload_sha256"] = payload_sha256(body)
    return body


def verify_payload_sha(value: dict, code: str) -> dict:
    if not isinstance(value, dict):
        _fail(code)
    body = copy.deepcopy(value)
    digest = body.pop("payload_sha256", None)
    if digest != payload_sha256(body):
        _fail(code)
    return value


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("JSON_READ_FAILED", f"{path}:{exc}")


def parse_utc(value, code: str = "UTC_TIMESTAMP_INVALID") -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        _fail(code, repr(value))
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except ValueError:
        _fail(code, value)


def stamp(value: dt.datetime) -> str:
    value = value.astimezone(dt.timezone.utc)
    if value.microsecond:
        return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(value, code: str = "DATE_INVALID") -> dt.date:
    try:
        parsed = dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        _fail(code, repr(value))
    if parsed.isoformat() != value:
        _fail(code, repr(value))
    return parsed


def parse_decimal(value, code: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if not isinstance(value, str):
        _fail(code, repr(value))
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        _fail(code, value)
    if not parsed.is_finite() or (positive and parsed <= 0) or (non_negative and parsed < 0):
        _fail(code, value)
    return parsed


def decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        _fail(code, repr(value))
    return value


# ---------------------------------------------------------------------------
# Policy (bound to ratification record shas)
# ---------------------------------------------------------------------------

def _record(root: Path, config: dict, key: str) -> dict:
    entry = (config.get("records") or {}).get(key) or {}
    if entry.get("sha256") != PINNED_RECORD_SHA256[key]:
        _fail("RECORD_SHA_NOT_PINNED", key)
    path = Path(root) / str(entry.get("repo_path", ""))
    if not path.is_file():
        _fail("RATIFICATION_RECORD_MISSING", str(entry.get("repo_path")))
    if file_sha256(path) != entry["sha256"]:
        _fail("RATIFICATION_RECORD_SHA_MISMATCH", key)
    record = _read_json(path)
    if record.get("id") != entry.get("record_id"):
        _fail("RATIFICATION_RECORD_ID_MISMATCH", key)
    return record


def load_policy(root: Path = ROOT, path: Optional[Path] = None) -> dict:
    """Validated policy bundle ``{"config", "rotation_policy"}``.

    Every numeric or categorical value used by this layer is cross-checked
    against the ratification record text it comes from; a drift fails closed.
    """
    root = Path(root)
    config = _read_json(root / CONFIG_RELATIVE_PATH if path is None else Path(path))
    if not isinstance(config, dict) or config.get("schema_version") != CONFIG_SCHEMA_VERSION:
        _fail("EXIT_POLICY_SCHEMA_INVALID")
    rules = config.get("rules") or {}

    exit_record = _record(root, config, "exit_provisional_v1")
    decision = exit_record.get("decision") or {}
    release = rules["release_full_sell"]
    release_decision = decision.get(release["rule_id"]) or {}
    if (
        release["rule_id"] != "RULE.EXIT.RELEASE_FULL_SELL.V1"
        or release_decision.get("status") != "PROVISIONAL" or release["status"] != "PROVISIONAL"
        or sorted(release_decision.get("markets") or []) != sorted(MARKETS)
        or sorted(release["markets"]) != sorted(MARKETS)
        or "first allowed execution time" not in str(release_decision.get("text"))
        or "in full" not in str(release_decision.get("text"))
    ):
        _fail("RELEASE_FULL_SELL_RECORD_MISMATCH")
    amended = [a for a in exit_record.get("amends") or [] if a.get("decision_key") == "RELEASE_HANDLING"]
    time_stop = rules["crypto_time_stop"]
    time_decision = decision.get(time_stop["rule_id"]) or {}
    if (
        time_stop["rule_id"] != "RULE.EXIT.CRYPTO_TIME_STOP_21D.V1"
        or time_decision.get("status") != "PROVISIONAL" or time_stop["status"] != "PROVISIONAL"
        or time_decision.get("markets") != ["CRYPTO"] or time_stop["markets"] != ["CRYPTO"]
        or time_stop["days"] != 21 or f"{time_stop['days']} days after the first buy" not in str(time_decision.get("text"))
        or time_stop["day_length_hours"] != 24
    ):
        _fail("CRYPTO_TIME_STOP_RECORD_MISMATCH")
    shadow = rules["shadow_controls"]
    shadow_decision = decision.get(shadow["rule_id"]) or {}
    shadow_text = str(shadow_decision.get("text"))
    controls = shadow["controls"]
    if (
        shadow["rule_id"] != "RULE.EXIT.SHADOW_CONTROLS.V1"
        or shadow_decision.get("status") != "RATIFIED" or shadow["record_only"] is not True
        or "1-B (release+lagging)" not in shadow_text
        or f"{controls['TS14']['days']}-day time stop" not in shadow_text
        or f"partial TP {controls['PTP1']['r_multiple']}R" not in shadow_text
        or f"{controls['DS5']['atr_multiple']}xATR disaster stop" not in shadow_text
        or "no disaster stop and no partial take-profit in defaults" not in shadow_text
        or set(controls) != {"1-B", "TS14", "PTP1", "DS5"}
        # exit study v2 pre-registration section 5 units (not in the record text itself)
        or (shadow["crypto_units"]["atr_period"], shadow["crypto_units"]["r_ref_atr_multiple"]) != (14, "3")
        or shadow["crypto_units"]["level_anchor"] != "FIRST_FILL_PRICE" or shadow["crypto_units"]["top_ups_change_levels"] is not False
        or controls["PTP1"]["quantity_fraction"] != "0.5" or controls["TS14"]["day_length_hours"] != 24
        or rules["monitored_stop_fill_model"]["monitoring_gap_interval_multiple"] != "2"
    ):
        _fail("SHADOW_CONTROLS_RECORD_MISMATCH")
    study = config["source_documents"]["exit_study_v2"]
    if (exit_record.get("source_documents") or {}).get(study["document"]) != study["sha256"]:
        _fail("EXIT_STUDY_V2_SHA_MISMATCH")
    canon = config["source_documents"]["execution_contract_canon"]
    if (exit_record.get("source_documents") or {}).get(canon["document"]) != canon["sha256"]:
        _fail("EXECUTION_CANON_SHA_MISMATCH")

    gap_record = _record(root, config, "rotation_observation_gap")
    gap = rules["observation_gap"]
    gap_decision = gap_record.get("decision") or {}
    interpreted = {item.get("sha256") for item in gap_record.get("interprets") or []}
    if (
        gap_decision.get("rule_id") != gap["rule_id"]
        or PINNED_RECORD_SHA256["exit_provisional_v1"] not in interpreted
        or "not a release" not in str(gap_decision.get("gap_lapse"))
        or "'판정 공백'" not in str(gap_decision.get("gap_lapse"))
        or gap["gap_lapse_display_ko"] != "판정 공백"
        or "outside the top bucket treat it as a release" not in str(gap_decision.get("gap_lapse"))
        or "time stops and other exit conditions continue to apply" not in str(gap_decision.get("other_exits_during_gap"))
        or gap["time_stops_apply_during_gap"] is not True
    ):
        _fail("OBSERVATION_GAP_RECORD_MISMATCH")

    d1_record = _record(root, config, "execution_contract_d1_d11")
    time_contract = rules["time_contract"]
    windows = time_contract["fill_windows"]
    new_numbers = d1_record.get("new_numbers") or []
    if (
        (d1_record.get("rule_ids") or {}).get(time_contract["rule_id"]) != "D1"
        or (d1_record.get("rule_ids") or {}).get(rules["monitored_stop_fill_model"]["rule_id"]) != "D9"
        or f"KR window {windows['KR']['start']}-{windows['KR']['end']} KST" not in new_numbers
        or f"US window {windows['US']['start']}-{windows['US']['end']} ET" not in new_numbers
        or windows["KR"]["timezone"] != "Asia/Seoul" or windows["US"]["timezone"] != "America/New_York"
        or windows["CRYPTO"]["decision_cycle_anchor"] != "07:00"
        or windows["KR"]["end_exclusive"] is not True or windows["US"]["end_exclusive"] is not True
        or (windows.get("window_basis") or {}).get("end_exclusive") != "CIO_INTERPRETATION_NOT_USER_TEXT"
        or "07:00Z" not in str(d1_record.get("user_sentence_verbatim"))
        or (d1_record.get("source_document") or {}).get("sha256") != canon["sha256"]
    ):
        _fail("TIME_CONTRACT_RECORD_MISMATCH")

    failure_record = _record(root, config, "data_failure_priority_c")
    failure_decision = failure_record.get("decision") or {}
    if (
        failure_decision.get("rule_id") != rules["data_failure_priority"]["rule_id"]
        or "strength release, time exit" not in str(failure_decision.get("ordinary_exits"))
        or "hold while STALE" not in str(failure_decision.get("ordinary_exits"))
    ):
        _fail("DATA_FAILURE_RECORD_MISMATCH")

    rotation_policy = RC.load_policy(root)
    identity = RC.policy_identity(rotation_policy)
    if identity["policy_sha256"] != config["rotation_policy"]["policy_sha256"] or identity["policy_sha256"] != PINNED_ROTATION_POLICY_SHA256:
        _fail("ROTATION_POLICY_V1_CHANGED")
    handling = rotation_policy["release_handling"]
    if (
        not amended or amended[0].get("sha256") != rotation_policy["ratification_record"]["sha256"]
        or handling["rule_id"] != release["supersedes"]["rule_id"]
        or handling["held_position_action"] != release["supersedes"]["superseded_value"]
        or handling["new_buy_stop"] is not True
    ):
        _fail("SUPERSEDED_RELEASE_HANDLING_MISMATCH")
    gap_days = gap["maximum_observation_gap_days"]
    committed_gaps = {m: rotation_policy["markets"][m]["maximum_observation_gap_days"] for m in MARKETS}
    max_gap_record = _record(root, config, "rotation_max_observation_gap")
    max_gap_rules = [r for r in max_gap_record.get("rules") or [] if r.get("rule_id") == gap_days.get("rule_id")]
    if (
        gap_days["kind"] != "USER_RATIFIED" or gap_days["record"] != "rotation_max_observation_gap"
        or gap_days["rule_id"] != "RULE.ROTATION.MAX_OBSERVATION_GAP.V1" or gap_days["unit"] != "CALENDAR_DAYS"
        or max_gap_record.get("status") != "RATIFIED" or len(max_gap_rules) != 1
        or not gap_days["read_from"].startswith(RC.POLICY_RELATIVE_PATH + "#")
        or gap_days["values"] != committed_gaps
        or f"crypto {committed_gaps['CRYPTO']}, US {committed_gaps['US']}, KR {committed_gaps['KR']} calendar days" not in max_gap_rules[0]["decision"]
        or "코인 2일, 미국 4일, 한국 7일(달력일)" not in str(max_gap_record.get("user_sentence_verbatim"))
        or gap["chain_break_handling"] != "ROTATION_CHAIN_RESET_BY_GAP_IS_GAP_STATE_NOT_RELEASE_RESOLVED_ON_FIRST_POST_GAP_JUDGMENT"
    ):
        _fail("OBSERVATION_GAP_LENGTH_SOURCE_MISMATCH")
    units = shadow["kr_us_units"]
    plan_record = _record(root, config, "build_plan_p1_p6")
    unit_rules = [r for r in plan_record.get("rules") or [] if r.get("rule_id") == units.get("rule_id")]
    unit_text = unit_rules[0]["decision"] if len(unit_rules) == 1 else ""
    if (
        units["status"] != "DEFINED" or units["record"] != "build_plan_p1_p6" or units["record_only"] is not True
        or units["rule_id"] != "RULE.EXIT.SHADOW_CONTROLS_KR_US_UNITS.V1" or plan_record.get("status") != "RATIFIED"
        or "1-B replaced by release-only" not in unit_text
        or f"time stop {units['TS14']['trading_days']} trading days" not in unit_text
        or f"partial take-profit {units['PTP1']['r_multiple']}R = {units['PTP1']['r_ref_atr_multiple']}x daily ATR{units['atr_period']}" not in unit_text
        or f"disaster stop = {units['DS5']['atr_multiple']}x daily ATR{units['atr_period']}" not in unit_text
        or "record-only" not in unit_text or "1-B reverts to its original definition" not in unit_text
        or units["1-B"]["component"] != "RELEASE_ONLY_EQUALS_DEFAULT_RELEASE_SELL"
        or (units["TS14"]["trading_days"], units["DS5"]["atr_multiple"], units["PTP1"]["r_multiple"], units["PTP1"]["r_ref_atr_multiple"])
        != (controls["TS14"]["days"], controls["DS5"]["atr_multiple"], controls["PTP1"]["r_multiple"], shadow["crypto_units"]["r_ref_atr_multiple"])
    ):
        _fail("SHADOW_CONTROLS_KR_US_UNITS_RECORD_MISMATCH")
    lag = rotation_policy["markets"]["CRYPTO"]["lagging_warning"]
    if (lag["ratio_sma_days"], lag["momentum_lookback_days"], lag["excluded_entities"]) != (30, 7, ["BTC"]):
        _fail("ROTATION_LAGGING_DEFINITION_MISMATCH")
    if any(value is not False for value in config["authority"].values()):
        _fail("EXIT_POLICY_AUTHORITY_MUST_BE_FALSE")
    try:
        registry = RR.RegistryContext.load(root / REG.REGISTRY_RELATIVE_PATH, root=root)
    except (REG.RuleRegistryError, RR.RuleLineageError) as exc:
        _fail("RULE_REGISTRY_INVALID", str(exc))
    check_registry_bindings(config, registry)
    return {"config": config, "rotation_policy": rotation_policy, "registry": registry}


def check_registry_bindings(config: dict, registry) -> None:
    """Every rule this layer cites must be a decided registry row whose primary
    record sha is the record this config binds; the release supersession must
    match the registry's partial supersession of RULE.ROTATION.RELEASE_HANDLING.V1."""
    for key, rule in config["rules"].items():
        row = registry.rules.get(rule["rule_id"])
        if row is None or not REG.is_decided(row):
            _fail("RULE_NOT_DECIDED_IN_REGISTRY", rule["rule_id"])
        if REG.primary_record_sha256(row) != config["records"][rule["record"]]["sha256"]:
            _fail("REGISTRY_RECORD_SHA_MISMATCH", rule["rule_id"])
    release = config["rules"]["release_full_sell"]
    target = registry.rules.get(release["supersedes"]["rule_id"])
    parts = [] if target is None else (target.get("superseded_parts") or [])
    matches = [
        part for part in parts
        if part["key_parameter"] == release["supersedes"]["registry_key_parameter"]
        and part["superseded_by"]["rule_id"] == release["rule_id"]
        and part["superseded_by"]["sha256"] == config["records"][release["record"]]["sha256"]
    ]
    if len(matches) != 1:
        _fail("REGISTRY_SUPERSESSION_MISMATCH", release["supersedes"]["rule_id"])


def rule_ref(policy: dict, rule_key: str, role: str) -> dict:
    """Registry-exact ``rule_refs`` entry via governance/rule_refs.make_rule_ref.

    ``registry_sha256`` is the sha of the committed config/rule_registry_v1.json
    bytes; version and source record sha come from the registry row.
    """
    if role not in ROLES:
        _fail("RULE_REF_ROLE_INVALID", role)
    try:
        return RR.make_rule_ref(policy["registry"], policy["config"]["rules"][rule_key]["rule_id"], role)
    except RR.RuleLineageError as exc:
        _fail("RULE_REF_INVALID", str(exc))


def _sorted_refs(refs: list, policy: Optional[dict] = None) -> list:
    unique = {(r["rule_id"], r["role"]): r for r in refs}
    ordered = [unique[key] for key in sorted(unique)]
    if policy is not None:
        try:
            return RR.validate_rule_refs(ordered, policy["registry"])
        except RR.RuleLineageError as exc:
            _fail("RULE_REFS_NOT_REGISTRY_EXACT", str(exc))
    return ordered


def supersession_block(policy: dict) -> dict:
    """Registry partial supersession (``superseded_parts``) as a display relation.

    ``SUPERSEDED_BY`` is not a ``rule_refs`` role in governance/rule_refs.py, so
    the relation is carried here, taken from the registry row; if the library
    later adds the role additively, ``overlay_held_position_action`` also emits it
    as a rule_ref.
    """
    release = policy["config"]["rules"]["release_full_sell"]
    target = policy["registry"].rules[release["supersedes"]["rule_id"]]
    part = next(p for p in target["superseded_parts"]
                if p["key_parameter"] == release["supersedes"]["registry_key_parameter"])
    return {
        "relation": SUPERSESSION_RELATION,
        "rule_id": target["rule_id"],
        "rule_version": target["version"],
        "key_parameter": part["key_parameter"],
        "superseded_by": copy.deepcopy(part["superseded_by"]),
        "registry_path": policy["registry"].relative_path,
        "registry_sha256": policy["registry"].sha256,
    }


def authority(policy: dict) -> dict:
    return copy.deepcopy(policy["config"]["authority"])


# ---------------------------------------------------------------------------
# D1 time contract: first allowed fill time
# ---------------------------------------------------------------------------

def validate_session_calendar(calendar: Optional[dict], market: str) -> dict:
    """``{"market", "source", "sessions": {date: "OPEN" | "CLOSED" | other}}``.

    The calendar is caller-supplied from the ratified official calendar
    sources; weekday inference is never used. Any status other than OPEN or
    CLOSED (early close, delayed open) has no ratified window and is UNKNOWN.
    """
    if not isinstance(calendar, dict) or calendar.get("market") != market:
        _fail("SESSION_CALENDAR_MARKET_MISMATCH", market)
    sessions = calendar.get("sessions")
    if not isinstance(sessions, dict) or not sessions:
        _fail("SESSION_CALENDAR_EMPTY", market)
    for day, status in sessions.items():
        parse_date(day, "SESSION_CALENDAR_DATE_INVALID")
        if not isinstance(status, str):
            _fail("SESSION_CALENDAR_STATUS_INVALID", day)
    return calendar


def _window_bounds(policy: dict, market: str, day: dt.date) -> tuple:
    window = policy["config"]["rules"]["time_contract"]["fill_windows"][market]
    zone = ZoneInfo(window["timezone"])
    start_h, start_m = (int(part) for part in window["start"].split(":"))
    end_h, end_m = (int(part) for part in window["end"].split(":"))
    start = dt.datetime(day.year, day.month, day.day, start_h, start_m, tzinfo=zone).astimezone(dt.timezone.utc)
    end = dt.datetime(day.year, day.month, day.day, end_h, end_m, tzinfo=zone).astimezone(dt.timezone.utc)
    return start, end


def crypto_decision_cycle(at: dt.datetime) -> dict:
    anchor = at.replace(hour=7, minute=0, second=0, microsecond=0)
    if at < anchor:
        anchor -= dt.timedelta(days=1)
    return {"start": stamp(anchor), "end": stamp(anchor + dt.timedelta(days=1))}


def first_allowed_fill_window(policy: dict, market: str, order_time: str, calendar: Optional[dict] = None) -> dict:
    """Earliest bound for a fill observation strictly after ``order_time`` (canon 1-2).

    KR / US: inside [start, end) of an OPEN regular session in the market's
    time zone; extended-hours / NXT / after-market are never inside. CRYPTO:
    24h, so the bound is the order time itself (the fill still needs a FRESH
    observation strictly after it).
    """
    if market not in MARKETS:
        _fail("MARKET_INVALID", str(market))
    after = parse_utc(order_time, "ORDER_TIME_INVALID")
    base = {"market": market, "order_time": stamp(after), "rule_ref": rule_ref(policy, "time_contract", "APPLIED")}
    if market == "CRYPTO":
        return base | {"status": "KNOWN", "not_before": stamp(after), "window": "24H",
                       "session_date": None, "decision_cycle": crypto_decision_cycle(after), "reason": None}
    if calendar is None:
        return base | {"status": "UNKNOWN", "not_before": None, "window": None, "session_date": None,
                       "decision_cycle": None, "reason": "SESSION_CALENDAR_NOT_SUPPLIED"}
    calendar = validate_session_calendar(calendar, market)
    window = policy["config"]["rules"]["time_contract"]["fill_windows"][market]
    zone = ZoneInfo(window["timezone"])
    sessions = calendar["sessions"]
    day = after.astimezone(zone).date()
    last = max(parse_date(d) for d in sessions)
    while day <= last:
        status = sessions.get(day.isoformat())
        if status is None:
            return base | {"status": "UNKNOWN", "not_before": None, "window": None, "session_date": day.isoformat(),
                           "decision_cycle": None, "reason": "SESSION_CALENDAR_DATE_UNKNOWN"}
        if status == "OPEN":
            start, end = _window_bounds(policy, market, day)
            if after < end:
                return base | {"status": "KNOWN", "not_before": stamp(max(start, after)),
                               "window": {"start": stamp(start), "end_exclusive": stamp(end),
                                          "local_start": window["start"], "local_end": window["end"],
                                          "timezone": window["timezone"],
                                          "end_exclusive_basis": policy["config"]["rules"]["time_contract"]["fill_windows"]["window_basis"]["end_exclusive"]},
                               "session_date": day.isoformat(), "decision_cycle": None, "reason": None}
        elif status != "CLOSED":
            return base | {"status": "UNKNOWN", "not_before": None, "window": None, "session_date": day.isoformat(),
                           "decision_cycle": None, "reason": f"NON_REGULAR_SESSION_WINDOW_NOT_RATIFIED:{status}"}
        day += dt.timedelta(days=1)
    return base | {"status": "UNKNOWN", "not_before": None, "window": None, "session_date": None,
                   "decision_cycle": None, "reason": "SESSION_CALENDAR_EXHAUSTED"}


def is_allowed_fill_time(policy: dict, market: str, at: str, calendar: Optional[dict] = None) -> tuple:
    """``(allowed: bool, reason: str | None)`` for one fill observation time."""
    moment = parse_utc(at, "FILL_TIME_INVALID")
    if market == "CRYPTO":
        return True, None
    calendar = validate_session_calendar(calendar, market)
    zone = ZoneInfo(policy["config"]["rules"]["time_contract"]["fill_windows"][market]["timezone"])
    day = moment.astimezone(zone).date()
    status = calendar["sessions"].get(day.isoformat())
    if status != "OPEN":
        return False, f"SESSION_NOT_OPEN:{status}"
    start, end = _window_bounds(policy, market, day)
    if not start <= moment < end:
        return False, "OUTSIDE_REGULAR_FILL_WINDOW"
    return True, None


# ---------------------------------------------------------------------------
# Position and rotation packet inputs
# ---------------------------------------------------------------------------

def validate_position(position: dict) -> dict:
    """Open PAPER position episode (D8: one episode, clock from the first fill)."""
    if not isinstance(position, dict):
        _fail("POSITION_INVALID")
    required = {"market", "symbol", "position_episode_id", "rotation_scope_id", "rotation_entity_id",
                "entry_rotation_as_of_date", "first_fill_at", "quantity"}
    missing = sorted(required - set(position))
    if missing:
        _fail("POSITION_FIELDS_MISSING", ",".join(missing))
    if position["market"] not in MARKETS:
        _fail("POSITION_MARKET_INVALID", str(position["market"]))
    for key in ("symbol", "position_episode_id", "rotation_scope_id", "rotation_entity_id"):
        _token(position[key], f"POSITION_{key.upper()}_INVALID")
    parse_date(position["entry_rotation_as_of_date"], "POSITION_ENTRY_ROTATION_AS_OF_INVALID")
    first_fill = parse_utc(position["first_fill_at"], "POSITION_FIRST_FILL_AT_INVALID")
    parse_decimal(position["quantity"], "POSITION_QUANTITY_INVALID", positive=True)
    for lot in position.get("lots") or []:
        if parse_utc(lot.get("t_fill"), "POSITION_LOT_FILL_TIME_INVALID") < first_fill:
            _fail("POSITION_LOT_BEFORE_FIRST_FILL")
    return position


def visible_rotation_packets(policy: dict, market: str, packets: list, t_dec: dt.datetime,
                             evaluation_date: dt.date) -> tuple:
    """``(visible, excluded_not_yet_available)``; each item ``{"packet", "available_at"}``.

    A packet whose ``available_at`` is after the decision time is not visible
    (lookahead). A packet dated after the evaluation date fails closed.
    """
    identity = RC.policy_identity(policy["rotation_policy"])
    visible, excluded, seen = [], [], set()
    for item in packets or []:
        packet = RC.validate_packet(item.get("packet") if isinstance(item, dict) else None)
        if packet["market"] != market:
            _fail("ROTATION_PACKET_MARKET_MISMATCH", packet["market"])
        if packet["policy"] != identity:
            _fail("ROTATION_PACKET_POLICY_MISMATCH", packet["as_of_date"])
        available = parse_utc(item.get("available_at"), "ROTATION_PACKET_AVAILABLE_AT_INVALID")
        as_of = parse_date(packet["as_of_date"], "ROTATION_PACKET_AS_OF_INVALID")
        if as_of > evaluation_date:
            _fail("ROTATION_PACKET_AFTER_EVALUATION_DATE", packet["as_of_date"])
        if packet["as_of_date"] in seen:
            _fail("ROTATION_PACKET_DATE_DUPLICATE", packet["as_of_date"])
        seen.add(packet["as_of_date"])
        if available > t_dec:
            excluded.append(packet["as_of_date"])
            continue
        visible.append({"packet": packet, "available_at": stamp(available)})
    visible.sort(key=lambda entry: entry["packet"]["as_of_date"])
    return visible, sorted(excluded)


def _entity(packet: dict, scope_id: str, entity_id: str) -> Optional[dict]:
    matches = [
        entity for scope in packet["scopes"] if scope["scope_id"] == scope_id
        for entity in scope["entities"] if entity["entity_id"] == entity_id
    ]
    if len(matches) > 1:
        _fail("ROTATION_ENTITY_AMBIGUOUS", entity_id)
    return matches[0] if matches else None


def _trigger(reason: str, entry: dict, entity: dict, gap_reset_packet_without_entity: bool = False) -> dict:
    packet = entry["packet"]
    return {
        "reason_code": reason,
        "fact": "ROTATION_PACKET",
        "packet_as_of_date": packet["as_of_date"],
        "packet_payload_sha256": packet["payload_sha256"],
        "packet_available_at": entry["available_at"],
        "entity_state": entity["state"],
        "entity_bucket": entity["bucket"],
        "chain_reset": packet["chain"]["reset"],
        "strong_lapsed_by_gap": entity["strong_lapsed_by_gap"],
        "gap_reset_packet_without_entity": gap_reset_packet_without_entity,
    }


def decision_local_date(policy: dict, market: str, t_dec: str) -> str:
    """Market-local calendar date of the decision time (KR Asia/Seoul, US America/New_York, CRYPTO UTC)."""
    zone = ZoneInfo(policy["config"]["rules"]["time_contract"]["fill_windows"][market]["timezone"])
    return parse_utc(t_dec, "DECISION_TIME_INVALID").astimezone(zone).date().isoformat()


def bound_evaluation_date(policy: dict, market: str, t_dec: str, evaluation_date: Optional[str]) -> str:
    """The evaluation date is derived from ``t_dec``; a supplied one must match it."""
    derived = decision_local_date(policy, market, t_dec)
    if evaluation_date is not None:
        parse_date(evaluation_date, "EVALUATION_DATE_INVALID")
        if evaluation_date != derived:
            _fail("EVALUATION_DATE_NOT_DECISION_LOCAL_DATE", f"{evaluation_date}!={derived}")
    return derived


def rotation_judgment(policy: dict, position: dict, rotation_packets: list, t_dec: str,
                      evaluation_date: Optional[str] = None) -> dict:
    """Walk every visible packet after the entry anchor for the position's sector/bucket.

    Four branches of RULE.ROTATION.INTERPRETATION_OBSERVATION_GAP.V1 plus the
    ordinary release: STRONG_RELEASED -> release; chain reset (gap above the
    maximum) with the entity outside TOP -> release; reset with the entity in
    TOP -> strength continues (released later by the ratified BOTTOM-once /
    non-TOP-twice rule until the rotation layer re-confirms it); no new
    observation beyond the maximum gap -> hold with '판정 공백'.
    """
    position = validate_position(position)
    market = position["market"]
    decision_at = parse_utc(t_dec, "DECISION_TIME_INVALID")
    evaluation_date = bound_evaluation_date(policy, market, t_dec, evaluation_date)
    evaluation = parse_date(evaluation_date, "EVALUATION_DATE_INVALID")
    rotation_policy = policy["rotation_policy"]
    max_gap = rotation_policy["markets"][market]["maximum_observation_gap_days"]
    release_non_top = rotation_policy["confirmation"]["release_consecutive_non_top_observations"]
    visible, excluded = visible_rotation_packets(policy, market, rotation_packets, decision_at, evaluation)
    scope_id, entity_id = position["rotation_scope_id"], position["rotation_entity_id"]
    anchor_date = position["entry_rotation_as_of_date"]
    result = {
        "rule_id": policy["config"]["rules"]["observation_gap"]["rule_id"],
        "entity": {"scope_id": scope_id, "entity_id": entity_id},
        "entry_rotation_as_of_date": anchor_date,
        "maximum_observation_gap_days": max_gap,
        "maximum_observation_gap_days_source": {
            "path": RC.POLICY_RELATIVE_PATH,
            "pointer": f"/markets/{market}/maximum_observation_gap_days",
            "kind": policy["config"]["rules"]["observation_gap"]["maximum_observation_gap_days"]["kind"],
            "unit": policy["config"]["rules"]["observation_gap"]["maximum_observation_gap_days"]["unit"],
            "rule_id": policy["config"]["rules"]["observation_gap"]["maximum_observation_gap_days"]["rule_id"],
            "record_id": policy["config"]["records"]["rotation_max_observation_gap"]["record_id"],
            "record_sha256": policy["config"]["records"]["rotation_max_observation_gap"]["sha256"],
        },
        "chain_break_handling": policy["config"]["rules"]["observation_gap"]["chain_break_handling"],
        "visible_packet_count": len(visible),
        "excluded_not_yet_available_as_of_dates": excluded,
        "last_observed_as_of_date": None,
        "latest_packet_as_of_date": visible[-1]["packet"]["as_of_date"] if visible else None,
        "events": [],
        "trigger": None,
    }

    def finish(status: str, **extra) -> dict:
        out = result | extra | {"judgment_status": status, "display_ko": JUDGMENT_DISPLAY_KO[status]}
        out["exit_layer_new_buy_stop"] = status not in ("STRONG", "STRENGTH_CONTINUED_AFTER_GAP")
        return out

    anchor = next((entry for entry in visible if entry["packet"]["as_of_date"] == anchor_date), None)
    if anchor is None:
        return finish("ENTRY_ANCHOR_UNAVAILABLE")
    anchor_entity = _entity(anchor["packet"], scope_id, entity_id)
    if anchor_entity is None or anchor_entity["state"] not in RC.STRONG_STATES:
        return finish("ENTRY_ANCHOR_NOT_STRONG")
    mode = "STRONG"
    last_observed = anchor_date
    last_entity_missing = False
    # A chain reset whose packet does not carry this entity leaves the gap
    # unresolved: the next packet that observes the entity is the first
    # post-gap judgment (the rotation layer has already reset its streaks).
    pending_gap_return = False
    for entry in visible:
        packet = entry["packet"]
        if packet["as_of_date"] <= anchor_date or packet["observation"]["status"] != "OBSERVED":
            continue
        entity = _entity(packet, scope_id, entity_id)
        last_observed = packet["as_of_date"]
        if entity is None:
            last_entity_missing = True
            if packet["chain"]["reset"]:
                pending_gap_return = True
            result["events"].append({"as_of_date": packet["as_of_date"], "event": "ENTITY_NOT_IN_OBSERVED_PACKET"})
            continue
        last_entity_missing = False
        reset = packet["chain"]["reset"] or pending_gap_return
        via_pending = pending_gap_return
        if pending_gap_return:
            result["events"].append({"as_of_date": packet["as_of_date"], "event": "FIRST_ENTITY_JUDGMENT_AFTER_GAP_RESET_WITHOUT_ENTITY"})
        if mode == "STRONG":
            if reset:
                if not via_pending and not entity["strong_lapsed_by_gap"]:
                    _fail("ROTATION_GAP_RESET_WITHOUT_LAPSE_FLAG", packet["as_of_date"])
                pending_gap_return = False
                if entity["bucket"] == "TOP":
                    mode = "CONTINUED"
                    result["events"].append({"as_of_date": packet["as_of_date"], "event": "GAP_RETURN_INSIDE_TOP_STRENGTH_CONTINUES"})
                    continue
                result["trigger"] = _trigger(REASON_RELEASE_AFTER_GAP, entry, entity, via_pending)
                break
            if entity["state"] == "STRONG_RELEASED":
                result["trigger"] = _trigger(REASON_RELEASE, entry, entity)
                break
            if entity["state"] not in RC.STRONG_STATES:
                _fail("ROTATION_STRONG_STATE_LOST_WITHOUT_RELEASE_OR_GAP", packet["as_of_date"])
            continue
        # mode == CONTINUED (strength continued after a gap return inside TOP)
        if reset:
            pending_gap_return = False
            if entity["bucket"] == "TOP":
                result["events"].append({"as_of_date": packet["as_of_date"], "event": "GAP_RETURN_INSIDE_TOP_STRENGTH_CONTINUES"})
                continue
            result["trigger"] = _trigger(REASON_RELEASE_AFTER_GAP, entry, entity, via_pending)
            break
        if entity["state"] in RC.STRONG_STATES:
            mode = "STRONG"
            result["events"].append({"as_of_date": packet["as_of_date"], "event": "RECONFIRMED_BY_ROTATION_LAYER"})
            continue
        if entity["bucket"] == "BOTTOM" or entity["non_top_streak"] >= release_non_top:
            result["trigger"] = _trigger(REASON_RELEASE_AFTER_CONTINUATION, entry, entity)
            break
    result["last_observed_as_of_date"] = last_observed
    if result["trigger"] is not None:
        return finish("RELEASED")
    if (evaluation - parse_date(last_observed)).days > max_gap:
        return finish("OBSERVATION_GAP")
    if visible[-1]["packet"]["observation"]["status"] != "OBSERVED":
        return finish("OBSERVATION_UNKNOWN_WITHIN_MAX_GAP")
    if last_entity_missing:
        return finish("OBSERVATION_GAP" if pending_gap_return else "ENTITY_NOT_OBSERVED",
                      gap_pending_entity_not_observed_since_reset=pending_gap_return)
    return finish("STRONG" if mode == "STRONG" else "STRENGTH_CONTINUED_AFTER_GAP")


# ---------------------------------------------------------------------------
# Crypto 21-day time stop
# ---------------------------------------------------------------------------

def crypto_time_stop(policy: dict, position: dict, t_dec: str, decision_snapshot: Optional[dict] = None) -> dict:
    """First fill + 21x24h -> the first FRESH decision snapshot at or after the deadline.

    D8: the clock is the episode's first fill and top-ups never move it.
    D4 (ordinary exit): a STALE snapshot holds; the exit waits for FRESH.
    """
    position = validate_position(position)
    rule = policy["config"]["rules"]["crypto_time_stop"]
    if position["market"] not in rule["markets"]:
        return {"rule_id": rule["rule_id"], "status": "NOT_APPLICABLE", "deadline_at": None, "trigger": None}
    first_fill = parse_utc(position["first_fill_at"])
    deadline = first_fill + dt.timedelta(hours=rule["days"] * rule["day_length_hours"])
    decision_at = parse_utc(t_dec, "DECISION_TIME_INVALID")
    base = {"rule_id": rule["rule_id"], "first_fill_at": stamp(first_fill), "deadline_at": stamp(deadline), "trigger": None}
    if decision_at < deadline:
        return base | {"status": "NOT_DUE", "reason": None}
    if decision_snapshot is None:
        return base | {"status": "DUE_WAITING_FRESH_DECISION_SNAPSHOT", "reason": "NO_DECISION_SNAPSHOT"}
    snapshot_id = _token(decision_snapshot.get("snapshot_id"), "DECISION_SNAPSHOT_ID_INVALID")
    captured = parse_utc(decision_snapshot.get("captured_at"), "DECISION_SNAPSHOT_CAPTURED_AT_INVALID")
    if captured > decision_at:
        _fail("DECISION_SNAPSHOT_AFTER_DECISION_TIME", snapshot_id)
    freshness = decision_snapshot.get("freshness")
    if captured < deadline:
        return base | {"status": "DUE_WAITING_FRESH_DECISION_SNAPSHOT", "reason": "DECISION_SNAPSHOT_BEFORE_DEADLINE"}
    if freshness != "FRESH":
        return base | {"status": "DUE_WAITING_FRESH_DECISION_SNAPSHOT", "reason": f"DECISION_SNAPSHOT_{freshness}"}
    return base | {
        "status": "DUE",
        "reason": None,
        "trigger": {
            "reason_code": REASON_TIME_STOP,
            "fact": "CLOCK",
            "deadline_at": stamp(deadline),
            "decision_snapshot_id": snapshot_id,
            "decision_snapshot_captured_at": stamp(captured),
        },
    }


# ---------------------------------------------------------------------------
# paper_exit_intent/1
# ---------------------------------------------------------------------------

def intent_id_for(position: dict, trigger: dict) -> str:
    fact = (
        {"deadline_at": trigger["deadline_at"]} if trigger["reason_code"] == REASON_TIME_STOP
        else {"packet_payload_sha256": trigger["packet_payload_sha256"], "packet_as_of_date": trigger["packet_as_of_date"]}
    )
    return payload_sha256({
        "schema_version": INTENT_SCHEMA_VERSION,
        "market": position["market"],
        "symbol": position["symbol"],
        "position_episode_id": position["position_episode_id"],
        "reason_code": trigger["reason_code"],
        "fact": fact,
    })


def _trigger_available_at(trigger: dict) -> dt.datetime:
    if trigger["reason_code"] == REASON_TIME_STOP:
        return parse_utc(trigger["deadline_at"])
    return parse_utc(trigger["packet_available_at"])


def build_exit_intent(policy: dict, position: dict, trigger: dict, t_dec: str, calendar: Optional[dict] = None) -> dict:
    """Full-position SELL intent bound to the exit ratification record (sha 47276abe...).

    Timestamps (canon 1-1): t_obs = the fact's observation (rotation as-of
    date or the clock deadline), t_avail = when Atlas had it, t_dec = the
    decision slot, t_ord = t_dec, t_fill = first allowed fill (null until a
    fill event is recorded in the store).
    """
    position = validate_position(position)
    decision_at = parse_utc(t_dec, "DECISION_TIME_INVALID")
    available = _trigger_available_at(trigger)
    if decision_at < available:
        _fail("DECISION_BEFORE_FACT_AVAILABLE", trigger["reason_code"])
    reason = trigger["reason_code"]
    if reason in RELEASE_REASONS:
        rule_key = "release_full_sell"
        t_obs = {"precision": "DATE", "as_of_date": trigger["packet_as_of_date"]}
    elif reason == REASON_TIME_STOP:
        rule_key = "crypto_time_stop"
        if position["market"] != "CRYPTO":
            _fail("TIME_STOP_MARKET_INVALID", position["market"])
        t_obs = {"precision": "INSTANT", "at": trigger["deadline_at"]}
    else:
        _fail("EXIT_REASON_INVALID", str(reason))
    refs = [rule_ref(policy, rule_key, "EXITED_BY"), rule_ref(policy, "time_contract", "APPLIED"),
            rule_ref(policy, "data_failure_priority", "APPLIED")]
    ratifications = [{"record_id": policy["config"]["records"]["exit_provisional_v1"]["record_id"],
                      "sha256": policy["config"]["records"]["exit_provisional_v1"]["sha256"]}]
    if reason in (REASON_RELEASE_AFTER_GAP, REASON_RELEASE_AFTER_CONTINUATION):
        refs.append(rule_ref(policy, "observation_gap", "APPLIED"))
        ratifications.append({"record_id": policy["config"]["records"]["rotation_observation_gap"]["record_id"],
                              "sha256": policy["config"]["records"]["rotation_observation_gap"]["sha256"]})
    window = first_allowed_fill_window(policy, position["market"], stamp(decision_at), calendar)
    intent = {
        "schema_version": INTENT_SCHEMA_VERSION,
        "intent_id": intent_id_for(position, trigger),
        "market": position["market"],
        "symbol": position["symbol"],
        "position_episode_id": position["position_episode_id"],
        "rotation_entity": {"scope_id": position["rotation_scope_id"], "entity_id": position["rotation_entity_id"]},
        "side": "SELL",
        "quantity_basis": "FULL_POSITION",
        "quantity": decimal_text(parse_decimal(position["quantity"], "POSITION_QUANTITY_INVALID", positive=True)),
        "reason_code": reason,
        "trigger": copy.deepcopy(trigger),
        "ratification_records": ratifications,
        "rule_refs": _sorted_refs(refs, policy),
        "timestamps": {
            "t_obs": t_obs,
            "t_avail": stamp(available),
            "t_dec": stamp(decision_at),
            "t_ord": stamp(decision_at),
            "t_fill": None,
        },
        "first_allowed_fill": {k: v for k, v in window.items() if k != "rule_ref"},
        "persistence": "UNTIL_POSITION_FULLY_SOLD",
        "stale_price_policy": "HOLD_WHILE_STALE_ORDINARY_EXIT",
        "authority": authority(policy),
    }
    return with_payload_sha(intent)


def validate_intent(intent: dict) -> dict:
    verify_payload_sha(intent, "EXIT_INTENT_SHA_MISMATCH")
    if intent.get("schema_version") != INTENT_SCHEMA_VERSION or intent.get("side") != "SELL":
        _fail("EXIT_INTENT_SCHEMA_INVALID")
    if intent["timestamps"]["t_fill"] is not None:
        _fail("EXIT_INTENT_RECORD_MUST_NOT_CARRY_FILL")
    if intent["timestamps"]["t_ord"] != intent["timestamps"]["t_dec"]:
        _fail("EXIT_INTENT_ORDER_TIME_NOT_DECISION_TIME")
    if parse_utc(intent["timestamps"]["t_dec"]) < parse_utc(intent["timestamps"]["t_avail"]):
        _fail("DECISION_BEFORE_FACT_AVAILABLE")
    if PINNED_RECORD_SHA256["exit_provisional_v1"] not in [r["sha256"] for r in intent["ratification_records"]]:
        _fail("EXIT_INTENT_NOT_BOUND_TO_EXIT_RECORD")
    if any(value is not False for value in intent["authority"].values()):
        _fail("EXIT_INTENT_AUTHORITY_MUST_BE_FALSE")
    return intent


class ExitIntentStore:
    """Append-only on-disk intent store (survives restarts and date changes).

    ``<root>/intents/<intent_id>.json`` is written once (atomic rename) and
    never rewritten; ``<root>/events/<intent_id>.jsonl`` is an append-only,
    hash-chained fill log. An intent stays OPEN until the recorded fills sum to
    its quantity. One OPEN intent per (market, position episode).
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def _intent_path(self, intent_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", str(intent_id)):
            _fail("EXIT_INTENT_ID_INVALID", str(intent_id))
        return self.root / "intents" / f"{intent_id}.json"

    def _events_path(self, intent_id: str) -> Path:
        self._intent_path(intent_id)
        return self.root / "events" / f"{intent_id}.jsonl"

    def intents(self) -> list:
        folder = self.root / "intents"
        if not folder.is_dir():
            return []
        result = []
        for path in sorted(folder.glob("*.json")):
            intent = validate_intent(_read_json(path))
            if path.stem != intent["intent_id"]:
                _fail("EXIT_INTENT_FILENAME_MISMATCH", path.name)
            result.append(intent)
        return result

    def get(self, intent_id: str) -> Optional[dict]:
        path = self._intent_path(intent_id)
        return validate_intent(_read_json(path)) if path.exists() else None

    def events(self, intent_id: str) -> list:
        path = self._events_path(intent_id)
        if not path.exists():
            return []
        events, previous = [], None
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            event = verify_payload_sha(json.loads(line), "EXIT_INTENT_EVENT_SHA_MISMATCH")
            if event.get("schema_version") != INTENT_EVENT_SCHEMA_VERSION or event["intent_id"] != intent_id:
                _fail("EXIT_INTENT_EVENT_INVALID", str(index))
            if event["sequence"] != index or event["previous_event_sha256"] != previous:
                _fail("EXIT_INTENT_EVENT_CHAIN_BROKEN", str(index))
            previous = event["payload_sha256"]
            events.append(event)
        return events

    def status(self, intent_id: str) -> dict:
        intent = self.get(intent_id)
        if intent is None:
            _fail("EXIT_INTENT_NOT_FOUND", intent_id)
        events = self.events(intent_id)
        filled = sum((parse_decimal(e["fill"]["quantity"], "FILL_QUANTITY_INVALID") for e in events), Decimal(0))
        total = parse_decimal(intent["quantity"], "EXIT_INTENT_QUANTITY_INVALID", positive=True)
        state = "FILLED" if filled >= total else "PARTIALLY_FILLED" if filled > 0 else "OPEN"
        return {
            "intent_id": intent_id,
            "status": state,
            "quantity": intent["quantity"],
            "filled_quantity": decimal_text(filled),
            "remaining_quantity": decimal_text(max(Decimal(0), total - filled)),
            "t_fill_first": events[0]["fill"]["t_obs"] if events else None,
            "t_fill_last": events[-1]["fill"]["t_obs"] if events else None,
        }

    def open_intent_for_episode(self, market: str, position_episode_id: str) -> Optional[dict]:
        found = [
            intent for intent in self.intents()
            if intent["market"] == market and intent["position_episode_id"] == position_episode_id
        ]
        open_ = [intent for intent in found if self.status(intent["intent_id"])["status"] != "FILLED"]
        if len(open_) > 1:
            _fail("MULTIPLE_OPEN_EXIT_INTENTS_FOR_EPISODE", position_episode_id)
        return open_[0] if open_ else None

    def filled_intents_for_episode(self, market: str, position_episode_id: str) -> list:
        return [
            intent for intent in self.intents()
            if intent["market"] == market and intent["position_episode_id"] == position_episode_id
            and self.status(intent["intent_id"])["status"] == "FILLED"
        ]

    def put_intent(self, intent: dict) -> str:
        intent = validate_intent(intent)
        path = self._intent_path(intent["intent_id"])
        data = render_json(intent)
        if path.exists():
            if path.read_bytes() != data:
                _fail("EXIT_INTENT_APPEND_ONLY_CONFLICT", intent["intent_id"])
            return "ALREADY_PRESENT"
        existing = self.open_intent_for_episode(intent["market"], intent["position_episode_id"])
        if existing is not None:
            _fail("OPEN_EXIT_INTENT_ALREADY_EXISTS_FOR_EPISODE", existing["intent_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        return "CREATED"

    def record_fill(self, policy: dict, intent_id: str, fill: dict, calendar: Optional[dict] = None) -> dict:
        """Append one SELL fill. Refuses STALE/unverified prices, fills not strictly
        after the order time, fills outside the ratified window, and over-fills."""
        intent = self.get(intent_id)
        if intent is None:
            _fail("EXIT_INTENT_NOT_FOUND", intent_id)
        fill_id = _token(fill.get("fill_id"), "FILL_ID_INVALID")
        for event in self.events(intent_id):
            if event["fill"]["fill_id"] == fill_id:
                if canonical_json(event["fill"]) != canonical_json(_normalized_fill(fill)):
                    _fail("FILL_ID_REUSED_WITH_DIFFERENT_CONTENT", fill_id)
                return event
        normalized = _normalized_fill(fill)
        if normalized["price_status"] != "FRESH":
            _fail("FILL_PRICE_NOT_VERIFIED_FRESH", normalized["price_status"])
        if parse_utc(normalized["t_obs"]) <= parse_utc(intent["timestamps"]["t_ord"]):
            _fail("FILL_NOT_AFTER_ORDER_TIME", fill_id)
        allowed, reason = is_allowed_fill_time(policy, intent["market"], normalized["t_obs"], calendar)
        if not allowed:
            _fail("FILL_OUTSIDE_ALLOWED_FILL_TIME", str(reason))
        status = self.status(intent_id)
        if status["status"] == "FILLED":
            _fail("EXIT_INTENT_ALREADY_FILLED", intent_id)
        quantity = parse_decimal(normalized["quantity"], "FILL_QUANTITY_INVALID", positive=True)
        remaining = parse_decimal(status["remaining_quantity"], "REMAINING_INVALID")
        if quantity > remaining:
            _fail("FILL_QUANTITY_EXCEEDS_REMAINING", fill_id)
        events = self.events(intent_id)
        after = remaining - quantity
        event = with_payload_sha({
            "schema_version": INTENT_EVENT_SCHEMA_VERSION,
            "intent_id": intent_id,
            "sequence": len(events) + 1,
            "previous_event_sha256": events[-1]["payload_sha256"] if events else None,
            "event_type": "FILL",
            "fill": normalized,
            "t_fill": normalized["t_obs"],
            "remaining_quantity_after": decimal_text(after),
            "status_after": "FILLED" if after == 0 else "PARTIALLY_FILLED",
        })
        path = self._events_path(intent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(canonical_json(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event


def _normalized_fill(fill: dict) -> dict:
    if not isinstance(fill, dict):
        _fail("FILL_INVALID")
    return {
        "fill_id": _token(fill.get("fill_id"), "FILL_ID_INVALID"),
        "t_obs": stamp(parse_utc(fill.get("t_obs"), "FILL_T_OBS_INVALID")),
        "price": decimal_text(parse_decimal(fill.get("price"), "FILL_PRICE_INVALID", positive=True)),
        "quantity": decimal_text(parse_decimal(fill.get("quantity"), "FILL_QUANTITY_INVALID", positive=True)),
        "price_status": fill.get("price_status"),
        "price_source": _token(fill.get("price_source"), "FILL_PRICE_SOURCE_INVALID"),
    }


# ---------------------------------------------------------------------------
# Per-position evaluation
# ---------------------------------------------------------------------------

def evaluate_position(policy: dict, position: dict, *, t_dec: str, rotation_packets: list, evaluation_date: Optional[str] = None,
                      store: Optional[ExitIntentStore] = None, decision_snapshot: Optional[dict] = None,
                      calendar: Optional[dict] = None) -> dict:
    """One decision slot for one open position episode.

    Order: an intent already OPEN in the store wins (it persists regardless
    of today's packet). Otherwise the earliest available exit fact among the
    rotation release (history walk) and the crypto 21-day stop becomes a new
    intent, written to the store when one is given. Otherwise HOLD.
    """
    position = validate_position(position)
    decision_at = parse_utc(t_dec, "DECISION_TIME_INVALID")
    evaluation_date = bound_evaluation_date(policy, position["market"], stamp(decision_at), evaluation_date)
    judgment = rotation_judgment(policy, position, rotation_packets, stamp(decision_at), evaluation_date)
    time_stop = crypto_time_stop(policy, position, stamp(decision_at), decision_snapshot)
    existing = None
    if store is not None:
        if store.filled_intents_for_episode(position["market"], position["position_episode_id"]):
            _fail("POSITION_OPEN_AFTER_EXIT_INTENT_FILLED", position["position_episode_id"])
        existing = store.open_intent_for_episode(position["market"], position["position_episode_id"])
    refs = [rule_ref(policy, "observation_gap", "APPLIED"), rule_ref(policy, "release_full_sell", "APPLIED")]
    if position["market"] == "CRYPTO":
        refs.append(rule_ref(policy, "crypto_time_stop", "APPLIED"))
    intent, write_status = None, None
    if existing is not None:
        action, intent = "EXIT_INTENT_OPEN", existing
        write_status = "ALREADY_OPEN_IN_STORE"
    else:
        candidates = [t for t in (judgment["trigger"], time_stop["trigger"]) if t is not None]
        if candidates:
            chosen = min(candidates, key=lambda t: (_trigger_available_at(t), 0 if t["reason_code"] in RELEASE_REASONS else 1))
            intent = build_exit_intent(policy, position, chosen, stamp(decision_at), calendar)
            write_status = store.put_intent(intent) if store is not None else "NOT_PERSISTED_NO_STORE"
            action = "EXIT_INTENT_CREATED"
        else:
            action = "HOLD"
    if intent is not None:
        refs += [ref for ref in intent["rule_refs"] if ref["role"] == "EXITED_BY"]
    evaluation = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "market": position["market"],
        "symbol": position["symbol"],
        "position_episode_id": position["position_episode_id"],
        "evaluation_date": evaluation_date,
        "t_dec": stamp(decision_at),
        "action": action,
        "holding_status": "EXIT_PENDING_FILL" if intent is not None else "HOLD",
        "judgment": judgment,
        "judgment_display_ko": judgment["display_ko"],
        "exit_layer_new_buy_stop": judgment["exit_layer_new_buy_stop"] or intent is not None,
        "crypto_time_stop": time_stop,
        "exit_intent": intent,
        "exit_intent_store_status": store.status(intent["intent_id"]) if (store is not None and intent is not None) else None,
        "exit_intent_write_status": write_status,
        "defaults_without": ["DISASTER_STOP", "PARTIAL_TAKE_PROFIT"],
        "rule_refs": _sorted_refs(refs, policy),
        "authority": authority(policy),
    }
    return with_payload_sha(evaluation)


# ---------------------------------------------------------------------------
# Display overlay for the #752 wiring / portal outputs (policy file untouched)
# ---------------------------------------------------------------------------

EFFECTIVE_RELEASE_TEXT_KO = "강세 해제가 확인되면 신규 매수를 멈추고, 보유분은 첫 허용 체결 시각에 전량 매도합니다(임시값)."
SUPERSEDED_PORTAL_TEXT_KO = "강세 해제 시 신규 매수만 중단하고 보유분은 강제 청산하지 않습니다."


def overlay_held_position_action(policy: dict, wiring_output: dict, at_utc: Optional[str] = None) -> dict:
    """Mark ``held_position_action`` from ``new_buy_permission`` as superseded.

    The original field value is kept as produced (ratified rotation policy
    unchanged). The copy gains ``held_position_action_superseded`` with the
    registry's partial supersession (relation SUPERSEDED_BY ->
    RULE.EXIT.RELEASE_FULL_SELL.V1), and its ``rule_refs`` are re-issued
    registry-exact (null ``registry_sha256`` from the wiring filled) with
    RULE.EXIT.RELEASE_FULL_SELL.V1 / APPLIED added.
    """
    rule = policy["config"]["rules"]["release_full_sell"]
    if not isinstance(wiring_output, dict) or "held_position_action" not in wiring_output:
        _fail("WIRING_OUTPUT_INVALID")
    if wiring_output["held_position_action"] != rule["supersedes"]["superseded_value"]:
        _fail("WIRING_HELD_POSITION_ACTION_UNEXPECTED", str(wiring_output["held_position_action"]))
    result = copy.deepcopy(wiring_output)
    released = bool(result.get("release_new_buy_stop"))
    result["held_position_action_superseded"] = {
        "superseded_rule_id": rule["supersedes"]["rule_id"],
        "superseded_value": rule["supersedes"]["superseded_value"],
        "superseded_by_rule_id": rule["rule_id"],
        "superseded_by_record_sha256": policy["config"]["records"]["exit_provisional_v1"]["sha256"],
        "effective_held_position_action": rule["effective_held_position_action"],
        "new_buy_stop_retained": rule["supersedes"]["new_buy_stop_retained"],
        "exit_intent_required": released,
        "display_ko": EFFECTIVE_RELEASE_TEXT_KO,
    }
    block = supersession_block(policy)
    if at_utc is not None:
        target = policy["registry"].rules[block["rule_id"]]
        at = stamp(parse_utc(at_utc, "OVERLAY_AT_UTC_INVALID"))
        block["superseded_part_in_force_at"] = {
            "at_utc": at,
            "superseded_value_in_force": REG.part_in_force_at(target, block["key_parameter"], at, policy["registry"].registry),
        }
    result["held_position_action_superseded"]["registry_supersession"] = block
    pairs = [(ref["rule_id"], ref["role"]) for ref in result.get("rule_refs") or []]
    pairs.append((rule["rule_id"], "APPLIED"))
    if SUPERSESSION_RELATION in ROLES:  # additive library role, if a later PR registers it
        pairs.append((rule["rule_id"], SUPERSESSION_RELATION))
    try:
        result["rule_refs"] = RR.canonical_rule_refs(pairs, policy["registry"])
    except RR.RuleLineageError as exc:
        _fail("RULE_REFS_NOT_REGISTRY_EXACT", str(exc))
    return result


def overlay_portal_projection(policy: dict, projection: dict) -> dict:
    """Portal ch02 display copy: the old release text is shown as superseded."""
    rules = projection.get("display_rules_ko") if isinstance(projection, dict) else None
    if not isinstance(rules, list) or SUPERSEDED_PORTAL_TEXT_KO not in rules:
        _fail("PORTAL_PROJECTION_RELEASE_TEXT_NOT_FOUND")
    result = copy.deepcopy(projection)
    result.pop("payload_sha256", None)
    result["display_rules_superseded_ko"] = [{
        "superseded_text_ko": SUPERSEDED_PORTAL_TEXT_KO,
        "effective_text_ko": EFFECTIVE_RELEASE_TEXT_KO,
        "rule_refs": [rule_ref(policy, "release_full_sell", "APPLIED")],
        "registry_supersession": supersession_block(policy),
    }]
    result["source_projection_payload_sha256"] = projection.get("payload_sha256")
    return with_payload_sha(result)
