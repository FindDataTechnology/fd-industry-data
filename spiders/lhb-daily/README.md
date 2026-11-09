# lhb-daily —— 沪深龙虎榜（每日上榜股票×上榜原因）

自建单元（沪深市场统计·龙虎榜）。**免 key、免鉴权、直连可达**（无需代理出口，
manifest 不声明 `egress_secret`）。取最近有榜交易日（从今天 UTC+8 逐日回溯跳过
无榜日，上限 7 日），每上榜股票×上榜原因一行：
`(source, symbol, name, trade_date, reason, close, change_pct, lhb_net_buy,
lhb_buy, lhb_sell, source_url, scraped_at)`。

## 1. 数据表面

### 1.1 东方财富 datacenter 聚合端点（akshare `stock_lhb_detail_em` 同源）

| 项 | 值 |
|---|---|
| 页面 | `https://data.eastmoney.com/stock/lhb.html` |
| 端点 | `GET https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL&filter=(TRADE_DATE='YYYY-MM-DD')&pagesize=500&pagenum=1&sortColumns=SECURITY_CODE&sortTypes=1&source=WEB&client=WEB` |
| 鉴权 | **免 key、免鉴权**；常规浏览器 UA + 页面 Referer 即可（无 UA 请求实测本机当前放行，但东财 WAF 存在间歇软拦截，一律补 UA，见 §5） |
| 频度 | 交易日收盘后更新（约 17:00+）；`filter` 按单日查询，传非交易日得空信封 |
| 信封 | `{version, success, code, message, result:{count, pages, data[]}}` |

响应 `data[]` 每行一个「股票×上榜原因」（实测 2026-11-06，节选）：

```json
{"TRADE_DATE": "2026-11-06 00:00:00", "SECUCODE": "000525.SZ",
 "SECURITY_CODE": "000525", "SECURITY_NAME_ABBR": "红太阳",
 "EXPLANATION": "日涨幅偏离值达到7%的前5只证券",
 "CLOSE_PRICE": 5.02, "CHANGE_RATE": 10.0877,
 "BILLBOARD_NET_AMT": 98694262.06, "BILLBOARD_BUY_AMT": 148944265.27,
 "BILLBOARD_SELL_AMT": 50250003.21, "ACCUM_AMOUNT": 593597267,
 "MARKET": "SZ", "TRADE_MARKET": "深交所主板", "EXPLAIN": "普通席位买入，成功率38.49%", ...}
```

字段映射（→ 输出列，金额上游即**元**，无换算）：

| 上游字段 | 含义 | → 输出列 |
|---|---|---|
| `SECURITY_CODE` | 6 位股票代码（str） | `symbol` |
| `SECURITY_NAME_ABBR` | 证券简称 | `name` |
| `TRADE_DATE` | 上榜日（"YYYY-MM-DD 00:00:00"） | `trade_date`（取前 10 位） |
| `EXPLANATION` | **上榜原因** | `reason` |
| `CLOSE_PRICE` | 收盘价（元） | `close` |
| `CHANGE_RATE` | 涨跌幅（%） | `change_pct` |
| `BILLBOARD_NET_AMT` | 龙虎榜净买额（元） | `lhb_net_buy` |
| `BILLBOARD_BUY_AMT` | 龙虎榜买入额（元） | `lhb_buy` |
| `BILLBOARD_SELL_AMT` | 龙虎榜卖出额（元） | `lhb_sell` |
| `MARKET` | 市场（SH/SZ/BJ） | 范围过滤（剔 BJ，见 §2） |

注意 `EXPLANATION`（上榜原因，交易所口径）≠ `EXPLAIN`（东财解读，如"普通席位
买入，成功率 38.49%"）——本单元取 `EXPLANATION`。

### 1.2 席位明细（可选扩展，本单元未拉）

买卖席位（营业部）明细是另一张报表
`RPT_BILLBOARD_DAILYDETAILSBUY`（同 datacenter 端点，filter
`(TRADE_DATE='...')(SECURITY_CODE="...")`），实测可用：每行
`OPERATEDEPT_NAME`（营业部）/`BUY`/`SELL`/`NET` 等。本单元输出契约只含
汇总口径（每次运行一个单日聚合请求已自足），席位明细留作后续扩展，不并入。

## 2. 通道选择理由与范围裁定

| 候选通道 | 实测 | 判定 |
|---|---|---|
| `RPT_DAILYBILLBOARD_DETAILSNEW`（东财聚合，akshare `stock_lhb_detail_em` 同源） | 200，2026-11-06 得 79 行，字段与 akshare DataFrame 逐值一致 | ✅ **选中** |
| `RPT_BILLBOARD_DAILYDETAILSBUY`（席位明细） | 200，单股 5 行营业部买卖明细 | 备查（扩展用，本单元不拉） |
| 沪/深交易所官方 LHB 页面 | 官方页面为 HTML 表格/静态 JSON 挂载，解析面大；东财聚合即交易所披露数据的一口源（akshare 生产口径同源） | ❌ 不走（直连优先选 JSON 聚合） |

**范围裁定（北交所剔除）**：端点是沪深北三市聚合（2026-11-06 实测
`MARKET: SZ 41 / SH 31 / BJ 7`），本单元裁决范围为**沪深交易所**——
`MARKET=BJ`（北交所）行显式剔除，输出契约不带市场列以免语义混入；
仅剔除 MARKET 明确为 BJ 的行，字段缺失不臆测（缺失行若身份列完整会照常透出，
若身份列也缺失则失败即红）。

**行粒度**：同一股票可因多条上榜原因同日多行（实测 000525 一行、002086 三行；
688429 两条原因的买卖额各不相同——金额按上榜原因口径计），按
（symbol, trade_date, reason）去重后每原因一行，符合"每上榜股票×上榜原因一行"。

## 3. 直连证据（2026-11-07 实测，本机）

| 请求 | 结果 |
|---|---|
| `GET .../get?...RPT_DAILYBILLBOARD_DETAILSNEW...TRADE_DATE='2026-11-06'`（curl + UA） | HTTP 200，`count=79`、`pages=1`、`data` 79 行 |
| 同端点 `TRADE_DATE='2026-11-05'` | HTTP 200，69 行（与 akshare `stock_lhb_detail_em(20261105,20261105)` 行数一致） |
| akshare `stock_lhb_detail_em(start=20261106, end=20261106)` | 79 行，`代码/名称/上榜原因/收盘价/涨跌幅/龙虎榜净买额/买入额/卖出额` 与端点逐值一致（同源印证） |
| 非交易日 `TRADE_DATE='2026-11-07'`（周六）/`'2026-11-01'`（周日） | HTTP 200 但 `success=false, code=9201, message="返回数据为空", result=null` |
| 分页对账：`pagesize=10/50/200/500 × pagenum=1/2` | 全部返回当日全量（79 行），`pages` 恒 1——**分页参数被端点忽略** |
| 无 UA 请求（仅 Accept 头） | HTTP 200 且数据正常（本机当日未被拦；WAF 属间歇风控，spider 仍补常规 UA） |
| 席位明细 `RPT_BILLBOARD_DAILYDETAILSBUY`（000525 @2026-11-06） | HTTP 200，5 行营业部明细（`OPERATEDEPT_NAME`/`BUY`/`SELL`/`NET`） |
| spider 冒烟（周六运行） | 回溯到 2026-11-06（周五），出 3 行（limit=3） |

恒等式核对：79/79 行满足 `BILLBOARD_NET_AMT = BILLBOARD_BUY_AMT −
BILLBOARD_SELL_AMT`（透传不重算的依据）。量级 sanity：单日净买额 10⁷~10⁹ 元、
上榜 60~80 行（2026-11-05/11-06 两日），与东财 LHB 页面展示同量级。

→ 免 key 免鉴权直连全绿，**无需 fx01 代理出口**；manifest 不写 `egress_secret`、
不写 `site`。（tencent 站点出口复测待合入后由 CI/人工补齐。）

## 4. 单位换算

无换算：上游金额字段（BILLBOARD_*）即**元**，`CLOSE_PRICE` 即**元**，
`CHANGE_RATE` 即**%**，全部原值透传；`trade_date` 由
"YYYY-MM-DD 00:00:00" 截前 10 位规范化。

## 5. 口坑

- **非交易日端点不回空数组，回错误信封**：`success=false, code=9201,
  message="返回数据为空", result=null`——这是"该日无榜"的合法信号，回溯跳过；
  其它任何 HTTP/JSON/信封异常一律抛错（回溯绝不掩盖端点级故障，失败即红）。
- **分页参数被端点忽略**：`pagesize=10` 仍回当日全量、`pagenum=2` 同样全量、
  `pages` 恒为 1——单请求即全量；spider 用 `result.count` 与实收行数对账，
  不平即抛错（防上游哪天恢复真分页后静默半量）。
- **东财 WAF 可能软拦截无 UA 请求**：实测当日无 UA 恰好放行，但属间歇风控；
  spider 一律带常规浏览器 UA + `Referer: data.eastmoney.com/stock/lhb.html`。
  不做指纹伪装与频次对抗，遇风控升级按协议转人工。
- **端点聚合含北交所**：`MARKET=BJ` 行超出沪深裁决范围，显式剔除（§2）；
  不做"顺手带上"——输出契约无市场列，混入即语义漂移。
- **TRADE_DATE 带 00:00:00 尾巴且必须与查询日期对账**：行日期前 10 位不等
  查询日期即抛错（防端点串日污染快照）。
- **同一股票多上榜原因**：每原因一行、金额按原因口径各异（688429 实测两条
  原因买卖额不同）；按（symbol, trade_date, reason）去重守住行粒度契约。
- **数值列 null 容忍、身份列缺失即红**：close/change_pct/三金额列缺失 →
  null（schema 可空，不编造）；`SECURITY_CODE`（非 6 位数字）/`SECURITY_NAME_ABBR`/
  `EXPLANATION` 缺失即抛错并列出实得值。
- **回溯上限 7 日**：覆盖普通长假缺口（元旦/清明/五一/端午/国庆前后最长
  连休 8 天含周末，7 个自然日回溯足以跨过到最近一个有榜交易日）；7 日全空
  即红（超长休市或上游迁移，交人工判断）。
- **仅常规 UA**：免鉴权站点，不做指纹伪装与频次对抗，遇风控升级按协议转人工。
- **失败即红**：HTTP 错误 / 非 JSON / 信封异常 / count 对账不平 / 身份列
  缺失 / 回溯 7 日仍 0 观测 → `RuntimeError`，不静默回空。

## 6. golden 断言口径（跨期稳定）

`golden/001-lhb-daily.json` 用 `{"limit": 20}` 重放（最近有榜交易日的沪深
榜单切片），只锚结构常量：`source=lhb-daily` + `min_rows=3`。**绝不锚**
`trade_date`（滚动）、具体 `symbol`/`name`（每日上榜名单全换）与任何当期
金额/涨跌幅（每日变动）；`rows_contains` 仅支持 `equals`，无法表达"symbol
形如 6 位字符串"这类形态断言——6 位码校验在 spider 内做（非 6 位即红），
golden 不锚具体代码。`scraped_at` / `upstream_updated` 进 `whitelist_fields`。

## 7. 自检

```bash
python3 spiders/lhb-daily/spider.py        # 冒烟：打印最近有榜交易日 3 行
python3 scripts/validate_manifests.py      # manifest 契约校验
python3 scripts/check_manifest_commands.py # manifest 命令漂移 lint
python3 scripts/conformance_gate.py        # 仓级机械闸
```
