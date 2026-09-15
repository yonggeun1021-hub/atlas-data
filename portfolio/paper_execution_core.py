#!/usr/bin/env python3
"""Shared loader for the PAPER execution core v1 (build plan PR1).

``config/paper_execution_core_v1.json`` names, for every ratified number the
core uses, the registry row and key parameter it comes from.  This loader
resolves those values from ``config/rule_registry_v1.json`` (validated against
the byte-exact authority records by ``governance/rule_registry.py``) so no
ratified number is restated in code.  CIO interpretations carry their source
(build plan section 9 / execution contract canon section) in the config.

Money and fractions are exact ``fractions.Fraction`` values parsed from the
records' decimal strings and serialized as ``"p/q"`` (or ``"n"``), so every
record re-derives byte-identically without a rounding convention.

Pure and offline: no network, no secret, no order, no runtime wiring.
"""
from __future__ import annotations

import copy
from fractions import Fraction
import functools
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance import rule_registry as REGISTRY  # noqa: E402
from governance import rule_refs as REFS  # noqa: E402


CONFIG_RELATIVE_PATH = "config/paper_execution_core_v1.json"
CONFIG_SCHEMA_VERSION = "paper_execution_core/1"
# Two-place edit on purpose: a config change must also change this pin.
PINNED_CONFIG_SHA256 = "b405bdd68e79378edc6e23c9be1bfd9d9f0ae7b498b03d386041b781a76c7f7f"
PINNED_SOURCE_DOCUMENTS = {
    "execution_contract_canon": "7a26907f9c05935278ae9232e4de90bb0d23e39447a57c22f66484642a35127c",
    "build_plan": "11f3d3422378cc152ae2af555cfb62926e3fe6779f2de0125c1bd811ebf6412a",
}
MARKETS = ("CRYPTO", "KR", "US")
DECIMAL_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
RATIO_RE = re.compile(r"^-?[0-9]+/[1-9][0-9]*$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-/]{0,160}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PaperExecutionCoreError(ValueError):
    """Fail-closed PAPER execution core violation."""


def fail(code: str, detail: str = "") -> None:
    raise PaperExecutionCoreError(f"{code}:{detail}" if detail else code)


canonical_json = REGISTRY.canonical_json
payload_sha256 = REGISTRY.payload_sha256


def frac(value, label: str = "value") -> Fraction:
    """Exact number from a decimal / ratio string or an int (never a float)."""
    if isinstance(value, bool) or isinstance(value, float):
        fail("NUMBER_TYPE_INVALID", label)
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, str) and (DECIMAL_RE.fullmatch(value) or RATIO_RE.fullmatch(value)):
        return Fraction(value)
    fail("NUMBER_INVALID", label)


def fstr(value: Fraction) -> str:
    value = Fraction(value)
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def opt_frac(value, label: str):
    return None if value is None else frac(value, label)


def opt_fstr(value):
    return None if value is None else fstr(value)


def require_utc(value, label: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        fail("UTC_TIMESTAMP_INVALID", label)
    return value


def require_token(value, label: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        fail("TOKEN_INVALID", label)
    return value


def require_market(value) -> str:
    if value not in MARKETS:
        fail("MARKET_INVALID", str(value))
    return value


def sign(record: dict, field: str) -> dict:
    record = copy.deepcopy(record)
    record.pop(field, None)
    record[field] = payload_sha256(record)
    return record


def verify_signed(record: dict, field: str, code: str) -> None:
    unsigned = {k: v for k, v in record.items() if k != field}
    if record.get(field) != payload_sha256(unsigned):
        fail(code)


class Core:
    """Validated config + registry context + resolved ratified parameters."""

    def __init__(self, config: dict, config_sha256: str, context: REFS.RegistryContext):
        self.config = config
        self.config_sha256 = config_sha256
        self.context = context
        self.params = {}
        self.param_rules = {}
        for alias, ref in config["registry_parameters"].items():
            row = context.rules.get(ref["rule_id"])
            if row is None or not REGISTRY.is_decided(row):
                fail("CONFIG_RULE_NOT_DECIDED", ref["rule_id"])
            item = row["key_parameters"].get(ref["key_parameter"])
            if item is None:
                fail("CONFIG_KEY_PARAMETER_MISSING", f"{ref['rule_id']}:{ref['key_parameter']}")
            self.params[alias] = copy.deepcopy(item["value"])
            self.param_rules[alias] = ref["rule_id"]

    @property
    def interpretations(self) -> dict:
        return self.config["cio_interpretations"]

    def param(self, alias: str):
        if alias not in self.params:
            fail("CONFIG_PARAMETER_UNKNOWN", alias)
        return copy.deepcopy(self.params[alias])

    def rule_refs(self, pairs, decision_at_utc: str) -> list:
        """Canonical ``rule_refs``; every cited rule must be in force at the decision."""
        require_utc(decision_at_utc, "decision_at_utc")
        for rule_id, _role in pairs:
            row = self.context.rules.get(rule_id)
            if row is None or not REGISTRY.in_force_at(row, decision_at_utc, self.context.registry):
                fail("RULE_NOT_IN_FORCE_AT_DECISION", f"{rule_id}@{decision_at_utc}")
        return REFS.canonical_rule_refs(pairs, self.context)

    def not_defined_ids(self) -> list:
        return sorted(item["id"] for item in self.config["not_defined"])


def _validate_config(config: dict, context: REFS.RegistryContext) -> None:
    if not isinstance(config, dict) or config.get("schema_version") != CONFIG_SCHEMA_VERSION:
        fail("CONFIG_SCHEMA_INVALID")
    for key, sha in PINNED_SOURCE_DOCUMENTS.items():
        if config["source_documents"].get(key, {}).get("sha256") != sha:
            fail("CONFIG_SOURCE_DOCUMENT_SHA_MISMATCH", key)
    ratified_by = config["source_documents"]["execution_contract_canon"]["ratified_as_source_document_of_record_sha256"]
    d10 = context.rules["RULE.VALIDATION.MECHANICAL_ONLY.V1"]
    if REGISTRY.primary_record_sha256(d10) != ratified_by:
        fail("CONFIG_CANON_RATIFICATION_RECORD_MISMATCH")
    if config["registry"]["path"] != REGISTRY.REGISTRY_RELATIVE_PATH:
        fail("CONFIG_REGISTRY_PATH_MISMATCH")
    authority = config["authority"]
    if any(value is not False for value in authority.values()):
        fail("CONFIG_AUTHORITY_MUST_BE_FALSE")
    ids = [item["id"] for item in config["not_defined"]]
    if len(ids) != len(set(ids)):
        fail("CONFIG_NOT_DEFINED_DUPLICATE")


@functools.lru_cache(maxsize=4)
def _load_cached(root_text: str, verify_pin: bool) -> Core:
    root = Path(root_text)
    path = root / CONFIG_RELATIVE_PATH
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if verify_pin and sha != PINNED_CONFIG_SHA256:
        fail("CONFIG_SHA_PIN_MISMATCH", sha)
    config = json.loads(raw.decode("utf-8"))
    context = REFS.RegistryContext.load(root / REGISTRY.REGISTRY_RELATIVE_PATH, root=root)
    _validate_config(config, context)
    return Core(config, sha, context)


def load_core(root: Path = ROOT, *, verify_pin: bool = True) -> Core:
    return _load_cached(str(Path(root).resolve()), verify_pin)
