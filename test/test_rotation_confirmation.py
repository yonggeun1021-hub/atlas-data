"""User-ratified rotation confirmation layer regression (RULE.ROTATION.*.V1, 2026-09-15)."""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RC = load_module("rotation_confirmation_under_test", ROOT / "rotation" / "rotation_confirmation.py")
POLICY = RC.load_policy()
MAPPING = RC.load_state_mapping()
US = POLICY["markets"]["US"]["entities"]
RATIFICATION_SHA = "c6f5dbbe36f3eabc104db9c547ba99d84300fd5b7ef4d801a71c76a071b47116"


def us_obs(day: str, ranking: list, status="OBSERVED") -> dict:
    """``ranking`` lists the top of the order; remaining SPDRs follow alphabetically."""
    order = list(ranking) + [s for s in US if s not in ranking]
    entities = [
        {"entity_id": s, "source_identity": s, "strength": str(20 - index)}
        for index, s in enumerate(order)
    ]
    if status != "OBSERVED":
        return {"as_of_date": day, "status": "UNKNOWN", "unknown_reason": "TEST", "sources": [], "scopes": {}, "aux": {}}
    return {"as_of_date": day, "status": "OBSERVED", "unknown_reason": None, "sources": [{"path": f"fixture/{day}"}],
            "scopes": {"SPY": entities}, "aux": {}}


def states(packet: dict) -> dict:
    return {e["entity_id"]: e["state"] for scope in packet["scopes"] for e in scope["entities"]}


def entity(packet: dict, entity_id: str) -> dict:
    return next(e for scope in packet["scopes"] for e in scope["entities"] if e["entity_id"] == entity_id)


def days(start: str, count: int, step: int = 1) -> list:
    base = dt.date.fromisoformat(start)
    return [(base + dt.timedelta(days=i * step)).isoformat() for i in range(count)]


class PolicyBindingTests(unittest.TestCase):
    def test_policy_is_bound_to_user_ratification_record_sha(self):
        record = POLICY["ratification_record"]
        self.assertEqual(record["sha256"], RATIFICATION_SHA)
        self.assertEqual(RC.file_sha256(ROOT / record["repo_path"]), RATIFICATION_SHA)
        self.assertEqual(
            {m: POLICY["markets"][m]["ratification_status"] for m in RC.MARKETS},
            {"CRYPTO": "RATIFIED", "KR": "TEMPORARY", "US": "PROVISIONAL"},
        )
        self.assertEqual(
            {m: POLICY["markets"][m]["evidence_badge_ko"] for m in RC.MARKETS},
            {"CRYPTO": "경계선 통과", "US": "증거 부족·잠정", "KR": "관찰 시작·잠정"},
        )

    def _tmp_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        for relative in (RC.POLICY_RELATIVE_PATH, POLICY["ratification_record"]["repo_path"],
                         POLICY["markets"]["KR"]["source"]["sector_policy_path"], RC.STATE_MAPPING_RELATIVE_PATH):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, root / relative)
        return tmp, root

    def test_tampered_record_or_policy_fails_closed(self):
        tmp, root = self._tmp_root()
        with tmp:
            record = root / POLICY["ratification_record"]["repo_path"]
            record.write_bytes(record.read_bytes() + b" ")
            with self.assertRaisesRegex(RC.RotationConfirmationError, "RATIFICATION_RECORD_SHA_MISMATCH"):
                RC.load_policy(root)
        for mutate, code in (
            (lambda p: p["markets"]["KR"]["strength_basis"]["twenty_session_basis"].update(enabled=True), "KR_TWENTY_SESSION_BASIS_NOT_RATIFIED"),
            (lambda p: p["markets"]["US"].update(ratification_status="RATIFIED"), "POLICY_STATUS_MISMATCH"),
            (lambda p: p["confirmation"].update(enter_consecutive_top_observations=1), "POLICY_CONFIRMATION_RULE_MISMATCH"),
            (lambda p: p["release_handling"].update(forced_exit=True), "POLICY_RELEASE_HANDLING_MISMATCH"),
            (lambda p: p["common"].update(t1_selection_states=["STRONG_CONFIRMED", "STRONG_HELD", "EMERGING_WATCH"]), "POLICY_COMMON_STATES_MISMATCH"),
            (lambda p: p["markets"]["US"]["entities"].append("SMH"), "US_SPDR_ENTITIES_MISMATCH"),
        ):
            tmp, root = self._tmp_root()
            with tmp:
                value = json.loads((root / RC.POLICY_RELATIVE_PATH).read_text(encoding="utf-8"))
                mutate(value)
                (root / RC.POLICY_RELATIVE_PATH).write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(RC.RotationConfirmationError, code):
                    RC.load_policy(root)

    def test_rule_refs_inline_shape_matches_rule_refs_library_fields(self):
        refs = RC.market_rule_refs(POLICY, "KR")
        self.assertEqual([r["rule_id"] for r in refs], [
            "RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1", "RULE.ROTATION.KR.V1T", "RULE.ROTATION.RELEASE_HANDLING.V1",
        ])
        for ref in refs:
            self.assertEqual(set(ref), {"rule_id", "version", "registry_sha256", "source_record_sha256", "role"})
            self.assertEqual((ref["version"], ref["source_record_sha256"], ref["role"]), (1, RATIFICATION_SHA, "APPLIED"))
        self.assertEqual(POLICY["markets"]["CRYPTO"]["rule_id"], "RULE.ROTATION.CRYPTO.V1")
        self.assertEqual(POLICY["markets"]["US"]["rule_id"], "RULE.ROTATION.US.V1P")


class StateMachineTests(unittest.TestCase):
    def build(self, observations, market="US"):
        return RC.build_market_packets(POLICY, market, observations, MAPPING)

    def test_confirm_after_two_consecutive_top_then_hold(self):
        d = days("2026-09-01", 4)
        packets = self.build([us_obs(d[0], ["XLK", "XLE", "XLV"]), us_obs(d[1], ["XLK", "XLE", "XLF"]),
                              us_obs(d[2], ["XLK", "XLC", "XLE"]), us_obs(d[3], ["XLK", "XLE", "XLC"])])
        self.assertEqual(states(packets[0])["XLK"], "NEUTRAL")
        self.assertEqual(entity(packets[0], "XLK")["top_streak"], 1)
        self.assertEqual(states(packets[1])["XLK"], "STRONG_CONFIRMED")
        self.assertEqual(states(packets[1])["XLV"], "NEUTRAL")
        self.assertEqual(states(packets[2])["XLK"], "STRONG_HELD")
        xlk = entity(packets[3], "XLK")
        self.assertEqual((xlk["state"], xlk["state_since_date"], xlk["observations_in_state"]), ("STRONG_HELD", d[1], 3))
        self.assertEqual(states(packets[3])["XLC"], "STRONG_CONFIRMED")
        self.assertTrue(xlk["decision_eligible"])
        self.assertEqual(packets[3]["summary"]["counts"]["STRONG_HELD"], 2)

    def test_release_on_two_non_top_or_single_bottom_and_no_forced_exit(self):
        d = days("2026-09-01", 5)
        order_bottom = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLP", "XLRE", "XLU", "XLV", "XLY", "XLK"]
        packets = self.build([
            us_obs(d[0], ["XLK", "XLE", "XLV"]), us_obs(d[1], ["XLK", "XLE", "XLV"]),
            us_obs(d[2], ["XLB", "XLC", "XLF", "XLK"]),  # XLK rank 4 = first non-top (MIDDLE)
            us_obs(d[3], ["XLB", "XLC", "XLF", "XLE"]),  # XLK second consecutive non-top -> released
            us_obs(d[4], order_bottom),
        ])
        self.assertEqual(states(packets[2])["XLK"], "STRONG_HELD")
        self.assertEqual(states(packets[3])["XLK"], "STRONG_RELEASED")
        released = entity(packets[3], "XLK")
        self.assertTrue(released["release_new_buy_stop"])
        self.assertFalse(released["forced_exit"])
        self.assertFalse(released["decision_eligible"])
        self.assertEqual(states(packets[4])["XLK"], "NEUTRAL")
        self.assertEqual(entity(packets[4], "XLK")["last_release_on"], d[3])
        self.assertEqual(packets[3]["decision_view"]["forced_exit"], [])

        packets = self.build([
            us_obs(d[0], ["XLK", "XLE", "XLV"]), us_obs(d[1], ["XLK", "XLE", "XLV"]), us_obs(d[2], order_bottom),
        ])
        self.assertEqual(entity(packets[2], "XLK")["bucket"], "BOTTOM")
        self.assertEqual(states(packets[2])["XLK"], "STRONG_RELEASED")  # bottom once

    def test_gap_above_maximum_resets_streaks_and_lapses_strong(self):
        packets = self.build([
            us_obs("2026-09-01", ["XLK"]), us_obs("2026-09-02", ["XLK"]), us_obs("2026-09-07", ["XLK"]),
            us_obs("2026-09-08", ["XLK"]),
        ])
        self.assertEqual(states(packets[1])["XLK"], "STRONG_CONFIRMED")
        after_gap = entity(packets[2], "XLK")
        self.assertTrue(packets[2]["chain"]["reset"])
        self.assertEqual((after_gap["state"], after_gap["top_streak"], after_gap["strong_lapsed_by_gap"]), ("NEUTRAL", 1, True))
        self.assertEqual(states(packets[3])["XLK"], "STRONG_CONFIRMED")
        # exactly the maximum gap (4 calendar days for US) keeps the chain
        packets = self.build([us_obs("2026-09-01", ["XLK"]), us_obs("2026-09-05", ["XLK"])])
        self.assertFalse(packets[1]["chain"]["reset"])
        self.assertEqual(states(packets[1])["XLK"], "STRONG_CONFIRMED")

    def test_entry_gate_view_exposes_state_days_and_per_entity_rule_refs(self):
        d = days("2026-09-01", 4)
        order_bottom = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLP", "XLRE", "XLU", "XLV", "XLY", "XLK"]
        packets = self.build([us_obs(d[0], ["XLK"]), us_obs(d[1], ["XLK"]), us_obs(d[2], ["XLK"]), us_obs(d[3], order_bottom)])
        gate = {row["entity_id"]: row for row in packets[2]["entry_gate_view"]}
        self.assertEqual(set(gate), set(US))
        xlk = gate["XLK"]
        self.assertEqual((xlk["state"], xlk["decision_eligible"], xlk["observations_in_state"], xlk["state_since_date"]),
                         ("STRONG_HELD", True, 2, d[1]))
        self.assertEqual([(r["rule_id"], r["role"]) for r in xlk["rule_refs"]],
                         [("RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1", "APPLIED"), ("RULE.ROTATION.US.V1P", "APPLIED")])
        self.assertIn(("RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1", "BLOCKED_BY"), [(r["rule_id"], r["role"]) for r in gate["XLY"]["rule_refs"]])
        released = {row["entity_id"]: row for row in packets[3]["entry_gate_view"]}["XLK"]
        self.assertEqual((released["state"], released["release_new_buy_stop"], released["forced_exit"]), ("STRONG_RELEASED", True, False))
        self.assertIn(("RULE.ROTATION.RELEASE_HANDLING.V1", "BLOCKED_BY"), [(r["rule_id"], r["role"]) for r in released["rule_refs"]])

    def test_unknown_observation_is_missing_not_inferred(self):
        packets = self.build([us_obs("2026-09-01", ["XLK"]), us_obs("2026-09-02", [], status="UNKNOWN"),
                              us_obs("2026-09-03", ["XLK"])])
        self.assertEqual(packets[1]["scopes"], [])
        self.assertEqual(packets[1]["observation"]["status"], "UNKNOWN")
        self.assertEqual(packets[1]["chain"]["last_observed_as_of_date"], "2026-09-01")
        self.assertEqual(states(packets[2])["XLK"], "STRONG_CONFIRMED")  # consecutive *observations*

    def test_emerging_watch_is_display_only(self):
        d = days("2026-09-01", 6)
        base = ["XLB", "XLC", "XLE"]
        obs = [us_obs(day, base) for day in d[:5]]
        # XLY starts alphabetically last among the rest (rank 11) and jumps to rank 4
        obs.append(us_obs(d[5], base + ["XLY"]))
        packets = self.build(obs)
        xly = entity(packets[5], "XLY")
        self.assertEqual(xly["state"], "EMERGING_WATCH")
        self.assertEqual((xly["emerging_watch"]["lookback_rank"], xly["rank"]), (11, 4))
        self.assertFalse(xly["decision_eligible"])
        self.assertNotIn("XLY", [row["entity_id"] for row in packets[5]["decision_view"]["t1_selection"]])
        self.assertEqual(packets[5]["decision_view"]["emerging_watch_display_only"], [{"scope_id": "SPY", "entity_id": "XLY"}])

    def test_crypto_rank_one_top_rank_three_bottom(self):
        def obs(day, alt, eth):
            return {"as_of_date": day, "status": "OBSERVED", "unknown_reason": None, "sources": [], "aux": {},
                    "scopes": {"BTC_RELATIVE_BUCKETS": [
                        {"entity_id": "ALT", "source_identity": "ALT", "strength": alt},
                        {"entity_id": "BTC", "source_identity": "BTC", "strength": "0"},
                        {"entity_id": "ETH", "source_identity": "ETH", "strength": eth},
                    ]}}
        packets = self.build([obs("2026-10-01", "0.05", "0.01"), obs("2026-10-02", "0.04", "0.02"),
                              obs("2026-10-03", "0.03", "-0.01"), obs("2026-10-04", "-0.02", "0.01")], market="CRYPTO")
        self.assertEqual([entity(p, "ALT")["bucket"] for p in packets], ["TOP", "TOP", "TOP", "BOTTOM"])
        self.assertEqual(states(packets[1])["ALT"], "STRONG_CONFIRMED")
        self.assertEqual(states(packets[3])["ALT"], "STRONG_RELEASED")  # rank 3 once
        self.assertEqual(entity(packets[3], "ALT")["lagging"]["status"], "UNKNOWN")
        self.assertEqual(entity(packets[3], "BTC")["lagging"]["status"], "EXCLUDED_BENCHMARK")

    def test_crypto_lagging_warning_uses_only_points_up_to_as_of(self):
        cfg = POLICY["markets"]["CRYPTO"]
        end = dt.date(2026, 11, 30)
        daily = {}
        for offset in range(37):
            day = (end - dt.timedelta(days=36 - offset)).isoformat()
            # ETH steadily underperforms BTC, ALT outperforms
            daily[day] = {"BTC": "1.000", "ETH": "0.990", "ALT": "1.010"}
        result = RC._crypto_lagging(daily, end.isoformat(), cfg)
        self.assertEqual(result["ETH"]["quadrant"], "LAGGING")
        self.assertTrue(result["ETH"]["lagging_warning"])
        self.assertEqual(result["ALT"]["quadrant"], "LEADING")
        future = dict(daily)
        future[(end + dt.timedelta(days=1)).isoformat()] = {"BTC": "1", "ETH": "2", "ALT": "0.5"}
        self.assertEqual(RC._crypto_lagging(future, end.isoformat(), cfg), result)
        self.assertEqual(RC._crypto_lagging(daily, (end + dt.timedelta(days=1)).isoformat(), cfg)["ETH"]["status"], "UNKNOWN")

    def test_ledger_mapping_annotation_uses_ratified_nine_cells(self):
        packets = self.build([us_obs("2026-09-01", ["XLK"]), us_obs("2026-09-02", ["XLE"])])
        xlk = entity(packets[1], "XLK")
        self.assertEqual((xlk["ledger_bucket_transition"], xlk["ledger_p2_state"]), ("TOP_TO_MIDDLE", "WEAKENING"))
        self.assertEqual(xlk["state"], "NEUTRAL")  # 9-cell WEAKENING is not a release without confirmation
        self.assertIsNone(entity(packets[0], "XLK")["ledger_p2_state"])


class LedgerAdapterTests(unittest.TestCase):
    def test_observations_from_validated_ledger_drive_the_same_confirmation(self):
        fixture = load_module("rotation_state_ledger_fixture_for_confirmation", ROOT / "test" / "test_rotation_state_ledger.py")
        first = fixture.crypto_packet("2026-08-20")
        ledger = fixture.MODULE.apply_rotation(first, fixture.policy_for(first), contract=fixture.CONTRACT)
        second = fixture.crypto_packet("2026-08-21")
        ledger = fixture.MODULE.apply_rotation(second, fixture.policy_for(second), ledger, contract=fixture.CONTRACT)
        observations = RC.observations_from_ledger(ledger, "CRYPTO", POLICY)
        self.assertEqual([o["as_of_date"] for o in observations], ["2026-08-20", "2026-08-21"])
        packets = RC.build_market_packets(POLICY, "CRYPTO", observations, MAPPING)
        records = {(r["as_of_date"], r["entity_id"]): r for r in ledger["records"]}
        for packet in packets:
            for scope in packet["scopes"]:
                for row in scope["entities"]:
                    record = records[(packet["as_of_date"], row["entity_id"])]
                    self.assertEqual(row["bucket"], record["structural_bucket_transition"].split("_TO_")[1])
                    self.assertIsNone(row["emerging_watch"]["condition_met"])  # ranks not in ledger
        self.assertEqual([states(p)["ETH"] for p in packets], ["NEUTRAL", "STRONG_CONFIRMED"])
        self.assertEqual([states(p)["BTC"] for p in packets], ["NEUTRAL", "NEUTRAL"])


class RetainedEvidenceReplayTests(unittest.TestCase):
    """Replay over committed evidence: deterministic, prefix-stable (no lookahead), append-only."""

    @classmethod
    def setUpClass(cls):
        cls.observations = {m: RC.EXTRACTORS[m](POLICY, ROOT) for m in RC.MARKETS}
        cls.packets = {m: RC.build_market_packets(POLICY, m, cls.observations[m], MAPPING) for m in RC.MARKETS}

    def test_rebuild_is_byte_deterministic(self):
        for market in RC.MARKETS:
            again = RC.build_market(market, ROOT, POLICY)
            self.assertEqual([RC.render_json(p) for p in again], [RC.render_json(p) for p in self.packets[market]])
            for packet in again:
                RC.validate_packet(packet)
                self.assertNotIn("generated_at", RC.canonical_json(packet))

    def test_packet_for_date_is_identical_when_later_evidence_is_absent(self):
        for market in RC.MARKETS:
            observations = self.observations[market]
            for index in range(len(observations)):
                prefix = RC.build_market_packets(POLICY, market, observations[: index + 1], MAPPING)
                self.assertEqual(RC.render_json(prefix[-1]), RC.render_json(self.packets[market][index]), (market, index))

    def test_committed_confirmation_packets_match_replay(self):
        for market in RC.MARKETS:
            built = {p["as_of_date"]: p for p in self.packets[market]}
            committed = sorted((ROOT / RC.EVIDENCE_RELATIVE_ROOT / market).glob("*/packet.json"))
            self.assertTrue(committed, market)
            for path in committed:
                self.assertIn(path.parent.name, built, (market, path))
                self.assertEqual(path.read_bytes(), RC.render_json(built[path.parent.name]), (market, path))

    def test_first_states_on_retained_evidence(self):
        us = {p["as_of_date"]: p for p in self.packets["US"]}
        self.assertEqual(
            sorted(e["entity_id"] for e in us["2026-09-11"]["scopes"][0]["entities"] if e["state"] in RC.STRONG_STATES),
            ["XLC", "XLE", "XLK"],
        )
        self.assertEqual(entity(us["2026-09-02"], "XLK")["state"], "STRONG_RELEASED")
        kr = {p["as_of_date"]: p for p in self.packets["KR"]}
        self.assertTrue(kr["2026-09-03"]["chain"]["reset"])  # 08-21 -> 09-03 exceeds the 7-day KR gap
        self.assertEqual(entity(kr["2026-09-10"], "KOSPI.SECTOR.18")["state"], "STRONG_HELD")
        self.assertTrue(all(p["observation"]["status"] == "UNKNOWN" for p in self.packets["CRYPTO"] if p["as_of_date"] <= "2026-09-13"))

    def test_append_only_writer_refuses_conflicting_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packets = copy.deepcopy(self.packets["US"][:2])
            RC.write_market("US", packets, root)
            self.assertEqual(RC.verify_market("US", packets, root), [])
            changed = copy.deepcopy(packets)
            changed[0]["pending_definitions"] = []
            changed[0]["payload_sha256"] = RC.payload_sha256({k: v for k, v in changed[0].items() if k != "payload_sha256"})
            with self.assertRaisesRegex(RC.RotationConfirmationError, "APPEND_ONLY_EVIDENCE_CONFLICT"):
                RC.write_market("US", changed, root)

    def test_portal_projection_lists_states_with_days_and_badge(self):
        projection = RC.build_portal_projection(ROOT, POLICY)
        self.assertEqual((projection["schema_version"], projection["chapter"]), ("rotation_confirmation_portal_ch02/1", "02"))
        by_market = {m["market"]: m for m in projection["markets"]}
        self.assertEqual(set(by_market), set(RC.MARKETS))
        for market in RC.MARKETS:
            entry = by_market[market]
            self.assertEqual(entry["evidence_badge_ko"], POLICY["markets"][market]["evidence_badge_ko"])
            for row in entry["strong"] + entry["released"] + entry["emerging_watch"]:
                self.assertGreaterEqual(row["observations_in_state"], 1)
                self.assertIsNotNone(row["state_since_date"])
        text = RC.canonical_json(projection)
        for forbidden in ("quantity", "account", "order_id", "notional"):
            self.assertNotIn(forbidden, text)
        committed = ROOT / RC.PORTAL_RELATIVE_PATH
        self.assertTrue(committed.exists())
        RC.validate_packet(json.loads(RC.latest_path(ROOT, "US").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
