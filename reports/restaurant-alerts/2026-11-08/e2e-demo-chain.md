# 端到端演示链留档 — 一条真实披露事件的全链

- 留档日期：2026-11-08（CST）
- 任务：add-restaurant-vertical / 6.3「新披露事件→车道入库→舰值守→预警→板面更新，全程留档」
- 事件：**大家乐集团（00341.HK）「正面盈利預告」，release_date 2026-11-04**
  `https://www1.hkexnews.hk/listedco/listconews/sehk/2026/1104/2026110400600.pdf`

## 0. 结论（先说状态，不含糊）

| 段 | 状态 | 一句话 |
|---|---|---|
| ① 新披露事件 | ✅ | 港交所披露易真实公告，`ann_type=profit-alert`（预警级事件，不只是财报日历） |
| ② 车道入库 | ✅ | `hkex-disclosure` 单元 run 4803 落 crawl store（`rows_written=524`），同公告后续 6 轮重跑幂等只留一条 |
| ③ 预警 | ✅ | 事件台账首次上报 `2026-11-07T16:44:08Z`，键 `hkex:00341:2026-11-04:profit-alert:a004cb53`，当日重跑 `new=0`（幂等） |
| ④ 板面更新 | ✅ | 公司板图④时间轴含该点 `2026-11-04 大家乐集团 · 正面盈利預告`；公开投影板同点同现 |
| ⑤ 舰值守 | ⚠️ **盲读** | 舰队首轮 run 准点触发、产出 2 份值守记录，但工具面未挂载 → 记录内容是**如实报障**，不是本事件的读数 |

**因此 6.3 暂不勾**：四段机械腿全通，值守腿只证明"链路触发+留档"，不证明"读到数据"。该缺陷已在 4.6 定性挂账（`installMcpRef` 只收 `origin==="registry"` + 凭据/组双闸）；4.6 修好后重跑本事件即可把本段补成实读，届时一并勾 6.3。此处不拿"跑过了"冒充"读到了"。

## 1. 事件选取理由

- 在监控宇宙内：`docs/restaurant-universe.yaml` L91 `code: "00341.HK"` / `name_en: Cafe de Coral Holdings Ltd.`
- 事件类型是 `profit-alert`（正面盈利预告）——同时落在
  `config/restaurant-alerts.json` 的 `events.hkex_profit_warning_types` 与公司板的
  `event_types={results, profit-alert, profit-warning}` 两个消费面，能一次性贯通
  「披露 → 预警 → 板面」三段，而不是只能证一段。
- 上游原文可回指：`doc_url` 指向披露易 PDF 本体。

## 2. 实际时间线（UTC，非按任务书顺序重排）

| 时刻 (UTC) | 段 | 落点 |
|---|---|---|
| 2026-11-07 15:45:11 → 15:46:52 | ② 车道入库 | `crawl_runs.id=4803` `status=success` `rows_written=524`，本公告 `crawl_items.idx=13` |
| 2026-11-07 16:44:08 | ③ 预警 | `state/events.jsonl` 第 118 行首次上报 |
| 2026-11-07 17:41:00 | ⑤ 舰值守 | `run_b32cebb8918f4c3d9801`（cron 准点，turn 正常结束） |
| 2026-11-07 17:50:43 → 17:52:17 | ② 复跑 | run 4829 重跑同一公告，仍只有一条 |
| 2026-11-08 02:03 (CST) = 2026-11-07 18:03Z | ④ 板面重建 | `restaurant-company.html` / `.spec.json` 重出 |

任务书写的是「……→舰值守→预警→……」，实际时序是**预警早于值守首轮**（预警 16:44Z / 值守 17:41Z）。这里按实际时刻记录，不为了对齐任务书顺序而改时间。

## 3. 逐段证据

### 3.1 新披露事件（crawl_items 原始 payload）

```json
{"stock_code": "00341", "stock_name": "大家樂集團", "ann_type": "profit-alert",
 "title_zh": "正面盈利預告", "release_date": "2026-11-04",
 "doc_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/1104/2026110400600.pdf",
 "scraped_at": "2026-11-07T17:50:43.612584+00:00",
 "is_profit_warning": false,
 "headline_status": "not_applicable", "headline_revenue": null,
 "headline_net_income": null, "headline_period_label": null}
```

`headline_*` 全 null、`headline_status=not_applicable` 是**如实缺测**：该单元只解析公告标题与元数据，正文不解析 → 不推断营收/利润数字。

### 3.2 车道入库

- 单元：`spiders/hkex-disclosure`，镜像 tag `local-hk-kpi`，`commit_sha` 记录为 `unknown`（本地跑，见 6.4 挂账）。
- 首次落地：run `4803`，`started_at 15:45:11Z` / `finished_at 15:46:52Z` / `rows_written=524` / 本公告 `idx=13`。
- 幂等：同一 `doc_url` 在 4814 / 4815 / 4823 / 4824 / 4826 / 4829 六轮里反复出现，消费面按 `doc_url` 去重后仍是一条（公司板 `_hk_events` 与事件扫描都用 `_dedupe_latest(..., "doc_url")`）。
- **口径**：公告面入 **crawl store**（`crawl_runs`/`crawl_items`），不产 `semantic_observations`。查库可知 `00341.HK`（entity 301039）观测行 = **0 条**，这是设计如此（公告正文不解析、不造数字），不是丢数；观测面只装真有数值序列的概念。

### 3.3 预警

`state/events.jsonl` 第 118 行（append-only，首次上报即留痕）：

```json
{"key": "hkex:00341:2026-11-04:profit-alert:a004cb53",
 "kind": "profit_warning", "source": "hkex-disclosure", "date": "2026-11-04",
 "reported_at": "2026-11-07T16:44:08.888729+00:00",
 "ref": {"title": "正面盈利預告", "doc_url": ".../2026110400600.pdf",
         "ann_type": "profit-alert", "entity": "00341", "entity_name": "大家樂集團"}}
```

- 自然键含标题哈希（`a004cb53`），同一公告改标题不会误判成同一条。
- 幂等复跑（`2026-11-08/events.json`）：`scanned_total=198` / `ledger_seen_before=198` / `new=0` / `skipped_seen=198` → 第二次运行不产生第二条上报。
- `ref` 保留了回指原文的三件套（title / doc_url / entity），审计时可直接点回披露易。

### 3.4 舰值守（盲读，如实标注）

- 舰：`fleet_1794070918358_8fyq8t`（三岗 监视/合成/产出），信封 v2 `2vCK2UDKScEU8sqoG5lo0Q`。
- 首轮：`run_b32cebb8918f4c3d9801`，2026-11-07T17:41:00Z 准点触发，turn 正常结束，产出 **2 份值守记录**。
- 记录内容（原文摘要）：`list_mcp_resources(server="fd-open-data-mcp")` →
  `MCP resource server "fd-open-data-mcp" is unavailable in this agent's scope`；`tool_search` 零命中；
  **11 组核心概念全部零读数**；`gaps_attributed=11` 统一归 `access_not_enabled`，并声明"序列本身是否断档无从判断"。
- 判读：这段**不能**当作"值守舰读到了本次盈警"。它证明的是——值守链路能按 cron 真触发、能产出可留档的值守报告、且**没数据时不编数据**。
- 根因（4.6 已定性，装包报告原文）：`installed_packs.report.mcpServers` 三条全 `status:"unavailable"`,
  `reason:"not a registry server (packs may only reference registry entries or bind a local server of the same name)"`
  —— `paas/pack-store.js::installMcpRef` 只收 `entry.origin === "registry"`，而 `registry-bridge.js::mapFacetServer`
  在 facet-catalog 模式下把条目全标成 `origin:"facet-catalog"`；叠加过期凭据（platform 2026-11-04 / 本地 2026-10-29 过期）与组闸（`required_groups=["analysts","sl63fy08ruh9"]` vs owner `["creators"]`）。

### 3.5 板面更新

- 公司板图④（`chart-hk-events`）数据点：`2026-11-04 大家乐集团 · 正面盈利預告`（`value=[17, 6]` = 第 18 个日期 × 第 7 家港股）。
- 图④来源行（板内注释原文）：`港交所披露易公告（标题与元数据） · 已抓取公告 905 条 · 2024-12-13~2026-11-06 · 本图只画业绩公告 20 条 + 正面盈利预告 2 条 · 盈警（profit warning）本期 0 条 · 仅标题与元数据，公告正文未解析，不推断营收/利润数字`
- 公开投影板（`restaurant-company-public.html`）同一数据点同现 → 公开/内部两块板读同一个事件面，没有形状漂移。
- 板面在入库（15:46Z）与预警（16:44Z）之后重出，说明"板面更新"来自真库读，不是静态写死。

## 4. 留档清单与 sha256

| 产物 | 证明什么 | sha256（前 16） |
|---|---|---|
| `fd-industry-data/reports/restaurant-alerts/state/events.jsonl` | 预警首报键 + 上报时刻 + ref 回指 | `9d3a7f6575d4d46a` |
| `dashboards/restaurant-company.spec.json` | 图④事件点已进 spec（三源渲染前态） | `d9039271783ce3bb` |
| `dashboards/restaurant-company.html` | 渲染后板面真含该点 | `c3fc871e0a5fbb8e` |
| `fd-industry-data/reports/restaurant-alerts/2026-11-08/duty-log.md` | 当日值守档案（健康/阈值/事件三级） | 见该文件 §7 |

## 5. 复现命令

```bash
# ② 入库（真库读）
psql "$FD_OPEN_DATA_DSN" -c "select r.id,r.started_at,r.rows_written,ci.idx
  from crawl_items ci join crawl_runs r on r.id=ci.run_id
  where r.source='hkex-disclosure'
    and ci.payload->>'doc_url'='https://www1.hkexnews.hk/listedco/listconews/sehk/2026/1104/2026110400600.pdf'
  order by r.started_at";
# ③ 预警（幂等复跑）
python3 scripts/restaurant_alerts.py --events --asof 2026-11-08
# ④ 板面
cd ../dashboards && python3 build_restaurant_company_spec.py && python3 build_dashboard.py restaurant-company.spec.json restaurant-company.html
# ⑤ 值守（需平台会话；k8s 隧道通则）
#   GET /api/fleets/fleet_1794070918358_8fyq8t/runs/run_b32cebb8918f4c3d9801
```

## 6. 本链暴露而未修的（指向 6.4 汇总）

1. 值守腿盲读 → `installMcpRef` origin 判断 + 凭据刷新 + 组闸（4.6）。
2. 值班舰首轮 model 被日预算打爆 → 已改派 `glm-5.3-flash`，但 cell 级预算口径仍是隐患（记一笔）。
3. `crawl_runs.commit_sha='unknown'`：本地跑没有提交锚点，出产线镜像后应带 sha（不改代码，仅记账）。
