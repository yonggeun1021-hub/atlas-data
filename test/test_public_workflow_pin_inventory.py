import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.public_workflow_pin_inventory import inventory, summarize_telemetry

class WorkflowPinInventoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        (self.root/'config').mkdir();(self.root/'.github/workflows').mkdir(parents=True)
        self.path='.github/workflows/a.yml';(self.root/self.path).write_text('name: a\n')
        self.sha=hashlib.sha256((self.root/self.path).read_bytes()).hexdigest()
    def put(self,obj):
        (self.root/'config/pins.json').write_text(json.dumps(obj))
    def pair(self,**kw):
        return {'workflow_path':self.path,'workflow_sha256':self.sha,**kw}
    def test_nested_four_pairs_and_prefixed_receipt(self):
        self.put({'markets':[self.pair() for _ in range(4)],'receipt':{'natural_receipt_workflow_path':self.path,'natural_receipt_workflow_sha256':self.sha}})
        r=inventory(self.root);self.assertEqual(r['counts'],{'MATCH':5});self.assertEqual(len({x['pointer'] for x in r['pins']}),5)
    def test_changed_bytes_stale_not_compatible(self):
        self.put(self.pair());(self.root/self.path).write_text('name: b\n')
        r=inventory(self.root);self.assertEqual(r['pins'][0]['status'],'STALE_PIN');self.assertFalse(r['authority']['compatibility_inferred'])
    def test_missing_target_remains_visible(self):
        self.put(self.pair());(self.root/self.path).unlink()
        self.assertEqual(inventory(self.root)['counts'],{'PIN_TARGET_MISSING':1})
    def test_half_pairs_and_bad_digest(self):
        self.put([{'workflow_path':self.path},{'workflow_sha256':self.sha},self.pair(workflow_sha256='bad')])
        self.assertEqual(inventory(self.root)['counts'],{'INCOMPLETE_OR_INVALID_PIN':2,'UNPINNED_REFERENCE_DECLARED_NON_COVERAGE':1})
    def test_path_escape_not_read(self):
        self.put(self.pair(workflow_path='../outside.yml'))
        self.assertEqual(inventory(self.root)['counts'],{'INVALID_WORKFLOW_PATH':1})
    def test_symlink_not_read(self):
        (self.root/self.path).unlink();(self.root/self.path).symlink_to(self.root/'config/pins.json');self.put(self.pair())
        self.assertEqual(inventory(self.root)['counts'],{'SYMLINK_OR_ESCAPING_PATH':1})
    def test_malformed_source_visible(self):
        (self.root/'config/pins.json').write_text('{')
        self.assertEqual(inventory(self.root)['gaps'][0]['code'],'CONFIG_UNREADABLE')
    def test_non_workflow_not_claimed(self):
        self.put({'path':'x.py','sha256':self.sha})
        r=inventory(self.root);self.assertEqual(r['pins'],[]);self.assertTrue(r['DECLARED_NON_COVERAGE'])
    def test_no_mutation(self):
        self.put(self.pair());before=(self.root/'config/pins.json').read_bytes();inventory(self.root)
        self.assertEqual(before,(self.root/'config/pins.json').read_bytes())
    def test_telemetry_twenty_two_compatible_and_live_changes(self):
        p=self.root/'events.jsonl';p.write_text('\n'.join(json.dumps({'workflow_path':self.path,'pinned_sha':'0'*40,'live_sha':('1' if i<11 else '2')*40,'decision':'drift_compatible','observed_at':'2026-09-19T01:00:00Z'}) for i in range(22)))
        r=summarize_telemetry(p);g=r['groups'][0];self.assertEqual(g['stale_observations'],22);self.assertEqual(len(g['live_shas']),2);self.assertEqual(g['pin_status'],'STALE_PIN_REVIEW_REQUIRED');self.assertFalse(r['suppresses_runtime_warnings'])
    def test_blocked_stays_visible_not_folded_into_compatible(self):
        p=self.root/'events.jsonl';p.write_text(json.dumps({'workflow_path':self.path,'pinned_sha':'0'*40,'live_sha':'1'*40,'decision':'drift_blocked','observed_at':'2026-09-19T01:00:00Z'}))
        self.assertEqual(summarize_telemetry(p)['groups'][0]['counts'],{'drift_blocked':1})
    def test_invalid_rows_and_secret_not_echoed(self):
        p=self.root/'events.jsonl';p.write_text('SECRET\n'+json.dumps({'token':'SECRET'})+'\n'+json.dumps({'workflow_path':self.path,'pinned_sha':'0'*40,'live_sha':'1'*40,'decision':'match','observed_at':'2026-99-99T00:00:00Z'}))
        r=summarize_telemetry(p);self.assertEqual(r['invalid_rows'],3);self.assertNotIn('SECRET',json.dumps(r))

if __name__=='__main__':unittest.main()
