# 文献文档解析 · Harbor 交付说明

题面：[查看官方题目](https://play.bohrium.com/challenge/product-arena-08-3027d350)。本目录是 Skill 源码，兼容 Harbor 的 `/app/submission/`；可直接从 [SKILL.md](SKILL.md) 查看入口、方法、依赖回退和适用范围。

1. 平台将本 Skill 安装到 `/skills/weekly-skill/`。在本地试用时，进入本目录，以 Python 3.10+ 按 [非赛例调用示例](examples/README.md) 运行；按需安装 [requirements.txt](requirements.txt) 中相应 Python 依赖。Harbor 运行优先用实际预装工具，根据 `environment/resources.yaml` 查可用资源。
2. 正式运行从 `/input/case.json`、实际 `task.md` 和原件取得本次任务；中间文件写到 `/app/work`。成品、证据定位、状态和不确定性写到 `/app/output`，其中 `result.json` 必须保留原 `case_id` 并满足当次 `output_contract`。启用编译或 Office 渲染时核验真实成品。未提供材料、原文未报告、证据冲突、工具失败分开记。
3. 本题验收：先按指定物理页逐页核对双栏顺序、跨栏标题和正文覆盖，再按题面交结构化内容。表格抽查表头与首尾行，公式记录编号及原页，无法辨认处保留页区与原因。 运行 `scripts/core.py check` 的用法见入口；按任务要求另核对主结果和实际产物。

`examples/` 仅供演示接口；正式输入、白板、结果和评测均由平台在运行时提供。本题可提交公开 GitHub 链接；只有核验到与本包一致的公开版本后才生成 `GITHUB_URL.txt`。
