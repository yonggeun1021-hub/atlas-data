#!/usr/bin/env python3
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
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
    evaluation_at = "2026-09-08T10:00:00+09:00"

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
        source_path = ROOT / "data/observations/korea_leadership_context/2026-09-04/packet.json"
        wrapper = json.loads(source_path.read_text(encoding="utf-8"))
        packet = wrapper["leadership_packet"]
        packet["observation_date"] = cls.observation_date
        packet["available_at"] = "2026-09-08T09:40:00+09:00"
        packet["window"] = {
            "first_input_session": "2026-09-07",
            "first_return_session": cls.observation_date,
            "last_return_session": cls.observation_date,
            "lookback_sessions": 1,
            "exact_expected_sessions": True,
        }
        packet["payload_sha256"] = APP.payload_sha256(
            {key: value for key, value in packet.items() if key != "payload_sha256"}
        )
        wrapper.update({
            "generated_at": "2026-09-08T00:40:00Z",
            "observation_date": cls.observation_date,
            "prior_date": "2026-09-07",
            "leadership_packet_sha256": packet["payload_sha256"],
        })
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
                "2026-09-08T15:00:00+09:00",
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
        self.assertFalse(result["authority"]["trading_authorized"])

    def test_future_packet_first_seen_keeps_input_unauthorized(self):
        result = self._evaluate("2026-09-08T01:30:00Z")
        self.assertEqual(result["status"], "UNKNOWN_INPUTS_NOT_AVAILABLE_BY_EVALUATION")
        self.assertFalse(result["inputs_available_by_evaluation"])
        self.assertFalse(result["authority"]["bounded_internal_paper_entry_filter_input_authorized"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
