# Official KRX calendar for PAPER market data

KIS PAPER does not expose the domestic holiday transaction used by the REAL
profile.  The PAPER quote path therefore uses a separate calendar source: KRX
Global **[01023] Market Closing(Holiday)**.  The collector performs only the
three requests required by that public page (`PAGE_GET`, `OTP_GET`, and
`HOLIDAY_POST`).  It rejects redirects and performs no OAuth, account, order,
or REAL-domain operation.

The capture bundle embeds the exact provider response bytes as base64, records
their SHA-256 and receipt time, and discards the short-lived OTP.  The pure
adapter validates every returned date, weekday, year, ordering, and hash.
Weekends and dates listed by KRX become `CLOSED`; an unlisted weekday uses the
regular 09:00-15:30 session published in the KRX equity-market rules.  Invalid,
unknown, future, or tampered evidence fails closed.

Capture the annual source before the execution session opens:

```bash
python3 collectors/krx_official_holiday_calendar.py 2026 \
  evidence/market_calendar/krx_global_holiday/2026-09-09/capture-2026.json
```

Derive one committed envelope for every calendar date from context session D
through execution session E:

```bash
python3 market_data/krx_official_holiday_calendar.py \
  evidence/market_calendar/krx_global_holiday/2026-09-09/capture-2026.json \
  2026-09-09 \
  evidence/market_calendar/krx_global_holiday/2026-09-09/calendar-2026-09-09.json \
  --receipt evidence/market_calendar/krx_global_holiday/2026-09-09/derivation-2026-09-09.json
```

The existing PAPER quote consumer accepts the resulting calendar packet only
when its externally supplied packet hash matches.  The D-to-E predecessor
validator additionally reads the exact committed capture and rederives each
calendar packet.  Calendar qualification grants no candidate, entry, order,
trading, production, or real-capital authority.

Official sources:

- <https://global.krx.co.kr/contents/GLB/05/0501/0501110000/GLB0501110000.jsp>
- <https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T1.jsp>
