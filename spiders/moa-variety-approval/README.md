# moa-variety-approval —— 农业农村部种业管理司「公示公告」（**事件面**）

新源接入档案（`templates/new-source/` 范式）。
数据源：`https://zys.moa.gov.cn/gsgg/`（农业农村部种业管理司，实测 2026-11-08，国内直连）。

## 一句话结论：这个源只有事件，没有可解析的数值

详情页 **`<table>` 数 = 0**；品种清单（品种名 / 作物 / 审定编号 / 选育单位）全部在
**`.doc` 附件**里，且走政务 reader 代理：

```
https://zys.moa.gov.cn/zxfile/reader?file=/…/P020…doc
```

实测两处（省级 + 国家级）都是同一形态：

| 页面 | 发布日期 | `<table>` 数 | 正文数值 |
|---|---|---|---|
| 关于四川省审定通过的主要农作物品种名称等信息的公示 | 2026-11-03 | 0 | 无 |
| 第N届国家农作物品种审定委员会…初审通过品种…的公示 | 2026-10-22 | 0 | 无 |

按 2026-10-13 裁决，**OCR 类源暂不处理**；平台也没有 `.doc` 共享解析层，
不为它引入新依赖。因此本单元**只登记事件、不产出任何数值观测行**——
先例 `spiders/samr-food-inspection/`。清单要看请点开附件，本单元如实标注
「清单见附件，未解析」，而不是伪装成有数值。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 列表 | `GET https://zys.moa.gov.cn/gsgg/` | 静态 HTML，UTF-8，**19 条/页** |
| 分页 | `GET .../gsgg/index_N.htm` | `index_1`/`index_2` 均 200；`index_99` **404** → 翻页以 404 终止 |
| 详情 | `GET .../gsgg/YYYYMM/tYYYYMMDD_<id>.htm` | 无表、正文短，清单在附件 |

入口：`run_moa_variety_approval(limit=40, window_start=None, window_end=None, with_detail=False) -> list[dict]`
（`spider.py`，只用标准库 `urllib` + `re` + `html`）。

## 事件类型（闭集，全部从标题判定，不依赖详情页）

| `notice_type` | 标题特征 | 是否有品种清单 |
|---|---|---|
| `provincial_variety_approval` | `关于<省>审定通过的主要农作物品种名称等信息的公示` | 有，在附件 |
| `national_variety_approval` | 含 `国家农作物品种审定委员会` | 有，在附件 |
| `seed_license` | `<省>农业农村厅关于核发…种子生产经营许可证的公示` | 无（企业/区域名单） |
| `variety_revocation` | 含 `撤销审定` | 有，在附件 |
| `other` | 以上都不匹配 | 未知 |

`province` 从标题 `关于X省/自治区/市` 或标题首部解析；`scope` 见到
`国家农作物品种审定委员会` 判 `national`，否则 `provincial`。

## **附件存在性不猜**（`with_detail` 的语义）

默认 `with_detail=False`：19 条/页 × 多页对政务站请求量偏大，所以**不进详情页**，
此时 `has_attachment` / `attachment_count` / `variety_list_in_attachment`
一律 **null**——**未知就记未知，不写 `False`**。需要附件事实时显式传
`with_detail=True`，逐条进详情页数附件链接。

## 三处实现细节（都是实测踩坑后定的）

1. **发布日期粘在锚点文本里**：页面形如
   `<a href="./202611/t20261106_6489248.htm">…公示2026-11-06</a>`，日期在 `</a>`
   内部。不先摘掉的话会出现 `publish_date=""` 且标题尾部挂日期。→
   `_split_trailing_date()` 先摘日期，再回退到锚点后文本里的日期。
2. **「农牧厅」不等于「农业农村厅」**：`内蒙古自治区农牧厅关于…` 不含「农业农村」，
   旧的 `关于…农业农村厅` 正则匹配不到。→ 改成通用的
   `关于([\u4e00-\u9fa5]{2,12}?(?:省|自治区|市))` + 标题首部兜底。
3. **跳过窗口下界后可整页停**：列表严格倒序，某页全部早于 `window_start`
   就 break，不白翻到底（`MAX_PAGES=40` 只是硬上限）。

## 真实样例（2026-11-08 实跑，窗口 2026-11）

```json
{"publish_date": "2026-11-03", "title": "关于四川省审定通过的主要农作物品种名称等信息的公示",
 "notice_type": "provincial_variety_approval", "province": "四川省", "scope": "provincial",
 "url": "https://zys.moa.gov.cn/gsgg/202611/t20261103_6489162.htm",
 "has_attachment": null, "attachment_count": null, "variety_list_in_attachment": null}
```

同月共 6 条：川/琼/湘省级审定公示（11-03）+ 粤/冀/蒙种子生产经营许可公示（11-06）。

## 定位与边界

`authority: official`——农业农村部种业管理司本级发布。但**它是事件面**：
不产观测行，因此不进任何数值看板，也不作阈值预警判据。它的价值在于
「新品种审定节奏 / 许可核发」这类供给侧事件，供柏讯做事件条与关联线索。

## 自检

```bash
cd fd-industry-data
.venv/bin/python scripts/validate_manifests.py
.venv/bin/python scripts/conformance_gate.py
.venv/bin/python -m py_compile spiders/moa-variety-approval/spider.py
```
