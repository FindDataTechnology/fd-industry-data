# sse-szse-margin —— 沪深交易所两融余额（融资融券交易总量）

自建单元（2026-11-07）。抓沪深两所**官方**披露的融资融券交易总量日频汇总：
融资余额（rzye）/ 融券余额（rqye）/ 两融余额（rzrqye），统一换算为**元**。
**免 key、免鉴权、直连可达**（无需代理出口，manifest 不声明 `egress_secret`）。

## 1. 数据表面

| 项 | SSE（上交所） | SZSE（深交所） |
|---|---|---|
| 落地页 | https://www.sse.com.cn/market/othersdata/margin/sum/ | https://www.szse.cn/disclosure/margin/margin/index.html |
| JSON 端点 | `GET https://query.sse.com.cn/marketdata/tradedata/queryMargin.do?isPagination=true&beginDate=YYYYMMDD&endDate=YYYYMMDD&tabType=&stockCode=&pageHelp.pageSize=N&pageHelp.pageNo=1&pageHelp.beginPage=1&pageHelp.cacheSize=1&pageHelp.endPage=5` | `GET https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1837_xxpl&txtDate=YYYY-MM-DD&tab1PAGENO=1` |
| 鉴权/请求头 | 常规 UA + `Referer: https://www.sse.com.cn/`（见坑位：Referer 强制与出口 IP/WAF 状态相关，代码恒带） | 常规 UA + `Referer` 落地页（实测不带也能 200，恒带对齐站点 XHR） |
| 请求模式 | **一次请求取整个日期窗口**（`pageHelp.pageSize` 给足窗口天数） | **逐日查询**（`txtDate` 单日，每交易日 1 行；无 `txtDate` 返回全历史但行内**无日期**，不可用） |
| 响应形态 | 对象：`result` 为日行数组（`opDate`/`rzmre`/`rzye`/`rqmcl`/`rqyl`/`rqylje`/`rzrqjyzl`），`pageHelp.total` 计数 | 单元素数组：`[0].metadata.cols`（中文列名+**标注单位**）+ `[0].data`（该日 1 行，键 `jrrzmr`/`jrrzye`/`jrrjmc`/`jrrjyl`/`jrrjye`/`jrrzrjye`，字符串带千分位，**行内不带日期**） |
| 原生单位 | **元**（int） | **亿元**（列名标注），换算 ×1e8 → 元 |
| 字段映射 | rzye←`rzye`，rqye←`rqylje`（融券余量金额），rzrqye←`rzrqjyzl` | rzye←`jrrzye`，rqye←`jrrjye`，rzrqye←`jrrzrjye`（各按列名标注单位换算） |
| 频度/深度 | 日频，2010-03 起 | 日频，2010-03 起（早期年份为全零行，代码跳过） |

输出行（8 键，与 manifest columns 一一对应）：
`{source:"sse-szse-margin", exchange:"SSE"|"SZSE", trade_date:"YYYY-MM-DD", rzye:float(元), rqye:float(元), rzrqye:float(元), source_url:str, scraped_at:ISO}`。
`limit` = **每所**保留的最近交易日行数上限（合计至多 `2*limit` 行），按
（exchange, trade_date）升序确定性排序。SZSE 逐日回溯自然日尝试上限
`min(3*limit+20, 400)`（约覆盖 260 个交易日，长假期也够）。

## 2. 通道选择理由（brief 给的起点为何不可用）

| 候选通道 | 实测（2026-11-07） | 判定 |
|---|---|---|
| SSE `commonQuery.do?sqlId=COMMON_SSE_SJ_GPSJ_CJGZ_MRSC_JYHL_SUM_STATU_L&PRODUCT_CODE=01`（brief 起点） | HTTP 200 但 `result: null`、`total: 0`——只回显参数不落数据 | ❌ 空壳 sqlId |
| SSE `commonQuery.do?sqlId=COMMON_SSE_ZQPZ_YZPLXX_ETFCZRZQ_DEFAULT_LJ`（brief 已标注非正确端点） | 未走通 | ❌ |
| SSE 页面 HTML 里找 sqlId | 页面是 SPA，表格由 `common_table.js`/`table_config.js` 装载，HTML 无 sqlId | — |
| SSE `query.sse.com.cn/marketdata/tradedata/queryMargin.do`（akshare 同源核对） | 200，`result` 带数值，`rzye+rqylje==rzrqjyzl` 逐元相等（口径自洽） | ✅ **选中** |
| SZSE `ShowReport/data?CATALOGID=2003_sczm`（brief 给的 catalog，网上流传写法） | HTTP 200 但 `error:"noalert@显示报表数据失败"`、`data:[]`（catalog 已下线/改名；加 PAGENO/PAGESIZE/txtQueryDate 均无效） | ❌ 目录失效 |
| SZSE 页面 HTML `data-s-catalog-id` | `1837_xxpl`（与 akshare `stock_margin_szse` 互证） | ✅ **选中** |
| SZSE 不带 `txtDate`（报表默认） | 200，**4031 行全历史**但行内无日期（`pagetype:"vertical"`） | ❌ 无法定位日期 |

单位换算：SZSE 列名自带单位标注（`融资余额<br>(亿元)` 等）。代码**从列名
动态解析单位**（亿元→×1e8，万元→×1e4，元→×1），缺标注/未知口径即抛
`RuntimeError`——上游若改单位口径，宁可红也不猜。SSE 数值原生即元，实测
`rzrqjyzl == rzye + rqylje` 精确相等。

## 3. 直连证据（2026-11-07 实测，本机 macOS 直连，无代理）

| 请求 | 结果 |
|---|---|
| `GET query.sse.com.cn/marketdata/tradedata/queryMargin.do`（窗口 20261026..20261107，pageSize 50，带 Referer） | **HTTP 200**，`total:10`、`result` 10 行（2026-10-26..11-06 共 10 个交易日） |
| 同上**不带 Referer** | HTTP 200（今日本机未触发 403；但 brief/历史经验称缺 Referer 常 403，WAF 按 IP/指纹波动——代码恒带） |
| `GET commonQuery.do?sqlId=...SUM_STATU_L&PRODUCT_CODE=01` | HTTP 200，`result: null`、`total: 0`（空壳） |
| `GET szse.cn/api/report/ShowReport/data?...CATALOGID=2003_sczm&TABKEY=tab1`（±PAGENO/PAGESIZE/txtQueryDate/Referer/random） | HTTP 200，`error:"noalert@显示报表数据失败"`、`data:[]`（4 种参数组合均空） |
| `GET ...CATALOGID=1837_xxpl&txtDate=2026-11-05` | HTTP 200，1 行：融资买入额 887.84 / 融资余额 **10,901.79** / 融券卖出量 0.53 / 融券余量 12.36 / 融券余额 103.64 / 融资融券余额 **11,005.44**（亿元） |
| `GET ...txtDate=2026-11-07`（周六）/ `2026-10-01`（国庆） | HTTP 200，`data:[]` 0 行、无 error——非交易日自然空档 |
| `GET ...txtDate=2026-11-06`（周五，当日周六查询） | 0 行（SZSE 有发布时滞：T 日数据可能 T+1 工作日才可查；SSE 同日已有 11-06） |
| `GET ...CATALOGID=1837_xxpl`（不带 txtDate） | HTTP 200，4031 行全历史、行内无日期 |

量级互证：SZSE 2026-11-05 融资余额 10,901.79 亿元 = 1.090179e12 元，与 SSE
同期 rzye 1.18e12 元同量级，合理。本机实跑 `limit=10` 得两所各 10 行。

## 4. 口坑

- **SSE 的 `commonQuery.do` + sqlId 是空壳**：200 但 `result:null`——别被
  「端点存在」迷惑；真通道是 `marketdata/tradedata/queryMargin.do`，日期参数
  为 **8 位 YYYYMMDD**（带连字符会查不到数据）。
- **SSE Referer 是否强制随出口 IP/WAF 波动**：本日实测不带亦 200，但站点
  XHR 恒带 `Referer: https://www.sse.com.cn/`，代码恒带以防 WAF 按 Referer
  过滤；遇 403 先核对 Referer/UA 再判风控。
- **SZSE catalog 是 1837_xxpl**：流传的 `2003_sczm` 已失效（返回
  `error:"noalert@显示报表数据失败"` 且加任何分页/日期参数都无效）。识别
  方法：落地页 HTML 内嵌 `var curCatalogId = '1837_xxpl'` 与
  `data-s-catalog-id` 属性。
- **SZSE 行内没有日期列**：日期来自查询参数 `txtDate`；不带 `txtDate` 的
  全历史 4000+ 行因无日期不可用。因此多日数据靠**逐日回溯**（每交易日约
  1.4 次请求，周末/节假日 0 行直接跳过；`limit=50` 约 170 次请求内收敛）。
- **SZSE 有发布时滞**：T 日汇总可能 T+1 工作日才可查（2026-11-07 查 11-06
  为空而 SSE 已有）——回溯从北京时区「今天」起算，取「可查到的最新交易日」。
- **SZSE 单位标在列名里**（`(亿元)`）：代码动态解析列名单位再换算，列名缺
  单位标注/换列名（缺 `jrrzye/jrrjye/jrrzrjye` 任一列）即抛 `RuntimeError`
  （报错带实际列名清单），绝不按旧系数硬换。
- **SZSE 早期全零行**：2010 年数据起点附近整行为 0.00，视为无数据跳过。
- **部分失败策略**：两所各自抓取；单所失败（HTTP/非 JSON/缺结构/缺列/0 行）
  该所抛 `RuntimeError`、stderr 告警，**另一所照常产出**；两所全败（合计
  0 行）才整体抛错——绝不静默回空。
- **仅常规 UA**：免鉴权公开统计，不做指纹伪装与频次对抗；SZSE 逐日回溯为
  顺序请求、无并发、无重试，遇风控升级按协议转人工。
- **失败即红**：两所全败/0 观测行/缺列 → `RuntimeError`，不静默回空。

## 5. golden 断言口径（跨期稳定）

`golden/001-sse-szse-margin.json` 用 `{"limit": 10}` 重放（两所各取最近
10 个交易日），**只锚结构常量**：`source=sse-szse-margin`、
`exchange=SZSE` + `min_rows=2`。**绝不锚**任何当期余额数值与 `trade_date`
（两融余额每日变动、回溯起点随日期漂移）。

只锚 SZSE 不锚 SSE 的原因：SZSE 报表接口无 WAF 门槛（带不带 Referer 都
200），是跨环境可达性最稳的一所；SSE `query.sse.com.cn` 对出口 IP/Referer
的 WAF 策略波动（见坑位），若按协议需代理出口，单锚 SSE 会让 golden 在
受限环境误报。SZSE 单所 `limit=10` 即产出 ≥2 行，`min_rows=2` 在「仅 SZSE
可达」的最坏情形下仍成立。

## 6. 自检

```bash
python3 spiders/sse-szse-margin/spider.py        # 冒烟：打印两所最近各 4 行
python3 scripts/validate_manifests.py            # manifest 契约校验
python3 scripts/check_manifest_commands.py       # manifest 命令漂移 lint
python3 scripts/conformance_gate.py              # 仓级机械闸（基线已有 restaurant-rpi 两条，不新增）
.venv/bin/python -c "from fd_industry_data.health.samples import load_samples, validate_sample; from pathlib import Path; s=load_samples(Path('spiders/sse-szse-margin')); print(validate_sample(s[0]))"
.venv/bin/python -c "from fd_industry_data.health.samples import load_samples; from fd_industry_data.health.guard import replay_sample; from pathlib import Path; s=load_samples(Path('spiders/sse-szse-margin')); r=replay_sample(Path('spiders/sse-szse-margin'), s[0]); print(r)"
```
