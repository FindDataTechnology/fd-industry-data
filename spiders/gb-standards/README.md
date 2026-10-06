# gb-standards —— 全国标准信息公共服务平台（国标 GB）检索

新源接入档案（`templates/new-source/`）。工单：
`reports/health-tickets/20261006-gb-standards-ca4f8b1f.yaml`（`kind=generate`，
brief 侦察簿 W1-F）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 检索 | `GET https://std.samr.gov.cn/gb/search/gbQueryPage?searchText=<关键词>&pageNumber=<页>&pageSize=20` | 免鉴权纯 JSON：`{"total","pageNumber","rows":[...]}`，默认按发布日期新→旧 |

入口：`run_gb_standards(limit=100, search_text="") -> list[dict]`（`spider.py`）。
仅用标准库 `urllib`，无新依赖。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `std_code` | `C_STD_CODE` | 标准号（如 `GB/T 1.1-2020`），剥高亮标签后透出 |
| `title` | `C_C_NAME` | 标准中文名称 |
| `std_nature` | `STD_NATURE` | 强制性/推荐性 |
| `issue_date` | `ISSUE_DATE` | 发布日期 `YYYY-MM-DD`（**月度新发布统计的派生轴**） |
| `act_date` | `ACT_DATE` | 实施日期 |
| `state` | `STATE` | 状态：现行/即将实施/废止（**废止统计的派生口径**） |
| `record_id` | `id` | 上游记录 id，可追溯 |
| `url` | 请求 URL | 行级来源可追溯 |
| `source` | 常量 | `gb-standards` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

「国标监管强度月度序列」（新发布/废止数量）由明细行的 `issue_date`/`state`
派生聚合，本单元交付明细行（工单 expectations 的行字段口径）。

## 坑位与容错（brief.notes / 实测踩坑逐条落实）

- **浏览器 UA**：notes 点名必须带；实测无 UA 也回包，但按纪律照带常规浏览器
  UA + `Referer`，不做指纹伪装、不做频次对抗——遇风控升级按协议转人工，不逆向。
- **高亮标签**：`searchText` 非空时命中字段包 `<sacinfo>…</sacinfo>`（实测），
  一律剥离后再透出。
- **只发实测有效参数**：`searchText`（空=全库 79,816 条）、`pageNumber`（实测 1/2）、
  `pageSize`（实测 1/5/20，封顶 20）。未实测的参数一概不发。
- **节奏**：政府站点，翻页串行低频（请求间 sleep 0.8s）、不并发、不重试放大。
- **`limit` 语义**：明细行数上限，不足时按 `pageNumber` 翻页补齐；
  `limit=5 + search_text="GB/T 1.1-2020"` 时结果恒为该固定标准 → golden 重放确定。
- **失败即红**：单页失败只跳过并留痕；全部页失败抛 `RuntimeError`，不静默返回空列表。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
python3 spiders/gb-standards/spider.py          # 冒烟，打印 3 行最新发布
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-gb-standards-ca4f8b1f.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-gb-standards.json` 锚**固定标准号查询**（brief 指定策略）：
`params = {limit: 5, search_text: "GB/T 1.1-2020"}`，`rows_contains` 只锚
`std_code == GB/T 1.1-2020`（跨期稳定的标准号）与 `source == gb-standards`，
以及 `min_rows: 1`。
**绝不锚** 最新发布的当期明细（默认检索路径首行随时间滚动）、日期与状态之外的
波动值；`scraped_at`/`timestamp` 进 `whitelist_fields`。

## 实测样例行（2026-10-06 冒烟）

默认检索（新→旧）首行：

```json
{"std_code": "GB/T 19247.1-2026", "title": "印制板组装 第1部分：通用规范 采用表面安装和相关组装技术的电子和电气焊接组装的要求",
 "std_nature": "推荐性", "issue_date": "2026-09-28", "act_date": "2027-01-01", "state": "即将实施",
 "record_id": "5CB12E0EFBCD2034E06397BE0A0A34FB", "source": "gb-standards", "scraped_at": "…"}
```

golden 定向检索（`search_text=GB/T 1.1-2020`，高亮已剥离）：

```json
{"std_code": "GB/T 1.1-2020", "title": "标准化工作导则  第1部分：标准化文件的结构和起草规则",
 "std_nature": "推荐性", "issue_date": "2020-03-31", "act_date": "2020-10-01", "state": "现行",
 "record_id": "A24AF19F41445C2EE05397BE0A0A5E0D", "source": "gb-standards", "scraped_at": "…"}
```
