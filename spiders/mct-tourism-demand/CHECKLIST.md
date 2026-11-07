# mct-tourism-demand 接入检查清单（add-hotel-vertical · 波1）

- [x] 目录名 = manifest `name`（`mct-tourism-demand`；入口 `run_mct_tourism_demand`）
- [x] `spider.py` 提供 `run_mct_tourism_demand(limit, pages, periods) -> list[dict]`，只用标准库（urllib/re/json/html）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/entities/fetch
- [x] `site` 未写（只访问 zwgk.mct.gov.cn 公开静态页，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] `data_period: irregular` 并写明理由（四种长度的累计期，词表无对应项）
- [x] 期次从标题解析（`fy` / `ytd_h1` / `ytd_9m` / `quarter`），**不按发布日推算**
- [x] 累计与单季分两个 `period_kind`，同 2026Q1 跨文档重复按 (期间,粒度,口径,指标) 去重：
      同值留痕「同值复现」，异值留痕「口径冲突」且两行都留
- [x] 全国 / 城镇 / 农村三档全入行，不只取总数
- [x] 单位保留上游原样（亿人次 / 万亿元 / 万人次 / 家 / 亿元 / 元/间夜 / % / 个），**不做量纲换算**
- [x] `yoy_pct` 只在**同句**内取（句界 `。`/`；`），下降取负；未公布留空不补 0
- [x] `delta` 仅当单位与指标同族才给（否则不给），杜绝「花费增量挂到人次行」
- [x] 公报行 `source_kind=bulletin`，与季报 `dispatch` 严格分开（直报口径 ≠ 季报口径）
- [x] `<style>` / `<script>` 整块剥除；数字被标签切开时压空白归一
- [x] `BULLETIN_MIN_YEAR=2024` 之前的公报整份跳过并留痕（措辞不同，不跨口径拼补）
- [x] `UnmappedPhrase` 硬失败：任一 dispatch 稿缺 `国内居民出游人次` → `RuntimeError`
- [x] `LAST_TRACES` 留痕：每份文档行数、跳过原因、同值复现 / 口径冲突
- [x] golden 样本 `golden/001-mct-tourism-demand.json`（锚 2024–2026 已冻结值，24 行；四个易变字段入 whitelist）
- [x] 离线回归：`tests/test_mct_tourism_demand.py` 跑 `tests/fixtures/mct_tourism/` 七份真件
- [x] 实跑证据（2026-11-08，7 份真件）：60 行 / 7 份文档 / 6 组同值复现 / 0 组口径冲突
- [ ] 仓级自检全绿：`validate_manifests.py` / `conformance_gate.py` / `check_manifest_commands.py`
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（`crawl_sources`）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，核对 `crawl_runs` 与产出
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）——**人工门，spider-heal 不点亮**
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身
- [ ] 5.2 供需板接线：本单元的需求侧行 × `mct-star-hotel` 的供给侧行，两源延迟各自标注
