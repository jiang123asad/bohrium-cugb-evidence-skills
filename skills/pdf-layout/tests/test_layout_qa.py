"""Tests for scripts/layout_qa.py (stdlib unittest)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import layout_qa  # noqa: E402


def native_layout(blocks=None, tables=None, page_overrides=None):
    page = {
        'pdf_page': 1,
        'width': 600,
        'height': 800,
        'blocks': blocks if blocks is not None else [
            {'id': 'P0001-B001', 'kind': 'heading_candidate', 'text': '1. Introduction',
             'bbox': [72, 70, 200, 82], 'reading_order': 1},
            {'id': 'P0001-B002', 'kind': 'text', 'text': 'Body text.',
             'bbox': [72, 90, 400, 110], 'reading_order': 2},
        ],
        'tables': tables or [],
    }
    if page_overrides:
        page.update(page_overrides)
    return {'source': 'demo.pdf', 'page_count': 1, 'pages': [page]}


def status(result, check_id):
    return next(check['status'] for check in result['checks'] if check['id'] == check_id)


def kinds(result):
    return [issue['kind'] for issue in result['issues']]


def run_cli(*args):
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    return subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'layout_qa.py'), *map(str, args)],
        capture_output=True, text=True, encoding='utf-8', env=env)


class LayoutQaTests(unittest.TestCase):

    def test_clean_layout_passes(self):
        result = layout_qa.check_layout(native_layout())
        self.assertEqual([check['status'] for check in result['checks']],
                         ['pass'] * len(layout_qa.CHECKS))
        self.assertEqual(result['summary'], {'elements': 2, 'fail': 0, 'warn': 0})
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['policy'], layout_qa.POLICY)

    def test_missing_page_reported(self):
        data = {'elements': [
            {'id': 'E1', 'kind': 'text', 'text': 'No page here.',
             'bbox': [10, 10, 100, 20]},
        ]}
        result = layout_qa.check_layout(data)
        self.assertEqual(status(result, 'missing_page'), 'fail')
        self.assertIn('missing_page', kinds(result))
        issue = next(item for item in result['issues'] if item['kind'] == 'missing_page')
        self.assertEqual(issue['element'], 'E1')

        zero_page = native_layout(page_overrides={'pdf_page': 1})
        zero_page['pages'][0]['pdf_page'] = 0
        zero = layout_qa.check_layout(zero_page)
        self.assertEqual(status(zero, 'missing_page'), 'fail')
        self.assertIn('页码 0', zero['issues'][0]['detail'])

    def test_bbox_inverted_and_out_of_page(self):
        blocks = [
            {'id': 'P0001-B001', 'kind': 'text', 'text': 'Inverted box.',
             'bbox': [300, 110, 72, 90], 'reading_order': 1},
            {'id': 'P0001-B002', 'kind': 'text', 'text': 'Outside box.',
             'bbox': [500, 700, 900, 900], 'reading_order': 2},
        ]
        result = layout_qa.check_layout(native_layout(blocks=blocks))
        self.assertEqual(status(result, 'bbox_out_of_page'), 'fail')
        details = ' '.join(item['detail'] for item in result['issues']
                           if item['kind'] == 'bbox_out_of_page')
        self.assertIn('退化', details)
        self.assertIn('超出页面', details)

        missing = layout_qa.check_layout({'elements': [
            {'id': 'E1', 'kind': 'text', 'text': 'x', 'page': 1},
        ]})
        self.assertEqual(status(missing, 'bbox_out_of_page'), 'fail')
        self.assertIn('bbox 缺失', missing['issues'][0]['detail'])

    def test_duplicate_order_within_page(self):
        blocks = [
            {'id': 'P0001-B001', 'kind': 'text', 'text': 'First.',
             'bbox': [72, 70, 200, 82], 'reading_order': 2},
            {'id': 'P0001-B002', 'kind': 'text', 'text': 'Second.',
             'bbox': [72, 90, 200, 102], 'reading_order': 2},
            {'id': 'P0001-B003', 'kind': 'text', 'text': 'Third.',
             'bbox': [72, 110, 200, 122], 'reading_order': 1},
        ]
        result = layout_qa.check_layout(native_layout(blocks=blocks))
        self.assertEqual(status(result, 'reading_order'), 'warn')
        order_issues = [item for item in result['issues'] if item['kind'] == 'reading_order']
        self.assertTrue(order_issues)
        self.assertTrue(any('重复' in item['detail'] for item in order_issues))
        self.assertTrue(any('未递增' in item['detail'] for item in order_issues))
        self.assertEqual(result['summary']['fail'], 0)
        self.assertGreaterEqual(result['summary']['warn'], 2)

    def test_table_cell_count_mismatch(self):
        tables = [{
            'id': 'P0001-T001', 'kind': 'table', 'bbox': [72, 200, 400, 300],
            'num_rows': 2, 'num_cols': 2,
            'table_cells': [
                {'text': 'a', 'row_span': 1, 'col_span': 1},
                {'text': 'b', 'row_span': 1, 'col_span': 1},
                {'text': 'c', 'row_span': 1, 'col_span': 1},
            ],
            'rows': [['a', 'b'], ['c', None]],
        }]
        result = layout_qa.check_layout(native_layout(tables=tables))
        self.assertEqual(status(result, 'table_incomplete'), 'fail')
        self.assertIn('3', result['issues'][-1]['detail'])

        merged = [{
            'id': 'P0001-T001', 'kind': 'table', 'bbox': [72, 200, 400, 300],
            'num_rows': 2, 'num_cols': 2,
            'table_cells': [
                {'text': 'merged', 'row_span': 2, 'col_span': 1},
                {'text': 'x', 'row_span': 1, 'col_span': 1},
                {'text': 'y', 'row_span': 1, 'col_span': 1},
            ],
        }]
        spanned = layout_qa.check_layout(native_layout(tables=merged))
        self.assertEqual(status(spanned, 'table_incomplete'), 'pass')

        empty = layout_qa.check_layout(native_layout(tables=[{
            'id': 'P0001-T001', 'kind': 'table', 'bbox': [72, 200, 400, 300],
            'num_rows': 2, 'num_cols': 2, 'table_cells': [],
        }]))
        self.assertEqual(status(empty, 'table_incomplete'), 'fail')
        self.assertIn('没有单元格', empty['issues'][-1]['detail'])

    def test_duplicate_id_and_empty_text(self):
        blocks = [
            {'id': 'P0001-B001', 'kind': 'text', 'text': 'Alpha',
             'bbox': [72, 70, 200, 82], 'reading_order': 1},
            {'id': 'P0001-B001', 'kind': 'text', 'text': 'Beta',
             'bbox': [72, 90, 200, 102], 'reading_order': 2},
            {'id': 'P0001-B003', 'kind': 'text', 'text': '   ',
             'bbox': [72, 110, 200, 122], 'reading_order': 3},
        ]
        result = layout_qa.check_layout(native_layout(blocks=blocks))
        self.assertEqual(status(result, 'duplicate_id'), 'fail')
        self.assertIn('duplicate_id', kinds(result))
        self.assertEqual(status(result, 'empty_text'), 'warn')
        empty_issue = next(item for item in result['issues'] if item['kind'] == 'empty_text')
        self.assertEqual(empty_issue['element'], 'P0001-B003')
        self.assertEqual(result['summary']['elements'], 3)

    def test_docling_example_shape(self):
        data = json.loads((ROOT / 'examples' / 'output' / 'docling-layout.json')
                          .read_text(encoding='utf-8-sig'))
        result = layout_qa.check_layout(data, page_limit=2)
        self.assertEqual(result['summary']['elements'], 9)
        self.assertEqual(status(result, 'table_incomplete'), 'pass')
        self.assertEqual(status(result, 'duplicate_id'), 'pass')
        self.assertEqual(status(result, 'reading_order'), 'pass')
        # The export keeps one unlocated content item; it must surface, not vanish.
        unlocated = [item for item in result['issues']
                     if item['kind'] == 'missing_page' and item['element'] == '#/texts/4']
        self.assertEqual(len(unlocated), 1)
        self.assertEqual(status(result, 'missing_page'), 'fail')

    def test_cli_exit_codes_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            clean = tmp / 'clean.json'
            clean.write_text(json.dumps(native_layout(), ensure_ascii=False), encoding='utf-8')
            clean_out = tmp / 'clean-out.json'
            run = run_cli(clean, clean_out, '--pages', '1')
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads(clean_out.read_text(encoding='utf-8'))
            self.assertEqual(list(report), ['checks', 'issues', 'summary', 'policy'])
            self.assertEqual(report['summary'], {'elements': 2, 'fail': 0, 'warn': 0})
            self.assertEqual(json.loads(run.stdout.strip()), report['summary'])

            broken = tmp / 'broken.json'
            broken.write_text(json.dumps({'elements': [
                {'id': 'E1', 'kind': 'text', 'text': 'no page', 'bbox': [1, 2, 3, 4]},
                {'id': 'E1', 'kind': 'text', 'text': 'dup', 'page': 1,
                 'bbox': [10, 20, 10, 4], 'order': 1},
            ]}), encoding='utf-8')
            broken_out = tmp / 'broken-out.json'
            fail_run = run_cli(broken, broken_out)
            self.assertEqual(fail_run.returncode, 1, fail_run.stderr)
            self.assertGreater(json.loads(broken_out.read_text(encoding='utf-8'))
                               ['summary']['fail'], 0)

            bad = tmp / 'bad.json'
            bad.write_text('{not json', encoding='utf-8')
            bad_run = run_cli(bad, tmp / 'bad-out.json')
            self.assertEqual(bad_run.returncode, 2)
            self.assertIn('无法读取', bad_run.stderr)

    def test_items_and_list_shapes(self):
        items = {'items': [
            {'id': 'I1', 'kind': 'text', 'text': 'One', 'page': 1,
             'bbox': [10, 10, 100, 20], 'order': 1},
            {'id': 'I2', 'kind': 'text', 'text': 'Two', 'page': 1,
             'bbox': [10, 30, 100, 40], 'order': 2},
        ]}
        result = layout_qa.check_layout(items)
        self.assertEqual(result['summary'], {'elements': 2, 'fail': 0, 'warn': 0})

        listed = layout_qa.check_layout([
            {'id': 'L1', 'kind': 'text', 'text': 'One', 'page': 1,
             'bbox': [10, 10, 100, 20], 'order': 1},
        ])
        self.assertEqual(listed['summary']['elements'], 1)
        self.assertEqual(status(listed, 'missing_page'), 'pass')


if __name__ == '__main__':
    unittest.main()
