#!/usr/bin/env python3
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rotation import kr_internal_paper_theme_application as APP


class ContractAndAdmissionTests(unittest.TestCase):
    def test_contract_is_exact_bounded_profile(self):
        contract = APP.load_contract()
        self.assertEqual(
            contract["allowed_asset_ids"],
            ["KR:XKRX:000660", "KR:XKRX:005930"],
        )
        self.assertFalse(contract["authority"]["global_taxonomy_authority_changed"])
        self.assertFalse(contract["authority"]["real_authority"])
        self.assertFalse(contract["authority"]["trading_authorized"])

    def test_approval_evidence_binds_complete_determining_payload(self):
        registry = json.loads(APP.REGISTRY_PATH.read_text(encoding="utf-8"))
        record = APP._validate_registry_document(registry, APP.load_contract())
        evidence_path = ROOT / record["approval_evidence_ref"]
        evidence_raw = evidence_path.read_bytes()
        evidence = json.loads(evidence_raw.decode("utf-8"))
        self.assertEqual(hashlib.sha256(evidence_raw).hexdigest(), record["approval_evidence_sha256"])
        self.assertEqual(evidence["determining_payload"], APP.determining_payload(record))
        self.assertEqual(
            evidence["approved_full_payload_sha256"],
            APP.payload_sha256(APP.determining_payload(record)),
        )

    def test_scope_or_authority_drift_fails_closed(self):
        registry = json.loads(APP.REGISTRY_PATH.read_text(encoding="utf-8"))
        expanded = copy.deepcopy(registry)
        expanded["records"][0]["allowed_asset_ids"].append("KR:XKRX:999999")
        with self.assertRaisesRegex(APP.ThemeApplicationError, "REGISTRY_SCOPE_OR_DECISION_MISMATCH"):
            APP._validate_registry_document(expanded, APP.load_contract())
        promoted = copy.deepcopy(registry)
        promoted["records"][0]["real_authority"] = True
        with self.assertRaisesRegex(APP.ThemeApplicationError, "REGISTRY_AUTHORITY_EXPANDED"):
            APP._validate_registry_document(promoted, APP.load_contract())

    def test_committed_source_admission_and_first_seen_are_verified(self):
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        result = APP.resolve_source_admission(head)
        self.assertEqual(result["status"], "RATIFIED_EXACT_SCOPE")
        self.assertEqual(result["source_manifest_first_seen_at"], "2026-09-07T15:32:29Z")
        self.assertEqual(
            set(result["source_snapshot_first_seen_at"].values()),
            {"2026-09-07T15:32:29Z"},
        )
        self.assertGreaterEqual(
            result["admission_real_usable_from"],
            result["registry_record_first_seen_at"],
        )

    def test_committed_next_session_decision_is_independently_verified(self):
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        result = APP.resolve_next_session_decision(head)
        self.assertEqual(result["status"], "ADOPTED_EXACT_D_TO_E_SCOPE")
        self.assertGreaterEqual(
            result["decision_real_usable_from"],
            result["decision_evidence_first_seen_at"],
        )


class ActualTimeMembershipTests(unittest.TestCase):
    usable = "2026-09-07T16:05:50Z"  # 2026-09-08 01:05:50 KST

    def test_prior_observation_date_has_empty_interval(self):
        result = APP.evaluate_membership_times(
            "2026-09-07", self.usable, "2026-09-08T02:00:00Z"
        )
        self.assertFalse(result["nonempty"])
        self.assertFalse(result["active"])
        self.assertEqual(result["status"], "UNKNOWN_EMPTY_MEMBERSHIP_INTERVAL")

    def test_same_kst_day_evaluation_and_execution_can_be_active(self):
        result = APP.evaluate_membership_times(
            "2026-09-08",
            self.usable,
            "2026-09-08T10:00:00+09:00",
            "2026-09-08T15:00:00+09:00",
        )
        self.assertTrue(result["nonempty"])
        self.assertTrue(result["evaluation_active"])
        self.assertTrue(result["forward_execution_active"])
        self.assertTrue(result["active"])

    def test_next_day_1000_kst_carryover_fails(self):
        result = APP.evaluate_membership_times(
            "2026-09-08",
            self.usable,
            "2026-09-09T10:00:00+09:00",
            "2026-09-09T10:01:00+09:00",
        )
        self.assertFalse(result["evaluation_active"])
        self.assertFalse(result["forward_execution_active"])
        self.assertFalse(result["active"])
        self.assertEqual(result["status"], "UNKNOWN_EVALUATION_OUTSIDE_MEMBERSHIP_INTERVAL")

    def test_next_midnight_is_exclusive(self):
        result = APP.evaluate_membership_times(
            "2026-09-08",
            self.usable,
            "2026-09-08T23:59:59+09:00",
            "2026-09-09T00:00:00+09:00",
        )
        self.assertTrue(result["evaluation_active"])
        self.assertFalse(result["forward_execution_active"])
        self.assertFalse(result["active"])
        self.assertEqual(result["status"], "UNKNOWN_FORWARD_EXECUTION_OUTSIDE_MEMBERSHIP_INTERVAL")

    def test_naive_actual_time_is_rejected(self):
        with self.assertRaisesRegex(APP.ThemeApplicationError, "EVALUATION_AT_INVALID"):
            APP.evaluate_membership_times("2026-09-08", self.usable, "2026-09-08T10:00:00")


class ExistingValidatorReuseTests(unittest.TestCase):
    def test_real_master_and_leadership_are_independently_validated_before_date_mismatch(self):
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        master = ROOT / "data/observations/krx_global_universe/2026-08-28/packet.json"
        leadership = ROOT / "data/observations/korea_leadership_context/2026-09-04/packet.json"
        with self.assertRaisesRegex(APP.ThemeApplicationError, "OBSERVATION_DATE_MISMATCH"):
            APP.evaluate_application(
                master,
                leadership,
                "2026-09-08T10:00:00+09:00",
                head,
            )


class SyntheticEndToEndTests(unittest.TestCase):
    """Exercise the full reducer with explicitly synthetic source packets."""

    observation_date = "2026-09-08"
    evaluation_at = "2026-09-08T19:00:00+09:00"

    @staticmethod
    def _krx_row(code: str, name: str, market: str) -> dict:
        return {
            "BAS_DD": "20260908",
            "ISU_CD": code,
            "ISU_NM": name,
            "MKT_NM": market,
            "SECT_TP_NM": "SYNTHETIC_VALIDATOR_FIXTURE",
            "TDD_CLSPRC": "100",
            "CMPPREVDD_PRC": "1",
            "FLUC_RT": "1.00",
            "TDD_OPNPRC": "99",
            "TDD_HGPRC": "101",
            "TDD_LWPRC": "98",
            "ACC_TRDVOL": "1000",
            "ACC_TRDVAL": "100000",
            "MKTCAP": "1000000",
            "LIST_SHRS": "10000",
        }

    @classmethod
    def _snapshot(cls, market: str, rows: list[dict]) -> dict:
        body = json.dumps(
            {"OutBlock_1": rows}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        endpoint = {"KOSPI": "stk_bydd_trd", "KOSDAQ": "ksq_bydd_trd"}[market]
        return {
            "market": market,
            "response_body_base64": base64.b64encode(body).decode("ascii"),
            "source_identity": {
                "source_id": "krx_open_api_stock_daily",
                "source_url": (
                    f"https://data-dbg.krx.co.kr/svc/apis/sto/{endpoint}?basDd=20260908"
                ),
                "source_sha256": hashlib.sha256(body).hexdigest(),
                "available_at": "2026-09-08T00:20:00Z",
                "retrieved_at_utc": "2026-09-08T00:30:00Z",
            },
        }

    @classmethod
    def _master(cls) -> dict:
        kru = APP._load_module("synthetic_krx_universe_for_application_test", "universe/krx_global_universe.py")
        source = {
            "schema_version": "krx_global_universe_input/1",
            "master_id": "SYNTHETIC.KR.THEME.APPLICATION.20260908",
            "as_of_date": cls.observation_date,
            "snapshots": [
                cls._snapshot("KOSPI", [
                    cls._krx_row("000660", "SYNTHETIC SK HYNIX", "KOSPI"),
                    cls._krx_row("005930", "SYNTHETIC SAMSUNG", "KOSPI"),
                ]),
                cls._snapshot("KOSDAQ", [
                    cls._krx_row("999999", "SYNTHETIC CONTROL", "KOSDAQ"),
                ]),
            ],
        }
        return kru.build_packet(source)

    @classmethod
    def _leadership(cls) -> dict:
        producer = APP._load_module(
            "synthetic_korea_leadership_for_application_test",
            ".github/scripts/korea_leadership.py",
        )
        policy = producer.load_policy(producer.POLICY_PATH)
        active = sorted({
            row["series_identity"]
            for row in policy["records"]
            if row["effective_from"] <= cls.observation_date
            and (row["effective_to"] is None or cls.observation_date < row["effective_to"])
        })
        packet = producer.build_transform({
            "schema_version": 1,
            "source_name": policy["source_name"],
            "market": policy["market"],
            "market_timezone": policy["market_timezone"],
            "run_mode": "FORWARD_SHADOW",
            "observation_date": cls.observation_date,
            "fetched_at": "2026-09-08T18:05:00+09:00",
            "available_at": "2026-09-08T18:00:00+09:00",
            "decision_at": "2026-09-08T18:10:00+09:00",
            "expected_session_dates": ["2026-09-07", cls.observation_date],
            "series_rows": [
                {
                    "series_identity": identity,
                    "rows": [
                        {"session_date": "2026-09-07", "close": "100"},
                        {"session_date": cls.observation_date, "close": str(101 + index)},
                    ],
                }
                for index, identity in enumerate(active)
            ],
        })
        wrapper = {
            "generated_at": "2026-09-08T09:10:00Z",
            "observation_date": cls.observation_date,
            "prior_date": "2026-09-07",
            "leadership_packet_sha256": packet["payload_sha256"],
            "leadership_packet": packet,
            "markets": ["KOSDAQ", "KOSPI"],
            "outcome": "synthetic_validator_fixture",
            "reason": "SYNTHETIC_ONLY_NOT_MARKET_EVIDENCE",
            "schema_version": "korea_leadership_live_fetch/1",
        }
        wrapper["payload_sha256"] = APP.payload_sha256(
            {key: value for key, value in wrapper.items() if key != "payload_sha256"}
        )
        return wrapper

    @staticmethod
    def _admission() -> dict:
        return {
            "status": "RATIFIED_EXACT_SCOPE",
            "application_scope": "KR_INTERNAL_PAPER_BASELINE_V0_ENTRY_FILTER",
            "allowed_asset_ids": ["KR:XKRX:000660", "KR:XKRX:005930"],
            "allowed_canonical_instrument_ids": ["KRX:000660:COMMON", "KRX:005930:COMMON"],
            "theme_id": "THEME.KR.KOSPI.ELECTRICAL_ELECTRONIC_EQUIPMENT",
            "rotation_series_identity": "KOSPI::전기전자",
            "admission_real_usable_from": "2026-09-07T16:34:25Z",
        }

    def _evaluate(self, first_seen: str) -> dict:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        with (
            mock.patch.object(APP, "resolve_source_admission", return_value=self._admission()),
            mock.patch.object(
                APP,
                "_load_exact_packet",
                side_effect=[(self._master(), first_seen), (self._leadership(), first_seen)],
            ),
        ):
            return APP.evaluate_application(
                Path("SYNTHETIC_MASTER_PACKET"),
                Path("SYNTHETIC_LEADERSHIP_PACKET"),
                self.evaluation_at,
                head,
                "2026-09-08T19:10:00+09:00",
            )

    def test_full_validator_path_emits_bounded_active_input(self):
        result = self._evaluate("2026-09-08T00:45:00Z")
        self.assertEqual(result["status"], "ACTIVE_BOUNDED_INTERNAL_PAPER_INPUT")
        self.assertTrue(result["authority"]["bounded_internal_paper_entry_filter_input_authorized"])
        self.assertEqual(
            [row["canonical_instrument_id"] for row in result["assets"]],
            ["KRX:000660:COMMON", "KRX:005930:COMMON"],
        )
        self.assertEqual(result["rotation_series_identity"], "KOSPI::전기전자")
        self.assertFalse(result["authority"]["real_authority"])
        self.assertFalse(result["authority"]["baseline_entry_eligibility_authorized"])
        self.assertFalse(result["authority"]["new_entry_authorized"])
        self.assertFalse(result["authority"]["trading_authorized"])

    def test_future_packet_first_seen_keeps_input_unauthorized(self):
        result = self._evaluate("2026-09-08T10:30:00Z")
        self.assertEqual(result["status"], "UNKNOWN_INPUTS_NOT_AVAILABLE_BY_EVALUATION")
        self.assertFalse(result["inputs_available_by_evaluation"])
        self.assertFalse(result["authority"]["bounded_internal_paper_entry_filter_input_authorized"])


class SyntheticNextSessionTests(unittest.TestCase):
    """Exercise the adopted D-to-E mechanism; fixtures are never market evidence."""

    context_date = "2026-09-08"
    execution_date = "2026-09-09"
    evaluation_at = "2026-09-09T15:00:00+09:00"

    @classmethod
    def _master(cls, day: str, retrieved_at: str) -> dict:
        day8 = day.replace("-", "")
        kru = APP._load_module(
            f"synthetic_next_session_krx_universe_{day8}",
            "universe/krx_global_universe.py",
        )

        def row(code: str, name: str, market: str) -> dict:
            value = SyntheticEndToEndTests._krx_row(code, name, market)
            value["BAS_DD"] = day8
            return value

        def snapshot(market: str, rows: list[dict]) -> dict:
            body = json.dumps(
                {"OutBlock_1": rows}, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            endpoint = {"KOSPI": "stk_bydd_trd", "KOSDAQ": "ksq_bydd_trd"}[market]
            return {
                "market": market,
                "response_body_base64": base64.b64encode(body).decode("ascii"),
                "source_identity": {
                    "source_id": "krx_open_api_stock_daily",
                    "source_url": (
                        f"https://data-dbg.krx.co.kr/svc/apis/sto/{endpoint}?basDd={day8}"
                    ),
                    "source_sha256": hashlib.sha256(body).hexdigest(),
                    "available_at": day,
                    "retrieved_at_utc": retrieved_at,
                },
            }

        return kru.build_packet({
            "schema_version": "krx_global_universe_input/1",
            "master_id": f"SYNTHETIC.KR.NEXT.SESSION.{day8}",
            "as_of_date": day,
            "snapshots": [
                snapshot("KOSPI", [
                    row("000660", "SYNTHETIC SK HYNIX", "KOSPI"),
                    row("005930", "SYNTHETIC SAMSUNG", "KOSPI"),
                ]),
                snapshot("KOSDAQ", [row("999999", "SYNTHETIC CONTROL", "KOSDAQ")]),
            ],
        })

    @classmethod
    def _relation(
        cls,
        previous_date: str | None = None,
        execution_date: str | None = None,
    ) -> dict:
        source = json.loads(
            (ROOT / "data/observations/korea_market_signals/2026-09-04/packet.json").read_text(
                encoding="utf-8"
            )
        )
        source.update({
            "previous_date": previous_date or cls.context_date,
            "as_of_date": execution_date or cls.execution_date,
            "generated_at": "2026-09-09T00:10:00Z",
            "available_at": "2026-09-09T00:10:00Z",
        })
        source["payload_sha256"] = APP.payload_sha256(
            {key: value for key, value in source.items() if key != "payload_sha256"}
        )
        return source

    @staticmethod
    def _calendar(day: str, status: str) -> dict:
        source_ref = f"fixture:ctca0903r:{day}"
        source_sha256 = hashlib.sha256(source_ref.encode("utf-8")).hexdigest()
        calendar = {
            "session_date": day,
            "status": status,
            "timezone": "Asia/Seoul",
            "open_at": f"{day}T09:00:00+09:00" if status == "OPEN_REGULAR" else None,
            "close_at": f"{day}T15:30:00+09:00" if status == "OPEN_REGULAR" else None,
            "observed_at": "2026-08-30T00:00:00Z",
            "available_at": "2026-08-30T00:00:00Z",
            "source_ref": source_ref,
            "source_sha256": source_sha256,
            "provider_id": "KIS_OPEN_API_DOMESTIC_HOLIDAY_CTCA0903R",
            "market_rule_source": "KRX_EQUITY_MARKET_OPERATION_RULES",
        }
        return {
            "schema_version": "krx_date_specific_session_source/1",
            "as_of_date": day,
            "official_response_ref": source_ref,
            "official_response_sha256": source_sha256,
            "calendar": calendar,
        }

    @staticmethod
    def _decision() -> dict:
        return {
            "status": "ADOPTED_EXACT_D_TO_E_SCOPE",
            "decision_id": "KR_INTERNAL_PAPER_PREVIOUS_COMPLETED_SESSION_CONTEXT_V1",
            "decision_real_usable_from": "2026-09-07T16:50:00Z",
        }

    def _evaluate(
        self,
        *,
        relation_previous: str | None = None,
        e_first_seen: str = "2026-09-09T00:05:00Z",
        relation_first_seen: str = "2026-09-09T00:15:00Z",
        execution_at: str = "2026-09-09T15:05:00+09:00",
        calendar_statuses: list[str] | None = None,
    ) -> dict:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        packets = [
            (self._master(self.context_date, "2026-09-08T00:30:00Z"), "2026-09-08T00:35:00Z"),
            (SyntheticEndToEndTests._leadership(), "2026-09-08T09:15:00Z"),
            (self._master(self.execution_date, "2026-09-09T00:00:00Z"), e_first_seen),
            (self._relation(relation_previous), relation_first_seen),
        ]
        if calendar_statuses is None:
            calendar_statuses = ["OPEN_REGULAR", "OPEN_REGULAR"]
        with tempfile.TemporaryDirectory() as directory:
            calendar_paths = [
                Path(directory) / f"calendar-{index}.json"
                for index in range(len(calendar_statuses))
            ]
            for path, day, status in zip(
                calendar_paths,
                [self.context_date, self.execution_date],
                calendar_statuses,
            ):
                packet = self._calendar(day, status)
                path.write_text(json.dumps(packet, sort_keys=True), encoding="utf-8")
                packets.append((packet, "2026-08-30T00:00:00Z"))
            with (
                mock.patch.object(
                    APP,
                    "resolve_source_admission",
                    return_value=SyntheticEndToEndTests._admission(),
                ),
                mock.patch.object(APP, "resolve_next_session_decision", return_value=self._decision()),
                mock.patch.object(APP, "_load_exact_packet", side_effect=packets),
            ):
                return APP.evaluate_next_session_application(
                    Path("SYNTHETIC_D_MASTER"),
                    Path("SYNTHETIC_D_LEADERSHIP"),
                    Path("SYNTHETIC_E_MASTER"),
                    Path("SYNTHETIC_D_E_RELATION"),
                    calendar_paths,
                    self.evaluation_at,
                    execution_at,
                    head,
                )

    def test_immediate_previous_session_context_is_bounded_input_only(self):
        result = self._evaluate()
        self.assertEqual(result["status"], "ACTIVE_PREVIOUS_COMPLETED_SESSION_CONTEXT_INPUT")
        self.assertEqual(result["context_session_date"], self.context_date)
        self.assertEqual(result["execution_session_date"], self.execution_date)
        self.assertTrue(result["session_relation_exact"])
        self.assertTrue(result["authority"]["previous_completed_session_context_input_authorized"])
        self.assertFalse(result["authority"]["new_entry_authorized"])
        self.assertFalse(result["authority"]["regime_gate_authorized"])
        self.assertFalse(result["context_series_observation"]["top_bucket_verified"])

    def test_future_available_execution_input_is_rejected(self):
        result = self._evaluate(e_first_seen="2026-09-09T06:10:00Z")
        self.assertEqual(result["status"], "UNKNOWN_INPUT_AVAILABLE_AFTER_EVALUATION")
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])

    def test_non_immediate_context_session_is_rejected(self):
        result = self._evaluate(relation_previous="2026-09-07")
        self.assertEqual(result["status"], "UNKNOWN_CONTEXT_NOT_IMMEDIATE_PREVIOUS_SESSION")
        self.assertFalse(result["session_relation_exact"])
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])

    def test_execution_after_e_session_close_is_rejected(self):
        result = self._evaluate(execution_at="2026-09-09T15:31:00+09:00")
        self.assertEqual(result["status"], "UNKNOWN_EXECUTION_MEMBERSHIP_EXPIRED")
        self.assertFalse(result["execution_membership"]["forward_execution_active"])
        self.assertFalse(result["authority"]["previous_completed_session_context_input_authorized"])

    def test_missing_calendar_evidence_is_unknown(self):
        result = self._evaluate(calendar_statuses=[])
        self.assertEqual(result["status"], "UNKNOWN_SESSION_CALENDAR_EVIDENCE_MISSING")
        self.assertFalse(result["session_calendar_verified"])

    def test_committed_calendar_proves_weekend_and_rejects_intervening_open(self):
        for middle_status, expected in (
            ("CLOSED", None),
            ("OPEN_REGULAR", "SESSION_CALENDAR_INTERVENING_OPEN_SESSION"),
        ):
            with self.subTest(middle_status=middle_status), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
                subprocess.run(["git", "config", "user.name", "Synthetic Test"], cwd=repo, check=True)
                subprocess.run(["git", "config", "user.email", "synthetic@example.invalid"], cwd=repo, check=True)
                days = ["2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31"]
                statuses = ["OPEN_REGULAR", middle_status, "CLOSED", "OPEN_REGULAR"]
                paths = []
                for day, status in zip(days, statuses):
                    path = repo / f"calendar-{day}.json"
                    path.write_text(
                        json.dumps(self._calendar(day, status), sort_keys=True),
                        encoding="utf-8",
                    )
                    paths.append(path)
                subprocess.run(["git", "add", "."], cwd=repo, check=True)
                environment = os.environ.copy()
                environment.update(
                    GIT_AUTHOR_DATE="2026-08-30T01:00:00+00:00",
                    GIT_COMMITTER_DATE="2026-08-30T01:00:00+00:00",
                )
                subprocess.run(
                    ["git", "commit", "-q", "-m", "synthetic calendar evidence"],
                    cwd=repo,
                    env=environment,
                    check=True,
                )
                head = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=repo, text=True
                ).strip()
                if expected is None:
                    result = APP.verify_immediate_session_calendar(
                        paths,
                        "2026-08-28",
                        "2026-08-31",
                        "2026-08-31T05:00:00Z",
                        repo,
                        head,
                    )
                    self.assertTrue(result["verified"])
                    self.assertEqual(
                        [row["status"] for row in result["sessions"]],
                        statuses,
                    )
                else:
                    with self.assertRaisesRegex(APP.ThemeApplicationError, expected):
                        APP.verify_immediate_session_calendar(
                            paths,
                            "2026-08-28",
                            "2026-08-31",
                            "2026-08-31T05:00:00Z",
                            repo,
                            head,
                        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
