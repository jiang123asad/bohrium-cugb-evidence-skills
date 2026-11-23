# 最小调用示例（示例，非正式案例）

输入：`input/claims.json`，两条合成断言，各带示意证据与元数据；A01 为附条件文本断言，A02 额外指定 URI 主语/谓词与数值字面量宾语，写入实际 RDF 三元组。示例中的材料名与证据为接口演示，不是真实论文。

```bash
S=./scripts # 在本 Skill 根目录运行
python "$S/structured.py" nanopub examples/input/claims.json nanopub-index.json --trig assertions.trig.txt
```

实际输出见 `output/nanopub-index.json` 与 `output/assertions.trig.txt`（rdflib 7.6.0 实际运行）：
每条断言独立 Head/Assertion/Provenance/PublicationInfo 共 8 张命名图，TriG 序列化后回读校验一致；
published=false，URN 为本地草稿标识，不声称签名或正式注册。

## 发布前结构预检（示例）

`input/nanopub-check.json` 以 `nanocheck.py` 的 `np` 结构列出 Head/Assertion/Provenance/PublicationInfo 四图内容；运行
`python "$S/nanocheck.py" examples/input/nanopub-check.json examples/output/nanocheck-report.json` 得到 `output/nanocheck-report.json`：
11 项检查全部 pass，publishable=true，哈希输入 2 行。正式发布前须以同一命令核对真实四图，publishable=false 即阻断。
