#!/usr/bin/env python3
"""``RULE.LIQUIDITY.US_SIP_SOURCE.V1`` -- pure US T2 C3 liquidity evaluator.

Ratified rule (user ratification 2026-09-15,
``USER_RATIFICATION_US_LIQUIDITY_SIP_SOURCE_20260915`` -- not itself a repo
file; see the threshold note below):

  * primary_source: Alpaca historical SIP daily bars, only bars whose
    regular-session close is at least 15 minutes in the past.
  * metric: 20-session average traded value (dollar volume).
  * fallback: if SIP is unavailable, IEX traded value >= threshold -> PASS;
    below threshold -> UNKNOWN (never FAIL on IEX alone, because IEX-only
    volume is a known partial view of the tape, not a confident negative).
  * expansion beyond the approved 22-symbol universe needs a separate
    approval; this module and its caller never expand that universe.

This module makes NO network call, reads NO Alpaca credential, and reads NO
raw per-day bar.  It turns a caller-supplied 20-session traded-value
observation (already aggregated by ``collectors/alpaca_sip_daily_bars.py``)
into the rule's PASS/FAIL/UNKNOWN verdict.  ``REQUIRED_SESSION_WINDOW = 20``
is the rule's own window *definition* (matching the "20-session" window
convention already used elsewhere in this repo -- e.g.
``config/free_market_data_contract.json:22`` ``return_windows_sessions``,
``config/rotation_confirmation_policy_v1.json:99``
``strength_window_sessions``) -- it is not a threshold number.

★ Threshold status (2026-09-15 CIO search, recorded here so a reviewer does
  not have to repeat it): the ratified USD amount for this rule
  (``avg_dollar_volume_20_sessions_usd_min = 10000000``,
  ``last_close_usd_min = 5``) lives in
  ``outputs/USER_RATIFICATION_PAPER_LIQUIDITY_KR_US_20260914.json`` -- a CIO
  planning-workspace record, NOT a file in this git repository. Every
  liquidity-threshold surface actually committed to this repo says the same
  thing: ``config/us_investable_registry_contract.json``
  (``liquidity.repository_default_policy == "ABSENT"``,
  ``policy_requirement == "EXTERNAL_RATIFIED_POLICY_REQUIRED"``),
  ``config/krx_investable_registry_contract.json``
  (``measurement_policy.turnover.status == "UNRATIFIED"``,
  ``proposed_threshold: null``), and ``universe/krx_investable_registry.py``'s
  own docstring ("does not ... invent liquidity thresholds"). This PR's scope
  is narrow (per the CIO build plan's PR6 row) and does not commit a new
  ratified policy packet -- doing so is a separate, deliberate follow-up.
  ``load_policy()`` therefore returns ``None`` when
  ``config/us_liquidity_sip_source_policy.json`` does not exist (true today),
  and ``evaluate_symbol_liquidity`` maps that to
  ``UNKNOWN`` / ``LIQUIDITY_THRESHOLD_POLICY_ABSENT_FROM_REPO`` rather than
  inventing a number. A future PR that commits a ratified
  ``us_liquidity_sip_source_policy/1`` packet at that path activates real
  PASS/FAIL evaluation with no code change here; the tests in
  ``test/test_us_liquidity_sip_source.py`` already exercise the real
  arithmetic against a mocked policy.
"""
from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "us_liquidity_sip_source_policy.json"

RULE_ID = "RULE.LIQUIDITY.US_SIP_SOURCE.V1"
POLICY_SCHEMA_VERSION = "us_liquidity_sip_source_policy/1"
RESULT_SCHEMA_VERSION = "us_liquidity_sip_source_result/1"

# The rule's own window definition (see module docstring) -- not a threshold.
REQUIRED_SESSION_WINDOW = 20

FEEDS = ("sip", "iex")
NOTIONAL_FORMULAS = ("CLOSE_TIMES_VOLUME",)

_TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,63}$")
_DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INSTANT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class UsLiquiditySipSourceError(ValueError):
    """A contract, policy, or observation shape is invalid -- fail closed."""


def _fail(code: str, detail: str = "") -> None:
    raise UsLiquiditySipSourceError(f"{code}:{detail}" if detail else code)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def payload_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _token(value: object, code: str) -> str:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _decimal(value: object, code: str) -> Decimal:
    if not isinstance(value, str) or _DECIMAL_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return Decimal(value)
    except InvalidOperation:
        _fail(code)


def _instant(value: object, code: str) -> str:
    if not isinstance(value, str) or _INSTANT_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _date(value: object, code: str) -> str:
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        _fail(code)
    return value


# ─────────────────────────────────────────────────────────────────────────
# Policy (external, ratified, and -- as of this PR -- absent from the repo)
# ─────────────────────────────────────────────────────────────────────────

_POLICY_FIELDS = {
    "schema_version", "policy_id", "approval_status", "ratified_by",
    "ratified_at", "min_avg_traded_value_usd", "packet_sha256",
}


def _validate_policy(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != _POLICY_FIELDS:
        _fail("POLICY_FIELDS_INVALID")
    if value["schema_version"] != POLICY_SCHEMA_VERSION:
        _fail("POLICY_SCHEMA_INVALID")
    if value["approval_status"] != "RATIFIED":
        _fail("POLICY_NOT_RATIFIED")
    _token(value["policy_id"], "POLICY_ID_INVALID")
    if not isinstance(value["ratified_by"], str) or not value["ratified_by"].strip():
        _fail("POLICY_RATIFIED_BY_INVALID")
    _instant(value["ratified_at"], "POLICY_RATIFIED_AT_INVALID")
    _decimal(value["min_avg_traded_value_usd"], "POLICY_THRESHOLD_INVALID")
    digest = _sha(value["packet_sha256"], "POLICY_SHA_INVALID")
    body = copy.deepcopy(value)
    body.pop("packet_sha256")
    if payload_sha256(body) != digest:
        _fail("POLICY_SHA_MISMATCH")
    return copy.deepcopy(value)


def load_policy(path: Path = POLICY_PATH) -> Optional[dict]:
    """Return the ratified threshold packet, or ``None`` if none is committed.

    A missing file is the expected, handled state today (see module
    docstring) -- it is NOT an error. A file that exists but fails
    validation IS an error: presence implies it should already be a valid
    ratified packet.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("POLICY_UNREADABLE", str(exc))
    return _validate_policy(raw)


# ─────────────────────────────────────────────────────────────────────────
# Per-feed observation (already aggregated by the caller; no raw bars here)
# ─────────────────────────────────────────────────────────────────────────

_OBSERVATION_FIELDS = {
    "feed", "avg_traded_value_usd", "session_count", "window_end",
    "notional_formula", "source_ref",
}


def _validate_observation(value: object, expected_feed: str) -> dict:
    if not isinstance(value, dict) or set(value) != _OBSERVATION_FIELDS:
        _fail(f"{expected_feed.upper()}_OBSERVATION_FIELDS_INVALID")
    if value["feed"] != expected_feed:
        _fail(f"{expected_feed.upper()}_OBSERVATION_FEED_MISMATCH")
    _decimal(value["avg_traded_value_usd"], f"{expected_feed.upper()}_AVG_TRADED_VALUE_INVALID")
    session_count = value["session_count"]
    if type(session_count) is not int or session_count < 0:
        _fail(f"{expected_feed.upper()}_SESSION_COUNT_INVALID")
    _date(value["window_end"], f"{expected_feed.upper()}_WINDOW_END_INVALID")
    if value["notional_formula"] not in NOTIONAL_FORMULAS:
        _fail(f"{expected_feed.upper()}_NOTIONAL_FORMULA_INVALID")
    if not isinstance(value["source_ref"], str) or not value["source_ref"].strip():
        _fail(f"{expected_feed.upper()}_SOURCE_REF_INVALID")
    return copy.deepcopy(value)


# ─────────────────────────────────────────────────────────────────────────
# The rule itself
# ─────────────────────────────────────────────────────────────────────────

def evaluate_symbol_liquidity(
    symbol: str,
    sip: Optional[dict],
    iex: Optional[dict],
    policy: Optional[dict],
) -> dict:
    """Apply RULE.LIQUIDITY.US_SIP_SOURCE.V1 to one symbol.

    ``sip``/``iex`` are ``None`` or a dict matching ``_OBSERVATION_FIELDS``
    (feed, avg_traded_value_usd, session_count, window_end,
    notional_formula, source_ref) -- an already-aggregated 20-session
    observation, never a per-day bar.

    Feed selection (independent of whether a threshold policy exists, so
    the derived average/session-count/feed are always reported when the
    data allows it -- only the PASS/FAIL/UNKNOWN *status* depends on the
    threshold):
      1. SIP present with a full session window -> use SIP.
      2. Otherwise, IEX present with a full session window -> use IEX.
      3. Otherwise -> no usable observation; status is UNKNOWN.

    Status, exactly as ratified, once a feed is selected:
      * Policy absent -> UNKNOWN (never invented; see module docstring).
      * Selected feed is SIP -> PASS if its average meets the threshold,
        FAIL otherwise (SIP is the complete tape, so a confirmed shortfall
        is a confident negative).
      * Selected feed is IEX (fallback) -> PASS if it meets the threshold,
        else UNKNOWN (never FAIL -- IEX alone is a known-partial view and
        cannot confirm a shortfall).
    """
    symbol = _token(symbol, "SYMBOL_INVALID")
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "rule_id": RULE_ID,
        "symbol": symbol,
        "status": "UNKNOWN",
        "source_feed_used": None,
        "avg_traded_value_usd": None,
        "session_count": None,
        "window_end": None,
        "threshold_usd": None,
        "min_session_window": REQUIRED_SESSION_WINDOW,
        "policy_id": None,
        "reasons": [],
    }

    reasons: list[str] = []
    sip_obs = _validate_observation(sip, "sip") if sip is not None else None
    iex_obs = _validate_observation(iex, "iex") if iex is not None else None

    chosen_feed: Optional[str] = None
    chosen_obs: Optional[dict] = None
    if sip_obs is not None and sip_obs["session_count"] >= REQUIRED_SESSION_WINDOW:
        chosen_feed, chosen_obs = "sip", sip_obs
    else:
        if sip_obs is not None:
            reasons.append("SIP_WINDOW_INSUFFICIENT_SESSIONS")
        if iex_obs is not None and iex_obs["session_count"] >= REQUIRED_SESSION_WINDOW:
            chosen_feed, chosen_obs = "iex", iex_obs
        elif iex_obs is not None:
            reasons.append("IEX_WINDOW_INSUFFICIENT_SESSIONS")

    if chosen_feed is None:
        result["reasons"] = reasons or ["NO_FEED_DATA_AVAILABLE"]
        return result

    result.update(
        source_feed_used=chosen_feed,
        avg_traded_value_usd=chosen_obs["avg_traded_value_usd"],
        session_count=chosen_obs["session_count"],
        window_end=chosen_obs["window_end"],
    )

    if policy is None:
        result["status"] = "UNKNOWN"
        result["reasons"] = reasons + ["LIQUIDITY_THRESHOLD_POLICY_ABSENT_FROM_REPO"]
        return result

    policy = _validate_policy(policy)
    threshold = Decimal(policy["min_avg_traded_value_usd"])
    result["threshold_usd"] = policy["min_avg_traded_value_usd"]
    result["policy_id"] = policy["policy_id"]
    avg = Decimal(chosen_obs["avg_traded_value_usd"])
    if chosen_feed == "sip":
        status = "PASS" if avg >= threshold else "FAIL"
        if status != "PASS":
            reasons.append("SIP_BELOW_THRESHOLD")
    else:
        status = "PASS" if avg >= threshold else "UNKNOWN"
        if status != "PASS":
            reasons.append("IEX_FALLBACK_BELOW_THRESHOLD")
    result["status"] = status
    result["reasons"] = reasons
    return result
