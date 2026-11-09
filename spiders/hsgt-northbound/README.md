# hsgt-northbound —— 沪深港通北向资金（沪深股通每日成交概况，交易所官方口径）

北向资金（沪股通 + 深股通）每日成交概况。**免 key、免鉴权、直连可达**（无需
代理出口，manifest 不声明 `egress_secret`）。

## 1. 披露制度变迁（口径基石，2024-08 切换）

- **2024-08-16 起，港交所（HKEX）停止披露北向实时成交与每日净买入**
  （北向实时数据于 2024-08-16 收盘后终止，末个含净买入的交易日为 2024-08-16）。
- 北向（沪股通/深股通）**现存官方披露面** = 交易所每日成交概况：
  **买+卖合计的成交总额、成交总笔数、ETF 成交总额**，外加额度使用。
  **不再有买入/卖出拆分、不再有净买入**。
- 两交易所的具体形态（本单元实测）：
  - SSE「沪股通成交概况」每日成交信息 = 当日成交总额 / 总成交笔数 / ETF 成交总额（亿元人民币）；
  - SZSE「深股通交易日报」= 当日交易总额 / 交易总笔数 / ETF 交易总额（亿元人民币）。
- 因此本单元输出契约中 `buy_turnover`/`sell_turnover` **恒为 `None`**——
  键保留（`market.hsgt_buy_turnover`/`market.hsgt_sell_turnover` 概念位），
  值空缺是**披露制度而非采集缺陷**；若上游未来恢复拆分，解析代码已就位会自动透出。
- 对照：**南向**（港股通沪/深）官方仍披露买入/卖出拆分（如 SSE
  `ggt/getQuatationInfo.do` 返回 `BUY_AMOUNT`/`SELL_AMOUNT`）——不要把南向字段
  误当北向口径。

## 2. 数据表面与端点

| 项 | 沪股通（SSE） | 深股通（SZSE） |
|---|---|---|
| 官方页面 | http://www.sse.com.cn/services/hkexsc/hgtscsj/hgtcjgk/ （沪港通专栏→沪股通市场数据→沪股通成交概况） | http://www.szse.cn/szhk/szhktradeinfo/szdaily/index.html （深港通→深港通交易信息→深股通交易日报） |
| 端点 | `GET https://query.sse.com.cn/commonSoaQuery.do?sqlId=FW_HGTZL_HGTSCSJ_HGTCJGK_MRTJ&tradeDate=YYYYMMDD` | `GET https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=SGT_SGTJYRB&txtDate=YYYY-MM-DD` |
| 鉴权 | 免 key；**必须带 `Referer: http://www.sse.com.cn/`**（否则 200 但 `success:false`） | 免 key；无 Referer/无 UA 也 200（实测） |
| 数据粒度 | 单日一请求（`tradeDate` 紧凑 YYYYMMDD） | 单日一请求（`txtDate` 带连字符） |
| 非交易日形态 | HTTP 200，`result: []` 或 `result: null` | HTTP 200，`data: []`、`metadata.subname: ""` |
| 单位 | 亿元（人民币）/ 万笔 | 亿元（人民币）/ 万笔 |

响应样本（2026-11-06 实测）：

```jsonc
// SSE result[0]（totalAmount 带千分位逗号）
{"tradeDate": "20261106", "totalAmount": "1,283.68", "totalVolume": "704.67", "etfTotalAmount": "26.48"}
// SZSE data（垂直报表，label/total 对）
[{"label": "当日交易总额（亿元人民币）", "total": "1,342.25"},
 {"label": "当日交易总笔数（万笔）",     "total": "754.69"},
 {"label": "当日ETF交易总额（亿元人民币）", "total": "24.73"}]
```

两个端点均取自**交易所官方页面自身加载的 JS/报表**（SSE 页面 JS
`/xhtml/home/2021public/querySearch/search_southboundStock_2021.js` 内
`sqlId: "FW_HGTZL_HGTSCSJ_HGTCJGK_MRTJ"`；SZSE 页面 `curCatalogId = 'SGT_SGTJYRB'`），
非第三方聚合。

## 3. 端点探索记录（README 纪律：记录最终可用通道）

brief 建议 `http://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_SJ_SHHGTLGT_JYGK_MRTJ_JYL_Y_M&STAT_DATE=...`（带 Referer）。实测（2026-11-07）：

| 候选 | 实测 | 判定 |
|---|---|---|
| brief sqlId @ `commonQuery.do`（`STAT_DATE=2026-11-06`/`20261106`/无参） | 200 但 `result: null`、`total: 0` | ❌ 空，已退役 |
| brief sqlId @ `commonSoaQuery.do` | `{"success":"false","errorMsg":"SOA service is null！"}` | ❌ 非 SOA sqlId |
| 官方页面 JS 实走：`commonSoaQuery.do?sqlId=FW_HGTZL_HGTSCSJ_HGTCJGK_MRTJ&tradeDate=` | 200，`result[0]` 含 totalAmount/tradeDate | ✅ **选中（沪股通）** |
| `commonQuery.do?sqlId=FW_HGTZL_HGTSCSJ_HGTCJGK_MRTJ` | 未走此路（页面 JS 走 commonSoaQuery） | — |
| SZSE `ShowReport/data?CATALOGID=SGT_SGTJYRB&txtDate=`（官方页面 curCatalogId） | 200，垂直报表 3 行 | ✅ **选中（深股通）** |
| SZSE 用 `txtQueryDate=`（错误参数名） | 参数被忽略，返回默认期数据 | ❌ 参数名以 conditions 元数据为准：`txtDate` |

输出行：`(source, channel, trade_date, buy_turnover, sell_turnover, total_turnover, source_url, scraped_at)`；
`limit` 为**每通道**最近交易日数上限，合计至多 `2*limit` 行，按
(trade_date, channel) 字典序确定性排序。单位换算：上游亿元 × 1e8 = 元。

## 4. 直连证据（2026-11-07 实测）

| 出口 | 请求 | 结果 |
|---|---|---|
| 本机直连 | `GET https://query.sse.com.cn/commonSoaQuery.do?sqlId=FW_HGTZL_HGTSCSJ_HGTCJGK_MRTJ&tradeDate=20261105`（带 Referer） | HTTP 200，totalAmount=1,519.97 亿 |
| 本机直连 | 同端点 `tradeDate=20261106` | HTTP 200，totalAmount=1,283.68 亿 |
| 本机直连 | 同端点 `tradeDate=20261107`（周六） | HTTP 200，`result: []`（非交易日形态） |
| 本机直连 | 同端点 **无 Referer** | HTTP 200 但 `{"success":"false","errorMsg":"null"}` |
| 本机直连 | `GET https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=SGT_SGTJYRB&txtDate=2026-11-05`（无 Referer 无 UA） | HTTP 200，当日交易总额=1,681.67 亿 |
| 本机直连 | 同端点 `txtDate=2026-11-06` | HTTP 200，总额=1,342.25 亿 |
| 本机直连 | 同端点 `txtDate=2026-11-07`（周六） | HTTP 200，`data: []` |
| 官方页面 | SSE 沪股通成交概况页 / SZSE 深股通交易日报页 | 均 HTTP 200 |

→ 本机直连全绿（本单元为境内官方源，未测境外出口；如需境外站点运行先复测）。
manifest 不写 `egress_secret`、不写 `site`。

## 5. 口坑

- **brief 的 sqlId 是死路**：`COMMON_SSE_SJ_SHHGTLGT_JYGK_MRTJ_JYL_Y_M` 在
  `commonQuery.do` 上永远 `result:null`（与日期格式无关），在 `commonSoaQuery.do`
  上报 `SOA service is null`。找端点的正确姿势：打开官方专栏页面，翻它自己加载
  的查询 JS，拿页面实走的 sqlId/CATALOGID。
- **SSE 拒绝时仍是 HTTP 200**：无 Referer 时 body 是
  `{"jsonCallBack":"null","success":"false","errorMsg":"null"}`——必须判
  `success/errorMsg` 形态，不能只看状态码；本单元视其为该通道端点级失败。
- **SSE 数值带千分位逗号**：`"1,283.68"`，解析前去逗号。
- **SZSE 参数名是 `txtDate` 不是 `txtQueryDate`**：参数名可从响应
  `metadata.conditions[].name` 读出；传错参数名不报错、而是悄悄返回默认期数据
  （`subname` 会暴露真实期次，本单元校验回填）。
- **北向无买卖拆分是制度不是漏采**：2024-08-16 起 HKEX 停止北向实时/每日净买入
  披露，交易所侧北向只剩合计成交概况；`buy_turnover`/`sell_turnover` 恒为 None，
  golden 绝不锚这两个字段，也不锚任何成交额数值。
- **单通道失败不拖垮整体**：某通道网络/结构异常 → stderr 告警 + 只出可达通道的
  行；两通道全失败或合计 0 行才抛 `RuntimeError`（失败即红，绝不静默空）。
  golden 锚两个通道（均实测可达）。
- **单请求瞬断有界重试**：逐日回溯一次跑 10-20 个请求，实测出现过单请求 SSL
  握手超时（复测即恢复）——网络层失败重试 1 次（共 2 次尝试，间隔 2s）再红；
  解析错误（非 JSON/缺列/数值坏）属端点形态问题，不重试直接红。
- **非交易日 = 空数据体**：周末/节假日两通道均返回空（不报错）；`limit` 个
  交易日靠逐日回溯凑齐（周末跳过），回看上限 60 自然日，触顶即接受已收集行。
- **单日单请求**：无免 key 的日期区间端点；`limit` 大时请求数线性增长（每通道
  约交易日数 + 节假日数），勿一次性拉长历史，长历史回补应走交易所月报/年报面。
- 仅常规 UA + SSE 官方要求的 Referer；免 key 免鉴权，不做指纹伪装与频次对抗，
  遇风控升级按协议转人工。

## 6. golden 断言口径（跨期稳定）

`golden/001-hsgt-northbound.json` 用 `{"limit": 10}` 重放（每通道最近 10 个
交易日，两通道均可达时约 20 行），锚**结构常量**：`source=hsgt-northbound` /
`channel=沪股通` / `channel=深股通` + `min_rows=4`（两通道各 ≥2）。
**绝不锚** `trade_date`（滚动窗口）、`total_turnover` 数值、买卖字段；
`scraped_at` / `upstream_updated` 进 `whitelist_fields`。

## 7. 自检

```bash
python3 spiders/hsgt-northbound/spider.py       # 冒烟：打印最近 3 个交易日 × 2 通道
python3 scripts/validate_manifests.py           # manifest 契约校验
python3 scripts/check_manifest_commands.py      # manifest 命令漂移 lint
python3 scripts/conformance_gate.py             # 仓级机械闸
```
