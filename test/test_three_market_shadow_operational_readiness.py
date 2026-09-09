#!/usr/bin/env python3
"""P10-01 committed Daily Briefing to Shadow readiness regressions."""
import ast
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
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
LEDGER_FIXTURE_PATH = ROOT / "test" / "test_three_market_shadow_ledger.py"
spec = importlib.util.spec_from_file_location("p10_01_ledger_fixture", LEDGER_FIXTURE_PATH)
LEDGER_FIXTURE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(LEDGER_FIXTURE)
PACKETS = sorted((ROOT / "evidence" / "daily_briefing").rglob("packet.json"))


def validated_unified_generated_at(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rows = [
        row for row in value.get("components", [])
        if isinstance(row, dict) and row.get("component_id") == "UNIFIED_DECISION"
    ]
    if (
        len(rows) != 1
        or rows[0].get("validated") is not True
        or not isinstance(rows[0].get("packet"), dict)
    ):
        return None
    generated_at = value.get("generated_at")
    if rows[0]["packet"].get("generated_at") != generated_at:
        return None
    try:
        return dt.datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except (TypeError, ValueError):
        return None


def latest_validated_packet(paths):
    candidates = [
        (generated_at, path)
        for path in paths
        if (generated_at := validated_unified_generated_at(path)) is not None
    ]
    if not candidates:
        raise RuntimeError("VALIDATED_UNIFIED_DECISION_DAILY_FIXTURE_MISSING")
    return max(candidates, key=lambda item: (item[0], item[1].as_posix()))[1]


PACKET = latest_validated_packet(PACKETS)


def commit_for(path: Path) -> str:
    return subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", path.relative_to(ROOT).as_posix()],
        cwd=ROOT,
        text=True,
    ).strip()


SOURCE_COMMIT = commit_for(PACKET)
RECORDED_AT = (
    validated_unified_generated_at(PACKET) + dt.timedelta(seconds=1)
).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    def packet(self, daily=None, recorded_at=RECORDED_AT, previous_ledger=None):
        patcher = mock.patch.object(
            MODULE.DAILY_LINEAGE,
            "_validate_daily_at_commit",
            return_value=synthetic_daily() if daily is None else daily,
        )
        with patcher:
            return MODULE.build_packet(
                PACKET, SOURCE_COMMIT, recorded_at, previous_ledger=previous_ledger
            )

    def test_latest_validated_fixture_is_selected_by_generated_at(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lexical_last_but_old = root / "z-old.json"
            chronological_latest = root / "a-new.json"
            for path, generated_at in (
                (lexical_last_but_old, "2026-08-28T13:41:45Z"),
                (chronological_latest, "2026-08-29T00:39:03Z"),
            ):
                path.write_text(json.dumps({
                    "generated_at": generated_at,
                    "components": [{
                        "component_id": "UNIFIED_DECISION",
                        "validated": True,
                        "packet": {"generated_at": generated_at},
                    }],
                }), encoding="utf-8")
            self.assertEqual(
                latest_validated_packet(sorted(root.glob("*.json"))),
                chronological_latest,
            )

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
        authority = self.packet()["authority"]
        self.assertTrue(authority["shadow_observation_recording_authorized"])
        self.assertTrue(all(
            value is False for key, value in authority.items()
            if key != "shadow_observation_recording_authorized"
        ))

    def test_completed_append_uses_distinct_v2_contract_identity(self):
        contract = MODULE.load_contract()
        self.assertEqual(contract["schema_version"], 2)
        self.assertEqual(contract["contract_version"], "three_market_shadow_operational_readiness/2")
        packet = self.packet()
        self.assertEqual(packet["contract_version"], contract["contract_version"])
        self.assertEqual(packet["schema_version"], "three_market_shadow_operational_readiness_packet/2")

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

    def test_exact_commit_validated_p9_appends_zero_capital_ledger(self):
        unified = current_unified()
        entry_exit, intraday_risk = LEDGER_FIXTURE.intraday_evidence(unified)
        rows = [
            {
                "component_id": "ENTRY_EXIT_TRIGGER_ELIGIBILITY",
                "validated": True,
                "packet": entry_exit,
            },
            {
                "component_id": "INTRADAY_RISK_ESCALATION",
                "validated": True,
                "packet": intraday_risk,
            },
        ]
        daily = synthetic_daily(rows)
        with mock.patch.object(
            MODULE,
            "_validate_shadow_inputs_at_commit",
            return_value=(
                unified,
                entry_exit,
                intraday_risk,
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
        self.assertEqual(packet["status"], "ZERO_CAPITAL_SHADOW_APPENDED")
        self.assertEqual(packet["summary"]["shadow_append_ready_count"], 1)
        self.assertEqual(packet["summary"]["shadow_record_count"], 1)
        self.assertEqual(packet["summary"]["real_capital_deployed"], "0")
        self.assertEqual(packet["summary"]["real_order_count"], 0)
        self.assertEqual(
            packet["source"]["entry_exit_trigger_eligibility_packet_sha256"],
            entry_exit["packet_sha256"],
        )
        self.assertEqual(
            packet["source"]["intraday_risk_escalation_packet_sha256"],
            intraday_risk["packet_sha256"],
        )
        self.assertEqual(packet["shadow_ledger"]["ledger_revision"], 1)
        self.assertEqual(packet["shadow_ledger"]["records"][0]["unified_decision"], unified)
        self.assertEqual(packet["shadow_ledger"]["summary"]["real_capital_deployed"], "0")
        self.assertEqual(packet["shadow_ledger"]["summary"]["real_order_count"], 0)
        with mock.patch.object(
            MODULE,
            "_validate_shadow_inputs_at_commit",
            return_value=(unified, entry_exit, intraday_risk),
        ):
            retry = self.packet(daily, previous_ledger=packet["shadow_ledger"])
        self.assertEqual(retry["shadow_ledger"], packet["shadow_ledger"])

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

    def test_exact_p9_validator_reads_git_blob_over_stdin(self):
        daily = synthetic_daily([
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
        ])
        blob = json.dumps(daily).encode("utf-8")
        checked = {
            "unified": current_unified(),
            "entry_exit": {"packet_sha256": "2" * 64},
            "intraday_risk": {"packet_sha256": "3" * 64},
        }
        completed = subprocess.CompletedProcess(
            [], 0, stdout=json.dumps(checked)
        )
        with mock.patch.object(
            MODULE.DAILY_LINEAGE, "_git_blob", return_value=blob
        ), mock.patch.object(
            MODULE.DAILY_LINEAGE, "_materialize_exact_commit"
        ), mock.patch.object(
            MODULE.subprocess, "run", return_value=completed
        ) as exact_validator:
            result = MODULE._validate_shadow_inputs_at_commit(
                SOURCE_COMMIT, PACKET.relative_to(ROOT).as_posix()
            )
        self.assertEqual(result[0], current_unified())
        self.assertEqual(exact_validator.call_args.kwargs["input"], blob.decode("utf-8"))

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

    def test_existing_ledger_output_requires_exact_prior_ledger_cli_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "existing-ledger.json"
            output.write_text("{}", encoding="utf-8")
            with mock.patch.object(
                sys,
                "argv",
                [
                    str(SOURCE),
                    str(PACKET),
                    "--source-commit", SOURCE_COMMIT,
                    "--recorded-at", RECORDED_AT,
                    "--ledger-out", str(output),
                    "--history-root", str(root / "readiness"),
                ],
            ), mock.patch.object(
                MODULE,
                "build_packet",
                side_effect=AssertionError("must fail before source evaluation"),
            ) as build:
                self.assertEqual(MODULE.main(), 1)
            build.assert_not_called()
            self.assertEqual(output.read_text(encoding="utf-8"), "{}")

    def test_stale_prior_ledger_cannot_replace_existing_ledger_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "existing-ledger.json"
            prior = root / "stale-ledger.json"
            existing = LEDGER_FIXTURE.append(
                LEDGER_FIXTURE.decision(), "2026-08-21T02:15:00Z"
            )
            stale = LEDGER_FIXTURE.MODULE.empty_ledger(LEDGER_FIXTURE.CONTRACT)
            output.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            prior.write_text(json.dumps(stale, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            original = output.read_bytes()
            with mock.patch.object(
                sys,
                "argv",
                [
                    str(SOURCE),
                    str(PACKET),
                    "--source-commit", SOURCE_COMMIT,
                    "--recorded-at", RECORDED_AT,
                    "--ledger", str(prior),
                    "--ledger-out", str(output),
                    "--history-root", str(root / "readiness"),
                ],
            ), mock.patch.object(
                MODULE,
                "build_packet",
                side_effect=AssertionError("must fail before source evaluation"),
            ) as build:
                self.assertEqual(MODULE.main(), 1)
            build.assert_not_called()
            self.assertEqual(output.read_bytes(), original)

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
