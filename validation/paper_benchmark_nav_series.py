#!/usr/bin/env python3
"""Benchmark ("simply bought and held") NAV series producer for a PAPER account.

Why this file exists
--------------------
The ratified checkpoint schedule card
(``CLAUDE_CIO_CHECKPOINT_SCHEDULE_CARD_20260917.md``, sha256
``50e422ba…``) asks one question at day 30: **그냥 들고 있는 것보다 나은가.**
Two of its five stop rules cannot be answered by anything that exists today:

- rule 1, 비용 차감 후 그냥 보유보다 낮다 — there is no benchmark NAV series
  anywhere in either repo. ``validation/crypto_paper_counterfactual.py``'s only
  counterfactual is ``no_trade_benchmark_pnl = "0"``, which is *not* holding.
- rule 5, 하락 구간에서 그냥 보유보다 더 깎였다 — needs the PAPER account's own
  drawdown series (``_drawdown_metrics`` there already computes that from a
  caller-supplied ``NAV_SERIES``) *and* the same missing benchmark.

Neither can be backfilled once trading has started, because a benchmark that
starts at an instant somebody chose after seeing the prices is not a benchmark.
So this module's real product is not the arithmetic — it is the **anchor**.

What is reused rather than duplicated
------------------------------------
- ``shadow/crypto_paper_simulator.py`` validates the ledger (hash chain,
  schema, replay). This module never parses a ledger event without it and
  never recomputes cash, positions or fills.
- ``validation/crypto_paper_counterfactual.py`` already owns the drawdown
  metric and the ``NAV_SERIES`` / ``MARK_SERIES`` row shapes
  (``{observed_at, available_at, total_nav}`` and
  ``{observed_at, available_at, market, price}``). The benchmark series is
  emitted in exactly that ``NAV_SERIES`` shape
  (``nav_series_for_counterfactual``) so it can be fed straight into the
  existing metric instead of growing a second one here.
- ``private_evidence/virtual_portfolio_performance_query.py`` (P7-19, private
  repo, read-only) is the P&L/cumulative-return/max-drawdown engine for the
  older Master Virtual Portfolio. This module deliberately mirrors its three
  refusals — query time versus data time, observed versus estimated (no
  interpolation of a missing period), and a trusted self-hash rather than a
  hash rewritten inside the request — and stays in the same read-only,
  no-new-ledger posture, so the two compose instead of competing. It computes
  no P&L of its own for the PAPER account: the account's NAV series is an
  input.

The definition of "simply holding"
----------------------------------
``config/paper_benchmark_nav_series_policy.json`` holds it, with its sources
and the alternative that was rejected. In one line: at the account's first
fill instant, buy the market's single benchmark asset (crypto: ``KRW-BTC``,
because the card says 비트코인 보유) with the market's ratified base allocation
of NAV0, pay the same fee rate and the same realized entry slippage the PAPER
engine itself charged on that fill, and then never touch it again.

Two notional bases, four series, one anchor
-------------------------------------------
The account's own crypto sleeve is ``NAV0 x 0.15 x state_multiplier``, so a flat
base-share benchmark and an exposure-matched one answer different questions.
Both are therefore emitted from the same anchor (``FLAT_BASE_SHARE`` and
``MULTIPLIER_MATCHED``, each marked ``EXPOSURE_MATCHED`` and ``ASSET_ONLY``):
the flat share is a yardstick that owes nothing to our own regime call, which is
what makes stop rule 1 honest; the multiplier-matched one compares drawdowns at
equal exposure, which is what makes stop rule 5 honest (on the flat share alone,
rule 5 is nearly unfailable whenever the account entered below multiplier 1.00).
The mapping lives in the policy's ``declared_stop_rule_binding``. As of
2026-09-18 it is RATIFIED by the user's own record
(``evidence/authority/USER_RATIFICATION_BENCHMARK_NOTIONAL_BASIS_20260918.json``,
sha256 ``ae04aea2…``, verified by hash in :func:`load_policy` rather than
trusted as a string) and is copied into every anchor and series record, so it is
fixed before the first fill rather than chosen at day 30. Ratifying the binding
is *not* authority to publish a verdict off it: ``verdict_authorized`` stays
false and every verdict stays ``NOT_EMITTED_RATIFICATION_REQUIRED`` until
``verdict_authorized_blocked_on`` is empty. In particular
``RATIFICATION_LEDGER_ATTESTATION`` and ``RATIFICATION_CLOCK_ATTESTATION`` were
disclosed and deliberately left open by that record. A first fill can only
happen in RISK_ON or NEUTRAL, so an UNKNOWN (or RISK_OFF / STRESS) state at the
anchoring fill refuses outright -- UNKNOWN's ratified multiplier is a sentence,
not a number, and taking 0.50 from it would be inventing a size. The rejected alternative — equal-weighting the same candidates the
system bought — is not "simply holding": it borrows the system's own
selection, and its candidate set is not even known at the anchor instant, so
it could not be anchored once. **Both the definition and which of the two
emitted variants binds stop rules 1 and 5 need the user's sentence**; the
policy lists every such item under ``ratification_required`` and this module
emits no stop-rule verdict.

The anchor cannot be chosen later
---------------------------------
0. The **ledger must be authenticated, not merely hash-consistent.** Anyone can
   build a self-consistent ledger dict, so ``derive_anchor`` refuses one:
   :func:`authenticate_ledger` recovers the ledger from its own published,
   content-addressed, append-only snapshot store (which refuses two histories
   of the same length, and any history that is not a prefix-extension of every
   shorter one) and binds it to a genesis pin — ledger_id, the ACCOUNT_OPENED
   event's own hash, its ``event_at`` and the initial cash — established once
   when the account was opened, before any fill existed, and supplied
   separately. What that still does not prove is that a fill is genuine:
   ``RATIFICATION_LEDGER_ATTESTATION``.
1. ``anchor_utc`` is *derived* from the first ``FILL_APPLIED`` event of the
   validated ledger. A caller-supplied value is only ever compared, never
   used; a mismatch refuses. There is no parameter that moves it.
2. The anchor price must be an observation with
   ``observed_at <= anchor_utc`` and ``anchor_utc - observed_at`` within the
   ratified orderbook staleness window. A price observed after the fill can
   never anchor.
3. Exactly one eligible observation may be supplied. Two refuse as
   ``ANCHOR_PRICE_AMBIGUOUS`` — the later one is not preferred, and no
   average is taken.
4. The record must be written within one decision cycle of the fill
   (``recorded_at_utc - anchor_utc``). This is the anti-hindsight teeth: a
   month later, when it is known how the asset moved, the anchor simply
   cannot be created any more. Re-anchoring then needs a separate user
   decision, not a retry. Because that deadline must not rest on the caller's
   own claim about the time, ``recorded_at_utc`` is checked against evidence
   the caller did not author: a **clock witness**, a post-fill observation of
   the benchmark market carrying its own source sha256. The record time may
   not precede the witness's ``available_at``, may not sit further from it
   than the ratified staleness window, and (when the fill is already in the
   past by the process clock) may not be in the future. This bounds the claim;
   it does not prove it — ``clock_basis`` says so, and
   ``RATIFICATION_CLOCK_ATTESTATION`` asks for a runtime-attested write time.
5. ``record_anchor`` is write-once, and **the pointer file is not the
   binding.** Deleting the pointer and rerunning inside the lag window used to
   let a new digest bind; now :func:`bound_digests` reads the binding back out
   of two places that are never rewritten — the append-only
   ``bindings/<digest>.json`` markers and the content-addressed
   ``<digest>/anchor.json`` records — and any differing prior digest raises
   ``ANCHOR_ALREADY_BOUND_IMMUTABLE``. Re-recording byte-identical bytes stays
   an idempotent no-op, a missing pointer is recoverable rather than fatal, and
   ``load_anchor`` returns the record only against a separately supplied
   trusted sha256 — a hash rewritten inside the file is not a trust anchor.

Fail closed
-----------
A missing benchmark mark at a sample, a mark that is not on the PAPER NAV
sampling grid, a sample gap wider than the market allows, a null NAV or mark,
an ambiguous anchor, a fee rate that does not reconcile against the fill that
supposedly charged it — every one of these refuses. Nothing is interpolated,
carried forward, resampled or averaged.

Scope, authority and wiring
---------------------------
Crypto (Upbit KRW) is the only market defined; KR and US are shaped in the
policy but ``NOT_DEFINED`` pending the instrument question (an index level is
not something a person could have bought). Every authority field is False.
This module is evidence only: it opens no network, credential, order,
allocation, exit or trading path, is imported by no briefing, decision or
execution path, and is invoked by **no** schedule or workflow in this repo.

Its one caller is the private crypto PAPER runtime, which derives the anchor
immediately after its own restart-verified ledger write
(``private_evidence/crypto_paper_benchmark_anchor.py``, called from
``crypto_paper_natural_runtime.execute_observation``). That caller is
structurally unable to let this module affect a fill or a ledger write: it runs
after both are durable, it is wrapped so that no failure here can propagate, and
its only side effect is appending evidence. When an anchor cannot be derived the
fill still stands and the reason is written to an append-only
``crypto_paper_benchmark_anchor_attempts`` record plus a journal line, so a lost
anchor is never silent. Turning that wiring on in production still needs the
public runtime pin bumped to a commit containing this file
(``RATIFICATION_ACTIVATION``).
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_EVEN, localcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "paper_benchmark_nav_series_policy.json"
REGISTRY_PATH = ROOT / "config" / "rule_registry_v1.json"
SIMULATOR_PATH = ROOT / "shadow" / "crypto_paper_simulator.py"

_SIM_SPEC = importlib.util.spec_from_file_location(
    "paper_benchmark_crypto_paper_simulator", SIMULATOR_PATH
)
SIMULATOR = importlib.util.module_from_spec(_SIM_SPEC)
assert _SIM_SPEC.loader is not None
_SIM_SPEC.loader.exec_module(SIMULATOR)

POLICY_SCHEMA_VERSION = "paper_benchmark_nav_series_policy/1"
ANCHOR_SCHEMA_VERSION = "paper_benchmark_anchor/1"
SERIES_SCHEMA_VERSION = "paper_benchmark_nav_series/1"
DEFINITION_ID = "SINGLE_ASSET_BUY_AND_HOLD_AT_FIRST_FILL.V1"

POINTER_FILENAME = "ANCHOR_RECORDED.json"
ANCHOR_FILENAME = "anchor.json"
BINDINGS_DIRNAME = "bindings"

MARKING_EXPOSURE_MATCHED = "EXPOSURE_MATCHED"
MARKING_ASSET_ONLY = "ASSET_ONLY"
MARKINGS = (MARKING_EXPOSURE_MATCHED, MARKING_ASSET_ONLY)

NOTIONAL_BASIS_FLAT = "FLAT_BASE_SHARE"
NOTIONAL_BASIS_MULTIPLIER = "MULTIPLIER_MATCHED"
NOTIONAL_BASES = (NOTIONAL_BASIS_FLAT, NOTIONAL_BASIS_MULTIPLIER)

# Four named series from ONE anchor: {notional basis} x {marking}. Emitting both
# bases forecloses nothing and removes the only reason to re-anchor later.
SERIES_NAMES = tuple(
    f"{basis}__{marking}" for basis in NOTIONAL_BASES for marking in MARKINGS
)

STOP_RULE_1 = "CHECKPOINT_B_STOP_RULE_1"
STOP_RULE_5 = "CHECKPOINT_B_STOP_RULE_5"
DECLARED_BINDING_SERIES = {
    STOP_RULE_1: f"{NOTIONAL_BASIS_FLAT}__{MARKING_EXPOSURE_MATCHED}",
    STOP_RULE_5: f"{NOTIONAL_BASIS_MULTIPLIER}__{MARKING_EXPOSURE_MATCHED}",
}
# Ratified by the user 2026-09-18 (record
# evidence/authority/USER_RATIFICATION_BENCHMARK_NOTIONAL_BASIS_20260918.json,
# sha256 ae04aea2...). The *binding* is now fixed; emitting a stop-rule
# *verdict* is a separate authority that is still withheld, which is why
# ``verdict_authorized`` must still be false and every verdict is still
# VERDICT_NOT_EMITTED.
DECLARED_BINDING_STATUS = "RATIFIED"
DECLARED_BINDING_RATIFICATION_SOURCE = "benchmark_notional_basis_ratification"
DECLARED_BINDING_RATIFICATION_FIELDS = (
    "record_type", "item", "source_document", "repo_path", "sha256",
    "ratified_at_utc", "ratified_at_kst", "verbatim_ko", "also_ratified",
)
# Disclosed and deliberately NOT closed by the 2026-09-18 record. These stay in
# ratification_required and keep verdict_authorized gated.
DECLARED_BINDING_OPEN_RESIDUALS = (
    "RATIFICATION_LEDGER_ATTESTATION",
    "RATIFICATION_CLOCK_ATTESTATION",
)

MARKET_STATE_OBSERVATION_FIELDS = (
    "state", "multiplier", "observed_at", "available_at",
    "source_ref", "source_sha256", "source_schema_version",
)
MARKET_STATE_UNKNOWN = "UNKNOWN"
NEW_BUYS_PERMITTED = ("PERMIT", "PERMIT_SELECTIVE")

SECONDS_PER_DAY = 86400
# One crypto decision cycle. RULE.EXEC.TIME_CONTRACT.V1 ratifies a 07:00Z
# daily crypto decision cycle; "daily" is what makes this 86400, and using
# one cycle as the anchor write deadline is the CIO interpretation named in
# the policy's ratification_required (RATIFICATION_ANCHOR_WRITE_DEADLINE).
DAILY_DECISION_CYCLE_SECONDS = SECONDS_PER_DAY

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DIGEST_DIR_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")

VERDICT_NOT_EMITTED = "NOT_EMITTED_RATIFICATION_REQUIRED"

AUTHORITY = {
    "evidence_only": True,
    "benchmark_record_only": True,
    "allocation_authorized": False,
    "order_authorized": False,
    "paper_order_authorized": False,
    "paper_exit_authorized": False,
    "exchange_order_authorized": False,
    "stop_rule_verdict_authorized": False,
    "strategy_change_authorized": False,
    "risk_threshold_change_authorized": False,
    "network_access_authorized": False,
    "credential_access_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "real_capital_authorized": False,
}

DEFINITIONS = {
    "benchmark_definition_id": DEFINITION_ID,
    "units": (
        "quantity of the benchmark asset bought once at the anchor and never changed; "
        "every *_krw / price / nav field is canonical decimal text, every *_fraction "
        'field is exact decimal text ("-0.05" = -5%)'
    ),
    "series_names": (
        "{notional basis}__{marking}, four series from ONE anchor. Notional bases: "
        "FLAT_BASE_SHARE = NAV0 x the ratified base share at multiplier 1.00; "
        "MULTIPLIER_MATCHED = that share x the per-market state multiplier that "
        "applied at the anchoring fill. Both are sized off the same effective "
        "entry price, fee rate and instant, so the pair cannot be cherry-picked "
        "from two anchors."
    ),
    "declared_stop_rule_binding": (
        "which series is intended to judge which stop rule -- rule 1 on "
        "FLAT_BASE_SHARE__EXPOSURE_MATCHED, rule 5 on "
        "MULTIPLIER_MATCHED__EXPOSURE_MATCHED. RATIFIED 2026-09-18 by the user's "
        "own record (verified by sha256 at policy load): recorded from the anchor "
        "onward so it cannot be chosen at day 30 to suit the result. Ratifying "
        "the binding is not authority to publish a verdict off it -- verdicts "
        "stay NOT_EMITTED_RATIFICATION_REQUIRED while verdict_authorized is false."
    ),
    "EXPOSURE_MATCHED_total_nav": (
        "nav0_krw - anchor_cash_spent_krw + units x mark price. Comparable one-for-one "
        "with the PAPER account's own total_nav: both start at NAV0 and both leave the "
        "undeployed remainder in cash."
    ),
    "ASSET_ONLY_total_nav": (
        "units x mark price. The benchmark asset sleeve alone; its return basis is the "
        "notional committed at the anchor, so the entry cost drag is included."
    ),
    "max_drawdown_fraction": (
        "min over the observed samples of (nav_i - running_peak_i) / running_peak_i, the "
        "running peak taken over samples 0..i inclusive. A statement about the observed "
        "samples only -- never about any instant between two of them, and never "
        "extrapolated before the first or after the last."
    ),
    "series_points": (
        "exactly one point per supplied PAPER NAV sample, at that sample's own "
        "observed_at, priced by the benchmark mark carrying the identical observed_at -- "
        "never interpolated, resampled, averaged or carried forward"
    ),
    "exit_cost_treatment": (
        "not charged. An open PAPER position is marked at the mark price with no "
        "hypothetical liquidation cost deducted, so the held benchmark quantity is "
        "marked the same way (policy cost_model.exit_cost_treatment)."
    ),
    "generated_at_versus_observed_at": (
        "generated_at is when this query ran; every sample keeps its own observed_at and "
        "available_at. The two are never blurred."
    ),
    "verdict": (
        "no stop-rule verdict is emitted. Which variant binds stop rules 1 and 5 is "
        "RATIFICATION_VARIANT_BINDING in the policy; this record carries the inputs only."
    ),
}


class PaperBenchmarkNavSeriesError(ValueError):
    """Fail-closed benchmark anchor / series violation."""


def fail(code: str):
    raise PaperBenchmarkNavSeriesError(code)


# ─────────────────────────────────────────────────────────────────────────
# Small self-contained primitives. Deliberately not imported from the
# simulator or the counterfactual module (CIO copy-preserve convention per
# run_all.py: each evidence module stays independently replayable without a
# cross-module edit surface). Only ledger validation is imported, because
# reimplementing a hash chain check would be the duplication that matters.
# ─────────────────────────────────────────────────────────────────────────

def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"JSON_READ_FAILED:{path}:{exc}")


def _format_decimal(value: Decimal) -> str:
    if not value.is_finite():
        fail("DECIMAL_NON_FINITE")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _floor(value: Decimal, scale: int) -> Decimal:
    quantum = Decimal(1).scaleb(-scale)
    with localcontext() as ctx:
        ctx.prec = max(60, len(value.as_tuple().digits) + scale + 10)
        return value.quantize(quantum, rounding=ROUND_DOWN)


def _round_half_even(value: Decimal, scale: int) -> Decimal:
    quantum = Decimal(1).scaleb(-scale)
    with localcontext() as ctx:
        ctx.prec = max(60, len(value.as_tuple().digits) + scale + 10)
        return value.quantize(quantum, rounding=ROUND_HALF_EVEN)


def _divide(numerator: Decimal, denominator: Decimal, scale: int) -> Decimal:
    if denominator == 0:
        fail("DIVISION_BY_ZERO")
    with localcontext() as ctx:
        ctx.prec = max(60, scale + 20)
        return _round_half_even(numerator / denominator, scale)


def _decimal(value, code: str, *, scale: int, positive: bool = False,
             allow_negative: bool = False) -> Decimal:
    if not isinstance(value, str):
        fail(code)
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        fail(code)
    if not parsed.is_finite():
        fail(code)
    if not allow_negative and parsed < 0:
        fail(code)
    if positive and parsed <= 0:
        fail(code)
    if value != _format_decimal(parsed):
        fail(f"{code}:NON_CANONICAL")
    if max(0, -parsed.as_tuple().exponent) > scale:
        fail(f"{code}:SCALE_EXCEEDED")
    return parsed


def _decimal_value(value, code: str, *, positive: bool = False) -> Decimal:
    """Parse decimal text by VALUE, without demanding canonical form.

    The ratified multiplier table writes "1.00" / "0.70", and a market-state
    observation is lifted verbatim from an artifact whose formatting this module
    does not control. Comparing those by value rather than by spelling is the
    point; everything this module itself emits still goes through
    ``_format_decimal``.
    """
    if not isinstance(value, str) or not value.strip():
        fail(code)
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        fail(code)
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        fail(code)
    return parsed


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        fail(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        fail(code)


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        fail(code)
    return value


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        fail(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        fail(code)
    return value


def _with_packet_sha(value: dict) -> dict:
    result = copy.deepcopy(value)
    result.pop("packet_sha256", None)
    result["packet_sha256"] = payload_sha256(result)
    return result


def _verify_packet_sha(value: dict, code: str) -> dict:
    digest = _sha(value.get("packet_sha256"), f"{code}_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256", None)
    if payload_sha256(unsigned) != digest:
        fail(f"{code}_SHA_MISMATCH")
    return value


# ─────────────────────────────────────────────────────────────────────────
# Policy + ratified-number resolution. Every number comes from a pinned
# record; nothing operational is a literal in this file.
# ─────────────────────────────────────────────────────────────────────────

POLICY_TOP_FIELDS = (
    "schema_version", "policy_id", "status", "description", "why_this_exists",
    "stop_rules_unblocked", "stop_rules_not_in_scope", "source_documents",
    "registry", "registry_parameters", "benchmark_definition",
    "declared_stop_rule_binding", "cost_model", "markets", "fail_closed",
    "ratification_required", "authority",
)


def load_policy(path: Path = POLICY_PATH) -> dict:
    value = _read_json(Path(path))
    if not isinstance(value, dict) or set(value) != set(POLICY_TOP_FIELDS):
        fail("POLICY_FIELDS_MISMATCH")
    if value.get("schema_version") != POLICY_SCHEMA_VERSION:
        fail("POLICY_SCHEMA_INVALID")
    if value.get("authority") != AUTHORITY:
        fail("POLICY_AUTHORITY_MISMATCH")
    definition = value.get("benchmark_definition")
    if not isinstance(definition, dict) or definition.get("definition_id") != DEFINITION_ID:
        fail("POLICY_DEFINITION_ID_MISMATCH")
    if set(definition.get("variants_emitted") or {}) != set(SERIES_NAMES):
        fail("POLICY_SERIES_NAMES_MISMATCH")
    if set(definition.get("notional_bases") or {}) != set(NOTIONAL_BASES):
        fail("POLICY_NOTIONAL_BASES_MISMATCH")
    binding = value.get("declared_stop_rule_binding")
    if not isinstance(binding, dict):
        fail("POLICY_DECLARED_BINDING_MISSING")
    if binding.get("status") != DECLARED_BINDING_STATUS:
        fail("POLICY_DECLARED_BINDING_STATUS_INVALID")
    if binding.get("verdict_authorized") is not False:
        # Ratifying WHICH curve judges WHICH rule is not authority to publish a
        # verdict off it. That stays withheld until every id in
        # verdict_authorized_blocked_on is closed.
        fail("POLICY_DECLARED_BINDING_VERDICT_AUTHORIZED")
    for rule_id, series_name in DECLARED_BINDING_SERIES.items():
        row = binding.get(rule_id)
        if not isinstance(row, dict) or row.get("series") != series_name:
            fail(f"POLICY_DECLARED_BINDING_SERIES_MISMATCH:{rule_id}")
    # A RATIFIED status is only as good as the record behind it, so the record
    # is resolved and hashed here rather than trusted as a string. An asserted
    # ratification with no verifiable record is refused outright.
    record = binding.get("ratification_record")
    if not isinstance(record, dict) or set(record) != set(
        DECLARED_BINDING_RATIFICATION_FIELDS
    ):
        fail("POLICY_DECLARED_BINDING_RATIFICATION_RECORD_FIELDS_MISMATCH")
    if record.get("record_type") != "USER_RATIFICATION":
        fail("POLICY_DECLARED_BINDING_RATIFICATION_RECORD_TYPE_INVALID")
    if record.get("source_document") != DECLARED_BINDING_RATIFICATION_SOURCE:
        fail("POLICY_DECLARED_BINDING_RATIFICATION_SOURCE_INVALID")
    source = (value.get("source_documents") or {}).get(
        DECLARED_BINDING_RATIFICATION_SOURCE
    )
    if not isinstance(source, dict):
        fail("POLICY_DECLARED_BINDING_RATIFICATION_SOURCE_MISSING")
    if source.get("repo_path") != record.get("repo_path"):
        fail("POLICY_DECLARED_BINDING_RATIFICATION_PATH_MISMATCH")
    if source.get("file_sha256") != record.get("sha256"):
        fail("POLICY_DECLARED_BINDING_RATIFICATION_SHA_MISMATCH")
    ratified = _pinned_source_value(value, DECLARED_BINDING_RATIFICATION_SOURCE)
    item = (ratified or {}).get(record.get("item")) if isinstance(ratified, dict) else None
    if not isinstance(item, dict) or item.get("status") != "RATIFIED":
        fail("POLICY_DECLARED_BINDING_RATIFICATION_ITEM_NOT_RATIFIED")
    bound = item.get("stop_rule_binding")
    if not isinstance(bound, dict):
        fail("POLICY_DECLARED_BINDING_RATIFICATION_ITEM_INVALID")
    for rule_id, series_name in DECLARED_BINDING_SERIES.items():
        row = bound.get(rule_id)
        if not isinstance(row, dict) or row.get("series") != series_name:
            # The policy may not claim a binding the user's own record does not
            # say. This is what makes the mapping unforgeable after the fact.
            fail(f"POLICY_DECLARED_BINDING_RATIFICATION_SERIES_MISMATCH:{rule_id}")
    # The two disclosed residuals are NOT closed by that record; a policy that
    # quietly marks them resolved is refused.
    residuals = binding.get("residual_weaknesses_still_open")
    if not isinstance(residuals, dict) or set(residuals) != set(
        DECLARED_BINDING_OPEN_RESIDUALS
    ):
        fail("POLICY_DECLARED_BINDING_RESIDUALS_MISMATCH")
    blocked = binding.get("verdict_authorized_blocked_on")
    if not isinstance(blocked, list) or not blocked:
        fail("POLICY_DECLARED_BINDING_BLOCKED_ON_EMPTY")
    for residual in DECLARED_BINDING_OPEN_RESIDUALS:
        if str(residuals.get(residual, "")).split()[0:1] != ["DISCLOSED_NOT_RESOLVED"]:
            fail(f"POLICY_DECLARED_BINDING_RESIDUAL_CLOSED:{residual}")
        if residual not in blocked:
            fail(f"POLICY_DECLARED_BINDING_RESIDUAL_NOT_BLOCKING:{residual}")
    if not isinstance(value.get("ratification_required"), list) or not value["ratification_required"]:
        fail("POLICY_RATIFICATION_LIST_EMPTY")
    return value


def _registry_parameter(registry: dict, rule_id: str, key: str):
    rules = registry.get("rules")
    if not isinstance(rules, list):
        fail("REGISTRY_RULES_MISSING")
    for row in rules:
        if isinstance(row, dict) and row.get("rule_id") == rule_id:
            if row.get("status") != "RATIFIED":
                fail(f"REGISTRY_RULE_NOT_RATIFIED:{rule_id}")
            parameters = row.get("key_parameters")
            if not isinstance(parameters, dict) or key not in parameters:
                fail(f"REGISTRY_PARAMETER_MISSING:{rule_id}:{key}")
            return copy.deepcopy(parameters[key].get("value"))
    fail(f"REGISTRY_RULE_MISSING:{rule_id}")


def _pinned_source_value(policy: dict, name: str):
    """Read one value out of a policy-pinned repo file, verifying its sha256."""
    source = (policy.get("source_documents") or {}).get(name)
    if not isinstance(source, dict):
        fail(f"POLICY_SOURCE_MISSING:{name}")
    path = ROOT / _text(source.get("repo_path"), f"POLICY_SOURCE_PATH_INVALID:{name}")
    expected = _sha(source.get("file_sha256"), f"POLICY_SOURCE_SHA_INVALID:{name}")
    if not path.is_file():
        fail(f"POLICY_SOURCE_FILE_MISSING:{name}")
    if file_sha256(path) != expected:
        fail(f"POLICY_SOURCE_FILE_SHA_MISMATCH:{name}")
    document = _read_json(path)
    key = _text(source.get("read_key"), f"POLICY_SOURCE_KEY_INVALID:{name}")
    if not isinstance(document, dict) or key not in document:
        fail(f"POLICY_SOURCE_KEY_ABSENT:{name}:{key}")
    return document[key]


def resolve_market_parameters(
    market: str, *, policy: dict | None = None, registry: dict | None = None,
) -> dict:
    """Resolve every operational number for one market from pinned records."""
    policy = load_policy() if policy is None else policy
    registry = _read_json(REGISTRY_PATH) if registry is None else registry
    markets = policy.get("markets")
    if not isinstance(markets, dict) or market not in markets:
        fail(f"MARKET_UNKNOWN:{market}")
    row = markets[market]
    if row.get("status") != "READY" or row.get("benchmark_asset") is None:
        fail(f"MARKET_NOT_DEFINED:{market}:{row.get('blocked_on')}")

    contract = SIMULATOR.load_contract()
    scale = int(contract["decimal_scale"])

    base_allocation = _registry_parameter(
        registry, "RULE.ALLOCATION.V2", "base_allocation_all_markets_risk_on"
    )
    if not isinstance(base_allocation, dict) or market not in base_allocation:
        fail(f"BASE_ALLOCATION_MISSING:{market}")
    base_share = _decimal_value(
        base_allocation[market], f"BASE_ALLOCATION_INVALID:{market}", positive=True,
    )

    gap_days = _registry_parameter(
        registry, "RULE.ROTATION.MAX_OBSERVATION_GAP.V1", "max_gap_days"
    )
    if not isinstance(gap_days, dict) or gap_days.get("unit") != "CALENDAR_DAYS":
        fail("MAX_GAP_DAYS_SHAPE_INVALID")
    if not isinstance(gap_days.get(market), int) or gap_days[market] <= 0:
        fail(f"MAX_GAP_DAYS_MISSING:{market}")

    cycle = _registry_parameter(
        registry, "RULE.EXEC.TIME_CONTRACT.V1", "crypto_decision_cycle"
    )
    if not isinstance(cycle, dict) or not isinstance(cycle.get("decision_time_utc"), str):
        fail("CRYPTO_DECISION_CYCLE_SHAPE_INVALID")

    staleness = _pinned_source_value(policy, "upbit_market_evidence_policy_ratified")
    if not isinstance(staleness, int) or staleness <= 0:
        fail("ANCHOR_PRICE_STALENESS_INVALID")

    multipliers = _registry_parameter(
        registry, "RULE.ALLOCATION.V2", "per_market_state_multiplier_of_base"
    )
    if not isinstance(multipliers, dict) or MARKET_STATE_UNKNOWN not in multipliers:
        fail("STATE_MULTIPLIER_TABLE_INVALID")
    new_buys = _registry_parameter(
        registry, "RULE.ALLOCATION.V2", "new_buys_by_market_state"
    )
    if not isinstance(new_buys, dict) or set(new_buys) != set(multipliers):
        fail("NEW_BUYS_TABLE_INVALID")

    genesis_krw = _registry_parameter(
        registry, "RULE.CRYPTO.PAPER_V2_LEDGER_GENESIS.V1", "initial_cash_krw"
    )
    if not isinstance(genesis_krw, int) or genesis_krw <= 0:
        fail("LEDGER_GENESIS_KRW_INVALID")

    return {
        "market": market,
        "currency": _text(row.get("currency"), "MARKET_CURRENCY_INVALID"),
        "benchmark_asset": _text(row.get("benchmark_asset"), "BENCHMARK_ASSET_INVALID"),
        "benchmark_asset_basis": _text(
            row.get("benchmark_asset_basis"), "BENCHMARK_ASSET_BASIS_INVALID"
        ),
        "base_allocation_fraction": base_share,
        "max_sample_gap_seconds": gap_days[market] * SECONDS_PER_DAY,
        "anchor_price_max_staleness_seconds": staleness,
        "anchor_record_max_lag_seconds": DAILY_DECISION_CYCLE_SECONDS,
        "anchor_clock_witness_max_skew_seconds": staleness,
        "state_multipliers": multipliers,
        "new_buys_by_state": new_buys,
        "market_state_max_staleness_seconds": gap_days[market] * SECONDS_PER_DAY,
        "expected_ledger_genesis": genesis_krw,
        "decimal_scale": scale,
        "simulator_contract": contract,
    }


# ─────────────────────────────────────────────────────────────────────────
# Anchor derivation. Nothing here is chosen: anchor_utc comes off the first
# fill, the price comes off an observation that must already have existed at
# that instant, and the record has to be written before the next cycle.
# ─────────────────────────────────────────────────────────────────────────

PRICE_OBSERVATION_FIELDS = (
    "market", "price", "observed_at", "available_at", "source_ref", "source_sha256",
)

LEDGER_GENESIS_PIN_FIELDS = (
    "ledger_id", "genesis_event_sha256", "genesis_event_at", "initial_cash",
)


class VerifiedLedger:
    """A ledger recovered from its own published append-only snapshot store and
    bound to the genesis pin recorded when the account was opened.

    An internally hash-consistent dict proves only that somebody computed the
    hashes correctly -- anyone can build one. This wrapper is the only way a
    ledger reaches :func:`derive_anchor`, and it can only be produced by
    :func:`authenticate_ledger`.
    """

    __slots__ = ("_ledger", "_ledger_id", "_genesis_event_sha256",
                 "_snapshot_root", "_snapshot_names")

    def __init__(self, ledger, ledger_id, genesis_event_sha256, snapshot_root,
                 snapshot_names):
        self._ledger = copy.deepcopy(ledger)
        self._ledger_id = ledger_id
        self._genesis_event_sha256 = genesis_event_sha256
        self._snapshot_root = Path(snapshot_root)
        self._snapshot_names = tuple(snapshot_names)

    @property
    def ledger(self) -> dict:
        return copy.deepcopy(self._ledger)

    @property
    def ledger_id(self) -> str:
        return self._ledger_id

    @property
    def genesis_event_sha256(self) -> str:
        return self._genesis_event_sha256

    @property
    def snapshot_names(self) -> tuple:
        return self._snapshot_names


def _validate_genesis_pin(value) -> dict:
    if not isinstance(value, dict) or set(value) != set(LEDGER_GENESIS_PIN_FIELDS):
        fail("LEDGER_GENESIS_PIN_FIELDS_MISMATCH")
    _text(value.get("ledger_id"), "LEDGER_GENESIS_PIN_LEDGER_ID_INVALID")
    _sha(value.get("genesis_event_sha256"), "LEDGER_GENESIS_PIN_SHA_INVALID")
    _utc(value.get("genesis_event_at"), "LEDGER_GENESIS_PIN_EVENT_AT_INVALID")
    if not isinstance(value.get("initial_cash"), str) or not value["initial_cash"]:
        fail("LEDGER_GENESIS_PIN_INITIAL_CASH_INVALID")
    return copy.deepcopy(value)


def authenticate_ledger(
    snapshot_root: Path, ledger_id: str, *, genesis_pin: dict,
    contract: dict | None = None,
) -> VerifiedLedger:
    """Recover the ledger from its published snapshots and bind it to its genesis.

    Three things a caller cannot fabricate on its own are required to line up:

    1. The ledger must exist as **published, content-addressed, append-only
       snapshots**. ``SIMULATOR.recover_ledger`` validates every snapshot in the
       store, requires each snapshot's filename to match its own length and
       packet hash, refuses two different histories of the same length
       (``LEDGER_HISTORY_DIVERGED_AT_LENGTH``) and refuses any history that is
       not a strict prefix-extension of every shorter one
       (``LEDGER_HISTORY_DIVERGED``). So a fabricated fill cannot replace or
       contradict what the runtime already published -- it can only ever be an
       extension of the real prefix.
    2. The genesis pin is established **once, when the account is opened, before
       any fill exists**, and is supplied separately from the ledger (the same
       posture as ``trusted_anchor_sha256``). It fixes the ledger_id, the
       ACCOUNT_OPENED event's own hash, its ``event_at`` and the initial cash.
       Because every later event is chained to that event's hash, a fabricated
       history has to reproduce the genesis event byte-for-byte: it cannot
       change the account identity, the open time or NAV0.
    3. The head we actually read has to be one of the files in that store, by
       name, so the anchor's lineage names a snapshot that can be re-read.

    What this does NOT prove: that the *fill itself* is genuine. A party who can
    already write into the runtime's snapshot store and mint a genesis pin can
    still publish a fabricated extension. That residual is a runtime-side
    signing/attestation question, recorded as
    ``RATIFICATION_LEDGER_ATTESTATION`` in the policy -- not something an
    offline reader can close.
    """
    contract = SIMULATOR.load_contract() if contract is None else contract
    pin = _validate_genesis_pin(genesis_pin)
    _text(ledger_id, "LEDGER_ID_INVALID")
    if pin["ledger_id"] != ledger_id:
        fail("LEDGER_PIN_LEDGER_ID_MISMATCH")

    root = Path(snapshot_root)
    ledger_dir = root / ledger_id
    if not ledger_dir.is_dir():
        fail("LEDGER_SNAPSHOT_STORE_MISSING")
    names = tuple(sorted(path.name for path in ledger_dir.glob("*.json")))
    if not names:
        fail("LEDGER_SNAPSHOT_STORE_EMPTY")

    checked = SIMULATOR.recover_ledger(root, ledger_id, contract)
    if checked["ledger_id"] != ledger_id:
        fail("LEDGER_ID_MISMATCH")
    head_name = f"{len(checked['events']):08d}-{checked['packet_sha256']}.json"
    if head_name not in names:
        fail("LEDGER_HEAD_SNAPSHOT_NOT_IN_STORE")

    genesis = checked["events"][0]
    if (
        genesis.get("event_type") != "ACCOUNT_OPENED"
        or genesis.get("previous_event_sha256") is not None
        or genesis.get("sequence") != 1
    ):
        fail("LEDGER_GENESIS_EVENT_INVALID")
    if genesis["event_sha256"] != pin["genesis_event_sha256"]:
        fail("LEDGER_PIN_GENESIS_SHA_MISMATCH")
    if genesis["event_at"] != pin["genesis_event_at"]:
        fail("LEDGER_PIN_GENESIS_EVENT_AT_MISMATCH")
    if genesis["payload"].get("initial_cash") != pin["initial_cash"]:
        fail("LEDGER_PIN_INITIAL_CASH_MISMATCH")
    return VerifiedLedger(checked, ledger_id, genesis["event_sha256"], root, names)


def _validate_price_observation(row, index: int, scale: int) -> dict:
    if not isinstance(row, dict) or set(row) != set(PRICE_OBSERVATION_FIELDS):
        fail(f"PRICE_OBSERVATION_FIELDS_MISMATCH:{index}")
    _text(row.get("market"), f"PRICE_OBSERVATION_MARKET_INVALID:{index}")
    _decimal(
        row.get("price"), f"PRICE_OBSERVATION_PRICE_INVALID:{index}",
        scale=scale, positive=True,
    )
    observed = _utc(row.get("observed_at"), f"PRICE_OBSERVATION_OBSERVED_AT_INVALID:{index}")
    available = _utc(row.get("available_at"), f"PRICE_OBSERVATION_AVAILABLE_AT_INVALID:{index}")
    if observed > available:
        fail(f"PRICE_OBSERVATION_AVAILABLE_BEFORE_OBSERVED:{index}")
    _text(row.get("source_ref"), f"PRICE_OBSERVATION_SOURCE_REF_INVALID:{index}")
    _sha(row.get("source_sha256"), f"PRICE_OBSERVATION_SOURCE_SHA_INVALID:{index}")
    return copy.deepcopy(row)


def _first_fill(ledger: dict) -> tuple[dict, dict]:
    """The ledger's first FILL_APPLIED event and the intent that produced it."""
    intents: dict[str, dict] = {}
    for event in ledger["events"]:
        if event["event_type"] == "ORDER_SUBMITTED":
            intents[event["order_id"]] = event["payload"]["intent"]
        elif event["event_type"] == "FILL_APPLIED":
            intent = intents.get(event["order_id"])
            if intent is None:
                fail("ANCHOR_FILL_INTENT_MISSING")
            return copy.deepcopy(event), copy.deepcopy(intent)
    fail("ANCHOR_NO_FILL_YET")


def read_market_state(
    observation, *, params: dict, anchor_utc: dt.datetime, recorded_at: dt.datetime,
) -> dict:
    """The **single** point at which the market state enters this module.

    This module deliberately opens **no path of its own**. The caller supplies a
    ``market_state_observation`` lifted verbatim from whatever artifact the
    crypto state-multiplier wiring reads, so that there is one state source in
    the system rather than two that can disagree.

    Contract (fields, all required, all validated here):
      ``state``                  one of the five ratified states
      ``multiplier``             must EQUAL the ratified multiplier for that state
      ``observed_at``            when the state was observed (<= anchor instant
                                 is not required; a state confirmed shortly after
                                 the fill is still the state that sized it, but it
                                 may never be later than the record time)
      ``available_at``           when it became readable (<= recorded_at)
      ``source_ref`` / ``source_sha256`` / ``source_schema_version``
                                 the artifact it came from, recorded into the
                                 anchor so the source is auditable even though
                                 this module does not choose it

    Verified 2026-09-18 (why no path is bound here):
      * ``universe/crypto_paper_buy_eligibility.py`` contains no market-state or
        state-multiplier reference at all, so the wiring being built has not yet
        settled on an artifact this module could match.
      * ``portfolio/paper_allocation_envelope.effective_market_state`` — the
        canonical consumer of the ratified multiplier table — also takes
        ``confirmed_state`` as a caller-supplied string and opens no path.
      * ``regime/paper_regime_reference.py`` →
        ``data/latest_paper_regime_reference.json`` does emit exactly the five
        ratified states and is already the Stage-1 rotation input, so it is the
        likely candidate — but its own header says it cannot authorize a capital
        action, and it is the producer that began failing 2026-09-18 with
        ``REFERENCE_FROZEN_NORMALIZATION_BINDING_MISSING``
        (``regime/paper_regime_reference.py:849``). Binding it is a governance
        decision: ``RATIFICATION_MARKET_STATE_SOURCE_BINDING``.
    """
    if observation is None:
        fail("ANCHOR_MARKET_STATE_UNAVAILABLE")
    if not isinstance(observation, dict) or set(observation) != set(
        MARKET_STATE_OBSERVATION_FIELDS
    ):
        fail("ANCHOR_MARKET_STATE_FIELDS_MISMATCH")
    state = observation.get("state")
    if not isinstance(state, str) or state not in params["state_multipliers"]:
        fail("ANCHOR_MARKET_STATE_NOT_RATIFIED")
    _text(observation.get("source_ref"), "ANCHOR_MARKET_STATE_SOURCE_REF_INVALID")
    _sha(observation.get("source_sha256"), "ANCHOR_MARKET_STATE_SOURCE_SHA_INVALID")
    _text(
        observation.get("source_schema_version"),
        "ANCHOR_MARKET_STATE_SOURCE_SCHEMA_INVALID",
    )
    observed = _utc(
        observation.get("observed_at"), "ANCHOR_MARKET_STATE_OBSERVED_AT_INVALID"
    )
    available = _utc(
        observation.get("available_at"), "ANCHOR_MARKET_STATE_AVAILABLE_AT_INVALID"
    )
    if observed > available:
        fail("ANCHOR_MARKET_STATE_AVAILABLE_BEFORE_OBSERVED")
    if available > recorded_at:
        fail("ANCHOR_MARKET_STATE_FROM_FUTURE")

    permission = params["new_buys_by_state"][state]
    if state == MARKET_STATE_UNKNOWN:
        # UNKNOWN holds current exposure and permits no new buys, so it can
        # never be the state of a FIRST fill. Its ratified multiplier is not
        # even a number ("hold_current_up_to_0.50_no_new_buys"), and taking
        # 0.50 from that sentence would be inventing a size.
        fail("ANCHOR_MARKET_STATE_UNKNOWN_REFUSED")
    if permission not in NEW_BUYS_PERMITTED:
        fail(f"ANCHOR_MARKET_STATE_FORBIDS_NEW_BUYS:{state}")

    # Staleness is measured from the anchoring fill, not from "now": the
    # question is whether the state that sized this fill was fresh.
    age = int(abs((anchor_utc - observed).total_seconds()))
    if age > params["market_state_max_staleness_seconds"]:
        fail("ANCHOR_MARKET_STATE_STALE")

    expected_text = params["state_multipliers"][state]
    if not isinstance(expected_text, str):
        fail(f"ANCHOR_MARKET_STATE_MULTIPLIER_NOT_NUMERIC:{state}")
    try:
        expected = Decimal(expected_text)
    except (InvalidOperation, ValueError):
        # A state whose ratified multiplier is a sentence rather than a number
        # can never size anything here.
        fail(f"ANCHOR_MARKET_STATE_MULTIPLIER_NOT_NUMERIC:{state}")
    multiplier = _decimal_value(
        observation.get("multiplier"), "ANCHOR_MARKET_STATE_MULTIPLIER_INVALID",
        positive=True,
    )
    if multiplier != expected:
        fail(f"ANCHOR_MARKET_STATE_MULTIPLIER_NOT_RATIFIED:{state}")
    return {
        "observation": copy.deepcopy(observation),
        "state": state,
        "multiplier": multiplier,
        "new_buys": permission,
        "age_seconds_from_fill": age,
        "source_binding": "NOT_BOUND_RATIFICATION_MARKET_STATE_SOURCE_BINDING",
    }


def _size_at_anchor(
    *, basis: str, nav0: Decimal, share: Decimal, effective_price: Decimal,
    fee_rate: Decimal, scale: int,
) -> dict:
    """One (notional, units, cash) triple. Both bases share one effective price,
    one fee rate and one anchor instant -- only the share differs."""
    notional = _floor(nav0 * share, scale)
    if notional <= 0:
        fail(f"ANCHOR_NOTIONAL_NOT_POSITIVE:{basis}")
    units = _floor(
        _divide(notional, effective_price * (Decimal(1) + fee_rate), scale + 10), scale
    )
    if units <= 0:
        fail(f"ANCHOR_UNITS_NOT_POSITIVE:{basis}")
    gross = _floor(units * effective_price, scale)
    fee = _floor(gross * fee_rate, scale)
    cash_spent = gross + fee
    if cash_spent > notional:
        fail(f"ANCHOR_CASH_SPENT_EXCEEDS_NOTIONAL:{basis}")
    return {
        "notional_basis": basis,
        "share_of_nav0": _format_decimal(share),
        "notional_krw": _format_decimal(notional),
        "units": _format_decimal(units),
        "anchor_gross_krw": _format_decimal(gross),
        "anchor_fee_krw": _format_decimal(fee),
        "anchor_cash_spent_krw": _format_decimal(cash_spent),
        "anchor_residual_cash_krw": _format_decimal(notional - cash_spent),
    }


def derive_anchor(
    *, market: str, verified_ledger: VerifiedLedger, price_observations: list,
    recorded_at_utc: str, clock_witness: dict, market_state_observation,
    anchor_utc: str | None = None, policy: dict | None = None,
    params: dict | None = None, now=None,
) -> dict:
    """Derive the one immutable anchor for (market, ledger) from real data."""
    policy = load_policy() if policy is None else policy
    params = resolve_market_parameters(market, policy=policy) if params is None else params
    scale = params["decimal_scale"]

    if not isinstance(verified_ledger, VerifiedLedger):
        # An internally hash-consistent dict is not provenance: anyone can
        # build one. Only authenticate_ledger() can mint this wrapper.
        fail("LEDGER_NOT_AUTHENTICATED")
    checked = SIMULATOR.validate_ledger(
        verified_ledger.ledger, params["simulator_contract"]
    )
    ledger_id = _text(checked.get("ledger_id"), "LEDGER_ID_INVALID")
    if ledger_id != verified_ledger.ledger_id:
        fail("LEDGER_IDENTITY_DRIFTED_FROM_AUTHENTICATION")
    if checked.get("currency") != params["currency"]:
        fail("LEDGER_CURRENCY_MISMATCH")

    genesis = checked["events"][0]
    if genesis["event_type"] != "ACCOUNT_OPENED":
        fail("LEDGER_GENESIS_EVENT_INVALID")
    nav0 = _decimal(
        genesis["payload"]["initial_cash"], "LEDGER_GENESIS_CASH_INVALID",
        scale=scale, positive=True,
    )
    if nav0 != Decimal(params["expected_ledger_genesis"]):
        fail("LEDGER_GENESIS_NOT_RATIFIED_NAV0")

    fill_event, intent = _first_fill(checked)
    fill = fill_event["payload"]
    if intent.get("side") != "BUY":
        fail("ANCHOR_FIRST_FILL_NOT_BUY")

    derived_anchor_utc = _utc(fill_event.get("event_at"), "ANCHOR_FILL_EVENT_AT_INVALID")
    if anchor_utc is not None and anchor_utc != fill_event["event_at"]:
        # The whole point: a supplied anchor time is only ever checked.
        fail("ANCHOR_UTC_NOT_DERIVED_FROM_FIRST_FILL")
    anchor_utc_text = fill_event["event_at"]

    fee_rate = _decimal(intent.get("fee_rate"), "ANCHOR_FEE_RATE_INVALID", scale=scale)
    gross = _decimal(fill.get("gross_value"), "ANCHOR_GROSS_VALUE_INVALID", scale=scale, positive=True)
    fee_amount = _decimal(fill.get("fee_amount"), "ANCHOR_FEE_AMOUNT_INVALID", scale=scale)
    if _floor(gross * fee_rate, scale) != fee_amount:
        # The fee rate is not a constant in this repo (the simulator contract's
        # cost_model is CALLER_SUPPLIED_FEE_RATE_AND_QUEUE_FRACTION_NO_DEFAULTS),
        # so it is only trustworthy if it reconciles against the fill that
        # supposedly charged it. Defence in depth: the simulator's own replay
        # re-derives the match and already refuses a ledger where these two
        # disagree, so this branch should be unreachable for a valid ledger.
        fail("ANCHOR_FEE_RATE_NOT_RECONCILED_WITH_FILL")
    entry_slippage_bps = _decimal(
        fill.get("realized_slippage_bps"), "ANCHOR_SLIPPAGE_BPS_INVALID", scale=scale,
    )

    recorded_at = _utc(recorded_at_utc, "ANCHOR_RECORDED_AT_INVALID")
    if recorded_at < derived_anchor_utc:
        fail("ANCHOR_RECORDED_BEFORE_FILL")
    lag = int((recorded_at - derived_anchor_utc).total_seconds())
    if lag > params["anchor_record_max_lag_seconds"]:
        fail("ANCHOR_RECORD_LAG_EXCEEDED")

    # ── the record-lag deadline above is the anti-hindsight guard, so it must
    # not rest on the caller's own claim about what time it is. recorded_at_utc
    # is checked against evidence the caller did not author: a post-fill
    # observation of the benchmark market, carrying its own source sha256 into
    # the append-only capture tree.
    witness = _validate_price_observation(clock_witness, "clock_witness", scale)
    if witness["market"] != params["benchmark_asset"]:
        fail("ANCHOR_CLOCK_WITNESS_MARKET_MISMATCH")
    witness_observed = _utc(
        witness["observed_at"], "ANCHOR_CLOCK_WITNESS_OBSERVED_AT_INVALID"
    )
    witness_available = _utc(
        witness["available_at"], "ANCHOR_CLOCK_WITNESS_AVAILABLE_AT_INVALID"
    )
    if witness_observed < derived_anchor_utc:
        # A pre-fill observation proves nothing about being alive at record
        # time -- it could have been held for a month.
        fail("ANCHOR_CLOCK_WITNESS_NOT_AFTER_FILL")
    if witness_available > recorded_at:
        fail("ANCHOR_RECORDED_AT_BEFORE_EVIDENCE")
    witness_skew = int((recorded_at - witness_available).total_seconds())
    if witness_skew > params["anchor_clock_witness_max_skew_seconds"]:
        # Claiming a record time long after the newest evidence in hand is the
        # shape of a hindsight write.
        fail("ANCHOR_CLOCK_WITNESS_SKEW_EXCEEDED")

    wall_now = dt.datetime.now(tz=dt.timezone.utc) if now is None else now
    if derived_anchor_utc <= wall_now and recorded_at > wall_now:
        # In production the fill is always already in the past, so a record
        # time in the future is a clock claim, not an observation. (For a
        # fixture anchored in the future this ceiling is inert by construction.)
        fail("ANCHOR_RECORDED_AT_IN_FUTURE")

    if not isinstance(price_observations, list) or not price_observations:
        fail("ANCHOR_PRICE_OBSERVATIONS_EMPTY")
    validated = [
        _validate_price_observation(row, index, scale)
        for index, row in enumerate(price_observations)
    ]
    eligible = []
    for row in validated:
        if row["market"] != params["benchmark_asset"]:
            continue
        observed = _utc(row["observed_at"], "ANCHOR_PRICE_OBSERVED_AT_INVALID")
        available = _utc(row["available_at"], "ANCHOR_PRICE_AVAILABLE_AT_INVALID")
        if observed > derived_anchor_utc:
            continue
        if int((derived_anchor_utc - observed).total_seconds()) > params[
            "anchor_price_max_staleness_seconds"
        ]:
            continue
        if available > recorded_at:
            continue
        eligible.append(row)
    if not eligible:
        fail("ANCHOR_PRICE_UNAVAILABLE")
    if len(eligible) > 1:
        # Refuse rather than prefer the latest or average them.
        fail("ANCHOR_PRICE_AMBIGUOUS")
    observation = eligible[0]
    anchor_price = Decimal(observation["price"])

    market_state = read_market_state(
        market_state_observation, params=params,
        anchor_utc=derived_anchor_utc, recorded_at=recorded_at,
    )

    effective_price = _floor(
        anchor_price * (Decimal(1) + entry_slippage_bps / Decimal(10000)), scale
    )
    if effective_price <= 0:
        fail("ANCHOR_EFFECTIVE_PRICE_NOT_POSITIVE")
    base_share = params["base_allocation_fraction"]
    shares = {
        NOTIONAL_BASIS_FLAT: base_share,
        NOTIONAL_BASIS_MULTIPLIER: _floor(
            base_share * market_state["multiplier"], scale
        ),
    }
    notionals = {
        basis: _size_at_anchor(
            basis=basis, nav0=nav0, share=shares[basis],
            effective_price=effective_price, fee_rate=fee_rate, scale=scale,
        )
        for basis in NOTIONAL_BASES
    }

    record = {
        "schema_version": ANCHOR_SCHEMA_VERSION,
        "policy_id": policy["policy_id"],
        "benchmark_definition_id": DEFINITION_ID,
        "market": market,
        "currency": params["currency"],
        "ledger_id": ledger_id,
        "ledger_packet_sha256": checked["packet_sha256"],
        "ledger_provenance": {
            "authentication": "PUBLISHED_APPEND_ONLY_SNAPSHOT_STORE_PLUS_GENESIS_PIN",
            "genesis_event_sha256": verified_ledger.genesis_event_sha256,
            "genesis_event_at": checked["events"][0]["event_at"],
            "genesis_initial_cash": checked["events"][0]["payload"]["initial_cash"],
            "snapshot_count": len(verified_ledger.snapshot_names),
            "head_snapshot_name": (
                f"{len(checked['events']):08d}-{checked['packet_sha256']}.json"
            ),
            "residual": "RATIFICATION_LEDGER_ATTESTATION",
        },
        "anchor_utc": anchor_utc_text,
        "anchor_basis": "FIRST_FILL_APPLIED_EVENT_AT_DERIVED_NOT_SUPPLIED",
        "first_fill": {
            "order_id": fill_event["order_id"],
            "event_sha256": fill_event["event_sha256"],
            "previous_event_sha256": fill_event["previous_event_sha256"],
            "market": intent["market"],
            "filled_quantity": fill["filled_quantity"],
            "gross_value": fill["gross_value"],
            "fee_amount": fill["fee_amount"],
            "realized_slippage_bps": fill["realized_slippage_bps"],
        },
        "benchmark_asset": params["benchmark_asset"],
        "benchmark_asset_basis": params["benchmark_asset_basis"],
        "anchor_price": observation["price"],
        "anchor_price_observation": observation,
        "anchor_price_staleness_seconds": int((
            derived_anchor_utc
            - _utc(observation["observed_at"], "ANCHOR_PRICE_OBSERVED_AT_INVALID")
        ).total_seconds()),
        "nav0_krw": _format_decimal(nav0),
        "nav0_basis": "LEDGER_ACCOUNT_OPENED_INITIAL_CASH",
        "base_allocation_fraction": _format_decimal(base_share),
        "market_state": {
            "state": market_state["state"],
            "multiplier": _format_decimal(market_state["multiplier"]),
            "new_buys": market_state["new_buys"],
            "age_seconds_from_fill": market_state["age_seconds_from_fill"],
            "max_staleness_seconds": params["market_state_max_staleness_seconds"],
            "source_binding": market_state["source_binding"],
            "observation": market_state["observation"],
        },
        "notionals": notionals,
        "notional_bases_identical": (
            notionals[NOTIONAL_BASIS_FLAT]["units"]
            == notionals[NOTIONAL_BASIS_MULTIPLIER]["units"]
        ),
        "declared_stop_rule_binding": copy.deepcopy(
            policy["declared_stop_rule_binding"]
        ),
        "cost_model": {
            "fee_rate": _format_decimal(fee_rate),
            "fee_rate_source": "ANCHOR_FILL_ORDER_INTENT_RECONCILED_AGAINST_FILL",
            "entry_slippage_bps": _format_decimal(entry_slippage_bps),
            "entry_slippage_source": "ANCHOR_FILL_REALIZED_SLIPPAGE_BPS",
            "exit_cost_treatment": "NOT_CHARGED_MIRRORS_PAPER_NAV_OPEN_POSITION_MARKING",
        },
        "effective_entry_price": _format_decimal(effective_price),
        "max_sample_gap_seconds": params["max_sample_gap_seconds"],
        "anchor_price_max_staleness_seconds": params["anchor_price_max_staleness_seconds"],
        "anchor_record_max_lag_seconds": params["anchor_record_max_lag_seconds"],
        "recorded_at_utc": recorded_at_utc,
        "record_lag_seconds": lag,
        "clock_witness": witness,
        "clock_witness_skew_seconds": witness_skew,
        "clock_basis": "WITNESS_BOUNDED_NOT_PROVEN",
        "anchor_clock_witness_max_skew_seconds": params[
            "anchor_clock_witness_max_skew_seconds"
        ],
        "ratification_required": [
            row["id"] for row in policy["ratification_required"]
        ],
        "authority": copy.deepcopy(AUTHORITY),
    }
    return _with_packet_sha(record)


def validate_anchor(value: dict, *, policy: dict | None = None) -> dict:
    policy = load_policy() if policy is None else policy
    if not isinstance(value, dict):
        fail("ANCHOR_NOT_OBJECT")
    if value.get("schema_version") != ANCHOR_SCHEMA_VERSION:
        fail("ANCHOR_SCHEMA_INVALID")
    if value.get("benchmark_definition_id") != DEFINITION_ID:
        fail("ANCHOR_DEFINITION_ID_INVALID")
    if value.get("authority") != AUTHORITY:
        fail("ANCHOR_AUTHORITY_MISMATCH")
    _verify_packet_sha(value, "ANCHOR")
    _token(value.get("market"), "ANCHOR_MARKET_INVALID")
    _text(value.get("ledger_id"), "ANCHOR_LEDGER_ID_INVALID")
    _sha(value.get("ledger_packet_sha256"), "ANCHOR_LEDGER_SHA_INVALID")
    _utc(value.get("anchor_utc"), "ANCHOR_UTC_INVALID")
    _utc(value.get("recorded_at_utc"), "ANCHOR_RECORDED_AT_INVALID")
    if value.get("clock_basis") != "WITNESS_BOUNDED_NOT_PROVEN":
        fail("ANCHOR_CLOCK_BASIS_INVALID")
    provenance = value.get("ledger_provenance")
    if not isinstance(provenance, dict) or provenance.get("authentication") != (
        "PUBLISHED_APPEND_ONLY_SNAPSHOT_STORE_PLUS_GENESIS_PIN"
    ):
        fail("ANCHOR_LEDGER_PROVENANCE_INVALID")
    _sha(provenance.get("genesis_event_sha256"), "ANCHOR_GENESIS_EVENT_SHA_INVALID")
    _validate_price_observation(
        value.get("clock_witness"), "recorded_clock_witness",
        int(SIMULATOR.load_contract()["decimal_scale"]),
    )
    scale = int(SIMULATOR.load_contract()["decimal_scale"])
    for key in ("nav0_krw", "anchor_price", "effective_entry_price"):
        _decimal(value.get(key), f"ANCHOR_{key.upper()}_INVALID", scale=scale, positive=True)
    notionals = value.get("notionals")
    if not isinstance(notionals, dict) or set(notionals) != set(NOTIONAL_BASES):
        fail("ANCHOR_NOTIONAL_BASES_MISSING")
    for basis, row in notionals.items():
        if not isinstance(row, dict) or row.get("notional_basis") != basis:
            fail(f"ANCHOR_NOTIONAL_BASIS_MISLABELLED:{basis}")
        for key in ("notional_krw", "units", "anchor_cash_spent_krw"):
            _decimal(
                row.get(key), f"ANCHOR_{basis}_{key.upper()}_INVALID",
                scale=scale, positive=True,
            )
        _decimal(
            row.get("anchor_residual_cash_krw"),
            f"ANCHOR_{basis}_RESIDUAL_CASH_INVALID", scale=scale,
        )
    state = value.get("market_state")
    if not isinstance(state, dict) or state.get("state") not in (
        "RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS", MARKET_STATE_UNKNOWN
    ):
        fail("ANCHOR_MARKET_STATE_RECORD_INVALID")
    if state.get("new_buys") not in NEW_BUYS_PERMITTED:
        fail("ANCHOR_MARKET_STATE_RECORD_FORBIDS_NEW_BUYS")
    binding = value.get("declared_stop_rule_binding")
    if not isinstance(binding, dict) or binding.get("status") != DECLARED_BINDING_STATUS:
        fail("ANCHOR_DECLARED_BINDING_INVALID")
    for rule_id, series_name in DECLARED_BINDING_SERIES.items():
        row = binding.get(rule_id)
        if not isinstance(row, dict) or row.get("series") != series_name:
            fail(f"ANCHOR_DECLARED_BINDING_SERIES_MISMATCH:{rule_id}")
    return copy.deepcopy(value)


# ─────────────────────────────────────────────────────────────────────────
# Write-once recording. A second, different anchor for the same account is
# not an update -- it is the thing this module exists to make impossible.
# ─────────────────────────────────────────────────────────────────────────

def _anchor_dir(root: Path, market: str, ledger_id: str) -> Path:
    return Path(root) / market / ledger_id / "anchor"


def bound_digests(root: Path, market: str, ledger_id: str) -> set:
    """Every anchor digest this account is already bound to, from the evidence
    tree itself rather than from the pointer file.

    The pointer is a convenience, not the binding. Deleting it and rerunning
    inside the lag window must not let a second, different anchor bind, so the
    binding is recoverable from two independent places that are never rewritten:
    the append-only ``bindings/<digest>.json`` markers, and the
    content-addressed ``<digest>/anchor.json`` records themselves.
    """
    base = _anchor_dir(root, market, ledger_id)
    digests = set()
    if not base.is_dir():
        return digests
    bindings = base / BINDINGS_DIRNAME
    if bindings.is_dir():
        for path in sorted(bindings.glob("*.json")):
            name = path.name[: -len(".json")]
            if DIGEST_DIR_RE.fullmatch(name) is None:
                fail(f"ANCHOR_BINDING_NAME_INVALID:{path.name}")
            marker = _read_json(path)
            if not isinstance(marker, dict) or marker.get("anchor_sha256") != name:
                fail(f"ANCHOR_BINDING_CONTENT_INVALID:{path.name}")
            digests.add(name)
    for child in sorted(base.iterdir()):
        if not child.is_dir() or DIGEST_DIR_RE.fullmatch(child.name) is None:
            continue
        if (child / ANCHOR_FILENAME).is_file():
            digests.add(child.name)
    return digests


def record_anchor(root: Path, anchor: dict) -> Path:
    """Write the anchor once. Identical bytes are a no-op; anything else refuses."""
    checked = validate_anchor(anchor)
    digest = checked["packet_sha256"]
    base = _anchor_dir(root, checked["market"], checked["ledger_id"])
    base.mkdir(parents=True, exist_ok=True)
    already = bound_digests(root, checked["market"], checked["ledger_id"])
    if already - {digest}:
        # Reached whether or not the pointer file still exists.
        fail(
            "ANCHOR_ALREADY_BOUND_IMMUTABLE:"
            + ",".join(sorted(already - {digest}))
        )
    content_dir = base / digest
    content_dir.mkdir(parents=True, exist_ok=True)
    content_path = content_dir / ANCHOR_FILENAME
    payload = (canonical_json(checked) + "\n").encode("utf-8")
    if content_path.exists():
        if content_path.read_bytes() != payload:
            fail("ANCHOR_CONTENT_ADDRESSED_BYTES_DIVERGED")
    else:
        with open(content_path, "xb") as handle:
            handle.write(payload)

    bindings_dir = base / BINDINGS_DIRNAME
    bindings_dir.mkdir(parents=True, exist_ok=True)
    marker_path = bindings_dir / f"{digest}.json"
    marker_bytes = (canonical_json({
        "schema_version": "paper_benchmark_anchor_binding/1",
        "market": checked["market"],
        "ledger_id": checked["ledger_id"],
        "anchor_sha256": digest,
        "anchor_utc": checked["anchor_utc"],
        "recorded_at_utc": checked["recorded_at_utc"],
        "immutability": "APPEND_ONLY_ONE_BINDING_PER_ACCOUNT",
    }) + "\n").encode("utf-8")
    if marker_path.exists():
        if marker_path.read_bytes() != marker_bytes:
            fail("ANCHOR_BINDING_BYTES_DIVERGED")
    else:
        with open(marker_path, "xb") as handle:
            handle.write(marker_bytes)

    pointer = {
        "schema_version": "paper_benchmark_anchor_pointer/1",
        "market": checked["market"],
        "ledger_id": checked["ledger_id"],
        "anchor_sha256": digest,
        "anchor_utc": checked["anchor_utc"],
        "recorded_at_utc": checked["recorded_at_utc"],
        "immutability": "WRITE_ONCE_NO_UPDATE_PATH",
    }
    pointer_path = base / POINTER_FILENAME
    pointer_bytes = (canonical_json(pointer) + "\n").encode("utf-8")
    try:
        with open(pointer_path, "xb") as handle:
            handle.write(pointer_bytes)
    except FileExistsError:
        existing = _read_json(pointer_path)
        if not isinstance(existing, dict) or existing.get("anchor_sha256") != digest:
            fail("ANCHOR_ALREADY_RECORDED_IMMUTABLE")
    return content_path


class VerifiedAnchor:
    """An anchor reloaded from disk and matched to a separately trusted sha256.

    Mirrors P7-19's ``VerifiedPerformanceHistory``: a hash written inside the
    request is not a trust anchor, so ``build_series`` accepts only an
    instance produced by :func:`load_anchor`.
    """

    __slots__ = ("_anchor", "_sha256", "_path")

    def __init__(self, anchor: dict, sha256: str, path: Path):
        self._anchor = copy.deepcopy(anchor)
        self._sha256 = sha256
        self._path = Path(path)

    @property
    def anchor(self) -> dict:
        return copy.deepcopy(self._anchor)

    @property
    def sha256(self) -> str:
        return self._sha256

    @property
    def path(self) -> Path:
        return self._path


def load_anchor(
    root: Path, market: str, ledger_id: str, *, trusted_anchor_sha256: str,
    policy: dict | None = None,
) -> VerifiedAnchor:
    """Reload the recorded anchor against a separately trusted sha256."""
    expected = _sha(trusted_anchor_sha256, "TRUSTED_ANCHOR_SHA_INVALID")
    base = _anchor_dir(root, market, ledger_id)
    digests = bound_digests(root, market, ledger_id)
    if not digests:
        fail("ANCHOR_NOT_RECORDED")
    if len(digests) > 1:
        fail("ANCHOR_MULTIPLE_BINDINGS:" + ",".join(sorted(digests)))
    if digests != {expected}:
        fail("ANCHOR_BINDING_SHA_MISMATCH")
    pointer_path = base / POINTER_FILENAME
    if pointer_path.is_file():
        pointer = _read_json(pointer_path)
        if not isinstance(pointer, dict) or pointer.get("anchor_sha256") != expected:
            fail("ANCHOR_POINTER_SHA_MISMATCH")
    content_path = base / expected / ANCHOR_FILENAME
    if not content_path.is_file():
        fail("ANCHOR_CONTENT_MISSING")
    anchor = validate_anchor(_read_json(content_path), policy=policy)
    if anchor["packet_sha256"] != expected:
        fail("ANCHOR_CONTENT_SHA_MISMATCH")
    if anchor["market"] != market or anchor["ledger_id"] != ledger_id:
        fail("ANCHOR_IDENTITY_MISMATCH")
    return VerifiedAnchor(anchor, expected, content_path)


# ─────────────────────────────────────────────────────────────────────────
# Series. The PAPER NAV samples are the grid; the benchmark is priced on
# exactly that grid or it refuses.
# ─────────────────────────────────────────────────────────────────────────

NAV_ROW_FIELDS = ("observed_at", "available_at", "total_nav")
MARK_ROW_FIELDS = ("observed_at", "available_at", "market", "price")


def _validate_grid(rows, *, role: str, fields: tuple, value_field: str,
                   generated_at: dt.datetime, scale: int) -> list[dict]:
    if not isinstance(rows, list) or not rows:
        fail(f"{role}_EMPTY")
    prior = None
    normalized = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != set(fields):
            fail(f"{role}_FIELDS_MISMATCH:{index}")
        if row.get(value_field) is None:
            # A per-market account view carries a null NAV while a held
            # market has no FRESH mark. It is never valued as zero.
            fail(f"{role}_VALUE_UNKNOWN:{index}")
        observed = _utc(row.get("observed_at"), f"{role}_OBSERVED_AT_INVALID:{index}")
        available = _utc(row.get("available_at"), f"{role}_AVAILABLE_AT_INVALID:{index}")
        if observed > available or available > generated_at:
            fail(f"{role}_LOOKAHEAD:{index}")
        if prior is not None and observed <= prior:
            fail(f"{role}_TIME_NOT_STRICTLY_INCREASING:{index}")
        prior = observed
        _decimal(row.get(value_field), f"{role}_VALUE_INVALID:{index}",
                 scale=scale, positive=True)
        normalized.append(copy.deepcopy(row))
    return normalized


def _series_metrics(navs: list[Decimal], basis: Decimal, rows: list[dict],
                    scale: int) -> tuple[list[dict], dict]:
    peak = None
    max_drawdown = Decimal(0)
    peak_at = None
    trough_at = None
    active_peak_at = None
    points = []
    previous_observed = None
    for row, nav in zip(rows, navs):
        observed = _utc(row["observed_at"], "SERIES_OBSERVED_AT_INVALID")
        if peak is None or nav > peak:
            peak = nav
            active_peak_at = row["observed_at"]
        drawdown = _divide(nav - peak, peak, scale)
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            peak_at = active_peak_at
            trough_at = row["observed_at"]
        points.append({
            "observed_at": row["observed_at"],
            "available_at": row["available_at"],
            "total_nav": _format_decimal(nav),
            "return_fraction": _format_decimal(_divide(nav - basis, basis, scale)),
            "drawdown_fraction": _format_decimal(drawdown),
            "gap_seconds_from_previous": (
                None if previous_observed is None
                else int((observed - previous_observed).total_seconds())
            ),
        })
        previous_observed = observed
    summary = {
        "basis_krw": _format_decimal(basis),
        "final_nav": points[-1]["total_nav"],
        "final_return_fraction": points[-1]["return_fraction"],
        "max_drawdown_fraction": _format_decimal(max_drawdown),
        "max_drawdown_peak_at": peak_at,
        "max_drawdown_trough_at": trough_at,
        "observation_count": len(points),
    }
    return points, summary


def build_series(
    verified_anchor: VerifiedAnchor, *, series_id: str, generated_at_utc: str,
    paper_nav_series: list, paper_nav_series_sha256: str, benchmark_marks: list,
    policy: dict | None = None, params: dict | None = None,
) -> dict:
    """Build the benchmark series on the PAPER NAV series' own sampling grid."""
    if not isinstance(verified_anchor, VerifiedAnchor):
        fail("ANCHOR_NOT_VERIFIED")
    policy = load_policy() if policy is None else policy
    anchor = verified_anchor.anchor
    params = (
        resolve_market_parameters(anchor["market"], policy=policy)
        if params is None else params
    )
    scale = params["decimal_scale"]
    _token(series_id, "SERIES_ID_INVALID")
    generated_at = _utc(generated_at_utc, "GENERATED_AT_INVALID")
    anchor_utc = _utc(anchor["anchor_utc"], "ANCHOR_UTC_INVALID")
    if generated_at < anchor_utc:
        fail("GENERATED_AT_BEFORE_ANCHOR")
    if anchor["benchmark_asset"] != params["benchmark_asset"]:
        fail("ANCHOR_BENCHMARK_ASSET_DRIFTED_FROM_POLICY")

    expected_nav_sha = _sha(paper_nav_series_sha256, "PAPER_NAV_SERIES_SHA_INVALID")
    if payload_sha256(paper_nav_series) != expected_nav_sha:
        fail("PAPER_NAV_SERIES_SHA_MISMATCH")

    nav_rows = _validate_grid(
        paper_nav_series, role="PAPER_NAV_SERIES", fields=NAV_ROW_FIELDS,
        value_field="total_nav", generated_at=generated_at, scale=scale,
    )
    mark_rows = _validate_grid(
        benchmark_marks, role="BENCHMARK_MARK_SERIES", fields=MARK_ROW_FIELDS,
        value_field="price", generated_at=generated_at, scale=scale,
    )
    for index, row in enumerate(mark_rows):
        if row["market"] != anchor["benchmark_asset"]:
            fail(f"BENCHMARK_MARK_MARKET_MISMATCH:{index}")

    if _utc(nav_rows[0]["observed_at"], "PAPER_NAV_OBSERVED_AT_INVALID") < anchor_utc:
        fail("PAPER_NAV_SAMPLE_BEFORE_ANCHOR")
    gap_limit = params["max_sample_gap_seconds"]
    previous = None
    for row in nav_rows:
        observed = _utc(row["observed_at"], "PAPER_NAV_OBSERVED_AT_INVALID")
        if previous is not None and int((observed - previous).total_seconds()) > gap_limit:
            fail(f"PAPER_NAV_SAMPLE_GAP_EXCEEDED:{row['observed_at']}")
        previous = observed

    marks_by_time = {}
    for row in mark_rows:
        if row["observed_at"] in marks_by_time:
            fail(f"BENCHMARK_MARK_DUPLICATE_AT_SAMPLE:{row['observed_at']}")
        marks_by_time[row["observed_at"]] = row
    nav_times = {row["observed_at"] for row in nav_rows}
    for row in nav_rows:
        if row["observed_at"] not in marks_by_time:
            # No interpolation, no carry-forward, no nearest neighbour.
            fail(f"BENCHMARK_MARK_MISSING_AT_SAMPLE:{row['observed_at']}")
    for observed_at in marks_by_time:
        if observed_at not in nav_times:
            # An off-grid mark would let a different, more favourable grid in.
            fail(f"BENCHMARK_MARK_OFF_GRID:{observed_at}")

    nav0 = Decimal(anchor["nav0_krw"])
    priced_rows = []
    for row in nav_rows:
        mark = marks_by_time[row["observed_at"]]
        priced_rows.append({
            "observed_at": row["observed_at"],
            "available_at": mark["available_at"],
            "mark_price": mark["price"],
        })

    # Four series, one anchor. Both notional bases are sized off the SAME
    # effective entry price, fee rate and anchor instant -- only the share of
    # NAV0 differs -- so the pair can never be cherry-picked from two anchors.
    series_out = {}
    for basis in NOTIONAL_BASES:
        sizing = anchor["notionals"][basis]
        units = Decimal(sizing["units"])
        notional = Decimal(sizing["notional_krw"])
        cash_spent = Decimal(sizing["anchor_cash_spent_krw"])
        asset_navs = [
            _floor(units * Decimal(priced["mark_price"]), scale)
            for priced in priced_rows
        ]
        exposure_navs = [nav0 - cash_spent + value for value in asset_navs]
        for marking, navs, basis_krw in (
            (MARKING_EXPOSURE_MATCHED, exposure_navs, nav0),
            (MARKING_ASSET_ONLY, asset_navs, notional),
        ):
            points, summary = _series_metrics(navs, basis_krw, priced_rows, scale)
            for point, priced in zip(points, priced_rows):
                point["mark_price"] = priced["mark_price"]
            series_out[f"{basis}__{marking}"] = {
                **summary,
                "notional_basis": basis,
                "notional_basis_detail": sizing,
                "marking": marking,
                "series": points,
            }
    if set(series_out) != set(SERIES_NAMES):
        fail("SERIES_NAMES_INCOMPLETE")

    paper_navs = [Decimal(row["total_nav"]) for row in nav_rows]
    paper_points, paper_summary = _series_metrics(paper_navs, nav0, nav_rows, scale)

    declared = copy.deepcopy(anchor["declared_stop_rule_binding"])
    comparison = {}
    for name, summary in series_out.items():
        declared_for = sorted(
            rule_id for rule_id, series_name in DECLARED_BINDING_SERIES.items()
            if series_name == name
        )
        comparison[name] = {
            "notional_basis": summary["notional_basis"],
            "marking": summary["marking"],
            "declared_binding_for": declared_for,
            "stop_rule_1_inputs": {
                "ko": "비용 차감 후 그냥 보유보다 낮다",
                "paper_final_return_fraction": paper_summary["final_return_fraction"],
                "benchmark_final_return_fraction": summary["final_return_fraction"],
                "paper_minus_benchmark_return_fraction": _format_decimal(
                    Decimal(paper_summary["final_return_fraction"])
                    - Decimal(summary["final_return_fraction"])
                ),
            },
            "stop_rule_5_inputs": {
                "ko": "하락 구간에서 그냥 보유보다 더 깎였다",
                "paper_max_drawdown_fraction": paper_summary["max_drawdown_fraction"],
                "benchmark_max_drawdown_fraction": summary["max_drawdown_fraction"],
                "paper_minus_benchmark_max_drawdown_fraction": _format_decimal(
                    Decimal(paper_summary["max_drawdown_fraction"])
                    - Decimal(summary["max_drawdown_fraction"])
                ),
            },
            "verdict": VERDICT_NOT_EMITTED,
        }

    record = {
        "schema_version": SERIES_SCHEMA_VERSION,
        "policy_id": policy["policy_id"],
        "benchmark_definition_id": DEFINITION_ID,
        "series_id": series_id,
        "generated_at": generated_at_utc,
        "market": anchor["market"],
        "currency": anchor["currency"],
        "ledger_id": anchor["ledger_id"],
        "benchmark_asset": anchor["benchmark_asset"],
        "anchor_sha256": verified_anchor.sha256,
        "anchor_utc": anchor["anchor_utc"],
        "anchor_notionals": copy.deepcopy(anchor["notionals"]),
        "notional_bases_identical": anchor["notional_bases_identical"],
        "market_state_at_anchor": copy.deepcopy(anchor["market_state"]),
        "declared_stop_rule_binding": declared,
        "cost_model": copy.deepcopy(anchor["cost_model"]),
        "paper_nav_series_sha256": expected_nav_sha,
        "sample_count": len(nav_rows),
        "observation_start_utc": nav_rows[0]["observed_at"],
        "observation_end_utc": nav_rows[-1]["observed_at"],
        "max_sample_gap_seconds": gap_limit,
        "paper_account": {
            "basis_krw": paper_summary["basis_krw"],
            "final_nav": paper_summary["final_nav"],
            "final_return_fraction": paper_summary["final_return_fraction"],
            "max_drawdown_fraction": paper_summary["max_drawdown_fraction"],
            "max_drawdown_peak_at": paper_summary["max_drawdown_peak_at"],
            "max_drawdown_trough_at": paper_summary["max_drawdown_trough_at"],
            "series": paper_points,
        },
        "variants": series_out,
        "nav_series_for_counterfactual": [
            {
                "observed_at": point["observed_at"],
                "available_at": point["available_at"],
                "total_nav": point["total_nav"],
            }
            for point in series_out[DECLARED_BINDING_SERIES[STOP_RULE_1]]["series"]
        ],
        "nav_series_for_counterfactual_variant": DECLARED_BINDING_SERIES[STOP_RULE_1],
        "comparison": comparison,
        "definitions": copy.deepcopy(DEFINITIONS),
        "lineage": {
            "policy_path": "config/paper_benchmark_nav_series_policy.json",
            "simulator_path": "shadow/crypto_paper_simulator.py",
            "counterfactual_consumer": "validation/crypto_paper_counterfactual.py",
            "ledger_packet_sha256": anchor["ledger_packet_sha256"],
        },
        "ratification_required": copy.deepcopy(anchor["ratification_required"]),
        "authority": copy.deepcopy(AUTHORITY),
    }
    return _with_packet_sha(record)


# ─────────────────────────────────────────────────────────────────────────
# CLI. Dispatch only: reads a request file, writes a record. Nothing here is
# scheduled, and no workflow invokes it.
# ─────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Record the immutable benchmark anchor, or build the benchmark NAV "
            "series. Evidence only; no trading, order or allocation authority."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    anchor_cmd = sub.add_parser(
        "anchor",
        help=(
            "derive and record the one anchor (request carries "
            "ledger_snapshot_root, ledger_id, ledger_genesis_pin, "
            "price_observations, clock_witness, market_state_observation, "
            "recorded_at_utc)"
        ),
    )
    anchor_cmd.add_argument("--request", required=True)
    anchor_cmd.add_argument("--root", required=True)

    series_cmd = sub.add_parser("series", help="build the benchmark NAV series")
    series_cmd.add_argument("--request", required=True)
    series_cmd.add_argument("--root", required=True)
    series_cmd.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    request = _read_json(Path(args.request))
    if not isinstance(request, dict):
        fail("REQUEST_NOT_OBJECT")

    if args.command == "anchor":
        verified_ledger = authenticate_ledger(
            Path(request["ledger_snapshot_root"]),
            request["ledger_id"],
            genesis_pin=request["ledger_genesis_pin"],
        )
        anchor = derive_anchor(
            market=request["market"],
            verified_ledger=verified_ledger,
            price_observations=request["price_observations"],
            recorded_at_utc=request["recorded_at_utc"],
            clock_witness=request["clock_witness"],
            market_state_observation=request["market_state_observation"],
            anchor_utc=request.get("anchor_utc"),
        )
        path = record_anchor(Path(args.root), anchor)
        print(json.dumps({
            "recorded": str(path),
            "anchor_sha256": anchor["packet_sha256"],
            "anchor_utc": anchor["anchor_utc"],
        }, ensure_ascii=False))
        return 0

    verified = load_anchor(
        Path(args.root), request["market"], request["ledger_id"],
        trusted_anchor_sha256=request["trusted_anchor_sha256"],
    )
    series = build_series(
        verified,
        series_id=request["series_id"],
        generated_at_utc=request["generated_at_utc"],
        paper_nav_series=request["paper_nav_series"],
        paper_nav_series_sha256=request["paper_nav_series_sha256"],
        benchmark_marks=request["benchmark_marks"],
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(canonical_json(series) + "\n", encoding="utf-8")
    print(json.dumps({
        "written": str(out_path),
        "series_sha256": series["packet_sha256"],
        "sample_count": series["sample_count"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
