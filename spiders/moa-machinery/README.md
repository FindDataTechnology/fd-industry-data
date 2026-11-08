# moa-machinery —— 农业农村部《全国农业机械化发展统计公报》（年度）

新源接入档案（`templates/new-source/` 范式；同源参考 `spiders/moa-hog-price/`）。
数据源：农业农村部农业机械化管理司「农业机械化情况」专刊
`https://njhs.moa.gov.cn/nyjxhqk/`（实测 2026-11-08，国内直连，禁代理）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 列表 | `GET https://njhs.moa.gov.cn/nyjxhqk/` | UTF-8，单页静态、无分页；每条公报一个 `<li>` |
| 正文 | `GET https://njhs.moa.gov.cn/nyjxhqk/<yyyymm>/t<yyyymmdd>_<id>.htm` | 标题 `h1.sj_xiang_biaoti`；正文 `div.sj_arc_body > div.TRS_Editor`，1 张作物表 + 三段文字 |

列表条目实测形态（2025 期）：

```html
<li><a href="./202610/t20261019_6488733.htm" title="2025年全国农业机械化发展统计公报">
    <span class="sj_gztzle">2025年全国农业机械化发展统计公报</span>
    <span class="sj_gztzri">2026-10-19</span></a></li>
```

入口：`run_moa_machinery(limit=20, window_start=None, window_end=None) -> list[dict]`
（`spider.py`）。仅标准库 `urllib` + `re` + `html`，无新依赖、不 OCR。

## 鉴权与参数

- 无 key / 无验证码 / 无 cookie；带常规浏览器 UA。
- **禁代理**：opener 显式挂空 `ProxyHandler({})`，不吃 `http_proxy`/`https_proxy` 环境变量。
- TLS：`*.moa.gov.cn` 为 CFCA OV 公开站点；按 `moa-hog-price` 同源惯例不对该 host
  做证书校验（不假设容器信任库），无凭据可泄。
- `limit` 计**期数**（每期约 19 行），最新期在前。
- `window_start`/`window_end`（YYYY-MM-DD）为平台首爬回补窗口，**不上 manifest
  声明面**（同 `abs-sdmx` 惯例），入口签名显式接收即可被 kwargs 注入；按公报
  **发布日期**过滤，且先过滤后截断。非法/不成对 → `ValueError`。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `period` | 标题 `YYYY年` | **期号年份**（2025 期发布于 2026-10-19，`period` 仍是 `2025`） |
| `item` | 见下表 | 指标标识（英文 snake_case） |
| `value` | 正文/表格 | float；**解析失败跳过不出行，绝不写 0** |
| `unit` | 正文/表头 | 亿亩次 / % / 万台 / 万部 / 万架 / 万个 / 万人 / 亿元 |
| `title` | `h1.sj_xiang_biaoti` | 公报标题 |
| `source_url` | — | 正文页 URL |
| `scraped_at` | — | UTC ISO8601 |

## 指标清单（跨年措辞稳定，2022/2024/2025 逐条实测）

| item | 中文 | unit | 定位句 |
|---|---|---|---|
| `operated_area_mu_times` | 五项作业面积 | 亿亩次 | 全年完成机耕、机播、机收、机电灌溉、机械植保五项作业面积达到 X 亿亩次 |
| `mechanization_rate` | 农作物耕种收综合机械化率 | % | 全国农作物耕种收综合机械化率达(到) X% |
| `tillage_rate` | 机耕率 | % | 机耕率、机播率、机收率分别达到 X%、Y%、Z% |
| `seeding_rate` | 机播率 | % | 同上 |
| `harvest_rate` | 机收率 | % | 同上 |
| `tractor_count` | 拖拉机拥有量 | 万台 | 全国拖拉机拥有量 X 万台 |
| `tractor_implement_count` | 配套农具 | 万部 | 配套农具 X 万部 |
| `uav_count` | 农用（植保）无人驾驶航空器 | 万架 | (农用（植保）)无人驾驶航空器拥有量达到 X 万架 |
| `cooperative_count` | 农机专业合作社 | 万个 | 农机专业合作社 X 万个 |
| `operator_count` | 乡村农机从业人员 | 万人 | 乡村农机从业人员 X 万人 |
| `service_income` | 农机服务收入 | 亿元 | (全国)农机服务收入 X 亿元 |
| `crop_mechanization_rate_<crop>` | 分作物耕种收综合机械化率 | % | 作物表（小麦/水稻/玉米/大豆/油菜/马铃薯/花生/棉花，固定 8 行） |

**刻意不抽**（宁缺不错）：

- 谷物联合收割机 / 玉米收获机 / 水稻插秧机的「拥有量分别达到…」——三个品种的
  中文枚举顺序逐年在换（2022 期收割机在前，2024/2025 期插秧机在前），按位置抽会张冠李戴。
- 农业机械总动力——只有 2022 期给出（统计口径已变）。
- 保护性耕作面积——2022 期是「8300 万亩」，2024/2025 期是「1.81/1.75 亿亩」，
  单位跨年不一致，同一序列不可比。

## 坑位与容错

- **列表页偶发串频道**：实测同一 URL 某次返回「通知公告」频道页面（同模板、无公报
  链接）。`_fetch_list` 校验候选链接，取空即重试（共 3 次、间隔 1 s），仍空抛
  `RuntimeError`——不静默返回空列表。
- **指标句被标签切碎**：HTML 里 `<font>`/`<span>` 把句子拆开，直接对原始 HTML 跑
  正则会漏。必须先剥标签、反转义、去全部空白，再匹配。
- **整期跳过留痕**：单期一个指标都没抽到 → 该期跳过并 `logger.warning`；单页取数
  失败 → 跳过并记数；全部期次 0 行 → `RuntimeError`（失败即红）。
- **发布年份 ≠ 数据年份**：`period` 只取标题年份。

## 历史深度

实测 4 期：2022（2024-06-18 发布）、2023（2024-09-26）、2024（2025-11-20）、
2025（2026-10-19）。年度，每年一期；列表页单页静态，无更深归档。

## 实测样例行（2026-11-08 实跑，76 行 = 4 期 × 19 行）

```json
{"period": "2025", "item": "operated_area_mu_times", "value": 79.76, "unit": "亿亩次", "title": "2025年全国农业机械化发展统计公报", "source_url": "https://njhs.moa.gov.cn/nyjxhqk/202610/t20261019_6488733.htm", "scraped_at": "2026-11-07T16:50:22.619941+00:00"}
{"period": "2025", "item": "mechanization_rate", "value": 76.82, "unit": "%", "title": "2025年全国农业机械化发展统计公报", "source_url": "https://njhs.moa.gov.cn/nyjxhqk/202610/t20261019_6488733.htm", "scraped_at": "2026-11-07T16:50:22.619941+00:00"}
{"period": "2025", "item": "tractor_count", "value": 1999.19, "unit": "万台", "title": "2025年全国农业机械化发展统计公报", "source_url": "https://njhs.moa.gov.cn/nyjxhqk/202610/t20261019_6488733.htm", "scraped_at": "2026-11-07T16:50:22.619941+00:00"}
{"period": "2022", "item": "mechanization_rate", "value": 73.11, "unit": "%", "title": "2022年全国农业机械化发展统计公报", "source_url": "https://njhs.moa.gov.cn/nyjxhqk/202406/t20240618_6457395.htm", "scraped_at": "2026-11-07T16:50:22.619941+00:00"}
```

## 自检

```bash
python3 spiders/moa-machinery/spider.py            # 实跑最新一期，打印行数 + 前 5 行
python3 scripts/validate_manifests.py
python3 scripts/conformance_gate.py --roots .
python3 -m py_compile spiders/moa-machinery/spider.py
```

## golden 断言口径

`golden/001-moa-machinery.json` 用 `params={"limit": 20}`（当前全部 4 期，共 76 行），
锚 2025 期三项已知值 `79.76`（作业面积）/ `76.82`（综合机械化率）/ `1999.19`
（拖拉机保有量），加 2022 期 `73.11` / `2144.07` 两个更早锚点。所锚数值四期互不
相同，`rows_contains` 的逐条独立匹配不会串期。`scraped_at` / `source_url` 进
`whitelist_fields`（URL 会随站点改版变动，不作断言）。
