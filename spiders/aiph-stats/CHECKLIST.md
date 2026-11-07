# aiph-stats 接入检查清单

- [x] 目录名 = manifest `name` = `aiph-stats`；入口函数 `run_aiph_stats`
- [x] 只依赖镜像预装依赖：`scrapling` + 仓内共享 `fd_industry_data.tabular.xlsx`（**不自写 xlsx 解析**，
      符合 conformance gate 的 workbook-parsing 规则），无新依赖
- [x] manifest 字段齐全：name/label/source_url/data_period/scanner_mode/functions(columns)/concepts/entities/fetch
- [x] `site` 缺省（tencent）——本机直连 aiph.org 实测 200，Cloudflare 前置但无 JS 挑战
- [x] **不写 schedule**——先静默合入，跑通再点亮（建议每年 1 月一次，年鉴年更）
- [x] 本地自检：`validate_manifests` 0 violations / `conformance_gate` 本单元 0 violations
- [x] 首爬回补窗口：**故意不声明** `window_start`/`window_end`——年鉴一版即全年，窗口过滤会误删真数据；
      历史深度改由 `editions` 参数控制（实测索引页 2022–2025 四版可用）
- [x] 传输完整性：比对 `content-length` → 不足则 `Range` 续传 → `zipfile.testzip()` 验包 →
      坏包整版跳过（实测截断 119679/131982 vs 136164）
- [x] golden 样本 `golden/001-aiph-stats.json`：`min_rows=50`，锁 9 品种里的 3 个（含上游拼写错误的
      `chrysanthemum` 表）、脚注剥离（`Netherlands 3` → `Netherlands`）、上游不一致（orchid 80/90）与
      古董年份（Kenya `ref_year=2012`）
- [x] 口径声明入 manifest description + README：**只做面积不做贸易**（Trade 表是 Eurostat 二次发布，
      避免与 `eurostat-api`/`un-comtrade` 双计）
- [x] 版权署名写入 README：对外看板引用须署名 AIPH / Union Fleurs
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（年鉴年更，建议 `0 6 20 1 *` 类年度 cron 并错峰）
- [ ] 波2 前置：给 `fd-open-data-protocol/vocabulary/units.yaml` 补 `hectare` 条目，
      否则 `area.flower.planted.*` 三概念注册时无量纲可依
