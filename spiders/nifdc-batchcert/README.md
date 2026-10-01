# nifdc-batchcert（中检院生物制品批签发公示）

中检院（中国药品检定研究院，原 NIFDC）生物制品批签发产品公示数据的爬虫单元。
入口函数：`run_nifdc_batchcert(limit: int = 100, product_name: str = "") -> list[dict]`。

## 端点与跳转口径

- 站点首页：`https://bio.nifdc.org.cn/pqf/search.do?formAction=pqfXxpt`
  → nginx 301 永久跳转到 **`https://bio.nidc.org.cn/pqf/`**（院名由「中国食品药品检定
  研究院」改为「中国药品检定研究院」，旧域名整域转发，DNS 亦指向不同 IP）。
  本单元**直连最终 host `bio.nidc.org.cn`**，README/manifest 均按此口径记录。
- 实际取数走两条 GET（免 key、无验证码、无 cookie 依赖）：
  1. 周公示汇总页：`GET https://bio.nidc.org.cn/pqf/search.do?formAction=pqfGs`
     返回每周公示链接 `search.do?formAction=listGsxq&parameter1=<id>&parameter2=<双重URL编码的签发日期区间>`
     （页面 charset=GBK；实测约 100 条链接，含同周多页/镜像 id，去重后约 39 个周窗口，
     覆盖 2025-09 至 2026-10）。
  2. 周公示明细页：`GET` 上一步取到的 listGsxq URL（**原样回发站点自带的完整 query**，
     不构造、不改写参数；页面 charset=UTF-8），内含 8 列数据表。

## 参数白名单（只发实测有效参数）

- pqfGs / listGsxq 均为 **GET，无请求体参数**。listGsxq 的 query 原样复用汇总页下发的
  `parameter1`/`parameter2`（实测 200）。
- `formAction=list1`（按品名/企业/批号检索，POST `index`/`entNameS`/`drugNameS`/`codePiS`）
  与 `formAction=list2`（按批签发证号检索，POST `index`/`codePqfCnS`）实测存在：
  - `list2` 用真实证号（如 `批签中检20262490`）能命中返回；**`list1` 服务端库当前为空**——
    任意查询（含全空查询、真实品名「人血白蛋白」/「重组乙型肝炎疫苗（汉逊酵母）」、
    真实企业「成都生物」）一律返回「搜到0条结果」，故本单元不发 `list1` 请求。
  - 「按品名取数」由入口函数的 `product_name` 参数在**已取回的公示行上做客户端子串过滤**
    实现（只过滤内存行，不追加任何站点参数）。
- 请求头仅 `User-Agent`（chrome120 impersonate 由 scrapling 附加；无 Referer/Cookie 依赖，实测无 cookie 也可取数）。

## 容错规则（spec 契约）

- `FetcherSession(impersonate="chrome120", timeout=25, verify=False, retries=1)`：
  **retries=1 = 单次尝试，不重试**；5xx / 状态非 200 / 单周解析异常 → 记 warning 后跳过该周，
  不连环重试、不做反爬绕过。
- 行级：8 列不齐、序号非数字、品名或批号为空的行视为解析失败，**留空不写脏行**；
  跨周去重键 `(cert_no, batch_no)`。
- `limit` 生效：返回行数 ≤ limit，取最新周窗口优先（周窗口按起始日期倒序）。

## 字段清单（实测表头，11 列）

站点周公示明细页实测表头为 **8 列**（brief 预估 13 列的 `list1` 检索服务端为空，按 brief
「以实测表头为准」条款落 8 列口径），加 `issue_date_window` 与契约要求的 `scraped_at`、
`source_url` 共 11 列：

| 列 | 站点表头 | 说明 |
|---|---|---|
| `seq_no` | 序号 | 周内行号 |
| `product_name` | 产品名称 | 按品名过滤即基于此列 |
| `batch_no` | 批号 | |
| `expire_date` | 有效期至 | **保留原格式**：站点不同周页混用 `2029年4月24日` / `2029-05-31` / `2029.05.12` 三种写法，未做 ISO 化（brief 允许保留原格式） |
| `license_holder` | 上市许可持有人 | 含进口持有人英文名 |
| `cert_no` | 证书编号 | 批签发证明文件号，如 `批签中检20262490` |
| `issue_conclusion` | 签发结论 | 实测窗口内均为「予以签发」 |
| `issuing_agency` | 批签发机构 | 中检院/省级药检院 |
| `issue_date_window` | （取自周公示标题） | 如 `签发日期：2026年9月14日至2026年9月20日`，即签发日期区间（站点不提供逐行签发日期） |
| `scraped_at` | — | UTC ISO 时间 |
| `source_url` | — | 该行所属周公示明细页 URL |

brief 建议中的 规格/签发量(批签发量)/收检编号/报告编号/逐行签发日期 **站点周公示页不提供**
（仅存在于已空的 `list1` 13 列检索里），无法取到，未造数。

## TLS 口径

`*.nidc.org.cn` 证书为 CFCA OV 证书（issuer=China Financial Certification Authority，
有效期 2026-09-04 ~ 2027-03-22）。macOS 系统信任库实测校验通过，但 CFCA 根不在部分
Linux 容器信任库内，按 brief 统一 **verify=False**，不因信任库差异翻车。

## 网络口径

国内黄金源：**直连**（本机未配代理、FetcherSession 未传 proxy），【直连=全部成功/代理=未用】。

## 历史深度

- 周公示汇总实测约 100 条链接、去重后约 **39 个周窗口**（2025-09-01～09-07 起，
  至取数时当周 2026-09-28～10-04 止），每周 1 页 30~90+ 行；单周窗口在汇总页可能出现
  多个链接（同窗多页/多机构），行级去重后合并取数。
- 更早历史（2016-2024）的逐批查询库（`list1`/13 列）在现网为空，暂无获取路径。

## 真实取数证据

- 取数时间：**2026-10-02 04:21~04:22（UTC+8）**，`.venv/bin/python` 实测执行
  `run_nifdc_batchcert`（scrapling 0.4.12 + lxml）。
- **查询词/条数**：
  - 无过滤 `run_nifdc_batchcert(limit=100)` → **100 行**（来自 2026-09-28～10-04、
    2026-09-21～09-27 两个周窗口的多个公示页，11 个不同产品）；
    序列**首值**=人凝血因子Ⅷ / 批号 202606026 / 批签粤检20261563（当周），
    **末值**=人凝血因子Ⅷ / 批号 2026H07015 / 批签渝检20260191（前一周）。
  - 按品名 `product_name="人血白蛋白"` → **200 行（触 limit 上限）**，持有人含
    CSL Behring、Octapharma、Grifols、Takeda 等（含进口）。
  - 按品名 `product_name="乙型肝炎疫苗"` → **10 行**（limit=10），如
    重组乙型肝炎疫苗（汉逊酵母）/202603008A/批签中检20262374。
- **样例行 JSON（limit=100 运行的疫苗样例，未加工）**：

```json
{"seq_no": "5", "product_name": "流感病毒裂解疫苗", "batch_no": "202608011A", "expire_date": "2027年8月21日", "license_holder": "武汉生物制品研究所有限责任公司", "cert_no": "批签鄂检20260486", "issue_conclusion": "予以签发", "issuing_agency": "湖北省药品监督检验研究院", "issue_date_window": "签发日期：2026年09月28日至2026年10月04日", "scraped_at": "2026-10-01T20:22:10.972915+00:00", "source_url": "https://bio.nidc.org.cn/pqf/search.do?formAction=listGsxq&parameter1=4028813a1d225be5011d22697942001a&parameter2=%25E7%25AD%25BE%25E5%258F%2591%25E6%2597%25A5%25E6%259C%259F%25EF%25BC%259A2026%25E5%25B9%25B409%25E6%259C%258828%25E6%2597%25A5%25E8%2587%25B32026%25E5%25B9%25B410%25E6%259C%258804%25E6%2597%25A5"}
```

## 遗留风险

1. `list1`（13 列按品名检索）服务端为空属于**站点侧状态**；若中检院后续回填数据，可把
   「按品名」改回服务端检索并升级 13 列 schema（表头实测仍保留在 list1 页面模板里）。
2. 周公示页 `expire_date` 三种格式混排（站点行为），下游使用时需归一化；本单元按 brief
   保留原格式。
3. 同一周窗口的多个 listGsxq 链接由站点缓存 id（parameter1）差异造成，行级去重已兜底；
   若站点未来改为标准分页，无需改采集逻辑（仍是「取全表行→去重→截 limit」）。
