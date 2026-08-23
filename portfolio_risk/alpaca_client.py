#!/usr/bin/env python3
"""Read-only Alpaca PAPER trading-API client -- account/positions only.

★ Structural (not conventional) safety: this module can NEVER issue an
  order-creation/modification/cancellation call.
    - The base host is a hard-coded constant (`PAPER_API_BASE`), never a
      parameter -- there is no code path that can point this module at the
      live-trading host.
    - `_get()` is the ONLY function in this module that ever opens a
      network connection, and it never passes `data=`/`method=` to
      `urllib.request.Request` -- `urllib.request` defaults to GET
      whenever no request body is supplied, so this is a GET call by
      construction, not by convention.
    - Every caller-facing fetch function (`fetch_account`, `fetch_positions`)
      hits a path from `ALLOWED_PATHS` ONLY -- a frozenset checked BEFORE
      any network call is attempted. `/v2/orders` (or any other
      trading-mutation endpoint) is not a member of that set and there is
      no function anywhere in this module that could reach it.
  See `test/test_portfolio_risk_input.py::NoOrderApiCallPossibleTests` for
  the structural proof (grep-based + a direct attempt to call an
  unregistered path, which raises before any network I/O).

★ Credential pattern: reused verbatim from `collectors/free_market_data.py`
  -- `ALPACA_API_KEY`/`ALPACA_API_SECRET` via `os.getenv(...)`, the same
  `APCA-API-KEY-ID`/`APCA-API-SECRET-KEY` header pair. No new secrets
  mechanism is invented.
"""
from __future__ import annotations

import json
import urllib.request

PAPER_API_BASE = "https://paper-api.alpaca.markets"  # ★ hard-coded -- never parameterized, never the live host

ALLOWED_PATHS = frozenset({"/v2/account", "/v2/positions"})


class AlpacaClientError(ValueError):
    pass


def _get(url: str, headers: dict[str, str]) -> bytes:
    """The ONLY function in this module that performs network I/O. No
    `data=`/`method=` is ever passed -- this is structurally a GET."""
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise AlpacaClientError(f"HTTP_STATUS:{response.status}")
        return response.read()


def _fetch_path(path: str, key: str, secret: str, getter=_get) -> bytes:
    if path not in ALLOWED_PATHS:
        # ★ Fails BEFORE any network call is even constructed.
        raise AlpacaClientError(f"PATH_NOT_ALLOWLISTED:{path}")
    url = PAPER_API_BASE + path
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    return getter(url, headers)


def fetch_account(key: str, secret: str, getter=_get) -> dict:
    raw = _fetch_path("/v2/account", key, secret, getter)
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AlpacaClientError("ALPACA_ACCOUNT_JSON_INVALID") from exc
    if not isinstance(body, dict):
        raise AlpacaClientError("ALPACA_ACCOUNT_SHAPE_INVALID")
    return body


def fetch_positions(key: str, secret: str, getter=_get) -> list[dict]:
    raw = _fetch_path("/v2/positions", key, secret, getter)
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AlpacaClientError("ALPACA_POSITIONS_JSON_INVALID") from exc
    if not isinstance(body, list):
        raise AlpacaClientError("ALPACA_POSITIONS_SHAPE_INVALID")
    return body
