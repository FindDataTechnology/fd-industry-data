# reports/health-tickets — 自愈工单目录

巡检器（`scripts/health_inspect.py`，每日 GHA workflow `health-inspect`）的产出目录。

- 工单文件名：`<YYYYMMDD>-<source>-<short_id>.yaml`，schema 见
  `fd_industry_data/health/ticket.py`（category 五类枚举 / verify 验证链声明 /
  golden 路径 / lifecycle 只追加 / terminal 终态枚举）。
- `last-run.json`：最近一次巡检的汇总（读写来源、健康源数、出单、排队与
  总闸状态），随 ticket 一起被 workflow 提交。
- 工单生命周期由 SUBMIT/STATUS 接线回填（submitted / status / terminal 事件）；
  终态 `fixed-pending-human` 与 `manual` 由人工处置后关闭。
- 本目录是萬星 spider-heal Agent Service 读取工单的路径：
  `SUBMIT FindDataTechnology/fd-industry-data reports/health-tickets/<f>.yaml`。