"""Offline passage retrieval and literal evidence anchoring; not a scientific fact checker."""
import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

from core import canonical, file_sha256, literal_match, load, read, relative_file, save

STOPWORDS = set('a an the and or of to for in on at by from with is are was were be been this that it as what which how does do'.split())


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def within(root, name):
    return relative_file(root, name)


def tokens(text):
    result = []
    for term in re.findall(r'[a-z0-9]+(?:\.[0-9]+)?|[\u3400-\u9fff]+', canonical(text).casefold()):
        if re.fullmatch(r'[\u3400-\u9fff]+', term):
            result.extend(term[i:i + 2] for i in range(len(term) - 1)) if len(term) > 1 else result.append(term)
        elif term not in STOPWORDS:
            result.append(term)
    return result


def spans(text, max_chars):
    start = 0
    while start < len(text):
        while start < len(text) and text[start].isspace():
            start += 1
        if start == len(text):
            break
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = max(text.rfind('\n', start, end), text.rfind(' ', start, end))
            if boundary > start + max_chars // 2:
                end = boundary
        while end > start and text[end - 1].isspace():
            end -= 1
        yield start, end
        start = end


def text_passages(text, max_chars):
    heading = []
    start = None
    offset = 0
    def emit(a, b):
        if a is None:
            return []
        return [{'quote': text[a + x:a + y], 'kind': 'text', 'section_path': list(heading),
                 'locator': {'line_start': text.count('\n', 0, a + x) + 1,
                             'line_end': text.count('\n', 0, a + y - 1) + 1,
                             'char_start': a + x, 'char_end': a + y}}
                for x, y in spans(text[a:b], max_chars)]
    for line in text.splitlines(keepends=True):
        match = re.match(r'^(#{1,6})\s+(.+?)\s*$', line)
        if match or not line.strip():
            yield from emit(start, offset)
            start = None
            if match:
                level = len(match.group(1))
                heading = heading[:level - 1] + [match.group(2)]
        elif start is None:
            start = offset
        offset += len(line)
    yield from emit(start, len(text))


def layout_passages(data, max_chars):
    if not isinstance(data.get('pages'), list):
        raise ValueError('layout must contain normalized pages; import Docling JSON with layout.py first')
    headings = []
    sections = {}
    for item in data.get('items', []):
        if item.get('body_order') is None:
            sections[item['source_ref']] = []
            continue
        if item.get('heading_level'):
            headings = headings[:item['heading_level'] - 1] + [canonical(item['text'])]
        sections[item['source_ref']] = list(headings)
    headings = []
    for page in data['pages']:
        page_no = page['pdf_page']
        for block in page.get('blocks', []):
            if block.get('kind') == 'repeated_margin_candidate':
                continue
            text = block.get('text', '')
            if not isinstance(text, str):
                raise ValueError('layout block text must be a string')
            if block.get('heading_level'):
                level = block['heading_level']
                if type(level) != int or level < 1:
                    raise ValueError('heading level must be a positive integer')
                headings = headings[:level - 1] + [canonical(text)]
                continue
            for a, b in spans(text, max_chars):
                locator = {'pdf_page': page_no, 'element_id': block['id'], 'bbox': block.get('bbox'),
                           'element_char_start': a, 'element_char_end': b}
                for key in ('source_ref', 'provenance', 'charspan', 'text_basis'):
                    if key in block:
                        locator[key] = block[key]
                yield {'quote': text[a:b], 'kind': block.get('kind', 'text'),
                       'section_path': sections.get(block.get('source_ref'), list(headings)), 'locator': locator,
                       'needs_visual_review': page.get('needs_visual_review', False)}
        for table in page.get('tables', []):
            rows = table.get('rows', [])
            if not isinstance(rows, list):
                raise ValueError('table rows must be a list')
            row_text = [' | '.join('' if cell is None else str(cell) for cell in row) for row in rows]
            text = '\n'.join(row_text)
            for a, b in spans(text, max_chars):
                yield {'quote': text[a:b], 'kind': 'table_serialization',
                       'section_path': sections.get(table.get('source_ref'), []),
                       'locator': {'pdf_page': page_no, 'element_id': table['id'], 'bbox': table.get('bbox'),
                                   'row_start': text.count('\n', 0, a), 'row_end': text.count('\n', 0, b - 1),
                                   'char_start': a, 'char_end': b, 'source_ref': table.get('source_ref'),
                                   'provenance': table.get('provenance', [])},
                       'needs_visual_review': True}


def build_index(root, manifest_path, max_chars=1600):
    if type(max_chars) != int or not 80 <= max_chars <= 10000:
        raise ValueError('max_chars must be between 80 and 10000')
    manifest = load(manifest_path)
    sources = manifest.get('sources')
    if not isinstance(sources, list) or not sources:
        raise ValueError('manifest needs a nonempty sources list')
    indexed = []
    chunks = []
    seen = set()
    warnings = []
    for source in sources:
        if not isinstance(source, dict) or set(source) - {'file', 'layout'}:
            raise ValueError('each source must specify file and optionally layout')
        original = within(root, source['file'])
        name = original.relative_to(root.resolve()).as_posix()
        if name in seen:
            raise ValueError(f'duplicate source: {name}')
        seen.add(name)
        fingerprint = file_sha256(original)
        record = {'file': name, 'sha256': fingerprint}
        if 'layout' in source:
            artifact = within(manifest_path.resolve().parent, source['layout'])
            layout = load(artifact)
            if str(layout.get('source', '')).replace('\\', '/').split('/')[-1] != original.name:
                raise ValueError(f'layout source name differs from original: {name}')
            if layout.get('backend') == 'docling-json (stdlib)':
                if original.suffix.lower() != '.json' or layout.get('origin_verified') is not False:
                    raise ValueError(f'Docling evidence must cite its export JSON: {name}')
                field = 'input_sha256'
            else:
                if original.suffix.lower() != '.pdf':
                    raise ValueError(f'PDF layout evidence must cite its source PDF: {name}')
                field = 'source_sha256'
            if layout.get(field) != fingerprint:
                raise ValueError(f'layout is stale or missing its source hash: {name}')
            record.update({'layout_artifact': source['layout'], 'layout_sha256': sha256(artifact.read_bytes()),
                           'extraction_verified': False})
            items = list(layout_passages(layout, max_chars))
            if layout.get('unmapped_items'):
                warnings.append({'file': name, 'issue': 'layout contains unmapped items; inspect the layout artifact'})
        else:
            items = list(text_passages(read(original), max_chars))
        indexed.append(record)
        for item in items:
            identity = json.dumps([name, fingerprint, item['locator'], item['quote']], ensure_ascii=False, sort_keys=True)
            item.update({'id': 'E' + sha256(identity.encode('utf-8'))[:24], 'file': name,
                         'source_sha256': fingerprint, 'quote_sha256': sha256(item['quote'].encode('utf-8'))})
            chunks.append(item)
        if not items:
            warnings.append({'file': name, 'issue': 'no searchable passages'})
    return {'schema_version': 1, 'sources': indexed, 'chunks': chunks, 'warnings': warnings,
            'scope': 'Explicit local sources only; extracted text and source hashes are not proof of scientific support.'}


def search(index, queries, limit=5, per_source=3):
    if type(limit) != int or not 1 <= limit <= 50 or type(per_source) != int or not 1 <= per_source <= 50:
        raise ValueError('limit and per_source must be integers between 1 and 50')
    chunks = index['chunks']
    counters = [Counter(tokens(chunk['quote'])) for chunk in chunks]
    frequencies = Counter(term for counts in counters for term in counts)
    lengths = [sum(counts.values()) for counts in counters]
    average = sum(lengths) / max(len(chunks), 1) or 1
    answers = []
    seen = set()
    for query in queries:
        if not isinstance(query, dict) or set(query) - {'id', 'query', 'files', 'pages', 'kinds'}:
            raise ValueError('queries accept id, query, files, pages and kinds only')
        key, text = query['id'], query['query']
        if not isinstance(key, str) or not key or key in seen or not isinstance(text, str) or not text.strip():
            raise ValueError('queries need unique nonempty IDs and nonempty query strings')
        seen.add(key)
        for field, kind in [('files', str), ('pages', int), ('kinds', str)]:
            if field in query and (not isinstance(query[field], list) or any(type(x) != kind for x in query[field])):
                raise ValueError(f'{field} must be a list of {kind.__name__}')
        terms = set(tokens(text))
        ranked = []
        for i, chunk in enumerate(chunks):
            if 'files' in query and chunk['file'] not in query['files']:
                continue
            if 'pages' in query and chunk['locator'].get('pdf_page') not in query['pages']:
                continue
            if 'kinds' in query and chunk['kind'] not in query['kinds']:
                continue
            matched = sorted(terms & counters[i].keys())
            if not matched:
                continue
            score = 0.0
            for term in matched:
                idf = math.log(1 + (len(chunks) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
                tf = counters[i][term]
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * lengths[i] / average))
            ranked.append((score, chunk, matched))
        ranked.sort(key=lambda row: (-row[0], row[1]['file'], row[1]['id']))
        matches = []
        counts = Counter()
        for score, chunk, matched in ranked:
            if counts[chunk['file']] >= per_source:
                continue
            counts[chunk['file']] += 1
            matches.append({**chunk, 'score': round(score, 6), 'matched_terms': matched,
                            'evidence_status': 'retrieved_not_semantically_verified'})
            if len(matches) >= limit:
                break
        answers.append({'id': key, 'query': text, 'status': 'matched' if matches else 'no_match',
                        'matches': matches, 'unmatched_terms': sorted(terms - frequencies.keys())})
    return {'query_results': answers, 'method': 'BM25 sparse lexical retrieval (k1=1.2, b=0.75)',
            'scope': 'Ranking is lexical relevance, not entailment; no match is not evidence of nonexistence.'}


def verify(root, index, ledger):
    chunks = {chunk['id']: chunk for chunk in index['chunks']}
    sources = {source['file']: source for source in index['sources']}
    current, raw_text = {}, {}
    for name, source in sources.items():
        try:
            p = within(root, name)
            current[name] = file_sha256(p) == source['sha256']
            if current[name] and 'layout_artifact' not in source:
                raw_text[name] = read(p)
        except (ValueError, OSError):
            current[name] = False
    claims = []
    seen = set()
    for claim in ledger['claims']:
        key = claim['id']
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError('claims need unique nonempty IDs')
        seen.add(key)
        if not isinstance(claim.get('statement'), str) or not claim['statement'].strip():
            raise ValueError('each claim needs a statement')
        evidence = claim.get('evidence', [])
        if not isinstance(evidence, list):
            raise ValueError('claim evidence must be a list')
        checked = []
        for link in evidence:
            chunk = chunks.get(link['chunk_id'])
            quote = link.get('quote')
            reason = None
            if chunk is None:
                reason = 'unknown_chunk'
            elif not current.get(chunk['file'], False):
                reason = 'source_missing_or_changed'
            elif sha256(chunk['quote'].encode('utf-8')) != chunk['quote_sha256']:
                reason = 'indexed_quote_changed'
            elif chunk.get('source_sha256') != sources[chunk['file']]['sha256']:
                reason = 'indexed_source_hash_changed'
            elif chunk['file'] in raw_text and not matches_text_source(chunk, raw_text[chunk['file']]):
                reason = 'passage_differs_from_source_location'
            elif not isinstance(quote, str) or not literal_match(quote, chunk['quote']):
                reason = 'quote_not_in_passage'
            checked.append({'chunk_id': link['chunk_id'], 'anchor_valid': reason is None, 'reason': reason,
                            'file': chunk['file'] if chunk else None, 'locator': chunk['locator'] if chunk else None,
                            'basis': ('source_characters' if chunk['file'] in raw_text else 'layout_index_excerpt') if chunk else None})
        claims.append({'id': key, 'statement': claim['statement'], 'anchors_valid': bool(checked) and all(e['anchor_valid'] for e in checked),
                       'evidence': checked, 'semantic_support': 'not_assessed'})
    return {'claims': claims, 'all_anchors_valid': bool(claims) and all(c['anchors_valid'] for c in claims),
            'scope': 'Checks source hashes and literal excerpts. Plain text is replayed from original characters; layout excerpts are checked against the index, not the page image. No semantic support is assessed.'}


def matches_text_source(chunk, text):
    locator = chunk['locator']
    a, b = locator.get('char_start'), locator.get('char_end')
    return (type(a) is int and type(b) is int and 0 <= a < b <= len(text)
            and text[a:b] == chunk['quote']
            and locator.get('line_start') == text.count('\n', 0, a) + 1
            and locator.get('line_end') == text.count('\n', 0, b - 1) + 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    index = modes.add_parser('index')
    index.add_argument('root', type=Path); index.add_argument('manifest', type=Path); index.add_argument('output', type=Path)
    index.add_argument('--max-chars', type=int, default=1600)
    query = modes.add_parser('search')
    query.add_argument('index', type=Path); query.add_argument('queries', type=Path); query.add_argument('output', type=Path)
    query.add_argument('--limit', type=int, default=5); query.add_argument('--per-source', type=int, default=3)
    check = modes.add_parser('verify')
    check.add_argument('root', type=Path); check.add_argument('index', type=Path)
    check.add_argument('ledger', type=Path); check.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.mode == 'index':
        result = build_index(args.root.resolve(), args.manifest, args.max_chars)
    elif args.mode == 'search':
        result = search(load(args.index), load(args.queries)['queries'], args.limit, args.per_source)
    else:
        result = verify(args.root.resolve(), load(args.index), load(args.ledger))
    save(args.output, result)
    return int(args.mode == 'verify' and not result['all_anchors_valid'])


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__, 'detail': str(exc)}, ensure_ascii=False))
        sys.exit(2)
