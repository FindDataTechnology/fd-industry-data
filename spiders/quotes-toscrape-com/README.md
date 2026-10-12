# quotes-toscrape-com 接入档案（quotes.toscrape.com 公开练习站名句列表）

- 单元：`spiders/quotes-toscrape-com/`，入口 `run_quotes_toscrape_com(limit: int = 100) -> list[dict]`
- 网络：公开练习站（Scrapy 官方 demo），**直连可用，无反爬、无鉴权、无 JS 渲染**
  【实测 2026-10-12 直连 HTTP 200，静态 HTML 11 KB】
- 语种/编码：HTML（UTF-8），字段值含弯引号，原样透出
- 出单：`reports/health-tickets/20261012-wire-DE-20261013-U1.yaml`（`kind=generate`，
  wire 需求单 `DE-20261013-U1`，客户代号 `aloadtre`；brief 标注 **UI 目检 drill**）

## 端点

```
GET https://quotes.toscrape.com/          # 首页，10 条/页
GET https://quotes.toscrape.com/page/N/   # 翻页（<li class="next"> 提供下一页）
```

- 无查询参数、无鉴权（无 cookie/token）；分页为站内静态链接。
- 结构（2026-10-12 实测）：`div.quote` → `span.text` / `small.author` / `div.tags > a.tag`。

## 字段口径

| 字段 | 类型 | 口径 |
|---|---|---|
| `text` | str | 名句原文，页面渲染原样（含弯引号，不去引号、不截断） |
| `author` | str | 作者名，页面渲染原样 |
| `tags` | str | 标签逗号连接；无名句标签时为空串（**不臆造占位**） |
| `url` | str | 该行读取的页面地址（首页为常量 `https://quotes.toscrape.com/`，翻页后为 `/page/N/`） |
| `source` | str | 恒为 `quotes-toscrape-com`（常量，可断言） |
| `scraped_at` | str | 取数时刻 UTC ISO8601；`whitelist_fields` 内 |

## 坑位与容错处理

1. **golden 断言纪律**：只锚 `min_rows >= 5` 与三个常量（`source`、首页 `url`、
   首页首条作者 `Albert Einstein`——静态夹具的常量标签，与在役单 `wire-finddatatech-cloud`
   锚 `service == "fd-wire-api"` 同口径）；**绝不锚日期、`scraped_at` 或波动数值**。
2. **解析收口**：`div.quote` 块内嵌 `div.tags`（同为 `div`），用深度计数收口，
   防止把相邻名句的字段串行；只认 `span.text` / `small.author` / `a.tag` 三个锚点。
3. **分页上限**：每页 10 条，按 `limit` 翻页但**最多 5 页**（硬上限，防越界抓取）；
   同一页不重复访问（`visited` 去重），`limit<=0` 返回空列表。
4. **失败即红**：页面不可达/非 200、或解析不到任何名句块时抛 `RuntimeError`，
   让验证链的 golden 重放与真取数环判红并转人工；**绝不伪造行、不静默返回空列表**。
5. **依赖纪律**：只用标准库 `urllib` + `html.parser`（镜像自带），不引入新依赖；
   仅带常规 `User-Agent`，不做指纹伪装、不加签名头、不做频次对抗（有限重试仅容错）。
6. **不写 `schedule` / 不写 `site`**：静默合入，点亮由人工决定；本单为 drill，
   人工裁决不点亮（前例：`gen/drill-gen-healthz` PR #3、`gen/example-com` PR #59
   均按计划关闭不合并，产物归档）。

## 验证链

```
python3 scripts/health_verify.py --ticket reports/health-tickets/20261012-wire-DE-20261013-U1.yaml
```

结果：`verdict: ok`（golden 重放 1/1 通过 + 真取数 ≥1 行 + manifest 校验 0 违规 +
conformance gate PASS + 密钥扫描 0 命中），详见 PR 描述。
