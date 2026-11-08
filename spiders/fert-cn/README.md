# fert-cn —— 中肥网 化肥月度进出口统计（`authority=industry`）

新源接入档案（`templates/new-source/` 范式）。
数据源：中肥网 `https://www.fert.cn/10005/`（实测 2026-11-08，国内直连，禁代理）。

> **口径红线**：中肥网是**商业门户/产业媒体**，不是行业协会。`authority` 必须标
> **`industry`**，**绝不标 `association`**。本单元产出只作**成本链参考序列**，
> **不得作为预警判据**。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 列表 | `GET https://www.fert.cn/10005/` | 「化肥进出口」栏目，分页；每条一个数据月 |
| 正文 | `GET https://www.fert.cn/news/<YYYY>/<MM>/<DD>/<id>.shtml` | 正文一句话给出量 / 金额 / 均价 / 环比 / 同比 |

一条正文样例（2026-08 尿素出口）：量 `52.46 万吨`、金额 `217,872,915 美元`、
均价 `415.28 美元/吨`、环比 `+30.0%`、同比 `−34.1%`。

入口：`run_fert_cn(limit=50, window_start=None, window_end=None) -> list[dict]`
（`spider.py`）。仅标准库 `urllib` + `re` + `html`，无新依赖、不 OCR。

## 鉴权与参数

- 正文页免鉴权；不登录会员区。
- **Vip 区不接**：化肥**产量与出厂价**在 `vip.fert.cn` 且需会员登录 → 本单元不接，
  也不以任何方式绕过。
- **禁代理**：opener 显式挂空 `ProxyHandler({})`。
- `window_start`/`window_end`（`YYYY-MM`）过滤的是文章的**数据月**（标题/正文里的
  「YYYY年M月份」），**不是发布日**；先过滤后截断。

## 字段口径

| 列 | 口径 |
|---|---|
| `record_id` | 稳定键 `YYYY-MM|product|flow` |
| `period` | 数据月 `YYYY-MM`（取正文声明，不取发布日） |
| `product` | 品名原文（尿素 / 磷酸二铵 / 硫酸钾 / 其他氯化钾 …） |
| `product_kind` | 本地分桶：`fertilizer` / `raw_material` / `other`（栏目同页还发硫磺、磷矿石、原油、无烟煤） |
| `flow` | `export` / `import`（由正文动词判定） |
| `volume_wan_t` | 归一到**万吨**；换算无损（原文值与原单位另存两列） |
| `volume_published` / `volume_unit` | 原文数值与原单位（**同一篇文章内不同语句可能用 万吨 / 吨 / 千克**） |
| `amount_usd` | 金额（美元）；缺失 → null，**绝不补 0** |
| `avg_price_usd_t` | 平均单价（美元/吨），原文值 |
| `mom_pct` / `yoy_pct` | 环比/同比 %，signed（减/降为负）；**无方向词则 null**（不推断符号） |
| `prev_month_volume_wan_t` / `prev_year_volume_wan_t` | 上月 / 同期量，归一万吨；缺失 null |
| `title` / `published_date` / `source_url` / `source` / `scraped_at` | 原文标题 / 列表发布日 / 正文 URL / `fert-cn` / UTC ISO8601 |

## 历史深度（实测）

本次实跑覆盖 2026-01 … 2026-08（8 个数据月）。更早文章仍在列表中，按需回补。

## 真实样例（2026-11-08 实跑，窗口钉 2026-08）

```json
{"record_id": "2026-08|硫酸|import", "period": "2026-08", "product": "硫酸",
 "product_kind": "raw_material", "flow": "import", "volume_wan_t": 0.99,
 "volume_published": 0.99, "volume_unit": "万吨", "amount_usd": 2690486.0,
 "avg_price_usd_t": 270.72, "mom_pct": 831.9, "yoy_pct": -97.7}
```

## 定位与边界

- 定位：**成本链参考序列**（化肥进出口量价，观察投入品成本方向）。
- 边界：`authority=industry`，**不进阈值预警**；产量/出厂价（vip 会员区）不接；
  不替代官方统计口径（海关口径见侦察簿「海关农产品贸易」不可得留痕）。

## 自检

```bash
cd fd-industry-data
.venv/bin/python scripts/validate_manifests.py
.venv/bin/python scripts/conformance_gate.py
.venv/bin/python -m py_compile spiders/fert-cn/spider.py
```
