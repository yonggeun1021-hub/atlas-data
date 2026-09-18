#!/usr/bin/env python3
"""Daily producer freshness watchdog (read-only, evidence-only).

Three silent failures were found by hand on 2026-09-18, each unnoticed for
days:

  * ``free-market-data.yml`` failed 2026-09-16 and 2026-09-17 on a push
    race -- US Stage 1 evidence and the US rotation chain silently stopped
    advancing.
  * The KIS master capture (outside this public repo) failed every day for
    days -- a late run outside its binding window.
  * KR/US population symbol observations had not been produced since
    2026-09-10 / 2026-09-11 -- a week -- and nothing noticed, because
    ``decision/korea_population_symbol_observation.py`` and
    ``decision/us_population_symbol_observation.py`` have never had a
    scheduled trigger in ``.github/workflows`` at all.

Nothing in this repo watches for "a daily producer stopped producing".
This module is that watch. It never fetches anything, never writes
anything under ``data/`` or ``evidence/``, and carries zero order/action/
production/capital authority -- see ``authority`` in :func:`build_report`.

Design choices, made explicit rather than guessed:

* **No weekday inference.** ``config/us_session_calendar_source_v1.json``
  and every module built on it (``market_data/us_official_session_calendar.py``,
  ``regime/kr_paper_runtime_calendar_packets.py``, etc.) declare
  ``weekday_inference_prohibited: true`` -- market OPEN/CLOSED status must
  come from an official capture, never from ``date.weekday()`` alone. This
  module honours that split:
    - For KR-linked producers, the officially captured KRX holiday list
      already committed at
      ``evidence/market_calendar/krx_global_holiday/2026-09-09/capture-2026.json``
      is replayed offline through the unmodified
      ``regime.kr_paper_runtime_calendar_packets`` /
      ``market_data.krx_official_holiday_calendar`` builders (the same
      modules KR PAPER runtime already trusts) to decide, for any date in
      the captured year, whether KRX was open. A Korean holiday therefore
      never counts as a missed cycle.
    - For US/crypto-linked producers, there is no committed official
      NYSE/Nasdaq capture anywhere in this repo (only the *source*
      ratification in ``config/us_session_calendar_source_v1.json`` --
      fetching the actual page needs network access this watchdog
      deliberately does not take). Rather than invent a fixed-date holiday
      table (the exact anti-pattern the repo prohibits), each producer's
      *own already-declared* GitHub Actions ``cron`` day-of-week set is
      read as the ground truth for which days it is expected to run --
      that is reading existing configuration, not guessing market status.
      This resolves the concrete example in the brief ("a weekday-only US
      producer must not alarm on a Monday for not having run on Sunday")
      without claiming US-holiday awareness this repo does not have. US
      federal holidays are absorbed by each item's one-cycle grace
      (``allowed_missed_cycles``) rather than by a guessed holiday list.
* **Self-declared thresholds win.** ``data/latest_rotation_confirmation_{kr,us,crypto}.json``
  already carries its own ``maximum_observation_gap_days`` -- the rule's own
  declared tolerance. This module reads that field from the artifact
  itself instead of hard-coding a second, possibly-drifting, opinion.
* **"Never produced" is reported differently from "went stale".** A
  producer with zero ``.github/workflows`` trigger at all (population
  symbol observations, the Alpaca SIP daily-bars dispatch-only capture) is
  labelled ``NO_SCHEDULE_*`` rather than ``STALE`` -- there is no cron to
  have "missed". A path/glob that has *never* matched anything is labelled
  ``NEVER_PRODUCED`` -- structurally different from an artifact that used
  to update and stopped.
* **"The source is quiet" is reported differently from "we are broken".**
  Age alone cannot tell those apart, and an alarm that conflates them
  trains its reader to dismiss it. On 2026-09-18 that produced a false
  incident report: ``evidence/fred_dexkous_fx`` legitimately ends at
  2026-09-11 because FRED's H.10 DEXKOUS series had published nothing
  newer, while the collector ran fine every day (its raw captures for
  09-15/09-16/09-17 are committed, each one's manifest declaring
  ``observation_date_range`` ending 2026-09-11). So every watched item now
  records *two* dates and classifies on the pair:

    - ``last_date`` -- the newest observation we hold, as before;
    - ``source_latest`` -- the newest date the SOURCE itself claims to
      offer, read only from evidence this repo already commits (a raw
      manifest's ``observation_date_range`` end, a venue manifest's
      ``latest_finalized_day``, the newest dated directory of the upstream
      producer the artifact itself names as its input). No network call and
      no new collection source is introduced; see ``source_latest`` in each
      :func:`default_watchlist` entry for the exact field used.

  The resulting states:

    - ``SOURCE_NOT_YET_PUBLISHED`` -- we are level with the source. Not an
      alarm; reported informationally with both dates shown.
    - ``COLLECTION_BEHIND_SOURCE`` -- the source offers newer than we hold.
      The loudest alarm in this module, because it means a run that looked
      successful dropped data. This is checked even when the calendar axis
      says FRESH, since that is exactly the case age cannot see.
    - ``SOURCE_LATEST_UNKNOWN`` -- no *current* source-side latest date is
      available, so the distinction genuinely cannot be made. Its own
      explicit state: never folded into "fine" and never into "stale". Two
      causes, reported separately in ``source_latest_status``:
        * ``UNAVAILABLE`` -- the producer's committed evidence exposes no
          source-side latest date at all (a structural blind spot; each such
          entry carries a ``resolve_by`` note saying what would have to be
          captured). Nothing is invented to fill the gap.
        * ``CLAIM_NOT_CURRENT`` -- a source-latest field exists but the
          evidence carrying it is itself stalled, so its claim is
          co-frozen with our output and cannot clear an alarm. Without this
          guard a producer whose own artifact carries its source-latest
          field would self-certify as "source is quiet" for exactly as long
          as it stayed broken -- which is how ``free_market_data``'s real
          09-16/09-17 outage would have been silenced.

  A source that is ahead by no more than ``source_lag_allowance`` expected
  cycles (default 1) is not an alarm: upstream normally lands before the
  producer that reads it within the same cycle.

* **Cross-reference, not duplication: PR #794 /
  ``collectors/verify_evidence_staged.py``.** A run that reports success
  while ``COLLECTION_BEHIND_SOURCE`` holds is precisely the failure #794
  guards against, but the two act at different times on different inputs
  and neither replaces the other. #794 runs *inside* the collection job:
  it compares the collector's own reported ``new_observation_paths``
  against ``git diff --staged --name-only`` and fails the run before
  ``git commit``, so a file the collector claims to have written but did
  not stage never becomes a green run. That check is scoped to one run's
  self-report, which is also its limit. This watchdog runs *after the
  fact* against what is actually committed, and so covers what #794
  structurally cannot see: a producer that never ran at all, one that was
  never scheduled (the population observations have no workflow trigger),
  a push lost after the guard passed, an upstream that advanced while the
  downstream did not, and -- the case here -- a collector whose own
  self-report is empty and correct precisely *because* the source
  published nothing. #794 is the gate; this is the audit that the gate was
  reached at all. Neither reimplements the other's comparison: #794 never
  reads a source-side date, and this module never inspects a git index.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KST = ZoneInfo("Asia/Seoul")
# /2 adds the source-side axis: source_latest, schedule_status, and the
# alarm / unknown / informational split (see the module docstring).
SCHEMA_VERSION = "daily_producer_freshness_watchdog/2"


def _load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


KR_CALENDAR_PACKETS = _load_module(
    "atlas_watchdog_kr_paper_runtime_calendar_packets",
    "regime/kr_paper_runtime_calendar_packets.py",
)
KRX_HOLIDAY_CALENDAR = KR_CALENDAR_PACKETS.CALENDAR


class FreshnessWatchdogError(ValueError):
    """A watchlist item could not be evaluated at all (bad spec, not a stale artifact)."""


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------

def kr_trading_day_status(day: dt.date, root: Path = ROOT) -> str:
    """OPEN_REGULAR / CLOSED / UNKNOWN for one KST date, replayed offline
    from the already-committed official KRX holiday capture. Never guesses
    from ``day.weekday()`` outside that officially-approved rule; a date
    outside the captured year (or any integrity mismatch) is UNKNOWN rather
    than guessed, and UNKNOWN is treated by the caller as "not an expected
    day" -- silence toward false alarms, never toward missing a real one,
    because KRX's captured year already covers the near-term dates this
    watchdog runs against.
    """
    try:
        capture_raw = (root / KR_CALENDAR_PACKETS.CAPTURE_REF).read_bytes()
    except OSError:
        return "UNKNOWN"
    if KRX_HOLIDAY_CALENDAR.digest(capture_raw) != KR_CALENDAR_PACKETS.CAPTURE_SHA256:
        return "UNKNOWN"
    try:
        packet, _receipt = KRX_HOLIDAY_CALENDAR.build_calendar_packet(
            capture_raw, KR_CALENDAR_PACKETS.CAPTURE_REF, day.isoformat()
        )
    except KRX_HOLIDAY_CALENDAR.KrxOfficialHolidayCalendarError:
        return "UNKNOWN"
    return packet["calendar"]["status"]


def is_expected_production_day(calendar: dict, day: dt.date, root: Path = ROOT) -> bool:
    kind = calendar["type"]
    if kind == "EVERY_DAY":
        return True
    if kind == "WEEKDAY_SET":
        return day.weekday() in calendar["weekdays"]
    if kind == "KR_TRADING_DAY":
        status = kr_trading_day_status(day, root)
        if status == "UNKNOWN":
            return False  # conservative: never alarm on a date we cannot officially classify
        return status == "OPEN_REGULAR"
    raise FreshnessWatchdogError(f"UNKNOWN_CALENDAR_TYPE:{kind}")


def missed_expected_days(calendar: dict, last_date: dt.date, today: dt.date, root: Path = ROOT) -> list[dt.date]:
    """Expected-production days strictly after ``last_date`` up to and
    including ``today``."""
    missed = []
    day = last_date + dt.timedelta(days=1)
    while day <= today:
        if is_expected_production_day(calendar, day, root):
            missed.append(day)
        day += dt.timedelta(days=1)
    return missed


# ---------------------------------------------------------------------------
# Artifact reading
# ---------------------------------------------------------------------------

DATE_LEN = len("YYYY-MM-DD")


def _get_nested(value: object, dotted_path: str):
    cursor = value
    for key in dotted_path.split("."):
        if not isinstance(cursor, dict) or key not in cursor:
            return None
        cursor = cursor[key]
    return cursor


def _extract_date(value: object) -> dt.date | None:
    if not isinstance(value, str) or len(value) < DATE_LEN:
        return None
    try:
        return dt.date.fromisoformat(value[:DATE_LEN])
    except ValueError:
        return None


def _read_json(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _latest_glob_date(root: Path, glob_pattern: str) -> dt.date | None:
    best = None
    for match in root.glob(glob_pattern):
        candidate = _extract_date(match.parent.name)
        if candidate is not None and (best is None or candidate > best):
            best = candidate
    return best


# ---------------------------------------------------------------------------
# Source-side latest date ("what does the source itself say it offers?")
# ---------------------------------------------------------------------------

# Locator kinds for a watchlist entry's ``source_latest`` declaration.
SOURCE_UNAVAILABLE = "UNAVAILABLE"        # no source-side latest is committed anywhere
SOURCE_SELF_FIELD = "SELF_FIELD"          # a field inside the producer's own artifact
SOURCE_DATED_PATH = "DATED_PATH"          # newest dated path segment IS the source's latest
SOURCE_DATED_PATH_FIELD = "DATED_PATH_FIELD"  # newest dated path -> read a field inside it

# ``source_latest_status`` values.
SOURCE_STATUS_RESOLVED = "RESOLVED"
SOURCE_STATUS_UNAVAILABLE = "UNAVAILABLE"
SOURCE_STATUS_NO_EVIDENCE = "NO_SOURCE_EVIDENCE"
SOURCE_STATUS_CLAIM_NOT_CURRENT = "CLAIM_NOT_CURRENT"
SOURCE_STATUS_NOT_DECLARED = "NOT_DECLARED"

DEFAULT_SOURCE_LAG_ALLOWANCE = 1


def _path_date(relative_parts: tuple[str, ...]) -> dt.date | None:
    """The last date-shaped segment of a committed evidence path.

    Handles both ``.../<date>/packet.json`` and the content-addressed
    ``.../<date>/<revision_sha256>/manifest.json`` layout without caring
    which depth the date sits at.
    """
    best = None
    for part in relative_parts:
        candidate = _extract_date(part)
        if candidate is not None:
            best = candidate
    return best


def _collect_dates(value: object, parts: tuple[str, ...]) -> list[dt.date]:
    """Every date reachable at ``parts`` under ``value``.

    Segment forms: ``key`` (dict key), ``3`` (list index), ``key[]`` /
    ``[]`` (fan out over every element of a list, so the caller can take the
    max across e.g. 624 per-pair OHLC entries).
    """
    if not parts:
        found = _extract_date(value)
        return [found] if found is not None else []
    head, rest = parts[0], parts[1:]
    if head.endswith("[]"):
        cursor = value
        key = head[:-2]
        if key:
            if not isinstance(value, dict) or key not in value:
                return []
            cursor = value[key]
        if not isinstance(cursor, list):
            return []
        collected: list[dt.date] = []
        for element in cursor:
            collected.extend(_collect_dates(element, rest))
        return collected
    if head.isdigit():
        if not isinstance(value, list):
            return []
        index = int(head)
        if index >= len(value):
            return []
        return _collect_dates(value[index], rest)
    if not isinstance(value, dict) or head not in value:
        return []
    return _collect_dates(value[head], rest)


def _max_date_at(value: object, dotted_path: str) -> dt.date | None:
    found = _collect_dates(value, tuple(dotted_path.split(".")))
    return max(found) if found else None


def _newest_dated_matches(root: Path, glob_pattern: str) -> tuple[dt.date | None, list[Path]]:
    """The newest date any match's path carries, plus every match on it.

    Several revisions can share one date (the FX raw capture commits one
    content-addressed revision directory per run, two on some days), so the
    caller reads all of them and takes the max claim.
    """
    best: dt.date | None = None
    newest: list[Path] = []
    for match in root.glob(glob_pattern):
        candidate = _path_date(match.relative_to(root).parts)
        if candidate is None:
            continue
        if best is None or candidate > best:
            best, newest = candidate, [match]
        elif candidate == best:
            newest.append(match)
    return best, newest


def _claim_is_current(
    spec: dict,
    root: Path,
    today: dt.date,
    claim_date: dt.date,
    declared_gap: int | None,
) -> bool:
    """Is a source-latest claim captured on ``claim_date`` still speaking for
    today, or is it co-frozen with a stalled producer?

    Deliberately measured against *today's* expected cycle rather than
    against our own last observation: a collector that stops writing both
    its raw captures and its observations must not be able to point at its
    own final raw capture and call the source quiet forever.
    """
    calendar = spec["calendar"]
    if calendar["type"] == "NO_SCHEDULE":
        threshold = declared_gap if declared_gap is not None else spec["default_max_gap_days"]
        return (today - claim_date).days <= threshold
    if declared_gap is not None:
        return (today - claim_date).days <= declared_gap
    allowed = spec.get("allowed_missed_cycles", 1)
    return len(missed_expected_days(calendar, claim_date, today, root)) <= allowed


def resolve_source_latest(
    spec: dict,
    root: Path,
    today: dt.date,
    parsed: dict | None,
    declared_gap: int | None,
) -> dict:
    """Read the source's own latest offered date from committed evidence only.

    Never fetches anything and never guesses: an absent or stalled claim is
    reported as such (``UNAVAILABLE`` / ``NO_SOURCE_EVIDENCE`` /
    ``CLAIM_NOT_CURRENT``) rather than filled in with a substitute value.
    """
    locator = spec.get("source_latest")
    if locator is None:
        return {"source_latest_status": SOURCE_STATUS_NOT_DECLARED}

    facts: dict = {
        "source_latest": None,
        "source_latest_status": SOURCE_STATUS_RESOLVED,
        "source_latest_field": locator.get("field_note"),
        "source_claim_date": None,
    }

    kind = locator["kind"]
    if kind == SOURCE_UNAVAILABLE:
        facts["source_latest_status"] = SOURCE_STATUS_UNAVAILABLE
        facts["source_latest_resolve_by"] = locator["resolve_by"]
        return facts

    if kind == SOURCE_SELF_FIELD:
        if parsed is None:
            facts["source_latest_status"] = SOURCE_STATUS_NO_EVIDENCE
            return facts
        found = _max_date_at(parsed, locator["field"])
        # The claim rides inside the producer's own artifact, so it is only
        # as current as that artifact is.
        claim_date = _extract_date(_get_nested(parsed, locator["claim_date_field"]))
        if found is None or claim_date is None:
            facts["source_latest_status"] = SOURCE_STATUS_NO_EVIDENCE
            return facts
        facts["source_latest"] = found.isoformat()
        facts["source_claim_date"] = claim_date.isoformat()
        if not _claim_is_current(spec, root, today, claim_date, declared_gap):
            facts["source_latest_status"] = SOURCE_STATUS_CLAIM_NOT_CURRENT
        return facts

    if kind in (SOURCE_DATED_PATH, SOURCE_DATED_PATH_FIELD):
        claim_date, matches = _newest_dated_matches(root, locator["glob"])
        if claim_date is None:
            facts["source_latest_status"] = SOURCE_STATUS_NO_EVIDENCE
            return facts
        facts["source_claim_date"] = claim_date.isoformat()
        if kind == SOURCE_DATED_PATH:
            found = claim_date
        else:
            candidates = []
            for match in matches:
                payload = _read_json(match)
                if payload is None:
                    continue
                at_path = _max_date_at(payload, locator["field"])
                if at_path is not None:
                    candidates.append(at_path)
            if not candidates:
                facts["source_latest_status"] = SOURCE_STATUS_NO_EVIDENCE
                return facts
            found = max(candidates)
        facts["source_latest"] = found.isoformat()
        if not _claim_is_current(spec, root, today, claim_date, declared_gap):
            facts["source_latest_status"] = SOURCE_STATUS_CLAIM_NOT_CURRENT
        return facts

    raise FreshnessWatchdogError(f"UNKNOWN_SOURCE_LATEST_KIND:{kind}")


def _our_comparable_date(spec: dict, parsed: dict | None, last_date: dt.date) -> dt.date:
    """The date of ours that is in the same unit as the source's claim.

    Most producers track an observation date directly, so ``last_date`` is
    already comparable. A few track a run timestamp or a decision date
    instead and separately record the input observation date they consumed
    (``us_paper_runtime_decision``, ``crypto_paper_runtime_decision``);
    comparing a run timestamp against a source data date would be a unit
    mismatch, so those entries name the comparable field explicitly.
    """
    for field in spec.get("source_compare_fields", ()):
        if parsed is None:
            break
        found = _extract_date(_get_nested(parsed, field))
        if found is not None:
            return found
    return last_date


def _source_lag_cycles(spec: dict, root: Path, our_date: dt.date, source_date: dt.date) -> int:
    """How many of the producer's own expected cycles the source is ahead by."""
    if spec["calendar"]["type"] == "NO_SCHEDULE":
        return (source_date - our_date).days
    return len(missed_expected_days(spec["calendar"], our_date, source_date, root))


def evaluate_file_item(spec: dict, root: Path, today: dt.date) -> dict:
    path = root / spec["path"]
    parsed = _read_json(path)
    if parsed is None:
        return {**_base_result(spec), "status": "NEVER_PRODUCED", "last_date": None, "detail": f"missing or unreadable: {spec['path']}"}

    last_date = None
    for field in spec["date_fields"]:
        last_date = _extract_date(_get_nested(parsed, field))
        if last_date is not None:
            break
    if last_date is None:
        return {
            **_base_result(spec),
            "status": "DATE_FIELD_MISSING",
            "last_date": None,
            "detail": f"none of {spec['date_fields']} parsed as a date in {spec['path']}",
        }

    gap_field = spec.get("gap_field")
    declared_gap = _get_nested(parsed, gap_field) if gap_field else None
    if not isinstance(declared_gap, int) or declared_gap < 0:
        declared_gap = None

    return _classify(spec, root, today, last_date, declared_gap=declared_gap, parsed=parsed)


def evaluate_glob_item(spec: dict, root: Path, today: dt.date) -> dict:
    last_date = _latest_glob_date(root, spec["glob"])
    if last_date is None:
        return {**_base_result(spec), "status": "NEVER_PRODUCED", "last_date": None, "detail": f"no match ever for {spec['glob']}"}
    return _classify(spec, root, today, last_date, declared_gap=None, parsed=None)


def _base_result(spec: dict) -> dict:
    return {"id": spec["id"], "label_ko": spec["label_ko"], "workflow": spec.get("workflow")}


def _classify(
    spec: dict,
    root: Path,
    today: dt.date,
    last_date: dt.date,
    declared_gap: int | None,
    parsed: dict | None = None,
) -> dict:
    """Two-axis classification.

    ``schedule_status`` is the calendar/age axis exactly as before (FRESH /
    STALE / NO_SCHEDULE_FRESH / NO_SCHEDULE_STALE). ``status`` is that
    refined by the source-side axis -- see :func:`_apply_source_axis`. For
    entries that declare no ``source_latest`` locator at all the two are
    identical, so the module keeps its original behaviour for any
    hand-supplied watchlist.
    """
    result = _classify_schedule(spec, root, today, last_date, declared_gap)
    return _apply_source_axis(spec, root, today, last_date, declared_gap, parsed, result)


def _classify_schedule(spec: dict, root: Path, today: dt.date, last_date: dt.date, declared_gap: int | None) -> dict:
    calendar = spec["calendar"]
    result = {**_base_result(spec), "last_date": last_date.isoformat()}

    if calendar["type"] == "NO_SCHEDULE":
        age_days = (today - last_date).days
        threshold = declared_gap if declared_gap is not None else spec["default_max_gap_days"]
        result["age_days"] = age_days
        result["threshold_days"] = threshold
        if age_days > threshold:
            result["status"] = "NO_SCHEDULE_STALE"
            result["detail"] = (
                f"no automated .github/workflows trigger exists for this producer; "
                f"last output is {age_days}d old (business threshold {threshold}d)"
            )
        else:
            result["status"] = "NO_SCHEDULE_FRESH"
        return result

    if declared_gap is not None:
        # A domain rule (e.g. rotation confirmation) already declares its own
        # calendar-day tolerance -- trust it over a second, independently
        # maintained opinion.
        age_days = (today - last_date).days
        result["age_days"] = age_days
        result["threshold_days"] = declared_gap
        result["status"] = "STALE" if age_days > declared_gap else "FRESH"
        result["detail"] = f"self-declared maximum_observation_gap_days={declared_gap}"
        return result

    missed = missed_expected_days(calendar, last_date, today, root)
    allowed = spec.get("allowed_missed_cycles", 1)
    result["age_days"] = (today - last_date).days
    result["missed_expected_cycles"] = len(missed)
    result["allowed_missed_cycles"] = allowed
    if missed and len(missed) > allowed:
        result["status"] = "STALE"
        result["detail"] = (
            f"missed {len(missed)} expected production day(s) since {last_date.isoformat()}: "
            f"{', '.join(day.isoformat() for day in missed)}"
        )
    else:
        result["status"] = "FRESH"
    return result


GAP_SCHEDULE_STATUSES = {"STALE", "NO_SCHEDULE_STALE"}


def _apply_source_axis(
    spec: dict,
    root: Path,
    today: dt.date,
    last_date: dt.date,
    declared_gap: int | None,
    parsed: dict | None,
    result: dict,
) -> dict:
    """Refine a schedule-only verdict with the source's own latest date.

    Age alone cannot tell "the upstream source published nothing newer"
    (nothing is wrong) from "our run looked fine but dropped what the source
    did publish" (a real incident). This is the step that separates them.
    """
    schedule_status = result["status"]
    result["schedule_status"] = schedule_status

    facts = resolve_source_latest(spec, root, today, parsed, declared_gap)
    result.update(facts)
    source_status = facts["source_latest_status"]

    if source_status == SOURCE_STATUS_NOT_DECLARED:
        # No source axis declared for this entry (hand-supplied watchlists):
        # leave the schedule verdict exactly as it was.
        return result

    our_date = _our_comparable_date(spec, parsed, last_date)
    result["our_compared_date"] = our_date.isoformat()

    # Fail-closed and asymmetric on purpose. Any source claim we hold -- even a
    # stalled one -- is still proof the source once offered that date, so it can
    # RAISE this alarm. Only a *current* claim may CLEAR one.
    if facts.get("source_latest") is not None:
        source_date = dt.date.fromisoformat(facts["source_latest"])
        lag = _source_lag_cycles(spec, root, our_date, source_date) if source_date > our_date else 0
        allowance = spec.get("source_lag_allowance", DEFAULT_SOURCE_LAG_ALLOWANCE)
        result["source_lag_cycles"] = lag
        result["source_lag_allowance"] = allowance
        if lag > allowance:
            # The loudest state in this module: a run that looked successful
            # left committed data behind what the source already offered.
            result["status"] = "COLLECTION_BEHIND_SOURCE"
            result["detail"] = (
                f"source offers {source_date.isoformat()} but we hold only {our_date.isoformat()} "
                f"({lag} expected cycle(s) behind, allowance {allowance}); "
                f"source-side date read from {facts['source_latest_field']}"
            )
            return result
        if source_status == SOURCE_STATUS_RESOLVED and schedule_status in GAP_SCHEDULE_STATUSES:
            # There is an age gap, and the source itself -- speaking currently --
            # says it has nothing newer. Informational, not an alarm.
            result["status"] = "SOURCE_NOT_YET_PUBLISHED"
            result["detail"] = (
                f"our latest {our_date.isoformat()} == source's own latest {source_date.isoformat()}; "
                f"the source has published nothing newer (source-side date from "
                f"{facts['source_latest_field']}, claim captured {facts['source_claim_date']})"
            )
            return result

    if source_status == SOURCE_STATUS_RESOLVED:
        return result

    if schedule_status in GAP_SCHEDULE_STATUSES:
        # A gap exists and we genuinely cannot say whose fault it is. Its own
        # explicit state -- never silently "fine", never silently "stale".
        result["status"] = "SOURCE_LATEST_UNKNOWN"
        if source_status == SOURCE_STATUS_UNAVAILABLE:
            result["detail"] = (
                f"{result.get('detail', 'age gap')}; no source-side latest date is committed for this "
                f"producer, so 'source is quiet' cannot be told apart from 'we dropped data'. "
                f"To resolve: {facts['source_latest_resolve_by']}"
            )
        elif source_status == SOURCE_STATUS_CLAIM_NOT_CURRENT:
            result["detail"] = (
                f"{result.get('detail', 'age gap')}; the evidence carrying this producer's source-side "
                f"latest date is itself stalled (claim captured {facts['source_claim_date']}, "
                f"read from {facts['source_latest_field']}), so its claim is co-frozen with our output "
                f"and cannot clear the alarm"
            )
        else:
            result["detail"] = (
                f"{result.get('detail', 'age gap')}; the declared source-side evidence "
                f"({facts['source_latest_field']}) could not be read"
            )
    return result


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def _weekday_set(*names: str) -> dict:
    index = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
    return {"type": "WEEKDAY_SET", "weekdays": frozenset(index[name] for name in names)}


EVERY_DAY = {"type": "EVERY_DAY"}
NO_SCHEDULE = {"type": "NO_SCHEDULE"}
KR_TRADING_DAY = {"type": "KR_TRADING_DAY"}
# free-market-data.yml / fred-dexkous-fx.yml cron '* * * 0-5' (UTC Sun-Fri
# 21:3x/21:4x) lands as KST Mon-Sat next-day; only KST Sunday is off.
US_MON_SAT_KST = _weekday_set("MON", "TUE", "WED", "THU", "FRI", "SAT")
# spdr-sector-holdings.yml cron '0 22 * * 1-5' (UTC Mon-Fri 22:00) lands as
# KST Tue-Sat next-day.
US_TUE_SAT_KST = _weekday_set("TUE", "WED", "THU", "FRI", "SAT")


def default_watchlist() -> list[dict]:
    """The watched daily producers, derived from what is actually committed
    (``data/latest_*.json`` pointers and dated observation directories),
    not a hand-typed guess. See module docstring for the calendar policy.
    """
    return [
        {
            "id": "free_market_data",
            "label_ko": "미국 FRED/Alpaca 시장 데이터 (Stage1 근거)",
            "kind": "FILE",
            "path": "data/latest_free_market_data.json",
            "date_fields": ["observed_at_utc", "us_market_reference.as_of_session_date"],
            "calendar": US_MON_SAT_KST,
            "allowed_missed_cycles": 1,
            "workflow": ".github/workflows/free-market-data.yml (cron 35 21 * * 0-5 UTC)",
            # Alpaca's own answer to "what is your newest daily bar": the
            # latest opened_at among the daily bars the provider returned.
            # It lives inside this same artifact, so while the workflow is
            # failing the claim is co-frozen and reports CLAIM_NOT_CURRENT
            # rather than self-certifying the 09-16/09-17 outage as quiet.
            "source_latest": {
                "kind": SOURCE_SELF_FIELD,
                "field": "alpaca.daily_bars[].opened_at",
                "claim_date_field": "observed_at_utc",
                "field_note": "max alpaca.daily_bars[].opened_at in data/latest_free_market_data.json (provider-returned newest daily bar)",
            },
        },
        {
            "id": "rotation_confirmation_kr",
            "label_ko": "KR 로테이션 확인 체인",
            "kind": "FILE",
            "path": "data/latest_rotation_confirmation_kr.json",
            "date_fields": ["as_of_date"],
            "gap_field": "maximum_observation_gap_days",
            "default_max_gap_days": 7,
            "calendar": KR_TRADING_DAY,
            "workflow": ".github/workflows/rotation-confirmation.yml (workflow_run <- korea-leadership-live-proof.yml)",
            # The packet names its own input: observation.sources[].path is
            # data/observations/korea_leadership_context/<date>/packet.json.
            # The newest such committed directory is therefore the newest
            # observation this rule's source actually offers it.
            "source_latest": {
                "kind": SOURCE_DATED_PATH,
                "glob": "data/observations/korea_leadership_context/*/packet.json",
                "field_note": "newest committed data/observations/korea_leadership_context/<date>/ (the input observation.sources[].path names)",
            },
        },
        {
            "id": "rotation_confirmation_us",
            "label_ko": "US 로테이션 확인 체인",
            "kind": "FILE",
            "path": "data/latest_rotation_confirmation_us.json",
            "date_fields": ["as_of_date"],
            "gap_field": "maximum_observation_gap_days",
            "default_max_gap_days": 4,
            "calendar": US_MON_SAT_KST,
            "workflow": ".github/workflows/rotation-confirmation.yml (workflow_run <- free-market-data.yml)",
            # observation.sources[].path is
            # evidence/free_market_data/derived/<date>/<revision>/manifest.json.
            "source_latest": {
                "kind": SOURCE_DATED_PATH,
                "glob": "evidence/free_market_data/derived/*/*/manifest.json",
                "field_note": "newest committed evidence/free_market_data/derived/<date>/ (the input observation.sources[].path names)",
            },
        },
        {
            "id": "rotation_confirmation_crypto",
            "label_ko": "CRYPTO 로테이션 확인 체인",
            "kind": "FILE",
            "path": "data/latest_rotation_confirmation_crypto.json",
            "date_fields": ["as_of_date"],
            "gap_field": "maximum_observation_gap_days",
            "default_max_gap_days": 2,
            "calendar": EVERY_DAY,
            "workflow": ".github/workflows/rotation-confirmation.yml (workflow_run <- crypto-breadth-capture.yml)",
            # observation.sources[].path is
            # data/observations/crypto_leadership/<date>/packet.json.
            "source_latest": {
                "kind": SOURCE_DATED_PATH,
                "glob": "data/observations/crypto_leadership/*/packet.json",
                "field_note": "newest committed data/observations/crypto_leadership/<date>/ (the input observation.sources[].path names)",
            },
        },
        {
            "id": "kr_paper_runtime_decision",
            "label_ko": "KR PAPER 런타임 판정",
            "kind": "FILE",
            "path": "data/latest_kr_paper_runtime_decision.json",
            "date_fields": ["evaluation_at", "current_observation.as_of_date"],
            "calendar": KR_TRADING_DAY,
            "allowed_missed_cycles": 1,
            "workflow": ".github/workflows/kr-paper-runtime-daily-publish.yml (cron 40 23 * * 0-4, 45 0 * * 1-5 UTC)",
            # Structural blind spot, stated rather than papered over. The KRX
            # pykrx source is referenced by digest only (source_sha256 /
            # source_manifest_sha256); no manifest carrying "the newest
            # trading session KRX actually returned" is committed anywhere in
            # this repo, and session_boundary_freshness.session_calendar is a
            # *calendar* of planned sessions, not a statement that price data
            # for them exists -- using it would just restate the calendar
            # check this module already performs.
            "source_latest": {
                "kind": SOURCE_UNAVAILABLE,
                "field_note": None,
                "resolve_by": (
                    "have kr-paper-runtime-daily-publish.yml commit the pykrx source manifest it already "
                    "hashes (source_manifest_sha256), carrying the newest trading session the source "
                    "returned -- or add source_latest_session_date to kr_paper_runtime_decision itself"
                ),
            },
        },
        {
            "id": "us_paper_runtime_decision",
            "label_ko": "US PAPER 런타임 판정",
            "kind": "FILE",
            "path": "data/latest_us_paper_runtime_decision.json",
            "date_fields": ["evaluation_at"],
            "calendar": US_MON_SAT_KST,
            "allowed_missed_cycles": 1,
            "workflow": ".github/workflows/us-paper-runtime.yml (cron 55 21 * * 0-5, 40 23 * * 0-5 UTC)",
            # Its source is free_market_data, which it names in
            # latest_source_diagnostic.source. evaluation_at is a run
            # timestamp, so the comparable date of ours is the free_market_data
            # packet date it actually read -- not when the run happened.
            "source_latest": {
                "kind": SOURCE_DATED_PATH,
                "glob": "evidence/free_market_data/derived/*/*/manifest.json",
                "field_note": "newest committed evidence/free_market_data/derived/<date>/ (the input latest_source_diagnostic.source names)",
            },
            "source_compare_fields": ["latest_source_diagnostic.source.observed_at_utc"],
        },
        {
            "id": "crypto_paper_runtime_decision",
            "label_ko": "CRYPTO PAPER 런타임 판정",
            "kind": "FILE",
            "path": "data/latest_crypto_paper_runtime_decision.json",
            "date_fields": ["evaluation_at", "current_decision_date"],
            "calendar": EVERY_DAY,
            "allowed_missed_cycles": 1,
            "workflow": ".github/workflows/crypto-paper-runtime.yml (cron 15 7 * * *, 45 8 * * * UTC)",
            # The venue states its own newest finalized daily candle in the
            # crypto breadth raw capture manifest. current_decision_date is a
            # decision date (deliberately T+1 of the observation), so compare
            # against the BREADTH observation date the packet records reading.
            "source_latest": {
                "kind": SOURCE_DATED_PATH_FIELD,
                "glob": "evidence/crypto/breadth/raw/*/_manifest.json",
                "field": "raw.ohlc[].latest_finalized_day",
                "field_note": "max raw.ohlc[].latest_finalized_day in the newest evidence/crypto/breadth/raw/<date>/_manifest.json (venue-reported newest finalized daily candle)",
            },
            "source_compare_fields": ["current_observation.axis_observations.BREADTH.observation_date"],
            # The chain is finalized-candle -> axis observation -> next-day
            # decision, so one cycle of lag is the designed steady state; two
            # is the first genuinely anomalous value.
            "source_lag_allowance": 2,
        },
        {
            "id": "crypto_leadership",
            "label_ko": "크립토 Leadership 관측 포인터",
            "kind": "GLOB",
            "glob": "data/observations/crypto_leadership/*/packet.json",
            "calendar": EVERY_DAY,
            "allowed_missed_cycles": 1,
            "workflow": "P1-CR-06 Crypto Breadth Daily Capture chain (cron 40 0 * * * UTC)",
            # Verified linkage, not a guess: sha256 of
            # evidence/crypto/breadth/raw/<D+1>/_manifest.json equals this
            # packet's lineage.manifest_sha256_by_date entry for as_of_date D
            # (checked for 2026-09-15 and 2026-09-16), so that manifest is
            # demonstrably the source document behind each dated packet.
            "source_latest": {
                "kind": SOURCE_DATED_PATH_FIELD,
                "glob": "evidence/crypto/breadth/raw/*/_manifest.json",
                "field": "raw.ohlc[].latest_finalized_day",
                "field_note": "max raw.ohlc[].latest_finalized_day in the newest evidence/crypto/breadth/raw/<date>/_manifest.json (venue-reported newest finalized daily candle)",
            },
        },
        {
            "id": "spdr_sector_holdings",
            "label_ko": "SPDR 섹터 홀딩스 매핑",
            "kind": "FILE",
            "path": "data/latest_spdr_sector_holdings.json",
            "date_fields": ["capture_date_utc"],
            "calendar": US_TUE_SAT_KST,
            "allowed_missed_cycles": 1,
            "workflow": ".github/workflows/spdr-sector-holdings.yml (cron 0 22 * * 1-5 UTC)",
            # This artifact is a pointer built from the per-ETF capture files
            # its own capture_ids name, so its input is those dated captures.
            # Same unit on both sides (capture_date_utc), which catches the
            # "captures landed but the pointer was not advanced" drop.
            # NOTE the residual gap recorded in the PR body: State Street's own
            # holdings_as_of_date IS committed inside each capture, but the
            # pointer records no as-of date, so the freshness of the holdings
            # *data* (as opposed to the capture) is not yet comparable here.
            "source_latest": {
                "kind": SOURCE_DATED_PATH,
                "glob": "evidence/spdr_sector_holdings/derived/*/XLK.json",
                "field_note": "newest committed evidence/spdr_sector_holdings/derived/<capture_date_utc>/ (the captures this pointer's capture_ids name)",
            },
        },
        {
            "id": "fred_dexkous_fx",
            "label_ko": "FRED DEXKOUS KRW/USD 환율 관측 (RULE.NAV.KRW_USD_CONVERSION_FRED_DEXKOUS.V1)",
            "kind": "GLOB",
            # No data/latest_*.json pointer exists for this producer -- only
            # dated, append-only observation evidence. That absence is itself
            # a finding: see the module docstring's "never produced" note.
            "glob": "evidence/fred_dexkous_fx/observations/*/*.captured.json",
            "calendar": US_MON_SAT_KST,
            "allowed_missed_cycles": 1,
            "workflow": ".github/workflows/fred-dexkous-fx.yml (cron 40 21 * * 0-5 UTC)",
            # The case that motivated this whole change. The collector's raw
            # captures keep landing daily in a path separate from the
            # observations, and each run's manifest states the span FRED
            # actually served: observation_date_range's end. On 2026-09-18 the
            # newest capture (2026-09-17) declares an end of 2026-09-11 --
            # identical to our newest observation. FRED's H.10 series had
            # simply published nothing newer, which is why the three-day "FX
            # observation loss" reported by hand that day did not exist.
            "source_latest": {
                "kind": SOURCE_DATED_PATH_FIELD,
                "glob": "evidence/fred_dexkous_fx/raw/*/*/manifest.json",
                "field": "observation_date_range.1",
                "field_note": "observation_date_range end in the newest evidence/fred_dexkous_fx/raw/<date>/<revision>/manifest.json (span the FRED API actually served)",
            },
        },
        {
            "id": "us_sip_daily_liquidity",
            "label_ko": "US SIP 일일 유동성 (T2/C3)",
            "kind": "FILE",
            "path": "data/latest_us_sip_daily_liquidity.json",
            "date_fields": ["generated_at_utc"],
            "calendar": NO_SCHEDULE,
            "default_max_gap_days": 10,
            "workflow": ".github/workflows/alpaca-sip-daily-bars.yml (workflow_dispatch only -- no cron)",
            # Structural blind spot, stated rather than papered over. The
            # capture records the bar window it *requested* (window.start /
            # window.end) and the last bar each symbol came back with
            # (per_symbol[].window_end), but nothing states the newest daily
            # session Alpaca had available at run time -- so both sides of the
            # comparison would come from the same read, which proves nothing.
            "source_latest": {
                "kind": SOURCE_UNAVAILABLE,
                "field_note": None,
                "resolve_by": (
                    "have alpaca-sip-daily-bars.yml record the provider's newest available daily-bar "
                    "session (the max opened_at returned for an open-ended request) into "
                    "data/latest_us_sip_daily_liquidity.json, alongside a data-date field so the pointer "
                    "is comparable to it -- generated_at_utc is a run timestamp, not an observation date"
                ),
            },
        },
        {
            "id": "korea_population_symbol_observation",
            "label_ko": "KR 전체-모집단 심볼 관측",
            "kind": "GLOB",
            # persist_packet() writes packet.json.gz (compressed) plus an
            # always-uncompressed summary.json sidecar (_summary_sidecar) --
            # glob on the sidecar so this never needs to gzip-decode.
            "glob": "data/observations/korea_population_symbol_observation/*/summary.json",
            "calendar": NO_SCHEDULE,
            "default_max_gap_days": 4,
            "workflow": "decision/korea_population_symbol_observation.py -- no .github/workflows trigger exists",
            # The summary names its own input in population.source.path
            # (data/observations/krx_global_universe/<date>/packet.json), and
            # both sides are exact-trading-date population dates, so the
            # comparison is same-unit. On 2026-09-18 the upstream universe has
            # advanced to 2026-09-16 while this observation still holds
            # 2026-09-10 -- committed data the source already offers that we
            # never took: COLLECTION_BEHIND_SOURCE, not "source is quiet".
            "source_latest": {
                "kind": SOURCE_DATED_PATH,
                "glob": "data/observations/krx_global_universe/*/packet.json",
                "field_note": "newest committed data/observations/krx_global_universe/<date>/ (the input population.source.path names)",
            },
        },
        {
            "id": "us_population_symbol_observation",
            "label_ko": "US 전체-모집단 심볼 관측",
            "kind": "GLOB",
            "glob": "data/observations/us_population_symbol_observation/*/summary.json",
            "calendar": NO_SCHEDULE,
            "default_max_gap_days": 4,
            "workflow": "decision/us_population_symbol_observation.py -- no .github/workflows trigger exists",
            # Same shape as the KR entry: population.source.path names
            # data/observations/us_global_universe/<date>/packet.json, which
            # has reached 2026-09-16 while this observation holds 2026-09-11.
            "source_latest": {
                "kind": SOURCE_DATED_PATH,
                "glob": "data/observations/us_global_universe/*/packet.json",
                "field_note": "newest committed data/observations/us_global_universe/<date>/ (the input population.source.path names)",
            },
        },
    ]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

# Something is actually wrong and someone has to act.
ALARM_STATUSES = {
    "COLLECTION_BEHIND_SOURCE", "STALE", "NEVER_PRODUCED", "NO_SCHEDULE_STALE",
    "DATE_FIELD_MISSING",
}
# There is a gap and we cannot say whose it is. Not "fine", not "stale".
UNKNOWN_STATUSES = {"SOURCE_LATEST_UNKNOWN"}
# There is a gap and the source itself accounts for it. Report, do not alarm.
INFORMATIONAL_STATUSES = {"SOURCE_NOT_YET_PUBLISHED"}

# Loudest first, so the issue body always leads with the state that means a
# successful-looking run dropped data.
STATUS_SEVERITY = {
    "COLLECTION_BEHIND_SOURCE": 0,
    "NEVER_PRODUCED": 1,
    "DATE_FIELD_MISSING": 2,
    "STALE": 3,
    "NO_SCHEDULE_STALE": 4,
    "SOURCE_LATEST_UNKNOWN": 5,
    "SOURCE_NOT_YET_PUBLISHED": 6,
    "NO_SCHEDULE_FRESH": 7,
    "FRESH": 8,
}

# Kept for the old key name; see NON_FRESH_STATUSES usage in stale_items.
NON_FRESH_STATUSES = ALARM_STATUSES | UNKNOWN_STATUSES


def _by_severity(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: (STATUS_SEVERITY.get(item["status"], 99), item["id"]))


def build_report(root: Path = ROOT, today: dt.date | None = None, watchlist: list[dict] | None = None) -> dict:
    if today is None:
        today = dt.datetime.now(tz=KST).date()
    specs = list(watchlist) if watchlist is not None else default_watchlist()
    items = []
    for spec in specs:
        if spec["kind"] == "FILE":
            items.append(evaluate_file_item(spec, root, today))
        elif spec["kind"] == "GLOB":
            items.append(evaluate_glob_item(spec, root, today))
        else:
            raise FreshnessWatchdogError(f"UNKNOWN_ITEM_KIND:{spec['kind']}")

    alarm_items = _by_severity([item for item in items if item["status"] in ALARM_STATUSES])
    unknown_items = _by_severity([item for item in items if item["status"] in UNKNOWN_STATUSES])
    informational_items = _by_severity([item for item in items if item["status"] in INFORMATIONAL_STATUSES])

    # Which producers structurally cannot answer "is the source quiet, or are
    # we broken?" -- always listed, whether or not they are fresh today, so the
    # blind spot is never silently absorbed into "fine".
    source_latest_blind_spots = [
        {
            "id": spec["id"],
            "label_ko": spec["label_ko"],
            "resolve_by": spec["source_latest"]["resolve_by"],
        }
        for spec in specs
        if (spec.get("source_latest") or {}).get("kind") == SOURCE_UNAVAILABLE
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of_kst_date": today.isoformat(),
        "items": items,
        # Everything needing attention: real alarms plus unexplained gaps.
        "stale_items": alarm_items + unknown_items,
        "alarm_items": alarm_items,
        "unknown_items": unknown_items,
        "informational_items": informational_items,
        "source_latest_blind_spots": source_latest_blind_spots,
        "all_fresh": not (alarm_items or unknown_items),
        "authority": {
            "read_only_watch": True,
            "final_regime_authorized": False,
            "capital_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _render_item(item: dict) -> list[str]:
    status = item["status"]
    if status == "COLLECTION_BEHIND_SOURCE":
        line = (
            f"- [{item['id']}] {item['label_ko']} -- 원천은 {item['source_latest']}까지 제공하는데 "
            f"우리는 {item['our_compared_date']}까지만 보유 (수집 실행은 성공한 것처럼 보였지만 "
            f"데이터가 누락됨): {item['detail']}."
        )
    elif status == "NEVER_PRODUCED":
        line = f"- [{item['id']}] {item['label_ko']} -- 한 번도 생성된 적이 없습니다 ({item['detail']})."
    elif status == "DATE_FIELD_MISSING":
        line = f"- [{item['id']}] {item['label_ko']} -- 날짜 필드를 읽을 수 없습니다 ({item['detail']})."
    elif status == "SOURCE_LATEST_UNKNOWN":
        line = (
            f"- [{item['id']}] {item['label_ko']} -- 정체는 있으나 원천 최신일을 확인할 수 없어 "
            f"'원천이 조용함'과 '우리가 놓침'을 구분할 수 없습니다 "
            f"(우리 마지막: {item['last_date']}, 사유 {item['source_latest_status']}): {item['detail']}."
        )
    elif status == "SOURCE_NOT_YET_PUBLISHED":
        line = (
            f"- [{item['id']}] {item['label_ko']} -- 원천이 아직 신규 발표를 하지 않았습니다 "
            f"(우리 최신: {item['our_compared_date']}, 원천 최신: {item['source_latest']}, "
            f"원천 주장 시점: {item['source_claim_date']}). 조치 불필요."
        )
    elif status == "NO_SCHEDULE_STALE":
        line = (
            f"- [{item['id']}] {item['label_ko']} -- 자동 스케줄이 없는 산출물이 {item['age_days']}일째 갱신되지 않았습니다 "
            f"(마지막: {item['last_date']}, 기준 {item['threshold_days']}일)."
        )
    else:
        line = (
            f"- [{item['id']}] {item['label_ko']} -- {item['age_days']}일째 정체 "
            f"(마지막: {item['last_date']}, {item['detail']})."
        )
    lines = [line]
    if item.get("workflow"):
        lines.append(f"  · producer: {item['workflow']}")
    return lines


def render_issue_body(report: dict) -> str:
    lines = [
        f"자동 점검 시각(KST 기준일): {report['as_of_kst_date']}",
        "주문·매매·자본 배분 권한은 열리지 않았습니다 (읽기 전용 관측).",
        "",
    ]

    alarm_items = report.get("alarm_items", report["stale_items"])
    unknown_items = report.get("unknown_items", [])
    informational_items = report.get("informational_items", [])

    if alarm_items:
        lines.append("■ 경보 — 조치 필요")
        for item in alarm_items:
            lines.extend(_render_item(item))
        lines.append("")

    if unknown_items:
        lines.append("■ 판별 불가 — 원천 최신일 미확보 (정상도 아니고 정체 확정도 아님)")
        for item in unknown_items:
            lines.extend(_render_item(item))
        lines.append("")

    if informational_items:
        lines.append("■ 정보 — 원천이 조용함 (경보 아님)")
        for item in informational_items:
            lines.extend(_render_item(item))
        lines.append("")

    blind_spots = report.get("source_latest_blind_spots", [])
    if blind_spots:
        lines.append("■ 구조적 한계 — 원천 최신일이 아직 커밋되지 않는 산출물")
        for spot in blind_spots:
            lines.append(f"- [{spot['id']}] {spot['label_ko']} -- 해결 방법: {spot['resolve_by']}")
        lines.append("")

    if not (alarm_items or unknown_items):
        lines.append("경보 대상 없음: 모든 감시 대상이 최신입니다.")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", default=None, help="Override the KST decision date (testing/dispatch only).")
    parser.add_argument("--format", choices=["json", "issue"], default="json")
    parser.add_argument("--check", action="store_true", help="Exit 1 if any watched item is non-fresh.")
    args = parser.parse_args(argv)

    today = dt.date.fromisoformat(args.today) if args.today else None
    report = build_report(today=today)

    if args.format == "issue":
        print(render_issue_body(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    if args.check and not report["all_fresh"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
