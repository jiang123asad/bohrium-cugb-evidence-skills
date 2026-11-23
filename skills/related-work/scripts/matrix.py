#!/usr/bin/env python3
"""Check a related-work draft against its comparison matrix.

Four checks, all mechanical:
  1. thin_record      a reviewed source lacks one of the fields a comparison needs
  2. group_defect     a comparison group has fewer than two sources or no stated basis
  3. ungrouped_source a reviewed source appears in no group
  4. uncited_source   a reviewed source is never cited in the draft
  5. overclaim        the draft uses a first/best/unstudied claim the matrix cannot support

It reports; it does not rewrite. Whether a group is genuinely comparable is an author call.

Usage:
  python matrix.py sources.json groups.json draft.md out.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

FIELDS = ("problem", "method", "result", "condition", "limitation")
OVERCLAIM = re.compile(
    r"首次|首创|第一个|首个|前所未有|无人(?:研究|涉足)|尚无(?:研究|报道)|全面优于|远超|唯一"
    r"|\bfirst\b|\bnovel\b|\bunprecedented\b|\boutperforms? all\b|\bstate[- ]of[- ]the[- ]art\b",
    re.I)


def load(path: Path, key: str):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data[key] if isinstance(data, dict) else data


def check(sources: list[dict], groups: list[dict], draft: str) -> dict:
    issues: list[dict] = []
    by_id = {s.get("id"): s for s in sources if s.get("id")}

    for s in sources:
        missing = [f for f in FIELDS if not str(s.get(f) or "").strip()]
        if missing:
            issues.append({"kind": "thin_record", "source": s.get("id"),
                           "detail": "比较所需字段缺失：" + "、".join(missing)})
        if not str(s.get("anchor") or "").strip():
            issues.append({"kind": "thin_record", "source": s.get("id"),
                           "detail": "缺少原文锚点，无法回溯"})

    grouped = set()
    for g in groups:
        members = [m for m in g.get("sources", []) if m in by_id]
        grouped.update(members)
        if len(members) < 2:
            issues.append({"kind": "group_defect", "group": g.get("name"),
                           "detail": f"组内仅 {len(members)} 份来源，无法构成比较"})
        if not str(g.get("basis") or "").strip():
            issues.append({"kind": "group_defect", "group": g.get("name"),
                           "detail": "未写明组内比较的共同维度或口径"})
        for m in g.get("sources", []):
            if m not in by_id:
                issues.append({"kind": "dangling_member", "group": g.get("name"),
                               "detail": f"引用了不存在的来源 {m}"})

    for sid in by_id:
        if sid not in grouped:
            issues.append({"kind": "ungrouped_source", "source": sid,
                           "detail": "已读来源未进入任何比较组"})
        cited = re.search(rf"\[@?{re.escape(str(sid))}\]|\({re.escape(str(sid))}\)|\[{re.escape(str(sid))}\]",
                          draft) or (by_id[sid].get("title") and by_id[sid]["title"][:12] in draft)
        if not cited:
            issues.append({"kind": "uncited_source", "source": sid,
                           "detail": "稿件正文未引用该来源"})

    for m in OVERCLAIM.finditer(draft):
        sent = draft[max(0, m.start() - 60):m.start() + 60].replace("\n", " ")
        issues.append({"kind": "overclaim", "term": m.group(0),
                       "detail": f"绝对化表述需证据支撑；上下文：…{sent}…"})

    return {"issues": issues, "summary": dict(Counter(i["kind"] for i in issues)),
            "policy": "只报告可核对的缺陷；组是否真的同口径、论断是否成立，仍由作者读原文判断。"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sources", type=Path)
    ap.add_argument("groups", type=Path)
    ap.add_argument("draft", type=Path)
    ap.add_argument("out", type=Path)
    args = ap.parse_args()

    result = check(load(args.sources, "sources"), load(args.groups, "groups"),
                   args.draft.read_text(encoding="utf-8-sig"))
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
