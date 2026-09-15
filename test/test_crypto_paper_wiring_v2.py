#!/usr/bin/env python3
"""Crypto PAPER wiring v2 (build plan PR3): decision snapshot /4 cutover,
promotion contract/3 rotation source, buy eligibility contract/3, runtime
request /4 (session budget allocation, marketable limit + 150bp quantity
reduction, market-state mapping, exit-policy sell requests).

Natural replays use the committed 2026-09-14 23:43 decision inputs (retained
sources), the committed CRYPTO_PAPER_RUNTIME_V1 decision for 2026-09-14 and
the committed 2026-09-13 crypto rotation confirmation packet.  Where a test
needs a KNOWN regime and a STRONG bucket that nature has not produced yet,
only those two contract/3 evaluators are lifted (test-only), and the rule
in-force instant is lifted past the P1-P6 record effective time that the
natural 2026-09-14 inputs predate; every other derivation runs for real.
"""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
from decimal import Decimal
from fractions import Fraction
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BRIDGE = load("test_crypto_paper_wiring_v2_bridge", "shadow/crypto_paper_runtime_bridge.py")
DECISION = BRIDGE.DECISION
PROMOTION = DECISION.PROMOTION
ELIGIBILITY = DECISION.ELIGIBILITY
ELIG_PROMOTION = ELIGIBILITY.PROMOTION
SIMULATOR = BRIDGE.SIMULATOR
V3 = load("test_crypto_paper_wiring_v2_promotion_fixtures", "test/test_crypto_candidate_promotion_v3.py")
RC = load("test_crypto_paper_wiring_v2_rotation", "rotation/rotation_confirmation.py")

from governance import rule_registry as REGISTRY  # noqa: E402
from portfolio import paper_allocation_envelope as ENV  # noqa: E402
from portfolio import paper_execution_core as CORE  # noqa: E402
from portfolio import paper_session_budget as SB  # noqa: E402

EXIT = BRIDGE._v4_modules()["EXIT"]

NATURAL_DECISION_GLOB = "evidence/crypto_paper_decision/2026-09-14/2343/*/packet.json"
# The committed packet's generated_at is its capture end truncated to the
# second; the last retained message arrived 0.17s later, so a bridge replay
# uses the next second (same technique as the per-market bridge tests).
BRIDGE_REPLAY_AT = "2026-09-14T23:43:42Z"
RUNTIME_PATH = ROOT / "evidence/regime/crypto_paper_runtime/2026-09-14/40e533d35f66e22adb1157d31d88a7694ed70d9812f299e943e949acafb4993a.json"
ROTATION_PATH = ROOT / "evidence/rotation/confirmation/CRYPTO/2026-09-13/packet.json"
CUT = dt.datetime(2026, 9, 14, 7, 0, tzinfo=dt.timezone.utc)
CODE_COMMIT = "b" * 40
OBSERVATION_COMMIT = "c" * 40
LEDGER_ID = "PAPER.LEDGER.WIRING.V2.TEST"
CASH = "200000000"
STRONG_BUCKETS = ("ETH", "ALT")


# ---------------------------------------------------------------------------
# Natural /4 replay helpers
# ---------------------------------------------------------------------------

def natural_packet() -> dict:
    paths = sorted(ROOT.glob(NATURAL_DECISION_GLOB))
    assert len(paths) == 1, paths
    return json.loads(paths[0].read_text(encoding="utf-8"))


def natural_entries(packet: dict) -> dict:
    refs = {row["role"]: row for row in packet["source_refs"]}

    def entry(role):
        path = ROOT / refs[role]["path"]
        return {"date": path.parent.name, "path": path, "record": json.loads(path.read_text(encoding="utf-8"))}

    universe = entry("upbit_tradeable_universe_packet")
    universe["packet"] = universe["record"]["packet"]
    market = entry("upbit_market_evidence_packet")
    market["date"] = market["record"]["snapshot_date"]
    return {
        "universe_entry": universe, "market_evidence_entry": market,
        "realtime_entry": entry("upbit_realtime_capture_run"),
        "leadership_entry": entry("crypto_leadership_packet"),
    }


def file_entry(path: Path) -> dict:
    return {"date": path.parent.name, "path": path, "record": json.loads(path.read_text(encoding="utf-8"))}


def replay(schema_version: str, *, v4_sources: bool = True, generated_at: str | None = None) -> dict:
    packet = natural_packet()
    kwargs = natural_entries(packet)
    if schema_version == DECISION.V4_OUTPUT_SCHEMA_VERSION and generated_at is None:
        # /4 requires a decision instant no realtime input postdates; the
        # committed packet's truncated 23:43:41 precedes its last message.
        generated_at = BRIDGE_REPLAY_AT
    if v4_sources and schema_version == DECISION.V4_OUTPUT_SCHEMA_VERSION:
        kwargs.update(runtime_decision_entry=file_entry(RUNTIME_PATH), rotation_entry=file_entry(ROTATION_PATH))
    stack = contextlib.ExitStack()
    if schema_version == DECISION.V4_OUTPUT_SCHEMA_VERSION and not isinstance(DECISION.v4_cutover_at, mock.Mock):
        stack.enter_context(cutover())  # /4 records the cutover it was emitted under
    with stack:
        return _build_replay(packet, kwargs, schema_version, generated_at)


def _build_replay(packet, kwargs, schema_version, generated_at):
    return DECISION.build_snapshot(
        generated_at=generated_at or packet["generated_at"], source_commit=packet["source_commit"],
        previous_entry=packet["previous_state_reference"],
        # The component registry is bound to its own generated_at instant.
        component_rows=packet["source_components"] if generated_at is None else None,
        started_at=packet.get("started_at"), schema_version=schema_version, **kwargs,
    )


@contextlib.contextmanager
def cutover(at: dt.datetime | None = CUT):
    with mock.patch.object(DECISION, "v4_cutover_at", return_value=at):
        yield


@contextlib.contextmanager
def lifted_regime_and_rotation():
    """RISK_ON runtime regime and STRONG_CONFIRMED ETH/ALT buckets (test-only),
    applied to every P5-08 instance that derives or re-derives promotion."""
    def regime(runtime_decision, *, reference_at):
        return PROMOTION._regime_gate_criterion(
            "RISK_ON", "CRYPTO_RUNTIME_REGIME:RISK_ON:NEW_BUYS_PERMIT", source_runtime_regime="RISK_ON",
            runtime_decision_id="TEST_ONLY", runtime_decision_date="2026-09-14",
            expected_decision_date="2026-09-14", runtime_reasons=[],
        )

    def rotation(canonical_asset_id, rotation_confirmation, *, reference_at):
        bucket = PROMOTION.rotation_bucket(canonical_asset_id)
        lineage = {
            "rotation_bucket": bucket, "required_states": ["STRONG_CONFIRMED", "STRONG_HELD"],
            "confirmation_as_of_date": rotation_confirmation["as_of_date"],
            "confirmation_payload_sha256": rotation_confirmation["payload_sha256"],
            "scope_id": "BTC_RELATIVE_BUCKETS",
        }
        if bucket in STRONG_BUCKETS:
            return PROMOTION._criterion("PASS", "ROTATION_STRONG_CONFIRMED", sector_state="STRONG_CONFIRMED",
                                        strong_confirmed_on="2026-09-13", **lineage)
        return PROMOTION._criterion("FAIL", "ROTATION_NOT_STRONG_CONFIRMED_OR_HELD:NEUTRAL",
                                    sector_state="NEUTRAL", strong_confirmed_on=None, **lineage)

    with contextlib.ExitStack() as stack:
        for module in (PROMOTION, ELIG_PROMOTION):
            stack.enter_context(mock.patch.object(module, "evaluate_crypto_runtime_regime", side_effect=regime))
            stack.enter_context(mock.patch.object(
                module, "evaluate_t2_rotation_membership_confirmed", side_effect=rotation,
            ))
        stack.enter_context(rules_in_force_after_p1_p6())
        yield


@contextlib.contextmanager
def rules_in_force_after_p1_p6():
    """The natural 2026-09-14 inputs predate the P1-P6 record (2026-09-15T00:27Z)
    that RULE.HEDGE.KR_STRESS_UNRATIFIED_INTERIM.V1 cites in every allocation
    envelope; lift only the in-force instant (test-only)."""
    real_in_force = REGISTRY.in_force_at

    def in_force(row, timestamp_utc, registry):
        return real_in_force(row, max(timestamp_utc, "2026-09-15T00:28:00Z"), registry)

    with mock.patch.object(REGISTRY, "in_force_at", side_effect=in_force):
        yield


# ---------------------------------------------------------------------------
# Private runtime input fixtures (test-only values)
# ---------------------------------------------------------------------------

def ledger(*, eth_position: bool = False, open_order_market: str | None = None):
    value = SIMULATOR.create_ledger(
        ledger_id=LEDGER_ID, initial_cash=CASH, opened_at="2026-09-14T20:00:00Z",
        idempotency_key="PAPER.ACCOUNT.OPEN.WIRING.V2.TEST",
    )
    if eth_position:
        intent = SIMULATOR.build_intent(
            order_id="PAPER.BUY.KRW-ETH.EARLIER.TEST", idempotency_key="PAPER.SUBMIT.KRW-ETH.EARLIER.TEST",
            market="KRW-ETH", side="BUY", order_type="LIMIT", quantity="1", limit_price="10000000",
            fee_rate="0", queue_fraction="1", submitted_at="2026-09-14T20:10:00Z",
            expires_at="2026-09-15T07:00:00Z", market_regime_status="PASS",
            source_plan_ref="test://plan", source_plan_sha256="b" * 64,
            source_evidence_ref="test://book", source_evidence_sha256="c" * 64,
        )
        value = SIMULATOR.submit_order(value, intent)
        book = SIMULATOR.build_snapshot(
            snapshot_id="TEST.KRW-ETH.BOOK", market="KRW-ETH", captured_at="2026-09-14T20:20:00Z",
            freshness_status="FRESH", ask_levels=[{"price": "5000000", "quantity": "5"}],
            bid_levels=[{"price": "4990000", "quantity": "5"}], source_ref="test://book", source_sha256="d" * 64,
        )
        value = SIMULATOR.match_order(value, order_id=intent["order_id"], snapshot=book,
                                      event_at="2026-09-14T20:20:00Z", idempotency_key="PAPER.MATCH.ETH.TEST")
    if open_order_market is not None:
        intent = SIMULATOR.build_intent(
            order_id=f"PAPER.BUY.{open_order_market}.CARRIED.TEST",
            idempotency_key=f"PAPER.SUBMIT.{open_order_market}.CARRIED.TEST",
            market=open_order_market, side="BUY", order_type="LIMIT", quantity="1", limit_price="1000",
            fee_rate="0", queue_fraction="1", submitted_at="2026-09-14T23:30:00Z",
            expires_at="2026-09-15T07:00:00Z", market_regime_status="PASS",
            source_plan_ref="test://plan", source_plan_sha256="b" * 64,
            source_evidence_ref="test://book", source_evidence_sha256="c" * 64,
        )
        value = SIMULATOR.submit_order(value, intent)
    return value


def account(decision: dict, **kwargs) -> dict:
    value = ledger(**kwargs)
    held = ["KRW-ETH"] if kwargs.get("eth_position") else []
    marks = BRIDGE.latest_mark_prices_by_market(decision, held)
    return SIMULATOR.build_account_state_per_market(
        value, observed_at=decision["generated_at"], mark_prices=marks["marks"],
        mark_status=marks["mark_status"], mark_source_ref=marks["source_ref"],
        mark_source_sha256=marks["source_sha256"],
    )


def config() -> dict:
    return BRIDGE.build_runtime_config(
        approval_status=BRIDGE.RUNTIME_CONFIG_APPROVAL, approved_by="CIO_TEST",
        approved_at="2026-09-14T20:00:00Z", ledger_id=LEDGER_ID, initial_cash_krw=CASH,
        fee_rate="0.0005", queue_fraction="1", order_type="LIMIT", limit_price_source="ENTRY_ZONE_LOW",
    )


def envelope(decision_at: str, crypto_state: str = "RISK_ON") -> dict:
    core = CORE.load_core()
    return ENV.allocation_envelope(
        core, decision_at_utc=decision_at,
        market_states={"US": "UNKNOWN", "KR": "UNKNOWN", "CRYPTO": crypto_state},
        unknown_streaks={"US": 1, "KR": 1, "CRYPTO": 0}, drawdown_stage="NONE",
        cross_market_flow_validated=False, evidence_mode="NATURAL",
    )


def request(decision: dict, *, account_state=None, **overrides) -> dict:
    inputs = {
        "allocation_envelope": envelope(decision["generated_at"]),
        "recorded_session_budget": None, "position_fills": [], "exit_intents": [],
    }
    inputs.update(overrides)
    return BRIDGE.build_runtime_request(
        decision, expected_source_commit=decision["source_commit"],
        public_code_commit_sha=CODE_COMMIT, observation_commit_sha=OBSERVATION_COMMIT,
        account_state=account_state if account_state is not None else account(decision),
        open_position_risk=None, runtime_config=config(), known_idempotency_keys=[], **inputs,
    )


def by_market(rows: list) -> dict:
    return {row["market"]: row for row in rows}


# ---------------------------------------------------------------------------
# 1. Cutover
# ---------------------------------------------------------------------------

class CutoverTests(unittest.TestCase):
    def test_committed_config_names_the_user_ratified_t_cut_and_emits_v3_before_it(self):
        config_value = DECISION.load_wiring_config()
        cutover_block = config_value["decision_snapshot_v4_cutover"]
        self.assertEqual((cutover_block["t_cut_utc"], cutover_block["status"]), ("2026-09-18T07:00:00Z", "ACTIVE_FROM_T_CUT"))
        self.assertEqual(cutover_block["source_record"], {
            "rule_id": "RULE.CRYPTO.PAPER_V2_TCUT.V1",
            "path": "evidence/authority/USER_RATIFICATION_CRYPTO_PAPER_V2_OPERATION_20260915.json",
            "sha256": "ccc846a32565b0e837a30606e9aa81e894529fbed4be1917d70269ac315b5e56",
        })
        self.assertEqual(DECISION.v4_cutover_at(), dt.datetime(2026, 9, 18, 7, 0, tzinfo=dt.timezone.utc))
        for instant, expected in (
            ("2026-09-14T23:43:41Z", DECISION.PER_MARKET_OUTPUT_SCHEMA_VERSION),
            ("2026-09-18T06:59:59Z", DECISION.PER_MARKET_OUTPUT_SCHEMA_VERSION),
            ("2026-09-18T07:00:00Z", DECISION.V4_OUTPUT_SCHEMA_VERSION),
        ):
            self.assertEqual(DECISION.schema_version_for(DECISION._parse_utc(instant, "t")), expected, instant)
        self.assertTrue(all(value is False for value in config_value["authority"].values()))

    def test_active_cutover_switches_at_t_cut_only(self):
        with cutover(dt.datetime(2026, 9, 20, 7, 0, tzinfo=dt.timezone.utc)):
            before = DECISION.schema_version_for(DECISION._parse_utc("2026-09-20T06:59:59Z", "t"))
            at = DECISION.schema_version_for(DECISION._parse_utc("2026-09-20T07:00:00Z", "t"))
        self.assertEqual(before, DECISION.PER_MARKET_OUTPUT_SCHEMA_VERSION)
        self.assertEqual(at, DECISION.V4_OUTPUT_SCHEMA_VERSION)

    def test_t_cut_must_be_a_registry_decision_cycle_instant_with_active_status(self):
        base = json.loads(DECISION.WIRING_CONFIG_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wiring.json"
            record = base["decision_snapshot_v4_cutover"]["source_record"]
            for t_cut, status, source, expected in (
                ("2026-09-18T07:00:00Z", "ACTIVE_FROM_T_CUT", record, None),
                ("2026-09-18T07:30:00Z", "ACTIVE_FROM_T_CUT", record, "WIRING_CONFIG_T_CUT_NOT_AT_CRYPTO_DECISION_CYCLE"),
                ("2026-09-20T07:00:00Z", "ACTIVE_FROM_T_CUT", record, "WIRING_CONFIG_T_CUT_NOT_THE_RATIFIED_INSTANT"),
                ("2026-09-18T07:00:00Z", "ACTIVE_FROM_T_CUT", dict(record, sha256="0" * 64), "WIRING_CONFIG_T_CUT_RECORD_HASH_MISMATCH"),
                ("2026-09-18T07:00:00Z", "ACTIVE_FROM_T_CUT", None, "WIRING_CONFIG_T_CUT_RECORD_MISSING"),
                ("2026-09-18T07:00:00Z", "NOT_ACTIVE", record, "WIRING_CONFIG_CUTOVER_STATUS_INVALID"),
                (None, "ACTIVE_FROM_T_CUT", record, "WIRING_CONFIG_CUTOVER_STATUS_INVALID"),
            ):
                value = copy.deepcopy(base)
                value["decision_snapshot_v4_cutover"].update(t_cut_utc=t_cut, status=status, source_record=source)
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.subTest(t_cut=t_cut, status=status, source=bool(source)):
                    if expected is None:
                        self.assertEqual(DECISION.load_wiring_config(path)["decision_snapshot_v4_cutover"]["t_cut_utc"], t_cut)
                    else:
                        with self.assertRaisesRegex(DECISION.CryptoPaperDecisionSnapshotError, expected):
                            DECISION.load_wiring_config(path)

    def test_v3_replay_is_byte_identical_and_v4_needs_active_cutover(self):
        committed = natural_packet()
        self.assertEqual(DECISION.validate_output(committed), committed)
        self.assertEqual(DECISION.canonical_json(replay(DECISION.PER_MARKET_OUTPUT_SCHEMA_VERSION)),
                         DECISION.canonical_json(committed))
        v4 = replay(DECISION.V4_OUTPUT_SCHEMA_VERSION)
        self.assertEqual(v4["crypto_paper_wiring"]["t_cut_utc"], "2026-09-14T07:00:00Z")
        with cutover():
            self.assertEqual(DECISION.validate_output(v4), v4)
        with self.assertRaisesRegex(DECISION.CryptoPaperDecisionSnapshotError, "V4_T_CUT_CHANGED_OR_INACTIVE"):
            DECISION.validate_output(v4)
        # T_cut is immutable once set: moving it invalidates every /4 packet emitted under it.
        with cutover(dt.datetime(2026, 9, 13, 7, 0, tzinfo=dt.timezone.utc)):
            with self.assertRaisesRegex(DECISION.CryptoPaperDecisionSnapshotError, "V4_T_CUT_CHANGED_OR_INACTIVE"):
                DECISION.validate_output(v4)
        with cutover(None):
            inactive = replay(DECISION.V4_OUTPUT_SCHEMA_VERSION)
        self.assertIsNone(inactive["crypto_paper_wiring"]["t_cut_utc"])
        with cutover(), self.assertRaisesRegex(DECISION.CryptoPaperDecisionSnapshotError, "NOT_EFFECTIVE_FOR_GENERATED_AT"):
            DECISION.validate_output(inactive)

    def test_committed_v4_packets_name_the_configured_t_cut(self):
        """Immutability guard: once any /4 packet is committed, the config T_cut must equal its record."""
        t_cut = DECISION.load_wiring_config()["decision_snapshot_v4_cutover"]["t_cut_utc"]
        if t_cut is None:
            self.assertEqual(DECISION.load_wiring_config()["decision_snapshot_v4_cutover"]["status"], "NOT_ACTIVE")
            return
        for day in sorted((ROOT / "evidence" / "crypto_paper_decision").glob("20*")):
            if day.name < t_cut[:10]:
                continue
            for path in day.glob("*/*/packet.json"):
                packet = json.loads(path.read_text(encoding="utf-8"))
                if packet.get("schema_version") == DECISION.V4_OUTPUT_SCHEMA_VERSION:
                    self.assertEqual(packet["crypto_paper_wiring"]["t_cut_utc"], t_cut, path)

    def test_frozen_schema_tuples_are_unchanged(self):
        self.assertEqual(DECISION.OUTPUT_SCHEMA_VERSIONS, (
            "crypto_paper_decision_snapshot_packet/1", "crypto_paper_decision_snapshot_packet/2",
            "crypto_paper_decision_snapshot_packet/3",
        ))
        self.assertNotIn(DECISION.V4_OUTPUT_SCHEMA_VERSION, DECISION.PER_MARKET_OUTPUT_SCHEMA_VERSIONS)
        self.assertIn(DECISION.V4_OUTPUT_SCHEMA_VERSION, DECISION.PER_MARKET_LAYOUT_SCHEMA_VERSIONS)


# ---------------------------------------------------------------------------
# 2. Decision snapshot /4 on natural inputs
# ---------------------------------------------------------------------------

class DecisionSnapshotV4Tests(unittest.TestCase):
    def test_v4_binds_runtime_decision_and_rotation_and_rederives(self):
        v3 = natural_packet()
        v4 = replay(DECISION.V4_OUTPUT_SCHEMA_VERSION)
        with cutover():
            self.assertEqual(DECISION.validate_output(v4), v4)
        self.assertEqual(set(v4) - set(v3), {"crypto_paper_wiring"})
        roles = [row["role"] for row in v4["source_refs"]]
        self.assertEqual(roles[-2:], list(DECISION.V4_SOURCE_ROLES))
        self.assertNotEqual(v4["generation_id"], v3["generation_id"])
        wiring = v4["crypto_paper_wiring"]
        self.assertEqual(wiring["promotion_contract_version"], "crypto_candidate_promotion_contract/3")
        self.assertEqual(wiring["eligibility_contract_version"], "crypto_paper_buy_eligibility_contract/3")
        self.assertEqual(wiring["crypto_paper_runtime_decision"]["runtime_regime"], "UNKNOWN")
        self.assertEqual(wiring["rotation_confirmation"]["as_of_date"], "2026-09-13")
        for row in v4["candidates"]:
            with self.subTest(market=row["market"]):
                self.assertEqual(row["state"], "WATCH")
                t2 = row["p5_08"]["t2_required_conditions"]
                self.assertEqual(t2["T2_REGIME_PERMITS_NEW_BUYS"]["status"], "UNKNOWN")
                self.assertEqual(t2["T2_ROTATION_MEMBERSHIP"]["status"], "UNKNOWN")
                # packet as of 2026-09-13 predates the rotation rule's decision-effective date
                self.assertTrue(t2["T2_ROTATION_MEMBERSHIP"]["reason"].startswith("ROTATION_CONFIRMATION_UNKNOWN:"))
                self.assertEqual(row["p5_08"]["unapplied_rules"], [])
        self.assertEqual(v4["funnel_counts"]["paper_ready_count"], 0)

    def test_v4_tamper_and_lookahead_fail_closed(self):
        v4 = replay(DECISION.V4_OUTPUT_SCHEMA_VERSION)
        forged = copy.deepcopy(v4)
        forged["crypto_paper_wiring"]["crypto_paper_runtime_decision"]["runtime_regime"] = "RISK_ON"
        forged["payload_sha256"] = DECISION.payload_sha256({k: v for k, v in forged.items() if k != "payload_sha256"})
        with cutover(), self.assertRaisesRegex(DECISION.CryptoPaperDecisionSnapshotError, "OUTPUT_DERIVATION_MISMATCH"):
            DECISION.validate_output(forged)
        packet = natural_packet()
        future_rotation = file_entry(ROTATION_PATH)
        future_rotation["record"] = dict(future_rotation["record"], as_of_date="2026-09-14")
        future_rotation["date"] = "2026-09-14"
        with self.assertRaisesRegex(DECISION.CryptoPaperDecisionSnapshotError, "ROTATION_CONFIRMATION_FUTURE_DATED"):
            DECISION.build_snapshot(
                generated_at=packet["generated_at"], source_commit=packet["source_commit"],
                schema_version=DECISION.V4_OUTPUT_SCHEMA_VERSION, rotation_entry=future_rotation,
                **natural_entries(packet),
            )
        late_runtime = file_entry(RUNTIME_PATH)
        late_runtime["record"] = dict(late_runtime["record"], evaluation_at="2026-09-14T23:59:00Z")
        with self.assertRaisesRegex(DECISION.CryptoPaperDecisionSnapshotError, "RUNTIME_DECISION_FUTURE_DATED"):
            DECISION.build_snapshot(
                generated_at=packet["generated_at"], source_commit=packet["source_commit"],
                schema_version=DECISION.V4_OUTPUT_SCHEMA_VERSION, runtime_decision_entry=late_runtime,
                **natural_entries(packet),
            )
        with self.assertRaisesRegex(DECISION.CryptoPaperDecisionSnapshotError, "V4_SOURCES_REQUIRE_SCHEMA_V4"):
            DECISION.build_snapshot(
                generated_at=packet["generated_at"], source_commit=packet["source_commit"],
                schema_version=DECISION.PER_MARKET_OUTPUT_SCHEMA_VERSION,
                runtime_decision_entry=file_entry(RUNTIME_PATH), **natural_entries(packet),
            )

    def test_source_finders_select_available_evidence_only(self):
        found = DECISION.find_latest_runtime_decision(not_after=DECISION._parse_utc("2026-09-14T23:43:41Z", "t"))
        self.assertEqual(found["path"], RUNTIME_PATH)
        self.assertIsNone(DECISION.find_latest_runtime_decision(
            not_after=DECISION._parse_utc("2026-09-14T14:02:18Z", "t")))
        rotation = DECISION.find_latest_rotation_confirmation(before_date="2026-09-14")
        self.assertEqual(rotation["path"], ROTATION_PATH)

    def test_populate_selects_and_retains_v4_sources_after_t_cut(self):
        packet = natural_packet()
        realtime = ROOT / next(r["path"] for r in packet["source_refs"] if r["role"] == "upbit_realtime_capture_run")
        output_root = Path(tempfile.mkdtemp(prefix=".crypto_wiring_v2_populate_", dir=ROOT))
        self.addCleanup(__import__("shutil").rmtree, output_root, ignore_errors=True)
        # Pin the leadership input to the natural packet's own (as of 2026-09-13).
        leadership = ROOT / next(r["path"] for r in packet["source_refs"] if r["role"] == "crypto_leadership_packet")
        leadership_root = output_root / "leadership_input"
        (leadership_root / "2026-09-13").mkdir(parents=True)
        (leadership_root / "2026-09-13" / "packet.json").write_bytes(leadership.read_bytes())
        with cutover():
            result = DECISION.populate(
                generated_at=BRIDGE_REPLAY_AT, source_commit=packet["source_commit"],
                realtime_run_path=realtime, output_root=output_root, leadership_data_root=leadership_root,
            )
            record = result["record"]
            self.assertEqual(DECISION.validate_output(record), record)
        self.assertEqual(result["outcome"], "populated")
        self.assertEqual(record["schema_version"], DECISION.V4_OUTPUT_SCHEMA_VERSION)
        refs = {row["role"]: row["path"] for row in record["source_refs"]}
        for role in DECISION.V4_SOURCE_ROLES:
            self.assertTrue(refs[role].startswith(output_root.relative_to(ROOT).as_posix() + "/_sources/sha256/"), role)
        self.assertEqual(record["crypto_paper_wiring"]["rotation_confirmation"]["as_of_date"], "2026-09-13")
        with mock.patch.object(DECISION, "v4_cutover_at", return_value=None):
            before = DECISION.populate(
                generated_at=BRIDGE_REPLAY_AT, source_commit=packet["source_commit"],
                realtime_run_path=realtime, output_root=output_root, leadership_data_root=leadership_root,
            )
        self.assertEqual(before["record"]["schema_version"], DECISION.PER_MARKET_OUTPUT_SCHEMA_VERSION)
        self.assertFalse(set(DECISION.V4_SOURCE_ROLES) & {row["role"] for row in before["record"]["source_refs"]})

    def test_lifted_regime_and_rotation_reach_focused_review_but_public_eligibility_waits_for_private_inputs(self):
        with lifted_regime_and_rotation():
            v4 = replay(DECISION.V4_OUTPUT_SCHEMA_VERSION)
            with cutover():
                DECISION.validate_output(v4)
        rows = by_market(v4["candidates"])
        self.assertEqual(rows["KRW-BTC"]["p5_08"]["promotion_state"], "BLOCKED")
        self.assertEqual(rows["KRW-ETH"]["p5_08"]["promotion_state"], "FOCUSED_REVIEW")
        eth = rows["KRW-ETH"]["p5_09"]
        self.assertEqual(eth["eligibility_state"], "WATCH")
        self.assertEqual(eth["eligibility_reason"], "GATING_CRITERIA_UNKNOWN:DUPLICATE_GUARD,REENTRY_PERMITTED")
        self.assertEqual(set(eth["record_only_features"]), set(ELIGIBILITY.RECORD_ONLY_FEATURES_V3))


# ---------------------------------------------------------------------------
# 3. Promotion contract/3 rotation source
# ---------------------------------------------------------------------------

def rotation_packets(eth_top_days: int = 2, *, unknown_last: bool = False) -> list:
    def obs(day, alt, eth, status="OBSERVED"):
        if status != "OBSERVED":
            return {"as_of_date": day, "status": "UNKNOWN", "unknown_reason": "TEST", "sources": [], "scopes": {}, "aux": {}}
        return {"as_of_date": day, "status": "OBSERVED", "unknown_reason": None, "sources": [], "aux": {},
                "scopes": {"BTC_RELATIVE_BUCKETS": [
                    {"entity_id": "ALT", "source_identity": "ALT", "strength": alt},
                    {"entity_id": "BTC", "source_identity": "BTC", "strength": "0"},
                    {"entity_id": "ETH", "source_identity": "ETH", "strength": eth},
                ]}}
    days = ["2026-09-17", "2026-09-18", "2026-09-19"]
    observations = [obs(days[0], "-0.01", "0.01"), obs(days[1], "-0.01", "0.02"),
                    obs(days[2], "-0.02", "0.03", "UNKNOWN" if unknown_last else "OBSERVED")]
    return RC.build_market_packets(RC.load_policy(), "CRYPTO", observations, RC.load_state_mapping())


class _EligibilityUniverse:
    """P3-12 ratification fixture swap on the P5-08 instance P5-09 re-derives with."""

    def setUp(self):
        universe = ELIG_PROMOTION.UPBIT_UNIVERSE
        policy, taxonomy, registry = universe.load_policy(), universe.load_taxonomy(), universe.load_identity_registry()
        for doc, field in ((policy, "effective_date"), (taxonomy, "effective_from"), (registry, "effective_from")):
            doc["approval_status"] = "RATIFIED"
            doc[field] = "2026-08-28"
        self._patches = [
            mock.patch.object(universe, "load_policy", return_value=policy),
            mock.patch.object(universe, "load_taxonomy", return_value=taxonomy),
            mock.patch.object(universe, "load_identity_registry", return_value=registry),
            mock.patch.object(universe.EXACT_RELEASE_BINDING, "validate_exact_release", return_value=True),
        ]
        for patch in self._patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in reversed(self._patches)])

    def promotion(self, *, runtime_state="RISK_ON", rotation=None, rows=None):
        rows = rows or [V3.universe_row(), V3.universe_row(V3.ALT_MARKET, "ETH"), V3.universe_row("KRW-SOL", "SOL")]
        evidence = {row["market"]: V3.market_evidence(row["market"]) for row in rows}
        rotation = rotation_packets()[-1] if rotation is None else rotation
        return ELIG_PROMOTION.build_promotion_packet(
            V3.universe_packet(rows), V3.regime_envelope(), evidence, None, evaluation_as_of=V3.DAY,
            contract_version=3, crypto_runtime_decision=V3.runtime_decision(runtime_state),
            rotation_confirmation=rotation,
        )


class PromotionRotationTests(_EligibilityUniverse, unittest.TestCase):
    def test_strong_bucket_passes_neutral_bucket_fails_and_rederives(self):
        packet = self.promotion()
        self.assertEqual(ELIG_PROMOTION.validate_output(packet), packet)
        rows = by_market(packet["candidates"])
        self.assertEqual(rows["KRW-ETH"]["promotion_state"], "FOCUSED_REVIEW")
        eth_t2 = rows["KRW-ETH"]["t2_required_conditions"]["T2_ROTATION_MEMBERSHIP"]
        self.assertEqual((eth_t2["status"], eth_t2["sector_state"], eth_t2["strong_confirmed_on"]),
                         ("PASS", "STRONG_HELD", "2026-09-18"))
        self.assertEqual(rows["KRW-BTC"]["promotion_state"], "BLOCKED")
        self.assertEqual(rows["KRW-SOL"]["t2_required_conditions"]["T2_ROTATION_MEMBERSHIP"]["reason"],
                         "ROTATION_NOT_STRONG_CONFIRMED_OR_HELD:NEUTRAL")
        self.assertEqual(rows["KRW-ETH"]["unapplied_rules"], [])
        self.assertIn({"rule_id": "RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1", "version": 1, "registry_sha256": None,
                       "source_record_sha256": PROMOTION.ROTATION_RECORD_SHA256, "role": "BLOCKED_BY"},
                      rows["KRW-BTC"]["rule_refs"])
        self.assertIn("rotation_confirmation", packet["source_packets"])

    def test_unobserved_packet_is_unknown_and_same_day_packet_is_lookahead(self):
        packet = self.promotion(rotation=rotation_packets(unknown_last=True)[-1])
        eth = by_market(packet["candidates"])["KRW-ETH"]
        self.assertEqual(eth["promotion_state"], "WATCH")
        self.assertEqual(eth["t2_required_conditions"]["T2_ROTATION_MEMBERSHIP"]["reason"],
                         "ROTATION_CONFIRMATION_UNKNOWN:CONFIRMATION_OBSERVATION_UNKNOWN_TEST")
        same_day = copy.deepcopy(rotation_packets()[-1])
        same_day["as_of_date"] = V3.DAY
        same_day["payload_sha256"] = RC.payload_sha256({k: v for k, v in same_day.items() if k != "payload_sha256"})
        with self.assertRaisesRegex(ELIG_PROMOTION.CryptoCandidatePromotionError, "ROTATION_CONFIRMATION_LOOKAHEAD"):
            self.promotion(rotation=same_day)
        with self.assertRaisesRegex(ELIG_PROMOTION.CryptoCandidatePromotionError, "ROTATION_CONFIRMATION_REQUIRES_CONTRACT_V3"):
            ELIG_PROMOTION.build_promotion_packet(
                V3.universe_packet([V3.universe_row()]), V3.regime_envelope(), {}, None,
                evaluation_as_of=V3.DAY, rotation_confirmation=rotation_packets()[-1],
            )

    def test_without_rotation_source_contract_3_output_is_unchanged(self):
        rows = [V3.universe_row()]
        packet = ELIG_PROMOTION.build_promotion_packet(
            V3.universe_packet(rows), V3.regime_envelope(), {"KRW-BTC": V3.market_evidence()}, None,
            evaluation_as_of=V3.DAY, contract_version=3, crypto_runtime_decision=V3.runtime_decision("RISK_ON"),
        )
        self.assertNotIn("rotation_confirmation", packet["source_packets"])
        self.assertEqual(packet["candidates"][0]["unapplied_rules"], [
            {"rule_id": "RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1", "reason_code": "CRYPTO_ROTATION_CONFIRMATION_NOT_WIRED"},
        ])


# ---------------------------------------------------------------------------
# 4. Buy eligibility contract/3
# ---------------------------------------------------------------------------

DECISION_AT = V3.REFERENCE_AT
PACKET_ID = "e" * 64


def budget_record(markets=("KRW-ETH",), *, fee="0.0005", price="1001", decision_at=DECISION_AT):
    core = CORE.load_core()
    env = ENV.allocation_envelope(
        core, decision_at_utc=decision_at,
        market_states={"US": "UNKNOWN", "KR": "UNKNOWN", "CRYPTO": "RISK_ON"},
        unknown_streaks={"US": 1, "KR": 1, "CRYPTO": 0}, drawdown_stage="NONE",
        cross_market_flow_validated=False, evidence_mode="NATURAL",
    )
    return SB.build_session_budget_record(
        core, market="CRYPTO", session_id=SB.crypto_session_id(core, decision_at), decision_at_utc=decision_at,
        nav_snapshot={"as_of_utc": decision_at, "virtual_cash_krw": CASH, "holdings": [],
                      "open_buy_reservations": [], "fx_observation": None},
        envelope=env,
        candidates=[{"instrument": market, "avg_traded_value": "10000000000", "adv_window": "30_DAYS",
                     "adv_source": "test", "limit_price": price, "quantity_step": "1/1000000000000000000",
                     "fee_rate": fee} for market in markets],
    )


def fill(fill_id, side, at, *, packet="d" * 64, exit_reason=None, strength="SE-OLD"):
    return {"fill_id": fill_id, "market": "CRYPTO", "instrument": "KRW-ETH", "side": side, "filled_at_utc": at,
            "quantity": "1", "price": "1000", "fee": "0", "decision_packet_id": packet,
            "exit_reason": exit_reason, "strength_episode_id": strength if side == "BUY" else None}


class EligibilityV3Tests(_EligibilityUniverse, unittest.TestCase):
    def build(self, promotion=None, **kwargs):
        promotion = promotion or self.promotion()
        return ELIGIBILITY.build_eligibility_packet_v3(
            promotion, evaluation_as_of=V3.DAY, decision_at_utc=DECISION_AT, **kwargs,
        )

    def test_contract_v3_file_and_v2_default_unchanged(self):
        contract = ELIGIBILITY.load_contract_v3()
        self.assertEqual(contract["contract_version"], "crypto_paper_buy_eligibility_contract/3")
        self.assertEqual(ELIGIBILITY.load_contract()["contract_version"], "crypto_paper_buy_eligibility_contract/2")
        self.assertEqual(ELIGIBILITY.OUTPUT_SCHEMA_VERSION, "crypto_paper_buy_eligibility_packet/2")

    def test_public_derivation_waits_for_private_inputs_and_record_only_features_never_block(self):
        packet = self.build()
        self.assertEqual(ELIGIBILITY.validate_output(packet), packet)
        eth = packet["candidates"][0]
        self.assertEqual(eth["market"], "KRW-ETH")
        self.assertEqual(eth["eligibility_state"], "WATCH")
        self.assertEqual(eth["criteria"]["REENTRY_PERMITTED"]["reason"], "DECISION_PACKET_ID_NOT_SUPPLIED")
        failing = {"status": "FAIL", "reason": "TEST_ONLY_FEATURE_FAIL"}
        with mock.patch.object(ELIGIBILITY, "evaluate_breakout_or_pullback", return_value=failing), \
                mock.patch.object(ELIGIBILITY, "evaluate_trigger_timeframe_alignment", return_value=failing):
            waiting = self.build(decision_packet_id=PACKET_ID, known_idempotency_keys=[], position_fills=[])
        eth = waiting["candidates"][0]
        self.assertEqual(eth["eligibility_state"], "WAIT")
        self.assertEqual(eth["record_only_features"]["BREAKOUT_OR_PULLBACK"]["status"], "FAIL")
        self.assertEqual(eth["record_only_features"]["TRIGGER_TIMEFRAME_ALIGNMENT"]["status"], "FAIL")
        self.assertTrue(all(value is None for value in eth["order_draft"].values()))

    def test_session_budget_sizes_the_draft_with_r1_key_and_cycle_expiry(self):
        record = budget_record()
        packet = self.build(decision_packet_id=PACKET_ID, known_idempotency_keys=[], position_fills=[],
                            session_budget_record=record, fee_rate="0.0005")
        self.assertEqual(ELIGIBILITY.validate_output(packet), packet)
        eth = packet["candidates"][0]
        self.assertEqual(eth["eligibility_state"], "PAPER_BUY_ELIGIBLE")
        draft = eth["order_draft"]
        line = record["allocation"][0]
        self.assertEqual((draft["quantity"], draft["allocated_krw"]), (line["quantity"], line["allocated_krw"]))
        self.assertEqual(draft["expires_at"], "2026-09-21T07:00:00Z")
        self.assertEqual(draft["reentry_key"], {"market": "CRYPTO", "decision_packet_id": PACKET_ID, "instrument": "KRW-ETH"})
        self.assertEqual(draft["duplicate_guard_key"],
                         ELIGIBILITY.compute_reentry_key(PACKET_ID, "KRW-ETH")["duplicate_guard_key"])
        self.assertEqual(draft["planned_loss"]["role"], "RECORD_ONLY")
        self.assertEqual(draft["planned_loss"]["status"], "UNKNOWN")  # 2 daily bars < ATR14 period, never a gate
        roles = {(ref["rule_id"], ref["role"]) for ref in eth["rule_refs"]}
        self.assertIn(("RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V2", "SIZED_BY"), roles)
        self.assertIn(("RULE.RISK.PLANNED_LOSS_RECORD_ONLY.V1", "APPLIED"), roles)
        tampered = copy.deepcopy(packet)
        tampered["candidates"][0]["order_draft"]["quantity"] = "999"
        tampered["payload_sha256"] = ELIGIBILITY.payload_sha256({k: v for k, v in tampered.items() if k != "payload_sha256"})
        with self.assertRaisesRegex(ELIGIBILITY.CryptoPaperBuyEligibilityError, "OUTPUT_DERIVATION_MISMATCH"):
            ELIGIBILITY.validate_output(tampered)

    def test_duplicate_r1_key_and_d7_reentry_block(self):
        key = ELIGIBILITY.compute_reentry_key(PACKET_ID, "KRW-ETH")["duplicate_guard_key"]
        duplicate = self.build(decision_packet_id=PACKET_ID, known_idempotency_keys=[key], position_fills=[])
        self.assertEqual(duplicate["candidates"][0]["eligibility_reason"], "GATING_CRITERIA_FAILED:DUPLICATE_GUARD")
        strength = ELIGIBILITY._v3_modules()["EPISODE"].strength_episode_id("CRYPTO", "ETH", "2026-09-18")
        released = [fill("F1", "BUY", "2026-09-19T08:00:00Z", strength=strength),
                    fill("F2", "SELL", "2026-09-19T20:00:00Z", exit_reason="RELEASE_FULL_SELL")]
        blocked = self.build(decision_packet_id=PACKET_ID, known_idempotency_keys=[], position_fills=released)
        self.assertEqual(blocked["candidates"][0]["criteria"]["REENTRY_PERMITTED"]["reason"],
                         "REENTRY_DENIED:REENTRY_REQUIRES_NEW_STRONG_CONFIRMATION_AFTER_EXIT")
        reduced = [released[0], fill("F2", "SELL", "2026-09-19T20:00:00Z", exit_reason="ALLOCATION_REDUCTION")]
        allowed = self.build(decision_packet_id=PACKET_ID, known_idempotency_keys=[], position_fills=reduced)
        self.assertEqual(allowed["candidates"][0]["eligibility_state"], "WAIT")
        same_packet = [released[0], fill("F2", "SELL", "2026-09-19T20:00:00Z", packet=PACKET_ID,
                                         exit_reason="ALLOCATION_REDUCTION")]
        denied = self.build(decision_packet_id=PACKET_ID, known_idempotency_keys=[], position_fills=same_packet)
        self.assertEqual(denied["candidates"][0]["criteria"]["REENTRY_PERMITTED"]["reason"],
                         "REENTRY_DENIED:R1_SOLD_IN_SAME_DECISION_PACKET")

    def test_budget_record_must_match_session_and_focused_candidates(self):
        with self.assertRaisesRegex(ELIGIBILITY.CryptoPaperBuyEligibilityError, "SESSION_BUDGET_CANDIDATE_NOT_FOCUSED_REVIEW"):
            self.build(decision_packet_id=PACKET_ID, known_idempotency_keys=[], position_fills=[],
                       session_budget_record=budget_record(("KRW-BTC",)), fee_rate="0.0005")
        with self.assertRaisesRegex(ELIGIBILITY.CryptoPaperBuyEligibilityError, "SESSION_BUDGET_RECORD_DECISION_MISMATCH"):
            self.build(decision_packet_id=PACKET_ID, known_idempotency_keys=[], position_fills=[],
                       session_budget_record=budget_record(decision_at="2026-09-20T07:20:00Z"), fee_rate="0.0005")


# ---------------------------------------------------------------------------
# 5. Sizing and market-state mapping
# ---------------------------------------------------------------------------

def book(asks, bids=(("990", "100"),)):
    return SIMULATOR.build_snapshot(
        snapshot_id="TEST.BOOK", market="KRW-ETH", captured_at="2026-09-20T07:40:00Z", freshness_status="FRESH",
        ask_levels=[{"price": p, "quantity": q} for p, q in asks],
        bid_levels=[{"price": p, "quantity": q} for p, q in bids],
        source_ref="test://book", source_sha256="a" * 64,
    )


class SizingTests(unittest.TestCase):
    THRESHOLD = BRIDGE.crypto_slippage_rule(CORE.load_core())["threshold_bp"]

    def test_threshold_comes_from_the_registry(self):
        rule = BRIDGE.crypto_slippage_rule(CORE.load_core())
        self.assertEqual((rule["rule_id"], rule["basis"], rule["action"], rule["threshold_bp"]),
                         ("RULE.EXEC.QUALITY_LAYERS.V1", "ACTUAL_ORDER_NOTIONAL", "REDUCE_QUANTITY", 150))

    def test_deep_book_is_budget_bound_without_reduction(self):
        sizing = BRIDGE.marketable_limit_sizing(
            book([("1000", "1000")]), side="BUY", fee_rate="0", queue_fraction="1",
            threshold_bp=self.THRESHOLD, budget_krw=Fraction(10000),
        )
        self.assertEqual((sizing["quantity"], sizing["limit_price"], sizing["reasons"]), ("10", "1000", []))
        self.assertEqual(sizing["submitted_amount_krw"], "10000")

    def test_vwap_bound_reduces_quantity_to_the_threshold(self):
        # 1 unit at 1000, then 1100 (1000bp away): VWAP <= 1015 allows 15/85 of a unit at 1100.
        sizing = BRIDGE.marketable_limit_sizing(
            book([("1000", "1"), ("1100", "10")]), side="BUY", fee_rate="0", queue_fraction="1",
            threshold_bp=self.THRESHOLD, budget_krw=Fraction(100000),
        )
        quantity = Fraction(Decimal(sizing["quantity"]))
        vwap = (1000 + 1100 * (quantity - 1)) / quantity
        self.assertLessEqual(vwap, Fraction(1015))
        # floored to the 8-decimal crypto quantity step, so VWAP sits just under the bound
        self.assertGreater(vwap, Fraction(1015) - Fraction(1, 10 ** 4))
        self.assertEqual(sizing["quantity_decimal_places"], 8)
        self.assertEqual(len(sizing["quantity"].split(".")[1]), 8)
        self.assertEqual(sizing["limit_price"], "1100")
        self.assertIn("QUANTITY_REDUCED_ACTUAL_NOTIONAL_SLIPPAGE_ABOVE_THRESHOLD", sizing["reasons"])
        self.assertLess(quantity, Fraction(Decimal(sizing["quantity_without_slippage_bound"])))
        self.assertLessEqual(Fraction(sizing["submitted_amount_krw"]), Fraction(100000))

    def test_budget_counts_the_marketable_limit_and_fee_and_queue_fraction(self):
        sizing = BRIDGE.marketable_limit_sizing(
            book([("1000", "1"), ("1010", "100")]), side="BUY", fee_rate="0.001", queue_fraction="0.5",
            threshold_bp=self.THRESHOLD, budget_krw=Fraction(5000),
        )
        self.assertLessEqual(Fraction(sizing["submitted_amount_krw"]), Fraction(5000))
        self.assertEqual(sizing["limit_price"], "1010")
        sell = BRIDGE.marketable_limit_sizing(
            book([("1000", "1")], bids=(("1000", "1"), ("900", "10"))), side="SELL", fee_rate="0",
            queue_fraction="1", threshold_bp=self.THRESHOLD, max_quantity=Fraction(5),
        )
        quantity = Fraction(Decimal(sell["quantity"]))
        self.assertGreaterEqual((1000 + 900 * (quantity - 1)) / quantity, Fraction(985))
        self.assertIsNone(sell["submitted_amount_krw"])

    def test_market_state_mapping_to_simulator_vocabulary(self):
        expected = {"RISK_ON": "PASS", "NEUTRAL": "PASS", "RISK_OFF": "FAIL", "STRESS": "FAIL",
                    "UNKNOWN": "UNKNOWN", "PASS": "PASS", "NOT_EVALUATED": "NOT_EVALUATED"}
        for state, status in expected.items():
            with self.subTest(state=state):
                self.assertEqual(BRIDGE.simulator_market_regime_status(state), status)
        with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "MARKET_REGIME_STATUS_UNMAPPABLE"):
            BRIDGE.simulator_market_regime_status("BULL")
        with self.assertRaises(SIMULATOR.CryptoPaperSimulatorError):
            SIMULATOR.build_intent(
                order_id="PAPER.BUY.X.TEST", idempotency_key="PAPER.X.TEST", market="KRW-ETH", side="BUY",
                order_type="LIMIT", quantity="1", limit_price="1", fee_rate="0", queue_fraction="1",
                submitted_at="2026-09-20T07:00:00Z", expires_at="2026-09-21T07:00:00Z",
                market_regime_status="RISK_ON", source_plan_ref="t", source_plan_sha256="a" * 64,
                source_evidence_ref="t", source_evidence_sha256="a" * 64,
            )


# ---------------------------------------------------------------------------
# 6. Runtime request /4
# ---------------------------------------------------------------------------

class RuntimeRequestV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with lifted_regime_and_rotation():
            cls.lifted = replay(DECISION.V4_OUTPUT_SCHEMA_VERSION, generated_at=BRIDGE_REPLAY_AT)
        cls.natural = replay(DECISION.V4_OUTPUT_SCHEMA_VERSION, generated_at=BRIDGE_REPLAY_AT)

    def setUp(self):
        stack = contextlib.ExitStack()
        stack.enter_context(cutover())
        stack.enter_context(rules_in_force_after_p1_p6())
        self.addCleanup(stack.close)

    def test_schema_routing_and_missing_inputs(self):
        v3 = natural_packet()
        with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "RUNTIME_REQUEST_V4_INPUTS_REQUIRE_V4_REQUEST"):
            request(v3)
        with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "RUNTIME_REQUEST_V4_REQUIRED_FOR_DECISION_V4"):
            BRIDGE._derive_runtime_request(
                self.natural, expected_source_commit=self.natural["source_commit"], account_state=None,
                open_position_risk=None, runtime_config=None,
            )
        packet = BRIDGE.build_runtime_request(
            self.natural, expected_source_commit=self.natural["source_commit"], account_state=None,
            open_position_risk=None, runtime_config=None,
        )
        self.assertEqual(packet["schema_version"], "crypto_paper_runtime_request/4")
        self.assertEqual(packet["status"], "WAIT_RUNTIME_INPUTS_MISSING")
        self.assertEqual(packet["blockers"], [
            "RUNTIME_INPUT_MISSING:" + name for name in (
                "ALLOCATION_ENVELOPE", "EXIT_INTENTS", "PAPER_ACCOUNT_STATE", "POSITION_FILLS",
                "USER_RATIFIED_RUNTIME_CONFIG",
            )
        ])
        self.assertEqual(BRIDGE.validate_runtime_request(packet), packet)
        natural = request(self.natural)
        self.assertEqual(natural["status"], "NO_ELIGIBLE_CANDIDATE")
        self.assertEqual(natural["wiring"]["simulator_market_regime_status"], "UNKNOWN")

    def test_multiple_candidates_share_one_session_budget(self):
        with lifted_regime_and_rotation():
            packet = request(self.lifted)
            self.assertEqual(BRIDGE.validate_runtime_request(packet), packet)
        self.assertEqual(packet["status"], "PAPER_INTENTS_READY")
        rows = by_market(packet["requests"])
        self.assertEqual(sorted(rows), ["KRW-ETH", "KRW-SHIB", "KRW-SOL", "KRW-SUI", "KRW-XRP"])
        record = packet["session_budget_record"]
        self.assertEqual(record["status"], "ALLOCATED")
        self.assertEqual(record["key"]["session_id"], "CRYPTO-2026-09-14")
        budget = Fraction(record["market_room"]["budget_krw"])
        self.assertEqual(Fraction(record["market_room"]["cap_krw"]), Fraction(CASH) * Fraction("0.15"))
        self.assertEqual(budget, Fraction(record["market_room"]["room_krw"]) / 3)
        self.assertEqual({line["allocated_krw"] for line in record["allocation"]}, {CORE.fstr(budget / 5)})
        self.assertIn("MARKET_ACTION_CAPPED:KRW-LINK:MARKET_REALTIME_FRESHNESS_NOT_FRESH:KRW-LINK:STALE", packet["blockers"])
        for market, row in rows.items():
            with self.subTest(market=market):
                intent, sizing = row["intent"], row["execution_sizing"]
                self.assertEqual((intent["side"], intent["order_type"]), ("BUY", "LIMIT"))
                self.assertEqual(intent["market_regime_status"], "PASS")
                self.assertEqual(intent["expires_at"], "2026-09-15T07:00:00Z")
                self.assertEqual(intent["quantity"], sizing["quantity"])
                self.assertGreaterEqual(Decimal(intent["limit_price"]), Decimal(sizing["best_price"]))
                self.assertLessEqual(Fraction(sizing["submitted_amount_krw"]), Fraction(row["order_draft"]["allocated_krw"]))
                self.assertLessEqual(Fraction(sizing["expected_slippage_bps"]), 150)
                self.assertEqual(intent["idempotency_key"], row["order_draft"]["duplicate_guard_key"])

    def test_restart_reuses_the_record_and_a_later_decision_in_the_session_is_blocked(self):
        with lifted_regime_and_rotation():
            first = request(self.lifted)
            rerun = request(self.lifted, recorded_session_budget=first["session_budget_record"])
            self.assertEqual(rerun["requests"], first["requests"])
            earlier = SB.build_session_budget_record(
                CORE.load_core(), market="CRYPTO", session_id="CRYPTO-2026-09-14",
                decision_at_utc="2026-09-14T23:13:00Z",
                nav_snapshot={"as_of_utc": "2026-09-14T23:13:00Z", "virtual_cash_krw": CASH, "holdings": [],
                              "open_buy_reservations": [], "fx_observation": None},
                envelope=envelope("2026-09-14T23:13:00Z"), candidates=[],
            )
            later = request(self.lifted, recorded_session_budget=earlier)
            self.assertEqual(BRIDGE.validate_runtime_request(later), later)
        self.assertEqual(later["status"], "WAIT_SESSION_BUDGET_ALLOCATED")
        self.assertEqual(later["requests"], [])
        self.assertIn("SESSION_BUDGET_ALREADY_ALLOCATED:CRYPTO-2026-09-14", later["blockers"])
        conflicting = copy.deepcopy(first["session_budget_record"])
        conflicting["allocation"][0]["allocated_krw"] = "1"
        conflicting = CORE.sign({k: v for k, v in conflicting.items() if k != "record_sha256"}, "record_sha256")
        with lifted_regime_and_rotation(), self.assertRaises(BRIDGE.CryptoPaperRuntimeBridgeError):
            request(self.lifted, recorded_session_budget=conflicting)

    def test_carried_open_order_is_a_reservation_not_a_blanket_block(self):
        with lifted_regime_and_rotation():
            packet = request(self.lifted, account_state=account(self.lifted, open_order_market="KRW-BTC"))
        self.assertEqual(len(packet["requests"]), 5)
        self.assertFalse(any(b.startswith("NEW_INTENT_BLOCKED_PENDING_OPEN_ORDERS") for b in packet["blockers"]))
        reservations = packet["session_budget_record"]["inputs"]["nav_snapshot"]["open_buy_reservations"]
        self.assertEqual([row["order_id"] for row in reservations], ["PAPER.BUY.KRW-BTC.CARRIED.TEST"])
        self.assertEqual(packet["match_snapshots"][0]["market"], "KRW-BTC")

    def test_duplicate_key_skips_only_that_market(self):
        key = ELIGIBILITY.compute_reentry_key(self.lifted["generation_id"], "KRW-SOL")["duplicate_guard_key"]
        with lifted_regime_and_rotation():
            packet = BRIDGE.build_runtime_request(
                self.lifted, expected_source_commit=self.lifted["source_commit"],
                public_code_commit_sha=CODE_COMMIT, observation_commit_sha=OBSERVATION_COMMIT,
                account_state=account(self.lifted), open_position_risk=None, runtime_config=config(),
                known_idempotency_keys=[key], allocation_envelope=envelope(self.lifted["generated_at"]),
                recorded_session_budget=None, position_fills=[], exit_intents=[],
            )
        self.assertNotIn("KRW-SOL", by_market(packet["requests"]))
        self.assertEqual(len(packet["requests"]), 4)

    def test_exit_intent_becomes_a_sell_request_and_stops_new_buys_in_that_market(self):
        policy = EXIT.load_policy()
        position = {"market": "CRYPTO", "symbol": "KRW-ETH", "position_episode_id": "PE-TEST-ETH",
                    "rotation_scope_id": "BTC_RELATIVE_BUCKETS", "rotation_entity_id": "ETH",
                    "entry_rotation_as_of_date": "2026-08-23", "first_fill_at": "2026-08-24T20:20:00Z",
                    "quantity": "1"}
        trigger = {"reason_code": EXIT.REASON_TIME_STOP, "fact": "CLOCK", "deadline_at": "2026-09-14T20:20:00Z",
                   "decision_snapshot_id": "TEST.SNAPSHOT", "decision_snapshot_captured_at": "2026-09-14T23:43:00Z"}
        intent = EXIT.build_exit_intent(policy, position, trigger, "2026-09-14T23:43:00Z")
        with lifted_regime_and_rotation():
            packet = request(self.lifted, account_state=account(self.lifted, eth_position=True),
                             exit_intents=[{"intent": intent, "remaining_quantity": "1"}])
            self.assertEqual(BRIDGE.validate_runtime_request(packet), packet)
        sell = packet["sell_requests"][0]
        self.assertEqual((sell["market"], sell["exit_reason_code"]), ("KRW-ETH", EXIT.REASON_TIME_STOP))
        self.assertEqual((sell["intent"]["side"], sell["intent"]["order_type"]), ("SELL", "LIMIT"))
        self.assertLessEqual(Decimal(sell["intent"]["quantity"]), Decimal("1"))
        self.assertNotIn("KRW-ETH", by_market(packet["requests"]))
        self.assertIn("NEW_BUY_STOPPED_BY_OPEN_EXIT_INTENT:KRW-ETH", packet["blockers"])
        with lifted_regime_and_rotation():
            over = request(self.lifted, account_state=account(self.lifted),
                           exit_intents=[{"intent": intent, "remaining_quantity": "1"}])
        self.assertIn("EXIT_INTENT_POSITION_QUANTITY_MISMATCH:KRW-ETH", over["blockers"])

    # -- activation fixes (#763 review items 3-5) ---------------------------

    @staticmethod
    def exit_intent():
        policy = EXIT.load_policy()
        position = {"market": "CRYPTO", "symbol": "KRW-ETH", "position_episode_id": "PE-TEST-ETH",
                    "rotation_scope_id": "BTC_RELATIVE_BUCKETS", "rotation_entity_id": "ETH",
                    "entry_rotation_as_of_date": "2026-08-23", "first_fill_at": "2026-08-24T20:20:00Z",
                    "quantity": "1"}
        trigger = {"reason_code": EXIT.REASON_TIME_STOP, "fact": "CLOCK", "deadline_at": "2026-09-14T20:20:00Z",
                   "decision_snapshot_id": "TEST.SNAPSHOT", "decision_snapshot_captured_at": "2026-09-14T23:43:00Z"}
        return EXIT.build_exit_intent(policy, position, trigger, "2026-09-14T23:43:00Z")

    def account_with(self, value) -> dict:
        held = [row["market"] for row in SIMULATOR.build_account_state(
            value, observed_at=self.lifted["generated_at"], mark_prices={"KRW-ETH": "1"} if self._has_eth(value) else {},
            mark_freshness_status="FRESH", mark_source_ref="t", mark_source_sha256="d" * 64,
        )["positions"]]
        marks = BRIDGE.latest_mark_prices_by_market(self.lifted, held)
        return SIMULATOR.build_account_state_per_market(
            value, observed_at=self.lifted["generated_at"], mark_prices=marks["marks"],
            mark_status=marks["mark_status"], mark_source_ref=marks["source_ref"],
            mark_source_sha256=marks["source_sha256"],
        )

    @staticmethod
    def _has_eth(value) -> bool:
        return any(event["event_type"] == "FILL_APPLIED" for event in value["events"])

    @staticmethod
    def open_order(value, *, market, side, expires_at, submitted_at="2026-09-14T23:10:00Z", suffix="TEST"):
        intent = SIMULATOR.build_intent(
            order_id=f"PAPER.{side}.{market}.{suffix}", idempotency_key=f"PAPER.SUBMIT.{side}.{market}.{suffix}",
            market=market, side=side, order_type="LIMIT", quantity="1" if side == "SELL" else "0.1",
            limit_price="1000", fee_rate="0", queue_fraction="1", submitted_at=submitted_at,
            expires_at=expires_at, market_regime_status="PASS", source_plan_ref="test://plan",
            source_plan_sha256="b" * 64, source_evidence_ref="test://book", source_evidence_sha256="c" * 64,
        )
        return SIMULATOR.submit_order(value, intent)

    def run_request(self, *, account_state, known=(), recorded=None, exits=(), envelope_state="RISK_ON"):
        return BRIDGE.build_runtime_request(
            self.lifted, expected_source_commit=self.lifted["source_commit"],
            public_code_commit_sha=CODE_COMMIT, observation_commit_sha=OBSERVATION_COMMIT,
            account_state=account_state, open_position_risk=None, runtime_config=config(),
            known_idempotency_keys=list(known), allocation_envelope=envelope(self.lifted["generated_at"], envelope_state),
            recorded_session_budget=recorded, position_fills=[],
            exit_intents=[{"intent": intent, "remaining_quantity": "1"} for intent in exits],
        )

    def test_restart_after_partial_submission_submits_only_the_remaining_lines(self):
        with lifted_regime_and_rotation():
            first = request(self.lifted)
            record = first["session_budget_record"]
            submitted = first["requests"][:2]
            value = ledger()
            for row in submitted:
                value = SIMULATOR.submit_order(value, row["intent"])
            rerun = self.run_request(account_state=self.account_with(value), recorded=record,
                                     known=[row["intent"]["idempotency_key"] for row in submitted])
            self.assertEqual(BRIDGE.validate_runtime_request(rerun), rerun)
            self.assertEqual(rerun["session_budget_record"], record)
            self.assertTrue(rerun["wiring"]["session_budget_record_reused"])
            self.assertEqual([row["intent"] for row in rerun["requests"]], [row["intent"] for row in first["requests"][2:]])
            for row in first["requests"][2:]:
                value = SIMULATOR.submit_order(value, row["intent"])
            done = self.run_request(account_state=self.account_with(value), recorded=record,
                                    known=[row["intent"]["idempotency_key"] for row in first["requests"]])
        self.assertEqual(done["requests"], [])
        self.assertEqual(done["session_budget_record"], record)
        self.assertNotEqual(done["status"], "PAPER_INTENTS_READY")

    def test_exit_sell_is_valid_through_the_next_slot_and_expired_sells_do_not_block_reissue(self):
        intent = self.exit_intent()
        with lifted_regime_and_rotation():
            fresh = self.run_request(account_state=account(self.lifted, eth_position=True), exits=[intent])
            stale_sell = self.open_order(ledger(eth_position=True), market="KRW-ETH", side="SELL",
                                         expires_at="2026-09-14T23:40:00Z")
            reissued = self.run_request(account_state=self.account_with(stale_sell), exits=[intent])
            live_sell = self.open_order(ledger(eth_position=True), market="KRW-ETH", side="SELL",
                                        expires_at="2026-09-15T00:00:00Z")
            blocked = self.run_request(account_state=self.account_with(live_sell), exits=[intent])
        self.assertEqual(fresh["sell_requests"][0]["intent"]["expires_at"], "2026-09-15T00:30:00Z")
        self.assertEqual(fresh["wiring"]["sell_order_valid_before_utc"], "2026-09-15T00:30:00Z")
        self.assertEqual(len(reissued["sell_requests"]), 1)
        self.assertEqual(blocked["sell_requests"], [])
        self.assertIn("EXIT_SELL_ORDER_ALREADY_OPEN:KRW-ETH", blocked["blockers"])

    def exit_orders_at(self, generated_at: str, value) -> tuple:
        """``_exit_orders`` for the natural book re-stamped at another decision instant (test-only)."""
        decision = dict(self.lifted, generated_at=generated_at)
        marks = BRIDGE.latest_mark_prices_by_market(self.lifted, ["KRW-ETH"])
        state = SIMULATOR.build_account_state_per_market(
            value, observed_at=generated_at, mark_prices=marks["marks"], mark_status=marks["mark_status"],
            mark_source_ref=marks["source_ref"], mark_source_sha256=marks["source_sha256"],
        )
        intents = {e["order_id"]: e["payload"]["intent"] for e in value["events"] if e["event_type"] == "ORDER_SUBMITTED"}
        core = CORE.load_core()
        bounds = SB.session_bounds(core, SB.crypto_session_id(core, generated_at))
        return BRIDGE._exit_orders(
            decision, exit_intents=[{"intent": self.exit_intent(), "remaining_quantity": "1"}], checked_account=state,
            intent_by_order_id=intents, config=config(), threshold_bp=150, regime_status="PASS",
            source_root=ROOT, session_order_valid_before=bounds["order_valid_before_utc"],
        )

    def test_sell_cut_short_by_the_session_is_deferred_to_the_next_session(self):
        self.assertEqual(BRIDGE.sell_order_valid_before("2026-09-15T06:40:00Z", "2026-09-15T07:00:00Z"),
                         "2026-09-15T07:00:00Z")
        self.assertEqual(BRIDGE.sell_order_valid_before("2026-09-14T23:43:42Z", "2026-09-15T07:00:00Z"),
                         "2026-09-15T00:30:00Z")
        value = ledger(eth_position=True)
        # 06:40Z (06:30 slot): the 07:30Z slot bound would be cut to 07:00Z -> no sell, deferred.
        sells, _cancels, blockers, markets = self.exit_orders_at("2026-09-15T06:40:00Z", value)
        self.assertEqual(sells, [])
        self.assertIn("EXIT_SELL_DEFERRED_TO_NEXT_SESSION:KRW-ETH", blockers)
        self.assertEqual(markets, {"KRW-ETH"})
        # 06:10Z (06:00 slot): the slot bound equals the session end -> issued, expires 07:00Z.
        sells, _cancels, blockers, _markets = self.exit_orders_at("2026-09-15T06:10:00Z", value)
        self.assertEqual(sells[0]["intent"]["expires_at"], "2026-09-15T07:00:00Z")
        # 07:10Z: the next session's first decision issues it.
        sells, _cancels, blockers, _markets = self.exit_orders_at("2026-09-15T07:10:00Z", value)
        self.assertNotIn("EXIT_SELL_DEFERRED_TO_NEXT_SESSION:KRW-ETH", blockers)
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]["intent"]["expires_at"], "2026-09-15T08:00:00Z")

    def test_stale_or_mismatched_private_buy_inputs_let_exits_proceed(self):
        self.assertEqual(BRIDGE.BUY_SIDE_FAILURES_EXITS_MAY_PROCEED, {
            "ALLOCATION_ENVELOPE_NOT_THIS_DECISION", "ALLOCATION_ENVELOPE_STATE_NOT_DECISION_REGIME",
            "RECORDED_SESSION_BUDGET_NOT_THIS_SESSION", "RECORDED_SESSION_BUDGET_STATE_NOT_DECISION_REGIME",
        })
        generated = self.lifted["generated_at"]
        cases = {
            "missing": ({"allocation_envelope": None}, "RUNTIME_INPUT_MISSING:ALLOCATION_ENVELOPE"),
            "state": ({"allocation_envelope": envelope(generated, "NEUTRAL")},
                      "BUY_SIDE_BLOCKED_EXITS_PROCEED:ALLOCATION_ENVELOPE_STATE_NOT_DECISION_REGIME"),
            "stale": ({"allocation_envelope": envelope("2026-09-14T23:13:00Z")},
                      "BUY_SIDE_BLOCKED_EXITS_PROCEED:ALLOCATION_ENVELOPE_NOT_THIS_DECISION"),
            "other_session_record": ({"recorded_session_budget": budget_record()},
                                     "BUY_SIDE_BLOCKED_EXITS_PROCEED:RECORDED_SESSION_BUDGET_NOT_THIS_SESSION"),
        }
        for name, (overrides, blocker) in cases.items():
            with self.subTest(case=name), lifted_regime_and_rotation():
                packet = request(self.lifted, account_state=account(self.lifted, eth_position=True),
                                 exit_intents=[{"intent": self.exit_intent(), "remaining_quantity": "1"}], **overrides)
                self.assertEqual(BRIDGE.validate_runtime_request(packet), packet)
                self.assertEqual(packet["status"], "PAPER_INTENTS_READY")
                self.assertEqual(len(packet["sell_requests"]), 1)
                self.assertEqual(packet["requests"], [])
                self.assertIn(blocker, packet["blockers"])
        with lifted_regime_and_rotation(), self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError, "^ALLOCATION_ENVELOPE_NOT_THIS_DECISION$",
        ):
            request(self.lifted, allocation_envelope=envelope("2026-09-14T23:13:00Z"))  # no sells: still raises

    def test_tampered_envelope_raises_hash_mismatch_even_when_sells_exist(self):
        forged = envelope(self.lifted["generated_at"])
        forged["decision_at_utc"] = "2026-09-14T23:13:00Z"  # edited, not re-hashed
        with lifted_regime_and_rotation(), self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError, "^EXECUTION_CORE_REJECTED:ENVELOPE_SHA_MISMATCH$",
        ):
            request(self.lifted, account_state=account(self.lifted, eth_position=True),
                    exit_intents=[{"intent": self.exit_intent(), "remaining_quantity": "1"}],
                    allocation_envelope=forged)

    def test_deferred_sell_is_not_reported_as_issued(self):
        self.assertTrue(BRIDGE.sell_issuance_deferred("2026-09-15T06:40:00Z", "2026-09-15T07:00:00Z"))
        self.assertFalse(BRIDGE.sell_issuance_deferred("2026-09-15T06:10:00Z", "2026-09-15T07:00:00Z"))
        with lifted_regime_and_rotation():
            packet = request(self.lifted, account_state=account(self.lifted, eth_position=True),
                             exit_intents=[{"intent": self.exit_intent(), "remaining_quantity": "1"}])
        self.assertFalse(packet["wiring"]["sell_issuance_deferred_to_next_session"])
        self.assertEqual(packet["wiring"]["sell_order_valid_before_utc"], packet["sell_requests"][0]["intent"]["expires_at"])
        # A 06:40Z decision (last slot of the session) reports no sell validity.
        late = dict(self.lifted, generated_at="2026-09-15T06:40:00Z")
        with mock.patch.object(BRIDGE, "validate_decision_snapshot", side_effect=lambda value, **_: value), \
                mock.patch.object(BRIDGE, "_promotion_packet_v4", return_value=None), \
                mock.patch.object(BRIDGE, "_carried_open_order_matches",
                                  return_value={"match_snapshots": [], "blockers": [], "carried_open_order_ids": []}):
            deferred = BRIDGE._derive_runtime_request_v4(
                late, expected_source_commit=late["source_commit"], account_state=None, open_position_risk=None,
                runtime_config=None, allocation_envelope=None, recorded_session_budget=None,
                position_fills=None, exit_intents=None,
            )
        self.assertIsNone(deferred["wiring"]["sell_order_valid_before_utc"])
        self.assertTrue(deferred["wiring"]["sell_issuance_deferred_to_next_session"])

    def test_integrity_failures_on_the_buy_side_still_abort_when_sells_exist(self):
        tampered = copy.deepcopy(budget_record())
        tampered["allocation"][0]["allocated_krw"] = "1"
        tampered = CORE.sign({k: v for k, v in tampered.items() if k != "record_sha256"}, "record_sha256")
        with lifted_regime_and_rotation(), self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError, "^EXECUTION_CORE_REJECTED:",
        ):
            request(self.lifted, account_state=account(self.lifted, eth_position=True),
                    exit_intents=[{"intent": self.exit_intent(), "remaining_quantity": "1"}],
                    recorded_session_budget=tampered)
        real_rebuild = BRIDGE._promotion_packet_v4

        def inconsistent(decision, **kwargs):
            packet = real_rebuild(decision, **kwargs)
            packet["candidates"][0]["promotion_state"] = "BLOCKED" if packet["candidates"][0]["promotion_state"] != "BLOCKED" else "WATCH"
            return packet

        with lifted_regime_and_rotation(), mock.patch.object(BRIDGE, "_promotion_packet_v4", side_effect=inconsistent), \
                self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "^PROMOTION_REBUILD_INCONSISTENT_WITH_DECISION:"):
            request(self.lifted, account_state=account(self.lifted, eth_position=True),
                    exit_intents=[{"intent": self.exit_intent(), "remaining_quantity": "1"}])

    def test_open_buys_in_an_exit_market_are_cancelled_first(self):
        intent = self.exit_intent()
        with lifted_regime_and_rotation():
            value = self.open_order(ledger(eth_position=True), market="KRW-ETH", side="BUY",
                                    expires_at="2026-09-15T07:00:00Z", submitted_at="2026-09-14T23:00:00Z")
            packet = self.run_request(account_state=self.account_with(value), exits=[intent])
            self.assertEqual(BRIDGE.validate_runtime_request(packet), packet)
        self.assertEqual(packet["cancel_requests"], [{
            "market": "KRW-ETH", "order_id": "PAPER.BUY.KRW-ETH.TEST", "exit_intent_id": intent["intent_id"],
            "reason_code": "OPEN_BUY_CANCELLED_BEFORE_EXIT_ORDER",
        }])
        self.assertFalse(any("PAPER.BUY.KRW-ETH.TEST" in row["order_ids"] for row in packet["match_snapshots"]))
        reservations = packet["session_budget_record"]["inputs"]["nav_snapshot"]["open_buy_reservations"]
        self.assertEqual(reservations, [])
        self.assertEqual(len(packet["sell_requests"]), 1)

    def test_buy_side_failure_does_not_stop_exits(self):
        intent = self.exit_intent()
        with lifted_regime_and_rotation():
            packet = self.run_request(account_state=account(self.lifted, eth_position=True), exits=[intent],
                                      envelope_state="NEUTRAL")
            self.assertEqual(BRIDGE.validate_runtime_request(packet), packet)
        self.assertEqual(packet["status"], "PAPER_INTENTS_READY")
        self.assertEqual(len(packet["sell_requests"]), 1)
        self.assertEqual(packet["requests"], [])
        self.assertIn("BUY_SIDE_BLOCKED_EXITS_PROCEED:ALLOCATION_ENVELOPE_STATE_NOT_DECISION_REGIME", packet["blockers"])

    def test_envelope_state_must_match_the_decision_regime(self):
        with lifted_regime_and_rotation(), self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError, "ALLOCATION_ENVELOPE_STATE_NOT_DECISION_REGIME",
        ):
            request(self.lifted, allocation_envelope=envelope(self.lifted["generated_at"], "NEUTRAL"))

    def test_tampered_request_fails_rederivation(self):
        with lifted_regime_and_rotation():
            packet = request(self.lifted)
            forged = copy.deepcopy(packet)
            forged["requests"][0]["intent"]["quantity"] = "1000"
            forged["requests"][0]["intent"] = SIMULATOR._with_packet_sha(
                {k: v for k, v in forged["requests"][0]["intent"].items() if k != "packet_sha256"})
            forged["packet_sha256"] = BRIDGE.payload_sha256({k: v for k, v in forged.items() if k != "packet_sha256"})
            with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "RUNTIME_REQUEST_DERIVATION_MISMATCH"):
                BRIDGE.validate_runtime_request(forged)


if __name__ == "__main__":
    unittest.main()
