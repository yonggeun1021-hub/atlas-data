#!/usr/bin/env python3
"""Briefing content recency regression (CLAUDE_CIO briefing audit 2026-09-14, Task B).

Every fixture is a real audited slot: the sealed packets 2026-09-10 AM/PM,
2026-09-11 AM/PM, 2026-09-12 AM and 2026-09-13 AM already committed under
evidence/daily_briefing, plus the exact data/latest_krx.json fields and PAPER
reference pointer generation present at each finalization seal commit
(test/fixtures/briefing_content_recency_20260914/audited_slots.json).

Covers the five producer fixes:
  1. KRX latest confirmed close binds to latest_krx decision_readiness.
  2. Weekend mornings show Friday's recorded session, not only 09-10.
  3. The PAPER regime reference is rendered with its dates and a
     non-authority label.
  4. Undated rows carry a 기준일; only the ratified KR session rule labels
     staleness (no invented windows).
  5. KOSPI/KOSDAQ one-session moves are recomputable from retained raw bytes.
"""

from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from briefing_core import chain  # noqa: E402


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ORCH = _load("briefing_content_recency_orchestrator", "briefing/daily_orchestrator.py")
RECOMPUTE = _load("briefing_content_recency_index_recompute", "validation/korea_index_move_recompute.py")
FIXTURE = json.loads(
    (ROOT / "test/fixtures/briefing_content_recency_20260914/audited_slots.json").read_text(
        encoding="utf-8"
    )
)
SLOTS = {(row["decision_date"], row["slot"]): row for row in FIXTURE["slots"]}


def _instant(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _audited_packet(decision_date: str, slot: str) -> tuple[dict, dict]:
    fixture = SLOTS[(decision_date, slot)]
    packet = json.loads((ROOT / fixture["packet_path"]).read_text(encoding="utf-8"))
    return packet, fixture


def _with_references(decision_date: str, slot: str) -> tuple[dict, dict]:
    """The audited packet with the references its build would have frozen.

    Goes through the real replay derivation: only the selections are supplied
    (latest_krx raw fields at the seal, the post-close bundle selected from the
    immutable archive, the PAPER pointer generation at the seal); every
    displayed field is re-derived from retained bytes.
    """
    packet, fixture = _audited_packet(decision_date, slot)
    generated = _instant(packet["generated_at"])
    step0 = copy.deepcopy(packet["frozen_sources"]["STEP0_READ_MODEL_HEALTH"])
    frozen = {
        "krx_confirmed_close": {
            "source_git_blob_sha1": fixture["latest_krx_at_seal"]["source_git_blob_sha1"]
        },
        "krx_post_close": {
            "selected_date": ORCH._select_krx_post_close(ORCH.ROOT, decision_date, generated)
        },
        "paper_regime": {
            "evidence_path": fixture["paper_reference_pointer_at_seal"]["evidence_path"]
        },
    }
    step0[ORCH.PRESENTATION_REFERENCES] = frozen
    packet["frozen_sources"]["STEP0_READ_MODEL_HEALTH"] = ORCH._presentation_references_snapshot(
        step0, decision_date, generated, root=None
    )
    packet.pop("packet_sha256")
    packet["packet_sha256"] = ORCH.payload_sha256(packet)
    return packet, fixture


def _pinned_fresh_build(decision_date: str, slot: str) -> dict:
    """A real fresh ``build_packet`` for an audited slot, pinned to its seal.

    A fresh build reads live collector state "now": the STEP0 read model and
    the rolling pointers data/latest_krx.json and
    data/latest_paper_regime_reference.json. Building a past slot against the
    live tree therefore changes whenever the daily collect rewrites those
    files (STEP0 becomes SOURCE_GENERATED_AT_AFTER_PACKET_GENERATED_AT).
    Here exactly those reads return the audited seal-time inputs: the STEP0
    snapshot frozen in the sealed packet, the latest_krx git blob recorded at
    the seal, and the retained PAPER pointer generation at the seal. The
    capture, selection, freeze, classify and validate code runs unmodified.
    """
    sealed, fixture = _audited_packet(decision_date, slot)
    step0_at_seal = copy.deepcopy(sealed["frozen_sources"]["STEP0_READ_MODEL_HEALTH"])
    seal_krx = fixture["latest_krx_at_seal"]
    krx_raw = ORCH._git_blob_bytes(ORCH.ROOT, seal_krx["source_git_blob_sha1"])
    if krx_raw is None or ORCH.hashlib.sha256(krx_raw).hexdigest() != seal_krx["source_sha256"]:
        raise AssertionError(f"seal-time latest_krx blob unavailable: {seal_krx['source_git_blob_sha1']}")
    pointer = fixture["paper_reference_pointer_at_seal"]
    pointer_raw = (ROOT / pointer["evidence_path"]).read_bytes()
    if ORCH.hashlib.sha256(pointer_raw).hexdigest() != pointer["sha256"]:
        raise AssertionError(f"retained PAPER pointer generation rewritten: {pointer['evidence_path']}")
    capture_krx = ORCH._capture_krx_confirmed_close
    select_paper = ORCH._select_paper_regime_reference
    with tempfile.TemporaryDirectory() as temp:
        seal_root = Path(temp)
        (seal_root / ORCH.KRX_CONFIRMED_SOURCE_PATH).parent.mkdir(parents=True, exist_ok=True)
        (seal_root / ORCH.KRX_CONFIRMED_SOURCE_PATH).write_bytes(krx_raw)
        (seal_root / ORCH.PAPER_REGIME_REFERENCE_POINTER_PATH).write_bytes(pointer_raw)
        (seal_root / pointer["evidence_path"]).parent.mkdir(parents=True)
        (seal_root / pointer["evidence_path"]).write_bytes(pointer_raw)

        def step0_snapshot(requested_date):
            if requested_date != decision_date:
                raise AssertionError(f"unexpected STEP0 read for {requested_date}")
            return copy.deepcopy(step0_at_seal)

        with mock.patch.object(ORCH, "_fetch_step0_snapshot", step0_snapshot), mock.patch.object(
            ORCH, "_capture_krx_confirmed_close", lambda root, repository_root: capture_krx(seal_root, repository_root)
        ), mock.patch.object(
            ORCH, "_select_paper_regime_reference", lambda root, generated_at_dt: select_paper(seal_root, generated_at_dt)
        ):
            return ORCH.build_packet(slot, decision_date, sealed["generated_at"])


def _board(packet: dict) -> str:
    by_id = {row["component_id"]: row for row in packet["components"]}
    return "\n".join(ORCH._market_session_freshness_lines(packet, by_id))


def _field(text: str, name: str) -> str:
    """The machine value of a board line: text before the first ';' (the portal
    parser reads exactly this span), without the Korean gloss after it."""
    prefix = f"- {name}: "
    line = next(line for line in text.splitlines() if line.startswith(prefix))[len(prefix):]
    return line.split(";", 1)[0].strip()


class AuditedFixtureIdentityTests(unittest.TestCase):
    def test_fixture_bytes_are_the_sealed_read_model_bytes(self):
        self.assertEqual(
            FIXTURE["audit_sha256"],
            "ebd75026f05ee63e6ecfc5c6771b1230b1ec5eea4dac2d34eb94fb698d7df249",
        )
        for (decision_date, slot), fixture in SLOTS.items():
            with self.subTest(slot=f"{decision_date}/{slot}"):
                packet, _ = _audited_packet(decision_date, slot)
                sources = packet["frozen_sources"]["STEP0_READ_MODEL_HEALTH"]["value"]["sources"]
                self.assertEqual(
                    fixture["latest_krx_at_seal"]["source_sha256"],
                    sources["krx"]["source_sha256"],
                )
                self.assertNotIn(
                    ORCH.PRESENTATION_REFERENCES,
                    packet["frozen_sources"]["STEP0_READ_MODEL_HEALTH"],
                )

    def test_retained_briefings_carry_the_audited_defects(self):
        cases = {
            ("2026-09-10", "morning"): "- latest_confirmed_close_date: 2026-09-08",
            ("2026-09-11", "morning"): "- latest_confirmed_close_date: 2026-09-09",
            ("2026-09-12", "morning"): "- latest_observed_unconfirmed_date: UNKNOWN",
            ("2026-09-13", "morning"): "- latest_observed_unconfirmed_date: UNKNOWN",
        }
        for (decision_date, slot), defect in cases.items():
            with self.subTest(slot=f"{decision_date}/{slot}"):
                fixture = SLOTS[(decision_date, slot)]
                briefing = (ROOT / fixture["packet_path"]).with_name("briefing.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn(defect, briefing)


class KrxSessionRecencyTests(unittest.TestCase):
    """Fix 1 and Fix 2."""

    EXPECTED = {
        # slot: (confirmed close, observed-unconfirmed, completed session, status)
        ("2026-09-10", "morning"): ("2026-09-09", "UNKNOWN", "2026-09-09", "CONFIRMED"),
        ("2026-09-10", "evening"): ("2026-09-09", "2026-09-10", "2026-09-10", "OBSERVED_UNCONFIRMED"),
        ("2026-09-11", "morning"): ("2026-09-10", "UNKNOWN", "2026-09-10", "CONFIRMED"),
        ("2026-09-11", "evening"): ("2026-09-10", "2026-09-11", "2026-09-11", "OBSERVED_UNCONFIRMED"),
        ("2026-09-12", "morning"): ("2026-09-10", "2026-09-11", "2026-09-11", "OBSERVED_UNCONFIRMED"),
        ("2026-09-13", "morning"): ("2026-09-10", "2026-09-11", "2026-09-11", "OBSERVED_UNCONFIRMED"),
    }

    def test_confirmed_close_binds_to_latest_krx_not_five_axis_date(self):
        for (decision_date, slot), expected in self.EXPECTED.items():
            confirmed, observed, completed, status = expected
            with self.subTest(slot=f"{decision_date}/{slot}"):
                packet, fixture = _with_references(decision_date, slot)
                board = _board(packet)
                self.assertEqual(_field(board, "latest_confirmed_close_date"), confirmed)
                self.assertEqual(_field(board, "latest_observed_unconfirmed_date"), observed)
                self.assertEqual(
                    _field(board, "latest_completed_session_date"), f"{completed} ({status})"
                )
                self.assertIn(f"evidence_date={confirmed}", board)
                self.assertIn(
                    "- latest_confirmed_close_basis: data/latest_krx.json "
                    "decision_readiness.confirmed_through",
                    board,
                )
                # The five-axis date stays visible under its own name.
                self.assertTrue(
                    _field(board, "index_move_observation_date").startswith(
                        fixture["korea_market_signals_as_of_at_seal"]
                    )
                )

    def test_weekday_morning_lag_cases_are_fixed(self):
        packet, fixture = _with_references("2026-09-10", "morning")
        board = _board(packet)
        self.assertEqual(fixture["korea_market_signals_as_of_at_seal"], "2026-09-08")
        self.assertNotIn("latest_confirmed_close_date: 2026-09-08", board)
        self.assertIn(
            "index_move_observation_date: 2026-09-08; freshness=SOURCE_NOT_ADVANCED_EXPECTED_SESSION",
            board,
        )
        # Same date the retained Dynamic Clock KOREA price rows already used.
        dynamic = next(
            row for row in packet["components"] if row["component_id"] == "DYNAMIC_CLOCK"
        )
        korea_dates = {
            candidate.get("price_observation_date")
            for candidate in dynamic["packet"]["markets"]["KOREA"]["watch_review"]
        }
        self.assertEqual(korea_dates, {_field(board, "latest_confirmed_close_date")})

    def test_weekend_shows_friday_session_and_scopes_the_step0_date(self):
        for decision_date in ("2026-09-12", "2026-09-13"):
            with self.subTest(decision_date=decision_date):
                packet, _ = _with_references(decision_date, "morning")
                rendered = ORCH.render_markdown(packet)
                # scheduled_briefing_retrieval_authority/4 weekend contract
                # lines: the STEP0 collector date and each market's own date
                # are named separately; the ambiguous v3 line is gone.
                for line in (
                    "- market_session: MARKET_CLOSED",
                    "- new_session: NONE",
                    "- source_evidence_kst_date: 2026-09-11",
                    "- krx_latest_confirmed_close_date: 2026-09-10",
                    "- us_latest_verified_session_date: 2026-09-11",
                    "- latest_confirmed_evidence_relabelled_as_today: false",
                ):
                    self.assertIn(line + "\n", rendered)
                self.assertNotIn("- latest_confirmed_evidence_date:", rendered)
                self.assertIn(
                    "- source_evidence_kst_date_scope: STEP0 read-model collector run KST date, "
                    "not a market session date",
                    rendered,
                )
                self.assertIn(
                    "- krx_latest_completed_session_date: 2026-09-11 "
                    "(OBSERVED_UNCONFIRMED; confirmed close 2026-09-10)",
                    rendered,
                )
                # Korean glosses for the machine labels.
                self.assertIn("- latest_confirmed_close_date: 2026-09-10; 거래소 확정 종가", rendered)
                self.assertIn(
                    "- latest_observed_unconfirmed_date: 2026-09-11; 관측·미확정(거래소 확정 전)",
                    rendered,
                )
                self.assertIn(
                    "- latest_completed_session_date: 2026-09-11 (OBSERVED_UNCONFIRMED); "
                    "최근 완료 거래일 · 관측·미확정(거래소 확정 전)",
                    rendered,
                )
                self.assertNotIn("- latest_observed_unconfirmed_date: UNKNOWN", rendered)
                self.assertIn(
                    "freshness=SOURCE_NOT_ADVANCED_EXPECTED_SESSION (KOSPI/KOSDAQ one-session "
                    "moves after 2026-09-10 through 2026-09-11 are not yet observed",
                    rendered,
                )

    def test_confirmed_close_requires_the_step0_bytes(self):
        packet, _ = _with_references("2026-09-11", "morning")
        references = packet["frozen_sources"]["STEP0_READ_MODEL_HEALTH"][ORCH.PRESENTATION_REFERENCES]
        references["krx_confirmed_close"]["source_sha256"] = "0" * 64
        context = ORCH.krx_session_context(packet)
        self.assertIsNone(context["latest_confirmed_close_date"])
        self.assertEqual(
            context["latest_confirmed_close_unknown_reason"], "KRX_CONFIRMED_SOURCE_NOT_STEP0_BYTES"
        )
        self.assertIn("latest_confirmed_close_date: UNKNOWN", _board(packet))

    def test_temporal_guards_fail_closed(self):
        generated = _instant("2026-09-09T22:59:53Z")
        seal = SLOTS[("2026-09-11", "morning")]["latest_krx_at_seal"]
        frozen = {"source_git_blob_sha1": seal["source_git_blob_sha1"]}
        kwargs = {"step0_source_sha256": seal["source_sha256"], "repository_root": ORCH.ROOT}
        reference = ORCH._krx_confirmed_close_reference(frozen, "2026-09-09", generated, **kwargs)
        self.assertEqual(reference["unknown_reason"], "KRX_CONFIRMED_THROUGH_AFTER_DECISION_DATE")
        reference = ORCH._krx_confirmed_close_reference(frozen, "2026-09-11", generated, **kwargs)
        self.assertEqual(
            reference["unknown_reason"], "KRX_CONFIRMED_SOURCE_COLLECTED_AFTER_GENERATION"
        )
        # A post-close bundle collected after generation is never selected.
        self.assertEqual(
            ORCH._select_krx_post_close(ORCH.ROOT, "2026-09-10", generated), "2026-09-09"
        )
        late = ORCH._krx_post_close_reference(ORCH.ROOT, "2026-09-10", "2026-09-10", generated)
        self.assertEqual(late["unknown_reason"], "POST_CLOSE_COLLECTED_AFTER_GENERATION")

    def test_legacy_packets_render_unchanged_board(self):
        packet, _ = _audited_packet("2026-09-10", "morning")
        board = _board(packet)
        briefing = (ROOT / SLOTS[("2026-09-10", "morning")]["packet_path"]).with_name(
            "briefing.md"
        ).read_text(encoding="utf-8")
        self.assertIn(board, briefing)


class PresentationReferenceFreezeTests(unittest.TestCase):
    def test_replay_is_deterministic_and_rederives_displayed_fields(self):
        packet, _ = _with_references("2026-09-13", "morning")
        snapshot = packet["frozen_sources"]["STEP0_READ_MODEL_HEALTH"]
        generated = _instant(packet["generated_at"])
        replayed = ORCH._presentation_references_snapshot(
            copy.deepcopy(snapshot), "2026-09-13", generated, root=None
        )
        self.assertEqual(replayed, snapshot)
        tampered = copy.deepcopy(snapshot)
        tampered[ORCH.PRESENTATION_REFERENCES]["krx_post_close"]["latest_observed_day"] = "2026-09-12"
        tampered[ORCH.PRESENTATION_REFERENCES]["paper_regime"]["markets"][0]["candidate_regime"] = "RISK_ON"
        self.assertEqual(
            ORCH._presentation_references_snapshot(tampered, "2026-09-13", generated, root=None),
            snapshot,
        )

    def test_legacy_snapshot_replays_byte_identical(self):
        packet, _ = _audited_packet("2026-09-13", "morning")
        snapshot = packet["frozen_sources"]["STEP0_READ_MODEL_HEALTH"]
        self.assertIs(
            ORCH._presentation_references_snapshot(
                snapshot, "2026-09-13", _instant(packet["generated_at"]), root=None
            ),
            snapshot,
        )

    def test_fresh_capture_reads_pointers_from_the_given_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = SLOTS[("2026-09-11", "morning")]
            pointer = fixture["paper_reference_pointer_at_seal"]["evidence_path"]
            (root / pointer).parent.mkdir(parents=True)
            shutil.copyfile(ROOT / pointer, root / pointer)
            (root / "data").mkdir()
            shutil.copyfile(ROOT / pointer, root / ORCH.PAPER_REGIME_REFERENCE_POINTER_PATH)
            generated = _instant("2026-09-10T22:58:40Z")
            self.assertEqual(ORCH._select_paper_regime_reference(root, generated), pointer)
            # Generated after the briefing: nothing is selected.
            self.assertIsNone(
                ORCH._select_paper_regime_reference(root, _instant("2026-09-10T06:00:00Z"))
            )
            # Pointer bytes not retained identically: nothing is selected.
            (root / pointer).write_bytes((ROOT / pointer).read_bytes() + b" ")
            self.assertIsNone(ORCH._select_paper_regime_reference(root, generated))

            krx = {"collected_at_utc": "2026-09-10T21:00:33+00:00",
                   "decision_readiness": {"confirmed_through": "2026-09-10"}}
            (root / "data" / "latest_krx.json").write_bytes(json.dumps(krx).encode())
            # Bytes the trusted repository never committed freeze nothing.
            self.assertEqual(
                ORCH._capture_krx_confirmed_close(root, ORCH.ROOT), {"source_git_blob_sha1": None}
            )
            committed = (ROOT / "data/latest_krx.json").read_bytes()
            (root / "data" / "latest_krx.json").write_bytes(committed)
            oid = ORCH._git_blob_oid(committed)
            if ORCH._git_blob_bytes(ORCH.ROOT, oid) is not None:
                self.assertEqual(
                    ORCH._capture_krx_confirmed_close(root, ORCH.ROOT), {"source_git_blob_sha1": oid}
                )
            self.assertIsNone(ORCH._select_krx_post_close(root, "2026-09-11", generated))


class FreshBuildReplayTests(unittest.TestCase):
    """A real fresh build freezes the references and validate_packet replays them."""

    def test_fresh_weekend_build_round_trips_and_tamper_fails(self):
        packet = _pinned_fresh_build("2026-09-13", "morning")
        self.assertEqual(packet["generated_at"], "2026-09-12T22:15:36Z")
        references = ORCH.presentation_references(packet)
        self.assertIsNotNone(references)
        self.assertEqual(references["scope"], "PRESENTATION_ONLY_NOT_A_COMPONENT_INPUT")
        self.assertEqual(references["krx_post_close"]["selected_date"], "2026-09-11")
        self.assertIsNone(references["krx_post_close"]["unknown_reason"])
        ORCH.validate_packet(packet)
        # Presentation references never change a component row.
        legacy_step0 = {
            key: value
            for key, value in packet["frozen_sources"]["STEP0_READ_MODEL_HEALTH"].items()
            if key != ORCH.PRESENTATION_REFERENCES
        }
        legacy_sources = copy.deepcopy(packet["frozen_sources"])
        legacy_sources["STEP0_READ_MODEL_HEALTH"] = legacy_step0
        legacy = ORCH.build_packet(
            "morning", "2026-09-13", "2026-09-12T22:15:36Z", frozen_sources=legacy_sources
        )
        self.assertEqual(legacy["components"], packet["components"])
        self.assertIsNone(ORCH.presentation_references(legacy))

        tampered = copy.deepcopy(packet)
        tampered["frozen_sources"]["STEP0_READ_MODEL_HEALTH"][ORCH.PRESENTATION_REFERENCES][
            "krx_post_close"
        ]["latest_observed_day"] = "2026-09-12"
        tampered.pop("packet_sha256")
        tampered["packet_sha256"] = ORCH.payload_sha256(tampered)
        with self.assertRaisesRegex(ORCH.DailyOrchestratorError, "OUTPUT_MISMATCH"):
            ORCH.validate_packet(tampered)


class ConfirmedCloseValidatorBindingTests(unittest.TestCase):
    """CIO review #722 blocking 1: the confirmed-close date is validator-bound."""

    @classmethod
    def setUpClass(cls):
        cls.packet = _pinned_fresh_build("2026-09-13", "morning")

    def _rehashed(self, mutate) -> dict:
        tampered = copy.deepcopy(self.packet)
        mutate(tampered["frozen_sources"]["STEP0_READ_MODEL_HEALTH"][ORCH.PRESENTATION_REFERENCES])
        tampered.pop("packet_sha256")
        tampered["packet_sha256"] = ORCH.payload_sha256(tampered)
        return tampered

    def test_fresh_build_freezes_committed_blob(self):
        confirmed = ORCH.presentation_references(self.packet)["krx_confirmed_close"]
        step0 = next(
            row for row in self.packet["components"]
            if row["component_id"] == "STEP0_READ_MODEL_HEALTH"
        )
        seal = SLOTS[("2026-09-13", "morning")]["latest_krx_at_seal"]
        # The seal-time STEP0 row is reproduced (weekend: DATA_BLOCKED with its payload).
        sealed, _ = _audited_packet("2026-09-13", "morning")
        sealed_step0 = next(
            row for row in sealed["components"] if row["component_id"] == "STEP0_READ_MODEL_HEALTH"
        )
        self.assertEqual(step0, sealed_step0)
        self.assertEqual(confirmed["source_git_blob_sha1"], seal["source_git_blob_sha1"])
        raw = ORCH._git_blob_bytes(ORCH.ROOT, confirmed["source_git_blob_sha1"])
        self.assertEqual(ORCH.hashlib.sha256(raw).hexdigest(), confirmed["source_sha256"])
        self.assertEqual(confirmed["source_sha256"], step0["packet"]["sources"]["krx"]["source_sha256"])
        self.assertIsNone(confirmed["unknown_reason"])
        self.assertEqual(confirmed["confirmed_through"], seal["confirmed_through"])
        self.assertEqual(
            ORCH.presentation_references(self.packet)["paper_regime"]["evidence_path"],
            SLOTS[("2026-09-13", "morning")]["paper_reference_pointer_at_seal"]["evidence_path"],
        )
        ORCH.validate_packet(self.packet)

    def test_altered_confirmed_through_fails_validation(self):
        confirmed = ORCH.presentation_references(self.packet)["krx_confirmed_close"]
        if confirmed["source_git_blob_sha1"] is None:
            self.skipTest("working-tree latest_krx.json is not a committed blob")
        for field, value in (
            ("confirmed_through", "2026-09-12"),
            ("collected_at_utc", "2026-09-11T21:00:00+00:00"),
            ("source_sha256", "0" * 64),
            ("unknown_reason", None if confirmed["unknown_reason"] else "X"),
        ):
            with self.subTest(field=field):
                tampered = self._rehashed(
                    lambda refs: refs["krx_confirmed_close"].__setitem__(field, value)
                )
                # Validation fails, so no ledger is ever built from the tampered packet.
                with self.assertRaisesRegex(ORCH.DailyOrchestratorError, "OUTPUT_MISMATCH"):
                    ORCH.validate_packet(tampered)

    def test_missing_or_foreign_blob_fails_or_is_unbound(self):
        missing = self._rehashed(
            lambda refs: refs["krx_confirmed_close"].__setitem__("source_git_blob_sha1", "f" * 40)
        )
        with self.assertRaisesRegex(
            ORCH.DailyOrchestratorError, "PRESENTATION_KRX_CONFIRMED_SOURCE_BLOB_MISSING"
        ):
            ORCH.validate_packet(missing)
        # A real committed blob whose bytes are not the STEP0 gate's bytes never
        # yields a confirmed date.
        foreign_oid = SLOTS[("2026-09-10", "morning")]["latest_krx_at_seal"]["source_git_blob_sha1"]
        reference = ORCH._krx_confirmed_close_reference(
            {"source_git_blob_sha1": foreign_oid},
            "2026-09-13",
            _instant("2026-09-12T22:15:36Z"),
            step0_source_sha256=SLOTS[("2026-09-13", "morning")]["latest_krx_at_seal"]["source_sha256"],
            repository_root=ORCH.ROOT,
        )
        self.assertEqual(reference["unknown_reason"], "KRX_CONFIRMED_SOURCE_NOT_STEP0_BYTES")


class ClaimLedgerBoardAgreementTests(unittest.TestCase):
    """CIO review #722 blocking 2 and mutation M11."""

    def test_board_and_ledger_dates_agree(self):
        for (decision_date, slot) in SLOTS:
            with self.subTest(slot=f"{decision_date}/{slot}"):
                packet, _ = _with_references(decision_date, slot)
                board = _board(packet)
                claims = {
                    claim["claim_id"]: claim["statement"]
                    for claim in chain._delivery_claims(packet, "packet.json")
                }
                self.assertNotIn("freshness.krx.latest_confirmed_close_date", claims)
                self.assertIn(
                    f"through {_field(board, 'latest_confirmed_close_date')}.",
                    claims["freshness.krx.latest_confirmed_session_date"],
                )
                self.assertIn(
                    f"dated {_field(board, 'index_move_observation_date')}.",
                    claims["freshness.krx.index_move_observation_date"],
                )
                self.assertIn(
                    _field(board, "latest_completed_session_date").split(" ")[0],
                    claims["freshness.krx.latest_completed_session_date"],
                )
        legacy, _ = _audited_packet("2026-09-13", "morning")
        legacy_claims = {claim["claim_id"] for claim in chain._delivery_claims(legacy, "packet.json")}
        self.assertIn("freshness.krx.latest_confirmed_close_date", legacy_claims)
        self.assertNotIn("freshness.krx.index_move_observation_date", legacy_claims)

    def test_ledger_requires_step0_hash_binding(self):
        packet, _ = _with_references("2026-09-11", "morning")
        references = packet["frozen_sources"]["STEP0_READ_MODEL_HEALTH"][ORCH.PRESENTATION_REFERENCES]
        references["krx_confirmed_close"]["source_sha256"] = "0" * 64
        self.assertIsNone(references["krx_confirmed_close"]["unknown_reason"])
        statements = dict(chain._presentation_reference_statements(packet))
        self.assertNotIn("freshness.krx.latest_confirmed_session_date", statements)
        self.assertNotIn("confirmed 2026-09-10 session", statements.get(
            "freshness.krx.latest_completed_session_date", ""
        ))


class PaperRegimeReferenceTests(unittest.TestCase):
    """Fix 3."""

    def test_risk_off_reference_is_shown_with_dates_and_label(self):
        for decision_date, slot in (("2026-09-10", "evening"), ("2026-09-11", "morning")):
            with self.subTest(slot=f"{decision_date}/{slot}"):
                packet, _ = _with_references(decision_date, slot)
                rendered = ORCH.render_markdown(packet)
                regime = rendered[rendered.index("## 1. Regime"):rendered.index("## 2. Cross-Market Flow")]
                self.assertIn("- PAPER 참고 판정 (런타임 판정 아님 · 매매/주문 권한 없음)", regime)
                self.assertIn("reference_generated_at=2026-09-10T06:58:16Z", regime)
                self.assertIn(
                    "  - US: PAPER 참고 판정=RISK_OFF score=-3 confidence=0.6 기준일=2026-09-09 "
                    "coverage=5/5 runtime_regime=UNKNOWN",
                    regime,
                )
                self.assertIn(
                    "KR: PAPER 참고 판정=NEUTRAL score=-2 confidence=0.2 기준일=2026-09-08 "
                    "freshness=SOURCE_NOT_ADVANCED_EXPECTED_SESSION",
                    regime,
                )
                # Runtime authority is unchanged everywhere else.
                self.assertIn("US: regime=UNKNOWN", rendered)

    def test_non_authority_and_self_hash_guards(self):
        path = SLOTS[("2026-09-10", "evening")]["paper_reference_pointer_at_seal"]["evidence_path"]
        generated = _instant("2026-09-10T09:41:36Z")
        self.assertIsNone(ORCH._paper_regime_reference(ORCH.ROOT, path, generated)["unknown_reason"])
        self.assertEqual(
            ORCH._paper_regime_reference(ORCH.ROOT, "../etc/packet.json", generated)["unknown_reason"],
            "PAPER_REFERENCE_SELECTION_INVALID",
        )
        self.assertEqual(
            ORCH._paper_regime_reference(ORCH.ROOT, path, _instant("2026-09-10T06:00:00Z"))[
                "unknown_reason"
            ],
            "PAPER_REFERENCE_GENERATED_AFTER_GENERATION",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            value = json.loads((ROOT / path).read_text(encoding="utf-8"))
            value["markets"][0]["paper_reference"]["candidate_regime"] = "RISK_ON"
            (root / path).parent.mkdir(parents=True)
            (root / path).write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(
                ORCH._paper_regime_reference(root, path, generated)["unknown_reason"],
                "RETAINED_PAPER_REFERENCE_SELF_HASH_MISMATCH",
            )
            value = json.loads((ROOT / path).read_text(encoding="utf-8"))
            value["authority"]["runtime_regime_authorized"] = True
            value.pop("payload_sha256")
            value["payload_sha256"] = ORCH.payload_sha256(value)
            (root / path).write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(
                ORCH._paper_regime_reference(root, path, generated)["unknown_reason"],
                "PAPER_REFERENCE_CLAIMS_RUNTIME_AUTHORITY",
            )

    def test_claim_ledger_matches_rendered_references(self):
        packet, _ = _with_references("2026-09-11", "morning")
        statements = dict(chain._presentation_reference_statements(packet))
        self.assertIn("through 2026-09-10", statements["freshness.krx.latest_confirmed_session_date"])
        self.assertIn("confirmed 2026-09-10 session", statements["freshness.krx.latest_completed_session_date"])
        self.assertIn("US candidate_regime=RISK_OFF", statements["paper_reference.us.candidate_regime"])
        self.assertIn("not a runtime regime", statements["paper_reference.us.candidate_regime"])
        packet, _ = _with_references("2026-09-13", "morning")
        statements = dict(chain._presentation_reference_statements(packet))
        self.assertIn("the 2026-09-11 session was observed", statements["freshness.krx.latest_completed_session_date"])
        context = ORCH.krx_session_context(packet)
        self.assertEqual(context["latest_completed_session_date"], "2026-09-11")
        legacy, _ = _audited_packet("2026-09-13", "morning")
        self.assertEqual(chain._presentation_reference_statements(legacy), [])
        claims = chain._delivery_claims(packet, "packet.json")
        self.assertIn(
            "paper_reference.us.candidate_regime", {claim["claim_id"] for claim in claims}
        )


class DatedRowTests(unittest.TestCase):
    """Fix 4."""

    def test_rows_carry_observation_dates(self):
        packet, _ = _with_references("2026-09-13", "morning")
        rendered = ORCH.render_markdown(packet)
        self.assertIn("- **FORWARD_ALPHA_REVIEW**: OK · 기준일=2026-08-22", rendered)
        self.assertIn("기준일(pilot_evidence_decision_date)=2026-08-22", rendered)
        self.assertIn(
            "034020.KS: opportunity_state=BLOCKED shadow_action=REJECT comparison_label=BLOCKED "
            "기준일=2026-08-22",
            rendered,
        )
        self.assertIn(
            "published_at=2026-08-05 기준일(retrieved)=2026-08-20", rendered
        )
        self.assertIn(
            "- **OFFICIAL_RELEASE_SUMMARY**: PENDING — "
            "OFFICIAL_FACTS_OBSERVED_INTERPRETATION_AND_RANKING_UNRATIFIED · 기준일=2026-08-20",
            rendered,
        )
        self.assertIn("신규시설투자등 기준일(filing_date)=2026-09-10", rendered)
        self.assertIn("기준일(latest_period_end)=2026-08-31", rendered)
        # The pointer to a separately refreshed (frozen since 09-11) file is gone.
        self.assertNotIn("evidence/operational/dynamic_clock/briefing_section.json", rendered)
        self.assertIn(
            "full list: this revision's packet.json, DYNAMIC_CLOCK markets.CRYPTO.watch_review; "
            "기준일=2026-09-13",
            rendered,
        )
        for line in rendered.splitlines():
            if line.startswith("- **") and "packet" not in line:
                component_id = line[4:line.index("**", 4)]
                row = next(r for r in packet["components"] if r["component_id"] == component_id)
                if row.get("as_of_date"):
                    self.assertIn(f"기준일={row['as_of_date']}", line)

    def test_no_invented_stale_window(self):
        policy = json.loads(
            (ROOT / ORCH.KR_SESSION_FRESHNESS_POLICY_PATH).read_text(encoding="utf-8")
        )
        self.assertEqual(policy["decision"]["status"], "CIO_RATIFIED")
        for axis in ("TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"):
            rule = policy["markets"]["KR"]["session_based_axes"][axis]
            self.assertEqual(rule["freshness_form"], "SESSION_EXACT_MATCH")
            self.assertIsNone(rule["numeric_ttl_seconds"])
            self.assertIn(ORCH.KR_SESSION_NOT_ADVANCED_REASON, rule["not_advanced_rule"])
        packet, _ = _with_references("2026-09-13", "morning")
        rendered = ORCH.render_markdown(packet)
        for line in rendered.splitlines():
            if "FORWARD_ALPHA_REVIEW" in line or "OFFICIAL_RELEASE_SUMMARY" in line or "DART 329180" in line:
                self.assertNotIn("stale", line.lower())
                self.assertNotIn("freshness=", line)


class KoreaIndexMoveRecomputeTests(unittest.TestCase):
    """Fix 5."""

    def test_retained_raw_recomputes_the_audited_portal_values(self):
        packet = json.loads(
            (ROOT / "data/observations/korea_market_signals/2026-09-10/packet.json").read_text(
                encoding="utf-8"
            )
        )
        report = RECOMPUTE.verify_packet(packet)
        self.assertEqual(report["markets"]["KOSPI"]["verdict"], "MATCH")
        self.assertEqual(report["markets"]["KOSPI"]["one_session_return_pct"], "-0.251289")
        self.assertEqual(report["markets"]["KOSDAQ"]["verdict"], "MATCH")
        self.assertEqual(report["markets"]["KOSDAQ"]["one_session_return_pct"], "0.788805")
        self.assertFalse(report["authority"]["trading_authorized"])
        tampered = copy.deepcopy(packet)
        tampered["axes"]["TREND"]["measurement"]["benchmarks"]["KOSPI"]["one_session_return_pct"] = "-0.251288"
        self.assertEqual(RECOMPUTE.verify_packet(tampered)["markets"]["KOSPI"]["verdict"], "MISMATCH")

    def test_friday_session_is_recomputable_with_cross_check(self):
        retained = RECOMPUTE.retained_index_responses(ROOT)
        kospi = RECOMPUTE.recompute_session("2026-09-11", "KOSPI", retained=retained)
        self.assertEqual(kospi["status"], "RECOMPUTED")
        self.assertEqual(kospi["one_session_return_pct"], "-1.763028")
        self.assertEqual(kospi["previous_session_cross_check"], "MATCH:20260910")

    def test_unretained_session_is_not_guessed(self):
        packet = json.loads(
            (ROOT / "data/observations/korea_market_signals/2026-09-09/packet.json").read_text(
                encoding="utf-8"
            )
        )
        report = RECOMPUTE.verify_packet(packet)
        for market in ("KOSPI", "KOSDAQ"):
            self.assertEqual(report["markets"][market]["verdict"], "NOT_VERIFIABLE_RAW_NOT_RETAINED")
            self.assertIsNone(report["markets"][market]["one_session_return_pct"])

    def test_retained_bytes_are_hash_checked_before_use(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "config").mkdir()
            shutil.copyfile(
                ROOT / "config/korea_market_signals_contract.json",
                root / "config/korea_market_signals_contract.json",
            )
            capture = "evidence/regime/kr_information_system/2026-09-11/source-capture"
            shutil.copytree(ROOT / capture, root / capture)
            self.assertEqual(
                RECOMPUTE.recompute_session("2026-09-10", "KOSPI", root=root)["status"], "RECOMPUTED"
            )
            response = root / capture / "responses/20260910-KOSPI-index.json"
            response.write_bytes(response.read_bytes().replace(b"7,033.92", b"7,133.92"))
            with self.assertRaisesRegex(RECOMPUTE.IndexRecomputeError, "RETAINED_RESPONSE_HASH_MISMATCH"):
                RECOMPUTE.retained_index_responses(root)


if __name__ == "__main__":
    unittest.main()
