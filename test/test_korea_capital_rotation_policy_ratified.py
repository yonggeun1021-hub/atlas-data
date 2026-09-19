#!/usr/bin/env python3
"""P2-03 rotation-policy RATIFICATION MATERIALIZATION regression.

Separate, bounded slice from the candidate lane (PR #669, merged;
rotation/korea_capital_rotation_policy_candidate.py / test/test_korea_
capital_rotation_policy_candidate.py stay unmodified, historical UNRATIFIED
evidence). Proves, against the real, unmodified rotation/korea_capital_
rotation.py functions:

- The ratified artifact's identity/hashes/effective_from are all mechanically
  recomputed, never hand-typed, and match the real external ratification
  trail (PR #669 comment 5643258809, 2026-09-12T03:47:23Z) -- never the
  tainted 2026-08-22 self-declared timestamp or either candidate-authoring
  placeholder (2026-09-11, 2026-09-12).
- `effective_from` is the first real KRX trading session after the
  ratification instant, mechanically resolved from the real, committed
  official KRX holiday capture -- not weekday arithmetic.
- The ratified policy stays honestly inert for any observation pair that
  predates `effective_from` (the CIO's "still closed" rule: ratification
  alone is not a natural proof), and only becomes `effective=True` for a
  structurally in-interval pair -- proving the mechanism, not claiming a
  real natural sample exists yet.
- The real P2-01 authority registry stays untouched (0 records).
- No schedule/cron file is touched by this slice.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RATIFIED_SCRIPT = ROOT / "rotation" / "korea_capital_rotation_policy_ratified.py"
CANDIDATE_SCRIPT = ROOT / "rotation" / "korea_capital_rotation_policy_candidate.py"
KCR_SCRIPT = ROOT / "rotation" / "korea_capital_rotation.py"
TTA_SCRIPT = ROOT / "rotation" / "theme_taxonomy_authority.py"
LEADERSHIP_POLICY_PATH = ROOT / "config" / "korea_leadership_policy.json"
P2_03_WORKFLOW = ROOT / ".github" / "workflows" / "p2-03-korea-observation-pair.yml"
LEADERSHIP_WORKFLOW = ROOT / ".github" / "workflows" / "korea-leadership-live-proof.yml"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RAT = load_module("korea_capital_rotation_policy_ratified", RATIFIED_SCRIPT)
CAND = load_module("korea_capital_rotation_policy_candidate", CANDIDATE_SCRIPT)
KCR = load_module("korea_capital_rotation_for_ratified_test", KCR_SCRIPT)
TTA = load_module("theme_taxonomy_authority_for_ratified_test", TTA_SCRIPT)

TAINTED_RATIFIED_AT_UTC = "2026-08-22T07:19:09Z"
CANDIDATE_AUTHORING_DATES = {"2026-09-11", "2026-09-12"}
REAL_RATIFIED_AT_UTC = "2026-09-12T03:47:23Z"
REAL_EFFECTIVE_FROM = "2026-09-14"
DECISION_TRAIL_URL = (
    "https://github.com/yonggeun1021-hub/atlas-data/pull/669"
    "#issuecomment-5643258809"
)

ALL_ARTIFACT_LOADERS = (
    RAT.load_committed_decision,
    RAT.load_committed_document,
    RAT.load_committed_binding,
    RAT.load_committed_policy,
)


class CommittedVsRebuiltTests(unittest.TestCase):
    def test_all_four_artifacts_byte_identical_to_rebuild(self):
        decision, document, binding, policy = RAT.build_all()
        self.assertEqual(decision, RAT.load_committed_decision())
        self.assertEqual(document, RAT.load_committed_document())
        self.assertEqual(binding, RAT.load_committed_binding())
        self.assertEqual(policy, RAT.load_committed_policy())

    def test_rebuild_is_deterministic(self):
        self.assertEqual(RAT.build_all(), RAT.build_all())


class EffectiveFromResolutionTests(unittest.TestCase):
    """Mechanically re-derive effective_from from the real KRX holiday
    capture -- do not just assert the stored value."""

    def test_effective_from_matches_committed_policy(self):
        resolved = RAT.resolve_effective_from()
        self.assertEqual(resolved, REAL_EFFECTIVE_FROM)
        self.assertEqual(RAT.load_committed_policy()["effective_from"], resolved)

    def test_ratification_date_itself_is_a_weekend_not_eligible(self):
        ratified_at = dt.datetime.fromisoformat(
            REAL_RATIFIED_AT_UTC.replace("Z", "+00:00")
        )
        ratified_at_kst = ratified_at.astimezone(dt.timezone(dt.timedelta(hours=9)))
        self.assertEqual(ratified_at_kst.date().isoformat(), "2026-09-12")
        self.assertGreaterEqual(ratified_at_kst.date().weekday(), 5)  # Sat/Sun

    def test_effective_from_is_a_real_weekday(self):
        resolved_date = dt.date.fromisoformat(REAL_EFFECTIVE_FROM)
        self.assertLess(resolved_date.weekday(), 5)

    def test_effective_from_strictly_after_ratification_calendar_date(self):
        ratified_at = dt.datetime.fromisoformat(
            REAL_RATIFIED_AT_UTC.replace("Z", "+00:00")
        )
        ratified_at_kst = ratified_at.astimezone(dt.timezone(dt.timedelta(hours=9)))
        resolved_date = dt.date.fromisoformat(REAL_EFFECTIVE_FROM)
        self.assertGreater(resolved_date, ratified_at_kst.date())


class NeverReusesTaintedOrPlaceholderValuesTests(unittest.TestCase):
    def test_ratified_at_is_the_real_decision_instant(self):
        for loader in ALL_ARTIFACT_LOADERS:
            document = loader()
            if "ratified_at_utc" in document:
                self.assertEqual(document["ratified_at_utc"], REAL_RATIFIED_AT_UTC)
                self.assertNotEqual(document["ratified_at_utc"], TAINTED_RATIFIED_AT_UTC)

    def test_effective_from_never_a_candidate_authoring_placeholder(self):
        for loader in ALL_ARTIFACT_LOADERS:
            document = loader()
            if "effective_from" in document:
                self.assertNotIn(document["effective_from"], CANDIDATE_AUTHORING_DATES)
                self.assertEqual(document["effective_from"], REAL_EFFECTIVE_FROM)

    def test_external_decision_trail_cited(self):
        decision = RAT.load_committed_decision()
        self.assertEqual(decision["external_decision_trail"], DECISION_TRAIL_URL)
        self.assertEqual(decision["superseded_candidate_pr"], 669)

    def test_ratified_by_is_the_real_cio_not_a_self_declared_script(self):
        for loader in ALL_ARTIFACT_LOADERS:
            document = loader()
            if "ratified_by" in document:
                self.assertEqual(document["ratified_by"], "Atlas CIO")


class CandidateLaneUnmodifiedTests(unittest.TestCase):
    """This is a SEPARATE bounded slice -- the candidate lane must stay
    exactly as historical UNRATIFIED evidence."""

    def test_candidate_documents_still_unratified(self):
        policy = CAND.load_committed_policy()
        self.assertEqual(policy["approval_status"], "UNRATIFIED")
        self.assertIsNone(policy["ratified_by"])
        self.assertIsNone(policy["ratified_at_utc"])

    def test_candidate_and_ratified_policies_are_different_files_and_ids(self):
        candidate_policy = CAND.load_committed_policy()
        ratified_policy = RAT.load_committed_policy()
        self.assertNotEqual(candidate_policy["policy_id"], ratified_policy["policy_id"])
        self.assertNotEqual(CAND.POLICY_CANDIDATE_PATH, RAT.POLICY_PATH)

    def test_ratified_mapping_matches_the_ratified_candidate_semantics(self):
        # Same 46-identity, top/bottom=3, gap=7 semantics the CIO ratified --
        # just materialized as real RATIFIED artifacts now.
        candidate_policy = CAND.load_committed_policy()
        ratified_policy = RAT.load_committed_policy()
        self.assertEqual(
            ratified_policy["maximum_calendar_gap_days"],
            candidate_policy["maximum_calendar_gap_days"],
        )
        for scope in ratified_policy["benchmark_scopes"]:
            self.assertEqual(scope["top_count"], 3)
            self.assertEqual(scope["bottom_count"], 3)
        candidate_series = sorted(
            m["series_identity"]
            for scope in candidate_policy["benchmark_scopes"]
            for m in scope["members"]
        )
        ratified_series = sorted(
            m["series_identity"]
            for scope in ratified_policy["benchmark_scopes"]
            for m in scope["members"]
        )
        self.assertEqual(candidate_series, ratified_series)
        self.assertEqual(len(ratified_series), 46)


class RealAuthorityRegistryUntouchedTests(unittest.TestCase):
    def test_registry_still_has_zero_records(self):
        self.assertEqual(TTA.load_registry()["records"], [])

    def test_no_schedule_cron_file_touched(self):
        for path in (P2_03_WORKFLOW, LEADERSHIP_WORKFLOW):
            text = path.read_text(encoding="utf-8")
            self.assertIn("workflow_dispatch:", text)
        # Leadership keeps its existing weekday schedule unchanged; the
        # combined pair workflow gains none.
        p2_03_text = P2_03_WORKFLOW.read_text(encoding="utf-8")
        lines = p2_03_text.splitlines()
        on_index = next(i for i, line in enumerate(lines) if line.strip() == "on:")
        block = []
        for line in lines[on_index + 1:]:
            if line and not line.startswith((" ", "\t")):
                break
            block.append(line)
        self.assertFalse(any("schedule:" in line for line in block))

    def test_combined_pair_workflow_builds_external_current_ratified_handoff(self):
        text = P2_03_WORKFLOW.read_text(encoding="utf-8")
        job = text.split("  korea-current-ratified-rotation-proof:\n", 1)[1]
        self.assertIn("    needs: korea-leadership-live-fetch\n", job)
        self.assertIn("git fetch origin main", job)
        self.assertIn("git reset --hard origin/main", job)
        self.assertIn(
            "python3 .github/scripts/korea_capital_rotation_ledger_proof.py",
            job,
        )
        self.assertIn("--current-ratified-policy", job)
        self.assertIn(
            '--packet-out "$RUNNER_TEMP/p2-03-current-ratified/packet.json"',
            job,
        )
        self.assertIn('= "ROTATION_BUCKETS_OBSERVED"', job)
        self.assertIn('= "true"', job)
        self.assertIn("public-main-commit.txt", job)
        self.assertIn(
            "p2-03-current-ratified-rotation-${{ github.run_id }}-${{ github.run_attempt }}",
            job,
        )
        self.assertIn("if-no-files-found: error", job)
        self.assertNotIn("--commit-pointer", job)


class RealProducerAcceptanceTests(unittest.TestCase):
    """Proven against the real, unmodified korea_capital_rotation.py --
    never a reimplementation of its rules."""

    @classmethod
    def setUpClass(cls):
        cls.contract = KCR.load_contract()
        cls.decision, cls.document, cls.binding, cls.policy = RAT.build_all()

    def _eligible(self):
        return {
            identity: {"benchmark_identity": benchmark, "role": "SECTOR"}
            for prefix, benchmark, members in RAT._sector_scopes()
            for identity in members
        }

    def test_binding_accepted_by_real_validate_binding(self):
        validated = KCR._validate_binding(self.binding, self.contract, derived=False)
        self.assertEqual(validated, self.binding)

    def test_upstream_leadership_policy_sha_independently_recomputed(self):
        real_sha = RAT.file_sha256(LEADERSHIP_POLICY_PATH)
        self.assertEqual(self.binding["upstream_leadership_policy_sha256"], real_sha)
        self.assertEqual(self.policy["upstream_leadership_policy_sha256"], real_sha)

    def test_stays_inert_for_pre_effective_from_pair(self):
        """The CIO's explicit rule: ratification alone is not a natural
        proof. Any pair predating effective_from must stay inert."""
        validated_binding = KCR._validate_binding(self.binding, self.contract, derived=False)
        eligible = self._eligible()
        _, effective, _ = KCR._validate_policy(
            self.policy, validated_binding, eligible,
            dt.date(2026, 9, 8), dt.date(2026, 9, 9),
            dt.datetime(2026, 9, 8, 10, 0, tzinfo=dt.timezone.utc),
        )
        self.assertFalse(effective)

    def test_effective_from_session_alone_does_not_yet_count(self):
        """A pair whose CURRENT date is effective_from but whose PRIOR date
        is not >= effective_from must also stay inert (prior_date must
        itself be inside the interval, per the CIO's explicit rule)."""
        validated_binding = KCR._validate_binding(self.binding, self.contract, derived=False)
        eligible = self._eligible()
        _, effective, _ = KCR._validate_policy(
            self.policy, validated_binding, eligible,
            dt.date(2026, 9, 11), dt.date(2026, 9, 14),
            dt.datetime(2026, 9, 11, 10, 0, tzinfo=dt.timezone.utc),
        )
        self.assertFalse(effective)

    def test_becomes_effective_for_a_structurally_in_interval_pair(self):
        """Structural proof the mechanism works once real evidence lands --
        NOT a claim that this specific pair is a real natural observation."""
        validated_binding = KCR._validate_binding(self.binding, self.contract, derived=False)
        eligible = self._eligible()
        _, effective, scopes = KCR._validate_policy(
            self.policy, validated_binding, eligible,
            dt.date(2026, 9, 14), dt.date(2026, 9, 15),
            dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc),
        )
        self.assertTrue(effective)
        self.assertEqual(len(scopes), 2)

    def test_anti_lookahead_structurally_guaranteed(self):
        """ratified_at_utc predates effective_from, so covers_both can never
        be true for evidence that already existed at ratification time --
        this is a structural property of the artifact, checked directly."""
        ratified_at = dt.datetime.fromisoformat(
            self.policy["ratified_at_utc"].replace("Z", "+00:00")
        )
        effective_from = dt.date.fromisoformat(self.policy["effective_from"])
        self.assertLess(ratified_at.date(), effective_from)


if __name__ == "__main__":
    unittest.main()
