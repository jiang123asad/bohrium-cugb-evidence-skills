#!/usr/bin/env python3
"""Terminology consistency scan for academic prose.

Checks (all advisory, no rewriting):
  1. abbreviation used before its definition
  2. canonical term vs listed variants mixed in one text
  3. casing variants of the same multiword term
  4. hyphen/space variants (co-author vs coauthor)
  5. undefined uppercase abbreviations appearing repeatedly

Glossary format (optional):
  {"terms":[{"canonical":"perovskite solar cell","variants":["PSC"],
             "abbr":"PSC","definition":"perovskite solar cell (PSC)"}]}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ABBR_DEF = re.compile(r"([A-Za-z][A-Za-z \-]{3,}?)\s*\(([A-Z][A-Za-z0-9\-]{1,7})\)")
ABBR = re.compile(r"\b([A-Z][A-Z0-9]{1,6})\b")
PROTECTED = {"AI", "DNA", "RNA", "DOI", "PDF", "SI", "GB", "ISO", "URL", "API", "CPU", "GPU",
             "NMR", "XRD", "SEM", "TEM", "FTIR", "UV", "IR", "pH"}


def sentences(text: str) -> list[tuple[int, str]]:
    out, start = [], 0
    for m in re.finditer(r"[^。！？.!?\n]+[。！？.!?]?", text):
        seg = m.group(0)
        if seg.strip():
            out.append((m.start(), seg))
    return out


def scan(text: str, glossary: list[dict]) -> dict:
    issues: list[dict] = []
    lower = text.casefold()

    # 1-2. glossary-driven checks
    for term in glossary:
        canonical = term.get("canonical", "")
        abbr = term.get("abbr", "")
        definition = term.get("definition", "")
        variants = [v for v in term.get("variants", []) if v]

        if abbr:
            use = text.find(abbr)
            defined_at = text.find(definition) if definition else text.find(f"({abbr})")
            if use >= 0 and (defined_at < 0 or use < defined_at):
                issues.append({"kind": "abbr_before_definition", "term": abbr,
                               "detail": "缩写先于其定义出现" if defined_at >= 0 else "缩写出现但全篇没有定义",
                               "position": use})

        if canonical:
            canon_count = len(re.findall(re.escape(canonical), text, re.I))
            for v in variants:
                if v == canonical:
                    continue
                hits = [m.start() for m in re.finditer(re.escape(v), text)]
                if hits and (canon_count or len(hits) < canon_count + len(hits)):
                    issues.append({"kind": "variant_mixed", "term": canonical,
                                   "detail": f"变体「{v}」与规范写法并用（{len(hits)} 处）",
                                   "positions": hits[:10]})

    # 3. casing variants of two-word terms, ignoring sentence-initial capitals
    spans = [(m.start(), m.group(0)) for m in re.finditer(r"[A-Za-z][A-Za-z\-]+", text)]
    groups = defaultdict(list)
    for (p1, w1), (p2, w2) in zip(spans, spans[1:]):
        if len(w1) < 3 and len(w2) < 3:
            continue
        gap = text[p1 + len(w1):p2]
        if gap.strip():
            continue
        sent_start = p1 == 0 or text[max(0, p1 - 2):p1].strip()[-1:] in {"。", "！", "？", ".", "!", "?"}
        groups[(w1.casefold(), w2.casefold())].append((f"{w1}{gap}{w2}", sent_start))
    for key, forms in groups.items():
        surface = {f for f, init in forms if not init}
        if len(surface) > 1 and len(forms) >= 2:
            issues.append({"kind": "inconsistent_casing", "term": " ".join(key),
                           "detail": "同一术语存在多种大小写：" + " / ".join(sorted(surface)),
                           "counts": {f: sum(1 for x, _ in forms if x == f) for f in surface}})

    # 4. hyphen/space variants: "co-author" vs "coauthor"
    hyphenated = {f"{m.group(1).casefold()}-{m.group(2).casefold()}"
                  for m in re.finditer(r"\b([A-Za-z]{2,})-([A-Za-z]{2,})\b", text)}
    tokens = {t.casefold() for t in re.findall(r"\b[A-Za-z][A-Za-z]*\b", text)}
    for h in hyphenated:
        joined = h.replace("-", "")
        if joined in tokens:
            issues.append({"kind": "hyphen_variant", "term": joined,
                           "detail": f"同时出现「{h}」与「{joined}」",
                           "forms": [h, joined]})

    # 5. undefined abbreviations
    defined = {m.group(2) for m in ABBR_DEF.finditer(text)}
    used = Counter(m.group(1) for m in ABBR.finditer(text))
    for abbr, n in used.items():
        if n >= 2 and abbr not in defined and abbr not in PROTECTED:
            if re.search(rf"\({abbr}\)|\b{abbr}\b\s*[（(]", text):
                continue
            issues.append({"kind": "undefined_abbr", "term": abbr,
                           "detail": f"缩写出现 {n} 次，未见「全称（缩写）」式定义"})

    summary = Counter(i["kind"] for i in issues)
    return {"issues": issues, "summary": dict(summary),
            "policy": "只报告不一致与缺失定义，不自动替换；改与不改由作者确认。"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("text", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--glossary", type=Path, default=None)
    args = ap.parse_args()

    text = args.text.read_text(encoding="utf-8-sig")
    glossary = []
    if args.glossary:
        data = json.loads(args.glossary.read_text(encoding="utf-8-sig"))
        glossary = data.get("terms", data) if isinstance(data, dict) else data
    result = scan(text, glossary)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        sys.exit(2)
