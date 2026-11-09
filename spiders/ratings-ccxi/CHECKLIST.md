# ratings-ccxi 接入检查清单（批次六，2026-11-08）

- [x] 目录名 = manifest `name`（`ratings-ccxi`；入口 `run_ratings_ccxi`）
- [x] `spider.py` 提供 `run_ratings_ccxi(limit=200, keyword="银行", rating_types="", since="", until="") -> list[dict]`，纯标准库（`hmac`/`hashlib`/`urllib`）
- [x] **签名算法从前端主包实读还原**：`/js/app.835690d9.js` 的 `W()` + `V()`/`F()`/`G()`/`Y()`；
      签名串 `METHOD\npath?query\nbody\nts\nnonce`，HMAC-SHA256 → `X-Api-Sign`，
      另有 `X-Api-Version/Timestamp/Nonce`（README 贴了原文 JS）
- [x] **不猜签名**：实抓 200 验证通过；错钥实测 403 `签名校验失败`
- [x] **密钥可配置**：`CCXI_HMAC_KEY` / `CCXI_API_VERSION` / `CCXI_BASE_URL`；
      缺省回落实测前端常量，**显式空串直接报错**（不静默降级）；每次运行打印来源
- [x] 配置项在 manifest `functions[].description` 中写明（验收项"签名配置项生效"）
- [x] **三接口全覆盖**：`dqgz` 定期跟踪 / `cspj` 初始评级 / `bdqgz` 不定期跟踪，
      真实 URL 与参数见 README 表；`cspj` 改用 `orderby={"rankdate":"desc"}`
      以支持日期窗提前停止（实测被接受且严格降序）
- [x] **银行过滤**：`likes={tracename|bondname|reportname: keyword}`，服务端模糊匹配；
      实测 3,870 / 1,129 / 296 条（2026-11-08）
- [x] **ratingreport URL 去重**：同一 PDF 的多债记录合并为一行，保留
      `bond_abbreviations`（`;` 连接）与 `bond_count`；实测贵阳农商行 2 条 → 1 行
- [x] **默认口径首爬**：`limit=200` → 200 行 / 11.5s / 3 请求（dqgz 93、cspj 92、bdqgz 15）
- [x] **日期窗**：`since`/`until` 钉死 2026-09 窗口，golden 锚不随新报告漂移；
      窗口实测 6 行 / 4.6s（增量参数，README 留痕理由）
- [x] `data_period: irregular`（评级无统计期）；`schedule: "7 10 * * 1"` 按任务给定
- [x] manifest 字段齐全；columns 16 键与输出行一一对应；5 个声明参数全部可达
- [x] 失败策略：单接口失败留痕续跑、签名 403 立即失败并提示换钥、
      网络错误 3 次退避、空页/到 total/40 页兜底、缺测保留空串不补造、请求间隔 1s
- [x] PDF 直链（website-oss）免签名，只落 URL 不下载
- [x] 本地自检：`validate_manifests`（0 违规）/ `conformance_gate`（0 违规）/
      golden schema+replay（`ok=True rows=6 diffs=[]`）
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：验证 crawl_runs 记录与评级落库（首爬对标 README 200 行/11.5s）
