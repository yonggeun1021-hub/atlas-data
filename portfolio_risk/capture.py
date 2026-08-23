#!/usr/bin/env python3
"""CLI entry point: capture one real Alpaca PAPER account snapshot,
VERIFY it in memory, and print/return only a redacted, public-repo-safe
summary. No file on disk -- committed, local, or otherwise -- is ever
written by this module.

    python3 -m portfolio_risk.capture

★ Credential pattern reused verbatim from `collectors/free_market_data.py`:
  `ALPACA_API_KEY`/`ALPACA_API_SECRET` via `os.getenv(...)`. No new secrets
  mechanism. If either is missing, this exits non-zero without attempting
  any network call (fail-closed, same as the free-market-data collector).

★ CIO review round 2 (2026-08-23) -- the repo (`yonggeun1021-hub/atlas-data`)
  is PUBLIC. Round 1's fix (stripping the account number out of committed
  raw evidence) does not address the real defect: real NAV, cash, buying
  power, positions/quantities/market values, unrealized P&L, account
  status, and even a stable `account_id_hash` are ALL real financial data
  that must never reach a public repo, regardless of how well any one
  identifying field is scrubbed. This is a data-classification/storage-
  location problem, not a field-scrubbing problem.

  Fix (structural, not conventional):
    1. This module contains ZERO filesystem-write code. No `open(...,
       "wb")`, no `os.replace`, no `gzip`, no `tempfile`, no `Path.write*`
       call exists anywhere in this file -- verified by
       `test/test_portfolio_risk_input.py::PublicRepoNeverReceivesRealFinancialData::test_capture_module_contains_no_filesystem_write_capability_at_all`
       (a source-level scan, not just "we don't call it").
    2. `run()` builds and `validate_snapshot()`s the REAL packet (with real
       numbers) entirely in memory, then immediately discards it -- the
       only thing this module ever returns or prints is the output of
       `_redact_for_public_repo()`, an EXPLICIT allowlist-only
       constructor (never `**packet`, never a stripped copy of `packet`)
       that includes only: schema_version, source=ALPACA_PAPER,
       authority=all-false, capture_status, risk_capacity_status (a status
       LABEL, never a number), account_scope_label, field_completeness_status,
       real_data_persistence_status, timestamps, and error_code. See
       `PUBLIC_SAFE_CAPTURE_RESULT_KEYS`.
    3. No print/log statement anywhere in this module includes a dollar
       amount, quantity, or NAV figure -- `main()` only ever
       `json.dumps()`s the redacted result.
    4. The workflow (`.github/workflows/portfolio-risk-input.yml`) no
       longer has `contents: write`, no longer has a commit/push step, and
       no longer runs on a schedule -- `workflow_dispatch` only, and even
       then it never writes to the repo. Live persistence of real account
       data requires a future, separately-designed PRIVATE evidence store
       -- not in scope here. See `REAL_DATA_PERSISTENCE_STATUS`.
"""
from __future__ import annotations

import datetime as dt
import json
import os

from portfolio_risk import alpaca_client, portfolio_snapshot as ps

UTC = dt.timezone.utc

# ★ The current, honest state of real-account-data persistence: BLOCKED.
# Nothing about this PR changes this value to anything else -- that
# requires a future, separately-reviewed private evidence store.
REAL_DATA_PERSISTENCE_STATUS = "PRIVATE_STORAGE_REQUIRED_BEFORE_LIVE_PERSISTENCE"

# ★ The ONLY keys `_redact_for_public_repo()` is allowed to produce. A test
# asserts every redacted result's key set is a subset of exactly this.
PUBLIC_SAFE_CAPTURE_RESULT_KEYS = frozenset({
    "schema_version", "source", "authority", "capture_status",
    "risk_capacity_status", "account_scope_label", "field_completeness_status",
    "captured_at", "available_at", "decision_at",
    "real_data_persistence_status", "error_code",
})


def _redact_for_public_repo(packet: dict) -> dict:
    """The ONLY public-repo-safe summary of a real account capture.
    Built as an EXPLICIT field-by-field allowlist -- never by copying or
    stripping the real `packet` -- so a new real-data field added to
    `portfolio_snapshot`'s packet shape in the future cannot silently leak
    in here. Every value here is a status label, a schema/version string,
    a timestamp, or the fixed all-`False` authority block -- never a
    dollar amount, quantity, or NAV figure."""
    risk = packet["risk_capacity_inputs"]
    result = {
        "schema_version": packet["schema_version"],
        "source": "ALPACA_PAPER",
        "authority": packet["authority"],
        "capture_status": "SUCCESS",
        "risk_capacity_status": risk["status"],
        "account_scope_label": risk["account_scope_label"],
        "field_completeness_status": "FULL_SCOPE" if not risk["data_completeness"]["missing_sources"] else "PARTIAL_SCOPE",
        "captured_at": packet["captured_at"],
        "available_at": packet["available_at"],
        "decision_at": packet["decision_at"],
        "real_data_persistence_status": REAL_DATA_PERSISTENCE_STATUS,
        "error_code": None,
    }
    assert set(result) <= PUBLIC_SAFE_CAPTURE_RESULT_KEYS  # ★ belt-and-suspenders, see also the test
    return result


def _redacted_failure_result(error_code: str) -> dict:
    """Same allowlist, for the failure path -- a real exception message
    could theoretically embed a real value (e.g. `NEGATIVE_NAV_OR_CASH_REJECTED:
    equity=-10.0`), so only a caller-classified `error_code` string (never
    the raw exception text) is ever included."""
    result = {
        "schema_version": ps.SCHEMA_VERSION,
        "source": "ALPACA_PAPER",
        "authority": ps.AUTHORITY_ALL_FALSE,
        "capture_status": "FAILURE",
        "risk_capacity_status": None,
        "account_scope_label": None,
        "field_completeness_status": None,
        "captured_at": None,
        "available_at": None,
        "decision_at": None,
        "real_data_persistence_status": REAL_DATA_PERSISTENCE_STATUS,
        "error_code": error_code,
    }
    assert set(result) <= PUBLIC_SAFE_CAPTURE_RESULT_KEYS
    return result


def run(key: str, secret: str, now: dt.datetime) -> dict:
    """Fetches, builds, and validates ONE real Alpaca paper account
    snapshot entirely in memory, then discards it. Returns ONLY the
    redacted, public-repo-safe summary -- never the real packet, never any
    real figure. No filesystem write happens anywhere in this function."""
    account_raw_body = alpaca_client.fetch_account(key, secret)
    positions_raw_body = alpaca_client.fetch_positions(key, secret)

    ts = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    account_fact = ps.build_alpaca_paper_account_fact(
        account_raw_body, positions_raw_body, captured_at=ts, decision_at=ts,
    )
    packet = ps.assemble_snapshot(
        account_facts=[account_fact], fx_rates={},
        captured_at=ts, available_at=ts, decision_at=ts,
    )
    ps.validate_snapshot(packet)  # real PIT/tamper/authority verification, in memory only

    redacted = _redact_for_public_repo(packet)
    del packet, account_fact, account_raw_body, positions_raw_body  # ★ discarded, never persisted
    return redacted


def main() -> int:
    key = os.getenv("ALPACA_API_KEY", "").strip()
    secret = os.getenv("ALPACA_API_SECRET", "").strip()
    if not all((key, secret)):
        raise SystemExit("PORTFOLIO_RISK_INPUT_CREDENTIALS_MISSING")
    try:
        redacted = run(key, secret, dt.datetime.now(UTC).replace(microsecond=0))
    except (ps.PortfolioSnapshotError, alpaca_client.AlpacaClientError) as exc:
        # ★ Only the exception's CLASS + a fixed generic code is printed --
        # never `str(exc)`, which could embed a real numeric value (e.g. a
        # NEGATIVE_NAV_OR_CASH_REJECTED message includes the real number).
        print(json.dumps(_redacted_failure_result(type(exc).__name__), sort_keys=True))
        return 1
    print(json.dumps(redacted, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
