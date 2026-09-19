#!/usr/bin/env python3
"""Crypto PAPER wiring v2 follow-up: /4 consumers and the decision time bound.

1. ``portfolio/crypto_paper_stale_hold.py``, ``governance/rule_lineage_producers.py``
   and ``briefing/crypto_funnel_briefing.py`` accept decision snapshot /4
   (additive; /1-/3 outputs unchanged, issued briefing contract/3 frozen).
2. ``decision_time_not_before_inputs``: the workflow's ``generated_at`` is
   sampled after the bounded capture and truncated to the second, so a
   realtime message received later in that same second postdated the decision
   and the bridge rejected it (REALTIME_*_FUTURE_DATED, e.g. the committed
   2026-09-14 23:43:41 packet).  New packets are stamped at the first whole
   second no realtime input postdates (<= +1s); /4 enforces the invariant at
   build; committed packets re-derive byte-identically.
"""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import shutil
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


W = load("test_crypto_paper_wiring_v2_consumers_fixtures", "test/test_crypto_paper_wiring_v2.py")
DECISION = W.DECISION
BRIDGE = W.BRIDGE
HOLD = load("test_crypto_paper_wiring_v2_consumers_hold", "portfolio/crypto_paper_stale_hold.py")
BRIEFING = load("test_crypto_paper_wiring_v2_consumers_briefing", "briefing/crypto_funnel_briefing.py")
GAP = load("test_crypto_paper_wiring_v2_consumers_gap", ".github/scripts/check_crypto_decision_capture_gap.py")

from governance import rule_lineage_producers as LINEAGE  # noqa: E402
from governance import rule_refs as REFS  # noqa: E402

V4 = DECISION.V4_OUTPUT_SCHEMA_VERSION
V3 = DECISION.PER_MARKET_OUTPUT_SCHEMA_VERSION
# universe/crypto_candidate_promotion.py::T2_RULE_ID -- the contract/3 rule
# carried as BLOCKED_BY for a BLOCKED promotion.  Read, never re-derived.
T2_RULE_ID = "RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1"
COMMITTED_AT = "2026-09-14T23:43:41Z"
COMMITTED_BRIEFING_GLOB = "evidence/crypto_funnel_briefing/2026-09-14/2343/*/packet.json"
EARLIER_RUN_GLOB = "evidence/crypto_paper_decision/2026-09-14/2113/*/packet.json"


def realtime_entry(packet: dict) -> dict:
    return W.natural_entries(packet)["realtime_entry"]


@contextlib.contextmanager
def cutover_everywhere():
    with contextlib.ExitStack() as stack:
        for module in (DECISION, HOLD.DECISION, BRIEFING.DECISION):
            stack.enter_context(mock.patch.object(module, "v4_cutover_at", return_value=W.CUT))
        yield


class RepoTempMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix=".crypto_wiring_v2_consumers_", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, relative: str, value: dict) -> Path:
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path


# ---------------------------------------------------------------------------
# 1. /4 consumers
# ---------------------------------------------------------------------------

class StaleHoldV4Tests(unittest.TestCase):
    def test_v4_decision_is_a_per_market_input_and_v3_output_is_unchanged(self):
        v4 = W.replay(V4)
        with cutover_everywhere():
            state_v4 = HOLD.evaluate_stale_holds(v4, held_markets=["KRW-BTC", "KRW-WLD"])
        state_v3 = HOLD.evaluate_stale_holds(W.natural_packet(), held_markets=["KRW-BTC", "KRW-WLD"])
        rows_v4 = [(row["market"], row["exit_execution"]) for row in state_v4["markets"]]
        rows_v3 = [(row["market"], row["exit_execution"]) for row in state_v3["markets"]]
        self.assertEqual(rows_v4, rows_v3)
        self.assertEqual(dict(rows_v4)["KRW-WLD"], HOLD.HOLD)
        with self.assertRaisesRegex(HOLD.CryptoPaperStaleHoldError, "DECISION_PACKET_NOT_PER_MARKET_SCHEMA"):
            HOLD.evaluate_stale_holds(dict(v4, schema_version="crypto_paper_decision_snapshot_packet/9"),
                                      held_markets=[], revalidate_decision=False)


class LineageV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = REFS.RegistryContext.load()

    def test_v3_sidecar_keeps_its_gates_and_unapplied_rules(self):
        sidecar = LINEAGE.build_crypto_decision_sidecar(W.natural_packet(), self.context)
        gates = {event["gate"] for event in sidecar["events"]}
        self.assertEqual(gates, {"realtime_freshness_per_market", "realtime_liquidity_floor", "candidate_state"})
        state = next(event for event in sidecar["events"] if event["gate"] == "candidate_state")
        self.assertEqual(state["unapplied_rules"], sorted(LINEAGE.CRYPTO_CANDIDATE_UNAPPLIED, key=lambda r: r["rule_id"]))

    def test_v4_sidecar_cites_promotion_and_eligibility_rule_refs(self):
        with W.lifted_regime_and_rotation():
            v4 = W.replay(V4)
        sidecar = LINEAGE.build_crypto_decision_sidecar(v4, self.context)
        events = {(event["instrument"], event["gate"]): event for event in sidecar["events"]}
        eth = events[("KRW-ETH", "promotion_t2_required")]
        self.assertEqual(eth["event_type"], "DECISION")
        self.assertIn(("RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1", "APPLIED"),
                      {(ref["rule_id"], ref["role"]) for ref in eth["rule_refs"]})
        self.assertTrue(all(ref["registry_sha256"] == self.context.sha256 for ref in eth["rule_refs"]))
        self.assertEqual(events[("KRW-BTC", "promotion_t2_required")]["event_type"], "BLOCK")
        eligibility = events[("KRW-ETH", "buy_eligibility")]
        self.assertEqual(eligibility["outcome"]["eligibility_state"], "WATCH")
        state = events[("KRW-ETH", "candidate_state")]
        self.assertEqual(state["unapplied_rules"], list(LINEAGE.CRYPTO_CANDIDATE_UNAPPLIED_V4))

    def test_v4_promotion_watch_on_unknown_t2_is_a_decision_not_a_block(self):
        """WATCH is an undetermined gate, not a block, and carries no BLOCKED_BY.

        contract/3 ``aggregate_t2_state`` names the T2 rule BLOCKED_BY only for
        BLOCKED (a FAILED required condition).  An UNKNOWN one yields WATCH with
        every ref APPLIED, so emitting BLOCK for it would claim a blocking rule
        the packet never recorded and fail closed as
        BLOCK_EVENT_WITHOUT_BLOCKING_RULE.  The replay fixture above only ever
        produces BLOCKED or FOCUSED_REVIEW, which is why this shape went
        unnoticed until the first natural /4 packet (2026-09-18T07:15:38Z) came
        back WATCH on every market with a not-current runtime decision.
        """
        with W.lifted_regime_and_rotation():
            v4 = W.replay(V4)
        row = next(r for r in v4["candidates"] if r["market"] == "KRW-BTC")
        p5_08 = row["p5_08"]
        p5_08["promotion_state"] = "WATCH"
        p5_08["promotion_reason"] = "T2_REQUIRED_UNKNOWN:T2_ROTATION_MEMBERSHIP"
        p5_08["rule_refs"] = [dict(ref, role="APPLIED") for ref in p5_08["rule_refs"]]
        p5_08["unapplied_rules"] = []
        row["p5_09"] = None  # no eligibility row is produced below FOCUSED_REVIEW
        sidecar = LINEAGE.build_crypto_decision_sidecar(v4, self.context)
        event = next(e for e in sidecar["events"]
                     if (e["instrument"], e["gate"]) == ("KRW-BTC", "promotion_t2_required"))
        self.assertEqual(event["event_type"], "DECISION")
        self.assertEqual(event["outcome"]["promotion_state"], "WATCH")
        self.assertEqual({ref["role"] for ref in event["rule_refs"]}, {"APPLIED"})
        # BLOCKED still blocks, and still names the rule that did it: contract/3
        # marks its T2 rule BLOCKED_BY for exactly that state.
        p5_08["promotion_state"] = "BLOCKED"
        p5_08["promotion_reason"] = "T2_REQUIRED_FAILED:T2_ROTATION_MEMBERSHIP"
        p5_08["rule_refs"] = [
            dict(ref, role="BLOCKED_BY" if ref["rule_id"] == T2_RULE_ID else ref["role"])
            for ref in p5_08["rule_refs"]
        ]
        blocked = next(e for e in LINEAGE.build_crypto_decision_sidecar(v4, self.context)["events"]
                       if (e["instrument"], e["gate"]) == ("KRW-BTC", "promotion_t2_required"))
        self.assertEqual(blocked["event_type"], "BLOCK")
        self.assertIn("BLOCKED_BY", {ref["role"] for ref in blocked["rule_refs"]})


class BriefingV4Tests(RepoTempMixin, unittest.TestCase):
    def test_issued_contract_3_briefing_still_validates(self):
        paths = sorted(ROOT.glob(COMMITTED_BRIEFING_GLOB))
        if not paths:
            self.skipTest("committed briefing evidence not present in this checkout")
        packet = json.loads(paths[0].read_text(encoding="utf-8"))
        self.assertEqual(packet["contract_version"], "crypto_funnel_briefing_contract/3")
        self.assertEqual(BRIEFING.validate_briefing(packet), packet)

    def test_contract_4_builds_v4_and_v3_briefings(self):
        contract = BRIEFING.load_contract()
        self.assertEqual(contract["contract_version"], "crypto_funnel_briefing_contract/4")
        self.assertEqual(contract["source_schema_versions"][-1], V4)
        # The briefing re-derives with its own module instances, so natural
        # (unlifted) /4 bytes are used here.
        with cutover_everywhere():
            v4 = W.replay(V4)
            path = self.write("decision/v4.json", v4)
            relative = str(path.relative_to(ROOT))
            briefing = BRIEFING.build_briefing(v4, source_path=relative, source_file_sha256=BRIEFING._file_sha256(path))
            self.assertEqual(BRIEFING.validate_briefing(briefing), briefing)
        self.assertEqual(briefing["regime"]["crypto_paper_runtime_decision"]["runtime_regime"], "UNKNOWN")
        rows = {row["market"]: row for row in briefing["candidates"]}
        self.assertEqual(rows["KRW-ETH"]["promotion_state"], "WATCH")
        self.assertEqual(rows["KRW-ETH"]["t2_required_conditions"]["T2_ROTATION_MEMBERSHIP"]["status"], "UNKNOWN")
        self.assertIsNone(rows["KRW-ETH"]["eligibility_state"])
        v3 = W.natural_packet()
        v3_path = self.write("decision/v3.json", v3)
        v3_briefing = BRIEFING.build_briefing(
            v3, source_path=str(v3_path.relative_to(ROOT)), source_file_sha256=BRIEFING._file_sha256(v3_path),
        )
        self.assertNotIn("crypto_paper_runtime_decision", v3_briefing["regime"])
        self.assertNotIn("promotion_state", v3_briefing["candidates"][0])
        frozen = BRIEFING._expected_v3_contract()
        with self.assertRaisesRegex(BRIEFING.CryptoFunnelBriefingError, "SOURCE_SCHEMA_VERSION_INVALID"), \
                cutover_everywhere():
            BRIEFING.build_briefing(v4, source_path=relative, source_file_sha256=BRIEFING._file_sha256(path),
                                    contract=frozen)


# ---------------------------------------------------------------------------
# 2. Decision time never before its realtime inputs
# ---------------------------------------------------------------------------

class DecisionTimeBoundTests(RepoTempMixin, unittest.TestCase):
    def test_bound_on_committed_runs(self):
        packet = W.natural_packet()
        entry = realtime_entry(packet)
        self.assertEqual(DECISION.realtime_inputs_latest_at(entry["record"]).isoformat(), "2026-09-14T23:43:41.166628+00:00")
        self.assertEqual(DECISION.decision_time_not_before_inputs(COMMITTED_AT, entry), "2026-09-14T23:43:42Z")
        self.assertEqual(DECISION.decision_time_not_before_inputs("2026-09-14T23:43:42Z", entry), "2026-09-14T23:43:42Z")
        self.assertEqual(DECISION.decision_time_not_before_inputs(COMMITTED_AT, None), COMMITTED_AT)
        with self.assertRaisesRegex(DECISION.CryptoPaperDecisionSnapshotError, "MORE_THAN_ONE_SECOND"):
            DECISION.decision_time_not_before_inputs("2026-09-14T23:43:39Z", entry)
        earlier = sorted(ROOT.glob(EARLIER_RUN_GLOB))
        if earlier:
            other = json.loads(earlier[0].read_text(encoding="utf-8"))
            try:
                other_entry = realtime_entry(other)
            except FileNotFoundError:
                other_entry = None
            if other_entry is not None:
                self.assertEqual(DECISION.decision_time_not_before_inputs(other["generated_at"], other_entry),
                                 other["generated_at"])

    def test_committed_v3_packet_replays_unchanged_and_v4_enforces_the_invariant(self):
        committed = W.natural_packet()
        self.assertEqual(DECISION.validate_output(committed), committed)
        with self.assertRaisesRegex(DECISION.CryptoPaperDecisionSnapshotError, "REALTIME_INPUT_AFTER_DECISION"):
            W.replay(V4, generated_at=COMMITTED_AT)  # the committed, input-preceding stamp
        self.assertEqual(W.replay(V4, generated_at="2026-09-14T23:43:42Z")["generated_at"], "2026-09-14T23:43:42Z")

    def populate(self, **kwargs):
        packet = W.natural_packet()
        realtime = ROOT / next(r["path"] for r in packet["source_refs"] if r["role"] == "upbit_realtime_capture_run")
        leadership = ROOT / next(r["path"] for r in packet["source_refs"] if r["role"] == "crypto_leadership_packet")
        leadership_root = self.tmp / "leadership_input"
        (leadership_root / "2026-09-13").mkdir(parents=True, exist_ok=True)
        (leadership_root / "2026-09-13" / "packet.json").write_bytes(leadership.read_bytes())
        return DECISION.populate(
            generated_at=COMMITTED_AT, source_commit=packet["source_commit"], realtime_run_path=realtime,
            output_root=self.tmp / "out", leadership_data_root=leadership_root, **kwargs,
        )

    def test_new_v3_packet_is_stamped_after_its_inputs_and_the_bridge_accepts_it(self):
        output = self.tmp / "github_output"
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
            result = self.populate()
            DECISION._write_github_output(result)
        record = result["record"]
        self.assertEqual((record["schema_version"], record["generated_at"]), (V3, "2026-09-14T23:43:42Z"))
        self.assertEqual(DECISION.validate_output(record), record)
        self.assertEqual(output.read_text(encoding="utf-8").splitlines()[-3], "generated_at=2026-09-14T23:43:42Z")
        request = BRIDGE.build_runtime_request(
            record, expected_source_commit=record["source_commit"], account_state=None,
            open_position_risk=None, runtime_config=None,
        )
        self.assertEqual(request["schema_version"], "crypto_paper_runtime_request/3")
        self.assertEqual(BRIDGE.orderbook_snapshot(record, market="KRW-ETH")["market"], "KRW-ETH")
        # The committed packet stamped 23:43:41 still fails closed at the bridge.
        with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "FUTURE_DATED"):
            BRIDGE.orderbook_snapshot(W.natural_packet(), market="KRW-ETH")

    def test_bound_crossing_utc_midnight_writes_no_packet(self):
        record = {"run": {"ended_at": "2026-09-14T23:59:59Z", "status": {"generated_at": "2026-09-14T23:59:59Z"},
                          "latest_public_messages": {"ticker|-|KRW-BTC": {"received_at": "2026-09-14T23:59:59.400000Z"}},
                          "message_log": []}}
        self.assertEqual(
            DECISION.decision_time_not_before_inputs("2026-09-14T23:59:59Z", {"record": record}),
            "2026-09-15T00:00:00Z",
        )
        with mock.patch.object(DECISION, "decision_time_not_before_inputs", return_value="2026-09-15T00:00:00Z"):
            result = self.populate()
        self.assertEqual((result["outcome"], result["evaluation_status"], result["decision_state"]),
                         ("not_evaluated", "NOT_EVALUATED", "WAIT"))
        self.assertEqual(result["reason"], "WAIT:DECISION_TIME_BOUND_CROSSES_UTC_DATE")
        self.assertEqual((result["record"], result["path"], result["generated_at"]), (None, None, COMMITTED_AT))
        self.assertFalse((self.tmp / "out" / "2026-09-15").exists())

    def test_capture_gap_guard_accepts_only_the_input_bounded_stamp(self):
        packet = W.natural_packet()
        run_path = ROOT / next(r["path"] for r in packet["source_refs"] if r["role"] == "upbit_realtime_capture_run")
        stamped = self.write("packet.json", {"generated_at": "2026-09-14T23:43:42Z"})
        result = GAP.measure(realtime_run_path=run_path, decision_generated_at=COMMITTED_AT, decision_packet_path=stamped)
        self.assertEqual((result["decision_generated_at"], result["status"]), ("2026-09-14T23:43:42Z", GAP.WITHIN_BUDGET))
        too_late = self.write("late.json", {"generated_at": "2026-09-14T23:43:43Z"})
        with self.assertRaisesRegex(GAP.CaptureGapError, "DECISION_PACKET_GENERATED_AT_MISMATCH"):
            GAP.measure(realtime_run_path=run_path, decision_generated_at=COMMITTED_AT, decision_packet_path=too_late)
        with self.assertRaisesRegex(GAP.CaptureGapError, "DECISION_PACKET_GENERATED_AT_MISMATCH"):
            GAP.measure(realtime_run_path=run_path, decision_generated_at="2026-09-14T23:43:42Z",
                        decision_packet_path=self.write("next.json", {"generated_at": "2026-09-14T23:43:43Z"}))


if __name__ == "__main__":
    unittest.main()
