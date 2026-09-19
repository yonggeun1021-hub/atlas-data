"""TKT-3 security_sector_membership/1 regression (KR, KIS_ONLY, synthetic masters)."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universe" / "security_sector_membership.py"
SPEC = importlib.util.spec_from_file_location("security_sector_membership", MODULE_PATH)
MEM = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MEM)
REG = MEM.REG

CONTRACT = MEM.load_contract()
REGISTRY_CONTRACT = REG.load_contract()
T1 = "2026-09-13T14:21:33Z"
T2 = "2026-09-14T14:21:33Z"
T3 = "2026-09-15T14:21:33Z"


def _tail(market: str, values: dict) -> str:
    widths, fields = REG.MASTER_LAYOUT[market]
    defaults = {"security_group": "ST", "preferred_code": "0", "spac": "N", "etp_code": "0",
                "sector_large": "0000", "sector_medium": "0000", "sector_small": "0000"}
    merged = {**defaults, **values}
    out = []
    for field, width in zip(fields, widths):
        text = str(merged.get(field, ""))
        assert len(text) <= width, (field, text)
        out.append(text.ljust(width))
    return "".join(out)


def master_line(market: str, short: str, name: str, **values) -> bytes:
    standard = values.pop("standard", f"KR7{short}00{'3' if market == 'KOSPI' else '8'}")
    head = short.ljust(9).encode("ascii") + standard.ljust(12).encode("ascii") + name.encode("cp949")
    return head + _tail(market, values).encode("ascii")


def idx_line(group: str, code: str, name: str) -> bytes:
    body = name.encode("cp949")
    return (group + code).encode("ascii") + body + b" " * (40 - len(body))


def default_idx_names() -> dict:
    return {(row["market"], row["kis_sector_code"]): row["source_name"] for row in CONTRACT["sector_code_table"]}


class Workspace:
    def __init__(self, root: Path):
        self.root = root
        self.counter = 0

    def write_zip(self, member: str, lines: list[bytes]) -> Path:
        self.counter += 1
        path = self.root / f"{self.counter:03d}_{member}.zip"
        info = zipfile.ZipInfo(member, date_time=(2026, 9, 12, 5, 58, 0))  # fixed: archive bytes are deterministic
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(info, b"\n".join(lines) + b"\n")
        return path

    def publication(self, *, session: str, observed_at: str, kospi: list[bytes], kosdaq: list[bytes],
                    idx_names: dict | None = None, previous: dict | None = None) -> dict:
        names = default_idx_names() if idx_names is None else idx_names
        idx = [idx_line("0" if market == "KOSPI" else "1", code, name) for (market, code), name in sorted(names.items())]
        prev_path = None
        if previous is not None:
            self.counter += 1
            prev_path = self.root / f"{self.counter:03d}_previous.json"
            prev_path.write_text(json.dumps(previous, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        value = {
            "schema_version": MEM.INPUT_SCHEMA_VERSION,
            "as_of_session": session,
            "masters": {
                "KOSPI": {"path": str(self.write_zip("kospi_code.mst", kospi)), "source_url": "https://example.invalid/kospi",
                          "retrieved_at_utc": observed_at, "http_last_modified": None},
                "KOSDAQ": {"path": str(self.write_zip("kosdaq_code.mst", kosdaq)), "source_url": "https://example.invalid/kosdaq",
                           "retrieved_at_utc": observed_at, "http_last_modified": None},
            },
            "idxcode": {"path": str(self.write_zip("idxcode.mst", idx)), "source_url": "https://example.invalid/idx",
                        "retrieved_at_utc": observed_at, "http_last_modified": None},
            "previous_document_path": None if prev_path is None else str(prev_path),
        }
        return MEM.build_document(value, CONTRACT)


def base_kospi() -> list[bytes]:
    return [
        master_line("KOSPI", "005930", "삼성전자", sector_large="0027", sector_medium="0013"),
        master_line("KOSPI", "000660", "SK하이닉스", sector_large="0027", sector_medium="0013"),
        master_line("KOSPI", "139480", "이마트", sector_large="0016"),
        master_line("KOSPI", "900001", "제조만", sector_large="0027"),
        master_line("KOSPI", "005830", "증권사", sector_large="0021", sector_medium="0024"),
        master_line("KOSPI", "004970", "무코드"),
        master_line("KOSPI", "005935", "삼성전자우", sector_large="0027", sector_medium="0013", preferred_code="1"),
        master_line("KOSPI", "069500", "ETF", security_group="EF", etp_code="1"),
    ]


def base_kosdaq() -> list[bytes]:
    return [
        master_line("KOSDAQ", "247540", "에코", sector_large="1009", sector_medium="1028"),
        master_line("KOSDAQ", "900002", "무코드닥"),
        master_line("KOSDAQ", "900003", "스팩", sector_large="1014", spac="Y"),
    ]


class ContractTests(unittest.TestCase):
    def test_binding_hash_is_pinned_and_covers_all_46_series(self):
        binding = MEM.load_binding(CONTRACT)
        self.assertEqual(len(binding), 46)
        self.assertEqual(CONTRACT["taxonomy_binding"]["document_payload_sha256"],
                         "6027e89b70766599bac4a242aef5bf608f7979628a6e8b5cbaf23219cab287e3")
        self.assertEqual(len(CONTRACT["sector_code_table"]), 46)

    def test_exactly_two_ratified_aliases(self):
        aliases = {(a["market"], a["kis_sector_code"], a["source_name"], a["series_identity"]) for a in CONTRACT["ratified_aliases"]}
        self.assertEqual(aliases, {
            ("KOSPI", "0013", "전기·전자", "KOSPI::전기전자"),
            ("KOSDAQ", "1028", "전기·전자", "KOSDAQ::전기전자"),
        })
        for row in CONTRACT["sector_code_table"]:
            if row["match"] == "EXACT":
                self.assertEqual(row["series_identity"], f"{row['market']}::{row['source_name']}")

    def test_extra_alias_rejected(self):
        contract = copy.deepcopy(CONTRACT)
        row = next(r for r in contract["sector_code_table"] if r["market"] == "KOSPI" and r["kis_sector_code"] == "0020")
        row["match"] = "RATIFIED_ALIAS"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contract.json"
            path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(MEM.MembershipError, "CONTRACT_ALIAS_TABLE_MISMATCH"):
                MEM.load_contract(path)

    def test_authority_promotion_rejected(self):
        contract = copy.deepcopy(CONTRACT)
        contract["authority"]["t2_eligibility_granted"] = True
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contract.json"
            path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(MEM.MembershipError, "CONTRACT_AUTHORITY_INVALID"):
                MEM.load_contract(path)

    def test_binding_pin_tamper_rejected(self):
        contract = copy.deepcopy(CONTRACT)
        contract["taxonomy_binding"]["document_payload_sha256"] = "0" * 64
        with self.assertRaisesRegex(MEM.MembershipError, "BINDING_DOCUMENT_NOT_PINNED"):
            MEM.load_binding(contract)

    def test_parent_view_matches_decision_d3(self):
        parents = {p["parent_series_identity"]: p for p in CONTRACT["parent_view"]}
        self.assertEqual(set(parents), {"KOSPI::제조", "KOSDAQ::제조", "KOSPI::금융"})
        self.assertIn("KOSPI.SECTOR.18", parents["KOSPI::제조"]["child_membership_ids"])
        self.assertEqual(parents["KOSPI::금융"]["child_kis_sector_codes"], ["0024", "0025"])

    def test_path_a_crosswalk_matches_registry(self):
        registry = json.loads((ROOT / "config" / "kr_internal_paper_theme_source_admission_registry.json").read_text(encoding="utf-8"))
        binding = MEM.load_binding(CONTRACT)
        for entry in CONTRACT["path_a_crosswalk"]:
            record = next(r for r in registry["records"] if r["theme_id"] == entry["path_a_theme_id"])
            self.assertEqual(record["rotation_series_identity"], entry["rotation_series_identity"])
            self.assertEqual(binding[entry["rotation_series_identity"]], entry["membership_id"])
        self.assertEqual(CONTRACT["path_a_crosswalk"][0]["membership_id"], "KOSPI.SECTOR.18")

    def test_source_policy_is_single_source_paper_only(self):
        policy = CONTRACT["source_policy"]
        self.assertEqual(policy["decision_sha256"], "1fb9a8d525491dbcacac379b36d07c8c50c0b8ae90700e9de51bf83f106cf220")
        self.assertEqual((policy["kr_sector_membership_source"], policy["source_count"], policy["verification"]),
                         ("KIS_ONLY", 1, "SINGLE_SOURCE_KIS"))
        self.assertTrue(policy["satisfies_t2_c5"])
        self.assertFalse(policy["satisfies_t3"])


class GenesisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(Path(self.tmp.name))
        self.doc = self.ws.publication(session="2026-09-11", observed_at=T1, kospi=base_kospi(), kosdaq=base_kosdaq())

    def tearDown(self):
        self.tmp.cleanup()

    def row(self, asset_id: str) -> dict:
        rows = [r for r in self.doc["rows"] if r["asset_id"] == asset_id and r["effective_to"] is None]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_regression_005930_000660_map_to_kospi_sector_18(self):
        for asset_id in ("KR:XKRX:005930", "KR:XKRX:000660"):
            row = self.row(asset_id)
            self.assertEqual((row["status"], row["membership_id"], row["series_identity"]),
                             ("ACTIVE", "KOSPI.SECTOR.18", "KOSPI::전기전자"))
            self.assertEqual((row["leaf_level"], row["parent_membership_id"]), ("sector_medium", "KOSPI.SECTOR.20"))
            self.assertEqual((row["source_count"], row["verification"]), (1, "SINGLE_SOURCE_KIS"))
            self.assertEqual(row["effective_from"], T1)
        self.assertEqual(MEM.members(self.doc, "KOSPI.SECTOR.18", T1), ["KR:XKRX:000660", "KR:XKRX:005930"])

    def test_at_most_one_active_per_asset_and_deepest_level(self):
        counts = {}
        for r in self.doc["rows"]:
            if r["status"] == "ACTIVE":
                counts[r["asset_id"]] = counts.get(r["asset_id"], 0) + 1
        self.assertTrue(all(v == 1 for v in counts.values()))
        self.assertEqual(self.row("KR:XKRX:139480")["membership_id"], "KOSPI.SECTOR.13")  # large-only 유통
        self.assertEqual(self.row("KR:XKRX:139480")["parent_membership_id"], None)
        self.assertEqual(self.row("KR:XKRX:900001")["membership_id"], "KOSPI.SECTOR.20")  # 제조 without medium
        securities = self.row("KR:XKRX:005830")
        self.assertEqual((securities["membership_id"], securities["parent_membership_id"]), ("KOSPI.SECTOR.22", "KOSPI.SECTOR.04"))
        self.assertEqual(MEM.members(self.doc, "KOSPI.SECTOR.20", T1), ["KR:XKRX:900001"])  # parents never ACTIVE for children
        kosdaq = self.row("KR:XKRX:247540")
        self.assertEqual((kosdaq["membership_id"], kosdaq["parent_membership_id"]), ("KOSDAQ.SECTOR.16", "KOSDAQ.SECTOR.18"))

    def test_no_code_stock_stays_unmapped(self):
        for asset_id in ("KR:XKRX:004970", "KR:XKRX:900002"):
            row = self.row(asset_id)
            self.assertEqual((row["status"], row["unmapped_reason"], row["membership_id"]), ("UNMAPPED", "NO_SECTOR_CODE", None))
        self.assertEqual(self.doc["counts"]["KOSPI"]["by_status"]["UNMAPPED"], 1)

    def test_non_common_products_are_excluded_not_unmapped(self):
        ids = {r["asset_id"] for r in self.doc["rows"]}
        for asset_id in ("KR:XKRX:005935", "KR:XKRX:069500", "KR:XKRX:900003"):
            self.assertNotIn(asset_id, ids)
        excluded = self.doc["publication"]["excluded_non_common_stock"]
        self.assertEqual(excluded["KOSPI"], {"PREFERRED_STOCK": 1, "ETF": 1})
        self.assertEqual(excluded["KOSDAQ"], {"SPAC": 1})

    def test_c5_active_parent_and_blocked_cases(self):
        c5 = MEM.c5_rotation_membership
        self.assertEqual(c5(self.doc, "KR:XKRX:005930", ["KOSPI.SECTOR.18"], T1)["reason"], "ACTIVE_SERIES_SELECTED")
        via_parent = c5(self.doc, "KR:XKRX:005930", ["KOSPI.SECTOR.20"], T1)
        self.assertEqual((via_parent["result"], via_parent["via_parent"]), ("PASS", True))
        self.assertFalse(via_parent["t3_two_source_verified"])
        self.assertEqual(c5(self.doc, "KR:XKRX:005930", ["KOSPI.SECTOR.04"], T1)["result"], "FAIL")
        self.assertEqual(c5(self.doc, "KR:XKRX:004970", ["KOSPI.SECTOR.18"], T1)["reason"], "MEMBERSHIP_UNMAPPED")
        self.assertEqual(c5(self.doc, "KR:XKRX:005930", ["KOSPI.SECTOR.18"], "2026-09-13T00:00:00Z")["reason"],
                         "MEMBERSHIP_NOT_OBSERVED")  # no backdating before first observation

    def test_deterministic_and_self_hashed(self):
        again = self.ws.publication(session="2026-09-11", observed_at=T1, kospi=base_kospi(), kosdaq=base_kosdaq())
        self.assertEqual(MEM.canonical_json(again), MEM.canonical_json(self.doc))
        tampered = copy.deepcopy(self.doc)
        tampered["rows"][0]["membership_id"] = "KOSPI.SECTOR.01"
        with self.assertRaises(MEM.MembershipError):
            MEM.validate_document(tampered, CONTRACT)
        resigned = MEM._self_hashed(tampered)
        with self.assertRaisesRegex(MEM.MembershipError, "ROW_ID_MISMATCH|ROW_MEMBERSHIP_INVALID|ROW_PARENT_VIEW_INVALID|DOCUMENT_COUNTS_MISMATCH"):
            MEM.validate_document(resigned, CONTRACT)

    def test_public_summary_has_counts_only(self):
        summary = MEM.build_public_summary(self.doc, CONTRACT)
        text = MEM.canonical_json(summary)
        for code in ("005930", "000660", "139480", "004970", "KR:XKRX", "row_sha256"):
            self.assertNotIn(code, text)
        self.assertEqual(summary["coverage"]["total"], {"common_stocks": 8, "active": 6, "pending_change": 0, "unmapped": 2})
        self.assertEqual(summary["active_by_membership_id"]["KOSPI"]["KOSPI.SECTOR.18"], 2)
        self.assertEqual(summary["private_document_payload_sha256"], self.doc["payload_sha256"])
        self.assertTrue(all(v is False for v in summary["authority"].values()))


class ClassificationGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def open_row(self, doc, asset_id):
        return next(r for r in doc["rows"] if r["asset_id"] == asset_id and r["effective_to"] is None)

    def test_renamed_index_code_is_unmapped_without_normalization(self):
        names = default_idx_names()
        names[("KOSPI", "0016")] = "유 통"
        doc = self.ws.publication(session="2026-09-11", observed_at=T1, kospi=base_kospi(), kosdaq=base_kosdaq(), idx_names=names)
        row = self.open_row(doc, "KR:XKRX:139480")
        self.assertEqual((row["status"], row["unmapped_reason"]), ("UNMAPPED", "SECTOR_CODE_NAME_CHANGED"))

    def test_alias_source_name_must_match_exactly(self):
        names = default_idx_names()
        names[("KOSPI", "0013")] = "전기전자"  # the binding spelling is not the ratified source name
        doc = self.ws.publication(session="2026-09-11", observed_at=T1, kospi=base_kospi(), kosdaq=base_kosdaq(), idx_names=names)
        self.assertEqual(self.open_row(doc, "KR:XKRX:005930")["unmapped_reason"], "SECTOR_CODE_NAME_CHANGED")

    def test_undeclared_hierarchy_and_codes_are_unmapped(self):
        kospi = [
            master_line("KOSPI", "100001", "유통아래화학", sector_large="0016", sector_medium="0008"),
            master_line("KOSPI", "100002", "중분류만", sector_medium="0013"),
            master_line("KOSPI", "100003", "없는코드", sector_large="0099"),
            master_line("KOSPI", "100004", "소분류", sector_large="0027", sector_medium="0013", sector_small="0001"),
        ]
        doc = self.ws.publication(session="2026-09-11", observed_at=T1, kospi=kospi, kosdaq=base_kosdaq())
        reasons = {r["asset_id"]: r["unmapped_reason"] for r in doc["rows"] if r["market"] == "KOSPI"}
        self.assertEqual(reasons, {
            "KR:XKRX:100001": "HIERARCHY_UNDECLARED",
            "KR:XKRX:100002": "HIERARCHY_UNDECLARED",
            "KR:XKRX:100003": "SECTOR_CODE_NOT_IN_TABLE",
            "KR:XKRX:100004": "SECTOR_SMALL_LEVEL_UNDECLARED",
        })


class ChangeDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(Path(self.tmp.name))
        self.d1 = self.ws.publication(session="2026-09-11", observed_at=T1, kospi=base_kospi(), kosdaq=base_kosdaq())

    def tearDown(self):
        self.tmp.cleanup()

    def changed_kospi(self):
        lines = base_kospi()
        lines[0] = master_line("KOSPI", "005930", "삼성전자", sector_large="0027", sector_medium="0015")
        return lines

    def test_change_is_pending_for_one_publication_then_active(self):
        d2 = self.ws.publication(session="2026-09-14", observed_at=T2, kospi=self.changed_kospi(), kosdaq=base_kosdaq(), previous=self.d1)
        rows = [r for r in d2["rows"] if r["asset_id"] == "KR:XKRX:005930"]
        self.assertEqual([(r["status"], r["membership_id"], r["effective_from"], r["effective_to"]) for r in rows], [
            ("ACTIVE", "KOSPI.SECTOR.18", T1, T2),
            ("PENDING_CHANGE", "KOSPI.SECTOR.12", T2, None),
        ])
        self.assertEqual(MEM.c5_rotation_membership(d2, "KR:XKRX:005930", ["KOSPI.SECTOR.12"], T2)["reason"], "MEMBERSHIP_PENDING_CHANGE")
        self.assertEqual(MEM.members(d2, "KOSPI.SECTOR.18", "2026-09-14T00:00:00Z"), ["KR:XKRX:000660", "KR:XKRX:005930"])
        self.assertNotIn("KR:XKRX:005930", MEM.members(d2, "KOSPI.SECTOR.12", T2))
        d3 = self.ws.publication(session="2026-09-15", observed_at=T3, kospi=self.changed_kospi(), kosdaq=base_kosdaq(), previous=d2)
        self.assertIn("KR:XKRX:005930", MEM.members(d3, "KOSPI.SECTOR.12", T3))
        self.assertEqual(MEM.c5_rotation_membership(d3, "KR:XKRX:005930", ["KOSPI.SECTOR.12"], T3)["result"], "PASS")
        unchanged = [r for r in d3["rows"] if r["asset_id"] == "KR:XKRX:000660"]
        self.assertEqual(len(unchanged), 1)
        self.assertEqual(unchanged[0]["effective_from"], T1)

    def test_unmapped_to_mapped_is_a_change(self):
        lines = base_kospi()
        lines[5] = master_line("KOSPI", "004970", "무코드", sector_large="0016")
        d2 = self.ws.publication(session="2026-09-14", observed_at=T2, kospi=lines, kosdaq=base_kosdaq(), previous=self.d1)
        row = next(r for r in d2["rows"] if r["asset_id"] == "KR:XKRX:004970" and r["effective_to"] is None)
        self.assertEqual(row["status"], "PENDING_CHANGE")

    def test_delisting_and_code_reuse(self):
        lines = [l for l in base_kospi() if not l.startswith(b"139480")]
        lines[1] = master_line("KOSPI", "000660", "새종목", sector_large="0016", standard="KR7000660999")
        d2 = self.ws.publication(session="2026-09-14", observed_at=T2, kospi=lines, kosdaq=base_kosdaq(), previous=self.d1)
        delisted = [r for r in d2["rows"] if r["asset_id"] == "KR:XKRX:139480"]
        self.assertEqual([(r["effective_to"]) for r in delisted], [T2])
        reused = [r for r in d2["rows"] if r["asset_id"] == "KR:XKRX:000660"]
        self.assertEqual([(r["kis_standard_code"], r["status"], r["effective_to"]) for r in reused], [
            ("KR7000660003", "ACTIVE", T2),
            ("KR7000660999", "ACTIVE", None),
        ])

    def test_publication_must_be_later(self):
        with self.assertRaisesRegex(MEM.MembershipError, "PUBLICATION_NOT_AFTER_PREVIOUS"):
            self.ws.publication(session="2026-09-11", observed_at=T2, kospi=base_kospi(), kosdaq=base_kosdaq(), previous=self.d1)
        with self.assertRaisesRegex(MEM.MembershipError, "PUBLICATION_NOT_AFTER_PREVIOUS"):
            self.ws.publication(session="2026-09-14", observed_at=T1, kospi=base_kospi(), kosdaq=base_kosdaq(), previous=self.d1)

    def test_retroactive_edits_rejected(self):
        d2 = self.ws.publication(session="2026-09-14", observed_at=T2, kospi=self.changed_kospi(), kosdaq=base_kosdaq(), previous=self.d1)
        backdated = copy.deepcopy(d2)
        row = next(r for r in backdated["rows"] if r["status"] == "PENDING_CHANGE")
        row["effective_from"] = T1
        row["row_id"] = MEM._row_id(row)
        with self.assertRaisesRegex(MEM.MembershipError, "SUCCESSOR_NEW_ROW_BACKDATED|ROW_INTERVALS_OVERLAP"):
            MEM.validate_successor(self.d1, backdated)
        rewritten = copy.deepcopy(d2)
        old = next(r for r in rewritten["rows"] if r["asset_id"] == "KR:XKRX:000660")
        old["effective_to"] = "2026-09-13T20:00:00Z"
        old["effective_to_session"] = "2026-09-14"
        with self.assertRaisesRegex(MEM.MembershipError, "SUCCESSOR_RETROACTIVE_CLOSE"):
            MEM.validate_successor(self.d1, rewritten)
        dropped = copy.deepcopy(d2)
        dropped["rows"] = [r for r in dropped["rows"] if r["asset_id"] != "KR:XKRX:139480"]
        with self.assertRaisesRegex(MEM.MembershipError, "SUCCESSOR_DROPPED_ROW"):
            MEM.validate_successor(self.d1, dropped)


class StaticSurfaceTests(unittest.TestCase):
    def test_module_has_no_network_imports(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        self.assertFalse(roots & {"requests", "urllib", "http", "socket", "pykrx", "subprocess"})

    def test_cli_rejects_bad_input_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "input.json"
            bad.write_text(json.dumps({"schema_version": "wrong"}), encoding="utf-8")
            out_doc, out_summary = Path(tmp) / "doc.json", Path(tmp) / "summary.json"
            with mock.patch("sys.stderr"):
                code = MEM.main(["--input", str(bad), "--output-document", str(out_doc), "--output-summary", str(out_summary)])
            self.assertEqual(code, 2)
            self.assertFalse(out_doc.exists() or out_summary.exists())


if __name__ == "__main__":
    unittest.main()
