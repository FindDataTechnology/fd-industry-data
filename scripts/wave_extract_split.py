#!/usr/bin/env python3
"""波抽取器的「能定时跑吗」分类器（change schedule-wave-observation-extraction 1.1）。

**要回答的问题**：四个波包里的抽取器，哪些能在定时作业里跑，哪些不能？

判据只有一条，且从源码里读、不靠人工清单：一个抽取器的 ``extract()`` 若调用
``unit_items(cur, ...)``，它读的是中央库的 ``crawl_items``，因此**必须**有常驻调用方；
若它调 ``live_items(...)`` 或自带 ``_load_unit``（在抽取进程里直接打上游 API），
它就是「单元还没部署时」的临时形态——在抽取 pod 里再打一遍上游等于造一个不受
crawl policy / 代理池 / 限速管束的影子爬虫，因此**不得**被定时执行。

这件事必须机械判，因为它是**会静默反向**的那类规则：有人把一个 ``live_items``
单元改回 ``unit_items``（正解），或新写一个抽取器忘了声明形态，人工清单都不会动，
而定时作业会照旧跳过它——观测层就那么悄悄冻住，跟这次要修的病灶一模一样。

分类结果有两份且必须互补（``tests/test_wave_extract_split.py`` 双向校验）：
  * ``scheduled_by_package()`` —— 每个包 ``--only`` 该带哪些 SOURCE；
  * ``LIVE_FETCH_REASONS``    —— 每个被排除的模块及**具名理由**。

``gen_wave_extract_manifest.py``（fd-infra-deploy）从同一个 git ref 取本文件执行，
用它算出 CronJob 的 ``--only`` 参数，于是清单与判据只有一份真源。

用法::

    python3 scripts/wave_extract_split.py            # 打印分类与两份名单
    python3 scripts/wave_extract_split.py --json     # 给生成器/测试机读
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

#: 参与分类的四个波包（顺序即 CronJob 里的执行顺序）。
PACKAGES = (
    "restaurant_extractors",
    "agriculture_extractors",
    "hotel_extractors",
    "flower_extractors",
)

#: 驱动脚本（每个包一个），同样按包名索引。
DRIVERS = {
    "restaurant_extractors": "extract_restaurant_wave0.py",
    "agriculture_extractors": "extract_agriculture_wave1.py",
    "hotel_extractors": "extract_hotel_wave0.py",
    "flower_extractors": "extract_flower_wave1.py",
}

#: 允许的形态值。
SCHEDULED = "crawl_items"   # 读 crawl_items → 必须有常驻调用方
LIVE = "live-fetch"         # 进程内打上游 → 不得定时执行
#: 读 crawl_items **但**仍会打上游（下载 PDF / 抓详情页取正文）。
#: 这类不能定时跑：文档面在库外，每次抽取都要重新下载——既绕开爬取层的
#: 限速/代理，又让一轮作业的时长取决于外部站点（2026-11-19 实测：hkex 的
#: 抽取器在 pod 里 do_sys_poll 挂了 5 分钟以上）。正解是把下载挪进爬虫
#: （payload 里存正文），抽取器只读库。
HYBRID = "crawl_items+net"
UNKNOWN = "unknown"         # 三种都不像 → 判不出，测试红

#: 被排除模块的具名理由。键是 ``<包名>/<模块名>``（模块名去掉 .py）。
#: **为什么按模块而不是按 SOURCE**：``eurostat-api`` 在 restaurant 包里是读
#: crawl_items 的（可定时），在 hotel / flower 包里是 live-fetch 的（不可定时）。
#: 按 SOURCE 建表会把它们混成一个。
LIVE_FETCH_REASONS = {
    "agriculture_extractors/agri_casde":
        "live_items(run_agri_casde)：CASDE 月度供需表由蜘蛛直取，单元已在产"
        "（schedule 10 6 3 * *），数据落 crawl_items；正解是改成 unit_items",
    "agriculture_extractors/cncotton":
        "live_items(run_cncotton)：单元已 parked（上游 2025-08 停更），"
        "定时打上游无收益；恢复发布后先改 unit_items 再谈排期",
    "agriculture_extractors/moa_fishery":
        "live_items(run_moa_fishery)：单元已在产（40 6 7 * *）；正解是改 unit_items",
    "agriculture_extractors/moa_hog_price":
        "live_items(run_moa_hog_capacity / run_moa_hog_price)：单元已在产"
        "（58 8 * * 2），且它的周价三面已由 semantic-extract-batch 的 ex_moa 抽取；"
        "改 unit_items 前不得再起第二条打上游的路",
    "hotel_extractors/eurostat_accommodation":
        "_load_unit(spiders/eurostat-api)：与 flower 的同名抽取器共用同一份单元，"
        "该单元已在产；改 unit_items 后与本包其它单元一起排期",
    "hotel_extractors/mct_star_hotel":
        "_load_unit(spiders/mct-star-hotel)：单元已断更（最新 2024Q3），"
        "月探针在跑（42 11 20 * *）；正解是改 unit_items",
    "hotel_extractors/mct_tourism_demand":
        "_load_unit(spiders/mct-tourism-demand)：单元已在产（42 10 10 * *）；"
        "正解是改 unit_items",
    "flower_extractors/comtrade_flowers":
        "_load_unit(spiders/un-comtrade)：UN Comtrade 需 API key，"
        "在抽取进程里取会绕开凭据/配额管束；正解是改 unit_items",
    "flower_extractors/eurostat_flower_area":
        "_load_unit(spiders/eurostat-api)：同 hotel 的同名抽取器，共用单元",
    # ── hybrid：读 crawl_items，但仍下载 PDF / 抓详情页取正文 ──
    "hotel_extractors/hkex_operating":
        "读 crawl_items 但 fetch_pdf(url) 仍要下载港交所公告 PDF 取正文；"
        "在抽取 pod 里下载既绕开爬取层的限速/代理，又让一轮作业时长取决于外部站点"
        "（2026-11-19 实测挂了 5 分钟以上）。正解是把 PDF 正文存进 payload，"
        "抽取器只读库",
    "hotel_extractors/sec_8k_operating":
        "读 crawl_items 但 urlopen 抓 EDGAR 详情页/exhibit；同上，正解是正文入 payload",
}


#: 判「这个模块会不会打上游」的静态特征。刻意粗（宁可多判 hybrid）：
#: 漏判的代价是一轮作业挂在外网站点上，多判的代价只是一个源推迟定时。
_NET_HINTS = ("urlopen", "Request", "HTTPConnection", "HTTPSConnection",
              "requests.get", "requests.post", "httpx.", "fetch_pdf")


def _calls_network(tree: ast.AST) -> bool:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            nm = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else None)
            if nm:
                names.add(nm)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return any(h in names for h in _NET_HINTS)


def classify_text(text: str) -> tuple[str | None, str, list[str]]:
    """返回 ``(SOURCE, 形态, 被调用的函数名列表)``。

    只做静态读：不 import、不执行，因此可以在没有依赖的环境里跑（生成器要在
    一个干净 checkout 上按 git ref 判，那里没有 psycopg2）。
    """
    tree = ast.parse(text)
    source: str | None = None
    has_extract = False
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "SOURCE" for t in node.targets):
            try:
                source = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                source = None
        elif isinstance(node, ast.FunctionDef) and node.name == "extract":
            has_extract = True
    if source is None or not has_extract:
        return None, "not-an-extractor", []

    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else None)
            if name:
                calls.add(name)

    # 网络调用的静态特征：urlopen / Request / http.client / requests.get|post
    net = _calls_network(tree)

    if "unit_items" in calls:
        kind = HYBRID if net else SCHEDULED
    elif "live_items" in calls or "_load_unit" in calls:
        kind = LIVE
    else:
        kind = UNKNOWN
    return str(source), kind, sorted(calls)


def scan(pkg_dir: Path) -> list[dict]:
    """扫一个包目录。模块的取舍与 ``load_extractors()`` 逐条一致：

    只认不以下划线开头的 ``*.py``——驱动本身也是这个规则（``_common`` /
    ``_samr_readtime`` 因此不参与，前者是公共件、后者不产生写入形态）。
    """
    out: list[dict] = []
    for path in sorted(pkg_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        source, kind, calls = classify_text(path.read_text(encoding="utf-8"))
        if kind == "not-an-extractor":
            continue
        out.append({
            "module": path.stem,
            "source": source,
            "kind": kind,
            "calls": calls,
            "key": f"{pkg_dir.name}/{path.stem}",
        })
    return out


def scan_scripts_dir(scripts_dir: Path) -> dict[str, list[dict]]:
    return {pkg: scan(scripts_dir / pkg) for pkg in PACKAGES}


def scheduled_by_package(scripts_dir: Path) -> dict[str, list[str]]:
    """每个包 ``--only`` 该带的 SOURCE 列表（去重、保持出现顺序）。"""
    out: dict[str, list[str]] = {}
    for pkg, mods in scan_scripts_dir(scripts_dir).items():
        names: list[str] = []
        for m in mods:
            if m["kind"] == SCHEDULED and m["source"] not in names:
                names.append(m["source"])
        out[pkg] = names
    return out


def excluded(scripts_dir: Path) -> dict[str, str]:
    """实测的 live-fetch 模块 → 理由（理由缺了就给空串，由测试判红）。"""
    out: dict[str, str] = {}
    for mods in scan_scripts_dir(scripts_dir).values():
        for m in mods:
            if m["kind"] == LIVE:
                out[m["key"]] = LIVE_FETCH_REASONS.get(m["key"], "")
    return out


def main() -> int:
    scripts_dir = Path(__file__).resolve().parent
    report = scan_scripts_dir(scripts_dir)
    if "--json" in sys.argv:
        print(json.dumps({
            "packages": report,
            "scheduled": scheduled_by_package(scripts_dir),
            "excluded": excluded(scripts_dir),
        }, ensure_ascii=False, indent=2))
        return 0
    for pkg, mods in report.items():
        print(f"== {pkg}  (driver {DRIVERS[pkg]})")
        for m in mods:
            mark = "*" if m["kind"] == SCHEDULED else "-"
            print(f"  {mark} {m['module']:<26} {m['kind']:<12} src={m['source']}")
    print()
    for pkg, names in scheduled_by_package(scripts_dir).items():
        print(f"--only {','.join(names)}   # {pkg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
