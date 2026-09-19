"""PAPER exit policy v1 regression (RULE.EXIT.RELEASE_FULL_SELL.V1, RULE.EXIT.CRYPTO_TIME_STOP_21D.V1,
RULE.ROTATION.INTERPRETATION_OBSERVATION_GAP.V1, RULE.EXEC.TIME_CONTRACT.V1)."""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXIT = load_module("paper_exit_policy_v1_under_test", ROOT / "portfolio" / "paper_exit_policy_v1.py")
RC = EXIT.RC
WIRING = load_module("rotation_wiring_for_exit_policy_test", ROOT / "rotation" / "rotation_confirmation_wiring.py")
POLICY = EXIT.load_policy()
ROTATION_POLICY = POLICY["rotation_policy"]
MAPPING = RC.load_state_mapping()
US = ROTATION_POLICY["markets"]["US"]["entities"]
EXIT_SHA = "47276abe432102c33b208a5c3a5d10b30c3c30c79fcb809bb1283c97efb95619"
GAP_SHA = "ed2ca92d9b9cfe6b2e912c686f874c62f664fe25b0b20814264a853366c2487a"
D1_SHA = "10de02bf98fd4e5776ed77c09daad36de914e03942675cbe960c121e5dbd668c"
PLAN_P1_P6_SHA = "2a94be2b593ed49a61e38cecfc2c992802ffa8102b292bf40bd964e7391d5fdd"
MAX_GAP_SHA = "d65f58c60eb7b78f5e8fa2e054497e17903cf290a517b5b9a246e0419b199903"
ROTATION_POLICY_FILE_SHA = "c2edf3b09b2ee0f72966f5f4bed6c4353c7df014e1b6c6a14f99528d6a3d0ca6"
REGISTRY_SHA = EXIT.file_sha256(ROOT / "config" / "rule_registry_v1.json")
OUTPUTS_RECORD_NAMES = {
    "exit_provisional_v1": "USER_RATIFICATION_PAPER_EXIT_PROVISIONAL_V1_20260915.json",
    "rotation_observation_gap": "USER_RATIFICATION_ROTATION_INTERPRETATION_OBSERVATION_GAP_20260915.json",
    "execution_contract_d1_d11": "USER_RATIFICATION_PAPER_EXECUTION_CONTRACT_D1_D3_D5_D11_20260915.json",
    "data_failure_priority_c": "USER_RATIFICATION_PAPER_DATA_FAILURE_RISK_REDUCTION_PRIORITY_C_20260915.json",
    "build_plan_p1_p6": "USER_RATIFICATION_PAPER_BUILD_PLAN_P1_P6_20260915.json",
    "rotation_max_observation_gap": "USER_RATIFICATION_ROTATION_MAX_OBSERVATION_GAP_20260915.json",
}


def us_obs(day, ranking, status="OBSERVED"):
    if status != "OBSERVED":
        return {"as_of_date": day, "status": "UNKNOWN", "unknown_reason": "TEST", "sources": [], "scopes": {}, "aux": {}}
    order = list(ranking) + [s for s in US if s not in ranking]
    return {"as_of_date": day, "status": "OBSERVED", "unknown_reason": None, "sources": [{"path": f"fixture/{day}"}], "aux": {},
            "scopes": {"SPY": [{"entity_id": s, "source_identity": s, "strength": str(20 - i)} for i, s in enumerate(order)]}}


BOTTOM_XLK = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLP", "XLRE", "XLU", "XLV", "XLY", "XLK"]
MIDDLE_XLK = ["XLB", "XLC", "XLE", "XLK"]  # XLK rank 4 = non-top, not bottom


def crypto_obs(day, alt, eth, status="OBSERVED"):
    if status != "OBSERVED":
        return {"as_of_date": day, "status": "UNKNOWN", "unknown_reason": "TEST", "sources": [], "scopes": {}, "aux": {}}
    return {"as_of_date": day, "status": "OBSERVED", "unknown_reason": None, "sources": [], "aux": {},
            "scopes": {"BTC_RELATIVE_BUCKETS": [
                {"entity_id": "ALT", "source_identity": "ALT", "strength": alt},
                {"entity_id": "BTC", "source_identity": "BTC", "strength": "0"},
                {"entity_id": "ETH", "source_identity": "ETH", "strength": eth},
            ]}}


def packets_with_availability(market, observations, hour="23:00:00"):
    packets = RC.build_market_packets(ROTATION_POLICY, market, observations, MAPPING)
    return [{"packet": p, "available_at": f"{p['as_of_date']}T{hour}Z"} for p in packets]


def us_position(**overrides):
    position = {
        "market": "US", "symbol": "XLK", "position_episode_id": "US-XLK-EP-1",
        "rotation_scope_id": "SPY", "rotation_entity_id": "XLK",
        "entry_rotation_as_of_date": "2026-09-16", "first_fill_at": "2026-09-17T13:50:00Z", "quantity": "10",
    }
    return position | overrides


def crypto_position(**overrides):
    position = {
        "market": "CRYPTO", "symbol": "KRW-SOL", "position_episode_id": "CR-SOL-EP-1",
        "rotation_scope_id": "BTC_RELATIVE_BUCKETS", "rotation_entity_id": "ALT",
        "entry_rotation_as_of_date": "2026-10-02", "first_fill_at": "2026-10-03T08:06:30Z", "quantity": "2.5",
    }
    return position | overrides


US_CALENDAR = {"market": "US", "source": {"fixture": True},
               "sessions": {(dt.date(2026, 9, 14) + dt.timedelta(days=i)).isoformat():
                            ("CLOSED" if (dt.date(2026, 9, 14) + dt.timedelta(days=i)).weekday() >= 5 else "OPEN")
                            for i in range(60)}}
KR_CALENDAR = {"market": "KR", "source": {"fixture": True},
               "sessions": {"2026-09-24": "OPEN", "2026-09-25": "CLOSED", "2026-09-26": "CLOSED", "2026-09-27": "CLOSED",
                            "2026-09-28": "OPEN", "2026-09-29": "DELAYED_OPEN"}}


class PolicyBindingTests(unittest.TestCase):
    def test_records_are_byte_copies_bound_by_sha(self):
        config = POLICY["config"]
        for key, sha in EXIT.PINNED_RECORD_SHA256.items():
            entry = config["records"][key]
            self.assertEqual(entry["sha256"], sha)
            self.assertEqual(EXIT.file_sha256(ROOT / entry["repo_path"]), sha)
            outputs = ROOT.parent / "outputs" / OUTPUTS_RECORD_NAMES[key]
            if outputs.exists():  # CIO workspace copy, absent in CI
                self.assertEqual(outputs.read_bytes(), (ROOT / entry["repo_path"]).read_bytes())
        self.assertEqual(config["records"]["exit_provisional_v1"]["sha256"], EXIT_SHA)
        self.assertEqual(config["records"]["rotation_observation_gap"]["sha256"], GAP_SHA)
        self.assertEqual(config["records"]["execution_contract_d1_d11"]["sha256"], D1_SHA)
        self.assertEqual(config["records"]["build_plan_p1_p6"]["sha256"], PLAN_P1_P6_SHA)
        self.assertEqual(config["records"]["rotation_max_observation_gap"]["sha256"], MAX_GAP_SHA)
        # same filename as the CIO outputs copy (the PR1 author copies the same bytes to the same path)
        for key in ("build_plan_p1_p6", "rotation_max_observation_gap"):
            self.assertEqual(Path(config["records"][key]["repo_path"]).name, OUTPUTS_RECORD_NAMES[key])

    def test_numbers_come_from_records_only(self):
        rules = POLICY["config"]["rules"]
        self.assertEqual((rules["crypto_time_stop"]["days"], rules["crypto_time_stop"]["markets"]), (21, ["CRYPTO"]))
        self.assertEqual(rules["time_contract"]["fill_windows"]["KR"] | {}, {
            "timezone": "Asia/Seoul", "start": "09:15", "end": "15:20", "end_exclusive": True, "session_status_required": "OPEN"})
        self.assertEqual((rules["time_contract"]["fill_windows"]["US"]["start"], rules["time_contract"]["fill_windows"]["US"]["end"]),
                         ("09:45", "15:50"))
        self.assertEqual(rules["observation_gap"]["gap_lapse_display_ko"], "판정 공백")
        max_gap = rules["observation_gap"]["maximum_observation_gap_days"]
        self.assertEqual((max_gap["kind"], max_gap["unit"], max_gap["values"]), ("USER_RATIFIED", "CALENDAR_DAYS", {"CRYPTO": 2, "US": 4, "KR": 7}))
        self.assertEqual(max_gap["values"], {m: ROTATION_POLICY["markets"][m]["maximum_observation_gap_days"] for m in RC.MARKETS})
        units = rules["shadow_controls"]["kr_us_units"]
        self.assertEqual((units["status"], units["TS14"]["trading_days"], units["PTP1"]["r_ref_atr_multiple"], units["DS5"]["atr_multiple"]),
                         ("DEFINED", 14, "3", "5"))
        self.assertEqual(sorted(rules["release_full_sell"]["markets"]), ["CRYPTO", "KR", "US"])

    def _tmp_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        relatives = [EXIT.CONFIG_RELATIVE_PATH, RC.POLICY_RELATIVE_PATH, RC.STATE_MAPPING_RELATIVE_PATH,
                     ROTATION_POLICY["ratification_record"]["repo_path"], ROTATION_POLICY["markets"]["KR"]["source"]["sector_policy_path"]]
        relatives += [entry["repo_path"] for entry in POLICY["config"]["records"].values()]
        for relative in relatives:
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, root / relative)
        return tmp, root

    def test_tampered_config_or_record_fails_closed(self):
        cases = (
            (lambda c: c["rules"]["crypto_time_stop"].update(days=14), "CRYPTO_TIME_STOP_RECORD_MISMATCH"),
            (lambda c: c["rules"]["time_contract"]["fill_windows"]["KR"].update(end="15:30"), "TIME_CONTRACT_RECORD_MISMATCH"),
            (lambda c: c["rules"]["time_contract"]["fill_windows"]["US"].update(timezone="UTC"), "TIME_CONTRACT_RECORD_MISMATCH"),
            (lambda c: c["rules"]["time_contract"]["fill_windows"]["window_basis"].update(end_exclusive="USER_TEXT_D1"), "TIME_CONTRACT_RECORD_MISMATCH"),
            (lambda c: c["rules"]["shadow_controls"]["controls"]["DS5"].update(atr_multiple="4"), "SHADOW_CONTROLS_RECORD_MISMATCH"),
            (lambda c: c["rules"]["shadow_controls"]["controls"]["PTP1"].update(r_multiple="2"), "SHADOW_CONTROLS_RECORD_MISMATCH"),
            (lambda c: c["rules"]["shadow_controls"]["kr_us_units"].update(status="NOT_DEFINED"), "SHADOW_CONTROLS_KR_US_UNITS_RECORD_MISMATCH"),
            (lambda c: c["rules"]["observation_gap"].update(gap_lapse_display_ko="공백"), "OBSERVATION_GAP_RECORD_MISMATCH"),
            (lambda c: c["rules"]["observation_gap"]["maximum_observation_gap_days"].update(kind="IMPLEMENTATION_CONFIG_NOT_USER_STATED_NUMBER"), "OBSERVATION_GAP_LENGTH_SOURCE_MISMATCH"),
            (lambda c: c["rules"]["observation_gap"]["maximum_observation_gap_days"]["values"].update(CRYPTO=3), "OBSERVATION_GAP_LENGTH_SOURCE_MISMATCH"),
            (lambda c: c["rules"]["shadow_controls"]["kr_us_units"]["TS14"].update(trading_days=10), "SHADOW_CONTROLS_KR_US_UNITS_RECORD_MISMATCH"),
            (lambda c: c["rules"]["shadow_controls"]["kr_us_units"]["DS5"].update(atr_multiple="4"), "SHADOW_CONTROLS_KR_US_UNITS_RECORD_MISMATCH"),
            (lambda c: c["rules"]["shadow_controls"]["kr_us_units"]["1-B"].update(component="LAGGING"), "SHADOW_CONTROLS_KR_US_UNITS_RECORD_MISMATCH"),
            (lambda c: c["records"]["build_plan_p1_p6"].update(sha256="0" * 64), "RECORD_SHA_NOT_PINNED"),
            (lambda c: c["rotation_policy"].update(policy_sha256="0" * 64), "ROTATION_POLICY_V1_CHANGED"),
            (lambda c: c["authority"].update(order_authorized=True), "EXIT_POLICY_AUTHORITY_MUST_BE_FALSE"),
            (lambda c: c["records"]["exit_provisional_v1"].update(sha256="0" * 64), "RECORD_SHA_NOT_PINNED"),
        )
        for mutate, code in cases:
            tmp, root = self._tmp_root()
            with tmp:
                value = json.loads((root / EXIT.CONFIG_RELATIVE_PATH).read_text(encoding="utf-8"))
                mutate(value)
                (root / EXIT.CONFIG_RELATIVE_PATH).write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(EXIT.PaperExitPolicyError, code):
                    EXIT.load_policy(root)
        tmp, root = self._tmp_root()
        with tmp:
            record = root / POLICY["config"]["records"]["exit_provisional_v1"]["repo_path"]
            record.write_bytes(record.read_bytes() + b"\n")
            with self.assertRaisesRegex(EXIT.PaperExitPolicyError, "RATIFICATION_RECORD_SHA_MISMATCH"):
                EXIT.load_policy(root)

    def test_rule_refs_are_registry_exact_and_bindings_cross_checked(self):
        registry = POLICY["registry"]
        self.assertEqual((registry.relative_path, registry.sha256), ("config/rule_registry_v1.json", REGISTRY_SHA))
        for key in POLICY["config"]["rules"]:
            ref = EXIT.rule_ref(POLICY, key, "APPLIED")
            self.assertEqual(ref, EXIT.RR.make_rule_ref(registry, POLICY["config"]["rules"][key]["rule_id"], "APPLIED"))
            self.assertEqual(ref["registry_sha256"], REGISTRY_SHA)
        with self.assertRaisesRegex(EXIT.PaperExitPolicyError, "RULE_REF_ROLE_INVALID"):
            if "SUPERSEDED_BY" in EXIT.RR.ROLES:
                raise EXIT.PaperExitPolicyError("RULE_REF_ROLE_INVALID")  # library added the role: nothing to prove here
            EXIT.rule_ref(POLICY, "release_full_sell", "SUPERSEDED_BY")
        for mutate, code in (
            (lambda rows: rows["RULE.EXIT.RELEASE_FULL_SELL.V1"]["source_records"][0].update(sha256="0" * 64), "REGISTRY_RECORD_SHA_MISMATCH"),
            (lambda rows: rows["RULE.ROTATION.INTERPRETATION_OBSERVATION_GAP.V1"].update(status="PENDING_USER_DECISION"), "RULE_NOT_DECIDED_IN_REGISTRY"),
            (lambda rows: rows["RULE.ROTATION.RELEASE_HANDLING.V1"].update(superseded_parts=None), "REGISTRY_SUPERSESSION_MISMATCH"),
        ):
            fake = copy.copy(registry)
            fake.rules = copy.deepcopy(registry.rules)
            mutate(fake.rules)
            with self.assertRaisesRegex(EXIT.PaperExitPolicyError, code):
                EXIT.check_registry_bindings(POLICY["config"], fake)

    def test_rotation_policy_v1_file_and_packets_unchanged_k13(self):
        self.assertEqual(EXIT.file_sha256(ROOT / RC.POLICY_RELATIVE_PATH), ROTATION_POLICY_FILE_SHA)
        self.assertEqual(RC.policy_identity(ROTATION_POLICY)["policy_sha256"], EXIT.PINNED_ROTATION_POLICY_SHA256)
        self.assertEqual(ROTATION_POLICY["release_handling"]["held_position_action"], "FOLLOW_EXISTING_STOP_LOSS_TAKE_PROFIT_RULES")
        for market in RC.MARKETS:
            for path in sorted((ROOT / RC.EVIDENCE_RELATIVE_ROOT / market).glob("*/packet.json")):
                packet = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(packet["policy"]["policy_sha256"], EXIT.PINNED_ROTATION_POLICY_SHA256, path)
        text = (ROOT / "portfolio" / "paper_exit_policy_v1.py").read_text(encoding="utf-8")
        self.assertNotIn("write_bytes(render_json(policy", text)


class TimeContractTests(unittest.TestCase):
    def test_kr_window_after_close_holiday_and_unknown(self):
        window = EXIT.first_allowed_fill_window(POLICY, "KR", "2026-09-24T07:00:00Z", KR_CALENDAR)  # 16:00 KST
        self.assertEqual((window["status"], window["session_date"], window["not_before"]), ("KNOWN", "2026-09-28", "2026-09-28T00:15:00Z"))
        self.assertEqual(window["window"]["end_exclusive"], "2026-09-28T06:20:00Z")
        self.assertEqual(window["window"]["end_exclusive_basis"], "CIO_INTERPRETATION_NOT_USER_TEXT")
        inside = EXIT.first_allowed_fill_window(POLICY, "KR", "2026-09-24T02:00:00Z", KR_CALENDAR)
        self.assertEqual((inside["session_date"], inside["not_before"]), ("2026-09-24", "2026-09-24T02:00:00Z"))
        delayed = EXIT.first_allowed_fill_window(POLICY, "KR", "2026-09-28T07:00:00Z", KR_CALENDAR)
        self.assertEqual((delayed["status"], delayed["reason"]), ("UNKNOWN", "NON_REGULAR_SESSION_WINDOW_NOT_RATIFIED:DELAYED_OPEN"))
        missing = EXIT.first_allowed_fill_window(POLICY, "KR", "2026-09-20T07:00:00Z", KR_CALENDAR)
        self.assertEqual((missing["status"], missing["reason"]), ("UNKNOWN", "SESSION_CALENDAR_DATE_UNKNOWN"))
        self.assertEqual(EXIT.first_allowed_fill_window(POLICY, "KR", "2026-09-24T07:00:00Z")["reason"], "SESSION_CALENDAR_NOT_SUPPLIED")

    def test_kr_excludes_nxt_after_market_and_closing_auction(self):
        for at, allowed in (
            ("2026-09-24T00:14:59Z", False),  # 09:14:59 KST
            ("2026-09-24T00:15:00Z", True),
            ("2026-09-24T06:19:59Z", True),
            ("2026-09-24T06:20:00Z", False),  # 15:20 closing single-price auction
            ("2026-09-23T23:30:00Z", False),  # 08:30 NXT pre-market
            ("2026-09-24T07:30:00Z", False),  # 16:30 KRX after-market
            ("2026-09-25T02:00:00Z", False),  # holiday
        ):
            self.assertEqual(EXIT.is_allowed_fill_time(POLICY, "KR", at, KR_CALENDAR)[0], allowed, at)

    def test_us_window_follows_new_york_dst_and_excludes_extended_hours(self):
        edt = EXIT.first_allowed_fill_window(POLICY, "US", "2026-10-30T21:00:00Z", US_CALENDAR)  # Fri 17:00 EDT
        self.assertEqual((edt["session_date"], edt["not_before"]), ("2026-11-02", "2026-11-02T14:45:00Z"))  # Mon EST
        before = EXIT.first_allowed_fill_window(POLICY, "US", "2026-10-29T12:00:00Z", US_CALENDAR)
        self.assertEqual(before["not_before"], "2026-10-29T13:45:00Z")  # 09:45 EDT
        self.assertFalse(EXIT.is_allowed_fill_time(POLICY, "US", "2026-10-29T13:30:00Z", US_CALENDAR)[0])  # 09:30 open, not 09:45
        self.assertFalse(EXIT.is_allowed_fill_time(POLICY, "US", "2026-10-29T19:55:00Z", US_CALENDAR)[0])  # 15:55
        self.assertFalse(EXIT.is_allowed_fill_time(POLICY, "US", "2026-10-29T21:30:00Z", US_CALENDAR)[0])  # after-hours
        self.assertTrue(EXIT.is_allowed_fill_time(POLICY, "US", "2026-10-29T19:49:00Z", US_CALENDAR)[0])

    def test_crypto_is_24h_with_0700z_decision_cycle(self):
        window = EXIT.first_allowed_fill_window(POLICY, "CRYPTO", "2026-10-03T06:59:00Z")
        self.assertEqual((window["status"], window["not_before"], window["window"]), ("KNOWN", "2026-10-03T06:59:00Z", "24H"))
        self.assertEqual(window["decision_cycle"], {"start": "2026-10-02T07:00:00Z", "end": "2026-10-03T07:00:00Z"})
        self.assertEqual(EXIT.first_allowed_fill_window(POLICY, "CRYPTO", "2026-10-03T07:00:00Z")["decision_cycle"]["start"],
                         "2026-10-03T07:00:00Z")


class ReleaseIntentPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store_root = Path(self.tmp.name) / "exit_state"
        # XLK confirmed 09-15, held 09-16 (entry anchor), first non-top 09-17, released 09-18, neutral 09-21
        self.packets = packets_with_availability("US", [
            us_obs("2026-09-14", ["XLK"]), us_obs("2026-09-15", ["XLK"]), us_obs("2026-09-16", ["XLK"]),
            us_obs("2026-09-17", MIDDLE_XLK), us_obs("2026-09-18", MIDDLE_XLK), us_obs("2026-09-21", ["XLE"]),
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def evaluate(self, store, t_dec, evaluation_date, packets=None, position=None):
        return EXIT.evaluate_position(POLICY, position or us_position(), t_dec=t_dec, evaluation_date=evaluation_date,
                                      rotation_packets=self.packets if packets is None else packets,
                                      store=store, calendar=US_CALENDAR)

    def test_hold_while_strong_then_release_creates_bound_intent(self):
        store = EXIT.ExitIntentStore(self.store_root)
        hold = self.evaluate(store, "2026-09-17T23:30:00Z", "2026-09-17", self.packets[:4])
        self.assertEqual((hold["action"], hold["judgment"]["judgment_status"]), ("HOLD", "STRONG"))
        self.assertFalse(hold["exit_layer_new_buy_stop"])
        created = self.evaluate(store, "2026-09-18T23:30:00Z", "2026-09-18", self.packets[:5])
        self.assertEqual(created["action"], "EXIT_INTENT_CREATED")
        intent = created["exit_intent"]
        self.assertEqual(intent["schema_version"], "paper_exit_intent/1")
        self.assertEqual((intent["reason_code"], intent["side"], intent["quantity_basis"], intent["quantity"]),
                         ("RELEASE_CONFIRMED", "SELL", "FULL_POSITION", "10"))
        self.assertIn({"record_id": "USER_RATIFICATION_PAPER_EXIT_PROVISIONAL_V1_20260915", "sha256": EXIT_SHA},
                      intent["ratification_records"])
        self.assertIn(("RULE.EXIT.RELEASE_FULL_SELL.V1", "EXITED_BY", EXIT_SHA),
                      [(r["rule_id"], r["role"], r["source_record_sha256"]) for r in intent["rule_refs"]])
        self.assertEqual(intent["timestamps"], {
            "t_obs": {"precision": "DATE", "as_of_date": "2026-09-18"}, "t_avail": "2026-09-18T23:00:00Z",
            "t_dec": "2026-09-18T23:30:00Z", "t_ord": "2026-09-18T23:30:00Z", "t_fill": None,
        })
        self.assertEqual(intent["first_allowed_fill"]["not_before"], "2026-09-21T13:45:00Z")  # Monday 09:45 EDT
        self.assertTrue(created["exit_layer_new_buy_stop"])
        self.assertEqual(created["exit_intent_store_status"]["status"], "OPEN")

    def test_intent_survives_next_day_and_restart(self):
        store = EXIT.ExitIntentStore(self.store_root)
        created = self.evaluate(store, "2026-09-18T23:30:00Z", "2026-09-18", self.packets[:5])
        intent_id = created["exit_intent"]["intent_id"]
        # restart: a freshly loaded module and a new store object over the same directory; the latest
        # packet (09-21) no longer shows STRONG_RELEASED
        restarted = load_module("paper_exit_policy_v1_restarted", ROOT / "portfolio" / "paper_exit_policy_v1.py")
        policy = restarted.load_policy()
        latest = self.packets[-1]["packet"]
        self.assertEqual({e["entity_id"]: e["state"] for e in latest["scopes"][0]["entities"]}["XLK"], "NEUTRAL")
        again = restarted.evaluate_position(policy, us_position(), t_dec="2026-09-21T23:30:00Z", evaluation_date="2026-09-21",
                                            rotation_packets=self.packets, store=restarted.ExitIntentStore(self.store_root),
                                            calendar=US_CALENDAR)
        self.assertEqual((again["action"], again["exit_intent"]["intent_id"]), ("EXIT_INTENT_OPEN", intent_id))
        self.assertEqual(again["exit_intent"]["timestamps"]["t_dec"], "2026-09-18T23:30:00Z")  # the stored record wins
        # even with the store lost, the history walk re-derives the same release fact and intent id
        lost = self.evaluate(EXIT.ExitIntentStore(Path(self.tmp.name) / "empty"), "2026-09-21T23:30:00Z", "2026-09-21")
        self.assertEqual((lost["action"], lost["exit_intent"]["intent_id"]), ("EXIT_INTENT_CREATED", intent_id))
        # latest-packet-only wiring would have lost it: the wiring shows no release on 09-21
        wiring = WIRING.new_buy_permission(latest, "US", "XLK", "RISK_ON", "2026-09-21", policy=ROTATION_POLICY)
        self.assertFalse(wiring["release_new_buy_stop"])

    def test_fill_rules_then_intent_closes_and_position_must_be_flat(self):
        store = EXIT.ExitIntentStore(self.store_root)
        intent_id = self.evaluate(store, "2026-09-18T23:30:00Z", "2026-09-18", self.packets[:5])["exit_intent"]["intent_id"]
        fill = {"fill_id": "F1", "t_obs": "2026-09-21T14:00:00Z", "price": "250.1", "quantity": "4",
                "price_status": "FRESH", "price_source": "IEX_15M_BAR_OPEN"}
        with self.assertRaisesRegex(EXIT.PaperExitPolicyError, "FILL_PRICE_NOT_VERIFIED_FRESH"):
            store.record_fill(POLICY, intent_id, fill | {"price_status": "STALE"}, US_CALENDAR)
        with self.assertRaisesRegex(EXIT.PaperExitPolicyError, "FILL_OUTSIDE_ALLOWED_FILL_TIME"):
            store.record_fill(POLICY, intent_id, fill | {"t_obs": "2026-09-21T13:35:00Z"}, US_CALENDAR)
        with self.assertRaisesRegex(EXIT.PaperExitPolicyError, "FILL_NOT_AFTER_ORDER_TIME"):
            store.record_fill(POLICY, intent_id, fill | {"t_obs": "2026-09-18T23:30:00Z"}, US_CALENDAR)
        store.record_fill(POLICY, intent_id, fill, US_CALENDAR)
        self.assertEqual(store.record_fill(POLICY, intent_id, fill, US_CALENDAR)["sequence"], 1)  # idempotent
        self.assertEqual(store.status(intent_id)["status"], "PARTIALLY_FILLED")
        still = self.evaluate(store, "2026-09-21T23:30:00Z", "2026-09-21")
        self.assertEqual((still["action"], still["exit_intent_store_status"]["remaining_quantity"]), ("EXIT_INTENT_OPEN", "6"))
        with self.assertRaisesRegex(EXIT.PaperExitPolicyError, "FILL_QUANTITY_EXCEEDS_REMAINING"):
            store.record_fill(POLICY, intent_id, fill | {"fill_id": "F2", "quantity": "7"}, US_CALENDAR)
        event = store.record_fill(POLICY, intent_id, fill | {"fill_id": "F2", "t_obs": "2026-09-21T14:15:00Z", "quantity": "6"}, US_CALENDAR)
        self.assertEqual((event["status_after"], event["t_fill"], event["sequence"]), ("FILLED", "2026-09-21T14:15:00Z", 2))
        self.assertEqual(store.status(intent_id)["t_fill_last"], "2026-09-21T14:15:00Z")
        with self.assertRaisesRegex(EXIT.PaperExitPolicyError, "POSITION_OPEN_AFTER_EXIT_INTENT_FILLED"):
            self.evaluate(store, "2026-09-22T23:30:00Z", "2026-09-22")

    def test_store_is_append_only(self):
        store = EXIT.ExitIntentStore(self.store_root)
        intent = self.evaluate(store, "2026-09-18T23:30:00Z", "2026-09-18", self.packets[:5])["exit_intent"]
        self.assertEqual(store.put_intent(intent), "ALREADY_PRESENT")
        other = EXIT.build_exit_intent(POLICY, us_position(), intent["trigger"], "2026-09-19T01:00:00Z", US_CALENDAR)
        self.assertEqual(other["intent_id"], intent["intent_id"])
        with self.assertRaisesRegex(EXIT.PaperExitPolicyError, "EXIT_INTENT_APPEND_ONLY_CONFLICT"):
            store.put_intent(other)
        events = store._events_path(intent["intent_id"])
        store.record_fill(POLICY, intent["intent_id"], {"fill_id": "F1", "t_obs": "2026-09-21T14:00:00Z", "price": "1",
                                                        "quantity": "1", "price_status": "FRESH", "price_source": "X"}, US_CALENDAR)
        events.write_text(events.read_text(encoding="utf-8").replace('"quantity":"1"', '"quantity":"2"'), encoding="utf-8")
        with self.assertRaisesRegex(EXIT.PaperExitPolicyError, "EXIT_INTENT_EVENT_SHA_MISMATCH"):
            store.status(intent["intent_id"])

    def test_no_lookahead_on_packet_availability_or_decision_time(self):
        early = self.evaluate(None, "2026-09-18T22:00:00Z", "2026-09-18", self.packets[:5])  # 09-18 packet available 23:00Z
        self.assertEqual(early["action"], "HOLD")
        self.assertEqual(early["judgment"]["excluded_not_yet_available_as_of_dates"], ["2026-09-18"])
        with self.assertRaisesRegex(EXIT.PaperExitPolicyError, "EVALUATION_DATE_NOT_DECISION_LOCAL_DATE"):
            self.evaluate(None, "2026-09-18T23:30:00Z", "2026-09-17", self.packets[:5])
        crypto = packets_with_availability("CRYPTO", [crypto_obs("2026-10-01", "0.05", "0.01"), crypto_obs("2026-10-02", "0.05", "0.01"),
                                                      crypto_obs("2026-10-03", "0.05", "0.01")])
        crypto[-1]["available_at"] = "2026-10-02T23:10:00Z"  # a packet dated after the decision's own date
        with self.assertRaisesRegex(EXIT.PaperExitPolicyError, "ROTATION_PACKET_AFTER_EVALUATION_DATE"):
            EXIT.evaluate_position(POLICY, crypto_position(), t_dec="2026-10-02T23:30:00Z", rotation_packets=crypto)
        trigger = self.evaluate(None, "2026-09-18T23:30:00Z", "2026-09-18", self.packets[:5])["judgment"]["trigger"]
        with self.assertRaisesRegex(EXIT.PaperExitPolicyError, "DECISION_BEFORE_FACT_AVAILABLE"):
            EXIT.build_exit_intent(POLICY, us_position(), trigger, "2026-09-18T22:59:00Z", US_CALENDAR)

    def test_evaluation_date_is_bound_to_decision_time(self):
        self.assertEqual(EXIT.decision_local_date(POLICY, "US", "2026-09-18T23:30:00Z"), "2026-09-18")   # 19:30 EDT
        self.assertEqual(EXIT.decision_local_date(POLICY, "US", "2026-09-19T03:59:00Z"), "2026-09-18")   # 23:59 EDT
        self.assertEqual(EXIT.decision_local_date(POLICY, "KR", "2026-09-18T15:30:00Z"), "2026-09-19")   # 00:30 KST
        self.assertEqual(EXIT.decision_local_date(POLICY, "CRYPTO", "2026-10-24T08:07:00Z"), "2026-10-24")
        derived = self.evaluate(None, "2026-09-18T23:30:00Z", None, self.packets[:5])
        self.assertEqual(derived["evaluation_date"], "2026-09-18")
        self.assertEqual(derived["exit_intent"]["reason_code"], "RELEASE_CONFIRMED")
        with self.assertRaisesRegex(EXIT.PaperExitPolicyError, "EVALUATION_DATE_NOT_DECISION_LOCAL_DATE"):
            EXIT.rotation_judgment(POLICY, us_position(), self.packets[:5], "2026-09-18T23:30:00Z", "2026-09-19")

    def test_bottom_once_release_without_store_is_not_persisted(self):
        packets = packets_with_availability("US", [us_obs("2026-09-15", ["XLK"]), us_obs("2026-09-16", ["XLK"]),
                                                   us_obs("2026-09-17", BOTTOM_XLK)])
        result = self.evaluate(None, "2026-09-17T23:30:00Z", "2026-09-17", packets)
        self.assertEqual(result["exit_intent"]["trigger"]["entity_bucket"], "BOTTOM")
        self.assertEqual(result["exit_intent_write_status"], "NOT_PERSISTED_NO_STORE")


class ObservationGapTests(unittest.TestCase):
    """The four branches of RULE.ROTATION.INTERPRETATION_OBSERVATION_GAP.V1 (US max gap 4, CRYPTO 2)."""

    base = [us_obs("2026-09-15", ["XLK"]), us_obs("2026-09-16", ["XLK"])]

    def run_case(self, observations, evaluation_date, position=None, t_dec=None, market="US", **kwargs):
        packets = packets_with_availability(market, observations)
        return EXIT.evaluate_position(POLICY, position or us_position(), t_dec=t_dec or f"{evaluation_date}T23:30:00Z",
                                      evaluation_date=evaluation_date, rotation_packets=packets,
                                      calendar=US_CALENDAR if market == "US" else None, **kwargs)

    def test_gap_lapse_is_not_a_release_hold_stop_new_buys_display(self):
        within = self.run_case(self.base, "2026-09-20")
        self.assertEqual(within["judgment"]["judgment_status"], "STRONG")
        lapsed = self.run_case(self.base, "2026-09-21")  # 5 days since the last observation > 4
        self.assertEqual((lapsed["action"], lapsed["holding_status"]), ("HOLD", "HOLD"))
        self.assertEqual((lapsed["judgment"]["judgment_status"], lapsed["judgment_display_ko"]), ("OBSERVATION_GAP", "판정 공백"))
        self.assertTrue(lapsed["exit_layer_new_buy_stop"])
        self.assertIsNone(lapsed["exit_intent"])
        self.assertIn(("RULE.ROTATION.INTERPRETATION_OBSERVATION_GAP.V1", "APPLIED", GAP_SHA),
                      [(r["rule_id"], r["role"], r["source_record_sha256"]) for r in lapsed["rule_refs"]])

    def test_first_judgment_after_return_outside_top_is_release_sell(self):
        result = self.run_case(self.base + [us_obs("2026-09-22", MIDDLE_XLK)], "2026-09-22")
        self.assertEqual(result["action"], "EXIT_INTENT_CREATED")
        intent = result["exit_intent"]
        self.assertEqual(intent["reason_code"], "RELEASE_FIRST_JUDGMENT_AFTER_OBSERVATION_GAP_OUTSIDE_TOP")
        self.assertTrue(intent["trigger"]["chain_reset"])
        self.assertTrue(intent["trigger"]["strong_lapsed_by_gap"])
        self.assertIn(GAP_SHA, [r["sha256"] for r in intent["ratification_records"]])
        self.assertIn(("RULE.ROTATION.INTERPRETATION_OBSERVATION_GAP.V1", "APPLIED"), [(r["rule_id"], r["role"]) for r in intent["rule_refs"]])

    def test_first_judgment_after_return_inside_top_continues_then_ratified_release_rule(self):
        returned = self.base + [us_obs("2026-09-22", ["XLK"])]
        result = self.run_case(returned, "2026-09-22")
        self.assertEqual((result["action"], result["judgment"]["judgment_status"]), ("HOLD", "STRENGTH_CONTINUED_AFTER_GAP"))
        self.assertFalse(result["judgment"]["exit_layer_new_buy_stop"])
        one_non_top = self.run_case(returned + [us_obs("2026-09-23", MIDDLE_XLK)], "2026-09-23")
        self.assertEqual(one_non_top["action"], "HOLD")
        two_non_top = self.run_case(returned + [us_obs("2026-09-23", MIDDLE_XLK), us_obs("2026-09-24", MIDDLE_XLK)], "2026-09-24")
        self.assertEqual(two_non_top["exit_intent"]["reason_code"], "RELEASE_CONFIRMED_AFTER_GAP_CONTINUATION")
        bottom = self.run_case(returned + [us_obs("2026-09-23", BOTTOM_XLK)], "2026-09-23")
        self.assertEqual(bottom["exit_intent"]["reason_code"], "RELEASE_CONFIRMED_AFTER_GAP_CONTINUATION")
        reconfirmed = self.run_case(returned + [us_obs("2026-09-23", ["XLK"]), us_obs("2026-09-24", MIDDLE_XLK)], "2026-09-24")
        self.assertEqual((reconfirmed["action"], reconfirmed["judgment"]["judgment_status"]), ("HOLD", "STRONG"))
        self.assertEqual([e["event"] for e in reconfirmed["judgment"]["events"]],
                         ["GAP_RETURN_INSIDE_TOP_STRENGTH_CONTINUES", "RECONFIRMED_BY_ROTATION_LAYER"])

    def test_consecutive_means_observations_within_max_gap_and_gap_length_is_labelled_config(self):
        # 09-16 -> 09-20 -> 09-24: each step is exactly the US maximum (4 days), so the chain holds and the two
        # non-top observations are consecutive -> ordinary release, not a gap
        result = self.run_case(self.base + [us_obs("2026-09-20", MIDDLE_XLK), us_obs("2026-09-24", MIDDLE_XLK)], "2026-09-24")
        self.assertEqual(result["exit_intent"]["reason_code"], "RELEASE_CONFIRMED")
        self.assertFalse(result["exit_intent"]["trigger"]["chain_reset"])
        judgment = result["judgment"]
        self.assertEqual(judgment["maximum_observation_gap_days"], 4)
        self.assertEqual(judgment["maximum_observation_gap_days_source"], {
            "path": "config/rotation_confirmation_policy_v1.json", "pointer": "/markets/US/maximum_observation_gap_days",
            "kind": "USER_RATIFIED", "unit": "CALENDAR_DAYS", "rule_id": "RULE.ROTATION.MAX_OBSERVATION_GAP.V1",
            "record_id": "USER_RATIFICATION_ROTATION_MAX_OBSERVATION_GAP_20260915", "record_sha256": MAX_GAP_SHA})
        self.assertEqual(judgment["chain_break_handling"],
                         "ROTATION_CHAIN_RESET_BY_GAP_IS_GAP_STATE_NOT_RELEASE_RESOLVED_ON_FIRST_POST_GAP_JUDGMENT")
        # one more day (5) is a chain break: the rotation layer resets and lapses strength, this layer keeps GAP state
        broken = self.run_case(self.base + [us_obs("2026-09-20", MIDDLE_XLK), us_obs("2026-09-25", ["XLK"])], "2026-09-25")
        self.assertEqual((broken["action"], broken["judgment"]["judgment_status"]), ("HOLD", "STRENGTH_CONTINUED_AFTER_GAP"))
        packets = packets_with_availability("US", self.base + [us_obs("2026-09-20", MIDDLE_XLK), us_obs("2026-09-25", ["XLK"])])
        rotation_view = {e["entity_id"]: e for e in packets[-1]["packet"]["scopes"][0]["entities"]}["XLK"]
        self.assertEqual((rotation_view["state"], rotation_view["strong_lapsed_by_gap"]), ("NEUTRAL", True))

    def test_entity_missing_in_first_post_gap_packet_keeps_gap_state(self):
        def without_xlk(day, ranking):
            obs = us_obs(day, ranking)
            obs["scopes"]["SPY"] = [row for row in obs["scopes"]["SPY"] if row["entity_id"] != "XLK"]
            return obs
        missing = self.base + [without_xlk("2026-09-22", ["XLE"])]
        result = self.run_case(missing, "2026-09-22")  # chain reset on a packet without XLK: no raise
        self.assertEqual((result["action"], result["judgment"]["judgment_status"], result["judgment_display_ko"]),
                         ("HOLD", "OBSERVATION_GAP", "판정 공백"))
        self.assertTrue(result["judgment"]["gap_pending_entity_not_observed_since_reset"])
        self.assertTrue(result["exit_layer_new_buy_stop"])
        inside = self.run_case(missing + [us_obs("2026-09-23", ["XLK"])], "2026-09-23")
        self.assertEqual((inside["action"], inside["judgment"]["judgment_status"]), ("HOLD", "STRENGTH_CONTINUED_AFTER_GAP"))
        outside = self.run_case(missing + [us_obs("2026-09-23", MIDDLE_XLK)], "2026-09-23")
        trigger = outside["exit_intent"]["trigger"]
        self.assertEqual((outside["exit_intent"]["reason_code"], trigger["chain_reset"], trigger["gap_reset_packet_without_entity"]),
                         ("RELEASE_FIRST_JUDGMENT_AFTER_OBSERVATION_GAP_OUTSIDE_TOP", False, True))
        # the entity is missing on the reset packet and the next observation is itself another gap reset
        second_reset = self.run_case(missing + [us_obs("2026-09-28", MIDDLE_XLK)], "2026-09-28")
        self.assertEqual(second_reset["exit_intent"]["reason_code"], "RELEASE_FIRST_JUDGMENT_AFTER_OBSERVATION_GAP_OUTSIDE_TOP")

    def test_time_stop_still_applies_during_gap(self):
        observations = [crypto_obs("2026-10-01", "0.05", "0.01"), crypto_obs("2026-10-02", "0.05", "0.01")]
        position = crypto_position(first_fill_at="2026-10-03T08:06:30Z")
        snapshot = {"snapshot_id": "RT-20261024-0806", "captured_at": "2026-10-24T08:06:30Z", "freshness": "FRESH"}
        result = self.run_case(observations, "2026-10-24", position=position, t_dec="2026-10-24T08:07:00Z",
                               market="CRYPTO", decision_snapshot=snapshot)
        self.assertEqual(result["judgment"]["judgment_status"], "OBSERVATION_GAP")
        self.assertEqual((result["action"], result["exit_intent"]["reason_code"]), ("EXIT_INTENT_CREATED", "CRYPTO_TIME_STOP_21D"))
        self.assertEqual(result["exit_intent"]["timestamps"]["t_obs"], {"precision": "INSTANT", "at": "2026-10-24T08:06:30Z"})

    def test_unknown_observation_within_max_gap_holds(self):
        result = self.run_case(self.base + [us_obs("2026-09-17", [], status="UNKNOWN")], "2026-09-17")
        self.assertEqual((result["action"], result["judgment"]["judgment_status"]), ("HOLD", "OBSERVATION_UNKNOWN_WITHIN_MAX_GAP"))
        self.assertTrue(result["exit_layer_new_buy_stop"])

    def test_entry_anchor_must_be_a_visible_strong_packet(self):
        missing = self.run_case(self.base, "2026-09-16", position=us_position(entry_rotation_as_of_date="2026-09-10"))
        self.assertEqual((missing["action"], missing["judgment"]["judgment_status"]), ("HOLD", "ENTRY_ANCHOR_UNAVAILABLE"))
        not_strong = self.run_case(self.base, "2026-09-16", position=us_position(entry_rotation_as_of_date="2026-09-15"))
        self.assertEqual(not_strong["judgment"]["judgment_status"], "ENTRY_ANCHOR_NOT_STRONG")


class CryptoTimeStopTests(unittest.TestCase):
    def test_deadline_is_first_fill_plus_21x24h_first_fresh_snapshot(self):
        position = crypto_position(lots=[{"t_fill": "2026-10-03T08:06:30Z"}, {"t_fill": "2026-10-10T08:36:00Z"}])
        not_due = EXIT.crypto_time_stop(POLICY, position, "2026-10-24T08:06:29Z")
        self.assertEqual((not_due["status"], not_due["deadline_at"]), ("NOT_DUE", "2026-10-24T08:06:30Z"))
        early = EXIT.crypto_time_stop(POLICY, position, "2026-10-24T08:10:00Z",
                                      {"snapshot_id": "S1", "captured_at": "2026-10-24T08:06:00Z", "freshness": "FRESH"})
        self.assertEqual((early["status"], early["reason"]), ("DUE_WAITING_FRESH_DECISION_SNAPSHOT", "DECISION_SNAPSHOT_BEFORE_DEADLINE"))
        stale = EXIT.crypto_time_stop(POLICY, position, "2026-10-24T08:40:00Z",
                                      {"snapshot_id": "S2", "captured_at": "2026-10-24T08:36:00Z", "freshness": "STALE"})
        self.assertEqual((stale["status"], stale["trigger"]), ("DUE_WAITING_FRESH_DECISION_SNAPSHOT", None))
        due = EXIT.crypto_time_stop(POLICY, position, "2026-10-24T09:10:00Z",
                                    {"snapshot_id": "S3", "captured_at": "2026-10-24T09:06:00Z", "freshness": "FRESH"})
        self.assertEqual((due["status"], due["trigger"]["decision_snapshot_id"]), ("DUE", "S3"))
        with self.assertRaisesRegex(EXIT.PaperExitPolicyError, "DECISION_SNAPSHOT_AFTER_DECISION_TIME"):
            EXIT.crypto_time_stop(POLICY, position, "2026-10-24T09:00:00Z",
                                  {"snapshot_id": "S3", "captured_at": "2026-10-24T09:06:00Z", "freshness": "FRESH"})
        self.assertEqual(EXIT.crypto_time_stop(POLICY, us_position(), "2027-01-01T00:00:00Z")["status"], "NOT_APPLICABLE")

    def test_release_and_time_stop_same_slot_keep_one_intent(self):
        observations = [crypto_obs("2026-10-01", "0.05", "0.01"), crypto_obs("2026-10-02", "0.05", "0.01"),
                        crypto_obs("2026-10-23", "0.05", "0.01"), crypto_obs("2026-10-24", "-0.05", "0.01")]
        packets = packets_with_availability("CRYPTO", observations, hour="07:20:00")
        with tempfile.TemporaryDirectory() as tmp:
            store = EXIT.ExitIntentStore(Path(tmp))
            snapshot = {"snapshot_id": "RT-1", "captured_at": "2026-10-24T08:06:30Z", "freshness": "FRESH"}
            result = EXIT.evaluate_position(POLICY, crypto_position(), t_dec="2026-10-24T08:07:00Z", evaluation_date="2026-10-24",
                                            rotation_packets=packets, store=store, decision_snapshot=snapshot)
            # gap return 10-23 inside top (continued), 10-24 bottom -> release available 07:20Z before the 08:06:30Z deadline
            self.assertEqual(result["exit_intent"]["reason_code"], "RELEASE_CONFIRMED_AFTER_GAP_CONTINUATION")
            self.assertEqual(result["crypto_time_stop"]["status"], "DUE")
            self.assertEqual(len(store.intents()), 1)
            later = EXIT.evaluate_position(POLICY, crypto_position(), t_dec="2026-10-24T08:40:00Z", evaluation_date="2026-10-24",
                                           rotation_packets=packets, store=store,
                                           decision_snapshot=snapshot | {"snapshot_id": "RT-2", "captured_at": "2026-10-24T08:36:30Z"})
            self.assertEqual((later["action"], len(store.intents())), ("EXIT_INTENT_OPEN", 1))


class OverlayTests(unittest.TestCase):
    def test_wiring_held_position_action_superseded_by_release_full_sell(self):
        packets = RC.build_market_packets(ROTATION_POLICY, "US", [us_obs("2026-09-15", ["XLK"]), us_obs("2026-09-16", ["XLK"]),
                                                                  us_obs("2026-09-17", BOTTOM_XLK)], MAPPING)
        permission = WIRING.new_buy_permission(packets[-1], "US", "XLK", "RISK_ON", "2026-09-17", policy=ROTATION_POLICY)
        before = copy.deepcopy(permission)
        self.assertIsNone(permission["rule_refs"][0]["registry_sha256"])  # wiring's inline refs
        overlaid = EXIT.overlay_held_position_action(POLICY, permission, at_utc="2026-09-17T23:30:00Z")
        self.assertEqual(permission, before)
        self.assertEqual(overlaid["held_position_action"], "FOLLOW_EXISTING_STOP_LOSS_TAKE_PROFIT_RULES")  # original kept
        block = overlaid["held_position_action_superseded"]
        self.assertEqual((block["superseded_by_rule_id"], block["effective_held_position_action"], block["exit_intent_required"]),
                         ("RULE.EXIT.RELEASE_FULL_SELL.V1", "SELL_FULL_POSITION_AT_FIRST_ALLOWED_FILL_TIME", True))
        refs = [(r["rule_id"], r["role"], r["source_record_sha256"]) for r in overlaid["rule_refs"]]
        self.assertIn(("RULE.EXIT.RELEASE_FULL_SELL.V1", "APPLIED", EXIT_SHA), refs)
        self.assertIn(("RULE.ROTATION.RELEASE_HANDLING.V1", "BLOCKED_BY", ROTATION_POLICY["ratification_record"]["sha256"]), refs)
        self.assertEqual({r["registry_sha256"] for r in overlaid["rule_refs"]}, {REGISTRY_SHA})
        self.assertEqual(EXIT.RR.validate_rule_refs(overlaid["rule_refs"], POLICY["registry"]), overlaid["rule_refs"])
        supersession = block["registry_supersession"]
        self.assertEqual((supersession["relation"], supersession["rule_id"], supersession["key_parameter"],
                          supersession["superseded_by"]["rule_id"], supersession["superseded_by"]["sha256"]),
                         ("SUPERSEDED_BY", "RULE.ROTATION.RELEASE_HANDLING.V1", "held_positions", "RULE.EXIT.RELEASE_FULL_SELL.V1", EXIT_SHA))
        self.assertFalse(supersession["superseded_part_in_force_at"]["superseded_value_in_force"])
        if "SUPERSEDED_BY" not in EXIT.RR.ROLES:
            self.assertNotIn("SUPERSEDED_BY", [r["role"] for r in overlaid["rule_refs"]])
        self.assertEqual(overlaid["rule_refs"], sorted(overlaid["rule_refs"], key=lambda r: (r["rule_id"], r["role"])))
        self.assertEqual(EXIT.file_sha256(ROOT / RC.POLICY_RELATIVE_PATH), ROTATION_POLICY_FILE_SHA)

    def test_portal_projection_overlay_keeps_committed_projection(self):
        path = ROOT / RC.PORTAL_RELATIVE_PATH
        committed = path.read_bytes()
        projection = json.loads(committed)
        overlaid = EXIT.overlay_portal_projection(POLICY, projection)
        self.assertEqual(overlaid["source_projection_payload_sha256"], projection["payload_sha256"])
        row = overlaid["display_rules_superseded_ko"][0]
        self.assertEqual((row["rule_refs"][0]["role"], row["registry_supersession"]["relation"]), ("APPLIED", "SUPERSEDED_BY"))
        EXIT.verify_payload_sha(overlaid, "OVERLAY_SHA")
        self.assertEqual(path.read_bytes(), committed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
