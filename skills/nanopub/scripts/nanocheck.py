#!/usr/bin/env python3
"""Pre-publication structure check for local nanopublications.

The validation order follows the high-level order used by
Nanopublication/nanopub-java (MIT): first the four-graph structure, then
compliance with the publication template, then a deterministic content
hash.  This is an independent standard-library implementation; no
upstream code is copied and no RDF library is required.

The hash is a local stand-in for a trusty URI: SHA-256 over the sorted,
canonical assertion triples, one per line, LF-terminated.  It is not a
signature and does not prove authorship or online registration.

Usage:
    python scripts/nanocheck.py nanopub.json out.json
    python scripts/nanocheck.py nanopub.json out.json --hash-input lines.txt
    python scripts/nanocheck.py nanopub.json out.json --expect-hash <sha256>

Exit codes: 0 = publishable, 1 = blocked by a failed check, 2 = bad input.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

CHECK_ORDER = (
    'structure.head_graph',
    'structure.named_graphs',
    'assertion.nonempty',
    'assertion.triples',
    'assertion.term_format',
    'assertion.literal_datatype',
    'provenance.required',
    'provenance.sources',
    'pubinfo.required',
    'timestamps.iso8601',
    'assertion.hash',
    'hash.matches_expected',
)

SCHEME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9+.\-]*:\S+$')
BAD_IRI_CHARS = re.compile(r'[\s<>"{}|\\^`]')
RATIONALE_KEYS = ('sourcesNote', 'sourcesReason', 'sources_reason',
                  'sourcesMissingReason', 'sources_missing_reason')
HEX64 = re.compile(r'^[0-9a-f]{64}$')


# ---------------------------------------------------------------- input/output

def _reject_number(value):
    raise ValueError(f'invalid JSON number: {value}')


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f'duplicate JSON key: {key}')
        obj[key] = value
    return obj


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'non-finite JSON number: {value}')
    return number


def load_json(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    return json.loads(text, parse_constant=_reject_number,
                      parse_float=_finite_float, object_pairs_hook=_unique_object)


def save_json(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                   encoding='utf-8')
    tmp.replace(target)


# ---------------------------------------------------------------- small helpers

def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _present(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def _nfc(value):
    return unicodedata.normalize('NFC', value) if isinstance(value, str) else value


def is_iri(value):
    text = _text(value)
    return bool(text) and bool(SCHEME_RE.match(text)) and not BAD_IRI_CHARS.search(text)


def parse_iso8601(value):
    """Parse an ISO 8601 calendar date or date-time; return datetime or None.

    Accepts a trailing Z, comma or dot fractions, and +hhmm/+hh:mm offsets,
    so behaviour does not depend on the running Python 3.10/3.11 version.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text[-1:] in ('Z', 'z'):
        text = text[:-1] + '+00:00'
    text = text.replace(',', '.')
    text = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', text)
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def _literal_lexical(value):
    """Return the lexical form of a literal value, or None if unsupported."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return _nfc(value)
    if isinstance(value, (int, float)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    return None


def _infer_object_type(value):
    if isinstance(value, str) and is_iri(value):
        return 'uri'
    return 'literal'


def canonical_triple_line(triple):
    """One deterministic, whitespace-stable line for one assertion triple."""
    subject = _nfc(_text(triple.get('s')))
    predicate = _nfc(_text(triple.get('p')))
    obj = triple.get('o')
    kind = triple.get('o_type') or _infer_object_type(obj)
    if kind == 'uri':
        rendered = '<' + _nfc(_text(obj)) + '>'
    else:
        lexical = _literal_lexical(obj)
        if lexical is None:
            return None
        rendered = json.dumps(lexical, ensure_ascii=False)
        datatype = _nfc(_text(triple.get('datatype')))
        language = _text(triple.get('language'))
        if datatype:
            rendered += '^^<' + datatype + '>'
        elif language:
            rendered += '@' + language.lower()
    return f'{subject} {predicate} {rendered}'


def canonicalise(triples):
    """Return (sorted unique lines, skipped indices, duplicate count)."""
    lines, skipped = [], []
    for index, triple in enumerate(triples):
        if not isinstance(triple, dict):
            skipped.append(index)
            continue
        if not (_text(triple.get('s')) and _text(triple.get('p')) and _present(triple.get('o'))):
            skipped.append(index)
            continue
        line = canonical_triple_line(triple)
        if line is None:
            skipped.append(index)
            continue
        lines.append(line)
    unique = sorted(set(lines))
    return unique, skipped, len(lines) - len(unique)


def hash_lines(lines):
    payload = ''.join(line + '\n' for line in lines).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


class Report:
    def __init__(self):
        self.checks = []

    def add(self, check_id, status, detail):
        assert status in ('pass', 'fail', 'warn')
        self.checks.append({'id': check_id, 'status': status, 'detail': detail})


def _short(items, limit=6):
    items = list(items)
    if len(items) <= limit:
        return ', '.join(str(item) for item in items)
    return ', '.join(str(item) for item in items[:limit]) + f', ... (+{len(items) - limit} more)'


# ---------------------------------------------------------------- the checks

def check_document(document, expect_hash=None):
    """Run all checks in publication order and return the report dict."""
    if not isinstance(document, dict):
        raise ValueError('top-level JSON must be an object containing "np"')
    np_obj = np_object(document)
    raw_container = document if 'np' in document else np_obj

    report = Report()

    # -- 1. four-graph structure -------------------------------------------
    head = _text(np_obj.get('id'))
    if not head:
        report.add('structure.head_graph', 'fail',
                   'head graph identifier (np.id) is missing or empty')
    elif not is_iri(head):
        report.add('structure.head_graph', 'warn',
                   f'head graph id {head!r} is not an absolute IRI; local draft only')
    else:
        report.add('structure.head_graph', 'pass', f'head graph id: {head}')

    assertion = np_obj.get('assertion')
    provenance = np_obj.get('provenance')
    pubinfo = np_obj.get('pubinfo')
    shape_issues = []
    if not isinstance(assertion, list):
        shape_issues.append('assertion graph must be a list of triples')
    if not isinstance(provenance, dict):
        shape_issues.append('provenance graph must be an object')
    if not isinstance(pubinfo, dict):
        shape_issues.append('pubinfo graph must be an object')
    if shape_issues:
        report.add('structure.named_graphs', 'fail',
                   'incomplete four-graph structure: ' + '; '.join(shape_issues))
    else:
        report.add('structure.named_graphs', 'pass',
                   'assertion, provenance and pubinfo graphs are present')

    # -- 2. template compliance --------------------------------------------
    triples = assertion if isinstance(assertion, list) else []
    provenance = provenance if isinstance(provenance, dict) else {}
    pubinfo = pubinfo if isinstance(pubinfo, dict) else {}

    if not triples:
        report.add('assertion.nonempty', 'fail', 'assertion graph is missing or empty')
    else:
        report.add('assertion.nonempty', 'pass', f'assertion graph has {len(triples)} triple(s)')

    malformed, bad_terms, bare_literals = [], [], []
    for index, triple in enumerate(triples):
        if not isinstance(triple, dict):
            malformed.append(f'#{index} is not an object')
            continue
        missing = [key for key in ('s', 'p', 'o') if not _present(triple.get(key))]
        if missing:
            malformed.append(f'#{index} missing {"/".join(missing)}')
            continue
        kind = triple.get('o_type')
        if kind is not None and kind not in ('literal', 'uri'):
            malformed.append(f'#{index} o_type must be "literal" or "uri"')
            continue
        if not is_iri(triple.get('s')):
            bad_terms.append(f'#{index} subject {_text(triple.get("s"))!r}')
        if not is_iri(triple.get('p')):
            bad_terms.append(f'#{index} predicate {_text(triple.get("p"))!r}')
        resolved = kind or _infer_object_type(triple.get('o'))
        if resolved == 'uri':
            if not is_iri(triple.get('o')):
                bad_terms.append(f'#{index} object {_text(triple.get("o"))!r}')
        elif not _text(triple.get('datatype')) and not _text(triple.get('language')):
            bare_literals.append(f'#{index}')

    if malformed:
        report.add('assertion.triples', 'fail',
                   f'{len(malformed)} malformed of {len(triples)} triple(s): {_short(malformed)}')
    else:
        report.add('assertion.triples', 'pass',
                   f'all {len(triples)} triple(s) carry non-empty s, p and o')

    if bad_terms:
        report.add('assertion.term_format', 'warn',
                   f'terms are not absolute IRIs: {_short(bad_terms)}')
    else:
        report.add('assertion.term_format', 'pass',
                   'subjects, predicates and URI objects are absolute IRIs')

    if bare_literals:
        report.add('assertion.literal_datatype', 'warn',
                   'literal object without datatype or language tag (implicit xsd:string): '
                   + _short(bare_literals))
    else:
        report.add('assertion.literal_datatype', 'pass',
                   'every literal object carries a datatype or language tag')

    required_missing, required_bad = [], []
    for key in ('assertedBy', 'generatedAt'):
        if not _text(provenance.get(key)):
            required_missing.append(key)
    if 'sources' not in provenance:
        required_missing.append('sources')
    elif not isinstance(provenance.get('sources'), list):
        required_bad.append('sources must be a list')
    if required_missing or required_bad:
        detail = []
        if required_missing:
            detail.append('missing ' + ', '.join(required_missing))
        detail.extend(required_bad)
        report.add('provenance.required', 'fail', '; '.join(detail))
    else:
        report.add('provenance.required', 'pass',
                   'assertedBy, generatedAt and sources are present')

    sources = provenance.get('sources')
    if not isinstance(sources, list):
        report.add('provenance.sources', 'fail', 'sources is not a list')
    else:
        invalid = [f'#{i}' for i, item in enumerate(sources)
                   if not isinstance(item, str) or not item.strip()]
        reason = _source_rationale(provenance, np_obj, raw_container)
        if invalid:
            report.add('provenance.sources', 'fail',
                       'sources entries must be non-empty strings: ' + _short(invalid))
        elif sources:
            report.add('provenance.sources', 'pass', f'{len(sources)} source(s) recorded')
        elif reason:
            report.add('provenance.sources', 'pass',
                       f'sources is empty; documented reason ({reason[0]}): {reason[1]}')
        else:
            report.add('provenance.sources', 'fail',
                       'sources is empty and no reason is recorded '
                       '(use provenance.sourcesNote or np.notes)')

    pub_missing = [key for key in ('createdBy', 'createdAt') if not _text(pubinfo.get(key))]
    if pub_missing:
        report.add('pubinfo.required', 'fail', 'missing ' + ', '.join(pub_missing))
    else:
        report.add('pubinfo.required', 'pass', 'createdBy and createdAt are present')

    stamp_issues, stamp_checked = [], 0
    for label, container, key in (('provenance.generatedAt', provenance, 'generatedAt'),
                                  ('pubinfo.createdAt', pubinfo, 'createdAt')):
        value = container.get(key)
        if not _present(value):
            continue  # absence is reported by provenance.required / pubinfo.required
        stamp_checked += 1
        if parse_iso8601(value) is None:
            stamp_issues.append(f'{label}={value!r}')
    if stamp_issues:
        report.add('timestamps.iso8601', 'fail',
                   'not ISO 8601: ' + _short(stamp_issues))
    elif not stamp_checked:
        report.add('timestamps.iso8601', 'pass', 'no timestamp present to parse')
    else:
        report.add('timestamps.iso8601', 'pass',
                   f'{stamp_checked} timestamp(s) parse as ISO 8601')

    # -- 3. deterministic content hash -------------------------------------
    lines, skipped, duplicates = canonicalise(triples)
    digest = hash_lines(lines)
    detail = (f'sha256 over {len(lines)} canonical assertion line(s), sorted and '
              'LF-terminated; local trusty-URI stand-in, not a signature')
    if duplicates:
        detail += f'; {duplicates} duplicate triple(s) collapsed'
    if skipped:
        detail += f'; {len(skipped)} malformed triple(s) excluded'
    report.add('assertion.hash', 'pass', detail)

    if expect_hash is not None:
        expected = _text(expect_hash).lower()
        if not HEX64.match(expected):
            report.add('hash.matches_expected', 'fail',
                       'expected hash must be 64 hexadecimal characters')
        elif expected == digest:
            report.add('hash.matches_expected', 'pass', 'recomputed hash matches the expected value')
        else:
            report.add('hash.matches_expected', 'fail',
                       f'expected {expected}, recomputed {digest}')

    order = {check_id: index for index, check_id in enumerate(CHECK_ORDER)}
    report.checks.sort(key=lambda check: order.get(check['id'], len(order)))

    statuses = [check['status'] for check in report.checks]
    publishable = 'fail' not in statuses
    summary = {
        'total': len(report.checks),
        'pass': statuses.count('pass'),
        'warn': statuses.count('warn'),
        'fail': statuses.count('fail'),
        'failed_checks': [check['id'] for check in report.checks if check['status'] == 'fail'],
        'warned_checks': [check['id'] for check in report.checks if check['status'] == 'warn'],
        'assertion_triples': len(triples),
        'assertion_hash_lines': len(lines),
        'duplicate_triples': duplicates,
        'hash_algorithm': 'sha256 over NFC-normalised, sorted canonical assertion lines',
        'hash_line_format': 's p o, one per line, LF-terminated; URI as <iri>, '
                            'literal as JSON string plus ^^<datatype> or @language',
    }
    return {
        'checks': report.checks,
        'assertion_hash_sha256': digest,
        'publishable': publishable,
        'summary': summary,
    }


def np_object(document):
    """Return the nanopublication object, accepting a bare object as well."""
    if not isinstance(document, dict):
        return {}
    value = document.get('np') if 'np' in document else document
    return value if isinstance(value, dict) else {}


def assertion_triples(document):
    value = np_object(document).get('assertion')
    return value if isinstance(value, list) else []


def _source_rationale(*containers):
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in RATIONALE_KEYS:
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return key, value.strip()
        notes = container.get('notes')
        if isinstance(notes, str) and notes.strip():
            return 'notes', notes.strip()
    return None


# ---------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Check nanopublication structure and content hash before publishing.')
    parser.add_argument('input', type=Path, help='nanopublication JSON with an "np" object')
    parser.add_argument('output', type=Path, help='report JSON to write')
    parser.add_argument('--hash-input', type=Path, default=None,
                        help='also write the exact canonical lines that were hashed')
    parser.add_argument('--expect-hash', default=None,
                        help='fail unless the recomputed hash equals this value')
    args = parser.parse_args(argv)

    try:
        document = load_json(args.input)
    except (OSError, ValueError) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__, 'detail': str(exc)},
                         ensure_ascii=False))
        return 2

    try:
        result = check_document(document, expect_hash=args.expect_hash)
    except ValueError as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__, 'detail': str(exc)},
                         ensure_ascii=False))
        return 2

    if args.hash_input is not None:
        lines, _, _ = canonicalise(assertion_triples(document))
        args.hash_input.parent.mkdir(parents=True, exist_ok=True)
        with open(args.hash_input, 'w', encoding='utf-8', newline='\n') as handle:
            for line in lines:
                handle.write(line + '\n')

    save_json(args.output, result)
    summary = result['summary']
    print(json.dumps({
        'publishable': result['publishable'],
        'assertion_hash_sha256': result['assertion_hash_sha256'],
        'pass': summary['pass'], 'warn': summary['warn'], 'fail': summary['fail'],
        'failed_checks': summary['failed_checks'],
    }, ensure_ascii=False))
    return 0 if result['publishable'] else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
