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
