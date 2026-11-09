# hsgt-northbound 接入检查清单

- [x] 目录名 = manifest `name`（`hsgt-northbound`；入口 `run_hsgt_northbound`）
- [x] `spider.py` 提供 `run_hsgt_northbound(limit=N) -> list[dict]`，只依赖标准库
      `urllib` + `json`（无新依赖）
- [x] **直连复测通过**：`query.sse.com.cn/commonSoaQuery.do`（带 Referer）与
      `www.szse.cn/api/report/ShowReport/data` 均 HTTP 200（免 key、免鉴权），
      无需代理出口
- [x] manifest **不声明 `egress_secret`**、不硬编码代理地址（代码只访问固定
      https 主机）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] **无 `schedule` 无 `site`**——静默合入，点亮留给人工门
- [x] 口径：2024-08-16 披露变更后北向只有买+卖合计成交概况——
      `buy_turnover`/`sell_turnover` 恒为 None（制度性空缺，README 论证），
      `total_turnover` = 上游亿元 × 1e8（元）
- [x] 通道选择：SSE 官方页面 JS 实走的 `commonSoaQuery.do + FW_HGTZL_HGTSCSJ_HGTCJGK_MRTJ`
      与 SZSE 官方页面 `ShowReport/data + SGT_SGTJYRB`；brief 的
      `COMMON_SSE_SJ_SHHGTLGT_JYGK_MRTJ_JYL_Y_M` 已退役（实测死路），探测清单记 README
- [x] golden 样本 `golden/001-hsgt-northbound.json`（`limit=10` 重放）：**只锚
      结构常量**（`source`/`channel` 两通道 + `min_rows=4`），不锚交易日、
      不锚任何成交额数值
- [x] 失败语义：网络/非 JSON/缺列/数值非法 → `RuntimeError`（失败即红）；
      非交易日空结果跳过；单通道不可达只出可达通道的行，两通道全空才红
- [x] manifest columns 与输出键一致（8 键）
- [x] README 记披露制度变迁/端点/鉴权/实测证据/坑位
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py` / golden replay 全绿
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮（人工补 `schedule`）后试跑：看 crawl_runs 记录与产出
