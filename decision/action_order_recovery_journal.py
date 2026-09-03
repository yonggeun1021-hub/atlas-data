#!/usr/bin/env python3
"""Crash-safe, evidence-only persistence for the P9-04 duplicate guard.

The journal is deliberately external to the repository and has no order,
broker, account, or network path.  Immutable ledger/result blobs are made
durable before one atomic HEAD replacement.  A crash before HEAD publication
therefore leaves only unreachable blobs; recovery continues from the last
fully committed and independently revalidated chain.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "action_order_recovery_journal_contract.json"
GUARD_PATH = ROOT / "decision" / "action_order_idempotency.py"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")


class ActionOrderRecoveryJournalError(ValueError):
    """Fail-closed recovery-journal contract or chain violation."""


def _load_guard():
    spec = importlib.util.spec_from_file_location(
        "atlas_action_order_idempotency_for_recovery", GUARD_PATH
    )
    if spec is None or spec.loader is None:
        raise ActionOrderRecoveryJournalError("GUARD_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD = _load_guard()


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ActionOrderRecoveryJournalError("CANONICAL_JSON_INVALID") from exc


def payload_sha256(value: object) -> str:
    return GUARD.payload_sha256(value)


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActionOrderRecoveryJournalError(code) from exc
    if not isinstance(value, dict):
        raise ActionOrderRecoveryJournalError(code)
    return value


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "action_order_recovery_journal/1",
        "source_guard_contract_version": "action_order_idempotency_guard/1",
        "source_result_schema_version": "action_order_idempotency_result/2",
        "head_schema_version": "action_order_recovery_journal_head/1",
        "commit_schema_version": "action_order_recovery_journal_commit/1",
        "storage_mode": "EXTERNAL_CONTENT_ADDRESSED_EVIDENCE_ONLY",
        "exact_retry_policy": "RETURN_EXISTING_RECEIPT_NO_WRITE",
        "crash_commit_policy": "IMMUTABLE_BLOBS_THEN_ATOMIC_HEAD",
        "repository_internal_storage_authorized": False,
        "authority": {
            "simulation_shadow_evidence_only": True,
            "action_creation_authorized": False,
            "order_creation_authorized": False,
            "order_execution_authorized": False,
            "order_cancellation_authorized": False,
            "broker_submission_authorized": False,
            "withdrawal_authorized": False,
            "real_capital_authorized": False,
            "live_account_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _exact_value_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _exact_value_equal(actual[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _exact_value_equal(left, right)
            for left, right in zip(actual, expected)
        )
    return actual == expected


def _validate_contract(value: object) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ActionOrderRecoveryJournalError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        actual = value.get(key)
        if not _exact_value_equal(actual, expected_value):
            raise ActionOrderRecoveryJournalError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path), "CONTRACT_READ_FAILED"))


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ActionOrderRecoveryJournalError(code)
    return value


def _token(value: object, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise ActionOrderRecoveryJournalError(code)
    return value


def _exact_int(value: object, code: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ActionOrderRecoveryJournalError(code)
    return value


def _external_root(path: Path) -> Path:
    path = Path(path).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        return path
    raise ActionOrderRecoveryJournalError(f"TRACKED_JOURNAL_ROOT_FORBIDDEN:{path}")


def _directory_fsync(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _write_immutable_json(path: Path, value: dict) -> bool:
    """Publish one immutable blob without ever replacing an existing path."""
    path = Path(path)
    payload = _json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise ActionOrderRecoveryJournalError("IMMUTABLE_BLOB_READ_FAILED") from exc
        if existing != payload:
            raise ActionOrderRecoveryJournalError(
                f"IMMUTABLE_BLOB_CONFLICT:{path.name}"
            )
        return False
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ActionOrderRecoveryJournalError(
                    f"IMMUTABLE_BLOB_CONFLICT:{path.name}"
                )
            return False
        _directory_fsync(path.parent)
        return True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_head_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _directory_fsync(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def _exclusive_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".journal.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _ledger_blob_path(root: Path, digest: str) -> Path:
    return root / "ledgers" / f"{digest}.json"


def _receipt_blob_path(root: Path, digest: str) -> Path:
    return root / "receipts" / f"{digest}.json"


def _checked_ledger(value: dict, guard_contract: dict) -> dict:
    try:
        checked = GUARD._validate_ledger(value, guard_contract)
    except GUARD.ActionOrderIdempotencyError as exc:
        raise ActionOrderRecoveryJournalError(f"LEDGER_INVALID:{exc}") from exc
    normalized = copy.deepcopy(checked["normalized"])
    normalized["packet_sha256"] = checked["packet_sha256"]
    if canonical_json(normalized) != canonical_json(value):
        raise ActionOrderRecoveryJournalError("LEDGER_NOT_NORMALIZED")
    return normalized


def _checked_batch(value: dict, guard_contract: dict) -> dict:
    try:
        checked = GUARD._validate_batch(value, guard_contract)
    except GUARD.ActionOrderIdempotencyError as exc:
        raise ActionOrderRecoveryJournalError(f"BATCH_INVALID:{exc}") from exc
    normalized = copy.deepcopy(checked["normalized"])
    normalized["packet_sha256"] = checked["packet_sha256"]
    if canonical_json(normalized) != canonical_json(value):
        raise ActionOrderRecoveryJournalError("BATCH_NOT_NORMALIZED")
    return normalized


def _checked_result(value: dict, guard_contract: dict) -> dict:
    try:
        return GUARD.validate_result(value, guard_contract)
    except GUARD.ActionOrderIdempotencyError as exc:
        raise ActionOrderRecoveryJournalError(f"RESULT_INVALID:{exc}") from exc


def _new_head(journal_id: str, ledger_sha256: str, contract: dict) -> dict:
    head = {
        "schema_version": contract["head_schema_version"],
        "contract_version": contract["contract_version"],
        "journal_id": journal_id,
        "revision": 0,
        "initial_ledger_sha256": ledger_sha256,
        "current_ledger_sha256": ledger_sha256,
        "commits": [],
        "authority": copy.deepcopy(contract["authority"]),
    }
    head["packet_sha256"] = payload_sha256(head)
    return head


def _validate_commit(value: object, contract: dict) -> dict:
    fields = {
        "schema_version",
        "contract_version",
        "revision",
        "batch_id",
        "observed_at",
        "attempt_batch_sha256",
        "prior_ledger_sha256",
        "result_sha256",
        "updated_ledger_sha256",
        "previous_commit_sha256",
        "commit_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ActionOrderRecoveryJournalError("COMMIT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["commit_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
    ):
        raise ActionOrderRecoveryJournalError("COMMIT_IDENTITY_INVALID")
    _exact_int(value.get("revision"), "COMMIT_REVISION_TYPE_INVALID", minimum=1)
    try:
        GUARD._text(value.get("batch_id"), "COMMIT_BATCH_ID_INVALID")
    except GUARD.ActionOrderIdempotencyError as exc:
        raise ActionOrderRecoveryJournalError(str(exc)) from exc
    try:
        GUARD._utc(value.get("observed_at"), "COMMIT_OBSERVED_AT_INVALID")
    except GUARD.ActionOrderIdempotencyError as exc:
        raise ActionOrderRecoveryJournalError(str(exc)) from exc
    for field in (
        "attempt_batch_sha256",
        "prior_ledger_sha256",
        "result_sha256",
        "updated_ledger_sha256",
    ):
        _sha(value.get(field), f"COMMIT_{field.upper()}_INVALID")
    previous = value.get("previous_commit_sha256")
    if previous is not None:
        _sha(previous, "COMMIT_PREVIOUS_SHA_INVALID")
    digest = _sha(value.get("commit_sha256"), "COMMIT_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("commit_sha256")
    if payload_sha256(unsigned) != digest:
        raise ActionOrderRecoveryJournalError("COMMIT_SHA_MISMATCH")
    return copy.deepcopy(value)


def _validate_head(value: object, journal_id: str, contract: dict) -> dict:
    fields = {
        "schema_version",
        "contract_version",
        "journal_id",
        "revision",
        "initial_ledger_sha256",
        "current_ledger_sha256",
        "commits",
        "authority",
        "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ActionOrderRecoveryJournalError("HEAD_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["head_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("journal_id") != journal_id
        or not _exact_value_equal(value.get("authority"), contract["authority"])
    ):
        raise ActionOrderRecoveryJournalError("HEAD_IDENTITY_INVALID")
    revision = _exact_int(value.get("revision"), "HEAD_REVISION_TYPE_INVALID")
    _sha(value.get("initial_ledger_sha256"), "HEAD_INITIAL_LEDGER_SHA_INVALID")
    _sha(value.get("current_ledger_sha256"), "HEAD_CURRENT_LEDGER_SHA_INVALID")
    commits = value.get("commits")
    if not isinstance(commits, list) or revision != len(commits):
        raise ActionOrderRecoveryJournalError("HEAD_REVISION_DERIVATION_MISMATCH")
    digest = _sha(value.get("packet_sha256"), "HEAD_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise ActionOrderRecoveryJournalError("HEAD_SHA_MISMATCH")
    return copy.deepcopy(value)


def _load_ledger_blob(root: Path, digest: str, guard_contract: dict) -> dict:
    value = _checked_ledger(
        _read_json(_ledger_blob_path(root, digest), "LEDGER_BLOB_READ_FAILED"),
        guard_contract,
    )
    if value["packet_sha256"] != digest:
        raise ActionOrderRecoveryJournalError("LEDGER_BLOB_NAME_MISMATCH")
    return value


def _load_receipt_blob(root: Path, digest: str, guard_contract: dict) -> dict:
    value = _checked_result(
        _read_json(_receipt_blob_path(root, digest), "RECEIPT_BLOB_READ_FAILED"),
        guard_contract,
    )
    if value["packet_sha256"] != digest:
        raise ActionOrderRecoveryJournalError("RECEIPT_BLOB_NAME_MISMATCH")
    return value


def _recover_unlocked(root: Path, journal_id: str, contract: dict) -> dict:
    head = _validate_head(
        _read_json(root / "head.json", "JOURNAL_HEAD_READ_FAILED"),
        journal_id,
        contract,
    )
    guard_contract = GUARD.load_contract()
    prior_sha = head["initial_ledger_sha256"]
    current_ledger = _load_ledger_blob(root, prior_sha, guard_contract)
    previous_commit_sha = None
    seen_batches: set[str] = set()
    for index, raw_commit in enumerate(head["commits"], start=1):
        commit = _validate_commit(raw_commit, contract)
        if (
            commit["revision"] != index
            or commit["previous_commit_sha256"] != previous_commit_sha
            or commit["prior_ledger_sha256"] != prior_sha
        ):
            raise ActionOrderRecoveryJournalError("COMMIT_CHAIN_DERIVATION_MISMATCH")
        batch_sha = commit["attempt_batch_sha256"]
        if batch_sha in seen_batches:
            raise ActionOrderRecoveryJournalError("COMMIT_BATCH_DUPLICATE")
        seen_batches.add(batch_sha)
        receipt = _load_receipt_blob(root, commit["result_sha256"], guard_contract)
        sources = receipt["source_packets"]
        updated = receipt["updated_ledger_candidate"]
        if (
            receipt["batch_id"] != commit["batch_id"]
            or receipt["observed_at"] != commit["observed_at"]
            or sources["prior_ledger"]["packet_sha256"] != prior_sha
            or sources["attempt_batch"]["packet_sha256"] != batch_sha
            or updated["packet_sha256"] != commit["updated_ledger_sha256"]
        ):
            raise ActionOrderRecoveryJournalError("COMMIT_RESULT_DERIVATION_MISMATCH")
        current_ledger = _load_ledger_blob(
            root, commit["updated_ledger_sha256"], guard_contract
        )
        if canonical_json(current_ledger) != canonical_json(updated):
            raise ActionOrderRecoveryJournalError("COMMIT_LEDGER_DERIVATION_MISMATCH")
        prior_sha = commit["updated_ledger_sha256"]
        previous_commit_sha = commit["commit_sha256"]
    if head["current_ledger_sha256"] != prior_sha:
        raise ActionOrderRecoveryJournalError("HEAD_CURRENT_LEDGER_DERIVATION_MISMATCH")
    return {"head": head, "ledger": current_ledger}


def initialize_journal(root: Path, journal_id: str, initial_ledger: dict) -> dict:
    root = _external_root(root)
    journal_id = _token(journal_id, "JOURNAL_ID_INVALID")
    contract = load_contract()
    guard_contract = GUARD.load_contract()
    checked_ledger = _checked_ledger(initial_ledger, guard_contract)
    with _exclusive_lock(root):
        head_path = root / "head.json"
        if head_path.exists():
            recovered = _recover_unlocked(root, journal_id, contract)
            initial = _load_ledger_blob(
                root,
                recovered["head"]["initial_ledger_sha256"],
                guard_contract,
            )
            if canonical_json(initial) != canonical_json(checked_ledger):
                raise ActionOrderRecoveryJournalError(
                    "JOURNAL_ALREADY_INITIALIZED_WITH_DIFFERENT_LEDGER"
                )
            return {"action": "ALREADY_INITIALIZED_NOOP", **recovered}
        digest = checked_ledger["packet_sha256"]
        _write_immutable_json(_ledger_blob_path(root, digest), checked_ledger)
        head = _new_head(journal_id, digest, contract)
        _write_head_atomic(head_path, head)
        return {"action": "INITIALIZED", "head": head, "ledger": checked_ledger}


def recover_journal(root: Path, journal_id: str) -> dict:
    root = _external_root(root)
    journal_id = _token(journal_id, "JOURNAL_ID_INVALID")
    return _recover_unlocked(root, journal_id, load_contract())


def apply_attempt_batch(root: Path, journal_id: str, attempt_batch: dict) -> dict:
    root = _external_root(root)
    journal_id = _token(journal_id, "JOURNAL_ID_INVALID")
    contract = load_contract()
    guard_contract = GUARD.load_contract()
    checked_batch = _checked_batch(attempt_batch, guard_contract)
    batch_sha = checked_batch["packet_sha256"]
    with _exclusive_lock(root):
        recovered = _recover_unlocked(root, journal_id, contract)
        head = recovered["head"]
        for commit in head["commits"]:
            if commit["attempt_batch_sha256"] == batch_sha:
                receipt = _load_receipt_blob(
                    root, commit["result_sha256"], guard_contract
                )
                return {
                    "action": "EXACT_RETRY_NOOP",
                    "head": head,
                    "ledger": recovered["ledger"],
                    "receipt": receipt,
                }
        try:
            receipt = GUARD.build_result(
                recovered["ledger"], checked_batch, guard_contract
            )
        except GUARD.ActionOrderIdempotencyError as exc:
            raise ActionOrderRecoveryJournalError(f"GUARD_REJECTED:{exc}") from exc
        updated = _checked_ledger(receipt["updated_ledger_candidate"], guard_contract)
        receipt_sha = receipt["packet_sha256"]
        updated_sha = updated["packet_sha256"]
        _write_immutable_json(_receipt_blob_path(root, receipt_sha), receipt)
        _write_immutable_json(_ledger_blob_path(root, updated_sha), updated)
        revision = head["revision"] + 1
        previous_commit_sha = (
            None if not head["commits"] else head["commits"][-1]["commit_sha256"]
        )
        commit = {
            "schema_version": contract["commit_schema_version"],
            "contract_version": contract["contract_version"],
            "revision": revision,
            "batch_id": checked_batch["batch_id"],
            "observed_at": checked_batch["observed_at"],
            "attempt_batch_sha256": batch_sha,
            "prior_ledger_sha256": recovered["ledger"]["packet_sha256"],
            "result_sha256": receipt_sha,
            "updated_ledger_sha256": updated_sha,
            "previous_commit_sha256": previous_commit_sha,
        }
        commit["commit_sha256"] = payload_sha256(commit)
        next_head = copy.deepcopy(head)
        next_head["revision"] = revision
        next_head["current_ledger_sha256"] = updated_sha
        next_head["commits"].append(commit)
        next_head.pop("packet_sha256")
        next_head["packet_sha256"] = payload_sha256(next_head)
        _write_head_atomic(root / "head.json", next_head)
        verified = _recover_unlocked(root, journal_id, contract)
        return {
            "action": "COMMITTED_EVIDENCE_ONLY",
            "head": verified["head"],
            "ledger": verified["ledger"],
            "receipt": receipt,
        }


def _summary(outcome: dict) -> dict:
    result = {
        "action": outcome["action"],
        "journal_id": outcome["head"]["journal_id"],
        "revision": outcome["head"]["revision"],
        "head_sha256": outcome["head"]["packet_sha256"],
        "current_ledger_sha256": outcome["head"]["current_ledger_sha256"],
        "authority": outcome["head"]["authority"],
    }
    if "receipt" in outcome:
        result["receipt_sha256"] = outcome["receipt"]["packet_sha256"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persist or recover P9-04 evidence without order authority"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init")
    init.add_argument("--root", type=Path, required=True)
    init.add_argument("--journal-id", required=True)
    init.add_argument("--ledger", type=Path, required=True)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--root", type=Path, required=True)
    apply.add_argument("--journal-id", required=True)
    apply.add_argument("--batch", type=Path, required=True)
    recover = subparsers.add_parser("recover")
    recover.add_argument("--root", type=Path, required=True)
    recover.add_argument("--journal-id", required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            outcome = initialize_journal(
                args.root,
                args.journal_id,
                _read_json(args.ledger, "LEDGER_INPUT_READ_FAILED"),
            )
        elif args.command == "apply":
            outcome = apply_attempt_batch(
                args.root,
                args.journal_id,
                _read_json(args.batch, "BATCH_INPUT_READ_FAILED"),
            )
        else:
            outcome = {"action": "RECOVERED", **recover_journal(args.root, args.journal_id)}
        print(json.dumps(_summary(outcome), ensure_ascii=False, sort_keys=True))
        return 0
    except (ActionOrderRecoveryJournalError, OSError, TypeError, ValueError) as exc:
        print(f"Action/Order recovery journal failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
