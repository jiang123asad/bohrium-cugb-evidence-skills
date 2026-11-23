"""Tests for scripts/nanocheck.py (run: python -m unittest discover -s tests)."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import nanocheck  # noqa: E402

XSD = 'http://www.w3.org/2001/XMLSchema#'

def make_np():
    return {
        'np': {
            'id': 'urn:nanopub:local:check-demo',
            'assertion': [
                {'s': 'urn:ex:sample-1', 'p': 'urn:ex:measured-value',
                 'o': '12.5', 'o_type': 'literal', 'datatype': XSD + 'decimal'},
                {'s': 'urn:ex:sample-1', 'p': 'urn:ex:material',
                 'o': 'urn:ex:steel', 'o_type': 'uri'},
                {'s': 'urn:ex:sample-1', 'p': 'urn:ex:label',
                 'o': '样品 A', 'o_type': 'literal', 'language': 'zh'},
            ],
            'provenance': {
                'assertedBy': 'urn:agent:demo',
                'generatedAt': '2026-11-19T10:00:00+08:00',
                'sources': ['https://example.org/demo-paper'],
            },
            'pubinfo': {
                'createdBy': 'urn:agent:demo',
                'createdAt': '2026-11-19T02:00:00Z',
            },
        }
    }

def statuses(result):
    return {check['id']: check['status'] for check in result['checks']}

def detail_of(result, check_id):
    for check in result['checks']:
        if check['id'] == check_id:
            return check['detail']
    raise AssertionError(f'check not reported: {check_id}')

class ValidNanopubTests(unittest.TestCase):
    def test_valid_nanopub_is_publishable(self):
        result = nanocheck.check_document(make_np())
        self.assertTrue(result['publishable'])
        self.assertEqual([], result['summary']['failed_checks'])
        self.assertEqual([], result['summary']['warned_checks'])
        self.assertTrue(all(check['status'] == 'pass' for check in result['checks']))
        self.assertEqual(3, result['summary']['assertion_hash_lines'])
        self.assertEqual(3, result['summary']['assertion_triples'])
        self.assertRegex(result['assertion_hash_sha256'], r'^[0-9a-f]{64}$')

    def test_checks_follow_structure_template_hash_order(self):
        result = nanocheck.check_document(make_np())
        reported = [check['id'] for check in result['checks']]
        expected = [check_id for check_id in nanocheck.CHECK_ORDER
                    if check_id != 'hash.matches_expected']
        self.assertEqual(expected, reported)

class AssertionGraphTests(unittest.TestCase):
    def test_empty_assertion_blocks_publication(self):
        doc = make_np()
        doc['np']['assertion'] = []
        result = nanocheck.check_document(doc)
        self.assertFalse(result['publishable'])
        self.assertEqual('fail', statuses(result)['assertion.nonempty'])
        self.assertIn('assertion.nonempty', result['summary']['failed_checks'])
        self.assertEqual(0, result['summary']['assertion_hash_lines'])
        self.assertRegex(result['assertion_hash_sha256'], r'^[0-9a-f]{64}$')

    def test_triple_missing_object_fails(self):
        doc = make_np()
        doc['np']['assertion'].append({'s': 'urn:ex:sample-1', 'p': 'urn:ex:broken'})
        result = nanocheck.check_document(doc)
        self.assertFalse(result['publishable'])
        self.assertEqual('fail', statuses(result)['assertion.triples'])
        self.assertIn('missing o', detail_of(result, 'assertion.triples'))

    def test_duplicate_triples_are_collapsed_in_hash(self):
        doc = make_np()
        doc['np']['assertion'].append(copy.deepcopy(doc['np']['assertion'][0]))
        result = nanocheck.check_document(doc)
        self.assertTrue(result['publishable'])
        self.assertEqual(1, result['summary']['duplicate_triples'])
        self.assertEqual(3, result['summary']['assertion_hash_lines'])

class TemplateTests(unittest.TestCase):
    def test_missing_provenance_field_fails(self):
        doc = make_np()
        del doc['np']['provenance']['assertedBy']
        result = nanocheck.check_document(doc)
        self.assertFalse(result['publishable'])
        self.assertEqual('fail', statuses(result)['provenance.required'])
        self.assertIn('assertedBy', detail_of(result, 'provenance.required'))

    def test_empty_sources_need_a_documented_reason(self):
        doc = make_np()
        doc['np']['provenance']['sources'] = []
        blocked = nanocheck.check_document(doc)
        self.assertFalse(blocked['publishable'])
        self.assertEqual('fail', statuses(blocked)['provenance.sources'])

        doc['np']['provenance']['sourcesNote'] = '材料为内部未发表记录，无公开来源。'
        allowed = nanocheck.check_document(doc)
        self.assertTrue(allowed['publishable'])
        self.assertEqual('pass', statuses(allowed)['provenance.sources'])
        self.assertIn('documented reason', detail_of(allowed, 'provenance.sources'))

    def test_bad_timestamp_fails_and_date_only_passes(self):
        doc = make_np()
        doc['np']['provenance']['generatedAt'] = '2026/11/19 10:00'
        result = nanocheck.check_document(doc)
        self.assertFalse(result['publishable'])
        self.assertEqual('fail', statuses(result)['timestamps.iso8601'])
        self.assertIn('generatedAt', detail_of(result, 'timestamps.iso8601'))

        doc = make_np()
        doc['np']['provenance']['generatedAt'] = '2026-11-19'
        doc['np']['pubinfo']['createdAt'] = '2026-11-19T02:00:00.123Z'
        result = nanocheck.check_document(doc)
        self.assertTrue(result['publishable'])
        self.assertEqual('pass', statuses(result)['timestamps.iso8601'])

    def test_literal_without_datatype_is_only_a_warning(self):
        doc = make_np()
        doc['np']['assertion'].append(
            {'s': 'urn:ex:sample-1', 'p': 'urn:ex:note', 'o': 'plain text', 'o_type': 'literal'})
        result = nanocheck.check_document(doc)
        self.assertTrue(result['publishable'])
        self.assertEqual('warn', statuses(result)['assertion.literal_datatype'])
        self.assertEqual(['assertion.literal_datatype'], result['summary']['warned_checks'])
        self.assertIn('#3', detail_of(result, 'assertion.literal_datatype'))

class HashTests(unittest.TestCase):
    def test_hash_is_stable_across_key_and_triple_order(self):
        first = make_np()
        second = make_np()
        second['np']['assertion'] = [
            {'o_type': 'literal', 'language': 'zh', 'o': '样品 A',
             'p': 'urn:ex:label', 's': 'urn:ex:sample-1'},
            {'o': 'urn:ex:steel', 's': 'urn:ex:sample-1',
             'o_type': 'uri', 'p': 'urn:ex:material'},
            {'datatype': XSD + 'decimal', 'o': '12.5',
             'p': 'urn:ex:measured-value', 'o_type': 'literal', 's': 'urn:ex:sample-1'},
        ]
        a = nanocheck.check_document(first)
        b = nanocheck.check_document(second)
        self.assertEqual(a['assertion_hash_sha256'], b['assertion_hash_sha256'])
        self.assertEqual(3, a['summary']['assertion_hash_lines'])
        self.assertEqual(3, b['summary']['assertion_hash_lines'])

        changed = make_np()
        changed['np']['assertion'][0]['o'] = '12.6'
        c = nanocheck.check_document(changed)
        self.assertNotEqual(a['assertion_hash_sha256'], c['assertion_hash_sha256'])

    def test_hash_ignores_layout_whitespace_but_not_literal_content(self):
        first = make_np()
        spaced = make_np()
        spaced['np']['assertion'][1]['s'] = '  urn:ex:sample-1  '
        spaced['np']['assertion'][1]['p'] = 'urn:ex:material '
        spaced['np']['assertion'][1]['o'] = ' urn:ex:steel'
        a = nanocheck.check_document(first)
        b = nanocheck.check_document(spaced)
        self.assertEqual(a['assertion_hash_sha256'], b['assertion_hash_sha256'])

        literal_changed = make_np()
        literal_changed['np']['assertion'][2]['o'] = '样品  A'
        c = nanocheck.check_document(literal_changed)
        self.assertNotEqual(a['assertion_hash_sha256'], c['assertion_hash_sha256'])

    def test_expected_hash_is_verified_when_given(self):
        doc = make_np()
        digest = nanocheck.check_document(doc)['assertion_hash_sha256']
        matched = nanocheck.check_document(doc, expect_hash=digest)
        self.assertEqual('pass', statuses(matched)['hash.matches_expected'])
        self.assertTrue(matched['publishable'])

        mismatched = nanocheck.check_document(doc, expect_hash='0' * 64)
        self.assertEqual('fail', statuses(mismatched)['hash.matches_expected'])
        self.assertFalse(mismatched['publishable'])

class CliTests(unittest.TestCase):
    def _run(self, doc, extra=()):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        source = base / 'nanopub.json'
        target = base / 'out.json'
        source.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / 'nanocheck.py'), str(source), str(target), *extra],
            cwd=str(ROOT), capture_output=True, text=True, encoding='utf-8')
        return completed, target, base

    def test_cli_writes_report_and_hash_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            hash_input = Path(tmp) / 'lines.txt'
            completed, target, base = self._run(
                make_np(), ('--hash-input', str(hash_input)))
            self.assertEqual(0, completed.returncode, completed.stderr)
            report = json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual({'checks', 'assertion_hash_sha256', 'publishable', 'summary'},
                             set(report))
            self.assertTrue(report['publishable'])
            payload = hash_input.read_bytes()
            self.assertEqual(3, len(payload.decode('utf-8').splitlines()))
            self.assertEqual(report['assertion_hash_sha256'],
                             hashlib.sha256(payload).hexdigest())

    def test_cli_exits_nonzero_when_not_publishable(self):
        doc = make_np()
        doc['np']['assertion'] = []
        completed, target, _ = self._run(doc)
        self.assertEqual(1, completed.returncode)
        report = json.loads(target.read_text(encoding='utf-8'))
        self.assertFalse(report['publishable'])
        self.assertIn('assertion.nonempty', report['summary']['failed_checks'])

if __name__ == '__main__':
    unittest.main()
