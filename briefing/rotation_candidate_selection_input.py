#!/usr/bin/env python3
"""Rotation Stage 3 candidate-selection input projection.

This module turns four caller-supplied packets into the non-interpretive input
a later candidate-selection stage would read: a validated
``rotation_discovery_briefing_packet/4``, its exact rotation ledger, the Stage 2
capital-flow posture packet, and the exact historical Stage 1 paper-regime
packet Stage 2 binds.  It projects ``rotation.latest_changes`` one-to-one, in
the order the Rotation Discovery validator already fixed, forwards Stage 1
lineage separately, and hard-binds every selection-adjacent field to a closed
constant.

The briefing alone is not an admissible source.  A briefing digest only proves
the briefing is internally self-consistent, so a caller who edits
``rotation.latest_changes`` and re-signs the packet still passes the producer's
own validator.  The exact ``rotation_state_ledger`` packet the briefing bound
by ``rotation.source_ledger_sha256`` is therefore a required second input, and
the projected rows are re-derived from that ledger.

It does not rank, select, score, evaluate readiness, promote, generate an
action, choose a persistence default, call a provider, discover or latest-fetch
a Stage 1 file, or touch the network.  A projected row is an *input to*
selection, never a selection outcome.
"""
from __future__ import annotations

import argparse
import copy
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
    sys.path.insert(0, str(ROOT))
CONTRACT_PATH = ROOT / "config" / "rotation_candidate_selection_input_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"SOURCE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BRIEFING = _load_module(
    "atlas_rotation_discovery_briefing", ROOT / "briefing" / "rotation_discovery.py"
)
PAPER_REGIME = _load_module(
    "atlas_paper_regime_reference",
    ROOT / "regime" / "paper_regime_reference.py",
)
CAPITAL_FLOW = _load_module(
    "atlas_capital_flow_posture_reference",
    ROOT / "portfolio" / "capital_flow_posture_reference.py",
)


class RotationCandidateSelectionInputError(ValueError):
    """Fail-closed candidate-selection input contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RotationCandidateSelectionInputError(
            f"JSON_READ_FAILED:{path}:{exc}"
        ) from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "rotation_candidate_selection_input/2",
        "output_schema_version": "rotation_candidate_selection_input_packet/2",
        "source_contract": "rotation_discovery_briefing/4",
        "source_output_schema_version": "rotation_discovery_briefing_packet/4",
        "source_ledger_contract": "rotation_state_ledger/1",
        "source_ledger_schema_version": "rotation_state_ledger_packet/1",
        "stage2_source_contract": "capital_flow_posture_reference_policy/v1",
        "stage2_source_schema_version": "capital_flow_posture_reference/v1",
        "stage2_stage1_source_index": 0,
        "stage2_stage1_source_type": "P1_PAPER_REGIME_REFERENCE_PACKET",
        "stage2_stage1_source_path": "data/latest_paper_regime_reference.json",
        "stage1_source_contract": "paper_regime_reference_policy/v1",
        "stage1_source_schema_version": "paper_regime_reference/v2",
        "stage1_market_order": ["US", "KR", "CRYPTO"],
        "stage1_projected_market_fields": [
            "market", "as_of_date", "candidate_regime", "runtime_regime",
        ],
        "source_section": "rotation.latest_changes",
        "projected_source_fields": [
            "market",
            "scope_id",
            "entity_id",
            "as_of_date",
            "structural_bucket_transition",
            "prior_state",
            "current_state",
            "state_transition",
            "record_sha256",
            "source_packet_sha256",
        ],
        "closed_constants": {
            "selection_rank": None,
            "selected": False,
            "candidate_eligible": False,
            "ready_status": "NOT_EVALUATED",
            "promotion_status": "PROMOTION_NOT_AUTHORIZED",
            "action": None,
        },
        "status": (
            "ROTATION_CANDIDATE_SELECTION_INPUT_PROJECTED_NO_SELECTION_AUTHORITY"
        ),
        "authority": {
            "input_projection_only": True,
            "candidate_selection_authorized": False,
            "candidate_ranking_authorized": False,
            "scoring_authorized": False,
            "readiness_evaluation_authorized": False,
            "stage_promotion_authorized": False,
            "action_generation_authorized": False,
            "persistence_default_authorized": False,
            "runtime_regime_authorized": False,
            "capital_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise RotationCandidateSelectionInputError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise RotationCandidateSelectionInputError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _checked_source_contract(source_contract: dict | None) -> dict:
    """Return the producer's own contract, validated by the producer itself."""
    if source_contract is None:
        return BRIEFING.load_contract()
    try:
        return BRIEFING._validate_contract(source_contract)
    except BRIEFING.RotationDiscoveryBriefingError as exc:
        raise RotationCandidateSelectionInputError(
            f"SOURCE_CONTRACT_INVALID:{exc}"
        ) from exc


def _checked_briefing(
    briefing: dict,
    contract: dict,
    source_contract: dict,
    wildcard_root: Path,
    dart_root: Path,
) -> dict:
    """Revalidate a caller-supplied briefing through its own producer.

    Only an object that is already a ``rotation_discovery_briefing_packet/4``
    and still passes the unmodified Rotation Discovery validator may be read.
    Nothing is loaded, discovered, or repaired here.
    """
    if not isinstance(briefing, dict):
        raise RotationCandidateSelectionInputError("SOURCE_BRIEFING_INVALID")
    if briefing.get("schema_version") != contract["source_output_schema_version"]:
        raise RotationCandidateSelectionInputError("SOURCE_BRIEFING_SCHEMA_INVALID")
    if briefing.get("contract_version") != contract["source_contract"]:
        raise RotationCandidateSelectionInputError("SOURCE_BRIEFING_CONTRACT_INVALID")
    try:
        return BRIEFING.validate_briefing(
            copy.deepcopy(briefing),
            source_contract,
            wildcard_root=wildcard_root,
            dart_root=dart_root,
        )
    except BRIEFING.RotationDiscoveryBriefingError as exc:
        raise RotationCandidateSelectionInputError(
            f"SOURCE_BRIEFING_REVALIDATION_FAILED:{exc}"
        ) from exc


def _checked_rotation(
    checked: dict, rotation_ledger, contract: dict, source_contract: dict
) -> dict:
    """Re-derive the rotation section from the exact bound ledger packet.

    Passing the producer's validator only proves a briefing is *internally*
    self-consistent.  A caller who edits ``rotation.latest_changes`` and then
    recomputes ``state_counts``, ``latest_change_count`` and ``packet_sha256``
    produces a self-resigned briefing the producer still accepts, so the
    briefing's own rows can never be the authority for this projection.

    The ledger the briefing bound by ``source_ledger_sha256`` is therefore
    required as a separate input, and the rotation section is re-derived from
    it by the producer's own derivation — reused rather than reimplemented, so
    this projection cannot diverge from it or introduce a second rotation
    policy, ordering rule or state mapping.  The briefing's stored section is
    compared against that re-derivation and never trusted: any row that was
    added, dropped, reordered, restated or rehashed fails closed here.
    """
    if not isinstance(rotation_ledger, dict):
        raise RotationCandidateSelectionInputError("SOURCE_LEDGER_INVALID")
    if (
        rotation_ledger.get("schema_version")
        != contract["source_ledger_schema_version"]
    ):
        raise RotationCandidateSelectionInputError("SOURCE_LEDGER_SCHEMA_INVALID")
    if rotation_ledger.get("contract_version") != contract["source_ledger_contract"]:
        raise RotationCandidateSelectionInputError("SOURCE_LEDGER_CONTRACT_INVALID")
    generated = BRIEFING._utc(
        checked["generated_at"], "SOURCE_BRIEFING_GENERATED_AT_INVALID"
    )
    try:
        derived = BRIEFING._rotation_section(
            copy.deepcopy(rotation_ledger), generated, source_contract
        )
    except BRIEFING.RotationDiscoveryBriefingError as exc:
        raise RotationCandidateSelectionInputError(
            f"SOURCE_LEDGER_REVALIDATION_FAILED:{exc}"
        ) from exc
    if derived["source_ledger_sha256"] != checked["rotation"]["source_ledger_sha256"]:
        raise RotationCandidateSelectionInputError("SOURCE_LEDGER_NOT_BOUND")
    if derived != checked["rotation"]:
        raise RotationCandidateSelectionInputError("SOURCE_ROTATION_SECTION_TAMPERED")
    return derived


def _producer_file_sha256(packet: dict) -> str:
    data = (
        json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _checked_stage1(stage1_reference, contract: dict) -> tuple[dict, str]:
    if not isinstance(stage1_reference, dict):
        raise RotationCandidateSelectionInputError("STAGE1_SOURCE_INVALID")
    if stage1_reference.get("schema_version") != contract["stage1_source_schema_version"]:
        raise RotationCandidateSelectionInputError("STAGE1_SOURCE_SCHEMA_INVALID")
    if stage1_reference.get("contract_version") != contract["stage1_source_contract"]:
        raise RotationCandidateSelectionInputError("STAGE1_SOURCE_CONTRACT_INVALID")
    try:
        checked = PAPER_REGIME.validate_reference(
            copy.deepcopy(stage1_reference), ROOT
        )
    except PAPER_REGIME.PaperRegimeReferenceError as exc:
        raise RotationCandidateSelectionInputError(
            f"STAGE1_SOURCE_REVALIDATION_FAILED:{exc}"
        ) from exc
    markets = checked.get("markets")
    if (
        not isinstance(markets, list)
        or [row.get("market") for row in markets]
        != contract["stage1_market_order"]
    ):
        raise RotationCandidateSelectionInputError("STAGE1_MARKET_VOCABULARY_INVALID")
    for row in markets:
        if (
            not isinstance(row.get("as_of_date"), str)
            or not isinstance((row.get("paper_reference") or {}).get("candidate_regime"), str)
            or row.get("runtime_regime") != "UNKNOWN"
        ):
            raise RotationCandidateSelectionInputError("STAGE1_MARKET_FIELDS_INVALID")
    for key in (
        "action_authorized", "buy_authorized", "capital_authorized",
        "final_regime_authorized", "order_authorized", "production_authorized",
        "runtime_regime_authorized", "stage_authorized", "strategy_authorized",
        "trading_authorized",
    ):
        if checked.get("authority", {}).get(key) is not False:
            raise RotationCandidateSelectionInputError(
                f"STAGE1_AUTHORITY_OPENED:{key}"
            )
    return checked, _producer_file_sha256(checked)


def _checked_stage2(
    stage2_reference, checked_stage1: dict, stage1_file_sha256: str,
    contract: dict,
) -> tuple[dict, str]:
    if not isinstance(stage2_reference, dict):
        raise RotationCandidateSelectionInputError("STAGE2_SOURCE_INVALID")
    if stage2_reference.get("schema_version") != contract["stage2_source_schema_version"]:
        raise RotationCandidateSelectionInputError("STAGE2_SOURCE_SCHEMA_INVALID")
    if stage2_reference.get("contract_version") != contract["stage2_source_contract"]:
        raise RotationCandidateSelectionInputError("STAGE2_SOURCE_CONTRACT_INVALID")
    try:
        checked = CAPITAL_FLOW.validate_reference(
            copy.deepcopy(stage2_reference), ROOT
        )
    except CAPITAL_FLOW.CapitalFlowPostureReferenceError as exc:
        raise RotationCandidateSelectionInputError(
            f"STAGE2_SOURCE_REVALIDATION_FAILED:{exc}"
        ) from exc
    sources = checked.get("sources")
    index = contract["stage2_stage1_source_index"]
    if not isinstance(sources, list) or len(sources) <= index:
        raise RotationCandidateSelectionInputError("STAGE2_STAGE1_BINDING_MISSING")
    expected = {
        "source_type": contract["stage2_stage1_source_type"],
        "path": contract["stage2_stage1_source_path"],
        "sha256": stage1_file_sha256,
        "schema_version": checked_stage1["schema_version"],
        "contract_version": checked_stage1["contract_version"],
        "payload_sha256": checked_stage1["payload_sha256"],
        "generation_id": checked_stage1["generation_id"],
    }
    if sources[index] != expected:
        raise RotationCandidateSelectionInputError("STAGE2_STAGE1_BINDING_MISMATCH")
    for key in (
        "action_authorized", "actual_flow_claim_authorized", "buy_authorized",
        "cash_target_authorized", "cross_market_allocation_authorized",
        "gross_exposure_authorized", "order_authorized",
        "position_size_authorized", "production_authorized", "stage_authorized",
        "trading_authorized",
    ):
        if checked.get("authority", {}).get(key) is not False:
            raise RotationCandidateSelectionInputError(
                f"STAGE2_AUTHORITY_OPENED:{key}"
            )
    return checked, _producer_file_sha256(checked)


def _stage1_lineage(
    stage1: dict, stage1_file_sha256: str,
    stage2: dict, stage2_file_sha256: str,
    contract: dict,
) -> dict:
    markets = []
    for source in stage1["markets"]:
        projected = {
            "market": source["market"],
            "as_of_date": source["as_of_date"],
            "candidate_regime": source["paper_reference"]["candidate_regime"],
            "runtime_regime": source["runtime_regime"],
        }
        if list(projected) != contract["stage1_projected_market_fields"]:
            raise RotationCandidateSelectionInputError(
                "STAGE1_PROJECTED_MARKET_FIELDS_INVALID"
            )
        markets.append(projected)
    return {
        "schema_version": stage1["schema_version"],
        "contract_version": stage1["contract_version"],
        "generation_id": stage1["generation_id"],
        "payload_sha256": stage1["payload_sha256"],
        "file_sha256": stage1_file_sha256,
        "markets": markets,
        "stage2_binding": {
            "schema_version": stage2["schema_version"],
            "contract_version": stage2["contract_version"],
            "generation_id": stage2["generation_id"],
            "payload_sha256": stage2["payload_sha256"],
            "file_sha256": stage2_file_sha256,
            "stage1_source_index": contract["stage2_stage1_source_index"],
            "status": "EXACT_STAGE2_TO_STAGE1_BINDING_REVALIDATED",
        },
    }


def _project(
    checked: dict, rotation: dict, stage1_lineage: dict, contract: dict,
) -> dict:
    """Derive the whole projection from the ledger-derived rotation section."""
    rows = []
    for change in rotation["latest_changes"]:
        row = {
            field: copy.deepcopy(change[field])
            for field in contract["projected_source_fields"]
        }
        row.update(copy.deepcopy(contract["closed_constants"]))
        rows.append(row)
    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": contract["status"],
        "source": {
            "contract_version": checked["contract_version"],
            "schema_version": checked["schema_version"],
            "section": contract["source_section"],
            "slot": checked["slot"],
            "generated_at": checked["generated_at"],
            "status": checked["status"],
            "briefing_sha256": checked["packet_sha256"],
            "rotation_ledger_sha256": rotation["source_ledger_sha256"],
        },
        "input_count": len(rows),
        "inputs": rows,
        "stage1_lineage": copy.deepcopy(stage1_lineage),
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": copy.deepcopy(checked["unresolved_boundaries"]),
    }


def build_candidate_selection_input(
    briefing: dict,
    rotation_ledger: dict,
    contract: dict | None = None,
    *,
    stage2_reference: dict,
    stage1_reference: dict,
    source_contract: dict | None = None,
    wildcard_root: Path = ROOT,
    dart_root: Path = ROOT,
) -> dict:
    """Project a validated briefing plus its bound ledger into selection input."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    source_contract = _checked_source_contract(source_contract)
    checked = _checked_briefing(
        briefing, contract, source_contract, Path(wildcard_root), Path(dart_root)
    )
    rotation = _checked_rotation(checked, rotation_ledger, contract, source_contract)
    stage1, stage1_file_sha256 = _checked_stage1(stage1_reference, contract)
    stage2, stage2_file_sha256 = _checked_stage2(
        stage2_reference, stage1, stage1_file_sha256, contract
    )
    lineage = _stage1_lineage(
        stage1, stage1_file_sha256, stage2, stage2_file_sha256, contract
    )
    packet = _project(checked, rotation, lineage, contract)
    packet["payload_sha256"] = payload_sha256(packet)
    return validate_candidate_selection_input(
        packet,
        briefing,
        rotation_ledger,
        contract,
        stage2_reference=stage2_reference,
        stage1_reference=stage1_reference,
        source_contract=source_contract,
        wildcard_root=wildcard_root,
        dart_root=dart_root,
    )


def validate_candidate_selection_input(
    packet: dict,
    briefing: dict,
    rotation_ledger: dict,
    contract: dict | None = None,
    *,
    stage2_reference: dict,
    stage1_reference: dict,
    source_contract: dict | None = None,
    wildcard_root: Path = ROOT,
    dart_root: Path = ROOT,
) -> dict:
    """Re-derive the projection from the same source pair and compare.

    The packet is never trusted on its own, and neither is the briefing.  Row
    order, row fields, carried metadata, carried boundaries, bound source
    hashes, and the closed constants are all re-derived from the revalidated
    briefing and the exact ledger it bound, and the digest is recomputed over
    the re-derived bytes.
    """
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "source", "input_count",
        "inputs", "stage1_lineage", "authority", "unresolved_boundaries",
        "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise RotationCandidateSelectionInputError("INPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status") != contract["status"]
        or packet.get("authority") != contract["authority"]
    ):
        raise RotationCandidateSelectionInputError("INPUT_IDENTITY_INVALID")
    inputs = packet.get("inputs")
    if not isinstance(inputs, list):
        raise RotationCandidateSelectionInputError("INPUT_ROWS_INVALID")
    row_fields = set(contract["projected_source_fields"]) | set(
        contract["closed_constants"]
    )
    for row in inputs:
        if not isinstance(row, dict) or set(row) != row_fields:
            raise RotationCandidateSelectionInputError("INPUT_ROW_FIELDS_INVALID")
        for key, closed in contract["closed_constants"].items():
            if row.get(key) != closed or type(row.get(key)) is not type(closed):
                raise RotationCandidateSelectionInputError(
                    f"INPUT_ROW_AUTHORITY_OPENED:{key}"
                )
    if (
        type(packet.get("input_count")) is not int
        or packet["input_count"] != len(inputs)
    ):
        raise RotationCandidateSelectionInputError("INPUT_COUNT_INVALID")
    digest = packet.get("payload_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise RotationCandidateSelectionInputError("INPUT_SHA_INVALID")

    source_contract = _checked_source_contract(source_contract)
    checked = _checked_briefing(
        briefing, contract, source_contract, Path(wildcard_root), Path(dart_root)
    )
    rotation = _checked_rotation(checked, rotation_ledger, contract, source_contract)
    stage1, stage1_file_sha256 = _checked_stage1(stage1_reference, contract)
    stage2, stage2_file_sha256 = _checked_stage2(
        stage2_reference, stage1, stage1_file_sha256, contract
    )
    lineage = _stage1_lineage(
        stage1, stage1_file_sha256, stage2, stage2_file_sha256, contract
    )
    expected = _project(checked, rotation, lineage, contract)
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256")
    if unsigned != expected:
        raise RotationCandidateSelectionInputError("INPUT_DERIVATION_MISMATCH")
    if payload_sha256(expected) != digest:
        raise RotationCandidateSelectionInputError("INPUT_SHA_MISMATCH")
    return copy.deepcopy(packet)


def _within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _reject_repository_output(path: Path, root: Path) -> None:
    """Reject any output path that reaches the repository, symlinks included.

    ``Path.resolve()`` on its own is not a repository guard, because it follows
    symlinks in both directions.  A lexically in-repository path whose parent is
    a symlink resolves *outside* the repository and passes a resolved-only
    check, while the tracked path is still what gets created; conversely an
    out-of-repository path can resolve back *into* the repository.  The lexical
    path, the fully resolved path, and every symlink on the way are therefore
    all rejected, and the whole guard is re-run immediately before the replace.
    """
    lexical_root = Path(os.path.abspath(str(root)))
    real_root = Path(os.path.realpath(str(root)))
    lexical = Path(os.path.abspath(str(path)))
    for entry in (lexical, *lexical.parents):
        if not entry.is_symlink():
            continue
        if (
            _within(entry, lexical_root)
            or _within(entry, real_root)
            or _within(Path(os.path.realpath(str(entry))), real_root)
        ):
            raise RotationCandidateSelectionInputError(
                f"TRACKED_OUTPUT_SYMLINK_FORBIDDEN:{path}"
            )
    if _within(lexical, lexical_root) or _within(
        Path(os.path.realpath(str(path))), real_root
    ):
        raise RotationCandidateSelectionInputError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")


def write_json_atomic(path: Path, value: dict, root: Path = ROOT) -> None:
    path = Path(path)
    root = Path(root)
    _reject_repository_output(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _reject_repository_output(path, root)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run(
    briefing_path: Path, ledger_path: Path, stage2_path: Path,
    stage1_path: Path, output_path: Path,
) -> int:
    try:
        packet = build_candidate_selection_input(
            _read_json(briefing_path),
            _read_json(ledger_path),
            stage2_reference=_read_json(stage2_path),
            stage1_reference=_read_json(stage1_path),
        )
        write_json_atomic(output_path, packet)
        return 0
    except (
        RotationCandidateSelectionInputError,
        BRIEFING.RotationDiscoveryBriefingError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"Rotation candidate-selection input projection failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Project a Rotation Discovery briefing into selection input"
    )
    parser.add_argument("briefing", type=Path)
    parser.add_argument("--rotation-ledger", type=Path, required=True)
    parser.add_argument("--stage2-posture-reference", type=Path, required=True)
    parser.add_argument("--stage1-paper-reference", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(
        args.briefing,
        args.rotation_ledger,
        args.stage2_posture_reference,
        args.stage1_paper_reference,
        args.out,
    )


if __name__ == "__main__":
    raise SystemExit(main())
