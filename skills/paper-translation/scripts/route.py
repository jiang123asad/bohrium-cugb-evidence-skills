"""Route glossary entries per segment; Python 3.10+, standard library only.

The segment-scoped glossary prompt is inspired by BabelDOC (AGPL); this is an
independent implementation, not copied code. Usage:

    python scripts/route.py source.md glossary.csv out.json
        [--targets targets.md] [--max-per-segment 12]

`glossary.csv` has a header row and two columns, `source,target` (UTF-8; a
quoted field may contain commas). The source is split into blank-line
separated blocks; blocks longer than 400 characters are further split after
Chinese/English sentence enders. Ids are stable (`S001`, `S002`, ...) and do
not depend on the glossary, the targets file or --max-per-segment.

Only entries whose source term occurs in a segment are emitted for it.
Matching is case-insensitive for Latin terms (word-boundary aware, tolerant of
plurals) and exact for Chinese terms. When more than --max-per-segment entries
match, the longest matched terms are kept and the number dropped is recorded in
`overflow` (never silently dropped).

With --targets, a tab-separated file of `S001<TAB>译文` lines is checked:
every routed entry's target must occur in that segment's translation. Missing
ones are listed in `missing_target`; the translation is never modified.

The full report is written to out.json; stdout receives only the summary:
{"segments":n,"with_glossary":n,"missing_targets":n,"overflow_segments":n}
A missing target is reported, not treated as a command failure (exit 0).
"""
import argparse
import csv
import io
import json
import re
import sys
import unicodedata
from pathlib import Path

MAX_SEGMENT_CHARS = 400
HEADER_SOURCE = {'source', 'src', 'term', '原文', '源术语', '术语'}
HEADER_TARGET = {'target', 'dst', 'translation', '译文', '目标术语', '译术语'}
SENTENCE_ENDERS = set('。！？；…!?')
CJK_ENDERS = set('。！？；…')
CLOSERS = set('”’"\')]}»」』）】》')
ABBREVIATIONS = {
    'e.g', 'i.e', 'etc', 'vs', 'cf', 'al', 'fig', 'figs', 'eq', 'eqs', 'ref',
    'refs', 'no', 'nos', 'vol', 'pp', 'sec', 'tab', 'suppl', 'approx', 'ca',
    'resp', 'dr', 'prof', 'st', 'min', 'max', 'inc', 'ltd', 'et', 'u.s',
    'u.k', 'e.u', 'u.n', 'ph.d', 'mr', 'mrs', 'ms',
}
LATIN_ALNUM = re.compile(r'[A-Za-z0-9]')
SPACE = re.compile(r'\s+')


def read_text(path):
    return path.read_text(encoding='utf-8-sig')


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                   encoding='utf-8')
    tmp.replace(path)


def normalise(text):
    """NFC, single spaces: segments are translation units, not layout copies."""
    return SPACE.sub(' ', unicodedata.normalize('NFC', text)).strip()


def _is_abbreviation(text, start, dot):
    token = re.search(r'([A-Za-z.]+)$', text[start:dot])
    if not token:
        return False
    word = token.group(1).rstrip('.').lower()
    return word in ABBREVIATIONS or (len(word) == 1 and word.isalpha())


def _sentence_pieces(text):
    """Split normalised text after sentence enders, keeping each ender."""
    pieces = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in SENTENCE_ENDERS:
            j = i + 1
            while j < n and text[j] in SENTENCE_ENDERS | CLOSERS:
                j += 1
            # Chinese enders need no following space; ASCII ones do unless
            # the next sentence starts with CJK text.
            if (j == n or text[j].isspace() or ch in CJK_ENDERS
                    or ord(text[j]) > 127):
                piece = text[start:j].strip()
                if piece:
                    pieces.append(piece)
                start = j
                i = j
                continue
        elif ch == '.':
            prev = text[i - 1] if i else ''
            nxt = text[i + 1] if i + 1 < n else ''
            if prev.isdigit() and nxt.isdigit():
                i += 1
                continue
            if nxt and nxt not in CLOSERS and not nxt.isspace():
                i += 1
                continue
            if _is_abbreviation(text, start, i):
                i += 1
                continue
            j = i + 1
            while j < n and text[j] in CLOSERS:
                j += 1
            k = j
            while k < n and text[k].isspace():
                k += 1
            if k < n and text[k].islower():
                i += 1
                continue
            piece = text[start:j].strip()
            if piece:
                pieces.append(piece)
            start = j
            i = j
            continue
        i += 1
    tail = text[start:].strip()
    if tail:
        pieces.append(tail)
    return pieces


def split_segments(text):
    """Blank-line blocks; long blocks are split on sentence enders."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    segments = []
    for block in re.split(r'\n\s*\n', text):
        block = normalise(block)
        if not block:
            continue
        if len(block) > MAX_SEGMENT_CHARS:
            pieces = _sentence_pieces(block)
            segments.extend(pieces if pieces else [block])
        else:
            segments.append(block)
    return segments


def term_pattern(term):
    """Case-insensitive Latin matching; boundary-aware and plural-tolerant."""
    if not (LATIN_ALNUM.match(term[0]) and LATIN_ALNUM.match(term[-1])):
        return re.compile(re.escape(term), re.IGNORECASE)
    variants = [re.escape(term)]
    if term[-1].isalpha():
        variants.extend([re.escape(term) + 's', re.escape(term) + 'es'])
        if term[-1] in 'yY':
            variants.append(re.escape(term[:-1]) + 'ies')
    body = '(?:' + '|'.join(variants) + ')'
    return re.compile(r'(?<![A-Za-z0-9])' + body + r'(?![A-Za-z0-9])', re.IGNORECASE)


def load_glossary(path):
    text = read_text(path)
    reader = csv.reader(io.StringIO(text, newline=''))
    entries = []
    seen = {}
    first_row = True
    for line_no, row in enumerate(reader, 1):
        if not row or all(not cell.strip() for cell in row):
            continue
        cells = [cell.strip() for cell in row]
        if first_row:
            first_row = False
            if cells[0].casefold() in HEADER_SOURCE or (
                    len(cells) > 1 and cells[1].casefold() in HEADER_TARGET):
                continue
        if len(cells) < 2:
            raise ValueError(f'glossary line {line_no}: expected 2 columns, got {len(cells)}')
        if any(cell for cell in cells[2:]):
            raise ValueError(f'glossary line {line_no}: unquoted comma or extra column')
        source, target = normalise(cells[0]), normalise(cells[1])
        if not source or not target:
            raise ValueError(f'glossary line {line_no}: empty source or target')
        key = source.casefold()
        if key in seen:
            if seen[key] != target:
                raise ValueError(f'glossary line {line_no}: conflicting targets for {source!r}')
            continue
        seen[key] = target
        entries.append({'source': source, 'target': target, 'pattern': term_pattern(source)})
    if not entries:
        raise ValueError('glossary has no entries')
    return entries


def load_targets(path):
    text = read_text(path)
    targets = {}
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip().lstrip('\ufeff')
        if not line or line.startswith('#'):
            continue
        if '\t' not in line:
            raise ValueError(f'targets line {line_no}: expected "S001<TAB>translation"')
        segment_id, target = line.split('\t', 1)
        segment_id = segment_id.strip()
        if not segment_id:
            raise ValueError(f'targets line {line_no}: empty segment id')
        if segment_id in targets:
            raise ValueError(f'targets line {line_no}: duplicate id {segment_id}')
        targets[segment_id] = target.strip()
    return targets


def route_segment(text, entries, max_per_segment):
    hits = []
    for entry in entries:
        match = entry['pattern'].search(text)
        if match:
            hits.append((entry, match.start()))
    hits.sort(key=lambda item: (-len(item[0]['source']), item[1], item[0]['source'].casefold()))
    kept = hits[:max_per_segment]
    return ([{'source': entry['source'], 'target': entry['target']} for entry, _ in kept],
            len(hits) - len(kept))


def target_present(target, translation):
    if not translation:
        return False
    return bool(term_pattern(normalise(target)).search(normalise(translation)))


def build_report(source_text, entries, max_per_segment, targets=None):
    segments = split_segments(source_text)
    report = []
    with_glossary = 0
    overflow_segments = 0
    missing_targets = 0
    for index, text in enumerate(segments, 1):
        segment_id = f'S{index:03d}'
        routed, overflow = route_segment(text, entries, max_per_segment)
        item = {'id': segment_id, 'source': text, 'glossary': routed, 'overflow': overflow}
        if routed:
            with_glossary += 1
        if overflow:
            overflow_segments += 1
        if targets is not None:
            found = segment_id in targets
            translation = targets.get(segment_id, '')
            missing = [{'source': entry['source'], 'target': entry['target']}
                       for entry in routed
                       if not target_present(entry['target'], translation)]
            item['target'] = translation
            item['target_found'] = found
            item['missing_target'] = missing
            missing_targets += len(missing)
        report.append(item)
    summary = {'segments': len(report), 'with_glossary': with_glossary,
               'missing_targets': missing_targets, 'overflow_segments': overflow_segments}
    return {'segments': report, 'summary': summary}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('source', type=Path)
    parser.add_argument('glossary', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--targets', type=Path)
    parser.add_argument('--max-per-segment', type=int, default=12)
    args = parser.parse_args(argv)
    if args.max_per_segment < 0:
        raise ValueError('--max-per-segment must be >= 0')
    entries = load_glossary(args.glossary)
    targets = load_targets(args.targets) if args.targets else None
    report = build_report(read_text(args.source), entries, args.max_per_segment, targets)
    save(args.output, report)
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, KeyError, csv.Error) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__, 'detail': str(exc)},
                         ensure_ascii=False))
        sys.exit(2)
