FROM python:3.12-slim-bookworm

WORKDIR /app

# The reused P9-06 gate module (dedup/out-of-order/backoff primitives) plus
# the two modules it imports at module-load time (microstructure/
# upbit_candle_finalization.py, execution/intraday_freshness.py -- both
# stdlib-only, no further transitive atlas-data deps), and this service's
# own code. No other atlas-data package (collectors, bridge, universe,
# briefing, etc.) is copied into the image -- this service reads no P3-12
# eligibility state and writes no evidence.
COPY realtime/upbit_realtime_gate.py /app/realtime/upbit_realtime_gate.py
COPY microstructure/__init__.py /app/microstructure/__init__.py
COPY microstructure/upbit_candle_finalization.py /app/microstructure/upbit_candle_finalization.py
COPY execution/intraday_freshness.py /app/execution/intraday_freshness.py
COPY services/upbit-realtime-observation/observation_gate.py /app/services/upbit-realtime-observation/observation_gate.py
COPY services/upbit-realtime-observation/public_market_intelligence.py /app/services/upbit-realtime-observation/public_market_intelligence.py
COPY services/upbit-realtime-observation/service.py /app/services/upbit-realtime-observation/service.py

RUN pip install --no-cache-dir "websockets>=12.0,<16" "cryptography>=43,<46" \
 && useradd --system --uid 10001 --home /nonexistent --shell /usr/sbin/nologin atlas
USER atlas

ENV PYTHONUNBUFFERED=1
EXPOSE 8792
CMD ["python", "/app/services/upbit-realtime-observation/service.py"]
