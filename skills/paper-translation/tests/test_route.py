"""Tests for scripts/route.py (per-segment glossary routing)."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import route  # noqa: E402


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text, encoding='utf-8')
        return path

    def run_cli(self, source, glossary, targets=None, max_per_segment=None):
        out_path = self.dir / 'routes.json'
        args = [str(self.write('source.md', source)), str(self.write('glossary.csv', glossary)),
                str(out_path)]
        if targets is not None:
            args += ['--targets', str(self.write('targets.tsv', targets))]
        if max_per_segment is not None:
            args += ['--max-per-segment', str(max_per_segment)]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = route.main(args)
        self.assertEqual(code, 0)
        report = json.loads(out_path.read_text(encoding='utf-8'))
        return report, json.loads(buffer.getvalue())

    def test_routes_only_entries_present_in_segment(self):
        source = 'Remote sensing data were used.\n\nWe measured yield in the field.\n'
        glossary = ('source,target\nremote sensing,遥感\nyield,产量\n'
                    'irrigation,灌溉\n')
        report, summary = self.run_cli(source, glossary)
        self.assertEqual([s['id'] for s in report['segments']], ['S001', 'S002'])
        self.assertEqual([g['source'] for g in report['segments'][0]['glossary']],
                         ['remote sensing'])
        self.assertEqual([g['source'] for g in report['segments'][1]['glossary']], ['yield'])
        self.assertNotIn('irrigation',
                         [g['source'] for s in report['segments'] for g in s['glossary']])
        self.assertEqual(summary, {'segments': 2, 'with_glossary': 2,
                                   'missing_targets': 0, 'overflow_segments': 0})

    def test_chinese_term_matching_is_literal(self):
        source = '本研究使用遥感影像进行分类。\n\n影像分辨率较高。\n'
        glossary = 'source,target\n遥感影像,remote sensing imagery\n影像,image\n图像,graph\n'
        report, _ = self.run_cli(source, glossary)
        first = [g['source'] for g in report['segments'][0]['glossary']]
        second = [g['source'] for g in report['segments'][1]['glossary']]
        self.assertEqual(first, ['遥感影像', '影像'])
        self.assertEqual(second, ['影像'])
        self.assertNotIn('图像', first + second)

    def test_latin_matching_is_case_insensitive_and_boundary_aware(self):
        source = ('REMOTE SENSING products were validated.\n\n'
                  'The network stayed stable.\n\nNet primary production increased.\n\n'
                  'Training samples were limited.\n')
        glossary = ('source,target\nRemote Sensing,遥感\nnet,净\n'
                    'training sample,训练样本\n')
        report, _ = self.run_cli(source, glossary)
        self.assertEqual([g['source'] for g in report['segments'][0]['glossary']],
                         ['Remote Sensing'])
        self.assertEqual(report['segments'][1]['glossary'], [])
        self.assertEqual([g['source'] for g in report['segments'][2]['glossary']], ['net'])
        self.assertEqual([g['source'] for g in report['segments'][3]['glossary']],
                         ['training sample'])

    def test_max_per_segment_keeps_longest_and_reports_overflow(self):
        source = 'alpha beta gamma delta epsilon appears here.\n'
        glossary = ('source,target\nalpha,A\nbeta,B\ngamma,C\ndelta,D\n'
                    'epsilon,E\n')
        report, summary = self.run_cli(source, glossary, max_per_segment=3)
        segment = report['segments'][0]
        self.assertEqual([g['source'] for g in segment['glossary']],
                         ['epsilon', 'alpha', 'gamma'])
        self.assertEqual(segment['overflow'], 2)
        self.assertEqual(summary['overflow_segments'], 1)

    def test_missing_target_is_reported_and_never_fixed(self):
        source = 'Remote sensing data were used.\n\nWe measured yield.\n'
        glossary = 'source,target\nremote sensing,遥感\nyield,产量\n'
        targets = 'S001\t遥感数据\nS002\t我们测量了产量。\n'
        report, summary = self.run_cli(source, glossary, targets)
        self.assertEqual(report['segments'][0]['missing_target'], [])
        self.assertTrue(report['segments'][1]['target_found'])
        self.assertEqual(summary['missing_targets'], 0)
        self.assertEqual(report['segments'][1]['target'], '我们测量了产量。')

        targets = 'S001\t遥感数据\nS002\t我们进行了测量。\n'
        report, summary = self.run_cli(source, glossary, targets)
        self.assertEqual(report['segments'][1]['missing_target'],
                         [{'source': 'yield', 'target': '产量'}])
        self.assertEqual(summary['missing_targets'], 1)

    def test_absent_target_line_marks_all_routed_terms_missing(self):
        source = 'Remote sensing data were used.\n'
        glossary = 'source,target\nremote sensing,遥感\n'
        report, summary = self.run_cli(source, glossary, targets='S002\t无关译文\n')
        segment = report['segments'][0]
        self.assertFalse(segment['target_found'])
        self.assertEqual(segment['missing_target'],
                         [{'source': 'remote sensing', 'target': '遥感'}])
        self.assertEqual(summary['missing_targets'], 1)

    def test_segment_ids_are_stable_across_glossaries_and_limits(self):
        source = '\n\nFirst paragraph. Still first.\n\nSecond paragraph.\n\n\n'
        first = route.build_report(source, route.load_glossary(self.write('a.csv',
                                 'source,target\nalpha,A\n')), 12)
        second = route.build_report(source, route.load_glossary(self.write('b.csv',
                                  'source,target\nalpha,A\nbeta,B\n')), 1)
        ids = [segment['id'] for segment in first['segments']]
        self.assertEqual(ids, ['S001', 'S002'])
        self.assertEqual(ids, [segment['id'] for segment in second['segments']])

    def test_long_blocks_split_on_sentence_enders_only(self):
        short = '第一句。第二句。'
        self.assertEqual(route.split_segments(short), [short])

        chinese = '甲' * 180 + '。' + '乙' * 180 + '。' + '丙' * 180 + '。'
        pieces = route.split_segments(chinese)
        self.assertEqual(len(pieces), 3)
        self.assertTrue(pieces[0].endswith('。'))
        self.assertTrue(pieces[-1].endswith('。'))

        english = ('The first sentence is here. ' * 20
                   + 'The second one follows. ' * 20)
        pieces = route.split_segments(english)
        self.assertGreater(len(pieces), 2)
        self.assertTrue(pieces[0].endswith('here.'))
        self.assertTrue(all(piece.endswith('.') for piece in pieces))

    def test_cli_accepts_quoted_comma_and_summary_keys(self):
        source = 'Smith, 2020 reported a new method.\n'
        glossary = 'source,target\n"Smith, 2020",史密斯\n'
        report, summary = self.run_cli(source, glossary)
        self.assertEqual([g['source'] for g in report['segments'][0]['glossary']],
                         ['Smith, 2020'])
        self.assertEqual(set(summary), {'segments', 'with_glossary',
                                        'missing_targets', 'overflow_segments'})
        self.assertEqual(summary['segments'], 1)


if __name__ == '__main__':
    unittest.main()
