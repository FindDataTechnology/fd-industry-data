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
  `FD_WINDOW_END`），缺省 = **以上一个完整自然月为终点的滚动 6 个月**
  （上游回填滞后，见 `docs/usaspending-awards-window-decision.md`）；
  窗口过滤先于 `limit` 截断。
- 依赖：仅标准库 + `fd_industry_data`（无新依赖）。
- `GET /api/v2/references/filter_tree/naics/` 目前 404，未使用；NAICS 取自
  每行 award 自带的 `NAICS.code`。

## 口径：这是**头部样本**，不是全量（务必按此解读）

| 列 | 含义 | 口径 |
|---|---|---|
| `obligated_amount` | 组内头部 award 金额之和（USD，2 位小数） | **部分和 / 下界**，按金额降序取头部；**不是该月该类该组的全额** |
| `award_count` | 组内去重后的 award 笔数 | 样本内笔数 |
| `page_cap_reached` | 该 (类型, 月) 批次是否被页数上限截断 | 本例几乎恒为 `true`（每月每类远超 300 笔），故**默认就应按样本读** |
| `batch_total_awards` | 该 (类型, 月) 的**全量**笔数（分母） | `sum(award_count) / batch_total_awards` = 笔数覆盖率；**`null` 表示未知，不是 0** |

实测（2026-10，类型 A）：采样 **297** 笔 / 全量 **35,031** 笔 = **笔数覆盖率 0.85%**。
样本按金额降序选，故**金额覆盖率显著高于笔数覆盖率**——但那个数**我们没测过，因此不写数字**。
要引用金额覆盖率，必须先测再写（不接受"大概是大部分"这种表述）。

**解读纪律**（与 `spiders/hf-datasets` 的 `sampled_total` 同一立场）：
- 看**趋势与相对结构**（哪个月 / 哪个 NAICS / 哪个部门的头部金额在变），**不读绝对全量**；
- 任何对外表述都称它为"头部样本和"；把 `obligated_amount` 当"该月联邦合同总额"用，
  就是拿抽样当全量；
- 只有 `page_cap_reached=false` 才代表该批是全量（当前配置下实际不会出现，
  除非上游当月该类不足 300 笔）。

接入清单见 `CHECKLIST.md`。本单元**未写 `schedule`**（静默合入）；
点亮的解锁条件见 `docs/usaspending-awards-window-decision.md`。
