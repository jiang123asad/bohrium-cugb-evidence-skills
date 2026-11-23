import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import glean


def graph(entities, relations):
    return {'entities': [{'id': i, 'name': n, 'type': 'method', 'description': ''}
                         for i, n in entities],
            'relations': relations}


class GleanTest(unittest.TestCase):
    def test_supported_relation_with_normalised_whitespace(self):
        source = ('Random Forest improves yield prediction.\n'
                  'Yield prediction is evaluated on the Dryland Demo dataset.\n')
        model = graph([('E1', 'Random Forest'), ('E2', 'yield prediction'),
                       ('E3', 'Dryland Demo')],
                      [{'id': 'R1', 'head': 'E1', 'tail': 'E2', 'type': 'improves',
                        'evidence': 'Random Forest\nimproves   yield prediction.'},
                       {'id': 'R2', 'head': 'E2', 'tail': 'E3', 'type': 'evaluated_on',
                        'evidence': 'Yield prediction is evaluated on the Dryland Demo dataset.'}])
        result = glean.analyse(model, source, rounds=2)
        statuses = {row['id']: row['status'] for row in result['relations']}
        self.assertEqual(statuses, {'R1': 'supported', 'R2': 'supported'})
        self.assertEqual(result['summary'],
                         {'supported': 2, 'unsupported': 0, 'candidate': 0, 'rounds': 2})
        self.assertEqual(result['merges'], [])

    def test_unsupported_evidence_is_reported_not_dropped(self):
        source = 'Random Forest improves yield prediction.\n'
        model = graph([('E1', 'Random Forest'), ('E2', 'yield prediction')],
                      [{'id': 'R1', 'head': 'E1', 'tail': 'E2', 'type': 'improves',
                        'evidence': 'Neural networks improve yield prediction.'}])
        result = glean.analyse(model, source)
        self.assertEqual(result['relations'][0]['status'], 'unsupported')
        self.assertEqual(result['relations'][0]['evidence'],
                         'Neural networks improve yield prediction.')
        self.assertEqual(result['summary']['unsupported'], 1)
        self.assertEqual(result['summary']['candidate'], 0)

    def test_gleaning_finds_pair_without_relation(self):
        source = ('Random Forest improves yield prediction.\n'
                  'The Dryland Demo dataset covers yield prediction.\n')
        model = graph([('E1', 'Random Forest'), ('E2', 'yield prediction'),
                       ('E3', 'Dryland Demo')],
                      [{'id': 'R1', 'head': 'E1', 'tail': 'E2', 'type': 'improves',
                        'evidence': 'Random Forest improves yield prediction.'}])
        result = glean.analyse(model, source, rounds=2)
        candidates = [row for row in result['relations'] if row['status'] == 'candidate']
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual({candidate['head'], candidate['tail']}, {'E2', 'E3'})
        self.assertEqual(candidate['evidence'],
                         'The Dryland Demo dataset covers yield prediction.')
        self.assertTrue(candidate['id'].startswith('C'))
        self.assertEqual(result['summary'],
                         {'supported': 1, 'unsupported': 0, 'candidate': 1, 'rounds': 2})

    def test_no_gleaning_when_relation_already_exists(self):
        source = ('Random Forest improves yield prediction.\n'
                  'The Dryland Demo dataset covers yield prediction.\n')
        model = graph([('E1', 'Random Forest'), ('E2', 'yield prediction'),
                       ('E3', 'Dryland Demo')],
                      [{'id': 'R1', 'head': 'E1', 'tail': 'E2', 'type': 'improves',
                        'evidence': 'Random Forest improves yield prediction.'},
                       {'id': 'R2', 'head': 'E3', 'tail': 'E2', 'type': 'covers',
                        'evidence': 'The Dryland Demo dataset covers yield prediction.'}])
        result = glean.analyse(model, source, rounds=2)
        self.assertEqual(result['summary']['candidate'], 0)

    def test_entity_merge_on_case_whitespace_punctuation(self):
        model = graph([('E1', 'Random Forest'), ('E2', 'random-forest'),
                       ('E3', 'Random  Forest')], [])
        result = glean.analyse(model, 'Random Forest is used.\n')
        self.assertEqual(len(result['merges']), 1)
        self.assertEqual(set(result['merges'][0]), {'E1', 'E2', 'E3'})

    def test_no_fuzzy_merge_for_similar_names(self):
        model = graph([('E1', 'Random Forest'), ('E2', 'Random Forests'),
                       ('E3', 'Random Forest Model')], [])
        result = glean.analyse(model, 'Random Forest is used.\n')
        self.assertEqual(result['merges'], [])

    def test_candidates_stay_candidates_and_policy_is_reported(self):
        source = 'Alpha improves Beta.\nGamma improves Beta.\n'
        model = graph([('E1', 'Alpha'), ('E2', 'Beta'), ('E3', 'Gamma')],
                      [{'id': 'R1', 'head': 'E1', 'tail': 'E2', 'type': 'improves',
                        'evidence': 'Alpha improves Beta.'}])
        result = glean.analyse(model, source, rounds=3)
        statuses = [row['status'] for row in result['relations']]
        self.assertNotIn('confirmed', statuses)
        self.assertEqual(result['summary']['rounds'], 3)
        self.assertEqual(result['policy'], 'candidate 必须人工确认，不得直接写入图谱')
        self.assertEqual(statuses.count('candidate'), 1)

    def test_cli_writes_report_and_prints_summary(self):
        source = ('Random Forest improves yield prediction.\n'
                  'The Dryland Demo dataset covers yield prediction.\n')
        model = graph([('E1', 'Random Forest'), ('E2', 'yield prediction'),
                       ('E3', 'Dryland Demo')],
                      [{'id': 'R1', 'head': 'E1', 'tail': 'E2', 'type': 'improves',
                        'evidence': 'Random Forest improves yield prediction.'}])
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            graph_path, source_path, out_path = tmp / 'graph.json', tmp / 'source.md', tmp / 'out.json'
            graph_path.write_text(json.dumps(model, ensure_ascii=False), encoding='utf-8')
            source_path.write_text(source, encoding='utf-8')
            run = subprocess.run(
                [sys.executable, str(ROOT / 'scripts' / 'glean.py'),
                 str(graph_path), str(source_path), str(out_path), '--rounds', '1'],
                capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(run.returncode, 0, run.stderr)
            printed = json.loads(run.stdout)
            self.assertTrue(printed['ok'])
            report = json.loads(out_path.read_text(encoding='utf-8'))
            self.assertEqual(report['summary'],
                             {'supported': 1, 'unsupported': 0, 'candidate': 1, 'rounds': 1})
            self.assertEqual(report['policy'], 'candidate 必须人工确认，不得直接写入图谱')

    def test_nodes_and_edges_model_is_accepted(self):
        model = {'nodes': [{'id': 'n1', 'label': 'Random Forest', 'type': 'method'},
                           {'id': 'n2', 'label': 'yield prediction', 'type': 'task'}],
                 'edges': [{'id': 'e1', 'source': 'n1', 'target': 'n2', 'predicate': 'use',
                            'evidence': [{'file': 'paperA.txt', 'locator': 'L2',
                                          'quote': 'We use Random Forest for yield prediction.'}]}]}
        result = glean.analyse(model, 'We use Random Forest for yield prediction.\n')
        self.assertEqual(result['relations'][0]['status'], 'supported')
        self.assertEqual(result['summary']['candidate'], 0)


if __name__ == '__main__':
    unittest.main()
