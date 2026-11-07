# dounan-flower-price 接入检查清单

- [x] 目录名 = manifest `name` = `dounan-flower-price`；入口函数 `run_dounan_flower_price`（连字符换下划线）
- [x] `spider.py` 只依赖镜像预装依赖：`scrapling.fetchers.FetcherSession`（标准库 re/html/datetime），无新依赖
- [x] manifest 字段齐全：name/label/source_url/data_period/scanner_mode/functions(columns)/concepts/entities/fetch
- [x] `site` 缺省（tencent）——国内昆明市政府源，直连实测 200，不写 `site`
- [x] **不写 schedule**——先静默合入，跑通再点亮
- [x] 本地自检：`python3 scripts/validate_manifests.py && python3 scripts/conformance_gate.py --roots .` 双绿
- [x] 首爬回补窗口：入口显式声明 `window_start` / `window_end` 两个可选参数（同时读
      `FD_WINDOW_START` / `FD_WINDOW_END`），并按 `price_date` 过滤；
      **窗口过滤先于 limit 截断**（列表按发布倒序，一旦整篇早于窗口下界即停止翻页）
- [x] golden 样本 `golden/001-dounan-flower-price.json`：窗口钉死单日（2026-11-05），
      锁 23 品种表 + 三价（金鱼草 1.15/0.94/1.05、红玫瑰（单头）0.35/0.22/0.27）
- [x] 历史深度如实声明：该栏目只有 **滚动窗**（实测 2026-11-08 最老可见 2026-09-09，
      约 2 个月、10 个列表页），能回补多少回补多少，不宣称长期历史
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（建议每日 09:30 后一次；上游 T+1 发布）
- [ ] 指标语义另行统一注册（`price.flower.dounan.{high,low,avg}`），不阻塞爬取本身
