import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from audit.ic_weekly_control import auto_queue,produce,projection,validate,routing_result

def load(name):
    s=importlib.util.spec_from_file_location(name,ROOT/'services/ic_weekly_control'/f'{name}.py')
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
hook=load('controller_hook');installer=load('install')

class ControlTests(unittest.TestCase):
    def setUp(self):
        self.p=json.loads((ROOT/'evidence/ic_weekly_control/IC6-TSM-20260904.json').read_text())
        self.at='2026-09-05T03:00:00Z'
    def test_no_action_meaningful_probe_not_fail(self):
        p=produce(self.p,self.at)
        self.assertEqual(p['results']['system_learning_result'],'PROBE_RECORDED')
        self.assertNotIn('FAIL',json.dumps(p['results']))
    def test_safe_action_queue(self):
        s=hook.consume(self.p,{},self.at)
        self.assertEqual(len(s['actions']),4)
        self.assertTrue(all(v['status']=='QUEUED' for v in s['actions'].values()))
    def test_authority_actions_never_queue(self):
        for kind in ('policy','threshold','REAL','order','trading','entry_definition','sizing','ttl','pit_acceptance'):
            a=copy.deepcopy(self.p['system_actions'][0]);a['kind']=kind
            self.assertFalse(auto_queue(a))
        for k in self.p['authority']:
            p=copy.deepcopy(self.p);p['authority'][k]=True
            with self.assertRaises(ValueError):validate(p)
    def test_required_authority_cannot_queue_even_safe_kind(self):
        self.p['system_actions'][0]['authority_required']=True
        s=hook.consume(self.p,{},self.at)
        self.assertEqual(s['actions']['IC6-a']['status'],'WAITING_USER')
        self.assertIn('IC6-a',s['user_actions'])
    def test_bool_not_truthy(self):
        for v in (1,'true',None):
            p=copy.deepcopy(self.p);p['system_actions'][0]['safe_auto_queue']=v
            with self.assertRaises(ValueError):validate(p)
    def test_missing_surface_is_routing_gap_not_cio_failure(self):
        self.assertEqual(routing_result(self.p['decision_routing'][0]),'ROUTING_EVIDENCE_MISSING')
    def test_forged_surface_rejected(self):
        self.p['decision_routing'][0].update(surfaced_to_cio_at=self.at,routing_status='surfaced_to_cio')
        with self.assertRaises(ValueError):validate(self.p)
    def test_skipped_routing_stage_rejected(self):
        self.p['decision_routing'][0]['decided_at']=self.at
        with self.assertRaises(ValueError):validate(self.p)
    def test_deterministic(self):
        self.assertEqual(produce(self.p,self.at),produce(self.p,self.at))
    def test_future_probe_rejected(self):
        for key,value in [('event_date','2026-09-10'),('observed_at','2026-09-10T00:00:00Z'),('expected_recorded_at','2026-09-10T00:00:00Z')]:
            p=copy.deepcopy(self.p);p['natural_probes'][0][key]=value
            with self.assertRaises(ValueError):produce(p,self.at)
    def test_retro_expectation_not_preevent(self):
        self.p['natural_probes'][0]['expectation_basis']='pre_event'
        with self.assertRaises(ValueError):validate(self.p)
    def test_kpi_unknown_not_zero(self):
        self.p['kpi_deltas']['profitability_evidence']['delta']=0
        with self.assertRaises(ValueError):validate(self.p)
    def test_weekly_rerun_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            hook.tick(d,self.at);hook.tick(d,'2026-09-05T04:00:00Z')
            root=Path(d)/'state/ic_weekly_control'
            s=json.loads((root/'state.json').read_text())
            self.assertEqual(len(s['actions']),4);self.assertEqual(len(s['packets']),2)
            hook.tick(d,'2026-09-12T03:00:00Z')
            s=json.loads((root/'state.json').read_text())
            self.assertEqual(len(s['actions']),4);self.assertEqual(len(s['packets']),3)
    def test_external_inbox_consumed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'state/ic_weekly_control/inbox';root.mkdir(parents=True)
            p=copy.deepcopy(self.p);p['ic_id']='IC7-external'
            (root/'packet.json').write_text(json.dumps(p))
            hook.tick(d,self.at);hook.tick(d,self.at)
            state=json.loads((root.parent/'state.json').read_text())
            self.assertIn('IC7-external',state['packets'])
            self.assertEqual(len(state['actions']),4)
    def test_changed_packet_identity_rejected(self):
        state=hook.consume(self.p,{},self.at)
        self.p['natural_probes'][0]['actual_behavior']='different'
        with self.assertRaises(ValueError):hook.consume(self.p,state,self.at)
    def test_weekday_has_no_weekly_packet(self):
        with tempfile.TemporaryDirectory() as d:
            hook.tick(d,'2026-09-07T03:00:00Z')
            self.assertEqual(len(list((Path(d)/'state/ic_weekly_control/packets').glob('*.json'))),1)
    def test_money_lane_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'state').mkdir()
            sentinels={'runtime_tasks.json':{'tasks':[{'id':'runtime-regime','lane':'money_path','status':'WORKING','pid':42}]},'cio_queue.json':{'owner':'Claude'}}
            for n,v in sentinels.items():(root/'state'/n).write_text(json.dumps(v))
            before={n:(root/'state'/n).read_bytes() for n in sentinels}
            with patch('subprocess.Popen',side_effect=AssertionError('No IC process spawning')),patch('os.kill',side_effect=AssertionError('No preemption')):
                hook.tick(d,self.at)
            self.assertEqual(before,{n:(root/'state'/n).read_bytes() for n in sentinels})
    def test_surface_receipt_and_historical_distinction(self):
        with tempfile.TemporaryDirectory() as d:
            hook.tick(d,self.at)
            v=hook.surface(d,{'user_action':'NONE'},self.at)
            self.assertIn('IC5',v['user_action'])
            root=Path(d)/'state/ic_weekly_control';s=json.loads((root/'state.json').read_text())
            for key,r in s['routing'].items():
                self.assertEqual(r['surfaced_to_cio_at'],self.at)
                self.assertIsNone(r['acknowledged_at'])
                self.assertTrue((root/r['surface_evidence']).exists())
                self.assertEqual(s['user_actions'][key]['historical_assessment'],'ROUTING_EVIDENCE_MISSING')
    def test_routing_progress_from_external_packet(self):
        state=hook.consume(self.p,{},self.at)
        p=copy.deepcopy(self.p);p['ic_id']='IC6-routing-receipt';p['generated_at']=self.at
        for row in p['decision_routing']:
            row.update(surfaced_to_cio_at=self.at,acknowledged_at=self.at,decided_at=self.at,canonicalized_at=self.at,surface_evidence='external-receipt-id',routing_status='canonicalized')
        state=hook.consume(p,state,self.at)
        self.assertEqual(state['user_actions'],{})
        self.assertTrue(all(r['assessment']=='CANONICALIZED' for r in state['routing'].values()))
    def test_changed_action_identity_rejected(self):
        s=hook.consume(self.p,{},self.at);self.p['system_actions'][0]['kind']='order'
        with self.assertRaises(ValueError):hook.consume(self.p,s,self.at)
    def test_projection_omits_private_prose(self):
        self.p['natural_probes'][0]['shadow_paper_result']='PRIVATE_PNL_SENTINEL'
        self.assertNotIn('PRIVATE_PNL',json.dumps(projection(self.p)))
    def test_unknown_fields_rejected(self):
        self.p['account_balance']=10
        with self.assertRaises(ValueError):validate(self.p)
    def test_install_is_scoped_and_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'dispatcher_tick.py').write_text('def main():\n'+installer.TICK_ANCHOR)
            (root/'atlas-status').write_text('def main():\n'+installer.STATUS_ANCHOR)
            (root/'runtime_worker.py').write_text('sentinel')
            installer.install(d);installer.install(d)
            self.assertEqual((root/'runtime_worker.py').read_text(),'sentinel')
            self.assertEqual((root/'dispatcher_tick.py').read_text().count('ic_control_bridge.tick'),1)
            self.assertLess((root/'dispatcher_tick.py').read_text().index('subprocess.Popen'),(root/'dispatcher_tick.py').read_text().index('ic_control_bridge.tick'))

if __name__=='__main__':unittest.main()
