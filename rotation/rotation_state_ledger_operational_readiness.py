#!/usr/bin/env python3
"""P2-05 repository-evidence-only operational readiness inventory.

This command never accepts a caller-supplied state policy or ledger. It reports
whether the repository contains the three independently required inputs for an
operational state history: a full producer packet, an externally ratified state
policy, and append-only ledger evidence. A briefing pointer is lineage evidence,
not a substitute for the full packet or ledger.

It also exposes an immutable, Git-backed form of the same inventory for
consumers that must replay an archived verdict exactly: see the frozen
readiness inputs section below. Neither form authorizes a state vocabulary,
ledger, ranking, freshness policy, availability or any money action.
"""
from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "rotation_state_ledger_operational_readiness_contract.json"
LEDGER_CONTRACT_PATH = ROOT / "config" / "rotation_state_ledger_contract.json"
SCHEMA_VERSION = "rotation_state_ledger_operational_readiness/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# The three independently required inputs, as repository-relative POSIX paths.
# They are the ONLY paths this module will ever freeze or replay. A frozen
# packet supplies bytes for exactly these keys and nothing else: it cannot name
# an extra path, a URL, a repository, a ref or a validation HEAD. (The trusted
# repository root stays an API argument, exactly as build_readiness(root)
# already takes one.)
READINESS_CONTRACT_REL = "config/rotation_state_ledger_operational_readiness_contract.json"
LEDGER_CONTRACT_REL = "config/rotation_state_ledger_contract.json"
KOREA_ROTATION_POINTER_REL = "data/latest_korea_rotation.json"
FROZEN_INPUT_PATHS = (
    READINESS_CONTRACT_REL,
    LEDGER_CONTRACT_REL,
    KOREA_ROTATION_POINTER_REL,
)
FROZEN_INPUT_SCHEMA_VERSION = "p2_rotation_readiness_inputs/1"
FROZEN_INPUT_ENVELOPE_KEYS = frozenset({"schema_version", "source_commit", "files"})
FROZEN_INPUT_FILE_KEYS = frozenset({"state", "blob_oid", "content_base64"})
FROZEN_INPUT_STATES = ("PRESENT", "ABSENT")
INVENTORY_SCHEMA_VERSION = "rotation_state_ledger_operational_readiness_inventory/1"
# Git SHA-1 object ids, lowercase and unabbreviated. An abbreviated, uppercase
# or SHA-256 oid is rejected rather than resolved.
GIT_OID_RE = re.compile(r"^[0-9a-f]{40}$")
GIT_BLOB_MODES = ("100644", "100755")
# The fixed, finite blocker vocabulary every market row carries. Hoisted so the
# live packet builder and the pure frozen-input rederivation cannot drift apart.
MARKET_BLOCKERS = (
    "FULL_PRODUCTION_ROTATION_PACKET_MISSING",
    "EXTERNAL_RATIFIED_STATE_POLICY_MISSING",
    "APPEND_ONLY_OPERATIONAL_LEDGER_EVIDENCE_MISSING",
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEDGER = _load_module(
    "atlas_rotation_state_ledger_for_readiness",
    ROOT / "rotation" / "rotation_state_ledger.py",
)


class RotationStateLedgerReadinessError(ValueError):
    pass


class RotationStateLedgerReadinessProvenanceError(RuntimeError):
    """Immutable frozen-input provenance failure.

    Raised for envelope shape, repository boundary, Git object access,
    trusted-ancestry, tree/blob identity, base64 and byte-equality failures --
    everything that happens BEFORE a single source byte is parsed.  It is
    deliberately NOT a ``RotationStateLedgerReadinessError`` (and not a
    ``ValueError`` at all), so no consumer's semantic-invalid handler can
    accidentally swallow it and render an unverifiable input as a validated
    diagnostic.  A dirty worktree, an unavailable Git object and a failed
    ``git`` invocation are hard failures here, never semantic-invalid
    inventory.
    """


class RotationStateLedgerReadinessSemanticError(RotationStateLedgerReadinessError):
    """Recomputed semantic failure of already-authenticated committed bytes.

    Reached only after every identity check has passed, so it means exactly
    one thing: the real committed inputs this repository proved it holds do
    not satisfy the readiness contract.  This is the only failure a consumer
    may map to a fixed generic diagnostic.
    """


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RotationStateLedgerReadinessError(f"JSON_READ_FAILED:{path}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": SCHEMA_VERSION,
        "markets": ["US", "KOREA", "CRYPTO"],
        "ledger_contract_version": "rotation_state_ledger/1",
        "repository_default_state_policy": "ABSENT",
        "repository_operational_ledger_evidence": "ABSENT",
        "market_rotation_evidence": {
            "US": None,
            "KOREA": "data/latest_korea_rotation.json",
            "CRYPTO": None,
        },
        "readiness_requirement": [
            "FULL_PRODUCTION_ROTATION_PACKET",
            "EXTERNAL_RATIFIED_STATE_POLICY",
            "APPEND_ONLY_OPERATIONAL_LEDGER_EVIDENCE",
        ],
        "authority": {
            "readiness_inventory_only": True,
            "p2_state_vocabulary_authorized": False,
            "state_ledger_authorized": False,
            "regime_input_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "briefing_wiring_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _contract_from_value(value) -> dict:
    """The one readiness-contract check, independent of where the bytes came
    from.  The live path feeds it the on-disk file; the frozen-input path
    feeds it the exact committed blob it has already authenticated."""
    if value != _expected_contract():
        raise RotationStateLedgerReadinessError("READINESS_CONTRACT_MISMATCH")
    return value


def _load_contract(root: Path) -> dict:
    return _contract_from_value(_read_json(root / "config" / CONTRACT_PATH.name))


def _date(value, code: str) -> str:
    if not isinstance(value, str):
        raise RotationStateLedgerReadinessError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise RotationStateLedgerReadinessError(code) from exc
    if parsed.isoformat() != value:
        raise RotationStateLedgerReadinessError(code)
    return value


def _timestamp(value, code: str) -> str:
    if not isinstance(value, str):
        raise RotationStateLedgerReadinessError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RotationStateLedgerReadinessError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RotationStateLedgerReadinessError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise RotationStateLedgerReadinessError(code)
    return value


def _git(root: Path, *args: str, binary: bool = False):
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RotationStateLedgerReadinessError(
            "EVIDENCE_GIT_PROVENANCE_UNVERIFIED"
        ) from exc
    return completed.stdout if binary else completed.stdout.decode("utf-8")


def _git_result(root: Path, *args: str):
    """Run git WITHOUT raising on a non-zero exit and return (code, stdout).

    Used only where a specific non-zero code is itself an answer -- "this
    object does not exist", "this commit is not an ancestor" -- so those two
    facts get their own explicit diagnostics instead of collapsing into the
    generic provenance-unverified code.  No git command anywhere in this
    module contacts a remote, so an unresolvable oid can never trigger a
    fetch of whatever a packet happens to name.
    """
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise RotationStateLedgerReadinessError(
            "EVIDENCE_GIT_PROVENANCE_UNVERIFIED"
        ) from exc
    return completed.returncode, completed.stdout.decode("utf-8", "replace")


def _verify_head_blob(path: Path, root: Path) -> str:
    path = Path(path).resolve()
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise RotationStateLedgerReadinessError("EVIDENCE_PATH_INVALID") from exc
    if _git(root, "status", "--porcelain", "--", relative).strip():
        raise RotationStateLedgerReadinessError("EVIDENCE_WORKTREE_DIRTY")
    head = _git(root, "rev-parse", "HEAD").strip()
    committed = _git(root, "show", f"{head}:{relative}", binary=True)
    try:
        current = path.read_bytes()
    except OSError as exc:
        raise RotationStateLedgerReadinessError("EVIDENCE_MISSING") from exc
    if current != committed:
        raise RotationStateLedgerReadinessError("EVIDENCE_HEAD_BLOB_MISMATCH")
    return head


def _validate_korea_pointer(value: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "as_of_date", "generated_at",
        "run_status", "rotation", "breadth", "authority", "payload_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RotationStateLedgerReadinessError("KOREA_POINTER_FIELDS_MISMATCH")
    digest = _sha(value.get("payload_sha256"), "KOREA_POINTER_SHA_INVALID")
    payload = copy.deepcopy(value)
    payload.pop("payload_sha256")
    if payload_sha256(payload) != digest:
        raise RotationStateLedgerReadinessError("KOREA_POINTER_SHA_MISMATCH")
    rotation = value.get("rotation")
    authority = value.get("authority")
    if (
        value.get("schema_version") != "korea_rotation_briefing_pointer/3"
        or value.get("contract_version") != "korea_capital_rotation/4"
        or value.get("run_status") != "OK"
        or not isinstance(rotation, dict)
        or set(rotation) != {"status", "rotation_policy_effective", "packet_sha256"}
        or rotation.get("status") != "ROTATION_BUCKETS_OBSERVED"
        or rotation.get("rotation_policy_effective") is not True
        or not isinstance(authority, dict)
        or not authority
        or any(item is not False for item in authority.values())
    ):
        raise RotationStateLedgerReadinessError("KOREA_POINTER_SEMANTIC_INVALID")
    _date(value.get("as_of_date"), "KOREA_POINTER_DATE_INVALID")
    _timestamp(value.get("generated_at"), "KOREA_POINTER_TIME_INVALID")
    _sha(rotation.get("packet_sha256"), "KOREA_ROTATION_PACKET_SHA_INVALID")
    return copy.deepcopy(value)


def _market_row(market: str, contract: dict, root: Path) -> dict:
    pointer_rel = contract["market_rotation_evidence"][market]
    if pointer_rel is None:
        upstream_status = "ROTATION_EVIDENCE_NOT_COMMITTED"
        pointer_sha = rotation_packet_sha = rotation_as_of = None
        pointer_commit = None
    else:
        pointer_path = root / pointer_rel
        pointer_commit = _verify_head_blob(pointer_path, root)
        pointer = _validate_korea_pointer(_read_json(pointer_path))
        upstream_status = "POINTER_ONLY_FULL_ROTATION_PACKET_NOT_COMMITTED"
        pointer_sha = pointer["payload_sha256"]
        rotation_packet_sha = pointer["rotation"]["packet_sha256"]
        rotation_as_of = pointer["as_of_date"]
    blockers = list(MARKET_BLOCKERS)
    return {
        "market": market,
        "readiness_status": "NOT_READY",
        "upstream_rotation_evidence_status": upstream_status,
        "upstream_pointer_path": pointer_rel,
        "upstream_pointer_commit": pointer_commit,
        "upstream_pointer_sha256": pointer_sha,
        "upstream_rotation_packet_sha256": rotation_packet_sha,
        "upstream_rotation_as_of_date": rotation_as_of,
        "state_policy_status": "ABSENT_BY_REPOSITORY_CONTRACT",
        "ledger_evidence_status": "ABSENT",
        "ledger_record_count": 0,
        "blockers": blockers,
    }


def build_readiness(root: Path = ROOT) -> dict:
    root = Path(root).resolve()
    contract = _load_contract(root)
    ledger_contract = LEDGER.load_contract(root / "config" / LEDGER_CONTRACT_PATH.name)
    if (
        ledger_contract["contract_version"] != contract["ledger_contract_version"]
        or ledger_contract["repository_default_policy"] != "ABSENT"
    ):
        raise RotationStateLedgerReadinessError("LEDGER_CONTRACT_BOUNDARY_MISMATCH")
    markets = [_market_row(market, contract, root) for market in contract["markets"]]
    packet = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "evidence_basis": "REPOSITORY_COMMITTED_EVIDENCE_ONLY",
        "overall_status": "BLOCKED_NO_MARKET_HAS_OPERATIONAL_STATE_HISTORY",
        "ready_market_count": 0,
        "required_market_count": len(markets),
        "markets": markets,
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_readiness(value: dict, root: Path = ROOT) -> dict:
    expected = build_readiness(root)
    if value != expected:
        raise RotationStateLedgerReadinessError("READINESS_REDERIVATION_MISMATCH")
    return copy.deepcopy(value)


# ---------------------------------------------------------------------------
# Immutable, Git-backed frozen readiness inputs
#
# build_readiness()/validate_readiness() above stay exactly what they were: a
# LIVE command that reads today's working tree, enforces the committed-HEAD and
# dirty-file checks, and reports today's inventory.  That is the right shape
# for a command, and the wrong shape for a consumer that must still be able to
# re-derive an ARCHIVED verdict years later: the Korea pointer is a mutable
# rolling file, so re-reading it at validation time makes an honest old packet's
# verdict depend on when it is validated.
#
# The block below adds the missing half without weakening the first: a consumer
# freezes the exact committed BYTES of the three required inputs plus the commit
# they came from, and every later replay proves that frozen tuple against real
# Git objects -- trusted repository boundary, then trusted ancestry, then the
# commit tree, then blob oid, then a recomputed Git blob hash, then a raw byte
# comparison -- BEFORE a single byte is parsed.  Only after all of that does the
# pure semantic stage run, and it runs the same contract, ledger-boundary and
# Korea-pointer validators the live producer uses, never a weaker copy.
#
# The two failure classes are kept rigidly apart:
#   * provenance/envelope/Git/dirty/missing-object  -> hard failure
#     (RotationStateLedgerReadinessProvenanceError), never a diagnostic;
#   * recomputed semantics over authenticated bytes -> semantic failure
#     (RotationStateLedgerReadinessSemanticError), which a consumer may map to
#     one fixed generic diagnostic.
#
# Accepted limitation, stated plainly: this proves the frozen tuple is a real,
# complete, trusted historical source state of THIS repository.  It is not a
# producer signature.  Someone holding the repository could replace the whole
# tuple with a different, genuinely trusted historical commit and its exact
# blobs; that is a different valid source lineage, not a forgery of this one,
# and nothing here claims to distinguish issuers.
# ---------------------------------------------------------------------------


def _provenance(code: str, detail: str = "") -> RotationStateLedgerReadinessProvenanceError:
    return RotationStateLedgerReadinessProvenanceError(
        f"{code}:{detail}" if detail else code
    )


def _require(condition, code: str, detail: str = "") -> None:
    if not condition:
        raise _provenance(code, detail)


def _git_blob_oid(data: bytes) -> str:
    """The Git object id of ``data`` as a loose blob, computed here rather than
    asked of git, so the stored bytes are checked against the stored oid
    independently of whatever the repository would answer."""
    header = f"blob {len(data)}".encode("ascii") + b"\0"
    return hashlib.sha1(header + data).hexdigest()


def _strict_b64decode(value: str, relative: str) -> bytes:
    try:
        data = base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise _provenance("FROZEN_INPUT_CONTENT_BASE64_INVALID", relative) from exc
    # Canonical form only: reject alternate padding/trailing-bit spellings that
    # decode to the same bytes but are not what a capture would have written.
    if base64.b64encode(data).decode("ascii") != value:
        raise _provenance("FROZEN_INPUT_CONTENT_BASE64_NOT_CANONICAL", relative)
    return data


def _require_repository_boundary(root: Path) -> None:
    """Prove ``root`` is the top of a real Git work tree before any object
    lookup.  The trusted anchor is this local repository -- a packet can name
    neither a repository, a ref, a remote, nor a validation HEAD."""
    code, out = _git_result(root, "rev-parse", "--show-toplevel")
    _require(code == 0, "SOURCE_REPOSITORY_BOUNDARY_UNVERIFIED", str(root))
    top = out.strip()
    _require(bool(top), "SOURCE_REPOSITORY_BOUNDARY_UNVERIFIED", str(root))
    _require(
        Path(top).resolve() == root,
        "SOURCE_REPOSITORY_BOUNDARY_MISMATCH",
        f"{top}!={root}",
    )


def _trusted_validation_head(root: Path) -> str:
    """The locally configured trusted validation HEAD.  Read from the
    repository, never from the packet."""
    code, out = _git_result(root, "rev-parse", "HEAD")
    _require(code == 0, "TRUSTED_VALIDATION_HEAD_UNRESOLVED", str(root))
    head = out.strip()
    _require(
        GIT_OID_RE.fullmatch(head) is not None,
        "TRUSTED_VALIDATION_HEAD_INVALID",
        head,
    )
    return head


def _tree_entry(root: Path, commit: str, relative: str):
    """``(mode, type, oid)`` for ``relative`` in ``commit``'s tree, or None if
    the tree genuinely does not contain that path."""
    code, out = _git_result(
        root, "ls-tree", "-z", "--full-tree", f"{commit}^{{tree}}", "--", relative
    )
    _require(code == 0, "FROZEN_INPUT_TREE_UNREADABLE", relative)
    records = [record for record in out.split("\0") if record]
    if not records:
        return None
    _require(len(records) == 1, "FROZEN_INPUT_TREE_ENTRY_AMBIGUOUS", relative)
    meta, separator, path = records[0].partition("\t")
    _require(separator == "\t" and path == relative, "FROZEN_INPUT_TREE_PATH_MISMATCH", relative)
    fields = meta.split()
    _require(len(fields) == 3, "FROZEN_INPUT_TREE_ENTRY_INVALID", relative)
    mode, object_type, oid = fields
    _require(GIT_OID_RE.fullmatch(oid) is not None, "FROZEN_INPUT_TREE_OID_INVALID", relative)
    return mode, object_type, oid


def _verify_frozen_file(root: Path, commit: str, relative: str, entry):
    """Return the authenticated raw bytes for ``relative``, or None if the
    commit tree proves the path was genuinely absent."""
    _require(
        isinstance(entry, dict) and set(entry) == FROZEN_INPUT_FILE_KEYS,
        "FROZEN_INPUT_FILE_FIELDS_MISMATCH",
        relative,
    )
    state = entry["state"]
    blob_oid = entry["blob_oid"]
    content_base64 = entry["content_base64"]
    _require(state in FROZEN_INPUT_STATES, "FROZEN_INPUT_STATE_INVALID", relative)
    tree = _tree_entry(root, commit, relative)
    if state == "ABSENT":
        # An ABSENT tag cannot hide a committed entry of ANY kind -- blob,
        # tree, symlink or submodule.
        _require(tree is None, "FROZEN_INPUT_ABSENT_HIDES_COMMITTED_ENTRY", relative)
        _require(
            blob_oid is None and content_base64 is None,
            "FROZEN_INPUT_ABSENT_FIELDS_INVALID",
            relative,
        )
        return None
    _require(tree is not None, "FROZEN_INPUT_PRESENT_NOT_IN_COMMIT_TREE", relative)
    mode, object_type, tree_oid = tree
    _require(
        object_type == "blob" and mode in GIT_BLOB_MODES,
        "FROZEN_INPUT_BLOB_MODE_INVALID",
        f"{relative}:{mode}:{object_type}",
    )
    _require(
        isinstance(blob_oid, str) and GIT_OID_RE.fullmatch(blob_oid) is not None,
        "FROZEN_INPUT_BLOB_OID_INVALID",
        relative,
    )
    _require(blob_oid == tree_oid, "FROZEN_INPUT_BLOB_OID_MISMATCH", relative)
    _require(isinstance(content_base64, str), "FROZEN_INPUT_CONTENT_BASE64_INVALID", relative)
    data = _strict_b64decode(content_base64, relative)
    _require(_git_blob_oid(data) == tree_oid, "FROZEN_INPUT_BLOB_HASH_MISMATCH", relative)
    committed = _git(root, "cat-file", "blob", tree_oid, binary=True)
    _require(data == committed, "FROZEN_INPUT_BLOB_BYTES_MISMATCH", relative)
    return data


def _verify_readiness_inputs(envelope, root: Path) -> dict:
    _require(isinstance(envelope, dict), "FROZEN_INPUT_ENVELOPE_INVALID", type(envelope).__name__)
    _require(
        set(envelope) == FROZEN_INPUT_ENVELOPE_KEYS,
        "FROZEN_INPUT_ENVELOPE_FIELDS_MISMATCH",
        str(sorted(envelope)),
    )
    _require(
        envelope["schema_version"] == FROZEN_INPUT_SCHEMA_VERSION,
        "FROZEN_INPUT_SCHEMA_VERSION_INVALID",
        repr(envelope["schema_version"]),
    )
    commit = envelope["source_commit"]
    _require(
        isinstance(commit, str) and GIT_OID_RE.fullmatch(commit) is not None,
        "FROZEN_INPUT_SOURCE_COMMIT_INVALID",
        repr(commit),
    )
    files = envelope["files"]
    _require(
        isinstance(files, dict) and set(files) == set(FROZEN_INPUT_PATHS),
        "FROZEN_INPUT_FILE_KEYS_MISMATCH",
        str(sorted(files)) if isinstance(files, dict) else type(files).__name__,
    )

    _require_repository_boundary(root)
    trusted_head = _trusted_validation_head(root)
    # Object existence first, so "this repository has never held that object"
    # is reported as itself instead of as a failed ancestry test.
    code, _ = _git_result(root, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
    _require(code == 0, "FROZEN_INPUT_SOURCE_COMMIT_OBJECT_MISSING", commit)
    code, _ = _git_result(root, "merge-base", "--is-ancestor", commit, trusted_head)
    if code == 1:
        raise _provenance("FROZEN_INPUT_SOURCE_COMMIT_NOT_TRUSTED_ANCESTOR", commit)
    _require(code == 0, "EVIDENCE_GIT_PROVENANCE_UNVERIFIED", "merge-base")

    return {
        relative: _verify_frozen_file(root, commit, relative, files[relative])
        for relative in FROZEN_INPUT_PATHS
    }


def verify_readiness_inputs(envelope, root: Path = ROOT) -> dict:
    """Authenticate a frozen input envelope and return its raw source bytes.

    Every failure here is a hard failure.  The blanket re-raise is deliberate:
    an unexpected error while PROVING provenance must never be indistinguishable
    from a validated-but-semantically-invalid input.
    """
    try:
        return _verify_readiness_inputs(envelope, Path(root).resolve())
    except RotationStateLedgerReadinessProvenanceError:
        raise
    except Exception as exc:  # noqa: BLE001 - unprovable input is a hard failure
        raise _provenance(
            "FROZEN_INPUT_PROVENANCE_UNVERIFIED", f"{type(exc).__name__}:{exc}"
        ) from exc


def _capture_frozen_file(root: Path, head: str, relative: str) -> dict:
    # The existing live checks stay mandatory: nothing dirty or uncommitted is
    # ever frozen, even when the resulting bytes would be semantically invalid.
    if _git(root, "status", "--porcelain", "--", relative).strip():
        raise _provenance("EVIDENCE_WORKTREE_DIRTY", relative)
    path = root / relative
    tree = _tree_entry(root, head, relative)
    if tree is None:
        _require(
            not (path.exists() or path.is_symlink()),
            "EVIDENCE_UNCOMMITTED",
            relative,
        )
        return {"state": "ABSENT", "blob_oid": None, "content_base64": None}
    mode, object_type, oid = tree
    _require(
        object_type == "blob" and mode in GIT_BLOB_MODES,
        "FROZEN_INPUT_BLOB_MODE_INVALID",
        f"{relative}:{mode}:{object_type}",
    )
    committed = _git(root, "cat-file", "blob", oid, binary=True)
    try:
        live = path.read_bytes()
    except OSError as exc:
        raise _provenance("EVIDENCE_MISSING", relative) from exc
    _require(live == committed, "EVIDENCE_HEAD_BLOB_MISMATCH", relative)
    _require(_git_blob_oid(committed) == oid, "FROZEN_INPUT_BLOB_HASH_MISMATCH", relative)
    return {
        "state": "PRESENT",
        "blob_oid": oid,
        "content_base64": base64.b64encode(committed).decode("ascii"),
    }


def capture_readiness_inputs(root: Path = ROOT) -> dict:
    """Freeze the three required inputs at the repository's current HEAD.

    Bytes are frozen even when they are semantically invalid -- but only after
    object identity is proven -- so a genuinely broken committed input replays
    as the same deterministic verdict instead of silently recapturing later.
    """
    try:
        root = Path(root).resolve()
        _require_repository_boundary(root)
        head = _trusted_validation_head(root)
        envelope = {
            "schema_version": FROZEN_INPUT_SCHEMA_VERSION,
            "source_commit": head,
            "files": {
                relative: _capture_frozen_file(root, head, relative)
                for relative in FROZEN_INPUT_PATHS
            },
        }
    except RotationStateLedgerReadinessProvenanceError:
        raise
    except Exception as exc:  # noqa: BLE001 - an unprovable capture is a hard failure
        raise _provenance(
            "FROZEN_INPUT_CAPTURE_FAILED", f"{type(exc).__name__}:{exc}"
        ) from exc
    # Never trust a capture more than a replay: prove the envelope through the
    # exact verification path every later consumer will use.
    verify_readiness_inputs(envelope, root)
    return envelope


def _decode_source_json(data, relative: str):
    if data is None:
        raise RotationStateLedgerReadinessSemanticError(f"EVIDENCE_MISSING:{relative}")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RotationStateLedgerReadinessSemanticError(
            f"JSON_READ_FAILED:{relative}"
        ) from exc


def _rederive_inventory(sources: dict) -> dict:
    """Pure semantic rederivation from authenticated raw source bytes.

    Reads no file, runs no git command and consults no clock: the only inputs
    are the byte strings already proven to be the pinned commit's blobs.  The
    checks themselves are the live producer's own -- the same contract
    equality, the same ledger contract validator and boundary, and the same
    Korea pointer field/self-hash/semantic validation.  Market rows are derived
    anew here rather than copied from any persisted output.
    """
    contract = _contract_from_value(
        _decode_source_json(sources[READINESS_CONTRACT_REL], READINESS_CONTRACT_REL)
    )
    # LEDGER.load_contract() is exactly _validate_contract(_read_json(path));
    # this is the same validator with the path stage removed, not a weaker one.
    ledger_contract = LEDGER._validate_contract(
        _decode_source_json(sources[LEDGER_CONTRACT_REL], LEDGER_CONTRACT_REL)
    )
    if (
        ledger_contract["contract_version"] != contract["ledger_contract_version"]
        or ledger_contract["repository_default_policy"] != "ABSENT"
    ):
        raise RotationStateLedgerReadinessError("LEDGER_CONTRACT_BOUNDARY_MISMATCH")
    markets = []
    for market in contract["markets"]:
        pointer_rel = contract["market_rotation_evidence"][market]
        if pointer_rel is not None:
            if pointer_rel not in sources:
                raise RotationStateLedgerReadinessError(
                    f"ROTATION_EVIDENCE_PATH_NOT_FROZEN:{market}"
                )
            _validate_korea_pointer(_decode_source_json(sources[pointer_rel], pointer_rel))
        markets.append({"market": market, "blockers": list(MARKET_BLOCKERS)})
    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "evidence_basis": "REPOSITORY_COMMITTED_EVIDENCE_ONLY",
        "overall_status": "BLOCKED_NO_MARKET_HAS_OPERATIONAL_STATE_HISTORY",
        "markets": markets,
        "authority": copy.deepcopy(contract["authority"]),
    }


def evaluate_frozen_readiness_inputs(envelope, root: Path = ROOT) -> dict:
    """Authenticate a frozen envelope, then rederive the readiness inventory.

    Carries no commit, blob oid, base64, hash, path, age or timestamp into its
    result: a consumer that fingerprints this inventory sees the semantics
    only, so an unchanged repository state produces an unchanged fingerprint
    across commits and invocations.
    """
    sources = verify_readiness_inputs(envelope, root)
    try:
        return _rederive_inventory(sources)
    except RotationStateLedgerReadinessSemanticError:
        raise
    except ValueError as exc:
        # Both this module's and the ledger module's validators raise
        # ValueError subclasses; either one means authenticated bytes failed
        # their own contract.
        raise RotationStateLedgerReadinessSemanticError(
            f"READINESS_INPUT_SEMANTIC_INVALID:{type(exc).__name__}:{exc}"
        ) from exc


def write_json_atomic(path: Path, value: dict, root: Path = ROOT) -> None:
    path = Path(path).resolve()
    try:
        path.relative_to(Path(root).resolve())
    except ValueError:
        pass
    else:
        raise RotationStateLedgerReadinessError("TRACKED_OUTPUT_FORBIDDEN")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    packet = build_readiness()
    validate_readiness(packet)
    write_json_atomic(args.out, packet)
    print(
        "rotation state ledger readiness: "
        f"status={packet['overall_status']} ready=0/{packet['required_market_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
