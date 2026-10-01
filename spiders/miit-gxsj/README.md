# miit-gxsj 接入档案（工信部软件业/通信业月度运行数据）

- slug: `miit-gxsj`，入口: `run_miit_gxsj(limit: int = 100) -> list[dict]`
- frequency: monthly（工信部运行监测协调局按月发布上月运行情况，月末/次月初出刊）
- 行 schema: `channel / title / publish_date / url / metric / value / unit / excerpt / scraped_at / source_url`，每篇文章产 1..N 行（抽到几个数字写几行）

## 端点

| 用途 | 方法 | URL |
|---|---|---|
| 栏目文章列表（两栏通用） | **GET** | `https://www.miit.gov.cn/api-gateway/jpaas-publish-server/front/page/build/unit` |
| 文章正文页 | GET | `https://www.miit.gov.cn/gxsj/tjfx/{rjy\|txy}/art/<年>/art_<id>.html` |

- 栏目列表页（Referer 来源，也是页面侧 queryData 的出处）：
  - 软件业: `https://www.miit.gov.cn/gxsj/tjfx/rjy/index.html`，pageId=`50bf589f36614b7394522245a13fedae`
  - 通信业: `https://www.miit.gov.cn/gxsj/tjfx/txy/index.html`，pageId=`1434685f08314ae8ae78f78b6a5a7915`
- 响应结构: JSON `{success, code, data: {html}}`，文章列表（标题+链接+日期，最新在前）嵌在 `data.html` 字符串内（`<li><a title=.. href=..></a><span class="fr">YYYY-MM-DD</span></li>`）。
- 正文页正文容器为 `div.ccontent#con_con`；页头 meta（`PubDate/ArticleTitle/ColumnName`）可作发布日期兜底。

## 鉴权

无鉴权（无 cookie/token）。但站点前置 WAF：
- **裸请求 403**——`User-Agent`（Chrome 级 UA，FetcherSession `impersonate="chrome120"`）+ **`Referer`（对应栏目页 URL）必带**，实测缺一不可。
- **POST 表单会被拒**：POST 返回 HTTP 500 `{"message":"不支持的HTTP方法"}`；页面侧 `unitbuild.js` 也用 GET。**必须 GET**。
- 国内源：**直连为准，不走代理**。

## 参数白名单（只发实测有效参数，多余参数一律不发）

`build/unit` 的 7 个 query 参数全部来自栏目页 HTML 内 `queryData` 固化值，逐个实测有效：

| 参数 | 值 |
|---|---|
| `parseType` | `buildstatic` |
| `webId` | `8d828e408d90447786ddbe128d495e9e` |
| `tplSetId` | `209741b2109044b5b7695700b2bec37e` |
| `pageType` | `column` |
| `tagId` | `右侧内容` |
| `editType` | `null` |
| `pageId` | `50bf589f36614b7394522245a13fedae`（软件业）/ `1434685f08314ae8ae78f78b6a5a7915`（通信业） |

正文页：只发 URL 本身，无附加参数。列表中非 `/gxsj/*/art/*.html` 的链接（如年度统计专题页 `rjnj2024`）不入队，记录后跳过。

## 容错规则（spec 契约）

- HTTP 非 200（含 5xx/403）、空响应、非 JSON、`success!=true` → 记 warning 后跳过该栏目/文章，**不重试、不连环请求、不尝试绕反爬**。
- 正文解析失败或正则为空 → 留空跳过，不写脏行；数值无法 parse(float) 的匹配丢弃。
- `limit` 生效：两栏按「最新文章优先、栏目轮询」抓取，行数达 `limit` 即停；返回 ≤limit 行。
- 实现细节：scrapling 0.4 的 `resp.text` 对 JSON 响应为空，原始字节取 `resp.body` 再 decode。

## 历史深度

- 栏目接口一次返回 **24 条/栏**（软件业、通信业同），最新在首页顶部，无翻页参数（白名单外参数未测不发）。
- 实测列表覆盖到 **2024-12**（软件业最早为「2024年1－11月份软件业经济运行情况」2024-12-31；通信业最早为「2024年通信业统计公报解读」2025-01-26）。更早文章不在本接口首页，本期不取。

## 真实取数证据

- **取数时间**: 2026-10-01T20:17:29+00:00（北京时间 2026-10-02 04:17）
- **网络**: 【直连=9/9 全 200（栏目接口×2 + 正文页×7）；代理=未使用（国内源禁代理）】
- **总条数**: `run_miit_gxsj(limit=100)` → **100 行**（软件业 64 行/4 篇，通信业 36 行/3 篇）
- **序列首值**（第 1 行）: 软件业·2026年1—7月份软件业运行情况·软件业务收入 = **89785.0 亿元**（同比 +9.2%）
- **序列末值**（第 100 行）: 软件业·2026年1—4月份软件业运行情况·基础软件产品收入同比增速 = **9.1 %**

### 验证门槛（brief 任务 2.3）：两栏最新文章标题/日期/抽出数字

- **软件业**：《2026年1—7月份软件业运行情况》（2026-08-31）抽出 12 个数字，含：
  软件业务收入 89785 亿元（+9.2%）、软件业利润总额 10396 亿元（+1.3%）、软件业务出口 406.1 亿美元（+12.2%）等
- **通信业**：《2026年前7个月通信业经济运行情况》（2026-08-28）抽出 12 个数字，含：
  电信业务收入 1.02 万亿元（-2.1%）、电信业务总量同比 +7.7%、移动电话用户总数 18.47 亿户、5G 移动电话用户 13.03 亿户、5G 基站总数 515.4 万个、移动互联网累计流量 2587 亿GB（+17.6%）、DOU 23.81 GB/户·月（+13.9%）等

### 样例行（第 1 行，原样 JSON）

```json
{"channel": "软件业", "title": "2026年1—7月份软件业运行情况", "publish_date": "2026-08-31", "url": "https://www.miit.gov.cn/gxsj/tjfx/rjy/art/2026/art_3588c41c7dd34514868a93b3cb2c020c.html", "metric": "软件业务收入", "value": 89785.0, "unit": "亿元", "excerpt": "口保持正增长。 一、总体运行情况 前7个月，我国软件业务收入89785亿元，同比增长9.2%。软件业利润总额10396亿元，同比增", "scraped_at": "2026-10-01T20:17:29.614792+00:00", "source_url": "https://www.miit.gov.cn/api-gateway/jpaas-publish-server/front/page/build/unit?parseType=buildstatic&webId=8d828e408d90447786ddbe128d495e9e&tplSetId=209741b2109044b5b7695700b2bec37e&pageType=column&tagId=%E5%8F%B3%E4%BE%A7%E5%86%85%E5%AE%B9&editType=null&pageId=50bf589f36614b7394522245a13fedae"}
```

### 通信业样例行（正文第 1 行）

```json
{"channel": "通信业", "title": "2026年前7个月通信业经济运行情况", "publish_date": "2026-08-28", "url": "https://www.miit.gov.cn/gxsj/tjfx/txy/art/2026/art_d78fc2fbd1d9467286f9a71986bdafe7.html", "metric": "电信业务收入", "value": 1.02, "unit": "万亿元", "excerpt": "一、总体运行情况 前7个月，电信业务收入累计完成1.02万亿元，同比下降2.1%。 电信业务总量平稳增长", "scraped_at": "2026-10-01T20:17:29+00:00", "source_url": "…build/unit?…&pageId=1434685f08314ae8ae78f78b6a5a7915"}
```

## 遗留问题

- 栏目接口无翻页参数，历史深度以接口首页 24 条/栏为上限；更早数据需另行侦察（不在本期范围）。
- 指标白名单覆盖两栏月报的常见指标（收入/利润/出口/流量/用户/基站），个别低频表述（如固话用户、 IPTV 用户）暂未配置，漏抽不误抽。
- 下降/回落统一记为负值增速（如电信业务收入 -2.1%），下游需按带符号百分比消费。
