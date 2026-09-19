#!/usr/bin/env python3
"""Fail-closed controller helpers for the scheduled P2-03 pair handoff.

This module does not fetch KRX data and does not dispatch a workflow.  It
only decides whether an already discovered session pair is safe to send to
the dependency-ordered producer and validates a prior producer artifact
before that artifact may suppress a retry.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DAY8 = re.compile(r"\d{8}")
COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
ACTIVE_RUN_STATUSES = {"queued", "in_progress", "waiting", "pending", "requested"}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_LOAD_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEDGER = _load(
    "korea_capital_rotation_ledger_proof_for_pair_controller",
    ROOT / ".github" / "scripts" / "korea_capital_rotation_ledger_proof.py",
)
KCR = _load(
    "korea_capital_rotation_for_pair_controller",
    ROOT / "rotation" / "korea_capital_rotation.py",
)


def _day8(value: str, label: str) -> tuple[str, str, dt.date]:
    if not isinstance(value, str) or DAY8.fullmatch(value) is None:
        raise RuntimeError(f"{label}_INVALID")
    parsed = dt.date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    return value, parsed.isoformat(), parsed


def request_readiness(prior_date: str, current_date: str, root: Path = ROOT) -> dict:
    """Return whether the combined producer can safely own this exact pair.

    Existing Leadership without an already eligible Breadth observation is a
    permanent chronology conflict for that date: fetching Breadth later must
    not be presented as if it preceded Leadership's decision time.  When no
    current Leadership packet exists, the combined workflow can safely create
    Breadth first and Leadership second.
    """
    _, prior_iso, prior = _day8(prior_date, "PRIOR_DATE")
    _, current_iso, current = _day8(current_date, "CURRENT_DATE")
    if prior >= current:
        raise RuntimeError("SESSION_PAIR_ORDER_INVALID")

    binding, policy = LEDGER.load_current_ratified_artifacts()
    effective_from = dt.date.fromisoformat(policy["effective_from"])
    effective_to = (
        None
        if policy["effective_to"] is None
        else dt.date.fromisoformat(policy["effective_to"])
    )
    if prior < effective_from:
        seed_current = current >= effective_from and (
            effective_to is None or current < effective_to
        )
        return {
            "call_ready": False,
            "seed_current_leadership": seed_current,
            "status": (
                "SEED_EFFECTIVE_DATE_LEADERSHIP_CONTEXT"
                if seed_current
                else "WAIT_POLICY_NOT_EFFECTIVE_FOR_PAIR"
            ),
            "prior_date": prior_iso,
            "current_date": current_iso,
            "policy_id": policy["policy_id"],
            "rotation_policy_sha256": KCR.payload_sha256(policy),
            "upstream_leadership_policy_sha256": binding[
                "upstream_leadership_policy_sha256"
            ],
        }
    if effective_to is not None and current >= effective_to:
        return {
            "call_ready": False,
            "seed_current_leadership": False,
            "status": "WAIT_POLICY_NOT_EFFECTIVE_FOR_PAIR",
            "prior_date": prior_iso,
            "current_date": current_iso,
            "policy_id": policy["policy_id"],
            "rotation_policy_sha256": KCR.payload_sha256(policy),
            "upstream_leadership_policy_sha256": binding[
                "upstream_leadership_policy_sha256"
            ],
        }

    breadth_path = (
        root / "data" / "observations" / "korea_breadth_context"
        / current_iso / "packet.json"
    )
    leadership_path = (
        root / "data" / "observations" / "korea_leadership_context"
        / current_iso / "packet.json"
    )
    if not leadership_path.is_file():
        return {
            "call_ready": True,
            "seed_current_leadership": False,
            "status": (
                "CALL_ORDERED_CAPTURE_WITH_EXISTING_BREADTH"
                if breadth_path.is_file()
                else "CALL_ORDERED_CAPTURE"
            ),
            "prior_date": prior_iso,
            "current_date": current_iso,
            "policy_id": policy["policy_id"],
            "rotation_policy_sha256": KCR.payload_sha256(policy),
            "upstream_leadership_policy_sha256": binding[
                "upstream_leadership_policy_sha256"
            ],
        }
    if not breadth_path.is_file():
        return {
            "call_ready": False,
            "seed_current_leadership": False,
            "status": "WAIT_EXISTING_LEADERSHIP_PRECEDES_MISSING_BREADTH",
            "prior_date": prior_iso,
            "current_date": current_iso,
            "policy_id": policy["policy_id"],
            "rotation_policy_sha256": KCR.payload_sha256(policy),
            "upstream_leadership_policy_sha256": binding[
                "upstream_leadership_policy_sha256"
            ],
        }

    try:
        packet = LEDGER.build_current_ratified_packet(prior_iso, current_iso)
    except Exception as exc:  # exact producer owns the detailed fail-closed reason
        return {
            "call_ready": False,
            "seed_current_leadership": False,
            "status": "WAIT_EXISTING_PAIR_NOT_PRODUCER_ELIGIBLE",
            "reason": f"{type(exc).__name__}:{exc}",
            "prior_date": prior_iso,
            "current_date": current_iso,
            "policy_id": policy["policy_id"],
            "rotation_policy_sha256": KCR.payload_sha256(policy),
            "upstream_leadership_policy_sha256": binding[
                "upstream_leadership_policy_sha256"
            ],
        }
    if (
        packet["status"] != "ROTATION_BUCKETS_OBSERVED"
        or packet["rotation_policy_effective"] is not True
    ):
        return {
            "call_ready": False,
            "seed_current_leadership": False,
            "status": "WAIT_EXISTING_PAIR_NOT_PRODUCER_ELIGIBLE",
            "prior_date": prior_iso,
            "current_date": current_iso,
            "policy_id": policy["policy_id"],
            "rotation_policy_sha256": KCR.payload_sha256(policy),
            "upstream_leadership_policy_sha256": binding[
                "upstream_leadership_policy_sha256"
            ],
        }
    return {
        "call_ready": True,
        "seed_current_leadership": False,
        "status": "CALL_EXISTING_ELIGIBLE_PAIR",
        "prior_date": prior_iso,
        "current_date": current_iso,
        "policy_id": policy["policy_id"],
        "rotation_policy_sha256": packet["lineage"]["rotation_policy_sha256"],
        "upstream_leadership_policy_sha256": packet["lineage"][
            "upstream_leadership_policy_sha256"
        ],
    }


def classify_runs(
    payloads: list[dict], expected_title: str, current_run_id: int
) -> dict:
    """Find active exact-request runs and prior successes worth inspecting.

    A green workflow is deliberately not a duplicate proof.  Successes are
    only candidates until their final rotation artifact passes
    ``verify_handoff`` against the current policy and source bytes.
    """
    active: dict[int, dict] = {}
    successes: dict[int, dict] = {}
    for payload in payloads:
        runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
        if not isinstance(runs, list):
            raise RuntimeError("WORKFLOW_RUNS_PAYLOAD_INVALID")
        for run in runs:
            if not isinstance(run, dict) or type(run.get("id")) is not int:
                raise RuntimeError("WORKFLOW_RUN_INVALID")
            if run["id"] == current_run_id:
                continue
            status = run.get("status")
            if run.get("display_title") == expected_title and status in ACTIVE_RUN_STATUSES:
                attempt = run.get("run_attempt", 1)
                if type(attempt) is not int or attempt < 1:
                    raise RuntimeError("WORKFLOW_RUN_ATTEMPT_INVALID")
                active[run["id"]] = {
                    "run_id": run["id"],
                    "run_attempt": attempt,
                }
            if status == "completed" and run.get("conclusion") == "success":
                attempt = run.get("run_attempt", 1)
                if type(attempt) is not int or attempt < 1:
                    raise RuntimeError("WORKFLOW_RUN_ATTEMPT_INVALID")
                successes[run["id"]] = {
                    "run_id": run["id"],
                    "run_attempt": attempt,
                    "created_at": str(run.get("created_at", "")),
                }
    ordered = sorted(
        successes.values(),
        key=lambda row: (row["created_at"], row["run_id"], row["run_attempt"]),
        reverse=True,
    )
    return {
        "active_same_request_runs": [active[key] for key in sorted(active)],
        "successful_artifact_candidates": ordered,
    }


def _handoff_source_paths(prior_iso: str, current_iso: str) -> tuple[str, ...]:
    """Files whose exact bytes determine the current-ratified packet."""
    return (
        ".github/scripts/korea_capital_rotation_ledger_proof.py",
        "rotation/korea_capital_rotation.py",
        "rotation/korea_capital_rotation_ledger_wire.py",
        "rotation/korea_capital_rotation_policy_ratified.py",
        "rotation/theme_taxonomy.py",
        "rotation/theme_taxonomy_authority.py",
        "market_data/krx_official_holiday_calendar.py",
        "config/korea_capital_rotation_contract.json",
        "config/korea_sector_identity_binding_contract.json",
        "config/theme_taxonomy_contract.json",
        "config/korea_leadership_policy.json",
        "config/korea_rotation_sector_identity_decision.json",
        "config/korea_rotation_sector_identity_binding_document.json",
        "config/korea_rotation_sector_identity_taxonomy_binding.json",
        "config/korea_capital_rotation_policy_ratified.json",
        "evidence/market_calendar/krx_global_holiday/2026-09-09/capture-2026.json",
        f"data/observations/korea_leadership_context/{prior_iso}/packet.json",
        f"data/observations/korea_leadership_context/{current_iso}/packet.json",
        f"data/observations/korea_breadth_context/{current_iso}/packet.json",
    )


def _verify_declared_source_revision(
    commit: str, source_paths: tuple[str, ...], root: Path = ROOT
) -> None:
    """Require declared revision bytes to equal every current producer input.

    An unrelated commit may advance main without invalidating an exact
    artifact.  Conversely, a historical ancestor that lacks or differs in a
    producer, policy, contract, calendar, or observation input cannot claim
    the packet reconstructed from current bytes.
    """
    for relative in source_paths:
        current_path = root / relative
        try:
            current_bytes = current_path.read_bytes()
            declared_bytes = subprocess.check_output(
                ["git", "-C", str(root), "show", f"{commit}:{relative}"],
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                f"FINAL_HANDOFF_DECLARED_SOURCE_UNAVAILABLE:{relative}"
            ) from exc
        if declared_bytes != current_bytes:
            raise RuntimeError(
                f"FINAL_HANDOFF_DECLARED_SOURCE_BYTES_MISMATCH:{relative}"
            )


def verify_handoff(
    artifact_dir: Path, prior_date: str, current_date: str
) -> dict:
    """Verify an exact final artifact against current sources and policy."""
    _, prior_iso, _ = _day8(prior_date, "PRIOR_DATE")
    _, current_iso, _ = _day8(current_date, "CURRENT_DATE")
    packet_paths = sorted(Path(artifact_dir).rglob("packet.json"))
    commit_paths = sorted(Path(artifact_dir).rglob("public-main-commit.txt"))
    if len(packet_paths) != 1 or len(commit_paths) != 1:
        raise RuntimeError("FINAL_HANDOFF_FILES_MISSING_OR_AMBIGUOUS")
    packet = json.loads(packet_paths[0].read_text(encoding="utf-8"))
    KCR.validate_packet(packet)
    if (
        packet["status"] != "ROTATION_BUCKETS_OBSERVED"
        or packet["rotation_policy_effective"] is not True
        or packet["observation_pair"]["prior_date"] != prior_iso
        or packet["observation_pair"]["current_date"] != current_iso
    ):
        raise RuntimeError("FINAL_HANDOFF_REQUEST_OR_STATUS_MISMATCH")

    # This is the decisive dedupe proof: re-run the exact current producer
    # from the repository's current committed source identities and current
    # ratified policy.  A policy/source update makes an older green artifact
    # unequal and therefore retryable.
    expected = LEDGER.build_current_ratified_packet(prior_iso, current_iso)
    if packet != expected:
        raise RuntimeError("FINAL_HANDOFF_CURRENT_SOURCE_OR_POLICY_MISMATCH")
    commit = commit_paths[0].read_text(encoding="utf-8").strip()
    if COMMIT.fullmatch(commit) is None:
        raise RuntimeError("FINAL_HANDOFF_PUBLIC_COMMIT_INVALID")
    try:
        subprocess.run(
            ["git", "-C", str(ROOT), "cat-file", "-e", f"{commit}^{{commit}}"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", commit, "HEAD"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("FINAL_HANDOFF_PUBLIC_COMMIT_NOT_IN_CURRENT_MAIN") from exc
    _verify_declared_source_revision(
        commit, _handoff_source_paths(prior_iso, current_iso)
    )
    return {
        "validated": True,
        "status": "FINAL_ROTATION_HANDOFF_VALIDATED",
        "prior_date": prior_iso,
        "current_date": current_iso,
        "packet_sha256": packet["payload_sha256"],
        "rotation_policy_sha256": packet["lineage"]["rotation_policy_sha256"],
        "upstream_leadership_policy_sha256": packet["lineage"][
            "upstream_leadership_policy_sha256"
        ],
        "public_main_commit": commit,
    }


def _write(value: dict, path: Path | None) -> None:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if path is None:
        print(rendered)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    readiness = sub.add_parser("request-readiness")
    readiness.add_argument("--prior-date", required=True)
    readiness.add_argument("--current-date", required=True)
    readiness.add_argument("--out", type=Path)

    runs = sub.add_parser("classify-runs")
    runs.add_argument("--runs-json", action="append", type=Path, required=True)
    runs.add_argument("--expected-title", required=True)
    runs.add_argument("--current-run-id", required=True, type=int)
    runs.add_argument("--out", type=Path)

    verify = sub.add_parser("verify-handoff")
    verify.add_argument("--artifact-dir", required=True, type=Path)
    verify.add_argument("--prior-date", required=True)
    verify.add_argument("--current-date", required=True)
    verify.add_argument("--out", type=Path)

    args = parser.parse_args()
    if args.command == "request-readiness":
        value = request_readiness(args.prior_date, args.current_date)
    elif args.command == "classify-runs":
        value = classify_runs(
            [json.loads(path.read_text(encoding="utf-8")) for path in args.runs_json],
            args.expected_title,
            args.current_run_id,
        )
    else:
        value = verify_handoff(args.artifact_dir, args.prior_date, args.current_date)
    _write(value, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
