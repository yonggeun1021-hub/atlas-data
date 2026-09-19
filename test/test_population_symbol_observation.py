#!/usr/bin/env python3
"""KR/US full-population symbol observation packet regression.

Runs on real committed inputs into temporary output directories and
checks: every population symbol appears exactly once with one status per
axis, bounded review rows are byte-identical, missing inputs are reported
(never estimated), no stage tag or promotion is produced, the generation id
makes reruns idempotent, chunked resume reproduces the uninterrupted packet,
tampering fails closed, and a fresh-process re-read succeeds.

Inputs are pinned: the rolling pointers (stage_history.json,
data/briefing/krx, latest market packets and bounded reviews) come from the
frozen consistent snapshot in test/rolling_pointer_snapshot.py, and the
dated universe packets / KRX capture are read in place under recorded hashes.
The live pointers are rewritten by separately scheduled workflows hours
apart, so the default inputs are not one snapshot for most of a weekday.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "population_symbol_observation.py"
SPEC = importlib.util.spec_from_file_location("population_symbol_observation", SOURCE)
CORE = importlib.util.module_from_spec(SPEC)
sys.modules["population_symbol_observation"] = CORE
assert SPEC.loader is not None
SPEC.loader.exec_module(CORE)
if str(ROOT / "test") not in sys.path:
    sys.path.insert(0, str(ROOT / "test"))
import rolling_pointer_snapshot as SNAPSHOT  # noqa: E402


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pinned_inputs(market: str, snapshot_root: Path) -> dict:
    return SNAPSHOT.kr_inputs(snapshot_root) if market == "KR" else SNAPSHOT.us_inputs(snapshot_root)


class _MarketMixin:
    market = ""
    compress = False

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.snapshot = SNAPSHOT.materialize(Path(cls.tmp.name) / "snapshot")
        cls.inputs = pinned_inputs(cls.market, cls.snapshot)
        cls.out = Path(cls.tmp.name) / cls.market
        cls.lookup_at = now_utc()
        cls.stage_sha_before = _sha(cls.inputs["stage_history_path"])
        cls.live_stage_sha_before = _sha(ROOT / "data" / "stage_history.json")
        cls.result = CORE.build(cls.market, generated_at=cls.lookup_at, inputs=cls.inputs, output_dir=cls.out, compress=cls.compress)
        cls.packet = cls.result["packet"]
        cls.rows = {row["symbol"]: row for row in cls.packet["symbols"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_population_symbol_appears_once_with_one_status_per_axis(self):
        packet = self.packet
        self.assertEqual(CORE.validate_packet(packet), packet)
        self.assertEqual(len(packet["symbols"]), packet["population"]["count"])
        self.assertEqual(len(self.rows), packet["population"]["count"])
        contract = CORE.load_contract()
        for row in packet["symbols"]:
            self.assertIn(row["observation_status"], contract["observation_statuses"])
            self.assertIn(row["data_observation"]["status"], contract["data_observation_statuses"])
            self.assertIn(row["evaluability"]["status"], contract["evaluability_statuses"])
            self.assertIn(row["evaluation"]["status"], contract["evaluation_statuses"])
            self.assertIn(row["formal_candidate"]["status"], contract["formal_candidate_statuses"])
            if row["evaluability"]["status"] == "NOT_EVALUABLE":
                self.assertTrue(row["evaluability"]["reasons"])
                self.assertNotEqual(row["evaluation"]["status"], "EVALUATED")
            if row["evaluation"]["status"] == "NOT_EVALUATED":
                self.assertIsNone(row["evaluation"]["entry_state"])
                self.assertIsNone(row["evaluation"]["row"])
        summary = packet["summary"]
        counts = packet["status_counts"]
        self.assertEqual(sum(counts["observation_status"].values()), packet["population"]["count"])
        self.assertEqual(summary["evaluated_count"], counts["evaluation"].get("EVALUATED", 0) + counts["evaluation"].get("EVALUATED_BOUNDED", 0))
        self.assertEqual(summary["evaluable_count"], counts["evaluability"].get("EVALUABLE", 0))
        self.assertEqual(summary["not_evaluable_count"], counts["evaluability"].get("NOT_EVALUABLE", 0))
        self.assertEqual(summary["passed_count"], 0)
        self.assertTrue(packet["reconciliation"]["population_equals_rows"])
        self.assertTrue(packet["reconciliation"]["bounded_rows_byte_identical_to_review"])

    def test_formal_candidates_come_only_from_existing_stage_tags_and_nothing_is_promoted(self):
        stages = _json(self.inputs["stage_history_path"])
        latest = stages[sorted(stages)[-1]]
        tagged = {s for s, row in latest.items() if isinstance(row.get("stage"), str) and s in self.rows}
        formal = {s for s, row in self.rows.items() if row["formal_candidate"]["status"] == "PIPELINE_SUBJECT"}
        self.assertEqual(formal, tagged)
        for row in self.packet["symbols"]:
            self.assertFalse(row["formal_candidate"]["promotion_by_this_packet"])
            self.assertEqual(row["formal_candidate"]["basis"], "notion_atlas_stage_tag_via_stage_history_only")
        # the run never touches the stage tags
        self.assertEqual(_sha(self.inputs["stage_history_path"]), self.stage_sha_before)
        self.assertEqual(_sha(ROOT / "data" / "stage_history.json"), self.live_stage_sha_before)
        self.assertTrue(all(v is False for k, v in self.packet["authority"].items() if k != "observation_only"))

    def test_bounded_review_subjects_are_copied_not_recomputed(self):
        bounded = _json(self.inputs["bounded_review_path"])
        for row in bounded["symbols"]:
            mine = self.rows[row["symbol"]]
            self.assertEqual(mine["evaluation"]["status"], "EVALUATED_BOUNDED")
            self.assertEqual(mine["evaluation"]["row"], row)
            self.assertEqual(mine["evaluation"]["evaluated_at"], bounded["generated_at"])
            self.assertEqual(mine["observation_status"], "EVALUATED_BOUNDED")
            self.assertTrue(mine["formal_candidate"]["bounded_review_subject"])

    def test_packet_generated_at_is_input_derived_and_rerun_is_idempotent(self):
        self.assertEqual(self.packet["generated_at_semantics"], "INPUT_SNAPSHOT_TIME_MAX_OF_SOURCE_TIMESTAMPS_NOT_WALL_CLOCK")
        self.assertLessEqual(self.packet["generated_at"], self.lookup_at)
        later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        again = CORE.build(self.market, generated_at=later, inputs=self.inputs, output_dir=self.out, compress=self.compress)
        self.assertEqual(again["persist"]["outcome"], "verified_existing")
        self.assertEqual(again["packet"], self.packet)
        self.assertEqual(again["resume"]["reused_chunks"], again["resume"]["planned_chunks"])
        self.assertEqual(again["receipt"]["lookup_at"], later)

    def test_fresh_process_reverify_and_summary_sidecar(self):
        completed = subprocess.run(
            [sys.executable, str(SOURCE), "--market", self.market, "--reverify", "--output-dir", str(self.out)],
            capture_output=True, text=True, check=True,
        )
        verified = json.loads(completed.stdout)
        self.assertEqual(verified["outcome"], "REVERIFIED")
        self.assertEqual(verified["payload_sha256"], self.packet["payload_sha256"])
        self.assertEqual(verified["generation_id"], self.packet["generation_id"])
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["summary"], self.packet["summary"])
        self.assertEqual(summary["payload_sha256"], self.packet["payload_sha256"])

    def test_tampered_packet_or_summary_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "packet.json"
            tampered = json.loads(json.dumps(self.packet))
            tampered["summary"]["passed_count"] = 1
            target.write_text(json.dumps(tampered, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            (Path(tmp) / "summary.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(CORE.PopulationSymbolObservationError):
                CORE.reverify(Path(tmp))
            # rehashed tamper of an existing packet is refused on rebuild
            unsigned = dict(tampered)
            unsigned.pop("payload_sha256")
            tampered["payload_sha256"] = CORE.payload_sha256(unsigned)
            target.write_text(json.dumps(tampered, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            with self.assertRaises(CORE.PopulationSymbolObservationError) as ctx:
                CORE.persist_packet(self.packet, Path(tmp))
            self.assertIn("EXISTING_PACKET_DRIFT_OR_TAMPER", str(ctx.exception))


class KoreaPopulationTests(_MarketMixin, unittest.TestCase):
    market = "KR"

    def test_kr_session_only_rows_report_missing_sma20_and_flows_not_estimates(self):
        watch_files = {p.stem for p in Path(self.inputs["watchlist_root"]).glob("*.json")}
        session_only = [row for s, row in self.rows.items() if s not in watch_files and row["data_observation"]["status"] == "DATA_OBSERVED"]
        self.assertTrue(session_only)
        for row in session_only:
            self.assertEqual(row["observation_status"], "EVALUABLE_SESSION_PRICE_ONLY")
            self.assertEqual(row["evaluability"]["status"], "NOT_EVALUABLE")
            reasons = row["evaluability"]["reasons"]
            self.assertTrue(any(r.startswith("SMA20_NOT_COMPUTABLE:RETAINED_SESSIONS=") for r in reasons))
            self.assertIn("INVESTOR_FLOW_NOT_AVAILABLE", reasons)
            self.assertEqual(row["evaluation"]["status"], "NOT_EVALUATED")
            self.assertIsNotNone(row["facts"]["session_close"])
            partial = row["evaluation"]["partial_review"]
            self.assertFalse(partial["is_evaluation"])
            self.assertEqual(partial["entry_state"], "BLOCKED")
            self.assertEqual(partial["price_status"], "OBSERVED_CONFIRMED_NO_SMA20")
        self.assertEqual(self.packet["summary"]["evaluated_without_full_inputs_count"], 0)

    def test_kr_watchlist_symbols_without_stage_are_evaluated_but_not_formal(self):
        stages = _json(self.inputs["stage_history_path"])
        latest = stages[sorted(stages)[-1]]
        watch_files = {p.stem for p in Path(self.inputs["watchlist_root"]).glob("*.json")}
        untagged = [s for s in watch_files if s in self.rows and latest.get(s, {}).get("stage") is None]
        self.assertTrue(untagged)
        for symbol in untagged:
            row = self.rows[symbol]
            self.assertEqual(row["evaluation"]["status"], "EVALUATED")
            self.assertEqual(row["evaluation"]["row_source"], "extracted_symbol_row")
            self.assertEqual(row["evaluation"]["entry_state"], "WAIT")
            self.assertIn("PIPELINE_STAGE_NOT_ASSIGNED", row["evaluation"]["reasons"])
            self.assertEqual(row["formal_candidate"]["status"], "NOT_A_FORMAL_CANDIDATE")
            self.assertEqual(row["evaluation"]["evaluated_at_basis"], "INPUT_SNAPSHOT_TIME")

    def test_kr_interrupted_run_resumes_to_the_identical_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "KR"
            first = CORE.build("KR", generated_at=self.lookup_at, inputs=self.inputs, output_dir=out, max_chunks=2)
            self.assertIsNone(first["packet"])
            self.assertFalse(first["resume"]["complete"])
            self.assertEqual(first["resume"]["built_chunks"], 2)
            self.assertTrue((out / "work" / "progress.json").is_file())
            second = CORE.build("KR", generated_at=self.lookup_at, inputs=self.inputs, output_dir=out)
            self.assertTrue(second["resume"]["complete"])
            self.assertEqual(second["resume"]["reused_chunks"], 2)
            self.assertEqual(second["packet"], self.packet)
            self.assertEqual(_sha(out / "packet.json"), _sha(self.out / "packet.json"))

    def test_kr_row_build_failure_does_not_abort_the_run(self):
        adapter = CORE._adapter("KR")
        contract = CORE.load_contract()
        ctx = adapter.load_context(self.inputs, generated_at=self.lookup_at, contract=contract)
        watch = next(s for s in ctx["watchlist"] if s not in ctx["bounded_rows"] and s in ctx["population_records"])
        original = adapter.KOREA_REVIEW._symbol_row

        def broken(symbol, *args, **kwargs):
            if symbol == watch:
                raise adapter.KOREA_REVIEW.KoreaSymbolMarketReviewError("SYNTHETIC_ROW_FAILURE")
            return original(symbol, *args, **kwargs)

        adapter.KOREA_REVIEW._symbol_row = broken
        try:
            row = adapter.build_symbol(ctx, watch)
        finally:
            adapter.KOREA_REVIEW._symbol_row = original
        self.assertEqual(row["evaluation"]["status"], "NOT_EVALUATED")
        self.assertIn("ROW_BUILD_FAILED:SYNTHETIC_ROW_FAILURE", row["evaluation"]["reasons"])
        self.assertEqual(row["evaluability"]["status"], "NOT_EVALUABLE")

    def test_kr_input_change_yields_a_new_generation_not_a_drift_error(self):
        adapter = CORE._adapter("KR")
        inputs = self.inputs
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "KR"
            base = CORE.build("KR", generated_at=self.lookup_at, inputs=inputs, output_dir=out)
            self.assertEqual(base["persist"]["outcome"], "populated")
            # A changed input that does not feed the bounded review: one
            # watchlist observation file removed from a copied watchlist root.
            watch_root = Path(tmp) / "krx"
            watch_root.mkdir()
            removed = None
            for path in sorted(Path(inputs["watchlist_root"]).glob("*.json")):
                record = json.loads(path.read_text(encoding="utf-8"))
                if removed is None and path.stem not in adapter.KOREA_REVIEW.load_contract()["supported_pipeline_subjects"]:
                    removed = path.stem
                    continue
                (watch_root / path.name).write_text(json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            self.assertIsNotNone(removed)
            other = dict(inputs)
            other["watchlist_root"] = watch_root
            rebuilt = CORE.build("KR", generated_at=self.lookup_at, inputs=other, output_dir=out)
            self.assertEqual(rebuilt["persist"]["outcome"], "superseded_generation")
            self.assertNotEqual(rebuilt["packet"]["generation_id"], base["packet"]["generation_id"])
            self.assertEqual(rebuilt["resume"]["superseded_generation_id"], base["packet"]["generation_id"])
            self.assertEqual(rebuilt["packet"]["summary"]["formal_candidate_count"], base["packet"]["summary"]["formal_candidate_count"])
            self.assertEqual(rebuilt["packet"]["summary"]["population_count"], base["packet"]["summary"]["population_count"])
            # the removed watchlist symbol falls back to the session response: still present, now NOT_EVALUABLE
            row = next(r for r in rebuilt["packet"]["symbols"] if r["symbol"] == removed)
            self.assertEqual(row["evaluation"]["status"], "NOT_EVALUATED")
            self.assertEqual(row["observation_status"], "EVALUABLE_SESSION_PRICE_ONLY")
            self.assertEqual(rebuilt["packet"]["summary"]["evaluated_count"], base["packet"]["summary"]["evaluated_count"] - 1)

    def test_kr_bounded_review_reads_the_declared_watchlist_root(self):
        # The bounded review must be built from inputs["watchlist_root"] (the
        # files recorded in input_refs), not the module-default live
        # data/briefing/krx: a marker that exists only in the declared copy
        # must reach the reproduced review, and a committed review that lacks
        # it must fail closed.
        adapter = CORE._adapter("KR")
        contract = CORE.load_contract()
        review = adapter.KOREA_REVIEW
        with tempfile.TemporaryDirectory() as tmp:
            watch_root = Path(tmp) / "krx"
            watch_root.mkdir()
            for path in sorted(Path(self.inputs["watchlist_root"]).glob("*.json")):
                (watch_root / path.name).write_bytes(path.read_bytes())
            subject = review.load_contract()["supported_pipeline_subjects"][0]
            marked = _json(watch_root / f"{subject}.json")
            marked["declared_root_marker"] = "only-in-declared-watchlist-root"
            (watch_root / f"{subject}.json").write_text(json.dumps(marked, ensure_ascii=False), encoding="utf-8")
            stale = dict(self.inputs, watchlist_root=watch_root)
            with self.assertRaises(CORE.PopulationSymbolObservationError) as caught:
                adapter.load_context(stale, generated_at=self.lookup_at, contract=contract)
            self.assertIn("KR_BOUNDED_REVIEW_NOT_REPRODUCIBLE", str(caught.exception))
            rebuilt = review.build_review(
                _json(self.inputs["market_signals_path"]), _json(self.inputs["stage_history_path"]), briefing_root=watch_root
            )
            review_path = Path(tmp) / "review.json"
            review_path.write_text(json.dumps(rebuilt, ensure_ascii=False), encoding="utf-8")
            ctx = adapter.load_context(dict(stale, bounded_review_path=review_path), generated_at=self.lookup_at, contract=contract)
            self.assertEqual(
                ctx["bounded"]["source"]["stage_snapshot"]["subjects"][subject]["declared_root_marker"],
                "only-in-declared-watchlist-root",
            )


class UsPopulationTests(_MarketMixin, unittest.TestCase):
    market = "US"
    compress = True

    def test_us_symbols_without_bars_record_directory_facts_and_missing_registry_facts(self):
        unpriced = [row for row in self.packet["symbols"] if row["data_observation"]["status"] == "DATA_NOT_OBSERVED" and row["evaluation"]["status"] == "NOT_EVALUATED"]
        self.assertTrue(unpriced)
        for row in unpriced[:200]:
            self.assertEqual(row["evaluability"]["status"], "NOT_EVALUABLE")
            self.assertIn(row["evaluability"]["reasons"][0], ("PRICE_SOURCE_NOT_RETAINED", "PRICE_SOURCE_NOT_CONFIGURED"))
            self.assertEqual(row["facts"]["registry_evaluation"], "NOT_RUN:REQUIRED_FACTS_MISSING")
            self.assertIn("liquidity", row["facts"]["registry_required_facts_missing"])
            self.assertIn("directory_attributes", row["data_observation"]["fields_present"])
        stages = _json(self.inputs["stage_history_path"])
        latest = stages[sorted(stages)[-1]]
        configured = {row["symbol"] for row in self.packet["symbols"] if "PRICE_SOURCE_NOT_CONFIGURED" in row["evaluability"]["reasons"]}
        self.assertTrue(configured <= set(latest))

    def test_us_priced_symbols_without_stage_are_evaluated_but_not_formal(self):
        evaluated = [row for row in self.packet["symbols"] if row["evaluation"]["status"] == "EVALUATED"]
        self.assertTrue(evaluated)
        for row in evaluated:
            self.assertEqual(row["evaluation"]["row_source"], "extracted_symbol_row")
            self.assertEqual(row["evaluation"]["row"]["price_context"]["status"], "OBSERVED")
            self.assertEqual(row["formal_candidate"]["status"], "NOT_A_FORMAL_CANDIDATE")
            self.assertIn("PIPELINE_STAGE_NOT_ASSIGNED", row["evaluation"]["reasons"])
        self.assertEqual(
            self.packet["summary"]["evaluated_without_full_inputs_count"],
            sum(1 for row in self.packet["symbols"] if row["evaluation"]["status"] == "EVALUATED_BOUNDED" and row["evaluability"]["status"] == "NOT_EVALUABLE"),
        )

    def test_us_compressed_packet_is_written_and_reread(self):
        self.assertTrue((self.out / "packet.json.gz").is_file())
        self.assertFalse((self.out / "packet.json").is_file())
        self.assertEqual(CORE.read_packet_file(self.out / "packet.json.gz"), self.packet)


class ChunkHelperTests(unittest.TestCase):
    def test_chunk_plan_and_generation_id_are_deterministic(self):
        symbols = [f"S{i:03d}" for i in range(7)]
        self.assertEqual([len(c) for c in CORE.chunk_symbols(symbols, 3)], [3, 3, 1])
        with self.assertRaises(CORE.PopulationSymbolObservationError):
            CORE.chunk_symbols(symbols, 0)
        refs = [{"path": "b", "file_sha256": "2"}, {"path": "a", "file_sha256": "1"}]
        self.assertEqual(CORE.generation_id("KR", "2026-09-10", refs), CORE.generation_id("KR", "2026-09-10", list(reversed(refs))))
        self.assertNotEqual(CORE.generation_id("KR", "2026-09-10", refs), CORE.generation_id("KR", "2026-09-11", refs))

    def test_progress_of_another_generation_is_ignored_not_reused(self):
        contract = CORE.load_contract()
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            symbols = [f"S{i:03d}" for i in range(4)]
            small = dict(contract)
            small["chunk_size"] = {"KR": 2, "US": 2}
            rows, report = CORE.run_chunks(market="KR", gen_id="g1", symbols=symbols, build_row=lambda s: {"symbol": s}, work_dir=work, contract=small)
            self.assertEqual(len(rows), 4)
            self.assertEqual(report["built_chunks"], 2)
            rows2, report2 = CORE.run_chunks(market="KR", gen_id="g2", symbols=symbols, build_row=lambda s: {"symbol": s, "v": 2}, work_dir=work, contract=small)
            self.assertEqual(report2["reused_chunks"], 0)
            self.assertEqual(report2["superseded_generation_id"], "g1")
            self.assertTrue(all(row["v"] == 2 for row in rows2))


if __name__ == "__main__":
    unittest.main()
