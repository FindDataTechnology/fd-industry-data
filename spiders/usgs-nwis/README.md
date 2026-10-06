# usgs-nwis —— USGS 水文日值（DV，站点 01646500 流量）

新源接入档案。工单：`reports/health-tickets/20261006-usgs-nwis-f0a47fcb.yaml`
（`kind=generate`，brief 侦察簿 W1-J）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 日值序列 | `GET https://waterservices.usgs.gov/nwis/dv/?sites=01646500&startDT=2020-01-01&endDT=2020-12-31&parameterCd=00060&format=json` | 公开 REST DV 服务，免 key，JSON；实测 366 天（2020 闰年）满量 |

入口：`run_usgs_nwis(limit=100) -> list[dict]`（`spider.py`）。仅用标准库 `urllib`，
无新依赖。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `site` | `sourceInfo.siteCode[0].value` | USGS 站号 `01646500`（Potomac River 近华盛顿 Little Falls 泵站，百年站） |
| `site_name` | `sourceInfo.siteName` | 站点显示名 |
| `parameter_cd` | `variable.variableCode[0].value` | `00060` = 河流流量 |
| `date` | `dateTime[:10]` | ISO `2020-01-01`（`dateTime` 原为 `2020-01-01T00:00:00.000`） |
| `value` | `values[<j>].value[].value` | 日均流量 ft3/s；**`-999999` 缺测占位 → `None`** |
| `unit` | `variable.unit.unitCode` | `ft3/s` |
| `qualifiers` | `value[].qualifiers`（复数，list） | 逗号拼接（`A`=已核准 / `P`=预发布 / `A:e`=估算） |
| `url` / `source` / `scraped_at` | 请求 URL / 常量 / 本地 | 行级可追溯 / `usgs-nwis` / UTC ISO8601 |

## 坑位与容错（brief.notes 逐条落实）

- **golden 锚固定站点固定历史年份**：2020 年数据已定稿（qualifier=A），日值不再
  变化——锚 `2020-01-01 == 7610`（实测）。部分站 100+ 年历史，扩站时可照此
  「固定站点 + 定稿年份」口径另加样本。
- **`qualifiers` 是复数键**（list，非单数字符串），拼串透出不丢信息。
- 单序列失败跳过留痕（stderr 计数）；拉取失败 / 载荷无 `value.timeSeries` /
  零可用行 → `RuntimeError`（失败即红，不静默返回空列表）。
- 只带常规浏览器 UA；不做指纹伪装、不做频次对抗、无签名/验证码处理。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
python3 spiders/usgs-nwis/spider.py          # 冒烟，打印前 3 行（2020-01-01 起）
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-usgs-nwis-f0a47fcb.yaml
python3 scripts/validate_manifests.py && python3 scripts/check_manifest_commands.py
python3 scripts/conformance_gate.py --roots .
```

## golden 断言口径（跨期稳定）

`golden/001-usgs-nwis.json`：`params={"limit":5}` → 2020 年前 5 天（date 升序，
恒定历史）。锚 `site == 01646500`、`parameter_cd == 00060`、`date == 2020-01-01`、
`value == 7610.0`（实测定稿值，qualifier=A）、`source == usgs-nwis`，
`min_rows: 5`。**绝不锚**当期/近期日期与预发布（P）数据；
`scraped_at`/`timestamp` 进 `whitelist_fields`。
