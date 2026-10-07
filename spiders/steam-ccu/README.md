# steam-ccu —— Steam 在线人数（CCU）+ 评论聚合

> **⚠️ 需代理出口**：`api.steampowered.com` 与 `store.steampowered.com` **均被墙**。
> 测试/本地必须 `export HTTPS_PROXY=http://<proxy>:<port>`（urllib 缺省尊重该
> 环境变量，本单元不硬编码代理地址）；**集群 runner 代理注入就绪前不点亮**
> ——manifest 刻意不写 `site` 与 `schedule`，静默合入。

批次二 Wave C 直建（机器重启恢复，无工单，入口在 PR body 手工留证）。
侦察簿：`data-source-scouting/INDUSTRY-DATA-SOURCES.md`「★接」行
（决策记录 #7：文娱接 Steam，列 Wave C）。免 key。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| CCU 在线人数 | `GET https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=<id>` | 免 key；`{"response":{"player_count":<int>,"result":1}}`；坏/无统计 appid → HTTP 404 |
| 评论聚合 | `GET https://store.steampowered.com/appreviews/<id>?json=1&num_per_page=0&language=all&purchase_type=all` | 免 key；`query_summary`（review_score/desc、total_positive/negative/reviews）；`num_per_page=0` 只取聚合、不拉正文不翻页 |

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

## 坑位与容错（全部实测）

- **双域均被墙**：api 域与 store 域都要代理；无代理出口时连接直接失败。
  单元用 urllib 缺省代理行为（环境变量），不硬编码代理地址。
- **坏 appid 两端表现不一致**：CCU 端 HTTP 404（urlopen 抛 HTTPError）；
  评论端 HTTP 200 + `success=1` 但 `total_reviews=0`、
  `review_score_desc="No user reviews"`（不报错）——所以行是否成立以
  「两路都成功」为准，单路失败即整 app 跳过并留痕。
- **CCU 是实时数**：值分钟级波动（实测两次相隔数分钟的调用 971138 →
  1058440），**golden 只锚结构断言 + min_rows，绝不锚任何数值**
  （含 ccu、total_*、positive_ratio、review_score）。
- 评论端 `success != 1` 视为失败（实测正常恒为 1）；响应非 JSON 视为失败。
- 站点对 UA 不敏感（缺省 Python UA 实测可通）；本单元仍带常规浏览器 UA
  统一姿态，不做指纹伪装与频次对抗，遇风控升级按协议转人工，不逆向。
- **全部 app 失败抛 `RuntimeError`**（失败即红，不静默返回空列表）。
- **无 schedule 无 site**：代理注入就绪前不点亮（静默合入）。
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
