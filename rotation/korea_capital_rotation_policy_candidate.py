#!/usr/bin/env python3
"""P2-03 rotation-policy canonicalization-only candidate lane.

Phase A of the Korea Natural Rotation Producer slice found that the only
`korea_capital_rotation_policy/1` object anywhere in this repository (other
than the test-only fixture in `test/test_korea_capital_rotation.py`) is
`REAL_ROTATION_POLICY`, built inline by `.github/scripts/korea_capital_
rotation_ledger_proof.py` -- a manual one-shot proof script, not a committed,
independently reviewable artifact. That policy self-asserts `ratified_by:
"Atlas CIO"` / `ratified_at_utc: "2026-08-22T07:19:09Z"` with no external
ratification trail (no PR review comments, no Notion decision packet -- unlike
e.g. P1-COM-05's real, documented Regime Policy Ratification Decision Packet
v1), and its own `taxonomy_binding` is an honest all-zero placeholder
(`taxonomy_id: "TAXONOMY.NOT_RATIFIED"`, `taxonomy_decision_sha256`/
`taxonomy_packet_sha256` = 64 zero characters). CIO verdict:
`P2_03_ROTATION_POLICY_CANONICALIZATION_REQUIRED` -- Phase B (schedule
migration, full-packet automation) stays blocked; this module opens only a
policy-canonicalization lane instead.

`rotation/korea_capital_rotation.py` already ships a real, already-merged
(2026-09-05, commit 248141b9) `theme_taxonomy/2` consumption path: a caller
can supply a real `theme_taxonomy_input/1` graph's raw bytes
(`taxonomy_source_bytes`), which gets independently re-run through the real,
already-existing P2-01 producer (`rotation/theme_taxonomy.py::build_packet()`)
and its independent authority resolver
(`rotation/theme_taxonomy_authority.py::resolve_graph_authority()`) -- nothing
about the graph's own claimed identity or approval is trusted. This module
uses that real, already-merged path instead of inventing a competing binding
scheme, and does not touch `config/theme_taxonomy_authority_registry.json`
(which stays exactly as merged: 0 ratified records) or any file owned by
PR #576 (P2-01: Theme / Value-Chain taxonomy population).

Candidate documents this module builds (none RATIFIED, none wired into any
production or scheduled path):

1. `config/korea_rotation_theme_identity_graph_candidate.json` -- a real
   `theme_taxonomy_input/1` graph: one `THEME` node per real, already-
   `RATIFIED` `config/korea_leadership_policy.json` SECTOR record (24 KOSPI +
   22 KOSDAQ), no edges, no memberships (neither is required unless
   `approval_status == "RATIFIED"`, and this graph's approval is honestly
   `"UNRATIFIED"`). Node `theme_id`s are the same positional
   `{KOSPI|KOSDAQ}.SECTOR.NN` tokens `.github/scripts/korea_capital_
   rotation_ledger_proof.py` already used for its own (self-ratified, now
   superseded) attempt -- reusing only the already-established, non-arbitrary
   naming, not its ratification claim.
2. `config/korea_rotation_theme_identity_decision_rationale_candidate.json`
   -- the small, real, committed document `approval.decision_sha256` inside
   the graph above is a content hash of, so that hash is independently
   reviewable rather than an opaque number.
3. `config/korea_rotation_theme_identity_taxonomy_packet_candidate.json` --
   the REAL, unmodified `rotation/theme_taxonomy.py::build_packet()` output
   for graph #1, committed as a verified snapshot (regression-tested
   committed-vs-rebuilt byte-identical, this repo's usual discipline).
   Because the graph's own approval is UNRATIFIED and the real authority
   registry is empty, this resolves honestly to
   `graph_status: "DRAFT_OR_NOT_EFFECTIVE_GRAPH"` and
   `authority_resolution.status: "AUTHORITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD"`
   -- never a fabricated or self-declared authorization.
4. `config/korea_rotation_theme_identity_binding_candidate.json` -- the exact
   6-field `taxonomy_binding` shape `korea_capital_rotation.py::_validate_
   binding(value, contract, derived=False)` requires from a caller, using the
   real producer's current contract version (`theme_taxonomy/2`, read live
   from that producer's own contract, never a literal) and the real
   identity/hashes from documents #1-#3 above.
5. `config/korea_capital_rotation_policy_candidate.json` -- a standalone,
   schema-valid `korea_capital_rotation_policy/1` object with
   `approval_status: "UNRATIFIED"`, `ratified_by: null`,
   `ratified_at_utc: null`. Its `taxonomy_decision_sha256` /
   `taxonomy_packet_sha256` / `upstream_leadership_policy_sha256` are the
   same real values as document #4's, never re-typed or re-derived
   separately (single source of truth).

Because `approval_status == "UNRATIFIED"` on both the graph (#1) and the
rotation policy (#5), `korea_capital_rotation.py`'s own `_validate_policy()`
forces `effective = False` unconditionally regardless of dates
(`effective = status == "RATIFIED" and covers_both`), and `theme_taxonomy.py`'s
own `build_packet()` forces `graph_currently_effective = False` unconditionally
whenever `approval_status != "RATIFIED"`. Neither candidate can produce a
rank, bucket, transition, membership activation, or any authority even if fed
to the real, unmodified production code today.
`test/test_korea_capital_rotation_policy_candidate.py` proves all of this
against the real `rotation/theme_taxonomy.py` and `rotation/korea_capital_
rotation.py` functions, not a reimplementation of their rules.

This module writes nothing at import time and has no CLI that touches a
production or scheduled path. `write_candidates()` (re)generates the five
committed documents above from source.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Optional

try:
    from rotation import theme_taxonomy as TT
except ModuleNotFoundError:  # direct ``python rotation/....py`` CLI
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rotation import theme_taxonomy as TT


ROOT = Path(__file__).resolve().parents[1]
LEADERSHIP_POLICY_PATH = ROOT / "config" / "korea_leadership_policy.json"
GRAPH_PATH = ROOT / "config" / "korea_rotation_theme_identity_graph_candidate.json"
DECISION_RATIONALE_PATH = (
    ROOT / "config" / "korea_rotation_theme_identity_decision_rationale_candidate.json"
)
TAXONOMY_PACKET_PATH = (
    ROOT / "config" / "korea_rotation_theme_identity_taxonomy_packet_candidate.json"
)
BINDING_CANDIDATE_PATH = (
    ROOT / "config" / "korea_rotation_theme_identity_binding_candidate.json"
)
POLICY_CANDIDATE_PATH = ROOT / "config" / "korea_capital_rotation_policy_candidate.json"

POLICY_SCHEMA_VERSION = "korea_capital_rotation_policy/1"
GRAPH_INPUT_SCHEMA_VERSION = "theme_taxonomy_input/1"

CANDIDATE_STATUS = "UNRATIFIED_CANDIDATE"
POLICY_ID = "POLICY.P2_03.KOREA_OWN_BENCHMARK_EXTREMES.CANDIDATE.V1"
DECISION_ID = "DECISION.P2_03.KOREA_SECTOR_IDENTITY_BINDING.CANDIDATE.V1"
TAXONOMY_ID = "TAXONOMY.P2_03.KOREA_SECTOR_IDENTITY.CANDIDATE.V1"

# Authored the day this canonicalization-only lane was opened. Inert either
# way (UNRATIFIED forces effective=False unconditionally on both the graph
# and the rotation policy) -- NOT the 2026-08-22T07:19:09Z self-declared
# ratification timestamp this lane exists to replace, and not silently
# copied from the upstream Leadership policy's own 2026-08-01 effective_from
# either.
PROPOSED_EFFECTIVE_FROM = "2026-09-11"
PROPOSED_MAXIMUM_CALENDAR_GAP_DAYS = 30
PROPOSED_TOP_COUNT = 1
PROPOSED_BOTTOM_COUNT = 1

REQUIRED_BENCHMARKS = {"KOSPI::코스피", "KOSDAQ::코스닥"}

SCOPE_NOTE = (
    "Positional per-benchmark SECTOR series_identity -> theme_id binding "
    "candidate, scoped ONLY to korea_capital_rotation.py's own "
    "taxonomy_binding/rotation_policy fields. This is NOT the P2-01 "
    "cross-market Theme / Value-Chain taxonomy "
    "(config/theme_taxonomy_authority_registry.json, owned separately under "
    "PR #576, currently 0 ratified records) and does not modify, populate, "
    "or duplicate it -- it consumes the real, already-merged theme_taxonomy/2 "
    "producer and authority resolver read-only. UNRATIFIED: no production, "
    "trading, candidate, Stage, briefing, or Regime authority is opened by "
    "any document in this lane. Does not reuse the 2026-08-22T07:19:09Z "
    "self-declared ratification timestamp or the all-zero taxonomy SHA "
    "placeholders previously embedded in .github/scripts/korea_capital_"
    "rotation_ledger_proof.py."
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
        "contract_version": "p2_03_rotation_theme_identity_decision_rationale/1",
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


def build_theme_identity_graph(
    decision_sha256: str, path: Path = LEADERSHIP_POLICY_PATH
) -> dict:
    """A real ``theme_taxonomy_input/1`` graph: one THEME node per real,
    already-RATIFIED SECTOR record. No edges, no memberships -- neither is
    required for an honestly UNRATIFIED graph."""
    nodes = []
    for prefix, benchmark, members in _sector_scopes(path):
        for index, identity in enumerate(members, 1):
            nodes.append({
                "theme_id": f"{prefix}.SECTOR.{index:02d}",
                "display_name": identity.split("::", 1)[1],
                "description": (
                    f"KRX {prefix} industry-sector index classification "
                    f"({benchmark}) per config/korea_leadership_policy.json "
                    f"SECTOR record {identity!r} (RATIFIED, effective "
                    f"{load_upstream_leadership_policy(path)['effective_from']})."
                ),
                "node_type": "THEME",
                "valid_from": load_upstream_leadership_policy(path)["effective_from"],
                "valid_to": None,
            })
    graph = {
        "schema_version": GRAPH_INPUT_SCHEMA_VERSION,
        "taxonomy_id": TAXONOMY_ID,
        "as_of_date": PROPOSED_EFFECTIVE_FROM,
        "approval": {
            "approval_status": "UNRATIFIED",
            "decision_id": DECISION_ID,
            "decision_sha256": decision_sha256,
            "ratified_by": None,
            "ratified_at_utc": None,
            "effective_from": PROPOSED_EFFECTIVE_FROM,
            "effective_to": None,
        },
        "nodes": sorted(nodes, key=lambda item: item["theme_id"]),
        "edges": [],
        "memberships": [],
    }
    return graph


def build_taxonomy_packet_candidate(graph: dict) -> dict:
    """The REAL, unmodified P2-01 producer output for `graph` -- never a
    reimplementation of its rules."""
    return TT.build_packet(graph)


def build_taxonomy_binding_candidate(graph: dict, packet: dict, path: Path = LEADERSHIP_POLICY_PATH) -> dict:
    """The exact 6-field shape korea_capital_rotation.py::_validate_binding()
    requires from a caller (``derived=False``): real identity throughout,
    never the all-zero placeholder."""
    return {
        "taxonomy_contract_version": TT.load_contract()["contract_version"],
        "taxonomy_id": graph["taxonomy_id"],
        "taxonomy_decision_id": graph["approval"]["decision_id"],
        "taxonomy_decision_sha256": graph["approval"]["decision_sha256"],
        "taxonomy_packet_sha256": packet["payload_sha256"],
        "upstream_leadership_policy_sha256": file_sha256(path),
    }


def build_candidate_policy(
    binding: dict, path: Path = LEADERSHIP_POLICY_PATH
) -> dict:
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


def build_all(path: Path = LEADERSHIP_POLICY_PATH) -> tuple[dict, dict, dict, dict, dict]:
    rationale = build_decision_rationale(path)
    graph = build_theme_identity_graph(rationale["payload_sha256"], path)
    packet = build_taxonomy_packet_candidate(graph)
    binding = build_taxonomy_binding_candidate(graph, packet, path)
    policy = build_candidate_policy(binding, path)
    return rationale, graph, packet, binding, policy


def write_candidates(
    rationale_path: Path = DECISION_RATIONALE_PATH,
    graph_path: Path = GRAPH_PATH,
    packet_path: Path = TAXONOMY_PACKET_PATH,
    binding_path: Path = BINDING_CANDIDATE_PATH,
    policy_path: Path = POLICY_CANDIDATE_PATH,
    source_path: Path = LEADERSHIP_POLICY_PATH,
) -> None:
    rationale, graph, packet, binding, policy = build_all(source_path)
    _write_json(rationale_path, rationale)
    _write_json(graph_path, graph)
    _write_json(packet_path, packet)
    _write_json(binding_path, binding)
    _write_json(policy_path, policy)


def load_committed_rationale(path: Path = DECISION_RATIONALE_PATH) -> dict:
    return _read_json(path)


def load_committed_graph(path: Path = GRAPH_PATH) -> dict:
    return _read_json(path)


def load_committed_packet(path: Path = TAXONOMY_PACKET_PATH) -> dict:
    return _read_json(path)


def load_committed_binding(path: Path = BINDING_CANDIDATE_PATH) -> dict:
    return _read_json(path)


def load_committed_policy(path: Path = POLICY_CANDIDATE_PATH) -> dict:
    return _read_json(path)


def main() -> int:
    write_candidates()
    print(
        "wrote UNRATIFIED candidate documents "
        "(no schedule/full-packet automation, no authority opened)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
