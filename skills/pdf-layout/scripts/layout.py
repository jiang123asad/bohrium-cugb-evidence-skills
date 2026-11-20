"""PDF extraction or offline DoclingDocument JSON import; preserves layout evidence."""
import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

from core import file_sha256, finite_float, reject_number, save, unique_object

def order(blocks, width):
    if not blocks:
        return [], None
    # Prefer a gutter with substantial overlapping left/right text bands and few cutters.
    options=[]
    for fraction in (0.43, 0.46, 0.48, 0.50, 0.52, 0.54, 0.57):
        x=width*fraction
        left=[b for b in blocks if b['bbox'][2] < x-4 and len(b['text'])>=18]
        right=[b for b in blocks if b['bbox'][0] > x+4 and len(b['text'])>=18]
        smaller=min(len(left),len(right))
        if smaller<1:
            continue
        top=max(min(b['bbox'][1] for b in g) for g in (left,right))
        bot=min(max(b['bbox'][3] for b in g) for g in (left,right))
        if bot-top<18:
            continue
        if smaller==1:
            single=(left if len(left)==1 else right)[0]['bbox']
            span=max(b['bbox'][3] for b in left+right)-min(b['bbox'][1] for b in left+right)
            if max(len(left),len(right))<3 or single[2]-single[0]<width*.22 or (bot-top)/span<.45:
                continue
        cross=0
        for b in blocks:
            bb=b['bbox']
            if not (bb[0]<x-4 and bb[2]>x+4 and top<bb[1]<bot):
                continue
            overlaps=any(min(bb[3],c['bbox'][3])-max(bb[1],c['bbox'][1])>2 for c in left+right)
            if overlaps or bb[2]-bb[0]<width*.5:
                cross+=1
        options.append((smaller-cross*1.5+(bot-top)/3000, -abs(fraction-.50),x,cross))
    xs=max(options) if options else None
    by_y=lambda b:(round(b['bbox'][1],1),b['bbox'][0])
    if not xs or (xs[0]<1.5 and not (xs[0]>=0.9 and xs[3]==0)):
        return sorted(blocks,key=by_y),None
    x=xs[2]
    full=[b for b in blocks if b['bbox'][0]<x-4 and b['bbox'][2]>x+4]
    columns=[b for b in blocks if b not in full]
    result=[]
    for b in sorted(full,key=by_y):
        band=[c for c in columns if c['bbox'][1]<b['bbox'][1]]
        result.extend(sorted(band,key=lambda c:(c['bbox'][0]>=x-4,*by_y(c))))
        columns=[c for c in columns if c not in band]
        result.append(b)
    result.extend(sorted(columns,key=lambda c:(c['bbox'][0]>=x-4,*by_y(c))))
    return result,round(x,2)

def kind(text):
    t=' '.join(text.split())
    if re.match(r'^(?:fig(?:ure)?\.?\s*\d|图\s*\d)',t,re.I): return 'figure_caption_candidate'
    if re.match(r'^(?:tab(?:le)?\.?\s*\d|表\s*\d)',t,re.I): return 'table_caption_candidate'
    if re.fullmatch(r'(?:abstract|references|bibliography|摘要|参考文献|acknowledg(?:e)?ments)[:：]?',t,re.I):
        return 'heading_candidate'
    if len(t)<100 and '\n' not in text and re.match(r'^\d+(?:\.\d+)*\.?\s+\D',t): return 'heading_candidate'
    if len(t)<400 and re.search(r'\(\d+[A-Za-z]?\)\s*$',t) and re.search(r'[=∑∏∫±×·≤≥<>]|\b(?:sin|cos|tan|log|exp|ln|max|min)\b',t):
        return 'formula_candidate'
    return 'text'

def heading_level(text):
    t=' '.join(text.split())
    m=re.match(r'^(\d+(?:\.\d+)*)\.?\s+\D',t)
    if m: return m.group(1).count('.')+1
    if re.fullmatch(r'(?:abstract|references|bibliography|摘要|参考文献|acknowledg(?:e)?ments)[:：]?',t,re.I): return 1
    return None

def box(obj):
    return [round(float(obj[k]),2) for k in ('x0','top','x1','bottom')]

def extract(path, chosen=None, with_tables=False, password=''):
    import pdfplumber
    pages=[]
    counts=Counter()
    with pdfplumber.open(path,laparams={},password=password) as doc:
        total=len(doc.pages)
        requested=set(chosen) if chosen is not None else set(range(1,total+1))
        invalid=requested-set(range(1,total+1))
        if invalid or not requested:
            raise ValueError(f'expected physical pages 1..{total}; invalid selection: {sorted(invalid)}')
        for p in doc.pages:
            if p.page_number not in requested:
                p.close()
                continue
            blocks=[]
            for o in p.objects.get('textboxhorizontal',[]):
                t=(o.get('text') or '').rstrip('\n')
                if t.strip():
                    b=box(o)
                    blocks.append({'text':t,'bbox':b,'kind':kind(t),'method':'pdfminer_textbox'})
            if not blocks and p.chars:
                for l in p.extract_text_lines():
                    blocks.append({'text':l['text'],'bbox':box(l),'kind':kind(l['text']),'method':'pdfplumber_line'})
            # IDs follow geometry, independently of selection-dependent margin votes and reading order.
            for i,b in enumerate(sorted(blocks,key=lambda b:(b['bbox'][1],b['bbox'][0],*b['bbox'][2:],b['text'])),1):
                b['id']=f"P{p.page_number:04d}-B{i:03d}"
                if b['kind']=='heading_candidate':
                    lv=heading_level(b['text'])
                    if lv: b['heading_level']=lv
                role='top' if b['bbox'][3]<p.height*.09 else 'bottom' if b['bbox'][1]>p.height*.91 else None
                if role and len(b['text'])<135:
                    b['_margin']=(role,re.sub(r'\d+', '#',' '.join(b['text'].casefold().split())))
            counts.update({b['_margin'] for b in blocks if '_margin'in b})
            tt=[]
            errors=[]
            if with_tables:
                try:
                    for t in p.find_tables():
                        tt.append({'bbox':list(t.bbox),'cell_bboxes':[list(c) for c in t.cells],
                                   'rows':t.extract(),'method':'pdfplumber_lines'})
                except (ValueError,TypeError) as e:
                    errors.append('table pass: '+str(e))
            pages.append({'pdf_page':p.page_number,'page_label':getattr(p,'page_label',None),
                          'width':p.width,'height':p.height,'blocks':blocks,'tables':tt,'warnings':errors})
            p.close()
    for p in pages:
        body,margin=[],[]
        for b in p['blocks']:
            k=b.pop('_margin',None)
            (margin if k and counts[k]>=2 else body).append(b)
        arranged,gutter=order(body,p['width'])
        p['blocks']=arranged+margin
        p['gutter_pt']=gutter
        letters=sum(c.isalnum() for b in body for c in b['text'])
        separated=any(a['bbox'][2]+20<b['bbox'][0] and
                      min(a['bbox'][3],b['bbox'][3])>max(a['bbox'][1],b['bbox'][1])
                      for a in body for b in body)
        ambiguous=separated and gutter is None
        p['needs_visual_review']=ambiguous or letters<25 or any('\ufffd' in b['text'] or '(cid:' in b['text'] for b in body)
        if ambiguous:
            p['warnings'].append('possible columns or marginal notes: ordering needs visual confirmation')
        if p['needs_visual_review']:
            p['warnings'].append('inspect rendered page; OCR only if image text is actually present')
        for i,b in enumerate(p['blocks'],1):
            b['reading_order']=i
            if i>len(arranged):
                b['original_kind']=b['kind']
                b['kind']='repeated_margin_candidate'
        for i,t in enumerate(p['tables'],1):
            t['id']=f"P{p['pdf_page']:04d}-T{i:03d}"
    return {'source':path.name,'source_sha256':file_sha256(path),'page_count':total,
            'extracted_pages':sorted(requested),'bbox_convention':'x0, top, x1, bottom (PDF pt, top-left)',
            'backend':f'pdfplumber {pdfplumber.__version__}','pages':pages}

# Reference model: PyPI docling-core 2.107.1; never import its runtime here.
DOCLING_SCHEMA = '1.11.0'


def _docling_graph(doc):
    nodes = {}
    for root in ('body', 'furniture'):
        if root in doc:
            nodes[f'#/{root}'] = doc[root]
    if '#/body' not in nodes:
        raise ValueError('DoclingDocument.body is required')
    for collection in ('groups', 'texts', 'tables', 'pictures'):
        values = doc.get(collection, [])
        if not isinstance(values, list):
            raise ValueError(f'{collection}: expected an array')
        nodes.update((f'#/{collection}/{i}', value) for i, value in enumerate(values))
    for collection in ('key_value_items', 'form_items', 'field_regions', 'field_items'):
        if doc.get(collection):
            raise ValueError(f'unsupported Docling collection: {collection}')
    for ref, node in nodes.items():
        if not isinstance(node, dict) or node.get('self_ref') != ref:
            raise ValueError(f'{ref}: self_ref must match its array/root position')

    def resolve(value):
        ref = value.get('$ref') if isinstance(value, dict) else None
        if not isinstance(ref, str) or not ref.startswith('#/'):
            raise ValueError(f'external or malformed $ref: {str(ref)[:120]}')
        if ref not in nodes:
            raise ValueError(f'unknown $ref: {ref[:120]}')
        return ref

    pending = [doc]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if '$ref' in value:
                resolve(value)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    children, parents = {}, {}
    for ref, node in nodes.items():
        if not isinstance(node.get('children', []), list):
            raise ValueError(f'{ref}: children must be an array')
        children[ref] = [resolve(c) for c in node.get('children', [])]
        parents[ref] = [resolve(node['parent'])] if node.get('parent') is not None else []
        for key in ('captions', 'references', 'footnotes'):
            if not isinstance(node.get(key, []), list):
                raise ValueError(f'{ref}: {key} must be an array')
            for value in node.get(key, []):
                target = resolve(value)
                if key == 'captions' and not target.startswith('#/texts/'):
                    raise ValueError(f'{ref}: caption must reference a text item')
    for edges in (children, parents):
        states = {}
        for ref in nodes:
            stack = [(ref, False)]
            while stack:
                current, leaving = stack.pop()
                if leaving:
                    states[current] = 2
                elif states.get(current) == 1:
                    raise ValueError(f'cyclic $ref: {current}')
                elif current not in states:
                    states[current] = 1
                    stack.append((current, True))
                    stack.extend((c, False) for c in reversed(edges[current]))
    order, seen = [], set()
    stack = list(reversed(children['#/body']))
    while stack:
        ref = stack.pop()
        if ref in seen:
            raise ValueError(f'repeated child in body tree: {ref}')
        seen.add(ref)
        order.append(ref)
        stack.extend(reversed(children[ref]))
    return nodes, order


def _docling_number(value, context):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{context}: expected a finite number')
    return value


def _docling_bbox(value, page, context):
    if not isinstance(value, dict):
        raise ValueError(f'{context}: bbox must be an object')
    origin = value.get('coord_origin', 'TOPLEFT')
    if origin not in ('TOPLEFT', 'BOTTOMLEFT'):
        raise ValueError(f'{context}: unsupported coord_origin {str(origin)[:60]}')
    l, t, r, b = [_docling_number(value.get(k), f'{context}.{k}') for k in ('l', 't', 'r', 'b')]
    if l < 0 or min(t, b) < 0 or l >= r or (t >= b if origin == 'TOPLEFT' else t <= b):
        raise ValueError(f'{context}: invalid or empty bbox')
    if page is None:
        return None
    if origin == 'BOTTOMLEFT':
        t, b = page['height'] - t, page['height'] - b
    if not (0 <= l < r <= page['width'] and 0 <= t < b <= page['height']):
        raise ValueError(f'{context}: bbox outside page bounds')
    return [l, t, r, b]


def _docling_table(node, item, pages):
    data = node.get('data')
    ref = item['source_ref']
    if not isinstance(data, dict) or not isinstance(data.get('table_cells'), list):
        raise ValueError(f'{ref}: data.table_cells must be an array')
    nr, nc = data.get('num_rows'), data.get('num_cols')
    if any(type(n) is not int or n < 0 for n in (nr, nc)):
        raise ValueError(f'{ref}: invalid table dimensions')
    if max(nr, nc, nr * nc) > 1_000_000:
        raise ValueError(f'{ref}: table exceeds 1000000 grid slots')
    rows = [[None] * nc for _ in range(nr)]
    occupied, cells = set(), []
    page_numbers = {p.get('page_no') for p in item['provenance']}
    page_no = next(iter(page_numbers)) if len(page_numbers) == 1 else None
    page = pages.get(page_no)
    missing_boxes = 0
    for i, cell in enumerate(data['table_cells']):
        context = f'{ref}/data/table_cells/{i}'
        if not isinstance(cell, dict) or not isinstance(cell.get('text'), str):
            raise ValueError(f'{context}: expected a cell with text')
        a, b, c, d = [cell.get(k) for k in ('start_row_offset_idx', 'end_row_offset_idx',
                                           'start_col_offset_idx', 'end_col_offset_idx')]
        if any(type(v) is not int for v in (a, b, c, d)) or not (0 <= a < b <= nr and 0 <= c < d <= nc):
            raise ValueError(f'{context}: invalid row/column offsets')
        for key, expected in (('row_span', b - a), ('col_span', d - c)):
            if key in cell and (type(cell[key]) is not int or cell[key] != expected):
                raise ValueError(f'{context}: {key} disagrees with offsets')
        for key in ('column_header', 'row_header', 'row_section', 'fillable'):
            if key in cell and type(cell[key]) is not bool:
                raise ValueError(f'{context}: {key} must be boolean')
        for row in range(a, b):
            for col in range(c, d):
                if (row, col) in occupied:
                    raise ValueError(f'{context}: overlapping table cells')
                occupied.add((row, col))
        rows[a][c] = cell['text']
        bbox = _docling_bbox(cell['bbox'], page, context) if cell.get('bbox') is not None else None
        missing_boxes += bbox is None
        cells.append({**cell, 'source_bbox': cell.get('bbox'), 'bbox': bbox,
                      'pdf_page': page_no if bbox is not None else None,
                      'mapping_status': 'mapped' if bbox is not None else 'unmapped'})
    item.update(num_rows=nr, num_cols=nc, table_cells=cells, rows=rows,
                orientation=data.get('orientation', 'rot_0'),
                rows_convention='anchor text only; covered/absent slots are null; use table_cells for spans')
    if missing_boxes:
        item['warnings'].append(f'{missing_boxes} table cells lack a unique page/bbox; no cell placement inferred')
    if len(item['provenance']) > 1:
        item['warnings'].append('table content is document-level; not assigned to individual provenance fragments')


def _docling_text_spans(item):
    orig = item['orig']
    spans = {}
    for i, prov in enumerate(item['provenance']):
        span = prov.get('charspan')
        if (isinstance(span, list) and len(span) == 2 and all(type(n) is int for n in span)
                and 0 <= span[0] < span[1] <= len(orig)):
            spans[i] = span
        else:
            item['warnings'].append(f'provenance {i}: missing/invalid charspan for orig; text not placed')
    conflicts, furthest, owner = set(), -1, None
    for i in sorted(spans, key=lambda i: spans[i]):
        start, end = spans[i]
        if start < furthest:
            conflicts.update((owner, i))
        if end > furthest:
            furthest, owner = end, i
    if conflicts:
        item['warnings'].append('overlapping charspans; ambiguous text fragments not placed')
    return {i: span for i, span in spans.items() if i not in conflicts}


def import_docling(path, chosen=None):
    """Keep full items and body order; page fragments slice orig, never guess ambiguous spans."""
    path = Path(path)
    raw = path.read_bytes()
    doc = json.loads(raw.decode('utf-8-sig'), parse_constant=reject_number,
                     parse_float=finite_float, object_pairs_hook=unique_object)
    if not isinstance(doc, dict) or doc.get('schema_name') != 'DoclingDocument':
        raise ValueError('expected a DoclingDocument JSON export')
    version = doc.get('version')
    if not isinstance(version, str) or not re.fullmatch(r'1\.\d+\.\d+', version):
        raise ValueError('unsupported DoclingDocument schema version')
    nodes, reading_order = _docling_graph(doc)
    if not isinstance(doc.get('pages', {}), dict):
        raise ValueError('pages must be a page-number dictionary')
    pages = {}
    for key, value in doc.get('pages', {}).items():
        if not isinstance(value, dict) or not isinstance(value.get('size'), dict):
            raise ValueError(f'pages/{key}: missing page size')
        number = value.get('page_no')
        if type(number) is not int or number < 1 or key != str(number):
            raise ValueError(f'pages/{key}: invalid or inconsistent page_no')
        width, height = [_docling_number(value['size'].get(k), f'pages/{key}/{k}') for k in ('width', 'height')]
        if min(width, height) <= 0:
            raise ValueError(f'pages/{key}: page size must be positive')
        pages[number] = {'pdf_page': number, 'width': width, 'height': height,
                         'blocks': [], 'tables': [], 'reading_order': [], 'warnings': [],
                         'needs_visual_review': False}
    selected = set(chosen) if chosen is not None else set(pages)
    if chosen is not None and not selected:
        raise ValueError('select at least one Docling page')
    if any(type(n) is not int or n not in pages for n in selected):
        raise ValueError('selected page does not exist in DoclingDocument.pages')
    warnings, items, unmapped = [], [], []
    if version != DOCLING_SCHEMA:
        warnings.append(f'schema {version} not reference-tested; tested {DOCLING_SCHEMA}')
    if doc.get('origin') is not None:
        if not isinstance(doc['origin'], dict):
            raise ValueError('origin must be an object or null')
        warnings.append('origin is unverified exporter metadata; binary_hash is not input_sha256')
    positions = {ref: i for i, ref in enumerate(reading_order)}
    caption_of = {}
    for ref, node in nodes.items():
        for caption in node.get('captions', []):
            caption_of.setdefault(caption['$ref'], []).append(ref)

    def unplace(item, reason, span=None):
        entry = {'source_ref': item['source_ref'], 'reason': reason, 'bbox': None,
                 'pdf_page': None, 'text': item.get('text', '')}
        if span is not None:
            entry.update(charspan=span, text=item['orig'][span[0]:span[1]], text_basis='orig')
        unmapped.append(entry)
        item['warnings'].append(reason)

    refs = reading_order + [r for r in nodes if r not in positions and r not in ('#/body', '#/furniture')]
    for ref in refs:
        node = nodes[ref]
        is_group, is_text = ref.startswith('#/groups/'), ref.startswith('#/texts/')
        item = {k: node[k] for k in ('text', 'orig', 'label', 'name', 'content_layer', 'marker',
                                    'enumerated', 'formatting', 'annotations', 'meta') if k in node}
        item.update(source_ref=ref, kind=node.get('label', 'group' if is_group else 'unknown'),
                    body_order=positions.get(ref), children=[c['$ref'] for c in node.get('children', [])],
                    parent_ref=(node.get('parent') or {}).get('$ref'),
                    provenance=node.get('prov', []), locations=[], warnings=[],
                    method='docling_json', bbox=None, pdf_page=None)
        if not is_text:
            item.setdefault('text', '')
        for key, target in (('captions', 'caption_refs'), ('references', 'reference_refs'), ('footnotes', 'footnote_refs')):
            if key in node:
                item[target] = [c['$ref'] for c in node[key]]
        if ref in caption_of:
            item['caption_of'] = caption_of[ref]
        if 'level' in node:
            if type(node['level']) is not int or not 1 <= node['level'] <= 100:
                raise ValueError(f'{ref}: invalid heading level')
            item['heading_level'] = node['level']
        if not isinstance(item['provenance'], list) or any(not isinstance(p, dict) for p in item['provenance']):
            raise ValueError(f'{ref}: prov must be an array of objects')
        if is_text and any(not isinstance(item.get(k), str) for k in ('text', 'orig')):
            raise ValueError(f'{ref}: text and orig must be strings')
        if item['body_order'] is None:
            item['warnings'].append('not reachable from body; reading order unknown')
        if ref.startswith('#/pictures/'):
            item['image_omitted'] = node.get('image') is not None
            item['warnings'].append('picture pixels not inspected; caption references only')
        for i, prov in enumerate(item['provenance']):
            number = prov.get('page_no')
            if type(number) is not int or number < 1:
                raise ValueError(f'{ref}/prov/{i}: invalid page_no')
            page = pages.get(number)
            bbox = _docling_bbox(prov['bbox'], page, f'{ref}/prov/{i}') if prov.get('bbox') is not None else None
            location = {'pdf_page': number, 'bbox': bbox, 'charspan': prov.get('charspan'),
                        'provenance_index': i, 'mapping_status': 'mapped' if bbox is not None else 'unmapped'}
            if page is None:
                location['reason'] = f'unknown page {number}; no page or coordinates synthesized'
            elif bbox is None:
                location['reason'] = f'provenance {i}: missing bbox'
            if 'reason' in location:
                unplace(item, location['reason'])
            item['locations'].append(location)
        if not is_group and not item['provenance']:
            unplace(item, 'missing provenance; content retained without page/bbox')
        if ref.startswith('#/tables/'):
            _docling_table(node, item, pages)
        spans = _docling_text_spans(item) if is_text else {}
        covered, projected = [], []
        for loc in item['locations']:
            if loc['bbox'] is None:
                continue
            number, index = loc['pdf_page'], loc['provenance_index']
            fragment = dict(item, **loc)
            if is_text:
                span = spans.get(index)
                fragment['text'] = ''
                fragment['orig'] = ''
                fragment['text_basis'] = 'orig'
                if span is not None:
                    covered.append(span)
                    fragment['text'] = fragment['orig'] = item['orig'][span[0]:span[1]]
                    if len(item['provenance']) == 1 and span == [0, len(item['orig'])]:
                        fragment.update(text=item['text'], text_basis='text')
                else:
                    fragment['mapping_status'] = 'location_only'
            elif ref.startswith('#/tables/') and len(item['provenance']) > 1:
                fragment.update(rows=[], table_cells=[], content_ref=ref, mapping_status='location_only')
            if number not in selected or is_group:
                continue
            page = pages[number]
            bucket, prefix = ('tables', 'T') if ref.startswith('#/tables/') else ('blocks', 'B')
            fragment['id'] = f"P{number:04d}-{prefix}{len(page[bucket]) + 1:03d}"
            page[bucket].append(fragment)
            projected.append(fragment)
            page['reading_order'].append(fragment['id'])
        if is_text and item['provenance']:
            if not item['orig'] and item['text']:
                unplace(item, 'empty orig; cleaned text cannot be localized')
            cursor = 0
            for start, end in sorted(covered):
                if start > cursor:
                    unplace(item, 'orig charspan not reliably localized', [cursor, start])
                cursor = max(cursor, end)
            if cursor < len(item['orig']):
                unplace(item, 'orig charspan not reliably localized', [cursor, len(item['orig'])])
        item['needs_visual_review'] = bool(item['warnings'])
        for fragment in projected:
            fragment['needs_visual_review'] = item['needs_visual_review']
        for number in {loc['pdf_page'] for loc in item['locations']} & selected:
            page = pages[number]
            page['warnings'].extend(f'{ref}: {w}' for w in item['warnings'])
            page['needs_visual_review'] |= item['needs_visual_review']
        warnings.extend(f'{ref}: {w}' for w in item['warnings'])
        items.append(item)
    return {'source': path.name, 'input_sha256': hashlib.sha256(raw).hexdigest(),
            'backend': 'docling-json (stdlib)', 'docling_schema_version': version,
            'page_count': len(pages), 'extracted_pages': sorted(selected),
            'bbox_convention': 'x0, top, x1, bottom (source page units, top-left; PDF exports use pt)',
            'origin': doc.get('origin'), 'origin_verified': False,
            'body': doc['body'], 'reading_order': reading_order, 'items': items,
            'unmapped_items': unmapped, 'pages': [pages[n] for n in sorted(selected)],
            'warnings': warnings, 'needs_visual_review': bool(warnings),
            'media_policy': 'No images decoded; no URLs or referenced local files opened.'}


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    try:
        p = _JsonArgumentParser(description=__doc__)
        p.add_argument('pdf', type=Path); p.add_argument('output', type=Path)
        p.add_argument('--pages', type=int, nargs='+'); p.add_argument('--tables', action='store_true')
        p.add_argument('--format', choices=('pdf', 'docling'), default='pdf')
        a = p.parse_args(argv)
        data = import_docling(a.pdf, a.pages) if a.format == 'docling' else extract(a.pdf, a.pages, a.tables)
        save(a.output, data)
        return 0
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__, 'detail': str(exc)[:500]}, ensure_ascii=True), file=sys.stderr)
        return 2

if __name__=='__main__':
    sys.exit(main())
