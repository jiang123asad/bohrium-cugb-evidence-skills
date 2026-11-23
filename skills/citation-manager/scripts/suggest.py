#!/usr/bin/env python3
"""Propose citation insert points: which sentence needs a source, and which record fits.

This is deliberately advisory. It scores each uncited sentence against record titles and
abstracts by term overlap, returns ranked candidates with the evidence, and only writes
a {{cite:key}} placeholder when the match is strong; everything else is left for review.
It never edits the bibliography and never invents a citation for a weak match.

Usage:
  python suggest.py manuscript.md bib.json suggestions.json [--annotate out.md]
                    [--min-score 0.5] [--margin 0.1]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

HAN_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
LATIN_RE = re.compile(r"[a-z0-9]+")
SENT_RE = re.compile(r"[^。！？.!?\n]+[。！？.!?]?")
CITE_RE = re.compile(
    r"\{\{cite:[^}]+\}\}|\\cite\{[^}]+\}"
    r"|\[\s*(?:[A-Za-z]{1,4})?\d+(?:\s*[-–,]\s*(?:[A-Za-z]{1,4})?\d+)*\s*\]"
)
BIB_HEAD_RE = re.compile(r"^\s*#{0,6}\s*(参考文献|references|bibliography)\b", re.I | re.M)
STOP = {"the", "and", "for", "with", "from", "into", "using", "based", "this", "that",
        "study", "new", "via", "are", "was", "were", "has", "have", "not", "but"}


def words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    out = [w for w in LATIN_RE.findall(text) if len(w) > 2 and w not in STOP]
    for run in HAN_RUN.findall(text):
        out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return list(dict.fromkeys(out))


def field(rec: dict, name: str) -> str:
    f = rec.get("fields") or {}
    return str(f.get(name) or rec.get(name) or "")


def score(sentence: str, rec: dict) -> float:
    target = set(words(field(rec, "title"))) | set(words(field(rec, "abstract")))
    if not target:
        return 0.0
    sw = set(words(sentence))
    if not sw:
        return 0.0
    hit = len(sw & target)
    # coverage of the record's terms, damped by sentence length so a long paragraph
    # does not win just by containing many words
    return round(hit / (len(target) ** 0.5) / max(1.0, len(sw) ** 0.5), 3)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manuscript", type=Path)
    ap.add_argument("bib", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--annotate", type=Path, default=None)
    ap.add_argument("--min-score", type=float, default=0.5)
    ap.add_argument("--margin", type=float, default=0.1)
    args = ap.parse_args()

    text = args.manuscript.read_text(encoding="utf-8-sig")
    data = json.loads(args.bib.read_text(encoding="utf-8-sig"))
    entries = data.get("entries", []) if isinstance(data, dict) else data

    head = BIB_HEAD_RE.search(text)
    body = text[:head.start()] if head else text

    rows = []
    for m in SENT_RE.finditer(body):
        sent = m.group(0).strip()
        if len(sent) < 8 or sent.startswith(("#", "|", ">")) or CITE_RE.search(sent):
            continue
        ranked = sorted(((score(sent, r), r.get("id")) for r in entries), reverse=True)[:3]
        ranked = [(s, i) for s, i in ranked if s > 0]
        if not ranked:
            continue
        top, top_id = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else 0.0
        if top >= args.min_score and (top - second) >= args.margin:
            action = "insert"
        elif top >= 0.3:
            action = "review"
        else:
            continue
        rows.append({
            "sentence": sent,
            "offset": m.start(),
            "action": action,
            "candidates": [{"id": i, "score": s} for s, i in ranked],
            "evidence": f"题名/摘要词项与句子的匹配度 {top:.2f}，领先次优 {top - second:.2f}",
        })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "suggestions": rows,
        "policy": "insert 只在匹配度与领先幅度都过阈值时给出，写入的是 {{cite:key}} 占位；"
                  "review 必须人工确认。引用是否真的支持该句仍要读原文。",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.annotate:
        out, last = [], 0
        for row in rows:
            if row["action"] != "insert":
                continue
            end = row["offset"] + len(row["sentence"])
            sent = row["sentence"]
            punct = sent[-1] if sent and sent[-1] in "。！？.!?" else ""
            core = sent[:-1] if punct else sent
            out.append(body[last:row["offset"]])
            out.append(f"{core}{{{{cite:{row['candidates'][0]['id']}}}}}{punct}")
            last = end
        out.append(body[last:])
        out.append(text[head.start():] if head else "")
        args.annotate.parent.mkdir(parents=True, exist_ok=True)
        args.annotate.write_text("".join(out), encoding="utf-8")

    print(json.dumps({"suggestions": len(rows),
                      "insert": sum(1 for r in rows if r["action"] == "insert"),
                      "review": sum(1 for r in rows if r["action"] == "review")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        sys.exit(2)
