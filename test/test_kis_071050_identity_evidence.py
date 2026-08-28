#!/usr/bin/env python3
"""Exact KIS 071050 common-share evidence and authority-boundary regression."""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity import canonical_identity as ci  # noqa: E402


OBSERVATION_PATH = (
    ROOT / "evidence" / "identity" / "observations" / "2026-08-28"
    / "kis-kospi-master-071050-share-class.json"
)
TRAILING_FIELD_BYTES = 227
PREFERRED_STOCK_CLASS_OFFSET = 158


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decode_row(entry: dict) -> dict:
    row = base64.b64decode(entry["row_raw_base64"], validate=True)
    if hashlib.sha256(row).hexdigest() != entry["row_sha256"]:
        raise AssertionError("raw row hash mismatch")
    part1 = row[:-TRAILING_FIELD_BYTES]
    part2 = row[-TRAILING_FIELD_BYTES:]
    return {
        "short_code": part1[0:9].decode("cp949").strip(),
        "standard_product_number": part1[9:21].decode("ascii").strip(),
        "korean_name": part1[21:].decode("cp949").strip(),
        "security_group_code": part2[0:2].decode("ascii").strip(),
        "preferred_stock_class_code": part2[
            PREFERRED_STOCK_CLASS_OFFSET:PREFERRED_STOCK_CLASS_OFFSET + 1
        ].decode("ascii"),
    }


class Kis071050MasterObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = json.loads(OBSERVATION_PATH.read_text(encoding="utf-8"))

    def test_source_and_official_layout_are_exactly_pinned(self):
        self.assertEqual(
            self.record["source"],
            {
                "provider": "KIS_PUBLIC_MASTER",
                "url": "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
                "archive_sha256": "8de794458d38e4304b0b1f69c9de0f2b4ab71ea5781585653d83b2d5c0d13be1",
                "member": "kospi_code.mst",
                "master_sha256": "abfec9c79eca665741b6189fc88214961088067782791f9c90aa0715c510b4a2",
            },
        )
        layout = self.record["official_layout"]
        self.assertEqual(
            (layout["repository"], layout["commit_sha"]),
            (
                "koreainvestment/open-trading-api",
                "b4e6249714418aa57833d1cbbbced39cbcc5b125",
            ),
        )
        self.assertEqual(layout["preferred_stock_class_field"], "prst_cls_code")
        self.assertEqual(layout["code_meanings"]["0"], "COMMON_STOCK")
        self.assertEqual(layout["code_meanings"]["1"], "PREFERRED_STOCK_OLD")

    def test_raw_target_row_independently_decodes_as_071050_common_stock(self):
        decoded = _decode_row(self.record["target"])
        self.assertEqual(
            decoded,
            {
                "short_code": "071050",
                "standard_product_number": "KR7071050009",
                "korean_name": "한국금융지주",
                "security_group_code": "ST",
                "preferred_stock_class_code": "0",
            },
        )
        expected = dict(decoded, official_share_class="COMMON_STOCK")
        self.assertEqual(self.record["target"]["observation"], expected)

    def test_adjacent_071055_row_proves_common_preferred_separation(self):
        preferred = self.record["common_preferred_confusion_counterexample"]
        decoded = _decode_row(preferred)
        self.assertEqual(
            decoded,
            {
                "short_code": "071055",
                "standard_product_number": "KR7071051007",
                "korean_name": "한국금융지주우",
                "security_group_code": "ST",
                "preferred_stock_class_code": "1",
            },
        )
        self.assertNotEqual(
            self.record["target"]["observation"]["standard_product_number"],
            decoded["standard_product_number"],
        )
        self.assertNotEqual(
            self.record["target"]["observation"]["preferred_stock_class_code"],
            decoded["preferred_stock_class_code"],
        )

    def test_locked_capture_attestation_is_read_only_and_exactly_bound(self):
        attestation = self.record["locked_private_capture_attestation"]
        self.assertEqual(
            attestation["record_sha256"],
            "047c0adb0b38b6989a9f0cf2ed8196c9897cc2c6d5b71b4d0b827c748b8e6fda",
        )
        self.assertEqual(attestation["preflight_status"], "PASS_LOCKED_READY")
        self.assertTrue(attestation["exact_allowlisted_symbol_matched_once"])
        self.assertTrue(attestation["common_stock_class_observed"])
        self.assertFalse(attestation["broker_read_attempted"])
        self.assertFalse(attestation["order_submission_attempted"])
        self.assertTrue(all(value is False for value in self.record["authority"].values()))


class Kis071050ApprovalBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.authority = ci.load_authority()

    def test_exact_four_row_identity_chain_has_verified_approval_bytes(self):
        expected = (
            (ci.LAYER_ISSUER, "issuers", "atlas.identity.issuer.korea-investment-holdings"),
            (
                ci.LAYER_INSTRUMENT, "instruments",
                "atlas.identity.instrument.korea-investment-holdings-common",
            ),
            (
                ci.LAYER_LISTING, "listings",
                "atlas.identity.listing.korea-investment-holdings-common",
            ),
            (
                ci.LAYER_SOURCE_ALIAS, "source_aliases",
                "atlas.identity.alias.kis-paper-korea-investment-holdings-common",
            ),
        )
        for layer, key, rule_id in expected:
            matches = [row for row in self.authority[key] if row["rule_id"] == rule_id]
            self.assertEqual(len(matches), 1, rule_id)
            row = matches[0]
            self.assertTrue(ci.verify_business_payload(row, layer), rule_id)
            self.assertTrue(ci.verify_approval_evidence(row, layer), rule_id)
            approval_path = ROOT / row["approval_evidence_ref"]
            self.assertEqual(_sha(approval_path), row["approval_evidence_sha256"])
            approval = json.loads(approval_path.read_text(encoding="utf-8"))
            for source in approval["source_evidence"]:
                source_path = ROOT / source["path"]
                self.assertTrue(source_path.is_file(), source)
                self.assertEqual(_sha(source_path), source["sha256"], source)
            self.assertEqual(
                approval["boundary"],
                "MECHANICAL_IDENTITY_OR_SCOPE_ONLY_NO_INVESTMENT_OR_TRADING_AUTHORITY",
            )

    def test_no_other_kis_balance_alias_is_ratified(self):
        aliases = [
            row for row in self.authority["source_aliases"]
            if row.get("source_name") == "kis_paper_domestic_balance"
        ]
        self.assertEqual(
            [(row["source_asset_id"], row["listing_id"]) for row in aliases],
            [("071050", "XKRX:071050")],
        )


if __name__ == "__main__":
    unittest.main()
