# nmc-weather —— 中央气象台气象站实况

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261004-nmc-weather-23a303a2.yaml`
（`kind=generate`，brief 侦察簿 W2-B）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 实况 | `GET https://www.nmc.cn/rest/weather?stationid=<站码>` | 公开 REST，免鉴权，返回 JSON |
| 省码表 | `GET https://www.nmc.cn/rest/province` | `[{code,name,url}]`，如 `ABJ` = 北京市 |
| 站点表 | `GET https://www.nmc.cn/rest/province/<省码>` | `[{code,province,city,url}]`，站码为内部短码 |

入口：`run_nmc_weather(limit=100) -> list[dict]`（`spider.py`）。仅用标准库 `urllib`，无新依赖。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `station` | `data.real.station.code` | NMC **内部短码**（`Wqsps` = 北京），非 WMO 站号 |
| `station_name` / `city` | `data.real.station.city` | 站点所在城市显示名 |
| `province` | `data.real.station.province` | 省/直辖市名 |
| `temperature` | `data.real.weather.temperature` | 气温 ℃，实况观测值 |
| `humidity` | `data.real.weather.humidity` | 相对湿度 % |
| `weather` | `data.real.weather.info` | 天气现象描述（如 `晴`） |
| `wind` | `data.real.wind.speed` | 风速 m/s |
| `wind_direct` / `wind_power` | `data.real.wind.direct` / `.power` | 风向文本 / 风力文本 |
| `publish_time` | `data.real.publish_time` | 上游发布时间（字符串，原样透出） |
| `url` | 请求 URL | 行级来源可追溯 |
| `source` | 常量 | `nmc-weather` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 坑位与容错（brief.notes 逐条落实）

- **缺测占位 `9999`**：数值 `9999.0` 与字符串 `"9999"` 一律归一为 `None`（`_num` / `_text`），
  绝不当作真实观测值；`warn.*` 全为占位符，故不并入行字段。
- **浏览器 UA**：站点要求常规浏览器 User-Agent，本单元只带固定 UA + `Referer`，不做指纹伪装、
  不做频次对抗、无签名/验证码处理——遇风控升级按协议转人工（换数据表面），不逆向。
- **`limit` 语义**：先取种子站（`Wqsps`，工单 `brief.source_urls` 指定），不足时才按省码表顺序
  扩展。`limit=1` 时站点与 URL 恒定 → golden 重放确定。
- **失败即红**：单站失败只跳过并在异常信息里留痕；**全部站点失败抛 `RuntimeError`**，不静默返回
  空列表（避免「空洞通过」）。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
python3 spiders/nmc-weather/spider.py          # 单页冒烟，打印 1 行实况
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261004-nmc-weather-23a303a2.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-nmc-weather.json` 只锚**常量标签**：`station == Wqsps`、`source == nmc-weather`、
`url == https://www.nmc.cn/rest/weather?stationid=Wqsps`，以及 `min_rows: 1`。
**绝不锚** 温度/湿度/风速等波动数值、发布时间与日期；`scraped_at`/`timestamp` 进 `whitelist_fields`。