#!/usr/bin/env python3
"""农业通用财报：从 GTA 面板取数并登记观测库（awaken-agriculture-report-ingest W5 的 GTA 通道）。

链路：windows-admin ``gta_panel``（只读 ``fdbiz_ro``，值单位**元**）
      → 本文件的指标→变量映射（口径钉死）
      → ``semantic_observations``（``source_used='gta-panel'``、粒度 ``year``）

为什么要有这条通道（2026-11-08 实测）：GTA 对农业 101 家**核心财务变量覆盖 101/101**
（营收/净利/总资产/所有者权益各 101 家，1992–2025，2023/2024 满覆盖），而库内 akshare
车道只覆盖 54 家；且两者**逐值一致**（牧原 2024 营收两边同为 1,379.47 亿）——
GTA 作为「按年、全覆盖」的第二车道，不替换 akshare 的季度车道，两者按 ``source_used`` 并存。

纪律（照抄银行线 ``register_bank_money_from_gta.py`` 的裁定，不重新发明）：
  · 面板滞后一财年（FY2025 全空）⇒ 该年**不写行**，不插值、不拿上年顶替；
  · ``entity_id`` 按 ``entity_type='stock' and code=…`` 解析（同一代码可能另有
    ``company`` 实体，按代码直接猜会写到错主体）；
  · ``value`` 是 varchar，按整数字符串写（避免 1e13 量级走科学计数法）；
  · 幂等靠 ``uq_sem_obs``（``on conflict do nothing``），重跑零重复；
  · 只写**已在册**的 concepts，缺一个即整体拒绝（不临时造概念）。

用法::

    python3 scripts/load_gta_agri_financials.py                     # dry-run，只算不写
    python3 scripts/load_gta_agri_financials.py --verify             # 与 akshare 年度值逐值对账
    python3 scripts/load_gta_agri_financials.py --write              # 写库
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WHITELIST = REPO / "docs" / "agri-report-whitelist.json"

#: 指标 → GTA 面板变量（名称取自 `variables` 表，口径见 label）。
#: 只收**通用三表科目**；GTA 没有分部/分产品/出栏量这类经营细分（实测 label 命中 0），
#: 那部分必须走年报正文抽取，不在本文件范围。
INDICATORS: list[dict] = [
    {"concept": "financials.revenue",      "gta_name": "b001101000", "label": "营业收入",     "unit": "元"},
    {"concept": "financials.net_income",   "gta_name": "b002000000", "label": "净利润",       "unit": "元"},
    {"concept": "financials.total_assets", "gta_name": "a001000000", "label": "资产总计",     "unit": "元"},
    {"concept": "financials.equity",       "gta_name": "a003000000", "label": "所有者权益合计", "unit": "元"},
    {"concept": "financials.operating_profit", "gta_name": "b001300000", "label": "营业利润", "unit": "元"},
    {"concept": "financials.total_liabilities", "gta_name": "a002000000", "label": "负债合计", "unit": "元"},
    {"concept": "financials.operating_cash_flow", "gta_name": "c001000000",
     "label": "经营活动产生的现金流量净额", "unit": "元"},
]
#: 同义变量（面板里同一科目重复登记，值相同）——只作备注，装载只用上面选的规范变量：
#:   负债合计另有 ``td``（panel_c2）；经营现金流另有 ``d000100000``（实测与 c001000000 同值）。
#: 资产负债率（financials.debt_ratio）**不装**：它是比率，库里已有 akshare 的 104/104 覆盖，
#: 且「负债合计 ÷ 资产总计」需与源站口径（是否含少数股东权益）逐家核对后才可派生，不在本轮臆造。
#:
#: 应收账款（financials.accounts_receivable）**撤出本车道**：面板的 `a001111000`「应收账款净额」
#: 与库内 akshare 的 `financials.accounts_receivable` **不是同一口径**——实测 000798 中水渔业 2022
#: GTA 13.08M vs akshare 194.98M（差 15 倍），应收票据仅 0.42M 凑不上，说明 akshare 那条含的科目
#: 更多（其他应收款/长期应收款/应收款项融资之类）。凑不出对应关系就不装：**宁可这一项仍走 akshare
#: 的 104 家覆盖，也不把错口径写进同一概念**（这正是 cnreport 车道踩过的坑）。
SOURCE_USED = "gta-panel"
GRANULARITY = "year"


def _gta_dsn(explicit: str | None) -> str:
    """显式 > GTA_PANEL_DSN > 由 FDBIZ_DOMAIN_PG_URL 换库名。

    business-mcp 的 DSN 指向 ``postgres`` 库，而 ``fdbiz_ro`` 在该库**没有 CONNECT 权限**；
    它授权的库是 ``gta_panel``，换掉路径段即可复用同一套凭据。
    """
    if explicit:
        return explicit
    if os.environ.get("GTA_PANEL_DSN"):
        return os.environ["GTA_PANEL_DSN"]
    for candidate in (REPO.parent / "fd-find-data-business-mcp" / ".env",
                      REPO / ".env"):
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                if line.startswith("FDBIZ_DOMAIN_PG_URL="):
                    base = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return f"{base.rsplit('/', 1)[0]}/gta_panel"
    raise SystemExit("缺连接串：给 --dsn、GTA_PANEL_DSN，或在 fd-find-data-business-mcp/.env 里有 FDBIZ_DOMAIN_PG_URL")


def _codes() -> list[str]:
    wl = json.loads(WHITELIST.read_text(encoding="utf-8"))
    return [r["code"] for r in wl["a_share"]]


def _fetch(dsn: str, codes: list[str], years: list[int]) -> list[dict]:
    import psycopg

    out: list[dict] = []
    with psycopg.connect(dsn, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute("set statement_timeout = 120000")
            for item in INDICATORS:
                col = item["gta_name"]
                cur.execute(
                    f'select id_org, year, "{col}" from panel '
                    f'where year = any(%s) and id_org = any(%s) and "{col}" is not null',
                    (years, codes))
                for firm, year, value in cur.fetchall():
                    out.append({"concept": item["concept"], "firm_id": firm.strip(),
                                "year": int(year), "value": float(value),
                                "unit": item["unit"], "label": item["label"]})
    return out


def _entity_map(conn, codes: list[str]) -> dict[str, int]:
    """代码 → 本体库 entities.id（只认 entity_type='stock'）。"""
    with conn.cursor() as cur:
        cur.execute("""select code, id from entities
                       where entity_type='stock' and code = any(%s)""", (codes,))
        return {code: eid for code, eid in cur.fetchall()}


def _concept_map(conn, wanted: list[str]) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("select code, id from concepts where code = any(%s)", (wanted,))
        return {code: cid for code, cid in cur.fetchall()}


def _verify_against_akshare(conn, rows: list[dict]) -> list[str]:
    """与库内 akshare 的**年度**值逐值对账，不一致即列出（不是覆盖，是交叉验证）。"""
    problems: list[str] = []
    with conn.cursor() as cur:
        for row in rows:
            cur.execute("""
                select o.value::float8 from semantic_observations o
                join concepts k on k.id = o.concept_id
                join entities e on e.id = o.entity_id
                where k.code = %s and o.source_used = 'akshare'
                  and e.entity_type = 'stock' and e.code = %s
                  and o.date = %s and o.granularity = 'year'
            """, (row["concept"], row["firm_id"], f"{row['year']}-12-31"))
            hit = cur.fetchone()
            if not hit:
                continue  # akshare 没有该年年度值，无可比对象（不算冲突）
            if hit[0] and abs(hit[0] - row["value"]) > max(1.0, abs(hit[0]) * 1e-6):
                problems.append(
                    f"{row['firm_id']} {row['year']} {row['concept']}: "
                    f"GTA {row['value']:.2f} vs akshare {hit[0]:.2f}")
    return problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=None, help="gta_panel 连接串（默认从 business-mcp .env 推导）")
    ap.add_argument("--years", default="2020-2024", help="年份区间，如 2020-2024")
    ap.add_argument("--verify", action="store_true", help="与 akshare 年度值逐值对账")
    ap.add_argument("--write", action="store_true", help="写库（默认 dry-run）")
    ap.add_argument("--out", default=str(REPO / "docs" / "gta-agri-financials.json"))
    args = ap.parse_args(argv)

    lo, _, hi = args.years.partition("-")
    years = list(range(int(lo), int(hi or lo) + 1))
    codes = _codes()
    dsn = _gta_dsn(args.dsn)
    print(f"取数：gta_panel → {len(codes)} 家 × {len(INDICATORS)} 指标 × {years[0]}–{years[-1]}")
    rows = _fetch(dsn, codes, years)
    if not rows:
        raise SystemExit("FATAL 面板返回 0 行（年份区间内无数据），不写库")

    body_dsn = os.environ.get("FD_OPEN_DATA_DSN")
    if not body_dsn:
        raise SystemExit("需要 FD_OPEN_DATA_DSN（先 source ~/.config/finddata/dashboards.env）")
    import psycopg

    with psycopg.connect(body_dsn, connect_timeout=20) as conn:
        ents = _entity_map(conn, codes)
        cons = _concept_map(conn, [i["concept"] for i in INDICATORS])
        missing_c = [i["concept"] for i in INDICATORS if i["concept"] not in cons]
        if missing_c:
            raise SystemExit(f"FATAL 概念未在册，拒绝写库：{missing_c}")
        unresolved = sorted({r["firm_id"] for r in rows} - set(ents))
        if unresolved:
            raise SystemExit(f"FATAL 这些代码在本体库找不到 stock 实体：{unresolved[:8]}")

        per_year: dict[int, int] = {}
        for row in rows:
            per_year[row["year"]] = per_year.get(row["year"], 0) + 1
        print("每年行数:", dict(sorted(per_year.items())))
        print(f"合计 {len(rows)} 行 · 覆盖 {len({r['firm_id'] for r in rows})} 家 · "
              f"指标 {len({r['concept'] for r in rows})} 个")

        if args.verify:
            problems = _verify_against_akshare(conn, rows)
            print(f"\n与 akshare 年度值对账：冲突 {len(problems)} 处")
            for p in problems[:10]:
                print("  ✗", p)
            if problems:
                return 1
            print("  逐值一致（无可比对象的年份跳过，不计冲突）")

        if not args.write:
            print("\nDRY-RUN：未写库（加 --write 落库）")
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"indicators": INDICATORS, "years": years,
                                       "rows": len(rows)}, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            print(f"写出摘要 {out}")
            return 0

        ins = skipped = 0
        with conn.cursor() as cur:
            for row in rows:
                cur.execute("""
                    insert into semantic_observations
                      (concept_id, entity_type, entity_id, date, granularity, value, unit,
                       source_used, fetched_at, obs_month)
                    values (%s, 'stock', %s, %s, %s, %s, %s, %s, now(), null)
                    on conflict on constraint uq_sem_obs do nothing
                """, (cons[row["concept"]], ents[row["firm_id"]], f"{row['year']}-12-31",
                      GRANULARITY, str(int(round(row["value"]))), row["unit"], SOURCE_USED))
                if cur.rowcount:
                    ins += 1
                else:
                    skipped += 1
        conn.commit()
        print(f"\n写入完成：inserted={ins} · conflict_skipped={skipped}（幂等）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
