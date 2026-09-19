#!/usr/bin/env python3
"""Read-only byte-pin inventory; NOT a workflow-trigger or runtime-admission gate."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

SHA256 = re.compile(r'^[0-9a-f]{64}$')
NON_COVERAGE = [
    'Validator invocation, workflow trigger/filter semantics and conditional execution',
    'Non-JSON contracts, non-workflow pin shapes, historical-commit pins and overlays',
    'Server dispatcher pinned revisions and compatibility without supplied telemetry',
    'Unretained historical logs, runtime execution success and automatic pin refresh',
]


def inventory(root: Path) -> dict:
    root = root.resolve()
    rows, gaps = [], []
    config = root / 'config'
    if not config.is_dir():
        gaps.append({'code': 'CONFIG_DIRECTORY_MISSING', 'source': 'config'})
    def visit(obj, source, pointer):
        if isinstance(obj, list):
            for index, value in enumerate(obj):
                visit(value, source, f'{pointer}/{index}')
        elif isinstance(obj, dict):
            keys = sorted(k for k in obj if k.endswith('workflow_path') or k.endswith('workflow_sha256'))
            prefixes = sorted({k.removesuffix('path') if k.endswith('path') else k.removesuffix('sha256') for k in keys})
            for prefix in prefixes:
                path, pin = obj.get(prefix+'path'), obj.get(prefix+'sha256')
                location = f'{pointer}/{prefix}sha256'
                row = {'source': source, 'pointer': location, 'workflow_path': path, 'pinned_sha256': pin, 'live_sha256': None}
                if isinstance(path, str) and prefix+'sha256' not in obj:
                    row['status'] = 'UNPINNED_REFERENCE_DECLARED_NON_COVERAGE'
                elif not isinstance(path, str) or not isinstance(pin, str) or not SHA256.fullmatch(pin):
                    row['status'] = 'INCOMPLETE_OR_INVALID_PIN'
                elif ('\\' in path or '..' in PurePosixPath(path).parts or PurePosixPath(path).is_absolute()
                      or not path.startswith('.github/workflows/') or not path.endswith(('.yml', '.yaml'))):
                    row['status'] = 'INVALID_WORKFLOW_PATH'
                else:
                    target = root / path
                    # A path escaping through a symlink is not read.
                    if target.resolve() != target or not target.resolve().is_relative_to(root):
                        row['status'] = 'SYMLINK_OR_ESCAPING_PATH'
                    elif not target.is_file():
                        row['status'] = 'PIN_TARGET_MISSING'
                    else:
                        try:
                            live = hashlib.sha256(target.read_bytes()).hexdigest()
                            row.update(live_sha256=live, status='MATCH' if live == pin else 'STALE_PIN')
                        except OSError:
                            row['status'] = 'PIN_TARGET_UNREADABLE'
                rows.append(row)
            for key, value in sorted(obj.items()):
                escaped = key.replace('~', '~0').replace('/', '~1')
                visit(value, source, f'{pointer}/{escaped}')
    for path in sorted(config.rglob('*.json')):
        source = path.relative_to(root).as_posix()
        if path.resolve() != path:
            gaps.append({'code': 'SYMLINK_CONFIG_NOT_READ', 'source': source})
            continue
        try:
            visit(json.loads(path.read_text(encoding='utf-8')), source, '')
        except (ValueError, OSError, UnicodeError):
            gaps.append({'code': 'CONFIG_UNREADABLE', 'source': source})
    return {'schema': 'public_workflow_pin_inventory/1', 'status': 'BOUNDED_INVENTORY_ONLY',
            'pins': rows, 'counts': {s: sum(r['status'] == s for r in rows) for s in sorted({r['status'] for r in rows})},
            'gaps': gaps, 'DECLARED_NON_COVERAGE': NON_COVERAGE,
            'authority': {'pin_refresh': False, 'dispatch': False, 'compatibility_inferred': False}}


def summarize_telemetry(path: Path) -> dict:
    """Optional normalized, redacted input; never print original lines or unknown fields.

    Each row: workflow_path, pinned_sha, live_sha, decision, observed_at.
    Compatible is a source report, not a claim derived from hashes. Pin strings
    must use the same length (Git SHA1 or SHA256); malformed rows remain gaps.
    """
    groups, invalid = {}, 0
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            r = json.loads(line)
            if not isinstance(r, dict):
                raise ValueError()
            w, pin, live, decision, at = (r.get(k) for k in ('workflow_path','pinned_sha','live_sha','decision','observed_at'))
            if (not isinstance(w, str) or not re.fullmatch(r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml', w)
                or not isinstance(pin, str) or not isinstance(live, str) or len(pin) != len(live)
                or len(pin) not in (40,64) or not re.fullmatch('[0-9a-f]+',pin+live)
                or decision not in ('drift_compatible','drift_blocked','match')
                or not isinstance(at, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z', at)):
                raise ValueError()
            from datetime import datetime
            datetime.fromisoformat(at.replace('Z','+00:00'))
            if (decision == 'match') != (pin == live):
                raise ValueError()
            key = (w,pin)
            g = groups.setdefault(key, {'workflow_path':w,'pinned_sha':pin,'live_shas':set(), 'counts':{}, 'stale_observations':0})
            g['live_shas'].add(live)
            g['counts'][decision] = g['counts'].get(decision,0)+1
            g['stale_observations'] += int(pin != live)
        except (ValueError, TypeError):
            invalid += 1
    return {'source': 'SUPPLIED_NORMALIZED_TELEMETRY_NOT_INDEPENDENT_RUNTIME_PROOF',
            'invalid_rows':invalid, 'groups':[{**g,'live_shas':sorted(g['live_shas']),
                'pin_status':'STALE_PIN_REVIEW_REQUIRED' if g['stale_observations'] else 'MATCH_REPORTED',
                'compatibility':'SOURCE_REPORTED_ONLY'} for _,g in sorted(groups.items())],
            'raw_log_parser':'DECLARED_NON_COVERAGE', 'pin_refresh':False, 'suppresses_runtime_warnings':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--telemetry-jsonl',type=Path)
    args=parser.parse_args()
    result=inventory(args.root)
    if args.telemetry_jsonl:
        try:
            result['telemetry']=summarize_telemetry(args.telemetry_jsonl)
        except (OSError,UnicodeError):
            result['telemetry']={'status':'INPUT_UNREADABLE','raw_values_logged':False}
    print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True))
    # Nonzero means attention needed, never bypasses or modifies existing admission.
    return int(bool(result['gaps']) or not result['pins'] or any(r['status']!='MATCH' for r in result['pins'])
               or ('telemetry' in result and (result['telemetry'].get('status')=='INPUT_UNREADABLE'
                   or result['telemetry'].get('invalid_rows',0)>0
                   or any(g['stale_observations'] for g in result['telemetry'].get('groups',[])))))

if __name__=='__main__':
    raise SystemExit(main())
