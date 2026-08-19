"""Atlas Market Regime contracts.

No scoring engine is authorized in this package.  The runtime surface builds
and validates fail-closed UNKNOWN output envelopes, the frozen unratified
coverage audit, and a ratified five-of-five coverage-only audit.  Even a
``COVERAGE_MET`` result cannot classify a market until freshness and
classification policies are separately ratified.
"""
