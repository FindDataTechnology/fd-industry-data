# dbnomics-api — DBnomics 聚合 API 接入档案

DBnomics（db.nomics.world）聚合各国官方统计机构（ECB/IMF/OECD/Eurostat 等）的时间序列，
提供统一 REST API。本单元按序列取全量观测值，行粒度为「序列 × 观测日」。

- 入口函数：`run_dbnomics_api(limit: int = 100, default_sequences: list | None = None) -> list[dict]`
  （fd-runner 调 `run_dbnomics_api`；hyphen→underscore）
- manifest：`spiders/dbnomics-api/manifest.yaml`（command=`run_dbnomics_api`，frequency=monthly 随上游）

## 端点

```
GET https://api.db.nomics.world/v22/series/{provider}/{dataset}/{code}?observations=1
```

- 返回 JSON：顶层 `series.docs[]`，每条 doc 内联平行数组
  `period`（YYYY-MM-DD 起始日列表）与 `value`（对齐的数值列表）。
- 仅发实测有效参数：`observations=1`。其余（`dimensions`、`facets` 等）一律不发。
- 多序列合并接口 `GET /v22/series/{provider}/{dataset}?series_ids=...` 存在，但逐条取
  语义更简单、失败隔离更好，定案为**逐序列单请求**（每序列一个 URL，独立成败）。
- 历史深度：随上游。ECB EXR 日频参考汇率自 **2000-01-13** 起全量返回（单请求 ~270KB）。

## 鉴权

免 key、免 token，直连可用。仅设 `Accept: application/json` 与自定义 UA。

## provider 白名单（硬约束）

代码内 `PROVIDER_WHITELIST = {ECB, IMF, OECD, ESTAT}`（IMF/OECD/ECB 必收，Eurostat=ESTAT 可选）。
manifest 参数 `default_sequences`（`"provider/dataset/code"` 字符串数组，默认
`["ECB/EXR/D.CNY.EUR.SP00.A"]`，即 ECB 日频人民币/欧元即期均价）可传参扩展序列；
**白名单外的 provider 在发请求前即被 `ValueError` 拒绝**（实测：传 `SAFE/...` 被拒，未发起任何 HTTP）。

### ⚠️ SAFE（国家外管局）降级警示

DBnomics 镜像了 SAFE 序列，但该镜像**冻结在 2020-12，仅 12 个观测值，此后不再更新**。
SAFE **不得作为持续源接入**（也不在白名单内）；如需 SAFE 数据只能离线一次性导入，
不能走本单元的常态抓取。（2026-10-02 复核：`SAFE/ORA` 逐序列仍为 12 个月度观测，
2020-01→2020-12，无更新。）

### 后续扩展点（未实现）

- **BACI_HS17**（CEPII 国际贸易流，约 1781 万序列）：适合按 HS6 码切片批量接入，
  本期不实现，仅记录为候选扩展。

## 容错规则

- 直连失败仅在**连接层**（超时/拒绝）触发代理兜底：先探测 `127.0.0.1:7890` 端口，
  通了经 `FetcherSession(proxy=...)` 重试一次；代理也失败则记日志跳过。
- **HTTP 5xx/空响应 → 记录后跳过，不重试、不绕反爬**（服务端已应答即不复议）。
- 网络模式粘性（`NET_MODE`）：一次会话内定住 direct 或 proxy，不反复切换。
- 解析纪律：`value` 为上游缺测标记 `"NA"`（假日）时跳过该格，**不写脏行、不补造数据**；
  `period`/`value` 数组长度不一致时整序列跳过。

## 网络模式

海外源，**直连优先**。实测直连 HTTP 200（约 2.5s/请求），未启用代理。
代理端口 7890 本机虽开，但本单元**无需走代理**。

**【直连=2/代理=0】**（两次验证运行均直连成功；sticky 模式=direct）

## 真实取数证据

- 取数时间：**2026-10-01T20:28:50+00:00**（scraped_at，UTC；本地 2026-10-02 04:28 CST）
- 序列：`ECB/EXR/D.CNY.EUR.SP00.A` — Daily – Chinese yuan renminbi – Euro – Spot – Average
- 上游观测总数：**6898**；解析入行：**6837**（61 个假日 `"NA"` 格按规则跳过）
- 首值：**2000-01-13 = 8.5054**；末值：**2026-09-30 = 7.613**（距取数日 2 天，满足
  「末值日期在近 5 日内」的验证门槛）
- 样例行（1 条，JSON）：

```json
{"provider": "ECB", "dataset": "EXR", "series_code": "D.CNY.EUR.SP00.A", "series_name": "Daily – Chinese yuan renminbi – Euro – Spot – Average", "period": "2026-09-30", "value": 7.613, "scraped_at": "2026-10-01T20:28:50+00:00", "source_url": "https://api.db.nomics.world/v22/series/ECB/EXR/D.CNY.EUR.SP00.A?observations=1"}
```

- 验证方式：`.venv/bin/python` 经 importlib 加载 `spiders/dbnomics-api/spider.py`，
  实际执行 `run_dbnomics_api(...)`；`validate_manifests.py` 63 manifests 0 violations；
  conformance gate PASS（65 units, 0 violations）。

## 遗留说明

- `default_sequences` 传参扩展即可接入 IMF/OECD/ESTAT 序列，但本期仅实测默认 ECB 一条；
  其他 provider 序列码未逐条侦察，接入前需先人工验证序列码有效。
- manifest frequency=monthly 系按 brief「随上游」口径登记；默认序列本身为日频。
