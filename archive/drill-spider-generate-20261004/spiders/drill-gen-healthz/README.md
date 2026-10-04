# drill-gen-healthz 接入档案（FindData 平台健康检查端点）

- 单元：`spiders/drill-gen-healthz/`，入口 `run_drill_gen_healthz(limit: int = 100) -> list[dict]`
- 网络：平台自有端点，**直连可用，无反爬、无鉴权、无代理**【实测 2026-10-04 直连 HTTP 200 + JSON】
- 语种/编码：JSON（UTF-8）
- 出单：`reports/health-tickets/20261004-drill-gen-healthz-e1e98e04.yaml`（`kind=generate`，生成流演练夹具 D6）

## 端点

```
GET https://platform.finddatatech.cloud/healthz
```

- 全量型端点：无分页、无查询参数、无鉴权（无 cookie/token）。
- 实测响应（2026-10-04，`ok=true, uptimeMs=9066630, cells=2`）：

```json
{"ok": true, "uptimeMs": 9066630, "cells": 2}
```

## 字段口径（行 = 1）

| 字段 | 类型 | 口径 |
|---|---|---|
| `ok` | bool | `/healthz` 报告的平台健康标志；**唯一 golden 断言锚点**（`ok == true`） |
| `cells` | int | 平台报告的 cell 数；波动值，仅透出、**不断言** |
| `uptime_ms` | int | 进程 uptime（ms），映射自上游 `uptimeMs`；波动值，仅透出、**不断言** |
| `url` | str | 恒为 `https://platform.finddatatech.cloud/healthz`（常量，可断言） |
| `source` | str | 恒为 `drill-gen-healthz`（常量，可断言） |
| `scraped_at` | str | 取数时刻 UTC ISO8601；`whitelist_fields` 内 |

## 坑位与容错处理

1. **波动值纪律**：`uptimeMs` 逐秒变化、`cells` 随部署变化——样本只锚 `ok == true` 与两个常量
   字段（`url` / `source`），绝不锚数值或日期（跨期稳定）。
2. **`limit` 语义**：上游只返回单行，`limit` 仅做行数截断；`limit` 缺失或非正时返回该单行，
   不静默返回空列表（避免验证链"空通过"）。
3. **失败即红**：非 200、非 JSON、缺 `ok` 字段三种情况一律抛异常（`RuntimeError` / `ValueError`），
   让 `health_verify` 的 golden 重放与真取数环判红，不做静默降级。
4. **依赖纪律**：只用标准库 `urllib`（镜像自带），不引入新依赖；仅带常规 `User-Agent`，
   不做指纹伪装、不加签名头。
5. **不写 schedule / 不写 site**：静默合入，点亮由人工决定；本站点是平台自身端点，
   沿用默认执行位（tencent）。

## 验证链

```
python3 scripts/health_verify.py --ticket reports/health-tickets/20261004-drill-gen-healthz-e1e98e04.yaml
```

结果：`verdict: ok`（golden 重放 1/1 通过 + 真取数 ≥1 行 + manifest 校验 0 违规 + conformance gate PASS），
详见 PR 描述。