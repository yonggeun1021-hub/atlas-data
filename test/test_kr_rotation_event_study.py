#!/usr/bin/env python3
"""Pre-registered KR rotation event study regression (offline, synthetic data).

Covers the pinned pre-registration hash and its parameter binding, the R1-k
confirmation/release mechanics on a hand-built series, forward-excess
construction, gates and the ratified-structure verdict mapping, fail-closed
BLOCKED/INSUFFICIENT outcomes, and the aggregate-only public validator that
refuses index values, per-day sequences and raw provider content. Synthetic
records are produced by the real backfill module through an in-memory
provider. `run_all.py` executes this file directly.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import io
import json
import math
from pathlib import Path
import random
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


S = _load("kr_rotation_event_study_under_test", "rotation/kr_rotation_event_study.py")
B = _load("kr_sector_index_history_backfill_for_study_test", "collectors/kr_sector_index_history_backfill.py")

KEY = "synthetic-study-key"
TODAY = dt.date(2026, 9, 15)


class Response:
    def __init__(self, body):
        self.body, self.status = body, 200

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class WalkProvider:
    """Deterministic random-walk index levels per identity across requested dates."""

    def __init__(self, identities, dates, *, drop=None, holidays=()):
        self.identities = identities
        self.holidays = set(holidays)
        self.drop = drop or {}
        rng = random.Random(20260915)
        self.levels = {}
        for market in B.MARKETS:
            for identity in [identities[market]["benchmark"], *identities[market]["members"]]:
                level, drift = rng.uniform(800, 3000), rng.gauss(0, 0.0008)
                for bas_dd in dates:
                    level *= math.exp(drift + rng.gauss(0, 0.012))
                    self.levels[(identity, bas_dd)] = level

    def __call__(self, request, timeout=None):
        url = urlparse(request.full_url)
        bas_dd = parse_qs(url.query)["basDd"][0]
        market = "KOSPI" if url.path.endswith("kospi_dd_trd") else "KOSDAQ"
        rows = []
        if bas_dd not in self.holidays:
            for identity in [self.identities[market]["benchmark"], *self.identities[market]["members"]]:
                if bas_dd in self.drop.get(identity, ()):
                    continue
                rows.append({
                    "BAS_DD": bas_dd, "IDX_NM": identity.split("::", 1)[1],
                    "CLSPRC_IDX": f"{self.levels[(identity, bas_dd)]:,.2f}",
                    "ACC_TRDVAL": f"{random.Random(identity + bas_dd).randint(10**9, 10**11):,}",
                })
        return Response(json.dumps({"OutBlock_1": rows}, ensure_ascii=False).encode("utf-8"))


def build_inputs(tmp: Path, start="2025-06-02", end="2026-06-30", **provider_kwargs):
    contract = B.load_contract()
    identities = B.ratified_identities()
    plan = B.build_plan(start, end, contract=contract, today_kst=TODAY)
    provider = WalkProvider(identities, [r["bas_dd"] for r in plan["dates"]], **provider_kwargs)
    records = tmp / "records"
    receipt = B.run_backfill(KEY, plan, records, contract=contract, identities=identities,
                             opener=provider, sleep=lambda s: None, monotonic=lambda: 0.0)
    receipt_path = tmp / "public" / "BACKFILL_RECEIPT.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_bytes(B.canonical_bytes(receipt))
    return receipt, receipt_path, records, provider


class PreregistrationTest(unittest.TestCase):
    def test_hash_is_pinned_and_parameters_bind(self):
        value = S.verify_preregistration()
        self.assertEqual(S.file_sha256(S.PREREGISTRATION_PATH), S.PREREGISTRATION_SHA256)
        self.assertEqual(value["definitions"]["main_horizon_sessions"], 10)
        self.assertEqual([c["id"] for c in value["candidates"]], list(S.KINDS))
        self.assertIn("NO_KRX_SECTOR_INDEX_HISTORY_FETCHED", value["data_state_at_writing"])
        self.assertTrue(all(flag is False for flag in value["authority"].values()))

    def test_tampered_preregistration_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prereg.json"
            path.write_bytes(S.PREREGISTRATION_PATH.read_bytes().replace(b'"main_horizon_sessions": 10', b'"main_horizon_sessions": 20'))
            with self.assertRaisesRegex(S.StudyError, "PREREGISTRATION_HASH_MISMATCH"):
                S.verify_preregistration(path)


class EngineMechanicsTest(unittest.TestCase):
    def scope(self):
        members = [f"KOSPI::{c}" for c in "BCDEFGHZ"]
        return {"benchmark": "KOSPI::BENCH", "members": members}

    def data(self, scope, T=60):
        closes = {i: [100.0] * T for i in [scope["benchmark"], *scope["members"]]}
        z = closes["KOSPI::Z"]
        for t in range(T):
            z[t] = 100.0 if t < 30 else (110.0 if t in (30, 31) else 90.0)
        turnover = {i: [5.0] * T for i in scope["members"]}
        return {"dates": [f"2026{t:04d}" for t in range(T)], "closes": closes, "turnover": turnover}

    def test_confirmation_and_release_on_hand_built_series(self):
        scope = self.scope()
        run = S.run_scope("KOSPI", scope, self.data(scope))
        z = lambda name: [ev["t"] for ev in run["events"][name] if ev["e"] == "KOSPI::Z"]
        self.assertEqual(z("R1-k1_ENTER"), [30])
        self.assertEqual(z("R1-k2_ENTER"), [31])
        self.assertEqual(z("R1-k3_ENTER"), [])
        self.assertEqual(z("R1-k1_EXIT"), [32])
        self.assertEqual(z("R1-k2_EXIT"), [32])
        self.assertEqual(z("R2_ENTER"), [31])
        # D (tie-ranked third while all others are flat) is displaced by Z at
        # t=30,31 and re-confirms TOP at t=33 without a release afterwards.
        self.assertEqual([ev["t"] for ev in run["events"]["R1-k2_ENTER"] if ev["e"] == "KOSPI::D"], [33])
        self.assertEqual(run["whipsaw"]["2"], {"enter_n": 2, "exit_within_5_n": 1})
        self.assertEqual(run["held_k2"], [1])
        # 1-session basis: Z is TOP only at t=30 (flat at t=31 ties last), so the
        # comparison rule never confirms it.
        self.assertEqual(z("L1_R1-k2_ENTER"), [])

    def test_forward_excess_is_relative_to_scope_equal_weight(self):
        scope = self.scope()
        run = S.run_scope("KOSPI", scope, self.data(scope))
        for t in (25, 29, 40):
            for h in S.HORIZONS:
                values = [run["fx"](e, t, h) for e in scope["members"]]
                if None not in values:
                    self.assertAlmostEqual(sum(values), 0.0, places=12)
        # executed at close t+1: Z's forward excess from t=29 starts at 110
        expected = math.log(90 / 110) - (math.log(90 / 110) / 8)
        self.assertAlmostEqual(run["fx"]("KOSPI::Z", 29, 5), expected, places=12)
        self.assertIsNone(run["fx"]("KOSPI::Z", 55, 5))


class GateTest(unittest.TestCase):
    def summary(self, kind, n=100, nno=40, mean=0.01, trimmed=0.009, recent=0.002, recent_n=10, hit=0.60, base=0.50, scopes=(0.01, 0.01), sectors=None):
        sectors = sectors if sectors is not None else {"a": [0.01] * 3, "b": [0.02] * 3, "c": [-0.01] * 3}
        return {
            "kind": kind, "n": n, "n_nonoverlap": nno, "hit_rate": hit, "baseline_hit_rate": base,
            "per_scope": {"KOSPI": {"n": 50, "mean": scopes[0]}, "KOSDAQ": {"n": 50, "mean": scopes[1]}},
            "_raw": {"mean": mean, "trimmed": trimmed, "recent": recent, "recent_n": recent_n, "per_sector": sectors},
        }

    def test_enter_gates(self):
        self.assertEqual(S.gate(self.summary("enter")), ("PASS", []))
        self.assertEqual(S.gate(self.summary("enter", n=79))[0], "INSUFFICIENT")
        self.assertEqual(S.gate(self.summary("enter", nno=29))[0], "INSUFFICIENT")
        self.assertEqual(S.gate(self.summary("enter", mean=0.006)), ("FAIL", ["stress_cost"]))
        self.assertEqual(S.gate(self.summary("enter", hit=0.54)), ("FAIL", ["hit_rate"]))
        self.assertEqual(S.gate(self.summary("enter", trimmed=-0.001)), ("FAIL", ["without_best"]))
        self.assertEqual(S.gate(self.summary("enter", recent=-0.001)), ("FAIL", ["recent"]))
        self.assertEqual(S.gate(self.summary("enter", recent_n=0, recent=None))[1], ["recent"])
        self.assertEqual(S.gate(self.summary("enter", scopes=(0.01, -0.001))), ("FAIL", ["repeatability"]))
        split = {"a": [0.01] * 3, "b": [-0.02] * 3}
        self.assertEqual(S.gate(self.summary("enter", sectors=split)), ("FAIL", ["repeatability"]))

    def test_exit_gates_and_structure_verdict(self):
        base = dict(mean=-0.01, trimmed=-0.008, recent=-0.002)
        self.assertEqual(S.gate(self.summary("exit", **base)), ("JUSTIFIED", []))
        self.assertEqual(S.gate(self.summary("exit", n=29, **base))[0], "INSUFFICIENT")
        self.assertEqual(S.gate(self.summary("exit", mean=-0.01, trimmed=0.001, recent=-0.002)), ("NOT_JUSTIFIED", ["without_worst"]))
        self.assertEqual(S.structure_verdict({"R1-k2_ENTER": "PASS", "R1-k2_EXIT": "JUSTIFIED"}), "SUPPORTED")
        self.assertEqual(S.structure_verdict({"R1-k2_ENTER": "PASS", "R1-k2_EXIT": "NOT_JUSTIFIED"}), "ENTRY_ONLY_SUPPORTED")
        self.assertEqual(S.structure_verdict({"R1-k2_ENTER": "FAIL", "R1-k2_EXIT": "JUSTIFIED"}), "RELEASE_ONLY_SUPPORTED")
        self.assertEqual(S.structure_verdict({"R1-k2_ENTER": "INSUFFICIENT", "R1-k2_EXIT": "NOT_JUSTIFIED"}), "INSUFFICIENT")
        self.assertEqual(S.structure_verdict({"R1-k2_ENTER": "FAIL", "R1-k2_EXIT": "NOT_JUSTIFIED"}), "NOT_SUPPORTED")


class StudyEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.receipt, cls.receipt_path, cls.records, cls.provider = build_inputs(cls.base)
        cls.doc = S.build_study(cls.receipt_path, cls.records)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_complete_study_on_synthetic_history(self):
        doc = self.doc
        self.assertEqual(self.receipt["status"], "COMPLETE")
        self.assertEqual(doc["status"], "COMPLETE", doc["reason"])
        self.assertEqual(len(doc["candidates"]), len(S.KINDS) * len(S.HORIZONS))
        self.assertEqual(doc["coverage"]["KOSPI"]["window_session_count"], self.receipt["sessions"]["session_confirmed_count"])
        self.assertGreaterEqual(doc["coverage"]["KOSDAQ"]["window_session_count"], S.MIN_WINDOW)
        gates = doc["verdict_summary"]["gates"]
        self.assertEqual(set(gates), set(S.KINDS))
        self.assertTrue(set(gates.values()) <= {"PASS", "FAIL", "INSUFFICIENT", "JUSTIFIED", "NOT_JUSTIFIED"})
        self.assertIn(doc["verdict_summary"]["ratified_structure_verdict"],
                      {"SUPPORTED", "ENTRY_ONLY_SUPPORTED", "RELEASE_ONLY_SUPPORTED", "INSUFFICIENT", "NOT_SUPPORTED"})
        main = [c for c in doc["candidates"] if c["horizon"] == S.MAIN and c["n"] > 0]
        self.assertTrue(all("gate" in c and "per_sector" in c for c in main))
        self.assertEqual(doc["inputs"]["records_payload_sha256"], self.receipt["records_payload_sha256"])
        self.assertEqual(doc["preregistration"]["sha256"], S.PREREGISTRATION_SHA256)
        self.assertTrue(all(v is False for k, v in doc["authority"].items() if k.endswith("_authorized")))

    def test_public_outputs_carry_no_index_values(self):
        S.validate_public_artifact(self.doc)
        S.validate_public_artifact(self.receipt)
        text = json.dumps(self.doc, ensure_ascii=False) + json.dumps(self.receipt, ensure_ascii=False)
        sample = list(self.provider.levels.items())[::97]
        for (_, _), level in sample:
            self.assertNotIn(f"{level:,.2f}", text)
            self.assertNotIn(f"{level:.2f}", text)
        self.assertNotIn(KEY, text)

    def test_study_is_deterministic(self):
        again = S.build_study(self.receipt_path, self.records)
        self.assertEqual(S.canonical_bytes(again), S.canonical_bytes(self.doc))

    def test_tampered_record_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt, receipt_path, records, _ = build_inputs(Path(tmp), start="2025-06-02", end="2025-06-13")
            path = records / "KOSPI" / "20250603.json"
            record = json.loads(path.read_text())
            record["series"]["KOSPI::건설"]["close"] = "1.00"
            path.write_bytes(B.canonical_bytes(record))
            doc = S.build_study(receipt_path, records)
            self.assertEqual((doc["status"], doc["reason"]), ("BLOCKED", "RECORDS_PAYLOAD_HASH_MISMATCH"))
            S.validate_public_artifact(doc)


class StudyBlockedTest(unittest.TestCase):
    def test_missing_receipt_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = S.build_study(Path(tmp) / "none.json", Path(tmp) / "records")
        self.assertEqual((doc["status"], doc["reason"]), ("BLOCKED", "BACKFILL_RECEIPT_MISSING"))
        self.assertEqual(doc["candidates"], [])
        S.validate_public_artifact(doc)

    def test_incomplete_backfill_blocks_without_statistics(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt, receipt_path, records, _ = build_inputs(Path(tmp), start="2025-06-02", end="2025-06-13")
            receipt["status"] = "STOPPED"
            receipt_path.write_bytes(B.canonical_bytes(receipt))
            doc = S.build_study(receipt_path, records)
        self.assertEqual((doc["status"], doc["reason"]), ("BLOCKED", "BACKFILL_NOT_COMPLETE:STOPPED"))
        self.assertIsNone(doc["verdict_summary"])

    def test_identity_gap_shortens_window_to_insufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt, receipt_path, records, _ = build_inputs(
                Path(tmp), start="2025-06-02", end="2026-06-30", drop={"KOSDAQ::제약": {"20260102"}})
            doc = S.build_study(receipt_path, records)
        self.assertEqual(doc["status"], "INSUFFICIENT_EXACT_IDENTITY_COVERAGE")
        self.assertEqual(doc["coverage"]["KOSDAQ"]["window_first"], "2026-01-05")
        self.assertFalse(doc["coverage"]["KOSDAQ"]["qualifies"])
        self.assertTrue(doc["coverage"]["KOSPI"]["qualifies"])
        self.assertEqual(doc["candidates"], [])
        S.validate_public_artifact(doc)


class PublicValidatorTest(unittest.TestCase):
    def doc(self):
        return S._base_document("BLOCKED", "X")

    def test_rejects_series_like_content(self):
        cases = {
            "PUBLIC_FORBIDDEN_KEY": lambda d: d.update(descriptive={"KOSPI::건설": {"close": 1}}),
            "PUBLIC_NUMERIC_SEQUENCE": lambda d: d.update(descriptive={"x": [1.0, 2.0, 3.0, 4.0, 5.0]}),
            "PUBLIC_DATE_KEYED_MAP": lambda d: d.update(descriptive={"2026-09-10": 0.1}),
            "PUBLIC_FORMATTED_NUMBER": lambda d: d.update(reason="6,909.91"),
            "PUBLIC_STUDY_KEYS_INVALID": lambda d: d.update(series=[]),
            "PUBLIC_CANDIDATE_KEYS_INVALID": lambda d: d.update(candidates=[{"event": "R1-k2_ENTER", "daily": []}]),
        }
        for code, mutate in cases.items():
            with self.subTest(code=code):
                doc = self.doc()
                mutate(doc)
                with self.assertRaisesRegex(S.StudyError, code):
                    S.validate_public_artifact(doc)
        with self.assertRaisesRegex(S.StudyError, "PUBLIC_ARTIFACT_SCHEMA_UNKNOWN"):
            S.validate_public_artifact({"schema_version": "anything"})

    def test_public_dir_rejects_non_json_and_inside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "KR_ROTATION_EVENT_STUDY.json").write_bytes(S.canonical_bytes(self.doc()))
            self.assertEqual(S.validate_public_dir(Path(tmp)), ["KR_ROTATION_EVENT_STUDY.json"])
            Path(tmp, "records.csv").write_text("a,b\n")
            with self.assertRaisesRegex(S.StudyError, "PUBLIC_DIR_NON_JSON_FILE"):
                S.validate_public_dir(Path(tmp))
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(S.StudyError, "OUTPUT_INSIDE_REPOSITORY_FORBIDDEN"):
                S.main(["--receipt", "x.json", "--records-dir", "/tmp/none", "--out", str(ROOT / "out.json")])
        self.assertFalse((ROOT / "out.json").exists())


if __name__ == "__main__":
    unittest.main()
