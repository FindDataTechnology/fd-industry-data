# worldbank-procnotices —— 世界银行采购公告

新源接入档案。工单：`reports/health-tickets/20261006-worldbank-procnotices-50c670be.yaml`
（`kind=generate`，brief 侦察簿 W1-M）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 公告明细 | `GET https://search.worldbank.org/api/v2/procnotices` | 公开 REST API v2，免鉴权，返回 JSON |

入口：`run_worldbank_procnotices(limit=100, qterm=None) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib`，无新依赖。

## 参数白名单（全部实测过，2026-10-07 直连冒烟）

| 参数 | 实测口径 |
|---|---|
| `rows=50` | 每页条数（实测 50 正常返回 50 条） |
| `os=<n>` | 分页偏移游标 |
| `srt=noticedate&order=desc` | 按公告日期倒序（最新在前），显式固化默认序 |
| `fl=<字段列表>` | 字段白名单；**实测 `fl=docna,pdate` 不生效**（返回仅剩 `project_id`），真实字段名为 camelCase |
| `qterm=<项目号>` | 项目全文过滤；golden 重放走固定历史项目 `P099833`（64 条，2013-2014，结果恒定） |

刻意排除 `notice_text`（单条数十 KB 的 HTML 正文），白名单化后单页 <100 KB。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `notice_id` | `id` | 公告唯一号（如 `OP00473030`） |
| `notice_type` / `notice_status` / `notice_lang` | `notice_type` / `notice_status` / `notice_lang_name` | 类型（REOI/IFB/Contract Award…）/状态（Published/Draft/Revised）/语言 |
| `pdate` | `noticedate` | 公告日期，上游原样 `05-Oct-2026` |
| `date` | 派生 | ISO `2026-10-05`；解析失败 `None` 不猜 |
| `project_id` / `project_name` / `country` | `project_id` / `project_name` / `project_ctry_name` | 项目三元组 |
| `bid_reference_no` / `title` | `bid_reference_no` / `bid_description`（缺失回退 `project_name`） | 标书编号 / 公告标题 |
| `procurement_method` | `procurement_method_name` | 采购方式（如 QCQS/CQS） |
| `submission_deadline` | `submission_deadline_date` | 递交截止日（原样） |
| `url` | 请求 URL（页级） | 来源可追溯 |
| `source` / `scraped_at` | 常量 / 本地 | `worldbank-procnotices` / UTC ISO8601 |

**无金额字段**（brief.notes 明示 42 万条为「公告量」口径）——按日/月公告量由下游对
`date` 聚合得出，本单元不虚造金额。

## 坑位与容错

- **单页失败跳过留痕**：错误打到 stderr 并继续下一页；**全部页失败抛 `RuntimeError`**
  （失败即红，不静默返回空列表）。
- **防御性翻页上限**：连续页失败时以 `os > limit*4 + 4*PAGE_SIZE` 封顶，不无限翻页。
- 只带常规浏览器 UA + `Accept: application/json`；不做指纹伪装、不做频次对抗、
  无签名/验证码处理——遇风控升级按协议转人工（换数据表面），不逆向。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
python3 spiders/worldbank-procnotices/spider.py          # 冒烟，打印 3 行最新公告
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-worldbank-procnotices-50c670be.yaml
python3 scripts/validate_manifests.py && python3 scripts/check_manifest_commands.py
python3 scripts/conformance_gate.py
```

## golden 断言口径（跨期稳定）

`golden/001-worldbank-procnotices.json` 用 `qterm=P099833`（已关账历史项目，64 条公告，
最近一条 2014-11-14 之后不再新增）**重放固定历史**，锚常量：`notice_id == OP00030142`、
`pdate == 14-Nov-2014`、`project_id == P099833`、`source == worldbank-procnotices`，
`min_rows: 5`（该项目近 5 条均 ≤2014，永不变化）。
**绝不锚** 最新公告、total 计数（42 万+随日增长）与任何滚动值；
`scraped_at`/`timestamp` 进 `whitelist_fields`。
