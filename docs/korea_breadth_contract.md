# Korea Breadth PIT Universe Contract (P1-KR-05)

Status: source-capability and raw participation pilot. No Breadth
classification, threshold, Regime, Production, redistribution, or trading
authority.

## Purpose

For one explicitly supplied pair of completed KRX trading dates, reconstruct
the exact source-coverage universe returned by the official KOSPI and KOSDAQ
stock daily endpoints and count how many shared instruments advanced,
declined, or were unchanged. This answers whether the source can support a
point-in-time Korea breadth observation. It does not define an investable
universe or a market regime.

Official KRX services:

- `sto/stk_bydd_trd`: 유가증권 일별매매정보, available from 2010-01-04;
- `sto/ksq_bydd_trd`: 코스닥 일별매매정보, available from 2010-01-04.

The API key is sent only in the `AUTH_KEY` request header. Response bytes,
instrument identifiers, names, and prices remain in memory. The live workflow
prints only aggregate counts and authority flags and writes no raw or derived
market artifact.

## Point-in-time universe

For each market and date, the PIT source universe is the set of unique
`ISU_CD` values in that exact date's official response. Identity is the exact
KRX identifier. Names, display tickers, and string normalization never create
or merge identity.

Between explicit previous and current completed trading dates:

- `shared_count` is the identifier intersection;
- `entered_count` and `exited_count` are set differences;
- a row with an empty close remains a universe member but is excluded from
  paired price participation;
- paired members with two valid closes are counted as `ADVANCE`, `DECLINE`, or
  `UNCHANGED`.

The helper does not guess the previous session from a calendar. Both dates are
inputs, must be ordered, and must each return a non-empty valid response.

## Fail-closed boundaries

The proof stops on missing authentication, non-200 response, malformed JSON,
missing response block, zero rows, missing required fields, date mismatch,
empty identity/name/market, duplicate identity, malformed non-empty close,
or zero paired price observations. Partial availability is represented by
counts and never converted to zero or neutral.

All outputs preserve the following as false:

- Breadth classification;
- threshold;
- Regime score;
- Production wiring;
- trading action.

## Live proof

The manual workflow calls both markets for two date pairs:

- historical: `2010-01-04` → `2010-01-05`;
- recent: operator-supplied previous/current completed KRX sessions.

Success proves official historical universe and one-day raw participation can
be reconstructed without raw persistence. It does not authorize scheduled
capture or evaluator consumption.
