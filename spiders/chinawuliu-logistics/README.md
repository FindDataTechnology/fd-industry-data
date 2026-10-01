# chinawuliu-logistics 接入档案

中国物流与采购联合会（CFLP，www.chinawuliu.com.cn）「学术研究-信息统计-统计数据」栏目，一个单元吃三口径：

| channel | 指数 | 发布频率 | 期号形态 | 数值单位 |
|---|---|---|---|---|
| `weekly-freight` | 中国公路物流运价指数（周指数报告） | 每周五（栏目实际隔周登载） | YYYY-MM-DD（报告日） | 点 |
| `lpi` | 中国物流业景气指数（LPI） | 月度 | YYYY-MM | % |
| `e-commerce` | 中国电商物流指数 | 月度 | YYYY-MM | 点 |

## 端点

- 栏目列表：`http://www.chinawuliu.com.cn/xsyj/tjsj/`，翻页 `index_2.shtml` … `index_11.shtml`（实测 `index_12` 起返回 404）。
- 文章页：`http://www.chinawuliu.com.cn/xsyj/YYYYMM/DD/<ID>.shtml`（纯静态路径，无查询参数）。
- **必须走 http://**：该主机 `https://` 握手失败（TLS 层直接断连，直连与 curl_cffi 均复现），站点仅在 80 端口可靠服务。
- 列表条目结构：`<li><a href="/xsyj/…" title="标题">标题</a><span class="time">YYYY/MM/DD HH:MM</span></li>`；
  文章页期数证据在 `<meta name="description">` 与 `<span class="new-unit">发布时间：YYYY-MM-DD HH:MM:SS</span>`。

## 鉴权

免鉴权，无 Cookie/Token。请求头仅 UA（Chrome120 指纹由 curl_cffi impersonate 提供）+ `Accept` + `Accept-Language: zh-CN`。

## 参数白名单（只发实测有效参数）

- 列表翻页仅 `index.shtml` / `index_{2..11}.shtml`；多余分页参数一律不发。
- 文章页仅 `.shtml` 静态 URL，无 query 参数。
- HTTP 全部走 `scrapling.fetchers.FetcherSession(impersonate="chrome120", timeout=25, verify=False, retries=1)`。

## 容错规则

- 非 200 / 空响应 / 网络异常 → 记录 WARNING 后跳过该 URL；`retries=1` 单次尝试，**不连环重试、不绕反爬**。
- 数值抽取：优先文章页 `meta description`（机器可读、直接含当期数值），正文 `div.text.mb-50` 兜底；
  meta 与正文都抽不到数值 → 该行不产出（宁缺勿脏，绝不写脏行）。
- 标题分类过滤：仅收三口径文章（周指数报告含「公路物流运价周指数」；LPI 需「物流业景气指数为」；电商需「电商物流指数为」），
  PMI/仓储指数/大宗商品周报/物流运行分析等一律跳过；分析类文章（标题无数值句式）天然不匹配规则。
- 列表页推荐位与列表条目重复 → 按 URL 去重。
- 翻页止损：某页全部条目早于回补窗口、或列表无条目、或第 1 页不可达（整体放弃）。

## 历史深度

- 栏目自 2015 年起可回溯（侦察口径）；本单元**首爬只回补 12 个月**（`BACKFILL_DAYS=370`，超出窗口的文章按发布日期跳过）。
- 12 个月 ≈ 列表前 10-11 页；每页 20 条，三口径文章约占 1/3。

## 真实取数证据

取数时间：**2026-10-02 04:21（本机 UTC 2026-10-01T20:21Z）**，`run_chinawuliu_logistics(limit=100)` 实跑。
网络结论：**【直连=39/代理=0】**（国内源直连，未用代理）。

三口径 12 个月回补（新→旧）：

| channel | 条数 | 首（最新）期 | 末（最旧）期 |
|---|---|---|---|
| weekly-freight | 20 | 2026-08-21 = **1049.22 点**（环比 -0.05%）<br>http://www.chinawuliu.com.cn/xsyj/202608/21/668285.shtml | 2025-10-10 = **1049.35 点**（环比 +0.05%）<br>http://www.chinawuliu.com.cn/xsyj/202510/10/657253.shtml |
| lpi | 12 | 2026-08 = **50.9%**（环比 +0.5pp）<br>http://www.chinawuliu.com.cn/xsyj/202609/09/668584.shtml | 2025-09 = **51.2%**（环比 +0.3pp）<br>http://www.chinawuliu.com.cn/xsyj/202510/11/657278.shtml |
| e-commerce | 7 | 2026-08 = **111.5 点**（环比 +0.3 点）<br>http://www.chinawuliu.com.cn/xsyj/202609/15/668668.shtml | 2025-09 = **112.7 点**<br>http://www.chinawuliu.com.cn/xsyj/202510/16/657641.shtml |

总计 **39 行**（limit=100 未触顶；三口径合计在 12 个月窗口内的全部可解析文章）。
首末值均已与文章页 meta description 逐条核对一致（如 2025.10.10 期 meta 原文：「中国公路物流运价指数为1049.35点，比上周回升0.05%」）。

样例行（1 条，JSON）：

```json
{"period": "2026-08-21", "channel": "weekly-freight", "index_name": "中国公路物流运价指数", "value": 1049.22, "yoy": null, "mom": "-0.05%", "title": "中国公路物流运价周指数报告（2026.8.21)", "url": "http://www.chinawuliu.com.cn/xsyj/202608/21/668285.shtml", "scraped_at": "2026-10-01T20:20:57+00:00", "source_url": "http://www.chinawuliu.com.cn/xsyj/tjsj/"}
```

## 本地验证方法

```bash
cd /Users/chengsishi/finddata/fd-industry-data && .venv/bin/python - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("spider", "spiders/chinawuliu-logistics/spider.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
rows = m.run_chinawuliu_logistics(limit=5)
print(len(rows)); print(rows[0])
PY
```

## 遗留问题

- 周指数报告在该栏目约隔周登载（12 个月仅 20 期），非每周五全量；如需每周全量需扩到站内「指数报告」专栏，超出本 brief 三口径范围。
- 周指数数值为 1049 点量级（近两年口径），与月度稿「中国公路物流运价指数为105.2点」(百点量级) 量纲不同，跨 channel 比较需按 `index_name` 区分。
- `yoy` 字段现网文章 meta 很少携带同比表述，多数期为 `null`；属源数据现状，非解析缺陷。
