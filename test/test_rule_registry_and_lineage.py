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

# Hashes of the CIO workspace originals named in the implementation order.
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
    "USER_RATIFICATION_RULE_GOVERNANCE_EVIDENCE_GATED_ADJUSTMENT_20260915.json": "c3f1e78ca987760af205807f67e9b56e8e7bd0078cbb87ac566b49767a855f9b",
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
    """A minimal root holding the registry's source records and bindings."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        registry = _registry()
        paths = {s["repo_path"] for row in registry["rules"] for s in row["source_records"]}
        paths |= {b["path"] for row in registry["rules"] for b in row["implementation_bindings"]}
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
        self.assertEqual(len(ids), 13)
        status = {row["rule_id"]: row["status"] for row in registry["rules"]}
        self.assertEqual(status["RULE.ROTATION.US.V1P"], "PROVISIONAL")
        self.assertEqual(status["RULE.ROTATION.KR.V1T"], "TEMPORARY")
        self.assertEqual(
            {k for k, v in status.items() if v == "RATIFIED"},
            set(REG.REQUIRED_RULE_IDS) - {"RULE.ROTATION.US.V1P", "RULE.ROTATION.KR.V1T"},
        )

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

    def test_rotation_record_is_the_timestamp_corrected_version(self):
        rotation = _row(_registry(), "RULE.ROTATION.CRYPTO.V1")["source_records"][0]
        self.assertTrue(rotation["sha256"].startswith("c6f5dbbe"))
        self.assertNotIn("68ca157398e2", rotation["sha256"])

    def test_copied_authority_records_contain_no_secret_like_values(self):
        for row in _registry()["rules"]:
            for source in row["source_records"]:
                text = (ROOT / source["repo_path"]).read_text(encoding="utf-8")
                self.assertIsNone(SECRET_LIKE.search(text), source["repo_path"])

    def test_triggers_only_where_records_state_them(self):
        registry = _registry()
        pending = {row["rule_id"] for row in registry["rules"] if row["trigger_pending_user_confirmation"]}
        self.assertEqual(pending, {
            "RULE.ALLOCATION.V2", "RULE.LIQUIDITY.KRUS.V1", "RULE.CRYPTO.FRESHNESS.PER_MARKET.V1",
            "RULE.US.SESSION_CALENDAR.V1", "RULE.CRYPTO.TAXONOMY.ADD_20260914",
            "RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1", "RULE.ROTATION.RELEASE_HANDLING.V1",
            "RULE.GOVERNANCE.EVIDENCE_GATED.V1",
        })
        for row in registry["rules"]:
            self.assertEqual(row["review_triggers"] is None, row["trigger_pending_user_confirmation"])
        sample = _row(registry, "RULE.ROTATION.CRYPTO.V1")["minimum_sample"]
        self.assertEqual(sample["value"], 10)
        others = [row["rule_id"] for row in registry["rules"]
                  if row["minimum_sample"] is not None and row["rule_id"] != "RULE.ROTATION.CRYPTO.V1"]
        self.assertEqual(others, [])

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

    def test_supersedes_hash_must_be_named_by_primary_record(self):
        _row(self.registry, "RULE.ALLOCATION.V2")["supersedes"]["sha256"] = "0" * 64
        self.assertInvalid(self.registry, "SUPERSEDES_NOT_NAMED_BY_PRIMARY_RECORD")

    def test_binding_claim_must_hold(self):
        _row(self.registry, "RULE.US.SESSION_CALENDAR.V1")["implementation_bindings"][1]["binds_record_sha256"] = True
        self.assertInvalid(self.registry, "BINDING_DOES_NOT_CONTAIN_RECORD_SHA")

    def test_status_and_family_vocabulary(self):
        _row(self.registry, "RULE.ROTATION.US.V1P")["status"] = "CONFIRMED"
        self.assertInvalid(self.registry, "STATUS_INVALID")


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
            expected = lineage_root / "crypto_paper_decision/2026-09-14/2113" / (packet["payload_sha256"] + ".json")
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
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(LIN.main(["crypto-decision", "--packet", "missing.json",
                                               "--lineage-root", str(blocker)]), 0)
            self.assertIn("RULE_LINEAGE_EMIT_FAILED", err.getvalue())
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

    def test_reference_scan_is_idempotent_and_skips_legacy(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()) as err:
            first = LIN.scan_reference_evidence("2026-08-28", lineage_root=Path(tmp))
            statuses = {r["status"] for r in first}
            self.assertEqual(statuses, {"written", "SKIPPED_SCHEMA"})
            second = LIN.scan_reference_evidence("2026-08-28", lineage_root=Path(tmp))
            self.assertEqual({r["status"] for r in second}, {"verified_existing", "SKIPPED_SCHEMA"})
            self.assertEqual(LIN.scan_reference_evidence("2999-01-01", lineage_root=Path(tmp)), [])
        self.assertEqual(err.getvalue(), "")


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
