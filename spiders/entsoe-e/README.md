# entsoe-e —— ENTSO-E 透明平台电力序列（Web API）

批次二 Wave B 收尾单元（B-c，唯一人工审批源）。**securityToken 鉴权**
（env `ENTSOE_SECURITY_TOKEN`）。集群内直连 `web-api.tp.entsoe.eu` 实测
HTTP 200（2026-10-14），**不需出口代理**（与 census/socrata 等被墙主机不同）。

## 1. 数据表面

| 项 | 值 |
|---|---|
| 平台 | ENTSO-E Transparency Platform（欧洲输电运营商联盟） |
| 端点 | `GET https://web-api.tp.entsoe.eu/api` |
| 鉴权 | `securityToken` **查询参数**（env `ENTSOE_SECURITY_TOKEN`；人工审批发放） |
| 文档类型 | `A75` 分燃料实际发电（默认）/ `A65` 总负荷 |
| 区域 | EIC 码（默认 `10YCZ-CEPS-----N` 捷克） |
| 频度 | 15 分钟 / 30 分钟 / 60 分钟（由 `resolution` 决定；A75/A65 实测 PT60M） |
| 历史深度 | 2015 起（平台留存；默认滚动回看 7 个完整 UTC 日） |

查询参数（A75）：`documentType=A75&processType=A16&in_Domain=<zone>&periodStart/End`

行样例（实测 2026-10-14，CZ 2020-01-01）：

```text
period=2020-01-01T00:00Z position=1 quantity_mw=232.21 psr_type=B01
business_type=A01 zone=10YCZ-CEPS-----N resolution=PT60M
```

输出列：`period`（UTC 小时桶起点，按 resolution 步进）+ `position` +
`quantity_mw`（MW）+ `psr_type`（A75 燃料码；A65 为 null）+ `business_type` +
`zone` + `resolution` + `url`（**token 已 REDACTED**）/ `source` / `scraped_at`。

psrType 燃料码（实测 CZ 一周出现 15 类）：B01 生物质 / B02 褐煤 / B03 天然气 /
B04 硬煤 / B05 燃油 / B06 水电 / B10 抽蓄 / B11 风电 / B12 太阳能 / B14 核能 /
B15 其他可再生 / B16 其他 / B17 废弃物 / B19 其他化石 / B20 未知。

## 2. 鉴权与口坑（实测逐条）

| 事项 | 实测结论 |
|---|---|
| token 传递 | **只能查询参数**：`securityToken` 请求头形式返 401 code 999 "Authentication failed."（2026-10-14 本机+集群双实测）；查询参数返 200 |
| 无 token | HTTP 401 + `Acknowledgement_MarketDocument` code 999——单元显式 `RuntimeError`，**不裸跑**（审批源纪律） |
| 无效 zone | HTTP 400 + "No matching data found for Data item ..."——单元显式 `RuntimeError` |
| 时间格式 | **UTC `YYYYMMDDHHMM` 硬格式**；`periodStart`≥`periodEnd` 服务端 400 |
| 返回格式 | 只返 XML（`GL_MarketDocument`，命名空间 `urn:iec62325.351:tc57wg16:451-6:...`）——`xml.etree` 按本地名解析 |
| 限速 | 社区口径 ~400 次/分（超限临时封 10 分钟）——每次调用只发 1 个请求，≤1 req/s 自限；429/5xx 退避 2s 重试一次仍失败即红 |
| 凭据卫生 | token 只走请求 URL；所有行 `url` 字段与异常文本均 REDACTED（凭据永不入库/入日志） |

## 3. 信号选择理由

A75（分燃料实际发电）是欧洲电力结构最核心的公开序列：分燃料粒度 × 小时级
频度 × 2015 起历史，用于能源转型跟踪（风光占比、核电/煤电退出）与电力负荷
分析。A65（总负荷）是需求侧对应序列。两个文档类型同构（`GL_MarketDocument`），
同一解析栈覆盖；参数 `document_type` 切换。

**放弃的兄弟端点**：A44 日前电价（仍受支持，但电价类数据平台已有其他线，
且需要 `in_Domain`+`out_Domain` 双域语义）；A11/A24 等发电预测（预测值非统计
观测）；跨境流（A11）需成对 zone——均留作后续扩展参数化，不在本单元首发范围。

## 4. 运行

```bash
# 默认：A75 捷克，最近 7 个完整 UTC 日（生产日跑）
python3 -m fd_industry_data.runner_cli entsoe-e --limit 100

# 显式窗口（golden 锚定用途）
python3 -c "from spiders.entsoe_e.spider import run_entsoe_e; \
  run_entsoe_e(limit=20, period_start='202001010000', period_end='202001020000')"
```

- `limit` 语义：按期次升序保留**最近** `limit` 行（生产取最新）；
  A75 一周约 2,600 点，默认 `--limit 100` 取最新 100 点。
- 缺 `ENTSOE_SECURITY_TOKEN` 立即 `RuntimeError`——**token 未注入时不启动**。

## 5. 历史与凭据

- 账号 `aloadtree@gmail.com` 于 2026-10-07 注册并发出 API 访问申请信
  （`.batch2/waveb-applications/entsoe-e-application-email.md`）；
- **2026-10-14 token 自助生成**（My Account → Web API Access → Generate Token，
  平台侧状态 Generated/ACTIVE）；本机经 Keycloak 登录后页面路由
  `myAccount/webApiAccess` 生成（旧 `/myAccount/clientmanagement` 已 404）；
- token 注入：集群 secret `fd-industry-source-keys` 键 `ENTSOE_SECURITY_TOKEN`
  （envFrom 全量注入，2026-10-14 集群内 env 实测 len=36 到位）；
  本地副本 `secrets/fd-industry-source-keys.env`（不入 git）。
