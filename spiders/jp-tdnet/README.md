# spiders/jp-tdnet — TDNet 適時開示（日本版及时披露，无键）

## 这个单元解决什么

`add-restaurant-vertical` 3.4 的日股事件面。原本挂 `edinet-api`（需要 `Subscription-Key`，
须 `data-sources@` 域邮箱申请）。改用**公开无键**的 TDNet 日次列表页，
覆盖 `決算短信`、`業績予想の修正`（日本版盈利预警/预告）等及时披露。

## 数据面

| 项 | 实况（2026-11-09 实测） |
|---|---|
| 入口 | `https://www.release.tdnet.info/inbs/I_list_001_YYYYMMDD.html`（`001` = 東証） |
| 形态 | UTF-8 HTML，单页 18–22 KB，HTTP 200 |
| 表头 | `時刻 / コード / 会社名 / 表題 / XBRL / 上場取引所 / 更新履歴` |
| 代码 | **5 位**（4 位 TSE 代码 + 1 位校验位）→ 一律取前 4 位匹配 |
| 保留窗 | **约 31 天**，更早日期同一 URL 直接 404 |

## 用法

```bash
# 生产默认：餐饮宇宙 4 家（7550/7581/2702/3197），近 7 天
python -c "from spiders.jp_tdnet... " # 或经 provider 调度，见 manifest functions

# 首跑/验证：不过滤，整页保留
run_jp_tdnet(days=2, codes="")

# golden 重放（零网络）
run_jp_tdnet(fixture="spiders/jp-tdnet/golden/001-tdnet-list.html", codes="4272")
```

## 口坑

1. **5 位码**：按 4 位等值比较会永远 miss。`tdnet_code[:4]` 才是 TSE 代码。
2. **31 天保留窗**：跨财报季回溯会全空——那不是"没有披露"，是页面已不存在。
   故 golden 用**当日真实列表**做解析夹具，只锚结构不锚日期/数值。
3. **单日 404 视为该日无列表**并跳过；但**全部请求都失败**时抛 `RuntimeError`
   （绝不把"全炸"伪装成"零披露"）。
4. 同一标的同日可有多条（決算短信 + 業績予想修正 + 自己株式取得），不去重。
5. 网络走调度环境提供的 `HTTP(S)_PROXY`（`urllib` 默认读环境变量）；本单元不自带凭据。

## 与宇宙清单的关系

`docs/restaurant-universe.yaml` 的 4 家日股（`7550.T`/`7581.T`/`2702.T`/`3197.T`）
`lanes` 由 `edinet-api` 改为 `jp-tdnet`。价格/财务面走 `fd-open-data-mcp` 的
`yfinance` 源（Yahoo chart 端点，经同一代理出口），不在本单元职责内。
