#!/usr/bin/env python3
"""Task 8.2 / 8.3 — 农业监控宇宙注册脚本 + 失败即拒自检。

宇宙真源 = ``docs/agriculture-universe.yaml``（当前尚未提供：8.1 因 segment 无
真源而阻塞，见 openspec/changes/add-agriculture-industry-sources/tasks.md 8.1）。
本脚本按输入契约先行落地，yaml 由主 agent / 8.1 提供后直接可跑。

成分真源（不是手写名单）
------------------------
``select e.code from entity_relationships r join entities e on e.id = r.source_id
where r.target_id = 94`` —— entity 94 = 申万一级 801010「农林牧渔」，
``metadata_json={'sw_l1': true, 'classification_system': 'shenwan'}``，104 只 stock。
yaml 的 ``source.constituents`` 可改写 target_id；缺省即 94。

存储落点（照既有 register_{hotel,restaurant,flower}_universe.py，不新造表）
-------------------------------------------------------------------------
* ``segment`` / ``region`` / ``lane`` / ``caliber`` / ``universe_id`` / ``evidence``
  写进 **entities.metadata_json**（MCP 与取数层读的查询面）；
* 跨源标识写进 **entity_source_identifiers**：``source='agriculture-universe'``
  （宇宙成员）与 ``source='exchange-ticker:<exchange>'``（交易所原生代码回指车道）；
* **绝不新建实体**：本宇宙 104 只全部是既有 ``entity_type='stock'`` 行；找不到即拒，
  不 INSERT entities，也不改 name_zh/name_en（改名只报错，不静默覆盖）。

失败即拒（--apply / 默认 dry-run 都先过这一关，一次列全所有违规）
----------------------------------------------------------------
1. 总数 != ``count_expected``；
2. yaml code 集合 != 库内真源集合（多、少各列名单）；
3. code 重复；
4. ``name`` != 库内 ``entities.name_zh``（改名也报）；
5. ``segment`` 为 null 或不在闭集（闭集来自 yaml ``closed_segments``，且必须与
   ``caliber`` 的规范闭集一致）；
6. 任一家在库内找不到对应 stock 实体（报出且绝不新建）；
7. ``region`` 若给必须可归一为省级行政单位（GB/T 2260 名称或 CN-XX 码）；
8. ``lane`` 若给必须在 A 股车道闭集内，且 lane 的市场归属必须与 region 一致。

``--selftest`` 在**同一事务**内 apply → 读回 → 再 apply → 断言零变更 → ROLLBACK，
并用一条指纹查询证明回滚后库内零变更（entities 总数、标注数、全表 metadata 指纹、
跨源标识指纹四项与跑前逐项相等）。

退出码
------
* 0  成功（dry-run / --apply / --selftest 通过）
* 2  DSN 未设置或命令行互斥参数
* 3  输入契约错误（yaml 读不了 / 顶层结构不对 / constituents 无法解析）
* 4  失败即拒：yaml 内容违规（逐条指名，未写库）
* 5  写后核验失败（已回滚）

用法
----
    source ~/.config/finddata/dashboards.env
    export NO_PROXY='*'
    .venv/bin/python scripts/register_agriculture_universe.py                  # dry-run
    .venv/bin/python scripts/register_agriculture_universe.py --apply          # 落库
    .venv/bin/python scripts/register_agriculture_universe.py --selftest \
        --universe /tmp/agri-fixture-a.yaml --allow-subset                     # 幂等/回滚实证
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_UNIVERSE = ROOT / "docs" / "agriculture-universe.yaml"
DEFAULT_TARGET_ID = 94  # entities.id=94 → 申万一级 801010 农林牧渔
ENTITY_TYPE = "stock"
LIST_TAG = "agriculture-universe"

# ── segment 闭集（按 caliber）────────────────────────────────────────────────
# 中文九值来自 tasks.md 8.1 的明确闭集；英文 slug 来自
# openspec/changes/add-agriculture-industry-sources/specs/agriculture-universe/spec.md
# 「hog / poultry / aquatic / feed / animal-health / seed / planting / forestry /
#  agri-processing」。两者是同一口径的两种写法，yaml 选其一并保持全表一致。
# coarse-7 是 proposal 已拍板的粗口径（tasks.md 8.1 解锁条件②），只定义中文。
CALIBERS: dict[str, dict[str, set[str]]] = {
    "shenwan-l1": {
        "zh": {
            "生猪养殖", "禽养殖", "饲料", "动保", "种业",
            "种植", "渔业", "林业", "农产品加工",
        },
        "en": {
            "hog", "poultry", "aquatic", "feed", "animal-health",
            "seed", "planting", "forestry", "agri-processing",
        },
    },
    "coarse-7": {
        "zh": {"种植", "养殖", "饲料", "动保", "农产品加工", "渔业", "林业"},
    },
}

# ── 省级行政单位（GB/T 2260）─────────────────────────────────────────────────
# 与 scripts/hotel_regions.py 的 PROVINCE_GB 同源；额外补 71/81/82 与新疆兵团。
# 集合只在常量里维护，校验逻辑只查表，不写死任何省份名。
PROVINCES: dict[str, str] = {
    "北京": "11", "天津": "12", "河北": "13", "山西": "14", "内蒙古": "15",
    "辽宁": "21", "吉林": "22", "黑龙江": "23",
    "上海": "31", "江苏": "32", "浙江": "33", "安徽": "34", "福建": "35",
    "江西": "36", "山东": "37",
    "河南": "41", "湖北": "42", "湖南": "43", "广东": "44", "广西": "45",
    "海南": "46",
    "重庆": "50", "四川": "51", "贵州": "52", "云南": "53", "西藏": "54",
    "陕西": "61", "甘肃": "62", "青海": "63", "宁夏": "64", "新疆": "65",
    "台湾": "71", "香港": "81", "澳门": "82",
}
# 省级但不在 GB/T 2260 的行政单位（与 hotel_regions.PROVINCE_SPECIAL 同源）
PROVINCE_SPECIAL = {"新疆兵团": "CN-XJBT"}

# 官方全称 → 规范短名（只列与短名不同的：4 直辖市 + 5 自治区 + 2 特区 + 台湾省）
PROVINCE_FULL = {
    "北京": "北京市", "天津": "天津市", "上海": "上海市", "重庆": "重庆市",
    "内蒙古": "内蒙古自治区", "广西": "广西壮族自治区", "西藏": "西藏自治区",
    "宁夏": "宁夏回族自治区", "新疆": "新疆维吾尔自治区",
    "香港": "香港特别行政区", "澳门": "澳门特别行政区", "台湾": "台湾省",
}

#: 规范短名 / GB 码 / CN-XX 码 / 官方全称 → 规范短名。校验只做一次查表。
PROVINCE_ALIASES: dict[str, str] = {}
for _name, _gb in PROVINCES.items():
    PROVINCE_ALIASES[_name] = _name
    PROVINCE_ALIASES[f"CN-{_gb}"] = _name
    PROVINCE_ALIASES[PROVINCE_FULL.get(_name, f"{_name}省")] = _name
for _name, _code in PROVINCE_SPECIAL.items():
    PROVINCE_ALIASES[_name] = _name
    PROVINCE_ALIASES[_code] = _name

#: 本宇宙全部为 A 股，region 必为境内省级单位 → 市场恒为 CN。
#: 车道集合来自农业线已部署/已核对的 A 股通道（见 register_flower_universe.py
#: LANES 与 tasks.md 8.1：stock-zygc 只作证据，不作 segment 真源）。
LANE_MARKET: dict[str, str] = {
    "cninfo-operating": "CN",   # 巨潮/交易所经营与财务数据车道（酒店/餐饮线既有）
    "fd-cn-report": "CN",       # A 股本地财报车道
    "stock-zygc": "CN",         # 东财主营构成（仅作 evidence，不作 segment 真源）
}
REGION_MARKET = "CN"  # 省级行政单位 → A 股市场

# ── 违规分类名（打印顺序即语义顺序）──────────────────────────────────────────
CAT_INPUT = "输入契约"
CAT_TOTAL = "总数不符"
CAT_MISSING = "成分缺失"
CAT_EXTRA = "成分多余"
CAT_DUP = "代码重复"
CAT_NO_ENTITY = "实体缺失"
CAT_NAME = "名称不符"
CAT_SEG_NULL = "segment 缺失"
CAT_SEG_BAD = "segment 越界"
CAT_REGION = "region 越界"
CAT_LANE = "lane 越界"
CAT_LANE_REGION = "lane/region 错配"
CAT_ORDER = [
    CAT_INPUT, CAT_TOTAL, CAT_MISSING, CAT_EXTRA, CAT_DUP, CAT_NO_ENTITY,
    CAT_NAME, CAT_SEG_NULL, CAT_SEG_BAD, CAT_REGION, CAT_LANE, CAT_LANE_REGION,
]


class InputError(Exception):
    """yaml 顶层契约/真源读取失败（exit 3）。"""


# ── 读取 ─────────────────────────────────────────────────────────────────────
def fetch_truth(cur, target_id: int) -> set[str]:
    """库内真源：申万 801010（target_id）的全部成分代码。"""
    cur.execute(
        "SELECT DISTINCT e.code FROM entity_relationships r"
        " JOIN entities e ON e.id = r.source_id WHERE r.target_id = %s",
        (target_id,),
    )
    return {row[0] for row in cur.fetchall()}


def fetch_entities(cur, codes: list[str]) -> tuple[dict, dict]:
    """code → 实体记录；stock 与非 stock 分开（后者用于「有行但不是 stock」）。"""
    if not codes:
        return {}, {}
    cur.execute(
        "SELECT id, code, entity_type, name_zh, name_en, metadata_json"
        " FROM entities WHERE code = ANY(%s)",
        (codes,),
    )
    stock: dict[str, dict] = {}
    other: dict[str, list[dict]] = defaultdict(list)
    for eid, code, etype, name_zh, name_en, meta in cur.fetchall():
        rec = {
            "id": eid, "code": code, "entity_type": etype,
            "name_zh": name_zh, "name_en": name_en, "metadata_json": meta or {},
        }
        if etype == ENTITY_TYPE:
            stock.setdefault(code, rec)
        else:
            other[code].append(rec)
    return stock, other


def load_yaml(path: Path) -> dict:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InputError(
            f"宇宙真源不存在: {path}\n"
            "  修法：等待 8.1 产出 docs/agriculture-universe.yaml，或用 --universe 指向 fixture"
        ) from None
    except yaml.YAMLError as exc:
        raise InputError(f"yaml 解析失败: {path}: {exc}") from None
    if not isinstance(doc, dict):
        raise InputError(f"yaml 顶层必须是 mapping，实为 {type(doc).__name__}")
    if "companies" not in doc:
        raise InputError("yaml 缺 companies 列表")
    if not isinstance(doc["companies"], list) or not doc["companies"]:
        raise InputError("companies 必须是非空列表")
    return doc


def parse_target_id(doc: dict) -> int:
    source = doc.get("source") or {}
    raw = source.get("constituents")
    if raw is None:
        return DEFAULT_TARGET_ID
    match = re.search(r"target_id\s*=\s*(\d+)", str(raw))
    if not match:
        raise InputError(
            f"source.constituents 无法解析出 target_id: {raw!r}（期望形如 "
            "'entity_relationships.target_id=94'）"
        )
    return int(match.group(1))


def normalize_province(value) -> str | None:
    if not isinstance(value, str):
        return None
    return PROVINCE_ALIASES.get(value.strip())


def resolve_closed_segments(doc: dict, violations: list[str]) -> set[str] | None:
    """确定 segment 闭集；yaml 给的值必须与 caliber 规范闭集逐项相等。"""
    caliber = doc.get("caliber")
    given = doc.get("closed_segments")

    if given is not None:
        if not isinstance(given, list) or not given:
            violations.append(
                f"[{CAT_INPUT}] (closed_segments)｜期望：非空字符串列表｜实际：{given!r}"
                f"｜修法：按 caliber 写全闭集"
            )
            return None
        if not all(isinstance(x, str) and x.strip() for x in given):
            violations.append(
                f"[{CAT_INPUT}] (closed_segments)｜期望：全部为非空字符串｜实际：{given!r}"
                f"｜修法：去掉空值/非字符串"
            )
            return None
        if len(set(given)) != len(given):
            dupes = sorted({x for x in given if given.count(x) > 1})
            violations.append(
                f"[{CAT_INPUT}] (closed_segments)｜期望：无重复｜实际：重复 {dupes}"
                f"｜修法：删除重复项"
            )
        given_set = set(given)

        if caliber is None:
            print("WARN: yaml 未声明 caliber，直接采用 yaml 的 closed_segments")
            return given_set
        if caliber not in CALIBERS:
            violations.append(
                f"[{CAT_INPUT}] (caliber)｜期望：{'/'.join(CALIBERS)}｜实际：{caliber!r}"
                f"｜修法：填已登记口径；新口径须先在本脚本 CALIBERS 登记"
            )
            return given_set
        for variant, expected in CALIBERS[caliber].items():
            if given_set == expected:
                return given_set
        expected_desc = "；".join(
            f"{name}={'/'.join(sorted(s))}"
            for name, s in CALIBERS[caliber].items()
        )
        violations.append(
            f"[{CAT_INPUT}] (closed_segments)｜期望：caliber={caliber} 的规范闭集"
            f"（{expected_desc}）｜实际：{'/'.join(sorted(given_set))}"
            f"｜修法：闭集不得在 yaml 里改写；新口径须先登记 CALIBERS"
        )
        return given_set

    if caliber in CALIBERS:
        default = CALIBERS[caliber]["zh"]
        print(f"WARN: yaml 未给 closed_segments，采用 caliber={caliber} 默认闭集")
        return set(default)
    violations.append(
        f"[{CAT_INPUT}] (caliber/closed_segments)｜期望：至少其一可确定闭集"
        f"｜实际：caliber={caliber!r}, closed_segments=null"
        f"｜修法：补 caliber 或 closed_segments"
    )
    return None


# ── 校验（失败即拒，一次列全）───────────────────────────────────────────────
def validate(doc: dict, truth: set[str], stock: dict, other: dict,
             allow_subset: bool) -> tuple[dict[str, list[str]], list[str], set[str]]:
    violations: dict[str, list[str]] = defaultdict(list)
    warnings: list[str] = []
    rejected: set[str] = set()

    def bad(cat: str, code: str, expected: str, actual: str, fix: str) -> None:
        violations[cat].append(
            f"[{cat}] {code}｜期望：{expected}｜实际：{actual}｜修法：{fix}"
        )

    version = doc.get("version")
    if version is not None and version != 1:
        violations[CAT_INPUT].append(
            f"[{CAT_INPUT}] (version)｜期望：1｜实际：{version!r}｜修法：升级脚本或改回 1"
        )

    closed = resolve_closed_segments(doc, violations[CAT_INPUT])
    closed_desc = "/".join(sorted(closed)) if closed else "（闭集未确定）"

    rows = doc["companies"]
    count_expected = doc.get("count_expected")
    if count_expected is None:
        count_expected = len(truth)
        warnings.append(
            f"yaml 未给 count_expected，按库内真源 {len(truth)} 家断言"
        )
    elif isinstance(count_expected, bool) or not isinstance(count_expected, int):
        violations[CAT_INPUT].append(
            f"[{CAT_INPUT}] (count_expected)｜期望：整数｜实际：{count_expected!r}"
            f"｜修法：填与真源一致的整数"
        )
        count_expected = len(truth)
    if len(rows) != count_expected:
        violations[CAT_TOTAL].append(
            f"[{CAT_TOTAL}] (宇宙)｜期望：count_expected={count_expected} 家"
            f"｜实际：yaml {len(rows)} 家"
            f"｜修法：补齐或删除条目，使条数与申万成分对账一致"
        )

    # code / name 提取（缺字段只报一次，不参与后续逐项比较）
    parsed: list[tuple[str, dict]] = []
    name_en_missing = 0
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            violations[CAT_INPUT].append(
                f"[{CAT_INPUT}] 第 {idx} 条｜期望：mapping｜实际：{type(row).__name__}"
                f"｜修法：写成 - code: ... name: ... segment: ..."
            )
            continue
        code = row.get("code")
        ticker = row.get("ticker")
        if code and ticker and code != ticker:
            bad(CAT_INPUT, str(code), "code 与 ticker 一致",
                f"code={code!r}, ticker={ticker!r}", "只保留一个，或让两者相同")
            continue
        code = code or ticker
        if not code or not isinstance(code, str):
            violations[CAT_INPUT].append(
                f"[{CAT_INPUT}] 第 {idx} 条｜期望：非空字符串 code（或 ticker）"
                f"｜实际：{code!r}｜修法：补 code"
            )
            continue
        code = code.strip()
        parsed.append((code, row))

    codes = [c for c, _ in parsed]
    dupes = sorted({c for c in codes if codes.count(c) > 1})
    for code in dupes:
        bad(CAT_DUP, code, "每个 code 出现 1 次", f"出现 {codes.count(code)} 次",
            "删除重复条目（重复写入会掩盖真实差异）")
        rejected.add(code)
    yaml_codes = set(codes)

    if not allow_subset:
        missing = sorted(truth - yaml_codes)
        if missing:
            violations[CAT_MISSING].append(
                f"[{CAT_MISSING}] (宇宙)｜期望：库内真源 {len(truth)} 家全部出现"
                f"｜实际：yaml 缺 {len(missing)} 家：{', '.join(missing)}"
                f"｜修法：补回这些成分；缺一家即拒绝，不做沉默的部分注册"
            )
            rejected.update(missing)
    else:
        missing = sorted(truth - yaml_codes)
        if missing:
            warnings.append(
                f"子集模式（--allow-subset）：跳过 {len(missing)} 家真源成分的缺失断言"
                f"（仅用于自检 fixture，生产须用全量 yaml）"
            )

    extra = sorted(yaml_codes - truth)
    for code in extra:
        where = "库内存在 stock 实体" if code in stock else "库内无此 stock 实体"
        bad(CAT_EXTRA, code, "code 属于申万 801010 成分",
            f"不在真源（{where}）", "删除该条；非本行业公司不得进入宇宙")
        rejected.add(code)

    for code, row in parsed:
        name = row.get("name")
        name_zh = row.get("name_zh")
        if name and name_zh and str(name).strip() != str(name_zh).strip():
            bad(CAT_NAME, code, "name 与 name_zh 一致",
                f"name={name!r}, name_zh={name_zh!r}", "只保留一个字段")
            rejected.add(code)
        name = name if name is not None else name_zh
        if name is None or not str(name).strip():
            bad(CAT_NAME, code, "非空名称", "缺失", "补 name（必须与库内 name_zh 逐字一致）")
            rejected.add(code)

        segment = row.get("segment")
        if segment is None or (isinstance(segment, str) and not segment.strip()):
            bad(CAT_SEG_NULL, code, f"∈ 闭集 {closed_desc}", "null/空",
                "按申万三级或用户对照表填 segment；留空即拒，不猜")
            rejected.add(code)
        elif closed is not None and segment not in closed:
            bad(CAT_SEG_BAD, code, f"∈ 闭集 {closed_desc}", repr(segment),
                "改为闭集内的值；新细分口径须先登记 CALIBERS")
            rejected.add(code)

        region = row.get("region")
        norm_region = None
        if region is not None:
            norm_region = normalize_province(region)
            if norm_region is None:
                bad(CAT_REGION, code,
                    "省级行政单位名（GB/T 2260 名称 / 官方全称 / CN-XX 码），"
                    f"共 {len(PROVINCES) + len(PROVINCE_SPECIAL)} 个",
                    repr(region),
                    "改为省级单位（如 广东 / 广东省 / CN-44）；城市/区县不得填进 region")
                rejected.add(code)
        lane = row.get("lane")
        if lane is not None:
            if lane not in LANE_MARKET:
                bad(CAT_LANE, code, f"∈ {sorted(LANE_MARKET)}", repr(lane),
                    "A 股公司只能用 A 股车道；非本市场车道须另案登记")
                rejected.add(code)
            elif norm_region is not None and LANE_MARKET[lane] != REGION_MARKET:
                bad(CAT_LANE_REGION, code,
                    f"lane 市场 {LANE_MARKET[lane]} == region 市场 {REGION_MARKET}",
                    f"lane={lane!r}, region={region!r}",
                    "核对车道与 region；不得跨境错配")

        if code not in stock:
            others = other.get(code) or []
            if others:
                actual = "实体存在但不是 stock: " + ", ".join(
                    f"entity_type={o['entity_type']!r}" for o in others)
            else:
                actual = "库内找不到该 code"
            bad(CAT_NO_ENTITY, code, "库内存在 entity_type='stock' 的既有实体",
                actual,
                "先由实体注册批次落库；本脚本绝不新建实体，也不改 entity_type")
            rejected.add(code)
            continue

        db_name = (stock[code]["name_zh"] or "").strip()
        if name is not None and str(name).strip() != db_name:
            bad(CAT_NAME, code, db_name or "（库内 name_zh 为空）",
                str(name).strip(),
                "改 yaml 与库内名称一致；本脚本不覆盖名称（改名须走名称门）")
            rejected.add(code)

        name_en = row.get("name_en")
        if name_en is not None:
            db_en = (stock[code]["name_en"] or "").strip()
            if not db_en:
                name_en_missing += 1
            elif str(name_en).strip() != db_en:
                bad(CAT_NAME, code, f"name_en={db_en!r}", f"yaml={name_en!r}",
                    "改 yaml 与库内一致；本脚本不覆盖名称")
                rejected.add(code)

    if name_en_missing:
        warnings.append(
            f"{name_en_missing} 家的库内 name_en 为空（本宇宙 104/104 皆然）："
            "yaml 的 name_en 既不校验也不回填——名称写入不属本脚本，须另案走名称门"
        )

    return violations, warnings, rejected


# ── 计划 / 写入 ──────────────────────────────────────────────────────────────
def meta_for(company: dict, doc: dict, norm_region: str | None) -> dict:
    """写入 entities.metadata_json 的农业宇宙标注（与既有 universe 脚本同形）。"""
    return {
        "list": LIST_TAG,
        "change": "add-agriculture-industry-sources",
        "task": "8.2",
        "vertical": "agriculture",
        "universe_id": doc.get("universe_id"),
        "caliber": doc.get("caliber"),
        "segment": company.get("segment"),
        "region": norm_region,
        "lane": company.get("lane"),
        "ticker": company["code"],
        "evidence": company.get("evidence"),
        "truth_source": "fd-industry-data/docs/agriculture-universe.yaml",
    }


def exchange_for(code: str) -> str:
    """A 股 6 位代码 → 交易所（代码段约定；境外票符原样取前缀）。"""
    if code.isdigit() and len(code) == 6:
        if code[0] == "6":
            return "SSE"
        if code[0] in ("0", "3"):
            return "SZSE"
        if code[0] in ("4", "8", "9"):
            return "BSE"
    return code.split(".")[-1] if "." in code else "UNKNOWN"


def identifiers_for(company: dict) -> list[tuple[str, str]]:
    code = company["code"]
    return [
        (LIST_TAG, code),
        (f"exchange-ticker:{exchange_for(code)}", code.split(".")[0]),
    ]


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def sync(cur, rows: list[dict], doc: dict, execute: bool) -> dict:
    """计算并（可选）执行写入。created 恒为 0 —— 本脚本不新建实体。"""
    codes = [r["code"] for r in rows]
    stock, _ = fetch_entities(cur, codes)
    ids = [stock[c]["id"] for c in codes if c in stock]
    cur.execute(
        "SELECT entity_id, source, identifier FROM entity_source_identifiers"
        " WHERE entity_type = %s AND entity_id = ANY(%s)",
        (ENTITY_TYPE, ids),
    )
    have = {(eid, src, ident) for eid, src, ident in cur.fetchall()}

    stats = {"created": 0, "updated": 0, "unchanged": 0, "identifiers": 0,
             "lines": []}
    for company in rows:
        code = company["code"]
        rec = stock[code]
        eid = rec["id"]
        norm_region = (normalize_province(company["region"])
                       if company.get("region") is not None else None)
        old_meta = rec["metadata_json"] or {}
        new_meta = {**old_meta, **meta_for(company, doc, norm_region)}
        changed = _canon(old_meta) != _canon(new_meta)
        if changed:
            stats["updated"] += 1
            if execute:
                cur.execute(
                    "UPDATE entities SET metadata_json = %s, updated_at = now()"
                    " WHERE id = %s AND entity_type = %s",
                    (json.dumps(new_meta, ensure_ascii=False), eid, ENTITY_TYPE),
                )
        else:
            stats["unchanged"] += 1
        added = 0
        for source, identifier in identifiers_for(company):
            if (eid, source, identifier) in have:
                continue
            added += 1
            stats["identifiers"] += 1
            if execute:
                cur.execute(
                    "INSERT INTO entity_source_identifiers"
                    " (entity_type, entity_id, source, identifier, created_at)"
                    " VALUES (%s, %s, %s, %s, now())",
                    (ENTITY_TYPE, eid, source, identifier),
                )
        stats["lines"].append((code, changed, added, rec["name_zh"], company))
    return stats


def print_plan(stats: dict, rows: list[dict], rejected: set[str]) -> None:
    print(f"\n== 注册计划（{len(rows)} 家）==")
    for code, changed, added, db_name, company in stats["lines"]:
        tag = "updated" if changed else "unchanged"
        raw_region = company.get("region")
        norm_region = (normalize_province(raw_region)
                       if raw_region is not None else None)
        region_txt = "null" if raw_region is None else (
            norm_region if norm_region == raw_region else f"{raw_region}→{norm_region}")
        extra = f" +{added} 标识" if added else ""
        print(
            f"  [{tag}] {code} {db_name}｜segment={company.get('segment')}"
            f"｜region={region_txt}｜lane={company.get('lane')}{extra}"
        )
    print(
        f"统计：created={stats['created']} updated={stats['updated']}"
        f" unchanged={stats['unchanged']} rejected={len(rejected)}"
        f"｜新增跨源标识={stats['identifiers']} 条（created 恒为 0：绝不新建实体）"
    )


def print_violations(violations: dict[str, list[str]]) -> None:
    total_cat = sum(1 for c in CAT_ORDER if violations.get(c))
    total = sum(len(violations.get(c, [])) for c in CAT_ORDER)
    print(f"\n== 失败即拒：{total_cat} 类 / {total} 条违规（一次列全）==")
    for cat in CAT_ORDER:
        items = violations.get(cat) or []
        if not items:
            continue
        print(f"\n-- {cat}（{len(items)} 条）--")
        for item in items:
            print("  " + item)


def fingerprint(cur) -> dict:
    """一条查询给出四项指纹：用于证明回滚后库内零变更。"""
    cur.execute(
        "SELECT"
        " (SELECT count(*) FROM entities),"
        " (SELECT count(*) FROM entities WHERE metadata_json->>'list' = %s),"
        " (SELECT count(*) FROM entity_source_identifiers WHERE source = %s),"
        " (SELECT md5(coalesce(string_agg(id::text || ':' ||"
        "   coalesce(metadata_json::text, ''), '|' ORDER BY id), '')) FROM entities),"
        " (SELECT md5(coalesce(string_agg(entity_type || ':' || entity_id || ':' ||"
        "   source || ':' || identifier, '|'"
        "   ORDER BY entity_type, entity_id, source, identifier), ''))"
        "   FROM entity_source_identifiers)",
        (LIST_TAG, LIST_TAG),
    )
    total, marked, ids, ent_fp, id_fp = cur.fetchone()
    return {"entities_total": total, "marked": marked, "universe_ids": ids,
            "entities_fp": ent_fp, "identifiers_fp": id_fp}


def fmt_fp(fp: dict) -> str:
    return (
        f"entities_total={fp['entities_total']} marked={fp['marked']}"
        f" universe_ids={fp['universe_ids']}"
        f" entities_md5={fp['entities_fp'][:12] if fp['entities_fp'] else None}"
        f" identifiers_md5={fp['identifiers_fp'][:12] if fp['identifiers_fp'] else None}"
    )


def verify_write(cur, rows: list[dict]) -> tuple[list[str], dict]:
    codes = [r["code"] for r in rows]
    stock, _ = fetch_entities(cur, codes)
    problems: list[str] = []
    for company in rows:
        code = company["code"]
        rec = stock.get(code)
        if rec is None:
            problems.append(f"{code}: 写后找不到 stock 实体")
            continue
        meta = rec["metadata_json"] or {}
        miss = [k for k in ("list", "segment", "region", "lane", "ticker")
                if k not in meta]
        if miss:
            problems.append(f"{code}: 写后 metadata 缺 {miss}")
        elif meta["list"] != LIST_TAG or meta["ticker"] != code:
            problems.append(f"{code}: 写后 metadata 标记不对: {meta.get('list')}/{meta.get('ticker')}")
        elif meta["segment"] != company.get("segment"):
            problems.append(f"{code}: segment 写后 {meta['segment']!r} != yaml {company.get('segment')!r}")
        want_region = (normalize_province(company.get("region"))
                       if company.get("region") is not None else None)
        if "region" in meta and meta["region"] != want_region:
            problems.append(f"{code}: region 写后 {meta['region']!r} != 期望 {want_region!r}")
        if "lane" in meta and meta["lane"] != company.get("lane"):
            problems.append(f"{code}: lane 写后 {meta['lane']!r} != yaml {company.get('lane')!r}")
    cur.execute(
        "SELECT entity_id, count(*) FROM entity_source_identifiers"
        " WHERE entity_type = %s AND source = %s AND identifier = ANY(%s)"
        " GROUP BY entity_id",
        (ENTITY_TYPE, LIST_TAG, codes),
    )
    counts = dict(cur.fetchall())
    for company in rows:
        rec = stock.get(company["code"])
        if rec and counts.get(rec["id"], 0) < 1:
            problems.append(f"{company['code']}: 缺 agriculture-universe 跨源标识")
    dist = {
        "segment": Counter(stock[c]["metadata_json"].get("segment")
                           for c in codes if c in stock),
        "region": Counter(stock[c]["metadata_json"].get("region")
                          for c in codes if c in stock),
        "lane": Counter(stock[c]["metadata_json"].get("lane")
                        for c in codes if c in stock),
    }
    return problems, dist


# ── 主流程 ───────────────────────────────────────────────────────────────────
def run(conn, args, rows, doc) -> int:
    cur = conn.cursor()

    if args.selftest:
        return selftest(conn, cur, rows, doc)

    if not args.apply:
        stats = sync(cur, rows, doc, execute=False)
        print_plan(stats, rows, set())
        conn.rollback()
        print("\nDRY-RUN（只读事务，未写库；加 --apply 落库）")
        return 0

    stats = sync(cur, rows, doc, execute=True)
    print_plan(stats, rows, set())
    problems, dist = verify_write(cur, rows)
    if problems:
        conn.rollback()
        print("\nFATAL 写后核验失败（已回滚，库内零变更）:")
        for p in problems:
            print("  " + p)
        return 5

    print("\nsegment:", dict(dist["segment"]))
    print("region :", dict(dist["region"]))
    print("lane   :", dict(dist["lane"]))
    conn.commit()
    print(f"APPLIED（已提交）：updated={stats['updated']}"
          f" unchanged={stats['unchanged']} 新增标识={stats['identifiers']}")
    return 0


def selftest(conn, cur, rows, doc) -> int:
    checks: list[tuple[str, bool, str]] = []
    base = fingerprint(cur)
    print(f"基线指纹：{fmt_fp(base)}")

    try:
        first = sync(cur, rows, doc, execute=True)
        print(f"\n第一次 apply：created={first['created']} updated={first['updated']}"
              f" unchanged={first['unchanged']} 新增标识={first['identifiers']}")

        fp_after_first = fingerprint(cur)
        problems, _dist = verify_write(cur, rows)

        pre_marked = base["marked"] == len(rows) and base["universe_ids"] >= len(rows)
        wrote = first["updated"] + first["identifiers"]
        checks.append((
            "首次 created=0（复用既有实体，不新建）",
            first["created"] == 0,
            f"created={first['created']}",
        ))
        checks.append((
            "首次确实写入（updated+标识 > 0；已先行落库时豁免）",
            wrote > 0 or pre_marked,
            f"updated={first['updated']} identifiers={first['identifiers']}"
            + ("（该 yaml 已先行落库，首次无写入属正常前置态）" if pre_marked else ""),
        ))
        checks.append((
            f"读回断言：{len(rows)} 家全部带农业宇宙标注",
            fp_after_first["marked"] == len(rows) and not problems,
            f"marked={fp_after_first['marked']}/{len(rows)}"
            + (f"；问题={problems[:3]}..." if problems else "；写后核验 0 问题"),
        ))

        before_second = fingerprint(cur)
        second = sync(cur, rows, doc, execute=True)
        after_second = fingerprint(cur)
        print(f"第二次 apply：created={second['created']} updated={second['updated']}"
              f" unchanged={second['unchanged']} 新增标识={second['identifiers']}")
        checks.append((
            "第二次 created=0",
            second["created"] == 0,
            f"created={second['created']}",
        ))
        checks.append((
            "第二次 updated=0",
            second["updated"] == 0,
            f"updated={second['updated']}",
        ))
        checks.append((
            "第二次新增标识=0",
            second["identifiers"] == 0,
            f"identifiers={second['identifiers']}",
        ))
        checks.append((
            "第二次前后全库指纹完全相同（零变更）",
            before_second == after_second,
            f"{fmt_fp(before_second)} -> {fmt_fp(after_second)}",
        ))
    finally:
        conn.rollback()

    after_rollback = fingerprint(cur)
    same = after_rollback == base
    checks.append((
        "ROLLBACK 后与基线逐项相同（库内零变更）",
        same,
        f"基线 {fmt_fp(base)}\n        回滚后 {fmt_fp(after_rollback)}",
    ))

    print("\n== --selftest 断言 ==")
    for label, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}：{detail}")

    if same:
        print(f"\n已回滚，库内零变更。证明查询："
              f"entities 总数 / metadata 标注数 / 跨源标识数 / 全表 metadata 指纹 "
              f"四项与跑前一致 -> {fmt_fp(after_rollback)}")
    else:
        print(f"\nFATAL 回滚后仍有差异！基线 {fmt_fp(base)} -> {fmt_fp(after_rollback)}")

    failed = [c for c in checks if not c[1]]
    return 0 if not failed else 5


def main() -> int:
    ap = argparse.ArgumentParser(description="农业监控宇宙注册（默认 dry-run）")
    ap.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE,
                    help=f"宇宙真源 yaml（默认 {DEFAULT_UNIVERSE}）")
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--apply", action="store_true", help="提交写入（默认 dry-run）")
    ap.add_argument("--selftest", action="store_true",
                    help="同事务 apply→复核→再 apply→断言零变更→ROLLBACK")
    ap.add_argument("--allow-subset", action="store_true",
                    help="允许 yaml 为真源子集（跳过缺失断言；仅供自检 fixture）")
    args = ap.parse_args()
    if args.apply and args.selftest:
        print("FATAL: --apply 与 --selftest 互斥")
        return 2
    if not args.dsn:
        print("FATAL: $FD_OPEN_DATA_DSN 未设置（source ~/.config/finddata/dashboards.env）")
        return 2

    try:
        doc = load_yaml(args.universe)
        target_id = parse_target_id(doc)
    except InputError as exc:
        print(f"FATAL: {exc}")
        return 3

    conn = None
    try:
        conn = psycopg2.connect(args.dsn)
        conn.autocommit = False
        if not args.apply and not args.selftest:
            conn.set_session(readonly=True)  # dry-run 物理上不可写
        cur = conn.cursor()

        truth = fetch_truth(cur, target_id)
        codes = []
        for row in doc["companies"]:
            if isinstance(row, dict):
                code = row.get("code") or row.get("ticker")
                if isinstance(code, str) and code.strip():
                    codes.append(code.strip())
        stock, other = fetch_entities(cur, sorted(set(codes) | truth))

        violations, warnings, rejected = validate(
            doc, truth, stock, other, args.allow_subset)
        print(f"真源：target_id={target_id}，库内成分 {len(truth)} 家；"
              f"yaml={args.universe}（{len(doc['companies'])} 条）")
        if args.allow_subset:
            print("模式：子集（--allow-subset，跳过缺失断言；仅供自检 fixture）")
        for w in warnings:
            print(f"WARN: {w}")

        total_violations = sum(len(v) for v in violations.values())
        if total_violations:
            print_violations(violations)
            print(f"\nFATAL: 拒绝写入（共 {total_violations} 条违规，"
                  f"涉及 {len(rejected)} 个 code）")
            conn.rollback()
            return 4

        # 只对「校验通过」的行做计划/写入；由于无违规，rows 即全部
        rows = []
        for row in doc["companies"]:
            code = (row.get("code") or row.get("ticker")).strip()
            rows.append({**row, "code": code, "region": row.get("region")})
        return run(conn, args, rows, doc)
    except psycopg2.Error as exc:
        if conn is not None:
            conn.rollback()
        print(f"FATAL: 数据库错误（已回滚）: {exc}")
        return 3
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
