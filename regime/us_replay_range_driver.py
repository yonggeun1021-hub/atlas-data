#!/usr/bin/env python3
"""US-DATA-1 U3 driver: replay the committed declared range in resumable chunks.

Reads only a committed ``us_replay_range_declaration/1``
(``regime/us_replay_range_declaration.py::load_declaration``), which must
rebuild byte for byte from the probe summary committed next to it. It has no
date arguments, so no start, end or sub-range can be chosen here
(``build_parser``'s option set is pinned by a test).

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
* A chunk counts as complete only when its file exists, its sidecar
  ``<file>.meta.json`` binds the file's sha256, the chunk dates and the replay
  code fingerprint, the file passes the population module's own
  ``validate_population``, has ``requested_dates`` exactly equal to the chunk
  dates, and carries no provider-access failure (HTTP 401/403/429/5xx, network
  error, missing credential). A chunk with such a failure is re-run whole on
  resume. That is a retry of provider access, not a selection over data
  outcomes. A child that exits non-zero never has its output promoted.
* ``run`` executes pending chunks only and stops starting chunks once its time
  budget is spent. A later run resumes from the uploaded chunk artifact. It
  refuses to add chunks to a directory holding chunks made by different
  replay code.

Pacing is a hard per-window bound, not an average. The population CLI inside a
chunk is unpaced, so a chunk may send all of its requests in one burst. Two
rules make every 60-second window safe anyway:

1. ``max_chunk_size`` caps a chunk so its worst-case requests (18 Alpaca and 6
   FRED per date) are at most half of each per-minute limit (Alpaca 180, FRED
   100), which gives 5 dates.
2. Consecutive chunk attempts start at least ``PACING_WINDOW_SECONDS`` (60)
   apart, measured from the previous attempt's start.

Chunks run one at a time, so a 60-second window can overlap at most the chunk
still running when it opens plus one chunk that starts inside it: at most two
chunks, hence at most each full limit.

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
PACING_WINDOW_SECONDS = 60.0
MAX_TIME_BUDGET_MINUTES = 320
# Files whose bytes determine what a chunk contains. Chunks made under
# different bytes are never merged.
REPLAY_CODE_FILES = (
    "regime/us_historical_replay_population.py",
    "collectors/free_market_data.py",
    "regime/paper_regime_reference.py",
    "config/free_market_data_contract.json",
    "config/paper_regime_reference_policy_v1.json",
)
CHUNK_META_SCHEMA = "us_regime_replay_chunk_meta/1"
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


def max_chunk_size(
    *,
    alpaca_per_date: int = DEFAULT_ALPACA_REQUESTS_PER_DATE,
    fred_per_date: int = DEFAULT_FRED_REQUESTS_PER_DATE,
    alpaca_rpm: int = DEFAULT_ALPACA_RPM,
    fred_rpm: int = DEFAULT_FRED_RPM,
) -> int:
    """Largest chunk whose worst-case requests are <= half of each limit."""
    if min(alpaca_per_date, fred_per_date, alpaca_rpm, fred_rpm) <= 0:
        fail("PACING_INPUT_INVALID")
    size = min((alpaca_rpm // 2) // alpaca_per_date, (fred_rpm // 2) // fred_per_date)
    if size < 1:
        fail("PACING_LIMIT_TOO_LOW_FOR_ONE_DATE")
    return size


DEFAULT_CHUNK_SIZE = max_chunk_size()  # 5


def plan_chunks(sessions: list[str], chunk_size: int) -> list[dict]:
    if type(chunk_size) is not int or not 1 <= chunk_size <= max_chunk_size():
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


def replay_code_fingerprint(root: Path = ROOT) -> dict:
    files = {rel: CAL.file_sha256(Path(root) / rel) for rel in REPLAY_CODE_FILES}
    return {"files": files, "sha256": CAL.payload_sha256(files)}


def meta_path(chunks_dir: Path, chunk: dict) -> Path:
    return Path(chunks_dir) / f"{chunk['file_name']}.meta.json"


def write_chunk_meta(chunks_dir: Path, chunk: dict, code_sha256: str) -> None:
    meta = {
        "schema_version": CHUNK_META_SCHEMA,
        "chunk_index": chunk["index"],
        "dates_sha256": chunk["dates_sha256"],
        "file_sha256": CAL.file_sha256(Path(chunks_dir) / chunk["file_name"]),
        "replay_code_sha256": code_sha256,
    }
    meta_path(chunks_dir, chunk).write_bytes(CAL.canonical_bytes(meta))


def read_chunk_meta(chunks_dir: Path, chunk: dict) -> dict | None:
    """The chunk's sidecar if it binds this chunk and this exact file, else None."""
    try:
        meta = json.loads(meta_path(chunks_dir, chunk).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(meta, dict)
        or meta.get("schema_version") != CHUNK_META_SCHEMA
        or meta.get("chunk_index") != chunk["index"]
        or meta.get("dates_sha256") != chunk["dates_sha256"]
        or meta.get("file_sha256") != CAL.file_sha256(Path(chunks_dir) / chunk["file_name"])
        or not isinstance(meta.get("replay_code_sha256"), str)
    ):
        return None
    return meta


def chunk_state(chunk: dict, chunks_dir: Path, *, validator=None) -> str:
    """``COMPLETE``, ``ABSENT``, ``INVALID`` or ``PROVIDER_ACCESS_FAILURE``."""
    path = Path(chunks_dir) / chunk["file_name"]
    if not path.is_file():
        return "ABSENT"
    try:
        population = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "INVALID"
    if read_chunk_meta(chunks_dir, chunk) is None:
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


def start_wait_seconds(previous_start: float | None, now: float) -> float:
    """Wait before the next chunk attempt so starts are >= one window apart."""
    if previous_start is None:
        return 0.0
    return max(0.0, PACING_WINDOW_SECONDS - (now - previous_start))


def estimate(session_count: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict:
    chunks = math.ceil(session_count / chunk_size)
    minutes = chunks * PACING_WINDOW_SECONDS / 60.0
    return {
        "session_count": session_count,
        "chunk_size": chunk_size,
        "chunks": chunks,
        "alpaca_requests_upper_bound": session_count * DEFAULT_ALPACA_REQUESTS_PER_DATE,
        "fred_requests_upper_bound": session_count * DEFAULT_FRED_REQUESTS_PER_DATE,
        "paced_minutes_lower_bound": round(minutes, 1),
        "workflow_runs_lower_bound": math.ceil(minutes / MAX_TIME_BUDGET_MINUTES),
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
    time_budget_seconds: float = MAX_TIME_BUDGET_MINUTES * 60,
    runner=None,
    validator=None,
    sleep=time.sleep,
    monotonic=time.monotonic,
    env: dict | None = None,
    log=print,
    max_attempts: int = 2,
    code_sha256: str | None = None,
) -> dict:
    forbid_inside_checkout(work_dir)
    env = dict(os.environ) if env is None else env
    secrets = _secrets_from_env(env)
    runner = default_runner if runner is None else runner
    chunks_dir = Path(work_dir) / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    plan = plan_chunks(declaration["range"]["sessions"], chunk_size)
    code_sha = replay_code_fingerprint()["sha256"] if code_sha256 is None else code_sha256
    foreign = sorted({
        meta["replay_code_sha256"]
        for chunk in plan
        if (meta := read_chunk_meta(chunks_dir, chunk)) is not None and meta["replay_code_sha256"] != code_sha
    })
    if foreign:
        fail("CHUNK_CODE_HASH_MISMATCH", f"{len(foreign)} other replay code hash(es) in work dir")
    pending = pending_chunks(plan, chunks_dir, validator=validator)
    log(json.dumps({"planned_chunks": len(plan), "pending_chunks": len(pending)}, sort_keys=True))
    started = monotonic()
    previous_start = None
    executed = 0
    failures = []
    budget_reached = False
    for chunk in pending:
        state = None
        for attempt in range(1, max_attempts + 1):
            wait = start_wait_seconds(previous_start, monotonic())
            if monotonic() + wait - started >= time_budget_seconds:
                budget_reached = True
                break
            if wait > 0:
                sleep(wait)
            previous_start = monotonic()
            temporary = chunks_dir / f".{chunk['file_name']}.partial"
            code, output = runner(chunk, temporary, env)
            output = redact(output, secrets)
            if code == 0 and temporary.is_file():
                os.replace(temporary, chunks_dir / chunk["file_name"])
                write_chunk_meta(chunks_dir, chunk, code_sha)
            elif temporary.exists():
                temporary.unlink()
            state = chunk_state(chunk, chunks_dir, validator=validator)
            log(json.dumps({"chunk": chunk["index"], "attempt": attempt, "exit": code, "state": state}, sort_keys=True))
            if code != 0:
                log("CHILD_OUTPUT_TAIL:\n" + "\n".join(output.splitlines()[-20:]))
            executed += 1
            if state == "COMPLETE":
                break
        if budget_reached:
            log("TIME_BUDGET_REACHED: remaining chunks resume in a later run")
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
    code_sha256: str | None = None,
) -> dict:
    forbid_inside_checkout(work_dir)
    chunks_dir = Path(work_dir) / "chunks"
    plan = plan_chunks(declaration["range"]["sessions"], chunk_size)
    states = {chunk["index"]: chunk_state(chunk, chunks_dir, validator=validator) for chunk in plan}
    replay_module_sha = CAL.file_sha256(POPULATION_CLI)
    code_sha = replay_code_fingerprint()["sha256"] if code_sha256 is None else code_sha256
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
        "replay_code_sha256": code_sha,
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
    chunk_code = {read_chunk_meta(chunks_dir, chunk)["replay_code_sha256"] for chunk in plan}
    if chunk_code != {code_sha}:
        # Never merge chunks produced by different replay code, or by code other
        # than the code this finalize runs (and validates) with.
        fail("CHUNK_CODE_HASH_MISMATCH", f"{len(chunk_code)} chunk code hash(es); current {code_sha[:12]}")
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


def build_parser() -> argparse.ArgumentParser:
    # No option here may select dates. test_driver_parser_accepts_no_date_or_range_option
    # pins the exact option set.
    parser = argparse.ArgumentParser(description="US regime historical replay driver (declared range only)")
    parser.add_argument("command", choices=["plan", "run", "finalize"])
    parser.add_argument("--declaration", type=Path, default=DECL.DEFAULT_DECLARATION_PATH)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--time-budget-minutes", type=int, default=MAX_TIME_BUDGET_MINUTES)
    parser.add_argument("--summary-out", type=Path)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    declaration = DECL.load_declaration(args.declaration)
    if args.command == "plan":
        print(json.dumps(estimate(declaration["range"]["session_count"], args.chunk_size), sort_keys=True))
        return 0
    if args.work_dir is None:
        fail("WORK_DIR_REQUIRED")
    if args.command == "run":
        if not 1 <= args.time_budget_minutes <= MAX_TIME_BUDGET_MINUTES:
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
