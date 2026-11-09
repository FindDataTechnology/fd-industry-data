# stock-zygc 接入检查清单

- [x] 目录名 = manifest `name`（`stock-zygc`；入口 `run_stock_zygc`）
- [x] `spider.py` 提供 `run_stock_zygc(symbols="SH600000,SZ000001", limit=200) -> list[dict]`，
      只依赖标准库 `urllib` + `json`（无新依赖）
- [x] **直连复测通过**：`emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax`
      本机直连 HTTP 200（免 key、免鉴权，常规 UA），无需代理出口
- [x] manifest **不声明 `egress_secret`**、不硬编码代理地址（代码只访问固定 https 主机）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] **无 `schedule` 无 `site`**——静默合入，点亮留给人工门
- [x] 通道选择：**JSON**（`PC_HSF10` PageAjax `zygcfx`）；旧 `PC_F10` 前缀 302 软拦截、
      `BusinessAnalysisNew` 变体 302、非法代码 HTTP 200 + `status:-1` 等坑位已记 README
- [x] MAINOP_TYPE 映射 **1→industry / 2→product / 3→region**，依据为三方独立证据
      （东财 F10 前端 JS tab 定义 / akshare 同端点映射 / 茅台·平安·浦发实测分部语义），
      已推翻 brief 预写的 2=行业/3=产品/1=地区（README §2 论证）
- [x] 上游拼写 `MAIN_BUSINESS_RPOFIT`/`GROSS_RPOFIT_RATIO` 照抄不改；占比字段小数
      ×100 转百分比与 manifest `unit: percent` 自洽（README §3）
- [x] golden 样本 `golden/001-stock-zygc.json`（`{symbols:"SH600000", limit:50}` 重放）：
      **只锚结构常量**（`source`/`segment_type`/`symbol` + `min_rows`），
      绝不锚收入/占比/毛利率数值
- [x] 请求失败/非 JSON/缺 zygcfx/空列表/0 观测行/非法 symbol 均抛
      `RuntimeError`/`ValueError`（失败即红；多股时任一股失败即整体失败，错误含 symbol）
- [x] README 记端点/字段映射/MAINOP_TYPE 依据/实测证据/坑位
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py` 全绿（不新增违规）
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮（人工补 `schedule`）后试跑：看 crawl_runs 记录与产出
