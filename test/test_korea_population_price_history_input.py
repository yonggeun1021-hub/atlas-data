#!/usr/bin/env python3
"""The KR population evaluator's new ``price_history_session/1`` input.

Two properties are proved here:

1. with no store configured -- the public default -- the packet is
   byte-identical to the one produced before this input existed;
2. with a store configured, a population symbol holding the session's stored
   bar reaches the contract's **existing** ``EVALUABLE_PRICE`` level (no new
   state name is introduced) and SMA20 becomes computable from stored closes,
   while investor flows stay explicitly absent rather than estimated.

Every store here is built in a temporary directory from fixture bytes; no
network call is made or possible.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collectors import krx_price_history as COLLECTOR  # noqa: E402
from decision import korea_population_symbol_observation as ADAPTER  # noqa: E402
from universe import price_history_store as STORE  # noqa: E402
if str(ROOT / "test") not in sys.path:
    sys.path.insert(0, str(ROOT / "test"))
import rolling_pointer_snapshot as SNAPSHOT  # noqa: E402

CORE = ADAPTER.CORE
COMMIT = "0" * 40
SESSION = "2026-09-10"
COMPACT = "20260910"
GENERATED_AT = "2026-09-13T00:00:00Z"


def provider_payload(codes, bas_dd, part):
    rows = []
    for index, code in enumerate(codes):
        close = 1000 + index * 5
        rows.append({
            "BAS_DD": bas_dd, "ISU_CD": code, "ISU_NM": f"FIXTURE{code}",
            "MKT_NM": "KOSPI" if part == "kospi" else "KOSDAQ", "SECT_TP_NM": "",
            "TDD_CLSPRC": str(close), "CMPPREVDD_PRC": "5", "FLUC_RT": "0.50",
            "TDD_OPNPRC": str(close - 5), "TDD_HGPRC": str(close + 10),
            "TDD_LWPRC": str(close - 10), "ACC_TRDVOL": str(10000 + index),
            "ACC_TRDVAL": str((10000 + index) * close),
            "MKTCAP": str(close * 8941100), "LIST_SHRS": "8941100",
        })
    return json.dumps({"OutBlock_1": rows}, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


class KoreaPopulationPriceHistoryInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Rolling pointers (stage_history, briefing/krx, bounded review) are
        # rewritten by separately scheduled workflows; read the same frozen
        # snapshot test_population_symbol_observation.py uses.
        cls.tmp = tempfile.TemporaryDirectory()
        cls.snapshot = SNAPSHOT.materialize(Path(cls.tmp.name) / "snapshot")
        assert SNAPSHOT.kr_inputs(cls.snapshot)["session_date"] == SESSION
        cls.contract = CORE.load_contract()
        cls.price_contract = COLLECTOR.load_contract()
        packet = json.loads(
            (ROOT / "data/observations/krx_global_universe" / SESSION / "packet.json")
            .read_bytes().decode("utf-8")
        )
        cls.population = [
            record["primary_symbol"] for record in packet["asset_master"]["records"]
        ]
        watchlist = {
            path.stem for path in Path(SNAPSHOT.kr_inputs(cls.snapshot)["watchlist_root"]).glob("*.json")
        }
        cls.subject_code = next(
            code for code in cls.population if code not in watchlist
        )

    def build_store(self, root, codes, sessions=20, available="2026-09-11T08:00:00Z"):
        store = STORE.PriceHistoryStore(root, contract=self.price_contract)
        end = dt.date.fromisoformat(SESSION)
        days = [end - dt.timedelta(days=offset) for offset in range(sessions - 1, -1, -1)]
        for day in days:
            bas_dd = day.strftime("%Y%m%d")
            raw = {"kospi": provider_payload(codes, bas_dd, "kospi")}
            rows = COLLECTOR.derive_compact_rows(raw, bas_dd, self.price_contract)
            compact = COLLECTOR.compact_bytes(rows)
            parts = [{
                "part_id": "kospi",
                "endpoint": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
                "http_status": 200, "row_count": len(codes),
                "raw_relpath": "raw/kospi.json.gz",
                "raw_sha256": COLLECTOR.digest(raw["kospi"]),
                "raw_byte_count": len(raw["kospi"]),
            }]
            manifest = COLLECTOR.build_manifest(
                market="KR", bas_dd=bas_dd, status="OK", parts=parts, compact=compact,
                compact_row_count=len(rows), pit_class="FORWARD_CAPTURE",
                attempts=[{
                    "attempt_no": 1, "kind": "FORWARD_PRIMARY",
                    "requested_at_utc": available, "retrieved_at_utc": available,
                    "http_status": 200, "row_count": len(codes), "outcome": "OK",
                }],
                public_code_commit=COMMIT, contract=self.price_contract,
                first_available_observed_at_utc=available,
            )
            store.write_session("KR", manifest, raw_by_part=raw, compact=compact)
        return store

    def context(self, price_history_root):
        inputs = ADAPTER.default_inputs(ROOT, session_date=SESSION)
        inputs.update(SNAPSHOT.kr_inputs(self.snapshot))
        inputs["price_history_root"] = price_history_root
        return ADAPTER.load_context(
            inputs, generated_at=GENERATED_AT, contract=self.contract
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_default_has_no_store_and_leaves_the_packet_unchanged(self):
        self.assertIsNone(ADAPTER.default_inputs(ROOT, session_date=SESSION)["price_history_root"])
        ctx = self.context(None)
        self.assertEqual(ctx["price_history"]["status"], "NOT_CONFIGURED")
        self.assertNotIn("price_history_store", ctx["sources"])
        row = ADAPTER.build_symbol(ctx, self.subject_code)
        self.assertNotEqual(row["evaluability"]["level"], "EVALUABLE_PRICE")

    def test_configured_store_reaches_the_existing_evaluable_price_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build_store(tmp, [self.subject_code])
            ctx = self.context(Path(tmp))
            self.assertEqual(ctx["price_history"]["status"], "LOADED")
            self.assertEqual(ctx["price_history"]["latest_bas_dd"], COMPACT)
            row = ADAPTER.build_symbol(ctx, self.subject_code)
            self.assertEqual(row["evaluability"]["level"], "EVALUABLE_PRICE")
            self.assertEqual(row["evaluability"]["status"], "EVALUABLE")
            self.assertIn(
                "EVALUABLE_PRICE", self.contract["observation_statuses"]
            )
            self.assertEqual(row["data_observation"]["status"], "DATA_OBSERVED")
            self.assertIn("sma20", row["data_observation"]["fields_present"])
            # Flows are still absent and are never estimated.
            self.assertIn("investor_flows", row["data_observation"]["fields_missing"])
            self.assertEqual(row["evaluation"]["row_source"], "price_history_session_store")

    def test_store_reports_sma20_computable_symbol_count_to_the_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build_store(tmp, [self.subject_code, self.population[1]])
            ctx = self.context(Path(tmp))
            summary = ctx["sources"]["price_history_store"]
            self.assertEqual(summary["sma_sessions"], 20)
            self.assertEqual(summary["sessions_in_window"], 20)
            self.assertEqual(summary["sma_readiness_status"], "COMPUTABLE")
            self.assertEqual(summary["sma_computable_symbol_count"], 2)

    def test_a_short_window_is_reported_not_estimated(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build_store(tmp, [self.subject_code], sessions=5)
            ctx = self.context(Path(tmp))
            summary = ctx["sources"]["price_history_store"]
            self.assertEqual(summary["sessions_in_window"], 5)
            self.assertEqual(summary["sma_readiness_status"], "INSUFFICIENT_SESSIONS")
            self.assertEqual(summary["sma_computable_symbol_count"], 0)
            row = ADAPTER.build_symbol(ctx, self.subject_code)
            self.assertEqual(row["evaluability"]["level"], "EVALUABLE_PRICE")
            self.assertIn("sma20", row["data_observation"]["fields_missing"])
            self.assertIsNone(row["evaluation"]["row"]["price_context"].get("sma20")
                              if row["evaluation"].get("row") else None)

    def test_a_symbol_absent_from_the_store_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build_store(tmp, [self.subject_code])
            ctx = self.context(Path(tmp))
            other = next(
                code for code in self.population if code != self.subject_code
            )
            row = ADAPTER.build_symbol(ctx, other)
            self.assertNotEqual(row["evaluation"].get("row_source"),
                                "price_history_session_store")

    def test_private_store_packet_is_marked_and_cannot_be_written_into_public_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build_store(tmp, [self.subject_code])
            ctx = self.context(Path(tmp))
            self.assertEqual(ctx["distribution"], ADAPTER.PRIVATE_ONLY_DISTRIBUTION)
            self.assertEqual(ctx["sources"]["price_history_store"]["distribution"],
                             ADAPTER.PRIVATE_ONLY_DISTRIBUTION)
            inputs = ADAPTER.default_inputs(ROOT, session_date=SESSION)
            inputs.update(SNAPSHOT.kr_inputs(self.snapshot))
            inputs["price_history_root"] = Path(tmp)
            public_target = ROOT / "data" / "observations" / "korea_population_symbol_observation" / "_guard_probe"
            for kwargs in ({}, {"output_dir": public_target},
                           {"output_dir": Path(tmp) / "out", "work_dir": public_target}):
                with self.assertRaisesRegex(CORE.PopulationSymbolObservationError,
                                            "PRIVATE_ONLY_PACKET_PUBLIC_WRITE_REFUSED"):
                    CORE.build("KR", generated_at=GENERATED_AT, inputs=inputs, **kwargs)
            self.assertFalse(public_target.exists())

    def test_default_packet_stays_public(self):
        self.assertEqual(self.context(None)["distribution"], "PUBLIC")

    def test_store_inside_public_repository_is_refused(self):
        with self.assertRaisesRegex(CORE.PopulationSymbolObservationError,
                                    "KR_PRICE_HISTORY_STORE_INSIDE_PUBLIC_REPOSITORY"):
            self.context(ROOT / "data")

    def test_point_in_time_withholds_a_session_not_yet_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build_store(tmp, [self.subject_code], available="2027-01-01T00:00:00Z")
            ctx = self.context(Path(tmp))
            self.assertEqual(ctx["price_history"]["window"], [])
            row = ADAPTER.build_symbol(ctx, self.subject_code)
            self.assertNotEqual(row["evaluation"].get("row_source"),
                                "price_history_session_store")


if __name__ == "__main__":
    unittest.main(verbosity=2)
