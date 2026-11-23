# 最小调用示例（示例，非正式案例）

输入：`input/refs.bib`（两条含 DOI 的记录，题名带内层花括号）与 `input/ordered.json`
（引用簇 c1 先引 demoB，c2 引 demoA+demoB）。

```bash
S=/skills/weekly-skill/scripts
python "$S/structured.py" bib examples/input/refs.bib bib.json
python "$S/structured.py" citations examples/input/ordered.json numbering.json
```

实际输出见 `output/bib.json`（pybtex 0.26.1 正确解析内层花括号）与 `output/numbering.json`：
按首次出现顺序 demoB=1、demoA=2，c2 渲染为 [1,2]；unused_canonical_ids 为空。

## 核验与去重示例（`audit/`，合成数据）

`audit/records.json` 含五条记录：真实 DOI（LeCun 2015）、同 DOI 但题名不符、不存在的 DOI、
无 DOI 的中文条目、arXiv DOI。`audit/claims.json` 含三条论断，分别对应可支持、数字不符、
主题无关。

```bash
python "$S/refaudit.py" dedupe  examples/audit/records.json merges.json
python "$S/refaudit.py" verify  examples/audit/records.json verification.json --online
python "$S/refaudit.py" support examples/audit/claims.json support.json
python "$S/cslformat.py" bib.json references.txt --style gbt7714 --order numbering.json
python "$S/refaudit.py" audit-markers manuscript.txt numbering.json rendered_audit.json
```

2026-11-22 的实际运行结果（Crossref 与 OpenAlex 可达，DataCite 与 doi.org 在本机不可达）：

- `dedupe`：同 DOI 的两条记录成组，`confirmation_required=true`（题名冲突，提示录入错误）。
- `verify`：`VERIFIED` 两条（Crossref 一条、OpenAlex 一条）、`DOI_MISMATCH` / `UNRESOLVED` /
  `UNVERIFIABLE` 各一条；不存在的 DOI 只在 Crossref 范围内判 `UNRESOLVED`。
- `support`：`SUPPORTED` / `PARTIAL`（33% 不在摘要中）/ `NOT_SUPPORTED` 各一条。
- `cslformat`：GB/T 输出形如 `LECUN Y, BENGIO Y, HINTON G. Deep learning[J]. Nature, 2015,
  521(7553): 436-444. DOI: 10.1038/nature14539.`；中文条目保留原姓名，arXiv 条目渲染为
  `[EB/OL]`。
- `audit-markers`：正文标记序列或文末编号与期望不一致时输出 `body_cluster` /
  `bibliography` 差异。

这些是合成的接口演示，不是官方案例输出。

## 导出与插入建议（`audit/`）

```bash
python "$S/export.py"  examples/audit/library.json --out references --format both
python "$S/suggest.py" examples/audit/manuscript.md examples/audit/library.json suggestions.json \
       --annotate manuscript-suggested.md
```

2026-11-22 实跑：`export` 写出 3 条 RIS 与 3 条 BibTeX，RIS 回读后 DOI 与页码一致；
`suggest` 对 `manuscript.md` 给出 1 条 `review`（zhang2023，匹配度 0.467），已带 `[D1]` 的句子被跳过，
未达阈值的句子不插入。插入建议只写 `{{cite:key}}` 占位，是否支持该句仍需读原文。

