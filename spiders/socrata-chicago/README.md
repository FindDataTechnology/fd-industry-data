# socrata-chicago —— 芝加哥 CTA 公交月度客流（Socrata SODA 聚合）

批次二 Wave C 直建单元。**需代理出口**（manifest `egress_secret: fd-industry-egress-fx01`）。

## 1. 数据表面

| 项 | 值 |
|---|---|
| 平台 | 芝加哥开放数据平台 data.cityofchicago.org（Socrata） |
| 数据集 | **CTA - Ridership - Bus Routes - Monthly Day-Type Averages**，ID `bynn-gwxy` |
| 端点 | `GET /resource/bynn-gwxy.json?$select=month_beginning,sum(monthtotal)&$group=month_beginning&$order=month_beginning DESC&$limit=N` |
| 鉴权 | **免 key、免鉴权**（匿名请求即可） |
| 频度 | 月频（`month_beginning` 逐月，最近更新 2026-09-28） |
| 历史深度 | 2001-01 起 |

输出口径为**全网（全部线路）月度乘次合计**，由 SoQL 聚合在服务端完成。

聚合响应样例（2026-10-08 实测）：

```json
[{"month_beginning": "2026-07-01T00:00:00.000", "sum_monthtotal": "17337209"},
 {"month_beginning": "2026-06-01T00:00:00.000", "sum_monthtotal": "17278266"}]
```

原始粒度（未聚合）为「线路 × 月」：`route` / `routename` / `month_beginning` /
`avg_weekday_rides` / `avg_saturday_rides` / `avg_sunday_holiday_rides` / `monthtotal`。

输出列：`period`（ISO yyyy-mm）+ `bus_rides_total` + `url` / `source` / `scraped_at`。

## 2. 数据集选择理由

本域目录 API（`?domains=data.cityofchicago.org&q=monthly`）检索后比对：

| 候选 | 判定 |
|---|---|
| **`bynn-gwxy` CTA 公交月度客流** | ✅ **选中**：2001 起连续、月频、服务端可聚合为全网总量、最近更新 2026-09-28 |
| `t2rn-p8d7` CTA L 车站月度进站量 | ⭕ 同构备选（按 `station_id` 分组亦可聚合） |
| `u9mu-2sy8` Taxi Trips by Month | ⭕ 可用（2024- 起，历史较短） |
| `esuh-pijk` TNP 网约车司机月度数 | ❌ `/resource/` 返回空对象 `{}`，字段不可解析 |
| `aj9i-xni8` Taxi Medallion 月均价 | ❌ chart 型派生视图，非原始表格 |

**注意**：Socrata 目录 API 是**联邦检索**（不指定 `domains` 会跨站返回 NY/Chicago 等
全部域结果——实测 q=monthly 时 NY 与 Chicago 返回完全相同的列表），选数据集必须
带 `domains` 限定并回本域 `/resource/<id>.json` 实弹验证。

## 3. 代理要求（关键）

- data.cityofchicago.org 从 tencent 站点**直连不可达**（Socrata 边缘对直连来源 403）；
  本机旧代理出口 38.76.150.185 被 Socrata 全域封禁。
- 本单元**不硬编码代理地址**：标准库 `urllib` 默认尊重 `HTTPS_PROXY`/`HTTP_PROXY`。
- 运行侧出口由 manifest 的 `egress_secret: fd-industry-egress-fx01` 声明，
  经 AppSet 参数 `egressSecret` → CronJob 模板 `envFrom` 注入（集群 secret
  `fd-industry-egress-fx01` = mihomo fx01，出口 38.76.150.159）。
- 本地复测（Mac）：

  ```bash
  export HTTPS_PROXY='http://fx01:lwxsibec@100.64.0.7:30081' HTTP_PROXY="$HTTPS_PROXY"
  python3 -c "import importlib.util; ... run_socrata_chicago(limit=6)"
  ```

## 4. 口坑

- **URL 含 `$` 参数必须 urlencode**：`$select`/`$group`/`$order`/`$limit` 手工拼接带空格
  会触发 `InvalidURL: URL can't contain control characters`——本单元走
  `urllib.parse.urlencode`。
- **聚合别名 `sum_monthtotal`**：Socrata 给 `sum(monthtotal)` 的自动别名；若上游改名
  或换聚合函数，此处需同步（失败时单元抛 RuntimeError 并提示别名）。
- **数值列以字符串返回**：需 `int(float(...))` 转换。
- **计数类 golden 不锚当期值**：客流逐月更新、历史期次会被上游修订，golden 只锚
  结构常量（`source` + `period` 正则 + `min_rows`）。

## 5. 容错与失败语义

- 端点请求异常 → `RuntimeError`；响应非 JSON 数组 → `RuntimeError`；
- 聚合行缺 `month_beginning` 或缺 `sum_monthtotal` → 跳过该行；零可解析行 → `RuntimeError`；
- 不重试、不做频次对抗，遇风控升级按协议转人工，不逆向。

## 6. 复现验证

```bash
export HTTPS_PROXY='http://fx01:lwxsibec@100.64.0.7:30081'
curl -sG "https://data.cityofchicago.org/resource/bynn-gwxy.json" \
  --data-urlencode '$select=month_beginning,sum(monthtotal)' \
  --data-urlencode '$group=month_beginning' \
  --data-urlencode '$order=month_beginning DESC' \
  --data-urlencode '$limit=3'
```

期望：JSON 数组，含 `month_beginning` 与 `sum_monthtotal`。
