# cpcaauto-monthly — 乘联会乘用车市场月度分析

中国汽车流通协会**乘用车市场信息联席分会**（CPCA，俗称「乘联会」）「车市解读」频道
的月度/周度市场分析报告。乘联会是中国乘用车零售/批发销量的权威口径发布方，
其月度分析的「零售/批发/出口/新能源渗透率」数值被行业与监管广泛引用；对
普通消费者与下沉市场研究者而言，是判断车市景气与「油电替代」节奏的第一手
公开来源。

- 站点：<https://www.cpcaauto.com/>
- 频道：`news.php?types=csjd`（车市解读）
- 排期：`31 6 * * *`（每日 06:31 探测；月度分析多在次月 8-12 日发布）
- 入口：`run_cpcaauto_monthly(limit=50, max_pages=3, category=None) -> list[dict]`
- 依赖：标准库 `asyncio/re/logging/datetime/urllib` + **scrapling**（`lxml` 随之而来）。
  **无 PDF 库、无 Excel 库、无新增第三方依赖**。

## 两段通道

| 段 | 请求 | 取什么 |
|---|---|---|
| 发现 | `GET news.php?types=csjd&page=N` | `<li class="q">` 内 `<span>2026-10-12 </span>` + `<a href="newslist.php?types=csjd&id=4350">【月度分析】…</a>`；20 条/页，实测共 84 页 |
| 正文 | `GET newslist.php?types=csjd&id=<N>` | `div.read_content` → `div.tit` 标题 / `div.view` 发布时间与附件链接 / `div.text` 正文 |

实测（2026-10-13）：

| article_id | 标题 | 类别 | 期间 | 正文 |
|---|---|---|---|---|
| 4350 | 【月度分析】2026年9月份全国乘用车市场分析 | 月度分析 | 2026-09 | 10,240 字 |
| 4349 | 【周度分析】车市扫描(20260928-0930) | 周度分析 | 2026-09 | 4,690 字 |
| 4348 | 【新能源】2026年9月新能源乘用车厂商批发销量快讯 | 新能源 | 2026-09 | 936 字 |
| 4347 | 【联合发布】重点新车上市预告(2026年10月） | 联合发布 | 2026-10 | 2,484 字 |

正文内含的真实数值样本（id=4350）：

> 2026年9月全国乘用车市场零售 **161.8 万辆**，同比下降 28.0%，环比增长 5.0%；
> 1–9 月乘用车累计零售 1,333.4 万辆，同比下降 21.7%。
> 9 月新能源零售渗透率攀升至 **67.0%**，批发渗透率达 65.4%。
> 9 月乘用车出口（含整车与 CKD）**91.7 万辆**，同比增长 73.7%，环比增长 3.2%。

## 坑位（全部实测）

1. **列表必须走 `news.php`**。`newslist.php?types=csjd` **不带 `id`** 时返回 PHP 的
   MySQL 报错壳：`Table 'cpca.ecs_books_csjd' doesn't exist`（错误码 1146）或
   `You have an error in your SQL syntax ... near ''`（错误码 1064）。HTTP 状态仍是
   200，长度约 496 字节——**不看长度就会把报错壳当列表页**。带 `anid` 的变体
   （`news.php?types=csjd&anid=120`）实测 0 条，同不可用。
2. **必须指纹伪装**。裸 UA 返回 33 字节「请使用浏览器访问本站」。本单元用
   `FetcherSession(impersonate="chrome")`（仓内已有 4 个单元同机制：
   carbon-monitor、chinawuliu-logistics、sc-gd-stats、nbs_gdp），并在 `_fetch`
   里加一道拦截页判别（正文页不可能 < 80 字）。
3. **标题前导 `&nbsp;`**。列表锚文本以 `&nbsp;&nbsp;` 开头，解码后是 `\xa0`。
   `_LEAD_NBSP_RE` 去掉它，否则类别正则 `^【` 匹配不上。
4. **正文是叙述文，不是表**。所以本单元**不抽数值**：`content` 存正文纯文本，
   `period`/`category` 由标题锚定。这是有意为之——强抽数值会把「同比下降 28.0%」
   这类相对量与「161.8 万辆」这类绝对量混在一个列里，语义无法自洽。下游若需
   数值，按 `report_title` 的期间与正文段落自行解析。
5. **附件是 `.rar`，不下载不解析**。`div.view` 里有 `/upload/bookpic/*.rar`
   （原始报告包），仅抽成 `attach_url` 溯源列；代码从不请求它。
6. **无间隔连发会拿到 200 空壳**。实测无节流连取正文时，站点对部分请求返回
   **HTTP 200 但正文 0 字**（页面壳仍在），单条被跳过——首爬即有 31 行里若干条
   被此形态漏掉。加 `SAME_HOST_GAP_S = 0.4`（仓内同约定，见 `cninfo-operating`）
   后同批 id 全部可解析。**这是本源的硬性礼貌要求，不是可选优化**。
7. **`period` 的兜底规则**。标题取不到「YYYY年M月」时（如纯快讯）退回发布月的
   上一月，因为月度分析在次月发布；取不到就记 `None`，**不猜、不填 0**。

## 容错

- 单次尝试，不重试（`retries=1`）；单 URL 失败记 warning 后跳过，不影响整批。
- 正文块缺失 / 正文 < 80 字 → 该条跳过并留痕，不产出半条记录。
- 去重键 = `article_id`；`limit` 真实生效（循环内早停 + 出口截断）。
- `page` 上限 = `min(尾页号, max_pages)`，默认最多走 3 页（60 条报告）。

## 未纳入范围

`types=news`（行业新闻）与 `types=bgzl`（上海上牌数/京城车市/进口车/二手车/价格指数/
热点评述/新能源月报/商用车月报/政策分析）实测可达，但前者是资讯非数据、后者口径
未核，本轮不接。如后续要接，各自独立立项（`news.php` 的 `types` 参数即入口）。

## 自检

```bash
python3 -m py_compile spiders/cpcaauto-monthly/spider.py
python3 scripts/validate_manifests.py
python3 scripts/conformance_gate.py --roots .
```
