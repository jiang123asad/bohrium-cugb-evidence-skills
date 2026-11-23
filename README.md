# Bohrium · CUGB 分站赛 · 十项 Skill 交付版

这十个独立 Skill 面向地大分站赛第一期的文献阅读与写作题。每个目录包含可独立加载的 `SKILL.md`、脚本、依赖说明和可复跑示例；`traces/` 保存各题的提交轨迹，`MANIFEST.md` 记录对应的 challenge 与 attempt 编号。

## 阅读类

| Skill | 核心功能 |
|---|---|
| [pdf-layout](skills/pdf-layout) | 按页抽取 PDF 文本、版面和表格，稳定元素位置 |
| [paper-translation](skills/paper-translation) | 按段对齐译文，按段路由术语表，检查漏段、术语和科学数值 |
| [structured-notes](skills/structured-notes) | 用章节、来源定位和引文组织笔记 |
| [nanopub](skills/nanopub) | 构建离线、未签名的 RDF 纳米出版物，并回读命名图 |
| [literature-graph](skills/literature-graph) | 按证据定义实体和有向边，保留歧义并查询证据链 |

## 写作类

| Skill | 核心功能 |
|---|---|
| [academic-polish](skills/academic-polish) | 受控编辑与变更日志：最小改动润色，保留事实、数字与引用 |
| [citation-manager](skills/citation-manager) | 引用绑定、去重、按序重排、DOI 核验与 GB/T 7714/APA 渲染 |
| [related-work](skills/related-work) | 从来源材料写引言与相关工作，比较矩阵约束论断和贡献表述 |
| [methods-repro](skills/methods-repro) | 从代码、配置与日志写方法与实验章节，参数溯源与可复现清单 |
| [latex-build](skills/latex-build) | LaTeX 工程编辑、多轮真编译、按日志定位修复与版面检查 |

各 Skill 标明依赖、开源来源和适用范围；示例只用于复跑接口。六个含 `tests/` 的技能可用 `python -m unittest discover -s tests` 复跑。