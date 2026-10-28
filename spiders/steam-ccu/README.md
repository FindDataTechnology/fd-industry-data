# steam-ccu —— Steam 在线人数（CCU）+ 评论聚合

> **⚠️ 需代理出口**：`api.steampowered.com` 与 `store.steampowered.com` **均被墙**。
> 测试/本地必须 `export HTTPS_PROXY=http://<proxy>:<port>`（urllib 缺省尊重该
> 环境变量，本单元不硬编码代理地址）。
> （历史说明：曾以「manifest 不写 `site`/`schedule`，静默合入」方式等待代理注入；
> 当前 manifest 已带 `schedule: "56 7 * * *"`，`site` 仍未写——**schedule/cron 与
> 点亮属人工门，spider-heal 一律不动**。）

批次二 Wave C 直建（机器重启恢复，无工单，入口在 PR body 手工留证）。
侦察簿：`data-source-scouting/INDUSTRY-DATA-SOURCES.md`「★接」行
（决策记录 #7：文娱接 Steam，列 Wave C）。免 key。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| CCU 在线人数 | `GET https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=<id>` | 免 key；`{"response":{"player_count":<int>,"result":1}}`；坏/无统计 appid → HTTP 404 |
| 评论聚合 | `GET https://store.steampowered.com/appreviews/<id>?json=1&num_per_page=0&language=all&purchase_type=all` | 免 key；`query_summary`（review_score/desc、total_positive/negative/reviews）；`num_per_page=0` 只取聚合、不拉正文不翻页。**2026-10 实测该 JSON 子表面已退役**（302 → `store.steampowered.com/` HTML 落地页）→ 评论列降级 `null` |

入口：`run_steam_ccu(limit=100) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib` + `json` + `time`，无新依赖。

## 种子口径

固定 12 个热门 app（代码常量 `SEEDS`，每 app 一行）：CS2(730)、Dota 2(570)、
PUBG(578080)、GTA V(271590)、Apex(1172470)、Rust(252490)、R6 Siege(359550)、
Destiny 2(1085660)、CoD(1938090)、黑神话·悟空(2358720)、永劫无间(1203220)、
怪物猎人·荒野(2246340)。

- **名称是种子元数据**（两个端点均不回名称），非抓取字段；appid 失效由
  失败留痕暴露（整行跳过）。
- 单 app 两请求（CCU + 评论），app 间 0.2s 礼貌间隔；无重试、无频次放大。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `appid` / `name` | 种子常量 | app 标识 + 种子名 |
| `ccu` | `response.player_count` | **实时数**，每次调用都在波动；`result != 1` 视为该 app 失败 |
| `review_score` / `review_score_desc` | `query_summary` | 0-10 档位 + 标签（Very Positive 等） |
| `total_positive` / `total_negative` / `total_reviews` | `query_summary` | 全语言、全购买类型评论计数（非实时，缓慢累计） |
| `positive_ratio` | 派生 | `total_positive/total_reviews*100`，**round 4 位**；无评论时 `null` |
| `url` | 请求 URL | 行级来源可追溯（appreviews 端点） |
| `source` | 常量 | `steam-ccu` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

> **评论列 null 口径（2026-10 起）**：评论子表面退役时，`review_score` /
> `review_score_desc` / `total_positive` / `total_negative` / `total_reviews` /
> `positive_ratio` 记为 `null`（**留痕，不静默填 0**），`ccu` 主数据面照常产出。

## 坑位与容错（全部实测）

- **双域均被墙**：api 域与 store 域都要代理；无代理出口时连接直接失败。
  单元用 urllib 缺省代理行为（环境变量），不硬编码代理地址。
- **两路分级失败（2026-10 修复）**：CCU 是**主数据面**，失败（HTTP 404 /
  形态异常）即整 app 跳过并留痕；评论是**子表面**，失败只把该行评论列置
  `null` 并留痕、行仍成立。旧实现两路同处一个 `try`，评论挂掉会连带整行
  被丢——CCU 数据被误杀，是工单 `20261025-steam-ccu`（rows=0）的根因。
- **评论 JSON 子表面已退役（2026-10 实测）**：`appreviews` 请求 302 重定向至
  `https://store.steampowered.com/` 并返回 HTML（**六组 UA/头部组合均同**，
  结论与 UA 无关）。故评论列持续为 `null`，`ccu` 正常。**未证实候选**：响应头
  `access-control-allow-origin: *.steambase` 暗示该面已迁至 `*.steambase`
  主机——**未验证**，需另开 `generate` 工单寻替代表面。
- **CCU 是实时数**：值分钟级波动（实测两次相隔数分钟的调用 971138 →
  1058440），**golden 只锚结构断言 + min_rows，绝不锚任何数值**
  （含 ccu、total_*、positive_ratio、review_score）。
- 评论端 `success != 1` 视为失败（实测正常恒为 1）；响应非 JSON 视为失败——
  现已带 HTTP 状态 / Content-Type / 最终 URL 落进错误信息（如
  `reviews: 非 JSON 子表面（HTTP 200, content-type=text/html, final=...）`）。
- 站点对 UA 不敏感（缺省 Python UA 实测可通）；本单元仍带常规浏览器 UA
  统一姿态，不做指纹伪装与频次对抗，遇风控升级按协议转人工，不逆向。
- **全部 app 取数失败抛 `RuntimeError`**（失败即红，不静默返回空列表）；
  仅评论列降级**不**触发抛错（行仍成立，降级留痕走 stderr）。
- **schedule 已点亮**（manifest `56 7 * * *`），`site` 仍未写；schedule/cron
  与总闸**由人工门负责，spider-heal 不改**。
- manifest `entity_type: product` 为新自由值（既有枚举 region/country/
  industry/world/company/movie 无「游戏作品」位）；语义治理轨道另行定名，
  不阻塞爬取。

## 历史深度与节奏

CCU 为瞬时值（无历史面）；评论计数为全量累计（约 2013 年起，随时间单调
增长）。日级抓取可见 CCU 时段曲线与评论累计趋势。

## 实测样例行与证据（2026-10-07 经代理抓取）

```
appid=730, ccu=971138(实时), review_score=8, review_score_desc="Very Positive",
total_positive=8502000, total_negative=1405312, total_reviews=9907312
```

## 自检（均需代理出口）

```bash
python3 spiders/steam-ccu/spider.py            # 冒烟，打印最近 3 行
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/validate_manifests.py          # v2 调度契约校验
python3 scripts/conformance_gate.py --roots .  # 布局门
```

## golden 断言口径（跨期稳定）

`golden/001-steam-ccu.json`：`min_rows: 5`（12 种子，留足失效余量）+
结构常量断言（`appid == 730`、`name == "Counter-Strike 2"`、
`source == "steam-ccu"`）。**绝不锚数值**：`ccu` / `total_*` /
`positive_ratio` / `review_score` / `review_score_desc` 全部进
`whitelist_fields`（实时或随口碑漂移）。重放即真取数，需代理出口。
