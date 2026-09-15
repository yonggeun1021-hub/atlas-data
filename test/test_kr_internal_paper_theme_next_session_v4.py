#!/usr/bin/env python3
"""Tests for the version 4 bounded D-to-E next-session application.

Every synthetic packet built here is a validator fixture only; none of it is
market evidence, and no result here authorizes an entry, order, or REAL
authority.
"""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rotation import kr_internal_paper_theme_next_session_v4 as V4


def _load_fixture_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The version 3 test module is loaded by path and referenced only through this
# attribute, so its TestCase classes are not collected a second time here.
V3T = _load_fixture_module(
    "kr_internal_paper_theme_application_v3_fixtures",
    "test/test_kr_internal_paper_theme_application.py",
)

# payload_sha256 of the version 3 positive fixture output
# (SyntheticNextSessionTests.test_immediate_previous_session_context_is_bounded_input_only)
# recorded at the C1 base commit ec2416a286231059b361a905d2662cf4086c19b7.
V3_POSITIVE_OUTPUT_GOLDEN_SHA256 = "6c6b26ee2bac72ef1b7e50e169ffe20c532a73407a5319ba9caed73e4144a112"
V3_MODULE_SHA256 = "b4414785f4257254c0bc63b2d27eb2f4974a149727e6508786784b9959f079e6"
V3_CONTRACT_SHA256 = "717fea4b6e7fca24e1cba607bd330f8455aa4777727cb0eb7a46edacd6e7c602"
V3_ADOPTION_EVIDENCE_SHA256 = "2571be782cb70473aaba49ed6c6a2fc0e67cd6f6a5a1af3d8ec66433aec5022b"

CONTEXT_DATE = "2026-09-08"
EXECUTION_DATE = "2026-09-09"
EVALUATION_AT = "2026-09-09T15:00:00+09:00"
FORWARD_AT = "2026-09-09T15:05:00+09:00"
TARGETS = {
    "000660": ("KRX:000660:COMMON", "DART:00164779", "XKRX:000660"),
    "005930": ("KRX:005930:COMMON", "DART:00126380", "XKRX:005930"),
}
CONTEXT_IDENTITIES = [
    {
        "asset_id": "KR:XKRX:000660",
        "canonical_instrument_id": "KRX:000660:COMMON",
        "canonical_issuer_id": "DART:00164779",
        "listing_id": "XKRX:000660",
    },
    {
        "asset_id": "KR:XKRX:005930",
        "canonical_instrument_id": "KRX:005930:COMMON",
        "canonical_issuer_id": "DART:00126380",
        "listing_id": "XKRX:005930",
    },
]


def _head(repo: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def _published(day: str) -> str:
    return f"data/observations/krx_global_universe/{day}/packet.json"


def _utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def _master(day: str, retrieved_at: str) -> dict:
    return V3T.SyntheticNextSessionTests._master(day, retrieved_at)


def _leadership(source_name: str | None = None, available_at: str | None = None) -> dict:
    wrapper = V3T.SyntheticEndToEndTests._leadership()
    packet = wrapper["leadership_packet"]
    if source_name is not None:
        packet["policy"]["source_name"] = source_name
    if available_at is not None:
        packet["available_at"] = available_at
    if source_name is not None or available_at is not None:
        packet["payload_sha256"] = V4.APP.payload_sha256(
            {key: value for key, value in packet.items() if key != "payload_sha256"}
        )
        wrapper["leadership_packet_sha256"] = packet["payload_sha256"]
        wrapper["payload_sha256"] = V4.APP.payload_sha256(
            {key: value for key, value in wrapper.items() if key != "payload_sha256"}
        )
    return wrapper


@contextlib.contextmanager
def _git_repo():
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Synthetic Test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "synthetic@example.invalid"], cwd=repo, check=True)

        def commit(message: str, committed_at: str) -> None:
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            environment = os.environ.copy()
            environment.update(GIT_AUTHOR_DATE=committed_at, GIT_COMMITTER_DATE=committed_at)
            subprocess.run(
                ["git", "commit", "-q", "--allow-empty", "-m", message],
                cwd=repo, env=environment, check=True,
            )

        yield repo, commit


def _write_json(repo: Path, relative: str, value) -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def _resolved(ticker: str, decision_date: str) -> dict:
    instrument_id, issuer_id, listing_id = TARGETS[ticker]
    basis = {
        "verified_row_first_seen_at": "2026-08-25T06:19:27Z",
        "verified_evidence_first_seen_at": "2026-08-25T06:19:27Z",
        "ratified_at": "2026-08-25T06:19:27Z",
    }
    return {
        "status": "RESOLVED",
        "decision_date": decision_date,
        "canonical_issuer_id": issuer_id,
        "canonical_instrument_id": instrument_id,
        "listing_id": listing_id,
        "identity_basis": {"source_alias": dict(basis), "listing": dict(basis)},
    }


class RecordingResolver:
    """Fake canonical resolver; the temp repo carries no identity authority."""

    def __init__(self, override=None):
        self.calls = []
        self.override = override

    def __call__(self, source_name, source_asset_id, market, decision_date, authority, trusted_commit=None):
        self.calls.append({
            "source_name": source_name,
            "ticker": source_asset_id,
            "market": market,
            "decision_date": decision_date,
            "trusted_commit": trusted_commit,
        })
        result = _resolved(source_asset_id, decision_date)
        if self.override is not None:
            result = self.override(result, source_asset_id, decision_date)
        return result


def _decision_v4() -> dict:
    return {
        "status": "ADOPTED_EXACT_D_TO_E_SCOPE_V2",
        "decision_id": "KR_INTERNAL_PAPER_PREVIOUS_COMPLETED_SESSION_CONTEXT_V2",
        "contract_first_seen_at": "2026-09-07T16:50:00Z",
        "amendment_evidence_first_seen_at": "2026-09-07T16:50:00Z",
        "predecessor_decision_evidence_first_seen_at": "2026-09-07T16:43:02Z",
        "user_ratification_id": "KR-NEXT-SESSION-INPUT-POLICY-DBE-20260913",
        "decision_real_usable_from": "2026-09-07T16:50:00Z",
    }


def _synthetic_evaluate(
    *,
    mode: str = V4.IDENTITY_MODE_LATEST_PUBLISHED,
    evaluation_at: str = EVALUATION_AT,
    forward_at: str = FORWARD_AT,
    calendar_days: list[str] | None = None,
    calendar_statuses: list[str] | None = None,
    calendar_available_at: str = "2026-09-08T00:00:00Z",
    calendar_commit_at: str = "2026-09-08T00:05:00+00:00",
    d_master_retrieved_at: str = "2026-09-08T00:30:00Z",
    d_inputs_commit_at: str = "2026-09-08T09:15:00+00:00",
    leadership: dict | None = None,
    older_packets: list[tuple[str, str, str]] = (),
    identity_day: str = CONTEXT_DATE,
    e_first_seen: str = "2026-09-09T00:05:00Z",
    resolver: RecordingResolver | None = None,
    authority=None,
    contract_patch: dict | None = None,
) -> tuple[dict, RecordingResolver, str]:
    """Build an explicitly synthetic trusted repo and run the v4 reducer."""
    calendar_days = calendar_days or [CONTEXT_DATE, EXECUTION_DATE]
    calendar_statuses = calendar_statuses or ["OPEN_REGULAR"] * len(calendar_days)
    resolver = resolver or RecordingResolver()
    with _git_repo() as (repo, commit):
        for day, retrieved_at, committed_at in older_packets:
            _write_json(repo, _published(day), _master(day, retrieved_at))
            commit(f"synthetic older master {day}", committed_at)
        calendar_paths = []
        for day, status in zip(calendar_days, calendar_statuses):
            calendar_paths.append(_write_json(
                repo, f"calendar-{day}.json",
                V3T.SyntheticNextSessionTests._calendar(day, status, calendar_available_at),
            ))
        commit("synthetic calendar evidence", calendar_commit_at)
        d_master_path = _write_json(repo, _published(CONTEXT_DATE), _master(CONTEXT_DATE, d_master_retrieved_at))
        d_leadership_path = _write_json(repo, "d-leadership.json", leadership or _leadership())
        commit("synthetic D inputs", d_inputs_commit_at)
        if mode == V4.IDENTITY_MODE_EXACT_E_MASTER:
            e_master_path = _write_json(repo, "e-master.json", _master(EXECUTION_DATE, "2026-09-09T00:00:00Z"))
            commit("synthetic E master", e_first_seen)
            evidence = {"mode": mode, "executionMasterPacketPath": e_master_path}
        else:
            evidence = {"mode": mode, "identityMasterPacketPath": repo / _published(identity_day)}
        head = _head(repo)
        patches = [
            mock.patch.object(V4.APP, "resolve_source_admission", return_value=V3T.SyntheticEndToEndTests._admission()),
            mock.patch.object(V4, "resolve_next_session_decision_v4", return_value=_decision_v4()),
            mock.patch.object(V4.APP, "_repo_and_commit", return_value=(repo, head)),
            mock.patch.object(V4.APP, "_target_identities_for_session", return_value=copy.deepcopy(CONTEXT_IDENTITIES)),
            mock.patch.object(V4.CI, "resolve_instrument_identity", side_effect=resolver),
        ]
        if authority is not None:
            patches.append(mock.patch.object(V4.CI, "load_authority", return_value=authority))
        with contextlib.ExitStack() as stack:
            if contract_patch is not None:
                contract_file = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "contract_v4.json"
                contract_file.write_text(json.dumps(contract_patch, indent=2), encoding="utf-8")
                stack.enter_context(mock.patch.object(V4, "CONTRACT_V4_PATH", contract_file))
                stack.enter_context(mock.patch.object(
                    V4, "_expected_next_session_contract_v4", return_value=copy.deepcopy(contract_patch)
                ))
            for patch in patches:
                stack.enter_context(patch)
            result = V4.evaluate_next_session_application_v4(
                d_master_path, d_leadership_path, evidence, calendar_paths,
                evaluation_at, forward_at, head,
            )
        return result, resolver, head


def _set_path(value: dict, path: tuple, new):
    target = value
    for key in path[:-1]:
        target = target[key]
    if new is _REMOVE_LAST:
        target[path[-1]].pop()
    else:
        target[path[-1]] = new
    return value


_REMOVE_LAST = object()


class ContractAndDecisionTests(unittest.TestCase):
    def test_v4_contract_is_exact_pin(self):
        contract = V4.load_next_session_contract_v4()
        self.assertEqual(contract, V4._expected_next_session_contract_v4())
        self.assertEqual(contract["schema_version"], "kr_internal_paper_theme_next_session_contract/4")
        identity_modes = ("execution_session", "identity_evidence_modes",
                          "LATEST_PUBLISHED_MASTER_WITH_E_LISTING_RESOLUTION", "identity_master")
        variants = {
            "post_close_source_admitted": (
                ("context_session", "context_source_modes", "SAME_DAY_POST_CLOSE", "post_close_source_admitted"), True),
            "maximum_age_authorized": (identity_modes + ("maximum_age_authorized",), 30),
            "d_master_interval_extension_authorized": (
                ("membership", "d_master_interval_extension_authorized"), True),
            "d_minus_two_fallback_authorized": (
                ("session_boundary", "d_minus_two_fallback_authorized"), True),
            "e_plus_one_carry_authorized": (("execution_session", "e_plus_one_carry_authorized"), True),
            "authority.real_authority": (("authority", "real_authority"), True),
            "authority.new_entry_authorized": (("authority", "new_entry_authorized"), True),
            "separate_required_inputs": (("separate_required_inputs",), _REMOVE_LAST),
        }
        with tempfile.TemporaryDirectory() as directory:
            for label, (path, new) in variants.items():
                with self.subTest(variant=label):
                    mutated = _set_path(copy.deepcopy(contract), path, new)
                    target = Path(directory) / f"{label}.json"
                    target.write_text(json.dumps(mutated, indent=2), encoding="utf-8")
                    with self.assertRaisesRegex(V4.ThemeApplicationError, "NEXT_SESSION_CONTRACT_V4_MISMATCH"):
                        V4.load_next_session_contract_v4(target)

    def test_v4_calendar_binding_equals_v3(self):
        self.assertEqual(
            V4._expected_next_session_contract_v4()["session_boundary"],
            V4.APP._expected_next_session_contract()["session_boundary"],
        )
        diverged = V4._expected_next_session_contract_v4()
        diverged["session_boundary"]["calendar_contract_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "diverged.json"
            target.write_text(json.dumps(diverged), encoding="utf-8")
            with mock.patch.object(V4, "_expected_next_session_contract_v4", return_value=copy.deepcopy(diverged)):
                with self.assertRaisesRegex(V4.ThemeApplicationError, "NEXT_SESSION_V4_CALENDAR_BINDING_DIVERGED"):
                    V4.load_next_session_contract_v4(target)

    def test_committed_v2_decision_is_independently_verified(self):
        result = V4.resolve_next_session_decision_v4(_head(ROOT))
        self.assertEqual(result["status"], "ADOPTED_EXACT_D_TO_E_SCOPE_V2")
        self.assertEqual(result["decision_id"], "KR_INTERNAL_PAPER_PREVIOUS_COMPLETED_SESSION_CONTEXT_V2")
        self.assertEqual(result["user_ratification_id"], "KR-NEXT-SESSION-INPUT-POLICY-DBE-20260913")
        self.assertGreaterEqual(result["decision_real_usable_from"], result["amendment_evidence_first_seen_at"])
        self.assertGreaterEqual(result["decision_real_usable_from"], result["contract_first_seen_at"])
        self.assertGreaterEqual(result["decision_real_usable_from"], "2026-09-13T12:40:00Z")
        self.assertFalse(result["authority"]["new_entry_authorized"])

    def _amendment_inputs(self):
        contract = V4._expected_next_session_contract_v4()
        amendment = json.loads((ROOT / contract["decision_evidence"]["path"]).read_text(encoding="utf-8"))
        predecessor = json.loads(
            (ROOT / contract["predecessor_decision_evidence"]["path"]).read_text(encoding="utf-8")
        )
        return contract, amendment, predecessor

    def test_amendment_evidence_is_public_safe_and_pinned(self):
        contract, amendment, predecessor = self._amendment_inputs()
        raw = (ROOT / contract["decision_evidence"]["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), contract["decision_evidence"]["sha256"])
        V4._validate_amendment_document(amendment, contract, predecessor)
        self.assertEqual(amendment["recording_decision_id"], "CIO-C1-IMPL-20260913")
        text = raw.decode("utf-8")
        self.assertNotIn("/Users/", text)
        self.assertIsNone(re.search(r"[A-Za-z]:\\\\", text))
        self.assertEqual(
            amendment["user_ratification"]["ratification_sha256"],
            "c18d397994324f09e95a0ba17baae5a722448d595ad1d401e1fb2536f0b0e3b5",
        )

    def test_amendment_tamper_fails_closed(self):
        contract, amendment, predecessor = self._amendment_inputs()
        cases = {
            "rules_3_before": (
                lambda value: value["amends"].__setitem__("rules_3_before", value["amends"]["rules_3_before"][:-1] + "!"),
                "NEXT_SESSION_AMENDMENT_EVIDENCE_MISMATCH",
            ),
            "amends.sha256": (
                lambda value: value["amends"].__setitem__("sha256", "0" * 64),
                "NEXT_SESSION_AMENDMENT_EVIDENCE_MISMATCH",
            ),
            "authority_changes.real": (
                lambda value: value["authority_changes"].__setitem__("real", True),
                "NEXT_SESSION_AMENDMENT_EVIDENCE_MISMATCH",
            ),
            "ratification_sha256": (
                lambda value: value["user_ratification"].__setitem__("ratification_sha256", "1" * 64),
                "NEXT_SESSION_AMENDMENT_EVIDENCE_MISMATCH",
            ),
            "backdated": (
                lambda value: value.__setitem__("recorded_after_decision_at_utc", "2026-09-13T12:39:59Z"),
                "NEXT_SESSION_AMENDMENT_BACKDATED",
            ),
        }
        for label, (mutate, code) in cases.items():
            with self.subTest(case=label):
                mutated = copy.deepcopy(amendment)
                mutate(mutated)
                with self.assertRaisesRegex(V4.ThemeApplicationError, code):
                    V4._validate_amendment_document(mutated, contract, predecessor)

    def test_predecessor_contract_pin(self):
        contract = V4._expected_next_session_contract_v4()
        contract_rel = contract["predecessor_contract"]["contract_path"]
        evidence_rel = contract["predecessor_decision_evidence"]["path"]
        good_contract = (ROOT / contract_rel).read_bytes()
        good_evidence = (ROOT / evidence_rel).read_bytes()
        cases = (
            (good_contract + b" ", good_evidence, "NEXT_SESSION_PREDECESSOR_CONTRACT_PIN_MISMATCH"),
            (good_contract, good_evidence + b" ", "NEXT_SESSION_PREDECESSOR_DECISION_PIN_MISMATCH"),
            (good_contract, good_evidence, None),
        )
        for contract_raw, evidence_raw, code in cases:
            with self.subTest(code=code), _git_repo() as (repo, commit):
                for relative, raw in ((contract_rel, contract_raw), (evidence_rel, evidence_raw)):
                    (repo / relative).parent.mkdir(parents=True, exist_ok=True)
                    (repo / relative).write_bytes(raw)
                commit("synthetic predecessor pins", "2026-09-08T00:00:00+00:00")
                if code is None:
                    evidence, first_seen = V4._verify_predecessor_pins(repo, _head(repo), contract)
                    self.assertEqual(evidence["decision_id"], "KR_INTERNAL_PAPER_PREVIOUS_COMPLETED_SESSION_CONTEXT_V1")
                    self.assertEqual(first_seen, "2026-09-08T00:00:00Z")
                else:
                    with self.assertRaisesRegex(V4.ThemeApplicationError, code):
                        V4._verify_predecessor_pins(repo, _head(repo), contract)


class BackwardCompatibilityTests(unittest.TestCase):
    def test_v3_artifacts_byte_identical(self):
        self.assertEqual(
            hashlib.sha256((ROOT / "rotation/kr_internal_paper_theme_application.py").read_bytes()).hexdigest(),
            V3_MODULE_SHA256,
        )
        self.assertEqual(
            hashlib.sha256((ROOT / "config/kr_internal_paper_theme_next_session_contract.json").read_bytes()).hexdigest(),
            V3_CONTRACT_SHA256,
        )
        self.assertEqual(
            hashlib.sha256((
                ROOT / "evidence/authority/kr_internal_paper_previous_completed_session_context_adoption_20260908.json"
            ).read_bytes()).hexdigest(),
            V3_ADOPTION_EVIDENCE_SHA256,
        )
        self.assertEqual(V4.APP.NEXT_SESSION_OUTPUT_SCHEMA, "kr_internal_paper_theme_next_session_application/3")
        self.assertFalse(hasattr(V4, "NEXT_SESSION_OUTPUT_SCHEMA"))

    def test_v3_positive_output_golden_unchanged(self):
        self.assertIsNot(V3T.APP, V4.APP)
        result = V3T.SyntheticNextSessionTests()._evaluate()
        self.assertEqual(result["schema_version"], "kr_internal_paper_theme_next_session_application/3")
        self.assertEqual(result["status"], "ACTIVE_PREVIOUS_COMPLETED_SESSION_CONTEXT_INPUT")
        self.assertEqual(result["payload_sha256"], V3_POSITIVE_OUTPUT_GOLDEN_SHA256)

    def test_v4_output_keeps_gate_read_keys(self):
        result, _, _ = _synthetic_evaluate()
        self.assertEqual(result["schema_version"], "kr_internal_paper_theme_next_session_application/4")
        for key in (
            "context_identities", "execution_identities",
            "immediate_session_predecessor_verified", "session_calendar_verified",
        ):
            self.assertIn(key, result)
        for key in (
            "context_master_payload_sha256", "context_leadership_payload_sha256",
            "session_calendar_receipt_sha256",
        ):
            self.assertIn(key, result["lineage"])
        self.assertIn("top_bucket_verified", result["context_series_observation"])
        self.assertIn("previous_completed_session_context_input_authorized", result["authority"])
        v3_keys = set(V3T.SyntheticNextSessionTests()._evaluate())
        self.assertLessEqual(v3_keys, set(result))


class LatestPublishedMasterPositiveTests(unittest.TestCase):
    def test_b_mode_latest_published_master_with_e_listing_is_bounded_input_only(self):
        result, resolver, head = _synthetic_evaluate()
        self.assertEqual(result["status"], "ACTIVE_PREVIOUS_COMPLETED_SESSION_CONTEXT_INPUT")
        self.assertTrue(result["authority"]["previous_completed_session_context_input_authorized"])
        self.assertEqual(result["execution_identity_evidence_mode"], V4.IDENTITY_MODE_LATEST_PUBLISHED)
        self.assertEqual(result["context_source_mode"], "NEXT_SESSION_OFFICIAL_DAILY")
        self.assertEqual(result["execution_session_date"], EXECUTION_DATE)
        self.assertIsNone(result["lineage"]["execution_master_payload_sha256"])
        self.assertIsNone(result["lineage"]["execution_master_first_seen_at"])
        self.assertEqual(
            result["lineage"]["execution_identity_master_payload_sha256"],
            result["lineage"]["context_master_payload_sha256"],
        )
        self.assertEqual(result["execution_identity_evidence"]["identity_master_as_of_date"], CONTEXT_DATE)
        self.assertEqual(result["execution_identities"], CONTEXT_IDENTITIES)
        self.assertEqual(
            [call["decision_date"] for call in resolver.calls],
            ["2026-09-09T06:00:00Z", "2026-09-09T06:05:00Z"] * 2,
        )
        self.assertEqual({call["trusted_commit"] for call in resolver.calls}, {head})
        self.assertEqual({call["source_name"] for call in resolver.calls}, {"krx_open_api_stock_daily"})
        self.assertFalse(result["authority"]["new_entry_authorized"])
        self.assertFalse(result["authority"]["regime_gate_authorized"])
        self.assertFalse(result["context_series_observation"]["top_bucket_verified"])
        self.assertEqual(
            result["separate_required_inputs"],
            V4._expected_next_session_contract_v4()["separate_required_inputs"],
        )
        self.assertEqual(len(result["separate_required_inputs"]), 4)

    def test_evaluation_after_e_open_before_e_close_is_allowed(self):
        """No pre-open assumption: any instant on E before the close is admissible."""
        for evaluation_at, forward_at, expected_instants in (
            ("2026-09-09T09:30:00+09:00", "2026-09-09T09:35:00+09:00",
             ["2026-09-09T00:30:00Z", "2026-09-09T00:35:00Z"]),
            ("2026-09-09T13:15:00.750+09:00", "2026-09-09T13:24:59.999+09:00",
             ["2026-09-09T04:15:00Z", "2026-09-09T04:24:59Z"]),
            ("2026-09-09T15:29:00+09:00", "2026-09-09T15:29:59+09:00",
             ["2026-09-09T06:29:00Z", "2026-09-09T06:29:59Z"]),
        ):
            with self.subTest(evaluation_at=evaluation_at):
                result, resolver, _ = _synthetic_evaluate(evaluation_at=evaluation_at, forward_at=forward_at)
                self.assertEqual(result["status"], "ACTIVE_PREVIOUS_COMPLETED_SESSION_CONTEXT_INPUT")
                self.assertTrue(result["inputs_available_by_evaluation"])
                self.assertTrue(result["authority"]["previous_completed_session_context_input_authorized"])
                self.assertEqual(
                    [call["decision_date"] for call in resolver.calls[:2]], expected_instants
                )

    def test_evaluation_at_or_after_e_close_is_not_active(self):
        result, _, _ = _synthetic_evaluate(
            evaluation_at="2026-09-09T15:30:00+09:00", forward_at="2026-09-09T15:31:00+09:00"
        )
        self.assertEqual(result["status"], "UNKNOWN_EVALUATION_OUTSIDE_EXECUTION_SESSION_MEMBERSHIP")
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])

    def test_real_resolver_resolves_targets_at_instant(self):
        master = _master(CONTEXT_DATE, "2026-09-08T00:30:00Z")
        identities, receipts, available_at, reason, detail = V4._e_identities_by_listing_resolution(
            master, _utc("2026-09-09T06:00:00Z"), _utc("2026-09-09T06:05:00Z"), _head(ROOT),
        )
        self.assertIsNone(reason, detail)
        self.assertEqual(identities, CONTEXT_IDENTITIES)
        self.assertEqual({row["status"] for row in receipts}, {"RESOLVED"})
        self.assertEqual(
            [(row["asset_id"], row["instant"]) for row in receipts],
            [
                ("KR:XKRX:000660", "2026-09-09T06:00:00Z"), ("KR:XKRX:000660", "2026-09-09T06:05:00Z"),
                ("KR:XKRX:005930", "2026-09-09T06:00:00Z"), ("KR:XKRX:005930", "2026-09-09T06:05:00Z"),
            ],
        )
        self.assertLessEqual(available_at, _utc("2026-09-09T06:00:00Z"))

    def test_no_numeric_age_window(self):
        with _git_repo() as (repo, commit):
            _write_json(repo, _published("2020-01-02"), _master("2020-01-02", "2020-01-02T00:30:00Z"))
            commit("synthetic ancient master", "2020-01-02T10:00:00+00:00")
            selected, reason = V4._select_latest_available_identity_master(
                repo, _head(repo), _utc("2026-09-09T06:00:00Z"), CONTEXT_DATE,
            )
        self.assertIsNone(reason)
        self.assertEqual(selected["as_of_date"], "2020-01-02")
        source = Path(V4.__file__).read_text(encoding="utf-8")
        for pattern in ("max_age", "staleness", "timedelta(days="):
            self.assertNotIn(pattern, source)


class LatestPublishedMasterNegativeTests(unittest.TestCase):
    def test_lookahead_identity_master_unknown(self):
        for label, kwargs in (
            ("retrieved_at", {"d_master_retrieved_at": "2026-09-09T06:10:00Z"}),
            ("commit_time", {"d_inputs_commit_at": "2026-09-09T06:10:00+00:00"}),
        ):
            with self.subTest(case=label):
                result, _, _ = _synthetic_evaluate(**kwargs)
                self.assertEqual(result["status"], "UNKNOWN_E_IDENTITY_MASTER_AVAILABLE_AFTER_EVALUATION")
                self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])

    def test_future_packet_is_ignored_not_selected(self):
        evaluation = _utc("2026-09-09T06:00:00Z")
        with _git_repo() as (repo, commit):
            _write_json(repo, _published("2026-09-07"), _master("2026-09-07", "2026-09-07T09:30:00Z"))
            commit("synthetic master A", "2026-09-07T10:00:00+00:00")
            _write_json(repo, _published(CONTEXT_DATE), _master(CONTEXT_DATE, "2026-09-08T09:30:00Z"))
            commit("synthetic master B published after evaluation", "2026-09-09T06:10:00+00:00")
            head = _head(repo)
            selected, reason = V4._select_latest_available_identity_master(repo, head, evaluation, CONTEXT_DATE)
            self.assertIsNone(reason)
            self.assertEqual(selected["as_of_date"], "2026-09-07")
            _, _, reason_b = V4._identity_master_evidence(
                repo / _published(CONTEXT_DATE), repo, head, evaluation, CONTEXT_DATE
            )
            self.assertEqual(reason_b, "E_IDENTITY_MASTER_AVAILABLE_AFTER_EVALUATION")
            _, _, reason_a = V4._identity_master_evidence(
                repo / _published("2026-09-07"), repo, head, evaluation, CONTEXT_DATE
            )
            self.assertIsNone(reason_a)

    def test_packets_dated_after_context_session_are_not_opened(self):
        evaluation = _utc("2026-09-09T06:00:00Z")
        with _git_repo() as (repo, commit):
            _write_json(repo, _published(CONTEXT_DATE), _master(CONTEXT_DATE, "2026-09-08T09:30:00Z"))
            (repo / _published(EXECUTION_DATE)).parent.mkdir(parents=True)
            (repo / _published(EXECUTION_DATE)).write_text("not json", encoding="utf-8")
            commit("synthetic masters", "2026-09-08T10:00:00+00:00")
            head = _head(repo)
            original = V4.TTA._git_blob
            opened = []

            def recording_blob(repo_arg, commit_arg, relative):
                opened.append(relative)
                return original(repo_arg, commit_arg, relative)

            with mock.patch.object(V4.TTA, "_git_blob", side_effect=recording_blob):
                selected, reason = V4._select_latest_available_identity_master(repo, head, evaluation, CONTEXT_DATE)
        self.assertIsNone(reason)
        self.assertEqual(selected["as_of_date"], CONTEXT_DATE)
        self.assertNotIn(_published(EXECUTION_DATE), opened)

    def test_not_latest_available_master_unknown(self):
        result, _, _ = _synthetic_evaluate(
            older_packets=[("2026-09-07", "2026-09-07T09:30:00Z", "2026-09-07T10:00:00+00:00")],
            identity_day="2026-09-07",
        )
        self.assertEqual(result["status"], "UNKNOWN_E_IDENTITY_MASTER_NOT_LATEST_AVAILABLE")
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])
        self.assertEqual(result["execution_identities"], [])

    def test_newer_available_invalid_packet_fails_closed(self):
        evaluation = _utc("2026-09-09T06:00:00Z")
        with _git_repo() as (repo, commit):
            _write_json(repo, _published("2026-09-07"), _master("2026-09-07", "2026-09-07T09:30:00Z"))
            commit("synthetic valid older master", "2026-09-07T10:00:00+00:00")
            tampered = _master(CONTEXT_DATE, "2026-09-08T09:30:00Z")
            tampered["payload_sha256"] = "0" * 64
            _write_json(repo, _published(CONTEXT_DATE), tampered)
            commit("synthetic tampered newer master", "2026-09-08T10:00:00+00:00")
            head = _head(repo)
            selected, reason = V4._select_latest_available_identity_master(repo, head, evaluation, CONTEXT_DATE)
            self.assertIsNone(selected)
            self.assertEqual(reason, "E_IDENTITY_MASTER_NEWER_AVAILABLE_PACKET_INVALID")
            _, _, reason_older = V4._identity_master_evidence(
                repo / _published("2026-09-07"), repo, head, evaluation, CONTEXT_DATE
            )
            self.assertEqual(reason_older, "E_IDENTITY_MASTER_NEWER_AVAILABLE_PACKET_INVALID")

    def test_listing_mismatch_unknown(self):
        def wrong_listing(result, ticker, instant):
            if ticker == "000660":
                result["listing_id"] = "XKRX:000661"
            return result

        def wrong_instrument(result, ticker, instant):
            if ticker == "005930":
                result["canonical_instrument_id"] = "KRX:005930:PREFERRED"
            return result

        authority = copy.deepcopy(V4.CI.load_authority())
        for row in authority["listings"]:
            if row.get("listing_id") == "XKRX:000660":
                row["ticker"] = "000661"
        for label, kwargs in (
            ("listing_id", {"resolver": RecordingResolver(wrong_listing)}),
            ("canonical_instrument_id", {"resolver": RecordingResolver(wrong_instrument)}),
            ("listing_row_ticker", {"authority": authority}),
        ):
            with self.subTest(case=label):
                result, _, _ = _synthetic_evaluate(**kwargs)
                self.assertEqual(result["status"], "UNKNOWN_E_LISTING_IDENTITY_MISMATCH")
                self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])

    def test_listing_unresolved_at_evaluation_unknown(self):
        def pit_at_evaluation(result, ticker, instant):
            if instant == "2026-09-09T06:00:00Z":
                return {**result, "status": "IDENTITY_NOT_COMPUTABLE_PIT_VIOLATION",
                        "canonical_instrument_id": None, "canonical_issuer_id": None, "listing_id": None}
            return result

        result, _, _ = _synthetic_evaluate(resolver=RecordingResolver(pit_at_evaluation))
        self.assertEqual(result["status"], "UNKNOWN_E_LISTING_NOT_RESOLVED_AT_EVALUATION")
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])

    def test_listing_expires_before_forward_execution_unknown(self):
        def expired_at_execution(result, ticker, instant):
            if instant == "2026-09-09T06:05:00Z":
                return {**result, "status": "IDENTITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD",
                        "canonical_instrument_id": None, "canonical_issuer_id": None, "listing_id": None}
            return result

        result, _, _ = _synthetic_evaluate(resolver=RecordingResolver(expired_at_execution))
        self.assertEqual(result["status"], "UNKNOWN_E_LISTING_NOT_RESOLVED_AT_FORWARD_EXECUTION")
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])

    def test_e_calendar_closed_unknown(self):
        result, _, _ = _synthetic_evaluate(calendar_statuses=["OPEN_REGULAR", "CLOSED"])
        self.assertEqual(result["status"], "UNKNOWN_SESSION_CALENDAR_BOUNDARY_NOT_OPEN_REGULAR")
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])

    def test_e_calendar_first_seen_future_unknown(self):
        result, _, _ = _synthetic_evaluate(calendar_commit_at="2026-09-09T06:10:00+00:00")
        self.assertEqual(result["status"], "UNKNOWN_SESSION_CALENDAR_FUTURE_AT_EVALUATION")
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])

    def test_evaluation_not_on_e_local_date_unknown(self):
        for label, evaluation_at, forward_at in (
            ("evaluation_on_e_plus_one", "2026-09-10T10:00:00+09:00", "2026-09-10T10:05:00+09:00"),
            ("forward_on_next_day", "2026-09-09T23:58:00+09:00", "2026-09-10T00:02:00+09:00"),
        ):
            with self.subTest(case=label):
                result, _, _ = _synthetic_evaluate(evaluation_at=evaluation_at, forward_at=forward_at)
                self.assertEqual(result["status"], "UNKNOWN_EXECUTION_SESSION_DATE_NOT_EVALUATION_LOCAL_DATE")
                self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])

    def test_intervening_open_session_unknown(self):
        result, _, _ = _synthetic_evaluate(
            calendar_days=[CONTEXT_DATE, EXECUTION_DATE, "2026-09-10"],
            evaluation_at="2026-09-10T15:00:00+09:00",
            forward_at="2026-09-10T15:05:00+09:00",
        )
        self.assertEqual(result["status"], "UNKNOWN_SESSION_CALENDAR_INTERVENING_OPEN_SESSION")
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])


class PostCloseContextNotActivatedTests(unittest.TestCase):
    """Ratified decision D stays not activated (condition precedent V1 failed)."""

    post_close_kwargs = {
        "leadership": _leadership(
            source_name="KRX_POST_CLOSE_SAME_DAY_SYNTHETIC",
            available_at="2026-09-08T16:30:00+09:00",
        ),
        "d_master_retrieved_at": "2026-09-08T07:30:00Z",
        "d_inputs_commit_at": "2026-09-08T07:30:00+00:00",
    }

    def test_post_close_flag_false_is_unknown(self):
        result, _, _ = _synthetic_evaluate(**self.post_close_kwargs)
        self.assertEqual(result["status"], "UNKNOWN_CONTEXT_POST_CLOSE_SOURCE_NOT_ADMITTED")
        self.assertIsNone(result["context_source_mode"])
        self.assertTrue(result["session_calendar_verified"])
        self.assertTrue(result["inputs_available_by_evaluation"])
        self.assertIsNone(result["execution_identity_evidence"]["reason"])
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])
        control_kwargs = dict(self.post_close_kwargs)
        control_kwargs["leadership"] = _leadership(available_at="2026-09-08T16:30:00+09:00")
        control, _, _ = _synthetic_evaluate(**control_kwargs)
        self.assertEqual(control["status"], "ACTIVE_PREVIOUS_COMPLETED_SESSION_CONTEXT_INPUT")

    def test_non_official_master_source_is_unknown(self):
        packet = _master(CONTEXT_DATE, "2026-09-08T00:30:00Z")
        contract = V4._expected_next_session_contract_v4()
        wrapper = _leadership()
        self.assertIsNone(V4._context_source_is_official_daily(packet, wrapper, contract))
        packet["asset_master"]["records"][0]["source_identity"]["source_id"] = "krx_post_close_same_day"
        self.assertEqual(
            V4._context_source_is_official_daily(packet, wrapper, contract),
            "CONTEXT_POST_CLOSE_SOURCE_NOT_ADMITTED",
        )

    def test_no_post_close_activation_branch(self):
        flipped = V4._expected_next_session_contract_v4()
        flipped["context_session"]["context_source_modes"]["SAME_DAY_POST_CLOSE"]["post_close_source_admitted"] = True
        flipped["context_session"]["context_source_modes"]["SAME_DAY_POST_CLOSE"]["validator_activation_branch_present"] = True
        baseline, _, _ = _synthetic_evaluate(**self.post_close_kwargs)
        result, _, _ = _synthetic_evaluate(contract_patch=flipped, **self.post_close_kwargs)
        self.assertEqual(result["status"], "UNKNOWN_CONTEXT_POST_CLOSE_SOURCE_NOT_ADMITTED")
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])
        self.assertEqual(result["payload_sha256"], baseline["payload_sha256"])
        source = Path(V4.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count("post_close_source_admitted"), 1)
        self.assertEqual(source.count("SAME_DAY_POST_CLOSE"), 1)
        self.assertEqual(source.count("validator_activation_branch_present"), 1)


class ModeTests(unittest.TestCase):
    def test_exact_e_master_mode_still_supported(self):
        result, resolver, _ = _synthetic_evaluate(mode=V4.IDENTITY_MODE_EXACT_E_MASTER)
        self.assertEqual(result["status"], "ACTIVE_PREVIOUS_COMPLETED_SESSION_CONTEXT_INPUT")
        self.assertEqual(result["schema_version"], "kr_internal_paper_theme_next_session_application/4")
        self.assertEqual(result["execution_identity_evidence_mode"], "EXACT_E_MASTER")
        self.assertEqual(
            result["lineage"]["execution_master_payload_sha256"],
            _master(EXECUTION_DATE, "2026-09-09T00:00:00Z")["payload_sha256"],
        )
        self.assertEqual(result["lineage"]["execution_master_first_seen_at"], "2026-09-09T00:05:00Z")
        self.assertIsNone(result["lineage"]["execution_identity_master_payload_sha256"])
        self.assertEqual(resolver.calls, [])

    def test_exact_e_master_mode_rejects_future_e_master(self):
        result, _, _ = _synthetic_evaluate(mode=V4.IDENTITY_MODE_EXACT_E_MASTER, e_first_seen="2026-09-09T06:10:00Z")
        self.assertEqual(result["status"], "UNKNOWN_INPUT_AVAILABLE_AFTER_EVALUATION")

    def test_identity_evidence_mode_shape_invalid_raises(self):
        head = _head(ROOT)
        for evidence in (
            None,
            {},
            {"mode": "EXACT_E_MASTER"},
            {"mode": "EXACT_E_MASTER", "executionMasterPacketPath": "e.json", "extra": 1},
            {"mode": "EXACT_E_MASTER", "identityMasterPacketPath": "e.json"},
            {"mode": "LATEST_PUBLISHED_MASTER_WITH_E_LISTING_RESOLUTION", "executionMasterPacketPath": "e.json"},
            {"mode": "SAME_DAY", "identityMasterPacketPath": "e.json"},
            {"mode": "LATEST_PUBLISHED_MASTER_WITH_E_LISTING_RESOLUTION", "identityMasterPacketPath": ""},
        ):
            with self.subTest(evidence=evidence):
                with self.assertRaisesRegex(V4.ThemeApplicationError, "E_IDENTITY_EVIDENCE_MODE_INVALID"):
                    V4.evaluate_next_session_application_v4(
                        Path("d.json"), Path("l.json"), evidence, [],
                        EVALUATION_AT, FORWARD_AT, head,
                    )

    def test_base_module_is_loaded_from_own_root(self):
        self.assertEqual(V4.APP.ROOT, V4.ROOT)
        self.assertEqual(
            Path(V4.APP.__file__).resolve(),
            (V4.ROOT / "rotation/kr_internal_paper_theme_application.py").resolve(),
        )
        self.assertIs(V4.APP.CI, V4.CI)


class ForwardExecutionTtlTests(unittest.TestCase):
    """The 600-second limit and evaluation-before-forward-execution ordering."""

    def test_forward_execution_exactly_600_seconds_is_active(self):
        result, _, _ = _synthetic_evaluate(
            evaluation_at="2026-09-09T15:00:00+09:00", forward_at="2026-09-09T15:10:00+09:00"
        )
        self.assertEqual(result["status"], "ACTIVE_PREVIOUS_COMPLETED_SESSION_CONTEXT_INPUT")
        self.assertEqual(result["execution_membership"]["decision_to_execution_seconds"], 600.0)
        self.assertTrue(result["execution_membership"]["within_600_second_window"])

    def test_forward_execution_601_seconds_is_unknown(self):
        result, _, _ = _synthetic_evaluate(
            evaluation_at="2026-09-09T15:00:00+09:00", forward_at="2026-09-09T15:10:01+09:00"
        )
        self.assertEqual(result["status"], "UNKNOWN_FORWARD_EXECUTION_ORDER_OR_600_SECOND_TTL")
        self.assertTrue(result["execution_membership"]["forward_execution_active"])
        self.assertEqual(result["execution_membership"]["decision_to_execution_seconds"], 601.0)
        self.assertFalse(result["execution_membership"]["within_600_second_window"])
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])

    def test_forward_execution_before_evaluation_is_unknown(self):
        result, _, _ = _synthetic_evaluate(
            evaluation_at="2026-09-09T15:00:00+09:00", forward_at="2026-09-09T14:59:59+09:00"
        )
        self.assertEqual(result["status"], "UNKNOWN_FORWARD_EXECUTION_ORDER_OR_600_SECOND_TTL")
        self.assertTrue(result["execution_membership"]["forward_execution_active"])
        self.assertIsNone(result["execution_membership"]["decision_to_execution_seconds"])
        self.assertFalse(result["execution_membership"]["within_600_second_window"])
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])


class IdentityMasterBoundaryTests(unittest.TestCase):
    def test_named_master_dated_after_context_session_unknown(self):
        """An available E-dated master named by the caller is never admitted as D-or-earlier."""
        result, resolver, _ = _synthetic_evaluate(
            older_packets=[(EXECUTION_DATE, "2026-09-09T00:00:00Z", "2026-09-09T00:05:00+00:00")],
            identity_day=EXECUTION_DATE,
        )
        self.assertEqual(result["status"], "UNKNOWN_E_IDENTITY_MASTER_AS_OF_AFTER_CONTEXT_SESSION")
        self.assertEqual(result["execution_identity_evidence"]["identity_master_as_of_date"], EXECUTION_DATE)
        self.assertLessEqual(
            _utc(result["execution_identity_evidence"]["identity_master_available_at"]),
            _utc("2026-09-09T06:00:00Z"),
        )
        self.assertEqual(result["execution_identities"], [])
        self.assertEqual(resolver.calls, [])
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])

    def test_named_master_after_context_session_reason_direct(self):
        evaluation = _utc("2026-09-09T06:00:00Z")
        with _git_repo() as (repo, commit):
            _write_json(repo, _published(CONTEXT_DATE), _master(CONTEXT_DATE, "2026-09-08T09:30:00Z"))
            _write_json(repo, _published(EXECUTION_DATE), _master(EXECUTION_DATE, "2026-09-09T00:00:00Z"))
            commit("synthetic D and E masters", "2026-09-09T00:05:00+00:00")
            head = _head(repo)
            _, evidence, reason = V4._identity_master_evidence(
                repo / _published(EXECUTION_DATE), repo, head, evaluation, CONTEXT_DATE
            )
            self.assertEqual(reason, "E_IDENTITY_MASTER_AS_OF_AFTER_CONTEXT_SESSION")
            self.assertEqual(evidence["identity_master_as_of_date"], EXECUTION_DATE)
            _, _, control = V4._identity_master_evidence(
                repo / _published(CONTEXT_DATE), repo, head, evaluation, CONTEXT_DATE
            )
            self.assertIsNone(control)

    def test_identical_bytes_at_unpublished_path_not_latest(self):
        """Same payload at a non-published path must not pass as the selected published master."""
        evaluation = _utc("2026-09-09T06:00:00Z")
        with _git_repo() as (repo, commit):
            packet = _master(CONTEXT_DATE, "2026-09-08T09:30:00Z")
            published = _write_json(repo, _published(CONTEXT_DATE), packet)
            mirror = _write_json(repo, "mirror/krx_global_universe/packet.json", packet)
            self.assertEqual(published.read_bytes(), mirror.read_bytes())
            commit("synthetic published master and byte-identical mirror", "2026-09-08T10:00:00+00:00")
            head = _head(repo)
            selected, selection_reason = V4._select_latest_available_identity_master(
                repo, head, evaluation, CONTEXT_DATE
            )
            self.assertIsNone(selection_reason)
            self.assertEqual(selected["path"], _published(CONTEXT_DATE))
            _, mirror_evidence, mirror_reason = V4._identity_master_evidence(
                mirror, repo, head, evaluation, CONTEXT_DATE
            )
            self.assertEqual(mirror_evidence["identity_master_payload_sha256"], selected["payload_sha256"])
            self.assertEqual(mirror_reason, "E_IDENTITY_MASTER_NOT_LATEST_AVAILABLE")
            _, _, published_reason = V4._identity_master_evidence(
                published, repo, head, evaluation, CONTEXT_DATE
            )
            self.assertIsNone(published_reason)


class TargetRowKospiTests(unittest.TestCase):
    def test_target_row_not_active_kospi_unknown(self):
        def row_of(master, asset_id):
            return next(row for row in master["asset_master"]["records"] if row["asset_id"] == asset_id)

        def drop_row(master):
            records = master["asset_master"]["records"]
            records[:] = [row for row in records if row["asset_id"] != "KR:XKRX:005930"]

        def kosdaq_only(master):
            for membership in row_of(master, "KR:XKRX:000660")["active_memberships"]:
                if membership["membership_type"] == "UNIVERSE":
                    membership["membership_id"] = "KOSDAQ"

        def no_active_memberships(master):
            row_of(master, "KR:XKRX:000660")["active_memberships"] = []

        def kospi_as_market_type(master):
            for membership in row_of(master, "KR:XKRX:005930")["active_memberships"]:
                if membership["membership_type"] == "UNIVERSE":
                    membership["membership_type"] = "MARKET"

        cases = {
            "row_missing": (drop_row, "KR:XKRX:005930"),
            "market": (lambda m: row_of(m, "KR:XKRX:000660").__setitem__("market", "USA"), "KR:XKRX:000660"),
            "asset_class": (lambda m: row_of(m, "KR:XKRX:005930").__setitem__("asset_class", "ETF"), "KR:XKRX:005930"),
            "primary_symbol": (
                lambda m: row_of(m, "KR:XKRX:000660").__setitem__("primary_symbol", "000661"), "KR:XKRX:000660"),
            "universe_kosdaq": (kosdaq_only, "KR:XKRX:000660"),
            "no_active_memberships": (no_active_memberships, "KR:XKRX:000660"),
            "kospi_not_universe_type": (kospi_as_market_type, "KR:XKRX:005930"),
        }
        head = _head(ROOT)
        evaluation, execution = _utc("2026-09-09T06:00:00Z"), _utc("2026-09-09T06:05:00Z")
        control = _master(CONTEXT_DATE, "2026-09-08T00:30:00Z")
        with mock.patch.object(V4.CI, "resolve_instrument_identity", side_effect=RecordingResolver()):
            _, _, _, control_reason, _ = V4._e_identities_by_listing_resolution(control, evaluation, execution, head)
        self.assertIsNone(control_reason)
        for label, (mutate, asset_id) in cases.items():
            with self.subTest(case=label):
                master = copy.deepcopy(control)
                mutate(master)
                resolver = RecordingResolver()
                with mock.patch.object(V4.CI, "resolve_instrument_identity", side_effect=resolver):
                    identities, receipts, available_at, reason, detail = V4._e_identities_by_listing_resolution(
                        master, evaluation, execution, head,
                    )
                self.assertEqual(reason, "E_IDENTITY_TARGET_NOT_ACTIVE_KOSPI")
                self.assertEqual(detail, asset_id)
                self.assertEqual((identities, receipts, available_at), ([], [], None))
                self.assertEqual(resolver.calls, [])


class BaseModuleOriginTests(unittest.TestCase):
    BASE_NAME = "kr_internal_paper_theme_next_session_v4_base_application"

    def _load(self, *, spec_edit=None, module_edit=None):
        original = importlib.util.spec_from_file_location

        def hooked(name, *args, **kwargs):
            spec = original(name, *args, **kwargs)
            if name != self.BASE_NAME:
                return spec
            if spec_edit is not None:
                return spec_edit(spec)
            if module_edit is not None:
                real_exec = spec.loader.exec_module

                def exec_then_edit(module):
                    real_exec(module)
                    module_edit(module)

                spec.loader.exec_module = exec_then_edit
            return spec

        with mock.patch.object(V4.importlib.util, "spec_from_file_location", side_effect=hooked):
            return V4._load_base_application()

    def test_unmodified_load_passes(self):
        module = self._load()
        self.assertEqual(module.ROOT, V4.ROOT)
        self.assertIs(module.CI, V4.CI)
        self.assertIs(module.TTA, V4.TTA)

    def test_missing_spec_or_loader_raises(self):
        def no_loader(spec):
            spec.loader = None
            return spec

        for label, spec_edit in (("spec_none", lambda spec: None), ("loader_none", no_loader)):
            with self.subTest(case=label):
                with self.assertRaisesRegex(ValueError, "^V4_BASE_MODULE_ORIGIN_INVALID$"):
                    self._load(spec_edit=spec_edit)

    def test_module_origin_mismatch_raises(self):
        elsewhere = Path(tempfile.gettempdir()) / "atlas-synthetic-other-checkout"
        foreign_kcr = type(V4.CI)("rotation.korea_capital_rotation")
        foreign_kcr.__file__ = str(elsewhere / "rotation" / "korea_capital_rotation.py")
        cases = {
            "root": {"module_edit": lambda m: setattr(m, "ROOT", elsewhere)},
            "ci_object": {"module_edit": lambda m: setattr(m, "CI", type(V4.CI)("identity.canonical_identity"))},
            "tta_object": {"module_edit": lambda m: setattr(
                m, "TTA", type(V4.TTA)("rotation.theme_taxonomy_authority"))},
            "base_file": {"module_edit": lambda m: setattr(
                m, "__file__", str(elsewhere / "rotation" / "kr_internal_paper_theme_application.py"))},
            "kcr_file": {"module_edit": lambda m: setattr(m, "KCR", foreign_kcr)},
            "ci_file": {"file_patch": (V4.CI, elsewhere / "identity" / "canonical_identity.py")},
            "tta_file": {"file_patch": (V4.TTA, elsewhere / "rotation" / "theme_taxonomy_authority.py")},
        }
        for label, case in cases.items():
            with self.subTest(case=label), contextlib.ExitStack() as stack:
                if "file_patch" in case:
                    target, fake = case["file_patch"]
                    stack.enter_context(mock.patch.object(target, "__file__", str(fake)))
                with self.assertRaisesRegex(ValueError, "^V4_BASE_MODULE_ORIGIN_INVALID$") as caught:
                    self._load(module_edit=case.get("module_edit"))
                self.assertEqual(type(caught.exception).__name__, "ThemeApplicationError")


class DecisionEvidenceBytesTests(unittest.TestCase):
    """Amendment SHA and exact-committed-bytes branches in a synthetic trusted repo.

    The synthetic repository borrows this checkout's object store through git
    alternates only so the pinned base-profile commit is resolvable; its own
    history contains just the synthetic commit below.
    """

    CONTRACT_REL = "config/kr_internal_paper_theme_next_session_contract_v4.json"
    COMMITTED_AT = "2026-09-13T13:00:00+00:00"

    def _resolve(self, *, committed=None, worktree=None):
        contract = V4._expected_next_session_contract_v4()
        relatives = (
            self.CONTRACT_REL,
            contract["base_profile"]["contract_path"],
            contract["predecessor_contract"]["contract_path"],
            contract["predecessor_decision_evidence"]["path"],
            contract["decision_evidence"]["path"],
        )
        common = subprocess.check_output(["git", "rev-parse", "--git-common-dir"], cwd=ROOT, text=True).strip()
        objects = (ROOT / common).resolve() / "objects"
        with _git_repo() as (repo, commit):
            alternates = repo / ".git" / "objects" / "info" / "alternates"
            alternates.parent.mkdir(parents=True, exist_ok=True)
            alternates.write_text(f"{objects}\n", encoding="utf-8")
            for relative in relatives:
                raw = (committed or {}).get(relative, (ROOT / relative).read_bytes())
                if raw is None:
                    continue
                (repo / relative).parent.mkdir(parents=True, exist_ok=True)
                (repo / relative).write_bytes(raw)
            commit("synthetic v4 decision evidence", self.COMMITTED_AT)
            for relative, raw in (worktree or {}).items():
                (repo / relative).parent.mkdir(parents=True, exist_ok=True)
                (repo / relative).write_bytes(raw)
            return V4.resolve_next_session_decision_v4(_head(repo), repo / self.CONTRACT_REL)

    def _amendment(self):
        relative = V4._expected_next_session_contract_v4()["decision_evidence"]["path"]
        return relative, (ROOT / relative).read_bytes()

    def test_synthetic_repo_control_is_adopted(self):
        result = self._resolve()
        self.assertEqual(result["status"], "ADOPTED_EXACT_D_TO_E_SCOPE_V2")
        self.assertEqual(result["amendment_evidence_first_seen_at"], "2026-09-13T13:00:00Z")
        self.assertEqual(result["contract_first_seen_at"], "2026-09-13T13:00:00Z")

    def test_amendment_sha_mismatch_raises(self):
        relative, raw = self._amendment()
        tampered = raw + b"\n"
        self.assertEqual(json.loads(tampered), json.loads(raw))
        with self.assertRaisesRegex(V4.ThemeApplicationError, "^NEXT_SESSION_AMENDMENT_EVIDENCE_SHA_MISMATCH$"):
            self._resolve(committed={relative: tampered})

    def test_amendment_not_exact_committed_bytes_raises(self):
        relative, raw = self._amendment()
        for label, committed in (
            ("committed_bytes_differ", {relative: raw + b"\n"}),
            ("absent_from_trusted_commit", {relative: None}),
        ):
            with self.subTest(case=label):
                with self.assertRaisesRegex(
                    V4.ThemeApplicationError, "^NEXT_SESSION_AMENDMENT_EVIDENCE_NOT_EXACT_COMMITTED_BYTES$"
                ):
                    self._resolve(committed=committed, worktree={relative: raw})

    def test_contract_not_exact_committed_bytes_raises(self):
        raw = (ROOT / self.CONTRACT_REL).read_bytes()
        with self.assertRaisesRegex(V4.ThemeApplicationError, "^NEXT_SESSION_CONTRACT_V4_NOT_EXACT_COMMITTED_BYTES$"):
            self._resolve(committed={self.CONTRACT_REL: raw + b"\n"}, worktree={self.CONTRACT_REL: raw})


if __name__ == "__main__":
    unittest.main(verbosity=2)
