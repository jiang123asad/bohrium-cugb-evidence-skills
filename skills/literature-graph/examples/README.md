# 最小调用示例（示例，非正式案例）

输入：`input/graph-model.json`，两篇论文各自定义缩写 RF（Random Forest / Ridge Fusion），一条 use 边和一条 evaluated_on 边；两份原文同在 input。

```bash
S=./scripts # 在本 Skill 根目录运行
python "$S/structured.py" graph examples/input/graph-model.json graph-index.json
```

实际输出见 `output/graph-index.json`：ambiguous_aliases 中 `rf → [n1, n2]`，两个实体保留独立 ID；
边端点、证据字段通过校验。`query_results` 中 Q1=matched（带 e1 及原文）、Q2=ambiguous（RF 有两个候选）、Q3=no_match（反方向无边）、Q4=unknown_entity。Q5=matched，列出 n1→n3→n4 的两条真实边及各自的引文；该链没有增加 n1 与 n4 的直接边。
