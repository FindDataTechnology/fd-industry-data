# nasa-power —— NASA POWER 逐点气象日值（T2M / PRECTOTCORR）

新源接入档案。工单：`reports/health-tickets/20261006-nasa-power-64d35640.yaml`
（`kind=generate`，brief 侦察簿 W1-J）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 逐点日值 | `GET https://power.larc.nasa.gov/api/temporal/daily/point` | 公开 REST，免 key，JSON |

实测请求参数（只发这些，2026-10-07 直连冒烟）：
`parameters=T2M,PRECTOTCORR&community=AG&longitude=87.6&latitude=43.8&start=19840101&end=19841231&format=JSON`

入口：`run_nasa_power(limit=100) -> list[dict]`（`spider.py`）。仅用标准库 `urllib`，
无新依赖。

## 字段口径（brief 指定 long 格式）

| 列 | 来源 | 口径 |
|---|---|---|
| `date` | 载荷键 `YYYYMMDD` | ISO `1984-01-01` |
| `parameter` | `T2M` / `PRECTOTCORR` | 气温 / 降水，固定参数序（行序确定 → golden 可复现） |
| `value` | `properties.parameter.<P>[date]` | 日值；**`-999.0` 缺测占位（header 明示 fill_value）→ `None`** |
| `unit` | 常量映射 | `degC`（T2M）/ `mm`（PRECTOTCORR，mm/day） |
| `lon` / `lat` | 常量 | 87.6 / 43.8（乌鲁木齐，brief 实测点） |
| `url` / `source` / `scraped_at` | 请求 URL / 常量 / 本地 | 行级可追溯 / `nasa-power` / UTC ISO8601 |

1984 全年 = 366 天（闰年）× 2 参数 = 732 行；`limit` 截断。

## 坑位与容错（brief.notes 逐条落实）

- **区间参数 `start`/`end` 为 YYYYMMDD**（不是 ISO）。
- **数值可复现**：MERRA-2 对 1984 已定版——brief.notes 实测「1984 乌鲁木齐 366 天
  满量」（T2M/PRECTOTCORR 均无缺测占位），golden 依此锚固定历史值。
- **缺测占位 `-999.0`** 一律归一 `None`，绝不当作真实观测值。
- 单参数缺列/脏值跳过留痕（stderr 计数）；拉取失败 / 载荷无参数数据 / 零可用行 →
  `RuntimeError`（失败即红，不静默返回空列表）。
- 只带常规浏览器 UA；不做指纹伪装、不做频次对抗、无签名/验证码处理。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
python3 spiders/nasa-power/spider.py          # 冒烟，打印前 4 行（1984-01-01/02 × T2M/PRECTOTCORR）
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-nasa-power-64d35640.yaml
python3 scripts/validate_manifests.py && python3 scripts/check_manifest_commands.py
python3 scripts/conformance_gate.py --roots .
```

## golden 断言口径（跨期稳定）

`golden/001-nasa-power.json`：`params={"limit":4}` → 恒为 1984-01-01/01-02 ×
T2M/PRECTOTCORR 四行（已定版再分析，数值不随时间变化）。锚 `date == 1984-01-01`、
`parameter == T2M`、`value == -5.88`（T2M 1984-01-01，实测）、`value == 0.14`
（PRECTOTCORR 1984-01-01，实测）、`lon == 87.6`、`lat == 43.8`、
`source == nasa-power`，`min_rows: 4`。**绝不锚**当期/近期日期与滚动值；
`scraped_at`/`timestamp` 进 `whitelist_fields`。
