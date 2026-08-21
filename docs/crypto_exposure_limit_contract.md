# P7-05 Crypto Separate Exposure Limit Contract

`portfolio/crypto_exposure_limit.py` evaluates an explicit long-only Crypto
portfolio against an externally ratified policy. Five checks stay separate:
total Crypto exposure, per-asset exposure, total planned loss, per-asset
planned loss, and BTC-reference realized volatility.

The volatility identity is pinned to the existing `btc_risk/v1` feature:
30 close-to-close returns, RMS estimator, 365-day annualization, and annualized
fraction units. Missing, stale, differently defined, or future-available
measurements fail closed. This does not interpret the currently uncalibrated
BTC Stress feature as a Regime or signal.

Every position carries exact asset identity, position, and ratified Crypto
universe membership hashes. Packet lineage also binds the portfolio snapshot,
P7-04 market/theme budget packet, Crypto universe packet, volatility source
snapshot, and observation. Planned loss may not exceed the long position's NAV
weight.

The repository defines no limit value. Only a `CIO`-ratified, effective policy
can produce `WITHIN_RATIFIED_LIMITS` or `LIMIT_BREACH`. Both are risk checks,
not reduction, sizing, or order instructions; those outputs stay null/empty
and Production/trading authority remains false. CLI output inside the
repository tree is forbidden.

Output schema `crypto_exposure_packet/2` embeds the exact normalized input and
full ratified policy packets. `validate_packet()` re-runs the ingestion input
and policy validators, including policy status and effective interval,
position/volatility identity, upstream P7-04 status, source lineage, and both
source packet hashes. It then rebuilds every total, per-asset assessment,
breach, summary, and closed action field from those validated sources.
Re-hashing an envelope after raising a limit, changing a measured exposure, or
suppressing a breach therefore fails closed at the standalone and P8
consumption boundaries.
