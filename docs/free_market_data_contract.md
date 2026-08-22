# Free market data evidence

This slice captures FRED `VIXCLS` initial-release observations and Alpaca Basic
IEX latest bars.  It is evidence-only.  IEX is a single-exchange partial US
feed and cannot authorize US breadth, market-wide prices, entry, action, order,
broker submission, production, or trading.

Raw provider bytes are retained as deterministic gzip files with SHA-256
lineage.  A latest pointer is published for briefing/status consumers.  Missing
credentials, missing provider rows, malformed JSON, or a non-IEX contract fail
closed before publication.
