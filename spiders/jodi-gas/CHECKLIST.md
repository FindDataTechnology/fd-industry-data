# jodi-gas 接入检查清单（批次三直建，jodi-oil 兄弟范式）

- [x] 目录名 = manifest `name`（`jodi-gas`；入口 `run_jodi_gas`）
- [x] `spider.py` 提供 `run_jodi_gas(limit=N, year=Y) -> list[dict]`，只依赖标准库
      `urllib`/`csv`/`json`/`zipfile`/`io`（无新依赖）
- [x] **直连复测通过**：发现接口 + 下载直链本机 + tencent 站点均 200/206
      （免 key、免鉴权），无需代理出口
- [x] manifest **不声明 `egress_secret`**、不硬编码代理地址与 publicationId
      （发现接口每次解析，随上游重发自适应）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] **无 `schedule` 无 `site`**——静默合入，点亮留给人工门
- [x] 通道选择：发现接口 + `/jodi-publisher/` SDMX CSV zip（jodi-oil 同构解析）；
      直猜路径 404、jodidb.org TableViewer 与需 key 的 REST API 均不走（README 对照表）
- [x] golden 样本 `golden/001-jodi-gas.json`（固定 `year=2009` 重放）：**只锚结构常量**
      （`source`/`product`/`year`/`flow=INDPROD` + `min_rows`），不锚数值
- [x] 发现/下载/解包/解析失败、0 观测行均抛 `RuntimeError`（失败即红）
- [x] README 记端点/鉴权/信号理由/实测证据/坑位（含 jodi-oil 坑继承与排序键跨年修复）
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮（人工补 `schedule`）后试跑：看 crawl_runs 记录与产出
