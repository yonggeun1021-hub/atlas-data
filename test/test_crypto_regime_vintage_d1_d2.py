#!/usr/bin/env python3
"""W5-01 (D1) and W5-02 (D2) Crypto decision-generation regressions.

D1: the unratified realtime gate ``overall_status == STALE`` must not stop a
scheduler run from writing a decision packet.  MISSING observations,
CONNECTION and DATE_MISMATCH stay WAIT, and the ratified realtime freshness
still caps every actionable state through the unchanged
``cap_state_for_freshness``.

D2: the live component registry looks UTC-keyed source directories up by the
UTC vintage date (schema /2, ``vintage_date_utc``), never by the KST
operational date.  Issued schema /1 records keep revalidating exactly, an
absent source is still absent (no fallback date), and Crypto stays UNKNOWN.
"""
from __future__ import annotations

import ast
import copy
import datetime as dt
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# Fixture builders are reused verbatim from the existing decision-snapshot
# regression, together with the exact CPDS module instance they bind.
HELPERS = _load(
    "crypto_regime_vintage_d1_d2_snapshot_helpers",
    "test/test_crypto_paper_decision_snapshot.py",
)
CPDS = HELPERS.CPDS
UNI = HELPERS.UNI
REGISTRY = _load(
    "crypto_regime_vintage_d1_d2_registry",
    "regime/crypto_live_component_registry.py",
)

EVAL_AS_OF = HELPERS.EVAL_AS_OF
GENERATED_AT = HELPERS.GENERATED_AT
SOURCE_COMMIT = HELPERS.SOURCE_COMMIT
LEGACY_SCHEMA = "crypto_live_component_registry/1"
CURRENT_SCHEMA = "crypto_live_component_registry/2"
ALL_COMPONENTS = {
    "BTC_TREND", "BTC_RISK", "STABLECOIN_NET_ISSUANCE",
    "CRYPTO_BREADTH", "CRYPTO_LEADERSHIP",
}

# sha256 of ``inspect.getsource(cap_state_for_freshness)`` at origin/main
# 7e9aa25e.  W5-01 must leave the downstream action-state cap byte-identical.
CAP_STATE_FOR_FRESHNESS_SOURCE_SHA256 = (
    "9654f04864135fcacc48eb6ae734d9f3a81d26753664fb9e14037f75918fae1f"
)


def rehash(record: dict) -> dict:
    value = copy.deepcopy(record)
    value.pop("payload_sha256", None)
    value["payload_sha256"] = REGISTRY.payload_sha256(value)
    return value


class _TempDir:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cr_d1d2_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for patcher in HELPERS.ratified_policy_patches():
            patcher.start()
            self.addCleanup(patcher.stop)

    def same_day_inputs(self, *, eligible=False):
        row = (
            HELPERS.universe_row(market="KRW-BTC", canonical_asset_id="BTC")
            if eligible
            else HELPERS.universe_row(state=UNI.STATE_OBSERVATION_POOL, canonical_asset_id=None)
        )
        packet = HELPERS.universe_packet([row])
        universe = HELPERS.write_universe_entry(self.tmp, packet)
        market = HELPERS.write_market_evidence_entry(
            self.tmp, {"KRW-BTC": HELPERS.valid_market_evidence_packet("KRW-BTC")},
        )
        return universe, market


# ---------------------------------------------------------------------------
# W5-01 / D1
# ---------------------------------------------------------------------------

class RealtimeStaleIsNotAGenerationBlockerTests(_TempDir, unittest.TestCase):
    def test_vintage_readiness_ignores_unratified_gate_stale(self):
        universe, market = self.same_day_inputs()
        realtime = HELPERS.write_realtime_entry(self.tmp, overall_status="STALE")
        reasons = CPDS.vintage_readiness(
            expected_date=EVAL_AS_OF,
            generated_dt=CPDS._parse_utc(GENERATED_AT, "generated_at"),
            universe_entry=universe,
            market_evidence_entry=market,
            realtime_entry=realtime,
        )
        self.assertFalse(
            [reason for reason in reasons if reason.startswith("UPBIT_REALTIME_NOT_READY")],
            reasons,
        )

    def test_stale_realtime_writes_packet_and_still_caps_action_state(self):
        self.same_day_inputs(eligible=True)
        realtime = HELPERS.write_realtime_entry(self.tmp, overall_status="STALE")
        observed_caps = []
        real_cap = CPDS.cap_state_for_freshness

        def spy(state, reason, overall):
            result = real_cap(state, reason, overall)
            observed_caps.append((state, overall, result))
            return result

        with (
            mock.patch.object(
                CPDS, "_realtime_freshness",
                return_value=(CPDS.STALE, "UPBIT_REALTIME_RATIFIED_POLICY_RESULT_STALE"),
            ),
            mock.patch.object(CPDS, "cap_state_for_freshness", side_effect=spy),
        ):
            result = CPDS.populate(
                generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
                universe_data_root=self.tmp / "universe",
                market_evidence_data_root=self.tmp / "market_evidence",
                realtime_run_path=realtime["path"], output_root=self.tmp / "out",
                expected_vintage_date=EVAL_AS_OF,
            )
        self.assertEqual(result["evaluation_status"], "EVALUATED")
        self.assertEqual(result["outcome"], "populated")
        record = result["record"]
        self.assertEqual(record["freshness_status"]["realtime"], CPDS.STALE)
        self.assertNotEqual(record["freshness_status"]["overall"], CPDS.FRESH)
        self.assertIn(
            "UPBIT_REALTIME_RATIFIED_POLICY_RESULT_STALE", record["derivation_notes"],
        )
        self.assertTrue(observed_caps)
        for _state, overall, capped in observed_caps:
            self.assertNotEqual(overall, CPDS.FRESH)
            self.assertNotIn(capped["state"], CPDS._ACTIONABLE_STATES)
        for candidate in record["candidates"]:
            self.assertNotIn(candidate["state"], CPDS._ACTIONABLE_STATES)

    def test_cap_state_for_freshness_is_unchanged_and_caps_stale(self):
        source = inspect.getsource(CPDS.cap_state_for_freshness)
        self.assertEqual(
            hashlib.sha256(source.encode("utf-8")).hexdigest(),
            CAP_STATE_FOR_FRESHNESS_SOURCE_SHA256,
        )
        for state in CPDS._ACTIONABLE_STATES:
            capped = CPDS.cap_state_for_freshness(state, "ALL_CRITERIA_PASSED", CPDS.STALE)
            self.assertEqual(capped["state"], "WAIT")
            self.assertEqual(capped["reason"], "OVERALL_FRESHNESS_NOT_FRESH:STALE")
            self.assertTrue(capped["capped"])


class RealtimeHardWaitReasonsRemainTests(_TempDir, unittest.TestCase):
    def _reasons(self, realtime):
        universe, market = self.same_day_inputs()
        return CPDS.vintage_readiness(
            expected_date=EVAL_AS_OF,
            generated_dt=CPDS._parse_utc(GENERATED_AT, "generated_at"),
            universe_entry=universe,
            market_evidence_entry=market,
            realtime_entry=realtime,
        )

    def _populate(self, realtime_path):
        return CPDS.populate(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_data_root=self.tmp / "universe",
            market_evidence_data_root=self.tmp / "market_evidence",
            realtime_run_path=realtime_path, output_root=self.tmp / "out",
            expected_vintage_date=EVAL_AS_OF,
        )

    def test_missing_realtime_run_waits(self):
        self.assertIn("UPBIT_REALTIME_NOT_READY:MISSING", self._reasons(None))
        result = self._populate(self.tmp / "realtime" / EVAL_AS_OF / "run_999.json")
        self.assertEqual(result["evaluation_status"], "NOT_EVALUATED")
        self.assertEqual(result["decision_state"], "WAIT")
        self.assertIn("UPBIT_REALTIME_NOT_READY:MISSING", result["reason"])
        self.assertFalse(list((self.tmp / "out").rglob("packet.json")))

    def test_missing_observations_wait_even_when_gate_reports_stale(self):
        realtime = HELPERS.write_realtime_entry(
            self.tmp, with_observations=False, overall_status="STALE",
        )
        reasons = self._reasons(realtime)
        self.assertIn("UPBIT_REALTIME_NOT_READY:MISSING_OBSERVATIONS", reasons)
        result = self._populate(realtime["path"])
        self.assertEqual(result["evaluation_status"], "NOT_EVALUATED")
        self.assertIn("UPBIT_REALTIME_NOT_READY:MISSING_OBSERVATIONS", result["reason"])
        self.assertNotIn("UPBIT_REALTIME_NOT_READY:STALE", result["reason"])

    def test_connection_not_connected_waits(self):
        realtime = HELPERS.write_realtime_entry(self.tmp, overall_status="STALE")
        status = realtime["record"]["run"]["status"]
        status["connection_state"] = "RECONNECTING"
        status["payload_sha256"] = CPDS.payload_sha256(
            {key: value for key, value in status.items() if key != "payload_sha256"}
        )
        realtime["record"]["source_sha256"] = CPDS.payload_sha256(realtime["record"]["run"])
        realtime["path"].write_text(json.dumps(realtime["record"]), encoding="utf-8")
        reasons = self._reasons(realtime)
        self.assertIn("UPBIT_REALTIME_NOT_READY:CONNECTION_RECONNECTING", reasons)
        result = self._populate(realtime["path"])
        self.assertEqual(result["evaluation_status"], "NOT_EVALUATED")
        self.assertIn("UPBIT_REALTIME_NOT_READY:CONNECTION_RECONNECTING", result["reason"])

    def test_realtime_date_mismatch_waits(self):
        realtime = HELPERS.write_realtime_entry(
            self.tmp, date="2026-08-28", overall_status="FRESH",
        )
        reasons = self._reasons(realtime)
        self.assertIn(
            f"UPBIT_REALTIME_NOT_READY:DATE_MISMATCH:expected={EVAL_AS_OF}:actual=2026-08-28",
            reasons,
        )
        result = self._populate(realtime["path"])
        self.assertEqual(result["evaluation_status"], "NOT_EVALUATED")
        self.assertIn("UPBIT_REALTIME_NOT_READY:DATE_MISMATCH", result["reason"])


class RealtimeVintageGateIsCryptoScopedTests(unittest.TestCase):
    def test_vintage_gate_is_reachable_only_from_the_crypto_producer(self):
        callers = []
        for path in sorted(ROOT.rglob("*.py")):
            relative = path.relative_to(ROOT)
            if relative.parts[0] in {"test", "evidence", "data", ".git"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "vintage_readiness(" in text or "UPBIT_REALTIME_NOT_READY" in text:
                callers.append(relative.as_posix())
        self.assertEqual(callers, ["decision/crypto_paper_decision_snapshot.py"])

        flag_users = []
        for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            if "--expected-vintage-date" in workflow.read_text(encoding="utf-8"):
                flag_users.append(workflow.name)
        self.assertEqual(flag_users, ["upbit-realtime-capture.yml"])


class NaturalStaleRealtimeReplayTests(unittest.TestCase):
    """Replay committed natural inputs that were NOT_EVALUATED on main.

    2026-09-12 run_031 (ended 17:51:20Z = 02:51 KST on 09-13) carries a gate
    ``overall_status`` of STALE.  Before W5-01 the scheduler wrote no packet
    (``UPBIT_REALTIME_NOT_READY:STALE``); before W5-02 the registry asked for
    the KST 2026-09-13 directories and wired 0 components.  Inputs dated
    after the replay day are excluded by copying only that day's universe,
    market evidence and prior leadership packets, exactly as they existed.
    """

    GENERATED_AT = "2026-09-12T17:52:00Z"
    DAY = "2026-09-12"
    RUN = ROOT / "evidence/crypto/upbit/realtime/2026-09-12/run_031.json"

    def test_combined_d1_d2_replay_writes_four_axis_packet_capped_by_stale(self):
        record = json.loads(self.RUN.read_text(encoding="utf-8"))
        self.assertEqual(record["run"]["status"]["overall_status"], "STALE")
        self.assertEqual(record["run"]["status"]["connection_state"], "CONNECTED")
        with tempfile.TemporaryDirectory(prefix="cr_d1d2_natural_") as tmp:
            tmp = Path(tmp)
            shutil.copytree(CPDS.UNIVERSE_DATA_ROOT / self.DAY, tmp / "universe" / self.DAY)
            for child in CPDS.MARKET_EVIDENCE_DATA_ROOT.iterdir():
                if child.name.startswith(self.DAY + "-"):
                    shutil.copytree(child, tmp / "market_evidence" / child.name)
            for child in CPDS.LEADERSHIP_DATA_ROOT.iterdir():
                if child.is_dir() and child.name < self.DAY:
                    shutil.copytree(child, tmp / "leadership" / child.name)
            result = CPDS.populate(
                generated_at=self.GENERATED_AT, source_commit=SOURCE_COMMIT,
                universe_data_root=tmp / "universe",
                market_evidence_data_root=tmp / "market_evidence",
                leadership_data_root=tmp / "leadership",
                realtime_run_path=self.RUN, output_root=tmp / "out",
                expected_vintage_date=self.DAY,
                wire_regime_components=True,
            )
        self.assertEqual(result["evaluation_status"], "EVALUATED", result["reason"])
        packet = result["record"]
        self.assertEqual(packet["freshness_status"]["realtime"], CPDS.STALE)
        self.assertNotEqual(packet["freshness_status"]["overall"], CPDS.FRESH)
        registry = packet["source_components"]
        self.assertEqual(registry["schema_version"], CURRENT_SCHEMA)
        self.assertEqual(registry["vintage_date_utc"], self.DAY)
        self.assertNotIn("operational_date_kst", registry)
        defined = {
            axis for axis, row in packet["crypto_regime_five_axis"].items()
            if row["status"] == "DEFINED"
        }
        self.assertEqual(defined, {"TREND", "BREADTH", "RISK_VOL", "LIQUIDITY"})
        self.assertEqual(
            packet["crypto_regime_five_axis"]["LEADERSHIP"]["status"], "UNDEFINED",
        )
        for candidate in packet["candidates"]:
            self.assertNotIn(candidate["state"], CPDS._ACTIONABLE_STATES)


# ---------------------------------------------------------------------------
# W5-02 / D2
# ---------------------------------------------------------------------------

class RegistryUtcVintageBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = REGISTRY.load_contract()
        cls.legacy_profile = REGISTRY._schema_profile(cls.contract, LEGACY_SCHEMA)
        # 23:59:59 KST on 2026-09-12: both date bases agree.
        cls.daytime = REGISTRY.build_registry("2026-09-12T14:59:59Z")

    def test_contract_bumps_schema_and_binds_utc_vintage(self):
        self.assertEqual(self.contract["schema_version"], 2)
        self.assertEqual(
            self.contract["contract_version"], "crypto_live_component_registry_contract/2",
        )
        self.assertEqual(self.contract["output_schema_version"], CURRENT_SCHEMA)
        self.assertEqual(self.contract["lookup_date_basis"], "UTC_VINTAGE_DATE")
        self.assertEqual(self.contract["lookup_date_field"], "vintage_date_utc")
        self.assertEqual(
            self.contract["legacy_output_schemas"][LEGACY_SCHEMA]["lookup_date_basis"],
            "KST_OPERATIONAL_DATE",
        )
        self.assertEqual(self.daytime["schema_version"], CURRENT_SCHEMA)
        self.assertEqual(self.daytime["vintage_date_utc"], "2026-09-12")
        self.assertEqual(set(self.daytime["rows"]), ALL_COMPONENTS)

    def test_kst_early_morning_generations_keep_same_utc_day_components(self):
        # 00:00, 02:52 and 08:59:59 KST on 2026-09-13 are all UTC 2026-09-12.
        for generated_at in (
            "2026-09-12T15:00:00Z", "2026-09-12T17:52:00Z", "2026-09-12T23:59:59Z",
        ):
            with self.subTest(generated_at=generated_at):
                record = REGISTRY.build_registry(generated_at)
                self.assertEqual(record["vintage_date_utc"], "2026-09-12")
                self.assertEqual(set(record["rows"]), set(self.daytime["rows"]))
                self.assertEqual(
                    record["source_directories"], self.daytime["source_directories"],
                )
                self.assertEqual(
                    REGISTRY.validate_registry(
                        copy.deepcopy(record), expected_generated_at=generated_at,
                    ),
                    record,
                )
                legacy = REGISTRY._assemble(
                    generated_at, self.contract, root=REGISTRY.ROOT,
                    profile=self.legacy_profile,
                )
                # The defect being fixed: the KST lookup found nothing.
                self.assertEqual(legacy["operational_date_kst"], "2026-09-13")
                self.assertEqual(legacy["rows"], {})

    def test_absent_same_utc_day_sources_fail_closed_without_fallback(self):
        # 09:45 KST on 2026-09-13: only the 00:38Z BTC capture exists for the
        # UTC vintage; breadth lands at 00:52Z and stablecoin after 06:20Z.
        record = REGISTRY.build_registry("2026-09-13T00:45:00Z")
        self.assertEqual(record["vintage_date_utc"], "2026-09-13")
        self.assertEqual(set(record["rows"]), {"BTC_TREND", "BTC_RISK"})
        for source in record["source_directories"]:
            self.assertTrue(source["path"].endswith("/2026-09-13"), source["path"])
        # 09:00 KST: nothing for the UTC vintage yet, and no 2026-09-12 carry.
        empty = REGISTRY.build_registry("2026-09-13T00:00:00Z")
        self.assertEqual(empty["rows"], {})
        self.assertEqual(empty["source_directories"], [])

    def test_removed_source_directory_is_absent_not_substituted(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".cr-d1d2-observation-") as tmp:
            observation_root = Path(tmp)
            for source in self.daytime["source_directories"]:
                if source["path"].startswith("evidence/stablecoin/"):
                    continue
                target = observation_root / source["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(ROOT / source["path"], target)
            # An older stablecoin capture is present but must not be used.
            older = "evidence/stablecoin/raw/2026-09-11"
            shutil.copytree(ROOT / older, observation_root / older)
            record = REGISTRY.build_registry("2026-09-12T17:52:00Z", root=observation_root)
        self.assertNotIn("STABLECOIN_NET_ISSUANCE", record["rows"])
        self.assertEqual(
            set(record["rows"]), ALL_COMPONENTS - {"STABLECOIN_NET_ISSUANCE"},
        )
        self.assertFalse(
            any(source["path"].startswith("evidence/stablecoin/")
                for source in record["source_directories"])
        )

    def test_record_identity_forgeries_fail_closed(self):
        generated_at = "2026-09-12T14:59:59Z"
        renamed_back = copy.deepcopy(self.daytime)
        renamed_back["operational_date_kst"] = renamed_back.pop("vintage_date_utc")
        with self.assertRaisesRegex(
            REGISTRY.CryptoLiveComponentRegistryError, "REGISTRY_FIELDS_MISMATCH",
        ):
            REGISTRY.validate_registry(rehash(renamed_back), expected_generated_at=generated_at)

        wrong_date = copy.deepcopy(self.daytime)
        wrong_date["vintage_date_utc"] = "2026-09-13"
        with self.assertRaisesRegex(
            REGISTRY.CryptoLiveComponentRegistryError, "REGISTRY_IDENTITY_INVALID",
        ):
            REGISTRY.validate_registry(rehash(wrong_date), expected_generated_at=generated_at)

        wrong_contract = copy.deepcopy(self.daytime)
        wrong_contract["contract_version"] = "crypto_live_component_registry_contract/1"
        with self.assertRaisesRegex(
            REGISTRY.CryptoLiveComponentRegistryError, "REGISTRY_IDENTITY_INVALID",
        ):
            REGISTRY.validate_registry(rehash(wrong_contract), expected_generated_at=generated_at)

        unknown = copy.deepcopy(self.daytime)
        unknown["schema_version"] = "crypto_live_component_registry/3"
        with self.assertRaisesRegex(
            REGISTRY.CryptoLiveComponentRegistryError, "REGISTRY_IDENTITY_INVALID",
        ):
            REGISTRY.validate_registry(rehash(unknown), expected_generated_at=generated_at)

    def test_legacy_record_relabelled_as_current_schema_fails_rederivation(self):
        generated_at = "2026-09-12T17:52:00Z"
        legacy = REGISTRY._assemble(
            generated_at, self.contract, root=REGISTRY.ROOT, profile=self.legacy_profile,
        )
        self.assertEqual(
            REGISTRY.validate_registry(copy.deepcopy(legacy), expected_generated_at=generated_at),
            legacy,
        )
        forged = copy.deepcopy(legacy)
        forged["schema_version"] = CURRENT_SCHEMA
        forged["contract_version"] = self.contract["contract_version"]
        forged["vintage_date_utc"] = "2026-09-12"
        forged.pop("operational_date_kst")
        with self.assertRaisesRegex(
            REGISTRY.CryptoLiveComponentRegistryError, "REGISTRY_DERIVATION_MISMATCH",
        ):
            REGISTRY.validate_registry(rehash(forged), expected_generated_at=generated_at)


class IssuedLegacyPacketsKeepRevalidatingTests(unittest.TestCase):
    def test_committed_schema_v1_decision_packets_still_reproduce(self):
        packets = [
            next((ROOT / "evidence/crypto_paper_decision/2026-09-13/0022").glob("*/packet.json")),
            next((ROOT / "evidence/crypto_paper_decision/2026-09-13/1447").glob("*/packet.json")),
        ]
        for path in packets:
            with self.subTest(packet=path.relative_to(ROOT).as_posix()):
                packet = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(packet["source_components"]["schema_version"], LEGACY_SCHEMA)
                self.assertEqual(
                    CPDS.validate_output(copy.deepcopy(packet), allow_external_sources=True),
                    packet,
                )


class CryptoRemainsUnknownTests(unittest.TestCase):
    def test_restored_components_do_not_create_a_crypto_regime(self):
        generated_at = "2026-09-12T17:52:00Z"
        record = REGISTRY.build_registry(generated_at)
        regime = CPDS.build_regime_snapshot(generated_at, record["rows"])
        self.assertEqual(regime["market"], "CRYPTO")
        self.assertEqual(regime["regime"], "UNKNOWN")

    def test_crypto_runtime_and_normalization_stay_unratified(self):
        output_contract = json.loads(
            (ROOT / "config/regime_output_contract.json").read_text(encoding="utf-8")
        )
        self.assertEqual(output_contract["runtime_authorized_regimes"], ["UNKNOWN"])
        normalization = json.loads(
            (ROOT / "config/paper_runtime_normalization_v1.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("CRYPTO", normalization["ratified_markets"])
        self.assertIn("CRYPTO", normalization["unratified_markets"])

    def test_registry_module_stays_evidence_only(self):
        tree = ast.parse(
            (ROOT / "regime/crypto_live_component_registry.py").read_text(encoding="utf-8")
        )
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess"):
            self.assertNotIn(prohibited, imports)
        authority = REGISTRY.load_contract()["authority"]
        for key, value in authority.items():
            if key != "evidence_registry_only":
                self.assertIs(value, False, key)


if __name__ == "__main__":
    unittest.main()
