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
because the card says 비트코인 보유) with the market's whole ratified base
allocation of NAV0, pay the same fee rate and the same realized entry
slippage the PAPER engine itself charged on that fill, and then never touch
it again. The rejected alternative — equal-weighting the same candidates the
system bought — is not "simply holding": it borrows the system's own
selection, and its candidate set is not even known at the anchor instant, so
it could not be anchored once. **Both the definition and which of the two
emitted variants binds stop rules 1 and 5 need the user's sentence**; the
policy lists every such item under ``ratification_required`` and this module
emits no stop-rule verdict.

The anchor cannot be chosen later
---------------------------------
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
   decision, not a retry.
5. ``record_anchor`` is write-once: the pointer file is created with
   ``open(..., "x")``. Re-recording byte-identical bytes is an idempotent
   no-op; anything else raises ``ANCHOR_ALREADY_RECORDED_IMMUTABLE``. The
   record is additionally content-addressed by its own payload sha256, and
   ``load_anchor`` will only return it against a separately supplied trusted
   sha256 — a hash rewritten inside the file is not a trust anchor.

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
execution path, and is wired into **no** schedule or workflow — activation
(the anchor has to be written at the first fill, which happens inside the
private crypto runtime) is a separate user decision.
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

VARIANT_EXPOSURE_MATCHED = "EXPOSURE_MATCHED"
VARIANT_ASSET_ONLY = "ASSET_ONLY"
VARIANTS = (VARIANT_EXPOSURE_MATCHED, VARIANT_ASSET_ONLY)

SECONDS_PER_DAY = 86400
# One crypto decision cycle. RULE.EXEC.TIME_CONTRACT.V1 ratifies a 07:00Z
# daily crypto decision cycle; "daily" is what makes this 86400, and using
# one cycle as the anchor write deadline is the CIO interpretation named in
# the policy's ratification_required (RATIFICATION_ANCHOR_WRITE_DEADLINE).
DAILY_DECISION_CYCLE_SECONDS = SECONDS_PER_DAY

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
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
    "registry", "registry_parameters", "benchmark_definition", "cost_model",
    "markets", "fail_closed", "ratification_required", "authority",
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
    if set(definition.get("variants_emitted") or {}) != set(VARIANTS):
        fail("POLICY_VARIANTS_MISMATCH")
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
    base_share = _decimal(
        base_allocation[market], f"BASE_ALLOCATION_INVALID:{market}",
        scale=scale, positive=True,
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


def derive_anchor(
    *, market: str, ledger: dict, price_observations: list, recorded_at_utc: str,
    anchor_utc: str | None = None, policy: dict | None = None,
    params: dict | None = None,
) -> dict:
    """Derive the one immutable anchor for (market, ledger) from real data."""
    policy = load_policy() if policy is None else policy
    params = resolve_market_parameters(market, policy=policy) if params is None else params
    scale = params["decimal_scale"]

    checked = SIMULATOR.validate_ledger(ledger, params["simulator_contract"])
    ledger_id = _text(checked.get("ledger_id"), "LEDGER_ID_INVALID")
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

    notional = _floor(nav0 * params["base_allocation_fraction"], scale)
    if notional <= 0:
        fail("ANCHOR_NOTIONAL_NOT_POSITIVE")
    effective_price = _floor(
        anchor_price * (Decimal(1) + entry_slippage_bps / Decimal(10000)), scale
    )
    if effective_price <= 0:
        fail("ANCHOR_EFFECTIVE_PRICE_NOT_POSITIVE")
    units = _floor(
        _divide(notional, effective_price * (Decimal(1) + fee_rate), scale + 10), scale
    )
    if units <= 0:
        fail("ANCHOR_UNITS_NOT_POSITIVE")
    anchor_gross = _floor(units * effective_price, scale)
    anchor_fee = _floor(anchor_gross * fee_rate, scale)
    cash_spent = anchor_gross + anchor_fee
    if cash_spent > notional:
        fail("ANCHOR_CASH_SPENT_EXCEEDS_NOTIONAL")

    record = {
        "schema_version": ANCHOR_SCHEMA_VERSION,
        "policy_id": policy["policy_id"],
        "benchmark_definition_id": DEFINITION_ID,
        "market": market,
        "currency": params["currency"],
        "ledger_id": ledger_id,
        "ledger_packet_sha256": checked["packet_sha256"],
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
        "base_allocation_fraction": _format_decimal(params["base_allocation_fraction"]),
        "notional_krw": _format_decimal(notional),
        "notional_basis": "NAV0_TIMES_RULE_ALLOCATION_V2_BASE_SHARE_MULTIPLIER_ONE",
        "cost_model": {
            "fee_rate": _format_decimal(fee_rate),
            "fee_rate_source": "ANCHOR_FILL_ORDER_INTENT_RECONCILED_AGAINST_FILL",
            "entry_slippage_bps": _format_decimal(entry_slippage_bps),
            "entry_slippage_source": "ANCHOR_FILL_REALIZED_SLIPPAGE_BPS",
            "exit_cost_treatment": "NOT_CHARGED_MIRRORS_PAPER_NAV_OPEN_POSITION_MARKING",
        },
        "effective_entry_price": _format_decimal(effective_price),
        "units": _format_decimal(units),
        "anchor_gross_krw": _format_decimal(anchor_gross),
        "anchor_fee_krw": _format_decimal(anchor_fee),
        "anchor_cash_spent_krw": _format_decimal(cash_spent),
        "anchor_residual_cash_krw": _format_decimal(notional - cash_spent),
        "max_sample_gap_seconds": params["max_sample_gap_seconds"],
        "anchor_price_max_staleness_seconds": params["anchor_price_max_staleness_seconds"],
        "anchor_record_max_lag_seconds": params["anchor_record_max_lag_seconds"],
        "recorded_at_utc": recorded_at_utc,
        "record_lag_seconds": lag,
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
    scale = int(SIMULATOR.load_contract()["decimal_scale"])
    for key in ("nav0_krw", "notional_krw", "units", "anchor_price",
                "effective_entry_price", "anchor_cash_spent_krw"):
        _decimal(value.get(key), f"ANCHOR_{key.upper()}_INVALID", scale=scale, positive=True)
    _decimal(
        value.get("anchor_residual_cash_krw"), "ANCHOR_RESIDUAL_CASH_INVALID", scale=scale,
    )
    return copy.deepcopy(value)


# ─────────────────────────────────────────────────────────────────────────
# Write-once recording. A second, different anchor for the same account is
# not an update -- it is the thing this module exists to make impossible.
# ─────────────────────────────────────────────────────────────────────────

def _anchor_dir(root: Path, market: str, ledger_id: str) -> Path:
    return Path(root) / market / ledger_id / "anchor"


def record_anchor(root: Path, anchor: dict) -> Path:
    """Write the anchor once. Identical bytes are a no-op; anything else refuses."""
    checked = validate_anchor(anchor)
    digest = checked["packet_sha256"]
    base = _anchor_dir(root, checked["market"], checked["ledger_id"])
    base.mkdir(parents=True, exist_ok=True)
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
    pointer_path = base / POINTER_FILENAME
    if not pointer_path.is_file():
        fail("ANCHOR_NOT_RECORDED")
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

    units = Decimal(anchor["units"])
    nav0 = Decimal(anchor["nav0_krw"])
    notional = Decimal(anchor["notional_krw"])
    cash_spent = Decimal(anchor["anchor_cash_spent_krw"])

    exposure_navs = []
    asset_navs = []
    priced_rows = []
    for row in nav_rows:
        mark = marks_by_time[row["observed_at"]]
        asset_value = _floor(units * Decimal(mark["price"]), scale)
        exposure_navs.append(nav0 - cash_spent + asset_value)
        asset_navs.append(asset_value)
        priced_rows.append({
            "observed_at": row["observed_at"],
            "available_at": mark["available_at"],
            "mark_price": mark["price"],
        })

    exposure_points, exposure_summary = _series_metrics(
        exposure_navs, nav0, priced_rows, scale
    )
    asset_points, asset_summary = _series_metrics(
        asset_navs, notional, priced_rows, scale
    )
    for point, priced in zip(exposure_points, priced_rows):
        point["mark_price"] = priced["mark_price"]
    for point, priced in zip(asset_points, priced_rows):
        point["mark_price"] = priced["mark_price"]

    paper_navs = [Decimal(row["total_nav"]) for row in nav_rows]
    paper_points, paper_summary = _series_metrics(paper_navs, nav0, nav_rows, scale)

    comparison = {}
    for name, summary in (
        (VARIANT_EXPOSURE_MATCHED, exposure_summary),
        (VARIANT_ASSET_ONLY, asset_summary),
    ):
        comparison[name] = {
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
        "anchor_units": anchor["units"],
        "anchor_cash_spent_krw": anchor["anchor_cash_spent_krw"],
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
        "variants": {
            VARIANT_EXPOSURE_MATCHED: {**exposure_summary, "series": exposure_points},
            VARIANT_ASSET_ONLY: {**asset_summary, "series": asset_points},
        },
        "nav_series_for_counterfactual": [
            {
                "observed_at": point["observed_at"],
                "available_at": point["available_at"],
                "total_nav": point["total_nav"],
            }
            for point in exposure_points
        ],
        "nav_series_for_counterfactual_variant": VARIANT_EXPOSURE_MATCHED,
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

    anchor_cmd = sub.add_parser("anchor", help="derive and record the one anchor")
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
        anchor = derive_anchor(
            market=request["market"],
            ledger=request["ledger"],
            price_observations=request["price_observations"],
            recorded_at_utc=request["recorded_at_utc"],
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
