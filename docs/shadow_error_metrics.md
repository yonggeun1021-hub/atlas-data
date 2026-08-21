# P10-03 Shadow Error Metrics

`shadow/error_metrics.py` aggregates evidence-bound Shadow assessments for four
fixed categories: false positive, miss, stale, and silent error. Every assessed
decision/window must include all four categories as `PRESENT`, `ABSENT`, or
`UNVERIFIED`, preventing selective reporting of only favorable cases.

Rates use only verified `PRESENT + ABSENT` rows. A zero denominator returns
`null`, never a misleading 0%. Classification and definitions remain external;
the module does not infer an error, explain a cause, claim performance, or change
a strategy. The CLI is offline and writes only outside the repository.
