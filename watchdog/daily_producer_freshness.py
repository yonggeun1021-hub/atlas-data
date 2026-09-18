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
import re
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


# ---------------------------------------------------------------------------
# Run-conclusion axis ("is a non-red latest run hiding real failures?")
# ---------------------------------------------------------------------------
#
# A workflow's *latest* run conclusion can be ``skipped`` while real failures
# sit behind it, which makes every consumer of "latest run conclusion" -- the
# badge, a dashboard, the portal status tile -- read non-red while the producer
# has in fact been down. Observed 2026-09-18 on
# ``.github/workflows/paper-regime-reference.yml``:
#
#   35295228731  01:25Z  failure
#   35296380899  01:42Z  failure
#   35296682372  01:46Z  skipped   <- newest terminal state
#   35298736708  02:17Z  skipped
#
# The skips come from that workflow's job-level
# ``if: ... || github.event.workflow_run.conclusion == 'success'``: the run was
# a ``workflow_run`` trigger from an upstream that concluded ``cancelled``, so
# its single ``build`` job never ran at all.
#
# This module still performs **no network call of its own**. Run history is
# supplied as a JSON file (``--run-history``) that the workflow writes with
# ``gh api`` using the token it already has; when the file is absent or
# unreadable the axis fails closed to RUN_HISTORY_UNAVAILABLE and, critically,
# never suppresses an output-staleness alarm. Output staleness is judged purely
# from committed evidence and always stands on its own -- a producer whose
# output is stale alarms even when no run failed.

RUN_HISTORY_SCHEMA = "daily_producer_run_history/1"

TERMINAL_CONCLUSIONS = {"success", "failure"}
FAILED_CONCLUSIONS = {"failure", "cancelled", "timed_out", "startup_failure"}
DEFAULT_TERMINAL_RUN_WINDOW_HOURS = 24

RUN_STATUS_OK = "OK"
RUN_STATUS_NO_WORKFLOW = "NO_WORKFLOW"
RUN_STATUS_HISTORY_UNAVAILABLE = "RUN_HISTORY_UNAVAILABLE"
RUN_STATUS_NO_RUNS_RECORDED = "NO_RUNS_RECORDED"


def load_run_history(path: Path | None) -> dict | None:
    """Read the run-history sidecar the workflow fetched, or None.

    Fail-closed: a missing, unreadable, or wrong-schema file yields None, which
    the caller reports as RUN_HISTORY_UNAVAILABLE rather than as "runs fine".
    """
    if path is None:
        return None
    payload = _read_json(path)
    if payload is None:
        return None
    if payload.get("schema_version") != RUN_HISTORY_SCHEMA:
        return None
    workflows = payload.get("workflows")
    return payload if isinstance(workflows, dict) else None


def _parse_run_timestamp(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _sorted_runs(raw_runs: object) -> list[dict]:
    """Completed runs, newest first, with a parsed timestamp attached."""
    runs = []
    for entry in raw_runs if isinstance(raw_runs, list) else []:
        if not isinstance(entry, dict):
            continue
        created = _parse_run_timestamp(entry.get("created_at"))
        if created is None:
            continue
        runs.append({**entry, "_created": created})
    runs.sort(key=lambda run: run["_created"], reverse=True)
    return runs


def _run_label(run: dict) -> str:
    return f"run {run.get('id')} {run.get('created_at')} {run.get('conclusion')}"


def evaluate_run_conclusions(
    spec: dict,
    history: dict | None,
    now_utc: dt.datetime,
    today_is_expected: bool,
) -> dict:
    """Classify a producer's recent run conclusions.

    Returns ``run_status`` plus, when something is wrong, the candidate status
    and a detail naming the *last actual failure* rather than the skip that
    hides it.
    """
    workflow_file = spec.get("workflow_file")
    if not workflow_file:
        # Producers with no workflow at all (the population observations) have
        # no run history to read; their silence is already covered by the
        # NO_SCHEDULE axis.
        return {"run_status": RUN_STATUS_NO_WORKFLOW}
    if history is None:
        return {"run_status": RUN_STATUS_HISTORY_UNAVAILABLE}

    runs = _sorted_runs(history["workflows"].get(workflow_file))
    if not runs:
        return {"run_status": RUN_STATUS_NO_RUNS_RECORDED}

    facts: dict = {
        "run_status": RUN_STATUS_OK,
        "latest_run_conclusion": runs[0].get("conclusion"),
        "latest_run_id": runs[0].get("id"),
        "latest_run_created_at": runs[0].get("created_at"),
    }

    # Runs since the last success -- the window in which a hidden failure lives.
    since_success: list[dict] = []
    for run in runs:
        if run.get("conclusion") == "success":
            facts["last_success_run_id"] = run.get("id")
            facts["last_success_created_at"] = run.get("created_at")
            break
        since_success.append(run)

    hidden_failures = [run for run in since_success if run.get("conclusion") in FAILED_CONCLUSIONS]
    if runs[0].get("conclusion") == "skipped" and hidden_failures:
        # Report the real failure, not the skip that became the newest state.
        last_failure = hidden_failures[0]
        facts["run_status"] = "SKIPPED_OVER_FAILURE"
        facts["last_actual_failure"] = {
            "id": last_failure.get("id"),
            "created_at": last_failure.get("created_at"),
            "conclusion": last_failure.get("conclusion"),
        }
        facts["candidate_status"] = "LATEST_RUN_SKIPPED_OVER_FAILURE"
        facts["candidate_detail"] = (
            f"latest run is skipped ({_run_label(runs[0])}) but the producer has been failing since "
            f"{last_failure.get('created_at')}: last actual failure {_run_label(last_failure)}"
            + (
                f"; {len(hidden_failures)} failed run(s) since the last success "
                f"({facts.get('last_success_created_at')})"
                if facts.get("last_success_created_at")
                else f"; {len(hidden_failures)} failed run(s) and no success on record"
            )
            + ". Anything reading 'latest run conclusion' sees a non-red state."
        )
        return facts

    # Silence: an upstream-triggered producer that keeps being skipped never
    # reaches a terminal state, so nothing goes red anywhere.
    window_hours = spec.get("terminal_run_window_hours", DEFAULT_TERMINAL_RUN_WINDOW_HOURS)
    cutoff = now_utc - dt.timedelta(hours=window_hours)
    recent = [run for run in runs if run["_created"] >= cutoff]
    terminal = [run for run in recent if run.get("conclusion") in TERMINAL_CONCLUSIONS]
    facts["runs_in_window"] = len(recent)
    facts["terminal_runs_in_window"] = len(terminal)
    facts["terminal_run_window_hours"] = window_hours

    if runs[0].get("conclusion") in FAILED_CONCLUSIONS:
        # Recorded, not alarmed. A plain failing latest run is already visibly
        # red on every badge and dashboard -- it is not a blindness case -- and
        # whether it matters is decided by the output-staleness and
        # classification axes, which do not depend on run metadata at all. But
        # reporting it as run_status OK would be simply untrue.
        facts["run_status"] = "LATEST_RUN_FAILED"
        facts["failed_runs_since_last_success"] = len(hidden_failures)
        return facts

    if not terminal and today_is_expected:
        facts["run_status"] = "SILENT_NO_TERMINAL_RUN"
        facts["candidate_status"] = "PRODUCER_SILENT_NO_TERMINAL_RUN"
        non_terminal = ", ".join(sorted({str(run.get("conclusion")) for run in recent})) or "none"
        facts["candidate_detail"] = (
            f"no run reached success or failure in the last {window_hours}h "
            f"({len(recent)} run(s) recorded, conclusions: {non_terminal}); an upstream stall keeps "
            f"this producer silent with no red state anywhere"
        )
    return facts


# ---------------------------------------------------------------------------
# Classification axis ("the pointer exists -- but is the state available?")
# ---------------------------------------------------------------------------
#
# A pointer being present and dated today does not mean the state downstream
# rules depend on is available. ``.github/workflows/crypto-regime-refresh-watchdog.yml``
# checks only that ``data/latest_crypto_regime_refresh_status.json`` carries
# today's 5/5 current-reference pointer, so it reports green while
# ``official_decision.classification_status`` is a WAIT_* state and
# ``runtime_regime`` is UNKNOWN because a required input is missing. Issue #511
# has been open since 2026-08-31 on exactly that. This module therefore keys on
# the producer's own declared *classification status*, never on pointer
# presence -- and it does not try to fix that other workflow.
#
# Fail-closed in both directions that matter: a status nobody has declared is
# an alarm (a new unrecognised state must not pass silently), and a status the
# repo has deliberately parked as pending ratification is reported without
# alarming, so a designed long-running state never becomes daily noise.

CLASS_STATUS_NOT_DECLARED = "NOT_DECLARED"
CLASS_STATUS_AVAILABLE = "AVAILABLE"
CLASS_STATUS_EXPECTED_UNAVAILABLE = "EXPECTED_UNAVAILABLE"
CLASS_STATUS_UNAVAILABLE = "UNAVAILABLE"
CLASS_STATUS_UNDECLARED_VALUE = "UNDECLARED_STATUS_VALUE"
CLASS_STATUS_FIELD_MISSING = "STATUS_FIELD_MISSING"


def _classification_entries(spec: dict, parsed: dict) -> list[tuple[str, object, object]]:
    """(scope label, status value, missing-inputs value) per classified scope.

    ``per_market_field`` fans out over a list of per-market objects so each
    market is judged on its own status rather than on a whole-file summary.
    """
    locator = spec["classification"]
    per_market = locator.get("per_market_field")
    if per_market:
        markets = _get_nested(parsed, per_market)
        entries = []
        for market in markets if isinstance(markets, list) else []:
            if not isinstance(market, dict):
                continue
            label = str(market.get(locator.get("market_label_field", "market"), "?"))
            entries.append((
                label,
                _get_nested(market, locator["status_field"]),
                _get_nested(market, locator["missing_inputs_field"]) if locator.get("missing_inputs_field") else None,
            ))
        return entries
    return [(
        "ALL",
        _get_nested(parsed, locator["status_field"]),
        _get_nested(parsed, locator["missing_inputs_field"]) if locator.get("missing_inputs_field") else None,
    )]


def evaluate_classification(spec: dict, parsed: dict | None) -> dict:
    locator = spec.get("classification")
    if locator is None:
        return {"classification_status": CLASS_STATUS_NOT_DECLARED}
    if parsed is None:
        return {"classification_status": CLASS_STATUS_FIELD_MISSING}

    entries = _classification_entries(spec, parsed)
    if not entries:
        return {"classification_status": CLASS_STATUS_FIELD_MISSING}

    available = set(locator["available_statuses"])
    expected_unavailable = set(locator.get("expected_unavailable_statuses", ()))
    failure_markers = tuple(locator.get("failure_markers", ()))

    observed: dict[str, str] = {}
    problems: list[str] = []
    parked: list[str] = []
    for label, status, missing_inputs in entries:
        observed[label] = status if isinstance(status, str) else None
        missing_list = [str(item) for item in missing_inputs] if isinstance(missing_inputs, list) else []
        if not isinstance(status, str):
            problems.append(f"{label}: classification status field is absent or not a string")
            continue
        if any(marker in status for marker in failure_markers):
            problems.append(f"{label}: {status} (declared failure state)")
            continue
        if missing_list:
            problems.append(f"{label}: {status}, required input(s) missing: {', '.join(missing_list)}")
            continue
        if status in available:
            continue
        if status in expected_unavailable:
            parked.append(f"{label}: {status}")
            continue
        problems.append(f"{label}: {status} (status not declared available or expected-pending)")

    facts: dict = {"classification_observed": observed}
    if problems:
        facts["classification_status"] = CLASS_STATUS_UNAVAILABLE
        facts["candidate_status"] = "CLASSIFICATION_UNAVAILABLE"
        facts["candidate_detail"] = (
            "the pointer is present but the state downstream rules depend on is not available -- "
            + "; ".join(problems)
            + f" (read from {locator['status_field']}, not from pointer presence)"
        )
        return facts
    facts["classification_status"] = (
        CLASS_STATUS_EXPECTED_UNAVAILABLE if parked else CLASS_STATUS_AVAILABLE
    )
    if parked:
        facts["classification_parked"] = parked
    return facts


def evaluate_file_item(spec: dict, root: Path, today: dt.date,
                       run_history: dict | None = None, now_utc: dt.datetime | None = None) -> dict:
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

    return _classify(spec, root, today, last_date, declared_gap=declared_gap, parsed=parsed,
                     run_history=run_history, now_utc=now_utc)


def evaluate_glob_item(spec: dict, root: Path, today: dt.date,
                       run_history: dict | None = None, now_utc: dt.datetime | None = None) -> dict:
    last_date = _latest_glob_date(root, spec["glob"])
    if last_date is None:
        return {**_base_result(spec), "status": "NEVER_PRODUCED", "last_date": None, "detail": f"no match ever for {spec['glob']}"}
    return _classify(spec, root, today, last_date, declared_gap=None, parsed=None,
                     run_history=run_history, now_utc=now_utc)


def _base_result(spec: dict) -> dict:
    return {"id": spec["id"], "label_ko": spec["label_ko"], "workflow": spec.get("workflow")}


def _classify(
    spec: dict,
    root: Path,
    today: dt.date,
    last_date: dt.date,
    declared_gap: int | None,
    parsed: dict | None = None,
    run_history: dict | None = None,
    now_utc: dt.datetime | None = None,
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
    result = _apply_source_axis(spec, root, today, last_date, declared_gap, parsed, result)
    # The source axis is the only one allowed to DOWNGRADE (a gap the source
    # itself accounts for). The run and classification axes may only escalate,
    # so an output-staleness alarm can never be talked out of by run metadata.
    result = _apply_escalating_axis(result, evaluate_classification(spec, parsed))
    result = _apply_escalating_axis(
        result,
        evaluate_run_conclusions(
            spec,
            run_history,
            now_utc if now_utc is not None else dt.datetime.now(tz=dt.timezone.utc),
            # A producer with no schedule has no expected day, so "no terminal
            # run today" is not a finding for it -- its silence is already the
            # NO_SCHEDULE axis's job.
            spec["calendar"]["type"] != "NO_SCHEDULE"
            and is_expected_production_day(spec["calendar"], today, root),
        ),
    )
    return result


def _apply_escalating_axis(result: dict, facts: dict) -> dict:
    """Merge an axis's facts, replacing ``status`` only if strictly louder."""
    candidate = facts.pop("candidate_status", None)
    detail = facts.pop("candidate_detail", None)
    result.update(facts)
    if candidate is None:
        return result
    current_severity = STATUS_SEVERITY.get(result["status"], 99)
    if STATUS_SEVERITY.get(candidate, 99) < current_severity:
        result["status"] = candidate
        result["detail"] = detail
    else:
        # Kept visible even when it does not become the headline status, so a
        # louder finding never hides a second, independent one.
        result.setdefault("also_detected", []).append({"status": candidate, "detail": detail})
    return result


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


# ---------------------------------------------------------------------------
# Derived schedule claims ("does a committed workflow actually drive this?")
# ---------------------------------------------------------------------------
#
# A per-producer ``workflow`` description and ``calendar`` that are hardcoded
# go stale the moment somebody adds a trigger, and the watchdog then reports a
# cron-driven producer as unscheduled -- a quiet lie of exactly the kind this
# module exists to catch, told by the module itself. So the two population
# observation specs derive their claim from what is committed: a workflow that
# stages a producer's own output root is, by construction, running it.
#
# This is the same coupling PR #799 added a test for, and deriving it means the
# claim is correct whichever of the two changes lands first -- nothing to update
# by hand on either ordering.


def _workflow_bodies(root: Path) -> dict[str, str]:
    """Executable YAML of every committed workflow, comments stripped.

    Comments are dropped so a *proposed* trigger written as a comment (this
    watchdog's own dispatch-only header does exactly that) is never mistaken
    for a live one.
    """
    bodies: dict[str, str] = {}
    directory = root / ".github" / "workflows"
    if not directory.is_dir():
        return bodies
    for path in sorted(directory.glob("*.yml")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        bodies[path.name] = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
    return bodies


def committed_driver(root: Path, output_root: str) -> dict | None:
    """The committed workflow that stages ``output_root``, with its real cron.

    Returns None when nothing stages it -- the only case in which a spec may
    claim that no trigger exists.
    """
    for name, body in _workflow_bodies(root).items():
        if output_root not in body:
            continue
        crons = re.findall(r'cron:\s*["\']([^"\']+)["\']', body)
        return {"name": name, "scheduled": "schedule:" in body, "crons": crons}
    return None


def _population_observation_spec(
    root: Path, spec_id: str, label_ko: str, glob: str, script: str, source_glob: str, source_note: str
) -> dict:
    output_root = glob.split("/*", 1)[0]
    driver = committed_driver(root, output_root)
    spec: dict = {
        "id": spec_id,
        "label_ko": label_ko,
        "kind": "GLOB",
        # persist_packet() writes packet.json.gz (compressed) plus an
        # always-uncompressed summary.json sidecar -- glob the sidecar so this
        # never needs to gzip-decode.
        "glob": glob,
        "source_latest": {
            "kind": SOURCE_DATED_PATH,
            "glob": source_glob,
            "field_note": source_note,
        },
    }
    if driver is None:
        spec["calendar"] = NO_SCHEDULE
        spec["default_max_gap_days"] = 4
        spec["workflow"] = f"{script} -- no .github/workflows trigger exists"
        spec["schedule_derivation"] = "NO_COMMITTED_WORKFLOW"
        return spec

    spec["workflow_file"] = driver["name"]
    if not driver["scheduled"]:
        spec["calendar"] = NO_SCHEDULE
        spec["default_max_gap_days"] = 4
        spec["workflow"] = f".github/workflows/{driver['name']} (workflow_dispatch only -- no cron)"
        spec["schedule_derivation"] = "COMMITTED_WORKFLOW_DISPATCH_ONLY"
        return spec

    # Scheduled. Only a cron whose day-of-week field is unrestricted can be
    # read as "every day" without converting a UTC day-set into KST, which this
    # module will not guess -- a restricted set keeps EVERY_DAY but with a
    # week of grace, so it can never false-alarm on a day it cannot place.
    day_fields = {parts[4] for parts in (c.split() for c in driver["crons"]) if len(parts) >= 5}
    every_day = bool(day_fields) and day_fields == {"*"}
    spec["calendar"] = EVERY_DAY
    spec["allowed_missed_cycles"] = 1 if every_day else 7
    spec["workflow"] = (
        f".github/workflows/{driver['name']} (cron {', '.join(driver['crons'])})"
        if driver["crons"] else f".github/workflows/{driver['name']} (schedule)"
    )
    spec["schedule_derivation"] = (
        "COMMITTED_WORKFLOW_CRON" if every_day else "COMMITTED_WORKFLOW_CRON_DAY_SET_NOT_CONVERTED"
    )
    return spec


def default_watchlist(root: Path = ROOT) -> list[dict]:
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
            "workflow_file": "free-market-data.yml",
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
            "workflow_file": "rotation-confirmation.yml",
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
            "workflow_file": "rotation-confirmation.yml",
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
            "workflow_file": "rotation-confirmation.yml",
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
            "workflow_file": "kr-paper-runtime-daily-publish.yml",
            "classification": {
                "status_field": "decision_status",
                "available_statuses": ["PAPER_RUNTIME_CLASSIFIED"],
                # Parked by design, so they never become daily noise: US waits on
                # US_PAPER_RUNTIME_ADOPTION_IDENTITY / PIT_ACCEPTED / official
                # session calendar binding, CRYPTO on provisional forward
                # acceptance. Both are reported as EXPECTED_UNAVAILABLE.
                "expected_unavailable_statuses": ["BLOCKED"],
                "failure_markers": ["_FAILED", "REDERIVATION_FAILED"],
            },
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
            "workflow_file": "us-paper-runtime.yml",
            "classification": {
                "status_field": "decision_status",
                "available_statuses": ["PAPER_RUNTIME_CLASSIFIED"],
                # Parked by design, so they never become daily noise: US waits on
                # US_PAPER_RUNTIME_ADOPTION_IDENTITY / PIT_ACCEPTED / official
                # session calendar binding, CRYPTO on provisional forward
                # acceptance. Both are reported as EXPECTED_UNAVAILABLE.
                "expected_unavailable_statuses": ["BLOCKED"],
                "failure_markers": ["_FAILED", "REDERIVATION_FAILED"],
            },
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
            "workflow_file": "crypto-paper-runtime.yml",
            "classification": {
                "status_field": "decision_status",
                "available_statuses": ["PAPER_RUNTIME_CLASSIFIED"],
                # Parked by design, so they never become daily noise: US waits on
                # US_PAPER_RUNTIME_ADOPTION_IDENTITY / PIT_ACCEPTED / official
                # session calendar binding, CRYPTO on provisional forward
                # acceptance. Both are reported as EXPECTED_UNAVAILABLE.
                "expected_unavailable_statuses": ["BLOCKED"],
                "failure_markers": ["_FAILED", "REDERIVATION_FAILED"],
            },
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
            "workflow_file": "crypto-breadth-capture.yml",
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
            "workflow_file": "spdr-sector-holdings.yml",
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
            "workflow_file": "fred-dexkous-fx.yml",
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
            "workflow_file": "alpaca-sip-daily-bars.yml",
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
        # Both population observation specs derive their schedule claim from
        # what is committed rather than hardcoding it -- see
        # _population_observation_spec. Today no workflow stages their output
        # roots, so they resolve to NO_SCHEDULE and honestly say no trigger
        # exists; the moment one does (PR #799), the same code names that
        # workflow and its cron instead, with nothing to edit by hand.
        _population_observation_spec(
            root,
            "korea_population_symbol_observation",
            "KR 전체-모집단 심볼 관측",
            "data/observations/korea_population_symbol_observation/*/summary.json",
            "decision/korea_population_symbol_observation.py",
            "data/observations/krx_global_universe/*/packet.json",
            "newest committed data/observations/krx_global_universe/<date>/ (the input population.source.path names)",
        ),
        _population_observation_spec(
            root,
            "us_population_symbol_observation",
            "US 전체-모집단 심볼 관측",
            "data/observations/us_population_symbol_observation/*/summary.json",
            "decision/us_population_symbol_observation.py",
            "data/observations/us_global_universe/*/packet.json",
            "newest committed data/observations/us_global_universe/<date>/ (the input population.source.path names)",
        ),
        {
            # Added 2026-09-18 alongside the run-conclusion axis: this is the
            # producer the skipped-over-failure incident actually happened to
            # (runs 35295228731/35296380899 failure -> 35296682372/35298736708
            # skipped), and nothing was watching it.
            "id": "paper_regime_reference",
            "label_ko": "PAPER 시장 참고 판정 (US/KR/CRYPTO)",
            "kind": "FILE",
            "path": "data/latest_paper_regime_reference.json",
            "date_fields": ["generated_at"],
            "calendar": EVERY_DAY,
            "allowed_missed_cycles": 1,
            "workflow": ".github/workflows/paper-regime-reference.yml (cron 20,50 1 * * * UTC + workflow_run from 5 upstreams)",
            "workflow_file": "paper-regime-reference.yml",
            # Its forced path is only twice a day, so an upstream stall can keep
            # it silent for ~24h with nothing red anywhere.
            "terminal_run_window_hours": 24,
            # Per-market classification: each market is judged on its own
            # declared status, never on the presence of the pointer.
            "classification": {
                "per_market_field": "markets",
                "market_label_field": "market",
                "status_field": "classification_status",
                "available_statuses": ["PAPER_REFERENCE_CLASSIFIED"],
                "failure_markers": ["_FAILED", "REDERIVATION_FAILED"],
            },
            # Structural blind spot: its three declared inputs are
            # data/latest_*.json pointers, not dated directories, so there is no
            # dated source-side latest to read. Note the divergence this would
            # expose -- markets[].as_of_date is US 2026-09-15 / KR 2026-09-10 /
            # CRYPTO 2026-09-17 in the committed pointer.
            "source_latest": {
                "kind": SOURCE_UNAVAILABLE,
                "field_note": None,
                "resolve_by": (
                    "record each input's own as-of date per market in data/latest_paper_regime_reference.json "
                    "(it already records sources[].path and sha256, but no input date), so a single market "
                    "falling behind its own source is detectable instead of only the whole-file generated_at"
                ),
            },
        },
        {
            # The issue #511 shape. crypto-regime-refresh-watchdog.yml checks
            # only that this file carries today's 5/5 *current_reference*
            # pointer, so it reports green while official_decision's own
            # classification_status is a WAIT_* state and runtime_regime is
            # UNKNOWN because a required axis is missing. Keyed on the
            # classification status, never on pointer presence. That other
            # workflow is deliberately not modified here -- its bytes are
            # referenced from config/regime_source_owner_registry_v2.json's
            # markets.CRYPTO.status_owner.workflow_path.
            "id": "crypto_regime_refresh_status",
            "label_ko": "크립토 시장판정 갱신 상태 (issue #511)",
            "kind": "FILE",
            "path": "data/latest_crypto_regime_refresh_status.json",
            "date_fields": ["current_reference.as_of_date"],
            "calendar": EVERY_DAY,
            "allowed_missed_cycles": 1,
            "workflow": ".github/workflows/paper-regime-reference.yml (writes this pointer; cron 20,50 1 * * * UTC)",
            "workflow_file": "paper-regime-reference.yml",
            "terminal_run_window_hours": 24,
            "classification": {
                "status_field": "official_decision.classification_status",
                "missing_inputs_field": "official_decision.coverage.missing_axes",
                "available_statuses": ["CLASSIFIED", "OFFICIAL_DECISION_CLASSIFIED"],
                "failure_markers": ["_FAILED", "REDERIVATION_FAILED"],
            },
            "source_latest": {
                "kind": SOURCE_DATED_PATH,
                "glob": "data/observations/crypto_recent_reference/*/packet.json",
                "field_note": "newest committed data/observations/crypto_recent_reference/<date>/ (the CURRENT_REFERENCE input sources[].path names)",
            },
        },
    ]


# ---------------------------------------------------------------------------
# Declared non-coverage ("what this watchdog cannot see, said out loud")
# ---------------------------------------------------------------------------
#
# This module can only observe what is committed to a repository. A producer
# that runs outside both repositories and writes nothing into either is
# invisible to it, and the honest response is to say so in a place that cannot
# be mistaken for coverage -- not to add a watchlist spec that would either sit
# permanently at NEVER_PRODUCED or silently watch a path nobody writes.
#
# Each entry names the producer, the incident that proved the gap, why this
# module cannot see it, and what would actually catch it. An entry graduates
# into default_watchlist() the moment it starts committing a receipt.

DECLARED_NON_COVERAGE = [
    {
        "id": "atlas_kis_market_poll",
        "label_ko": "KIS KRX 호가/분봉 수집 (Ubuntu atlas-kis-market-poll.service)",
        "incident": (
            "2026-09-10 09:00 KST through 2026-09-18 every run failed -- 84 runs a day for 7 "
            "trading days -- with no alarm anywhere. The systemd timer fired normally the whole "
            "time; only the service died (first a container wrapper stopped supplying "
            "ATLAS_CANDLE_REQUESTS_URL, then fetchQuote() required an output.stck_bsop_date field "
            "that the KIS inquire-price response does not contain). KRX 1-minute bars for 6 "
            "trading days are permanently unrecoverable."
        ),
        "why_not_observable": (
            "Nothing this service writes reaches either repository, so there is no committed "
            "artifact whose staleness could be measured. Verified against both: the string "
            "'market poll' appears nowhere in atlas-data or atlas-private-evidence (no unit file, "
            "config, or evidence root); there is no KR minute/intraday evidence root in either "
            "repo (atlas-private-evidence has us_minute_evidence/ but no KR equivalent); and the "
            "one KR price path that IS committed, atlas-private-evidence price_history/KR/, is "
            "KRX *daily* data from the public data-dbg.krx.co.kr bydd_trd endpoints "
            "(schema price_history_session/1, keyed by bas_dd) which kept capturing normally "
            "throughout the outage -- which is precisely why nothing went red."
        ),
        "would_be_caught_by": [
            "Making it observable at all (then this module covers it): have the service, or a thin "
            "wrapper, commit a per-run receipt in the shape this repo already uses for out-of-band "
            "captures -- data/operations/<producer>_runs/<date>/run-<id>-attempt-<n>.json with "
            "authority 'operations_telemetry_only' -- carrying the session date covered and the "
            "rows written. Staleness of that receipt against the KRX trading calendar is then a "
            "first-class spec here, and the 'schedule looks healthy while the work never happens' "
            "shape is exactly what the existing axes already detect.",
            "Server-side, and needed regardless: the timer was healthy while the unit failed, so "
            "watching the timer proves nothing. systemd's own OnFailure= handler on "
            "atlas-kis-market-poll.service (or WatchdogSec= with sd_notify) is the mechanism that "
            "fires on the unit's Result=, and it is outside both repositories and outside this "
            "module.",
            "The specific bug class: fetchQuote() requiring a response field that never exists is a "
            "contract failure, catchable before deploy by a committed sample of the KIS "
            "inquire-price response plus a test asserting every field the code requires is present "
            "in it.",
        ],
    },
]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

# Something is actually wrong and someone has to act.
ALARM_STATUSES = {
    "COLLECTION_BEHIND_SOURCE", "STALE", "NEVER_PRODUCED", "NO_SCHEDULE_STALE",
    "DATE_FIELD_MISSING", "LATEST_RUN_SKIPPED_OVER_FAILURE",
    "PRODUCER_SILENT_NO_TERMINAL_RUN", "CLASSIFICATION_UNAVAILABLE",
}
# There is a gap and we cannot say whose it is. Not "fine", not "stale".
UNKNOWN_STATUSES = {"SOURCE_LATEST_UNKNOWN"}
# There is a gap and the source itself accounts for it. Report, do not alarm.
INFORMATIONAL_STATUSES = {"SOURCE_NOT_YET_PUBLISHED"}

# Loudest first, so the issue body always leads with the state that means a
# successful-looking run dropped data.
STATUS_SEVERITY = {
    "COLLECTION_BEHIND_SOURCE": 0,
    # A non-red latest run hiding real failures is the next loudest, because
    # every "is it green?" surface is actively reporting the wrong answer.
    "LATEST_RUN_SKIPPED_OVER_FAILURE": 1,
    "CLASSIFICATION_UNAVAILABLE": 2,
    "PRODUCER_SILENT_NO_TERMINAL_RUN": 3,
    "NEVER_PRODUCED": 4,
    "DATE_FIELD_MISSING": 5,
    "STALE": 6,
    "NO_SCHEDULE_STALE": 7,
    "SOURCE_LATEST_UNKNOWN": 8,
    "SOURCE_NOT_YET_PUBLISHED": 9,
    "NO_SCHEDULE_FRESH": 10,
    "FRESH": 11,
}

# Kept for the old key name; see NON_FRESH_STATUSES usage in stale_items.
NON_FRESH_STATUSES = ALARM_STATUSES | UNKNOWN_STATUSES


def _by_severity(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: (STATUS_SEVERITY.get(item["status"], 99), item["id"]))


def build_report(root: Path = ROOT, today: dt.date | None = None, watchlist: list[dict] | None = None,
                 run_history: dict | None = None, now_utc: dt.datetime | None = None) -> dict:
    if today is None:
        today = dt.datetime.now(tz=KST).date()
    specs = list(watchlist) if watchlist is not None else default_watchlist(root)
    items = []
    for spec in specs:
        if spec["kind"] == "FILE":
            items.append(evaluate_file_item(spec, root, today, run_history, now_utc))
        elif spec["kind"] == "GLOB":
            items.append(evaluate_glob_item(spec, root, today, run_history, now_utc))
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

    # Producers whose newest run failed outright. Recorded rather than alarmed
    # (a red badge is already visible), but never left looking like "OK".
    failing_runs = [
        {
            "id": item["id"],
            "label_ko": item["label_ko"],
            "latest_run_id": item.get("latest_run_id"),
            "latest_run_created_at": item.get("latest_run_created_at"),
            "latest_run_conclusion": item.get("latest_run_conclusion"),
            "failed_runs_since_last_success": item.get("failed_runs_since_last_success"),
        }
        for item in items
        if item.get("run_status") == "LATEST_RUN_FAILED"
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
        "producers_with_failing_runs": failing_runs,
        # Stated so the absence of an alarm for these is never read as
        # coverage. See DECLARED_NON_COVERAGE.
        "declared_non_coverage": DECLARED_NON_COVERAGE,
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
    elif status == "LATEST_RUN_SKIPPED_OVER_FAILURE":
        failure = item.get("last_actual_failure", {})
        line = (
            f"- [{item['id']}] {item['label_ko']} -- 최신 run 이 skipped 라서 배지·대시보드·포털은 "
            f"빨강이 아니지만 실제로는 실패 중입니다. 마지막 실제 실패: run {failure.get('id')} "
            f"{failure.get('created_at')} ({failure.get('conclusion')}). {item['detail']}"
        )
    elif status == "PRODUCER_SILENT_NO_TERMINAL_RUN":
        line = (
            f"- [{item['id']}] {item['label_ko']} -- 상류 정체로 계속 skip 되어 "
            f"{item.get('terminal_run_window_hours')}시간 동안 성공도 실패도 기록되지 않았습니다 "
            f"(어디에도 빨강이 없습니다): {item['detail']}"
        )
    elif status == "CLASSIFICATION_UNAVAILABLE":
        line = (
            f"- [{item['id']}] {item['label_ko']} -- 포인터는 최신이지만 시장판정 자체가 "
            f"사용 불가입니다 (포인터 존재가 아니라 분류 상태로 판정): {item['detail']}"
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
    # A louder finding must not hide a second, independent one.
    for extra in item.get("also_detected", []):
        lines.append(f"  · 추가 감지 [{extra['status']}]: {extra['detail']}")
    if item.get("run_status") in (RUN_STATUS_HISTORY_UNAVAILABLE, RUN_STATUS_NO_RUNS_RECORDED):
        lines.append(
            f"  · run 이력 미확보 ({item['run_status']}) — run 결론 축은 판정하지 않았고, "
            f"산출물 정체 판정은 그와 무관하게 그대로 유지됩니다."
        )
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

    failing = report.get("producers_with_failing_runs", [])
    if failing:
        lines.append("■ 참고 — 최신 run 이 실패로 끝난 산출물 (배지에 이미 빨강으로 보이므로 경보로 올리지 않음)")
        for entry in failing:
            lines.append(
                f"- [{entry['id']}] {entry['label_ko']} -- 최신 run {entry['latest_run_id']} "
                f"{entry['latest_run_created_at']} = {entry['latest_run_conclusion']} "
                f"(마지막 성공 이후 실패 {entry['failed_runs_since_last_success']}건). "
                f"산출물 정체 여부는 run 결론과 무관하게 별도로 판정됩니다."
            )
        lines.append("")

    non_coverage = report.get("declared_non_coverage", [])
    if non_coverage:
        lines.append("■ 이 감시가 볼 수 없는 산출물 — 경보가 없다는 것이 정상이라는 뜻은 아닙니다")
        for entry in non_coverage:
            lines.append(f"- [{entry['id']}] {entry['label_ko']}")
            lines.append(f"  · 감시 불가 이유: {entry['why_not_observable']}")
            for remedy in entry["would_be_caught_by"]:
                lines.append(f"  · 무엇이 잡아낼 수 있는가: {remedy}")
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
    parser.add_argument(
        "--run-history",
        default=None,
        help=(
            "Path to the run-conclusion sidecar the workflow fetched with `gh api`. This module never "
            "calls the network itself; without this file the run axis fails closed to "
            "RUN_HISTORY_UNAVAILABLE and output-staleness alarms are unaffected."
        ),
    )
    parser.add_argument(
        "--emit-workflow-files",
        action="store_true",
        help=(
            "Print the workflow filenames the watchlist needs run history for, one per line, so the "
            "workflow's fetch step reads the list from here instead of duplicating it."
        ),
    )
    args = parser.parse_args(argv)

    if args.emit_workflow_files:
        for name in sorted({spec["workflow_file"] for spec in default_watchlist() if spec.get("workflow_file")}):
            print(name)
        return 0

    today = dt.date.fromisoformat(args.today) if args.today else None
    run_history = load_run_history(Path(args.run_history) if args.run_history else None)
    report = build_report(today=today, run_history=run_history)

    if args.format == "issue":
        print(render_issue_body(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    if args.check and not report["all_fresh"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
