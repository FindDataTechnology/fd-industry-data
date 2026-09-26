# 新数据源接入（平台路径）

> 本文替代旧的「宏观十步接入配方」。镜像前置步骤已随 gitops-crawl-runtime 消失；
> registry 治理（列→绑定→verified→标识符→series 波次）仍是概念线的独立后续，
> 不再阻塞爬取上线。契约见 openspec `unified-source-onboarding`。

## 三步主路径

```
建目录 → gate 绿 → 点亮
```

1. **建目录**：`cp -r templates/new-source spiders/<my-source>/`，改 manifest 与
   `run_<name>`，照 `templates/new-source/CHECKLIST.md` 逐项勾。schedule 留空 =
   静默合入。
2. **gate 绿**：push 触发 Jenkins `fd-industry-runner`（gate 阶段在镜像同构环境跑
   validate_manifests + conformance gate + 全源 import 扫描）；本地可先跑：
   `python3 scripts/validate_manifests.py && python3 scripts/conformance_gate.py --roots .`
3. **点亮**：Console/`platform_trigger` 先试跑一次看产出，再补 `schedule` 提交。
   ApplicationSet ~3 分钟渲染出该源 CronJob；dispatcher 每 5 分钟把 manifest 镜像进
   `crawl_sources`（Console 源清单的数据来源）。

## 老配方 → 新路径对照

| 旧十步（宏观配方） | 现在 |
|---|---|
| 0. 镜像前置（先构建分发新镜像再动 DB） | **已删除**——内容走 git，镜像低频发版 |
| 1–5. 列/绑定/verified/标识符 | 概念线后续治理（unified-indicator-registry），可在点亮后独立做 |
| 6. 适配器 | 行业源不需要；概念线仍在 fd-open-data-mcp |
| 7–8. 波次/重启 | 行业源用 manifest schedule；概念线 policy 不变 |
| 9–10. 验证与坑 | trigger-now 试跑 + crawl_runs 遥测，所见即所得 |

## 特殊源与联邦成员

- 无法归一为 `spiders/<src>/` 的特殊 Job（wenshu 等）：运行建制不动，以最小 helper
  向 `crawl_runs` 报到（`scripts/report_special_run.sh`）+ `crawl_sources` 登记，
  Console 即可见。
- scrapyd 线（law/concept）：联邦成员，报到即可，不迁移。
- 触发例外（立即跑/回填/临时改频）：Console 或 `platform_trigger` 写
  `pending_runs`，目标站点 dispatcher 认领执行——永不直达执行节点。

## 从 harness 生成源（fd-scraw-harness → 落仓通道）

发现工作台的产出可一键落仓，替代手工复制+对齐：

```
harness discover/analyze/generate/approve
        |
        v  export_manifest  (v2 对齐：version=2、无调度字段、附 spider 路径/内容)
        v
python3 scripts/land_source.py <manifest.yaml> <spider.py>
        |  本地三件检查（validate_manifests / conformance gate / py_compile）
        v  全绿才 commit，红则原子拒绝（不留半成品）
git push  ->  Jenkins gate  ->  Console 待点亮可见
        |
        v  platform_trigger 试跑（看 crawl_runs 产出）
        v  补 schedule 提交 = 点亮（独立提交，人控节奏）
```

要点：落仓用工作区既有 git 凭据（平台组件保持只读）；落仓永远不点亮
（schedule 会被剥除并提示）；spider 必须带 `run_<src>(limit)` 入口（缺即拒，
模板见 `templates/new-source/`）。契约由内容仓单方裁决，harness 只是适配。
## 认证源（登录态爬取，session-pool）

需要登录的源走身份池：manifest 加 `auth_profile: <名>`，然后为每个账号建身份并登录：

```bash
# 1) 源声明（manifest v3）：auth_profile: rmfyalk
# 2) 登录（驱动源仓 spiders/<src>/login.py 的 login(account) 单元；自动或人辅助）
python3 scripts/login_session.py <src> <account_alias>
# 3) 试跑/点亮与普通源相同；dispatcher 会租借身份并注入 FD_ACCOUNT/FD_SESSION_JAR_PATH
```

要点：每源多账号（五态状态机：login_required/active/cooldown/banned/retired）；会话
Fernet 加密存 RustFS platform-sessions bucket（密钥 k8s secret platform-session-key）；
租借是表语义（同身份不并发、TTL 回收）；失效双路反馈（runner 识别 401/验证码上报 +
产出骤降推断）；Console `/panel/auth` 看身份矩阵/需登录队列/事件流，MCP `auth_status/
auth_events/auth_request_login` 供 agent 操作。登录单元自动化程度按源声明（rmfyalk=人辅助
OAuth；auth-smoke=自动测试单元模板）。
### Console 内登录（login-station-console，默认路径）

上述第 2 步的登录现在默认在 Console 完成：`/panel/auth` 面板「新建账号」（自动分配独立
出口 proxy）→ 对 login_required 身份点「登录」→ 页内观察窗（集群登录站 headful 浏览器，
经 panel 反代、零公网暴露）→ 人辅助滑块/全自动单元完成 → 会话入池、身份 active → 后续
爬取自动租借该身份并走同一出口。MCP `auth_launch_login` 可代拉起。需要操作员家 IP 的源
（如 wenshu）仍走本机 `scripts/login_session.py`（面板标注不提供站内登录）。
