# restaurant-rpi 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`restaurant-rpi`；入口 `run_restaurant_rpi`）
- [x] `spider.py` 提供 `run_restaurant_rpi(limit=N) -> list[dict]`，只依赖标准库
      `urllib/json/re/zlib/urllib.parse`（无新依赖；PDF 文本定位为 stdlib 自实现，
      共享 tabular 层只覆盖 xlsx/xls/html/csv/text，不含 PDF）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] `site` 未写（只访问 restaurant.org 公开页面/PDF，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门（建议 cron 定在每月末 RPI
      发布日之后）
- [x] 数据周期 `data_period: month`（RPI 月度，每月最后一个工作日发布）
- [x] golden 样本 `golden/001-restaurant-rpi.json`（锚 2026-05 定稿历史值 rpi=100.1
      + source + month，min_rows 3；**滚动窗约 5 版寿命，滑出后重录**，见 README）
- [x] 有界历史回补：入口 `run_restaurant_rpi(limit, months_back=0)`，缺省 0 保持原语义
      （只取当期 5 个月窗）；`months_back` 1..18 按期次 URL 规律回取旧版月报
      （`/NRA/media/Research/RPI/2024/RPI-<Month>-<Year>.pdf`），合并去重后按 month
      升序；**>18 或为负 → ValueError**；单期 404/失败/无表 → stderr 留痕并跳过，
      不重试、不判红、不造数；回补版还要求窗口连续且末月 = 版次月
      （March-2026 源表把 January 2026 误印为 January 2025 → 整版跳过）
- [x] golden `golden/002-restaurant-rpi-backfill.json`（params `limit=60, months_back=12`）
      锚只在回补版出现的更早月份（2025-09），验证回补与跨版合并
- [x] manifest `parameters` 声明 `months_back`（int，说明回补语义与 ≤18 上限）
- [x] 关键坑落实：官方页发现现行 PDF 链接（工单旧链 404）；数值切块 x 间距合并；
      同名双序列分节落位；缺值 None 不补 0；当期两跳全失败/解析 0 表 → RuntimeError
- [x] 本地自检全绿：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py --roots .`（从仓根跑；在 finddata 工作区根则加
      fd-industry-data/ 前缀）
- [x] golden 重放绿：`load_spider_module('restaurant-rpi','spiders')` 真实取数回放，
      ok=True rows=5
- [x] 真实落库：runner `--params '{"months_back":12}'` success，库内观测月
      2025-05..2026-09 共 17 个月（Sep-2025…Sep-2026 13 版全部 200；
      March-2026 因源表年份笔误整版跳过，非 404）
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）——**人工门，
      spider-heal 不点亮**
- [ ] 需要指标语义时，另行走统一指标注册目录治理（列→绑定→verified→标识符），
      不阻塞爬取本身

注（历史回补）：单版报告只提供最近 5 个月滚动窗，缺省行为（`months_back=0`）不变；
需要更长序列时显式传 `months_back`（≤18）按版次 URL 回取，各版窗口合并/去重。
回补失败只 stderr 留痕，不影响当期 success 判定；缺月不补造。
