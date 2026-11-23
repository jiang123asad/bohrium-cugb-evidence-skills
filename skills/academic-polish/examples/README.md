# 最小调用示例（示例，非正式案例）

输入：`input/before.txt` 与 `input/after.txt`；润色把 “may decline” 改为 “may decrease”，保留 may 与 5%。

```bash
S=/skills/weekly-skill/scripts
python "$S/core.py" compare examples/input/before.txt examples/input/after.txt
```

实际输出见 `output/compare.json`：missing/added 均为空，数值守恒。
若改成 “decreased by 5%”（去掉 may），数字虽守恒，限定强度变化仍须按 SKILL.md 第二遍信号词复核拦截。
