# 波抽取的常驻排期与它的欠账（2026-11-19）

> change `schedule-wave-observation-extraction` 的运维留档。设计理由见该 change 的
> `design.md`；本文只记**跑起来之后要知道的事**与**还没修的那部分**。

## 1. 现在有什么

`fd-prod` 命名空间的 CronJob `wave-observation-extract`，每天 **18:10 CST**（`10 18 * * *`，
`timeZone: Asia/Shanghai`）。

正文来自 5 份 ConfigMap（`wave-extract-src-{shared,restaurant,agriculture,hotel,flower}`），
由 `fd-infra-deploy/tools/gen_wave_extract_configmap.py` 从 `fd-industry-data:gitee/main`
渲染，projected 投进 `/src`，`workingDir=/src`。

**为什么拆 5 份**：单份 55 键的对象实测 852KB，被 `kubectl apply` 的客户端路径拒收
（`metadata.annotations: Too long: may not be more than 262144 bytes`——那是
`last-applied-configuration` 注解的上限）。按包拆后每份 40–160KB。

**改抽取器后必须重跑生成器**，否则集群里跑的还是旧正文：

```bash
cd ~/finddata
python3 fd-infra-deploy/tools/gen_wave_extract_configmap.py \
    --out fd-infra-deploy/all-services/prod/wave-extract-configmap.yaml
python3 fd-infra-deploy/tools/gen_wave_extract_configmap.py --check   # 漂移会退非零
```

`check_inline_configmap_copies.py` 会按 `finddata.io/inline-sources` 逐字节比对，
所以「仓里改了、清单没重渲染」在闸上就红，不用等 pod 里出错。

## 2. 定时跑的是哪些、不是哪些

划分由 `fd-industry-data/scripts/wave_extract_split.py` **按 AST 判**：`extract()` 里
调 `unit_items()` 的读中央库 `crawl_items`，可以定时跑；调 `live_items()` 或自带
`_load_unit` 的在**抽取进程里直接打上游**，不排期。

| 包 | 定时跑 | 不跑 |
|---|---|---|
| restaurant | 全 14 个 | — |
| agriculture | `fert-cn` | `agri-casde` / `cncotton` / `moa-fishery` / `moa-hog-price` |
| hotel | `cninfo-operating` / `hkex-disclosure` / `sec-edgar-8k-operating` | `eurostat-accommodation` / `mct-star-hotel` / `mct-tourism-demand` |
| flower | `aiph-stats` / `dounan-flower-price` / `sec-edgar-facts` | `un-comtrade` / `eurostat-flower-area` |

**为什么不排 live-fetch 型**：那些单元都已在产（`agri-casde` `10 6 3 * *`、
`moa-fishery` `40 6 7 * *`、`moa-hog-price` `58 8 * * 2`、`mct-*` 月探针），
数据本来就落 `crawl_items`。在抽取 pod 里再打一遍上游，等于造一个绕开 crawl policy /
代理池 / 限速管束的影子爬虫。`live_items` 自己的注释也写着它是「单元还没部署时」的
临时手段——这些单元部署了，它是残留形态。

逐个理由写在 `LIVE_FETCH_REASONS`（`wave_extract_split.py`）里，单测双向校验：
新增一个 live-fetch 而不写理由会红；写了理由但已不是 live-fetch 也会红。

## 3. 欠账（本 change 没修的）

### 3.1 agriculture 四个单元仍是 live-fetch ⇒ CASDE 序列会继续变旧

`agri-casde` 跑的是 `live_items(run_agri_casde)`：抽取时实时打 agri.cn 取当期预测表。
它的月度预测**不会**跟着 CrawlJob 的排期自动进观测层。下游「成本链」板的玉米进口 /
结余（`agriculture.cn.corn.import.monthly` 等）会先于 DCE 那条出问题。

修法：按单元把 `live_items` 换成 `unit_items`（各单元的 payload 形状不同，是逐个的
改造，不是一行替换）。改完后 `wave_extract_split.py` 会把它判成 `crawl_items`，
重新生成清单即自动进入 `--only`——这是那套判据存在的意义。

### 3.2 `sec-edgar-facts` 抽得出来但写不进去

两个包各有一个 `sec_edgar_facts.py`（restaurant 796 行 / flower 329 行），都能产出；
但公司实体未注册，writer 按 `entity_id=None` 全部丢弃。抽取器自己的 notes 里写着
「672 条事实的公司实体未注册，未写」。

这是**真实缺口**，不是判据误报。已在 `check_observation_lag.py` 的 `KNOWN_GAPS` 里
逐行豁免（带理由与日期），修法属独立一笔。

### 3.3 平台把 `eurostat-api` 归错包

首跑日志里 `unknown series units: ['cninfo-operating', 'hkex-disclosure',
'sec-edgar-8k-operating']; known: ['eurostat-api', 'mct-star-hotel', 'mct-tourism-demand']`
——这是驱动自己的一个 name-space 提示（它按包维护了一份「已知单元」表），不影响写入。
`eurostat-api` 同时被 restaurant / hotel / flower 三个包读，属预期。

## 4. 存活判据

`fd-industry-data/scripts/check_observation_lag.py`：比较每个源的
`max(crawl_runs.started_at)` 与 `max(semantic_observations.fetched_at)`，爬取前进而
观测不动超过 2 个爬取周期就红。

**为什么不用现成的两种**：`freshness` 会把「上游停更」和「抽取器死了」混为一谈
（`cncotton` 2025-08 就停发了会常年红）；`staleness` 的参照物是本表内数据自身的
最大值，整表停摆时 lag 恒 0，是死门。

范围只含「本仓有 `crawl_items` 型抽取器」的 20 个源（`--scope all` 可看全表盘点，
但那不是门禁：153 源里 85 个「从未抽取」会让闸常年红）。

```bash
cd ~/finddata/fd-industry-data
set -a; . ~/.config/finddata/dashboards.env; set +a
python3 scripts/check_observation_lag.py            # 门禁口径
python3 scripts/check_observation_lag.py --self-test
python3 scripts/check_observation_lag.py --scope all # 盘点
```
