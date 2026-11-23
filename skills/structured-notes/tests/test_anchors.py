"""Tests for scripts/anchors.py; run with: python -m unittest discover -s tests"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import anchors  # noqa: E402


SOURCE = (
    '# 结果\n'
    '治疗组在晴天条件下观察到 5 mg 的效应。\n'
    '第二段：治疗组在\n    晴天条件下复现。\n'
    '对照组的重复表述。\n'
    '对照组的重复表述。\n'
)


class AnchorTests(unittest.TestCase):

    def check(self, note, text=SOURCE):
        return anchors.check_note(note, text, 'source.md')

    def test_exact_interval_is_anchored(self):
        quote = '治疗组在晴天条件下观察到 5 mg 的效应。'
        start = SOURCE.index(quote)
        note = {'id': 'N1', 'quote': quote, 'start': start, 'end': start + len(quote)}
        row = self.check(note)
        self.assertEqual(row['status'], 'anchored')
        self.assertEqual(row['interval'], [start, start + len(quote)])
        self.assertEqual(row['reason'], 'exact_match')

    def test_whitespace_normalised_interval_is_anchored(self):
        # The interval contains a newline and four spaces; the quote has one space.
        start = SOURCE.index('治疗组在\n    晴天条件下')
        end = start + len('治疗组在\n    晴天条件下')
        quote = '治疗组在 晴天条件下'
        row = self.check({'id': 'N2', 'quote': quote, 'start': start, 'end': end})
        self.assertEqual(row['status'], 'anchored')
        self.assertEqual(row['reason'], 'whitespace_match')
        self.assertEqual(row['interval'], [start, end])

    def test_interval_mismatch_is_unsupported(self):
        start = SOURCE.index('对照组的重复表述。')
        quote = '治疗组在晴天条件下观察到'
        row = self.check({'id': 'N3', 'quote': quote, 'start': start,
                          'end': start + len(quote)})
        self.assertEqual(row['status'], 'unsupported')
        self.assertEqual(row['reason'], 'interval_mismatch')
        self.assertIsNone(row['interval'])
        self.assertEqual(row['given_interval'], [start, start + len(quote)])

    def test_ambiguous_duplicate_quote_is_not_filled(self):
        quote = '对照组的重复表述。'
        row = self.check({'id': 'N4', 'quote': quote})
        self.assertEqual(row['status'], 'ambiguous')
        self.assertIsNone(row['interval'])
        self.assertEqual(row['occurrences'], 2)
        self.assertEqual(len(row['candidates']), 2)
        self.assertNotEqual(row['candidates'][0], row['candidates'][1])

    def test_missing_quote_is_unsupported(self):
        row = self.check({'id': 'N5', 'quote': '本文从未出现的句子。'})
        self.assertEqual(row['status'], 'unsupported')
        self.assertEqual(row['reason'], 'no_match')
        self.assertIsNone(row['interval'])

    def test_absent_interval_unique_quote_is_repaired(self):
        quote = '观察到 5 mg 的效应'
        row = self.check({'id': 'N6', 'quote': quote})
        start = SOURCE.index(quote)
        self.assertEqual(row['status'], 'repaired')
        self.assertEqual(row['interval'], [start, start + len(quote)])
        self.assertEqual(row['reason'], 'unique_match')
        self.assertEqual(SOURCE[start:start + len(quote)], quote)

    def test_out_of_bounds_interval_is_unsupported(self):
        row = self.check({'id': 'N7', 'quote': '治疗组', 'start': len(SOURCE) - 2,
                          'end': len(SOURCE) + 50})
        self.assertEqual(row['status'], 'unsupported')
        self.assertEqual(row['reason'], 'interval_out_of_bounds')

    def test_source_path_escape_is_rejected(self):
        result = anchors.check_notes(
            {'notes': [{'id': 'N8', 'file': '../secret.md', 'quote': 'x'}]},
            root=Path(__file__).resolve().parent)
        row = result['notes'][0]
        self.assertEqual(row['status'], 'unsupported')
        self.assertEqual(row['reason'], 'path_escape')

    def test_cli_writes_output_and_reports_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'source.md').write_text(SOURCE, encoding='utf-8')
            quote = '治疗组在晴天条件下观察到'
            start = SOURCE.index(quote)
            payload = {'notes': [
                {'id': 'OK', 'file': 'source.md', 'quote': quote,
                 'start': start, 'end': start + len(quote)},
                {'id': 'BAD', 'quote': '编造的引文'},
            ]}
            notes_path = root / 'notes.json'
            notes_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            out_path = root / 'out.json'
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = anchors.main([str(notes_path), str(root / 'source.md'), str(out_path)])

            self.assertEqual(code, 1)
            result = json.loads(out_path.read_text(encoding='utf-8'))
            self.assertEqual([row['status'] for row in result['notes']],
                             ['anchored', 'unsupported'])
            summary = result['summary']
            self.assertEqual(summary['total'], 2)
            self.assertEqual(summary['supported'], 1)
            self.assertEqual(summary['unsupported'], 1)
            self.assertFalse(summary['all_anchored'])
            self.assertEqual(summary['unsupported_ids'], ['BAD'])
            printed = json.loads(stdout.getvalue())
            self.assertEqual(printed['unsupported'], 1)


if __name__ == '__main__':
    unittest.main()
