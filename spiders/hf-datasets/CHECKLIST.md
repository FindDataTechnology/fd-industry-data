# hf-datasets 接入检查清单（批次二 Wave C 直建）

- [x] 目录名 = manifest `name`（`hf-datasets`；入口 `run_hf_datasets`）
- [x] `spider.py` 提供 `run_hf_datasets(limit=N, pages=P) -> list[dict]`，只依赖标准库
      `urllib` + `json` + `re`（无新依赖）
- [x] **经代理复测通过**：`huggingface.co` HTTP 200（代理出口）；`hf-mirror.com` 直连 200 兜底
- [x] 单元代码不硬编码代理地址（urllib 默认尊重 `HTTPS_PROXY`/`HTTP_PROXY` 环境变量）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] **无 `schedule` 无 `site`**——静默合入；需代理出口，代理注入就绪前不点亮（人工门）
- [x] golden 样本 `golden/001-hf-datasets.json`：计数类**只锚结构常量**
      （`source` + `min_rows`），不锚当期计数/主机/URL
- [x] 全部主机失败抛 `RuntimeError`（失败即红，不静默空列表）
- [x] README 记数据集选择理由 + 代理要求 + 侦察证据
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py --roots .` 全绿（无工单，PR body 说明并手工验证入口行数）
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 代理注入（source-keys / egress）就绪后：人工补 `schedule` 点亮
- [ ] 点亮后试跑：看 crawl_runs 记录与产出；指标语义治理另行推进
