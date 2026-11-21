#!/usr/bin/env python3
"""CaaS 投递契约的跨面合规闸（openspec: add-caas-delivery-contract 3.1/3.2）。

**为什么是一个会红的闸**：本能力最容易的失败方式是「各面各说各话」——四个面
各自维护一份形态清单，看起来都能跑，实际互不相认。靠人记是记不住的（2026-11-17
的教训：门从未执行，而输出和「执行且通过」一模一样）。所以这里每条判据都有明确的
失败分支，且闸自身有负向样本：喂一份刻意写坏的实现必须红。

四项判据：

  ① 权威表副本一致       —— 每个消费方仓内的 caas-forms.json 必须与权威文件同 sha256
  ② 无第二份形态清单     —— 实现文件里不得硬编码形态名字面量清单
  ③ 回执可投影三值       —— 各面必须能产出 delivered/consumed/failed 三值之一
  ④ 投递有幂等键         —— 各面的投递路径必须能给出 correlation/idem

用法::

    python3 scripts/check_caas_conformance.py            # 跑全部判据
    python3 scripts/check_caas_conformance.py --json     # 机读
    python3 scripts/check_caas_conformance.py --fixtures # 只用内置负向样本自检（闸自检）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WORKSPACE = REPO.parent

# VAAS 是工作区**之外**的独立仓（`~/VAAS`，远程 FindDataTechnology/fd-vaas-skills），
# 不是 finddata/ 的子目录。路径不写死成 WORKSPACE/"VAAS"，否则闸会在一个空地址上
# 判绿（没人消费的副本 = 没有副本）。
VAAS_ROOT = Path.home() / "VAAS"

AUTHORITY = WORKSPACE / "openspec" / "specs" / "caas-delivery" / "caas-forms.json"

# 每个消费方仓内的权威表副本（镜像独立构建，不能跨仓读文件）
COPIES = {
    "paas": WORKSPACE / "paas" / "lib" / "caas-forms.json",
    "fd-industry-data": REPO / "fd_industry_data" / "alerts_core" / "caas-forms.json",
    "vaas": VAAS_ROOT / "mcp-server" / "mcp_server" / "caas-forms.json",
}

# 参与判据②的实现文件：形态名一旦被硬编码成清单，即为第二份口径
FORM_LIST_SCAN = {
    "paas/lib/caas-envelope.js": WORKSPACE / "paas" / "lib" / "caas-envelope.js",
    "fd-industry-data/alerts_core/delivery.py": REPO / "fd_industry_data" / "alerts_core" / "delivery.py",
    "fd-industry-data/alerts_core/caas.py": REPO / "fd_industry_data" / "alerts_core" / "caas.py",
    "vaas/mcp_server/caas.py": VAAS_ROOT / "mcp-server" / "mcp_server" / "caas.py",
}


@dataclass
class Finding:
    check: str
    ok: bool
    detail: str

    def as_dict(self) -> dict:
        return {"check": self.check, "ok": self.ok, "detail": self.detail}


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    # Surfaces this checkout cannot see (the authority and the other consumer
    # repos live outside a single-repo CI clone). Recorded rather than failed:
    # a verdict that silently omits them would be the "judge says green because
    # it never looked" shape this project keeps getting bitten by.
    uncovered: list[str] = field(default_factory=list)

    def add(self, check: str, ok: bool, detail: str) -> None:
        self.findings.append(Finding(check, ok, detail))

    @property
    def ok(self) -> bool:
        return all(f.ok for f in self.findings)

    @property
    def partial(self) -> bool:
        """True when some surfaces were skipped — the verdict is NOT complete."""
        return bool(self.uncovered)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "partial": self.partial,
            "uncovered": list(self.uncovered),
            "findings": [f.as_dict() for f in self.findings],
            "failed": [f.check for f in self.findings if not f.ok],
        }


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _forms_from(p: Path) -> list[str]:
    return list(json.loads(p.read_text(encoding="utf-8"))["forms"].keys())


def check_authority(report: Report, authority: Path = AUTHORITY, copies: dict[str, Path] | None = None) -> None:
    """① 副本一致。

    In a single-repo CI checkout the authority lives in ANOTHER repo (the
    workspace root's openspec/) and the other consumers are not present at all.
    Reporting that as red would make the gate permanently red; reporting it as
    green would be a lie. So an absent surface is recorded as UNCOVERED and the
    report stops claiming to be a full verdict (see `run()` / `--covered`).
    """
    copies = COPIES if copies is None else copies
    if not authority.exists():
        # 区分两件不同的事：
        #   · 本仓被单独 clone（根 openspec/ 整个不存在）→ 合法未覆盖，判绿
        #   · 工作区在、根 openspec/ 也在，唯独权威文件没了 → 真缺失，判红。
        # 第一版把两者都当「未覆盖」，于是 2026-11-18 权威文件随 change 归档之后，
        # 这道闸连续三天对权威表零约束却一直报绿（ci-gate-never-ran-looks-green）。
        if (WORKSPACE / "openspec").is_dir():
            report.add(
                "authority",
                False,
                f"权威文件缺失：{authority}（根 openspec/ 存在，说明是本工作区而非独立 clone）",
            )
        else:
            report.uncovered.append("authority")
            report.add("authority", True, f"未覆盖：权威文件不在本检出（{authority}）")
        return
    want = _sha(authority)
    report.add("authority", True, f"权威 {authority.name} sha={want[:12]}")
    for name, path in copies.items():
        if not path.exists():
            report.uncovered.append(f"copy:{name}")
            report.add("copy", True, f"{name}: 未覆盖（副本不在本检出）")
            continue
        got = _sha(path)
        report.add("copy", got == want, f"{name}: {'一致' if got == want else f'漂移 {got[:12]} != {want[:12]}'}")


_FORM_WORDS = ("text", "chart", "file", "asset", "link")


def check_no_parallel_form_list(report: Report, scan: dict[str, Path] | None = None,
                                forms: list[str] | None = None) -> None:
    """② 无第二份形态清单。

    判定"第二份权威清单"而不是"出现形态名"：形态名是必读的标识符，出现是正常的。
    判据是**同一处的字面量里出现 3 个以上不同形态名**——那才是把权威表抄进了代码。
    调用方挑 1–2 个首选形态（``resolveForm(["chart","link"], caps)``）是正常用法，
    不该被误判。

    ponytail: 3 是拍的阈值。若某天真有合法调用需要一次点名 3 个形态，把它换成
    「须等于权威表全集」的更严格判据。
    """
    scan = FORM_LIST_SCAN if scan is None else scan
    wanted = set(forms or _FORM_WORDS)
    alternation = "|".join(sorted(wanted))
    # 匹配 [ ... ] 或 ( ... ) 里用逗号分隔的形态名字面量（带或不带引号）
    literal = re.compile(
        r"[\[(]\s*[\"']?(?:" + alternation + r")[\"']?\s*(?:,\s*[\"']?(?:" + alternation + r")[\"']?\s*)+[\])]"
    )
    min_distinct = 3
    for name, path in scan.items():
        if not path.exists():
            report.uncovered.append(f"no-parallel-list:{name}")
            report.add("no-parallel-list", True, f"{name}: 未覆盖（不在本检出）")
            continue
        hits = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "caas-forms" in line or "caas_forms" in line:
                continue
            for m in literal.finditer(line):
                names = {w for w in wanted if re.search(rf"[\"']?{w}[\"']?", m.group(0))}
                if len(names) >= min_distinct:
                    hits.append(f"L{lineno}: {line.strip()[:90]}")
        report.add(
            "no-parallel-list",
            not hits,
            f"{name}: {'无硬编码形态清单' if not hits else '发现硬编码清单 → ' + ' | '.join(hits)}",
        )


def check_ack_projection(report: Report, impls: dict[str, Path] | None = None) -> None:
    """③ 回执可投影三值：实现必须能产出 delivered/consumed/failed 之一。

    三值来自权威表（``caas-forms.json`` 的 ``ack.order``），实现里**故意**不写死
    这些字面量——写死就是判据②要抓的第二份口径。所以这里查的是两件事之一：

      (a) 实现导出的投影函数返回 ``canonical_ack`` 的结果（值来自权威表），或
      (b) 实现里出现三值字面量（合法：某些面直接映射既有状态字符串）

    两者都不满足才算不合规。判据问的是「能不能给出三值」，不是「有没有抄三遍」。
    """
    impls = impls or {
        "paas/lib/caas-envelope.js": WORKSPACE / "paas" / "lib" / "caas-envelope.js",
        "fd-industry-data/alerts_core/caas.py": REPO / "fd_industry_data" / "alerts_core" / "caas.py",
        "vaas/mcp_server/caas.py": VAAS_ROOT / "mcp-server" / "mcp_server" / "caas.py",
    }
    values = ("delivered", "consumed", "failed")
    for name, path in impls.items():
        if not path.exists():
            # 与 check_authority 同一口径：独立 clone 时兄弟仓的实现不在本检出，
            # 这是「未覆盖」而非「不合规」。判红只用于「文件在、内容不对」。
            report.uncovered.append(f"ack-three-values:{name}")
            report.add("ack-three-values", True, f"{name}: 未覆盖（不在本检出）")
            continue
        src = path.read_text(encoding="utf-8")
        literal = [v for v in values if re.search(rf"[\"']{v}[\"']", src)]
        # (a) 经权威表的投影：有 ack 投影函数且从 canonical/投影路径取值
        via_authority = bool(
            re.search(r"ack[_A-Za-z]*[Pp]rojection|ackProjection", src)
            and re.search(r"canonical_?[Aa]ck|ACK_ORDER|ack\.order|ack_order", src)
        )
        ok = len(literal) == 3 or via_authority
        detail = (
            f"{name}: 三值齐备（字面量）" if len(literal) == 3
            else f"{name}: 经权威表投影" if via_authority
            else f"{name}: 缺三值产出（字面量 {literal or '无'}，投影路径也找不到）"
        )
        report.add("ack-three-values", ok, detail)


def check_idempotency(report: Report, impls: dict[str, Path] | None = None) -> None:
    """④ 投递有幂等键：实现里必须能找到幂等键的产出点。"""
    impls = impls or {
        "paas/lib/caas-envelope.js": (WORKSPACE / "paas" / "lib" / "caas-envelope.js", "idemKeyFor"),
        "fd-industry-data/alerts_core/ledger.py": (REPO / "fd_industry_data" / "alerts_core" / "ledger.py", "event_key"),
        "fd-wanxing/services/wanxing-facade/a2a.js": (WORKSPACE / "fd-wanxing" / "services" / "wanxing-facade" / "a2a.js", "idempotency"),
        "vaas/db/models.py": (VAAS_ROOT / "mcp-server" / "mcp_server" / "db" / "models.py", "idem_key"),
    }
    for name, spec in impls.items():
        if isinstance(spec, tuple):
            path, needle = spec
        else:
            path, needle = spec, "idem"
        if not path.exists():
            report.uncovered.append(f"idempotency:{name}")
            report.add("idempotency", True, f"{name}: 未覆盖（不在本检出）")
            continue
        src = path.read_text(encoding="utf-8")
        report.add("idempotency", needle in src, f"{name}: {'有幂等键 ' + needle if needle in src else '缺幂等键'}")

def run(report: Report | None = None) -> Report:
    report = Report() if report is None else report
    check_authority(report)
    check_no_parallel_form_list(report)
    check_ack_projection(report)
    check_idempotency(report)
    return report


# ── 闸自检：负向样本 ─────────────────────────────────────────────────────────
#
# 一个只能判绿的闸等于没有闸。这里用**先在内存里构造的坏样本**喂给每条判据，
# 断言它们真的会红。样本落在临时目录，不污染仓库。

def self_test() -> int:
    import tempfile

    failures: list[str] = []

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        authority = root / "caas-forms.json"
        authority.write_text(json.dumps({"version": 1, "forms": {k: {} for k in _FORM_WORDS}}), encoding="utf-8")

        # 负向 1：副本漂移
        drifted = root / "drifted.json"
        drifted.write_text(json.dumps({"version": 1, "forms": {"text": {}}}), encoding="utf-8")
        r = Report()
        check_authority(r, authority=authority, copies={"drifted": drifted})
        if r.ok:
            failures.append("copy drift was not detected")

        # 负向 2：硬编码形态清单
        bad_impl = root / "bad_impl.js"
        bad_impl.write_text('const FORMS = ["text", "chart", "file", "asset", "link"];\n', encoding="utf-8")
        r = Report()
        check_no_parallel_form_list(r, scan={"bad_impl.js": bad_impl})
        if r.ok:
            failures.append("hardcoded form list was not detected")

        # 负向 3：回执缺三值
        no_ack = root / "no_ack.py"
        no_ack.write_text("state = 'sent'\n", encoding="utf-8")
        r = Report()
        check_ack_projection(r, impls={"no_ack.py": no_ack})
        if r.ok:
            failures.append("missing ack projection was not detected")

        # 负向 4：缺幂等键
        no_idem = root / "no_idem.py"
        no_idem.write_text("def deliver(): pass\n", encoding="utf-8")
        r = Report()
        check_idempotency(r, impls={"no_idem.py": (no_idem, "idemKeyFor")})
        if r.ok:
            failures.append("missing idempotency was not detected")

        # 正向对照：合规实现必须判绿。这里的「合规」= 三值经权威表投影 +
        # 有幂等键，且形态表从权威文件读（不是硬编码）——正是真实实现的形状。
        good_impl = root / "good.js"
        good_impl.write_text(
            'import { FORMS, ACK_ORDER, ackProjection } from "./caas-envelope.js";\n'
            'export function ackProjection(x) { return ACK_ORDER; }\n'
            'export function idemKeyFor() {}\n',
            encoding="utf-8",
        )
        r = Report()
        check_no_parallel_form_list(r, scan={"good.js": good_impl})
        if not r.ok:
            failures.append("compliant implementation was wrongly flagged for parallel list")
        r = Report()
        check_ack_projection(r, impls={"good.js": good_impl})
        check_idempotency(r, impls={"good.js": (good_impl, "idemKeyFor")})
        if not r.ok:
            failures.append("compliant implementation was wrongly flagged for ack/idem")

    if failures:
        print("SELF-TEST FAILED — 闸没抓住这些：")
        for f in failures:
            print("  -", f)
        return 1
    print("SELF-TEST OK — 四条判据均能判红，合规样本判绿")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="机读输出")
    ap.add_argument("--fixtures", action="store_true", help="只跑闸自检（负向样本）")
    ap.add_argument(
        "--require-full",
        action="store_true",
        help="有未覆盖面时也判红（跨仓工作区里跑；单仓 CI 不要加）",
    )
    args = ap.parse_args(argv)

    if args.fixtures:
        return self_test()

    report = run()
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        for f in report.findings:
            print(("  OK  " if f.ok else "  RED ") + f"{f.check}: {f.detail}")
        print()
        if not report.ok:
            print(f"FAIL — {len(report.as_dict()['failed'])} 项不通过")
        elif report.partial:
            # Say exactly what was not looked at. "PASS" alone here would be the
            # false-green this whole project exists to avoid.
            print(f"PASS（部分覆盖）— 未覆盖 {len(set(report.uncovered))} 面："
                  + ", ".join(sorted(set(report.uncovered))[:6])
                  + (" …" if len(set(report.uncovered)) > 6 else ""))
        else:
            print("PASS — 全部面已覆盖")

    if not report.ok:
        return 1
    if args.require_full and report.partial:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
