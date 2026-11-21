#!/usr/bin/env python3
"""``limit_kind`` 语义闸（change fix-crawl-reliability-floor）。

要回答的问题
────────────
``--limit`` 有两种语义，而 manifest 里没有字段承载它——只能人读 ``spider.py``：
  rolling   滚动尾部：本轮取最新 N，历史靠逐轮累积（增量型）
  full_set  固定历史全集：``limit`` 必须覆盖全集，否则被默认 100 截断（全集型）

2026-11-21 实测：125 个亮着单元里 97 个没声明 ``limit``，全靠默认 100。
2026-11-11 的全量扫描发现 7 个全集型单元因此丢了 55%–99% 的行。

三档判定
────────
``rolling``    未声明 ``limit`` **不报红**（缺省 100 就是它的历史契约）
``full_set``   未声明 ``limit`` **报红**——它会被默认值截断而「成功」
``unassigned`` 缺字段——单列，不判。不许默认成任一取值。

「已定性、未测量」
──────────────────
标 ``full_set`` 但只给了结构性论证、没实测全集规模的单元进这个清单。它们不能
被报成「已修复」——那会把一个已知截断说成没事。清单显式列出，供人后续量。

用法::

    python3 scripts/check_limit_semantics.py
    python3 scripts/check_limit_semantics.py --json
    python3 scripts/check_limit_semantics.py --self-test

退出码：0 无红；1 有「full_set 缺 limit」；2 判不出（清单目录不可用）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPIDERS = REPO / "spiders"
EXEMPT = REPO / "config" / "limit-kind-exempt.txt"
#: 已知的「full_set 缺 limit」存量违规。**只报新增**：永久红的闸会被无视，
#: 但把存量写进基线不等于放水——每次 rerun 仍列出它们，只是不改变退出码。
#: 每行 `单元名 | 事实`；修好或量完全集后从此文件删掉。
BASELINE = REPO / "config" / "limit-kind-baseline.txt"

KINDS = ("rolling", "full_set")


def read_exempt(path: Path = EXEMPT) -> dict[str, str]:
    """单元名 -> 理由。豁免是一笔有主的账，不是把判据调松。"""
    return _read_pipe_table(path)


def read_baseline(path: Path = BASELINE) -> set[str]:
    """已知存量违规：只报新增，不改退出码以外的事实呈现。"""
    return set(_read_pipe_table(path))


def _read_pipe_table(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        out[parts[0]] = parts[1] if len(parts) > 1 else ""
    return out


def read_units(spiders: Path = SPIDERS) -> list[dict]:
    """亮着单元的 (name, limit_kind, limit, data_period)。"""
    import yaml

    out: list[dict] = []
    for p in sorted(spiders.glob("*/manifest.yaml")):
        try:
            m = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - 坏一个清单不拖垮整表
            continue
        if not (m.get("schedule") or "").strip():
            continue
        out.append({
            "name": m.get("name") or p.parent.name,
            "limit_kind": m.get("limit_kind"),
            "limit": m.get("limit"),
            "data_period": m.get("data_period"),
        })
    return out


def judge(units: list[dict], exempt: dict[str, str],
          baseline: set[str] | None = None) -> dict:
    """纯函数：给单元清单与豁免/基线，返回分组。自证与真跑共用。"""
    baseline = baseline or set()
    violated: list[str] = []     # full_set 缺 limit（未豁免）——含基线里的
    unassigned: list[str] = []   # 缺字段
    unmeasured: list[str] = []   # full_set（定性了但未见实测记录）
    bad_kind: list[str] = []     # 取值不在枚举里
    for u in units:
        kind = u["limit_kind"]
        if kind is None:
            unassigned.append(u["name"])
        elif kind not in KINDS:
            bad_kind.append(f"{u['name']}={kind}")
        elif kind == "full_set":
            unmeasured.append(u["name"])
            if u["limit"] is None and u["name"] not in exempt:
                violated.append(u["name"])
    known = sorted(set(violated) & baseline)
    new = sorted(set(violated) - baseline)
    return {
        "total": len(units),
        "rolling": sum(1 for u in units if u["limit_kind"] == "rolling"),
        "full_set": sum(1 for u in units if u["limit_kind"] == "full_set"),
        "unassigned": sorted(unassigned),
        "full_set_without_limit": sorted(violated),
        "known_violations": known,
        "new_violations": new,
        "full_set_units": sorted(unmeasured),
        "bad_kind": sorted(bad_kind),
        "exempt": sorted(exempt),
    }


def self_test() -> int:
    units = [
        {"name": "a", "limit_kind": "rolling", "limit": None, "data_period": "day"},
        {"name": "b", "limit_kind": "full_set", "limit": 5000, "data_period": None},
        {"name": "c", "limit_kind": "full_set", "limit": None, "data_period": None},
        {"name": "d", "limit_kind": None, "limit": None, "data_period": None},
        {"name": "e", "limit_kind": "bogus", "limit": None, "data_period": None},
    ]
    r = judge(units, {"c": "已定性未测量"})
    bad = []
    if r["full_set_without_limit"]:
        bad.append("c 已豁免，不该红")
    if "d" not in r["unassigned"]:
        bad.append("d 缺字段应为 unassigned")
    if "e=bogus" not in r["bad_kind"]:
        bad.append("e 非法取值应报")
    if "b" not in r["full_set_units"]:
        bad.append("b 应进已定性清单")
    r2 = judge(units, {})
    if "c" not in r2["full_set_without_limit"]:
        bad.append("c 未豁免时必须红")
    if r2["rolling"] != 1:
        bad.append("rolling 计数错")
    # 基线：存量不报，但「新增」必须报
    r3 = judge(units, {}, baseline={"c"})
    if "c" not in r3["known_violations"]:
        bad.append("c 在基线里应为 known 而非 new")
    if r3["new_violations"]:
        bad.append("c 已在基线，new 应为空")
    r4 = judge(units, {}, baseline={"a"})
    if "c" not in r4["new_violations"]:
        bad.append("c 不在基线时必须进 new")
    if bad:
        print("selftest FAILED:\n" + "\n".join("  " + b for b in bad), file=sys.stderr)
        return 1
    print("selftest OK（9 断言：rolling 不报 / full_set 有 limit 不报 / "
          "full_set 缺 limit 报 / 豁免生效 / unassigned 单列 / 非法取值报 / "
          "基线内不报 new / 基线外进 new）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="check_limit_semantics")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    if not SPIDERS.is_dir():
        print("判不出：spiders/ 目录不可用", file=sys.stderr)
        return 2
    units = read_units()
    if not units:
        # 「一个亮着单元都没有」与「清单读不到」不许同形
        print("判不出：读不到任何亮着单元（清单目录空的？）", file=sys.stderr)
        return 2
    report = judge(units, read_exempt(), read_baseline())

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["new_violations"] or report["bad_kind"] else 0

    print(f"亮着单元 {report['total']}：rolling {report['rolling']}"
          f" / full_set {report['full_set']} / unassigned {len(report['unassigned'])}")
    for n in report["new_violations"]:
        print(f"  ✗ 新增 full_set 缺 limit（将被默认 100 截断）：{n}")
    for n in report["known_violations"]:
        print(f"  ! 存量 full_set 缺 limit（已知，基线内）：{n}")
    for n in report["bad_kind"]:
        print(f"  ✗ limit_kind 取值非法：{n}")
    for n in report["unassigned"]:
        print(f"  · unassigned（缺字段，单列不判）：{n}")
    print(f"已定性、未测量：{len(report['full_set_units'])} 个"
          f"（{', '.join(report['full_set_units']) or '—'}）")
    ok = not report["new_violations"] and not report["bad_kind"]
    print("结论：" + ("通过" if ok else "有红"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
