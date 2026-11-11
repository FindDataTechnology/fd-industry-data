# usaspending-awards

USAspending.gov 联邦合同授予的**聚合信号层**：把月度窗口内的头部合同 award
（award_type_codes A/B/C/D，按金额降序，每类型 × 每月至多 3 页 × 100 条）在
客户端按 `(award_type, naics_code, agency_name)` 聚合，产出
**月度 × NAICS 行业 × 授予部门** 粒度的 `obligated_amount` 信号行。

- 取数：`POST https://api.usaspending.gov/api/v2/search/spending_by_award/`，
  公共端点，无 key、无验证码/签名墙；robots 经
  `fd_industry_data.http.allowed_by_robots` 复核，节流复用
  `fd_industry_data.http.throttle`。
- 口径：`period` 是**聚合窗口月**（YYYY-MM），不是单笔 award 的起始日期；
  `obligated_amount` 是头部 award 的**部分和**（翻页有上限，不做全量明细）；
  `award_count` 是组内去重（Award ID）笔数。不解读 A/B/C/D 含义，原码透传。
- 窗口：`window_start`/`window_end`（kwargs 或 `FD_WINDOW_START`/
  `FD_WINDOW_END`），缺省 = 上一个完整自然月；窗口过滤先于 `limit` 截断。
- 依赖：仅标准库 + `fd_industry_data`（无新依赖）。
- `GET /api/v2/references/filter_tree/naics/` 目前 404，未使用；NAICS 取自
  每行 award 自带的 `NAICS.code`。

接入清单见 `CHECKLIST.md`（schedule 未写，静默合入后由人工门点亮）。
