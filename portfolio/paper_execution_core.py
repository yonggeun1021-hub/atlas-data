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
import gzip
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
PINNED_CONFIG_SHA256 = "b86f6836a0b887c5d798f18c621cc8869fcfd03f85d89eb06226e9bf48a035cb"
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

    def __init__(self, config: dict, config_sha256: str, context: REFS.RegistryContext, root: Path = ROOT):
        self.root = Path(root)
        self.config = config
        self.config_sha256 = config_sha256
        self.context = context
        self.params = {}
        self.param_rules = {}
        self.unavailable = {}
        for alias, ref in config["registry_parameters"].items():
            row = context.rules.get(ref["rule_id"])
            item = None if row is None or not REGISTRY.is_decided(row) else row["key_parameters"].get(ref["key_parameter"])
            if item is None:
                # A replay registry snapshot may predate a row; fail only when used.
                self.unavailable[alias] = f"{ref['rule_id']}:{ref['key_parameter']}"
                continue
            self.params[alias] = copy.deepcopy(item["value"])
            self.param_rules[alias] = ref["rule_id"]

    @property
    def interpretations(self) -> dict:
        return self.config["cio_interpretations"]

    def param(self, alias: str):
        if alias in self.unavailable:
            fail("REGISTRY_PARAMETER_UNAVAILABLE", self.unavailable[alias])
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
    order_ref = config["registry_parameters"]["reduction_order"]
    registry_order = context.rules[order_ref["rule_id"]]["key_parameters"][order_ref["key_parameter"]]["value"]
    if config["cio_interpretations"]["reduction"]["tier_order"] != registry_order:
        fail("CONFIG_REDUCTION_ORDER_DIFFERS_FROM_REGISTRY")
    ids = [item["id"] for item in config["not_defined"]]
    if len(ids) != len(set(ids)):
        fail("CONFIG_NOT_DEFINED_DUPLICATE")


def _read_config(root: Path, verify_pin: bool):
    raw = (root / CONFIG_RELATIVE_PATH).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if verify_pin and sha != PINNED_CONFIG_SHA256:
        fail("CONFIG_SHA_PIN_MISMATCH", sha)
    return json.loads(raw.decode("utf-8")), sha


@functools.lru_cache(maxsize=None)
def _load_cached(root_text: str, verify_pin: bool) -> Core:
    root = Path(root_text)
    config, sha = _read_config(root, verify_pin)
    context = REFS.RegistryContext.load(root / REGISTRY.REGISTRY_RELATIVE_PATH, root=root)
    _validate_config(config, context)
    core = Core(config, sha, context, root)
    if core.unavailable:
        fail("CONFIG_KEY_PARAMETER_MISSING", str(sorted(core.unavailable.values())))
    return core


def load_core(root: Path = ROOT, *, verify_pin: bool = True) -> Core:
    """Core for new decisions: the current, fully validated registry."""
    return _load_cached(str(Path(root).resolve()), verify_pin)


# ---------------------------------------------------------------------------
# Replay against the registry a record was written with
# ---------------------------------------------------------------------------
# Records name the registry by sha256 in every rule_refs entry.  Replay must
# not depend on today's registry (rows get added), and git history is not
# available in shallow CI checkouts, so the exact registry bytes are kept
# (gzip, sha256 of the uncompressed JSON) in an append-only, content-addressed store.  Every registry change commits its
# snapshot (``write_registry_snapshot``; a test fails if the current one is
# missing).  A snapshot was validated when it was current; on replay its
# bytes are only re-hashed, never re-validated against today's fixed id list.
SNAPSHOT_DIR = "evidence/rule_registry_snapshots"


def snapshot_relative_path(registry_sha256: str) -> str:
    if not isinstance(registry_sha256, str) or SHA256_RE.fullmatch(registry_sha256) is None:
        fail("REGISTRY_SHA_INVALID")
    return f"{SNAPSHOT_DIR}/rule_registry_v1-{registry_sha256}.json.gz"


def write_registry_snapshot(root: Path = ROOT) -> str:
    raw = (Path(root) / REGISTRY.REGISTRY_RELATIVE_PATH).read_bytes()
    REGISTRY.load_registry(Path(root) / REGISTRY.REGISTRY_RELATIVE_PATH, root=Path(root))
    sha = hashlib.sha256(raw).hexdigest()
    path = Path(root) / snapshot_relative_path(sha)
    if path.exists():
        if gzip.decompress(path.read_bytes()) != raw:
            fail("REGISTRY_SNAPSHOT_APPEND_ONLY_CONFLICT", sha)
        return sha
    path.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 keeps the compressed bytes deterministic; the sha is of the JSON bytes.
    path.write_bytes(gzip.compress(raw, mtime=0))
    return sha


@functools.lru_cache(maxsize=None)
def _load_replay_cached(root_text: str, registry_sha256: str) -> Core:
    root = Path(root_text)
    config, sha = _read_config(root, True)
    path = root / snapshot_relative_path(registry_sha256)
    try:
        raw = gzip.decompress(path.read_bytes())
    except OSError:
        fail("REGISTRY_SNAPSHOT_UNAVAILABLE", registry_sha256)
    if hashlib.sha256(raw).hexdigest() != registry_sha256:
        fail("REGISTRY_SNAPSHOT_SHA_MISMATCH", registry_sha256)
    context = REFS.RegistryContext(json.loads(raw.decode("utf-8")), registry_sha256, REGISTRY.REGISTRY_RELATIVE_PATH)
    return Core(config, sha, context, root)


def load_core_for_record(record: dict, root: Path = ROOT) -> Core:
    """Core bound to the registry snapshot and config version a record names."""
    shas = {ref.get("registry_sha256") for ref in record.get("rule_refs") or [] if isinstance(ref, dict)}
    if len(shas) != 1:
        fail("RECORD_REGISTRY_SHA_AMBIGUOUS")
    core = _load_replay_cached(str(Path(root).resolve()), shas.pop())
    if record.get("config_sha256") != core.config_sha256:
        # Behaviour changes ship with a new config pin; an older record is not
        # silently re-derived under different rules.
        fail("CONFIG_VERSION_UNAVAILABLE", str(record.get("config_sha256")))
    return core
