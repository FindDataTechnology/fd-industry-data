#!/usr/bin/env python3
"""农业「财务细分」登记：把年报抽取结果落成 segment 实体 + 概念 + 观测（B 单 W3 / 4.1-4.5）。

## 为什么用实体承载「分部」而不是概念

观测表的唯一键是 `uq_sem_obs (concept_id, entity_type, entity_id, date, granularity,
source_used)`，**没有自由维度列**。一家公司同年有 5–8 个分部，若都塞进同一个
(概念, 实体, 日期) 会撞键；而给每个分部单独建概念又会让概念空间被自由文本命名炸开
（各家用词不同：养殖业务 / 生猪 / 猪产业）。

因此：**分部 = 实体**（`entity_type='segment'`），概念保持通用维度
（收入 / 收入占比 / 同比 / 销售量 …）。这样：

* 分部原文保留在 `entities.name_zh` + `metadata_json.raw_name`，**不做同义词归并**；
* 不同分部是不同实体，唯一键天然不冲突；
* 同一分部跨年追加到同一实体，时间序列自然成列。

## 概念族（本脚本自建，六字段齐备）

| code | 含义 | 单位 | 频次 |
|---|---|---|---|
| `agri.segment.revenue` | 分部营业收入 | 元 | yearly |
| `agri.segment.revenue_share` | 分部收入占比（报告自印） | % | yearly |
| `agri.segment.revenue_yoy` | 分部收入同比（报告自印） | % | yearly |
| `agri.volume.sales` | 销售量 | 见每条观测的 unit | yearly |
| `agri.volume.production` | 生产量 | 同上 | yearly |
| `agri.volume.inventory` | 库存量 | 同上 | yearly |

## 纪律

1. **只登记抽取器判为 `complete` 的维度**；`complete=false`（疑排版不匹配）整段跳过并留痕，
   绝不把「只抽到 1 行」当作「该公司只有 1 个业务」。
2. **只写报告印出的数**：缺项不补零、不推算；占比与同比用报告自印值，不自己算。
3. **实物量单位逐条落 `observations.unit`**（万头/吨/万羽…各不相同），
   概念层 `unit` 留空并在 caveat 说明「单位见每条观测，不可跨单位聚合」。
4. 幂等：实体按 `(entity_type, code)` 唯一、观测按 `uq_sem_obs` 幂等，重跑零重复。

## 用法

    python3 scripts/register_agri_segments.py --dry-run          # 默认
    python3 scripts/register_agri_segments.py --apply            # 落库
    python3 scripts/register_agri_segments.py --apply --verify    # 落库并与总营收对账
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
#: 默认读**全量**产物。历史坑：这里曾默认指向 `docs/agri-segment-extract.json`
#: （4 家试点的旧文件），重跑登记只回补那 4 家，其余 97 家的缺行永远补不上——
#: 实测导致 18 个分部实体「只有收入占比没有收入金额」。默认必须指全量，
#: 试跑用 `--extract docs/agri-segment-extract.json` 显式指定。
EXTRACT = REPO / "docs" / "agri-segment-extract-2024-all.json"
#: 白名单家数：产物覆盖家数明显少于它时**大声警告**，不静默按小样本登记。
WHITELIST = REPO / "docs" / "agri-report-whitelist.json"

SOURCE_USED = "cn-report"
DIM_CODE = {"分行业": "ind", "分产品": "prod", "分地区": "reg", "分销售模式": "sm"}
VOL_DIM = "vol"

#: 概念族：code → (name_zh, name_en, unit, measure, frequency, category)
CONCEPTS: dict[str, tuple[str, str, str, str, str, str]] = {
    "agri.segment.revenue": (
        "分部营业收入", "Segment revenue", "元", "value", "yearly", "Income Statement"),
    "agri.segment.revenue_share": (
        "分部收入占比", "Segment revenue share", "%", "ratio", "yearly", "Income Statement"),
    "agri.segment.revenue_yoy": (
        "分部收入同比", "Segment revenue YoY", "%", "ratio", "yearly", "Income Statement"),
    "agri.volume.sales": (
        "分部销售量", "Segment sales volume", "", "value", "yearly", "Operating Data"),
    "agri.volume.production": (
        "分部生产量", "Segment production volume", "", "value", "yearly", "Operating Data"),
    "agri.volume.inventory": (
        "分部库存量", "Segment inventory volume", "", "value", "yearly", "Operating Data"),
}
CONCEPT_CAVEAT = (
    "口径：全部取自已披露年报正文（分行业/分产品/分地区表、实物销售量表），报告自印值，"
    "不推算、不补零；分部名为公司原文，未做同义词归并（各家用词不同）。"
    "收入单位元、占比与同比为报告自印百分数。实物量单位逐条不同（万头/吨/万羽等），"
    "见每条观测的 unit，**不可跨单位聚合**。"
)

ITEM_TO_CONCEPT = {"销售量": "agri.volume.sales", "生产量": "agri.volume.production",
                   "库存量": "agri.volume.inventory"}

#: 叙述式经营指标（4.2）→ 概念。单位随公司而变（万头/万吨/元每公斤），逐条落 observations.unit。
NARRATIVE_CONCEPTS: dict[str, tuple[str, str, str, str, str]] = {
    "free_cost":      ("养殖完全成本", "Farming full cost", "元/公斤", "value", "Operating Data"),
    "avg_price":      ("商品猪销售均价", "Hog average selling price", "元/公斤", "value", "Operating Data"),
    "sow_inventory":  ("能繁母猪存栏", "Breeding sow inventory", "万头", "value", "Operating Data"),
    "feed_sales":     ("饲料销量", "Feed sales volume", "万吨", "value", "Operating Data"),
    "seed_area":      ("制种面积", "Seed production area", "万亩", "value", "Operating Data"),
    "planted_area":   ("播种/种植面积", "Planted area", "万亩", "value", "Operating Data"),
    "batch_release":  ("批签发数量", "Batch release volume", "", "value", "Operating Data"),
}
for _k in NARRATIVE_CONCEPTS:
    _zh, _en, _u, _m, _cat = NARRATIVE_CONCEPTS[_k]
    CONCEPTS[f"agri.ops.{_k}"] = (_zh, _en, "", _m, "yearly", _cat)


def _segment_code(stock: str, dim: str, raw_name: str) -> str:
    """稳定可复算的分部实体码：<股票>.<维度>.<原名短哈希>（原名见 name_zh/raw_name）。"""
    digest = hashlib.sha1(raw_name.encode("utf-8")).hexdigest()[:8]
    return f"{stock}.{dim}.{digest}"


def _entity_type(code: str) -> str:
    """概念的实体类型**按码分**，不能一律 segment。

    实测缺陷：`agri.ops.*`（完全成本/饲料销量/能繁存栏…）是**公司级**经营指标，
    观测全部写在 `entity_type='stock'` 上，但概念被硬写成 `'segment'` ——
    概念与观测类型不一致，任何按实体类型读的消费方（含 plan_crawl）都会整批漏读
    （实测 plan_crawl 直接回 "entity_type mismatch: concept is segment, scope is stock"）。
    """
    return "stock" if code.startswith("agri.ops.") else "segment"


def _ensure_concepts(cur, apply: bool) -> tuple[dict[str, int], list[str]]:
    cur.execute("select code, id from concepts where code = any(%s)", (list(CONCEPTS),))
    have = {code: cid for code, cid in cur.fetchall()}
    missing = [c for c in CONCEPTS if c not in have]
    problems: list[str] = []
    if missing and not apply:
        problems.append(f"概念未在册（--apply 时会按六字段创建）：{missing}")
    if apply:
        for code in missing:
            name_zh, name_en, unit, measure, freq, cat = CONCEPTS[code]
            if not all([name_zh, name_en, freq, cat]):
                raise SystemExit(f"FATAL 概念 {code} 六字段不全，拒绝创建")
            cur.execute("""
                insert into concepts (code, name_zh, name_en, category, unit, measure,
                                      frequency, entity_type, source, verified, deprecated)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,true,false)
                on conflict on constraint uq_concept_identity do nothing
                returning id
            """, (code, name_zh, name_en, cat, unit, measure, freq,
                  _entity_type(code), SOURCE_USED))
            row = cur.fetchone()
            if row:
                have[code] = row[0]
            else:  # 已存在同身份概念
                cur.execute("""select id from concepts where code=%s
                               and entity_type=%s order by id limit 1""",
                            (code, _entity_type(code)))
                have[code] = cur.fetchone()[0]
        print(f"  概念：创建 {len(missing)} 个（{missing}）")
    return have, problems


def _ensure_segment_entity(cur, stock: str, dim: str, raw_name: str,
                           apply: bool) -> tuple[int | None, str]:
    code = _segment_code(stock, dim, raw_name)
    cur.execute("select id from entities where entity_type='segment' and code=%s", (code,))
    row = cur.fetchone()
    if row:
        return row[0], code
    if not apply:
        return None, code
    meta = {"parent_stock_code": stock, "dimension": dim, "raw_name": raw_name,
            "list": "agriculture-segment", "source": SOURCE_USED}
    cur.execute("""
        insert into entities (entity_type, code, name_zh, metadata_json, updated_at)
        values ('segment', %s, %s, %s::jsonb, now())
        on conflict (entity_type, code) do nothing
        returning id
    """, (code, raw_name, json.dumps(meta, ensure_ascii=False)))
    got = cur.fetchone()
    if got:
        return got[0], code
    cur.execute("select id from entities where entity_type='segment' and code=%s", (code,))
    return cur.fetchone()[0], code


def _fmt_value(value: float, unit: str) -> str:
    """按口径格式化数值字符串（`value` 是 varchar，写错会一路带到看板）。

    **金额固定两位**：报告原文就是两位小数；用 `f"{value:.6f}"` 会把 float 噪声
    印出来（实测牧原养殖业务写成 `136229329743.490005`、抵消行写成
    `-24435772335.759998`）。百分比同两位，其余保持六位去尾零。
    """
    if unit in ("元", "%"):
        return f"{value:.2f}"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _write_obs(cur, concept_id: int, entity_id: int, date: str, value: float,
               unit: str, *, entity_type: str = "segment") -> int:
    cur.execute("""
        insert into semantic_observations
          (concept_id, entity_type, entity_id, date, granularity, value, unit,
           source_used, fetched_at, obs_month)
        values (%s,%s,%s,%s,'year',%s,%s,%s,now(),null)
        on conflict on constraint uq_sem_obs do nothing
    """, (concept_id, entity_type, entity_id, date,
          _fmt_value(value, unit), unit, SOURCE_USED))
    return cur.rowcount


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--extract", default=str(EXTRACT))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="默认行为，显式写上更清楚")
    args = ap.parse_args(argv)
    apply = args.apply

    src = Path(args.extract)
    if not src.is_file():
        raise SystemExit(f"FATAL 缺抽取结果 {src}（先跑 scripts/agri_segment_extract.py）")
    doc = json.loads(src.read_text(encoding="utf-8"))
    # 产物覆盖度自检：小样本产物（试点/局部重抽）登记进全量库正是上一轮数据丢失的来源，
    # 故显式提示，并让人看清「命令行里少了 --extract」而不是把它当成数据现状。
    companies = doc.get("companies") or []
    try:
        n_wl = len(json.loads(WHITELIST.read_text(encoding="utf-8")).get("a_share") or [])
    except Exception:  # noqa: BLE001 —— 白名单缺失不应阻断登记
        n_wl = 0
    if n_wl and len(companies) < n_wl:
        print(f"  ⚠ 产物只含 {len(companies)} 家（白名单 {n_wl} 家）——"
              f"本次只登记这些公司，其余不会被动到；若想全量请用默认产物或 --extract 指定全量文件")
    dsn = os.environ.get("FD_OPEN_DATA_DSN")
    if not dsn:
        raise SystemExit("需要 FD_OPEN_DATA_DSN（先 source ~/.config/finddata/dashboards.env）")
    import psycopg

    stats = {"entities": 0, "obs": 0, "skipped_dim": 0, "skipped_row": 0,
             "no_stock": 0, "verify_flags": []}
    with psycopg.connect(dsn, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cons, problems = _ensure_concepts(cur, apply)
            for p in problems:
                print(f"  ! {p}")

            for company in doc.get("companies") or []:
                stock, year = company["code"], int(company["year"])
                cur.execute("""select id from entities
                               where entity_type='stock' and code=%s""", (stock,))
                hit = cur.fetchone()
                if not hit:
                    stats["no_stock"] += 1
                    print(f"  ! {stock}: 本体库无该 stock 实体，跳过")
                    continue
                stock_id = hit[0]
                date = f"{year}-12-31"

                cur.execute("""
                    select sum(v) from (
                      select distinct on (o.entity_id) o.value::float8 v
                      from semantic_observations o
                      join concepts k on k.id=o.concept_id
                      join entities e on e.id=o.entity_id
                      where k.code='financials.revenue' and e.entity_type='stock'
                        and e.code=%s and o.date=%s
                        and o.source_used in ('gta-panel','akshare')
                      order by o.entity_id,
                               case o.source_used when 'gta-panel' then 0 else 1 end
                    ) x
                """, (stock, date))
                total_rev = cur.fetchone()[0]

                for dim_name, block in (company.get("by_dimension") or {}).items():
                    if not block.get("complete"):
                        stats["skipped_dim"] += 1
                        print(f"  – {stock} {year} {dim_name}: 抽取不完整（{block.get('note')}），跳过")
                        continue
                    # 数量级自检：该维度合计与公司总营收严重不符 → 整段拒收。
                    # 分地区应基本等于总营收（分区互斥）；分行业/分产品含内部抵消，放宽。
                    seg_sum = sum(r.get("revenue") or 0 for r in block["rows"])
                    if total_rev and seg_sum:
                        ratio = seg_sum / total_rev
                        lo, hi = (0.6, 1.6) if dim_name == "分地区" else (0.3, 3.0)
                        if not (lo <= ratio <= hi):
                            stats["skipped_dim"] += 1
                            stats["verify_flags"].append(
                                f"{stock} {year} {dim_name}: 合计/总营收 = {ratio:.2f}，"
                                f"超出 [{lo},{hi}]，整段拒收（疑抽错）")
                            print(f"  – {stock} {year} {dim_name}: 数量级不符 "
                                  f"({ratio:.2f})，整段拒收")
                            continue
                    dim = DIM_CODE[dim_name]
                    for row in block["rows"]:
                        raw = (row.get("name") or "").strip()
                        if not raw:
                            stats["skipped_row"] += 1
                            continue
                        eid, code = _ensure_segment_entity(cur, stock, dim, raw, apply)
                        stats["entities"] += 1          # dry-run 也计「计划创建/命中」
                        if eid is None and not apply:
                            stats["obs"] += sum(
                                1 for f in ("revenue", "revenue_pct", "yoy_pct")
                                if row.get(f) is not None)
                            continue
                        if eid is None:
                            continue
                        for field, concept in (("revenue", "agri.segment.revenue"),
                                               ("revenue_pct", "agri.segment.revenue_share"),
                                               ("yoy_pct", "agri.segment.revenue_yoy")):
                            val = row.get(field)
                            if val is None:
                                continue
                            unit = "%" if field.endswith("pct") else "元"
                            if apply:
                                stats["obs"] += _write_obs(
                                    cur, cons[concept], eid, date, float(val), unit)

                for nar in company.get("narrative") or []:
                    code = f"agri.ops.{nar.get('key')}"
                    if code not in cons or nar.get("value") is None:
                        continue
                    stats["entities"] += 1
                    if not apply:
                        stats["obs"] += 1
                        continue
                    stats["obs"] += _write_obs(
                        cur, cons[code], stock_id, date, float(nar["value"]),
                        (nar.get("unit") or "").strip(),
                        entity_type="stock")

                for item in company.get("physical_volume") or []:
                    concept = ITEM_TO_CONCEPT.get(item.get("item") or "")
                    raw = (item.get("name") or "").strip()
                    if not concept or not raw or item.get("current") is None:
                        stats["skipped_row"] += 1
                        continue
                    eid, _code = _ensure_segment_entity(cur, stock, VOL_DIM, raw, apply)
                    stats["entities"] += 1
                    if eid is None and not apply:
                        stats["obs"] += 1
                        continue
                    if eid is None:
                        continue
                    if apply:
                        stats["obs"] += _write_obs(cur, cons[concept], eid, date,
                                                   float(item["current"]),
                                                   (item.get("unit") or "").strip())

            if apply:
                conn.commit()

            if args.verify:
                # 分地区应把全国切成互斥块：其收入合计应接近公司总营收。
                # 分行业含内部抵消行，不作硬判据。偏差 >20% 只报警，不改数。
                for company in doc.get("companies") or []:
                    stock, year = company["code"], int(company["year"])
                    reg = (company.get("by_dimension") or {}).get("分地区") or {}
                    if not reg.get("complete"):
                        continue
                    seg_sum = sum(r["revenue"] for r in reg["rows"] if r.get("revenue"))
                    # 两车道并存时**只取一条**（GTA 优先）：直接 sum 会把总收入翻倍，
                    # 让正确的分部表看起来只有 50%（实测 000702/002100 就是这样被误报）。
                    cur.execute("""
                        select sum(v) from (
                          select distinct on (o.entity_id) o.value::float8 v
                          from semantic_observations o
                          join concepts k on k.id=o.concept_id
                          join entities e on e.id=o.entity_id
                          where k.code='financials.revenue' and e.entity_type='stock'
                            and e.code=%s and o.date=%s
                            and o.source_used in ('gta-panel','akshare')
                          order by o.entity_id,
                                   case o.source_used when 'gta-panel' then 0 else 1 end
                        ) x
                    """, (stock, f"{year}-12-31"))
                    total = cur.fetchone()[0]
                    if not total or not seg_sum:
                        continue
                    ratio = seg_sum / total
                    if not (0.8 <= ratio <= 1.2):
                        stats["verify_flags"].append(
                            f"{stock} {year}: 分地区合计 {seg_sum:,.0f} / 总营收 {total:,.0f}"
                            f" = {ratio:.2f}（>20% 偏差，疑抽取错位）")
                print(f"\n对账：分地区合计 vs 总营收，超 20% 偏差 {len(stats['verify_flags'])} 条")
                for f in stats["verify_flags"][:8]:
                    print("  ⚠", f)

    # dry-run 里 `obs` 是**计划条数**，不是写入条数；混用一个措辞会让人以为「写了 22 条」。
    obs_label = "观测写入" if apply else "观测计划"
    print(f"\n{'APPLIED' if apply else 'DRY-RUN'}：分部实体 {stats['entities']} · "
          f"{obs_label} {stats['obs']} · 跳过维度 {stats['skipped_dim']} · "
          f"跳过行 {stats['skipped_row']} · 无 stock 实体 {stats['no_stock']}")
    if not apply:
        print("（未写库；加 --apply 落库）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
