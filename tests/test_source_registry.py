import copy
import json
from pathlib import Path
import tempfile
import unittest

from app.source_registry import get_active_source


class SourceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'sources.json'
        # A fictional reviewed public source; no merchant permission is required.
        self.source = {
            'id': 'authorized-test', 'status': 'active', 'origin': 'https://seller.example',
            'admission': {'basis': 'public endpoint reviewed', 'evidence': ['https://seller.example/catalog'],
                          'reviewed_at': '2026-10-12', 'scope': 'read-only catalog facts'},
            'allowed_endpoints': ['https://seller.example/catalog'],
            'limits': {'requests_per_minute': 6, 'concurrency': 1, 'max_requests_per_run': 4},
        }

    def write(self, sources):
        self.path.write_text(json.dumps({'sources': sources}), encoding='utf-8')

    def test_admitted_source_returns_exact_scoped_policy(self):
        self.write([self.source])
        self.assertEqual(get_active_source('authorized-test', self.path)['allowed_endpoints'],
                         ['https://seller.example/catalog'])

    def test_nonactive_and_unknown_sources_cannot_be_selected(self):
        for status in ('pending', 'paused', 'revoked', 'rejected', None):
            with self.subTest(status=status):
                source = dict(self.source, status=status)
                self.write([source])
                with self.assertRaises(PermissionError):
                    get_active_source('authorized-test', self.path)
        with self.assertRaises(PermissionError):
            get_active_source('unknown', self.path)

    def test_active_flag_without_complete_admission_evidence_is_denied(self):
        for key in ('basis', 'evidence', 'reviewed_at', 'scope'):
            with self.subTest(key=key):
                source = copy.deepcopy(self.source)
                source['admission'][key] = None
                self.write([source])
                with self.assertRaises(PermissionError):
                    get_active_source('authorized-test', self.path)

    def test_duplicate_source_identity_is_denied(self):
        self.write([dict(self.source, status='pending'), self.source])
        with self.assertRaises(PermissionError):
            get_active_source('authorized-test', self.path)

    def test_unscoped_endpoints_and_nonpositive_limits_are_denied(self):
        mutations = [
            ('allowed_endpoints', []),
            ('allowed_endpoints', ['http://seller.example/catalog']),
            ('allowed_endpoints', ['https://other.example/catalog']),
            ('origin', 'https://user:password@seller.example'),
            ('limits', {'requests_per_minute': 0, 'concurrency': 1, 'max_requests_per_run': 4}),
            ('limits', {'requests_per_minute': True, 'concurrency': 1, 'max_requests_per_run': 4}),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                self.write([dict(self.source, **{key: value})])
                with self.assertRaises(PermissionError):
                    get_active_source('authorized-test', self.path)

    def test_registry_revocation_takes_effect_on_next_selection(self):
        self.write([self.source])
        get_active_source('authorized-test', self.path)
        self.write([dict(self.source, status='revoked')])
        with self.assertRaises(PermissionError):
            get_active_source('authorized-test', self.path)

    def test_unavailable_or_malformed_registry_is_denied(self):
        for content in ('not json', 'null', '{}', '{"sources":[null]}'):
            with self.subTest(content=content):
                self.path.write_text(content, encoding='utf-8')
                with self.assertRaises(PermissionError):
                    get_active_source('authorized-test', self.path)
        self.path.unlink()
        with self.assertRaises(PermissionError):
            get_active_source('authorized-test', self.path)

    def test_nonactive_registered_sources_cannot_be_selected(self):
        registry = json.loads((Path(__file__).parents[1] / 'config/sources.json').read_text(encoding='utf-8'))
        for source in registry['sources']:
            with self.subTest(source=source['id']):
                if source['status'] == 'active':
                    get_active_source(source['id'])
                else:
                    with self.assertRaises(PermissionError):
                        get_active_source(source['id'])

    def test_initial_public_sources_can_be_selected_without_permission_emails(self):
        for source_id in ('class_cellulars', 'mojitech', 'beytech'):
            with self.subTest(source_id=source_id):
                source = get_active_source(source_id)
                self.assertTrue(source['allowed_endpoints'])


if __name__ == '__main__':
    unittest.main()
