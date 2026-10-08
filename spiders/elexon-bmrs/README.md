# elexon-bmrs —— 英国 BMRS 日度有效温度（Elexon Insights API）

批次二 Wave C 直建单元。**需代理出口**（manifest `egress_secret: fd-industry-egress-fx01`）。

## 1. 数据表面

| 项 | 值 |
|---|---|
| 平台 | Elexon（英国电力调度 BMRS）Insights API |
| 数据集 | **TEMP**（GB effective temperature，需求预测天气锚） |
| 端点 | `GET https://data.elexon.co.uk/bmrs/api/v1/datasets/TEMP?format=json` |
| 鉴权 | **免 key、免鉴权** |
| 频度 | 日频（每日一行，`publishTime` 当日 15:45Z 前后发布） |
| 历史深度 | **31 天滚动窗**（2026-10-08 实测 2026-09-07→10-07 连续 31 行；更早不可回补） |

行样例（实测）：

```json
{"dataset": "TEMP", "measurementDate": "2026-10-07", "publishTime": "2026-10-07T15:45:00Z",
 "temperature": 13.6,
 "temperatureReferenceAverage": 12.9, "temperatureReferenceHigh": 14.9, "temperatureReferenceLow": 10.9}
```

输出列：`period`（ISO 日期）+ `temperature` + 可选参考基线三列（存在才输出）
+ `url` / `source` / `scraped_at`。

## 2. 信号选择理由与放弃的兄弟端点（侦察证据）

Elexon BMRS 免 key 端点里，**TEMP 是唯一日频且有 31 天可回看历史的稳定序列**：

| 候选端点 | 实测（2026-10-08，经 fx01） | 判定 |
|---|---|---|
| **`datasets/TEMP`** | 200，31 天日度连续，值列齐全 | ✅ **选中** |
| `generation/outturn`（FUELINST 聚合视图） | 200 但只有 demand 列、无 fuelType；`datasets/FUELINST` 有 20 燃料类型 × 116 时段，但 **`settlementDate`/`publishDateTime` 过滤参数被忽略，恒返最新一天快照**——无历史，日跑会全量重写 | ❌ 快照型 |
| `datasets/B1610`（30 分钟实发结算） | 必填 `settlementDate`+`settlementPeriod`，但**所有日期/时段组合恒返 0 行**（含当天与近三天） | ❌ 恒空 |
| `system/frequency` | 200（15 秒粒度实时频率） | ❌ 实时流，非周期统计信号，重写量大无回补 |
| `datasets/WINDFOR` | 200 但行内无 settlementDate（startTime 快照） | ❌ 快照型 |
| `generation/actual/total`、`demand/actual`、`system/demand` 等 | 404 | ❌ 路径不存在 |

TEMP 的价值：英国电网负荷-温度强相关，TEMP 是 Elexon 官方需求预测的天气输入，
与能源线（eia-v2/ember）同域互补。

## 3. 代理要求（关键）

- data.elexon.co.uk 从 tencent 站点**直连不可达**（Azure WAF 403）；本机旧代理
  出口 38.76.150.185 同样被封。只有 fx01 出口（38.76.150.159）实测可达。
- 本单元**不硬编码代理地址**：标准库 `urllib` 默认尊重 `HTTPS_PROXY`/`HTTP_PROXY`。
- 运行侧出口由 manifest 的 `egress_secret: fd-industry-egress-fx01` 声明，
  经 AppSet 参数 `egressSecret` → CronJob 模板 `envFrom` 注入。

## 4. 口坑

- **31 天滚动窗**：窗口滚动，`limit` 缺省 100 已足够全窗；缺深历史，长基线分析需
  自积累。
- **参考基线列可选**：非每行都有，golden 将三列列入 whitelist（存在性不锚）。
- **数值类 golden 不锚当期值**：温度随天气滚动，golden 只锚结构常量。

## 5. 容错与失败语义

- 端点请求异常 → `RuntimeError`；响应缺 `data` 数组 → `RuntimeError`；
- 行缺 `measurementDate` 或 `temperature` → 跳过该行；零可解析行 → `RuntimeError`；
- 不重试、不做频次对抗，遇风控升级按协议转人工，不逆向。

## 6. 复现验证

```bash
export HTTPS_PROXY='http://fx01:lwxsibec@100.64.0.7:30081'
curl -s "https://data.elexon.co.uk/bmrs/api/v1/datasets/TEMP?format=json" | head -c 400
```

期望：`{"metadata": {"datasets": ["TEMP"]}, "data": [...]}`，data 为 31 行日度序列。
