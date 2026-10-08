# afdc 接入检查清单（批次三直建）

- [x] 目录名 = manifest `name`（`afdc`；入口 `run_afdc`）
- [x] `spider.py` 提供 `run_afdc(limit=N, year=Y) -> list[dict]`，只依赖标准库
      `urllib` + `json`（无新依赖）
- [x] **直连复测通过**：`afdc.energy.gov/data/10332.json` 本机 + tencent 站点均
      HTTP 200（免 key、免鉴权），无需代理出口
- [x] manifest **不声明 `egress_secret`**、不硬编码代理地址（代码只访问固定 https 主机）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] **无 `schedule` 无 `site`**——静默合入，点亮留给人工门
- [x] 通道选择：**JSON**（`/data/10332.json` 图表载荷）优先于 xlsx（brief 要求）；
      `/data.json` 406、州级 map 图无载荷等坑位已记 README
- [x] golden 样本 `golden/001-afdc.json`（固定 `year=2000` 重放）：**只锚结构常量**
      （`source`/`unit`/`year`/`fuel` 码表 + `min_rows`），不锚任何计数值
- [x] 请求失败/非 JSON/缺结构/0 观测行均抛 `RuntimeError`（失败即红）
- [x] README 记端点/鉴权/信号理由/实测证据/坑位
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮（人工补 `schedule`）后试跑：看 crawl_runs 记录与产出
