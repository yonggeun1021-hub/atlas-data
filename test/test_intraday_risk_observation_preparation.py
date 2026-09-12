#!/usr/bin/env python3
"""Deterministic P9-05 observation-preparation fixtures."""

import copy
import datetime as dt
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "execution" / "intraday_risk_observation_preparation.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("intraday_risk_observation_preparation", SOURCE)
CONTRACT = MODULE.load_contract()
CONSUMER = load_module(
    "intraday_risk_observation_consumer",
    ROOT / "execution" / "intraday_risk_escalation.py",
)
CONSUMER_CONTRACT = CONSUMER.load_contract()
SOURCE_SHA = "a" * 64


def definition(
    market="US",
    source_profile_id="US_COMPLETED_15M",
    session_profile_id="US_REGULAR",
    method="PRIOR_MEAN",
    count=2,
):
    source = CONTRACT["source_profiles"][source_profile_id]
    return {
        "schema_version": CONTRACT["definition_schema_version"],
        "definition_id": f"TEST.{market}.{method}",
        "market": market,
        "source_profile_id": source_profile_id,
        "session_profile_id": session_profile_id,
        "provider_id": f"TEST.{market}.FEED",
        "provider_contract_ref": source["provider_contract_ref"],
        "provider_contract_sha256": source["provider_contract_sha256"],
        "elapsed_timeframe": "15m",
        "baseline_method": method,
        "prior_comparable_session_count": count,
    }


def iso_at(local_date, local_time, offset):
    return f"{local_date}T{local_time}{offset}"


def add_minutes(value, minutes):
    parsed = dt.datetime.fromisoformat(value)
    return (parsed + dt.timedelta(minutes=minutes)).isoformat()


def bars(open_at, volumes):
    rows = []
    cursor = open_at
    for volume in volumes:
        close_at = add_minutes(cursor, 15)
        rows.append({"open_at": cursor, "close_at": close_at, "volume": str(volume)})
        cursor = close_at
    return rows


def us_current(session_profile_id="US_REGULAR", close_time="16:00:00"):
    opened = iso_at("2026-03-09", "09:30:00", "-04:00")
    return {
        "schema_version": CONTRACT["current_session_schema_version"],
        "subject_id": "US.AAPL",
        "market": "US",
        "session_id": "2026-03-09",
        "session_profile_id": session_profile_id,
        "session_open_at": opened,
        "session_close_at": iso_at("2026-03-09", close_time, "-04:00"),
        "observed_at": "2026-03-09T14:00:00Z",
        "provider_timestamp": "2026-03-09T14:00:00Z",
        "received_at": "2026-03-09T14:00:00Z",
        "reference_close": "100",
        "open_price": "99",
        "last_price": "98",
        "bid_price": "97.9",
        "ask_price": "98.1",
        "bars": bars(opened, [10, 20]),
        "source_ref": "fixture://us/current",
        "source_sha256": SOURCE_SHA,
        "available_at": "2026-03-09T14:00:00Z",
    }


def us_prior(local_date, offset, volumes):
    opened = iso_at(local_date, "09:30:00", offset)
    return {
        "schema_version": CONTRACT["prior_session_schema_version"],
        "subject_id": "US.AAPL",
        "market": "US",
        "session_id": local_date,
        "session_profile_id": "US_REGULAR",
        "session_open_at": opened,
        "session_close_at": iso_at(local_date, "16:00:00", offset),
        "bars": bars(opened, volumes),
        "source_ref": f"fixture://us/{local_date}",
        "source_sha256": SOURCE_SHA,
        "available_at": "2026-03-09T13:00:00Z",
    }


class PreparationTest(unittest.TestCase):
    def test_us_mean_prepares_consumer_observation_and_is_deterministic(self):
        inputs = [
            us_prior("2026-03-05", "-05:00", [5, 15]),
            us_prior("2026-03-06", "-05:00", [15, 25]),
        ]
        first = MODULE.build_preparation(definition(), us_current(), inputs, CONTRACT)
        second = MODULE.build_preparation(
            definition(), us_current(), list(reversed(inputs)), CONTRACT
        )
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "PREPARED")
        self.assertEqual(first["prepared_observation"]["cumulative_volume"], "30")
        self.assertEqual(first["prepared_observation"]["expected_volume_to_time"], "30")
        self.assertEqual(first["elapsed_bucket_end"], "2026-03-09T14:00:00Z")
        self.assertEqual(MODULE.validate_receipt(first, CONTRACT), first)

    def test_prepared_row_is_accepted_by_existing_p9_consumer_contract(self):
        receipt = MODULE.build_preparation(definition(), us_current(), [
            us_prior("2026-03-05", "-05:00", [5, 15]),
            us_prior("2026-03-06", "-05:00", [15, 25]),
        ], CONTRACT)
        normalized = {
            "schema_version": CONSUMER_CONTRACT["input_schema_version"],
            "contract_version": CONSUMER_CONTRACT["contract_version"],
            "batch_id": "TEST.PREPARED.BATCH",
            "observed_at": us_current()["observed_at"],
            "observations": [receipt["prepared_observation"]],
            "upstream_lineage": {
                "entry_exit_trigger_eligibility_packet_sha256": "b" * 64,
                "important_event_detection_packet_sha256": "c" * 64,
                "concentration_guard_packet_sha256": "d" * 64,
                "planned_loss_budget_packet_sha256": "e" * 64,
            },
            "authority": CONSUMER_CONTRACT["input_authority"],
        }
        batch = {
            **normalized,
            "packet_sha256": CONSUMER.payload_sha256(normalized),
        }
        checked = CONSUMER._validate_batch(batch, CONSUMER_CONTRACT)
        self.assertEqual(
            checked["normalized"]["observations"],
            [receipt["prepared_observation"]],
        )

    def test_korea_median_is_caller_selected(self):
        selected = definition(
            "KOREA", "KRX_COMPLETED_15M", "KOREA_REGULAR", "PRIOR_MEDIAN", 3
        )
        opened = "2026-08-21T09:00:00+09:00"
        current = us_current()
        current.update({
            "subject_id": "KOREA.005930", "market": "KOREA",
            "session_id": "2026-08-21", "session_profile_id": "KOREA_REGULAR",
            "session_open_at": opened, "session_close_at": "2026-08-21T15:30:00+09:00",
            "observed_at": "2026-08-21T00:30:00Z",
            "provider_timestamp": "2026-08-21T00:30:00Z",
            "received_at": "2026-08-21T00:30:00Z",
            "available_at": "2026-08-21T00:30:00Z", "bars": bars(opened, [20, 20]),
        })
        priors = []
        for day, total in (("2026-08-18", 10), ("2026-08-19", 30), ("2026-08-20", 100)):
            prior_open = f"{day}T09:00:00+09:00"
            priors.append({
                "schema_version": CONTRACT["prior_session_schema_version"],
                "subject_id": "KOREA.005930", "market": "KOREA", "session_id": day,
                "session_profile_id": "KOREA_REGULAR", "session_open_at": prior_open,
                "session_close_at": f"{day}T15:30:00+09:00",
                "bars": bars(prior_open, [total, 0]),
                "source_ref": f"fixture://kr/{day}", "source_sha256": SOURCE_SHA,
                "available_at": "2026-08-20T07:00:00Z",
            })
        receipt = MODULE.build_preparation(selected, current, priors, CONTRACT)
        self.assertEqual(receipt["prepared_observation"]["expected_volume_to_time"], "30")

    def test_upbit_utc_day_is_provider_scoped_explicit_selection(self):
        selected = definition(
            "CRYPTO", "UPBIT_COMPLETED_15M", "UPBIT_UTC_DAY", "PRIOR_MEAN", 1
        )
        opened = "2026-08-21T00:00:00+00:00"
        current = us_current()
        current.update({
            "subject_id": "CRYPTO.KRW-BTC", "market": "CRYPTO",
            "session_id": "2026-08-21", "session_profile_id": "UPBIT_UTC_DAY",
            "session_open_at": opened, "session_close_at": "2026-08-22T00:00:00+00:00",
            "observed_at": "2026-08-21T00:30:00Z",
            "provider_timestamp": "2026-08-21T00:30:00Z",
            "received_at": "2026-08-21T00:30:00Z",
            "available_at": "2026-08-21T00:30:00Z", "bars": bars(opened, [3, 7]),
        })
        prior_open = "2026-08-20T00:00:00+00:00"
        prior = {
            "schema_version": CONTRACT["prior_session_schema_version"],
            "subject_id": "CRYPTO.KRW-BTC", "market": "CRYPTO",
            "session_id": "2026-08-20", "session_profile_id": "UPBIT_UTC_DAY",
            "session_open_at": prior_open, "session_close_at": "2026-08-21T00:00:00+00:00",
            "bars": bars(prior_open, [4, 6]), "source_ref": "fixture://upbit/prior",
            "source_sha256": SOURCE_SHA, "available_at": "2026-08-21T00:00:00Z",
        }
        receipt = MODULE.build_preparation(selected, current, [prior], CONTRACT)
        self.assertEqual(receipt["selection"]["session_profile_id"], "UPBIT_UTC_DAY")
        self.assertIn(
            "NOT_UNIVERSAL_CRYPTO_FACT",
            CONTRACT["session_profiles"]["UPBIT_UTC_DAY"]["boundary_authority"],
        )

    def test_zero_selected_denominator_is_unknown_without_observation(self):
        priors = [
            us_prior("2026-03-05", "-05:00", [0, 0]),
            us_prior("2026-03-06", "-05:00", [0, 0]),
        ]
        receipt = MODULE.build_preparation(definition(), us_current(), priors, CONTRACT)
        self.assertEqual(receipt["status"], "NOT_AVAILABLE")
        self.assertEqual(receipt["baseline"]["status"], "ZERO_BASELINE_UNKNOWN")
        self.assertIsNone(receipt["prepared_observation"])

    def test_zero_denominator_applies_to_selected_method_only(self):
        priors = [
            us_prior("2026-03-04", "-05:00", [0, 0]),
            us_prior("2026-03-05", "-05:00", [0, 0]),
            us_prior("2026-03-06", "-05:00", [50, 50]),
        ]
        mean = MODULE.build_preparation(
            definition(method="PRIOR_MEAN", count=3), us_current(), priors, CONTRACT
        )
        median = MODULE.build_preparation(
            definition(method="PRIOR_MEDIAN", count=3), us_current(), priors, CONTRACT
        )
        self.assertEqual(mean["status"], "PREPARED")
        self.assertEqual(
            mean["prepared_observation"]["expected_volume_to_time"],
            "33.333333333333333333333333333333333333333333333333",
        )
        self.assertEqual(median["status"], "NOT_AVAILABLE")

    def test_missing_bucket_rejected(self):
        current = us_current()
        current["bars"][1]["open_at"] = add_minutes(current["bars"][1]["open_at"], 15)
        with self.assertRaisesRegex(Exception, "MISSING_OR_DUPLICATE_BUCKET"):
            MODULE.build_preparation(definition(), current, [
                us_prior("2026-03-05", "-05:00", [5, 15]),
                us_prior("2026-03-06", "-05:00", [15, 25]),
            ], CONTRACT)

        trailing = us_current()
        trailing["bars"].pop()
        with self.assertRaisesRegex(Exception, "CURRENT_COMPLETED_BUCKET_MISSING"):
            MODULE.build_preparation(definition(), trailing, [
                us_prior("2026-03-05", "-05:00", [5]),
                us_prior("2026-03-06", "-05:00", [15]),
            ], CONTRACT)

    def test_unfinished_bucket_rejected(self):
        current = us_current()
        current["observed_at"] = "2026-03-09T13:59:59Z"
        current["received_at"] = "2026-03-09T13:59:59Z"
        current["provider_timestamp"] = "2026-03-09T13:59:59Z"
        current["available_at"] = "2026-03-09T13:59:59Z"
        with self.assertRaisesRegex(Exception, "UNFINISHED_OR_FUTURE_BUCKET"):
            MODULE.build_preparation(definition(), current, [
                us_prior("2026-03-05", "-05:00", [5, 15]),
                us_prior("2026-03-06", "-05:00", [15, 25]),
            ], CONTRACT)

    def test_future_current_and_prior_sources_rejected(self):
        current = us_current()
        current["available_at"] = "2026-03-09T14:00:01Z"
        with self.assertRaisesRegex(Exception, "CURRENT_SOURCE_FROM_FUTURE"):
            MODULE.build_preparation(definition(), current, [], CONTRACT)
        current = us_current()
        priors = [
            us_prior("2026-03-05", "-05:00", [5, 15]),
            us_prior("2026-03-06", "-05:00", [15, 25]),
        ]
        priors[1]["available_at"] = "2026-03-09T14:00:01Z"
        with self.assertRaisesRegex(Exception, "PRIOR_SOURCE_FROM_FUTURE"):
            MODULE.build_preparation(definition(), current, priors, CONTRACT)

    def test_early_close_profile_mismatch_rejected(self):
        current = us_current(close_time="13:00:00")
        with self.assertRaisesRegex(Exception, "SESSION_BOUNDARY_MISMATCH"):
            MODULE.build_preparation(definition(), current, [], CONTRACT)

    def test_dst_offset_mismatch_rejected_and_transition_fixture_passes(self):
        priors = [
            us_prior("2026-03-05", "-05:00", [5, 15]),
            us_prior("2026-03-06", "-05:00", [15, 25]),
        ]
        MODULE.build_preparation(definition(), us_current(), priors, CONTRACT)
        bad = us_current()
        bad["session_open_at"] = "2026-03-09T09:30:00-05:00"
        bad["session_close_at"] = "2026-03-09T16:00:00-05:00"
        bad["bars"] = bars(bad["session_open_at"], [10, 20])
        with self.assertRaisesRegex(Exception, "SESSION_BOUNDARY_MISMATCH"):
            MODULE.build_preparation(definition(), bad, priors, CONTRACT)

    def test_explicit_selection_has_no_fallback(self):
        for field in ("elapsed_timeframe", "baseline_method", "prior_comparable_session_count"):
            selected = definition()
            selected.pop(field)
            with self.assertRaisesRegex(Exception, "DEFINITION_FIELDS_MISMATCH"):
                MODULE.build_preparation(selected, us_current(), [], CONTRACT)
        selected = definition(count=0)
        with self.assertRaisesRegex(Exception, "PRIOR_SESSION_COUNT_INVALID"):
            MODULE.build_preparation(selected, us_current(), [], CONTRACT)

    def test_contract_duplicate_key_fails_closed(self):
        source = MODULE.CONTRACT_PATH.read_text(encoding="utf-8")
        duplicate = source.replace("{", '{\n  "schema_version": 1,', 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate.json"
            path.write_text(duplicate, encoding="utf-8")
            with self.assertRaisesRegex(Exception, "JSON_DUPLICATE_KEY:schema_version"):
                MODULE.load_contract(path)

    def test_drawdown_wire_formula_unchanged_and_not_peak_drawdown(self):
        semantics = CONTRACT["metric_semantics"]["DRAWDOWN_FRACTION"]
        self.assertEqual(semantics["display_name"], "Reference-close decline fraction")
        self.assertEqual(
            semantics["formula"],
            "max(0,(reference_close-last_price)/reference_close)",
        )
        self.assertFalse(semantics["true_peak_drawdown"])

    def test_receipt_tamper_rejected(self):
        receipt = MODULE.build_preparation(definition(), us_current(), [
            us_prior("2026-03-05", "-05:00", [5, 15]),
            us_prior("2026-03-06", "-05:00", [15, 25]),
        ], CONTRACT)
        tampered = copy.deepcopy(receipt)
        tampered["baseline"]["expected_volume_to_time"] = "999"
        with self.assertRaisesRegex(Exception, "RECEIPT_BASELINE_INVALID"):
            MODULE.validate_receipt(tampered, CONTRACT)


if __name__ == "__main__":
    unittest.main()
