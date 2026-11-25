# 最小调用示例（示例，非正式案例）

输入：`input/refs.bib`（两条含 DOI 的记录，题名带内层花括号）与 `input/ordered.json`
（引用簇 c1 先引 demoB，c2 引 demoA+demoB）。

```bash
S=./scripts # 在本 Skill 根目录运行
python "$S/structured.py" bib examples/input/refs.bib bib.json
python "$S/structured.py" citations examples/input/ordered.json numbering.json
```

实际输出见 `output/bib.json`（pybtex 0.26.1 正确解析内层花括号）与 `output/numbering.json`：
按首次出现顺序 demoB=1、demoA=2，c2 渲染为 [1,2]；unused_canonical_ids 为空。
