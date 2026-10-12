# fangchan-market — 中房网房地产市场监测报告

中国房地产业协会官方网站**中房网**（www.fangchan.com）的房地产市场监测报告。
中房网是中房协的官方发声渠道，其月度/不定期的市场分析（重点城市成交、房企
销售、政策解读）是房地产行业与购房者判断市场走向的公开口径来源之一。

- 站点：<http://www.fangchan.com/>（频道 `/news/320/`）
- 数据接口：`http://news.fangchan.com/api/newslist.json`
- 排期：`41 6 */2 * *`（每 2 日 06:41 探测）
- 入口：`run_fangchan_market(limit=50, max_pages=2, subcolumn="原创", page_size=40) -> list[dict]`
- 依赖：标准库 `asyncio/json/logging/re/urllib/datetime` + **scrapling**。
  **无 PDF 库、无 Excel 库、无新增第三方依赖**。

## 两段通道

| 段 | 请求 | 取什么 |
|---|---|---|
| 发现 | `GET news.fangchan.com/api/newslist.json?subcolumn=<栏目>&page=N&pagesize=M` | `{total, data:[{title, url, createtime, media, zhaiyao, keyword, pic}]}`；条目直接带详情页 `url` |
| 正文 | `GET <url>`（`www.fangchan.com`） | `div.summary-text-p` 内正文段落（已剥分享/二维码噪音） |

实测（2026-10-13）：

| article_id | 标题 | 发布日 | 正文 |
|---|---|---|---|
| 7515241843282744309 | 9月重点城市新房成交环比企稳，二手房交易同比保持正增长 | 2026-10-12 | 1,852 字 |
| 7514495726509167279 | 典型房企9月业绩环增15.7%，整体投资意愿仍处低位 | 2026-10-10 | 1,641 字 |
| 7510925446289363622 | 关于贷款贴息新政，哪些群体会直接受益？ | 2026-09-30 | 1,994 字 |

正文内含的真实数值样本（id=7515241843282744309，全部经抽取后复核仍在文内）：

> 9 月全国重点 **50 城**新建商品住宅新增供应面积约 **1368 万平方米**，环比大增
> **103.6%**，单月供应规模创 2025 年 12 月以来新高；同比下降 15.7%。
> 成交面积约 **1020 万平方米**，环比增长 2.4%，同比下降 **20.1%**。
> 二手房成交规模约为新房的 **1.4 倍**。
> 9 月上海以 193 万平方米居全国首位，环比增长 10.4%、同比增长 26.3%。

## 栏目白名单（实测）

| 栏目 | total | 是否纳入 |
|---|---|---|
| 原创 | 898 | ✅ 默认 |
| 政策 | 7011 | ✅ |
| 市场 | 3742 | ✅ |
| 数据 / 研究 / 年鉴 / 快报 / 测评 / 报告 | **0** | ❌ 实测空栏目，入白名单只会得到空结果 |

`subcolumn` 参数按原样透传，未来新栏目无需改代码；但**上述六个空栏目不要用**。

## 坑位（全部实测）

1. **频道页主列表是 JS 空壳**。`/news/320/` 的 `<ul class="item-news-list"></ul>`
   与相邻 `<div class="page"></div>` 服务端 HTML 里都是空的，内容由 JS 回填。
   服务端 HTML 里只有右栏「更多」区块的 8 条时间线。所以**必须走 JSON API**，
   否则一次最多只能拿到 8 条。
2. **五种分页路径全部 404**（实测）：`index_2.html` / `list_2.html` / `page/2.html` /
   `index_1.html` / `index_p2.html`。分页只能通过 API 的 `page` 参数。
3. **正文要剥两层噪音**。详情页正文在 `div.summary-text-p`，但同页还有分享控件
   （`div.tubiaofenxian`、`div.wechat-share-pic` 含「扫描二维码分享」）与相关阅读块。
   `_BODY_NOISE_XPATHS` 显式移除，否则正文会被「扫描二维码分享」等字样污染。
4. **`createtime` 是 unix 秒字符串**（如 `"1791773281"`），不是 ISO。
   `_iso_date` 按 **UTC+8** 落日期，与站点展示一致；`"0"`/空值/不可解析一律记 `None`。
5. **API 与站点不同域**。API 在 `news.fangchan.com`（乐居系），详情页在
   `www.fangchan.com`。本单元把 API 当发现层、站点当取数层；API 全挂时退化为
   频道页右栏时间线（最多 8 条）并记 warning，**不静默归零**。
6. **正文是叙述文，不是表**。所以本单元**不抽数值**：`content` 存正文纯文本。
   理由同 `cpcaauto-monthly`——相对量与绝对量混抽会让列语义不自洽。
7. **`pic_url` 指向 `ess.leju.com`**，仅作溯源列，代码从不下载。

## 容错

- 单次尝试，不重试（`retries=1`）；单 URL 失败记 warning 后跳过，不影响整批。
- 正文容器全缺失或 < 120 字 → 该条跳过并留痕，不产出半条记录。
- API 载荷非 JSON / `.data` 非列表 → `ValueError` 上抛被捕获，停止翻页并留痕，
  **不把错误载荷当空列表吞掉**。
- 去重键 = 详情页 URL；`limit` 真实生效（循环内早停 + 出口截断）。
- `page` 上限 = `max_pages`（默认 2 页 = 80 条）；API 不可用时退化通道不分页。

## 自检

```bash
python3 -m py_compile spiders/fangchan-market/spider.py
python3 scripts/validate_manifests.py
python3 scripts/conformance_gate.py --roots .
```
