# Evidence Skills · CUGB Bohrium · Harbor 交付版

这五个独立 Skill 面向地大分站赛第一期的文献阅读题。每个目录包含可独立加载的 `SKILL.md`、脚本、依赖说明和可复跑合成示例。

| Skill | 核心功能 |
|---|---|
| [pdf-layout](skills/pdf-layout) | 按页抽取 PDF 文本、版面和表格，稳定元素位置 |
| [paper-translation](skills/paper-translation) | 按段对齐译文，检查漏段、术语和科学数值 |
| [structured-notes](skills/structured-notes) | 用章节、来源定位和引文组织笔记 |
| [nanopub](skills/nanopub) | 构建离线、未签名的 RDF 纳米出版物，并回读命名图 |
| [literature-graph](skills/literature-graph) | 按证据定义实体和有向边，保留歧义并查询证据链 |

v6 延续原有的 Docling 离线导入、引文定位和查询脚本，在此基础上增加交付契约检查、版面成品反查、译段覆盖、直接证据与推断分流、断言导出审核，以及条件不同的关系回查。按行检索仍是默认，BM25 可按需使用。各 Skill 标明依赖、开源来源和适用范围。示例只用于复跑接口。

