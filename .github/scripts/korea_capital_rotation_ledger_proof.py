#!/usr/bin/env python3
"""P2-03 Korea capital-rotation -> daily briefing one-shot wiring proof.

Manual verification tool (mirrors the existing korea_breadth_derived_
outputs.py precedent): builds one real Korea capital-rotation packet
using the committed, real P3-03/P1-KR-05 breadth-context lineage
(rotation/korea_capital_rotation_ledger_wire.py's coverage_context.
breadth), and refreshes the committed briefing pointer
(data/latest_korea_rotation.json) that briefing/daily_orchestrator.py
reads -- then, optionally, builds one real daily briefing packet for the
same decision_date to prove the breadth lineage renders all the way
through.

This production proof deliberately has no P2-05 state-policy or ledger
write path. P2-05 requires an external, independently ratified state
mapping and the repository contract declares that mapping ABSENT. Test
fixtures may exercise the generic ledger capability, but this script
must never manufacture a RATIFIED policy or turn a fixture mapping into
operational state history.

Honesty boundary, updated 2026-08-22 (minimal rotation_policy
ratification + PIT temporal-invariant correction): Breadth, Leadership,
AND korea_capital_rotation.py's own rotation_policy are now all real.
Breadth is the committed P3-03/P1-KR-05 lineage -- source_available_at
(verified official publication timing) stays permanently null, an
honest, unchanged gap, but korea_capital_rotation.py now correctly
compares first_seen_at against decision_time (never against
observation_date -- that direction was backwards, see docs/korea_
capital_rotation_contract.md), so a genuine forward_live capture whose
first_seen_at predates decision_time is real, independently re-derived
AVAILABLE, not permanently BLOCKED. Leadership is the
committed korea_leadership_context/{date}/packet.json real
observations, built by real KRX index fetches through the ratified
P1-KR-07 policy (48 sector/benchmark identities). rotation_policy
(REAL_ROTATION_POLICY below) is now RATIFIED for real: it reuses the
already-implemented ranking meaning (RELATIVE_STRENGTH_VS_OWN_
BENCHMARK, own-benchmark-scope-only, TOP/MIDDLE/BOTTOM ordinal
buckets) exactly as-is, maps every real ratified P1-KR-07 SECTOR
identity 1:1 to a positional theme_id token (never a P2-01 cross-market
Theme grouping -- that taxonomy remains UNRATIFIED, honestly recorded
via the all-zero taxonomy_decision/packet SHA placeholders below), and
introduces exactly one new number: top_count=bottom_count=1. That is
deliberately the *only* value that needs no external justification --
"flag the single best and single worst performer within each
benchmark's own scope" is the unique choice that is not a percentage or
score cutoff (any N>1 would need a basis for N that does not exist), so
it is the minimal non-arbitrary realization of the existing TOP/BOTTOM
bucket vocabulary, not an invented investment threshold.

POLICY_EFFECTIVE here means only "this calculation contract is now
active" -- authority.trading_authorized / stage_promotion_authorized /
production_authorized etc. all stay closed exactly as before; nothing
about Buy/Stage/Action authority changes.

Anti-lookahead note: korea_capital_rotation.py's own _validate_policy()
rejects `ratified_at_utc` claimed to be before an observation pair's
prior_available_at was already real (OUTPUT_POLICY_RATIFIED_AFTER_
PRIOR_OBSERVATION) whenever that pair falls inside the policy's
effective window -- this is what stops a ratification from being
backdated to retroactively "cover" evidence that already existed. The
real REAL_ROTATION_POLICY.ratified_at_utc below is genuinely fixed at
the real moment this policy was ratified (2026-08-22T07:19:09Z); the
already-committed 2026-08-19/2026-08-21 evidence pair predates that
timestamp and is therefore correctly REJECTED by this same real policy
if replayed (see test_korea_capital_rotation_policy_ratified.py) --
this is not a bug, it is the anti-cherry-picking property working as
designed. The real end-to-end proof that flips POLICY_NOT_EFFECTIVE ->
KOREA_BREADTH_BLOCKED therefore uses a genuinely NEW observation pair
(2026-08-18 prior / 2026-08-20 current) fetched strictly AFTER
ratification.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
# Existing externally approved publication: merged PR #696. The file digest
# was separately supplied by the Stage1 owner and independently reviewed by
# Root; it is NOT generated from a caller's proposed runtime bytes.
# A later display release needs an independently reviewed pin update.
REVIEWED_PAPER_RUNTIME_RELEASE = {
    "publication_commit": "b08c5db2c87e47a059c31463acb627fe0d3742c4",
    "publication_merged_at": "2026-09-13T01:02:06Z",
    "path": "data/latest_kr_paper_runtime_decision.json",
    "sha256": "a5f76eb6b38292185a893bcf9d321da7154777d5cd5e0cb7c2c994a1874aea44",
    "code_revision": "0be1d1ab8b43916ad11b5c6ad51394ed2fc58f08",
    "evaluation_at": "2026-09-13T00:58:44Z",
    "qualification_sha256": "8c1b6c1a6f4ee6cc8c0c29f9bc998adcad1cccc8decb74298849cc24ad582cb9",
    "actual_source_qualification": "RATIFIED_KR_PAPER_DISPLAY_ONLY",
}


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


WIRE = _load_module(
    "korea_capital_rotation_ledger_wire_for_proof",
    "rotation/korea_capital_rotation_ledger_wire.py",
)
KCR = _load_module("korea_capital_rotation_for_proof", "rotation/korea_capital_rotation.py")
RATIFIED = _load_module(
    "korea_capital_rotation_policy_ratified_for_proof",
    "rotation/korea_capital_rotation_policy_ratified.py",
)


def _leadership_context_root() -> Path:
    """Resolved from ROOT at CALL time, never cached at import time -- a
    test (or a real alternate checkout) that reassigns the module's own
    ROOT after import must see every leadership-context read/write follow
    it. A module-level constant computed once at import would silently
    keep pointing at the original ROOT forever."""
    return ROOT / "data" / "observations" / "korea_leadership_context"


def load_real_leadership_packet(observation_date: str) -> dict:
    """Reads the real, already-committed korea_leadership_context
    evidence (built by a real .github/scripts/korea_leadership_live_
    fetch.py workflow_dispatch run) and returns the full real
    korea_leadership.build_transform() output for that date -- fails
    closed if that date's real run never populated or was blocked."""
    path = _leadership_context_root() / observation_date / "packet.json"
    if not path.is_file():
        raise RuntimeError(f"NO_LEADERSHIP_EVIDENCE_FOR_DATE:{observation_date}")
    summary = json.loads(path.read_text(encoding="utf-8"))
    if summary.get("outcome") != "populated" or not summary.get("leadership_packet"):
        raise RuntimeError(
            f"LEADERSHIP_NOT_POPULATED_FOR_DATE:{observation_date}:{summary.get('outcome')}"
        )
    return summary["leadership_packet"]


# The real, fixed moment REAL_ROTATION_POLICY was ratified. Held as a
# literal constant (never dt.datetime.now()) so every invocation of this
# script -- today or on a future rerun -- sees the exact same real
# ratification instant; a policy's own ratified_at_utc is a historical
# fact, not something that should drift with wall-clock time.
REAL_ROTATION_POLICY_RATIFIED_AT_UTC = "2026-08-22T07:19:09Z"
REAL_ROTATION_POLICY_EFFECTIVE_FROM = "2026-08-01"


def build_real_price_side(prior_date: str, current_date: str):
    """Real prior_observation/current_observation from the two committed
    real Leadership packets -- no synthetic fixture. Builds the REAL,
    ratified rotation_policy (see module docstring): every real ratified
    P1-KR-07 SECTOR identity is mapped 1:1 to a positional theme_id
    token (never a P2-01 cross-market theme grouping -- that taxonomy
    stays UNRATIFIED, honestly recorded via the all-zero taxonomy
    binding placeholders), ranking/order/tie-break reuse the contract's
    existing meaning unchanged, and top_count=bottom_count=1 is the one
    new number this ratification introduces (see docstring for why 1 is
    the minimal non-arbitrary choice). approval_status is now RATIFIED
    for real: build_packet() will genuinely rank and bucket whenever a
    supplied observation pair's dates fall inside the effective window
    AND that pair's own prior_available_at is not before this real
    ratified_at_utc (anti-lookahead, see module docstring)."""
    prior = load_real_leadership_packet(prior_date)
    current = load_real_leadership_packet(current_date)

    leadership_policy = _load_module(
        "korea_leadership_for_proof", ".github/scripts/korea_leadership.py"
    ).load_policy()
    upstream_leadership_policy_sha256 = current["policy"]["policy_sha256"]

    taxonomy_decision_sha256 = "0" * 64  # no real ratified P2-01 decision exists yet
    taxonomy_packet_sha256 = "0" * 64
    binding = {
        "taxonomy_contract_version": "theme_taxonomy/1",
        "taxonomy_id": "TAXONOMY.NOT_RATIFIED",
        "taxonomy_decision_id": "DECISION.NOT_RATIFIED",
        "taxonomy_decision_sha256": taxonomy_decision_sha256,
        "taxonomy_packet_sha256": taxonomy_packet_sha256,
        "upstream_leadership_policy_sha256": upstream_leadership_policy_sha256,
    }
    context = {
        "breadth": None,  # filled in by the caller with the real breadth context
        "investor_flow": {
            "status": "KRX_ONLY_PARTIAL_MARKET_COVERAGE",
            "market_venue_scope": "KRX_ONLY",
            "nxt_included": False,
            "whole_korea_market_claim_authorized": False,
            "source_release_time_status": "unverified",
            "available_at": None,
            "decision_eligible": False,
            "ranking_input_authorized": False,
        },
    }
    input_value = {
        "schema_version": "korea_capital_rotation_input/1",
        "as_of_date": current_date,
        "taxonomy_binding": binding,
        "coverage_context": context,
        "prior_observation": prior,
        "current_observation": current,
    }

    def scope_for(market_prefix: str, benchmark_identity: str) -> dict:
        members = sorted(
            row["series_identity"]
            for row in current["relative_strength_observations"]
            if row["role"] == "SECTOR" and row["series_identity"].startswith(f"{market_prefix}::")
        )
        return {
            "benchmark_identity": benchmark_identity,
            "members": [
                {
                    "series_identity": identity,
                    # positional token tied back to the real series_identity
                    # via this same mapping -- not an invented cross-market
                    # theme grouping (P2-01 Theme taxonomy remains
                    # unratified: taxonomy_decision/packet SHA below are the
                    # honest all-zero placeholder, never a real P2-01
                    # decision).
                    "theme_id": f"{market_prefix}.SECTOR.{index:02d}",
                }
                for index, identity in enumerate(members, 1)
            ],
            # The one new number this ratification introduces -- see
            # module docstring for why 1 (not any N>1) is the minimal
            # non-arbitrary realization of the existing TOP/BOTTOM bucket
            # vocabulary: it identifies only the single extremal member on
            # each side, never a chosen percentage/score cutoff.
            "top_count": 1,
            "bottom_count": 1,
        }

    rotation_policy = {
        "schema_version": "korea_capital_rotation_policy/1",
        "policy_id": "POLICY.P2.03.KOREA_OWN_BENCHMARK_EXTREMES_V1",
        "approval_status": "RATIFIED",
        "ratified_by": "Atlas CIO",
        "ratified_at_utc": REAL_ROTATION_POLICY_RATIFIED_AT_UTC,
        "effective_from": REAL_ROTATION_POLICY_EFFECTIVE_FROM,
        "effective_to": None,
        "taxonomy_decision_sha256": taxonomy_decision_sha256,
        "taxonomy_packet_sha256": taxonomy_packet_sha256,
        "upstream_leadership_policy_sha256": upstream_leadership_policy_sha256,
        "ranking_metric": "RELATIVE_STRENGTH_VS_OWN_BENCHMARK",
        "ranking_order": "DESCENDING_WITHIN_BENCHMARK_SCOPE",
        "tie_break": "SERIES_IDENTITY_ASC",
        "maximum_calendar_gap_days": 30,
        "benchmark_scopes": [
            scope_for("KOSDAQ", "KOSDAQ::코스닥"),
            scope_for("KOSPI", "KOSPI::코스피"),
        ],
    }
    return input_value, rotation_policy


def load_current_ratified_artifacts() -> tuple[dict, dict]:
    """Load and independently re-derive the externally ratified P2-03 inputs.

    The legacy August proof above remains byte-for-byte meaningful and is the
    default path.  This opt-in path is deliberately separate: it accepts only
    the four committed artifacts materialized by the external CIO decision,
    and rejects any drift between those bytes and the ratification builder's
    deterministic reconstruction.
    """
    rebuilt_decision, rebuilt_document, rebuilt_binding, rebuilt_policy = (
        RATIFIED.build_all()
    )
    decision = RATIFIED.load_committed_decision()
    document = RATIFIED.load_committed_document()
    binding = RATIFIED.load_committed_binding()
    policy = RATIFIED.load_committed_policy()
    if (decision, document, binding, policy) != (
        rebuilt_decision,
        rebuilt_document,
        rebuilt_binding,
        rebuilt_policy,
    ):
        raise RuntimeError("RATIFIED_ARTIFACT_REDERIVATION_MISMATCH")
    if (
        binding["taxonomy_decision_sha256"] != decision["payload_sha256"]
        or binding["taxonomy_packet_sha256"] != document["payload_sha256"]
        or policy["taxonomy_decision_sha256"] != decision["payload_sha256"]
        or policy["taxonomy_packet_sha256"] != document["payload_sha256"]
        or policy["upstream_leadership_policy_sha256"]
        != binding["upstream_leadership_policy_sha256"]
    ):
        raise RuntimeError("RATIFIED_ARTIFACT_LINEAGE_MISMATCH")
    return binding, policy


def build_current_ratified_price_side(
    prior_date: str, current_date: str
) -> tuple[dict, dict]:
    """Build the real source pair with the current external ratified policy.

    No date, policy field, threshold, identity, or source observation is
    supplied by this adapter.  The caller chooses only the two existing,
    committed Leadership observation dates; the exact binding and policy are
    loaded and re-derived from the ratified artifacts.
    """
    prior = load_real_leadership_packet(prior_date)
    current = load_real_leadership_packet(current_date)
    binding, policy = load_current_ratified_artifacts()
    expected_upstream_sha = binding["upstream_leadership_policy_sha256"]
    if (
        prior["policy"]["policy_sha256"] != expected_upstream_sha
        or current["policy"]["policy_sha256"] != expected_upstream_sha
    ):
        raise RuntimeError("RATIFIED_UPSTREAM_LEADERSHIP_POLICY_MISMATCH")
    value = {
        "schema_version": "korea_capital_rotation_input/1",
        "as_of_date": current_date,
        "taxonomy_binding": dict(binding),
        "coverage_context": {
            "breadth": None,
            "investor_flow": {
                "status": "KRX_ONLY_PARTIAL_MARKET_COVERAGE",
                "market_venue_scope": "KRX_ONLY",
                "nxt_included": False,
                "whole_korea_market_claim_authorized": False,
                "source_release_time_status": "unverified",
                "available_at": None,
                "decision_eligible": False,
                "ranking_input_authorized": False,
            },
        },
        "prior_observation": prior,
        "current_observation": current,
    }
    return value, policy


def build_current_ratified_packet(prior_date: str, current_date: str) -> dict:
    """Build and fully validate one packet without writing any repository file."""
    value, rotation_policy = build_current_ratified_price_side(
        prior_date, current_date
    )
    source = WIRE.load_breadth_context_source(current_date)
    decision_time = value["current_observation"]["available_at"]
    breadth, _ = WIRE.build_coverage_context_breadth(
        current_date, 3, source, decision_time
    )
    value["coverage_context"]["breadth"] = breadth
    return KCR.build_packet(value, rotation_policy)


def write_external_ratified_packet(path: Path, packet: dict) -> Path:
    """Persist a full packet only to an explicit path outside the repository."""
    path = Path(path)
    if not path.is_absolute():
        raise RuntimeError("RATIFIED_PACKET_OUTPUT_MUST_BE_ABSOLUTE")
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise RuntimeError("RATIFIED_PACKET_TRACKED_OUTPUT_FORBIDDEN")
    WIRE.write_json_atomic(resolved, packet)
    return resolved


def run_current_ratified(
    prior_date: str, current_date: str, packet_out: Path
) -> dict:
    """Opt-in read-only proof plus one explicit external packet artifact."""
    packet = build_current_ratified_packet(prior_date, current_date)
    output_path = write_external_ratified_packet(packet_out, packet)
    return {"rotation_packet": packet, "packet_out": output_path}


def _pin_source_files(
    paths: list, source_commit: str, *, required_count: int, error_prefix: str,
) -> list:
    """Pins a list of local files to their committed bytes at source_commit
    -- shared shape for the PAPER and usable-seed consumption paths (the
    PAPER path's own original pinned_bytes/PAPER_LOCAL_SOURCE_DRIFT loop,
    factored out and reused, never duplicated).

    The first ``required_count`` entries are mandatory config/policy/
    binding sources: absent-at-commit there is always a hard failure,
    exactly like any path that exists locally but was never committed at
    source_commit (an unreviewed local addition or a stale/wrong commit --
    never silently tolerated). Any remaining entries are optional per-date
    evidence that may legitimately not exist yet -- recorded as
    ABSENT_AT_SOURCE_COMMIT, never raised, only when genuinely absent from
    both the commit and the local checkout."""
    def pinned_bytes(relative_path: str) -> bytes:
        try:
            return subprocess.run(
                ["git", "show", f"{source_commit}:{relative_path}"],
                cwd=ROOT, check=True, capture_output=True,
            ).stdout
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"{error_prefix}_PINNED_SOURCE_UNAVAILABLE:{relative_path}") from exc

    source_files = []
    for index, path in enumerate(paths):
        relative = path.relative_to(ROOT).as_posix()
        try:
            committed = pinned_bytes(relative)
        except RuntimeError:
            if path.is_file() or index < required_count:
                raise
            source_files.append({"path": relative, "status": "ABSENT_AT_SOURCE_COMMIT", "sha256": None})
            continue
        if not path.is_file() or path.read_bytes() != committed:
            raise RuntimeError(f"{error_prefix}_LOCAL_SOURCE_DRIFT:{relative}")
        source_files.append({"path": relative, "status": "PINNED", "sha256": hashlib.sha256(committed).hexdigest()})
    return source_files


def _verify_source_files_unchanged(source_files: list, *, error_prefix: str) -> None:
    """Re-reads every pinned path's actual current bytes immediately before
    persisting a receipt that already recorded their pinned digests --
    refuses if anything moved locally during consumption."""
    for source_file in source_files:
        path = ROOT / source_file["path"]
        actual_sha = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual_sha != source_file["sha256"]:
            raise RuntimeError(f"{error_prefix}_SOURCE_CHANGED_DURING_CONSUMPTION:{source_file['path']}")


def build_current_ratified_paper_consumption(
    prior_date: str, current_date: str, *, source_commit: str,
    expected_runtime_sha256: str, evaluation_at: str,
) -> dict:
    """Bind immutable Stage1 display bytes to the existing real P2-03 attempt.

    No provider request, legacy fallback, policy mutation, or pointer write.
    The expected runtime digest is an independent caller-supplied trust pin,
    never calculated from the bytes being admitted. A full commit identifies
    where to read bytes; it does not independently approve those bytes.
    Missing Leadership remains a recorded missing input; the aggregate
    Stage1 leadership count cannot stand in for a per-sector observation.
    """
    if not isinstance(source_commit, str) or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise RuntimeError("PAPER_SOURCE_COMMIT_MUST_BE_FULL_SHA")

    expected_runtime_sha256 = KCR._sha(
        expected_runtime_sha256, "PAPER_RUNTIME_EXPECTED_SHA_INVALID",
    )
    if expected_runtime_sha256 != REVIEWED_PAPER_RUNTIME_RELEASE["sha256"]:
        raise KCR.KoreaCapitalRotationError("PAPER_RUNTIME_EXPECTED_SHA_NOT_REVIEWED")
    publication_commit = REVIEWED_PAPER_RUNTIME_RELEASE["publication_commit"]
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", publication_commit, source_commit],
        cwd=ROOT, capture_output=True,
    )
    if ancestry.returncode != 0:
        raise RuntimeError("PAPER_RUNTIME_SOURCE_NOT_DESCENDANT_OF_REVIEWED_PUBLICATION")
    if KCR._timestamp(evaluation_at, "PAPER_CONSUMPTION_TIME_INVALID") < KCR._timestamp(
        REVIEWED_PAPER_RUNTIME_RELEASE["publication_merged_at"], "PAPER_PUBLICATION_TIME_INVALID",
    ):
        raise RuntimeError("PAPER_RUNTIME_REVIEWED_PUBLICATION_NOT_YET_AVAILABLE")

    def pinned_bytes(relative_path: str, commit: str = source_commit) -> bytes:
        try:
            return subprocess.run(
                ["git", "show", f"{commit}:{relative_path}"],
                cwd=ROOT, check=True, capture_output=True,
            ).stdout
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"PAPER_PINNED_SOURCE_UNAVAILABLE:{relative_path}") from exc

    runtime_path = REVIEWED_PAPER_RUNTIME_RELEASE["path"]
    approved_bytes = pinned_bytes(runtime_path, publication_commit)
    if hashlib.sha256(approved_bytes).hexdigest() != REVIEWED_PAPER_RUNTIME_RELEASE["sha256"]:
        raise RuntimeError("PAPER_RUNTIME_REVIEWED_PUBLICATION_RECEIPT_MISMATCH")
    runtime_bytes = pinned_bytes(runtime_path)
    if hashlib.sha256(runtime_bytes).hexdigest() != expected_runtime_sha256:
        raise KCR.KoreaCapitalRotationError("PAPER_RUNTIME_SOURCE_SHA_MISMATCH")
    runtime = json.loads(runtime_bytes)
    if any(runtime.get(key) != REVIEWED_PAPER_RUNTIME_RELEASE[key] for key in (
        "code_revision", "evaluation_at", "qualification_sha256", "actual_source_qualification",
    )):
        raise RuntimeError("PAPER_RUNTIME_REVIEWED_PUBLICATION_BINDING_MISMATCH")
    # The existing ratified producer reads local inputs. Check those bytes
    # against the same immutable source before attributing their lineage.
    paths = [
        RATIFIED.DECISION_PATH, RATIFIED.IDENTITY_DOCUMENT_PATH,
        RATIFIED.BINDING_PATH, RATIFIED.POLICY_PATH,
        RATIFIED.LEADERSHIP_POLICY_PATH, RATIFIED.KRX_HOLIDAY_CAPTURE_PATH,
        KCR.CONTRACT_PATH, KCR.SECTOR_IDENTITY_BINDING_CONTRACT_PATH,
    ]
    for date in (prior_date, current_date):
        KCR._date(date, "PAPER_OBSERVATION_DATE_INVALID")
        paths.extend([
            ROOT / "data/observations/korea_leadership_context" / date / "packet.json",
            ROOT / "data/observations/korea_breadth_context" / date / "packet.json",
        ])
    source_files = _pin_source_files(paths, source_commit, required_count=8, error_prefix="PAPER")

    _binding, policy = load_current_ratified_artifacts()
    packet, error = None, None
    try:
        packet = build_current_ratified_packet(prior_date, current_date)
    except (RuntimeError, KCR.KoreaCapitalRotationError, WIRE.KoreaRotationWireError) as exc:
        error = str(exc)
    _verify_source_files_unchanged(source_files, error_prefix="PAPER")
    receipt = KCR.consume_paper_runtime_context(
        runtime_bytes,
        expected_runtime_sha256=expected_runtime_sha256,
        source_commit=source_commit, evaluation_at=evaluation_at,
        prior_date=prior_date, current_date=current_date,
        rotation_policy=policy, rotation_packet=packet, rotation_error=error,
    )
    receipt["lineage"]["rotation_inputs_source_commit"] = source_commit
    receipt["lineage"]["reviewed_runtime_release"] = dict(REVIEWED_PAPER_RUNTIME_RELEASE)
    receipt["lineage"]["rotation_source_files"] = source_files
    receipt["lineage"]["consumer_code_sha256"] = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in (
            "rotation/korea_capital_rotation.py",
            ".github/scripts/korea_capital_rotation_ledger_proof.py",
        )
    }
    receipt.pop("payload_sha256")
    receipt["payload_sha256"] = KCR.payload_sha256(receipt)
    return receipt


def run_current_ratified_paper_consumption(
    prior_date: str, current_date: str, consumer_out: Path, *,
    source_commit: str, expected_runtime_sha256: str, evaluation_at: str,
) -> dict:
    receipt = build_current_ratified_paper_consumption(
        prior_date, current_date, source_commit=source_commit,
        expected_runtime_sha256=expected_runtime_sha256, evaluation_at=evaluation_at,
    )
    output_path = write_external_ratified_packet(consumer_out, receipt)
    persisted = json.loads(output_path.read_bytes())
    if persisted != receipt:
        raise RuntimeError("PAPER_CONSUMPTION_READBACK_MISMATCH")
    return {"consumer_receipt": persisted, "consumer_out": output_path}


_LIVE_FETCH = None


def _live_fetch_module():
    """Lazily load the existing seed verifier; default proof paths never import it."""
    global _LIVE_FETCH
    if _LIVE_FETCH is None:
        _LIVE_FETCH = _load_module(
            "korea_leadership_live_fetch_for_proof",
            ".github/scripts/korea_leadership_live_fetch.py",
        )
    return _LIVE_FETCH


def _read_usable_seed(
    live, observation_date: str, seed_fetch_prior_date: str,
    source_commit: str, commit_available: bool,
) -> tuple[dict, bytes | None]:
    """Locate one seed at the immutable source commit; never fetch or repair.

    Only byte-identical committed and local evidence is handed to the
    existing generic verifier (whose integrity failures propagate) and the
    existing require_usable_seed() check. Every other location state is a
    reasoned NOT_READY decided by the central consumer.
    """
    relative = f"{KCR.USABLE_SEED_SOURCE_DIRECTORY}/{observation_date}/packet.json"
    local_path = _leadership_context_root() / observation_date / "packet.json"
    if live.output_path_for(observation_date) != local_path:
        raise RuntimeError("USABLE_SEED_VERIFIER_PATH_BINDING_MISMATCH")
    local = local_path.read_bytes() if local_path.is_file() else None
    committed = None
    if commit_available:
        shown = subprocess.run(
            ["git", "show", f"{source_commit}:{relative}"], cwd=ROOT, capture_output=True,
        )
        committed = shown.stdout if shown.returncode == 0 else None
    if not commit_available:
        availability = "SOURCE_COMMIT_UNAVAILABLE"
    elif committed is None:
        availability = "ABSENT_AT_SOURCE_COMMIT" if local is None else "UNCOMMITTED_LOCAL_ONLY"
    elif local is None:
        availability = "COMMITTED_NOT_MATERIALIZED"
    elif local != committed:
        availability = "LOCAL_BYTES_DIFFER_FROM_SOURCE_COMMIT"
    else:
        availability = "PINNED"
    summary = not_ready = None
    if availability == "PINNED":
        summary = live.verify_existing_observation(
            seed_fetch_prior_date.replace("-", ""), observation_date.replace("-", ""),
        )
        try:
            live.require_usable_seed(summary)
        except live.LeadershipLiveFetchError as exc:
            not_ready = str(exc)
    seed = {
        "observation_date": observation_date,
        "seed_fetch_prior_date": seed_fetch_prior_date,
        "source_commit": source_commit,
        "source_path": relative,
        "availability": availability,
        "committed_bytes": committed if availability in (
            "PINNED", "COMMITTED_NOT_MATERIALIZED", "LOCAL_BYTES_DIFFER_FROM_SOURCE_COMMIT",
        ) else None,
        "local_file_sha256": (
            None if local is None or availability == "COMMITTED_NOT_MATERIALIZED"
            else hashlib.sha256(local).hexdigest()
        ),
        "verified_summary": summary,
        "predecessor_not_ready_reason": not_ready,
    }
    return seed, local


def build_usable_seed_rotation_consumption(
    prior_date: str, current_date: str, *, source_commit: str,
    prior_seed_fetch_prior_date: str, current_seed_fetch_prior_date: str,
) -> dict:
    """Connect two committed usable Leadership seeds to the existing
    current-ratified consumer without writing any repository file.

    Each seed is named by the same (fetch prior date, observation date) pair
    the live-fetch verifier binds; the adapter supplies no date, policy or
    threshold of its own. Only when both seeds are READY does the unchanged
    build_current_ratified_packet() run, and its packet/4 is carried unchanged
    in an external receipt. A full commit identifies where bytes are read;
    it does not independently approve those bytes.
    """
    if not isinstance(source_commit, str) or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise RuntimeError("USABLE_SEED_SOURCE_COMMIT_MUST_BE_FULL_SHA")
    for value in (prior_date, current_date, prior_seed_fetch_prior_date, current_seed_fetch_prior_date):
        KCR._date(value, "USABLE_SEED_DATE_INVALID")
    live = _live_fetch_module()
    try:
        commit_available = subprocess.run(
            ["git", "cat-file", "-e", f"{source_commit}^{{commit}}"], cwd=ROOT, capture_output=True,
        ).returncode == 0
    except OSError:
        commit_available = False
    prior_seed, prior_local = _read_usable_seed(
        live, prior_date, prior_seed_fetch_prior_date, source_commit, commit_available,
    )
    current_seed, current_local = _read_usable_seed(
        live, current_date, current_seed_fetch_prior_date, source_commit, commit_available,
    )
    # Ratified policy/binding bytes and Breadth packets are pinned to the
    # same immutable source_commit the two Leadership seeds are pinned to
    # -- the same shape as the PAPER path's own pinned_bytes loop (F2).
    # Leadership evidence itself is not repeated here: it already has its
    # own, stricter verifier via _read_usable_seed()/require_usable_seed().
    # The first 8 are mandatory ratified config/policy/binding sources
    # (hard fail if absent-at-commit, exactly like the PAPER path); the
    # per-date Breadth packets are optional evidence that may genuinely
    # not exist yet -- absent-at-commit there stays a recorded, non-raising
    # lineage gap, and the rotation attempt below (which reads the same
    # live Breadth file itself) still surfaces the reason as its own
    # NOT_READY/WAIT result. No new policy or threshold is introduced.
    pinned_paths = [
        RATIFIED.DECISION_PATH, RATIFIED.IDENTITY_DOCUMENT_PATH,
        RATIFIED.BINDING_PATH, RATIFIED.POLICY_PATH,
        RATIFIED.LEADERSHIP_POLICY_PATH, RATIFIED.KRX_HOLIDAY_CAPTURE_PATH,
        KCR.CONTRACT_PATH, KCR.SECTOR_IDENTITY_BINDING_CONTRACT_PATH,
    ] + [
        ROOT / "data/observations/korea_breadth_context" / date / "packet.json"
        for date in (prior_date, current_date)
    ]
    if commit_available:
        pinned_source_files = _pin_source_files(
            pinned_paths, source_commit, required_count=8, error_prefix="USABLE_SEED",
        )
    else:
        # Mirrors the seeds' own SOURCE_COMMIT_UNAVAILABLE handling: no
        # source_commit to pin against at all, so nothing here is treated
        # as pinned, drifted or approved -- purely informational.
        pinned_source_files = [
            {"path": path.relative_to(ROOT).as_posix(), "status": "SOURCE_COMMIT_UNAVAILABLE", "sha256": None}
            for path in pinned_paths
        ]
    _binding, policy = load_current_ratified_artifacts()
    packet, error = None, None
    if all(
        KCR.usable_seed_readiness(seed, label)["readiness"] == "READY"
        for seed, label in ((prior_seed, "prior"), (current_seed, "current"))
    ):
        try:
            packet = build_current_ratified_packet(prior_date, current_date)
        except (RuntimeError, KCR.KoreaCapitalRotationError, WIRE.KoreaRotationWireError) as exc:
            error = str(exc)
    for date, before in ((prior_date, prior_local), (current_date, current_local)):
        path = _leadership_context_root() / date / "packet.json"
        if (path.read_bytes() if path.is_file() else None) != before:
            raise RuntimeError(f"USABLE_SEED_SOURCE_CHANGED_DURING_CONSUMPTION:{date}")
    if commit_available:
        _verify_source_files_unchanged(pinned_source_files, error_prefix="USABLE_SEED")
    receipt = KCR.consume_usable_seed_pair(
        prior_seed, current_seed, rotation_policy=policy,
        rotation_packet=packet, rotation_error=error,
    )
    receipt["lineage"]["rotation_source_files"] = pinned_source_files
    receipt["lineage"]["consumer_code_sha256"] = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in (
            "rotation/korea_capital_rotation.py",
            ".github/scripts/korea_capital_rotation_ledger_proof.py",
            ".github/scripts/korea_leadership_live_fetch.py",
        )
    }
    receipt.pop("payload_sha256")
    receipt["payload_sha256"] = KCR.payload_sha256(receipt)
    return receipt


def run_usable_seed_rotation_consumption(
    prior_date: str, current_date: str, receipt_out: Path, *, source_commit: str,
    prior_seed_fetch_prior_date: str, current_seed_fetch_prior_date: str,
) -> dict:
    receipt = build_usable_seed_rotation_consumption(
        prior_date, current_date, source_commit=source_commit,
        prior_seed_fetch_prior_date=prior_seed_fetch_prior_date,
        current_seed_fetch_prior_date=current_seed_fetch_prior_date,
    )
    output_path = write_external_ratified_packet(receipt_out, receipt)
    persisted = json.loads(output_path.read_bytes())
    if persisted != receipt:
        raise RuntimeError("USABLE_SEED_RECEIPT_READBACK_MISMATCH")
    return {"usable_seed_receipt": persisted, "receipt_out": output_path}


def run(prior_date: str, current_date: str, pointer_out: Path | None) -> dict:
    as_of_date = current_date
    value, rotation_policy = build_real_price_side(prior_date, current_date)
    source = WIRE.load_breadth_context_source(as_of_date)
    # decision_time: the real current Leadership observation's own
    # available_at -- no part of this rotation decision could have been
    # made any earlier than this real, already-KST-validated instant
    # (see rotation/korea_capital_rotation.py's PIT temporal-invariant
    # correction, 2026-08-22).
    decision_time = value["current_observation"]["available_at"]
    breadth, reason = WIRE.build_coverage_context_breadth(as_of_date, 3, source, decision_time)
    value["coverage_context"]["breadth"] = breadth
    rotation_packet = KCR.build_packet(value, rotation_policy)

    context_rel_path = None
    if source is not None:
        context_rel_path = str(
            WIRE.context_source_path(as_of_date).relative_to(ROOT)
        )
    pointer = WIRE.build_briefing_pointer(
        rotation_packet, reason, source, context_rel_path,
        generated_at=(source["generated_at"] if source else f"{as_of_date}T23:59:59Z"),
    )
    if pointer_out is not None:
        WIRE.write_json_atomic(pointer_out, pointer)
    return {
        "rotation_packet": rotation_packet,
        "pointer": pointer,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-date", required=True, help="YYYY-MM-DD, real committed Leadership evidence")
    parser.add_argument("--current-date", required=True, help="YYYY-MM-DD, real committed Leadership evidence")
    parser.add_argument(
        "--commit-pointer", action="store_true",
        help="Also write data/latest_korea_rotation.json (tracked, committed).",
    )
    parser.add_argument(
        "--current-ratified-policy",
        action="store_true",
        help=(
            "Use the externally ratified current P2-03 artifacts instead of "
            "the preserved legacy August proof policy."
        ),
    )
    parser.add_argument(
        "--packet-out",
        type=Path,
        help=(
            "Absolute external path for the full korea_capital_rotation_packet/4 "
            "(required with --current-ratified-policy)."
        ),
    )
    parser.add_argument("--paper-runtime-source-commit", help="Immutable commit containing canonical Stage1 display and current P2 inputs.")
    parser.add_argument("--expected-paper-runtime-sha256", help="Required independent reviewed SHA-256 of the canonical runtime bytes; do not derive it from the input being admitted.")
    parser.add_argument("--paper-consumer-out", type=Path, help="External read-only consumption receipt; never a packet/4 replacement.")
    parser.add_argument("--evaluation-at", help="Timezone-aware consumption time; source evaluation time is retained separately.")
    parser.add_argument("--usable-seed-source-commit", help="Immutable full commit the two committed Leadership seed files are read at; not an approval of their bytes.")
    parser.add_argument("--prior-seed-fetch-prior-date", help="YYYY-MM-DD fetch prior date recorded in the prior-date seed (live-fetch verifier binding).")
    parser.add_argument("--current-seed-fetch-prior-date", help="YYYY-MM-DD fetch prior date recorded in the current-date seed (live-fetch verifier binding).")
    parser.add_argument("--usable-seed-receipt-out", type=Path, help="External read-only usable-seed connection receipt; never a packet/4 replacement.")
    args = parser.parse_args()
    paper_args = (
        args.paper_runtime_source_commit, args.expected_paper_runtime_sha256,
        args.paper_consumer_out, args.evaluation_at,
    )
    seed_args = (
        args.usable_seed_source_commit, args.prior_seed_fetch_prior_date,
        args.current_seed_fetch_prior_date, args.usable_seed_receipt_out,
    )
    if any(value is not None for value in seed_args):
        if not all(value is not None for value in seed_args) or not args.current_ratified_policy:
            parser.error("usable-seed consumption requires --current-ratified-policy and all four usable-seed arguments")
        if args.commit_pointer or args.packet_out or any(value is not None for value in paper_args):
            parser.error("usable-seed consumption cannot write the briefing pointer, substitute for --packet-out, or combine with PAPER consumption")
        result = run_usable_seed_rotation_consumption(
            args.prior_date, args.current_date, args.usable_seed_receipt_out,
            source_commit=args.usable_seed_source_commit,
            prior_seed_fetch_prior_date=args.prior_seed_fetch_prior_date,
            current_seed_fetch_prior_date=args.current_seed_fetch_prior_date,
        )
        receipt = result["usable_seed_receipt"]
        print(json.dumps({
            "seed_pair_readiness": receipt["seed_pair_readiness"],
            "prior_seed": receipt["seeds"]["prior"]["readiness"],
            "current_seed": receipt["seeds"]["current"]["readiness"],
            "rotation_status": receipt["rotation"]["status"],
            "reasons": receipt["rotation"]["reasons"],
            "payload_sha256": receipt["payload_sha256"],
            "receipt_out": str(result["receipt_out"]),
        }, ensure_ascii=False))
        return 0 if receipt["rotation"]["status"] == "ROTATION_PACKET_AVAILABLE" else 3
    if any(value is not None for value in paper_args):
        if not all(value is not None for value in paper_args) or not args.current_ratified_policy:
            parser.error("PAPER consumption requires --current-ratified-policy and all four PAPER arguments, including --expected-paper-runtime-sha256")
        if args.commit_pointer or args.packet_out:
            parser.error("PAPER consumption cannot write the briefing pointer or substitute for --packet-out")
        result = run_current_ratified_paper_consumption(
            args.prior_date, args.current_date, args.paper_consumer_out,
            source_commit=args.paper_runtime_source_commit,
            expected_runtime_sha256=args.expected_paper_runtime_sha256,
            evaluation_at=args.evaluation_at,
        )
        receipt = result["consumer_receipt"]
        print(json.dumps({
            "market_context": receipt["market_context"]["status"],
            "runtime_regime": receipt["market_context"]["runtime_regime"],
            "rotation_status": receipt["rotation"]["status"],
            "reasons": receipt["rotation"]["reasons"],
            "payload_sha256": receipt["payload_sha256"],
            "consumer_out": str(result["consumer_out"]),
        }, ensure_ascii=False))
        return 0
    if args.current_ratified_policy:
        if args.commit_pointer:
            parser.error(
                "--commit-pointer is forbidden with --current-ratified-policy"
            )
        if args.packet_out is None:
            parser.error(
                "--packet-out is required with --current-ratified-policy"
            )
        result = run_current_ratified(
            args.prior_date, args.current_date, args.packet_out
        )
        packet = result["rotation_packet"]
        print(
            "korea capital rotation current-ratified proof: "
            f"rotation_status={packet['status']} "
            f"rotation_policy_effective={packet['rotation_policy_effective']} "
            f"rotation_policy_id={packet['rotation_policy']['policy_id']} "
            f"rotation_policy_sha256={packet['lineage']['rotation_policy_sha256']} "
            f"breadth_status={packet['coverage_context']['breadth']['status']} "
            f"packet_out={result['packet_out']}"
        )
        return 0
    if args.packet_out is not None:
        parser.error("--packet-out requires --current-ratified-policy")
    pointer_out = WIRE.BRIEFING_POINTER_PATH if args.commit_pointer else None
    result = run(args.prior_date, args.current_date, pointer_out)
    print(
        "korea capital rotation briefing proof: "
        f"rotation_status={result['rotation_packet']['status']} "
        f"rotation_policy_effective={result['rotation_packet']['rotation_policy_effective']} "
        f"breadth_status={result['rotation_packet']['coverage_context']['breadth']['status']} "
        f"pointer_path={pointer_out or '(not written)'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
