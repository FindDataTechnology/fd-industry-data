# drill-heal — 自愈演练夹具（结构层故障注入）

**用途**：验证 spider-self-heal-l2 的 5.2 端到端演练——一个「站点正常返回 200、
解析面失配」的结构层坏源。**不是真实数据源，演练结束后归档。**

- 故障：`spider.py` 用不存在的标记（`<h1x-nonexistent-marker>`）抽取 `<title>`，
  使 `title` 恒为空、`title_brand` 恒为空串。
- 正确修复：恢复 `<title>...</title>` 提取；`title_brand` 在标题包含
  `Shanghai Metals Market (SMM)` 时输出 `SMM`。
- 断言：`golden/001-drill-heal.json`（`min_rows=1` + `title_brand=SMM`）——
  修复前重放红，修复后绿；验证链见对应工单的 `verify` 字段。