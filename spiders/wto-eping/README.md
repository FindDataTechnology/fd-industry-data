# wto-eping —— WTO ePing 最新贸易壁垒通报流

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-wto-eping-51734044.yaml`
（`kind=generate`，brief 侦察簿 W3）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 最新通报 | `GET https://eping.wto.org/api/v1/notifications/getLatestNotifications` | 公开 REST，**免鉴权匿名可用**，返回 JSON 数组，单次实测 10 行，TBT/SPS 混排、当日新鲜 |

入口：`run_wto_eping(limit=100) -> list[dict]`（`spider.py`）。仅用标准库 `urllib`/`re`/`html`，无新依赖。

## 参数白名单

**零查询参数。** 实测带 `language=EN` 即 HTTP 400——只带常规浏览器 UA/`Referer`。
**请求头按精确值**：`Accept: application/json` 返 JSON；复合值（`application/json, text/plain, */*`）
实测回落 `application/xml`，故只发精确头。HS 编码 + 日期检索需免费注册（brief 明示本批只接
匿名最新流；1995 起全库在注册面，未接）。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `notification_id` | `documentSymbol`（strip） | WTO 文件号（如 `G/SPS/N/KHM/3`；原文带前导空格） |
| `type` | 派生 | `G/SPS/...` → `SPS`，`G/TBT/...` → `TBT`；其余系列为 `None`（不丢真） |
| `member` | `notifyingMember` | 通报成员（如 `Cambodia`、`Kuwait, the State of`） |
| `date` | `distributionDate` | 上游 `dd/mm/yyyy` → 归一 ISO `YYYY-MM-DD` |
| `date_raw` | `distributionDate` | 原样保留（`06/10/2026`） |
| `title` / `products` | 同名字段 | 富文本 HTML 去标签 + 反转义 + 压空白 → 纯文本 |
| `url` / `source` / `scraped_at` | 常量/本地 | 追溯与标识 |

`limit` = 返回行数上限（上游单次至多 10 行，>10 也只有 10）。

## 坑位与容错（brief.notes 逐条落实）

- **匿名可用**：无需注册/鉴权；仅带常规 UA，不做指纹伪装、不做频次对抗——遇风控升级按协议
  转人工（换注册数据表面），不逆向。
- **不收参数**：见上，任何查询参数都可能 400，故零参数。
- **富文本脏字段**：`title`/`products` 含 `<p>`/`<strong>`/`&nbsp;`，统一清洗；清洗后空串归 `None`。
- **失败即红**：单一数据表面，任何 HTTP/解析失败或 0 行 → 抛 `RuntimeError`，不静默回空。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 实测样例行（2026-10-07 冒烟）

```json
{"notification_id": "G/SPS/N/KHM/3", "type": "SPS", "member": "Cambodia",
 "date": "2026-10-06", "date_raw": "06/10/2026",
 "title": "The Law on Fisheries"}
```

## 自检

```bash
python3 spiders/wto-eping/spider.py          # 单页冒烟，打印 2 行最新通报
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-wto-eping-51734044.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-wto-eping.json` 只锚**常量结构字段**（brief「锚固定结构字段」）：`source == wto-eping`、
`url == <feed 端点>`，以及 `min_rows: 1`。最新流内容逐日滚动，**绝不锚** 具体文件号/成员/日期/类型
（某日可能全为单一系列）；`scraped_at`/`timestamp` 进 `whitelist_fields`。
