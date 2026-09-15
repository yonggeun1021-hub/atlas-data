#!/usr/bin/env python3
"""PAPER execution core v1: position episodes, re-entry, status vocabulary, checklist, scorecard contract."""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portfolio import paper_execution_core as CORE  # noqa: E402
from portfolio import paper_execution_status as STATUS  # noqa: E402
from portfolio import paper_position_episode as EP  # noqa: E402
from validation import paper_mechanical_checklist as CHECK  # noqa: E402
from validation import paper_scorecard_contract as SCORE  # noqa: E402

F = Fraction
SHA = "a" * 64
AT = "2026-10-20T08:00:00Z"


def record(name: str) -> dict:
    return json.loads((ROOT / "evidence/authority" / name).read_text(encoding="utf-8"))


def fill(fid, side, at, qty, price, *, packet="PKT-1", reason=None, episode="SE-A", fee="0"):
    return {"fill_id": fid, "market": "CRYPTO", "instrument": "KRW-SOL", "side": side, "filled_at_utc": at,
            "quantity": qty, "price": price, "fee": fee, "decision_packet_id": packet, "exit_reason": reason,
            "strength_episode_id": episode if side == "BUY" else None}


class PositionEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.core = CORE.load_core()

    def episodes(self, fills, as_of=AT):
        return EP.build_position_episodes(self.core, market="CRYPTO", instrument="KRW-SOL", fills=fills, as_of_utc=as_of)

    def test_top_ups_and_partial_fills_keep_episode_and_clock(self):
        fills = [fill("b1", "BUY", "2026-09-20T07:31:00Z", "1", "100", fee="1"),
                 fill("b2", "BUY", "2026-09-20T07:36:00Z", "0.5", "101"),
                 fill("b3", "BUY", "2026-09-22T07:40:00Z", "1.5", "110", packet="PKT-3"),
                 fill("s1", "SELL", "2026-10-01T07:40:00Z", "1", "120", packet="PKT-9", reason="ALLOCATION_REDUCTION")]
        (episode,) = self.episodes(fills)
        self.assertEqual((episode["status"], episode["top_up_count"]), ("OPEN", 2))
        self.assertEqual(episode["holding_clock_start_utc"], "2026-09-20T07:31:00Z")
        cost = F(100) + 1 + F("50.5") + 165
        average = cost / 3
        self.assertEqual(F(episode["realized_pnl_net_fees"]), 120 - average)
        self.assertEqual(F(episode["average_cost_open"]), average)
        self.assertEqual(episode["rule_refs"][0]["rule_id"], "RULE.EXEC.TOPUP_POSITION_LEVEL.V1")

    def test_close_and_new_episode(self):
        fills = [fill("b1", "BUY", "2026-09-20T07:31:00Z", "2", "100"),
                 fill("s1", "SELL", "2026-09-25T07:31:00Z", "2", "90", reason="RELEASE_FULL_SELL", packet="PKT-5"),
                 fill("b2", "BUY", "2026-10-05T07:31:00Z", "1", "95", packet="PKT-15", episode="SE-B")]
        first, second = self.episodes(fills)
        self.assertEqual((first["status"], first["closing_exit_reason"]), ("CLOSED", "RELEASE_FULL_SELL"))
        self.assertEqual(first["realized_pnl_net_fees"], "-20")
        self.assertNotEqual(first["position_episode_id"], second["position_episode_id"])
        self.assertEqual(second["holding_clock_start_utc"], "2026-10-05T07:31:00Z")
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "SELL_EXCEEDS_POSITION"):
            self.episodes([fills[0], fill("s9", "SELL", "2026-09-21T00:00:00Z", "3", "1", reason="RELEASE_FULL_SELL")])
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "SELL_WITHOUT_OPEN_POSITION"):
            self.episodes([fill("s9", "SELL", "2026-09-21T00:00:00Z", "1", "1", reason="RELEASE_FULL_SELL")])

    def test_strength_episode_id(self):
        a = EP.strength_episode_id("CRYPTO", "L1", "2026-09-18")
        self.assertEqual(a, EP.strength_episode_id("CRYPTO", "L1", "2026-09-18"))
        self.assertNotEqual(a, EP.strength_episode_id("CRYPTO", "L1", "2026-10-08"))


class ReentryTests(unittest.TestCase):
    def setUp(self):
        self.core = CORE.load_core()
        self.buy = fill("b1", "BUY", "2026-09-20T07:31:00Z", "1", "100", episode="SE-A")

    def decide(self, fills, packet="PKT-30", episode=None):
        return EP.reentry_decision(self.core, market="CRYPTO", instrument="KRW-SOL", decision_packet_id=packet,
                                   decision_at_utc=AT, current_strength_episode=episode, fills=fills)

    def test_r1_same_packet_blocked_even_after_reduction(self):
        fills = [self.buy, fill("s1", "SELL", "2026-10-20T07:40:00Z", "1", "90", packet="PKT-30", reason="ALLOCATION_REDUCTION")]
        result = self.decide(fills)
        self.assertEqual((result["verdict"], result["reason"]), ("DENIED", "R1_SOLD_IN_SAME_DECISION_PACKET"))
        self.assertEqual(result["rule_refs"][0]["role"], "BLOCKED_BY")
        self.assertEqual(self.decide(fills, packet="PKT-31")["verdict"], "ALLOWED")

    def test_release_and_time_stop_need_new_confirmation_after_exit(self):
        for reason in ("RELEASE_FULL_SELL", "CRYPTO_TIME_STOP_21D"):
            fills = [self.buy, fill("s1", "SELL", "2026-10-11T07:40:00Z", "1", "90", packet="PKT-21", reason=reason)]
            self.assertEqual(self.decide(fills)["reason"], "NO_CURRENT_STRENGTH_EPISODE")
            same = {"strength_episode_id": "SE-A", "confirmation_available_at_utc": "2026-09-18T07:15:00Z"}
            self.assertEqual(self.decide(fills, episode=same)["verdict"], "DENIED")
            before = {"strength_episode_id": "SE-B", "confirmation_available_at_utc": "2026-10-11T07:00:00Z"}
            self.assertEqual(self.decide(fills, episode=before)["verdict"], "DENIED")
            after = {"strength_episode_id": "SE-B", "confirmation_available_at_utc": "2026-10-15T07:15:00Z"}
            self.assertEqual(self.decide(fills, episode=after)["verdict"], "ALLOWED", reason)

    def test_unregistered_exit_reason_is_unknown_and_open_position_is_top_up(self):
        fills = [self.buy, fill("s1", "SELL", "2026-10-11T07:40:00Z", "1", "90", packet="PKT-21", reason="MANUAL")]
        self.assertEqual(self.decide(fills)["verdict"], "UNKNOWN")
        self.assertEqual(self.decide([self.buy])["reason"], "OPEN_POSITION_TOP_UP_NOT_REENTRY")


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.core = CORE.load_core()
        self.d4 = record("paper_data_failure_risk_reduction_priority_c_user_ratification_20260915.json")

    def test_display_strings_trace_to_records(self):
        principles = " ".join(self.d4["decision"]["principles_all_options"])
        self.assertIn(STATUS.display_ko(self.core, "RISK_EXECUTION_UNCERTAIN"), principles)
        self.assertIn(STATUS.display_ko(self.core, "MARKET_EXECUTION_BLOCKED"), principles)
        gap = record("rotation_interpretation_observation_gap_user_ratification_20260915.json")
        self.assertIn(STATUS.display_ko(self.core, "JUDGMENT_GAP"), json.dumps(gap, ensure_ascii=False))
        d1 = record("paper_execution_contract_d1_d3_d5_d11_user_ratification_20260915.json")
        self.assertIn(STATUS.display_ko(self.core, "MONITORING_GAP"), json.dumps(d1, ensure_ascii=False))

    def test_classification_option_c(self):
        classify = lambda kind, price, block=None, market="CRYPTO": STATUS.classify_required_exit(
            self.core, exit_kind=kind, price_status=price, market_block=block, market=market)
        self.assertEqual(classify("RELEASE_FULL_SELL", "FRESH")["action"], "EXECUTE_AT_FIRST_ALLOWED_FILL")
        self.assertEqual(classify("RELEASE_FULL_SELL", "STALE")["action"], "HOLD_WHILE_STALE")
        self.assertEqual(classify("CRYPTO_TIME_STOP_21D", "MISSING")["status_code"], None)
        stress = classify("STRESS", "STALE")
        self.assertEqual((stress["status_code"], stress["action"]),
                         ("RISK_EXECUTION_UNCERTAIN", "EXECUTE_AT_FIRST_FRESH_PRICE_WITHOUT_WAITING_FOR_SLOT"))
        self.assertEqual(classify("DRAWDOWN_OVERRIDE", "STALE")["status_code"], "RISK_EXECUTION_UNCERTAIN")
        self.assertEqual(classify("STRESS", "FRESH", "LIMIT_DOWN_LOCK", "KR")["status_code"], "MARKET_EXECUTION_BLOCKED")
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "MARKET_BLOCK_CONDITION_INVALID"):
            classify("STRESS", "FRESH", "LIMIT_DOWN_LOCK", "CRYPTO")

    def test_status_row_monitoring_gap_and_delay_loss(self):
        row = STATUS.status_row(self.core, code="RISK_EXECUTION_UNCERTAIN", market="CRYPTO", instrument="KRW-SOL",
                                reason="PRICE_STALE", started_at_utc="2026-10-20T07:00:00Z", as_of_utc="2026-10-20T07:45:30Z")
        self.assertEqual((row["elapsed_seconds"], row["status_ko"]), (2730, "위험 평가·집행 불확실"))
        times = ["2026-10-20T00:06:00Z", "2026-10-20T00:36:00Z", "2026-10-20T01:36:00Z", "2026-10-20T02:37:00Z"]
        gaps = STATUS.monitoring_gap(self.core, market="CRYPTO", instrument="KRW-SOL", observation_times_utc=times,
                                     monitoring_interval_seconds=1800, as_of_utc="2026-10-20T03:40:00Z")
        self.assertEqual([(g["started_at_utc"], g["resolved_at_utc"]) for g in gaps],
                         [("2026-10-20T01:36:00Z", "2026-10-20T02:37:00Z"), ("2026-10-20T02:37:00Z", None)])
        sell = STATUS.delay_loss_row(self.core, market="CRYPTO", instrument="KRW-SOL", side="SELL", quantity="2",
                                     decision_at_utc="2026-10-20T07:00:00Z", decision_reference_price="100",
                                     decision_reference_kind="LAST_VERIFIED_BEFORE_DECISION",
                                     fill_at_utc="2026-10-20T07:36:00Z", fill_price="93", status_code="RISK_EXECUTION_UNCERTAIN")
        self.assertEqual(sell["delay_loss"], "14")
        buy = STATUS.delay_loss_row(self.core, market="KR", instrument="005930", side="BUY", quantity="10",
                                    decision_at_utc="2026-10-20T00:00:00Z", decision_reference_price=None,
                                    decision_reference_kind="NONE", fill_at_utc="2026-10-20T00:30:00Z", fill_price="70000")
        self.assertIsNone(buy["delay_loss"])
        self.assertEqual(buy["delay_loss_reason"], "NO_DECISION_TIME_REFERENCE_PRICE_NOT_INTERPOLATED")
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "FILL_NOT_AFTER_DECISION"):
            STATUS.delay_loss_row(self.core, market="KR", instrument="005930", side="BUY", quantity="1",
                                  decision_at_utc="2026-10-20T00:00:00Z", decision_reference_price="1",
                                  decision_reference_kind="VERIFIED_AT_DECISION", fill_at_utc="2026-10-20T00:00:00Z",
                                  fill_price="1")


def evidence(path_id, kind, passed=True):
    return {"path_id": path_id, "kind": kind, "passed": passed, "evidence_sha256": SHA}


SAFE = {"observed_sessions": 30, "scheduled_runs_expected": 100, "scheduled_runs_executed": 95,
        "duplicate_ledger_mutations": 0, "reconciliation_mismatches": 0, "silent_errors": 0,
        "restart_recovery_attempts": 1, "restart_recovery_successes": 1, "stale_data_orders": 0}


class ChecklistTests(unittest.TestCase):
    def setUp(self):
        self.core = CORE.load_core()
        self.paths = self.core.interpretations["mechanical_checklist"]["paths"]

    def complete(self, fixture_for_nf=False):
        items = []
        for pid, spec in self.paths.items():
            kinds = {"N": ["N"], "F": ["F"], "N+F": ["N", "F"], "F+N": ["F", "N"], "N/F": ["F" if fixture_for_nf else "N"]}
            items += [evidence(pid, k) for k in kinds[spec["evidence"]]]
        return items

    def evaluate(self, items, window_closed=False, safety=SAFE, cohort="INVESTMENT_PAPER"):
        return CHECK.evaluate_checklist(self.core, market="CRYPTO", as_of_utc=AT, cohort=cohort, evidence=items,
                                        observation_window_closed=window_closed, safety_metrics=dict(safety))

    def test_validated_only_with_all_paths_and_safety(self):
        result = self.evaluate(self.complete())
        self.assertEqual(result["verdict"], "MECHANICAL_VALIDATED")
        self.assertEqual(result["display_ko"], "기계 검증 완료 / 투자 성과 표본 부족")
        self.assertEqual(sorted(self.paths), sorted(f"P{i}" for i in range(1, 19)))

    def test_fixture_substitution_rules(self):
        self.assertEqual(self.evaluate(self.complete(fixture_for_nf=True))["verdict"], "IN_PROGRESS")
        closed = self.evaluate(self.complete(fixture_for_nf=True), window_closed=True)
        self.assertEqual(closed["verdict"], "MECHANICAL_VALIDATED")
        self.assertIn("P2", closed["natural_not_observed_paths"])
        no_natural_p1 = [e for e in self.complete() if e["path_id"] != "P1"] + [evidence("P1", "F")]
        result = self.evaluate(no_natural_p1, window_closed=True)
        self.assertEqual((result["verdict"], result["paths"]["P1"]["status"]), ("IN_PROGRESS", "PENDING_NATURAL"))
        only_n_p7 = [e for e in self.complete() if not (e["path_id"] == "P7" and e["kind"] == "F")]
        self.assertEqual(self.evaluate(only_n_p7)["paths"]["P7"]["status"], "PENDING_BOTH_KINDS")

    def test_operational_safety_overrides(self):
        self.assertEqual(self.evaluate(self.complete(), safety={**SAFE, "observed_sessions": 29})["verdict"], "IN_PROGRESS")
        failed = self.evaluate(self.complete(), safety={**SAFE, "duplicate_ledger_mutations": 1})
        self.assertEqual((failed["verdict"], failed["operational_safety"]["breaches"]), ("FAILED", ["duplicate_ledger_mutations"]))
        low = self.evaluate(self.complete(), safety={**SAFE, "scheduled_runs_executed": 94})
        self.assertEqual(low["verdict"], "FAILED")
        bad_path = self.complete() + [evidence("P9", "F", passed=False)]
        self.assertEqual(self.evaluate(bad_path)["verdict"], "FAILED")
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "PERFORMANCE_SAMPLE_STATE_INVALID"):
            CHECK.evaluate_checklist(self.core, market="CRYPTO", as_of_utc=AT, cohort="INVESTMENT_PAPER",
                                     evidence=self.complete(), observation_window_closed=False,
                                     safety_metrics=dict(SAFE), performance_sample_state="성과 좋음")
        canary = self.evaluate(self.complete(), cohort="SYSTEM_CANARY")
        self.assertFalse(canary["performance_counted"])


def row(i, episode, date, metric, recalculated=False, rule="RULE.ROTATION.CRYPTO.V1", nav="VERIFIED"):
    return {"position_episode_id": f"PE-{i}", "strength_episode_id": episode, "market": "CRYPTO", "entry_date": date,
            "rule_id": rule, "metric_net_of_cost": metric, "uses_recalculated_rotation_days": recalculated,
            "nav_verification": nav}


class ScorecardTests(unittest.TestCase):
    def setUp(self):
        self.core = CORE.load_core()

    def test_checkpoints_and_labels(self):
        self.assertEqual(SCORE.checkpoints_up_to(self.core, 250), [30, 80, 160, 240])
        rows = [row(0, "SE-1", "2026-10-01", "1"), row(1, "SE-1", "2026-10-02", "-1", recalculated=True),
                row(2, "SE-2", "2026-10-02", "0.5", nav="UNVERIFIED")]
        summary = SCORE.scorecard_summary(self.core, rule_id="RULE.ROTATION.CRYPTO.V1", rows=rows, as_of_utc=AT,
                                          scheduled_check=True, last_judged_checkpoint=None)
        self.assertEqual(summary["clusters"], {"STRENGTH_EPISODE": 2, "ENTRY_DATE": 2})
        self.assertEqual((summary["n_eff"], summary["judgement_allowed"]), (2, False))
        self.assertEqual(summary["sample_label_ko"], "표본 부족 2/30")
        self.assertEqual(summary["cumulative_label_ko"], "참고, 판정 아님")
        recalc = self.core.context.rules["RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1"]["key_parameters"]["mark"]["value"]
        self.assertEqual(summary["recalculated_mark_ko"], recalc)
        self.assertEqual((summary["nav_unverified_rows"], summary["nav_status_ko"]), (1, "NAV 일부 미검증"))
        self.assertEqual(summary["badges"], "NOT_DEFINED:SCORECARD_BADGE_THRESHOLDS")

    def test_wider_cluster_and_judgement_only_at_new_checkpoint(self):
        rows = [row(i, f"SE-{i // 2}", f"2026-{1 + i % 12:02d}-01", str((-1) ** i * (i % 5))) for i in range(64)]
        summary = SCORE.scorecard_summary(self.core, rule_id="RULE.ROTATION.CRYPTO.V1", rows=rows, as_of_utc=AT,
                                          scheduled_check=False, last_judged_checkpoint=None)
        se = {k: F(v) for k, v in summary["clustered_se_squared"].items()}
        wider = "STRENGTH_EPISODE" if se["STRENGTH_EPISODE"] >= se["ENTRY_DATE"] else "ENTRY_DATE"
        self.assertEqual(summary["cluster_used"], wider)
        self.assertEqual(summary["n_eff"], summary["clusters"][wider])
        big = [row(i, f"SE-{i}", "2026-10-01", str(i % 3)) for i in range(35)]
        big_by_date = [dict(r, entry_date=f"2026-10-{1 + i % 28:02d}") for i, r in enumerate(big)]
        at_check = SCORE.scorecard_summary(self.core, rule_id="RULE.ROTATION.CRYPTO.V1", rows=big_by_date, as_of_utc=AT,
                                           scheduled_check=True, last_judged_checkpoint=None)
        self.assertEqual((at_check["cluster_used"], at_check["n_eff"], at_check["new_checkpoint"]), ("STRENGTH_EPISODE", 35, 30))
        self.assertTrue(at_check["judgement_allowed"])
        again = SCORE.scorecard_summary(self.core, rule_id="RULE.ROTATION.CRYPTO.V1", rows=big_by_date, as_of_utc=AT,
                                        scheduled_check=True, last_judged_checkpoint=30)
        self.assertFalse(again["judgement_allowed"])
        off = SCORE.scorecard_summary(self.core, rule_id="RULE.ROTATION.CRYPTO.V1", rows=big_by_date, as_of_utc=AT,
                                      scheduled_check=False, last_judged_checkpoint=None)
        self.assertFalse(off["judgement_allowed"])

    def test_cooling_off_gate(self):
        gate = lambda **kw: SCORE.readjustment_proposal_gate(self.core, **{
            "rule_id": "RULE.ROTATION.CRYPTO.V1", "market": "CRYPTO", "reason": "PERFORMANCE",
            "kr_us_trading_days_since_effective": None, "post_effective_sample_n": 10, **kw})
        rotation = self.core.context.rules["RULE.ROTATION.CRYPTO.V1"]
        minimum = rotation["minimum_sample"]["value"]
        start = rotation["effective_from"]["utc"][:10]
        self.assertEqual(start, "2026-09-14")
        self.assertEqual(gate(as_of_date="2026-10-13", post_effective_sample_n=minimum)["status"], "BLOCKED:COOLING_OFF")
        self.assertTrue(gate(as_of_date="2026-10-14", post_effective_sample_n=minimum)["allowed"])
        self.assertFalse(gate(as_of_date="2026-10-14", post_effective_sample_n=minimum - 1)["allowed"])
        self.assertEqual(gate(as_of_date="2026-09-20", reason="BUG_FIX")["status"], "NOT_BLOCKED_REASON")
        kr = gate(rule_id="RULE.ROTATION.KR.V1T", market="KR", as_of_date="2026-12-01", kr_us_trading_days_since_effective=25)
        self.assertEqual((kr["status"], kr["allowed"]), ("MIN_SAMPLE_NOT_DEFINED", False))
        self.assertIn("RULE_MINIMUM_SAMPLE", self.core.not_defined_ids())
        d11 = json.dumps(record("paper_execution_contract_d1_d3_d5_d11_user_ratification_20260915.json"), ensure_ascii=False)
        self.assertNotRegex(d11, r"최소 표본[^,.]*\d")  # the ratified D11 text gives no numeric fallback
        self.assertTrue(kr["calendar_met"])
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "TRADING_DAYS_INPUT_REQUIRED"):
            gate(rule_id="RULE.ROTATION.KR.V1T", market="KR", as_of_date="2026-12-01")


if __name__ == "__main__":
    unittest.main()
