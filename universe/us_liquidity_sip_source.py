#!/usr/bin/env python3
"""``RULE.LIQUIDITY.US_SIP_SOURCE.V1`` -- pure US T2 C3 liquidity evaluator.

Ratified rules this module implements, bound to their committed evidence
(see ``EVIDENCE_REFS`` / ``config/us_liquidity_sip_source_policy.json``,
each cross-checked by sha256 against the byte-identical copy committed at
``evidence/authority/``):

  * base record (``PAPER-LIQUIDITY-KR-US-V1-20260914``), US column:
    ``avg_dollar_volume_20_sessions_usd_min = 10000000``,
    ``last_close_usd_min = 5``, exclude OTC / non-exchange-listed, window
    "20 completed US sessions; fewer than 20 -> NOT_EVALUATED (no T2)",
    fail_closed "missing price history, missing halt/admin/exchange flag,
    or stale data -> C3 UNKNOWN -> no T2".
  * SIP-source record (``USER_RATIFICATION_US_LIQUIDITY_SIP_SOURCE_20260915``,
    rule ``RULE.LIQUIDITY.US_SIP_SOURCE.V1``): primary_source = Alpaca
    historical SIP daily bars, only bars whose regular-session close is at
    least 15 minutes in the past; metric = 20-session average traded
    value; fallback = if SIP is unavailable, IEX traded value >= threshold
    -> PASS, below -> UNKNOWN (never FAIL on IEX alone -- IEX-only volume
    is a known-partial view of the tape, not a confident negative).

This module makes NO network call, reads NO Alpaca credential, and reads NO
raw per-day bar. ``evaluate_symbol_liquidity`` turns a caller-supplied
20-session traded-value observation (already aggregated by
``collectors/alpaca_sip_daily_bars.py``) into the composite verdict.
``REQUIRED_SESSION_WINDOW = 20`` is the rule's own window *definition*
(matching the "20-session" window convention already used elsewhere in
this repo -- e.g. ``config/free_market_data_contract.json:22``
``return_windows_sessions``, and the same 20-session strength window this
repo's own capital-rotation confirmation policy also uses) -- it is not a
threshold number.

★ Status vocabulary, exactly as the base record's own words (2026-09-15
  CIO correction -- an earlier revision of this module used ``UNKNOWN`` for
  both cases; that conflated two different facts the record itself keeps
  separate):
    - fewer than ``REQUIRED_SESSION_WINDOW`` completed sessions on every
      available feed -> ``NOT_EVALUATED`` ("no T2" -- there is nothing yet
      to judge, not a judgment that came back inconclusive).
    - a real judgment was attempted but an input was missing, stale, or the
      evaluated feed cannot confirm/deny (IEX-only shortfall) ->
      ``UNKNOWN``.
    - a definite, evidence-backed negative (SIP-confirmed shortfall, a
      confirmed sub-$5 close, or a confirmed OTC/non-exchange-listed venue)
      -> ``FAIL``.
    - every sub-check PASS -> ``PASS``.

★ The OTC / non-exchange-listed exclusion (2026-09-15 CIO wiring): without
  a listing input, every US name's ``otc_exclusion_status`` was ``UNKNOWN``,
  which the base record's own fail_closed clause turns into an overall
  ``UNKNOWN`` for every name -- the reader could never PASS. Rather than
  leave that unresolved, ``exchange_listing_status`` is now sourced from
  ``universe/us_listing_lookup.py``, which reads the already-committed
  Nasdaq Trader Symbol Directory capture at
  ``data/observations/us_global_universe/<date>/packet.json``
  (``universe/us_global_universe.py``, contract
  ``us_global_universe_adapter/1`` -- untouched by this change; see that
  module's docstring for the point-in-time packet-selection rule and the
  packet's own field definitions this reads). ``exchange_listing_status``
  is still honestly ``None`` (-> ``UNKNOWN``) whenever that lookup itself
  can't resolve a symbol (no packet as-of the evaluation date, or the
  symbol absent from the selected packet) -- this module never assumes.
  ``"TEST_ISSUE"`` is accepted alongside ``"OTC"``/``"EXCHANGE_LISTED"``:
  Nasdaq's own confirmed-test-security flag is a distinct, evidence-backed
  exclusion, reported with its own reason even though it currently folds
  into the same ``otc_exclusion_status`` sub-check as OTC.

★ Threshold status: the ratified USD amounts above are now bound via
  ``config/us_liquidity_sip_source_policy.json``, sha256-cross-checked
  against ``evidence/authority/paper_liquidity_kr_us_user_ratification_
  20260914.json`` and ``evidence/authority/us_liquidity_sip_source_user_
  ratification_20260915.json``. ``load_policy()`` returns ``None`` --
  never inventing a number -- if that policy file is missing, malformed,
  or if either evidence file is missing or its sha256 no longer matches
  the value cited in the policy (fail closed on tamper/drift, per the
  base record's own fail_closed clause); every sub-check then reports
  ``UNKNOWN`` rather than silently reusing a stale number. Use
  ``describe_policy()`` for a non-raising diagnostic of *why*.
"""
from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys
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
EXCHANGE_LISTING_STATUSES = ("EXCHANGE_LISTED", "OTC", "TEST_ISSUE")
ALLOWED_STATUSES = ("PASS", "FAIL", "UNKNOWN", "NOT_EVALUATED")

_TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,63}$")
_DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INSTANT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_RELATIVE_PATH_RE = re.compile(r"^evidence/authority/[A-Za-z0-9_.-]+\.json$")


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
# Policy: the ratified threshold, sha256-bound to its committed evidence
# ─────────────────────────────────────────────────────────────────────────

_EVIDENCE_REF_FIELDS = {"ratification_id", "description", "path", "sha256"}
_SIP_SOURCE_RULE_FIELDS = {"rule_id", "primary_source", "metric", "fallback"}
_POLICY_FIELDS = {
    "schema_version", "policy_id", "approval_status", "ratified_by",
    "ratified_at", "min_avg_traded_value_usd", "last_close_usd_min",
    "exclude_conditions", "insufficient_sessions_status",
    "missing_or_stale_data_status", "sip_source_rule", "evidence_refs",
    "packet_sha256",
}


def _validate_evidence_ref(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != _EVIDENCE_REF_FIELDS:
        _fail("EVIDENCE_REF_FIELDS_INVALID")
    _token(value["ratification_id"], "EVIDENCE_REF_ID_INVALID")
    if not isinstance(value["description"], str) or not value["description"].strip():
        _fail("EVIDENCE_REF_DESCRIPTION_INVALID")
    if not isinstance(value["path"], str) or _RELATIVE_PATH_RE.fullmatch(value["path"]) is None:
        _fail("EVIDENCE_REF_PATH_INVALID")
    _sha(value["sha256"], "EVIDENCE_REF_SHA_INVALID")
    return copy.deepcopy(value)


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
    _decimal(value["min_avg_traded_value_usd"], "POLICY_VOLUME_THRESHOLD_INVALID")
    _decimal(value["last_close_usd_min"], "POLICY_PRICE_FLOOR_INVALID")
    if (
        not isinstance(value["exclude_conditions"], list)
        or value["exclude_conditions"] != ["OTC_OR_NON_EXCHANGE_LISTED"]
    ):
        _fail("POLICY_EXCLUDE_CONDITIONS_INVALID")
    if value["insufficient_sessions_status"] != "NOT_EVALUATED":
        _fail("POLICY_INSUFFICIENT_SESSIONS_STATUS_INVALID")
    if value["missing_or_stale_data_status"] != "UNKNOWN":
        _fail("POLICY_MISSING_OR_STALE_DATA_STATUS_INVALID")
    rule = value["sip_source_rule"]
    if not isinstance(rule, dict) or set(rule) != _SIP_SOURCE_RULE_FIELDS or rule["rule_id"] != RULE_ID:
        _fail("POLICY_SIP_SOURCE_RULE_INVALID")
    refs = value["evidence_refs"]
    if not isinstance(refs, list) or len(refs) != 2:
        _fail("POLICY_EVIDENCE_REFS_COUNT_INVALID")
    validated_refs = [_validate_evidence_ref(ref) for ref in refs]
    if len({ref["path"] for ref in validated_refs}) != 2:
        _fail("POLICY_EVIDENCE_REFS_DUPLICATE_PATH")
    digest = _sha(value["packet_sha256"], "POLICY_SHA_INVALID")
    body = copy.deepcopy(value)
    body.pop("packet_sha256")
    if payload_sha256(body) != digest:
        _fail("POLICY_SHA_MISMATCH")
    result = copy.deepcopy(value)
    result["evidence_refs"] = validated_refs
    return result


def verify_evidence_refs(policy: dict, root: Path = ROOT) -> list[str]:
    """Return problem codes (empty if every cited evidence file matches).

    Never raises -- this is the fail-closed check itself, not a schema
    assertion. A problem here means "trust the ratified number no more
    today", not "the code is broken".
    """
    problems: list[str] = []
    for ref in policy["evidence_refs"]:
        evidence_path = root / ref["path"]
        if not evidence_path.exists():
            problems.append(f"EVIDENCE_FILE_MISSING:{ref['path']}")
            continue
        try:
            actual_sha = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        except OSError:
            problems.append(f"EVIDENCE_FILE_UNREADABLE:{ref['path']}")
            continue
        if actual_sha != ref["sha256"]:
            problems.append(f"EVIDENCE_HASH_MISMATCH:{ref['path']}")
    return problems


def describe_policy(path: Path = POLICY_PATH, root: Path = ROOT) -> dict:
    """Non-raising diagnostic: why is (or isn't) the policy usable?

    ``status`` is one of ``RATIFIED`` (usable), ``ABSENT_FROM_REPO``,
    ``MALFORMED``, or ``EVIDENCE_HASH_MISMATCH``.
    """
    path = Path(path)
    if not path.exists():
        return {"status": "ABSENT_FROM_REPO", "problems": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        policy = _validate_policy(raw)
    except (OSError, json.JSONDecodeError, UsLiquiditySipSourceError) as exc:
        return {"status": "MALFORMED", "problems": [str(exc)]}
    problems = verify_evidence_refs(policy, root)
    if problems:
        return {"status": "EVIDENCE_HASH_MISMATCH", "problems": problems}
    return {"status": "RATIFIED", "problems": []}


def load_policy(path: Path = POLICY_PATH, *, root: Path = ROOT) -> Optional[dict]:
    """Return the ratified threshold packet, or ``None`` if it isn't usable.

    ``None`` covers three fail-closed cases, all deliberately collapsed to
    the same "cannot bind a number today" outcome for evaluation purposes
    (use ``describe_policy`` for which one it was):
      * the file does not exist (the expected state until a ratified
        packet is committed -- NOT an error);
      * the file exists but is structurally malformed (a real bug: this
        DOES raise, since presence implies it should already be valid);
      * the file is well-formed but a cited evidence file is missing or
        its sha256 no longer matches (evidence drift/tamper -- fails
        closed to ``None`` rather than raising, since this is an
        environment/evidence-integrity fact, not a code defect).
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("POLICY_UNREADABLE", str(exc))
    policy = _validate_policy(raw)
    problems = verify_evidence_refs(policy, root)
    if problems:
        print(
            f"WARN: {RULE_ID} policy evidence verification failed, "
            f"falling back to UNKNOWN: {problems}",
            file=sys.stderr,
        )
        return None
    return policy


# ─────────────────────────────────────────────────────────────────────────
# Per-feed observation (already aggregated by the caller; no raw bars here)
# ─────────────────────────────────────────────────────────────────────────

_OBSERVATION_FIELDS = {
    "feed", "avg_traded_value_usd", "last_close_usd", "session_count",
    "window_end", "notional_formula", "source_ref",
}


def _validate_observation(value: object, expected_feed: str) -> dict:
    if not isinstance(value, dict) or set(value) != _OBSERVATION_FIELDS:
        _fail(f"{expected_feed.upper()}_OBSERVATION_FIELDS_INVALID")
    if value["feed"] != expected_feed:
        _fail(f"{expected_feed.upper()}_OBSERVATION_FEED_MISMATCH")
    _decimal(value["avg_traded_value_usd"], f"{expected_feed.upper()}_AVG_TRADED_VALUE_INVALID")
    _decimal(value["last_close_usd"], f"{expected_feed.upper()}_LAST_CLOSE_INVALID")
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

def _combine(*statuses: str) -> str:
    if "FAIL" in statuses:
        return "FAIL"
    if "UNKNOWN" in statuses:
        return "UNKNOWN"
    return "PASS"


def evaluate_symbol_liquidity(
    symbol: str,
    sip: Optional[dict],
    iex: Optional[dict],
    policy: Optional[dict],
    exchange_listing_status: Optional[str] = None,
) -> dict:
    """Apply the ratified US T2 C3 liquidity condition to one symbol.

    ``sip``/``iex`` are ``None`` or a dict matching ``_OBSERVATION_FIELDS``
    -- an already-aggregated 20-session observation (never a per-day bar).
    ``exchange_listing_status`` is ``None`` (the listing lookup itself
    could not resolve this symbol -- see module docstring),
    ``"EXCHANGE_LISTED"``, ``"OTC"``, or ``"TEST_ISSUE"``.

    Step 1 -- feed selection, independent of whether a threshold policy
    exists, so the derived average/last-close/session-count/feed are
    always reported when the data allows it:
      1. SIP present with a full session window -> use SIP.
      2. Otherwise, IEX present with a full session window -> use IEX.
      3. Otherwise -> ``NOT_EVALUATED`` ("no T2"; nothing else is checked).

    Step 2 -- once a feed is selected, three independent sub-checks, each
    PASS/FAIL/UNKNOWN, combined by ``FAIL`` beats ``UNKNOWN`` beats
    ``PASS`` (fail closed):
      * ``volume_status`` -- SIP: PASS/FAIL against the threshold (SIP is
        the complete tape, so a confirmed shortfall is a confident
        negative). IEX (fallback): PASS/UNKNOWN, never FAIL.
      * ``price_status`` -- the selected feed's own last close vs the
        ratified price floor; PASS/FAIL (this is real bar data, never
        missing when a feed was selected).
      * ``otc_exclusion_status`` -- PASS if ``exchange_listing_status ==
        "EXCHANGE_LISTED"``, FAIL if ``"OTC"`` or ``"TEST_ISSUE"``
        (distinct reasons), UNKNOWN if ``None`` (input not available --
        honest, not assumed).
      * any of the three is UNKNOWN if the policy itself could not be
        bound (see ``load_policy``/``describe_policy``).
    """
    symbol = _token(symbol, "SYMBOL_INVALID")
    if exchange_listing_status is not None and exchange_listing_status not in EXCHANGE_LISTING_STATUSES:
        _fail("EXCHANGE_LISTING_STATUS_INVALID", str(exchange_listing_status))

    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "rule_id": RULE_ID,
        "symbol": symbol,
        "status": "NOT_EVALUATED",
        "source_feed_used": None,
        "avg_traded_value_usd": None,
        "last_close_usd": None,
        "session_count": None,
        "window_end": None,
        "threshold_usd": None,
        "last_close_usd_min": None,
        "min_session_window": REQUIRED_SESSION_WINDOW,
        "policy_id": None,
        "volume_status": None,
        "price_status": None,
        "otc_exclusion_status": None,
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
        # Base record: "fewer than 20 -> NOT_EVALUATED (no T2)". Nothing
        # else is evaluated -- there is no observation to check it against.
        result["status"] = "NOT_EVALUATED"
        result["reasons"] = reasons or ["NO_FEED_DATA_AVAILABLE"]
        return result

    result.update(
        source_feed_used=chosen_feed,
        avg_traded_value_usd=chosen_obs["avg_traded_value_usd"],
        last_close_usd=chosen_obs["last_close_usd"],
        session_count=chosen_obs["session_count"],
        window_end=chosen_obs["window_end"],
    )

    if policy is None:
        # Base record fail_closed clause: missing/stale data -> UNKNOWN.
        # A missing ratified threshold is exactly that kind of missing
        # input for every remaining sub-check.
        reasons.append("LIQUIDITY_THRESHOLD_POLICY_UNAVAILABLE")
        result.update(
            status="UNKNOWN",
            volume_status="UNKNOWN",
            price_status="UNKNOWN",
            otc_exclusion_status="UNKNOWN",
            reasons=reasons,
        )
        return result

    policy = _validate_policy(policy)
    threshold = Decimal(policy["min_avg_traded_value_usd"])
    price_floor = Decimal(policy["last_close_usd_min"])
    result["threshold_usd"] = policy["min_avg_traded_value_usd"]
    result["last_close_usd_min"] = policy["last_close_usd_min"]
    result["policy_id"] = policy["policy_id"]

    avg = Decimal(chosen_obs["avg_traded_value_usd"])
    if chosen_feed == "sip":
        volume_status = "PASS" if avg >= threshold else "FAIL"
        if volume_status != "PASS":
            reasons.append("SIP_BELOW_THRESHOLD")
    else:
        volume_status = "PASS" if avg >= threshold else "UNKNOWN"
        if volume_status != "PASS":
            reasons.append("IEX_FALLBACK_BELOW_THRESHOLD")

    last_close = Decimal(chosen_obs["last_close_usd"])
    price_status = "PASS" if last_close >= price_floor else "FAIL"
    if price_status != "PASS":
        reasons.append("LAST_CLOSE_BELOW_PRICE_FLOOR")

    if exchange_listing_status is None:
        otc_exclusion_status = "UNKNOWN"
        reasons.append("EXCHANGE_LISTING_STATUS_UNAVAILABLE")
    elif exchange_listing_status == "OTC":
        otc_exclusion_status = "FAIL"
        reasons.append("OTC_EXCLUDED")
    elif exchange_listing_status == "TEST_ISSUE":
        # A confirmed test issue (Nasdaq's own "Test Issue" = Y flag) is a
        # distinct, evidence-backed exclusion from OTC -- reported with its
        # own reason so a reviewer never mistakes one for the other, even
        # though both currently fold into the same otc_exclusion_status.
        otc_exclusion_status = "FAIL"
        reasons.append("TEST_ISSUE_EXCLUDED")
    else:  # "EXCHANGE_LISTED"
        otc_exclusion_status = "PASS"

    result.update(
        status=_combine(volume_status, price_status, otc_exclusion_status),
        volume_status=volume_status,
        price_status=price_status,
        otc_exclusion_status=otc_exclusion_status,
        reasons=reasons,
    )
    return result
