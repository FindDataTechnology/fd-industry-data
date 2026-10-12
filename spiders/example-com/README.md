# example-com

接入档案（unified-source-onboarding）。来源：萬星 wire 需求单 `DE-20261012-99`
（demand-orchestration 生产验证 drill，客户代号 `drill-wa`），greenfield 新建单元。

## 来源

- 站点：`https://example.com/`（IANA 保留的公开示例站，RFC 2606）
- 形态：单页静态 HTML，无鉴权、无 cookie、无 JS 渲染依赖、无分页
- 实测响应（2026-10-12）：HTTP 200，`<title>Example Domain</title>`
- 公开数据断言：`evidence.public_data_asserted = true`（纯公开页面）

## 字段口径（brief.expectations 点名「公开页面标题列表」）

| 列 | 来源 | 口径 |
|---|---|---|
| `title` | 页面 `<title>` | 原样透传（常量标签，如 `Example Domain`） |
| `url` | 页面绝对 URL | 该标题所属页面地址，供下游溯源 |
| `source` | 常量 | 站点标签 `example.com` |
| `scraped_at` | 本地 | 取数时刻 UTC ISO8601 |

入口：`run_example_com(limit=100) -> list[dict]`。

抓取口径（只浅一层）：种子 = `https://example.com/`；只跟随**同 host**
（`example.com` / `www.example.com`）的 `<a href>`，去重后按发现顺序取前 `limit` 条；
外链（如 `https://www.iana.org/domains/example`）不入结果集——本单元只交付示例站
自身的公开页面。当前源站无同站链接，故单次取数 1 行。

## 坑位与容错

- **只依赖标准库**（`urllib` + `html.parser`）：静态 HTML、无 JS 渲染需求，既不引入
  scrapling/httpx 等新依赖（新依赖 = 发版决策），也少一层解析漂移。
- **网络层只做容错、不做对抗**：15s 超时 + 至多 3 次尝试（退避 2s/4s）；不伪装指纹、
  不做频次对抗、不做协议破解。遇签名墙/验证码 → 转人工，不换打法。
- **失败不伪造行**：种子页不可达 / 非 HTML / 无 `<title>` → 抛 `RuntimeError`，由验证链
  判红并转人工。浅层次级页面取数失败只跳过该页（不拖垮整单），同样不产出占位行。
- **不写 `schedule`**：静默合入（点亮 = 人工补写；工单 `brief.cadence` 为 daily）。
- **不写 `site`**：走默认执行位；`site` 是执行位登记，与源站无关。
- `limit <= 0` → 返回空列表。

## 验收

```bash
python3 scripts/health_verify.py --ticket reports/health-tickets/20261012-wire-DE-20261012-99.yaml
```

golden 断言（`golden/001-example-com.json`）只锚**跨期稳定**字段：站点常量标签
`title=Example Domain`、种子 `url` 与常量 `source`；**不锚**时间戳或任何波动数值。
