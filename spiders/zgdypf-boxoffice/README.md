# zgdypf-boxoffice（专资办电影票房）接入档案

国家电影专资办 数据中心（zgdypw.cn）电影票房日榜/周榜。旧 endata.com.cn 已易主艺恩，勿用。

- 入口函数：`run_zgdypf_boxoffice(limit: int = 100) -> list[dict]`
- frequency：daily（默认口径 `days=2, weeks=1`：今日+昨日日榜 + 本周周一锚定周榜，行数截断到 limit）
- 行 schema：见 manifest `functions[0].columns`（period/scope/film_code/film_name/ranking/box_office_wan/…/scraped_at/source_url）
- 大盘行：`film_name="大盘"`、`ranking=0`、`film_code=""`，与影片行同表混排，用 `film_name` 区分

## 端点与鉴权

免鉴权，直连（国内源，禁走代理；实测直连可用，全程未配 proxy）。

| 用途 | 端点 |
|---|---|
| 日榜 | `GET https://zgdypf.zgdypw.cn/getDayData?withSvcFee=1&date=YYYY-MM-DD&dateType=0` |
| 周榜 | `GET https://zgdypf.zgdypw.cn/getPeriodData?withSvcFee=1&date=YYYY-MM-DD&dateType=1` |
| 今日快照（未采用） | `GET https://www.zgdypw.cn/data/searchDayBoxOffice.json`（无 date 参数，仅最新营业日，含场次/人次/影城数绝对值；因无法回溯历史，本爬虫未用） |

响应为纯 JSON：`{"list": [影片行...], "nationalSales": {"salesDesc": {"unit": "亿|万", "value": "..."}, "splitSalesDesc": {...}}}`。

## 参数白名单（硬约束，只发实测有效参数）

- 日榜：`withSvcFee=1`、`date=YYYY-MM-DD`、`dateType=0`
- 周榜：`withSvcFee=1`、`date=YYYY-MM-DD（必须锚定周一）`、`dateType=1`
  - 实测：周榜 date 传非周一（如 2024-02-06）→ `200 {"list":[]}` 空列表； spider 内部强制取 `_monday_of(end)`，绝不外发非周一日期
  - 实测：周榜 withSvcFee=0 / 缺省时返回体相同（周榜疑似固定含服务费口径），仍按契约固定发 `withSvcFee=1`
- 请求头：chrome120 impersonation（scrapling FetcherSession）+ `Referer: https://zgdypf.zgdypw.cn/`

## 容错规则

- **HTTP 204 / 空响应**（当日无数据：未来日期、2017-01-01 之前、当日数据未生成）→ 记日志后跳过，不报错、不重试
- **5xx / 非 JSON** → 记日志后跳过；不连环重试、不尝试绕反爬
- **`"<0.1"` 字符串值**（尾部长尾影片票房）→ 统一清洗函数按区间中值记 `0.05`（万元）写入数值列，绝不写脏 float；解析失败置 None，整行不写
- 影片行 `name` 为空或票房解析失败 → 该行不写

## 口径

- 取**含服务费口径**（`withSvcFee=1`，与 `nationalSales.salesDesc` 大盘同口径）；分账口径单列 `box_office_split_wan` 供对照
- 实测同日 www.zgdypw.cn 快照与 zgdypf 端点数值有小幅修订差（约 0.2%~0.7%，快照时点不同所致），以 zgdypf 端点为准（唯一取数来源）

## 历史深度

- 日榜实测可回溯至 **2017-01-01**（2017-01-01 取数成功，61KB 载荷；2016-12-31 返回 HTTP 204）
- **本批次首爬只回补 12 个月**，更深历史不做（需要时再单独回补）
- 周榜实测 2024-02-05（周一）可取

## 真实取数证据

取数时间：**2026-10-02 04:14 +08:00**，直连（【直连=通/代理=未用（国内源禁代理）】），执行 `spiders/zgdypf-boxoffice/spider.py` 实际函数。

### 门槛 1：最近 7 天日榜求和 vs 大盘（差 <2%）— PASS

| 日期 | 影片行求和(万) | 大盘(万) | 差 |
|---|---|---|---|
| 2026-09-26 | 9686.77 | 9728.75 | 0.432% |
| 2026-09-27 | 5706.93 | 5748.16 | 0.717% |
| 2026-09-28 | 2239.49 | 2269.28 | 1.313% |
| 2026-09-29 | 2180.48 | 2195.90 | 0.702% |
| 2026-09-30 | 3348.23 | 3363.84 | 0.464% |
| 2026-10-01 | 18070.55 | 18070.07 | 0.003% |
| 2026-10-02 | 3088.15 | 3087.96 | 0.006% |
| **7 天合计** | **44320.60** | **44463.96** | **0.322%** |

单日最大差 1.313%，7 天合计差 0.322%，均 <2%。

### 门槛 2：历史回取 2024-02-10（春节档）— PASS

- 返回 53 行（1 大盘 + 52 影片）；日大盘 **134700 万（13.47 亿）**，分账 121800 万
- 当日榜首：**飞驰人生2 42430.73 万**（热辣滚烫 41845.94 万次之）
- 影片行求和 134781.61 万，与大盘差 0.06%

### 周榜抽样（2024-02-05 周一）

- 222 行（1 大盘 + 221 影片）；周大盘 259800 万（25.98 亿）；周榜首 热辣滚烫 78910.89 万（avg_price 50.54 元）

### 样例行 JSON（大盘行，2026-10-02）

```json
{
  "period": "2026-10-02",
  "scope": "day",
  "film_code": "",
  "film_name": "大盘",
  "ranking": 0,
  "box_office_wan": 3087.96,
  "box_office_split_wan": 2738.69,
  "box_office_rate": "",
  "session_rate": "",
  "seat_rate": "",
  "online_sales_rate": "",
  "release_days": null,
  "total_sales_desc": "",
  "avg_price": null,
  "avg_attendance": null,
  "release_date": "",
  "scraped_at": "2026-10-02T04:14:35+08:00",
  "source_url": "https://zgdypf.zgdypw.cn/getDayData?withSvcFee=1&date=2026-10-02&dateType=0"
}
```

影片行样例（2024-02-10 榜尾，展示 `"<0.1"` → 0.05 清洗）：

```json
{"period": "2024-02-10", "scope": "day", "film_code": "00104052022", "film_name": "开山人", "ranking": 52, "box_office_wan": 0.05, "box_office_split_wan": 0.05, "box_office_rate": "0.00%", "session_rate": "0.00%", "seat_rate": "0.00%", "online_sales_rate": "", "release_days": 75, "total_sales_desc": "3008.82万", "avg_price": null, "avg_attendance": null, "release_date": "", "scraped_at": "2026-10-02T04:14:50+08:00", "source_url": "https://zgdypf.zgdypw.cn/getDayData?withSvcFee=1&date=2024-02-10&dateType=0"}
```

总条数口径：默认 `run_zgdypf_boxoffice()`（limit=100）返回 ≤100 行；`days=7` 全量返回 7×(1 大盘+~60~130 影片) 行。

## 遗留风险

- 日榜影片行无场次/人次/影城数绝对值（端点只给占比），如需绝对量需另接 www.zgdypw.cn 今日快照端点（无 date 参数，仅当日）
- 9-28 单日差 1.313% 略高于其他日（小票房日长尾 `"<0.1"` 影片多、按 0.05 记所致），仍满足 <2% 门槛
- 周榜在周一凌晨（新周期刚开始）可能返回空列表，按容错规则静默跳过，次日运行自然补上

## 首爬回补口径（2026-10-02 批次）
点亮前首爬窗口临时调大：`DEFAULT_DAYS=370 / DEFAULT_WEEKS=53`（滚动 12 个月一次性回补，crawl_runs 留痕）。
首爬完成后回调稳态 `days=3, weeks=2`（今日+昨日+营业日 catch-up + 本周/上周周榜）。历史深度：日榜可回溯 2017-01-01，更深年份按需再议（design 决策 4：不做一次性全量回补）。
