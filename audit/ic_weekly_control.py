"""Deterministic diagnostic control loop; never an investment/order evaluator."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

AUTHORITY = {k: False for k in ('policy', 'threshold', 'REAL', 'order', 'trading')}
KPI = ('decision_readiness', 'natural_paper_readiness', 'profitability_evidence', 'limited_real_readiness')
ROUTING = ('created', 'surfaced_to_cio', 'acknowledged', 'decided', 'canonicalized')
SAFE_KINDS = {'code_enforcement_audit', 'paper_evidence_audit', 'lifecycle_aggregation_audit', 'decision_routing_audit'}

def instant(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None: raise ValueError('Explicit timezone required')
    return d.astimezone(timezone.utc)

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()

def routing_result(row):
    if not row['surfaced_to_cio_at']: return 'ROUTING_EVIDENCE_MISSING'
    if not row['acknowledged_at']: return 'SURFACED_AWAITING_ACKNOWLEDGEMENT'
    if not row['decided_at']: return 'ACKNOWLEDGED_AWAITING_DECISION'
    if not row['canonicalized_at']: return 'DECIDED_AWAITING_CANONICALIZATION'
    return 'CANONICALIZED'

def auto_queue(action):
    return (action['safe_auto_queue'] is True and action['authority_required'] is False
            and action['kind'] in SAFE_KINDS and action['owner_lane'] == 'Codex/control'
            and action['critical_path_relation'] == 'secondary_nonpreemptive')

def validate(packet):
    schema = json.loads((Path(__file__).resolve().parents[1]/'config/ic_weekly_control_packet.schema.json').read_text())
    def check(value, spec, path):
        if '$ref' in spec: return check(value, schema['$defs'][spec['$ref'].split('/')[-1]], path)
        if 'const' in spec and (type(value) is not type(spec['const']) or value != spec['const']): raise ValueError(path)
        if 'enum' in spec and value not in spec['enum']: raise ValueError(path)
        typ = spec.get('type')
        ok = {'object':lambda: isinstance(value,dict), 'array':lambda:isinstance(value,list), 'string':lambda:isinstance(value,str), 'boolean':lambda:type(value)is bool, 'null':lambda:value is None, 'number':lambda:type(value)in (float,int)}
        if typ and not any(ok[t]() for t in (typ if isinstance(typ,list) else [typ])): raise ValueError(path)
        if value is None: return
        if isinstance(value,dict):
            if set(spec.get('required',[]))-value.keys(): raise ValueError(path+' missing fields')
            if spec.get('additionalProperties') is False and value.keys()-spec.get('properties',{}).keys(): raise ValueError(path+' unknown fields')
            for k,v in value.items():
                if k in spec.get('properties',{}): check(v,spec['properties'][k],path+'.'+k)
        if isinstance(value,list):
            for v in value: check(v,spec['items'],path+'[]')
        if isinstance(value,str) and spec.get('minLength') and not value: raise ValueError(path)
    check(packet,schema,'packet')
    cutoff = instant(packet['generated_at'])
    if not re.fullmatch(r'[A-Za-z0-9_-]+',packet['ic_id']): raise ValueError('Invalid packet identity')
    datetime.strptime(packet['decision_date'],'%Y-%m-%d')
    if packet['decision_date'] > cutoff.date().isoformat(): raise ValueError('Future decision date')
    for group,key in [('natural_probes','probe_id'),('system_actions','action_id'),('decision_routing','decision_id')]:
        ids=[r[key] for r in packet[group]]
        if len(ids)!=len(set(ids)) or any(not re.fullmatch(r'[A-Za-z0-9_-]+',v) for v in ids): raise ValueError('Invalid/duplicate identity')
    for probe in packet['natural_probes']:
        datetime.strptime(probe['event_date'],'%Y-%m-%d')
        if probe['event_date'] > packet['decision_date'] or instant(probe['observed_at']) > cutoff: raise ValueError('Future observation')
        if instant(probe['expected_recorded_at']) > cutoff: raise ValueError('Future expectation')
        if probe['expectation_basis']=='pre_event' and instant(probe['expected_recorded_at']).date().isoformat()>probe['event_date']: raise ValueError('Backdated expectation')
    for row in packet['decision_routing']:
        stamps=[row[k+'_at'] for k in ROUTING]
        prior=instant(stamps[0]) if stamps[0] else None
        if prior and prior>cutoff: raise ValueError('Future creation')
        missing=False
        for stamp in stamps[1:]:
            if stamp is None: missing=True; continue
            d=instant(stamp)
            if missing or d>cutoff or (prior and d<prior): raise ValueError('Invalid routing chronology')
            prior=d
        expected=ROUTING[sum(s is not None for s in stamps[1:])]
        if row['routing_status'] != expected: raise ValueError('Unsupported routing status')
        if bool(row['surfaced_to_cio_at']) != bool(row['surface_evidence']): raise ValueError('Surface receipt required')
    for assertion in packet['next_assertions']: instant(assertion['due_at'])
    for k in packet['kpi_deltas'].values():
        if (k['status']=='NOT_COMPUTABLE') != (k['delta'] is None): raise ValueError('Unsupported KPI delta')
    return packet

def produce(source, generated_at, decision_date=None, ic_id=None):
    p=deepcopy(source)
    p.update(generated_at=generated_at,decision_date=decision_date or source['decision_date'],ic_id=ic_id or source['ic_id'])
    p['results']={'investment_action_result':source['results']['investment_action_result'],
        'system_learning_result':'PROBE_RECORDED' if p['natural_probes'] else 'NO_PROBE_EVIDENCE',
        'routing_result':'ROUTING_EVIDENCE_MISSING' if any(not r['surfaced_to_cio_at'] for r in p['decision_routing']) else 'ROUTING_RECORDED',
        'profitability_evidence_result':p['kpi_deltas']['profitability_evidence']['status']}
    return validate(p)

def projection(packet):
    validate(packet)
    return dict(schema_version='ic_control_summary/v1',eligible=True,read_only=True,
        ic_id=packet['ic_id'],generated_at=packet['generated_at'],probe_count=len(packet['natural_probes']),
        safe_action_count=sum(auto_queue(a) for a in packet['system_actions']),
        results=packet['results'],authority=AUTHORITY.copy(),real_state='CLOSED')
