# dce — 大连商品交易所日线结算价（新浪通道）

## 单元档案

| 项 | 值 |
|---|---|
| 默认通道 | 新浪期货日线 `InnerFuturesNewService.getDailyKLine`（stdlib urllib，无第三方依赖） |
| 默认 symbols | `M0`（豆粕主力连续）、`C0`（玉米主力连续） |
| 主量 | 结算价 settle（交易所口径，经新浪转载），辅 close/volume/持仓量 |
| 调度 | `20 17 * * 1-5`（交易日 17:20 抓当日收盘+结算），`data_period: day`，manifest v2 |
| golden | `golden/001-dce-m0.json`：M0 序列锚 2021-01-05 settle=3468.0，min_rows 4000 |
| 抽取器 | `scripts/restaurant_extractors/dce.py`（dedup 按 (symbol, date)，settle==0 哨兵跳过，≥24 期门） |

## 通道实录：为什么不是官方站点

**瑞数（Ruishu）JS 挑战，结构性挡死。** 旧页面通道（scrapling 模拟 Chrome 抓
`www.dce.com.cn/dalianshangpin/...` 列表页）在生产环境的表现是：

- 直连/模拟浏览器均返回 **HTTP 412** 或空壳页——瑞数在首次响应里下 JS 质询，
  求不出动态 cookie 就拿不到任何表格；
- 生产 run 状态 success 但 **rows_written=0**（解析器在空壳页上零产出，
  异常被吞）——这是 2026-11-06 清账裁决 park 的直接证据；
- 继续投入需要常驻 JS 运行时 + 持续对抗瑞数升级，而同一份日线数据经新浪
  免费可得，边际收益为零。

因此 2026-11-07 餐饮垂直线波0 **有意识翻案**该 park 裁决：默认通道切换为
新浪日线，去 `parked`、恢复在产调度。裁决依据落在
`output/adjudication/ledger.json`（decided_by `restaurant-vertical-2026-11-07`）。

旧页面通道代码**原样保留**在 `spider.py` 的 `_SCRAPING_AVAILABLE` 守卫块内
（`DCESpider` / `run_spider` / `run_dce_pages`），不再被任何调度面引用；
保留它只为存档页面结构知识，激活前必须先解决瑞数质询。

## 新浪通道口径

- **接口**：`GET https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_dce_=/InnerFuturesNewService.getDailyKLine?symbol=<SYM>`
  （jsonp 信封 `var _dce_=([...]);`；akshare `futures_zh_daily_sina` 同一底层，
  其硬编码的 `type=2021_04_12` 参数经实测惰性——任意值或省略响应逐字节一致）。
- **数据语义**：交易所日线经新浪转载——`d` 交易日 / `o h l c` 四价 / `v` 成交量 /
  `p` 持仓量 / `s` 结算价。主力连续 `M0`/`C0` 是新浪按主力切换拼好的
  roll 序列（历史深度实测 **2005-01-04 .. 2026-11-06，各 ~5300 行**）。
- **结算价哨兵**：`s=0.000` 表示当日未转载结算价——M0 共 1589 天
  （散布全史，最后一例 2024-09-25）。payload 照实存 0.0，
  抽取器按"不造假"纪律跳过这些行，绝不写 0 价格。
- **具体合约**：任意新浪合约符号可用（如 `M2701`，2026-01-19 起 193 行）；
  非主力连续符号 `contract_desc` 保持原名，主力连续为中文描述。

## Payload 契约

```json
{
  "symbol": "M0",
  "contract_desc": "豆粕主力连续",
  "date": "2021-01-05",
  "settle": 3468.0,
  "close": 3483.0,
  "volume": 1409491.0,
  "open_interest": 1658509.0,
  "source_url": "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/InnerFuturesNewService.getDailyKLine?symbol=M0",
  "scraped_at": "2026-11-07T13:50:12.225968+00:00"
}
```

确定性顺序：symbols 按入参顺序、日期升序；`limit` 截断该流。
`--params '{"symbols":["M0","C0"]}'`（manifest 已声明 `symbols` 参数，
runner 只透传入口签名显式接受的键）。

## 本地运行

确定性顺序：**按日期倒序、symbols 间轮转**（M0 最新一日、C0 最新一日、M0 次新、
C0 次新……）。runner 默认 `--limit 100`，该排序下每次调度 run 刷新两个品种
各最近 ~50 个交易日；全历史（~10.6k 行）由一次性大 limit 回填落入
`crawl_items`（跨 run 持久，抽取器读全部成功 run 的 payload）：

```bash
# 单跑（默认 M0+C0；全历史回填用大 limit，中央库按 (symbol,date) 语义去重）
source ~/.config/finddata/dashboards.env
FD_CRAWL_DB_URL="$FD_OPEN_DATA_DSN" FD_WRITER_MAX_ITEMS=50000 \
  ./.venv/bin/python -m fd_industry_data.runner_cli dce \
  --params '{"symbols":["M0","C0"]}' --limit 20000
```

幂等：语义库按 `(concept, entity, date, granularity, source)` 唯一约束去重，
重复 run 零新行；同一 (symbol, date) 多次抓取由抽取器保留 scraped_at 最新
的 payload。

## 验证数字（2026-11-07 实测）

| 断言 | 值 |
|---|---|
| M0 行数 / 窗口 | 5315 行，2005-01-04 .. 2026-11-06 |
| C0 行数 / 窗口 | 5309 行，2005-01-04 .. 2026-11-06 |
| M2701（对照） | 193 行，2026-01-19 起 |
| 锚点 M0 2021-01-05 | settle 3468.0 / close 3483.0 / vol 1,409,491 / 持仓 1,658,509 |
| settle==0 行数 | M0 1589（末例 2024-09-25）；C0 1581 |
| 结算价可用窗口 | 2010-06-08 .. 2026-11-06（两品种同；此前 settle 全为 0 哨兵）|
| type 参数惰性 | 三种取值响应逐字节一致 |

## 已知事项

1. 主力连续的历史由新浪拼接，交易所换月切点以新浪口径为准（未复权，
   与逐合约结算价一致；跨切换日的价差不代表收益）。
2. `settle==0` 天数约占 30%（2010-06-08 前全为 0，此后零星散布到 2024-09-25）；
   结算价序列实际覆盖 2010-06-08 起约 3726 期，远超 24 期趋势门。
3. 实体行 `DCE.M0` / `DCE.C0`（entity_type `contract`）由主代理在正式抽取前
   注册；抽取器自身不建实体行，缺实体时照常产出行（entity_id=None）并留 note。
