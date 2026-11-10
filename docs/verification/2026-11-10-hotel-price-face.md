# 验证导出 · 住宿价格面（2026-11-10）

> 由生产库 `fd_open_data` 直接导出（只读查询），不是自述。

## 概念

| 字段 | 值 |
|---|---|
| id | `2998` |
| code | `hotel.eu.accommodation_price_index.monthly` |
| unit | `index_2015_100` |
| measure | `price_index` |
| frequency | `month` |
| entity_type | `country` |
| verified | `True` |
| deprecated | `False` |

## 观测

| 字段 | 值 |
|---|---|
| rows | 301 |
| first | 2000-12 |
| last | 2025-12 |
| distinct_values | 293 |
| 空值行 | 0 |
| 与 `hotel.eu.nights.monthly` 重叠月份 | **180** |

抽样（前 3 期，逐值可复核）：

```json
[
 {
  "date": "2000-12",
  "value": "71.35"
 },
 {
  "date": "2001-01",
  "value": "69.11"
 },
 {
  "date": "2001-02",
  "value": "70.18"
 }
]
```

## provenance.pairing（4 条 nights 概念）

| 概念 | state | debt | available_side.eu | decision |
|---|---|---|---|---|
| `hotel.eu.nights.annual` | `partial` | `D1` | `hotel.eu.accommodation_price_index.monthly` | `2026-11-10-hotel-accommodation-price-index.md` |
| `hotel.eu.nights.foreign.monthly` | `partial` | `D1` | `hotel.eu.accommodation_price_index.monthly` | `2026-11-10-hotel-accommodation-price-index.md` |
| `hotel.eu.nights.monthly` | `partial` | `D1` | `hotel.eu.accommodation_price_index.monthly` | `2026-11-10-hotel-accommodation-price-index.md` |
| `hotel.eu.nights.yoy.monthly` | `partial` | `D1` | `hotel.eu.accommodation_price_index.monthly` | `2026-11-10-hotel-accommodation-price-index.md` |

## 逐项对拍（CP112 / CP111 / golden）

| 项 | 结果 |
|---|---|
| CP112 期数 | **301** |
| CP111 期数 | **301** |
| 两者**日期集合**完全相同 | ✅ True |
| 首期/末期 | CP112 `2000-12→2025-12` ／ CP111 `2000-12→2025-12`（一致） |
| golden 记「CP111 301 值（2000-12→2025-12）」 | ✅ **逐项吻合**（期数、首末、口径 `unit=I15, geo=EU27_2020` 全对齐） |
| golden 记「CP11/CP112 均 404」 | ❌ **不成立**：CP112 实得 301 期有值 |

### 库内数据 vs 上游：摘要对拍（不是肉眼比对）

| 来源 | CP112 值摘要 sha256 |
|---|---|
| 上游 Eurostat（本地直取） | `eaf881e752787fbaf200914610a89152cb4c4c32abfee8709ea688279f396960` |
| 生产库 `semantic_observations`（concept 2998） | `eaf881e752787fbaf200914610a89152cb4c4c32abfee8709ea688279f396960` |

**✓ 逐值一致**：301 期 `date=value` 逐行排序后取 sha256，两侧相同 → 入库没有丢期、没有改值、没有插值。

### provenance 决定记录指针（约束③）

| 记录 | state | debt | decision 指针 |
|---|---|---|---|
| `hotel.eu.accommodation_price_index.monthly` | — | — | `…/decisions/2026-11-10-hotel-accommodation-price-index.md`（provenance.decision + stricter_side） |
| 4 条 `hotel.eu.nights*` | `partial` | `D1` | 同一条决定记录（pairing.decision，**4/4** 均有） |
