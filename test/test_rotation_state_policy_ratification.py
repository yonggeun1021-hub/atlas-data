#!/usr/bin/env python3
"""P2-05 CIO-ratified rotation_state_policy/1 identity/evidence regression.

Covers: contract integrity/tamper, the 9-cell mapping's exact match against
the ledger's own structural_bucket_transitions, the documented semantic rule
being mechanically consistent with the ratified mapping (protects against a
future silent edit to one drifting from the other), an independent
recomputation of the Korea maximum_ledger_gap_days derivation straight from
the canonical KRX holiday capture (not just asserting the stored numbers),
build_policy() producing a real, ledger-acceptable rotation_state_policy/1
for each market when bound to that market's own real-shaped test packet, a
"no override surface" regression (the self-ratification-bypass shape PR #348
removed cannot recur here because build_policy() accepts no parameter through
which a caller could inject an alternate mapping/ratifier/approval status),
and confirmation that policy_id never collides with the test-only
"*.STATE.TEST.V1" fixture family.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import inspect
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "rotation" / "rotation_state_policy_ratification.py"
LEDGER_SCRIPT = ROOT / "rotation" / "rotation_state_ledger.py"
LEDGER_TEST = ROOT / "test" / "test_rotation_state_ledger.py"
CALENDAR_SCRIPT = ROOT / "market_data" / "krx_official_holiday_calendar.py"
CALENDAR_CAPTURE = (
    ROOT / "evidence" / "market_calendar" / "krx_global_holiday" / "2026-09-09"
    / "capture-2026.json"
)
P2_03_WORKFLOW = ROOT / ".github" / "workflows" / "p2-03-korea-observation-pair.yml"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("rotation_state_policy_ratification", SCRIPT)
LEDGER = load_module("rotation_state_ledger_for_ratification_test", LEDGER_SCRIPT)
LEDGER_FIXTURES = load_module(
    "rotation_state_ledger_fixtures_for_ratification_test", LEDGER_TEST
)
CALENDAR = load_module("krx_official_holiday_calendar_for_ratification_test", CALENDAR_SCRIPT)

CONTRACT = MODULE.load_contract()


class ContractIntegrityTests(unittest.TestCase):
    def test_contract_matches_expected(self):
        self.assertEqual(CONTRACT, MODULE._expected_contract())

    def test_mapping_tamper_rejected(self):
        tampered = copy.deepcopy(CONTRACT)
        tampered["state_by_bucket_transition"]["TOP_TO_TOP"] = "WEAKENING"
        with self.assertRaisesRegex(
            MODULE.RotationStatePolicyRatificationError, "CONTRACT_FIELD_MISMATCH"
        ):
            MODULE._validate_contract(tampered)

    def test_gap_tamper_rejected(self):
        tampered = copy.deepcopy(CONTRACT)
        tampered["markets"]["KOREA"]["maximum_ledger_gap_days"] = 3
        with self.assertRaisesRegex(
            MODULE.RotationStatePolicyRatificationError, "CONTRACT_FIELD_MISMATCH"
        ):
            MODULE._validate_contract(tampered)

    def test_ratifier_tamper_rejected(self):
        tampered = copy.deepcopy(CONTRACT)
        tampered["ratified_by"] = "test-cio"
        with self.assertRaisesRegex(
            MODULE.RotationStatePolicyRatificationError, "CONTRACT_FIELD_MISMATCH"
        ):
            MODULE._validate_contract(tampered)

    def test_extra_field_rejected(self):
        tampered = copy.deepcopy(CONTRACT)
        tampered["extra"] = True
        with self.assertRaisesRegex(
            MODULE.RotationStatePolicyRatificationError, "CONTRACT_FIELDS_MISMATCH"
        ):
            MODULE._validate_contract(tampered)


class NineCellCrossCheckTests(unittest.TestCase):
    def test_nine_cells_match_ledger_structural_transitions(self):
        ledger_contract = LEDGER.load_contract()
        self.assertEqual(
            set(CONTRACT["state_by_bucket_transition"]),
            set(ledger_contract["structural_bucket_transitions"]),
        )
        self.assertEqual(9, len(CONTRACT["state_by_bucket_transition"]))

    def test_vocabulary_matches_ledger_contract(self):
        ledger_contract = LEDGER.load_contract()
        self.assertEqual(CONTRACT["state_vocabulary"], ledger_contract["state_vocabulary"])
        self.assertEqual(set(CONTRACT["state_vocabulary"]), set(CONTRACT["state_semantics"]))


class SemanticRuleConsistencyTests(unittest.TestCase):
    """The ratified 9-cell mapping must mechanically follow the ratified
    semantic rule. If someone edits one without the other, this fails."""

    def test_mapping_matches_documented_semantics(self):
        semantics = CONTRACT["state_semantics"]
        self.assertEqual(semantics["STRONG"], "CURRENT_BUCKET_TOP")
        self.assertEqual(
            semantics["WEAKENING"], "CURRENT_BUCKET_BOTTOM_OR_TOP_TO_MIDDLE"
        )
        self.assertEqual(semantics["EMERGING"], "NON_WEAKENING_MIDDLE_STATE")
        for cell, state in CONTRACT["state_by_bucket_transition"].items():
            prior, current = cell.split("_TO_")
            if current == "TOP":
                expected = "STRONG"
            elif current == "BOTTOM":
                expected = "WEAKENING"
            elif prior == "TOP":
                expected = "WEAKENING"
            else:
                expected = "EMERGING"
            self.assertEqual(state, expected, cell)

    def test_middle_to_middle_is_not_labelled_as_rising(self):
        # CIO's explicit clarification: MIDDLE_TO_MIDDLE = EMERGING means
        # "held MIDDLE without decline," never "improving."
        self.assertEqual(
            CONTRACT["state_by_bucket_transition"]["MIDDLE_TO_MIDDLE"], "EMERGING"
        )
        self.assertEqual(
            CONTRACT["state_semantics"]["EMERGING"], "NON_WEAKENING_MIDDLE_STATE"
        )
        self.assertNotIn("RISING", CONTRACT["state_semantics"]["EMERGING"])
        self.assertNotIn("IMPROV", CONTRACT["state_semantics"]["EMERGING"])


class KoreaGapMechanicalDerivationTests(unittest.TestCase):
    """Independently recompute the Korea gap from the canonical capture file
    -- do not just assert the stored numbers."""

    @classmethod
    def setUpClass(cls):
        raw = CALENDAR_CAPTURE.read_bytes()
        checked = CALENDAR.validate_capture(raw)
        cls.holidays = {dt.date.fromisoformat(iso) for iso in checked["closures"]}
        cls.year = checked["capture"]["year"]

    def _trading_days(self):
        start = dt.date(self.year, 1, 1)
        end = dt.date(self.year, 12, 31)
        days = []
        current = start
        while current <= end:
            if current.weekday() < 5 and current not in self.holidays:
                days.append(current)
            current += dt.timedelta(days=1)
        return days

    def test_capture_year_matches_contract(self):
        self.assertEqual(self.year, CONTRACT["korea_gap_derivation"]["canonical_calendar_year"])

    def test_natural_maximum_gap_is_six(self):
        trading_days = self._trading_days()
        gaps = [
            (trading_days[i] - trading_days[i - 1]).days
            for i in range(1, len(trading_days))
        ]
        self.assertEqual(max(gaps), 6)
        self.assertEqual(
            max(gaps),
            CONTRACT["korea_gap_derivation"]["natural_maximum_scheduled_gap_days"],
        )

    def test_one_missed_session_grace_yields_seven(self):
        trading_days = self._trading_days()
        worst = max(
            (trading_days[i + 2] - trading_days[i]).days
            for i in range(len(trading_days) - 2)
        )
        self.assertEqual(worst, 7)
        self.assertEqual(
            worst, CONTRACT["korea_gap_derivation"]["maximum_ledger_gap_days"]
        )
        self.assertEqual(
            CONTRACT["markets"]["KOREA"]["maximum_ledger_gap_days"], worst
        )

    def test_missed_session_grace_is_exactly_one(self):
        self.assertEqual(
            CONTRACT["korea_gap_derivation"]["missed_session_grace_days"], 1
        )


class CronBoundaryTests(unittest.TestCase):
    def test_p2_03_combined_workflow_has_no_schedule_trigger(self):
        text = P2_03_WORKFLOW.read_text(encoding="utf-8")
        lines = text.splitlines()
        on_index = next(i for i, line in enumerate(lines) if line.strip() == "on:")
        trigger_block = []
        for line in lines[on_index + 1:]:
            if line and not line.startswith((" ", "\t")):
                break
            trigger_block.append(line)
        self.assertTrue(
            any("workflow_dispatch:" in line for line in trigger_block)
        )
        self.assertFalse(
            any("schedule:" in line or "cron:" in line for line in trigger_block),
            "p2-03-korea-observation-pair.yml must stay workflow_dispatch-only "
            "-- adding a schedule/cron here is explicitly out of this lane's scope.",
        )

    def test_contract_records_no_cron_status(self):
        self.assertEqual(
            CONTRACT["korea_gap_derivation"]["cron_status"],
            "P2-03_COMBINED_WORKFLOW_WORKFLOW_DISPATCH_ONLY_NO_SCHEDULE",
        )


class MarketGapValueTests(unittest.TestCase):
    def test_us_gap(self):
        self.assertEqual(MODULE.market_gap_days("US"), 4)

    def test_korea_gap(self):
        self.assertEqual(MODULE.market_gap_days("KOREA"), 7)

    def test_crypto_gap(self):
        self.assertEqual(MODULE.market_gap_days("CRYPTO"), 2)

    def test_unsupported_market_rejected(self):
        with self.assertRaisesRegex(
            MODULE.RotationStatePolicyRatificationError, "MARKET_UNSUPPORTED"
        ):
            MODULE.market_gap_days("JAPAN")

    def test_only_korea_carries_an_operational_assumption(self):
        self.assertIsNone(CONTRACT["markets"]["US"]["operational_assumption"])
        self.assertIsNone(CONTRACT["markets"]["CRYPTO"]["operational_assumption"])
        self.assertEqual(
            CONTRACT["markets"]["KOREA"]["operational_assumption"],
            "ONE_FULL_P2_03_PACKET_PER_KRX_TRADING_DAY_POST_CLOSE",
        )


class BuildPolicyIntegrationTests(unittest.TestCase):
    """build_policy() bound to each market's own real-shaped rotation packet
    must be accepted end-to-end by the unchanged rotation_state_ledger.py."""

    def _apply(self, packet, policy):
        return LEDGER.apply_rotation(packet, policy)

    def test_us_policy_accepted_by_ledger(self):
        packet = LEDGER_FIXTURES.us_packet(as_of_date="2026-09-15")
        policy = MODULE.build_policy(
            "US",
            packet["contract_version"],
            packet["lineage"]["rotation_policy_sha256"],
        )
        self.assertEqual(policy["policy_id"], "US.ROTATION_STATE.CIO_RATIFIED.V1")
        self.assertEqual(policy["maximum_ledger_gap_days"], 4)
        ledger = self._apply(packet, policy)
        self.assertEqual(ledger["status"], "STATE_HISTORY_OBSERVED")
        self.assertTrue(ledger["authority"]["state_ledger_authorized"])
        self.assertFalse(ledger["authority"]["regime_input_authorized"])
        self.assertFalse(ledger["authority"]["production_authorized"])
        self.assertFalse(ledger["authority"]["trading_authorized"])

    def test_korea_policy_accepted_by_ledger(self):
        packet = LEDGER_FIXTURES.korea_packet(as_of_date="2026-09-15")
        policy = MODULE.build_policy(
            "KOREA",
            packet["contract_version"],
            packet["lineage"]["rotation_policy_sha256"],
        )
        self.assertEqual(policy["maximum_ledger_gap_days"], 7)
        ledger = self._apply(packet, policy)
        self.assertEqual(ledger["status"], "STATE_HISTORY_OBSERVED")

    def test_crypto_policy_accepted_by_ledger(self):
        packet = LEDGER_FIXTURES.crypto_packet(as_of_date="2026-09-15")
        policy = MODULE.build_policy(
            "CRYPTO",
            packet["contract_version"],
            packet["lineage"]["rotation_policy_sha256"],
        )
        self.assertEqual(policy["maximum_ledger_gap_days"], 2)
        ledger = self._apply(packet, policy)
        self.assertEqual(ledger["status"], "STATE_HISTORY_OBSERVED")

    def test_policy_identity_hash_is_deterministic_and_content_addressed(self):
        packet = LEDGER_FIXTURES.crypto_packet(as_of_date="2026-09-15")
        policy = MODULE.build_policy(
            "CRYPTO",
            packet["contract_version"],
            packet["lineage"]["rotation_policy_sha256"],
        )
        first = MODULE.policy_identity_sha256(policy)
        second = MODULE.policy_identity_sha256(copy.deepcopy(policy))
        self.assertEqual(first, second)
        mutated = copy.deepcopy(policy)
        mutated["maximum_ledger_gap_days"] = 99
        self.assertNotEqual(first, MODULE.policy_identity_sha256(mutated))

    def test_wrong_contract_version_rejected(self):
        packet = LEDGER_FIXTURES.us_packet(as_of_date="2026-09-15")
        with self.assertRaisesRegex(
            MODULE.RotationStatePolicyRatificationError,
            "ROTATION_CONTRACT_VERSION_MISMATCH",
        ):
            MODULE.build_policy(
                "US", "us_capital_rotation/999",
                packet["lineage"]["rotation_policy_sha256"],
            )

    def test_bad_sha_rejected(self):
        packet = LEDGER_FIXTURES.us_packet(as_of_date="2026-09-15")
        with self.assertRaisesRegex(
            MODULE.RotationStatePolicyRatificationError, "ROTATION_POLICY_SHA_INVALID"
        ):
            MODULE.build_policy("US", packet["contract_version"], "not-a-sha")


class NoOverrideSurfaceRegressionTests(unittest.TestCase):
    """PR #348 removed a production script that had copied the test-fixture
    mapping and self-labelled it RATIFIED. build_policy() must expose no
    parameter through which a caller could reintroduce that shape."""

    def test_build_policy_signature_has_no_override_parameters(self):
        signature = inspect.signature(MODULE.build_policy)
        self.assertEqual(
            list(signature.parameters),
            [
                "market",
                "rotation_contract_version",
                "rotation_policy_sha256",
                "contract",
            ],
        )

    def test_build_policy_rejects_unknown_keyword(self):
        packet = LEDGER_FIXTURES.us_packet(as_of_date="2026-09-15")
        with self.assertRaises(TypeError):
            MODULE.build_policy(
                "US",
                packet["contract_version"],
                packet["lineage"]["rotation_policy_sha256"],
                ratified_by="someone-else",
            )
        with self.assertRaises(TypeError):
            MODULE.build_policy(
                "US",
                packet["contract_version"],
                packet["lineage"]["rotation_policy_sha256"],
                state_by_bucket_transition={"TOP_TO_TOP": "WEAKENING"},
            )

    def test_policy_ids_never_collide_with_test_fixture_family(self):
        for policy_id in CONTRACT["policy_id_by_market"].values():
            self.assertNotIn("STATE.TEST", policy_id)
            self.assertNotEqual(policy_id.split(".")[-1], "TEST.V1")

    def test_ratified_by_is_never_the_test_fixture_value(self):
        self.assertNotEqual(CONTRACT["ratified_by"], "test-cio")
        self.assertEqual(CONTRACT["ratified_by"], "CIO")


if __name__ == "__main__":
    unittest.main()
