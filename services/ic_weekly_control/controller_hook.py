"""Bounded local IC queue adapter; never edits or dispatches Runtime Regime workers.

Existing dispatcher invokes tick. Independent control queue reserves all existing
money-path slots: it has no process launch, cancellation or runtime queue writes.
"""
from copy import deepcopy
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import sys
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from audit.ic_weekly_control import auto_queue, digest, instant, produce, projection, routing_result, validate


def read(path, default):
    return json.loads(path.read_text()) if path.exists() else deepcopy(default)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temp.open('w') as f:
        json.dump(value,f,indent=2,ensure_ascii=False); f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.chmod(temp,0o600); os.replace(temp,path)


def consume(packet, state, at):
    """Idempotent transactional state; no commands/prompts accepted from packets."""
    validate(packet)
    if instant(at)<instant(packet['generated_at']): raise ValueError('Consumption before generation')
    state=deepcopy(state)
    for key in ('actions','routing','packets','user_actions'): state.setdefault(key,{})
    packet_key=digest(packet)
    previous=state['packets'].get(packet['ic_id'])
    if previous and previous['sha256']!=packet_key: raise ValueError('Packet identity changed; explicit revision required')
    state['packets'].setdefault(packet['ic_id'],{'sha256':packet_key,'consumed_at':at})
    for a in packet['system_actions']:
        old=state['actions'].get(a['action_id'])
        if old and old['definition_sha256']!=digest(a): raise ValueError('Action identity changed; explicit revision required')
        if old: continue
        eligible=auto_queue(a)
        state['actions'][a['action_id']]={'definition':a,'definition_sha256':digest(a),'status':'QUEUED' if eligible else 'WAITING_USER','queued_at':at if eligible else None,'owner_lane':'Codex/control','critical_path_relation':'secondary_nonpreemptive'}
        if not eligible:
            state['user_actions'][a['action_id']]={'purpose':a['purpose'],'published_at':at,'channel':'atlas-status User Action','authority_required':a['authority_required']}
    for row in packet['decision_routing']:
        # Retain actual receipts; never turn today's publication into historical delivery.
        state['routing'].setdefault(row['decision_id'],deepcopy(row))
        r=state['routing'][row['decision_id']]
        r['assessment']=routing_result(r)
        if not r['canonicalized_at']:
            state['user_actions'].setdefault(row['decision_id'],{'purpose':row['purpose'],'published_at':at,'channel':'atlas-status User Action','historical_assessment':routing_result(row)})
    state['last_consumed_at']=at
    return state


def surface(base, view, at):
    """Called only by atlas-status: timestamp durable availability, not human ACK."""
    root=Path(base)/'state/ic_weekly_control'
    if not root.exists(): return view
    with (root/'lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        state=read(root/'state.json',{})
        pending=state.get('user_actions',{})
        if not pending: return view
        view=deepcopy(view)
        labels=[f"{key}: {r['purpose']}" for key,r in pending.items()]
        view['user_action']='; '.join(([view['user_action']] if view.get('user_action') not in (None,'NONE') else [])+labels)
        receipt={'at':at,'channel':'atlas-status User Action','decision_ids':list(pending),'meaning':'published to operator view, not acknowledgement or decision'}
        receipt_id=digest(receipt)
        write(root/'surface_receipts'/f'{receipt_id}.json',receipt)
        for key in pending:
            r=state.get('routing',{}).get(key)
            if r and not r['surfaced_to_cio_at']:
                r.update(surfaced_to_cio_at=at,routing_status='surfaced_to_cio',surface_evidence=f'surface_receipts/{receipt_id}.json',assessment='SURFACED_AWAITING_ACKNOWLEDGEMENT')
        write(root/'state.json',state)
    return view


def summary(base):
    state=read(Path(base)/'state/ic_weekly_control/state.json',{})
    actions=state.get('actions',{})
    counts={name:sum(a['status']==name for a in actions.values()) for name in ('QUEUED','WAITING_USER')}
    return ('IC CONTROL (SECONDARY)\n'
            f"Queue             : {counts['QUEUED']} safe audit actions; {counts['WAITING_USER']} awaiting user\n"
            'Money Lane        : RESERVED; IC has no worker dispatch/preemption authority\n'
            'Weekly Owner      : external ChatGPT IC; existing controller hook\n'
            'REAL              : CLOSED')


def tick(base, at, seed=None):
    root=Path(base)/'state/ic_weekly_control'; root.mkdir(parents=True,exist_ok=True)
    with (root/'lock').open('a+') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return {'status':'BUSY'}
        state=read(root/'state.json',{})
        source=read(Path(seed) if seed else ROOT/'evidence/ic_weekly_control/IC6-TSM-20260904.json',{})
        validate(source)
        date=instant(at).astimezone(ZoneInfo('Asia/Seoul'))
        week=date.date().isoformat()
        packets=[]
        # Existing external IC owner deposits public-safe packets atomically here.
        for incoming in sorted((root/'inbox').glob('*.json')):
            candidate=read(incoming,{})
            validate(candidate)
            packets.append(candidate)
        if source['ic_id'] not in state.get('packets',{}): packets.append(source)
        weekly=root/'packets'/f'IC-WEEK-{week}.json'
        if date.weekday()==5 and not weekly.exists():
            p=deepcopy(source)
            # Carry unresolved probes explicitly; never fabricate new weekly output.
            for k in p['kpi_deltas'].values(): k.update(delta=None,status='NOT_COMPUTABLE',basis='Weekly carry-forward; fresh comparable evidence not supplied')
            p['results']['investment_action_result']='NOT_OBSERVED_THIS_WEEK'
            for r in p['natural_probes']: r['status']='CARRIED_FORWARD_AWAITING_FRESH_EVIDENCE'
            # UTC generated_at may fall on the previous calendar date in Korea.
            p=produce(p,at,instant(at).date().isoformat(),'IC-WEEK-'+week)
            packets.append(p)
        for existing in sorted((root/'packets').glob('*.json')):
            prior=read(existing,{})
            if prior.get('ic_id') not in state.get('packets',{}): packets.append(prior)
        for p in packets:
            state=consume(p,state,at)
            write(root/'packets'/f"{p['ic_id']}.json",p)
        write(root/'state.json',state)
        write(root/'projection.json',projection(packets[-1] if packets else source))
        write(root/'scheduler.json',source['scheduler'])
        return {'status':'OK','queued':[k for k,v in state.get('actions',{}).items() if v['status']=='QUEUED']}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--base',required=True);parser.add_argument('--at',required=True)
    a=parser.parse_args();print(json.dumps(tick(a.base,a.at)))
