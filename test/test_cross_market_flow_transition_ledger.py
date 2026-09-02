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

V1_CONTRACT_VERSION = "cross_market_flow_transition_ledger/1"


def render(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


class CrossMarketFlowTransitionLedgerTest(unittest.TestCase):
    """/2 continues the frozen /1 chain and orders only by source_generated_date_kst.

    Fixture dates sit after 2026-09-02 so the US observation timestamp, not the
    copied crypto refresh timestamp, drives the producer generated_at.
    """

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
        self.latest_path = (
            self.root / "data" / "latest_cross_market_flow_transition_ledger.json"
        )
        self.seed_packet = self._seed_predecessor("2026-09-10")

    def tearDown(self):
        self._temp.cleanup()

    # ---- fixture plumbing -------------------------------------------------

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
        session_date: str | None = None,
        observed_time: str = "21:39:09",
    ) -> None:
        """session_date pins the market session while `date` advances the clock."""
        session = session_date or date
        us = self._json("latest_free_market_data.json")
        us["observed_at_utc"] = f"{date}T{observed_time}Z"
        reference = us["us_market_reference"]
        reference["as_of_session_date"] = session
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
        kr["as_of_date"] = kr_date or session
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
        session_date: str | None = None,
        observed_time: str = "21:39:09",
    ) -> tuple[Path, dict]:
        self._set_market_inputs(
            date,
            us_positive=not reverse,
            kr_positive=reverse,
            kr_date="2026-08-31" if unknown else None,
            session_date=session_date,
            observed_time=observed_time,
        )
        paper = MODULE.PRODUCER.PAPER_REGIME.build_reference(self.root)
        MODULE.PRODUCER.PAPER_REGIME.write_packet(paper, self.root)
        packet = MODULE.PRODUCER.build_reference(self.root)
        path = self.root / "data" / name
        path.write_bytes(render(packet))
        MODULE.PRODUCER.validate_reference(packet, self.root)
        return path, packet

    def _contract(self) -> dict:
        return json.loads(self.contract_path.read_text(encoding="utf-8"))

    def _write_contract(self, value: dict) -> None:
        self.contract_path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _seed_predecessor(self, date: str) -> dict:
        """Write a synthetic but hash-valid /1 chain and pin it in the contract.

        Production fresh chains are forbidden, so every test starts from a
        predecessor exactly as the repository does.
        """
        _, packet = self._build_source("seed-source.json", date)
        state = MODULE._current_state(packet)
        semantic = MODULE.payload_sha256(MODULE._semantic_state(state))
        flow = state["cross_market_flow"]
        v1_key = flow["comparison_as_of_date"] or packet["generated_at"][:10]
        entry = {
            "ledger_revision": 1,
            "effective_observation_date": v1_key,
            "observed_at": packet["generated_at"],
            "observation_mode": "NATURAL",
            "counts_toward_persistence": True,
            "previous_state": None,
            "current_state": copy.deepcopy(state),
            "current_semantic_state_sha256": semantic,
            "first_seen": packet["generated_at"],
            "confirmed_at": None,
            "persistence": {
                "state_observation_count_total": 1,
                "state_natural_observation_count_total": 1,
                "current_streak_observation_count": 1,
                "current_streak_natural_count": 1,
                "confirmation_threshold": None,
                "confirmation_status": "NOT_COMPUTABLE_POLICY_UNRATIFIED",
            },
            "transition": {
                "type": "INITIAL",
                "reversal": {
                    "detected": False,
                    "previous_leader": None,
                    "previous_laggard": None,
                    "current_leader": flow["relative_strength_leader"],
                    "current_laggard": flow["relative_strength_laggard"],
                },
                "invalidation": {"detected": False, "reason": None},
            },
            "lineage": {
                "input_path": "data/seed-source.json",
                "input_file_sha256": MODULE.file_sha256(
                    self.root / "data" / "seed-source.json"
                ),
                "input_payload_sha256": packet["payload_sha256"],
                "input_generation_id": packet["generation_id"],
                "input_schema_version": packet["schema_version"],
                "input_contract_version": packet["contract_version"],
                "producer_policy": copy.deepcopy(packet["policy"]),
                "producer_sources": copy.deepcopy(packet["sources"]),
                "input_packet": copy.deepcopy(packet),
            },
            "previous_entry_sha256": None,
        }
        entry["entry_sha256"] = MODULE.payload_sha256(entry)
        ledger = {
            "schema_version": "cross_market_flow_transition_ledger_packet/1",
            "contract_version": V1_CONTRACT_VERSION,
            "status": "HISTORY_OBSERVED",
            "ledger_id": "CROSS_MARKET_FLOW",
            "ledger_revision": 1,
            "contract": {
                "path": "config/cross_market_flow_transition_ledger_contract.json",
                "sha256": MODULE.file_sha256(self.contract_path),
                "contract_version": V1_CONTRACT_VERSION,
            },
            "entries": [entry],
            "current_state": copy.deepcopy(state),
            "latest_transition": copy.deepcopy(entry["transition"]),
            "observation_mode_counts": {
                "NATURAL": 1,
                "MANUAL": 0,
                "RECOVERY": 0,
                "REPLAY": 0,
            },
            "counted_natural_observations": 1,
            "authority": copy.deepcopy(MODULE._expected_contract()["authority"]),
            "unresolved_boundaries": list(MODULE.UNRESOLVED_BOUNDARIES),
        }
        ledger["payload_sha256"] = MODULE.payload_sha256(ledger)
        relative = (
            "evidence/portfolio/cross_market_flow_transition_ledger/"
            f"{v1_key}/{ledger['payload_sha256']}/packet.json"
        )
        evidence = self.root / relative
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_bytes(render(ledger))
        self.latest_path.write_bytes(render(ledger))
        contract = self._contract()
        contract["predecessor"] = {
            "contract_version": V1_CONTRACT_VERSION,
            "evidence_path": relative,
            "evidence_file_sha256": MODULE.file_sha256(evidence),
            "payload_sha256": ledger["payload_sha256"],
            "tail_entry_sha256": entry["entry_sha256"],
            "height": 1,
        }
        self._write_contract(contract)
        self.v1_ledger = ledger
        self.v1_entry = entry
        self.v1_evidence = evidence
        return packet

    def _latest(self) -> dict:
        return json.loads(self.latest_path.read_text(encoding="utf-8"))

    def _apply(self, path: Path, mode: str, previous: dict | None = "LATEST") -> dict:
        if previous == "LATEST":
            previous = self._latest()
        return MODULE.apply_observation(
            path,
            mode,
            previous,
            root=self.root,
            contract_path=self.contract_path,
        )

    def _forward(self, name: str, date: str, **kwargs) -> tuple[Path, dict]:
        path, packet = self._build_source(name, date, **kwargs)
        self.assertGreater(
            MODULE.source_generated_date_kst(packet),
            MODULE.source_generated_date_kst(self.seed_packet),
            "fixture must advance the producer generated_at",
        )
        return path, packet

    # ---- contract ---------------------------------------------------------

    def test_contract_pins_axis_scope_and_closes_all_adjacent_authority(self):
        contract = MODULE.load_contract(self.contract_path)
        self.assertEqual(
            contract["contract_version"], "cross_market_flow_transition_ledger/2"
        )
        self.assertEqual(
            contract["ledger_schema_version"],
            "cross_market_flow_transition_ledger_packet/2",
        )
        self.assertEqual(contract["schema_version"], 2)
        self.assertEqual(contract["observation_order_key"], "source_generated_date_kst")
        self.assertEqual(
            contract["observation_count_scope"], "CUMULATIVE_INCLUDING_PREDECESSOR"
        )
        self.assertFalse(contract["production_fresh_chain_allowed"])
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

    def test_repository_contract_matches_the_pinned_production_predecessor(self):
        shipped = json.loads(
            (
                ROOT / "config" / "cross_market_flow_transition_ledger_contract.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(shipped["predecessor"], MODULE.PRODUCTION_PREDECESSOR)
        MODULE.validate_contract(shipped, production=True)
        repointed = copy.deepcopy(shipped)
        repointed["predecessor"]["height"] = 2
        with self.assertRaises(MODULE.CrossMarketFlowTransitionLedgerError) as ctx:
            MODULE.validate_contract(repointed, production=True)
        self.assertIn("CONTRACT_PRODUCTION_PREDECESSOR_MISMATCH", str(ctx.exception))

    # ---- order key --------------------------------------------------------

    def test_source_generated_date_uses_a_fixed_kst_day_boundary(self):
        self.assertEqual(
            MODULE.source_generated_date_kst({"generated_at": "2026-09-01T14:59:59Z"}),
            "2026-09-01",
        )
        self.assertEqual(
            MODULE.source_generated_date_kst({"generated_at": "2026-09-01T15:00:00Z"}),
            "2026-09-02",
        )
        self.assertEqual(
            MODULE.source_generated_date_kst({"generated_at": "2026-09-02T00:00:00+09:00"}),
            "2026-09-02",
        )

    # ---- /1 pointer behaviour --------------------------------------------

    def test_identical_v1_source_is_a_write_free_noop(self):
        before_latest = self.latest_path.read_bytes()
        before_evidence = self.v1_evidence.read_bytes()
        result = self._apply(self.root / "data" / "seed-source.json", "NATURAL")
        self.assertEqual(result["action"], "V1_NOOP_KEEP")
        self.assertEqual(result["ledger"]["contract_version"], V1_CONTRACT_VERSION)
        self.assertEqual(self.latest_path.read_bytes(), before_latest)
        self.assertEqual(self.v1_evidence.read_bytes(), before_evidence)

    def test_same_source_generated_date_with_a_different_packet_is_drift(self):
        path, _ = self._build_source("drift.json", "2026-09-10", reverse=True)
        with self.assertRaises(MODULE.CrossMarketFlowTransitionLedgerError) as ctx:
            self._apply(path, "NATURAL")
        self.assertIn("SOURCE_REVISION_DRIFT_SAME_SOURCE_GENERATED_DATE", str(ctx.exception))

    def test_older_source_generated_date_is_stale(self):
        path, _ = self._build_source("stale.json", "2026-09-09")
        with self.assertRaises(MODULE.CrossMarketFlowTransitionLedgerError) as ctx:
            self._apply(path, "NATURAL")
        self.assertIn("SOURCE_STALE_NON_FORWARD_SOURCE_GENERATED_DATE", str(ctx.exception))

    def test_forward_source_bootstraps_v2_at_revision_two(self):
        path, packet = self._forward("forward.json", "2026-09-11")
        result = self._apply(path, "NATURAL")
        self.assertEqual(result["action"], "V2_BOOTSTRAP")
        ledger = result["ledger"]
        entry = ledger["entries"][0]
        self.assertEqual(ledger["contract_version"], "cross_market_flow_transition_ledger/2")
        self.assertEqual(ledger["ledger_revision"], 2)
        self.assertEqual(entry["ledger_revision"], 2)
        self.assertEqual(entry["previous_state"], self.v1_entry["current_state"])
        self.assertEqual(entry["previous_entry_sha256"], self.v1_entry["entry_sha256"])
        self.assertNotEqual(entry["transition"]["type"], "INITIAL")
        self.assertEqual(
            entry["source_generated_date_kst"],
            MODULE.source_generated_date_kst(packet),
        )
        self.assertIsNone(entry["confirmed_at"])
        self.assertEqual(ledger["predecessor"]["height"], 1)

    # ---- continuity -------------------------------------------------------

    def test_unchanged_state_continues_first_seen_persistence_and_counts(self):
        path, _ = self._forward("same.json", "2026-09-11")
        ledger = self._apply(path, "NATURAL")["ledger"]
        entry = ledger["entries"][0]
        self.assertEqual(entry["transition"]["type"], "UNCHANGED")
        self.assertEqual(
            entry["current_semantic_state_sha256"],
            self.v1_entry["current_semantic_state_sha256"],
        )
        self.assertEqual(entry["first_seen"], self.v1_entry["observed_at"])
        self.assertEqual(
            entry["persistence"],
            {
                "state_observation_count_total": 2,
                "state_natural_observation_count_total": 2,
                "current_streak_observation_count": 2,
                "current_streak_natural_count": 2,
                "confirmation_threshold": None,
                "confirmation_status": "NOT_COMPUTABLE_POLICY_UNRATIFIED",
            },
        )
        self.assertEqual(
            ledger["observation_mode_counts"],
            {"NATURAL": 2, "MANUAL": 0, "RECOVERY": 0, "REPLAY": 0},
        )
        self.assertEqual(ledger["counted_natural_observations"], 2)
        self.assertEqual(
            ledger["observation_count_scope"], "CUMULATIVE_INCLUDING_PREDECESSOR"
        )

    def test_only_natural_observations_raise_the_natural_counters(self):
        path, _ = self._forward("manual.json", "2026-09-11")
        ledger = self._apply(path, "MANUAL")["ledger"]
        entry = ledger["entries"][0]
        self.assertFalse(entry["counts_toward_persistence"])
        self.assertEqual(entry["persistence"]["state_natural_observation_count_total"], 1)
        self.assertEqual(entry["persistence"]["current_streak_natural_count"], 1)
        self.assertEqual(entry["persistence"]["state_observation_count_total"], 2)
        self.assertEqual(ledger["counted_natural_observations"], 1)
        self.assertEqual(
            ledger["observation_mode_counts"],
            {"NATURAL": 1, "MANUAL": 1, "RECOVERY": 0, "REPLAY": 0},
        )
        self.assertEqual(
            entry["persistence"]["confirmation_status"],
            "NOT_COMPUTABLE_POLICY_UNRATIFIED",
        )

    def test_state_change_then_return_keeps_the_original_first_seen(self):
        changed_path, _ = self._forward("changed.json", "2026-09-11", reverse=True)
        ledger = self._apply(changed_path, "NATURAL")["ledger"]
        self.assertEqual(ledger["entries"][0]["persistence"]["current_streak_natural_count"], 1)
        back_path, _ = self._forward("back.json", "2026-09-12")
        ledger = self._apply(back_path, "NATURAL", ledger)["ledger"]
        entry = ledger["entries"][-1]
        self.assertEqual(entry["first_seen"], self.v1_entry["observed_at"])
        self.assertEqual(entry["persistence"]["state_observation_count_total"], 2)
        self.assertEqual(entry["persistence"]["current_streak_observation_count"], 1)
        self.assertEqual(ledger["ledger_revision"], 3)

    # ---- transitions ------------------------------------------------------

    def test_unknown_to_comparable_with_an_older_session_date_records_recovery(self):
        unknown_path, _ = self._forward("unknown.json", "2026-09-11", unknown=True)
        ledger = self._apply(unknown_path, "NATURAL")["ledger"]
        self.assertEqual(ledger["entries"][-1]["transition"]["type"], "INVALIDATION")
        order_key = ledger["entries"][-1]["source_generated_date_kst"]
        # the recovery carries a market session date strictly older than the
        # order key: exactly the case the /1 axis rejected
        recovered_path, recovered = self._build_source(
            "recovered.json", "2026-09-12", session_date="2026-09-11"
        )
        session = recovered["cross_market_flow"]["comparison_as_of_date"]
        self.assertIsNotNone(session)
        self.assertLess(session, MODULE.source_generated_date_kst(recovered))
        self.assertLessEqual(session, order_key)
        ledger = self._apply(recovered_path, "NATURAL", ledger)["ledger"]
        entry = ledger["entries"][-1]
        self.assertEqual(entry["transition"]["type"], "RECOVERY")
        self.assertEqual(
            entry["current_state"]["cross_market_flow"]["comparison_as_of_date"], session
        )
        self.assertEqual(ledger["ledger_revision"], 3)

    def test_static_or_regressing_comparison_date_still_appends_when_source_advances(self):
        first_path, first = self._forward("pin1.json", "2026-09-11", session_date="2026-09-11")
        ledger = self._apply(first_path, "NATURAL")["ledger"]
        second_path, second = self._build_source(
            "pin2.json", "2026-09-12", session_date="2026-09-11"
        )
        self.assertEqual(
            first["cross_market_flow"]["comparison_as_of_date"],
            second["cross_market_flow"]["comparison_as_of_date"],
        )
        self.assertNotEqual(first["payload_sha256"], second["payload_sha256"])
        ledger = self._apply(second_path, "NATURAL", ledger)["ledger"]
        self.assertEqual(ledger["ledger_revision"], 3)
        self.assertEqual(
            ledger["entries"][-1]["source_generated_date_kst"],
            MODULE.source_generated_date_kst(second),
        )

    def test_exact_leader_laggard_swap_is_reversal(self):
        path, first = self._forward("lead.json", "2026-09-11")
        ledger = self._apply(path, "NATURAL")["ledger"]
        swap_path, second = self._forward("swap.json", "2026-09-12", reverse=True)
        first_flow = first["cross_market_flow"]
        second_flow = second["cross_market_flow"]
        self.assertEqual(
            first_flow["relative_strength_leader"],
            second_flow["relative_strength_laggard"],
        )
        ledger = self._apply(swap_path, "NATURAL", ledger)["ledger"]
        transition = ledger["entries"][-1]["transition"]
        self.assertEqual(transition["type"], "REVERSAL")
        self.assertTrue(transition["reversal"]["detected"])

    # ---- predecessor integrity -------------------------------------------

    def test_each_predecessor_identity_field_is_tamper_evident(self):
        path, _ = self._forward("forward.json", "2026-09-11")
        ledger = self._apply(path, "NATURAL")["ledger"]
        MODULE.validate_ledger(
            json.loads(render(ledger)),
            root=self.root,
            contract_path=self.contract_path,
        )
        original = self._contract()
        for field, value in (
            ("evidence_path", "evidence/portfolio/does/not/exist/packet.json"),
            ("evidence_file_sha256", "0" * 64),
            ("payload_sha256", "1" * 64),
            ("tail_entry_sha256", "2" * 64),
            ("height", 2),
        ):
            contract = copy.deepcopy(original)
            contract["predecessor"][field] = value
            self._write_contract(contract)
            with self.assertRaises(
                MODULE.CrossMarketFlowTransitionLedgerError, msg=field
            ):
                MODULE.validate_ledger(
                    json.loads(render(ledger)),
                    root=self.root,
                    contract_path=self.contract_path,
                )
            self._write_contract(original)

    def test_declared_predecessor_projection_cannot_be_inflated(self):
        path, _ = self._forward("forward.json", "2026-09-11")
        ledger = self._apply(path, "NATURAL")["ledger"]
        tampered = json.loads(render(ledger))
        tampered["predecessor"]["counted_natural_observations"] = 9
        tampered["predecessor"]["observation_mode_counts"]["NATURAL"] = 9
        tampered.pop("payload_sha256")
        tampered["payload_sha256"] = MODULE.payload_sha256(tampered)
        with self.assertRaises(MODULE.CrossMarketFlowTransitionLedgerError) as ctx:
            MODULE.validate_ledger(
                tampered, root=self.root, contract_path=self.contract_path
            )
        self.assertIn("LEDGER_PREDECESSOR_MISMATCH", str(ctx.exception))

    def test_missing_predecessor_evidence_or_pointer_fails_closed(self):
        path, _ = self._forward("forward.json", "2026-09-11")
        latest = self._latest()
        with self.assertRaises(MODULE.CrossMarketFlowTransitionLedgerError) as ctx:
            self._apply(path, "NATURAL", None)
        self.assertIn("PREDECESSOR_REQUIRED_MISSING", str(ctx.exception))
        self.v1_evidence.unlink()
        with self.assertRaises(MODULE.CrossMarketFlowTransitionLedgerError) as ctx:
            self._apply(path, "NATURAL", latest)
        self.assertIn("PREDECESSOR_REQUIRED_MISSING", str(ctx.exception))

    def test_predecessor_ledger_pointer_from_another_chain_is_rejected(self):
        path, _ = self._forward("forward.json", "2026-09-11")
        foreign = copy.deepcopy(self.v1_ledger)
        foreign["ledger_id"] = "SOMETHING_ELSE"
        foreign.pop("payload_sha256")
        foreign["payload_sha256"] = MODULE.payload_sha256(foreign)
        with self.assertRaises(MODULE.CrossMarketFlowTransitionLedgerError) as ctx:
            self._apply(path, "NATURAL", foreign)
        self.assertIn("PREDECESSOR_LEDGER_IDENTITY_MISMATCH", str(ctx.exception))

    def test_v1_shaped_entry_cannot_be_mixed_into_a_v2_chain(self):
        path, _ = self._forward("forward.json", "2026-09-11")
        ledger = self._apply(path, "NATURAL")["ledger"]
        mixed = json.loads(render(ledger))
        mixed["entries"].append(copy.deepcopy(self.v1_entry))
        mixed["ledger_revision"] = 3
        mixed.pop("payload_sha256")
        mixed["payload_sha256"] = MODULE.payload_sha256(mixed)
        with self.assertRaises(MODULE.CrossMarketFlowTransitionLedgerError) as ctx:
            MODULE.validate_ledger(
                mixed, root=self.root, contract_path=self.contract_path
            )
        self.assertIn("LEDGER_ENTRY_FIELDS_MISMATCH", str(ctx.exception))

    # ---- tamper and determinism ------------------------------------------

    def test_p2_market_counts_require_exact_int_and_allow_zero(self):
        _, packet = self._build_source("counts.json", "2026-09-11")
        for key in ("comparable_market_count", "required_market_count"):
            with self.subTest(key=key, value=0):
                candidate = copy.deepcopy(packet)
                candidate["cross_market_flow"][key] = 0
                flow = MODULE._current_state(candidate)["cross_market_flow"]
                self.assertEqual(flow[key], 0)
                self.assertIs(type(flow[key]), int)
            with self.subTest(key=key, value=True):
                candidate["cross_market_flow"][key] = True
                with self.assertRaisesRegex(
                    MODULE.CrossMarketFlowTransitionLedgerError,
                    f"SOURCE_MARKET_COUNT_TYPE_INVALID:{key}",
                ):
                    MODULE._current_state(candidate)

    def test_rehashed_state_confirmation_and_authority_tamper_fail_closed(self):
        path, _ = self._forward("forward.json", "2026-09-11")
        ledger = self._apply(path, "NATURAL")["ledger"]

        def rehash(value: dict) -> dict:
            value = copy.deepcopy(value)
            previous = value["predecessor"]["tail"]["entry_sha256"]
            for entry in value["entries"]:
                entry["previous_entry_sha256"] = previous
                entry.pop("entry_sha256", None)
                entry["entry_sha256"] = MODULE.payload_sha256(entry)
                previous = entry["entry_sha256"]
            value.pop("payload_sha256", None)
            value["payload_sha256"] = MODULE.payload_sha256(value)
            return value

        def expect_failure(mutate, fragment):
            candidate = json.loads(render(ledger))
            mutate(candidate)
            with self.assertRaises(
                MODULE.CrossMarketFlowTransitionLedgerError, msg=fragment
            ) as ctx:
                MODULE.validate_ledger(
                    rehash(candidate), root=self.root, contract_path=self.contract_path
                )
            self.assertIn(fragment, str(ctx.exception))

        def set_confirmed(value):
            value["entries"][0]["confirmed_at"] = "2026-09-11T00:00:00Z"

        def set_order_key(value):
            value["entries"][0]["source_generated_date_kst"] = "2026-09-20"

        def set_state(value):
            flow = value["entries"][0]["current_state"]["cross_market_flow"]
            flow["relative_strength_leader"] = "CRYPTO"
            value["entries"][0]["current_semantic_state_sha256"] = MODULE.payload_sha256(
                MODULE._semantic_state(value["entries"][0]["current_state"])
            )
            value["current_state"] = copy.deepcopy(value["entries"][0]["current_state"])

        def set_streak(value):
            value["entries"][0]["persistence"]["current_streak_natural_count"] = 9

        expect_failure(set_confirmed, "LEDGER_ENTRY_CHAIN_INVALID")
        expect_failure(set_order_key, "LEDGER_SOURCE_LINEAGE_MISMATCH")
        expect_failure(set_state, "LEDGER_STATE_SOURCE_MISMATCH")
        expect_failure(set_streak, "LEDGER_ENTRY_DERIVATION_MISMATCH")
        for key in ("trading_authorized", "order_authorized", "confirmation_authorized"):
            expect_failure(
                lambda value, key=key: value["authority"].__setitem__(key, True),
                "LEDGER_CONTRACT_MISMATCH",
            )

    def test_restart_and_duplicate_are_byte_identical_and_write_nothing_new(self):
        path, _ = self._forward("forward.json", "2026-09-11")
        first = self._apply(path, "NATURAL")["ledger"]
        again = MODULE.apply_observation(
            path,
            "NATURAL",
            self._latest(),
            root=self.root,
            contract_path=self.contract_path,
        )["ledger"]
        self.assertEqual(render(first), render(again))
        duplicate = self._apply(path, "REPLAY", json.loads(render(first)))
        self.assertEqual(duplicate["action"], "V2_NOOP")
        self.assertEqual(render(duplicate["ledger"]), render(first))
        self.assertEqual(duplicate["ledger"]["observation_mode_counts"]["REPLAY"], 0)

    def test_written_evidence_is_content_addressed_and_matches_the_pointer(self):
        path, _ = self._forward("forward.json", "2026-09-11")
        ledger = self._apply(path, "NATURAL")["ledger"]
        evidence, latest = MODULE.write_outputs(ledger, self.root)
        self.assertEqual(evidence.read_bytes(), latest.read_bytes())
        self.assertEqual(evidence.read_bytes(), render(ledger))
        self.assertEqual(evidence.parent.name, ledger["payload_sha256"])
        self.assertEqual(
            evidence.parent.parent.name,
            ledger["entries"][-1]["source_generated_date_kst"],
        )
        self.assertTrue(self.v1_evidence.exists(), "v1 evidence must remain untouched")
        MODULE.write_outputs(ledger, self.root)
        self.assertEqual(evidence.read_bytes(), render(ledger))

    def test_empty_ledger_is_never_written(self):
        contract = MODULE.load_contract(self.contract_path)
        empty = MODULE.empty_ledger(
            contract, contract_path=self.contract_path, root=self.root
        )
        self.assertEqual(empty["status"], "EMPTY")
        self.assertEqual(empty["ledger_revision"], 1)
        self.assertEqual(empty["counted_natural_observations"], 1)
        MODULE.validate_ledger(
            empty, contract, root=self.root, contract_path=self.contract_path
        )
        with self.assertRaises(MODULE.CrossMarketFlowTransitionLedgerError) as ctx:
            MODULE.write_outputs(empty, self.root)
        self.assertIn("EMPTY_LEDGER_WRITE_FORBIDDEN", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=1)
