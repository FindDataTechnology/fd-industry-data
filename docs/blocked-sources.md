# 上游封锁源挂账（需人工 / 登录态车道）

> 本表只记**取数被上游主动挡下**、且不是"缺 golden"就能解决的源。判据：跑真实取数后
> 拿到的行是**诚实的失败占位**（`value: null` + `_error_category`/`_error_detail`），
> 或者直接抛 `CaptchaRequiredError`。这类源**不许锚 golden**——锚了就是拿"预期失败"
> 当契约，等于假绿。

## 1. 当前挂账

| 源 | 现象（实跑原文） | 判据字段 | 处置 |
|---|---|---|---|
| `spiders/customs-xborder-retail` | `HTTP 412 疑似防护层拦截 (JSL/WAF)` | `_error_category: "upstream-protection"`；返回行 `value: null`、`indicator_name: 海关跨境电商零售进出口统计（契约层待人工）` | 走**登录态 / 出网位**车道（见 §2）：WAF 认的是会话与出口指纹，换执行位或带会话才有戏。**不得锚 golden**（锚了就是把被墙固化成验收通过） |
| `spiders/tariff-hs-rates` | `CaptchaRequiredError: 契约层待人工: 需解图片验证码 /wmsw/imgcode/preValidate；验证码图片已获取（1772 bytes），请人工/OCR 识别后填入 imgcode 再 preValidate` | 抛异常（非返回行） | 同上；该源需要**人工/OCR 解一次验证码**换取会话（`spiders/<src>/login.py` 的 `login(account)`），或改用带允许清单的出口 |

> **2026-11-09 状态订正**：两者已随提交 `ead9bb1` 进入内容仓（此前写的是「留在工作区不入仓」）。
> 当前形态是安全的：**无 `schedule`、无 `golden/`**，`crawl_sources` 里 schedule 为空，
> `crawl_runs` 中 0 次运行 —— 即「静默合入」，不会被调度、不会产生「成功但空值」的假数据。
> 真正的约束因此从「不许入仓」改成「**不许点亮、不许锚 golden**」；点亮前必须先按 §2 拿到可用会话。

## 2. 正确车道（不要自己发明）

仓库已有**认证源（登录态爬取，session-pool）**机制，见 `docs/onboarding.md`「认证源」节：

```bash
# 1) 源声明（manifest v3）：auth_profile: <名>
# 2) 登录（驱动 spiders/<src>/login.py 的 login(account)；自动或人辅助）
python3 scripts/login_session.py <src> <account_alias>
# 3) 试跑/点亮与普通源相同；dispatcher 租借身份并注入 FD_ACCOUNT / FD_SESSION_JAR_PATH
```

- 身份池五态：`login_required / active / cooldown / banned / retired`；
- 会话 Fernet 加密存 RustFS `platform-sessions`（密钥 k8s secret `platform-session-key`）；
- 失效双路反馈：runner 识别 **401 / 验证码**上报 + 产出骤降推断；
- Console `/panel/auth` 看身份矩阵与需登录队列；agent 侧走 MCP
  `auth_status` / `auth_events` / `auth_request_login`。

即：这两个源**不是数据面契约问题**，是**准入问题**——先拿到可用会话，再谈 golden。

## 3. 为什么这次单独记一笔

2026-11-09 收批次六时，`customs-xborder-retail` 与 `tariff-hs-rates` 是 34 个新单元里
唯二没有 golden 的。当时的直觉是"补个 golden 就齐了"，实跑后才发现两者都**跑不出真数据**：

- `customs-xborder-retail` 能返回 1 行，但那一行的每个数值字段都是空、`indicator_name`
  直接写着「契约层待人工」、`_error_detail` 是 WAF 412——**把这种行锚进 golden，等于
  把"被墙"固化成验收通过**，是最典型的假绿。
- `tariff-hs-rates` 连行都返回不了，抛的是验证码异常（日志里 `validateMultiCode` /
  `imgcode` 三个请求全 200，说明是该站的人机校验，不是网络问题）。

故两者**静默入仓但绝不点亮、绝不锚 golden**，在此挂账，等登录态车道接通后再收（状态见 §1 末尾的 2026-11-09 订正）。
