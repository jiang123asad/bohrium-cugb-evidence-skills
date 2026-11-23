"""Source-anchor checker for structured reading notes; Python 3.10+, stdlib only.

Every note should carry a verbatim ``quote`` and a character interval
``[start, end)`` into its source file.  A note is trusted only when
``source[start:end]`` really equals the quote (whitespace-insensitive).
The mechanism is borrowed from google/langextract (Apache-2.0): each extracted
item carries a character interval, and an item whose interval does not match
the source is dropped as unsupported.  This module is an independent
implementation, not a copy of that project.

Notes without an interval are searched for the quote: one hit repairs the
interval, several hits are ambiguous, no hit is unsupported.  Quotes and
offsets are never invented or rewritten.

Usage:
    python anchors.py notes.json source.md out.json
    python anchors.py notes.json out.json              # every note has "file"
    python anchors.py notes.json out.json --root input  # "file" relative to DIR

Input:  {"notes":[{"id":"N1","file":"source.md","claim":"...",
                   "quote":"...","start":123,"end":140}]}
        "start"/"end" are optional; "claim" is informational and not checked.
Output: {"notes":[{id,file,status,interval,reason,...}],"summary":{...}}
        status is anchored | repaired | ambiguous | unsupported.

Exit code 0 when all notes are anchored/repaired, 1 when any note is
ambiguous/unsupported, 2 on unusable input.  Sources are read as UTF-8 in
universal-newline mode (CRLF becomes LF, as with Python's normal text reads)
and a leading BOM is dropped, so offsets match the text an agent normally sees.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path


class SourceError(ValueError):
    """A note's source path cannot be resolved safely."""

    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def normalize(text):
    """Collapse whitespace and apply NFC; the result no longer maps to indices."""
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', text)).strip()


def _search_patterns(quote):
    """Literal patterns that tolerate any whitespace between quote words."""
    patterns = []
    for variant in (quote, unicodedata.normalize('NFC', quote)):
        words = variant.split()
        if not words:
            continue
        pattern = r'\s+'.join(re.escape(word) for word in words)
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns


def find_spans(text, quote):
    """All character spans in *text* matching *quote*, whitespace-flexible."""
    for pattern in _search_patterns(quote):
        spans = [(match.start(), match.end()) for match in re.finditer(pattern, text)]
        if spans:
            return spans
    return []


def read_source(path):
    """Read a source file as UTF-8 with universal newlines, stripping a BOM."""
    with path.open('r', encoding='utf-8') as handle:
        text = handle.read()
    if text.startswith('\ufeff'):
        return text[1:], True
    return text, False


def _row(note_id, source, status, interval, reason, **extra):
    row = {'id': note_id, 'file': source, 'status': status,
           'interval': list(interval) if interval is not None else None,
           'reason': reason}
    row.update(extra)
    return row


def check_note(note, text, source):
    """Check one note against already-loaded *text*; return one output row."""
    note_id = note.get('id')
    quote = note.get('quote')
    if not isinstance(quote, str) or not normalize(quote):
        return _row(note_id, source, 'unsupported', None, 'empty_quote')

    start, end = note.get('start'), note.get('end')
    if start is None and end is None:
        return _search_note(note_id, quote, text, source)

    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        return _row(note_id, source, 'unsupported', None, 'invalid_interval',
                    given_interval=[start, end])
    if end > len(text):
        return _row(note_id, source, 'unsupported', None, 'interval_out_of_bounds',
                    given_interval=[start, end])

    excerpt = text[start:end]
    if normalize(excerpt) == normalize(quote):
        reason = 'exact_match' if excerpt == quote else 'whitespace_match'
        return _row(note_id, source, 'anchored', (start, end), reason)
    # A supplied interval that points elsewhere is not silently re-searched:
    # the note is dropped as unsupported rather than guessed at.
    return _row(note_id, source, 'unsupported', None, 'interval_mismatch',
                given_interval=[start, end])


def _search_note(note_id, quote, text, source):
    spans = find_spans(text, quote)
    if len(spans) == 1:
        return _row(note_id, source, 'repaired', spans[0], 'unique_match')
    if spans:
        return _row(note_id, source, 'ambiguous', None, 'ambiguous_match',
                    occurrences=len(spans),
                    candidates=[list(span) for span in spans[:10]])
    return _row(note_id, source, 'unsupported', None, 'no_match')


def resolve_source(root, name, default_source):
    """Resolve a note's source path inside *root*; reject absolute/escaping paths."""
    if isinstance(name, str) and name.strip():
        candidate = Path(name)
        if candidate.is_absolute():
            raise SourceError('path_escape', 'note "file" must be relative to the root directory')
        path = (root / candidate).resolve()
        if not path.is_relative_to(root.resolve()):
            raise SourceError('path_escape', 'note "file" escapes the root directory')
        return path, name
    if default_source is not None:
        path = Path(default_source)
        if not path.is_absolute():
            path = Path.cwd() / path
        return path.resolve(), path.name
    raise SourceError('no_source_file', 'note has no "file" and no fallback source was given')


def check_notes(payload, root, default_source=None):
    """Check every note; *payload* is the parsed notes.json (or a bare list)."""
    notes = payload.get('notes') if isinstance(payload, dict) else payload
    if not isinstance(notes, list):
        raise ValueError('notes.json must contain a "notes" list')
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f'root is not a directory: {root}')

    cache = {}
    bom_files = []
    rows = []

    for index, note in enumerate(notes):
        if not isinstance(note, dict):
            rows.append(_row(f'#{index}', None, 'unsupported', None, 'invalid_note'))
            continue
        note_id = note.get('id')
        if not isinstance(note_id, str) or not note_id.strip():
            note_id = f'#{index}'
        try:
            path, label = resolve_source(root, note.get('file'), default_source)
        except SourceError as exc:
            rows.append(_row(note_id, note.get('file'), 'unsupported', None,
                             exc.reason, detail=exc.detail))
            continue

        if path not in cache:
            try:
                text, had_bom = read_source(path)
            except (OSError, UnicodeDecodeError) as exc:
                cache[path] = ('error', None, None, f'{type(exc).__name__}: {exc}')
            else:
                cache[path] = ('ok', text, had_bom, None)
                if had_bom:
                    bom_files.append(label)
        state, text, had_bom, error = cache[path]
        if state != 'ok':
            rows.append(_row(note_id, label, 'unsupported', None,
                             'source_unreadable', detail=error))
            continue

        row = check_note(note, text, label)
        row['id'] = note_id
        rows.append(row)

    counts = Counter(row['status'] for row in rows)
    summary = {
        'total': len(rows),
        'anchored': counts['anchored'],
        'repaired': counts['repaired'],
        'ambiguous': counts['ambiguous'],
        'unsupported': counts['unsupported'],
        'supported': counts['anchored'] + counts['repaired'],
        'all_anchored': counts['ambiguous'] == 0 and counts['unsupported'] == 0,
        'unsupported_ids': [row['id'] for row in rows if row['status'] == 'unsupported'],
        'ambiguous_ids': [row['id'] for row in rows if row['status'] == 'ambiguous'],
        'sources': sorted({row['file'] for row in rows if row.get('file')}),
    }
    if bom_files:
        summary['bom_stripped'] = sorted(set(bom_files))
    return {'notes': rows, 'summary': summary}


def load_json(path):
    with Path(path).open('r', encoding='utf-8') as handle:
        return json.load(handle)


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                   encoding='utf-8')
    tmp.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Check note quotes against character intervals in their source files.')
    parser.add_argument('notes', type=Path, help='notes.json')
    parser.add_argument('source', type=Path, nargs='?',
                        help='fallback source file for notes without "file"')
    parser.add_argument('out', type=Path, nargs='?', help='output JSON')
    parser.add_argument('--root', type=Path,
                        help='directory for per-note "file" paths (default: notes.json directory)')
    args = parser.parse_args(argv)

    out = args.out
    source = args.source
    if out is None:            # two positionals: notes.json out.json
        out, source = source, None
    if out is None:
        parser.error('missing output path')

    try:
        payload = load_json(args.notes)
        root = (args.root or args.notes.parent).resolve()
        result = check_notes(payload, root, default_source=source)
        save_json(out, result)
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__, 'detail': str(exc)},
                         ensure_ascii=False))
        return 2

    print(json.dumps(result['summary'], ensure_ascii=False))
    return 0 if result['summary']['all_anchored'] else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
