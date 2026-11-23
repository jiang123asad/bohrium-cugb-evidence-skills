#!/usr/bin/env python3
"""Parse tagged reference exports (RIS, EndNote tagged, RefWorks / CNKI) into bib.json.

CNKI exports are RefWorks-like tagged text (`%T`, `%A`, `%J`, `%D`, ...) or EndNote
tagged (`%0`, `%A`, `%T`), often encoded GBK/GB18030 rather than UTF-8. This parser
detects encoding and tag family, and emits the same record shape as
structured.py bib so downstream dedupe/verify/format commands work unchanged.
Format mapping follows the Zotero translator tables used as a spec (AGPL; consulted
for the field map only, no code copied).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

TYPE_MAP = {
    "jour": "article", "journal": "article", "journal article": "article",
    "journal article (print)": "article", "book": "book", "book, whole": "book",
    "book section": "incollection", "chap": "incollection",
    "conf": "inproceedings", "conference proceeding": "inproceedings",
    "conference paper": "inproceedings", "cpaper": "inproceedings",
    "thesis": "phdthesis", "dissertation": "phdthesis", "rprt": "techreport",
    "report": "techreport", "elec": "misc", "web page": "misc", "patent": "patent",
    "standard": "misc", "gen": "misc", "generic": "misc",
}

RIS_MAP = {
    "TI": "title", "T1": "title", "JO": "journal", "JF": "journal", "JA": "journal",
    "T2": "journal", "PY": "year", "Y1": "year", "VL": "volume", "IS": "number",
    "SP": "pages", "DO": "doi", "UR": "url", "AB": "abstract", "N2": "abstract",
    "PB": "publisher", "CY": "address", "SN": "issn", "ET": "edition",
}
REFWORKS_MAP = {
    "T1": "title", "JF": "journal", "JO": "journal", "YR": "year", "VO": "volume",
    "IS": "number", "SP": "pages", "DO": "doi", "LK": "url", "AB": "abstract",
    "PB": "publisher", "PP": "address", "SN": "issn",
}
ENDNOTE_MAP = {
    "T": "title", "J": "journal", "B": "journal", "D": "year", "V": "volume",
    "N": "number", "P": "pages", "R": "doi", "U": "url", "X": "abstract",
    "I": "publisher", "C": "address", "@": "issn",
}
AUTHOR_TAGS = {"AU", "A1", "A"}
TYPE_TAG_RIS = "TY"
TYPE_TAG_REFWORKS = "RT"
TYPE_TAG_ENDNOTE = "0"


def decode(raw: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "utf-8-replace"


def detect_family(lines: list[str]) -> str:
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if re.match(r"^[A-Z][A-Z0-9]\s+-\s", s):
            return "ris"
        if re.match(r"^%[0-9A-Z@]\s", s):
            return "endnote"
        if re.match(r"^[A-Z][A-Z0-9]\s+\S", s):
            return "refworks"
    return "unknown"


def fold_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return re.sub(r"[^0-9a-z]+|[\u3400-\u9fff]+", "", text)


def make_id(rec_fields: dict, authors: list[str], used: set[str]) -> str:
    fam = ""
    if authors:
        a = authors[0]
        fam = a.split(",")[0].strip() if "," in a else (a.split()[-1] if a.split() else a)
    fam = fold_key(fam) or "ref"
    year = re.search(r"\d{4}", rec_fields.get("year", ""))
    base = f"{fam}{year.group(0) if year else ''}"
    title_word = fold_key(rec_fields.get("title", ""))[:12]
    key = base + title_word
    n, final = 1, key
    while final in used:
        n += 1
        final = f"{key}{chr(96 + n)}"
    used.add(final)
    return final


def parse_records(text: str, family: str) -> list[dict]:
    lines = text.splitlines()
    records: list[dict] = []
    cur_fields: dict = {}
    cur_authors: list[str] = []
    cur_type = ""
    started = False

    def flush():
        nonlocal cur_fields, cur_authors, cur_type, started
        if started and (cur_fields or cur_authors):
            records.append({
                "type": TYPE_MAP.get(cur_type.casefold(), "article"),
                "fields": dict(cur_fields),
                "authors": list(cur_authors),
            })
        cur_fields, cur_authors, cur_type, started = {}, [], "", False

    for line in lines + ["END  - flush"]:
        if family == "ris":
            m = re.match(r"^([A-Z][A-Z0-9])\s+-\s?(.*)$", line)
            if not m:
                continue
            tag, value = m.group(1), m.group(2).strip()
            if tag == "TY":
                flush()
                started = True
                cur_type = value
                continue
            if tag == "ER":
                flush()
                continue
            if not started:
                continue
            if tag == "AU":
                cur_authors.append(value)
            elif tag == "EP":
                cur_fields["pages"] = (cur_fields.get("pages", "") + "-" + value).lstrip("-")
            elif tag in RIS_MAP:
                key = RIS_MAP[tag]
                cur_fields[key] = (cur_fields[key] + " " + value).strip() if key in cur_fields and key == "title" else value
        elif family == "endnote":
            m = re.match(r"^%([0-9A-Z@])\s?(.*)$", line)
            if not m:
                continue
            tag, value = m.group(1), m.group(2).strip()
            if tag == "0":
                flush()
                started = True
                cur_type = value
                continue
            if not started:
                continue
            if tag == "A":
                cur_authors.append(value)
            elif tag in ENDNOTE_MAP:
                cur_fields[ENDNOTE_MAP[tag]] = value
        elif family == "refworks":
            m = re.match(r"^([A-Z][A-Z0-9])\s+(.*)$", line)
            if not m:
                continue
            tag, value = m.group(1), m.group(2).strip()
            if tag == "RT":
                flush()
                started = True
                cur_type = value
                continue
            if not started:
                continue
            if tag == "A1":
                cur_authors.append(value)
            elif tag in REFWORKS_MAP:
                cur_fields[REFWORKS_MAP[tag]] = value
    flush()
    return records


def finalize(records: list[dict]) -> list[dict]:
    used: set[str] = set()
    out = []
    for rec in records:
        fields = rec["fields"]
        authors = []
        for a in rec["authors"]:
            for part in re.split(r";\s*", a):
                part = part.strip()
                if part and part.casefold() not in {"others", "et al", "等"}:
                    authors.append(part)
        if "pages" not in fields and "OP" in fields:
            fields["pages"] = fields.pop("OP")
        rec_id = make_id(fields, authors, used)
        out.append({
            "id": rec_id,
            "type": rec["type"],
            "fields": fields,
            "persons": {"author": authors},
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("out", type=Path)
    args = ap.parse_args()

    raw = args.input.read_bytes()
    text, encoding = decode(raw)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    family = detect_family(lines)
    if family == "unknown":
        raise ValueError("unrecognised export format: expected RIS, EndNote tagged, or RefWorks/CNKI tagged text")
    records = finalize(parse_records(text, family))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "source_format": family, "source_encoding": encoding,
        "entries": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"entries": len(records), "format": family, "encoding": encoding}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        sys.exit(2)
