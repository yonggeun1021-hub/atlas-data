import copy
import datetime as dt
import json
from pathlib import Path
import unittest

from regime import paper_regime_runtime_adoption as subject


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_RAW = (ROOT / "config" / "paper_regime_runtime_adoption_v1.json").read_bytes()
SCHEMA = json.loads(SCHEMA_RAW)
SCHEMA_SHA256 = "3d821dac459a40fce1eac6dce2803fb8671c52a99bf22d63aa61014704c4614a"
HEX_A = "a" * 64
HEX_B = "b" * 64
HEAD = "1" * 40


def raw(value):
    return subject.canonical_bytes(value)


def envelope(value, identity=None):
    exact = raw(value)
    return {
        "raw": exact,
        "expected_sha256": subject.sha256(exact),
        "expected_identity": identity or {},
    }


def event_identity(value, extra=None):
    identity = {
        "source_id": value["source_id"],
        "workflow.path": value["workflow"]["path"],
        "workflow.github_head_sha": value["workflow"]["github_head_sha"],
        "workflow.run_id": value["workflow"]["run_id"],
        "workflow.run_attempt": value["workflow"]["run_attempt"],
    }
    identity.update(extra or {})
    return envelope(value, identity)


def source(observation_date="2026-09-09", *, price_date=None,
           available_at="2026-09-09T08:40:00Z", identity=HEX_A):
    value = {
        "observation_date": observation_date,
        "available_at": available_at,
        "observation_identity_sha256": identity,
    }
    if price_date is not None:
        value["price_date"] = price_date
    value["payload_sha256"] = subject.sha256(raw(value))
    return value


def calendar(market="KR", status="OPEN_REGULAR", latest="2026-09-09",
             session_date=None):
    value = {
        "market": market,
        "status": status,
        "latest_completed_session": latest,
        "adapter_provenance": "externally-qualified-not-authenticated-by-helper",
    }
    if status == "CLOSED":
        value["session_date"] = session_date
    return envelope(value)


def event(source_value=None, *, source_id="KR_FIVE_SIGNALS", status="completed",
          conclusion="success", expected_at="2026-09-09T09:10:00Z",
          observed_at="2026-09-09T09:12:00Z", completed_at="2026-09-09T09:11:00Z",
          evidence_type="GITHUB_WORKFLOW_RUN_COMPLETED_EVENT", publication="published",
          run_id=101, workflow_hash=HEX_B, path="data/source.json"):
    output = {
        "path": None,
        "file_sha256": None,
        "payload_sha256": None,
        "observation_identity_sha256": None,
        "observation_date": None,
        "price_date": None,
        "available_at": None,
        "publication_result": None,
    }
    if source_value is not None:
        source_raw = raw(source_value)
        output.update({
            "path": path,
            "file_sha256": subject.sha256(source_raw),
            "payload_sha256": source_value["payload_sha256"],
            "observation_identity_sha256": source_value["observation_identity_sha256"],
            "observation_date": source_value["observation_date"],
            "price_date": source_value.get("price_date"),
            "available_at": source_value["available_at"],
            "publication_result": publication,
        })
    expected_kst = None
    if expected_at is not None:
        parsed_due = dt.datetime.fromisoformat(expected_at.replace("Z", "+00:00"))
        expected_kst = parsed_due.astimezone(dt.timezone(dt.timedelta(hours=9))).isoformat()
    value = {
        "schema_version": "paper_regime_source_event_receipt/1",
        "evidence_type": evidence_type,
        "observed_at": observed_at,
        "source_id": source_id,
        "workflow": {
            "name": "Upstream producer",
            "path": ".github/workflows/upstream.yml",
            "file_sha256": workflow_hash,
            "github_head_sha": HEAD,
            "run_id": run_id,
            "run_attempt": 1,
            "event_name": "schedule",
            "event_schedule": "10 9 * * 1-5",
        },
        "event": {
            "slot_id": "primary",
            "expected_at_utc": expected_at,
            "expected_at_kst": expected_kst,
            "scheduled_event_date_kst": expected_kst[:10] if expected_kst else None,
        },
        "terminal": {
            "status": status,
            "conclusion": conclusion if status == "completed" else None,
            "completed_at_utc": completed_at if status == "completed" else None,
            "producer_step_outcome": None,
            "validation_step_outcome": None,
        },
        "source_output": output,
        "authority": {
            "operations_telemetry_only": True,
            "decision_authorized": False,
            "order_authorized": False,
            "capital_authorized": False,
            "trading_authorized": False,
        },
    }
    return event_identity(value)


def base(market="KR", evaluation_at="2026-09-09T18:30:00+09:00"):
    return {
        "market": market,
        "evaluation_at": evaluation_at,
        "event_source_id": f"{market}_FIVE_SIGNALS",
        "official_session": calendar(market),
        "events": [],
        "prior_as_of_date": "2026-09-08",
    }


def successful(market="KR", frequency="KR_FIVE_SIGNALS", observation="2026-09-09",
               *, decision=None, price_date=None, publication="published"):
    request = base(market)
    if market == "CRYPTO":
        request["official_session"] = None
        request["event_source_id"] = "CRYPTO_BTC_AND_BREADTH"
        request["expected_observation_date"] = observation
    value = source(observation, price_date=price_date)
    receipt = event(value, source_id=request["event_source_id"], publication=publication)
    request.update({
        "events": [receipt],
        "source_components": [{
            "source_id": request["event_source_id"],
            "frequency_rule": frequency,
            "path": "data/source.json",
            "artifact": envelope(value),
        }],
        "judgement": envelope({
            "candidate_regime": "NEUTRAL",
            "score": 0,
            "decision_date": decision or observation,
            "price_date": price_date,
            "axes": ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"],
            "caveats": [],
            "required_assessments_complete": True,
        }),
        "prior_observation_identity_sha256": HEX_B,
    })
    return request


def evaluate(request):
    return subject.evaluate_paper_regime_eligibility(
        SCHEMA, request, schema_raw=SCHEMA_RAW,
        expected_schema_sha256=SCHEMA_SHA256,
    )


class PaperRegimeRuntimeAdoptionTests(unittest.TestCase):
    def test_01_us_weekly_liquidity_current_fetch(self):
        request = successful("US", "US_ETF_DAILY", "2026-09-08")
        request["official_session"] = calendar("US", latest="2026-09-08")
        values = [
            ("US_ETF_DAILY", "ETF_DAILY", "2026-09-08"),
            ("US_VIXCLS", "VIXCLS", "2026-09-07"),
            ("US_WRESBAL", "WRESBAL", "2026-09-02"),
            ("US_TOTBKCR", "TOTBKCR", "2026-08-26"),
        ]
        components = []
        # One successful upstream publication causally binds a complete source
        # file.  Each logical component intentionally retains its own date.
        combined = source("2026-09-08")
        combined["components"] = {
            source_id: {"observation_date": observation_date}
            for _, source_id, observation_date in values
        }
        unsigned = copy.deepcopy(combined)
        unsigned.pop("payload_sha256")
        combined["payload_sha256"] = subject.sha256(raw(unsigned))
        request["events"] = [event(combined, source_id="US_FIVE_SIGNALS")]
        for rule, source_id, observation_date in values:
            components.append({
                "source_id": source_id,
                "frequency_rule": rule,
                "path": "data/source.json",
                "source_pointer": f"components.{source_id}",
                "artifact": envelope(combined),
            })
        request["source_components"] = components
        result = evaluate(request)
        self.assertEqual(result["component_eligibility"], {
            "ETF_DAILY": "CURRENT",
            "VIXCLS": "CURRENT_AS_FETCHED_NOT_PIT",
            "WRESBAL": "CURRENT_AS_FETCHED_NOT_PIT",
            "TOTBKCR": "CURRENT_AS_FETCHED_NOT_PIT",
        })
        self.assertEqual(result["market_eligibility"], "CURRENT_AS_FETCHED_NOT_PIT")
        self.assertTrue(result["classification_may_be_displayed"])
        self.assertEqual(result["source_facts"]["WRESBAL"]["observation_date"],
                         "2026-09-02")

    def test_02_kr_after_close_before_publication(self):
        request = base(evaluation_at="2026-09-09T17:00:00+09:00")
        request["first_due_at"] = "2026-09-09T18:10:00+09:00"
        result = evaluate(request)
        self.assertEqual(result["market_eligibility"], "AWAITING_SCHEDULED_PUBLICATION")
        self.assertEqual(result["displayed_as_of_date"], "2026-09-08")
        self.assertEqual(result["hysteresis"]["increment"], 0)

    def test_03_kr_due_without_run_receipt_is_unconfirmed_not_failed(self):
        request = base(evaluation_at="2026-09-09T18:10:00+09:00")
        request["first_due_at"] = "2026-09-09T18:10:00+09:00"
        result = evaluate(request)
        self.assertEqual(result["market_eligibility"], "DUE_COLLECTION_UNCONFIRMED")
        self.assertNotIn("FAILED", result["market_eligibility"])

    def test_04_kr_running_or_queued_uses_actual_readback(self):
        request = base(evaluation_at="2026-09-09T18:12:00+09:00")
        request["events"] = [event(
            status="in_progress", conclusion=None, completed_at=None,
            evidence_type="GITHUB_ACTIONS_REST_RUN_READBACK",
        )]
        result = evaluate(request)
        self.assertEqual(result["market_eligibility"], "COLLECTION_RUNNING_OR_QUEUED")
        self.assertFalse(result["current_eligible"])

    def test_05_terminal_failure_not_hidden_by_future_recovery(self):
        request = base(evaluation_at="2026-09-09T18:20:00+09:00")
        request["events"] = [event(
            conclusion="failure", completed_at="2026-09-09T09:15:00Z",
            observed_at="2026-09-09T09:16:00Z",
        )]
        request["scheduled_slots"] = [{
            "slot_id": "recovery",
            "expected_at": "2026-09-09T18:25:00+09:00",
            "status": "NOT_DUE",
        }]
        result = evaluate(request)
        self.assertEqual(result["market_eligibility"], "COLLECTION_FAILED")
        self.assertEqual(result["hysteresis"]["increment"], 0)
        self.assertEqual(result["event_facts"]["future_scheduled_slots"][0]["status"],
                         "NOT_DUE")

    def test_06_successful_unchanged_event_is_confirmed_no_op(self):
        request = successful(publication="skipped_existing")
        request["prior_observation_identity_sha256"] = HEX_A
        result = evaluate(request)
        self.assertEqual(result["market_eligibility"], "CURRENT")
        self.assertEqual(result["hysteresis"], {
            "increment": 0, "new_market_observation": False,
        })
        self.assertFalse(result["authority"]["order_authorized"])

    def test_07_daily_source_not_advanced(self):
        request = successful(observation="2026-09-08")
        request["official_session"] = calendar("KR", latest="2026-09-09")
        result = evaluate(request)
        self.assertEqual(result["market_eligibility"], "SOURCE_NOT_ADVANCED_EXPECTED_SESSION")
        self.assertFalse(result["current_eligible"])

    def test_08_holiday_unknown_calendar_and_crypto_missing_day(self):
        holiday = base(evaluation_at="2026-09-10T18:30:00+09:00")
        holiday["expected_session_date"] = "2026-09-10"
        holiday["official_session"] = calendar(
            "KR", "CLOSED", "2026-09-09", "2026-09-10"
        )
        closed = evaluate(holiday)
        self.assertEqual(closed["market_eligibility"], "OFFICIAL_HOLIDAY_CARRY")
        self.assertEqual(closed["displayed_as_of_date"], "2026-09-08")

        unknown = copy.deepcopy(holiday)
        unknown["official_session"] = calendar("KR", "UNKNOWN", None)
        self.assertEqual(evaluate(unknown)["market_eligibility"], "SESSION_CALENDAR_UNKNOWN")

        crypto = base("CRYPTO", "2026-09-10T18:30:00+09:00")
        crypto["official_session"] = None
        crypto["event_source_id"] = "CRYPTO_BTC_AND_BREADTH"
        crypto["expected_observation_date"] = "2026-09-09"
        crypto_value = source("2026-09-08")
        crypto["events"] = [event(crypto_value, source_id=crypto["event_source_id"])]
        crypto["source_components"] = [{
            "source_id": crypto["event_source_id"],
            "frequency_rule": "CRYPTO_BTC_AND_BREADTH",
            "path": "data/source.json", "artifact": envelope(crypto_value),
        }]
        crypto["judgement"] = envelope({
            "candidate_regime": "UNKNOWN",
            "decision_date": "2026-09-09",
            "caveats": SCHEMA["comparability_and_caveats"]["required_crypto_caveats"],
        })
        self.assertEqual(evaluate(crypto)["market_eligibility"], "SOURCE_INVALID")

    def test_09_latest_failure_overrides_older_ready(self):
        request = base("US", "2026-09-10T06:45:00+09:00")
        request["official_session"] = calendar("US", latest="2026-09-09")
        request["events"] = [event(
            source_id="US_FIVE_SIGNALS", conclusion="failure",
            expected_at="2026-09-09T21:35:00Z",
            completed_at="2026-09-09T21:40:00Z", observed_at="2026-09-09T21:41:00Z",
        )]
        result = evaluate(request)
        self.assertEqual(result["market_eligibility"], "COLLECTION_FAILED")
        self.assertEqual(result["displayed_as_of_date"], "2026-09-08")

    def test_10_cross_market_dates_never_use_crypto_price_date(self):
        rows = [
            {"market": "US", "decision_date": "2026-09-08", "price_date": "2026-09-08",
             "market_eligibility": "CURRENT", "candidate_regime": "NEUTRAL",
             "required_assessments_complete": True},
            {"market": "KR", "decision_date": "2026-09-09", "price_date": "2026-09-09",
             "market_eligibility": "CURRENT", "candidate_regime": "NEUTRAL",
             "required_assessments_complete": True},
            {"market": "CRYPTO", "decision_date": "2026-09-09", "price_date": "2026-09-08",
             "market_eligibility": "CURRENT", "candidate_regime": "NEUTRAL",
             "required_assessments_complete": False},
        ]
        result = subject.compare_market_rows(rows)
        self.assertEqual(result["same_date_groups"]["2026-09-09"], ["CRYPTO", "KR"])
        self.assertNotIn("US", result["same_date_groups"]["2026-09-09"])
        self.assertFalse(result["price_date_substitution_used"])
        self.assertEqual(result["three_market_comparison_status"], "PARTIAL_OR_NON_COMPARABLE")

    def test_11_tamper_future_identity_and_parameter_faults_fail_closed(self):
        mutations = []

        future = successful()
        future_receipt = json.loads(future["events"][0]["raw"])
        future_receipt["terminal"]["completed_at_utc"] = "2026-09-09T10:00:00Z"
        future_receipt["observed_at"] = "2026-09-09T10:01:00Z"
        future["events"] = [event_identity(future_receipt)]
        mutations.append(future)

        workflow_tamper = successful()
        workflow_value = json.loads(workflow_tamper["events"][0]["raw"])
        workflow_tamper["events"] = [event_identity(
            workflow_value, {"workflow.file_sha256": "c" * 64}
        )]
        mutations.append(workflow_tamper)

        missing_head = successful()
        receipt = json.loads(missing_head["events"][0]["raw"])
        receipt["workflow"]["github_head_sha"] = None
        missing_head["events"] = [event_identity(receipt)]
        mutations.append(missing_head)

        source_tamper = successful()
        source_receipt = json.loads(source_tamper["events"][0]["raw"])
        source_receipt["source_output"]["file_sha256"] = "d" * 64
        source_tamper["events"] = [event_identity(source_receipt)]
        mutations.append(source_tamper)

        for dotted, wrong in (
            ("workflow.run_id", 999),
            ("workflow.github_head_sha", "2" * 40),
            ("workflow.path", ".github/workflows/wrong.yml"),
        ):
            wrong_identity = successful()
            identity_value = json.loads(wrong_identity["events"][0]["raw"])
            wrong_identity["events"] = [event_identity(identity_value, {dotted: wrong})]
            mutations.append(wrong_identity)

        parameter = successful()
        parameter["normalization_binding"] = {
            "expected_version": "adopted/v1", "actual_version": "other/v1",
        }
        mutations.append(parameter)

        missing_axis = successful()
        judgement = json.loads(missing_axis["judgement"]["raw"])
        judgement["axes"] = judgement["axes"][:-1]
        missing_axis["judgement"] = envelope(judgement)
        mutations.append(missing_axis)

        future_observation = successful()
        future_source = source("2026-09-10")
        future_observation["events"] = [event(
            future_source, source_id=future_observation["event_source_id"]
        )]
        future_observation["source_components"][0]["artifact"] = envelope(future_source)
        mutations.append(future_observation)

        for request in mutations:
            with self.subTest(reason=request):
                result = evaluate(request)
                self.assertEqual(result["market_eligibility"], "SOURCE_INVALID")
                self.assertFalse(result["authority"]["capital_authorized"])

        self_declared = successful()
        receipt = json.loads(self_declared["events"][0]["raw"])
        receipt["verified"] = True
        receipt["evidence_type"] = "SOURCE_WORKFLOW_SELF_RECORDER"
        self_declared["events"] = [event_identity(receipt)]
        result = evaluate(self_declared)
        self.assertEqual(result["market_eligibility"], "SOURCE_INVALID")
        self.assertFalse(result["evidence_boundary"]["external_authenticity_verified_by_helper"])

    def test_12_legacy_bytes_rederived_and_crypto_caveats_preserved(self):
        request = successful(
            "CRYPTO", "CRYPTO_BTC_AND_BREADTH", "2026-09-09",
            decision="2026-09-09", price_date="2026-09-08",
        )
        caveats = list(SCHEMA["comparability_and_caveats"]["required_crypto_caveats"])
        judgement = json.loads(request["judgement"]["raw"])
        judgement["caveats"] = caveats
        judgement["required_assessments_complete"] = False
        request["judgement"] = envelope(judgement)
        legacy = {"payload_sha256": "74b2db4ba1a7f626ccbb3480e13b36afb6644f4b787da7d95e0f986e7b124519"}
        request["legacy_artifacts"] = [envelope(legacy)]
        result = evaluate(request)
        self.assertEqual(result["caveats"], caveats)
        self.assertEqual(result["decision_date"], "2026-09-09")
        self.assertEqual(result["price_date"], "2026-09-08")
        self.assertTrue(result["legacy"]["rederivation_unchanged"])
        self.assertFalse(result["legacy"]["hash_rewritten"])
        self.assertFalse(result["comparison"]["complete"])
        self.assertIn("REQUIRED_ASSESSMENT_UNASSESSED", result["reasons"])
        self.assertFalse(result["authority"]["runtime_production_regime_authorized"])

    def test_nullable_terminal_facts_remain_unconfirmed(self):
        request = successful()
        receipt = json.loads(request["events"][0]["raw"])
        receipt["terminal"]["completed_at_utc"] = None
        receipt["event"]["expected_at_utc"] = None
        receipt["event"]["expected_at_kst"] = None
        receipt["event"]["slot_id"] = None
        request["events"] = [event_identity(receipt)]
        result = evaluate(request)
        self.assertEqual(result["market_eligibility"], "DUE_COLLECTION_UNCONFIRMED")
        self.assertIn("SUCCESS_DUE_SLOT_UNCONFIRMED", result["reasons"])
        self.assertIsNone(result["event_facts"]["completed_at_utc"])

        no_causal_output = successful()
        receipt = json.loads(no_causal_output["events"][0]["raw"])
        receipt["source_output"]["file_sha256"] = None
        no_causal_output["events"] = [event_identity(receipt)]
        result = evaluate(no_causal_output)
        self.assertEqual(result["market_eligibility"], "DUE_COLLECTION_UNCONFIRMED")
        self.assertIn("SUCCESS_CAUSAL_OUTPUT_IDENTITY_UNCONFIRMED", result["reasons"])

    def test_three_market_completion_requires_unique_markets_and_non_unknown(self):
        def row(market, regime="NEUTRAL"):
            return {
                "market": market,
                "decision_date": "2026-09-09",
                "market_eligibility": "CURRENT",
                "candidate_regime": regime,
                "required_assessments_complete": True,
            }

        complete = subject.compare_market_rows([
            row("US"), row("KR"), row("CRYPTO")
        ])
        self.assertTrue(complete["complete"])
        stress = subject.compare_market_rows([
            row("US"), row("KR"), row("CRYPTO", "STRESS")
        ])
        self.assertTrue(stress["complete"])
        duplicate = subject.compare_market_rows([
            row("US"), row("US"), row("US")
        ])
        self.assertFalse(duplicate["complete"])
        unknown = subject.compare_market_rows([
            row("US"), row("KR"), row("CRYPTO", "UNKNOWN")
        ])
        self.assertFalse(unknown["complete"])
        missing_assessment = [row("US"), row("KR"), row("CRYPTO")]
        missing_assessment[2].pop("required_assessments_complete")
        self.assertFalse(subject.compare_market_rows(missing_assessment)["complete"])

    def test_holiday_carry_requires_exact_closed_date_and_rejects_future_values(self):
        request = base(evaluation_at="2026-09-10T18:30:00+09:00")
        request["expected_session_date"] = "2026-09-10"
        request["official_session"] = calendar(
            "KR", "CLOSED", "2026-09-09", "2026-09-10"
        )
        request["prior_as_of_date"] = "2026-09-11"
        self.assertEqual(evaluate(request)["market_eligibility"], "SOURCE_INVALID")

        wrong_date = base(evaluation_at="2026-09-10T18:30:00+09:00")
        wrong_date["expected_session_date"] = "2026-09-10"
        wrong_date["official_session"] = calendar(
            "KR", "CLOSED", "2026-09-09", "2026-09-11"
        )
        self.assertEqual(evaluate(wrong_date)["market_eligibility"], "SOURCE_INVALID")

        future_latest = base(evaluation_at="2026-09-10T18:30:00+09:00")
        future_latest["expected_session_date"] = "2026-09-10"
        future_latest["official_session"] = calendar(
            "KR", "CLOSED", "2026-09-11", "2026-09-10"
        )
        self.assertEqual(evaluate(future_latest)["market_eligibility"], "SOURCE_INVALID")

    def test_unknown_prior_identity_never_advances_hysteresis(self):
        request = successful()
        request.pop("prior_observation_identity_sha256")
        result = evaluate(request)
        self.assertEqual(result["hysteresis"], {
            "increment": 0, "new_market_observation": False,
        })

        first = successful()
        first.pop("prior_observation_identity_sha256")
        first["prior_observation_absent_confirmed"] = True
        self.assertEqual(evaluate(first)["hysteresis"], {
            "increment": 1, "new_market_observation": True,
        })


if __name__ == "__main__":
    unittest.main()
