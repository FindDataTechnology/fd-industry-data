# jp-foodservice-trend — 日本外食産業市場動向調査（JF 全店月次）

日本フードサービス協会（JF, 日本フードサービス協会 = Japan Foodservice Association）
每月发布「外食産業市場動向調査」，按月给出外食产业 **全店** 口径的 4 个指标：
売上高 / 店舗数 / 利用客数 / 客単価，每个都表达为 **前年同月比**，并按业态拆 7 行
（全体 + 6 个业态）。本单元把每份月次 Excel 的「全店」表解析成
`month × segment` 的长表，每行 4 个 ratio。

## 口径（上游原文口径，原样输出，不换算）

- 指标是 **前年同月比**：`概要_公表版` 表 3 注明「回答事業社数をベースにした
  前年同月比を指標とする」。故值是 **ratio**（1.0 = 与去年同月持平，1.05 = +5%），
  本单元原样输出该 ratio，**不换算成增减率、不重算、不四舍五入**。
- **全店データ** = 含新开店的全部店铺（非「既存店」口径）；`全体` 为各业态合计。
- 业态行（上游首列，全角空格填充，读取时先把所有空白剥掉）：
  `全体` / `ファーストフード` / `ファミリーレストラン` /
  `パブレストラン/居酒屋`（源表用全角「／」，输出归一为 ASCII `/`）/
  `ディナーレストラン` / `喫茶` / `その他`。
- 每份报告只带 **13 个月滚动窗**（当月 + 前 12 个月），所以单份文件不足以覆盖历史；
  更深历史靠索引页归档（月度 Excel 实测可回溯到第 8 页，约 2020 年；第 9 页起为
  年度报告，没有月次 Excel）。

## 实测端点（2026-11-08 测）

| 端点 | 状态 | 说明 |
|---|---|---|
| `https://www.jfnet.or.jp/industry_report/` | 200 | 报告索引第 1 页：9 份月次报告，每份挂 PDF + Excel 两个链接 |
| `https://www.jfnet.or.jp/industry_report/page/N/` | 200 | 第 N 页（每页 9 份）；第 8 页仍是月次（约 2020 年），第 9 页起为年度报告 |
| `https://www.jfnet.or.jp/wp/wp-content/uploads/<YYYY>/<MM>/data_c_m<YYYY>_<M>.xls` | 200 | 月次 Excel 直链 = 本单元结构化数据面 |

索引页实测（2026-11-08，第 1 页 9 份，版次 2026-01…2026-09）：

```
(2026,9)  .../uploads/2026/10/data_c_m2026_09.xls   ← 最新版
(2026,8)  .../uploads/2026/09/data_c_m2026_08.xls
(2026,7)  .../uploads/2026/09/data_c_m2026_07.xls   ← 7、8 月两版同在 uploads/2026/09
...
(2026,1)  .../uploads/2026/02/data_c_m2026_1.xls    ← 月份不补零
```

鉴权：无（公开页面与 Excel，无需 token/cookie）。
网络模式：**直连**（本机 2026-11-08 全部取数直连即通）。spider 仍保留
「直连失败 → 经 `http://127.0.0.1:7890` 重试一次」的兜底，每 URL 至多两次尝试、90s 超时。

## 解析规则（按内容定位，不写死行列号）

工作簿两张表：`全店`（数据）+ `概要_公表版`（调查方法/样本量，不取）。
`全店` 表布局（2022-12 版与 2026-09 版两版实测一致）：

```
行 67  ａ．売上高前年同月比        ← 块头
行 69  25年9月 10月 … 26年9月      ← 月份行（首列带年，后续只写月，跨年需推进）
行 70  全　　体  1.0481 … 1.0731   ← 7 个业态各一行
行 78  ｂ．店舗数前年同月比
行 89  ｃ．利用客数前年同月比
行 100 ｄ．客単価前年同月比
```

1. 块头 = 任一行的前 3 格同时含「前年同月比」与指标关键词（売上高/店舗数/利用客数/
   客単価）；`客単価` 的匹配必须排在 `客数` 之前（子串关系），否则客単価块会被误判。
2. 块内第一行「N年M月 / M月」标签即月份行；只有 `M月` 的格按前一个月份推进，
   `月 <= 前月` 判为跨年、年 +1（滚动窗跨年度时必需）。
3. 块内首列标签命中 7 个规范业态名的行才是数据行；其余（注释、小计说明）跳过。
4. 每块至少解析出 10 格，否则判「版面漂移」直接 `RuntimeError`（失败即红，
   不静默出半张表）。
5. 值域守卫：`0.2 ≤ ratio ≤ 5.0`；越界格**跳过并 stderr 留痕**（不造数）。
   非数值格（`-`、`…`、`X` 等）直接跳过。
6. 跨块恒等式守卫：上游口径下 `売上高比 = 利用客数比 × 客単価比`
   （实测两版工作簿全格偏差 ≤ 4.5e-16）。偏差 > 1e-6 只可能由「块-列错位」
   造成，故判红转人工。
7. 去重：同一 `(month, segment)` 可能被多份报告的滚动窗覆盖——**取最新报告版**的值，
   `edition` 记录胜出版本（这样同一月份在后续重跑中稳定可复现）。
8. 输出按月份升序、业态按上游顺序排列，`[:limit]` 截断。

## 入口与窗口

`run_jp_foodservice_trend(limit=500, since=None)`：

- `since=None`（缺省，月度调度行为）：只读索引 **第 1 页**（最新 9 份报告），
  9 份的 13 个月窗并集约 **21 个月**；按月份升序截 `limit`。
- `since="YYYY-MM"`：逐页回溯索引直到版次早于 `since`，下载覆盖该月及以后的每份
  报告（滚动窗并集去重），再把输出裁到 `month >= since`。实测
  `since="2025-01"` = 21 份报告（2025-01…2026-09），端到端约 90 秒。
- `limit<=0` → `[]`；`since` 非 `YYYY-MM` → `ValueError`；索引页无 Excel 链接、
  工作簿无「全店」表、无前年同月比块、0 数据行 → `RuntimeError`（失败即红）。

## 已知坑

1. **扩展名不可推导**：直链文件名 `data_c_m<YYYY>_<M>`，月份**不补零**
   （`data_c_m2026_1.xls`）；且个别月份是 `.xlsx`（如 2023-08）。扩展名与
   uploads 目录月都不能从版次推出来，只能从索引页锚文本取。
2. **索引分页**：每页 9 份，月次到第 8 页（约 2020）为止，第 9 页起是年度报告
   （无 Excel 链接，`_index_entries` 返回空即停止回溯）。缺省只取第 1 页，想要
   更深历史必须显式传 `since`。
3. **13 个月滚动窗**：单份报告覆盖「当月 + 前 12 个月」，相邻版次大量重叠；
   不做去重会重复输出，且旧版值可能覆盖新版修订值（spider 按 edition 取新）。
4. **月份标签跨年**：月份行首列写「25年9月」，其后只写「10月…9月」；不推进年份
   会把 2026 年的列错标成 2025。
5. **上传节奏不齐**：2026-07 与 2026-08 两版同在 `uploads/2026/09`，不能按
   `uploads/<年>/<月>` 反推版次日期；版次一律以索引页文件名里的
   `data_c_m<YYYY>_<M>` 为准。
6. **两个 sheet**：`概要_公表版` 只有调查方法/样本量，没有数据；取数固定
   `sheet="全店"`，改名/缺表即 `RuntimeError`。
7. 索引页 2026-11-08 尚未出现 2026-10 版（最新为 2026-09），调度定在每月 8 日，
   若上游晚发则该月运行仍会取到上一版——滚动窗本就覆盖上月，不判失败。

## 本地运行

```bash
cd /Users/chengsishi/finddata/fd-industry-data

# 冒烟：python3 spiders/jp-foodservice-trend/spider.py [limit]
.venv/bin/python spiders/jp-foodservice-trend/spider.py 7          # 缺省：第 1 页，最早 7 行

# 带 since 的深历史（目录名带连字符，不能按包路径 import，用文件路径加载）
.venv/bin/python -c "
import importlib.util, pathlib, json
p = pathlib.Path('spiders/jp-foodservice-trend/spider.py').resolve()
spec = importlib.util.spec_from_file_location('jft', p)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
rows = m.run_jp_foodservice_trend(limit=14, since='2025-01')
print(len(rows)); print(json.dumps(rows[0], ensure_ascii=False))
"

# 自检
.venv/bin/python -c "from fd_industry_data.health.samples import load_samples, validate_sample; from pathlib import Path; s=load_samples(Path('spiders/jp-foodservice-trend')); print(validate_sample(s[0]))"
.venv/bin/python -c "from fd_industry_data.health.samples import load_samples; from fd_industry_data.health.guard import replay_sample; from pathlib import Path; d=Path('spiders/jp-foodservice-trend'); s=load_samples(d); print(replay_sample(d, s[0]))"
python3 scripts/validate_manifests.py
python3 scripts/check_manifest_commands.py
python3 scripts/conformance_gate.py
```

## 证据（首爬 2026-11-08，真实运行，非构造）

缺省调用（`limit=13`，`since=None`）：日志 `9 份月次报告（2026-01 … 2026-09）`，
合并窗口 21 个月，输出 13 行。逐字前 2 行（`scraped_at` 略）：

```json
{"month": "2025-01", "segment": "全体", "sales_yoy": 1.0765190538017952, "store_count_yoy": 1.0041919991116293, "customer_count_yoy": 1.0339080179902553, "spend_per_customer_yoy": 1.0412135654914143, "edition": "2026-01", "url": "https://www.jfnet.or.jp/wp/wp-content/uploads/2026/02/data_c_m2026_1.xls", "source": "jp-foodservice-trend", "scraped_at": "2026-11-07T16:10:00.140010+00:00"}
{"month": "2025-01", "segment": "ファーストフード", "sales_yoy": 1.0813916941569786, "store_count_yoy": 1.0124703948958385, "customer_count_yoy": 1.038502935306044, "spend_per_customer_yoy": 1.041298639987277, "edition": "2026-01", "url": "https://www.jfnet.or.jp/wp/wp-content/uploads/2026/02/data_c_m2026_1.xls", "source": "jp-foodservice-trend", "scraped_at": "2026-11-07T16:10:00.140010+00:00"}
```

`since="2025-01"` 调用：日志 `21 份月次报告（2025-01 … 2026-09）`，`limit=14`
输出 14 行（2025-01 全 7 业态 + 2025-02 前 7 行），wall clock 约 90s。
2025-01／全体 四值与缺省调用逐位一致（去重取新版 = 确定性）。

抽验：2025-01／全体 满足跨块恒等式
`customer_count_yoy × spend_per_customer_yoy = 1.0339080180 × 1.0412135655
≈ 1.0765190538 = sales_yoy`（spider 内置守卫对所有行强制此式，偏差 >1e-6 即判红）。

## golden 断言策略

`golden/001-jp-foodservice-trend.json`（params `limit=14, since="2025-01"`）：
`min_rows=14` + 8 条锚——`source`、`month=2025-01`、`segment=全体`、
`edition=2026-01`，以及该行四个 ratio 的**逐位定稿值**
（1.0765190538017952 / 1.0041919991116293 / 1.0339080179902553 / 1.0412135654914143，
读自 `uploads/2026/02/data_c_m2026_1.xls`）。`scraped_at` 在 whitelist。

为什么用 `since` 而不是缺省：缺省只取索引第 1 页，窗口最早就已经是 2025-01；
每出一版新报告窗口前移一个月，锚点约 1 个月后就会滑出（属预期老化）。给
`since="2025-01"` 后索引逐页回溯到 2025-01 版，2025-01 只会被 2026-01 版覆盖
（后续版次的 13 个月窗都从 2025-02 起），故该值是历史定稿，锚点长期稳定。
**最新月（2026-09）不入锚**——下版报告可能修订滚动窗末月。若多年后索引归档
被裁掉 2025-01 版，replay 会失败，届时把 `since` 与锚点月份一起前移重录即可。
