# nra-rpi — NRA Restaurant Performance Index（美国餐饮业绩效指数）

## 源与面

- 发布方：National Restaurant Association（美国餐饮业行业协会，`industry` authority）
- 落地页：<https://restaurant.org/research-and-media/research/restaurant-performance-index/>
- 数据面：月度 PDF（3 页，约 600 KB）——落地页上的 `RPI-<Month>-<YYYY>.pdf` 最新一期链接。
  工单限定上游为 PDF 文本层；无逐期归档 URL 证据（CDN 目录年份 ≠ 报告期，实测
  `…/RPI/2024/RPI-September-2026.pdf`），因此**只取最新一期**。

## 数据语义

RPI 为合成扩散指数（基线 100），由两个等权分量构成：

| index 字段 | 含义 |
|---|---|
| `RPI` | 综合指数 |
| `current_situation` | Current Situation Index（现状分量） |
| `expectations` | Expectations Index（预期分量） |

每期 PDF 第 3 页「Restaurant Performance Index and Its Components」表给出近 5 个月
数值 + 环比变动。本单元输出长表：每行 `(period, index, value, unit, source_url, scraped_at)`，
一期最多 15 行（5 个月 × 3 指数），新期在前。

**内置交叉验证（质量门）**：每月必须满足 `RPI ≈ (current_situation + expectations) / 2`
（±0.15 四舍五入容差），不过则整月丢弃并打日志，绝不零填充、绝不猜测。该项同时防御
表格列错位/解析漂移。

## 提取方式（无 OCR，无反爬对抗）

1. 抓落地页 → 正则取最新 `RPI-<Month>-<YYYY>.pdf` 绝对链接；
2. 下载 PDF，校验 `%PDF-` 魔数；
3. 文本层：优先平台共享层 `fd_industry_data.pdftext.extract_text(payload, layout=True)`
   （先例：`spiders/cncotton`、`spiders/cba-wealth-index`）；不可用或 `PdfTextError`
   时回退**内嵌 stdlib 提取器**（同契约，零第三方依赖：FlateDecode 内容流、WinAnsi、
   Type0/Identity-H + ToUnicode CMap、Tm 基线聚类 + x 排序还原 layout 行）；
4. 解析三行复合指数 + 月份表头 → 长表 + 交叉验证。

- 纯图像 PDF（无文本层）→ 显式报错跳过，不做 OCR。
- 403/429/5xx 仅一次退避重试；遇反爬墙直接抛错转人工，不做任何规避。

## 已知边界

- 仅最新一期可达 → 无 `window_start/window_end` 语义（每运行 ≤15 行）。
- 文本伪影：叙述段落存在词序扰动/标签断词（多 BT 块按 y 聚类交错所致），**不影响**
  表格行；未映射 CID 字节出现在个别叙述行，同样不影响表格。
- golden 用结构断言不钉数值（最新期会推进），期次内数值质量由交叉验证把关（见
  `golden/001-nra-rpi.json` note）。

## 证据（生成时实测，2026-11-11）

| 项 | 结果 |
|---|---|
| 落地页 GET | 200，含 `RPI-September-2026.pdf` 链接 |
| PDF 下载 | 200，647,249 B，`%PDF-1.7`，3 页 |
| 文本层 | stdlib 回退提取器 177 行 layout 输出 |
| 解析 | 2026-05..2026-09 × 3 指数 = 15 行，交叉验证 15/15 通过 |
| 最新月（2026-09） | RPI=100.7，current_situation=100.7，expectations=100.7，与 PDF 逐字一致，环比 +0.1% |
| 本地验证 | `scripts/health_verify.py --ticket 20261107-nra-rpi-3a5952f4` 全绿（本地为回退路径；镜像运行走平台 pdftext） |

## 契约

- 入口：`run_nra_rpi(limit=None) -> list[dict]`（limit 截断行数，新期在前）。
- 0 行 / 表头缺失 / 交叉验证全挂 → 抛错（平台按失败计数），不静默出空表。
