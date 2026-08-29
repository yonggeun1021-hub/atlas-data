#!/usr/bin/env python3
"""P10-01 committed Daily Briefing to Shadow readiness regressions."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shadow" / "three_market_shadow_operational_readiness.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "three_market_shadow_operational_readiness", SOURCE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module()
UNIFIED_FIXTURE_PATH = ROOT / "test" / "test_unified_decision_contract.py"
spec = importlib.util.spec_from_file_location("p10_01_unified_fixture", UNIFIED_FIXTURE_PATH)
UNIFIED_FIXTURE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(UNIFIED_FIXTURE)
def _has_validated_unified_decision(path: Path) -> bool:
    """True only if `path`'s UNIFIED_DECISION component is validated=True.

    The daily briefing pipeline commits `packet.json` incrementally; the very
    latest committed packet can legitimately still have UNIFIED_DECISION
    unvalidated (e.g. the first run of a newly activated slot). This
    regression needs a genuinely validated packet to exercise the accept
    path -- see the identical helper in test_decision_change_lineage_operational.py.
    """
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    rows = payload.get("components")
    if not isinstance(rows, list):
        return False
    matches = [
        row for row in rows
        if isinstance(row, dict) and row.get("component_id") == "UNIFIED_DECISION"
    ]
    return (
        len(matches) == 1
        and matches[0].get("validated") is True
        and isinstance(matches[0].get("packet"), dict)
    )


def _latest_packet_with_validated_unified_decision(candidates):
    for candidate in reversed(candidates):
        if _has_validated_unified_decision(candidate):
            return candidate
    raise AssertionError(
        "No committed evidence/daily_briefing/**/packet.json has a validated "
        "UNIFIED_DECISION component yet -- this P10-01 shadow readiness "
        "regression needs at least one fully-validated daily packet."
    )


PACKET = _latest_packet_with_validated_unified_decision(
    sorted((ROOT / "evidence" / "daily_briefing").rglob("packet.json"))
)


def commit_for(path: Path) -> str:
    return subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", path.relative_to(ROOT).as_posix()],
        cwd=ROOT,
        text=True,
    ).strip()


SOURCE_COMMIT = commit_for(PACKET)
RECORDED_AT = "2026-08-27T00:00:01Z"


def current_unified():
    return UNIFIED_FIXTURE.MODULE.build_packet(
        UNIFIED_FIXTURE.components(),
        UNIFIED_FIXTURE.reasons(),
        "2026-08-27",
        "morning",
        "2026-08-27T00:00:00Z",
        UNIFIED_FIXTURE.CONTRACT,
    )


def synthetic_daily(extra_components=None):
    rows = [{
        "component_id": "UNIFIED_DECISION",
        "validated": True,
        "packet": current_unified(),
    }]
    rows.extend(extra_components or [])
    return {
        "decision_date": "2026-08-27",
        "slot": "morning",
        "packet_sha256": "1" * 64,
        "components": rows,
    }


class ThreeMarketShadowOperationalReadinessTests(unittest.TestCase):
    def packet(self, daily=None, recorded_at=RECORDED_AT):
        patcher = mock.patch.object(
            MODULE.DAILY_LINEAGE,
            "_validate_daily_at_commit",
            return_value=synthetic_daily() if daily is None else daily,
        )
        with patcher:
            return MODULE.build_packet(PACKET, SOURCE_COMMIT, recorded_at)

    def test_real_committed_source_reports_exact_missing_p9_boundary(self):
        # No mocked validator: exercise the exact historical commit archive.
        packet = MODULE.build_packet(PACKET, SOURCE_COMMIT, RECORDED_AT)
        self.assertEqual(packet["status"], "BLOCKED_MISSING_EXACT_P9_LIVE_INPUTS")
        self.assertEqual(packet["summary"]["unified_decision_ready_count"], 1)
        self.assertEqual(packet["summary"]["entry_exit_trigger_eligibility_ready_count"], 0)
        self.assertEqual(packet["summary"]["intraday_risk_escalation_ready_count"], 0)
        self.assertEqual(packet["summary"]["shadow_append_ready_count"], 0)
        self.assertEqual(packet["summary"]["shadow_record_count"], 0)
        self.assertEqual(packet["summary"]["real_capital_deployed"], "0")
        self.assertEqual(packet["summary"]["real_order_count"], 0)
        self.assertIsNone(
            packet["source"]["entry_exit_trigger_eligibility_packet_sha256"]
        )
        self.assertIsNone(packet["source"]["intraday_risk_escalation_packet_sha256"])
        self.assertIsNone(packet["shadow_ledger"])
        self.assertIsNone(packet["action"])
        self.assertIsNone(packet["order_intent"])

    def test_all_authorities_remain_false(self):
        self.assertTrue(self.packet()["authority"])
        self.assertTrue(all(value is False for value in self.packet()["authority"].values()))

    def test_missing_or_unvalidated_unified_is_rejected(self):
        daily = {
            "packet_sha256": "2" * 64,
            "components": [],
        }
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowOperationalReadinessError,
            "UNIFIED_DECISION_MUST_BE_VALIDATED",
        ):
            self.packet(daily)

    def test_duplicate_daily_component_is_rejected(self):
        daily = synthetic_daily()
        daily["components"].append(copy.deepcopy(daily["components"][0]))
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowOperationalReadinessError,
            "DAILY_COMPONENT_DUPLICATE:UNIFIED_DECISION",
        ):
            self.packet(daily)

    def test_fake_p9_packets_cannot_unlock_readiness(self):
        rows = [
            {"component_id": "ENTRY_EXIT_TRIGGER_ELIGIBILITY", "validated": True, "packet": {}},
            {"component_id": "INTRADAY_RISK_ESCALATION", "validated": True, "packet": {}},
        ]
        daily = synthetic_daily(rows)
        with mock.patch.object(
            MODULE,
            "_validate_shadow_inputs_at_commit",
            side_effect=MODULE.ThreeMarketShadowOperationalReadinessError(
                "P9_LIVE_INPUTS_INVALID_AT_SOURCE_COMMIT"
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.ThreeMarketShadowOperationalReadinessError,
                "P9_LIVE_INPUTS_INVALID_AT_SOURCE_COMMIT",
            ):
                self.packet(daily)

    def test_exact_commit_validated_p9_only_opens_zero_capital_readiness(self):
        rows = [
            {
                "component_id": "ENTRY_EXIT_TRIGGER_ELIGIBILITY",
                "validated": True,
                "packet": {"synthetic": "entry"},
            },
            {
                "component_id": "INTRADAY_RISK_ESCALATION",
                "validated": True,
                "packet": {"synthetic": "risk"},
            },
        ]
        daily = synthetic_daily(rows)
        with mock.patch.object(
            MODULE,
            "_validate_shadow_inputs_at_commit",
            return_value=(
                current_unified(),
                {
                    "generated_at": "2026-08-27T00:00:00Z",
                    "packet_sha256": "2" * 64,
                },
                {
                    "observed_at": "2026-08-27T00:00:00Z",
                    "packet_sha256": "3" * 64,
                },
            ),
        ) as exact_validator:
            packet = self.packet(daily)
        self.assertEqual(exact_validator.call_count, 2)
        self.assertTrue(
            all(
                call.args
                == (SOURCE_COMMIT, PACKET.relative_to(ROOT).as_posix())
                for call in exact_validator.call_args_list
            )
        )
        self.assertEqual(packet["status"], "READY_FOR_ZERO_CAPITAL_SHADOW_APPEND")
        self.assertEqual(packet["summary"]["shadow_append_ready_count"], 1)
        self.assertEqual(packet["summary"]["shadow_record_count"], 0)
        self.assertEqual(packet["summary"]["real_capital_deployed"], "0")
        self.assertEqual(packet["summary"]["real_order_count"], 0)
        self.assertEqual(
            packet["source"]["entry_exit_trigger_eligibility_packet_sha256"],
            "2" * 64,
        )
        self.assertEqual(
            packet["source"]["intraday_risk_escalation_packet_sha256"],
            "3" * 64,
        )
        self.assertTrue(all(value is False for value in packet["authority"].values()))

    def test_future_unified_decision_is_rejected(self):
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowOperationalReadinessError,
            "UNIFIED_DECISION_FROM_FUTURE",
        ):
            self.packet(recorded_at="2026-08-26T23:59:59Z")

    def test_current_unified_validator_is_never_used_for_historical_packet(self):
        with mock.patch.object(
            MODULE.SHADOW.UNIFIED,
            "validate_packet",
            side_effect=AssertionError("current validator must not reinterpret history"),
        ) as current_validator:
            packet = MODULE.build_packet(PACKET, SOURCE_COMMIT, RECORDED_AT)
        self.assertEqual(packet["status"], "BLOCKED_MISSING_EXACT_P9_LIVE_INPUTS")
        current_validator.assert_not_called()

    def test_semantic_tamper_with_valid_new_hash_is_rejected(self):
        packet = self.packet()
        packet["summary"]["shadow_record_count"] = 1
        packet["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in packet.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowOperationalReadinessError,
            "SEMANTIC_TAMPER_OR_DRIFT",
        ):
            MODULE.validate_packet(packet, PACKET, SOURCE_COMMIT, RECORDED_AT)

    def test_contract_authority_escalation_is_rejected(self):
        contract = MODULE.load_contract()
        contract["authority"]["trading_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowOperationalReadinessError,
            "CONTRACT_TAMPER_OR_DRIFT",
        ):
            MODULE.build_packet(PACKET, SOURCE_COMMIT, RECORDED_AT, contract)

    def test_content_addressed_write_is_idempotent(self):
        packet = self.packet()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, created = MODULE.write_packet(packet, root)
            self.assertTrue(created)
            self.assertEqual(MODULE.write_packet(packet, root), (path, False))
            self.assertEqual(json.loads(path.read_text()), packet)

    def test_module_has_no_network_order_or_global_monkeypatch_surface(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertTrue({"requests", "urllib", "httpx"}.isdisjoint(imports))
        text = SOURCE.read_text(encoding="utf-8")
        for forbidden in ("/v2/orders", "submit_order", "globals()[", "monkeypatch"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
