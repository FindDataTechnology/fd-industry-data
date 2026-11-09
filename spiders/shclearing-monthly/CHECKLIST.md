# shclearing-monthly 接入检查清单（批次六，2026-11-08）

- [x] 目录名 = manifest `name`（`shclearing-monthly`；入口 `run_shclearing_monthly`）
- [x] `spider.py` 提供 `run_shclearing_monthly(limit=800, months=1, period="") -> list[dict]`，纯标准库
- [x] **端点实读**：`sdocClient/getMaxDate` → `"2026-09-30"`；`sdocClient/monthListPage`
      （`operTime=2026-09`，`channelId=ff80808140a43ba80140a45b17320007`）→ `flag="0"`，7 条；
      `sattaClient/download/<attachmentId>` 取附件（README「端点」表）
- [x] **三重门**：先 GET `/cpyyw/ywsj/tjyb/` 建会话；每次请求带
      `Referer: https://www.shclearing.com.cn/cpyyw/ywsj/tjyb/`；实测无 Referer 无 cookie
      → 302 `error.jsp`（空 body），带 Referer → 200（README「三重门怎么过」）
- [x] **魔数验证**：PDF `%PDF-1.7`（表四 43,214 B / 表五 48,350 B）；xlsx `PK\x03\x04`
      （25,251 B）；`Content-Type` 统一撒谎为 `application/x-msdownload`，故类型只认魔数
- [x] **PDF 文本层**：`pdftext.extract_text(payload, layout=True)` 抽取表四 22 行 / 表五 38 行；
      表五表头跨 3 行、空档不等宽，直接按空格切列会错位一列（证据见 README「失败的尝试」）
- [x] **表格解析走共享层**：同一批次汇总 xlsx 的 `表四`/`表五` 工作表经
      `tabular.read_grid(sheet=...)`；未内联手写 xlsx 解析器（gate 的
      `inline-tabular-parser` 规则覆盖）
- [x] **表五两行表头合并**：券种大类 + 子类以 `·` 拼接（`公司信用类债券·超短期融资券`），
      大类已含于子类名时不重复（`其他公司信用类债券`），单券种列用自身名
- [x] **PDF 与 xlsx 互证**：`表四 2.2/本月末=99,189.83`、`表五 2.存款类金融机构/金融债券=51,463.82`
      均在 PDF 文本中找到，与 xlsx `99189.83` / `51463.824572` 一致
- [x] **长表口径**：每（期, 表, 行标签, 指标）一行，10 个列键与 manifest `columns` 一一对应；
      单期约 257 行（表四 19 行 × 3 指标 + 表五 20 行 × 10 券种）
- [x] **单位**：两表 `单位：亿元`，全部行 `unit=亿元`；上游空/`-` 不出行，真实 `0.00` 出行
- [x] **与中债登对账**：2026-09 上清所表四合计 546,141.99715401 亿 vs 中债登托管合计
      1,365,657.5632941716 亿；商业银行侧上清所存款类 10 券种合计 ≈162,531.80 亿 vs
      中债登 `1.商业银行` 886,790.8119340657 亿。结论=不同托管人、不可等值，
      仅规模/占比可对账（上清所占两所托管 28.6%、占商业银行 15.5%，见 README 对账表）
- [x] **golden 定月**：入口新增 `period`（`YYYY-MM`/`YYYYMM`），golden 用 `period="2026-09"`
      钉死历史月；默认 `period` 空走 `getMaxDate`，调度口径不变（README「为什么 golden 钉 period」）
- [x] **参数真实生效**：`limit` 截断返回行数、`months` 控制回溯期数、`period` 定起点月；
      三者均在入口签名中显式声明，manifest 三个 `parameters` 均可达
- [x] **失败策略**：空目录当「未发布」回探（上限 `months+4`）；xlsx 失败回退 PDF 行级解析；
      单表失败留痕续跑；魔数不符 `ValueError`；探测窗全空 `RuntimeError`；请求间隔 1.2s
- [x] manifest 字段齐全：`version "2"` / `data_period: month` / `scanner_mode: upstream-curated` /
      `schedule: "12 15 5,20 * *"` / `fetch.module: fd_industry_data.provider:IndustryDataProvider`
- [x] 本地自检：`validate_manifests.py` → 146 manifests / **0 violations**；
      `conformance_gate.py` → 本单元无违规，但全仓 1 条**既有**违规
      `[missing-manifest] spiders/dounan-flower-price`（非本单元、未改动）
- [ ] 全仓 gate 0 违规（待 `dounan-flower-price` 由人工补 manifest 或归档后复跑）
- [ ] push 后 CI gate 绿
- [ ] 点亮后试跑
