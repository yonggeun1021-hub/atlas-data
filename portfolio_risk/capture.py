#!/usr/bin/env python3
"""CLI entry point: capture one real Alpaca PAPER account snapshot and
publish it as append-only evidence.

    python3 -m portfolio_risk.capture

★ Credential pattern reused verbatim from `collectors/free_market_data.py`:
  `ALPACA_API_KEY`/`ALPACA_API_SECRET` via `os.getenv(...)`. No new secrets
  mechanism. If either is missing, this exits non-zero without attempting
  any network call (fail-closed, same as the free-market-data collector).

★ Evidence layout mirrors `collectors/free_market_data.py::publish()`:
    evidence/operational/portfolio_risk_input/raw/<day>/*.json.gz  (immutable)
    evidence/operational/portfolio_risk_input/raw/<day>/manifest.json
    data/latest_portfolio_risk_input.json                          (mutable pointer)

★ This module NEVER writes a raw Alpaca account number anywhere -- the
  packet built by `portfolio_snapshot.build_alpaca_paper_account_fact`
  already replaces it with `account_id_hash` (sha256) before this module
  ever sees it.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
from pathlib import Path
import tempfile

from portfolio_risk import alpaca_client, portfolio_snapshot as ps

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    os.replace(temp, path)


def _canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def run(root: Path, key: str, secret: str, now: dt.datetime) -> dict:
    account_raw_body = alpaca_client.fetch_account(key, secret)
    positions_raw_body = alpaca_client.fetch_positions(key, secret)

    ts = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    account_fact = ps.build_alpaca_paper_account_fact(
        account_raw_body, positions_raw_body, captured_at=ts, decision_at=ts,
    )
    packet = ps.assemble_snapshot(
        account_facts=[account_fact],
        fx_rates={},
        expected_sources={"ALPACA_PAPER_ACCOUNT"},
        captured_at=ts, available_at=ts, decision_at=ts,
    )
    ps.validate_snapshot(packet)

    day = now.astimezone(UTC).date().isoformat()
    raw_dir = root / "evidence" / "operational" / "portfolio_risk_input" / "raw" / day
    _atomic_write(raw_dir / "alpaca_account.json.gz", gzip.compress(_canonical_bytes(account_raw_body), mtime=0))
    _atomic_write(raw_dir / "alpaca_positions.json.gz", gzip.compress(_canonical_bytes(positions_raw_body), mtime=0))
    _atomic_write(raw_dir / "manifest.json", json.dumps(packet, indent=2, sort_keys=True).encode() + b"\n")
    _atomic_write(root / "data" / "latest_portfolio_risk_input.json", json.dumps(packet, indent=2, sort_keys=True).encode() + b"\n")
    return packet


def main() -> int:
    key = os.getenv("ALPACA_API_KEY", "").strip()
    secret = os.getenv("ALPACA_API_SECRET", "").strip()
    if not all((key, secret)):
        raise SystemExit("PORTFOLIO_RISK_INPUT_CREDENTIALS_MISSING")
    packet = run(ROOT, key, secret, dt.datetime.now(UTC).replace(microsecond=0))
    print(json.dumps({
        "status": "PASS",
        "packet_sha256": packet["packet_sha256"],
        "total_nav_status": packet["risk_capacity_inputs"]["total_nav_status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
