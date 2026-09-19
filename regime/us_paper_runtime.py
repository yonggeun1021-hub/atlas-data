#!/usr/bin/env python3
"""US PAPER runtime decision (U4): fail-closed, adoption-gated.

This mirrors the KR runtime bridge (PR #696) and CRYPTO_PAPER_RUNTIME_V1
(#720/#723) for US.  It is the pure calculation; the publication module
collects committed evidence and writes nothing here.

* Axis arithmetic is ``regime.paper_regime_reference.build_us`` unmodified,
  over the committed ``free_market_data_capture/5`` packet.  Its thresholds are
  ``PAPER_RUNTIME_NORMALIZATION_V1`` markets.US (file ratified by commit
  ``ba82906a``), proven equal to the reference policy block on every call.
* TREND/BREADTH/LEADERSHIP freshness is the ratified SESSION_EXACT_MATCH rule,
  evaluated by ``regime.regime_semantic_freshness.evaluate_axis_freshness``.
* RISK_VOL (VIXCLS) and LIQUIDITY (WRESBAL/TOTBKCR) follow FRED release
  semantics: the capture's own fetch, never coerced to a session date, with
  ALFRED vintage lookahead and observation lookahead rejected.
* Those two forms are reported FRESH for any DEFINED factor, because the
  ratified rule reads a DEFINED release-cycle factor as this run's own fetch.
  A committed capture is not this run's fetch, so the decision additionally
  states the collection coverage of the capture it read -- measured by the
  publication module in the collector's own cadence dates, never in elapsed
  wall-clock days -- and blocks on ``US_FREE_MARKET_DATA_COLLECTION_BEHIND_SOURCE``
  past its bound, or on ``..._COLLECTION_COVERAGE_UNMEASURED`` if the
  measurement is absent or contradicts itself.
* Classification and hysteresis come only from the unmodified
  ``regime.decision_authority.replay_common_v1`` over the accepted historical
  sequence followed by one step per official session.
* A runtime regime is emitted only when an active ``US_PAPER_RUNTIME_ADOPTION_V1``
  identity binds a US ``PIT_ACCEPTED`` acceptance record (re-evaluated on every
  call) and an official session calendar.  Anything absent, invalid, unbound,
  missing, stale or lookahead is UNKNOWN with explicit reasons; nothing is
  carried forward.  A decision expires at the next official session close.
  Both of those artifacts are bound from inside the adoption identity, so an
  absent or invalid adoption reports their two UNBOUND reasons as well -- and
  says in ``adoption.derived_reasons`` that it derived them, because neither can
  be cleared while the adoption is the thing that is missing.

No provider is called, no file is written, and no strategy, stage, buy,
action, capital, order, production, trading or REAL authority is opened.
"""

from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from regime import decision_authority as COMMON
from regime import market_scoped_pit_acceptance as PIT
from regime import paper_regime_reference as REFERENCE
from regime import regime_semantic_freshness as SEMANTIC


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_RELATIVE = "config/us_paper_runtime_contract_v1.json"
CONTRACT_SHA256 = "bad715c8a960b6aa7a5618b22e5ccefc580f38599e2c3f532f7e182c700812d3"
ADOPTION_RELATIVE = "config/us_paper_runtime_adoption_v1.json"
TEMPLATE_RELATIVE = "config/us_paper_runtime_adoption_v1.TEMPLATE.json"
PIT_STATUS_RELATIVE = "data/latest_market_scoped_pit_acceptance.json"

SCHEMA_VERSION = "us_paper_runtime_decision/1"
ADOPTION_CONTRACT_VERSION = "us_paper_runtime_adoption/v1"
ADOPTION_IDENTITY = "US_PAPER_RUNTIME_ADOPTION_V1"
ADOPTION_ACTIVE_STATUS = "CIO_TECHNICAL_ADOPTED"
ADOPTION_TEMPLATE_STATUS = "TEMPLATE_NOT_ACTIVE"
# Both bindings the runtime needs live INSIDE the adoption identity
# (``pit_acceptance`` and ``session_calendar``), so when the adoption itself is
# absent or invalid these two reasons are consequences of that one failure, not
# two further observations: nothing else in the repository could satisfy either
# of them on its own.  They are still reported -- suppressing them would hide a
# closed gate -- but ``adoption.derived_reasons`` says they were derived, so a
# reader can tell this case apart from the one where a *valid* adoption simply
# omits a binding (``test_calendar_gates``), which is an independent
# observation and leaves ``derived_reasons`` empty.  Without that distinction
# the packet reads as three separable wiring gaps and invites a hunt for a
# second place to bind an artifact that has none.
ADOPTION_DERIVED_REASONS = ("US_PIT_ACCEPTED_RECORD_UNBOUND", "US_OFFICIAL_SESSION_CALENDAR_UNBOUND")
ACCEPTANCE_RECORD_SCHEMA = "us_pit_acceptance_record/1"
CALENDAR_SCHEMA = "us_official_session_calendar/1"
SOURCE_SCHEMA = "free_market_data_capture/5"

# Ratified identities, verbatim.  The contract file must carry exactly these.
NORMALIZATION_RELATIVE = "config/paper_runtime_normalization_v1.json"
NORMALIZATION_SHA256 = "cb6945225cd4f972825aaa3a6fb300ab440bd9b0d247bd0edb216e8513335385"
NORMALIZATION_RATIFYING_COMMIT = "ba82906a1e438247070cba96a08c9b9469fcfce5"
NORMALIZATION_US_BLOCK_SHA256 = "766ab14531682c133d900299dbb66a71a7c83f0cec73e7e140455c8b57e39419"
FRESHNESS_RELATIVE = "config/regime_semantic_freshness_policy_v1.json"
FRESHNESS_SHA256 = "0194dd4647b9a42b5183977c822eb8125320d32e93ef4e7f4efda364db6ff193"
PIT_CONTRACT_RELATIVE = "config/market_scoped_pit_acceptance_contract_v1.json"
PIT_CONTRACT_SHA256 = "c13006da3da69df4a94ab494942045c988bd6e7d6c51c0fe57c12c889ab45712"
REFERENCE_POLICY_RELATIVE = "config/paper_regime_reference_policy_v1.json"
REFERENCE_POLICY_SHA256 = "6ed1a18670834ff8ed021a4076fc2976792884b6d66f51473c1d3ae0aea57350"

IMPLEMENTATION_PATHS = (
    "regime/us_paper_runtime.py",
    "regime/us_paper_runtime_publication.py",
    "regime/decision_authority.py",
    "regime/paper_regime_reference.py",
    "regime/regime_semantic_freshness.py",
    "regime/market_scoped_pit_acceptance.py",
    "regime/us_historical_replay_population.py",
    "config/us_historical_pit_replay_identity_v1.json",
    "collectors/free_market_data.py",
    "collectors/fred_vix_provenance.py",
)

LIVE_NATURAL = "LIVE_NATURAL"
EVIDENCE_CLASSES = {LIVE_NATURAL, "SYNTHETIC_OFFLINE_FIXTURE"}

# Collection coverage of the capture the decision actually read.  The publication
# module measures it in the collector's own cadence dates (coverage, not elapsed
# wall-clock) and this module refuses to treat an unmeasured or self-contradicting
# block as current, so degrading to an older capture can never be silent.
SOURCE_CURRENT = "SOURCE_CURRENT"
COLLECTION_BEHIND_SOURCE = "COLLECTION_BEHIND_SOURCE"
COLLECTION_COVERAGE_UNMEASURED = "COLLECTION_COVERAGE_UNMEASURED"
COLLECTION_COVERAGE_STATUSES = (SOURCE_CURRENT, COLLECTION_BEHIND_SOURCE)
COLLECTION_COVERAGE_FIELDS = (
    "measure", "cadence_cron", "cadence_declared_in", "selected_capture_cadence_date",
    "evaluated_cadence_date", "uncovered_cadence_dates", "uncovered_cadence_date_count",
    "tolerated_uncovered_cadence_dates", "status",
)
AXES = ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"]
SESSION_AXES = ("TREND", "BREADTH", "LEADERSHIP")
RUNTIME_REGIMES = ["RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS", "UNKNOWN"]
LIQUIDITY_SERIES = ("TOTBKCR", "WRESBAL")
NEW_YORK = ZoneInfo("America/New_York")
CLOSE_TIMES = {"16:00": dt.time(16, 0), "13:00": dt.time(13, 0)}
STILL_CURRENT_VINTAGE = "9999-12-31"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REASON = re.compile(r"^[A-Z][A-Z0-9_]*$")

AUTHORITY_CLOSED = {
    "paper_runtime_display_authorized": False,
    "strategy_authorized": False,
    "stage_authorized": False,
    "buy_authorized": False,
    "action_authorized": False,
    "capital_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "real_authorized": False,
}
CAVEATS = [
    "US_BREADTH_LEADERSHIP_REPRESENTATIVE_ETF_PROXY_NOT_FULL_SECURITY_UNIVERSE",
    "ALPACA_IEX_ONLY_PARTIAL_US_MARKET_UNADJUSTED_CLOSES",
    "FRED_LIQUIDITY_RAW_NOT_RETAINED_HASH_ATTESTED",
    "CONFIDENCE_MATCHING_AXIS_FRACTION_NOT_PROBABILITY",
    "DECISION_EXPIRES_AT_NEXT_OFFICIAL_SESSION_CLOSE",
]


class UsPaperRuntimeError(ValueError):
    pass


def require(condition: bool, code: str) -> None:
    if not condition:
        raise UsPaperRuntimeError(code)


def sha256(raw: bytes) -> str:
    require(isinstance(raw, bytes), "BYTES_REQUIRED")
    return hashlib.sha256(raw).hexdigest()


def canonical_bytes(value: object) -> bytes:
    return COMMON.canonical_bytes(value)


def payload_sha256(value: object) -> str:
    return COMMON.payload_sha256(value)


def pretty_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def json_object(raw: bytes, code: str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: require(False, "NONFINITE_JSON"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsPaperRuntimeError(code) from exc
    require(isinstance(value, dict), code)
    return value


def instant(value: object, code: str) -> dt.datetime:
    require(isinstance(value, str) and UTC_SECOND.fullmatch(value) is not None, code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise UsPaperRuntimeError(code) from exc


def day(value: object, code: str) -> dt.date:
    require(isinstance(value, str) and ISO_DATE.fullmatch(value) is not None, code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise UsPaperRuntimeError(code) from exc
    require(parsed.isoformat() == value, code)
    return parsed


def utc_text(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def decimal(value: object, code: str) -> Decimal:
    require(isinstance(value, str) and bool(value.strip()), code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise UsPaperRuntimeError(code) from exc
    require(parsed.is_finite(), code)
    return parsed


def reason_code(exc: Exception, fallback: str) -> str:
    text = str(exc).split(":", 1)[0].strip()
    return text if REASON.fullmatch(text or "") else fallback


def _bound_file(root: Path, relative: object, code: str) -> bytes:
    require(isinstance(relative, str) and relative and not relative.startswith("/")
            and ".." not in Path(relative).parts, code + "_PATH_INVALID")
    path = Path(root) / relative
    require(path.is_file(), code + "_ABSENT")
    return path.read_bytes()


# ---------------------------------------------------------------------------
# Ratified identities (always checked, independent of adoption)
# ---------------------------------------------------------------------------

def load_contract() -> dict:
    raw = (ROOT / CONTRACT_RELATIVE).read_bytes()
    require(sha256(raw) == CONTRACT_SHA256, "US_RUNTIME_CONTRACT_HASH_MISMATCH")
    contract = json_object(raw, "US_RUNTIME_CONTRACT_INVALID")
    bindings = contract["ratified_bindings"]
    require(
        bindings["normalization"]["path"] == NORMALIZATION_RELATIVE
        and bindings["normalization"]["sha256"] == NORMALIZATION_SHA256
        and bindings["normalization"]["ratifying_commit"] == NORMALIZATION_RATIFYING_COMMIT
        and bindings["normalization"]["us_block_payload_sha256"] == NORMALIZATION_US_BLOCK_SHA256
        and bindings["semantic_freshness"]["sha256"] == FRESHNESS_SHA256
        and bindings["pit_acceptance_contract"]["sha256"] == PIT_CONTRACT_SHA256
        and bindings["reference_policy"]["sha256"] == REFERENCE_POLICY_SHA256
        and contract["adoption_identity"]["implementation_paths"] == list(IMPLEMENTATION_PATHS),
        "US_RUNTIME_CONTRACT_BINDING_INVALID",
    )
    return contract


def verify_ratified_bindings() -> dict:
    """Prove the already-ratified US identities this runtime consumes."""
    contract = load_contract()
    normalization_raw = (ROOT / NORMALIZATION_RELATIVE).read_bytes()
    require(sha256(normalization_raw) == NORMALIZATION_SHA256, "NORMALIZATION_IDENTITY_DRIFT")
    normalization = json_object(normalization_raw, "NORMALIZATION_IDENTITY_INVALID")
    require(normalization.get("policy_status") == "RATIFIED"
            and "US" in normalization.get("ratified_markets", [])
            and normalization.get("missing_axis_or_invalid_axis_rule")
            == "MISSING_INVALID_OR_STALE_REQUIRED_AXIS_IS_IMMEDIATE_UNKNOWN",
            "NORMALIZATION_US_NOT_RATIFIED")
    us_block = normalization["markets"]["US"]
    require(payload_sha256(us_block) == NORMALIZATION_US_BLOCK_SHA256, "NORMALIZATION_US_BLOCK_DRIFT")
    require(normalization["required_axes"] == AXES, "NORMALIZATION_AXES_INVALID")
    reference_raw = (ROOT / REFERENCE_POLICY_RELATIVE).read_bytes()
    require(sha256(reference_raw) == REFERENCE_POLICY_SHA256
            and normalization["source_candidate_binding"]["sha256"] == REFERENCE_POLICY_SHA256,
            "REFERENCE_POLICY_DRIFT")
    reference_policy = json_object(reference_raw, "REFERENCE_POLICY_INVALID")
    require(reference_policy["markets"]["US"] == us_block, "NORMALIZATION_NOT_VERBATIM_REFERENCE_US_BLOCK")
    freshness_raw = (ROOT / FRESHNESS_RELATIVE).read_bytes()
    require(sha256(freshness_raw) == FRESHNESS_SHA256, "FRESHNESS_POLICY_DRIFT")
    freshness_policy = SEMANTIC.load_policy(ROOT / FRESHNESS_RELATIVE)
    us_freshness = freshness_policy["markets"]["US"]
    require(sorted(us_freshness["session_based_axes"]) == sorted(SESSION_AXES)
            and all(row["freshness_form"] == SEMANTIC.SESSION_EXACT_MATCH
                    for row in us_freshness["session_based_axes"].values())
            and sorted(us_freshness["release_based_axes"]) == ["LIQUIDITY", "RISK_VOL"]
            and us_freshness["release_based_axes"]["RISK_VOL"]["series_id"] == "VIXCLS"
            and us_freshness["release_based_axes"]["LIQUIDITY"]["series_id"] == ["WRESBAL", "TOTBKCR"],
            "FRESHNESS_US_RULE_INVALID")
    require(sha256((ROOT / PIT_CONTRACT_RELATIVE).read_bytes()) == PIT_CONTRACT_SHA256,
            "PIT_ACCEPTANCE_CONTRACT_DRIFT")
    common_policy = COMMON.load_common_v1_policy()
    require(common_policy["required_axes"] == AXES, "COMMON_V1_AXES_INVALID")
    common_binding = payload_sha256(common_policy["binding"])
    require(common_binding == contract["ratified_bindings"]["common_v1"]["binding_payload_sha256"],
            "COMMON_V1_BINDING_DRIFT")
    return {
        "contract_sha256": CONTRACT_SHA256,
        "normalization_sha256": NORMALIZATION_SHA256,
        "normalization_ratifying_commit": NORMALIZATION_RATIFYING_COMMIT,
        "normalization_us_block_payload_sha256": NORMALIZATION_US_BLOCK_SHA256,
        "semantic_freshness_sha256": FRESHNESS_SHA256,
        "pit_acceptance_contract_sha256": PIT_CONTRACT_SHA256,
        "reference_policy_sha256": REFERENCE_POLICY_SHA256,
        "common_v1_binding_payload_sha256": common_binding,
        "reference_policy": reference_policy,
        "freshness_policy": freshness_policy,
        "common_policy": common_policy,
    }


# The historical-replay identity file is introduced by the U1 wiring change
# (PR #739), which may land before or after this producer.  Its *absence* is a
# real, semantically meaningful state (the population module then replays the
# narrow 3-axis scope), so it is bound explicitly as ABSENT rather than
# skipped: an adoption bound while it was absent stops matching the moment the
# file appears, and vice versa.  Every other implementation path must exist.
IMPLEMENTATION_OPTIONAL_PATHS = frozenset({"config/us_historical_pit_replay_identity_v1.json"})
IMPLEMENTATION_PATH_ABSENT = "ABSENT"


def implementation_sha256() -> dict:
    bound = {}
    for path in IMPLEMENTATION_PATHS:
        try:
            bound[path] = sha256((ROOT / path).read_bytes())
        except FileNotFoundError:
            if path not in IMPLEMENTATION_OPTIONAL_PATHS:
                raise
            bound[path] = IMPLEMENTATION_PATH_ABSENT
    return bound


# ---------------------------------------------------------------------------
# Adoption identity (U5) and its bound artifacts
# ---------------------------------------------------------------------------

def load_adoption(root: Path, now: dt.datetime, ratified: dict) -> tuple[dict, str]:
    path = Path(root) / ADOPTION_RELATIVE
    require(path.is_file(), "US_PAPER_RUNTIME_ADOPTION_IDENTITY_ABSENT")
    raw = path.read_bytes()
    adoption = json_object(raw, "ADOPTION_IDENTITY_JSON_INVALID")
    require(adoption.get("contract_version") == ADOPTION_CONTRACT_VERSION
            and adoption.get("identity") == ADOPTION_IDENTITY, "ADOPTION_IDENTITY_INVALID")
    require(adoption.get("status") != ADOPTION_TEMPLATE_STATUS, "ADOPTION_IDENTITY_IS_TEMPLATE_NOT_ACTIVE")
    require(adoption.get("status") == ADOPTION_ACTIVE_STATUS, "ADOPTION_IDENTITY_NOT_ACTIVE")
    require(adoption.get("market") == "US" and adoption.get("evidence_class") == LIVE_NATURAL
            and adoption.get("runtime_authorized_regimes") == RUNTIME_REGIMES,
            "ADOPTION_SCOPE_INVALID")
    require(instant(adoption.get("effective_at_utc"), "ADOPTION_EFFECTIVE_AT_INVALID") <= now,
            "ADOPTION_NOT_YET_EFFECTIVE")
    authority = adoption.get("authority")
    require(isinstance(authority, dict) and set(authority) == set(AUTHORITY_CLOSED)
            and authority.get("paper_runtime_display_authorized") is True
            and all(v is False for k, v in authority.items() if k != "paper_runtime_display_authorized"),
            "ADOPTION_AUTHORITY_ESCALATION")
    bindings = adoption.get("bindings")
    require(isinstance(bindings, dict), "ADOPTION_BINDINGS_MISSING")
    expected = {
        "contract_sha256": CONTRACT_SHA256,
        "normalization": {
            "path": NORMALIZATION_RELATIVE, "sha256": NORMALIZATION_SHA256,
            "ratifying_commit": NORMALIZATION_RATIFYING_COMMIT,
            "us_block_payload_sha256": NORMALIZATION_US_BLOCK_SHA256,
        },
        "semantic_freshness": {"path": FRESHNESS_RELATIVE, "sha256": FRESHNESS_SHA256},
        "pit_acceptance_contract": {"path": PIT_CONTRACT_RELATIVE, "sha256": PIT_CONTRACT_SHA256},
        "reference_policy": {"path": REFERENCE_POLICY_RELATIVE, "sha256": REFERENCE_POLICY_SHA256},
        "common_v1_binding_payload_sha256": ratified["common_v1_binding_payload_sha256"],
        "implementation_sha256": implementation_sha256(),
    }
    for key, value in expected.items():
        require(bindings.get(key) == value, "ADOPTION_BINDING_MISMATCH_" + key.upper())
    return adoption, sha256(raw)


def load_calendar(root: Path, adoption: dict) -> dict:
    binding = adoption.get("session_calendar")
    require(isinstance(binding, dict), "US_OFFICIAL_SESSION_CALENDAR_UNBOUND")
    raw = _bound_file(root, binding.get("path"), "US_OFFICIAL_SESSION_CALENDAR")
    require(isinstance(binding.get("sha256"), str) and sha256(raw) == binding["sha256"],
            "US_OFFICIAL_SESSION_CALENDAR_HASH_MISMATCH")
    calendar = json_object(raw, "US_OFFICIAL_SESSION_CALENDAR_INVALID")
    require(calendar.get("schema_version") == CALENDAR_SCHEMA and calendar.get("market") == "US"
            and calendar.get("timezone") == "America/New_York"
            and isinstance(calendar.get("source"), dict), "US_OFFICIAL_SESSION_CALENDAR_INVALID")
    start = day(calendar.get("coverage_start"), "US_OFFICIAL_SESSION_CALENDAR_INVALID")
    end = day(calendar.get("coverage_end"), "US_OFFICIAL_SESSION_CALENDAR_INVALID")
    rows = calendar.get("sessions")
    require(isinstance(rows, list) and bool(rows) and start <= end, "US_OFFICIAL_SESSION_CALENDAR_INVALID")
    sessions, previous = [], None
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"date", "close_time_et"}, "US_OFFICIAL_SESSION_ROW_INVALID")
        session = day(row["date"], "US_OFFICIAL_SESSION_ROW_INVALID")
        require(start <= session <= end and session.weekday() < 5
                and (previous is None or session > previous)
                and row["close_time_et"] in CLOSE_TIMES, "US_OFFICIAL_SESSION_ROW_INVALID")
        close = dt.datetime.combine(session, CLOSE_TIMES[row["close_time_et"]], tzinfo=NEW_YORK)
        sessions.append({"date": session, "close_at": close.astimezone(dt.timezone.utc)})
        previous = session
    return {"sessions": sessions, "coverage_start": start, "coverage_end": end, "sha256": binding["sha256"]}


def session_plan(calendar: dict, now: dt.datetime, history_last: dt.date) -> dict:
    """Context session, expiry and the live chain, all from the bound calendar."""
    sessions = calendar["sessions"]
    completed = [row for row in sessions if row["close_at"] <= now]
    require(bool(completed), "EXPECTED_COMPLETED_SESSION_CALENDAR_UNKNOWN")
    context = completed[-1]
    later = [row for row in sessions if row["date"] > context["date"]]
    require(bool(later), "EXPECTED_COMPLETED_SESSION_CALENDAR_UNKNOWN")
    execution = later[0]
    require(now < execution["close_at"], "EXPECTED_COMPLETED_SESSION_CALENDAR_UNKNOWN")
    dates = [row["date"] for row in sessions]
    require(history_last in dates, "HISTORY_LAST_SESSION_NOT_IN_OFFICIAL_CALENDAR")
    require(context["date"] > history_last, "HISTORY_OVERLAPS_CURRENT_SESSION")
    live = []
    for index, row in enumerate(sessions):
        if history_last < row["date"] <= context["date"]:
            live.append({"date": row["date"], "close_at": row["close_at"],
                         "expires_at": sessions[index + 1]["close_at"]})
    return {"context": context, "execution": execution, "live_sessions": live}


def population_module():
    """The real US population owner; its validator is the only bundle validator."""
    from regime import us_historical_replay_population as POPULATION

    return POPULATION


def load_acceptance(root: Path, adoption: dict) -> dict:
    """Bound US PIT_ACCEPTED record, re-evaluated from the bound bundle bytes."""
    binding = adoption.get("pit_acceptance")
    require(isinstance(binding, dict), "US_PIT_ACCEPTED_RECORD_UNBOUND")
    for key in ("bundle_sha256", "acceptance_record_sha256", "replay_report_sha256"):
        require(isinstance(binding.get(key), str) and SHA256.fullmatch(binding[key]) is not None,
                "US_PIT_ACCEPTED_RECORD_UNBOUND")
    bundle_raw = _bound_file(root, binding.get("bundle_path"), "US_PIT_POPULATION_BUNDLE")
    require(sha256(bundle_raw) == binding["bundle_sha256"], "US_PIT_POPULATION_BUNDLE_HASH_MISMATCH")
    record_raw = _bound_file(root, binding.get("acceptance_record_path"), "US_PIT_ACCEPTANCE_RECORD")
    require(sha256(record_raw) == binding["acceptance_record_sha256"], "US_PIT_ACCEPTANCE_RECORD_HASH_MISMATCH")
    record = json_object(record_raw, "US_PIT_ACCEPTANCE_RECORD_INVALID")
    require(record.get("schema_version") == ACCEPTANCE_RECORD_SCHEMA and record.get("market") == "US",
            "US_PIT_ACCEPTANCE_RECORD_INVALID")
    require(record.get("bundle_sha256") == binding["bundle_sha256"], "US_PIT_ACCEPTANCE_RECORD_BUNDLE_UNBOUND")
    bundle = json_object(bundle_raw, "US_PIT_POPULATION_BUNDLE_INVALID")
    try:
        POPULATION = population_module()
        POPULATION.validate_population(copy.deepcopy(bundle))
    except Exception as exc:  # the owner validator fails closed with its own codes
        raise UsPaperRuntimeError("US_PIT_POPULATION_BUNDLE_REVALIDATION_FAILED") from exc
    evaluation = PIT.evaluate_market_pit_acceptance("US", bundle)
    require(canonical_bytes(record.get("evaluation")) == canonical_bytes(evaluation),
            "US_PIT_ACCEPTANCE_RECORD_REDERIVATION_MISMATCH")
    require(evaluation["status"] == PIT.STATUS_PIT_ACCEPTED, "US_PIT_NOT_ACCEPTED")
    require(evaluation["replay_report_sha256"] == binding["replay_report_sha256"],
            "US_PIT_ACCEPTANCE_REPLAY_BINDING_MISMATCH")
    sequence = PIT._build_sequence("US", bundle["records"])
    require(sequence is not None, "US_PIT_NOT_ACCEPTED")
    require(PIT.payload_sha256(COMMON.replay_common_v1(copy.deepcopy(sequence))) == evaluation["replay_report_sha256"],
            "US_PIT_ACCEPTANCE_REPLAY_BINDING_MISMATCH")
    history_last = day(binding.get("history_last_session_date"), "US_PIT_HISTORY_LAST_SESSION_INVALID")
    require(sequence["steps"][-1]["as_of_date"] == history_last.isoformat(),
            "US_PIT_HISTORY_LAST_SESSION_MISMATCH")
    return {
        "history_steps": sequence["steps"],
        "history_last": history_last,
        "summary": {
            "status": evaluation["status"],
            "bundle_sha256": binding["bundle_sha256"],
            "acceptance_record_sha256": binding["acceptance_record_sha256"],
            "replay_report_sha256": evaluation["replay_report_sha256"],
            "evaluated_date_count": evaluation["evaluated_date_count"],
            "regimes_observed": evaluation["regimes_observed"],
            "history_first_session_date": sequence["steps"][0]["as_of_date"],
            "history_last_session_date": history_last.isoformat(),
        },
    }


def published_pit_status(root: Path) -> dict | None:
    """Display-only copy of the published market-scoped status; never trusted."""
    try:
        status = json_object((Path(root) / PIT_STATUS_RELATIVE).read_bytes(), "PIT_STATUS_INVALID")
        row = [m for m in status["markets"] if m.get("market") == "US"][0]
        return {"status": row["status"], "reasons": list(row["reasons"]),
                "trusted_as_acceptance": False}
    except (OSError, UsPaperRuntimeError, KeyError, IndexError, TypeError):
        return None


# ---------------------------------------------------------------------------
# One session step
# ---------------------------------------------------------------------------

def _freshness(axis: str, observation_date: str, session_date: dt.date | None, policy: dict) -> None:
    result = SEMANTIC.evaluate_axis_freshness(
        "US", axis, {"status": "DEFINED", "observation_date": observation_date},
        expected_completed_session_date=None if session_date is None else session_date.isoformat(),
        policy=policy,
    )
    require(result["freshness_status"] == SEMANTIC.FRESH, f"{axis}_{result['reason']}")


def _session_axis(reference: dict, axis: str, session_date: dt.date | None, policy: dict) -> dict:
    as_of = reference.get("as_of_session_date")
    day(as_of, f"{axis}_OBSERVATION_DATE_INVALID")
    if axis == "TREND":
        rows = reference.get("trend_etfs")
        require(isinstance(rows, list) and bool(rows), "TREND_INPUT_INVALID")
        dates = {row.get("as_of_session_date") for row in rows}
    else:
        proxy = reference.get("proxy_axes", {}).get(axis)
        require(isinstance(proxy, dict) and proxy.get("status") == "OBSERVED", f"{axis}_PROXY_NOT_OBSERVED")
        measurement = proxy["measurement"]
        rows = measurement["observations" if axis == "BREADTH" else "ordered_groups"]
        require(isinstance(rows, list) and bool(rows), f"{axis}_INPUT_INVALID")
        dates = {measurement.get("as_of_session_date")} | {row.get("as_of_session_date") for row in rows}
    require(dates == {as_of}, f"{axis}_MIXED_SESSION_GENERATION")
    _freshness(axis, as_of, session_date, policy)
    return {"observation_session_date": as_of, "freshness_form": SEMANTIC.SESSION_EXACT_MATCH}


def _vintage(start: object, end: object, observation: object, captured: dt.date,
             session_date: dt.date | None, prefix: str) -> dict:
    realtime_start = day(start, prefix + "_VINTAGE_INVALID")
    require(realtime_start <= captured, prefix + "_VINTAGE_LOOKAHEAD")
    if end != STILL_CURRENT_VINTAGE:
        require(day(end, prefix + "_VINTAGE_INVALID") >= realtime_start, prefix + "_VINTAGE_INVALID")
    observed = day(observation, prefix + "_OBSERVATION_DATE_INVALID")
    require(observed <= realtime_start, prefix + "_VINTAGE_LOOKAHEAD")
    if session_date is not None:
        require(observed <= session_date, prefix + "_OBSERVATION_LOOKAHEAD")
    return {"observation_date": observation, "realtime_start": start, "realtime_end": end}


def _risk_vol(record: dict, observed_at: dt.datetime, session_date: dt.date | None, policy: dict) -> dict:
    fred = record["reference_input"].get("fred")
    require(isinstance(fred, dict) and fred.get("status") == "READY" and fred.get("series_id") == "VIXCLS",
            "RISK_VOL_SOURCE_NOT_READY")
    evidence = record.get("vix_evidence")
    require(isinstance(evidence, dict) and isinstance(evidence.get("observation"), dict),
            "RISK_VOL_RAW_EVIDENCE_MISSING")
    captured = instant(evidence.get("captured_at_utc"), "RISK_VOL_CAPTURE_TIME_INVALID")
    require(captured == observed_at, "RISK_VOL_FETCH_NOT_SAME_CAPTURE")
    observation = evidence["observation"]
    require(all(observation.get(key) == fred.get(key)
                for key in ("series_id", "observation_date", "value", "realtime_start", "realtime_end")),
            "RISK_VOL_RAW_REDERIVATION_MISMATCH")
    require(fred.get("response_sha256") == evidence.get("raw_response_sha256"), "RISK_VOL_RAW_REDERIVATION_MISMATCH")
    row = _vintage(fred["realtime_start"], fred["realtime_end"], fred["observation_date"],
                   captured.date(), session_date, "RISK_VOL")
    _freshness("RISK_VOL", fred["observation_date"], session_date, policy)
    return {**row, "series_id": "VIXCLS", "value": fred["value"],
            "raw_response_sha256": fred["response_sha256"], "freshness_form": SEMANTIC.RELEASE_CYCLE_LATEST_FETCH}


def _liquidity(record: dict, observed_at: dt.datetime, session_date: dt.date | None, policy: dict) -> dict:
    liquidity = record["reference_input"].get("fred_liquidity")
    require(isinstance(liquidity, dict) and liquidity.get("status") == "READY"
            and liquidity.get("derivation_version") == "fred_liquidity_current/v1",
            "LIQUIDITY_SOURCE_NOT_READY")
    require(instant(liquidity.get("captured_at_utc"), "LIQUIDITY_CAPTURE_TIME_INVALID") == observed_at,
            "LIQUIDITY_FETCH_NOT_SAME_CAPTURE")
    series = liquidity.get("series")
    require(isinstance(series, list) and sorted(row.get("series_id") for row in series) == list(LIQUIDITY_SERIES),
            "LIQUIDITY_SERIES_INVALID")
    require(liquidity.get("derived_payload_sha256") == sha256(canonical_bytes(series)),
            "LIQUIDITY_DERIVED_HASH_MISMATCH")
    hashes = liquidity.get("response_hashes")
    rows = {}
    for row in series:
        sid = row["series_id"]
        pair = {"metadata_response_sha256": row.get("metadata_response_sha256"),
                "observations_response_sha256": row.get("observations_response_sha256")}
        require(isinstance(hashes, dict) and hashes.get(sid) == pair
                and all(isinstance(v, str) and SHA256.fullmatch(v) for v in pair.values()),
                "LIQUIDITY_RESPONSE_HASH_MISMATCH")
        require(isinstance(row.get("frequency"), str) and row["frequency"].startswith("Weekly"),
                "LIQUIDITY_FREQUENCY_INVALID")
        vintage = _vintage(row.get("realtime_start"), row.get("realtime_end"), row.get("observation_date"),
                           observed_at.date(), session_date, "LIQUIDITY")
        previous = day(row.get("previous_observation_date"), "LIQUIDITY_OBSERVATION_DATE_INVALID")
        require(previous < day(row["observation_date"], "LIQUIDITY_OBSERVATION_DATE_INVALID"),
                "LIQUIDITY_OBSERVATION_ORDER_INVALID")
        require(decimal(row.get("value"), "LIQUIDITY_VALUE_INVALID")
                - decimal(row.get("previous_value"), "LIQUIDITY_VALUE_INVALID")
                == decimal(row.get("change"), "LIQUIDITY_VALUE_INVALID"), "LIQUIDITY_CHANGE_INVALID")
        _freshness("LIQUIDITY", row["observation_date"], session_date, policy)
        rows[sid] = {**vintage, "previous_observation_date": row["previous_observation_date"],
                     "change": row["change"]}
    return {"series": rows, "derived_payload_sha256": liquidity["derived_payload_sha256"],
            "freshness_form": SEMANTIC.RELEASE_CYCLE_LATEST_FETCH}


def collection_coverage(record: object) -> dict:
    """The selected capture's collection coverage, or a fail-closed UNMEASURED block.

    A producer that degrades to an older capture must record in its own output
    which capture it used and how far behind the collector's cadence that is.
    An absent, malformed or self-contradicting block (one claiming
    ``SOURCE_CURRENT`` while its own count exceeds its own bound) is never read
    as current: it becomes ``COLLECTION_COVERAGE_UNMEASURED`` and blocks exactly
    like being behind, so the measurement cannot be dropped silently.
    """
    coverage = record.get("collection_coverage") if isinstance(record, dict) else None
    if not isinstance(coverage, dict) or set(coverage) != set(COLLECTION_COVERAGE_FIELDS):
        return {"status": COLLECTION_COVERAGE_UNMEASURED}
    dates, count = coverage["uncovered_cadence_dates"], coverage["uncovered_cadence_date_count"]
    bound = coverage["tolerated_uncovered_cadence_dates"]
    if (coverage["status"] not in COLLECTION_COVERAGE_STATUSES
            or not isinstance(dates, list) or not all(isinstance(value, str) for value in dates)
            or not isinstance(count, int) or isinstance(count, bool) or count != len(dates)
            or not isinstance(bound, int) or isinstance(bound, bool) or bound < 0
            or (coverage["status"] == SOURCE_CURRENT and count > bound)
            or (coverage["status"] == COLLECTION_BEHIND_SOURCE and count <= bound
                and coverage["selected_capture_cadence_date"] is not None
                and coverage["evaluated_cadence_date"] is not None)):
        return {"status": COLLECTION_COVERAGE_UNMEASURED}
    return copy.deepcopy(coverage)


def evaluate_source(record: object, session: dict | None, now: dt.datetime, ratified: dict) -> dict:
    """Signed axes from one committed capture; any failure is that axis UNDEFINED."""
    axes = {axis: {"status": "UNDEFINED", "direction": None} for axis in AXES}
    observations = {axis: {} for axis in AXES}
    reasons: list[str] = []
    session_date = None if session is None else session["date"]
    source = None
    try:
        require(isinstance(record, dict), "SESSION_SOURCE_MISSING")
        if "error" in record:
            code = record["error"] if isinstance(record["error"], str) and REASON.fullmatch(record["error"]) \
                else "INVALID"
            raise UsPaperRuntimeError(code if code.startswith("SESSION_SOURCE_")
                                      or code == "SOURCE_NOT_ADVANCED_EXPECTED_SESSION"
                                      else "SESSION_SOURCE_" + code)
        observed_at = instant(record.get("observed_at_utc"), "SOURCE_OBSERVED_AT_INVALID")
        source = {"revision_path": record.get("revision_path"), "packet_sha256": record.get("packet_sha256"),
                  "observed_at_utc": record["observed_at_utc"]}
        require(record.get("schema_version") == SOURCE_SCHEMA, "SESSION_SOURCE_SCHEMA_UNSUPPORTED")
        require(record.get("raw_rederivation") == "ALPACA_DAILY_AND_FRED_VIX_RAW_REDERIVED",
                "SESSION_SOURCE_RAW_NOT_REDERIVED")
        require(observed_at <= now, "SESSION_SOURCE_LOOKAHEAD")
        if session is not None:
            require(record.get("session_date") == session_date.isoformat(), "SESSION_SOURCE_DATE_MISMATCH")
            require(observed_at >= session["close_at"], "SESSION_SOURCE_CAPTURED_BEFORE_SESSION_CLOSE")
            require(observed_at < session["expires_at"], "SESSION_SOURCE_OUTSIDE_SESSION_WINDOW")
        reference_input = record.get("reference_input")
        require(isinstance(reference_input, dict), "SESSION_SOURCE_INPUT_INVALID")
        try:
            built = REFERENCE.build_us(copy.deepcopy(reference_input), ratified["reference_policy"])
        except REFERENCE.PaperRegimeReferenceError as exc:
            raise UsPaperRuntimeError("US_REFERENCE_DERIVATION_FAILED") from exc
        directions = {row["axis"]: row for row in built["axes"]}
        require(list(directions) == AXES, "US_REFERENCE_DERIVATION_FAILED")
    except (UsPaperRuntimeError, KeyError, TypeError, AttributeError) as exc:
        code = reason_code(exc, "SESSION_SOURCE_INPUT_SHAPE_INVALID") if isinstance(exc, UsPaperRuntimeError) \
            else "SESSION_SOURCE_INPUT_SHAPE_INVALID"
        for axis in AXES:
            observations[axis] = {"missing_reason": code}
        return {"axes": axes, "axis_observations": observations, "complete": False,
                "reasons": [code], "source": source}

    reference = reference_input["us_market_reference"]
    derive = {
        "TREND": lambda: _session_axis(reference, "TREND", session_date, ratified["freshness_policy"]),
        "BREADTH": lambda: _session_axis(reference, "BREADTH", session_date, ratified["freshness_policy"]),
        "LEADERSHIP": lambda: _session_axis(reference, "LEADERSHIP", session_date, ratified["freshness_policy"]),
        "RISK_VOL": lambda: _risk_vol(record, observed_at, session_date, ratified["freshness_policy"]),
        "LIQUIDITY": lambda: _liquidity(record, observed_at, session_date, ratified["freshness_policy"]),
    }
    for axis in AXES:
        try:
            observed = derive[axis]()
            axes[axis] = {"status": "DEFINED", "direction": directions[axis]["direction"]}
            observations[axis] = {**observed, "observed_direction": directions[axis]["direction"],
                                  "observed_value": directions[axis]["observed_value"]}
        except (UsPaperRuntimeError, SEMANTIC.RegimeSemanticFreshnessError, KeyError, TypeError, AttributeError) as exc:
            code = reason_code(exc, f"{axis}_INPUT_SHAPE_INVALID") \
                if isinstance(exc, (UsPaperRuntimeError, SEMANTIC.RegimeSemanticFreshnessError)) \
                else f"{axis}_INPUT_SHAPE_INVALID"
            observations[axis] = {"missing_reason": code, "observed_direction": directions[axis]["direction"]}
            reasons.append(code)
    return {"axes": axes, "axis_observations": observations,
            "complete": all(axes[a]["status"] == "DEFINED" for a in AXES),
            "reasons": sorted(set(reasons)), "source": source}


# ---------------------------------------------------------------------------
# Runtime decision
# ---------------------------------------------------------------------------

def publication_key(packet: dict) -> str:
    context = (packet.get("session") or {}).get("context_session_date")
    return context if context else "calendar-unknown-" + str(packet.get("evaluation_at", ""))[:10]


def basis_sha256(packet: dict) -> str:
    unsigned = {k: v for k, v in packet.items()
                if k not in {"evaluation_at", "code_revision", "decision_id", "basis_sha256"}}
    return payload_sha256(unsigned)


def evaluate_us_paper_runtime(*, evaluation_at: str, code_revision: str, session_records: dict,
                              latest_source_record: object, evidence_class: str,
                              root: Path = ROOT) -> dict:
    """Pure calculation.  Any failure yields runtime UNKNOWN, never a carried state."""
    packet = {
        "schema_version": SCHEMA_VERSION, "market": "US", "scope": "US_INTERNAL_VIRTUAL_PAPER_ONLY",
        "evaluation_at": evaluation_at, "code_revision": code_revision, "evidence_class": evidence_class,
        "decision_status": "BLOCKED", "paper_regime": "UNKNOWN", "runtime_regime": "UNKNOWN",
        "direction": "UNKNOWN", "confidence": None, "runtime_decision_available": False,
        "session": {"context_session_date": None, "context_session_close_at": None,
                    "execution_session_date": None, "expires_at": None},
        "bindings": None,
        "adoption": {"identity": ADOPTION_IDENTITY, "path": ADOPTION_RELATIVE, "status": "ABSENT",
                     "sha256": None, "blocking_reason": None, "derived_reasons": []},
        "pit_acceptance": {"status": "UNBOUND", "published_market_scoped_status": published_pit_status(root)},
        "current_observation": None, "latest_source_diagnostic": None, "chain": [], "aggregation": None,
        "reasons": [], "caveats": list(CAVEATS), "authority": dict(AUTHORITY_CLOSED),
    }
    reasons: list[str] = []
    try:
        now = instant(evaluation_at, "EVALUATION_TIME_INVALID")
        require(isinstance(code_revision, str) and re.fullmatch(r"[0-9a-f]{40}", code_revision) is not None,
                "CODE_REVISION_INVALID")
        require(evidence_class in EVIDENCE_CLASSES, "EVIDENCE_CLASS_INVALID")
        require(isinstance(session_records, dict), "SESSION_RECORDS_INVALID")
        ratified = verify_ratified_bindings()
        packet["bindings"] = {k: v for k, v in ratified.items()
                              if k not in {"reference_policy", "freshness_policy", "common_policy"}}
        if evidence_class != LIVE_NATURAL:
            reasons.append("EVIDENCE_CLASS_NOT_LIVE_NATURAL")

        diagnostic = evaluate_source(copy.deepcopy(latest_source_record), None, now, ratified)
        coverage = collection_coverage(latest_source_record)
        packet["latest_source_diagnostic"] = {
            "use": "DIAGNOSTIC_ONLY_NOT_A_RUNTIME_STEP_NO_SESSION_CALENDAR_APPLIED",
            "source": diagnostic["source"], "axis_observations": diagnostic["axis_observations"],
            "reasons": diagnostic["reasons"], "collection_coverage": coverage,
        }
        # RISK_VOL and LIQUIDITY carry the RELEASE_CYCLE_LATEST_FETCH freshness
        # form, which regime_semantic_freshness reports FRESH for any DEFINED
        # factor because it assumes the capture is this run's own fetch.  Reading
        # a committed capture breaks that assumption, so the skew of the capture
        # itself is stated here and blocks past its bound.
        if coverage["status"] != SOURCE_CURRENT:
            reasons.append("US_FREE_MARKET_DATA_" + coverage["status"])

        adoption = None
        try:
            adoption, adoption_sha = load_adoption(root, now, ratified)
            packet["adoption"].update(status=ADOPTION_ACTIVE_STATUS, sha256=adoption_sha)
        except (UsPaperRuntimeError, OSError, KeyError, TypeError) as exc:
            code = reason_code(exc, "ADOPTION_IDENTITY_INVALID") if isinstance(exc, UsPaperRuntimeError) \
                else "ADOPTION_IDENTITY_INVALID"
            packet["adoption"]["status"] = "ABSENT" if code == "US_PAPER_RUNTIME_ADOPTION_IDENTITY_ABSENT" else "INVALID"
            packet["adoption"]["blocking_reason"] = code
            packet["adoption"]["derived_reasons"] = list(ADOPTION_DERIVED_REASONS)
            reasons.extend([code, *ADOPTION_DERIVED_REASONS])

        if adoption is not None:
            acceptance = calendar = None
            try:
                acceptance = load_acceptance(root, adoption)
                packet["pit_acceptance"].update(acceptance["summary"])
            except (UsPaperRuntimeError, PIT.MarketScopedPitAcceptanceError, COMMON.DecisionAuthorityError,
                    OSError, KeyError, TypeError, IndexError) as exc:
                code = reason_code(exc, "US_PIT_ACCEPTED_RECORD_INVALID") \
                    if isinstance(exc, UsPaperRuntimeError) else "US_PIT_ACCEPTED_RECORD_INVALID"
                packet["pit_acceptance"]["status"] = "NOT_ACCEPTED_OR_UNBOUND"
                reasons.append(code)
            try:
                calendar = load_calendar(root, adoption)
            except (UsPaperRuntimeError, OSError, KeyError, TypeError) as exc:
                reasons.append(reason_code(exc, "US_OFFICIAL_SESSION_CALENDAR_INVALID")
                               if isinstance(exc, UsPaperRuntimeError) else "US_OFFICIAL_SESSION_CALENDAR_INVALID")
            if acceptance is not None and calendar is not None:
                reasons.extend(_classify(packet, acceptance, calendar, session_records, now, ratified))

        if reasons:
            packet["reasons"] = list(dict.fromkeys(reasons))
            packet.update(decision_status="BLOCKED", paper_regime="UNKNOWN", runtime_regime="UNKNOWN",
                          direction="UNKNOWN", confidence=None, runtime_decision_available=False)
        else:
            aggregation = packet["aggregation"]
            packet.update(decision_status="PAPER_RUNTIME_CLASSIFIED", paper_regime=aggregation["final_regime"],
                          runtime_regime=aggregation["final_regime"], direction=aggregation["final_direction"],
                          confidence=aggregation["final_confidence"], runtime_decision_available=True)
            packet["authority"]["paper_runtime_display_authorized"] = True
    except (UsPaperRuntimeError, COMMON.DecisionAuthorityError, SEMANTIC.RegimeSemanticFreshnessError,
            OSError, KeyError, TypeError, ValueError) as exc:
        code = reason_code(exc, "INPUT_SHAPE_INVALID") if isinstance(
            exc, (UsPaperRuntimeError, COMMON.DecisionAuthorityError, SEMANTIC.RegimeSemanticFreshnessError)
        ) else "INPUT_SHAPE_INVALID"
        packet.update(decision_status="BLOCKED", paper_regime="UNKNOWN", runtime_regime="UNKNOWN",
                      direction="UNKNOWN", confidence=None, runtime_decision_available=False,
                      reasons=list(dict.fromkeys([*reasons, code])), authority=dict(AUTHORITY_CLOSED))
    require(packet["runtime_regime"] in RUNTIME_REGIMES, "RUNTIME_REGIME_UNAUTHORIZED")
    packet["basis_sha256"] = basis_sha256(packet)
    packet["decision_id"] = "us-paper-regime:" + payload_sha256(packet)
    return packet


def _classify(packet: dict, acceptance: dict, calendar: dict, session_records: dict,
              now: dt.datetime, ratified: dict) -> list[str]:
    plan = session_plan(calendar, now, acceptance["history_last"])
    packet["session"] = {
        "context_session_date": plan["context"]["date"].isoformat(),
        "context_session_close_at": utc_text(plan["context"]["close_at"]),
        "execution_session_date": plan["execution"]["date"].isoformat(),
        "expires_at": utc_text(plan["execution"]["close_at"]),
    }
    steps = []
    for session in plan["live_sessions"]:
        step = evaluate_source(copy.deepcopy(session_records.get(session["date"].isoformat())),
                               session, now, ratified)
        step["session_date"] = session["date"].isoformat()
        steps.append(step)
    packet["chain"] = [{"session_date": s["session_date"], "complete": s["complete"], "reasons": s["reasons"],
                        "source": s["source"],
                        "axis_directions": {a: s["axes"][a]["direction"] for a in AXES}} for s in steps]
    history = acceptance["history_steps"]
    sequence = {
        "schema_version": 1, "market": "US", "case_id": "us-paper-runtime",
        "steps": copy.deepcopy(history) + [
            {"packet_id": "us-live-" + s["session_date"], "as_of_date": s["session_date"],
             "axes": copy.deepcopy(s["axes"])} for s in steps],
    }
    report = COMMON.replay_common_v1(sequence)
    live_rows = report["steps"][len(history):]
    packet["aggregation"] = {
        "consumed_via": "regime.decision_authority.replay_common_v1",
        "report_payload_sha256": payload_sha256(report),
        "source_sequence_sha256": report["source_sequence_sha256"],
        "policy_binding": report["policy_binding"],
        "step_count": report["step_count"], "history_step_count": len(history),
        "live_steps": live_rows,
        "final_regime": report["final_regime"], "final_direction": report["final_direction"],
        "final_confidence": report["final_confidence"],
    }
    last, row = steps[-1], live_rows[-1]
    packet["current_observation"] = {
        "session_date": last["session_date"], "complete": last["complete"], "source": last["source"],
        "axis_observations": last["axis_observations"], "reasons": last["reasons"],
        "candidate_regime": row["raw_classification"], "score": row["score"],
        "confirmed_regime": row["confirmed_regime"], "hysteresis": row["hysteresis"],
    }
    reasons = []
    if not last["complete"]:
        reasons.append("CURRENT_SESSION_OBSERVATION_INCOMPLETE")
        reasons.extend(last["reasons"])
    elif report["final_regime"] == "UNKNOWN":
        reasons.append("COMMON_CONFIRMATION_PENDING")
    return reasons


def is_current(packet: dict, observed_at: str) -> bool:
    """A consumer may show the runtime regime only before the decision expires."""
    try:
        expires = instant((packet.get("session") or {}).get("expires_at"), "EXPIRES_AT_INVALID")
        return (packet.get("runtime_decision_available") is True
                and instant(observed_at, "OBSERVED_AT_INVALID") < expires)
    except UsPaperRuntimeError:
        return False


def validate_us_paper_runtime(packet: dict, **inputs) -> dict:
    expected = evaluate_us_paper_runtime(**inputs)
    require(canonical_bytes(packet) == canonical_bytes(expected), "RUNTIME_REDERIVATION_MISMATCH")
    return copy.deepcopy(expected)
