# jp-foodservice-trend 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`jp-foodservice-trend`；入口 `run_jp_foodservice_trend`，
      与 `run_<slug>`（连字符转下划线）契约一致）
- [x] `spider.py` 提供 `run_jp_foodservice_trend(limit=500, since=None) -> list[dict]`，
      只依赖标准库 `urllib/json/re` + 共享 `fd_industry_data.tabular.read_grid`
      （xls/xlsx 读取走共享层，未内联自实现 workbook 解析）
- [x] manifest 字段齐全：version/name/label/source_url/schedule/data_period/
      functions（command/frequency/parameters/columns）/concepts/entities/fetch；
      `columns` 10 项与输出行 10 键一一对应
- [x] `site` 未写（只访问 www.jfnet.or.jp 公开索引页与 Excel，2026-11-08 实测直连
      可达，走缺省执行位 tencent）
- [x] **`schedule: "12 7 8 * *"`（每月 8 日 07:12，月度作业）+ `data_period: month`**
      ——JF 月次报告在次月月初上传（2026-09 版落在 `uploads/2026/10`），8 日取最新
      已发布版；13 个月滚动窗保证上月数据不丢
- [x] 参数声明与入口签名一致（gate `declared-param-reachability`）：`limit`（int，行数
      上限）与 `since`（str，最早月份 `YYYY-MM`）均在入口签名内；未声传入参不接受的参数
- [x] 口径原样保留：四个指标均为**前年同月比 ratio**（1.0 = 持平），不换算增减率；
      **全店**口径（含新开店，非既存店）；`全体` = 业态合计
- [x] 解析按内容定位，不写死行列号：块头按「前年同月比 + 指标关键词」识别
      （`客単価` 优先于 `客数`，避免子串误判）；月份行支持「N年M月 / M月」并跨年推进；
      业态按 7 个规范名匹配首列
- [x] 失败即红：索引页无 Excel 链接 / 缺「全店」表 / 无前年同月比块 / 某块 <10 格
      （版面漂移）/ 解析 0 行 / 跨块恒等式偏差 >1e-6 → `RuntimeError`；
      单格越界（`0.2..5.0` 外）或非数值 → 跳过并 stderr 留痕，不造数、不 0 填
- [x] 多版去重确定性：同一 `(month, segment)` 取**最新报告版**的值，`edition` 记录
      胜出版本；`since` 逐页回溯索引（第 8 页约到 2020），`since=None` 只取第 1 页
      （最新 9 份，并集约 21 个月）
- [x] golden 样本 `golden/001-jp-foodservice-trend.json`（params `limit=14,
      since="2025-01"`）：`min_rows=14` + 8 锚——`source`、`month=2025-01`、
      `segment=全体`、`edition=2026-01`，以及该行四个前年同月比的逐位定稿值
      （sales=1.0765190538017952 / 店舗数=1.0041919991116293 /
      利用客数=1.0339080179902553 / 客単価=1.0412135654914143，读自
      `uploads/2026/02/data_c_m2026_1.xls`）；`scraped_at` 在 whitelist；
      **不锚最新月**（2026-09，下版可能修订）
- [x] 本地自检：`validate_manifests.py` → `136 manifests, 0 violations`；
      `check_manifest_commands.py` → `scanned=134 problems=1`（唯一 DRIFT 是既有
      `education_card_catalog` 的 IMPORT_FAIL，与本单元无关）
- [x] golden schema：`load_samples` → `validate_sample` → `[]`（无错误）
- [x] golden 重放绿：`replay_sample(Path('spiders/jp-foodservice-trend'), s[0])`
      → `ok=True, diffs=[], rows=14`（实弹网络，2026-11-08，约 2 分钟 / 21 份报告）
- [x] 仓级机械闸：`/usr/bin/python3 scripts/conformance_gate.py` → **PASS — 138 units,
      0 violations**（`jp-foodservice-trend` 不在违规列表；本单元此前因缺 manifest
      被判红，已修复）
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 平台试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
      （建议 `--params '{"limit":500}'` 走缺省窗口；深历史单独跑 `since` 参数）
- [ ] 需要指标语义时，另行走统一指标注册目录治理（列→绑定→verified→标识符），
      不阻塞爬取本身

注（窗口与寿命）：缺省调用只取索引第 1 页（最新 9 份报告 → 合并 21 个月）；若需
2020 年起的更早月份，显式传 `since="YYYY-MM"`，spider 逐页回溯索引并按滚动窗去重。
golden 用 `since="2025-01"` 使锚点落在历史定稿月（后续版次窗口不再覆盖 2025-01），
避免缺省窗口每月前移导致的预期老化。
