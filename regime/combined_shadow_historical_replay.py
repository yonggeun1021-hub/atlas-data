#!/usr/bin/env python3
"""P1-COM-05 combined KR+US SHADOW historical replay — population and report.

CIO mandate (2026-09-04) closing slice of the P1-COM-05 replay-population set:
join the KR five-axis replay population and the US free-axis replay population
over **one caller-supplied set of historical dates**, and report what the two
markets together can and cannot show on those dates.

This module invents nothing new:

* Every KR observation comes from ``regime/kr_historical_replay_population.py``
  and every US observation from ``regime/us_historical_replay_population.py``,
  both imported and called unmodified.  This file contains no KRX/Alpaca/FRED
  request, no axis derivation, and no threshold.
* Normalization is exactly what those populations already carry, which is
  ``regime/paper_regime_reference.py::build_kr`` / ``build_us`` applied by the
  market modules.  Nothing here re-normalizes, re-scores, or re-classifies.
* Each embedded market population is re-checked by that market's *own*
  validator before it is joined, so this report can never publish a
  sub-population its owning contract would reject.  ``validate_population``
  additionally re-derives every per-market view from the embedded record it
  claims to project, and requires each embedded population to replay exactly
  this population's requested dates — a market view is otherwise only ever
  compared with itself, and a coherent set of forged views, or a genuine
  market population for a *different* date, would pass every other check.
  The two blocks that describe the population rather than its records are
  checked too: the ``pit_replay`` declaration must be complete and must carry
  its declared values, so a re-signed payload cannot claim future dates were
  used, and every ``episode_input_sources`` record must be a well-formed pin
  with a syntactically valid SHA-256 and a count the published episodes
  support.

What this module deliberately refuses to do:

* **No episode selection.**  Dates come only from ``--date``,
  ``--episode NAME=D1,D2``, or a caller-supplied ``--episode-file``.  Nothing
  is scanned, inferred, or chosen here — no bull/bear/sideways/stress window is
  ever picked by this code.  An episode name is an opaque caller label: it is
  attached to a date *after* that date has been replayed, never used to select
  a date, and never fed into any market evaluation.  ``label_semantics``
  records that, and ``test/test_combined_shadow_historical_replay.py`` pins it
  by proving a labelled run and an unlabelled run produce identical records.
* **No cross-market regime.**  There is no ratified rule that combines a KR
  candidate regime with a US one, so ``cross_market_regime`` stays ``UNKNOWN``
  with ``classification_status =
  NOT_COMPUTABLE_NO_RATIFIED_CROSS_MARKET_RULE``.  The per-market candidate
  regimes are carried verbatim and never summed, averaged, ranked, or
  reconciled.  Inventing that rule is a separate CIO ratification.
* **No threshold tuning** and **no US BREADTH/LEADERSHIP backfill** — the US
  population keeps both axes ``UNKNOWN``, and this report's validator rejects
  any embedded US record that carries a value for them.

Historical replay evidence != NATURAL evidence: every record is tagged
``evidence_class = "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY"``, every
authority flag stays ``false`` except the one read-only "this is shadow
historical-replay evidence" marker, and output is refused anywhere inside this
repository checkout — external ``--out`` or a private system-temp file only.

Point-in-time integrity is structural and then re-checked at the join:

* Each market module already anchors every request to one requested date and
  only ever looks backward from it.
* This module additionally recomputes, from each market record's own
  attestation, whether any consumed source date is later than the requested
  date.  A market that fails that check is contained to ``BLOCKED`` for that
  one date with ``COMBINED_LOOKAHEAD_VIOLATION`` — the other market and every
  other date are unaffected, and no such date can contribute a candidate
  regime.
* A market whose whole population is unavailable (missing contract, unreadable
  candidate policy, or a sub-population its own validator rejects) degrades to
  ``NOT_COMPUTABLE`` for every date instead of aborting the report, so a
  single-market replay still yields honest evidence.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import kr_historical_replay_population as KRP  # noqa: E402
from regime import us_historical_replay_population as USP  # noqa: E402


SCHEMA_VERSION = "regime_combined_shadow_historical_replay/v1"
MODE = "SHADOW_HISTORICAL_REPLAY_NOT_NATURAL"
EVIDENCE_CLASS = "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY"
MARKETS = ("KR", "US")

SHA256 = re.compile(r"^[0-9a-f]{64}$")
DATE10 = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE8 = re.compile(r"^\d{8}$")
# Deliberately narrow: an episode label is carried into deterministic,
# hash-attested output, so it stays a short printable identifier.
EPISODE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.:\-]{0,63}$")

OUTCOME_OBSERVED = "OBSERVED"
OUTCOME_PARTIAL = "PARTIAL"
OUTCOME_BLOCKED = "BLOCKED"
OUTCOMES = (OUTCOME_OBSERVED, OUTCOME_PARTIAL, OUTCOME_BLOCKED)

# Native per-market record statuses -> the one shared vocabulary this join
# uses. Explicit and total on purpose: if either market module later adds a
# record status, the join fails closed on that date instead of silently
# bucketing an unknown status as observed. The KR side's two statuses are
# fixed by kr_historical_replay_population.validate_population; the US side's
# are read from that module's own constants.
OUTCOME_BY_MARKET_STATUS = {
    "KR": {"OBSERVED": OUTCOME_OBSERVED, "BLOCKED": OUTCOME_BLOCKED},
    "US": {
        USP.STATUS_OBSERVED: OUTCOME_OBSERVED,
        USP.STATUS_PARTIAL: OUTCOME_PARTIAL,
        USP.STATUS_BLOCKED: OUTCOME_BLOCKED,
    },
}
EFFECTIVE_DATE_FIELD = {"KR": "effective_trading_date", "US": "effective_session_date"}

COMBINED_BOTH = "BOTH_MARKETS_REPLAYED"
COMBINED_SINGLE = "SINGLE_MARKET_ONLY"
COMBINED_NONE = "NOT_COMPUTABLE_NO_MARKET_REPLAYED"
COMBINED_STATUSES = (COMBINED_BOTH, COMBINED_SINGLE, COMBINED_NONE)

CROSS_MARKET_REGIME = "UNKNOWN"
CROSS_MARKET_STATUS = "NOT_COMPUTABLE_NO_RATIFIED_CROSS_MARKET_RULE"
LABEL_SEMANTICS = "OPAQUE_CALLER_LABEL_NOT_A_REGIME_OR_OUTCOME_CLAIM"
DATE_SELECTION = "CALLER_SUPPLIED_ONLY"

LOOKAHEAD_CONTAINED = "COMBINED_LOOKAHEAD_VIOLATION"
MARKET_RECORD_MISSING = "MARKET_RECORD_MISSING_FROM_POPULATION"
# The one code under which a whole market population can be reported
# unavailable. Every such reason is ``CODE:MARKET:detail``, and
# ``_validate_embedded_populations`` requires that shape, so an unavailable
# market always keeps an attributable cause instead of degrading into a
# BLOCKED market with no reason at all.
MARKET_POPULATION_UNAVAILABLE = "MARKET_POPULATION_UNAVAILABLE"

# The exact authority boundary of this population, declared once and required
# key-for-key by ``validate_population``. A payload that drops a flag must not
# pass merely because the flag it dropped is no longer there to be checked.
AUTHORITY_GRANTED_KEY = "historical_replay_evidence_authorized"
AUTHORITY = {
    "historical_replay_evidence_authorized": True,
    "natural_promotion_authorized": False,
    "episode_selection_authorized": False,
    "cross_market_regime_authorized": False,
    "threshold_tuning_authorized": False,
    "us_breadth_authorized": False,
    "us_leadership_authorized": False,
    "sensor_normalization_ratification_authorized": False,
    "registry_promotion_authorized": False,
    "ttl_ratification_authorized": False,
    "pit_replay_acceptance_authorized": False,
    "runtime_regime_wiring_authorized": False,
    "strategy_authorized": False,
    "stage_authorized": False,
    "buy_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "capital_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "real_authorized": False,
}

# The exact point-in-time block every population publishes, declared once so
# ``build_population`` and ``validate_population`` cannot drift apart, and
# required key for key by ``_validate_pit_replay``.
#
# This block is the population's own declaration that no date's evaluation saw a
# later date. A re-hashed payload is a valid signature over whatever it
# contains, so a validator that never inspected it would accept a population
# that flipped ``future_dates_used_in_any_date_evaluation`` to ``true`` — a
# payload simultaneously claiming to honour and to breach the non-negotiable PIT
# boundary — or that simply deleted the declaration. Neither the key set, the
# values, nor the statement is optional: softening the statement while keeping
# the booleans would misdescribe the same payload just as effectively.
PIT_REPLAY_TRUE_KEYS = (
    "each_date_replayed_independently",
    "each_market_replayed_independently",
    "lookahead_rechecked_at_join",
)
PIT_REPLAY_FALSE_KEYS = (
    "future_dates_used_in_any_date_evaluation",
    "retained_sources_mutated_by_this_module",
    "candidate_rule_modified_by_this_module",
    "market_observations_recomputed_by_this_module",
    "cross_market_rule_invented_by_this_module",
)
PIT_REPLAY_STATEMENT = (
    "Every observation is the KR or US replay population's own output for"
    " one caller-supplied date, re-validated by that market's own"
    " validator before it is joined. This module adds no source request,"
    " no axis derivation, no threshold, and no cross-market rule. Each"
    " market record's consumed source dates are re-checked against its"
    " requested date at the join, and any market that consumed a later"
    " date is failed closed for that one date only."
)
PIT_REPLAY_KEYS = PIT_REPLAY_TRUE_KEYS + PIT_REPLAY_FALSE_KEYS + ("statement",)

# The exact shape of one caller-supplied episode-input source record, as
# ``load_episode_file`` emits it. This list is the population's whole statement
# of *where* its caller labels came from, so it is validated rather than carried
# unread: an unchecked field can be re-signed into a syntactically impossible
# digest, a count no set of episodes supports, or a bare string that pins
# nothing at all.
EPISODE_SOURCE_KEYS = ("path", "sha256", "episode_count")


class CombinedReplayError(ValueError):
    """A requested combined KR+US historical replay cannot be safely built."""


def fail(code: str, detail: str = "") -> None:
    raise CombinedReplayError(f"{code}:{detail}" if detail else code)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CombinedReplayError("CANONICAL_JSON_INVALID") from exc


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise CombinedReplayError(f"SOURCE_MISSING:{path}") from exc


def redact(text: str, secrets: list[str]) -> str:
    """Never let a credential reach a recorded reason.

    Reuses the US module's redaction verbatim so both slices strip the same
    way; this wrapper only guarantees a string result for KR-side reasons too.
    """
    return USP.redact(str(text), [secret for secret in secrets if secret])


# ---------------------------------------------------------------------------
# Caller-supplied request: dates and episode labels only.
# ---------------------------------------------------------------------------


def parse_episode_argument(text: str) -> dict:
    """Parse one ``--episode NAME=D1,D2`` argument into a labelled date list.

    The name is split on the *first* ``=`` only, so a label may not contain
    ``=`` but the date list is never ambiguous.
    """
    if not isinstance(text, str) or "=" not in text:
        fail("EPISODE_ARGUMENT_INVALID")
    name, _, dates = text.partition("=")
    return {"name": name.strip(), "dates": [part.strip() for part in dates.split(",") if part.strip()]}


def load_episode_file(path: Path) -> tuple[list[dict], dict]:
    """Read caller-supplied episodes from a JSON file the caller names.

    This is still caller selection, not automatic selection: the file, its
    episode names, and every date in it come from outside this module, and its
    sha256 is pinned into the report so a reader can attribute each episode to
    the exact input that declared it.
    """
    path = Path(path)
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CombinedReplayError(f"EPISODE_FILE_UNREADABLE:{path}") from exc
    rows = body.get("episodes") if isinstance(body, dict) else None
    if not isinstance(rows, list) or not rows:
        fail("EPISODE_FILE_INVALID", "episodes")
    episodes = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("dates"), list):
            fail("EPISODE_FILE_INVALID", "episode")
        episodes.append({
            "name": row.get("name"),
            "dates": [str(value) for value in row["dates"]],
        })
    return episodes, {"path": str(path), "sha256": file_sha256(path), "episode_count": len(episodes)}


def resolve_request(dates: list[str], episodes: list[dict]) -> tuple[list[str], list[dict]]:
    """Turn the caller's raw ``--date``/``--episode`` input into the request.

    Returns the sorted, de-duplicated union of every requested date plus the
    normalized episode rows. Ordering is caller-independent so a shuffled
    command line reproduces a byte-identical report.
    """
    normalized = []
    seen_names = set()
    for episode in episodes:
        name = episode.get("name")
        if not isinstance(name, str) or EPISODE_NAME.fullmatch(name) is None:
            fail("EPISODE_NAME_INVALID", str(name))
        if name in seen_names:
            fail("EPISODE_NAME_DUPLICATE", name)
        seen_names.add(name)
        episode_dates = sorted({str(value) for value in episode.get("dates", [])})
        if not episode_dates:
            fail("EPISODE_HAS_NO_DATES", name)
        normalized.append({"name": name, "dates": episode_dates})
    normalized.sort(key=lambda row: row["name"])

    unique_dates = sorted(
        {str(value) for value in dates}
        | {date for row in normalized for date in row["dates"]}
    )
    if not unique_dates:
        fail("NO_DATES_REQUESTED")
    return unique_dates, normalized


# ---------------------------------------------------------------------------
# Per-market join.
# ---------------------------------------------------------------------------


def _iso_date(value: object) -> str | None:
    """Normalize a KR ``YYYYMMDD`` or ISO ``YYYY-MM-DD`` date, else ``None``."""
    if not isinstance(value, str):
        return None
    if DATE10.fullmatch(value) is not None:
        return value
    if DATE8.fullmatch(value) is not None:
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return None


def _date_like_strings(value: object) -> list[str]:
    """Every date-shaped string reachable inside ``value``, normalized to ISO.

    Walking the attestation generically (rather than naming KR's and US's
    current fields) means a date field added to either market's attestation is
    automatically covered by the lookahead re-check below instead of silently
    escaping it.
    """
    if isinstance(value, str):
        iso = _iso_date(value)
        return [iso] if iso is not None else []
    if isinstance(value, dict):
        return [item for entry in value.values() for item in _date_like_strings(entry)]
    if isinstance(value, list):
        return [item for entry in value for item in _date_like_strings(entry)]
    return []


def _unavailable_view(market: str, reason: str) -> dict:
    return {
        "market": market,
        "market_status": None,
        "outcome": OUTCOME_BLOCKED,
        "effective_date": None,
        "axis_coverage": None,
        "candidate_regime": None,
        "candidate_classification_status": None,
        "runtime_regime": "UNKNOWN",
        "failure_reason": reason,
        "lookahead_violation": False,
        "source_dates_consulted": [],
    }


def market_view(market: str, record: dict, requested_date: str) -> dict:
    """Project one market's replay record into the shared join vocabulary.

    The market's own status, coverage, and candidate normalization are carried
    verbatim — nothing is recomputed. The only judgement made here is the
    lookahead re-check, which can demote a market to ``BLOCKED`` for this one
    date but can never promote it.
    """
    status = record.get("status")
    outcome = OUTCOME_BY_MARKET_STATUS[market].get(status)
    if outcome is None:
        return _unavailable_view(market, f"UNRECOGNIZED_MARKET_RECORD_STATUS:{status}")

    candidate = record.get("candidate_normalized_result")
    five_axis = record.get("five_axis")
    attestation = record.get("no_lookahead_attestation")
    effective_date = _iso_date(record.get(EFFECTIVE_DATE_FIELD[market]))

    consulted = sorted(set(
        _date_like_strings(attestation) + ([effective_date] if effective_date else [])
    ))
    # Recomputed at the join rather than trusted: each market already anchors
    # backward, so this can only ever confirm that — but if it ever did not,
    # this date must fail closed instead of publishing the market's claim.
    anchor = _iso_date(requested_date)
    violating = [date for date in consulted if anchor is not None and date > anchor]
    if violating:
        view = _unavailable_view(market, LOOKAHEAD_CONTAINED)
        view["market_status"] = status
        view["lookahead_violation"] = True
        return view

    return {
        "market": market,
        "market_status": status,
        "outcome": outcome,
        "effective_date": effective_date,
        "axis_coverage": (
            copy.deepcopy(five_axis.get("coverage")) if isinstance(five_axis, dict) else None
        ),
        "candidate_regime": (
            candidate.get("paper_reference", {}).get("candidate_regime")
            if isinstance(candidate, dict)
            else None
        ),
        "candidate_classification_status": (
            candidate.get("classification_status") if isinstance(candidate, dict) else None
        ),
        "runtime_regime": (
            candidate.get("runtime_regime", "UNKNOWN") if isinstance(candidate, dict) else "UNKNOWN"
        ),
        "failure_reason": record.get("failure_reason"),
        "lookahead_violation": False,
        "source_dates_consulted": consulted,
    }


def _combined_normalized_result(views: dict) -> dict:
    """Carry both markets' existing normalization side by side — never merge.

    No ratified rule combines a KR candidate regime with a US one, so this
    result reports each market's own candidate verbatim and states that the
    cross-market answer is not computable. Producing one would be a new policy
    this module has no authority to invent.
    """
    return {
        "cross_market_regime": CROSS_MARKET_REGIME,
        "cross_market_classification_status": CROSS_MARKET_STATUS,
        "cross_market_score": None,
        "cross_market_confidence": None,
        "runtime_regime": "UNKNOWN",
        "per_market_candidate_regime": {
            market: views[market]["candidate_regime"] for market in MARKETS
        },
        "per_market_classification_status": {
            market: views[market]["candidate_classification_status"] for market in MARKETS
        },
        "normalization_source": (
            "regime/paper_regime_reference.py::build_kr,build_us"
            " (applied by the KR and US replay populations, not re-applied here)"
        ),
        "statement": (
            "Each market's candidate normalization is reported exactly as its own"
            " replay population produced it. No ratified rule combines them, so the"
            " cross-market regime stays UNKNOWN and no combined score, confidence,"
            " or ranking is computed."
        ),
    }


def combined_record(requested_date: str, views: dict, episode_names: list[str]) -> dict:
    replayed = [market for market in MARKETS if views[market]["outcome"] != OUTCOME_BLOCKED]
    if len(replayed) == len(MARKETS):
        combined_status = COMBINED_BOTH
    elif replayed:
        combined_status = COMBINED_SINGLE
    else:
        combined_status = COMBINED_NONE
    contained = [market for market in MARKETS if views[market]["lookahead_violation"]]
    anchor = _iso_date(requested_date)
    credited = [
        date for market in MARKETS for date in views[market]["source_dates_consulted"]
    ]
    return {
        "requested_date": requested_date,
        # Attached after both markets were replayed from the date alone; the
        # label is descriptive metadata and never an input to any evaluation.
        "episode_names": sorted(episode_names),
        "evidence_class": EVIDENCE_CLASS,
        "combined_status": combined_status,
        "markets": {market: views[market] for market in MARKETS},
        "markets_by_outcome": {
            outcome: [market for market in MARKETS if views[market]["outcome"] == outcome]
            for outcome in OUTCOMES
        },
        "combined_normalized_result": _combined_normalized_result(views),
        "no_lookahead_attestation": {
            "anchor_requested_date": requested_date,
            "per_market_source_dates": {
                market: list(views[market]["source_dates_consulted"]) for market in MARKETS
            },
            # Computed over the dates actually credited above, never asserted:
            # any market that consumed a later date was already demoted to
            # BLOCKED and cleared, so this is False by construction — and if it
            # ever were not, validate_population rejects the record.
            "any_source_date_after_requested_date": bool(
                anchor is not None and any(date > anchor for date in credited)
            ),
            "markets_failed_closed_for_lookahead": contained,
            "other_requested_dates_consulted": False,
            "episode_label_used_in_evaluation": False,
        },
    }


# ---------------------------------------------------------------------------
# Population.
# ---------------------------------------------------------------------------


def _market_population(market: str, build, validate, secrets: list[str]) -> tuple[dict | None, str | None]:
    """Build one market's replay population and check it with its own validator.

    A market that cannot produce a population its owning contract accepts is
    reported unavailable rather than published: the combined report then shows
    that market NOT_COMPUTABLE on every date instead of aborting, so a
    single-market replay still yields honest evidence.
    """
    try:
        population = build()
        validate(population)
    except (
        KRP.ReplayPopulationError, USP.ReplayPopulationError, CombinedReplayError,
    ) as exc:
        return None, redact(
            f"{MARKET_POPULATION_UNAVAILABLE}:{market}:{exc}", secrets,
        )
    except Exception as exc:  # noqa: BLE001 — deliberate per-market containment,
        # mirrors the per-date containment in both market modules: an
        # unrecognized failure shape degrades to "this market is not replayable
        # in this run" instead of aborting the combined report. Only the
        # exception *type* is recorded, never its message.
        return None, (
            f"{MARKET_POPULATION_UNAVAILABLE}:{market}"
            f":UNSUPPORTED_SHAPE_{type(exc).__name__}"
        )
    return population, None


def _pit_replay_block() -> dict:
    """The population's point-in-time declaration, built from the shared shape.

    Emitted here and re-required by ``_validate_pit_replay`` from the same
    constants, so a field can never be published without being checked or
    checked without being published.
    """
    return {
        **{key: True for key in PIT_REPLAY_TRUE_KEYS},
        **{key: False for key in PIT_REPLAY_FALSE_KEYS},
        "statement": PIT_REPLAY_STATEMENT,
    }


def _episode_coverage(dates: list[str], by_date: dict) -> dict:
    counts = {status: 0 for status in COMBINED_STATUSES}
    for date in dates:
        counts[by_date[date]["combined_status"]] += 1
    return {"requested_date_count": len(dates), "combined_status_counts": counts}


def build_population(
    credentials: dict,
    *,
    dates: list[str] | None = None,
    episodes: list[dict] | None = None,
    episode_sources: list[dict] | None = None,
    kr_opener=None,
    us_getter=None,
) -> dict:
    unique_dates, episode_rows = resolve_request(list(dates or []), list(episodes or []))
    secrets = [str(value) for value in credentials.values() if value]

    kr_kwargs = {} if kr_opener is None else {"opener": kr_opener}
    kr_population, kr_reason = _market_population(
        "KR",
        lambda: KRP.build_population(
            str(credentials.get("krx_auth_key", "")), unique_dates, **kr_kwargs,
        ),
        KRP.validate_population,
        secrets,
    )
    us_population, us_reason = _market_population(
        "US",
        lambda: USP.build_population(
            {
                "fred_key": credentials.get("fred_key", ""),
                "alpaca_key": credentials.get("alpaca_key", ""),
                "alpaca_secret": credentials.get("alpaca_secret", ""),
            },
            unique_dates,
            getter=us_getter,
        ),
        USP.validate_population,
        secrets,
    )

    populations = {"KR": kr_population, "US": us_population}
    unavailable = {"KR": kr_reason, "US": us_reason}
    indexed = {
        market: (
            {record["requested_date"]: record for record in populations[market]["records"]}
            if populations[market] is not None
            else {}
        )
        for market in MARKETS
    }
    episode_names_by_date: dict[str, list[str]] = {date: [] for date in unique_dates}
    for row in episode_rows:
        for date in row["dates"]:
            episode_names_by_date[date].append(row["name"])

    records = []
    for date in unique_dates:
        views = {}
        for market in MARKETS:
            if populations[market] is None:
                views[market] = _unavailable_view(market, unavailable[market])
            elif date not in indexed[market]:
                views[market] = _unavailable_view(market, MARKET_RECORD_MISSING)
            else:
                views[market] = market_view(market, indexed[market][date], date)
        records.append(combined_record(date, views, episode_names_by_date[date]))

    by_date = {record["requested_date"]: record for record in records}
    combined_counts = {status: 0 for status in COMBINED_STATUSES}
    market_counts = {market: {outcome: 0 for outcome in OUTCOMES} for market in MARKETS}
    for record in records:
        combined_counts[record["combined_status"]] += 1
        for market in MARKETS:
            market_counts[market][record["markets"][market]["outcome"]] += 1

    population = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "wbs": "P1-COM-05",
        "markets": list(MARKETS),
        "evidence_class": EVIDENCE_CLASS,
        "requested_dates": unique_dates,
        "ungrouped_dates": [
            date for date in unique_dates if not episode_names_by_date[date]
        ],
        "episodes": [
            {
                "name": row["name"],
                "dates": row["dates"],
                "date_selection": DATE_SELECTION,
                "label_semantics": LABEL_SEMANTICS,
                "coverage": _episode_coverage(row["dates"], by_date),
            }
            for row in episode_rows
        ],
        "episode_input_sources": list(episode_sources or []),
        "episode_selection": {
            # Structural facts, each pinned by
            # test/test_combined_shadow_historical_replay.py.
            "selected_by_this_module": False,
            "selection_source": DATE_SELECTION,
            "label_influences_any_record": False,
            "statement": (
                "Every requested date was supplied by the caller. This module never"
                " scans, infers, or chooses a bull, bear, sideways, or stress window,"
                " and an episode name is attached only after its dates have already"
                " been replayed, so no label can change any market observation or"
                " normalization result."
            ),
        },
        "market_population_status": {
            market: {
                "available": populations[market] is not None,
                "unavailable_reason": unavailable[market],
                "schema_version": (
                    populations[market]["schema_version"] if populations[market] else None
                ),
                "payload_sha256": (
                    populations[market]["payload_sha256"] if populations[market] else None
                ),
            }
            for market in MARKETS
        },
        "market_populations": {
            market: copy.deepcopy(populations[market]) for market in MARKETS
        },
        "source_reuse": [
            "regime/kr_historical_replay_population.py::build_population,validate_population",
            "regime/us_historical_replay_population.py::build_population,validate_population",
        ],
        "records": records,
        "combined_summary": {
            "requested_date_count": len(unique_dates),
            "episode_count": len(episode_rows),
            "combined_status_counts": combined_counts,
            "per_market_outcome_counts": market_counts,
            "cross_market_classification_status": CROSS_MARKET_STATUS,
        },
        # Structural facts about *how* this report was produced. Each is enforced
        # by test/test_combined_shadow_historical_replay.py rather than merely
        # asserted here, and re-required key for key by ``_validate_pit_replay``.
        "pit_replay": _pit_replay_block(),
        "authority": dict(AUTHORITY),
    }
    population["payload_sha256"] = payload_sha256(population)
    return population


def validate_population(value: dict) -> dict:
    """Integrity/shape check only — deliberately never re-derives.

    Re-derivation would require re-issuing live KRX/Alpaca/FRED requests for
    every replayed date. Per the CIO mandate an actual provider probe stays
    separate from implementation verification and must never become a CI
    prerequisite, so ``--verify`` checks the hash, the SHADOW/never-NATURAL
    shape, the never-cross-market-classified guarantee, and each embedded
    market population against that market's own validator.

    "Shape" is deliberately exact rather than "whatever happens to be present".
    A re-hashed payload is a valid signature over whatever it contains, so a
    check that only inspected the keys it found would accept a population that
    silently dropped its records or its explicit authority boundary. Every
    requested date therefore maps to exactly one record, every published count
    is recomputed from those records, and the authority block must match
    ``AUTHORITY`` key for key.

    Consistency with itself is still not enough. Each record is additionally
    re-derived from the embedded market records it claims to join, and each
    embedded population must replay exactly this population's requested dates —
    otherwise a coherent set of forged market views, or a genuine market
    population for a different date, would satisfy every self-consistency check
    above.

    A market with no embedded population has no records to check, so the one
    thing it must still carry is its *reason*: an unavailable market is BLOCKED
    on every date, and without an attributable cause that BLOCKED market becomes
    an UNKNOWN nothing explains.

    The two blocks that describe the population rather than its records are
    checked here too, for the same reason the record bijection is. The
    ``pit_replay`` declaration is the population's own statement that no date
    saw a later one, and ``episode_input_sources`` is its only statement of
    where its caller labels came from; a validator that carried either unread
    would accept a re-signed payload declaring that future dates *were* used, or
    pinning its episode file to a digest that is not a SHA-256 at all.
    """
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        fail("POPULATION_SCHEMA_INVALID")
    unsigned = copy.deepcopy(value)
    claimed = unsigned.pop("payload_sha256", None)
    if (
        not isinstance(claimed, str)
        or SHA256.fullmatch(claimed) is None
        or payload_sha256(unsigned) != claimed
    ):
        fail("POPULATION_SHA_INVALID")
    if value.get("mode") != MODE or value.get("evidence_class") != EVIDENCE_CLASS:
        fail("POPULATION_MODE_INVALID")
    if value.get("markets") != list(MARKETS):
        fail("POPULATION_SCOPE_INVALID", "markets")
    requested = value.get("requested_dates")
    if (
        not isinstance(requested, list)
        or not requested
        or any(not isinstance(date, str) for date in requested)
        or requested != sorted(set(requested))
    ):
        fail("POPULATION_DATE_ORDER_INVALID")
    # Embedded populations are checked first because every record below is
    # re-derived from them: a record can only be verified against the market
    # evidence it actually came from once that evidence is known to be valid.
    _validate_embedded_populations(value, requested)
    records = _validate_records(value, requested)
    _validate_episodes(value, requested, records)
    _validate_episode_input_sources(value)
    _validate_summary(value, requested, records)
    _validate_pit_replay(value)
    _validate_authority(value)
    return copy.deepcopy(value)


def _validate_pit_replay(value: dict) -> None:
    """The point-in-time declaration must be complete and must say what it says.

    Point-in-time integrity is non-negotiable, so the block asserting it is
    checked exactly rather than trusted. Three things are required and none is
    redundant:

    * the **exact key set**, because a payload that deletes
      ``future_dates_used_in_any_date_evaluation`` has not stopped claiming PIT
      integrity — it has stopped being checkable, and every other guarantee here
      would still pass;
    * the **declared value** of every flag, because a re-signed payload setting
      that flag ``true`` would otherwise publish, under a valid signature, a
      population that simultaneously claims and denies the boundary;
    * the **statement**, because rewriting the prose while leaving the booleans
      alone misdescribes the same payload just as effectively to a human reader.

    This is a check on what the population *declares*. What it actually did is
    enforced separately and structurally, by the per-record lookahead re-check
    in ``_validate_record`` and by full re-derivation from the embedded market
    records; neither depends on this block being honest.
    """
    pit = value.get("pit_replay")
    if not isinstance(pit, dict) or sorted(pit) != sorted(PIT_REPLAY_KEYS):
        fail("PIT_REPLAY_SCHEMA_INVALID")
    for key in PIT_REPLAY_TRUE_KEYS:
        if pit[key] is not True:
            fail("PIT_REPLAY_DECLARATION_INVALID", key)
    for key in PIT_REPLAY_FALSE_KEYS:
        if pit[key] is not False:
            fail("PIT_REPLAY_DECLARATION_INVALID", key)
    if pit["statement"] != PIT_REPLAY_STATEMENT:
        fail("PIT_REPLAY_STATEMENT_INVALID")


def _validate_episode_input_sources(value: dict) -> None:
    """Every pinned episode-input file must be attributable and well-formed.

    ``episode_input_sources`` is the population's only record of *which*
    caller-supplied file declared its episode labels. Carrying it unread made it
    forgeable in three separate ways, each of which this checks:

    * the block itself could be replaced with ``null`` or a bare string, leaving
      a population that claims a file-driven episode set while pinning nothing;
    * a record's ``sha256`` could be any text at all, so a pin that reads like
      provenance could name no file that could ever exist;
    * ``episode_count`` could exceed the episodes the population actually
      published, so a source could claim to have contributed labels that are not
      there.

    The count bound is deliberately an upper bound, not an equality: ``--episode``
    arguments add rows that no file declared, so the honest derivable fact is
    that the files together cannot have contributed more episodes than the
    population carries. Which specific published episode came from which file is
    not recorded by this contract, and is not inferred here.

    A path appearing twice is refused: ``load_episode_file`` is called once per
    caller-named file, and the same file listed twice would have produced
    duplicate episode names and already failed closed in ``resolve_request``.
    """
    sources = value.get("episode_input_sources")
    if not isinstance(sources, list):
        fail("EPISODE_INPUT_SOURCES_INVALID", "not a list")
    paths = []
    declared = 0
    for index, source in enumerate(sources):
        label = f"[{index}]"
        if not isinstance(source, dict) or sorted(source) != sorted(EPISODE_SOURCE_KEYS):
            fail("EPISODE_INPUT_SOURCE_SCHEMA_INVALID", label)
        path = source["path"]
        if not isinstance(path, str) or not path.strip():
            fail("EPISODE_INPUT_SOURCE_SCHEMA_INVALID", f"{label}.path")
        digest = source["sha256"]
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            fail("EPISODE_INPUT_SOURCE_SHA_INVALID", f"{label}.sha256")
        count = source["episode_count"]
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            fail("EPISODE_INPUT_SOURCE_COUNT_INVALID", f"{label}.episode_count")
        paths.append(path)
        declared += count
    if len(set(paths)) != len(paths):
        fail("EPISODE_INPUT_SOURCE_DUPLICATE_PATH")
    # ``_validate_episodes`` has already run, so ``episodes`` is a list of dicts.
    if declared > len(value["episodes"]):
        fail("EPISODE_INPUT_SOURCE_COUNT_EXCEEDS_PUBLISHED_EPISODES", str(declared))


def _validate_records(value: dict, requested: list[str]) -> list[dict]:
    """Exactly one record per requested date, in the same order — no omissions.

    ``build_population`` emits one record for each sorted, de-duplicated
    requested date, so list equality is the whole bijection. Checking only the
    records that happen to be present would let a re-hashed payload drop every
    record and still satisfy each per-record guarantee vacuously.

    Each record is then re-derived in full from the embedded market records it
    claims to join. The per-market view is this report's only statement about
    what a market showed, and it is a *projection* of an embedded record — so
    comparing it with anything other than that record leaves it forgeable.
    """
    records = value.get("records")
    if not isinstance(records, list) or len(records) != len(requested):
        fail("POPULATION_RECORDS_NOT_BIJECTIVE", "count")
    dates = [
        record.get("requested_date") if isinstance(record, dict) else None
        for record in records
    ]
    if dates != requested:
        fail("POPULATION_RECORDS_NOT_BIJECTIVE", "requested_date")
    indexed = _embedded_market_records(value)
    episode_names = _episode_names_by_date(value, requested)
    for record in records:
        date = record["requested_date"]
        views = {
            market: _expected_market_view(value, indexed, market, date)
            for market in MARKETS
        }
        _validate_record(
            record, requested, combined_record(date, views, episode_names[date]),
        )
    return records


def _embedded_market_records(value: dict) -> dict:
    """``{market: {requested_date: record} | None}`` from the embedded populations.

    ``None`` marks a market whose population was unavailable for the whole run,
    which is exactly the case ``build_population`` treats as "this market
    produced nothing on every date".
    """
    populations = value["market_populations"]
    return {
        market: (
            {record["requested_date"]: record for record in populations[market]["records"]}
            if populations[market] is not None
            else None
        )
        for market in MARKETS
    }


def _expected_market_view(
    value: dict, indexed: dict, market: str, requested_date: str,
) -> dict:
    """Rebuild one market's join view from the embedded record it must come from.

    This mirrors ``build_population``'s own three cases exactly: an unavailable
    market population, an available population with no record for this date, and
    the ordinary projection of that date's own market record. The middle case is
    build-time containment of a market module that broke its own record
    bijection; ``_validate_embedded_populations`` separately refuses to certify
    such a population, so it is reproduced here only so the mismatch is reported
    by the date-set check rather than as an unexplained record difference.
    """
    if indexed[market] is None:
        return _unavailable_view(
            market, value["market_population_status"][market]["unavailable_reason"],
        )
    record = indexed[market].get(requested_date)
    if record is None:
        return _unavailable_view(market, MARKET_RECORD_MISSING)
    return market_view(market, record, requested_date)


def _episode_names_by_date(value: dict, requested: list[str]) -> dict:
    """Which caller labels name each requested date — shape only, checked later.

    Only what full-record re-derivation needs is resolved here; the deeper
    episode guarantees (semantics, ordering, reproduced coverage) stay in
    ``_validate_episodes``, which runs once the records exist to check them
    against.
    """
    names: dict[str, list[str]] = {date: [] for date in requested}
    episodes = value.get("episodes")
    if not isinstance(episodes, list):
        fail("EPISODE_LIST_INVALID")
    for episode in episodes:
        if not isinstance(episode, dict) or not isinstance(episode.get("dates"), list):
            fail("EPISODE_LIST_INVALID", "episode")
        name = episode.get("name")
        if not isinstance(name, str):
            fail("EPISODE_NAME_INVALID", str(name))
        for date in episode["dates"]:
            if date not in names:
                fail("EPISODE_DATE_NOT_REQUESTED", name)
            names[date].append(name)
    return names


def _validate_summary(value: dict, requested: list[str], records: list[dict]) -> None:
    """Recompute every published count from the records it claims to summarize.

    A summary is a derived view, never an independent assertion: if it does not
    reproduce from the records, one of the two is wrong and the population must
    fail closed rather than let a reader trust a count nothing supports.
    """
    combined_counts = {status: 0 for status in COMBINED_STATUSES}
    market_counts = {market: {outcome: 0 for outcome in OUTCOMES} for market in MARKETS}
    for record in records:
        combined_counts[record["combined_status"]] += 1
        for market in MARKETS:
            market_counts[market][record["markets"][market]["outcome"]] += 1
    # ``_validate_episodes`` has already run, so ``episodes`` is a list of dicts.
    episodes = value["episodes"]
    expected = {
        "requested_date_count": len(requested),
        "episode_count": len(episodes),
        "combined_status_counts": combined_counts,
        "per_market_outcome_counts": market_counts,
        "cross_market_classification_status": CROSS_MARKET_STATUS,
    }
    if value.get("combined_summary") != expected:
        fail("COMBINED_SUMMARY_INCONSISTENT")
    ungrouped = [
        date for date in requested
        if not any(date in episode["dates"] for episode in episodes)
    ]
    if value.get("ungrouped_dates") != ungrouped:
        fail("UNGROUPED_DATES_INCONSISTENT")


def _validate_authority(value: dict) -> None:
    """The authority block must be present, complete, and exactly as declared."""
    authority = value.get("authority")
    if not isinstance(authority, dict) or sorted(authority) != sorted(AUTHORITY):
        fail("POPULATION_AUTHORITY_SCHEMA_INVALID")
    for key, allowed in AUTHORITY.items():
        if authority[key] is not allowed:
            fail("POPULATION_AUTHORITY_INVALID", key)


def _validate_episodes(value: dict, requested: list[str], records: list[dict]) -> None:
    selection = value.get("episode_selection", {})
    if (
        selection.get("selected_by_this_module") is not False
        or selection.get("label_influences_any_record") is not False
        or selection.get("selection_source") != DATE_SELECTION
    ):
        fail("EPISODE_SELECTION_INVALID")
    episodes = value.get("episodes")
    if not isinstance(episodes, list):
        fail("EPISODE_LIST_INVALID")
    by_date = {record["requested_date"]: record for record in records}
    names = []
    for episode in episodes:
        if not isinstance(episode, dict):
            fail("EPISODE_LIST_INVALID", "episode")
        name = episode.get("name")
        if not isinstance(name, str) or EPISODE_NAME.fullmatch(name) is None:
            fail("EPISODE_NAME_INVALID", str(name))
        if episode.get("label_semantics") != LABEL_SEMANTICS:
            fail("EPISODE_LABEL_SEMANTICS_INVALID", name)
        if episode.get("date_selection") != DATE_SELECTION:
            fail("EPISODE_SELECTION_INVALID", name)
        dates = episode.get("dates")
        if not isinstance(dates, list) or not dates or dates != sorted(set(dates)):
            fail("EPISODE_HAS_NO_DATES", name)
        # An episode may only label dates the caller actually requested; a date
        # appearing here but not in the population would mean this module
        # selected it.
        if any(date not in requested for date in dates):
            fail("EPISODE_DATE_NOT_REQUESTED", name)
        # A label describes the dates it names and nothing else, so its coverage
        # must reproduce from those dates' own records rather than be asserted.
        if episode.get("coverage") != _episode_coverage(dates, by_date):
            fail("EPISODE_COVERAGE_INCONSISTENT", name)
        names.append(name)
    if names != sorted(set(names)):
        fail("EPISODE_NAME_DUPLICATE")


def _validate_record(record: dict, requested: list[str], expected: dict) -> None:
    if not isinstance(record, dict) or record.get("evidence_class") != EVIDENCE_CLASS:
        fail("RECORD_EVIDENCE_CLASS_INVALID")
    if record.get("requested_date") not in requested:
        fail("RECORD_DATE_NOT_REQUESTED")
    if record.get("combined_status") not in COMBINED_STATUSES:
        fail("RECORD_COMBINED_STATUS_INVALID")
    combined = record.get("combined_normalized_result")
    if not isinstance(combined, dict):
        fail("RECORD_NORMALIZED_RESULT_MISSING")
    # The load-bearing guarantee of this slice: no combined regime is ever
    # published, whatever the two markets individually reported.
    if combined.get("cross_market_regime") != CROSS_MARKET_REGIME:
        fail("CROSS_MARKET_MUST_NOT_CLASSIFY")
    if combined.get("cross_market_classification_status") != CROSS_MARKET_STATUS:
        fail("CROSS_MARKET_STATUS_INVALID")
    if combined.get("cross_market_score") is not None or combined.get("cross_market_confidence") is not None:
        fail("CROSS_MARKET_MUST_NOT_SCORE")
    if combined.get("runtime_regime") != "UNKNOWN":
        fail("RUNTIME_REGIME_MUST_STAY_UNKNOWN")
    views = record.get("markets")
    if not isinstance(views, dict) or sorted(views) != sorted(MARKETS):
        fail("RECORD_MARKET_SET_INVALID")
    for market in MARKETS:
        view = views[market]
        if not isinstance(view, dict) or view.get("outcome") not in OUTCOMES:
            fail("RECORD_MARKET_OUTCOME_INVALID", market)
        if view.get("runtime_regime") != "UNKNOWN":
            fail("RUNTIME_REGIME_MUST_STAY_UNKNOWN", market)
        anchor = _iso_date(record.get("requested_date"))
        if anchor is not None and any(
            date > anchor for date in view.get("source_dates_consulted", [])
        ):
            fail("RECORD_LOOKAHEAD_VIOLATION", market)
        # A market the join blocked produced nothing on this date, so it may not
        # carry a candidate regime a reader could still count.
        if view["outcome"] == OUTCOME_BLOCKED and view.get("candidate_regime") is not None:
            fail("BLOCKED_MARKET_MUST_NOT_CLASSIFY", market)
    # The US replay population classifies nothing (3/5 coverage); a combined
    # record claiming otherwise would mean a US regime was manufactured.
    if views["US"].get("candidate_regime") not in (None, "UNKNOWN"):
        fail("US_PARTIAL_COVERAGE_MUST_NOT_CLASSIFY")
    # Both derived views of the same outcomes are recomputed rather than
    # trusted: a payload cannot report a combined status or an outcome grouping
    # its own per-market views do not support.
    replayed = [market for market in MARKETS if views[market]["outcome"] != OUTCOME_BLOCKED]
    expected_status = (
        COMBINED_BOTH if len(replayed) == len(MARKETS)
        else COMBINED_SINGLE if replayed
        else COMBINED_NONE
    )
    if record["combined_status"] != expected_status:
        fail("RECORD_COMBINED_STATUS_INCONSISTENT")
    if record.get("markets_by_outcome") != {
        outcome: [market for market in MARKETS if views[market]["outcome"] == outcome]
        for outcome in OUTCOMES
    }:
        fail("RECORD_OUTCOME_GROUPING_INCONSISTENT")
    per_market = combined.get("per_market_candidate_regime")
    if per_market != {market: views[market].get("candidate_regime") for market in MARKETS}:
        fail("RECORD_PER_MARKET_REGIME_INCONSISTENT")
    # The load-bearing binding: every check above compares the record with its
    # own mutable view, so all of them pass on a re-hashed payload whose views
    # were forged together. This last one compares the whole record with the one
    # rebuilt from the embedded market records it claims to join, so a market
    # view — its outcome, coverage, candidate regime, consulted source dates, or
    # containment claim — cannot say anything the underlying record does not.
    if record != expected:
        fail("RECORD_NOT_DERIVED_FROM_ITS_EMBEDDED_MARKET_RECORDS", record["requested_date"])


def _validate_unavailable_reason(market: str, reason: object) -> None:
    """An unavailable market population must say *why*, in an attributable form.

    ``build_population`` records exactly one code here —
    ``MARKET_POPULATION_UNAVAILABLE:<market>:<detail>`` — for both the
    contract-rejection and the unrecognized-shape case, so that exact shape,
    naming this market and carrying a non-empty detail, is what is required.
    """
    prefix = f"{MARKET_POPULATION_UNAVAILABLE}:{market}:"
    if (
        not isinstance(reason, str)
        or not reason.startswith(prefix)
        or not reason[len(prefix):].strip()
    ):
        fail("UNAVAILABLE_MARKET_MUST_CARRY_AN_ATTRIBUTABLE_REASON", market)


def _validate_embedded_populations(value: dict, requested: list[str]) -> None:
    populations = value.get("market_populations")
    if not isinstance(populations, dict) or sorted(populations) != sorted(MARKETS):
        fail("POPULATION_SCOPE_INVALID", "market_populations")
    statuses = value.get("market_population_status")
    if not isinstance(statuses, dict) or sorted(statuses) != sorted(MARKETS):
        fail("POPULATION_SCOPE_INVALID", "market_population_status")
    validators = {"KR": KRP.validate_population, "US": USP.validate_population}
    for market in MARKETS:
        embedded = populations[market]
        status = statuses[market]
        if not isinstance(status, dict) or sorted(status) != [
            "available", "payload_sha256", "schema_version", "unavailable_reason",
        ]:
            fail("MARKET_POPULATION_STATUS_INVALID", market)
        if status["available"] is not (embedded is not None):
            fail("MARKET_POPULATION_STATUS_INCONSISTENT", market)
        if embedded is None:
            if status["schema_version"] is not None or status["payload_sha256"] is not None:
                fail("MARKET_POPULATION_STATUS_INCONSISTENT", market)
            # An unavailable market is still an *attributable* one. Checking only
            # the two pinned-identity fields would let a re-signed payload delete
            # the reason: every record's view for that market is rebuilt from
            # this field, so both the status and every ``failure_reason`` would
            # then agree on ``null`` and every self-consistency check above would
            # still pass. The result is a BLOCKED market with no recorded cause,
            # which downstream evidence can only summarize as an unattributed
            # UNKNOWN. The shape is required too, not merely non-emptiness, so
            # the reason keeps naming the market it belongs to.
            _validate_unavailable_reason(market, status["unavailable_reason"])
            continue
        try:
            validators[market](copy.deepcopy(embedded))
        except (KRP.ReplayPopulationError, USP.ReplayPopulationError) as exc:
            raise CombinedReplayError(
                f"EMBEDDED_POPULATION_INVALID:{market}:{exc}"
            ) from exc
        # The pinned identity of an embedded population must be that
        # population's own, so a reader cannot be pointed at a different one.
        if (
            status["schema_version"] != embedded.get("schema_version")
            or status["payload_sha256"] != embedded.get("payload_sha256")
            or status["unavailable_reason"] is not None
        ):
            fail("MARKET_POPULATION_STATUS_INCONSISTENT", market)
        # A market population is internally valid over *its own* requested
        # dates, so validity alone does not make it the source of these
        # records. An embedded population replaying a different date set could
        # otherwise supply one date's genuine market evidence to a combined
        # record claiming another date.
        if embedded.get("requested_dates") != requested:
            fail("EMBEDDED_POPULATION_DATE_SET_MISMATCH", market)


# ---------------------------------------------------------------------------
# Output boundary.
# ---------------------------------------------------------------------------


def _forbid_tracked_output(root: Path, path: Path) -> None:
    """Fail closed if ``path`` resolves inside this repository checkout.

    Combined historical replay evidence must never land in any tracked
    location — the NATURAL ``data/observations/`` and
    ``evidence/free_market_data/`` paths included — so the guard is a blanket
    "not inside the checkout at all", not a NATURAL-path denylist that a new
    tracked directory could slip past.
    """
    root_resolved = Path(root).resolve()
    path_resolved = Path(path).resolve()
    try:
        path_resolved.relative_to(root_resolved)
    except ValueError:
        return
    fail("TRACKED_OUTPUT_FORBIDDEN", str(path_resolved))


def _atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_population(population: dict, out_path: Path, *, root: Path = ROOT) -> Path:
    _forbid_tracked_output(root, out_path)
    text = json.dumps(population, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(Path(out_path), text)
    return Path(out_path)


def _default_temp_out() -> Path:
    fd, name = tempfile.mkstemp(prefix="combined_shadow_historical_replay.", suffix=".json")
    os.close(fd)
    return Path(name)


def _credentials_from_env() -> dict:
    # ★ The account/trading Alpaca credential (`ALPACA_API_KEY` /
    # `ALPACA_API_SECRET`) lives only in the private evidence repo. Market-data
    # credential resolution is delegated to the US module verbatim, so this
    # slice has no code path of its own that could read those names.
    return {"krx_auth_key": os.environ.get("KRX_API_KEY", "").strip(), **USP._credentials_from_env()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date", action="append", default=[], dest="dates",
        help="Historical date, YYYY-MM-DD, replayed in both markets. Repeatable."
             " No date is ever selected automatically.",
    )
    parser.add_argument(
        "--episode", action="append", default=[], dest="episodes",
        help="Caller-labelled episode as NAME=YYYY-MM-DD[,YYYY-MM-DD...]. Repeatable."
             " The label is descriptive only and never selects a date.",
    )
    parser.add_argument(
        "--episode-file", type=Path, default=None,
        help='Caller-supplied JSON: {"episodes": [{"name": ..., "dates": [...]}]}.',
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="External output path (must be outside this checkout)."
             " Defaults to a private system-temp file.",
    )
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()

    if args.verify:
        value = json.loads(Path(args.verify).read_text(encoding="utf-8"))
        validate_population(value)
        print(f"PASS_COMBINED_SHADOW_HISTORICAL_REPLAY_VERIFIED:{value['payload_sha256']}")
        return 0

    episodes = [parse_episode_argument(text) for text in args.episodes]
    episode_sources = []
    if args.episode_file is not None:
        from_file, source = load_episode_file(args.episode_file)
        episodes.extend(from_file)
        episode_sources.append(source)
    if not args.dates and not episodes:
        fail("NO_DATES_REQUESTED")

    population = build_population(
        _credentials_from_env(),
        dates=args.dates,
        episodes=episodes,
        episode_sources=episode_sources,
    )
    out_path = args.out if args.out is not None else _default_temp_out()
    write_population(population, out_path)
    summary = population["combined_summary"]
    print(json.dumps(
        {
            "out": str(out_path),
            "payload_sha256": population["payload_sha256"],
            "records": len(population["records"]),
            "episodes": summary["episode_count"],
            "combined_status_counts": summary["combined_status_counts"],
            "market_populations_available": {
                market: population["market_population_status"][market]["available"]
                for market in MARKETS
            },
        },
        ensure_ascii=False, sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
