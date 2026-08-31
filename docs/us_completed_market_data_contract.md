# US completed market-data contract

`market_data/us_session_bars.py` validates caller-supplied US regular-session
bars. It is network-free and has no broker or credential path.

## Session semantics

- Timezone is IANA `America/New_York`; offsets are validated against the date,
  so EST/EDT cannot be swapped.
- A date-specific official exchange calendar snapshot is mandatory. Weekday
  inference is prohibited.
- `OPEN_REGULAR`: 09:30–16:00 ET. `OPEN_EARLY_CLOSE`: 09:30–13:00 ET.
- `CLOSED` is a normal no-bar outcome. `UNKNOWN` blocks. Pre/post-market bars
  are outside scope.
- A full regular session contains 26 completed 15m bars, six full 1h bars, and
  one exact-session 1d bar. The 15:30–16:00 30-minute tail is not mislabeled as
  1h. Early close contains 14 completed 15m bars, three full 1h bars, and one
  1d bar.

## PIT, gaps, and corporate actions

Only intervals whose close is at or before `decision_at` exist. Missing
expected intervals block; exact duplicates dedupe and count; conflicting
duplicates block. A historical fetch is not historical availability:
`BACKFILL` requires a source-proven `original_available_at` or it is rejected.

Version 1 accepts RAW bars only. Adjusted history is blocked until a separately
ratified vintage/revision contract exists. Split and dividend events are kept
as PIT action lineage; effective events must be `APPLIED`. Symbol changes must
match a non-overlapping effective symbol timeline. Future or intraday-effective
action application is rejected.

Freshness delegates to the existing common P9-01
`execution/intraday_freshness.py` contract. The repository has no US default
threshold. An external, effective, ratified US policy is required. Feed scope
(for example `IEX_ONLY`) is retained and never promoted to full-market SIP.

All results are observation-only. OAuth, broker POST, PAPER order, real
account/capital, Production, and Trading authority remain false.
