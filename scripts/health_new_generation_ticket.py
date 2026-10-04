#!/usr/bin/env python3
"""生成单 helper（source-generation-flow）：把一条数据源侦察结论落成合法的
`kind=generate` 工单。**只写工单文件——不触网、不建单元目录。**

用法示例：
  python3 scripts/health_new_generation_ticket.py --slug nmc-weather \
    --source-url "https://www.nmc.cn/rest/weather?stationid=Wqsps" \
    --expectations "取气象站实况：温度/湿度/天气描述/风速；行字段建议 station/temperature/humidity/weather/wind" \
    --cadence hourly \
    --notes "侦察簿 W2-B：/rest/province 枚举站码（内部短码）；9999 为缺测占位；须带浏览器 UA"

生成后由维护者 SUBMIT 给 spider-heal（生成技能就绪后）；产物要求见 docs/health-loop.md「生成流」。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import ticket as tm  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--slug", required=True, help="新单元 slug（spiders/<slug>/，须匹配 ^[a-z0-9][a-z0-9-]*$）")
    ap.add_argument("--source-url", action="append", required=True, dest="source_urls",
                    help="数据源 URL（可重复）")
    ap.add_argument("--expectations", required=True, help="期望产出描述（行字段/覆盖范围）")
    ap.add_argument("--cadence", default="", help="可选节奏（daily/weekly/monthly/hourly…）——只写 brief，不写 schedule")
    ap.add_argument("--notes", default="", help="可选备注（侦察结论/坑位/容错规则）")
    ap.add_argument("--outdir", default=str(REPO / "reports" / "health-tickets"))
    args = ap.parse_args()

    unit = f"spiders/{args.slug}/"
    now = datetime.now(timezone.utc).isoformat()
    doc = tm.new_ticket(
        source=args.slug,
        unit=unit,
        kind="generate",
        brief={
            "source_urls": list(args.source_urls),
            "expectations": args.expectations.strip(),
            "cadence": args.cadence.strip(),
            "notes": args.notes.strip(),
        },
        evidence={
            "window": {"from": now, "to": now},
            "failing_runs": [],
            "last_success": None,
            "notes": "生成单（source-generation-flow）：目标单元尚不存在，按 brief 从零建立并交付 golden 样本",
        },
        verify_commands=[],
        golden_paths=[f"{unit}golden/"],
    )
    errors = tm.validate_ticket(doc)
    if errors:
        print("生成单校验失败：", errors)
        return 2
    fname = doc["ticket_id"] + ".yaml"
    doc["verify"]["commands"] = [
        f"python3 scripts/health_verify.py --ticket reports/health-tickets/{fname}"
    ]
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / fname).write_text(tm.dump_ticket(doc), encoding="utf-8")
    print(f"written: {outdir / fname}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())