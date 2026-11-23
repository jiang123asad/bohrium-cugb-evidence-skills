# 最小调用示例（示例，非正式案例）

输入：`input/source.txt`（两行来源文本）与 `input/quotes.json`（两条引文：一条真实、一条篡改）。

```bash
S=/skills/weekly-skill/scripts
python "$S/core.py" quotes examples/input examples/input/quotes.json
```

实际输出见 `output/verified.json`：第 0 条 verified=true；第 1 条 “validated across all regions”
不在指定行段内，verified=false（quote_not_in_range）；因含失败引文，命令退出码为 1，这是本示例的预期结果。论断—证据映射与分组模板见 assets/；
人工核验流程用 upstream/audit_claims.py，未做人工核验不得标注已验证。

## 原文证据检索

```bash
python "$S/evidence.py" index examples/input examples/input/retrieval-sources.json evidence-index.json
python "$S/evidence.py" search evidence-index.json examples/input/retrieval-queries.json retrieval.json
```

Q1 会返回原段中的否定句 `No cross-region validation was reported.`；Q2 没有词汇匹配。输出示例见 `output/evidence-index.json`、`output/retrieval.json`。将返回的 chunk ID 与原文短引写入 `{claims:[{id,statement,evidence:[{chunk_id,quote}]}]}` 后，可用 `evidence.py verify <原件目录> <索引> <论断清单> <报告>` 检查锚点；`output/anchors.json` 为合成例子的核验结果。锚点有效不代表论断正确，语义支持仍标为未评估。
