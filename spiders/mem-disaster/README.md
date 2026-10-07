# mem-disaster —— 应急管理部月度灾情通报

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-mem-disaster-eccf1032.yaml`
（`kind=generate`，brief 侦察簿 W3）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 栏目列表 | `GET https://www.mem.gov.cn/gk/tjsj/` | HTML 首页内联每月通报链接（新→旧，约 19 个月度通报；翻页页为 JS 渲染壳，仅取首页） |
| 通报正文 | `/xw/yjglbgzdt/<yyyymm>/t*.shtml` | 文章页，正文含全国汇总句（受灾/死亡失踪/直接经济损失） |

入口：`run_mem_disaster(limit=100, month=None) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib / gzip / re`，无新依赖。无鉴权；固定 UA + Referer。

## 参数白名单（只发实测过的参数）

- 无请求参数（GET 栏目页与文章页）；函数参数 `month="YYYY-MM"` 用于单月过滤
  （golden 用），`limit` 控制解析篇数。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `month` | 标题 `发布(\d{4})年(\d{1,2})月全国自然灾害情况` | `YYYY-MM`；半年/季度/全年/十大特刊天然不匹配被排除 |
| `title` / `publish_time` | 列表项 `<a>标题<span>时间</span></a>` | 空白压缩后的标题；上游发布时间 |
| `affected_person` | 汇总句 `共造成…([\d\.]+)万人次…受灾` | **万人次**原值（如 `1672.9`） |
| `deaths_missing` | 汇总句 `死亡失踪([\d\.]+)人` | **人**（如 `318`） |
| `economic_loss` | 汇总句 `直接经济损失…([\d\.]+)亿元`；万元值 ÷1e4 归一 | **亿元**（如 `571.5`；「近4800万元」→ `0.48`） |
| `summary` | 正文第一个同时含「受灾」与「经济损失」的句段 | 指标溯源原句（分灾种明细在汇总句之后，不取） |
| `url` / `source` / `scraped_at` | `urljoin(栏目页, href)` / 常量 / 本地 | 行级可追溯；`mem-disaster`；UTC ISO8601 |

## 历史深度（2026-10-06 实测）

首页约 **19 个月度通报**（实测覆盖 2026-08 回溯至 2025-02；2025-06、2025-09 无独立月报，
只有半年/前三季度特刊——该两月无月度行属上游事实，不是取数缺陷）。更早历史在 JS 翻页
壳之后，静态取不到，留档不取。

实测样例行（2026-07 通报，发布 2026-08-14）：

```
{"month": "2026-07", "title": "国家防灾减灾救灾委员会办公室应急管理部发布2026年7月全国自然灾害情况",
 "publish_time": "2026-08-14 16:05", "affected_person": 1672.9, "deaths_missing": 318.0,
 "economic_loss": 571.5, "summary": "据初步统计，各种自然灾害共造成全国1672.9万人次不同程度受灾，…直接经济损失571.5亿元",
 "url": "https://www.mem.gov.cn/xw/yjglbgzdt/202608/t20260814_680668.shtml", "source": "mem-disaster", …}
```

## 坑位与容错（brief.notes 逐条落实）

- **响应 gzip**：部分文章页（实测 2025 年旧文）**不发 `Accept-Encoding` 也直接回 gzip**，
  统一按魔数 `1f 8b` 嗅探 + 标准库 `gzip` 解压，两种形态都兼容。
- **月中发布——当月未出属正常容错**：默认只解析列表上有的通报；`month` 指定月不在首页
  （当月未发布或已翻出首页）→ 结构化 `RuntimeError`（确定性红，绝不造数）。
- **失败即红**：列表页失败 / 无月度条目 / 全部文章解析失败 → 抛 `RuntimeError`；
  单篇失败只跳过并在异常信息里留痕。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。
- golden 时效：锚 `2026-07` 通报定版值；约 1.5 年后该月翻出首页时 golden 会确定性红，
  属预期行为——人工换锚到新的固定历史月即可（CHECKLIST 已留步）。

## 自检

```bash
python3 spiders/mem-disaster/spider.py          # 单页冒烟，打印 2 行
python3 scripts/check_manifest_commands.py
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-mem-disaster-eccf1032.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-mem-disaster.json`（`params: {limit: 3, month: "2026-07"}`）只锚**固定历史月
通报数值**：`month == 2026-07`、`affected_person == 1672.9`、`deaths_missing == 318`、
`economic_loss == 571.5`、`source == mem-disaster`，以及 `min_rows: 1`。
**绝不锚**当月值与抓取时间；`scraped_at`/`timestamp` 进 `whitelist_fields`。
