# jp-tdnet 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`jp-tdnet`）；入口 `run_jp_tdnet`，
      与 `run_<slug>`（连字符转下划线）契约一致
- [x] `spider.py` 只依赖标准库（`re/urllib/json`），未内联面板解析依赖
- [x] manifest 字段齐全：version/name/label/source_url/data_period/functions
      （command/frequency/parameters/columns）/entities/fetch；`columns` 11 项与输出行 11 键一一对应
- [x] `schedule` **不写**（静默合入，点亮留人工门）
- [x] `site` 未写（只访问 www.release.tdnet.info 公开页；2026-11-09 实测经代理 200）
- [x] 参数声明与入口签名一致：`limit`（int）/`days`（int）/`codes`（str）/`fixture`（str）
      全在 `run_jp_tdnet(limit=200, days=7, codes=None, fixture=None)` 签名内
- [x] **5 位码规则**：`tdnet_code[:4]` 才是 TSE 代码；4 位等值比较会永远 miss（当日实测锚
      `42720 → 4272`）
- [x] 失败即红：网络层失败 / 非法 limit / 全部页面取不到 → `RuntimeError`；
      单日 404 视为该日无列表跳过（不掩盖整站故障）
- [x] golden `golden/expected.json`（gate schema：sample_id/created/target/params/expect/
      whitelist_fields）：`min_rows=1` + 5 锚（tdnet_code/ticker/disclose_time/company/source）；
      只锚结构，不锚日期或数值（TDNet 只保留约 31 天）
- [x] 夹具 `golden/001-tdnet-list.html`（2026-11-09 真实日次列表，22 KB）
- [x] 验证：`python3 scripts/conformance_gate.py` → `PASS — 163 units, 0 violations`
- [x] 实跑：`days=2, codes=""` → `pages=2 rows=14`（真实披露行，字段齐全）
