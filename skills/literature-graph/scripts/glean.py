"""Relation coverage check with a gleaning pass; Python 3.10+, standard library only.

Pass 1 verifies that every relation's evidence sentence really occurs in the
source (after whitespace normalisation); failures stay in the report as
``unsupported`` so the caller can drop them instead of keeping untraceable
edges.  Pass 2 re-reads the source and gleans sentences that mention at least
two known entities but have no relation between them; those pairs are only
``candidate`` review items and never confirmed edges.  Entities whose
normalised names are identical (case, whitespace, punctuation) are reported as
merge suggestions; similar but distinct names are left alone.

The two passes follow the ideas of graphrag's ``max_gleanings`` second
extraction pass and paper2lkg's backtranslation-style coverage check (both
MIT); this is an independent standard-library implementation, not copied code.
"""
import argparse
import json
import re
import sys
import unicodedata
from itertools import combinations
from pathlib import Path

POLICY = 'candidate 必须人工确认，不得直接写入图谱'
SPACE = re.compile(r'\s+')
NONWORD = re.compile(r'[\W_]+', re.UNICODE)

def norm_space(text):
    return SPACE.sub(' ', unicodedata.normalize('NFC', text)).strip()

def merge_key(name):
    """Case/whitespace/punctuation-insensitive identity, never fuzzy."""
    return NONWORD.sub('', unicodedata.normalize('NFC', name).casefold())

def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)

def evidence_texts(raw):
    """Accept a sentence, an evidence list, or graph-model {quote} objects."""
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    texts = []
    for item in raw:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict):
            for key in ('quote', 'evidence', 'text', 'sentence'):
                if isinstance(item.get(key), str):
                    texts.append(item[key])
                    break
    return texts

def parse_graph(data):
    """Read {entities, relations}; also accept the skill model {nodes, edges}."""
    if not isinstance(data, dict):
        raise ValueError('graph must be a JSON object')
    raw_entities = data.get('entities')
    if raw_entities is None and 'nodes' in data:
        raw_entities = [{'id': n.get('id'), 'name': n.get('name') or n.get('label'),
                         'type': n.get('type', ''), 'description': n.get('description', '')}
                        if isinstance(n, dict) else n for n in data['nodes']]
    if raw_entities is None:
        raise ValueError('graph needs an entities or nodes list')
    raw_relations = data.get('relations')
    if raw_relations is None and 'edges' in data:
        raw_relations = [{'id': e.get('id'), 'head': e.get('head') or e.get('source'),
                          'tail': e.get('tail') or e.get('target'),
                          'type': e.get('type') or e.get('predicate', ''),
                          'evidence': e.get('evidence', [])}
                         if isinstance(e, dict) else e for e in data['edges']]
    if raw_relations is None:
        raise ValueError('graph needs a relations or edges list')
    if not isinstance(raw_entities, list) or not isinstance(raw_relations, list):
        raise ValueError('entities and relations must be lists')
    entities = []
    seen_entities = set()
    for rec in raw_entities:
        if not isinstance(rec, dict):
            raise ValueError('each entity must be an object')
        eid = rec.get('id')
        name = rec.get('name')
        if not isinstance(eid, str) or not eid.strip():
            raise ValueError(f'missing entity id: {eid!r}')
        if eid in seen_entities:
            raise ValueError(f'duplicate entity id: {eid}')
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f'{eid}: missing name')
        seen_entities.add(eid)
        entities.append({'id': eid, 'name': name, 'type': rec.get('type', ''),
                         'description': rec.get('description', '')})
    relations = []
    seen_relations = set()
    for rec in raw_relations:
        if not isinstance(rec, dict):
            raise ValueError('each relation must be an object')
        rid = rec.get('id')
        if not isinstance(rid, str) or not rid.strip():
            raise ValueError(f'missing relation id: {rid!r}')
        if rid in seen_relations:
            raise ValueError(f'duplicate relation id: {rid}')
        head, tail = rec.get('head'), rec.get('tail')
        if not isinstance(head, str) or not head.strip():
            raise ValueError(f'{rid}: missing head')
        if not isinstance(tail, str) or not tail.strip():
            raise ValueError(f'{rid}: missing tail')
        seen_relations.add(rid)
        relations.append({'id': rid, 'head': head, 'tail': tail,
                          'type': rec.get('type', ''),
                          'evidence': evidence_texts(rec.get('evidence'))})
    return entities, relations

def split_sentences(text):
    sentences = []
    for block in re.split(r'\n+', text):
        block = norm_space(block)
        if not block:
            continue
        parts = re.split(r'(?<=[。！？!?；;])\s*|(?<=\.)\s+(?=[A-Z0-9“"\'(\[])', block)
        for part in parts:
            part = norm_space(part)
            if part:
                sentences.append(part)
    return sentences

def name_pattern(name):
    left = r'(?<![0-9A-Za-z])' if re.match(r'[0-9A-Za-z]', name[0]) else ''
    right = r'(?![0-9A-Za-z])' if re.match(r'[0-9A-Za-z]', name[-1]) else ''
    return re.compile(left + re.escape(name) + right, re.IGNORECASE)

def mention_keys(sentence, name_index):
    """Entity groups mentioned in order; nested shorter names are suppressed."""
    found = []
    for name, key in name_index:
        for match in name_pattern(name).finditer(sentence):
            found.append((match.start(), match.end(), key))
    found.sort(key=lambda row: (row[0], -(row[1] - row[0])))
    accepted = []
    for start, end, key in found:
        if any(s <= start and end <= e and (e - s) > (end - start) for s, e, _ in accepted):
            continue
        accepted.append((start, end, key))
    accepted.sort()
    order = []
    for _, _, key in accepted:
        if key not in order:
            order.append(key)
    return order

def analyse(graph, source_text, rounds=2):
    entities, relations = parse_graph(graph)
    haystack = norm_space(source_text)
    sentences = split_sentences(source_text)

    groups = {}
    for entity in entities:
        groups.setdefault(merge_key(entity['name']) or 'id:' + entity['id'], []).append(entity)
    merges = [[entity['id'] for entity in group] for group in groups.values() if len(group) > 1]
    representative = {}
    group_of = {}
    for key, group in groups.items():
        representative[key] = group[0]['id']
        for entity in group:
            group_of[entity['id']] = key

    def pair_key(head, tail):
        left = group_of.get(head, '?' + str(head))
        right = group_of.get(tail, '?' + str(tail))
        return None if left == right else frozenset((left, right))

    rows = []
    supported = unsupported = 0
    covered = set()
    for relation in relations:
        texts = [norm_space(text) for text in relation['evidence']]
        texts = [text for text in texts if text]
        hit = next((text for text in texts if text in haystack), '')
        status = 'supported' if hit else 'unsupported'
        supported += status == 'supported'
        unsupported += status == 'unsupported'
        rows.append({'id': relation['id'], 'status': status,
                     'evidence': hit or (texts[0] if texts else '')})
        key = pair_key(relation['head'], relation['tail'])
        if key is not None:
            covered.add(key)

    name_index = []
    for key, group in groups.items():
        seen = set()
        for entity in group:
            variant = norm_space(entity['name'])
            if variant and variant.casefold() not in seen:
                seen.add(variant.casefold())
                name_index.append((variant, key))

    candidates = []
    counter = 0
    rounds_run = 0
    relation_ids = {row['id'] for row in rows}
    for _ in range(max(1, int(rounds))):
        rounds_run += 1
        for sentence in sentences:
            keys = mention_keys(sentence, name_index)
            for left, right in combinations(keys, 2):
                key = frozenset((left, right))
                if key in covered:
                    continue
                covered.add(key)
                counter += 1
                while f'C{counter}' in relation_ids:
                    counter += 1
                candidates.append({'id': f'C{counter}', 'status': 'candidate',
                                   'head': representative[left], 'tail': representative[right],
                                   'evidence': sentence})

    return {'relations': rows + candidates,
            'merges': merges,
            'summary': {'supported': supported, 'unsupported': unsupported,
                        'candidate': len(candidates), 'rounds': rounds_run},
            'policy': POLICY}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('graph', type=Path, help='entities/relations JSON')
    parser.add_argument('source', type=Path, help='source text the evidence must come from')
    parser.add_argument('output', type=Path, help='coverage report JSON')
    parser.add_argument('--rounds', type=int, default=2, help='gleaning passes (default 2)')
    args = parser.parse_args(argv)
    if args.rounds < 1:
        parser.error('--rounds must be at least 1')
    graph = read_json(args.graph)
    source_text = args.source.read_text(encoding='utf-8-sig')
    result = analyse(graph, source_text, args.rounds)
    write_json(args.output, result)
    print(json.dumps({'ok': True, 'output': str(args.output),
                      'summary': result['summary'], 'merges': len(result['merges'])},
                     ensure_ascii=False))
    return 0

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__, 'detail': str(exc)},
                         ensure_ascii=False))
        sys.exit(2)
