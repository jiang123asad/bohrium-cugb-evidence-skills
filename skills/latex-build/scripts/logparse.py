#!/usr/bin/env python3
"""Turn a real LaTeX build log into a prioritised repair queue.

The parser keeps two diagnostic channels apart, mirroring the mechanism of the
``latexlog`` parser in James-Yu/LaTeX-Workshop (MIT), reimplemented here from
its documented behaviour; no upstream code is vendored:

* ``file:line: message`` lines emitted under ``-file-line-error``;
* classic ``! ...`` error blocks whose location arrives later on an ``l.NN`` line.

``Overfull``/``Underfull`` box reports form a third, non-fatal class and keep
their ``at lines A--B`` ranges.  Source context is taken from the
``(./path/file.tex`` openings TeX prints while reading input, so every record
can be reported as ``file:line``.  Python 3.10+, standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SEVERITY_ORDER = ('error', 'warning', 'box')
SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITY_ORDER)}

KIND_LABEL = {
    'undefined_control_sequence': 'undefined control sequence',
    'latex_error': 'LaTeX error',
    'package_error': 'package error',
    'tex_error': 'TeX error',
    'missing_file': 'missing file',
    'undefined_reference': 'undefined reference',
    'undefined_citation': 'undefined citation',
    'rerun_required': 'rerun request',
    'latex_warning': 'LaTeX warning',
    'package_warning': 'package warning',
    'overfull_hbox': 'overfull hbox',
    'underfull_vbox': 'underfull vbox',
}

# ./main.tex:17: Undefined control sequence.  (also C:\build\main.tex:17: ...)
FILE_LINE_RE = re.compile(
    r'^(?P<file>(?:[A-Za-z]:)?[^:\s<>"]+\.(?:tex|ltx|sty|cls|def|clo|fd))'
    r':(?P<line>\d+):[ \t]?(?P<message>.*)$'
)

# An opening parenthesis followed by something that looks like a TeX input file.
FILE_OPEN_RE = re.compile(
    r'\("?(?P<path>(?:[A-Za-z]:)?[^()\s"]*\.'
    r'(?:tex|ltx|sty|cls|def|clo|fd|cfg|bbl|aux|toc|out|ldf|bib))"?'
)

L_LINE_RE = re.compile(r'^l\.(?P<line>\d+)(?:\s+(?P<source>.*))?$')

WARNING_RE = re.compile(
    r'^(?P<prefix>(?:LaTeX|Package\s+\S+|Class\s+\S+)\s+Warning:)'
    r'[ \t]*(?P<message>.*)$'
)

BOX_RE = re.compile(r'^(?P<kind>Overfull|Underfull)\s+\\(?P<box>[hv])box\b(?P<rest>.*)$')
BOX_RANGE_RE = re.compile(
    r'\bat\s+lines?\s+(?P<start>\d+)(?:\s*--\s*(?P<end>\d+))?'
)

NO_FILE_RE = re.compile(r'^No file\s+(?P<name>[^\s.]+(?:\.[A-Za-z0-9]+)?)\.?$')

INPUT_LINE_RE = re.compile(r'\bon input line\s+(\d+)')

# Lines TeX puts between an error and its l.NN marker; keep them out of context.
CONTEXT_SKIP_RE = re.compile(
    r'^(?:See the LaTeX manual|Type\s+H\s+<return>|Type\s+X\s+to quit|'
    r'Enter file name:|\.\.\.|<recently read>|<argument>)'
)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _normalize_file(name: str | None, root: Path | None) -> str | None:
    """Return a stable source path, relative to --source-root when possible."""
    if not name:
        return None
    path = name.strip().strip('"').replace('\\', '/')
    path = re.sub(r'^\./+', '', path)
    if not path or path in ('.', '..'):
        return None
    if root is not None:
        raw = Path(path)
        candidates = (raw,) if raw.is_absolute() else (root / raw,)
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if resolved.is_file() and resolved.is_relative_to(root):
                    return resolved.relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
    return path


def _context_file(stack: list[str]) -> str | None:
    for name in reversed(stack):
        if name.lower().endswith(('.tex', '.ltx')):
            return name
    return stack[-1] if stack else None


def _scan_file_stack(line: str, stack: list[str]) -> None:
    """Track ``(file`` openings and ``)`` closings on ordinary log lines.

    Parentheses that belong to an inline annotation such as ``(Font)`` or
    ``(see above)`` are balanced inside the line and never pop a file.
    """
    index = 0
    local_depth = 0
    while index < len(line):
        char = line[index]
        if char == '(':
            match = FILE_OPEN_RE.match(line, index)
            if match:
                if len(stack) < 64:
                    stack.append(match.group('path'))
                index = match.end()
                continue
            local_depth += 1
        elif char == ')':
            if local_depth:
                local_depth -= 1
            elif stack:
                stack.pop()
        index += 1


def _append_context(diagnostic: dict, text: str, limit: int = 6) -> None:
    text = text.strip()
    if not text or CONTEXT_SKIP_RE.match(text):
        return
    if text[0] in '()':            # file stack bookkeeping, not error context
        return
    parts = diagnostic['context'].splitlines() if diagnostic['context'] else []
    if len(parts) >= limit:
        return
    parts.append(text)
    diagnostic['context'] = _clip('\n'.join(parts), 1200)


def _new_diagnostic(severity: str, kind: str, message: str, file: str | None,
                    line: int | None, order: int, context: str = '',
                    line_end: int | None = None) -> dict:
    return {
        'severity': severity,
        'kind': kind,
        'message': _clip(message, 500),
        'file': file,
        'line': line,
        'line_end': line_end,
        'context': _clip(context, 1200),
        '_order': order,
    }


def _bang_kind(body: str) -> str:
    if re.search(r"File\s+`[^']*'\s+not found", body):
        return 'missing_file'
    if re.search(r"I can't find file", body):
        return 'missing_file'
    if body.startswith('LaTeX Error:'):
        return 'latex_error'
    if body.startswith('Undefined control sequence'):
        return 'undefined_control_sequence'
    if re.match(r'Package\s+\S+\s+Error:', body):
        return 'package_error'
    return 'tex_error'


def _warning_kind(message: str, prefix: str) -> str:
    lowered = message.lower()
    if 'undefined references' in lowered:
        return 'undefined_reference'
    if 'undefined citations' in lowered:
        return 'undefined_citation'
    if re.search(r'\bReference\s+`[^\']*\'', message, re.I) and 'undefined' in lowered:
        return 'undefined_reference'
    if re.search(r'\bCitation\s+`[^\']*\'', message, re.I) and 'undefined' in lowered:
        return 'undefined_citation'
    if re.search(r"\bFile\s+`[^']*'[^.]*not found", message, re.I):
        return 'missing_file'
    if 'rerun' in lowered and ('label' in lowered or 'citation' in lowered
                               or 'cross-reference' in lowered):
        return 'rerun_required'
    return 'latex_warning' if prefix.startswith('LaTeX') else 'package_warning'


def _box_range(rest: str) -> tuple[int | None, int | None]:
    match = BOX_RANGE_RE.search(rest)
    if not match:
        return None, None
    start = int(match.group('start'))
    end = int(match.group('end')) if match.group('end') else None
    return start, end


def _location(diagnostic: dict) -> str:
    file = diagnostic['file'] or '<main log>'
    if diagnostic['line'] is None:
        return file
    return f"{file}:{diagnostic['line']}"


def _next_action(diagnostics: list[dict], summary: dict[str, int]) -> str:
    for severity, verb in (('error', 'before rebuilding'),
                           ('warning', 'before the next build')):
        group = [d for d in diagnostics if d['severity'] == severity]
        if not group:
            continue
        first = group[0]
        label = KIND_LABEL.get(first['kind'], first['kind'].replace('_', ' '))
        if len(group) == 1:
            hint = f"fix {label} at {_location(first)} {verb}"
        else:
            hint = (f"fix {len(group)} {severity}s, starting with {label} at "
                    f"{_location(first)}, {verb}")
        if severity == 'warning' and first['kind'] in (
                'undefined_reference', 'undefined_citation', 'rerun_required'):
            hint += '; rerun LaTeX until references converge'
        return hint
    if summary['box']:
        return (f'build is error-free; {summary["box"]} box warning(s) remain, '
                'review layout after the content is final')
    return ('no diagnostics found in log; nothing to fix - confirm the log '
            'belongs to the newest build round')


def _sort_key(diagnostic: dict) -> tuple:
    return (
        SEVERITY_RANK[diagnostic['severity']],
        diagnostic['file'] is None,
        diagnostic['file'] or '',
        diagnostic['line'] is None,
        diagnostic['line'] if diagnostic['line'] is not None else 0,
        diagnostic['_order'],
    )


def parse_text(text: str, source_root: str | Path | None = None) -> dict:
    """Parse log text and return the JSON-ready report."""
    root = Path(source_root).resolve() if source_root is not None else None
    stack: list[str] = []
    diagnostics: list[dict] = []
    pending: dict | None = None          # last diagnostic, for continuation lines
    line_queue: list[dict] = []          # errors still waiting for an l.NN marker

    for order, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip('\r\n')
        stripped = line.strip()
        if not stripped:
            pending = None
            continue

        marker = L_LINE_RE.match(stripped)
        if marker:
            waiting = [d for d in line_queue
                       if d['line'] is None and order - d['_order'] <= 20]
            if waiting:
                target = waiting[0]
                target['line'] = int(marker.group('line'))
                if pending is target:
                    _append_context(target, stripped)
                line_queue.remove(target)
                pending = None
                continue

        file_line = FILE_LINE_RE.match(stripped)
        if file_line:
            message = file_line.group('message').strip()
            line_no = int(file_line.group('line'))
            file_name = _normalize_file(file_line.group('file'), root)
            warning = WARNING_RE.match(message)
            if warning:
                diagnostic = _new_diagnostic(
                    'warning', _warning_kind(warning.group('message'), warning.group('prefix')),
                    message, file_name, line_no, order, stripped)
            else:
                diagnostic = _new_diagnostic(
                    'error', _bang_kind(message), message, file_name, line_no,
                    order, stripped)
            diagnostics.append(diagnostic)
            pending = None if warning else diagnostic
            continue

        if stripped.startswith('!'):
            body = stripped[1:].strip()
            line_match = INPUT_LINE_RE.search(body)
            diagnostic = _new_diagnostic(
                'error', _bang_kind(body), body,
                _normalize_file(_context_file(stack), root),
                int(line_match.group(1)) if line_match else None,
                order, stripped)
            diagnostics.append(diagnostic)
            pending = None if diagnostic['severity'] == 'warning' else diagnostic
            if diagnostic['line'] is None:
                line_queue.append(diagnostic)
            continue

        box = BOX_RE.match(stripped)
        if box:
            start, end = _box_range(box.group('rest'))
            box_kind = f"{box.group('kind').lower()}_{'hbox' if box.group('box') == 'h' else 'vbox'}"
            diagnostic = _new_diagnostic(
                'box', box_kind, stripped,
                _normalize_file(_context_file(stack), root), start, order, stripped, end)
            diagnostics.append(diagnostic)
            pending = None
            continue

        warning = WARNING_RE.match(stripped)
        if warning:
            message = warning.group('message').strip()
            line_match = INPUT_LINE_RE.search(message)
            diagnostic = _new_diagnostic(
                'warning', _warning_kind(message, warning.group('prefix')),
                f"{warning.group('prefix')} {message}".strip(),
                _normalize_file(_context_file(stack), root),
                int(line_match.group(1)) if line_match else None,
                order, stripped)
            diagnostics.append(diagnostic)
            pending = None
            continue

        no_file = NO_FILE_RE.match(stripped)
        if no_file:
            diagnostic = _new_diagnostic(
                'warning', 'missing_file', stripped,
                _normalize_file(_context_file(stack), root), None, order, stripped)
            diagnostics.append(diagnostic)
            pending = None
            continue

        if pending is not None:
            _append_context(pending, stripped)

        _scan_file_stack(line, stack)

    diagnostics.sort(key=_sort_key)
    for diagnostic in diagnostics:
        diagnostic.pop('_order', None)

    summary = {name: sum(1 for d in diagnostics if d['severity'] == name)
               for name in SEVERITY_ORDER}
    return {
        'status': 'clean' if not diagnostics else 'needs_fixes',
        'diagnostics': diagnostics,
        'summary': summary,
        'next_action': _next_action(diagnostics, summary),
    }


def parse_file(log_path: str | Path, source_root: str | Path | None = None) -> dict:
    """Read ``log_path`` (CRLF/BOM tolerant) and parse it."""
    path = Path(log_path)
    if not path.is_file():
        raise ValueError(f'log file not found: {path}')
    if source_root is not None and not Path(source_root).is_dir():
        raise ValueError(f'source root is not a directory: {source_root}')
    text = path.read_bytes().decode('utf-8-sig', 'replace')
    return parse_text(text, source_root)


def save_report(path: str | Path, report: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                   encoding='utf-8')
    tmp.replace(path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Turn a LaTeX log into a prioritised JSON repair queue.')
    parser.add_argument('log', type=Path, help='the .log file of the latest build round')
    parser.add_argument('out', type=Path, help='destination JSON report')
    parser.add_argument('--source-root', type=Path, default=None,
                        help='project root used to resolve log paths to source files')
    args = parser.parse_args(argv)

    report = parse_file(args.log, args.source_root)
    save_report(args.out, report)

    summary = report['summary']
    print(f"logparse: {summary['error']} error(s), {summary['warning']} warning(s), "
          f"{summary['box']} box warning(s) -> {args.out}")
    if report['status'] == 'clean':
        print('logparse: no diagnostics found in log')
    print(f"logparse: next_action: {report['next_action']}")
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__, 'detail': str(exc)},
                         ensure_ascii=False))
        sys.exit(2)
