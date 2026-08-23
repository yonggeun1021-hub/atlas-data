#!/usr/bin/env python3
"""P8-10 Price Reflection regression — reduced MVP scope (CIO final
integration ruling on PR #212, 2026-08-23).

`decision/price_reflection.py`'s Event Evidence Authority engine
(`decision/event_evidence.py`, built across CIO review rounds 5-9) has been
REMOVED from this PR entirely -- not patched, DELETED, along with this
module's own `event_reaction`/`reflection_reference` citation-input
parameters and every internal function that only existed to verify or
threshold-classify them. See `decision/price_reflection.py`'s own module
docstring for the full ruling. This file's regressions now cover only the
reduced MVP boundary the CIO explicitly kept:

  * real, caller-supplied price/volume/valuation data -> a real `price_state`
    momentum read (`OVEREXTENDED`/`STRONG_MOMENTUM`/`MODERATE`/`WEAK`/
    `UNKNOWN`), completely unaffected by this reduction;
  * `reflection_status` is now the literal constant `"UNKNOWN"` in EVERY
    packet this module can ever build -- proven here structurally (no
    `event_reaction`/`reflection_reference` parameter exists at all, not
    merely "currently unused"), not merely empirically (no real subject
    happens to have qualifying evidence);
  * `PRICE_DATA_MISSING`/`PRICE_STALE`/`REFLECTION_UNCERTAIN_WITH_VALID_
    PRICE` `data_state` separation, `PROVISIONAL` threshold-basis exposure,
    closed output vocabulary/tamper-evidence, and the offline/output-outside
    -repo CLI guard.
"""

import ast
import copy
from decimal import Decimal
import importlib.util
import inspect
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "price_reflection.py"
PRICE_EVIDENCE_SOURCE = ROOT / "decision" / "price_evidence.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("price_reflection", SOURCE)
CONTRACT = MODULE.load_contract()
PRICE_EVIDENCE = load_module("price_reflection_real_price_evidence", PRICE_EVIDENCE_SOURCE)

# A real, stable Korea ticker identity + timestamp, reused by other test
# files (test_alpha_review.py, test_pilot_gate_hardening_fixtures.py) as the
# subject/decision_date/price_as_of for their OWN synthetic price_reflection
# fixtures -- not tied to any verification machinery any more (removed
# entirely, see decision/price_reflection.py's own docstring), just a
# stable, real, non-restricted-Pilot Korea subject identity.
REAL_EVIDENCE_SUBJECT = "329180.KS"
REAL_EVIDENCE_DECISION_DATE = "2026-08-20"
REAL_EVIDENCE_GENERATED_AT = "2026-08-20T00:00:00Z"
REAL_EVIDENCE_PRICE_AS_OF = "2026-08-19T21:58:30Z"


def base_kwargs(**overrides):
    value = {
        "subject": "TSM",
        "decision_date": "2026-08-22",
        "generated_at": "2026-08-22T00:00:00Z",
        "contract": CONTRACT,
    }
    value.update(overrides)
    return value


def resign(tampered: dict) -> dict:
    tampered["packet_sha256"] = MODULE.payload_sha256(
        {k: v for k, v in tampered.items() if k != "packet_sha256"}
    )
    return tampered


class PriceReflectionTests(unittest.TestCase):
    # ── authority ────────────────────────────────────────────────────
    def test_authority_dict_exact_values(self):
        self.assertEqual(CONTRACT["authority"], {
            "price_reflection_assembly_only": True,
            "rule_authority_substitution_authorized": False,
            "stage_promotion_authorized": False,
            "candidate_ready_buy_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        })
        packet = MODULE.build_packet(**base_kwargs())
        self.assertEqual(packet["authority"], CONTRACT["authority"])

    # ── Rule 1: staleness forces both fields UNKNOWN unconditionally ────
    def test_stale_price_as_of_forces_both_fields_unknown_despite_strong_positive_inputs(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-07-30T19:59:00Z",  # >5 days before decision_date
            recent_return_windows={"1m": "25", "3m": "30", "6m": "40"},
            relative_strength={"vs_market": "20", "position_vs_recent_high_pct": "1"},
            valuation_context={"position_in_range": "HIGH"},
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertEqual(pr["confidence"], "UNKNOWN")
        self.assertEqual(pr["data_state"], "PRICE_STALE")
        self.assertTrue(any("STALE" in reason for reason in pr["reasons"]))

    def test_missing_price_as_of_forces_both_fields_unknown(self):
        packet = MODULE.build_packet(**base_kwargs(
            recent_return_windows={"1m": "25"},
            relative_strength={"vs_market": "20"},
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertEqual(pr["confidence"], "UNKNOWN")
        self.assertEqual(pr["data_state"], "PRICE_DATA_MISSING")
        self.assertIn("price_as_of", pr["missing_inputs"])

    def test_fresh_price_within_ceiling_is_not_forced_unknown(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",  # 1 day before decision_date
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertNotEqual(packet["price_reflection"]["price_state"], "UNKNOWN")

    def test_price_as_of_in_future_is_rejected(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "PRICE_AS_OF_IN_FUTURE"):
            MODULE.build_packet(**base_kwargs(price_as_of="2026-08-23T00:00:00Z"))

    def test_custom_freshness_ceiling_is_honored(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-01T19:59:00Z",
            freshness_ceiling_days=30,
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertNotEqual(packet["price_reflection"]["price_state"], "UNKNOWN")

    # ── OVEREXTENDED documented as timing risk, not "bad company" ───────
    def test_overextended_documented_as_timing_not_business_quality(self):
        doc = (ROOT / "docs" / "price_reflection_contract.md").read_text(encoding="utf-8")
        self.assertIn("does not mean the", doc.lower())
        self.assertIn("entry-timing risk", doc.lower())

    # ── Rule 2: structurally no thesis/fundamental parameter ───────────
    def test_builder_signature_has_no_thesis_or_fundamental_parameter(self):
        params = list(inspect.signature(MODULE.build_packet).parameters)
        for bad in MODULE.FORBIDDEN_PARAMETER_SUBSTRINGS:
            self.assertFalse(
                any(bad in name.lower() for name in params),
                f"forbidden substring {bad!r} found in builder parameters {params}",
            )
        MODULE.assert_no_fundamental_parameters()

    # ── Core CIO round-2 fix: momentum alone never yields a reflection verdict ──
    def test_sharp_rally_near_high_with_no_reference_point_is_overextended_price_state_but_unknown_reflection(self):
        self.assertNotIn("REJECTED", CONTRACT["allowed_price_state"])
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "25"},
            relative_strength={"vs_market": "20", "position_vs_recent_high_pct": "1"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "OVEREXTENDED")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertEqual(pr["data_state"], "REFLECTION_UNCERTAIN_WITH_VALID_PRICE")

    def test_strong_momentum_alone_never_produces_fully_reflected(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10", "position_vs_recent_high_pct": "50"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "STRONG_MOMENTUM")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertNotIn(pr["reflection_status"], ("FULLY_REFLECTED", "PARTIALLY_REFLECTED", "UNDER_REFLECTED"))

    # ══════════════ scope-reduction regressions (CIO final ruling) ═════════

    def test_reduction_build_packet_has_no_event_or_reference_parameter_at_all(self):
        """Structural, not empirical: `event_reaction`/`reflection_reference`
        do not exist in `build_packet`'s signature -- Python itself raises
        `TypeError` on either, there is no way to even ATTEMPT to supply a
        citation, let alone verify one."""
        params = set(inspect.signature(MODULE.build_packet).parameters)
        self.assertNotIn("event_reaction", params)
        self.assertNotIn("reflection_reference", params)
        for bad_kwargs in (
            {"event_reaction": {"direction": "POSITIVE"}},
            {"reflection_reference": {"reference_event_id": "x"}},
        ):
            with self.assertRaises(TypeError):
                MODULE.build_packet(**base_kwargs(**bad_kwargs))

    def test_reduction_decision_event_evidence_module_no_longer_exists(self):
        self.assertFalse(
            (ROOT / "decision" / "event_evidence.py").exists(),
            "decision/event_evidence.py must be deleted, not merely disconnected",
        )
        self.assertFalse(
            (ROOT / "test" / "fixtures" / "event_evidence").exists(),
            "test/fixtures/event_evidence/ must be deleted along with the engine it backed",
        )

    def test_reduction_reflection_status_is_unconditionally_unknown_even_with_maximal_positive_signal(self):
        """The strongest positive momentum/valuation signal this module can
        be given (rally + near-high + expensive valuation, all fresh) still
        yields reflection_status=UNKNOWN -- there is no remaining input
        combination that unlocks anything else."""
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T23:59:00Z",
            recent_return_windows={"1m": "50", "3m": "60", "6m": "70"},
            relative_strength={
                "vs_market": "45", "vs_sector": "40",
                "volume_change_pct": "200", "position_vs_recent_high_pct": "0",
            },
            valuation_context={"position_in_range": "HIGH"},
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertNotEqual(pr["price_state"], "UNKNOWN")  # a real, strong price signal DOES register
        self.assertEqual(pr["reflection_status"], "UNKNOWN")  # but reflection never does
        self.assertEqual(pr["confidence"], "UNKNOWN")
        self.assertNotEqual(pr["data_state"], "VALID")

    def test_reduction_event_reaction_and_reflection_reference_are_always_fully_inert(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["event_reaction"], MODULE._INERT_EVENT_REACTION)
        self.assertEqual(pr["reflection_reference"], MODULE._INERT_REFLECTION_REFERENCE)
        self.assertTrue(all(v == "UNKNOWN" for v in pr["event_reaction"].values()))
        self.assertTrue(all(v == "UNKNOWN" for v in pr["reflection_reference"].values()))
        self.assertIn("event_reaction", pr["missing_inputs"])
        self.assertIn("reflection_reference", pr["missing_inputs"])

    def test_reduction_tampered_packet_claiming_a_real_citation_is_rejected(self):
        """`validate_packet` re-asserts the inert-only invariant independent
        of how a packet was constructed -- a loaded/tampered packet cannot
        smuggle a fabricated citation back in even though build_packet
        itself could never have produced one."""
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["event_reaction"]["capture_kind"] = "LIVE_OFFICIAL_CAPTURE"
        tampered = resign(tampered)
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "OUTPUT_EVENT_REACTION_MUST_BE_INERT_IN_THIS_REDUCED_SCOPE"
        ):
            MODULE.validate_packet(tampered, CONTRACT)

        tampered2 = resign(copy.deepcopy(packet))
        tampered2["price_reflection"]["reflection_reference"]["expectations_gap_status"] = "POSITIVE"
        tampered2 = resign(tampered2)
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "OUTPUT_REFLECTION_REFERENCE_MUST_BE_INERT_IN_THIS_REDUCED_SCOPE"
        ):
            MODULE.validate_packet(tampered2, CONTRACT)

    def test_reduction_validate_packet_rejects_the_exact_cio_repro_case(self):
        """CIO closing-fix ruling (2026-08-23): `build_packet()` being
        structurally incapable of producing anything but `reflection_status
        ="UNKNOWN"` is NOT the same as `validate_packet()` refusing anything
        else -- the CIO's own direct repro: take a real packet, edit
        `reflection_status` to `"PARTIALLY_REFLECTED"` + `confidence="LOW"`
        + `data_state="VALID"`, recompute the hash. This is that EXACT case,
        used directly (not a different synthetic one) -- `validate_packet`
        must now reject it. `_with_synthetic_reflection_status()`-style
        fixtures in test_alpha_review.py/test_pilot_gate_hardening_
        fixtures.py that used to rely on this exact tamper pattern to reach
        `alpha_review.py`'s positive states are retired for the same
        reason -- see those files' own docstrings."""
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        self.assertEqual(packet["price_reflection"]["reflection_status"], "UNKNOWN")  # sanity
        tampered = copy.deepcopy(packet)
        tampered["price_reflection"]["reflection_status"] = "PARTIALLY_REFLECTED"
        tampered["price_reflection"]["confidence"] = "LOW"
        tampered["price_reflection"]["data_state"] = "VALID"
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in tampered.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "OUTPUT_REFLECTION_STATUS_MUST_BE_UNKNOWN_IN_THIS_REDUCED_SCOPE"
        ):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_reduction_all_real_subjects_still_unknown_via_real_price_evidence_assembly(self):
        """Required confirmation: BTC, all 4 real Pilot subjects
        (TSM/298040.KS/267260.KS/034020.KS), and the 4 restricted Korea
        tickers all resolve `reflection_status=UNKNOWN` through the REAL,
        unmocked `decision/price_evidence.py` assembly + `build_packet()`
        chain -- structurally guaranteed now, not merely because no
        citation happens to have been supplied."""
        for subject in ("BTC", "298040.KS", "267260.KS", "005930.KS", "000660.KS", "TSM", "034020.KS"):
            with self.subTest(subject=subject):
                kwargs = {
                    "subject": subject, "decision_date": "2026-08-22", "generated_at": "2026-08-22T00:00:00Z",
                    **PRICE_EVIDENCE.assemble_price_evidence(subject, "2026-08-22"),
                }
                packet = MODULE.build_packet(**kwargs)
                self.assertEqual(packet["price_reflection"]["reflection_status"], "UNKNOWN")

    # ── threshold approval status ────────────────────────────────────
    def test_classification_thresholds_are_declared_provisional(self):
        self.assertEqual(CONTRACT["classification_thresholds_approval_status"], "PROVISIONAL")
        self.assertIn(
            CONTRACT["classification_thresholds_approval_status"], CONTRACT["allowed_threshold_basis"],
        )

    def test_every_packet_echoes_threshold_basis_verbatim(self):
        packet = MODULE.build_packet(**base_kwargs())
        self.assertEqual(
            packet["price_reflection"]["threshold_basis"],
            CONTRACT["classification_thresholds_approval_status"],
        )

    def test_threshold_basis_mismatch_is_rejected(self):
        packet = MODULE.build_packet(**base_kwargs())
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["threshold_basis"] = "RATIFIED"
        tampered = resign(tampered)
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "OUTPUT_THRESHOLD_BASIS_MISMATCH"):
            MODULE.validate_packet(tampered, CONTRACT)

    # ── Korea data no longer has a bespoke triple-required gate ─────────
    def test_korea_partial_fields_still_yields_a_price_state_from_momentum_alone(self):
        packet = MODULE.build_packet(**base_kwargs(
            subject="298040",
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"position_vs_recent_high_pct": "10"},
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertNotEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")

    def test_data_source_scope_propagates_verbatim(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertEqual(packet["price_reflection"]["data_source_scope"], "IEX_ONLY_PARTIAL_US_MARKET")

    def test_default_data_source_scope_is_unknown(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        self.assertEqual(packet["price_reflection"]["data_source_scope"], "UNKNOWN")

    def test_kraken_ohlc_scope_is_accepted_for_crypto_subjects(self):
        packet = MODULE.build_packet(**base_kwargs(
            subject="BTC",
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "18"},
            relative_strength={"position_vs_recent_high_pct": "0"},
            data_source_scope="KRAKEN_OHLC",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["data_source_scope"], "KRAKEN_OHLC")
        self.assertNotEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")

    def test_allowed_data_source_scope_vocabulary(self):
        self.assertEqual(sorted(CONTRACT["allowed_data_source_scope"]), sorted([
            "IEX_ONLY_PARTIAL_US_MARKET", "KRX_OFFICIAL", "KRAKEN_OHLC", "UNKNOWN",
        ]))

    # ── closed enums reject out-of-vocabulary values ────────────────────
    def test_price_state_enum_is_closed(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["price_state"] = "MOONING"
        tampered = resign(tampered)
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "OUTPUT_PRICE_STATE_INVALID"):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_reflection_status_enum_is_closed(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["reflection_status"] = "TOTALLY_PRICED_IN"
        tampered = resign(tampered)
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "OUTPUT_REFLECTION_STATUS_INVALID"):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_data_source_scope_enum_is_closed(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "DATA_SOURCE_SCOPE_INVALID"):
            MODULE.build_packet(**base_kwargs(data_source_scope="BLOOMBERG_TERMINAL"))

    def test_valuation_position_enum_is_closed(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "VALUATION_CONTEXT_POSITION_INVALID"):
            MODULE.build_packet(**base_kwargs(valuation_context={"position_in_range": "SKY_HIGH"}))

    # ── minimal packet builds and validates ─────────────────────────────
    def test_minimal_packet_builds_and_validates(self):
        packet = MODULE.build_packet(**base_kwargs())
        MODULE.validate_packet(packet, CONTRACT)
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertEqual(pr["data_state"], "PRICE_DATA_MISSING")

    def test_output_fields_are_exactly_the_specified_set(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        self.assertEqual(set(packet), {
            "schema_version", "contract_version", "generated_at", "subject",
            "decision_date", "price_reflection", "authority", "packet_sha256",
        })
        self.assertEqual(packet["schema_version"], "price_reflection_packet/6")
        self.assertEqual(packet["contract_version"], "price_reflection/6")
        self.assertEqual(set(packet["price_reflection"]), {
            "price_state", "reflection_status", "confidence", "data_state", "threshold_basis",
            "price_as_of", "relative_strength", "recent_return_windows", "event_reaction",
            "reflection_reference", "valuation_context", "reasons", "missing_inputs",
            "data_source_scope",
        })
        self.assertEqual(set(packet["price_reflection"]["event_reaction"]), set(MODULE._INERT_EVENT_REACTION))
        self.assertEqual(set(packet["price_reflection"]["reflection_reference"]), set(MODULE._INERT_REFLECTION_REFERENCE))

    # ── determinism + tamper detection ──────────────────────────────────
    def test_deterministic_and_tamper_evident(self):
        kwargs = base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5", "3m": "8"},
            relative_strength={"vs_market": "3", "position_vs_recent_high_pct": "12"},
            valuation_context={"position_in_range": "MID"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        )
        first = MODULE.build_packet(**kwargs)
        second = MODULE.build_packet(**kwargs)
        self.assertEqual(first, second)

        tampered = copy.deepcopy(first)
        tampered["price_reflection"]["reasons"].append("INJECTED")
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "OUTPUT_SHA_MISMATCH"):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_data_state_enum_is_closed(self):
        packet = MODULE.build_packet(**base_kwargs())
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["data_state"] = "TOTALLY_FINE_TRUST_ME"
        tampered = resign(tampered)
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "OUTPUT_DATA_STATE_INVALID"):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_data_state_allowed_vocabulary(self):
        self.assertEqual(sorted(CONTRACT["allowed_data_state"]), sorted([
            "PRICE_DATA_MISSING", "PRICE_STALE",
            "REFLECTION_UNCERTAIN_WITH_VALID_PRICE", "VALID",
        ]))

    def test_price_state_and_reflection_status_vocabularies_have_no_rejected_value(self):
        self.assertEqual(sorted(CONTRACT["allowed_price_state"]), sorted([
            "OVEREXTENDED", "STRONG_MOMENTUM", "MODERATE", "WEAK", "UNKNOWN",
        ]))
        self.assertEqual(sorted(CONTRACT["allowed_reflection_status"]), sorted([
            "UNDER_REFLECTED", "PARTIALLY_REFLECTED", "FULLY_REFLECTED", "UNKNOWN",
        ]))

    # ── CLI is offline and write-outside-repo only ──────────────────────
    def test_cli_is_offline_and_writes_only_outside_repository(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            input_path = temp / "input.json"
            input_path.write_text(json.dumps({
                "subject": "TSM", "decision_date": "2026-08-22",
                "generated_at": "2026-08-22T00:00:00Z",
                "price_as_of": "2026-08-21T19:59:00Z",
                "recent_return_windows": {"1m": "5"},
                "relative_strength": {"vs_market": "3"},
            }), encoding="utf-8")
            output = temp / "out" / "packet.json"
            self.assertEqual(MODULE.run(input_path, output), 0)
            self.assertTrue(output.exists())
            forbidden = ROOT / "data" / "price_reflection_test.json"
            self.assertEqual(MODULE.run(input_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
