# ico-coffee-price —— ICO 咖啡指示价（日度 PDF，I-CIP 总指标 + 4 组指标）

新源接入档案（`templates/new-source/`）。餐饮垂直线波 1 源；风格参照
`spiders/restaurant-rpi/`（同为官方 PDF 面）。

## 数据表面（2026-11-07 实测）

| 用途 | 端点 | 说明 |
|---|---|---|
| 当月活文档（主） | `GET https://ico.org/documents/I-CIP.pdf` | 200 直连（www.ico.org 302→ico.org）；当月逐日表 |
| 月度归档（2026 命名） | `GET https://ico.org/documents/prices/I-CIP_<MM>.<YYYY>.pdf` | 2026-01..2026-09 实测全 200 |
| 月度归档（2025 命名） | `GET https://ico.org/documents/prices/I-CIP_<MonthName><YYYY>.pdf` | 2025-09..2025-12 实测全 200 |
| 归档双 host | `https://icocoffee.org/documents/prices/...` | 两种命名交叉同样 200（spider 作 ico.org 之后的兜底 host） |

入口：`run_ico_coffee_price(limit=500, months_back=2) -> list[dict]`（`spider.py`）。
依赖 = 纯标准库 + poppler `pdftotext`（`-layout`；Dockerfile runner 阶段已装
poppler-utils）。

## 归档真实窗口（诚实口径，2026-11-07 全量探测）

- **在线归档只有 ~13 个月**：2025-09..2025-12（2025 命名）+ 2026-01..2026-09
  （2026 命名）+ 当月活文档（2026-11）。
- **2026-10 实测 404**（ico.org / icocoffee.org 双 host、两种命名均试）——归档
  滞后于当月；缺口月按月跳过并在 stderr 留痕，不判红、不造数。
- **2025-01..2025-08、2024 及更早全 404**。1990 年起的长序列官方只挂死链
  xlsx；页面明文 Excel「**on request** from stats@ico.org」（Members/学术免费，
  其余报价）——本单元不伪造更早历史，不做 URL 猜测式回补；长史扩展属线下
  数据获取（邮件索取）后的另行接入。

## PDF 布局与解析

```
In US cents/lb
                I-CIP   Colombian Milds   Other Milds   Brazilian Naturals   Robustas
2-Nov           254.88  348.47            329.01        288.59               158.01
6-Nov                                        （空行 = 周末/假日，跳过）
Average / High / Low / DoD Change            （汇总行，非 D-Mon 形态，天然排除）
```

- 锚定**最后一个** `In US cents/lb`（首个在图表纵轴区）；表尾到
  `© International Coffee Organization` 为止。
- **列交叉验证守卫**（表头名与数值列错位风险的对冲）：每行要求 Colombian
  Milds = 5 列最高、Robustas = 最低、I-CIP 介于其间、全为正；出界抛
  `RuntimeError`（判红）。
- **年月归属**：日行只写 `D-Mon`，年份取文档标题
  `ICO Indicator Prices - <Month> <YYYY> (I-CIP)`；归档月与 URL 期望月交叉
  校验，不一致判红。日期 + 1–4 数值的残行判红（版面异常）。

## 字段口径（宽表，每日一行）

| 列 | 口径 |
|---|---|
| `month` | `YYYY-MM`（标题年月，归档月另与 URL 交叉校验） |
| `day` | 交易日 `YYYY-MM-DD`；周末/假日（源表空行）不出行 |
| `icip` | ICO Composite Indicator Price，US cents/lb（**唯一注册概念列**） |
| `colombian_milds` / `other_milds` / `brazilian_naturals` / `robustas` | 4 组分指示价，US cents/lb；**留在 payload 不注册概念**（需求裁决：综合价单概念入语义库，分项随行可溯） |
| `source_url` | 该行所出自 PDF 的确切 URL |
| `scraped_at` | 抓取时间 ISO 串 |

`limit` = 返回行数上限：按 `day` 升序确定性排序后截取。

## 网络模式（2026-11-07 实测）

- 直连可达（ico.org / icocoffee.org 均 200，未触发代理回退）。spider 行为：
  直连优先 → 网络级失败回退一次 `http://127.0.0.1:7890` 代理；超时 30s。
- 当月文档不可得 → 判红（主数据面）；归档月 404 → 跳过留痕。

## 与 data.ico.org JSON API 的关系（架构变更记录）

本单元曾有一版走 `data.ico.org/globe/api/v1/globe/icip` JSON API（同日早间
侦察产物，未提交即被本版替换）。改判依据：

1. **窗口更宽**：API 匿名层 `openFrom=2026-01-01`（实测 2026-01-02 起 217 个
   交易日）；PDF 归档实测到 **2025-09**（约 13 个月）。
2. 平台 PDF 抽取能力已落地（`pdftotext` 进 runner 镜像，batch-six 引入），
   原侦察「镜像无 PDF 解析库故不接 PDF」的前提不再成立。
3. 需求裁决走 PDF 面 + 列交叉验证（表头/数值列错位风险由恒序守卫对冲）。

门户 API 仍是合法的深历史扩展路径（注册登录后数十年序列），如未来接入应
另立命令/单元，不与本 PDF 面混装。

## golden 断言口径（跨期稳定）

`golden/001-ico-coffee-price.json` 以 `months_back=13` 重放全真实窗口（当月 +
2025-09..2026-09 归档，含两种命名 × 双 host 全路径），锚**两个归档月的
I-CIP 精确历史值**（2026-09-01 = 279.85、2025-09-01 = 333.68，均为已收盘
归档值）。**绝不锚当月值**——ICO 对当月值日间修订（活文档明注「October 1 & 2
data were revised on October 5」）；归档月为定稿。ICO 若重述归档历史会使锚
可见变红——按 README 口径换锚，不属静默漂移。`scraped_at` 进
`whitelist_fields`。

## 坑位与容错

- **失败即红**：当月文档不可得 / 非 PDF / pdftotext 失败 / 列交叉验证出界 /
  残行 / 全部候选零产出 → `RuntimeError`。
- **归档缺口不红**：归档月 404（如 2026-10）跳过留痕——归档深度是上游运营
  状态，不是解析失败。
- **无 schedule**：manifest 刻意不写 schedule，静默合入，试跑验证后人工点亮。
- 首爬回补窗口（`window_start`/`window_end`）未在入口声明——在线归档本就
  只有 ~13 个月且由 `months_back` 控制，平台窗口对滚动 12 个月回补是自然
  子集；如需按窗取数再显式接参（见 CHECKLIST）。

## 自检

```bash
./.venv/bin/python spiders/ico-coffee-price/spider.py   # 冒烟（当月 + 近 2 月归档）
./.venv/bin/python scripts/validate_manifests.py
./.venv/bin/python scripts/check_manifest_commands.py
./.venv/bin/python scripts/conformance_gate.py
```
