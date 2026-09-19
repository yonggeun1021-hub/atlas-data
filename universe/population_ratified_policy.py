#!/usr/bin/env python3
"""Ratified population-level policy rules (KR / US), read-only evaluators.

User ratification (2026-09-16 KST, verbatim ``"이걸로 일단 확정하자"``), verbatim
scope quote from the committed record:

    "전 종목 발굴 규칙 6건 (한국·미국). CANDIDATE_PASS_RULE, STAGE_TRANSITION_RULE은
    제외 -- 근거 확보 후 별도 확정."

Evidence copied byte-for-byte into this repository:
``evidence/authority/population_policy_6_rules_user_ratification_20260916.json``
(the ratification record) and
``evidence/authority/population_policy_6_rules_ratification_card_20260916.md``
(the card it points to). Both are sha256-checked against
``config/population_ratified_policy_contract.json`` on every ``load_contract``
call; a byte drift fails closed.

Scope -- the six rules this module implements, and only these:
``INVESTABLE_UNIVERSE``, ``LIQUIDITY``, ``LISTING_DELISTING``, ``TAXONOMY``,
``TRADABILITY`` (KR + US), and ``SOURCE_HIERARCHY`` (US only). It never
touches ``CANDIDATE_PASS_RULE`` or ``STAGE_TRANSITION_RULE`` -- those stay
``미정`` and this module grants no candidate, ranking, or stage-promotion
authority (see ``authority`` in the contract: every key is ``False``).

Fail-closed contract for every rule (2026-09-18 CIO instruction): a symbol
whose required input is not wired into this pipeline resolves to ``UNKNOWN``,
never a silent PASS or a silent exclusion. Today that is true for:

* KR ``LIQUIDITY`` unless the private 20-session price-history store is
  configured (``ATLAS_PRICE_HISTORY_ROOT``) -- absent by default in this
  public repository;
* US ``LIQUIDITY`` for any symbol without >=20 IEX daily bars already
  captured by ``collectors/free_market_data.py`` (22 symbols today);
* KR ``TAXONOMY`` -- no KRX 46-industry table is wired into this pipeline;
* KR and US ``TRADABILITY`` -- no KIS master (관리종목/투자경고/투자위험/
  단기과열) or US halt/deficiency feed is wired into this pipeline;
* KR and US ``LISTING_DELISTING`` -- no listing-date or delisting-flag
  source is wired into this pipeline;
* US ``SOURCE_HIERARCHY`` for a symbol with no recorded feed at all.

US ``TAXONOMY`` and (indirectly) US ``SOURCE_HIERARCHY`` reuse two already
ratified, already implemented, read-only fact providers rather than
reinventing them: ``universe/us_spdr_sector_mapping.sector_for_symbol`` (its
own point-in-time / fail-closed rules are untouched, only read) and the
``feed`` field ``collectors/free_market_data.py`` already records per
capture. KR ``INVESTABLE_UNIVERSE`` and US ``INVESTABLE_UNIVERSE`` are new,
mechanical, name/flag-pattern classifiers over fields this pipeline already
carries (KRX display name; Nasdaq Trader directory ETF / Test Issue /
Security Name) -- documented per-pattern below, including the one
deliberately-ambiguous case (a KR name ending in a bare "우") this module
refuses to guess on.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "population_ratified_policy_contract.json"
CONTRACT_VERSION = "population_ratified_policy/1"

RULE_STATUSES = ("MET", "UNMET", "UNKNOWN")
OVERALL_STATUSES = (
    "RATIFIED_POPULATION_PASS",
    "RATIFIED_POPULATION_EXCLUDED",
    "RATIFIED_POPULATION_UNKNOWN",
)
KR_RULES = ("INVESTABLE_UNIVERSE", "LIQUIDITY", "LISTING_DELISTING", "TAXONOMY", "TRADABILITY")
US_RULES = ("INVESTABLE_UNIVERSE", "LIQUIDITY", "LISTING_DELISTING", "TAXONOMY", "TRADABILITY", "SOURCE_HIERARCHY")


class PopulationRatifiedPolicyError(ValueError):
    """Fail-closed ratified-policy violation."""


def _fail(code: str, detail: str | None = None) -> None:
    raise PopulationRatifiedPolicyError(code if detail is None else f"{code}:{detail}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("CONTRACT_READ_FAILED", str(exc))
    if not isinstance(contract, dict) or contract.get("contract_version") != CONTRACT_VERSION:
        _fail("CONTRACT_VERSION_INVALID")
    authority = contract.get("authority")
    if not isinstance(authority, dict) or not authority or any(v is not False for v in authority.values()):
        _fail("CONTRACT_AUTHORITY_INVALID")
    for evidence in contract.get("ratification_evidence") or []:
        full = ROOT / evidence["path"]
        if not full.is_file() or _sha256_file(full) != evidence["sha256"]:
            _fail("RATIFICATION_EVIDENCE_DRIFT", evidence["path"])
    if contract.get("excludes") != ["CANDIDATE_PASS_RULE", "STAGE_TRANSITION_RULE"]:
        _fail("CONTRACT_SCOPE_INVALID")
    return copy.deepcopy(contract)


def _verdict(status: str, reason: str, **extra) -> dict:
    if status not in RULE_STATUSES:
        _fail("RULE_STATUS_INVALID", status)
    return {"status": status, "reason": reason, **extra}


def MET(reason: str, **extra) -> dict:
    return _verdict("MET", reason, **extra)


def UNMET(reason: str, **extra) -> dict:
    return _verdict("UNMET", reason, **extra)


def UNKNOWN(reason: str, **extra) -> dict:
    return _verdict("UNKNOWN", reason, **extra)


def overall(rule_results: dict) -> dict:
    statuses = {v["status"] for v in rule_results.values()}
    if "UNMET" in statuses:
        status = "RATIFIED_POPULATION_EXCLUDED"
    elif statuses == {"MET"}:
        status = "RATIFIED_POPULATION_PASS"
    else:
        status = "RATIFIED_POPULATION_UNKNOWN"
    return {"status": status, "rules": rule_results}


# --------------------------------------------------------------------------
# KR -- RULE.INVESTABLE_UNIVERSE
# --------------------------------------------------------------------------
# KRX naming conventions used here (mechanical, over ``display_name`` only --
# this pipeline carries no explicit KRX security-type field):
#   * "신주인수권" anywhere in the name -> rights certificate/warrant.
#   * "스팩" anywhere in the name -> SPAC (기업인수목적회사).
#   * name ends with "리츠" -> REIT.
#   * name ends with a digit+우[letter] or 우+letter (e.g. "1우", "2우B",
#     "우B") -> multi-class preferred stock; this exact suffix shape has no
#     known common-stock counter-example on KRX.
#   * name ends with a BARE "우" (no digit before, no letter after) is
#     genuinely ambiguous: most such names are single-class preferred stock
#     (e.g. "삼성전자우"), but at least one real KOSPI common stock's own
#     legal name ends in the syllable "우" ("미래에셋대우" -- "대우" is part
#     of the company name, not a preferred marker). This module resolves the
#     ambiguity only when the population itself also carries the base name
#     (name minus the trailing "우") as another symbol's display name --
#     that is the actual preferred/common pairing pattern on KRX. Otherwise
#     it reports UNKNOWN rather than guessing either way.
_KR_RIGHTS_RE = re.compile(r"신주인수권")
_KR_SPAC_RE = re.compile(r"스팩")
_KR_REIT_RE = re.compile(r"리츠$")
_KR_PREFERRED_STRONG_RE = re.compile(r"(?:\d우[A-Z]?|우[A-Z])$")
_KR_PREFERRED_BARE_RE = re.compile(r"우$")


def kr_investable_universe(display_name: str | None, population_display_names: frozenset) -> dict:
    name = (display_name or "").strip()
    if not name:
        return UNKNOWN("DISPLAY_NAME_MISSING")
    if _KR_RIGHTS_RE.search(name):
        return UNMET("RIGHTS_NAME_KEYWORD:신주인수권")
    if _KR_SPAC_RE.search(name):
        return UNMET("SPAC_NAME_KEYWORD:스팩")
    if _KR_REIT_RE.search(name):
        return UNMET("REIT_NAME_SUFFIX:리츠")
    if _KR_PREFERRED_STRONG_RE.search(name):
        return UNMET("PREFERRED_NAME_SUFFIX_MULTI_CLASS")
    if _KR_PREFERRED_BARE_RE.search(name):
        base = name[:-1]
        if base in population_display_names:
            return UNMET(f"PREFERRED_NAME_SUFFIX_BASE_MATCH:{base}")
        return UNKNOWN("PREFERRED_NAME_SUFFIX_AMBIGUOUS_NO_BASE_MATCH")
    return MET("NO_EXCLUSION_NAME_PATTERN_MATCHED")


# --------------------------------------------------------------------------
# US -- RULE.INVESTABLE_UNIVERSE
# --------------------------------------------------------------------------
# ETF flag and Test Issue flag come straight from the Nasdaq Trader Symbol
# Directory rows this pipeline already carries per symbol
# (``decision/us_population_symbol_observation.py::_directory_facts``).
# Security Name classification reuses the same pure, no-authority heuristic
# ``universe/us_investable_universe_v1.classify_security_name`` already uses
# for its own (separately, T1-display-only) purpose -- read-only import,
# nothing about that module's own scope changes.
def _classify_us_security_name():
    module_path = ROOT / "universe" / "us_investable_universe_v1.py"
    import importlib.util

    spec = importlib.util.spec_from_file_location("population_us_investable_universe_v1", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_US_V1 = _classify_us_security_name()
US_EXCLUDED_SECURITY_TYPES = (_US_V1.SECURITY_TYPE_ADS, _US_V1.SECURITY_TYPE_ORDINARY, _US_V1.SECURITY_TYPE_EXCLUDED)


def us_investable_universe(etf_flags: list, test_issue_flags: list, security_name: str | None) -> dict:
    if not etf_flags or not test_issue_flags:
        return UNKNOWN("ETF_OR_TEST_ISSUE_FLAG_NOT_PRESENT_IN_SOURCE_ROW")
    if any(flag == "Y" for flag in etf_flags):
        return UNMET("ETF_FLAG_Y")
    if any(flag == "Y" for flag in test_issue_flags):
        return UNMET("TEST_ISSUE_FLAG_Y")
    name = (security_name or "").strip()
    if not name:
        return UNKNOWN("SECURITY_NAME_MISSING")
    security_type = _US_V1.classify_security_name(name)
    if security_type in US_EXCLUDED_SECURITY_TYPES:
        return UNMET(f"SECURITY_NAME_CLASS:{security_type}")
    return MET(f"SECURITY_NAME_CLASS:{security_type}")


# --------------------------------------------------------------------------
# LIQUIDITY -- ratified thresholds, per market (kept in the per-market
# contract as required: KR in KRW, US in USD -- see
# config/population_ratified_policy_contract.json ``rules.LIQUIDITY``).
# --------------------------------------------------------------------------
def kr_liquidity(avg_trading_value_20s_krw: float | None, sessions_available: int, latest_close_krw: float | None,
                 *, required_sessions: int, min_avg_trading_value_krw: float, min_close_krw: float) -> dict:
    if sessions_available < required_sessions or avg_trading_value_20s_krw is None or latest_close_krw is None:
        return UNKNOWN(f"PRICE_HISTORY_{required_sessions}_SESSION_WINDOW_NOT_AVAILABLE:sessions={sessions_available}")
    if avg_trading_value_20s_krw >= min_avg_trading_value_krw and latest_close_krw >= min_close_krw:
        return MET(f"AVG_TRADING_VALUE_{avg_trading_value_20s_krw:.0f}_CLOSE_{latest_close_krw:.0f}")
    return UNMET(f"AVG_TRADING_VALUE_{avg_trading_value_20s_krw:.0f}_CLOSE_{latest_close_krw:.0f}")


def us_liquidity(bars: list, *, required_sessions: int, min_avg_dollar_volume_usd: float, min_close_usd: float) -> dict:
    if len(bars) < required_sessions:
        return UNKNOWN(f"LESS_THAN_{required_sessions}_SESSIONS_AVAILABLE:sessions={len(bars)}")
    window = bars[-required_sessions:]
    try:
        dollar_volumes = [float(bar["close"]) * float(bar["volume"]) for bar in window]
        latest_close = float(window[-1]["close"])
    except (KeyError, TypeError, ValueError):
        return UNKNOWN("BAR_CLOSE_OR_VOLUME_NOT_NUMERIC")
    avg_dollar_volume = sum(dollar_volumes) / required_sessions
    if avg_dollar_volume >= min_avg_dollar_volume_usd and latest_close >= min_close_usd:
        return MET(f"AVG_DOLLAR_VOLUME_{avg_dollar_volume:.2f}_CLOSE_{latest_close:.2f}")
    return UNMET(f"AVG_DOLLAR_VOLUME_{avg_dollar_volume:.2f}_CLOSE_{latest_close:.2f}")


# --------------------------------------------------------------------------
# LISTING_DELISTING -- fail-closed: no listing-date or delisting-flag source
# is wired into this pipeline for either market today.
# --------------------------------------------------------------------------
def kr_listing_delisting(listing_date: str | None, delisting_flag: object) -> dict:
    if listing_date is None and delisting_flag is None:
        return UNKNOWN("LISTING_DATE_AND_DELISTING_FLAG_SOURCE_NOT_WIRED")
    _fail("LISTING_DELISTING_INPUT_UNEXPECTED", "no caller wires this input yet")


def us_listing_delisting(listing_date: str | None, delisting_flag: object) -> dict:
    if listing_date is None and delisting_flag is None:
        return UNKNOWN("LISTING_DATE_AND_DELISTING_FLAG_SOURCE_NOT_WIRED")
    _fail("LISTING_DELISTING_INPUT_UNEXPECTED", "no caller wires this input yet")


# --------------------------------------------------------------------------
# TAXONOMY
# --------------------------------------------------------------------------
def kr_taxonomy(sector_46: str | None) -> dict:
    if sector_46 is None:
        return UNKNOWN("KRX_46_INDUSTRY_TABLE_NOT_WIRED")
    return MET(f"KRX_46_INDUSTRY:{sector_46}", sector=sector_46)


def us_taxonomy(spdr_result: dict) -> dict:
    status = spdr_result.get("status")
    if status == "OK":
        return MET(f"SPDR_SECTOR_ETF:{spdr_result.get('sector_etf')}", sector_etf=spdr_result.get("sector_etf"),
                    basis=spdr_result.get("basis"))
    if status in ("UNKNOWN_NO_T2", "NO_POINT_IN_TIME_CAPTURE_AVAILABLE"):
        return UNKNOWN(status)
    _fail("SPDR_RESULT_STATUS_UNEXPECTED", str(status))


# --------------------------------------------------------------------------
# TRADABILITY -- fail-closed: no KIS master (KR) or halt/deficiency feed
# (US) is wired into this pipeline today.
# --------------------------------------------------------------------------
def kr_tradability(kis_master_row: dict | None) -> dict:
    if kis_master_row is None:
        return UNKNOWN("KIS_MASTER_NOT_AVAILABLE")
    _fail("TRADABILITY_INPUT_UNEXPECTED", "no caller wires this input yet")


def us_tradability(halt_deficiency_row: dict | None) -> dict:
    if halt_deficiency_row is None:
        return UNKNOWN("HALT_DEFICIENCY_FEED_NOT_AVAILABLE")
    _fail("TRADABILITY_INPUT_UNEXPECTED", "no caller wires this input yet")


# --------------------------------------------------------------------------
# SOURCE_HIERARCHY (US only) -- SIP first, IEX second, IEX-only disclosed.
# --------------------------------------------------------------------------
def us_source_hierarchy(feed: str | None) -> dict:
    if feed == "sip":
        return MET("SIP_PRIMARY_SOURCE")
    if feed == "iex":
        return MET("IEX_FALLBACK_RECORDED", note="IEX 기준(실제 거래량의 일부)")
    return UNKNOWN("NO_FEED_RECORDED")


# --------------------------------------------------------------------------
# Per-market entry points
# --------------------------------------------------------------------------
def evaluate_kr(*, display_name: str | None, population_display_names: frozenset,
                 avg_trading_value_20s_krw: float | None, sessions_available: int, latest_close_krw: float | None,
                 sector_46: str | None, contract: dict) -> dict:
    thresholds = contract["rules"]["LIQUIDITY"]["KR"]
    rules = {
        "INVESTABLE_UNIVERSE": kr_investable_universe(display_name, population_display_names),
        "LIQUIDITY": kr_liquidity(
            avg_trading_value_20s_krw, sessions_available, latest_close_krw,
            required_sessions=thresholds["window_sessions"],
            min_avg_trading_value_krw=thresholds["min_avg_trading_value"],
            min_close_krw=thresholds["min_close"],
        ),
        "LISTING_DELISTING": kr_listing_delisting(None, None),
        "TAXONOMY": kr_taxonomy(sector_46),
        "TRADABILITY": kr_tradability(None),
    }
    return overall(rules)


def evaluate_us(*, etf_flags: list, test_issue_flags: list, security_name: str | None, bars: list,
                 spdr_result: dict, feed: str | None, contract: dict) -> dict:
    thresholds = contract["rules"]["LIQUIDITY"]["US"]
    rules = {
        "INVESTABLE_UNIVERSE": us_investable_universe(etf_flags, test_issue_flags, security_name),
        "LIQUIDITY": us_liquidity(
            bars,
            required_sessions=thresholds["window_sessions"],
            min_avg_dollar_volume_usd=thresholds["min_avg_dollar_volume"],
            min_close_usd=thresholds["min_close"],
        ),
        "LISTING_DELISTING": us_listing_delisting(None, None),
        "TAXONOMY": us_taxonomy(spdr_result),
        "TRADABILITY": us_tradability(None),
        "SOURCE_HIERARCHY": us_source_hierarchy(feed),
    }
    return overall(rules)
