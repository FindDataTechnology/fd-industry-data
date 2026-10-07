# openchargemap 接入档案

Open Charge Map（全球 EV 充电桩众包点位库）POI 取数。入口：
`run_openchargemap(limit: int = 100, country_code="DE", latitude=None, longitude=None, distance_km=None) -> list[dict]`，
返回行 schema 与 manifest `functions[].columns` 对齐，每行带 `source` / `source_url` / `scraped_at`，`limit` 生效。

- 网络：海外源，直连即可（2026-10-07 实测 200，无反爬）。**本机到 OCM 链路约 1/4 概率
  连接层断连/滞留**（`IncompleteRead`/`RemoteDisconnected`/超时；curl 与 urllib 同样中招，
  失败多为 17-60s 长滞留）——单次尝试 TIMEOUT 30s，连接层抖动退避 5/15/30s 至多重试 3 次
  （残余失败 ~0.8%/调用）；服务端已应答的 HTTP 状态错误不重试，重试用尽即红。
- 依赖：**仅标准库**（`urllib.request` / `urllib.parse` / `json` / `os`），无 scrapling、无新增依赖。

## 端点与鉴权

- 端点：`https://api.openchargemap.io/v3/poi/?key=...&maxresults=...&compact=true&output=json`
  + 过滤参数（见下）。
- 鉴权：**`key=` 查询参数**（env `OCM_API_KEY` 注入；官方实锤 `key=` 与 `x-api-key` 头二选一，
  统一按查询参数用）。key 只从 `os.environ` 读取；行内 `source_url` 的 key 一律
  `REDACTED`（凭据不落盘）。
- fair use 无公示数值限速——点位量级小、周任务足够；本爬虫每次调用只发 **1 个请求**。

## 过滤口径（实测坑，2026-10-07）

- **`countrycode=` 只认 ISO alpha-2**：`DE` → CountryID 87 过滤正确；
  **alpha-3（如 `DEU`）静默不过滤**、返回全球最新点位——绝不用 alpha-3。
- 默认排序按新增/核验时间倒序，数据持续增长（首页每日刷新）——**golden 绝不锚总数、
  绝不锚首页特定行**。
- 固定 POI 锚改用**地理半径过滤**：`latitude`/`longitude`/`distance`/`distanceunit=KM`
  实测有效，结果按距离升序。
- `compact=true` 仍返回全结构（引用对象置 null）；`poiids=` 参数实测被忽略（不过滤，禁用）。

## 行字段口径

点位数组元素 → 行：`id`/`uuid`、`AddressInfo`（`title`/`address`/`town`/`postcode`/
`state_or_province`/`country_id`/`latitude`/`longitude`）、`usage_type_id`/`status_type_id`/
`number_of_points`/`date_created`/`date_last_verified`/`is_recently_verified`/`operator_id`/
`data_provider_id`、`num_connections`（Connections 数）与 `max_power_kw`（Connections 内
PowerKW 最大值，无则 None）。非 200 / 空数组 / 非 list → 抛 `RuntimeError`（失败即红）。

## golden 锚（结构常量 + min_rows，绝不锚总数，2026-10-07 实况）

1. `golden/001-openchargemap.json`——DE 列表：params `{"limit": 20}`，min_rows 10，
   锚 `country_id=87`（DE 过滤正确性）与 `source`。
2. `golden/002-openchargemap-poi-uuid.json`——**固定 POI UUID 存在性**：
   params `{"limit": 50, "latitude": 48.4998228, "longitude": 9.1533116, "distance_km": 2}`
   （Reutlingen POI 511254 坐标 2km 半径，实测仅 6 点、按距离升序该点第一），
   锚 `uuid=39B6C0CE-6C7C-4E66-9DF5-93DF49682756`、`id=511254`、`country_id=87`。
   字段集合由 spider schema + manifest `columns` 固化；`min_rows` 只锚下界。

## 真实取数证据（2026-10-07，直连）

- `countrycode=DE&maxresults=5` → 5 点全为 CountryID 87（Reutlingen/Singen/Konstanz…）。
- 同坐标 `distance=2&distanceunit=KM` → 6 点，首点即 POI 511254（dist=0）。
- `countrycode=DEU` 对照 → 返回西班牙/芬兰等全球点（证实 alpha-3 失效）。
- 冒烟（构建时实况）：`python3 spiders/openchargemap/spider.py` → DE 列表 3 行，
  首行 country_id=87、num_connections=2、max_power_kw=240.0。

## 本地自检

```bash
set -a; source /Users/chengsishi/finddata/secrets/fd-industry-source-keys.env; set +a
cd <仓根> && python3 spiders/openchargemap/spider.py              # 冒烟
python3 scripts/health_verify.py --ticket reports/health-tickets/20261007-openchargemap-e92579f3.yaml
python3 scripts/validate_manifests.py && python3 scripts/check_manifest_commands.py \
  && python3 scripts/conformance_gate.py --roots .
```
