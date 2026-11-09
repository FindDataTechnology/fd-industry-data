# stock-zygc —— 上市公司主营构成（分行业/分产品/分地区）

东方财富 F10「主营构成分析」直建单元。**免 key、免鉴权、直连可达**（无需代理
出口，manifest 不声明 `egress_secret`）。季度报告期（年报/中报/一季报/三季报），
只做单股/少股查询入口——批量按白名单全量采集是调度层的事。

## 1. 数据表面

| 项 | 值 |
|---|---|
| 平台 | 东方财富 F10（emweb.securities.eastmoney.com，`pc_hsf10` 前端） |
| 端点 | `GET https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax?code=SH600000` |
| 鉴权 | **免 key、免鉴权**（匿名 GET + 常规 UA 即可；无 UA（curl 默认）同样 200） |
| 频度 | 季度报告期（年报 12-31 / 中报 06-30 / 一季报 03-31 / 三季报 09-30） |
| 历史深度 | **每股硬上限 200 行**（实测浦发/平安/茅台均恰 200 行，约覆盖最近 9 个报告期），更早报告期拿不到 |
| 代码形态 | `SH600000`/`SZ000001`（沪深；`BJ` 北交所同端点同形态） |

JSON 载荷结构（实测 2026-11-07，SH600000）：

```json
{"zyfw": [{"SECUCODE": "600000.SH", "BUSINESS_SCOPE": "吸收公众存款;..."}],
 "zygcfx": [
   {"SECUCODE": "600000.SH", "SECURITY_CODE": "600000",
    "REPORT_DATE": "2026-06-30 00:00:00", "MAINOP_TYPE": "3",
    "ITEM_NAME": "总行", "MAIN_BUSINESS_INCOME": 29190000000,
    "MBI_RATIO": 0.31127, "MAIN_BUSINESS_COST": 12014000000,
    "MBC_RATIO": 0.209705, "MAIN_BUSINESS_RPOFIT": 17176000000,
    "MBR_RATIO": 0.470743, "GROSS_RPOFIT_RATIO": 0.588421, "RANK": 1}, ...],
 "jyps": [...]}
```

本单元只消费 `zygcfx`。输出行：
`(source, symbol, report_date, segment_type, segment_name, main_income,
income_ratio, main_profit, gross_margin, source_url, scraped_at)`。

## 2. MAINOP_TYPE 映射依据（实测推翻 brief 预写映射）

brief 预写「2=按行业, 3=按产品, 1=按地区」——**实测三方案独立证据一致推翻**，
本单元采用 **1→industry, 2→product, 3→region**：

| 证据源 | 内容 |
|---|---|
| 东方财富 F10 前端源码 | `pc_hsf10/pages/js/chunk-3b84982d.c1c66306.js` 内 tab 定义：`types:[{name:"按产品",value:"2"},{name:"按行业",value:"1"},{name:"按地区",value:"3"}]`——上游自己的渲染代码 |
| akshare 同端点实现 | `akshare/stock/finance/stock_zygc.py`：`{"1": "按行业分类", "2": "按产品分类", "3": "按地区分类"}` |
| 实测数据语义 | 茅台 SH600519（2025-12-31）：type 2 = 茅台酒/其他系列酒（产品）、type 3 = 国内/国外（地区）、type 1 = 酒类（行业）；平安 SZ000001 type 3 = 总部/南区/东区/北区/西区/境外（地区）；浦发 SH600000 type 3 = 总行/长三角地区/…（地区） |

未知分类码（未来上游若扩 `"4"`）的行跳过不透出；分类语义以上游 type 码为准，
**不按分部名猜**（银行把地区分部标在 type 3 下、把利息收入类标在 type 1/2 下
皆属上游自身分类，照抄）。

## 3. 字段映射（上游拼写原样照抄；占比 ×100 转百分比）

| 上游字段 | 输出字段 | 变换 |
|---|---|---|
| `REPORT_DATE`（"2026-06-30 00:00:00"） | `report_date` | 取日期部分 "2026-06-30" |
| `MAINOP_TYPE`（"1"/"2"/"3"） | `segment_type` | 按上表映射 |
| `ITEM_NAME` | `segment_name` | 原文照抄 |
| `MAIN_BUSINESS_INCOME` | `main_income` | 数值（元）；null 透出 |
| `MBI_RATIO`（小数 0.31127） | `income_ratio` | **×100 → 31.127（百分比）**；null 透出 |
| `MAIN_BUSINESS_RPOFIT`（上游拼写 RPOFIT） | `main_profit` | 数值（元）；null 透出（银行行业分部常无利润） |
| `GROSS_RPOFIT_RATIO`（上游拼写 RPOFIT，小数） | `gross_margin` | **×100 → 百分比**；null 透出（实测 SH600000 200 行中 131 行为 null） |

占比 ×100 的理由：上游以小数编码（0.31127 = 31.127%），manifest concept
`unit: percent`（仓库口径如 moa-hog-price 的 `mom_pct` 均为百分数值），×100 后
数值与声明单位自洽；"照抄不改"仅指字段名拼写（RPOFIT），不指数值编码。

## 4. 通道选择与实测证据（2026-11-07）

| 探测 | 请求 | 结果 |
|---|---|---|
| brief 给的旧前缀 | `GET …/PC_F10/BusinessAnalysis/PageAjax?code=SH600000` | **HTTP 302** → `/other.html?type=soft&color=b`（WAF 软拦截，路径级） |
| 现役前缀（本单元） | `GET …/PC_HSF10/BusinessAnalysis/PageAjax?code=SH600000` | **HTTP 200**，106 KB JSON，`zygcfx` 200 行 |
| 另一市场 | `GET …/PC_HSF10/BusinessAnalysis/PageAjax?code=SZ000001` | HTTP 200，103 KB，200 行 |
| 验证股 | `GET …/PC_HSF10/BusinessAnalysis/PageAjax?code=SH600519` | HTTP 200，67 KB，200 行 |
| 非法代码 | `GET …/PC_HSF10/BusinessAnalysis/PageAjax?code=SZ999999` | **HTTP 200** + `{"status":-1,"message":"股票代码不合法"}` |
| 变体探测 | `GET …/PC_HSF10/BusinessAnalysisNew/PageAjax?code=SH600000` | HTTP 302 软拦截（New 变体也被拦） |

→ 本机直连全绿，无需代理出口；manifest 不写 `egress_secret`、不写 `site`。

## 5. 口坑

- **旧 `PC_F10` 前缀 302 软拦截**：拦截是路径级的（无 UA、无 Referer 的 curl 默认
  请求打 `PC_HSF10` 也是 200）；别按 brief/旧文档回退旧前缀。
- **错误响应也是 HTTP 200**：非法代码返回 `{"status":-1,"message":"股票代码不合法"}`，
  无 `zygcfx` 键——只能按结构判错，代码里缺 key/空列表一律显式红（含 symbol）。
- **每股 200 行硬上限**：请求侧无翻页参数（`pageSize:200` 是上游前端自己的上限，
  见其 JS `pageSize:200,sortColumns:"MAINOP_TYPE,RANK3"`），更早报告期拿不到；
  `limit` 语义定为**每股**上限，多股时不会互相挤占。
- **占比字段是小数不是百分数**：`MBI_RATIO`/`GROSS_RPOFIT_RATIO` 0.31127 表示
  31.127%，输出已 ×100（见 §3）；下游消费注意口径。
- **null 语义**：`MAIN_BUSINESS_RPOFIT`/`GROSS_RPOFIT_RATIO` 大量为 null（银行
  行业分部无利润/毛利率），输出 `null` 透出不造 0。
- **金融机构分类怪异**：浦发银行的"地区"分部（总行/长三角地区…）上游就标在
  type 3；语义以 type 码为准，不按名字猜。
- **响应偶发 gzip 编码**：即使请求 `Accept-Encoding: identity`，上游也偶发回
  gzip 体（urllib 不自动解压，实测 SZ000001 一次命中 `\x1f\x8b` 魔数）——代码
  请求 identity + 魔数兜底 `gzip.decompress` 双保险。
- **仅常规 UA、直连**：不做指纹伪装与频次对抗，遇风控升级按协议转人工。
- **失败即红**：HTTP 错误 / 非 JSON / 缺 `zygcfx` / 空列表 / 0 观测行 / 非法
  symbol → `RuntimeError`/`ValueError`（多股时任一股失败即整体失败，错误信息含
  symbol），绝不静默回空。

## 6. golden 断言口径（跨期稳定）

`golden/001-stock-zygc.json` 用 `{"symbols": "SH600000", "limit": 50}` 重放
（浦发银行每期必有行业/产品/地区三分部，实测每股 200 行），只锚结构常量：
`source=stock-zygc` / `segment_type=product`（浦发每期都有 product 分部）/
`symbol=SH600000` + `min_rows=3`。**绝不锚**收入金额/占比/毛利率数值（随报告期
滚动更新，且银行分部利润大量为 null）；`scraped_at` / `upstream_updated` 进
`whitelist_fields`。

## 7. 自检

```bash
python3 spiders/stock-zygc/spider.py            # 冒烟：打印 SH600000 最近 3 行
python3 scripts/validate_manifests.py           # manifest 契约校验
python3 scripts/check_manifest_commands.py      # manifest 命令漂移 lint
python3 scripts/conformance_gate.py             # 仓级机械闸
```
