# NBS Stats Spider - 国家统计局综合数据

Scrapling spider for comprehensive data from the National Bureau of Statistics of China (国家统计局).

## Data Coverage

| Category | Indicators | Frequency |
|----------|-----------|-----------|
| **GDP** | Quarterly, Annual, By Province | Q/A |
| **Price Indices** | CPI, PPI | Monthly |
| **Industrial** | Production, PMI | Monthly |
| **Population** | Annual population | Annual |
| **Employment** | Urban employment | Monthly |
| **Circulation Prices** | 流通领域重要生产资料市场价格（~50品种） | Ten-day (旬度) |

## Data Sources

- **Primary**: `https://data.stats.gov.cn/easyquery.htm` (NBS API)
- **Circulation prices**: `https://www.stats.gov.cn/sj/zxfb/` (最新发布 article HTML tables)
- **Fallback**: akshare library (wraps NBS data)

## Authentication

**No authentication required.** All data is publicly available through the NBS easyquery API.

## Anti-Bot Measures

- Chrome browser impersonation via `curl_cffi`
- Proper `Referer` and `X-Requested-With` headers
- 2-second download delay
- Timestamp cache-busting (`k1` parameter)

## Quick Start

```bash
# Install dependencies
pip install "scrapling[all]>=0.4.7"
scrapling install --force

# Run the spider
cd spiders/nbs-stats
python spider.py
```

## Usage

### Fetch All Categories

```python
import asyncio
from spider import get_nbs_data

results = asyncio.run(get_nbs_data(
    categories=["gdp", "price_indices", "industrial", "population", "employment"],
    start_year=2015,
))
```

### Fetch Specific Category

```python
from spider import get_gdp_data, get_price_indices

gdp = get_gdp_data(start_year=2020)
cpi_ppi = get_price_indices(start_year=2020)
```

### Fetch Circulation-Area Producer Goods Prices (旬度)

```python
from spider import get_circulation_prices

rows = get_circulation_prices(limit=50)  # newest issue first
```

### Using the Spider Class

```python
from spider import NbsStatsSpider

spider = NbsStatsSpider()
spider.categories = ["gdp", "price_indices"]
spider.start_year = 2015
result = spider.start()
result.items.to_json("nbs_data.json")
```

## 流通领域重要生产资料市场价格（旬度）— 接入档案

`get_circulation_prices(limit: int = 100) -> list[dict]`，2026-10 新增的独立解析路径，
与 easyquery hgjd/hgyd 代码零复用、零改动。

### 端点与流程

1. 列表页 `https://www.stats.gov.cn/sj/zxfb/`（不足时按 `index_1.html`、`index_2.html`
   顺序翻页，最多 3 页），按标题匹配「流通领域重要生产资料市场价格」取文章链接（新→旧）。
2. 文章页（如 `…/sj/zxfb/202609/t20260923_1965403.html`）解析内联 HTML 表格。
3. 列按实测表头动态映射：`产品名称 / 单位 / 本期价格（元）/ 比上期价格涨跌（元）/ 涨跌幅（%）`。
   若未来表头增加「同比」列会自动映射到 `yoy_pct`（当前无此列，恒为 null）。

### 鉴权与参数白名单

- 无鉴权、无登录。**只发实测有效参数**：请求仅带 `Accept`、`Accept-Language` 头，
  无任何多余 query 参数。HTTP 一律走 `scrapling.fetchers.FetcherSession`
  （`impersonate="chrome120", timeout=25, verify=False`）。
- 国内源：**直连，从不走代理**。

### 容错规则

- 列表/文章 5xx、空响应、响应过短 → 记录 warning 后跳过，**不重试、不绕反爬**。
- 表格单元格「—」「－」空串等 → `None`；无品名且无任何数值的行不写（不留脏行）。
- 文章页同一表格可能内嵌两次：取数据行最多的表，并按 `(category, product_name)` 去重。
- 期号无法从标题解析时 `period` 置空字符串，行仍保留（不猜测）。

### 历史深度

旬度发布，2014 年起可回溯（按列表页翻页取旧文）。默认 `limit=100` 覆盖最新 2 期
（每期 ~50 行）；单次最多取 6 期（`CIRC_MAX_ISSUES`），期与期间隔 2 秒。

### 行 schema（manifest functions[].columns 对齐）

| 字段 | 类型 | 说明 |
|------|------|------|
| period | str | 旬区间 `YYYY-MM-DD~YYYY-MM-DD`（下旬截止到月末实际日期） |
| category | str | 表内分类行（如 黑色金属/有色金属/化工产品/煤炭/石油天然气/非金属矿物制品/农业生产资料/农产品/林产品） |
| product_name | str | 产品名称（含规格型号） |
| price | float\|None | 本期价格（元） |
| price_unit | str | 「单位」列（吨/千克等） |
| price_change | float\|None | 比上期价格涨跌（元） |
| mom_pct | str\|None | 涨跌幅（%，比上期） |
| yoy_pct | str\|None | 同比（当前表头无此列，恒 null，留作扩展） |
| scraped_at | str | 抓取时间（ISO） |
| source_url | str | 文章 URL |

### 真实取数证据（2026-10-02 04:18 UTC 实测，直连）

- 最新一期文章 URL：`https://www.stats.gov.cn/sj/zxfb/202609/t20260923_1965403.html`
  （《2026年9月中旬流通领域重要生产资料市场价格变动情况》）
- 期号区间（本次 limit=100 实取 2 期）：`2026-09-01~2026-09-10` 与 `2026-09-11~2026-09-20`
- 最新一期行数：**50 行**（门槛 ≥45 达标），price 非空 50/50
- 分类分组实测为 9 组（侦察簿所记「六大类」为旧版口径，NBS 已将能源拆分为煤炭/石油天然气、
  农林拆分为农业生产资料/农产品/林产品，品种总数仍 ~50）
- 首/末行（品名+价格）：
  - 首：`黑色金属 / 螺纹钢（Φ20mm，HRB400E）` 本期价格 **3165.1 元/吨**（比上期 -17.6，涨跌幅 -0.6%）
  - 末：`林产品 / 瓦楞纸（AA级120g）` 本期价格 **3080.5 元/吨**（比上期 -28.4，涨跌幅 -0.9%）
- 样例行 JSON（首行）：

```json
{"period": "2026-09-11~2026-09-20", "category": "黑色金属", "product_name": "螺纹钢（Φ20mm，HRB400E）", "price": 3165.1, "price_unit": "吨", "price_change": -17.6, "mom_pct": "-0.6", "yoy_pct": null, "scraped_at": "2026-10-01T20:18:03.974419+00:00", "source_url": "https://www.stats.gov.cn/sj/zxfb/202609/t20260923_1965403.html"}
```

- 结论：【直连=6/代理=0】列表页+3 篇文章×2 轮全部直连 200，未用代理。

## fd-runner 入口（run_nbs_stats）合并语义

`run_nbs_stats(limit=100)` 是唯一入口，顺序执行两条**相互隔离**的路径：

1. **既有 easyquery 路径**（hgjd/hgyd 等指标，原逻辑原样保留）整体包 try/except：
   失败只记 warning 返回已取部分，**不可能拖死新数据**。
2. **流通领域价格路径**（`get_circulation_prices`）用剩余行数预算
   （`limit - len(legacy)`）补齐；自身失败同样 log-and-skip。

两路结果拼接（旧路径在前）后截断到 `limit`，保证「返回 ≤limit 行」。
注意：`data.stats.gov.cn/easyquery.htm` 对部分网络环境返回 403（2026-10-02 实测），
此时旧路径按其原有语义记 warning 返回 `[]`，合并结果即纯流通领域价格行。

## Output

### SQLite Database

`data/nbs_stats.db` with table `nbs_indicators`:

| Column | Type | Description |
|--------|------|-------------|
| period | TEXT | 统计期间 (e.g., 2024Q1, 2024-01, 2024) |
| category | TEXT | 数据类别 |
| indicator_type | TEXT | 指标类型 |
| value | REAL | 指标数值 |
| indicator_code | TEXT | NBS内部编码 |
| indicator_name | TEXT | 指标名称 |
| unit | TEXT | 单位 |
| frequency | TEXT | 频率 (Q/A/M) |
| source | TEXT | 数据来源 |
| fetched_at | TEXT | 抓取时间 |

### JSON Files

- `output/nbs_gdp.json` - GDP data
- `output/nbs_price_indices.json` - CPI/PPI data
- `output/nbs_industrial.json` - Industrial production & PMI
- `output/nbs_population.json` - Population data
- `output/nbs_employment.json` - Employment data

## Rate Limiting

- **Download delay**: 2 seconds between requests
- **Concurrent requests**: 1
- **Recommended**: Do not reduce delay below 2s

## Notes

- NBS API returns data in a custom JSON format with `returncode`, `returndata`, `datanodes`, and `wdnodes`
- Period parsing handles Chinese date formats (e.g., "2024年第1季度" → "2024Q1")
- akshare fallback is used when direct API calls fail
- Data is deduplicated via SQLite `UNIQUE` constraints
