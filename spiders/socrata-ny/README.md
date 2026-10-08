# socrata-ny —— 纽约州交通燃料现货价（Socrata SODA API）

批次二 Wave C 直建单元。**需代理出口**（manifest `egress_secret: fd-industry-egress-fx01`）。

## 1. 数据表面

| 项 | 值 |
|---|---|
| 平台 | 纽约州开放数据平台 data.ny.gov（Socrata） |
| 数据集 | **Transportation Fuels Spot Prices: Beginning January 2011**，ID `k7gz-mn77` |
| 端点 | `GET https://data.ny.gov/resource/k7gz-mn77.json?$limit=N&$order=date DESC` |
| 鉴权 | **免 key、免鉴权**（匿名请求即可，未注册 app token） |
| 频度 | 周频（`date` 逐周递增，最近更新 2026-10-06） |
| 历史深度 | 2011-01 起（平台标注 Beginning January 2011） |

返回行样例（2026-10-08 实测，经 fx01 出口）：

```json
{"date": "2026-09-25T00:00:00.000",
 "ny_conventional_gasoline_spot_price_gal": "3.58",
 "ny_ultra_low_sulfur_diesel_spot_price_gal": "4.96",
 "wti_crude_oil_spot_price_barrel": "93.57",
 "brent_crude_oil_spot_price_barrel": "117.08"}
```

输出列：`period`（ISO 日期）+ `ny_gasoline_spot_usd_gal` + `ny_diesel_spot_usd_gal`
+ `wti_crude_spot_usd_bbl` + `brent_crude_spot_usd_bbl` + `url` / `source` / `scraped_at`。

## 2. 数据集选择理由

目录 API（`https://data.ny.gov/api/catalog/v1?q=...`）检索后比对候选：

| 候选 | 判定 |
|---|---|
| **`k7gz-mn77` 交通燃料现货价** | ✅ **选中**：周频、含成品油与原油两类价格、2011 起连续、表格型 dataset、字段稳定 |
| `crem-w557` Monthly Transportation Statistics | ❌ 该 ID 在 data.ny.gov 上 `/resource/` 返 404（目录项与资源 ID 不对应） |
| `aj9i-xni8` Taxi Medallion 月均价 | ❌ 类型为 chart（图表派生视图），非原始表格 |
| `ebb7-mvp5` DSNY Monthly Tonnage | ⭕ 月频垃圾清运量，亦可；本批优先能源价格（口径与既有能源线相接） |

**注意**：Socrata 目录 API 是**联邦检索**（不指定 `domains` 时会跨站返回 NY/Chicago 等
全部域的结果），选数据集时必须回本域用 `/resource/<id>.json` 实弹验证一次再定。

## 3. 代理要求（关键）

- data.ny.gov 从 tencent 站点**直连不可达**（Socrata 边缘对直连来源返回 403）；
  本机旧代理出口 38.76.150.185 被 Socrata 全域封禁。
- 本单元**不硬编码代理地址**：标准库 `urllib` 默认尊重环境变量
  `HTTPS_PROXY`/`HTTP_PROXY`，由运行环境注入。
- 运行侧出口由 manifest 的 `egress_secret: fd-industry-egress-fx01` 声明，
  经 AppSet 参数 `egressSecret` → CronJob 模板 `envFrom` 注入（集群 secret
  `fd-industry-egress-fx01` = mihomo fx01，出口 38.76.150.159）。
- 本地复测命令（Mac）：

  ```bash
  export HTTPS_PROXY='http://fx01:lwxsibec@100.64.0.7:30081' HTTP_PROXY="$HTTPS_PROXY"
  python3 -c "import importlib.util; ... run_socrata_ny(limit=5)"
  ```

## 4. 口坑

- **URL 含 `$` 参数必须 urlencode**：`$limit` / `$order` 手工拼接一旦带空格会触发
  `InvalidURL: URL can't contain control characters`——本单元统一走
  `urllib.parse.urlencode`。
- **数值列以字符串返回**：需逐字段 `float()`；空串/缺失记 `None` 并跳过该列（不丢整行）。
- 默认序已是最新，但本单元仍显式声明 `$order=date DESC`，不依赖上游默认序。
- **数值类 golden 不锚当期值**：价格随行情滚动，golden 只锚结构常量
  （`source` 字段 + `period` 正则 + `min_rows`），绝不锚具体价格。

## 5. 容错与失败语义

- 端点请求异常 → `RuntimeError`（失败即红）；
- 响应非 JSON 数组（上游形态变更）→ `RuntimeError`；
- 全部行缺 `date`（不可解析）→ `RuntimeError`（零可解析行）；
- 单列缺失/非数值 → 该列 `None`，行保留；
- 不做重试与频次对抗，遇风控升级按协议转人工，不逆向。

## 6. 复现验证

```bash
export HTTPS_PROXY='http://fx01:lwxsibec@100.64.0.7:30081'
curl -s "https://data.ny.gov/resource/k7gz-mn77.json?%24limit=2&%24order=date+DESC" | head -c 400
```

期望：JSON 数组，含上述 5 个字段。
