# chinawealth-reports —— 中国理财网 银行业理财市场报告（元数据 + PDF 直链）

新源接入档案（批次六·银行业，change `add-banking-industry-sources` 任务 5.2）。
不下载、不解析 PDF 正文：本单元交付**报告元数据**（期号/标题/类型/发布日/发布
机构/详情页 URL/PDF 直链），正文数值若后续需要，另走 `fd_industry_data.pdftext`。

## 1. 端点与实读形态

| 项 | 值 |
|---|---|
| 栏目页 | `https://www.chinawealth.com.cn/lc_lcsj/lcbg/`（首页 = `index.html`） |
| 分页 | 静态 HTML，`pageCount` 写在首页内联脚本：`let pageCount = Number('3')`；第 1 页 `index.html`，第 k 页（k≥2）= `index_{pageCount-k+1}.html` |
| 实测页数/条数 | 3 页 / 28 条（10 + 10 + 8），2026-11-08 |
| 详情页 | 列表 href 形如 `./202610/t20261021_855467147.html`，相对栏目页解析 → `…/lcbg/202610/t20261021_855467147.html` |
| 附件直链 | 详情页 `data-recurl` 属性；值形如 `./P020261021528431241365.pdf`，**相对详情页目录**，需对详情页 URL 做 urljoin |
| 请求头 | 常规 Chrome UA + `Referer: https://www.chinawealth.com.cn/lc_lcsj/lcbg/`；实测 200，无 403/302/验证页 |
| robots.txt | `GET /robots.txt` → **404**（无 robots 声明）；按 ≥1s 间隔串行抓取 |
| 反爬 | 本轮 31 次请求（1 首页 + 2 列表页 + 28 详情页）全部 200，未见频控 |

### 1.1 `data-recurl` 的原文形态（实测片段）

附件**不是** `<a href>`，而是空 `<li>` 上的属性，由页面内联 JS 拼出链接：

```html
<div class="news_down">
    <div class="news_dtit">附件下载</div>
    <ul class="news_dlist">
        <li class='file-list' data-recurl='./P020261021528431241365.pdf'
            data-sourceName='P020261021528431241365.pdf'
            data-download='中国银行业理财市场季度报告（2026年三季度）.pdf'
            data-sern='1' data-othertion='1'>
        </li>
        <script>
            $(".file-list").each(function(i, ele){
                var recurl = $(ele).data("recurl");
                …
                var html2 = '<a href="' + recurl + '"' + 'target="_blank">' + sern + '.' + download + '[点击预览]</a>';
                …
            })
        </script>
```

所以静态 HTML 里既没有 PDF 的 `<a>`，也没有绝对地址；只能读 `data-recurl`
再按详情页目录解析。`data-download` 是发布方给的下载名（早期几期带书名号
`《…》`，如实保留）。

### 1.2 列表项原文形态

```html
<li>
  <div class="news_text news_width">
    <h2 class="news_name">
      <a title="中国银行业理财市场季度报告（2026年三季度）"
         href="./202610/t20261021_855467147.html" target="_blank">
         中国银行业理财市场季度报告（2026年三季度）
      </a>
    </h2>
    …
    <div class="news_other">
      <b>来源：</b> 银行理财登记中心
      <span class="news_date">2026-10-21</span>
    </div>
  </div>
</li>
```

### 1.3 分页后缀不是正序（硬坑）

栏目页 `jumpTo(page)` 用 `pageCount - page + 1` 生成后缀，`createPageHTMLApp`
的「下一页」也按 `index_{pageCount-1}.html` 走。实测：

| 逻辑页 | URL | pageIndex |
|---|---|---|
| 1 | `index.html` | 0 |
| 2 | `index_2.html` | 2 |
| 3 | `index_1.html` | 1 |

`index_3.html` → **404**。因此 `pages` 参数不能按 `index_{k}.html` 直拼；
每次运行先读首页 `pageCount`，再用 `pageCount-k+1` 反推。新报告发布后
`pageCount` 会变（如 3→4），后缀整体平移，硬编码必坏。

## 2. 输出字段（10 键，与 manifest `columns` 一一对应）

| 字段 | 来源 | 说明 |
|---|---|---|
| `title` | 详情页 `<h4 title=… class="news_tit">`，回退列表 `title` | 两处不一致时取详情并留痕 |
| `issue` | 标题括号内文字 | `2026年三季度` / `2026年上` / `2016` / `2017上半年`；多期合并公告用 `、` 连接（`2013年、2014上半年`） |
| `doc_type` | 标题字面分类 | `quarterly` / `half_year` / `annual` / `mixed`（规则见 §2.1） |
| `published_at` | 详情页 `发布时间：YYYY-MM-DD`，回退列表 `news_date` | |
| `publisher` | 详情页 `文章来源：…` | `银行理财登记中心` / 早年为 `中央结算公司`、`全国银行业理财信息登记系统`；2013 年报页无此字段 → null |
| `report_url` | 列表 href → 绝对地址 | 稳定身份 |
| `pdf_url` | `data-recurl` → 对详情页 urljoin | 27/28 条有；1 条纯文字公告为 null |
| `pdf_filename` | `data-download` | 无附件时 null |
| `source` | 常量 | `chinawealth-reports` |
| `scraped_at` | 运行开始时间 | ISO 带时区 |

排序：`published_at` 倒序，同日按 `report_url` 倒序（确定性）。`limit` 在排序后截断。

### 2.1 类型规则（优先级从上到下）

1. 标题含「季度」→ `quarterly`（4 条）
2. 同时含年度标记（`年度报告` / `年报` / `报告（YYYY`）与半年标记（`半年`/`上半年`），
   且标题以 `、` 并列两个报告 → `mixed`（1 条：2013 年报 + 2014 上半年合并公告）
3. 含「半年」或「上半年」→ `half_year`（10 条；含上游误标为「年度报告（2014年上半年）」的一期）
4. 其余 → `annual`（13 条）

实测分布：annual 13 / half_year 10 / quarterly 4 / mixed 1 = 28。

## 3. 实测取数证据（2026-11-08）

`run_chinawealth_reports(limit=60, pages=3)` → **28 行 / 42.2 秒 / 31 次请求**
（1 首页 + 2 列表页 + 28 详情页，间隔 1.0s，详情页为耗时主体）：

```
[chinawealth-reports] 第 1 页 10 条（pageCount=3）
[chinawealth-reports] 第 2 页 10 条（index_2.html）
[chinawealth-reports] 第 3 页 8 条（index_1.html）
[chinawealth-reports] 无 PDF 附件（纯文字公告）：…/202404/t20240423_853607077.html
```

样例行（最新一期）：

```json
{"title":"中国银行业理财市场季度报告（2026年三季度）","issue":"2026年三季度",
 "doc_type":"quarterly","published_at":"2026-10-21","publisher":"银行理财登记中心",
 "report_url":"https://www.chinawealth.com.cn/lc_lcsj/lcbg/202610/t20261021_855467147.html",
 "pdf_url":"https://www.chinawealth.com.cn/lc_lcsj/lcbg/202610/P020261021528431241365.pdf",
 "pdf_filename":"中国银行业理财市场季度报告（2026年三季度）.pdf",
 "source":"chinawealth-reports","scraped_at":"2026-11-08T00:33:30+08:00"}
```

PDF 直链抽检：`GET …/P020261021528431241365.pdf` → **200 / `application/pdf` /
763,528 bytes / 魔数 `%PDF-1.7`**（只验证可达，不落正文）。

## 4. 失败策略

- 首页不可达/无条目 → `RuntimeError`（不返回空表冒充成功）。
- 第 2..N 页失败或解析出 0 条 → 留痕并停止翻页，保留已得条目。
- 单个详情页失败 → 留痕，**仍用列表元数据出行**（title/published_at 来自列表，
  `pdf_url`/`publisher`/`pdf_filename` 为 null），不丢整条报告。
- 无 `data-recurl` 的公告 → 正常出行，`pdf_url=null` 并留痕（实测 1 条：
  2013 年报 + 2014 上半年合并公告，正文为纯文字、无附件）。
- `limit<=0` 或 `pages<=0` → 返回 `[]`；`pages` 超过上游 `pageCount` → 按
  `pageCount` 截断（不 404 试探）。

## 5. golden 口径与理由

`golden/001-chinawealth-reports.json` 用 `pages=3` 取全量 28 条，锚：

- `min_rows: 28`（全量条数；上游只增不减，新增期发布后仍 ≥28）
- `issue=2026年三季度` + `title=中国银行业理财市场季度报告（2026年三季度）`
  + `published_at=2026-10-21` —— 交付时最新一期的**元数据快照**
- `pdf_url` 固定到该期 PDF（`P020261021528431241365.pdf`；TRS 内容号，
  不随后续发布变化）
- `doc_type` 三值 + `publisher=银行理财登记中心` 做结构锚（证明分类与详情页
  解析链路整体健康，而不只是"最新一条恰好命中"）
- `issue=2013年、2014上半年` + `pdf_filename=中国银行业理财市场年度报告2013.pdf`
  锚住「多期合并标题」与「无书名号命名」两个边界形态

**为什么锚 2026年三季度而不是"动态最新一期"**：列表是滚动累积的，新一期发布
只会把它往后挤，不会改写它的标题/发布日/PDF 文件名——这三个值都是历史常量，
所以 golden 不需要随发布人工刷新。代价是它依赖 `pages` 深度：当前 28 条 /
3 页，20 条新报告（约 5 年，季度+半年+年度节奏）之后才会掉出前 3 页；届时把
golden `params.pages` 与 `min_rows` 一起上调即可（README 留痕）。这与
`chinabond-monthly` 用 `period` 钉历史月的思路一致，但本单元不需要额外入口参数。

`whitelist_fields: ["scraped_at"]`——唯一每次运行必变的字段。
