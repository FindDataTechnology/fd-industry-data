# cncotton —— 中国棉花网《棉花国际市场价格与贸易分析报告》（月度）

新源接入档案（`templates/new-source/` 范式）。
数据源：中国棉花网 `http://www.chinacotton.org/`（实测 2026-11-08，国内直连）。

## **必须用 `http://`（不是笔误）**

`https://www.chinacotton.org/` 的 **443 端口从未部署**，连接直接被 RST；
`http://www.chinacotton.org/` 返回 200（IIS 7.5 / ASP.NET）。改 https 会断流。
manifest 的 `source_url` 与 spider 的 `BASE` 都必须写 `http://`。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 列表 | `GET http://www.chinacotton.org/articleList.aspx?sort=6` | 「国际动态」：**单页列全 129 条**（articleID 408 倒序到 30），无分页 |
| 详情 | `GET .../articlePage.aspx?articleID=N` | **正文为空**，只有标题、发布时间、一个 PDF 附件链接 |
| PDF | `GET http://www.chinacotton.org/article/<ts>.pdf` | 实测 260–280KB / 3 页 |

兄弟栏目（本单元不接，供后续判断）：`sort=1` 生产监测报告（月更）、`sort=2` 供需平衡表
（仅到 2023-09）、`sort=4/5` 宏观与研究（无数值）。

入口：`run_cncotton(limit=30, window_start=None, window_end=None) -> list[dict]`
（`spider.py`）。标准库 + 平台共享层 `fd_industry_data.pdftext`。

## 数值从 PDF 文本层来，**不做 OCR**

详情页无正文，数字全在 PDF 里。这些 PDF **带真文本层**，用平台共享层
`fd_industry_data.pdftext`（`pdftotext` 单层包装）抽取即可。

- `pdftext` 明确不做 OCR：图片型 PDF 抽出空串 → 本单元**跳过并计数**，绝不猜。
- 因此本单元不触碰 2026-10-13「OCR 类源暂不处理」的裁决。
- `pdftext.available()` 为假（镜像缺 `poppler-utils`）时**直接抛错**——失败即红，
  不静默回空表。

## 字段口径

| 列 | 口径 |
|---|---|
| `period` | 报告期 `YYYY-MM`，取自标题（「2025年8月…」→ `2025-08`） |
| `forecast_season` | 正文的 USDA 预测年度（`2025/26`），随年度自动识别不硬编码 |
| `item` / `sub_period` | 见下表 |
| `value` / `unit` | 该行单位下数值；抽不到**不出行、不补 0** |
| `yoy_pct` / `mom_pct` | signed；「持平」记 **0.0**（不是 null）；原文缺方向词才 null |
| `quote_month` | 仅 Cotlook 行有值（报价所属月） |
| `pdf_url` | 数值实际来源的 PDF（`http`） |

| item | sub_period | unit | 含义 |
|---|---|---|---|
| `planted_area` | `forecast_season` | 万亩 / 万公顷 | 全球棉花播种面积（两行，同 yoy） |
| `production` | `forecast_season` | 万吨 | 全球产量 |
| `consumption` | `forecast_season` | 万吨 | 全球消费量 |
| `ending_stocks` | `forecast_season` | 万吨 | 全球期末库存 |
| `stock_use_ratio` | `forecast_season` | % | 库存消费比（`yoy_pct` = 百分点变化） |
| `cotlook_a_index` | `prev_month_avg` / `current_mtd_avg` | 美分/磅 | 上月月均价 / 本月截至某日均价 |
| `import_cum_volume` | `cumulative_Nm` | 万吨 | 我国 1—N 月累计进口 |
| `import_month_volume` | `month` | 万吨 | 我国当月进口（带同比+环比） |

## 三处实现细节（都是实测踩坑后定的）

1. **紧凑化后匹配**：PDF 会把句子折行切开（「44415 万\n亩」），所以先把所有空白
   去掉再跑正则。
2. **Cotlook 第二处不带年份**：第一处写「2025年7月Cotlook A…」，第二处只写
   「8月Cotlook A…（截至8月15日）」→ 年份正则必须可选，缺年份时用报告期年份补。
3. **方向词有多字形态**：「同比**减少**1个百分点」不是「减」+「少」；方向组按
   `增加|减少|上涨|下跌|增|减|涨|跌|降|持平` 排序（长词在前），否则丢值。

## 历史深度与停更（如实声明）

- 可回溯到 articleID 30 一代（2018 年前后），但**每期都要下一份 PDF**，故按 `limit`
  从最新往旧取；默认 `limit=30` 约覆盖 3 期。
- **站点最后一期是 2025 年 8 月**：自 2025-09 起未再更新（截至 2026-11 约 14 个月）。
  **不伪造 2025-09 之后的期次**；点亮点亮后由健康巡检按停更分诊。
- 更早期次的 PDF 部分为图片型（无文本层）或措辞不同 → 跳过并计数（实测 `limit=200`
  时 36 期出数、93 条被跳过）。

## 真实样例（2026-11-08 实跑，2025-08 期）

```json
{"period": "2025-08", "forecast_season": "2025/26", "item": "cotlook_a_index",
 "sub_period": "current_mtd_avg", "quote_month": "2025-08", "value": 78.43,
 "unit": "美分/磅", "yoy_pct": -1.7, "mom_pct": -0.5, "published_at": "2025-09-08",
 "pdf_url": "http://www.chinacotton.org/article/202598204353455.pdf"}
```

同期对照：播种面积 44415 万亩（−1.8%）、产量 2539.2 万吨（−2.1%）、消费量 2568.8
万吨（持平）、期末库存 1609.3 万吨（−1.5%）、库存消费比 62.6%（−1.0 个百分点）、
Cotlook A 上月均 78.81（−3.1%）、1—7 月进口 51.6 万吨（−74.2%）、当月进口 5.4 万吨
（−73.2% / 环比 +92.5%）。

## 定位与边界

`authority: industry`——发布方是研究机构（现代棉花产业技术体系产业经济研究室），
数值为**转述 USDA 官方预测** + 自有进口统计。只作参考序列，**不作阈值预警判据**。

## 自检

```bash
cd fd-industry-data
.venv/bin/python scripts/validate_manifests.py
.venv/bin/python scripts/conformance_gate.py
.venv/bin/python -m py_compile spiders/cncotton/spider.py
```
