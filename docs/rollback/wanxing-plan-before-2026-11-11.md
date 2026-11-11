# 回滚点 · wanxing serving plan（2026-11-11）

**改动对象**：`/data/wanxing.db` 的 `wanxing_plans` 表，`user_id=1`（`admin@finddatatech.cloud`）

## 改动前实况（只读查得，逐字段）

```json
{
  "user_id": 1,
  "plan_ref": "wp_00088b829cc1",
  "email": "admin@finddatatech.cloud",
  "limit_minutes": 120,
  "note": "add-serving-plans prod smoke",
  "updated_by": "ops",
  "created_at": 1791666907555,
  "updated_at": 1791666931020
}
```
`used`（账本派生）= **120 分钟 / 91 turns**，`remaining = 0`。
逐字快照另存 `wanxing-plan-before-2026-11-11.json`。

## 为什么改

spider-heal 的 SUBMIT 全批返回 `HTTP 402 PLAN_EXHAUSTED`
（`fd-wanxing/services/wanxing-facade/core.js:465`）。额度精确用尽（120/120），
不是配置错误、不是 hollow-ok。用户裁决：「加上 agent 分钟数吧，我们现在是测试」。

## 回滚方式（一条 SQL）

额度是**单个整数字段**，`used` 由 `wanxing_usage` 账本派生（无计数器），
所以回滚＝把 `limit_minutes` 写回原值即可，下一次准入立即生效（`core.js:455 planStateFor`）：

```sql
UPDATE wanxing_plans SET limit_minutes = 120, note = 'add-serving-plans prod smoke', updated_by = 'ops', updated_at = 1791666931020
WHERE user_id = 1;
```

**不需要**动 `wanxing_usage`（账本只增不改，删记录会污染计费归属）。
**不需要**重启 pod（`planStateFor` 每次准入现查库）。

## 影响面（共享列，必须先看清）

- 该 plan 挂在 `user_id=1` = `admin@finddatatech.cloud`，是**平台管理员身份**，
  同时服务于其它 caller（spider-heal 通过该身份调用）。
- 提高 cap 只会让**更多**调用被放行，不会收回任何人的额度；不影响计费单价、不影响账本。
- 唯一风险：额度提高后测试回合数变多，可能触发平台侧其它限额（RPM/并发槽）。
