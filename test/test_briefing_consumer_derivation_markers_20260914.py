#!/usr/bin/env python3
"""``_validate_pinned_delivery_packet`` derivation-marker regression.

Problem: the consumer's closed top-level field set predates three additive
daily_orchestrator packet fields introduced on main --
``runtime_regime_readiness_version`` (655db94a, 2026-09-05),
``flow_replay_version`` (2026-09-07) and ``crypto_derivation_version``
(3074f410, 2026-09-09) -- so it rejected every retained
``daily_orchestrator/6`` packet since 2026-09-06 AM with
DELIVERY_PACKET_FIELDS_MISMATCH.

Fix: the consumer now allows exactly these three optional markers on top of
the unchanged required field set. Each, when present, must be a plain
``int`` (not ``bool``/``None``) in its supported set --
``runtime_regime_readiness_version`` in {1, 2, 3}, ``flow_replay_version``
in {1}, ``crypto_derivation_version`` in {1} -- and markers are accepted
only when ``contract_version == "daily_orchestrator/6"``; otherwise the
consumer fails DELIVERY_PACKET_DERIVATION_VERSION_INVALID. The sets are
hard-coded in the consumer, not imported from the orchestrator, so the
consumer stays independent of the producer checkout.

Inputs are the real retained packets under ``evidence/daily_briefing/**``
(committed history; nothing here reads the wall clock) plus small
in-memory mutations of one such packet to exercise the reject paths a real
retained fixture does not happen to cover. No authority, status, schema or
packet-shape change beyond the marker acceptance itself.
"""

from __future__ import annotations

import copy
import glob
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONSUMER = _load(
    "briefing_consumer_derivation_markers_consumer",
    ROOT / ".github/scripts/consume_scheduled_briefing_authority.py",
)

MARKERS = CONSUMER.OPTIONAL_DELIVERY_PACKET_DERIVATION_MARKERS
MARKER_CONTRACT_VERSION = CONSUMER.DELIVERY_PACKET_DERIVATION_MARKER_CONTRACT_VERSION
SUPPORTED_PACKET_CONTRACT_VERSIONS = (
    "daily_orchestrator/3", "daily_orchestrator/4",
    "daily_orchestrator/5", "daily_orchestrator/6",
)


def _retained_packets() -> list[tuple[str, dict]]:
    """Every packet.json committed under evidence/daily_briefing, as-is."""
    rows = []
    for path in sorted(glob.glob(str(ROOT / "evidence/daily_briefing/**/packet.json"), recursive=True)):
        rows.append((path, json.loads(Path(path).read_text(encoding="utf-8"))))
    return rows


RETAINED_PACKETS = _retained_packets()


def _rehash(packet: dict) -> dict:
    packet = dict(packet)
    packet.pop("packet_sha256", None)
    packet["packet_sha256"] = hashlib.sha256(
        CONSUMER.canonical_json(packet).encode("utf-8")
    ).hexdigest()
    return packet


def _find_retained(contract_version: str, *, with_markers: bool) -> dict:
    """A real retained packet at the given contract_version, deep-copied."""
    for _, packet in RETAINED_PACKETS:
        if packet.get("contract_version") != contract_version:
            continue
        all_markers_present = set(MARKERS) <= set(packet)
        no_markers_present = not (set(MARKERS) & set(packet))
        if with_markers and all_markers_present:
            return copy.deepcopy(packet)
        if not with_markers and no_markers_present:
            return copy.deepcopy(packet)
    raise AssertionError(
        f"no retained fixture at {contract_version} with_markers={with_markers}"
    )


class RetainedPacketFixturesCoverExpectedVersions(unittest.TestCase):
    """Guard against the glob silently matching nothing (e.g. a sparse checkout)."""

    def test_retained_fixtures_span_every_known_contract_version(self):
        seen = {packet.get("contract_version") for _, packet in RETAINED_PACKETS}
        for version in ("daily_orchestrator/2", "daily_orchestrator/3",
                        "daily_orchestrator/5", "daily_orchestrator/6"):
            with self.subTest(version=version):
                self.assertIn(version, seen)

    def test_at_least_one_retained_v6_packet_carries_every_marker(self):
        self.assertTrue(any(
            packet.get("contract_version") == "daily_orchestrator/6"
            and set(MARKERS) <= set(packet)
            for _, packet in RETAINED_PACKETS
        ))


class RetainedPacketsValidateByContractVersion(unittest.TestCase):
    """Every retained /3-/6 packet passes; every retained /2 packet still fails."""

    def test_retained_v3_v5_v6_packets_pass(self):
        checked = 0
        for path, packet in RETAINED_PACKETS:
            if packet.get("contract_version") not in SUPPORTED_PACKET_CONTRACT_VERSIONS:
                continue
            checked += 1
            with self.subTest(path=path):
                CONSUMER._validate_pinned_delivery_packet(
                    packet, packet["decision_date"], packet["slot"]
                )
        self.assertGreater(checked, 0)

    def test_retained_v2_packets_fail_by_design(self):
        checked = 0
        for path, packet in RETAINED_PACKETS:
            if packet.get("contract_version") != "daily_orchestrator/2":
                continue
            checked += 1
            with self.subTest(path=path):
                with self.assertRaisesRegex(
                    CONSUMER.ScheduledConsumerError, "DELIVERY_PACKET_SCHEMA_UNSUPPORTED"
                ):
                    CONSUMER._validate_pinned_delivery_packet(
                        packet, packet["decision_date"], packet["slot"]
                    )
        self.assertGreater(checked, 0)


class DerivationMarkerAcceptanceTests(unittest.TestCase):
    """Direct mutation coverage for value/version-gating a retained fixture may not hit."""

    def setUp(self):
        self.base = _find_retained("daily_orchestrator/6", with_markers=True)
        for name in MARKERS:
            self.assertIn(name, self.base)

    def _validate(self, packet: dict) -> None:
        CONSUMER._validate_pinned_delivery_packet(
            packet, packet["decision_date"], packet["slot"]
        )

    def test_every_supported_marker_value_is_accepted(self):
        for name, allowed in MARKERS.items():
            for value in sorted(allowed):
                with self.subTest(marker=name, value=value):
                    packet = copy.deepcopy(self.base)
                    packet[name] = value
                    self._validate(_rehash(packet))

    def test_markers_are_independently_optional(self):
        for name in MARKERS:
            with self.subTest(missing=name):
                packet = copy.deepcopy(self.base)
                del packet[name]
                self._validate(_rehash(packet))
        packet = copy.deepcopy(self.base)
        for name in MARKERS:
            del packet[name]
        self._validate(_rehash(packet))

    def test_null_marker_value_is_rejected(self):
        packet = copy.deepcopy(self.base)
        packet["runtime_regime_readiness_version"] = None
        with self.assertRaisesRegex(
            CONSUMER.ScheduledConsumerError, "DELIVERY_PACKET_DERIVATION_VERSION_INVALID"
        ):
            self._validate(packet)

    def test_bool_marker_value_is_rejected(self):
        packet = copy.deepcopy(self.base)
        packet["flow_replay_version"] = True
        with self.assertRaisesRegex(
            CONSUMER.ScheduledConsumerError, "DELIVERY_PACKET_DERIVATION_VERSION_INVALID"
        ):
            self._validate(packet)

    def test_out_of_range_marker_value_is_rejected(self):
        packet = copy.deepcopy(self.base)
        packet["runtime_regime_readiness_version"] = 4
        with self.assertRaisesRegex(
            CONSUMER.ScheduledConsumerError, "DELIVERY_PACKET_DERIVATION_VERSION_INVALID"
        ):
            self._validate(packet)

    def test_string_marker_value_is_rejected(self):
        packet = copy.deepcopy(self.base)
        packet["crypto_derivation_version"] = "1"
        with self.assertRaisesRegex(
            CONSUMER.ScheduledConsumerError, "DELIVERY_PACKET_DERIVATION_VERSION_INVALID"
        ):
            self._validate(packet)

    def test_unknown_extra_field_is_rejected(self):
        packet = copy.deepcopy(self.base)
        packet["some_unregistered_marker"] = 1
        with self.assertRaisesRegex(
            CONSUMER.ScheduledConsumerError, "DELIVERY_PACKET_FIELDS_MISMATCH"
        ):
            self._validate(packet)

    def test_marker_on_a_non_v6_packet_is_rejected(self):
        self.assertNotEqual(MARKER_CONTRACT_VERSION, "daily_orchestrator/5")
        packet = _find_retained("daily_orchestrator/5", with_markers=False)
        packet["runtime_regime_readiness_version"] = 1
        with self.assertRaisesRegex(
            CONSUMER.ScheduledConsumerError, "DELIVERY_PACKET_DERIVATION_VERSION_INVALID"
        ):
            self._validate(packet)


if __name__ == "__main__":
    unittest.main()
