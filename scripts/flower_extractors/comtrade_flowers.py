#!/usr/bin/env python3
"""UN Comtrade 花卉三档 — 花卉垂直线抽取器（add-flower-vertical 3.2）。

真源：`spiders/un-comtrade`（UN Comtrade 年度 HS6 贸易流，reporter=156 中国，
partner=0 世界）。三个花卉档位 + 两个流向 = 6 个组合：

| HS6 | 含义 | 出口 2023（实测） | 进口 2023（实测） |
|---|---|---|---|
| 060311 | 玫瑰切花 | 17,283,341 美元 | 5,876,523 美元 |
| 060319 | 其他鲜切花 | 39,250,772 美元 | 42,701,244 美元 |
| 060120 | 鳞茎/块茎/球根（休眠/生长中/开花株） | 19,371 美元 | 23,927 美元 |

口径纪律：

1. **三档恒不合并**：一档一码（`trade.export_flow.world` / `trade.import_flow.world`
   × HS 实体），合并会掩盖品类结构；0602 活体观赏植物另属一组，不入本板。
2. **只取 partner=0（world）**：伙伴国轴在既有 TradeFlow 概念里已是「一序列一码」，
   本波不展开伙伴国明细。
3. **口径闸**：回包里 `cmdCode`/`flowCode`/`partnerCode`/`reporterCode` 必须与请求
   完全一致，否则跳过并记 note——上游串档会让「玫瑰」变「其他切花」，这比缺数更坏。
4. **缺失年份不补 0**：上游当年无申报就没有行；贸易额 0 与「未申报」是两件事。

## 与 3.3 Eurostat 腿的差别（重要）

Eurostat 是本轮真跑通的；**本条只做了单元级验证**：解析/映射用真实回包 fixture
（`tests/fixtures/comtrade_flowers_2023.json`，2026-11-08 取自官方免 key 预览端点，
数值与年度定稿值一致）钉住，**但取数路径本身需要 `COMTRADE_API_KEY`**，本机未配置
——所以入库这一步还没跑过。别把「测试绿」读成「已入库」。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]          # fd-industry-data/
_UNIT = _ROOT / "spiders" / "un-comtrade" / "spider.py"

if str(_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "scripts"))

SOURCE = "un-comtrade"
REPORTER = 156        # 中国
PARTNER = 0           # world
YEARS_BACK = 10       # 滚动 10 个完整年度（够做同比与趋势）

#: 三个以上板档位（HS6 → 中文名）
HS = (
    ("060311", "玫瑰切花"),
    ("060319", "其他鲜切花"),
    ("060120", "鳞茎球根"),
)

CODE_EXPORT = "trade.export_flow.world"
CODE_IMPORT = "trade.import_flow.world"

#: 概念五元组：(code, name_zh, name_en, category, unit, measure, frequency,
#: entity_type, source)。export 已存在（TradeFlow 族），import 本波新增。
CONCEPTS: list[tuple] = [
    (
        CODE_EXPORT,
        "中国对全球出口额",
        "China exports to world",
        "贸易规模",
        "美元",
        "export",
        "yearly",
        "hs_code",
        SOURCE,
    ),
    (
        CODE_IMPORT,
        "中国自全球进口额",
        "China imports from world",
        "贸易规模",
        "美元",
        "import",
        "yearly",
        "hs_code",
        SOURCE,
    ),
]


def _load_unit():
    """按路径加载 ``spiders/un-comtrade/spider.py``（目录名带连字符，不可 import）。"""
    name = "unit_un_comtrade_flower"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _UNIT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _entity_ids(cur, codes: set[str]) -> dict[str, int]:
    if not codes:
        return {}
    cur.execute(
        "select code, id from entities where entity_type = 'hs_code' and code = any(%s)",
        (sorted(codes),))
    return {code: eid for code, eid in cur.fetchall()}


def _pick(row: dict, *names):
    """按优先级取字段：爬虫输出 snake_case，免 key 预览回包是 camelCase。

    两条路必须都能读——测试 fixture 保真保存的是官方预览回包的 camelCase，
    而生产走单元入口拿到的是 spider 规范化后的 snake_case（2026-11-08 实测：
    只认 camelCase 会把真数据全判成「口径不一致」，125 行一条不剩）。
    """
    for name in names:
        if name in row:
            return row[name]
    return None


def map_rows(rows, entity_ids: dict[str, int], notes: list[str] | None = None,
             *, expected: set[tuple[str, str]] | None = None,
             year_min: int | None = None) -> list[tuple]:
    """把单元回包映射成观测行。**纯函数**，便于用真实 fixture 钉口径。

    ``expected`` = 本次请求允许的 ``(cmdCode, flowCode)`` 组合集合。串档检测必须
    按**请求**判：只校验「字段是否属于三档之一」的话，请求 060311 却回 060319
    会被当成合法行写进去（第一版就是这样，被测试抓出来的）。

    返回 ``(concept, entity_type, entity_id, date, granularity, value)``。
    """
    notes = notes if notes is not None else []
    wanted = {code for code, _zh in HS}
    out: list[tuple] = []
    rejected = 0
    for row in rows:
        cmd = str(_pick(row, "cmd_code", "cmdCode") or "")
        flow = str(_pick(row, "flow_code", "flowCode") or "")
        # 不截断：季度/月度期次（如 "2023-Q1"）必须被拒，截成 "2023" 会冒充年度值
        period = str(_pick(row, "period") or "")
        value = _pick(row, "primary_value", "primaryValue")
        # 口径闸：回包必须与**本次请求**逐字一致，串档比缺数更坏
        if cmd not in wanted or flow not in ("X", "M"):
            rejected += 1
            continue
        if expected is not None and (cmd, flow) not in expected:
            rejected += 1
            continue
        partner = _pick(row, "partner_code", "partnerCode")
        reporter = _pick(row, "reporter_code", "reporterCode")
        partner2 = _pick(row, "partner2_code", "partner2Code")
        if (partner not in (PARTNER, None, str(PARTNER))
                or reporter not in (REPORTER, None, str(REPORTER))
                # partner2 = 「第二伙伴国」（转口来源国）。2016/2017 上游把它的明细行
                # 一并返回（partner2=344/458/682…），其中 partner2=0 那行才是世界合计；
                # 只写「第一条」会写进某条明细（实测 2016 被写成 44.5 万，真实 940 万）。
                or partner2 not in (0, None, "0")):
            rejected += 1
            continue
        if len(period) != 4 or not period.isdigit() or value is None:
            rejected += 1
            continue
        if year_min is not None and int(period) < year_min:
            continue
        eid = entity_ids.get(cmd)
        if eid is None:
            continue
        concept = CODE_EXPORT if flow == "X" else CODE_IMPORT
        out.append((concept, "hs_code", eid, f"{period}-01-01", "year",
                    round(float(value), 2)))
    if rejected:
        notes.append(
            f"un-comtrade: 跳过 {rejected} 行与请求口径不一致的回包"
            f"（cmdCode/flowCode/partnerCode/reporterCode 任一不符即拒）")
    # 缺失只能按**本次请求**判：extract() 是逐组合调用 map_rows 的，用「三档全集」
    # 去算缺失会让每个单组合调用都谎报另外两档「零行」（2026-11-08 实跑噪音）。
    if expected is None:
        missing = wanted - {str(_pick(r, "cmd_code", "cmdCode")) for r in rows}
        if missing:
            notes.append(f"un-comtrade: 档位 {sorted(missing)} 本次零行（上游未申报，不补 0）")
    return out


def extract(cur, notes: list[str]) -> list[tuple]:
    """6 个组合（3 档 × 出口/进口）逐个取数并映射成观测行。

    取数走单元入口（生产端点，需 ``COMTRADE_API_KEY``）。任一组失败即抛——
    半个贸易板比空板更容易误读。
    """
    # 未配置凭证 ≠ 数据为零：明确跳过并留 note（不能落成 0 行，也不能炸掉整条
    # 花卉抽取批次——同一批次里还有斗南/AIPH/SEC 三个能跑腿）。
    # key 配好后真实取数失败仍然抛错（见文件末尾的 got_any 断言）。
    if not (os.environ.get("COMTRADE_API_KEY") or os.environ.get("COMTRADE_SUBSCRIPTION_KEY")):
        notes.append(
            "un-comtrade: 未配置 COMTRADE_API_KEY/COMTRADE_SUBSCRIPTION_KEY，"
            "花卉贸易本次跳过（不是「贸易额为 0」，也不是「上游没数据」）")
        return []

    unit = _load_unit()
    ids = _entity_ids(cur, {code for code, _zh in HS})
    unregistered = sorted({code for code, _zh in HS} - set(ids))
    if unregistered:
        notes.append(
            f"un-comtrade: HS 实体未注册，跳过 {unregistered}"
            "（先跑 scripts/register_flower_entities.py --apply）")

    out: list[tuple] = []
    got_any = False
    for code, _zh in HS:
        for flow in ("X", "M"):
            got = unit.run_un_comtrade(
                limit=200, reporter_code=REPORTER, partner_code=PARTNER,
                cmd_code=code, flow_code=flow, years_back=YEARS_BACK,
            )
            if not got:
                notes.append(f"un-comtrade: {code} {flow} 回包零行")
                continue
            got_any = True
            # 每个组合单独过口径闸：期望值就是本次请求的那一对
            out.extend(map_rows(got, ids, notes, expected={(code, flow)}))

    if not got_any:
        raise RuntimeError(
            "un-comtrade flower: 6 个组合全部零行 —— 检查 COMTRADE_API_KEY 是否注入"
            "（缺 key 时端点会拒绝，不应把它落成 0 行）")
    return out
