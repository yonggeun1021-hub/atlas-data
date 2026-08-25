# Identity Authority Pilot — mechanical facts only

This pilot ratifies a deliberately small set of mechanical identity facts:

- Bitcoin on Atlas's dedicated Kraken path;
- Samsung Electronics common stock (`005930`);
- SK hynix common stock (`000660`);
- the existing Dynamic Clock market labels `BTC`, `CRYPTO`, and `KOREA`
  to the already-declared portfolio account scopes used for exposure
  reconciliation.

The join key between candidates and portfolio positions is
`canonical_instrument_id`, never issuer name, raw ticker text, or account
scope. Every row has its own external approval packet, exact source-file
hashes, full determining-payload hash, and git-derived first-seen gate.
New rows become usable only after their actual row and approval packet have
appeared in Git history; no effective date or claimed `first_seen_at` can
backdate that boundary.

## Deliberate exclusions

- No other crypto asset or Korea/US listing is inferred.
- `US -> ALPACA_PAPER_ACCOUNT` is not added because the current Dynamic
  Clock has no US candidate market and this pilot avoids speculative edges.
- No investability, liquidity, tradability, validity-window, entry, sizing,
  stage, order, production, or trading authority is granted.
- P8-13 remains closed.

Source evidence is revalidated by
`test/test_identity_authority_pilot.py`. The generic tamper/provenance
mechanism remains covered by `test/test_identity_foundation.py`.
