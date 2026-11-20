# 最小调用示例（示例，非正式案例）

输入：`input/alignment.json`，两段原文与译文，其中 S01 故意把 `3.2%` 误译为 `32%`，术语表含 remote sensing→遥感。

```bash
S=/skills/weekly-skill/scripts
python "$S/structured.py" translation examples/input/alignment.json qa.json
```

实际输出见 `output/qa.json`：S01 被标 `protected_token_difference`，missing `3.2%`、added `32%`；
S02 术语命中且无差异。工具只报候选，交付前仍需逐段复核否定、因果与指代。
