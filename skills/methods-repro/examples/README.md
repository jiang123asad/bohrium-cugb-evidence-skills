# 最小调用示例（示例，非正式案例）

输入：`input/facts.json`，同一运行 r1 的 learning_rate 在 config.yaml 为 0.01、train.log 为 0.005。

```bash
S=./scripts # 在本 Skill 根目录运行
python "$S/structured.py" reconcile examples/input/facts.json conflicts.json
```

实际输出见 `output/conflicts.json`：review_conflicts 列出 r1/learning_rate 的两个观测及各自来源定位。
脚本只标记冲突，不判定权威来源；定稿取舍与覆盖记录由作者/Agent 按证据说明。
