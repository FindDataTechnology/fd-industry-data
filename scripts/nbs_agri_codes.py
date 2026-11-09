#!/usr/bin/env python3
"""NBS 农业年度指标「码表 → 序列清单」转换器（add-agriculture-industry-sources 任务 2.7 的配套工具）。

为什么要这个脚本：`data.stats.gov.cn` 的指标树取码口（`easyquery.htm?m=getTree…`）
已被站点 WAF 按 URL 规则封死（`reason:UrlACL`，换任何出口都一样；站点已改版为 SPA，
新后端 `/api/` 整个前缀被拦截页接管）。所以**码只能由人在浏览器里导出**，本脚本负责
把那份导出物变成可直接落 manifest 的序列清单，避免手抄出错、也避免任何人臆造 code。

输入（三选一，自动识别）：
  * 浏览器里复制的指标树 JSON（含 `id`/`name` 或 `code`/`name` 字段，任意层级）
  * 两列 CSV/TSV：`code,name`（表头可有可无）
  * 纯文本：每行 `<code><空白><名称>`

输出：每个指标一行（code / 名称 / 建议概念码 / 建议单位占位 / 年度频次），
并对每个 code 做**形态校验**（只认 `A0` 开头的 NBS 年度指标码形态，其余判为可疑并
单列），最后打印一份可直接抄进 manifest 的 `indicators:` 片段。

纪律：**不猜码、不补码**。形态不对的码一律进 `可疑` 清单而不是被修正；查不到就是查不到。

用法::

    python3 scripts/nbs_agri_codes.py ~/Downloads/nbs-tree.json
    python3 scripts/nbs_agri_codes.py ~/Downloads/tree.csv --filter 农产品
    python3 scripts/nbs_agri_codes.py tree.txt --emit-manifest > /tmp/indicators.yaml
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path

#: NBS 年度库（hgnd）指标码形态：A0 + 两位分类 + 若干位序号（大小写字母/数字混合）。
#: 只做「像不像」，不做「对不对」——对不对只有源站能定。
CODE_RE = re.compile(r"^A0[A-Za-z0-9]{2,}$")

#: 名称里出现这些词的，默认认为属于农业口径（可用 --filter 覆盖）。
AGRI_HINTS = ("农产品", "农业", "粮食", "谷物", "棉花", "油料", "糖料", "猪", "牛", "羊",
              "家禽", "水产", "造林", "农作物", "播种面积", "农业机械", "化肥", "农药")


def _walk_json(node, out: list[tuple[str, str]], depth: int = 0) -> None:
    """递归收集 (code, name)；不限定键名，兼容 id/code/zbcode。"""
    if depth > 8:
        return
    if isinstance(node, dict):
        code = node.get("code") or node.get("id") or node.get("zbcode") or node.get("value")
        name = node.get("name") or node.get("label") or node.get("cname") or node.get("text")
        if isinstance(code, str) and isinstance(name, str) and code.strip() and name.strip():
            out.append((code.strip(), name.strip()))
        for value in node.values():
            _walk_json(value, out, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _walk_json(item, out, depth + 1)


def _parse(path: Path) -> list[tuple[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    stripped = raw.lstrip()
    if stripped.startswith(("{", "[")):
        doc = json.loads(stripped)
        out: list[tuple[str, str]] = []
        _walk_json(doc, out)
        return out

    # 两列表格（逗号或制表符）
    first = raw.splitlines()[0] if raw.splitlines() else ""
    if "," in first or "\t" in first:
        delim = "," if "," in first else "\t"
        rows = list(csv.reader(io.StringIO(raw), delimiter=delim))
        out = []
        for row in rows:
            if len(row) < 2:
                continue
            code, name = row[0].strip(), row[1].strip()
            if code.lower() in ("code", "指标代码", "编码", "id"):
                continue  # 表头
            if code and name:
                out.append((code, name))
        return out

    # 纯文本：<code> <name>
    out = []
    for line in raw.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0]:
            out.append((parts[0], parts[1].strip()))
    return out


def _suggest_concept(code: str, name: str) -> str:
    """建议的概念码：nbs.agri.<slug>.annual。slug 只保留安全字符，仅作建议。"""
    slug = re.sub(r"[^0-9A-Za-z]+", "_", code).strip("_").lower()
    return f"nbs.agri.{slug}.annual"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path, help="浏览器导出物（JSON / CSV / 纯文本）")
    ap.add_argument("--filter", default="", help="只保留名称含该子串的指标")
    ap.add_argument("--all", action="store_true",
                    help="不按农业关键词过滤（默认只保留疑似农业口径）")
    ap.add_argument("--emit-manifest", action="store_true",
                    help="只打印 indicators: 片段，便于重定向进文件")
    args = ap.parse_args(argv)

    if not args.source.is_file():
        raise SystemExit(f"FATAL 找不到输入文件：{args.source}")
    pairs = _parse(args.source)
    if not pairs:
        raise SystemExit(
            "FATAL 没解析出任何 (code, name)。期望 JSON（含 id/code + name）、"
            "两列 CSV，或每行 `<code> <名称>` 的纯文本。"
        )

    seen: dict[str, str] = {}
    dup: list[str] = []
    for code, name in pairs:
        if code in seen:
            if seen[code] != name:
                dup.append(code)
            continue
        seen[code] = name

    if args.filter:
        kept = {c: n for c, n in seen.items() if args.filter in n}
    elif args.all:
        kept = dict(seen)
    else:
        kept = {c: n for c, n in seen.items() if any(h in n for h in AGRI_HINTS)}

    suspicious = sorted(c for c in kept if not CODE_RE.match(c))

    if args.emit_manifest:
        for code in sorted(kept):
            if code in suspicious:
                continue
            print(f"  - code: \"{code}\"")
            print(f"    name_zh: \"{kept[code]}\"")
            print(f"    concept: \"{_suggest_concept(code, kept[code])}\"")
            print(f"    unit: \"\"            # ← 单位必须按源站表头填，本脚本不猜")
            print(f"    frequency: annual")
        return 0

    print(f"输入：{args.source}")
    print(f"解析：{len(pairs)} 条 → 去重后 {len(seen)} 条 · 命中保留 {len(kept)} 条"
          f"（{'--all' if args.all else f'过滤={args.filter!r}' if args.filter else '农业关键词'}）")
    if dup:
        print(f"  ! 同码不同名（需人工确认，未自动合并）：{dup[:8]}")
    print()
    print(f"{'code':<12}{'建议概念码':<34}名称")
    for code in sorted(kept):
        mark = " " if code not in suspicious else "!"
        print(f"{mark}{code:<11}{_suggest_concept(code, kept[code]):<34}{kept[code]}")
    if suspicious:
        print(f"\n! 形态可疑（不是 A0 开头的年度指标码）：{len(suspicious)} 条 —— "
              "这些**不落 manifest**，先回源站核对；本脚本不修正码值")
        for code in suspicious[:10]:
            print(f"    {code}  {kept[code]}")
    print(f"\n合计可用 {len(kept) - len(suspicious)} 条。"
          f"下一步：`--emit-manifest > /tmp/indicators.yaml` 后按单位补全，再改 manifest。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
