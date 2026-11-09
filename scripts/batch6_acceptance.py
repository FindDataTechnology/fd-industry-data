#!/usr/bin/env python3
"""批次六验收汇总生成器（add-banking-industry-sources 任务 7.3）。

对 20 个银行业单元逐个重放 golden，记录：run 结果（ok/rows/diffs）、实测时长、
golden 参数；再跑两条仓级门禁（validate_manifests / conformance_gate），把结果
写成 ``reports/batch6-acceptance-<YYYYMMDD>.md``，并在同目录落一份 JSON 供
``scripts/schedule_overlap_check.py --durations`` 复用实测时长。

用法：
  python3 scripts/batch6_acceptance.py                # 全量
  python3 scripts/batch6_acceptance.py --only nfra-penalty,chinabond-monthly
  python3 scripts/batch6_acceptance.py --skip-network  # 只出门禁段（不重跑爬虫）

只读 manifests/goldens，网络访问仅由各单元入口自身发起；写盘仅 reports/ 下两个文件。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import guard, samples  # noqa: E402

NEW_UNITS = [
    "nfra-stats-xls",
    "nfra-penalty",
    "pboc-credit-funds",
    "pboc-loan-usage",
    "chinamoney-shibor",
    "chinamoney-lpr",
    "chinamoney-ncd-curve",
    "shclearing-monthly",
    "chinabond-monthly",
    "chinabond-yield-curve",
    "shclearing-capital-notes",
    "cba-wealth-index",
    "chinawealth-reports",
    "yindeng-npl-transfer",
    "eastmoney-disclosure-plan",
    "ratings-ccxi",
    "dagong-ratings",
    "lh-ratings",
    "dfjc-ratings",
    "xsj-ratings",
]

#: 失败运行（出口封禁/退避堆积）不参与时长口径，用稳态估计值占排期窗口。
#: NFRA 两单元：站点被 403 封时实测是 6×90s 退避≈10 分钟，非正常运行；早间正常
#: 运行时约 1-3 分钟量级（8 页列表 + 逐文档详情/附件），取 3 分钟。
DURATION_ESTIMATES = {"nfra-stats-xls": 3, "nfra-penalty": 3}

#: spec「4 项留痕」——障碍与解锁条件逐字对齐
#: `openspec/changes/add-banking-industry-sources/specs/industry-source-expansion/spec.md`
#: Requirement「批次六 SHALL 留痕 4 项并写明解锁条件」。
LEDGER = [
    (
        "① chinawealth 产品查询 API",
        "RSA 公钥 + AES-256-ECB 握手已复现、服务端 429 频控，M 成本，非行业面核心",
        "频控配额可满足批量拉取（或渠道方给出免握手的只读通道）",
    ),
    (
        "② PBoC 货政报告与区域金融运行报告",
        "季度/年度 PDF；文本层与统计附表抽取策略待验证",
        "文本层 + 统计附表抽取策略验证通过（或上游给出结构化附表）",
    ),
    (
        "③ 银行资本债「不赎回」公告",
        "无干净结构化通道：上清所 TRS 全时段 0 条；上市银行侧由巨潮 searchkey/披露易类目覆盖，非上市银行挂起",
        "非上市银行侧出现稳定结构化披露通道",
    ),
    (
        "④ 中债-银登信贷资产价格指数疑停更",
        "1487 点止于 2024-12-12，接入前须复核活性",
        "复核确认停更则按历史快照口径登记、不注册成长序列",
    ),
]

#: 四项留痕的本期核对结论（不进入验收门槛，见 spec Requirement 的 Scenario）。
LEDGER_STATUS = {
    "④ 中债-银登信贷资产价格指数疑停更": "本期未接入、未注册成长序列（按 spec 口径仅登记，无序列可污染）",
}


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _replay(slug: str) -> dict:
    unit_dir = REPO / "spiders" / slug
    golden_files = sorted((unit_dir / "golden").glob("*.json"))
    if not golden_files:
        return {"slug": slug, "ok": False, "rows": 0, "diffs": ["no golden file"],
                "seconds": 0.0, "params": {}, "sample": ""}
    doc = json.loads(golden_files[0].read_text())
    schema_errors = samples.validate_sample(doc)
    started = time.monotonic()
    result = guard.replay_sample(unit_dir, doc)
    elapsed = time.monotonic() - started
    diffs = list(result.diffs) + [f"schema: {e}" for e in schema_errors]
    return {
        "slug": slug,
        "ok": bool(result.ok) and not schema_errors,
        "rows": int(result.rows),
        "diffs": diffs,
        "seconds": round(elapsed, 1),
        "params": doc.get("params") or {},
        "sample": str(doc.get("sample_id", "")),
    }


def _classify_violations(conf_out: str) -> tuple[list[str], list[str]]:
    """把 conformance_gate 输出拆成（本批 20 单元违规, 其余单元违规）。

    裸 `VIOLATION [...]` 行只到 `spiders/<slug>` 为止，故按 slug 归属；非本批
    单元的违规大多是并发会话在写的脚手架，报告必须点名而不是让验收表背锅。
    """
    mine: list[str] = []
    foreign: list[str] = []
    for line in conf_out.splitlines():
        if not line.startswith("VIOLATION"):
            continue
        hit = next((s for s in NEW_UNITS if f"spiders/{s}" in line), "")
        (mine if hit else foreign).append(line)
    return mine, foreign


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="逗号分隔的单元名子集")
    ap.add_argument("--skip-network", action="store_true", help="跳过 golden 重放")
    ap.add_argument("--reuse", default="", help="复用既有 JSON 的逐源结果，只重跑门禁")
    ap.add_argument("--external", default="",
                    help="外部出口核验结果 JSON：本地被出口封禁的单元用异地实测覆盖")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    args = ap.parse_args()

    wanted = [s.strip() for s in args.only.split(",") if s.strip()] or NEW_UNITS
    missing = [s for s in wanted if not (REPO / "spiders" / s).is_dir()]
    wanted = [s for s in wanted if s not in missing]

    results: list[dict] = []
    if args.reuse:
        results = json.loads(Path(args.reuse).read_text())["units"]
        print(f"复用 {len(results)} 条逐源结果（{args.reuse}）", flush=True)
    elif not args.skip_network:
        for idx, slug in enumerate(wanted, 1):
            print(f"[{idx}/{len(wanted)}] {slug} ...", flush=True)
            row = _replay(slug)
            results.append(row)
            print(
                f"    ok={row['ok']} rows={row['rows']} {row['seconds']}s"
                + (f" diffs={row['diffs'][:2]}" if row["diffs"] else ""),
                flush=True,
            )

    if args.external:
        # 本机出口被源站封禁时，用异地出口的实测结果覆盖**本地失败**的行；
        # 本地已 PASS 的行绝不覆盖（本地绿是更强的证据）。
        ext = json.loads(Path(args.external).read_text())
        for row in results:
            fix = ext.get(row["slug"])
            if fix and not row["ok"]:
                row.update({
                    "ok": bool(fix["ok"]),
                    "rows": int(fix.get("rows", 0)),
                    "diffs": list(fix.get("diffs") or []),
                    "external": fix.get("host", "external"),
                    "local_error": "；".join(row["diffs"])[:200],
                })
                print(f"  {row['slug']}: 用异地问核验结果覆盖本地失败"
                      f"（{fix.get('host')}，rows={row['rows']}）", flush=True)

    gate_rc, gate_out = _run([sys.executable, "scripts/validate_manifests.py"])
    conf_rc, conf_out = _run([sys.executable, "scripts/conformance_gate.py"])
    mine, foreign = _classify_violations(conf_out)

    out_dir = REPO / "reports"
    out_dir.mkdir(exist_ok=True)
    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "units": results,
        "missing_dirs": missing,
        "validate_manifests": {"rc": gate_rc, "output": gate_out},
        "conformance_gate": {"rc": conf_rc, "output": conf_out},
        "conformance_gate_batch6_violations": mine,
        "conformance_gate_foreign_violations": foreign,
    }
    json_path = out_dir / f"batch6-acceptance-{args.date}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    # 时长口径：只认**成功**运行的实测。失败运行的耗时是退避/重试堆积（如 NFRA 的
    # 6×90s WAF 退避 = 10 分钟），拿来占排期窗口会把整点时段全部锁死——必须用稳态
    # 估计值，且估计依据写进报告，不静默填。
    durations = {}
    for r in results:
        if r["ok"] and not r.get("external"):
            durations[r["slug"]] = max(1, int(round(r["seconds"] / 60 + 0.999)))
        else:
            durations[r["slug"]] = DURATION_ESTIMATES.get(r["slug"], 15)
    (out_dir / f"batch6-durations-{args.date}.json").write_text(
        json.dumps(durations, ensure_ascii=False, indent=2) + "\n"
    )

    failed = [r for r in results if not r["ok"]]
    lines = [
        f"# 批次六验收汇总（{args.date}）",
        "",
        f"生成时间：{payload['generated']}　生成器：`python3 fd-industry-data/scripts/batch6_acceptance.py`",
        "",
        "## 1. 逐源首爬与 golden 重放",
        "",
        "| # | 单元 | golden 参数 | rows | 时长 | 结果 |",
        "|---|---|---|---|---|---|",
    ]
    for idx, row in enumerate(results, 1):
        if row["ok"]:
            verdict = f"PASS（异地出口 {row['external']} 核验）" if row.get("external") else "PASS"
        else:
            verdict = "FAIL：" + "；".join(row["diffs"][:2])
        secs = "—（异地）" if row.get("external") else f"{row['seconds']}s"
        lines.append(
            f"| {idx} | `{row['slug']}` | `{json.dumps(row['params'], ensure_ascii=False)}` "
            f"| {row['rows']} | {secs} | {verdict} |"
        )
    if missing:
        lines += ["", "缺失目录（未交付）：" + "、".join(f"`{m}`" for m in missing)]
    lines += [
        "",
        "## 2. 仓级门禁",
        "",
        f"- `scripts/validate_manifests.py` → rc={gate_rc}："
        f"`{gate_out.splitlines()[-1] if gate_out else ''}`",
        f"- `scripts/conformance_gate.py` → rc={conf_rc}：**本批 20 单元违规 "
        f"{len(mine)} 条**；其余单元违规 {len(foreign)} 条（"
        f"`{conf_out.splitlines()[-1] if conf_out else ''}`）",
    ]
    if foreign:
        lines += [
            "",
            "其余单元违规（不属本 change，多为并发会话在写的脚手架；仅点名，不代改）：",
            "",
        ]
        lines += [f"- {v}" for v in foreign]
    lines += [
        "",
        "## 3. spec 四项留痕核对",
        "",
        "| 留痕项 | 障碍（spec 口径） | 解锁条件 | 本期状态 |",
        "|---|---|---|---|",
    ]
    for item, blocker, unlock in LEDGER:
        lines.append(f"| {item} | {blocker} | {unlock} | {LEDGER_STATUS.get(item, '仍成立（未解锁）')} |")
    lines += [
        "",
        "## 4. 本期已知状态与交付后修正",
        "",
        "**NFRA 两单元：本机出口被封，改用异地出口（cheap1）核验通过。**",
        "本机（Mac）打 `www.nfra.gov.cn/cbircweb/*` 全部 `HTTP 403`（单元同款头与 curl 默认头双测均 403，",
        "首页 200 → 出口 IP 级 WAF 拦截，非源结构变化）；同在 cheap1 上同一请求 200。",
        "故把单元目录 + `fd_industry_data` 同步到 cheap1（临时 `pip install --target pylibs lxml xlrd`）",
        "跑同一份 golden 重放：`nfra-stats-xls` → **`ok=True rows=800 diffs=[]`**，",
        "`nfra-penalty` → **`ok=True rows=60 diffs=[]`**。明细与出处见",
        "`reports/batch6-external-verify-20261108.json`（含 `_provenance`）。",
        "单元代码与 golden 一字未改；本地 FAIL 行的耗时是 6×90s 退避堆积，不计入排期口径。",
        "",
        "**`chinamoney-shibor` 交付后修正（默认收敛为增量）**：全史 20 年 = 21 片 ×2 通道",        "≈140 请求 /156s；`runner_cli` 不注入 manifest 默认值，若默认抓全史则日频每天重打",
        "同一请求指纹，正是本单元 README 记录的 403 封禁成因。故新增 `days` 参数并默认 45",
        "（≈30 交易日 = 1 片 = 2 请求，实测 13s / 208 行），`days=0` 保持全史回补能力",
        "（golden 用该路径验 2006-10-08 锚），平台首爬窗口 `window_start`/`window_end` 优先级最高。",
        "验收门槛不变：golden 重放仍为 `ok=True diffs=[]`。",
        "",
        "**排期时长口径（7.1 输入）**：`batch6-durations-<date>.json` 只对**成功**运行取实测；",
        "失败运行（如 NFRA 出口被封时 6×90s 退避 = 10 分钟）改用稳态估计 3 分钟，",
        "估计依据：早间正常运行时 8 页列表 + 逐文档详情/附件约 1-3 分钟量级。",
        "若拿退避耗时占窗口，13:18-13:21 整段会被 lock 死（实测重算一次即 103 条窗口相交）。",
        "",
        "## 5. 复现方式",
        "",
        "```bash",
        "cd fd-industry-data",
        "python3 scripts/batch6_acceptance.py                 # 逐源重放 + 门禁",
        "python3 scripts/batch6_acceptance.py --reuse reports/batch6-acceptance-<date>.json  # 只重跑门禁",
        "python3 scripts/schedule_overlap_check.py \\",
        "    --new <逗号分隔 20 单元> \\",
        "    --durations reports/batch6-durations-<date>.json",
        "```",
        "",
        f"合计 {len(results)} 源，PASS {len(results) - len(failed)}，FAIL {len(failed)}。",
    ]
    md_path = out_dir / f"batch6-acceptance-{args.date}.md"
    md_path.write_text("\n".join(lines) + "\n")
    print(f"\nwrote {md_path.relative_to(REPO)}")
    print(f"wrote {json_path.relative_to(REPO)}")
    print(f"gate rc={gate_rc} conf rc={conf_rc} failed={[r['slug'] for r in failed]}")
    return 0 if not failed and gate_rc == 0 and conf_rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
