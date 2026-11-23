# 最小调用示例（示例，非正式案例）

输入：`input/project/main.tex`，含编号公式与 \ref 交叉引用的最小工程（仅用内核宏包）。

```bash
S=./scripts # 在本 Skill 根目录运行
python "$S/texcheck.py" examples/input/project main.tex build-report work-scratch --engine xelatex --build-base root
```

实际输出见 `output/build-report/compile-report.json` 与 `compile-report.txt`
（Tectonic 0.15.0 缓存模式实际运行）：built_pdf=true，多轮编译后引用收敛，unresolved_final_log=false，
diagnostics 仅含一次正常的 aux 重跑提示。compiled.pdf 为二进制产物，运行时生成并渲染检查，
按平台纯文本资源约定不随包分发。缺引擎时脚本返回失败并保留真实日志，不捏造编译状态。
