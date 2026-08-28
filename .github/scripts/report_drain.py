#!/usr/bin/env python3
"""Render a drain result as GitHub Actions annotations.

Kept out of the workflow YAML so the heredoc that used to hold it cannot
collide with the outer `run:` block's own heredoc terminator.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    if not data.get("activated"):
        print("::notice::finalization not activated yet "
              "(config/atlas_finalization_activation.json absent or null) — nothing is owed")
    else:
        date, slot = data.get("active_from") or (None, None)
        print(f"::notice::finalization active from {date} {slot}")
    if data.get("semantic_validator_expected") is False:
        print("::notice::no semantic validator configured — clean rounds deliver "
              "stamped UNVALIDATED_NO_VALIDATOR; facts were never checked")
    for item in data.get("pending_delivery_debt", []):
        print(f"::error::{item['briefing_id']} sealed but never delivered "
              f"({item.get('age_days')} days old) — debt does not expire")
    for item in data.get("missing_production", []):
        print(f"::warning::missed slot {item['briefing_id']} — {item['reason']} "
              f"(calendar: {item['calendar_confidence']})")
    for item in data.get("machine_failures", []):
        print(f"::error::{item['briefing_id']} blocked: {item['error']}")
    for item in data.get("observed_pending", []):
        print(f"::warning::{item['briefing_id']} pending: {item['error']}")
    owed = {
        "CIO_RULING_MISSING": "no signed CIO ruling on whether it moves an investment conclusion",
        "PORTAL_ADAPTER_NOT_IMPLEMENTED": "no Portal/SSOT write adapter exists, so the record "
                                          "cannot be proven corrected",
        "PORTAL_PROJECTION_RECEIPT_MISSING": "the Portal/SSOT write left no receipt",
        "NO_USER_REACHING_CHANNEL_CONFIGURED": "no user-reaching channel is configured, so a "
                                               "capital-impact alert cannot be proven delivered",
        "CAPITAL_ALERT_RECEIPT_MISSING": "capital impact is PRESENT and no alert receipt exists",
        "PORTAL_RECEIPT_WITHOUT_ADAPTER": "a projection receipt exists but no adapter could have "
                                          "produced it",
        "PORTAL_RECEIPT_ADAPTER_MISMATCH": "the projection receipt names a different adapter",
        "PORTAL_RECEIPT_CONTENT_MISMATCH": "the projection receipt hashes something other than the "
                                           "content this change requires",
        "PORTAL_RECEIPT_CHANGE_KEY_MISMATCH": "the projection receipt is for a different change",
        "PORTAL_RECEIPT_INCOMPLETE": "the projection receipt names no target or no write time",
        "PORTAL_RECEIPT_BEFORE_RULING": "a projection receipt exists but no ruling does",
        "ALERT_RECEIPT_CHANNEL_NOT_USER_REACHING": "the alert receipt names a channel that does "
                                                   "not reach the user",
        "ALERT_RECEIPT_CONTENT_MISMATCH": "the alert receipt hashes something other than the "
                                          "required alert content",
        "ALERT_RECEIPT_CHANGE_KEY_MISMATCH": "the alert receipt is for a different change",
        "ALERT_RECEIPT_INCOMPLETE": "the alert receipt names no send time or transport id",
    }
    for item in data.get("cio_attention_required", []):
        reasons = "; ".join(owed.get(b, b) for b in item.get("blocked_by", []))
        print(f"::error::{item['briefing_id']} source changed AFTER delivery "
              f"(axes: {', '.join(item.get('changed_axes') or ['unknown'])}) and is not finished: "
              f"{reasons}. Change key {item.get('post_delivery_change_key')}. "
              "Redelivery stays forbidden either way.")
        if item.get("expected_projection_sha256"):
            print(f"::notice::{item['briefing_id']} expected projection content sha256="
                  f"{item['expected_projection_sha256']}"
                  + (f" · expected alert content sha256={item['expected_alert_sha256']}"
                     if item.get("expected_alert_sha256") else ""))
    for item in data.get("post_delivery_changes", []):
        if item.get("complete"):
            print(f"::notice::{item['briefing_id']} post-delivery change closed: "
                  f"capital_impact={item['capital_impact']} ruled by {item.get('resolved_by')}, "
                  f"portal_synced={item.get('portal_synced')}, "
                  f"alert_delivered={item.get('alert_delivered')}")
    if data.get("complete"):
        print("::notice::all due briefings delivered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
