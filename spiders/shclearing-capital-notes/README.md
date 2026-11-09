# shclearing-capital-notes —— 上清所 商业银行资本工具（二级资本债券 / 无固定期限资本债券）

新源接入档案（批次六·银行业，change `add-banking-industry-sources` 任务 4.4）。

## 端点（2026-11-08 实读）

TRS 公告检索（一个端点，按 `channelId` 分栏目）：

```
POST https://www.shclearing.com.cn/shchapp/web/disclosureForTrsServer/search
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
X-Requested-With: XMLHttpRequest
Referer: https://www.shclearing.com.cn/xxpl/jyltysgg/   # ch166 流通
         https://www.shclearing.com.cn/xxpl/fxpl/        # ch144 发行
User-Agent: <浏览器 UA>
body: channelId=166&keyword=二级资本债券&start=0&limit=50
```

**无需 Cookie / JSESSIONID**：仅 UA + 同栏目 Referer 即返回 JSON（实测 HTTP 200）。
这与上清所月报 `monthListPage` 的「JSESSIONID + Referer 三重门」不同，属另一套 TRS
检索通道。

| channelId | 栏目 | 全栏目条数（pageTotal） |
|---|---|---|
| 166 | 交易流通要素公告（**流通要素**） | 369,062 |
| 144 | 发行披露（**发行公告**） | 521,622 |

## 参数（实读，不是猜测）

| 参数 | 语义 | 实测 |
|---|---|---|
| `channelId` | 栏目：`166` 流通 / `144` 发行 | 两值均返回 `success:true` |
| `keyword` | 标题全文检索 | `二级资本债券` → ch166 `totalProperty=390`、ch144 `totalProperty=713` |
| `start` | **偏移量**（ExtJS 口径） | `limit=10&start=10` 首条 2026-09-24；`start=20` 首条 2026-08-21（与 `start=0` 的 2026-11-05 不同） |
| `limit` | **本页条数**（ExtJS 口径） | `limit=200` 一次返回 200 条 |

坑：`pageNo` / `pageSize` / `page` / `curPage` **全部被忽略**——传 `pageNo=2&pageSize=10`
仍返回默认 20 条且首条不变；只有 `start` + `limit` 组合生效。响应走 ExtJS 命名
（`root` / `totalProperty`），据此反推并实测确认。

响应结构：

```json
{"success": true,
 "root": [{"title": "…流通要素公告", "url": "https://www.shclearing.com.cn/xxpl/jyltysgg/jrz_547/202610/t20261026_1891498.html",
           "publishDate": "2026-10-26", "channelId": "172"}],
 "totalProperty": 390, "licenceCode": 2197822014.1794069132.1}
```

注意 `root[].channelId` 是文章子栏目（如 172），**不是**检索用的一级栏目 166/144，
落库以本单元 `channel` 列（166/144 语义值）为准。

## 检索词口径

上清所标题里永续债写作「**无固定期限资本债券**」（搜「永续债」0 条，侦察簿已记）。
默认两个词：`二级资本债券`、`无固定期限资本债券`；`keyword` 给定时**只**抓该词
（不再叠加默认词）。

## 详情页 URL 形态与字段抽取

- 流通（ch166）：`https://www.shclearing.com.cn/xxpl/jyltysgg/jrz_547/YYYYMM/tYYYYMMDD_<id>.html`
- 发行（ch144）：`https://www.shclearing.com.cn/xxpl/fxpl/jrz/YYYYMM/tYYYYMMDD_<id>.html`

详情页含「基本信息 / 其他信息」两列 label-value 表。**走共享层**
`fd_industry_data.tabular` 的 HTML 通道：`tabular.read_grid(payload, table=i)` 逐表
读取并按两列切分，不手写表格解析（gate 的 `inline-tabular-parser` 规则）。

字段名先去掉单位括注再匹配（`发行（创设）总额（亿元）` → `发行总额`），
同义标签按优先级兜底：

| 输出列 | 上游标签 | 清洗 |
|---|---|---|
| `bond_code` | 产品代码 | 原样文本（保留前导零） |
| `bond_short_name` | 产品简称 | 原样 |
| `bond_name` | 产品全称 | 原样 |
| `issue_amount` | 发行（创设）总额（亿元）/ 计划发行总额 / 实际发行总额 | 去千分位逗号 → float；`amount_unit` 固定 `亿元` |
| `term` / `term_years` | 期限 | 原样 `10年`；正则 `(\d+(?:\.\d+)?)年` → `10.0` |
| `coupon_rate` | 票面年利率（%）/ 票面利率 | 去 `%` → float；`coupon_unit` 固定 `%` |
| `product_rating` | 产品评级 / 债项评级 | 原样 |
| `issuer_rating` | 主体评级 | 原样 |
| `rating_agency` | 产品评级机构 | 原样 |
| `issue_date` | 发行（创设）日 | 原样 |
| `circulation_date` | 流通日 | 原样 |
| `circulation_end_date` | 流通结束日 | 原样 |
| `maturity_date` | 到期（兑付）日 | 原样 |
| `interest_method` | 计息方式 | 原样 |

缺失口径：**数值缺失写 `null`，文本缺失写 `null`，绝不写 0**；上游 `-` / `--` /
`—` / `…` / 空 一律视为缺测。

## 实测样例行（26无锡农商行二级资本债01）

golden 复算命中的真实字段组（`keyword=无锡农村商业银行`，channel 166）：

```json
{"channel":"circulation","channel_label":"交易流通要素公告",
 "bond_name":"无锡农村商业银行股份有限公司2026年二级资本债券（第一期）",
 "bond_short_name":"26无锡农商行二级资本债01","bond_code":"232680049",
 "issue_amount":5.0,"amount_unit":"亿元","term":"10年","term_years":10.0,
 "coupon_rate":1.72,"coupon_unit":"%","product_rating":"AA","issuer_rating":"AA+",
 "rating_agency":"联合资信","issue_date":"2026-10-22","circulation_date":"2026-10-27",
 "circulation_end_date":"2036-10-24","maturity_date":"2036-10-26",
 "interest_method":"附息固定"}
```

原锚「26无锡农商行二级资本债01」**可得**（2026-10-26 公告，检索第 1 条），无需替换。

## ch144（发行）的真实形态（重要）

实测抽样 ch144「二级资本债券」前 8 条详情页（申购区间与申购提示性说明 / 发行公告），
**8/8 都没有 label-value 要素表**，正文是自由文本或附件链接。因此：

- ch144 仍接入检索（覆盖发行披露栏目），但详情页解析不到要素表时出**公告级行**：
  `bond_name = 公告标题`，`publish_date` / `detail_url` 真实，其余要素字段为 `null`；
- 代码/总额/期限/票面利率/评级这些结构化字段，来自 ch166 **流通要素公告**页；
- 代码不猜值：ch144 行不会被伪造成带数值的「发行要素」记录。

两个栏目按预算分配：ch166 取 `limit//2`，ch144 用剩余额度——否则 ch166 一个词就
有 390 条，会把 `limit` 吃满、ch144 永不触达。

## 实测取数与耗时

| 调用 | 结果 |
|---|---|
| golden replay（`limit=10, keyword=无锡农村商业银行`） | `ok=True rows=5 diffs=[]`（golden 定稿于 ch144 公告级行补齐之前，锚在 ch166 前 5 条内必然命中） |
| `_search` ch166 `二级资本债券` | `totalProperty=390`，`limit=200` 一次可取 200 条 |
| `_search` ch144 `二级资本债券` | `totalProperty=713` |

首爬说明：修复前用默认关键词跑 `limit=20` 时，ch144 详情页无结构化字段导致
`produced` 长期为 0，分页一路走到 71 万条库（请求超时被中止）。已修：每个
（栏目, 关键词）最多 `MAX_PAGES=12` 页，且 ch144 无要素表时出公告级行，`produced`
正常增长。修复后未再整跑（按交付指令停止站点请求）——**首次调度试跑仍需人工确认
行数**，预期 = ch166 10 行 + ch144 10 行（`limit=20`）。

## 失败策略

- 检索失败（网络/非 JSON/`success:false`）→ 留痕并换下一个关键词；两栏目两词全空
  → `RuntimeError`（不返回空壳冒充成功）。
- 单条详情失败 → 留痕跳过，其余继续。
- 详情页命中但无任何要素字段：ch166 → 不出行（不是要素公告）；ch144 → 公告级行。
- 分页上限 `MAX_PAGES=12`，防止无匹配时遍历整库。
- 网络礼节：**每次请求间隔 ≥1s**（`SLEEP_S=1.0`，检索与详情统一）。

## 为什么 golden 用发行人关键词

默认关键词下 ch166 命中 390 条、按发布时间倒序，锚债券会随时间被新公告越推越远，
golden 的 `limit` 一旦固定就会失效。改用发行人名 `无锡农村商业银行` 作为 golden
参数：它是标题子串、命中集合稳定且很小，锚债券长期位于第 1 条。`keyword` 已声明
在入口签名里，给定即真实生效（不叠加默认词），因此这不是 golden 专用后门。
