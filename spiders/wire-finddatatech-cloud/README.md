# wire-finddatatech-cloud

接入档案（unified-source-onboarding）。来源：萬星 wire 需求单 `DE-20261011-99`
（demand-orchestration 演练单，客户代号 `drill-wi`），greenfield 新建单元。

## 来源

- 端点：`https://wire.finddatatech.cloud/health`（Caddy 前置，`fd-wire-api` 服务）
- 形态：单个 JSON 对象，无分页、无鉴权、无 cookie 依赖
- 实测响应（2026-10-11）：`{"status":"ok","service":"fd-wire-api","version":"0.1.0"}`

## 字段口径（brief.expectations 点名 status/service/version）

| 列 | 来源 | 口径 |
|---|---|---|
| `service` | 端点 `service` | 原样透传（常量标签，如 `fd-wire-api`） |
| `status` | 端点 `status` | 原样透传，不做健康判定映射 |
| `version` | 端点 `version` | 原样透传，随服务发版变化 |
| `url` | 常量 | 读取的端点地址，供下游溯源 |
| `scraped_at` | 本地 | 取数时刻 UTC ISO8601 |

入口：`run_wire_finddatatech_cloud(limit=100) -> list[dict]`，单次取数 1 行。

## 坑位与容错

- **只依赖标准库**：端点返回纯 JSON，用 `urllib` 直取；不引入 scrapling/httpx，
  避免新依赖（=发版决策）与解析层漂移。
- **网络层只做容错、不做对抗**：15s 超时 + 至多 3 次尝试（退避 2s/4s）；不伪装指纹、
  不做频次对抗。
- **失败不伪造行**：端点不可达或返回非 JSON 对象 → 抛 `RuntimeError`，由验证链判红。
  本单是演练单，`drill` 侧可随时关闭端点，关闭时必须显式失败而不是吐假数据。
- **不写 `schedule`**：静默合入（点亮由人工补写，工单 `brief.cadence` 为 daily）。
- **不写 `site`**：走默认执行位；`site` 是执行位登记，与源站无关。
- `limit <= 0` → 返回空列表；`limit >= 1` → 返回该 1 行。

## 验收

```bash
python3 scripts/health_verify.py --ticket reports/health-tickets/20261011-wire-DE-20261011-99.yaml
```

golden 断言（`golden/001-wire-health.json`）只锚**跨期稳定**字段：`service` 常量标签与
端点 `url`；**不锚** `status`/`version`/时间戳（健康状态与发版号会漂）。
