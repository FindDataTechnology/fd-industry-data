# agri-casde —— CASDE 农产品供需形势分析月度平衡表

新源接入档案（`templates/new-source/` 范式）。
数据源：中国农业农村信息网「数据 > 供需形势」
`https://www.agri.cn/sj/gxxs/`（实测 2026-11-08，国内直连，禁代理）。

**通道反直觉**：CASDE 供需平衡表**不在** `moa.gov.cn` 域内，在 `agri.cn`。按
`moa.gov.cn` 站内检索会一无所获。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 列表 | `GET https://www.agri.cn/sj/gxxs/` | 裸目录即第 1 页；分页 `index_N.htm`，10 条/页 |
| 正文 | `GET https://www.agri.cn/sj/gxxs/<yyyymm>/t<yyyymmdd>_<id>.htm` | 裸 GET 即 200，无 UA/Cookie 要求；正文含 5 张 HTML `<table>` |

**没有固定总页数**：模板写 19 页是假的，`index_19.htm` 实测 404（本次实测
`index_14.htm` 起即 404）。翻页必须以 404 终止。

每期 5 张表：玉米 / 大豆 / 棉花 / 食用植物油 / 食糖，每表 18–24 行。期次主键
是正文里的 `CASDE-No.<n>`（如 No.124 = 2026-10，期号与月份成等差数列）。

入口：`run_agri_casde(limit=50, window_start=None, window_end=None) -> list[dict]`
（`spider.py`）。仅标准库 `urllib` + `html.parser` + `re` + `json`，无新依赖、不 OCR。

## 鉴权与参数

- 无 key / 无验证码 / 无 cookie；正文页裸 GET 即可。
- **禁代理**：opener 显式挂空 `ProxyHandler({})`，不吃代理环境变量。
- `limit` 计**行数**（不是期数）；确定性顺序 = 列表新→旧、表内行序、列序。
- `window_start`/`window_end`（YYYY-MM 或 YYYY-MM-DD）为平台首爬回补窗口，
  **按期次月过滤、先过滤后截断**；非法/不成对 → `ValueError`。

## 字段口径

| 列 | 口径 |
|---|---|
| `casde_no` | 期号整数（如 124），**期次主键** |
| `period` | 期次月 `YYYY-MM`（No.124 = `2026-10`）；取自列表标题，缺标题月份时按期号-月份等差回推 |
| `commodity` | `corn` / `soybean` / `cotton` / `edible_oil` / `sugar` |
| `item` | 行标签原文（播种面积 / 产量 / 进口量 / 消费量 / 期末库存 / 价格区间 …） |
| `value` | float；**解析失败跳过并计数，绝不写 0** |
| `unit` | 继承上一条单位行：千公顷 / 万吨 / 元/吨 / 公斤/公顷 / 吨/公顷 / 美分/磅 |
| `forecast_kind` | `actual`（历史年度列）/ `current`（列头月份=本期月）/ `previous`（其他预测月） |
| `season` | 列头原文，如 `2026/27` |
| `forecast_month` | 从列头解析出的预测月 `YYYY-MM`；actual 列为 null |
| `basis` | 列头里的 `forecast` / `estimate` 字样；两者都没有则 null |

## 四处坑（逐条已实现）

1. **列表混流**：列表里混着大蒜 / 罗非鱼 / 对虾专报，大蒜还有大众版与详细版两个
   版本。只保留标题含 `CASDE-No.<n>` 且含「农产品供需形势分析」的条目——专报不带
   期号，靠期号过滤最稳。
2. **单位独立成行**：单位在自己的行上切换（`千公顷（1000 hectares）` / `万吨`
   / `元/吨` / `公斤/公顷` / `吨/公顷` / `美分/磅`），切换后续所有数据行。
   **单位行绝不能当数据行**；单位未知的数据行跳过并计数。
3. **多列预测且列名带月份**：表头形如 `2026/27 （9月预测）` / `2026/27 （10月预测）`。
   列名不可硬编码，按列名解析出预测月，与本期月比较判定 current/previous。
   5 月这类期次的表头是「4月估计 / 5月估计 / 5月预测」——**current/previous 由月份
   判定，不绑定「预测」二字**。
4. **价格区间**：单元格可为 `2300-2600` / `14.0-18.5`，拆成 `_low` / `_high` 两行，
   不取中点、不填 0。

## 历史深度（实测，不假装有长序列）

- HTML 表格形态从 **`CASDE-No.95`（2024-05）** 起；本次实跑覆盖 No.119–124
  （2026-05 … 2026-10）。
- 更早期次（No.27 = 2018-09 起）正文只有叙述文字 + PDF 附件、**无 HTML 表**：本单元
  不解析 PDF、不 OCR，这类期次跳过并计数。
- 月更，每月 9—12 日发布。

## 真实样例（2026-11-08 实跑）

```json
{"casde_no": 124, "period": "2026-10", "commodity": "corn", "item": "播种面积",
 "value": 44741.0, "unit": "千公顷", "forecast_kind": "actual", "season": "2024/25",
 "forecast_month": null, "basis": null,
 "source_url": "https://www.agri.cn/sj/gxxs/202610/t20261009_8875475.htm"}
```

2026-05（No.119）锚点：棉花期末库存 2024/25 = 788 万吨、Cotlook A 指数 = 79 美分/磅、
播种面积 2838 千公顷。

## 自检

```bash
cd fd-industry-data
.venv/bin/python scripts/validate_manifests.py
.venv/bin/python scripts/conformance_gate.py
.venv/bin/python -m py_compile spiders/agri-casde/spider.py
```

golden `001-agri-casde.json` 锚 **No.119 历史期**而非最新期：最新期会随下期修订，
锚历史期避免换月即假故障（先例：批次四 nbs-housing）。
