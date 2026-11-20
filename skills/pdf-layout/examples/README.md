# 最小调用示例（示例，非正式案例）

输入：`input/make_sample_pdf.py` 生成的双栏演示 PDF（两页，含重复页眉、章节 1/1.1、公式 (1)、图题）。
PDF 为二进制，按平台纯文本资源约定不随包分发；先运行生成脚本（需 pymupdf）。

```bash
S=/skills/weekly-skill/scripts   # 本地复跑时改为实际 scripts 目录
python examples/input/make_sample_pdf.py        # 生成 sample-two-column.pdf
python "$S/layout.py" sample-two-column.pdf layout.json --tables
```

实际输出见 `output/layout.json`（本包已在 pdfplumber 0.11.10 / Python 3.11 实际运行）：
- 第 1 页检出 gutter=306.0pt，左栏读完再读右栏，公式 (1) 标为 formula_candidate；
- `1. Introduction` heading_level=1，`1.1. Study area` heading_level=2；
- 两页重复页眉与页码标为 repeated_margin_candidate，保留原始块不删除。

## Docling 导出 JSON（合成示例）

`input/docling-model.json` 由 docling-core 2.107.1 / DoclingDocument 1.11.0 的真实数据模型生成，不是论文解析结果，也不存在其 origin 所述原 PDF。

```bash
python "$S/layout.py" examples/input/docling-model.json docling-layout.json --format docling
```

本命令只需标准库，结果见 `output/docling-layout.json`。保留 body 顺序、BOTTOMLEFT 坐标转换、跨页 orig 字符跨度、表格合并格和题注引用；缺定位文本留在 unmapped_items。图片不加载，origin 不验证；input_sha256 只校验此 JSON。

直接 PDF 和 Docling JSON 均报告 `page_count` 与 `extracted_pages`。引文核验依据原 PDF 或导出 JSON 的相应 SHA256，二者不可互换。
