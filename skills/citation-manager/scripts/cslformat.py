#!/usr/bin/env python3
"""Render bibliographic records to GB/T 7714-2015 (numeric) or APA 7 plain text.

Self-contained, standard library only. This exists so the skill does not depend on
Pandoc/citeproc being installed; the output contract for citation tasks usually asks
for exactly these two styles. Formatting rules follow GB/T 7714-2015 numeric order
and the CTeX gbt7714 style used as a rule oracle (LPPL, rules only, no code copied).

Input records use the same shape as structured.py bib output:
  {"id", "type", "fields": {...}, "persons": {"author": [...]}}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)

TYPE_TAG = {
    "article": "J", "inproceedings": "C", "conference": "C", "book": "M",
    "incollection": "M", "phdthesis": "D", "mastersthesis": "D",
    "techreport": "R", "patent": "P", "standard": "S", "misc": "Z", "online": "EB/OL",
}


def has_han(text: str) -> bool:
    return bool(HAN_RE.search(text or ""))


def strip_tex(text: str) -> str:
    text = re.sub(r"\\[A-Za-z]+\s*\{([^{}]*)\}", r"\1", text or "")
    return text.replace("{", "").replace("}", "").replace("\\&", "&").replace("\\_", "_").strip()


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", strip_tex(text)).casefold()
    return re.sub(r"\s+", "", "".join(c for c in text if c.isalnum() or HAN_RE.match(c)))


def field(rec: dict, name: str) -> str:
    f = rec.get("fields") or {}
    return strip_tex(str(f.get(name) or rec.get(name) or ""))


def authors(rec: dict) -> list[str]:
    persons = (rec.get("persons") or {}).get("author")
    if persons:
        return [str(p).strip() for p in persons if str(p).strip()]
    raw = field(rec, "author")
    return [a.strip() for a in re.split(r"\s+and\s+", raw) if a.strip()] if raw else []


def split_name(name: str) -> tuple[str, str]:
    """Return (family, given). Chinese names are returned whole as family."""
    name = name.strip()
    if "," in name:
        family, _, given = name.partition(",")
        return family.strip(), given.strip()
    if has_han(name):
        return name.replace(" ", ""), ""
    parts = name.split()
    return (parts[-1], " ".join(parts[:-1])) if parts else (name, "")


def initials(given: str, dotted: bool) -> str:
    parts = [p for p in re.split(r"[\s.\-]+", given) if p and p[0].isalpha()]
    if dotted:
        return " ".join(p[0].upper() + "." for p in parts)
    return "".join(p[0].upper() for p in parts)


def gbt_authors(rec: dict) -> str:
    names = authors(rec)
    if not names:
        return ""
    chinese = has_han(field(rec, "title"))
    out = []
    for name in names[:3]:
        family, given = split_name(name)
        if has_han(family):
            out.append(family)
        else:
            ini = initials(given, dotted=False)
            out.append(f"{family.upper()} {ini}".strip())
    if len(names) > 3:
        out.append("等" if chinese else "et al")
    return ", ".join(out)


def apa_authors(rec: dict) -> str:
    names = authors(rec)
    if not names:
        return ""
    out = []
    for name in names:
        family, given = split_name(name)
        if has_han(family):
            out.append(family)
        else:
            ini = initials(given, dotted=True)
            out.append(f"{family}, {ini}".rstrip(", "))
    if len(out) > 20:
        out = out[:19] + ["..."] + [out[-1]]
    if len(out) == 1:
        return out[0]
    return ", ".join(out[:-1]) + ", & " + out[-1]


def year_of(rec: dict) -> str:
    m = re.search(r"\d{4}", field(rec, "year") or field(rec, "date"))
    return m.group(0) if m else ""


def norm_doi(raw: str) -> str:
    d = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", (raw or "").strip(), flags=re.I)
    d = d.replace("\\textunderscore", "_").replace("\\_", "_")
    d = re.sub(r"[.,;:\]\)}\"']+$", "", d)
    return d if re.match(r"^10\.\d{4,9}/\S+$", d, re.I) else ""


def pages_of(rec: dict) -> str:
    return field(rec, "pages").replace("--", "-")


def is_preprint(rec: dict) -> bool:
    venue = (field(rec, "journal") + " " + field(rec, "booktitle")).lower()
    doi = norm_doi(field(rec, "doi"))
    return "arxiv" in venue or "preprint" in venue or bool(ARXIV_DOI.match(doi))


def fmt_gbt(rec: dict) -> str:
    f_type = (rec.get("type") or "misc").lower()
    tag = TYPE_TAG.get(f_type, "Z")
    names = gbt_authors(rec)
    title = field(rec, "title").rstrip(".")
    year = year_of(rec)
    doi = norm_doi(field(rec, "doi"))
    url = field(rec, "url")
    venue = field(rec, "journal") or field(rec, "booktitle")

    if is_preprint(rec) or tag == "EB/OL":
        tag = "EB/OL"
        m = ARXIV_DOI.match(doi)
        link = url or (f"https://arxiv.org/abs/{m.group(1)}" if m else (f"https://doi.org/{doi}" if doi else ""))
        cited = date.today().isoformat()
        s = f"{names}. {title}[{tag}]. ({year or 'n.d.'})[{cited}]. {link}." if link else \
            f"{names}. {title}[{tag}]. ({year or 'n.d.'})[{cited}]."
        return s

    if f_type == "book":
        place = field(rec, "address") or field(rec, "location")
        publisher = field(rec, "publisher")
        tail = f"{place + ': ' if place else ''}{publisher}" or venue
        if year:
            tail += f", {year}"
        if pages_of(rec):
            tail += f": {pages_of(rec)}"
        return f"{names}. {title}[M]. {tail}.".replace("..", ".")

    if f_type in {"inproceedings", "conference"}:
        tail = venue
        if year:
            tail += (", " if tail else "") + year
        if pages_of(rec):
            tail += f": {pages_of(rec)}"
        s = f"{names}. {title}[C]//{tail}." if tail else f"{names}. {title}[C]. {year}."
        return s

    tail = venue
    if year:
        tail += f", {year}"
    if field(rec, "volume"):
        tail += f", {field(rec, 'volume')}"
    if field(rec, "number"):
        tail += f"({field(rec, 'number')})"
    if pages_of(rec):
        tail += f": {pages_of(rec)}"
    s = f"{names}. {title}[{tag}]. {tail}." if tail else f"{names}. {title}[{tag}]."
    if doi:
        s += f" DOI: {doi}."
    elif url:
        s += f" {url}."
    return s


def fmt_apa(rec: dict) -> str:
    names = apa_authors(rec)
    year = year_of(rec) or "n.d."
    title = field(rec, "title").rstrip(".")
    doi = norm_doi(field(rec, "doi"))
    url = field(rec, "url")
    f_type = (rec.get("type") or "misc").lower()

    if is_preprint(rec):
        m = ARXIV_DOI.match(doi)
        link = f"https://arxiv.org/abs/{m.group(1)}" if m else url
        return f"{names} ({year}). {title}. arXiv. {link}".rstrip()

    if f_type == "book":
        publisher = field(rec, "publisher")
        s = f"{names} ({year}). {title}. {publisher}.".replace("..", ".")
    else:
        venue = field(rec, "journal") or field(rec, "booktitle")
        tail = venue
        if field(rec, "volume"):
            tail += f", {field(rec, 'volume')}"
        if field(rec, "number"):
            tail += f"({field(rec, 'number')})"
        if pages_of(rec):
            tail += f", {pages_of(rec)}"
        s = f"{names} ({year}). {title}."
        if tail:
            s += f" {tail}."
    if doi:
        s += f" https://doi.org/{doi}"
    elif url:
        s += f" {url}"
    return s


def load_entries(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get("entries", [])
    return data


def order_of(entries: list[dict], numbering: dict | None, style: str) -> list[dict]:
    by_id = {e["id"]: e for e in entries}
    if numbering and numbering.get("bibliography"):
        ordered = sorted(numbering["bibliography"], key=lambda b: b["number"])
        return [(by_id[b["id"]], b["number"]) for b in ordered if b["id"] in by_id]
    if style == "apa7":
        return [(e, None) for e in sorted(entries, key=lambda r: (fold(authors(r)[0]) if authors(r) else "", year_of(r)))]
    return [(e, i + 1) for i, e in enumerate(entries)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bib", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--style", choices=["gbt7714", "apa7"], default="gbt7714")
    ap.add_argument("--order", type=Path, default=None,
                    help="numbering.json from structured.py citations; sets the bibliography order")
    args = ap.parse_args()

    entries = load_entries(args.bib)
    numbering = json.loads(args.order.read_text(encoding="utf-8-sig")) if args.order else None
    fmt = fmt_gbt if args.style == "gbt7714" else fmt_apa
    lines = []
    for rec, num in order_of(entries, numbering, args.style):
        lines.append(f"[{num}] {fmt(rec)}" if num is not None else fmt(rec))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
