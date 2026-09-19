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

import copy
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

    # -- external trusted Flow history context -------------------------------
    #
    # Structural validation is delegated to two SUBPROCESSES, so the trusted
    # history context has to survive that hop. Both get exactly the same
    # arguments: threading it into one and not the other would let a packet
    # clear one canonical boundary and not the other.

    def test_no_context_adds_no_arguments(self):
        self.run_validator()
        for call in self.canonical_calls():
            self.assertNotIn("--historical-source-commit", call)
            self.assertNotIn("--trusted-repository-root", call)
            self.assertNotIn("--trusted-validation-head", call)

    def test_history_context_is_threaded_into_both_subprocesses(self):
        bv.validate(self.repo, DATE, SLOT, history_context={
            "historical_source_commit": "a" * 40,
            "trusted_repository_root": str(self.repo),
            "trusted_validation_head": "b" * 40,
        })
        calls = self.canonical_calls()
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("daily_orchestrator.py" in call for call in calls))
        self.assertTrue(any("daily_briefing_delivery.py" in call for call in calls))
        for call in calls:
            self.assertIn(f"--historical-source-commit {'a' * 40}", call)
            self.assertIn(f"--trusted-repository-root {self.repo}", call)
            self.assertIn(f"--trusted-validation-head {'b' * 40}", call)

    def test_a_partial_context_forwards_only_what_was_supplied(self):
        """Nothing is invented to fill a gap -- no live HEAD, no locator value."""
        bv.validate(self.repo, DATE, SLOT, history_context={
            "historical_source_commit": "c" * 40,
        })
        for call in self.canonical_calls():
            self.assertIn(f"--historical-source-commit {'c' * 40}", call)
            self.assertNotIn("--trusted-validation-head", call)
            self.assertNotIn("--trusted-repository-root", call)

    def test_missing_required_context_stays_a_blocking_structural_finding(self):
        """A canonical validator that refuses for want of context is a HOLD.

        It is NOT softened to PASS, and the machine run is not entitled to
        withdraw anything on the strength of it.
        """
        self.install_canonical(packet_ok=False)
        verdict = bv.validate(self.repo, DATE, SLOT, history_context={})
        self.assertEqual(verdict["machine_status"], "HOLD")
        self.assertEqual(verdict["validation_status"], "HOLD")
        self.assertFalse(verdict["clears_prior_machine_block"])
        self.assertIn("CANONICAL_PACKET_VALIDATION_FAILED",
                      {f["code"] for f in verdict["findings"]})

    def test_the_cli_accepts_and_forwards_the_context(self):
        bv.main(["--slot", SLOT, "--decision-date", DATE,
                 "--repo-root", str(self.repo),
                 "--historical-source-commit", "d" * 40,
                 "--trusted-validation-head", "e" * 40])
        calls = self.canonical_calls()
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIn(f"--historical-source-commit {'d' * 40}", call)
            self.assertIn(f"--trusted-validation-head {'e' * 40}", call)

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


class FlowRecoveryPositiveE2ETests(unittest.TestCase):
    """Real, UNMOCKED delegation of a real source-backed Flow version-1 packet.

    Every other test in this file installs stand-in scripts at the canonical
    paths, so none of them can show that the two delegated subprocesses can
    actually replay a frozen Flow envelope -- they prove delegation and
    argument threading, not authentication. These do: the REAL
    ``briefing/daily_orchestrator.py validate`` and REAL
    ``.github/scripts/daily_briefing_delivery.py consume`` are spawned by the
    production ``check_canonical_structure``, so the production
    ``validate_packet`` executes inside each subprocess, and with it the
    ten-input Git authentication, the isolated materialization and the producer
    re-derivation. Nothing here is mocked or stubbed.

    The fixture repository is isolated so no test writes into the real
    checkout, and it reaches the canonical scripts honestly:

      * ``briefing/`` is a symlink, so the orchestrator's own
        ``Path(__file__).resolve()`` still lands on the real repository and it
        loads its real sibling builders and evidence;
      * the delivery script is a real COPY, so its ``REPO_ROOT`` is the fixture
        and it reads the fixture's locator rather than the real checkout's.

    The TRUSTED REPOSITORY ROOT used to authenticate the frozen inputs is the
    real repository, supplied the only legitimate way -- as explicit external
    operator context, threaded to both subprocesses as
    ``--trusted-repository-root``. It is never read out of the locator or the
    packet.
    """

    SLOT = "morning"

    @classmethod
    def setUpClass(cls):
        # Loaded lazily: importing the orchestrator is expensive and no other
        # test in this file needs it.
        spec = importlib.util.spec_from_file_location(
            "flow_e2e_delivery", ROOT / ".github/scripts/daily_briefing_delivery.py"
        )
        cls.delivery = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.delivery)
        import briefing.daily_orchestrator as orchestrator

        cls.orchestrator = orchestrator
        reference = orchestrator.CAPITAL_FLOW_ENGINE.build_reference()
        flow_dt = orchestrator.dt.datetime.fromisoformat(
            reference["generated_at"].replace("Z", "+00:00")
        )
        # Decide for the day AFTER the real Flow evidence and generate at the
        # end of that KST day, so the Flow row is a POPULATED diagnostic rather
        # than the correct-but-empty DATA_BLOCKED row a stale hardcoded date
        # would produce. Neither temporal check is loosened.
        decision_date = flow_dt.date() + orchestrator.dt.timedelta(days=1)
        cls.date = decision_date.isoformat()
        cls.generated_at = (
            orchestrator.dt.datetime.fromisoformat(f"{cls.date}T23:59:59")
            .replace(tzinfo=orchestrator.KST)
            .astimezone(orchestrator.UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
        cls.source_packet = orchestrator.build_packet(
            cls.SLOT, cls.date, cls.generated_at
        )
        # Built ONCE. Each test works on a copy, so a full orchestrator
        # re-derivation is not repeated just to lay out files.
        cls.template = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.template, ignore_errors=True)
        # The orchestrator resolves its own ROOT through this symlink, so it
        # keeps reading the real repository's builders and evidence.
        (cls.template / "briefing").symlink_to(ROOT / "briefing")
        scripts = cls.template / ".github/scripts"
        scripts.mkdir(parents=True)
        # A real copy, NOT a symlink: the delivery script derives its repo root
        # from its own resolved location, and it must land on this fixture.
        shutil.copy2(
            ROOT / ".github/scripts/daily_briefing_delivery.py",
            scripts / "daily_briefing_delivery.py",
        )
        cls._install(cls.template, cls.source_packet)
        (cls.template / "evidence/daily_briefing" / cls.SLOT / cls.date
         / "rev-001/briefing.md").write_text(
            f"# Atlas Daily Briefing {cls.date} ({cls.SLOT})\n", encoding="utf-8"
        )
        # A real build_locator run -- itself a real validate_packet -- so a
        # fixture that could not be authenticated never reaches a test.
        cls.delivery.write_locator(
            cls.template,
            cls.delivery.build_locator(
                cls.template, cls.SLOT, cls.date,
                history_context=cls.history_context(),
            ),
        )

    def setUp(self):
        parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        self.repo = parent / "repo"
        # symlinks=True keeps briefing/ a symlink to the real repository
        # rather than duplicating it.
        shutil.copytree(self.template, self.repo, symlinks=True)
        self.date_root = (
            self.repo / "evidence/daily_briefing" / self.SLOT / self.date
        )

    @classmethod
    def history_context(cls):
        """External operator context: the real repository to authenticate in.

        A Flow version-1 packet carries its own envelope, so no historical
        source commit is supplied -- and none is invented.
        """
        return {"trusted_repository_root": str(ROOT)}

    @classmethod
    def _install(cls, root: Path, packet: dict) -> None:
        """Write the packet and an index whose digest matches it.

        The index digest is kept consistent on purpose, including for the
        tampered packet: every self-consistency check must pass so that only
        real re-derivation can reject it.
        """
        date_root = root / "evidence/daily_briefing" / cls.SLOT / cls.date
        rev = date_root / "rev-001"
        rev.mkdir(parents=True, exist_ok=True)
        (rev / "packet.json").write_text(
            json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (date_root / "index.json").write_text(
            json.dumps({
                "schema_version": 1,
                "slot": cls.SLOT,
                "decision_date": cls.date,
                "latest_revision": 1,
                "revisions": [{
                    "revision": 1,
                    "path": "rev-001",
                    "packet_sha256": packet["packet_sha256"],
                }],
            }, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _canonical_codes(self, findings):
        return {f["code"] for f in findings if f["code"].startswith("CANONICAL_")}

    def test_real_positive(self):
        """Both real canonical subprocesses accept a populated Flow-1 packet."""
        packet = self.source_packet
        # (1) A real frozen Flow version-1 packet.
        self.assertEqual(packet["flow_replay_version"], 1)
        envelope = packet["frozen_sources"][
            self.orchestrator.P2_FLOW_REPLAY_INPUTS
        ]
        self.assertEqual(len(envelope["files"]), 10)
        self.assertRegex(envelope["source_commit"], r"^[0-9a-f]{40}$")

        # (2) A POPULATED Flow diagnostic, not a fail-closed empty row.
        rows = {row["component_id"]: row for row in packet["components"]}
        flow_row = rows["P2_FLOW_ENGINE"]
        self.assertEqual(flow_row["status"], "PENDING")
        self.assertIsNotNone(flow_row["packet"])
        flow_sha = flow_row["packet"]["payload_sha256"]
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

        # (4) The real delegation path, through two real subprocesses.
        findings, _bound = bv.check_canonical_structure(
            self.repo, self.date, self.SLOT,
            history_context=self.history_context(),
        )
        self.assertEqual(self._canonical_codes(findings), set(), findings)

    def test_resigned_tamper_rejected(self):
        """A semantic Flow tamper, re-signed at every level, is still refused.

        The nested Flow packet is re-hashed on its own terms, the row's copy of
        that digest is relabelled, the briefing packet is re-signed and the
        index digest is updated -- so the packet's own hash chain is
        self-consistent and every byte-level check passes. What rejects it is
        real re-derivation from the frozen inputs.

        The exact code is asserted in-process, because the subprocess detail is
        a truncated traceback; the subprocess assertions below then prove the
        two canonical validators really do fail closed on it. The positive test
        above shares this fixture, so a clean run there is what rules out the
        fixture itself being the cause.
        """
        tampered = copy.deepcopy(self.source_packet)
        flow_row = next(
            row for row in tampered["components"]
            if row["component_id"] == "P2_FLOW_ENGINE"
        )
        self.assertEqual(
            flow_row["packet"]["cross_market_flow"]["actual_money_flow"], "UNKNOWN"
        )
        flow_row["packet"]["cross_market_flow"]["actual_money_flow"] = "US_TO_KR"
        flow_row["packet"]["payload_sha256"] = (
            self.orchestrator.CAPITAL_FLOW_ENGINE.payload_sha256({
                key: value
                for key, value in flow_row["packet"].items()
                if key != "payload_sha256"
            })
        )
        flow_row["source_packet_sha256"] = flow_row["packet"]["payload_sha256"]
        tampered["packet_sha256"] = self.orchestrator.payload_sha256({
            key: value for key, value in tampered.items()
            if key != "packet_sha256"
        })
        self.assertNotEqual(
            tampered["packet_sha256"], self.source_packet["packet_sha256"]
        )

        # The precise refusal, from the real production validator.
        with self.assertRaisesRegex(
            self.orchestrator.DailyOrchestratorError, "OUTPUT_MISMATCH"
        ):
            self.orchestrator.validate_packet(
                copy.deepcopy(tampered), trusted_repository_root=ROOT
            )

        # ...and both real canonical subprocesses fail closed on it.
        self._install(self.repo, tampered)
        findings, _bound = bv.check_canonical_structure(
            self.repo, self.date, self.SLOT,
            history_context=self.history_context(),
        )
        self.assertEqual(
            self._canonical_codes(findings),
            {"CANONICAL_PACKET_VALIDATION_FAILED", "CANONICAL_CONSUME_FAILED"},
            findings,
        )
        for finding in findings:
            if finding["code"].startswith("CANONICAL_"):
                self.assertEqual(finding["severity"], "STRUCTURAL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
