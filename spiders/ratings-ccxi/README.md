# ratings-ccxi —— 中诚信国际 银行评级结果（定期跟踪 / 初始评级 / 不定期跟踪）

新源接入档案（批次六·银行业，change `add-banking-industry-sources` 任务 6.1）。

站点 `www.ccxi.com.cn` 是 Vue SPA，栏目页只渲染壳；数据接口在
`website-api.ccxi.com.cn`，**每个请求都要带 HMAC-SHA256 签名头**。签名逻辑
不在业务 chunk 里，而被打进首页主包 `/js/app.835690d9.js`（Vue 的 axios 请求
拦截器）。本单元按该主包实读还原，不是猜的。

## 三个接口（真实 URL 与参数）

全部 `GET`，免登录、免 cookie；`likes` 是服务端模糊匹配，`filters` 是等值过滤，
嵌套对象在 query 里按 `JSON.stringify` 内联（见下节签名串规则）。

| kind | URL | params（插入顺序即签名顺序） | 银行过滤字段 |
|---|---|---|---|
| `dqgz` 定期跟踪 | `https://website-api.ccxi.com.cn/admin/content/dqgz/page?codetranslate=true` | `pageNo`, `pageSize`, `orderby="rankdate"`, `filters={"launchedstatus":"启用"}`, `likes={"tracename":"银行"}` | `tracename` |
| `cspj` 初始评级 | `https://website-api.ccxi.com.cn/admin/content/cspj/page?codetranslate=true` | `pageNo`, `pageSize`, `orderby={"rankdate":"desc"}`, `filters={"launchedstatus":"启用"}`, `likes={"bondname":"银行"}` | `bondname` |
| `bdqgz` 不定期跟踪 | `https://website-api.ccxi.com.cn/admin/content/bdqgz/page` | `pageNo`, `pageSize`, `orderby={"rankdate":"desc"}`, `filters={"launchedstatus":"启用"}`, `likes={"reportname":"银行"}` | `reportname` |

响应统一是 `{"data":{"total":N,"records":[...]},"code":0,"msg":"操作成功"}`。
`pageSize=100` 实测可用（上游无 20 条上限），本单元固定 100。

前端原始调用（渲染层，取自 `chunks/chunk-47371c10.7ae85db2.js` 与 `app.js`）：

```js
// 定期跟踪（首页）
this.$http.get("/admin/content/dqgz/page?codetranslate=true",
  {pageNo:1, pageSize:3, orderby:"rankdate",
   filters:{levelone:"102", leveltwo:"104", launchedstatus:"启用"}})

// 初始评级（评级结果页 Type1/Type2）
t.orderby={bondissuancedate:"desc"}; t.filters={launchedstatus:"启用", levelone:..., leveltwo:...};
this.$http.get("/admin/content/cspj/page?codetranslate=true", t)

// 不定期跟踪（评级结果页 Type4）
t.likes={reportname:this.keywords.trim()}, t.orderby={rankdate:"desc"};
this.$http.get("/admin/content/bdqgz/page", t)
```

两处与本单元的差异，均实测确认：

1. **`cspj` 前端按 `bondissuancedate` 排，本单元按 `rankdate` 排**。实测
   `orderby={"rankdate":"desc"}` 被接受，首三条 `rankdate` 为 2026-10-16 /
   2026-10-14 / 2026-10-08（严格降序），故可用"遇到整页更旧即停"的日期窗口
   提前退出；按发行日排则做不到，会为历史窗口扫全表。
2. **`filters` 只保留 `launchedstatus:"启用"`**，不再叠加 `levelone/leveltwo`。
   叠加会把银行债券按券种拆散：实测 `bdqgz` 加 `levelone=102&leveltwo=103`
   后银行条数从 296 掉到 241；`cspj` 若限 `leveltwo=75`（金融机构评级）会漏掉
   归类在其他层的银行相关债券。层级信息仍原样保留在 `classification` 列，
   需要按券种收窄时在下游过滤即可。

## 签名算法还原

### 1) 定位签名实现

首页 HTML 只有 `<script src="/js/chunk-vendors…">` 与 `/js/app.835690d9.js`。
在 `app.js` 里搜 `website-api` 即命中 axios 实例，往上一屏就是签名函数：

```js
var K=W,J="https://website-api.ccxi.com.cn";
const X={baseURL:J,timeout:2e4},Z=P.a.create(X);
Z.interceptors.request.use((function(e){const t=localStorage.getItem("token");
  return t&&(e.headers.Authorization="Bearer "+t),K(e),e}), ...)
```

### 2) 签名函数 `W` 与它依赖的常量/工具（原文）

```js
const I="v1",H="df464cdfcf2974a621abaeda20a2d1d4c7d3bb969f61aaee11d4fbe78fd859c2",q=[/* SHA-256 K 表 */];

function L(e){ /* SHA-256 → 64 位 hex 字符串 */ }
function B(e){ /* JS 字符串 → UTF-8 字节数组 */ }
function U(e){ /* hex → 字节数组 */ }
function Y(e){ /* crypto.getRandomValues 生成 e/2 字节 → 2e 位 hex；e=16 即 16 位 nonce */ }
function G(e,t){ /* HMAC-SHA256：B(e) 为 key，B(t) 为 message，返回 hex */ }

function F(e){return encodeURIComponent(e).replace(/%3A/gi,":").replace(/%24/g,"$")
  .replace(/%2C/gi,",").replace(/%20/g,"+").replace(/%5B/gi,"[").replace(/%5D/gi,"]")}

function V(e){const t=[];return Object.keys(e).forEach((function(a){let n=e[a];
  if(null!==n&&"undefined"!==typeof n)if(Array.isArray(n))
    for(let e=0;e<n.length;e++)t.push(F(a)+"[]="+F(n[e]));
  else"object"===typeof n&&(n=JSON.stringify(n)),t.push(F(a)+"="+F(n))})),t.join("&")}

function W(e){
  const t=Date.now().toString(), a=Y(16), n=(e.method||"get").toUpperCase();
  let i=e.url||"";
  if(e.params&&"object"===typeof e.params){const t=V(e.params);
    t&&(i=i+(i.indexOf("?")>=0?"&":"?")+t)}
  let s="";
  void 0!==e.data&&null!==e.data&&(s="undefined"!==typeof FormData&&e.data instanceof FormData
    ?"":"string"===typeof e.data?e.data:JSON.stringify(e.data));
  const r=n+"\n"+i+"\n"+s+"\n"+t+"\n"+a, o=G(H,r);
  e.headers["X-Api-Version"]=I; e.headers["X-Api-Timestamp"]=t;
  e.headers["X-Api-Nonce"]=a;   e.headers["X-Api-Sign"]=o;
}
```

### 3) 还原结论（实现要点）

签名串是五行、`\n` 连接，**GET 的 body 段是空串**：

```
GET\n<path?query>\n\n<timestamp_ms>\n<nonce>
sign = HMAC-SHA256(key=H 的 UTF-8 字节, msg=上面的串).hexdigest()
```

- `X-Api-Version: v1`、`X-Api-Timestamp: <毫秒>`、`X-Api-Nonce: <16 位 hex>`、
  `X-Api-Sign: <64 位 hex>`；
- `path?query` 必须与**实际发送的 URL 完全一致**：`params` 里嵌套对象
  （`orderby`/`filters`/`likes`）先 `JSON.stringify` 成紧凑 JSON（中文不转义），
  再做 `encodeURIComponent` 并还原 `: $ , 空格→+ [ ]` 六处；
- 因此 `spider.py` 用同一份 `params` 同时生成"要发的 query"和"要签的串"，
  不存在两处拼接不一致的可能；数组会展开成 `key[]=v`（本单元未用数组）；
- 毫秒时间戳没有窗口校验的实测迹象（同一 ts 多次请求均 200），nonce 只需 16 位 hex。

实测握手（2026-11-08）：

```
GET /admin/content/dqgz/page?codetranslate=true&pageNo=1&pageSize=3
    &orderby=rankdate&filters={"launchedstatus":"启用","levelone":"102","leveltwo":"104"}
    &likes={"tracename":"银行"}
→ HTTP 200, application/json, total=3870
```

### 4) 密钥来源与配置项

密钥就是 `app.js` 里的常量 `H`（上面已贴原文），版本是常量 `I="v1"`：

```
$ curl -s https://www.ccxi.com.cn/ | grep -o '/js/app[^"]*\.js'      # → /js/app.835690d9.js
$ curl -s https://www.ccxi.com.cn/js/app.835690d9.js | grep -o 'H="[0-9a-f]\{64\}"'
H="df464cdfcf2974a621abaeda20a2d1d4c7d3bb969f61aaee11d4fbe78fd859c2"
```

**可配置实现（`spider.py::_credentials`）**：

| 环境变量 | 缺省 | 行为 |
|---|---|---|
| `CCXI_HMAC_KEY` | 内置实测值 `H`（公开前端常量） | 设置则用环境变量；**设置为空串直接 `RuntimeError`**，不静默回落 |
| `CCXI_API_VERSION` | `v1` | 同上（空串报错） |
| `CCXI_BASE_URL` | `https://website-api.ccxi.com.cn` | 仅路径不同域名时使用（签名只覆盖 path，不覆盖 host） |

**为什么选"内置默认值 + 环境变量覆盖"而不是"无 env 即报错"**：这把钥是
**公开前端包里的常量**，不是用户私密凭据——没有 env 就报错会让 golden 重放、
健康巡检、离线导入全部无法运行，而它本身并不提供任何保密性。与此同时上游
换钥（重新构建前端包）的频率不可控，所以把"换钥"做成纯配置动作：改
`CCXI_HMAC_KEY` 即生效，不动代码。空串报错是为了区分"没配置"与"配置注入失败"。

每次运行会打印一行来源，便于确认配置项真的生效：

```
[ratings-ccxi] 签名配置：key=<环境变量 CCXI_HMAC_KEY> version=v1
```

配置项生效实测（2026-11-08）：

| 场景 | 结果 |
|---|---|
| 未设 env（回落内置钥） | HTTP 200，出数 |
| `CCXI_HMAC_KEY=deadbeef` | HTTP 403 `{"code":403,"msg":"签名校验失败"}` → `RuntimeError` 并提示更新 `CCXI_HMAC_KEY/CCXI_API_VERSION` |
| `CCXI_HMAC_KEY=`（空串） | `RuntimeError: CCXI_HMAC_KEY 已设置但为空…`（不发请求） |

用错钥时上游明确回 403，说明签名确实被服务端校验，不是摆设。

## 银行过滤规则

`likes` 由服务端做模糊包含匹配，本单元 `keyword="银行"`（默认）分别作用于：

- `dqgz` → `tracename`（如 `2022年贵阳农村商业银行股份有限公司二级资本债券跟踪评级`）；
- `cspj` → `bondname`（如 `湖南银行股份有限公司2026年第一期绿色金融债券`）；
- `bdqgz` → `reportname`（公告标题，如 `…延迟披露河南新郑农村商业银行股份有限公司…的公告`）。

这三个接口都没有独立的"行业"字段（`industry` 实测为空串），所以银行口径就是
**名称包含关键词**，不做二次猜测。`bdqgz` 的 `issuers` 上游为 `null`，因此该接口
的 `issuer` 列为空、银行名只出现在 `report_title` 里——这是上游形态，不补造。

实测条数（2026-11-08，`launchedstatus=启用`）：

| 接口 | 银行条数（`likes=银行`） |
|---|---|
| dqgz | 3,870 |
| cspj | 1,129 |
| bdqgz | 296 |

侦察簿记的 dqgz 3,983 是更早一次观测，与本次 3,870 的差异来自"启用"过滤与
上游记录增删，属正常漂移，不是接口变化。

## URL 去重规则

上游对**同一份报告按所覆盖的每只债各存一条记录**。实测（`pageSize=5`）：

```
id=140282  22贵阳农商行二级资本债01  → 20260930-…-Eq6639-…2026年度跟踪评级报告.pdf
id=140281  21贵阳农商二级            → 同一个 PDF（URL 完全相同）
```

去重键是 `pdf_url`（`ratingreport`，`cspj` 用 `bondreport`；两者都为空时退回
`report_type:info_id`），跨三个接口全局去重。合并时保留：

- 最新一条（列表按 `rankdate` 降序，首条即最新）的等级/展望/日期；
- `bond_abbreviations`：被合并的债券简称用 `;` 连接（上例得
  `22贵阳农商行二级资本债01;21贵阳农商二级`）；
- `bond_count`：合并条数（上例为 2），空字段用后续记录补齐。

## 实测取数证据

| 口径 | 行数 | 耗时 | 请求数 |
|---|---|---|---|
| 默认 `limit=200`（无日期窗） | **200**（dqgz 93 / cspj 92 / bdqgz 15） | **11.5s** | 3 |
| golden 窗口 `since=2026-09-01, until=2026-09-30` | 6（dqgz 1 / cspj 4 / bdqgz 1） | 4.6s | 3 |

默认口径首行样例：

```json
{"report_type":"cspj","report_type_cn":"初始评级",
 "issuer":"中国邮政储蓄银行股份有限公司",
 "report_title":"中国邮政储蓄银行股份有限公司2026年无固定期限资本债券(第二期)(债券通)",
 "subject_rating":"AAA","debt_rating":"AAA","outlook":"稳定","rating_date":"2026-10-16",
 "pdf_url":"https://website-oss.ccxi.com.cn/zhongchengxin//file/20261103-1793697338826-8kjY52-[[originalname]]-中国邮政储蓄银行股份有限公司2026年无固定期限资本债券（第二期）（债券通）信用评级报告.pdf",
 "subject_report_url":"","info_id":287438,
 "bond_abbreviations":"26邮储银行永续债02BC","bond_count":1,
 "classification":"初始评级/金融机构评级/普通金融债",
 "source":"ratings-ccxi","scraped_at":"2026-11-08T00:37:36+08:00"}
```

PDF 直链在 `website-oss.ccxi.com.cn`，**自身免签名**（直接 curl 可取），
本单元只落 URL，不下载 PDF。

## 失败策略

- 单接口失败（网络/解析）→ 留痕跳过，其余接口照常出数；三接口全失败 → `RuntimeError`。
- 签名被拒（401/403）→ **立即 `RuntimeError`**，不做无谓重试：这是密钥/版本
  换版信号，重试只会继续 403，并提示改 `CCXI_HMAC_KEY`/`CCXI_API_VERSION`。
- 普通网络错误 → 每请求最多 3 次（退避 2s/4s），仍失败则记为该接口失败。
- 空页 / 不满一页 / 已到 `total` → 结束该接口翻页；单接口最多 40 页兜底，
  防上游分页异常死循环。
- 请求间隔 1s（网络礼节）；`pageSize=100` 是实测值，不试探更大值。
- 空值（等级/展望/日期缺失、`bdqgz` 的 `issuers=null`）→ 保留空串，不补造。

## 为什么 golden 钉"闭区间日期窗"

默认口径取最新 N 行，会随新报告发布不断滚动，任何最新的锚都会失效（这与
`chinabond-monthly` 用 `period` 钉月份是同一类问题）。因此入口加了
`since` / `until` 两个可选参数，golden 用 `since=2026-09-01, until=2026-09-30`
把窗口固定成 2026 年 9 月：

- 窗口内的历史报告不会因未来发布而改变，锚值长期成立；
- 按 `rankdate` 降序翻页，遇到更旧整页即停，9 月窗口只需 3 个请求（4.6s）；
- 默认行为不变（两个参数都为空 = 最新 N 行），调度口径与 spec 一致。

这是相对 tasks.md 原文的**增量**（可选参数），不是收窄：三接口、银行过滤、
URL 去重、签名可配置全部按原文实现。

锚的选择（全部实抓于 2026-11-08）：

- 主体条目：dqgz `贵阳农村商业银行股份有限公司` 2026 年度跟踪评级 —
  主体 `AA-` / 债项 `A+` / 展望 `稳定` / 日期 `2026-09-29` / PDF URL 原值；
  且 `bond_count=2`，一行同时证明"按 URL 去重"生效；
- 另加 `report_type=cspj`、`report_type=bdqgz` 两条规则，证明三接口都真的出数；
- `min_rows=5`（实测 6）留 1 行容错，避免上游下架一条公告就把 golden 打成假失败。

## data_period 与排期

`data_period: irregular`：评级结果没有统计期，按发行人披露日零散发布；不定期
跟踪公告（bdqgz）更是无规律。用 `day` 会让超期判定把"今天没发新评级"误报成
缺数，故取 `irregular`。排期 `7 10 * * 1`（每周一 10:07）按任务给定值执行。

## 配置项用法

```bash
# 默认：内置实测密钥 + v1
python3 -c "from fd_industry_data.health import guard; \
  print(len(guard.load_unit_entry('spiders/ratings-ccxi','run_ratings_ccxi')(limit=200)))"

# 上游换钥后：只改环境变量，代码不动
export CCXI_HMAC_KEY=<新版 app.js 里的 H 常量>
export CCXI_API_VERSION=v1
```

参数：`limit`（行数上限，按接口均分预算）、`keyword`（银行关键词，空串=不过滤）、
`rating_types`（`dqgz,cspj,bdqgz` 子集，空=全部）、`since`/`until`
（`YYYY-MM-DD` 闭区间，空=不限）。
