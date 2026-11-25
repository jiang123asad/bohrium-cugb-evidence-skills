# Nanopublication · Harbor 交付说明

题面：[查看官方题目](https://play.bohrium.com/challenge/product-arena-09-0e8befd4)。本目录是 Skill 源码，兼容 Harbor 的 `/app/submission/`；可直接从 [SKILL.md](SKILL.md) 查看入口、方法、依赖回退和适用范围。

1. 平台将本 Skill 安装到 `/skills/weekly-skill/`。在本地试用时，进入本目录，以 Python 3.10+ 按 [非赛例调用示例](examples/README.md) 运行；按需安装 [requirements.txt](requirements.txt) 中相应 Python 依赖。Harbor 运行优先用实际预装工具，根据 `environment/resources.yaml` 查可用资源。
2. 正式运行从 `/input/case.json`、实际 `task.md` 和原件取得本次任务；中间文件写到 `/app/work`。成品、证据定位、状态和不确定性写到 `/app/output`，其中 `result.json` 必须保留原 `case_id` 并满足当次 `output_contract`。启用编译或 Office 渲染时核验真实成品。未提供材料、原文未报告、证据冲突、工具失败分开记。
3. 本题验收：为每条可独立断言保留断言、来源和发布信息图及证据位置；从实际产物回读图及关联。签名或公开发布状态仅在实际完成后报告。 运行 `scripts/core.py check` 的用法见入口；按任务要求另核对主结果和实际产物。

`examples/` 仅供演示接口；正式输入、白板、结果和评测均由平台在运行时提供。本题可提交公开 GitHub 链接；只有核验到与本包一致的公开版本后才生成 `GITHUB_URL.txt`。

## 版本与维护

- 版本：2026-11-25 修订（v6.1）；源码在本仓库 `skills/nanopub/` 持续维护。
- 本次修订：补充“异常与降级处理”（扫描件与受限文档、双栏错序、超长文、非中文、工具失败）；运行主流程与示例未变。
- 反馈：请在本仓库 Issues 提交，附 `result.json` 的 status 与 verification 输出，便于定位。
