#!/usr/bin/env python3
from __future__ import annotations

import copy
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
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
        self.policy = MODULE.validate_policy(
            MODULE.read_json(
                self.root / "config/capital_flow_posture_reference_policy_v1.json",
                "POLICY_INVALID",
            )
        )
        paper = MODULE.PAPER_REGIME.build_reference(self.root)
        MODULE.PAPER_REGIME.write_packet(paper, self.root)

    def tearDown(self):
        self._temp.cleanup()

    # ---- fixture plumbing -------------------------------------------------

    def _pointer(self) -> dict:
        return json.loads(self.pointer_path.read_text(encoding="utf-8"))

    def _write_pointer(self, value: dict) -> None:
        self.pointer_path.write_bytes(render(value))

    def _consumable(self, packet: dict) -> tuple:
        """The prior history this packet may consume, and its head.

        This reuses the producer's own ``_consumable_history`` instead of
        writing a second copy of the ledger's self-observation rule.  The tests
        then assert the *property* that rule must have -- no consumed entry may
        be an observation of this packet -- so reuse cannot hide a broken rule.
        """
        recorded = MODULE._consumable_history(
            LEDGER,
            self.policy["transition_ledger"],
            self.contract,
            self._pointer(),
            self.predecessor,
            packet["generated_at"],
            self.root,
            self.contract_path,
        )
        head = recorded[-1] if recorded else self.predecessor["tail"]
        return recorded, head

    def _assert_self_observation_excluded(self, packet: dict) -> tuple:
        """No entry recording this packet may appear in its own prior history."""
        entries = self._pointer()["entries"]
        recorded, head = self._consumable(packet)
        excluded = [
            item for item in entries
            if item["observed_at"] == packet["generated_at"]
        ]
        self.assertEqual(
            [item for item in entries if item not in excluded], recorded
        )
        for item in recorded:
            self.assertNotEqual(item["observed_at"], packet["generated_at"])
        self.assertNotEqual(head["observed_at"], packet["generated_at"])
        for item in excluded:
            self.assertNotEqual(head["entry_sha256"], item["entry_sha256"])
        return recorded, head

    def _ledger_latest_date(self) -> str:
        entries = self._pointer()["entries"]
        if entries:
            return entries[-1]["source_generated_date_kst"]
        return self.predecessor["tail"]["source_generated_date_kst"]

    def _advance_past_ledger(self) -> dict:
        """Move the fixture clock to a test-only instant after the whole chain.

        The canonical chain grows on its own, so no fixture may assume a
        particular calendar day is still free.  The clock is stepped until the
        producer's own ``source_generated_date_kst`` is strictly forward of the
        chain's latest entry -- never a hard-coded date.
        """
        for _ in range(60):
            packet = MODULE.build_reference(self.root)
            if (
                LEDGER.source_generated_date_kst(packet)
                > self._ledger_latest_date()
            ):
                return packet
            self._advance_source_clock()
        self.fail("fixture clock could not advance past the canonical ledger")

    def _append(self, mode: str) -> tuple:
        """Append one forward observation exactly as P2-COM-03 does."""
        packet = self._advance_past_ledger()
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
        return packet, result["ledger"]

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
        recorded, head = self._assert_self_observation_excluded(packet)
        self.assertTrue(recorded, "fixture must retain consumable prior history")
        transition = packet["flow_candidates"]["transition"]
        persistence = packet["flow_candidates"]["persistence"]

        self.assertEqual(transition["status"], "RECORDED_HISTORY_OBSERVED")
        self.assertEqual(transition["evidence_status"], "LEDGER_CONSUMED")
        self.assertEqual(transition["recorded_type"], recorded[-1]["transition"]["type"])
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
        recorded, head = self._assert_self_observation_excluded(packet)
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
        self.assertEqual(source["consumed_head_entry_sha256"], head["entry_sha256"])
        self.assertEqual(source["consumed_head_observed_at"], head["observed_at"])
        self.assertEqual(
            source["consumed_head_ledger_revision"],
            recorded[-1]["ledger_revision"] if recorded
            else self.predecessor["height"],
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
        entries, _ = self._assert_self_observation_excluded(packet)
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
        before = self._advance_past_ledger()["flow_candidates"]["persistence"]
        for mode in ("MANUAL", "REPLAY"):
            with self.subTest(mode=mode):
                appended, ledger = self._append(mode)
                head = ledger["entries"][-1]
                self.assertEqual(head["observation_mode"], mode)
                self.assertFalse(head["counts_toward_persistence"])
                # step past the new entry so it becomes prior history rather
                # than this packet's own excluded self observation
                after = self._advance_past_ledger()
                recorded, _ = self._assert_self_observation_excluded(after)
                self.assertIn(
                    head["entry_sha256"],
                    [item["entry_sha256"] for item in recorded],
                    "the non-NATURAL entry must be visible as prior history",
                )
                persistence = after["flow_candidates"]["persistence"]
                self.assertEqual(
                    after["flow_candidates"]["transition"][
                        "current_semantic_state_sha256"
                    ],
                    appended["flow_candidates"]["transition"][
                        "current_semantic_state_sha256"
                    ],
                    "fixture must hold the semantic state while the clock moves",
                )
                # nothing natural may grow from a non-NATURAL observation.
                # The natural streak is preserved by the ledger rather than
                # reset, so the invariant is "must not increase", not "is zero"
                self.assertEqual(
                    persistence["natural_observation_count"],
                    before["natural_observation_count"],
                    "a non-NATURAL observation was promoted to natural evidence",
                )
                self.assertEqual(
                    persistence["current_streak_natural_count"],
                    before["current_streak_natural_count"],
                    "a non-NATURAL observation extended the natural streak",
                )
                # but the observation itself must still be recorded as seen
                self.assertGreater(
                    persistence["observation_count"], before["observation_count"]
                )
                self.assertGreater(
                    persistence["current_streak_observation_count"],
                    before["current_streak_observation_count"],
                )
                before = persistence

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
        packet, ledger = self._append("NATURAL")
        pending = packet["flow_candidates"]["transition"]["pending_type"]
        before = packet["flow_candidates"]["persistence"]
        head = ledger["entries"][-1]
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
        # the recorded entry is on the chain, yet is excluded from the rebuild
        self.assertIn(
            head["entry_sha256"],
            [item["entry_sha256"] for item in self._pointer()["entries"]],
        )
        self._assert_self_observation_excluded(packet)
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


# ---------------------------------------------------------------------------
# Frozen Flow replay inputs -- proved against a REAL Git repository
# ---------------------------------------------------------------------------
#
# Every test below builds an actual repository with `git init` and real
# commits, then exercises the production capture/verify/materialize path
# against those objects. Nothing about provenance is mocked: the tree lookups,
# blob ids, raw bytes and ancestry checks are the real ones, so a counterexample
# here is a counterexample against the shipped code rather than against a stub.
#
# The fixture repository is built from THIS repository's own ten committed
# closure inputs, so the positive replay below is a real production closure
# rather than a synthetic one. It is a fixture repository all the same, and
# nothing here claims its synthetic commit is any packet's original issuing
# commit.
# ---------------------------------------------------------------------------


_GIT_ENV = {
    **os.environ,
    # Isolate from developer/CI global config: a global commit.gpgsign, hook
    # path or template would otherwise leak into the fixture.
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "Atlas Flow Replay Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@atlas.invalid",
    "GIT_COMMITTER_NAME": "Atlas Flow Replay Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@atlas.invalid",
    "GIT_AUTHOR_DATE": "2026-09-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-09-01T00:00:00+00:00",
}


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c", "user.name=Atlas Flow Replay Fixture",
            "-c", "user.email=fixture@atlas.invalid",
            "-c", "commit.gpgsign=false",
            "-c", "core.hooksPath=/dev/null",
            *args,
        ],
        cwd=cwd, env=_GIT_ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed in {cwd}: "
            f"{result.stderr.decode('utf-8', 'replace')}"
        )
    return result.stdout.decode("utf-8")


class FlowFrozenReplayGitProvenanceTests(unittest.TestCase):
    """The ten Flow inputs are frozen as Git identities and replayed from them.

    The property under test is that the Flow section of an archived briefing
    stops depending on when it is validated: after capture, the market
    pointers, the P2-COM-03 ledger and HEAD itself may all move and the same
    envelope must still rebuild the same packet.
    """

    PATHS = MODULE.FLOW_REPLAY_INPUT_PATHS
    REQUIRED = MODULE.FLOW_REPLAY_REQUIRED_INPUT_PATHS
    CONTRACT = MODULE.TRANSITION_LEDGER_CONTRACT_REL
    PREDECESSOR = MODULE.TRANSITION_LEDGER_PREDECESSOR_REL
    POINTER = MODULE.TRANSITION_LEDGER_POINTER_REL
    CROSS_ASSET = MODULE.CROSS_ASSET_CONTRACT_REL

    # -- fixture -----------------------------------------------------------

    def make_repo(self, omit=(), mutate=None) -> tuple[Path, str]:
        """A real Git repository holding the ten closure inputs, committed.

        ``omit`` drops paths entirely (so the commit tree genuinely does not
        contain them -- a real absence, not a claimed one); ``mutate`` may
        rewrite the working tree before the commit.
        """
        root = Path(tempfile.mkdtemp(prefix="flow-replay-repo-")).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for relative in self.PATHS:
            if relative in omit:
                continue
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        if mutate is not None:
            mutate(root)
        git(root, "init", "--quiet")
        git(root, "add", "-A")
        git(root, "commit", "--quiet", "-m", "flow replay closure")
        return root, git(root, "rev-parse", "HEAD").strip()

    def capture(self, root: Path) -> dict:
        return MODULE.capture_flow_replay_inputs(root)

    def replay(self, envelope, root: Path) -> dict:
        return MODULE.build_reference_from_frozen_inputs(
            envelope, trusted_repository_root=root
        )

    @staticmethod
    def _rewrite_json(path: Path, mutate) -> None:
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    # -- the envelope ------------------------------------------------------

    def test_capture_freezes_exactly_ten_real_object_identities_no_content(self):
        root, head = self.make_repo()
        envelope = self.capture(root)

        self.assertEqual(
            set(envelope), {"schema_version", "source_commit", "files"}
        )
        self.assertEqual(envelope["schema_version"], "capital_flow_replay_inputs/1")
        self.assertEqual(envelope["source_commit"], head)
        self.assertEqual(set(envelope["files"]), set(self.PATHS))
        self.assertEqual(len(self.PATHS), 10)

        for relative, entry in envelope["files"].items():
            with self.subTest(path=relative):
                self.assertEqual(set(entry), {"state", "blob_oid", "sha256"})
                self.assertEqual(entry["state"], "PRESENT")
                # The oid is the repository's real one for that path...
                actual = git(
                    root, "rev-parse", f"{head}:{relative}"
                ).strip()
                self.assertEqual(entry["blob_oid"], actual)
                # ...and the digest is over the real committed bytes.
                self.assertEqual(
                    entry["sha256"],
                    hashlib.sha256((root / relative).read_bytes()).hexdigest(),
                )
        # Digests and a commit, never content: an envelope cannot hand the
        # validator the bytes it wants validated.
        self.assertNotIn("content_base64", json.dumps(envelope))

    def test_envelope_cannot_name_an_extra_missing_or_foreign_path(self):
        root, _head = self.make_repo()
        envelope = self.capture(root)

        for label, mutate in (
            ("extra", lambda e: e["files"].__setitem__(
                "config/somewhere_else.json",
                {"state": "ABSENT", "blob_oid": None, "sha256": None})),
            ("missing", lambda e: e["files"].pop(self.POINTER)),
            ("absolute", lambda e: e["files"].__setitem__(
                "/etc/passwd",
                {"state": "ABSENT", "blob_oid": None, "sha256": None})),
            ("traversal", lambda e: e["files"].__setitem__(
                "../outside.json",
                {"state": "ABSENT", "blob_oid": None, "sha256": None})),
        ):
            with self.subTest(case=label):
                broken = copy.deepcopy(envelope)
                mutate(broken)
                with self.assertRaisesRegex(
                    MODULE.FlowReplayProvenanceError,
                    "FLOW_REPLAY_FILE_KEYS_MISMATCH",
                ):
                    self.replay(broken, root)

        for label, mutate in (
            ("repository", lambda e: e.__setitem__("repository", "https://x/y")),
            ("ref", lambda e: e.__setitem__("ref", "refs/heads/main")),
            ("validation_head", lambda e: e.__setitem__("head", "a" * 40)),
            ("dropped_commit", lambda e: e.pop("source_commit")),
        ):
            with self.subTest(case=label):
                broken = copy.deepcopy(envelope)
                mutate(broken)
                with self.assertRaisesRegex(
                    MODULE.FlowReplayProvenanceError,
                    "FLOW_REPLAY_ENVELOPE_FIELDS_MISMATCH",
                ):
                    self.replay(broken, root)

    def test_malformed_envelope_forms_fail_closed(self):
        root, _head = self.make_repo()
        envelope = self.capture(root)
        for label, value, code in (
            ("none", None, "FLOW_REPLAY_ENVELOPE_INVALID"),
            ("list", [], "FLOW_REPLAY_ENVELOPE_INVALID"),
            ("string", "x", "FLOW_REPLAY_ENVELOPE_INVALID"),
        ):
            with self.subTest(case=label):
                with self.assertRaisesRegex(MODULE.FlowReplayProvenanceError, code):
                    self.replay(value, root)

        for label, mutate, code in (
            ("schema", lambda e: e.__setitem__("schema_version", "other/1"),
             "FLOW_REPLAY_SCHEMA_VERSION_INVALID"),
            ("abbreviated_commit",
             lambda e: e.__setitem__("source_commit", e["source_commit"][:8]),
             "FLOW_REPLAY_SOURCE_COMMIT_INVALID"),
            ("uppercase_commit",
             lambda e: e.__setitem__("source_commit", e["source_commit"].upper()),
             "FLOW_REPLAY_SOURCE_COMMIT_INVALID"),
            ("null_commit", lambda e: e.__setitem__("source_commit", None),
             "FLOW_REPLAY_SOURCE_COMMIT_INVALID"),
            ("files_list", lambda e: e.__setitem__("files", []),
             "FLOW_REPLAY_FILE_KEYS_MISMATCH"),
            ("entry_extra_key",
             lambda e: e["files"][self.POINTER].__setitem__("content_base64", "AA=="),
             "FLOW_REPLAY_FILE_FIELDS_MISMATCH"),
            ("entry_state",
             lambda e: e["files"][self.POINTER].__setitem__("state", "MAYBE"),
             "FLOW_REPLAY_STATE_INVALID"),
        ):
            with self.subTest(case=label):
                broken = copy.deepcopy(envelope)
                mutate(broken)
                with self.assertRaisesRegex(MODULE.FlowReplayProvenanceError, code):
                    self.replay(broken, root)

    # -- the positive replay ------------------------------------------------

    def test_frozen_replay_reproduces_the_producer_exactly(self):
        root, _head = self.make_repo()
        envelope = self.capture(root)
        direct = MODULE.build_reference(root)
        replayed = self.replay(envelope, root)
        self.assertEqual(replayed, direct)
        # A real packet, not an empty shell, and the producer's own semantic
        # hashing and authority are untouched by the replay path.
        self.assertEqual(replayed["schema_version"], MODULE.SCHEMA_VERSION)
        unsigned = copy.deepcopy(replayed)
        claimed = unsigned.pop("payload_sha256")
        self.assertEqual(MODULE.payload_sha256(unsigned), claimed)
        self.assertIs(replayed["authority"]["trading_authorized"], False)
        self.assertIs(replayed["authority"]["production_authorized"], False)
        self.assertEqual(replayed["cross_market_flow"]["actual_money_flow"], "UNKNOWN")

    def test_replay_is_invariant_to_later_input_and_head_movement(self):
        """The whole point: an archived Flow section stops moving under us."""
        root, head = self.make_repo()
        envelope = self.capture(root)
        before = self.replay(envelope, root)

        # Move every mutable thing the live path used to re-read: the three
        # market pointers, the ledger pointer, and HEAD itself. A trailing
        # newline keeps each file valid JSON while giving it a new blob id and
        # a new SHA256 -- the smallest change that is genuinely a new object.
        for relative in (
            MODULE.FREE_MARKET_DATA_REL,
            MODULE.KOREA_MARKET_SIGNALS_REL,
            MODULE.CRYPTO_REFRESH_STATUS_REL,
            self.POINTER,
        ):
            path = root / relative
            path.write_bytes(path.read_bytes() + b"\n")
        git(root, "add", "-A")
        git(root, "commit", "--quiet", "-m", "inputs move after capture")
        moved_head = git(root, "rev-parse", "HEAD").strip()
        self.assertNotEqual(moved_head, head)

        after = self.replay(envelope, root)
        self.assertEqual(after, before)
        self.assertEqual(envelope["source_commit"], head)
        # A live build from the same tree now genuinely differs, so the
        # equality above is invariance rather than a vacuous no-op.
        with self.assertRaises(MODULE.CapitalFlowPostureReferenceError):
            MODULE.build_reference(root)

    def test_historical_closure_is_read_from_the_commit_tree_not_a_claim(self):
        root, head = self.make_repo()
        expected = self.replay(self.capture(root), root)
        # Move on, then replay the ORIGINAL commit purely from external
        # operator context: a commit id, and nothing else.
        pointer = root / self.POINTER
        pointer.write_bytes(pointer.read_bytes() + b"\n")
        git(root, "add", "-A")
        git(root, "commit", "--quiet", "-m", "later")
        rebuilt = MODULE.build_reference_from_source_commit(
            head, trusted_repository_root=root
        )
        self.assertEqual(rebuilt, expected)

        closure = MODULE.flow_replay_inputs_at_commit(head, trusted_repository_root=root)
        self.assertEqual(closure["source_commit"], head)
        self.assertEqual(set(closure["files"]), set(self.PATHS))

    # -- Git counterexamples ------------------------------------------------

    def test_historical_context_requires_the_exact_lowercase_commit_hash(self):
        root, head = self.make_repo()
        git(root, "branch", "historical-source", head)
        git(root, "tag", "historical-tag", head)
        git(root, "tag", "-a", "annotated-source", "-m", "not a commit object", head)
        tag_oid = git(root, "rev-parse", "annotated-source").strip()
        aliases = ["HEAD", "historical-source", "historical-tag", head[:12],
                   head.upper(), head + "^{commit}", head + "~0", head + "\n", tag_oid, None, 42]
        for source in aliases:
            with self.subTest(source=source), self.assertRaisesRegex(
                MODULE.FlowReplayProvenanceError, "FLOW_REPLAY_SOURCE_COMMIT_INVALID"
            ):
                MODULE.flow_replay_inputs_at_commit(source, trusted_repository_root=root)
        self.assertEqual(
            MODULE.flow_replay_inputs_at_commit(head, trusted_repository_root=root),
            self.capture(root),
        )
        forged = self.capture(root)
        forged["source_commit"] = tag_oid
        with self.assertRaisesRegex(MODULE.FlowReplayProvenanceError,
                                    "FLOW_REPLAY_SOURCE_COMMIT_INVALID"):
            MODULE.verify_flow_replay_inputs(forged, trusted_repository_root=root)

    def test_replacement_commit_cannot_hide_real_tree_or_present_path(self):
        root, head = self.make_repo()
        original = self.capture(root)
        git(root, "rm", "--quiet", self.POINTER)
        replacement = git(root, "commit-tree", git(root, "write-tree").strip(),
                          "-m", "replacement without pointer").strip()
        git(root, "replace", head, replacement)
        self.assertEqual(git(root, "ls-tree", head, "--", self.POINTER), "")
        self.assertEqual(MODULE.flow_replay_inputs_at_commit(
            head, trusted_repository_root=root), original)
        forged = copy.deepcopy(original)
        forged["files"][self.POINTER] = {"state": "ABSENT", "blob_oid": None, "sha256": None}
        with self.assertRaisesRegex(MODULE.FlowReplayProvenanceError,
                                    "FLOW_REPLAY_ABSENT_HIDES_COMMITTED_ENTRY"):
            MODULE.verify_flow_replay_inputs(forged, trusted_repository_root=root)

    def test_replacement_commit_cannot_invent_an_absent_path(self):
        root, head = self.make_repo(omit=(self.POINTER,))
        original = self.capture(root)
        target = root / self.POINTER
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / self.POINTER, target)
        git(root, "add", self.POINTER)
        oid = git(root, "rev-parse", ":" + self.POINTER).strip()
        replacement = git(root, "commit-tree", git(root, "write-tree").strip(),
                          "-m", "replacement invents pointer").strip()
        git(root, "replace", head, replacement)
        self.assertIn(oid, git(root, "ls-tree", head, "--", self.POINTER))
        self.assertEqual(MODULE.flow_replay_inputs_at_commit(
            head, trusted_repository_root=root), original)
        forged = copy.deepcopy(original)
        forged["files"][self.POINTER] = {
            "state": "PRESENT", "blob_oid": oid,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
        with self.assertRaisesRegex(MODULE.FlowReplayProvenanceError,
                                    "FLOW_REPLAY_PRESENT_NOT_IN_COMMIT_TREE"):
            MODULE.verify_flow_replay_inputs(forged, trusted_repository_root=root)

    def test_replacement_commit_cannot_forge_trusted_ancestry(self):
        root, head = self.make_repo()
        envelope = self.capture(root)
        tree = git(root, "rev-parse", head + "^{tree}").strip()
        unrelated = git(root, "commit-tree", tree, "-m", "unrelated root").strip()
        replacement = git(root, "commit-tree", tree, "-p", unrelated,
                          "-m", "forged ancestry").strip()
        git(root, "replace", head, replacement)
        # Ordinary Git now accepts this false ancestry; provenance Git must not.
        git(root, "merge-base", "--is-ancestor", unrelated, head)
        forged = copy.deepcopy(envelope)
        forged["source_commit"] = unrelated
        with self.assertRaisesRegex(MODULE.FlowReplayProvenanceError,
                                    "FLOW_REPLAY_SOURCE_COMMIT_NOT_TRUSTED_ANCESTOR"):
            MODULE.verify_flow_replay_inputs(forged, trusted_repository_root=root)
        MODULE.verify_flow_replay_inputs(envelope, trusted_repository_root=root)

    def test_replacement_blob_cannot_change_authenticated_original_bytes(self):
        root, head = self.make_repo()
        envelope = self.capture(root)
        relative = self.POINTER
        original_bytes = (root / relative).read_bytes()
        original_oid = envelope["files"][relative]["blob_oid"]
        (root / relative).write_bytes(original_bytes + b"\n")
        replacement_oid = git(root, "hash-object", "-w", relative).strip()
        git(root, "replace", original_oid, replacement_oid)
        self.assertNotEqual(git(root, "cat-file", "blob", original_oid).encode(), original_bytes)
        checked = MODULE.verify_flow_replay_inputs(envelope, trusted_repository_root=root)
        self.assertEqual(checked[relative], original_bytes)
        self.assertEqual(MODULE.flow_replay_inputs_at_commit(
            head, trusted_repository_root=root), envelope)

    def test_an_absent_claim_cannot_hide_a_committed_entry(self):
        root, _head = self.make_repo()
        envelope = self.capture(root)
        broken = copy.deepcopy(envelope)
        broken["files"][self.POINTER] = {
            "state": "ABSENT", "blob_oid": None, "sha256": None,
        }
        with self.assertRaisesRegex(
            MODULE.FlowReplayProvenanceError,
            "FLOW_REPLAY_ABSENT_HIDES_COMMITTED_ENTRY",
        ):
            self.replay(broken, root)

    def test_a_present_claim_cannot_invent_an_uncommitted_path(self):
        root, _head = self.make_repo(omit=(self.POINTER,))
        envelope = self.capture(root)
        self.assertEqual(envelope["files"][self.POINTER]["state"], "ABSENT")
        broken = copy.deepcopy(envelope)
        broken["files"][self.POINTER] = {
            "state": "PRESENT",
            "blob_oid": "0" * 40,
            "sha256": "0" * 64,
        }
        with self.assertRaisesRegex(
            MODULE.FlowReplayProvenanceError,
            "FLOW_REPLAY_PRESENT_NOT_IN_COMMIT_TREE",
        ):
            self.replay(broken, root)

    def test_blob_oid_and_sha256_tamper_fail_closed(self):
        root, _head = self.make_repo()
        envelope = self.capture(root)
        other = envelope["files"][MODULE.FLOW_POLICY_REL]["blob_oid"]

        broken = copy.deepcopy(envelope)
        broken["files"][self.POINTER]["blob_oid"] = other
        with self.assertRaisesRegex(
            MODULE.FlowReplayProvenanceError, "FLOW_REPLAY_BLOB_OID_MISMATCH"
        ):
            self.replay(broken, root)

        broken = copy.deepcopy(envelope)
        broken["files"][self.POINTER]["sha256"] = "f" * 64
        with self.assertRaisesRegex(
            MODULE.FlowReplayProvenanceError, "FLOW_REPLAY_SHA256_MISMATCH"
        ):
            self.replay(broken, root)

        broken = copy.deepcopy(envelope)
        broken["files"][self.POINTER]["blob_oid"] = "not-an-oid"
        with self.assertRaisesRegex(
            MODULE.FlowReplayProvenanceError, "FLOW_REPLAY_BLOB_OID_INVALID"
        ):
            self.replay(broken, root)

    def test_whitespace_only_retamper_of_a_committed_input_is_a_different_object(self):
        """A re-signed blob is not the frozen one, even if it 'means' the same."""
        root, _head = self.make_repo()
        envelope = self.capture(root)
        original = envelope["files"][self.POINTER]["blob_oid"]
        path = root / self.POINTER
        path.write_bytes(path.read_bytes() + b"\n")
        git(root, "add", "-A")
        git(root, "commit", "--quiet", "-m", "whitespace")
        retampered = self.capture(root)
        self.assertNotEqual(
            retampered["files"][self.POINTER]["blob_oid"], original
        )
        # The ORIGINAL envelope still resolves to the original object, so the
        # replay is unaffected by the newer commit.
        self.assertEqual(
            MODULE.verify_flow_replay_inputs(
                envelope, trusted_repository_root=root
            )[self.POINTER],
            MODULE._git(root, "cat-file", "blob", original, binary=True),
        )

    def test_symlink_and_tree_entries_are_refused_not_dereferenced(self):
        def link(root: Path) -> None:
            target = root / self.POINTER
            target.unlink()
            target.symlink_to(Path("..") / MODULE.FREE_MARKET_DATA_REL)

        root, _head = self.make_repo(mutate=link)
        mode = git(root, "ls-tree", "HEAD", "--", self.POINTER).split()[0]
        self.assertEqual(mode, "120000")
        with self.assertRaisesRegex(
            MODULE.FlowReplayProvenanceError, "FLOW_REPLAY_BLOB_MODE_INVALID"
        ):
            self.capture(root)

    def test_source_commit_must_exist_and_be_a_trusted_ancestor(self):
        root, _head = self.make_repo()
        envelope = self.capture(root)

        broken = copy.deepcopy(envelope)
        broken["source_commit"] = "0" * 40
        with self.assertRaisesRegex(
            MODULE.FlowReplayProvenanceError,
            "FLOW_REPLAY_SOURCE_COMMIT_OBJECT_MISSING",
        ):
            self.replay(broken, root)

        # A real commit in the same repository that is NOT an ancestor of the
        # trusted validation HEAD.
        git(root, "checkout", "--quiet", "-b", "sidetrack")
        (root / "unrelated.txt").write_text("sidetrack\n", encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "--quiet", "-m", "sidetrack")
        sidetrack = git(root, "rev-parse", "HEAD").strip()
        git(root, "checkout", "--quiet", "-")
        broken = copy.deepcopy(envelope)
        broken["source_commit"] = sidetrack
        with self.assertRaisesRegex(
            MODULE.FlowReplayProvenanceError,
            "FLOW_REPLAY_SOURCE_COMMIT_NOT_TRUSTED_ANCESTOR",
        ):
            self.replay(broken, root)

    def test_a_non_repository_trusted_root_is_refused_and_never_fetched(self):
        root, _head = self.make_repo()
        envelope = self.capture(root)
        outside = Path(tempfile.mkdtemp(prefix="flow-replay-not-a-repo-")).resolve()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        with self.assertRaises(MODULE.FlowReplayProvenanceError):
            self.replay(envelope, outside)

    def test_a_trusted_root_supplied_by_the_envelope_is_impossible(self):
        """The envelope has no field that could name a root; assert the shape."""
        root, _head = self.make_repo()
        envelope = self.capture(root)
        self.assertEqual(
            set(envelope), {"schema_version", "source_commit", "files"}
        )
        for entry in envelope["files"].values():
            self.assertEqual(set(entry), {"state", "blob_oid", "sha256"})

    # -- closure semantics ---------------------------------------------------

    def test_a_genuinely_absent_required_input_is_unreplayable_not_empty(self):
        for relative in self.REQUIRED:
            with self.subTest(path=relative):
                root, _head = self.make_repo(omit=(relative,))
                envelope = self.capture(root)
                self.assertEqual(envelope["files"][relative]["state"], "ABSENT")
                with self.assertRaisesRegex(
                    MODULE.UnreplayableFlowHistoryError,
                    f"FLOW_REPLAY_REQUIRED_INPUT_ABSENT:{re.escape(relative)}",
                ):
                    self.replay(envelope, root)

    def test_c5_uses_the_frozen_cross_asset_contract_with_no_root_fallback(self):
        """The producer's `root != ROOT` fixture fallback must be unreachable.

        The real repository ROOT holds a valid P2-COM-01 contract, so a replay
        that fell back to it would quietly succeed here. It must not: the
        frozen bytes are the only ones that count.
        """
        def break_identity(root: Path) -> None:
            self._rewrite_json(
                root / self.CROSS_ASSET,
                lambda value: value.__setitem__(
                    "contract_version", "cross_asset_flow_evidence/999"
                ),
            )

        root, _head = self.make_repo(mutate=break_identity)
        envelope = self.capture(root)
        self.assertTrue((ROOT / self.CROSS_ASSET).is_file())
        with self.assertRaisesRegex(
            MODULE.CapitalFlowPostureReferenceError, "CROSS_ASSET_FLOW_CONTRACT_"
        ):
            self.replay(envelope, root)

        # And a genuinely absent one is a hard closure failure, not a fallback.
        root, _head = self.make_repo(omit=(self.CROSS_ASSET,))
        with self.assertRaisesRegex(
            MODULE.UnreplayableFlowHistoryError,
            "FLOW_REPLAY_REQUIRED_INPUT_ABSENT",
        ):
            self.replay(self.capture(root), root)

    def test_c4_revalidates_the_production_predecessor_in_the_isolated_root(self):
        """`load_contract(temp_path)` would validate in NON-production mode.

        That is precisely the check that must not be skipped: without the
        explicit `production=True` call, a contract re-pointed at a different
        predecessor would be accepted inside a temporary root and the replay
        would silently continue on a foreign chain.
        """
        foreign = dict(LEDGER.PRODUCTION_PREDECESSOR)
        foreign["payload_sha256"] = "a" * 64

        def repoint(root: Path) -> None:
            self._rewrite_json(
                root / self.CONTRACT,
                lambda value: value.__setitem__("predecessor", foreign),
            )

        root, _head = self.make_repo(mutate=repoint)
        envelope = self.capture(root)
        # Non-production validation really would accept it -- so the guard is
        # load-bearing, not decorative.
        LEDGER.validate_contract(
            json.loads((root / self.CONTRACT).read_text(encoding="utf-8")),
            production=False,
        )
        with self.assertRaisesRegex(
            MODULE.CapitalFlowPostureReferenceError,
            "CONTRACT_PRODUCTION_PREDECESSOR_MISMATCH",
        ):
            self.replay(envelope, root)

    def test_c4_revalidates_the_real_predecessor_file_from_the_frozen_bytes(self):
        def corrupt(root: Path) -> None:
            self._rewrite_json(
                root / self.PREDECESSOR,
                lambda value: value.__setitem__("ledger_revision", 999),
            )

        root, _head = self.make_repo(mutate=corrupt)
        with self.assertRaisesRegex(
            MODULE.CapitalFlowPostureReferenceError,
            "TRANSITION_LEDGER_PREDECESSOR_INVALID",
        ):
            self.replay(self.capture(root), root)

    def test_optional_transition_ledger_combinations(self):
        # 8/9/10 all absent -- the honest "no consumable history" record.
        root, _head = self.make_repo(
            omit=(self.CONTRACT, self.PREDECESSOR, self.POINTER)
        )
        packet = self.replay(self.capture(root), root)
        ledger_source = packet["sources"][2]
        self.assertEqual(ledger_source["chain_status"], MODULE.NO_PRIOR_HISTORY)
        self.assertIsNone(ledger_source["contract_sha256"])

        # 8 absent, 9+10 present: still authenticated, never consumed.
        root, _head = self.make_repo(omit=(self.CONTRACT,))
        envelope = self.capture(root)
        self.assertEqual(envelope["files"][self.POINTER]["state"], "PRESENT")
        self.assertEqual(envelope["files"][self.PREDECESSOR]["state"], "PRESENT")
        unconsumed = self.replay(envelope, root)
        self.assertEqual(
            unconsumed["sources"][2]["chain_status"], MODULE.NO_PRIOR_HISTORY
        )

        # 8+9 present, 10 absent -- a ratified chain whose pointer is gone.
        root, _head = self.make_repo(omit=(self.POINTER,))
        with self.assertRaisesRegex(
            MODULE.UnreplayableFlowHistoryError, "TRANSITION_LEDGER_POINTER_MISSING"
        ):
            self.replay(self.capture(root), root)

        # 8+10 present, 9 absent -- the pinned predecessor is required.
        root, _head = self.make_repo(omit=(self.PREDECESSOR,))
        with self.assertRaisesRegex(
            MODULE.UnreplayableFlowHistoryError,
            "TRANSITION_LEDGER_PREDECESSOR_REQUIRED",
        ):
            self.replay(self.capture(root), root)

    def test_semantic_violation_never_becomes_a_normal_empty_state(self):
        def truncate(root: Path) -> None:
            (root / self.POINTER).write_text("{ not json", encoding="utf-8")

        root, _head = self.make_repo(mutate=truncate)
        with self.assertRaises(MODULE.CapitalFlowPostureReferenceError) as caught:
            self.replay(self.capture(root), root)
        self.assertIn("TRANSITION_LEDGER", str(caught.exception))

    # -- isolation -----------------------------------------------------------

    def test_materialization_is_isolated_exact_and_removed(self):
        root, _head = self.make_repo(omit=(self.POINTER,))
        verified = MODULE.verify_flow_replay_inputs(
            self.capture(root), trusted_repository_root=root
        )
        seen = None
        with MODULE.materialized_flow_replay_root(verified) as temporary:
            seen = temporary
            self.assertNotEqual(temporary.resolve(), ROOT)
            self.assertNotEqual(temporary.resolve(), root)
            for relative, data in verified.items():
                path = temporary / relative
                if data is None:
                    # A proven-absent path is not created, so the producer
                    # sees the tree shape the source commit actually had.
                    self.assertFalse(path.exists(), relative)
                    continue
                self.assertEqual(path.read_bytes(), data, relative)
        self.assertIsNotNone(seen)
        self.assertFalse(seen.exists())

    def test_replay_never_writes_into_the_real_repository(self):
        root, _head = self.make_repo()
        before = {
            relative: (ROOT / relative).read_bytes() for relative in self.PATHS
        }
        self.replay(self.capture(root), root)
        for relative, data in before.items():
            self.assertEqual((ROOT / relative).read_bytes(), data, relative)
        self.assertEqual(git(root, "status", "--porcelain").strip(), "")


if __name__ == "__main__":
    unittest.main()
