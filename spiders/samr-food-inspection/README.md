# samr-food-inspection（市场监管总局食品安全抽检公告）

国家市场监督管理总局（SAMR）政务公开 → 法定主动公开内容 → 食品抽检司栏目的
食品安全监督抽检公告/通告爬虫单元。入口函数：
`run_samr_food_inspection(limit: int = 20, max_pages: int = 2) -> list[dict]`。

一条公告产 **1 行**（announcement 级）：标题、发布日期、URL、字段级口径分类
（`result_category`）与餐饮相关标注（`restaurant_related`）、正文摘要（`body_digest`）。
本单元为下游「抽检不合格过滤」规则提供**字段级过滤依据，无需正文全文重爬**。

## 端点与取数路径（实测 2026-11-07，UTC+8；国内源，仅直连）

- **栏目壳页**：`https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/spcjs/`（UTF-8，实测 200）
  - 该页是博达 jPaas 壳页：列表由前端脚本 `AuthorizedRead/unitbuild.js` 调
    `/api-gateway/jpaas-publish-server/front/page/build/unit` 渲染，壳页静态 HTML 里带完整调用参数：
    `<script ... src="...unitbuild.js" webId="29e9522…" url="/api-gateway/jpaas-publish-server/front/page/build/unit" queryData="{'parseType':'bulidstatic','webId':'…','tplSetId':'…','pageType':'column','tagId':'内容区域','editType':'null','pageId':'c50b450d…'}">`。
    本单元每次运行**实时解析壳页**取 webId/pageId/tplSetId/tagId（不硬编码 pageId），
    解析失败 = RuntimeError（硬失败契约）。
- **列表 API**：`GET https://www.samr.gov.cn/api-gateway/jpaas-publish-server/front/page/build/unit`
  带 `parseType=bulidstatic / webId / tplSetId / pageType=column / tagId=内容区域 /
  editType=null / pageId / paramJson={"pageNo":N,"pageSize":20}`，
  返回 `{"code":"200","data":{"html":"<li class=\"nav04Left02_content\">…"}}`。
  - 每页 20 条，站点标称 count=228（rows=20 → 12 页，末页 8 条）。
  - **分页参数必须放 `paramJson`**：实测把 `pageNo=2` 直接放 URL 参数会返回第 1 页
    （page.js 的分页实现是 `queryData.paramJson = JSON.stringify({pageNo, pageSize})`）。
  - 条目结构：`<li class="nav04Left02_content"><a href="/zw/.../art_<hash>.html" title="标题">标题</a></li>`
    紧跟 `<li class="nav04Left02_contenttime">2026-10-12</li>`（发布日期）。
- **正文页**：`https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/spcjs/art/<年>/art_<hash>.html`
  - 服务端整页渲染（meta `pagetype=1`）：正文块 `div.Three_xilan_07`
    （公告标题 + 文号 + 段落），附件链接在 `ul.contentLeft0102box`
    （`<a href="….xlsx" title="附件9 餐饮食品监督抽检不合格产品信息.xlsx">`）。
  - 页头元数据：`<meta name="ArticleTitle">`、`<meta name="PubDate" content="2026-10-12 19:01">`、
    `<meta name="Description">`（正文摘要，作兜底）。

### 直连说明

国内政府站点，**仅直连**（scouting book 的代理规则不适用本源）。无鉴权、无验证码、
无 cookie 要求；未观测到频控。`*.samr.gov.cn` 证书为 **CFCA EV**
（issuer=China Financial Certification Authority CN=CFCA EV OCA，notAfter=2027-01-09），
按仓内惯例 **verify=False**（不假设 Linux 容器信任库含 CFCA 根）。
请求间 0.5–0.9s 抖动限速，timeout=30s，`retries=1` 单次尝试。

## 字段级口径（任务 2.4 核心契约）

### result_category 分类规则（编码于 spider.py 的 TITLE_RULES / BODY_RULES）

**判定次序：标题规则优先（体裁由标题决定）；标题判不出才用正文规则兜底；
正文 = 正文块文本 + 附件名。**

| 档 | 标题规则（首条命中即返回） | 正文兜底规则 | 语义 |
|---|---|---|---|
| `other` | `快速检测\|补充检验方法` | `(季度\|年度\|半年\|上半年)[^。]{0,12}抽检\|抽检情况` | 快检/补充检验方法公告、季度/年度抽检情况综合统计 |
| `other` | `非食用物质名录\|废止\|工作规范\|管理规定\|建议的答复\|核查处置\|召回` | — | 名录/废止/规范/答复/核查处置/召回监管类 |
| `unqualified` | `不合格` | `检出[^。]{0,14}批次[^。]{0,8}不合格\|不合格食品\|不合格产品信息` | 具体批次抽检不合格通报/通告（总局栏目只公开发布不合格结果，绝对主力） |
| `qualified` | `抽检[^。]{0,20}合格`（不含「不合格」时才轮得到） | `全部合格\|均未检出` | 全部合格类通告（**防御性分类，2022 年以来栏目内未见实例**） |
| `other` | `抽检情况` | — | 「监督抽检情况」季度/年度综合统计通告 |
| （判不出） | — | — | `None`（缺失=None，不猜） |

**矛盾处理（口径与文本矛盾时跳过留痕，不猜）**：标题与正文**同时**给出抽检结果类
结论（都在 {unqualified, qualified} 内）且相左 → 该条**不产出行**，记 warning 留痕。
`title=other` 是体裁结论，与正文引用「不合格」字样**不构成矛盾**（如对人大代表建议的
答复会复述抽检不合格情况），照常按 other 出行。

### restaurant_related 标注规则

标题 + 正文块 + 附件名中命中 `餐饮|食堂|餐馆`（餐饮食品/餐饮具/餐饮服务/餐饮单位的
前缀族；**附件名是批次通报分类覆盖面的唯一载体**——正文块常不写「餐饮」二字，而
「附件9 餐饮食品监督抽检不合格产品信息.xlsx」只在附件区出现，必须纳入检索面）。
**纯标注列，绝不做过滤**。命中 = True，未命中 = False。

### notice_date / body_digest

- `notice_date`：**列表页** `contenttime` 的 `YYYY-MM-DD`（锚定日期）；列表缺日期时
  回退正文 meta PubDate 的日期部分；两者皆缺 = None。列表与 meta 日期不一致时
  **保留列表页日期**并记 warning（date drift 留痕）。
- `body_digest`：正文块 `div.Three_xilan_07` 去空白文本前 **200** 字符；正文块缺失
  （站点改版）回退 meta Description；再缺 = None。**下游凭 body_digest + result_category
  即可做字段级过滤，无需全文重爬。**

## 容错规则（spec 契约）

- `FetcherSession(impersonate="chrome120", timeout=30, verify=False, retries=1)`：
  单次尝试，不重试、不绕反爬。
- **硬失败**：栏目壳页取不到（网络/非 200）或壳页里解析不出 jPaas queryData
  → `raise RuntimeError`（列表不可用 = 单元不可用，如实上抛）。
- **软失败**：单篇正文页取不到 → 记 warning 跳过该条，不阻塞其他条；
  单字段缺失 → 该字段记 `None`（**绝不用 0/占位值/猜测填充**）。
- 列表按发布时间倒序逐页取，`limit` 真实生效（返回行数 ≤ limit）；
  仅当未凑满 limit 才翻页，`max_pages`（默认 2）限制翻页深度。
- 行级去重键 = 正文页 URL（跨页重复条目只取一次）。

## 字段清单（8 列）

| 列 | 说明 |
|---|---|
| `notice_date` | 公告发布日期（ISO YYYY-MM-DD，列表页锚定） |
| `title` | 公告标题（列表页锚定，空白折叠） |
| `url` | 正文页 URL |
| `result_category` | `unqualified` / `qualified` / `other`（口径见上表；判不出 = None） |
| `restaurant_related` | bool，餐饮相关标注（annotation only，不过滤） |
| `body_digest` | 正文块去空白前 200 字符 |
| `source` | 固定 `samr-food-inspection` |
| `scraped_at` | UTC ISO 时间 |

## 真实取数证据

- 取数时间：**2026-11-07 20:27~20:33（UTC+8）**，Python 3.13.13 + scrapling 0.4.12 + lxml 6.1.1，
  经 `fd_industry_data.loader.load_spider_module('samr-food-inspection','spiders')` 装载执行。
- **首爬全量**：`run_samr_food_inspection(limit=20)` → **20 行 / 20 篇公告 / 1 个列表页**
  （列表第 1 页恰好 20 条候选，limit 未截断）：
  - `result_category` 分布：**unqualified 17 / qualified 0 / other 3**
    （other = 13 项快检方法公告、补充检验方法公告 2026 年第 24 号、废止 4 项方法公告）；
  - `restaurant_related`：True 17 / False 3（每篇批次通报的附件都含「餐饮食品监督抽检
    不合格产品信息.xlsx」）；
  - 无 None 字段（20 行 × notice_date/title/url/result_category/restaurant_related/body_digest 全非 None）；
  - 合法性：result_category 全部落在三值枚举内，restaurant_related 全为 bool。
- **样例行（未加工）**：

```json
{"notice_date": "2026-10-12", "title": "市场监管总局办公厅关于52批次食品抽检不合格情况的通报", "url": "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/spcjs/art/2026/art_5b7200efca0644f593c3fd6659b20a22.html", "result_category": "unqualified", "restaurant_related": true, "body_digest": "市场监管总局办公厅关于52批次食品抽检不合格情况的通报市监食检发〔2026〕124号近期，市场监管总局组织食品安全监督抽检1940批次样品，检出52批次样品不合格。产品抽检结果可查询https://spcjsac.gsxt.gov.cn/。对抽检发现的不合格食品，有关省级市场监管部门已组织开展核查处置。现将监督抽检不合格食品具体情况通报如下：（一）湖北省楷式梵商贸行（店铺名为楷式梵食品店3）在淘宝", "source": "samr-food-inspection", "scraped_at": "2026-11-07T12:27:56.744904+00:00"}
```

```json
{"notice_date": "2026-09-28", "title": "市场监管总局关于发布《蔬菜水果中三唑磷的快速检测 胶体金免疫层析法》等13项食品快速检测方法的公告", "url": "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/spcjs/art/2026/art_721205f6c1a247ffbff1daa4c7a5eb98.html", "result_category": "other", "restaurant_related": false, "body_digest": "根据《中华人民共和国食品安全法》有关规定，市场监管总局批准发布《蔬菜水果中三唑磷的快速检测胶体金免疫层析法》等13项食品快速检测方法。名称和编号如下：蔬菜水果中三唑磷的快速检测胶体金免疫层析法（KJ202601）蔬菜水果中戊唑醇的快速检测胶体金免疫层析法（KJ202602）蔬菜水果中百菌清的快速检测胶体金免疫层析法（KJ202603）蔬菜水果中吡唑醚菌酯的快速检测胶体金免疫层析法（KJ202604", "source": "samr-food-inspection", "scraped_at": "2026-11-07T12:27:56.744904+00:00"}
```

## 字段级口径人工判定 vs 解析器输出（验收准则：抽样批次样本过滤结果与人工判定一致）

人工通读 6 篇真实公告（标题 + 正文 + 附件名）给出判定，再跑解析器比对——**6/6 全一致**：

| 公告（发布日） | 人工判定（读原文） | 解析器输出 | 一致 |
|---|---|---|---|
| 关于52批次食品抽检不合格情况的通报（2026-10-12，市监食检发〔2026〕124号） | unqualified（抽检不合格通报；附件 2–12 含「餐饮食品监督抽检不合格产品信息」→ 餐饮相关） | `unqualified` / `restaurant_related=True` | ✅ |
| 关于发布《蔬菜水果中三唑磷的快速检测 胶体金免疫层析法》等13项食品快速检测方法的公告（2026-09-28） | other（检验方法公告，非抽检结果） | `other` / `restaurant_related=False` | ✅ |
| 关于进一步加强跨境电商零售进口食品召回监管的公告（2026-02-13，总局 商务部） | other（召回监管制度类） | `other` / `restaurant_related=False` | ✅ |
| 关于35批次食品抽检不合格情况的通告（2025-07-25，2025年第10号） | unqualified（抽检不合格通告，餐饮相关） | `unqualified` / `restaurant_related=True` | ✅ |
| 关于2024年第三季度市场监管部门食品安全监督抽检情况的通告（2024-11-07） | other（季度综合统计，非批次结果） | `other` / `restaurant_related=False` | ✅ |
| 对十四届全国人大三次会议第7864号建议的答复（2025-07-02） | other（建议答复；正文虽引用抽检不合格工作内容，体裁仍是答复——验证「title=other 不与正文矛盾」的次序规则） | `other` / `restaurant_related=True` | ✅ |

另做整窗一致性核对：首爬 20 行的分类与列表页标题清点（17 篇「…抽检不合格情况的通报」
+ 3 篇公告）**零不一致**。
矛盾跳过分支与 body 规则兜底分支为防御性实现（实测 99 期存量标题全部可由标题规则判定，
无矛盾案例），触发路径留 warning 日志。

## 历史深度与周期

- 列表 API `count=228`（rows=20 → 12 页）；末页（pageNo=12）最早条目 **2019-02-22**
  （「关于12批次食品不合格情况的通告」）。本单元默认 `max_pages=2`（≈40 条候选），
  需要更深历史时调大即可，采集逻辑无需改动。
- 发布节奏不规则（`data_period: irregular`）：实测 2026-01~10 共 16 条，月均 1~3 条、
  突发成批（如 2026-06 月内 4 条）；2022-2024 年另有季度综合统计通告。
  因此轮询频率取 `weekly`（保证周内捕获，成本极低），健康巡检不判超期。

## 遗留风险

1. **jPaas 壳页契约**：列表依赖壳页 `<script>` 标签里的 webId/pageId/tplSetId；站点改版
   换 CMS 时解析失败会按硬失败契约抛 RuntimeError（fail loudly，不静默空转）。
2. **分页参数通道**：`paramJson` 承载分页是站点前端实现细节（page.js）；若站点改用
   静态分页（index_N.html），翻页逻辑需随之调整（limit=20 内的单页取数不受影响）。
3. `qualified` 档为防御性分类：总局栏目 2022 年以来未发布过「全部合格」类通告；若未来
   出现新体裁（标题判不出），该行 result_category=None 并留 warning，届时补规则即可。
4. 批次通报的分类覆盖面只体现在附件名（xlsx）；若站点未来不再发布附件或改为图片表格，
   restaurant_related 会系统性失真（warning 不可见）——届时需升级为附件内容解析。
