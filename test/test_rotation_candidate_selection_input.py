#!/usr/bin/env python3
"""Rotation Stage 3 candidate-selection input projection regression."""

import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "briefing" / "rotation_candidate_selection_input.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("rotation_candidate_selection_input", SOURCE)
BRIEFING = MODULE.BRIEFING
ROTATION_FIXTURE = load_module(
    "candidate_input_rotation_fixture", ROOT / "test" / "test_rotation_state_ledger.py"
)
DISCOVERY_FIXTURE = load_module(
    "candidate_input_event_fixture", ROOT / "test" / "test_event_discovery_case.py"
)
CONTRACT = MODULE.load_contract()
BRIEFING_CONTRACT = BRIEFING.load_contract()
GENERATED_AT = "2026-08-21T02:00:00Z"
STAGE1_PATH = ROOT / (
    "evidence/regime/paper_reference/2026-09-12/"
    "ab1283311ef0e4d2e9045dc95b25d14e289bdb3066616943d88c1ca31e1a0cce/"
    "packet.json"
)
STAGE2_PATH = ROOT / (
    "evidence/portfolio/capital_flow_posture_reference/2026-09-12/"
    "c396052fc6a335826f843276627b31f009c338ac0277cbc1d988f8fc6eae802a/"
    "packet.json"
)
STAGE1_REFERENCE = json.loads(STAGE1_PATH.read_text(encoding="utf-8"))
STAGE2_REFERENCE = json.loads(STAGE2_PATH.read_text(encoding="utf-8"))


def build_input(
    source_briefing,
    source_ledger,
    contract=CONTRACT,
    *,
    stage2_reference=STAGE2_REFERENCE,
    stage1_reference=STAGE1_REFERENCE,
):
    return MODULE.build_candidate_selection_input(
        source_briefing,
        source_ledger,
        contract,
        stage2_reference=stage2_reference,
        stage1_reference=stage1_reference,
    )


def validate_input(
    packet,
    source_briefing,
    source_ledger,
    contract=CONTRACT,
    *,
    stage2_reference=STAGE2_REFERENCE,
    stage1_reference=STAGE1_REFERENCE,
):
    return MODULE.validate_candidate_selection_input(
        packet,
        source_briefing,
        source_ledger,
        contract,
        stage2_reference=stage2_reference,
        stage1_reference=stage1_reference,
    )


def observed_ledger():
    packet = ROTATION_FIXTURE.us_packet()
    return BRIEFING.ROTATION.apply_rotation(
        packet, ROTATION_FIXTURE.policy_for(packet)
    )


def briefing(ledger, slot="morning", generated_at=GENERATED_AT):
    return BRIEFING.build_briefing(
        ledger,
        [DISCOVERY_FIXTURE.d1_record()],
        DISCOVERY_FIXTURE.bindings(),
        slot,
        generated_at,
        BRIEFING_CONTRACT,
    )


def resign(value):
    """Recompute every digest and count a self-resigned briefing would carry.

    This is the exact capability the source-admission guard has to survive: a
    tamperer who edits ``rotation.latest_changes`` and then repairs the packet's
    own self-consistency.  Each adversarial case below asserts that the result
    is still accepted by the unmodified producer validator before asserting
    that this projection rejects it.
    """
    packet = copy.deepcopy(value)
    rotation = packet["rotation"]
    counts = {state: 0 for state in BRIEFING_CONTRACT["rotation_states"]}
    for row in rotation["latest_changes"]:
        counts[row["current_state"]] += 1
    rotation["latest_change_count"] = len(rotation["latest_changes"])
    rotation["state_counts"] = counts
    packet["summary"]["rotation_change_count"] = len(rotation["latest_changes"])
    packet.pop("packet_sha256")
    packet["packet_sha256"] = BRIEFING.payload_sha256(packet)
    return packet


class RotationCandidateSelectionInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = observed_ledger()
        cls.empty_ledger = BRIEFING.ROTATION.empty_ledger()
        cls.observed = briefing(cls.ledger)
        cls.empty = briefing(cls.empty_ledger)
        cls.packet = build_input(cls.observed, cls.ledger, CONTRACT)

    # ---------------------------------------------------------------- positive

    def test_contract_is_projection_only_and_closes_selection_authority(self):
        self.assertTrue(CONTRACT["authority"]["input_projection_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "input_projection_only":
                self.assertFalse(value, key)
        self.assertEqual(CONTRACT["source_section"], "rotation.latest_changes")
        self.assertEqual(
            CONTRACT["source_output_schema_version"],
            "rotation_discovery_briefing_packet/4",
        )
        self.assertEqual(CONTRACT["source_ledger_contract"], "rotation_state_ledger/1")
        self.assertEqual(
            CONTRACT["source_ledger_schema_version"],
            "rotation_state_ledger_packet/1",
        )
        self.assertEqual(CONTRACT["contract_version"], "rotation_candidate_selection_input/2")
        self.assertEqual(
            CONTRACT["output_schema_version"],
            "rotation_candidate_selection_input_packet/2",
        )
        self.assertEqual(CONTRACT["stage1_market_order"], ["US", "KR", "CRYPTO"])

    def test_real_stage2_to_stage1_lineage_is_revalidated_and_forwarded(self):
        lineage = self.packet["stage1_lineage"]
        self.assertEqual(
            lineage["generation_id"],
            "ab1283311ef0e4d2e9045dc95b25d14e289bdb3066616943d88c1ca31e1a0cce",
        )
        self.assertEqual(
            lineage["payload_sha256"],
            "d1a26695574361c4a2d38c958a08ddf76b4f3cf00cc800bf2e3cf40543f35daf",
        )
        self.assertEqual(
            lineage["file_sha256"],
            "4f714994db0dbcc65fdfc58d2440bc62552f121a02d7a811d22d0a93ec659da7",
        )
        self.assertEqual(
            lineage["markets"],
            [
                {
                    "market": "US",
                    "as_of_date": "2026-09-11",
                    "candidate_regime": "NEUTRAL",
                    "runtime_regime": "UNKNOWN",
                },
                {
                    "market": "KR",
                    "as_of_date": "2026-09-10",
                    "candidate_regime": "NEUTRAL",
                    "runtime_regime": "UNKNOWN",
                },
                {
                    "market": "CRYPTO",
                    "as_of_date": "2026-09-12",
                    "candidate_regime": "NEUTRAL",
                    "runtime_regime": "UNKNOWN",
                },
            ],
        )
        stage2 = lineage["stage2_binding"]
        self.assertEqual(
            stage2["generation_id"],
            "c396052fc6a335826f843276627b31f009c338ac0277cbc1d988f8fc6eae802a",
        )
        self.assertEqual(
            stage2["payload_sha256"],
            "a787569b8bc67e686e1b34f076e860a16294d1687e7144eb8a1cdb2886ea762a",
        )
        self.assertEqual(
            stage2["file_sha256"],
            "dd8cf8c5cf6c177b2a4fe4df0dbfbe6ac556afce30f9c7dbd385223e9463ec1b",
        )
        self.assertEqual(
            stage2["status"], "EXACT_STAGE2_TO_STAGE1_BINDING_REVALIDATED"
        )

    def test_stage1_or_stage2_self_resigning_cannot_replace_real_inputs(self):
        stage1 = copy.deepcopy(STAGE1_REFERENCE)
        stage1["markets"][0]["paper_reference"]["candidate_regime"] = "RISK_ON"
        stage1.pop("payload_sha256")
        stage1["payload_sha256"] = MODULE.PAPER_REGIME.payload_sha256(stage1)
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionInputError,
            "STAGE1_SOURCE_REVALIDATION_FAILED",
        ):
            build_input(
                self.observed, self.ledger, stage1_reference=stage1
            )

        stage2 = copy.deepcopy(STAGE2_REFERENCE)
        stage2["sources"][0]["generation_id"] = "0" * 64
        stage2.pop("payload_sha256")
        stage2["payload_sha256"] = MODULE.CAPITAL_FLOW.payload_sha256(stage2)
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionInputError,
            "STAGE2_SOURCE_REVALIDATION_FAILED",
        ):
            build_input(
                self.observed, self.ledger, stage2_reference=stage2
            )

    def test_stage1_and_stage2_are_both_mandatory_caller_inputs(self):
        for kwargs, error in (
            ({"stage1_reference": None}, "STAGE1_SOURCE_INVALID"),
            ({"stage2_reference": None}, "STAGE2_SOURCE_INVALID"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.RotationCandidateSelectionInputError, error
            ):
                build_input(self.observed, self.ledger, **kwargs)

    def test_stage1_lineage_cannot_be_rebound_inside_stage3_packet(self):
        packet = copy.deepcopy(self.packet)
        packet["stage1_lineage"]["markets"][1]["market"] = "KOREA"
        packet["payload_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in packet.items() if key != "payload_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionInputError,
            "INPUT_DERIVATION_MISMATCH",
        ):
            validate_input(packet, self.observed, self.ledger)

    def test_latest_changes_are_projected_one_to_one_in_existing_order(self):
        changes = self.observed["rotation"]["latest_changes"]
        self.assertGreater(len(changes), 0)
        self.assertEqual(self.packet["input_count"], len(changes))
        self.assertEqual(len(self.packet["inputs"]), len(changes))
        for row, change in zip(self.packet["inputs"], changes):
            for field in CONTRACT["projected_source_fields"]:
                self.assertEqual(row[field], change[field], field)
        self.assertEqual(
            [row["entity_id"] for row in self.packet["inputs"]],
            [change["entity_id"] for change in changes],
        )

    def test_every_row_carries_only_source_fields_and_closed_constants(self):
        expected_fields = set(CONTRACT["projected_source_fields"]) | set(
            CONTRACT["closed_constants"]
        )
        for row in self.packet["inputs"]:
            self.assertEqual(set(row), expected_fields)
            self.assertIsNone(row["selection_rank"])
            self.assertIs(row["selected"], False)
            self.assertIs(row["candidate_eligible"], False)
            self.assertEqual(row["ready_status"], "NOT_EVALUATED")
            self.assertEqual(row["promotion_status"], "PROMOTION_NOT_AUTHORIZED")
            self.assertIsNone(row["action"])

    def test_briefing_metadata_boundaries_and_source_hashes_are_bound(self):
        source = self.packet["source"]
        self.assertEqual(source["slot"], self.observed["slot"])
        self.assertEqual(source["generated_at"], self.observed["generated_at"])
        self.assertEqual(source["status"], self.observed["status"])
        self.assertEqual(source["contract_version"], self.observed["contract_version"])
        self.assertEqual(source["schema_version"], self.observed["schema_version"])
        self.assertEqual(source["briefing_sha256"], self.observed["packet_sha256"])
        self.assertEqual(
            source["rotation_ledger_sha256"],
            self.observed["rotation"]["source_ledger_sha256"],
        )
        self.assertEqual(
            source["rotation_ledger_sha256"], self.ledger["payload_sha256"]
        )
        self.assertEqual(
            self.packet["unresolved_boundaries"],
            self.observed["unresolved_boundaries"],
        )

    def test_rows_are_rederived_from_the_bound_ledger_records(self):
        """Every projected row must trace to a record of the supplied ledger."""
        by_record_sha = {
            record["record_sha256"]: record for record in self.ledger["records"]
        }
        self.assertGreater(len(self.packet["inputs"]), 0)
        for row in self.packet["inputs"]:
            record = by_record_sha.get(row["record_sha256"])
            self.assertIsNotNone(record, row["record_sha256"])
            self.assertEqual(row["market"], record["market"])
            self.assertEqual(row["scope_id"], record["scope_id"])
            self.assertEqual(row["entity_id"], record["entity_id"])
            self.assertEqual(row["as_of_date"], record["as_of_date"])
            self.assertEqual(row["prior_state"], record["prior_p2_state"])
            self.assertEqual(row["current_state"], record["current_p2_state"])
            self.assertEqual(row["state_transition"], record["state_transition"])
            self.assertEqual(
                row["source_packet_sha256"], record["input_packet_sha256"]
            )

    def test_payload_hash_is_deterministic_and_source_bound(self):
        again = build_input(
            self.observed, self.ledger, CONTRACT
        )
        self.assertEqual(again, self.packet)
        self.assertEqual(again["payload_sha256"], self.packet["payload_sha256"])
        unsigned = copy.deepcopy(self.packet)
        unsigned.pop("payload_sha256")
        self.assertEqual(
            MODULE.payload_sha256(unsigned), self.packet["payload_sha256"]
        )
        other = build_input(
            self.empty, self.empty_ledger, CONTRACT
        )
        self.assertNotEqual(other["payload_sha256"], self.packet["payload_sha256"])

    def test_validator_rederives_projection_from_the_same_source_pair(self):
        self.assertEqual(
            validate_input(
                self.packet, self.observed, self.ledger, CONTRACT
            ),
            self.packet,
        )

    def test_empty_rotation_projects_zero_rows_without_inventing_a_candidate(self):
        packet = build_input(
            self.empty, self.empty_ledger, CONTRACT
        )
        self.assertEqual(packet["inputs"], [])
        self.assertEqual(packet["input_count"], 0)
        self.assertEqual(
            packet["unresolved_boundaries"], self.empty["unresolved_boundaries"]
        )

    def test_returned_packet_is_a_copy_the_caller_cannot_mutate_into_source(self):
        packet = build_input(
            self.observed, self.ledger, CONTRACT
        )
        packet["inputs"][0]["selected"] = True
        self.assertIs(self.packet["inputs"][0]["selected"], False)
        self.assertEqual(
            self.observed["rotation"]["latest_changes"],
            briefing(observed_ledger())["rotation"]["latest_changes"],
        )
        self.assertEqual(self.ledger, observed_ledger())

    # ------------------------------------------------------------- adversarial

    def test_non_briefing_input_is_rejected(self):
        for value, error in (
            (None, "SOURCE_BRIEFING_INVALID"),
            ([], "SOURCE_BRIEFING_INVALID"),
            ("packet", "SOURCE_BRIEFING_INVALID"),
            ({}, "SOURCE_BRIEFING_SCHEMA_INVALID"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.RotationCandidateSelectionInputError, error
            ):
                build_input(value, self.ledger, CONTRACT)

    def test_foreign_or_downgraded_source_schema_is_rejected(self):
        wrong_schema = copy.deepcopy(self.observed)
        wrong_schema["schema_version"] = "rotation_discovery_briefing_packet/3"
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionInputError,
            "SOURCE_BRIEFING_SCHEMA_INVALID",
        ):
            build_input(
                wrong_schema, self.ledger, CONTRACT
            )
        wrong_contract = copy.deepcopy(self.observed)
        wrong_contract["contract_version"] = "rotation_discovery_briefing/3"
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionInputError,
            "SOURCE_BRIEFING_CONTRACT_INVALID",
        ):
            build_input(
                wrong_contract, self.ledger, CONTRACT
            )

    def test_tampered_briefing_fails_the_source_validator_before_projection(self):
        forged = copy.deepcopy(self.observed)
        forged["rotation"]["latest_changes"][0]["current_state"] = "STRONG"
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionInputError,
            "SOURCE_BRIEFING_REVALIDATION_FAILED",
        ):
            build_input(forged, self.ledger, CONTRACT)

        opened = copy.deepcopy(self.observed)
        opened["authority"]["stage_promotion_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionInputError,
            "SOURCE_BRIEFING_REVALIDATION_FAILED",
        ):
            build_input(opened, self.ledger, CONTRACT)

    # -- bound rotation_state_ledger source admission -------------------------

    def test_bound_rotation_state_ledger_packet_is_required(self):
        for value, error in (
            (None, "SOURCE_LEDGER_INVALID"),
            ([], "SOURCE_LEDGER_INVALID"),
            ("ledger", "SOURCE_LEDGER_INVALID"),
            ({}, "SOURCE_LEDGER_SCHEMA_INVALID"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.RotationCandidateSelectionInputError, error
            ):
                build_input(
                    self.observed, value, CONTRACT
                )

    def test_foreign_or_self_rehashed_ledger_packet_is_rejected(self):
        wrong_schema = copy.deepcopy(self.ledger)
        wrong_schema["schema_version"] = "rotation_state_ledger_packet/0"
        wrong_contract = copy.deepcopy(self.ledger)
        wrong_contract["contract_version"] = "rotation_state_ledger/0"
        rehashed = copy.deepcopy(self.ledger)
        record = rehashed["records"][0]
        record["current_p2_state"] = next(
            state
            for state in BRIEFING_CONTRACT["rotation_states"]
            if state != record["current_p2_state"]
        )
        rehashed.pop("payload_sha256")
        rehashed["payload_sha256"] = BRIEFING.ROTATION.payload_sha256(rehashed)
        for ledger, error in (
            (wrong_schema, "SOURCE_LEDGER_SCHEMA_INVALID"),
            (wrong_contract, "SOURCE_LEDGER_CONTRACT_INVALID"),
            (rehashed, "SOURCE_LEDGER_REVALIDATION_FAILED"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.RotationCandidateSelectionInputError, error
            ):
                build_input(
                    self.observed, ledger, CONTRACT
                )

    def test_ledger_not_bound_by_source_ledger_sha256_is_rejected(self):
        """A valid but different ledger cannot stand in for the bound one."""
        self.assertNotEqual(
            self.empty_ledger["payload_sha256"], self.ledger["payload_sha256"]
        )
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionInputError, "SOURCE_LEDGER_NOT_BOUND"
        ):
            build_input(
                self.observed, self.empty_ledger, CONTRACT
            )
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionInputError, "SOURCE_LEDGER_NOT_BOUND"
        ):
            build_input(
                self.empty, self.ledger, CONTRACT
            )
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionInputError, "SOURCE_LEDGER_NOT_BOUND"
        ):
            validate_input(
                self.packet, self.observed, self.empty_ledger, CONTRACT
            )

    def test_resigned_briefing_row_tampering_is_rejected_against_the_ledger(self):
        """Re-signed row add/drop/reorder/state/hash tampering must fail closed.

        Each mutation below repairs ``state_counts``, ``latest_change_count``,
        ``summary`` and ``packet_sha256``, so the producer's own validator still
        accepts the briefing.  Only re-deriving the rotation section from the
        bound ledger catches them.
        """
        rows = self.observed["rotation"]["latest_changes"]
        self.assertGreater(len(rows), 1)
        states = BRIEFING_CONTRACT["rotation_states"]
        carried = (
            "as_of_date", "structural_bucket_transition", "prior_state",
            "current_state", "state_transition", "record_sha256",
            "source_packet_sha256",
        )

        def dropped(packet):
            packet["rotation"]["latest_changes"].pop()

        def added(packet):
            changes = packet["rotation"]["latest_changes"]
            extra = copy.deepcopy(changes[-1])
            extra["entity_id"] = f"{extra['entity_id']}_X"
            changes.append(extra)

        def restated(packet):
            row = packet["rotation"]["latest_changes"][0]
            other = next(s for s in states if s != row["current_state"])
            row["current_state"] = other
            row["state_transition"] = (
                f"UNINITIALIZED_TO_{other}"
                if row["prior_state"] is None
                else f"{row['prior_state']}_TO_{other}"
            )

        def rehashed(packet):
            packet["rotation"]["latest_changes"][0]["record_sha256"] = "a" * 64

        def reordered(packet):
            changes = packet["rotation"]["latest_changes"]
            first, second = changes[0], changes[1]
            for field in carried:
                first[field], second[field] = second[field], first[field]

        for name, mutate in (
            ("dropped", dropped),
            ("added", added),
            ("restated", restated),
            ("rehashed", rehashed),
            ("reordered", reordered),
        ):
            with self.subTest(tamper=name):
                tampered = copy.deepcopy(self.observed)
                mutate(tampered)
                tampered = resign(tampered)
                self.assertNotEqual(
                    tampered["rotation"]["latest_changes"],
                    self.observed["rotation"]["latest_changes"],
                )
                # The producer still accepts the re-signed briefing.
                self.assertEqual(
                    BRIEFING.validate_briefing(tampered, BRIEFING_CONTRACT), tampered
                )
                self.assertEqual(
                    tampered["rotation"]["source_ledger_sha256"],
                    self.ledger["payload_sha256"],
                )
                with self.assertRaisesRegex(
                    MODULE.RotationCandidateSelectionInputError,
                    "SOURCE_ROTATION_SECTION_TAMPERED",
                ):
                    build_input(
                        tampered, self.ledger, CONTRACT
                    )
                with self.assertRaisesRegex(
                    MODULE.RotationCandidateSelectionInputError,
                    "SOURCE_ROTATION_SECTION_TAMPERED",
                ):
                    validate_input(
                        self.packet, tampered, self.ledger, CONTRACT
                    )

    def test_resigned_rotation_summary_tampering_is_rejected_against_the_ledger(self):
        """Section metadata around the rows is re-derived from the ledger too."""
        mutations = {
            "ledger_status": lambda p: p["rotation"].update(
                {"ledger_status": "EMPTY"}
            ),
            "ledger_revision": lambda p: p["rotation"].update(
                {"ledger_revision": p["rotation"]["ledger_revision"] + 1}
            ),
            "source_boundaries": lambda p: p["rotation"]["source_boundaries"].append(
                "PRODUCTION_AUTHORIZED"
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                tampered = copy.deepcopy(self.observed)
                mutate(tampered)
                tampered = resign(tampered)
                with self.assertRaisesRegex(
                    MODULE.RotationCandidateSelectionInputError,
                    "SOURCE_ROTATION_SECTION_TAMPERED",
                ):
                    build_input(
                        tampered, self.ledger, CONTRACT
                    )

    # -- projected packet tampering -------------------------------------------

    def test_reordered_or_resized_projection_is_rejected(self):
        self.assertGreater(len(self.packet["inputs"]), 1)
        reordered = copy.deepcopy(self.packet)
        reordered["inputs"].reverse()
        dropped = copy.deepcopy(self.packet)
        dropped["inputs"] = dropped["inputs"][1:]
        dropped["input_count"] = len(dropped["inputs"])
        duplicated = copy.deepcopy(self.packet)
        duplicated["inputs"].append(copy.deepcopy(duplicated["inputs"][0]))
        duplicated["input_count"] = len(duplicated["inputs"])
        miscounted = copy.deepcopy(self.packet)
        miscounted["input_count"] = len(miscounted["inputs"]) + 1
        for packet, error in (
            (reordered, "INPUT_DERIVATION_MISMATCH"),
            (dropped, "INPUT_DERIVATION_MISMATCH"),
            (duplicated, "INPUT_DERIVATION_MISMATCH"),
            (miscounted, "INPUT_COUNT_INVALID"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.RotationCandidateSelectionInputError, error
            ):
                validate_input(
                    packet, self.observed, self.ledger, CONTRACT
                )

    def test_opened_closed_constant_is_rejected_per_field(self):
        for key, value in (
            ("selection_rank", 1),
            ("selected", True),
            ("selected", 0),
            ("candidate_eligible", True),
            ("ready_status", "READY"),
            ("promotion_status", "PROMOTED"),
            ("action", "ENTER"),
        ):
            packet = copy.deepcopy(self.packet)
            packet["inputs"][0][key] = value
            with self.subTest(field=key, value=value), self.assertRaisesRegex(
                MODULE.RotationCandidateSelectionInputError,
                f"INPUT_ROW_AUTHORITY_OPENED:{key}",
            ):
                validate_input(
                    packet, self.observed, self.ledger, CONTRACT
                )

    def test_added_or_removed_row_field_is_rejected(self):
        scored = copy.deepcopy(self.packet)
        scored["inputs"][0]["score"] = 0.9
        stripped = copy.deepcopy(self.packet)
        stripped["inputs"][0].pop("record_sha256")
        for packet in (scored, stripped):
            with self.assertRaisesRegex(
                MODULE.RotationCandidateSelectionInputError, "INPUT_ROW_FIELDS_INVALID"
            ):
                validate_input(
                    packet, self.observed, self.ledger, CONTRACT
                )

    def test_altered_source_field_value_is_rejected(self):
        packet = copy.deepcopy(self.packet)
        packet["inputs"][0]["record_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionInputError, "INPUT_DERIVATION_MISMATCH"
        ):
            validate_input(
                packet, self.observed, self.ledger, CONTRACT
            )

    def test_altered_metadata_boundaries_or_source_hash_is_rejected(self):
        mutations = {
            "briefing_sha256": lambda p: p["source"].update(
                {"briefing_sha256": "0" * 64}
            ),
            "rotation_ledger_sha256": lambda p: p["source"].update(
                {"rotation_ledger_sha256": "0" * 64}
            ),
            "slot": lambda p: p["source"].update({"slot": "evening"}),
            "generated_at": lambda p: p["source"].update(
                {"generated_at": "2026-08-22T02:00:00Z"}
            ),
            "section": lambda p: p["source"].update({"section": "rotation.selected"}),
            "dropped_boundary": lambda p: p["unresolved_boundaries"].pop(),
            "added_boundary": lambda p: p["unresolved_boundaries"].append(
                "SELECTION_AUTHORIZED"
            ),
        }
        for name, mutate in mutations.items():
            packet = copy.deepcopy(self.packet)
            mutate(packet)
            with self.subTest(mutation=name), self.assertRaisesRegex(
                MODULE.RotationCandidateSelectionInputError, "INPUT_DERIVATION_MISMATCH"
            ):
                validate_input(
                    packet, self.observed, self.ledger, CONTRACT
                )

    def test_identity_authority_field_and_digest_tampering_is_rejected(self):
        identity = copy.deepcopy(self.packet)
        identity["status"] = "ROTATION_CANDIDATE_SELECTED"
        authority = copy.deepcopy(self.packet)
        authority["authority"]["candidate_selection_authorized"] = True
        extra = copy.deepcopy(self.packet)
        extra["ranked_candidate"] = "theme-a"
        rows = copy.deepcopy(self.packet)
        rows["inputs"] = {}
        digest = copy.deepcopy(self.packet)
        digest["payload_sha256"] = "0" * 64
        malformed = copy.deepcopy(self.packet)
        malformed["payload_sha256"] = "not-a-digest"
        for packet, error in (
            (identity, "INPUT_IDENTITY_INVALID"),
            (authority, "INPUT_IDENTITY_INVALID"),
            (extra, "INPUT_FIELDS_MISMATCH"),
            (rows, "INPUT_ROWS_INVALID"),
            (digest, "INPUT_SHA_MISMATCH"),
            (malformed, "INPUT_SHA_INVALID"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.RotationCandidateSelectionInputError, error
            ):
                validate_input(
                    packet, self.observed, self.ledger, CONTRACT
                )

    def test_projection_is_rejected_against_a_different_source_briefing(self):
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionInputError, "INPUT_DERIVATION_MISMATCH"
        ):
            validate_input(
                self.packet, self.empty, self.empty_ledger, CONTRACT
            )
        evening = briefing(self.ledger, slot="evening")
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionInputError, "INPUT_DERIVATION_MISMATCH"
        ):
            validate_input(
                self.packet, evening, self.ledger, CONTRACT
            )

    def test_contract_tampering_is_rejected(self):
        opened = copy.deepcopy(CONTRACT)
        opened["authority"]["candidate_selection_authorized"] = True
        constants = copy.deepcopy(CONTRACT)
        constants["closed_constants"]["ready_status"] = "READY"
        ledger_contract = copy.deepcopy(CONTRACT)
        ledger_contract["source_ledger_contract"] = "rotation_state_ledger/0"
        truncated = copy.deepcopy(CONTRACT)
        truncated.pop("closed_constants")
        unbound = copy.deepcopy(CONTRACT)
        unbound.pop("source_ledger_contract")
        for contract, error in (
            (opened, "CONTRACT_FIELD_MISMATCH:authority"),
            (constants, "CONTRACT_FIELD_MISMATCH:closed_constants"),
            (ledger_contract, "CONTRACT_FIELD_MISMATCH:source_ledger_contract"),
            (truncated, "CONTRACT_FIELDS_MISMATCH"),
            (unbound, "CONTRACT_FIELDS_MISMATCH"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.RotationCandidateSelectionInputError, error
            ):
                build_input(
                    self.observed, self.ledger, contract
                )

    # -- tracked output -------------------------------------------------------

    def test_in_repository_symlink_or_symlinked_parent_output_is_rejected(self):
        """A resolved-only guard is not enough — both directions are closed.

        ``repo/escape`` is an in-repository symlink that points out of the tree,
        so the resolved path lands outside while the tracked path is still what
        would be created.  ``into-repo`` and ``self.json`` point the other way.
        """
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            repo = temp / "repo"
            tracked = repo / "data"
            tracked.mkdir(parents=True)
            outside = temp / "outside"
            outside.mkdir()

            escape = repo / "escape"
            escape.symlink_to(outside)
            into_repo = temp / "into-repo"
            into_repo.symlink_to(tracked)
            self_link = outside / "self.json"
            self_link.symlink_to(tracked / "linked.json")

            for target, error in (
                (tracked / "plain.json", "TRACKED_OUTPUT_FORBIDDEN"),
                (repo / "root.json", "TRACKED_OUTPUT_FORBIDDEN"),
                (escape / "escaped.json", "TRACKED_OUTPUT_SYMLINK_FORBIDDEN"),
                (into_repo / "redirected.json", "TRACKED_OUTPUT_SYMLINK_FORBIDDEN"),
                (self_link, "TRACKED_OUTPUT_SYMLINK_FORBIDDEN"),
            ):
                with self.subTest(target=str(target)), self.assertRaisesRegex(
                    MODULE.RotationCandidateSelectionInputError, error
                ):
                    MODULE.write_json_atomic(target, self.packet, root=repo)

            self.assertEqual(sorted(item.name for item in tracked.iterdir()), [])
            self.assertEqual(sorted(item.name for item in repo.iterdir()), ["data", "escape"])
            self.assertEqual(sorted(item.name for item in outside.iterdir()), ["self.json"])
            self.assertFalse(self_link.exists())

            allowed = outside / "allowed.json"
            MODULE.write_json_atomic(allowed, self.packet, root=repo)
            self.assertEqual(
                json.loads(allowed.read_text(encoding="utf-8")), self.packet
            )

    def test_cli_is_offline_and_writes_only_outside_repository(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)

        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            briefing_path = temp / "briefing.json"
            briefing_path.write_text(json.dumps(self.observed), encoding="utf-8")
            ledger_path = temp / "ledger.json"
            ledger_path.write_text(json.dumps(self.ledger), encoding="utf-8")
            self.assertEqual(
                MODULE.run(
                    briefing_path,
                    ledger_path,
                    STAGE2_PATH,
                    STAGE1_PATH,
                    temp / "out" / "input.json",
                ),
                0,
            )
            written = json.loads(
                (temp / "out" / "input.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                written["payload_sha256"], self.packet["payload_sha256"]
            )

            forbidden = ROOT / "data" / "rotation_candidate_selection_input_test.json"
            self.assertEqual(
                MODULE.run(
                    briefing_path, ledger_path, STAGE2_PATH, STAGE1_PATH, forbidden
                ),
                1,
            )
            self.assertFalse(forbidden.exists())

            unbound = temp / "unbound.json"
            unbound.write_text(json.dumps(self.empty_ledger), encoding="utf-8")
            self.assertEqual(
                MODULE.run(
                    briefing_path,
                    unbound,
                    STAGE2_PATH,
                    STAGE1_PATH,
                    temp / "out" / "unbound.json",
                ),
                1,
            )
            self.assertFalse((temp / "out" / "unbound.json").exists())

    def test_repository_symlinked_output_is_rejected_through_the_cli(self):
        """The real repository root is guarded, not just an injected one."""
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            briefing_path = temp / "briefing.json"
            briefing_path.write_text(json.dumps(self.observed), encoding="utf-8")
            ledger_path = temp / "ledger.json"
            ledger_path.write_text(json.dumps(self.ledger), encoding="utf-8")
            into_repo = temp / "into-repo"
            into_repo.symlink_to(ROOT / "data")
            target = into_repo / f"candidate-input-symlink-{os.getpid()}.json"
            self.assertEqual(
                MODULE.run(
                    briefing_path, ledger_path, STAGE2_PATH, STAGE1_PATH, target
                ),
                1,
            )
            self.assertFalse((ROOT / "data" / target.name).exists())


if __name__ == "__main__":
    unittest.main()
