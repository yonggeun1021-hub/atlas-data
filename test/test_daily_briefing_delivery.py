#!/usr/bin/env python3
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/daily_briefing_delivery.py"
SPEC = importlib.util.spec_from_file_location("daily_briefing_delivery", SCRIPT)
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)

# The SAME orchestrator module object the delivery script imported its
# validate_packet from -- importing the script above already put it in
# sys.modules, and it inserted the repository root on sys.path to do so. Used
# only to BUILD real fixtures; the delivery path under test still reaches
# validation through its own import, unmocked.
import briefing.daily_orchestrator as orchestrator  # noqa: E402


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def flow_ready_basis():
    """(decision_date, generated_at) that let the REAL Flow inputs on disk
    produce a POPULATED P2_FLOW_ENGINE row, resolved from the producer's own
    output rather than hardcoded.

    The Flow row is labelled with the Flow reference's own generated_at, which
    capital_flow_posture_reference.build_reference() takes straight from
    data/latest_paper_regime_reference.json. Against a hardcoded past date that
    evidence is legitimately from the future, and the orchestrator's temporal
    boundary correctly rebuilds the row as DATA_BLOCKED with a null packet --
    which cannot demonstrate a real positive replay. So the basis is resolved
    honestly instead: decide for the day AFTER the Flow evidence, and generate
    at the end of that KST day, which is genuinely after it. Neither temporal
    check is loosened; see test_daily_orchestrator._flow_ready_decision_date_
    and_generated_at() for the same reasoning on the orchestrator's own axis.
    """
    reference = orchestrator.CAPITAL_FLOW_ENGINE.build_reference()
    flow_dt = dt.datetime.fromisoformat(
        reference["generated_at"].replace("Z", "+00:00")
    )
    decision_date = flow_dt.date() + dt.timedelta(days=1)
    generated_at = (
        dt.datetime.fromisoformat(f"{decision_date.isoformat()}T23:59:59")
        .replace(tzinfo=orchestrator.KST)
        .astimezone(dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return decision_date.isoformat(), generated_at


class DailyBriefingDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.slot = "morning"
        self.date = "2026-08-25"
        self.date_root = self.root / "evidence/daily_briefing/morning/2026-08-25"
        packet = {
            "slot": self.slot,
            "decision_date": self.date,
            "packet_sha256": "packet-digest",
            "components": [
                {
                    "component_id": "INVESTMENT_DECISION_REVIEW",
                    "status": "DATA_BLOCKED",
                    "reason": "P8-09_EXPECTATIONS_GAP_UNKNOWN",
                    "packet": {
                        "review_outcome": "BLOCKED",
                        "trade_proposal": None,
                        "money_action": "NONE",
                        "capital": 0,
                    },
                },
                {
                    "component_id": "INVESTMENT_REVIEW_SHADOW",
                    "status": "DATA_BLOCKED",
                    "reason": "DECISION_REVIEW_BLOCKED",
                    "packet": {
                        "review_outcome": "BLOCKED",
                        "ledger_record_created": False,
                        "capital": {"authorized": False, "amount": 0},
                        "action": None,
                        "order": None,
                        "stage_change": None,
                    },
                },
                {
                    "component_id": "SHADOW_ENTRY_REVIEW",
                    "status": "READY",
                    "reason": None,
                    "packet": {
                        "schema_version": "shadow_entry_review_briefing_status/1",
                        "sample_status": "NATURAL_OPERATIONAL_SAMPLE",
                        "summary": {
                            "candidate_count": 69,
                            "zero_capital_review_item_count": 1,
                            "probe_review_count": 1,
                        },
                        "policy_status": {
                            "candidate_validity": "UNRATIFIED",
                            "entry": "UNRATIFIED",
                            "position_management": "UNRATIFIED",
                            "position_size": "UNRATIFIED",
                        },
                        "review_items": [{
                            "subject": "005930",
                            "market": "KOREA",
                            "review_state": "REVERSAL_PROBE_REVIEW",
                            "participation_state": "PROBE_REVIEW",
                            "review_due_status": "REVIEW_OVERDUE",
                            "review_reason": "WEAK_PRICE_STATE_WITH_TWO_INDEPENDENT_TRIGGER_TYPES",
                            "money_boundary": {
                                "capital": 0,
                                "trade_proposal": None,
                                "stage_promotion_authority": False,
                                "buy_authority": False,
                                "action_authority": False,
                                "order_authority": False,
                                "production_authority": False,
                                "trading_authority": False,
                            },
                        }],
                        "why_not_executable": ["ENTRY_POLICY_UNRATIFIED"],
                        "authority": {
                            "capital": 0,
                            "trade_proposal": None,
                            "stage_promotion_authority": False,
                            "buy_authority": False,
                            "action_authority": False,
                            "order_authority": False,
                            "production_authority": False,
                            "trading_authority": False,
                        },
                    },
                },
            ],
        }
        dump(self.date_root / "rev-001/packet.json", packet)
        (self.date_root / "rev-001/briefing.md").write_text("# full briefing\n")
        dump(self.date_root / "index.json", {
            "schema_version": 1,
            "slot": self.slot,
            "decision_date": self.date,
            "latest_revision": 1,
            "revisions": [{
                "revision": 1,
                "path": "rev-001",
                "packet_sha256": "packet-digest",
            }],
        })
        self.validate = mock.patch.object(delivery, "validate_packet")
        self.validate_mock = self.validate.start()
        locator = delivery.build_locator(self.root, self.slot, self.date)
        delivery.write_locator(self.root, locator)

    def tearDown(self):
        self.validate.stop()
        self.tmp.cleanup()

    def test_exact_locator_delivers_blocked_review_and_no_shadow_record(self):
        result = delivery.consume(self.root, self.slot, self.date)
        review, shadow, zero_capital_review = result["components"]
        self.assertEqual(review["review_outcome"], "BLOCKED")
        self.assertIsNone(review["trade_proposal"])
        self.assertEqual(review["money_action"], "NONE")
        self.assertEqual(review["capital"], 0)
        self.assertFalse(shadow["ledger_record_created"])
        self.assertEqual(zero_capital_review["sample_status"], "NATURAL_OPERATIONAL_SAMPLE")
        self.assertEqual(zero_capital_review["capital"], 0)
        self.assertIsNone(zero_capital_review["trade_proposal"])
        self.assertEqual(zero_capital_review["review_items"][0]["subject"], "005930")
        self.assertFalse(any(result["authority"].values()))

    def test_wrong_date_never_falls_back(self):
        with self.assertRaisesRegex(delivery.DeliveryError, "LOCATOR_DATE_MISMATCH"):
            delivery.consume(self.root, self.slot, "2026-08-24")

    def test_wrong_slot_never_falls_back(self):
        with self.assertRaisesRegex(delivery.DeliveryError, "LOCATOR_SLOT_MISMATCH"):
            delivery.consume(self.root, "evening", self.date)

    def test_index_latest_revision_drift_is_rejected(self):
        index_path = self.date_root / "index.json"
        index = json.loads(index_path.read_text())
        index["latest_revision"] = 2
        dump(index_path, index)
        with self.assertRaisesRegex(delivery.DeliveryError, "INDEX_REVISION_INVALID"):
            delivery.consume(self.root, self.slot, self.date)

    def test_packet_path_tamper_even_with_existing_alternate_is_rejected(self):
        alternate = self.date_root / "rev-999"
        alternate.mkdir()
        (alternate / "packet.json").write_text(
            (self.date_root / "rev-001/packet.json").read_text()
        )
        (alternate / "briefing.md").write_text("# alternate\n")
        locator_path = self.root / delivery.LOCATOR_PATH
        locator = json.loads(locator_path.read_text())
        locator["packet_path"] = locator["packet_path"].replace("rev-001", "rev-999")
        dump(locator_path, locator)
        with self.assertRaisesRegex(delivery.DeliveryError, "LOCATOR_DRIFT_OR_TAMPER"):
            delivery.consume(self.root, self.slot, self.date)

    def test_packet_file_byte_tamper_is_rejected(self):
        path = self.date_root / "rev-001/packet.json"
        path.write_text(path.read_text() + " ")
        with self.assertRaisesRegex(delivery.DeliveryError, "LOCATOR_DRIFT_OR_TAMPER"):
            delivery.consume(self.root, self.slot, self.date)

    def test_briefing_byte_tamper_is_rejected(self):
        (self.date_root / "rev-001/briefing.md").write_text("# changed\n")
        with self.assertRaisesRegex(delivery.DeliveryError, "LOCATOR_DRIFT_OR_TAMPER"):
            delivery.consume(self.root, self.slot, self.date)

    def test_packet_re_read_revalidates_dynamic_clock_source_type(self):
        """The packet used for delivery must be the value that was validated.

        Simulate a local replacement after build_locator() validates and hashes
        the packet but before consume() reads it again for component delivery.
        """
        packet_path = self.date_root / "rev-001/packet.json"
        packet = json.loads(packet_path.read_text())
        packet["frozen_sources"] = {"DYNAMIC_CLOCK": {"kind": "unavailable"}}
        dump(packet_path, packet)
        delivery.write_locator(
            self.root, delivery.build_locator(self.root, self.slot, self.date)
        )

        real_read_json = delivery._read_json
        packet_reads = 0

        def replace_after_locator_check(path):
            nonlocal packet_reads
            value = real_read_json(path)
            if path == packet_path:
                packet_reads += 1
                if packet_reads == 2:
                    value["frozen_sources"]["DYNAMIC_CLOCK"] = []
            return value

        contexts_seen = []

        def validate_dynamic_clock_type(
            value, *, trusted_repository_root, trusted_validation_head,
            historical_source_commit,
        ):
            context = (trusted_repository_root, trusted_validation_head,
                       historical_source_commit)
            self.assertEqual(context, (self.root, None, None))
            contexts_seen.append(context)
            source = value.get("frozen_sources", {}).get("DYNAMIC_CLOCK")
            if not isinstance(source, dict):
                raise delivery.DeliveryError("DYNAMIC_CLOCK_SOURCE_INVALID")

        with mock.patch.object(
            delivery, "_read_json", side_effect=replace_after_locator_check
        ), mock.patch.object(
            delivery, "validate_packet", side_effect=validate_dynamic_clock_type
        ):
            with self.assertRaisesRegex(
                delivery.DeliveryError, "DYNAMIC_CLOCK_SOURCE_INVALID"
            ):
                delivery.consume(self.root, self.slot, self.date)

        self.assertEqual(contexts_seen, [(self.root, None, None)] * 2)

    def test_missing_component_is_rejected(self):
        path = self.date_root / "rev-001/packet.json"
        packet = json.loads(path.read_text())
        packet["components"] = packet["components"][:1]
        dump(path, packet)
        locator = delivery.build_locator(self.root, self.slot, self.date)
        delivery.write_locator(self.root, locator)
        with self.assertRaisesRegex(delivery.DeliveryError, "COMPONENT_MISSING"):
            delivery.consume(self.root, self.slot, self.date)

    def test_authority_escalation_is_rejected_even_when_locator_is_resigned(self):
        locator_path = self.root / delivery.LOCATOR_PATH
        locator = json.loads(locator_path.read_text())
        locator["authority"]["buy"] = True
        dump(locator_path, locator)
        with self.assertRaisesRegex(delivery.DeliveryError, "AUTHORITY_ESCALATION"):
            delivery.consume(self.root, self.slot, self.date)

    def test_blocked_review_with_trade_proposal_is_rejected(self):
        path = self.date_root / "rev-001/packet.json"
        packet = json.loads(path.read_text())
        packet["components"][0]["packet"]["trade_proposal"] = {"side": "BUY"}
        dump(path, packet)
        delivery.write_locator(
            self.root, delivery.build_locator(self.root, self.slot, self.date)
        )
        with self.assertRaisesRegex(delivery.DeliveryError, "BLOCKED_REVIEW_ACTION_LEAK"):
            delivery.consume(self.root, self.slot, self.date)

    def test_blocked_shadow_with_created_record_is_rejected(self):
        path = self.date_root / "rev-001/packet.json"
        packet = json.loads(path.read_text())
        packet["components"][1]["packet"]["ledger_record_created"] = True
        dump(path, packet)
        delivery.write_locator(
            self.root, delivery.build_locator(self.root, self.slot, self.date)
        )
        with self.assertRaisesRegex(delivery.DeliveryError, "BLOCKED_SHADOW_LEAK"):
            delivery.consume(self.root, self.slot, self.date)

    def test_zero_capital_review_authority_escalation_is_rejected(self):
        path = self.date_root / "rev-001/packet.json"
        packet = json.loads(path.read_text())
        packet["components"][2]["packet"]["review_items"][0]["money_boundary"][
            "buy_authority"
        ] = True
        dump(path, packet)
        delivery.write_locator(
            self.root, delivery.build_locator(self.root, self.slot, self.date)
        )
        with self.assertRaisesRegex(
            delivery.DeliveryError, "SHADOW_REVIEW_ITEM_AUTHORITY_INVALID"
        ):
            delivery.consume(self.root, self.slot, self.date)

    def test_zero_capital_review_post_hoc_field_is_rejected(self):
        path = self.date_root / "rev-001/packet.json"
        packet = json.loads(path.read_text())
        packet["components"][2]["packet"]["review_items"][0]["forward_return"] = 9.9
        dump(path, packet)
        delivery.write_locator(
            self.root, delivery.build_locator(self.root, self.slot, self.date)
        )
        with self.assertRaisesRegex(
            delivery.DeliveryError, "SHADOW_REVIEW_POST_HOC_FIELD_FORBIDDEN"
        ):
            delivery.consume(self.root, self.slot, self.date)

    def test_locator_write_is_idempotent(self):
        locator = delivery.build_locator(self.root, self.slot, self.date)
        self.assertFalse(delivery.write_locator(self.root, locator))

    def test_render_is_bounded_and_contains_no_full_packet_dump(self):
        text = delivery.render_delivery(delivery.consume(self.root, self.slot, self.date))
        self.assertIn("INVESTMENT_DECISION_REVIEW: DATA_BLOCKED", text)
        self.assertIn("SHADOW_ENTRY_REVIEW: READY", text)
        self.assertIn("005930 (KOREA): REVERSAL_PROBE_REVIEW", text)
        self.assertIn("capital=0 / trade_proposal=null", text)
        self.assertIn("Trading authority: false", text)
        self.assertNotIn("packet_sha256", text)
        self.assertNotIn("components\":", text)

    # -- external trusted Flow history context -------------------------------
    #
    # consume() validates the packet TWICE -- once inside the locator rebuild
    # and once on the exact in-memory value it goes on to deliver. Both are
    # real validation boundaries, so both must receive the same trusted
    # context: handing it to one and not the other would leave a path where a
    # packet clears the weaker boundary.

    def _contexts(self):
        return [call.kwargs for call in self.validate_mock.call_args_list]

    def test_no_context_is_the_normal_path_and_supplies_no_history(self):
        self.validate_mock.reset_mock()
        delivery.consume(self.root, self.slot, self.date)
        contexts = self._contexts()
        self.assertEqual(len(contexts), 2)
        for kwargs in contexts:
            self.assertIsNone(kwargs["historical_source_commit"])
            self.assertIsNone(kwargs["trusted_validation_head"])
            # The trusted root defaults to the repository being delivered
            # from -- never to anything named by the locator or the packet.
            self.assertEqual(kwargs["trusted_repository_root"], Path(self.root))

    def test_the_same_history_context_reaches_both_validation_paths(self):
        self.validate_mock.reset_mock()
        context = {
            "historical_source_commit": "a" * 40,
            "trusted_repository_root": self.root,
            "trusted_validation_head": "b" * 40,
        }
        delivery.consume(self.root, self.slot, self.date, history_context=context)
        contexts = self._contexts()
        self.assertEqual(len(contexts), 2)
        for kwargs in contexts:
            self.assertEqual(kwargs["historical_source_commit"], "a" * 40)
            self.assertEqual(kwargs["trusted_validation_head"], "b" * 40)
            self.assertEqual(kwargs["trusted_repository_root"], Path(self.root))

    def test_build_locator_forwards_context_on_its_own(self):
        self.validate_mock.reset_mock()
        delivery.build_locator(
            self.root, self.slot, self.date,
            history_context={"historical_source_commit": "c" * 40},
        )
        self.assertEqual(
            self._contexts()[0]["historical_source_commit"], "c" * 40
        )

    def test_both_cli_commands_accept_and_forward_the_context(self):
        for command in ("publish-locator", "consume"):
            with self.subTest(command=command):
                self.validate_mock.reset_mock()
                delivery.main([
                    command,
                    "--slot", self.slot,
                    "--decision-date", self.date,
                    "--repo-root", str(self.root),
                    "--historical-source-commit", "d" * 40,
                    "--trusted-validation-head", "e" * 40,
                ])
                contexts = self._contexts()
                self.assertTrue(contexts)
                for kwargs in contexts:
                    self.assertEqual(kwargs["historical_source_commit"], "d" * 40)
                    self.assertEqual(kwargs["trusted_validation_head"], "e" * 40)

    def test_a_validation_failure_is_never_softened_by_the_delivery_path(self):
        """An unreplayable packet stops delivery; it is not delivered anyway."""
        self.validate_mock.side_effect = RuntimeError(
            "UNREPLAYABLE_FLOW_HISTORY_SOURCE_COMMIT_REQUIRED: no context"
        )
        with self.assertRaisesRegex(
            RuntimeError, "UNREPLAYABLE_FLOW_HISTORY_SOURCE_COMMIT_REQUIRED"
        ):
            delivery.consume(self.root, self.slot, self.date)
        self.validate_mock.side_effect = None


class FlowRecoveryPositiveE2ETests(unittest.TestCase):
    """Real, UNMOCKED delivery of a real source-backed Flow version-1 packet.

    Every other test in this file replaces ``validate_packet`` with a mock, so
    none of them can show that the delivery path actually replays a frozen Flow
    envelope -- they prove forwarding, not authentication. These do: consume()
    runs for real, so the production ``validate_packet`` executes, and with it
    the ten-input Git authentication, the isolated materialization and the
    producer re-derivation behind it. Nothing here patches validate_packet,
    consume, build_locator or any subprocess.

    The evidence tree is an isolated fixture, but the TRUSTED REPOSITORY ROOT
    handed to validation is this real repository, supplied the only legitimate
    way -- as explicit external operator context. It is never read out of the
    locator or the packet, neither of which may name a repository at all.
    """

    SLOT = "morning"

    @classmethod
    def setUpClass(cls):
        cls.decision_date, cls.generated_at = flow_ready_basis()
        # One real, full orchestrator build. This is the fixture, not the
        # subject: the subject is what the delivery path does with it.
        cls.source_packet = orchestrator.build_packet(
            cls.SLOT, cls.decision_date, cls.generated_at
        )
        # Built ONCE. Each test works on a copy, so a full orchestrator
        # re-derivation is not repeated just to lay out files.
        cls.template_root = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.template_root, ignore_errors=True)
        cls._install(cls.template_root, cls.source_packet)
        # Building the locator already runs a real validate_packet, so a
        # fixture that could not be authenticated never reaches a test.
        delivery.write_locator(
            cls.template_root,
            delivery.build_locator(
                cls.template_root, cls.SLOT, cls.decision_date,
                history_context=cls.history_context(),
            ),
        )

    @classmethod
    def history_context(cls):
        """External operator context: the real repository to authenticate in.

        A Flow version-1 packet needs no historical source commit -- it carries
        its own envelope -- so that stays absent here on purpose.
        """
        return {"trusted_repository_root": ROOT}

    @classmethod
    def _install(cls, root: Path, packet: dict) -> None:
        date_root = (
            root / "evidence/daily_briefing" / cls.SLOT / cls.decision_date
        )
        dump(date_root / "rev-001/packet.json", packet)
        (date_root / "rev-001/briefing.md").write_text(
            f"# Atlas Daily Briefing {cls.decision_date} ({cls.SLOT})\n",
            encoding="utf-8",
        )
        dump(date_root / "index.json", {
            "schema_version": 1,
            "slot": cls.SLOT,
            "decision_date": cls.decision_date,
            "latest_revision": 1,
            "revisions": [{
                "revision": 1,
                "path": "rev-001",
                "packet_sha256": packet["packet_sha256"],
            }],
        })

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        shutil.copytree(self.template_root, self.root)

    def _flow_row(self, packet):
        rows = {row["component_id"]: row for row in packet["components"]}
        return rows["P2_FLOW_ENGINE"], rows

    def test_real_positive(self):
        """A populated Flow diagnostic really survives an unmocked delivery."""
        # Nothing in this class is mocked: the delivery module still holds the
        # production function it imported.
        self.assertIs(
            delivery.validate_packet, orchestrator.validate_packet
        )
        self.assertNotIsInstance(delivery.validate_packet, mock.Mock)

        packet = self.source_packet
        # (1) It is a real frozen Flow version-1 packet, not a legacy one.
        self.assertEqual(packet["flow_replay_version"], 1)
        envelope = packet["frozen_sources"][orchestrator.P2_FLOW_REPLAY_INPUTS]
        self.assertEqual(
            envelope["schema_version"], "capital_flow_replay_inputs/1"
        )
        self.assertEqual(len(envelope["files"]), 10)
        self.assertRegex(envelope["source_commit"], r"^[0-9a-f]{40}$")

        # (2) The Flow row is POPULATED, not a fail-closed empty row.
        flow_row, rows = self._flow_row(packet)
        self.assertEqual(flow_row["status"], "PENDING")
        self.assertIsNotNone(flow_row["packet"])
        flow_sha = flow_row["packet"]["payload_sha256"]
        self.assertEqual(
            flow_row["packet"]["schema_version"],
            "capital_flow_posture_reference/v1",
        )
        # ...and it stays a diagnostic: naming it grants nothing.
        self.assertFalse(flow_row["decision_eligible"])
        self.assertFalse(flow_row["action_eligible"])
        self.assertFalse(flow_row["order_eligible"])

        # (3) Downstream evidence: the same bytes reached both consumers.
        self.assertEqual(
            rows["DEFENSIVE_ACTION_DECISION"]["packet"]["lineage"][
                "source_packet_sha256"]["P2_FLOW_ENGINE"],
            flow_sha,
        )
        self.assertEqual(
            rows["STRATEGIC_CAPITAL_POSTURE"]["packet"]["lineage"][
                "source_packet_sha256"]["P2_CROSS_MARKET_FLOW"],
            flow_sha,
        )

        # (4) The real consumer path accepts it -- which means the real
        # validate_packet authenticated and replayed the envelope twice.
        delivered = delivery.consume(
            self.root, self.SLOT, self.decision_date,
            history_context=self.history_context(),
        )
        self.assertEqual(
            [row["component_id"] for row in delivered["components"]],
            list(delivery.DELIVERED_COMPONENTS),
        )
        self.assertFalse(any(delivered["authority"].values()))
        self.assertEqual(delivered["decision_date"], self.decision_date)

    def test_resigned_tamper_rejected(self):
        """A semantic Flow tamper, re-signed at every level, is still refused.

        The nested Flow packet is re-hashed on its own terms, the row's copy of
        that digest is relabelled, the whole briefing packet is re-signed and
        the index digest is updated to match -- so the packet's own hash chain
        is self-consistent and every byte-level check in the delivery path
        passes. What rejects it is real re-derivation from the frozen inputs,
        which reproduces the untampered Flow bytes.
        """
        tampered = copy.deepcopy(self.source_packet)
        flow_row, _rows = self._flow_row(tampered)
        self.assertEqual(
            flow_row["packet"]["cross_market_flow"]["actual_money_flow"],
            "UNKNOWN",
        )
        flow_row["packet"]["cross_market_flow"]["actual_money_flow"] = "US_TO_KR"
        flow_row["packet"]["payload_sha256"] = (
            orchestrator.CAPITAL_FLOW_ENGINE.payload_sha256({
                key: value
                for key, value in flow_row["packet"].items()
                if key != "payload_sha256"
            })
        )
        flow_row["source_packet_sha256"] = flow_row["packet"]["payload_sha256"]
        unsigned = {
            key: value for key, value in tampered.items()
            if key != "packet_sha256"
        }
        tampered["packet_sha256"] = orchestrator.payload_sha256(unsigned)
        self.assertNotEqual(
            tampered["packet_sha256"], self.source_packet["packet_sha256"]
        )
        self._install(self.root, tampered)

        with self.assertRaisesRegex(
            orchestrator.DailyOrchestratorError, "OUTPUT_MISMATCH"
        ):
            delivery.consume(
                self.root, self.SLOT, self.decision_date,
                history_context=self.history_context(),
            )

    def test_a_missing_trusted_root_does_not_soften_into_a_delivery_pass(self):
        """The isolated tree is not a repository, so authentication must fail.

        Without the external trusted root the frozen envelope cannot be proven
        against real Git objects. That is a hard provenance failure, and the
        delivery path must surface it rather than deliver anyway.
        """
        with self.assertRaises(
            orchestrator.CAPITAL_FLOW_ENGINE.CapitalFlowPostureReferenceError
        ):
            delivery.consume(self.root, self.SLOT, self.decision_date)


if __name__ == "__main__":
    unittest.main()
