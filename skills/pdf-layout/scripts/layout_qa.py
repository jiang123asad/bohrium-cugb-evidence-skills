"""Delivery-time layout integrity checks (standard library only, Python 3.10+).

Borrows the provenance discipline of Docling (MIT): every delivered element
should keep a page and a bbox, the reading order must be internally consistent,
and table grids must be complete. This reports structural defects only; it
cannot judge whether the extracted text or the reading order matches the PDF.
Container-only groups (label group/unspecified with no text) are structure, not
content, so they are not asked for provenance.

CLI:
    python scripts/layout_qa.py layout.json out.json [--pages N]

Exit codes: 0 = no fail, 1 = at least one fail, 2 = unreadable input.
"""
import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

CHECKS = ('missing_page', 'bbox_out_of_page', 'reading_order',
          'table_incomplete', 'duplicate_id', 'empty_text')

# Page/bbox/table defects block delivery; order and blank text are warnings
# because this package treats reading order as a candidate and some elements
# (e.g. pictures) are legitimately textless.
SEVERITY = {
    'missing_page': 'fail',
    'bbox_out_of_page': 'fail',
    'reading_order': 'warn',
    'table_incomplete': 'fail',
    'duplicate_id': 'fail',
    'empty_text': 'warn',
}

PASS_DETAIL = {
    'missing_page': '所有元素都有有效页码',
    'bbox_out_of_page': 'bbox 完整、未退化且位于页面内',
    'reading_order': '页内顺序值唯一且严格递增',
    'table_incomplete': '表格单元格与声明的行列数一致',
    'duplicate_id': '元素 id 唯一',
    'empty_text': '文本元素均有内容',
}

POLICY = '只报告结构缺陷；语义正确性与阅读顺序是否符合原文仍需回看原 PDF'

CONTAINER = {'group', 'groups', 'unspecified', 'body', 'furniture', 'root',
             '_root_', 'section'}
NON_TEXT = {'picture', 'pictures', 'figure', 'figures', 'image', 'images',
            'table', 'tables', 'chart', 'charts', 'drawing', 'drawings',
            'group', 'groups', 'unspecified', 'body', 'furniture', 'root',
            '_root_', 'section'}


def as_int(value):
    """Return an int for int-like JSON values, else None (bools are rejected)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else None
    if isinstance(value, str) and value.strip().lstrip('+-').isdigit():
        return int(value.strip())
    return None


def as_float(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def as_bbox(value, page_height=None):
    """Normalize list [x0, top, x1, bottom] or {l,t,r,b,x0,...} to a 4-list."""
    if isinstance(value, dict):
        if all(key in value for key in ('x0', 'top', 'x1', 'bottom')):
            values = [as_float(value[key]) for key in ('x0', 'top', 'x1', 'bottom')]
        elif all(key in value for key in ('l', 't', 'r', 'b')):
            values = [as_float(value[key]) for key in ('l', 't', 'r', 'b')]
            if any(item is None for item in values):
                return None
            origin = str(value.get('coord_origin') or 'TOPLEFT').upper()
            if origin == 'BOTTOMLEFT':
                if not page_height:
                    return None
                values = [values[0], page_height - values[1],
                          values[2], page_height - values[3]]
            elif origin != 'TOPLEFT':
                return None
        else:
            return None
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        values = [as_float(item) for item in value]
    else:
        return None
    if any(item is None for item in values):
        return None
    return [float(item) for item in values]


def page_size(sizes, page):
    size = sizes.get(page)
    if not size:
        return None
    width, height = size
    return (width, height) if width and height else None


def table_info(obj, kind):
    """Extract grid facts from a native pdfplumber table or a Docling table."""
    keys = ('table_cells', 'cells', 'cell_bboxes', 'num_rows', 'num_cols',
            'n_rows', 'n_cols', 'row_count', 'col_count')
    if kind not in ('table', 'table_data') and not any(key in obj for key in keys):
        return None

    cells = None
    for key in ('table_cells', 'cell_bboxes', 'cells'):
        value = obj.get(key)
        if isinstance(value, list):
            cells = value
            break
        count = as_int(value)
        if count is not None:
            cells = count
            break

    rows = obj.get('rows')
    rows = rows if isinstance(rows, list) else None

    num_rows = None
    for key in ('num_rows', 'n_rows', 'row_count'):
        num_rows = as_int(obj.get(key))
        if num_rows is not None:
            break
    num_cols = None
    for key in ('num_cols', 'n_cols', 'col_count'):
        num_cols = as_int(obj.get(key))
        if num_cols is not None:
            break

    row_lengths = [len(row) for row in rows if isinstance(row, list)] if rows else []
    if num_rows is None and rows is not None:
        num_rows = len(rows)
    if num_cols is None and row_lengths:
        num_cols = max(row_lengths) or None
    ragged = bool(row_lengths) and len(set(row_lengths)) > 1

    count = len(cells) if isinstance(cells, list) else cells
    covered = count
    if isinstance(cells, list) and cells and all(isinstance(cell, dict) for cell in cells):
        covered = sum((as_int(cell.get('row_span')) or 1) *
                      (as_int(cell.get('col_span')) or 1) for cell in cells)
    return {'rows': num_rows, 'cols': num_cols, 'cells': count,
            'covered': covered, 'ragged': ragged}


class Element:
    """One deliverable element with page/bbox evidence."""

    __slots__ = ('eid', 'kind', 'page', 'boxes', 'order', 'text', 'table',
                 'structural', 'note', 'label', 'body_order')

    def __init__(self, eid, kind, page, boxes, order, text, table,
                 structural=False, note='', label='', body_order=None):
        self.eid = eid
        self.kind = kind
        self.page = page
        self.boxes = boxes
        self.order = order
        self.text = text
        self.table = table
        self.structural = structural
        self.note = note
        self.label = label or eid or '（无 id 元素）'
        self.body_order = body_order


def make_element(obj, page, sizes, index=0, use_body_order=False):
    kind = ''
    for key in ('kind', 'label', 'type'):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            kind = value.strip().lower()
            break

    eid = None
    for key in ('id', 'element_id', 'source_ref'):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            eid = value.strip()
            break

    page_number = None
    for key in ('pdf_page', 'page', 'page_no', 'page_num'):
        page_number = as_int(obj.get(key))
        if page_number is not None:
            break
    if page_number is None:
        page_number = page

    boxes = []

    def add_box(candidate, box_page):
        box = as_bbox(candidate, (page_size(sizes, box_page) or (None, None))[1])
        if box is not None:
            key = (box_page, tuple(round(value, 4) for value in box))
            if key not in [(p, tuple(round(v, 4) for v in b)) for p, b in boxes]:
                boxes.append((box_page, box))

    if 'bbox' in obj and obj.get('bbox') is not None:
        add_box(obj.get('bbox'), page_number)
    for location in obj.get('locations') or []:
        if not isinstance(location, dict):
            continue
        box_page = None
        for key in ('pdf_page', 'page', 'page_no'):
            box_page = as_int(location.get(key))
            if box_page is not None:
                break
        if box_page is None:
            box_page = page_number
        if location.get('bbox') is not None:
            add_box(location.get('bbox'), box_page)
    for provenance in obj.get('provenance') or []:
        if not isinstance(provenance, dict):
            continue
        box_page = None
        for key in ('page_no', 'pdf_page', 'page'):
            box_page = as_int(provenance.get(key))
            if box_page is not None:
                break
        if box_page is None:
            box_page = page_number
        if provenance.get('bbox') is not None:
            add_box(provenance.get('bbox'), box_page)

    text = None
    for key in ('text', 'content'):
        value = obj.get(key)
        if isinstance(value, str):
            text = value
            break

    order = None
    order_keys = ('reading_order', 'order', 'seq')
    if use_body_order:
        order_keys += ('body_order',)
    for key in order_keys:
        if key in obj:
            order = as_int(obj.get(key))
            if order is not None:
                break

    table = table_info(obj, kind)
    structural = kind in CONTAINER and not (text or '').strip()
    if kind in NON_TEXT:
        text = None

    return Element(eid, kind, page_number, boxes, order, text, table,
                   structural=structural, label=(eid or f'item[{index}]'),
                   body_order=as_int(obj.get('body_order')))


def collect(doc):
    """Return (elements, page_order_lists, page_sizes) for supported shapes."""
    elements = []
    page_orders = []
    sizes = {}

    if isinstance(doc, list):
        for index, obj in enumerate(doc):
            if not isinstance(obj, dict):
                raise ValueError(f'元素 {index} 不是 JSON 对象')
            element = make_element(obj, None, sizes, index)
            if not element.structural:
                elements.append(element)
        return elements, page_orders, sizes

    if not isinstance(doc, dict):
        raise ValueError('顶层应为数组或包含 pages/items/elements 的对象')

    pages = doc.get('pages') if isinstance(doc.get('pages'), list) else []

    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        page_number = None
        for key in ('pdf_page', 'page', 'page_no'):
            page_number = as_int(page.get(key))
            if page_number is not None:
                break
        if page_number is None:
            page_number = index + 1
        sizes[page_number] = (as_float(page.get('width')), as_float(page.get('height')))

    if pages:
        seen_refs = set()
        for index, page in enumerate(pages):
            if not isinstance(page, dict):
                continue
            page_number = None
            for key in ('pdf_page', 'page', 'page_no'):
                page_number = as_int(page.get(key))
                if page_number is not None:
                    break
            if page_number is None:
                page_number = index + 1
            keys = ['blocks', 'tables']
            if not any(key in page for key in keys):
                keys = ['elements', 'items']
            for key in keys:
                for obj in page.get(key) or []:
                    if not isinstance(obj, dict):
                        continue
                    element = make_element(obj, page_number, sizes)
                    if element.eid:
                        seen_refs.add(element.eid)
                    if not element.structural:
                        elements.append(element)
            reading_order = page.get('reading_order')
            if isinstance(reading_order, list) and reading_order:
                ids = []
                for entry in reading_order:
                    if isinstance(entry, str):
                        ids.append(entry)
                    elif isinstance(entry, dict):
                        ref = entry.get('id') or entry.get('source_ref')
                        if isinstance(ref, str):
                            ids.append(ref)
                if ids:
                    page_orders.append((page_number, ids))

        # Docling items may hold content that no page carries (unmapped items).
        for index, item in enumerate(doc.get('items') or []):
            if not isinstance(item, dict):
                continue
            ref = item.get('source_ref') or item.get('id')
            if isinstance(ref, str) and ref in seen_refs:
                continue
            element = make_element(item, None, sizes, index)
            if element.structural:
                continue
            if element.page is None and not element.boxes:
                has_content = bool((element.text or '').strip()) or element.table is not None \
                    or element.kind in ('picture', 'figure', 'image')
                if not has_content:
                    continue
                element.note = '仅出现在 items，未被任何页承载'
                elements.append(element)
        return elements, page_orders, sizes

    source = None
    source_key = ''
    for key in ('items', 'elements'):
        if isinstance(doc.get(key), list):
            source = doc[key]
            source_key = key
            break
    if source is None:
        raise ValueError('缺少 pages / items / elements 之一')

    for index, obj in enumerate(source):
        if not isinstance(obj, dict):
            raise ValueError(f'元素 {index} 不是 JSON 对象')
        element = make_element(obj, None, sizes, index,
                               use_body_order=(source_key == 'items'))
        if not element.structural:
            elements.append(element)

    reading_order = doc.get('reading_order')
    if isinstance(reading_order, list) and reading_order:
        ids = [entry for entry in reading_order if isinstance(entry, str)]
        if ids:
            page_orders.append((None, ids))
    return elements, page_orders, sizes


def check_layout(data, page_limit=None):
    """Run all structural checks and return the delivery report dict."""
    elements, page_orders, sizes = collect(data)
    buckets = {kind: [] for kind in CHECKS}
    issues = []

    def add(kind, element, detail):
        issue = {'kind': kind, 'element': element.label, 'detail': detail}
        buckets[kind].append(issue)
        issues.append(issue)

    for element in elements:
        used_pages = [page for page, _ in element.boxes if page is not None]
        page = element.page if element.page is not None else (used_pages[0] if used_pages else None)
        problem = None
        if page is None:
            problem = '缺少页码（page/pdf_page）'
            if element.note:
                problem = element.note + '，缺少页码'
        elif page < 1:
            problem = f'页码 {page} < 1（物理页码从 1 起）'
        elif page_limit is not None and page > page_limit:
            problem = f'页码 {page} 超出 --pages {page_limit}'
        else:
            beyond = sorted({value for value in used_pages
                             if page_limit is not None and value > page_limit})
            if beyond:
                problem = f'定位页 {beyond} 超出 --pages {page_limit}'
        if problem:
            add('missing_page', element, problem)

    for element in elements:
        problems = []
        if not element.boxes:
            problems.append('bbox 缺失或无法解析')
        for page, box in element.boxes:
            plain = '[' + ', '.join(f'{value:g}' for value in box) + ']'
            if any(value < 0 for value in box):
                problems.append(f'page {page} bbox 含负坐标 {plain}')
                continue
            if box[2] <= box[0] or box[3] <= box[1]:
                problems.append(f'page {page} bbox 退化（x1<=x0 或 y1<=y0）{plain}')
                continue
            size = page_size(sizes, page)
            if size:
                width, height = size
                if box[0] < -0.5 or box[1] < -0.5 or box[2] > width + 0.5 or box[3] > height + 0.5:
                    problems.append(f'page {page} bbox 超出页面 {width:g}x{height:g} {plain}')
        if problems:
            add('bbox_out_of_page', element, '；'.join(problems[:2]))

    by_id = {}
    for element in elements:
        if element.eid and element.eid not in by_id:
            by_id[element.eid] = element

    for page, ids in page_orders:
        where = f'page {page}' if page is not None else '全文'
        duplicated = sorted(value for value, count in Counter(ids).items() if count > 1)
        if duplicated:
            add('reading_order', Element(None, '', page, [], None, None, None,
                                         label=where),
                f'reading_order 重复：{duplicated}')
        known = {element.eid for element in elements
                 if element.eid and element.page == page}
        unknown = [value for value in dict.fromkeys(ids) if known and value not in known]
        if unknown:
            add('reading_order', Element(None, '', page, [], None, None, None,
                                         label=where),
                f'reading_order 指向该页不存在的元素：{unknown[:3]}')
        sequence = []
        for value in dict.fromkeys(ids):
            element = by_id.get(value)
            order = None
            if element is not None:
                order = element.order if element.order is not None else element.body_order
            if order is not None:
                sequence.append((order, value))
        for (left, left_id), (right, right_id) in zip(sequence, sequence[1:]):
            if right < left:
                add('reading_order', Element(None, '', page, [], None, None, None,
                                             label=where),
                    f'reading_order 与元素顺序不一致：{left_id}({left}) 之后为 '
                    f'{right_id}({right})')
                break

    groups = {}
    for element in elements:
        if element.order is None:
            continue
        used_pages = [page for page, _ in element.boxes if page is not None]
        key = element.page if element.page is not None else (used_pages[0] if used_pages else None)
        groups.setdefault(key, []).append(element)
    for page, items in groups.items():
        where = f'page {page}' if page is not None else '页面未知'
        counts = Counter(item.order for item in items)
        for value in sorted(value for value, count in counts.items() if count > 1):
            owners = [item.label for item in items if item.order == value][:3]
            add('reading_order', Element(None, '', page, [], None, None, None, label=where),
                f'顺序值 {value} 重复：{owners}')
        previous = None
        for item in items:
            if previous is not None and item.order < previous.order:
                add('reading_order', item,
                    f'{where} 顺序未递增：{previous.order}（{previous.label}）之后为 '
                    f'{item.order}（{item.label}）')
            previous = item

    for element in elements:
        if element.table is None:
            continue
        info = element.table
        rows, cols = info['rows'], info['cols']
        cells, covered = info['cells'], info['covered']
        expected = rows * cols if rows and cols else None
        if cells == 0:
            add('table_incomplete', element, '表格没有单元格')
        elif info['ragged']:
            add('table_incomplete', element, '表格行长度不一致，网格不完整')
        elif expected is not None:
            if covered != expected:
                detail = f'单元格 {covered} != 行列 {rows}x{cols}={expected}'
                if cells is not None and cells != covered:
                    detail += f'（原始 cell 数 {cells}，按 span 覆盖 {covered}）'
                add('table_incomplete', element, detail)
        elif cells is None:
            add('table_incomplete', element, '未提供单元格或行列数，无法核对表格结构')

    ids = [element.eid for element in elements if element.eid]
    for eid in sorted(value for value, count in Counter(ids).items() if count > 1):
        owners = [element.label for element in elements if element.eid == eid]
        add('duplicate_id', Element(eid, '', None, [], None, None, None, label=eid),
            f'id 重复 {len(owners)} 次')

    for element in elements:
        if element.text is not None and not element.text.strip():
            detail = '文本元素内容为空'
            if element.note:
                detail += f'（{element.note}）'
            add('empty_text', element, detail)

    checks = []
    for kind in CHECKS:
        found = buckets[kind]
        status = 'pass'
        if found:
            status = 'fail' if SEVERITY[kind] == 'fail' else 'warn'
        if not found:
            detail = PASS_DETAIL[kind]
        else:
            examples = '；'.join(f"{issue['element']}: {issue['detail']}" for issue in found[:2])
            detail = f"{len(found)} 项"
            if len(found) > 2:
                detail += '（示例）'
            detail += '：' + examples
        checks.append({'id': kind, 'status': status, 'detail': detail})

    fail = sum(1 for issue in issues if SEVERITY[issue['kind']] == 'fail')
    warn = len(issues) - fail
    return {
        'checks': checks,
        'issues': issues,
        'summary': {'elements': len(elements), 'fail': fail, 'warn': warn},
        'policy': POLICY,
    }


def reject_number(value):
    raise ValueError(f'invalid JSON number: {value}')


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                   encoding='utf-8')
    tmp.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='交付前版面完整性检查：页码/bbox、阅读顺序、表格网格、id 与空文本')
    parser.add_argument('layout', help='layout.py 或 --format docling 产出的 JSON')
    parser.add_argument('out', help='检查报告 JSON 输出路径')
    parser.add_argument('--pages', type=int, default=None, help='本次交付覆盖的物理页数上限')
    args = parser.parse_args(argv)

    if args.pages is not None and args.pages < 1:
        print('--pages 必须 >= 1', file=sys.stderr)
        return 2
    try:
        path = Path(args.layout)
        data = json.loads(path.read_text(encoding='utf-8-sig'), parse_constant=reject_number)
        result = check_layout(data, args.pages)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(f'无法读取版面 JSON：{exc}', file=sys.stderr)
        return 2

    if result['summary']['elements'] == 0:
        print('警告：未找到可检查的版面元素', file=sys.stderr)
    try:
        save(Path(args.out), result)
    except OSError as exc:
        print(f'无法写入报告：{exc}', file=sys.stderr)
        return 2
    print(json.dumps(result['summary'], ensure_ascii=False))
    return 0 if result['summary']['fail'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
