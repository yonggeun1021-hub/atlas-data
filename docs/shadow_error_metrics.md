# P10-03 Shadow Error Metrics

`shadow/error_metrics.py` aggregates evidence-bound Shadow assessments for four
fixed categories: false positive, miss, stale, and silent error. Every assessed
decision/window must include all four categories as `PRESENT`, `ABSENT`, or
`UNVERIFIED`, preventing selective reporting of only favorable cases.

Contract v2 requires an exact, self-validating P10-02 comparison packet. Every
assessment must match its comparison packet SHA, evaluation window, decision
date, and `(decision_id, market)` key; `COMMON` rows must match an existing
decision across the comparison markets. The output embeds both the assessment
batch and comparison packet, so a self-rehashed upstream authority mutation
fails closed during later validation.

Rates use only verified `PRESENT + ABSENT` rows. A zero denominator returns
`null`, never a misleading 0%. Classification and definitions remain external;
the module does not infer an error, explain a cause, claim performance, or change
a strategy. The CLI is offline and writes only outside the repository.

```bash
python shadow/error_metrics.py /tmp/shadow-assessments.json \
  /tmp/atlas-legacy-comparison.json \
  --out /tmp/shadow-error-metrics.json
```
