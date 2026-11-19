"""营销信号侦察（openspec: arm-marketing-signal-scout）。

**只读**：本模块不写任何表、不新建存储。它把已经验证过的三个信号源
归集成「实体 → 最近信号」视图，供营销侧读取。

验证依据（2026-11-19，50 条人工判读，命中率 56%）：

  | 源 | 命中率 | 结论 |
  |---|---|---|
  | ted-notices | 100% | 主干，具名采购方 + CPV + 金额 |
  | worldbank-procnotices | 100% | 主干，但实体是**采购方**（无供应商字段） |
  | nfra-penalty | 80% | 可用，需过滤自然人主体 |
  | samr-food-inspection | 0% | 不接入：通报级聚合，无企业名 |
  | usaspending-awards | 0% | 不接入：聚合到 (naive, naics, agency)，无中标企业 |

三条判据（详见 change reports/verdict-criteria.md）：
  ① 可归集到实体  ② 可陈述为事件  ③ 可对应产品面

**硬边界**：本模块只产出「参考信息」。它 MUST NOT 发起任何对外触达
（外呼、加好友、群发、自动回信）。信号只喂选题与人工判断。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

#: 已验证可用的信号源 → 其产品面归属（判据③）
USABLE_SOURCES: dict[str, tuple[str, str]] = {
    "ted-notices": ("wire", "欧盟招标：柏讯·行业数据"),
    "worldbank-procnotices": ("wire", "世行采购：柏讯·开放数据 / 行业数据"),
    "nfra-penalty": ("wire", "金融处罚：柏讯·企业风险 / 识律·合规检索"),
}

#: 判读阶段确认不接入的源（保留在册以便日后重议时有据）
EXCLUDED_SOURCES: dict[str, str] = {
    "samr-food-inspection": "通报级聚合，行内无被抽检企业名（企业清单在 spcjsac.gsxt.gov.cn）",
    "usaspending-awards": "聚合到 (award_type, naics_code, agency_name)，无中标企业",
}


@dataclass(frozen=True)
class Signal:
    """一条可用信号。四要素缺一不可（判据①②③ + 触达对象）。"""

    source: str
    entity: str          # 采购方 / 被处罚机构（可归集的实体）
    event: str           # 「某实体于某时发生某事」
    date: str            # 事件时点
    line: str            # 五线之一
    capability: str      # 该线的哪个能力回应
    detail: dict = field(default_factory=dict)

    def compact(self) -> str:
        """写进 contact 属性的紧凑串：类型|日期|来源。"""
        return f"{self.event[:40]}|{self.date}|{self.source}"


# ── 各源的归一化（只读，只调用既有 spider，不改它们） ──────────────────────


def _from_ted(rows: Iterable[dict]) -> list[Signal]:
    out = []
    for r in rows:
        buyer = (r.get("buyer") or "").strip()
        date = (r.get("date") or "").strip()
        if not buyer or not date:
            continue
        title = (r.get("title") or "").strip()
        out.append(
            Signal(
                source="ted-notices",
                entity=buyer,
                event=f"发布招标：{title[:60]}",
                date=date,
                line="wire",
                capability=USABLE_SOURCES["ted-notices"][1],
                detail={"cpv": r.get("cpv"), "value": r.get("value"), "url": r.get("url")},
            )
        )
    return out


def _from_worldbank(rows: Iterable[dict]) -> list[Signal]:
    out = []
    for r in rows:
        country = (r.get("country") or "").strip()
        proj = (r.get("project_name") or "").strip()
        date = (r.get("date") or "").strip()
        if not country or not date:
            continue
        # 实体 = 采购方（国家+项目）。该源无供应商字段，已在验证记录里核实。
        entity = f"{country}·{proj[:40]}" if proj else country
        out.append(
            Signal(
                source="worldbank-procnotices",
                entity=entity,
                event=f"采购公告：{(r.get('title') or '')[:60]}",
                date=date,
                line="wire",
                capability=USABLE_SOURCES["worldbank-procnotices"][1],
                detail={"project_id": r.get("project_id"), "url": r.get("url")},
            )
        )
    return out


#: 自然人判定：这条判据在 NFRA 的 2/10 不命中里验证过。
#: 营销对象是机构，不是个人；个人主体不作为触达对象。
#: 注意：这些标记只用于**整体判定**（整行是个人），剥离机构后缀在下面单独做。
_PERSONAL_MARKERS = ("员工", "（时任", "(时任", "时任")

#: 机构名后常见的「及相关责任人」后缀——剥掉后仍是机构，不能因此丢行。
_RESPONSIBLE_SUFFIXES = ("及相关责任人员", "及相关责任人")


def _from_nfra(rows: Iterable[dict]) -> list[Signal]:
    out = []
    for r in rows:
        party = (r.get("party") or "").strip()
        if not party:
            continue
        # 先剥离「及相关责任人」后缀：剩下的机构名才是实体。
        entity = party
        for suf in _RESPONSIBLE_SUFFIXES:
            entity = entity.split(suf)[0].strip()
        if not entity:
            continue
        # 再看剥离后的主体是否仍是自然人（员工/时任…）。整行是个人才丢。
        if any(m in entity for m in _PERSONAL_MARKERS):
            continue
        out.append(
            Signal(
                source="nfra-penalty",
                entity=entity,
                event=f"行政处罚：{(r.get('violation') or '')[:50]}",
                date=(r.get("doc_date") or r.get("notice_date") or r.get("date") or "").strip(),
                line="wire",
                capability=USABLE_SOURCES["nfra-penalty"][1],
                detail={"amount": r.get("amount"), "office": r.get("office")},
            )
        )
    return out


NORMALIZERS: dict[str, Callable[[Iterable[dict]], list[Signal]]] = {
    "ted-notices": _from_ted,
    "worldbank-procnotices": _from_worldbank,
    "nfra-penalty": _from_nfra,
}


def collect(sources: dict[str, Iterable[dict]] | None = None) -> list[Signal]:
    """把各源的原始行归一成 Signal 列表。

    ``sources`` 由调用方通过既有 spider 取到（本模块不自己发请求——
    取数策略属于 spider，归集属于这里）。缺省为空 = 返回空列表。
    """
    out: list[Signal] = []
    for name, rows in (sources or {}).items():
        fn = NORMALIZERS.get(name)
        if fn is None:
            continue  # 未验证的源不参与归集
        out.extend(fn(rows))
    return out


def by_entity(signals: Iterable[Signal]) -> dict[str, list[Signal]]:
    """实体 → 最近信号（按日期倒序）。只读视图。"""
    acc: dict[str, list[Signal]] = {}
    for s in signals:
        acc.setdefault(s.entity, []).append(s)
    for k in acc:
        acc[k].sort(key=lambda s: s.date, reverse=True)
    return acc


# ── 自检：判据与过滤在最小样本上成立 ────────────────────────────────────────


def _selftest() -> int:
    fails = []

    ted = _from_ted([{"buyer": "HSE", "date": "2026-11-18", "title": "Arch services", "cpv": "71200000"}])
    if len(ted) != 1 or ted[0].entity != "HSE":
        fails.append("ted 归一失败")

    wb = _from_worldbank([{"country": "Madagascar", "project_name": "ETIG", "date": "2026-11-18", "title": "x"}])
    if not wb or not wb[0].entity.startswith("Madagascar"):
        fails.append("worldbank 归一失败")

    nfra_rows = [
        {"party": "兴业银行股份有限公司", "violation": "数据安全", "date": "2026-11-18", "amount": 695},
        {"party": "某银行员工张三", "violation": "受贿", "date": "2026-11-18"},
        # 真源用的字段名是 doc_date（不是 date）——2026-11-19 pod 内实测发现，
        # 当时日期读成空串、compact() 出来是「类型||来源」。这条夹具钉住它。
        {"party": "中国人寿再保险有限责任公司及相关责任人员", "violation": "报送不真实", "doc_date": "2026-11-18"},
    ]
    nfra = _from_nfra(nfra_rows)
    if len(nfra) != 2:
        fails.append(f"nfra 自然人过滤失败：得到 {len(nfra)} 条，期望 2")
    if any("员工" in s.entity for s in nfra):
        fails.append("nfra 未过滤自然人")
    if any("及相关责任人员" in s.entity for s in nfra):
        fails.append("nfra 未剥离责任人后缀")
    # 日期不许为空（doc_date 那次回归的形状）
    if any(not s.date for s in nfra):
        fails.append(f"nfra 日期为空：{[s.entity for s in nfra if not s.date]}")

    # 未验证的源必须被忽略
    got = collect({"samr-food-inspection": [{"a": 1}], "usaspending-awards": [{"b": 2}]})
    if got:
        fails.append("未验证源未被忽略")

    # 按实体归集 + 日期倒序
    g = by_entity(ted + wb)
    if "HSE" not in g or "Madagascar·ETIG" not in g:
        fails.append("by_entity 归集失败")

    # compact 格式
    if ted and ted[0].compact().count("|") != 2:
        fails.append("compact 格式不是 类型|日期|来源")

    if fails:
        print("SELFTEST FAILED")
        for f in fails:
            print("  -", f)
        return 1
    print("SELFTEST OK（三条归一 + 自然人过滤 + 未验证源忽略 + 归集排序）")
    return 0




# ── 5.2 写入前的闭集校验（写入路径的守门，不是新存储） ──────────────────────

_SIGNAL_FORMAT_PARTS = 3  # 类型|日期|来源


def validate_signal_value(value: str) -> tuple[bool, str]:
    """校验 `signal` 属性值格式 `类型|日期|来源`。

    返回 (ok, reason)。非法即拒写并记录原因——与属性闭集
    (paas/lib/lead-attributes.json 的 `signal.format`) 同源。

    为什么校验在写入侧而不是读侧：写进去一条坏值，读面只会如实显示它，
    而下游按 `|` 拆分会静默得到错误的类型/日期。
    """
    v = (value or "").strip()
    if not v:
        return False, "empty"
    parts = v.split("|")
    if len(parts) != _SIGNAL_FORMAT_PARTS:
        return False, f"expect {_SIGNAL_FORMAT_PARTS} parts (类型|日期|来源), got {len(parts)}"
    kind, date, source = (p.strip() for p in parts)
    if not kind:
        return False, "empty event type"
    if not date:
        return False, "empty date"
    if source not in USABLE_SOURCES:
        # 未验证的源不许写入（与 collect() 的忽略规则一致）
        return False, f"source not in verified set: {source!r}"
    return True, "ok"


def _selftest_validate() -> int:
    fails = []
    ok, _ = validate_signal_value("发布招标|2026-11-18|ted-notices")
    if not ok:
        fails.append("合法值被判非法")
    for bad in ("", "只有两段|2026-11-18", "a|b|c|d", "类型||ted-notices",
                "类型|2026-11-18|samr-food-inspection"):
        ok, _ = validate_signal_value(bad)
        if ok:
            fails.append(f"非法值被放行: {bad!r}")
    if fails:
        print("VALIDATE SELFTEST FAILED")
        for f in fails:
            print("  -", f)
        return 1
    print("VALIDATE SELFTEST OK（合法放行 + 空/段数错/空字段/未验证源全拒）")
    return 0


if __name__ == "__main__":
    import sys

    if "--selftest" in sys.argv:
        rc = _selftest()
        rc |= _selftest_validate()
        raise SystemExit(rc)
    raise SystemExit(0)
