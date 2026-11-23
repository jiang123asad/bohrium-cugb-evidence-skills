# 第三方组件与规则来源

本包**未收录任何第三方源代码**。`scripts/refaudit.py` 为独立实现，仅借鉴下列项目的判定思路与阈值，许可证信息一并列出以备复核。

| 项目 | 许可证 | 借鉴内容 | 使用方式 |
|---|---|---|---|
| [tvquynh/citation-verifier](https://github.com/tvquynh/citation-verifier) | MIT | 四态判定、DOI→题名相似度门限、"故障不等于造假" | 重写实现 |
| [TUM-AVS/Bib-Hallucination-Checker](https://github.com/TUM-AVS/Bib-Hallucination-Checker) | MIT | 误报规则：副标题处理、作者名变体、空作者表不判负面 | 重写实现 |
| [rpatrik96/bibtexupdater](https://github.com/rpatrik96/bibtexupdater) | MIT | 预印本→正式版的来源优先级（arXiv → OpenAlex → Crossref） | 规则参考 |
| [CTeX-org/gbt7714-bibtex-style](https://github.com/CTeX-org/gbt7714-bibtex-style) | LPPL-1.3c | GB/T 7714 的文献类型标识、中英文分支与标点规则 | 仅规则参考，未收录 `.bst` |
| [citation-js/citation-js](https://github.com/citation-js/citation-js) | MIT（`plugin-csl` 依赖 citeproc-js，CPAL/AGPL） | 格式归一化与字段映射思路 | 未收录 |
| [citation-style-language/styles](https://github.com/citation-style-language/styles) | CC BY-SA 3.0 | GB/T 7714 与 APA 7 样式文件 | 未收录，避免样式许可牵连 |
| [zotero/translators](https://github.com/zotero/translators) | AGPL-3.0 | RIS / EndNote tagged / RefWorks（CNKI 导出）的字段映射与编码习惯 | 仅作格式规范参考，`tagged.py` 为独立实现 |
| [OpenAlex](https://api.openalex.org) | CC0（数据） | DOI 元数据第二来源，用于 Crossref 未命中时的交叉核验 | 运行时 API 调用，非代码 |

## 运行时依赖

- Python 3.10+，标准库即可运行 `refaudit.py`、`cslformat.py`、`tagged.py`。
- `structured.py bib` 需要 Pybtex（`requirements.txt`）。
- 在线核验使用 Crossref 与 OpenAlex 公开 API；DataCite 与 doi.org Handle 注册表在不可达时自动降级为 `INCONCLUSIVE`。
- 非 GB/T 7714 / APA 7 的 CSL 渲染（若任务要求）使用平台预装的 Pandoc citeproc，不随包分发。

## 许可

本包自身脚本可按赛事要求复用；借鉴的规则思路来自上述 MIT/LPPL/CC BY-SA 项目，若后续需要分发修改后的样式文件，须遵守其原始许可。
