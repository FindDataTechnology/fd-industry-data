# 三站点发布演练报告（harden-crawl-gitops-foundation task 2.3，2026-11-06）

## 演练方式（零版本变更的渲染-发布全链）
- 从仓内 `k8s/release.yaml`（单一来源）→ `scripts/render_release.py` 渲染三消费面 → `--check` 断言一致 → `kubectl apply` 到 tencent/xinru → 下一轮 tick 验证健康
- 未引入新镜像 tag：现役镜像 `sha-d84cc82 / sha-ea21592 / sha-8f734c7` 保持不变（新 tag sha-750da3f 在 tencent 节点拉取 4 分钟未完成验证，不滚未经实拉验证的镜像到 111 个 CronJob）

## 实况对账抓到的 out-of-band 漂移（第三处实锤）
建档时取的是仓内旧文件值，与集群实况不符：
| 组件 | 建档清单 | 集群实况 | 处置 |
|---|---|---|---|
| tencent dispatcher | Harbor:sha-f0a89b3 | ccr:sha-d84cc82 | 清单校正为实况 |
| federation-observer | Harbor:sha-b9e009d | ccr:sha-ea21592 | 清单校正为实况 |
| xinru dispatcher | ccr:sha-8f734c7 | 无 k8s 对象（docker 站 systemd，manifest-only） | 保留仓内值 |
| AppSet CronJob | ccr:sha-ea21592 | 同 | 登记面保留 |

## 验证
- 渲染三消费面 → `--check` 双态全绿（RENDERED_CONSISTENT ×3）
- `diff` 渲染态 vs 集群实况：tag 逐字一致（仅换行差）
- apply：tencent dispatcher configured、xinru observer configured
- 下一轮：dispatcher Complete 1/1、observer Complete 1/1
- 负向：`--check` 漂移注入（改错一位 tag）此前已实测 FAIL

## 附带处置（演练中发现的自洽缺口）
chart 的 `--window-auto` 曾被推到 111 个 Application，但现役镜像 sha-ea21592 不认该参数 → 已回滚 chart args、全舰队硬刷新、**删掉 park 后残留的 wfp CronJob**，核验 76/76 CronJob 零 `--window-auto`。window-auto 代码/测试保留在仓，待镜像 relay 完成后重新点亮。

## 追加：真实镜像滚动（2026-11-06 晚，用户拍板「滚」）

前述零版本演练后复核发现：新 tag `ccr:sha-750da3f` 的探针其实 `Succeeded/Completed`（imageID `sha256:99a64eba…`，节点已缓存），此前只看 hkccr 域名拉取慢就误判为「未验证」。据此执行真实滚动：

1. `release.yaml` 四组件统一 bump `sha-750da3f` → 三消费面还原占位符后 `render_release.py` 渲染 → `--check` 绿 → `kubectl apply`（tencent dispatcher / xinru observer 实况已更新）
2. fd-infra-deploy AppSet 声明 image 参数同步 → `reconcile-industry-appset.sh tencent apply`（replace 带 resourceVersion）→ check OK
3. chart 重新挂 `--window-auto`（新镜像支持）→ 双仓提交推送 → AppSet 生成器 + 111 个 Application 两轮硬刷新收敛
4. 清理 park 残留的 `fd-industry-dispatch-smoke` CronJob

**终态（实弹核验）**：74/74 CronJob 全部 `ccr:sha-750da3f`；73 个带 `--window-auto`（第 74 个是手工 apply 的 dispatcher，不带该参数属设计）；镜像内代码探针 `window-auto=True / dynamic cap=True`；三源真实调度运行全 success（abs-sdmx 100 行 / eurostat-api 100 行 / openalex-works 82 行），crawl_runs 报到 `image_tag` 为新 tag；dispatcher 与 observer 下一轮 tick 均 Complete 1/1。

**结论**：本轮代码面（data_period、--window-auto、动态落库帽、education 入口修复、gate 规则、供给漏斗）全部兑现到生产运行时，零残留。
