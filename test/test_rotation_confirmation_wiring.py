"""Ratified T1 / T2 C5 / new-buy wiring of rotation confirmation states."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WIRING = load_module("rotation_confirmation_wiring_under_test", ROOT / "rotation" / "rotation_confirmation_wiring.py")
RC = WIRING.RC
POLICY = RC.load_policy()
MAPPING = RC.load_state_mapping()
US = POLICY["markets"]["US"]["entities"]


def us_obs(day, ranking):
    order = list(ranking) + [s for s in US if s not in ranking]
    return {"as_of_date": day, "status": "OBSERVED", "unknown_reason": None, "sources": [], "aux": {},
            "scopes": {"SPY": [{"entity_id": s, "source_identity": s, "strength": str(20 - i)} for i, s in enumerate(order)]}}


def us_packets():
    # 09-12 XLY/XLK/XLE confirmed; 09-13 XLY released (bottom once); 09-14 XLB confirmed, XLE first non-top;
    # 09-15 XLE released (second non-top), XLC confirmed; 09-16 XLB/XLC/XLK held
    return RC.build_market_packets(POLICY, "US", [
        us_obs("2026-09-11", ["XLY", "XLK", "XLE"]),
        us_obs("2026-09-12", ["XLY", "XLK", "XLE"]),
        us_obs("2026-09-13", ["XLB", "XLK", "XLE"]),
        us_obs("2026-09-14", ["XLB", "XLK", "XLC"]),
        us_obs("2026-09-15", ["XLB", "XLK", "XLC"]),
        us_obs("2026-09-16", ["XLB", "XLK", "XLC", "XLF"]),
    ], MAPPING)


class T1SelectionTests(unittest.TestCase):
    def setUp(self):
        self.packets = {p["as_of_date"]: p for p in us_packets()}

    def test_selection_contains_only_confirmed_or_held(self):
        packet = self.packets["2026-09-16"]
        selection = WIRING.t1_rotation_selection(packet, "US", "2026-09-16", POLICY)
        self.assertEqual(selection["rotation_selection_status"], "SELECTED")
        states = {row["entity_id"]: row["state"] for row in selection["rotation_selection"]}
        self.assertEqual(states, {"XLB": "STRONG_HELD", "XLC": "STRONG_HELD", "XLK": "STRONG_HELD"})
        self.assertTrue(all(row["state"] in RC.STRONG_STATES for row in selection["rotation_selection"]))
        released = WIRING.t1_rotation_selection(self.packets["2026-09-15"], "US", "2026-09-15", POLICY)
        self.assertIn("XLE", [r["entity_id"] for r in released["excluded_released"]])
        self.assertNotIn("XLE", [r["entity_id"] for r in released["rotation_selection"]])
        self.assertIn("RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1", [r["rule_id"] for r in selection["rule_refs"]])

    def test_emerging_watch_never_enters_selection(self):
        observations = [us_obs(f"2026-09-{day:02d}", ["XLB", "XLC", "XLE"]) for day in range(15, 20)]
        observations.append(us_obs("2026-09-20", ["XLB", "XLC", "XLE", "XLY"]))
        packet = RC.build_market_packets(POLICY, "US", observations, MAPPING)[-1]
        selection = WIRING.t1_rotation_selection(packet, "US", "2026-09-20", POLICY)
        self.assertEqual([r["entity_id"] for r in selection["emerging_watch_display_only"]], ["XLY"])
        self.assertNotIn("XLY", [r["entity_id"] for r in selection["rotation_selection"]])

    def test_lookahead_stale_unknown_and_not_effective_fail_closed(self):
        packet = self.packets["2026-09-16"]
        with self.assertRaisesRegex(RC.RotationConfirmationError, "CONFIRMATION_PACKET_AFTER_EVALUATION_DATE"):
            WIRING.t1_rotation_selection(packet, "US", "2026-09-15", POLICY)
        stale = WIRING.t1_rotation_selection(packet, "US", "2026-09-21", POLICY)
        self.assertEqual((stale["rotation_selection_status"], stale["rotation_selection"]), ("UNKNOWN:CONFIRMATION_STALE", []))
        self.assertEqual(WIRING.t1_rotation_selection(packet, "US", "2026-09-20", POLICY)["rotation_selection_status"], "SELECTED")
        early = RC.build_market_packets(POLICY, "US", [us_obs("2026-09-10", ["XLK"]), us_obs("2026-09-11", ["XLK"])], MAPPING)[-1]
        self.assertEqual(WIRING.t1_rotation_selection(early, "US", "2026-09-14", POLICY)["rotation_selection_status"],
                         "UNKNOWN:RULE_NOT_EFFECTIVE_FOR_EVALUATION_DATE")
        unknown = RC.build_market_packets(POLICY, "US", [
            {"as_of_date": "2026-09-16", "status": "UNKNOWN", "unknown_reason": "SECTOR_ETF_SET_INCOMPLETE",
             "sources": [], "scopes": {}, "aux": {}},
        ], MAPPING)[-1]
        self.assertEqual(WIRING.t1_rotation_selection(unknown, "US", "2026-09-16", POLICY)["rotation_selection_status"],
                         "UNKNOWN:CONFIRMATION_OBSERVATION_UNKNOWN_SECTOR_ETF_SET_INCOMPLETE")
        tampered = copy.deepcopy(packet)
        tampered["scopes"][0]["entities"][0]["state"] = "EMERGING_WATCH"
        with self.assertRaisesRegex(RC.RotationConfirmationError, "PACKET_SHA_MISMATCH"):
            WIRING.t1_rotation_selection(tampered, "US", "2026-09-16", POLICY)
        with self.assertRaisesRegex(RC.RotationConfirmationError, "CONFIRMATION_PACKET_MARKET_MISMATCH"):
            WIRING.t1_rotation_selection(packet, "KR", "2026-09-16", POLICY)


class C5Tests(unittest.TestCase):
    def test_generic_c5_passes_only_confirmed_or_held(self):
        packets = {p["as_of_date"]: p for p in us_packets()}
        packet = packets["2026-09-15"]
        self.assertEqual(WIRING.c5_from_entity(packet, "US", "XLK", "2026-09-15", policy=POLICY)["result"], "PASS")
        released = WIRING.c5_from_entity(packet, "US", "XLE", "2026-09-15", policy=POLICY)
        self.assertEqual((released["result"], released["reason"]), ("FAIL", "SECTOR_NOT_STRONG_CONFIRMED_OR_HELD:STRONG_RELEASED"))
        confirmed_day = packets["2026-09-15"]
        self.assertEqual(WIRING.c5_from_entity(confirmed_day, "US", "XLC", "2026-09-15", policy=POLICY)["reason"], "SECTOR_STRONG_CONFIRMED")
        self.assertEqual(WIRING.c5_from_entity(packet, "US", "XLY", "2026-09-30", policy=POLICY)["reason"],
                         "ROTATION_CONFIRMATION_UNKNOWN:CONFIRMATION_STALE")

    def test_kr_c5_uses_unchanged_membership_function_with_confirmed_ids(self):
        fixture = load_module("security_sector_membership_fixture_for_wiring", ROOT / "test" / "test_security_sector_membership.py")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = fixture.Workspace(Path(tmp))
            doc = workspace.publication(session="2026-09-11", observed_at=fixture.T1, kospi=fixture.base_kospi(), kosdaq=fixture.base_kosdaq())
            kr = WIRING.load_latest_packet("KR")
            evaluation = max(kr["as_of_date"], POLICY["decisions_effective_from"])
            selection = WIRING.t1_rotation_selection(kr, "KR", evaluation, POLICY)
            selected = sorted(r["entity_id"] for r in selection["rotation_selection"])
            for asset_id in ("KR:XKRX:005930", "KR:XKRX:139480", "KR:XKRX:004970", "KR:XKRX:247540"):
                wired = WIRING.c5_rotation_membership_kr(doc, asset_id, kr, fixture.T1, evaluation, POLICY)
                direct = fixture.MEM.c5_rotation_membership(doc, asset_id, selected, fixture.T1)
                self.assertEqual({k: wired[k] for k in direct}, direct)  # decision of the membership component unchanged
                self.assertEqual(wired["rotation_confirmation"]["selected_membership_ids"], selected)
                if wired["result"] == "PASS":
                    self.assertIn(
                        wired["rotation_confirmation"]["membership_sector_state"] or "PARENT",
                        ("STRONG_CONFIRMED", "STRONG_HELD", "PARENT"),
                    )


class NewBuyPermissionTests(unittest.TestCase):
    def setUp(self):
        self.packets = {p["as_of_date"]: p for p in us_packets()}

    def test_market_state_matrix(self):
        packet = self.packets["2026-09-16"]
        self.assertEqual(WIRING.new_buy_permission(packet, "US", "XLK", "RISK_ON", "2026-09-16", policy=POLICY)["new_buy_permission"], "PERMIT")
        self.assertEqual(WIRING.new_buy_permission(packet, "US", "XLK", "NEUTRAL", "2026-09-16", policy=POLICY)["new_buy_permission"], "PERMIT_SELECTIVE")
        for state in ("RISK_OFF", "STRESS", "UNKNOWN"):
            result = WIRING.new_buy_permission(packet, "US", "XLK", state, "2026-09-16", policy=POLICY)
            self.assertEqual((result["new_buy_permission"], result["reason"]), ("DENY", f"MARKET_STATE_{state}_DENIES_NEW_BUYS"))
        for state in ("RISK_ON", "NEUTRAL"):
            neutral = WIRING.new_buy_permission(packet, "US", "XLV", state, "2026-09-16", policy=POLICY)
            self.assertEqual((neutral["new_buy_permission"], neutral["reason"]), ("DENY", "SECTOR_NOT_STRONG_CONFIRMED_OR_HELD:NEUTRAL"))
        with self.assertRaisesRegex(RC.RotationConfirmationError, "MARKET_STATE_INVALID"):
            WIRING.new_buy_permission(packet, "US", "XLK", "BULL", "2026-09-16", policy=POLICY)

    def test_release_is_new_buy_stop_only(self):
        packet = self.packets["2026-09-15"]
        result = WIRING.new_buy_permission(packet, "US", "XLE", "RISK_ON", "2026-09-15", policy=POLICY)
        self.assertEqual((result["new_buy_permission"], result["reason"]), ("DENY", "STRENGTH_RELEASED_NEW_BUY_STOP"))
        self.assertTrue(result["release_new_buy_stop"])
        self.assertFalse(result["forced_exit"])
        self.assertEqual(result["held_position_action"], "FOLLOW_EXISTING_STOP_LOSS_TAKE_PROFIT_RULES")
        self.assertTrue(result["exit_review_display"])
        self.assertFalse(result["allocation_v2_numbers_changed"])
        self.assertIn(("RULE.ROTATION.RELEASE_HANDLING.V1", "BLOCKED_BY"), [(r["rule_id"], r["role"]) for r in result["rule_refs"]])
        risk_off = WIRING.new_buy_permission(packet, "US", "XLE", "RISK_OFF", "2026-09-15", policy=POLICY)
        self.assertFalse(risk_off["forced_exit"])
        self.assertTrue(risk_off["release_new_buy_stop"])


class OtherComponentsUnchangedTests(unittest.TestCase):
    PINNED_RUNTIME_DIRS = ("shadow", "decision", "universe", "realtime", "portfolio", "regime", "private_evidence")

    # New PAPER exit policy v1 layer (build plan PR2). It is not part of the pinned
    # runtime chain; the second pattern proves no existing module imports it.
    NEW_EXIT_LAYER_MODULES = ("portfolio/paper_exit_policy_v1.py", "portfolio/paper_shadow_controls.py")
    # Crypto PAPER wiring v2 (build plan PR3) reads confirmation packets and exit
    # intents only on the decision snapshot /4 / runtime request /4 path (off
    # until the configured cutover T_cut). In these modules the confirmation and
    # exit layers may be *loaded* only inside the named lazy loader functions;
    # plain packet-field names are data, not an import of the layer.
    PR3_LAZY_LOADERS = {
        "universe/crypto_candidate_promotion.py": {"_rotation_wiring"},
        "universe/crypto_paper_buy_eligibility.py": {"_v3_modules"},
        "shadow/crypto_paper_runtime_bridge.py": {"_v4_modules"},
        "decision/crypto_paper_decision_snapshot.py": set(),
        "briefing/crypto_funnel_briefing.py": set(),
    }
    WIRING_KEY_ACCESS_ONLY = ("briefing/crypto_funnel_briefing.py",)
    LAYER_MODULE = re.compile(r"(rotation_confirmation(_wiring)?|paper_exit_policy_v1|paper_shadow_controls)(\.py)?$")

    def _layer_loads_outside_loaders(self, path: Path, loaders: set) -> list:
        import ast

        tree = ast.parse(path.read_text(encoding="utf-8"))
        found, seen_loaders = [], set()

        def visit(node, enclosing):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                enclosing = node.name
                if node.name in loaders:
                    seen_loaders.add(node.name)
            names = []
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.fullmatch(r"[\w./-]+\.py", node.value):
                names.append(node.value.rsplit("/", 1)[-1])
            elif isinstance(node, ast.Import):
                names.extend(alias.name.rsplit(".", 1)[-1] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.extend(alias.name for alias in node.names)
                names.append((node.module or "").rsplit(".", 1)[-1])
            elif isinstance(node, ast.Call):
                # String loads: importlib.import_module("rotation.rotation_confirmation"),
                # import_module(...), __import__(...), importlib.__import__(...).
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if called in {"import_module", "__import__"} and node.args \
                        and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    names.append(node.args[0].value.rsplit(".", 1)[-1])
            for name in names:
                if self.LAYER_MODULE.search(name) and enclosing not in loaders:
                    found.append(f"{name}@{enclosing}")
            for child in ast.iter_child_nodes(node):
                visit(child, enclosing)

        visit(tree, None)
        self.assertEqual(seen_loaders, loaders, path)
        return found

    def test_guard_catches_import_statements_path_loads_and_string_imports(self):
        source = (
            "import importlib\n"
            "def _rotation_wiring():\n"
            "    return importlib.import_module('rotation.rotation_confirmation_wiring')\n"
            "def sneaky():\n"
            "    a = importlib.import_module('rotation.rotation_confirmation')\n"
            "    b = __import__('portfolio.paper_exit_policy_v1')\n"
            "    from rotation import rotation_confirmation\n"
            "    return _load('x', 'portfolio/paper_shadow_controls.py')\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "module.py"
            path.write_text(source, encoding="utf-8")
            found = self._layer_loads_outside_loaders(path, {"_rotation_wiring"})
        self.assertEqual(sorted(found), sorted([
            "rotation_confirmation@sneaky", "paper_exit_policy_v1@sneaky",
            "rotation_confirmation@sneaky", "paper_shadow_controls.py@sneaky",
        ]))

    def test_no_existing_producer_or_pinned_runtime_module_imports_the_confirmation_layer(self):
        pattern = re.compile(r"rotation_confirmation")
        exit_layer = re.compile(r"paper_exit_policy_v1|paper_shadow_controls")
        offenders = []
        for directory in self.PINNED_RUNTIME_DIRS + ("briefing", "discovery", ".github/scripts"):
            base = ROOT / directory
            if not base.exists():
                continue
            for path in base.rglob("*.py"):
                relative = path.relative_to(ROOT).as_posix()
                if relative in self.NEW_EXIT_LAYER_MODULES:
                    continue
                if relative in self.PR3_LAZY_LOADERS:
                    offenders.extend(
                        f"{relative}:{item}"
                        for item in self._layer_loads_outside_loaders(path, self.PR3_LAZY_LOADERS[relative])
                    )
                    if relative in self.WIRING_KEY_ACCESS_ONLY:
                        # Only the /4 packet's wiring key may be read; no other mention.
                        text = path.read_text(encoding="utf-8")
                        rest = text.replace('["rotation_confirmation"]', "")
                        if text == rest or pattern.search(rest) or exit_layer.search(rest):
                            offenders.append(f"{relative}:NOT_KEY_ACCESS_ONLY")
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if pattern.search(text) or exit_layer.search(text):
                    offenders.append(relative)
        self.assertEqual(offenders, [])

    def test_existing_rotation_contracts_still_load_unchanged(self):
        ledger = load_module("ledger_for_unchanged_check", ROOT / "rotation" / "rotation_state_ledger.py")
        ratification = load_module("ratification_for_unchanged_check", ROOT / "rotation" / "rotation_state_policy_ratification.py")
        self.assertEqual(ledger.load_contract()["repository_default_policy"], "ABSENT")
        contract = ratification.load_contract()
        self.assertEqual(contract["state_by_bucket_transition"], MAPPING)
        self.assertFalse(contract["authority"]["candidate_ranking_authorized"])

    def test_membership_c5_behaviour_is_identical_for_same_selection(self):
        fixture = load_module("security_sector_membership_fixture_for_unchanged", ROOT / "test" / "test_security_sector_membership.py")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = fixture.Workspace(Path(tmp))
            doc = workspace.publication(session="2026-09-11", observed_at=fixture.T1, kospi=fixture.base_kospi(), kosdaq=fixture.base_kosdaq())
            ssm = WIRING._ssm()
            for ids in ([], ["KOSPI.SECTOR.18"], ["KOSPI.SECTOR.20"], ["KOSDAQ.SECTOR.05"]):
                self.assertEqual(ssm.c5_rotation_membership(doc, "KR:XKRX:005930", ids, fixture.T1),
                                 fixture.MEM.c5_rotation_membership(doc, "KR:XKRX:005930", ids, fixture.T1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
