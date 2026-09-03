#!/usr/bin/env python3
from __future__ import annotations

import copy
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "portfolio" / "capital_flow_posture_reference.py"
SPEC = importlib.util.spec_from_file_location("capital_flow_posture_reference_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class CapitalFlowPostureReferenceTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        shutil.copytree(ROOT / "config", self.root / "config")
        (self.root / "data").mkdir()
        for name in (
            "latest_free_market_data.json",
            "latest_korea_market_signals.json",
            "latest_crypto_regime_refresh_status.json",
        ):
            shutil.copy2(ROOT / "data" / name, self.root / "data" / name)
        paper = MODULE.PAPER_REGIME.build_reference(self.root)
        MODULE.PAPER_REGIME.write_packet(paper, self.root)

    def tearDown(self):
        self._temp.cleanup()

    def test_current_inputs_expose_two_stage_capital_model_without_numbers(self):
        packet = MODULE.build_reference(self.root)
        self.assertEqual(packet["cross_market_flow"]["actual_money_flow"], "UNKNOWN")
        self.assertIn(
            packet["cross_market_flow"]["comparison_status"],
            {"UNKNOWN", "PARTIAL_RELATIVE_STRENGTH_REFERENCE", "THREE_MARKET_RELATIVE_STRENGTH_REFERENCE"},
        )
        self.assertEqual(packet["total_exposure_review"]["review"], "WAIT_INCOMPLETE_MARKET_SET")
        self.assertIsNone(packet["total_exposure_review"]["invested_target_pct"])
        self.assertIsNone(packet["total_exposure_review"]["cash_target_pct"])

    def test_each_market_has_review_priority_but_no_target_weight(self):
        packet = MODULE.build_reference(self.root)
        reviews = {row["market"]: row for row in packet["market_allocation_reviews"]}
        self.assertEqual(set(reviews), {"US", "KR", "CRYPTO"})
        self.assertEqual(reviews["CRYPTO"]["review_priority"], "WAIT_FOR_COMPLETE_REGIME")
        self.assertTrue(all(row["target_weight_pct"] is None for row in reviews.values()))

        leaders = [row["market"] for row in reviews.values() if row["review_priority"] == "RELATIVE_STRENGTH_LEADER_REFERENCE"]
        laggards = [row["market"] for row in reviews.values() if row["review_priority"] == "RELATIVE_STRENGTH_LAGGARD_REFERENCE"]
        expected_leader = leaders[0] if len(leaders) == 1 else None
        expected_laggard = laggards[0] if len(laggards) == 1 else None
        self.assertEqual(packet["cross_market_flow"]["relative_strength_leader"], expected_leader)
        self.assertEqual(packet["cross_market_flow"]["relative_strength_laggard"], expected_laggard)

    def test_p2_com_01_contract_identity_is_hash_bound_and_read(self):
        packet = MODULE.build_reference(self.root)
        sources = {row["source_type"]: row for row in packet["sources"]}
        regime = sources["P1_PAPER_REGIME_REFERENCE_PACKET"]
        self.assertEqual(regime["schema_version"], "paper_regime_reference/v2")
        self.assertEqual(regime["contract_version"], "paper_regime_reference_policy/v1")
        self.assertEqual(len(regime["payload_sha256"]), 64)

        flow = sources["P2_COM_01_CROSS_ASSET_FLOW_CONTRACT"]
        flow_path = self.root / flow["path"]
        self.assertEqual(flow["sha256"], hashlib.sha256(flow_path.read_bytes()).hexdigest())
        self.assertEqual(flow["contract_version"], "cross_asset_flow_evidence/1")
        self.assertEqual(flow["output_schema_version"], "cross_asset_flow_evidence_packet/1")
        self.assertEqual(flow["cross_market_assessment_status"], "UNKNOWN")
        self.assertFalse(flow["cross_market_flow_claim_authorized"])
        self.assertEqual(
            packet["generation_id"],
            MODULE.payload_sha256({
                "policy_sha256": MODULE.file_sha256(
                    self.root / "config/capital_flow_posture_reference_policy_v1.json"
                ),
                "sources": packet["sources"],
            }),
        )

    def test_relative_candidates_are_not_promoted_to_actual_flow(self):
        packet = MODULE.build_reference(self.root)
        flow = packet["cross_market_flow"]
        candidates = packet["flow_candidates"]
        self.assertEqual(candidates["receiver_candidate"]["market"], flow["relative_strength_leader"])
        self.assertEqual(candidates["donor_candidate"]["market"], flow["relative_strength_laggard"])
        self.assertEqual(candidates["actual_flow_claim"], "UNKNOWN")
        self.assertIsNone(candidates["confidence"])
        # this fixture has no P2-COM-03 chain at all, so transition and
        # persistence stay honestly empty rather than being invented
        self.assertEqual(candidates["transition"]["status"], "UNKNOWN")
        self.assertEqual(
            candidates["transition"]["source"], "P2_COM_03_APPEND_ONLY_LEDGER"
        )
        self.assertEqual(
            candidates["transition"]["evidence_status"], "NO_PRIOR_RECORDED_HISTORY"
        )
        self.assertIsNone(candidates["transition"]["pending_type"])
        self.assertIsNone(candidates["persistence"]["observation_count"])
        self.assertIsNone(candidates["persistence"]["natural_observation_count"])
        self.assertIsNone(candidates["persistence"]["first_seen"])
        self.assertIsNone(candidates["persistence"]["confirmed_at"])
        self.assertEqual(
            candidates["persistence"]["confirmation_status"],
            "NOT_COMPUTABLE_POLICY_UNRATIFIED",
        )
        self.assertEqual(
            candidates["invalidation"]["status"],
            "NOT_COMPUTABLE_POLICY_UNRATIFIED",
        )

    def test_authority_boundary_keeps_capital_and_orders_closed(self):
        authority = MODULE.build_reference(self.root)["authority"]
        self.assertTrue(authority["paper_reference_display_authorized"])
        self.assertTrue(authority["relative_strength_comparison_authorized"])
        for key, value in authority.items():
            if key not in {"paper_reference_display_authorized", "relative_strength_comparison_authorized"}:
                self.assertFalse(value, key)

    def test_resigned_output_tamper_and_source_tamper_fail_closed(self):
        packet = MODULE.build_reference(self.root)
        self.assertEqual(MODULE.validate_reference(packet, self.root), packet)
        tampered = copy.deepcopy(packet)
        tampered["total_exposure_review"]["invested_target_pct"] = 80
        unsigned = copy.deepcopy(tampered)
        unsigned.pop("payload_sha256")
        tampered["payload_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(MODULE.CapitalFlowPostureReferenceError, "REFERENCE_REDERIVATION_MISMATCH"):
            MODULE.validate_reference(tampered, self.root)

        source = json.loads((self.root / "data/latest_paper_regime_reference.json").read_text())
        source["markets"][0]["paper_reference"]["score"] = 5
        unsigned_source = copy.deepcopy(source)
        unsigned_source.pop("payload_sha256")
        source["payload_sha256"] = MODULE.payload_sha256(unsigned_source)
        (self.root / "data/latest_paper_regime_reference.json").write_text(json.dumps(source), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.CapitalFlowPostureReferenceError, "SOURCE_REVALIDATION_FAILED"):
            MODULE.build_reference(self.root)

    def test_policy_identity_and_boolean_types_fail_closed(self):
        path = self.root / "config/capital_flow_posture_reference_policy_v1.json"
        original = json.loads(path.read_text())
        cases = [
            ("schema_version", True, "POLICY_VERSION_INVALID"),
            ("status", "RATIFIED", "POLICY_STATUS_INVALID"),
        ]
        for key, value, code in cases:
            with self.subTest(key=key):
                changed = copy.deepcopy(original)
                changed[key] = value
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaisesRegex(MODULE.CapitalFlowPostureReferenceError, code):
                    MODULE.build_reference(self.root)
        changed = copy.deepcopy(original)
        changed["authority"]["order_authorized"] = 0
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.CapitalFlowPostureReferenceError, "POLICY_AUTHORITY_INVALID"):
            MODULE.build_reference(self.root)

    def test_p2_com_01_contract_semantic_tamper_fails_even_with_new_file_digest(self):
        path = self.root / "config/cross_asset_flow_evidence_contract.json"
        value = json.loads(path.read_text())
        value["authority"]["cross_market_flow_claim_authorized"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(
            MODULE.CapitalFlowPostureReferenceError,
            "CROSS_ASSET_FLOW_CONTRACT_REVALIDATION_FAILED",
        ):
            MODULE.build_reference(self.root)

    def test_write_is_append_only_and_latest_is_identical(self):
        packet = MODULE.build_reference(self.root)
        evidence, latest = MODULE.write_packet(packet, self.root)
        self.assertEqual(evidence.read_bytes(), latest.read_bytes())
        self.assertEqual(MODULE.validate_reference(json.loads(latest.read_text()), self.root), packet)
        MODULE.write_packet(packet, self.root)


LEDGER = MODULE.transition_ledger_module()


def render(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


class CapitalFlowLedgerConsumptionTest(unittest.TestCase):
    """P2-COM-02 consumes the canonical P2-COM-03 chain instead of discarding it.

    The fixture copies the repository's own ratified ledger pointer, contract,
    and pinned predecessor evidence, so these tests read exactly the canonical
    chain rather than a private re-implementation of it.  Nothing tracked is
    ever written back.
    """

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        shutil.copytree(ROOT / "config", self.root / "config")
        (self.root / "data").mkdir()
        for name in (
            "latest_free_market_data.json",
            "latest_korea_market_signals.json",
            "latest_crypto_regime_refresh_status.json",
            "latest_cross_market_flow_transition_ledger.json",
        ):
            shutil.copy2(ROOT / "data" / name, self.root / "data" / name)
        self.contract_path = (
            self.root / "config" / "cross_market_flow_transition_ledger_contract.json"
        )
        self.pointer_path = (
            self.root / "data" / "latest_cross_market_flow_transition_ledger.json"
        )
        self.source_path = (
            self.root / "data" / "latest_capital_flow_posture_reference.json"
        )
        self.contract = LEDGER.load_contract(self.contract_path)
        relative = self.contract["predecessor"]["evidence_path"]
        evidence = self.root / relative
        evidence.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, evidence)
        self.predecessor = LEDGER.load_predecessor(self.contract, self.root)
        paper = MODULE.PAPER_REGIME.build_reference(self.root)
        MODULE.PAPER_REGIME.write_packet(paper, self.root)

    def tearDown(self):
        self._temp.cleanup()

    # ---- fixture plumbing -------------------------------------------------

    def _pointer(self) -> dict:
        return json.loads(self.pointer_path.read_text(encoding="utf-8"))

    def _write_pointer(self, value: dict) -> None:
        self.pointer_path.write_bytes(render(value))

    def _append(self, mode: str) -> dict:
        """Append this packet to the canonical chain exactly as P2-COM-03 does."""
        packet = MODULE.build_reference(self.root)
        MODULE.write_packet(packet, self.root)
        result = LEDGER.apply_observation(
            self.source_path,
            mode,
            self._pointer(),
            root=self.root,
            contract_path=self.contract_path,
        )
        self.assertEqual(result["action"], "V2_APPEND")
        self._write_pointer(result["ledger"])
        return result["ledger"]

    def _resign_pointer(self, mutate) -> None:
        """Re-sign a mutated pointer so only semantics, not hashes, can fail."""
        value = self._pointer()
        mutate(value)
        value.pop("payload_sha256")
        value["payload_sha256"] = LEDGER.payload_sha256(value)
        self._write_pointer(value)

    # ---- consumption ------------------------------------------------------

    def test_recorded_transition_and_persistence_are_consumed_not_discarded(self):
        packet = MODULE.build_reference(self.root)
        pointer = self._pointer()
        head = pointer["entries"][-1]
        transition = packet["flow_candidates"]["transition"]
        persistence = packet["flow_candidates"]["persistence"]

        self.assertEqual(transition["status"], "RECORDED_HISTORY_OBSERVED")
        self.assertEqual(transition["evidence_status"], "LEDGER_CONSUMED")
        self.assertEqual(transition["recorded_type"], head["transition"]["type"])
        self.assertEqual(
            transition["previous_semantic_state_sha256"],
            head["current_semantic_state_sha256"],
        )
        self.assertEqual(
            transition["previous_semantic_state"],
            LEDGER._semantic_state(head["current_state"]),
        )
        state = LEDGER._current_state({
            "status": packet["status"],
            "cross_market_flow": packet["cross_market_flow"],
        })
        self.assertEqual(
            transition["current_semantic_state_sha256"],
            LEDGER.payload_sha256(LEDGER._semantic_state(state)),
        )
        self.assertEqual(
            transition["pending_type"],
            LEDGER._transition_type(head["current_state"], state),
        )
        # the canonical chain has already observed this exact UNKNOWN state, so
        # the count that used to be null is now a real observed number
        self.assertIsInstance(persistence["observation_count"], int)
        self.assertGreaterEqual(persistence["observation_count"], 1)
        self.assertIsNotNone(persistence["first_seen"])
        self.assertEqual(persistence["status"],
                         "RECORDED_OBSERVATION_COUNT_CONFIRMATION_UNRATIFIED")

    def test_consumed_ledger_identity_is_hash_bound_into_generation_id(self):
        packet = MODULE.build_reference(self.root)
        source = {
            row["source_type"]: row for row in packet["sources"]
        }["P2_COM_03_TRANSITION_LEDGER"]
        self.assertEqual(
            source["contract_sha256"],
            hashlib.sha256(self.contract_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            source["contract_version"], "cross_market_flow_transition_ledger/2"
        )
        self.assertEqual(
            source["predecessor_payload_sha256"], self.predecessor["payload_sha256"]
        )
        self.assertEqual(
            source["consumed_head_entry_sha256"],
            self._pointer()["entries"][-1]["entry_sha256"],
        )
        self.assertEqual(
            packet["generation_id"],
            MODULE.payload_sha256({
                "policy_sha256": MODULE.file_sha256(
                    self.root / "config/capital_flow_posture_reference_policy_v1.json"
                ),
                "sources": packet["sources"],
            }),
        )

    def test_producer_counts_are_the_ledger_accounting_minus_the_pending_append(self):
        """The producer must not re-implement persistence; it must match P2-COM-03.

        The ledger's own ``_persistence`` includes the observation being
        appended.  The producer describes history only, so it must equal exactly
        that minus the pending append -- for every observation mode.
        """
        packet = MODULE.build_reference(self.root)
        persistence = packet["flow_candidates"]["persistence"]
        semantic_sha = packet["flow_candidates"]["transition"][
            "current_semantic_state_sha256"
        ]
        entries = self._pointer()["entries"]
        for mode in ("NATURAL", "MANUAL", "RECOVERY", "REPLAY"):
            with self.subTest(mode=mode):
                first_seen, expected = LEDGER._persistence(
                    entries, semantic_sha, mode, self.predecessor
                )
                natural = 1 if mode == "NATURAL" else 0
                self.assertEqual(
                    persistence["observation_count"] + 1,
                    expected["state_observation_count_total"],
                )
                self.assertEqual(
                    persistence["natural_observation_count"] + natural,
                    expected["state_natural_observation_count_total"],
                )
                self.assertEqual(persistence["first_seen"], first_seen)
        self.assertEqual(persistence["counted_observation_modes"], ["NATURAL"])
        self.assertEqual(
            persistence["excluded_observation_modes"],
            ["MANUAL", "RECOVERY", "REPLAY"],
        )
        self.assertFalse(persistence["counts_current_packet"])

    def test_manual_and_replay_observations_never_become_natural_evidence(self):
        """A non-NATURAL append raises the total count but never the natural one."""
        before = MODULE.build_reference(self.root)["flow_candidates"]["persistence"]
        semantic_sha = None
        for mode in ("MANUAL", "REPLAY"):
            with self.subTest(mode=mode):
                ledger = self._append(mode)
                head = ledger["entries"][-1]
                self.assertEqual(head["observation_mode"], mode)
                self.assertFalse(head["counts_toward_persistence"])
                self._advance_source_clock()
                after = MODULE.build_reference(self.root)
                persistence = after["flow_candidates"]["persistence"]
                semantic_sha = after["flow_candidates"]["transition"][
                    "current_semantic_state_sha256"
                ]
                self.assertEqual(
                    persistence["natural_observation_count"],
                    before["natural_observation_count"],
                    "a non-NATURAL observation was promoted to natural evidence",
                )
                self.assertGreater(
                    persistence["observation_count"], before["observation_count"]
                )
                self.assertEqual(persistence["current_streak_natural_count"], 0)
                before = persistence
        self.assertIsNotNone(semantic_sha)

    def _advance_source_clock(self) -> None:
        """Move the P1 clock one day forward so the next append is not drift."""
        path = self.root / "data" / "latest_free_market_data.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        observed = value["observed_at_utc"]
        moved = datetime.datetime.strptime(
            observed, "%Y-%m-%dT%H:%M:%SZ"
        ) + datetime.timedelta(days=1)
        value["observed_at_utc"] = moved.strftime("%Y-%m-%dT%H:%M:%SZ")
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        paper = MODULE.PAPER_REGIME.build_reference(self.root)
        MODULE.PAPER_REGIME.write_packet(paper, self.root)

    # ---- determinism ------------------------------------------------------

    def test_appending_this_packet_leaves_the_rebuild_byte_identical(self):
        """The ledger records this packet; rebuilding it must not see itself."""
        packet = MODULE.build_reference(self.root)
        pending = packet["flow_candidates"]["transition"]["pending_type"]
        before = packet["flow_candidates"]["persistence"]
        self._append("NATURAL")
        head = self._pointer()["entries"][-1]
        self.assertEqual(head["observed_at"], packet["generated_at"])
        # what the packet described as pending is exactly what P2-COM-03 recorded
        self.assertEqual(head["transition"]["type"], pending)
        self.assertEqual(
            head["persistence"]["state_observation_count_total"],
            before["observation_count"] + 1,
        )
        self.assertEqual(
            head["persistence"]["state_natural_observation_count_total"],
            before["natural_observation_count"] + 1,
        )
        rebuilt = MODULE.build_reference(self.root)
        self.assertEqual(rebuilt, packet)
        self.assertEqual(MODULE.validate_reference(packet, self.root), packet)
        self.assertEqual(
            self.source_path.read_bytes(), render(MODULE.build_reference(self.root))
        )

    def test_rebuild_is_byte_identical_without_any_ledger_write(self):
        first = MODULE.build_reference(self.root)
        second = MODULE.build_reference(self.root)
        self.assertEqual(render(first), render(second))

    # ---- fail closed ------------------------------------------------------

    def test_missing_pointer_over_a_ratified_chain_fails_closed(self):
        self.pointer_path.unlink()
        with self.assertRaisesRegex(
            MODULE.CapitalFlowPostureReferenceError,
            "TRANSITION_LEDGER_POINTER_MISSING",
        ):
            MODULE.build_reference(self.root)

    def test_unreadable_or_unsupported_pointer_fails_closed(self):
        original = self._pointer()
        self.pointer_path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(
            MODULE.CapitalFlowPostureReferenceError,
            "TRANSITION_LEDGER_READ_FAILED",
        ):
            MODULE.build_reference(self.root)
        self._write_pointer(original)
        self._resign_pointer(
            lambda value: value.update(
                {"contract_version": "cross_market_flow_transition_ledger/9"}
            )
        )
        with self.assertRaisesRegex(
            MODULE.CapitalFlowPostureReferenceError,
            "TRANSITION_LEDGER_CONTRACT_VERSION_UNSUPPORTED",
        ):
            MODULE.build_reference(self.root)

    def test_tampered_pointer_fails_closed_even_after_being_re_signed(self):
        cases = {
            "state": lambda value: value["entries"][-1]["current_state"][
                "cross_market_flow"
            ].update({"relative_strength_leader": "CRYPTO"}),
            "persistence": lambda value: value["entries"][-1]["persistence"].update(
                {"state_natural_observation_count_total": 99}
            ),
            "first_seen": lambda value: value["entries"][-1].update(
                {"first_seen": "2020-01-01T00:00:00Z"}
            ),
            "confirmed_at": lambda value: value["entries"][-1].update(
                {"confirmed_at": "2026-09-03T00:00:00Z"}
            ),
            # a REPLAY observation relabelled as counting toward persistence
            "counted_mode_relabelled": lambda value: value["entries"][-1].update(
                {"observation_mode": "REPLAY", "counts_toward_persistence": True}
            ),
        }
        original = self._pointer()
        for name, mutate in cases.items():
            with self.subTest(case=name):
                self._write_pointer(original)
                self._resign_pointer(mutate)
                with self.assertRaisesRegex(
                    MODULE.CapitalFlowPostureReferenceError,
                    "TRANSITION_LEDGER_VALIDATION_FAILED",
                ):
                    MODULE.build_reference(self.root)

    def test_predecessor_or_lineage_drift_fails_closed(self):
        evidence = self.root / self.contract["predecessor"]["evidence_path"]
        cases = {
            "entry_sha_broken": lambda value: value["entries"][-1]["lineage"].update(
                {"input_payload_sha256": "0" * 64}
            ),
            "predecessor_projection_inflated": lambda value: value[
                "predecessor"
            ].update({"counted_natural_observations": 9}),
        }
        original = self._pointer()
        for name, mutate in cases.items():
            with self.subTest(case=name):
                self._write_pointer(original)
                self._resign_pointer(mutate)
                with self.assertRaisesRegex(
                    MODULE.CapitalFlowPostureReferenceError,
                    "TRANSITION_LEDGER_VALIDATION_FAILED",
                ):
                    MODULE.build_reference(self.root)
        self._write_pointer(original)
        evidence.write_bytes(evidence.read_bytes() + b" ")
        with self.assertRaisesRegex(
            MODULE.CapitalFlowPostureReferenceError,
            "TRANSITION_LEDGER_PREDECESSOR_INVALID",
        ):
            MODULE.build_reference(self.root)

    def test_predecessor_pointer_from_another_chain_fails_closed(self):
        """A structurally valid /1 pointer that is not the pinned chain."""
        evidence = self.root / self.contract["predecessor"]["evidence_path"]
        foreign = json.loads(evidence.read_text(encoding="utf-8"))
        foreign["ledger_id"] = "CROSS_MARKET_FLOW_FORK"
        foreign.pop("payload_sha256")
        foreign["payload_sha256"] = LEDGER.payload_sha256(foreign)
        LEDGER.verify_predecessor_ledger(foreign)
        self.assertNotEqual(
            foreign["payload_sha256"], self.predecessor["payload_sha256"]
        )
        self._write_pointer(foreign)
        with self.assertRaisesRegex(
            MODULE.CapitalFlowPostureReferenceError,
            "TRANSITION_LEDGER_PREDECESSOR_IDENTITY_MISMATCH",
        ):
            MODULE.build_reference(self.root)

    def test_transition_ledger_policy_identity_fails_closed(self):
        path = self.root / "config/capital_flow_posture_reference_policy_v1.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["transition_ledger"]["counted_observation_modes"] = [
            "NATURAL", "REPLAY",
        ]
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(
            MODULE.CapitalFlowPostureReferenceError,
            "POLICY_TRANSITION_LEDGER_INVALID",
        ):
            MODULE.build_reference(self.root)

    # ---- authority --------------------------------------------------------

    def test_recorded_history_opens_no_new_authority_and_no_flow_claim(self):
        packet = MODULE.build_reference(self.root)
        candidates = packet["flow_candidates"]
        self.assertEqual(candidates["actual_flow_claim"], "UNKNOWN")
        self.assertEqual(
            candidates["actual_flow_claim_reason"],
            "COMPARABLE_DIRECT_DONOR_RECEIVER_EVIDENCE_NOT_AVAILABLE",
        )
        self.assertIsNone(candidates["confidence"])
        self.assertEqual(
            candidates["confidence_status"], "NOT_COMPUTABLE_POLICY_UNRATIFIED"
        )
        self.assertEqual(
            candidates["invalidation"]["status"], "NOT_COMPUTABLE_POLICY_UNRATIFIED"
        )
        self.assertIsNone(candidates["persistence"]["confirmed_at"])
        self.assertIsNone(candidates["persistence"]["confirmation_threshold"])
        self.assertEqual(
            candidates["persistence"]["confirmation_status"],
            "NOT_COMPUTABLE_POLICY_UNRATIFIED",
        )
        self.assertIsNone(packet["total_exposure_review"]["invested_target_pct"])
        self.assertIsNone(packet["total_exposure_review"]["cash_target_pct"])
        for row in packet["market_allocation_reviews"]:
            self.assertIsNone(row["target_weight_pct"])
        for key, value in packet["authority"].items():
            if key in {
                "paper_reference_display_authorized",
                "relative_strength_comparison_authorized",
            }:
                self.assertIs(value, True, key)
            else:
                self.assertIs(value, False, key)


if __name__ == "__main__":
    unittest.main()
