#!/usr/bin/env python3
"""Export bib.json records to RIS or BibTeX.

Closes the loop with tagged.py: parse an export -> audit -> write it back out.
Standard library only; no CSL or Pandoc needed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RIS_TYPE = {
    "article": "JOUR", "book": "BOOK", "incollection": "CHAP",
    "inproceedings": "CONF", "conference": "CONF", "phdthesis": "THES",
    "mastersthesis": "THES", "techreport": "RPRT", "misc": "GEN",
    "online": "ELEC", "patent": "PAT",
}
BIB_TYPE = {
    "article": "article", "book": "book", "incollection": "incollection",
    "inproceedings": "inproceedings", "conference": "inproceedings",
    "phdthesis": "phdthesis", "mastersthesis": "mastersthesis",
    "techreport": "techreport", "misc": "misc", "online": "misc", "patent": "misc",
}


def field(rec: dict, name: str) -> str:
    f = rec.get("fields") or {}
    return str(f.get(name) or rec.get(name) or "").strip()


def authors(rec: dict) -> list[str]:
    persons = (rec.get("persons") or {}).get("author")
    if persons:
        return [str(p).strip() for p in persons if str(p).strip()]
    raw = field(rec, "author")
    return [a.strip() for a in re.split(r"\s+and\s+", raw) if a.strip()] if raw else []


def split_pages(pages: str) -> tuple[str, str]:
    m = re.split(r"\s*(?:--|-|–)\s*", pages.strip(), maxsplit=1)
    return (m[0], m[1]) if len(m) == 2 else (m[0] if m else "", "")


def to_ris(rec: dict) -> list[str]:
    t = RIS_TYPE.get((rec.get("type") or "misc").lower(), "GEN")
    lines = [f"TY  - {t}"]
    for a in authors(rec):
        lines.append(f"AU  - {a}")
    if field(rec, "title"):
        lines.append(f"TI  - {field(rec, 'title')}")
    if field(rec, "journal"):
        lines.append(f"JO  - {field(rec, 'journal')}")
    if field(rec, "booktitle"):
        lines.append(f"T2  - {field(rec, 'booktitle')}")
    if field(rec, "year"):
        lines.append(f"PY  - {field(rec, 'year')}")
    if field(rec, "volume"):
        lines.append(f"VL  - {field(rec, 'volume')}")
    if field(rec, "number"):
        lines.append(f"IS  - {field(rec, 'number')}")
    sp, ep = split_pages(field(rec, "pages"))
    if sp:
        lines.append(f"SP  - {sp}")
    if ep:
        lines.append(f"EP  - {ep}")
    if field(rec, "publisher"):
        lines.append(f"PB  - {field(rec, 'publisher')}")
    if field(rec, "doi"):
        lines.append(f"DO  - {field(rec, 'doi')}")
    if field(rec, "url"):
        lines.append(f"UR  - {field(rec, 'url')}")
    if field(rec, "abstract"):
        lines.append(f"AB  - {field(rec, 'abstract')}")
    lines.append("ER  - ")
    return lines


def to_bib(rec: dict) -> list[str]:
    t = BIB_TYPE.get((rec.get("type") or "misc").lower(), "misc")
    key = rec.get("id") or "ref"
    out = [f"@{t}{{{key},"]
    if authors(rec):
        out.append(f"  {'author':<8}= {{{' and '.join(authors(rec))}}},")
    for name, bib_name in (("title", "title"), ("journal", "journal"), ("booktitle", "booktitle"),
                           ("year", "year"), ("volume", "volume"), ("number", "number"),
                           ("publisher", "publisher"), ("doi", "doi"), ("url", "url")):
        val = field(rec, name)
        if val:
            out.append(f"  {bib_name:<8}= {{{val}}},")
    pages = field(rec, "pages")
    if pages:
        out.append(f"  {'pages':<8}= {{{pages.replace('-', '--')}}},")
    if out[-1].endswith(","):
        out[-1] = out[-1][:-1]
    out.append("}")
    return out


def load(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get("entries", [])
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bib", type=Path)
    ap.add_argument("--format", choices=["ris", "bib", "both"], default="both")
    ap.add_argument("--out", type=Path, required=True, help="output path; 'both' writes .ris and .bib")
    args = ap.parse_args()

    entries = load(args.bib)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    written = []
    if args.format in {"ris", "both"}:
        p = args.out.with_suffix(".ris") if args.format == "both" else args.out
        blocks = ["\n".join(to_ris(r)) for r in entries]
        p.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        written.append(str(p))
    if args.format in {"bib", "both"}:
        p = args.out.with_suffix(".bib") if args.format == "both" else args.out
        blocks = ["\n".join(to_bib(r)) for r in entries]
        p.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        written.append(str(p))
    print(json.dumps({"entries": len(entries), "written": written}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        sys.exit(2)
