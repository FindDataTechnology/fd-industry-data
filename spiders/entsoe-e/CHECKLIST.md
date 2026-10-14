# entsoe-e 接入检查清单（批次二 Wave B 收尾直建，B-c）

- [x] 目录名 = manifest `name`（`entsoe-e`；入口 `run_entsoe_e`）
- [x] `spider.py` 提供 `run_entsoe_e(limit=N, ...) -> list[dict]`，只依赖标准库
      `urllib` + `xml.etree` + `os` + `time` + `datetime`（无新依赖）
- [x] **本机实测通过**：A75（CZ 2020-01-01..07 锚定）20 行、A65 同日 10 行，
      quantity_mw/psr_type/business_type 齐全
- [x] **集群内实测通过**：探测 Job 直连 web-api.tp.entsoe.eu HTTP 200（无需代理）
- [x] **token 注入实证**：集群 Job 读 env `ENTSOE_SECURITY_TOKEN` len=36 到位
- [x] **错误路径三条实弹**：缺 env → RuntimeError；无效 token → 401 code999
      "Authentication failed."；无效 zone → 400 "No matching data found"
- [x] **凭据卫生**：token 只在请求 URL；行内 `url` 字段与全部异常文本 REDACTED
      （实测 `no-leak OK`：异常消息不含 token 明文）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] **无 `schedule` 无 `site`**——静默合入，点亮留给人工门
- [x] golden 锚固定历史期（CZ 2020-01-01..02 A75）——期次/值恒定，跨期稳定；
      规则只用 {field, equals}（守卫契约；`matches` 不受支持）
- [x] 请求失败/401/400/无 TimeSeries/零可解析点均抛 `RuntimeError`（失败即红）
- [x] README 记鉴权形态实测（query 参数唯一可用）+ 限速自限 + 历史与凭据链路
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮（人工补 `schedule`）后试跑：看 crawl_runs 记录与产出
