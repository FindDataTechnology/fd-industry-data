# moa-fishery —— 农业农村部渔业渔政管理局水产品产量

新源接入档案（`templates/new-source/` 范式；同源参考 `spiders/moa-hog-price/`）。
数据源：农业农村部渔业渔政管理局 `https://yyj.moa.gov.cn/gzdt/`
（实测 2026-11-08，国内直连，禁代理）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 列表 | `GET https://yyj.moa.gov.cn/gzdt/` | 「工作动态」栏目，UTF-8 静态 HTML，分页 `index_N.htm` |
| 月度累计 | `GET .../gzdt/<yyyymm>/t<yyyymmdd>_<id>.htm` | 正文标题与句子给「1—N月水产品产量 X 万吨，同比 ±Y%」 |
| 年度公报 | 同上域内 | 《YYYY年全国渔业经济统计公报》，含表1/表2，给分项产量 |

**两种粒度必须分开声明，绝不合并**：

| granularity | period 形态 | 含义 |
|---|---|---|
| `cumulative_month` | `YYYY-MM`（累计终点月） | 年内累计到第 N 月，`cumulative_month` 列给 N |
| `year` | `YYYY` | 年度公报全年值 |

入口：`run_moa_fishery(limit=50, window_start=None, window_end=None) -> list[dict]`
（`spider.py`）。仅标准库 `urllib` + `re` + `html`，无新依赖、不 OCR。

## 鉴权与参数

- 无 key / 无验证码 / 无 cookie；带常规浏览器 UA。
- **禁代理**：opener 显式挂空 `ProxyHandler({})`。
- TLS：`*.moa.gov.cn` 为 CFCA OV；按 `moa-hog-price` 同源惯例不对该 host 做证书校验。
- `limit` 计**行数**，最新在前。
- `window_start`/`window_end`（`YYYY-MM` / `YYYY-MM-DD`）**按月分辨率过滤数据期**：
  年度行按 12 月计入窗口（即窗口 `2025-01`…`2025-12` 能命中 `period=2025` 的年度行）；
  先过滤后截断。不成对/次序颠倒 → `ValueError`。

## 字段口径

| 列 | 口径 |
|---|---|
| `period` | 月度累计 = 累计终点月 `YYYY-MM`；年度 = `YYYY` |
| `granularity` | `cumulative_month` / `year` |
| `cumulative_month` | 累计到第几月（1..N 的 N），仅累计行有值；年度行 None |
| `item` | 总产量 / 海水养殖 / 海洋捕捞 / 淡水养殖 / 淡水捕捞 / 养殖 / 捕捞 / 海水产品 / 淡水产品 |
| `value` | 万吨；**取不到写 None，绝不填 0** |
| `yoy_pct` | 同比 %，signed（降为负）；原文缺失 → None |
| `title` / `source_url` / `scraped_at` | 文章标题 / 正文 URL / UTC ISO8601 |

## 历史深度（实测）

本次实跑覆盖 2020—2026：年度行 25 条（2020/2021/2023/2024/2025），月度累计行 90 条
（2024-07 … 2026-09）。月更，年内累计口径，**1—N 月**逐月递进。

## 真实样例（2026-11-08 实跑）

```json
{"period": "2026-09", "granularity": "cumulative_month", "cumulative_month": 9,
 "item": "总产量", "value": 5389.68, "unit": "万吨", "yoy_pct": 4.27,
 "title": "1—9月水产品产量5389.68万吨",
 "source_url": "https://yyj.moa.gov.cn/gzdt/202610/t20261022_6488844.htm"}
```

年度锚点：2025 年总产量 7652.14 万吨（+4.00%）、养殖 6325.62、海水产品 3859.94；
2024 年总产量 7357.59 万吨（+3.39%）。

## 价格序列未接（如实声明）

侦察未在该栏目看到水产品价格序列，本单元**不产出价格行**。若要价格，须另找官方
来源（本单元不臆造）。

## 自检

```bash
cd fd-industry-data
.venv/bin/python scripts/validate_manifests.py
.venv/bin/python scripts/conformance_gate.py
.venv/bin/python -m py_compile spiders/moa-fishery/spider.py
```

golden `001-moa-fishery.json` 用窗口钉死 2025 年，同时命中 `year` 与
`cumulative_month` 两种粒度——**窗口可过滤，新月份发布不会把锚点挤出 `limit`**。
