#!/usr/bin/env python3
"""US-DATA-1 U3 driver: replay the committed declared range in resumable chunks.

Reads only a committed ``us_replay_range_declaration/1``
(``regime/us_replay_range_declaration.py::load_declaration``). It has no date
arguments, so no start, end or sub-range can be chosen here.

Each chunk is one invocation of the population CLI exactly as it exists on main:

    python3 regime/us_historical_replay_population.py --date D1 --date D2 ... --out <chunk.json>

That module is called as a subprocess, never edited, and imported only for
``validate_population``. Its credentials come from the inherited environment
(``FRED_API_KEY``, ``ALPACA_MARKET_DATA_API_KEY``,
``ALPACA_MARKET_DATA_API_SECRET``). They are never passed on a command line,
and any child output is redacted before it is printed.

Resumable and idempotent:

* The chunk plan is a pure function of the declaration's session list and
  ``chunk_size``. Each chunk file name carries its index and the sha256 of its
  dates.
* A chunk counts as complete only when its file exists, passes the population
  module's own ``validate_population``, has ``requested_dates`` exactly equal
  to the chunk dates, and carries no provider-access failure (HTTP
  401/403/429/5xx, network error, missing credential). A chunk with such a
  failure is re-run whole on resume. That is a retry of provider access, not a
  selection over data outcomes.
* ``run`` executes pending chunks only, paced under the configured Alpaca and
  FRED per-minute limits, and stops starting chunks once its time budget is
  spent. A later run resumes from the uploaded chunk artifact.

``finalize`` works only on the full range. If any chunk is missing or
incomplete, the status is ``INCOMPLETE_RESUME_REQUIRED`` and acceptance is not
evaluated. Otherwise the chunks are merged into one population bundle, which is
re-validated. Its ``requested_dates`` must equal the declared sessions exactly.
The bundle is then passed unmodified to
``regime.market_scoped_pit_acceptance.evaluate_market_pit_acceptance("US", bundle)``.
The public summary carries statuses, counts and hashes only.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_data import us_official_session_calendar as CAL  # noqa: E402
from regime import us_replay_range_declaration as DECL  # noqa: E402


POPULATION_CLI = ROOT / "regime" / "us_historical_replay_population.py"
SUMMARY_SCHEMA = "us_regime_historical_replay_summary/1"
STATUS_COMPLETE = "EVALUATED"
STATUS_INCOMPLETE = "INCOMPLETE_RESUME_REQUIRED"

# Conservative per-date request upper bounds. Population as wired today uses
# 3 Alpaca + 5 FRED calls per date. Wired BREADTH/LEADERSHIP adds up to 15
# Alpaca calls, and one FRED call is reserved as headroom.
DEFAULT_ALPACA_REQUESTS_PER_DATE = 18
DEFAULT_FRED_REQUESTS_PER_DATE = 6
# Below the documented free-tier ceilings (Alpaca 200/min, FRED 120/min).
DEFAULT_ALPACA_RPM = 180
DEFAULT_FRED_RPM = 100
DEFAULT_CHUNK_SIZE = 40
# Provider-access failures (not data outcomes): auth refusal, rate limit, server
# error, network error, or a missing credential. A chunk carrying any of them is
# re-run whole, never accepted as complete.
PROVIDER_ACCESS_FAILURE = re.compile(
    r"HTTP_(ERROR|STATUS):(401|403|429|5\d\d)|NETWORK_ERROR"
    r"|BLOCKED_BY_(INCOMPLETE_)?(DEDICATED_MARKET_DATA|FRED)_CREDENTIAL"
)
SECRET_ENV = ("FRED_API_KEY", "ALPACA_MARKET_DATA_API_KEY", "ALPACA_MARKET_DATA_API_SECRET")


class DriverError(ValueError):
    """Replay driver invariant failed closed."""


def fail(code: str, detail: str = "") -> None:
    raise DriverError(f"{code}:{detail}" if detail else code)


_POPULATION_MODULE = None


def _population_module():
    """Load the population module once, read-only, for its validator."""
    global _POPULATION_MODULE
    if _POPULATION_MODULE is not None:
        return _POPULATION_MODULE
    import importlib.util

    spec = importlib.util.spec_from_file_location("atlas_us_replay_driver_population", POPULATION_CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _POPULATION_MODULE = module
    return module


# ---------------------------------------------------------------------------
# Planning.
# ---------------------------------------------------------------------------


def plan_chunks(sessions: list[str], chunk_size: int) -> list[dict]:
    if type(chunk_size) is not int or not 1 <= chunk_size <= 500:
        fail("CHUNK_SIZE_INVALID", str(chunk_size))
    if not sessions or sessions != sorted(set(sessions)):
        fail("SESSIONS_INVALID")
    chunks = []
    for index, offset in enumerate(range(0, len(sessions), chunk_size), start=1):
        dates = sessions[offset:offset + chunk_size]
        dates_sha = CAL.payload_sha256(dates)
        chunks.append({
            "index": index,
            "dates": dates,
            "dates_sha256": dates_sha,
            "file_name": f"chunk-{index:04d}-{dates_sha[:12]}.json",
        })
    return chunks


def has_provider_access_failure(population: dict) -> bool:
    return PROVIDER_ACCESS_FAILURE.search(json.dumps(population.get("records", []), sort_keys=True)) is not None


def chunk_state(chunk: dict, chunks_dir: Path, *, validator=None) -> str:
    """``COMPLETE``, ``ABSENT``, ``INVALID`` or ``PROVIDER_ACCESS_FAILURE``."""
    path = Path(chunks_dir) / chunk["file_name"]
    if not path.is_file():
        return "ABSENT"
    try:
        population = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "INVALID"
    validator = _population_module().validate_population if validator is None else validator
    try:
        validator(population)
    except Exception:  # any validation failure is simply "not complete"
        return "INVALID"
    if population.get("requested_dates") != chunk["dates"]:
        return "INVALID"
    if has_provider_access_failure(population):
        return "PROVIDER_ACCESS_FAILURE"
    return "COMPLETE"


def pending_chunks(plan: list[dict], chunks_dir: Path, *, validator=None) -> list[dict]:
    return [chunk for chunk in plan if chunk_state(chunk, chunks_dir, validator=validator) != "COMPLETE"]


def pace_seconds(
    date_count: int,
    elapsed_seconds: float,
    *,
    alpaca_per_date: int = DEFAULT_ALPACA_REQUESTS_PER_DATE,
    fred_per_date: int = DEFAULT_FRED_REQUESTS_PER_DATE,
    alpaca_rpm: int = DEFAULT_ALPACA_RPM,
    fred_rpm: int = DEFAULT_FRED_RPM,
) -> float:
    """Sleep needed after a chunk so its average request rate stays under both limits."""
    if min(date_count, alpaca_per_date, fred_per_date, alpaca_rpm, fred_rpm) <= 0:
        fail("PACING_INPUT_INVALID")
    needed = max(
        date_count * alpaca_per_date * 60.0 / alpaca_rpm,
        date_count * fred_per_date * 60.0 / fred_rpm,
    )
    return max(0.0, needed - elapsed_seconds)


def estimate(session_count: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict:
    alpaca = session_count * DEFAULT_ALPACA_REQUESTS_PER_DATE
    fred = session_count * DEFAULT_FRED_REQUESTS_PER_DATE
    minutes = max(alpaca / DEFAULT_ALPACA_RPM, fred / DEFAULT_FRED_RPM)
    return {
        "session_count": session_count,
        "chunks": math.ceil(session_count / chunk_size),
        "alpaca_requests_upper_bound": alpaca,
        "fred_requests_upper_bound": fred,
        "paced_minutes_lower_bound": round(minutes, 1),
    }


# ---------------------------------------------------------------------------
# Execution.
# ---------------------------------------------------------------------------


def redact(text: str, secrets: list[str]) -> str:
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return text


def _secrets_from_env(env: dict) -> list[str]:
    return [env.get(name, "") for name in SECRET_ENV]


def forbid_inside_checkout(path: Path, root: Path = ROOT) -> None:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return
    fail("WORK_DIR_INSIDE_CHECKOUT_FORBIDDEN")


def default_runner(chunk: dict, out_path: Path, env: dict) -> tuple[int, str]:
    command = [sys.executable, str(POPULATION_CLI)]
    for day in chunk["dates"]:
        command += ["--date", day]
    command += ["--out", str(out_path)]
    completed = subprocess.run(command, capture_output=True, text=True, env=env, cwd=str(ROOT))
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def run(
    declaration: dict,
    work_dir: Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    time_budget_seconds: float = 320 * 60,
    runner=None,
    validator=None,
    sleep=time.sleep,
    monotonic=time.monotonic,
    env: dict | None = None,
    log=print,
    max_attempts: int = 2,
) -> dict:
    forbid_inside_checkout(work_dir)
    env = dict(os.environ) if env is None else env
    secrets = _secrets_from_env(env)
    runner = default_runner if runner is None else runner
    chunks_dir = Path(work_dir) / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    plan = plan_chunks(declaration["range"]["sessions"], chunk_size)
    pending = pending_chunks(plan, chunks_dir, validator=validator)
    log(json.dumps({"planned_chunks": len(plan), "pending_chunks": len(pending)}, sort_keys=True))
    started = monotonic()
    executed = 0
    failures = []
    for chunk in pending:
        if monotonic() - started >= time_budget_seconds:
            log("TIME_BUDGET_REACHED: remaining chunks resume in a later run")
            break
        state = None
        for attempt in range(1, max_attempts + 1):
            chunk_started = monotonic()
            temporary = chunks_dir / f".{chunk['file_name']}.partial"
            code, output = runner(chunk, temporary, env)
            output = redact(output, secrets)
            if code == 0 and temporary.is_file():
                os.replace(temporary, chunks_dir / chunk["file_name"])
            elif temporary.exists():
                temporary.unlink()
            state = chunk_state(chunk, chunks_dir, validator=validator)
            log(json.dumps({"chunk": chunk["index"], "attempt": attempt, "exit": code, "state": state}, sort_keys=True))
            if code != 0:
                log("CHILD_OUTPUT_TAIL:\n" + "\n".join(output.splitlines()[-20:]))
            sleep(pace_seconds(len(chunk["dates"]), monotonic() - chunk_started))
            executed += 1
            if state == "COMPLETE":
                break
        if state != "COMPLETE":
            failures.append({"chunk": chunk["index"], "state": state})
            # Stop rather than keep calling a provider that is failing.
            break
    complete = sum(1 for chunk in plan if chunk_state(chunk, chunks_dir, validator=validator) == "COMPLETE")
    progress = {
        "declaration_sha256": declaration["declaration_sha256"],
        "chunk_size": chunk_size,
        "planned_chunks": len(plan),
        "complete_chunks": complete,
        "attempts_this_run": executed,
        "failures_this_run": failures,
    }
    (Path(work_dir) / "progress.json").write_bytes(CAL.canonical_bytes(progress))
    return progress


# ---------------------------------------------------------------------------
# Finalize.
# ---------------------------------------------------------------------------


def merge_populations(populations: list[dict], *, population_module=None) -> dict:
    """Merge chunk populations into one bundle in the population module's format."""
    module = _population_module() if population_module is None else population_module
    if not populations:
        fail("NO_CHUNKS")
    volatile = {"requested_dates", "records", "payload_sha256"}
    shared = {key: value for key, value in populations[0].items() if key not in volatile}
    for population in populations[1:]:
        other = {key: value for key, value in population.items() if key not in volatile}
        if CAL.canonical_json(other) != CAL.canonical_json(shared):
            fail("CHUNK_POPULATIONS_NOT_HOMOGENEOUS")
    merged = copy.deepcopy(shared)
    dates = [day for population in populations for day in population["requested_dates"]]
    if dates != sorted(set(dates)):
        fail("CHUNK_DATES_OVERLAP_OR_UNORDERED")
    merged["requested_dates"] = dates
    merged["records"] = [copy.deepcopy(record) for population in populations for record in population["records"]]
    merged["payload_sha256"] = module.payload_sha256(merged)
    module.validate_population(merged)
    return merged


def finalize(
    declaration: dict,
    work_dir: Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    validator=None,
    population_module=None,
    evaluate=None,
) -> dict:
    forbid_inside_checkout(work_dir)
    chunks_dir = Path(work_dir) / "chunks"
    plan = plan_chunks(declaration["range"]["sessions"], chunk_size)
    states = {chunk["index"]: chunk_state(chunk, chunks_dir, validator=validator) for chunk in plan}
    replay_module_sha = CAL.file_sha256(POPULATION_CLI)
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "market": "US",
        "declaration_sha256": declaration["declaration_sha256"],
        "declared_range": {
            "first_replay_date": declaration["range"]["first_replay_date"],
            "last_replay_date": declaration["range"]["last_replay_date"],
            "session_count": declaration["range"]["session_count"],
            "sessions_sha256": declaration["range"]["sessions_sha256"],
        },
        "replay_module_sha256": replay_module_sha,
        "chunk_size": chunk_size,
        "planned_chunks": len(plan),
        "complete_chunks": sum(1 for state in states.values() if state == "COMPLETE"),
        "incomplete_chunk_indexes": sorted(index for index, state in states.items() if state != "COMPLETE"),
        "status": STATUS_INCOMPLETE,
        "population_payload_sha256": None,
        "record_status_counts": None,
        "acceptance": None,
        "authority": {
            "public_summary_only": True,
            "evidence_accepted_by_this_summary": False,
            "runtime_authorized": False,
            "order_authorized": False,
            "trading_authorized": False,
            "real_authorized": False,
        },
    }
    if summary["incomplete_chunk_indexes"]:
        summary["summary_sha256"] = CAL.payload_sha256(summary)
        return summary
    populations = [
        json.loads((chunks_dir / chunk["file_name"]).read_text(encoding="utf-8")) for chunk in plan
    ]
    merged = merge_populations(populations, population_module=population_module)
    if merged["requested_dates"] != declaration["range"]["sessions"]:
        fail("MERGED_DATES_DIFFER_FROM_DECLARATION")
    if evaluate is None:
        from regime import market_scoped_pit_acceptance as MSPA

        evaluate = MSPA.evaluate_market_pit_acceptance
    result = evaluate("US", merged)
    counts: dict[str, int] = {}
    for record in merged["records"]:
        counts[record.get("status")] = counts.get(record.get("status"), 0) + 1
    summary.update({
        "status": STATUS_COMPLETE,
        "population_payload_sha256": merged["payload_sha256"],
        "record_status_counts": dict(sorted(counts.items())),
        "acceptance": {
            key: result.get(key)
            for key in (
                "market", "status", "reasons", "evaluated_date_count", "regimes_observed",
                "missing_regimes", "replay_report_sha256", "contract_version",
            )
        },
    })
    (Path(work_dir) / "merged_population.json").write_bytes(CAL.canonical_bytes(merged))
    summary["summary_sha256"] = CAL.payload_sha256(summary)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="US regime historical replay driver (declared range only)")
    parser.add_argument("command", choices=["plan", "run", "finalize"])
    parser.add_argument("--declaration", type=Path, default=DECL.DEFAULT_DECLARATION_PATH)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--time-budget-minutes", type=int, default=320)
    parser.add_argument("--summary-out", type=Path)
    args = parser.parse_args(argv)
    declaration = DECL.load_declaration(args.declaration)
    if args.command == "plan":
        print(json.dumps(estimate(declaration["range"]["session_count"], args.chunk_size), sort_keys=True))
        return 0
    if args.work_dir is None:
        fail("WORK_DIR_REQUIRED")
    if args.command == "run":
        if not 1 <= args.time_budget_minutes <= 340:
            fail("TIME_BUDGET_INVALID")
        missing = [name for name in SECRET_ENV if not os.environ.get(name, "").strip()]
        if missing:
            print(f"STOP: missing credentials: {','.join(missing)}")
            return 2
        progress = run(declaration, args.work_dir, chunk_size=args.chunk_size,
                       time_budget_seconds=args.time_budget_minutes * 60)
        print(json.dumps(progress, sort_keys=True))
        return 0 if not progress["failures_this_run"] else 4
    summary = finalize(declaration, args.work_dir, chunk_size=args.chunk_size)
    if args.summary_out is None:
        fail("SUMMARY_OUT_REQUIRED")
    forbid_inside_checkout(args.summary_out)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_bytes(CAL.canonical_bytes(summary))
    print(json.dumps({key: summary[key] for key in ("status", "complete_chunks", "planned_chunks", "acceptance")}, sort_keys=True))
    return 0 if summary["status"] == STATUS_COMPLETE else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DriverError, DECL.DeclarationError, CAL.UsSessionCalendarError) as exc:
        print(f"FATAL: {exc}")
        raise SystemExit(1)
