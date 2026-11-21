#!/usr/bin/env python3
"""一次性落 limit_kind（change fix-crawl-reliability-floor，人工判定表）。

判据（docs/limit-truncation-sweep-2026-11-11.md §2.3 口诀）：
    问「下一轮会不会取到这次被切掉的行」——会 → rolling，不会 → full_set

判定依据（来源标注在每个源旁）：
  * SWEEP   docs/limit-truncation-sweep-2026-11-11.md 已实测的全集型
  * YEAR    年鉴/年报/目录型（yearbook/catalog），固定历史集
  * DAY     日频行情/公告，滚动尾部
  * MONTH   月度序列，滚动尾部
  * UNASSIGNED 无法确证（carbon-monitor：65 万行索引，滚动/全集未定）

本脚本**只增不改**：文件里已有 `limit_kind:` 的行保持不动。
把文件当人工判定表提交，脚本只是把表写进 manifest。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

FULL_SET = {
    # SWEEP 实测全集型（limit 已覆盖）
    "usgs-nwis": "SWEEP: 全集 366，切片方向 [:limit]",
    "nasa-power": "SWEEP: 全集 732（1984 全年）",
    "afdc": "SWEEP: 全集 225（站点目录）",
    "boxofficemojo": "SWEEP: 全集 600（固定历史年）",
    "imf-pcps": "SWEEP: 全集 1,254（3 key × 418 期）",
    "cisa": "SWEEP: 全集 200",
    "jodi-gas": "SWEEP: 全集 16,556（固定 CSV）",
    "jodi-oil": "SWEEP: 全集 114,610（limit 130000）",
    "usaspending-awards": "SWEEP: 13 个月窗口固定回看，limit 8000",
    # YEAR（年鉴/年报/目录）
    "aiph-stats": "YEAR: 花卉统计年鉴，limit 300",
    "edgar-emissions": "YEAR: EDGAR 年度册（一次性发布）",
    "iea-ev": "YEAR: IEA EV 历史册",
    "moa-machinery": "YEAR: 农机统计年报",
    "mohurd-yearbook": "YEAR: 住建统计年鉴（SWEEP 已定性未测量，limit 6500）",
    "nrdl-national-catalog": "YEAR: 国家目录（固定集），limit 5000",
    # 目录/快照型（month/irregular）
    "education_cc_courses": "CATALOG: OCW + Open edX 编目，固定课程集",
    "education_oer_aggregators": "CATALOG: OER 机构聚合器（固定书/页集）",
    "education_openalex": "SNAPSHOT: OpenAlex 季度全量分区",
    "ourairports": "SNAPSHOT: 机场目录（固定集）",
    "mct-star-hotel": "CATALOG: 星级酒店月度册（2023Q2–2024Q3，源停更）",
    "mct-tourism-demand": "CATALOG: 旅游需求册（同上）",
    # 已定性但未测量（用户拍板：标 full_set，不补 limit）
    "gb-standards": "UNMEASURED: 国标分页翻库，全集未测（240s 未跑完）",
    "uk-hpi": "UNMEASURED: 1995 起的月度区域序列，按区域×月=固定历史集",
    "worldsteel": "UNMEASURED: 粗钢产量产品×地区固定表",
}

# 其余全部判为 rolling：日频行情/月度序列/新公告/新记录，逐轮累积历史。
UNASSIGNED = {
    "carbon-monitor": "65 万行索引，滚动/全集未定（设计文档 D3 留 unassigned）",
}

_KEY = re.compile(r"^limit_kind:")


def annotate(path: Path, kind: str, reason: str) -> bool:
    """在 manifest 顶层加一行 ``limit_kind``；已有则跳过。返回是否改动。"""
    text = path.read_text(encoding="utf-8")
    if re.search(r"^limit_kind:", text, re.M):
        return False
    # 插在 version 行之后（manifest 的固定头部位置）
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("version:"):
            lines.insert(i + 1, f"limit_kind: {kind}   # {reason}")
            break
    else:
        lines.insert(0, f"limit_kind: {kind}   # {reason}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return True


def main() -> int:
    changed = skipped = 0
    for p in sorted((REPO / "spiders").glob("*/manifest.yaml")):
        import yaml

        try:
            m = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            print(f"  ? 读不了 {p}: {exc}", file=sys.stderr)
            continue
        if not (m.get("schedule") or "").strip():
            continue  # 只处理亮着的
        name = m.get("name") or p.parent.name
        if name in UNASSIGNED:
            print(f"  · unassigned（不落字段）：{name} —— {UNASSIGNED[name]}")
            continue
        if name in FULL_SET:
            kind, reason = "full_set", FULL_SET[name]
        else:
            kind, reason = "rolling", "日频/月度序列，逐轮累积历史"
        if annotate(p, kind, reason):
            changed += 1
            print(f"  + {name:<28}{kind:<10}{reason}")
        else:
            skipped += 1
    print(f"\n改动 {changed}，已有字段跳过 {skipped}，unassigned {len(UNASSIGNED)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
