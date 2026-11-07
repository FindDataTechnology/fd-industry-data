# Flower Auction Spider (昆明国际花卉拍卖中心 · KIFA)

**状态：parked（未点亮，从未产出数据）。** 本单元的 `schedule` 为空，`parked: true`。

## 为什么 parked — 实测证据（2026-11-08 复测）

`kifa.net` **全站**落在雷池 SafeLine WAF 的 JS 挑战后面，不是某个端点坏了：

| URL | 状态 | 关键响应头 | 正文特征 |
|---|---|---|---|
| `https://www.kifa.net/` | **HTTP/2 468** | `server: Tengine`、`set-cookie: sl-session=…` | `/.safeline/static/favicon.png`、`challenge.rivers.chaitin.cn/challenge/v2/challenge.js` |
| `/kIFAPriceIndex.do` | 同上 468 | 同上 | 同上 |
| `/breedQuotation.do` | 同上 468 | 同上 | 同上 |
| `/info.do` | 同上 468 | 同上 | 同上 |

复测方式：带完整 Chrome 请求头（UA / Accept / Accept-Language）+ `--http2` **直连**，
四个 URL 的 `content-length` 都是 `14863`，`sl-session` 每次是新值（挑战下发新会话后仍 468）。

**与旧 park 记录的差异（这条最重要）**：2026-10-04 的 dormant-unit-hygiene 记录把原因
写成「需非云出口」。本次从**非云出口**直连，仍然 468 —— 换出口不解决问题。决定性闸门是
**浏览器执行 JS 挑战**，出口只是次要条件。

## 解锁条件（三项全满足才可复活，成本 L）

1. 具备 JS 执行与浏览器指纹能力的会话（playwright / chromium 真实渲染，能跑完
   challenge v2）；`impersonate="chrome"` 这类 HTTP 层指纹伪装**已验证不够**。
2. 稳定非云出口 + 复用 `sl-session`（`Max-Age=86400`，24h）——接既有登录态池即可，
   但要能识别挑战失效（468 复现）并重新过挑战。
3. 挑战机理随雷池版本演进 → 复活后必须纳入健康级巡检（468 / 验证码双路反馈），
   否则会退化成「静默零产出」。

**不点亮的取舍**：斗南日价（`spiders/dounan-flower-price`）已覆盖同类现货价格信息，
KIFA 的增量只是「拍卖成交侧」；为这部分增量长期维护 WAF 对抗不划算。若未来要它，就按
上面三项做，并且先把它当一个**需要登录态池 + 浏览器渲染**的源立项，而不是修现有 spider。

## 这个单元里现在有什么

- `spider.py`：早期按 `/auction`、`/price`、`/market` 猜的路径写的通用爬虫，
  **这三条路径在上游并不存在**（真实入口是 `*.do` 三个），且未处理任何 WAF 挑战。
  保留它是为了留一份「当时怎么想的」证据，不是可运行的实现。
- `manifest.yaml`：`parked: true`、无 `schedule`、`concepts` 指向 `commodity.*`
  （旧模板遗产，与花卉线现役概念族无关）。

复活时建议：新写一个 `flower-auction` 实现（playwright 会话 + 挑战识别），而不是在这份
spider 上打补丁；`golden/` 需要先有真实抓取样本才能立。

## 参考

- 侦察与决策：`openspec/changes/add-flower-vertical/`（proposal §KIFA 判死实证、
  design §为什么不用 KIFA）
- 同类源替代：`docs/flower-series-curation.md`
