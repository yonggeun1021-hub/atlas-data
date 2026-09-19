#!/usr/bin/env python3
"""P2-03 durable sector identity binding + rotation-policy candidate lane.

Supersedes this lane's first attempt (2026-09-11, `theme_taxonomy/2`
graph-backed candidate). CIO correction: a `theme_taxonomy/2` graph is
evaluated for one specific `as_of_date` (`rotation/korea_capital_rotation.py::
_consume_taxonomy()` requires the consumed packet's `as_of_date` to equal the
rotation's own decision date), so that candidate could not be reused unchanged
across Day N, Day N+1, and later natural sessions -- it would need a new
graph, a new packet, and a new `taxonomy_packet_sha256` every trading day.
Flipping that graph to `RATIFIED` is not the fix either: the real
`theme_taxonomy/2` contract requires a ratified graph to carry non-empty
edges, non-empty asset memberships, and both KOREA + US market coverage --
that is the cross-market P2-01 taxonomy contract (owned separately under
PR #576), intentionally broader than P2-03's own Korea sector-rotation
identity, and P2-03 must not fake memberships or hijack that global registry
merely to obtain sector identity labels. The legacy `theme_taxonomy/1` opaque
placeholder is also not an acceptable fallback -- CIO wants real, meaningful,
independently reviewable identity, not opaque tokens.

This lane instead adds one dedicated, P2-03-owned, **date-independent**
contract: `korea_sector_identity_binding/1`
(`config/korea_sector_identity_binding_contract.json`), and a small,
surgical extension to `rotation/korea_capital_rotation.py::_validate_binding()`
to accept it as a third binding version alongside the legacy and P2-01
producer versions (see that function's docstring). No P2-01 file
(`rotation/theme_taxonomy.py`, `rotation/theme_taxonomy_authority.py`,
`config/theme_taxonomy_authority_registry.json`) is touched.

Four candidate documents, none RATIFIED:

1. `config/korea_rotation_sector_identity_decision_rationale_candidate.json`
   -- the small, real, committed document
   `taxonomy_binding.taxonomy_decision_sha256` is a content hash of.
2. `config/korea_rotation_sector_identity_binding_document_candidate.json`
   -- the actual durable identity content: a fixed positional
   `series_identity -> theme_id` map for the 46 real, already-`RATIFIED`
   `config/korea_leadership_policy.json` SECTOR records (24 KOSPI + 22
   KOSDAQ). No `as_of_date` field exists in this document at all -- nothing
   about it is decision-date-specific, so its hash
   (`taxonomy_packet_sha256`) never needs to change across sessions.
3. `config/korea_rotation_sector_identity_taxonomy_binding_candidate.json`
   -- the exact 6-field `taxonomy_binding` shape
   `korea_capital_rotation.py::_validate_binding()` requires, using the
   `korea_sector_identity_binding/1` contract version.
4. `config/korea_capital_rotation_policy_candidate.json` -- a standalone,
   schema-valid `korea_capital_rotation_policy/1` object,
   `approval_status: "UNRATIFIED"`, `ratified_by: null`,
   `ratified_at_utc: null`. CIO candidate direction:
   `top_count = bottom_count = 3`, `maximum_calendar_gap_days = 7`
   (superseding this lane's first, narrower `1`/`30` proposal). Every hash
   is mechanically recomputed from documents #1-#2 and the live
   `config/korea_leadership_policy.json` bytes -- never hand-typed.

Because none of this is RATIFIED, and because the durable binding path never
enters `_consume_taxonomy()`'s v2 branch at all (only the P2-01 producer
version does), this candidate is structurally inert -- `effective` stays
`False` unconditionally -- **and** it is genuinely reusable unchanged across
different decision dates, unlike the graph-backed attempt it replaces.
`test/test_korea_capital_rotation_policy_candidate.py` proves both properties
against the real, unmodified `korea_capital_rotation.py` functions, including
a real Day N / Day N+1 durability regression built from two genuine
`.github/scripts/korea_leadership.py::build_transform()` observation packets
against the real, committed `config/korea_leadership_policy.json`.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
LEADERSHIP_POLICY_PATH = ROOT / "config" / "korea_leadership_policy.json"
DECISION_RATIONALE_PATH = (
    ROOT / "config" / "korea_rotation_sector_identity_decision_rationale_candidate.json"
)
IDENTITY_DOCUMENT_PATH = (
    ROOT / "config" / "korea_rotation_sector_identity_binding_document_candidate.json"
)
BINDING_CANDIDATE_PATH = (
    ROOT / "config" / "korea_rotation_sector_identity_taxonomy_binding_candidate.json"
)
POLICY_CANDIDATE_PATH = ROOT / "config" / "korea_capital_rotation_policy_candidate.json"

POLICY_SCHEMA_VERSION = "korea_capital_rotation_policy/1"
IDENTITY_DOCUMENT_CONTRACT_VERSION = "korea_sector_identity_binding/1"

CANDIDATE_STATUS = "UNRATIFIED_CANDIDATE"
POLICY_ID = "POLICY.P2_03.KOREA_OWN_BENCHMARK_EXTREMES.CANDIDATE.V2"
DECISION_ID = "DECISION.P2_03.KOREA_SECTOR_IDENTITY_BINDING.CANDIDATE.V2"
TAXONOMY_ID = "TAXONOMY.P2_03.KOREA_SECTOR_IDENTITY.CANDIDATE.V2"

# Authored the day this correction landed. Inert either way
# (approval_status != "RATIFIED" forces effective=False unconditionally,
# regardless of any date) -- NOT the 2026-08-22T07:19:09Z self-declared
# ratification timestamp, and NOT the 2026-09-11 date this lane's first,
# now-superseded attempt used (CIO: do not reuse either merely because a
# prior candidate happened to be authored then). The real ratification's
# effective_from must eventually be the first verified KRX session after
# the real CIO ratification event, not this placeholder.
PROPOSED_EFFECTIVE_FROM = "2026-09-12"
PROPOSED_MAXIMUM_CALENDAR_GAP_DAYS = 7
PROPOSED_TOP_COUNT = 3
PROPOSED_BOTTOM_COUNT = 3

REQUIRED_BENCHMARKS = {"KOSPI::코스피", "KOSDAQ::코스닥"}

SCOPE_NOTE = (
    "Fixed, date-independent positional per-benchmark SECTOR "
    "series_identity -> theme_id binding candidate, scoped ONLY to "
    "korea_capital_rotation.py's own taxonomy_binding/rotation_policy fields "
    "via the dedicated korea_sector_identity_binding/1 contract. This is NOT "
    "the P2-01 cross-market Theme / Value-Chain taxonomy "
    "(config/theme_taxonomy_authority_registry.json, owned separately under "
    "PR #576, currently 0 ratified records) and does not modify, populate, "
    "or duplicate it, and does not use the P2-01 theme_taxonomy/2 graph "
    "producer at all (that graph is evaluated per-as_of_date and cannot be "
    "reused unchanged across sessions; this binding intentionally carries no "
    "as_of_date). UNRATIFIED: no production, trading, candidate, Stage, "
    "briefing, or Regime authority is opened by any document in this lane. "
    "Does not reuse the 2026-08-22T07:19:09Z self-declared ratification "
    "timestamp, the all-zero taxonomy SHA placeholders previously embedded "
    "in .github/scripts/korea_capital_rotation_ledger_proof.py, or the "
    "2026-09-11 date this lane's first, superseded candidate was authored on."
)


class RotationPolicyCandidateError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise RotationPolicyCandidateError(f"SOURCE_FILE_UNREADABLE:{path}") from exc


def _write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RotationPolicyCandidateError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def load_upstream_leadership_policy(path: Path = LEADERSHIP_POLICY_PATH) -> dict:
    value = _read_json(path)
    if (
        value.get("approval_status") != "RATIFIED"
        or value.get("market") != "KOREA"
        or not isinstance(value.get("records"), list)
    ):
        raise RotationPolicyCandidateError("UPSTREAM_LEADERSHIP_POLICY_NOT_RATIFIED")
    return value


def _sector_scopes(path: Path = LEADERSHIP_POLICY_PATH) -> list[tuple[str, str, list[str]]]:
    """[(prefix, benchmark_identity, sorted series_identity list), ...]"""
    policy = load_upstream_leadership_policy(path)
    by_benchmark: dict[str, list[str]] = {}
    for record in policy["records"]:
        if record.get("role") != "SECTOR":
            continue
        by_benchmark.setdefault(record["benchmark_identity"], []).append(
            record["series_identity"]
        )
    if set(by_benchmark) != REQUIRED_BENCHMARKS:
        raise RotationPolicyCandidateError("UNEXPECTED_BENCHMARK_SET")
    result = []
    for benchmark in sorted(by_benchmark):
        prefix = "KOSPI" if benchmark.startswith("KOSPI") else "KOSDAQ"
        members = sorted(by_benchmark[benchmark])
        if len(members) != len(set(members)):
            raise RotationPolicyCandidateError(f"DUPLICATE_SERIES_IDENTITY:{benchmark}")
        result.append((prefix, benchmark, members))
    return result


def build_decision_rationale(path: Path = LEADERSHIP_POLICY_PATH) -> dict:
    policy = load_upstream_leadership_policy(path)
    rationale = {
        "schema_version": 1,
        "contract_version": "p2_03_rotation_sector_identity_decision_rationale/1",
        "candidate_decision_id": DECISION_ID,
        "candidate_taxonomy_id": TAXONOMY_ID,
        "candidate_status": CANDIDATE_STATUS,
        "proposed_effective_from": PROPOSED_EFFECTIVE_FROM,
        "scope_note": SCOPE_NOTE,
        "source_policy_path": "config/korea_leadership_policy.json",
        "source_policy_sha256": file_sha256(path),
        "source_policy_effective_from": policy["effective_from"],
    }
    rationale["payload_sha256"] = payload_sha256(rationale)
    return rationale


def build_sector_identity_binding_document(path: Path = LEADERSHIP_POLICY_PATH) -> dict:
    """The durable, date-independent identity content itself: a fixed
    positional SECTOR series_identity -> theme_id map. No as_of_date field
    exists anywhere in this document -- it is not evaluated per decision
    date the way a theme_taxonomy/2 graph is, so its hash never needs to
    change across Day N, Day N+1, or any later natural session, as long as
    the upstream Leadership policy it is pinned to does not change."""
    scopes = []
    for prefix, benchmark, members in _sector_scopes(path):
        scopes.append({
            "benchmark_identity": benchmark,
            "members": [
                {"series_identity": identity, "theme_id": f"{prefix}.SECTOR.{index:02d}"}
                for index, identity in enumerate(members, 1)
            ],
        })
    document = {
        "schema_version": 1,
        "contract_version": IDENTITY_DOCUMENT_CONTRACT_VERSION,
        "candidate_taxonomy_id": TAXONOMY_ID,
        "binding_status": CANDIDATE_STATUS,
        "scope_note": SCOPE_NOTE,
        "source_policy_path": "config/korea_leadership_policy.json",
        "source_policy_sha256": file_sha256(path),
        "source_policy_effective_from": load_upstream_leadership_policy(path)["effective_from"],
        "benchmark_scopes": scopes,
    }
    document["payload_sha256"] = payload_sha256(document)
    return document


def build_taxonomy_binding_candidate(
    rationale: dict, document: dict, path: Path = LEADERSHIP_POLICY_PATH
) -> dict:
    """The exact 6-field shape korea_capital_rotation.py::_validate_binding()
    requires -- using the dedicated, date-independent
    korea_sector_identity_binding/1 contract version, real identity
    throughout, never the all-zero placeholder and never a per-date graph
    hash."""
    return {
        "taxonomy_contract_version": IDENTITY_DOCUMENT_CONTRACT_VERSION,
        "taxonomy_id": TAXONOMY_ID,
        "taxonomy_decision_id": DECISION_ID,
        "taxonomy_decision_sha256": rationale["payload_sha256"],
        "taxonomy_packet_sha256": document["payload_sha256"],
        "upstream_leadership_policy_sha256": file_sha256(path),
    }


def build_candidate_policy(binding: dict, path: Path = LEADERSHIP_POLICY_PATH) -> dict:
    benchmark_scopes = [
        {
            "benchmark_identity": benchmark,
            "members": [
                {"series_identity": identity, "theme_id": f"{prefix}.SECTOR.{index:02d}"}
                for index, identity in enumerate(members, 1)
            ],
            "top_count": PROPOSED_TOP_COUNT,
            "bottom_count": PROPOSED_BOTTOM_COUNT,
        }
        for prefix, benchmark, members in _sector_scopes(path)
    ]
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "approval_status": "UNRATIFIED",
        "ratified_by": None,
        "ratified_at_utc": None,
        "effective_from": PROPOSED_EFFECTIVE_FROM,
        "effective_to": None,
        "taxonomy_decision_sha256": binding["taxonomy_decision_sha256"],
        "taxonomy_packet_sha256": binding["taxonomy_packet_sha256"],
        "upstream_leadership_policy_sha256": binding["upstream_leadership_policy_sha256"],
        "ranking_metric": "RELATIVE_STRENGTH_VS_OWN_BENCHMARK",
        "ranking_order": "DESCENDING_WITHIN_BENCHMARK_SCOPE",
        "tie_break": "SERIES_IDENTITY_ASC",
        "maximum_calendar_gap_days": PROPOSED_MAXIMUM_CALENDAR_GAP_DAYS,
        "benchmark_scopes": benchmark_scopes,
    }


def build_all(path: Path = LEADERSHIP_POLICY_PATH) -> tuple[dict, dict, dict, dict]:
    rationale = build_decision_rationale(path)
    document = build_sector_identity_binding_document(path)
    binding = build_taxonomy_binding_candidate(rationale, document, path)
    policy = build_candidate_policy(binding, path)
    return rationale, document, binding, policy


def write_candidates(
    rationale_path: Path = DECISION_RATIONALE_PATH,
    document_path: Path = IDENTITY_DOCUMENT_PATH,
    binding_path: Path = BINDING_CANDIDATE_PATH,
    policy_path: Path = POLICY_CANDIDATE_PATH,
    source_path: Path = LEADERSHIP_POLICY_PATH,
) -> None:
    rationale, document, binding, policy = build_all(source_path)
    _write_json(rationale_path, rationale)
    _write_json(document_path, document)
    _write_json(binding_path, binding)
    _write_json(policy_path, policy)


def load_committed_rationale(path: Path = DECISION_RATIONALE_PATH) -> dict:
    return _read_json(path)


def load_committed_document(path: Path = IDENTITY_DOCUMENT_PATH) -> dict:
    return _read_json(path)


def load_committed_binding(path: Path = BINDING_CANDIDATE_PATH) -> dict:
    return _read_json(path)


def load_committed_policy(path: Path = POLICY_CANDIDATE_PATH) -> dict:
    return _read_json(path)


def main() -> int:
    write_candidates()
    print(
        "wrote UNRATIFIED durable sector identity binding candidate documents "
        "(no schedule/full-packet automation, no authority opened)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
