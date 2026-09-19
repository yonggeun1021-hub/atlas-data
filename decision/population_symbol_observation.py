#!/usr/bin/env python3
"""Full-population symbol observation packet (KR / US), read-only.

Every symbol of the committed source-coverage population appears exactly once
with four separately reported facts:

* ``data_observation``  -- was any session price fact observed for it?
* ``evaluability``      -- are all inputs the *existing* symbol evaluator
                           needs present (KR: confirmed close + SMA20 +
                           investor flows; US: IEX daily bars)?
* ``evaluation``        -- ``EVALUATED_BOUNDED`` (row copied byte-for-byte
                           from the bounded review the contract already
                           produces), ``EVALUATED`` (row built by the same
                           extracted ``_symbol_row`` function), or
                           ``NOT_EVALUATED`` with the exact missing inputs;
* ``formal_candidate``  -- only the existing Notion Atlas-Stage tag (through
                           ``stage_history.json``) makes a symbol a pipeline
                           subject.  This packet never promotes anything.

Missing SMA20 / flows / prices are reported, never estimated, and never
turned into a pass.  A symbol whose row cannot be built does not abort the
run: it is recorded as ``NOT_EVALUATED`` with ``ROW_BUILD_FAILED:<code>``.

Idempotency and resume: the generation id is the hash of every input file
hash plus market and session date.  Rows are produced in fixed-size chunks
under ``<work_dir>/chunks``; ``progress.json`` records completed chunks for
the current generation so an interrupted run resumes without recomputing
them.  An existing ``packet.json`` that rebuilds byte-identical is left
untouched (``verified_existing``); a mismatch fails closed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # Existing regime modules import siblings as ``from regime import ...``.
    sys.path.insert(0, str(ROOT))
# The market adapters import this module by name; make that stable no matter
# how this file itself was loaded (script, importlib, or package).
sys.modules.setdefault("population_symbol_observation", sys.modules[__name__])
CONTRACT_PATH = ROOT / "config" / "population_symbol_observation_contract.json"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_OUTPUT_ROOTS = {
    "KR": ROOT / "data" / "observations" / "korea_population_symbol_observation",
    "US": ROOT / "data" / "observations" / "us_population_symbol_observation",
}


class PopulationSymbolObservationError(ValueError):
    """A source, contract, reconciliation, or persistence claim failed closed."""


def _fail(code: str, detail: str | None = None) -> None:
    raise PopulationSymbolObservationError(code if detail is None else f"{code}:{detail}")


def load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"SOURCE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(code, f"{path}:{exc}")
    if not isinstance(value, dict):
        _fail(code, str(path))
    return value


def relative(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return Path(path).resolve().as_posix()


def source_ref(path: Path, packet_sha256: str | None = None) -> dict:
    return {"path": relative(path), "file_sha256": file_sha256(path), "packet_sha256": packet_sha256}


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = read_json(path, "CONTRACT_READ_FAILED")
    if contract.get("contract_version") != "population_symbol_observation/1":
        _fail("CONTRACT_VERSION_INVALID")
    authority = contract.get("authority")
    if not isinstance(authority, dict) or authority.get("observation_only") is not True or any(
        value is not False for key, value in authority.items() if key != "observation_only"
    ):
        _fail("CONTRACT_AUTHORITY_INVALID")
    if contract.get("promotion_by_this_packet") is not False or contract.get("estimation_of_missing_inputs") != "PROHIBITED":
        _fail("CONTRACT_BOUNDARY_INVALID")
    return contract


def utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        _fail(code, repr(value))
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def stage_snapshot(stage_history: dict) -> tuple[str, dict]:
    dates = sorted(stage_history)
    if not dates or DATE_RE.fullmatch(dates[-1]) is None or not isinstance(stage_history[dates[-1]], dict):
        _fail("STAGE_HISTORY_INVALID")
    return dates[-1], stage_history[dates[-1]]


def formal_candidate(symbol: str, stage_as_of: str, latest_stage: dict, bounded_subjects: set) -> dict:
    row = latest_stage.get(symbol)
    stage = row.get("stage") if isinstance(row, dict) else None
    return {
        "status": "PIPELINE_SUBJECT" if isinstance(stage, str) else "NOT_A_FORMAL_CANDIDATE",
        "stage": stage if isinstance(stage, str) else None,
        "stage_as_of": stage_as_of,
        "in_stage_history": isinstance(row, dict),
        "bounded_review_subject": symbol in bounded_subjects,
        "basis": "notion_atlas_stage_tag_via_stage_history_only",
        "promotion_by_this_packet": False,
    }


def symbol_row(
    *,
    symbol: str,
    name: str | None,
    membership: dict,
    data_observation: dict,
    evaluability: dict,
    evaluation: dict,
    formal: dict,
    facts: dict,
    evidence_refs: list,
    population_policy: dict,
) -> dict:
    if evaluation["status"] == "EVALUATED_BOUNDED":
        status = "EVALUATED_BOUNDED"
    elif evaluability["status"] == "EVALUABLE":
        status = evaluability["level"]
    elif data_observation["status"] == "DATA_OBSERVED":
        status = "EVALUABLE_SESSION_PRICE_ONLY" if evaluability.get("session_price_present") else "NOT_EVALUABLE"
    else:
        status = "NOT_EVALUABLE"
    return {
        "symbol": symbol,
        "name": name,
        "membership": membership,
        "observation_status": status,
        "data_observation": data_observation,
        "evaluability": evaluability,
        "evaluation": evaluation,
        "formal_candidate": formal,
        "facts": facts,
        "evidence_refs": evidence_refs,
        # Ratified population-level policy (2026-09-16 user ratification;
        # see universe/population_ratified_policy.py). Separate from, and
        # never a substitute for, the still-미정 CANDIDATE_PASS_RULE /
        # STAGE_TRANSITION_RULE -- this block grants no candidate, ranking,
        # or stage-promotion authority.
        "population_policy": population_policy,
    }


# --------------------------------------------------------------------------
# generation / chunk / resume
# --------------------------------------------------------------------------
def generation_id(market: str, session_date: str, input_refs: list) -> str:
    material = {
        "market": market,
        "session_date": session_date,
        "inputs": sorted((ref["path"], ref["file_sha256"]) for ref in input_refs),
    }
    return payload_sha256(material)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def chunk_symbols(symbols: list, size: int) -> list:
    if size <= 0:
        _fail("CHUNK_SIZE_INVALID")
    return [symbols[index:index + size] for index in range(0, len(symbols), size)]


def _chunk_key(index: int, symbols: list) -> str:
    return f"{index:04d}-{hashlib.sha256(canonical_json(symbols).encode('utf-8')).hexdigest()[:16]}"


def _load_progress(work_dir: Path, gen_id: str, contract: dict) -> dict:
    path = work_dir / "progress.json"
    if not path.is_file():
        return {"schema_version": contract["progress_schema_version"], "generation_id": gen_id, "completed": {}}
    progress = read_json(path, "PROGRESS_READ_FAILED")
    if progress.get("schema_version") != contract["progress_schema_version"]:
        _fail("PROGRESS_SCHEMA_INVALID")
    if progress.get("generation_id") != gen_id:
        # Inputs changed: earlier chunks belong to another generation and are
        # ignored (never deleted, never reused).
        return {"schema_version": contract["progress_schema_version"], "generation_id": gen_id, "completed": {},
                "superseded_generation_id": progress.get("generation_id")}
    if not isinstance(progress.get("completed"), dict):
        _fail("PROGRESS_COMPLETED_INVALID")
    return progress


def _verified_chunk(path: Path, expected_sha: str, gen_id: str, key: str, contract: dict) -> list | None:
    if not path.is_file() or file_sha256(path) != expected_sha:
        return None
    chunk = read_json(path, "CHUNK_READ_FAILED")
    if (
        chunk.get("schema_version") != contract["chunk_schema_version"]
        or chunk.get("generation_id") != gen_id
        or chunk.get("chunk_key") != key
        or not isinstance(chunk.get("rows"), list)
    ):
        return None
    unsigned = dict(chunk)
    claimed = unsigned.pop("payload_sha256", None)
    if payload_sha256(unsigned) != claimed:
        return None
    return chunk["rows"]


def run_chunks(
    *,
    market: str,
    gen_id: str,
    symbols: list,
    build_row,
    work_dir: Path,
    contract: dict,
    max_chunks: int | None = None,
    progress_hook=None,
) -> tuple[list, dict]:
    """Build rows chunk by chunk, persisting each chunk and the progress file.

    ``max_chunks`` (tests) stops after that many *newly built* chunks to
    simulate an interruption; a later call resumes from ``progress.json``.
    Returns ``(rows, resume_report)``; ``rows`` is complete only when
    ``resume_report["complete"]`` is true.
    """
    work_dir = Path(work_dir)
    chunks_dir = work_dir / "chunks"
    progress = _load_progress(work_dir, gen_id, contract)
    completed: dict = progress["completed"]
    size = contract["chunk_size"][market]
    plan = chunk_symbols(symbols, size)
    rows: list = []
    built = 0
    reused = 0
    stopped = False
    for index, chunk in enumerate(plan):
        key = _chunk_key(index, chunk)
        path = chunks_dir / f"{key}.json"
        existing = _verified_chunk(path, completed.get(key, ""), gen_id, key, contract) if key in completed else None
        if existing is not None:
            rows.extend(existing)
            reused += 1
            continue
        if max_chunks is not None and built >= max_chunks:
            stopped = True
            break
        chunk_rows = [build_row(symbol) for symbol in chunk]
        record = {
            "schema_version": contract["chunk_schema_version"],
            "generation_id": gen_id,
            "chunk_key": key,
            "chunk_index": index,
            "symbols": chunk,
            "rows": chunk_rows,
        }
        record["payload_sha256"] = payload_sha256(record)
        _atomic_write(path, canonical_json(record))
        completed[key] = file_sha256(path)
        progress["completed"] = completed
        _atomic_write(work_dir / "progress.json", json.dumps(progress, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        rows.extend(chunk_rows)
        built += 1
    report = {
        "generation_id": gen_id,
        "chunk_size": size,
        "planned_chunks": len(plan),
        "reused_chunks": reused,
        "built_chunks": built,
        "complete": not stopped and reused + built == len(plan),
        "superseded_generation_id": progress.get("superseded_generation_id"),
    }
    return rows, report


# --------------------------------------------------------------------------
# packet assembly / validation / persistence
# --------------------------------------------------------------------------
def assemble_packet(
    *,
    market: str,
    session_date: str,
    generated_at: str,
    gen_id: str,
    population: dict,
    sources: dict,
    rows: list,
    policy_undefined: list,
    resume_report: dict,
    contract: dict,
    extra: dict | None = None,
) -> dict:
    symbols = [row["symbol"] for row in rows]
    if len(symbols) != len(set(symbols)):
        _fail("DUPLICATE_SYMBOL_ROW")
    if len(rows) != population["count"]:
        _fail("POPULATION_ROW_COUNT_MISMATCH", f"{len(rows)}!={population['count']}")
    observation_counts = Counter(row["observation_status"] for row in rows)
    data_counts = Counter(row["data_observation"]["status"] for row in rows)
    evaluability_counts = Counter(row["evaluability"]["status"] for row in rows)
    evaluation_counts = Counter(row["evaluation"]["status"] for row in rows)
    formal_counts = Counter(row["formal_candidate"]["status"] for row in rows)
    not_evaluable_reasons = Counter(
        reason for row in rows if row["evaluability"]["status"] == "NOT_EVALUABLE"
        for reason in row["evaluability"]["reasons"]
    )
    entry_states = Counter(
        row["evaluation"].get("entry_state") for row in rows if row["evaluation"]["status"] != "NOT_EVALUATED"
    )
    evaluated = sum(evaluation_counts[s] for s in ("EVALUATED_BOUNDED", "EVALUATED"))
    evaluated_without_price = sum(
        1 for row in rows
        if row["evaluation"]["status"] != "NOT_EVALUATED" and row["evaluability"]["status"] != "EVALUABLE"
    )
    population_policy_counts = Counter(row["population_policy"]["status"] for row in rows)
    population_policy_rule_counts: dict = {}
    for row in rows:
        for rule, verdict in row["population_policy"]["rules"].items():
            bucket = population_policy_rule_counts.setdefault(rule, Counter())
            bucket[verdict["status"]] += 1
    passed_count = population_policy_counts["RATIFIED_POPULATION_PASS"]
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "market": market,
        "as_of_session_date": session_date,
        "generated_at": generated_at,
        "generated_at_semantics": "INPUT_SNAPSHOT_TIME_MAX_OF_SOURCE_TIMESTAMPS_NOT_WALL_CLOCK",
        "generation_id": gen_id,
        "population": population,
        "sources": sources,
        "summary": {
            "population_count": population["count"],
            "data_observed_count": data_counts["DATA_OBSERVED"],
            "evaluable_count": evaluability_counts["EVALUABLE"],
            "evaluated_count": evaluated,
            "evaluated_bounded_count": evaluation_counts["EVALUATED_BOUNDED"],
            "evaluated_without_full_inputs_count": evaluated_without_price,
            "evaluated_semantics": "ROWS_PRODUCED_BY_THE_EXISTING_SYMBOL_EVALUATOR_NOT_A_PASS_JUDGEMENT",
            "formal_candidate_count": formal_counts["PIPELINE_SUBJECT"],
            "not_evaluable_count": evaluability_counts["NOT_EVALUABLE"],
            "not_evaluable_reason_counts": dict(sorted(not_evaluable_reasons.items())),
            "entry_state_counts": {str(k): v for k, v in sorted(entry_states.items(), key=lambda i: str(i[0]))},
            "passed_count": passed_count,
            # 2026-09-16 user ratification wired six population-level rules
            # (INVESTABLE_UNIVERSE, LIQUIDITY, LISTING_DELISTING, TAXONOMY,
            # TRADABILITY, and US SOURCE_HIERARCHY -- see
            # universe/population_ratified_policy.py). This is deliberately
            # NOT the same fact as the still-미정 CANDIDATE_PASS_RULE: this
            # count is symbols meeting every wired ratified population
            # filter, never a candidate-ranking or entry-eligibility verdict.
            # A symbol whose ratified rule lacks a wired required input
            # (e.g. no KIS master, no 46-industry table, no listing-date
            # source) is RATIFIED_POPULATION_UNKNOWN for that rule, not a
            # silent pass or exclusion -- see population_policy_counts and
            # population_policy_rule_counts below for the breakdown that
            # keeps "0 because no rule exists" (the old
            # NO_RATIFIED_PASS_RULE_ZERO_IS_ABSENCE_OF_RULE semantics, no
            # longer applicable here) distinguishable from "0 because the
            # wired rules did not pass every symbol".
            "passed_semantics": "RATIFIED_POPULATION_POLICY_PASS_COUNT_SIX_RULES_20260916_NOT_CANDIDATE_PASS_RULE",
            "population_policy_counts": dict(sorted(population_policy_counts.items())),
            "population_policy_rule_counts": {
                rule: dict(sorted(counts.items())) for rule, counts in sorted(population_policy_rule_counts.items())
            },
        },
        "status_counts": {
            "observation_status": dict(sorted(observation_counts.items())),
            "data_observation": dict(sorted(data_counts.items())),
            "evaluability": dict(sorted(evaluability_counts.items())),
            "evaluation": dict(sorted(evaluation_counts.items())),
            "formal_candidate": dict(sorted(formal_counts.items())),
            "population_policy": dict(sorted(population_policy_counts.items())),
        },
        "symbols": rows,
        "policy_undefined": policy_undefined,
        "reconciliation": {
            "population_equals_rows": len(rows) == population["count"],
            "population_equals_status_sum": sum(observation_counts.values()) == population["count"],
            "evaluated_subset_of_evaluable_or_bounded": all(
                row["evaluability"]["status"] == "EVALUABLE" or row["evaluation"]["status"] == "EVALUATED_BOUNDED"
                for row in rows if row["evaluation"]["status"] != "NOT_EVALUATED"
            ),
            "duplicate_symbols": [],
            **(extra or {}),
        },
        "state_lifetime": copy.deepcopy(contract["state_lifetime"]),
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = contract or load_contract()
    if not isinstance(packet, dict) or packet.get("schema_version") != contract["output_schema_version"]:
        _fail("PACKET_SCHEMA_INVALID")
    unsigned = dict(packet)
    claimed = unsigned.pop("payload_sha256", None)
    if not isinstance(claimed, str) or payload_sha256(unsigned) != claimed:
        _fail("PACKET_PAYLOAD_SHA256_MISMATCH")
    if packet.get("authority") != contract["authority"]:
        _fail("PACKET_AUTHORITY_INVALID")
    rows = packet.get("symbols")
    population = packet.get("population") or {}
    if not isinstance(rows, list) or len(rows) != population.get("count"):
        _fail("PACKET_ROW_COUNT_INVALID")
    symbols = [row.get("symbol") for row in rows]
    if len(symbols) != len(set(symbols)):
        _fail("PACKET_DUPLICATE_SYMBOL")
    allowed = {
        "observation_status": set(contract["observation_statuses"]),
        "data_observation": set(contract["data_observation_statuses"]),
        "evaluability": set(contract["evaluability_statuses"]),
        "evaluation": set(contract["evaluation_statuses"]),
        "formal_candidate": set(contract["formal_candidate_statuses"]),
    }
    population_policy_overall_allowed = set(contract["population_policy_overall_statuses"])
    population_policy_rule_allowed = set(contract["population_policy_rule_statuses"])
    # Backward compatibility: a packet persisted before the 2026-09-16
    # ratified population-policy wiring carries no ``population_policy`` key
    # on any row at all -- that pre-existing, immutable evidence stays valid
    # under its own (older) contract semantics rather than failing closed on
    # a field that did not exist yet. A packet with the key on SOME but not
    # ALL rows is genuinely inconsistent (a homogeneous build always adds it
    # to every row) and still fails closed.
    has_population_policy = any("population_policy" in row for row in rows)
    passed_count = 0
    for row in rows:
        if not has_population_policy:
            if "population_policy" in row:
                _fail("PACKET_POPULATION_POLICY_PARTIALLY_PRESENT", str(row.get("symbol")))
        if row.get("observation_status") not in allowed["observation_status"]:
            _fail("PACKET_OBSERVATION_STATUS_INVALID", str(row.get("symbol")))
        for key in ("data_observation", "evaluability", "evaluation", "formal_candidate"):
            if not isinstance(row.get(key), dict) or row[key].get("status") not in allowed[key]:
                _fail("PACKET_ROW_STATUS_INVALID", f"{row.get('symbol')}:{key}")
        if row["formal_candidate"].get("promotion_by_this_packet") is not False:
            _fail("PACKET_PROMOTION_FLAG_INVALID", str(row.get("symbol")))
        if row["evaluation"]["status"] == "NOT_EVALUATED" and row["evaluation"].get("entry_state") is not None:
            _fail("PACKET_NOT_EVALUATED_ENTRY_STATE_INVALID", str(row.get("symbol")))
        if row["evaluability"]["status"] == "NOT_EVALUABLE" and not row["evaluability"].get("reasons"):
            _fail("PACKET_NOT_EVALUABLE_WITHOUT_REASON", str(row.get("symbol")))
        if has_population_policy:
            policy = row.get("population_policy")
            if not isinstance(policy, dict) or policy.get("status") not in population_policy_overall_allowed:
                _fail("PACKET_POPULATION_POLICY_STATUS_INVALID", str(row.get("symbol")))
            rule_verdicts = policy.get("rules")
            if not isinstance(rule_verdicts, dict) or not rule_verdicts:
                _fail("PACKET_POPULATION_POLICY_RULES_MISSING", str(row.get("symbol")))
            for rule, verdict in rule_verdicts.items():
                if not isinstance(verdict, dict) or verdict.get("status") not in population_policy_rule_allowed:
                    _fail("PACKET_POPULATION_POLICY_RULE_STATUS_INVALID", f"{row.get('symbol')}:{rule}")
            statuses = {v["status"] for v in rule_verdicts.values()}
            expected_overall = (
                "RATIFIED_POPULATION_EXCLUDED" if "UNMET" in statuses
                else "RATIFIED_POPULATION_PASS" if statuses == {"MET"}
                else "RATIFIED_POPULATION_UNKNOWN"
            )
            if policy["status"] != expected_overall:
                _fail("PACKET_POPULATION_POLICY_OVERALL_INCONSISTENT", str(row.get("symbol")))
            if policy["status"] == "RATIFIED_POPULATION_PASS":
                passed_count += 1
    summary = packet.get("summary") or {}
    expected_passed_count = passed_count if has_population_policy else 0
    if summary.get("passed_count") != expected_passed_count or summary.get("population_count") != population.get("count"):
        _fail("PACKET_SUMMARY_INVALID")
    return packet


def _packet_target(output_dir: Path) -> Path | None:
    for name in ("packet.json", "packet.json.gz"):
        candidate = Path(output_dir) / name
        if candidate.is_file():
            return candidate
    return None


def read_packet_file(path: Path) -> dict:
    path = Path(path)
    try:
        raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, EOFError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("PACKET_READ_FAILED", f"{path}:{exc}")
    if not isinstance(value, dict):
        _fail("PACKET_READ_FAILED", str(path))
    return value


def _summary_sidecar(packet: dict) -> dict:
    return {
        "schema_version": packet["schema_version"] + "#summary",
        "market": packet["market"],
        "as_of_session_date": packet["as_of_session_date"],
        "generated_at": packet["generated_at"],
        "generation_id": packet["generation_id"],
        "payload_sha256": packet["payload_sha256"],
        "population": copy.deepcopy(packet["population"]),
        "summary": copy.deepcopy(packet["summary"]),
        "status_counts": copy.deepcopy(packet["status_counts"]),
        "reconciliation": copy.deepcopy(packet["reconciliation"]),
        "policy_undefined": copy.deepcopy(packet["policy_undefined"]),
        "authority": copy.deepcopy(packet["authority"]),
    }


def persist_packet(packet: dict, output_dir: Path, *, compress: bool = False) -> dict:
    """Write the packet once per generation; verify or refuse on re-run.

    ``packet.json`` (or ``packet.json.gz`` when ``compress``) holds the full
    row set; ``summary.json`` is a small reader-facing sidecar derived from it.

    **Committed evidence is append-only; scratch space is not.**  A changed
    input yields a new ``generation_id``, and the generation hashes *rolling*
    inputs (stage history, the bounded review pointer, the KRX watchlist).  So
    a rebuild of an already-persisted session supersedes it -- which is correct
    for a scratch or rebuild output directory, and an append-only violation
    when the output directory is inside this repository.  Superseding a
    committed packet is therefore refused; a scratch directory keeps the old
    behaviour.  (Before this guard, a daily scheduled run would have rewritten
    a committed packet the first time any rolling pointer moved.)
    """
    output_dir = Path(output_dir)
    text = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    existing_path = _packet_target(output_dir)
    if existing_path is not None:
        existing = read_packet_file(existing_path)
        if existing.get("generation_id") != packet["generation_id"]:
            if inside_public_repository(output_dir):
                _fail("COMMITTED_PACKET_SUPERSEDE_REFUSED", relative(existing_path))
            outcome = "superseded_generation"
        elif existing == packet:
            return {"outcome": "verified_existing", "path": relative(existing_path), "payload_sha256": packet["payload_sha256"]}
        else:
            _fail("EXISTING_PACKET_DRIFT_OR_TAMPER", relative(existing_path))
        if existing_path.name != ("packet.json.gz" if compress else "packet.json"):
            existing_path.unlink()
    else:
        outcome = "populated"
    target = output_dir / ("packet.json.gz" if compress else "packet.json")
    if compress:
        output_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=output_dir, prefix=".tmp-", suffix=".gz")
        os.close(fd)
        try:
            with gzip.GzipFile(tmp, "wb", mtime=0) as handle:
                handle.write(text.encode("utf-8"))
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    else:
        _atomic_write(target, text)
    _atomic_write(output_dir / "summary.json", json.dumps(_summary_sidecar(packet), ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return {"outcome": outcome, "path": relative(target), "payload_sha256": packet["payload_sha256"]}


def reverify(output_dir: Path, contract: dict | None = None) -> dict:
    """Fresh-process re-read: validate the persisted packet, its self hash and the summary sidecar."""
    contract = contract or load_contract()
    target = _packet_target(output_dir)
    if target is None:
        _fail("PACKET_MISSING", str(output_dir))
    packet = validate_packet(read_packet_file(target), contract)
    summary_path = Path(output_dir) / "summary.json"
    summary_ok = summary_path.is_file() and read_json(summary_path, "SUMMARY_READ_FAILED") == _summary_sidecar(packet)
    if not summary_ok:
        _fail("SUMMARY_SIDECAR_MISMATCH", str(summary_path))
    return {
        "outcome": "REVERIFIED",
        "path": relative(target),
        "file_sha256": file_sha256(target),
        "payload_sha256": packet["payload_sha256"],
        "generation_id": packet["generation_id"],
        "summary": copy.deepcopy(packet["summary"]),
    }


# --------------------------------------------------------------------------
# market adapters + CLI
# --------------------------------------------------------------------------
def _adapter(market: str):
    if market == "KR":
        return load_module("korea_population_symbol_observation", "decision/korea_population_symbol_observation.py")
    if market == "US":
        return load_module("us_population_symbol_observation", "decision/us_population_symbol_observation.py")
    _fail("MARKET_INVALID", market)


def _write_receipt(work_dir: Path, receipt: dict) -> None:
    _atomic_write(Path(work_dir) / "run_receipt.json", json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def inside_public_repository(path: Path) -> bool:
    """True when ``path`` resolves inside this public repository checkout."""
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
    except ValueError:
        return False
    return True


def build(
    market: str,
    *,
    generated_at: str,
    session_date: str | None = None,
    inputs: dict | None = None,
    work_dir: Path | None = None,
    output_dir: Path | None = None,
    max_chunks: int | None = None,
    write: bool = True,
    compress: bool = False,
) -> dict:
    """Build (or resume) one market's population packet.

    ``generated_at`` is the lookup time: every source must be no newer than it,
    and it is recorded only in the run receipt.  The packet's own
    ``generated_at`` is derived from the inputs (newest source timestamp) so
    that one generation is byte-identical whenever it is rebuilt.

    Returns ``{"packet", "persist", "resume", "receipt"}``; ``packet`` is
    ``None`` when the run stopped early (``max_chunks``) and must be resumed.
    """
    contract = load_contract()
    if market not in contract["markets"]:
        _fail("MARKET_INVALID", market)
    lookup_at = utc(generated_at, "GENERATED_AT_INVALID")
    adapter = _adapter(market)
    inputs = inputs or adapter.default_inputs(ROOT, session_date=session_date)
    ctx = adapter.load_context(inputs, generated_at=generated_at, contract=contract)
    snapshot_at = ctx["snapshot_at"]
    if utc(snapshot_at, "SNAPSHOT_AT_INVALID") > lookup_at:
        _fail("SOURCE_NEWER_THAN_LOOKUP_TIME", f"{snapshot_at}>{generated_at}")
    session = ctx["session_date"]
    output_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_ROOTS[market] / session
    work_dir = Path(work_dir) if work_dir else output_dir / "work"
    if ctx.get("distribution", "PUBLIC") != "PUBLIC":
        # A packet carrying private per-symbol vendor fields may be built in
        # memory or written outside this repository, never into it -- not the
        # default output root, not an explicit path under it, not work chunks.
        if inside_public_repository(work_dir) or (write and inside_public_repository(output_dir)):
            _fail("PRIVATE_ONLY_PACKET_PUBLIC_WRITE_REFUSED", ctx["distribution"])
    gen_id = generation_id(market, session, ctx["input_refs"])
    symbols = ctx["population_symbols"]
    rows, resume_report = run_chunks(
        market=market, gen_id=gen_id, symbols=symbols, build_row=lambda s: adapter.build_symbol(ctx, s),
        work_dir=work_dir, contract=contract, max_chunks=max_chunks,
    )
    receipt = {"market": market, "session_date": session, "generation_id": gen_id, "lookup_at": generated_at,
               "snapshot_at": snapshot_at, "resume": resume_report}
    if not resume_report["complete"]:
        _write_receipt(work_dir, receipt)
        return {"packet": None, "persist": None, "resume": resume_report, "receipt": receipt}
    packet = assemble_packet(
        market=market, session_date=session, generated_at=snapshot_at, gen_id=gen_id,
        population=ctx["population"], sources=ctx["sources"], rows=rows,
        policy_undefined=ctx["policy_undefined"], resume_report=resume_report, contract=contract,
        extra=adapter.reconciliation(ctx, rows),
    )
    validate_packet(packet, contract)
    persist = persist_packet(packet, output_dir, compress=compress) if write else None
    receipt["persist"] = persist
    receipt["payload_sha256"] = packet["payload_sha256"]
    if write:
        _write_receipt(work_dir, receipt)
    return {"packet": packet, "persist": persist, "resume": resume_report, "receipt": receipt}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=("KR", "US"), required=True)
    parser.add_argument("--generated-at", help="UTC lookup time; sources must be no newer (required unless --reverify)")
    parser.add_argument("--session-date")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--max-chunks", type=int)
    parser.add_argument("--reverify", action="store_true", help="fresh-process re-read of an existing packet only")
    parser.add_argument("--compress", action="store_true", help="persist packet.json.gz instead of packet.json")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args(argv)
    if args.reverify:
        if not args.output_dir:
            parser.error("--reverify requires --output-dir")
        print(json.dumps(reverify(args.output_dir), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if not args.generated_at:
        parser.error("--generated-at is required")
    result = build(
        args.market, generated_at=args.generated_at, session_date=args.session_date,
        work_dir=args.work_dir, output_dir=args.output_dir, max_chunks=args.max_chunks, compress=args.compress,
    )
    if result["packet"] is None:
        print(json.dumps({"outcome": "INTERRUPTED_RESUMABLE", "resume": result["resume"]}, ensure_ascii=False, indent=2))
        return 0
    if args.summary_only:
        packet = result["packet"]
        print(json.dumps({
            "persist": result["persist"], "resume": result["resume"], "generation_id": packet["generation_id"],
            "generated_at": packet["generated_at"], "lookup_at": args.generated_at,
            "summary": packet["summary"], "status_counts": packet["status_counts"], "reconciliation": packet["reconciliation"],
        }, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(result["packet"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
