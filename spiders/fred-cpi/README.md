# fred-cpi — 美国 CPI「在外就餐 / 在家就餐」

> 餐饮垂直线波1（scope addition，可回滚）· `kind=generate` 工单：
> `reports/health-tickets/20261107-fred-cpi-ba279d6a.yaml`

## 为什么有这条源

餐饮研究的核心对照对是 **CPI 在外就餐 vs 在家就餐**（剪刀差 = 餐饮定价与家庭食品成本的背离）。
这对概念早已在册（`price.cpi.us.food_away_from_home` / `price.cpi.us.food_at_home`），唯一来源是
`dbnomics-api`（BLS 镜像）——**观测冻结在 2025-01**，因为 `api.bls.gov` / `download.bls.gov`
对我们每个出口都是 Akamai 403。FRED 的 CSV 端点走同一条 BLS 序列、可达，于是本单元把这对概念
续到了 2026-09。

## 数据面

| 项 | 值 |
|---|---|
| 端点 | `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES_ID>` |
| 序列 | `CUSR0000SEFV`（Food Away From Home, SA）、`CUSR0000SAF11`（Food At Home, SA） |
| 口径 | BLS CPI-U，季调（前缀 `S`），指数 1982-84=100；`CUUR…`（NSA）**不得混入** |
| 粒度 | 月度，FRED 日期为当月 1 日 |
| 鉴权 | 无 |
| 实测窗口 | 1953-01 → 2026-09（away，884 期）/ 1952-01 → 2026-09（home，896 期）；一次 run 1,780 行 |

### 序列号定谳

工单预告的 `CUSR0000SEFV02` / `CUSR0000SEFV11` **不存在**（HTTP 404，站内同名页也 404）。
正确的是去掉两位后缀的 `CUSR0000SEFV` / `CUSR0000SAF11`，页面明确标注 "Seasonally Adjusted"。

### 出口

| 出口 | 结果 |
|---|---|
| 本机直连 | ❌ `TimeoutError`（30s read timeout，两序列都超） |
| fx 出口 `fdproxy@100.64.0.4:30080`（`curl_cffi`） | ✅ 2.2s / 200 |
| fx 出口同址（`urllib`） | ❌ 61s 超时——**标准库不给 CONNECT 隧道补 `Proxy-Authorization`** |

所以：**直连用 stdlib，代理腿用 `curl_cffi`**；配了 `HTTPS_PROXY`（生产恒由
`egress_secret: fd-industry-egress-fx01` 注入）就先走代理腿，失败再退直连一次。

## 缺测纪律

FRED 的缺测有两种形态，都必须**跳过而不是补零**：

1. `.`（文档里的缺测占位）；
2. **空串** —— 真实存在的一期：`2025-10-01,`（BLS 当月未发布），该月必须缺席。

因此 `price.cpi.us.food_*` 的 fred-cpi 侧在 **2025-10 有一个真缺期**，下游做环比必须当缺失处理。

## 与 dbnomics 的关系（重要）

| 项 | 说明 |
|---|---|
| 概念 | **同一对概念**，不新建（本单元 `CONCEPTS` 为空，harness 复用既有 code） |
| 观测 | 并存：同概念、不同 `source_used`，自然键含 `source_used` 故互不覆盖 |
| 重叠月 | 1952-01..2025-01 两个来源各有真实值 → 这是跨源核对面，不是重复计数 |
| 板面读法 | **一条序列只取单一 `source_used`**。推荐：`fred-cpi`（更新到 2026-09）；dbnomics 作历史核对 |

## 用法

```bash
# 全量回补（默认 limit=100 会截断历史）
FD_CRAWL_DB_URL="$FD_OPEN_DATA_DSN" FD_WRITER_MAX_ITEMS=50000 \
HTTPS_PROXY="http://fdproxy:***@100.64.0.4:30080" \
  ./.venv/bin/python -m fd_industry_data.runner_cli fred-cpi --limit 3000

# 换序列（NSA 是另一口径，需另立概念，不要塞进这对）
... --params '{"series":["CUUR0000SEFV","CUUR0000SAF11"]}'
```

**无 `schedule`**：本单元静默合入，点亮走人工门。

## 验证记录（2026-11-07）

- `fd-runner fred-cpi -> success rows=1780 in 7.1s`（run 4802）
- 抽取：1,780 行入库，`source_used=fred-cpi`，`1952-01..2026-09`；重跑 inserted=0
- 概念：新增 0（复用在册两条，无需再过命名门）
