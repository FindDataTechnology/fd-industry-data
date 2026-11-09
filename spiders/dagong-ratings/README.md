# dagong-ratings —— 大公国际 银行跟踪评级（定期跟踪公告）

新源接入档案（批次六·银行业，change `add-banking-industry-sources` 任务 6.2）。

## 端点（从栏目页内联 Vue 源码实读 + 实测）

栏目页 `https://www.dagongcredit.com/dggj/ratingNotice/regularTracking/A009003002index_1.htm`
页内 Vue 的 `handleSearch()` 原文（摘录）：

```js
this.params.pageNum = this.curPage || 1
this.params.size = this.pageSize || 10
this.params.channelCode = this.channelcode          // 本栏目 = A009003002
const res = await axios.post('/api/construction/openApi/getContentInfoPageList',
                             {...this.params, content});
this.tableData = res.data.data.records || [];
this.totalNum = res.data.data.total || 0;
```

实测请求原文（2026-11-08，HTTP 200）：

```http
POST /api/construction/openApi/getContentInfoPageList HTTP/1.1
Host: www.dagongcredit.com
Content-Type: application/json;charset=UTF-8
Accept: application/json, text/plain, */*
Origin: https://www.dagongcredit.com
Referer: https://www.dagongcredit.com/dggj/ratingNotice/regularTracking/A009003002index_1.htm

{"pageNum":1,"size":10,"channelCode":"A009003002","startTime":"","endTime":"","topic":"商业银行","content":""}
```

响应原文（截取首条记录与本层分页字段）：

```json
{"code":0,"data":{"records":[{"infoId":"1542110629026856960",
"channelCode":"A009003002",
"topic":"浙江平阳农村商业银行股份有限公司2026年度跟踪评级报告",
"pubTime":"2026-08-24 09:57:03",
"filePath":"/dggj/ratingNotice/regularTracking/2026/8/b5d0737af3104cbcb70313502bcf1273.htm",
"a1":"/dggj/content_file/ratingNotice/regularTracking/2026/8/72e79c17d64a41e594bb3d6e6cac8b50.pdf",
"a8":"AA","a9":null,"a12":"稳定",
"description":"大公国际资信评估有限公司确定浙江平阳农村商业银行股份有限公司的主体信用等级维持 AA，评级展望维持稳定。"}],
"total":94,"size":10,"current":1,"pages":10}}
```

**分页字段**：请求 `pageNum`（1 起）+ `size`；响应 `total` / `size` / `current` /
`pages`。实测 `size=10` 可用，`总 94 条 = 10 页`，第 10 页 4 条。

## 三个实测硬坑

1. **参数名是 `size`，不是 `pageSize`**。用 `{"pageNum":1,"pageSize":5,"topic":"商业银行"}`
   实测 HTTP 500：`{"status":500,"error":"Internal Server Error","path":"/openApi/getContentInfoPageList"}`。
   照抄栏目页的 `{pageNum,size,channelCode,startTime,endTime,topic,content}` 才 200。
2. **必须 JSON body**：表单编码（`application/x-www-form-urlencoded`）实测
   HTTP 415 `{"status":415,"error":"Unsupported Media Type"}`。
3. **`topic` 是「报告名称」关键字，不是行业字段**——栏目页把它绑在报告名称输入框上。
   银行口径必须配合 `channelCode=A009003002`（定期跟踪）；两者分开时口径完全不同
   （见下节证据表）。

## `topic` 过滤生效证据（同一接口三次实测）

| `channelCode` | `topic` | `total` | 说明 |
|---|---|---|---|
| `""` | `商业银行` | **172** | 跨栏目：含 A009003001（首次/信用评级）、A009003008、A009003002 |
| `A009003002` | `""` | **6679** | 定期跟踪全部行业（首条为中建安装集团 2026 年度跟踪评级报告） |
| `A009003002` | `商业银行` | **94** | **本单元口径**；与侦察簿记的「topic=商业银行 94 条」逐字相等 |

因此本单元写死栏目码 `A009003002`（模块常量 `CHANNEL_CODE`），`topic` 开放为参数
（默认 `商业银行`）。

## 字段实测含义表（`a1/a8/a12` 及同层字段）

| 字段 | 实测含义 | 实测证据 |
|---|---|---|
| `a1` | **PDF 直链**（相对路径，需补 `https://www.dagongcredit.com`） | 浙江平阳：`/dggj/content_file/ratingNotice/regularTracking/2026/8/72e79c17d64a41e594bb3d6e6cac8b50.pdf`；旧条目为 `/dggj/uploads/M00/uploadfile/...` |
| `a8` | **主体信用等级** | 浙江平阳 `a8=AA`，同条 `description` 写「主体信用等级维持 AA」 |
| `a9` | **债项等级**（可空；也可多级别串） | 山东安丘 `a9=A`；浙江稠州 2019 条 `a9="AA+,AA"`；浙江平阳 `a9=null` |
| `a12` | **评级展望**（可空） | 绵阳市商业银行 `稳定`；浙江温州龙湾 2026-07-29 条 `a12=null`（展望缺失） |
| `pubTime` | **评级时间**（`YYYY-MM-DD HH:MM:SS`） | 输出取日期部分 → `rating_date` |
| `topic` | **报告名称** | `浙江平阳农村商业银行股份有限公司2026年度跟踪评级报告` |
| `filePath` | 公告详情页（相对路径） | 输出为 `detail_url` |
| `infoId` | 上游内容主键 | `1542110629026856960` |
| `channelCode` | 栏目码 | 全量 94 条恒为 `A009003002` |

**空值不伪造**：2018 年及以前的 36 条记录 `a8` 为空、37 条 `a12` 为空（实测全量
94 条统计），输出一律留空串，不从标题/正文猜等级；`a1` 缺失时 `pdf_url` 留空。

## 实测取数证据

- 全量列表：`channelCode=A009003002 & topic=商业银行` → **94 条 / 10 页**（第 10 页 4 条）。
- **首爬（默认参数 `limit=200, topic=商业银行, pages=3, page=1`）→ 30 行 / 4.12s**
  （3 次请求 + 2×1s 礼节间隔）。
- golden replay（`pages=1, page=5`）→ **10 行 / 1.35s，ok=True diffs=[]**。
- 样例行（首爬第 1 条）：

```json
{"issuer":"浙江平阳农村商业银行股份有限公司",
 "report_title":"浙江平阳农村商业银行股份有限公司2026年度跟踪评级报告",
 "subject_rating":"AA","debt_rating":"","outlook":"稳定","rating_date":"2026-08-24",
 "pdf_url":"https://www.dagongcredit.com/dggj/content_file/ratingNotice/regularTracking/2026/8/72e79c17d64a41e594bb3d6e6cac8b50.pdf",
 "detail_url":"https://www.dagongcredit.com/dggj/ratingNotice/regularTracking/2026/8/b5d0737af3104cbcb70313502bcf1273.htm",
 "report_type":"regular_tracking","info_id":"1542110629026856960","source":"dagong-ratings"}
```

- 年份跨度 2014-2026，发布集中在每年 6-8 月（`data_period: irregular` 的依据）。

## 为什么 golden 加 `page` 参数（并钉第 5 页）

列表按 `pubTime` **倒序**分页，新公告会把旧条目整体往后推：任何「第 N 条」式锚点
都会随新公告漂走——这与 chinabond 可按月报期号钉死的形态不同。因此入口在契约
`limit/topic/pages` 之外**增加 `page` 起始页参数**（默认 1 = 最新页）：

- golden 用 `pages=1, page=5` 钉住第 41-50 条（2021-07-29 ~ 2019-07-29，等级已
  定稿、不会再变），锚第 49 条：**南洋商业银行（中国）有限公司 / 2020-06-11 /
  AAA / AAA / 稳定**，并等值校验 PDF 直链与 `infoId`；
- 调度口径不变（默认 `page=1` 取最新 3 页 30 条）。

这是**增量**（多一个可选参数），不是收窄：默认行为与新源口径一致，增量只为让
「锚固定条目」与「列表持续新增」两者同时成立。四个参数均真实生效：
`limit` 截断、`topic` 过滤、`pages` 页数、`page` 起始页。

## 失败策略

- 单页网络/非 JSON/`code!=0` 失败 → 留痕 `[dagong-ratings] 第 N 页失败：…` 后继续下一页；
- 某页 `records` 为空 → 视为列表到底，停止翻页；
- 所有请求页都失败或为空 → `RuntimeError`（不返回空表冒充成功）；
- 记录缺 `topic` → 丢该条留痕（无标题无法定位报告）；
- `a8/a9/a12` 为 `null` → 输出空串（不写 "None"，不猜等级）；
- 相邻请求间隔 1s（`SLEEP`）；10 页全量也只需 ~19s。
