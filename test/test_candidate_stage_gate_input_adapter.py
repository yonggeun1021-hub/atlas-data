#!/usr/bin/env python3
"""STAGE3-CANDIDATE-GATE-INPUT-ADAPTER-001 regression.

Builds fully self-consistent Korea/US market-native review packets through
the real, unmodified ``decision.korea_symbol_market_review`` and
``decision.us_symbol_market_review`` builders (fixture inputs only -- this
checkout's sparse ``data/`` corpus does not carry their real upstream
sources), then exercises the adapter against those packets. The adapter
itself is never given a shortcut around either module's own
``validate_output()``.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "discovery" / "candidate_stage_gate_input_adapter.py"
SPEC = importlib.util.spec_from_file_location("candidate_stage_gate_input_adapter", SOURCE)
ADAPTER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(ADAPTER)

from decision import korea_symbol_market_review as korea_review  # noqa: E402
from decision import us_symbol_market_review as us_review  # noqa: E402
from discovery import candidate_evidence_lifecycle_receipt as receipt  # noqa: E402

if str(ROOT / "test") not in sys.path:
    sys.path.insert(0, str(ROOT / "test"))
import rolling_pointer_snapshot as SNAPSHOT  # noqa: E402


EVALUATION_AT = "2026-09-13T06:00:00Z"
KOREA_NAMES = {
    "012450": "Hanwha Aerospace",
    "298040": "Hyosung Heavy Industries",
    "329180": "HD Hyundai Heavy Industries",
}
KOREA_SYMBOLS = tuple(KOREA_NAMES)
US_SYMBOLS = ("TSM", "SNDK")
ALL_BOUNDED_SYMBOLS = set(KOREA_SYMBOLS) | set(US_SYMBOLS)


# ---------------------------------------------------------------------------
# Korea fixture: a fully self-consistent market packet, stage snapshot, and
# per-symbol briefing row, built only so the real build_review()/
# validate_output() pair accepts them -- no field here is read by the
# adapter directly except through that reused module.
# ---------------------------------------------------------------------------

def _kr_market_packet(*, available_at="2026-09-12T20:00:00Z", as_of_date="2026-09-12"):
    payload = {
        "schema_version": "korea_market_signals_observation/1",
        "contract_version": "korea_market_signals/1",
        "status": "OBSERVED_UNCLASSIFIED",
        "market": "KOREA",
        "available_at": available_at,
        "as_of_date": as_of_date,
        "coverage": {"ratio": "5/5"},
        "axes": {
            "TREND": {
                "status": "OBSERVED",
                "measurement": {
                    "benchmarks": {
                        "KOSPI": {"one_session_return_pct": "0.5000"},
                        "KOSDAQ": {"one_session_return_pct": "0.3000"},
                    }
                },
            },
            "BREADTH": {
                "status": "OBSERVED",
                "measurement": {"combined": {"advancing_count": 500, "declining_count": 300}},
            },
            "RISK_VOL": {
                "status": "OBSERVED",
                "measurement": {"combined_mean_absolute_stock_move_pct": "1.2000"},
            },
            "LIQUIDITY": {
                "status": "OBSERVED",
                "measurement": {"combined": {"trading_value_change_pct": "2.0000"}},
            },
            "LEADERSHIP": {
                "status": "OBSERVED",
                "measurement": {
                    "largest_relative_returns": [
                        {"sector": "SHIPBUILDING", "relative_return_pct": "3.0"}
                    ]
                },
            },
        },
        "authority": {
            "observation_only": True,
            "market_regime_authorized": False,
            "entry_eligibility_authorized": False,
            "order_authorized": False,
        },
    }
    payload["payload_sha256"] = korea_review.payload_sha256(payload)
    return payload


def _kr_stages(stage_label: str = "Candidate") -> dict:
    return {
        "2026-09-12": {
            symbol: {"name": name, "stage": stage_label, "coverage": True, "collected": False}
            for symbol, name in KOREA_NAMES.items()
        }
    }


def _kr_briefing_row(symbol: str, stage_label: str) -> dict:
    return {
        "symbol": symbol,
        "name": KOREA_NAMES[symbol],
        "atlas_stage": stage_label,
        "status": "ok",
        "latest_confirmed_day": "2026-09-12",
        "latest_confirmed_row": {
            "confirmed": True,
            "close": 100000,
            "change_pct": "1.0000",
            "volume": 1000000,
            "net_value": {"외국인합계": 1000, "기관합계": 500, "개인": -1500},
        },
        "confirmed_metrics": {"sma20": 95000},
        "source": {"source_sha256": "b" * 64},
    }


def _kr_review_packet(tmp_dir: Path, *, stage_label: str = "Candidate") -> dict:
    briefing_dir = tmp_dir / "kr_briefing"
    briefing_dir.mkdir(parents=True, exist_ok=True)
    for symbol in KOREA_SYMBOLS:
        (briefing_dir / f"{symbol}.json").write_text(
            json.dumps(_kr_briefing_row(symbol, stage_label)), encoding="utf-8"
        )
    packet = korea_review.build_review(
        _kr_market_packet(), _kr_stages(stage_label), briefing_root=briefing_dir
    )
    korea_review.validate_output(packet)
    return packet


# ---------------------------------------------------------------------------
# US fixture.
# ---------------------------------------------------------------------------

def _us_daily_bars(symbol: str, count: int = 65, start_price: float = 100.0) -> list[dict]:
    rows = []
    price = start_price
    start = dt.date(2026, 1, 1)
    for offset in range(count):
        price += 0.5
        rows.append(
            {
                "symbol": symbol,
                "opened_at": (start + dt.timedelta(days=offset)).isoformat() + "T00:00:00Z",
                "close": round(price, 2),
            }
        )
    return rows


def _us_market_packet(
    *,
    observed_at: str = "2026-09-12T20:00:00Z",
    price_symbols: tuple[str, ...] = US_SYMBOLS,
    breadth_status: str = "OBSERVED",
    leadership_status: str = "OBSERVED",
) -> dict:
    daily_bars = []
    for symbol in price_symbols:
        daily_bars.extend(_us_daily_bars(symbol))
    payload = {
        "schema_version": "free_market_data_capture/5",
        "contract_version": "free_market_data/3",
        "observed_at_utc": observed_at,
        "alpaca": {"status": "READY", "source_scope": "TEST_FIXTURE", "daily_bars": daily_bars},
        "us_market_reference": {
            "schema_version": "us_market_reference/v2",
            "status": "READY",
            "as_of_session_date": "2026-09-12",
            "trend_etfs": [
                {"symbol": "SPY", "one_session_return_pct": "0.1"},
                {"symbol": "QQQ", "one_session_return_pct": "0.2"},
                {"symbol": "IWM", "one_session_return_pct": "0.3"},
            ],
            "proxy_axes": {
                "BREADTH": {
                    "status": breadth_status,
                    "measurement": {"as_of_session_date": "2026-09-12", "advancing": 1, "declining": 1},
                },
                "LEADERSHIP": {
                    "status": leadership_status,
                    "measurement": {
                        "as_of_session_date": "2026-09-12",
                        "ordered_groups": [
                            {"symbol": "SMH", "relative_return_pct": "1.0"},
                            {"symbol": "XLK", "relative_return_pct": "0.5"},
                        ],
                    },
                },
            },
        },
        "fred": {"status": "READY", "value": "15.0", "series_id": "VIXCLS", "observation_date": "2026-09-12"},
        "fred_liquidity": {
            "status": "READY",
            "captured_at_utc": observed_at,
            "series": [{"series_id": "WALCL", "value": 1}, {"series_id": "RRPONTSYD", "value": 2}],
        },
        "authority": {
            "evidence_capture_only": True,
            "market_regime_authorized": False,
            "entry_eligibility_authorized": False,
            "order_authorized": False,
        },
    }
    payload["packet_sha256"] = us_review.payload_sha256(payload)
    return payload


def _us_stages() -> dict:
    return {
        "2026-09-12": {
            "TSM": {"name": "Taiwan Semiconductor", "stage": "Ready", "coverage": True, "collected": False},
            "SNDK": {"name": "SanDisk", "stage": "Discovery", "coverage": True, "collected": False},
        }
    }


def _us_review_packet(*, price_symbols=US_SYMBOLS, breadth_status="OBSERVED", leadership_status="OBSERVED") -> dict:
    packet = us_review.build_review(
        _us_market_packet(
            price_symbols=price_symbols, breadth_status=breadth_status, leadership_status=leadership_status
        ),
        _us_stages(),
    )
    us_review.validate_output(packet)
    return packet


def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


class FullCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gate_input_adapter_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.korea_path = self.tmp_path / "latest_korea_symbol_market_review.json"
        self.us_path = self.tmp_path / "latest_us_symbol_market_review.json"
        _write(self.korea_path, _kr_review_packet(self.tmp_path))
        _write(self.us_path, _us_review_packet())

    def _build(self, **overrides):
        kwargs = {
            "evaluation_at_utc": EVALUATION_AT,
            "korea_review_path": self.korea_path,
            "us_review_path": self.us_path,
        }
        kwargs.update(overrides)
        return ADAPTER.build_gate_inputs(**kwargs)

    def test_exactly_the_five_bounded_symbols_are_represented(self):
        result = self._build()
        self.assertEqual(set(result), ALL_BOUNDED_SYMBOLS)

    def test_every_record_carries_all_nine_required_gates_and_contract_identity(self):
        result = self._build()
        for symbol, market in (*((s, "KOREA") for s in KOREA_SYMBOLS), *((s, "US") for s in US_SYMBOLS)):
            with self.subTest(symbol=symbol):
                row = result[symbol]
                self.assertEqual(row["contract_version"], "candidate_stage_gate_input/1")
                self.assertEqual(row["symbol"], symbol)
                self.assertEqual(row["market"], market)
                self.assertEqual(row["evaluation_at_utc"], EVALUATION_AT)
                self.assertEqual(row["reviewer_identity"], ADAPTER.ADAPTER_IDENTITY)
                self.assertEqual(set(row["gates"]), set(receipt.REQUIRED_STAGE_GATES))

    def test_only_coverage_gate_is_ever_pass_or_fail_the_rest_stay_missing(self):
        result = self._build()
        for symbol, row in result.items():
            with self.subTest(symbol=symbol):
                for gate_name, gate in row["gates"].items():
                    if gate_name == ADAPTER.COVERAGE_GATE:
                        self.assertIn(gate["status"], ("PASS", "FAIL"))
                    else:
                        self.assertEqual(gate["status"], "MISSING")
                        self.assertEqual(gate["evidence_refs"], [])

    def test_korea_symbols_pass_coverage_with_bound_evidence(self):
        result = self._build()
        for symbol in KOREA_SYMBOLS:
            gate = result[symbol]["gates"][ADAPTER.COVERAGE_GATE]
            self.assertEqual(gate["status"], "PASS")
            self.assertEqual(result[symbol]["market_native_evidence_contract"], "korea_symbol_market_review/1")
            ref = gate["evidence_refs"][0]
            self.assertRegex(ref["sha256"], r"^[0-9a-f]{64}$")
            self.assertLessEqual(
                dt.datetime.fromisoformat(ref["available_at_utc"].replace("Z", "+00:00")),
                dt.datetime.fromisoformat(EVALUATION_AT.replace("Z", "+00:00")),
            )

    def test_us_symbol_with_observed_price_and_full_axes_passes_coverage(self):
        result = self._build()
        gate = result["TSM"]["gates"][ADAPTER.COVERAGE_GATE]
        self.assertEqual(gate["status"], "PASS")
        self.assertEqual(result["TSM"]["market_native_evidence_contract"], "us_symbol_market_review/1")

    def test_us_symbol_with_unavailable_price_fails_coverage_not_missing(self):
        _write(self.us_path, _us_review_packet(price_symbols=("TSM",)))
        result = self._build()
        self.assertEqual(result["SNDK"]["gates"][ADAPTER.COVERAGE_GATE]["status"], "FAIL")
        self.assertEqual(result["TSM"]["gates"][ADAPTER.COVERAGE_GATE]["status"], "PASS")

    def test_us_incomplete_five_axis_fails_coverage_for_every_us_symbol(self):
        _write(self.us_path, _us_review_packet(breadth_status="MISSING"))
        result = self._build()
        for symbol in US_SYMBOLS:
            self.assertEqual(result[symbol]["gates"][ADAPTER.COVERAGE_GATE]["status"], "FAIL")

    def test_review_or_expiry_time_defaults_to_evaluation_time(self):
        result = self._build()
        for row in result.values():
            self.assertEqual(row["review_or_expiry_time_utc"], EVALUATION_AT)

    def test_receipt_still_holds_at_canonical_population_membership_even_with_coverage_connected(self):
        gate_inputs = self._build()
        gate_input = gate_inputs["298040"]
        system_gate_inputs = {"298040": gate_input}
        # The receipt is built at a fixed past instant, so its rolling-pointer
        # sources (stage history, Dynamic Clock validity/identity) are pinned
        # to the frozen snapshot; the live tree advances with every collect.
        with tempfile.TemporaryDirectory() as snapshot_tmp, SNAPSHOT.pinned_candidate_receipt_sources(
            receipt, SNAPSHOT.materialize(Path(snapshot_tmp))
        ):
            gen_receipt = receipt.build_receipt(
                generated_at_utc="2026-09-13T06:05:00Z",
                system_gate_inputs=system_gate_inputs,
            )
            # lookup_symbol() only re-derives with no system_gate_inputs, so it
            # cannot be used here; validate directly with the same gate inputs
            # the receipt was actually built with, then find the row.
            receipt.validate_receipt(gen_receipt, system_gate_inputs=system_gate_inputs)
        row = next(r for r in gen_receipt["lifecycle_records"] if r["symbol"] == "298040")
        evaluation = row["system_stage_evaluation"]
        self.assertFalse(evaluation["system_candidate_eligible"])
        self.assertEqual(evaluation["first_blocker"], "canonical_population_membership:MISSING")
        # The coverage gate this adapter connected still shows PASS inside
        # the receipt's own gate_results -- it is only the seven other
        # unconnected gates (population membership is checked first) that
        # hold the record back.
        coverage_result = next(
            g for g in evaluation["gate_results"] if g["gate"] == ADAPTER.COVERAGE_GATE
        )
        self.assertEqual(coverage_result["status"], "PASS")


class AbsentSourceTests(unittest.TestCase):
    def test_missing_review_files_yield_empty_mapping_not_a_crash(self):
        with tempfile.TemporaryDirectory(prefix="gate_input_adapter_absent_") as tmp:
            tmp_path = Path(tmp)
            result = ADAPTER.build_gate_inputs(
                evaluation_at_utc=EVALUATION_AT,
                korea_review_path=tmp_path / "no_korea.json",
                us_review_path=tmp_path / "no_us.json",
            )
        self.assertEqual(result, {})

    def test_one_market_connected_one_absent_contributes_only_the_connected_market(self):
        with tempfile.TemporaryDirectory(prefix="gate_input_adapter_partial_") as tmp:
            tmp_path = Path(tmp)
            korea_path = tmp_path / "latest_korea_symbol_market_review.json"
            _write(korea_path, _kr_review_packet(tmp_path))
            result = ADAPTER.build_gate_inputs(
                evaluation_at_utc=EVALUATION_AT,
                korea_review_path=korea_path,
                us_review_path=tmp_path / "no_us.json",
            )
        self.assertEqual(set(result), set(KOREA_SYMBOLS))


class NegativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gate_input_adapter_neg_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.korea_path = self.tmp_path / "latest_korea_symbol_market_review.json"
        self.us_path = self.tmp_path / "latest_us_symbol_market_review.json"

    def test_future_evidence_relative_to_evaluation_time_is_rejected(self):
        _write(self.korea_path, _kr_review_packet(self.tmp_path))
        _write(self.us_path, _us_review_packet())
        with self.assertRaisesRegex(
            ADAPTER.CandidateStageGateInputAdapterError, "NOT_POINT_IN_TIME"
        ):
            ADAPTER.build_gate_inputs(
                evaluation_at_utc="2026-09-12T00:00:00Z",  # before either packet's generated_at
                korea_review_path=self.korea_path,
                us_review_path=self.us_path,
            )

    def test_hash_tampered_review_packet_is_rejected_by_the_reused_validator(self):
        packet = _kr_review_packet(self.tmp_path)
        tampered = copy.deepcopy(packet)
        tampered["source"]["stage_snapshot"]["subjects"]["298040"]["latest_confirmed_row"]["close"] = 1
        unsigned = {key: value for key, value in tampered.items() if key != "packet_sha256"}
        tampered["packet_sha256"] = korea_review.payload_sha256(unsigned)
        _write(self.korea_path, tampered)
        _write(self.us_path, _us_review_packet())
        with self.assertRaisesRegex(
            korea_review.KoreaSymbolMarketReviewError, "OUTPUT_DERIVATION_MISMATCH"
        ):
            ADAPTER.build_gate_inputs(
                evaluation_at_utc=EVALUATION_AT,
                korea_review_path=self.korea_path,
                us_review_path=self.us_path,
            )

    def test_opened_authority_on_a_review_packet_is_rejected_by_the_reused_validator(self):
        packet = _us_review_packet()
        tampered = copy.deepcopy(packet)
        tampered["authority"]["order_authorized"] = True
        unsigned = {key: value for key, value in tampered.items() if key != "packet_sha256"}
        tampered["packet_sha256"] = us_review.payload_sha256(unsigned)
        _write(self.us_path, tampered)
        with self.assertRaisesRegex(us_review.UsSymbolMarketReviewError, "AUTHORITY_INVALID"):
            ADAPTER.build_gate_inputs(
                evaluation_at_utc=EVALUATION_AT,
                korea_review_path=self.tmp_path / "no_korea.json",
                us_review_path=self.us_path,
            )

    def test_unsupported_symbol_lookup_is_rejected(self):
        _write(self.korea_path, _kr_review_packet(self.tmp_path))
        _write(self.us_path, _us_review_packet())
        gate_inputs = ADAPTER.build_gate_inputs(
            evaluation_at_utc=EVALUATION_AT,
            korea_review_path=self.korea_path,
            us_review_path=self.us_path,
        )
        for unsupported in ("AAPL", "005930"):
            with self.subTest(symbol=unsupported):
                with self.assertRaisesRegex(
                    ADAPTER.CandidateStageGateInputAdapterError, "UNSUPPORTED_SYMBOL"
                ):
                    ADAPTER.gate_input_for_symbol(gate_inputs, unsupported)

    def test_manual_stage_is_never_a_parameter_and_never_changes_the_gate_result(self):
        self.assertNotIn("stage", inspect.signature(ADAPTER.build_gate_inputs).parameters)
        self.assertNotIn("watchlist", inspect.signature(ADAPTER.build_gate_inputs).parameters)
        _write(self.korea_path, _kr_review_packet(self.tmp_path, stage_label="Discovery"))
        _write(self.us_path, _us_review_packet())
        first = ADAPTER.build_gate_inputs(
            evaluation_at_utc=EVALUATION_AT,
            korea_review_path=self.korea_path,
            us_review_path=self.us_path,
        )
        _write(self.korea_path, _kr_review_packet(self.tmp_path, stage_label="Buy"))
        second = ADAPTER.build_gate_inputs(
            evaluation_at_utc=EVALUATION_AT,
            korea_review_path=self.korea_path,
            us_review_path=self.us_path,
        )
        for symbol in KOREA_SYMBOLS:
            # The evidence sha256 differs because the underlying briefing
            # fixture's atlas_stage text differs and is folded into the
            # review packet's hash -- that is expected. What must not
            # change is the decision itself: which gate statuses this
            # adapter derives from price/axis coverage, independent of any
            # stage tag.
            first_statuses = {name: gate["status"] for name, gate in first[symbol]["gates"].items()}
            second_statuses = {name: gate["status"] for name, gate in second[symbol]["gates"].items()}
            self.assertEqual(first_statuses, second_statuses)


if __name__ == "__main__":
    unittest.main()
