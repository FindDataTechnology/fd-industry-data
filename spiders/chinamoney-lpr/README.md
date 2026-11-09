# chinamoney-lpr —— 中国货币网 LPR（贷款市场报价利率）

新源接入档案（批次六·银行业，change `add-banking-industry-sources` 任务 3.2）。

## 端点（本轮从站点前端 fragment 实读，非猜测）

前端页面 `/chinese/bklpr/` 的数据段是 fragment
`/r/cms/chinese/chinamoney/html/currency/lpr-his-cn.html`，其 JS 明确：

```js
url: BK_URL + 'cm-u-bk-currency/LprHis?lang=CN&strStartDate=' + start + '&strEndDate=' + end
type: "POST"
```

| 项 | 值 |
|---|---|
| 端点 | `/ags/ms/cm-u-bk-currency/LprHis`（模块是 `cm-u-bk-currency`，**不是** `cm-u-bk-lpr`） |
| 方法 | POST（body 空，参数在 query string） |
| 参数 | `lang=CN&strStartDate=YYYY-MM-DD&strEndDate=YYYY-MM-DD`（**参数名独此一家**：别处是 startDate） |
| 行位置 | **响应顶层 `records`**，不是 `data.records` |
| 行键 | `showDateCN`（YYYY-MM-DD）+ `1Y` / `5Y` |

## 三条实测坑（本轮全部踩到）

1. **查错层级恒空**：先按 `data.records` 读 → 两次探测都是 0 条；改读顶层
   `records` 立刻拿到数据。站点把「列定义」放 `data`、把「行」放顶层，与
   Shibor/曲线两个模块同构。
2. **窗口 >1 年静默返空**：`strStartDate=2024-01-01&strEndDate=2026-11-07`
   → `records: []`（HTTP 200、无报错）；`2026-01-01..2026-11-07` → 10 条。
   故全史必须**按年分片**（`FIRST_YEAR=2013`，LPR 集中报价机制起点）。
3. **假 200**：频控返 HTTP 200 + HTML 403 页 → 先 `json.loads`、再验顶层有
   `records`，两者任一不过即退避重试一次（60s），仍不过则放弃该片并留痕。
   端点间隔恒 3.5s（同站纪律 ≥3-5s）。

## 实测取数证据（2026-11-07 首爬）

`limit=80` → **80 行 / 40 个报价期次 / 两期限齐 / 耗时 ~15s（4 个年度分片）**：

```
2026-10-20  LPR5Y 3.5   LPR1Y 3.0
2026-09-20  LPR5Y 3.5   LPR1Y 3.0
2026-08-20  LPR5Y 3.5   LPR1Y 3.0
年度分片：2026→10 期、2025→12 期、2024→12 期、2023→12 期（每月 20 日一期，齐）
```

LPR 每月 20 日报价（遇节假日顺延），故 `data_period: day` + 日频巡检是
「抓发布」而不是「抓变化」——报价不变时同日重复取到的值幂等。

## 失败策略

单年度分片失败 → stderr 留痕并继续（部分历史缺失可见于日志）；全部年度为空 →
`RuntimeError`（绝不静默回空）；空值/`---` 不出行；`limit` 真实生效。
