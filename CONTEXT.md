# fd-industry-data（爬虫内容仓域）

行业数据爬虫内容仓的统一语言：源单元、自愈闭环、接入路径。平台执行面（runner/调度/遥测）的行为契约见 openspec specs，此处只锁词汇。

## Language

### 接入路径（沿 unified-source-onboarding）

**静默 (Silent merge)**:
新源合入但未声明 schedule 的状态——不产生任何自动运行，Console 可见其待点亮。
_Avoid_: 禁用（那是 enabled=false，语义不同）

**点亮 (Activation)**:
为源补 schedule 提交，使其进入自动运行。点亮前必有平台试跑验证。
_Avoid_: 上线、启用

### 自愈闭环（spider-self-heal-l2）

**工单 (Health Ticket)**:
自愈闭环的最小工作单元：巡检产生、携带诊断证据、生命周期事件可追加、终结于五种终态之一（已修复待人工/样本更新待人工/转人工诊断/退役建议/排队）。
_Avoid_: issue、告警（告警是信号，工单是带诊断与终态的实体）

**分诊 (Triage)**:
按遥测证据把故障归入五类之一：网络层（换出口，不改代码）、结构层（定向修 parser）、契约层（重侦察数据表面，含反爬升级——须强制标记且不许逆向）、源死亡（退役）、兜底（整段重生成）。证据不足标人工，不猜类别。
_Avoid_: 排查（那是修复动作，不是分类）

**口径守卫 (Semantic Guard)**:
自动修复的硬门双闸：golden 重放（历史样本经新代码解析须与期望一致）+ 序列一致性（新代码重取近期窗口与库内观测比对）。拦截口径漂移；样本过期与修复失败分流处置。
_Avoid_: 回归测试（测试验证代码行为，守卫验证序列口径不变）

**golden 样本 (Golden Sample)**:
单元口径契约的载体：历史请求参数 + 期望解析结果（含白名单差异字段）。守卫的比对基准，更新本身走人工 PR。
_Avoid_: 测试夹具（它是对外口径契约，不是内部测试资产）

**修复编排 (Repair Orchestration)**:
从工单到带证据 PR 的自动路径：渲染 brief、派 agent 定向修复、跑验证链、汇出 PR。止于人工门（merge 与点亮），不越权。
_Avoid_: 自动修复（编排包含验证与留痕，不止改代码）

**巡检器 (Inspector)**:
定时只读扫描遥测并产出工单的确定性程序（GHA self-hosted job）。不是 agent、不做修复。
_Avoid_: 监控（那是持续采集，巡检是周期诊断出单）

**修复执行体 (Repair Executor)**:
执行定向修复的 agent，由壹座 Platform 承载（目录 agent + cron 自轮询工单目录），LLM 经 Platform 模型配置（后台可配）。
_Avoid_: 自研 agent 脚本、CLI worker（均为已否决选项，见 docs/adr/0001）
