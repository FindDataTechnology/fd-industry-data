# sec-edgar-8k-operating —— 美股酒店 8-K 业绩新闻稿（exhibit 99.1）定位

入口：`run_sec_edgar_8k_operating(limit=80, tickers=None, filings_back=4) -> list[dict]`。

**本单元只定位，不解析。** 它按申报事实（`8-K` + Item `2.02`）找到业绩新闻稿
（exhibit 99.1），给出的是一行「文件指针 + 选件证据」；正文里的分地区 / 分品牌
RevPAR 抽取在 `scripts/hotel_extractors/sec_8k_operating.py`（逐家 mapping）。

依赖：**仅标准库** `json / urllib / gzip / re / datetime`（YAML 宇宙表走
`fd_industry_data.universes`，与其它单元一致）。

## 为什么单开一条车道

`sec-edgar-facts`（companyfacts）只给**无维度** XBRL 事实：分地区营收、总资产这类
能拿到，但「美国区 RevPAR」「亚太 ADR」这类**分地区营运指标只写在新闻稿的表格里**，
companyfacts 里没有任何对应元素。两条车道不重叠、也不互相补齐：

| 车道 | 数据面 | 形状 |
|---|---|---|
| `sec-edgar-facts` | XBRL 无维度事实 + 申报事件面 | 结构化、逐期 |
| `sec-edgar-8k-operating`（本单元） | 8-K Item 2.02 的 exhibit 99.1 定位 | 文件指针 + 选件证据 |
| 抽取器（不在本单元内） | 新闻稿表格里的营运指标 | 逐季、分地区 |

## 数据表面（两段式）

| 段 | 端点 | 鉴权 | 说明 |
|---|---|---|---|
| 一 · 宇宙 | 本地 `docs/hotel-universe.yaml` | 无 | `lanes` 含本单元的实体（当前 5 家：MAR/HLT/WH/H/CHH） |
| 二 · 申报清单 | `GET https://data.sec.gov/submissions/CIK##########.json` | 免鉴权 | `filings.recent` 平行数组，取 `form=8-K` 且 `items` 含 `2.02` |
| 三 · 文件目录 | `GET https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/index.json` | 免鉴权 | 目录文件清单（大小、文件名） |
| 四 · 8-K 正文 | `GET https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{primaryDocument}` | 免鉴权 | 正文里的 exhibit 锚点 = 选件依据 |
| 输出 | exhibit 的**完整 URL + 证据** | —— | **不下载正文**（抽取器按需取，带 sha1 磁盘缓存） |

人在回路的关键点：**exhibit 文件名不可猜**。五家用三种命名法
（`*xex99earningsrel.htm` / `q3YYYYearningsrelease.htm` / `exhibit991YYYYMMDD.htm`），
且同一份公告里常带补充演示文稿。所以选件靠正文里的**文字证据**，不靠文件名。

## 选件规则（三条独立证据，宁缺勿猜）

1. 从 8-K 正文里取所有指向同目录、非 `.xsd` 的锚点，每个锚点带 ±600/900 字上下文。
2. 排除上下文出现 `supplemental` / `presentation` / `slide` 的候选（实测 Hyatt
   2026Q3 有两份：业绩稿 + 补充演示，排除后唯一）。
3. 候选里含显式 `Exhibit 99.1` 文字且**唯一** → `pick_evidence=explicit-exhibit-99.1`。
4. 否则取含 press-release 字样且唯一的候选 → `press-release-context`。
5. 都不唯一 → `status=ambiguous-exhibit-link` / `ambiguous-explicit-99.1`，
   `exhibit_url=null`，**不猜**。抽取器对 `status≠ok` 的载荷按不可得呈现。

`pick_evidence` 是随行证据列，任何时候都能回答「这份文件是怎么被选中的」。

## 期间：period_end 与 event_date 是两列，别混

实测坑：`submissions.reportDate` 是**最早事件日**，通常是**申报日**，不是财季末。
MAR 的 2026Q3 8-K：`reportDate = 2026-11-02`，而公告披露的是 2026-09-30 季。

| 列 | 含义 | 来源 |
|---|---|---|
| `period_end` | 新闻稿覆盖的财季末 `YYYY-MM-DD` | 从 8-K 正文解析「quarter ended September 30, 2026」；正文不写则为 `null` |
| `event_date` | `submissions.reportDate` | 申报清单原值（最早事件日，通常=申报日） |

抽取器不会用 `event_date` 当期间；它优先用 exhibit 自己的表头
（「Three Months Ended September 30, 2026」），两者不一致时以 exhibit 为准并留 note。

## 网络约束（实测）

- **必须带 `Accept-Encoding: gzip`**：python urllib 不带 gzip 在 1.27 MB 的 MAR
  exhibit 上会卡死（90 秒只收 128 KB）；带上即秒回。单元已强制 gzip 并解压。
- **User-Agent 必须带应用名 + 联系邮箱**，否则 403。
- 请求间隔 0.15 秒；单次传输失败重试一次，仍失败则整次运行报错（绝不返回 0 行当「没有数据」）。
- `index.json` 的 `item.type` 是 MIME 图标名（`text.gif` 之类），**不是** exhibit 类型；
  `-index.html` / `-index-headers.html` 已返回 SEC 404 页 —— 所以 exhibit 类型只能从正文锚点读。

## 覆盖与不可得（实测 2026-11-08，14 家美股酒店宇宙）

| 实体 | 状态 | 原因 |
|---|---|---|
| MAR 万豪 / HLT 希尔顿 / WH 温德姆 / H 凯悦 / CHH 精选 | `parsed` | 8-K Item 2.02 + exhibit 99.1 营运表可解析 |
| HTHT 华住 / IHG / ATAT 亚朵 / GHG 格林 / TCOM 携程 | `not-an-8k-filer` | 外国私人发行人，只有 20-F/6-K；华住实测 **271 份 6-K、0 份 8-K** |
| HGV 希尔顿度假 | `no-revpar-disclosure` | exhibit 890,755 字节，RevPAR 命中 0、出租率命中 0，8-K 正文无文件链接 |
| BKNG / EXPE / ABNB | `ota-no-hotel-kpi` | OTA，不披露酒店营运三指标 |

任务书原句「至少 4 家（MAR/HLT/WH/**HTHT**）」中的 HTHT 一项经实测证伪（6-K 申报人），
以 H + CHH 替补，实际覆盖 **5 家**。逐家理由见抽取器 `AVAILABILITY` 与
`openspec/changes/add-hotel-vertical/reports/4.0-wave3-recon.md`。

## 已知坑位

1. **累计期块冒充当季**（最危险）：五家的 FY（Q4）业绩稿把「Three Months Ended」与
   「Year Ended / Twelve Months Ended」并进同一张表，季度块后面紧跟行名一模一样的
   累计块（H：`Three Months Ended June 30,` → 数据行 → `Six Months Ended June 30,`）。
   只认 3M 会把年度值写成季度值（HLT FY 114.39 顶掉 Q4 110.89）。抽取器现在**先判累计期**
   并清表头，整块不写；同一份 exhibit 内出现同期两个不同值会留痕并以先出现者取用。
2. **同稿多张 historical 表**：温德姆 FY2025 稿同时有「REPORTING BASIS AS OF Q4 2025」
   与「PRE Q2 2025 REPORTING BASIS」两张同结构表（Global 2025 Q1 一个 38.44、一个
   36.13，旧基准表 Q2–Q4 全 `n/a`）。按「已披露季度数最多」取用本期基准，旧基准整块不写。
3. **同比位是破折号**：H 自有酒店出租率同比为「—」，量值三个都在。按数字个数判形状会
   得 5 个数字并整行丢弃；按**槽**解析（3 指标 × (量值, 同比)）才不丢真数据。
4. **表格上下文必须随换表重置**：`Total U.S.` / `International` 这类行名在营运表、
   房价表、规模表里重复出现；只有「表头与数据行同表」才算一条营运记录（否则 CHH 的
   royalty rate 表会被当成营运三列）。
5. **列归属不按顺序猜**：由 `RevPAR = ADR × 出租率` 恒等式裁决，只接受唯一解；
   同值时用表头列序作第二条独立证据（HLT 亚太出租率 71.9 与 RevPAR 71.78 几乎同值）。

## 参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `limit` | int | 80 | 最多返回行数（1 行 = 1 份 8-K Item 2.02 申报） |
| `tickers` | array | 宇宙内本车道实体 | 覆盖宇宙；空列表是硬失败，不静默回退 |
| `filings_back` | int | 4 | 每家往回看几份 8-K（≈ 一年季度） |

## 本机复核

```bash
# 只看某一家、某一期（离线，不出网）
python3 spiders/sec-edgar-8k-operating/spider.py --tickers MAR --filings-back 1 --limit 5

# 落库（本地 crawl_runs / crawl_items），再跑抽取器
FD_CRAWL_DB_URL="$FD_OPEN_DATA_DSN" FD_IMAGE_TAG=local \
  python3 scripts/land_command.py sec-edgar-8k-operating run_sec_edgar_8k_operating \
  --limit 40 --params '{"filings_back":4}'
python3 scripts/hotel_extractors/sec_8k_operating.py            # dry-run
python3 scripts/hotel_extractors/sec_8k_operating.py --write    # 写观测，重跑 inserted=0
```

实测：20 条载荷（5 家 × 4 季）→ 297 条季度观测（39 个概念，2025-03-31 ~ 2026-09-30），
重跑 `inserted = 0`。
