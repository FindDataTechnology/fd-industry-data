# Cleanup archive index — dormant-unit-hygiene

Generated at: 2026-10-04
Change: `openspec/changes/dormant-unit-hygiene/`（仓储：finddata 工作区）
Evidence: 三路只读实跑侦察（2026-10-04）+ manifest 命令 lint（`scripts/check_manifest_commands.py`）

处置总则：休眠（无 schedule）单元经实跑证据定性——域死/入口坏死/政策停爬/无独有能力者归档；
被运行时契约面（provider/catalog MCP 命令）引用者转为规范保留。

| # | 原路径 | 处置 | 证据摘要 |
|---|---|---|---|
| 1 | `spiders/yn-agriculture` | 归档 | 入口体死于 `ModuleNotFoundError: scrapy`；目标域 `nongye.yn.gov.cn` NXDOMAIN；与 yunnan-flowers/kunfloralexport 同模板拷贝 |
| 2 | `spiders/yunnan-flowers` | 归档 | 同模板拷贝；入口同上；`flowers.yunnan.gov.cn` NXDOMAIN |
| 3 | `spiders/flowers-yunnan` | 归档 | 与 #2 同站（已 NXDOMAIN）；`scrapling.DefaultFetcher/Request/Response` 导入即坏（0.4 已移除）；已在 quarantine 名单 |
| 4 | `spiders/kunfloralexport` | 归档 | 同模板第三份拷贝；`kunfloralexport.com` NXDOMAIN；入口体死于 scrapy |
| 5 | `spiders/flower-association` | 归档 | `chinaflower.org.cn` NXDOMAIN（实跑 0 行，日志全 DNS 失败） |
| 6 | `spiders/flower-trading` | 归档 | `kunmingflower.com` 解析至停放 IP、直连/代理均超时（实跑 280s 0 行） |
| 7 | `spiders/stat-gov` | 归档 | 目标域 `www.stat.gov.cn` 不存在；NBS 范围由在役 nbs-stats 覆盖；入口 `update_settings` AttributeError |
| 8 | `spiders/nhc` | 归档 | 模板入口硬损坏（`update_settings` AttributeError）；站点 WAF 412；解析仅得空 title 占位。若卫健委需求确凿，应新建专用单元 |
| 9 | `spiders/wanfangdata` | 归档 | 入口硬损坏；仅首页 meta；无独有能力（cqvip 为同类可跑代表） |
| 10 | `spiders/people-daily` | 归档 | 既有路径全部失效（`/rmrbs/data` 404、纸质版路径 404），驱动 parse 0 产出；无 `run_*` 入口 |
| 11 | `spiders/toutiao-open` | 归档 | 无入口（仅类）；manifest command 写类名；解析面与 2026 JSON 热榜面结构性不匹配（数据本身免 key 可取——如需应立新适配器） |
| 12 | `spiders/weibo-open` | 归档 | 无入口；热搜面落 `passport.weibo.com` 登录墙；OAuth2 凭据依赖无实现 |
| 13 | `spiders/wechat-mp` | 归档 | 无入口；搜狗微信正文层被 antispider 中间页拦截（不逆向反爬）；官方 API 需认证公众号 |
| 14 | `spiders/polygon` | 归档 | 政策停爬金融行情；站点改版 polygon.io→massive.com；CLI `NameError: logging`、scrapy 未安装、SQLite `col_str` 未定义——全链条断裂 |

**保留（未归档）**：

- `spiders/nbs_gdp` —— 规范保留：新增 `run_nbs_gdp(limit)` 包装（`get_macro_data`/`get_gdp_quarterly` 原名保留供 provider/catalog MCP 命令面）；修复 `_fetch_nbs_direct` 的 scrapling 0.4 API（`session.fetch`→`async with FetcherSession(...).get()`）；akshare 回退行单位由「亿元」修正为「%」（同比增速口径）。
- `spiders/flower-auction` —— 族内唯一真实活源（kifa.net，WAF 468 需非云出口），已主动 parked；README kifc.cn 误导修正。

回滚：`git mv archive/triage-cleanup-20261004/spiders/<slug> spiders/<slug>` 即可复原（全部内容随归档保留）。