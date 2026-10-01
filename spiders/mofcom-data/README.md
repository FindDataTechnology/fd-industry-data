# mofcom-data — 商务部数据中心（data.mofcom.gov.cn）接入档案

- 入口：`run_mofcom_data(limit: int = 100) -> list[dict]`（`spiders/mofcom-data/spider.py`）
- 三口径合并输出，`indicator` 列区分；行 schema：`period(YYYY-MM) / indicator / value(float|None) / unit / scraped_at / source_url`
- 网络：国内源，直连（禁代理）。实测结论【直连=全量成功，代理=未使用】
- 接入验证（真实取数）：2026-10-02 04:19（北京时间），全部 HTTP 200

## 一、端点与方法

| 口径 | 方法与 URL | 参数（实测有效） | 返回结构 |
|---|---|---|---|
| 货物进出口月度 | `POST https://data.mofcom.gov.cn/datamofcom/front/totalmonth/query` | 仅 `pageNumber=1`（form） | 裸双元素数组 `[[行...],[模块元信息]]` |
| 服务贸易历年 | `GET https://data.mofcom.gov.cn/datamofcom/front/fwmy/overyears?pageNumber=N` | 仅 `pageNumber`（query，必填） | `{"rows":[...],"total":44,"pageSize":10,"maxPageNum":5,...}` |
| 利用外资月度 | `POST https://data.mofcom.gov.cn/datamofcom/front/lywz/direct/query` | 仅 `pageNumber=1`（form；实测空 body 等效） | 裸双元素数组 `[[行...],[行副本(字符串化)]]` |

## 二、鉴权

免鉴权（无 cookie/token）。请求须带 `Referer: https://data.mofcom.gov.cn/`；
实测当前未强制校验 Referer（缺失仍 200），按站点契约始终携带。UA 由
`FetcherSession(impersonate="chrome120")` 自带完整 Chrome UA。

## 三、参数白名单（硬约束：只发实测有效参数，多余参数一律不发）

- `totalmonth/query`：仅 `pageNumber` 生效。实测 `pageNo`、`year` 等多发不改变响应
  （侦察簿所记「多余参数 500」未复现，但一律不发以守约）。**翻页返回重复内容**
  （pageNumber=1/2/3 响应 md5 相同）：当年月度全序列一次返回（2026 年 8 行
  202601→202608），故只取 pageNumber=1 一页、不翻页。
- `fwmy/overyears`：`pageNumber` 必填——GET 不带该参数返回 Spring 错误页
  （`Optional int parameter 'pageNumber' is present but cannot be translated...`，
  HTTP 200 但 body 为 error HTML）。真分页：pageSize=10，按响应 `maxPageNum`
  翻至末页/空页（实测 5 页 44 行），代码侧防御上限 12 页。
- `lywz/direct/query`：`pageNumber=1` 实测 200 且与空 body 响应逐字节一致；
  全序列一次返回（247 行），不翻页。

## 四、容错规则（spec 契约）

- 5xx / 非 200 / 空响应 / JSON 解析失败 → 记 warning 日志后跳过该口径；
  **单次尝试，不重试、不连环请求、不绕反爬**。
- 三口径相互独立，单口径失败不影响其余口径。
- 数值清洗：千分位逗号去除；`""`/`"—"`/`"-"`/null → `value=None`（源端未发布，
  行保留）；无法转 float 的脏值 → None。period 非 `YYYYMM`（服务贸易为 `YYYY`）
  的行丢弃不写脏行。
- 指标白名单外的字段（各口径 `*_per` 同比/占比列、审计字段 createTime 等）不采集。

## 五、历史深度（2026-10-02 实测）

| 口径 | 序列范围 | 源行数 | 输出行数 | 说明 |
|---|---|---|---|---|
| 货物进出口月度 | 2026-01 → 2026-08（当年全序列） | 8 | 64 | 8 指标（当月值/累计值 × 总额/出口/进口/顺差），单位 亿美元 |
| 服务贸易历年 | 1982 → 2025 年度（记为 YYYY-12） | 44 | 352 | 8 指标（人民币/美元 × 总额/出口/进口/差额）；人民币口径 2010 起有值（16 年），美元口径 44 年全量 |
| 利用外资月度 | 1983-12 → 2026-08 | 247 | 494 | 2 指标；1983→2007 仅年度点（12 月），2008 起逐月；`实际使用外资金额` 数值至 2022-12 止（2023 起 `—`，由 `新设外商投资企业数` 延续），两指标均为年内累计口径 |

合计输出 910 行（`limit=100000` 实测）；输出按 period 降序、同期内按指标白名单
顺序；`limit` 生效（实测 `run_mofcom_data(limit=7)` 返回 7 行）。

## 六、真实取数证据（2026-10-02 04:19 北京时间，直连，.venv 执行 spider 函数）

验证门槛：
- 货物进出口含 **202608 月行**：PASS（2026-08：进出口总额当月 6837.97 / 出口 4014.41 /
  进口 2823.56 / 顺差 1190.85 亿美元；累计 50421.72 / 29238.39 / 21183.33）
- 利用外资序列首点 = **1983-12**：PASS（实际使用外资金额 9.2 亿美元、
  新设外商投资企业数 470 家）

首末值（按指标）：
- 货物进出口·进出口总额（当月值）：首 2026-01 = 5907.59 → 末 2026-08 = 6837.97 亿美元
- 服务贸易·服务进出口总额（美元）：首 1982-12 = 47.0 → 末 2025-12 = 11315.0 亿美元
  （人民币：首 2010-12 = 25022.0 → 末 2025-12 = 80823.0 亿元人民币）
- 利用外资·实际使用外资金额（年内累计）：首 1983-12 = 9.2 → 末（有值）2022-12 = 1891.3 亿美元
- 利用外资·新设外商投资企业数（年内累计）：首 1983-12 = 470 → 末 2026-08 = 42582 家

样例行（输出第 1 行）：

```json
{"period": "2026-08", "indicator": "进出口总额（当月值）", "value": 6837.97, "unit": "亿美元", "scraped_at": "2026-10-01T20:19:51+00:00", "source_url": "https://data.mofcom.gov.cn/datamofcom/front/totalmonth/query"}
```

利用外资首点样例：

```json
{"period": "1983-12", "indicator": "实际使用外资金额（年内累计）", "value": 9.2, "unit": "亿美元", "scraped_at": "2026-10-01T20:19:52+00:00", "source_url": "https://data.mofcom.gov.cn/datamofcom/front/lywz/direct/query"}
```

## 七、遗留与风险

- 货物进出口月度仅覆盖当年（站点即如此），历史年度需另寻年度接口补充。
- `实际使用外资金额` 2023 年起源端未发布月度值（`—`），非采集侧问题。
- `totalmonth/query` 的 `pageNumber` 实测被服务端忽略（翻页重复）；若站点未来
  启用真分页，需回补翻页逻辑。
- 服务贸易年度行 period 记为 `YYYY-12`（年度值），消费侧按 indicator 语义理解。
