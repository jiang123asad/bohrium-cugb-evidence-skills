# 最小调用示例（示例，非正式案例）

输入：`input/notes-model.json`，三节章节树、两条带证据锚点的笔记，required_categories 含 results 与 limitation。

```bash
S=./scripts # 在本 Skill 根目录运行
python "$S/structured.py" notes examples/input/notes-model.json notes-index.json
```

实际输出见 `output/notes-index.json`：uncovered=[results, limitation]，unnoted_sections=[S3]，
并给出 section_order 与类别索引。缺口须区分“原文未报告”与“抽取遗漏”。
