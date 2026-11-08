# universes/ — 行业车道的默认监控宇宙（唯一真源）

本目录是**共享金融车道**的宇宙注册表。车道单元（`spiders/hkex-disclosure`、
`spiders/sec-edgar-facts`，及后续 `eastmoney-disclosure-plan`）MUST 从这里读取默认
宇宙，MUST NOT 在源码内硬编码公司列表。见 `openspec/changes/add-hotel-vertical`
的 `vertical-lane-generalization` 能力。

## 文件

| 文件 | region | 交易所 | 代码形态 |
|---|---|---|---|
| `hk.yaml` | `HK` | HKEX | 5 位零填充港股代码（`06862`） |
| `us.yaml` | `US` | NYSE / NASDAQ | 裸 ticker（`MCD`） |
| `cn-a.yaml` | `CN-A` | SSE / SZSE | 裸 6 位数字（`002186`） |

## schema

```yaml
version: "2026-11-08"
region: HK
exchange: HKEX
code_form: "5 位零填充"          # 人读说明，不入解析
entities:
  - ticker: "06862"
    name_zh: 海底捞
    name_en: Haidilao International Holding Ltd.
    segment: full-service
    verticals: [restaurant]      # 该实体所属垂直线，可多值
```

- `ticker` / `name_zh` / `segment` / `verticals` 四项必填，缺一即加载失败（不静默跳过）。
- `verticals` 是多归属数组：同一实体被两条线监控时**只有一条记录**，不重复登记。
- `segment` 词表**按垂直线各自定义**（餐饮四类 / 酒店六类），本目录不强制统一词表，
  但同一文件内必须出自该垂直线声明的词表。

## 默认宇宙语义

不传 `stocks` / `tickers` 参数时，车道默认宇宙 = **该 region 全部实体的并集**
（跨垂直线）。需要单垂直线时显式传参覆盖。因此向本目录新增实体**会**扩大默认宇宙，
这是有意为之：车道是按 region 共享的，不是按垂直线分裂的。

显式参数与注册表的关系：显式传入优先，不并入默认值；传入空集合是硬失败，
不静默回落到默认宇宙。

## 与实体注册的关系

本目录服务**车道默认值**（抓取面的公司清单）。实体注册（`entities` 表、
`entity_source_identifiers`）由各垂直线的注册脚本从各自的宇宙真源执行，例如餐饮线
`docs/restaurant-universe.yaml` + `scripts/register_restaurant_universe.py`。

两处若出现分歧，以**上游实测**为准，且分歧必须记录：见 `docs/` 下对应垂直线的
口径档。已知分歧一条（2026-11-08）：`docs/restaurant-universe.yaml` 记特海国际为
`00965.HK`，而 `hkex-disclosure` 单元上游实测确认应为 `09658`（`00965` 经
`prefix.do` 解析落到 `09658` 特海國際）。本目录采用实测值 `09658`。
