# lh-ratings 接入检查清单（批次六，2026-11-08）

- [x] 目录名 = manifest `name`（`lh-ratings`；入口 `run_lh_ratings`）
- [x] `spider.py` 提供 `run_lh_ratings(limit=, keyword=, max_pages=) -> list[dict]`，纯标准库
- [x] **分页参数实读**：`?p=N`（页脚 `end_page` + 跳转脚本双重确认，不是 `?page=`）
- [x] **未过滤总页数实读**：`/lists/111.html` 的 `end_page=2927`、12 条/页
- [x] **`title=银行` 服务端过滤实测**：`end_page` 2927 → **7**；7 页 78 条
      （前 6 页各 12 + 末页 6），证明过滤在服务端而非前端隐藏
- [x] **过滤口径留痕**：`title` 是标题子串匹配，会命中「银行**间**票据」等
      非银行发行人（实测 1 条），不做本地二次收窄（README「过滤口径」）
- [x] **越界页行为实测**：`p=8/9/9999` 均 HTTP 200 + 0 条 → 空页即停，不硬编码页数
- [x] **条目结构全量核对**：7 页 78/78 条均为 2 个 `h2` + 4 个 `p`，链接 78/78 为 PDF
- [x] **发行人归一化**：公司名后缀抽取 + 公告类剥「联合资信…关于/上调/将」前缀
      （「联合资信关于上调广西北部湾银行…」→ 广西北部湾银行股份有限公司）
- [x] **同 URL 去重**：78 条中 8 个重复 URL → 70 行；按 URL 去重，不按(发行人,年份)
- [x] **增量口径**：默认只巡前 5 页（60 条 → 去重 55 行），不回扫 2927 页；
      理由（成本/礼貌/前 N 页已覆盖新增）与历史回补方式写入 README
- [x] `data_period: irregular` + 理由（跟踪评级按发行人年度/事件驱动，无固定周期）
- [x] `schedule: "33 10 * * 3"` 逐字按批次六排期（每周三巡检）
- [x] manifest 字段齐全；columns 14 键与输出行一一对应；三个声明参数均可达
- [x] 列表是 div/li 非表格，字符串/正则解析（不涉及 `tabular`，不触发 gate）
- [x] 失败策略：单页失败留痕续跑、空页终止、全空 `RuntimeError`、条目版式异常
      留痕跳过、`limit`/`keyword`/`max_pages` 均真实生效
- [x] golden 锚较早稳定条目 + 公告类条目 + 常量字段，`max_pages=8` 留滚动余量
- [x] 本地自检：`validate_manifests` 0 违规 / `conformance_gate` 0 违规 /
      golden schema `[]` + replay `ok=True rows=70 diffs=[]`
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：验证 crawl_runs 记录与首次 55 行落库
