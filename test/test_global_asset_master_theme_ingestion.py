"""Pure candidate construction through the real GAM and taxonomy validators."""
from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gam_ingestion_fixture", ROOT / "test" / "test_global_asset_master.py")
F = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(F)
from universe import global_asset_master_theme_ingestion as I


class ThemeIngestionTests(unittest.TestCase):
    def setUp(self):
        self.master = F.binding_master_input()
        self.expected_row = copy.deepcopy(F.bound_membership(self.master))
        record = next(r for r in self.master['records'] if r['asset_id'] == 'US:XNAS:TEST')
        record['memberships'].remove(F.bound_membership(self.master))
        self.graph = F.taxonomy_fixture()
        self.requests = [{**F.binding_reference(), 'gam_source_identity': F.bound_theme_source()}]

    def repo(self, **kwargs):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return F.AuthorityRepo(Path(temp.name), self.graph, **kwargs)

    def build(self, repo, **overrides):
        args = dict(master_source=self.master, taxonomy_source=self.graph,
                    requests=self.requests, trusted_commit=repo.head(),
                    authority_registry_path=repo.registry_path)
        args.update(overrides)
        return I.build_theme_ingestion_preview(**args)

    def destination(self, packet):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'master.json'
        F.GAM.write_json_atomic(path, packet)
        return path

    def apply_args(self, repo, preview, path, expected, **overrides):
        args = dict(preview=preview, master_source=self.master, taxonomy_source=self.graph,
                    requests=self.requests, trusted_commit=repo.head(),
                    authority_registry_path=repo.registry_path, destination_path=path,
                    expected_previous_master_sha256=expected,
                    operational_application_approved=True)
        args.update(overrides)
        return args

    def assert_blocked(self, result, reason):
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIsNone(result['candidate_master'])
        self.assertIsNone(result['change'])
        self.assertEqual(result['addition_count'], 0)
        self.assertTrue(any(reason in r for r in result['failure_reasons']), result['failure_reasons'])

    def test_real_row_creation_and_exact_legacy_output(self):
        result = self.build(self.repo())
        self.assertEqual(result['status'], 'STRUCTURAL_PREVIEW')
        self.assertEqual(result['change'], 'APPEND')
        self.assertEqual(result['addition_count'], 1)
        self.assertEqual(result['unchanged_count'], 0)
        candidate = result['candidate_master']
        self.assertEqual(F.bound_membership(candidate), self.expected_row)
        self.assertEqual(candidate, F.GAM.build_master(F.binding_master_input()))
        self.assertFalse(result['authority']['master_population_authorized'])
        self.assertEqual(candidate['authority'], F.GAM.build_master(self.master)['authority'])

    def test_originals_and_existing_rows_are_unchanged_and_detached(self):
        before = copy.deepcopy((self.master, self.graph, self.requests))
        result = self.build(self.repo())
        self.assertEqual((self.master, self.graph, self.requests), before)
        result['candidate_master']['records'][0]['display_name'] = 'edited'
        result['taxonomy_memberships'][0]['evidence'].clear()
        result['requests'][0]['gam_source_identity'].clear()
        self.assertEqual((self.master, self.graph, self.requests), before)

    def test_repeat_with_candidate_is_no_change(self):
        repo = self.repo()
        first = self.build(repo)
        second = self.build(repo, master_source=first['candidate_master'])
        self.assertEqual(second['change'], 'NO_CHANGE')
        self.assertEqual(second['addition_count'], 0)
        self.assertEqual(second['unchanged_count'], 1)
        self.assertEqual(second['candidate_master'], first['candidate_master'])

    def test_all_selected_evidence_role_and_source_labels_survive(self):
        extra = copy.deepcopy(self.graph['memberships'][0]['evidence'][0])
        extra['evidence_id'] = 'EVIDENCE.US.TEST.SECOND'
        self.graph['memberships'][0]['evidence'].append(extra)
        repo = self.repo()
        result = self.build(repo)
        rebuilt = F.TT.build_packet(self.graph, trusted_commit=repo.head(), authority_registry_path=repo.registry_path)
        expected = next(m for m in rebuilt['memberships'] if m['membership_id'] == 'MEMBERSHIP.US.TEST')
        self.assertEqual(result['taxonomy_memberships'], [expected])
        self.assertEqual(len(expected['evidence']), 2)
        self.assertEqual(expected['role_id'], 'COMPUTE_VENDOR')
        binding = result['binding_report']['bindings'][0]
        self.assertTrue(binding['verified'])
        self.assertEqual(binding['source_id_comparison'], 'COMPARED')
        self.assertEqual(binding['failure_reasons'], [])
        self.assertEqual(result['binding_report']['verified_binding_count'], 1)
        self.assertNotIn('SOURCE_ID_REGISTRY_CROSS_MAPPING_UNRATIFIED', result['unresolved_boundaries'])

    def test_current_committed_empty_registry_blocks(self):
        result = I.build_theme_ingestion_preview(self.master, self.graph, self.requests,
                                                trusted_commit=F.repository_head())
        self.assert_blocked(result, 'TAXONOMY_SOURCE_NOT_AUTHORIZED')

    def test_unratified_authority_blocks(self):
        self.assert_blocked(self.build(self.repo(status='PROPOSED')), 'TAXONOMY_SOURCE_NOT_AUTHORIZED')

    def test_wrong_document_and_hash_block(self):
        repo = self.repo()
        for field, value in [('source_url', 'https://www.sec.gov/Archives/wrong'), ('source_sha256', 'a' * 64)]:
            with self.subTest(field=field):
                requests = copy.deepcopy(self.requests)
                requests[0]['gam_source_identity'][field] = value
                self.assert_blocked(self.build(repo, requests=requests), 'SOURCE_EVIDENCE_MISMATCH:' + field)

    def test_missing_evidence_and_wrong_theme_block(self):
        repo = self.repo()
        for field, value, reason in [('evidence_id', 'EVIDENCE.ABSENT', 'TAXONOMY_EVIDENCE_NOT_FOUND'),
                                     ('gam_membership_id', 'SEGMENT.POWER', 'THEME_IDENTITY_MISMATCH')]:
            with self.subTest(field=field):
                requests = copy.deepcopy(self.requests)
                requests[0][field] = value
                self.assert_blocked(self.build(repo, requests=requests), reason)

    def test_wrong_asset_and_market_block(self):
        self.requests[0]['taxonomy_membership_id'] = 'MEMBERSHIP.KR.005930'
        result = self.build(self.repo())
        self.assert_blocked(result, 'ASSET_ID_MISMATCH')
        self.assert_blocked(result, 'MARKET_MISMATCH')

    def test_date_and_future_interval_block(self):
        repo = self.repo()
        self.master['as_of_date'] = '2026-08-21'
        self.assert_blocked(self.build(repo), 'AS_OF_DATE_MISMATCH')
        self.master['as_of_date'] = '2026-08-19'
        self.assert_blocked(self.build(repo), 'GAM_MEMBERSHIP_NOT_ACTIVE')

    def test_conflicting_existing_interval_or_source_raises_without_mutation(self):
        repo = self.repo()
        for field, value in [('valid_from', '2026-08-19'), ('source_identity', F.source('nasdaq_trader_symbol_directory'))]:
            with self.subTest(field=field):
                master = F.binding_master_input()
                F.bound_membership(master)[field] = value
                before = copy.deepcopy(master)
                with self.assertRaises(I.AssetMasterError):
                    self.build(repo, master_source=master)
                self.assertEqual(master, before)

    def test_nonoverlapping_history_is_preserved_but_ambiguous_binding_blocks(self):
        master = F.binding_master_input()
        old = F.bound_membership(master)
        old['valid_from'], old['valid_to'] = '2026-08-18', '2026-08-19'
        before = copy.deepcopy(master)
        self.assert_blocked(self.build(self.repo(), master_source=master), 'GAM_THEME_MEMBERSHIP_AMBIGUOUS')
        self.assertEqual(master, before)

    def test_missing_targets_unknown_source_and_invalid_requests_raise(self):
        repo = self.repo()
        cases = [[], {}, self.requests * 2]
        for field, value in [('asset_id', 'US:XNAS:ABSENT'), ('taxonomy_membership_id', 'MEMBERSHIP.ABSENT'),
                             ('evidence_id', '')]:
            request = copy.deepcopy(self.requests)
            request[0][field] = value
            cases.append(request)
        request = copy.deepcopy(self.requests)
        request[0]['gam_source_identity']['source_id'] = 'invented'
        cases.append(request)
        for field in self.requests[0]:
            request = copy.deepcopy(self.requests)
            del request[0][field]
            cases.append(request)
        for request in cases:
            with self.subTest(request=request), self.assertRaises(I.AssetMasterError):
                self.build(repo, requests=request)
        for commit in [None, 'HEAD', 'a' * 7]:
            with self.subTest(commit=commit), self.assertRaises(I.AssetMasterError):
                self.build(repo, trusted_commit=commit)

    def test_request_order_is_deterministic_and_batch_failure_is_atomic(self):
        repo = self.repo()
        second = copy.deepcopy(self.requests[0])
        second['gam_membership_id'] = 'SEGMENT.POWER'
        requests = self.requests + [second]
        forward = self.build(repo, requests=requests)
        reverse = self.build(repo, requests=list(reversed(requests)))
        self.assertEqual(forward, reverse)
        self.assert_blocked(forward, 'THEME_IDENTITY_MISMATCH')
        self.assertEqual(forward['unchanged_count'], 0)

    def test_successful_multi_asset_batch_is_order_independent(self):
        member = self.graph['memberships'][1]
        identity = copy.deepcopy(member['evidence'][0]['source_identity'])
        # Literal original DART disclosure identity; no provider alias is used.
        second = dict(asset_id='KR:XKRX:005930', gam_membership_id='SEGMENT.POWER',
                      taxonomy_membership_id='MEMBERSHIP.KR.005930',
                      evidence_id='EVIDENCE.KR.005930', gam_source_identity=identity)
        repo = self.repo()
        first = self.build(repo, requests=self.requests + [second])
        self.assertEqual(first, self.build(repo, requests=[second] + self.requests))
        self.assertEqual(first['addition_count'], 2)
        self.assertEqual(first['status'], 'STRUCTURAL_PREVIEW')
        kr = next(r for r in first['candidate_master']['records'] if r['asset_id'] == second['asset_id'])
        self.assertIn(dict(membership_type='THEME', membership_id='SEGMENT.POWER',
                           valid_from='2026-08-20', valid_to=None, source_identity=identity), kr['memberships'])

    def test_literal_disclosure_preview_binding_is_verified_without_authority_expansion(self):
        for source_id, url in [
            ('sec_edgar', 'https://www.sec.gov/Archives/edgar/data/1/EVIDENCE.US.TEST.htm'),
            ('microsoft_sec_issuer_disclosure', 'https://www.sec.gov/Archives/edgar/data/1/EVIDENCE.US.TEST.htm'),
            ('tsmc_investor_relations', 'https://investor.tsmc.com/fixture.htm'),
        ]:
            with self.subTest(source_id=source_id):
                identity = self.graph['memberships'][0]['evidence'][0]['source_identity']
                identity.update(source_id=source_id, source_url=url)
                self.requests[0]['gam_source_identity'] = copy.deepcopy(identity)
                originals = copy.deepcopy((self.master, self.graph, self.requests))
                repo = self.repo()
                result = self.build(repo)
                self.assertEqual(F.bound_membership(result['candidate_master'])['source_identity'], identity)
                self.assertEqual(result['binding_report']['status'], 'THEME_SOURCE_BINDING_VERIFIED')
                self.assertEqual(result['binding_report']['verified_binding_count'], 1)
                self.assertFalse(result['authority']['master_population_authorized'])
                self.assertFalse(result['authority']['trading_authorized'])
                self.assertEqual((self.master, self.graph, self.requests), originals)

    def test_role_market_host_and_source_mismatch_fail_closed(self):
        repo = self.repo()
        for field, value in [('source_id', 'nasdaq_trader_symbol_directory'),
                             ('source_id', 'dart_open_api'),
                             ('source_url', 'https://example.invalid/forged')]:
            with self.subTest(field=field, value=value):
                requests = copy.deepcopy(self.requests)
                requests[0]['gam_source_identity'][field] = value
                with self.assertRaisesRegex(I.AssetMasterError, 'THEME_SOURCE_INVALID'):
                    self.build(repo, requests=requests)
        requests = copy.deepcopy(self.requests)
        requests[0]['gam_source_identity']['source_id'] = 'microsoft_sec_issuer_disclosure'
        self.assert_blocked(self.build(repo, requests=requests), 'SOURCE_EVIDENCE_MISMATCH:source_id')

    def test_validator_rederives_and_rejects_rehashed_tampering(self):
        repo = self.repo()
        preview = self.build(repo)
        kwargs = dict(trusted_commit=repo.head(), authority_registry_path=repo.registry_path)
        self.assertEqual(I.validate_theme_ingestion_preview(preview, self.master, self.graph, self.requests, **kwargs), preview)
        def edit_candidate(p):
            F.bound_membership(p['candidate_master'])['valid_from'] = '2026-08-19'
            p['candidate_master'] = F.rehash(p['candidate_master'])
        mutations = [edit_candidate,
                     lambda p: p['authority'].update(master_population_authorized=True),
                     lambda p: p.update(addition_count=99),
                     lambda p: p['taxonomy_memberships'][0]['evidence'].clear(),
                     lambda p: p['binding_report']['bindings'][0].update(verified=False),
                     lambda p: p['input_digests'].update(trusted_commit='a' * 40)]
        for mutate in mutations:
            forged = copy.deepcopy(preview)
            mutate(forged)
            forged = F.rehash(forged)
            with self.subTest(mutation=mutate), self.assertRaisesRegex(I.AssetMasterError, 'DERIVATION_MISMATCH'):
                I.validate_theme_ingestion_preview(forged, self.master, self.graph, self.requests, **kwargs)

    def test_rehashed_input_packet_and_malformed_taxonomy_are_rejected(self):
        repo = self.repo()
        packet = F.GAM.build_master(self.master)
        packet['authority']['investable_eligible'] = True
        with self.assertRaises(I.AssetMasterError):
            self.build(repo, master_source=F.rehash(packet))
        graph = copy.deepcopy(self.graph)
        graph['memberships'][0]['evidence'] = []
        with self.assertRaisesRegex(I.AssetMasterError, 'TAXONOMY_SOURCE_INVALID'):
            self.build(repo, taxonomy_source=graph)

    def test_application_append_and_rebuilt_no_change(self):
        repo = self.repo()
        original = F.GAM.build_master(self.master)
        path = self.destination(original)
        preview = self.build(repo)
        result = I.apply_theme_ingestion_preview(**self.apply_args(repo, preview, path, original['payload_sha256']))
        self.assertEqual(result['outcome'], 'APPLIED_APPEND')
        self.assertTrue(result['published'])
        self.assertEqual(result['change'], 'APPEND')
        self.assertEqual(result['addition_count'], 1)
        self.assertEqual(result['previous_master'], {k: original[k] for k in I.MASTER_IDENTITY_FIELDS})
        published = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(published, preview['candidate_master'])
        self.assertEqual(result['master'], {k: published[k] for k in I.MASTER_IDENTITY_FIELDS})
        self.assertEqual(F.bound_membership(published), self.expected_row)
        # NO_CHANGE only via a preview the caller explicitly rebuilt from the
        # new destination, with the updated expected digest.
        rebuilt = self.build(repo, master_source=published)
        self.assertEqual(rebuilt['change'], 'NO_CHANGE')
        before = path.read_bytes()
        repeat = I.apply_theme_ingestion_preview(**self.apply_args(
            repo, rebuilt, path, published['payload_sha256'], master_source=published))
        self.assertEqual(repeat['outcome'], 'APPLIED_NO_CHANGE')
        self.assertFalse(repeat['published'])
        self.assertEqual(repeat['unchanged_count'], 1)
        self.assertEqual(repeat['previous_master'], repeat['master'])
        self.assertEqual(path.read_bytes(), before)

    def test_application_stale_preview_conflicts(self):
        repo = self.repo()
        original = F.GAM.build_master(self.master)
        path = self.destination(original)
        stale = self.build(repo)
        applied = I.apply_theme_ingestion_preview(**self.apply_args(repo, stale, path, original['payload_sha256']))
        self.assertEqual(applied['outcome'], 'APPLIED_APPEND')
        after = path.read_bytes()
        published = json.loads(path.read_text(encoding='utf-8'))
        cases = [
            ('EXPECTED_PREVIOUS_MASTER_MISMATCH', dict(expected_previous_master_sha256=original['payload_sha256'])),
            ('ORIGINAL_MASTER_MISMATCH:payload_sha256', dict(expected_previous_master_sha256=published['payload_sha256'])),
            ('INGESTION_PREVIEW_DERIVATION_MISMATCH', dict(expected_previous_master_sha256=published['payload_sha256'],
                                                           master_source=published)),
        ]
        for reason, override in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(I.AssetMasterError, reason):
                I.apply_theme_ingestion_preview(**self.apply_args(
                    repo, stale, path, original['payload_sha256'], **override))
        self.assertEqual(path.read_bytes(), after)

    def test_application_approval_and_destination_guards(self):
        repo = self.repo()
        original = F.GAM.build_master(self.master)
        path = self.destination(original)
        preview = self.build(repo)
        before = path.read_bytes()
        digest = original['payload_sha256']
        cases = [
            ('APPROVAL_NOT_EXACTLY_TRUE', dict(operational_application_approved=False)),
            ('APPROVAL_NOT_EXACTLY_TRUE', dict(operational_application_approved=1)),
            ('APPROVAL_NOT_EXACTLY_TRUE', dict(operational_application_approved='true')),
            ('APPROVAL_NOT_EXACTLY_TRUE', dict(operational_application_approved=None)),
            ('DESTINATION_PATH_REQUIRED', dict(destination_path=None)),
            ('DESTINATION_PATH_REQUIRED', dict(destination_path='   ')),
            ('DESTINATION_NOT_AN_EXISTING_FILE', dict(destination_path=path.parent / 'absent.json')),
            ('DESTINATION_NOT_AN_EXISTING_FILE', dict(destination_path=path.parent)),
            ('DESTINATION_NOT_AN_EXISTING_FILE', dict(destination_path=path.parent / 'missing' / 'master.json')),
            ('EXPECTED_PREVIOUS_SHA256_INVALID', dict(expected_previous_master_sha256=None)),
            ('EXPECTED_PREVIOUS_SHA256_INVALID', dict(expected_previous_master_sha256='ABSENT')),
            ('EXPECTED_PREVIOUS_MASTER_MISMATCH', dict(expected_previous_master_sha256='a' * 64)),
        ]
        for reason, override in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(I.AssetMasterError, reason):
                I.apply_theme_ingestion_preview(**self.apply_args(repo, preview, path, digest, **override))
        blocked_repo = self.repo(status='PROPOSED')
        blocked = self.build(blocked_repo)
        with self.assertRaisesRegex(I.AssetMasterError, 'PREVIEW_NOT_APPLICABLE'):
            I.apply_theme_ingestion_preview(**self.apply_args(blocked_repo, blocked, path, digest))
        # Nothing is initialized, defaulted or marked where a target was absent.
        self.assertFalse((path.parent / 'absent.json').exists())
        self.assertFalse((path.parent / 'missing').exists())
        self.assertEqual(path.read_bytes(), before)

    def test_application_original_identity_mismatch_preserves_destination(self):
        repo = self.repo()
        preview = self.build(repo)
        other_id = copy.deepcopy(self.master)
        other_id['master_id'] = 'ATLAS_OTHER_ASSETS'
        other_date = copy.deepcopy(self.master)
        other_date['as_of_date'] = '2026-08-21'
        cases = [
            ('master_id', F.GAM.build_master(other_id)),
            ('as_of_date', F.GAM.build_master(other_date)),
            # Same identity, already-appended content: a changed previous master.
            ('payload_sha256', F.GAM.build_master(F.binding_master_input())),
        ]
        for field, packet in cases:
            with self.subTest(field=field):
                path = self.destination(packet)
                before = path.read_bytes()
                with self.assertRaisesRegex(I.AssetMasterError, f'ORIGINAL_MASTER_MISMATCH:{field}'):
                    I.apply_theme_ingestion_preview(**self.apply_args(
                        repo, preview, path, packet['payload_sha256']))
                self.assertEqual(path.read_bytes(), before)

    def test_application_cooperative_writers_are_serialized(self):
        repo = self.repo()
        original = F.GAM.build_master(self.master)
        path = self.destination(original)
        preview = self.build(repo)
        args = self.apply_args(repo, preview, path, original['payload_sha256'])
        before = path.read_bytes()
        started = [threading.Event(), threading.Event()]
        outcomes, failures = {}, {}

        def worker(index):
            started[index].set()
            try:
                outcomes[index] = I.apply_theme_ingestion_preview(**args)
            except I.AssetMasterError as exc:
                failures[index] = str(exc)

        threads = [threading.Thread(target=worker, args=(index,), daemon=True) for index in (0, 1)]
        with I._destination_apply_lock(path):
            for thread in threads:
                thread.start()
            for event in started:
                self.assertTrue(event.wait(30))
            # Neither cooperating caller can publish while the boundary is held.
            self.assertEqual(path.read_bytes(), before)
        for thread in threads:
            thread.join(120)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 1, (outcomes, failures))
        self.assertEqual(len(failures), 1, (outcomes, failures))
        self.assertEqual(next(iter(outcomes.values()))['outcome'], 'APPLIED_APPEND')
        self.assertIn('EXPECTED_PREVIOUS_MASTER_MISMATCH', next(iter(failures.values())))
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), preview['candidate_master'])

    def test_application_symlink_alias_writers_are_serialized(self):
        repo = self.repo()
        original = F.GAM.build_master(self.master)
        path = self.destination(original).resolve(strict=True)
        alias = path.with_name('master-alias.json')
        alias.symlink_to(path)
        preview = self.build(repo)
        publish_entered, release_publish = threading.Event(), threading.Event()
        outcomes, failures = {}, {}
        write_json_atomic = I.GAM.write_json_atomic

        def paused_publish(destination, value):
            publish_entered.set()
            if not release_publish.wait(10):
                raise RuntimeError('test publication release timed out')
            write_json_atomic(destination, value)

        def worker(index, destination):
            try:
                outcomes[index] = I.apply_theme_ingestion_preview(**self.apply_args(
                    repo, preview, destination, original['payload_sha256']))
            except Exception as exc:
                failures[index] = exc

        threads = []
        with mock.patch.object(I.GAM, 'write_json_atomic', paused_publish):
            try:
                # Ensure the alias caller has validated the old master before
                # the real-path caller starts; both then compete to publish.
                threads.append(threading.Thread(target=worker, args=(0, alias), daemon=True))
                threads[0].start()
                self.assertTrue(publish_entered.wait(10))
                threads.append(threading.Thread(target=worker, args=(1, path), daemon=True))
                threads[1].start()
            finally:
                release_publish.set()
                for thread in threads:
                    thread.join(10)
                    self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 1, (outcomes, failures))
        self.assertEqual(len(failures), 1, (outcomes, failures))
        result = next(iter(outcomes.values()))
        self.assertEqual(result['outcome'], 'APPLIED_APPEND')
        self.assertEqual(result['destination_path'], str(path))
        failure = next(iter(failures.values()))
        self.assertIsInstance(failure, I.AssetMasterError)
        self.assertIn('EXPECTED_PREVIOUS_MASTER_MISMATCH', str(failure))
        self.assertTrue(alias.is_symlink())
        self.assertEqual(alias.read_bytes(), path.read_bytes())
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), preview['candidate_master'])
        self.assertFalse(I._lock_path(alias).exists())

    def test_application_pre_publish_failure_preserves_destination(self):
        repo = self.repo()
        original = F.GAM.build_master(self.master)
        path = self.destination(original)
        preview = self.build(repo)
        before = path.read_bytes()
        published = []

        def fail(destination, value):
            published.append((Path(destination), value))
            raise OSError('injected pre-publish failure')

        with mock.patch.object(I.GAM, 'write_json_atomic', fail):
            with self.assertRaisesRegex(OSError, 'injected pre-publish failure'):
                I.apply_theme_ingestion_preview(**self.apply_args(repo, preview, path, original['payload_sha256']))
        self.assertEqual(published, [(path.resolve(strict=True), preview['candidate_master'])])
        self.assertEqual(path.read_bytes(), before)
        expected_files = {path.name, I._lock_path(path).name}
        self.assertEqual([p.name for p in path.parent.iterdir() if p.name not in expected_files], [])
        # The boundary is released, so the same application still succeeds.
        result = I.apply_theme_ingestion_preview(**self.apply_args(repo, preview, path, original['payload_sha256']))
        self.assertEqual(result['outcome'], 'APPLIED_APPEND')
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), preview['candidate_master'])


if __name__ == '__main__':
    unittest.main()
