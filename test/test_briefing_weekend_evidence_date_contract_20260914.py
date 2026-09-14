#!/usr/bin/env python3
"""Weekend briefing evidence-date contract: scheduled_briefing_retrieval_authority/4.

Problem (PR #731 review): the v3 weekend contract line
``- latest_confirmed_evidence_date: <date>`` carried the STEP0 read-model
collector run KST date. For KRX that overstates what is confirmed: the Friday
session is confirmed only on Sunday ~21:00Z, so every Saturday/Sunday morning
briefing shows KRX confirmed=Thursday next to US=Friday and the B5-1
UNSCOPED_CONFIRMED_EVIDENCE_DATE rule of the staging checklist HOLDs.

v4 replaces that line with three correctly named lines, each re-derived by the
renderer, the publisher and the consumer from the same hash-bound sources:
  * source_evidence_kst_date         -- STEP0 sources collected_for_kst_date
  * krx_latest_confirmed_close_date  -- data/latest_krx.json confirmed_through
                                        (frozen reference bound to STEP0 krx
                                        sha256; the publisher re-reads the blob)
  * us_latest_verified_session_date  -- READY FREE_MARKET_DATA
                                        us_market_reference.as_of_session_date
UNKNOWN whenever a source is absent or unbound. Envelopes are
version-dispatched: retained v3 slots keep validating under v3 rules and the
v3 line is never accepted under v4.

Inputs are the real retained weekend slots 2026-09-12 AM rev-001/rev-002 and
2026-09-13 AM rev-001 (test/fixtures/briefing_weekend_evidence_date_contract_20260914/
weekend_slots.json): sealed packets, briefings, sealed payloads, their v3
retrieval envelopes, and every B5 input read from the git blob committed at
the slot's finalization seal commit. The checks are the byte-identical staging
copy pinned by PR #731 (sha256 fd98a204...9a89792); they are not modified.
Nothing reads the wall clock.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "test/fixtures/briefing_weekend_evidence_date_contract_20260914"
FIXTURE = json.loads((FIXTURE_DIR / "weekend_slots.json").read_text(encoding="utf-8"))
B5_CHECKS_PATH = ROOT / "test/fixtures/briefing_b5_renderer_alignment_20260914/briefing_semantic_checks.py"
B5_CHECKS_SHA256 = "fd98a20461305c45f2ae2c495ef34595de56ebf12f33a4470a42536e62a89792"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B5 = _load("briefing_weekend_contract_b5_checks", B5_CHECKS_PATH)
ORCH = _load("briefing_weekend_contract_orchestrator", ROOT / "briefing/daily_orchestrator.py")
PUBLISHER = _load(
    "briefing_weekend_contract_publisher", ROOT / ".github/scripts/publish_scheduled_briefing_authority.py"
)
CONSUMER = _load(
    "briefing_weekend_contract_consumer", ROOT / ".github/scripts/consume_scheduled_briefing_authority.py"
)
SLOTS = {(row["decision_date"], row["revision"]): row for row in FIXTURE["slots"]}
PASS, NV, HOLD = B5.PASS, B5.NV, B5.HOLD
V3, V4 = PUBLISHER.SCHEMA_V3, PUBLISHER.SCHEMA_V4
EXPECTED_DATES = {
    "source_evidence_kst_date": "2026-09-11",
    "krx_latest_confirmed_close_date": "2026-09-10",
    "us_latest_verified_session_date": "2026-09-11",
}
SEALED_UNSCOPED_HOLD = (
    "UNSCOPED_CONFIRMED_EVIDENCE_DATE:rendered=2026-09-11 is not market-scoped: "
    "KRX confirmed=2026-09-10, US session=2026-09-11"
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(*args: str, cwd: Path = ROOT) -> bytes:
    completed = subprocess.run(
        ["git", "--no-replace-objects", *args], cwd=cwd, check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed (full history required)")
    return completed.stdout


def _retained(slot: dict, field: str) -> bytes:
    raw = (ROOT / slot[f"{field}_path"]).read_bytes()
    if _sha256(raw) != slot[f"{field}_sha256"]:
        raise AssertionError(f"retained {field} rewritten: {slot[f'{field}_path']}")
    return raw


def _sources(slot: dict) -> dict:
    sources = {}
    for input_id, row in slot["b5_inputs_at_seal_commit"].items():
        body = _git("cat-file", "blob", row["git_blob_sha1"])
        if _sha256(body) != row["sha256"] or not B5.repo_path_matches(input_id, row["repo_path"]):
            raise AssertionError(f"B5 input not the sealed bytes: {input_id} {row['repo_path']}")
        sources[input_id] = {"repo_path": row["repo_path"], "body": body}
    return sources


def _rehash(packet: dict) -> dict:
    packet.pop("packet_sha256", None)
    packet["packet_sha256"] = ORCH.payload_sha256(packet)
    return packet


def _referenced_packet(slot: dict) -> dict:
    """The sealed packet with the presentation references its build would freeze.

    The retained weekend packets predate PR #722. As in that PR's audited
    replay, only the selections are supplied (the data/latest_krx.json blob
    and the PAPER pointer generation committed at the seal commit, the
    post-close bundle selected from the immutable archive); every displayed
    field is re-derived from retained bytes by the orchestrator itself.
    """
    packet = json.loads(_retained(slot, "packet"))
    seal_krx = slot["latest_krx_at_seal"]
    if _sha256(_git("cat-file", "blob", seal_krx["source_git_blob_sha1"])) != seal_krx["source_sha256"]:
        raise AssertionError("seal-time latest_krx blob changed")
    pointer = slot["paper_reference_pointer_at_seal"]
    if _sha256((ROOT / pointer["evidence_path"]).read_bytes()) != pointer["sha256"]:
        raise AssertionError("retained PAPER pointer generation rewritten")
    generated = dt.datetime.fromisoformat(packet["generated_at"].replace("Z", "+00:00"))
    step0 = copy.deepcopy(packet["frozen_sources"]["STEP0_READ_MODEL_HEALTH"])
    step0[ORCH.PRESENTATION_REFERENCES] = {
        "krx_confirmed_close": {"source_git_blob_sha1": seal_krx["source_git_blob_sha1"]},
        "krx_post_close": {
            "selected_date": ORCH._select_krx_post_close(ORCH.ROOT, slot["decision_date"], generated)
        },
        "paper_regime": {"evidence_path": pointer["evidence_path"]},
    }
    packet["frozen_sources"]["STEP0_READ_MODEL_HEALTH"] = ORCH._presentation_references_snapshot(
        step0, slot["decision_date"], generated, root=None
    )
    return _rehash(packet)


def _payload(slot: dict, rendered: str) -> bytes:
    """The rendered briefing inside the exact sealed delivery envelope tail."""
    body = _retained(slot, "briefing").rstrip(b"\n")
    sealed = _retained(slot, "sealed_payload")
    if not sealed.startswith(body):
        raise AssertionError("sealed payload does not start with the retained briefing.md")
    return rendered.encode("utf-8").rstrip(b"\n") + sealed[len(body):]


def _evaluate(slot: dict, payload: bytes) -> dict:
    inputs = B5.build_inputs(
        briefing_date=slot["decision_date"], slot="morning", payload=payload, sources=_sources(slot)
    )
    return {row["check_id"]: row for row in B5.evaluate_all(inputs)}


def _component(packet: dict, component_id: str) -> dict:
    return next(row for row in packet["components"] if row["component_id"] == component_id)


class PinnedChecklistTests(unittest.TestCase):
    def test_checks_module_is_the_pinned_staging_copy(self):
        self.assertEqual(_sha256(B5_CHECKS_PATH.read_bytes()), B5_CHECKS_SHA256)
        self.assertEqual(FIXTURE["b5_checks_module"]["sha256"], B5_CHECKS_SHA256)
        self.assertEqual(B5.CONTRACT, "atlas_b5_semantic_checklist/1")
        self.assertEqual(
            B5.RULES[B5.SESSION_RECONCILIATION],
            ("KRX_BOARD_VS_LATEST_KRX", "DYNAMIC_CLOCK_KOREA_VS_LATEST_KRX",
             "US_BOARD_VS_US_REFERENCE", "UNSCOPED_CONFIRMED_EVIDENCE_DATE"),
        )

    def test_contract_version_is_v4(self):
        contract = json.loads((ROOT / PUBLISHER.CONTRACT_PATH).read_text(encoding="utf-8"))
        self.assertEqual(contract["schema_version"], V4)
        self.assertEqual(CONSUMER._load_contract()["schema_version"], V4)
        self.assertEqual(
            ORCH.WEEKEND_SESSION_CONTEXT_DATE_KEYS, tuple(EXPECTED_DATES),
        )


class WeekendB5SessionReconciliationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rendered = {}
        for key, slot in SLOTS.items():
            packet = _referenced_packet(slot)
            cls.rendered[key] = (packet, ORCH.render_markdown(packet))

    def test_v4_weekend_renders_pass_b5_1_and_every_other_check(self):
        for key, slot in SLOTS.items():
            _, rendered = self.rendered[key]
            results = _evaluate(slot, _payload(slot, rendered))
            with self.subTest(slot=key):
                session = results[B5.SESSION_RECONCILIATION]
                self.assertEqual(session["status"], PASS, session["reason"])
                self.assertEqual(session["reason"], "OK")
                for rule in B5.RULES[B5.SESSION_RECONCILIATION]:
                    inputs = B5.build_inputs(
                        briefing_date=slot["decision_date"], slot="morning",
                        payload=_payload(slot, rendered), sources=_sources(slot),
                    )
                    self.assertEqual(
                        B5.evaluate(B5.SESSION_RECONCILIATION, inputs, [rule])["status"], PASS, rule
                    )
                for check_id in B5.CHECK_IDS:
                    if check_id == B5.PORTAL_PARITY:
                        # The atlas-portal close-card render is not an atlas-data blob.
                        self.assertEqual(results[check_id]["status"], NV)
                    else:
                        self.assertEqual(results[check_id]["status"], PASS, (check_id, results[check_id]["reason"]))

    def test_checks_still_bite_on_the_sealed_v3_payloads(self):
        for key, slot in SLOTS.items():
            with self.subTest(slot=key):
                result = _evaluate(slot, _retained(slot, "sealed_payload"))[B5.SESSION_RECONCILIATION]
                self.assertEqual(result["status"], HOLD)
                self.assertEqual(result["reason"], SEALED_UNSCOPED_HOLD)

    def test_the_v3_line_under_a_v4_render_still_holds(self):
        for key, slot in SLOTS.items():
            _, rendered = self.rendered[key]
            mutated = rendered.replace(
                "- new_session: NONE\n",
                "- new_session: NONE\n- latest_confirmed_evidence_date: 2026-09-11\n", 1,
            )
            self.assertNotEqual(mutated, rendered)
            with self.subTest(slot=key):
                result = _evaluate(slot, _payload(slot, mutated))[B5.SESSION_RECONCILIATION]
                self.assertEqual(result["status"], HOLD)
                self.assertEqual(result["reason"], SEALED_UNSCOPED_HOLD)

    def test_market_scoped_lines_equal_the_b5_source_dates(self):
        """The new lines are not read by B5-1; bind them to its sources here."""
        for key, slot in SLOTS.items():
            packet, rendered = self.rendered[key]
            sources = _sources(slot)
            weekend = B5.parse_payload(rendered)["weekend"]
            with self.subTest(slot=key):
                self.assertNotIn("latest_confirmed_evidence_date", weekend)
                self.assertEqual({name: weekend[name] for name in EXPECTED_DATES}, EXPECTED_DATES)
                krx = json.loads(sources["b5_latest_krx"]["body"])
                fmd = json.loads(sources["b5_free_market_data"]["body"])
                self.assertEqual(
                    weekend["krx_latest_confirmed_close_date"], krx["decision_readiness"]["confirmed_through"]
                )
                self.assertEqual(
                    weekend["us_latest_verified_session_date"], fmd["us_market_reference"]["as_of_session_date"]
                )
                step0 = _component(packet, "STEP0_READ_MODEL_HEALTH")["packet"]["sources"]
                self.assertEqual(
                    {row["collected_for_kst_date"] for row in step0.values()},
                    {weekend["source_evidence_kst_date"]},
                )
                # The board dates B5-1 does read agree with the weekend lines.
                board = B5.parse_payload(rendered)["board"]
                self.assertEqual(
                    board["KRX"]["latest_confirmed_close_date"].split(";")[0],
                    weekend["krx_latest_confirmed_close_date"],
                )
                self.assertEqual(
                    board["US"]["latest_verified_us_session_date"], weekend["us_latest_verified_session_date"]
                )
                for line in rendered.splitlines():
                    if line.startswith("- pilot_subjects=") or "opportunity_state=" in line:
                        self.assertNotRegex(line, r"(?<![A-Za-z_])decision_date=")
                self.assertIn(" pilot_decision_date=2026-08-22 ", rendered)


class DerivationAgreementTests(unittest.TestCase):
    """Renderer, publisher and consumer derive the same three dates."""

    def _all(self, packet: dict) -> tuple[dict, dict, dict]:
        decision_date = packet["decision_date"]
        rendered = ORCH.weekend_session_context_dates(packet)
        published = PUBLISHER.weekend_market_dates(packet, decision_date)
        consumed = CONSUMER.weekend_market_dates(packet, decision_date)
        return rendered, published, consumed

    def assertAgree(self, packet: dict, krx: str, us: str) -> None:
        rendered, published, consumed = self._all(packet)
        self.assertEqual(published, consumed)
        self.assertEqual(
            {k: v for k, v in rendered.items() if k != "source_evidence_kst_date"}, published
        )
        self.assertEqual(published, {
            "krx_latest_confirmed_close_date": krx, "us_latest_verified_session_date": us,
        })

    def test_real_slots_and_unbound_variants(self):
        slot = SLOTS[("2026-09-13", 1)]
        base = _referenced_packet(slot)
        self.assertAgree(base, "2026-09-10", "2026-09-11")
        self.assertEqual(ORCH.weekend_session_context_dates(base)["source_evidence_kst_date"], "2026-09-11")

        def references(packet):
            return packet["frozen_sources"]["STEP0_READ_MODEL_HEALTH"][ORCH.PRESENTATION_REFERENCES]

        legacy = json.loads(_retained(slot, "packet"))
        self.assertAgree(legacy, "UNKNOWN", "2026-09-11")

        variants = {
            "confirmed close not bound to STEP0 bytes": (
                lambda p: references(p)["krx_confirmed_close"].update(source_sha256="0" * 64), "UNKNOWN", "2026-09-11"),
            "confirmed close carries an unknown reason": (
                lambda p: references(p)["krx_confirmed_close"].update(
                    unknown_reason="KRX_CONFIRMED_SOURCE_COLLECTED_AFTER_GENERATION"), "UNKNOWN", "2026-09-11"),
            "non-canonical confirmed date": (
                lambda p: references(p)["krx_confirmed_close"].update(confirmed_through="20260910"), "UNKNOWN", "2026-09-11"),
            "confirmed date after decision date": (
                lambda p: references(p)["krx_confirmed_close"].update(confirmed_through="2026-09-14"), "UNKNOWN", "2026-09-11"),
            "US component not READY": (
                lambda p: _component(p, "FREE_MARKET_DATA").update(status="DEGRADED"), "2026-09-10", "UNKNOWN"),
            "US session missing": (
                lambda p: _component(p, "FREE_MARKET_DATA")["packet"]["us_market_reference"].pop("as_of_session_date"),
                "2026-09-10", "UNKNOWN"),
            "US session after decision date": (
                lambda p: _component(p, "FREE_MARKET_DATA")["packet"]["us_market_reference"].update(
                    as_of_session_date="2026-09-14"), "2026-09-10", "UNKNOWN"),
        }
        for label, (mutate, krx, us) in variants.items():
            with self.subTest(variant=label):
                packet = copy.deepcopy(base)
                mutate(packet)
                self.assertAgree(packet, krx, us)

        mixed = copy.deepcopy(base)
        _component(mixed, "STEP0_READ_MODEL_HEALTH")["packet"]["sources"]["dart"]["collected_for_kst_date"] = "2026-09-10"
        self.assertEqual(ORCH.weekend_session_context_dates(mixed)["source_evidence_kst_date"], "UNKNOWN")


def _consume(decision_date: str, contract: dict, get):
    """consume() on real retained packets, with full packet validation."""
    return CONSUMER.consume(
        decision_date, "morning", {}, contract=contract, get=get, nonce_factory=lambda: "nonce",
    )


def _serve_from_git(repo: Path, envelopes: list[dict]):
    """HTTP stand-in: bootstrap URLs return the given envelopes, immutable URLs git bytes."""
    responses = {
        envelope["bootstrap_url"]: (200, (json.dumps(envelope, sort_keys=True) + "\n").encode("utf-8"))
        for envelope in envelopes
    }
    immutable = re.compile(r"^https://raw\.githubusercontent\.com/yonggeun1021-hub/atlas-data/([0-9a-f]{40})/(.+)$")

    def get(url: str):
        parsed = urlsplit(url)
        clean = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        if clean in responses:
            return responses[clean]
        match = immutable.match(clean)
        if match and "/main/" not in clean:
            try:
                return 200, _git("show", f"{match.group(1)}:{match.group(2)}", cwd=repo)
            except AssertionError:
                return 404, b""
        return 404, b""

    return get


class RetainedV3SlotTests(unittest.TestCase):
    """Old sealed slots keep validating under the contract version they were sealed with."""

    def _envelopes(self, decision_date: str) -> list[dict]:
        rows = sorted(
            (slot for slot in FIXTURE["slots"] if slot["decision_date"] == decision_date),
            key=lambda slot: slot["revision"],
        )
        return [json.loads(_retained(slot, "retrieval_envelope")) for slot in rows]

    def test_retained_envelopes_validate_under_v3(self):
        for slot in FIXTURE["slots"]:
            envelope = json.loads(_retained(slot, "retrieval_envelope"))
            with self.subTest(slot=(slot["decision_date"], slot["revision"])):
                self.assertEqual(envelope["schema_version"], V3)
                self.assertEqual(slot["retrieval_envelope_schema_version"], V3)
                contract = json.loads(_git("show", f"{envelope['source_commit']}:{PUBLISHER.CONTRACT_PATH}"))
                self.assertEqual(contract["schema_version"], V3)
                PUBLISHER.validate_envelope(ROOT, envelope)

    def test_retained_envelopes_consume_under_v3_with_the_v4_consumer(self):
        contract = CONSUMER._load_contract()
        for decision_date in ("2026-09-12", "2026-09-13"):
            envelopes = self._envelopes(decision_date)
            with self.subTest(decision_date=decision_date):
                raw, envelope = _consume(decision_date, contract, _serve_from_git(ROOT, envelopes))
                self.assertEqual(envelope, envelopes[-1])
                briefing = raw[envelope["delivery_locator"]["briefing_path"]].decode("utf-8")
                self.assertIn("- latest_confirmed_evidence_date: 2026-09-11\n", briefing)

    def test_v3_envelope_relabelled_as_v4_is_rejected(self):
        contract = CONSUMER._load_contract()
        for decision_date in ("2026-09-12", "2026-09-13"):
            envelopes = self._envelopes(decision_date)
            relabelled = [dict(envelope, schema_version=V4) for envelope in envelopes]
            with self.subTest(decision_date=decision_date):
                with self.assertRaisesRegex(PUBLISHER.ScheduledAuthorityError, "ENVELOPE_DRIFT_OR_TAMPER"):
                    PUBLISHER.validate_envelope(ROOT, relabelled[-1])
                with self.assertRaisesRegex(
                    CONSUMER.ScheduledConsumerError, "WEEKEND_BRIEFING_AMBIGUOUS_EVIDENCE_DATE_LINE"
                ):
                    _consume(decision_date, contract, _serve_from_git(ROOT, relabelled))


class V4PublishConsumeRealSlotTests(unittest.TestCase):
    """A real weekend slot re-rendered and published under v4 in a scratch repository."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        _git("init", "-q", str(self.repo), cwd=self.repo)
        _git("config", "user.name", "test", cwd=self.repo)
        _git("config", "user.email", "test@example.com", cwd=self.repo)

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, relative: str, raw: bytes) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def _commit(self, slot: dict, packet: dict, briefing: str, *, include_krx_blob: bool = True) -> str:
        decision_date = slot["decision_date"]
        for relative in (str(PUBLISHER.CONTRACT_PATH), "config/read_model_authority_contract.json"):
            self._write(relative, (ROOT / relative).read_bytes())
        for relative in ("data/briefing/step0_status.json", "data/briefing_status.json"):
            self._write(relative, _git("show", f"{slot['retrieval_envelope_source_commit']}:{relative}"))
        krx_path = self.repo / "data/latest_krx.json"
        if include_krx_blob:
            self._write("data/latest_krx.json", _git("cat-file", "blob", slot["latest_krx_at_seal"]["source_git_blob_sha1"]))
        elif krx_path.exists():
            krx_path.unlink()
        base = f"evidence/daily_briefing/morning/{decision_date}"
        packet_path = self._write(f"{base}/rev-001/packet.json", (json.dumps(packet, indent=2, sort_keys=True) + "\n").encode())
        briefing_path = self._write(f"{base}/rev-001/briefing.md", briefing.encode("utf-8"))
        index_path = self._write(f"{base}/index.json", (json.dumps({
            "schema_version": 1, "slot": "morning", "decision_date": decision_date, "latest_revision": 1,
            "revisions": [{"revision": 1, "path": "rev-001", "packet_sha256": packet["packet_sha256"]}],
        }, indent=2, sort_keys=True) + "\n").encode())
        self._write("data/briefing/daily_briefing_sources.json", (json.dumps({
            "schema_version": "daily_briefing_delivery/1", "slot": "morning", "decision_date": decision_date,
            "revision": 1,
            "index_path": f"{base}/index.json", "index_sha256": _sha256(index_path.read_bytes()),
            "packet_path": f"{base}/rev-001/packet.json", "packet_file_sha256": _sha256(packet_path.read_bytes()),
            "packet_sha256": packet["packet_sha256"],
            "briefing_path": f"{base}/rev-001/briefing.md", "briefing_sha256": _sha256(briefing_path.read_bytes()),
            "delivery_scope": ["INVESTMENT_DECISION_REVIEW", "INVESTMENT_REVIEW_SHADOW", "SHADOW_ENTRY_REVIEW"],
            "authority": {"stage": False, "buy": False, "action": False, "order": False,
                          "production": False, "trading": False},
        }, indent=2, sort_keys=True) + "\n").encode())
        _git("add", "-A", cwd=self.repo)
        _git("commit", "-q", "--allow-empty", "-m", "weekend v4 slot", cwd=self.repo)
        return _git("rev-parse", "HEAD", cwd=self.repo).decode("ascii").strip()

    def _consume(self, slot: dict, envelope: dict):
        return _consume(
            slot["decision_date"], CONSUMER._load_contract(self.repo / PUBLISHER.CONTRACT_PATH),
            _serve_from_git(self.repo, [envelope]),
        )

    def _unchecked_envelope(self, slot: dict, commit: str) -> dict:
        """An envelope whose hashes match the bytes, built by a publisher that skipped the weekend check."""
        with mock.patch.object(PUBLISHER, "_validate_weekend_delivery_semantics", lambda *args, **kwargs: None):
            return PUBLISHER.build_envelope(self.repo, commit, "morning", slot["decision_date"])

    def test_real_weekend_slots_publish_and_consume_under_v4(self):
        for key, slot in SLOTS.items():
            with self.subTest(slot=key):
                packet = _referenced_packet(slot)
                rendered = ORCH.render_markdown(packet)
                commit = self._commit(slot, packet, rendered)
                envelope = PUBLISHER.build_envelope(self.repo, commit, "morning", slot["decision_date"])
                self.assertEqual(envelope["schema_version"], V4)
                self.assertEqual(envelope["source_date_binding"]["source_evidence_kst_date"], "2026-09-11")
                PUBLISHER.validate_envelope(self.repo, envelope)
                raw, consumed = self._consume(slot, envelope)
                self.assertEqual(consumed, envelope)
                self.assertEqual(raw[envelope["delivery_locator"]["briefing_path"]], rendered.encode("utf-8"))

    def test_briefing_mutations_fail_publisher_and_consumer(self):
        slot = SLOTS[("2026-09-13", 1)]
        packet = _referenced_packet(slot)
        rendered = ORCH.render_markdown(packet)
        krx_line = "- krx_latest_confirmed_close_date: 2026-09-10\n"
        us_line = "- us_latest_verified_session_date: 2026-09-11\n"
        self.assertIn(krx_line, rendered)
        self.assertIn(us_line, rendered)
        cases = (
            ("fabricated KRX confirmed date", "WEEKEND_BRIEFING_SESSION_DATE_MISMATCH: krx_latest_confirmed_close_date",
             rendered.replace(krx_line, "- krx_latest_confirmed_close_date: 2026-09-11\n")),
            ("KRX and US dates swapped", "WEEKEND_BRIEFING_SESSION_DATE_MISMATCH: krx_latest_confirmed_close_date",
             rendered.replace(krx_line, "- krx_latest_confirmed_close_date: 2026-09-11\n").replace(
                 us_line, "- us_latest_verified_session_date: 2026-09-10\n")),
            ("verified US date hidden as UNKNOWN", "WEEKEND_BRIEFING_SESSION_DATE_MISMATCH: us_latest_verified_session_date",
             rendered.replace(us_line, "- us_latest_verified_session_date: UNKNOWN\n")),
            ("old v3 line accepted under v4", "WEEKEND_BRIEFING_AMBIGUOUS_EVIDENCE_DATE_LINE",
             rendered.replace("- new_session: NONE\n", "- new_session: NONE\n- latest_confirmed_evidence_date: 2026-09-11\n")),
            ("sealed v3 briefing bytes under v4", "WEEKEND_BRIEFING_AMBIGUOUS_EVIDENCE_DATE_LINE",
             _retained(slot, "briefing").decode("utf-8")),
            ("market line removed", "WEEKEND_BRIEFING_SESSION_CONTEXT_MISSING: us_latest_verified_session_date",
             rendered.replace(us_line, "")),
            ("market line with trailing claim", "WEEKEND_BRIEFING_SESSION_DATE_MISMATCH: krx_latest_confirmed_close_date",
             rendered.replace(krx_line, "- krx_latest_confirmed_close_date: 2026-09-10 (Friday 2026-09-11 confirmed)\n")),
        )
        for label, expected, briefing in cases:
            with self.subTest(case=label):
                self.assertNotEqual(briefing, rendered)
                commit = self._commit(slot, packet, briefing)
                with self.assertRaisesRegex(PUBLISHER.ScheduledAuthorityError, expected):
                    PUBLISHER.build_envelope(self.repo, commit, "morning", slot["decision_date"])
                envelope = self._unchecked_envelope(slot, commit)
                with self.assertRaisesRegex(CONSUMER.ScheduledConsumerError, expected):
                    self._consume(slot, envelope)

    def test_fabricated_or_unbound_krx_source_fails_the_publisher(self):
        slot = SLOTS[("2026-09-12", 2)]
        base = _referenced_packet(slot)

        # A fresh scratch repository that never held the latest_krx blob.
        commit = self._commit(slot, base, ORCH.render_markdown(base), include_krx_blob=False)
        with self.assertRaisesRegex(PUBLISHER.ScheduledAuthorityError, "WEEKEND_KRX_CONFIRMED_SOURCE_BLOB_MISSING"):
            PUBLISHER.build_envelope(self.repo, commit, "morning", slot["decision_date"])

        # A re-signed packet whose frozen confirmed close is not the blob's value,
        # rendered consistently so every line agrees with the packet.
        fabricated = copy.deepcopy(base)
        fabricated["frozen_sources"]["STEP0_READ_MODEL_HEALTH"][ORCH.PRESENTATION_REFERENCES][
            "krx_confirmed_close"]["confirmed_through"] = "2026-09-11"
        _rehash(fabricated)
        rendered = ORCH.render_markdown(fabricated)
        self.assertIn("- krx_latest_confirmed_close_date: 2026-09-11\n", rendered)
        commit = self._commit(slot, fabricated, rendered)
        with self.assertRaisesRegex(PUBLISHER.ScheduledAuthorityError, "WEEKEND_KRX_CONFIRMED_CLOSE_NOT_SOURCE_VALUE"):
            PUBLISHER.build_envelope(self.repo, commit, "morning", slot["decision_date"])

        # The unmodified packet publishes once the blob is present.
        commit = self._commit(slot, base, ORCH.render_markdown(base))
        PUBLISHER.build_envelope(self.repo, commit, "morning", slot["decision_date"])

    def test_unbound_sources_render_and_validate_as_unknown(self):
        slot = SLOTS[("2026-09-12", 1)]
        packet = _referenced_packet(slot)
        packet["frozen_sources"]["STEP0_READ_MODEL_HEALTH"][ORCH.PRESENTATION_REFERENCES][
            "krx_confirmed_close"]["source_sha256"] = "0" * 64
        previous_status = _component(packet, "FREE_MARKET_DATA")["status"]
        _component(packet, "FREE_MARKET_DATA")["status"] = "DEGRADED"
        packet["component_status_counts"][previous_status] -= 1
        packet["component_status_counts"]["DEGRADED"] += 1
        _rehash(packet)
        rendered = ORCH.render_markdown(packet)
        self.assertIn("- krx_latest_confirmed_close_date: UNKNOWN\n", rendered)
        self.assertIn("- us_latest_verified_session_date: UNKNOWN\n", rendered)
        commit = self._commit(slot, packet, rendered)
        envelope = PUBLISHER.build_envelope(self.repo, commit, "morning", slot["decision_date"])
        self._consume(slot, envelope)
        # Showing the dates anyway is a fabricated claim for both sides.
        claimed = rendered.replace(
            "- us_latest_verified_session_date: UNKNOWN\n", "- us_latest_verified_session_date: 2026-09-11\n"
        )
        commit = self._commit(slot, packet, claimed)
        with self.assertRaisesRegex(PUBLISHER.ScheduledAuthorityError, "WEEKEND_BRIEFING_SESSION_DATE_MISMATCH"):
            PUBLISHER.build_envelope(self.repo, commit, "morning", slot["decision_date"])
        with self.assertRaisesRegex(CONSUMER.ScheduledConsumerError, "WEEKEND_BRIEFING_SESSION_DATE_MISMATCH"):
            self._consume(slot, self._unchecked_envelope(slot, commit))


if __name__ == "__main__":
    unittest.main()
