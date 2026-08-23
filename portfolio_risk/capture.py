#!/usr/bin/env python3
"""CLI entry point: capture one real Alpaca PAPER account snapshot and
publish it as append-only evidence.

    python3 -m portfolio_risk.capture

★ Credential pattern reused verbatim from `collectors/free_market_data.py`:
  `ALPACA_API_KEY`/`ALPACA_API_SECRET` via `os.getenv(...)`. No new secrets
  mechanism. If either is missing, this exits non-zero without attempting
  any network call (fail-closed, same as the free-market-data collector).

★ CIO review round 1 (2026-08-23) fixed 2 P0 defects in this module:

  1. Raw account number leak (Fix 1/2). The previous version gzipped the
     RAW Alpaca `/v2/account` response verbatim into a committed
     `.json.gz` -- including the real `account_number` field, untouched.
     Gzip is not encryption. This version runs
     `portfolio_snapshot.sanitize_for_raw_evidence()` on both raw payloads
     BEFORE they are ever compressed or written -- there is no code path
     in this module that writes an un-sanitized raw payload to disk.

  2. Not actually append-only (Fix 3). The previous version wrote every
     run of a day to the SAME fixed path (`raw/<day>/alpaca_account.json.gz`
     etc.), so a second run silently overwrote the first snapshot. This
     version content-addresses every evidence file by the sha256 of its
     own bytes (`<name>-<sha16>.json[.gz]`): re-running with an identical
     snapshot reproduces the identical filename and is a byte-identical
     no-op (never re-written, never an error); a genuinely different
     snapshot gets a genuinely different filename, so both are preserved;
     and if a path ever collides with DIFFERENT content (should be
     impossible given content-addressing, but is checked explicitly rather
     than assumed), that is a hard failure, never a silent overwrite.

  `data/latest_portfolio_risk_input.json` remains the one MUTABLE pointer
  file (by repo-wide convention, mirroring `collectors/free_market_data.py`)
  -- it is expected to change every run and is not part of the append-only
  evidence set.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile

from portfolio_risk import alpaca_client, portfolio_snapshot as ps

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc


class PortfolioRiskCaptureError(ValueError):
    pass


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    os.replace(temp, path)


def _canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _write_append_only_or_noop(path: Path, data: bytes) -> None:
    """★ Fix 3: content-addressed append-only write. Identical content at
    an already-existing path is a silent no-op (not a re-write, not an
    error -- this IS what makes re-running the capture safe). Content that
    somehow differs at an already-existing path (should be structurally
    impossible since the path itself is derived from the content's own
    hash) is a hard failure -- an overwrite is never silent."""
    if path.exists():
        existing = path.read_bytes()
        if existing == data:
            return
        raise PortfolioRiskCaptureError(f"APPEND_ONLY_EVIDENCE_COLLISION_DIFFERENT_CONTENT:{path}")
    _atomic_write(path, data)


def run(root: Path, key: str, secret: str, now: dt.datetime) -> dict:
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
    ps.validate_snapshot(packet)

    # ★ Fix 1/2: sanitize BEFORE compression -- the raw account number (and
    # any other forbidden identifying field) never reaches disk, sanitized
    # or not. `account_raw_body`/`positions_raw_body` themselves are never
    # written anywhere past this point.
    sanitized_account = ps.sanitize_for_raw_evidence(account_raw_body)
    sanitized_positions = ps.sanitize_for_raw_evidence(positions_raw_body)
    account_bytes = gzip.compress(_canonical_bytes(sanitized_account), mtime=0)
    positions_bytes = gzip.compress(_canonical_bytes(sanitized_positions), mtime=0)
    manifest_bytes = json.dumps(packet, indent=2, sort_keys=True).encode() + b"\n"

    account_sha = hashlib.sha256(account_bytes).hexdigest()[:16]
    positions_sha = hashlib.sha256(positions_bytes).hexdigest()[:16]
    packet_sha = packet["packet_sha256"][:16]

    day = now.astimezone(UTC).date().isoformat()
    raw_dir = root / "evidence" / "operational" / "portfolio_risk_input" / "raw" / day
    _write_append_only_or_noop(raw_dir / f"alpaca_account-{account_sha}.json.gz", account_bytes)
    _write_append_only_or_noop(raw_dir / f"alpaca_positions-{positions_sha}.json.gz", positions_bytes)
    _write_append_only_or_noop(raw_dir / f"manifest-{packet_sha}.json", manifest_bytes)
    _atomic_write(root / "data" / "latest_portfolio_risk_input.json", manifest_bytes)  # ★ the one mutable pointer
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
        "risk_capacity_status": packet["risk_capacity_inputs"]["status"],
        "account_scope_label": packet["risk_capacity_inputs"]["account_scope_label"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
