#!/usr/bin/env python3
"""Finalization Gate spliced into the real H-24 chain.

The fixture reproduces the layout the live repo produces --
`evidence/daily_briefing/{slot}/{date}/index.json` + `rev-001/{packet.json,briefing.md}`
and `data/briefing/daily_briefing_sources.json` with the locator's real key set.

`FreshRunner` models the failure mode that actually matters on GitHub Actions:
a NEW runner with a NEW checkout, which sees only what was pushed.  Anything
left on the dead runner's disk is gone.

This is a faithful simulation of the chain, NOT a GitHub Actions run.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts"))
_spec = importlib.util.spec_from_file_location("bf", ROOT / ".github/scripts/briefing_finalization.py")
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)
import atlas_ed25519 as ed25519  # noqa: E402

SLOT, DATE = "evening", "2026-08-27"
BRIEFING_MD = "# Atlas Daily Briefing — 2026-08-27 (evening)\n\nStep 0 = PASS.\n"
CONSUME_MD = "### Investment Decision Review\n\nD3 / C1 / R1 / B0 — no change.\n"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class CountingUnsafeAdapter(bf.Adapter):
    """Stands in for a real human-reaching channel: unprobeable, unsafe to resend."""
    name = "test_unsafe"
    resend_safe = False
    payload_fidelity = "FULL"

    def __init__(self):
        self.sent = []

    def send(self, payload: bytes, meta: dict) -> bf.DeliveryProof:
        self.sent.append(payload)
        return bf.DeliveryProof(channel=self.name, transport_id=f"send-{len(self.sent)}",
                                sent_at_utc="2026-08-27T09:00:00Z", payload_fidelity="FULL",
                                transmitted_sha256=sha(payload), transmitted_bytes=len(payload),
                                covers_full_payload=True)


class Base(unittest.TestCase):
    def setUp(self):
        # Keep the calendar/debt assertions bound to the fixture's operating
        # day.  Using wall-clock "now" made this accepted suite begin owing the
        # following morning slot as soon as KST crossed 2026-08-28 07:05.
        self._real_utcnow = bf._utcnow
        bf._utcnow = lambda: dt.datetime(2026, 8, 27, 11, 0,
                                         tzinfo=dt.timezone.utc)
        self.repo = Path(tempfile.mkdtemp())
        self.remote = Path(tempfile.mkdtemp())          # what has been pushed
        self.summary = self.repo / "step_summary.md"
        self.summary.write_text("", encoding="utf-8")
        os.environ["GITHUB_STEP_SUMMARY"] = str(self.summary)
        os.environ["GITHUB_RUN_ID"] = "33051344125"
        self.unsafe = CountingUnsafeAdapter()
        bf.ADAPTERS[self.unsafe.name] = self.unsafe
        self.sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        # The out-of-band anchor is mandatory for approvals; set it by default so
        # signature tests exercise signature logic, not the anchor check.
        os.environ[bf.APPROVAL_FINGERPRINT_ENV] = bf.pubkey_fingerprint(ed25519.publickey(self.sk))
        self._build_bundle()

    def tearDown(self):
        bf._utcnow = self._real_utcnow
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.remote, ignore_errors=True)
        bf.ADAPTERS.pop(self.unsafe.name, None)
        os.environ.pop("GITHUB_STEP_SUMMARY", None)

    # ---- durability model -------------------------------------------------
    def push(self, *rel_paths: str):
        for rel in rel_paths:
            src, dst = self.repo / rel, self.remote / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

    def durable(self, repo_root: Path, path: Path) -> bool:
        rel = path.relative_to(repo_root)
        mirror = self.remote / rel
        return mirror.exists() and mirror.read_bytes() == path.read_bytes()

    def fresh_runner(self) -> Path:
        """A new checkout: only pushed state exists."""
        fresh = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, fresh, ignore_errors=True)
        for item in self.remote.rglob("*"):
            if item.is_file():
                dst = fresh / item.relative_to(self.remote)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dst)
        return fresh

    # ---- fixture ----------------------------------------------------------
    def _build_bundle(self, briefing_md=BRIEFING_MD):
        rev_dir = self.repo / "evidence/daily_briefing" / SLOT / DATE / "rev-001"
        rev_dir.mkdir(parents=True, exist_ok=True)
        packet = {"decision_date": DATE, "slot": SLOT, "components": []}
        packet["packet_sha256"] = sha(json.dumps(packet, sort_keys=True, separators=(",", ":")).encode())
        packet_bytes = json.dumps(packet, sort_keys=True, indent=2).encode()
        (rev_dir / "packet.json").write_bytes(packet_bytes)
        briefing_bytes = briefing_md.encode()
        (rev_dir / "briefing.md").write_bytes(briefing_bytes)
        index = {"decision_date": DATE, "latest_revision": 1, "schema_version": 1, "slot": SLOT,
                 "revisions": [{"generated_at": "2026-08-27T09:30:00Z", "path": "rev-001",
                                "revision": 1, "packet_sha256": packet["packet_sha256"],
                                "component_status_counts": {}}]}
        index_bytes = json.dumps(index, sort_keys=True, indent=2).encode()
        (rev_dir.parent / "index.json").write_bytes(index_bytes)
        locator = {"schema_version": "daily_briefing_delivery/1", "slot": SLOT, "decision_date": DATE,
                   "revision": 1,
                   "index_path": f"evidence/daily_briefing/{SLOT}/{DATE}/index.json",
                   "index_sha256": sha(index_bytes),
                   "packet_path": f"evidence/daily_briefing/{SLOT}/{DATE}/rev-001/packet.json",
                   "packet_file_sha256": sha(packet_bytes), "packet_sha256": packet["packet_sha256"],
                   "briefing_path": f"evidence/daily_briefing/{SLOT}/{DATE}/rev-001/briefing.md",
                   "briefing_sha256": sha(briefing_bytes),
                   "delivery_scope": "INVESTMENT_DECISION_REVIEW", "authority": {"trading_authority": False}}
        loc = self.repo / bf.LOCATOR_PATH
        loc.parent.mkdir(parents=True, exist_ok=True)
        loc.write_text(json.dumps(locator, sort_keys=True, indent=2), encoding="utf-8")
        pk = self.repo / bf.APPROVAL_PUBKEY_PATH
        pk.parent.mkdir(parents=True, exist_ok=True)
        pk.write_text(ed25519.publickey(self.sk).hex(), encoding="utf-8")
        self.consume = self.repo / "consume.md"
        self.consume.write_text(CONSUME_MD, encoding="utf-8")

    # ---- helpers ----------------------------------------------------------
    def _seal(self, repo=None):
        return bf.seal(repo or self.repo, DATE, SLOT, self.consume)

    def _payload_sha(self, repo=None):
        d = bf._latest(bf.slot_dir(repo or self.repo, DATE, SLOT), "draft")
        return json.loads(d.read_text())["delivery_payload_sha256"]

    def _validate(self, corrections=(), status=None, payload_sha="AUTO", repo=None, internal=False):
        repo = repo or self.repo
        if payload_sha == "AUTO":
            payload_sha = self._payload_sha(repo)
        return bf.record_validation(repo, DATE, SLOT, {
            "delivery_payload_sha256": payload_sha,
            "validation_status": status or ("PASS_WITH_CORRECTION" if corrections else "PASS"),
            "validator_id": "chatgpt-validator", "validated_at_utc": "2026-08-27T09:40:00Z",
            "corrections": list(corrections),
            "conclusion_diff": {"spec_version": None, "investment_conclusion_changed": False,
                                "money_action_changed": False, "stage_changed": False}}, internal=internal)

    def _deliver(self, channels=("github_step_summary",), required=None, repo=None, now=None):
        return bf.deliver(repo or self.repo, DATE, SLOT, list(channels), required=required,
                          now=now, durability_probe=self.durable)

    def _slot_rel(self, name):
        return f"{bf.FINALIZATION_ROOT}/{DATE}/{SLOT}/{name}"

    CORR = [{"id": "COR-01", "class": "FACT", "field_path": "step0",
             "before": "2026-08-19", "after": "2026-08-27", "source": "step0_status.json"}]


# ============================ P0-1 durable intent ==========================
class DurableIntent(Base):
    def test_transport_refused_when_intent_is_not_pushed(self):
        self._seal(); self._validate()
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver(channels=["test_unsafe"])
        self.assertEqual(ctx.exception.code, "FINALIZATION_INTENT_NOT_DURABLE")
        self.assertEqual(ctx.exception.exit_code, bf.EXIT_INTENT_NOT_DURABLE)
        self.assertEqual(len(self.unsafe.sent), 0)      # nothing was sent

    def test_publisher_runs_in_process_so_first_attempt_is_not_ambiguous(self):
        """intent write -> publish -> send must be one process (see deliver docstring)."""
        self._seal(); self._validate()
        published = []

        def publisher(repo_root, path):
            published.append(path)
            self.push(str(path.relative_to(repo_root)))

        r = bf.deliver(self.repo, DATE, SLOT, ["test_unsafe"], durability_probe=self.durable,
                       intent_publisher=publisher)
        self.assertEqual(len(published), 1)
        self.assertEqual(len(self.unsafe.sent), 1)
        self.assertEqual(r["channels"], ["test_unsafe"])

    def test_publish_failure_blocks_the_send(self):
        self._seal(); self._validate()

        def failing_publisher(repo_root, path):
            raise RuntimeError("push rejected: non-fast-forward")

        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["test_unsafe"], durability_probe=self.durable,
                       intent_publisher=failing_publisher)
        self.assertEqual(ctx.exception.code, "FINALIZATION_INTENT_PUBLISH_FAILED")
        self.assertEqual(len(self.unsafe.sent), 0)

    def test_fresh_runner_after_crash_does_not_double_send(self):
        """The rev3 regression the CIO reproduced: two real messages."""
        draft = self._seal(); self._validate()
        # everything the producer/validator wrote is pushed
        self.push(bf.LOCATOR_PATH, bf.APPROVAL_PUBKEY_PATH,
                  self._slot_rel(f"draft-rev-{draft['rev']:03d}.json"),
                  self._slot_rel(f"payload-rev-{draft['rev']:03d}.md"),
                  self._slot_rel("validation-rev-001.json"))
        # attempt 1: intent is written AND pushed, then transport succeeds,
        # then the runner dies before progress/receipt are pushed.
        bf.write_intent(self.repo, DATE, SLOT, draft, ["test_unsafe"], 1, bf._utcnow())
        self.push(self._slot_rel("delivery_intent.json"))
        self.assertTrue(self.durable(self.repo, bf.intent_path(self.repo, DATE, SLOT)))
        self.unsafe.send(b"payload", {"slot": SLOT, "kst_date": DATE})   # reached a human
        self.assertEqual(len(self.unsafe.sent), 1)

        # attempt 2 on a brand-new runner: only pushed state exists
        fresh = self.fresh_runner()
        self.assertTrue(bf.intent_path(fresh, DATE, SLOT).exists())
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(fresh, DATE, SLOT, ["test_unsafe"], durability_probe=self.durable)
        self.assertEqual(ctx.exception.code, "FINALIZATION_RECONCILE_PENDING")
        self.assertEqual(len(self.unsafe.sent), 1)      # still exactly one message

    def test_resend_safe_channel_still_recovers_on_fresh_runner(self):
        draft = self._seal(); self._validate()
        self.push(bf.LOCATOR_PATH, bf.APPROVAL_PUBKEY_PATH,
                  self._slot_rel(f"draft-rev-{draft['rev']:03d}.json"),
                  self._slot_rel(f"payload-rev-{draft['rev']:03d}.md"),
                  self._slot_rel("validation-rev-001.json"))
        bf.write_intent(self.repo, DATE, SLOT, draft, ["github_step_summary"], 1, bf._utcnow())
        self.push(self._slot_rel("delivery_intent.json"))
        fresh = self.fresh_runner()
        os.environ["GITHUB_STEP_SUMMARY"] = str(fresh / "summary.md")   # new runner, empty log
        r = bf.deliver(fresh, DATE, SLOT, ["github_step_summary"], durability_probe=self.durable)
        self.assertEqual(r["channels"], ["github_step_summary"])


# ====================== P0-2 asymmetric approval ===========================
class Approval(Base):
    def _approve(self, payload_sha, validation_rev, by="CIO", sk=None, decision="APPROVE"):
        message = bf.approval_message(f"{DATE}-pm", payload_sha, validation_rev, by,
                                      decision, bf.CONTRACT_VERSION)
        (bf.slot_dir(self.repo, DATE, SLOT) / "approval-rev-001.json").write_text(json.dumps({
            "contract_version": bf.CONTRACT_VERSION,
            "decision": decision, "approved_by": by,
            "signature": ed25519.sign(message, sk or self.sk).hex(),
            "approves_payload_sha256": payload_sha,
            "approves_validation_rev": validation_rev}), encoding="utf-8")

    def test_deny_cannot_be_edited_into_approve(self):
        """rev4 signed everything except `decision`; a DENY flipped to APPROVE verified."""
        draft = self._seal(); v = self._validate(self.CORR)
        self._approve(draft["delivery_payload_sha256"], v["rev"], decision="DENY")
        path = bf.slot_dir(self.repo, DATE, SLOT) / "approval-rev-001.json"
        tampered = json.loads(path.read_text())
        tampered["decision"] = "APPROVE"           # signature left untouched
        path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_APPROVAL_SIGNATURE_INVALID")
        self.assertFalse(bf.already_delivered(self.repo, DATE, SLOT))

    def test_swapped_pubkey_is_caught_by_the_out_of_band_anchor(self):
        """A repo writer can edit the key file; they cannot edit a repo secret."""
        attacker = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
        draft = self._seal(); v = self._validate(self.CORR)
        (self.repo / bf.APPROVAL_PUBKEY_PATH).write_text(ed25519.publickey(attacker).hex())
        self._approve(draft["delivery_payload_sha256"], v["rev"], sk=attacker)
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_APPROVAL_PUBKEY_UNTRUSTED")

    def test_approval_without_an_anchor_is_refused(self):
        """rev6 allowed an unanchored key and merely logged it. With main
        unprotected the anchor is the only line, so absence is fail-closed."""
        draft = self._seal(); v = self._validate(self.CORR)
        self._approve(draft["delivery_payload_sha256"], v["rev"])
        os.environ.pop(bf.APPROVAL_FINGERPRINT_ENV, None)
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_APPROVAL_ANCHOR_MISSING")
        self.assertFalse(bf.already_delivered(self.repo, DATE, SLOT))

    def test_clean_pass_needs_no_anchor(self):
        """The anchor gates APPROVALS, not every delivery."""
        os.environ.pop(bf.APPROVAL_FINGERPRINT_ENV, None)
        self._seal(); self._validate()
        r = bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"],
                       durability_probe=lambda *_: True)
        self.assertEqual(r["channels"], ["github_step_summary"])

    def test_repo_has_only_the_public_key(self):
        text = (self.repo / bf.APPROVAL_PUBKEY_PATH).read_text().strip()
        self.assertEqual(len(bytes.fromhex(text)), 32)
        self.assertNotEqual(text, self.sk.hex())

    def test_valid_signature_unblocks(self):
        draft = self._seal(); v = self._validate(self.CORR)
        self._approve(draft["delivery_payload_sha256"], v["rev"])
        self.push(*[self._slot_rel(p) for p in ("delivery_intent.json",)])
        r = bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"],
                       durability_probe=lambda *_: True)
        self.assertEqual(r["cio_approved_by"], "CIO")

    def test_signature_from_another_key_is_refused(self):
        draft = self._seal(); v = self._validate(self.CORR)
        other = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
        self._approve(draft["delivery_payload_sha256"], v["rev"], sk=other)
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_APPROVAL_SIGNATURE_INVALID")

    def test_unsigned_approval_is_refused(self):
        draft = self._seal(); v = self._validate(self.CORR)
        (bf.slot_dir(self.repo, DATE, SLOT) / "approval-rev-001.json").write_text(json.dumps({
            "decision": "APPROVE", "approved_by": "CIO",
            "approves_payload_sha256": draft["delivery_payload_sha256"],
            "approves_validation_rev": v["rev"]}), encoding="utf-8")
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_APPROVAL_SIGNATURE_INVALID")

    def test_signature_does_not_transfer_to_another_payload(self):
        draft = self._seal(); v = self._validate(self.CORR)
        self._approve(draft["delivery_payload_sha256"], v["rev"])
        self._build_bundle(BRIEFING_MD + "\nrevised\n")
        self._seal()
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertIn(ctx.exception.code, ("FINALIZATION_VALIDATION_STALE",
                                           "FINALIZATION_APPROVAL_PAYLOAD_MISMATCH"))

    def test_missing_public_key_fails_closed(self):
        draft = self._seal(); v = self._validate(self.CORR)
        self._approve(draft["delivery_payload_sha256"], v["rev"])
        (self.repo / bf.APPROVAL_PUBKEY_PATH).unlink()
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_APPROVAL_PUBKEY_MISSING")


# ================== P0-3 HOLD / P0-4 timeout state machine =================
class StateMachine(Base):
    def test_hold_is_never_delivered(self):
        self._seal(); self._validate(status="HOLD")
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_HELD")
        self.assertEqual(ctx.exception.exit_code, bf.EXIT_HELD)
        self.assertFalse(bf.already_delivered(self.repo, DATE, SLOT))
        self.assertEqual(self.summary.read_text(), "")

    def test_external_validator_cannot_assert_timeout(self):
        self._seal()
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._validate(status="UNVALIDATED_TIMEOUT")
        self.assertEqual(ctx.exception.code, "FINALIZATION_STATUS_NOT_EXTERNALLY_SUBMITTABLE")

    def test_inbox_cannot_smuggle_timeout(self):
        self._seal()
        inbox = bf.slot_dir(self.repo, DATE, SLOT) / "validation-inbox.json"
        inbox.write_text(json.dumps({
            "validation_status": "UNVALIDATED_TIMEOUT", "validator_id": "attacker",
            "corrections": [], "conclusion_diff": {"spec_version": None},
            "delivery_payload_sha256": self._payload_sha()}), encoding="utf-8")
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertEqual(ctx.exception.code, "FINALIZATION_STATUS_NOT_EXTERNALLY_SUBMITTABLE")

    def test_timeout_never_becomes_delivery_authority(self):
        self._seal()
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_PENDING")
        later = bf._utcnow() + dt.timedelta(minutes=bf.VALIDATION_TIMEOUT_MIN + 1)
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"], now=later,
                       durability_probe=lambda *_: True)
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_TIMEOUT")
        self.assertFalse(bf.already_delivered(self.repo, DATE, SLOT))


# ========================= P0-5 idempotent seal ============================
class IdempotentSeal(Base):
    def test_reseal_of_identical_input_reuses_the_draft(self):
        first = self._seal()
        second = self._seal()
        self.assertTrue(second["reused"])
        self.assertEqual(first["rev"], second["rev"])
        self.assertEqual(first["delivery_payload_sha256"], second["delivery_payload_sha256"])
        self.assertEqual(first["sealed_at_utc"], second["sealed_at_utc"])
        self.assertEqual(first["delivery_marker"], second["delivery_marker"])

    def test_reseal_does_not_invalidate_an_existing_verdict(self):
        self._seal(); self._validate()
        self._seal()                                    # retry of the same run
        r = bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"],
                       durability_probe=lambda *_: True)
        self.assertEqual(r["validation_status_at_delivery"], "PASS")

    def test_changed_source_does_create_a_new_draft(self):
        first = self._seal()
        self._build_bundle(BRIEFING_MD + "\nrevised\n")
        second = self._seal()
        self.assertFalse(second["reused"])
        self.assertEqual(second["rev"], first["rev"] + 1)


# ================= P0-6 drain / P0-7 missed slot recovery ==================
class Recovery(Base):
    def setUp(self):
        super().setUp()
        path = self.repo / bf.ACTIVATION_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"active_from_kst_date": DATE,
                                    "active_from_slot": "morning"}), encoding="utf-8")

    def test_drain_ingests_the_inbox_before_delivering(self):
        self._seal()
        inbox = bf.slot_dir(self.repo, DATE, SLOT) / "validation-inbox.json"
        inbox.write_text(json.dumps({
            "validation_status": "PASS", "validator_id": "chatgpt-validator",
            "corrections": [], "conclusion_diff": {"spec_version": None},
            "delivery_payload_sha256": self._payload_sha()}), encoding="utf-8")
        now = bf._utcnow()
        out = bf.drain(self.repo, ["github_step_summary"], now=now,
                       durability_probe=lambda *_: True)
        entry = next(e for e in out["drained"] if e["briefing_id"] == f"{DATE}-pm")
        self.assertTrue(entry["ingest"]["ingested"])
        # the verdict was used -- NOT a timeout delivery
        self.assertEqual(entry["receipt"]["validation_status_at_delivery"], "PASS")

    def test_backlog_reports_slots_that_never_produced_anything(self):
        state = bf.backlog(self.repo, now=bf._utcnow())
        ids = [m["briefing_id"] for m in state["missing_production"]]
        self.assertIn(f"{DATE}-pm", ids)
        entry = next(m for m in state["missing_production"] if m["briefing_id"] == f"{DATE}-pm")
        self.assertEqual(entry["action"], "RUN_PRODUCER")
        self.assertEqual(
            entry["calendar_confidence"],
            "DAILY_MORNING_WEEKDAY_EVENING_HOLIDAYS_UNKNOWN",
        )

    def test_weekend_morning_is_owed_but_weekend_evening_is_not(self):
        sunday_after_evening = dt.datetime(2026, 8, 30, 11, 0,
                                           tzinfo=dt.timezone.utc)  # 20:00 KST
        slots = bf.expected_slots(sunday_after_evening, activation={
            "active_from_kst_date": "2026-08-30",
            "active_from_slot": "morning",
        })
        ids = {(item["kst_date"], item["slot"]) for item in slots}
        self.assertIn(("2026-08-30", "morning"), ids)
        self.assertNotIn(("2026-08-30", "evening"), ids)

    def test_sealed_slot_moves_from_missing_to_pending(self):
        self._seal()
        state = bf.backlog(self.repo, now=bf._utcnow())
        self.assertNotIn(f"{DATE}-pm", [m["briefing_id"] for m in state["missing_production"]])
        self.assertIn(f"{DATE}-pm", [p["briefing_id"] for p in state["pending_delivery"]])

    def test_slot_not_yet_due_is_not_reported_missing(self):
        early = dt.datetime(2026, 8, 27, 0, 0, tzinfo=dt.timezone.utc)   # 09:00 KST
        ids = [m["briefing_id"] for m in bf.backlog(self.repo, now=early)["missing_production"]]
        self.assertNotIn("2026-08-27-pm", ids)          # evening not due at 09:00 KST
        self.assertIn("2026-08-27-am", ids)             # morning was due at 07:05


# ============ P0-9 transmitted bytes / P0-10 required channels =============
class ChannelSemantics(Base):
    def test_kakao_records_what_it_actually_transmitted(self):
        draft = self._seal()
        payload = (bf.slot_dir(self.repo, DATE, SLOT) / f"payload-rev-{draft['rev']:03d}.md").read_bytes()
        meta = {"slot": SLOT, "kst_date": DATE, "delivery_marker": draft["delivery_marker"]}
        text = bf.ADAPTERS["kakao"].build_message(payload, meta)
        self.assertTrue(text.startswith(draft["delivery_marker"]))   # marker survives truncation
        self.assertLessEqual(len(text), bf.ADAPTERS["kakao"].TEXT_LIMIT)
        self.assertEqual(bf.ADAPTERS["kakao"].payload_fidelity, "SUMMARY")

    def test_long_payload_keeps_the_marker(self):
        self._build_bundle("# long\n\n" + ("x" * 5000) + "\n")
        draft = self._seal()
        payload = (bf.slot_dir(self.repo, DATE, SLOT) / f"payload-rev-{draft['rev']:03d}.md").read_bytes()
        meta = {"slot": SLOT, "kst_date": DATE, "delivery_marker": draft["delivery_marker"]}
        text = bf.ADAPTERS["kakao"].build_message(payload, meta)
        self.assertIn(draft["delivery_marker"], text)

    def test_partial_success_writes_no_receipt_when_a_required_channel_fails(self):
        self._seal(); self._validate()
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary", "kakao"],
                       required=["kakao"], durability_probe=lambda *_: True)
        self.assertEqual(ctx.exception.code, "DELIVERY_REQUIRED_CHANNEL_INCOMPLETE")
        self.assertFalse(bf.already_delivered(self.repo, DATE, SLOT))

    def test_retry_only_attempts_the_missing_channel(self):
        self._seal(); self._validate()
        try:
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary", "test_unsafe"],
                       required=["github_step_summary", "test_unsafe"],
                       durability_probe=lambda *_: True)
        except bf.FinalizationError:
            pass
        progress = json.loads(bf.progress_path(self.repo, DATE, SLOT).read_text())
        self.assertIn("github_step_summary", progress["channels"])
        self.assertIn("test_unsafe", progress["channels"])
        self.assertEqual(len(self.unsafe.sent), 1)

    def test_receipt_separates_full_from_summary_channels(self):
        self._seal(); self._validate()
        r = bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"],
                       durability_probe=lambda *_: True)
        self.assertEqual(r["full_payload_channels"], ["github_step_summary"])
        self.assertEqual(r["sealed_payload_sha256"], self._payload_sha())
        proof = r["delivery_proofs"][0]
        self.assertNotEqual(proof["transmitted_sha256"], r["sealed_payload_sha256"])  # header added
        self.assertTrue(proof["covers_full_payload"])


# ============================ locator + ledger =============================
class ChainAndLedger(Base):
    def test_seal_verifies_locator_hashes(self):
        (self.repo / "evidence/daily_briefing" / SLOT / DATE / "rev-001/briefing.md").write_text("t")
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._seal()
        self.assertEqual(ctx.exception.code, "FINALIZATION_BRIEFING_SHA_MISMATCH")

    def test_seal_rejects_slot_mismatch(self):
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.seal(self.repo, DATE, "morning", self.consume)
        self.assertEqual(ctx.exception.code, "FINALIZATION_LOCATOR_IDENTITY_MISMATCH")

    def test_seal_rejects_resigned_dynamic_clock_identity_aliases(self):
        packet_path = (
            self.repo / "evidence/daily_briefing" / SLOT / DATE / "rev-001/packet.json"
        )
        locator_path = self.repo / bf.LOCATOR_PATH
        packet = json.loads(packet_path.read_text())
        report = {
            "decision_date": DATE,
            "mode": "OPERATIONAL",
            "by_market": {"BTC": {"raw_trigger_count": 0}},
        }
        packet["frozen_sources"] = {
            "DYNAMIC_CLOCK": {
                "kind": "report",
                "report_sha256": sha(bf._canonical(report)),
                "report": report,
            }
        }

        for field, replacement, code in (
            ("report_sha256", True, "FINALIZATION_DYNAMIC_CLOCK_SOURCE_INVALID"),
            (
                "report_sha256",
                "0" * 64,
                "FINALIZATION_DYNAMIC_CLOCK_SOURCE_SHA_MISMATCH",
            ),
            (
                "report.decision_date",
                "2026-08-26",
                "FINALIZATION_DYNAMIC_CLOCK_SOURCE_DATE_MISMATCH",
            ),
        ):
            with self.subTest(field=field, replacement=replacement):
                tampered = json.loads(json.dumps(packet))
                if field == "report.decision_date":
                    tampered["frozen_sources"]["DYNAMIC_CLOCK"]["report"][
                        "decision_date"
                    ] = replacement
                    tampered["frozen_sources"]["DYNAMIC_CLOCK"]["report_sha256"] = sha(
                        bf._canonical(tampered["frozen_sources"]["DYNAMIC_CLOCK"]["report"])
                    )
                else:
                    tampered["frozen_sources"]["DYNAMIC_CLOCK"][field] = replacement
                packet_bytes = json.dumps(tampered, sort_keys=True, indent=2).encode()
                packet_path.write_bytes(packet_bytes)
                locator = json.loads(locator_path.read_text())
                locator["packet_file_sha256"] = sha(packet_bytes)
                locator_path.write_text(json.dumps(locator, sort_keys=True, indent=2))
                with self.assertRaises(bf.FinalizationError) as ctx:
                    self._seal()
                self.assertEqual(ctx.exception.code, code)

        packet_bytes = json.dumps(packet, sort_keys=True, indent=2).encode()
        packet_path.write_bytes(packet_bytes)
        locator = json.loads(locator_path.read_text())
        locator["packet_file_sha256"] = sha(packet_bytes)
        locator_path.write_text(json.dumps(locator, sort_keys=True, indent=2))
        self.assertEqual(self._seal()["source"]["packet_path"], locator["packet_path"])

    def test_validation_must_name_its_payload(self):
        self._seal()
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._validate(payload_sha=None)
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_PAYLOAD_UNBOUND")

    def test_second_delivery_is_blocked(self):
        self._seal(); self._validate()
        bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"], durability_probe=lambda *_: True)
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertEqual(ctx.exception.code, "FINALIZATION_ALREADY_DELIVERED")

    def test_notice_surfaces_once_then_stops(self):
        self._seal(); self._validate()
        bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"], durability_probe=lambda *_: True)
        c = {"class": "DATE", "field_path": "pce", "before": "a", "after": "b", "source": "s"}
        bf.append_correction(self.repo, DATE, SLOT, c, portal_synced=True)
        bf.append_correction(self.repo, DATE, SLOT, c, portal_synced=False)
        first = bf.correction_notice(self.repo, DATE, SLOT, mark_surfaced=True)
        self.assertIn("정정 2건", first)
        self.assertIn("Portal 1/2 반영", first)
        self.assertIsNone(bf.correction_notice(self.repo, DATE, SLOT))   # not repeated
        bf.append_correction(self.repo, DATE, SLOT, c, portal_synced=True)
        self.assertIn("정정 1건", bf.correction_notice(self.repo, DATE, SLOT))

    def test_validator_cannot_grant_itself_auto_apply(self):
        self._seal()
        v = self._validate(self.CORR)
        self.assertFalse(v["routing"]["auto_apply_allowed"])
        self.assertEqual(v["routing"]["investment_conclusion_changed"], "UNKNOWN")
        self.assertTrue(v["routing"]["cio_gate_required"])

    def test_invented_spec_version_grants_nothing(self):
        """rev4 trusted any non-null spec_version -- 'evil/999' opened the gate."""
        self._seal()
        v = bf.record_validation(self.repo, DATE, SLOT, {
            "delivery_payload_sha256": self._payload_sha(),
            "validation_status": "PASS_WITH_CORRECTION", "validator_id": "attacker",
            "validated_at_utc": "2026-08-27T09:40:00Z", "corrections": list(self.CORR),
            "conclusion_diff": {"spec_version": "evil/999",
                                "investment_conclusion_changed": False,
                                "money_action_changed": False, "stage_changed": False}})
        self.assertFalse(v["routing"]["auto_apply_allowed"])
        self.assertTrue(v["routing"]["cio_gate_required"])
        self.assertFalse(v["routing"]["spec_version_ratified"])
        self.assertEqual(v["routing"]["investment_conclusion_changed"], "UNKNOWN")
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_CIO_APPROVAL_REQUIRED")

    def test_even_a_ratified_spec_is_inert_while_phase_a_holds(self):
        allow = self.repo / bf.CONCLUSION_SPEC_ALLOWLIST_PATH
        allow.parent.mkdir(parents=True, exist_ok=True)
        allow.write_text(json.dumps({"ratified_spec_versions": ["conclusion_diff/1"]}))
        self._seal()
        v = bf.record_validation(self.repo, DATE, SLOT, {
            "delivery_payload_sha256": self._payload_sha(),
            "validation_status": "PASS_WITH_CORRECTION", "validator_id": "v",
            "validated_at_utc": "2026-08-27T09:40:00Z", "corrections": list(self.CORR),
            "conclusion_diff": {"spec_version": "conclusion_diff/1",
                                "investment_conclusion_changed": False,
                                "money_action_changed": False, "stage_changed": False}})
        self.assertTrue(v["routing"]["spec_version_ratified"])
        self.assertFalse(v["routing"]["auto_apply_allowed"])   # Phase A still holds it shut
        self.assertTrue(v["routing"]["phase_a_auto_apply_disabled"])


class StateBinding(Base):
    def test_progress_does_not_carry_across_a_reseal(self):
        """rev4 reused draft-1 proofs for draft-2 and completed a receipt for
        bytes that channel never received."""
        self._seal(); self._validate()
        # kakao is required but has no transport here, so no receipt is written
        # and the slot stays undelivered -- reseal is still a normal reseal.
        try:
            bf.deliver(self.repo, DATE, SLOT, ["test_unsafe", "kakao"],
                       required=["kakao"], durability_probe=lambda *_: True)
        except bf.FinalizationError:
            pass
        self.assertEqual(len(self.unsafe.sent), 1)
        self.assertFalse(bf.already_delivered(self.repo, DATE, SLOT))
        self._build_bundle(BRIEFING_MD + "\nrevised\n")
        draft2 = self._seal()
        progress = bf._load_progress(self.repo, DATE, SLOT, draft2["delivery_payload_sha256"])
        self.assertEqual(progress["channels"], {})              # nothing inherited
        self.assertIn("superseded_from", progress)

    def test_open_intent_for_other_bytes_blocks_delivery(self):
        draft = self._seal(); self._validate()
        bf.write_intent(self.repo, DATE, SLOT, draft, ["test_unsafe"], 1, bf._utcnow())
        self._build_bundle(BRIEFING_MD + "\nrevised\n")
        self._seal(); self._validate()
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["test_unsafe"], durability_probe=lambda *_: True)
        self.assertEqual(ctx.exception.code, "FINALIZATION_INTENT_PAYLOAD_MISMATCH")
        self.assertEqual(len(self.unsafe.sent), 0)

    def test_verdicts_differing_only_in_corrections_are_both_ingested(self):
        """rev4 deduped on (payload, status) and discarded the second verdict."""
        self._seal()
        directory = bf.slot_dir(self.repo, DATE, SLOT)
        base = {"validation_status": "PASS_WITH_CORRECTION", "validator_id": "v",
                "conclusion_diff": {"spec_version": None},
                "delivery_payload_sha256": self._payload_sha()}
        (directory / "validation-inbox-rev-001.json").write_text(
            json.dumps({**base, "corrections": list(self.CORR)}), encoding="utf-8")
        first = bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertTrue(first["ingested"])
        other = [{**self.CORR[0], "id": "COR-02", "field_path": "tsm_price"}]
        (directory / "validation-inbox-rev-002.json").write_text(
            json.dumps({**base, "corrections": other}), encoding="utf-8")
        second = bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertTrue(second["ingested"])
        self.assertEqual(second["validation"]["corrections"][0]["id"], "COR-02")

    def test_identical_verdict_is_still_deduped(self):
        self._seal()
        directory = bf.slot_dir(self.repo, DATE, SLOT)
        verdict = {"validation_status": "PASS", "validator_id": "v", "corrections": [],
                   "conclusion_diff": {"spec_version": None},
                   "delivery_payload_sha256": self._payload_sha()}
        (directory / "validation-inbox-rev-001.json").write_text(json.dumps(verdict), encoding="utf-8")
        self.assertTrue(bf.ingest_inbox(self.repo, DATE, SLOT)["ingested"])
        self.assertFalse(bf.ingest_inbox(self.repo, DATE, SLOT)["ingested"])


class ActivationEpoch(Base):
    def _activate(self, date="2026-08-01", slot="morning"):
        path = self.repo / bf.ACTIVATION_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"active_from_kst_date": date, "active_from_slot": slot}),
                        encoding="utf-8")

    def test_unactivated_repo_owes_nothing(self):
        """Merging the feature must not turn the build red on day one."""
        state = bf.backlog(self.repo, now=bf._utcnow())
        self.assertFalse(state["activated"])
        self.assertEqual(state["missing_production"], [])
        self.assertEqual(state["pending_delivery"], [])
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertTrue(out["complete"])
        self.assertEqual(out["exit_code"], bf.EXIT_OK)

    def test_slots_before_the_epoch_are_not_debt(self):
        self._activate(date=DATE, slot="evening")
        state = bf.backlog(self.repo, now=bf._utcnow())
        ids = [m["briefing_id"] for m in state["missing_production"]]
        self.assertIn(f"{DATE}-pm", ids)
        self.assertNotIn(f"{DATE}-am", ids)          # earlier slot, same day
        self.assertFalse(any(i.startswith("2026-08-26") for i in ids))

    def test_after_activation_a_due_slot_is_debt(self):
        self._activate(date=DATE, slot="morning")
        state = bf.backlog(self.repo, now=bf._utcnow())
        self.assertTrue(state["activated"])
        self.assertIn(f"{DATE}-pm", [m["briefing_id"] for m in state["missing_production"]])


class InboxOrdering(Base):
    def _write(self, name, status, corrections=()):
        (bf.slot_dir(self.repo, DATE, SLOT) / name).write_text(json.dumps({
            "validation_status": status, "validator_id": "v", "corrections": list(corrections),
            "conclusion_diff": {"spec_version": None},
            "delivery_payload_sha256": self._payload_sha()}), encoding="utf-8")

    def _write_bad(self, name):
        (bf.slot_dir(self.repo, DATE, SLOT) / name).write_text(json.dumps({
            "validation_status": "PASS", "validator_id": "v", "corrections": [],
            "conclusion_diff": {"spec_version": None},
            "delivery_payload_sha256": "0" * 64}), encoding="utf-8")

    def test_legacy_inbox_cannot_walk_back_a_newer_verdict(self):
        self._seal()
        self._write("validation-inbox-rev-001.json", "PASS")
        self._write("validation-inbox.json", "HOLD")
        out = bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertEqual(out["authority_files"]["semantic"], "validation-inbox-rev-001.json")
        self.assertIn("validation-inbox.json", out["superseded_files"])
        self.assertEqual(out["validation"]["validation_status"], "PASS")

    def test_only_the_highest_revision_is_ingested(self):
        self._seal()
        self._write("validation-inbox-rev-001.json", "PASS")
        self._write("validation-inbox-rev-002.json", "HOLD")
        out = bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertEqual(out["authority_files"]["semantic"], "validation-inbox-rev-002.json")
        self.assertEqual(out["superseded_files"], ["validation-inbox-rev-001.json"])
        self.assertEqual(out["validation"]["validation_status"], "HOLD")
        # the superseded PASS was never recorded at all
        recorded = bf._recorded_validations(bf.slot_dir(self.repo, DATE, SLOT))
        self.assertEqual([b["validation_status"] for _r, b in recorded], ["HOLD"])

    def test_good_then_bad_revision_is_fail_closed(self):
        """rev7: rev-001 recorded first, rev-002 raised, and deliver shipped the
        stale PASS because *a* verdict existed."""
        self._seal()
        self._write("validation-inbox-rev-001.json", "PASS")
        bf.ingest_inbox(self.repo, DATE, SLOT)
        self._write_bad("validation-inbox-rev-002.json")
        with self.assertRaises(bf.FinalizationError):
            bf.ingest_inbox(self.repo, DATE, SLOT)
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"],
                       durability_probe=lambda *_: True)
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_INVALID")
        self.assertFalse(bf.already_delivered(self.repo, DATE, SLOT))
        self.assertEqual(self.summary.read_text(), "")

    def test_bad_then_good_revision_recovers(self):
        """rev7: ingest died on rev-001 every time, so rev-002 was unreachable
        and the append-only file could not be deleted."""
        self._seal()
        self._write_bad("validation-inbox-rev-001.json")
        with self.assertRaises(bf.FinalizationError):
            bf.ingest_inbox(self.repo, DATE, SLOT)
        self._write("validation-inbox-rev-002.json", "PASS")
        out = bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertEqual(out["authority_files"]["semantic"], "validation-inbox-rev-002.json")
        r = bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"],
                       durability_probe=lambda *_: True)
        self.assertEqual(r["validation_status_at_delivery"], "PASS")

    def test_legacy_alone_still_works(self):
        self._seal()
        self._write("validation-inbox.json", "PASS")
        out = bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertTrue(out["ingested"])
        self.assertEqual(out["superseded_files"], [])


class InvalidVerdictIsNotSilence(Base):
    def setUp(self):
        super().setUp()
        path = self.repo / bf.ACTIVATION_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"active_from_kst_date": DATE,
                                    "active_from_slot": "morning"}), encoding="utf-8")

    def _bad_inbox(self, payload_sha):
        (bf.slot_dir(self.repo, DATE, SLOT) / "validation-inbox-rev-001.json").write_text(
            json.dumps({"validation_status": "PASS", "validator_id": "v", "corrections": [],
                        "conclusion_diff": {"spec_version": None},
                        "delivery_payload_sha256": payload_sha}), encoding="utf-8")

    def test_stale_payload_sha_does_not_time_out_into_delivery(self):
        """rev6: ingest failed, the error was swallowed, and 21 minutes later
        deliver() minted UNVALIDATED_TIMEOUT and shipped it with exit 0."""
        self._seal()
        self._bad_inbox("0" * 64)
        later = bf._utcnow() + dt.timedelta(minutes=bf.VALIDATION_TIMEOUT_MIN + 1)
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"], now=later,
                       durability_probe=lambda *_: True)
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_INVALID")
        self.assertEqual(ctx.exception.exit_code, bf.EXIT_VALIDATION_INVALID)
        self.assertFalse(bf.already_delivered(self.repo, DATE, SLOT))
        self.assertEqual(self.summary.read_text(), "")

    def test_drain_reports_it_as_a_machine_failure_not_green(self):
        self._seal()
        self._bad_inbox("0" * 64)
        later = bf._utcnow() + dt.timedelta(minutes=bf.VALIDATION_TIMEOUT_MIN + 1)
        out = bf.drain(self.repo, ["github_step_summary"], now=later,
                       durability_probe=lambda *_: True)
        self.assertEqual(out["exit_code"], bf.EXIT_DRAIN_INCOMPLETE)
        self.assertEqual([e["error"] for e in out["machine_failures"]],
                         ["FINALIZATION_VALIDATION_INVALID"])

    def test_true_silence_remains_undelivered_after_timeout(self):
        self._seal()
        later = bf._utcnow() + dt.timedelta(minutes=bf.VALIDATION_TIMEOUT_MIN + 1)
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"], now=later,
                       durability_probe=lambda *_: True)
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_TIMEOUT")
        self.assertFalse(bf.already_delivered(self.repo, DATE, SLOT))


class DebtNeverExpires(Base):
    def setUp(self):
        super().setUp()
        path = self.repo / bf.ACTIVATION_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"active_from_kst_date": "2026-08-01",
                                    "active_from_slot": "morning"}), encoding="utf-8")

    def _old_undelivered(self, date="2026-07-01"):
        directory = self.repo / bf.FINALIZATION_ROOT / date / "evening"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "draft-rev-001.json").write_text(json.dumps({
            "briefing_id": f"{date}-pm", "rev": 1, "sealed_at_utc": "2026-07-01T09:30:00Z",
            "delivery_payload_sha256": "a" * 64, "delivery_marker": "m",
            "source": {"briefing_sha256": "b" * 64, "revision": 1}}), encoding="utf-8")

    def test_old_sealed_but_undelivered_slot_stays_in_the_backlog(self):
        """rev6 let debt age out past the 5-day lookback and reported complete."""
        self._old_undelivered()
        state = bf.backlog(self.repo, now=bf._utcnow())
        ids = [p["briefing_id"] for p in state["pending_delivery"]]
        self.assertIn("2026-07-01-pm", ids)
        entry = next(p for p in state["pending_delivery"] if p["briefing_id"] == "2026-07-01-pm")
        self.assertGreater(entry["age_days"], 5)

    def test_drain_is_not_green_while_old_debt_exists(self):
        self._old_undelivered()
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])
        self.assertEqual(out["exit_code"], bf.EXIT_DRAIN_INCOMPLETE)
        self.assertIn("2026-07-01-pm", [d["briefing_id"] for d in out["pending_delivery_debt"]])

    def test_exact_slot_drain_ignores_unrelated_old_debt(self):
        self._old_undelivered()
        self._seal(); self._validate()
        out = bf.drain(
            self.repo, ["github_step_summary"], durability_probe=lambda *_: True,
            target_date=DATE, target_slot=SLOT,
        )
        self.assertTrue(out["complete"])
        self.assertEqual(out["exit_code"], bf.EXIT_OK)
        self.assertEqual(out["scope"]["briefing_id"], f"{DATE}-pm")
        self.assertEqual([item["briefing_id"] for item in out["drained"]], [f"{DATE}-pm"])
        self.assertEqual(out["pending_delivery_debt"], [])
        self.assertEqual(out["missing_production"], [])

    def test_cli_exact_slot_drain_returns_green_after_delivery(self):
        self._seal(); self._validate()
        rc = bf.main([
            "drain", "--repo-root", str(self.repo), "--slot", SLOT,
            "--decision-date", DATE, "--channel", "github_step_summary",
            "--allow-nondurable-intent",
        ])
        self.assertEqual(rc, bf.EXIT_OK)

    def test_delivered_old_slot_leaves_the_backlog(self):
        self._old_undelivered()
        receipt = self.repo / bf.FINALIZATION_ROOT / "2026-07-01/evening/delivery_receipt.json"
        receipt.write_text(json.dumps({"briefing_id": "2026-07-01-pm"}), encoding="utf-8")
        state = bf.backlog(self.repo, now=bf._utcnow())
        self.assertNotIn("2026-07-01-pm", [p["briefing_id"] for p in state["pending_delivery"]])


class PostDeliveryReseal(Base):
    def setUp(self):
        super().setUp()
        path = self.repo / bf.ACTIVATION_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        # activation starts at THIS slot so the morning slot is not owed and the
        # drain outcome isolates the post-delivery escalation
        path.write_text(json.dumps({"active_from_kst_date": DATE,
                                    "active_from_slot": "evening"}), encoding="utf-8")

    def _deliver_once(self):
        self._seal(); self._validate()
        return bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"],
                          durability_probe=lambda *_: True)

    def test_identical_reseal_after_delivery_is_a_no_op(self):
        self._deliver_once()
        again = self._seal()
        self.assertTrue(again["reused"])
        self.assertNotIn("post_delivery_change", again)

    def test_changed_source_after_delivery_creates_no_deliverable_draft(self):
        """rev7 sealed draft-rev-002 that nobody would ever receive, while
        backlog saw the receipt and called the slot complete."""
        self._deliver_once()
        drafts_before = sorted(bf.slot_dir(self.repo, DATE, SLOT).glob("draft-rev-*.json"))
        self._build_bundle(BRIEFING_MD + "\nsame-day recovery\n")
        out = self._seal()
        self.assertTrue(out["post_delivery_change"])
        self.assertEqual(out["redelivery"], "FORBIDDEN")
        self.assertFalse(out["normal_delivery"])
        drafts_after = sorted(bf.slot_dir(self.repo, DATE, SLOT).glob("draft-rev-*.json"))
        self.assertEqual(drafts_before, drafts_after)      # no new deliverable draft

    def test_change_is_recorded_in_the_audit_paths(self):
        self._deliver_once()
        self._build_bundle(BRIEFING_MD + "\nsame-day recovery\n")
        out = self._seal()
        artifacts = sorted(bf.slot_dir(self.repo, DATE, SLOT)
                           .glob("post-delivery-change-rev-*.json"))
        self.assertEqual(len(artifacts), 1)
        ledger = bf._read_ledger(bf._ledger_path(self.repo, DATE, SLOT))
        self.assertTrue(ledger)
        self.assertTrue(all(e["portal_synced"] is False for e in ledger))  # derived, never asserted
        self.assertEqual(out["capital_impact"], bf.UNKNOWN)

    def test_repeated_seal_of_the_same_change_is_idempotent(self):
        """rev8 stacked post-delivery-change-rev-001 and -002 for one observation."""
        self._deliver_once()
        self._build_bundle(BRIEFING_MD + "\nsame-day recovery\n")
        first = self._seal()
        second = self._seal()
        self.assertTrue(second["reused"])
        self.assertEqual(first["post_delivery_change_key"], second["post_delivery_change_key"])
        self.assertEqual(len(sorted(bf.slot_dir(self.repo, DATE, SLOT)
                                    .glob("post-delivery-change-rev-*.json"))), 1)
        ledger = bf._read_ledger(bf._ledger_path(self.repo, DATE, SLOT))
        self.assertEqual(len(ledger), len(first["changed_axes"]))   # no duplicate rows

    def test_consume_only_change_does_not_claim_a_revision_change(self):
        """rev8 logged `SOURCE_REVISION: 1 -> 1` when only the consume text moved."""
        self._deliver_once()
        self.consume.write_text(CONSUME_MD + "\naddendum\n", encoding="utf-8")
        out = self._seal()
        axes = {a["axis"] for a in out["changed_axes"]}
        self.assertEqual(axes, {"consume_sha256", "body_sha256"})
        self.assertNotIn("revision", axes)
        self.assertNotIn("SOURCE_REVISION", {a["class"] for a in out["changed_axes"]})
        ledger = bf._read_ledger(bf._ledger_path(self.repo, DATE, SLOT))
        self.assertEqual({e["field_path"] for e in ledger}, {"consume", "delivery_body"})

    def test_a_genuine_revision_change_is_named_as_one(self):
        self._deliver_once()
        rev_dir = self.repo / "evidence/daily_briefing" / SLOT / DATE / "rev-002"
        rev_dir.mkdir(parents=True, exist_ok=True)
        self._build_bundle(BRIEFING_MD + "\nrecovered\n")
        loc = self.repo / bf.LOCATOR_PATH
        locator = json.loads(loc.read_text())
        locator["revision"] = 2
        loc.write_text(json.dumps(locator, sort_keys=True, indent=2), encoding="utf-8")
        out = self._seal()
        self.assertIn("revision", {a["axis"] for a in out["changed_axes"]})
        self.assertIn("SOURCE_REVISION", {a["class"] for a in out["changed_axes"]})

    def _sign_resolution(self, change_key, impact="NONE", by="CIO", sk=None,
                         action="Portal note only; no stage or money change"):
        message = bf.change_resolution_message(f"{DATE}-pm", change_key, impact, by, action,
                                               bf.CONTRACT_VERSION)
        (bf.slot_dir(self.repo, DATE, SLOT) / "post-delivery-resolution-rev-001.json").write_text(
            json.dumps({"contract_version": bf.CONTRACT_VERSION,
                        "post_delivery_change_key": change_key, "capital_impact": impact,
                        "resolved_by": by, "action_taken": action,
                        "signature": ed25519.sign(message, sk or self.sk).hex()}),
            encoding="utf-8")

    def _policy(self, portal_implemented=True, channels=("user_push",)):
        path = self.repo / bf.PROJECTION_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "portal": {"adapter": "notion_cockpit", "implemented": portal_implemented,
                       "verified_against_live_api": portal_implemented},
            "alert": {"required_for_capital_impact": ["PRESENT"],
                      "user_reaching_channels": list(channels)}}), encoding="utf-8")

    def _change_and_ruling(self):
        directory = bf.slot_dir(self.repo, DATE, SLOT)
        change = json.loads(sorted(directory.glob("post-delivery-change-rev-*.json"))[-1].read_text())
        ruling = bf.load_change_resolutions(self.repo, DATE, SLOT).get(
            change["post_delivery_change_key"])
        return change, ruling

    def _projection_receipt(self, change_key, sha=None, rev=1, **overrides):
        change, ruling = self._change_and_ruling()
        body = {"post_delivery_change_key": change_key, "adapter": "notion_cockpit",
                "target": "Cockpit — Atlas Command Center",
                "written_at_utc": "2026-08-27T10:00:00Z",
                "readback_at_utc": "2026-08-27T10:00:01Z",
                "read_after_write_verified": True,
                "content_sha256": sha or (bf.expected_projection_digest(
                    f"{DATE}-pm", change, ruling) if ruling else "a" * 64)}
        body.update(overrides)
        (bf.slot_dir(self.repo, DATE, SLOT)
         / f"portal-projection-receipt-rev-{rev:03d}.json").write_text(
            json.dumps(body), encoding="utf-8")

    def _alert_receipt(self, change_key, channel="user_push", sha=None, rev=1, **overrides):
        change, ruling = self._change_and_ruling()
        body = {"post_delivery_change_key": change_key, "channel": channel,
                "sent_at_utc": "2026-08-27T10:01:00Z", "transport_id": "push-42",
                "transmitted_sha256": sha or (bf.expected_alert_digest(
                    f"{DATE}-pm", change, ruling) if ruling else "b" * 64)}
        body.update(overrides)
        (bf.slot_dir(self.repo, DATE, SLOT)
         / f"capital-alert-receipt-rev-{rev:03d}.json").write_text(
            json.dumps(body), encoding="utf-8")

    def _make_change(self):
        self._deliver_once()
        self._build_bundle(BRIEFING_MD + "\nsame-day recovery\n")
        return self._seal()

    def test_unresolved_change_is_not_a_green_drain(self):
        """rev14 warned and went green while nobody knew whether the change
        moved an investment conclusion."""
        self._make_change()
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])
        self.assertEqual(out["exit_code"], bf.EXIT_CIO_ATTENTION_REQUIRED)
        self.assertEqual(len(out["cio_attention_required"]), 1)
        self.assertEqual(out["cio_attention_required"][0]["capital_impact"], bf.UNKNOWN)

    def test_a_ruling_alone_is_not_completion(self):
        """rev15 called a signed ruling done. It settles WHETHER the change
        matters -- not that the record was corrected or anyone was told."""
        self._policy()
        change = self._make_change()
        self._sign_resolution(change["post_delivery_change_key"], "NONE")
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])
        self.assertIn("PORTAL_PROJECTION_RECEIPT_MISSING",
                      out["cio_attention_required"][0]["blocked_by"])

    def test_none_completes_on_ruling_plus_portal_proof(self):
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "NONE")
        self._projection_receipt(key)
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertTrue(out["complete"], out["cio_attention_required"])
        self.assertEqual(out["exit_code"], bf.EXIT_OK)
        self.assertTrue(out["post_delivery_changes"][0]["portal_synced"])

    def test_present_also_needs_a_user_alert_proof(self):
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "PRESENT", action="Portal updated; position unchanged")
        self._projection_receipt(key)
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])
        self.assertIn("CAPITAL_ALERT_RECEIPT_MISSING",
                      out["cio_attention_required"][0]["blocked_by"])
        self._alert_receipt(key)
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertTrue(out["complete"], out["cio_attention_required"])
        self.assertTrue(out["post_delivery_changes"][0]["alert_delivered"])

    def test_step_summary_is_not_a_user_reaching_channel(self):
        self._policy(channels=("user_push",))
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "PRESENT", action="x")
        self._projection_receipt(key)
        self._alert_receipt(key, channel="github_step_summary")
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])

    def test_a_stub_file_is_not_a_receipt(self):
        """rev16: with portal.implemented false, a two-line file named like a
        receipt completed the change."""
        change = self._make_change()          # default config: nothing implemented
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "NONE")
        (bf.slot_dir(self.repo, DATE, SLOT) / "portal-projection-receipt-rev-001.json").write_text(
            json.dumps({"post_delivery_change_key": key}), encoding="utf-8")
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])
        blocked = out["cio_attention_required"][0]["blocked_by"]
        self.assertIn("PORTAL_ADAPTER_NOT_IMPLEMENTED", blocked)
        self.assertIn("PORTAL_RECEIPT_WITHOUT_ADAPTER", blocked)
        self.assertFalse(out["post_delivery_changes"][0]["portal_synced"])

    def test_receipt_must_name_the_configured_adapter(self):
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "NONE")
        self._projection_receipt(key, adapter="some_other_adapter")
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertIn("PORTAL_RECEIPT_ADAPTER_MISMATCH",
                      out["cio_attention_required"][0]["blocked_by"])

    def test_receipt_must_hash_the_content_it_was_supposed_to_write(self):
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "NONE")
        self._projection_receipt(key, sha="c" * 64)
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertIn("PORTAL_RECEIPT_CONTENT_MISMATCH",
                      out["cio_attention_required"][0]["blocked_by"])

    def test_receipt_without_a_target_or_timestamp_is_incomplete(self):
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "NONE")
        self._projection_receipt(key, target="  ")
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertIn("PORTAL_RECEIPT_INCOMPLETE",
                      out["cio_attention_required"][0]["blocked_by"])

    def test_receipt_without_exact_readback_proof_is_rejected(self):
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "NONE")
        self._projection_receipt(key, read_after_write_verified=False)
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertIn("PORTAL_RECEIPT_READBACK_UNVERIFIED",
                      out["cio_attention_required"][0]["blocked_by"])

    def test_implemented_but_not_live_verified_does_not_accept_receipts(self):
        self._policy()
        policy_path = self.repo / bf.PROJECTION_PATH
        policy = json.loads(policy_path.read_text())
        policy["portal"]["verified_against_live_api"] = False
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "NONE")
        self._projection_receipt(key)
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        blocked = out["cio_attention_required"][0]["blocked_by"]
        self.assertIn("PORTAL_ADAPTER_NOT_LIVE_VERIFIED", blocked)
        self.assertIn("PORTAL_RECEIPT_WITHOUT_LIVE_VERIFICATION", blocked)

    def test_a_projection_hash_does_not_survive_a_changed_ruling(self):
        """The ruling is part of the projected content, so re-ruling invalidates
        a receipt written against the old one."""
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "NONE")
        self._projection_receipt(key)
        self.assertTrue(bf.drain(self.repo, ["github_step_summary"],
                                 durability_probe=lambda *_: True)["complete"])
        self._sign_resolution(key, "PRESENT", action="position trimmed")
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])
        self.assertIn("PORTAL_RECEIPT_CONTENT_MISMATCH",
                      out["cio_attention_required"][0]["blocked_by"])

    def test_alert_receipt_must_bind_to_the_alert_content(self):
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "PRESENT", action="position trimmed")
        self._projection_receipt(key)
        self._alert_receipt(key, sha="d" * 64)
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertIn("ALERT_RECEIPT_CONTENT_MISMATCH",
                      out["cio_attention_required"][0]["blocked_by"])

    def test_alert_receipt_without_transport_id_is_incomplete(self):
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "PRESENT", action="x")
        self._projection_receipt(key)
        self._alert_receipt(key, transport_id="")
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertIn("ALERT_RECEIPT_INCOMPLETE",
                      out["cio_attention_required"][0]["blocked_by"])

    def test_the_expected_hashes_are_published_so_an_adapter_can_target_them(self):
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "PRESENT", action="x")
        entry = bf.backlog(self.repo)["post_delivery_changes"][0]
        self.assertEqual(len(entry["expected_projection_sha256"]), 64)
        self.assertEqual(len(entry["expected_alert_sha256"]), 64)
        self.assertNotEqual(entry["expected_projection_sha256"], entry["expected_alert_sha256"])

    def test_a_later_receipt_supersedes_an_earlier_bad_one(self):
        """rev17 took the OLDEST receipt, so a bad first one could never be
        replaced -- append-only meant permanently unfinishable."""
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "NONE")
        self._projection_receipt(key, rev=1, adapter="wrong_adapter", sha="c" * 64)
        self.assertFalse(bf.drain(self.repo, ["github_step_summary"],
                                  durability_probe=lambda *_: True)["complete"])
        self._projection_receipt(key, rev=2)          # correct, newer
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertTrue(out["complete"], out["cio_attention_required"])

    def test_a_later_bad_receipt_fails_closed_over_an_earlier_good_one(self):
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "NONE")
        self._projection_receipt(key, rev=1)
        self.assertTrue(bf.drain(self.repo, ["github_step_summary"],
                                 durability_probe=lambda *_: True)["complete"])
        self._projection_receipt(key, rev=2, sha="c" * 64)
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])
        self.assertIn("PORTAL_RECEIPT_CONTENT_MISMATCH",
                      out["cio_attention_required"][0]["blocked_by"])

    def test_re_ruling_can_be_completed_by_a_new_receipt(self):
        """The case that was unrecoverable: NONE -> receipt -> re-ruled PRESENT
        -> new Portal write -> new receipt."""
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "NONE")
        self._projection_receipt(key, rev=1)
        self.assertTrue(bf.drain(self.repo, ["github_step_summary"],
                                 durability_probe=lambda *_: True)["complete"])
        self._sign_resolution(key, "PRESENT", action="position trimmed")
        self.assertFalse(bf.drain(self.repo, ["github_step_summary"],
                                  durability_probe=lambda *_: True)["complete"])
        self._projection_receipt(key, rev=2)          # hashes the new ruling
        self._alert_receipt(key, rev=1)
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertTrue(out["complete"], out["cio_attention_required"])
        self.assertEqual(out["post_delivery_changes"][0]["capital_impact"], "PRESENT")

    def test_alert_receipts_follow_the_same_authority_rule(self):
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "PRESENT", action="x")
        self._projection_receipt(key)
        self._alert_receipt(key, rev=1, channel="github_step_summary")
        self.assertFalse(bf.drain(self.repo, ["github_step_summary"],
                                  durability_probe=lambda *_: True)["complete"])
        self._alert_receipt(key, rev=2)
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertTrue(out["complete"], out["cio_attention_required"])

    def test_missing_adapters_are_named_not_waved_through(self):
        """Today's real state: no Portal adapter, no user-reaching channel."""
        change = self._make_change()          # default config: nothing implemented
        self._sign_resolution(change["post_delivery_change_key"], "PRESENT", action="x")
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])
        blocked = out["cio_attention_required"][0]["blocked_by"]
        self.assertIn("PORTAL_ADAPTER_NOT_IMPLEMENTED", blocked)
        self.assertIn("NO_USER_REACHING_CHANNEL_CONFIGURED", blocked)

    def test_action_taken_cannot_be_edited_after_signing(self):
        """rev15 left action_taken outside the signature, so a PRESENT ruling
        could be rewritten to say the opposite of what was signed."""
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "PRESENT", action="Portal updated; alert sent")
        self._projection_receipt(key); self._alert_receipt(key)
        path = bf.slot_dir(self.repo, DATE, SLOT) / "post-delivery-resolution-rev-001.json"
        tampered = json.loads(path.read_text())
        tampered["action_taken"] = "NO ALERT SENT; PORTAL NOT UPDATED; ORDER XYZ EXECUTED"
        path.write_text(json.dumps(tampered), encoding="utf-8")
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])
        self.assertIn("CIO_RULING_MISSING", out["cio_attention_required"][0]["blocked_by"])

    def test_present_ruling_needs_a_stated_action(self):
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "PRESENT", action="   ")
        self._projection_receipt(key); self._alert_receipt(key)
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])
        self.assertIn("CIO_RULING_MISSING", out["cio_attention_required"][0]["blocked_by"])

    def test_an_unsigned_ruling_is_not_a_ruling(self):
        self._policy()
        change = self._make_change()
        (bf.slot_dir(self.repo, DATE, SLOT) / "post-delivery-resolution-rev-001.json").write_text(
            json.dumps({"post_delivery_change_key": change["post_delivery_change_key"],
                        "capital_impact": "NONE", "resolved_by": "CIO"}), encoding="utf-8")
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])
        self.assertEqual(out["exit_code"], bf.EXIT_CIO_ATTENTION_REQUIRED)

    def test_a_ruling_signed_with_another_key_is_refused(self):
        self._policy()
        change = self._make_change()
        other = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
        self._sign_resolution(change["post_delivery_change_key"], "NONE", sk=other)
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])

    def test_a_ruling_does_not_transfer_to_another_change(self):
        self._policy()
        change = self._make_change()
        self._sign_resolution("0" * 64, "NONE")
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])

    def test_a_ruling_never_permits_redelivery(self):
        self._policy()
        change = self._make_change()
        key = change["post_delivery_change_key"]
        self._sign_resolution(key, "PRESENT", action="handled")
        self._projection_receipt(key); self._alert_receipt(key)
        before = self.summary.read_text()
        bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertEqual(self.summary.read_text(), before)
        self.assertEqual(bf.backlog(self.repo)["post_delivery_changes"][0]["redelivery"],
                         "FORBIDDEN")

    def test_backlog_surfaces_the_change_instead_of_hiding_it(self):
        self._deliver_once()
        self._build_bundle(BRIEFING_MD + "\nsame-day recovery\n")
        self._seal()
        state = bf.backlog(self.repo, now=bf._utcnow())
        self.assertEqual([c["briefing_id"] for c in state["post_delivery_changes"]],
                         [f"{DATE}-pm"])
        self.assertEqual(state["post_delivery_changes"][0]["redelivery"], "FORBIDDEN")
        self.assertFalse(state["post_delivery_changes"][0]["complete"])
        self.assertFalse(state["post_delivery_changes"][0]["ruled"])

    def test_no_second_delivery_happens(self):
        self._deliver_once()
        before = self.summary.read_text()
        self._build_bundle(BRIEFING_MD + "\nsame-day recovery\n")
        self._seal()
        bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertEqual(self.summary.read_text(), before)


class AuthorityStreams(Base):
    """A machine checker and a semantic reviewer answer different questions;
    neither may overwrite the other's answer."""

    def _submit(self, status, stream, corrections=()):
        return bf.record_validation(self.repo, DATE, SLOT, {
            "delivery_payload_sha256": self._payload_sha(),
            "validation_status": status, "authority_stream": stream,
            "validator_id": f"{stream}-validator", "validated_at_utc": "2026-08-27T09:40:00Z",
            "corrections": list(corrections),
            "conclusion_diff": {"spec_version": None}})

    def _governing(self):
        v, problem = bf.resolve_validation(bf.slot_dir(self.repo, DATE, SLOT))
        self.assertIsNone(problem)
        return v

    def test_machine_hold_then_clear_returns_to_semantic_pending(self):
        """rev10: a transient structural fault held the briefing forever."""
        self._seal()
        self._submit("HOLD", "machine")
        self.assertEqual(self._governing()["validation_status"], "HOLD")
        self._submit(bf.MACHINE_CLEARED, "machine")
        self.assertIsNone(self._governing())          # slot open, semantic validator still owns it
        later = bf._utcnow() + dt.timedelta(minutes=bf.VALIDATION_TIMEOUT_MIN + 1)
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"], now=later,
                       durability_probe=lambda *_: True)
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_TIMEOUT")

    def test_machine_correction_then_clear_releases_the_block(self):
        self._seal()
        self._submit("PASS_WITH_CORRECTION", "machine", self.CORR)
        self.assertEqual(self._governing()["validation_status"], "PASS_WITH_CORRECTION")
        self._submit(bf.MACHINE_CLEARED, "machine")
        self.assertIsNone(self._governing())

    def test_machine_clear_cannot_lift_a_semantic_hold(self):
        self._seal()
        self._submit("HOLD", "semantic")
        self._submit(bf.MACHINE_CLEARED, "machine")
        governing = self._governing()
        self.assertEqual(governing["validation_status"], "HOLD")
        self.assertEqual(governing["authority_stream"], "semantic")
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_HELD")

    def test_semantic_pass_cannot_lift_a_machine_hold(self):
        """Structure is objective; a reviewer's opinion does not repair it."""
        self._seal()
        self._submit("HOLD", "machine")
        self._submit("PASS", "semantic")
        self.assertEqual(self._governing()["validation_status"], "HOLD")

    def test_machine_stream_cannot_assert_pass(self):
        self._seal()
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._submit("PASS", "machine")
        self.assertEqual(ctx.exception.code, "FINALIZATION_MACHINE_STREAM_STATUS_FORBIDDEN")

    def test_machine_clear_cannot_be_submitted_as_semantic(self):
        self._seal()
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._submit(bf.MACHINE_CLEARED, "semantic")
        self.assertEqual(ctx.exception.code, "FINALIZATION_MACHINE_CLEAR_FROM_WRONG_STREAM")

    def test_unknown_stream_is_refused(self):
        self._seal()
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._submit("PASS", "evil")
        self.assertEqual(ctx.exception.code, "FINALIZATION_AUTHORITY_STREAM_UNSUPPORTED")

    def test_legacy_verdict_without_a_stream_is_semantic(self):
        self._seal(); self._validate()
        governing = self._governing()
        self.assertEqual(governing["authority_stream"], "semantic")
        self.assertEqual(governing["validation_status"], "PASS")


class StreamInterleaving(Base):
    """Both streams may have unread verdicts waiting at the same time.

    rev 11's guarantees held only when the OTHER stream's verdict happened to
    be recorded first: one authoritative inbox meant the newer file demoted the
    other stream's unread verdict to history, and it was never recorded at all.
    These tests never pre-record anything -- both verdicts sit in the inbox and
    are drained together.
    """

    def _inbox(self, name, status, stream, corrections=()):
        (bf.slot_dir(self.repo, DATE, SLOT) / name).write_text(json.dumps({
            "validation_status": status, "authority_stream": stream,
            "validator_id": f"{stream}-validator", "corrections": list(corrections),
            "conclusion_diff": {"spec_version": None},
            "delivery_payload_sha256": self._payload_sha()}), encoding="utf-8")

    def _governing(self):
        v, problem = bf.resolve_validation(bf.slot_dir(self.repo, DATE, SLOT))
        self.assertIsNone(problem, problem)
        return v

    def test_unread_machine_hold_survives_a_newer_semantic_pass(self):
        self._seal()
        self._inbox("validation-inbox-rev-001.json", "HOLD", "machine")
        self._inbox("validation-inbox-rev-002.json", "PASS", "semantic")
        out = bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertEqual(out["count"], 2)
        governing = self._governing()
        self.assertEqual(governing["validation_status"], "HOLD")
        self.assertEqual(governing["authority_stream"], "machine")
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_HELD")
        self.assertEqual(self.summary.read_text(), "")

    def test_unread_semantic_hold_survives_a_newer_machine_clear(self):
        self._seal()
        self._inbox("validation-inbox-rev-001.json", "HOLD", "machine")
        bf.ingest_inbox(self.repo, DATE, SLOT)
        self._inbox("validation-inbox-rev-002.json", "HOLD", "semantic")
        self._inbox("validation-inbox-rev-003.json", bf.MACHINE_CLEARED, "machine")
        bf.ingest_inbox(self.repo, DATE, SLOT)
        governing = self._governing()
        self.assertEqual(governing["validation_status"], "HOLD")
        self.assertEqual(governing["authority_stream"], "semantic")

    def test_machine_stream_clears_its_own_block_across_streams(self):
        self._seal()
        self._inbox("validation-inbox-rev-001.json", "HOLD", "machine")
        self._inbox("validation-inbox-rev-002.json", bf.MACHINE_CLEARED, "machine")
        bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertIsNone(self._governing())

    def test_semantic_stream_latest_wins_within_its_own_stream(self):
        self._seal()
        self._inbox("validation-inbox-rev-001.json", "HOLD", "semantic")
        self._inbox("validation-inbox-rev-002.json", "PASS", "semantic")
        bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertEqual(self._governing()["validation_status"], "PASS")

    def test_per_stream_atomicity_good_then_bad_fails_closed(self):
        self._seal()
        self._inbox("validation-inbox-rev-001.json", "PASS", "semantic")
        bf.ingest_inbox(self.repo, DATE, SLOT)
        (bf.slot_dir(self.repo, DATE, SLOT) / "validation-inbox-rev-002.json").write_text(
            json.dumps({"validation_status": "PASS", "authority_stream": "semantic",
                        "validator_id": "v", "corrections": [],
                        "conclusion_diff": {"spec_version": None},
                        "delivery_payload_sha256": "0" * 64}), encoding="utf-8")
        with self.assertRaises(bf.FinalizationError):
            bf.ingest_inbox(self.repo, DATE, SLOT)
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_INVALID")

    def test_per_stream_atomicity_bad_then_good_recovers(self):
        self._seal()
        (bf.slot_dir(self.repo, DATE, SLOT) / "validation-inbox-rev-001.json").write_text(
            json.dumps({"validation_status": "PASS", "authority_stream": "semantic",
                        "validator_id": "v", "corrections": [],
                        "conclusion_diff": {"spec_version": None},
                        "delivery_payload_sha256": "0" * 64}), encoding="utf-8")
        with self.assertRaises(bf.FinalizationError):
            bf.ingest_inbox(self.repo, DATE, SLOT)
        self._inbox("validation-inbox-rev-002.json", "PASS", "semantic")
        bf.ingest_inbox(self.repo, DATE, SLOT)
        self.assertEqual(self._governing()["validation_status"], "PASS")

    def test_one_stream_being_broken_does_not_let_the_other_through(self):
        self._seal()
        self._inbox("validation-inbox-rev-001.json", "PASS", "semantic")
        (bf.slot_dir(self.repo, DATE, SLOT) / "validation-inbox-rev-002.json").write_text(
            json.dumps({"validation_status": "HOLD", "authority_stream": "machine",
                        "validator_id": "m", "corrections": [],
                        "conclusion_diff": {"spec_version": None},
                        "delivery_payload_sha256": "0" * 64}), encoding="utf-8")
        with self.assertRaises(bf.FinalizationError):
            bf.ingest_inbox(self.repo, DATE, SLOT)
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_INVALID")

    def test_unparseable_inbox_is_not_silence(self):
        self._seal()
        (bf.slot_dir(self.repo, DATE, SLOT) / "validation-inbox-rev-001.json").write_text(
            "{not json", encoding="utf-8")
        _v, problem = bf.resolve_validation(bf.slot_dir(self.repo, DATE, SLOT))
        self.assertIn("unreadable", problem)
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_INVALID")


class SemanticValidatorPolicy(Base):
    """The 20-minute wait is time granted to a semantic validator. With none
    configured it is pure latency, and rev 13 would have made every clean round
    finish red and undelivered: the job stops at 15 minutes and no same-day
    re-entry exists."""

    def setUp(self):
        super().setUp()
        path = self.repo / bf.ACTIVATION_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"active_from_kst_date": DATE,
                                    "active_from_slot": "morning"}), encoding="utf-8")

    def _policy(self, expected, minutes=20):
        path = self.repo / bf.SEMANTIC_VALIDATOR_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"expected": expected, "timeout_minutes": minutes}),
                        encoding="utf-8")

    def test_no_validator_configured_blocks_delivery(self):
        self._policy(False)
        self._seal()
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"],
                       durability_probe=lambda *_: True)
        self.assertEqual(ctx.exception.code, "FINALIZATION_SEMANTIC_VALIDATOR_REQUIRED")
        self.assertFalse(bf.already_delivered(self.repo, DATE, SLOT))

    def test_no_validator_configured_keeps_drain_incomplete(self):
        # activation starts at THIS slot, so the morning slot is not owed
        path = self.repo / bf.ACTIVATION_PATH
        path.write_text(json.dumps({"active_from_kst_date": DATE,
                                    "active_from_slot": "evening"}), encoding="utf-8")
        self._policy(False)
        self._seal()
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"], out)
        self.assertEqual(out["exit_code"], bf.EXIT_DRAIN_INCOMPLETE)
        self.assertFalse(out["semantic_validator_expected"])

    def test_no_validator_does_not_mint_an_internal_verdict(self):
        self._policy(False)
        self._seal()
        with self.assertRaises(bf.FinalizationError):
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"],
                       durability_probe=lambda *_: True)
        self.assertIsNone(bf._latest(bf.slot_dir(self.repo, DATE, SLOT), "validation"))

    def test_expected_validator_still_waits(self):
        self._policy(True)
        self._seal()
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_PENDING")
        later = bf._utcnow() + dt.timedelta(minutes=21)
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"], now=later,
                       durability_probe=lambda *_: True)
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_TIMEOUT")

    def test_absent_policy_file_keeps_the_conservative_wait(self):
        self._seal()
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_VALIDATION_PENDING")

    def test_no_validator_does_not_relax_hold(self):
        self._policy(False)
        self._seal(); self._validate(status="HOLD")
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_HELD")

    def test_no_validator_does_not_relax_the_cio_gate(self):
        self._policy(False)
        self._seal(); self._validate(self.CORR)
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "FINALIZATION_CIO_APPROVAL_REQUIRED")

    def test_no_validator_does_not_relax_durable_intent(self):
        self._policy(False)
        self._seal(); self._validate()
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.deliver(self.repo, DATE, SLOT, ["test_unsafe"], durability_probe=self.durable)
        self.assertEqual(ctx.exception.code, "FINALIZATION_INTENT_NOT_DURABLE")
        self.assertEqual(len(self.unsafe.sent), 0)

    def test_no_validator_never_creates_wait_metadata(self):
        self._policy(False)
        self._seal()
        later = bf._utcnow() + dt.timedelta(minutes=7)
        with self.assertRaises(bf.FinalizationError):
            bf.deliver(self.repo, DATE, SLOT, ["github_step_summary"], now=later,
                       durability_probe=lambda *_: True)
        self.assertIsNone(bf._latest(bf.slot_dir(self.repo, DATE, SLOT), "validation"))

    def test_retry_does_not_create_internal_verdicts(self):
        self._policy(False)
        self._seal()
        for _ in range(3):
            try:
                bf.deliver(self.repo, DATE, SLOT, ["kakao"], required=["kakao"],
                           durability_probe=lambda *_: True)
            except bf.FinalizationError:
                pass
        recorded = bf._recorded_validations(bf.slot_dir(self.repo, DATE, SLOT))
        self.assertEqual(len(recorded), 0)

    def test_external_validator_cannot_claim_no_validator(self):
        self._seal()
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._validate(status="UNVALIDATED_NO_VALIDATOR")
        self.assertEqual(ctx.exception.code, "FINALIZATION_STATUS_NOT_EXTERNALLY_SUBMITTABLE")


class PortalBeforeDelivery(Base):
    def setUp(self):
        super().setUp()
        path = self.repo / bf.ACTIVATION_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "active_from_kst_date": DATE,
            "active_from_slot": "morning",
            "portal_before_delivery": True,
            "notion_final_after_portal": True,
        }), encoding="utf-8")

    def _portal_receipt(self, draft, validation, **updates):
        projection_id = f"{DATE}-PM-" + "a" * 24
        envelope_path = (
            f"evidence/validated_briefing_portal/{SLOT}/{DATE}/"
            "rev-001/portal-projection.json"
        )
        if not (self.repo / ".git").exists():
            subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
            subprocess.run(["git", "-C", str(self.repo), "config",
                            "user.email", "atlas-tests@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(self.repo), "config",
                            "user.name", "Atlas Tests"], check=True)
            (self.repo / "source.txt").write_text("sealed source\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(self.repo), "add", "source.txt"], check=True)
            subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "source"],
                           check=True)
        source_commit = subprocess.run(
            ["git", "-C", str(self.repo), "rev-list", "--max-parents=0", "HEAD"],
            capture_output=True, text=True, check=True).stdout.splitlines()[0]
        envelope = {
            "schema_version": "portal_projection/2",
            "briefing_date": DATE,
            "slot": "PM",
            "projection_id": projection_id,
            "source_commit": source_commit,
            "completion_state": "VALIDATED",
            "safety_attestation": bf.PORTAL_RECEIPT_AUTHORITY,
        }
        envelope_file = self.repo / envelope_path
        envelope_file.parent.mkdir(parents=True, exist_ok=True)
        envelope_file.write_bytes(bf._canonical(envelope))
        subprocess.run(["git", "-C", str(self.repo), "add", envelope_path], check=True)
        if subprocess.run(["git", "-C", str(self.repo), "diff", "--cached", "--quiet"],
                          check=False).returncode != 0:
            subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "envelope"],
                           check=True)
        envelope_commit = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        body = {
            "schema_version": bf.PORTAL_FINAL_RECEIPT_SCHEMA,
            "briefing_id": f"{DATE}-pm",
            "kst_date": DATE,
            "slot": SLOT,
            "projection_id": projection_id,
            "envelope_commit": envelope_commit,
            "envelope_path": envelope_path,
            "envelope_sha256": bf._sha256(envelope_file.read_bytes()),
            "source_commit": source_commit,
            "portal_result": "DEPLOYED",
            "portal_run_id": "33176553036",
            "portal_source_sha": "e" * 40,
            "deployment_url": "https://atlas.example.test",
            "notion_receipt_page_id": "3ca9f2d73c84818a9481e8b4bb5a1fca",
            "viewer_readback_verified": True,
            "notion_receipt_readback_verified": True,
            "delivery_payload_sha256": draft["delivery_payload_sha256"],
            "validation_rev": validation["rev"],
            "observed_at_utc": "2026-08-27T10:00:00Z",
            "authority": bf.PORTAL_RECEIPT_AUTHORITY,
        }
        body.update(updates)
        return body

    def test_semantic_pass_without_portal_receipt_stays_undelivered(self):
        self._seal(); self._validate()
        with self.assertRaises(bf.FinalizationError) as ctx:
            self._deliver()
        self.assertEqual(ctx.exception.code, "PORTAL_FINAL_RECEIPT_MISSING")
        self.assertFalse(bf.already_delivered(self.repo, DATE, SLOT))

    def test_verified_portal_and_notion_receipt_unlocks_delivery(self):
        draft = self._seal(); validation = self._validate()
        recorded = bf.record_portal_final_receipt(
            self.repo, DATE, SLOT, self._portal_receipt(draft, validation))
        self.assertTrue(recorded["recorded"])
        result = bf.deliver(
            self.repo, DATE, SLOT, ["github_step_summary"],
            durability_probe=lambda *_: True)
        self.assertEqual(result["validation_status_at_delivery"], "PASS")

    def test_blocked_or_unverified_portal_receipt_is_rejected(self):
        draft = self._seal(); validation = self._validate()
        for update, code in (
            ({"portal_result": "BLOCKED"}, "PORTAL_FINAL_RECEIPT_RESULT_BLOCKED"),
            ({"viewer_readback_verified": False},
             "PORTAL_FINAL_RECEIPT_READBACK_UNVERIFIED"),
            ({"authority": {**bf.PORTAL_RECEIPT_AUTHORITY,
                            "trading_authority": True}},
             "PORTAL_FINAL_RECEIPT_AUTHORITY_FAILED"),
        ):
            with self.subTest(update=update):
                with self.assertRaises(bf.FinalizationError) as ctx:
                    bf.record_portal_final_receipt(
                        self.repo, DATE, SLOT,
                        self._portal_receipt(draft, validation, **update))
                self.assertEqual(ctx.exception.code, code)

    def test_receipt_is_bound_to_local_and_committed_envelope_bytes(self):
        draft = self._seal(); validation = self._validate()
        receipt = self._portal_receipt(draft, validation)
        (self.repo / receipt["envelope_path"]).write_text("{}", encoding="utf-8")
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.record_portal_final_receipt(self.repo, DATE, SLOT, receipt)
        self.assertEqual(ctx.exception.code, "PORTAL_FINAL_RECEIPT_ENVELOPE_HASH_MISMATCH")

    def test_receipt_cannot_name_an_unrelated_envelope_commit(self):
        draft = self._seal(); validation = self._validate()
        receipt = self._portal_receipt(
            draft, validation, envelope_commit="f" * 40)
        with self.assertRaises(bf.FinalizationError) as ctx:
            bf.record_portal_final_receipt(self.repo, DATE, SLOT, receipt)
        self.assertEqual(ctx.exception.code, "PORTAL_FINAL_RECEIPT_COMMIT_UNVERIFIED")


class DrainLiveness(Base):
    def setUp(self):
        super().setUp()
        path = self.repo / bf.ACTIVATION_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"active_from_kst_date": DATE,
                                    "active_from_slot": "morning"}), encoding="utf-8")

    def test_pending_slot_is_not_a_green_drain(self):
        """rev4 returned VALIDATION_PENDING inside a successful drain result."""
        self._seal()
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertFalse(out["complete"])
        self.assertEqual(out["exit_code"], bf.EXIT_DRAIN_INCOMPLETE)
        self.assertEqual([e["error"] for e in out["observed_pending"]],
                         ["FINALIZATION_VALIDATION_PENDING"])

    def test_held_slot_is_a_machine_failure(self):
        self._seal(); self._validate(status="HOLD")
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertEqual(out["exit_code"], bf.EXIT_DRAIN_INCOMPLETE)
        self.assertEqual([e["error"] for e in out["machine_failures"]], ["FINALIZATION_HELD"])

    def test_missing_production_alone_is_not_green(self):
        out = bf.drain(self.repo, ["github_step_summary"], durability_probe=lambda *_: True)
        self.assertTrue(out["missing_production"])
        self.assertFalse(out["complete"])

    def test_cli_drain_propagates_a_nonzero_exit(self):
        self._seal()
        rc = bf.main(["drain", "--repo-root", str(self.repo),
                      "--channel", "github_step_summary", "--allow-nondurable-intent"])
        self.assertEqual(rc, bf.EXIT_DRAIN_INCOMPLETE)


class Ed25519Vectors(unittest.TestCase):
    """RFC 8032 §7.1 -- the approval boundary is only as good as this."""
    def test_rfc8032_vectors(self):
        for sk_h, pk_h, msg_h, sig_h in [
            ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
             "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
             "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
            ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
             "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
             "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
        ]:
            sk, pk = bytes.fromhex(sk_h), bytes.fromhex(pk_h)
            msg, sig = bytes.fromhex(msg_h), bytes.fromhex(sig_h)
            self.assertEqual(ed25519.publickey(sk), pk)
            self.assertEqual(ed25519.sign(msg, sk), sig)
            self.assertTrue(ed25519.verify(sig, msg, pk))
            self.assertFalse(ed25519.verify(sig, msg + b"\x00", pk))


if __name__ == "__main__":
    unittest.main(verbosity=2)
