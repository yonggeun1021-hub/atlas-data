#!/usr/bin/env python3
"""P10-04 operational wiring for immutable daily Decision lineage.

The adapter consumes one committed Daily Briefing packet, independently
validates it, extracts the exact Unified Decision packet, and records a
content-addressed, forward-only observation.  It never interprets or changes a
Decision and has no money, action, order, or trading authority.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import functools
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RECORD_ROOT = ROOT / "evidence" / "operational" / "decision_change_lineage" / "records"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
RAW_DAILY_PREFIX = (
    "https://raw.githubusercontent.com/yonggeun1021-hub/atlas-data/"
)


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINEAGE = _load("atlas_p10_04_lineage", "decision/decision_change_lineage.py")


class OperationalDecisionLineageError(ValueError):
    """Fail-closed P10-04 operational wiring violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_dynamic_clock_frozen_source(packet: dict) -> None:
    """Validate a present P8-12 source without rejecting legacy packets."""
    frozen_sources = packet.get("frozen_sources")
    if frozen_sources is None:
        return
    if type(frozen_sources) is not dict:  # noqa: E721 - exact JSON boundary
        raise OperationalDecisionLineageError(
            "DAILY_DYNAMIC_CLOCK_SOURCE_INVALID:frozen_sources_type"
        )
    if "DYNAMIC_CLOCK" not in frozen_sources:
        return
    source = frozen_sources["DYNAMIC_CLOCK"]
    if type(source) is not dict:  # noqa: E721 - exact JSON boundary
        raise OperationalDecisionLineageError(
            "DAILY_DYNAMIC_CLOCK_SOURCE_INVALID:source_type"
        )
    kind = source.get("kind")
    if type(kind) is not str:  # noqa: E721 - reject bool/string aliases
        raise OperationalDecisionLineageError(
            "DAILY_DYNAMIC_CLOCK_SOURCE_INVALID:kind_type"
        )
    if kind == "unavailable":
        if set(source) != {"kind"}:
            raise OperationalDecisionLineageError(
                "DAILY_DYNAMIC_CLOCK_SOURCE_INVALID:unavailable_shape"
            )
        return
    if kind == "error":
        if set(source) != {"kind", "value"} or type(source.get("value")) is not str:
            raise OperationalDecisionLineageError(
                "DAILY_DYNAMIC_CLOCK_SOURCE_INVALID:error_shape"
            )
        return
    if kind != "report" or set(source) != {"kind", "report_sha256", "report"}:
        raise OperationalDecisionLineageError(
            "DAILY_DYNAMIC_CLOCK_SOURCE_INVALID:report_shape"
        )
    report = source.get("report")
    report_sha256 = source.get("report_sha256")
    if type(report) is not dict or type(report_sha256) is not str:
        raise OperationalDecisionLineageError(
            "DAILY_DYNAMIC_CLOCK_SOURCE_INVALID:report_hash_type"
        )
    if SHA256_RE.fullmatch(report_sha256) is None:
        raise OperationalDecisionLineageError(
            "DAILY_DYNAMIC_CLOCK_SOURCE_INVALID:report_sha256"
        )
    if payload_sha256(report) != report_sha256:
        raise OperationalDecisionLineageError(
            "DAILY_DYNAMIC_CLOCK_SOURCE_SHA256_MISMATCH"
        )
    decision_date = packet.get("decision_date")
    if type(decision_date) is not str:  # noqa: E721 - exact JSON boundary
        raise OperationalDecisionLineageError(
            "DAILY_DYNAMIC_CLOCK_SOURCE_DECISION_DATE_MISMATCH"
        )
    try:
        parsed_decision_date = dt.date.fromisoformat(decision_date)
    except ValueError:
        raise OperationalDecisionLineageError(
            "DAILY_DYNAMIC_CLOCK_SOURCE_DECISION_DATE_MISMATCH"
        ) from None
    if (
        parsed_decision_date.isoformat() != decision_date
        or report.get("decision_date") != decision_date
    ):
        raise OperationalDecisionLineageError(
            "DAILY_DYNAMIC_CLOCK_SOURCE_DECISION_DATE_MISMATCH"
        )


def _read_bytes(path: Path) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise OperationalDecisionLineageError(f"READ_FAILED:{path}:{exc}") from exc


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(_read_bytes(path))
    except json.JSONDecodeError as exc:
        raise OperationalDecisionLineageError(f"JSON_INVALID:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise OperationalDecisionLineageError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _utc(value: str, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise OperationalDecisionLineageError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise OperationalDecisionLineageError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise OperationalDecisionLineageError(code)
    return parsed


def _repo_relative(path: Path) -> str:
    try:
        relative = Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise OperationalDecisionLineageError("SOURCE_PATH_OUTSIDE_REPOSITORY") from exc
    if relative.startswith("../"):
        raise OperationalDecisionLineageError("SOURCE_PATH_OUTSIDE_REPOSITORY")
    return relative


def _git_blob(commit: str, relative: str) -> bytes:
    if not isinstance(commit, str) or FULL_SHA_RE.fullmatch(commit) is None:
        raise OperationalDecisionLineageError("SOURCE_COMMIT_MUST_BE_FULL_SHA")
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise OperationalDecisionLineageError("SOURCE_BLOB_UNAVAILABLE_AT_COMMIT")
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if resolved.returncode != 0 or resolved.stdout.strip() != commit:
        raise OperationalDecisionLineageError("SOURCE_COMMIT_NOT_IMMUTABLE")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if ancestor.returncode != 0:
        raise OperationalDecisionLineageError(
            "SOURCE_COMMIT_NOT_ANCESTOR_OF_CURRENT_HEAD"
        )
    return completed.stdout


def _materialize_exact_commit(commit: str, checkout: Path) -> None:
    """Create an isolated exact-commit checkout while retaining git history.

    ``git archive`` is insufficient for Atlas validators that independently
    prove first-seen provenance from commit history. Fetching the immutable
    commit and its ancestry into a new local repository preserves that
    evidence without consulting a branch name or the network.
    """
    if not isinstance(commit, str) or FULL_SHA_RE.fullmatch(commit) is None:
        raise OperationalDecisionLineageError("SOURCE_COMMIT_MUST_BE_FULL_SHA")
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if resolved.returncode != 0 or resolved.stdout.strip() != commit:
        raise OperationalDecisionLineageError("SOURCE_COMMIT_NOT_IMMUTABLE")
    commands = (
        ["git", "init", "--quiet", str(checkout)],
        [
            "git", "-C", str(checkout), "fetch", "--quiet", "--no-tags",
            str(ROOT), commit,
        ],
        ["git", "-C", str(checkout), "checkout", "--quiet", "--detach", commit],
    )
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            raise OperationalDecisionLineageError(
                "SOURCE_COMMIT_CHECKOUT_FAILED"
            )
    checked = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=checkout,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if (
        checked.returncode != 0
        or checked.stdout.strip() != commit
        or dirty.returncode != 0
        or dirty.stdout.strip()
    ):
        raise OperationalDecisionLineageError("SOURCE_COMMIT_CHECKOUT_INVALID")


@functools.lru_cache(maxsize=16)
def _validate_daily_at_commit(commit: str, relative: str, blob_sha256: str) -> dict:
    """Validate the immutable daily blob and its Unified Decision at source.

    Rebuilding an entire historical daily packet is not a stable lineage
    check: some diagnostic components intentionally derive Git first-seen
    provenance from the repository graph visible when the packet was built.
    A later clone can have a different ref graph even while the committed
    packet bytes and the Decision are unchanged.  P10-04 only consumes the
    Unified Decision, so this boundary verifies the whole daily blob's
    content hash, extracts that exact component, and runs the source commit's
    own Unified Decision validator in an isolated checkout.
    """
    blob = _git_blob(commit, relative)
    if hashlib.sha256(blob).hexdigest() != blob_sha256:
        raise OperationalDecisionLineageError("SOURCE_BLOB_SHA256_MISMATCH")
    try:
        value = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise OperationalDecisionLineageError("SOURCE_PACKET_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise OperationalDecisionLineageError("SOURCE_PACKET_OBJECT_REQUIRED")
    digest = value.get("packet_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise OperationalDecisionLineageError("SOURCE_PACKET_SHA256_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256", None)
    if payload_sha256(unsigned) != digest:
        raise OperationalDecisionLineageError("SOURCE_PACKET_SHA256_MISMATCH")
    _validate_dynamic_clock_frozen_source(value)
    rows = value.get("components")
    if not isinstance(rows, list):
        raise OperationalDecisionLineageError("DAILY_COMPONENTS_NOT_LIST")
    matches = [
        row for row in rows
        if isinstance(row, dict) and row.get("component_id") == "UNIFIED_DECISION"
    ]
    if len(matches) != 1:
        raise OperationalDecisionLineageError(
            "DAILY_COMPONENT_IDENTITY_INVALID:UNIFIED_DECISION"
        )
    unified_row = matches[0]
    unified = unified_row.get("packet")
    if unified_row.get("validated") is not True or not isinstance(unified, dict):
        raise OperationalDecisionLineageError(
            "DAILY_COMPONENT_NOT_VALIDATED:UNIFIED_DECISION"
        )
    with tempfile.TemporaryDirectory(prefix="atlas-p10-04-validate-") as temporary:
        checkout = Path(temporary) / "repo"
        _materialize_exact_commit(commit, checkout)
        validator = """
import importlib.util
import json
from pathlib import Path
import sys

path = Path("decision/unified_decision_contract.py")
spec = importlib.util.spec_from_file_location("atlas_exact_unified", path)
if spec is None or spec.loader is None:
    raise RuntimeError("UNIFIED_VALIDATOR_IMPORT_FAILED")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.validate_packet(json.load(sys.stdin))
"""
        completed = subprocess.run(
            [sys.executable, "-c", validator],
            cwd=checkout,
            check=False,
            text=True,
            input=canonical_json(unified),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if completed.returncode != 0:
            raise OperationalDecisionLineageError(
                f"UNIFIED_DECISION_INVALID_AT_SOURCE_COMMIT:{completed.stdout.strip()}"
            )
    return value


def _parse_daily_source_ref(source_ref: str) -> tuple[str, str]:
    if not isinstance(source_ref, str) or not source_ref.startswith(RAW_DAILY_PREFIX):
        raise OperationalDecisionLineageError("SNAPSHOT_SOURCE_REF_INVALID")
    remainder = source_ref[len(RAW_DAILY_PREFIX):]
    try:
        commit, relative = remainder.split("/", 1)
    except ValueError as exc:
        raise OperationalDecisionLineageError("SNAPSHOT_SOURCE_REF_INVALID") from exc
    path = Path(relative)
    if (
        FULL_SHA_RE.fullmatch(commit) is None
        or path.is_absolute()
        or ".." in path.parts
        or not relative.startswith("evidence/daily_briefing/")
        or not relative.endswith("/packet.json")
    ):
        raise OperationalDecisionLineageError("SNAPSHOT_SOURCE_REF_INVALID")
    return commit, relative


def _validate_snapshot_at_source(
    decision_packet: dict, source_ref: str, context: str
) -> dict:
    """Resolve each snapshot against the immutable daily blob it cites."""
    commit, relative = _parse_daily_source_ref(source_ref)
    blob = _git_blob(commit, relative)
    daily = _validate_daily_at_commit(
        commit, relative, hashlib.sha256(blob).hexdigest()
    )
    unified = _component(daily, "UNIFIED_DECISION")["packet"]
    if decision_packet != unified:
        raise OperationalDecisionLineageError(
            f"SNAPSHOT_DECISION_SOURCE_MISMATCH:{context}"
        )
    return copy.deepcopy(unified)


def _component(packet: dict, component_id: str) -> dict:
    rows = packet.get("components")
    if not isinstance(rows, list):
        raise OperationalDecisionLineageError("DAILY_COMPONENTS_NOT_LIST")
    matches = [row for row in rows if isinstance(row, dict) and row.get("component_id") == component_id]
    if len(matches) != 1:
        raise OperationalDecisionLineageError(f"DAILY_COMPONENT_IDENTITY_INVALID:{component_id}")
    row = matches[0]
    if row.get("validated") is not True or not isinstance(row.get("packet"), dict):
        raise OperationalDecisionLineageError(f"DAILY_COMPONENT_NOT_VALIDATED:{component_id}")
    return row


def _snapshot(unified: dict, source_ref: str) -> dict:
    contract = LINEAGE.load_contract()
    return {
        "schema_version": contract["snapshot_schema_version"],
        "decision_key": contract["decision_key"],
        "market": "COMMON",
        "subject_id": contract["subject_id"],
        "decided_at": unified["generated_at"],
        "decision_sha256": unified["packet_sha256"],
        "source_ref": source_ref,
        "source_sha256": unified["packet_sha256"],
        "decision_packet": copy.deepcopy(unified),
    }


def _record_without_hash(record: dict) -> dict:
    value = copy.deepcopy(record)
    value.pop("record_sha256", None)
    return value


def validate_record(record: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "recorded_at", "source_commit",
        "source_path", "source_blob_sha256", "previous_record_sha256",
        "lineage_packet", "authority", "record_sha256",
    }
    if not isinstance(record, dict) or set(record) != fields:
        raise OperationalDecisionLineageError("RECORD_FIELDS_MISMATCH")
    if (
        record.get("schema_version") != "decision_change_lineage_operational_record/1"
        or record.get("contract_version") != "decision_change_lineage_operational/1"
    ):
        raise OperationalDecisionLineageError("RECORD_IDENTITY_INVALID")
    _utc(record.get("recorded_at"), "RECORDED_AT_INVALID")
    if FULL_SHA_RE.fullmatch(str(record.get("source_commit"))) is None:
        raise OperationalDecisionLineageError("RECORD_SOURCE_COMMIT_INVALID")
    if not isinstance(record.get("source_path"), str) or not record["source_path"]:
        raise OperationalDecisionLineageError("RECORD_SOURCE_PATH_INVALID")
    for key in ("source_blob_sha256", "record_sha256"):
        value = record.get(key)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise OperationalDecisionLineageError(f"{key.upper()}_INVALID")
    previous = record.get("previous_record_sha256")
    if previous is not None and (
        not isinstance(previous, str) or re.fullmatch(r"[0-9a-f]{64}", previous) is None
    ):
        raise OperationalDecisionLineageError("PREVIOUS_RECORD_SHA256_INVALID")
    expected_authority = {
        "lineage_recording_only": True,
        "decision_interpretation_authorized": False,
        "decision_change_authorized": False,
        "action_generation_authorized": False,
        "capital_authorized": False,
        "order_generation_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }
    if record.get("authority") != expected_authority:
        raise OperationalDecisionLineageError("RECORD_AUTHORITY_INVALID")
    try:
        lineage_packet = LINEAGE.validate_output(
            record.get("lineage_packet"),
            snapshot_validator=_validate_snapshot_at_source,
        )
    except Exception as exc:
        raise OperationalDecisionLineageError(f"LINEAGE_PACKET_INVALID:{exc}") from exc
    if lineage_packet["observed_at"] != record["recorded_at"]:
        raise OperationalDecisionLineageError("LINEAGE_RECORD_TIME_MISMATCH")
    blob = _git_blob(record["source_commit"], record["source_path"])
    if hashlib.sha256(blob).hexdigest() != record["source_blob_sha256"]:
        raise OperationalDecisionLineageError("RECORD_SOURCE_BLOB_SHA256_MISMATCH")
    daily = _validate_daily_at_commit(
        record["source_commit"], record["source_path"], record["source_blob_sha256"]
    )
    unified = _component(daily, "UNIFIED_DECISION")["packet"]
    entries = lineage_packet.get("entries")
    if not isinstance(entries, list) or len(entries) != 1:
        raise OperationalDecisionLineageError("RECORD_LINEAGE_ENTRY_COUNT_INVALID")
    current = entries[0].get("current_snapshot")
    expected_ref = (
        "https://raw.githubusercontent.com/yonggeun1021-hub/atlas-data/"
        f"{record['source_commit']}/{record['source_path']}"
    )
    if (
        not isinstance(current, dict)
        or current.get("decision_packet") != unified
        or current.get("decision_sha256") != unified.get("packet_sha256")
        or current.get("source_sha256") != unified.get("packet_sha256")
        or current.get("source_ref") != expected_ref
    ):
        raise OperationalDecisionLineageError("RECORD_CURRENT_DECISION_LINEAGE_MISMATCH")
    if entries[0].get("change_type") != "UNCHANGED":
        evidence = entries[0].get("evidence")
        if (
            not isinstance(evidence, list)
            or len(evidence) != 1
            or evidence[0].get("uri") != expected_ref
            or evidence[0].get("source_sha256") != record["source_blob_sha256"]
        ):
            raise OperationalDecisionLineageError("RECORD_EVIDENCE_LINEAGE_MISMATCH")
    if payload_sha256(_record_without_hash(record)) != record["record_sha256"]:
        raise OperationalDecisionLineageError("RECORD_SHA256_MISMATCH")
    return copy.deepcopy(record)


def load_history(root: Path = RECORD_ROOT) -> list[dict]:
    root = Path(root)
    if not root.exists():
        return []
    rows = [validate_record(_read_json(path)) for path in sorted(root.glob("record-*.json"))]
    rows.sort(key=lambda row: row["recorded_at"])
    previous_sha = None
    for index, row in enumerate(rows):
        if row["previous_record_sha256"] != previous_sha:
            raise OperationalDecisionLineageError(f"RECORD_CHAIN_BROKEN:{index}")
        previous_sha = row["record_sha256"]
    if len({row["record_sha256"] for row in rows}) != len(rows):
        raise OperationalDecisionLineageError("RECORD_SHA256_DUPLICATE")
    return rows


def build_record(
    briefing_path: Path,
    source_commit: str,
    recorded_at: str,
    previous: dict | None = None,
) -> dict:
    observed = _utc(recorded_at, "RECORDED_AT_INVALID")
    relative = _repo_relative(briefing_path)
    disk_bytes = _read_bytes(briefing_path)
    blob = _git_blob(source_commit, relative)
    if disk_bytes != blob:
        raise OperationalDecisionLineageError("SOURCE_DISK_COMMIT_MISMATCH")
    blob_sha256 = hashlib.sha256(blob).hexdigest()
    daily = _validate_daily_at_commit(source_commit, relative, blob_sha256)
    unified = _component(daily, "UNIFIED_DECISION")["packet"]
    if _utc(unified["generated_at"], "UNIFIED_GENERATED_AT_INVALID") > observed:
        raise OperationalDecisionLineageError("UNIFIED_DECISION_FROM_FUTURE")
    source_ref = (
        "https://raw.githubusercontent.com/yonggeun1021-hub/atlas-data/"
        f"{source_commit}/{relative}"
    )
    current_snapshot = _snapshot(unified, source_ref)
    prior_snapshot = None
    previous_sha = None
    if previous is not None:
        prior_record = validate_record(previous)
        previous_sha = prior_record["record_sha256"]
        previous_entries = prior_record["lineage_packet"]["entries"]
        if len(previous_entries) != 1:
            raise OperationalDecisionLineageError("PREVIOUS_LINEAGE_ENTRY_COUNT_INVALID")
        prior_snapshot = previous_entries[0]["current_snapshot"]
        if prior_snapshot is None:
            raise OperationalDecisionLineageError("PREVIOUS_CURRENT_SNAPSHOT_MISSING")
        if _utc(prior_record["recorded_at"], "PREVIOUS_RECORDED_AT_INVALID") >= observed:
            raise OperationalDecisionLineageError("NON_FORWARD_RECORDED_AT")
    changed = prior_snapshot is None or prior_snapshot["decision_sha256"] != current_snapshot["decision_sha256"]
    evidence = []
    reasons = []
    if changed:
        reasons = [
            "FIRST_OBSERVED_UNIFIED_DECISION"
            if prior_snapshot is None
            else "UNIFIED_DECISION_PACKET_SHA_CHANGED"
        ]
        evidence = [{
            "evidence_id": f"DAILY.BRIEFING.{daily['decision_date'].replace('-', '')}.{daily['slot'].upper()}",
            "uri": source_ref,
            "available_at": unified["generated_at"],
            "source_sha256": hashlib.sha256(blob).hexdigest(),
        }]
    contract = LINEAGE.load_contract()
    batch = {
        "schema_version": contract["claim_batch_schema_version"],
        "contract_version": contract["contract_version"],
        "batch_id": f"DECISION.LINEAGE.{daily['decision_date'].replace('-', '')}.{daily['slot'].upper()}",
        "observed_at": recorded_at,
        "claims": [{
            "decision_key": contract["decision_key"],
            "market": "COMMON",
            "subject_id": contract["subject_id"],
            "change_observed_at": recorded_at,
            "prior_snapshot": prior_snapshot,
            "current_snapshot": current_snapshot,
            "reason_codes": reasons,
            "evidence": evidence,
        }],
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    batch["packet_sha256"] = LINEAGE.payload_sha256(batch)
    lineage = LINEAGE.build_lineage(
        batch, contract, snapshot_validator=_validate_snapshot_at_source
    )
    record = {
        "schema_version": "decision_change_lineage_operational_record/1",
        "contract_version": "decision_change_lineage_operational/1",
        "recorded_at": recorded_at,
        "source_commit": source_commit,
        "source_path": relative,
        "source_blob_sha256": blob_sha256,
        "previous_record_sha256": previous_sha,
        "lineage_packet": lineage,
        "authority": {
            "lineage_recording_only": True,
            "decision_interpretation_authorized": False,
            "decision_change_authorized": False,
            "action_generation_authorized": False,
            "capital_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    record["record_sha256"] = payload_sha256(record)
    return validate_record(record)


def write_record(record: dict, root: Path = RECORD_ROOT) -> tuple[Path, bool]:
    record = validate_record(record)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"record-{record['record_sha256']}.json"
    encoded = (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != encoded:
            raise OperationalDecisionLineageError("CONTENT_ADDRESSED_RECORD_COLLISION")
        return path, False
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(root))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return path, True


def run(briefing_path: Path, source_commit: str, recorded_at: str, root: Path = RECORD_ROOT) -> int:
    try:
        history = load_history(root)
        record = build_record(briefing_path, source_commit, recorded_at, history[-1] if history else None)
        path, created = write_record(record, root)
        print(f"record_path={path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
        print(f"record_created={'true' if created else 'false'}")
        print(f"record_sha256={record['record_sha256']}")
        print(f"change_type={record['lineage_packet']['entries'][0]['change_type']}")
        return 0
    except (OperationalDecisionLineageError, OSError, TypeError, ValueError) as exc:
        print(f"Operational Decision lineage failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Record committed Daily Decision lineage")
    parser.add_argument("briefing_packet", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--recorded-at", required=True)
    parser.add_argument("--record-root", type=Path, default=RECORD_ROOT)
    args = parser.parse_args()
    return run(args.briefing_packet, args.source_commit, args.recorded_at, args.record_root)


if __name__ == "__main__":
    raise SystemExit(main())
