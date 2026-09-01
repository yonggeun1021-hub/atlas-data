#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "portfolio" / "cross_market_flow_transition_ledger.py"
SPEC = importlib.util.spec_from_file_location(
    "cross_market_flow_transition_ledger_tested", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def render(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


class CrossMarketFlowTransitionLedgerTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        (self.root / "config").mkdir()
        (self.root / "data").mkdir()
        for name in (
            "cross_market_flow_transition_ledger_contract.json",
            "capital_flow_posture_reference_policy_v1.json",
            "paper_regime_reference_policy_v1.json",
        ):
            shutil.copy2(ROOT / "config" / name, self.root / "config" / name)
        for name in (
            "latest_free_market_data.json",
            "latest_korea_market_signals.json",
            "latest_crypto_regime_refresh_status.json",
        ):
            shutil.copy2(ROOT / "data" / name, self.root / "data" / name)
        self.contract_path = (
            self.root / "config" / "cross_market_flow_transition_ledger_contract.json"
        )

    def tearDown(self):
        self._temp.cleanup()

    def _json(self, name: str) -> dict:
        return json.loads((self.root / "data" / name).read_text(encoding="utf-8"))

    def _write(self, name: str, value: dict) -> None:
        (self.root / "data" / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _set_market_inputs(
        self,
        date: str,
        *,
        us_positive: bool = True,
        kr_positive: bool = False,
        kr_date: str | None = None,
    ) -> None:
        us = self._json("latest_free_market_data.json")
        us["observed_at_utc"] = f"{date}T21:39:09Z"
        reference = us["us_market_reference"]
        reference["as_of_session_date"] = date
        direction = "1" if us_positive else "-1"
        for row in reference["trend_etfs"]:
            row["returns"]["20_session_pct"] = direction
        reference["proxy_axes"]["BREADTH"]["measurement"]["advance_fraction"] = (
            "0.80" if us_positive else "0.20"
        )
        for row in reference["proxy_axes"]["LEADERSHIP"]["measurement"][
            "ordered_groups"
        ]:
            row["return_pct"] = direction
        us["fred"]["value"] = "10" if us_positive else "30"
        for row in us["fred_liquidity"]["series"]:
            row["change"] = direction
        self._write("latest_free_market_data.json", us)

        kr = self._json("latest_korea_market_signals.json")
        kr_effective_date = kr_date or date
        kr["as_of_date"] = kr_effective_date
        kr["generated_at"] = f"{date}T20:00:00Z"
        kr_direction = "1" if kr_positive else "-1"
        benchmarks = kr["axes"]["TREND"]["measurement"]["benchmarks"]
        benchmarks["KOSPI"]["one_session_return_pct"] = kr_direction
        benchmarks["KOSDAQ"]["one_session_return_pct"] = kr_direction
        kr["axes"]["BREADTH"]["measurement"]["combined"]["advance_fraction"] = (
            "0.80" if kr_positive else "0.20"
        )
        kr["axes"]["RISK_VOL"]["measurement"][
            "combined_mean_absolute_stock_move_pct"
        ] = "1" if kr_positive else "3"
        kr["axes"]["LIQUIDITY"]["measurement"]["combined"][
            "trading_value_change_pct"
        ] = "10" if kr_positive else "-10"
        for row in kr["axes"]["LEADERSHIP"]["measurement"]["observations"]:
            row["sector_return_pct"] = kr_direction
        self._write("latest_korea_market_signals.json", kr)

    def _build_source(
        self,
        name: str,
        date: str,
        *,
        reverse: bool = False,
        unknown: bool = False,
    ) -> tuple[Path, dict]:
        self._set_market_inputs(
            date,
            us_positive=not reverse,
            kr_positive=reverse,
            kr_date="2026-08-31" if unknown else None,
        )
        paper = MODULE.PRODUCER.PAPER_REGIME.build_reference(self.root)
        MODULE.PRODUCER.PAPER_REGIME.write_packet(paper, self.root)
        packet = MODULE.PRODUCER.build_reference(self.root)
        path = self.root / "data" / name
        path.write_bytes(render(packet))
        MODULE.PRODUCER.validate_reference(packet, self.root)
        return path, packet

    def _apply(
        self,
        path: Path,
        mode: str,
        previous: dict | None = None,
    ) -> dict:
        return MODULE.apply_source(
            path,
            mode,
            previous,
            root=self.root,
            contract_path=self.contract_path,
        )

    def test_contract_preserves_unknown_and_closes_all_adjacent_authority(self):
        contract = MODULE.load_contract(self.contract_path)
        self.assertEqual(
            contract["persistence_count_policy"],
            {"NATURAL": True, "MANUAL": False, "RECOVERY": False, "REPLAY": False},
        )
        self.assertEqual(contract["confirmation_policy"], "UNRATIFIED_CONFIRMED_AT_NULL")
        for key in (
            "confirmation_authorized",
            "numeric_threshold_authorized",
            "market_allocation_authorized",
            "capital_authorized",
            "stage_authorized",
            "buy_authorized",
            "action_authorized",
            "order_authorized",
            "production_authorized",
            "trading_authorized",
        ):
            self.assertFalse(contract["authority"][key], key)

    def test_exact_p2_output_is_consumed_with_unknown_null_and_lineage_preserved(self):
        path, source = self._build_source(
            "source-unknown.json", "2026-09-02", unknown=True
        )
        ledger = self._apply(path, "MANUAL")
        entry = ledger["entries"][0]
        flow = entry["current_state"]["cross_market_flow"]
        self.assertEqual(flow["actual_money_flow"], "UNKNOWN")
        self.assertEqual(flow["comparison_status"], "UNKNOWN")
        self.assertIsNone(flow["comparison_as_of_date"])
        self.assertIsNone(flow["relative_strength_leader"])
        self.assertIsNone(flow["relative_strength_laggard"])
        self.assertIsNone(entry["previous_state"])
        self.assertIsNone(entry["confirmed_at"])
        self.assertEqual(entry["lineage"]["input_packet"], source)
        self.assertEqual(
            entry["lineage"]["input_payload_sha256"], source["payload_sha256"]
        )
        self.assertEqual(
            entry["lineage"]["producer_sources"], source["sources"]
        )

    def test_same_source_duplicate_is_byte_identical_noop_even_with_new_label(self):
        path, _ = self._build_source("source-1.json", "2026-09-01")
        first = self._apply(path, "NATURAL")
        duplicate = self._apply(path, "REPLAY", json.loads(render(first)))
        self.assertEqual(render(duplicate), render(first))
        self.assertEqual(duplicate["ledger_revision"], 1)
        self.assertEqual(duplicate["observation_mode_counts"]["REPLAY"], 0)

    def test_same_day_different_exact_packet_is_revision_drift(self):
        first_path, _ = self._build_source("source-1.json", "2026-09-01")
        first = self._apply(first_path, "NATURAL")
        drift_path, _ = self._build_source(
            "source-drift.json", "2026-09-01", reverse=True
        )
        with self.assertRaisesRegex(
            MODULE.CrossMarketFlowTransitionLedgerError,
            "SOURCE_REVISION_DRIFT_SAME_OBSERVATION_DATE",
        ):
            self._apply(drift_path, "NATURAL", first)

    def test_restart_rebuild_is_deterministic_and_byte_identical(self):
        first_path, _ = self._build_source("source-1.json", "2026-09-01")
        first = self._apply(first_path, "NATURAL")
        second_path, _ = self._build_source("source-2.json", "2026-09-02")
        direct = self._apply(second_path, "NATURAL", first)
        restarted = self._apply(
            second_path, "NATURAL", json.loads(render(first))
        )
        self.assertEqual(render(direct), render(restarted))
        self.assertEqual(
            restarted["entries"][1]["previous_entry_sha256"],
            restarted["entries"][0]["entry_sha256"],
        )

    def test_non_forward_unseen_source_is_stale_and_fails_closed(self):
        first_path, _ = self._build_source("source-1.json", "2026-09-02")
        first = self._apply(first_path, "NATURAL")
        stale_path, _ = self._build_source(
            "source-stale.json", "2026-09-01", reverse=True
        )
        with self.assertRaisesRegex(
            MODULE.CrossMarketFlowTransitionLedgerError,
            "SOURCE_STALE_NON_FORWARD_OBSERVATION",
        ):
            self._apply(stale_path, "NATURAL", first)

    def test_content_addressed_evidence_and_latest_are_identical_append_only(self):
        path, _ = self._build_source("source-1.json", "2026-09-01")
        ledger = self._apply(path, "MANUAL")
        evidence, latest = MODULE.write_outputs(ledger, self.root)
        self.assertEqual(evidence.read_bytes(), latest.read_bytes())
        self.assertEqual(json.loads(latest.read_text()), ledger)
        before = evidence.read_bytes()
        evidence_again, latest_again = MODULE.write_outputs(ledger, self.root)
        self.assertEqual(evidence_again, evidence)
        self.assertEqual(latest_again, latest)
        self.assertEqual(evidence.read_bytes(), before)

    def test_resigned_source_tamper_and_resigned_ledger_tamper_fail_closed(self):
        path, source = self._build_source("source-1.json", "2026-09-01")
        tampered_source = copy.deepcopy(source)
        flow = tampered_source["cross_market_flow"]
        flow["relative_strength_leader"] = "KR"
        flow["relative_strength_laggard"] = "US"
        tampered_source.pop("payload_sha256")
        tampered_source["payload_sha256"] = MODULE.payload_sha256(tampered_source)
        tampered_path = self.root / "data" / "source-tampered.json"
        tampered_path.write_bytes(render(tampered_source))
        with self.assertRaisesRegex(
            MODULE.CrossMarketFlowTransitionLedgerError,
            "SOURCE_SEMANTIC_REVALIDATION_FAILED",
        ):
            self._apply(tampered_path, "MANUAL")

        ledger = self._apply(path, "NATURAL")
        tampered_ledger = copy.deepcopy(ledger)
        tampered_ledger["entries"][0]["confirmed_at"] = "2026-09-01T23:00:00Z"
        entry = tampered_ledger["entries"][0]
        entry.pop("entry_sha256")
        entry["entry_sha256"] = MODULE.payload_sha256(entry)
        tampered_ledger.pop("payload_sha256")
        tampered_ledger["payload_sha256"] = MODULE.payload_sha256(tampered_ledger)
        with self.assertRaisesRegex(
            MODULE.CrossMarketFlowTransitionLedgerError,
            "LEDGER_ENTRY_CHAIN_INVALID",
        ):
            MODULE.validate_ledger(tampered_ledger, MODULE.load_contract(self.contract_path))

    def test_exact_leader_laggard_swap_is_reversal(self):
        first_path, _ = self._build_source("source-1.json", "2026-09-01")
        ledger = self._apply(first_path, "NATURAL")
        second_path, _ = self._build_source(
            "source-2.json", "2026-09-02", reverse=True
        )
        ledger = self._apply(second_path, "NATURAL", ledger)
        second = ledger["entries"][1]
        self.assertEqual(second["transition"]["type"], "REVERSAL")
        self.assertTrue(second["transition"]["reversal"]["detected"])
        self.assertEqual(
            second["transition"]["reversal"],
            {
                "detected": True,
                "previous_leader": "US",
                "previous_laggard": "KR",
                "current_leader": "KR",
                "current_laggard": "US",
            },
        )

    def test_unknown_transition_is_invalidation_then_structural_recovery(self):
        first_path, _ = self._build_source("source-1.json", "2026-09-01")
        ledger = self._apply(first_path, "NATURAL")
        unknown_path, _ = self._build_source(
            "source-2.json", "2026-09-02", unknown=True
        )
        ledger = self._apply(unknown_path, "NATURAL", ledger)
        self.assertEqual(ledger["entries"][1]["transition"]["type"], "INVALIDATION")
        self.assertTrue(
            ledger["entries"][1]["transition"]["invalidation"]["detected"]
        )
        recovery_path, _ = self._build_source("source-3.json", "2026-09-03")
        ledger = self._apply(recovery_path, "NATURAL", ledger)
        self.assertEqual(ledger["entries"][2]["transition"]["type"], "RECOVERY")
        self.assertFalse(
            ledger["entries"][2]["transition"]["reversal"]["detected"]
        )

    def test_only_natural_counts_and_no_count_confirms_state(self):
        ledger = None
        modes = ["NATURAL", "MANUAL", "RECOVERY", "REPLAY"]
        for offset, mode in enumerate(modes, 1):
            path, _ = self._build_source(
                f"source-{offset}.json", f"2026-09-0{offset}"
            )
            ledger = self._apply(path, mode, ledger)
        assert ledger is not None
        self.assertEqual(
            ledger["observation_mode_counts"],
            {"NATURAL": 1, "MANUAL": 1, "RECOVERY": 1, "REPLAY": 1},
        )
        self.assertEqual(ledger["counted_natural_observations"], 1)
        self.assertEqual(
            [row["counts_toward_persistence"] for row in ledger["entries"]],
            [True, False, False, False],
        )
        last = ledger["entries"][-1]
        self.assertEqual(last["persistence"]["state_observation_count_total"], 4)
        self.assertEqual(last["persistence"]["current_streak_natural_count"], 1)
        self.assertEqual(
            last["persistence"]["confirmation_status"],
            "NOT_COMPUTABLE_POLICY_UNRATIFIED",
        )
        self.assertIsNone(last["confirmed_at"])


if __name__ == "__main__":
    unittest.main()
