#!/usr/bin/env python3
"""Rule registry v1 + decision lineage (rule_refs / rule_lineage_event/1).

Offline only.  Reads committed registry, authority records and committed
producer packets; tampering happens in temporary copies.
"""
from __future__ import annotations

import contextlib
import copy
import glob
import io
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance import rule_registry as REG  # noqa: E402
from governance import rule_refs as REFS  # noqa: E402
from governance import rule_lineage_producers as LIN  # noqa: E402


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CPDS = _load("rule_lineage_test_crypto_snapshot", "decision/crypto_paper_decision_snapshot.py")
PRR = _load("rule_lineage_test_paper_reference", "regime/paper_regime_reference.py")
COVERAGE = _load("rule_lineage_test_sidecar_coverage", ".github/scripts/check_rule_lineage_sidecar_coverage.py")

# Hashes of the CIO workspace originals (current bytes, after CIO timestamp
# corrections where a correction_note says so).
ORIGINAL_RECORD_SHA256 = {
    "USER_RATIFICATION_PAPER_MARKET_ALLOCATION_V2_20260913.json": "345801ab907f75c4761097670430fb097e5e8d3b1e595217850fe20fd240a4c8",
    "USER_RATIFICATION_PAPER_ACTIVE_INVERSE_HEDGE_20260913.json": "b24b38a34aa2ed34d98bf8ade6c1334268034e4e4d5718bac8450970ed5577f2",
    "CLAUDE_CIO_ADDENDUM_ALLOCATION_V2_HEDGE_20260914.json": "e5fdda49179cc4a0848a7dac5c377d3428f8371e9046be22f952457776619ed8",
    "USER_RATIFICATION_PAPER_LIQUIDITY_KR_US_20260914.json": "1e068439c4e43050072e38c7450dea38baccf701dab23e784467c9ce48ef40d6",
    "USER_RATIFICATION_CRYPTO_PAPER_RUNTIME_V1_20260914.json": "e2f9f69461088d52300258ab22f17d7f287bd7c2fd6efd1d49962b44f16fffe1",
    "CLAUDE_CIO_ADDENDUM_CRYPTO_RUNTIME_V1_INTERPRETATIONS_20260914.json": "bcff879d8814fe0d457a802392c1bfdee4f85040c718e7ea5a404f06a3481122",
    "USER_RATIFICATION_CRYPTO_REALTIME_FRESHNESS_PER_MARKET_20260914.json": "043932a4ff13e9bd683c8ff233bad3b5c8e8e2cb62045a1e756e27c7deba5ac4",
    "USER_RATIFICATION_US_SESSION_CALENDAR_SOURCE_20260914.json": "50259dafb000c6027dd44193fa1f38d8b65e9e33661a909df54d34e9443f318e",
    "USER_RATIFICATION_CRYPTO_BREADTH_TAXONOMY_ADDITIONS_20260914.json": "6ff7f4865db1dde6f61d40ada5c4971ef46f547f30bdab9f635415f0e6e8e931",
    "USER_RATIFICATION_CAPITAL_ROTATION_RULES_V1_20260915.json": "c6f5dbbe36f3eabc104db9c547ba99d84300fd5b7ef4d801a71c76a071b47116",
    "USER_RATIFICATION_RULE_GOVERNANCE_EVIDENCE_GATED_ADJUSTMENT_20260915.json": "4e08b945238badfae5f28ad412aa1f7d01a85ea4a0c6e508554a29521877cbcc",
    "USER_RATIFICATION_PAPER_ENTRY_BASELINE_B_20260915.json": "b2a905c4eaf23d44749d3e5bcd59b2efe34ff0b0ab5c955a8ce1e0870163154f",
    "USER_RATIFICATION_PAPER_B2_B3_SIZE_ASSEMBLY_20260915.json": "6ffeb7001662f32c32125db054919f8213e0be9ec32a82ec6b7b8cbf6d5077a5",
    "USER_RATIFICATION_PAPER_SESSION_SIZE_WORDING_CORRECTION_20260915.json": "9af25a3b210047aab033cf9398a0b254f269070925893407d2200805492ad9c4",
    "USER_RATIFICATION_PAPER_DATA_FAILURE_RISK_REDUCTION_PRIORITY_C_20260915.json": "3d07cbf1fbba35caaed032b7d3d52cec78e804ad6b415ed7e240191b4f45d1f6",
    "USER_RATIFICATION_PAPER_EXIT_PROVISIONAL_V1_20260915.json": "47276abe432102c33b208a5c3a5d10b30c3c30c79fcb809bb1283c97efb95619",
    "USER_RATIFICATION_PAPER_EXECUTION_CONTRACT_D1_D3_D5_D11_20260915.json": "10de02bf98fd4e5776ed77c09daad36de914e03942675cbe960c121e5dbd668c",
    "USER_RATIFICATION_US_LIQUIDITY_SIP_SOURCE_20260915.json": "6631506766c56793087a9f38360050e28148a40b47871a77514417aef61d3ca2",
    "USER_RATIFICATION_ROTATION_INTERPRETATION_OBSERVATION_GAP_20260915.json": "ed2ca92d9b9cfe6b2e912c686f874c62f664fe25b0b20814264a853366c2487a",
    "USER_RATIFICATION_PAPER_BUILD_PLAN_P1_P6_20260915.json": "2a94be2b593ed49a61e38cecfc2c992802ffa8102b292bf40bd964e7391d5fdd",
    "USER_RATIFICATION_ROTATION_MAX_OBSERVATION_GAP_20260915.json": "d65f58c60eb7b78f5e8fa2e054497e17903cf290a517b5b9a246e0419b199903",
}
# Hashes recorded before the CIO timestamp corrections; the corrected files name
# them in correction_note, so later records that cite them still resolve.
PRE_CORRECTION_SHA256 = {
    "USER_RATIFICATION_PAPER_B2_B3_SIZE_ASSEMBLY_20260915.json": "0e2691e072f4193b6fcd07c14cf2c87be469c4acb9167eca0cbd5d72a390e1c5",
    "USER_RATIFICATION_RULE_GOVERNANCE_EVIDENCE_GATED_ADJUSTMENT_20260915.json": "c3f1e78ca987760af205807f67e9b56e8e7bd0078cbb87ac566b49767a855f9b",
}
PENDING_IDS = set()  # the SIP source ratification resolved the last one
UNDECIDED_IDS = {
    "RULE.SIZE.PLANNED_LOSS_CAP.PENDING", "RULE.EXIT.PENDING", "RULE.EXECUTION.QUALITY_NUMBERS.PENDING",
    "RULE.CRYPTO.BTC_ETH_NAME_CAP.PENDING", "RULE.US.LIQUIDITY_IEX_TREATMENT.PENDING",
}
SECRET_LIKE = re.compile(
    r"(ghp_[A-Za-z0-9]{20,}|github_pat_|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY|"
    r"\"(api_key|apikey|access_token|secret_key|password)\"\s*:\s*\"[^\"]+\")",
    re.IGNORECASE,
)


def _registry() -> dict:
    return json.loads(REG.REGISTRY_PATH.read_text(encoding="utf-8"))


def _row(registry: dict, rule_id: str) -> dict:
    return next(row for row in registry["rules"] if row["rule_id"] == rule_id)


class TmpRootCase(unittest.TestCase):
    """A minimal root holding the registry's source records, bindings and pins."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        registry = _registry()
        paths = {s["repo_path"] for row in registry["rules"] for s in row["source_records"]}
        paths |= {b["path"] for row in registry["rules"] for b in row["implementation_bindings"]}
        paths.add(REG.PINS_RELATIVE_PATH)
        for relative in paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        self.registry = registry

    def tearDown(self):
        self._tmp.cleanup()

    def assertInvalid(self, registry: dict, code: str):
        with self.assertRaises(REG.RuleRegistryError) as ctx:
            REG.validate_registry(registry, self.root)
        self.assertTrue(str(ctx.exception).startswith(code), str(ctx.exception))


class CommittedRegistryTests(unittest.TestCase):
    def test_committed_registry_is_valid_with_the_fixed_id_set(self):
        registry = REG.load_registry()
        ids = [row["rule_id"] for row in registry["rules"]]
        self.assertEqual(sorted(ids), sorted(REG.REQUIRED_RULE_IDS))
        self.assertEqual(len(ids), 53)
        status = {row["rule_id"]: row["status"] for row in registry["rules"]}
        self.assertEqual({k for k, v in status.items() if v == "PENDING_USER_DECISION"}, PENDING_IDS)
        self.assertEqual({k for k, v in status.items() if v == "RESOLVED"}, UNDECIDED_IDS - PENDING_IDS)
        self.assertEqual({k for k, v in status.items() if v == "SUPERSEDED"}, {"RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V1"})
        # Only the held-position part of release handling was replaced; the
        # new-buy stop keeps the rotation record's RATIFIED status.
        self.assertEqual(status["RULE.ROTATION.RELEASE_HANDLING.V1"], "RATIFIED")
        self.assertEqual({k for k, v in status.items() if v == "PROVISIONAL"},
                         {"RULE.ROTATION.US.V1P", "RULE.EXIT.RELEASE_FULL_SELL.V1", "RULE.EXIT.CRYPTO_TIME_STOP_21D.V1"})
        self.assertEqual({k for k, v in status.items() if v == "TEMPORARY"}, {"RULE.ROTATION.KR.V1T"})

    def test_source_records_are_byte_exact_copies_of_the_named_originals(self):
        registry = _registry()
        seen = {}
        for row in registry["rules"]:
            for source in row["source_records"]:
                raw = (ROOT / source["repo_path"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), source["sha256"])
                seen[source["original_filename"]] = source["sha256"]
        for name, sha in ORIGINAL_RECORD_SHA256.items():
            self.assertEqual(seen.get(name), sha, name)

    def test_corrected_records_name_their_previous_hash(self):
        registry = _registry()
        by_name = {s["original_filename"]: s for row in registry["rules"] for s in row["source_records"]}
        for name, previous in PRE_CORRECTION_SHA256.items():
            source = by_name[name]
            raw = (ROOT / source["repo_path"]).read_bytes()
            self.assertIn(previous, REG.record_identities(raw, json.loads(raw)))
        rotation = _row(registry, "RULE.ROTATION.CRYPTO.V1")["source_records"][0]
        self.assertTrue(rotation["sha256"].startswith("c6f5dbbe"))
        # An unlisted hash is not an identity, and neither is a hash prefix.
        b2 = by_name["USER_RATIFICATION_PAPER_B2_B3_SIZE_ASSEMBLY_20260915.json"]
        raw = (ROOT / b2["repo_path"]).read_bytes()
        identities = REG.record_identities(raw, json.loads(raw))
        self.assertEqual(identities, {b2["sha256"], PRE_CORRECTION_SHA256[b2["original_filename"]]})
        self.assertNotIn(PRE_CORRECTION_SHA256["USER_RATIFICATION_RULE_GOVERNANCE_EVIDENCE_GATED_ADJUSTMENT_20260915.json"], identities)
        self.assertEqual(REG.record_identities(b"{}", {"correction_note": "previous file sha256 " + "a" * 16}), {hashlib.sha256(b"{}").hexdigest()})
        self.assertEqual(REG.record_identities(b"{}", {"correction_note": "previous file sha256 " + "b" * 65}), {hashlib.sha256(b"{}").hexdigest()})

    def test_effective_times_follow_corrected_records(self):
        registry = _registry()
        expected = {
            "RULE.GOVERNANCE.EVIDENCE_GATED.V1": "2026-09-14T15:18:00Z",
            "RULE.ENTRY.PAPER_BASELINE_B.V1": "2026-09-14T22:10:00Z",
            "RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V1": "2026-09-14T22:22:00Z",
            "RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V2": "2026-09-14T22:51:00Z",
            "RULE.EXEC.DATA_FAILURE_PRIORITY.V1": "2026-09-14T22:52:00Z",
            "RULE.EXIT.RELEASE_FULL_SELL.V1": "2026-09-14T22:57:00Z",
            "RULE.EXEC.TIME_CONTRACT.V1": "2026-09-14T23:01:00Z",
            "RULE.LIQUIDITY.US_SIP_SOURCE.V1": "2026-09-14T23:06:00Z",
            "RULE.ROTATION.INTERPRETATION_OBSERVATION_GAP.V1": "2026-09-14T23:13:00Z",
        }
        for rule_id, utc in expected.items():
            self.assertEqual(_row(registry, rule_id)["effective_from"]["utc"], utc, rule_id)

    def test_copied_authority_records_contain_no_secret_like_values(self):
        for row in _registry()["rules"]:
            for source in row["source_records"]:
                text = (ROOT / source["repo_path"]).read_text(encoding="utf-8")
                self.assertIsNone(SECRET_LIKE.search(text), source["repo_path"])

    def test_triggers_only_where_records_state_them(self):
        registry = _registry()
        with_triggers = {row["rule_id"] for row in registry["rules"] if row["review_triggers"]}
        self.assertEqual(with_triggers, {
            "RULE.HEDGE.INVERSE.V1", "RULE.CRYPTO.RUNTIME.V1", "RULE.ROTATION.CRYPTO.V1",
            "RULE.ROTATION.US.V1P", "RULE.ROTATION.KR.V1T",
        })
        for row in registry["rules"]:
            self.assertEqual(row["review_triggers"] is None, row["trigger_pending_user_confirmation"])
        samples = {row["rule_id"]: row["minimum_sample"]["value"] for row in registry["rules"] if row["minimum_sample"]}
        self.assertEqual(samples, {"RULE.ROTATION.CRYPTO.V1": 10})

    def test_undecided_rows_carry_no_decision_and_cannot_be_cited(self):
        registry = _registry()
        ctx = REFS.RegistryContext.load()
        for rule_id in UNDECIDED_IDS:
            row = _row(registry, rule_id)
            self.assertEqual((row["version"], row["key_parameters"], row["effective_from"]), (0, {}, None))
            self.assertTrue(row["pending_basis"])
            with self.assertRaises(REFS.RuleLineageError):
                REFS.make_rule_ref(ctx, rule_id, "APPLIED")
        resolvers = {row["rule_id"]: sorted(r["rule_id"] for r in row["resolved_by"])
                     for row in registry["rules"] if row["status"] == "RESOLVED"}
        self.assertEqual(resolvers, {
            "RULE.SIZE.PLANNED_LOSS_CAP.PENDING": ["RULE.RISK.PLANNED_LOSS_RECORD_ONLY.V1"],
            "RULE.EXIT.PENDING": ["RULE.EXIT.CRYPTO_TIME_STOP_21D.V1", "RULE.EXIT.RELEASE_FULL_SELL.V1",
                                  "RULE.EXIT.SHADOW_CONTROLS.V1"],
            "RULE.CRYPTO.BTC_ETH_NAME_CAP.PENDING": ["RULE.SIZE.BTC_ETH_PER_NAME_CAP.V1"],
            "RULE.EXECUTION.QUALITY_NUMBERS.PENDING": ["RULE.EXEC.QUALITY_LAYERS.V1", "RULE.EXEC.TIME_CONTRACT.V1"],
            "RULE.US.LIQUIDITY_IEX_TREATMENT.PENDING": ["RULE.LIQUIDITY.US_SIP_SOURCE.V1"],
        })

    def test_supersession_windows(self):
        registry = _registry()
        v1 = _row(registry, "RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V1")
        v2 = _row(registry, "RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V2")
        self.assertEqual((v1["version"], v2["version"]), (1, 2))
        self.assertEqual(v1["superseded_by"]["sha256"], v2["source_records"][0]["sha256"])
        self.assertEqual(v2["supersedes"]["sha256"], v1["source_records"][0]["sha256"])
        self.assertTrue(REG.in_force_at(v1, "2026-09-14T22:22:00Z", registry))
        self.assertFalse(REG.in_force_at(v1, "2026-09-14T22:51:00Z", registry))
        self.assertTrue(REG.in_force_at(v2, "2026-09-14T22:51:00Z", registry))
        self.assertFalse(REG.in_force_at(v2, "2026-09-14T22:50:59Z", registry))
        release = _row(registry, "RULE.ROTATION.RELEASE_HANDLING.V1")
        full_sell = _row(registry, "RULE.EXIT.RELEASE_FULL_SELL.V1")
        self.assertIsNone(release["superseded_by"])
        # Every key parameter still carrying the old held-position wording
        # (the parsed part, the full rules text and the verbatim sentence) is
        # a superseded part; only the parsed new-buy stop stays in force.
        pointer = {"rule_id": full_sell["rule_id"], "record_id": full_sell["source_records"][0]["record_id"],
                   "sha256": full_sell["source_records"][0]["sha256"]}
        self.assertEqual(release["superseded_parts"],
                         [{"key_parameter": key, "superseded_by": pointer}
                          for key in ("held_positions", "rules_text", "user_sentence", "on_release_new_buys")])
        self.assertEqual([p["key_parameter"] for p in full_sell["supersedes_parts"]],
                         ["held_positions", "rules_text", "user_sentence", "on_release_new_buys"])
        # "stop new buys ONLY" is superseded in part; the stop itself stays in force.
        self.assertFalse(REG.part_in_force_at(release, "on_release_new_buys", "2026-09-14T22:57:00Z", registry))
        self.assertEqual(release["key_parameters"]["on_release_new_buys_stop"]["value"], "STOP_NEW_BUYS")
        for key in ("rules_text", "user_sentence"):
            self.assertIn("손절·익절" if key == "user_sentence" else "stop-loss/take-profit",
                          release["key_parameters"][key]["text"] or release["key_parameters"][key]["value"])
            self.assertTrue(REG.part_in_force_at(release, key, "2026-09-14T22:56:59Z", registry))
            self.assertFalse(REG.part_in_force_at(release, key, "2026-09-14T22:57:00Z", registry))
        in_force_after = sorted(k for k in release["key_parameters"]
                                if REG.part_in_force_at(release, k, "2026-09-15T12:00:00Z", registry))
        for key in in_force_after:
            blob = json.dumps(release["key_parameters"][key], ensure_ascii=False)
            self.assertNotIn("stop-loss/take-profit", blob)
            self.assertNotIn("손절·익절", blob)
        self.assertIn("on_release_new_buys_stop", in_force_after)
        self.assertNotIn("on_release_new_buys", in_force_after)
        self.assertIsNone(full_sell["supersedes"])
        self.assertTrue(REG.in_force_at(release, "2026-09-15T12:00:00Z", registry))
        self.assertTrue(REG.part_in_force_at(release, "on_release_new_buys_stop", "2026-09-15T12:00:00Z", registry))
        self.assertTrue(REG.part_in_force_at(release, "held_positions", "2026-09-14T22:56:59Z", registry))
        self.assertFalse(REG.part_in_force_at(release, "held_positions", "2026-09-14T22:57:00Z", registry))
        self.assertEqual(release["key_parameters"]["on_release_new_buys"]["value"], "STOP_NEW_BUYS_ONLY")
        ctx = REFS.RegistryContext.load()
        self.assertEqual(REFS.make_rule_ref(ctx, v1["rule_id"], "SIZED_BY")["version"], 1)

    def test_build_plan_p1_p6_and_max_gap_rows(self):
        registry = _registry()
        p16 = ORIGINAL_RECORD_SHA256["USER_RATIFICATION_PAPER_BUILD_PLAN_P1_P6_20260915.json"]
        gap_sha = ORIGINAL_RECORD_SHA256["USER_RATIFICATION_ROTATION_MAX_OBSERVATION_GAP_20260915.json"]
        for rule_id in ("RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1", "RULE.NAV.KRW_USD_CONVERSION_FRED_DEXKOUS.V1",
                        "RULE.EXIT.SHADOW_CONTROLS_KR_US_UNITS.V1", "RULE.UNIVERSE.US_STOCK_SPDR_SECTOR_MAPPING.V1",
                        "RULE.HEDGE.KR_STRESS_UNRATIFIED_INTERIM.V1", "RULE.GOVERNANCE.COOLING_OFF.V1"):
            row = _row(registry, rule_id)
            self.assertEqual((row["status"], row["source_records"][0]["sha256"]), ("RATIFIED", p16))
            self.assertEqual(row["effective_from"]["utc"], "2026-09-15T00:27:55Z")
        nav = _row(registry, "RULE.NAV.KRW_USD_CONVERSION_FRED_DEXKOUS.V1")["key_parameters"]
        self.assertEqual(nav["staleness"]["value"]["max_business_days_without_new_value"], 10)
        self.assertEqual(nav["rate_source"]["value"]["series"], "FRED:DEXKOUS")
        cool = _row(registry, "RULE.GOVERNANCE.COOLING_OFF.V1")
        self.assertEqual(cool["key_parameters"]["calendar_minimum"]["value"]["CRYPTO"], {"value": 30, "unit": "DAYS"})
        gov = _row(registry, "RULE.GOVERNANCE.EVIDENCE_GATED.V1")
        self.assertEqual(gov["amended_by"], [{"rule_id": cool["rule_id"], "sha256": p16}])
        self.assertTrue(REG.part_in_force_at(gov, "cooling_off_period", "2026-09-15T00:27:54Z", registry))
        self.assertFalse(REG.part_in_force_at(gov, "cooling_off_period", "2026-09-15T00:27:55Z", registry))
        self.assertTrue(REG.part_in_force_at(gov, "judge_only_after_minimum_sample", "2026-09-16T00:00:00Z", registry))
        gap = _row(registry, "RULE.ROTATION.MAX_OBSERVATION_GAP.V1")
        self.assertEqual((gap["source_records"][0]["sha256"], gap["effective_from"]["utc"]), (gap_sha, "2026-09-15T00:28:45Z"))
        self.assertEqual(gap["key_parameters"]["max_gap_days"]["value"],
                         {"CRYPTO": 2, "US": 4, "KR": 7, "unit": "CALENDAR_DAYS"})
        interp = _row(registry, "RULE.ROTATION.INTERPRETATION_OBSERVATION_GAP.V1")
        self.assertEqual(interp["amended_by"], [{"rule_id": gap["rule_id"], "sha256": gap_sha}])
        self.assertEqual(_row(registry, "RULE.HEDGE.KR_STRESS_UNRATIFIED_INTERIM.V1")["key_parameters"]["kr_inverse_hedge"]["value"], "OFF")

    def test_amendment_links_do_not_change_amended_parameters(self):
        registry = _registry()
        freshness = _row(registry, "RULE.CRYPTO.FRESHNESS.PER_MARKET.V1")
        self.assertEqual(freshness["amended_by"][0]["rule_id"], "RULE.EXEC.DATA_FAILURE_PRIORITY.V1")
        self.assertEqual(freshness["key_parameters"]["provider_age_seconds_max"]["value"], 20)
        allocation = _row(registry, "RULE.ALLOCATION.V2")
        self.assertEqual(allocation["amended_by"][0]["rule_id"], "RULE.RISK.NAV_DRAWDOWN_LIFT.V1")
        self.assertEqual(allocation["key_parameters"]["per_market_state_multiplier_of_base"]["value"]["NEUTRAL"], "0.70")
        self.assertEqual(_row(registry, "RULE.LIQUIDITY.KRUS.V1")["amended_by"][0]["rule_id"],
                         "RULE.LIQUIDITY.US_SIP_SOURCE.V1")
        gap = _row(registry, "RULE.ROTATION.INTERPRETATION_OBSERVATION_GAP.V1")
        self.assertEqual({(a["rule_id"], a["relation"]) for a in gap["amends"]}, {
            ("RULE.ROTATION.CRYPTO.V1", "INTERPRETS"), ("RULE.ROTATION.US.V1P", "INTERPRETS"),
            ("RULE.ROTATION.KR.V1T", "INTERPRETS"), ("RULE.EXIT.RELEASE_FULL_SELL.V1", "INTERPRETS")})
        self.assertEqual(gap["key_parameters"]["consecutive_unit"]["value"]["max_gap_days"], {"CRYPTO": 2, "US": 4, "KR": 7})

    def test_review_note_values(self):
        registry = _registry()
        d6 = _row(registry, "RULE.EXEC.MULTI_MARKET_REALLOCATION.V1")["key_parameters"]["split"]
        self.assertTrue(d6["text"].startswith("여러 시장이 추가 배분을 받을 수 있으면"))
        self.assertEqual(d6["value"]["condition"], "MULTIPLE_MARKETS_ELIGIBLE_FOR_ADDITIONAL_ALLOCATION")
        sip = _row(registry, "RULE.LIQUIDITY.US_SIP_SOURCE.V1")["key_parameters"]["probe_limitation"]
        self.assertEqual(sip["value"], {"zero_bar_symbols": ["SPY", "MSFT"], "pagination_followed": False})
        levels = {rid: _row(registry, rid)["evidence_level_at_decision"]["level"] for rid in (
            "RULE.EXIT.RELEASE_FULL_SELL.V1", "RULE.EXIT.CRYPTO_TIME_STOP_21D.V1", "RULE.EXIT.SHADOW_CONTROLS.V1",
            "RULE.SIZE.BTC_ETH_PER_NAME_CAP.V1", "RULE.RISK.PLANNED_LOSS_RECORD_ONLY.V1", "RULE.RISK.NAV_DRAWDOWN_LIFT.V1")}
        self.assertEqual(levels, {
            "RULE.EXIT.RELEASE_FULL_SELL.V1": "STUDY_LEVEL_C_NO_RETURN_EDGE",
            "RULE.EXIT.CRYPTO_TIME_STOP_21D.V1": "STUDY_LEVEL_C_NO_RETURN_EDGE",
            "RULE.EXIT.SHADOW_CONTROLS.V1": "STUDY_LEVEL_C_NO_RETURN_EDGE",
            "RULE.SIZE.BTC_ETH_PER_NAME_CAP.V1": "NOT_STATED_IN_RECORD",
            "RULE.RISK.PLANNED_LOSS_RECORD_ONLY.V1": "NOT_STATED_IN_RECORD",
            "RULE.RISK.NAV_DRAWDOWN_LIFT.V1": "NOT_STATED_IN_RECORD",
        })
        b2 = _row(registry, "RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1")
        self.assertTrue(b2["source_records"][0]["repo_path"].endswith("_recorded_at_corrected.json"))
        self.assertEqual({b["path"] for b in b2["implementation_bindings"]},
                         {"universe/crypto_candidate_promotion.py", "config/crypto_candidate_promotion_contract_v3.json"})

    def test_sector_state_vocabulary_is_normalized(self):
        registry = _registry()
        common = _row(registry, "RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1")["key_parameters"]
        entry = _row(registry, "RULE.ENTRY.PAPER_BASELINE_B.V1")["key_parameters"]
        self.assertEqual(common["t1_t2_feed"]["value"]["eligible_sector_states"], ["STRONG_CONFIRMED", "STRONG_HELD"])
        self.assertEqual(common["neutral_market_new_buys"]["value"]["eligible_sector_states"], ["STRONG_CONFIRMED", "STRONG_HELD"])
        self.assertEqual(entry["sector_gate"]["value"], ["STRONG_CONFIRMED", "STRONG_HELD"])
        self.assertIn("strong confirmed/held", common["t1_t2_feed"]["text"])

    def test_pin_file_covers_every_parsed_item(self):
        pins = json.loads((ROOT / REG.PINS_RELATIVE_PATH).read_text(encoding="utf-8"))
        self.assertEqual(pins, REG.build_parsed_pins(_registry()))
        self.assertEqual(hashlib.sha256((ROOT / REG.PINS_RELATIVE_PATH).read_bytes()).hexdigest(), REG.PARSED_PINS_SHA256)

    def test_kst_minute_effective_conversion(self):
        self.assertEqual(REG.kst_minute_to_utc("2026-09-15T00:05+09:00"), "2026-09-14T15:05:00Z")
        with self.assertRaises(REG.RuleRegistryError):
            REG.kst_minute_to_utc("2026-09-15T00:0x+09:00")

    def test_cli_passes(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(REG.main([]), 0)
        self.assertIn("PASS_RULE_REGISTRY_VALID", out.getvalue())


class RegistryTamperTests(TmpRootCase):
    def test_baseline_valid_in_tmp_root(self):
        REG.validate_registry(self.registry, self.root)

    # --- parsed value drift under an unchanged quote (reviewer mutations) ---
    def test_hedge_time_stop_value_drift_with_same_quote_fails(self):
        item = _row(self.registry, "RULE.HEDGE.INVERSE.V1")["key_parameters"]["time_stop_sessions"]
        self.assertEqual((item["text"], item["value"]), ("10 sessions max holding", 10))
        item["value"] = 12
        self.assertInvalid(self.registry, "PARSED_VALUE_PIN_MISMATCH")

    def test_freshness_provider_age_value_drift_with_same_quote_fails(self):
        item = _row(self.registry, "RULE.CRYPTO.FRESHNESS.PER_MARKET.V1")["key_parameters"]["provider_age_seconds_max"]
        self.assertEqual(item["value"], 20)
        item["value"] = 60
        self.assertInvalid(self.registry, "PARSED_VALUE_PIN_MISMATCH")

    def test_trigger_condition_rewrite_fails(self):
        trigger = _row(self.registry, "RULE.ROTATION.CRYPTO.V1")["review_triggers"][0]
        trigger["condition"] = "확정 사건 5건 후 검토"
        self.assertInvalid(self.registry, "PARSED_VALUE_PIN_MISMATCH")

    def test_minimum_sample_drift_fails(self):
        _row(self.registry, "RULE.ROTATION.CRYPTO.V1")["minimum_sample"]["value"] = 5
        self.assertInvalid(self.registry, "PARSED_VALUE_PIN_MISMATCH")

    def test_pin_file_edit_without_constant_fails(self):
        path = self.root / REG.PINS_RELATIVE_PATH
        pins = json.loads(path.read_text(encoding="utf-8"))
        item = _row(self.registry, "RULE.HEDGE.INVERSE.V1")["key_parameters"]["time_stop_sessions"]
        item["value"] = 12
        pins["pins"]["RULE.HEDGE.INVERSE.V1#key_parameters.time_stop_sessions"] = REG.payload_sha256(item)
        path.write_text(json.dumps(pins, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.assertInvalid(self.registry, "PARSED_PINS_FILE_HASH_MISMATCH")

    # --- status cross-check against the record ---
    def test_row_status_must_match_record_status(self):
        _row(self.registry, "RULE.EXIT.RELEASE_FULL_SELL.V1")["status"] = "RATIFIED"
        self.assertInvalid(self.registry, "ROW_STATUS_DISAGREES_WITH_RECORD")

    def test_record_named_status_requires_status_source(self):
        _row(self.registry, "RULE.EXIT.SHADOW_CONTROLS.V1")["status_source"] = None
        self.assertInvalid(self.registry, "STATUS_SOURCE_REQUIRED")

    def test_rotation_status_source_value_must_match(self):
        _row(self.registry, "RULE.ROTATION.US.V1P")["status_source"]["record_value"] = "RATIFIED"
        self.assertInvalid(self.registry, "STATUS_SOURCE_VALUE_MISMATCH")

    # --- records and ids ---
    def test_record_byte_change_fails(self):
        path = self.root / _row(self.registry, "RULE.ALLOCATION.V2")["source_records"][0]["repo_path"]
        path.write_bytes(path.read_bytes().replace(b'"0.40"', b'"0.41"', 1))
        self.assertInvalid(self.registry, "SOURCE_RECORD_HASH_MISMATCH")

    def test_missing_record_fails(self):
        (self.root / _row(self.registry, "RULE.US.SESSION_CALENDAR.V1")["source_records"][0]["repo_path"]).unlink()
        self.assertInvalid(self.registry, "SOURCE_RECORD_MISSING")

    def test_rule_without_source_fails(self):
        _row(self.registry, "RULE.GOVERNANCE.EVIDENCE_GATED.V1")["source_records"] = []
        self.assertInvalid(self.registry, "RULE_WITHOUT_SOURCE_RECORD")

    def test_duplicate_id_fails(self):
        self.registry["rules"].append(copy.deepcopy(self.registry["rules"][0]))
        self.assertInvalid(self.registry, "RULE_ID_DUPLICATE")

    def test_unknown_or_missing_id_fails(self):
        _row(self.registry, "RULE.HEDGE.INVERSE.V1")["rule_id"] = "RULE.HEDGE.INVERSE.V9"
        self.assertInvalid(self.registry, "RULE_ID_SET_MISMATCH")

    def test_non_monotone_versions_fail(self):
        _row(self.registry, "RULE.ALLOCATION.V2")["lineage_key"] = "RULE"
        hedge = _row(self.registry, "RULE.HEDGE.INVERSE.V1")
        hedge["lineage_key"] = "RULE"
        REG.validate_registry(self.registry, self.root)  # hedge v1 earlier, allocation v2 later
        hedge["version"] = 3
        self.assertInvalid(self.registry, "VERSIONS_NOT_MONOTONE")

    def test_parameter_value_must_equal_record(self):
        _row(self.registry, "RULE.ALLOCATION.V2")["key_parameters"]["market_max_allocation"]["value"]["US"] = "0.60"
        self.assertInvalid(self.registry, "PARAMETER_NOT_EQUAL_TO_RECORD")

    def test_parsed_text_must_be_verbatim_in_record(self):
        _row(self.registry, "RULE.ROTATION.CRYPTO.V1")["key_parameters"]["strong_confirmation"]["text"] = (
            "strong confirmed = rank 1 for 3 consecutive days")
        self.assertInvalid(self.registry, "PARAMETER_TEXT_NOT_IN_RECORD")

    def test_pointer_must_resolve(self):
        _row(self.registry, "RULE.LIQUIDITY.KRUS.V1")["key_parameters"]["kr_last_close_krw_min"]["record_pointer"] = "/KR/nope"
        self.assertInvalid(self.registry, "POINTER_UNRESOLVED")

    def test_effective_from_must_come_from_record(self):
        _row(self.registry, "RULE.ROTATION.KR.V1T")["effective_from"]["utc"] = "2026-09-15T00:05:00Z"
        self.assertInvalid(self.registry, "EFFECTIVE_FROM_NOT_FROM_RECORD")

    def test_invented_trigger_fails(self):
        row = _row(self.registry, "RULE.ALLOCATION.V2")
        row["review_triggers"] = [{
            "trigger_id": "INVENTED", "condition": "x", "source": 0, "record_pointer": "/scope",
            "match": "PARSED_FROM_TEXT", "text": "after 30 trades"}]
        row["trigger_pending_user_confirmation"] = False
        self.assertInvalid(self.registry, "PARAMETER_TEXT_NOT_IN_RECORD")

    def test_null_trigger_requires_pending_flag(self):
        _row(self.registry, "RULE.US.SESSION_CALENDAR.V1")["trigger_pending_user_confirmation"] = False
        self.assertInvalid(self.registry, "TRIGGER_NULL_REQUIRES_PENDING_TRUE")

    def test_addendum_must_name_a_ratification_of_the_rule(self):
        row = _row(self.registry, "RULE.LIQUIDITY.KRUS.V1")
        row["source_records"].append(copy.deepcopy(_row(self.registry, "RULE.CRYPTO.RUNTIME.V1")["source_records"][1]))
        self.assertInvalid(self.registry, "ADDENDUM_NOT_BOUND_TO_RATIFICATION")

    def test_out_of_registry_supersedes_hash_must_be_named_by_primary_record(self):
        _row(self.registry, "RULE.ALLOCATION.V2")["supersedes"]["sha256"] = "0" * 64
        self.assertInvalid(self.registry, "SUPERSEDES_NOT_NAMED_BY_PRIMARY_RECORD")

    def test_binding_claim_must_hold(self):
        _row(self.registry, "RULE.US.SESSION_CALENDAR.V1")["implementation_bindings"][1]["binds_record_sha256"] = True
        self.assertInvalid(self.registry, "BINDING_DOES_NOT_CONTAIN_RECORD_SHA")

    def test_status_vocabulary(self):
        _row(self.registry, "RULE.ROTATION.US.V1P")["status"] = "CONFIRMED"
        self.assertInvalid(self.registry, "STATUS_INVALID")

    # --- undecided / supersession / amendment integrity ---
    def test_undecided_row_with_parameters_fails(self):
        row = _row(self.registry, "RULE.US.LIQUIDITY_IEX_TREATMENT.PENDING")
        row["key_parameters"] = {"x": {"source": 0, "record_pointer": "/explicitly_not_decided/4",
                                       "match": "PARSED_FROM_TEXT", "text": "US liquidity", "value": "SIP"}}
        self.assertInvalid(self.registry, "PENDING_ROW_MUST_CARRY_NO_DECISION")

    def test_pending_basis_must_be_in_record(self):
        _row(self.registry, "RULE.US.LIQUIDITY_IEX_TREATMENT.PENDING")["pending_basis"][0]["text"] = "US liquidity decided"
        self.assertInvalid(self.registry, "PARAMETER_TEXT_NOT_IN_RECORD")

    def test_pending_status_cannot_keep_a_resolver(self):
        _row(self.registry, "RULE.US.LIQUIDITY_IEX_TREATMENT.PENDING")["status"] = "PENDING_USER_DECISION"
        self.assertInvalid(self.registry, "PENDING_ROW_HAS_RESOLVER")

    def test_resolved_row_needs_decided_resolver(self):
        row = _row(self.registry, "RULE.EXIT.PENDING")
        row["resolved_by"] = None
        self.assertInvalid(self.registry, "RESOLVED_ROW_REQUIRES_RESOLVER")

    def test_resolver_hash_must_match(self):
        _row(self.registry, "RULE.CRYPTO.BTC_ETH_NAME_CAP.PENDING")["resolved_by"][0]["sha256"] = "0" * 64
        self.assertInvalid(self.registry, "RESOLVER_RECORD_MISMATCH")

    def test_record_named_rule_id_must_match(self):
        _row(self.registry, "RULE.ENTRY.PAPER_BASELINE_B.V1")["key_parameters"]["record_rule_id"]["value"] = "RULE.ENTRY.X.V1"
        self.assertInvalid(self.registry, "PARAMETER_NOT_EQUAL_TO_RECORD")

    def test_superseded_status_requires_matching_successor(self):
        v1 = _row(self.registry, "RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V1")
        v1["status"] = "RATIFIED"
        self.assertInvalid(self.registry, "SUPERSEDED_RULE_NOT_MARKED")
        v1["status"] = "SUPERSEDED"
        v1["superseded_by"]["sha256"] = "0" * 64
        self.assertInvalid(self.registry, "SUPERSEDED_BY_RECORD_MISMATCH")

    def test_successor_version_must_be_higher(self):
        _row(self.registry, "RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V2")["version"] = 1
        self.assertInvalid(self.registry, "VERSIONS_NOT_MONOTONE")

    def test_amended_by_must_mirror_amends(self):
        _row(self.registry, "RULE.ALLOCATION.V2")["amended_by"] = None
        self.assertInvalid(self.registry, "AMENDS_AMENDED_BY_MISMATCH")

    def test_successor_citing_an_unlisted_earlier_hash_is_rejected(self):
        # Drop the previous-hash note from the corrected B2/B3 copy: size V2's
        # record cites only that earlier hash, so supersession must fail.
        b2_path = next(s["repo_path"] for row in self.registry["rules"] for s in row["source_records"]
                       if s["original_filename"] == "USER_RATIFICATION_PAPER_B2_B3_SIZE_ASSEMBLY_20260915.json")
        path = self.root / b2_path
        record = json.loads(path.read_bytes())
        record["correction_note"] = "recorded_at_kst corrected; previous hash intentionally omitted"
        raw = (json.dumps(record, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
        path.write_bytes(raw)
        new_sha = hashlib.sha256(raw).hexdigest()
        for row in self.registry["rules"]:
            for source in row["source_records"]:
                if source["repo_path"] == b2_path:
                    source["sha256"], source["bytes"] = new_sha, len(raw)
        _row(self.registry, "RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V2")["supersedes"]["sha256"] = new_sha
        # The promotion implementation binds only the earlier hash, so its
        # binding claim fails first unless removed -- that is the same guard.
        promotion = _row(self.registry, "RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1")
        self.assertInvalid(copy.deepcopy(self.registry), "BINDING_DOES_NOT_CONTAIN_RECORD_SHA")
        promotion["implementation_bindings"] = []
        self.assertInvalid(self.registry, "SUPERSEDES_NOT_NAMED_BY_PRIMARY_RECORD")

    def test_partial_supersession_must_be_mirrored(self):
        _row(self.registry, "RULE.ROTATION.RELEASE_HANDLING.V1")["superseded_parts"] = None
        self.assertInvalid(self.registry, "SUPERSEDES_PARTS_MISMATCH")

    def test_partial_supersession_parameter_must_exist(self):
        _row(self.registry, "RULE.EXIT.RELEASE_FULL_SELL.V1")["supersedes_parts"][0]["key_parameter"] = "nope"
        self.assertInvalid(self.registry, "PART_SUPERSEDED_PARAMETER_MISSING")

    def test_abbreviated_citation_needs_matching_name_and_prefix(self):
        # The P1-P6 and max-gap records cite earlier records as "<FILE>.json (<8 hex>…)".
        cool = _row(self.registry, "RULE.GOVERNANCE.COOLING_OFF.V1")
        path = self.root / cool["source_records"][0]["repo_path"]
        original = path.read_bytes()
        for old, new in ((b"(4e08b945\xe2\x80\xa6)", b"(4e08b946\xe2\x80\xa6)"),
                         (b"USER_RATIFICATION_RULE_GOVERNANCE_EVIDENCE_GATED_ADJUSTMENT_20260915.json (4e08",
                          b"USER_RATIFICATION_RULE_GOVERNANCE_EVIDENCE_GATED_ADJUSTMENT_20260916.json (4e08")):
            self.assertIn(old, original)
            path.write_bytes(original.replace(old, new))
            old_sha = cool["source_records"][0]["sha256"]
            tampered = json.loads(json.dumps(self.registry).replace(old_sha, hashlib.sha256(path.read_bytes()).hexdigest()))
            for row in tampered["rules"]:
                for source in row["source_records"]:
                    if source["repo_path"] == cool["source_records"][0]["repo_path"]:
                        source["bytes"] = len(path.read_bytes())
            with self.assertRaises(REG.RuleRegistryError) as ctx:
                REG.validate_registry(tampered, self.root)
            self.assertIn("NOT_NAMED_BY_PRIMARY_RECORD", str(ctx.exception))
        path.write_bytes(original)

    def test_amendment_must_name_target_record(self):
        _row(self.registry, "RULE.EXEC.DATA_FAILURE_PRIORITY.V1")["amends"][0]["sha256"] = \
            _row(self.registry, "RULE.US.SESSION_CALENDAR.V1")["source_records"][0]["sha256"]
        self.assertInvalid(self.registry, "AMENDED_RECORD_NOT_A_SOURCE_OF_TARGET")


class RuleRefsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = REFS.RegistryContext.load()

    def test_ref_is_registry_exact(self):
        ref = REFS.make_rule_ref(self.ctx, "RULE.ALLOCATION.V2", "SIZED_BY")
        self.assertEqual(ref, {
            "rule_id": "RULE.ALLOCATION.V2", "version": 2, "registry_sha256": REG.registry_sha256(),
            "source_record_sha256": ORIGINAL_RECORD_SHA256["USER_RATIFICATION_PAPER_MARKET_ALLOCATION_V2_20260913.json"],
            "role": "SIZED_BY",
        })
        for field, bad in (("version", 1), ("registry_sha256", "0" * 64), ("source_record_sha256", "1" * 64)):
            tampered = dict(ref, **{field: bad})
            with self.assertRaises(REFS.RuleLineageError):
                REFS.validate_rule_refs([tampered], self.ctx)

    def test_superseded_by_role_is_additive(self):
        ref = REFS.make_rule_ref(self.ctx, "RULE.EXIT.RELEASE_FULL_SELL.V1", "SUPERSEDED_BY")
        self.assertEqual(REFS.validate_rule_refs([ref], self.ctx), [ref])
        self.assertEqual(REFS.ROLES[:4], ("APPLIED", "BLOCKED_BY", "SIZED_BY", "EXITED_BY"))

    def test_unknown_rule_role_and_duplicates_fail(self):
        with self.assertRaises(REFS.RuleLineageError):
            REFS.make_rule_ref(self.ctx, "RULE.NOPE.V1", "APPLIED")
        with self.assertRaises(REFS.RuleLineageError):
            REFS.make_rule_ref(self.ctx, "RULE.ALLOCATION.V2", "ALLOWED_BY")
        ref = REFS.make_rule_ref(self.ctx, "RULE.ALLOCATION.V2", "APPLIED")
        with self.assertRaises(REFS.RuleLineageError):
            REFS.validate_rule_refs([ref, dict(ref)], self.ctx)
        with self.assertRaises(REFS.RuleLineageError):
            REFS.validate_rule_refs([dict(ref, extra=1)], self.ctx)

    def test_canonical_order_and_merge(self):
        refs = REFS.canonical_rule_refs([
            ("RULE.HEDGE.INVERSE.V1", "EXITED_BY"), ("RULE.ALLOCATION.V2", "SIZED_BY"),
            ("RULE.ALLOCATION.V2", "SIZED_BY"), ("RULE.ALLOCATION.V2", "APPLIED"),
        ], self.ctx)
        self.assertEqual([(r["rule_id"], r["role"]) for r in refs], [
            ("RULE.ALLOCATION.V2", "APPLIED"), ("RULE.ALLOCATION.V2", "SIZED_BY"),
            ("RULE.HEDGE.INVERSE.V1", "EXITED_BY")])

    def _event(self, **overrides):
        kwargs = dict(
            decision_id="d1", producer="p", market="KR", instrument="005930",
            timestamp_utc="2026-09-15T00:00:00Z", event_type="BLOCK", gate="g",
            outcome={"state": "WAIT"},
            rule_refs=REFS.canonical_rule_refs([("RULE.LIQUIDITY.KRUS.V1", "BLOCKED_BY")], self.ctx),
            unapplied_rules=[], inputs={"x": 1},
            source_packet={"path": "evidence/x/packet.json", "schema_version": "x/1", "payload_sha256": "a" * 64},
        )
        kwargs.update(overrides)
        return REFS.build_lineage_event(self.ctx, **kwargs)

    def test_event_identity_and_tamper(self):
        event = self._event()
        self.assertEqual(event["schema_version"], "rule_lineage_event/1")
        tampered = copy.deepcopy(event)
        tampered["outcome"]["state"] = "PAPER_BUY_ELIGIBLE"
        with self.assertRaises(REFS.RuleLineageError):
            REFS.validate_lineage_event(tampered, self.ctx)
        for bad in ({"event_type": "SIGNAL"}, {"market": "JP"}, {"timestamp_utc": "2026-09-15"}):
            with self.assertRaises(REFS.RuleLineageError):
                self._event(**bad)

    def test_block_must_name_blocker_or_unapplied_rule(self):
        with self.assertRaises(REFS.RuleLineageError):
            self._event(rule_refs=[])
        event = self._event(rule_refs=[], unapplied_rules=[
            {"rule_id": "RULE.LIQUIDITY.KRUS.V1", "reason_code": "NO_PRODUCER"}])
        self.assertEqual(event["event_type"], "BLOCK")
        with self.assertRaises(REFS.RuleLineageError):
            self._event(unapplied_rules=[{"rule_id": "RULE.LIQUIDITY.KRUS.V1", "reason_code": "NO_PRODUCER"}])

    def test_sidecar_is_append_only(self):
        source = {"path": "evidence/x/packet.json", "schema_version": "x/1", "payload_sha256": "a" * 64}
        sidecar = REFS.build_sidecar(self.ctx, producer="p", source_packet=source, events=[self._event()])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "s.json"
            self.assertEqual(REFS.write_sidecar_append_only(path, sidecar), "written")
            self.assertEqual(REFS.write_sidecar_append_only(path, sidecar), "verified_existing")
            other = REFS.build_sidecar(self.ctx, producer="p", source_packet=source, events=[])
            with self.assertRaises(REFS.RuleLineageError):
                REFS.write_sidecar_append_only(path, other)
        broken = copy.deepcopy(sidecar)
        broken["decision_outcome_changed"] = True
        with self.assertRaises(REFS.RuleLineageError):
            REFS.validate_sidecar(broken, self.ctx)


def _crypto_packets():
    return sorted(glob.glob(str(ROOT / "evidence/crypto_paper_decision/*/*/*/packet.json")))


class CryptoDecisionLineageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = REFS.RegistryContext.load()

    def test_every_committed_packet_yields_decision_identical_lineage(self):
        paths = _crypto_packets()
        self.assertGreater(len(paths), 0)
        for path in paths:
            raw = Path(path).read_bytes()
            packet = json.loads(raw)
            sidecar = LIN.build_crypto_decision_sidecar(packet, self.ctx)
            self.assertEqual(Path(path).read_bytes(), raw)
            self.assertEqual(str(ROOT / sidecar["source_packet"]["path"]), path)
            self.assertEqual(sidecar["source_packet"]["payload_sha256"], packet["payload_sha256"])
            self.assertFalse(sidecar["decision_outcome_changed"])
            states = {e["instrument"]: e["outcome"]["state"] for e in sidecar["events"] if e["gate"] == "candidate_state"}
            self.assertEqual(states, {row["market"]: row["state"] for row in packet["candidates"]})
            self.assertEqual(LIN.build_crypto_decision_sidecar(packet, self.ctx), sidecar)

    def test_stale_market_is_blocked_by_the_per_market_rule(self):
        path = next(p for p in _crypto_packets()
                    if "2026-09-14/2113/340453e260e4cb5247e9f64eb73e036d77662f7c239b1bfd4394d44f705cf85e" in p)
        packet = json.loads(Path(path).read_text(encoding="utf-8"))
        sidecar = LIN.build_crypto_decision_sidecar(packet, self.ctx)
        by = {(e["instrument"], e["gate"]): e for e in sidecar["events"]}
        wld = by[("KRW-WLD", "realtime_freshness_per_market")]
        self.assertEqual(wld["event_type"], "BLOCK")
        self.assertEqual([(r["rule_id"], r["role"]) for r in wld["rule_refs"]],
                         [("RULE.CRYPTO.FRESHNESS.PER_MARKET.V1", "BLOCKED_BY")])
        self.assertTrue(wld["outcome"]["is_recorded_action_cap"])
        self.assertFalse(wld["outcome"]["capped_actionable_state"])  # state was WATCH, not actionable
        btc = by[("KRW-BTC", "realtime_liquidity_floor")]
        self.assertEqual((btc["event_type"], btc["rule_refs"][0]["role"]), ("DECISION", "APPLIED"))
        state = by[("KRW-WLD", "candidate_state")]
        self.assertEqual(state["outcome"]["state"], "WATCH")
        self.assertEqual({u["rule_id"] for u in state["unapplied_rules"]}, {
            "RULE.ALLOCATION.V2", "RULE.CRYPTO.RUNTIME.V1", "RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1",
            "RULE.ROTATION.CRYPTO.V1"})

    def test_liquidity_floor_exclusion_and_state_cap_are_lineaged(self):
        path = next(p for p in _crypto_packets()
                    if "2026-09-14/2113/340453e260e4cb5247e9f64eb73e036d77662f7c239b1bfd4394d44f705cf85e" in p)
        packet = json.loads(Path(path).read_text(encoding="utf-8"))
        row = next(r for r in packet["candidates"] if r["market"] == "KRW-SHIB")
        reason = "REALTIME_LIQUIDITY_FLOOR_EXCLUDED:KRW-SHIB:TURNOVER_30D_AVG_BELOW_FLOOR"
        row["realtime_liquidity_floor"] = {"status": "EXCLUDED", "reason": "TURNOVER_30D_AVG_BELOW_FLOOR",
                                           "krw_30d_avg_turnover": "1"}
        row.update({"state": "WAIT", "reason": reason, "freshness_capped": True,
                    "freshness_cap_reason": reason, "market_action_cap_reason": reason})
        sidecar = LIN.build_crypto_decision_sidecar(packet, self.ctx)
        by = {(e["instrument"], e["gate"]): e for e in sidecar["events"]}
        floor = by[("KRW-SHIB", "realtime_liquidity_floor")]
        self.assertEqual(floor["event_type"], "BLOCK")
        self.assertTrue(floor["outcome"]["capped_actionable_state"])
        state = by[("KRW-SHIB", "candidate_state")]
        self.assertEqual((state["event_type"], state["rule_refs"][0]["role"]), ("BLOCK", "BLOCKED_BY"))

    def test_legacy_packets_predate_the_rule(self):
        path = next(p for p in _crypto_packets() if "/2026-08-29/" in p)
        packet = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(packet["schema_version"], "crypto_paper_decision_snapshot_packet/1")
        for event in LIN.build_crypto_decision_sidecar(packet, self.ctx)["events"]:
            self.assertEqual(event["rule_refs"], [])
            self.assertIn({"rule_id": "RULE.CRYPTO.FRESHNESS.PER_MARKET.V1",
                           "reason_code": "PACKET_PREDATES_RULE_EFFECTIVE_FROM"}, event["unapplied_rules"])

    def test_cli_writes_sidecar_and_packet_still_revalidates(self):
        relative = ("evidence/crypto_paper_decision/2026-09-14/2113/"
                    "340453e260e4cb5247e9f64eb73e036d77662f7c239b1bfd4394d44f705cf85e/packet.json")
        raw = (ROOT / relative).read_bytes()
        packet = json.loads(raw)
        with tempfile.TemporaryDirectory() as tmp:
            lineage_root = Path(tmp) / "rule_lineage"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(LIN.main(["crypto-decision", "--packet", relative, "--lineage-root", str(lineage_root)]), 0)
            expected = (lineage_root / "crypto_paper_decision/2026-09-14/2113" / packet["payload_sha256"]
                        / f"registry-{self.ctx.sha256}.json")
            REFS.validate_sidecar(json.loads(expected.read_text(encoding="utf-8")), self.ctx)
            again = LIN.run_cli("crypto-decision", ROOT / relative, lineage_root=lineage_root)
            self.assertEqual(again["status"], "verified_existing")
            # Lineage failures are reported and never raised or turned into a non-zero exit.
            with contextlib.redirect_stderr(io.StringIO()) as err:
                tampered_dir = Path(tmp) / relative
                tampered_dir.parent.mkdir(parents=True)
                tampered = dict(packet, capture_hhmm="2114")
                tampered_dir.write_text(json.dumps(tampered), encoding="utf-8")
                self.assertEqual(LIN.run_cli("crypto-decision", tampered_dir, root=Path(tmp),
                                             lineage_root=lineage_root)["status"], "FAILED")
                blocker = Path(tmp) / "blocked"
                blocker.write_text("not a directory", encoding="utf-8")
                self.assertEqual(LIN.run_cli("crypto-decision", ROOT / relative, lineage_root=blocker)["status"], "FAILED")
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(LIN.main(["crypto-decision", "--packet", "missing.json",
                                               "--lineage-root", str(blocker)]), 0)
            self.assertIn("RULE_LINEAGE_EMIT_FAILED", err.getvalue())
            self.assertIn("::warning title=Rule lineage sidecar failed::", err.getvalue())
            self.assertEqual(json.loads(out.getvalue())["status"], "FAILED")  # stdout = one JSON document
        self.assertEqual((ROOT / relative).read_bytes(), raw)
        CPDS.validate_output(packet)

    def test_producer_sources_stay_byte_identical_to_their_pins(self):
        explanation_test = (ROOT / "test/test_crypto_axis_trade_bridge_explanation.py").read_text(encoding="utf-8")
        snapshot_sha = hashlib.sha256((ROOT / "decision/crypto_paper_decision_snapshot.py").read_bytes()).hexdigest()
        self.assertIn(f'"{snapshot_sha}"', explanation_test)
        qualification = json.loads((ROOT / "evidence/authority/"
                                    "kr_information_system_runtime_qualification_candidate_20260913.json").read_text())
        reference_sha = hashlib.sha256((ROOT / "regime/paper_regime_reference.py").read_bytes()).hexdigest()
        self.assertEqual(qualification["bindings"]["implementation_sha256"]["regime/paper_regime_reference.py"], reference_sha)
        for relative in ("decision/crypto_paper_decision_snapshot.py", "regime/paper_regime_reference.py"):
            self.assertNotIn("rule_lineage", (ROOT / relative).read_text(encoding="utf-8"))
        workflow = (ROOT / ".github/workflows/upbit-realtime-capture.yml").read_text(encoding="utf-8")
        self.assertIn("git add evidence/rule_lineage/crypto_paper_decision || true", workflow)
        steps = yaml.safe_load(workflow)["jobs"]["capture"]["steps"]
        ids = [step.get("id") for step in steps]
        lineage = ids.index("crypto_rule_lineage")
        self.assertLess(ids.index("crypto_paper_decision"), lineage)
        self.assertLess(ids.index("validation_capture"), lineage)
        self.assertTrue(steps[lineage]["continue-on-error"])
        self.assertEqual(steps[lineage]["timeout-minutes"], 5)
        self.assertIn("crypto-decision --packet \"$DECISION_PATH\"", steps[lineage]["run"])
        self.assertEqual(steps[lineage]["env"]["DECISION_PATH"], "${{ steps.crypto_paper_decision.outputs.path }}")
        self.assertEqual(steps[lineage + 1]["name"], "Commit append-only realtime evidence and run telemetry")


class PaperReferenceLineageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = REFS.RegistryContext.load()

    def test_committed_references_yield_lineage_without_claiming_allocation(self):
        paths = sorted(glob.glob(str(ROOT / "evidence/regime/paper_reference/*/*/packet.json")))
        built = 0
        for path in paths:
            packet = json.loads(Path(path).read_text(encoding="utf-8"))
            if packet.get("schema_version") != LIN.REFERENCE_SCHEMA_VERSION:
                continue
            sidecar = LIN.build_reference_sidecar(packet, self.ctx)
            self.assertEqual(str(ROOT / sidecar["source_packet"]["path"]), path)
            for event in sidecar["events"]:
                row = next(r for r in packet["markets"] if r["market"] == event["market"])
                self.assertEqual(event["outcome"]["candidate_regime"], row["paper_reference"]["candidate_regime"])
                self.assertEqual(event["rule_refs"], [])
                self.assertIn("RULE.ALLOCATION.V2", {u["rule_id"] for u in event["unapplied_rules"]})
            built += 1
        self.assertGreater(built, 0)

    def test_reference_cli_and_workflow(self):
        raw = (ROOT / "data/latest_paper_regime_reference.json").read_bytes()
        packet = json.loads(raw)
        with tempfile.TemporaryDirectory() as tmp:
            result = LIN.run_cli("paper-reference", "data/latest_paper_regime_reference.json",
                                 lineage_root=Path(tmp))
            self.assertEqual(result["status"], "written")
            self.assertTrue(Path(result["path"]).is_relative_to(Path(tmp) / "paper_regime_reference"))
            self.assertEqual(Path(result["path"]).name, f"registry-{self.ctx.sha256}.json")
            with contextlib.redirect_stderr(io.StringIO()):
                changed = Path(tmp) / "latest.json"
                changed.write_bytes(raw.replace(b'"generated_at"', b'"generated_at" ', 1))
                self.assertEqual(LIN.run_cli("paper-reference", changed, lineage_root=Path(tmp))["status"], "FAILED")
        self.assertEqual((ROOT / "data/latest_paper_regime_reference.json").read_bytes(), raw)
        PRR.validate_reference(packet, frozen_packet_authenticated=True)
        # The reference workflow is hash-pinned by the regime source owner
        # registry, so lineage runs in its own workflow and the pin still holds.
        owner = json.loads((ROOT / "config/regime_source_owner_registry_v2.json").read_text(encoding="utf-8"))
        pinned = [row for row in _walk(owner)
                  if isinstance(row, dict) and row.get("workflow_path") == ".github/workflows/paper-regime-reference.yml"]
        self.assertEqual(len(pinned), 1)
        self.assertEqual(pinned[0]["workflow_sha256"], hashlib.sha256(
            (ROOT / ".github/workflows/paper-regime-reference.yml").read_bytes()).hexdigest())
        self.assertNotIn("rule_lineage", (ROOT / ".github/workflows/paper-regime-reference.yml").read_text(encoding="utf-8"))
        workflow = yaml.safe_load((ROOT / ".github/workflows/rule-lineage-paper-reference.yml").read_text(encoding="utf-8"))
        triggers = workflow.get("on", workflow.get(True))
        self.assertEqual(triggers["workflow_run"]["workflows"], ["PAPER Market Risk Reference"])
        self.assertEqual(workflow["permissions"], {"contents": "write"})
        steps = workflow["jobs"]["sidecars"]["steps"]
        runs = "\n".join(step.get("run", "") for step in steps)
        self.assertIn("python3 governance/rule_registry.py", runs)
        self.assertIn("governance/rule_lineage_producers.py paper-reference-scan --min-date", runs)
        self.assertIn("git add evidence/rule_lineage/paper_regime_reference", runs)
        self.assertNotIn("regime/paper_regime_reference.py", runs)
        self.assertNotIn("secrets.", json.dumps(workflow))

    def test_scan_failure_keeps_stdout_parseable_for_the_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocked"
            blocker.write_text("not a directory", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(LIN.main(["paper-reference-scan", "--min-date", "2026-09-14",
                                           "--lineage-root", str(blocker)]), 0)
        # Exactly what the workflow step runs on stdout.
        summary = json.load(io.StringIO(out.getvalue()))["summary"]
        self.assertGreater(summary.get("FAILED", 0), 0)
        self.assertIn("::warning title=Rule lineage sidecar failed::", err.getvalue())
        self.assertNotIn("::warning", out.getvalue())
        workflow = (ROOT / ".github/workflows/rule-lineage-paper-reference.yml").read_text(encoding="utf-8")
        self.assertIn('json.load(sys.stdin)["summary"]', workflow)

    def test_reference_scan_is_idempotent_and_skips_legacy(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()) as err:
            first = LIN.scan_reference_evidence("2026-08-28", lineage_root=Path(tmp))
            statuses = {r["status"] for r in first}
            self.assertEqual(statuses, {"written", "SKIPPED_SCHEMA"})
            second = LIN.scan_reference_evidence("2026-08-28", lineage_root=Path(tmp))
            self.assertEqual({r["status"] for r in second}, {"verified_existing", "SKIPPED_SCHEMA"})
            self.assertEqual(LIN.scan_reference_evidence("2999-01-01", lineage_root=Path(tmp)), [])
        self.assertEqual(err.getvalue(), "")


class UpbitRealtimeCaptureTimeoutChangeTests(unittest.TestCase):
    """Regression test for this change only, not a general dispatcher-pin
    guard -- docs/do_not_touch_and_why.md section 3 explicitly treats a
    general guard (re-implementing event_ref_fingerprint() for all thirteen
    pinned files) as a decision, not a cleanup, since it would need to be
    kept in lockstep with the server config. This just pins down that
    raising timeout-minutes on the capture job stayed byte-drift-compatible
    (not fingerprint-changing) against that one file, the way it was
    verified by hand before the change was made."""

    # Reproduced from docs/do_not_touch_and_why.md section 3
    # ("event_ref_fingerprint is the sha256 of every stripped line
    # matching..."), split across two literals so this test file's own
    # comment can describe it without becoming a matching line itself if
    # anyone ever runs this fingerprint over the test tree.
    _TOKENS = ["GITHUB_EVENT", r"github\.event", r"github\.(triggering_)?actor",
               r"github\[", r"toJSON\(github", r"\binputs\.", "EVENT_NAME",
               "EVENT_SCHEDULE", r"uses:\s*\./"]
    _PATTERN = re.compile("|".join(_TOKENS), re.IGNORECASE)

    # The fingerprint of origin/main's upbit-realtime-capture.yml before this
    # PR (commit 5c4db14fc, HEAD at the time this change was written).
    _BASELINE_FINGERPRINT = "ddd3a4132c6e1791a80a72974fc12d3f101636148d68ad74a1ba922685f7db7d"

    def _fingerprint(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if self._PATTERN.search(line)]
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()

    def test_timeout_was_raised(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/upbit-realtime-capture.yml").read_text(encoding="utf-8"))
        self.assertEqual(workflow["jobs"]["capture"]["timeout-minutes"], 20)

    def test_fingerprint_is_unchanged_from_the_pre_change_baseline(self):
        text = (ROOT / ".github/workflows/upbit-realtime-capture.yml").read_text(encoding="utf-8")
        self.assertEqual(self._fingerprint(text), self._BASELINE_FINGERPRINT)

    def test_this_files_own_comment_does_not_self_match(self):
        # Guards against the exact mistake this PR's own first draft made:
        # explaining the fingerprint tokens inline in the workflow's comment
        # would itself change the fingerprint, since comments are not
        # stripped by event_ref_fingerprint().
        text = (ROOT / ".github/workflows/upbit-realtime-capture.yml").read_text(encoding="utf-8")
        matching = [line.strip() for line in text.splitlines() if self._PATTERN.search(line)]
        # Every matching line must be a real, pre-existing github-context
        # reference (env/with values), never workflow-authored prose.
        for line in matching:
            self.assertTrue(line.startswith(("ref:", "DURATION_SECONDS:", "ATLAS_EVENT", "TRIGGER:", "DEFAULT_BRANCH:")),
                             line)


class CryptoDecisionScanTests(unittest.TestCase):
    """governance/rule_lineage_producers.py::scan_crypto_decision_evidence.

    Second, independent path to the same sidecars the per-run capture-job
    step emits -- exists because that step is guarded on the capture job not
    having been cancelled (2026-09-18/19: coverage 49/49 -> 2/41 -> 0/1 with
    no signal, once the job started exceeding its timeout-minutes)."""

    @classmethod
    def setUpClass(cls):
        cls.ctx = REFS.RegistryContext.load()

    def test_scan_is_idempotent_over_every_retained_crypto_packet(self):
        paths = _crypto_packets()
        self.assertGreater(len(paths), 0)
        with tempfile.TemporaryDirectory() as tmp:
            first = LIN.scan_crypto_decision_evidence("2026-08-01", lineage_root=Path(tmp), context=self.ctx)
            self.assertEqual(len(first), len(paths))
            self.assertEqual({r["status"] for r in first}, {"written"})
            second = LIN.scan_crypto_decision_evidence("2026-08-01", lineage_root=Path(tmp), context=self.ctx)
            self.assertEqual({r["status"] for r in second}, {"verified_existing"})
            self.assertEqual(len(second), len(paths))

    def test_scan_excludes_the_non_date_sources_directory(self):
        base = ROOT / "evidence/crypto_paper_decision"
        self.assertTrue((base / "_sources").is_dir())
        # A plain string compare would place "_sources" after every date
        # ("_" sorts after every digit); DATE_DIR_RE must exclude it before
        # the >= min_date comparison ever runs.
        self.assertIsNone(LIN.DATE_DIR_RE.match("_sources"))
        with tempfile.TemporaryDirectory() as tmp:
            results = LIN.scan_crypto_decision_evidence("2026-09-19", lineage_root=Path(tmp), context=self.ctx)
        self.assertTrue(all("_sources" not in r["packet"] for r in results))

    def test_scan_min_date_excludes_earlier_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = LIN.scan_crypto_decision_evidence("2026-09-19", lineage_root=Path(tmp), context=self.ctx)
        self.assertGreater(len(results), 0)
        self.assertTrue(all("/2026-09-19/" in r["packet"] for r in results))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(LIN.scan_crypto_decision_evidence("2999-01-01", lineage_root=Path(tmp)), [])

    def test_cli_scan_kind_matches_the_workflow_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(LIN.main(["crypto-decision-scan", "--min-date", "2026-09-19",
                                           "--lineage-root", str(tmp)]), 0)
            summary = json.load(io.StringIO(out.getvalue()))["summary"]
            self.assertEqual(summary.get("FAILED", 0), 0)
            self.assertGreater(summary.get("written", 0), 0)
        workflow = (ROOT / ".github/workflows/rule-lineage-crypto-paper-decision.yml").read_text(encoding="utf-8")
        self.assertIn("governance/rule_lineage_producers.py crypto-decision-scan --min-date", workflow)


class CryptoPaperDecisionLineageWorkflowTests(unittest.TestCase):
    """.github/workflows/rule-lineage-crypto-paper-decision.yml wiring."""

    def setUp(self):
        self.text = (ROOT / ".github/workflows/rule-lineage-crypto-paper-decision.yml").read_text(encoding="utf-8")
        self.workflow = yaml.safe_load(self.text)

    def test_triggered_by_the_capture_workflow_and_dispatch(self):
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertEqual(triggers["workflow_run"]["workflows"],
                          ["P9-06 Upbit Realtime WebSocket Bounded Capture"])
        self.assertEqual(triggers["workflow_run"]["types"], ["completed"])
        self.assertIn("workflow_dispatch", triggers)

    def test_runs_on_cancelled_capture_runs_not_only_success(self):
        # The entire point: a cancelled capture run is exactly the case that
        # went silent on 2026-09-18/19, so this job must not skip it.
        condition = self.workflow["jobs"]["sidecars"]["if"]
        self.assertNotIn("conclusion == 'success'", condition)
        self.assertIn("workflow_dispatch", condition)

    def test_backfill_precedes_coverage_check_precedes_commit(self):
        steps = self.workflow["jobs"]["sidecars"]["steps"]
        ids = [step.get("id") for step in steps]
        names = [step.get("name") for step in steps]
        backfill = ids.index("backfill")
        coverage = names.index("Fail loudly if crypto PAPER decision sidecar coverage is not full")
        commit = names.index("Commit append-only rule lineage sidecars")
        self.assertLess(backfill, coverage)
        self.assertLess(coverage, commit)
        # A coverage-check failure must never suppress the commit of
        # whatever the backfill step did successfully write.
        self.assertEqual(steps[coverage]["if"], "always()")
        self.assertEqual(steps[commit]["if"], "always()")

    def test_coverage_check_reads_the_same_window_the_backfill_wrote(self):
        steps = self.workflow["jobs"]["sidecars"]["steps"]
        coverage = next(s for s in steps
                         if s.get("name") == "Fail loudly if crypto PAPER decision sidecar coverage is not full")
        self.assertIn("check_rule_lineage_sidecar_coverage.py", coverage["run"])
        self.assertIn("steps.backfill.outputs.min_date", coverage["run"])

    def test_only_touches_rule_lineage_evidence_and_no_secrets(self):
        runs = "\n".join(step.get("run", "") for step in self.workflow["jobs"]["sidecars"]["steps"])
        self.assertIn("git add evidence/rule_lineage/crypto_paper_decision", runs)
        self.assertNotIn("git add evidence/crypto_paper_decision", runs)
        self.assertNotIn("decision/crypto_paper_decision_snapshot.py", runs)
        self.assertNotIn("secrets.", json.dumps(self.workflow))
        self.assertEqual(self.workflow["permissions"], {"contents": "write"})

    def test_capture_workflow_own_name_matches_what_this_file_watches(self):
        capture = yaml.safe_load((ROOT / ".github/workflows/upbit-realtime-capture.yml").read_text(encoding="utf-8"))
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertEqual(triggers["workflow_run"]["workflows"], [capture["name"]])


class RuleLineageSidecarCoverageTests(unittest.TestCase):
    """.github/scripts/check_rule_lineage_sidecar_coverage.py."""

    def test_full_coverage_over_every_retained_crypto_packet(self):
        # Sanity check against the real, committed evidence tree (read-only):
        # every retained packet from 2026-09-15 onward must have a sidecar --
        # this is the exact backfilled range for the 2026-09-18/19 gap.
        result = COVERAGE.measure(ROOT, "2026-09-15")
        self.assertEqual(result["status"], COVERAGE.FULL)
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["total_packets"], result["total_with_sidecar"])
        self.assertGreater(result["total_packets"], 0)
        for date, counts in result["per_date"].items():
            self.assertEqual(counts["packets"], counts["with_sidecar"], date)

    def test_detects_a_dropped_date_and_reports_it_by_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            covered_dir = root / "evidence/crypto_paper_decision/2026-09-17/1200/gen-covered"
            dropped_dir = root / "evidence/crypto_paper_decision/2026-09-18/1300/gen-dropped"
            covered_dir.mkdir(parents=True)
            dropped_dir.mkdir(parents=True)
            (covered_dir / "packet.json").write_text(json.dumps({"payload_sha256": "a" * 64}), encoding="utf-8")
            (dropped_dir / "packet.json").write_text(json.dumps({"payload_sha256": "b" * 64}), encoding="utf-8")
            sidecar_dir = root / "evidence/rule_lineage/crypto_paper_decision/2026-09-17/1200" / ("a" * 64)
            sidecar_dir.mkdir(parents=True)
            (sidecar_dir / "registry-c.json").write_text("{}", encoding="utf-8")
            # 2026-09-18's own sidecar directory is never created -- the
            # exact shape of the 09-18 incident.
            result = COVERAGE.measure(root, "2026-09-17")
        self.assertEqual(result["status"], COVERAGE.DROPPED)
        self.assertEqual(result["total_packets"], 2)
        self.assertEqual(result["total_with_sidecar"], 1)
        self.assertEqual(result["per_date"], {
            "2026-09-17": {"packets": 1, "with_sidecar": 1},
            "2026-09-18": {"packets": 1, "with_sidecar": 0},
        })
        self.assertEqual(result["missing"], ["evidence/crypto_paper_decision/2026-09-18/1300/gen-dropped/packet.json"])

    def test_excludes_the_non_date_sources_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources_dir = root / "evidence/crypto_paper_decision/_sources/sha256/deadbeef"
            sources_dir.mkdir(parents=True)
            (sources_dir / "packet.json").write_text(json.dumps({"payload_sha256": "a" * 64}), encoding="utf-8")
            result = COVERAGE.measure(root, "2026-09-01")
        self.assertEqual(result["total_packets"], 0)
        self.assertEqual(result["status"], COVERAGE.FULL)

    def test_cli_exit_codes_and_step_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "evidence/crypto_paper_decision/2026-09-19/0100/gen").mkdir(parents=True)
            (root / "evidence/crypto_paper_decision/2026-09-19/0100/gen/packet.json").write_text(
                json.dumps({"payload_sha256": "a" * 64}), encoding="utf-8")
            summary_path = root / "step_summary.txt"
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = COVERAGE.main(["--min-date", "2026-09-19", "--root", str(root)])
            self.assertEqual(exit_code, 1)  # missing its sidecar -> loud failure
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(COVERAGE.main(["--min-date", "not-a-date", "--root", str(root)]), 2)

    def test_full_coverage_cli_exits_zero(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            exit_code = COVERAGE.main(["--min-date", "2026-09-15", "--root", str(ROOT)])
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(out.getvalue())["status"], COVERAGE.FULL)


def _walk(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


if __name__ == "__main__":
    unittest.main()
