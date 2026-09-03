#!/usr/bin/env python3
"""Deterministic validator (option B).

Three things are pinned before anything else: the validator can never grant
itself auto-apply authority, it can never assert a timeout, and a clean machine
run is never a final PASS.

Structural validation is delegated to the production H-24 scripts, so the
fixture installs stand-ins at those paths and the tests assert DELEGATION --
that B calls them and fails closed on their failure or absence -- rather than
re-testing semantics B no longer implements.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts"))
_s = importlib.util.spec_from_file_location("bf", ROOT / ".github/scripts/briefing_finalization.py")
bf = importlib.util.module_from_spec(_s); _s.loader.exec_module(bf)
_v = importlib.util.spec_from_file_location("bv", ROOT / ".github/scripts/briefing_validator.py")
bv = importlib.util.module_from_spec(_v); _v.loader.exec_module(bv)

SLOT, DATE = "evening", "2026-08-27"
BRIEFING = "# Atlas Daily Briefing — 2026-08-27 (evening)\n\nStep 0 = PASS.\n"
CONSUME = "### Investment Decision Review\n\nD3 / C1 / R1 / B0.\n"


def sha(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.build(); self.install_canonical(); self.seal()

    def install_canonical(self, packet_ok=True, consume_ok=True):
        """Stand-ins at the canonical paths. B must CALL these, not reimplement
        what they check (packet self-hash, locator schema/scope/authority)."""
        log = str(self.repo / "canonical_calls.log")
        template = (
            "import sys\n"
            "open({log!r}, 'a').write(' '.join(sys.argv) + chr(10))\n"
            "sys.stderr.write({msg!r})\n"
            "sys.exit({code})\n"
        )
        for rel, ok in ((bv.CANONICAL_ORCHESTRATOR, packet_ok),
                        (bv.CANONICAL_DELIVERY, consume_ok)):
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(template.format(
                log=log, msg="" if ok else "canonical validation failed",
                code=0 if ok else 1), encoding="utf-8")

    def canonical_calls(self):
        log = self.repo / "canonical_calls.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def build(self, briefing=BRIEFING, counts=None, latest_revision=1, step0_date=DATE):
        rev = self.repo / "evidence/daily_briefing" / SLOT / DATE / "rev-001"
        rev.mkdir(parents=True, exist_ok=True)
        packet = {"decision_date": DATE, "slot": SLOT,
                  "components": [{"id": "a", "status": "READY"}, {"id": "b", "status": "PENDING"}]}
        packet["packet_sha256"] = sha(json.dumps(packet, sort_keys=True, separators=(",", ":")).encode())
        pb = json.dumps(packet, sort_keys=True, indent=2).encode()
        (rev / "packet.json").write_bytes(pb)
        bb = briefing.encode()
        (rev / "briefing.md").write_bytes(bb)
        index = {"decision_date": DATE, "latest_revision": latest_revision, "schema_version": 1,
                 "slot": SLOT,
                 "revisions": [{"generated_at": "2026-08-27T09:30:00Z", "path": "rev-001",
                                "revision": 1, "packet_sha256": packet["packet_sha256"],
                                "component_status_counts": counts or {"READY": 1, "PENDING": 1}}]}
        ib = json.dumps(index, sort_keys=True, indent=2).encode()
        (rev.parent / "index.json").write_bytes(ib)
        locator = {"schema_version": "daily_briefing_delivery/1", "slot": SLOT,
                   "decision_date": DATE, "revision": 1,
                   "index_path": f"evidence/daily_briefing/{SLOT}/{DATE}/index.json",
                   "index_sha256": sha(ib),
                   "packet_path": f"evidence/daily_briefing/{SLOT}/{DATE}/rev-001/packet.json",
                   "packet_file_sha256": sha(pb), "packet_sha256": packet["packet_sha256"],
                   "briefing_path": f"evidence/daily_briefing/{SLOT}/{DATE}/rev-001/briefing.md",
                   "briefing_sha256": sha(bb),
                   # real locator carries these; B must not judge them itself
                   "delivery_scope": ["INVESTMENT_DECISION_REVIEW", "INVESTMENT_REVIEW_SHADOW",
                                      "SHADOW_ENTRY_REVIEW"],
                   "authority": {"collector_authority": False, "trading_authority": False,
                                 "production_authority": False, "recovery_authority": False,
                                 "schedule_authority": False, "data_readiness_authority": False}}
        loc = self.repo / bf.LOCATOR_PATH
        loc.parent.mkdir(parents=True, exist_ok=True)
        loc.write_text(json.dumps(locator, sort_keys=True, indent=2), encoding="utf-8")
        step0 = self.repo / "data/briefing/step0_status.json"
        step0.parent.mkdir(parents=True, exist_ok=True)
        step0.write_text(json.dumps({
            "expected_kst_date": step0_date,
            "collectors": {"krx": {"ok": 7, "failed": 0, "collected_for_kst_date": step0_date},
                           "sec": {"ok": 7, "failed": 0, "collected_for_kst_date": step0_date}},
            "totals": {"ok": 14, "failed": 0}}), encoding="utf-8")
        self.consume = self.repo / "consume.md"
        self.consume.write_text(CONSUME, encoding="utf-8")

    def seal(self):
        return bf.seal(self.repo, DATE, SLOT, self.consume)

    def run_validator(self):
        return bv.validate(self.repo, DATE, SLOT)


class Invariants(Base):
    def test_never_names_a_conclusion_spec(self):
        self.assertIsNone(self.run_validator()["conclusion_diff"]["spec_version"])

    def test_a_clean_verdict_is_not_even_submittable(self):
        """No validation_status at all -- the gate cannot mistake it for a PASS."""
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.derive_routing(self.run_validator())
        self.assertEqual(ctx.exception.code, "FINALIZATION_STATUS_UNSUPPORTED")

    def test_a_submitted_verdict_cannot_open_auto_apply(self):
        self.build(counts={"READY": 9, "PENDING": 1}); self.install_canonical(); self.seal()
        verdict = self.run_validator()
        self.assertEqual(verdict["validation_status"], "PASS_WITH_CORRECTION")
        routing = bf.derive_routing(verdict, ratified_specs=["anything"])
        self.assertFalse(routing["auto_apply_allowed"])
        self.assertEqual(routing["investment_conclusion_changed"], bf.UNKNOWN)
        self.assertTrue(routing["cio_gate_required"])

    def test_never_emits_a_timeout(self):
        for briefing in (BRIEFING, BRIEFING + "\nnoise\n"):
            self.build(briefing=briefing); self.install_canonical(); self.seal()
            verdict = self.run_validator()
            self.assertNotIn(verdict.get("validation_status"),
                             bf.INTERNAL_VALIDATION_STATUSES)

    def test_verdict_names_the_payload_it_examined(self):
        verdict = self.run_validator()
        draft = json.loads(bf._latest(bf.slot_dir(self.repo, DATE, SLOT), "draft").read_text())
        self.assertEqual(verdict["delivery_payload_sha256"], draft["delivery_payload_sha256"])

    def test_a_hold_verdict_is_accepted_by_the_gate(self):
        (self.repo / "evidence/daily_briefing" / SLOT / DATE / "rev-001/briefing.md").write_text("x")
        emitted = bv.emit(self.repo, DATE, SLOT, self.run_validator())
        self.assertIsNotNone(emitted["inbox"])
        out = bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertTrue(out["ingested"])
        self.assertEqual(out["authority_files"]["machine"], Path(emitted["inbox"]).name)

    def test_semantic_gaps_are_always_declared(self):
        verdict = self.run_validator()
        self.assertTrue(verdict["unverified_semantic"])
        joined = " ".join(verdict["unverified_semantic"])
        for topic in ("FACT_CLAIMS", "CAUSAL_CLAIMS", "STAGE_DECISIONS"):
            self.assertIn(topic, joined)


class MachinePassIsNotFinalPass(Base):
    """rev 1 turned a clean machine run into a final PASS and shipped it -- and
    a test locked that in. The machine cannot see whether a claim is true."""

    def test_clean_run_is_machine_pass_not_pass(self):
        verdict = self.run_validator()
        self.assertEqual(verdict["machine_status"], "MACHINE_PASS", verdict["findings"])
        self.assertEqual(verdict["semantic_status"], "UNVERIFIED")
        self.assertNotIn("validation_status", verdict)
        self.assertTrue(verdict["final_validation_open"])
        self.assertFalse(verdict["submits_to_gate"])

    def test_clean_run_writes_no_inbox_verdict(self):
        emitted = bv.emit(self.repo, DATE, SLOT, self.run_validator())
        self.assertIsNone(emitted["inbox"])
        self.assertTrue((self.repo / emitted["machine_record"]).exists())
        self.assertEqual(bf.ingest_inbox(self.repo, DATE, SLOT)["reason"],
                         "FINALIZATION_VALIDATION_INBOX_ABSENT")

    def test_clean_run_does_not_deliver_immediately(self):
        os.environ["GITHUB_STEP_SUMMARY"] = str(self.repo / "summary.md")
        self.addCleanup(os.environ.pop, "GITHUB_STEP_SUMMARY", None)
        bv.emit(self.repo, DATE, SLOT, self.run_validator())
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"],
                       durability_probe=lambda *_: True)
        # the gate's verdict slot is still open until the semantic validator answers
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_PENDING")
        self.assertFalse(bf.already_delivered(self.repo, DATE, SLOT))

    def test_machine_record_is_kept_even_when_nothing_is_submitted(self):
        emitted = bv.emit(self.repo, DATE, SLOT, self.run_validator())
        record = json.loads((self.repo / emitted["machine_record"]).read_text())
        self.assertEqual(record["machine_status"], "MACHINE_PASS")
        self.assertEqual(record["checks_run"][0], "canonical_structure")


class MachineBlockWithdrawal(Base):
    """A clean run must be able to retract its OWN earlier block -- and nothing
    else. rev 2 could raise a structural HOLD but never take it back."""

    def _tamper(self):
        (self.repo / "evidence/daily_briefing" / SLOT / DATE / "rev-001/briefing.md").write_text("x")

    def _restore(self):
        self.build(); self.install_canonical()

    def test_hold_then_fix_withdraws_the_block(self):
        self._tamper()
        bv.emit(self.repo, DATE, SLOT, self.run_validator())
        bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertEqual(bf.govern(bf._recorded_validations(
            bf.slot_dir(self.repo, DATE, SLOT)))["validation_status"], "HOLD")

        self._restore()
        verdict = self.run_validator()
        self.assertEqual(verdict["machine_status"], "MACHINE_PASS")
        self.assertTrue(verdict["clears_prior_machine_block"])
        self.assertEqual(verdict["validation_status"], bf.MACHINE_CLEARED)
        bv.emit(self.repo, DATE, SLOT, verdict)
        bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertIsNone(bf.govern(bf._recorded_validations(
            bf.slot_dir(self.repo, DATE, SLOT))))

    def test_correction_then_fix_withdraws_the_block(self):
        self.build(counts={"READY": 9, "PENDING": 1}); self.install_canonical(); self.seal()
        bv.emit(self.repo, DATE, SLOT, self.run_validator())
        bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertEqual(bf.govern(bf._recorded_validations(
            bf.slot_dir(self.repo, DATE, SLOT)))["validation_status"], "PASS_WITH_CORRECTION")

        self._restore()
        verdict = self.run_validator()
        self.assertEqual(verdict["validation_status"], bf.MACHINE_CLEARED)
        bv.emit(self.repo, DATE, SLOT, verdict)
        bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertIsNone(bf.govern(bf._recorded_validations(
            bf.slot_dir(self.repo, DATE, SLOT))))

    def test_withdrawal_is_not_a_pass(self):
        self._tamper()
        bv.emit(self.repo, DATE, SLOT, self.run_validator())
        bf.ingest_inbox(self.repo, DATE, SLOT)
        self._restore()
        bv.emit(self.repo, DATE, SLOT, self.run_validator())
        bf.ingest_inbox(self.repo, DATE, SLOT)
        os.environ["GITHUB_STEP_SUMMARY"] = str(self.repo / "summary.md")
        self.addCleanup(os.environ.pop, "GITHUB_STEP_SUMMARY", None)
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"],
                       durability_probe=lambda *_: True)
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_PENDING")

    def test_no_withdrawal_is_emitted_when_nothing_was_blocked(self):
        verdict = self.run_validator()
        self.assertFalse(verdict["clears_prior_machine_block"])
        self.assertIsNone(bv.emit(self.repo, DATE, SLOT, verdict)["inbox"])

    def test_a_clean_run_cannot_lift_a_semantic_hold(self):
        self._tamper()
        bv.emit(self.repo, DATE, SLOT, self.run_validator())
        bf.ingest_inbox(self.repo, DATE, SLOT)
        bf.record_validation(self.repo, DATE, SLOT, {
            "delivery_payload_sha256": json.loads(bf._latest(
                bf.slot_dir(self.repo, DATE, SLOT), "draft").read_text())["delivery_payload_sha256"],
            "validation_status": "HOLD", "authority_stream": "semantic",
            "validator_id": "cio", "corrections": [],
            "conclusion_diff": {"spec_version": None}})
        self._restore()
        bv.emit(self.repo, DATE, SLOT, self.run_validator())
        bf.ingest_inbox(self.repo, DATE, SLOT)
        governing = bf.govern(bf._recorded_validations(bf.slot_dir(self.repo, DATE, SLOT)))
        self.assertEqual(governing["validation_status"], "HOLD")
        self.assertEqual(governing["authority_stream"], "semantic")


class CrashBoundary(Base):
    """Wiring acceptance conditions."""

    def _tamper(self):
        (self.repo / "evidence/daily_briefing" / SLOT / DATE / "rev-001/briefing.md").write_text("x")

    def test_unrecorded_prior_hold_is_still_withdrawn(self):
        """A runner died between emitting a machine HOLD and ingesting it. The
        cause is fixed. rev 3 saw no RECORDED block, issued no withdrawal, and
        the stale HOLD was then ingested for the first time -- blocking a
        briefing that was already correct."""
        self._tamper()
        emitted = bv.emit(self.repo, DATE, SLOT, self.run_validator())
        self.assertIsNotNone(emitted["inbox"])          # published, never ingested
        self.assertEqual(bf._recorded_validations(bf.slot_dir(self.repo, DATE, SLOT)), [])

        self.build(); self.install_canonical()          # fixed
        verdict = self.run_validator()
        self.assertEqual(verdict["machine_status"], "MACHINE_PASS")
        self.assertTrue(verdict["clears_prior_machine_block"])
        bv.emit(self.repo, DATE, SLOT, verdict)
        bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertIsNone(bf.govern(bf._recorded_validations(bf.slot_dir(self.repo, DATE, SLOT))))

    def test_the_wiring_order_also_closes_it(self):
        """Belt and braces: reconciling first reaches the same state."""
        self._tamper()
        bv.emit(self.repo, DATE, SLOT, self.run_validator())
        bf.ingest_inbox(self.repo, DATE, SLOT)          # the reconcile step
        self.build(); self.install_canonical()
        bv.emit(self.repo, DATE, SLOT, self.run_validator())
        bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertIsNone(bf.govern(bf._recorded_validations(bf.slot_dir(self.repo, DATE, SLOT))))

    def test_unreadable_pending_inbox_counts_as_blocking(self):
        directory = bf.slot_dir(self.repo, DATE, SLOT)
        (directory / "validation-inbox-rev-001.json").write_text("{partial", encoding="utf-8")
        self.assertTrue(bv.machine_stream_is_blocking(self.repo, DATE, SLOT))

    def test_publication_is_atomic(self):
        """The gate fails closed on an unparseable inbox, so a writer that can
        leave half a file behind can permanently block a slot by crashing."""
        import unittest.mock as mock
        self._tamper()
        verdict = self.run_validator()
        real = bv.bf._atomic_write
        seen = {}

        def spy(path, payload):
            seen[path.name] = True
            return real(path, payload)

        # bv imports its own module instance; patch THAT one.
        with mock.patch.object(bv.bf, "_atomic_write", spy):
            bv.emit(self.repo, DATE, SLOT, verdict)
        self.assertTrue(any(n.startswith("validation-inbox-rev-") for n in seen))
        self.assertTrue(any(n.startswith("machine-validation-rev-") for n in seen))

    def test_no_partial_file_survives_a_failed_write(self):
        import unittest.mock as mock
        self._tamper()
        verdict = self.run_validator()
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                bv.emit(self.repo, DATE, SLOT, verdict)
        directory = bf.slot_dir(self.repo, DATE, SLOT)
        self.assertEqual(list(directory.glob("validation-inbox-rev-*.json")), [])
        # and nothing half-written is visible to the gate
        _v, problem = bf.resolve_validation(directory)
        self.assertIsNone(problem)


class CanonicalDelegation(Base):
    def test_both_canonical_validators_are_invoked(self):
        self.run_validator()
        calls = " ".join(self.canonical_calls())
        self.assertIn("daily_orchestrator.py validate", calls)
        self.assertIn("daily_briefing_delivery.py consume", calls)

    def test_packet_validation_failure_holds(self):
        self.install_canonical(packet_ok=False)
        verdict = self.run_validator()
        self.assertEqual(verdict["machine_status"], "HOLD")
        self.assertIn("CANONICAL_PACKET_VALIDATION_FAILED",
                      {f["code"] for f in verdict["findings"]})

    def test_consume_failure_holds(self):
        self.install_canonical(consume_ok=False)
        verdict = self.run_validator()
        self.assertEqual(verdict["machine_status"], "HOLD")
        self.assertIn("CANONICAL_CONSUME_FAILED", {f["code"] for f in verdict["findings"]})

    def test_absent_canonical_validators_fail_closed(self):
        """Rather than silently downgrading to a weaker local implementation."""
        for rel in (bv.CANONICAL_ORCHESTRATOR, bv.CANONICAL_DELIVERY):
            (self.repo / rel).unlink()
        verdict = self.run_validator()
        self.assertEqual(verdict["machine_status"], "HOLD")
        self.assertIn("CANONICAL_VALIDATOR_UNAVAILABLE", {f["code"] for f in verdict["findings"]})

    def test_structural_authority_is_declared(self):
        self.assertEqual(self.run_validator()["structural_authority"],
                         [bv.CANONICAL_ORCHESTRATOR, bv.CANONICAL_DELIVERY])

    def test_packet_replacement_after_binding_fails_closed(self):
        """Arithmetic must use the packet bytes whose Dynamic Clock was checked."""
        import unittest.mock as mock

        packet_path = (
            self.repo / "evidence/daily_briefing" / SLOT / DATE / "rev-001/packet.json"
        )
        locator_path = self.repo / bf.LOCATOR_PATH
        real_bind_locator = bv.bf.bind_locator

        def replace_after_binding(repo_root, kst_date, slot):
            bound = real_bind_locator(repo_root, kst_date, slot)
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            packet["frozen_sources"] = {"DYNAMIC_CLOCK": []}
            unsigned = dict(packet)
            unsigned.pop("packet_sha256")
            packet["packet_sha256"] = sha(
                json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
            )
            packet_bytes = json.dumps(packet, sort_keys=True, indent=2).encode()
            packet_path.write_bytes(packet_bytes)

            locator = json.loads(locator_path.read_text(encoding="utf-8"))
            locator["packet_file_sha256"] = sha(packet_bytes)
            locator["packet_sha256"] = packet["packet_sha256"]
            locator_path.write_text(
                json.dumps(locator, sort_keys=True, indent=2), encoding="utf-8"
            )
            return bound

        with mock.patch.object(
            bv.bf, "bind_locator", side_effect=replace_after_binding
        ):
            verdict = self.run_validator()

        self.assertEqual(verdict["machine_status"], "HOLD")
        self.assertIn(
            "VALIDATED_PACKET_CHANGED_BEFORE_USE",
            {finding["code"] for finding in verdict["findings"]},
        )


class StructuralFaultsHold(Base):
    def test_tampered_briefing_holds(self):
        (self.repo / "evidence/daily_briefing" / SLOT / DATE / "rev-001/briefing.md").write_text("x")
        verdict = self.run_validator()
        self.assertEqual(verdict["machine_status"], "HOLD")
        self.assertEqual(verdict["validation_status"], "HOLD")
        self.assertIn("FINALIZATION_BRIEFING_SHA_MISMATCH", {f["code"] for f in verdict["findings"]})

    def test_hold_is_refused_by_the_gate(self):
        (self.repo / "evidence/daily_briefing" / SLOT / DATE / "rev-001/briefing.md").write_text("x")
        bv.emit(self.repo, DATE, SLOT, self.run_validator())
        bf.ingest_inbox(self.repo, DATE, SLOT)
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"],
                       durability_probe=lambda *_: True)
        self.assertEqual(ctx.exception.code, "FINALIZATION_HELD")


class DeterministicCorrections(Base):
    def test_component_counts_are_checked_against_the_packet(self):
        self.build(counts={"READY": 5, "PENDING": 1}); self.install_canonical(); self.seal()
        verdict = self.run_validator()
        self.assertEqual(verdict["validation_status"], "PASS_WITH_CORRECTION")
        classes = {c["class"] for c in verdict["corrections"]}
        self.assertEqual(classes, {"ARITHMETIC"})

    def test_step0_totals_arithmetic(self):
        step0 = self.repo / "data/briefing/step0_status.json"
        data = json.loads(step0.read_text()); data["totals"]["ok"] = 99
        step0.write_text(json.dumps(data))
        codes = {f["code"] for f in self.run_validator()["findings"]}
        self.assertIn("STEP0_TOTALS_WRONG", codes)

    def test_header_date_mismatch_is_a_date_correction(self):
        self.build(briefing="# Atlas Daily Briefing — 2026-08-19 (evening)\n\nx\n")
        self.install_canonical(); self.seal()
        verdict = self.run_validator()
        correction = next(c for c in verdict["corrections"] if c["class"] == "DATE")
        self.assertEqual(correction["before"], "2026-08-19")
        self.assertEqual(correction["after"], DATE)

    def test_stale_step0_is_observed_not_corrected(self):
        """A briefing may legitimately report that data is not ready; the
        validator records the gap without judging the prose."""
        self.build(step0_date="2026-08-19"); self.install_canonical(); self.seal()
        verdict = self.run_validator()
        codes = {f["code"] for f in verdict["findings"]}
        self.assertIn("STEP0_DATE_NOT_TODAY", codes)
        self.assertEqual(verdict["machine_status"], "MACHINE_PASS")

    def test_header_without_any_date_is_a_date_correction(self):
        self.build(briefing="# Atlas Daily Briefing — evening\n\nx\n")
        self.install_canonical(); self.seal()
        verdict = self.run_validator()
        correction = next(c for c in verdict["corrections"] if c["class"] == "DATE")
        self.assertIsNone(correction["before"])
        self.assertEqual(correction["after"], DATE)
        self.assertEqual(verdict["machine_status"], "PASS_WITH_CORRECTION")

    def test_compact_view_dates_are_cross_referenced(self):
        view = self.repo / "data/briefing/krx/298040.json"
        view.parent.mkdir(parents=True, exist_ok=True)
        view.write_text(json.dumps({"source": {"collected_for_kst_date": "2026-08-19"}}))
        codes = {f["code"] for f in self.run_validator()["findings"]}
        self.assertIn("COMPACT_VIEW_DATE_NOT_TODAY", codes)


class EvidenceGradeRuleUndefined(Base):
    def test_absent_rule_means_not_checked_not_passed(self):
        verdict = self.run_validator()
        joined = " ".join(verdict["unverified_semantic"])
        self.assertIn("EVIDENCE_GRADES", joined)
        self.assertIn("no ratified mechanical rule", joined)
        self.assertNotIn("EVIDENCE_GRADE_SOURCE_MISSING",
                         {f["code"] for f in verdict["findings"]})

    def test_ratified_rule_is_applied(self):
        rule = self.repo / bv.EVIDENCE_RULE_PATH
        rule.parent.mkdir(parents=True, exist_ok=True)
        rule.write_text(json.dumps({"grade_patterns": {
            "OFFICIAL": {"marker": "⭕", "requires_source_path": True}}}))
        self.build(briefing="# Atlas Daily Briefing — 2026-08-27 (evening)\n\n"
                            "⭕ Step 0 PASS per data/briefing/nonexistent.json\n")
        self.install_canonical(); self.seal()
        verdict = self.run_validator()
        self.assertIn("EVIDENCE_GRADE_SOURCE_MISSING", {f["code"] for f in verdict["findings"]})
        self.assertEqual(verdict["validation_status"], "PASS_WITH_CORRECTION")


class PostDeliveryHandoff(Base):
    def test_change_observations_are_handed_to_the_validator_output(self):
        os.environ["GITHUB_STEP_SUMMARY"] = str(self.repo / "summary.md")
        self.addCleanup(os.environ.pop, "GITHUB_STEP_SUMMARY", None)
        draft = json.loads(bf._latest(
            bf.slot_dir(self.repo, DATE, SLOT), "draft").read_text())
        bf.record_validation(self.repo, DATE, SLOT, {
            "delivery_payload_sha256": draft["delivery_payload_sha256"],
            "validation_status": "PASS",
            "validator_id": "semantic-reviewer",
            "validated_at_utc": "2026-08-27T09:40:00Z",
            "corrections": [],
            "conclusion_diff": {
                "spec_version": None,
                "investment_conclusion_changed": False,
                "money_action_changed": False,
                "stage_changed": False,
            },
        })
        bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"],
                   durability_probe=lambda *_: True)
        self.consume.write_text(CONSUME + "\naddendum\n", encoding="utf-8")
        self.seal()
        inputs = self.run_validator()["post_delivery_inputs"]
        self.assertEqual(len(inputs), 1)
        self.assertIn("consume_sha256", inputs[0]["changed_axes"])
        self.assertEqual(inputs[0]["capital_impact"], bf.UNKNOWN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
