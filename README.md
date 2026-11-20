# Evidence Skills · CUGB Bohrium v5

这五个独立 Skill 面向地大分站赛第一期的文献阅读题。每个目录包含可独立加载的 `SKILL.md`、脚本、依赖说明和可复跑合成示例。

| Skill | 核心功能 |
|---|---|
| [pdf-layout](skills/pdf-layout) | 按页抽取 PDF 文本、版面和表格，稳定元素位置 |
| [paper-translation](skills/paper-translation) | 按段对齐译文，检查漏段、术语和科学数值 |
| [structured-notes](skills/structured-notes) | 用章节、来源定位和引文组织笔记 |
| [nanopub](skills/nanopub) | 构建离线、未签名的 RDF 纳米出版物，并回读命名图 |
| [literature-graph](skills/literature-graph) | 按证据定义实体和有向边，保留歧义并查询证据链 |

v5 合并两条 v4 的改进：Docling 离线导入、原文引文定位、真实交付文件检查和多跳图谱。原按行检索为默认，BM25 是可选辅助。引用的开源项目、依赖与适用边界见各 Skill；示例为合成材料，不含正式赛例或官方成绩。

