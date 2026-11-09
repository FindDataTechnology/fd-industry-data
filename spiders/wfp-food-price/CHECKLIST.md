# wfp-food-price 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`wfp-food-price`；入口 `run_wfp_food_price`）
- [x] `spider.py` 提供 `run_wfp_food_price(limit, window_start, window_end, years, include_non_food)`，
      只依赖标准库 `urllib/json/csv/datetime`（无新依赖）
- [x] **2026-11-08 迁移完成**：数据集 slug 从冻结的 `wfp-food-prices`（2021-08 后资源 202 空体）
      换成活的 `global-wfp-food-prices`（`last_modified=2026-11-05`）
- [x] **列映射整表重写**：新 schema 与旧版毫无重合（`countryiso3`/`date`/`category`/
      `pricetype`/`priceflag`/`usdprice` …），不是改名而是换源
- [x] **按年分片取数**：40 个 CSV / ≈620MB；`years` 缺省只下当年 + 上一年，绝不拉全档
- [x] 流式解析，取满 `limit` 即断流，不整文件落地
- [x] manifest 字段齐全：name/label/source_url/authority/data_period/functions/parameters/columns/concepts/entities/fetch
- [x] `authority: official` 已标（WFP VAM 一手市场调查）
- [x] `parked: true` 保留（点亮前不调度）
- [x] `site` 未写（只访问 data.humdata.org 公开 HTTPS，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] 真跑一次（2026-11-08，`window=2026-10`、`limit=100000` → **5763 行 / 16 国**，23.7s）
- [x] 修掉一处真缺陷：整月窗口的上界曾用 `YYYY-MM-00`，把当月全部排除成 0 行
      → 改为 `(下界 YYYY-MM-00, 上界 YYYY-MM-99)`
- [x] `golden/001-wfp-food-price.json` 重写并重放通过（`ok=True rows=5763`），锚真实行
      （AFG Bread 77.0 AFN / USD 1.19、IRQ `8 pcs`、SLV Wholesale、date 2026-10-15）
- [x] 口径两事实已落档：分片按「国家→日期」排序（小 `limit` = 字母序一段国家）、
      `non-food` 默认剔除
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）——**人工门，spider-heal 不点亮**
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身
