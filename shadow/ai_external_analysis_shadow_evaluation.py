"""Stage6 supplemental, zero-authority AI-versus-baseline shadow evaluator.

This is an extension record keyed to an existing P10-01 record hash, not a
second decision ledger.  It never changes baseline rank/decision or emits an
order, eligibility, weight, entry, exit, or model recommendation.
"""
from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import re

SHA = re.compile(r"^[0-9a-f]{64}$")
UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
FIELDS = (
    "market", "symbol", "decision_at", "source_cutoff_at", "baseline_rank",
    "baseline_decision", "ai_event_features", "shadow_rank_or_annotation",
    "forward_windows", "realized_return", "drawdown", "turnover_cost",
    "regime", "data_freshness", "model_sha256", "prompt_sha256", "source_sha256",
    "private_daily_receipt_sha256", "private_daily_output_sha256",
)
AUTHORITY = {"supplemental_shadow_evaluation": True, "baseline_mutation": False,
             "eligibility": False, "weight": False, "entry": False, "exit": False,
             "order": False, "paper": False, "real": False, "trading": False}

class AIExternalAnalysisShadowError(ValueError):
    pass

def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def payload_sha256(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()

def _sha(value, code):
    if not isinstance(value, str) or SHA.fullmatch(value) is None:
        raise AIExternalAnalysisShadowError(code)
    return value

def _utc(value, code):
    if not isinstance(value, str) or UTC.fullmatch(value) is None:
        raise AIExternalAnalysisShadowError(code)
    try:
        if datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%dT%H:%M:%SZ") != value:
            raise ValueError
    except ValueError:
        raise AIExternalAnalysisShadowError(code) from None
    return value

def build_record(p10_record_sha256, observation, recorded_at, sequence, previous_record_sha256=None):
    """Preserve one same-cutoff baseline/AI comparison without evaluating it."""
    if not isinstance(observation, dict) or set(observation) != set(FIELDS):
        raise AIExternalAnalysisShadowError("OBSERVATION_FIELDS_INVALID")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise AIExternalAnalysisShadowError("SEQUENCE_INVALID")
    _sha(p10_record_sha256, "P10_RECORD_SHA_INVALID")
    _utc(recorded_at, "RECORDED_AT_INVALID")
    _utc(observation["decision_at"], "DECISION_AT_INVALID")
    _utc(observation["source_cutoff_at"], "SOURCE_CUTOFF_INVALID")
    if recorded_at < observation["decision_at"]:
        raise AIExternalAnalysisShadowError("RECORD_BEFORE_DECISION")
    if observation["source_cutoff_at"] > observation["decision_at"]:
        raise AIExternalAnalysisShadowError("SOURCE_AFTER_DECISION")
    for key in ("market", "symbol", "baseline_decision", "shadow_rank_or_annotation", "regime", "data_freshness"):
        if not isinstance(observation[key], str) or not observation[key]:
            raise AIExternalAnalysisShadowError(f"{key.upper()}_INVALID")
    if type(observation["baseline_rank"]) is not int or observation["baseline_rank"] < 1:
        raise AIExternalAnalysisShadowError("BASELINE_RANK_INVALID")
    if not isinstance(observation["ai_event_features"], list) or not isinstance(observation["forward_windows"], list):
        raise AIExternalAnalysisShadowError("FEATURES_OR_WINDOWS_INVALID")
    if observation["market"] not in {"US", "Korea", "Crypto"}:
        raise AIExternalAnalysisShadowError("MARKET_INVALID")
    if observation["shadow_rank_or_annotation"] != "ANNOTATION_ONLY":
        raise AIExternalAnalysisShadowError("ANNOTATION_ONLY_REQUIRED")
    if observation["ai_event_features"] != [] or any(observation[key] is not None for key in ("realized_return", "drawdown", "turnover_cost")):
        raise AIExternalAnalysisShadowError("UNBOUND_RECORD_MUST_NOT_ASSERT")
    windows = observation["forward_windows"]
    if not windows or any(type(day) is not int or day < 1 for day in windows) or len(set(windows)) != len(windows):
        raise AIExternalAnalysisShadowError("FORWARD_WINDOWS_INVALID")
    for key in ("model_sha256", "prompt_sha256", "source_sha256", "private_daily_receipt_sha256", "private_daily_output_sha256"):
        _sha(observation[key], f"{key.upper()}_INVALID")
    if sequence == 1 and previous_record_sha256 is not None:
        raise AIExternalAnalysisShadowError("GENESIS_PREVIOUS_INVALID")
    if sequence > 1:
        _sha(previous_record_sha256, "PREVIOUS_SHA_INVALID")
    row = {"schema_version": "ai_external_analysis_shadow_extension_record/1",
           "sequence": sequence, "recorded_at": recorded_at,
           "p10_record_sha256": p10_record_sha256, "observation": copy.deepcopy(observation),
           "stage3_binding_status": "STAGE3_CONTRACT_UNBOUND",
           "previous_record_sha256": previous_record_sha256, "authority": copy.deepcopy(AUTHORITY)}
    row["record_sha256"] = payload_sha256(row)
    return row

def validate_record(row):
    """Rebuild semantics independently; a caller-computed digest is not proof."""
    keys = {"schema_version", "sequence", "recorded_at", "p10_record_sha256", "observation", "stage3_binding_status", "previous_record_sha256", "authority", "record_sha256"}
    if not isinstance(row, dict) or set(row) != keys:
        raise AIExternalAnalysisShadowError("RECORD_FIELDS_INVALID")
    expected = build_record(row["p10_record_sha256"], row["observation"], row["recorded_at"], row["sequence"], row["previous_record_sha256"])
    if canonical_json(row) != canonical_json(expected):
        raise AIExternalAnalysisShadowError("RECORD_SEMANTICS_INVALID")
    return copy.deepcopy(row)


def compare(records, minimum_observations=30):
    """No unbound sample can support effectiveness, regardless of its count."""
    if not isinstance(records, list) or type(minimum_observations) is not int or minimum_observations < 1:
        raise AIExternalAnalysisShadowError("COMPARATOR_INPUT_INVALID")
    previous = None
    bindings = set()
    for index, row in enumerate(records, 1):
        validate_record(row)
        if row["sequence"] != index or row["previous_record_sha256"] != previous:
            raise AIExternalAnalysisShadowError("RECORD_CHAIN_INVALID")
        if index > 1 and row["recorded_at"] < records[index - 2]["recorded_at"]:
            raise AIExternalAnalysisShadowError("RECORD_TIME_REGRESSION")
        binding = (row["p10_record_sha256"], row["observation"]["market"], row["observation"]["symbol"], row["observation"]["decision_at"])
        if binding in bindings:
            raise AIExternalAnalysisShadowError("DUPLICATE_OBSERVATION")
        bindings.add(binding)
        previous = row["record_sha256"]
    return {"schema_version": "ai_external_analysis_shadow_comparison/1",
            "status": "NOT_ENOUGH_OBSERVATIONS" if len(records) < minimum_observations else "BOUND_SAMPLE_REQUIRED",
            "observation_count": len(records), "minimum_observations": minimum_observations,
            "metrics": {key: None for key in ("hit_rate", "incremental_rank_ic", "net_return_after_cost", "maximum_drawdown", "false_positive", "omission", "turnover", "by_regime", "by_market")},
            "limitations": ["STAGE3_CONTRACT_UNBOUND", "NO_AI_EFFECTIVENESS_CLAIM", "NO_BASELINE_MUTATION"],
            "authority": copy.deepcopy(AUTHORITY)}

def portal_aggregate(comparison, last_evaluated_at, *, records=None):
    """Project aggregate-only status; never disclose prompts, filings or symbol rows."""
    _utc(last_evaluated_at, "LAST_EVALUATED_AT_INVALID")
    if records is None:
        records = []
    if not isinstance(comparison, dict):
        raise AIExternalAnalysisShadowError("COMPARISON_INVALID")
    template = compare(records, comparison.get("minimum_observations", 30))
    if records and last_evaluated_at < records[-1]["recorded_at"]:
        raise AIExternalAnalysisShadowError("EVALUATION_BEFORE_RECORD")
    if not isinstance(comparison, dict) or set(comparison) != set(template):
        raise AIExternalAnalysisShadowError("COMPARISON_INVALID")
    count = comparison["observation_count"]
    minimum = comparison["minimum_observations"]
    if type(count) is not int or count < 0 or type(minimum) is not int or minimum < 1:
        raise AIExternalAnalysisShadowError("COMPARISON_COUNT_INVALID")
    if canonical_json(comparison) != canonical_json(template):
        raise AIExternalAnalysisShadowError("COMPARISON_UNSUPPORTED_CLAIM")
    metrics = template["metrics"]
    return {"observation_count": count,
            "sufficiency_status": template["status"],
            "incremental_metrics": copy.deepcopy(metrics),
            "market_segmentation": None, "regime_segmentation": None,
            "last_evaluated_at": last_evaluated_at,
            "notUsedInScore": True}
