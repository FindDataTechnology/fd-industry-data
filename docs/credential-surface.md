# 凭据面的三层（change fix-crawl-reliability-floor）

> 定稿 2026-11-21。实操入口是 `scripts/check_credential_declarations.py`；
> 本文只回答「谁管哪一段、闸查哪个方向」。

## 三层，各管一段，互不代偿

```
声明面  spiders/*/manifest.yaml 的 requires_env
          ↓ 单元运行时读哪些键
登记面  config/credential-surface.yaml
          ↓ 谁不是单元依赖（non_unit_keys）、谁还等着补（pending_keys）
值面    secrets/fd-industry-source-keys.env（仓外，集群侧由 secret 注入）
          ↓ 键的实际取值
```

**三层之间没有任何一层能推出另一层。** 声明面说「这个单元需要 X」，不代表 X 有值；
值面说「X=abc」，不代表有单元在用它。两端的差集才是要追的东西。

## 闸查的两个方向

| 方向 | 现象 | 判定 | 修法 |
|---|---|---|---|
| 声明 ⊄ 值面 | 单元声明了 `requires_env: [X]`，值面没有 X | 红（除非在 `pending_keys`） | 去门户取号填进值面 |
| 值面 ⊄ 声明 ∪ 登记 | 值面有 X，没有任何单元声明它，也没登记为 `non_unit_keys` | 红 | 要么删掉，要么登记它为什么在这 |
| 声明 ∩ pending | X 在 `pending_keys` 且被声明 | 提示（不报红） | 人工取号后从 pending 移除 |

**`pending` 与 `exempt` 不同**：pending 是「等着补」（要跟进），exempt 是「不需要」
（不用跟）。混成一个会丢掉跟进。

## 覆盖边界（不许藏着）

本闸只断言**已声明 `requires_env` 的单元**。一个需要凭据却没声明的单元，在这条闸上
是「通过」——那是声明缺失，不是凭据齐备。

2026-11-21 实测：`declared_units 10 / lit_units 125`。也就是说 115 个亮着的单元
根本不在本闸范围内，它们缺 key 时只能在运行记录里以 `blocked-config` 看见。

产物每次都要输出这两个数。不写出来的后果是读数方以为它覆盖了全部源。

## 值与凭据在哪

- 本地：`~/finddata/secrets/fd-industry-source-keys.env`（`export KEY=...` 形态，不入 git）
- 集群：k8s secret `fd-industry-source-keys`，runner 经 `envFrom` 注入
- 4 个 `*_PASSWORD`（COMTRADE / ENTSOE / ESTAT / OCM）是注册账号密码，供人去门户换
  key；单元**不读**它们，所以登记在 `non_unit_keys`，否则会被报成孤儿键（假红）。

## 什么时候该动这三层

- **新增需要凭据的源**：manifest 写 `requires_env` → 若暂时拿不到值，加进
  `pending_keys`（带 `declared_by` 与 `action`）→ 拿到后填值面、从 pending 删。
- **源的鉴权方式变了**（如从 key= 改成 header）：改 spider、改 manifest 的
  `requires_env`，值面的键名通常不变。
- **退役一个源**：删 manifest 里那一行就会让它的键落进「值面孤儿」——如果那键还
  有用（别的源也在用），保持声明；如果真没用了，从值面删掉。
