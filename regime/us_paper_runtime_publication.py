#!/usr/bin/env python3
"""Rebuild the US PAPER runtime decision from committed free-market-data bytes.

Mirrors the KR (PR #696) and crypto (#720/#723) publication pattern.  Each
session's capture is selected from the committed, content-addressed
``evidence/free_market_data/derived`` revisions, its Alpaca daily response and
FRED VIX response are re-derived by the unmodified collector validators, and
the pure ``regime.us_paper_runtime`` calculation runs twice; the two results
must be canonically byte-identical.  Without an active adoption identity the
decision is UNKNOWN, with explicit reasons.

Reads committed evidence only.  It calls no provider, downloads nothing, and
opens no strategy, capital, order, production, trading or REAL authority.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

from regime import us_paper_runtime as RUNTIME


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "latest_us_paper_runtime_decision.json"
DERIVED_ROOT = Path("evidence/free_market_data/derived")
EVIDENCE_ROOTS = (
    DERIVED_ROOT,
    Path("evidence/free_market_data/raw"),
    Path("evidence/free_market_data/fred/raw"),
)

# The collector's own cadence, transcribed from the committed
# .github/workflows/free-market-data.yml schedule (cron "35 21 * * 0-5", i.e.
# 21:35Z Sunday through Friday).  Collection lag is counted in cadence dates the
# collector was scheduled for and did not cover -- never in elapsed wall-clock
# days.  Saturday is not a cadence date, so a Sunday evaluation reading Friday's
# capture is zero cadence dates behind even though it is two days behind.
SOURCE_CADENCE_WORKFLOW = ".github/workflows/free-market-data.yml"
SOURCE_CADENCE_CRON = "35 21 * * 0-5"
SOURCE_CADENCE_UTC_HOUR = 21
SOURCE_CADENCE_UTC_MINUTE = 35
SOURCE_CADENCE_WEEKDAYS = (6, 0, 1, 2, 3, 4)   # cron day-of-week 0-5 == Sun..Fri, as date.weekday()
COVERAGE_MEASURE = "UNCOVERED_COLLECTOR_CADENCE_DATES_NOT_ELAPSED_WALL_CLOCK"

# Read off the committed history, not chosen.  Across every decision under
# evidence/regime/us_paper_runtime committed to date, each packet published while
# the collector's cadence was actually complete scores exactly 0 uncovered
# cadence dates -- including 2026-09-12T00:00Z and 2026-09-14T08:13Z, which read
# a Friday and a Sunday capture across a Saturday.  The only two non-zero scores
# are the two committed collector failures: 2026-09-15T22:51:56Z (2026-09-14
# uncovered) and 2026-09-18T01:28:17Z (2026-09-16 uncovered).  The maximum over
# the healthy population is therefore 0, and test_us_paper_runtime_collection_
# coverage.py recomputes that scan so the bound cannot drift away from it.
TOLERATED_UNCOVERED_CADENCE_DATES = 0


class UsPaperRuntimePublicationError(ValueError):
    """The display-only decision could not be reproduced or would escalate authority."""


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FMD = _load("atlas_us_runtime_free_market_data", "collectors/free_market_data.py")
FRED = FMD.FRED_PROVENANCE


def _code(exc: Exception) -> str:
    text = str(exc).split(":", 1)[0].strip()
    return text if RUNTIME.REASON.fullmatch(text or "") else type(exc).__name__.upper()


def capture_index(root: Path) -> list[dict]:
    """Every committed capture: content-addressed revisions, then daily manifests.

    Immutable revisions are indexed first, so a daily compatibility manifest
    holding the same capture is deduplicated onto its revision path.

    Only the fields needed for selection are kept.  A file that cannot prove
    its own packet identity is indexed as unusable rather than skipped, so an
    invalid latest capture blocks its session instead of exposing an older one.
    """
    rows, seen = [], set()
    base = Path(root) / DERIVED_ROOT
    if not base.is_dir():
        return []
    for path in sorted(base.glob("*/*/manifest.json")) + sorted(base.glob("*/manifest.json")):
        relative = path.relative_to(root).as_posix()
        try:
            packet = json.loads(path.read_bytes())
            FMD.verify_packet_self_hash(packet)
            observed = RUNTIME.instant(packet.get("observed_at_utc"), "SOURCE_OBSERVED_AT_INVALID")
            if path.parent.parent == base:
                if path.parent.name != packet["observed_at_utc"][:10]:
                    raise RUNTIME.UsPaperRuntimeError("DAILY_MANIFEST_DATE_MISMATCH")
            elif FMD.derived_revision_path(packet) != relative:
                raise RUNTIME.UsPaperRuntimeError("DERIVED_REVISION_PATH_MISMATCH")
            reference = packet.get("us_market_reference") or {}
            key = (packet["observed_at_utc"], packet["packet_sha256"])
            if key in seen:
                continue
            seen.add(key)
            rows.append({"path": relative, "observed_at": observed,
                         "session_date": reference.get("as_of_session_date"), "error": None})
        except Exception as exc:  # the collector validators fail closed with their own codes
            rows.append({"path": relative, "observed_at": None, "session_date": None, "error": _code(exc)})
    return rows


def _record(root: Path, relative: str) -> dict:
    try:
        packet = json.loads((Path(root) / relative).read_bytes())
        FMD.verify_packet_self_hash(packet)
        replay = FMD.validate_alpaca_daily_evidence(Path(root), packet)
        vix = FRED.validate_evidence(Path(root), packet["fred"]["evidence"], decision_at=packet["observed_at_utc"])
    except Exception as exc:
        return {"error": _code(exc), "revision_path": relative}
    return {
        "revision_path": relative,
        "schema_version": packet.get("schema_version"),
        "packet_sha256": packet["packet_sha256"],
        "observed_at_utc": packet["observed_at_utc"],
        "session_date": (packet.get("us_market_reference") or {}).get("as_of_session_date"),
        "raw_rederivation": "ALPACA_DAILY_AND_FRED_VIX_RAW_REDERIVED",
        "alpaca_daily_raw_response_sha256": replay["raw_response_sha256"],
        "vix_evidence": {"captured_at_utc": vix["captured_at_utc"], "observation": vix["observation"],
                         "raw_response_sha256": vix["pointer"]["raw_response_sha256"]},
        "reference_input": {
            "us_market_reference": packet.get("us_market_reference"),
            "fred": {k: v for k, v in packet["fred"].items() if k != "evidence"},
            "fred_liquidity": packet.get("fred_liquidity"),
        },
    }


def _path_day(row: dict) -> str:
    return Path(row["path"]).parts[3]


def select_session_record(root: Path, index: list[dict], session: dict, now: dt.datetime) -> dict:
    """Latest capture for the session inside its window.

    An unreadable capture committed on or after the latest usable one, up to the
    window end, blocks the session: it may be the latest fetch, and an earlier
    capture is never substituted for it.
    """
    window = [row for row in index if row["observed_at"] is not None
              and session["close_at"] <= row["observed_at"] < session["expires_at"]
              and row["observed_at"] <= now]
    matching = [row for row in window if row["session_date"] == session["date"].isoformat()]
    if not matching:
        return {"error": "SOURCE_NOT_ADVANCED_EXPECTED_SESSION" if window else "SESSION_SOURCE_MISSING"}
    latest = max(matching, key=lambda row: (row["observed_at"], row["path"]))
    last_day = min(now, session["expires_at"]).date().isoformat()
    blocking = sorted(row["path"] for row in index if row["error"] is not None
                      and latest["observed_at"].date().isoformat() <= _path_day(row) <= last_day)
    if blocking:
        return {"error": "SESSION_SOURCE_LATEST_CAPTURE_UNVERIFIABLE", "revision_path": blocking[0]}
    return _record(root, latest["path"])


def cadence_instant(day: dt.date) -> dt.datetime:
    """The collector's scheduled instant on ``day`` (whether or not it is a cadence date)."""
    return dt.datetime(day.year, day.month, day.day, SOURCE_CADENCE_UTC_HOUR,
                       SOURCE_CADENCE_UTC_MINUTE, tzinfo=dt.timezone.utc)


def cadence_date(moment: dt.datetime) -> dt.date | None:
    """Cadence date of the latest collector cron instant at or before ``moment``.

    A capture is attributed to the slot that asked for it, not to the wall-clock
    day it landed on, so a scheduled run delayed past midnight UTC still counts
    for its own cadence date.
    """
    day = moment.date()
    for _ in range(len(SOURCE_CADENCE_WEEKDAYS) + 2):
        if day.weekday() in SOURCE_CADENCE_WEEKDAYS and cadence_instant(day) <= moment:
            return day
        day -= dt.timedelta(days=1)
    return None


def cadence_coverage(index: list[dict], selected: dt.datetime | None, now: dt.datetime) -> dict:
    """Collector cadence dates between the selected capture and ``now`` that hold no capture.

    Coverage, never elapsed wall-clock.  Saturday is not a cadence date, so a
    Sunday evaluation reading Friday's capture is two days but zero cadence
    dates behind.  The evaluation's own cadence date is never counted either:
    its capture may still be in flight, which is what keeps a normal D+1 run and
    a late-but-successful collector slot out of the red.
    """
    covered = {cadence_date(row["observed_at"]) for row in index if row["observed_at"] is not None}
    selected_day = None if selected is None else cadence_date(selected)
    evaluated_day = cadence_date(now)
    uncovered: list[str] = []
    if selected_day is not None and evaluated_day is not None:
        day = selected_day + dt.timedelta(days=1)
        while day < evaluated_day:
            if day.weekday() in SOURCE_CADENCE_WEEKDAYS and day not in covered:
                uncovered.append(day.isoformat())
            day += dt.timedelta(days=1)
    current = (selected_day is not None and evaluated_day is not None
               and len(uncovered) <= TOLERATED_UNCOVERED_CADENCE_DATES)
    return {
        "measure": COVERAGE_MEASURE,
        "cadence_cron": SOURCE_CADENCE_CRON,
        "cadence_declared_in": SOURCE_CADENCE_WORKFLOW,
        "selected_capture_cadence_date": None if selected_day is None else selected_day.isoformat(),
        "evaluated_cadence_date": None if evaluated_day is None else evaluated_day.isoformat(),
        "uncovered_cadence_dates": uncovered,
        "uncovered_cadence_date_count": len(uncovered),
        "tolerated_uncovered_cadence_dates": TOLERATED_UNCOVERED_CADENCE_DATES,
        "status": RUNTIME.SOURCE_CURRENT if current else RUNTIME.COLLECTION_BEHIND_SOURCE,
    }


def latest_source_record(root: Path, index: list[dict], now: dt.datetime) -> dict:
    """Newest committed capture at or before ``now``, with its collection coverage.

    Two properties ``select_session_record`` already had and this selector did
    not.  (1) An unreadable capture committed on or after the newest usable one
    blocks instead of being silently replaced by that older capture: it may be
    the newest fetch.  (2) Every record carries ``collection_coverage``, so a
    producer that reads an older capture states which capture it read and how
    many of the collector's own cadence dates it is behind.  This selector is
    the only source path the decision has while no adoption identity binds a
    session calendar, and the chain's coverage refusal
    (``SOURCE_NOT_ADVANCED_EXPECTED_SESSION``) is therefore never reached.
    """
    usable = [row for row in index if row["observed_at"] is not None and row["observed_at"] <= now]
    coverage = cadence_coverage(index, max((row["observed_at"] for row in usable), default=None), now)
    if not usable:
        return {"error": "SESSION_SOURCE_MISSING", "collection_coverage": coverage}
    latest = max(usable, key=lambda row: (row["observed_at"], row["path"]))
    blocking = sorted(row["path"] for row in index if row["error"] is not None
                      and latest["observed_at"].date().isoformat() <= _path_day(row)
                      <= now.date().isoformat())
    if blocking:
        return {"error": "SESSION_SOURCE_LATEST_CAPTURE_UNVERIFIABLE", "revision_path": blocking[0],
                "collection_coverage": coverage}
    return {**_record(root, latest["path"]), "collection_coverage": coverage}


def collect(root: Path, evaluation_at: str, evidence_root: Path | None = None) -> tuple[dict, dict]:
    """Session records for the adoption-bound chain plus the latest capture.

    ``root`` holds the adoption identity and its bound artifacts;
    ``evidence_root`` holds the committed captures (the same checkout in
    production).
    """
    evidence_root = Path(root) if evidence_root is None else Path(evidence_root)
    now = RUNTIME.instant(evaluation_at, "EVALUATION_TIME_INVALID")
    index = capture_index(evidence_root)
    records = {}
    try:
        ratified = RUNTIME.verify_ratified_bindings()
        adoption, _ = RUNTIME.load_adoption(root, now, ratified)
        calendar = RUNTIME.load_calendar(root, adoption)
        history_last = RUNTIME.day(adoption["pit_acceptance"]["history_last_session_date"], "HISTORY_LAST_INVALID")
        plan = RUNTIME.session_plan(calendar, now, history_last)
        for session in plan["live_sessions"]:
            records[session["date"].isoformat()] = select_session_record(evidence_root, index, session, now)
    except Exception:  # the pure runtime re-derives and reports the exact gate
        records = {}
    return records, latest_source_record(evidence_root, index, now)


def evidence_class(root: Path) -> str:
    """LIVE_NATURAL only for this repository's committed, unmodified captures."""
    if Path(root).resolve() != ROOT.resolve():
        return "SYNTHETIC_OFFLINE_FIXTURE"
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", *map(str, EVIDENCE_ROOTS)],
            cwd=ROOT, capture_output=True, text=True, check=True, timeout=120,
        ).stdout
        tracked = subprocess.run(
            ["git", "ls-files", "--", str(DERIVED_ROOT)],
            cwd=ROOT, capture_output=True, text=True, check=True, timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "SYNTHETIC_OFFLINE_FIXTURE"
    if status.strip() or not tracked.strip():
        return "SYNTHETIC_OFFLINE_FIXTURE"
    return RUNTIME.LIVE_NATURAL


def build_decision(*, evaluation_at: str, code_revision: str, root: Path = ROOT,
                   evidence_root: Path | None = None) -> dict:
    klass = evidence_class(root) if evidence_root is None else "SYNTHETIC_OFFLINE_FIXTURE"
    results = []
    for _ in range(2):
        records, latest = collect(root, evaluation_at, evidence_root)
        results.append(RUNTIME.evaluate_us_paper_runtime(
            evaluation_at=evaluation_at, code_revision=code_revision, session_records=records,
            latest_source_record=latest, evidence_class=klass, root=root))
    if RUNTIME.canonical_bytes(results[0]) != RUNTIME.canonical_bytes(results[1]):
        raise UsPaperRuntimePublicationError("DETERMINISTIC_REBUILD_MISMATCH")
    result = results[0]
    authority = result.get("authority")
    if not isinstance(authority, dict) or set(authority) != set(RUNTIME.AUTHORITY_CLOSED) or any(
        value is not False for key, value in authority.items() if key != "paper_runtime_display_authorized"
    ):
        raise UsPaperRuntimePublicationError("AUTHORITY_ESCALATION")
    if authority.get("paper_runtime_display_authorized") is not result.get("runtime_decision_available"):
        raise UsPaperRuntimePublicationError("DISPLAY_AUTHORITY_MISMATCH")
    if result["runtime_decision_available"] is not (result["runtime_regime"] != "UNKNOWN"):
        raise UsPaperRuntimePublicationError("RUNTIME_AVAILABILITY_MISMATCH")
    if result["runtime_regime"] != "UNKNOWN" and not result["reasons"] == []:
        raise UsPaperRuntimePublicationError("RUNTIME_REASONS_MISMATCH")
    return copy.deepcopy(result)


def published_for_current_session(output: Path, evaluation_at: str, root: Path = ROOT) -> bool:
    """True when ``output`` already holds this session's verified, unchanged decision.

    A retry slot must not republish an unchanged decision with a new evaluation
    time.  The retained packet counts only if (1) it still rederives
    byte-for-byte from its own evaluation_at and code_revision and (2) a build
    at the new evaluation time has the same publication key and the same basis
    (everything except the evaluation clock and code revision).  A source that
    advanced after the first publication, an adoption that became effective,
    or any other semantic change therefore republishes.
    """
    try:
        raw = Path(output).read_bytes()
        packet = json.loads(raw)
        if not isinstance(packet, dict):
            return False
        rebuilt = build_decision(evaluation_at=packet["evaluation_at"], code_revision=packet["code_revision"],
                                 root=root)
        if RUNTIME.pretty_bytes(rebuilt) != raw:
            return False
        current = build_decision(evaluation_at=evaluation_at, code_revision=packet["code_revision"], root=root)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return (RUNTIME.publication_key(current) == RUNTIME.publication_key(packet)
            and current["basis_sha256"] == packet["basis_sha256"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-at", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--published-for-current-session", action="store_true",
                        help="exit 0 if --output already holds this session's verified unchanged decision, else 3")
    parser.add_argument("--publication-key", action="store_true",
                        help="print the evidence publication key of --output")
    args = parser.parse_args(argv)
    if args.publication_key:
        print(RUNTIME.publication_key(json.loads(args.output.read_bytes())))
        return 0
    if args.published_for_current_session:
        published = published_for_current_session(args.output, args.evaluation_at, args.root)
        print(json.dumps({"output": str(args.output), "published_for_current_session": published}, sort_keys=True))
        return 0 if published else 3
    expected = RUNTIME.pretty_bytes(
        build_decision(evaluation_at=args.evaluation_at, code_revision=args.code_revision, root=args.root)
    )
    if args.check:
        if not args.output.is_file() or args.output.read_bytes() != expected:
            raise UsPaperRuntimePublicationError("PUBLISHED_DECISION_BYTES_MISMATCH")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_bytes(expected)
    temporary.replace(args.output)
    packet = json.loads(expected)
    print(json.dumps({
        "output": str(args.output), "sha256": hashlib.sha256(expected).hexdigest(),
        "runtime_regime": packet["runtime_regime"], "decision_status": packet["decision_status"],
        "publication_key": RUNTIME.publication_key(packet), "reasons": packet["reasons"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
