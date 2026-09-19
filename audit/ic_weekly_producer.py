"""Read immutable PUBLIC diagnostic snapshots; never infer KPI scores or orders.

This is a weekly evidence report producer, not the CIO's investment analysis.
Only enumerated status values and timestamps cross the publication boundary.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

from audit.ic_weekly_control import AUTHORITY, KPI, digest, instant, produce, projection

SOURCES = {
    'crypto_runtime': ('data/latest_crypto_paper_runtime_decision.json', 'evaluation_at',
                       'decision_status', frozenset({'BLOCKED', 'AVAILABLE', 'CLASSIFIED'})),
    'market_acceptance': ('data/latest_market_scoped_pit_acceptance.json', None,
                          None, frozenset()),
    'regime_reference': ('data/latest_paper_regime_reference.json', 'generated_at',
                         'status', frozenset({'REFERENCE_AVAILABLE', 'PARTIAL_REFERENCE_AVAILABLE'})),
    'capital_flow_reference': ('data/latest_capital_flow_posture_reference.json', 'generated_at',
                               'status', frozenset({'REFERENCE_AVAILABLE', 'PARTIAL_REFERENCE_AVAILABLE'})),
}


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL)


def collect(repo, commit, start, cutoff):
    """Read blobs at exactly one commit, never working-tree files/private paths.

Source timestamps are source claims, not independent proof of availability.
Untimed and stale snapshots are reported but cannot count as weekly evidence.
"""
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('EXACT_COMMIT_REQUIRED')
    if instant(start) >= instant(cutoff):
        raise ValueError('INVALID_PERIOD')
    if git(repo, 'rev-parse', '--verify', commit + '^{commit}').decode().strip() != commit:
        raise ValueError('COMMIT_MISMATCH')
    if instant(git(repo, 'show', '-s', '--format=%cI', commit).decode().strip()) > instant(cutoff):
        raise ValueError('COMMIT_AFTER_CUTOFF')
    rows = []
    for source_id, (path, time_field, status_field, allowed) in SOURCES.items():
        row = dict(source_id=source_id, path=path, sha256=None, source_observed_at=None,
                   source_available_at=None, status='MISSING', reported_status=None,
                   validation='DIAGNOSTIC_ONLY_NOT_INDEPENDENTLY_VALIDATED')
        # Distinguish an absent blob from git/object-store failure.
        entry = git(repo, 'ls-tree', commit, '--', path).decode().strip()
        if not entry:
            rows.append(row)
            continue
        if not entry.startswith('100644 blob ') and not entry.startswith('100755 blob '):
            row['status'] = 'INVALID_SOURCE'
            rows.append(row)
            continue
        raw = git(repo, 'show', commit + ':' + path)
        row['sha256'] = hashlib.sha256(raw).hexdigest()
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                raise ValueError('OBJECT_REQUIRED')
            row['reported_status'] = obj.get(status_field) if obj.get(status_field) in allowed else None
            stamp = obj.get(time_field) if time_field else None
            if stamp is None:
                row['status'] = 'SOURCE_TIME_MISSING'
            else:
                at = instant(stamp)
                row['source_observed_at'] = at.isoformat().replace('+00:00', 'Z')
                row['status'] = ('AFTER_CUTOFF' if at > instant(cutoff) else
                                 'PRIOR_PERIOD' if at < instant(start) else 'IN_PERIOD_SNAPSHOT')
                if row['status'] == 'AFTER_CUTOFF':
                    row['reported_status'] = None
        except (ValueError, TypeError, AttributeError):
            row.update(status='INVALID_SOURCE', reported_status=None, source_observed_at=None)
        rows.append(row)
    return rows


def build_report(repo, commit, start, cutoff, generated_at):
    if instant(generated_at) < instant(cutoff):
        raise ValueError('GENERATION_BEFORE_CUTOFF')
    rows = collect(repo, commit, start, cutoff)
    bundle = dict(period_start=start, cutoff=cutoff, source_commit=commit, sources=rows)
    key = digest(bundle)
    source = dict(
        schema_version='ic_weekly_control_packet/v1', ic_id='IC-EVIDENCE-' + key,
        decision_date=instant(cutoff).date().isoformat(), generated_at=generated_at,
        kpi_deltas={k: dict(delta=None, status='NOT_COMPUTABLE',
                           basis='No validated comparable KPI baseline in diagnostic snapshots') for k in KPI},
        natural_probes=[], blocker_changes=[], system_actions=[], next_assertions=[],
        decision_routing=[dict(decision_id='CIO-ANALYSIS-' + key, created_at=generated_at,
            surfaced_to_cio_at=None, acknowledged_at=None, decided_at=None,
            canonicalized_at=None, surface_evidence=None, routing_status='created',
            purpose='Review fixed weekly diagnostic evidence; not yet delivered', requires_user_action=False)],
        authority=AUTHORITY.copy(), real_state='CLOSED',
        results=dict(investment_action_result='NOT_OBSERVED_THIS_WEEK'),
        scheduler=dict(owner='CLAUDE_CIO', schedule='MANUAL_EVIDENCE_PRODUCTION_ONLY',
                       ownership_evidence='CODEX_STANDING_QUEUE item 15; schedule not activated', scheduler_id=None),
        provenance=dict(source='atlas-data immutable public diagnostic snapshots', basis=key))
    packet = produce(source, generated_at)
    return dict(schema_version='ic_weekly_evidence_report/1', report_key=key,
                generated_at=generated_at, **bundle,
                production_status='EVIDENCE_REPORT_PRODUCED',
                analysis_status='AWAITING_CIO_ANALYSIS', delivery_status='NOT_DELIVERED',
                history_scope='LATEST_SNAPSHOT_PER_SOURCE_NOT_FULL_WEEK_HISTORY',
                availability_status='SOURCE_AVAILABILITY_NOT_INDEPENDENTLY_PROVEN',
                in_period_source_count=sum(r['status'] == 'IN_PERIOD_SNAPSHOT' for r in rows),
                packet=packet, projection=projection(packet))


def save_report(report, root):
    """Same evidence key reuses original generation, never refreshes old evidence."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / (report['report_key'] + '.json')
    if path.exists():
        old = json.loads(path.read_text())
        expected = json.loads(json.dumps(report))
        at = old['generated_at']
        if not instant(report['cutoff']) <= instant(at) <= instant(report['generated_at']):
            raise ValueError('EXISTING_GENERATION_TIME_INVALID')
        expected['generated_at'] = at
        expected['packet']['generated_at'] = at
        expected['packet']['decision_routing'][0]['created_at'] = at
        expected['projection']['generated_at'] = at
        if old != expected:
            raise ValueError('EXISTING_REPORT_CONFLICT')
        return path, 'VERIFIED_EXISTING'
    # No replace: competing writers cannot silently overwrite a report.
    with path.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write('\n')
    return path, 'CREATED'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', default='.')
    for name in ('source-commit', 'period-start', 'cutoff', 'output-dir'):
        p.add_argument('--' + name, required=True)
    args = p.parse_args()
    at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    report = build_report(args.repo, args.source_commit, args.period_start, args.cutoff, at)
    path, status = save_report(report, args.output_dir)
    print(json.dumps(dict(status=status, file=path.name,
                         sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                         analysis_status=report['analysis_status'], delivery_status=report['delivery_status'])))


if __name__ == '__main__':
    main()
