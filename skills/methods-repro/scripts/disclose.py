#!/usr/bin/env python3
"""Check that a methods draft discloses the parameters its facts table records.

reconcile (structured.py) compares facts across files. This one compares the facts
table with the manuscript text: a recorded parameter that never reaches the text, a
text value that differs from the logged value, a numeric value with no unit, or one
parameter carrying different values across runs are all reported.

Usage:
  python disclose.py /app/work/facts.json draft.md out.json [--required lr,batch_size]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

METADATA = ("seed", "random", "repeat", "replicate", "epoch", "hardware", "gpu", "cpu")
# parameters that are ratios or indices by definition; no unit is the correct record
DIMENSIONLESS = {"lr", "learning_rate", "alpha", "beta", "gamma", "ratio", "momentum",
                 "p_value", "f1", "auc", "accuracy", "precision", "recall", "correlation"}


def check(facts: list[dict], draft: str, required: list[str]) -> dict:
    issues: list[dict] = []
    values: dict[str, set[str]] = defaultdict(set)
    units: dict[str, set[str]] = defaultdict(set)

    for f in facts:
        name = str(f.get("parameter") or "").strip()
        value = str(f.get("value") or "").strip()
        if not name:
            issues.append({"kind": "fact_without_parameter", "run": f.get("run"),
                           "detail": "事实条目缺少参数名"})
            continue
        values[name].add(value)
        if f.get("unit"):
            units[name].add(str(f["unit"]).strip())
        if (re.fullmatch(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", value) and not f.get("unit")
                and name.casefold() not in DIMENSIONLESS):
            issues.append({"kind": "unit_missing_in_facts", "parameter": name,
                           "detail": f"数值 {value} 未记单位（无量纲请写 1 或 dimensionless）"})
        if not f.get("locator") or not f.get("file"):
            issues.append({"kind": "fact_without_anchor", "parameter": name,
                           "detail": "缺少文件+定位，无法回溯到配置或日志"})

    for name, vals in values.items():
        if len(vals) > 1:
            issues.append({"kind": "multi_run_values", "parameter": name,
                           "detail": "该参数在不同运行中取不同值：" + "、".join(sorted(vals)) +
                                     "；正文必须指明依据哪次运行，不能合并叙述"})

    for name in (required or list(values)):
        if name not in values:
            issues.append({"kind": "required_parameter_missing", "parameter": name,
                           "detail": "任务要求披露的参数在事实表中没有记录"})
        elif name not in draft:
            issues.append({"kind": "parameter_not_in_text", "parameter": name,
                           "detail": "事实表记录了该参数，正文未出现"})
        elif not any(v and v in draft for v in values[name]):
            issues.append({"kind": "value_not_in_text", "parameter": name,
                           "detail": "正文出现参数名但未给出记录值：" + "、".join(sorted(values[name]))})

    for name in values:
        if name in draft:
            for v in values[name]:
                if not v or v not in draft:
                    continue
                tail = draft[draft.find(v):draft.find(v) + 40]
                us = units.get(name) or set()
                if us and not any(u in tail for u in us) and re.fullmatch(r"-?\d+(?:\.\d+)?", v):
                    issues.append({"kind": "unit_missing_in_text", "parameter": name,
                                   "detail": f"正文数值 {v} 附近未见单位 {'/'.join(sorted(us))}"})
                break

    text_l = draft.casefold()
    missing_meta = [m for m in METADATA if m not in text_l]
    if facts and len(missing_meta) >= len(METADATA) - 2:
        issues.append({"kind": "run_metadata_thin", "detail":
                       "正文几乎未提随机种子、重复次数或硬件环境；若材料未记录，应写明未知而非略过"})

    return {"issues": issues,
            "summary": {"parameters": len(values), "runs": len({f.get("run") for f in facts}), "issues": len(issues)},
            "policy": "只核对已记录事实与正文的一致性；材料未记录的值标为未知，不代填默认值。"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("facts", type=Path)
    ap.add_argument("draft", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--required", default="")
    args = ap.parse_args()

    facts = json.loads(args.facts.read_text(encoding="utf-8-sig"))
    facts = facts.get("facts", facts) if isinstance(facts, dict) else facts
    required = [x.strip() for x in args.required.split(",") if x.strip()]
    result = check(facts, args.draft.read_text(encoding="utf-8-sig"), required)
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
