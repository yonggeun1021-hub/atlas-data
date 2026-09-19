#!/usr/bin/env python3
"""Explicit-file caller for the existing GAM theme ingestion application.

This is the missing callable connection between five caller-named original JSON
files and the already accepted library capabilities
``validate_theme_ingestion_preview()`` and ``apply_theme_ingestion_preview()``.
Neither of those is modified, reimplemented or weakened here, and no preview is
constructed by this module: a reviewed preview is a required input file.

Every input file is named explicitly on the command line together with an
externally supplied SHA256 of its exact raw bytes.  Nothing is discovered,
defaulted, unwrapped from an envelope, reduced, inferred from the working
directory or derived from the contents of another input.  An expected digest
that came out of an input file would only attest to itself, so no expected
digest is ever read from one.

Validation-only is the default and touches no destination at all.  ``--apply``
additionally requires an explicitly named existing destination and its expected
previous master ``payload_sha256``, and calls the existing guarded application
with ``operational_application_approved=True``.  That flag expresses this
caller's explicit action and nothing else: it does not populate a graph, ratify
Theme Authority, or change any authority output, all of which remain exactly as
the existing library reports them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from universe import global_asset_master as GAM
from universe import global_asset_master_theme_ingestion as INGESTION

SCHEMA = "global_asset_master_theme_application_cli_result/1"

# Each input is one standalone original document, named by its own flag and
# verified against its own externally supplied digest.
INPUT_FILES = (
    ("preview", "--preview"),
    ("master_source", "--master-source"),
    ("taxonomy_source", "--taxonomy-source"),
    ("requests", "--requests"),
    ("authority_registry", "--authority-registry"),
)
ERROR_LIMIT = 300


class ApplicationCliError(ValueError):
    """Explicit-file caller precondition failure raised before any library call."""


def _concise(value) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= ERROR_LIMIT else text[:ERROR_LIMIT] + "...[truncated]"


def _object_pairs(name):
    def hook(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ApplicationCliError(f"INPUT_JSON_DUPLICATE_KEY:{name}:{key}")
            result[key] = value
        return result

    return hook


def _float(name):
    def hook(text):
        value = float(text)
        if not math.isfinite(value):
            raise ApplicationCliError(f"INPUT_JSON_NONFINITE_NUMBER:{name}:{text}")
        return value

    return hook


def _constant(name):
    def hook(text):
        raise ApplicationCliError(f"INPUT_JSON_NONFINITE_NUMBER:{name}:{text}")

    return hook


def _read_verified_json(path, expected_sha256: str, name: str):
    """Read one file's exact bytes once, verify the external digest, then parse.

    The digest is checked before the bytes are decoded or interpreted as JSON,
    so a file that does not match what the caller pinned is never parsed.
    ``NaN``/``Infinity`` literals and values that overflow to an infinity are
    both rejected, as are duplicate object keys, which JSON itself would
    silently resolve last-writer-wins.
    """
    if not isinstance(expected_sha256, str) or GAM.SHA256_RE.fullmatch(expected_sha256) is None:
        raise ApplicationCliError(f"INPUT_EXPECTED_SHA256_INVALID:{name}")
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ApplicationCliError(f"INPUT_FILE_UNREADABLE:{name}:{_concise(exc)}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise ApplicationCliError(
            f"INPUT_FILE_SHA256_MISMATCH:{name}:{expected_sha256}:{digest}"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApplicationCliError(f"INPUT_FILE_NOT_UTF8:{name}:{_concise(exc)}") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_pairs(name),
            parse_float=_float(name),
            parse_constant=_constant(name),
        )
    except json.JSONDecodeError as exc:
        raise ApplicationCliError(f"INPUT_JSON_MALFORMED:{name}:{_concise(exc)}") from exc
    return value, digest


class _Parser(argparse.ArgumentParser):
    """Argument errors are caller failures, not process exits inside ``run()``."""

    def error(self, message):
        raise ApplicationCliError(f"ARGUMENTS_INVALID:{_concise(message)}")


def _build_parser() -> _Parser:
    parser = _Parser(description=__doc__)
    for name, flag in INPUT_FILES:
        parser.add_argument(flag, dest=name, required=True, type=Path)
        parser.add_argument(f"{flag}-sha256", dest=f"{name}_sha256", required=True)
    parser.add_argument("--trusted-commit", dest="trusted_commit", required=True)
    parser.add_argument("--apply", dest="apply", action="store_true")
    parser.add_argument("--destination", dest="destination", type=Path)
    parser.add_argument(
        "--expected-previous-master-sha256", dest="expected_previous_master_sha256"
    )
    return parser


def _parse_args(argv):
    args = _build_parser().parse_args(argv)
    if GAM.TRUSTED_COMMIT_RE.fullmatch(args.trusted_commit or "") is None:
        # A branch, tag or moving reference is not an immutable authority pin.
        raise ApplicationCliError("TRUSTED_COMMIT_INVALID")
    destination_arguments = (args.destination, args.expected_previous_master_sha256)
    if args.apply:
        if args.destination is None:
            raise ApplicationCliError("APPLY_DESTINATION_REQUIRED")
        if args.expected_previous_master_sha256 is None:
            raise ApplicationCliError("APPLY_EXPECTED_PREVIOUS_MASTER_SHA256_REQUIRED")
        if GAM.SHA256_RE.fullmatch(args.expected_previous_master_sha256) is None:
            raise ApplicationCliError("APPLY_EXPECTED_PREVIOUS_MASTER_SHA256_INVALID")
    elif any(value is not None for value in destination_arguments):
        # Accepting them silently would imply a destination was considered.
        raise ApplicationCliError("DESTINATION_ARGUMENTS_REQUIRE_APPLY")
    return args


def _load_inputs(args):
    loaded, digests = {}, {}
    for name, _flag in INPUT_FILES:
        loaded[name], digests[name] = _read_verified_json(
            getattr(args, name), getattr(args, f"{name}_sha256"), name
        )
    return loaded, digests


def _bound_registry_path(path, expected_sha256, trusted_commit):
    """Bind the CLI's observed digest to the library's immutable registry read.

    The library independently reads its registry path and requires the exact
    committed bytes. Checking that same commit here connects those bytes to the
    external file digest, even if the working file changes between reads. Pass
    the resolved path onward so a changed symlink cannot select another file.
    """
    environment = dict(os.environ, GIT_NO_LAZY_FETCH="1")
    try:
        registry = Path(path).resolve(strict=True)
        root = Path(subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], cwd=registry.parent,
            env=environment, stderr=subprocess.PIPE, timeout=5,
        ).decode("utf-8").strip()).resolve()
        relative = registry.relative_to(root).as_posix()
        committed = subprocess.check_output(
            ["git", "show", f"{trusted_commit}:{relative}"], cwd=root,
            env=environment, stderr=subprocess.PIPE, timeout=5,
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        raise ApplicationCliError("AUTHORITY_REGISTRY_COMMIT_BINDING_UNAVAILABLE") from None
    if hashlib.sha256(committed).hexdigest() != expected_sha256:
        raise ApplicationCliError("AUTHORITY_REGISTRY_FILE_COMMIT_DIGEST_MISMATCH")
    return registry


def _validation_result(preview, digests, args):
    binding_status = preview["binding_report"]["status"]
    applicable = (
        preview["status"] == "STRUCTURAL_PREVIEW"
        and not preview["failure_reasons"]
        and preview["candidate_master"] is not None
        and binding_status == "THEME_SOURCE_BINDING_VERIFIED"
    )
    result = {
        "schema_version": SCHEMA,
        "mode": "VALIDATE_ONLY",
        "status": "PREVIEW_REVALIDATED" if applicable else "PREVIEW_NOT_APPLICABLE",
        "applied": False,
        "destination_checked": False,
        "preview": {
            "payload_sha256": preview["payload_sha256"],
            "status": preview["status"],
            "change": preview["change"],
            "addition_count": preview["addition_count"],
            "unchanged_count": preview["unchanged_count"],
            "binding_status": binding_status,
            "master_id": preview["binding_report"]["master_id"],
            "as_of_date": preview["binding_report"]["as_of_date"],
            "failure_reasons": list(preview["failure_reasons"]),
        },
        "preview_input_digests": dict(preview["input_digests"]),
        "input_file_sha256": dict(digests),
        "trusted_commit": args.trusted_commit,
        "notes": [
            "DESTINATION_APPLICABILITY_NOT_CHECKED",
            "NO_FILE_CREATED_OR_MODIFIED",
            "NOT_AN_OPERATIONAL_ADMISSION",
        ],
    }
    if applicable:
        return result, 0
    result["error"] = f"PREVIEW_NOT_APPLICABLE:{preview['status']}:{binding_status}"
    return result, 1


def _apply_result(outcome, preview, digests, args):
    return {
        "schema_version": SCHEMA,
        "mode": "APPLY",
        "status": "APPLIED",
        "applied": True,
        "outcome": outcome["outcome"],
        "change": outcome["change"],
        "published": outcome["published"],
        "destination_path": outcome["destination_path"],
        "addition_count": outcome["addition_count"],
        "unchanged_count": outcome["unchanged_count"],
        "previous_master": dict(outcome["previous_master"]),
        "master": dict(outcome["master"]),
        # Revalidated inside the guarded application, so this is the digest of
        # the preview it actually recomputed, not an unchecked caller claim.
        "preview_payload_sha256": preview["payload_sha256"],
        "expected_previous_master_sha256": args.expected_previous_master_sha256,
        "input_file_sha256": dict(digests),
        "trusted_commit": args.trusted_commit,
        "notes": [
            "APPROVAL_FLAG_IS_THIS_CALLER_ACTION_ONLY",
            "NO_AUTHORITY_OUTPUT_CHANGED",
            "NOT_AN_OPERATIONAL_ADMISSION",
        ],
    }


def _failure_result(mode, exc, digests, args, attempted: bool):
    if mode == "APPLY" and attempted:
        # This caller cannot observe whether the single atomic publish inside
        # the library already replaced the destination, so it never reports a
        # rollback and never repeats the call.
        destination_state = "REQUIRES_INSPECTION"
        notes = [
            "APPLICATION_EXCEPTION_IS_NOT_PROOF_OF_ROLLBACK",
            "INSPECT_DESTINATION_BEFORE_ANY_FURTHER_ACTION",
            "NO_AUTOMATIC_RETRY_PERFORMED",
        ]
    elif mode == "APPLY":
        destination_state = "NOT_REACHED_NO_APPLICATION_ATTEMPTED"
        notes = ["NO_APPLICATION_CALL_WAS_MADE", "NO_AUTOMATIC_RETRY_PERFORMED"]
    elif mode == "VALIDATE_ONLY":
        destination_state = "NOT_USED_IN_VALIDATION_ONLY_MODE"
        notes = ["NO_FILE_CREATED_OR_MODIFIED", "NOT_AN_OPERATIONAL_ADMISSION"]
    else:
        destination_state = "NO_MODE_SELECTED_ARGUMENTS_REJECTED"
        notes = ["NO_INPUT_WAS_READ", "NO_APPLICATION_CALL_WAS_MADE"]
    return {
        "schema_version": SCHEMA,
        "mode": mode,
        "status": "FAILED",
        "applied": None if attempted else False,
        "error": _concise(exc),
        "error_type": type(exc).__name__,
        "destination_state": destination_state,
        "input_file_sha256": dict(digests),
        "trusted_commit": getattr(args, "trusted_commit", None),
        "notes": notes,
    }


def run(argv=None, *, stdout=None, stderr=None) -> int:
    """Execute one explicit-file validation or application and return an exit code."""
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    mode, digests, args, attempted = "UNKNOWN", {}, None, False
    try:
        args = _parse_args(argv)
        mode = "APPLY" if args.apply else "VALIDATE_ONLY"
        loaded, digests = _load_inputs(args)
        registry_path = _bound_registry_path(
            args.authority_registry, digests["authority_registry"], args.trusted_commit,
        )
        call = {
            "preview": loaded["preview"],
            "master_source": loaded["master_source"],
            "taxonomy_source": loaded["taxonomy_source"],
            "requests": loaded["requests"],
            "trusted_commit": args.trusted_commit,
            "authority_registry_path": registry_path,
        }
        if args.apply:
            attempted = True
            outcome = INGESTION.apply_theme_ingestion_preview(
                **call,
                destination_path=args.destination,
                expected_previous_master_sha256=args.expected_previous_master_sha256,
                operational_application_approved=True,
            )
            result, code = _apply_result(outcome, loaded["preview"], digests, args), 0
        else:
            recomputed = INGESTION.validate_theme_ingestion_preview(**call)
            result, code = _validation_result(recomputed, digests, args)
    except Exception as exc:  # every failure is one compact JSON line, never a traceback
        failure = _failure_result(mode, exc, digests, args, attempted)
        print(GAM.canonical_json(failure), file=out)
        print(f"gam theme application failed: {failure['error']}", file=err)
        return 1
    print(GAM.canonical_json(result), file=out)
    if code:
        print(f"gam theme application failed: {result['error']}", file=err)
    return code


def main(argv=None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
