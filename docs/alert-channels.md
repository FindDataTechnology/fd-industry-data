# 告警通道运维手册

一条告警线往哪里送、怎么改、怎么回滚。对应 `openspec/specs/alert-delivery`，
实现最近一次落地见 `openspec/changes/relay-alert-channel`。

## 三个通道

| channel | 谁收 | 凭据 |
|---|---|---|
| `smtp` | 邮件 | `FD_ALERT_SMTP_*`（host/port/user/pass/from） |
| `webhook` | 一个 HTTP 端点（Mattermost incoming webhook / 企微群机器人 / ServerChan） | `FD_ALERT_WEBHOOK_URL` |
| `relay` | **交给一个 bot 去说** | 见下 |

`relay` 的特别之处：**同一份线路配置在两种部署下各自可用**，切换只改环境变量，
不改配置。

## relay 的两个后端

```
delivery:
  channel: relay
  to: ["alerts-slack"]        ← 不透明目标名，运维自己起
       │
       │ FD_ALERT_RELAY_URL 的路径决定协议
       ├── .../v1/send              → fd-bot   {bindingId, chatKey, text}
       └── .../api/bots/relay/send  → paas cell {channel, text} + Bearer
```

### 后端 A：paas cell（当前在用）

```yaml
- name: FD_ALERT_RELAY_URL
  value: https://platform.finddatatech.cloud/api/bots/relay/send
- name: FD_ALERT_RELAY_TOKEN
  valueFrom: {secretKeyRef: {name: platform-secrets, key: BOTS_RELAY_TOKEN}}
```

`to[0]` 直接当 cell 里的 channel 名查 `bot_channels` 表。建一条：

```bash
# 在一个 cell 的 app.db 里
INSERT INTO bot_channels (name, bot_id, chat_key, created_at)
VALUES ('alerts-slack', '<slack-bot-id>', 'C038JKMAG1H', datetime('now'));
```

**选哪个 bot**：优先 `url-keyed` 的腿（Slack / Telegram / 飞书），它们不受出口 IP 与
被动窗口约束。公众号的客服消息有 48 小时窗口（`45015`），用它当告警通道会周期性失声。

### 后端 B：fd-bot

```yaml
- name: FD_ALERT_RELAY_URL
  value: http://fd-bot.fd-bot.svc.cluster.local:8791/v1/send
- name: FD_ALERT_FD_BOT_BINDING
  value: b_mwo...
- name: FD_ALERT_FD_BOT_CHAT
  value: wrqmGucgAAB...
```

**两个内部标识走 env，不进配置**——配置里出现 `bindingId` 就是把内部寻址泄漏给写
告警规则的人。

## 怎么换一条线的通道

1. 改**真源** `config/<line>.json` 的 `delivery` 段（不是改清单！清单里是内联副本）
2. 同步内联块，然后校验：

   ```bash
   python3 scripts/check_inline_configmap_copies.py
   ```

   **它比对的是 `gitee/main`**，不是本地工作树——所以要先提交并推送。
3. `kubectl apply` 清单
4. 跑一轮真机：`kubectl create job --from=cronjob/<line> test-$(date +%H%M)`
5. **两样都要看**：
   - 产物里 `delivery.channel` 是你写的那条（不是"应该"是）
   - 群里真的收到那条消息

   只看产物会漏掉「回执对、出口断」——`alert-delivery` 就是为这件事立的。

## 回滚

把 `delivery.channel` 改回原值、`to` 改回原接收人，同步内联块，apply。
**不需要回滚代码**：`dispatch_any` 三条分支都在，改配置就能切回去。

验证回滚成功：产物里 `delivery.channel` 应为 `smtp` 或 `webhook`。

## 产物长什么样

```json
{
  "delivery": {"sent": 1, "failed": 0, "skipped": 0, "channel": "relay"},
  "verdict": {"code": "ok", "exit_code": 0, "message": "本轮 1 条告警，送达 1 条"}
}
```

`channel` 是**实际走过的通道**，不是配置里的愿望。这个字段是 2026-11-21 的回滚演练
逼出来的：此前 receipt 只记三个计数，于是「配置写 relay、实际发了邮件」看不出来。

## 告警镜像

告警 CronJob 用 `fd-industry-alerts`（44 MB），**不是** `fd-industry-runner`（860 MB）。

前者只含 `alerts_core`（第三方依赖为零，全是 stdlib）；后者含 Chromium + scrapling
整个浏览器栈——那是爬虫需要的，告警不需要。混用会让「改一行告警代码」变成
「重建 25–40 分钟的浏览器镜像」。

出片：`.github/workflows/alerts-image.yml`（47 秒）。改 `alerts_core/` 会自动触发。

## 已知边界

- **`fd-bot` 的 `/v1/send` 无鉴权**。当前只从集群内 Service 调用
  （`fd-bot.fd-bot.svc.cluster.local`），不出集群。加 Bearer 是独立后续。
- **`relay` 只发文本**（权威表 `forms: ["text"]`）。两套后端今天都没有发文件的路径。
- **`smtp` 的 `forms` 也是 `[text, link]`**，同样没有 file/chart 实现。
  这两条声明在 2026-11-21 从 `[text,link,file]` / `[text,link,chart,file]` 收敛而来——
  声明超出实现，就是另一种「声明了没兑现」。
