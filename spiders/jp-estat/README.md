# jp-estat 接入档案

日本政府统计 e-Stat API（v3.0，app/json 形态）。入口：
`run_jp_estat(limit: int = 100, search_word="人口", stats_data_id=None) -> list[dict]`，
返回行 schema 与 manifest `functions[].columns` 对齐，每行带 `source` / `source_url` / `scraped_at`，`limit` 生效。

- 网络：海外源，直连即可（2026-10-07 实测 200，无反爬）。
- 依赖：**仅标准库**（`urllib.request` / `urllib.parse` / `json` / `os`），无 scrapling、无新增依赖。

## 端点与鉴权（两步流）

1. `GET https://api.e-stat.go.jp/rest/3.0/app/json/getStatsList?appId=...&searchWord=人口&limit=10`
   → `GET_STATS_LIST.RESULT.STATUS == 0` + `DATALIST_INF.TABLE_INF[]`（`@id` = statsDataId）
2. `GET https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData?appId=...&statsDataId=...&limit=...`
   → `GET_STATS_DATA.STATISTICAL_DATA.DATA_INF.VALUE[]`（行字段 `@time`/`@cat01`/`@unit`/`$`）

- 鉴权：**`appId=` 查询参数**（env `ESTAT_APP_ID` 注入；portal 注册后即时自動発行）。
  key 只从 `os.environ` 读取；行内 `source_url` 中 appId 一律替换为 `REDACTED`（凭据不落盘）。
- 官方未公示数值化限速——低频分页即可；本爬虫每次调用发 1-2 个请求
  （`stats_data_id` 直供时只发 getStatsData 一个），控频由调用方负责。

## 行字段口径

- `RESULT.STATUS != 0` 或表列表空或数据行空 → 抛 `RuntimeError`（失败即红）。
- `TABLE_INF` / `VALUE` 单条时上游给 dict 而非 list——两形态都接。
- 值 `$` 为字符串；数值化成 float 才透出，抑制码（`-`/`X`/`na`）行跳过，绝不造数。
- `@time` 原样透出（`1993000000` = 1993-10-01 基准点），语义由表 meta 解释。

## golden 锚（固定 statsDataId + 固定历史期值，2026-10-07 构建时实况冻结）

- statsDataId `0000150041`：人口推計 平成5年10月1日現在推計人口（cycle 年次，updated 2025-10-03，
  定稿历史表，全表 87 行）。
- 锚行 `@cat01=001`（男女計）`@cat02=000`（全年齢）`@area=00000`（全国）`@time=1993000000`
  `@unit=人` → **value 124451938**（1993-10-01 日本总人口）。
- `golden/001-jp-estat.json`：params `{"limit": 100, "stats_data_id": "0000150041"}`，
  min_rows 50（全表 87 行，回补冗余），另锚 `stats_data_id`/`time`/`cat01`/`unit`/`source`。

## 真实取数证据（2026-10-07，直连）

- `getStatsList?searchWord=人口&limit=3` → STATUS 0，首表 `0000150041`（人口推計 平成5年…）。
- `getStatsData?statsDataId=0000150041&limit=100` → STATUS 0，87 行，首行即锚行
  （`$ == "124451938"`）。
- 冒烟（构建时实况）：`python3 spiders/jp-estat/spider.py` → 两步流 3 行，
  stats_data_id=0000150041、time=1993000000、value=124451938.0 起。

## 本地自检

```bash
set -a; source /Users/chengsishi/finddata/secrets/fd-industry-source-keys.env; set +a
cd <仓根> && python3 spiders/jp-estat/spider.py                    # 冒烟（两步流）
python3 scripts/health_verify.py --ticket reports/health-tickets/20261007-jp-estat-55b64112.yaml
python3 scripts/validate_manifests.py && python3 scripts/check_manifest_commands.py \
  && python3 scripts/conformance_gate.py --roots .
```
