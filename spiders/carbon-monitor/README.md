# carbon-monitor 接入档案（Carbon Monitor 日频 CO2 / 分燃料能源）

- 单元：`spiders/carbon-monitor/`，入口 `run_carbon_monitor(limit: int = 100) -> list[dict]`
- 网络：海外源，**直连可用，无需代理**【直连=2/代理=0】（2026-10-02 实测：carbon_global 与
  energy_global 两次全量拉取均直连成功，代理链路未触发）
- 语种/编码：CSV UTF-8，无 BOM

## 端点

```
GET https://datas.carbonmonitor.org/API/downloadFullDataset.php?source=<slug>
```

- 全量下载型接口：无分页、无日期过滤参数，每次返回整个数据集（carbon_global 约 34 MB）。
- 旧 `db.` 域名已废弃，禁用。
- 传输约束（硬性）：必须压缩传输（请求头 `Accept-Encoding: gzip`，curl_cffi 自动解压；
  spider 另带 gzip 魔数兜底解压）+ **长超时 ≥300s**（spider 取 540s：实测顺利时 34 MB 约
  2.5~4 分钟，服务端也会限速，实测出现过 300s 仅收到 7.6MB 的时段）。

## 鉴权

免鉴权。无 cookie/token， impersonate="chrome120" 即可。

## 参数白名单（只发实测有效参数，多余参数一律不发）

| source 取值 | 内容 | 实测状态 |
|---|---|---|
| `carbon_global`（默认） | 全球日频 CO2 排放，国家×部门，Mt CO2/day（上游文档口径） | 有效，200 + CSV |
| `energy_global` | 全球日频能源消费（分燃料 sector：Coal/Gas/Oil/Nuclear/Hydroelectricity/Solar/Wind/Other sources） | 有效，200 + CSV |

**电力分燃料数据集的发现记录**（brief 要求第二能力，实测 2026-10-02）：该端点**不存在**
名为 electricity 的 source。以下取值全部返回 HTTP 200 + PHP Fatal error 页面（因此
spider 在解析前做 CSV 表头嗅探，错误页一律按失败跳过）：
`carbon_electricity`、`electricity`、`world_electricity`、`china_electricity`、
`electricity_global`、`global_electricity`、`elec_global`、`carbon_power`、`power_global`。
官方站（carbonmonitor.org/downloadData.php）实际出货的分燃料伴随数据集是 `energy_global`
（即站内电力分燃料图表的数据源口径），故第二能力以 `source="energy_global"` 实现：

```python
run_carbon_monitor(limit=100, source="energy_global")
```

同端点还有 `carbon_china / carbon_eu / carbon_us / carbon_cities`（官方下载页可见），本轮
未接入、白名单不发，留待后续扩容。

## 容错规则（spec 纪律）

- **单次尝试，失败记日志跳过，绝不连环重试**。scrapling 内建重试必须关掉：`FetcherSession
  (retries=1)`（scrapling 语义 retries=总尝试次数；`retries=0` 有上游 bug，会话打不开报
  "No active session available"，故 1 是纪律下限）。
- 代理兜底仅当**直连在连接层失败**时触发：先探测 `127.0.0.1:7890` 端口，通则经
  `FetcherSession(proxy=...)` 复测一次；HTTP 4xx/5xx/错误页=已触达服务端，一律不再重试。
- 网络模式粘滞（NET_MODE）：解析成功后记住 direct/proxy，后续请求沿用。
- 解析失败（日期非法/数值非法/列数不足）留空跳过，不写脏行。
- 日频任务天然至多一次/天（manifest `frequency: daily`）；同进程内 6 小时缓存窗口复用同一次
  全量下载（`_FULL_CACHE`），不会一次任务内反复全量拉取。

## 解析与 limit 语义

- 表头：`country,date,sector,value,`（第 5 列为空名，实际值恒空串）。
- 日期上游为 `DD/MM/YYYY`（实测首列出现 13~20 日），统一归一化为 `YYYY-MM-DD`。
- 行 schema：`date / country / sector / value(float) / dataset / scraped_at / source_url`，
  与 manifest `columns` 一致。
- **limit 语义**：入口函数每次全量下载后在内存做新→旧排序（按 date 倒序，仅建 (日期, 文件
  偏移) 索引，不建 65 万行 dict），返回 ≤limit 行，即**最新的 limit 行**；全量 64.8 万行
  每次都在下载内容里，limit 只截断返回值。

## 历史深度（实测）

- carbon_global：2019-01-01 → 2026-07-31（滞后约 2 个月），38 国 × 6 部门。
- energy_global：2019-01-01 → 2026-10-01（滞后≈0，数据到昨天），燃料 sector 8 类。

## 真实取数证据（2026-10-02 05:11~05:14 UTC+8，直连）

carbon_global（入口数据集）：

- 全量行数：**647,946** 数据行（≥60 万门槛 ✓；含表头共 647,947 行）
- 表头：`country,date,sector,value,`
- 文件大小：**34,119,717 字节（34.1 MB）**；下载耗时 **153.7 s**；HTTP 200 直连
- CHINA 行：共 **16,614** 行 = 6 部门 × 2,769 天，部门齐全：
  `Domestic Aviation / Ground Transport / Industry / International Aviation / Power / Residential`（≥4 部门门槛 ✓）
- CHINA 样例（最新一天，经 spider 解析管道产出）：
  ```json
  {"date": "2026-07-31", "country": "China", "sector": "Domestic Aviation", "value": 0.289943, "dataset": "carbon_global", "scraped_at": "2026-10-01T21:11:46+00:00", "source_url": "https://datas.carbonmonitor.org/API/downloadFullDataset.php?source=carbon_global"}
  ```
- 序列首值（文件第 2 行）：`"Austria","01/01/2019","Domestic Aviation","0.0000655401",`
  → 归一化 `{"date": "2019-01-01", "country": "Austria", "sector": "Domestic Aviation", "value": 6.55401e-05}`
- `run_carbon_monitor(limit=10)` 实测：返回 10 行，date 均为 2026-07-31（最新日），首行
  Austria/Domestic Aviation/0.000400655，末行 Belgium/International Aviation/0.0198776。

energy_global（第二数据集，source 参数切换）：

- 全量行数：**1,657,384** 数据行；文件大小 **72,280,717 字节（72.3 MB）**；耗时 **167.8 s**；HTTP 200 直连
- 表头同上；样例（文件首数据行）：`"Argentina","01/01/2019","Coal","0.287008",`
- 最新日样例（`run_carbon_monitor(limit=3, source="energy_global")`）：
  ```json
  [{"date": "2026-10-01", "country": "Argentina", "sector": "Gas", "value": 103.382, "dataset": "energy_global", ...},
   {"date": "2026-10-01", "country": "Argentina", "sector": "Hydroelectricity", "value": 148.953, "dataset": "energy_global", ...}]
  ```
- 注意：CSV 无单位列；carbon_global 为上游文档口径 Mt CO2/day，energy_global 的单位上游文件
  未声明，spider 不做换算、不虚构单位。

## 验证方法（本机复现）

```bash
cd /Users/chengsishi/finddata/fd-industry-data && .venv/bin/python - <<'PY'
import importlib.util, time, json
spec = importlib.util.spec_from_file_location("spider", "spiders/carbon-monitor/spider.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
full = m.download_full_csv("carbon_global")          # 全量证据：行数/大小/耗时，不建行 dict
print(full["data_rows"], full["bytes"], full["elapsed_s"], full["header"])
m._FULL_CACHE["carbon_global"] = {**full, "_mono": time.monotonic()}  # 复用本次下载
print(json.dumps(m.run_carbon_monitor(limit=10)[0], ensure_ascii=False))
PY
```

## 遗留与风险

- 服务端限速波动大（同一日上午实测 153s 成功、也有 300s 仅 7.6MB 的时段）；540s 超时下单次
  尝试失败即跳过，等下一个日频窗口，符合"不连环重试"纪律。
- `downloadFullDataset.php` 对非法 source 返回 200+错误页而非 4xx/5xx，已用表头嗅探防护；
  若上游改表头（如加列），解析会整体失败并跳过，需人工复核表头。
- energy_global 体积 72 MB（168 万行），比 carbon_global 更大；若后续把它点亮为独立函数，
  建议单独评估 runner 内存与超时。
