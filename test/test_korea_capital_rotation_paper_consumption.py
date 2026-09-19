"""Stage1 display -> existing P2-03 read-only consumer; no live orders."""
from __future__ import annotations

import copy
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROOF = load("paper_rotation_proof", ".github/scripts/korea_capital_rotation_ledger_proof.py")
KCR = PROOF.KCR
FIXTURES = load("paper_rotation_fixtures", "test/test_korea_capital_rotation.py")
# Frozen dated copy of the reviewed #696 bytes; data/latest_* is a rolling pointer.
RUNTIME_PATH = ROOT / "evidence/regime/kr_information_system/2026-09-11/decision.json"
NOW = "2026-09-13T03:00:00Z"
# External Stage1/Root-reviewed #696 canonical pin, not calculated from input.
REVIEWED_RUNTIME_SHA256 = "a5f76eb6b38292185a893bcf9d321da7154777d5cd5e0cb7c2c994a1874aea44"


class PaperConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.raw = RUNTIME_PATH.read_bytes()
        self.runtime = json.loads(self.raw)
        self.policy = PROOF.RATIFIED.load_committed_policy()
        self.kwargs = {
            "expected_runtime_sha256": hashlib.sha256(self.raw).hexdigest(),
            "source_commit": "b1e904ce9af380f73fc7d0a54496907523d39180",
            "evaluation_at": NOW,
            "prior_date": "2026-09-10", "current_date": "2026-09-11",
            "rotation_policy": self.policy, "rotation_packet": None,
            "rotation_error": "NO_LEADERSHIP_EVIDENCE_FOR_DATE:2026-09-11",
        }

    def consume(self, runtime=None, **changes):
        raw = self.raw if runtime is None else json.dumps(runtime).encode()
        kwargs = self.kwargs | {"expected_runtime_sha256": hashlib.sha256(raw).hexdigest()} | changes
        return KCR.consume_paper_runtime_context(raw, **kwargs)

    def test_actual_display_consumption_does_not_manufacture_rotation(self):
        receipt = self.consume()
        context = receipt["market_context"]
        self.assertEqual(context["runtime_regime"], "NEUTRAL")
        self.assertEqual(context["direction"], "DETERIORATING")
        self.assertEqual(context["confidence"], "0.2")
        observation = context["current_observation"]
        self.assertEqual(observation["candidate_regime"], "RISK_OFF")
        self.assertEqual(observation["hysteresis"]["confirmation_count"], 1)
        self.assertEqual(observation["hysteresis"]["confirmation_required"], 2)
        self.assertIsNone(receipt["rotation"]["packet"])
        self.assertFalse(receipt["stage3_handoff"]["rotation_packet_ready_for_contract_validation"])
        self.assertEqual(receipt["rotation"]["status"], "WAIT_ROTATION_INPUT")
        self.assertIn("POLICY_NOT_EFFECTIVE_FOR_OBSERVATION_PAIR", receipt["rotation"]["reasons"])
        for key, value in receipt["authority"].items():
            self.assertIs(value, key == "paper_runtime_display_authorized")
        self.assertEqual(receipt["lineage"]["runtime_file_sha256"], self.kwargs["expected_runtime_sha256"])
        self.assertEqual(receipt["lineage"]["expected_runtime_file_sha256"], self.kwargs["expected_runtime_sha256"])
        self.assertNotIn("aggregation", receipt["market_context"])
        self.assertEqual(receipt, self.consume())
        self.assertEqual(receipt.pop("payload_sha256"), KCR.payload_sha256(receipt))

    def test_changed_bytes_cannot_reuse_approved_digest(self):
        changed = copy.deepcopy(self.runtime)
        changed["runtime_regime"] = "RISK_ON"
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "SOURCE_SHA_MISMATCH"):
            self.consume(changed, expected_runtime_sha256=self.kwargs["expected_runtime_sha256"])

    def test_rehashed_authority_escalation_and_numeric_booleans_rejected(self):
        for key in self.runtime["authority"]:
            with self.subTest(key=key):
                changed = copy.deepcopy(self.runtime)
                changed["authority"][key] = not changed["authority"][key]
                with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "AUTHORITY_MISMATCH"):
                    self.consume(changed)
        changed = copy.deepcopy(self.runtime)
        changed["authority"]["order_authorized"] = 0
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "AUTHORITY_MISMATCH"):
            self.consume(changed)

    def test_unqualified_or_wrong_market_or_non_natural_source_rejected(self):
        for key, value in (
            ("market", "US"), ("evidence_class", "SYNTHETIC"),
            ("actual_source_qualification", "PENDING"),
            ("runtime_decision_available", 1), ("decision_status", "UNKNOWN"),
            ("schema_version", "kr_paper_runtime_decision/4"), ("reasons", ["STALE"]),
        ):
            with self.subTest(key=key):
                changed = self.runtime | {key: value}
                with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "NOT_QUALIFIED"):
                    self.consume(changed)

    def test_date_mismatch_or_nonordered_pair_rejected(self):
        for changes in ({"current_date": "2026-09-10"}, {"prior_date": "2026-09-11"}):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "CONTEXT_DATE_MISMATCH"):
                    self.consume(**changes)

    def test_future_and_exact_expiry_and_naive_consumption_times_rejected(self):
        for time in ("2026-09-13T00:58:43Z", "2026-09-14T06:30:00Z", "2026-09-14T06:30:01Z"):
            with self.subTest(time=time):
                with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "NOT_AVAILABLE_OR_EXPIRED"):
                    self.consume(evaluation_at=time)
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "TIME_INVALID"):
            self.consume(evaluation_at="2026-09-13T03:00:00")
        self.consume(evaluation_at="2026-09-14T06:29:59Z")

    def test_rehashed_projection_and_lineage_tamper_rejected(self):
        for key, value, error in (
            ("confidence", "NaN", "CONFIDENCE_INVALID"),
            ("runtime_regime", "RISK_OFF", "CONFIRMED_REGIME_MISMATCH"),
            ("source_manifest_sha256", "bad", "LINEAGE_INVALID"),
        ):
            with self.subTest(key=key):
                with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, error):
                    self.consume(self.runtime | {key: value})
        changed = copy.deepcopy(self.runtime)
        changed["session_boundary_freshness"]["derived_ttl_seconds"] += 1
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "TTL_MISMATCH"):
            self.consume(changed)

    def test_missing_rotation_needs_reason_and_pinned_commit(self):
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "MISSING_REASON_REQUIRED"):
            self.consume(rotation_error=None)
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "SOURCE_COMMIT_INVALID"):
            self.consume(source_commit="main")
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "NOT_RATIFIED"):
            self.consume(rotation_policy=self.policy | {"approval_status": "UNRATIFIED"})

    def test_existing_synthetic_packet_is_validated_not_recomputed_from_regime(self):
        # Synthetic-only positive branch: legacy rotation fixture, not an
        # assertion of an actual post-ratification natural observation pair.
        value, policy = FIXTURES.make_bundle()
        packet = KCR.build_packet(value, policy)
        runtime = copy.deepcopy(self.runtime)
        runtime["current_observation"]["as_of_date"] = "2026-08-20"
        runtime["session_boundary_freshness"]["context_session_date"] = "2026-08-20"
        runtime["session_boundary_freshness"]["context_session_close_at"] = "2026-08-20T06:30:00Z"
        runtime["session_boundary_freshness"]["derived_ttl_seconds"] = 25 * 86400
        kwargs = {"prior_date": "2026-08-18", "current_date": "2026-08-20", "rotation_policy": policy,
                  "rotation_packet": packet, "rotation_error": None}
        receipt = self.consume(runtime, **kwargs)
        self.assertEqual(receipt["rotation"]["packet"], packet)
        self.assertEqual(receipt["lineage"]["rotation_packet_sha256"], packet["payload_sha256"])
        self.assertIn("ROTATION_BREADTH_NOT_AVAILABLE", receipt["rotation"]["reasons"])
        changed = copy.deepcopy(runtime)
        changed["runtime_regime"] = changed["paper_regime"] = "RISK_ON"
        changed["current_observation"]["confirmed_regime"] = "RISK_ON"
        second = self.consume(changed, **kwargs)
        self.assertEqual(second["rotation"]["packet"], packet)
        self.assertFalse(second["stage3_handoff"]["entry_authorized"])
        bad = copy.deepcopy(packet)
        bad["authority"]["trading_authorized"] = True
        with self.assertRaises(KCR.KoreaCapitalRotationError):
            self.consume(runtime, **(kwargs | {"rotation_packet": bad}))
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "BINDING_MISMATCH"):
            self.consume(runtime, **(kwargs | {"prior_date": "2026-08-19"}))

    def test_synthetic_complete_rotation_is_ready_only_for_stage3_validation(self):
        value, policy = FIXTURES.make_bundle()
        market = FIXTURES.forward_live_market("a" * 64, "2026-08-20", "2026-08-20T18:00:00+09:00")
        value["coverage_context"]["breadth"] = FIXTURES.breadth_context(
            "AVAILABLE", True, kosdaq=market, kospi=market,
        )
        packet = KCR.build_packet(value, policy)
        runtime = copy.deepcopy(self.runtime)
        runtime["current_observation"]["as_of_date"] = "2026-08-20"
        runtime["session_boundary_freshness"]["context_session_date"] = "2026-08-20"
        runtime["session_boundary_freshness"]["context_session_close_at"] = "2026-08-20T06:30:00Z"
        runtime["session_boundary_freshness"]["derived_ttl_seconds"] = 25 * 86400
        receipt = self.consume(runtime, prior_date="2026-08-18", current_date="2026-08-20",
                               rotation_policy=policy, rotation_packet=packet, rotation_error=None)
        self.assertEqual(receipt["rotation"]["status"], "ROTATION_PACKET_AVAILABLE")
        self.assertEqual(receipt["rotation"]["packet"], packet)
        self.assertTrue(receipt["stage3_handoff"]["rotation_packet_ready_for_contract_validation"])
        self.assertFalse(receipt["stage3_handoff"]["entry_authorized"])
        self.assertFalse(receipt["authority"]["order_authorized"])
        self.assertFalse(receipt["authority"]["strategy_authorized"])


class PinnedCurrentRatifiedIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()

    def build(self, expected_runtime_sha256=REVIEWED_RUNTIME_SHA256, **changes):
        kwargs = {"source_commit": self.commit, "expected_runtime_sha256": expected_runtime_sha256,
                  "evaluation_at": NOW} | changes
        return PROOF.build_current_ratified_paper_consumption("2026-09-10", "2026-09-11", **kwargs)

    def test_real_current_ratified_path_reads_canonical_and_records_absence(self):
        with mock.patch.object(PROOF.KCR, "build_packet", wraps=PROOF.KCR.build_packet) as rank:
            receipt = self.build()
        rank.assert_not_called()  # no per-sector 09-11 packet: no invented ranks
        self.assertEqual(receipt["market_context"]["runtime_regime"], "NEUTRAL")
        self.assertIn("NO_LEADERSHIP_EVIDENCE_FOR_DATE:2026-09-11", receipt["rotation"]["reasons"])
        absent = [row["path"] for row in receipt["lineage"]["rotation_source_files"] if row["status"] == "ABSENT_AT_SOURCE_COMMIT"]
        self.assertIn("data/observations/korea_leadership_context/2026-09-11/packet.json", absent)
        self.assertEqual(receipt["lineage"]["runtime_source_commit"], self.commit)
        self.assertEqual(receipt["lineage"]["expected_runtime_file_sha256"], REVIEWED_RUNTIME_SHA256)
        self.assertEqual(receipt["lineage"]["reviewed_runtime_release"], PROOF.REVIEWED_PAPER_RUNTIME_RELEASE)
        with self.assertRaisesRegex(RuntimeError, "NOT_DESCENDANT_OF_REVIEWED_PUBLICATION"):
            self.build(source_commit=PROOF.REVIEWED_PAPER_RUNTIME_RELEASE["code_revision"])
        with self.assertRaisesRegex(RuntimeError, "REVIEWED_PUBLICATION_NOT_YET_AVAILABLE"):
            self.build(evaluation_at="2026-09-13T01:02:05Z")

    def test_source_policy_drift_fails_before_rotation_attempt(self):
        for expected, error in (("0" * 64, "EXPECTED_SHA_NOT_REVIEWED"), ("bad", "EXPECTED_SHA_INVALID")):
            with self.subTest(expected=expected), mock.patch.object(PROOF, "load_current_ratified_artifacts") as policy:
                with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, error):
                    self.build(expected_runtime_sha256=expected)
                policy.assert_not_called()
        # Change both source bytes and the caller's SHA. The separate #696
        # approval pin remains fixed, so this cannot self-approve a release.
        proposed = json.loads(RUNTIME_PATH.read_bytes())
        proposed["direction"] = "IMPROVING"
        proposed_bytes = json.dumps(proposed).encode()
        real_run = subprocess.run
        def changed_git_read(args, **kwargs):
            if args == ["git", "show", f"{self.commit}:data/latest_kr_paper_runtime_decision.json"]:
                return subprocess.CompletedProcess(args, 0, stdout=proposed_bytes, stderr=b"")
            return real_run(args, **kwargs)
        with mock.patch.object(PROOF.subprocess, "run", changed_git_read):
            with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "SOURCE_SHA_MISMATCH"):
                self.build()
            with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "EXPECTED_SHA_NOT_REVIEWED"):
                self.build(hashlib.sha256(proposed_bytes).hexdigest())
        def changed_approved_read(args, **kwargs):
            approved = PROOF.REVIEWED_PAPER_RUNTIME_RELEASE["publication_commit"]
            if args == ["git", "show", f"{approved}:data/latest_kr_paper_runtime_decision.json"]:
                return subprocess.CompletedProcess(args, 0, stdout=proposed_bytes, stderr=b"")
            return real_run(args, **kwargs)
        with mock.patch.object(PROOF.subprocess, "run", changed_approved_read):
            with self.assertRaisesRegex(RuntimeError, "REVIEWED_PUBLICATION_RECEIPT_MISMATCH"):
                self.build()
        with self.assertRaises(TypeError):
            PROOF.build_current_ratified_paper_consumption(
                "2026-09-10", "2026-09-11", source_commit=self.commit, evaluation_at=NOW,
            )
        original = Path.read_bytes
        def changed(path):
            raw = original(path)
            return raw + b" " if path == PROOF.RATIFIED.POLICY_PATH else raw
        with mock.patch.object(Path, "read_bytes", changed):
            with self.assertRaisesRegex(RuntimeError, "PAPER_LOCAL_SOURCE_DRIFT"):
                self.build()

    def test_external_cli_and_fresh_process_readback_preserve_pointer(self):
        pointer = ROOT / "data/latest_korea_rotation.json"
        before = pointer.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "consumer.json"
            command = [sys.executable, str(ROOT / ".github/scripts/korea_capital_rotation_ledger_proof.py"),
                       "--current-ratified-policy", "--prior-date", "2026-09-10", "--current-date", "2026-09-11",
                       "--paper-runtime-source-commit", self.commit, "--evaluation-at", NOW,
                       "--expected-paper-runtime-sha256", REVIEWED_RUNTIME_SHA256,
                       "--paper-consumer-out", str(output)]
            first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)
            stored = json.loads(output.read_bytes())
            self.assertEqual(json.loads(first.stdout)["payload_sha256"], stored["payload_sha256"])
            self.assertEqual(stored, self.build())
            readback = subprocess.run([sys.executable, "-c", "import json,sys; p=json.load(open(sys.argv[1])); print(p['payload_sha256'])", str(output)],
                                      capture_output=True, text=True, check=True)
            self.assertEqual(readback.stdout.strip(), stored["payload_sha256"])
        self.assertEqual(pointer.read_bytes(), before)

    def test_paper_mode_forbids_pointer_packet_substitution_and_partial_options(self):
        base = [sys.executable, str(ROOT / ".github/scripts/korea_capital_rotation_ledger_proof.py"),
                "--current-ratified-policy", "--prior-date", "2026-09-10", "--current-date", "2026-09-11",
                "--paper-runtime-source-commit", self.commit,
                "--expected-paper-runtime-sha256", REVIEWED_RUNTIME_SHA256]
        for suffix in ([], ["--paper-consumer-out", "/tmp/unused-paper-consumer.json", "--evaluation-at", NOW, "--commit-pointer"],
                       ["--paper-consumer-out", "/tmp/unused-paper-consumer.json", "--evaluation-at", NOW, "--packet-out", "/tmp/unused-packet.json"]):
            with self.subTest(suffix=suffix):
                result = subprocess.run(base + suffix, cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "must-not-exist.json"
            suffix = ["--paper-consumer-out", str(out), "--evaluation-at", NOW]
            missing = subprocess.run(base[:-2] + suffix, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)
            self.assertIn("--expected-paper-runtime-sha256", missing.stderr)
            self.assertFalse(out.exists())
            mismatch = subprocess.run(base[:-1] + ["0" * 64] + suffix, cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn("PAPER_RUNTIME_EXPECTED_SHA_NOT_REVIEWED", mismatch.stderr)
            self.assertFalse(out.exists())
            proposed = json.loads(RUNTIME_PATH.read_bytes())
            proposed["direction"] = "IMPROVING"
            forged_bytes = json.dumps(proposed).encode()
            forged_sha = hashlib.sha256(forged_bytes).hexdigest()
            with mock.patch.object(PROOF, "write_external_ratified_packet") as writer:
                with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "EXPECTED_SHA_NOT_REVIEWED"):
                    PROOF.run_current_ratified_paper_consumption(
                        "2026-09-10", "2026-09-11", out,
                        source_commit=self.commit, expected_runtime_sha256=forged_sha, evaluation_at=NOW,
                    )
                writer.assert_not_called()
                self.assertFalse(out.exists())


# Exact seed-readiness predecessor head this connection is built on.
BASE_HEAD = "3a1a4f019e10984a48f76833840d4bd8393f36d2"
SYNTHETIC_SEED_COMMIT = "c" * 40  # SYNTHETIC_TEST_ONLY source identity, never natural proof


def synthetic_seed_summary(inner, observation_date, fetch_prior_date, *, outcome="populated"):
    summary = {
        "schema_version": "korea_leadership_live_attempt/1",
        "observation_date": observation_date,
        "prior_date": fetch_prior_date,
        "outcome": outcome,
        "reason": None if outcome == "populated" else "SYNTHETIC_BLOCKED_FIXTURE",
        "leadership_packet_sha256": None if inner is None else inner["payload_sha256"],
        "leadership_packet": inner,
        "markets": {
            market: {"raw_response_sha256": {"prior": "1" * 64, "current": "2" * 64}}
            for market in ("KOSDAQ", "KOSPI")
        },
        "generated_at": f"{observation_date}T09:05:00Z",
    }
    summary["payload_sha256"] = KCR.payload_sha256(summary)
    return summary


def synthetic_seed_input(summary):
    raw = (json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    ready = summary["outcome"] == "populated" and summary["leadership_packet"] is not None
    return {
        "observation_date": summary["observation_date"],
        "seed_fetch_prior_date": summary["prior_date"],
        "source_commit": SYNTHETIC_SEED_COMMIT,
        "source_path": f"data/observations/korea_leadership_context/{summary['observation_date']}/packet.json",
        "availability": "PINNED",
        "committed_bytes": raw,
        "local_file_sha256": hashlib.sha256(raw).hexdigest(),
        "verified_summary": json.loads(raw),
        "predecessor_not_ready_reason": None if ready else f"SEED_NOT_USABLE:outcome={summary['outcome']}",
    }


class UsableSeedRotationConnectionTests(unittest.TestCase):
    """SYNTHETIC_TEST_ONLY: legal rotation fixtures in memory, no natural evidence."""

    def setUp(self):
        self.value, self.policy = FIXTURES.make_bundle()
        market = FIXTURES.forward_live_market("a" * 64, "2026-08-20", "2026-08-20T18:00:00+09:00")
        self.available = copy.deepcopy(self.value)
        self.available["coverage_context"]["breadth"] = FIXTURES.breadth_context(
            "AVAILABLE", True, kosdaq=market, kospi=market,
        )
        self.prior_summary = synthetic_seed_summary(self.value["prior_observation"], "2026-08-18", "2026-08-14")
        self.current_summary = synthetic_seed_summary(self.value["current_observation"], "2026-08-20", "2026-08-19")
        self.prior_seed = synthetic_seed_input(self.prior_summary)
        self.current_seed = synthetic_seed_input(self.current_summary)

    def consume(self, prior=None, current=None, **kwargs):
        kwargs = {"rotation_policy": self.policy, "rotation_packet": None} | kwargs
        return KCR.consume_usable_seed_pair(
            self.prior_seed if prior is None else prior,
            self.current_seed if current is None else current, **kwargs,
        )

    def test_default_packet_bytes_match_base_head_consumer(self):
        shown = subprocess.run(
            ["git", "show", f"{BASE_HEAD}:rotation/korea_capital_rotation.py"], cwd=ROOT, capture_output=True,
        )
        if shown.returncode != 0:
            self.skipTest(f"BASE_HEAD_SOURCE_UNAVAILABLE:{BASE_HEAD}")
        base = types.ModuleType("korea_capital_rotation_base_head")
        base.__file__ = str(ROOT / "rotation" / "korea_capital_rotation.py")
        exec(compile(shown.stdout, "korea_capital_rotation_base_head", "exec"), base.__dict__)
        for label, value in (("unknown_breadth", self.value), ("available_breadth", self.available)):
            with self.subTest(label=label):
                value_before = KCR.canonical_json(value)
                policy_before = KCR.canonical_json(self.policy)
                expected = base.canonical_json(
                    base.build_packet(copy.deepcopy(value), copy.deepcopy(self.policy))
                ).encode("utf-8")
                packet = KCR.build_packet(value, self.policy)
                self.assertEqual(KCR.canonical_json(packet).encode("utf-8"), expected)
                self.assertEqual(base.validate_packet(copy.deepcopy(packet)), packet)
                receipt = self.consume(rotation_packet=packet)
                self.assertEqual(KCR.canonical_json(receipt["rotation"]["packet"]).encode("utf-8"), expected)
                self.assertEqual(KCR.canonical_json(value), value_before)
                self.assertEqual(KCR.canonical_json(self.policy), policy_before)

    def test_ready_seed_pair_preserves_leadership_lineage_through_existing_consumer(self):
        packet = KCR.build_packet(copy.deepcopy(self.available), self.policy)
        seeds_before = copy.deepcopy((self.prior_seed, self.current_seed))
        receipt = self.consume(rotation_packet=packet)
        self.assertEqual((self.prior_seed, self.current_seed), seeds_before)
        self.assertEqual(receipt["seed_pair_readiness"], "READY")
        self.assertEqual(receipt["rotation"]["status"], "ROTATION_PACKET_AVAILABLE")
        self.assertEqual(receipt["rotation"]["reasons"], [])
        self.assertEqual(receipt["rotation"]["packet"], packet)
        self.assertNotIn("seeds", receipt["rotation"]["packet"])
        for label, summary, seed in (
            ("prior", self.prior_summary, self.prior_seed), ("current", self.current_summary, self.current_seed),
        ):
            record = receipt["seeds"][label]
            inner = summary["leadership_packet"]
            self.assertEqual(record["readiness"], "READY")
            self.assertEqual(record["source_commit"], SYNTHETIC_SEED_COMMIT)
            self.assertEqual(record["source_path"], seed["source_path"])
            self.assertEqual(record["seed_fetch_prior_date"], summary["prior_date"])
            self.assertEqual(record["committed_file_sha256_observed"], hashlib.sha256(seed["committed_bytes"]).hexdigest())
            self.assertEqual(record["hash_role"], "OBSERVED_FROM_SOURCE_BYTES_NOT_INDEPENDENTLY_APPROVED")
            self.assertEqual(record["summary"]["payload_sha256"], summary["payload_sha256"])
            self.assertEqual(record["summary"]["generated_at"], summary["generated_at"])
            self.assertEqual(record["summary"]["leadership_packet_sha256"], inner["payload_sha256"])
            self.assertEqual(record["leadership"]["payload_sha256"], inner["payload_sha256"])
            self.assertEqual(record["leadership"]["available_at"], inner["available_at"])
            self.assertEqual(record["leadership"]["policy_sha256"], inner["policy"]["policy_sha256"])
            self.assertEqual(receipt["lineage"][f"{label}_upstream_packet_sha256"], inner["payload_sha256"])
            self.assertEqual(packet["lineage"][f"{label}_upstream_packet_sha256"], inner["payload_sha256"])
        self.assertEqual(receipt["lineage"]["upstream_leadership_policy_sha256"], self.policy["upstream_leadership_policy_sha256"])
        self.assertEqual(receipt["lineage"]["rotation_policy_sha256"], packet["lineage"]["rotation_policy_sha256"])
        self.assertEqual(receipt["rotation"]["policy_effective_from"], self.policy["effective_from"])
        self.assertEqual(receipt["rotation"]["policy_ratified_at_utc"], self.policy["ratified_at_utc"])
        # Original relative strengths trace to the seed rows; TOP/BOTTOM are the
        # existing consumer's own buckets, not re-derived here.
        prior_rows = {row["series_identity"]: row for row in self.value["prior_observation"]["relative_strength_observations"]}
        current_rows = {row["series_identity"]: row for row in self.value["current_observation"]["relative_strength_observations"]}
        for scope in receipt["rotation"]["packet"]["benchmark_scopes"]:
            for row in scope["theme_observations"]:
                series = row["series_identity"]
                self.assertEqual(row["prior_relative_strength_vs_benchmark"], KCR._render(Decimal(prior_rows[series]["relative_strength_vs_benchmark"]), 12))
                self.assertEqual(row["current_relative_strength_vs_benchmark"], KCR._render(Decimal(current_rows[series]["relative_strength_vs_benchmark"]), 12))
        scopes = receipt["rotation"]["packet"]["benchmark_scopes"]
        self.assertEqual([scope["top_themes"] for scope in scopes], [["THEME.KR.KOSPI.BIO"], ["THEME.KR.KOSDAQ.SEMICONDUCTOR"]])
        self.assertEqual([scope["bottom_themes"] for scope in scopes], [["THEME.KR.KOSPI.DEFENSE"], ["THEME.KR.KOSDAQ.ROBOTICS"]])
        self.assertEqual(
            {key for key, value in receipt["authority"].items() if value is not False},
            {"external_ratified_rotation_policy_only"},
        )
        self.assertEqual(receipt.pop("payload_sha256"), KCR.payload_sha256(receipt))

    def test_blocked_or_unpinned_seed_is_not_ready_and_forbids_rotation(self):
        packet = KCR.build_packet(copy.deepcopy(self.available), self.policy)
        blocked = synthetic_seed_input(synthetic_seed_summary(None, "2026-08-20", "2026-08-19", outcome="blocked"))
        receipt = self.consume(current=blocked)
        record = receipt["seeds"]["current"]
        self.assertEqual(record["readiness"], "NOT_READY")
        self.assertEqual(record["reasons"], ["SEED_NOT_USABLE:outcome=blocked"])
        self.assertEqual(record["summary"]["outcome"], "blocked")
        self.assertIsNone(record["leadership"])
        self.assertEqual(receipt["seeds"]["prior"]["readiness"], "READY")
        self.assertEqual(receipt["seed_pair_readiness"], "NOT_READY")
        self.assertEqual(receipt["rotation"]["status"], "WAIT_ROTATION_INPUT")
        self.assertIsNone(receipt["rotation"]["packet"])
        self.assertIn("USABLE_SEED_PAIR_NOT_READY", receipt["rotation"]["reasons"])
        self.assertIn("current:SEED_NOT_USABLE:outcome=blocked", receipt["rotation"]["reasons"])
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "ROTATION_ATTEMPT_FORBIDDEN"):
            self.consume(current=blocked, rotation_packet=packet)
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "READINESS_DERIVATION_MISMATCH"):
            self.consume(current=blocked | {"predecessor_not_ready_reason": None})
        unmaterialized = self.current_seed | {
            "availability": "COMMITTED_NOT_MATERIALIZED", "local_file_sha256": None,
            "verified_summary": None, "predecessor_not_ready_reason": None,
        }
        absent = unmaterialized | {"availability": "ABSENT_AT_SOURCE_COMMIT", "committed_bytes": None}
        for seed, reasons, committed_sha in (
            (unmaterialized, ["NATURAL_READBACK_UNAVAILABLE_MATERIALIZATION"], hashlib.sha256(self.current_seed["committed_bytes"]).hexdigest()),
            (absent, ["SEED_OBSERVATION_ABSENT_AT_SOURCE_COMMIT"], None),
        ):
            with self.subTest(availability=seed["availability"]):
                record = self.consume(current=seed)["seeds"]["current"]
                self.assertEqual(record["readiness"], "NOT_READY")
                self.assertEqual(record["reasons"], reasons)
                self.assertEqual(record["committed_file_sha256_observed"], committed_sha)
                self.assertIsNone(record["summary"])
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "UNPINNED_SUMMARY_FORBIDDEN"):
            self.consume(current=unmaterialized | {"verified_summary": self.current_summary})

    def test_malformed_tampered_wrong_date_or_wrong_policy_seed_is_rejected(self):
        tampered = copy.deepcopy(self.current_summary)
        tampered["leadership_packet"]["relative_strength_observations"][2]["relative_strength_vs_benchmark"] = "0.9"
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "HASH_MISMATCH"):
            self.consume(current=synthetic_seed_input(tampered))
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "VERIFIED_SUMMARY_BYTES_MISMATCH"):
            self.consume(current=self.current_seed | {"verified_summary": self.prior_summary})
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "SUMMARY_IDENTITY_MISMATCH"):
            self.consume(current=self.current_seed | {"seed_fetch_prior_date": "2026-08-18"})
        malformed_inner = copy.deepcopy(self.value["current_observation"])
        malformed_inner["status"] = "CLASSIFIED"
        FIXTURES.rehash(malformed_inner)
        malformed = synthetic_seed_input(synthetic_seed_summary(malformed_inner, "2026-08-20", "2026-08-19"))
        record = self.consume(current=malformed)["seeds"]["current"]
        self.assertEqual(record["readiness"], "NOT_READY")
        self.assertTrue(record["reasons"][0].startswith("SEED_LEADERSHIP_PACKET_INVALID:UPSTREAM_IDENTITY_INVALID"))
        packet = KCR.build_packet(copy.deepcopy(self.available), self.policy)
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "LINEAGE_MISMATCH:rotation_policy"):
            self.consume(rotation_packet=packet, rotation_policy=self.policy | {"policy_id": "POLICY.P2.03.OTHER"})
        foreign = copy.deepcopy(packet)
        foreign["lineage"]["current_upstream_packet_sha256"] = "f" * 64
        FIXTURES.rehash_output(foreign)
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "LINEAGE_MISMATCH:current_upstream_packet_sha256"):
            self.consume(rotation_packet=foreign)
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "PAIR_DATE_ORDER_INVALID"):
            self.consume(prior=self.current_seed, current=self.prior_seed)

    def test_ready_seeds_without_breadth_or_policy_effectivity_remain_not_ready(self):
        receipt = self.consume(rotation_packet=KCR.build_packet(copy.deepcopy(self.value), self.policy))
        self.assertEqual(receipt["seed_pair_readiness"], "READY")
        self.assertEqual(receipt["rotation"]["status"], "WAIT_ROTATION_INPUT")
        self.assertEqual(receipt["rotation"]["reasons"], ["ROTATION_BREADTH_NOT_AVAILABLE"])
        pre_effective = self.policy | {"effective_from": "2026-08-19"}
        packet = KCR.build_packet(copy.deepcopy(self.available), pre_effective)
        receipt = self.consume(rotation_packet=packet, rotation_policy=pre_effective)
        self.assertFalse(receipt["rotation"]["policy_effective_for_pair"])
        self.assertEqual(
            receipt["rotation"]["reasons"],
            ["POLICY_NOT_EFFECTIVE_FOR_OBSERVATION_PAIR", "POLICY_NOT_EFFECTIVE"],
        )
        receipt = self.consume(rotation_error="NO_BREADTH_OR_PAIR_SYNTHETIC")
        self.assertEqual(receipt["rotation"]["reasons"], ["ROTATION_PACKET_UNAVAILABLE", "NO_BREADTH_OR_PAIR_SYNTHETIC"])
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "MISSING_REASON_REQUIRED"):
            self.consume()


if __name__ == "__main__":
    unittest.main()
